"""Tests for the report builder's sign-in: story.py's SignInFlow run in the background, the report server's sign_in,
start_sign_in and sign_out tools, its auth block, charts that wait for sign-in, Zebra BI's refusals and the exports
that wait. Run: python3 -I -m unittest discover <plugin>/scripts/tests

A fake Zebra BI MCP and authorization server listens on 127.0.0.1 only. Like Zebra BI it issues no refresh token,
and its render_visual answers as FAKE["render"] says: a chart, one of Zebra BI's licence refusals (the sentences of
app/server/mcp/licence.py in the ZAI repository) or a refusal for the chart's own arguments. The browser is a
stand-in that opens the authorization page, which redirects to the loopback callback as the real one does. Nothing
reads or writes the real token store or report folder.
"""
import contextlib, io, json, os, re, shutil, stat, subprocess, sys, tempfile, threading, time, unittest, urllib.parse, urllib.request
import http.server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import report as R  # noqa: E402

R._legacy_checked = True  # a test never moves the real ~/Documents reports
import story as S  # noqa: E402
import report_server as RS  # noqa: E402
from test_report import Proc  # noqa: E402

SHIPPED = os.path.join(os.path.dirname(os.path.dirname(HERE)), "assets", "render-visual-schema.json")
ARGS = {"title": "Sales by region", "chart_type": "variance", "category_columns": ["Region"],
        "measure": {"name": "Sales", "Val": "AC", "PL": "PL"},
        "columns": ["Region", "AC", "PL"], "rows": [["West", 226.3, 226.8], ["East", 185.4, 194.4]]}
MONTHS = dict(ARGS, title="Sales by month", category_columns=["Month"], time_axis="yearmonth", columns=["Month", "AC", "PL"],
              rows=[["2026-%02d" % m, 10.0 + m, 11.0] for m in range(1, 13)])
SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "time_axis": {"type": "string"}}}

