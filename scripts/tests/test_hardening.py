"""Tests for the hardening of build writes, the SVG check, URL checks and profiles. Run: python3 -I -m unittest discover <plugin>/scripts/tests

A story or brand folder can come from someone else (a shared repository, a zip), so a symbolic link planted
in it must never redirect a build's writes to a file outside it.
"""
import importlib.util, os, shutil, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import artifact as A  # noqa: E402
import brand as B  # noqa: E402
import story as S  # noqa: E402
from zoffice import ooxml as X, snapshot as SN  # noqa: E402

_spec = importlib.util.spec_from_file_location("zs_profile_h", os.path.join(os.path.dirname(HERE), "profile.py"))
PR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PR)

PNG = os.path.join(os.path.dirname(HERE), "..", "assets", "brand", "snapshot-placeholder.png")


class Writes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.victim = os.path.join(self.tmp, "victim.txt")
        with open(self.victim, "w") as f:
            f.write("keep")
        self.saved_chrome = os.environ.get("ZEBRA_BI_CHROME")

    def tearDown(self):
        if self.saved_chrome is None:
            os.environ.pop("ZEBRA_BI_CHROME", None)
        else:
            os.environ["ZEBRA_BI_CHROME"] = self.saved_chrome
        shutil.rmtree(self.tmp)

    def kept(self):
        with open(self.victim) as f:
            return f.read() == "keep"

    def test_write_atomic_replaces_a_planted_link(self):
        link = os.path.join(self.tmp, "out.png")
        os.symlink(self.victim, link)
        X.write_atomic(link, b"new")
        self.assertTrue(self.kept())
        self.assertFalse(os.path.islink(link))
        self.assertEqual(X.read(link, "rb"), b"new")

    def test_package_does_not_follow_a_link_at_the_file_or_the_old_temp_name(self):
        for planted in ("deck.pptx", "deck.pptx.tmp"):
            with self.subTest(planted=planted):
                out = os.path.join(self.tmp, "deck.pptx")
                for p in (out, out + ".tmp"):
                    if os.path.lexists(p):
                        os.remove(p)
                os.symlink(self.victim, os.path.join(self.tmp, planted))
                pkg = X.Package()
                pkg.add("a.xml", "<a/>", "application/xml")
                pkg.write(out)
                self.assertTrue(self.kept())
                self.assertTrue(zipfile.is_zipfile(out))

    def test_placeholder_snapshots_do_not_follow_a_planted_link(self):
        os.environ["ZEBRA_BI_CHROME"] = os.path.join(self.tmp, "no-such-browser")
        out_dir = os.path.join(self.tmp, "snapshots")
        os.makedirs(out_dir)
        os.symlink(self.victim, os.path.join(out_dir, "v1.png"))
        snaps, chrome = SN.snapshot_all({"v1": {}}, None, out_dir)
        self.assertIsNone(chrome)
        self.assertTrue(self.kept())
        X.png_size(snaps["v1"])  # a real picture where the link was

    def test_a_linked_snapshot_folder_is_refused(self):
        os.environ["ZEBRA_BI_CHROME"] = os.path.join(self.tmp, "no-such-browser")
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.tmp, "snapshots"))
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            SN.snapshot_all({"v1": {}}, None, os.path.join(self.tmp, "snapshots"))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_logo_never_lands_on_a_planted_link(self):
        bdir = os.path.join(self.tmp, "brand")
        os.makedirs(bdir)
        os.symlink(self.victim, os.path.join(bdir, "logo.png"))
        self.assertEqual(B.store_logo(PNG, bdir), {"file": "logo.png", "svg": None})
        self.assertTrue(self.kept())
        X.png_size(os.path.join(bdir, "logo.png"))

    def test_artifact_imports_the_shared_writer(self):
        self.assertIs(A.X.write_atomic, X.write_atomic)


class SvgCheck(unittest.TestCase):
    NS = 'xmlns="http://www.w3.org/2000/svg"'

    def test_css_after_a_child_of_style_is_read(self):
        for css in ("@import url(http://h/x.css);", "rect{fill:url(http://h/p.svg#p)}"):
            with self.subTest(css=css):
                svg = '<svg %s><style>rect{}<g/>%s</style><rect/></svg>' % (self.NS, css)
                self.assertIsNotNone(X.svg_problem(svg.encode()))

    def test_attributes_that_fetch_are_refused(self):
        for attr in ("srcset", "imagesrcset", "poster", "background", "lowsrc", "data"):
            with self.subTest(attr=attr):
                svg = '<svg %s><g %s="http://h/a.png"/></svg>' % (self.NS, attr)
                self.assertIn("loads from elsewhere", X.svg_problem(svg.encode()))

    def test_html_elements_are_refused(self):
        svg = '<svg %s xmlns:h="http://www.w3.org/1999/xhtml"><h:img src="#x"/></svg>' % self.NS
        self.assertEqual(X.svg_problem(svg.encode()), "embeds HTML (img)")

    def test_a_plain_logo_still_passes(self):
        svg = ('<svg %s xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 10 10">'
               '<style>.a{fill:#123456}</style><defs><linearGradient id="g"/></defs>'
               '<rect class="a" width="10" height="10" fill="url(#g)" inkscape:label="box"/></svg>' % self.NS)
        self.assertIsNone(X.svg_problem(svg.encode()))


class Urls(unittest.TestCase):
    def test_addresses_urllib_would_read_differently_are_refused(self):
        for url in ("http://evil.example\\@127.0.0.1/", "https://user@x.example/mcp", "https://x.example/\x1b]0;t\x07",
                    "https://x.example/ a", "https://x.example/\tmcp", None):
            with self.subTest(url=url):
                self.assertFalse(S.safe_url(url))
        for url in ("https://mcp.zebrabi.com", "http://127.0.0.1:8080/mcp", "https://app.zebra-ai.com/mcp?x=1"):
            with self.subTest(url=url):
                self.assertTrue(S.safe_url(url))

    def test_same_resource_ignores_a_trailing_slash_and_the_default_port(self):
        self.assertTrue(S.same_resource("https://mcp.zebrabi.com", "https://mcp.zebrabi.com/"))
        self.assertTrue(S.same_resource("https://MCP.zebrabi.com:443/", "https://mcp.zebrabi.com"))
        self.assertFalse(S.same_resource("https://mcp.zebrabi.com", "https://mcp.zebrabi.com.evil.example"))
        self.assertFalse(S.same_resource("https://mcp.zebrabi.com", "http://mcp.zebrabi.com"))
        self.assertFalse(S.same_resource("https://app.zebra-ai.com/mcp", "https://app.zebra-ai.com/other"))


class Profiles(unittest.TestCase):
    def test_free_text_columns_carry_no_examples(self):
        col = PR.classify("Comment", ["note from person %d" % i for i in range(400)])
        self.assertEqual(col["role"], "text")
        self.assertNotIn("examples", col)

    def test_categories_keep_their_examples(self):
        col = PR.classify("Region", ["East", "West", "North"] * 40)
        self.assertEqual(col["role"], "category")
        self.assertEqual(col["examples"], ["East", "North", "West"])


if __name__ == "__main__":
    unittest.main()
