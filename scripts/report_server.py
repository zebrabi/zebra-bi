#!/usr/bin/env python3
"""report_server.py: the Zebra BI report builder as a local MCP server over stdio.

  python3 -I report_server.py

It serves one MCP App, the report builder (assets/report-builder.html), where the user lays out Zebra BI charts
and text in rows. The model adds charts and text with tools; the builder moves, resizes and edits them and saves
through app-only tools. Reports are files on this machine (report.py); charts are drawn by render_visual on the
Zebra BI MCP with the sign-in that story.py keeps (`story.py sign-in`), and nothing about a report is stored on a
Zebra BI server.

The model sees report titles, chart titles and the start of each text. Chart data reaches it only through
read_chart, which returns the aggregated rows it sent itself; the builder's results with drawn charts are app-only
(_meta.ui.visibility ["app"]).

In ChatGPT and Codex desktop the builder opens from the sidebar (entrypoint "global") or beside a chat
("thread"). The renderer is nested in the builder through iframe srcdoc: the desktop hosts block blob: frames.
Standard library only. stdout carries JSON-RPC alone; anything else goes to stderr.
"""
import hashlib, json, os, re, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = sys.stdout
sys.stdout = sys.stderr  # story.py prints progress; it must never reach the JSON-RPC stream

import report as R  # noqa: E402
import story as S  # noqa: E402

APP = os.path.join(HERE, "..", "assets", "report-builder.html")


def app_uri():
    """The builder's address carries a hash of the page: hosts cache an app page by its URI, so a changed page
    must get a new one."""
    try:
        with open(APP, "rb") as f:
            return "ui://zebra-bi/report-builder-%s.html" % hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return "ui://zebra-bi/report-builder.html"


APP_URI = app_uri()
MIME = "text/html;profile=mcp-app"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
FONTS = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"]  # Open Sans, the Zebra BI typeface
CSP = {"connectDomains": [], "resourceDomains": FONTS, "frameDomains": []}
SCHEMA_CACHE = os.path.join(os.path.dirname(S.CACHE), "render_visual.schema.json")
GENERIC_CHART = {"type": "object", "description": "The render_visual arguments: title, subtitle, chart_type, "
                 "category_columns, time_axis, measure {name, Val, PY, PM, PL, FC}, columns and rows."}

state = {}


def log(*parts):
    print("report_server:", *parts, file=sys.stderr, flush=True)


def chart_schema():
    """render_visual's own input schema from the Zebra BI MCP, cached; a generic one when it cannot be read.
    One attempt per session, so an offline start does not slow every tools/list."""
    if "schema" in state:
        return state["schema"]
    state["schema"] = GENERIC_CHART
    try:
        with open(SCHEMA_CACHE, encoding="utf-8") as f:
            state["schema"] = json.load(f)
            return state["schema"]
    except (OSError, ValueError):
        pass
    try:
        res = S.rpc("tools/list", {}, timeout=15)
        for tool in res.get("tools") or []:
            if isinstance(tool, dict) and tool.get("name") == "render_visual" and isinstance(tool.get("inputSchema"), dict):
                S.write_atomic(SCHEMA_CACHE, json.dumps(tool["inputSchema"]))
                state["schema"] = tool["inputSchema"]
                return state["schema"]
    except (S.SignIn, S.Unavailable, SystemExit, Exception) as e:  # schema is a nicety; never block tools/list
        log("render_visual schema unavailable:", e)
    return GENERIC_CHART


def ui(entrypoints=False, app_only=False):
    meta = {"ui": {"resourceUri": APP_URI}, "ui/resourceUri": APP_URI, "openai/outputTemplate": APP_URI}
    if entrypoints:
        meta["openai/ui"] = {"entrypoints": [{"type": "global"}, {"type": "thread"}]}
    if app_only:
        meta = {"ui": {"visibility": ["app"]}}
    return meta


def opt_report():
    return {"type": "string", "description": "Report name (slug) or title; omit for the report open in this chat."}


