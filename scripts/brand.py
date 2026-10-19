#!/usr/bin/env python3
"""brand.py: the brand a story is dressed in (page, slides, sheets, tone), never the charts.

  python3 -I brand.py show [<story-or-brand-dir>]        the brand that applies, merged over the default
  python3 -I brand.py check <brand-dir>                    validate .zebra/brand/brand.json; exit 1 on errors
  python3 -I brand.py inspect-template <file.pptx|.potx>   theme colours and fonts, layouts, logo candidates
  python3 -I brand.py logo <file.svg|.png> <brand-dir>     store a logo (an SVG also gets a PNG copy)
  python3 -I brand.py preview <brand-dir> <out-dir>        the sample story as a PDF and a deck in that brand

The default lives in assets/brand/default/brand.json. A company brand is .zebra/brand/brand.json, next to
its logo and template; it is merged over the default, so missing keys fall back. $ZEBRA_BI_BRAND
points at another brand folder (the brand skill's preview uses it). Standard library only.
"""
import base64, colorsys, copy, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from zoffice import ooxml as X  # noqa: E402

DEFAULT = os.path.join(HERE, "..", "assets", "brand", "default", "brand.json")
# Chart colours a brand accent must not imitate: the variance green and red, and the highlight blue
# (the IBCS defaults the Zebra BI renderer draws with).
RESERVED = {"variance green #7ACA00": "#7ACA00", "variance red #FF0000": "#FF0000", "highlight blue #0070C0": "#0070C0"}
# Fonts that ship with Microsoft Office on Windows and Mac, or with macOS; anything else is substituted
# on a machine that does not have it installed.
COMMON_FONTS = {"segoe ui", "segoe ui semibold", "segoe ui light", "segoe ui semilight", "calibri", "calibri light",
                "arial", "arial nova", "helvetica", "helvetica neue", "georgia", "georgia pro", "times new roman",
                "verdana", "tahoma", "trebuchet ms", "aptos", "aptos display", "aptos narrow", "cambria", "candara",
                "consolas", "garamond", "century gothic", "franklin gothic medium", "gill sans", "gill sans mt",
                "palatino linotype", "book antiqua", "corbel", "constantia", "avenir", "avenir next", "sf pro",
                "system-ui"}


FONT_NAME = re.compile(r"^[A-Za-z0-9 ,'\"._-]{1,200}$")  # what a CSS font list or an Office font name may hold


def inside(bdir, name):
    """The absolute path of a file the brand names, or None when it would leave the brand folder."""
    p = os.path.realpath(os.path.join(bdir, name))
    root = os.path.realpath(bdir)
    return p if p == root or p.startswith(root + os.sep) else None


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def find_brand_dir(start):
    """The .zebra/brand folder that applies to a story or workspace path, if one exists."""
    env = os.environ.get("ZEBRA_BI_BRAND")
    if env:
        return os.path.abspath(env)
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.basename(d) == ".zebra":
            cand = os.path.join(d, "brand")
            return cand if os.path.isfile(os.path.join(cand, "brand.json")) else None
        cand = os.path.join(d, ".zebra", "brand")
        if os.path.isfile(os.path.join(cand, "brand.json")):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def load(start=None, brand_dir=None):
    """The resolved brand: default merged with the company brand, file paths made absolute
    (logo['png'], logo['svg'], deck['template']) and '_dir' set to the brand folder or None."""
    brand = json.loads(X.read(DEFAULT))
    bdir = brand_dir or find_brand_dir(start)
    if bdir:
        brand = merge(brand, json.loads(X.read(os.path.join(bdir, "brand.json"))))
    brand["_dir"] = bdir
    brand["_errors"] = []
    logo = brand.setdefault("logo", {})
    for key, name in (("png", logo.get("file")), ("svg", logo.get("svg"))):
        logo[key] = inside(bdir, name) if bdir and name else None
        if bdir and name and not logo[key]:
            brand["_errors"].append("logo %r points outside the brand folder" % name)
    if logo["png"] and logo["png"].lower().endswith(".svg"):  # file given as SVG: the PNG copy sits next to it
        logo["svg"], logo["png"] = logo["png"], os.path.splitext(logo["png"])[0] + ".png"
    deck = brand.setdefault("deck", {})
    if bdir and deck.get("template"):
        name, deck["template"] = deck["template"], inside(bdir, deck["template"])
        if not deck["template"]:
            brand["_errors"].append("deck template %r points outside the brand folder" % name)
    return brand


# ---------- checks ----------

def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _is_hex(v):
    return isinstance(v, str) and len(v) == 7 and v.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in v[1:])


