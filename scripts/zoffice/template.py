"""The base a deck is built on: the generated default (editorial master, three layouts, theme from the
brand) or a company's own .pptx/.potx, whose masters, layouts, themes and media are kept and whose
slides are dropped. Also reads a template for the brand interview (`inspect`).
"""
import os, re, urllib.parse, zipfile
import xml.etree.ElementTree as ET

from . import ooxml as X

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": X.NS_R}
CT_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
R = X.NS_R + "/"
EMU = 914400
CT = {
    "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    "master": "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml",
    "layout": "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml",
    "notesMaster": "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml",
    "theme": "application/vnd.openxmlformats-officedocument.theme+xml",
    "presProps": "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml",
    "viewProps": "application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml",
    "tableStyles": "application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml",
}
# What is taken from a company template: its design and nothing else. Masters, layouts, themes, the notes
# and handout masters, presentation properties, images and embedded fonts. Slides, macros, ActiveX,
# embedded documents, OLE objects and custom XML are never copied (an allowlist, not a denylist).
KEEP = re.compile(r"^ppt/(slideMasters|slideLayouts|theme|notesMasters|handoutMasters)/(_rels/)?[^/]+\.xml(\.rels)?$"
                  r"|^ppt/(presProps|viewProps|tableStyles)\.xml$"
                  r"|^ppt/media/[^/]+\.(png|jpe?g|gif|svg|emf|wmf|tiff?|bmp)$"
                  r"|^ppt/fonts/[^/]+\.(fntdata|odttf)$", re.I)
HYPERLINK = R + "hyperlink"
# The only relationships a kept part may have: the design graph itself, pictures, embedded fonts, and
# hyperlinks out of the file. An OLE object, package, ActiveX or anything unknown refuses the template,
# whatever its target is called.
REL_TYPES = {R + t for t in ("slideLayout", "slideMaster", "theme", "image", "notesMaster", "handoutMaster", "font",
                             "presProps", "viewProps", "tableStyles", "themeOverride")} | {
    "http://schemas.microsoft.com/office/2007/relationships/hdphoto"}
# Content types a kept part may declare, so a template cannot relabel a part as something Office runs.
PART_TYPES = {T for T in CT.values()} | {"application/vnd.openxmlformats-officedocument.presentationml.handoutMaster+xml",
                                         "application/vnd.openxmlformats-officedocument.themeOverride+xml"}
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml",
               "emf": "image/x-emf", "wmf": "image/x-wmf", "tif": "image/tiff", "tiff": "image/tiff", "bmp": "image/bmp",
               "fntdata": "application/x-fontdata", "odttf": "application/vnd.openxmlformats-officedocument.obfuscatedFont"}


