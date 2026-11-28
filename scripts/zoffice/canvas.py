"""One Excel Canvas sheet that draws the story's Zebra BI visuals as one dashboard, or one visual, bound to the cells of one
data sheet (opt-in: office.py build --canvas).

Excel stores a canvas as a chartsheet whose drawing holds a chartEx part with a <cx:dashboard>: named bindings that
point at a hidden defined name (the bound cell range) and one HTML artifact. The page reads a binding with
`await window.getDataBindingValue(key)` and hears the window event `dataBindingKeyChanged`. Structure as seen in a
canvas Excel wrote itself, on Excel on the web on 26 Nov 2026.

The canvas runs the page with no iframe and no network, so the Zebra BI renderer (the page render_visual names) is
in the page and plays its own MCP Apps host (assets/canvas-shim.js). Its five uses of the parent window are
the MCP Apps transport; they are pointed at the page itself.

The page draws one tool result: {"visual": v} (what render_visual returns) or {"dashboard": {"title", "items": [{"width": 3..12,
"visual": v}]}} (what render_dashboard returns), which the same renderer lays out on a 12-column grid, 1 to MAX_ITEMS items.
The binding stays on the cells of one visual, the bound one, which is the first item of a dashboard; the other visuals draw as built.

Excel's canvas host does not load a large page in an ordinary workbook (tried on Excel on the web, 26 Nov 2026: in one
workbook of several sheets a tiny page loaded, the same page padded with a 4.4 MB comment did not, and a page of 1.5 MB
did and drew the chart). So the renderer's one inline script ships gzip-compressed and base64-encoded inside a JavaScript
string (window.__zbiGz) and a small loader in the page unpacks it with DecompressionStream; styles and the
<app-mcp-visual> element stay as they are, and the shim stays first in the head. The canvas host drops script blocks that
are not JavaScript (type="application/octet-stream" or "application/json" come back null), so the data rides in a JS string,
never in a typed script element. The page is about 1.5 MB; MAX_PAGE_BYTES is the limit the build and the check enforce. check_renderer stops the build when the fetched renderer
no longer has exactly those five uses. The page embeds the result the build drew, so totals that do not sum (ratios,
stock measures) stay as the server drew them; when the totals are sums the page also redraws from edited cells.
Standard library only.
"""
import base64, gzip, json, os, re, zlib
from decimal import Decimal, ROUND_HALF_UP

from . import ooxml as X
from . import visual as V

KEY = "zebra"
PARENT_USES = 5
DEFINED_NAME = "_xlchart.v7.%d"
SEEN_PAGE_BYTES = 1490000      # the largest canvas page seen to load in a workbook of several sheets (Excel on the web, 26 Nov 2026)
FAILED_PAGE_BYTES = 4400000    # a page of this size did not load in the same workbook
MAX_PAGE_BYTES = 2 << 20       # the build and the check refuse a page above this
MAX_ITEMS = 6                  # render_dashboard's own cap
NOTICE = "This chart shows the figures it was built from, not the current cells. Rebuild the workbook to redraw it."
DASHBOARD_NOTICE = "The first visual shows the figures it was built from, not the current cells. Rebuild the workbook to redraw it."
SHIM = os.path.join(X.HERE, "..", "..", "assets", "canvas-shim.js")

PARENT = "window.parent"
HOST = "window.__zbiHost"
XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
HEAD = re.compile(r"<head(\s[^>]*)?>", re.I)
BODY = re.compile(r"<body(\s[^>]*)?>", re.I)
SCRIPT_TAG = re.compile(r"<script(\s[^>]*)?>", re.I)
SCRIPT_END = re.compile(r"</script\s*>", re.I)
SCRIPT_TYPE = re.compile(r"""\stype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*))""", re.I)
JS_TYPES = ("", "text/javascript", "application/javascript", "text/ecmascript", "application/ecmascript", "module")
GZ_SCRIPT = re.compile(r'<script>window\.__zbiGz="([A-Za-z0-9+/=]*)";</script>')
LOADER = ("<script>(async function(){var b=atob(window.__zbiGz);window.__zbiGz=null;var u=new Uint8Array(b.length);"
          "for(var i=0;i<b.length;i++)u[i]=b.charCodeAt(i);var js=await new Response(new Blob([u]).stream()"
          ".pipeThrough(new DecompressionStream('gzip'))).text();var s=document.createElement('script');"
          "s.textContent=js;document.body.appendChild(s);})();</script>")
