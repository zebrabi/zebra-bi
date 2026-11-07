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
import datetime as dt, json, os, re, shutil, time

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
    if not isinstance(rep.get("show_notes", True), bool):
        raise ValueError("show_notes must be true or false")
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
                note = cell.get("note", "")
                if not isinstance(note, str) or len(note) > MAX_TEXT:
                    raise ValueError("cell %s: the note must be a string of at most %d characters" % (cid, MAX_TEXT))
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


def info(slug):
    """What a list shows about one report: slug, title, saved_at, charts and rows. None when it cannot be read."""
    try:
        rep = load(path_for(slug))
    except (OSError, ValueError):
        return None
    cells = [c for r in rep["rows"] for c in r["cells"]]
    return {"report": slug, "title": rep["title"], "saved_at": rep.get("saved_at"), "rev": rep["rev"],
            "charts": sum(1 for c in cells if c["type"] == "chart"), "rows": len(rep["rows"])}


def reports():
    """info() of every report under root(), last saved first."""
    out, base = [], root()
    for slug in os.listdir(base) if os.path.isdir(base) else []:
        if ID.fullmatch(slug) and os.path.isfile(os.path.join(base, slug, FILE)):
            i = info(slug)
            if i:
                i["mtime"] = os.path.getmtime(os.path.join(base, slug, FILE))
                out.append(i)
    return sorted(out, key=lambda r: -r["mtime"])