def _media_ok(name, data):
    """A picture's bytes must be the format its extension claims."""
    ext = name.rsplit(".", 1)[-1].lower()
    head = data[:64]
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if ext == "bmp":
        return head.startswith(b"BM")
    if ext in ("tif", "tiff"):
        return head.startswith((b"II*\x00", b"MM\x00*"))
    if ext == "emf":
        return len(data) > 44 and data[40:44] == b" EMF"
    if ext == "wmf":
        return head.startswith((b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00"))
    if ext == "svg":  # what it may contain is checked by X.svg_problem
        text = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        return text.startswith(b"<") and b"<svg" in data[:65536].lower()
    return ext in ("fntdata", "odttf")  # embedded fonts are obfuscated by design; only reachable as fonts


def _parse(raw, name):
    problem = X.xml_problem(raw)
    if problem:
        raise ValueError("%s %s; refusing to read it" % (name, problem))
    return ET.fromstring(raw)


def _hexrgb(c):
    return (c or "#000000").lstrip("#").upper()


class Base:
    """parts: {path: bytes}; overrides: {'/path': content type}; defaults: {ext: content type};
    presentation: presentation.xml text; pres_rels: non-slide relationships of presentation.xml;
    layouts: [{path, name, type, ph: {key: (x, y, cx, cy)}}]; size: (cx, cy); notes_master: path or None;
    master_has_picture: whether the first master already carries an image (a logo, usually)."""

    def __init__(self):
        self.parts, self.overrides, self.defaults = {}, {}, {}
        self.presentation, self.pres_rels, self.layouts = "", [], []
        self.size, self.notes_master, self.master_has_picture = (12192000, 6858000), None, False

    def layout(self, role, mapping):
        """The layout for 'title', 'content' or 'closing': the brand's mapped name, else by type."""
        name = (mapping or {}).get(role)
        if name:
            for l in self.layouts:
                if l["name"] == name:
                    return l
            raise ValueError("layout %r is not in the template (has: %s)" % (name, ", ".join(l["name"] for l in self.layouts)))
        prefs = {"title": ("title",), "content": ("titleOnly", "obj", "tx"), "closing": ("titleOnly", "obj", "tx")}[role]
        for t in prefs:
            for l in self.layouts:
                if l["type"] == t and has_title(l):
                    return l
        for l in self.layouts:
            if any(k in l["ph"] for k in ("title", "ctrTitle")):
                return l
        return self.layouts[0]


def has_title(layout):
    """A layout we can fill: it has a title placeholder. Designer layouts sometimes carry their title as a
    plain text box, which neither this tool nor PowerPoint's own New Slide can fill."""
    return any(k in layout["ph"] for k in ("title", "ctrTitle"))


# ---------- reading layouts ----------

# ST_PlaceholderType and ST_UInt: a placeholder's type and idx are written back into our slides, so anything
# else in a template is ignored rather than carried into the slide XML.
PH_TYPES = {"title", "body", "ctrTitle", "subTitle", "dt", "sldNum", "ftr", "hdr", "obj", "chart", "tbl",
            "clipArt", "dgm", "media", "sldImg", "pic"}


def _placeholders(tree):
    out = {}
    for sp in tree.iter("{%s}sp" % NS["p"]):
        ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is None:
            continue
        typ = ph.get("type", "obj")
        idx = ph.get("idx", "0")
        if typ not in PH_TYPES or not re.fullmatch(r"\d{1,10}", idx):
            continue
        off = sp.find("p:spPr/a:xfrm/a:off", NS)
        ext = sp.find("p:spPr/a:xfrm/a:ext", NS)
        rect = (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))) if off is not None and ext is not None else None
        out[typ if typ in ("title", "ctrTitle", "subTitle", "dt", "ftr", "sldNum") else "%s:%s" % (typ, idx)] = {
            "type": typ, "idx": idx, "rect": rect}
    return out


