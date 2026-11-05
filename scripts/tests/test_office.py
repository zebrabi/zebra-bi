"""Tests for the PowerPoint/Excel export. Run: python3 -I -m unittest discover <plugin>/scripts/tests

Fixtures are three render_visual results (structuredContent.visual) from the Superstore sample story:
a Chart with one category level, a Table with one level and a Table with two levels.
"""
import json, os, shutil, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
from zoffice import check as C, ooxml as X, pptx as P, visual as V, xlsx as XL  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
