#!/usr/bin/env python3
"""story.py: render the visuals of a Zebra BI data story and lay out its page.

  python3 -I story.py renderer [--refresh]   fetch/cache the chart renderer, print its path
  python3 -I story.py sign-in                sign in to Zebra BI in the browser, keep the token
  python3 -I story.py sign-out               forget (and revoke) every kept token

Library for pdf.py and office.py. Standard library only. The Zebra BI server
is the `zebra-bi` URL in the plugin's .mcp.json, so the scripts and the
connector always use the same one; $ZEBRA_BI_MCP_URL overrides it for testing.
It must be https://, or http:// on 127.0.0.1 or localhost, and must not
redirect. The renderer
cache lives in $CLAUDE_PLUGIN_DATA (or ~/.cache/zebra-bi), is checked
against its recorded sha256 and endpoint on every read and refreshes once a day. The
renderer's address is the one render_visual names in tools/list
(_meta.ui.resourceUri), because the server versions it.

story.json (written by the data-story skill) holds the narrative and, per
visual, the exact `render_visual` arguments. `render_visuals` calls
render_visual over HTTP for each visual whose arguments changed since the
last build, stores the result in visuals/<id>.json and prints the server's
own text for each one (it lists every adjustment the server made).
`assemble` returns the branded story page with the renderer and every
chart's result embedded, which pdf.py prints.

Sign-in follows the MCP authorization spec: `sign-in` reads the server's
protected-resource and authorization-server metadata, registers a public
client, opens the browser for an authorization-code grant with PKCE (S256)
and a loopback redirect on 127.0.0.1, and keeps the token in auth.json next
to the renderer cache (0600). The token is kept per endpoint and sent only
to that endpoint, as `Authorization: Bearer`. When it has expired and no
refresh token was issued, the next call needs `sign-in` again.

When the server cannot be used, a build stops with one line and writes no
file: "Zebra BI is unavailable (<reason>)" or "Zebra BI needs sign-in
(<endpoint>)". A build whose visuals are all cached needs no server.
"""
import base64, datetime as dt, hashlib, html, http.client, http.server, json, os, re, secrets, sys, time, urllib.error, urllib.parse, urllib.request, webbrowser

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
AUTH = os.path.join(os.path.dirname(CACHE), "auth.json")
SIGN_IN_WAIT = 300  # seconds the browser sign-in may take
SIGN_IN_CMD = "python3 -I %s sign-in" % os.path.abspath(__file__)
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


def write_atomic(path, data, mode=0o666):
    """Write to a temp file in the same folder, then rename over path, so a crash never leaves half a file."""
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    tmp = os.path.join(os.path.dirname(path), ".%s.%s.tmp" % (os.path.basename(path), os.urandom(4).hex()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def safe_url(url):
    """True for an https:// URL, or http:// on 127.0.0.1 or localhost, with no user part, backslash, space or
    control character (urllib would drop some of those, so the checked address and the one used would differ)."""
    try:
        if re.search(r"[\x00-\x20\x7f\\]", url):
            return False
        u = urllib.parse.urlsplit(url)
        if "@" in u.netloc:
            return False
        return bool(u.scheme == "https" and u.hostname or u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))
    except (TypeError, ValueError, AttributeError):
        return False


def opener(url, *handlers):
    """A urllib opener for url. Loopback addresses skip any proxy, which would see a test token in clear text."""
    if urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost"):
        handlers += (urllib.request.ProxyHandler({}),)
    return urllib.request.build_opener(*handlers)


def same_resource(a, b):
    """True when two URLs name the same resource (scheme, host, port and path, ignoring a trailing slash)."""
    try:
        ua, ub = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
        key = lambda u: (u.scheme.lower(), u.hostname, u.port or {"https": 443, "http": 80}.get(u.scheme.lower()), u.path.rstrip("/"))
        return key(ua) == key(ub)
    except (TypeError, ValueError, AttributeError):
        return False


def check_endpoint():
    if not safe_url(ENDPOINT):
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
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    tok = token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers=headers)
    try:
        with opener(ENDPOINT, NoRedirect).open(req, timeout=timeout) as resp:
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
        sys.exit("Zebra BI needs sign-in (%s). Nothing was built; run `%s`, then build again." % (ENDPOINT, SIGN_IN_CMD))
    sys.exit("Zebra BI is unavailable (%s). Nothing was built; try again when the server is back." % e)