def contrast(a, b):
    def lum(c):
        r, g, bl = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in _rgb(c)]
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def reserved_clash(color):
    """The chart colour an accent would be confused with, or None."""
    h, l, s = colorsys.rgb_to_hls(*_rgb(color))
    if s < 0.35 or l < 0.12 or l > 0.92:
        return None
    for name, ref in RESERVED.items():
        rh = colorsys.rgb_to_hls(*_rgb(ref))[0]
        if min(abs(h - rh), 1 - abs(h - rh)) * 360 < 25:
            return name
    return None


def check(brand):
    """Returns (errors, warnings) for a resolved brand."""
    errors, warnings = list(brand.get("_errors") or []), []
    c = brand.get("colors", {})
    for k in ("ink", "muted", "hairline", "paper", "surface"):
        if not _is_hex(c.get(k)):
            errors.append("colors.%s must be a #RRGGBB colour, got %r" % (k, c.get(k)))
    if c.get("accent") is not None and not _is_hex(c.get("accent")):
        errors.append("colors.accent must be a #RRGGBB colour or null, got %r" % c.get("accent"))
    if not errors:
        if contrast(c["ink"], c["paper"]) < 4.5:
            errors.append("ink %s on paper %s has contrast %.1f:1; text needs at least 4.5:1" % (c["ink"], c["paper"], contrast(c["ink"], c["paper"])))
        if contrast(c["muted"], c["paper"]) < 4.5:
            warnings.append("muted %s on paper %s has contrast %.1f:1; small text reads poorly below 4.5:1"
                            % (c["muted"], c["paper"], contrast(c["muted"], c["paper"])))
        if c.get("accent"):
            clash = reserved_clash(c["accent"])
            if clash:
                warnings.append("accent %s is close to the charts' %s; in IBCS that colour carries meaning, so readers may "
                                "read the accent as a variance. Prefer a neutral or a clearly different hue." % (c["accent"], clash))
    for role in ("headline", "body"):
        spec = (brand.get("fonts") or {}).get(role) or {}
        fam = spec.get("family")
        for key in ("family", "fallback"):
            if spec.get(key) and not FONT_NAME.match(str(spec[key])):
                errors.append("fonts.%s.%s holds characters a font name cannot have" % (role, key))
        if not fam:
            errors.append("fonts.%s.family is missing" % role)
        elif fam.lower() not in COMMON_FONTS:
            warnings.append("font %r (%s) does not ship with Office or macOS; machines without it substitute a font. "
                            "Embed it in the template or keep a common fallback." % (fam, role))
    css = (brand.get("fonts") or {}).get("web_css")
    if css not in (None, ""):
        host = urllib.parse.urlsplit(str(css)).hostname if str(css).startswith("https://") else None
        if not host:
            errors.append("fonts.web_css must be an https:// stylesheet address or null, got %r" % css)
        else:
            warnings.append("fonts.web_css loads a stylesheet from %s each time a PDF is built, plus the font files it "
                            "names: the build then needs the network, and %s sees each request. null keeps the build offline."
                            % (host, host))
    logo = brand.get("logo") or {}
    if logo.get("placement") not in ("cover", "none", None):
        errors.append("logo.placement must be 'cover' or 'none'")
    for key in ("png", "svg"):
        p = logo.get(key)
        if p and not os.path.isfile(p):
            errors.append("logo file %s does not exist" % p)
        elif p and os.path.getsize(p) > 1024 * 1024:
            warnings.append("logo %s is over 1 MB; every deck and page carries it" % os.path.basename(p))
        elif p and key == "svg":
            problem = X.svg_problem(X.read(p, "rb"))
            if problem:
                errors.append("logo %s %s; export a plain SVG or use a PNG" % (os.path.basename(p), problem))
    if logo.get("png") and os.path.isfile(logo["png"]):
        try:
            X.png_size(logo["png"])
        except ValueError as e:
            errors.append(str(e))
    deck = brand.get("deck") or {}
    if deck.get("template"):
        if not os.path.isfile(deck["template"]):
            errors.append("deck template %s does not exist" % deck["template"])
        else:
            from zoffice import template as T
            try:
                base = T.from_template(deck["template"])
                for role in ("title", "content", "closing"):
                    lay = base.layout(role, deck.get("layouts"))
                    if not T.has_title(lay):
                        errors.append("deck layout %r (%s) has no title placeholder, so the %s would be laid over the design. "
                                      "Pick a layout with one (brand.py inspect-template lists them) or add one to the template."
                                      % (lay["name"], role, "key message" if role == "title" else "heading"))
            except (ValueError, KeyError) as e:
                errors.append("deck template: %s" % e)
    lang = (brand.get("voice") or {}).get("language")
    if lang and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", str(lang)):
        warnings.append("voice.language should be a language tag such as en-US or de, got %r; the page says en" % lang)
    if brand.get("credit") in (None, ""):
        warnings.append("no credit line: the stories will not say the charts are Zebra BI's")
    return errors, warnings


