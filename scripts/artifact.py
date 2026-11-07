#!/usr/bin/env python3
"""artifact.py: lay out a Zebra BI data story as a Claude artifact (a plain page) with its Zebra BI charts.

  python3 -I artifact.py build <story-dir> [--live] [--brand <dir>]
  python3 -I artifact.py record <story-dir> <artifact-url>

`build` reuses story.py: every visual in story.json is rendered (or reused from visuals/<id>.json),
then each chart is drawn by the Zebra BI renderer in headless Chrome at the width it is shown at (the
page's column, or its cell when a row puts cells side by side) and captured at 2x (zoffice.snapshot).
<story-dir> may also be a report builder folder (report.zbreport): its rows become the page's rows. It writes <story-dir>/artifact/: index.html (the page, a fragment
the artifact host wraps in its own document) and charts/<id>.png. With --live it also writes
zebra-renderer.txt, the renderer the page loads to draw each chart live in place of its picture; the
pictures stay when it cannot. It prints JSON: the page to publish, the files to publish with it
({published path: local path}), the paths an earlier publish has that this one drops (to publish as
null), the page title, a one-sentence description and the artifact's URL when one was recorded.

`record` keeps the URL the page was published at, and what was published, in <story-dir>/artifact.json,
so the next build of the story updates the same artifact.

Without a browser, or when a chart does not draw, build stops with one line and writes no page.
Standard library only.
"""
import datetime as dt, html, json, os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brand as B  # noqa: E402
import story as S  # noqa: E402
from zoffice import ooxml as X, snapshot as SN  # noqa: E402

TEMPLATE = os.path.join(HERE, "..", "assets", "artifact-template.html")
WIDTH = 760  # the page column (47.5rem), which the charts are drawn at
GAP = 20  # between cells side by side in a row (1.25rem)
RENDERER_FILE = "zebra-renderer.txt"
FONT_HOST = "https://fonts.googleapis.com/"  # the only stylesheet host an artifact page may load

LIVE = """<script id="zebra-visuals" type="application/json">%(visuals)s</script>
<script>
(function () {
  // A minimal MCP Apps host, as in story-template.html: each figure[data-v] gets the Zebra BI renderer in
  // a sandboxed frame and its tool result. The renderer is the published file next to the page, checked
  // against the sha256 it was built with. Until a chart has drawn, and whenever the renderer cannot load,
  // the figure shows the chart's picture.
  var visuals = JSON.parse(document.getElementById('zebra-visuals').textContent);
  var plates = Array.prototype.slice.call(document.querySelectorAll('figure[data-v]'));
  var state = new Map();
  function hex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (b) { return ('0' + b.toString(16)).slice(-2); }).join(''); }
  fetch('%(file)s').then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); }).then(function (text) {
    return crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)).then(function (d) {
      if (hex(d) !== '%(sha)s') throw new Error('renderer does not match the build');
      start(URL.createObjectURL(new Blob([text], { type: 'text/html' })));
    });
  }).catch(function () { /* the pictures stay */ });
  function frameOf(plate) { return plate.querySelector('iframe'); }
  function load(plate, url) {
    var f = frameOf(plate);
    state.set(f, { plate: plate, sent: false, width: plate.clientWidth, url: url });
    plate.removeAttribute('data-live');
    f.setAttribute('sandbox', 'allow-scripts allow-same-origin');
    var src = url + '#' + plate.dataset.v + '-' + Date.now();
    if (!f.getAttribute('src')) { f.src = src; return; }
    f.addEventListener('load', function next() { f.removeEventListener('load', next); f.src = src; });
    f.src = 'about:blank';
  }
  function start(url) {
    plates.forEach(function (p) { load(p, url); });
    var t;
    window.addEventListener('resize', function () {
      clearTimeout(t);
      t = setTimeout(function () {
        plates.forEach(function (p) { var st = state.get(frameOf(p)); if (Math.abs(p.clientWidth - st.width) > 40) load(p, url); });
      }, 300);
    });
  }
  function frameFor(src) { var hit = null; state.forEach(function (st, f) { if (f.contentWindow === src) hit = f; }); return hit; }
  function post(f, msg) { f.contentWindow.postMessage(msg, '*'); }
  function sendData(f) {
    var st = state.get(f); if (st.sent) return; st.sent = true;
    post(f, { jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: {} });
    post(f, { jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: visuals[st.plate.dataset.v] });
  }
  window.addEventListener('message', function (ev) {
    var f = frameFor(ev.source); if (!f) return;
    var m = ev.data; if (!m || m.jsonrpc !== '2.0') return;
    if (m.id != null && m.method) {
      if (m.method === 'ui/initialize') {
        var w = state.get(f).plate.clientWidth;
        post(f, { jsonrpc: '2.0', id: m.id, result: {
          protocolVersion: (m.params && m.params.protocolVersion) || '2025-11-21',
          hostInfo: { name: 'zebra-bi', version: '%(version)s' },
          hostCapabilities: {},
          hostContext: {
            toolInfo: { id: 1, tool: { name: 'render_visual', inputSchema: { type: 'object' } } },
            theme: 'light', displayMode: 'inline', maxWidth: w, viewport: { width: w }
          }
        }});
        setTimeout(function () { sendData(f); }, 50);
      } else {
        post(f, { jsonrpc: '2.0', id: m.id, result: {} });
      }
      return;
    }
    if (m.method === 'notifications/initialized') sendData(f);
    else if (m.method === 'ui/notifications/size-changed' && m.params && m.params.height) {
      var st = state.get(f), h = Math.ceil(m.params.height);
      // The renderer pads short tables to a fixed height. The picture was cropped to the chart, so at about
      // the width it was drawn at, the frame takes the picture's height.
      var pw = Number(st.plate.dataset.w), ph = Number(st.plate.dataset.h);
      if (ph && Math.abs(st.plate.clientWidth - pw) <= 40) h = Math.min(h, ph);
      f.style.height = h + 'px';
      try { f.contentDocument.documentElement.style.overflow = 'hidden'; } catch (e) { /* not same-origin: leave it */ }
      if (st.sent) st.plate.setAttribute('data-live', 'ready');
    }
  });
})();
</script>"""


