"""Structural checks on a written .pptx or .xlsx, run before a file is handed over.

Office refuses or "repairs" a package with a dangling relationship, a part without a content type or
malformed XML, and the add-in shows nothing when its settings or data do not parse. These are the
checks the Zebra AI app's export never ran.
"""
import hashlib, json, os, posixpath, re, zipfile
import xml.etree.ElementTree as ET

from . import ooxml as X

NS_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
NS_REL = "{%s}" % X.NS_REL
NS_WE = "{%s}" % X.NS_WE
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
    summary = {"addins": {"charts": 0, "tables": 0}, "snapshots": 0, "placeholder_snapshots": 0}
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
    if not any(summary["addins"].values()):
        warnings.append("no Zebra BI add-in visual in the file")
    return errors, warnings, summary


def _check_range(z, trees, part, source):
    """The SourceData range must name an existing sheet and lie inside the written cells."""
    m = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)", source.get("range", ""))
    if not m:
        return ["%s: SourceData range %r is not a quoted 'Sheet'!A1:B2 range" % (part, source.get("range"))]
    sheet = m.group(1).replace("''", "'")
    wb = trees.get("xl/workbook.xml")
    rels = trees.get("xl/_rels/workbook.xml.rels")
    if wb is None or rels is None:
        return ["%s: SourceData in a file without a workbook" % part]
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rid = None
    for i, s in enumerate(wb.iter(main + "sheet")):
        if s.get("name") == sheet:
            rid = s.get("{%s}id" % X.NS_R)
            want_id = "{00000000-0001-0000-%02d00-000000000000}" % i
            if source.get("worksheetId") != want_id:
                return ["%s: worksheetId %s does not match sheet %r (want %s)" % (part, source.get("worksheetId"), sheet, want_id)]
    if not rid:
        return ["%s: SourceData names sheet %r, which does not exist" % (part, sheet)]
    target = next(_target("xl/_rels/workbook.xml.rels", r.get("Target")) for r in rels.iter(NS_REL + "Relationship") if r.get("Id") == rid)
    refs = {c.get("r") for c in trees[target].iter(main + "c") if len(c)}
    if "%s%s" % (m.group(2), m.group(3)) not in refs:
        return ["%s: SourceData starts at %s%s, which holds no header in sheet %r" % (part, m.group(2), m.group(3), sheet)]
    rows = [int(re.sub(r"[A-Z]+", "", r)) for r in refs]
    cols = [_col(re.sub(r"\d+", "", r)) for r in refs]
    if int(m.group(5)) > max(rows) or _col(m.group(4)) > max(cols):
        return ["%s: SourceData ends at %s%s, beyond the written data in sheet %r" % (part, m.group(4), m.group(5), sheet)]
    return []


def _col(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n
