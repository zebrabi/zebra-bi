#!/usr/bin/env python3
"""pdf.py: print a Zebra BI data story as an A4 PDF with its Zebra BI charts.

  python3 -I pdf.py build <story-dir> [--brand <dir>]

`build` reuses story.py: every visual in story.json is rendered (or reused from visuals/<id>.json),
then the branded page (story.assemble) is opened in headless Chrome, Edge or Chromium
(ZEBRA_BI_CHROME picks one) at the A4 printable width, so the Zebra BI renderer draws each chart
at the width it is printed at. Once every chart has drawn and held its size, the page is printed to
<slug>.pdf in the story folder. <story-dir> may also be a report builder folder (report.zbreport): its
rows put up to three charts or texts side by side, each chart drawn at its cell's width. Chart labels and values stay text in the PDF. The page follows the
brand (brand.py: .zebra/brand/, else the editorial default); the charts never change.

Without a browser, or when a chart does not draw, build stops with one line and writes no PDF.
Standard library only.
"""
import base64, html, json, os, re, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brand as B  # noqa: E402
import story as S  # noqa: E402
from zoffice import cdp, snapshot as SN  # noqa: E402

WIDTH = 680  # A4 (210 mm) minus 15 mm margins each side, in CSS px; the template's main is this wide
CHART_TIMEOUT = 30
FRAMES = "document.querySelectorAll('iframe[data-v]')"


def footer_template(brand):
    color = (brand.get("colors") or {}).get("muted") or "#616161"
    color = color if re.fullmatch(r"#[0-9A-Fa-f]{3,8}", str(color)) else "#616161"
    return ('<div style="width:100%%;margin:0 15mm;font:8px sans-serif;color:%s;text-align:right">'
            '<span class="pageNumber"></span> / <span class="totalPages"></span></div>' % html.escape(color))


def wait_charts(ch, timeout=None):
    """None once every chart is ready and no height has changed for a second, else the id of a chart that did not draw.
    Chrome draws an offscreen frame only once it is in view, so each frame is scrolled into view in turn."""
    timeout = timeout or CHART_TIMEOUT
    deadline = time.time() + timeout
    while ch.evaluate("document.readyState") != "complete":
        if time.time() > deadline:
            return "(page)"
        time.sleep(0.1)
    for i in range(ch.evaluate(FRAMES + ".length") or 0):
        frame = "%s[%d]" % (FRAMES, i)
        ch.evaluate(frame + ".scrollIntoView({block:'center'})")
        deadline = time.time() + timeout
        while ch.evaluate(frame + ".getAttribute('data-status')") != "ready":
            if time.time() > deadline:
                return ch.evaluate(frame + ".dataset.v")
            time.sleep(0.1)
    state = "JSON.stringify(Array.from(%s).map(function(f){return f.style.height+'|'+f.getAttribute('data-status')}))" % FRAMES
    deadline, last, since = time.time() + timeout, None, time.time()
    while time.time() < deadline:
        now = ch.evaluate(state)
        if now != last:
            last, since = now, time.time()
        elif time.time() - since >= 1.0 and all(s.endswith("|ready") for s in json.loads(now)):
            break
        time.sleep(0.2)
    else:
        return "(charts kept changing size)"
    ch.evaluate("window.scrollTo(0,0)")
    ch.call("Runtime.evaluate", expression="document.fonts.ready.then(function(){return true})", awaitPromise=True)
    return None


def trim_row_charts(ch):
    """Cut each chart in a report row to where its drawing ends. The renderer reports a minimum height, so a short
    table would leave a band of white between the chart and the note under it. Read from a small screenshot of
    the frame, as the artifact pictures are cropped (the frames' documents are not readable from the page).
    Chrome paints a frame only while it is in view, so each is scrolled into view first."""
    sel = "document.querySelectorAll('section.row iframe[data-v]')"
    for i in range(ch.evaluate(sel + ".length") or 0):
        frame = "%s[%d]" % (sel, i)
        ch.evaluate(frame + ".scrollIntoView({block:'start'})")
        time.sleep(0.3)
        x, y, w, h = json.loads(ch.evaluate("(function(r){return JSON.stringify([r.left+scrollX,r.top+scrollY,r.width,r.height])})"
                                            "(%s.getBoundingClientRect())" % frame))
        if not w or not h:
            continue
        shot = ch.call("Page.captureScreenshot", format="png", clip={"x": x, "y": y, "width": w, "height": h, "scale": 0.25})
        bottom = SN._content_bottom(base64.b64decode(shot["data"]), 0.25)
        if bottom and bottom + 16 < h:
            ch.evaluate("%s.style.height='%dpx'" % (frame, bottom + 16))
    ch.evaluate("window.scrollTo(0,0)")


def pages(pdf):
    return len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", pdf))


def print_page(page_html, binary, brand):
    """The page printed to PDF bytes by headless Chrome."""
    with tempfile.TemporaryDirectory(prefix="zebra-bi-pdf-") as tmp:
        path = os.path.join(tmp, "story.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(page_html)
        try:
            ch = cdp.Chrome(binary, os.path.join(tmp, "profile"))
        except RuntimeError as e:
            sys.exit("%s could not be started (%s); no PDF was written." % (os.path.basename(binary), e))
        try:
            ch.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=1000, deviceScaleFactor=1, mobile=False)
            ch.call("Page.navigate", url="file://" + path)
            failed = wait_charts(ch)
            if failed:
                sys.exit("Chart %s did not draw in the browser, so no PDF was written. Build again; if it repeats, "
                         "the Zebra BI renderer may have changed." % failed)
            trim_row_charts(ch)
            res = ch.call("Page.printToPDF", preferCSSPageSize=True, printBackground=True, displayHeaderFooter=True,
                          headerTemplate="<span></span>", footerTemplate=footer_template(brand))
        finally:
            ch.close()
    return base64.b64decode(res["data"])


def build(story_dir, brand_dir=None):
    brand = B.load(story_dir, brand_dir)
    errors, warnings = B.check(brand)
    for w in warnings:
        print("brand warning: " + w, file=sys.stderr)
    if errors:
        sys.exit("brand %r is not valid: %s" % (brand.get("name"), "; ".join(errors)))
    binary = SN.find_chrome()
    if not binary:
        sys.exit("The PDF needs Google Chrome, Microsoft Edge or Chromium to draw the charts, and none was found "
                 "(set ZEBRA_BI_CHROME to its path). No PDF was written.")
    story = S.load(story_dir)
    results = S.render_visuals(story_dir, story)
    renderer_path, meta = S.renderer()
    pdf = print_page(S.assemble(story, results, meta, renderer_path, brand), binary, brand)
    n = pages(pdf)
    if not pdf.startswith(b"%PDF-") or not n:
        sys.exit("The browser returned no usable PDF, so none was written.")
    out = os.path.join(story_dir, os.path.basename(os.path.normpath(story_dir)) + ".pdf")
    S.write_atomic(out, pdf)
    print(json.dumps({"file": out, "bytes": len(pdf), "pages": n, "brand": brand.get("name"), "visuals": len(results),
                      "renderer_sha256": meta["sha256"][:12]}, indent=2))
    return out


def main(argv):
    if len(argv) < 2 or argv[0] != "build":
        sys.exit(__doc__)
    bdir = os.path.abspath(argv[argv.index("--brand") + 1]) if "--brand" in argv else None
    build(os.path.abspath(argv[1]), bdir)


if __name__ == "__main__":
    main(sys.argv[1:])
