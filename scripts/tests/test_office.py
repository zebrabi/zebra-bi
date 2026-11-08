"""Tests for the PowerPoint/Excel export. Run: python3 -I -m unittest discover <plugin>/scripts/tests

Fixtures are three render_visual results (structuredContent.visual) from the Superstore sample story:
a Chart with one category level, a Table with one level and a Table with two levels.
"""
import contextlib, datetime as dt, hashlib, io, json, os, re, shutil, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
import office as O  # noqa: E402
import report as R  # noqa: E402
import story as S  # noqa: E402
from zoffice import check as C, ooxml as X, pptx as P, snapshot as SN, visual as V, xlsx as XL  # noqa: E402


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name + ".json"), encoding="utf-8") as f:
        return json.load(f)


MONTH, REGION, SUBCAT = fixture("sales-by-month"), fixture("sales-by-region"), fixture("sales-by-subcategory")
STORY = {
    "title": "2018 sales grew 20% to $722k", "subtitle": "Superstore, 2018 AC vs PY",
    "kpis": [{"value": "$722k", "label": "2018 sales"}],
    "source": {"label": "Superstore sample"},
    "sections": [
        {"heading": "Eight months of twelve grew", "body": "**November** added $39k.", "visual": "sales-by-month"},
        {"heading": "The West made more than half of the gain", "body": "West added $66k.", "visual": "sales-by-region"},
        {"heading": "Phones and Binders lead", "body": "Machines lost $12k.", "visual": "sales-by-subcategory"},
        {"heading": "No chart here", "body": "Text only."},
    ],
    "next": ["Check Washington next year."],
}
VISUALS = {"sales-by-month": MONTH, "sales-by-region": REGION, "sales-by-subcategory": SUBCAT}