# Sign-in: the MCP authorization flow (OAuth 2.1 authorization code + PKCE, loopback redirect).

class NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise Unavailable("%s redirects to %s" % (clean(req.full_url), clean(newurl)))


def http_json(url, body=None, form=False, timeout=30, ignore_body=False):
    """GET url (body None) or POST body as JSON or a form; the JSON answer ({} with ignore_body). Raises Unavailable."""
    if not safe_url(url):
        raise Unavailable("refusing %r: not https:// or loopback http://" % clean(url))
    data, headers = None, {"Accept": "application/json"}
    if body is not None:
        data = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET", headers=headers)
    try:
        with opener(url, NoFollow).open(req, timeout=timeout) as resp:
            raw = resp.read(1 << 20)
    except urllib.error.HTTPError as e:
        with e:
            detail = e.read(4096).decode("utf-8", "replace")
        try:
            err = json.loads(detail)
            detail = err.get("error_description") or err.get("error") or detail
        except (ValueError, AttributeError):
            pass
        raise Unavailable("%s answered %d: %s" % (clean(url), e.code, clean(detail, 300)))
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise Unavailable("cannot reach %s: %s" % (clean(url), clean(getattr(e, "reason", e))))
    if ignore_body:
        return {}
    try:
        res = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Unavailable("%s did not answer with JSON" % clean(url))
    if not isinstance(res, dict):
        raise Unavailable("%s did not answer with a JSON object" % clean(url))
    return res


def well_known(url, name):
    """The RFC 8414 / 9728 well-known address of url: /.well-known/<name> inserted before its path."""
    u = urllib.parse.urlsplit(url)
    path = u.path.rstrip("/")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "/.well-known/%s%s" % (name, path), "", ""))


def discover():
    """(authorization server metadata, resource) for ENDPOINT. Raises Unavailable."""
    check_endpoint()
    rm_urls = []
    probe = urllib.request.Request(ENDPOINT, method="POST", data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
                                   headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        opener(ENDPOINT, NoFollow).open(probe, timeout=30).close()
    except urllib.error.HTTPError as e:
        with e:
            m = re.search(r'resource_metadata="([^"]+)"', e.headers.get("WWW-Authenticate") or "")
        if m:
            rm_urls.append(m.group(1))
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise Unavailable("cannot reach %s: %s" % (ENDPOINT, clean(getattr(e, "reason", e))))
    u = urllib.parse.urlsplit(ENDPOINT)
    parent = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path.rstrip("/").rsplit("/", 1)[0] + "/", "", ""))
    rm_urls += [well_known(ENDPOINT, "oauth-protected-resource"), well_known(parent, "oauth-protected-resource")]
    rm, last = None, None
    for url in dict.fromkeys(rm_urls):
        try:
            rm = http_json(url)
            break
        except Unavailable as e:
            last = e
    if not rm:
        raise Unavailable("no protected-resource metadata for %s (%s)" % (ENDPOINT, last))
    # RFC 9728 section 3.3: metadata that names another resource is refused, or a token minted for that
    # server (say the real Zebra BI MCP) would be sent to this endpoint.
    resource = rm.get("resource")
    if not (isinstance(resource, str) and same_resource(resource, ENDPOINT)):
        raise Unavailable("the sign-in metadata of %s names another server (%s)" % (ENDPOINT, clean(str(resource))))
    servers = rm.get("authorization_servers")
    if not (isinstance(servers, list) and servers and isinstance(servers[0], str)):
        raise Unavailable("%s names no authorization server" % ENDPOINT)
    issuer = servers[0]
    meta, last = None, None
    for name in ("oauth-authorization-server", "openid-configuration"):
        try:
            meta = http_json(well_known(issuer, name))
            break
        except Unavailable as e:
            last = e
    if not meta:
        raise Unavailable("no metadata for the authorization server %s (%s)" % (clean(issuer), last))
    for key in ("authorization_endpoint", "token_endpoint"):
        if not safe_url(meta.get(key)):
            raise Unavailable("the authorization server %s has no usable %s" % (clean(issuer), key))
    if "S256" not in (meta.get("code_challenge_methods_supported") or []):
        raise Unavailable("the authorization server %s does not offer PKCE with S256" % clean(issuer))
    if meta.setdefault("issuer", issuer) != issuer:  # RFC 8414 section 3.3
        raise Unavailable("the authorization server %s calls itself %s" % (clean(issuer), clean(str(meta["issuer"]))))
    return meta, resource


