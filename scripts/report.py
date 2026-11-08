#!/usr/bin/env python3
"""report.py: a Zebra BI report, a page of rows with up to three cells each, a cell holding a chart or text.

Library for report_server.py. Standard library only.

A report is one JSON file, <reports>/<slug>/report.zbreport, on the user's machine; nothing about it is kept
on a Zebra BI server. Its charts are drawn by render_visual on the Zebra BI MCP, as story.py draws a story's,
and each result is cached in visuals/<id>.json next to the file. The format:

  {"format": "zebra-bi-report", "version": 1, "title": "...", "rev": 3,
   "key_message": "Sales are **12%** ahead of plan.", "kpis": [{"value": "1.2M", "label": "Sales, ΔPL +12%"}],
   "layout": "one-pager", "kept": [],
   "rows": [{"cells": [{"id": "c1", "type": "chart", "visual": "sales-by-month", "message": "...", "note": "...",
                        "why": "...", "recipe": {"source": "...", "query": "...", "code": "...", "period": "..."},
                        "kept": []},
                       {"id": "c2", "type": "text", "text": "Sales are **12%** ahead of plan."}]}],
   "visuals": {"sales-by-month": {"args": { ...render_visual arguments... }}}}

A chart cell's `message` sits above the chart and its `note` (the evidence) under it; `why` says why this chart
type; `recipe` is how its rows were made, so a later rebuild can make them again. `kept` lists numbers the user
chose to keep although no chart holds them (numbercheck.py traces every other number in the text).

`visuals` is the same map story.json has, so the story exports can read a report. `rev` goes up by one on every
save; the builder sends the rev it last saw, and a save against an older rev is refused so two editors do not
overwrite each other.
"""
import contextlib, datetime as dt, json, os, re, shutil, stat, sys, time

try:
    import fcntl
except ImportError:  # Windows: no flock; a single writer is the norm there
    fcntl = None

import numbercheck as N
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
MAX_MESSAGE = 300  # the message above a chart, and its why
MAX_KEY = 600  # the key message
MAX_KPIS = 4
MAX_KEPT = 50
LAYOUTS = S.LAYOUTS
RECIPE_KEYS = ("source", "query", "code", "period")


class Conflict(Exception):
    """The report changed since the editor last read it."""


class Refused(ValueError):
    """Zebra BI answered render_visual with an error result; text is its sentence, which says why."""

    def __init__(self, text):
        self.text = text
        super().__init__("Zebra BI refused the chart: %s" % text)


PLATFORM = sys.platform
LEGACY = "~/Documents/Zebra BI Reports"
_legacy_checked = False


def root():
    """The folder that holds every report: $ZEBRA_BI_REPORTS, else ~/Zebra BI Reports on macOS (privacy blocks
    ~/Documents for a server Claude Desktop starts) and ~/Documents/Zebra BI Reports elsewhere."""
    if os.environ.get("ZEBRA_BI_REPORTS"):
        return os.path.abspath(os.path.expanduser(os.environ["ZEBRA_BI_REPORTS"]))
    path = os.path.abspath(os.path.expanduser("~/Zebra BI Reports" if PLATFORM == "darwin" else LEGACY))
    move_legacy(path)
    return path


