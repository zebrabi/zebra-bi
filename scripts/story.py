#!/usr/bin/env python3
"""story.py: render the visuals of a Zebra BI data story and lay out its page.

  python3 -I story.py renderer [--refresh]   fetch/cache the chart renderer, print its path

Library for pdf.py and office.py. Standard library only. The Zebra BI server
is the `zebra-bi` URL in the plugin's .mcp.json, so the scripts and the
connector always use the same one; $ZEBRA_BI_MCP_URL overrides it for testing.
It must be https://, or http:// on 127.0.0.1 or localhost, and must not
redirect. The renderer
cache lives in $CLAUDE_PLUGIN_DATA (or ~/.cache/zebra-bi), is checked
against its recorded sha256 on every read and refreshes once a day. The
renderer's address is the one render_visual names in tools/list
(_meta.ui.resourceUri), because the server versions it.

story.json (written by the data-story skill) holds the narrative and, per
visual, the exact `render_visual` arguments. `render_visuals` calls
render_visual over HTTP for each visual whose arguments changed since the
last build, stores the result in visuals/<id>.json and prints the server's
own text for each one (it lists every adjustment the server made).
`assemble` returns the branded story page with the renderer and every
chart's result embedded, which pdf.py prints.

When the server cannot be used, a build stops with one line and writes no
file: "Zebra BI is unavailable (<reason>)" or "Zebra BI needs sign-in
(<endpoint>)". A build whose visuals are all cached needs no server.
"""
import base64, datetime as dt, hashlib, html, http.client, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "story-template.html")
MANIFEST = os.path.join(HERE, "..", ".claude-plugin", "plugin.json")
PLUGIN_MCP = os.path.join(HERE, "..", ".mcp.json")


def plugin_endpoint():
    """The zebra-bi server URL from the plugin's .mcp.json, so scripts and connector never drift apart."""
    try:
        with open(PLUGIN_MCP, encoding="utf-8") as f:
            url = json.load(f)["mcpServers"]["zebra-bi"]["url"]
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("Cannot read the zebra-bi server URL from %s" % PLUGIN_MCP)
    if not isinstance(url, str) or not url:
        sys.exit("The zebra-bi server URL in %s is empty" % PLUGIN_MCP)
    return url


ENDPOINT = os.environ.get("ZEBRA_BI_MCP_URL") or plugin_endpoint()
RENDERER_URI = "ui://zebra-bi/visual-v1.html"  # used only when tools/list names no renderer
CACHE = os.path.join(os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.cache/zebra-bi"), "renderer")
MAX_AGE = 24 * 3600
MAX_BYTES = 32 * 1024 * 1024  # one response
MAX_RENDERER = 20 * 1024 * 1024
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*")
# ESC sequences (CSI, OSC, two-byte), then C0/C1 controls, lone surrogates and bidi overrides.
ESCAPES = re.compile(r"(\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?|(\x1b\]|\x9d)[^\x07\x1b\x9c]*(\x07|\x1b\\|\x9c)?|\x1b[ -~]?")
# A JSON-RPC error message that is the auth status itself ("Unauthorized: ..."), not one that merely
# mentions it ("Unauthenticated requests are rate limited").
SIGN_IN = re.compile(r"\s*(unauthori[sz]ed|unauthenticated|authentication required|forbidden)\s*($|[:.;,!(-])", re.I)
CONTROLS = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff\u202a-\u202e\u2066-\u2069]")


class SignIn(Exception):
    pass


class Unavailable(Exception):
    pass


def clean(text, cap=2000):
    """Server text made safe for one terminal line: no escapes, no controls, newlines folded to spaces,
    at most cap characters."""
    text = ESCAPES.sub("", str(text))
    text = CONTROLS.sub("", re.sub("[\\s\x85\u2028\u2029]+", " ", text)).strip()
    return text[:cap] + "..." if len(text) > cap else text


