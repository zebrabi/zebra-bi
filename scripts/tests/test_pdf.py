"""Tests for pdf.py. Run: python3 -I -m unittest discover <plugin>/scripts/tests

The build tests print a real PDF in headless Chrome (skipped when none is installed) without any
network: the visuals and a stand-in renderer are cached in a temp folder. The stand-in answers the
MCP Apps handshake, draws an SVG of a set height (a title on top, a rule at the bottom) when it gets its
tool result and reports that size, so page breaks are predictable.
"""
import contextlib, datetime as dt, hashlib, io, json, os, re, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
import pdf as P  # noqa: E402
import story as S  # noqa: E402
from zoffice import snapshot as SN  # noqa: E402

FAKE_RENDERER = """<!doctype html><html><body style="margin:0"><div id="c"></div><script>
var H = %s, W = 0;
function send(m) { parent.postMessage(m, '*'); }
window.addEventListener('message', function (e) {
  var m = e.data; if (!m) return;
  if (m.id === 1 && m.result) {
    W = m.result.hostContext.viewport.width;  // printed after the title: the width the chart was drawn at
    send({ jsonrpc: '2.0', method: 'notifications/initialized' });
  }
  else if (m.method === 'ui/notifications/tool-result' && H) {
    var t = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    t.setAttribute('width', '600'); t.setAttribute('height', String(H));
    t.innerHTML = '<text x="10" y="40">' + m.params.structuredContent.visual.title + ' w' + W + '</text>'
      + '<rect x="0" y="' + (H - 4) + '" width="600" height="2"/>';  // the drawing reaches the bottom, so trimming keeps it
    document.getElementById('c').appendChild(t);
    send({ jsonrpc: '2.0', method: 'ui/notifications/size-changed', params: { height: H } });
  }
});
send({ jsonrpc: '2.0', id: 1, method: 'ui/initialize', params: {} });
</script></body></html>"""

ARGS = {"v%d" % i: {"title": "Chart %d" % i, "rows": [i]} for i in (1, 2, 3)}
STORY = {
    "title": "Sales grew", "subtitle": "AC vs PY", "kpis": [{"value": "$1k", "label": "sales"}],
    "sections": [{"heading": "Finding %d" % i, "body": "Body %d." % i, "visual": "v%d" % i} for i in (1, 2, 3)],
    "visuals": {vid: {"args": a} for vid, a in ARGS.items()},
    "next": ["Look at **this**."], "source": {"label": "Test data"},
}
# The report builder's layout: a chart beside its message, then three cells side by side.
ROWS = [{"cells": [{"visual": "v1", "note": "V1 rose **+5%**."}, {"text": "Key **message**."}]},
        {"cells": [{"visual": "v2"}, {"visual": "v3"}, {"text": "Note."}]}]