class Mapping(unittest.TestCase):
    def test_flat_rows_one_level(self):
        rows = V.flat_rows(REGION)
        self.assertEqual(rows[0], ["Region", "AC", "PY"])
        self.assertEqual(len(rows), 1 + len(REGION["xcol"]))
        self.assertEqual(rows[1][1], REGION["ycol_ac"][0]["value"])

    def test_flat_rows_two_levels_repeat_parent(self):
        rows = V.flat_rows(SUBCAT)
        self.assertEqual(rows[0][:2], SUBCAT["xcol_name"])
        leaves = sum(len(n["children"]) for n in SUBCAT["xcol"])
        self.assertEqual(len(rows), 1 + leaves)
        first = SUBCAT["xcol"][0]
        self.assertEqual(rows[1][:2], [first["name"], first["children"][0]["name"]])

    def test_chart_settings(self):
        s = V.chart_settings(MONTH)
        self.assertEqual(s["chartType"], V.CHART_TYPES["variance"])
        self.assertEqual((s["measure1Role"], s["measure2Role"], s["usedMeasuresCount"]), ("Values", "PreviousYear", 2))
        self.assertEqual(s["titleText"], MONTH["title"])

    def test_chart_dataview_lengths_match(self):
        dv = V.chart_dataview(MONTH)
        n = len(dv["categories"][0]["values"])
        self.assertEqual(n, len(MONTH["xcol"]))
        self.assertTrue(all(len(v["values"]) == n for v in dv["values"]))

    def test_matrix_two_levels(self):
        m = V.table_matrix(SUBCAT)
        kids = m["rows"]["root"]["children"]
        self.assertEqual(len(kids), len(SUBCAT["xcol"]) + 1)  # parents plus the unnamed grand total
        self.assertTrue(kids[0]["children"][0]["isSubtotal"])
        self.assertEqual(kids[0]["children"][0]["values"]["0"]["value"], SUBCAT["ycol_ac"][0]["value"])
        self.assertTrue(kids[-1]["isSubtotal"])
        self.assertNotIn("value", kids[-1])
        self.assertEqual(kids[-1]["values"]["0"]["value"], SUBCAT["grand_total"]["ac"])

    def test_table_data_shifts_value_indices(self):
        for host in ("pptx", "xlsx"):
            d = V.table_data(SUBCAT, host)
            values = [c["index"] for c in d["metadata"]["columns"] if "Category" not in c["roles"]]
            self.assertEqual(values, [0, 1])
            self.assertEqual([c["index"] for c in d["matrix"]["valueSources"]], [0, 1])
            self.assertEqual([c["index"] for c in d["matrix"]["columns"]["levels"][0]["sources"]], [0, 1])
        self.assertEqual(V.table_data(SUBCAT, "xlsx")["rowFields"], SUBCAT["xcol_name"])
        self.assertEqual(V.table_settings(SUBCAT, "xlsx")["measureRoles"], ["Values", "PreviousYear"])

    def test_double_encoding_round_trips(self):
        d = V.table_data(REGION, "pptx")
        self.assertEqual(json.loads(json.loads(X.double_encoded(d))), d)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        placeholder = os.path.join(X.ASSETS, "snapshot-placeholder.png")
        self.snaps = {k: placeholder for k in VISUALS}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, mod, ext):
        out = os.path.join(self.tmp, "story." + ext)
        mod.build(STORY, VISUALS, self.snaps, out, "Source: test.", B.load(start=self.tmp))
        return out

    def test_pptx_passes_check(self):
        out = self.build(P, "pptx")
        errors, warnings, summary = C.check(out)
        self.assertEqual(errors, [])
        self.assertEqual(summary["addins"], {"charts": 1, "tables": 2})
        self.assertEqual(summary["snapshots"], 3)
        self.assertEqual(summary["placeholder_snapshots"], 3)  # the test uses the placeholder on purpose
        with zipfile.ZipFile(out) as z:
            slides = [n for n in z.namelist() if n.startswith("ppt/slides/slide")]
        self.assertEqual(len(slides), 1 + len(STORY["sections"]) + 1)  # title, sections, next

    def test_xlsx_passes_check(self):
        out = self.build(XL, "xlsx")
        errors, warnings, summary = C.check(out)
        self.assertEqual(errors, [])
        self.assertEqual(summary["addins"], {"charts": 1, "tables": 2})
        with zipfile.ZipFile(out) as z:
            we = z.read("xl/webextensions/webextension2.xml").decode()
        self.assertIn("&quot;range&quot;:&quot;'The West made more than half'!A4:C8&quot;", we)
        self.assertIn('appref="{00000000-0001-0000-0200-000000000000}"', we)

    def test_check_catches_a_missing_part(self):
        out = self.build(P, "pptx")
        broken = os.path.join(self.tmp, "broken.pptx")
        with zipfile.ZipFile(out) as src, zipfile.ZipFile(broken, "w") as dst:
            for n in src.namelist():
                if n != "ppt/webextensions/webextension1.xml":
                    dst.writestr(n, src.read(n))
        errors, _, _ = C.check(broken)
        self.assertTrue(any("webextension1.xml" in e for e in errors), errors)

    def test_sheet_names_are_valid_and_unique(self):
        story = {"sections": [{"heading": "A: very/long [heading] that goes on and on", "visual": "a"},
                              {"heading": "A: very/long [heading] that goes on and on", "visual": "b"}]}
        names = XL.sheet_names(story)
        self.assertEqual(len(set(names.values())), 2)
        for n in names.values():
            self.assertLessEqual(len(n), 31)
            self.assertFalse(set(n) & set("[]:*?/\\'"))


def shapes(xml):
    """{shape name: (x, y, cx, cy)} for the shapes of one slide."""
    out = {}
    for m in re.finditer(r'<p:cNvPr id="\d+" name="([^"]*)"/>.*?<a:off x="(\d+)" y="(\d+)"/><a:ext cx="(\d+)" cy="(\d+)"/>', xml, re.S):
        out.setdefault(m.group(1), tuple(int(v) for v in m.groups()[1:]))
    return out


