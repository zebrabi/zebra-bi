"""Structural checks on a written .pptx or .xlsx, run before a file is handed over.

Office refuses or "repairs" a package with a dangling relationship, a part without a content type or
malformed XML, and the add-in shows nothing when its settings or data do not parse. These are the
checks the Zebra AI app's export never ran.
"""
import hashlib, json, os, posixpath, re, zipfile
import xml.etree.ElementTree as ET

from . import canvas as CV
from . import ooxml as X

NS_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
NS_REL = "{%s}" % X.NS_REL
NS_WE = "{%s}" % X.NS_WE
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_CX = "{%s}" % CV.NS_CX
KNOWN = {a["id"].lower(): k for k, a in X.ADDINS.items()}


def _target(rels_path, target):
    base = posixpath.dirname(posixpath.dirname(rels_path))  # the folder of the part that owns the rels
    return posixpath.normpath(posixpath.join(base, target)).lstrip("/")


def check(path):
    """Returns (errors, warnings, summary)."""
    errors, warnings = [], []
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        return ["not a zip package: %s" % e], [], {}
    names = set(z.namelist())
    if "[Content_Types].xml" not in names:
        return ["[Content_Types].xml is missing"], [], {}
    trees = {}
    for n in names:
        if n.endswith((".xml", ".rels")):
            raw = z.read(n)
            if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
                errors.append("%s declares a DOCTYPE or ENTITY" % n)
                continue
            try:
                trees[n] = ET.fromstring(raw)
            except ET.ParseError as e:
                errors.append("%s is not well-formed XML: %s" % (n, e))
    ct = trees.get("[Content_Types].xml")
    defaults = {d.get("Extension").lower() for d in ct.iter(NS_CT + "Default")} if ct is not None else set()
    overrides = {o.get("PartName").lstrip("/") for o in ct.iter(NS_CT + "Override")} if ct is not None else set()
    for o in overrides - names:
        errors.append("content type names a missing part: /%s" % o)
    for n, tree in trees.items():  # a negative size is invalid DrawingML; PowerPoint repairs or refuses the file
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n):
            for e in tree.iter():
                if e.tag.endswith("}ext") and any(re.fullmatch(r"-\d+", e.get(a) or "") for a in ("cx", "cy")):
                    errors.append("%s: a shape has a negative size (cx=%s, cy=%s)" % (n, e.get("cx"), e.get("cy")))
    for n in names:
        if n.endswith("/") or n == "[Content_Types].xml":
            continue
        if n not in overrides and n.rsplit(".", 1)[-1].lower() not in defaults:
            errors.append("%s has no content type" % n)
    for n, tree in trees.items():
        if not n.endswith(".rels"):
            continue
        for rel in tree.iter(NS_REL + "Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            tgt = _target(n, rel.get("Target"))
            if tgt not in names:
                errors.append("%s: %s points to missing %s" % (n, rel.get("Id"), tgt))

    placeholder = hashlib.sha256(X.read(os.path.join(X.ASSETS, "snapshot-placeholder.png"), "rb")).hexdigest()
    summary = {"addins": {"charts": 0, "tables": 0}, "snapshots": 0, "placeholder_snapshots": 0, "canvas": 0}
    for n, tree in trees.items():
        if not re.search(r"/webextensions/webextension\d+\.xml$", n):
            continue
        ref = tree.find(NS_WE + "reference")
        kind = KNOWN.get((ref.get("id") if ref is not None else "").lower())
        if not kind:
            errors.append("%s: not a Zebra BI add-in reference" % n)
            continue
        summary["addins"][kind] += 1
        a = X.ADDINS[kind]
        props = {p.get("name"): p.get("value") for p in tree.iter(NS_WE + "property")}
        try:
            settings = json.loads(props.get(a["settings"]) or "null")
            if not isinstance(settings, dict):
                errors.append("%s: %s is empty" % (n, a["settings"]))
        except ValueError as e:
            errors.append("%s: %s is not JSON: %s" % (n, a["settings"], e))
        data_raw = props.get(a["data"]) or ""
        source = props.get("SourceData")
        if data_raw:
            try:
                data = json.loads(json.loads(data_raw))
                want = ("matrix", "metadata") if kind == "tables" else ("values", "categories", "metadata")
                missing = [k for k in want if k not in data]
                if missing:
                    errors.append("%s: %s lacks %s" % (n, a["data"], ", ".join(missing)))
            except (ValueError, TypeError) as e:
                errors.append("%s: %s is not double-encoded JSON: %s" % (n, a["data"], e))
        elif not source:
            errors.append("%s: no data (%s) and no SourceData range" % (n, a["data"]))
        if source:
            errors += _check_range(z, trees, n, json.loads(source))
        snap = tree.find(NS_WE + "snapshot")
        rid = snap.get("{%s}embed" % X.NS_R) if snap is not None else None
        if not rid:
            warnings.append("%s: no snapshot image; readers without the add-in see an empty frame" % n)
            continue
        rels = trees.get(posixpath.join(posixpath.dirname(n), "_rels", posixpath.basename(n) + ".rels"))
        img = None
        for rel in (rels.iter(NS_REL + "Relationship") if rels is not None else []):
            if rel.get("Id") == rid:
                img = _target(posixpath.join(posixpath.dirname(n), "_rels", "x.rels"), rel.get("Target"))
        if not img or img not in names:
            errors.append("%s: snapshot %s does not resolve" % (n, rid))
            continue
        summary["snapshots"] += 1
        if hashlib.sha256(z.read(img)).hexdigest() == placeholder:
            summary["placeholder_snapshots"] += 1
            warnings.append("%s: snapshot is the placeholder, not the chart" % n)
    summary["canvas"] = _check_canvas(z, trees, errors, warnings)
    if not any(summary["addins"].values()):
        warnings.append("no Zebra BI add-in visual in the file")
    return errors, warnings, summary


def _check_range(z, trees, part, source):
    """The SourceData range must name an existing sheet and lie inside the written cells."""
    return _extent(z, trees, part, source.get("range", ""), "SourceData", source)


def _extent(z, trees, part, ref, what, source=None):
    """A quoted 'Sheet'!A1:B2 reference must name an existing sheet and lie inside the written cells. With `source`
    (an add-in's SourceData) its worksheetId must also match the sheet's position. Returns a list of errors."""
    m = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)", ref)
    if not m:
        return ["%s: %s range %r is not a quoted 'Sheet'!A1:B2 range" % (part, what, ref)]
    sheet = m.group(1).replace("''", "'")
    wb = trees.get("xl/workbook.xml")
    rels = trees.get("xl/_rels/workbook.xml.rels")
    if wb is None or rels is None:
        return ["%s: %s in a file without a workbook" % (part, what)]
    rid = None
    for i, s in enumerate(wb.iter(NS_MAIN + "sheet")):
        if s.get("name") == sheet:
            rid = s.get("{%s}id" % X.NS_R)
            want_id = "{00000000-0001-0000-%02d00-000000000000}" % i
            if source is not None and source.get("worksheetId") != want_id:
                return ["%s: worksheetId %s does not match sheet %r (want %s)" % (part, source.get("worksheetId"), sheet, want_id)]
    if not rid:
        return ["%s: %s names sheet %r, which does not exist" % (part, what, sheet)]
    target = next(_target("xl/_rels/workbook.xml.rels", r.get("Target")) for r in rels.iter(NS_REL + "Relationship") if r.get("Id") == rid)
    if target not in trees:
        return ["%s: %s names sheet %r, which has no cells" % (part, what, sheet)]
    refs = {c.get("r") for c in trees[target].iter(NS_MAIN + "c") if len(c)}
    if "%s%s" % (m.group(2), m.group(3)) not in refs:
        return ["%s: %s starts at %s%s, which holds no header in sheet %r" % (part, what, m.group(2), m.group(3), sheet)]
    rows = [int(re.sub(r"[A-Z]+", "", r)) for r in refs]
    cols = [_col(re.sub(r"\d+", "", r)) for r in refs]
    if int(m.group(5)) > max(rows) or _col(m.group(4)) > max(cols):
        return ["%s: %s ends at %s%s, beyond the written data in sheet %r" % (part, what, m.group(4), m.group(5), sheet)]
    return []


