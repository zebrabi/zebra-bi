#!/usr/bin/env python3
"""Dev host: serves a page that mounts the report builder like an MCP host, relaying JSON-RPC to report_server.py.

  ZEBRA_BI_REPORTS=<a scratch copy> CLAUDE_PLUGIN_DATA=<a scratch folder> python3 devhost.py <report_server.py> <port>

It refuses to start without both, so it never edits the real reports folder or the real Zebra BI sign-in. Copy the
renderer into the scratch folder (renderer/visual.html and visual.json from ~/.cache/zebra-bi) so cached charts draw;
keep auth.json out of it, so the server never asks Zebra BI for anything. Its /rpc and /fake answer only its own page
(any other Origin is refused), so another site open in the browser cannot drive it.

The Zebra BI sign-in is faked, never run: start_sign_in and sign_out are answered here and never reach the server.
POST /fake sets the fake, one or more of:
  {"auth": {...} | null}   the auth block every answer carries (null: the server's own)
  {"hide": "<slug>"}       move the report's drawn charts aside, so the builder shows ghosts ("only": [ids], some)
  {"refuse": {...} | null} the refusal the next draw_chart meets (it becomes the auth block's refusal)
  {"delay": ms}            how long draw_chart takes, so the charts arrive one by one
  {"links": []}            clear the links the page asked the host to open (the page reads them as top.links)
draw_chart, while the fake auth is signed in, puts the hidden chart back and lets the server draw it from its cache.

/card#<render_visual arguments, URI-encoded JSON> calls show_chart with them and mounts the chart card as a chat
would, 720 px wide. chart_card, while the fake auth is signed in, puts a result stashed for that chart as
<stash>/chat/<chart id>.json in place and lets the server draw it from its cache.
"""
import copy, glob, http.server, json, os, re, shutil, subprocess, sys, threading, time

REPORTS, DATA = os.environ.get("ZEBRA_BI_REPORTS"), os.environ.get("CLAUDE_PLUGIN_DATA")
if not REPORTS or not DATA:
    sys.exit("Set ZEBRA_BI_REPORTS to a scratch copy of the reports and CLAUDE_PLUGIN_DATA to a scratch folder; "
             "the dev host never runs on the real reports or the real Zebra BI sign-in.")
if os.path.exists(os.path.join(DATA, "auth.json")):
    sys.exit("%s holds a Zebra BI sign-in; the dev host fakes sign-in and wants a folder without one." % DATA)