def _read_layouts(parts):
    """Layouts in master order, with placeholder rects resolved against their master."""
    def rels_of(path):
        rp = os.path.join(os.path.dirname(path), "_rels", os.path.basename(path) + ".rels").replace("\\", "/")
        if rp not in parts:
            return []
        t = _parse(parts[rp], rp)
        return [(r.get("Id"), r.get("Type"), os.path.normpath(os.path.join(os.path.dirname(path), r.get("Target"))).replace("\\", "/"))
                for r in t]

    masters = sorted(p for p in parts if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", p))
    layouts, has_pic = [], False
    for m in masters:
        mtree = _parse(parts[m], m)
        if m == masters[0]:
            has_pic = mtree.find(".//p:cSld/p:spTree/p:pic", NS) is not None
        mph = _placeholders(mtree)
        mrels = {rid: tgt for rid, _, tgt in rels_of(m)}
        for lid in mtree.findall("p:sldLayoutIdLst/p:sldLayoutId", NS):
            path = mrels.get(lid.get("{%s}id" % X.NS_R))
            if not path or path not in parts:
                continue
            lt = _parse(parts[path], path)
            ph = _placeholders(lt)
            for k, v in ph.items():
                if v["rect"] is None:  # inherits its position from the master
                    src = mph.get(k) or mph.get({"ctrTitle": "title", "subTitle": "body:1"}.get(k, ""))
                    if src is None and ":" in k:
                        src = mph.get("body:1") or next((x for kk, x in mph.items() if kk.startswith("body")), None)
                    v["rect"] = src["rect"] if src else None
            cs = lt.find("p:cSld", NS)
            layouts.append({"path": path, "name": (cs.get("name") if cs is not None else "") or os.path.basename(path),
                            "type": lt.get("type", "cust"), "ph": ph, "master": m})
    return layouts, has_pic


# ---------- the generated default ----------

def _theme(brand):
    """The default theme with the brand's colours and fonts."""
    c, f = brand["colors"], brand["fonts"]
    base = X.read(os.path.join(X.ASSETS, "default", "pptx", "theme1.xml"))
    accent = c.get("accent") or c["ink"]
    # accent1 carries the brand accent; the rest are IBCS-safe neutrals, so Office's own charts and
    # SmartArt never introduce a colour that reads as a variance.
    scheme = {"dk1": c["ink"], "lt1": c["paper"], "dk2": c["muted"], "lt2": c["surface"], "accent1": accent,
              "accent2": "#404040", "accent3": "#8C8E91", "accent4": "#BFC2C5", "accent5": c["hairline"], "accent6": "#0070C0",
              "hlink": "#0F6CBD", "folHlink": "#5C2E91"}
    clr = '<a:clrScheme name="%s">%s</a:clrScheme>' % (X.t(brand.get("name", "Brand")), "".join(
        '<a:%s><a:srgbClr val="%s"/></a:%s>' % (k, _hexrgb(v), k) for k, v in scheme.items()))
    base = re.sub(r"<a:clrScheme .*?</a:clrScheme>", clr, base, flags=re.S)
    base = re.sub(r'(<a:majorFont>\s*<a:latin typeface=")[^"]*"(?: panose="[^"]*")?', r'\g<1>%s"' % X.t(f["headline"]["family"]), base)
    base = re.sub(r'(<a:minorFont>\s*<a:latin typeface=")[^"]*"(?: panose="[^"]*")?', r'\g<1>%s"' % X.t(f["body"]["family"]), base)
    return base.replace('name="Office Theme"', 'name=%s' % X.attr(brand.get("name", "Brand")), 1)


def _sp_ph(sid, name, ph, rect, body="", anchor=None, lst=""):
    x, y, cx, cy = rect
    a = ' anchor="%s"' % anchor if anchor else ""
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name=%s/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr>%s</p:nvPr></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            '<p:txBody><a:bodyPr vert="horz" lIns="0" tIns="0" rIns="0" bIns="0"%s><a:normAutofit/></a:bodyPr><a:lstStyle>%s</a:lstStyle>'
            '<a:p><a:r><a:rPr lang="en-US"/><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>'
            % (sid, X.attr(name), ph, x, y, cx, cy, a, lst, X.t(body)))


def _tree(shapes, name=None, bg=False):
    return ('<p:cSld%s>%s<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '%s</p:spTree></p:cSld>'
            % (" name=%s" % X.attr(name) if name else "",
               '<p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>' if bg else "", "".join(shapes)))


def _level(n, size, color, font, bold=False, space_before=0, line=None):
    ln = '<a:lnSpc><a:spcPct val="%d"/></a:lnSpc>' % line if line else ""
    return ('<a:lvl%dpPr marL="%d" indent="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1">'
            '%s<a:spcBef><a:spcPts val="%d"/></a:spcBef><a:buNone/>'
            '<a:defRPr sz="%d" b="%d" kern="1200"><a:solidFill><a:schemeClr val="%s"/></a:solidFill>'
            '<a:latin typeface="%s"/><a:ea typeface="+mn-ea"/><a:cs typeface="%s"/></a:defRPr></a:lvl%dpPr>'
            % (n, (n - 1) * 228600, ln, space_before, size * 100, int(bold), color, font, font, n))


