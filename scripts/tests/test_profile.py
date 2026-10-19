"""Tests for profile.py on workbooks shaped like Excel's own: dates are day counts with a date style.
Run: python3 -I -m unittest discover -s <plugin>/scripts/tests
"""
import importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "profile.py")
# Loaded by path: the standard library has its own module named profile.
_spec = importlib.util.spec_from_file_location("zs_profile", SCRIPT)
PR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PR)

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# Style 0 general, 1 built-in date (14), 2 custom dd.mm.yyyy (164), 3 built-in time h:mm (20), 4 custom with a quoted "d"
STYLES = ('<styleSheet xmlns="%s"><numFmts count="2"><numFmt numFmtId="164" formatCode="dd\\.mm\\.yyyy"/>'
          '<numFmt numFmtId="165" formatCode="#,##0 &quot;days&quot;"/></numFmts>'
          '<cellXfs count="5"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/><xf numFmtId="164" applyNumberFormat="1"/>'
          '<xf numFmtId="20" applyNumberFormat="1"/><xf numFmtId="165" applyNumberFormat="1"/></cellXfs></styleSheet>' % M)


def workbook(path, header, rows, styles=STYLES, date1904=False):
    """rows: lists of (value, style index) per column."""
    cols = "ABCDEFGH"
    shared = list(header)
    sheet = ['<row r="1">%s</row>' % "".join('<c r="%s1" t="s"><v>%d</v></c>' % (cols[i], i) for i in range(len(header)))]
    for r, row in enumerate(rows, start=2):
        sheet.append('<row r="%d">%s</row>' % (r, "".join('<c r="%s%d" s="%d"><v>%s</v></c>' % (cols[i], r, s, v)
                                                         for i, (v, s) in enumerate(row))))
    rels = '<Relationship Id="rId1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>' % R
    if styles:
        rels += '<Relationship Id="rId2" Type="%s/styles" Target="styles.xml"/>' % R
    rels += '<Relationship Id="rId3" Type="%s/sharedStrings" Target="sharedStrings.xml"/>' % R
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="%s">%s<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>'
                   % (M, R, '<workbookPr date1904="1"/>' if date1904 else ""))
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % rels)
        if styles:
            z.writestr("xl/styles.xml", styles)
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="%s">%s</sst>' % (M, "".join("<si><t>%s</t></si>" % s for s in shared)))
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>' % (M, "".join(sheet)))
    return path


class ExcelDates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def profile(self, header, rows, **kw):
        path = workbook(os.path.join(self.tmp, "book.xlsx"), header, rows, **kw)
        read, table = PR.read_xlsx(path)
        cols = {h: PR.classify(h, [r[i] for r in table[1:]]) for i, h in enumerate(table[0])}
        return table, cols

    def test_date_styles_become_iso_dates(self):
        rows = [[(43101 + i, 1), (43101 + i, 2), (0.25 + i / 1000, 3), (100.5 + i, 0), (5 + i % 7, 4)] for i in range(60)]
        table, cols = self.profile(["Order Date", "Ship Date", "Time", "Sales", "Lead"], rows)
        self.assertEqual(table[1][0], "2018-01-01")
        self.assertEqual(table[1][1], "2018-01-01")
        self.assertEqual(cols["Order Date"]["role"], "date")
        self.assertEqual(cols["Order Date"]["date_order"], "year-first")
        self.assertEqual(cols["Ship Date"]["role"], "date")
        self.assertEqual(cols["Time"]["role"], "measure")    # a time-only style is not a date
        self.assertEqual(cols["Sales"]["role"], "measure")   # a general number stays a number
        self.assertEqual(cols["Lead"]["role"], "measure")    # a "d" inside a quoted literal is not a day

    def test_fraction_keeps_the_time(self):
        table, _ = self.profile(["At"], [[(43101.5, 1)]])
        self.assertEqual(table[1][0], "2018-01-01T12:00:00")

    def test_1904_date_system(self):
        table, _ = self.profile(["Order Date"], [[(41639, 1)]], date1904=True)
        self.assertEqual(table[1][0], "2018-01-01")

    def test_without_styles_numbers_stay_numbers(self):
        table, cols = self.profile(["Order Date"], [[(43101 + i, 1)] for i in range(10)], styles=None)
        self.assertEqual(table[1][0], "43101")
        self.assertEqual(cols["Order Date"]["role"], "measure")

    def test_malformed_styles_do_not_crash(self):
        table, _ = self.profile(["Order Date"], [[(43101, 1)]], styles='<styleSheet xmlns="%s"/>' % M)
        self.assertEqual(table[1][0], "43101")


