"""Shared OOXML pieces: a zip package, relationships, and the Zebra BI for Office webextension part.

The add-in references, property names and the double-encoded data property follow the Zebra AI app's
PowerPoint and Excel export.
"""
import datetime as dt, json, os, re, struct, uuid, zipfile
from xml.sax.saxutils import escape, quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "..", "assets", "brand")

NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_WE = "http://schemas.microsoft.com/office/webextensions/webextension/2010/11"
R_IMAGE = NS_R + "/image"
R_WEBEXT = "http://schemas.microsoft.com/office/2011/relationships/webextension"
CT_WEBEXT = "application/vnd.ms-office.webextension+xml"

# AppSource asset ids and versions as the Zebra AI app writes them (Zebra BI Charts / Tables for Office).
ADDINS = {
    "charts": {"id": "WA200004688", "version": "1.0.4.0", "alt_store": "", "settings": "ChartSettings", "data": "zebrabi:table-data",
               "chooser": "ChartChooserStore", "binding": "chartsRangeChangeEvent"},
    "tables": {"id": "WA200004689", "version": "1.0.0.0", "alt_store": "omex", "settings": "ZBITablesSettings", "data": "zebrabi:matrix-data",
               "chooser": "TableChooserStore", "binding": "tablesRangeChanged"},
}


# The chooser stores the Zebra AI app writes: with isVisible false the add-in draws the saved visual instead of
# opening its "pick a chart/table" screen (pptx_defaults.WEBEXTENSION_DEFAULT_CHARTS/_TABLES).
CHOOSER = {
    "charts": {"isVisible": False, "selectedDimension": "time", "selectedChart": "time-comparison-column", "areComments": True},
    "tables": {"isVisible": False, "selectedDimension": "comparison", "selectedTable": "comparison-table-one", "areComments": False},
}


def t(text):
    return escape(str(text if text is not None else ""))


def attr(value):
    return quoteattr(str(value))


