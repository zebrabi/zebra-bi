"""Tests for the excel-visual skill's Office.js script, run in plain Node against a stub Excel (excel_stub.mjs).

The workbook the script asks Excel to create is checked with zoffice.check and compared with what xlsx.py writes for
the same data, so the two routes into Excel store the same add-in settings. Skipped where node is not on the PATH.
Sample Co figures, made up for the tests.
"""
import json, os, re, shutil, subprocess, sys, tempfile, unittest, zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
from zoffice import check as C, ooxml as X, xlsx as XL  # noqa: E402

NODE = shutil.which("node")
SCRIPT = os.path.join(ROOT, "skills", "excel-visual", "scripts", "visual-from-selection.js")
STUB = os.path.join(HERE, "excel_stub.mjs")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
REVENUE = [["Month", "AC", "PY"]] + [[m, 1420 + i * 118 + (i % 3) * 25, 1400 + i * 95] for i, m in enumerate(MONTHS)]
COSTS = [["Department", "AC", "PY"], ["Sales", 2140, 1980], ["Marketing", 1320, 1410], ["R&D", 2860, 2550],
         ["Customer support", 940, 905], ["Finance", 610, 620], ["IT", 780, 690], ["HR", 420, 415]]
NS_WE = "{%s}" % X.NS_WE


def node(args):
    return subprocess.run([NODE] + args, capture_output=True, text=True, timeout=60)


def addin(path, name="xl/webextensions/webextension1.xml"):
    """(kind, {property: value}) of the add-in part in a workbook."""
    with zipfile.ZipFile(path) as z:
        tree = ET.fromstring(z.read(name))
    kind = {a["id"].lower(): k for k, a in X.ADDINS.items()}[tree.find(NS_WE + "reference").get("id").lower()]
    return kind, {p.get("name"): p.get("value") for p in tree.iter(NS_WE + "property")}


def decoded(props):
    """The properties with their JSON read (the matrix data twice), so only content is compared, not key order."""
    out = {}
    for k, v in props.items():
        if k == "zebrabi:matrix-data" and v:
            out[k] = json.loads(json.loads(v))
        else:
            try:
                out[k] = json.loads(v) if v else v
            except ValueError:
                out[k] = v
    return out


def visual(kind, rows, title, costs=False):
    """The render_visual result shape for the same data: what xlsx.py is given on the file route."""
    cols = list(zip(*rows))
    data = lambda i: [{"value": float(v)} for v in cols[i][1:]]
    return {"title": title, "visual_type": "Table" if kind == "tables" else "Chart", "chart_type": "variance",
            "xcol_name": [rows[0][0]], "xcol": [{"name": str(r[0])} for r in rows[1:]],
            "ycol_ac": data(1), "ycol_py": data(2), "ycol_pl": None, "ycol_fc": None, "additional_measures": [],
            "measure": {"Val": rows[0][1], "PY": rows[0][2]}, "is_inverted": costs,
            "grand_total": {"ac": float(sum(cols[1][1:])), "py": float(sum(cols[2][1:])), "pl": None, "fc": None, "additional_measures": []}}


