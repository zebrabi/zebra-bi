"""Tests for story.py (and the page pdf.py prints) against a fake Zebra BI server. Run: python3 -I -m unittest discover <plugin>/scripts/tests

The fake server listens on 127.0.0.1 only and picks its behaviour from the URL path
(ZEBRA_BI_MCP_URL=http://127.0.0.1:<port>/<mode>): well-formed answers, malformed ones,
HTTP errors, a redirect and an oversize body.
"""
import contextlib, hashlib, http.server, io, json, os, re, shutil, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
import story as S  # noqa: E402

RENDERER = "<!doctype html><html><body>fake renderer<script>/* answers ui/initialize */</script></body></html>"
ADVERTISED = "ui://zebra-bi/visual-v9.html"  # what the fake server's tools/list names; not the built-in fallback
HITS = []


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def result(text):
    return {"content": [{"type": "text", "text": text}], "structuredContent": {"visual": {"title": "t"}}}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", headers=()):
        body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send(405, '{"error":{"message":"Method Not Allowed"}}')

    def do_POST(self):
        mode = self.path.strip("/")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        method = json.loads(body).get("method")
        HITS.append((mode, method))
        ok = lambda res: json.dumps({"jsonrpc": "2.0", "id": 1, "result": res})
        if mode == "e401":
            return self.send(401, '{"error":"unauthorized"}')
        if mode == "e405":
            return self.send(405, '{"error":{"message":"Method Not Allowed\\n[v2] forged\\u001b[31m red"}}')
        if mode == "redirect":
            return self.send(302, "", headers=[("Location", "/ok")])
        if mode == "html200":
            return self.send(200, "<html><body>Sign in to the network</body></html>", "text/html")
        if mode == "badutf8":
            return self.send(200, b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"\xff\xfe"}]}}')
        if mode == "rpcauth":
            return self.send(200, json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "Unauthorized: sign in first"}}))
        if mode == "loginnum":
            return self.send(200, json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "login_count is not numeric"}}))
        if mode == "ratelimit":
            return self.send(200, json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Unauthenticated requests are rate limited"}}))
        if mode == "rpcerror":
            return self.send(200, json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "internal\nerror"}}))
        if method == "tools/list":  # the renderer address is read from render_visual's _meta, never assumed
            meta = {} if mode == "nometa" else {"ui": {"resourceUri": ADVERTISED}}
            return self.send(200, ok({"tools": [{"name": "other_tool"}, {"name": "render_visual", "_meta": meta}]}))
        if method == "resources/read":
            uri = json.loads(body).get("params", {}).get("uri")
            if uri != (S.RENDERER_URI if mode == "nometa" else ADVERTISED):
                return self.send(200, json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32002, "message": "Resource not found: %s" % uri}}))
            text = {"junk": "Service temporarily unavailable", "nohandshake": "<!doctype html><html>no app</html>"}.get(mode, RENDERER)
            return self.send(200, ok({"contents": [{"uri": uri, "mimeType": "text/html", "text": text}]}))
        if mode == "ansi":
            return self.send(200, ok(result("ok\n[v2] the server REMOVED this chart\n\x1b[31mRED\x1b[0m\x9b2J done")))
        if mode == "placeholder":
            return self.send(200, ok(result("{{RENDERER_JSON}} {{TITLE}}")))
        if mode == "pretty":
            return self.send(200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result("pretty")}, indent=2))
        if mode == "sse":
            j = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result("sse")}, indent=1)
            return self.send(200, "event: message\n" + "".join("data: " + l + "\n" for l in j.splitlines()) + "\n", "text/event-stream")
        if mode == "surrogate":
            return self.send(200, '{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"lone \\udc00 surrogate"}],"structuredContent":{"visual":{"title":"t"}}}}')
        if mode == "empty":
            return self.send(200, ok({}))
        if mode == "textonly":
            return self.send(200, ok({"content": [{"type": "text", "text": "I could not draw that."}]}))
        if mode == "nullvisual":
            return self.send(200, ok({"content": [{"type": "text", "text": "t"}], "structuredContent": {"visual": None}}))
        if mode == "long":
            return self.send(200, ok(result("y" * 5000)))
        if mode == "big":
            return self.send(200, ok(result("x" * 4000)))
        return self.send(200, ok(result("The chart is fine.")))


def setUpModule():
    global SERVER, BASE
    SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = "http://127.0.0.1:%d/" % SERVER.server_address[1]


def tearDownModule():
    SERVER.shutdown()
    SERVER.server_close()