def _check_canvas(z, trees, errors, warnings):
    """Each Excel Canvas (a chartEx part with a dashboard) must be reachable from a chartsheet in the workbook, bind a
    defined name that is a real data range, and hold a page that Excel will run. Returns the number of canvases."""
    wb = trees.get("xl/workbook.xml")
    wb_rels = trees.get("xl/_rels/workbook.xml.rels")
    found = [n for n, t in sorted(trees.items()) if re.fullmatch(r"xl/charts/chartEx\d+\.xml", n) and t.find(NS_CX + "dashboard") is not None]
    if not found:
        return 0
    if wb is None or wb_rels is None:
        errors.append("a canvas in a file without a workbook")
        return len(found)
    sheets = [(s.get("name"), s.get("{%s}id" % X.NS_R)) for s in wb.iter(NS_MAIN + "sheet")]
    wb_target = {r.get("Id"): (_target("xl/_rels/workbook.xml.rels", r.get("Target")), r.get("Type")) for r in wb_rels.iter(NS_REL + "Relationship")}
    names = {d.get("name"): (d.text or "").strip() for d in wb.iter(NS_MAIN + "definedName")}

    def rel_targets(part, kind):
        rels = trees.get(posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"))
        return [_target(posixpath.join(posixpath.dirname(part), "_rels", "x.rels"), r.get("Target"))
                for r in (rels.iter(NS_REL + "Relationship") if rels is not None else []) if r.get("Type") == kind]

    reachable, chart_positions = set(), []
    for pos, (_, rid) in enumerate(sheets):
        target, kind = wb_target.get(rid, (None, None))
        if kind == CV.REL_CHARTSHEET:
            chart_positions.append(pos)
            for d in rel_targets(target, CV.REL_DRAWING):
                reachable.update(rel_targets(d, CV.REL_CHARTEX))
    bound = set()
    for n, t in trees.items():
        if re.search(r"/webextensions/webextension\d+\.xml$", n):
            props = {p.get("name"): p.get("value") for p in t.iter(NS_WE + "property")}
            try:
                m = re.match(r"'((?:[^']|'')+)'!", json.loads(props.get("SourceData") or "{}").get("range", ""))
            except (ValueError, AttributeError):
                m = None
            if m:
                bound.add(m.group(1).replace("''", "'"))
    last_bound = max([i for i, (name, _) in enumerate(sheets) if name in bound], default=-1)
    for pos in chart_positions:
        if pos < last_bound:
            errors.append("the canvas sheet %r comes before a sheet that holds a Zebra BI add-in; it must come after every worksheet an add-in binds" % sheets[pos][0])
    for n in found:
        tree = trees[n]
        if n not in reachable:
            errors.append("%s is not reachable from a chartsheet listed in workbook.xml" % n)
        dash = tree.find(NS_CX + "dashboard")
        data = {d.get("id"): d for d in tree.iter(NS_CX + "data")}
        artifacts = {a.get("name"): a for a in dash.iter(NS_CX + "artifact")}
        art = artifacts.get(dash.get("entrypoint"))
        if art is None or art.get("type") != "html":
            errors.append("%s: the dashboard entrypoint %r names no html artifact" % (n, dash.get("entrypoint")))
        bindings = list(dash.iter(NS_CX + "binding"))
        if not bindings:
            errors.append("%s: the dashboard has no binding" % n)
        for b in bindings:
            d = data.get(b.get("id"))
            if d is None:
                errors.append("%s: binding %r has id %s, and no cx:data has that id" % (n, b.get("name"), b.get("id")))
                continue
            f = next((e.text or "" for e in d.iter(NS_CX + "f")), "").strip()
            if f not in names:
                errors.append("%s: binding %r reads %r, which is not a defined name in the workbook" % (n, b.get("name"), f))
                continue
            errors += _extent(z, trees, n, names[f], "defined name %s" % f)
            if art is not None and art.text and '"%s"' % b.get("name") not in art.text:
                errors.append("%s: the page never names its binding %r" % (n, b.get("name")))
        clear = (art.text or "") if art is not None else ""
        for tag in CV.SCRIPT_TAG.finditer(clear):
            m = CV.SCRIPT_TYPE.search(tag.group(0))
            kind = next((g for g in m.groups() if g is not None), "") if m else ""
            if kind.strip().lower() not in CV.JS_TYPES:
                errors.append("%s: the page has a <script> block of type %r; the canvas host drops script blocks that are not "
                              "JavaScript, so data must ride in a JavaScript string" % (n, kind))
        try:
            clear, js = CV.unpack(clear)
        except ValueError as e:
            errors.append("%s: %s" % (n, e))
            js = None
        if js is not None and "DecompressionStream('gzip')" not in clear:
            errors.append("%s: the page packs its renderer script but has no loader that unpacks it" % n)
        text = clear + "\n" + (js or "")
        if "<iframe" in text.lower():
            errors.append("%s: the page contains an <iframe>, which Excel refuses on a canvas" % n)
        if CV.PARENT in text:
            errors.append("%s: the page still has %s; the renderer's transport must point at the page" % (n, CV.PARENT))
        if CV.SCRIPT_SRC.search(text):
            errors.append("%s: the page loads a script from an address, and a canvas has no network" % n)
        if art is not None:
            try:
                built = CV.built_result(clear)
            except ValueError as e:
                errors.append("%s: %s" % (n, e))
            else:
                why = CV.result_error(built) if built is not None else "the page embeds no result to draw"
                if why:
                    errors.append("%s: %s" % (n, why))
        if art is not None and "getDataBindingValue" not in text:
            errors.append("%s: the page never calls getDataBindingValue, so it cannot read its cells" % n)
        size = len((art.text or "").encode("utf-8")) if art is not None else 0
        if size > CV.MAX_PAGE_BYTES:
            errors.append("%s: %s" % (n, CV.size_error(size)[len("canvas: "):].replace("; build without --canvas", "")))
    return len(found)


def _col(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n
