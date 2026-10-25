"""Tests for artifact.py. Run: python3 -I -m unittest discover <plugin>/scripts/tests

The page tests assemble the artifact page from a three-chart story. The build tests draw the chart
pictures in headless Chrome (skipped when none is installed) with the stand-in renderer and cached
visuals of test_pdf, so nothing goes over the network.
"""
import contextlib, io, json, os, re, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import artifact as A  # noqa: E402
import brand as B  # noqa: E402
import story as S  # noqa: E402
from zoffice import snapshot as SN  # noqa: E402
from test_pdf import ARGS, STORY, Base as PdfBase  # noqa: E402

URL = "https://claude.ai/artifact/AbC123xyz"


def results():
    return {vid: {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"visual": {"title": "</script><b>" + a["title"]}}}
            for vid, a in ARGS.items()}


class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.brand = B.load(start=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def page(self, live=False, story=None):
        story = story or json.loads(json.dumps(STORY))
        sizes = {vid: (760, 300) for vid in ARGS}
        return A.assemble_page(story, results(), self.brand, sizes, live, "ab" * 32)

    def test_pictures_by_default(self):
        page = self.page()
        self.assertIsNone(re.search(r"<(!doctype|html|head|body)\b", page, re.I))  # the artifact host adds the document
        self.assertNotIn("{{", page)
        for vid in ARGS:
            self.assertIn('<img src="charts/%s.png" width="760" height="300"' % vid, page)
        self.assertNotIn("<iframe", page)
        self.assertNotIn("<script", page)  # no renderer, no host: nothing runs
        self.assertIn("Look at <b>this</b>.", page)
        self.assertIn("Source: Test data.", page)

    def test_title_is_a_name_from_the_kicker(self):
        story = dict(json.loads(json.dumps(STORY)), kicker="P&L · October 2026 close")
        self.assertEqual(A.page_title(story), "P&L October 2026 close")
        self.assertIn("<title>P&amp;L October 2026 close</title>", self.page(story=story))
        self.assertEqual(A.page_title({"title": "Sales grew"}), "Sales grew")

    def test_live_keeps_pictures_and_loads_the_renderer_file(self):
        page = self.page(live=True)
        for vid in ARGS:
            self.assertRegex(page, r'<figure class="zs-plate" data-v="%s" data-w="760" data-h="300"><img src="charts/%s.png"[^>]*><iframe title="[^"]*" scrolling="no"></iframe></figure>' % (vid, vid))
        self.assertIn("fetch('zebra-renderer.txt')", page)
        self.assertIn("!== '%s'" % ("ab" * 32), page)  # checked against the build's renderer
        self.assertIn("version: '%s'" % S.version(), page)
        data = re.search(r'<script id="zebra-visuals" type="application/json">(.*?)</script>', page, re.S).group(1)
        self.assertNotIn("<", data)  # a result cannot close its script element
        self.assertEqual(json.loads(data), results())

    def test_story_text_is_escaped(self):
        story = dict(json.loads(json.dumps(STORY)), title="<img src=x onerror=alert(1)> grew")
        page = self.page(story=story)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt; grew", page)

    def test_only_google_fonts_load(self):
        self.assertEqual(A.font_link({"fonts": {"web_css": "https://example.com/f.css"}}), "")
        self.assertIn("https://fonts.googleapis.com/css2?family=Inter", A.font_link({"fonts": {"web_css": "https://fonts.googleapis.com/css2?family=Inter"}}))

    def test_record_refuses_anything_but_an_artifact_url(self):
        for bad in ("https://example.com/artifact/x", "javascript:alert(1)", "https://claude.ai/chat/abc"):
            with self.subTest(url=bad), self.assertRaises(SystemExit):
                A.record(self.tmp, bad)


class NoBrowser(PdfBase):
    def test_no_browser_stops_before_anything_is_sent(self):
        saved = os.environ.get("ZEBRA_BI_CHROME")
        os.environ["ZEBRA_BI_CHROME"] = os.path.join(self.tmp, "no-such-browser")
        try:
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()) as out:
                A.build(self.story_dir)
        finally:
            if saved is None:
                del os.environ["ZEBRA_BI_CHROME"]
            else:
                os.environ["ZEBRA_BI_CHROME"] = saved
        self.assertTrue(str(cm.exception.code).startswith("The artifact needs Google Chrome"))
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(os.path.exists(os.path.join(self.story_dir, "artifact")))


@unittest.skipUnless(SN.find_chrome(), "no Chrome, Edge or Chromium installed")
class Build(PdfBase):
    def setUp(self):
        super().setUp()
        self.renderer(300)

    def build(self, live=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            A.build(self.story_dir, live)
        return json.loads(out.getvalue()[out.getvalue().index("{"):])

    def test_pictures_at_the_column_width(self):
        info = self.build()
        self.assertEqual(sorted(info["files"]), ["charts/%s.png" % v for v in sorted(ARGS)])
        for path in info["files"].values():
            w, h = A.png_size(path)
            self.assertEqual(w, A.WIDTH * 2)
            self.assertLess(h, 2 * 420)  # cropped to the drawn chart, not padded
        self.assertFalse(info["live"])
        self.assertIsNone(info["url"])
        self.assertTrue(os.path.isfile(info["page"]))
        self.assertEqual(info["title"], "Sales grew")

    def test_switching_back_from_live_drops_the_renderer(self):
        info = self.build(live=True)
        self.assertIn(A.RENDERER_FILE, info["files"])
        with open(info["files"][A.RENDERER_FILE], encoding="utf-8") as f:
            self.assertIn("ui/initialize", f.read())
        with contextlib.redirect_stdout(io.StringIO()):
            A.record(self.story_dir, URL)
        info = self.build()
        self.assertEqual(info["remove"], [A.RENDERER_FILE])
        self.assertEqual(info["url"], URL)


if __name__ == "__main__":
    unittest.main()