def compact(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
# Encodings in which every ASCII character is the same single byte, so the byte-level checks below see
# exactly what a parser will see. Office, Excel and SVG editors write UTF-8.
_ASCII_SAFE = {"utf-8", "utf8", "us-ascii", "ascii", "iso-8859-1", "latin1", "latin-1", "windows-1252", "cp1252"}
_XML_ENCODING = re.compile(rb"""\A(?:\xef\xbb\xbf)?\s*<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""", re.I)
# Inside an SVG a reference may point into the file itself (#id) or carry a picture inline; anything else
# reaches out when the SVG is drawn.
_REF = re.compile(rb"""(?:\bhref|\bsrc)\s*=\s*(["'])\s*(.*?)\1|url\(\s*(["']?)\s*([^"')\s]*)""", re.I | re.S)
_LOCAL = re.compile(rb"#|data:image/(png|jpe?g|gif|webp)[;,]", re.I)
# The only DOCTYPEs an SVG may carry: the public SVG 1.0 and 1.1 ones editors such as Illustrator write.
# They declare nothing and point at the W3C's own DTDs; any other DOCTYPE (a SYSTEM one, an internal
# subset) can declare entities and is refused.
_SVG_DOCTYPE = re.compile(
    rb'<!DOCTYPE\s+svg\s+PUBLIC\s+"(?:'
    rb'-//W3C//DTD SVG 1\.1//EN"\s+"http://www\.w3\.org/Graphics/SVG/1\.1/DTD/svg11\.dtd'
    rb'|-//W3C//DTD SVG 1\.1 (?:Tiny|Basic)//EN"\s+"http://www\.w3\.org/Graphics/SVG/1\.1/DTD/svg11-(?:tiny|basic)\.dtd'
    rb'|-//W3C//DTD SVG (?:1\.0|20010904)//EN"\s+"http://www\.w3\.org/TR/2001/REC-SVG-20010904/DTD/svg10\.dtd'
    rb')"\s*>')
# Elements that run code or pull in other documents; a namespace prefix (svg:script) does not hide them.
_ACTIVE = re.compile(rb"<\s*(?:[a-z0-9_.-]+:)?(script|foreignobject|iframe|object|embed|handler|listener)\b", re.I)


def encoding_problem(data):
    """Why XML is not in an ASCII-compatible encoding (UTF-16 or UTF-32 by BOM, NUL bytes or its declaration),
    or None. In such XML a byte search for "<!DOCTYPE" or "<script" finds nothing, so it is refused."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data:
        return "is not UTF-8 (it is UTF-16 or UTF-32)"
    m = _XML_ENCODING.match(data)
    if m and m.group(1).decode("ascii").lower() not in _ASCII_SAFE:
        return "declares the %s encoding instead of UTF-8" % m.group(1).decode("ascii")
    return None


def xml_problem(data):
    """Why an XML part must not be read or copied, or None. No part a deck, workbook or picture needs declares
    a DOCTYPE or an entity, so such a part is refused rather than parsed (no entity expansion, no external
    entities, no dependency)."""
    enc = encoding_problem(data)
    if enc:
        return enc
    if DECLARATION.search(data):
        return "declares a DOCTYPE or ENTITY"
    return None


def svg_problem(data):
    """Why an SVG must not travel in a deck or story page, or None when it is a plain picture: another
    encoding, declarations, scripts, event handlers, embedded documents, or references outside the file."""
    enc = encoding_problem(data)
    if enc:
        return enc
    if re.search(rb"<!\s*ENTITY", data, re.I):
        return "declares entities"
    allowed = {m.start() for m in _SVG_DOCTYPE.finditer(data)}
    if any(m.start() not in allowed for m in re.finditer(rb"<!\s*DOCTYPE", data, re.I)):
        return "declares a DOCTYPE other than the public SVG one"
    low = data.lower()
    m = _ACTIVE.search(low)
    if m:
        return {b"script": "carries a script", b"foreignobject": "embeds HTML (foreignObject)"}.get(
            m.group(1), "embeds an active element (%s)" % m.group(1).decode("ascii"))
    if re.search(rb"[\s/\"']([a-z0-9_.-]+:)?on[a-z]+\s*=", low):
        return "carries event handlers (on... attributes)"
    if re.search(rb"""attributename\s*=\s*["']\s*([a-z0-9_.-]+:)?href""", low):
        return "animates a link, which can point outside the file"
    if b"@import" in low or b"image-set(" in low:
        return "loads a stylesheet or picture from elsewhere"
    for m in _REF.finditer(data):
        ref = (m.group(2) if m.group(2) is not None else m.group(4)) or b""
        if ref and not _LOCAL.match(ref):
            return "refers to %s outside the file" % ref[:80].decode("utf-8", "replace")
    return None


def double_encoded(obj):
    """The add-in stores its data property as a JSON string that itself holds JSON (officeStorageHelper.ts)."""
    return json.dumps(compact(obj), ensure_ascii=False)


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s is not a PNG" % path)
    return struct.unpack(">II", head[16:24])


def rels(items):
    """items: [(rId, type, target)]"""
    body = "".join('<Relationship Id=%s Type=%s Target=%s/>' % (attr(i), attr(ty), attr(tg)) for i, ty, tg in items)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="%s">%s</Relationships>' % (NS_REL, body)


def webextension(kind, props, snapshot_rid=None, appref=None):
    """One webextension part. props: {name: python value}; strings go in as they are."""
    a = ADDINS[kind]
    props = dict(props)
    if not props.get(a["chooser"]):
        props[a["chooser"]] = compact(CHOOSER[kind])
    order = ["ZBILicenseSettings", a["chooser"], a["settings"], "SourceData", a["data"]]
    p = "".join('<we:property name=%s value=%s/>' % (attr(n), attr(props.get(n, ""))) for n in order if n in props or n != "SourceData")
    binding = ('<we:bindings><we:binding id=%s type="matrix" appref=%s/></we:bindings>' % (attr(a["binding"]), attr(appref))
               if appref else "<we:bindings/>")
    snap = ('<we:snapshot xmlns:r="%s" r:embed=%s/>' % (NS_R, attr(snapshot_rid)) if snapshot_rid
            else '<we:snapshot xmlns:r="%s"/>' % NS_R)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<we:webextension xmlns:we="%s" id="{%s}">'
            '<we:reference id="%s" version="%s" store="en-US" storeType="OMEX"/>'
            '<we:alternateReferences><we:reference id="%s" version="%s" store=%s storeType="OMEX"/></we:alternateReferences>'
            '<we:properties>%s</we:properties>%s%s</we:webextension>'
            % (NS_WE, str(uuid.uuid4()).upper(), a["id"].lower(), a["version"], a["id"], a["version"], attr(a["alt_store"]),
               p, binding, snap))


def core_props(title, creator="Zebra BI data story"):
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><cp:lastModifiedBy>%s</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'
            % (t(title), t(creator), t(creator), now, now))


def app_props(application):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<Application>%s</Application></Properties>' % t(application))


class Package:
    """Parts in memory, written once. Content types: defaults by extension plus one override per XML part."""

    def __init__(self):
        self.parts = {}
        self.overrides = {}
        self.defaults = {}  # extra extension defaults, e.g. a company template's emf or fntdata

    def add(self, path, data, content_type=None):
        self.parts[path] = data.encode("utf-8") if isinstance(data, str) else data
        if content_type:
            self.overrides["/" + path] = content_type

    def add_file(self, path, src):
        self.add(path, read(src, "rb"))

    def content_types(self):
        d = dict(self.defaults)
        d.update({"rels": "application/vnd.openxmlformats-package.relationships+xml", "xml": "application/xml",
                  "png": "image/png", "jpeg": "image/jpeg"})
        body = "".join('<Default Extension=%s ContentType=%s/>' % (attr(k), attr(v)) for k, v in sorted(d.items()))
        body += "".join('<Override PartName=%s ContentType=%s/>' % (attr(k), attr(v)) for k, v in sorted(self.overrides.items()))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">%s</Types>' % body)

    def write(self, out):
        tmp = out + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", self.content_types())
            for path in sorted(self.parts):
                z.writestr(path, self.parts[path])
        os.replace(tmp, out)
        return out