class ReportDeck(unittest.TestCase):
    """A report's deck: the cover, then a slide per chart with its message as the title and its note beside it."""

    DECK = {"kicker": "P&L close", "title": "Sales grew", "kpis": [{"value": "1", "label": "Sales"}],
            "sections": [{"heading": "P&L close", "body": "Read this first."},
                         {"heading": "Months grew", "visual": "sales-by-month", "note": "November **added** $39k.",
                          "texts": ["The user's own words."], "kind": "report-chart"},
                         {"heading": "Regions", "visual": "sales-by-region", "note": "", "texts": [], "kind": "report-chart"}]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        placeholder = os.path.join(X.ASSETS, "snapshot-placeholder.png")
        self.snaps = {"sales-by-month": placeholder, "sales-by-region": placeholder}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def slides(self):
        out = os.path.join(self.tmp, "deck.pptx")
        P.build(self.DECK, {"sales-by-month": MONTH, "sales-by-region": REGION}, self.snaps, out, "Built today.",
                B.load(start=self.tmp))
        self.assertEqual(C.check(out)[0], [])
        with zipfile.ZipFile(out) as z:
            return ({n: z.read("ppt/slides/slide%d.xml" % n).decode() for n in range(1, 5)},
                    {n: z.read("ppt/notesSlides/notesSlide%d.xml" % n).decode() for n in range(1, 5)})

    def test_the_note_sits_beside_the_chart_and_in_the_speaker_notes(self):
        slides, notes = self.slides()
        sh = shapes(slides[3])
        chart = next(v for k, v in sh.items() if k.startswith("Zebra BI"))
        self.assertGreater(sh["Note"][0], chart[0] + chart[2])  # right of the chart
        self.assertIn("added", slides[3])
        self.assertIn("own words", slides[3])
        self.assertIn("November", notes[3])
        self.assertNotIn("Note", shapes(slides[4]))  # no note, no box
        self.assertNotIn("November", notes[4])

    def test_a_chart_with_nothing_beside_it_is_centred(self):
        slides, _ = self.slides()
        sh = shapes(slides[4])
        chart = next(v for k, v in sh.items() if k.startswith("Zebra BI"))
        title = sh["Title"]
        self.assertAlmostEqual(chart[0] + chart[2] / 2, title[0] + title[2] / 2, delta=914400 * 0.05)

    def test_a_cover_without_a_subtitle_has_no_empty_placeholder(self):
        slides, _ = self.slides()
        self.assertNotIn("Subtitle", shapes(slides[1]))
        self.assertNotIn('type="subTitle"', slides[1])

    def test_a_story_section_with_a_note_keeps_its_finding(self):
        story = {"title": "T", "sections": [{"heading": "Growth", "body": "Growth came from the West.",
                                              "visual": "sales-by-month", "note": "an aside"}]}
        out = os.path.join(self.tmp, "story.pptx")
        P.build(story, {"sales-by-month": MONTH}, self.snaps, out, "Built today.", B.load(start=self.tmp))
        with zipfile.ZipFile(out) as z:
            slide = z.read("ppt/slides/slide2.xml").decode()
        self.assertIn("Growth came from the West.", slide)
        self.assertNotIn("Note", shapes(slide))

    def test_text_before_the_first_chart_gets_the_slide_height(self):
        slides, _ = self.slides()
        body = shapes(slides[2])["Finding"]
        self.assertGreater(body[3], 3 * 914400)  # more than three inches tall, not the 0.8 inch finding line


class ReportFolder(unittest.TestCase):
    """office.py builds a report folder (report.zbreport) as a deck, its charts drawn at the width a slide gives them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = S.CACHE, os.environ.get("ZEBRA_BI_CHROME")
        S.CACHE = os.path.join(self.tmp, "cache", "renderer")
        os.makedirs(S.CACHE)
        data = b"<!doctype html><title>stand-in renderer</title>"
        with open(os.path.join(S.CACHE, "visual.html"), "wb") as f:
            f.write(data)
        with open(os.path.join(S.CACHE, "visual.json"), "w") as f:
            json.dump({"sha256": hashlib.sha256(data).hexdigest(), "endpoint": S.ENDPOINT,
                       "fetched_at": dt.datetime.now().isoformat()}, f)
        os.environ["ZEBRA_BI_CHROME"] = os.path.join(self.tmp, "no-chrome")  # placeholder pictures, no browser
        self.dir = os.path.join(self.tmp, "q4-close")
        os.makedirs(os.path.join(self.dir, "visuals"))
        rep = R.new("Q4 close")
        for vid, visual in (("sales-by-month", MONTH), ("sales-by-region", REGION)):
            args = {"title": vid, "chart_type": "bar", "columns": ["Item", "AC"], "rows": [["a", 1]]}
            R.add_chart(rep, args, message="About " + vid, note="It held 1.")
            with open(os.path.join(self.dir, "visuals", vid + ".json"), "w") as f:
                json.dump({"args_hash": S.args_hash(args), "result": {"structuredContent": {"visual": visual}}}, f)
        self.rep = rep
        R.save(os.path.join(self.dir, R.FILE), rep)

    def tearDown(self):
        S.CACHE = self.saved[0]
        if self.saved[1] is None:
            os.environ.pop("ZEBRA_BI_CHROME", None)
        else:
            os.environ["ZEBRA_BI_CHROME"] = self.saved[1]
        shutil.rmtree(self.tmp)

    def test_a_report_folder_builds_a_deck(self):
        widths = []
        real = SN.snapshot_all
        SN.snapshot_all = lambda results, renderer, out_dir, width=SN.WIDTH, **kw: (widths.append(width), real(results, renderer, out_dir, width, **kw))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                O.build(self.dir, ["pptx"])
        finally:
            SN.snapshot_all = real
        info = json.loads(out.getvalue()[out.getvalue().index("{"):])
        pptx = os.path.join(self.dir, "q4-close.pptx")
        self.assertEqual(info["files"][0]["file"], pptx)
        with zipfile.ZipFile(pptx) as z:
            names = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            cover = z.read("ppt/slides/slide1.xml").decode()
        self.assertEqual(len(names), 3)  # the cover and one slide per chart
        self.assertIn("Built ", cover)
        self.assertNotIn("Source: unknown", cover)
        self.assertEqual(widths, [P.chart_width(B.load(start=self.dir))])
        self.assertLess(widths[0], SN.WIDTH)

    def test_a_storys_rows_a_deck_cannot_show_are_named(self):
        os.remove(os.path.join(self.dir, R.FILE))
        with open(os.path.join(self.dir, "story.json"), "w") as f:
            json.dump({"title": "T", "sections": [{"heading": "H", "visual": "sales-by-month"}],
                       "rows": [{"cells": [{"visual": "sales-by-region"}]}],
                       "visuals": {"sales-by-month": {"args": {"title": "sales-by-month", "chart_type": "bar", "columns": ["Item", "AC"], "rows": [["a", 1]]}},
                                   "sales-by-region": {"args": {"title": "sales-by-region", "chart_type": "bar", "columns": ["Item", "AC"], "rows": [["a", 1]]}}}}, f)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            O.build(self.dir, ["pptx"])
        self.assertIn("sales-by-region", err.getvalue())
        self.assertIn("only the sections", err.getvalue())

    def test_a_flagged_report_writes_no_deck(self):
        R.update_text(self.rep, "c1", "It held 99.0.")
        R.save(os.path.join(self.dir, R.FILE), self.rep)
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            O.build(self.dir, ["pptx"])
        self.assertIn("not in their charts", str(cm.exception.code))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "q4-close.pptx")))


if __name__ == "__main__":
    unittest.main()
