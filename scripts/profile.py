#!/usr/bin/env python3
"""profile.py <file> [--sheet-index N] [--name SOURCE_NAME]
       profile.py <file> [--sheet NAME] [--name SOURCE_NAME]

Profiles a local CSV or XLSX file with the standard library only and prints a
JSON source manifest (the `file` kind of .zebra/sources/<name>.json) to stdout.

What it settles so nobody asks twice: encoding, delimiter, header row, sheet,
date order (day-first or month-first), column roles (date / measure / category),
row count, sha256 and mtime. Run it with `python3 -I`.

--sheet-index N picks the Nth sheet (1-based) of the printed `sheets` list; the
skills always use it, because a sheet name comes from the file and does not
belong on a command line. --sheet NAME is for a person typing the command. An
index out of range or an unmatched name is an error. SOURCE_NAME is lowercase
letters, digits and dashes; without it the file name gives one.
"""
import csv, datetime as dt, hashlib, io, json, os, re, sys, zipfile
import xml.etree.ElementTree as ET

SAMPLE = 5000
# Limits for a file the user points at. A story profiles aggregated or exported data, so anything past these is
# a bigger job than this profiler (or a crafted workbook): it stops with one line instead of filling memory.
MAX_FILE = 100 * 1024 * 1024    # bytes on disk, CSV or XLSX
MAX_PART = 100 * 1024 * 1024    # unpacked bytes of one workbook part
MAX_RATIO = 100                 # unpacked / packed size of a part over 1 MB; real sheets stay far below
MAX_ROWS = 1000000
ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
DATE_RE = re.compile(r"^\s*(\d{1,4})[/.-](\d{1,2})[/.-](\d{1,4})\s*$")
NUM_RE = re.compile(r"^\s*-?[\d,]*\.?\d+([eE][-+]?\d+)?\s*$")
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH_WORDS = set(MONTHS) | {"january", "february", "march", "april", "june", "july", "august",
                              "september", "october", "november", "december", "sept"}
