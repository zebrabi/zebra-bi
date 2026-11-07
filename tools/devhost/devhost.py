#!/usr/bin/env python3
"""Dev host: serves a page that mounts the report builder like an MCP host, relaying JSON-RPC to report_server.py.

  python3 devhost.py <report_server.py> <port>
"""
import http.server, json, subprocess, sys, threading

SERVER, PORT = sys.argv[1], int(sys.argv[2])
proc = subprocess.Popen([sys.executable, "-I", SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
lock = threading.Lock()
n = [0]


def rpc(method, params):
    with lock:
        n[0] += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())


rpc("initialize", {"protocolVersion": "2025-06-18"})

PAGE = """<!doctype html><meta charset="utf-8"><title>dev host</title>
<body style="margin:0;background:#ddd"><iframe id="a" sandbox="allow-scripts allow-same-origin" style="width:100%;height:100vh;border:0;background:#fff"></iframe>
<script>
const a=document.getElementById('a');
const rpc=(method,params)=>fetch('/rpc',{method:'POST',body:JSON.stringify({method,params})}).then(r=>r.json());
rpc('resources/list',{}).then(l=>rpc('resources/read',{uri:l.result.resources[0].uri})).then(r=>{ a.srcdoc=r.result.contents[0].text; });
window.addEventListener('message',e=>{ if(e.source!==a.contentWindow) return; const m=e.data; if(!m||m.jsonrpc!=='2.0') return;
  const reply=(r)=>a.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:r},'*');
  if(m.method==='ui/initialize') reply({protocolVersion:'2026-01-26',hostInfo:{name:'devhost'},hostCapabilities:{serverTools:{}},hostContext:{displayMode:'fullscreen',theme:'light'}});
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
        self.wfile.write(PAGE.encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps(rpc(body["method"], body.get("params") or {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