def rows_story():
    return dict(json.loads(json.dumps(STORY)), sections=[], rows=json.loads(json.dumps(ROWS)))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = S.CACHE, P.CHART_TIMEOUT
        S.CACHE = os.path.join(self.tmp, "cache", "renderer")
        self.story_dir = os.path.join(self.tmp, "q4-sales")
        vdir = os.path.join(self.story_dir, "visuals")
        os.makedirs(vdir)
        with open(os.path.join(self.story_dir, "story.json"), "w") as f:
            json.dump(STORY, f)
        for vid, a in ARGS.items():
            res = {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"visual": {"title": a["title"]}}}
            with open(os.path.join(vdir, vid + ".json"), "w") as f:
                json.dump({"args_hash": S.args_hash(a), "result": res}, f)
        self.renderer(750)

    def tearDown(self):
        S.CACHE, P.CHART_TIMEOUT = self.saved
        shutil.rmtree(self.tmp)

    def renderer(self, height):
        """Cache a stand-in renderer that draws charts `height` px tall (0: never draws)."""
        os.makedirs(S.CACHE, exist_ok=True)
        data = (FAKE_RENDERER % height).encode()
        with open(os.path.join(S.CACHE, "visual.html"), "wb") as f:
            f.write(data)
        with open(os.path.join(S.CACHE, "visual.json"), "w") as f:
            json.dump({"sha256": hashlib.sha256(data).hexdigest(), "endpoint": S.ENDPOINT, "fetched_at": dt.datetime.now().isoformat()}, f)

    def build(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            P.build(self.story_dir)
        return json.loads(out.getvalue()[out.getvalue().index("{"):])

    def pdf_path(self):
        return os.path.join(self.story_dir, "q4-sales.pdf")


class Page(Base):
    def test_page_is_a4_with_a_frame_per_visual_section(self):
        story = json.loads(json.dumps(STORY))
        with contextlib.redirect_stdout(io.StringIO()):
            results = S.render_visuals(self.story_dir, story)
        path, meta = S.renderer()
        page = S.assemble(story, results, meta, path, B.load(start=self.tmp))
        self.assertIn("@page{size:A4;margin:16mm 15mm}", page)
        self.assertIn("break-inside:avoid-page", page)
        self.assertIn("break-after:avoid", page)
        for vid in ARGS:
            self.assertIn('<iframe data-v="%s"' % vid, page)

    def test_rows_put_cells_side_by_side(self):
        story = rows_story()
        with contextlib.redirect_stdout(io.StringIO()):
            results = S.render_visuals(self.story_dir, story)
        path, meta = S.renderer()
        page = S.assemble(story, results, meta, path, B.load(start=self.tmp))
        self.assertIn('<section class="row cols-2"><div class="cell"><iframe data-v="v1" title="Chart 1"></iframe>'
                      '<div class="note"><p>V1 rose <b>+5%</b>.</p></div></div>'
                      '<div class="cell"><p>Key <b>message</b>.</p></div></section>', page)
        self.assertIn('<section class="row cols-3"><div class="cell"><iframe data-v="v2"', page)
        self.assertIn("section.row.cols-3{grid-template-columns:repeat(3,minmax(0,1fr))}", page)
        self.assertEqual(S.story_visuals(story), ["v1", "v2", "v3"])

    def test_a_chart_message_sits_above_its_chart(self):
        story = rows_story()
        story["rows"][0]["cells"][0]["message"] = "V1 rose **+5%**"
        with contextlib.redirect_stdout(io.StringIO()):
            results = S.render_visuals(self.story_dir, story)
        path, meta = S.renderer()
        page = S.assemble(story, results, meta, path, B.load(start=self.tmp))
        self.assertIn('<div class="cell"><div class="msg">V1 rose <b>+5%</b></div><iframe data-v="v1"', page)
        self.assertIn("section.row .msg{", page)

    def page(self, layout):
        story = rows_story()
        if layout:
            story["layout"] = layout
        with contextlib.redirect_stdout(io.StringIO()):
            results = S.render_visuals(self.story_dir, story)
        path, meta = S.renderer()
        return S.assemble(story, results, meta, path, B.load(start=self.tmp))

    def test_a_board_pack_starts_a_page_at_every_chart_row(self):
        page = self.page("board-pack")
        self.assertIn('<body class="layout-board-pack">', page)
        self.assertIn('<section class="row cols-2 page">', page)
        self.assertIn('<section class="row cols-3 page">', page)
        self.assertIn(".layout-board-pack section.row.page{break-before:page", page)
        self.assertIn("@page{size:A4;margin:16mm 15mm}", page)

    def test_a_one_pager_prints_landscape(self):
        page = self.page("one-pager")
        self.assertIn('<body class="layout-one-pager">', page)
        self.assertIn("@page{size:A4 landscape;margin:16mm 15mm}", page)
        self.assertIn('<section class="row cols-2">', page)  # no page breaks

    def test_no_layout_or_an_unknown_one_prints_as_before(self):
        for layout in (None, "variance-walk", "grid\" onload=\"x"):
            page = self.page(layout)
            self.assertIn("@page{size:A4;margin:16mm 15mm}", page)
            self.assertNotIn(" page\">", page)
            self.assertNotIn("onload", page)
        self.assertIn("<body>", self.page(None))

    def test_a_row_holds_one_to_three_cells(self):
        for cells in ([], [{"visual": "v1"}] * 4, [{"heading": "no visual or text"}]):
            with self.subTest(cells=cells), self.assertRaises(SystemExit):
                S.rows({"rows": [{"cells": cells}]})

    def test_footer_names_the_source_only_when_there_is_one(self):
        self.assertTrue(S.footer(STORY, {}).startswith("Source: Test data. Built "))
        self.assertTrue(S.footer({"title": "Report"}, {}).startswith("Built "))

    def test_footer_colour_is_a_hex_colour_or_the_default(self):
        self.assertIn("color:#123456", P.footer_template({"colors": {"muted": "#123456"}}))
        self.assertIn("color:#616161", P.footer_template({"colors": {"muted": "red;background:url(x)"}}))

    def test_no_browser_stops_before_anything_is_sent(self):
        saved = os.environ.get("ZEBRA_BI_CHROME")
        os.environ["ZEBRA_BI_CHROME"] = os.path.join(self.tmp, "no-such-browser")
        try:
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()) as out:
                P.build(self.story_dir)
        finally:
            if saved is None:
                del os.environ["ZEBRA_BI_CHROME"]
            else:
                os.environ["ZEBRA_BI_CHROME"] = saved
        self.assertTrue(str(cm.exception.code).startswith("The PDF needs Google Chrome, Microsoft Edge or Chromium"))
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(os.path.exists(self.pdf_path()))