STATE = ".state.json"  # under root(): the open report per chat thread, and the one opened last anywhere
MAX_THREADS = 200
THREAD_DAYS = 90


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state():
    try:
        with open(os.path.join(root(), STATE), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st = st if isinstance(st, dict) else {}
    st["threads"] = st["threads"] if isinstance(st.get("threads"), dict) else {}
    return st


def write_state(st):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=THREAD_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = sorted(((t, v) for t, v in st["threads"].items() if isinstance(v, dict) and str(v.get("at", "")) >= cutoff),
                  key=lambda tv: tv[1].get("at", ""), reverse=True)[:MAX_THREADS]
    st["threads"] = dict(kept)
    os.makedirs(root(), exist_ok=True)
    S.write_atomic(os.path.join(root(), STATE), json.dumps(st, indent=1))


def remember(slug, thread=None):
    """slug is now the open report of this chat thread, and the report opened last anywhere."""
    st = read_state()
    st["last"] = slug
    if thread:
        st["threads"][str(thread)] = {"report": slug, "at": now()}
    write_state(st)


def forget(slug):
    """Clear slug wherever it is open (it was deleted)."""
    st = read_state()
    if st.get("last") == slug:
        st.pop("last")
    st["threads"] = {t: v for t, v in st["threads"].items() if v.get("report") != slug}
    write_state(st)


def exists(slug):
    return bool(slug) and ID.fullmatch(slug) is not None and os.path.isfile(os.path.join(root(), slug, FILE))


def resolve(thread=None):
    """The report a chat works on: its thread's open report, else the one opened last, else the newest. None when
    there is no report at all."""
    st = read_state()
    entry = st["threads"].get(str(thread)) if thread else None
    for slug in ((entry or {}).get("report"), st.get("last")):
        if exists(slug):
            return slug
    found = reports()
    return found[0]["report"] if found else None


def last():
    st = read_state()
    return st.get("last") if exists(st.get("last")) else resolve(None)


def find(name):
    """A report by slug, else by title (exact, then unique prefix, ignoring case). Raises ValueError otherwise."""
    name = str(name or "").strip()
    if exists(name):
        return name
    found, low = reports(), name.lower()
    for match in ([r for r in found if r["title"].lower() == low], [r for r in found if r["title"].lower().startswith(low)]):
        if len(match) == 1:
            return match[0]["report"]
        if len(match) > 1:
            raise ValueError("%d reports match %r: %s" % (len(match), name, ", ".join(r["report"] for r in match)))
    raise ValueError("no report named %r; list_reports shows them" % name)


# ---------- lifecycle ----------

DELETED = ".deleted"
KEEP_DAYS = 30


def unique_slug(title):
    base, n = slugify(title), 2
    slug = base
    while os.path.exists(os.path.join(root(), slug)):
        slug = "%s-%d" % (base[:56], n)
        n += 1
    return slug


def create(title):
    """A new empty report with its own folder. Returns its slug; a title that clashes gets a new slug."""
    slug = unique_slug(title)
    save(path_for(slug), new(title))
    return slug


def rename(slug, title):
    path = path_for(slug)
    rep = load(path)
    rep["title"] = clean_title(title)
    save(path, rep)
    return rep["title"]


def duplicate(slug, title=None):
    """A copy of the report, its text and its cached charts, as a new report. Returns the new slug."""
    rep = load(path_for(slug))
    title = clean_title(title or "%s (copy)" % rep["title"])
    new_slug = unique_slug(title)
    src, dst = os.path.join(root(), slug), os.path.join(root(), new_slug)
    os.makedirs(dst)
    if os.path.isdir(os.path.join(src, "visuals")) and not os.path.islink(os.path.join(src, "visuals")):
        shutil.copytree(os.path.join(src, "visuals"), os.path.join(dst, "visuals"))
    rep.update(title=title, rev=0)
    save(os.path.join(dst, FILE), rep)
    return new_slug


def trash(slug):
    """Move the report to Recently deleted. Returns the entry name restore() takes."""
    if not exists(slug):
        raise ValueError("no report named %r" % slug)
    os.makedirs(os.path.join(root(), DELETED), exist_ok=True)
    entry = "%s--%s" % (slug, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    os.replace(os.path.join(root(), slug), os.path.join(root(), DELETED, entry))
    with open(os.path.join(root(), DELETED, entry, "deleted.json"), "w", encoding="utf-8") as f:
        json.dump({"deleted_at": now(), "report": slug}, f)
    forget(slug)
    return entry


ENTRY = re.compile(r"([a-z0-9][a-z0-9-]{0,60})--[0-9]{8}T[0-9]{12}")


def deleted():
    """Recently deleted reports, newest first, after removing the ones older than KEEP_DAYS."""
    base, out = os.path.join(root(), DELETED), []
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in os.listdir(base) if os.path.isdir(base) else []:
        d = os.path.join(base, entry)
        if not ENTRY.fullmatch(entry) or os.path.islink(d):
            continue
        try:
            with open(os.path.join(d, "deleted.json"), encoding="utf-8") as f:
                meta = json.load(f)
            title = load(os.path.join(d, FILE))["title"]
        except (OSError, ValueError):
            continue
        if str(meta.get("deleted_at", "")) < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            continue
        out.append({"entry": entry, "report": ENTRY.fullmatch(entry).group(1), "title": title, "deleted_at": meta.get("deleted_at")})
    return sorted(out, key=lambda r: r["deleted_at"], reverse=True)


def deleted_entry(name):
    """A Recently deleted entry by entry name, slug or title (the newest when several match)."""
    name, items = str(name or "").strip(), deleted()
    for key in ("entry", "report", "title"):
        hit = [d for d in items if d[key].lower() == name.lower()]
        if hit:
            return hit[0]["entry"]
    raise ValueError("nothing named %r in Recently deleted" % name)


def restore(entry):
    """Move a Recently deleted report back. Returns its slug (a new one when the old is taken)."""
    if not ENTRY.fullmatch(entry):
        raise ValueError("not a Recently deleted entry: %r" % entry)
    src = os.path.join(root(), DELETED, entry)
    slug = ENTRY.fullmatch(entry).group(1)
    if os.path.exists(os.path.join(root(), slug)):
        slug = unique_slug(load(os.path.join(src, FILE))["title"])
    os.remove(os.path.join(src, "deleted.json"))
    os.replace(src, os.path.join(root(), slug))
    return slug


def purge(entry):
    """Delete a Recently deleted report for good."""
    if not ENTRY.fullmatch(entry):
        raise ValueError("not a Recently deleted entry: %r" % entry)
    d = os.path.join(root(), DELETED, entry)
    if not os.path.isdir(d) or os.path.islink(d):
        raise ValueError("nothing named %r in Recently deleted" % entry)
    shutil.rmtree(d)


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


def with_note(cell, note):
    """The chart cell with its note (the narrative under the chart), or without one when the note is empty."""
    note = str(note or "").strip()
    if len(note) > MAX_TEXT:
        raise ValueError("the note must hold at most %d characters" % MAX_TEXT)
    cell.pop("note", None)
    if note:
        cell["note"] = note
    return cell


def add_chart(rep, args, row=None, note=None):
    """Add a chart cell for render_visual args, with the narrative under it. Returns (visual id, row number)."""
    if not isinstance(args, dict) or not args:
        raise ValueError("chart needs the render_visual arguments")
    vid = unique_visual_id(rep, args.get("title"))
    rep["visuals"][vid] = {"args": args}
    return vid, place(rep, with_note({"id": next_cell_id(rep), "type": "chart", "visual": vid}, note), row)


def add_text(rep, text, row=None):
    text = str(text or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise ValueError("text must hold 1 to %d characters" % MAX_TEXT)
    return place(rep, {"id": next_cell_id(rep), "type": "text", "text": text}, row)


def apply_layout(rep, title, rows, rev, show_notes=None):
    """The builder's edit: a new title and the rows as it shows them. Cells keep their ids; a chart cell may only
    name a chart the report holds, so the editor can move and drop charts but never invent one. Charts no cell
    shows any more are dropped from visuals."""
    if rev != rep.get("rev"):
        raise Conflict("the report changed since it was opened (rev %s, now %s)" % (rev, rep.get("rev")))
    old_notes = {c["id"]: c.get("note") for r in rep["rows"] for c in r["cells"] if c["type"] == "chart"}
    new_rows = []
    for row in rows if isinstance(rows, list) else []:
        cells = [c for c in (row.get("cells") if isinstance(row, dict) else None) or [] if isinstance(c, dict)]
        if not cells:
            continue  # an emptied row disappears
        kept = []
        for c in cells:
            if c.get("type") == "chart":
                # a save without the note field (an older builder) keeps the note; "" removes it
                note = c["note"] if "note" in c else old_notes.get(c.get("id"))
                kept.append(with_note({"id": c.get("id"), "type": "chart", "visual": c.get("visual")}, note))
            else:
                kept.append({"id": c.get("id"), "type": "text", "text": str(c.get("text") or "")})
        new_rows.append({"cells": kept})
    out = dict(rep, title=clean_title(title if title is not None else rep["title"]), rows=new_rows)
    if show_notes is not None:
        out["show_notes"] = bool(show_notes)
    shown = {c["visual"] for r in new_rows for c in r["cells"] if c["type"] == "chart"}
    out["visuals"] = {k: v for k, v in rep["visuals"].items() if k in shown}
    validate(out)
    return out


# ---------- cells ----------

def locate(rep, cell):
    for ri, row in enumerate(rep["rows"]):
        for ci, c in enumerate(row["cells"]):
            if c["id"] == cell:
                return ri, ci
    raise ValueError("no cell %r in this report; open_report lists the cells" % cell)


def drop_unshown(rep):
    shown = {c["visual"] for r in rep["rows"] for c in r["cells"] if c["type"] == "chart"}
    rep["visuals"] = {k: v for k, v in rep["visuals"].items() if k in shown}


def remove_cell(rep, cell):
    ri, ci = locate(rep, cell)
    del rep["rows"][ri]["cells"][ci]
    rep["rows"] = [r for r in rep["rows"] if r["cells"]]
    drop_unshown(rep)


def update_text(rep, cell, text):
    ri, ci = locate(rep, cell)
    c = rep["rows"][ri]["cells"][ci]
    if c["type"] == "chart":  # a chart's text is its note; empty text removes the note
        with_note(c, text)
        return
    text = str(text or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise ValueError("text must hold 1 to %d characters" % MAX_TEXT)
    c["text"] = text


def move_cell(rep, cell, row, position=None):
    """Move cell into row (1-based) at position (1-based, default last). Row n+1 means a new row at the end; a row
    with three cells refuses a fourth."""
    ri, ci = locate(rep, cell)
    c = rep["rows"][ri]["cells"].pop(ci)
    rows = rep["rows"]  # an emptied row stays until the end, so row numbers are the ones the caller saw
    if not isinstance(row, int) or not 1 <= row <= len(rows) + 1:
        rows[ri]["cells"].insert(ci, c)
        raise ValueError("row must be 1 to %d" % (len(rows) + 1))
    if row == len(rows) + 1:
        rows.append({"cells": [c]})
    else:
        target = rows[row - 1]["cells"]
        if len(target) >= MAX_CELLS:
            rows[ri]["cells"].insert(ci, c)
            raise ValueError("row %d already holds %d cells" % (row, MAX_CELLS))
        pos = len(target) if position is None else max(0, min(int(position) - 1, len(target)))
        target.insert(pos, c)
    rep["rows"] = [r for r in rows if r["cells"]]


def replace_chart(rep, cell, args):
    """Point a chart cell at new render_visual args. Returns the new visual id; the old chart is dropped."""
    ri, ci = locate(rep, cell)
    c = rep["rows"][ri]["cells"][ci]
    if c["type"] != "chart":
        raise ValueError("cell %s is text; update_text changes text" % cell)
    if not isinstance(args, dict) or not args:
        raise ValueError("chart needs the render_visual arguments")
    vid = unique_visual_id(rep, args.get("title"))
    rep["visuals"][vid] = {"args": args}
    c["visual"] = vid
    drop_unshown(rep)
    return vid


def chart_title(rep, vid):
    args = rep["visuals"].get(vid, {}).get("args", {})
    return str(args.get("title") or vid)


def summary(rep):
    """What the model sees: the title and, per row, the chart titles and the start of each text. Never the data."""
    lines = ["Report: %s (%d rows)%s" % (rep["title"], len(rep["rows"]),
                                        "" if rep.get("show_notes", True) else ", chart notes hidden")]
    for i, row in enumerate(rep["rows"], 1):
        parts = []
        for c in row["cells"]:
            if c["type"] == "chart":
                note = " ".join(c.get("note", "").split())
                parts.append("[%s] chart %r, note %s" % (c["id"], chart_title(rep, c["visual"]),
                                                        repr(note[:60] + ("..." if len(note) > 60 else "")) if note else "MISSING"))
            else:
                t = " ".join(c["text"].split())
                parts.append("[%s] text %r" % (c["id"], t[:80] + ("..." if len(t) > 80 else "")))
        lines.append("Row %d: %s" % (i, "; ".join(parts)))
    return "\n".join(lines)


def to_story(rep):
    """The report in the story.json format, so story-pdf and story-artifact build it from its folder: the title
    as the heading and each row as a story row, its charts and texts side by side. A report names no data source."""
    notes = rep.get("show_notes", True)  # off: the notes stay in the report, the exports leave them out
    rows = [{"cells": [dict({"visual": c["visual"]}, **({"note": c["note"]} if notes and c.get("note") else {}))
                       if c["type"] == "chart" else {"text": c["text"]} for c in row["cells"]]}
            for row in rep["rows"] if row["cells"]]
    shown = {c["visual"] for row in rows for c in row["cells"] if "visual" in c}
    return {"title": rep["title"], "sections": [], "rows": rows,
            "visuals": {vid: {"args": v["args"]} for vid, v in rep["visuals"].items() if vid in shown}}


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