# ---------- logo ----------

def store_logo(src, bdir):
    """Copy a logo into the brand folder. An SVG is kept for the PDF page and drawn to a PNG (via
    headless Chrome) for PowerPoint and Excel. Returns the brand.json 'logo' fields to set."""
    os.makedirs(bdir, exist_ok=True)
    ext = os.path.splitext(src)[1].lower()
    if ext == ".png":
        X.png_size(src)
        shutil.copyfile(src, os.path.join(bdir, "logo.png"))
        return {"file": "logo.png", "svg": None}
    if ext != ".svg":
        raise ValueError("a logo must be .svg or .png, got %s" % ext)
    raw = X.read(src, "rb")
    problem = X.svg_problem(raw)
    if problem:
        raise ValueError("the SVG logo %s; export a plain SVG or use a PNG" % problem)
    shutil.copyfile(src, os.path.join(bdir, "logo.svg"))
    from zoffice import cdp, snapshot as SN
    binary = SN.find_chrome()
    if not binary:
        raise RuntimeError("drawing the SVG logo to PNG needs Chrome, Edge or Chromium; provide a PNG instead")
    with tempfile.TemporaryDirectory(prefix="zebra-bi-logo-") as tmp:
        page = os.path.join(tmp, "logo.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write('<!doctype html><html><body style="margin:0;background:transparent">'
                    '<img id="l" src="data:image/svg+xml;base64,%s" style="height:200px;display:block"></body></html>'
                    % base64.b64encode(raw).decode())
        ch = cdp.Chrome(binary, os.path.join(tmp, "profile"))
        try:
            ch.call("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
            ch.call("Page.navigate", url="file://" + page)
            w = 0
            for _ in range(50):
                w = ch.evaluate("document.getElementById('l').complete ? document.getElementById('l').naturalWidth && "
                                "document.getElementById('l').getBoundingClientRect().width : 0") or 0
                if w:
                    break
                time.sleep(0.1)
            if not w:
                raise RuntimeError("the SVG logo did not draw")
            shot = ch.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                           clip={"x": 0, "y": 0, "width": w, "height": 200, "scale": 1})
            with open(os.path.join(bdir, "logo.png"), "wb") as f:
                f.write(base64.b64decode(shot["data"]))
        finally:
            ch.close()
    return {"file": "logo.png", "svg": "logo.svg"}


def preview(bdir, out):
    """Copy the bundled sample story (three cached charts) to out and build it in the brand at bdir."""
    sample = os.path.join(HERE, "..", "assets", "brand", "sample")
    story_dir = os.path.join(out, "sample")
    if os.path.exists(story_dir):
        shutil.rmtree(story_dir)
    shutil.copytree(sample, story_dir)
    files = []
    for script, extra in (("pdf.py", []), ("office.py", ["--pptx"])):
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, script), "build", story_dir] + extra + ["--brand", bdir],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit("preview: %s failed:\n%s" % (script, (r.stderr or r.stdout).strip()))
    for name in ("sample.pdf", "sample.pptx"):
        files.append(os.path.join(story_dir, name))
    return files


def main(argv):
    if not argv or argv[0] not in ("show", "check", "inspect-template", "logo", "preview"):
        sys.exit(__doc__)
    cmd = argv[0]
    if cmd == "show":
        b = load(argv[1] if len(argv) > 1 else None)
        print(json.dumps(b, indent=2, ensure_ascii=False))
    elif cmd == "check":
        bdir = os.path.abspath(argv[1]) if len(argv) > 1 else find_brand_dir(None)
        b = load(brand_dir=bdir) if bdir else load()
        errors, warnings = check(b)
        for w in warnings:
            print("warning: " + w)
        for e in errors:
            print("error: " + e)
        print(json.dumps({"brand": b.get("name"), "dir": bdir, "ok": not errors, "warnings": len(warnings)}))
        sys.exit(1 if errors else 0)
    elif cmd == "inspect-template":
        from zoffice import template as T
        print(json.dumps(T.inspect(argv[1]), indent=2))
    elif cmd == "logo":
        print(json.dumps(store_logo(argv[1], os.path.abspath(argv[2]))))
    elif cmd == "preview":
        print(json.dumps({"files": preview(os.path.abspath(argv[1]), os.path.abspath(argv[2]))}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
