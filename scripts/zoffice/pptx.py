"""Write a story as a PowerPoint deck with a live Zebra BI for Office visual on each section slide.

The deck is built on a base (zoffice/template.py): the generated editorial default, or a company's own
template from the brand. Titles go into the layout's real placeholders, so the template's typography,
outline view and accessibility all work; the finding, KPIs and visual are placed in the layout's
content area. Text inherits the template's fonts; colours come from the brand. Each visual is an
mc:AlternateContent: the add-in part when PowerPoint supports web add-ins, and the chart's picture as
the fallback and as the add-in's own snapshot, as the Zebra AI app's PowerPoint export does.
"""
import os, posixpath, re

from . import ooxml as X
from . import template as T
from . import visual as V

P_NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
EMU = 914400  # per inch
MIN_REGION = 2.5  # inches: the finding, a gap and a readable chart
MIN_VISUAL = 1.0  # inches left for the chart, or the layout cannot hold a section
CT = {
    "presentation": T.CT["presentation"],
    "slide": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
    "notes": "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml",
    "core": "application/vnd.openxmlformats-package.core-properties+xml",
    "app": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
}
R = T.R


def hexval(c):
    return (c or "#000000").lstrip("#").upper()


# On a company template, text colours follow the template's own text colour (tx1), so a dark layout keeps
# its text readable; "muted" is tx1 at 65 %. The default deck uses the brand's exact colours.
SCHEME = {"ink": '<a:schemeClr val="tx1"/>', "muted": '<a:schemeClr val="tx1"><a:lumMod val="65000"/><a:lumOff val="35000"/></a:schemeClr>'}


def fill(color):
    return SCHEME[color] if color in SCHEME else '<a:srgbClr val="%s"/>' % hexval(color)


def runs(text, size, color, bold=False):
    """One paragraph's runs; **bold** spans become bold runs. Fonts are inherited from the template."""
    out = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            out.append('<a:r><a:rPr lang="en-US" sz="%d" b="%d" dirty="0"><a:solidFill>%s</a:solidFill>'
                       '</a:rPr><a:t>%s</a:t></a:r>' % (size * 100, int(bold or i % 2 == 1), fill(color), X.t(part)))
    return "".join(out)


def paragraphs(text, size, color, bold=False, space=600, caps_spacing=None):
    paras = [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", text or "") if p.strip()] or [""]
    body = []
    for p in paras:
        r = runs(p, size, color, bold)
        if caps_spacing:
            r = r.replace('<a:rPr lang="en-US"', '<a:rPr lang="en-US" spc="%d"' % caps_spacing)
        body.append('<a:p><a:pPr><a:spcAft><a:spcPts val="%d"/></a:spcAft></a:pPr>%s<a:endParaRPr lang="en-US" sz="%d"/></a:p>'
                    % (space, r, size * 100))
    return "".join(body)


def plain_paragraph(text):
    """Text for a placeholder: no size or colour, so the template's own style applies."""
    return "".join('<a:r><a:rPr lang="en-US" dirty="0"/><a:t>%s</a:t></a:r>' % X.t(p)
                   for p in re.split(r"\*\*(.+?)\*\*", text or "") if p)


def placeholder(sid, ph, text):
    """A slide placeholder filled with text, with its position and geometry written out: PowerPoint inherits
    both from the layout, Keynote drops a placeholder whose geometry it cannot resolve."""
    attrs = "type=%s" % X.attr(ph["type"]) + (" idx=%s" % X.attr(ph["idx"]) if ph["idx"] != "0" else "")
    xfrm = ('<a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>' % ph["rect"]
            if ph.get("rect") else "")
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name=%s/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph %s/></p:nvPr></p:nvSpPr><p:spPr>%s</p:spPr><p:txBody><a:bodyPr><a:normAutofit/></a:bodyPr><a:lstStyle/>'
            '<a:p>%s</a:p></p:txBody></p:sp>' % (sid, X.attr(ph["type"].title()), attrs, xfrm, plain_paragraph(text)))


def textbox(sid, name, x, y, w, h, body, anchor="t", autofit=True):
    fit = "<a:normAutofit/>" if autofit else ""
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name=%s/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '<a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="%s">%s</a:bodyPr>'
            '<a:lstStyle/>%s</p:txBody></p:sp>' % (sid, X.attr(name), x, y, w, h, anchor, fit, body))


def rule(sid, name, x, y, w, color, weight=9525):
    """A hairline: the editorial device that replaces boxes and stripes."""
    return ('<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="%d" name=%s/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="0"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
            '<a:ln w="%d"><a:solidFill>%s</a:solidFill></a:ln></p:spPr></p:cxnSp>'
            % (sid, X.attr(name), x, y, w, weight, fill(color)))