class Keys(unittest.TestCase):
    """A P&L in whole euros looks like a key column: whole numbers, almost all distinct, more than 50."""
    EUROS = [str(50000 + i * 7919) for i in range(60)]

    def role(self, name, values):
        return PR.classify(name, values)["role"]

    def test_whole_euro_amounts_stay_measures(self):
        for name in ("Actual", "Value", "Amount", "Revenue", "PY", "Total Cost EUR"):
            self.assertEqual(self.role(name, self.EUROS), "measure", name)

    def test_whole_euro_csv_profiles_actual_as_a_measure(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "pl.csv")
        with open(path, "w") as f:
            f.write("Account,Actual,Plan\n" + "".join("Revenue,%s,%d.5\n" % (v, i) for i, v in enumerate(self.EUROS)))
        out = subprocess.run([sys.executable, "-I", SCRIPT, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["measures"], ["Actual", "Plan"])

    def test_order_id_stays_an_id(self):
        self.assertEqual(self.role("OrderID", [str(10000 + i) for i in range(60)]), "id")
        self.assertEqual(self.role("Order_ID", ["1", "1", "2"]), "id")   # the name alone decides

    def test_unnamed_key_is_still_an_id(self):
        self.assertEqual(self.role("Order", [str(10000 + i) for i in range(60)]), "id")

    def test_negative_or_few_numbers_are_measures(self):
        self.assertEqual(self.role("Delta", [str(i - 30) for i in range(60)]), "measure")
        self.assertEqual(self.role("Order", [str(10000 + i) for i in range(50)]), "measure")


def two_sheets(path, names):
    """A workbook with one sheet per name; each sheet holds a header naming its sheet and one number."""
    sheets = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (n.replace("&", "&amp;").replace('"', "&quot;"), i, i)
                     for i, n in enumerate(names, 1))
    rels = "".join('<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' % (i, R, i)
                   for i in range(1, len(names) + 1))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>' % (M, R, sheets))
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % rels)
        for i in range(1, len(names) + 1):
            z.writestr("xl/worksheets/sheet%d.xml" % i,
                       '<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Sheet%d</t></is></c></row>'
                       '<row r="2"><c r="A2"><v>%d</v></c></row></sheetData></worksheet>' % (M, i, i))
    return path


class SheetChoice(unittest.TestCase):
    """Skills pass --sheet-index, so a sheet name from the file never reaches a command line."""
    NAMES = ["Notes", "Data $(touch PWNED)"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.book = two_sheets(os.path.join(self.tmp, "book.xlsx"), self.NAMES)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_profile(self, *args):
        return subprocess.run([sys.executable, "-I", SCRIPT, self.book, *args], cwd=self.tmp,
                              capture_output=True, text=True, timeout=60)

    def exit_message(self, *args):
        with self.assertRaises(SystemExit) as cm:
            PR.main([self.book, *args])
        return str(cm.exception.code)

    def test_index_picks_the_sheet_and_runs_nothing(self):
        out = self.run_profile("--sheet-index", "2")
        self.assertEqual(out.returncode, 0, out.stderr)
        m = json.loads(out.stdout)
        self.assertEqual(m["file"]["sheet"], self.NAMES[1])
        self.assertEqual(m["file"]["sheets"], self.NAMES)
        self.assertEqual(m["columns"][0]["name"], "Sheet2")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "PWNED")))
        self.assertFalse(os.path.exists(os.path.join(os.getcwd(), "PWNED")))

    def test_no_choice_is_the_first_sheet(self):
        read, rows = PR.read_xlsx(self.book)
        self.assertEqual((read["sheet"], rows[0][0]), ("Notes", "Sheet1"))

    def test_index_out_of_range_is_an_error(self):
        for bad in ("0", "3"):
            msg = self.exit_message("--sheet-index", bad)
            self.assertIn("out of range", msg)
            self.assertIn('2 "Data $(touch PWNED)"', msg)

    def test_index_must_be_a_number(self):
        self.assertIn("takes a number", self.exit_message("--sheet-index", "Data"))

    def test_unmatched_name_is_an_error(self):
        msg = self.exit_message("--sheet", "Dat")
        self.assertIn('no sheet named "Dat"', msg)
        self.assertIn('1 "Notes"', msg)

    def test_workbook_without_sheets_is_an_error(self):
        with zipfile.ZipFile(self.book, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="%s"/>' % M)
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        self.assertIn("no sheets in", self.exit_message())

    def test_option_without_value_is_an_error(self):
        for flag in ("--name", "--sheet-index", "--sheet"):
            self.assertIn(flag + " needs a value", self.exit_message(flag))

    def test_source_name_follows_the_manifest_rule(self):
        for bad in ("Sales 2025", "sales_2025", "-sales", "a--b", "x$(touch PWNED)"):
            self.assertIn("--name takes lowercase letters", self.exit_message("--sheet-index", "1", "--name", bad))
        out = self.run_profile("--sheet-index", "1", "--name", "sales-2025")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["name"], "sales-2025")

    def test_exact_name_still_works(self):
        read, rows = PR.read_xlsx(self.book, sheet="Notes")
        self.assertEqual((read["sheet"], rows[0][0]), ("Notes", "Sheet1"))


