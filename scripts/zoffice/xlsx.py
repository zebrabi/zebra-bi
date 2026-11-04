"""Write a story as an Excel workbook: a Story sheet, then one sheet per visual with its data and a live
Zebra BI for Office add-in bound to that data.

Hand-written SpreadsheetML (no openpyxl). The add-in part follows the Zebra AI app's Excel export: a
SourceData property with the range and worksheet id, and a matrix binding, so the add-in reads the cells
when the workbook opens. Here the sheet name in the range is always quoted and the add-in carries the
snapshot image.
"""
import math, os, re

from . import ooxml as X
from . import visual as V

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT = {
    "workbook": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "sheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
    "styles": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
    "drawing": "application/vnd.openxmlformats-officedocument.drawing+xml",
    "core": "application/vnd.openxmlformats-package.core-properties+xml",
    "app": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
}
# cellXfs indices in styles()
NORMAL, TITLE, HEADING, MUTED, HEADER, NUMBER, BODY, LINK, KICKER = range(9)


def _argb(c):
    return "FF" + (c or "#000000").lstrip("#").upper()


def styles(brand):
    """styles.xml from the brand: body and headline fonts, ink and muted colours, a hairline under headers."""
    c, f = brand["colors"], brand["fonts"]
    body, head = X.t(f["body"]["family"]), X.t(f["headline"]["family"])
    ink, muted, rule = _argb(c["ink"]), _argb(c["muted"]), _argb(c.get("accent") or c["ink"])
    font = '<font>%s<sz val="%d"/><color rgb="%s"/><name val="%s"/><family val="2"/></font>'
    fonts = [font % ("", 10, ink, body), font % ("", 20, ink, head), font % ("", 13, ink, head),
             font % ("", 10, muted, body), font % ("<b/>", 10, ink, body), font % ("<u/>", 10, "FF0F6CBD", body),
             font % ("<b/>", 9, muted, body)]
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="%s">
<fonts count="%d">%s</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top/><bottom style="thin"><color rgb="%s"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="9">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="4" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="0" fontId="5" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="6" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>""" % (NS_MAIN, len(fonts), "".join(fonts), rule)


COL_PX, ROW_PX = 64, 20     # default column width and row height in pixels
ADDIN_W_PX = 820            # on-sheet width of the add-in
DATA_TOP = 4                # first data row (1-based): heading, finding, blank row above it


def col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def plain(text):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", (text or "").strip()).replace("\n\n", "\n")


def rich_kpi(ref, value, label, brand):
    c, f = brand["colors"], brand["fonts"]
    return ('<c r="%s" t="inlineStr"><is><r><rPr><sz val="14"/><color rgb="%s"/><rFont val=%s/></rPr>'
            '<t xml:space="preserve">%s   </t></r><r><rPr><sz val="10"/><color rgb="%s"/><rFont val=%s/></rPr>'
            '<t xml:space="preserve">%s</t></r></is></c>'
            % (ref, _argb(c["ink"]), X.attr(f["headline"]["family"]), X.t(value), _argb(c["muted"]),
               X.attr(f["body"]["family"]), X.t(label)))


def cell(ref, value, style=NORMAL):
    if isinstance(value, tuple) and value and value[0] == "kpi":
        return rich_kpi(ref, value[1], value[2], value[3])
    if value is None or value == "":
        return '<c r="%s" s="%d"/>' % (ref, style) if style else ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, X.t(value))
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ""
    return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, repr(value) if isinstance(value, float) else value)


def sheet_xml(rows, widths, drawing=False, hyperlinks=(), heights=None):
    """rows: {row number: [(col number, value, style)]}"""
    cols = "".join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (c, c, w) for c, w in sorted(widths.items()))
    data = []
    for r in sorted(rows):
        ht = ' ht="%s" customHeight="1"' % heights[r] if heights and r in heights else ""
        data.append('<row r="%d"%s>%s</row>' % (r, ht, "".join(cell("%s%d" % (col_letter(c), r), v, s) for c, v, s in rows[r])))
    links = ""
    if hyperlinks:
        links = "<hyperlinks>%s</hyperlinks>" % "".join(
            '<hyperlink ref="%s" location=%s display=%s/>' % (ref, X.attr(loc), X.attr(disp)) for ref, loc, disp in hyperlinks)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="%s" xmlns:r="%s"><sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>'
            '<sheetFormatPr defaultRowHeight="15"/>%s<sheetData>%s</sheetData>%s%s</worksheet>'
            % (NS_MAIN, R[:-1], "<cols>%s</cols>" % cols if cols else "", "".join(data), links,
               '<drawing r:id="rIdDrawing"/>' if drawing else ""))


def drawing_xml(c0, r0, c1, r1, name, w_emu, h_emu):
    """xlsx_defaults.DRAWING_TEMPLATE, with the snapshot as the fallback picture."""
    anchor = ('<xdr:from><xdr:col>%d</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>%d</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
              '<xdr:to><xdr:col>%d</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>%d</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
              % (c0, r0, c1, r1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="%s" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<xdr:twoCellAnchor editAs="oneCell">%s<mc:AlternateContent>'
            '<mc:Choice xmlns:we="http://schemas.microsoft.com/office/webextensions/webextension/2010/11" Requires="we">'
            '<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="2" name=%s/><xdr:cNvGraphicFramePr>'
            '<a:graphicFrameLocks noGrp="1"/></xdr:cNvGraphicFramePr></xdr:nvGraphicFramePr>'
            '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm><a:graphic>'
            '<a:graphicData uri="http://schemas.microsoft.com/office/webextensions/webextension/2010/11">'
            '<we:webextensionref r:id="rIdWe"/></a:graphicData></a:graphic></xdr:graphicFrame></mc:Choice>'
            '<mc:Fallback><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="2" name=%s/><xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
            '<xdr:blipFill><a:blip r:embed="rIdSnap"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
            '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
            '</xdr:pic></mc:Fallback></mc:AlternateContent><xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>'
            % (R[:-1], anchor, X.attr(name), X.attr(name), w_emu, h_emu))


def sheet_names(story):
    """One unique, valid sheet name per visual, from the section heading (31 chars, no []:*?/\\)."""
    used, out = {"story"}, {}
    for s in story.get("sections", []):
        vid = s.get("visual")
        if not vid or vid in out:
            continue
        base = re.sub(r"[\[\]:*?/\\']", "", s.get("heading") or vid).strip()[:28].strip() or vid[:28]
        name, n = base, 2
        while name.lower() in used:
            name, n = "%s %d" % (base[:25], n), n + 1
        used.add(name.lower())
        out[vid] = name
    return out


def build(story, visuals, snapshots, out, footer, brand):
    pkg = X.Package()
    names = sheet_names(story)
    sheets = []  # (name, xml)

    # Story sheet: key message, subtitle, KPIs, one linked line per supporting message.
    kick = (str(story.get("kicker") or "").strip() or str(brand.get("kicker") or "").strip())[:60]  # story first, as story.kicker
    rows = {1: [(1, kick.upper(), KICKER)], 2: [(1, story.get("title", ""), TITLE)],
            3: [(1, story.get("subtitle", ""), MUTED)]}
    r = 5
    for k in story.get("kpis", [])[:6]:
        rows[r] = [(1, ("kpi", str(k.get("value", "")), str(k.get("label", "")), brand), NORMAL)]
        r += 1
    r += 1
    links = []
    for s in story.get("sections", []):
        rows[r] = [(1, s.get("heading", ""), HEADING if not s.get("visual") else LINK)]
        if s.get("visual") in names:
            links.append(("A%d" % r, "'%s'!A1" % names[s["visual"]], s.get("heading", "")))
        rows[r + 1] = [(1, plain(s.get("body", "")), BODY)]
        r += 3
    if story.get("next"):
        rows[r] = [(1, story.get("next_heading", "Where to look next"), HEADING)]
        for n in story["next"]:
            r += 1
            rows[r] = [(1, "- " + plain(n), BODY)]
        r += 2
    rows[r] = [(1, footer + (("  ·  " + brand["credit"]) if brand.get("credit") else ""), MUTED)]
    sheets.append(("Story", sheet_xml(rows, {1: 110}, hyperlinks=links)))

    # One sheet per visual.
    for n, (vid, name) in enumerate(names.items(), start=1):
        v = visuals[vid]
        sec = next(s for s in story["sections"] if s.get("visual") == vid)
        data = V.flat_rows(v)
        ncols, nrows = len(data[0]), len(data)
        rows = {1: [(1, sec.get("heading", ""), HEADING)], 2: [(1, plain(sec.get("body", "")), MUTED)]}
        for i, row in enumerate(data):
            rows[DATA_TOP + i] = [(c + 1, val, HEADER if i == 0 else (NUMBER if isinstance(val, (int, float)) and not isinstance(val, bool) else NORMAL))
                                  for c, val in enumerate(row)]
        sheet_index = len(sheets)  # 0-based position of this sheet in the workbook
        appref = "{00000000-0001-0000-%02d00-000000000000}" % sheet_index
        last = "%s%d" % (col_letter(ncols), DATA_TOP + nrows - 1)
        source = {"range": "'%s'!A%d:%s" % (name.replace("'", "''"), DATA_TOP, last), "worksheetId": appref}
        kind = "tables" if V.is_table(v) else "charts"
        if kind == "charts":
            props = {"ZBILicenseSettings": "", "ChartSettings": X.compact(V.chart_settings(v)), "SourceData": X.compact(source),
                     "ChartChooserStore": "", "zebrabi:table-data": ""}
        else:
            props = {"ZBILicenseSettings": "", "TableChooserStore": "", "SourceData": X.compact(source),
                     "ZBITablesSettings": X.compact(V.table_settings(v, "xlsx")),
                     "zebrabi:matrix-data": X.double_encoded(V.table_data(v, "xlsx"))}
        png = snapshots[vid]
        iw, ih = X.png_size(png)
        w_px = ADDIN_W_PX
        h_px = int(w_px * ih / iw)
        c0, r0 = ncols + 1, DATA_TOP - 1          # 0-based anchor: one empty column right of the data
        c1, r1 = c0 + math.ceil(w_px / COL_PX), r0 + math.ceil(h_px / ROW_PX)
        pkg.add("xl/drawings/drawing%d.xml" % n, drawing_xml(c0, r0, c1, r1, "Zebra BI %s" % kind.title(),
                                                             w_px * 9525, h_px * 9525), CT["drawing"])
        pkg.add("xl/drawings/_rels/drawing%d.xml.rels" % n,
                X.rels([("rIdWe", X.R_WEBEXT, "../webextensions/webextension%d.xml" % n),
                        ("rIdSnap", X.R_IMAGE, "../media/snapshot%d.png" % n)]))
        pkg.add("xl/webextensions/webextension%d.xml" % n, X.webextension(kind, props, snapshot_rid="rId1", appref=appref), X.CT_WEBEXT)
        pkg.add("xl/webextensions/_rels/webextension%d.xml.rels" % n, X.rels([("rId1", X.R_IMAGE, "../media/snapshot%d.png" % n)]))
        pkg.add_file("xl/media/snapshot%d.png" % n, png)
        widths = {c + 1: 16 for c in range(ncols)}
        widths[1] = max(16, min(40, max(len(str(row[0] or "")) for row in data) + 2))
        sheets.append((name, sheet_xml(rows, widths, drawing=True), n))

    wb_sheets, wb_rels = [], []
    for i, sh in enumerate(sheets, start=1):
        name, xml = sh[0], sh[1]
        pkg.add("xl/worksheets/sheet%d.xml" % i, xml, CT["sheet"])
        if len(sh) == 3:
            pkg.add("xl/worksheets/_rels/sheet%d.xml.rels" % i,
                    X.rels([("rIdDrawing", R + "drawing", "../drawings/drawing%d.xml" % sh[2])]))
        wb_sheets.append('<sheet name=%s sheetId="%d" r:id="rId%d"/>' % (X.attr(name), i, i))
        wb_rels.append(("rId%d" % i, R + "worksheet", "worksheets/sheet%d.xml" % i))
    wb_rels.append(("rIdStyles", R + "styles", "styles.xml"))
    pkg.add("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="%s" xmlns:r="%s"><bookViews><workbookView activeTab="0"/></bookViews><sheets>%s</sheets></workbook>'
            % (NS_MAIN, R[:-1], "".join(wb_sheets)), CT["workbook"])
    pkg.add("xl/_rels/workbook.xml.rels", X.rels(wb_rels))
    pkg.add("xl/styles.xml", styles(brand), CT["styles"])
    pkg.add("_rels/.rels", X.rels([("rId1", R + "officeDocument", "xl/workbook.xml"),
                                   ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
                                   ("rId3", R + "extended-properties", "docProps/app.xml")]))
    pkg.add("docProps/core.xml", X.core_props(story.get("title", "")), CT["core"])
    pkg.add("docProps/app.xml", X.app_props("Microsoft Excel"), CT["app"])
    return pkg.write(out)