def picture(sid, name, rid, x, y, w, h):
    return ('<p:pic><p:nvPicPr><p:cNvPr id="%d" name=%s/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
            % (sid, X.attr(name), rid, x, y, w, h))


def addin(sid, name, we_rid, img_rid, x, y, w, h):
    """pptx_defaults.ALTERNATE_CONTENT_TEMPLATE: the add-in frame, and the snapshot picture as fallback."""
    return ('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<mc:Choice xmlns:we="http://schemas.microsoft.com/office/webextensions/webextension/2010/11" '
            'xmlns:pca="http://schemas.microsoft.com/office/powerpoint/2013/contentapp" Requires="we pca">'
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="%d" name=%s/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/>'
            '</p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/webextensions/webextension/2010/11">'
            '<we:webextensionref r:id="%s"/></a:graphicData></a:graphic></p:graphicFrame></mc:Choice>'
            '<mc:Fallback>%s</mc:Fallback></mc:AlternateContent>'
            % (sid, X.attr(name), x, y, w, h, we_rid, picture(sid, name, img_rid, x, y, w, h)))


def slide(shapes):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sld %s><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>' % (P_NS, "".join(shapes)))


def notes(text):
    body = "".join('<a:p>%s</a:p>' % plain_paragraph(p.strip().replace("\n", " "))
                   for p in re.split(r"\n\s*\n", text or "") if p.strip()) or "<a:p/>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:notes %s><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>%s</p:txBody></p:sp>'
            '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>' % (P_NS, body))


def fit(img_w, img_h, box_w, box_h):
    scale = min(box_w / img_w, box_h / img_h)
    return int(img_w * scale), int(img_h * scale)


class Geometry:
    """Where things go on a slide of this base: the title placeholder of a layout and its content region."""

    def __init__(self, base):
        self.W, self.H = base.size
        self.inch = min(self.W / 12192000, self.H / 6858000) * EMU  # scales with the template's slide size

    def title_ph(self, layout):
        ph = layout["ph"]
        p = ph.get("ctrTitle") or ph.get("title")
        if p and p["rect"]:
            return p
        return {"type": "title", "idx": "0", "rect": (int(0.75 * self.inch), int(0.5 * self.inch),
                                                      self.W - int(1.5 * self.inch), int(0.95 * self.inch))}

    def region(self, layout):
        """The content area: the layout's largest body placeholder, else the space under the title. A caption or
        subheadline placeholder is too short for a finding and a chart, so it never becomes the content area."""
        rects = [v["rect"] for k, v in layout["ph"].items() if k.startswith(("obj:", "body:")) and v["rect"]]
        best = max(rects, key=lambda r: r[2] * r[3], default=None)
        if best and best[2] > 0 and best[3] >= int(MIN_REGION * self.inch):
            return best
        x, y, cx, cy = self.title_ph(layout)["rect"]
        top = y + cy + int(0.25 * self.inch)
        return (x, top, cx, max(0, self.H - top - int(0.45 * self.inch)))


def _rel(src_dir, target):
    return posixpath.relpath(target, src_dir)


