"""Tests for pdf.py. Run: python3 -I -m unittest discover <plugin>/scripts/tests

The build tests print a real PDF in headless Chrome (skipped when none is installed) without any
network: the visuals and a stand-in renderer are cached in a temp folder. The stand-in answers the
MCP Apps handshake, draws an SVG of a set height when it gets its tool result and reports that size,
so page breaks are predictable.
"""
import contextlib, datetime as dt, hashlib, io, json, os, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
import pdf as P  # noqa: E402
import story as S  # noqa: E402
from zoffice import snapshot as SN  # noqa: E402

FAKE_RENDERER = """<!doctype html><html><body style="margin:0"><div id="c"></div><script>
var H = %s;
function send(m) { parent.postMessage(m, '*'); }
window.addEventListener('message', function (e) {
  var m = e.data; if (!m) return;
  if (m.id === 1 && m.result) send({ jsonrpc: '2.0', method: 'notifications/initialized' });
  else if (m.method === 'ui/notifications/tool-result' && H) {
    var t = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    t.setAttribute('width', '600'); t.setAttribute('height', String(H));
    t.innerHTML = '<text x="10" y="40">' + m.params.structuredContent.visual.title + '</text>';
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
            json.dump({"sha256": hashlib.sha256(data).hexdigest(), "fetched_at": dt.datetime.now().isoformat()}, f)

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

    def test_chart_that_never_draws_writes_no_pdf(self):
        self.renderer(0)
        P.CHART_TIMEOUT = 3
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            P.build(self.story_dir)
        self.assertTrue(str(cm.exception.code).startswith("Chart v1 did not draw"), cm.exception.code)
        self.assertFalse(os.path.exists(self.pdf_path()))


if __name__ == "__main__":
    unittest.main()