def png_size(path):
    """(width, height) of a PNG from its IHDR, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def page_title(story):
    """The artifact's name: the kicker (reporting unit and period) without its separators, else the key message."""
    k = re.sub(r"\s*[·|/–—-]\s*", " ", str(story.get("kicker") or "")).strip()
    name = k or str(story.get("title") or "Data story").strip()
    return name[:60]


def font_link(brand):
    """The brand's web font stylesheet when an artifact may load it (Google Fonts only), else nothing."""
    url = str((brand.get("fonts") or {}).get("web_css") or "")
    return '<link rel="stylesheet" href="%s">' % html.escape(url) if url.startswith(FONT_HOST) else ""


def picture_widths(story):
    """{visual id: the CSS width its picture is drawn at}: the column, or its cell when it sits in a row."""
    widths = {s["visual"]: WIDTH for s in story.get("sections", []) if s.get("visual")}
    for row in S.rows(story):
        n = len(row["cells"])
        for c in row["cells"]:
            if c.get("visual"):
                widths.setdefault(c["visual"], (WIDTH - GAP * (n - 1)) // n)
    return widths


def assemble_page(story, results, brand, sizes, live=False, renderer_sha=None):
    """The artifact page as one HTML fragment. sizes: {visual id: (css width, css height)} of each picture."""
    e = html.escape
    kpis = "".join('<div class="zs-kpi"><b>%s</b><span>%s</span></div>' % (e(k["value"]), e(k["label"])) for k in story.get("kpis", []))

    def figure(vid, lead):
        if vid not in results:
            sys.exit("the story names visual %r, which is not in visuals" % vid)
        args = (story.get("visuals", {}).get(vid) or {}).get("args") or {}
        alt = "%sZebra BI chart: %s%s." % (lead, args.get("title", vid), (", " + args["subtitle"]) if args.get("subtitle") else "")
        w, h = sizes.get(vid) or (WIDTH, 400)
        frame = '<iframe title="%s" scrolling="no"></iframe>' % e(args.get("title", vid)) if live else ""
        return ('<figure class="zs-plate" data-v="%s" data-w="%d" data-h="%d"><img src="charts/%s.png" width="%d" height="%d" alt="%s">%s</figure>'
                % (e(vid), w, h, e(vid), w, h, e(alt), frame))

    sections = []
    for s in story["sections"]:
        fig = figure(s["visual"], s.get("heading", "") + ". ") if s.get("visual") else ""
        sections.append('<section class="zs-section"><div class="zs-text"><h2>%s</h2>\n%s</div>%s</section>'
                        % (e(s.get("heading", "")), S.para(s.get("body", "")), fig))
    for row in S.rows(story):
        cells = [('<div class="zs-cell">%s<div class="zs-text zs-note">%s</div></div>' % (figure(c["visual"], ""), S.para(c["note"]))
                  if c.get("note") else figure(c["visual"], "")) if c.get("visual")
                 else '<div class="zs-text">%s</div>' % S.para(c["text"]) for c in row["cells"]]
        sections.append('<section class="zs-section zs-row zs-cols-%d">%s</section>' % (len(cells), "".join(cells)))
    nxt = ""
    if story.get("next"):
        nxt = '<section class="zs-section zs-next"><h2>%s</h2><ul>%s</ul></section>' % (
            e(story.get("next_heading", "Where to look next")),
            "".join("<li>%s</li>" % re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(n)) for n in story["next"]))
    tokens = S.brand_tokens(brand, story)
    embed = lambda obj: json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
    live_block = ""
    if live:
        used = {vid: results[vid] for vid in S.story_visuals(story)}
        live_block = LIVE % {"visuals": embed(used), "file": RENDERER_FILE, "sha": renderer_sha, "version": S.version()}
    fills = {"PAGE_TITLE": e(page_title(story)), "FONT_LINK": font_link(brand), "BRAND_CSS": tokens["BRAND_CSS"],
             "KICKER": tokens["KICKER"], "LOGO": tokens["LOGO"], "TITLE": e(story["title"]), "SUBTITLE": e(story.get("subtitle", "")),
             "KPIS": kpis, "SECTIONS": "\n".join(sections), "NEXT": nxt, "FOOTER": S.footer(story, brand), "LIVE": live_block}
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: fills[m.group(1)], page)