def move_legacy(path):
    """Once per process, move reports from the old ~/Documents folder to `path`, if this process can reach them
    and `path` holds no report yet. A process privacy keeps out of ~/Documents sees no old folder and moves nothing."""
    global _legacy_checked
    old = os.path.abspath(os.path.expanduser(LEGACY))
    if _legacy_checked or old == path:
        return
    _legacy_checked = True
    try:
        if not os.path.isdir(old):
            return
        if not os.path.exists(path):
            os.rename(old, path)
            return
        if any(not name.startswith(".") for name in os.listdir(path)):
            return  # the new folder is in use: leave the old one for the user
        for name in os.listdir(old):
            if not os.path.exists(os.path.join(path, name)):
                os.rename(os.path.join(old, name), os.path.join(path, name))
        with contextlib.suppress(OSError):
            os.rmdir(old)
    except OSError:
        pass  # another volume or no permission: the old reports stay where they are


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
    if not _text_ok(rep.get("title"), MAX_TITLE):
        raise ValueError("title must be a string of at most %d characters" % MAX_TITLE)
    if not isinstance(rep.get("show_notes", True), bool):
        raise ValueError("show_notes must be true or false")
    if not _text_ok(rep.get("key_message", ""), MAX_KEY):
        raise ValueError("key_message must be a string of at most %d characters" % MAX_KEY)
    kpis = rep.get("kpis", [])
    if not isinstance(kpis, list) or len(kpis) > MAX_KPIS or not all(_kpi_ok(k) for k in kpis):
        raise ValueError("kpis must be at most %d tiles of value (40 characters) and label (120)" % MAX_KPIS)
    if rep.get("layout") is not None and rep["layout"] not in LAYOUTS:
        raise ValueError("layout must be one of %s" % ", ".join(LAYOUTS))
    if not _kept_ok(rep.get("kept", [])):
        raise ValueError("kept must be a list of at most %d numbers" % MAX_KEPT)
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
                if not (_text_ok(cell.get("message", ""), MAX_MESSAGE) and _text_ok(cell.get("why", ""), MAX_MESSAGE)):
                    raise ValueError("cell %s: message and why must be strings of at most %d characters" % (cid, MAX_MESSAGE))
                recipe = cell.get("recipe", {})
                if not isinstance(recipe, dict) or any(k not in RECIPE_KEYS or not _text_ok(v, MAX_TEXT) for k, v in recipe.items()):
                    raise ValueError("cell %s: recipe holds only %s, as strings" % (cid, ", ".join(RECIPE_KEYS)))
            elif cell.get("type") == "text":
                if not isinstance(cell.get("text"), str) or len(cell["text"]) > MAX_TEXT:
                    raise ValueError("cell %s: text must be a string of at most %d characters" % (cid, MAX_TEXT))
                after = cell.get("after")
                if after is not None and not (isinstance(after, str) and CELL_ID.fullmatch(after)):
                    raise ValueError("cell %s: after names the chart cell the text belongs to, like c1" % cid)
            else:
                raise ValueError("cell %s: type must be chart or text" % cid)
            if not _kept_ok(cell.get("kept", [])):
                raise ValueError("cell %s: kept must be a list of at most %d numbers" % (cid, MAX_KEPT))
    return rep


def _text_ok(v, limit):
    return isinstance(v, str) and len(v) <= limit


def _kpi_ok(k):
    return isinstance(k, dict) and set(k) <= {"value", "label"} and _text_ok(k.get("value", ""), 40) \
        and _text_ok(k.get("label", ""), 120)


def _kept_ok(v):
    return isinstance(v, list) and len(v) <= MAX_KEPT and all(isinstance(x, str) and len(x) <= 40 for x in v)


def _set_text(obj, key, text, limit, what):
    """Set obj[key] to the stripped text, or drop the key when the text is empty."""
    text = str(text or "").strip()
    if len(text) > limit:
        raise ValueError("%s must hold at most %d characters" % (what, limit))
    obj.pop(key, None)
    if text:
        obj[key] = text


MAX_FILE = 5 * 1024 * 1024  # bytes: a report file larger than this is refused unread


