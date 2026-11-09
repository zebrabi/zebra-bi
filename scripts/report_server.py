#!/usr/bin/env python3
"""report_server.py: the Zebra BI report builder as a local MCP server over stdio.

  python3 -I report_server.py

It serves one MCP App, the report builder (assets/report-builder.html), where the user lays out Zebra BI charts
and text in rows. The model adds charts and text with tools; the builder moves, resizes and edits them and saves
through app-only tools. Reports are files on this machine (report.py); charts are drawn by render_visual on the
Zebra BI MCP with the sign-in that story.py keeps in auth.json, and nothing about a report is stored on a Zebra BI
server.

Sign-in never blocks the draft. Without it a chart is saved and drawn later; the builder shows a ghost in its place.
sign_in (the model's, for "sign me in") and start_sign_in (the builder's button) open the Zebra BI sign-in page in the
browser and return at once: story.py's SignInFlow takes the redirect on 127.0.0.1 in a daemon thread, and
sign-in.json tells every server process how it goes. get_report and library carry an `auth` block the builder polls.
When Zebra BI refuses a chart for the user's licence (its sentences are recognized), the refusal is recorded in
refusal.json beside auth.json for that block; a chart refused for its own arguments shows its error as before.

The model sees report titles, chart titles and the start of each text. Chart data reaches it only through
read_chart, which returns the aggregated rows it sent itself; the builder's results with drawn charts are app-only
(_meta.ui.visibility ["app"]). Those tools also need the builder's key, which the server puts in the builder page
when the host reads it. The key keeps out a model that can read neither that page nor the key file; a host that lets
the model read MCP resources, or a model with a shell (Codex), can get it, so a call ChatGPT and Codex mark as the
model's is refused with or without the key.

show_chart shows one chart in the chat, outside any report, as its own small app page (assets/chart-card.html); the
chart is kept under the reports folder's .chat-charts and the card reads it by id through chart_card (app only), so
the model gets no chart data back.

In ChatGPT and Codex desktop the builder opens from the sidebar (entrypoint "global") or beside a chat
("thread"). The renderer is nested in the builder through iframe srcdoc: the desktop hosts block blob: frames.
Standard library only. stdout carries JSON-RPC alone; anything else goes to stderr.
"""
import contextlib, hashlib, hmac, io, json, os, re, secrets, sys, time, traceback, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = sys.stdout
sys.stdout = sys.stderr  # story.py prints progress; it must never reach the JSON-RPC stream

import report as R  # noqa: E402
import story as S  # noqa: E402

APP = os.path.join(HERE, "..", "assets", "report-builder.html")
KEY_SLOT = "__ZEBRA_BI_APP_KEY__"  # where the page takes its key
KEY = re.compile(r"[0-9a-f]{64}")


def app_key():
    """The builder's key: random, kept beside the sign-in (auth.json) readable by this user only, and the same for
    every server process, since a host may read the page from one and send its calls to another."""
    if "key" not in state:
        path = os.path.join(os.path.dirname(S.CACHE), "report-builder.key")
        key = read_key(path)
        if not KEY.fullmatch(key or ""):
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            tmp = "%s.%s.tmp" % (path, secrets.token_hex(4))
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(secrets.token_hex(32))
                if key is None:
                    with contextlib.suppress(FileExistsError):
                        os.link(tmp, path)  # only if no other process made one meanwhile
                else:
                    os.replace(tmp, path)  # a broken one
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            key = read_key(path)  # whichever process made it, every one reads the same key
            if not KEY.fullmatch(key or ""):
                raise OSError("the report builder's key file %s is broken" % path)
        state["key"] = key
    return state["key"]


