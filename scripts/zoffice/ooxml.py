"""Shared OOXML pieces: a zip package, relationships, and the Zebra BI for Office webextension part.

The add-in references, property names and the double-encoded data property follow the Zebra AI app's
PowerPoint and Excel export.
"""
import datetime as dt, io, json, os, re, struct, uuid, zipfile
import xml.etree.ElementTree as ET
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
# The only DOCTYPEs an SVG may carry: the public SVG 1.0 and 1.1 ones editors such as Illustrator write.
# They declare nothing and point at the W3C's own DTDs; any other DOCTYPE (a SYSTEM one, an internal
# subset) can declare entities and is refused.
_SVG_DOCTYPE = re.compile(
    rb'<!DOCTYPE\s+svg\s+PUBLIC\s+"(?:'
    rb'-//W3C//DTD SVG 1\.1//EN"\s+"http://www\.w3\.org/Graphics/SVG/1\.1/DTD/svg11\.dtd'
    rb'|-//W3C//DTD SVG 1\.1 (?:Tiny|Basic)//EN"\s+"http://www\.w3\.org/Graphics/SVG/1\.1/DTD/svg11-(?:tiny|basic)\.dtd'
    rb'|-//W3C//DTD SVG (?:1\.0|20010904)//EN"\s+"http://www\.w3\.org/TR/2001/REC-SVG-20010904/DTD/svg10\.dtd'
    rb')"\s*>')