def write_atomic(path, data):
    """Write to a temp file in the same folder, then rename over path, so a crash never leaves half a file."""
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    tmp = os.path.join(os.path.dirname(path), ".%s.%s.tmp" % (os.path.basename(path), os.urandom(4).hex()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def check_endpoint():
    try:
        u = urllib.parse.urlsplit(ENDPOINT)
        ok = u.scheme == "https" and u.hostname or u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")
    except ValueError:
        ok = False
    if not ok:
        sys.exit("ZEBRA_BI_MCP_URL must be an https:// address, or http:// on 127.0.0.1 or localhost; got %r" % ENDPOINT)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        sys.exit("ZEBRA_BI_MCP_URL redirects to %s; set it to the final address" % clean(newurl))


def capped(resp):
    raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Unavailable("%s sent more than %d MB" % (ENDPOINT, MAX_BYTES >> 20))
    return raw


def messages(text):
    """JSON-RPC messages in a body: the whole body as JSON, else the data of each SSE event."""
    try:
        yield json.loads(text)
        return
    except ValueError:
        pass
    for event in re.split(r"\r?\n\r?\n", text):
        data = "\n".join(re.sub(r"^data: ?", "", l) for l in event.splitlines() if l.startswith("data:"))
        try:
            yield json.loads(data)
        except ValueError:
            continue


def rpc(method, params, timeout=90):
    check_endpoint()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as resp:
            ctype, raw = resp.headers.get("Content-Type") or "no content type", capped(resp)
    except urllib.error.HTTPError as e:
        with e:
            if e.code in (401, 403):
                raise SignIn(ENDPOINT)
            detail = ""
            try:
                err = json.loads(e.read(65536).decode("utf-8", "replace")).get("error")
                detail = err.get("message", "") if isinstance(err, dict) else str(err or "")
            except Exception:
                pass
        detail = clean(detail).rstrip(".")
        raise Unavailable("%s answered %d%s" % (ENDPOINT, e.code, (": " + detail) if detail else ""))
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise Unavailable("cannot reach %s: %s" % (ENDPOINT, clean(getattr(e, "reason", e))))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Unavailable("%s sent a response that is not UTF-8" % ENDPOINT)
    for msg in messages(text):
        if not isinstance(msg, dict):
            continue
        if "error" in msg:
            err = msg["error"] if isinstance(msg["error"], dict) else {"message": msg["error"]}
            if err.get("code") in (401, 403) or SIGN_IN.match(str(err.get("message") or "")):
                raise SignIn(ENDPOINT)
            raise Unavailable("%s failed: %s" % (method, clean(err.get("message") or err.get("code"))))
        if "result" in msg:
            return msg["result"]
    raise Unavailable("%s answered %s with no JSON-RPC result" % (ENDPOINT, clean(ctype.split(";")[0])))


def stop(e):
    """The one line a build ends with when the server cannot be used."""
    if isinstance(e, SignIn):
        sys.exit("Zebra BI needs sign-in (%s). The scripts do not sign in, so nothing was built." % ENDPOINT)
    sys.exit("Zebra BI is unavailable (%s). Nothing was built; try again when the server is back." % e)


def cached_renderer(path, meta_path):
    """meta of the cached renderer when it is a regular file whose sha256 matches visual.json, else None."""
    if os.path.islink(path) or os.path.islink(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        with open(path, "rb") as f:
            ok = hashlib.sha256(f.read()).hexdigest() == meta.get("sha256")
    except (OSError, ValueError, AttributeError):
        return None
    return meta if ok else None


def renderer_uri():
    """The renderer address render_visual names in tools/list (_meta.ui.resourceUri), else RENDERER_URI."""
    res = rpc("tools/list", {})
    tools = res.get("tools") if isinstance(res, dict) else None
    for tool in tools if isinstance(tools, list) else []:
        if not (isinstance(tool, dict) and tool.get("name") == "render_visual"):
            continue
        meta = tool.get("_meta") if isinstance(tool.get("_meta"), dict) else {}
        ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
        uri = ui.get("resourceUri") or meta.get("ui/resourceUri")
        if isinstance(uri, str) and re.fullmatch(r"ui://[A-Za-z0-9._~/-]{1,200}", uri):
            return uri
    return RENDERER_URI


def renderer(refresh=False):
    """Return (path, meta) of the cached renderer, fetching it when stale, changed or missing."""
    os.makedirs(CACHE, exist_ok=True)
    path, meta_path = os.path.join(CACHE, "visual.html"), os.path.join(CACHE, "visual.json")
    meta = cached_renderer(path, meta_path)
    if meta and not refresh and time.time() - os.path.getmtime(path) < MAX_AGE:
        return path, meta
    try:
        uri = renderer_uri()
        res = rpc("resources/read", {"uri": uri})
        try:
            text = res["contents"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise Unavailable("%s sent no renderer text" % uri)
        data = text.encode("utf-8", "replace") if isinstance(text, str) else b""
        if not data or len(data) > MAX_RENDERER or not re.search(rb"<html|<!doctype", data[:65536], re.I) \
                or b"ui/initialize" not in data:  # the MCP Apps handshake the page's host answers
            raise Unavailable("%s is not an MCP Apps HTML page under %d MB" % (uri, MAX_RENDERER >> 20))
    except (SignIn, Unavailable) as e:  # offline or server down: fall back to an intact cached copy if there is one
        if meta:
            print("renderer: fetch failed (%s), using the cached copy from %s" % (
                "sign-in needed" if isinstance(e, SignIn) else e, meta.get("fetched_at")), file=sys.stderr)
            return path, meta
        stop(e)
    meta = {"uri": uri, "endpoint": ENDPOINT, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "fetched_at": dt.datetime.now().isoformat(timespec="seconds")}
    write_atomic(path, data)
    write_atomic(meta_path, json.dumps(meta, indent=2))
    return path, meta


def args_hash(args):
    return hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]


def cached_result(out, h):
    """The cached tool result when visuals/<id>.json parses and its args_hash matches, else None (stale)."""
    try:
        with open(out, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(cached, dict) and cached.get("args_hash") == h and isinstance(cached.get("result"), dict):
        return cached["result"]
    return None


def render_visuals(story_dir, story):
    vdir = os.path.join(story_dir, "visuals")
    os.makedirs(vdir, exist_ok=True)
    results, todo = {}, []
    for vid, v in story["visuals"].items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", vid):
            sys.exit("visual id %r: use lowercase letters, digits and dashes" % vid)
        res = cached_result(os.path.join(vdir, vid + ".json"), args_hash(v["args"]))
        if res is not None:
            results[vid] = res
            print("[%s] unchanged, reused" % vid)
        else:
            todo.append(vid)
    if todo:
        check_endpoint()
        print("Sending %d chart(s) to %s: labels and aggregated values" % (len(todo), ENDPOINT))
    for vid in todo:
        args = story["visuals"][vid]["args"]
        try:
            res = rpc("tools/call", {"name": "render_visual", "arguments": args})
        except (SignIn, Unavailable) as e:
            stop(e)
        res = res if isinstance(res, dict) else {}
        content = [c for c in res.get("content") or [] if isinstance(c, dict)] if isinstance(res.get("content"), list) else []
        if res.get("isError"):
            sys.exit("[%s] render_visual refused: %s" % (vid, clean(" ".join(str(c.get("text", "")) for c in content))))
        sc = res.get("structuredContent")
        if not (isinstance(sc, dict) and isinstance(sc.get("visual"), dict) and sc["visual"]):
            stop(Unavailable("render_visual returned no chart (structuredContent.visual) for visual %r" % vid))
        write_atomic(os.path.join(vdir, vid + ".json"), json.dumps({"args_hash": args_hash(args), "result": res}))
        results[vid] = res
        text = " ".join(str(c.get("text", "")) for c in content if c.get("type") == "text")
        print("[%s] %s" % (vid, clean(text)))
    return results


def para(text):
    """Escape, then allow **bold**; blank lines split paragraphs."""
    out = []
    for p in re.split(r"\n\s*\n", text.strip()):
        p = html.escape(p.strip()).replace("\n", " ")
        out.append("<p>" + re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p) + "</p>")
    return "\n".join(out)


def version():
    """This plugin's version for the host shim's hostInfo, from .claude-plugin/plugin.json."""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            v = str(json.load(f).get("version", ""))
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return v if re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", v) else "unknown"


def kicker(story, brand):
    """The label above the key message: the story's own (reporting unit and period), else the brand's, else none."""
    k = str((story or {}).get("kicker") or "").strip() or str(brand.get("kicker") or "").strip()
    return k[:60]


def brand_tokens(brand, story=None):
    """CSS variables, font link, kicker, logo and language for the page, from the brand (kicker: story first)."""
    c, f = brand["colors"], brand["fonts"]
    safe = lambda v: re.sub(r"[^A-Za-z0-9 ,'\"._-]", "", str(v or ""))  # brand.check refuses anything else; belt and braces
    css = ";".join("--%s:%s" % kv for kv in (("ink", c["ink"]), ("muted", c["muted"]), ("hairline", c["hairline"]),
                                             ("paper", c["paper"]), ("surface", c["surface"]), ("mark", c.get("accent") or c["ink"])))
    css += ";--font-headline:'%s',%s;--font-body:'%s',%s" % (
        safe(f["headline"]["family"]).replace("'", "").replace('"', ""), safe(f["headline"].get("fallback")) or "sans-serif",
        safe(f["body"]["family"]).replace("'", "").replace('"', ""), safe(f["body"].get("fallback")) or "sans-serif")
    link = ""
    if f.get("web_css") and str(f["web_css"]).startswith("https://"):
        link = '<link rel="stylesheet" href="%s"/>' % html.escape(f["web_css"])
    logo = (brand.get("logo") or {})
    tag = ""
    if logo.get("placement") == "cover":
        if logo.get("svg") and os.path.isfile(logo["svg"]):
            tag = '<img alt="" src="data:image/svg+xml;base64,%s"/>' % base64.b64encode(open(logo["svg"], "rb").read()).decode()
        elif logo.get("png") and os.path.isfile(logo["png"]):
            tag = '<img alt="" src="data:image/png;base64,%s"/>' % base64.b64encode(open(logo["png"], "rb").read()).decode()
    lang = str((brand.get("voice") or {}).get("language") or "")
    lang = lang if LANG.fullmatch(lang) else "en"
    return {"BRAND_CSS": css, "FONT_LINK": link, "KICKER": html.escape(kicker(story, brand)), "LOGO": tag, "LANG": lang}


def assemble(story, results, meta, renderer_path, brand):
    """The story page as one HTML string, renderer and chart results embedded."""
    e = html.escape
    kpis = "\n".join('<div class="kpi"><b>%s</b><span>%s</span></div>' % (e(k["value"]), e(k["label"])) for k in story.get("kpis", []))
    sections = []
    for s in story["sections"]:
        frame = ""
        if s.get("visual"):
            if s["visual"] not in results:
                sys.exit("section %r names visual %r, which is not in visuals" % (s.get("heading"), s["visual"]))
            frame = '<iframe data-v="%s" title="%s"></iframe>' % (e(s["visual"]), e(s.get("heading", s["visual"])))
        sections.append("<section>\n<h2>%s</h2>\n%s\n%s\n</section>" % (e(s.get("heading", "")), para(s.get("body", "")), frame))
    nxt = ""
    if story.get("next"):
        nxt = '<section class="next"><h2>%s</h2><ul>%s</ul></section>' % (
            e(story.get("next_heading", "Where to look next")),
            "".join("<li>%s</li>" % re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(n)) for n in story["next"]))
    src = story.get("source", {})
    footer = "Source: %s. Built %s." % (e(src.get("label", src.get("name", "unknown"))), dt.date.today().isoformat())
    if brand.get("credit"):
        footer += " &middot; " + e(brand["credit"])
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    # Data and the renderer are embedded as JSON in inert <script type="application/json">
    # blocks, so the page is one file Chrome opens from disk.
    # Every "<" is written as <, so no payload can close its script element or
    # switch the parser into a comment state ("<!--" followed by "<script").
    embed = lambda obj: json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
    with open(renderer_path, encoding="utf-8") as f:
        renderer_html = f.read()
    fills = {"TITLE": e(story["title"]), "SUBTITLE": e(story.get("subtitle", "")), "KPIS": kpis,
             "SECTIONS": "\n".join(sections), "NEXT": nxt, "FOOTER": footer, "VERSION": version(),
             "RENDERER_SHA": meta["sha256"], "VISUALS_JSON": embed(results), "RENDERER_JSON": embed(renderer_html)}
    fills.update(brand_tokens(brand, story))
    # One pass over the template: a value that holds "{{...}}" is never expanded again.
    page = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: fills[m.group(1)], page)
    return page


def main(argv):
    if not argv or argv[0] != "renderer":
        sys.exit(__doc__)
    path, meta = renderer("--refresh" in argv)
    print(json.dumps({"path": path, **meta}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