@unittest.skipUnless(SN.find_chrome(), "no Chrome, Edge or Chromium installed")
class Build(Base):
    def test_one_page_per_tall_chart_and_none_split(self):
        # Each section (heading, body, 750 px chart) fits a page but two do not, and the first does not
        # fit under the title block: title page, one page per section, then "where to look next" with the footer.
        info = self.build()
        with open(self.pdf_path(), "rb") as f:
            pdf = f.read()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(info["pages"], 5)
        self.assertEqual(P.pages(pdf), 5)
        self.assertEqual(info["visuals"], 3)
        if shutil.which("pdftotext"):
            import subprocess
            for page, label in ((2, "Chart 1"), (3, "Chart 2"), (4, "Chart 3")):
                text = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), self.pdf_path(), "-"],
                                      capture_output=True, text=True).stdout
                self.assertIn(label, text)  # the chart's own text is text in the PDF
                self.assertIn("Finding %d" % (page - 1), text)  # and its heading is on the same page
            last = subprocess.run(["pdftotext", "-f", "5", "-l", "5", self.pdf_path(), "-"], capture_output=True, text=True).stdout
            self.assertIn("Where to look next", last)
            self.assertIn("Source: Test data.", last)

    @unittest.skipUnless(shutil.which("pdftotext"), "needs pdftotext")
    def test_rows_print_side_by_side(self):
        import subprocess
        self.renderer(300)
        with open(os.path.join(self.story_dir, "story.json"), "w") as f:
            json.dump(rows_story(), f)
        info = self.build()
        self.assertEqual(info["visuals"], 3)
        text = subprocess.run(["pdftotext", "-layout", self.pdf_path(), "-"], capture_output=True, text=True).stdout
        line = lambda label: next(i for i, l in enumerate(text.splitlines()) if label in l)
        self.assertEqual(line("Chart 2"), line("Chart 3"))  # same row, so the same printed line
        self.assertNotEqual(line("Chart 1"), line("Chart 2"))

    def write_story(self, layout, rows=None):
        story = rows_story()
        story["layout"] = layout
        if rows is not None:
            story["rows"] = rows
        with open(os.path.join(self.story_dir, "story.json"), "w") as f:
            json.dump(story, f)

    @unittest.skipUnless(shutil.which("pdftotext"), "needs pdftotext")
    def test_a_board_pack_prints_a_cover_then_a_page_per_chart_row(self):
        import subprocess
        self.renderer(200)
        self.write_story("board-pack")
        info = self.build()
        self.assertEqual(info["pages"], 3)  # the cover, then one page for each of the two chart rows
        page = lambda n: subprocess.run(["pdftotext", "-f", str(n), "-l", str(n), self.pdf_path(), "-"],
                                        capture_output=True, text=True).stdout
        self.assertIn("Sales grew", page(1))
        self.assertNotIn("Chart 1", page(1))
        self.assertIn("Chart 1", page(2))
        self.assertIn("Chart 2", page(3))
        self.assertIn("Source: Test data.", page(3))  # the footer stays with the last chart

    def test_a_one_pager_fits_one_landscape_page(self):
        self.renderer(300)
        self.write_story("one-pager")
        info = self.build()
        self.assertEqual((info["pages"], info["one_page"]), (1, True))
        self.assertTrue(P.MIN_SCALE <= info["scale"] < 1, info["scale"])
        with open(self.pdf_path(), "rb") as f:
            box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", f.read())
        self.assertGreater(float(box.group(1)), float(box.group(2)))  # landscape: wider than tall

    @unittest.skipUnless(shutil.which("pdftotext"), "needs pdftotext")
    def test_a_scaled_one_pager_draws_its_charts_wider(self):
        # Charts keep their height when drawn wider, so a one-pager that has to shrink is laid out as wide as the
        # page allows at that scale: its charts get room for their labels and nothing prints smaller.
        import subprocess
        self.renderer(300)
        self.write_story("one-pager")
        info = self.build()
        self.assertEqual(info["pages"], 1)
        text = subprocess.run(["pdftotext", "-layout", self.pdf_path(), "-"], capture_output=True, text=True).stdout
        widths = {c: int(w) for c, w in re.findall(r"Chart (\d) w(\d+)", text)}
        self.assertEqual(sorted(widths), ["1", "2", "3"])
        self.assertGreater(widths["2"], (P.LANDSCAPE_WIDTH - 40) // 3 + 100, widths)  # three a row, well past a third of the page

    def test_a_one_pager_too_long_to_read_on_one_page_prints_on_more(self):
        self.renderer(750)
        self.write_story("one-pager", [{"cells": [{"visual": v}]} for v in ("v1", "v2", "v3")])
        info = self.build()
        self.assertEqual((info["one_page"], info["scale"]), (False, 1.0))
        self.assertGreater(info["pages"], 1)

    def test_chart_that_never_draws_writes_no_pdf(self):
        self.renderer(0)
        P.CHART_TIMEOUT = 3
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            P.build(self.story_dir)
        self.assertTrue(str(cm.exception.code).startswith("Chart v1 did not draw"), cm.exception.code)
        self.assertFalse(os.path.exists(self.pdf_path()))


if __name__ == "__main__":
    unittest.main()


class ChromeStart(unittest.TestCase):
    """A browser that does not come up is tried once more in a fresh profile, then reported."""

    def test_failed_start_is_retried_once_then_reported(self):
        from zoffice import cdp
        tmp = tempfile.mkdtemp()
        try:
            fake = os.path.join(tmp, "fake-chrome")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\necho run >> "$(dirname "$0")/runs"\nexit 1\n')
            os.chmod(fake, 0o755)
            with self.assertRaisesRegex(RuntimeError, "did not start"):
                cdp.Chrome(fake, os.path.join(tmp, "profile"), timeout=5)
            with open(os.path.join(tmp, "runs")) as f:
                self.assertEqual(f.read().split(), ["run", "run"])
        finally:
            shutil.rmtree(tmp)

    @unittest.skipUnless(SN.find_chrome() and os.name == "posix", "needs Chrome, Edge or Chromium on macOS or Linux")
    def test_devtools_runs_over_a_pipe_with_no_port(self):
        from zoffice import cdp
        tmp = tempfile.mkdtemp()
        try:
            ch = cdp.Chrome(SN.find_chrome(), os.path.join(tmp, "profile"))
            try:
                self.assertIsNone(ch.sock)
                self.assertEqual(ch.evaluate("6 * 7"), 42)
                self.assertFalse(os.path.exists(os.path.join(tmp, "profile", "DevToolsActivePort")))
            finally:
                ch.close()
        finally:
            shutil.rmtree(tmp)