def default_base(brand):
    W, H = 12192000, 6858000
    M = int(0.75 * EMU)
    title_rect = (M, int(0.5 * EMU), W - 2 * M, int(0.95 * EMU))
    body_rect = (M, int(1.65 * EMU), W - 2 * M, H - int(1.65 * EMU) - int(0.5 * EMU))
    P = 'xmlns:a="%s" xmlns:r="%s" xmlns:p="%s"' % (NS["a"], NS["r"], NS["p"])
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    body_levels = "".join(_level(i, 16 if i == 1 else 14, "tx1", "+mn-lt", space_before=600) for i in range(1, 10))
    master = (head + '<p:sldMaster %s>' % P
              + _tree([_sp_ph(2, "Title Placeholder 1", '<p:ph type="title"/>', title_rect, "Title", anchor="b"),
                       _sp_ph(3, "Text Placeholder 2", '<p:ph type="body" idx="1"/>', body_rect, "Text")], bg=True)
              + '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
                'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
              + '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/><p:sldLayoutId id="2147483650" r:id="rId2"/>'
                '<p:sldLayoutId id="2147483651" r:id="rId3"/></p:sldLayoutIdLst>'
              + '<p:hf sldNum="0" hdr="0" ftr="0" dt="0"/>'
              + '<p:txStyles><p:titleStyle>%s</p:titleStyle><p:bodyStyle>%s</p:bodyStyle>'
                '<p:otherStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr>%s</p:otherStyle></p:txStyles></p:sldMaster>'
              % (_level(1, 28, "tx1", "+mj-lt", line=95000), body_levels, _level(1, 14, "tx1", "+mn-lt")))

    def layout(name, typ, shapes):
        return (head + '<p:sldLayout %s type="%s" preserve="1">' % (P, typ) + _tree(shapes, name=name)
                + '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')

    big = '<a:lvl1pPr><a:lnSpc><a:spcPct val="92000"/></a:lnSpc><a:defRPr sz="4000"/></a:lvl1pPr>'
    sub = '<a:lvl1pPr><a:defRPr sz="1800"><a:solidFill><a:schemeClr val="tx2"/></a:solidFill></a:defRPr></a:lvl1pPr>'
    layouts = [
        ("Title", "title", [_sp_ph(2, "Title 1", '<p:ph type="ctrTitle"/>', (M, int(1.55 * EMU), W - 2 * M, int(2.1 * EMU)), "Key message", "b", big),
                            _sp_ph(3, "Subtitle 2", '<p:ph type="subTitle" idx="1"/>', (M, int(3.8 * EMU), W - 2 * M, int(0.8 * EMU)), "Subtitle", None, sub)]),
        ("Title Only", "titleOnly", [_sp_ph(2, "Title 1", '<p:ph type="title"/>', title_rect, "Title", "b")]),
        ("Blank", "blank", []),
    ]
    b = Base()
    b.size = (W, H)
    b.parts["ppt/slideMasters/slideMaster1.xml"] = master.encode()
    b.overrides["/ppt/slideMasters/slideMaster1.xml"] = CT["master"]
    mrels = []
    for i, (name, typ, shapes) in enumerate(layouts, start=1):
        p = "ppt/slideLayouts/slideLayout%d.xml" % i
        b.parts[p] = layout(name, typ, shapes).encode()
        b.overrides["/" + p] = CT["layout"]
        b.parts["ppt/slideLayouts/_rels/slideLayout%d.xml.rels" % i] = X.rels([("rId1", R + "slideMaster", "../slideMasters/slideMaster1.xml")]).encode()
        mrels.append(("rId%d" % i, R + "slideLayout", "../slideLayouts/slideLayout%d.xml" % i))
    mrels.append(("rId%d" % (len(layouts) + 1), R + "theme", "../theme/theme1.xml"))
    b.parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = X.rels(mrels).encode()
    base = os.path.join(X.ASSETS, "default", "pptx")
    b.parts["ppt/theme/theme1.xml"] = _theme(brand).encode()
    b.overrides["/ppt/theme/theme1.xml"] = CT["theme"]
    for name, path, ct in (("theme2.xml", "ppt/theme/theme2.xml", CT["theme"]),
                           ("presProps.xml", "ppt/presProps.xml", CT["presProps"]),
                           ("viewProps.xml", "ppt/viewProps.xml", CT["viewProps"]),
                           ("tableStyles.xml", "ppt/tableStyles.xml", CT["tableStyles"]),
                           ("notesMaster1.xml", "ppt/notesMasters/notesMaster1.xml", CT["notesMaster"]),
                           ("notesMaster1.xml.rels", "ppt/notesMasters/_rels/notesMaster1.xml.rels", None)):
        b.parts[path] = X.read(os.path.join(base, name), "rb")
        if ct:
            b.overrides["/" + path] = ct
    b.presentation = X.read(os.path.join(base, "presentation.xml"))
    b.pres_rels = [("rId1", R + "slideMaster", "slideMasters/slideMaster1.xml"),
                   ("rId3", R + "presProps", "presProps.xml"), ("rId4", R + "viewProps", "viewProps.xml"),
                   ("rId5", R + "theme", "theme/theme1.xml"), ("rId6", R + "tableStyles", "tableStyles.xml"),
                   ("rId7", R + "notesMaster", "notesMasters/notesMaster1.xml")]
    b.notes_master = "ppt/notesMasters/notesMaster1.xml"
    b.layouts, b.master_has_picture = _read_layouts(b.parts)
    return b


