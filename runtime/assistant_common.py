# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""What the backends of the assistant slot share (claude_backend.py, codex_backend.py): the log line, the WebSocket server side (RFC 6455) of the chat protocol of
the contract (docs/assistant-contract.md, section 4), and the configuration the CLIs get from the system: the MCP servers (OS Beroun, the apps) and the apps skills.

Standard library only."""
import json, os, re, struct, sys, threading, time

SHELL_PORT = os.environ.get("INTELLICAT_PORT", "8765")
CAROLINE_PORT = os.environ.get("CAROLINE_PORT", str(48765 + int(os.environ.get("INTELLICAT_PORT_OFFSET") or 0)))   # her backend (runtime/caroline.py), the host of her tools
SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(SIM, "beroun-tools")


def log(text):
    print(time.strftime("%H:%M:%S ") + text, flush=True)


# ---- WebSocket (the server's side; RFC 6455) ---------------------------------------------------------------------------
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WS:
    def __init__(self, sock):
        self.sock, self.lock, self.open = sock, threading.Lock(), True

    def send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n))
        with self.lock:
            try:
                self.sock.sendall(head + data)
            except OSError:
                self.open = False

    def _exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def recv(self):
        """The next text message (a str), or None when the connection is over."""
        data = b""
        while True:
            try:
                b1, b2 = self._exact(2)
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack(">H", self._exact(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self._exact(8))[0]
                mask = self._exact(4) if b2 & 0x80 else b"\0\0\0\0"
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(self._exact(n)))
            except (OSError, ConnectionError):
                self.open = False
                return None
            op = b1 & 0x0F
            if op == 8:
                self.open = False
                return None
            if op == 9:
                with self.lock:
                    self.sock.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if op in (0, 1):
                data += payload
                if b1 & 0x80:
                    return data.decode("utf-8", "replace")



# ---- the configuration of the CLI: its MCP servers and its skills -------------------------------------------------------
def _shell(path, body=None, timeout=5):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{SHELL_PORT}{path}", data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def mcp_config(tab="1"):
    """The MCP servers of a new conversation (its tab): OS Beroun's own (the one table), Caroline's own tools from her backend (caroline-tools: she hosts Communicator
    in every build; beroun_tools/bridge_server.py) and the apps' (app-<id>, app-<id>-<server> when an app has several)."""
    servers = {"beroun": {"type": "stdio", "command": sys.executable, "args": ["-m", "beroun_tools.mcp_server"],
                          "env": {"PYTHONPATH": TOOLS_DIR, "INTELLICAT_PORT": SHELL_PORT}},
               "caroline-tools": {"type": "stdio", "command": sys.executable, "args": ["-m", "beroun_tools.bridge_server"],
                                  "env": {"PYTHONPATH": TOOLS_DIR, "CAROLINE_PORT": CAROLINE_PORT, "INTELLICAT_ASSISTANT_TAB": str(tab)}}}
    try:
        apps = _shell("/api/assistant/apps")
    except Exception as e:
        log(f"the apps' servers are not known now ({e}); only OS Beroun's")
        apps = []
    for a in apps:
        specs = a.get("mcp") or []
        for spec in specs:
            name = f"app-{a['id']}" if len(specs) == 1 else f"app-{a['id']}-{spec['id']}"
            fill = lambda t: str(t).replace("{app}", a["folder"]).replace("{port}", SHELL_PORT)   # noqa: E731
            if spec.get("service"):
                try:
                    _shell("/api/assistant/app/prepare", {"app": a["id"]})   # the app's service has to run for its server to answer
                except Exception:
                    pass
            if spec.get("transport") == "http":
                servers[name] = {"type": "http", "url": fill(spec["url"])}
            else:
                cmd = [fill(c) for c in spec["cmd"]]
                cmd[0] = sys.executable if cmd[0] in ("python3", "python") else cmd[0]
                servers[name] = {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {k: fill(v) for k, v in (spec.get("env") or {}).items()}}
    return {"mcpServers": servers}, apps


def browser_command(data_dir):
    """The program a CLI's sign-in calls as its browser (BROWSER): <data_dir>/browser, which hands the address to the device browser (runtime/browser_open.py).
    The page then comes back to the CLI's own callback on 127.0.0.1 of this machine, so the owner types nothing here."""
    path = os.path.join(data_dir, "browser")
    os.makedirs(data_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write(f"#!/bin/sh\nexec {sys.executable} {os.path.join(SIM, 'runtime', 'browser_open.py')} \"$@\"\n")
    os.chmod(path, 0o755)
    return path


def link_skills(apps, workspace, sub=(".claude", "skills")):
    """The apps' skills as Claude's: <workspace>/.claude/skills/<name> -> the skill's folder (a folder with a SKILL.md), made anew for each conversation."""
    root = os.path.join(workspace, *sub)
    os.makedirs(root, exist_ok=True)
    for old in os.listdir(root):
        p = os.path.join(root, old)
        if os.path.islink(p):
            os.unlink(p)
    for a in apps:
        for folder in a.get("skills") or []:
            for d, _dirs, files in os.walk(folder):
                if "SKILL.md" in files:
                    name = re.sub(r"[^a-z0-9-]+", "-", os.path.basename(d).lower()).strip("-") or a["id"]
                    target = os.path.join(root, name)
                    if not os.path.lexists(target):
                        os.symlink(d, target)