def load_auth():
    """Every kept sign-in, {endpoint: entry}; {} when there is none or the file is not a plain file."""
    if os.path.islink(AUTH):
        return {}
    try:
        with open(AUTH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_auth(data):
    os.makedirs(os.path.dirname(AUTH), mode=0o700, exist_ok=True)
    write_atomic(AUTH, json.dumps(data, indent=2), mode=0o600)
    os.chmod(AUTH, 0o600)


def keep(entry, tok):
    """entry updated with a token-endpoint answer (access token, expiry, refresh token)."""
    if not isinstance(tok.get("access_token"), str) or not tok["access_token"]:
        raise Unavailable("the token endpoint sent no access_token")
    entry["access_token"] = tok["access_token"]
    life = tok.get("expires_in")
    entry["expires_at"] = int(time.time() + life) if isinstance(life, (int, float)) and life > 0 else None
    if isinstance(tok.get("refresh_token"), str):
        entry["refresh_token"] = tok["refresh_token"]
    return entry


def token():
    """The access token kept for ENDPOINT while it is valid, refreshed once if a refresh token was issued; else None."""
    data = load_auth()
    entry = data.get(ENDPOINT)
    if not (isinstance(entry, dict) and isinstance(entry.get("access_token"), str)):
        return None
    if not entry.get("expires_at") or time.time() < entry["expires_at"] - 60:
        return entry["access_token"]
    if not entry.get("refresh_token"):
        return None
    try:
        tok = http_json(entry["token_endpoint"], {"grant_type": "refresh_token", "refresh_token": entry["refresh_token"],
                                                  "client_id": entry["client_id"], "resource": entry.get("resource", ENDPOINT)}, form=True)
        data[ENDPOINT] = keep(entry, tok)
    except (Unavailable, KeyError):
        return None
    save_auth(data)
    return entry["access_token"]


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class Callback(http.server.BaseHTTPRequestHandler):
    """The loopback redirect: takes the one answer whose state matches, refuses everything else."""
    timeout = 5  # seconds a connection may sit idle, so a silent one (a browser preconnect, any local process) cannot hold sign-in

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        ok = u.path == "/callback" and q.get("state") == self.server.state
        if ok:
            self.server.answer = q
        text = ("Signed in to Zebra BI. You can close this tab." if ok and "code" in q else
                "Zebra BI sign-in did not finish. You can close this tab." if ok else "Not found.")
        body = ("<!doctype html><meta charset=utf-8><title>Zebra BI</title><p>%s</p>" % text).encode()
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def sign_in(open_url=webbrowser.open):
    """Sign in in the browser and keep the token for ENDPOINT. Exits with one line when it cannot."""
    if os.environ.get("ZEBRA_BI_MCP_URL"):
        print("Signing in to %s, which ZEBRA_BI_MCP_URL sets instead of the plugin's own address." % clean(ENDPOINT), flush=True)
    try:
        meta, resource = discover()
    except Unavailable as e:
        sys.exit("Zebra BI sign-in is unavailable (%s). Nothing was saved." % e)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Callback)
    srv.daemon_threads = True
    srv.timeout, srv.answer, srv.state = 1, None, secrets.token_urlsafe(24)
    redirect = "http://127.0.0.1:%d/callback" % srv.server_address[1]
    try:
        if not safe_url(meta.get("registration_endpoint")):
            sys.exit("Zebra BI sign-in is unavailable (%s offers no client registration). Nothing was saved." % clean(meta["issuer"]))
        try:
            reg = http_json(meta["registration_endpoint"], {
                "client_name": "Zebra BI story scripts", "redirect_uris": [redirect], "grant_types": ["authorization_code"],
                "response_types": ["code"], "token_endpoint_auth_method": "none"})
        except Unavailable as e:
            sys.exit("Zebra BI sign-in is unavailable (client registration: %s). Nothing was saved." % e)
        client_id = reg.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            sys.exit("Zebra BI sign-in is unavailable (client registration sent no client_id). Nothing was saved.")
        verifier = b64url(secrets.token_bytes(32))
        query = urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": srv.state,
            "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
            "resource": resource})
        url = meta["authorization_endpoint"] + ("&" if "?" in meta["authorization_endpoint"] else "?") + query
        print("Opening the Zebra BI sign-in page in your browser. If it does not open, visit:\n%s" % clean(url, 4000), flush=True)
        open_url(url)
        deadline = time.time() + SIGN_IN_WAIT
        while srv.answer is None and time.time() < deadline:
            srv.handle_request()
    finally:
        srv.server_close()
    answer = srv.answer
    if answer is None:
        sys.exit("Zebra BI sign-in timed out after %d s. Nothing was saved." % SIGN_IN_WAIT)
    if "error" in answer:
        sys.exit("Zebra BI sign-in was refused (%s). Nothing was saved." % clean(answer.get("error_description") or answer["error"], 300))
    if meta.get("authorization_response_iss_parameter_supported") is True and not answer.get("iss"):  # RFC 9207 section 2.4
        sys.exit("Zebra BI sign-in answered without naming its issuer. Nothing was saved.")
    if answer.get("iss") and answer["iss"] != meta["issuer"]:
        sys.exit("Zebra BI sign-in answered from an unexpected issuer (%s). Nothing was saved." % clean(answer["iss"]))
    if not answer.get("code"):
        sys.exit("Zebra BI sign-in sent no authorization code. Nothing was saved.")
    try:
        tok = http_json(meta["token_endpoint"], {
            "grant_type": "authorization_code", "code": answer["code"], "redirect_uri": redirect,
            "client_id": client_id, "code_verifier": verifier, "resource": resource}, form=True)
        entry = keep({"client_id": client_id, "token_endpoint": meta["token_endpoint"], "resource": resource,
                      "revocation_endpoint": meta.get("revocation_endpoint"), "issuer": meta["issuer"],
                      "signed_in_at": dt.datetime.now().isoformat(timespec="seconds")}, tok)
    except Unavailable as e:
        sys.exit("Zebra BI sign-in failed at the token step (%s). Nothing was saved." % e)
    data = load_auth()
    data[ENDPOINT] = entry
    save_auth(data)
    until = dt.datetime.fromtimestamp(entry["expires_at"]).isoformat(timespec="minutes") if entry["expires_at"] else "no stated expiry"
    print("Signed in to Zebra BI (%s). The token is kept in %s, valid until %s." % (ENDPOINT, AUTH, until))


