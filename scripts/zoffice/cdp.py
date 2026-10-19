"""A minimal Chrome DevTools Protocol client over a loopback WebSocket, standard library only.

Enough for snapshots: launch headless Chrome on a private profile, open one page target, send
commands and read their results. Events are skipped.
"""
import base64, json, os, signal, socket, struct, subprocess, time, urllib.request

POSIX = os.name == "posix"


def _read(path):
    with open(path) as f:
        return f.read()


class Chrome:
    def __init__(self, binary, profile, timeout=45):
        # A slow or busy machine can miss the first start, so there is one retry in a fresh profile next to it.
        for attempt, prof in enumerate((profile, profile + "-retry")):
            self.proc = subprocess.Popen(
                [binary, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                 "--remote-debugging-port=0", "--remote-allow-origins=http://127.0.0.1", "--user-data-dir=" + prof, "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=POSIX)  # its own process group, so close() can stop the helpers too
            port_file = os.path.join(prof, "DevToolsActivePort")
            deadline = time.time() + timeout
            while not (os.path.exists(port_file) and _read(port_file).strip()):
                if time.time() > deadline or self.proc.poll() is not None:
                    break
                time.sleep(0.1)
            else:
                break
            self.close()
            if attempt:
                raise RuntimeError("headless Chrome did not start")
        self.port = int(_read(port_file).split()[0])
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % self.port, timeout=10) as r:
            targets = json.load(r)
        page = next(t for t in targets if t.get("type") == "page")
        path = page["webSocketDebuggerUrl"].split(str(self.port), 1)[1]
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nOrigin: http://127.0.0.1\r\n\r\n"
                           % (path, self.port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise RuntimeError("DevTools closed the connection during the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("DevTools refused the WebSocket: %s" % head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.next_id = 0

    def _send(self, text):
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

    def call(self, method, **params):
        self.next_id += 1
        mid = self.next_id
        self._send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self._recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})

    def evaluate(self, expr):
        return self.call("Runtime.evaluate", expression=expr, returnByValue=True).get("result", {}).get("value")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass
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