def recorded(story_dir):
    """What the last `record` kept: {url, files}, or {}."""
    try:
        with open(os.path.join(story_dir, "artifact.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def build(story_dir, live=False, brand_dir=None):
    brand = B.load(story_dir, brand_dir)
    errors, warnings = B.check(brand)
    for w in warnings:
        print("brand warning: " + w, file=sys.stderr)
    if errors:
        sys.exit("brand %r is not valid: %s" % (brand.get("name"), "; ".join(errors)))
    if not SN.find_chrome():
        sys.exit("The artifact needs Google Chrome, Microsoft Edge or Chromium to draw the charts, and none was found "
                 "(set ZEBRA_BI_CHROME to its path). Nothing was written.")
    story = S.load(story_dir)
    results = S.render_visuals(story_dir, story)
    renderer_path, meta = S.renderer()
    used = S.story_visuals(story)
    missing = [v for v in used if v not in results]
    if missing:
        sys.exit("the story names visuals that are not in its visuals: %s" % ", ".join(missing))
    widths = picture_widths(story)
    out = os.path.join(story_dir, "artifact")
    charts = os.path.join(out, "charts")
    try:
        X.build_dir(out)
        snaps = {}
        for width in sorted(set(widths.values()), reverse=True):  # each chart drawn at the width it is shown at
            got, _ = SN.snapshot_all({v: results[v] for v in used if widths[v] == width}, renderer_path, charts,
                                     width=width, min_aspect=0)
            snaps.update(got)
    except ValueError as err:
        sys.exit("%s. Nothing was written." % err)
    except RuntimeError as err:
        sys.exit("Chart did not draw in the browser (%s). Nothing was written; build once more." % err)
    files, sizes = {}, {}
    for vid in used:
        files["charts/%s.png" % vid] = os.path.abspath(snaps[vid])
        px = png_size(snaps[vid])
        sizes[vid] = (px[0] // 2, px[1] // 2) if px else (widths[vid], 400)
    if live:
        dst = X.write_atomic(os.path.join(out, RENDERER_FILE), X.read(renderer_path, "rb"))
        files[RENDERER_FILE] = os.path.abspath(dst)
    page = assemble_page(story, results, brand, sizes, live, meta["sha256"])
    page_path = os.path.join(out, "index.html")
    S.write_atomic(page_path, page)
    before = recorded(story_dir)
    remove = sorted(p for p in before.get("files", []) if isinstance(p, str) and p not in files)
    build_info = {"page": os.path.abspath(page_path), "files": files, "remove": remove, "title": page_title(story),
                  "description": str(story["title"])[:1000], "url": before.get("url"), "live": live,
                  "brand": brand.get("name"), "renderer_sha256": meta["sha256"][:12]}
    S.write_atomic(os.path.join(out, "build.json"), json.dumps(build_info, indent=2))
    print(json.dumps(build_info, indent=2))
    return build_info


def record(story_dir, url):
    """Keep the artifact URL and what was published, so the next build updates the same artifact."""
    if not re.fullmatch(r"https://claude\.ai/(code/)?artifact/[A-Za-z0-9_-]+", url or ""):
        sys.exit("not a claude.ai artifact URL: %r" % url)
    try:
        with open(os.path.join(story_dir, "artifact", "build.json"), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        sys.exit("no build to record; run `artifact.py build` first")
    data = {"url": url, "files": sorted(info.get("files", {})), "live": bool(info.get("live")),
            "published_at": dt.datetime.now().isoformat(timespec="seconds")}
    S.write_atomic(os.path.join(story_dir, "artifact.json"), json.dumps(data, indent=2))
    print("Recorded %s for %s." % (url, os.path.basename(os.path.normpath(story_dir))))


def main(argv):
    if len(argv) >= 3 and argv[0] == "record":
        return record(os.path.abspath(argv[1]), argv[2])
    if len(argv) < 2 or argv[0] != "build":
        sys.exit(__doc__)
    bdir = os.path.abspath(argv[argv.index("--brand") + 1]) if "--brand" in argv else None
    build(os.path.abspath(argv[1]), "--live" in argv, bdir)


if __name__ == "__main__":
    main(sys.argv[1:])
