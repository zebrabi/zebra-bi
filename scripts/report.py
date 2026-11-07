#!/usr/bin/env python3
"""report.py: a Zebra BI report, a page of rows with up to three cells each, a cell holding a chart or text.

Library for report_server.py. Standard library only.

A report is one JSON file, <reports>/<slug>/report.zbreport, on the user's machine; nothing about it is kept
on a Zebra BI server. Its charts are drawn by render_visual on the Zebra BI MCP, as story.py draws a story's,
and each result is cached in visuals/<id>.json next to the file. The format:

  {"format": "zebra-bi-report", "version": 1, "title": "...", "rev": 3,
   "rows": [{"cells": [{"id": "c1", "type": "chart", "visual": "sales-by-month"},
                       {"id": "c2", "type": "text", "text": "Sales are **12%** ahead of plan."}]}],
   "visuals": {"sales-by-month": {"args": { ...render_visual arguments... }}}}

`visuals` is the same map story.json has, so the story exports can read a report. `rev` goes up by one on every
save; the builder sends the rev it last saw, and a save against an older rev is refused so two editors do not
overwrite each other.
"""
import json, os, re, time

import story as S

FORMAT = "zebra-bi-report"
EXT = ".zbreport"
FILE = "report" + EXT
MAX_CELLS = 3
MAX_ROWS = 60
MAX_TEXT = 5000
MAX_TITLE = 200
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
CELL_ID = re.compile(r"c[0-9]{1,6}")


class Conflict(Exception):
    """The report changed since the editor last read it."""


def root():
    """The folder that holds every report: $ZEBRA_BI_REPORTS, else ~/Documents/Zebra BI Reports."""
    return os.path.abspath(os.path.expanduser(os.environ.get("ZEBRA_BI_REPORTS") or "~/Documents/Zebra BI Reports"))


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-")
    return slug or "report"


def path_for(slug):
    if not ID.fullmatch(slug):
        raise ValueError("report %r: use lowercase letters, digits and dashes" % slug)
    return os.path.join(root(), slug, FILE)


def new(title):
    return {"format": FORMAT, "version": 1, "title": clean_title(title), "rev": 0, "rows": [], "visuals": {}}


def clean_title(title):
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    return title or "Untitled report"


def validate(rep):
    """Raise ValueError when rep is not a report this code can show and save."""
    if not isinstance(rep, dict) or rep.get("format") != FORMAT:
        raise ValueError("not a Zebra BI report (format must be %r)" % FORMAT)
    if not isinstance(rep.get("rows"), list) or len(rep["rows"]) > MAX_ROWS:
        raise ValueError("rows must be a list of at most %d rows" % MAX_ROWS)
    visuals = rep.get("visuals")
    if not isinstance(visuals, dict):
        raise ValueError("visuals must be an object")
    for vid, v in visuals.items():
        if not ID.fullmatch(vid) or not isinstance(v, dict) or not isinstance(v.get("args"), dict):
            raise ValueError("visual %r: needs lowercase id and an args object" % vid)
    seen = set()
    for i, row in enumerate(rep["rows"]):
        cells = row.get("cells") if isinstance(row, dict) else None
        if not isinstance(cells, list) or not 1 <= len(cells) <= MAX_CELLS:
            raise ValueError("row %d must hold 1 to %d cells" % (i + 1, MAX_CELLS))
        for cell in cells:
            cid = cell.get("id") if isinstance(cell, dict) else None
            if not isinstance(cid, str) or not CELL_ID.fullmatch(cid) or cid in seen:
                raise ValueError("row %d: every cell needs its own id like c1" % (i + 1))
            seen.add(cid)
            if cell.get("type") == "chart":
                if cell.get("visual") not in visuals:
                    raise ValueError("cell %s shows a chart the report does not hold" % cid)
            elif cell.get("type") == "text":
                if not isinstance(cell.get("text"), str) or len(cell["text"]) > MAX_TEXT:
                    raise ValueError("cell %s: text must be a string of at most %d characters" % (cid, MAX_TEXT))
            else:
                raise ValueError("cell %s: type must be chart or text" % cid)
    return rep


def load(path):
    with open(path, encoding="utf-8") as f:
        rep = json.load(f)
    validate(rep)
    rep.setdefault("rev", 0)
    return rep