# ---------- a company template ----------

def from_template(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        # Office resolves part names case-insensitively and may pick a different copy of a duplicated
        # entry than Python does, so anything ambiguous is refused rather than interpreted.
        lowered = [n.lower() for n in names]
        if len(set(lowered)) != len(lowered):
            raise ValueError("%s has duplicate entries; save it again from PowerPoint" % os.path.basename(path))
        bad = [n for n in names if re.search(r'[\\%\x00-\x1f]|(^|/)\.\.?(/|$)|^/', n)]
        if bad:
            raise ValueError("%s has unusual entry names (%s); save it again from PowerPoint" % (os.path.basename(path), bad[0]))
        if sum(i.file_size for i in z.infolist()) > 300 * 1024 * 1024:
            raise ValueError("%s unpacks to more than 300 MB; that is not a template" % os.path.basename(path))
        raw = {n: z.read(n) for n in names}
    if "ppt/presentation.xml" not in raw:
        raise ValueError("%s is not a PowerPoint file or template" % path)
    if any(re.search(r"vbaProject|activeX", n, re.I) for n in raw):
        raise ValueError("%s carries macros or ActiveX controls; save it as a plain .potx or .pptx" % os.path.basename(path))
    b = Base()
    ct = _parse(raw["[Content_Types].xml"], "[Content_Types].xml")
    overrides = {o.get("PartName"): o.get("ContentType") for o in ct.iter(CT_NS + "Override")}
    for n, data in raw.items():
        if not KEEP.match(n):
            continue
        if n.startswith(("ppt/media/", "ppt/fonts/")):
            if not _media_ok(n, data):
                raise ValueError("%s: %s is not the file type its name says; remove it from the template" % (os.path.basename(path), n))
            problem = X.svg_problem(data) if n.lower().endswith(".svg") else None
            if problem:  # every picture in ppt/media is copied, used or not, so each one is checked
                raise ValueError("%s: the picture %s %s; remove it from the template or replace it with a PNG"
                                 % (os.path.basename(path), n, problem))
            b.defaults[n.rsplit(".", 1)[-1].lower()] = MEDIA_TYPES[n.rsplit(".", 1)[-1].lower()]  # never the template's own label
        elif X.xml_problem(data):  # kept parts are copied whether or not they are parsed here
            raise ValueError("%s: %s %s; save the template again from PowerPoint"
                             % (os.path.basename(path), n, X.xml_problem(data)))
        elif n.endswith(".xml"):
            declared = overrides.get("/" + n)
            if declared not in PART_TYPES:
                raise ValueError("%s: %s is declared as %r, which is not a design part" % (os.path.basename(path), n, declared))
            b.overrides["/" + n] = declared
        b.parts[n] = data
    # Every relationship of a kept part must point at another kept part; outside the file only hyperlinks.
    kept = {p.lower() for p in b.parts}
    for n in [p for p in b.parts if p.endswith(".rels")]:
        owner_dir = os.path.dirname(os.path.dirname(n))
        for r in _parse(b.parts[n], n):
            if r.get("TargetMode") == "External":
                if r.get("Type") != HYPERLINK:
                    raise ValueError("%s links %s to an external %s; remove that link from the template"
                                     % (os.path.basename(path), n.split("/_rels/")[0], r.get("Type").rsplit("/", 1)[-1]))
                continue
            if r.get("Type") not in REL_TYPES:
                raise ValueError("%s: %s has a %s relationship, which a template's design does not need; remove that "
                                 "object from the template" % (os.path.basename(path), os.path.basename(n)[:-5],
                                                               (r.get("Type") or "?").rsplit("/", 1)[-1]))
            target = urllib.parse.unquote(r.get("Target") or "")
            tgt = os.path.normpath(os.path.join("/" if target.startswith("/") else owner_dir, target.lstrip("/"))).replace("\\", "/").lstrip("/")
            if "#" in target or tgt.lower() not in kept:
                raise ValueError("%s: %s uses %s, which is not a design part (an embedded object or custom data); "
                                 "remove it from the template" % (os.path.basename(path), os.path.basename(n)[:-5], tgt))
    pres = raw["ppt/presentation.xml"].decode("utf-8")
    pt = _parse(raw["ppt/presentation.xml"], "presentation.xml")
    sz = pt.find("p:sldSz", NS)
    if sz is not None:
        b.size = (int(sz.get("cx")), int(sz.get("cy")))
    # The template's slide list and its section list (which names those slides) are rebuilt.
    pres = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>|<p:sldIdLst/>", "<p:sldIdLst></p:sldIdLst>", pres, flags=re.S)
    pres = re.sub(r'<p:ext uri="\{521415D9-36F7-43E2-AB2F-B90AF26B5E84\}">.*?</p:ext>', "", pres, flags=re.S)
    pres = re.sub(r"<p:custShowLst>.*?</p:custShowLst>", "", pres, flags=re.S)
    pres = re.sub(r"<p:custDataLst>.*?</p:custDataLst>|<p:custDataLst/>", "", pres, flags=re.S)
    pres = re.sub(r"<p:smartTags[^>]*/>", "", pres)
    if "<p:sldIdLst>" not in pres:
        pres = pres.replace("</p:notesMasterIdLst>", "</p:notesMasterIdLst><p:sldIdLst></p:sldIdLst>", 1) \
            if "</p:notesMasterIdLst>" in pres else re.sub(r"(</p:sldMasterIdLst>)", r"\1<p:sldIdLst></p:sldIdLst>", pres, count=1)
    b.presentation = pres
    rt = _parse(raw["ppt/_rels/presentation.xml.rels"], "presentation.xml.rels")
    for r in rt:
        tgt = os.path.normpath(os.path.join("ppt", r.get("Target"))).replace("\\", "/")
        if r.get("TargetMode") == "External" or tgt not in b.parts:
            continue  # slides, comments, custom data, tags and anything else that was not copied
        b.pres_rels.append((r.get("Id"), r.get("Type"), r.get("Target")))
        if r.get("Type") == R + "notesMaster":
            b.notes_master = tgt
    kept_ids = {r[0] for r in b.pres_rels}
    for rid in set(re.findall(r'r:id="([^"]+)"', pres)) - kept_ids:  # a reference to a part that was not copied
        if not re.search(r'<p:sldId [^>]*r:id="%s"' % re.escape(rid), pres):
            raise ValueError("%s: presentation.xml refers to %s, which is not a design part" % (os.path.basename(path), rid))
    b.layouts, b.master_has_picture = _read_layouts(b.parts)
    if not b.layouts:
        raise ValueError("%s has no slide layouts" % path)
    return b


def inspect(path):
    """What the brand interview needs from a template: size, theme colours and fonts, layouts, logo candidates."""
    b = from_template(path)
    theme = next((p for p in sorted(b.parts) if re.fullmatch(r"ppt/theme/theme\d+\.xml", p)), None)
    colors, fonts = {}, {}
    if theme:
        t = _parse(b.parts[theme], theme)
        cs = t.find(".//a:clrScheme", NS)
        for el in (cs if cs is not None else []):
            tag = el.tag.split("}")[1]
            v = el.find("a:srgbClr", NS)
            s = el.find("a:sysClr", NS)
            colors[tag] = "#" + (v.get("val") if v is not None else (s.get("lastClr") if s is not None else "??????"))
        for kind in ("majorFont", "minorFont"):
            lt = t.find(".//a:%s/a:latin" % kind, NS)
            fonts[kind] = lt.get("typeface") if lt is not None else None
    images = sorted(p for p in b.parts if p.startswith("ppt/media/"))
    return {
        "size_in": [round(b.size[0] / EMU, 2), round(b.size[1] / EMU, 2)],
        "theme_colors": colors, "theme_fonts": fonts,
        "layouts": [{"name": l["name"], "type": l["type"], "title_placeholder": has_title(l), "placeholders": sorted(l["ph"])}
                    for l in b.layouts],
        "suggested": {r: b.layout(r, None)["name"] for r in ("title", "content", "closing")},
        "master_has_picture": b.master_has_picture,
        "images": images,
    }