# Zebra BI's refusals, as app/server/mcp/licence.py (denial_message) words them.
LICENCES = "Zebra BI for Power BI, Zebra BI for Office or Zebra AI"
CONTACT = "If your company has a Zebra BI licence, contact support@zebrabi.com."
REFUSED = {
    "trial": "The visual was not drawn because the user's Zebra BI trial ended on 5 Nov 2026 at 00:00 UTC. "
             "Drawing visuals needs a valid Zebra BI licence: %s. %s" % (LICENCES, CONTACT),
    "nolicence": "The visual was not drawn because no Zebra BI licence (%s) was found for the user's organization "
                 "(contoso.com). The user can start a 14-day trial by disconnecting and reconnecting Zebra BI. %s" % (LICENCES, CONTACT),
    "personal": "The visual was not drawn because no Zebra BI licence (%s) was found for the user's organization "
                "(gmail.com). The user can start a 14-day trial by disconnecting and reconnecting Zebra BI. %s" % (LICENCES, CONTACT),
    "noemail": "The visual was not drawn because Zebra BI could not tell which organization the user belongs to: "
               "Microsoft did not share a verified email address for their account. %s" % CONTACT,
    "otherlicence": "The visual was not drawn because the Zebra BI licence for your organization is suspended. "
                    "Start a trial today. %s" % CONTACT,
    "badargs": "The visual was not drawn. measure.Val names a column that is not in columns: 'Actual'.",
}
FAKE = {}  # render: what render_visual answers; deny: the user refuses on the sign-in page
SEEN = {}  # what the fake servers saw


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def base(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

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
        base, u = self.base(), urllib.parse.urlsplit(self.path)
        if u.path == "/.well-known/oauth-protected-resource/mcp":
            return self.send(200, json.dumps({"resource": base + "/mcp", "authorization_servers": [base]}))
        if u.path == "/.well-known/oauth-authorization-server":
            return self.send(200, json.dumps({
                "issuer": base, "authorization_endpoint": base + "/oauth/authorize", "token_endpoint": base + "/oauth/token",
                "registration_endpoint": base + "/oauth/register", "revocation_endpoint": base + "/oauth/revoke",
                "code_challenge_methods_supported": ["S256"]}))
        if u.path == "/oauth/authorize":  # the sign-in page: the user signs in, it redirects to the loopback callback
            q = dict(urllib.parse.parse_qsl(u.query))
            SEEN["challenge"] = q.get("code_challenge")
            answer = {"error": "access_denied"} if FAKE.get("deny") else {"code": "code-1"}
            answer["state"] = q.get("state", "")
            return self.send(302, "", headers=[("Location", q["redirect_uri"] + "?" + urllib.parse.urlencode(answer))])
        self.send(404, "{}")

    def do_POST(self):
        path = self.path.strip("/")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if path == "oauth/register":
            SEEN["registrations"] = SEEN.get("registrations", 0) + 1
            return self.send(201, json.dumps({"client_id": "cid-1", **json.loads(body)}))
        if path in ("oauth/token", "oauth/revoke"):
            form = dict(urllib.parse.parse_qsl(body.decode()))
            SEEN.setdefault("posts", []).append((path, form))
            if path == "oauth/revoke":
                return self.send(200, b"", ctype="text/plain")
            if form.get("code") != "code-1":
                return self.send(400, '{"error":"invalid_grant"}')
            return self.send(200, json.dumps({"access_token": "good", "token_type": "Bearer", "expires_in": 3600}))
        if self.headers.get("Authorization") != "Bearer good":
            return self.send(401, '{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Sign in to use Zebra BI visuals."}}',
                             headers=[("WWW-Authenticate", 'Bearer resource_metadata="%s/.well-known/oauth-protected-resource/mcp"' % self.base())])
        msg = json.loads(body)
        SEEN.setdefault("calls", []).append(msg.get("method"))
        ok = lambda res: self.send(200, json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": res}))
        if msg.get("method") == "tools/list":
            return ok({"tools": [{"name": "render_visual", "inputSchema": SCHEMA, "_meta": {"ui": {"resourceUri": "ui://zebra-bi/v.html"}}}]})
        if msg.get("method") == "resources/read":
            return ok({"contents": [{"uri": "ui://zebra-bi/v.html", "text": "<!doctype html><html>ui/initialize</html>"}]})
        mode = FAKE.get("render", "ok")
        if mode in REFUSED:
            return ok({"content": [{"type": "text", "text": REFUSED[mode]}], "isError": True})
        title = msg["params"]["arguments"].get("title")
        return ok({"content": [{"type": "text", "text": "Drew %s." % title}], "isError": False,
                   "structuredContent": {"visual": {"title": title}}})


def setUpModule():
    global SERVER, BASE
    SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = "http://127.0.0.1:%d" % SERVER.server_address[1]


def tearDownModule():
    SERVER.shutdown()
    SERVER.server_close()


def visit(url):
    """The user's browser: opens the sign-in page and follows its redirect to the loopback callback."""
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=10).read()
    except Exception:
        pass


def until(check, seconds=8):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = S.ENDPOINT, S.CACHE, S.AUTH, S.SIGN_IN_WAIT, RS.open_browser, dict(RS.state)
        S.ENDPOINT = BASE + "/mcp"
        S.CACHE = os.path.join(self.tmp, "data", "renderer")
        S.AUTH = os.path.join(self.tmp, "data", "auth.json")  # never the real token store
        os.environ["ZEBRA_BI_REPORTS"] = os.path.join(self.tmp, "reports")
        RS.state.clear()
        self.opened = []
        RS.open_browser = lambda url: self.opened.append(url) or threading.Thread(target=visit, args=(url,), daemon=True).start() or True
        FAKE.clear()
        SEEN.clear()

    def tearDown(self):
        until(lambda: S.sign_in_state()["state"] != "waiting", 3)  # let a background sign-in end before its folder goes
        S.ENDPOINT, S.CACHE, S.AUTH, S.SIGN_IN_WAIT, RS.open_browser = self.saved[:5]
        RS.state.clear()
        RS.state.update(self.saved[5])
        os.environ.pop("ZEBRA_BI_REPORTS", None)
        shutil.rmtree(self.tmp)

    def signed_in(self, token="good", **entry):
        S.save_auth({S.ENDPOINT: dict({"access_token": token, "expires_at": int(time.time()) + 3600, "client_id": "cid-1",
                                       "revocation_endpoint": BASE + "/oauth/revoke", "signed_in_at": "2026-11-08T10:00:00"}, **entry)})

    def report(self, title="October close"):
        return RS.call("create_report", {"title": title}, None)["structuredContent"]["report"]

    def get(self, slug, **kw):
        return RS.call("get_report", dict({"report": slug}, **kw), None)["structuredContent"]


class Flow(Base):
    """story.py's SignInFlow, run in the background as the report server runs it."""

    def test_a_sign_in_runs_in_the_background_and_keeps_the_token(self):
        url = S.begin_sign_in()
        st = S.sign_in_state()
        self.assertEqual((st["state"], st["url"], st["pid"]), ("waiting", url, os.getpid()))
        self.assertLessEqual(abs(st["started_at"] - time.time()), 5)
        path = os.path.join(self.tmp, "data", "sign-in.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(url.startswith(BASE + "/oauth/authorize?"))
        visit(url)
        self.assertTrue(until(lambda: S.token() == "good"))
        self.assertTrue(until(lambda: not os.path.exists(path)))
        self.assertEqual(S.sign_in_state(), {"state": "idle"})
        self.assertEqual(stat.S_IMODE(os.stat(S.AUTH).st_mode), 0o600)

    def test_a_second_start_while_waiting_gives_the_same_page(self):
        url = S.begin_sign_in()
        self.assertEqual(S.begin_sign_in(), url)
        self.assertEqual(SEEN["registrations"], 1)  # one client, one listener
        visit(url)
        self.assertTrue(until(lambda: S.token() == "good"))

    def test_a_stale_or_orphaned_wait_is_idle(self):
        os.makedirs(os.path.dirname(S.AUTH))
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        for st in ({"state": "waiting", "url": BASE, "started_at": time.time() - S.SIGN_IN_WAIT - 1, "pid": os.getpid()},
                   {"state": "waiting", "url": BASE, "started_at": time.time(), "pid": gone.pid}):
            S.write_sign_in(st)
            self.assertEqual(S.sign_in_state(), {"state": "idle"})

    def test_a_refused_sign_in_fails_with_its_reason(self):
        FAKE["deny"] = True
        visit(S.begin_sign_in())
        self.assertTrue(until(lambda: S.sign_in_state()["state"] == "failed"))
        self.assertIn("refused (access_denied)", S.sign_in_state()["reason"])
        self.assertIsNone(S.token())

    def test_a_sign_in_nobody_answers_goes_back_to_idle(self):
        S.SIGN_IN_WAIT = 1
        S.begin_sign_in()
        self.assertTrue(until(lambda: S.sign_in_state()["state"] == "idle", 5))
        self.assertIsNone(S.token())

    def test_the_terminal_sign_in_still_waits_for_the_browser(self):
        S.write_sign_in({"state": "failed", "reason": "Zebra BI sign-in timed out after 300 s. Nothing was saved."})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            S.sign_in(open_url=lambda url: threading.Thread(target=visit, args=(url,), daemon=True).start())
        self.assertIn("Signed in to Zebra BI", out.getvalue())
        self.assertEqual(S.token(), "good")
        self.assertEqual(S.sign_in_state(), {"state": "idle"})


class Tools(Base):
    """The report server's tools and results while the user is not signed in, signs in and is refused."""

    def test_not_signed_in_the_auth_block_is_idle(self):
        slug = self.report()
        idle = {"signed_in": False, "state": "idle", "url": None, "refusal": None}
        self.assertEqual(self.get(slug)["auth"], idle)
        self.assertEqual(RS.call("library", {}, None)["structuredContent"]["auth"], idle)

    def test_a_chart_added_without_sign_in_is_saved_and_waits(self):
        slug = self.report()
        out = RS.call("add_chart", {"chart": ARGS, "message": "West on plan", "note": "West AC 226.3."}, None)
        self.assertFalse(out.get("isError"), out)
        said = out["content"][0]["text"]
        self.assertIn("drawn when the user signs in to Zebra BI", said)
        self.assertIn('"Sign in to Zebra BI"', said)
        self.assertIn('"sign me in"', said)
        self.assertNotIn("calls", SEEN)  # no call to Zebra BI without a sign-in
        got = self.get(slug)
        cell = got["report"]["rows"][0]["cells"][0]
        self.assertEqual((cell["drawn"], cell["ghost"]), (False, {"kind": "bars", "count": 2}))
        self.assertEqual((got["results"], got["errors"]), ({}, {}))
        self.assertEqual(R.load(R.path_for(slug))["visuals"]["sales-by-region"]["args"], ARGS)  # saved as sent
        self.assertNotIn("drawn", R.load(R.path_for(slug))["rows"][0]["cells"][0])  # the file keeps no view state
        replaced = RS.call("replace_chart", {"cell": "c1", "chart": MONTHS}, None)
        self.assertFalse(replaced.get("isError"), replaced)
        self.assertIn("drawn when the user signs in", replaced["content"][0]["text"])

    def test_a_time_axis_ghost_is_columns_and_the_count_is_capped(self):
        self.assertEqual(RS.ghost(MONTHS), {"kind": "columns", "count": 12})
        self.assertEqual(RS.ghost(dict(MONTHS, rows=[["x", 1, 1]] * 100)), {"kind": "columns", "count": RS.MAX_GHOST["columns"]})
        self.assertEqual(RS.ghost(dict(ARGS, rows=[["x", 1, 1]] * 100)), {"kind": "bars", "count": RS.MAX_GHOST["bars"]})
        self.assertEqual(RS.ghost({"title": "x"}), {"kind": "bars", "count": 1})

    def test_sign_in_opens_the_browser_and_the_charts_draw_after(self):
        slug = self.report()
        RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)
        out = RS.call("sign_in", {}, None)
        self.assertFalse(out.get("isError"), out)
        url = out["structuredContent"]["url"]
        self.assertEqual((out["structuredContent"]["opened"], self.opened), (True, [url]))
        self.assertIn("sign-in page opened in the user's browser", out["content"][0]["text"])
        self.assertTrue(until(lambda: self.get(slug)["auth"]["signed_in"]))
        got = self.get(slug)
        self.assertEqual(got["auth"], {"signed_in": True, "state": "signed_in", "url": None, "refusal": None})
        self.assertEqual(got["results"]["sales-by-region"]["structuredContent"]["visual"]["title"], "Sales by region")
        self.assertTrue(got["report"]["rows"][0]["cells"][0]["drawn"])

    def test_sign_in_says_where_the_page_is_when_no_browser_opens(self):
        RS.open_browser = lambda url: False
        out = RS.call("sign_in", {}, None)
        url = out["structuredContent"]["url"]
        self.assertEqual(out["structuredContent"]["opened"], False)
        self.assertIn(url, out["content"][0]["text"])
        self.assertEqual(RS.auth()["state"], "waiting")
        self.assertEqual(RS.auth()["url"], url)
        self.assertEqual(RS.call("start_sign_in", {}, None)["structuredContent"]["url"], url)  # the same page, one listener
        visit(url)
        self.assertTrue(until(lambda: RS.auth()["signed_in"]))

    def test_sign_in_when_signed_in_opens_nothing(self):
        self.signed_in()
        out = RS.call("sign_in", {}, None)
        self.assertEqual((out["structuredContent"]["opened"], self.opened), (False, []))
        self.assertIn("already signed in", out["content"][0]["text"])
        self.assertTrue(RS.call("start_sign_in", {}, None)["structuredContent"]["opened"])  # the builder's Sign in again

    def test_refusals_are_recognized_from_zebra_bis_sentences(self):
        want = {"trial": ("trial_ended", "5 Nov 2026"), "nolicence": ("no_licence", None), "personal": ("no_licence", None),
                "noemail": ("no_verified_email", None), "otherlicence": ("other", None)}
        for mode, (kind, date) in want.items():
            got = RS.recognize(REFUSED[mode])
            self.assertEqual((got["kind"], got["date"]), (kind, date), mode)
        self.assertIsNone(RS.recognize(REFUSED["badargs"]))
        self.assertEqual(RS.recognize(REFUSED["trial"])["message"],
                         "Your Zebra BI trial ended on 5 Nov 2026. Your report and its texts stay; the charts need a Zebra BI licence.")
        self.assertEqual(RS.recognize(REFUSED["nolicence"])["message"], "Zebra BI found no licence for your company.")
        self.assertIn("support@zebrabi.com", RS.recognize(REFUSED["noemail"])["message"])
        other = RS.recognize(REFUSED["otherlicence"])["message"]
        self.assertIn("suspended", other)
        self.assertNotIn("trial", other.lower())  # Zebra BI's own sentence, without an offer of a trial

    def test_a_refused_chart_is_saved_and_the_auth_block_reports_it(self):
        self.signed_in()
        slug = self.report()
        for i, mode in enumerate(("trial", "nolicence", "noemail", "otherlicence")):
            FAKE["render"] = mode
            out = RS.call("add_chart", {"chart": dict(ARGS, title="Chart %d" % i), "message": "m"}, None)
            self.assertFalse(out.get("isError"), (mode, out))
            self.assertIn("Zebra BI did not draw it", out["content"][0]["text"])
            self.assertNotIn("trial", out["content"][0]["text"].lower(), mode)
        got = self.get(slug)
        self.assertEqual(got["auth"]["refusal"]["kind"], "other")  # the last one
        self.assertTrue(got["auth"]["signed_in"])
        self.assertEqual(got["errors"], {})  # a refusal is said once, in the banner, never per chart
        self.assertEqual([c["drawn"] for r in got["report"]["rows"] for c in r["cells"]], [False] * 4)
        FAKE["render"] = "trial"
        self.get(slug)
        self.assertEqual(RS.auth()["refusal"], {"kind": "trial_ended", "date": "5 Nov 2026", "message":
                         "Your Zebra BI trial ended on 5 Nov 2026. Your report and its texts stay; the charts need a Zebra BI licence."})

    def test_a_chart_zebra_bi_cannot_draw_is_no_refusal(self):
        self.signed_in()
        slug = self.report()
        FAKE["render"] = "badargs"
        out = RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)
        self.assertTrue(out.get("isError"))
        self.assertIn("measure.Val names a column", out["content"][0]["text"])
        self.assertEqual(R.load(R.path_for(slug))["visuals"], {})  # not added
        self.assertIsNone(RS.auth()["refusal"])

    def test_a_drawn_chart_clears_a_licence_refusal(self):
        self.signed_in()
        self.report()
        FAKE["render"] = "nolicence"
        RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)
        self.assertEqual(RS.auth()["refusal"]["kind"], "no_licence")
        FAKE["render"] = "ok"
        self.assertFalse(RS.call("add_chart", {"chart": MONTHS, "message": "m"}, None).get("isError"))
        self.assertIsNone(RS.auth()["refusal"])

    def test_a_new_sign_in_clears_the_refusal(self):
        self.signed_in()
        self.report()
        FAKE["render"] = "nolicence"
        RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)
        RS.call("start_sign_in", {}, None)  # Sign in again, so Zebra BI's page can offer what it offers
        self.assertTrue(until(lambda: S.load_auth()[S.ENDPOINT]["signed_in_at"] != "2026-11-08T10:00:00"))
        self.assertTrue(until(lambda: RS.auth()["state"] == "signed_in"))
        self.assertIsNone(RS.auth()["refusal"])

    def test_an_expired_sign_in_is_its_own_refusal(self):
        self.signed_in(expires_at=int(time.time()) - 10)
        slug = self.report()
        out = RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)
        self.assertFalse(out.get("isError"), out)
        self.assertIn("sign-in has expired", out["content"][0]["text"])
        self.assertNotIn("calls", SEEN)
        auth = self.get(slug)["auth"]
        self.assertEqual((auth["signed_in"], auth["state"]), (False, "idle"))
        self.assertEqual(auth["refusal"], {"kind": "expired", "date": None,
                                           "message": "Your Zebra BI sign-in has expired. Sign in again to draw the charts."})

    def test_a_token_zebra_bi_turns_away_is_expired(self):
        self.signed_in(token="revoked")
        slug = self.report()
        out = RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)
        self.assertFalse(out.get("isError"), out)
        auth = self.get(slug)["auth"]
        self.assertEqual((auth["signed_in"], auth["refusal"]["kind"]), (False, "expired"))

    def test_exports_wait_for_the_charts(self):
        slug = self.report()
        RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)
        for tool, what in (("save_pdf", "PDF"), ("save_pptx", "PowerPoint")):
            out = RS.call(tool, {}, None)
            self.assertTrue(out.get("isError"), tool)
            said = out["content"][0]["text"]
            self.assertTrue(said.startswith("No %s was saved: 1 chart is not drawn yet" % what), said)
            self.assertIn('"Sign in to Zebra BI"', said)
            self.assertIn("sign_in", said)
            self.assertEqual(out["structuredContent"]["waiting"], ["c1"])
        self.assertEqual([f for f in os.listdir(os.path.dirname(R.path_for(slug))) if f.endswith((".pdf", ".pptx"))], [])

    def test_sign_out_revokes_forgets_and_prints_nothing(self):
        self.signed_in()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            res = RS.call("sign_out", {}, None)
        self.assertEqual(out.getvalue(), "")  # stdout is the JSON-RPC stream
        self.assertFalse(res.get("isError"), res)
        self.assertEqual(res["structuredContent"]["auth"]["signed_in"], False)
        self.assertEqual(S.load_auth(), {})
        self.assertEqual(SEEN["posts"][-1], ("oauth/revoke", {"token": "good", "token_type_hint": "access_token", "client_id": "cid-1"}))

    def test_no_text_names_a_command_or_offers_a_trial(self):
        texts = [t["description"] for t in RS.tools()]
        slug = self.report()
        texts.append(RS.call("add_chart", {"chart": ARGS, "message": "m"}, None)["content"][0]["text"])
        texts.append(RS.call("save_pdf", {"report": slug}, None)["content"][0]["text"])
        texts.append(RS.call("sign_in", {}, None)["content"][0]["text"])
        for t in texts:
            for word in ("story.py", "python", "terminal", "trial"):
                self.assertNotIn(word, t.lower(), t)

    def test_the_shipped_schema_stands_in_until_sign_in(self):
        with open(SHIPPED, encoding="utf-8") as f:
            shipped = json.load(f)
        self.assertIn("time_axis", shipped["properties"])
        tools = {t["name"]: t for t in RS.tools()}
        self.assertEqual(tools["add_chart"]["inputSchema"]["properties"]["chart"], shipped)
        self.assertNotIn("calls", SEEN)  # not signed in: no call for the live schema
        RS.state.clear()
        self.signed_in()
        tools = {t["name"]: t for t in RS.tools()}
        self.assertEqual(tools["add_chart"]["inputSchema"]["properties"]["chart"], SCHEMA)  # the live one, once signed in
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "data", "render_visual.schema.json")))