def save(path, rep):
    """Validate, bump rev and write atomically. Returns the new rev."""
    validate(rep)
    rep["rev"] = int(rep.get("rev") or 0) + 1
    rep["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    S.write_atomic(path, json.dumps(rep, indent=1, ensure_ascii=False))
    return rep["rev"]


def reports():
    """(slug, title, path, mtime) of every report under root(), newest first."""
    out, base = [], root()
    for slug in os.listdir(base) if os.path.isdir(base) else []:
        p = os.path.join(base, slug, FILE)
        if ID.fullmatch(slug) and os.path.isfile(p):
            try:
                title = load(p)["title"]
            except (OSError, ValueError):
                continue
            out.append((slug, title, p, os.path.getmtime(p)))
    return sorted(out, key=lambda r: -r[3])


def next_cell_id(rep):
    used = [int(c["id"][1:]) for row in rep["rows"] for c in row["cells"]]
    return "c%d" % (max(used, default=0) + 1)


def unique_visual_id(rep, title):
    base = slugify(title or "chart")[:50] or "chart"
    vid, n = base, 2
    while vid in rep["visuals"]:
        vid, n = "%s-%d" % (base, n), n + 1
    return vid


def place(rep, cell, row=None):
    """Append cell to row (1-based) when it has room, else as a new row at the end."""
    rows = rep["rows"]
    if row is not None and 1 <= row <= len(rows) and len(rows[row - 1]["cells"]) < MAX_CELLS:
        rows[row - 1]["cells"].append(cell)
        return row
    if len(rows) >= MAX_ROWS:
        raise ValueError("the report already has %d rows" % MAX_ROWS)
    rows.append({"cells": [cell]})
    return len(rows)


def add_chart(rep, args, row=None):
    """Add a chart cell for render_visual args. Returns (visual id, row number)."""
    if not isinstance(args, dict) or not args:
        raise ValueError("chart needs the render_visual arguments")
    vid = unique_visual_id(rep, args.get("title"))
    rep["visuals"][vid] = {"args": args}
    return vid, place(rep, {"id": next_cell_id(rep), "type": "chart", "visual": vid}, row)


def add_text(rep, text, row=None):
    text = str(text or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise ValueError("text must hold 1 to %d characters" % MAX_TEXT)
    return place(rep, {"id": next_cell_id(rep), "type": "text", "text": text}, row)


def apply_layout(rep, title, rows, rev):
    """The builder's edit: a new title and the rows as it shows them. Cells keep their ids; a chart cell may only
    name a chart the report holds, so the editor can move and drop charts but never invent one. Charts no cell
    shows any more are dropped from visuals."""
    if rev != rep.get("rev"):
        raise Conflict("the report changed since it was opened (rev %s, now %s)" % (rev, rep.get("rev")))
    new_rows = []
    for row in rows if isinstance(rows, list) else []:
        cells = [c for c in (row.get("cells") if isinstance(row, dict) else None) or [] if isinstance(c, dict)]
        if not cells:
            continue  # an emptied row disappears
        kept = []
        for c in cells:
            if c.get("type") == "chart":
                kept.append({"id": c.get("id"), "type": "chart", "visual": c.get("visual")})
            else:
                kept.append({"id": c.get("id"), "type": "text", "text": str(c.get("text") or "")})
        new_rows.append({"cells": kept})
    out = dict(rep, title=clean_title(title if title is not None else rep["title"]), rows=new_rows)
    shown = {c["visual"] for r in new_rows for c in r["cells"] if c["type"] == "chart"}
    out["visuals"] = {k: v for k, v in rep["visuals"].items() if k in shown}
    validate(out)
    return out


def chart_title(rep, vid):
    args = rep["visuals"].get(vid, {}).get("args", {})
    return str(args.get("title") or vid)


def summary(rep):
    """What the model sees: the title and, per row, the chart titles and the start of each text. Never the data."""
    lines = ["Report: %s (%d rows)" % (rep["title"], len(rep["rows"]))]
    for i, row in enumerate(rep["rows"], 1):
        parts = []
        for c in row["cells"]:
            if c["type"] == "chart":
                parts.append("chart %r" % chart_title(rep, c["visual"]))
            else:
                t = " ".join(c["text"].split())
                parts.append("text %r" % (t[:80] + ("..." if len(t) > 80 else "")))
        lines.append("Row %d: %s" % (i, "; ".join(parts)))
    return "\n".join(lines)


def render(report_dir, vid, args):
    """The render_visual result for one chart, from visuals/<vid>.json when the args are unchanged, else drawn by
    the Zebra BI MCP and cached. Raises S.SignIn or S.Unavailable; never prints or exits."""
    vdir = os.path.join(report_dir, "visuals")
    if os.path.islink(vdir):
        raise S.Unavailable("%s is a symbolic link" % vdir)
    os.makedirs(vdir, exist_ok=True)
    out, h = os.path.join(vdir, vid + ".json"), S.args_hash(args)
    res = S.cached_result(out, h)
    if res is not None:
        return res
    S.check_endpoint()
    res = S.rpc("tools/call", {"name": "render_visual", "arguments": args})
    res = res if isinstance(res, dict) else {}
    content = [c for c in res.get("content") or [] if isinstance(c, dict)] if isinstance(res.get("content"), list) else []
    if res.get("isError"):
        raise ValueError("Zebra BI refused the chart: %s" % S.clean(" ".join(str(c.get("text", "")) for c in content)))
    sc = res.get("structuredContent")
    if not (isinstance(sc, dict) and isinstance(sc.get("visual"), dict) and sc["visual"]):
        raise S.Unavailable("render_visual returned no chart for %r" % vid)
    S.write_atomic(out, json.dumps({"args_hash": h, "endpoint": S.ENDPOINT, "result": res}))
    return res


def server_note(res):
    """The server's own text for a drawn chart (it lists every adjustment it made), one clean line."""
    content = res.get("content") if isinstance(res.get("content"), list) else []
    return S.clean(" ".join(str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("type") == "text"), 600)