SCRIPT_SRC = re.compile(r"<script[^>]*\ssrc\s*=", re.I)
# scenario name in chart_value_columns -> (field in the visual, key in grand_total)
FIELDS = {"Val": ("ycol_ac", "ac"), "FC": ("ycol_fc", "fc"), "PL": ("ycol_pl", "pl"), "PY": ("ycol_py", "py")}

NS_CX = "http://schemas.microsoft.com/office/drawing/2014/chartex"
R = X.NS_R + "/"
REL_CHARTSHEET = R + "chartsheet"
REL_DRAWING = R + "drawing"
REL_CHART = R + "chart"
REL_CHARTEX = "http://schemas.microsoft.com/office/2014/relationships/chartEx"
REL_SHAPES = R + "chartUserShapes"
CT_CHARTSHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.chartsheet+xml"
CT_CHARTEX = "application/vnd.ms-office.chartex+xml"
CT_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
CT_SHAPES = "application/vnd.openxmlformats-officedocument.drawingml.chartshapes+xml"
CT_DRAWING = "application/vnd.openxmlformats-officedocument.drawing+xml"


def check_renderer(html):
    """Stop, with one line, unless the renderer is what the canvas page was written for. Raises ValueError."""
    def stop(found):
        raise ValueError("canvas: the Zebra BI renderer changed (%s); build without --canvas" % found)

    n = html.count(PARENT)
    if n != PARENT_USES:
        stop("%d %s uses, expected %d" % (n, PARENT, PARENT_USES))
    if not HEAD.search(html[:65536]):
        stop("no <head> tag in its first 64 KB")
    for needle in ("ui/initialize", "ui/notifications/tool-result"):
        if needle not in html:
            stop("no %s" % needle)
    if "<iframe" in html.lower():
        stop("it contains an <iframe>, which Excel refuses on a canvas")
    if SCRIPT_SRC.search(html):
        stop("it loads a script from an address, and a canvas has no network")
    bad = XML_ILLEGAL.search(html)
    if bad:
        stop("it contains U+%04X, which an XML part cannot carry" % ord(bad.group(0)))
    tags, ends = list(SCRIPT_TAG.finditer(html)), html.lower().count("</script")
    if len(tags) != 1 or ends != 1:
        stop("%d script blocks, expected one inline script" % len(tags))
    body = BODY.search(html)
    if tags[0].group(1) or not body or body.start() > tags[0].start():
        stop("its one script is not a plain <script> after <body>")


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def _decimals(x):
    exp = Decimal(repr(x)).normalize().as_tuple().exponent
    return min(6, max(0, -exp)) if isinstance(exp, int) else 0


def additive(v):
    """True when every total the visual shows is the sum of its rows, so a changed cell can be redrawn with a new total."""
    if V.is_table(v) or v.get("visual_type") not in (None, "Chart"):
        return False
    xcol = v.get("xcol") or []
    if not xcol or any(n.get("children") for n in xcol):
        return False
    if v.get("top_n") not in (None, -1) or v.get("additional_measures") or v.get("measure_aggregations"):
        return False
    if v.get("group_col_names") or v.get("group_columns"):
        return False
    cols = V.chart_value_columns(v)
    if not cols:
        return False
    gt = v.get("grand_total") or {}
    for c in cols:
        vals = [n.get("value") for n in c["data"]]
        if len(vals) != len(xcol) or not all(_number(x) for x in vals):
            return False
        total = gt.get(FIELDS[c["name"]][1])
        if total is None:
            continue
        if not _number(total):
            return False
        places = max(_decimals(x) for x in vals)
        if abs(sum(vals) - total) > 0.5 * len(vals) * 10 ** -places + 1e-9 * abs(total):
            return False
    return True


def columns(v):
    """Where each scenario sits in the cells of the data sheet: [{field, total, col}], from chart_value_columns (none for a table,
    which is never redrawn from its cells)."""
    if V.is_table(v):
        return []
    first = max(1, len(v.get("xcol_name") or []))
    return [{"field": FIELDS[c["name"]][0], "total": FIELDS[c["name"]][1], "col": first + i}
            for i, c in enumerate(V.chart_value_columns(v))]


def _accepted(x):
    """The strings a cell holding x may read as: its plain value, then the way a #,##0 cell shows it."""
    plain = repr(x)
    out = [plain]
    try:
        shown = Decimal(plain).quantize(Decimal(1), ROUND_HALF_UP)
        out.append(format(int(shown), ","))
        if x < 0 and shown == 0:
            out.append("-0")
    except ArithmeticError:
        pass
    return out


def cells(v):
    """The cells the build writes for the visual (zoffice.visual.flat_rows): text, and for a number the strings it may read as."""
    rows = V.flat_rows(v)
    return [[_accepted(val) if i and _number(val) else ("" if val is None else str(val)) for val in row]
            for i, row in enumerate(rows)]


