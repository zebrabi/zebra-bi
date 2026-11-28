"""Tests for the Excel Canvas sheet (office.py build --canvas). Run: python3 -I -m unittest discover <plugin>/scripts/tests

The real renderer is never a fixture: a few-line stand-in page with exactly the five parent-window uses stands in for it.
The shim's behaviour in a page runs through canvas_host.mjs in plain Node and is skipped where node is not on the PATH.
"""
import base64, contextlib, copy, datetime as dt, hashlib, io, json, os, random, re, shutil, subprocess, sys, tempfile, unittest, zipfile
from decimal import Decimal, ROUND_HALF_UP
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
import office as O  # noqa: E402
import story as S  # noqa: E402
from test_office import MONTH, REGION, SUBCAT, STORY, VISUALS, fixture  # noqa: E402
from zoffice import canvas as CV, check as C, ooxml as X, visual as V, xlsx as XL  # noqa: E402

PARENT = "window.parent"
NODE = shutil.which("node")
SHEET = "Revenue by month"
NOTICE = CV.notice(MONTH, SHEET)


def stand_in(parents=5, extra=""):
    """A renderer page with the anchors the canvas needs: a head, the handshake strings and `parents` parent-window uses.
    It holds an ampersand, angle brackets, a carriage return and a non-ASCII letter on purpose, to test the escaping."""
    uses = ";".join("var p%d=%s" % (i, PARENT) for i in range(parents))
    return ('<!doctype html>\r\n<html><head><meta charset="utf-8"><title>stand-in \u00e9</title></head><body>'
            '<script>%s;var a=1<2&&3>2;var h=["ui/initialize","ui/notifications/tool-result"];%s</script></body></html>' % (uses, extra))


def unsummed_total():
    v = copy.deepcopy(MONTH)
    v["grand_total"]["ac"] += 5000.0   # a ratio or a stock measure: the total is not the sum of the rows
    return v


def small(ac, py=None):
    """A flat chart of len(ac) rows with these values (a few under 1,000, some with decimals)."""
    v = copy.deepcopy(MONTH)
    n = len(ac)
    py = py or [x + 1 for x in ac]
    v["xcol"] = v["xcol"][:n]
    for field, vals in (("ycol_ac", ac), ("ycol_py", py)):
        v[field] = v[field][:n]
        for node, x in zip(v[field], vals):
            node["value"] = float(x)
    v["grand_total"]["ac"] = float(sum(ac))
    v["grand_total"]["py"] = float(sum(py))
    return v


def excel_cells(v, edits=None, shown=None):
    """What getDataBindingValue answers for the visual's data range: rows of text, as the build wrote them. With `shown`
    (a thousands separator) every number reads the way a #,##0 cell shows it in that locale instead of its plain value."""
    plain = lambda x: "" if x is None else str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)   # 5.0 reads "5" in Excel
    rows = [[row[0]] + [plain(x) for x in row[1:]] for row in V.flat_rows(v)]
    if shown:
        rows = [rows[0]] + [[r[0]] + [format(int(Decimal(x).quantize(Decimal(1), ROUND_HALF_UP)), ",").replace(",", shown)
                                      for x in r[1:]] for r in rows[1:]]
    for (r, c), value in (edits or {}).items():
        rows[r][c] = value
    return rows