def tools():
    ui_open, app = ui(entrypoints=True), ui(app_only=True)
    obj = lambda props, req=(): dict({"type": "object", "properties": props}, **({"required": list(req)} if req else {}))
    write = {"readOnlyHint": False}
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
        {"name": "add_chart", "title": "Add a Zebra BI chart to the report",
         "description": "Draw a Zebra BI chart with render_visual and add it to the report: `message` goes above the "
                        "chart (its supporting message, numbers in **bold**), `note` under it (two or three sentences of "
                        "evidence read from the chart), `why` says in one sentence why this chart type, `recipe` keeps how "
                        "the rows were made (source, query or sheet, the code you ran, the period). Every number in the "
                        "text must come from the chart's rows (IBCS: AC, PL, PY, ΔPL%; no cause the data does not show). "
                        "Send aggregated rows only, one per category. It joins `row` when that row has room (at most 3 "
                        "cells), else starts a new row. Returns Zebra BI's notes on the chart and any number in your text "
                        "the chart does not hold.",
         "inputSchema": obj({"chart": chart_schema(), "message": {"type": "string", "maxLength": R.MAX_MESSAGE},
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
                        "cell keeps its place.",
         "inputSchema": obj({"cell": {"type": "string"}, "chart": chart_schema(), "report": opt_report()}, ["cell", "chart"]),
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
        {"name": "library", "title": "Reports for the builder", "description": "App only: reports and Recently deleted.",
         "inputSchema": obj({}), "annotations": {"readOnlyHint": True}, "_meta": app},
        {"name": "get_report", "title": "Report for the builder", "description": "App only: one report with chart results.",
         "inputSchema": obj({"report": {"type": "string"}, "follow": {"type": "string", "enum": ["last", "thread", "none"]},
                             "since": {"type": "integer"}}), "annotations": {"readOnlyHint": True}, "_meta": app},
        {"name": "get_renderer", "title": "Zebra BI renderer", "description": "App only: the Zebra BI chart renderer page.",
         "inputSchema": obj({}), "annotations": {"readOnlyHint": True}, "_meta": app},
        {"name": "save_layout", "title": "Save the report layout", "description": "App only: save the builder's rows and title.",
         "inputSchema": obj({"report": {"type": "string"}, "rev": {"type": "integer"}, "title": {"type": "string"},
                             "rows": {"type": "array"}, "show_notes": {"type": "boolean"}}, ["report", "rev", "rows"]), "annotations": write, "_meta": app},
        {"name": "purge_report", "title": "Delete a report forever", "description": "App only: remove a Recently deleted report for good.",
         "inputSchema": obj({"entry": {"type": "string"}}, ["entry"]), "annotations": {"readOnlyHint": False, "destructiveHint": True},
         "_meta": app},
    ]


def text(t, structured=None, error=False):
    out = {"content": [{"type": "text", "text": t}]}
    if structured is not None:
        out["structuredContent"] = structured
    if error:
        out["isError"] = True
    return out


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


def draw(slug, vid, args):
    """Render one chart into the report's cache; a readable error result when Zebra BI cannot draw it."""
    try:
        return R.render(os.path.dirname(R.path_for(slug)), vid, args), None
    except S.SignIn:
        return None, text("Zebra BI needs sign-in before charts can be drawn. Ask the user to run: %s" % S.SIGN_IN_CMD, error=True)
    except (S.Unavailable, ValueError, SystemExit) as e:
        return None, text("The chart was not drawn: %s" % S.clean(e, 600), error=True)


def results_for(rep, slug):
    """render_visual results for every chart in the report, drawing the ones not cached yet."""
    out, errors = {}, {}
    for vid, v in rep["visuals"].items():
        res, err = draw(slug, vid, v["args"])
        if res is not None:
            out[vid] = res
        else:
            errors[vid] = err["content"][0]["text"]
    return out, errors


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
        if err:
            return err
        rep, (vid, row) = R.change(R.path_for(slug), add)
        if vid not in probe["visuals"]:  # the id moved on meanwhile; the builder draws it from the same arguments
            res = draw(slug, vid, rep["visuals"][vid]["args"])[0] or res
        cid = next(c["id"] for r in rep["rows"] for c in r["cells"] if c.get("visual") == vid)
        return text("Added chart %r to row %d of %r. Zebra BI: %s%s" % (R.chart_title(rep, vid), row, rep["title"],
                    R.server_note(res), unchecked(rep, cid)), {"report": slug, "rev": rep["rev"]})
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
        if err:
            return err
        rep, vid = R.change(R.path_for(slug), swap)
        return text("Replaced chart %s with %r in %r. Zebra BI: %s" % (args.get("cell"), R.chart_title(rep, vid), rep["title"],
                    R.server_note(res)), {"report": slug, "rev": rep["rev"]})
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
        home = os.path.expanduser("~")
        shown = "~" + out[len(home):] if out.startswith(home + os.sep) else out
        layout = R.load(R.path_for(slug)).get("layout")
        return text(pdf_saved(out, pages, layout), {"report": slug, "file": out, "shown": shown, "pages": pages})
    if name == "save_pptx":
        slug = target(args, thread)
        found = R.flags(R.load(R.path_for(slug)))
        if found:
            return text("No PowerPoint was saved: these numbers are not in their charts: %s. Fix them, or the user keeps "
                        "them in the builder." % "; ".join(found), {"report": slug, "flags": found}, error=True)
        import office as O  # starts nothing until a deck is asked for
        folder = os.path.dirname(R.path_for(slug))
        out = os.path.join(folder, os.path.basename(folder) + ".pptx")
        try:
            O.build(folder, ["pptx"])
        except SystemExit as e:
            if e.code:
                return text("No PowerPoint was saved: %s" % S.clean(e, 600), error=True)
        slides = deck_slides(out)
        home = os.path.expanduser("~")
        shown = "~" + out[len(home):] if out.startswith(home + os.sep) else out
        return text("Saved %s (%d slides). It holds the report's aggregated numbers, so share it only where the data may go."
                    % (out, slides), {"report": slug, "file": out, "shown": shown, "slides": slides})
    # ----- app only -----
    if name == "library":
        return text("library", {"reports": R.reports(), "deleted": R.deleted(), "current": R.resolve(thread), "last": R.last()})
    if name == "get_report":
        slug, follow = args.get("report"), args.get("follow") or "none"
        followed = R.last() if follow == "last" else R.resolve(thread) if follow == "thread" else None
        if followed and followed != slug:
            return text("switched", {"switched": followed})
        if not slug or not R.exists(slug):
            return text("gone", {"gone": True, "report": slug})
        rep = R.load(R.path_for(slug))
        if args.get("since") is not None and args["since"] == rep["rev"]:
            return text("unchanged", {"unchanged": True, "rev": rep["rev"]})
        results, errors = results_for(rep, slug)
        return text("report %s rev %d" % (rep["title"], rep["rev"]), {"report": rep, "slug": slug, "results": results, "errors": errors,
                                                                     "checks": R.checks(rep)})
    if name == "get_renderer":
        try:
            rpath, meta = S.renderer()
        except SystemExit as e:
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
    """With $ZEBRA_BI_REPORT_LOG set, one JSON line per request: method, tool and _meta (never arguments)."""
    path = os.environ.get("ZEBRA_BI_REPORT_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"method": method, "tool": params.get("name"), "_meta": params.get("_meta")}) + "\n")
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
        return {"resources": [{"uri": APP_URI, "name": "report-builder", "title": "Zebra BI report builder",
                               "mimeType": MIME, "_meta": {"ui": {"csp": CSP, "prefersBorder": False}}}]}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "resources/read":
        if params.get("uri") != APP_URI:
            raise KeyError("unknown resource %r" % params.get("uri"))
        with open(APP, encoding="utf-8") as f:
            page = f.read()
        return {"contents": [{"uri": APP_URI, "mimeType": MIME, "text": page,
                              "_meta": {"ui": {"csp": CSP, "prefersBorder": False},
                                        "openai/widgetCSP": {"connect_domains": [], "resource_domains": FONTS, "frame_domains": []},
                                        "openai/widgetDescription": "The Zebra BI report builder: charts and text in rows."}}]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
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