def encoding_problem(data):
    """Why XML is not plain UTF-8 (or a declared single-byte Latin encoding), or None. In UTF-16, UTF-32,
    EBCDIC or invalid UTF-8 a byte search for "<!DOCTYPE" or "<script" can miss what a parser reads, so such
    XML is refused: it must start with "<" (after an optional UTF-8 BOM and whitespace) and decode cleanly."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data:
        return "is not UTF-8 (it is UTF-16 or UTF-32)"
    body = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    if not body.lstrip(b" \t\r\n").startswith(b"<"):
        return "does not start like UTF-8 XML"
    m = _XML_ENCODING.match(data)
    declared = m.group(1).decode("ascii").lower() if m else "utf-8"
    if declared not in _ASCII_SAFE:
        return "declares the %s encoding instead of UTF-8" % m.group(1).decode("ascii")
    if declared in ("utf-8", "utf8", "us-ascii", "ascii"):
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            return "is not valid UTF-8"
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


# What an SVG may refer to: a place in the file itself (#id) or a picture carried inline.
_LOCAL = re.compile(r"\s*(#|data:image/(png|jpe?g|gif|webp)[;,])", re.I)
# Elements that run code or pull in other documents, by local name, whatever their namespace prefix.
_ACTIVE = {"script", "foreignobject", "iframe", "object", "embed", "handler", "listener", "meta"}
# Attributes a browser fetches from besides href and src. A picture has no use for any of them, so any value is refused.
_FETCHES = {"srcset", "imagesrcset", "poster", "background", "lowsrc", "dynsrc", "longdesc", "data", "codebase",
            "archive", "manifest", "ping", "action", "formaction"}
_XHTML = "{http://www.w3.org/1999/xhtml}"
_SVG_ROOT = "{http://www.w3.org/2000/svg}svg"
_XML_NS = "{http://www.w3.org/XML/1998/namespace}"
_CSS_COMMENT = re.compile(r"/\*.*?(\*/|\Z)", re.S)
_CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\r\n\f]?|\\(.)", re.S)
_CSS_URL = re.compile(r"url\(\s*([\"']?)(.*?)\1\s*\)", re.I | re.S)


def _css_problem(css):
    """Why CSS text (a style element or any attribute value) reaches outside the file, with escapes such as \72
    decoded. It is read twice, with comments dropped and with them kept, because CSS drops comments between
    tokens but keeps them inside an unquoted url(...): url(/**/#x) is a path, not a place in the file.
    Anything either reading flags is refused."""
    def unescape(m):
        if m.group(1):
            n = int(m.group(1), 16)
            return chr(n) if 0 < n <= 0x10FFFF else "�"
        return m.group(2)
    for text in (_CSS_ESCAPE.sub(unescape, _CSS_COMMENT.sub(" ", css)).lower(), _CSS_ESCAPE.sub(unescape, css).lower()):
        if "@import" in text:
            return "imports a stylesheet from elsewhere"
        if "image-set(" in text or re.search(r"\bsrc\(", text):
            return "loads a picture from elsewhere"
        urls = _CSS_URL.findall(text)
        if text.count("url(") != len(urls):
            return "has a url() this check cannot read"
        for _, ref in urls:
            if not _LOCAL.match(ref):
                return "refers to %s outside the file" % ref.strip()[:80]
    return None


def svg_problem(data):
    """Why an SVG must not travel in a deck or story page, or None when it is a plain picture. The bytes are
    checked for what a parser would act on before building a tree (another encoding, entities, a DOCTYPE
    other than the public SVG one, a stylesheet processing instruction). Everything else is checked on the
    parsed tree, where CDATA is joined and character references are decoded exactly as PowerPoint or a
    browser sees them: active elements, event handlers, xml:base, links, animated links and CSS."""
    enc = encoding_problem(data)
    if enc:
        return enc
    if re.search(rb"<!\s*ENTITY", data, re.I):
        return "declares entities"
    allowed = {m.start() for m in _SVG_DOCTYPE.finditer(data)}
    if any(m.start() not in allowed for m in re.finditer(rb"<!\s*DOCTYPE", data, re.I)):
        return "declares a DOCTYPE other than the public SVG one"
    if re.search(rb"<\?\s*xml-stylesheet", data, re.I):
        return "loads a stylesheet from elsewhere (xml-stylesheet)"
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return "is not well-formed XML"
    if root.tag != _SVG_ROOT:
        return "is not an SVG (its root element is not svg in the SVG namespace)"
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = el.tag.rsplit("}", 1)[-1].lower()
        if name in _ACTIVE:
            return {"script": "carries a script", "foreignobject": "embeds HTML (foreignObject)"}.get(
                name, "embeds an active element (%s)" % name)
        if el.tag.startswith(_XHTML):
            return "embeds HTML (%s)" % name
        if name == "style":
            # The whole text content, as a browser reads it: CSS after a child element sits in that child's tail.
            problem = _css_problem("".join(el.itertext()))
            if problem:
                return problem
        for key, value in el.attrib.items():
            if key == _XML_NS + "base":
                return "sets xml:base, which can turn a link inside the file into one outside it"
            local = key.rsplit("}", 1)[-1].lower()
            if local.startswith("on"):
                return "carries event handlers (on... attributes)"
            if local in ("href", "src") and not _LOCAL.match(value):
                return "refers to %s outside the file" % value.strip()[:80]
            if local in _FETCHES and value.strip():
                return "has a %s attribute, which loads from elsewhere" % local
            if local == "attributename" and value.strip().rsplit(":", 1)[-1].lower() in ("href", "src"):
                return "animates a link, which can point outside the file"
            problem = _css_problem(value)
            if problem:
                return problem
    return None


def double_encoded(obj):
    """The add-in stores its data property as a JSON string that itself holds JSON (officeStorageHelper.ts)."""
    return json.dumps(compact(obj), ensure_ascii=False)


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def write_atomic(path, data, mode=0o666):
    """Write to a new temp file beside path, then rename it over path. A crash leaves no half file, and a
    symlink planted at path (or at the temp name) is replaced, never written through."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    tmp = os.path.join(os.path.dirname(path), ".%s.%s.tmp" % (os.path.basename(path), os.urandom(4).hex()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.remove(tmp)
        raise
    return path


def build_dir(path):
    """Create a folder a build writes into. A symbolic link there is refused: it would send the writes elsewhere."""
    if os.path.islink(path):
        raise ValueError("%s is a symbolic link; a build writes only into a real folder" % path)
    os.makedirs(path, exist_ok=True)
    return path


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
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", self.content_types())
            for path in sorted(self.parts):
                z.writestr(path, self.parts[path])
        return write_atomic(out, buf.getvalue())
