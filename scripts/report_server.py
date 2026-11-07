#!/usr/bin/env python3
"""report_server.py: the Zebra BI report builder as a local MCP server over stdio.

  python3 -I report_server.py

It serves one MCP App, the report builder (assets/report-builder.html), where the user lays out Zebra BI charts
and text in rows. The model adds charts and text with tools; the builder moves, resizes and edits them and saves
through app-only tools. Reports are files on this machine (report.py); charts are drawn by render_visual on the
Zebra BI MCP with the sign-in that story.py keeps (`story.py sign-in`), and nothing about a report is stored on a
Zebra BI server.

The model sees report titles, chart titles and the start of each text, never the chart data: tool results that
carry data are app-only (_meta.ui.visibility ["app"]).

In ChatGPT and Codex desktop the builder opens from the sidebar (entrypoint "global") or beside a chat
("thread"). The renderer is nested in the builder through iframe srcdoc: the desktop hosts block blob: frames.
Standard library only. stdout carries JSON-RPC alone; anything else goes to stderr.
"""
import hashlib, json, os, sys, traceback

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
CSP = {"connectDomains": [], "resourceDomains": [], "frameDomains": []}
SCHEMA_CACHE = os.path.join(os.path.dirname(S.CACHE), "render_visual.schema.json")
GENERIC_CHART = {"type": "object", "description": "The render_visual arguments: title, subtitle, chart_type, "
                 "category_columns, time_axis, measure {name, Val, PY, PM, PL, FC}, columns and rows."}

state = {"path": None}


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