def load(path):
    """The report at path, validated. Anything that is not a valid report (too large, not a regular file, nested
    too deep, the wrong shape) raises ValueError, so one broken file never takes a list of reports down with it."""
    data = read_regular(path, MAX_FILE + 1)
    if data is None:
        if not os.path.lexists(path):
            raise FileNotFoundError(path)
        raise ValueError("%s is not a regular report file" % os.path.basename(path))
    if len(data) > MAX_FILE:
        raise ValueError("%s is larger than %d MB" % (os.path.basename(path), MAX_FILE // (1024 * 1024)))
    try:
        rep = json.loads(data.decode("utf-8"))
        if not isinstance(rep, dict):
            raise ValueError("a report is a JSON object")
        validate(rep)
    except (RecursionError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as e:
        raise ValueError("not a valid report: %s" % type(e).__name__)
    rep.setdefault("rev", 0)
    return rep


@contextlib.contextmanager
def locked(path):
    """Hold the report's lock (an flock on .lock in its folder) while it is read, changed and written. The chat and
    the builder run separate servers; under the lock neither writes over a change the other made in between."""
    if fcntl is None:
        yield
        return
    fd = os.open(os.path.join(os.path.dirname(path), ".lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def change(path, fn):
    """Load the report, apply fn(rep), save it, all under its lock. Returns (report, what fn returned)."""
    with locked(path):
        rep = load(path)
        out = fn(rep)
        save(path, rep)
    return rep, out


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
    rep, _ = change(path_for(slug), lambda rep: rep.update(title=clean_title(title)))
    return rep["title"]


MAX_CACHED = 20 * 1024 * 1024  # bytes: a cached chart result larger than this is not copied


def read_regular(path, limit=MAX_CACHED):
    """The bytes of path when it is a regular file of at most limit bytes, never through a symbolic link; else None.
    O_NONBLOCK keeps a pipe in its place from holding the open until a writer comes; fstat then refuses it."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        return f.read(limit + 1)[:limit]


def duplicate(slug, title=None):
    """A copy of the report, its text and its cached charts, as a new report. Returns the new slug. Only the report's
    own chart results (visuals/<id>.json, regular files) are copied: a shared folder's links and extra files are not."""
    rep = load(path_for(slug))
    title = clean_title(title or "%s (copy)" % rep["title"])
    new_slug = unique_slug(title)
    src, dst = os.path.join(root(), slug), os.path.join(root(), new_slug)
    os.makedirs(dst)
    sdir = os.path.join(src, "visuals")
    if os.path.isdir(sdir) and not os.path.islink(sdir):
        for vid in rep["visuals"]:
            data = read_regular(os.path.join(sdir, vid + ".json"))
            if data is not None:
                os.makedirs(os.path.join(dst, "visuals"), exist_ok=True)
                S.write_atomic(os.path.join(dst, "visuals", vid + ".json"), data)
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
    # write_atomic replaces whatever sits at that name, a link from a shared folder too, and never writes through it
    S.write_atomic(os.path.join(root(), DELETED, entry, "deleted.json"), json.dumps({"deleted_at": now(), "report": slug}))
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


def add_chart(rep, args, row=None, note=None, message=None, why=None, recipe=None):
    """Add a chart cell for render_visual args: its message above, its evidence (note) under, why this chart type,
    and the recipe its rows came from. Returns (visual id, row number)."""
    if not isinstance(args, dict) or not args:
        raise ValueError("chart needs the render_visual arguments")
    cell = with_note({"id": next_cell_id(rep), "type": "chart"}, note)
    _set_text(cell, "message", message, MAX_MESSAGE, "the message")
    _set_text(cell, "why", why, MAX_MESSAGE, "why")
    recipe = {k: str(v) for k, v in (recipe or {}).items() if k in RECIPE_KEYS and v not in (None, "")}
    if any(len(v) > MAX_TEXT for v in recipe.values()):
        raise ValueError("a recipe part must hold at most %d characters" % MAX_TEXT)
    if recipe:
        cell["recipe"] = recipe
    vid = unique_visual_id(rep, args.get("title"))
    rep["visuals"][vid] = {"args": args}
    cell["visual"] = vid
    return vid, place(rep, cell, row)


def set_key_message(rep, text):
    """The report's key message, shown on top; empty text removes it."""
    _set_text(rep, "key_message", text, MAX_KEY, "the key message")


def set_kpis(rep, kpis):
    """Up to four KPI tiles, each a short value and a one-line label; an empty list removes them."""
    if not isinstance(kpis, list) or len(kpis) > MAX_KPIS:
        raise ValueError("at most %d KPI tiles" % MAX_KPIS)
    tiles = [{"value": str(k.get("value") or "").strip(), "label": str(k.get("label") or "").strip()}
             for k in kpis if isinstance(k, dict)]
    tiles = [t for t in tiles if t["value"] or t["label"]]  # a tile emptied in the builder is a tile removed
    if not all(_kpi_ok(t) and t["value"] for t in tiles):
        raise ValueError("each KPI tile needs a value (at most 40 characters) and a label (at most 120)")
    rep.pop("kpis", None)
    if tiles:
        rep["kpis"] = tiles


def add_text(rep, text, row=None):
    text = str(text or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise ValueError("text must hold 1 to %d characters" % MAX_TEXT)
    return place(rep, {"id": next_cell_id(rep), "type": "text", "text": text}, row)


def apply_layout(rep, title, rows, rev, show_notes=None, key_message=None, kpis=None, kept=None):
    """The builder's edit: a new title and the rows as it shows them. Cells keep their ids; a chart cell may only
    name a chart the report holds, so the editor can move and drop charts but never invent one. Charts no cell
    shows any more are dropped from visuals."""
    if rev != rep.get("rev"):
        raise Conflict("the report changed since it was opened (rev %s, now %s)" % (rev, rep.get("rev")))
    old = {c["id"]: c for r in rep["rows"] for c in r["cells"]}
    new_rows = []
    for row in rows if isinstance(rows, list) else []:
        cells = [c for c in (row.get("cells") if isinstance(row, dict) else None) or [] if isinstance(c, dict)]
        if not cells:
            continue  # an emptied row disappears
        out_cells = []
        for c in cells:
            was = old.get(c.get("id"), {})
            # a field the save leaves out (an older builder) keeps its stored value; "" removes it
            have = lambda key: c[key] if key in c else was.get(key)
            if c.get("type") == "chart":
                cell = with_note({"id": c.get("id"), "type": "chart", "visual": c.get("visual")}, have("note"))
                _set_text(cell, "message", have("message"), MAX_MESSAGE, "the message")
                _set_text(cell, "why", have("why"), MAX_MESSAGE, "why")
                if was.get("recipe") and was.get("visual") == cell["visual"]:
                    cell["recipe"] = was["recipe"]  # the builder never edits a recipe
            else:
                cell = {"id": c.get("id"), "type": "text", "text": str(c.get("text") or "")}
                if have("after"):
                    cell["after"] = have("after")
            if have("kept"):
                cell["kept"] = list(have("kept"))
            out_cells.append(cell)
        new_rows.append({"cells": out_cells})
    out = dict(rep, title=clean_title(title if title is not None else rep["title"]), rows=new_rows)
    if show_notes is not None:
        out["show_notes"] = bool(show_notes)
    if key_message is not None:
        set_key_message(out, key_message)
    if kpis is not None:
        set_kpis(out, kpis)
    if kept is not None:
        out.pop("kept", None)
        if kept:
            out["kept"] = list(kept)
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


def update_text(rep, cell, text, field=None):
    """Change a text cell, or a chart cell's note (the default), message or why; empty text removes the latter."""
    ri, ci = locate(rep, cell)
    c = rep["rows"][ri]["cells"][ci]
    if c["type"] == "chart":
        if field in (None, "note"):
            with_note(c, text)
        elif field in ("message", "why"):
            _set_text(c, field, text, MAX_MESSAGE, "the " + field)
        else:
            raise ValueError("a chart cell's field is message, note or why")
        return
    if field not in (None, "text"):
        raise ValueError("cell %s is text; it has no %s" % (cell, field))
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


def even_rows(n, most=MAX_CELLS):
    """How many charts go in each row so n charts take as few rows as `most` per row allows, filled evenly and
    fuller rows first: 4 is 2 + 2, 5 is 3 + 2, 7 is 3 + 2 + 2."""
    if n <= 0:
        return []
    count = -(-n // most)
    base, extra = divmod(n, count)
    return [base + 1] * extra + [base] * (count - extra)


def reading(rep):
    """(text cells before the first chart, [(chart cell, [the text cells that belong to it])]) in reading order,
    row by row, left to right. A text cell belongs to the chart its `after` names (arrange sets it) while it sits
    under that chart's row, else to the chart before it."""
    lead, charts = [], []
    above = {}  # the charts of the last row that held one, so a text under a row of charts finds its own
    for row in rep["rows"]:
        if any(c["type"] == "chart" for c in row["cells"]):
            above = {}
        for c in row["cells"]:
            if c["type"] == "chart":
                charts.append((c, []))
                above[c["id"]] = charts[-1]
            elif charts:
                (above.get(c.get("after")) or charts[-1])[1].append(c)
            else:
                lead.append(c)
    return lead, charts


def arrange(rep, layout):
    """Lay the same cells out as a named layout. A text cell keeps to the chart before it, in its own row right
    after that chart's row; text before the first chart stays on top.
    one-pager: largest variance first, rows of up to three filled evenly, notes hidden; the PDF prints it on one
    landscape page. variance-walk: one chart per row in the order the charts were built (the order the analysis
    reads), notes shown. board-pack: one chart per row, largest variance first, notes shown; the PDF puts the key
    message and KPI tiles on a cover and each chart on its own page."""
    if layout not in LAYOUTS:
        raise ValueError("layout must be one of %s" % ", ".join(LAYOUTS))
    lead, charts = reading(rep)
    for t in lead:
        t.pop("after", None)
    if layout == "variance-walk":
        charts.sort(key=lambda pair: int(pair[0]["id"][1:]))  # cell ids count up as cells are added
    else:
        size = lambda c: N.contribution(rep["visuals"][c["visual"]]["args"])
        charts.sort(key=lambda pair: -size(pair[0]))  # stable for equal sizes
    sizes = even_rows(len(charts)) if layout == "one-pager" else [1] * len(charts)
    groups, at = [], 0
    for n in sizes:
        groups.append(charts[at:at + n])
        at += n
    rows = [{"cells": [t]} for t in lead]
    for group in groups:
        rows.append({"cells": [c for c, _ in group]})
        for chart, texts in group:
            for t in texts:
                t["after"] = chart["id"]
                rows.append({"cells": [t]})
    rep["rows"] = rows
    rep["layout"] = layout
    rep["show_notes"] = layout != "one-pager"


def chart_title(rep, vid):
    args = rep["visuals"].get(vid, {}).get("args", {})
    return str(args.get("title") or vid)


def checks(rep):
    """Numbers in the report's text that no chart holds: {"cells": {cell id: {field: [raw]}}, "key_message": [raw],
    "kpis": [[raw] per tile]}, only where there are some. A chart's message and note are checked against that
    chart; the key message, KPI tiles and text cells against every chart in the report."""
    every = [v["args"] for v in rep["visuals"].values()]
    cells = {}
    for row in rep["rows"]:
        for c in row["cells"]:
            if c["type"] == "chart":
                own = [rep["visuals"][c["visual"]]["args"]]
                found = {f: N.untraced(c.get(f, ""), own, c.get("kept", ())) for f in ("message", "note")}
            else:
                found = {"text": N.untraced(c["text"], every, c.get("kept", ()))}
            found = {f: v for f, v in found.items() if v}
            if found:
                cells[c["id"]] = found
    out = {"cells": cells} if cells else {}
    key = N.untraced(rep.get("key_message", ""), every, rep.get("kept", ()))
    if key:
        out["key_message"] = key
    kpis = [N.untraced("%s %s" % (k["value"], k["label"]), every, rep.get("kept", ())) for k in rep.get("kpis", [])]
    if any(kpis):
        out["kpis"] = kpis
    return out


def flags(rep):
    """One line per untraced number in what the exports show, for a person: "c3 note: 99.0k". Empty when every
    number is traced or kept. Hidden notes are left out of the exports, so they do not count here."""
    found = checks(rep)
    shown = lambda field: field != "note" or rep.get("show_notes", True)
    lines = ["%s %s: %s" % (cid, field, raw) for cid, fields in found.get("cells", {}).items()
             for field, raws in fields.items() if shown(field) for raw in raws]
    lines += ["key message: %s" % raw for raw in found.get("key_message", [])]
    lines += ["KPI %d: %s" % (i, raw) for i, raws in enumerate(found.get("kpis", []), 1) for raw in raws]
    return lines


def summary(rep):
    """What the model sees: the title, key message and tiles and, per row, the chart titles with their message and
    note and the start of each text, plus any number no chart holds. Never the data."""
    short = lambda t, n: (lambda t: repr(t[:n] + ("..." if len(t) > n else "")) if t else "MISSING")(" ".join((t or "").split()))
    found = checks(rep)
    lines = ["Report: %s (%d rows)%s" % (rep["title"], len(rep["rows"]),
                                        "" if rep.get("show_notes", True) else ", chart notes hidden"),
             "Key message: %s" % short(rep.get("key_message"), 120)
             + ("; unchecked: %s" % ", ".join(found["key_message"]) if found.get("key_message") else "")]
    if rep.get("kpis"):
        lines.append("KPI tiles: " + "; ".join("%s %s" % (k["value"], k["label"]) for k in rep["kpis"])
                     + ("; unchecked: %s" % ", ".join(r for t in found.get("kpis", []) for r in t) if found.get("kpis") else ""))
    for i, row in enumerate(rep["rows"], 1):
        parts = []
        for c in row["cells"]:
            if c["type"] == "chart":
                part = "[%s] chart %r, message %s, note %s" % (c["id"], chart_title(rep, c["visual"]),
                                                               short(c.get("message"), 60), short(c.get("note"), 60))
            else:
                part = "[%s] text %s" % (c["id"], short(c["text"], 80))
            raws = [r for v in found.get("cells", {}).get(c["id"], {}).values() for r in v]
            parts.append(part + (", unchecked: %s" % ", ".join(raws) if raws else ""))
        lines.append("Row %d: %s" % (i, "; ".join(parts)))
    return "\n".join(lines)


def to_story(rep):
    """The report in the story.json format, so story-pdf and story-artifact build it from its folder: the title
    as the heading and each row as a story row, its charts and texts side by side. A report names no data source."""
    found = flags(rep)
    if found:
        raise ValueError("these numbers are not in their charts: %s" % "; ".join(found))
    notes = rep.get("show_notes", True)  # off: the notes stay in the report, the exports leave them out

    def chart(c):
        out = {"visual": c["visual"]}
        if c.get("message"):
            out["message"] = c["message"]
        if notes and c.get("note"):
            out["note"] = c["note"]
        return out
    rows = [{"cells": [chart(c) if c["type"] == "chart" else {"text": c["text"]} for c in row["cells"]
                       if c["type"] == "chart" or c["text"].strip()]}  # an empty text cell is the builder's, not the reader's
            for row in rep["rows"]]
    rows = [r for r in rows if r["cells"]]
    shown = {c["visual"] for row in rows for c in row["cells"] if "visual" in c}
    story = {"title": rep["title"], "sections": [], "rows": rows,
             "visuals": {vid: {"args": v["args"]} for vid, v in rep["visuals"].items() if vid in shown}}
    if rep.get("key_message"):  # the key message heads the page, with the report's title as the label above it
        story.update(kicker=rep["title"], title=rep["key_message"].replace("**", ""))  # a story title is plain text
    if rep.get("kpis"):
        story["kpis"] = rep["kpis"]
    if rep.get("layout"):  # the PDF prints a one-pager on one landscape page and a board pack a chart per page
        story["layout"] = rep["layout"]
    return story


def to_deck(rep):
    """The report as the deck's story (office.py): the cover as to_story makes it, then one section per chart in
    reading order, with its message (else its chart's title) as the heading, its note while notes are shown, and
    the text cells that belong to it. Text before the first chart is a section of its own under the report's
    title. Raises ValueError while an exported number is untraced, as to_story does."""
    story = to_story(rep)
    del story["rows"]
    notes = rep.get("show_notes", True)
    lead, charts = reading(rep)
    said = lambda cells: [t["text"] for t in cells if t["text"].strip()]  # an empty text cell is left out
    sections = [{"heading": rep["title"], "body": "\n\n".join(said(lead))}] if said(lead) else []
    for c, texts in charts:
        sections.append({"heading": (c.get("message") or chart_title(rep, c["visual"])).replace("**", ""),
                         "visual": c["visual"], "note": c.get("note", "") if notes else "",
                         "texts": said(texts), "kind": "report-chart"})
    story["sections"] = sections
    return story


def cached(report_dir, vid, args):
    """The cached render_visual result for one chart when its args are unchanged and ENDPOINT drew it, else None."""
    out = os.path.join(report_dir, "visuals", vid + ".json")
    if os.path.islink(os.path.dirname(out)):
        return None
    return S.cached_result(out, S.args_hash(args))


def render(report_dir, vid, args):
    """The render_visual result for one chart, from visuals/<vid>.json when the args are unchanged, else drawn by
    the Zebra BI MCP and cached. Raises S.SignIn, S.Unavailable or Refused; never prints or exits."""
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
        raise Refused(S.clean(" ".join(str(c.get("text", "")) for c in content)))
    sc = res.get("structuredContent")
    if not (isinstance(sc, dict) and isinstance(sc.get("visual"), dict) and sc["visual"]):
        raise S.Unavailable("render_visual returned no chart for %r" % vid)
    S.write_atomic(out, json.dumps({"args_hash": h, "endpoint": S.ENDPOINT, "result": res}))
    return res


def server_note(res):
    """The server's own text for a drawn chart (it lists every adjustment it made), one clean line."""
    content = res.get("content") if isinstance(res.get("content"), list) else []
    return S.clean(" ".join(str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("type") == "text"), 600)
