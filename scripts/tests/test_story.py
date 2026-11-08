"""Tests for story.py (and the page pdf.py prints) against a fake Zebra BI server. Run: python3 -I -m unittest discover <plugin>/scripts/tests

The fake server listens on 127.0.0.1 only and picks its behaviour from the URL path
(ZEBRA_BI_MCP_URL=http://127.0.0.1:<port>/<mode>): well-formed answers, malformed ones,
HTTP errors, a redirect and an oversize body. Mode "authed" wants a Bearer token and the
server also plays the authorization server (metadata, registration, token, revocation).
"""
import base64, contextlib, hashlib, http.server, io, json, os, re, shutil, socket, stat, sys, tempfile, threading, time, unittest, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brand as B  # noqa: E402
import story as S  # noqa: E402

RENDERER = "<!doctype html><html><body>fake renderer<script>/* answers ui/initialize */</script></body></html>"
ADVERTISED = "ui://zebra-bi/visual-v9.html"  # what the fake server's tools/list names; not the built-in fallback
HITS = []
AUTHS = []  # the Authorization header of every MCP call
OAUTH = {}  # what the fake authorization server saw


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
        base = "http://127.0.0.1:%d" % self.server.server_address[1]
        prm = {  # protected-resource metadata per sign-in mode
            "authed": {"resource": base + "/authed", "authorization_servers": [base]},
            "otherresource": {"resource": "https://mcp.zebrabi.com", "authorization_servers": [base]},  # a hostile endpoint
            "otherissuer": {"resource": base + "/otherissuer", "authorization_servers": [base + "/as-otherissuer"]},
            "isspromised": {"resource": base + "/isspromised", "authorization_servers": [base + "/as-isspromised"]},
        }
        if self.path.startswith("/.well-known/oauth-protected-resource/") and self.path.rsplit("/", 1)[1] in prm:
            return self.send(200, json.dumps(prm[self.path.rsplit("/", 1)[1]]))
        if self.path.startswith("/.well-known/oauth-authorization-server"):
            suffix = self.path[len("/.well-known/oauth-authorization-server"):]
            meta = {"issuer": base + suffix, "authorization_endpoint": base + "/oauth/authorize", "token_endpoint": base + "/oauth/token",
                    "registration_endpoint": base + "/oauth/register", "revocation_endpoint": base + "/oauth/revoke",
                    "code_challenge_methods_supported": ["S256"]}
            if suffix == "/as-otherissuer":
                meta["issuer"] = "https://someone-else.example"
            if suffix == "/as-isspromised":
                meta["authorization_response_iss_parameter_supported"] = True
            if suffix in ("", "/as-otherissuer", "/as-isspromised"):
                return self.send(200, json.dumps(meta))
        self.send(405, '{"error":{"message":"Method Not Allowed"}}')

    def oauth(self, mode, body):
        """The fake authorization server: registration, the code and refresh grants, revocation."""
        if mode == "oauth/register":
            reg = json.loads(body)
            OAUTH["redirect_uris"] = reg["redirect_uris"]
            return self.send(201, json.dumps({"client_id": "cid-1", **reg}))
        form = dict(urllib.parse.parse_qsl(body.decode()))
        OAUTH.setdefault("posts", []).append((mode, form))
        if mode == "oauth/revoke":  # as mcp.zebrabi.com answers: 200 and no body, which RFC 7009 has the client ignore
            return self.send(200, b"", ctype="text/plain")
        if form.get("grant_type") == "refresh_token":
            if form.get("refresh_token") != "refresh-1":
                return self.send(400, '{"error":"invalid_grant"}')
            return self.send(200, json.dumps({"access_token": "good", "token_type": "Bearer", "expires_in": 3600}))
        verifier_ok = OAUTH.get("challenge") == base64.urlsafe_b64encode(
            hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
        if form.get("code") != "code-1" or not verifier_ok or form.get("redirect_uri") not in OAUTH.get("redirect_uris", []):
            return self.send(400, '{"error":"invalid_grant","error_description":"bad code or verifier"}')
        return self.send(200, json.dumps({"access_token": "good", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "refresh-1"}))

    def do_POST(self):
        mode = self.path.strip("/")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if mode.startswith("oauth/"):
            return self.oauth(mode, body)
        AUTHS.append(self.headers.get("Authorization"))
        if mode in ("authed", "otherresource", "otherissuer", "isspromised") and self.headers.get("Authorization") != "Bearer good":
            base = "http://127.0.0.1:%d" % self.server.server_address[1]
            return self.send(401, '{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Sign in to use Zebra BI visuals."}}',
                             headers=[("WWW-Authenticate", 'Bearer resource_metadata="%s/.well-known/oauth-protected-resource/%s"' % (base, mode))])
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
        self.saved = S.ENDPOINT, S.CACHE, getattr(S, "MAX_BYTES", None), S.AUTH
        S.CACHE = os.path.join(self.tmp, "cache", "renderer")
        S.AUTH = os.path.join(self.tmp, "cache", "auth.json")  # never the real token store
        del AUTHS[:]
        OAUTH.clear()
        self.story_dir = os.path.join(self.tmp, "story")
        os.makedirs(self.story_dir)
        with open(os.path.join(self.story_dir, "story.json"), "w") as f:
            json.dump(STORY, f)
        del HITS[:]

    def tearDown(self):
        S.ENDPOINT, S.CACHE = self.saved[:2]
        if self.saved[2] is not None:
            S.MAX_BYTES = self.saved[2]
        S.AUTH = self.saved[3]
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
        del HITS[:]  # every visual and the renderer cached: no network call, no disclosure
        out = self.build()
        self.assertEqual(HITS, [])
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
        self.assertIn("@page{size:{{PAGE_SIZE}};margin:16mm 15mm}", t)  # A4, or A4 landscape for a one-pager
        self.assertIn("main{width:680px;", t)
        self.assertIn("URL.createObjectURL(new Blob(", t)
        self.assertNotIn("visual.html'", t)  # no sibling renderer copy: the page is only opened from disk



class SignIn(Base):  # sign-in: the MCP authorization flow against the fake server, and the kept token
    def browser(self, deny=False, state=None):
        """A stand-in for the user's browser: answers the authorization request on the loopback redirect."""
        def open_url(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            OAUTH["challenge"], OAUTH["authorize"] = q["code_challenge"], q
            answer = {"error": "access_denied"} if deny else {"code": "code-1"}
            answer["state"] = state or q["state"]

            def visit():
                time.sleep(0.2)
                try:
                    urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode(answer), timeout=5).read()
                except Exception:
                    pass
                if state:  # a forged answer is refused; then the real one arrives
                    urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": "code-1", "state": q["state"]}), timeout=5).read()
            threading.Thread(target=visit, daemon=True).start()
        return open_url

    def sign_in(self, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            S.sign_in(open_url=self.browser(**kw))
        del AUTHS[:]  # the unauthenticated discovery probe
        return out.getvalue()

    def test_build_without_sign_in_names_the_command(self):
        self.mode("authed")
        msg = self.exit_message(self.render)
        self.assertTrue(msg.startswith("Zebra BI needs sign-in (%s)" % (BASE + "authed")), msg)
        self.assertIn("sign-in`, then build again", msg)
        self.assertEqual(AUTHS, [None])

    def test_sign_in_keeps_a_token_that_builds_use(self):
        self.mode("authed")
        out = self.sign_in()
        self.assertIn("Signed in to Zebra BI", out)
        self.assertNotIn("good", out)  # the token is never printed
        q = OAUTH["authorize"]
        self.assertEqual((q["code_challenge_method"], q["resource"], q["client_id"]), ("S256", BASE + "authed", "cid-1"))
        self.assertTrue(q["redirect_uri"].startswith("http://127.0.0.1:"))
        self.assertEqual(stat.S_IMODE(os.stat(S.AUTH).st_mode), 0o600)
        entry = json.loads(read(S.AUTH))[BASE + "authed"]
        self.assertEqual((entry["access_token"], entry["refresh_token"]), ("good", "refresh-1"))
        out, res = self.render()
        self.assertIn("[v1] The chart is fine.", out)
        self.assertEqual(AUTHS[-1], "Bearer good")

    def test_token_goes_only_to_its_own_endpoint(self):
        self.mode("authed")
        self.sign_in()
        self.mode("ok")
        self.render()
        self.assertEqual(AUTHS, [None])

    def test_expired_token_is_refreshed_once(self):
        self.mode("authed")
        self.sign_in()
        data = json.loads(read(S.AUTH))
        data[BASE + "authed"].update(access_token="stale", expires_at=int(time.time()) - 10)
        S.save_auth(data)
        self.render()
        self.assertEqual(AUTHS[-1], "Bearer good")
        self.assertEqual(OAUTH["posts"][-1][1]["grant_type"], "refresh_token")
        self.assertEqual(json.loads(read(S.AUTH))[BASE + "authed"]["access_token"], "good")

    def test_expired_token_without_refresh_needs_sign_in(self):
        self.mode("authed")
        self.sign_in()
        data = json.loads(read(S.AUTH))
        data[BASE + "authed"].update(access_token="stale", expires_at=int(time.time()) - 10)
        del data[BASE + "authed"]["refresh_token"]
        S.save_auth(data)
        msg = self.exit_message(self.render)
        self.assertTrue(msg.startswith("Zebra BI needs sign-in ("), msg)
        self.assertEqual(AUTHS, [None])

    def test_denied_sign_in_saves_nothing(self):
        self.mode("authed")
        msg = self.exit_message(lambda: self.sign_in(deny=True))
        self.assertIn("refused (access_denied)", msg)
        self.assertFalse(os.path.exists(S.AUTH))

    def test_answer_with_another_state_is_ignored(self):
        self.mode("authed")
        out = self.sign_in(state="forged")
        self.assertIn("Signed in to Zebra BI", out)

    def test_sign_out_revokes_and_forgets(self):
        self.mode("authed")
        self.sign_in()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            S.sign_out()
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(OAUTH["posts"][-2:], [
            ("oauth/revoke", {"token": "refresh-1", "token_type_hint": "refresh_token", "client_id": "cid-1"}),
            ("oauth/revoke", {"token": "good", "token_type_hint": "access_token", "client_id": "cid-1"})])
        self.assertEqual(json.loads(read(S.AUTH)), {})
        msg = self.exit_message(self.render)
        self.assertTrue(msg.startswith("Zebra BI needs sign-in ("), msg)

    def test_sign_out_forgets_an_older_address_too(self):
        self.mode("authed")
        self.sign_in()
        data = json.loads(read(S.AUTH))
        data["https://old.example/mcp"] = dict(data[BASE + "authed"], access_token="old")
        S.save_auth(data)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            S.sign_out()
        self.assertEqual(json.loads(read(S.AUTH)), {})
        self.assertIn("Signed out of Zebra BI (https://old.example/mcp)", out.getvalue())
        self.assertIn(("oauth/revoke", {"token": "old", "token_type_hint": "access_token", "client_id": "cid-1"}), OAUTH["posts"])

    def test_metadata_naming_another_resource_is_refused(self):
        # A hostile endpoint names the real Zebra BI MCP as its resource: signing in would mint a token for that
        # server and send it here (RFC 9728 section 3.3), so sign-in stops before the browser opens.
        self.mode("otherresource")
        opened = []
        with contextlib.redirect_stdout(io.StringIO()):
            msg = self.exit_message(S.sign_in, opened.append)
        self.assertIn("names another server (https://mcp.zebrabi.com)", msg)
        self.assertEqual(opened, [])
        self.assertFalse(os.path.exists(S.AUTH))

    def test_issuer_that_differs_from_its_address_is_refused(self):
        self.mode("otherissuer")
        msg = self.exit_message(S.sign_in, lambda url: None)
        self.assertIn("calls itself https://someone-else.example", msg)

    def test_missing_iss_is_refused_when_the_server_promises_it(self):
        self.mode("isspromised")
        msg = self.exit_message(lambda: self.sign_in())
        self.assertIn("without naming its issuer", msg)
        self.assertFalse(os.path.exists(S.AUTH))

    def test_an_idle_connection_does_not_hold_sign_in(self):
        self.mode("authed")
        idle = []

        def open_url(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            port = urllib.parse.urlsplit(q["redirect_uri"]).port
            idle.append(socket.create_connection(("127.0.0.1", port)))  # connects and never sends a byte
            self.browser()(url)
        start = time.time()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            S.sign_in(open_url=open_url)
        self.assertIn("Signed in to Zebra BI", out.getvalue())
        self.assertLess(time.time() - start, 4)
        idle[0].close()

    def test_symlinked_token_store_is_not_read(self):
        other = os.path.join(self.tmp, "elsewhere.json")
        with open(other, "w") as f:
            json.dump({BASE + "authed": {"access_token": "good"}}, f)
        os.makedirs(os.path.dirname(S.AUTH), exist_ok=True)
        os.symlink(other, S.AUTH)
        self.mode("authed")
        self.assertIsNone(S.token())


class EndpointCache(Base):  # a renderer or chart fetched from another endpoint is never reused
    def fetch(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return S.renderer()

    def test_renderer_from_another_endpoint_is_fetched_again(self):
        self.mode("ok")
        self.fetch()
        self.mode("nometa")
        del HITS[:]
        _, meta = self.fetch()
        self.assertEqual(meta["endpoint"], BASE + "nometa")
        self.assertIn(("nometa", "resources/read"), HITS)

    def test_renderer_from_another_endpoint_is_no_fallback(self):
        self.mode("ok")
        self.fetch()
        self.mode("e405")
        msg = self.exit_message(S.renderer)
        self.assertTrue(msg.startswith("Zebra BI is unavailable ("), msg)

    def test_chart_from_another_endpoint_is_rendered_again(self):
        self.mode("ok")
        self.render()
        self.mode("pretty")
        out, _ = self.render()
        self.assertIn("Sending 1 chart(s) to %s" % (BASE + "pretty"), out)
        self.assertEqual(json.loads(read(os.path.join(self.story_dir, "visuals", "v1.json")))["endpoint"], BASE + "pretty")

    def test_chart_cached_without_an_endpoint_is_kept(self):  # the bundled sample and older builds
        self.mode("ok")
        self.render()
        path = os.path.join(self.story_dir, "visuals", "v1.json")
        data = json.loads(read(path))
        del data["endpoint"]
        S.write_atomic(path, json.dumps(data))
        self.mode("e405")
        out, _ = self.render()
        self.assertIn("[v1] unchanged, reused", out)

    def test_a_linked_visuals_folder_is_refused(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.story_dir, "visuals"))
        self.mode("ok")
        msg = self.exit_message(self.render)
        self.assertIn("symbolic link", msg)
        self.assertEqual(os.listdir(elsewhere), [])


class Proxy(Base):  # $HTTP_PROXY must not see calls to a loopback test server, or their token
    def test_loopback_calls_skip_the_proxy(self):
        keys = ("http_proxy", "HTTP_PROXY", "no_proxy", "NO_PROXY")
        saved = {k: os.environ.get(k) for k in keys}
        os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"  # nothing listens there
        os.environ.pop("no_proxy", None)
        os.environ.pop("NO_PROXY", None)
        try:
            self.mode("ok")
            out, _ = self.render()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertIn("[v1] The chart is fine.", out)


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