if __name__ == "__main__":
    unittest.main()


def raw_workbook(path, sheet_xml, shared=("Name",), compress=zipfile.ZIP_DEFLATED):
    """A one-sheet workbook whose sheet part is exactly `sheet_xml` (bytes)."""
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/></Relationships>' % R)
    with zipfile.ZipFile(path, "w", compression=compress) as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>' % (M, R))
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="%s">%s</sst>' % (M, "".join("<si><t>%s</t></si>" % s for s in shared)))
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return path


class Limits(unittest.TestCase):
    """A file the user points at can be huge or crafted; the profiler stops with one line instead of filling memory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = PR.MAX_FILE, PR.MAX_PART, PR.MAX_ROWS

    def tearDown(self):
        PR.MAX_FILE, PR.MAX_PART, PR.MAX_ROWS = self.saved
        shutil.rmtree(self.tmp)

    def exit_message(self, fn, *a):
        with self.assertRaises(SystemExit) as cm:
            fn(*a)
        return str(cm.exception.code)

    def test_workbook_that_unpacks_far_beyond_its_size_is_refused(self):
        rows = "".join('<row r="%d"><c r="A%d"><v>1</v></c></row>' % (i, i) for i in range(1, 2))
        sheet = ('<worksheet xmlns="%s"><sheetData>%s%s</sheetData></worksheet>'
                 % (M, rows, "<row/>" * 600000)).encode()  # ~3.6 MB of XML that packs to a few KB
        path = raw_workbook(os.path.join(self.tmp, "bomb.xlsx"), sheet)
        self.assertLess(os.path.getsize(path), 100000)
        self.assertIn("times its packed size", self.exit_message(PR.read_xlsx, path))

    def test_part_over_the_size_cap_is_refused(self):
        PR.MAX_PART = 2000
        path = workbook(os.path.join(self.tmp, "book.xlsx"), ["Month", "Actual"], [[(i, 0), (i * 10, 0)] for i in range(200)])
        self.assertIn("unpacks to", self.exit_message(PR.read_xlsx, path))

    def test_file_over_the_size_cap_is_refused(self):
        PR.MAX_FILE = 100
        path = os.path.join(self.tmp, "big.csv")
        with open(path, "w") as f:
            f.write("Month,Actual\n" + "".join("2026-%02d,%d\n" % (i % 12 + 1, i) for i in range(100)))
        self.assertIn("this profiler reads", self.exit_message(PR.main, [path]))

    def test_rows_over_the_cap_are_refused(self):
        PR.MAX_ROWS = 10
        book = workbook(os.path.join(self.tmp, "book.xlsx"), ["Month", "Actual"], [[(i, 0), (i, 0)] for i in range(20)])
        self.assertIn("more than 10 rows", self.exit_message(PR.read_xlsx, book))
        csv_path = os.path.join(self.tmp, "rows.csv")
        with open(csv_path, "w") as f:
            f.write("Month,Actual\n" + "".join("2026-01,%d\n" % i for i in range(20)))
        self.assertIn("more than 10 rows", self.exit_message(PR.read_csv, csv_path))

    def test_doctype_past_the_first_4kb_is_refused(self):
        sheet = ('<?xml version="1.0"?><!--%s--><!DOCTYPE worksheet [<!ELEMENT worksheet ANY>]>'
                 '<worksheet xmlns="%s"><sheetData/></worksheet>' % ("x" * 5000, M)).encode()
        path = raw_workbook(os.path.join(self.tmp, "late.xlsx"), sheet)
        self.assertIn("DOCTYPE", self.exit_message(PR.read_xlsx, path))

    def test_missing_shared_string_is_a_clear_error(self):
        sheet = ('<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="s"><v>7</v></c></row></sheetData></worksheet>' % M).encode()
        path = raw_workbook(os.path.join(self.tmp, "idx.xlsx"), sheet)
        self.assertIn("shared string '7'", self.exit_message(PR.read_xlsx, path))

    def test_cell_without_a_reference_follows_the_one_before(self):
        sheet = ('<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c t="inlineStr"><is><t>Actual</t></is></c></row>'
                 '<row r="2"><c><v>5</v></c><c><v>6</v></c></row></sheetData></worksheet>' % M).encode()
        _, rows = PR.read_xlsx(raw_workbook(os.path.join(self.tmp, "noref.xlsx"), sheet))
        self.assertEqual(rows, [["Name", "Actual"], ["5", "6"]])
