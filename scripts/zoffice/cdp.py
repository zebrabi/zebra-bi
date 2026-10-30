"""A minimal Chrome DevTools Protocol client, standard library only.

Enough for snapshots: launch headless Chrome on a private profile, attach to its one page, send
commands and read their results. Events are skipped. On macOS and Linux the protocol runs over a
pipe (--remote-debugging-pipe), so no other process or user on the machine can reach the browser;
on Windows it runs over a WebSocket on a loopback port.
"""
import base64, json, os, select, signal, socket, struct, subprocess, sys, time, urllib.request

POSIX = os.name == "posix"
if POSIX:
    import fcntl

# A throwaway headless browser has no reason to call home: no component updates, sync, metrics upload,
# phishing lists or other background traffic. Pages still load what they ask for (a brand's web font).
QUIET = ["--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-domain-reliability",
         "--disable-client-side-phishing-detection", "--disable-default-apps", "--metrics-recording-only", "--no-pings"]
FLAGS = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check"] + QUIET


def _read(path):
    with open(path) as f:
        return f.read()


class Chrome:
    def __init__(self, binary, profile, timeout=45):
        self.timeout, self.next_id, self.session = timeout, 0, None
        self.sock = self.rfd = self.wfd = self.proc = None
        self.buf = b""
        # A slow or busy machine can miss the first start, so there is one retry in a fresh profile next to it.
        for attempt, prof in enumerate((profile, profile + "-retry")):
            try:
                if POSIX:
                    self._start_pipe(binary, prof)
                else:
                    self._start_port(binary, prof)
                break
            except RuntimeError:
                self.close()
                self.buf, self.session = b"", None
                if attempt:
                    raise RuntimeError("headless Chrome did not start")

    def _start_pipe(self, binary, prof):
        cmd_r, cmd_w = os.pipe()  # commands: Chrome reads them on fd 3
        ans_r, ans_w = os.pipe()  # answers: Chrome writes them on fd 4
        # Chrome wants the pipe on fds 3 and 4 exactly. A fresh Python process (no threads, so dup2 is safe
        # there) moves the two ends onto them and execs Chrome; dash, Linux's sh, cannot name fds above 9.
        # Both ends are first moved above 9, so neither dup2 can overwrite the other.
        hi = [fcntl.fcntl(cmd_r, fcntl.F_DUPFD, 10), fcntl.fcntl(ans_w, fcntl.F_DUPFD, 10)]
        os.close(cmd_r)
        os.close(ans_w)
        self.wfd, self.rfd = cmd_w, ans_r
        launch = ("import os, sys; r, w = int(sys.argv[1]), int(sys.argv[2]); os.dup2(r, 3); os.dup2(w, 4); "
                  "os.close(r); os.close(w); os.execv(sys.argv[3], sys.argv[3:])")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-I", "-c", launch, str(hi[0]), str(hi[1]), binary]
                + FLAGS + ["--remote-debugging-pipe", "--user-data-dir=" + prof, "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=hi,
                start_new_session=True)  # its own process group, so close() can stop the helpers too
        finally:
            for fd in hi:
                os.close(fd)
        deadline = time.time() + self.timeout
        while True:  # the browser answers once it is up; its first page can follow a moment later
            pages = [t for t in self._call("Target.getTargets", {}, None).get("targetInfos", []) if t.get("type") == "page"]
            if pages:
                break
            if time.time() > deadline:
                raise RuntimeError("headless Chrome opened no page")
            time.sleep(0.1)
        self.session = self._call("Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True}, None)["sessionId"]

    def _start_port(self, binary, prof):
        self.proc = subprocess.Popen(
            [binary] + FLAGS + ["--remote-debugging-port=0", "--remote-allow-origins=http://127.0.0.1", "--user-data-dir=" + prof,
                                "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = os.path.join(prof, "DevToolsActivePort")
        deadline = time.time() + self.timeout
        while not (os.path.exists(port_file) and _read(port_file).strip()):
            if time.time() > deadline or self.proc.poll() is not None:
                raise RuntimeError("headless Chrome did not start")
            time.sleep(0.1)
        port = int(_read(port_file).split()[0])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback: never through a proxy
        with opener.open("http://127.0.0.1:%d/json/list" % port, timeout=10) as r:
            targets = json.load(r)
        page = next(t for t in targets if t.get("type") == "page")
        path = page["webSocketDebuggerUrl"].split(str(port), 1)[1]
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nOrigin: http://127.0.0.1\r\n\r\n"
                           % (path, port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise RuntimeError("DevTools closed the connection during the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("DevTools refused the WebSocket: %s" % head.split(b"\r\n", 1)[0].decode(errors="replace"))

    def _send(self, text):
        if self.sock is None:  # pipe: one NUL-terminated JSON message
            data = text.encode() + b"\0"
            try:
                while data:
                    data = data[os.write(self.wfd, data):]
            except OSError as e:
                raise RuntimeError("DevTools closed the connection (%s)" % e)
            return
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                               bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                               bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(min(1 << 20, n - len(buf)))
            if not chunk:
                raise RuntimeError("DevTools closed the connection")
            buf += chunk
        return bytes(buf)

    def _recv(self):
        if self.sock is None:  # pipe: read up to the next NUL, waiting at most timeout seconds
            deadline = time.time() + self.timeout
            while b"\0" not in self.buf:
                left = deadline - time.time()
                if left <= 0 or not select.select([self.rfd], [], [], left)[0]:
                    raise RuntimeError("DevTools did not answer within %d s" % self.timeout)
                chunk = os.read(self.rfd, 1 << 20)
                if not chunk:
                    raise RuntimeError("DevTools closed the connection")
                self.buf += chunk
            msg, _, self.buf = self.buf.partition(b"\0")
            return msg.decode()
        parts = []
        while True:
            b0, b1 = self._exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            payload = self._exact(n)
            op = b0 & 0x0F
            if op == 0x8:
                raise RuntimeError("DevTools closed the connection")
            if op in (0x9, 0xA):  # ping/pong: ignore
                continue
            parts.append(payload)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def _call(self, method, params, session):
        self.next_id += 1
        mid = self.next_id
        msg = {"id": mid, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        self._send(json.dumps(msg))
        while True:
            msg = json.loads(self._recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})

    def call(self, method, **params):
        return self._call(method, params, self.session)

    def evaluate(self, expr):
        return self.call("Runtime.evaluate", expression=expr, returnByValue=True).get("result", {}).get("value")

    def close(self):
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        for fd in (self.wfd, self.rfd):  # closing the command pipe also tells Chrome to quit
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.sock = self.wfd = self.rfd = None
        if self.proc is None:
            return
        try:
            if POSIX:
                os.killpg(self.proc.pid, signal.SIGTERM)
            else:
                self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        if POSIX:
            # Chrome's helper processes can outlive the browser for a moment and keep writing into the profile,
            # so a caller deleting it right away fails ("Directory not empty"). Wait for the whole group.
            deadline = time.time() + 5
            while True:
                try:
                    os.killpg(self.proc.pid, 0 if time.time() < deadline else signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    break
                if time.time() >= deadline:
                    break
                time.sleep(0.05)
