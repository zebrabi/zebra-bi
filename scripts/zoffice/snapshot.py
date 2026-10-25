"""Snapshot PNGs of each visual, drawn by the Zebra BI renderer in headless Chrome.

Each visual gets a one-chart page that reuses the MCP Apps host shim from assets/story-template.html.
One headless Chrome, driven over the DevTools protocol, opens each page, waits until the renderer has
reported its height (size-changed) and the height holds still, then captures exactly that area at 2x.
The PNG is what Office shows where the add-in is not installed.
"""
import base64, json, os, re, shutil, struct, tempfile, time, zlib

from . import cdp
from . import ooxml as X

TEMPLATE = os.path.join(X.ASSETS, "..", "story-template.html")
PLACEHOLDER = os.path.join(X.ASSETS, "snapshot-placeholder.png")
WIDTH = 1000
# The add-in lays itself out in the frame Office gives it, at larger type than the chat renderer.
# A short table's own aspect would make that frame too low, so snapshots (and with them the frames,
# which keep the snapshot's aspect so the fallback image is never stretched) are at least this tall.
MIN_ASPECT = 0.45
CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome():
    env = os.environ.get("ZEBRA_BI_CHROME")
    if env:
        return env if os.path.exists(env) else None
    for c in CANDIDATES:
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def page(vid, result, renderer_html, width=WIDTH):
    import story  # scripts/, which holds zoffice, is on the path wherever zoffice imports
    shim = re.search(r"<script>\s*\(function \(\) \{.*?</script>", X.read(TEMPLATE), re.S).group(0)
    shim = shim.replace("{{VERSION}}", story.version())
    embed = lambda o: json.dumps(o, ensure_ascii=False).replace("<", "\\u003c")
    # Record the height the renderer reports, so the first Chrome pass can read it from the DOM.
    probe = ("<script>window.addEventListener('message',function(e){var m=e.data;"
             "if(m&&m.method==='ui/notifications/size-changed'&&m.params&&m.params.height)"
             "document.body.setAttribute('data-h',Math.ceil(m.params.height));});</script>")
    return ('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}'
            'iframe{border:0;display:block;width:%dpx;height:420px}</style></head><body>%s'
            '<iframe data-v="%s"></iframe>'
            '<script id="zebra-visuals" type="application/json">%s</script>'
            '<script id="zebra-renderer" type="application/json">%s</script>%s</body></html>'
            % (width, probe, vid, embed({vid: result}), embed(renderer_html), shim))


def _wait_height(ch, timeout=30):
    """The reported height, once it has not changed for a second (the charts animate in)."""
    deadline, last, since = time.time() + timeout, None, None
    while time.time() < deadline:
        h = ch.evaluate("document.body.getAttribute('data-h')")
        if h and h == last:
            if time.time() - since >= 1.0:
                return int(h)
        else:
            last, since = h, time.time()
        time.sleep(0.2)
    return None


def _content_bottom(png, scale):
    """Last non-white row of a small 8-bit RGB(A) PNG, in CSS pixels. The renderer reports a minimum height,
    so short tables would otherwise carry a band of white below them."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, w = 8, b"", 0
    while pos < len(png):
        n, typ = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype not in (2, 6):
                return None
            bpp = 4 if ctype == 6 else 3
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride = zlib.decompress(idat), w * bpp
    prev, last, i = bytearray(stride), 0, 0
    for y in range(h):
        f, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b, c = prev[x], (prev[x - bpp] if x >= bpp else 0)
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if any(line[x] < 235 for x in range(stride) if bpp == 3 or x % 4 != 3):
            last = y
        prev = line
    return int((last + 1) / scale)


def snapshot_all(results, renderer_path, out_dir, width=WIDTH, min_aspect=MIN_ASPECT):
    """results: {id: render_visual result}. Returns ({id: png path}, chrome path or None).

    width is the CSS width each chart is drawn at (captured at 2x); min_aspect the lowest height/width kept."""
    os.makedirs(out_dir, exist_ok=True)
    binary = find_chrome()
    out = {}
    if not binary:
        for vid in results:
            dst = os.path.join(out_dir, vid + ".png")
            shutil.copyfile(PLACEHOLDER, dst)
            out[vid] = dst
        return out, None
    renderer_html = X.read(renderer_path)
    with tempfile.TemporaryDirectory(prefix="zebra-bi-snap-") as tmp:
        ch = cdp.Chrome(binary, os.path.join(tmp, "profile"))
        try:
            ch.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=2, mobile=False)
            for vid, res in results.items():
                html_path = os.path.join(tmp, vid + ".html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(page(vid, res, renderer_html, width))
                ch.call("Page.navigate", url="file://" + html_path)
                h = _wait_height(ch)
                if not h:
                    raise RuntimeError("snapshot of %r: the renderer never reported a size; the chart did not draw" % vid)
                h = min(h, 2400)
                small = ch.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                                clip={"x": 0, "y": 0, "width": width, "height": h, "scale": 0.1})
                bottom = _content_bottom(base64.b64decode(small["data"]), 0.1 * 2)  # deviceScaleFactor 2
                if bottom:
                    h = min(h, bottom + 16)
                h = max(h, int(width * min_aspect))
                shot = ch.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                               clip={"x": 0, "y": 0, "width": width, "height": h, "scale": 1})
                dst = os.path.join(out_dir, vid + ".png")
                with open(dst, "wb") as f:
                    f.write(base64.b64decode(shot["data"]))
                out[vid] = dst
        finally:
            ch.close()
    return out, binary