def _embed(obj):
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")


def is_dashboard(content):
    return isinstance(content, dict) and "dashboard" in content


def structured(content):
    """The structuredContent the page draws: a dashboard as it is, a visual wrapped as {"visual": v}."""
    return content if is_dashboard(content) else {"visual": content}


def bound_visual(content):
    """The visual whose cells the binding reads: the first item of a dashboard, else the visual itself."""
    return content["dashboard"]["items"][0]["visual"] if is_dashboard(content) else content


def is_wide(v, args=None):
    """A chart over time draws across the whole row (12 columns); a table or any other chart takes half (6). A time axis is
    what the story's call asked for (args.time_axis) or what the result marks as a date category."""
    if V.is_table(v):
        return False
    return bool((args or {}).get("time_axis")) or any(v.get("is_date_category") or [])


def layout(wide):
    """Widths for visuals in order, from True (a whole row) and False (half a row): every row sums to 12, and a half-width
    visual with no partner in its row (before a wide one or last) takes the whole row."""
    out, pending = [], None
    for i, w in enumerate(wide):
        if w:
            if pending is not None:
                out[pending] = 12
                pending = None
            out.append(12)
        elif pending is None:
            pending = i
            out.append(6)
        else:
            out.append(6)
            pending = None
    if pending is not None:
        out[pending] = 12
    return out


def dashboard(title, entries):
    """The render_dashboard structuredContent for [(visual, wide)] in order, at most MAX_ITEMS."""
    if not 1 <= len(entries) <= MAX_ITEMS:
        raise ValueError("canvas: a dashboard holds 1 to %d visuals, not %d" % (MAX_ITEMS, len(entries)))
    widths = layout([w for _, w in entries])
    return {"dashboard": {"title": title, "items": [{"width": w, "visual": v} for w, (v, _) in zip(widths, entries)]}}


def result(content):
    return {"content": [{"type": "text", "text": ""}], "structuredContent": structured(content), "isError": False}


def shim(content, key=KEY, version=None):
    """assets/canvas-shim.js with its placeholders filled for one visual, or for a dashboard (bound to its first visual)."""
    if version is None:
        import story  # scripts/, which holds zoffice, is on the path wherever zoffice imports
        version = story.version()
    v = bound_visual(content)
    text = X.read(SHIM)
    fill = {"__KEY__": key, "__RESULT__": result(content), "__CELLS__": cells(v), "__COLUMNS__": columns(v),
            "__ADDITIVE__": additive(v), "__NOTICE__": DASHBOARD_NOTICE if is_dashboard(content) else NOTICE,
            "__VERSION__": version}
    for token in fill:
        if text.count(token) != 1:
            raise ValueError("canvas-shim.js must hold the placeholder %s exactly once" % token)
    # One pass, so text that was put in is never searched for placeholders again.
    return re.sub("|".join(re.escape(t) for t in fill), lambda m: _embed(fill[m.group(0)]), text)


BUILT = re.compile(r"\bBUILT = ")


def built_result(text):
    """The tool result the page embeds (the shim's BUILT) from the page text, or None when the page has none. Raises ValueError
    when it is not JSON."""
    m = BUILT.search(text)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, m.end())[0]
    except ValueError as e:
        raise ValueError("the embedded result is not JSON (%s)" % e)