def tools():
    return [
        {"name": "open_report", "title": "Zebra BI report",
         "description": "Open the Zebra BI report builder on a report: an existing one by name, or a new one with this "
                        "title. Without arguments it opens the most recent report. The user arranges charts and text "
                        "in the builder; add content with add_chart and add_text.",
         "inputSchema": {"type": "object", "properties": {
             "title": {"type": "string", "description": "Title of a new report, or of the report to open."},
             "report": {"type": "string", "description": "Name (slug) of an existing report, from list_reports."}}},
         "annotations": {"readOnlyHint": False, "idempotentHint": True}, "_meta": ui(entrypoints=True)},
        {"name": "list_reports", "title": "List Zebra BI reports",
         "description": "List the Zebra BI reports on this computer, newest first.",
         "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
        {"name": "add_chart", "title": "Add a Zebra BI chart to the report",
         "description": "Draw a Zebra BI chart with render_visual and add it to the open report. Send aggregated "
                        "rows only, one per category. It joins the given row when that row has room (at most 3 cells), "
                        "else starts a new row. Returns the server's notes on the chart, not the chart itself.",
         "inputSchema": {"type": "object", "required": ["chart"], "properties": {
             "chart": chart_schema(),
             "row": {"type": "integer", "minimum": 1, "description": "Row to join (1-based). Omit for a new row."}}},
         "annotations": {"readOnlyHint": False}},
        {"name": "add_text", "title": "Add text to the report",
         "description": "Add a narrative paragraph to the open report (**bold** allowed). It joins the given row when "
                        "that row has room, else starts a new row.",
         "inputSchema": {"type": "object", "required": ["text"], "properties": {
             "text": {"type": "string", "maxLength": R.MAX_TEXT},
             "row": {"type": "integer", "minimum": 1}}},
         "annotations": {"readOnlyHint": False}},
        {"name": "get_report", "title": "Report for the builder", "description": "App only: the open report with chart results.",
         "inputSchema": {"type": "object", "properties": {"since": {"type": "integer"}}},
         "annotations": {"readOnlyHint": True}, "_meta": ui(app_only=True)},
        {"name": "get_renderer", "title": "Zebra BI renderer", "description": "App only: the Zebra BI chart renderer page.",
         "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}, "_meta": ui(app_only=True)},
        {"name": "save_layout", "title": "Save the report layout", "description": "App only: save the builder's rows and title.",
         "inputSchema": {"type": "object", "required": ["rev", "rows"], "properties": {
             "rev": {"type": "integer"}, "title": {"type": "string"}, "rows": {"type": "array"}}},
         "annotations": {"readOnlyHint": False}, "_meta": ui(app_only=True)},
    ]


def text(t, structured=None, error=False):
    out = {"content": [{"type": "text", "text": t}]}
    if structured is not None:
        out["structuredContent"] = structured
    if error:
        out["isError"] = True
    return out


def current(create_title=None):
    """Path of the open report: the one opened last by any process (the host runs one server per surface, so
    the chat and the sidebar builder are different processes), else the newest on disk, else a new one."""
    path = R.remembered()
    if path:
        state["path"] = path
        return path
    found = R.reports()
    if found and not create_title:
        state["path"] = found[0][2]
        R.remember(found[0][0])
        return state["path"]
    return open_or_create(create_title or "Untitled report")


def open_or_create(title=None, slug=None):
    if slug:
        path = R.path_for(slug)
        if not os.path.isfile(path):
            raise ValueError("no report named %r; list_reports shows the names" % slug)
    else:
        slug = R.slugify(title or "Untitled report")
        path = R.path_for(slug)
        if not os.path.isfile(path):
            R.save(path, R.new(title))
    state["path"] = path
    R.remember(os.path.basename(os.path.dirname(path)))
    return path


def results_for(rep, path):
    """render_visual results for every chart in the report, drawing the ones not cached yet."""
    out, errors = {}, {}
    for vid, v in rep["visuals"].items():
        try:
            out[vid] = R.render(os.path.dirname(path), vid, v["args"])
        except S.SignIn:
            errors[vid] = "Zebra BI needs sign-in: run %s" % S.SIGN_IN_CMD
        except (S.Unavailable, ValueError, SystemExit) as e:
            errors[vid] = S.clean(e, 300)
    return out, errors


def call(name, args):
    if name == "open_report":
        if args.get("report"):
            path = open_or_create(slug=str(args["report"]))
        elif args.get("title"):
            path = open_or_create(title=str(args["title"]))
        else:
            path = current()
        rep = R.load(path)
        return text(R.summary(rep) + "\nThe builder is open; the user can arrange it there.",
                    {"report": os.path.basename(os.path.dirname(path)), "title": rep["title"], "rev": rep["rev"]})
    if name == "list_reports":
        found = R.reports()
        if not found:
            return text("No reports yet in %s. open_report with a title starts one." % R.root())
        return text("\n".join("%s: %s" % (slug, title) for slug, title, _, _ in found))
    if name == "add_chart":
        path = current(None)
        rep = R.load(path)
        vid, row = R.add_chart(rep, args.get("chart"), args.get("row"))
        try:
            res = R.render(os.path.dirname(path), vid, rep["visuals"][vid]["args"])
        except S.SignIn:
            return text("Zebra BI needs sign-in before charts can be drawn. Ask the user to run: %s" % S.SIGN_IN_CMD, error=True)
        except (S.Unavailable, ValueError, SystemExit) as e:
            return text("The chart was not added: %s" % S.clean(e, 600), error=True)
        R.save(path, rep)
        return text("Added chart %r to row %d of %r. Zebra BI: %s" % (R.chart_title(rep, vid), row, rep["title"], R.server_note(res)))
    if name == "add_text":
        path = current(None)
        rep = R.load(path)
        row = R.add_text(rep, args.get("text"), args.get("row"))
        R.save(path, rep)
        return text("Added text to row %d of %r." % (row, rep["title"]))
    if name == "get_report":
        path = current(None)
        rep = R.load(path)
        if args.get("since") is not None and args["since"] == rep["rev"]:
            return text("unchanged", {"unchanged": True, "rev": rep["rev"]})
        results, errors = results_for(rep, path)
        return text("report %s rev %d" % (rep["title"], rep["rev"]),
                    {"report": rep, "results": results, "errors": errors, "path": path})
    if name == "get_renderer":
        try:
            rpath, meta = S.renderer()
        except SystemExit as e:
            return text("The Zebra BI renderer is unavailable: %s" % S.clean(e, 300), error=True)
        with open(rpath, encoding="utf-8") as f:
            return text("renderer %s" % meta.get("sha256", "")[:12], {"renderer": f.read(), "sha256": meta.get("sha256")})
    if name == "save_layout":
        path = current(None)
        rep = R.load(path)
        try:
            new = R.apply_layout(rep, args.get("title"), args.get("rows"), args.get("rev"))
        except R.Conflict as e:
            return text(str(e), {"conflict": True, "rev": rep["rev"]}, error=True)
        rev = R.save(path, new)
        return text("saved rev %d" % rev, {"rev": rev})
    raise KeyError("unknown tool %r" % name)


def handle(method, params):
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
                                        "openai/widgetCSP": {"connect_domains": [], "resource_domains": [], "frame_domains": []},
                                        "openai/widgetDescription": "The Zebra BI report builder: charts and text in rows."}}]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            return call(name, args if isinstance(args, dict) else {})
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