def build(story, visuals, snapshots, out, footer, brand):
    """story: story.json; visuals: {id: structuredContent.visual}; snapshots: {id: png path};
    brand: the resolved brand (scripts/brand.py load), with absolute file paths."""
    c = brand["colors"]
    template = (brand.get("deck") or {}).get("template")
    ink, muted, hair = ("ink", "muted", c["hairline"]) if template else (c["ink"], c["muted"], c["hairline"])
    mark = c.get("accent") or ink
    base = T.from_template(template) if template else T.default_base(brand)
    editorial = not template  # our hairlines belong to the default design; a company template brings its own
    mapping = (brand.get("deck") or {}).get("layouts") or {}
    L = {r: base.layout(r, mapping) for r in ("title", "content", "closing")}
    g = Geometry(base)
    inch = g.inch

    pkg = X.Package()
    pkg.defaults.update(base.defaults)
    for p, data in base.parts.items():
        pkg.add(p, data, base.overrides.get("/" + p))
    pkg.add("_rels/.rels", X.rels([("rId1", R + "officeDocument", "ppt/presentation.xml"),
                                   ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
                                   ("rId3", R + "extended-properties", "docProps/app.xml")]))
    pkg.add("docProps/core.xml", X.core_props(story.get("title", "")), CT["core"])
    pkg.add("docProps/app.xml", X.app_props("Microsoft Office PowerPoint"), CT["app"])
    pres_rels = list(base.pres_rels)
    pres = base.presentation
    notes_master = base.notes_master
    if not notes_master:  # a template without one gets the default notes master and its theme
        d = os.path.join(X.ASSETS, "default", "pptx")
        notes_master = "ppt/notesMasters/notesMasterZs.xml"
        pkg.add(notes_master, X.read(os.path.join(d, "notesMaster1.xml"), "rb"), T.CT["notesMaster"])
        pkg.add("ppt/notesMasters/_rels/notesMasterZs.xml.rels", X.rels([("rId1", R + "theme", "../theme/themeZsNotes.xml")]))
        pkg.add("ppt/theme/themeZsNotes.xml", X.read(os.path.join(d, "theme2.xml"), "rb"), T.CT["theme"])
        pres_rels.append(("rIdZsNotes", R + "notesMaster", "notesMasters/notesMasterZs.xml"))
        pres = re.sub(r"(</p:sldMasterIdLst>)", r'\1<p:notesMasterIdLst><p:notesMasterId r:id="rIdZsNotes"/></p:notesMasterIdLst>', pres, count=1)

    slides = []  # (shapes, layout, extra rels, notes text)

    # 1. Cover: kicker, key message, subtitle, KPIs, logo, source and credit.
    lay = L["title"]
    tp = g.title_ph(lay)
    tx, ty, tw, th = tp["rect"]
    shapes, extra, sid = [placeholder(2, tp, story.get("title", ""))], [], 10
    sub = lay["ph"].get("subTitle")
    if sub and sub["rect"]:
        shapes.append(placeholder(3, sub, story.get("subtitle", "")))
        below = sub["rect"][1] + sub["rect"][3]
    else:
        shapes.append(textbox(3, "Subtitle", tx, ty + th + int(0.15 * inch), tw, int(0.7 * inch),
                              paragraphs(story.get("subtitle", ""), 16, muted)))
        below = ty + th + int(0.85 * inch)
    kick = (str(story.get("kicker") or "").strip() or str(brand.get("kicker") or "").strip())[:60]  # story first, as story.kicker
    if kick and ty > int(0.9 * inch):
        ky = int(0.55 * inch) if editorial else ty - int(0.45 * inch)
        shapes.append(textbox(4, "Kicker", tx, ky, tw, int(0.3 * inch),
                              paragraphs(kick.upper(), 10, ink, bold=True, space=0, caps_spacing=150), autofit=False))
        if editorial:
            shapes.append(rule(5, "Kicker rule", tx, ky + int(0.36 * inch), tw, hair))
    kpis = story.get("kpis", [])[:4]
    if kpis:
        # Under the subtitle, but never into the source line at the foot of the slide.
        ky = min(below + int(0.45 * inch), base.size[1] - int(1.85 * inch))
        ky = max(ky, below + int(0.15 * inch))
        gap = int(0.3 * inch)
        kw = (tw - gap * (len(kpis) - 1)) // len(kpis)
        for i, k in enumerate(kpis):
            x = tx + i * (kw + gap)
            shapes.append(rule(sid, "KPI rule %d" % (i + 1), x, ky, kw, mark, 12700))
            shapes.append(textbox(sid + 1, "KPI %d" % (i + 1), x, ky + int(0.12 * inch), kw, int(1.0 * inch),
                                  paragraphs(str(k.get("value", "")), 26, ink, bold=True, space=200)
                                  + paragraphs(str(k.get("label", "")), 12, muted)))
            sid += 2
    credit = brand.get("credit")
    line = footer + (("  ·  " + credit) if credit else "")
    shapes.append(textbox(30, "Source", tx, base.size[1] - int(0.6 * inch), tw, int(0.3 * inch),
                          paragraphs(line, 9, muted, space=0), anchor="b"))
    logo = brand.get("logo") or {}
    if logo.get("png") and logo.get("placement") == "cover" and not base.master_has_picture:
        lw0, lh0 = X.png_size(logo["png"])
        lh = int(0.42 * inch)
        lw = int(lh * lw0 / lh0)
        if lw > int(2.2 * inch):
            lw, lh = int(2.2 * inch), int(2.2 * inch * lh0 / lw0)
        pkg.add_file("ppt/media/zsLogo.png", logo["png"])
        shapes.append(picture(31, "Logo", "rIdZsLogo", tx + tw - lw, int(0.45 * inch), lw, lh))
        extra.append(("rIdZsLogo", R + "image", "../media/zsLogo.png"))
    slides.append((shapes, lay, extra, story.get("subtitle", "")))

    # 2. One slide per supporting message: the message as the slide title, the finding, the visual.
    lay = L["content"]
    tp = g.title_ph(lay)
    rx, ry, rw, rh = g.region(lay)
    n_we = 0
    for s in story.get("sections", []):
        shapes, extra = [placeholder(2, tp, s.get("heading", ""))], []
        finding_h = int(0.8 * inch)
        shapes.append(textbox(3, "Finding", rx, ry, rw, finding_h, paragraphs(s.get("body", ""), 14, ink)))
        vid = s.get("visual")
        if vid:
            v = visuals[vid]
            n_we += 1
            png = snapshots[vid]
            iw, ih = X.png_size(png)
            top = ry + finding_h + int(0.15 * inch)
            box_h = ry + rh - top
            if rw <= 0 or box_h < int(MIN_VISUAL * inch):
                raise ValueError("layout %r leaves no room for a chart under the finding; choose another content "
                                 "layout in the brand (deck.layouts.content)" % lay["name"])
            w, h = fit(iw, ih, rw, box_h)
            kind = "tables" if V.is_table(v) else "charts"
            if kind == "charts":
                props = {"ZBILicenseSettings": "", "ChartSettings": X.compact(V.chart_settings(v)),
                         "zebrabi:table-data": X.double_encoded(V.chart_dataview(v)), "ChartChooserStore": ""}
            else:
                props = {"ZBILicenseSettings": "", "ZBITablesSettings": X.compact(V.table_settings(v, "pptx")),
                         "zebrabi:matrix-data": X.double_encoded(V.table_data(v, "pptx")), "TableChooserStore": ""}
            pkg.add_file("ppt/media/zsSnapshot%d.png" % n_we, png)
            pkg.add("ppt/webextensions/webextension%d.xml" % n_we, X.webextension(kind, props, snapshot_rid="rId1"), X.CT_WEBEXT)
            pkg.add("ppt/webextensions/_rels/webextension%d.xml.rels" % n_we,
                    X.rels([("rId1", X.R_IMAGE, "../media/zsSnapshot%d.png" % n_we)]))
            shapes.append(addin(5, "Zebra BI %s: %s" % ("Tables" if kind == "tables" else "Charts", V.title(v) or vid),
                                "rIdZsWe", "rIdZsSnap", rx, top, w, h))
            extra = [("rIdZsSnap", X.R_IMAGE, "../media/zsSnapshot%d.png" % n_we),
                     ("rIdZsWe", X.R_WEBEXT, "../webextensions/webextension%d.xml" % n_we)]
        slides.append((shapes, lay, extra, s.get("body", "")))

    # 3. Where to look next.
    if story.get("next"):
        lay = L["closing"]
        tp = g.title_ph(lay)
        rx, ry, rw, rh = g.region(lay)
        body = "".join('<a:p><a:pPr marL="228600" indent="-228600"><a:buFont typeface="Arial"/><a:buChar char="&#8226;"/>'
                       '<a:spcAft><a:spcPts val="1000"/></a:spcAft></a:pPr>%s</a:p>' % runs(n, 18, ink) for n in story["next"])
        slides.append(([placeholder(2, tp, story.get("next_heading", "Where to look next")),
                        textbox(3, "Next", rx, ry, rw, rh, body)], lay, [], ""))

    nm_rel = _rel("ppt/notesSlides", notes_master)
    sld_ids = []
    for i, (shapes, lay, extra, note) in enumerate(slides, start=1):
        pkg.add("ppt/slides/slide%d.xml" % i, slide(shapes), CT["slide"])
        pkg.add("ppt/slides/_rels/slide%d.xml.rels" % i,
                X.rels([("rId1", R + "slideLayout", _rel("ppt/slides", lay["path"])),
                        ("rIdNotes", R + "notesSlide", "../notesSlides/notesSlide%d.xml" % i)] + extra))
        pkg.add("ppt/notesSlides/notesSlide%d.xml" % i, notes(note), CT["notes"])
        pkg.add("ppt/notesSlides/_rels/notesSlide%d.xml.rels" % i,
                X.rels([("rId1", R + "notesMaster", nm_rel), ("rId2", R + "slide", "../slides/slide%d.xml" % i)]))
        pres_rels.append(("rIdZsSlide%d" % i, R + "slide", "slides/slide%d.xml" % i))
        sld_ids.append('<p:sldId id="%d" r:id="rIdZsSlide%d"/>' % (256 + i, i))
    pres = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", lambda m: "<p:sldIdLst>%s</p:sldIdLst>" % "".join(sld_ids), pres, count=1, flags=re.S)
    pkg.add("ppt/presentation.xml", pres, CT["presentation"])
    pkg.add("ppt/_rels/presentation.xml.rels", X.rels(pres_rels))
    return pkg.write(out)