# Names of amounts. Whole numbers in such a column are euros, not keys.
MEASURE_RE = re.compile(r"actual|plan|budget|forecast|value|amount|revenue|cost|sales|total|price|profit|margin|income|expense|quantity")
# IBCS scenario codes count only as a whole word: "AC", "PY Revenue".
MEASURE_TOKENS = {"ac", "pl", "bu", "fc", "py"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_csv(path):
    raw = open(path, "rb").read()
    enc = None
    for e in ENCODINGS:
        try:
            text = raw.decode(e)
            enc = e
            break
        except UnicodeDecodeError:
            continue
    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    rows = []
    for r in csv.reader(io.StringIO(text), delimiter=delim):
        if any(c.strip() for c in r):
            rows.append(r)
            if len(rows) > MAX_ROWS:
                sys.exit("refusing %s: more than %d rows; aggregate the data or export the range you need first" % (path, MAX_ROWS))
    return {"encoding": enc, "delimiter": delim}, rows


DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


def _checked(z, name):
    """Size and declaration checks on one part of an untrusted workbook, before anything parses it.
    XLSX parts never need a DOCTYPE, so a part that declares one is refused: that rules out entity
    expansion and external entities without a dependency such as defusedxml."""
    info = z.getinfo(name)
    if info.file_size > MAX_PART:
        sys.exit("refusing %s: it unpacks to %d MB, over the %d MB this profiler reads; export the range you need as CSV"
                 % (name, info.file_size >> 20, MAX_PART >> 20))
    if info.file_size > 1 << 20 and info.file_size > MAX_RATIO * max(info.compress_size, 1):
        sys.exit("refusing %s: it unpacks to %d times its packed size, which a real workbook does not"
                 % (name, info.file_size // max(info.compress_size, 1)))
    tail = b""
    with z.open(name) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            if DECLARATION.search(tail + chunk):
                sys.exit("refusing %s: workbook part declares a DOCTYPE or ENTITY" % name)
            tail = chunk[-16:]


def _xml(z, name):
    """Parse one small part (workbook, relationships, styles) after the checks."""
    _checked(z, name)
    return ET.fromstring(z.read(name))


def _stream(z, name, tag):
    """Yield each `tag` element of a large part (a sheet, the shared strings) after the checks, dropping it
    once the caller is done, so memory holds one row at a time instead of the whole parsed part."""
    _checked(z, name)
    with z.open(name) as f:
        parent = None
        for event, el in ET.iterparse(f, events=("start", "end")):
            if event == "start":
                if parent is None and el.tag != tag:
                    parent = el if el.tag.endswith(("}sheetData", "}sst")) else None
                continue
            if el.tag == tag:
                yield el
                el.clear()
                if parent is not None and len(parent) and parent[-1] is el:
                    del parent[-1]


def _col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


# Built-in number formats that show a date (14-17, 22, and the East Asian date formats); 18-21 and 45-47 are times.
DATE_FMT_IDS = set(range(14, 18)) | {22} | set(range(27, 37)) | set(range(50, 59))


def _part(target):
    p = target.lstrip("/")
    return p if p.startswith("xl/") else "xl/" + p


def _date_styles(z, ns, rels):
    """Indexes of the cell styles that show a date. Excel stores a date as a day count with such a style;
    without reading styles.xml a date column looks like a measure. Malformed styles mean no dates, never a crash."""
    t = next((r.get("Target") for r in rels if (r.get("Type") or "").endswith("/styles")), "styles.xml")
    name = _part(t)
    if name not in z.namelist():
        return set()
    st = _xml(z, name)
    custom = {}
    for f in st.iter("{%s}numFmt" % ns["m"]):
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|[\\_*].', "", f.get("formatCode") or "").lower()
        custom[f.get("numFmtId")] = bool(re.search(r"[dy]", code))
    out = set()
    xfs = st.find("m:cellXfs", ns)
    for i, xf in enumerate(xfs if xfs is not None else []):
        fid = xf.get("numFmtId", "0")
        if custom.get(fid) or (fid.isdigit() and int(fid) in DATE_FMT_IDS):
            out.add(str(i))
    return out


def _serial_to_iso(text, base):
    try:
        days = float(text)
    except (TypeError, ValueError):
        return None
    if not 0 <= days < 2958466:  # Excel's last day is 9999-12-31
        return None
    d = base + dt.timedelta(days=days)
    return d.strftime("%Y-%m-%dT%H:%M:%S") if days != int(days) else d.strftime("%Y-%m-%d")


def _pick_sheet(names, sheet, index):
    """The position of the chosen sheet in `names`: by 1-based index, by exact name, else the first.
    A wrong index or name exits with the list of sheets, so nobody profiles the wrong sheet unawares."""
    listing = "; sheets: " + ", ".join("%d %s" % (i, json.dumps(n, ensure_ascii=False)) for i, n in enumerate(names, 1))
    if index is not None:
        if not 1 <= index <= len(names):
            sys.exit("--sheet-index %d is out of range%s" % (index, listing))
        return index - 1
    if sheet is not None:
        if sheet not in names:
            sys.exit("no sheet named %s%s" % (json.dumps(sheet, ensure_ascii=False), listing))
        return names.index(sheet)
    return 0


def read_xlsx(path, sheet=None, index=None):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    z = zipfile.ZipFile(path)
    wb = _xml(z, "xl/workbook.xml")
    rels = _xml(z, "xl/_rels/workbook.xml.rels")
    target = {r.get("Id"): r.get("Target") for r in rels}
    date_xf = _date_styles(z, ns, rels)
    pr = wb.find("m:workbookPr", ns)
    base = dt.datetime(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") else dt.datetime(1899, 12, 30)
    listed = wb.find("m:sheets", ns)
    sheets = [(s.get("name"), target[s.get("{%s}id" % ns["r"])]) for s in (listed if listed is not None else [])]
    names = [s[0] for s in sheets]
    if not sheets:
        sys.exit("no sheets in " + path)
    chosen = sheets[_pick_sheet(names, sheet, index)]
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in _stream(z, "xl/sharedStrings.xml", "{%s}si" % ns["m"]):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    p = _part(chosen[1])
    if p not in z.namelist():
        sys.exit("refusing %s: the workbook lists sheet %s but has no part for it" % (path, json.dumps(chosen[0], ensure_ascii=False)))
    rows = []
    for row in _stream(z, p, "{%s}row" % ns["m"]):
        vals, col = {}, -1
        for c in row.findall("m:c", ns):
            v = c.find("m:v", ns)
            t = c.get("t")
            if t == "s" and v is not None:
                try:
                    val = shared[int(v.text)]
                except (TypeError, ValueError, IndexError):
                    sys.exit("refusing %s: a cell refers to shared string %r, which the workbook does not have" % (p, v.text))
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
            else:
                val = v.text if v is not None else ""
                if t in (None, "n") and c.get("s") in date_xf:
                    val = _serial_to_iso(val, base) or val
            ref = c.get("r") or ""
            col = _col_index(ref) if re.match(r"[A-Z]+", ref) else col + 1  # a cell without r= follows the one before
            vals[col] = val
        if vals:
            rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
            if len(rows) > MAX_ROWS:
                sys.exit("refusing %s: more than %d rows; aggregate the data or export the range you need first" % (path, MAX_ROWS))
    return {"sheet": chosen[0], "sheets": names}, rows


def date_order(values):
    """Return 'day-first', 'month-first', 'year-first' or 'ambiguous' for d/m/y strings."""
    a_over_12 = b_over_12 = year_first = 0
    for v in values:
        m = DATE_RE.match(v)
        if not m:
            continue
        a, b, c = m.groups()
        if len(a) == 4:
            year_first += 1
        elif int(a) > 12:
            a_over_12 += 1
        elif int(b) > 12:
            b_over_12 += 1
    if year_first:
        return "year-first"
    if a_over_12 and not b_over_12:
        return "day-first"
    if b_over_12 and not a_over_12:
        return "month-first"
    return "ambiguous"


def named_id(name):
    n = name.lower()
    return n.endswith(("id", "_id", "code")) or "postal" in n or "zip" in n


def looks_like_key(name, nums):
    """An unnamed key: many whole, non-negative, almost all distinct numbers. A P&L in whole euros has
    the same shape, so a column named like an amount (Actual, Revenue, PY, ...) is never a key."""
    n = name.lower()
    if MEASURE_RE.search(n) or set(re.split(r"[^a-z0-9]+", n)) & MEASURE_TOKENS:
        return False
    return (len(nums) > 50 and all(x >= 0 and x == int(x) for x in nums)
            and len(set(nums)) > 0.9 * len(nums))


def classify(name, values):
    vals = [v for v in values if v not in ("", None)]
    if not vals:
        return {"name": name, "role": "empty"}
    s = [str(v) for v in vals[:SAMPLE]]
    if sum(bool(DATE_RE.match(v)) for v in s) > 0.9 * len(s):
        return {"name": name, "role": "date", "date_order": date_order(s)}
    if all(v.strip().lower()[:3] in MONTHS and v.strip().lower() in MONTH_WORDS for v in s):
        return {"name": name, "role": "period", "period": "month-name",
                "note": "month names without a year; map to ISO YYYY-MM for a time axis once the year is known"}
    if all(re.fullmatch(r"\s*(19|20)\d\d\s*", v) for v in s):
        return {"name": name, "role": "period", "period": "year"}
    if sum(bool(NUM_RE.match(v)) for v in s) > 0.95 * len(s):
        nums = [float(str(v).replace(",", "")) for v in vals if NUM_RE.match(str(v))]
        role = "id" if named_id(name) or looks_like_key(name, nums) else "measure"
        out = {"name": name, "role": role, "min": min(nums), "max": max(nums)}
        if role == "measure":
            out["sum"] = round(sum(nums), 2)
        return out
    distinct = len(set(s))
    return {"name": name, "role": "category" if distinct <= max(50, len(s) // 20) else "text",
            "distinct_in_sample": distinct, "examples": sorted(set(s))[:5]}


def main(argv):
    if not argv:
        sys.exit(__doc__)
    path = os.path.abspath(argv[0])
    def opt(flag):
        if flag not in argv:
            return None
        i = argv.index(flag) + 1
        if i >= len(argv):
            sys.exit(flag + " needs a value")
        return argv[i]
    sheet = opt("--sheet")
    index = opt("--sheet-index")
    if index is not None:
        if not re.fullmatch(r"[0-9]+", index):
            sys.exit("--sheet-index takes a number from the printed sheets list, got " + json.dumps(index))
        index = int(index)
    name = opt("--name")
    if name is None:
        name = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(path))[0].lower()).strip("-") or "source"
    elif not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        sys.exit("--name takes lowercase letters, digits and dashes (for example sales-2025), got " + json.dumps(name, ensure_ascii=False))
    try:
        size = os.path.getsize(path)
    except OSError as e:
        sys.exit("cannot read %s: %s" % (path, e.strerror))
    if size > MAX_FILE:
        sys.exit("refusing %s: %d MB, over the %d MB this profiler reads; aggregate the data or export the range you need first"
                 % (path, size >> 20, MAX_FILE >> 20))
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        read, rows = read_xlsx(path, sheet, index)
        ftype = "xlsx"
    else:
        read, rows = read_csv(path)
        ftype = "csv"
    if not rows:
        sys.exit("no rows in " + path)
    header = [h.strip() for h in rows[0]]
    body = rows[1:]
    cols = [classify(h, [r[i] if i < len(r) else "" for r in body]) for i, h in enumerate(header)]
    st = os.stat(path)
    manifest = {
        "name": name,
        "kind": "file",
        "file": {"path": path, "type": ftype, "header_row": 1, **read,
                 "sha256": sha256(path), "mtime": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                 "bytes": st.st_size},
        "rows": len(body),
        "columns": cols,
        "date_columns": [c["name"] for c in cols if c["role"] == "date"],
        "measures": [c["name"] for c in cols if c["role"] == "measure"],
        "profiled_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