@unittest.skipUnless(NODE, "node is not on the PATH")
class Script(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_script(self, values, script=SCRIPT, **settings):
        scenario = os.path.join(self.tmp, "scenario.json")
        out = os.path.join(self.tmp, "book.xlsx")
        with open(scenario, "w") as f:
            json.dump(dict(settings, values=values, address="Start!A5:C17"), f)
        done = node([STUB, script, scenario, out])
        info = json.loads(done.stdout)
        return info, out

    def file_route(self, kind, rows, title, costs=False):
        """The same visual as xlsx.py writes it: (webextension properties, SourceData)."""
        v = visual(kind, rows, title, costs)
        story = {"sections": [{"heading": "Sample", "body": "", "visual": "sample"}]}
        out = os.path.join(self.tmp, "file-route.xlsx")
        XL.build(story, {"sample": v}, {"sample": os.path.join(X.ASSETS, "snapshot-placeholder.png")}, out, "Source: test.", B.load(start=self.tmp))
        return addin(out, "xl/webextensions/webextension1.xml")[1]

    def test_a_time_selection_opens_a_workbook_with_one_charts_add_in(self):
        info, out = self.run_script(REVENUE, title="Sample Co, Revenue by month (EUR thousands, 2026 AC vs PY)")
        self.assertEqual((info["created"], info["error"]), (1, None))
        errors, _, summary = C.check(out)
        self.assertEqual(errors, [])
        self.assertEqual(summary["addins"], {"charts": 1, "tables": 0})
        kind, props = addin(out)
        self.assertEqual(kind, "charts")
        source = json.loads(props["SourceData"])
        self.assertEqual(source["range"], "'Zebra BI chart'!A3:C15")
        self.assertEqual(source["worksheetId"], "{00000000-0001-0000-%02d00-000000000000}" % 0)

    def test_a_category_selection_opens_a_tables_add_in_with_its_data(self):
        info, out = self.run_script(COSTS, title="Sample Co, Operating expenses by department", costs=True)
        self.assertEqual((info["created"], info["error"]), (1, None))
        errors, _, summary = C.check(out)
        self.assertEqual(errors, [])
        self.assertEqual(summary["addins"], {"charts": 0, "tables": 1})
        kind, props = addin(out)
        data = json.loads(json.loads(props["zebrabi:matrix-data"]))
        self.assertEqual(kind, "tables")
        self.assertEqual(len(data["matrix"]["rows"]["root"]["children"]), len(COSTS))  # seven rows and the unnamed total
        total = data["matrix"]["rows"]["root"]["children"][-1]
        self.assertEqual(total["values"]["0"]["value"], sum(r[1] for r in COSTS[1:]))
        self.assertEqual(json.loads(props["ZBITablesSettings"])["invert"], True)

    def test_each_route_stores_the_same_settings_and_data(self):
        for kind, rows, title, costs in (("charts", REVENUE, "Sample Co, Revenue by month", False),
                                         ("tables", COSTS, "Sample Co, Operating expenses by department", True)):
            info, out = self.run_script(rows, title=title, costs=costs, visual="chart" if kind == "charts" else "table")
            self.assertEqual(info["error"], None)
            got_kind, mine = addin(out)
            theirs = self.file_route(kind, rows, title, costs)
            self.assertEqual(got_kind, kind)
            mine, theirs = decoded(mine), decoded(theirs)
            different = {k for k in set(mine) | set(theirs) if mine.get(k) != theirs.get(k)}
            # The only differences allowed: where the data sits (the script's sheet and range against the file route's).
            self.assertEqual(different, {"SourceData"}, kind)
            self.assertEqual(mine["SourceData"]["range"].split("!")[1], "A3:C%d" % (2 + len(rows)))

    def test_a_chart_can_be_asked_for_on_category_data(self):
        info, out = self.run_script(COSTS, visual="chart")
        self.assertEqual(info["error"], None)
        self.assertEqual(addin(out)[0], "charts")

    def test_a_selection_without_a_numeric_column_stops_with_the_message_and_creates_nothing(self):
        info, _ = self.run_script([["Department", "Note"], ["Sales", "high"], ["IT", "low"]])
        self.assertEqual(info["created"], 0)
        self.assertEqual(info["error"], "Select a header row, one category column and at least one numeric column.")

    def test_a_header_only_selection_stops_with_the_message(self):
        info, _ = self.run_script([["Month", "AC", "PY"]])
        self.assertEqual(info["created"], 0)
        self.assertIn("Select the data with its header row first", info["error"])

    def test_the_settings_lines_each_exist_exactly_once(self):
        with open(SCRIPT, encoding="utf-8") as f:
            text = f.read()
        for name in ("TITLE", "COSTS", "VISUAL"):
            self.assertEqual(len(re.findall(r"^const %s = " % name, text, re.M)), 1, name)
        broken = os.path.join(self.tmp, "broken.js")
        with open(broken, "w") as f:
            f.write(text.replace("const COSTS = false;", "let costs = false;"))
        info, _ = self.run_script(REVENUE, script=broken)
        self.assertEqual(info["created"], 0)
        self.assertIn("settings line for COSTS found 0 times", info["error"])

    def test_the_script_holds_no_guid_and_makes_no_call(self):
        with open(SCRIPT, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text), [])
        for network in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "import("):
            self.assertNotIn(network, text)

    def test_each_run_gives_its_web_extension_its_own_id(self):
        ids = set()
        for _ in range(2):
            info, out = self.run_script(REVENUE)
            with zipfile.ZipFile(out) as z:
                ids.add(ET.fromstring(z.read("xl/webextensions/webextension1.xml")).get("id"))
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(re.fullmatch(r"\{[0-9A-F]{8}-[0-9A-F]{4}-4[0-9A-F]{3}-8[0-9A-F]{3}-[0-9A-F]{12}\}", i) for i in ids), ids)

    def sheet(self, out):
        """{cell ref: (type, text)} of the script's worksheet."""
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        with zipfile.ZipFile(out) as z:
            tree = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        cells = {}
        for c in tree.iter(ns + "c"):
            inline = c.find(ns + "is")
            cells[c.get("r")] = (c.get("t"), "".join(inline.itertext()) if inline is not None else c.find(ns + "v").text)
        return cells

    def test_an_unrecognised_column_before_the_actuals_does_not_take_their_place(self):
        rows = [["Product", "Units", "AC", "PY"], ["Pens", 40, 1200, 1100], ["Pads", 25, 900, 950], ["Ink", 12, 400, 380]]
        info, out = self.run_script(rows)
        self.assertEqual(info["error"], None)
        cells = self.sheet(out)
        self.assertEqual([cells[r][1] for r in ("A3", "B3", "C3")], ["Product", "AC", "PY"])
        self.assertEqual(cells["A1"][1], "AC vs PY by Product")
        self.assertIn("left out: Units", info["log"][0])
        errors, _, _ = C.check(out)
        self.assertEqual(errors, [])

    def test_a_blank_spacer_column_is_ignored_and_the_actuals_are_kept(self):
        rows = [["Product", "", "AC", "PY"], ["Pens", "", 1200, 1100], ["Pads", "", 900, 950]]
        info, out = self.run_script(rows)
        self.assertEqual(info["error"], None)
        self.assertEqual([self.sheet(out)[r][1] for r in ("A3", "B3", "C3")], ["Product", "AC", "PY"])
        self.assertNotIn("left out", info["log"][0])

    def test_a_scenario_column_with_a_text_cell_stops_instead_of_dropping_the_column(self):
        rows = [["Product", "AC", "PY"], ["Pens", "n/a", 1100], ["Pads", 900, 950]]
        info, _ = self.run_script(rows)
        self.assertEqual(info["created"], 0)
        self.assertIn('Column "AC" reads as actuals but holds text or errors', info["error"])

    def test_two_columns_for_one_role_stop_with_both_names(self):
        rows = [["Product", "Actual 2025", "Actual 2026", "PY"], ["Pens", 1, 2, 3], ["Pads", 4, 5, 6]]
        info, _ = self.run_script(rows)
        self.assertEqual(info["created"], 0)
        self.assertIn('Two columns read as actuals ("Actual 2025" and "Actual 2026")', info["error"])

    def test_a_year_category_is_written_as_text(self):
        rows = [["Year", "AC", "PY"], [2024, 1200, 1100], [2025, 1300, 1200], [2026, 1500, 1300]]
        info, out = self.run_script(rows)
        self.assertEqual(info["error"], None)
        cells = self.sheet(out)
        self.assertEqual([cells["A%d" % r] for r in (4, 5, 6)], [("inlineStr", "2024"), ("inlineStr", "2025"), ("inlineStr", "2026")])
        self.assertEqual(cells["B4"][0], None)    # a scenario value stays a number

    def test_a_date_category_is_written_as_the_text_the_cells_show(self):
        rows = [["Month", "AC", "PY"], [46023, 1200, 1100], [46054, 1300, 1200]]
        text = [["Month", "AC", "PY"], ["Jan-26", "1,200", "1,100"], ["Feb-26", "1,300", "1,200"]]
        info, out = self.run_script(rows, text=text)
        self.assertEqual(info["error"], None)
        cells = self.sheet(out)
        self.assertEqual([cells["A4"], cells["A5"]], [("inlineStr", "Jan-26"), ("inlineStr", "Feb-26")])
        self.assertEqual(addin(out)[0], "charts")

    def test_a_control_character_in_a_label_does_not_break_the_workbook(self):
        rows = [["Department", "AC", "PY"], ["R&D\x0b", 2860, 2550], ["IT\x07", 780, 690]]
        info, out = self.run_script(rows)
        self.assertEqual(info["error"], None)
        errors, _, _ = C.check(out)
        self.assertEqual(errors, [])
        self.assertEqual([self.sheet(out)[r][1] for r in ("A4", "A5")], ["R&D", "IT"])


if __name__ == "__main__":
    unittest.main()