SERVER, PORT = sys.argv[1], int(sys.argv[2])
STASH = os.path.join(os.path.dirname(os.path.abspath(REPORTS)), "devhost-stash")
ORIGINS = ("http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT)
proc = subprocess.Popen([sys.executable, "-I", SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
lock = threading.Lock()
n = [0]
OUT = {"signed_in": False, "state": "idle", "url": None, "refusal": None}
FAKE = {"auth": None, "refuse": None, "delay": 0}
SIGN_IN_URL = "https://sign-in.example.invalid/authorize?devhost=1"


def rpc(method, params):
    with lock:
        n[0] += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())


def result(text, structured, error=False):
    return {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": text}], "structuredContent": structured,
                                         "isError": error}}


def hide(slug, only=None):
    """Move the report's drawn charts (or only those named) to the stash, so the server has them as not drawn yet."""
    if not re.fullmatch(r"[a-z0-9-]{1,60}", slug):
        raise ValueError("not a report: %r" % slug)
    folder = os.path.join(os.path.abspath(REPORTS), slug, "visuals")
    os.makedirs(os.path.join(STASH, slug), exist_ok=True)
    for f in glob.glob(os.path.join(folder, "*.json")):
        if only is not None and os.path.basename(f)[:-5] not in only:
            continue
        shutil.move(f, os.path.join(STASH, slug, os.path.basename(f)))


def unhide(slug, vid):
    if not (isinstance(slug, str) and re.fullmatch(r"[a-z0-9-]{1,60}", slug) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", vid or "")):
        return False
    src = os.path.join(STASH, slug, vid + ".json")
    if not os.path.isfile(src):
        return False
    os.makedirs(os.path.join(os.path.abspath(REPORTS), slug, "visuals"), exist_ok=True)
    shutil.move(src, os.path.join(os.path.abspath(REPORTS), slug, "visuals", vid + ".json"))
    return True


def faked(method, params):
    """The answer with the fake applied, or the server's own when the fake is off."""
    name = params.get("name") if method == "tools/call" else None
    args = params.get("arguments") or {}
    a = FAKE["auth"]
    if name == "start_sign_in":  # never the real sign-in
        FAKE["auth"] = a = dict(a or OUT, state="waiting", url=SIGN_IN_URL)
        return result("sign-in", {"url": SIGN_IN_URL, "opened": False, "auth": a})
    if name == "sign_out":
        FAKE["auth"] = a = dict(OUT)
        return result("signed out", {"signed_out": 1, "auth": a})
    if a is None:
        return rpc(method, params)
    if name == "get_renderer" and not a["signed_in"]:
        return result("The Zebra BI renderer comes with sign-in to Zebra BI.", {"needs_sign_in": True, "auth": a}, True)
    if name == "chart_card":
        first = rpc(method, params)
        sc = first.get("result", {}).get("structuredContent") or {}
        cid = sc.get("chart") or ""
        src = os.path.join(STASH, "chat", cid + ".json")
        if (not sc.get("drawn") and a["signed_in"] and not a["refusal"] and a["state"] != "waiting"
                and re.fullmatch(r"[0-9a-f]{16}", cid) and os.path.isfile(src)):
            dst = os.path.join(os.path.abspath(REPORTS), ".chat-charts", cid, "visuals")
            os.makedirs(dst, exist_ok=True)
            shutil.move(src, os.path.join(dst, "chart.json"))
            out = rpc(method, params)
        else:
            out = first
    elif name == "draw_chart":
        time.sleep(FAKE["delay"] / 1000.0)
        first = rpc(method, params)
        sc = first.get("result", {}).get("structuredContent") or {}
        if sc.get("drawn") or "visual" not in sc:
            out = first
        elif not a["signed_in"] or a["refusal"] or a["state"] == "waiting":
            out = first
        elif FAKE["refuse"]:
            FAKE["auth"] = a = dict(a, refusal=FAKE["refuse"], signed_in=FAKE["refuse"]["kind"] != "expired")
            FAKE["refuse"] = None
            out = first
        elif unhide(args.get("report"), sc["visual"]):
            out = rpc(method, params)
        else:
            out = first
    else:
        out = rpc(method, params)
    sc = (out.get("result") or {}).get("structuredContent")
    if isinstance(sc, dict) and "auth" in sc:
        sc["auth"] = copy.deepcopy(FAKE["auth"])
    return out


rpc("initialize", {"protocolVersion": "2025-06-18"})

PAGE = """<!doctype html><meta charset="utf-8"><title>dev host</title>
<body style="margin:0;background:#ddd"><iframe id="a" sandbox="allow-scripts allow-same-origin" style="width:100%;height:100vh;border:0;background:#fff"></iframe>
<script>
const a=document.getElementById('a'); window.links=[];
const rpc=(method,params)=>fetch('/rpc',{method:'POST',body:JSON.stringify({method,params})}).then(r=>r.json());
rpc('resources/list',{}).then(l=>rpc('resources/read',{uri:l.result.resources[0].uri})).then(r=>{ a.srcdoc=r.result.contents[0].text; });
window.addEventListener('message',e=>{ if(e.source!==a.contentWindow) return; const m=e.data; if(!m||m.jsonrpc!=='2.0') return;
  const reply=(r)=>a.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:r},'*');
  if(m.method==='ui/initialize') reply({protocolVersion:'2026-01-26',hostInfo:{name:'devhost'},hostCapabilities:{serverTools:{},openLinks:{}},hostContext:{displayMode:'fullscreen',theme:'light'}});
  else if(m.method==='ui/open-link') { window.links.push(m.params&&m.params.url); reply({}); }
  else if(m.method==='tools/call') rpc('tools/call',m.params).then(r=>a.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:r.result,error:r.error},'*'));
  else if(m.id!=null&&m.method) reply({});
});
</script>"""


CARD = """<!doctype html><meta charset="utf-8"><title>dev host: chart card</title>
<body style="margin:0;background:#ddd;padding:24px"><iframe id="a" sandbox="allow-scripts allow-same-origin" style="width:720px;height:200px;border:0;background:#fff;display:block"></iframe>
<script>
const a=document.getElementById('a'); window.links=[]; let shown=null;
const rpc=(method,params)=>fetch('/rpc',{method:'POST',body:JSON.stringify({method,params})}).then(r=>r.json());
const chart=JSON.parse(decodeURIComponent(location.hash.slice(1)));
rpc('tools/call',{name:'show_chart',arguments:{chart}}).then(r=>{ shown=r.result; window.said=r.result;
  return rpc('resources/list',{}); }).then(l=>rpc('resources/read',{uri:l.result.resources[1].uri})).then(r=>{ a.srcdoc=r.result.contents[0].text; });
window.addEventListener('message',e=>{ if(e.source!==a.contentWindow) return; const m=e.data; if(!m||m.jsonrpc!=='2.0') return;
  const reply=(r)=>a.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:r},'*');
  if(m.method==='ui/initialize') { reply({protocolVersion:'2026-01-26',hostInfo:{name:'devhost'},hostCapabilities:{serverTools:{},openLinks:{}},hostContext:{displayMode:'inline',theme:'light'}});
    setTimeout(()=>a.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:shown},'*'),30); }
  else if(m.method==='ui/notifications/size-changed') a.style.height=Math.ceil(m.params.height)+'px';
  else if(m.method==='ui/open-link') { window.links.push(m.params&&m.params.url); reply({}); }
  else if(m.method==='tools/call') rpc('tools/call',m.params).then(r=>a.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:r.result,error:r.error},'*'));
  else if(m.id!=null&&m.method) reply({});
});
</script>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((CARD if self.path.split("#")[0].split("?")[0] == "/card" else PAGE).encode())

    def do_POST(self):
        if self.headers.get("Origin") not in ORIGINS or self.path not in ("/rpc", "/fake"):
            self.send_response(403)
            self.end_headers()
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/fake":
            with lock:
                if "auth" in body:
                    FAKE["auth"] = None if body["auth"] is None else dict(OUT, **body["auth"])
                for k in ("refuse", "delay"):
                    if k in body:
                        FAKE[k] = body[k]
            if body.get("hide"):
                hide(body["hide"], body.get("only"))
            out = json.dumps(FAKE).encode()
        else:
            out = json.dumps(faked(body["method"], body.get("params") or {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