def sign_out():
    """Forget every kept sign-in (an endpoint used before, such as an older address, included), revoking the
    refresh and access tokens first where the server offers that."""
    data = load_auth()
    if not data:
        print("No Zebra BI sign-in is kept in %s." % AUTH)
        return
    for endpoint, entry in data.items():
        if not isinstance(entry, dict):
            continue
        for kind in ("refresh_token", "access_token"):
            if safe_url(entry.get("revocation_endpoint")) and isinstance(entry.get(kind), str) and entry[kind]:
                try:  # RFC 7009: a 200 means revoked and the client ignores the body, which is often empty
                    http_json(entry["revocation_endpoint"], {"token": entry[kind], "token_type_hint": kind,
                                                             "client_id": entry.get("client_id", "")}, form=True, ignore_body=True)
                except Unavailable as e:
                    print("Could not revoke the %s for %s (%s); forgetting it anyway." % (kind.replace("_", " "), clean(endpoint), e),
                          file=sys.stderr)
        print("Signed out of Zebra BI (%s)." % clean(endpoint))
    save_auth({})
    print("Nothing is kept in %s any more." % AUTH)


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
    """Return (path, meta) of the cached renderer, fetching it when stale, changed or missing. A copy fetched from
    another endpoint (a test server, an older address) counts as missing, even when the fetch fails."""
    os.makedirs(os.path.dirname(CACHE), mode=0o700, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    path, meta_path = os.path.join(CACHE, "visual.html"), os.path.join(CACHE, "visual.json")
    meta = cached_renderer(path, meta_path)
    if meta and meta.get("endpoint") != ENDPOINT:
        meta = None
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
    """The cached tool result when visuals/<id>.json parses, its args_hash matches and it was drawn by ENDPOINT,
    else None (stale). An entry that names no endpoint (the bundled sample, older builds) is kept."""
    try:
        with open(out, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(cached, dict) and cached.get("args_hash") == h and isinstance(cached.get("result"), dict) \
            and cached.get("endpoint", ENDPOINT) == ENDPOINT:
        return cached["result"]
    return None


def render_visuals(story_dir, story):
    vdir = os.path.join(story_dir, "visuals")
    if os.path.islink(vdir):
        sys.exit("%s is a symbolic link; a build writes only into a real folder. Nothing was built." % vdir)
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
        write_atomic(os.path.join(vdir, vid + ".json"), json.dumps({"args_hash": args_hash(args), "endpoint": ENDPOINT, "result": res}))
        results[vid] = res
        text = " ".join(str(c.get("text", "")) for c in content if c.get("type") == "text")
        print("[%s] %s" % (vid, clean(text)))
    return results


MAX_CELLS = 3  # cells side by side in one row, as in the report builder


def load(story_dir):
    """story.json in story_dir, or the report builder's report there (report.zbreport) as a story with rows."""
    path = os.path.join(story_dir, "story.json")
    report_file = os.path.join(story_dir, "report.zbreport")
    if not os.path.exists(path) and os.path.isfile(report_file):
        import report as R  # report.py imports this module, so it is imported only when needed
        return R.to_story(R.load(report_file))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        sys.exit("No story.json or report.zbreport in %s" % story_dir)


def rows(story):
    """The story's rows ([{"cells": [{"visual": id} | {"text": str}]}]), checked: 1 to MAX_CELLS cells each."""
    out = story.get("rows") or []
    for i, row in enumerate(out, 1):
        cells = row.get("cells") if isinstance(row, dict) else None
        if not isinstance(cells, list) or not 1 <= len(cells) <= MAX_CELLS:
            sys.exit("row %d needs 1 to %d cells side by side" % (i, MAX_CELLS))
        for c in cells:
            if not isinstance(c, dict) or not (c.get("visual") or isinstance(c.get("text"), str)) \
                    or not isinstance(c.get("note", ""), str):
                sys.exit("row %d: each cell is {\"visual\": id} or {\"text\": ...}" % i)
    return out


def story_visuals(story):
    """The visual ids the story shows, in page order, each once."""
    ids = [s["visual"] for s in story.get("sections", []) if s.get("visual")]
    ids += [c["visual"] for row in rows(story) for c in row["cells"] if c.get("visual")]
    return list(dict.fromkeys(ids))


def footer(story, brand):
    """The footer line, escaped: the source when the story names one, the build date, the brand's credit."""
    src = story.get("source") or {}
    label = src.get("label") or src.get("name")
    text = ("Source: %s. " % html.escape(label) if label else "") + "Built %s." % dt.date.today().isoformat()
    if brand.get("credit"):
        text += " &middot; " + html.escape(brand["credit"])
    return text


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
    for row in rows(story):
        cells = []
        for c in row["cells"]:
            vid = c.get("visual")
            if vid:
                if vid not in results:
                    sys.exit("a row names visual %r, which is not in visuals" % vid)
                title = ((story.get("visuals", {}).get(vid) or {}).get("args") or {}).get("title") or vid
                note = '<div class="note">%s</div>' % para(c["note"]) if c.get("note") else ""
                cells.append('<div class="cell"><iframe data-v="%s" title="%s"></iframe>%s</div>' % (e(vid), e(title), note))
            else:
                cells.append('<div class="cell">%s</div>' % para(c["text"]))
        sections.append('<section class="row cols-%d">%s</section>' % (len(cells), "".join(cells)))
    nxt = ""
    if story.get("next"):
        nxt = '<section class="next"><h2>%s</h2><ul>%s</ul></section>' % (
            e(story.get("next_heading", "Where to look next")),
            "".join("<li>%s</li>" % re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(n)) for n in story["next"]))
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
             "SECTIONS": "\n".join(sections), "NEXT": nxt, "FOOTER": footer(story, brand), "VERSION": version(),
             "RENDERER_SHA": meta["sha256"], "VISUALS_JSON": embed(results), "RENDERER_JSON": embed(renderer_html)}
    fills.update(brand_tokens(brand, story))
    # One pass over the template: a value that holds "{{...}}" is never expanded again.
    page = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: fills[m.group(1)], page)
    return page


def main(argv):
    if argv[:1] == ["sign-in"]:
        return sign_in()
    if argv[:1] == ["sign-out"]:
        return sign_out()
    if not argv or argv[0] != "renderer":
        sys.exit(__doc__)
    path, meta = renderer("--refresh" in argv)
    print(json.dumps({"path": path, **meta}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