STORY = {"title": "{{FOOTER}} grew", "source": {"label": "test"}, "visuals": {"v1": {"args": {"a": 1}}},
         "sections": [{"heading": "H", "body": "B", "visual": "v1"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = S.ENDPOINT, S.CACHE, getattr(S, "MAX_BYTES", None)
        S.CACHE = os.path.join(self.tmp, "cache", "renderer")
        self.story_dir = os.path.join(self.tmp, "story")
        os.makedirs(self.story_dir)
        with open(os.path.join(self.story_dir, "story.json"), "w") as f:
            json.dump(STORY, f)
        del HITS[:]

    def tearDown(self):
        S.ENDPOINT, S.CACHE = self.saved[:2]
        if self.saved[2] is not None:
            S.MAX_BYTES = self.saved[2]
        shutil.rmtree(self.tmp)

    def mode(self, m):
        S.ENDPOINT = BASE + m

    def render(self):
        """(stdout, results) of render_visuals on the one-visual story."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            res = S.render_visuals(self.story_dir, json.loads(json.dumps(STORY)))
        return out.getvalue(), res

    def exit_message(self, fn, *a):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            fn(*a)
        return str(cm.exception.code)

    def build(self):
        """stdout of the steps pdf.py runs before printing (render_visuals, renderer, assemble); the page lands in self.page."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            story = json.loads(json.dumps(STORY))
            results = S.render_visuals(self.story_dir, story)
            path, meta = S.renderer()
            self.page = S.assemble(story, results, meta, path, B.load(start=self.tmp))
        return out.getvalue()


class ServerText(Base):  # item 7
    def test_server_text_is_one_clean_line(self):
        self.mode("ansi")
        out, _ = self.render()
        line = [l for l in out.splitlines() if l.startswith("[v1]")]
        self.assertEqual(len(line), 1, out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x9b", out)
        self.assertFalse(any(l.startswith("[v2]") for l in out.splitlines()), out)
        self.assertIn("REMOVED this chart RED done", line[0])

    def test_long_server_text_is_capped(self):
        self.mode("long")
        out, _ = self.render()
        line = [l for l in out.splitlines() if l.startswith("[v1]")][0]
        self.assertEqual(line, "[v1] " + "y" * 2000 + "...")

    def test_http_error_detail_is_clean(self):
        self.mode("e405")
        msg = self.exit_message(self.render)
        self.assertTrue(msg.startswith("Zebra BI is unavailable ("), msg)
        self.assertIn("405", msg)
        self.assertNotIn("\x1b", msg)
        self.assertNotIn("\n", msg)


class Placeholders(Base):  # item 8
    def test_values_are_never_expanded_again(self):
        renderer_path = os.path.join(self.tmp, "visual.html")
        with open(renderer_path, "w") as f:
            f.write(RENDERER)
        results = {"v1": result("{{RENDERER_JSON}} {{TITLE}}")}
        brand = B.load(start=self.tmp)
        page = S.assemble(STORY, results, {"sha256": "ab" * 32}, renderer_path, brand)
        visuals = re.search(r'<script id="zebra-visuals" type="application/json">(.*?)</script>', page, re.S).group(1)
        self.assertEqual(json.loads(visuals), results)
        self.assertIn("<title>{{FOOTER}} grew</title>", page)
        self.assertEqual(page.count("fake renderer"), 1)


class Responses(Base):  # item 9
    def test_pretty_json_and_multiline_sse_parse(self):
        for mode, text in (("pretty", "pretty"), ("sse", "sse")):
            with self.subTest(mode=mode):
                shutil.rmtree(os.path.join(self.story_dir, "visuals"), ignore_errors=True)
                self.mode(mode)
                _, res = self.render()
                self.assertEqual(res["v1"]["content"][0]["text"], text)

    def test_malformed_answers_end_in_one_line(self):
        for mode, start in (("html200", "Zebra BI is unavailable ("), ("badutf8", "Zebra BI is unavailable ("),
                            ("rpcerror", "Zebra BI is unavailable ("), ("rpcauth", "Zebra BI needs sign-in (")):
            with self.subTest(mode=mode):
                self.mode(mode)
                msg = self.exit_message(self.render)
                self.assertTrue(msg.startswith(start), msg)
                self.assertNotIn("\n", msg.split(".")[0])
        self.mode("html200")
        self.assertIn("text/html", self.exit_message(self.render))

    def test_errors_that_only_mention_auth_are_not_sign_in(self):
        for mode in ("loginnum", "ratelimit"):
            with self.subTest(mode=mode):
                self.mode(mode)
                msg = self.exit_message(self.render)
                self.assertTrue(msg.startswith("Zebra BI is unavailable ("), msg)

    def test_sign_in_and_unavailable_write_no_story(self):
        for mode, start in (("e401", "Zebra BI needs sign-in (%s)" % (BASE + "e401")), ("e405", "Zebra BI is unavailable (")):
            with self.subTest(mode=mode):
                self.mode(mode)
                self.page = None
                msg = self.exit_message(self.build)
                self.assertTrue(msg.startswith(start), msg)
                self.assertIsNone(self.page)
                self.assertEqual(os.listdir(os.path.join(self.story_dir, "visuals")), [])


class Disclosure(Base):  # contract 2
    def test_disclosure_before_sending_and_not_when_cached(self):
        self.mode("ok")
        out = self.build()
        self.assertEqual(out.splitlines()[0], "Sending 1 chart(s) to %s: labels and aggregated values" % (BASE + "ok"))
        self.mode("e405")  # every visual and the renderer cached: no network call, no disclosure
        out = self.build()
        self.assertNotIn("Sending", out)
        self.assertIn("<iframe data-v=\"v1\"", self.page)


    def test_render_visuals_alone_discloses_as_office_py_calls_it(self):
        self.mode("ok")
        out, _ = self.render()
        self.assertEqual(out.splitlines(), ["Sending 1 chart(s) to %s: labels and aggregated values" % (BASE + "ok"),
                                            "[v1] The chart is fine."])


class Cache(Base):  # item 10
    def test_lone_surrogate_cache_is_complete_ascii_json(self):
        self.mode("surrogate")
        self.render()
        path = os.path.join(self.story_dir, "visuals", "v1.json")
        raw = read(path, "rb")
        raw.decode("ascii")
        self.assertEqual(json.loads(raw)["result"]["content"][0]["text"], "lone \udc00 surrogate")
        self.assertEqual(os.listdir(os.path.dirname(path)), ["v1.json"])

    def test_unparseable_cache_is_rendered_again(self):
        self.mode("ok")
        self.render()
        path = os.path.join(self.story_dir, "visuals", "v1.json")
        with open(path, "w") as f:
            f.write('{"args_hash": "x", "result": {"cont')
        del HITS[:]
        _, res = self.render()
        self.assertEqual(len(HITS), 1)
        self.assertEqual(json.loads(read(path))["result"], res["v1"])


class Empty(Base):  # item 11
    def test_empty_result_is_refused(self):
        self.mode("empty")
        msg = self.exit_message(self.render)
        self.assertIn("'v1'", msg)
        self.assertFalse(os.path.exists(os.path.join(self.story_dir, "visuals", "v1.json")))

    def test_result_without_a_visual_is_refused(self):
        for mode in ("textonly", "nullvisual"):
            with self.subTest(mode=mode):
                self.mode(mode)
                msg = self.exit_message(self.render)
                self.assertTrue(msg.startswith("Zebra BI is unavailable ("), msg)
                self.assertIn("'v1'", msg)
                self.assertFalse(os.path.exists(os.path.join(self.story_dir, "visuals", "v1.json")))

    def test_non_html_renderer_is_refused(self):
        for mode in ("junk", "nohandshake"):
            with self.subTest(mode=mode):
                self.mode(mode)
                msg = self.exit_message(S.renderer)
                self.assertTrue(msg.startswith("Zebra BI is unavailable ("), msg)


class Renderer(Base):  # item 12
    def fetch(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return S.renderer()

    def test_changed_cache_is_fetched_again(self):
        self.mode("ok")
        path, meta = self.fetch()
        with open(path, "a") as f:
            f.write("<script>tampered</script>")
        del HITS[:]
        path, meta2 = self.fetch()
        self.assertEqual([h for h in HITS if h[1] == "resources/read"], [("ok", "resources/read")])
        self.assertEqual(read(path), RENDERER)
        self.assertEqual(meta2["sha256"], hashlib.sha256(RENDERER.encode()).hexdigest())

    def test_symlinked_cache_is_fetched_again(self):
        self.mode("ok")
        path, _ = self.fetch()
        other = os.path.join(self.tmp, "elsewhere.html")
        shutil.copy(path, other)
        os.remove(path)
        os.symlink(other, path)
        del HITS[:]
        path, _ = self.fetch()
        self.assertEqual([h for h in HITS if h[1] == "resources/read"], [("ok", "resources/read")])
        self.assertFalse(os.path.islink(path))

    def test_renderer_address_comes_from_tools_list(self):
        self.mode("ok")
        _, meta = self.fetch()
        self.assertEqual(meta["uri"], ADVERTISED)
        self.assertEqual([h[1] for h in HITS], ["tools/list", "resources/read"])

    def test_renderer_address_falls_back_when_tools_list_names_none(self):
        self.mode("nometa")
        _, meta = self.fetch()
        self.assertEqual(meta["uri"], S.RENDERER_URI)

    def test_page_records_renderer_hash(self):
        self.mode("ok")
        self.build()
        page = self.page
        self.assertIn('<meta name="zebra-renderer" content="%s"/>' % hashlib.sha256(RENDERER.encode()).hexdigest(), page)
        self.assertEqual(re.findall(r"\{\{[A-Z_]+\}\}", page.replace("{{FOOTER}} grew", "")), [])


class Endpoint(Base):  # item 13
    def test_only_https_or_loopback_http(self):
        for url in ("file:///etc/hosts", "http://[::1]:1/mcp", "data:,x", "ftp://127.0.0.1/mcp", "https://[::1/mcp"):
            with self.subTest(url=url):
                S.ENDPOINT = url
                msg = self.exit_message(self.render)
                self.assertIn("ZEBRA_BI_MCP_URL must be", msg)

    def test_redirect_is_refused(self):
        self.mode("redirect")
        msg = self.exit_message(self.render)
        self.assertEqual(msg, "ZEBRA_BI_MCP_URL redirects to %s; set it to the final address" % (BASE + "ok"))


class Size(Base):  # item 14
    def test_oversize_response_is_refused(self):
        S.MAX_BYTES = 1000
        self.mode("big")
        msg = self.exit_message(self.render)
        self.assertTrue(msg.startswith("Zebra BI is unavailable ("), msg)
        self.assertIn("more than", msg)


class Lang(unittest.TestCase):  # item 15
    def test_language_tag_is_validated(self):
        b = B.merge(B.load(start=tempfile.gettempdir()), {"voice": {"language": 'en" onload="alert(1)'}})
        for bad in ('en" onload="alert(1)', "English", "en_US"):
            with self.subTest(language=bad):
                b["voice"]["language"] = bad
                self.assertEqual(S.brand_tokens(b)["LANG"], "en")
                errors, warnings = B.check(b)
                self.assertFalse(any("voice.language" in e for e in errors), errors)  # an old brand still builds
                self.assertTrue(any("voice.language" in w for w in warnings), warnings)
        b["voice"]["language"] = "de-CH"
        self.assertEqual(S.brand_tokens(b)["LANG"], "de-CH")
        self.assertFalse(any("voice.language" in m for m in sum(B.check(b), [])))


class Page(Base):  # items 16, 17, 18
    def test_host_version_comes_from_plugin_json(self):
        version = json.loads(read(os.path.join(HERE, "..", "..", ".claude-plugin", "plugin.json")))["version"]
        self.mode("ok")
        self.build()
        page = self.page
        self.assertIn("hostInfo: { name: 'zebra-bi', version: '%s' }" % version, page)

    def test_snapshot_pages_carry_the_version(self):
        from zoffice import snapshot as SN
        version = json.loads(read(os.path.join(HERE, "..", "..", ".claude-plugin", "plugin.json")))["version"]
        page = SN.page("v1", result("t"), RENDERER)
        self.assertIn("version: '%s'" % version, page)
        self.assertNotIn("{{VERSION}}", page)

    def test_reload_is_a_real_navigation(self):
        t = read(S.TEMPLATE)
        self.assertIn("f.src = 'about:blank';", t)
        self.assertIn("f.removeEventListener('load', next); f.src = url;", t)

    def test_template_sets_and_clears_ready(self):
        t = read(S.TEMPLATE)
        self.assertIn("f.setAttribute('data-status', 'ready')", t)
        self.assertIn("f.removeAttribute('data-status')", t)

    def test_template_is_an_a4_page_with_the_renderer_from_a_blob(self):
        t = read(S.TEMPLATE)
        self.assertIn("@page{size:A4;margin:16mm 15mm}", t)
        self.assertIn("main{width:680px;", t)
        self.assertIn("URL.createObjectURL(new Blob(", t)
        self.assertNotIn("visual.html'", t)  # no sibling renderer copy: the page is only opened from disk



class Kicker(unittest.TestCase):  # the label above the key message: story first, then brand, else none
    def test_story_kicker_wins(self):
        b = B.load(tempfile.mkdtemp())
        b["kicker"] = "Finance team"
        self.assertEqual(S.kicker({"kicker": "P&L · September 2026 close"}, b), "P&L · September 2026 close")
        self.assertIn("P&amp;L · September 2026 close", S.brand_tokens(b, {"kicker": "P&L · September 2026 close"})["KICKER"])

    def test_brand_kicker_is_the_fallback(self):
        b = B.load(tempfile.mkdtemp())
        b["kicker"] = "Finance team"
        self.assertEqual(S.kicker({}, b), "Finance team")

    def test_default_has_no_generic_label(self):
        b = B.load(tempfile.mkdtemp())
        self.assertEqual(S.kicker({}, b), "")
        self.assertEqual(S.brand_tokens(b)["KICKER"], "")

if __name__ == "__main__":
    unittest.main()