def rewrite(src, dst, edits):
    """Copy a package, applying {part: function(text) -> text}; a function that returns None drops the part."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n in edits:
                data = edits[n](data.decode("utf-8"))
                if data is None:
                    continue
                data = data.encode("utf-8")
            zout.writestr(n, data)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        placeholder = os.path.join(X.ASSETS, "snapshot-placeholder.png")
        self.snaps = {k: placeholder for k in VISUALS}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, renderer=None, visual="sales-by-month", name="story.xlsx", visuals=None):
        out = os.path.join(self.tmp, name)
        canvas = {"visual": visual, "renderer_html": renderer if renderer is not None else stand_in()} if visual else None
        XL.build(STORY, visuals or VISUALS, self.snaps, out, "Source: test.", B.load(start=self.tmp), canvas=canvas)
        return out


class Guard(Base):
    def test_the_stand_in_passes(self):
        CV.check_renderer(stand_in())

    def test_a_different_number_of_parent_uses_names_the_count(self):
        for n in (4, 6):
            with self.assertRaises(ValueError) as cm:
                CV.check_renderer(stand_in(parents=n))
            self.assertIn("%d %s uses, expected 5" % (n, PARENT), str(cm.exception))
            self.assertIn("build without --canvas", str(cm.exception))
            self.assertNotIn("\n", str(cm.exception))

    def test_each_other_unsafe_shape_is_refused(self):
        cases = {"an iframe": stand_in(extra="var f='<iframe src=x>';"),
                 "a script address": stand_in().replace("<script>", '<script src="https://x.invalid/a.js"></script><script>', 1),
                 "no head": stand_in().replace("<head>", "<headless>").replace("</head>", "</headless>"),
                 "a vertical tab": stand_in(extra="var v='\x0b';"),
                 "no handshake": stand_in().replace("ui/initialize", "ui/init")}
        for what, html in cases.items():
            with self.assertRaises(ValueError, msg=what) as cm:
                CV.check_renderer(html)
            self.assertTrue(str(cm.exception).startswith("canvas: the Zebra BI renderer changed ("), what)
            if what == "a vertical tab":
                self.assertIn("U+000B", str(cm.exception))

    def test_a_refused_renderer_leaves_no_file(self):
        out = os.path.join(self.tmp, "story.xlsx")
        for html in (stand_in(parents=4), stand_in(parents=6), stand_in(extra="<iframe>"), stand_in(extra="\x0b")):
            with self.assertRaises(ValueError):
                self.build(renderer=html)
            self.assertEqual(sorted(os.listdir(self.tmp)), [])
        self.assertFalse(os.path.exists(out))

    def test_a_table_is_drawn_and_an_unknown_visual_is_refused(self):
        out = self.build(visual="sales-by-region", name="table.xlsx")
        self.assertEqual(C.check(out)[0], [])
        os.remove(out)
        with self.assertRaises(ValueError) as cm:
            self.build(visual="nope")
        self.assertIn("not a visual", str(cm.exception))
        self.assertEqual(os.listdir(self.tmp), [])


class Page(unittest.TestCase):
    def test_the_parent_uses_point_at_the_page_and_the_shim_comes_first(self):
        page = CV.page(stand_in(), MONTH, version="1.2.3")
        clear, js = CV.unpack(page)
        self.assertEqual((clear + js).count(PARENT), 0)
        self.assertEqual(js.count("window.__zbiHost"), 5)
        self.assertEqual(clear.count("window.__zbiHost"), CV.shim(MONTH, version="1.2.3").count("window.__zbiHost"))
        self.assertLess(page.index("<script>"), page.index("window.__zbiGz="))   # the shim first, in the head
        self.assertLess(page.index("<script>"), page.index("</head>"))
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertNotRegex(clear, r"__[A-Z]+__")

    def test_the_renderer_script_ships_packed_and_the_loader_unpacks_it_in_place(self):
        filler = "var padding = 'zebra bi renderer text';\n" * 20000     # compressible, as the real script is
        renderer = stand_in(extra=filler)
        page = CV.page(renderer, MONTH, version="1")
        self.assertLess(len(page), len(renderer) // 5)
        self.assertNotIn("var padding", page)                        # the renderer body is not in clear text
        self.assertEqual(page.count("<script>window.__zbiGz=\""), 1)
        self.assertIn(CV.LOADER, page)
        self.assertIn("new DecompressionStream('gzip')", page)
        self.assertGreater(page.index(CV.LOADER), page.index("window.__zbiGz="))
        self.assertLess(page.index(CV.LOADER), page.index("</body>"))   # where the renderer's script was
        self.assertIn("<title>stand-in \u00e9</title>", page)         # the rest of the renderer page stays as it is
        clear, js = CV.unpack(page)
        want = renderer[renderer.index("<script>") + len("<script>"):renderer.index("</script>")].replace(PARENT, "window.__zbiHost")
        self.assertEqual(js, want)                                     # decompressing the string yields the patched script
        self.assertIn("var p0=window.__zbiHost", js)

    def test_no_script_block_is_typed_so_the_canvas_host_keeps_every_one(self):
        page = CV.page(stand_in(extra="var z='x';" * 50000), MONTH, version="1")
        tags = [m.group(0) for m in CV.SCRIPT_TAG.finditer(page)]
        self.assertEqual(len(tags), 3)                                 # the shim, the packed string and the loader
        self.assertEqual(set(tags), {"<script>"})
        self.assertNotIn("octet-stream", page)
        self.assertNotIn("application/json", page)

    def test_a_dashboard_page_draws_the_dashboard_and_stays_under_the_limit(self):
        d = CV.dashboard("T", [(MONTH, True), (REGION, False), (SUBCAT, False)])
        page = CV.page(stand_in(), d, version="1")
        self.assertLess(len(page.encode("utf-8")), CV.MAX_PAGE_BYTES)
        self.assertEqual(CV.built_result(page)["structuredContent"], d)
        self.assertIn("render_dashboard", page)
        self.assertGreater(len(page), len(CV.page(stand_in(), MONTH, version="1")))   # the other visuals ride in the page too

    def test_a_dashboard_page_over_the_limit_is_refused(self):
        noise = base64.b64encode(random.Random(7).randbytes(int(CV.MAX_PAGE_BYTES * 1.1))).decode()
        with self.assertRaises(ValueError) as cm:
            CV.page(stand_in(extra="//" + noise), CV.dashboard("T", [(MONTH, True)]))
        self.assertIn("over the 2 MB limit", str(cm.exception))

    def test_the_shim_holds_no_path_that_draws_from_cells(self):
        text = X.read(CV.SHIM)
        for word in ("__COLUMNS__", "__ADDITIVE__", "patched", "ADDITIVE", "COLUMNS"):
            self.assertNotIn(word, text)
        self.assertFalse(hasattr(CV, "additive") or hasattr(CV, "columns"))
        self.assertIsNone(re.search(r"\b__[A-Z]+__\b", CV.shim(MONTH, version="1")))   # every placeholder is filled

    def test_a_packing_is_repeatable(self):
        self.assertEqual(CV.page(stand_in(), MONTH, version="1"), CV.page(stand_in(), MONTH, version="1"))

    def test_a_page_over_the_limit_is_refused_with_the_size_and_what_was_seen(self):
        rng = random.Random(7)
        noise = base64.b64encode(rng.randbytes(int(CV.MAX_PAGE_BYTES * 1.1))).decode()   # does not compress
        with self.assertRaises(ValueError) as cm:
            CV.page(stand_in(extra="//" + noise), MONTH, version="1")
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("canvas: the page is "), msg)
        self.assertIn("over the 2 MB limit", msg)
        self.assertIn("4.4 MB", msg)
        self.assertIn("1.5 MB", msg)
        self.assertNotIn("\n", msg)

    def test_a_renderer_without_exactly_one_plain_script_after_the_body_is_refused(self):
        cases = {"two scripts": stand_in().replace("</body>", "<script>var q=1</script></body>"),
                 "a typed script": stand_in().replace("<script>", '<script type="module">', 1),
                 "a script in the head": stand_in().replace("<script>", "<p>").replace("</script>", "</p>").replace(
                     "<head>", "<head><script>var h=1</script>", 1),
                 "no script": stand_in().replace("<script>", "<p>").replace("</script>", "</p>")}
        for what, html in cases.items():
            with self.assertRaises(ValueError, msg=what) as cm:
                CV.check_renderer(html)
            self.assertIn("renderer changed", str(cm.exception), what)


class Writer(Base):
    def read(self, out, part):
        with zipfile.ZipFile(out) as z:
            return z.read(part)

    def test_the_workbook_passes_check_and_the_add_in_sheets_stay_put(self):
        out = self.build()
        errors, warnings, summary = C.check(out)
        self.assertEqual(errors, [])
        self.assertEqual(summary["canvas"], 1)
        self.assertEqual(summary["addins"], {"charts": 1, "tables": 2})
        want = "{00000000-0001-0000-%02d00-000000000000}" % 3  # Story is sheet 0, the third visual sheet is sheet 3
        self.assertIn('appref="%s"' % want, self.read(out, "xl/webextensions/webextension3.xml").decode())

    def test_the_canvas_is_the_last_sheet_and_its_name_is_bound_to_the_data(self):
        out = self.build()
        wb = ET.fromstring(self.read(out, "xl/workbook.xml"))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        sheets = list(wb.iter(ns + "sheet"))
        self.assertEqual([s.get("name") for s in sheets][-1], "Canvas")
        self.assertEqual(len(sheets), 5)
        rels = ET.fromstring(self.read(out, "xl/_rels/workbook.xml.rels"))
        last = sheets[-1].get("{%s}id" % X.NS_R)
        kinds = {r.get("Id"): r.get("Type") for r in rels}
        self.assertEqual(kinds[last], CV.REL_CHARTSHEET)
        self.assertEqual([kinds[s.get("{%s}id" % X.NS_R)] for s in sheets[:-1]], [XL.R + "worksheet"] * 4)
        name = wb.find(ns + "definedNames").find(ns + "definedName")
        self.assertEqual((name.get("name"), name.get("hidden")), ("_xlchart.v7.0", "1"))
        we = ET.fromstring(self.read(out, "xl/webextensions/webextension1.xml"))
        source = next(json.loads(p.get("value")) for p in we.iter("{%s}property" % X.NS_WE) if p.get("name") == "SourceData")
        self.assertEqual(name.text, source["range"])
        self.assertEqual(name.text, "'Eight months of twelve grew'!A4:C16")

    def test_binding_page_and_text_round_trip(self):
        out = self.build()
        raw = self.read(out, "xl/charts/chartEx1.xml")
        self.assertNotIn(b"<!DOCTYPE", raw)
        self.assertNotIn(b"<!ENTITY", raw)
        tree = ET.fromstring(raw)
        cx = "{%s}" % CV.NS_CX
        data = tree.find(cx + "chartData").find(cx + "data")
        binding = tree.find(cx + "dashboard").find(cx + "bindings").find(cx + "binding")
        self.assertEqual((binding.get("id"), binding.get("name"), binding.get("type")), (data.get("id"), "zebra", "cat"))
        self.assertEqual(data.find(cx + "strDim").find(cx + "f").text, "_xlchart.v7.0")
        art = tree.find(cx + "dashboard").find(cx + "artifacts").find(cx + "artifact")
        self.assertEqual((art.get("name"), art.get("type"), tree.find(cx + "dashboard").get("entrypoint")), ("index", "html", "index"))
        self.assertEqual(art.text, CV.page(stand_in(), MONTH, sheet="Eight months of twelve grew"))   # & < > and the carriage return come back exactly
        self.assertIn('the sheet \\"Eight months of twelve grew\\"', art.text)                          # the notice names the bound data sheet
        self.assertIn("\r", art.text)

    def test_no_canvas_means_the_workbook_of_today(self):
        out = self.build(visual=None)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            wb = z.read("xl/workbook.xml").decode()
        self.assertFalse([n for n in names if "chart" in n.lower()])
        self.assertNotIn("definedName", wb)
        self.assertNotIn("Canvas", wb)
        errors, _, summary = C.check(out)
        self.assertEqual((errors, summary["canvas"]), ([], 0))

    def test_the_canvas_sheet_name_avoids_a_visual_sheet(self):
        story = copy.deepcopy(STORY)
        story["sections"][0]["heading"] = "Canvas"
        out = os.path.join(self.tmp, "named.xlsx")
        XL.build(story, VISUALS, self.snaps, out, "Source: test.", B.load(start=self.tmp),
                 canvas={"visual": "sales-by-month", "renderer_html": stand_in()})
        wb = self.read(out, "xl/workbook.xml").decode()
        self.assertIn('name="Canvas 2"', wb)
        self.assertEqual(C.check(out)[0], [])


class CheckerNegatives(Base):
    def setUp(self):
        super().setUp()
        self.good = self.build()

    def broken(self, edits):
        dst = os.path.join(self.tmp, "broken.xlsx")
        rewrite(self.good, dst, edits)
        return C.check(dst)

    def test_an_iframe_in_the_page(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("</cx:artifact>", "&lt;iframe&gt;</cx:artifact>")})
        self.assertTrue(any("<iframe>" in e for e in errors), errors)

    def test_a_script_address_in_the_page(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("</cx:artifact>", "&lt;script src=&quot;a.js&quot;&gt;</cx:artifact>")})
        self.assertTrue(any("loads a script from an address" in e for e in errors), errors)

    def test_a_parent_window_use_left_in_the_page(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("</cx:artifact>", PARENT + "</cx:artifact>")})
        self.assertTrue(any("still has window.parent" in e for e in errors), errors)

    def test_a_page_that_never_reads_its_cells(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("getDataBindingValue", "getDataBindingValu")})
        self.assertTrue(any("never calls getDataBindingValue" in e for e in errors), errors)

    def test_a_binding_without_data(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace('<cx:binding name="zebra" id="0"', '<cx:binding name="zebra" id="7"')})
        self.assertTrue(any("no cx:data has that id" in e for e in errors), errors)

    def test_a_defined_name_on_a_missing_sheet(self):
        errors, _, _ = self.broken({"xl/workbook.xml": lambda t: t.replace("'Eight months of twelve grew'!", "'Nowhere'!")})
        self.assertTrue(any("names sheet 'Nowhere', which does not exist" in e for e in errors), errors)

    def test_a_defined_name_beyond_the_data(self):
        errors, _, _ = self.broken({"xl/workbook.xml": lambda t: t.replace("A4:C16", "A4:C99")})
        self.assertTrue(any("beyond the written data" in e for e in errors), errors)

    def test_a_missing_defined_name(self):
        errors, _, _ = self.broken({"xl/workbook.xml": lambda t: re.sub(r"<definedNames>.*?</definedNames>", "", t)})
        self.assertTrue(any("not a defined name in the workbook" in e for e in errors), errors)

    def test_a_chartex_no_sheet_reaches(self):
        errors, _, _ = self.broken({"xl/workbook.xml": lambda t: re.sub(r'<sheet name="Canvas"[^>]*/>', "", t)})
        self.assertTrue(any("not reachable from a chartsheet" in e for e in errors), errors)

    def test_a_canvas_before_an_add_in_sheet(self):
        def move(t):
            canvas = re.search(r'<sheet name="Canvas"[^>]*/>', t).group(0)
            return t.replace(canvas, "").replace('<sheet name="Phones', canvas + '<sheet name="Phones', 1)
        errors, _, _ = self.broken({"xl/workbook.xml": move})
        self.assertTrue(any("comes before a sheet that holds a Zebra BI add-in" in e for e in errors), errors)

    def test_a_dashboard_whose_entrypoint_names_nothing(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace('entrypoint="index"', 'entrypoint="home"')})
        self.assertTrue(any("names no html artifact" in e for e in errors), errors)

    def test_a_page_above_two_megabytes_is_an_error_that_names_the_size_seen(self):
        rng = random.Random(3)
        noise = base64.b64encode(rng.randbytes(int(CV.MAX_PAGE_BYTES * 0.8))).decode()   # base64 of noise: about 1.1x the limit
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("</cx:artifact>", noise + "</cx:artifact>")})
        self.assertTrue(any("over the 2 MB limit" in e and "4.4 MB" in e and "1.5 MB" in e for e in errors), errors)

    def test_a_non_javascript_script_block_is_an_error(self):
        for kind in ("application/octet-stream", "application/json"):
            block = '&lt;script type=&quot;%s&quot;&gt;AAAA&lt;/script&gt;' % kind
            errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("</cx:artifact>", block + "</cx:artifact>")})
            self.assertTrue(any("type %r" % kind in e and "not JavaScript" in e for e in errors), (kind, errors))

    def test_a_javascript_type_is_fine(self):
        block = '&lt;script type=&quot;text/javascript&quot;&gt;var a=1&lt;/script&gt;'
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("</cx:artifact>", block + "</cx:artifact>")})
        self.assertEqual(errors, [])

    def test_a_packed_string_that_does_not_unpack_is_an_error(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: re.sub(r'window\.__zbiGz="[A-Za-z0-9+/=]*"', 'window.__zbiGz="AAAAAAAA"', t, count=1)})
        self.assertTrue(any("cannot be unpacked" in e for e in errors), errors)

    def test_a_packed_page_without_its_loader_is_an_error(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("DecompressionStream", "DecompressionStrea")})
        self.assertTrue(any("no loader that unpacks it" in e for e in errors), errors)

    def rebuilt(self, content):
        """The good workbook with its page rebuilt around `content` (a visual or a dashboard, however malformed)."""
        page = CV.page(stand_in(), MONTH, version="1")
        built = CV._embed(CV.result(MONTH))
        bad = page.replace(built, CV._embed(content), 1)
        self.assertNotEqual(bad, page)
        return self.broken({"xl/charts/chartEx1.xml": lambda t: re.sub(
            r"(<cx:artifact [^>]*>).*(</cx:artifact>)", lambda m: m.group(1) + CV.escape_text(bad) + m.group(2), t, flags=re.S)})

    def dash(self, items):
        return {"content": [], "structuredContent": {"dashboard": {"title": "T", "items": items}}, "isError": False}

    def test_a_dashboard_page_passes_with_one_to_six_items(self):
        for n in (1, 2, 6):
            errors, _, _ = self.rebuilt(self.dash([{"width": 6, "visual": MONTH}] * n))
            self.assertEqual(errors, [], n)

    def test_a_dashboard_with_no_items_or_more_than_six_is_an_error(self):
        for n in (0, 7):
            errors, _, _ = self.rebuilt(self.dash([{"width": 6, "visual": MONTH}] * n))
            self.assertTrue(any("holds %d items, expected 1 to 6" % n in e for e in errors), (n, errors))

    def test_a_dashboard_item_without_a_visual_or_with_a_width_out_of_range_is_an_error(self):
        errors, _, _ = self.rebuilt(self.dash([{"width": 6}]))
        self.assertTrue(any("item 1 has no visual" in e for e in errors), errors)
        for w in (2, 13, "6", None):
            errors, _, _ = self.rebuilt(self.dash([{"width": w, "visual": MONTH}]))
            self.assertTrue(any("item 1 has width" in e for e in errors), (w, errors))

    def test_a_result_that_is_neither_a_visual_nor_a_dashboard_is_an_error(self):
        errors, _, _ = self.rebuilt({"content": [], "structuredContent": {"chart": {}}, "isError": False})
        self.assertTrue(any("neither a visual nor a dashboard" in e for e in errors), errors)

    def test_a_page_without_its_built_result_is_an_error(self):
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: t.replace("BUILT = ", "BUILT_ = ")})
        self.assertTrue(any("embeds no result" in e for e in errors), errors)

    def test_a_parent_window_use_inside_the_packed_script_is_found(self):
        page = CV.page(stand_in(), MONTH, version="1")
        _, js = CV.unpack(page)
        packed = re.search(r'window\.__zbiGz="([^"]*)"', page).group(1)
        bad = page.replace(packed, CV.pack(js + ";" + PARENT))
        errors, _, _ = self.broken({"xl/charts/chartEx1.xml": lambda t: re.sub(
            r"(<cx:artifact [^>]*>).*(</cx:artifact>)", lambda m: m.group(1) + CV.escape_text(bad) + m.group(2), t, flags=re.S)})
        self.assertTrue(any("still has window.parent" in e for e in errors), errors)


class Selection(unittest.TestCase):
    def test_bare_canvas_takes_every_visual_in_section_order_and_binds_the_first(self):
        used = ["sales-by-region", "sales-by-month", "sales-by-subcategory"]
        self.assertEqual(O.pick_canvas(True, used, VISUALS), (used, []))

    def test_a_visual_in_two_sections_is_drawn_once(self):
        self.assertEqual(O.pick_canvas(True, ["a", "b", "a"], {})[0], ["a", "b"])

    def test_a_named_visual_is_taken_alone_and_a_table_is_allowed(self):
        self.assertEqual(O.pick_canvas("sales-by-month", ["sales-by-month", "sales-by-region"], VISUALS), (["sales-by-month"], []))
        self.assertEqual(O.pick_canvas("sales-by-region", ["sales-by-region"], VISUALS), (["sales-by-region"], []))

    def test_more_than_six_visuals_take_the_first_six_and_name_the_rest(self):
        used = ["v%d" % i for i in range(9)]
        self.assertEqual(O.pick_canvas(True, used, {}), (used[:6], used[6:]))

    def test_an_unknown_id_and_a_story_without_a_visual_stop_with_one_line(self):
        for canvas, used in (("nope", ["sales-by-month"]), (True, [])):
            with self.assertRaises(SystemExit) as cm:
                O.pick_canvas(canvas, used, VISUALS)
            self.assertTrue(str(cm.exception.code).startswith("canvas: "))
            self.assertIn("No workbook was written", str(cm.exception.code))
            self.assertNotIn("\n", str(cm.exception.code))


class Layout(unittest.TestCase):
    def test_a_time_chart_takes_the_row_and_tables_and_other_charts_take_half(self):
        self.assertTrue(CV.is_wide(MONTH, {"time_axis": "yearmonth"}))
        self.assertFalse(CV.is_wide(MONTH, {}))
        self.assertFalse(CV.is_wide(MONTH))
        marked = copy.deepcopy(MONTH)
        marked["is_date_category"] = [True]
        self.assertTrue(CV.is_wide(marked))
        self.assertFalse(CV.is_wide(REGION, {"time_axis": "yearmonth"}))   # a table is half width

    def test_rows_sum_to_twelve_and_a_lone_half_becomes_a_whole_row(self):
        cases = {(True,): [12], (False,): [12], (False, False): [6, 6], (True, False, False): [12, 6, 6],
                 (False, True): [12, 12], (False, False, False): [6, 6, 12], (False, True, False, False): [12, 12, 6, 6],
                 (True, False, True, False, False, False): [12, 12, 12, 6, 6, 12]}
        for wide, want in cases.items():
            got = CV.layout(list(wide))
            self.assertEqual(got, want, wide)
            row = 0
            for w in got:   # a row is closed exactly at 12
                row += w
                self.assertLessEqual(row, 12)
                row %= 12
            self.assertEqual(row, 0, wide)

    def test_the_dashboard_result_has_the_render_dashboard_shape(self):
        d = CV.dashboard("Title", [(MONTH, True), (REGION, False), (SUBCAT, False)])
        self.assertEqual(list(d), ["dashboard"])
        self.assertEqual(d["dashboard"]["title"], "Title")
        items = d["dashboard"]["items"]
        self.assertEqual([(i["width"], list(i)) for i in items], [(12, ["width", "visual"]), (6, ["width", "visual"]), (6, ["width", "visual"])])
        self.assertEqual([i["visual"] for i in items], [MONTH, REGION, SUBCAT])
        self.assertIsNone(CV.result_error(CV.result(d)))

    def test_a_dashboard_holds_one_to_six_visuals(self):
        for n in (0, 7):
            with self.assertRaises(ValueError):
                CV.dashboard("T", [(MONTH, False)] * n)
        self.assertEqual(len(CV.dashboard("T", [(MONTH, False)] * 6)["dashboard"]["items"]), 6)

    def test_the_result_check_names_what_is_wrong(self):
        good = CV.dashboard("T", [(MONTH, True)])
        bad = {"zero": {"structuredContent": {"dashboard": {"title": "T", "items": []}}},
               "seven": {"structuredContent": {"dashboard": {"title": "T", "items": [{"width": 6, "visual": MONTH}] * 7}}},
               "width": {"structuredContent": {"dashboard": {"title": "T", "items": [{"width": 2, "visual": MONTH}]}}},
               "no visual": {"structuredContent": {"dashboard": {"title": "T", "items": [{"width": 6}]}}},
               "neither": {"structuredContent": {"other": 1}}, "nothing": {}}
        self.assertIsNone(CV.result_error(CV.result(good)))
        self.assertIsNone(CV.result_error(CV.result(MONTH)))
        for what, res in bad.items():
            self.assertIsNotNone(CV.result_error(res), what)


class Cli(unittest.TestCase):
    """office.py with --canvas, against a cached stand-in renderer and cached chart results: no network and no browser."""

    ARGS = {"title": "Sample Co, Sales by month", "chart_type": "variance", "columns": ["Month", "AC", "PY"], "rows": [["Jan", 1, 1]]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = S.CACHE, os.environ.get("ZEBRA_BI_CHROME")
        S.CACHE = os.path.join(self.tmp, "cache", "renderer")
        os.makedirs(S.CACHE)
        os.environ["ZEBRA_BI_CHROME"] = os.path.join(self.tmp, "no-chrome")
        self.dir = os.path.join(self.tmp, "sample-co")
        os.makedirs(os.path.join(self.dir, "visuals"))
        story = {"title": "T", "sections": [{"heading": "Months", "body": "Text.", "visual": "months"},
                                            {"heading": "Regions", "body": "Text.", "visual": "regions"}],
                 "visuals": {"months": {"args": self.ARGS}, "regions": {"args": self.ARGS}}}
        with open(os.path.join(self.dir, "story.json"), "w") as f:
            json.dump(story, f)
        for vid, visual in (("months", MONTH), ("regions", REGION)):
            with open(os.path.join(self.dir, "visuals", vid + ".json"), "w") as f:
                json.dump({"args_hash": S.args_hash(self.ARGS), "result": {"structuredContent": {"visual": visual}}}, f)
        self.renderer(stand_in())

    def tearDown(self):
        S.CACHE = self.saved[0]
        if self.saved[1] is None:
            os.environ.pop("ZEBRA_BI_CHROME", None)
        else:
            os.environ["ZEBRA_BI_CHROME"] = self.saved[1]
        shutil.rmtree(self.tmp)

    def renderer(self, html):
        data = html.encode("utf-8")
        with open(os.path.join(S.CACHE, "visual.html"), "wb") as f:
            f.write(data)
        with open(os.path.join(S.CACHE, "visual.json"), "w") as f:
            json.dump({"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "endpoint": S.ENDPOINT,
                       "fetched_at": dt.datetime.now().isoformat()}, f)

    def run_build(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                O.main(["build", self.dir, *argv])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def page_of(self, name="sample-co.xlsx"):
        """(the page of the canvas in the built workbook, the structuredContent it embeds)."""
        with zipfile.ZipFile(os.path.join(self.dir, name)) as z:
            tree = ET.fromstring(z.read("xl/charts/chartEx1.xml"))
        art = tree.find("{%s}dashboard" % CV.NS_CX).find("{%s}artifacts" % CV.NS_CX).find("{%s}artifact" % CV.NS_CX)
        return art.text, CV.built_result(CV.unpack(art.text)[0])["structuredContent"]

    def add_visual(self, vid, visual, **args):
        """Another section of the story, with its cached result, called with ARGS plus `args`."""
        call = dict(self.ARGS, **args)
        path = os.path.join(self.dir, "story.json")
        with open(path) as f:
            story = json.load(f)
        story["sections"].append({"heading": vid, "body": "Text.", "visual": vid})
        story["visuals"][vid] = {"args": call}
        with open(path, "w") as f:
            json.dump(story, f)
        with open(os.path.join(self.dir, "visuals", vid + ".json"), "w") as f:
            json.dump({"args_hash": S.args_hash(call), "result": {"structuredContent": {"visual": visual}}}, f)

    def test_a_build_with_canvas_draws_the_visuals_as_one_dashboard_and_says_what_the_workbook_carries(self):
        code, out, err = self.run_build("--xlsx", "--canvas")
        self.assertIsNone(code, err)
        info = json.loads(out[out.index("{"):])
        xlsx = info["files"][0]
        self.assertEqual(xlsx["file"], os.path.join(self.dir, "sample-co.xlsx"))
        self.assertEqual((xlsx["canvas"]["visual"], xlsx["canvas"]["visuals"], xlsx["canvas"]["left_out"]),
                         ("months", ["months", "regions"], []))
        self.assertEqual(xlsx["addins"], {"charts": 1, "tables": 1})
        line = next(l for l in err.splitlines() if l.startswith("canvas: this workbook carries the Zebra BI renderer"))
        self.assertIn("sha256 " + hashlib.sha256(stand_in().encode()).hexdigest()[:12], line)
        self.assertIn("without the add-in, a Zebra BI sign-in or a licence check", line)
        self.assertIn("draws 2 visuals as one dashboard as built, without the add-in", line)
        self.assertNotIn("redraw", line)
        self.assertIn(", packed into a ", line)
        self.assertIn("MB page (limit 2 MB)", line)
        self.assertGreater(xlsx["canvas"]["page_bytes"], 0)
        self.assertLess(xlsx["canvas"]["page_bytes"], CV.MAX_PAGE_BYTES)
        page, sc = self.page_of()
        self.assertEqual(len(page.encode("utf-8")), xlsx["canvas"]["page_bytes"])
        self.assertEqual(list(sc), ["dashboard"])
        self.assertEqual(sc["dashboard"]["title"], "T")
        self.assertEqual([(i["width"], i["visual"]) for i in sc["dashboard"]["items"]], [(6, MONTH), (6, REGION)])
        with open(os.path.join(self.dir, "story.json")) as f:
            sheet = XL.sheet_names(json.load(f))["months"]
        self.assertIn(json.dumps(CV.notice(sc, sheet))[1:-1].replace("<", "\\u003c"), page)   # the notice names the bound visual's sheet
        errors, _, summary = C.check(os.path.join(self.dir, "sample-co.xlsx"))
        self.assertEqual((errors, summary["canvas"]), ([], 1))

    def test_a_time_chart_is_a_whole_row_and_a_lone_half_width_visual_becomes_one_too(self):
        self.add_visual("trend", MONTH, time_axis="yearmonth")
        self.add_visual("regions2", REGION)
        code, out, err = self.run_build("--xlsx", "--canvas")
        self.assertIsNone(code, err)
        _, sc = self.page_of()
        items = sc["dashboard"]["items"]
        self.assertEqual([i["width"] for i in items], [6, 6, 12, 12])   # months + regions, trend, then regions2 alone
        self.assertEqual([i["visual"] for i in items], [MONTH, REGION, MONTH, REGION])

    def test_a_named_visual_keeps_drawing_alone_and_a_table_is_drawn_too(self):
        code, out, err = self.run_build("--canvas=months", "--xlsx")
        self.assertIsNone(code, err)
        canvas = json.loads(out[out.index("{"):])["files"][0]["canvas"]
        self.assertEqual((canvas["visual"], canvas["visuals"]), ("months", ["months"]))
        _, sc = self.page_of()
        self.assertEqual(sc, {"visual": MONTH})
        line = next(l for l in err.splitlines() if l.startswith("canvas: this workbook carries"))
        self.assertIn("draws one visual as built, without the add-in", line)
        self.assertNotIn("redraw", line)
        code, out, err = self.run_build("--canvas=regions", "--xlsx")
        self.assertIsNone(code, err)
        _, sc = self.page_of()
        self.assertEqual(sc, {"visual": REGION})
        self.assertEqual(C.check(os.path.join(self.dir, "sample-co.xlsx"))[0], [])

    def test_more_than_six_visuals_draw_the_first_six_and_the_build_names_the_rest(self):
        for i in range(3, 10):
            self.add_visual("extra%d" % i, MONTH)
        code, out, err = self.run_build("--xlsx", "--canvas")
        self.assertIsNone(code, err)
        self.assertIn("canvas: a dashboard draws at most 6 visuals, so these were left out: extra7, extra8, extra9", err)
        canvas = json.loads(out[out.index("{"):])["files"][0]["canvas"]
        self.assertEqual((canvas["visuals"], canvas["left_out"]), (["months", "regions", "extra3", "extra4", "extra5", "extra6"],
                                                                 ["extra7", "extra8", "extra9"]))
        _, sc = self.page_of()
        self.assertEqual(len(sc["dashboard"]["items"]), 6)
        self.assertEqual(C.check(os.path.join(self.dir, "sample-co.xlsx"))[0], [])

    def test_six_visuals_leave_nothing_out(self):
        for i in range(2, 6):
            self.add_visual("extra%d" % i, MONTH)
        code, out, err = self.run_build("--xlsx", "--canvas")
        self.assertIsNone(code, err)
        self.assertNotIn("left out", err)
        self.assertEqual(len(self.page_of()[1]["dashboard"]["items"]), 6)

    def test_without_canvas_nothing_carries_the_renderer(self):
        code, out, err = self.run_build("--xlsx")
        self.assertIsNone(code, err)
        self.assertNotIn("renderer (", err)
        self.assertEqual(json.loads(out[out.index("{"):])["files"][0]["canvas"], 0)

    def test_a_deck_only_build_with_canvas_stops_with_one_line_and_writes_nothing(self):
        code, out, _ = self.run_build("--pptx", "--canvas")
        self.assertEqual(code, "canvas: --canvas goes in the workbook; add --xlsx. No file was written.")
        self.assertEqual(os.listdir(self.dir), ["story.json", "visuals"])

    def test_an_unknown_id_writes_no_workbook(self):
        code, _, _ = self.run_build("--xlsx", "--canvas=nope")
        self.assertTrue(str(code).startswith("canvas: "), code)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "sample-co.xlsx")))

    def test_a_renderer_that_changed_stops_the_build_and_leaves_no_file(self):
        for parents in (4, 6):
            self.renderer(stand_in(parents=parents))
            for flags in (("--xlsx", "--canvas"), ("--canvas",)):   # no format flag builds the deck and the workbook
                code, _, err = self.run_build(*flags)
                self.assertTrue(str(code).startswith("canvas: the Zebra BI renderer changed ("), code)
                self.assertIn("%d window.parent uses, expected 5" % parents, str(code))
                self.assertTrue(str(code).endswith("No file was written."), code)
                self.assertEqual(sorted(os.listdir(self.dir)), ["story.json", "visuals"])

    def test_a_page_over_the_size_limit_stops_the_build_and_leaves_no_file(self):
        noise = base64.b64encode(random.Random(5).randbytes(int(CV.MAX_PAGE_BYTES * 1.1))).decode()
        self.renderer(stand_in(extra="//" + noise))
        for flags in (("--xlsx", "--canvas"), ("--canvas",)):
            code, _, _ = self.run_build(*flags)
            self.assertTrue(str(code).startswith("canvas: the page is "), code)
            self.assertIn("over the 2 MB limit", str(code))
            self.assertTrue(str(code).endswith("No file was written."), code)
            self.assertEqual(sorted(os.listdir(self.dir)), ["story.json", "visuals"])

    def test_a_report_folder_says_no_canvas_was_written(self):
        import report as R
        R._legacy_checked = True
        rep = R.new("Q4 close")
        for vid in ("months", "regions"):
            R.add_chart(rep, dict(self.ARGS, title=vid), message="About " + vid, note="")
        os.remove(os.path.join(self.dir, "story.json"))
        R.save(os.path.join(self.dir, R.FILE), rep)
        for vid, visual in (("months", MONTH), ("regions", REGION)):
            with open(os.path.join(self.dir, "visuals", vid + ".json"), "w") as f:
                json.dump({"args_hash": S.args_hash(dict(self.ARGS, title=vid)), "result": {"structuredContent": {"visual": visual}}}, f)
        code, out, err = self.run_build("--xlsx", "--canvas")
        self.assertIsNone(code, err)
        self.assertIn("A report builds as PowerPoint only; no canvas was written.", err)
        self.assertNotIn("renderer (", err)
        self.assertEqual([os.path.basename(f["file"]) for f in json.loads(out[out.index("{"):])["files"]], ["sample-co.pptx"])


@unittest.skipUnless(NODE, "node is not on the PATH")
class LoaderInAPage(Base):
    """The loader of the packed page, run in plain Node with a stub document: it must append the unpacked script to the body."""

    def test_the_loader_appends_the_unpacked_renderer_script_to_the_body(self):
        page = CV.page(stand_in(extra="var tail='end';"), MONTH, version="1")
        gz = re.search(r'<script>(window\.__zbiGz="[^"]*";)</script>', page).group(1)
        loader = CV.LOADER[len("<script>"):-len("</script>")]
        driver = os.path.join(self.tmp, "loader.mjs")
        with open(driver, "w") as f:
            f.write("const appended = [];\n"
                    "globalThis.window = globalThis;\n"
                    "globalThis.document = { body: { appendChild(el) { appended.push(el); } }, createElement: () => ({}) };\n"
                    + gz + "\n(0, eval)(%s);\n" % json.dumps(loader)
                    + "await new Promise((r) => setTimeout(r, 200));\n"
                      "process.stdout.write(JSON.stringify({ n: appended.length, js: appended[0] && appended[0].textContent, gz: window.__zbiGz }));\n")
        done = subprocess.run([NODE, driver], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        got = json.loads(done.stdout)
        self.assertEqual(got["n"], 1)
        self.assertEqual(got["js"], CV.unpack(page)[1])
        self.assertIn("var tail='end';", got["js"])
        self.assertIsNone(got["gz"])                       # the string is released once unpacked


class ShimRunner(Base):
    """Runs assets/canvas-shim.js in plain Node with a stub window and reports what it sends the renderer."""

    def run_shim(self, v, cells, steps=()):
        shim = os.path.join(self.tmp, "shim.js")
        scenario = os.path.join(self.tmp, "scenario.json")
        with open(shim, "w") as f:
            f.write(CV.shim(v, version="9.9.9", sheet=SHEET))
        with open(scenario, "w") as f:
            json.dump({"cells": cells, "steps": list(steps)}, f)
        done = subprocess.run([NODE, os.path.join(HERE, "canvas_host.mjs"), shim, scenario], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def visual(self, res, i=-1):
        return res["results"][i]["structuredContent"]["visual"]


@unittest.skipUnless(NODE, "node is not on the PATH")
class ShimInAPage(ShimRunner):
    """What the shim sends the renderer for each state of the cells."""

    def test_the_host_answers_the_handshake_the_app_needs(self):
        res = self.run_shim(MONTH, excel_cells(MONTH))
        self.assertEqual(res["init"]["hostInfo"], {"name": "zebra-bi-excel-canvas", "version": "9.9.9"})
        self.assertEqual(res["init"]["hostContext"]["toolInfo"]["tool"]["inputSchema"], {"type": "object"})
        self.assertEqual(res["counts"], [1])

    def test_unchanged_cells_draw_the_built_result_so_the_servers_totals_stand(self):
        v = unsummed_total()
        step = {"key": "zebra", "cells": excel_cells(v)}
        res = self.run_shim(v, excel_cells(v), [step])
        self.assertEqual(res["counts"], [1, 1])
        self.assertEqual(self.visual(res), v)
        self.assertEqual(self.visual(res)["grand_total"]["ac"], v["grand_total"]["ac"])
        self.assertFalse(res["notice"]["shown"])

    def test_cells_shown_the_way_a_number_format_shows_them_are_unchanged(self):
        res = self.run_shim(MONTH, excel_cells(MONTH, shown=","))
        self.assertEqual(res["counts"], [1])
        self.assertEqual(self.visual(res), MONTH)
        self.assertFalse(res["notice"]["shown"])

    def test_an_edited_value_keeps_the_built_drawing_and_shows_the_notice(self):
        edited = excel_cells(MONTH, {(1, 1): "43576"})   # January AC: 43,476 -> 43,576
        res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": edited}])
        self.assertEqual(res["counts"], [1, 1])                 # nothing is sent again: the drawing and its state stay
        self.assertEqual(self.visual(res), MONTH)
        self.assertEqual(res["notice"], {"shown": True, "text": NOTICE})

    def test_an_edited_label_keeps_the_built_drawing_and_shows_the_notice(self):
        edited = excel_cells(MONTH, {(1, 0): "Start"})
        res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": edited}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertEqual(self.visual(res), MONTH)
        self.assertEqual(res["notice"], {"shown": True, "text": NOTICE})

    def test_no_content_of_an_edited_cell_reaches_the_drawing(self):
        edited = excel_cells(MONTH, {(1, 0): "ZZ-EDITED", (1, 1): "987654321", (2, 1): "ZZ-TEXT"})
        for cells, steps in ((edited, []), (excel_cells(MONTH), [{"key": "zebra", "cells": edited}])):
            res = self.run_shim(MONTH, cells, steps)
            sent = json.dumps(res["results"])
            for token in ("ZZ-EDITED", "987654321", "ZZ-TEXT"):
                self.assertNotIn(token, sent)
            self.assertEqual(self.visual(res), MONTH)
            self.assertTrue(res["notice"]["shown"])

    def test_cells_shown_in_any_grouping_are_unchanged_and_an_edit_shows_the_notice(self):
        for sep in (",", ".", "\u00a0", "\u202f", "'", " "):
            shown = excel_cells(MONTH, shown=sep)
            res = self.run_shim(MONTH, shown, [{"key": "zebra", "cells": shown}])
            self.assertEqual(res["counts"], [1, 1], repr(sep))
            self.assertEqual(self.visual(res), MONTH, repr(sep))
            self.assertFalse(res["notice"]["shown"], repr(sep))
            # an edit in the same mode reads as a grouped number: the drawing stays, the notice is shown
            edited = excel_cells(MONTH, {(1, 1): format(43576, ",").replace(",", sep)}, shown=sep)
            res = self.run_shim(MONTH, shown, [{"key": "zebra", "cells": edited}])
            self.assertEqual(res["counts"], [1, 1], repr(sep))
            self.assertEqual(self.visual(res), MONTH, repr(sep))
            self.assertEqual(res["notice"], {"shown": True, "text": NOTICE}, repr(sep))

    def test_a_grouped_number_among_plain_ones_shows_the_notice_not_a_value_a_thousand_times_too_small(self):
        for cell in ("43.476", "43,476", "43 476", "43.576"):
            res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": excel_cells(MONTH, {(1, 1): cell})}])
            self.assertEqual(res["counts"], [1, 1], cell)
            self.assertEqual(self.visual(res), MONTH, cell)
            self.assertEqual(res["notice"], {"shown": True, "text": NOTICE}, cell)

    def test_cells_changed_before_the_page_opens_draw_the_built_result_at_once_with_the_notice(self):
        res = self.run_shim(MONTH, excel_cells(MONTH, {(1, 1): "43576"}))
        self.assertEqual(res["counts"], [1])
        self.assertEqual(self.visual(res), MONTH)
        self.assertEqual(res["notice"], {"shown": True, "text": NOTICE})

    def test_a_cell_that_is_not_a_plain_number_shows_the_notice_and_sends_nothing_new(self):
        for bad in ("abc", "1,234", "1.234", "", "12.5.1"):
            res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": excel_cells(MONTH, {(1, 1): bad})}])
            self.assertEqual(res["counts"], [1, 1], bad)
            self.assertEqual(self.visual(res), MONTH, bad)
            self.assertEqual(res["notice"], {"shown": True, "text": NOTICE}, bad)

    def test_a_different_row_count_or_header_shows_the_notice(self):
        fewer = excel_cells(MONTH)[:-1]
        renamed = excel_cells(MONTH)
        renamed[0][1] = "Revenue"
        for cells in (fewer, renamed):
            res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": cells}])
            self.assertEqual(res["counts"], [1, 1])
            self.assertEqual(self.visual(res), MONTH)
            self.assertEqual(res["notice"], {"shown": True, "text": NOTICE})

    def test_the_notice_goes_when_the_cells_are_put_back(self):
        res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": excel_cells(MONTH, {(1, 1): "abc"})},
                                                       {"key": "zebra", "cells": excel_cells(MONTH)}])
        self.assertEqual(res["counts"], [1, 1, 1])
        self.assertFalse(res["notice"]["shown"])

    def test_the_drawing_is_the_built_one_before_during_and_after_an_edit(self):
        res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": excel_cells(MONTH, {(1, 1): "1"})},
                                                       {"key": "zebra", "cells": excel_cells(MONTH)}])
        self.assertEqual(res["counts"], [1, 1, 1])
        self.assertTrue(all(r["structuredContent"]["visual"] == MONTH for r in res["results"]))
        self.assertFalse(res["notice"]["shown"])

    def test_a_change_event_for_another_binding_is_ignored(self):
        res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "other", "cells": excel_cells(MONTH, {(1, 1): "1"})}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertFalse(res["notice"]["shown"])

    def test_outside_excel_the_built_result_is_drawn_once(self):
        res = self.run_shim(MONTH, None, [{"key": "zebra", "cells": None}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertEqual(self.visual(res), MONTH)
        self.assertFalse(res["notice"]["shown"])

    UNREADABLE = ([], {"throws": True}, {"raw": "#REF!"}, {"raw": "null"}, {"raw": "{}"}, {"raw": "\"#REF!\""}, {"missing": True})

    def test_a_binding_that_cannot_be_read_keeps_a_notice_that_was_showing(self):
        for bad in self.UNREADABLE:
            edited = excel_cells(MONTH, {(1, 1): "43576"})
            res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": edited}, {"key": "zebra", "cells": bad}])
            self.assertEqual(res["counts"], [1, 1, 1], bad)
            self.assertEqual(self.visual(res), MONTH, bad)
            self.assertEqual(res["notice"], {"shown": True, "text": NOTICE}, bad)

    def test_a_binding_that_cannot_be_read_is_a_change_after_an_unedited_open(self):
        for bad in self.UNREADABLE:
            res = self.run_shim(MONTH, excel_cells(MONTH), [{"key": "zebra", "cells": bad}])
            self.assertEqual(res["counts"], [1, 1], bad)
            self.assertEqual(self.visual(res), MONTH, bad)
            self.assertEqual(res["notice"], {"shown": True, "text": NOTICE}, bad)

    def test_a_first_read_that_cannot_be_read_draws_the_built_result_once_with_the_notice(self):
        for bad in self.UNREADABLE:
            res = self.run_shim(MONTH, bad)
            self.assertEqual(res["counts"], [1], bad)
            self.assertEqual(self.visual(res), MONTH, bad)
            self.assertEqual(res["notice"], {"shown": True, "text": NOTICE}, bad)

    def test_the_notice_names_the_sheet_and_the_kind_of_visual_and_says_to_build_again(self):
        dash = CV.dashboard("T", [(MONTH, False), (REGION, False)])
        self.assertEqual(CV.notice(MONTH, SHEET),
                         'The cells on the sheet "Revenue by month" changed after this chart was built. It still shows the figures it '
                         'was built from. To show new figures, build the story again from the updated data.')
        self.assertIn("this table was built", CV.notice(REGION, "Regions"))
        self.assertIn("this dashboard was built", CV.notice(dash, SHEET))
        for text in (CV.notice(MONTH, SHEET), CV.notice(dash, SHEET), CV.notice(MONTH)):
            self.assertNotIn("Rebuild", text)
        self.assertIn("its data sheet", CV.notice(MONTH))

    def test_a_display_text_read_with_one_edit_shows_the_notice_and_never_draws_rounded_numbers(self):
        v = small([4.3, 2.0, 5.9, 3.6], [3.1, 2.2, 4.8, 3.9])
        shown = excel_cells(v, shown=",")                       # the text a #,##0 cell shows: 4 2 6 4
        res = self.run_shim(v, excel_cells(v), [{"key": "zebra", "cells": excel_cells(v, {(1, 1): "9"}, shown=",")}])
        self.assertEqual(shown[1][1], "4")
        self.assertEqual(res["counts"], [1, 1])
        self.assertEqual(self.visual(res), v)
        self.assertEqual(res["notice"], {"shown": True, "text": NOTICE})

    def test_a_raw_read_with_decimals_is_unchanged_and_one_edited_decimal_shows_the_notice(self):
        v = small([4.3, 2.0, 5.9, 3.6], [3.1, 2.2, 4.8, 3.9])
        res = self.run_shim(v, excel_cells(v), [{"key": "zebra", "cells": excel_cells(v)}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertFalse(res["notice"]["shown"])
        res = self.run_shim(v, excel_cells(v), [{"key": "zebra", "cells": excel_cells(v, {(1, 1): "9"})}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertEqual(self.visual(res), v)
        self.assertTrue(res["notice"]["shown"])

    def test_a_whole_number_edit_under_a_thousand_shows_the_notice(self):
        v = small([4, 2, 6, 4], [5, 3, 7, 5])      # whole numbers under 1,000 read the same raw or shown
        res = self.run_shim(v, excel_cells(v), [{"key": "zebra", "cells": excel_cells(v, {(1, 1): "9"})}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertEqual(self.visual(res), v)
        self.assertTrue(res["notice"]["shown"])

    def test_an_edit_that_has_the_digits_of_a_built_number_is_not_taken_as_that_number(self):
        v = small([1234, 5, 6, 7], [1, 2, 3, 4])
        edited = excel_cells(v, {(1, 1): "12.34"})
        res = self.run_shim(v, excel_cells(v), [{"key": "zebra", "cells": edited}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertEqual(self.visual(res), v)
        self.assertTrue(res["notice"]["shown"])
        # the same digits grouped are the way the built number shows
        res = self.run_shim(v, excel_cells(v), [{"key": "zebra", "cells": excel_cells(v, {(1, 1): "1.234"})}])
        self.assertEqual(res["counts"], [1, 1])
        self.assertFalse(res["notice"]["shown"])

    def test_a_label_with_spaces_around_it_is_not_a_change(self):
        v = unsummed_total()
        v["xcol"][0]["name"] = "2018-01 "
        v["xcol_name"] = [" Month"]
        res = self.run_shim(v, excel_cells(v))
        self.assertEqual(res["counts"], [1])
        self.assertFalse(res["notice"]["shown"])

    def test_an_open_sends_one_result_even_when_the_cells_answer_late(self):
        edited = excel_cells(MONTH, {(1, 1): "43576"})
        shim = os.path.join(self.tmp, "shim.js")
        scenario = os.path.join(self.tmp, "scenario.json")
        with open(shim, "w") as f:
            f.write(CV.shim(MONTH, version="1", sheet=SHEET))
        with open(scenario, "w") as f:
            json.dump({"cells": edited, "delay": 150, "wait": 400}, f)
        done = subprocess.run([NODE, os.path.join(HERE, "canvas_host.mjs"), shim, scenario], capture_output=True, text=True, timeout=60)
        res = json.loads(done.stdout)
        self.assertEqual(res["counts"], [1])
        self.assertEqual(self.visual(res), MONTH)
        self.assertEqual(res["notice"], {"shown": True, "text": NOTICE})

    def test_a_read_that_never_answers_still_draws_the_built_result_once(self):
        shim = os.path.join(self.tmp, "shim.js")
        scenario = os.path.join(self.tmp, "scenario.json")
        with open(shim, "w") as f:
            f.write(CV.shim(MONTH, version="1", sheet=SHEET))
        with open(scenario, "w") as f:
            json.dump({"cells": excel_cells(MONTH), "delay": "never", "wait": 2000}, f)
        done = subprocess.run([NODE, os.path.join(HERE, "canvas_host.mjs"), shim, scenario], capture_output=True, text=True, timeout=60)
        res = json.loads(done.stdout)
        self.assertEqual(res["counts"], [1])
        self.assertEqual(self.visual(res), MONTH)

    def test_a_label_or_title_that_names_a_placeholder_stays_text(self):
        v = copy.deepcopy(MONTH)
        v["xcol"][0]["name"] = "__NOTICE__"
        v["title"] = "Sales __CELLS__ and __RESULT__"
        text = CV.shim(v, version="1")
        self.assertEqual(text.count(CV._embed(CV.result(v))), 1)
        self.assertEqual(text.count("zebra-bi-excel-canvas"), 1)
        res = self.run_shim(v, excel_cells(v))
        self.assertEqual(res["counts"], [1])
        self.assertEqual(self.visual(res)["xcol"][0]["name"], "__NOTICE__")
        self.assertFalse(res["notice"]["shown"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(NODE, "node is not on the PATH")
class DashboardShimInAPage(ShimRunner):
    """The same shim for a dashboard: the binding reads the first visual; the dashboard always draws as built."""

    DASH = CV.dashboard("T", [(MONTH, False), (REGION, False), (SUBCAT, False), (small([5, 6, 7]), True)])

    def items(self, res, i=-1):
        return res["results"][i]["structuredContent"]["dashboard"]["items"]

    def test_the_host_names_the_dashboard_tool_and_draws_the_built_dashboard_once(self):
        res = self.run_shim(self.DASH, excel_cells(MONTH))
        self.assertEqual(res["init"]["hostContext"]["toolInfo"]["tool"]["name"], "render_dashboard")
        self.assertEqual(res["counts"], [1])
        self.assertEqual(res["results"][0]["structuredContent"], self.DASH)
        self.assertFalse(res["notice"]["shown"])
        self.assertEqual(self.run_shim(MONTH, excel_cells(MONTH))["init"]["hostContext"]["toolInfo"]["tool"]["name"], "render_visual")

    def test_an_edit_in_the_bound_cells_keeps_the_built_dashboard_and_shows_the_dashboard_notice(self):
        for edits in ({(1, 1): "43576"}, {(1, 0): "Start"}):
            res = self.run_shim(self.DASH, excel_cells(MONTH), [{"key": "zebra", "cells": excel_cells(MONTH, edits)}])
            self.assertEqual(res["counts"], [1, 1])
            self.assertEqual(res["results"][-1]["structuredContent"], self.DASH)
            self.assertEqual(res["notice"], {"shown": True, "text": CV.notice(self.DASH, SHEET)})

    def test_a_table_as_the_bound_visual_draws_as_built_and_an_edit_shows_the_notice(self):
        dash = CV.dashboard("T", [(REGION, False), (MONTH, False)])
        res = self.run_shim(dash, excel_cells(REGION), [{"key": "zebra", "cells": excel_cells(REGION)},
                                                        {"key": "zebra", "cells": excel_cells(REGION, {(1, 1): "1"})}])
        self.assertEqual(res["counts"], [1, 1, 1])
        self.assertEqual(res["results"][-1]["structuredContent"], dash)
        self.assertTrue(res["notice"]["shown"])

    def test_outside_excel_the_built_dashboard_is_drawn_once(self):
        res = self.run_shim(self.DASH, None)
        self.assertEqual(res["counts"], [1])
        self.assertEqual(res["results"][0]["structuredContent"], self.DASH)