def result_error(res):
    """Why a built result is not one the renderer draws ({"visual": v} or {"dashboard": {"items": [1 to MAX_ITEMS items]}}),
    or None when it is."""
    sc = res.get("structuredContent") if isinstance(res, dict) else None
    if not isinstance(sc, dict):
        return "the embedded result has no structuredContent"
    if "dashboard" in sc:
        items = (sc["dashboard"] or {}).get("items") if isinstance(sc["dashboard"], dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            return "the embedded dashboard holds %s items, expected 1 to %d" % (len(items) if isinstance(items, list) else "no", MAX_ITEMS)
        for i, it in enumerate(items, start=1):
            w = it.get("width") if isinstance(it, dict) else None
            if not isinstance(it, dict) or not isinstance(it.get("visual"), dict):
                return "dashboard item %d has no visual" % i
            if isinstance(w, bool) or not isinstance(w, int) or not 3 <= w <= 12:
                return "dashboard item %d has width %r, expected a whole number from 3 to 12" % (i, w)
        return None
    if not isinstance(sc.get("visual"), dict):
        return "the embedded result holds neither a visual nor a dashboard"
    return None


def pack(js):
    """The renderer script as a base64 text of its gzip (level 9, no timestamp, so a build is repeatable)."""
    return base64.b64encode(gzip.compress(js.encode("utf-8"), 9, mtime=0)).decode("ascii")


def unpack(page):
    """(the page without its packed script, the renderer script as the page unpacks it or None when the page is not packed).
    Raises ValueError when the packed text is not base64 of a gzip of UTF-8."""
    m = GZ_SCRIPT.search(page)
    if not m:
        return page, None
    try:
        js = gzip.decompress(base64.b64decode(m.group(1), validate=True)).decode("utf-8")
    except (ValueError, OSError, EOFError, zlib.error) as e:
        raise ValueError("the packed renderer script cannot be unpacked (%s)" % e)
    return page[:m.start()] + page[m.end():], js


def size_error(n):
    return ("canvas: the page is %.1f MB, over the %.0f MB limit. Excel on the web did not load a page of %.1f MB in a workbook "
            "of several sheets and loaded one of %.1f MB; build without --canvas"
            % (n / 1048576, MAX_PAGE_BYTES / 1048576, FAILED_PAGE_BYTES / 1e6, SEEN_PAGE_BYTES / 1e6))


def page(renderer_html, content, key=KEY, version=None):
    """content: a visual, or a dashboard from dashboard(). The canvas page: the host shim first in the head, the renderer's parent-window uses pointed at the page, and the
    renderer's one inline script packed (gzip + base64 in a JavaScript string) with a loader that unpacks it in place."""
    check_renderer(renderer_html)
    head = HEAD.search(renderer_html[:65536]).end()
    rest = renderer_html[head:].replace(PARENT, HOST)
    start = SCRIPT_TAG.search(rest)
    end = SCRIPT_END.search(rest, start.end())
    packed = '<script>window.__zbiGz="' + pack(rest[start.end():end.start()]) + '";</script>'
    out = (renderer_html[:head] + "<script>" + shim(content, key, version) + "</script>"
           + rest[:start.start()] + packed + LOADER + rest[end.end():])
    size = len(out.encode("utf-8"))
    if size > MAX_PAGE_BYTES:
        raise ValueError(size_error(size))
    return out


def escape_text(html):
    """XML character data for the artifact: & < > escaped, and a carriage return as a reference so it survives parsing."""
    return html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")


def defined_name(index, range_ref):
    return '<definedName name="%s" hidden="1">%s</definedName>' % (DEFINED_NAME % index, X.t(range_ref))


def chartsheet_path(index):
    return "xl/chartsheets/sheet%d.xml" % (index + 1)


def parts(html, data_sheet, drawing_no, index=0, key=KEY):
    """The package parts of one canvas sheet: [(path, data, content type or None)].

    drawing_no is the first of two free xl/drawings numbers (the frame, then the shapes of the fallback chart)."""
    n = index + 1
    frame, shapes = "xl/drawings/drawing%d.xml" % drawing_no, "xl/drawings/drawing%d.xml" % (drawing_no + 1)
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    chartsheet = (head + '<chartsheet xmlns="%s" xmlns:r="%s"><sheetPr/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
                  '<pageMargins left="0" right="0" top="0" bottom="0" header="0" footer="0"/><drawing r:id="rId1"/></chartsheet>'
                  % ("http://schemas.openxmlformats.org/spreadsheetml/2006/main", X.NS_R))
    drawing = (head + '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
               'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><xdr:absoluteAnchor><xdr:pos x="0" y="0"/>'
               '<xdr:ext cx="9753600" cy="7315200"/><mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
               '<mc:Choice xmlns:cx7="http://schemas.microsoft.com/office/drawing/2016/5/13/chartex" Requires="cx7">'
               '<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="2" name=""/><xdr:cNvGraphicFramePr><a:graphicFrameLocks/>'
               '</xdr:cNvGraphicFramePr></xdr:nvGraphicFramePr><xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm><a:graphic>'
               '<a:graphicData uri="http://schemas.microsoft.com/office/drawing/2014/chartex"><cx:chart xmlns:cx="%s" '
               'xmlns:r="%s" r:id="rId1"/></a:graphicData></a:graphic></xdr:graphicFrame></mc:Choice><mc:Fallback>'
               '<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="0" name=""/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>'
               '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm><a:graphic>'
               '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart '
               'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:r="%s" r:id="rId2"/></a:graphicData></a:graphic>'
               '</xdr:graphicFrame></mc:Fallback></mc:AlternateContent><xdr:clientData/></xdr:absoluteAnchor></xdr:wsDr>'
               % (NS_CX, X.NS_R, X.NS_R))
    chartex = (head.rstrip("\n") + '<cx:chartSpace xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="%s" '
               'version="1.1" xmlns:cx="%s"><cx:chartData><cx:data id="0"><cx:strDim type="cat"><cx:f>%s</cx:f></cx:strDim></cx:data>'
               '</cx:chartData><cx:dashboard entrypoint="index"><cx:bindings><cx:binding name=%s id="0" type="cat"/></cx:bindings>'
               '<cx:artifacts><cx:artifact name="index" type="html">%s</cx:artifact></cx:artifacts></cx:dashboard></cx:chartSpace>'
               % (X.NS_R, NS_CX, DEFINED_NAME % index, X.attr(key), escape_text(html)))
    chart = (head + '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="%s"><c:date1904 val="0"/><c:lang val="en-US"/>'
             '<c:roundedCorners val="0"/><c:style val="2"/><c:chart><c:autoTitleDeleted val="0"/><c:plotArea><c:layout/><c:barChart>'
             '<c:barDir val="col"/><c:grouping val="clustered"/><c:varyColors val="0"/><c:dLbls><c:showLegendKey val="0"/>'
             '<c:showVal val="0"/><c:showCatName val="0"/><c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/>'
             '</c:dLbls><c:gapWidth val="150"/><c:axId val="1"/><c:axId val="2"/></c:barChart><c:catAx><c:axId val="1"/><c:scaling>'
             '<c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="b"/><c:majorTickMark val="out"/>'
             '<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="2"/><c:crosses val="autoZero"/><c:auto val="1"/>'
             '<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx><c:valAx><c:axId val="2"/><c:scaling>'
             '<c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="l"/><c:majorGridlines/><c:majorTickMark val="out"/>'
             '<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="1"/><c:crosses val="autoZero"/>'
             '<c:crossBetween val="between"/></c:valAx></c:plotArea><c:legend><c:legendPos val="r"/><c:overlay val="0"/></c:legend>'
             '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/><c:showDLblsOverMax val="0"/></c:chart><c:printSettings><c:headerFooter/>'
             '<c:pageMargins b="0.75" l="0.7" r="0.7" t="0.75" header="0.3" footer="0.3"/><c:pageSetup/></c:printSettings>'
             '<c:userShapes r:id="rId1"/></c:chartSpace>' % X.NS_R)
    note = ('This is an Excel Canvas sheet drawn by Zebra BI. Your version of Excel does not show canvas sheets; '
            'the data is on the sheet "%s".' % data_sheet)
    user_shapes = ('<c:userShapes xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
                   'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><cdr:relSizeAnchor '
                   'xmlns:cdr="http://schemas.openxmlformats.org/drawingml/2006/chartDrawing"><cdr:from><cdr:x>0</cdr:x><cdr:y>0</cdr:y></cdr:from>'
                   '<cdr:to><cdr:x>1</cdr:x><cdr:y>1</cdr:y></cdr:to><cdr:sp macro="" textlink=""><cdr:nvSpPr><cdr:cNvPr id="2" name=""/>'
                   '<cdr:cNvSpPr><a:spLocks noTextEdit="1"/></cdr:cNvSpPr></cdr:nvSpPr><cdr:spPr><a:xfrm><a:off x="0" y="0"/>'
                   '<a:ext cx="0" cy="0"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:prstClr val="white"/></a:solidFill>'
                   '</cdr:spPr><cdr:txBody><a:bodyPr vertOverflow="clip" horzOverflow="clip"/><a:lstStyle/><a:p><a:r><a:rPr sz="1100"/>'
                   '<a:t>%s</a:t></a:r></a:p></cdr:txBody></cdr:sp></cdr:relSizeAnchor></c:userShapes>' % X.t(note))
    return [
        (chartsheet_path(index), chartsheet, CT_CHARTSHEET),
        ("xl/chartsheets/_rels/sheet%d.xml.rels" % n, X.rels([("rId1", REL_DRAWING, "../drawings/drawing%d.xml" % drawing_no)]), None),
        (frame, drawing, CT_DRAWING),
        ("xl/drawings/_rels/drawing%d.xml.rels" % drawing_no,
         X.rels([("rId1", REL_CHARTEX, "../charts/chartEx%d.xml" % n), ("rId2", REL_CHART, "../charts/chart%d.xml" % n)]), None),
        ("xl/charts/chartEx%d.xml" % n, chartex, CT_CHARTEX),
        ("xl/charts/chart%d.xml" % n, chart, CT_CHART),
        ("xl/charts/_rels/chart%d.xml.rels" % n, X.rels([("rId1", REL_SHAPES, "../drawings/drawing%d.xml" % (drawing_no + 1))]), None),
        (shapes, user_shapes, CT_SHAPES),
    ]
