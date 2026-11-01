# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Claude's backend as the provider of the assistant slot (docs/assistant-contract.md): a service of its own, started and watched by runtime/claude_assistant.py.

    python3 claude_backend.py        (the environment says where: CLAUDE_BACKEND_PORT, CLAUDE_DATA, CLAUDE_CLI, INTELLICAT_PORT)

It speaks the chat protocol of the contract (section 4) on ws://127.0.0.1:<port>/?tab=<id>: {"type": "user_message", "text"} and {"type": "interrupt"} in,
{"type": "sdk_message", "message": <event>} out, plus {"type": "history"} on connect and {"type": "busy"}. A tab is one conversation: one Claude Code CLI process
(claude -p, stream-json in and out), whose events are the Claude Agent SDK's, so they go out as they come. The session id of a tab is kept, so a conversation goes on
across the end of its process (an idle one is ended to free the memory) and across restarts of this backend (claude --resume).

What Claude may use: the OS Beroun MCP server (beroun-tools, the same tools Caroline has), Caroline's own tools from her backend (caroline-tools), the apps' own MCP
servers (app-<id>), web search and fetch, and the apps' skills (linked into its workspace). Nothing of the machine underneath: no shell, no files of its own (the CLI's built-in tools are off but those three).

Its account is the owner's Claude Code login (the owner's decision of 2026-10-31): /api/auth/* runs `claude auth login` on a terminal of its own, hands the sign-in
link to the wizard (a QR code on the screen) and the code the owner brings back to the CLI. The credentials stay in its own config folder.

Standard library only."""
import base64, hashlib, json, os, pty, re, select, signal, struct, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qr
from assistant_common import GUID, SHELL_PORT, TOOLS_DIR, WS, browser_command, log, mcp_config, link_skills

PORT = int(os.environ.get("CLAUDE_BACKEND_PORT", "48775"))
DATA = os.environ.get("CLAUDE_DATA", os.path.expanduser("~/.intellicat/data/claude"))
CLI = os.environ.get("CLAUDE_CLI", os.path.expanduser("~/.intellicat/claude-runtime/node_modules/.bin/claude"))
CONFIG = os.path.join(DATA, "config")         # CLAUDE_CONFIG_DIR: its login and its own state
WORKSPACE = os.path.join(DATA, "workspace")   # the CLI's working folder: the skills (.claude/skills) and the MCP config
TABS = os.path.join(DATA, "tabs")             # per tab: <tab>.json (its session id), <tab>.jsonl (its events, the history the chat view shows)
MODEL = os.environ.get("CLAUDE_MODEL", "")    # empty: the CLI's default for the account
IDLE_S = int(os.environ.get("CLAUDE_IDLE_S", "300"))   # a conversation's process ends after this long without a turn (it is resumed by the next message)
MAX_LIVE = int(os.environ.get("CLAUDE_MAX_LIVE", "3"))  # processes at once at most (each is a Node program of ~200 MB): the idlest goes first
HISTORY_MAX = 400                              # events of a tab kept on disk and sent on connect
TOOLS = "WebSearch,WebFetch,Skill"             # the CLI's built-in tools that stay on

SYSTEM_PROMPT = """You are Claude, the assistant of OS Beroun on this device: the manager of the system, not a guest. OS Beroun is the operating system of IntelliCat,
a small handheld console (a 640x480 screen, a cursor pad, A to confirm, B to go back). The owner talks to you in the chat on that screen: answer briefly and plainly,
the screen is small. You act on the device only through your tools: the group beroun (the apps, any app's window, the files of the memory card, settings, the clock,
the network, notifications, a message box, sounds, vibration, the store), the agents of the apps (agent_*), one group per app (<app>_*, and the app's own servers
mcp__app-<id>__*), and Caroline's own tools (mcp__caroline-tools__*: the messages and the messengers, mail, your memory across conversations, reminders, calendar,
contacts, notes, images, the device browser; she runs on the device as the host of Communicator). Read the servers' instructions for how to use them. Ask the owner
before anything that sends, deletes, buys or changes the owner's settings."""


def cli_env():
    env = dict(os.environ, CLAUDE_CONFIG_DIR=CONFIG, DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", INTELLICAT_PORT=SHELL_PORT)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):   # the owner's login is the account, never a key found in the environment
        env.pop(k, None)
    return env


# ---- the conversations ----------------------------------------------------------------------------------------------------
class Tab:
    def __init__(self, tid):
        self.id, self.proc, self.clients, self.busy, self.last = tid, None, [], False, time.time()
        self.lock = threading.Lock()
        self.file = os.path.join(TABS, re.sub(r"[^A-Za-z0-9_-]", "_", tid))
        try:
            self.session = json.load(open(self.file + ".json")).get("session")
        except (OSError, ValueError):
            self.session = None

    def history(self):
        try:
            lines = open(self.file + ".jsonl", encoding="utf-8").read().splitlines()[-HISTORY_MAX:]
            return [json.loads(x) for x in lines if x.strip()]
        except (OSError, ValueError):
            return []

    def _keep(self, evt):
        os.makedirs(TABS, exist_ok=True)
        with open(self.file + ".jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        if os.path.getsize(self.file + ".jsonl") > 2_000_000:   # the older half goes
            lines = open(self.file + ".jsonl", encoding="utf-8").read().splitlines()
            with open(self.file + ".jsonl", "w", encoding="utf-8") as f:
                f.write("\n".join(lines[len(lines) // 2:]) + "\n")

    def broadcast(self, msg):
        for c in list(self.clients):
            c.send(msg)
            if not c.open:
                self.clients.remove(c)

    def emit(self, evt, keep=True):
        if keep:
            self._keep(evt)
        self.broadcast({"type": "sdk_message", "message": evt})

    def set_busy(self, busy):
        self.busy = busy
        self.broadcast({"type": "busy", "busy": busy})

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _spawn(self):
        cfg, apps = mcp_config(self.id)
        os.makedirs(WORKSPACE, exist_ok=True)
        link_skills(apps, WORKSPACE)
        cfg_file = os.path.join(WORKSPACE, f"mcp-{os.getpid()}-{re.sub(r'[^A-Za-z0-9_-]', '_', self.id)}.json")
        json.dump(cfg, open(cfg_file, "w"))
        allowed = ["mcp__" + n for n in cfg["mcpServers"]] + TOOLS.split(",")
        cmd = [CLI, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--mcp-config", cfg_file, "--strict-mcp-config", "--tools", TOOLS, "--allowedTools", *allowed,
               "--permission-mode", "dontAsk", "--setting-sources", "project", "--append-system-prompt", SYSTEM_PROMPT]
        if MODEL:
            cmd += ["--model", MODEL]
        if self.session:
            cmd += ["--resume", self.session]
        self.proc = subprocess.Popen(cmd, cwd=WORKSPACE, env=cli_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.path.join(DATA, "cli.log"), "ab"),
                                     start_new_session=True)
        log(f"tab {self.id}: the CLI started, pid {self.proc.pid}" + (f", resuming {self.session}" if self.session else ""))
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name=f"claude-{self.id}").start()

    def _read(self, proc):
        for raw in proc.stdout:
            try:
                evt = json.loads(raw)
            except ValueError:
                continue
            if evt.get("type") == "system" and evt.get("subtype") == "init" and evt.get("session_id"):
                if evt["session_id"] != self.session:
                    self.session = evt["session_id"]
                    os.makedirs(TABS, exist_ok=True)
                    json.dump({"session": self.session}, open(self.file + ".json", "w"))
                continue   # the init event carries the tool list and the like: nothing for the chat
            if evt.get("type") in ("assistant", "user", "result"):
                self.emit(evt)
            if evt.get("type") == "result":
                self.last = time.time()
                self.set_busy(False)
        code = proc.wait()
        if self.busy:   # it ended in the middle of a turn
            self.emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": f"the conversation's process ended ({code})"})
            self.set_busy(False)
        log(f"tab {self.id}: the CLI ended with {code}")

    def say(self, text):
        with self.lock:
            if not self.alive():
                TABS_LIVE.make_room(self)
                self._spawn()
            self.last = time.time()
            self.emit({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}, "isOwnerText": True})
            self.set_busy(True)
            line = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"
            try:
                self.proc.stdin.write(line.encode())
                self.proc.stdin.flush()
            except OSError as e:
                self.emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": f"could not reach the conversation ({e})"})
                self.set_busy(False)

    def stop(self):
        """Ends the process (an interrupt, the idle reaper, the end of the backend); the conversation goes on with the next message."""
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


class Tabs:
    def __init__(self):
        self.tabs, self.lock = {}, threading.Lock()
        threading.Thread(target=self._reap, daemon=True, name="claude-reaper").start()

    def get(self, tid):
        with self.lock:
            if tid not in self.tabs:
                self.tabs[tid] = Tab(tid)
            return self.tabs[tid]

    def make_room(self, coming):
        live = sorted((t for t in self.tabs.values() if t is not coming and t.alive() and not t.busy), key=lambda t: t.last)
        while len([t for t in self.tabs.values() if t.alive()]) >= MAX_LIVE and live:
            t = live.pop(0)
            log(f"tab {t.id}: ended to make room")
            t.stop()

    def _reap(self):
        while True:
            time.sleep(15)
            for t in list(self.tabs.values()):
                if t.alive() and not t.busy and time.time() - t.last > IDLE_S:
                    log(f"tab {t.id}: idle, ended (resumed by the next message)")
                    t.stop()

    def stop_all(self):
        for t in list(self.tabs.values()):
            t.stop()

    def live(self):
        return [t.id for t in self.tabs.values() if t.alive()]


TABS_LIVE = Tabs()


# ---- the owner's Claude Code login --------------------------------------------------------------------------------------
class Login:
    """`claude auth login --claudeai` on a terminal of its own. Two ways (owner, 2026-10-31: the browser first):
    * browser: its BROWSER is runtime/browser_open.py, so the sign-in page opens in the device browser and comes back to the CLI's own callback on localhost: nothing to type;
    * paste_code: no browser for it; it prints the sign-in link (a QR code for a phone) and waits for the code the owner brings back."""
    URL = re.compile(r"https://\S*oauth/authorize\?\S+")

    def __init__(self):
        self.pid = self.fd = None
        self.url, self.state, self.error, self.out, self.kind = None, "idle", "", b"", "paste_code"
        self.lock = threading.Lock()
        self._status = (0.0, None)

    def status(self, fresh=False):
        """{signed_in, method, email?}: the CLI's own `auth status` (cached for a few seconds)."""
        at, st = self._status
        if not fresh and st is not None and time.time() - at < 10:
            return st
        try:
            r = subprocess.run([CLI, "auth", "status"], env=cli_env(), capture_output=True, text=True, timeout=30)
            j = json.loads(r.stdout or "{}")
            acct = j.get("account") if isinstance(j.get("account"), dict) else {}
            st = {"signed_in": bool(j.get("loggedIn")), "method": j.get("authMethod"), "email": j.get("email") or acct.get("email")}
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            st = {"signed_in": False, "method": None, "error": str(e)}
        self._status = (time.time(), st)
        return st

    def start(self, browser=False):
        with self.lock:
            self._end()
            os.makedirs(CONFIG, exist_ok=True)
            self.url, self.state, self.error, self.out, self.kind = None, "starting", "", b"", "browser" if browser else "paste_code"
            opener = browser_command(DATA) if browser else "/bin/false"
            pid, fd = pty.fork()
            if pid == 0:
                env = dict(cli_env(), BROWSER=opener, BROWSER_OPEN_LABEL="sign-in", TERM="xterm-256color", COLUMNS="4000")
                os.execve(CLI, [CLI, "auth", "login", "--claudeai"], env)
            self.pid, self.fd = pid, fd
            threading.Thread(target=self._pump, args=(pid, fd), daemon=True).start()
        if browser:
            self.state = "waiting_browser"   # the page is on the device's screen (or the link below, when the browser could not take it)
        end = time.time() + (5 if browser else 30)
        while time.time() < end and (self.state == "starting" or (browser and self.url is None and self.state == "waiting_browser")):
            time.sleep(0.2)
        return self.describe()

    def _pump(self, pid, fd):
        while True:
            try:
                r, _, _ = select.select([fd], [], [], 1.0)
                if r:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    self.out += chunk
                    text = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", " ", re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", self.out.decode("utf-8", "replace")))
                    if self.url is None:
                        m = self.URL.search(text)
                        if m:
                            self.url = m.group(0)
                            if self.state == "starting":
                                self.state = "waiting_code"
                    low = text.lower()
                    if self.state == "checking" and ("login successful" in low or "logged in" in low or "success" in low):
                        self.state = "done"
                    if self.state == "checking" and ("invalid" in low or "error" in low or "failed" in low):
                        self.state, self.error = "failed", text.strip().splitlines()[-1][-200:] if text.strip() else "the code was not accepted"
            except OSError:
                break
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                break
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:
            status = 0
        st = self.status(fresh=True)
        if st["signed_in"]:
            self.state = "done"
        elif self.state not in ("failed", "idle"):
            self.state, self.error = "failed", self.error or "the sign-in did not finish"
        log(f"login: {self.state}")

    def code(self, code):
        code = str(code or "").strip()
        if not code:
            raise ValueError("the code is empty")
        if self.fd is None or self.state != "waiting_code":
            raise ValueError("no sign-in is waiting for a code: start it again")
        self.state, self.out = "checking", b""
        os.write(self.fd, code.encode() + b"\r")
        end = time.time() + 60
        while time.time() < end and self.state == "checking":
            time.sleep(0.3)
        if self.state == "checking" and self.status(fresh=True)["signed_in"]:
            self.state = "done"
        if self.state == "done":
            self._end()
        return self.describe()

    def logout(self):
        self._end()
        subprocess.run([CLI, "auth", "logout"], env=cli_env(), capture_output=True, timeout=30)
        self._status = (0.0, None)
        TABS_LIVE.stop_all()
        return self.describe()

    def _end(self):
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.pid = self.fd = None

    def describe(self):
        return dict(self.status(), label="Claude account (a Claude subscription)", methods=["browser", "phone"], login={"state": self.state, "kind": self.kind, "url": self.url, "error": self.error})


LOGIN = Login()


def wizard_file():
    return os.path.join(DATA, "wizard.json")


def wizard_state():
    try:
        w = json.load(open(wizard_file()))
    except (OSError, ValueError):
        w = {}
    signed = LOGIN.status()["signed_in"]
    return {"needed": not signed and not w.get("dismissed"), "signed_in": signed, "dismissed": bool(w.get("dismissed"))}


# ---- HTTP + WebSocket -----------------------------------------------------------------------------------------------------
LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _local(self):
        o = self.headers.get("Origin")
        return o is None or o == "null" or bool(LOCAL_ORIGIN.match(o))

    def do_GET(self):
        path = self.path.split("?")[0]
        if self.headers.get("Upgrade", "").lower() == "websocket":
            return self._ws()
        if path == "/api/status":
            return self._json({"ok": True, "signed_in": LOGIN.status()["signed_in"], "live": TABS_LIVE.live(), "cli": os.path.isfile(CLI)})
        if path == "/api/auth/status":
            return self._json(LOGIN.describe())
        if path == "/api/auth/qr.svg":   # the sign-in link as a QR code, for the owner's phone (runtime/qr.py)
            if not LOGIN.url:
                return self._json({"error": "no sign-in is waiting"}, 404)
            data = qr.svg(LOGIN.url).encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(data)
        if path == "/api/wizard/state":
            return self._json(wizard_state())
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local():
            return self._json({"error": "not from this device"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "the body is not JSON"}, 400)
        path = self.path.split("?")[0]
        try:
            if path == "/api/auth/start":   # {browser: true}: the sign-in page in the device browser
                return self._json(LOGIN.start(browser=bool(body.get("browser"))))
            if path == "/api/auth/code":
                return self._json(LOGIN.code(body.get("code")))
            if path == "/api/auth/logout":
                return self._json(LOGIN.logout())
            if path == "/api/wizard/dismiss":
                os.makedirs(DATA, exist_ok=True)
                json.dump({"dismissed": True}, open(wizard_file(), "w"))
                return self._json(wizard_state())
            if path == "/api/tabs/clear":   # a new conversation in a tab: its session and history are forgotten
                t = TABS_LIVE.get(str(body.get("tab") or "1"))
                t.stop()
                t.session = None
                for ext in (".json", ".jsonl"):
                    try:
                        os.remove(t.file + ext)
                    except OSError:
                        pass
                t.broadcast({"type": "history", "events": []})
                return self._json({"ok": True})
        except ValueError as e:
            return self._json({"error": str(e)}, 409)
        self._json({"error": "not found"}, 404)

    def _ws(self):
        if not self._local():
            self.send_response(403)
            self.end_headers()
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode())
        self.end_headers()
        self.wfile.flush()
        tid = (re.search(r"[?&]tab=([^&]+)", self.path) or [None, "1"])[1]
        tab = TABS_LIVE.get(tid)
        ws = WS(self.connection)
        ws.send({"type": "history", "events": tab.history()})
        ws.send({"type": "busy", "busy": tab.busy})
        tab.clients.append(ws)
        try:
            while True:
                text = ws.recv()
                if text is None:
                    break
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if msg.get("type") == "user_message" and str(msg.get("text", "")).strip():
                    if not LOGIN.status()["signed_in"]:
                        ws.send({"type": "sdk_message", "message": {"type": "result", "subtype": "error_not_signed_in", "is_error": True,
                                                                     "result": "Claude is not signed in: sign in in Settings > Assistant"}})
                        continue
                    if tab.busy:
                        ws.send({"type": "error", "message": "a turn is still running: wait for it, or interrupt it"})
                        continue
                    tab.say(str(msg["text"]))
                elif msg.get("type") == "interrupt" and tab.busy:
                    tab.stop()
        finally:
            if ws in tab.clients:
                tab.clients.remove(ws)
        self.close_connection = True


def main():
    for d in (DATA, CONFIG, WORKSPACE, TABS):
        os.makedirs(d, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: (TABS_LIVE.stop_all(), LOGIN._end(), os._exit(0)))
    log(f"claude backend on 127.0.0.1:{PORT} (cli {CLI}, data {DATA})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