def read_key(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("ascii", "replace")
    except FileNotFoundError:
        return None


CARD = os.path.join(HERE, "..", "assets", "chart-card.html")
PAGES = {"builder": (APP, "report-builder", "The Zebra BI report builder: charts and text in rows."),
         "card": (CARD, "chart-card", "One Zebra BI chart in the chat.")}


def app_page(name="builder"):
    """An app page with its key in it, and its address: the builder, or the card that shows one chart in the chat.
    The address carries a hash of that page: hosts cache an app page by its URI, so a changed page, or a new key,
    must get a new one."""
    slot = "page:" + name
    if slot not in state:
        path, base, _ = PAGES[name]
        try:
            with open(path, encoding="utf-8") as f:
                page = f.read().replace(KEY_SLOT, app_key())
        except OSError as e:  # the chat's tools still work; this page cannot
            log("the %s page is unavailable:" % name, e)
            state[slot] = (None, "ui://zebra-bi/%s.html" % base)
        else:
            state[slot] = (page, "ui://zebra-bi/%s-%s.html" % (base, hashlib.sha256(page.encode("utf-8")).hexdigest()[:12]))
    return state[slot]


def app_uri(name="builder"):
    return app_page(name)[1]


MIME = "text/html;profile=mcp-app"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
FONTS = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"]  # Open Sans, the Zebra BI typeface
CSP = {"connectDomains": [], "resourceDomains": FONTS, "frameDomains": []}
SHIPPED_SCHEMA = os.path.join(HERE, "..", "assets", "render-visual-schema.json")  # render_visual's, until sign-in
GENERIC_CHART = {"type": "object", "description": "The render_visual arguments: title, subtitle, chart_type, "
                 "category_columns, time_axis, measure {name, Val, PY, PM, PL, FC}, columns and rows."}

state = {}
open_browser = webbrowser.open  # the user's browser; tests put a stand-in here


def log(*parts):
    print("report_server:", *parts, file=sys.stderr, flush=True)


def schema_cache():
    return os.path.join(os.path.dirname(S.CACHE), "render_visual.schema.json")


def chart_schema():
    """render_visual's own input schema: the live one from the Zebra BI MCP once signed in, cached; before that the
    copy this plugin ships; a generic one when neither can be read. One live attempt per session, so an offline start
    does not slow every tools/list."""
    if state.get("schema_from") in ("cache", "live"):
        return state["schema"]
    try:
        with open(schema_cache(), encoding="utf-8") as f:
            state.update(schema=json.load(f), schema_from="cache")
            return state["schema"]
    except (OSError, ValueError):
        pass
    if not state.get("schema_tried") and auth()["signed_in"]:
        state["schema_tried"] = True
        try:
            res = S.rpc("tools/list", {}, timeout=15)
            for tool in res.get("tools") or []:
                if isinstance(tool, dict) and tool.get("name") == "render_visual" and isinstance(tool.get("inputSchema"), dict):
                    S.write_atomic(schema_cache(), json.dumps(tool["inputSchema"]))
                    state.update(schema=tool["inputSchema"], schema_from="live")
                    return state["schema"]
        except (S.SignIn, S.Unavailable, SystemExit, Exception) as e:  # schema is a nicety; never block tools/list
            log("render_visual schema unavailable:", e)
    if "schema" not in state:
        try:
            with open(SHIPPED_SCHEMA, encoding="utf-8") as f:
                state.update(schema=json.load(f), schema_from="shipped")
        except (OSError, ValueError) as e:
            log("the shipped render_visual schema is unavailable:", e)
            state.update(schema=GENERIC_CHART, schema_from="generic")
    return state["schema"]


def ui(entrypoints=False, app_only=False, page="builder"):
    if app_only:
        return {"ui": {"visibility": ["app"]}}
    uri = app_uri(page)
    meta = {"ui": {"resourceUri": uri}, "ui/resourceUri": uri, "openai/outputTemplate": uri}
    if entrypoints:
        meta["openai/ui"] = {"entrypoints": [{"type": "global"}, {"type": "thread"}]}
    return meta


def opt_report():
    return {"type": "string", "description": "Report name (slug) or title; omit for the report open in this chat."}


def tools():
    ui_open, app = ui(entrypoints=True), ui(app_only=True)
    key = {"type": "string", "description": "The builder's key; the builder sends it."}
    obj = lambda props, req=(): dict({"type": "object", "properties": props}, **({"required": list(req)} if req else {}))
    write = {"readOnlyHint": False}
    chart = chart_schema()
    return [
        {"name": "open_report", "title": "Zebra BI report",
         "description": "Open the Zebra BI report builder. With `report` (slug or title) it opens that report; without "
                        "it, the report open in this chat, else the one opened last. Prints the report's cells with "
                        "their ids ([c1] ...) so you can name them. To start new work use create_report.",
         "inputSchema": obj({"report": opt_report()}), "annotations": {"readOnlyHint": False, "idempotentHint": True},
         "_meta": ui_open},
        {"name": "create_report", "title": "New Zebra BI report",
         "description": "Create an empty report with this title and open it in the builder. A title that is already "
                        "taken still makes a new report.",
         "inputSchema": obj({"title": {"type": "string"}}, ["title"]), "annotations": write, "_meta": ui(entrypoints=False)},
        {"name": "list_reports", "title": "List Zebra BI reports",
         "description": "The reports on this computer, last edited first, with their charts and rows. deleted=true "
                        "lists Recently deleted instead (kept for %d days)." % R.KEEP_DAYS,
         "inputSchema": obj({"deleted": {"type": "boolean"}}), "annotations": {"readOnlyHint": True}},
        {"name": "rename_report", "title": "Rename a Zebra BI report",
         "description": "Give a report a new title. Its folder keeps its name.",
         "inputSchema": obj({"title": {"type": "string"}, "report": opt_report()}, ["title"]), "annotations": write},
        {"name": "duplicate_report", "title": "Duplicate a Zebra BI report",
         "description": "Copy a report with its text and charts as a new report and open the copy, for example to "
                        "make next month's report from this month's. Then replace_chart updates its charts.",
         "inputSchema": obj({"title": {"type": "string"}, "report": opt_report()}), "annotations": write},
        {"name": "delete_report", "title": "Delete a Zebra BI report",
         "description": "Move a report to Recently deleted, where it stays restorable for %d days. Only when the user "
                        "asks to delete it." % R.KEEP_DAYS,
         "inputSchema": obj({"report": {"type": "string", "description": "Report name (slug) or title."}}, ["report"]),
         "annotations": {"readOnlyHint": False, "destructiveHint": False}},
        {"name": "restore_report", "title": "Restore a deleted Zebra BI report",
         "description": "Bring a report back from Recently deleted and open it.",
         "inputSchema": obj({"report": {"type": "string", "description": "Name or title from list_reports deleted=true."}}, ["report"]),
         "annotations": write},
        {"name": "show_chart", "title": "Zebra BI chart",
         "description": "Show one Zebra BI chart in the chat, drawn with render_visual: the default way to show business "
                        "figures (actuals against plan, previous year or forecast; a ranking; a trend). Send aggregated "
                        "rows only, one per category, the scenarios as columns AC, PL, PY, FC and time_axis for periods. "
                        "Tell the story in your reply, its numbers read from the rows you sent. One call per chart. "
                        "What leaves this computer: the chart's title, labels and aggregated values, sent to Zebra BI "
                        "(the chart service of this plugin, where the user signs in) to be drawn; never the source file. "
                        "Before the user signs in to Zebra BI the chart shows as a placeholder and is drawn after sign-in. "
                        "For a report the user arranges and saves as PDF or PowerPoint, use create_report and add_chart "
                        "with the same arguments.",
         "inputSchema": obj({"chart": chart}, ["chart"]),
         "annotations": {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": True},
         "_meta": ui(page="card")},
        {"name": "add_chart", "title": "Add a Zebra BI chart to the report",
         "description": "Draw a Zebra BI chart with render_visual and add it to the report: `message` goes above the "
                        "chart (its supporting message, numbers in **bold**), `note` under it (two or three sentences of "
                        "evidence read from the chart), `why` says in one sentence why this chart type, `recipe` keeps how "
                        "the rows were made (source, query or sheet, the code you ran, the period). Every number in the "
                        "text must come from the chart's rows (IBCS: AC, PL, PY, ΔPL%; no cause the data does not show). "
                        "Send aggregated rows only, one per category; the chart's title, labels and aggregated values go "
                        "to Zebra BI (the chart service of this plugin, where the user signs in) to be drawn, never the "
                        "source file. It joins `row` when that row has room (at most 3 "
                        "cells), else starts a new row. Returns Zebra BI's notes on the chart and any number in your text "
                        "the chart does not hold. Before the user signs in to Zebra BI the chart is saved and drawn "
                        "after sign-in: keep building the draft.",
         "inputSchema": obj({"chart": chart, "message": {"type": "string", "maxLength": R.MAX_MESSAGE},
                             "note": {"type": "string", "maxLength": R.MAX_TEXT},
                             "why": {"type": "string", "maxLength": R.MAX_MESSAGE},
                             "recipe": obj({k: {"type": "string"} for k in R.RECIPE_KEYS}),
                             "row": {"type": "integer", "minimum": 1}, "report": opt_report()}, ["chart", "message", "note"]),
         "annotations": write},
        {"name": "set_key_message", "title": "Set the report's key message",
         "description": "The key message on top of the report: what happened, how big, against what, numbers in **bold**, "
                        "each from one of the report's charts. Empty text removes it.",
         "inputSchema": obj({"text": {"type": "string", "maxLength": R.MAX_KEY}, "report": opt_report()}, ["text"]),
         "annotations": write},
        {"name": "set_kpis", "title": "Set the KPI tiles",
         "description": "Up to four KPI tiles under the key message, each a short value (\"1.0M\") and a one-line label "
                        "with its variance (\"Revenue, ΔPL −39.2k (−3.7%)\"); every number from the report's charts. "
                        "An empty list removes them.",
         "inputSchema": obj({"kpis": {"type": "array", "maxItems": R.MAX_KPIS,
                                      "items": obj({"value": {"type": "string", "maxLength": 40},
                                                    "label": {"type": "string", "maxLength": 120}}, ["value", "label"])},
                             "report": opt_report()}, ["kpis"]), "annotations": write},
        {"name": "arrange_report", "title": "Lay out the report",
         "description": "Rearrange the same cells as a named layout. one-pager: largest variance first, up to three "
                        "charts per row, notes hidden, printed on one landscape page, for a reader with a minute. "
                        "variance-walk: one chart per row in the order you added them, each with its note, for walking "
                        "someone through the month. board-pack: one chart per row, largest variance first, notes shown; "
                        "the PDF puts the key message and KPI tiles on a cover and each chart on its own page. Text stays "
                        "after its chart.",
         "inputSchema": obj({"layout": {"type": "string", "enum": list(R.LAYOUTS)}, "report": opt_report()}, ["layout"]),
         "annotations": write},
        {"name": "add_text", "title": "Add text to the report",
         "description": "Add a narrative paragraph (**bold** allowed). It joins `row` when that row has room, else "
                        "starts a new row.",
         "inputSchema": obj({"text": {"type": "string", "maxLength": R.MAX_TEXT}, "row": {"type": "integer", "minimum": 1},
                             "report": opt_report()}, ["text"]), "annotations": write},
        {"name": "update_text", "title": "Change a text cell or a chart's text",
         "description": "Replace the text of a text cell, or of a chart cell's `field`: message (above the chart), note "
                        "(the evidence under it, the default) or why. By the cell's id from open_report.",
         "inputSchema": obj({"cell": {"type": "string"}, "text": {"type": "string", "maxLength": R.MAX_TEXT},
                             "field": {"type": "string", "enum": ["message", "note", "why"]},
                             "report": opt_report()}, ["cell", "text"]), "annotations": write},
        {"name": "remove_cell", "title": "Remove a cell",
         "description": "Remove a chart or text cell from the report, by its id from open_report.",
         "inputSchema": obj({"cell": {"type": "string"}, "report": opt_report()}, ["cell"]), "annotations": write},
        {"name": "move_cell", "title": "Move a cell",
         "description": "Move a cell to `row` (1-based, as open_report numbers them; one past the last row makes a new "
                        "row) at `position` within the row (1-based, default last). A row holds at most 3 cells.",
         "inputSchema": obj({"cell": {"type": "string"}, "row": {"type": "integer", "minimum": 1},
                             "position": {"type": "integer", "minimum": 1}, "report": opt_report()}, ["cell", "row"]),
         "annotations": write},
        {"name": "replace_chart", "title": "Replace a chart",
         "description": "Redraw a chart cell with new render_visual arguments (new data, period or chart type). The "
                        "cell keeps its place. Before the user signs in to Zebra BI it is saved and drawn after sign-in.",
         "inputSchema": obj({"cell": {"type": "string"}, "chart": chart, "report": opt_report()}, ["cell", "chart"]),
         "annotations": write},
        {"name": "read_chart", "title": "Read a chart's numbers",
         "description": "The aggregated rows and arguments of one chart cell, by its id from open_report: what you need "
                        "to write or check the note under it. Read only.",
         "inputSchema": obj({"cell": {"type": "string"}, "report": opt_report()}, ["cell"]), "annotations": {"readOnlyHint": True}},
        {"name": "show_notes", "title": "Show or hide the notes under the charts",
         "description": "Turn the notes under the charts on or off for the whole report, in the builder and in its PDF "
                        "and page. Hidden notes are kept and come back when turned on. Text cells always show.",
         "inputSchema": obj({"show": {"type": "boolean"}, "report": opt_report()}, ["show"]), "annotations": write},
        {"name": "save_pdf", "title": "Save the report as a PDF",
         "description": "Print the report to an A4 PDF in its folder on this computer, its charts and texts side by side "
                        "as in the builder. Needs Chrome, Edge or Chromium. Returns the file's path.",
         "inputSchema": obj({"report": opt_report()}), "annotations": {"readOnlyHint": False, "destructiveHint": False}},
        {"name": "save_pptx", "title": "Save the report as PowerPoint",
         "description": "Save the report as a PowerPoint deck in its folder on this computer: a cover with the key message "
                        "and KPI tiles, then a slide per chart in the report's order, its message as the slide title and its "
                        "note beside it. Each chart is a live Zebra BI visual (editable with the Zebra BI add-in) with a "
                        "picture for everyone else. Needs Chrome, Edge or Chromium. Returns the file's path.",
         "inputSchema": obj({"report": opt_report()}), "annotations": {"readOnlyHint": False, "destructiveHint": False}},
        {"name": "sign_in", "title": "Sign in to Zebra BI",
         "description": "Open the Zebra BI sign-in page in the user's browser, so the report's charts can be drawn. Use "
                        "when the user asks to sign in to Zebra BI (\"sign me in\"), or after a draft whose charts wait "
                        "for sign-in. Returns at once with the page's address; the builder draws the charts as soon as "
                        "the user has signed in there.",
         "inputSchema": obj({}), "annotations": {"readOnlyHint": False, "openWorldHint": True}},
        {"name": "library", "title": "Reports for the builder", "description": "App only: reports and Recently deleted.",
         "inputSchema": obj({"app_key": key}), "annotations": {"readOnlyHint": True}, "_meta": app},
        {"name": "get_report", "title": "Report for the builder", "description": "App only: one report with chart results.",
         "inputSchema": obj({"report": {"type": "string"}, "follow": {"type": "string", "enum": ["last", "thread", "none"]},
                             "since": {"type": "integer"}, "draw": {"type": "boolean"}, "app_key": key}),
         "annotations": {"readOnlyHint": True}, "_meta": app},
        {"name": "draw_chart", "title": "Draw one chart", "description": "App only: draw one chart cell of a report.",
         "inputSchema": obj({"report": {"type": "string"}, "cell": {"type": "string"}, "app_key": key}, ["report", "cell"]),
         "annotations": {"readOnlyHint": False}, "_meta": app},
        {"name": "chart_card", "title": "Chart for the chat", "description": "App only: one chart shown in the chat.",
         "inputSchema": obj({"chart": {"type": "string"}, "app_key": key}, ["chart"]), "annotations": {"readOnlyHint": False},
         "_meta": app},
        {"name": "start_sign_in", "title": "Sign in to Zebra BI", "description": "App only: open the Zebra BI sign-in page.",
         "inputSchema": obj({"app_key": key}), "annotations": {"readOnlyHint": False, "openWorldHint": True}, "_meta": app},
        {"name": "sign_out", "title": "Sign out of Zebra BI", "description": "App only: revoke and forget the Zebra BI sign-in.",
         "inputSchema": obj({"app_key": key}), "annotations": {"readOnlyHint": False, "destructiveHint": True}, "_meta": app},
        {"name": "get_renderer", "title": "Zebra BI renderer", "description": "App only: the Zebra BI chart renderer page.",
         "inputSchema": obj({"app_key": key}), "annotations": {"readOnlyHint": True}, "_meta": app},
        {"name": "save_layout", "title": "Save the report layout", "description": "App only: save the builder's rows and title.",
         "inputSchema": obj({"report": {"type": "string"}, "rev": {"type": "integer"}, "title": {"type": "string"},
                             "rows": {"type": "array"}, "show_notes": {"type": "boolean"}, "app_key": key}, ["report", "rev", "rows"]),
         "annotations": write, "_meta": app},
        {"name": "purge_report", "title": "Delete a report forever", "description": "App only: remove a Recently deleted report for good.",
         "inputSchema": obj({"entry": {"type": "string"}, "app_key": key}, ["entry"]), "annotations": {"readOnlyHint": False, "destructiveHint": True},
         "_meta": app},
    ]


def text(t, structured=None, error=False):
    out = {"content": [{"type": "text", "text": t}]}
    if structured is not None:
        out["structuredContent"] = structured
    if error:
        out["isError"] = True
    return out


def home_relative(path):
    """path with the home folder as ~, so text the model reads does not carry the user's name."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def exit_reason(e, said):
    """Why a build stopped: its exit message, else the last line it printed on stderr."""
    if isinstance(e.code, str):
        return S.clean(e.code, 600)
    lines = [l for l in said.splitlines() if l.strip()]
    return S.clean(lines[-1], 600) if lines else "the build failed"


def deck_slides(path):
    """How many slides the deck at path holds."""
    import zipfile
    with zipfile.ZipFile(path) as z:
        return sum(1 for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))


def pdf_saved(out, pages, layout):
    """What save_pdf tells the model: the file, its pages, and a one-pager that did not fit one page."""
    line = "Saved %s (%d page%s)." % (out, pages, "" if pages == 1 else "s")
    if layout == "one-pager" and pages > 1:
        line += (" The one-pager did not fit one page at a readable size; fewer charts, or the variance walk or "
                 "board pack layout, would suit it better.")
    return line + " It holds the report's aggregated numbers, so share it only where the data may go."


def app_only_tools():
    """The tools only the builder app may call (visibility ["app"]): the host keeps them from the model."""
    return {t["name"] for t in tools() if (t.get("_meta") or {}).get("ui", {}).get("visibility") == ["app"]}


def model_turn(meta):
    """Whether a call comes from the model rather than the app. ChatGPT and Codex mark a model's call with
    x-codex-turn-metadata and never mark the app's; the server refuses an app-only tool such a call names, even with
    the builder's key."""
    return isinstance(meta, dict) and "x-codex-turn-metadata" in meta


def from_builder(args):
    """Whether an app-only call carries the builder's key. Hosts keep those tools from the model, but not every host
    marks the model's calls, so the server asks for the key in the builder page as well (deleting for good, saving
    the builder's layout and approving its flagged numbers). It holds only where the model cannot read the page
    (an MCP resource) or the key file."""
    key = args.get("app_key") if isinstance(args, dict) else None
    if not isinstance(key, str) or app_page()[0] is None:  # no page, no key: nothing can come from the builder
        return False
    return hmac.compare_digest(key.encode("utf-8"), app_key().encode("utf-8"))


def thread_of(meta):
    """The host's chat thread id from a call's _meta (ChatGPT and Codex send threadId / thread_id), else None."""
    meta = meta if isinstance(meta, dict) else {}
    t = meta.get("threadId") or meta.get("thread_id")
    if not t and isinstance(meta.get("x-codex-turn-metadata"), dict):
        t = meta["x-codex-turn-metadata"].get("thread_id")
    return str(t)[:120] if t else None


def target(args, thread):
    """The report a model tool acts on: `report` when given, else this chat's report. Raises when there is none."""
    if args.get("report"):
        return R.find(args["report"])
    slug = R.resolve(thread)
    if not slug:
        raise ValueError("there is no report yet; create_report starts one")
    return slug


def opened(slug, thread, lead="", pin=True):
    """Result of a tool that opens a report in the builder: its outline for the model, its slug for the builder.
    pin=False only shows it: the chat keeps no report and the report opened last stays as it was."""
    if pin:
        R.remember(slug, thread)
    rep = R.load(R.path_for(slug))
    return text((lead + "\n" if lead else "") + R.summary(rep),
                {"report": slug, "title": rep["title"], "rev": rep["rev"], "follow": "thread" if thread else "last"})


def unchecked(rep, where):
    """", Unchecked numbers: ..." for a cell id, "key_message" or "kpis" when its text holds numbers no chart has."""
    found = R.checks(rep)
    raws = (found.get("key_message", []) if where == "key_message"
            else [r for t in found.get("kpis", []) for r in t] if where == "kpis"
            else [r for v in found.get("cells", {}).get(where, {}).values() for r in v])
    return ". Unchecked numbers: %s" % ", ".join(raws) if raws else ""


def edit(slug, change):
    """Load, change, save one report under its lock. change(rep) returns the message."""
    rep, msg = R.change(R.path_for(slug), change)
    return text("%s (report %r)." % (msg, rep["title"]), {"report": slug, "rev": rep["rev"]})


# ----- sign-in and Zebra BI's refusals -----

SIGN_IN_ASK = 'the "Sign in to Zebra BI" button in the builder, or the user says "sign me in" and you call sign_in'
EXPIRED = "Your Zebra BI sign-in has expired. Sign in again to draw the charts."
# Zebra BI's sentences when it refuses a chart for the user's licence (ZAI: app/server/mcp/licence.py, denial_message).
TRIAL_ENDED = re.compile(r"Zebra BI trial ended on (\d{1,2} [A-Z][a-z]{2} \d{4})")
NO_EMAIL = re.compile(r"did not share a verified email address")
NO_LICENCE = re.compile(r"no Zebra BI licen[cs]e \(.*?\) was found for the user's organi[sz]ation")
LICENCE_LIKE = re.compile(r"licen[cs]e|subscription|\btrial\b", re.I)
MAX_GHOST = {"columns": 24, "bars": 12}  # a ghost's columns or bars, however many rows the chart has


def recognize(text):
    """The refusal Zebra BI's sentence names, as the builder shows it once: {"kind", "message", "date"}. None when the
    chart was refused for its own reasons (its arguments), which the chart shows itself. An unrecognized licence
    sentence is "other" with Zebra BI's words, less any sentence about a trial."""
    text = S.clean(text, 600)
    m = TRIAL_ENDED.search(text)
    if m:
        return {"kind": "trial_ended", "date": m.group(1), "message": "Your Zebra BI trial ended on %s. Your report and its "
                "texts stay; the charts need a Zebra BI licence." % m.group(1)}
    if NO_EMAIL.search(text):
        return {"kind": "no_verified_email", "date": None, "message": text}
    if NO_LICENCE.search(text):
        return {"kind": "no_licence", "date": None, "message": "Zebra BI found no licence for your company."}
    if LICENCE_LIKE.search(text):
        kept = [x for x in re.split(r"(?<=[.!?])\s+", text) if not re.search(r"\btrial\b", x, re.I)]
        return {"kind": "other", "date": None, "message": " ".join(kept) or "Zebra BI did not draw the charts."}
    return None


def told(refusal):
    """A refusal in words for the model: what happened, never an offer (the builder shows the user the next step)."""
    kind = refusal["kind"]
    if kind == "trial_ended":
        return "the user's Zebra BI access ended on %s, and the charts need a Zebra BI licence" % refusal["date"]
    if kind == "no_licence":
        return "Zebra BI found no licence for the user's company"
    if kind == "no_verified_email":
        return ("Microsoft shared no verified email address for the user's account, so Zebra BI could not tell their "
                "company (support@zebrabi.com helps)")
    if kind == "expired":
        return "the user's Zebra BI sign-in has expired"
    return refusal["message"].rstrip(".")


def kept_entry():
    """The sign-in kept for this Zebra BI MCP, or None."""
    entry = S.load_auth().get(S.ENDPOINT)
    return entry if isinstance(entry, dict) and isinstance(entry.get("access_token"), str) and entry["access_token"] else None


def refusal_path():
    return os.path.join(os.path.dirname(S.AUTH), "refusal.json")


def token_id(entry):
    """Names one sign-in without its token: a refusal recorded under it is forgotten when the user signs in again."""
    return hashlib.sha256(("%s %s" % (entry["access_token"], entry.get("signed_in_at"))).encode("utf-8")).hexdigest()[:16]


def record(refusal):
    entry = kept_entry()
    if entry is None:
        return
    os.makedirs(os.path.dirname(S.AUTH), mode=0o700, exist_ok=True)
    S.write_atomic(refusal_path(), json.dumps(dict(refusal, endpoint=S.ENDPOINT, sign_in=token_id(entry), at=int(time.time()))),
                   mode=0o600)


def recorded(entry):
    """The refusal recorded under this sign-in, else None."""
    path = refusal_path()
    if entry is None or os.path.islink(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        return None
    if not (isinstance(r, dict) and r.get("endpoint") == S.ENDPOINT and r.get("sign_in") == token_id(entry)
            and r.get("kind") in ("trial_ended", "no_licence", "no_verified_email", "expired", "other")):
        return None
    return {"kind": r["kind"], "message": str(r.get("message") or ""), "date": r.get("date")}


def forget_refusal():
    with contextlib.suppress(FileNotFoundError):
        os.remove(refusal_path())


def auth():
    """The auth block the builder polls: whether the user is signed in to Zebra BI, the browser sign-in's state and
    page, and the refusal that keeps the charts from being drawn. A kept sign-in past its expiry, or one Zebra BI turned
    away, is the refusal "expired"; with no sign-in kept there is none."""
    entry, flow = kept_entry(), S.sign_in_state()
    refusal = recorded(entry)
    live = entry is not None and (not entry.get("expires_at") or time.time() < entry["expires_at"] - 60
                                  or bool(entry.get("refresh_token")))
    if entry is not None and not live:
        refusal = {"kind": "expired", "message": EXPIRED, "date": None}
    signed_in = live and not (refusal and refusal["kind"] == "expired")
    st = flow["state"] if flow["state"] != "idle" else "signed_in" if signed_in else "idle"
    return {"signed_in": signed_in, "state": st, "url": flow.get("url"), "refusal": refusal}


def start_sign_in():
    """Start the browser sign-in, or find the one waiting, and open its page: (url, whether the browser opened)."""
    url = S.begin_sign_in()
    try:
        shown = bool(open_browser(url))
    except Exception as e:  # no browser here: the host opens the page from the URL
        log("cannot open the browser:", e)
        shown = False
    return url, shown


class Wait:
    """A chart that waits: for sign-in (refusal None), or for what Zebra BI's refusal names."""

    def __init__(self, refusal=None):
        self.refusal = refusal


def draw(slug, vid, args, signed=None):
    """(result, None) for a chart drawn now or before; (None, Wait) for one that waits for sign-in or for what Zebra
    BI's refusal names, which is recorded for the auth block; (None, error result) when Zebra BI cannot draw it for its
    own reasons. Without sign-in Zebra BI is not asked. signed is auth() when the caller has it."""
    return draw_in(os.path.dirname(R.path_for(slug)), vid, args, signed)


def draw_in(folder, vid, args, signed=None):
    """draw() for the chart vid kept in folder: a report's, or a chart shown in the chat."""
    res = R.cached(folder, vid, args)
    if res is not None:
        return res, None
    signed = signed or auth()
    if not signed["signed_in"]:
        return None, Wait(signed["refusal"])
    try:
        res = R.render(folder, vid, args)
    except S.SignIn:  # a kept token Zebra BI no longer takes
        refusal = {"kind": "expired", "message": EXPIRED, "date": None}
        record(refusal)
        return None, Wait(refusal)
    except R.Refused as e:
        refusal = recognize(e.text)
        if refusal is None:
            return None, text("The chart was not drawn: %s" % S.clean(e, 600), error=True)
        record(refusal)
        return None, Wait(refusal)
    except (S.Unavailable, ValueError, SystemExit) as e:
        return None, text("The chart was not drawn: %s" % S.clean(e, 600), error=True)
    if signed["refusal"]:
        forget_refusal()  # drawn: whatever kept the charts back is over
    return res, None


def waits(lead, wait):
    """What the model is told about a chart saved but not drawn: why it waits and what to do next."""
    r = wait.refusal
    if r is None:
        return ("%s It is drawn when the user signs in to Zebra BI. Keep building the whole draft (every chart, the key "
                "message, the KPI tiles, the layout), then end your reply by inviting the user to sign in to Zebra BI: %s."
                % (lead, SIGN_IN_ASK))
    if r["kind"] == "expired":
        return ("%s The user's Zebra BI sign-in has expired, so it is drawn when they sign in again. Keep building the "
                "draft, then end your reply by inviting the user to sign in to Zebra BI again: %s." % (lead, SIGN_IN_ASK))
    return ("%s Zebra BI did not draw it: %s. It stays in the report and every text stays. Keep building the draft; the "
            "builder shows the user the next step, so end your reply by saying in one line that the charts are not drawn "
            "and why." % (lead, told(r)))


def shown_waits(lead, wait):
    """What the model is told about a chart shown in the chat but not drawn yet."""
    r = wait.refusal
    if r is None:
        return ("%s It shows as a placeholder and is drawn when the user signs in to Zebra BI. Answer as usual, then end "
                "your reply by inviting the user to sign in: the \"Sign in to Zebra BI\" button on the chart, or the user "
                "says \"sign me in\" and you call sign_in." % lead)
    if r["kind"] == "expired":
        return ("%s The user's Zebra BI sign-in has expired, so it is drawn when they sign in again. Answer as usual, then "
                "end your reply by inviting the user to sign in again with the button on the chart." % lead)
    return ("%s Zebra BI did not draw it: %s. The chart shows the user the next step; say in one line that the chart is "
            "not drawn and why." % (lead, told(r)))


def results_for(rep, slug, draw_now=True):
    """render_visual results for the report's charts, drawing the ones not cached yet (draw_now=False: cached only).
    Returns (results, errors by chart, the Wait of each chart that waits). After one chart waits, the rest wait
    without asking Zebra BI again."""
    out, errors, waiting, stop = {}, {}, {}, None
    folder, signed = os.path.dirname(R.path_for(slug)), auth()
    for vid, v in rep["visuals"].items():
        res = R.cached(folder, vid, v["args"])
        if res is not None:
            out[vid] = res
            continue
        if not draw_now:
            continue
        if stop is not None:
            waiting[vid] = stop
            continue
        res, err = draw(slug, vid, v["args"], signed)
        if res is not None:
            out[vid] = res
        elif isinstance(err, Wait):
            waiting[vid] = stop = err
        else:
            errors[vid] = err["content"][0]["text"]
    return out, errors, waiting


def ghost(args):
    """The shape that stands in for a chart not drawn yet: columns for a time axis, else bars, one per row."""
    rows = args.get("rows") if isinstance(args.get("rows"), list) else []
    kind = "columns" if args.get("time_axis") else "bars"
    return {"kind": kind, "count": max(1, min(len(rows), MAX_GHOST[kind]))}


def with_cells(rep, results):
    """A copy of the report for the builder, each chart cell saying whether it is drawn and its ghost's shape."""
    out = json.loads(json.dumps(rep))
    for row in out["rows"]:
        for c in row["cells"]:
            if c["type"] == "chart":
                c["drawn"] = c["visual"] in results
                c["ghost"] = ghost(rep["visuals"][c["visual"]]["args"])
    return out


def export_waits(slug, what):
    """The error result an export gives while a chart is not drawn (after drawing what can be drawn now), else None."""
    rep = R.load(R.path_for(slug))
    _, _, waiting = results_for(rep, slug)
    cells = [c["id"] for r in rep["rows"] for c in r["cells"] if c["type"] == "chart" and c["visual"] in waiting]
    if not cells:
        return None
    lead = "No %s was saved: %d chart%s not drawn yet" % (what, len(cells), " is" if len(cells) == 1 else "s are")
    r = next(iter(waiting.values())).refusal
    if r is None:
        why = "; the charts need Zebra BI sign-in first. Ask the user to sign in: %s." % SIGN_IN_ASK
    elif r["kind"] == "expired":
        why = "; the user's Zebra BI sign-in has expired, so the charts need sign-in again. Ask the user to sign in: %s." % SIGN_IN_ASK
    else:
        why = ": Zebra BI did not draw them (%s). The builder shows the user the next step." % told(r)
    return text(lead + why, {"report": slug, "waiting": cells}, error=True)


def call(name, args, thread):
    if name == "open_report":
        named = bool(args.get("report"))
        slug = R.find(args["report"]) if named else R.resolve(thread)
        if not slug:
            return text("There are no reports yet. create_report starts one; the builder shows its empty home.",
                        {"report": None, "follow": "thread" if thread else "last"})
        return opened(slug, thread, "The builder shows this report; the user can arrange it there.", pin=named)
    if name == "create_report":
        return opened(R.create(args.get("title")), thread, "Created a new, empty report.")
    if name == "list_reports":
        if args.get("deleted"):
            items = R.deleted()
            if not items:
                return text("Recently deleted is empty.")
            return text("\n".join("%s: %s (deleted %s)" % (d["report"], d["title"], d["deleted_at"]) for d in items))
        items = R.reports()
        if not items:
            return text("No reports yet in %s. create_report starts one." % R.root())
        cur = R.resolve(thread)
        return text("\n".join("%s%s: %s (%d charts, %d rows, saved %s)" % ("* " if r["report"] == cur else "", r["report"],
                                r["title"], r["charts"], r["rows"], r["saved_at"]) for r in items) +
                    "\n* is the report open in this chat.")
    if name == "rename_report":
        slug = target(args, thread)
        title = R.rename(slug, args.get("title"))
        return text("Renamed the report to %r (its folder stays %s)." % (title, slug), {"report": slug})
    if name == "duplicate_report":
        slug = R.duplicate(target(args, thread), args.get("title"))
        return opened(slug, thread, "Made a copy and opened it.")
    if name == "delete_report":
        slug = R.find(args.get("report"))
        title = R.load(R.path_for(slug))["title"]
        R.trash(slug)
        return text("Moved %r to Recently deleted; restore_report brings it back within %d days." % (title, R.KEEP_DAYS),
                    {"deleted": slug})
    if name == "restore_report":
        return opened(R.restore(R.deleted_entry(args.get("report"))), thread, "Restored from Recently deleted.")
    if name == "show_chart":
        chart = args.get("chart")
        cid = R.show_chart(chart)
        res, err = draw_in(R.chat_folder(cid), R.CHAT_VISUAL, chart)
        if err and not isinstance(err, Wait):
            return err
        title = chart.get("title") or "the chart"
        out = {"chart": cid, "title": chart.get("title"), "drawn": res is not None}
        if isinstance(err, Wait):
            return text(shown_waits("Showed %r in the chat." % title, err), out)
        return text("Showed %r in the chat. Zebra BI: %s" % (title, R.server_note(res)), out)
    if name == "add_chart":
        slug = target(args, thread)
        recipe = args.get("recipe") if isinstance(args.get("recipe"), dict) else None
        add = lambda rep: R.add_chart(rep, args.get("chart"), args.get("row"), args.get("note"), args.get("message"),
                                      args.get("why"), recipe)
        # Draw on a copy first: drawing takes seconds, and the report is only loaded and saved under its lock after,
        # so a save the builder makes meanwhile is kept.
        probe = R.load(R.path_for(slug))
        vid, _ = add(probe)
        res, err = draw(slug, vid, probe["visuals"][vid]["args"])
        if err and not isinstance(err, Wait):
            return err
        rep, (vid, row) = R.change(R.path_for(slug), add)
        cid = next(c["id"] for r in rep["rows"] for c in r["cells"] if c.get("visual") == vid)
        if isinstance(err, Wait):  # saved all the same: the draft never waits for sign-in
            return text(waits("Saved chart %r to row %d of %r%s." % (R.chart_title(rep, vid), row, rep["title"], unchecked(rep, cid)),
                              err), {"report": slug, "rev": rep["rev"], "drawn": False})
        if vid not in probe["visuals"]:  # the id moved on meanwhile; the builder draws it from the same arguments
            res = draw(slug, vid, rep["visuals"][vid]["args"])[0] or res
        return text("Added chart %r to row %d of %r. Zebra BI: %s%s" % (R.chart_title(rep, vid), row, rep["title"],
                    R.server_note(res), unchecked(rep, cid)), {"report": slug, "rev": rep["rev"], "drawn": True})
    if name == "set_key_message":
        return edit(target(args, thread), lambda rep: R.set_key_message(rep, args.get("text"))
                    or "Key message set%s" % unchecked(rep, "key_message"))
    if name == "set_kpis":
        return edit(target(args, thread), lambda rep: R.set_kpis(rep, args.get("kpis") if isinstance(args.get("kpis"), list) else None)
                    or "KPI tiles set%s" % unchecked(rep, "kpis"))
    if name == "arrange_report":
        return edit(target(args, thread), lambda rep: R.arrange(rep, args.get("layout"))
                    or "Laid out as %s; now:\n%s" % (args.get("layout"), R.summary(rep)))
    if name == "add_text":
        return edit(target(args, thread), lambda rep: "Added text to row %d" % R.add_text(rep, args.get("text"), args.get("row")))
    if name == "update_text":
        return edit(target(args, thread), lambda rep: R.update_text(rep, args.get("cell"), args.get("text"), args.get("field"))
                    or "Changed the %s of %s%s" % (args.get("field") or "text", args.get("cell"), unchecked(rep, args.get("cell"))))
    if name == "remove_cell":
        return edit(target(args, thread), lambda rep: R.remove_cell(rep, args.get("cell")) or "Removed %s" % args.get("cell"))
    if name == "move_cell":
        return edit(target(args, thread), lambda rep: R.move_cell(rep, args.get("cell"), args.get("row"), args.get("position"))
                    or "Moved %s; now:\n%s" % (args.get("cell"), R.summary(rep)))
    if name == "replace_chart":
        slug = target(args, thread)
        swap = lambda rep: R.replace_chart(rep, args.get("cell"), args.get("chart"))
        probe = R.load(R.path_for(slug))  # drawn first, saved under the lock after, as add_chart
        vid = swap(probe)
        res, err = draw(slug, vid, probe["visuals"][vid]["args"])
        if err and not isinstance(err, Wait):
            return err
        rep, vid = R.change(R.path_for(slug), swap)
        if isinstance(err, Wait):
            return text(waits("Replaced chart %s with %r in %r." % (args.get("cell"), R.chart_title(rep, vid), rep["title"]), err),
                        {"report": slug, "rev": rep["rev"], "drawn": False})
        return text("Replaced chart %s with %r in %r. Zebra BI: %s" % (args.get("cell"), R.chart_title(rep, vid), rep["title"],
                    R.server_note(res)), {"report": slug, "rev": rep["rev"], "drawn": True})
    if name == "show_notes":
        def change(rep):
            rep["show_notes"] = bool(args.get("show"))
            return "Chart notes %s" % ("shown" if rep["show_notes"] else "hidden")
        return edit(target(args, thread), change)
    if name == "read_chart":
        slug = target(args, thread)
        rep = R.load(R.path_for(slug))
        ri, ci = R.locate(rep, args.get("cell"))
        cell = rep["rows"][ri]["cells"][ci]
        if cell["type"] != "chart":
            raise ValueError("cell %s is text, not a chart" % args.get("cell"))
        chart = rep["visuals"][cell["visual"]]["args"]
        return text("Chart %s in %r (row %d), message: %s, note: %s, why: %s, recipe: %s\n%s" % (
                    cell["id"], rep["title"], ri + 1, cell.get("message") or "MISSING", cell.get("note") or "MISSING",
                    cell.get("why") or "MISSING", json.dumps(cell.get("recipe") or {}, ensure_ascii=False),
                    json.dumps(chart, ensure_ascii=False)),
                    {"report": slug, "cell": cell["id"], "chart": chart, "message": cell.get("message"),
                     "note": cell.get("note"), "why": cell.get("why"), "recipe": cell.get("recipe")})
    if name == "save_pdf":
        slug = target(args, thread)
        wait = export_waits(slug, "PDF")
        if wait:
            return wait
        found = R.flags(R.load(R.path_for(slug)))
        if found:
            return text("No PDF was saved: these numbers are not in their charts: %s. Fix them, or the user keeps them "
                        "in the builder." % "; ".join(found), {"report": slug, "flags": found}, error=True)
        import pdf as P  # starts nothing until a PDF is asked for
        try:
            out = P.build(os.path.dirname(R.path_for(slug)))
        except SystemExit as e:
            return text("No PDF was saved: %s" % S.clean(e, 600), error=True)
        with open(out, "rb") as f:
            pages = P.pages(f.read())
        shown = home_relative(out)
        layout = R.load(R.path_for(slug)).get("layout")
        return text(pdf_saved(shown, pages, layout), {"report": slug, "file": out, "shown": shown, "pages": pages})
    if name == "save_pptx":
        slug = target(args, thread)
        wait = export_waits(slug, "PowerPoint")
        if wait:
            return wait
        found = R.flags(R.load(R.path_for(slug)))
        if found:
            return text("No PowerPoint was saved: these numbers are not in their charts: %s. Fix them, or the user keeps "
                        "them in the builder." % "; ".join(found), {"report": slug, "flags": found}, error=True)
        import office as O  # starts nothing until a deck is asked for
        folder = os.path.dirname(R.path_for(slug))
        out = os.path.join(folder, os.path.basename(folder) + ".pptx")
        said = io.StringIO()
        try:
            with contextlib.redirect_stderr(said):  # office.py says why on stderr and exits with 1
                O.build(folder, ["pptx"])
        except SystemExit as e:
            if e.code:
                return text("No PowerPoint was saved: %s" % exit_reason(e, said.getvalue()), error=True)
        slides = deck_slides(out)
        shown = home_relative(out)
        return text("Saved %s (%d slides). It holds the report's aggregated numbers, so share it only where the data may go."
                    % (shown, slides), {"report": slug, "file": out, "shown": shown, "slides": slides})
    if name == "sign_in":
        signed = auth()
        if signed["signed_in"] and not signed["refusal"]:
            return text("The user is already signed in to Zebra BI; the builder and the charts in the chat draw themselves.",
                        {"url": None, "opened": False, "signed_in": True})
        try:
            url, shown = start_sign_in()
        except S.Failed as e:
            return text(S.clean(e, 600), error=True)
        if shown:
            return text("The Zebra BI sign-in page opened in the user's browser. The charts, in the chat and in the "
                        "builder, are drawn as soon as they have signed in there; tell the user so in one line.",
                        {"url": url, "opened": True})
        return text("The Zebra BI sign-in page could not be opened from here; open it for the user: %s. The charts, in the "
                    "chat and in the builder, are drawn as soon as they have signed in there." % url, {"url": url, "opened": False})
    # ----- app only -----
    if name == "library":
        return text("library", {"reports": R.reports(), "deleted": R.deleted(), "current": R.resolve(thread), "last": R.last(),
                                "auth": auth()})
    if name == "start_sign_in":
        try:
            url, shown = start_sign_in()
        except S.Failed as e:
            return text(S.clean(e, 600), {"auth": auth()}, error=True)
        return text("sign-in", {"url": url, "opened": shown, "auth": auth()})
    if name == "sign_out":
        n = S.sign_out(say=lambda line, warning=False: log(line))  # never print: stdout is the JSON-RPC stream
        forget_refusal()
        return text("signed out", {"signed_out": n, "auth": auth()})
    if name == "chart_card":
        cid = args.get("chart")
        chart = R.chat_args(cid)
        res, err = draw_in(R.chat_folder(cid), R.CHAT_VISUAL, chart)
        return text("drawn" if res is not None else "not drawn",
                    {"chart": cid, "title": chart.get("title"), "subtitle": chart.get("subtitle"), "ghost": ghost(chart),
                     "drawn": res is not None, "result": res,
                     "error": None if err is None or isinstance(err, Wait) else err["content"][0]["text"], "auth": auth()})
    if name == "draw_chart":
        slug = args.get("report")
        if not R.exists(slug):
            return text("gone", {"gone": True, "report": slug})
        rep = R.load(R.path_for(slug))
        ri, ci = R.locate(rep, args.get("cell"))
        cell = rep["rows"][ri]["cells"][ci]
        if cell["type"] != "chart":
            raise ValueError("cell %s is text, not a chart" % args.get("cell"))
        res, err = draw(slug, cell["visual"], rep["visuals"][cell["visual"]]["args"])
        return text("drawn" if res is not None else "not drawn",
                    {"cell": cell["id"], "visual": cell["visual"], "drawn": res is not None, "result": res,
                     "error": None if err is None or isinstance(err, Wait) else err["content"][0]["text"], "auth": auth()})
    if name == "get_report":
        slug, follow = args.get("report"), args.get("follow") or "none"
        if slug and not R.exists(slug):  # deleted: say so, rather than follow on to another report
            return text("gone", {"gone": True, "report": slug})
        followed = R.last() if follow == "last" else R.resolve(thread) if follow == "thread" else None
        if followed and followed != slug:
            return text("switched", {"switched": followed})
        if not slug or not R.exists(slug):
            return text("gone", {"gone": True, "report": slug})
        rep = R.load(R.path_for(slug))
        if args.get("since") is not None and args["since"] == rep["rev"]:  # the poll still learns a sign-in
            return text("unchanged", {"unchanged": True, "rev": rep["rev"], "auth": auth()})
        results, errors, _ = results_for(rep, slug, args.get("draw") is not False)
        return text("report %s rev %d" % (rep["title"], rep["rev"]), {"report": with_cells(rep, results), "slug": slug,
                                                                     "results": results, "errors": errors,
                                                                     "checks": R.checks(rep), "auth": auth()})
    if name == "get_renderer":
        try:
            rpath, meta = S.renderer(raises=True)
        except S.SignIn:
            return text("The Zebra BI renderer comes with sign-in to Zebra BI.", {"needs_sign_in": True, "auth": auth()}, error=True)
        except (S.Unavailable, SystemExit) as e:
            return text("The Zebra BI renderer is unavailable: %s" % S.clean(e, 300), error=True)
        with open(rpath, encoding="utf-8") as f:
            return text("renderer %s" % meta.get("sha256", "")[:12], {"renderer": f.read(), "sha256": meta.get("sha256")})
    if name == "save_layout":
        slug = args.get("report")
        if not R.exists(slug):
            return text("this report was deleted", {"gone": True}, error=True)
        path = R.path_for(slug)
        with R.locked(path):  # the rev check and the write as one step, so a change in between is never lost
            rep = R.load(path)
            try:
                new = R.apply_layout(rep, args.get("title"), args.get("rows"), args.get("rev"), args.get("show_notes"),
                                     args.get("key_message"), args.get("kpis"), args.get("kept"))
            except R.Conflict as e:
                return text(str(e), {"conflict": True, "rev": rep["rev"]}, error=True)
            rev = R.save(path, new)
        return text("saved", {"rev": rev, "checks": R.checks(new)})
    if name == "purge_report":
        R.purge(args.get("entry"))
        return text("deleted for good", {"purged": args.get("entry")})
    raise KeyError("unknown tool %r" % name)


def debug(method, params):
    """With $ZEBRA_BI_REPORT_LOG set, one JSON line per request: method, tool and _meta (never arguments); for
    initialize, also what the client declared (its capabilities and name)."""
    path = os.environ.get("ZEBRA_BI_REPORT_LOG")
    if path:
        line = {"method": method, "tool": params.get("name"), "_meta": params.get("_meta")}
        if method == "initialize":
            line.update(capabilities=params.get("capabilities"), clientInfo=params.get("clientInfo"))
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
        except OSError:
            pass


def handle(method, params):
    debug(method, params)
    if method == "initialize":
        want = params.get("protocolVersion")
        return {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {}, "resources": {}, "extensions": {"io.modelcontextprotocol/ui": {}}},
                "serverInfo": {"name": "zebra-bi-report", "title": "Zebra BI report", "version": S.version()}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools()}
    if method == "resources/list":
        return {"resources": [{"uri": app_uri(), "name": "report-builder", "title": "Zebra BI report builder",
                               "mimeType": MIME, "_meta": {"ui": {"csp": CSP, "prefersBorder": False}}},
                              {"uri": app_uri("card"), "name": "chart-card", "title": "Zebra BI chart",
                               "mimeType": MIME, "_meta": {"ui": {"csp": CSP, "prefersBorder": True}}}]}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "resources/read":
        for name in PAGES:
            page, uri = app_page(name)
            if page is not None and params.get("uri") == uri:
                return {"contents": [{"uri": uri, "mimeType": MIME, "text": page,
                                      "_meta": {"ui": {"csp": CSP, "prefersBorder": name == "card"},
                                                "openai/widgetCSP": {"connect_domains": [], "resource_domains": FONTS,
                                                                     "frame_domains": []},
                                                "openai/widgetDescription": PAGES[name][2]}}]}
        raise KeyError("unknown resource %r" % params.get("uri"))
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name in app_only_tools():
            if model_turn(params.get("_meta")) or not from_builder(args):
                return text("%s is for the report builder app, not the model; ask the user to do it in the builder." % name,
                            error=True)
            args = {k: v for k, v in args.items() if k != "app_key"}
        try:
            return call(name, args if isinstance(args, dict) else {}, thread_of(params.get("_meta")))
        except KeyError:
            raise
        except (ValueError, OSError, SystemExit) as e:
            return text(S.clean(e, 600), error=True)
    raise KeyError("method not found: %s" % method)


def reply(msg):
    OUT.write(json.dumps(msg, ensure_ascii=False) + "\n")
    OUT.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(msg, dict) or msg.get("method") is None or "id" not in msg:
            continue  # notifications and stray responses
        try:
            reply({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg["method"], msg.get("params") or {})})
        except KeyError as e:
            reply({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e.args[0] if e.args else e)}})
        except Exception as e:  # keep serving; one bad request must not end the session
            log(traceback.format_exc())
            reply({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": S.clean(e, 300)}})


if __name__ == "__main__":
    main()