class Stdio(unittest.TestCase):
    """The new tools through report_server.py over stdio, as a host drives it; the browser is a stand-in program."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        browser = os.path.join(self.tmp, "browser")
        with open(browser, "w") as f:
            f.write("#!%s -I\nimport sys, urllib.request\n"
                    "urllib.request.build_opener(urllib.request.ProxyHandler({})).open(sys.argv[1], timeout=10).read()\n" % sys.executable)
        os.chmod(browser, 0o700)
        FAKE.clear()
        SEEN.clear()
        self.server = Proc(self.tmp, env_extra={"ZEBRA_BI_MCP_URL": BASE + "/mcp", "BROWSER": browser, "HOME": self.tmp})

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.tmp)

    def test_sign_in_draws_the_waiting_charts(self):
        tools = {t["name"]: t for t in self.server.rpc("tools/list")["result"]["tools"]}
        self.assertNotIn("visibility", tools["sign_in"].get("_meta", {}).get("ui", {}))
        for name in ("start_sign_in", "sign_out", "draw_chart"):
            self.assertEqual(tools[name]["_meta"]["ui"]["visibility"], ["app"], name)
        slug = self.server.tool("create_report", {"title": "October close"})["structuredContent"]["report"]
        self.assertFalse(self.server.tool("add_chart", {"chart": MONTHS, "message": "m"}).get("isError"))
        got = self.server.tool("get_report", {"report": slug})["structuredContent"]
        self.assertEqual(got["auth"]["state"], "idle")
        self.assertEqual(got["report"]["rows"][0]["cells"][0]["ghost"], {"kind": "columns", "count": 12})
        out = self.server.rpc("tools/call", {"name": "start_sign_in", "arguments": {}})["result"]
        self.assertTrue(out.get("isError"))  # app only: the builder's key
        out = self.server.tool("sign_in")
        self.assertTrue(out["structuredContent"]["opened"], out)
        self.assertTrue(until(lambda: self.server.tool("library")["structuredContent"]["auth"]["signed_in"]))
        unchanged = self.server.tool("get_report", {"report": slug, "since": got["report"]["rev"]})["structuredContent"]
        self.assertTrue(unchanged["unchanged"])
        self.assertTrue(unchanged["auth"]["signed_in"])  # the poll learns the sign-in without a new rev
        one = self.server.tool("draw_chart", {"report": slug, "cell": "c1"})["structuredContent"]
        self.assertTrue(one["drawn"])
        self.assertEqual(one["result"]["structuredContent"]["visual"]["title"], "Sales by month")
        self.assertTrue(self.server.tool("get_report", {"report": slug, "draw": False})["structuredContent"]["report"]["rows"][0]["cells"][0]["drawn"])
        out = self.server.tool("sign_out")
        self.assertFalse(out["structuredContent"]["auth"]["signed_in"])
        with open(os.path.join(self.tmp, "data", "auth.json")) as f:
            self.assertEqual(json.load(f), {})


if __name__ == "__main__":
    unittest.main()
