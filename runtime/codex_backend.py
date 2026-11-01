# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Codex (OpenAI) as the provider of the assistant slot (docs/assistant-contract.md): a service of its own, started and watched by runtime/codex_assistant.py.

    python3 codex_backend.py        (the environment says where: CODEX_BACKEND_PORT, CODEX_DATA, CODEX_CLI, INTELLICAT_PORT)

It speaks the same chat protocol of the contract (section 4) as Claude's backend on ws://127.0.0.1:<port>/?tab=<id>: {"type": "user_message", "text"} and
{"type": "interrupt"} in, {"type": "sdk_message", "message": <event>} out, plus {"type": "history"} on connect and {"type": "busy"}. The events are the shape the standard
chat view and the ask client know (assistant / user / result, with text, tool_use and tool_result blocks); here they are made from the JSONL of `codex exec --json`.

A tab is one conversation (a Codex thread). Codex has no process that waits for the next message: each turn is `codex exec --json` (the first) or `codex exec resume
<thread> --json` (the next ones), so nothing is kept alive between turns and the thread id in <tab>.json is all that a restart of this backend needs.

What Codex may use: the OS Beroun MCP server (beroun-tools, the same tools Caroline has), Caroline's own tools from her backend (caroline-tools), the apps' own MCP
servers (app-<id>), web search, and the apps' skills (linked into its workspace and listed in its AGENTS.md). Nothing of the machine underneath: its own shell and file tools are off, its sandbox is read-only, no approvals asked.

Its account is the owner's ChatGPT login (`codex login --device-auth`: a link and a one-time code, shown on the screen with a QR code of the link) or an OpenAI API key
the owner types in (`codex login --with-api-key`). The credentials stay in its own CODEX_HOME.

Standard library only."""
import base64, hashlib, json, os, re, signal, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qr
from assistant_common import browser_command, GUID, SHELL_PORT, WS, log, mcp_config, link_skills

PORT = int(os.environ.get("CODEX_BACKEND_PORT", "48776"))
DATA = os.environ.get("CODEX_DATA", os.path.expanduser("~/.intellicat/data/codex"))
CLI = os.environ.get("CODEX_CLI", os.path.expanduser("~/.intellicat/codex-runtime/node_modules/.bin/codex"))
HOME = os.path.join(DATA, "home")             # CODEX_HOME: its login, its config.toml and its own state
WORKSPACE = os.path.join(DATA, "workspace")   # the working folder of every turn: AGENTS.md and the skills (.agents/skills)
TABS = os.path.join(DATA, "tabs")             # per tab: <tab>.json (its thread id), <tab>.jsonl (its events, the history the chat view shows)
MODEL = os.environ.get("CODEX_MODEL", "")     # empty: the CLI's default for the account
HISTORY_MAX = 400
LABEL = "ChatGPT account"

SYSTEM_PROMPT = """You are Codex, the assistant of OS Beroun on this device: the manager of the system, not a guest. OS Beroun is the operating system of IntelliCat,
a small handheld console (a 640x480 screen, a cursor pad, A to confirm, B to go back). The owner talks to you in the chat on that screen: answer briefly and plainly,
the screen is small. You act on the device only through your tools: the MCP servers. The server beroun holds the apps, any app's window, the files of the memory card,
settings, the clock, the network, notifications, a message box, sounds, vibration, the store; the agents of the apps (agent_*); and one server per app (app-<id>) with the
app's own tools; and caroline-tools, Caroline's own tools (the messages and the messengers, mail, your memory across conversations, reminders, calendar, contacts,
notes, images, the device browser; she runs on the device as the host of Communicator). You have no shell and no files of your own: do not try. Read each server's
instructions for how to use it.
Ask the owner before anything that sends, deletes, buys or changes the owner's settings."""


def toml_str(s):
    return json.dumps(str(s), ensure_ascii=True)   # a JSON string is a TOML basic string


def toml_list(items):
    return "[" + ", ".join(toml_str(i) for i in items) + "]"


def config_toml(servers):
    """CODEX_HOME/config.toml: no shell, no approvals, a read-only sandbox, web search, and the MCP servers of the system."""
    out = ['approval_policy = "never"', 'sandbox_mode = "read-only"', 'web_search = "live"', 'check_for_update_on_startup = false', "",
           "[features]", "shell_tool = false", "unified_exec = false", "view_image = false", ""]
    for name, s in servers.items():
        out.append(f"[mcp_servers.{name}]")
        if s.get("type") == "http":
            out.append(f"url = {toml_str(s['url'])}")
        else:
            out.append(f"command = {toml_str(s['command'])}")
            out.append(f"args = {toml_list(s.get('args') or [])}")
            if s.get("env"):
                out.append("env = {" + ", ".join(f"{json.dumps(k)} = {toml_str(v)}" for k, v in s["env"].items()) + "}")
        out.append("startup_timeout_sec = 30")
        out.append("tool_timeout_sec = 120")
        out.append("")
    return "\n".join(out)


CONFIG_LOCK = threading.Lock()   # config.toml is one for every tab, and names the tab (caroline-tools): held from prepare() until the turn's CLI has read it


def prepare(tab="1"):
    """Before a turn: the config, the skills and AGENTS.md as the system is now (the apps installed, their servers), for the tab of the turn."""
    cfg, apps = mcp_config(tab)
    os.makedirs(HOME, exist_ok=True)
    os.makedirs(WORKSPACE, exist_ok=True)
    open(os.path.join(HOME, "config.toml"), "w", encoding="utf-8").write(config_toml(cfg["mcpServers"]))
    link_skills(apps, WORKSPACE, (".agents", "skills"))
    skills = []
    root = os.path.join(WORKSPACE, ".agents", "skills")
    for n in sorted(os.listdir(root)):
        skills.append(f"- {n}: {os.path.join('.agents', 'skills', n, 'SKILL.md')}")
    text = SYSTEM_PROMPT + ("\n\nSkills of the apps (read the SKILL.md when a task is the app's):\n" + "\n".join(skills) if skills else "") + "\n"
    open(os.path.join(WORKSPACE, "AGENTS.md"), "w", encoding="utf-8").write(text)


def cli_env():
    env = dict(os.environ, CODEX_HOME=HOME, INTELLICAT_PORT=SHELL_PORT, NO_COLOR="1")
    for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):   # the owner's login is the account, never a key found in the environment
        env.pop(k, None)
    return env


# ---- the events of codex exec --json -> the events of the chat protocol --------------------------------------------------------
def _assistant(blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def _tool_result(tool_id, text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": text}], "is_error": is_error}]}}


def _result_text(res):
    if not isinstance(res, dict):
        return "" if res is None else str(res)
    parts = [c.get("text", "") for c in res.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
    if not parts and res.get("structured_content") is not None:
        return json.dumps(res["structured_content"], ensure_ascii=False)
    return "\n".join(parts)


class Translator:
    """feed(one JSONL object) -> the events to send. `thread` is set by thread.started; `last` is the last text of the assistant (the result of the turn)."""
    def __init__(self):
        self.thread, self.last, self.opened, self.finished = None, "", set(), False

    def feed(self, e):
        t, out = e.get("type"), []
        if t == "thread.started":
            self.thread = e.get("thread_id")
        elif t in ("item.started", "item.updated", "item.completed"):
            it = e.get("item") or {}
            kind, iid = it.get("type"), it.get("id")
            if kind == "agent_message" and t == "item.completed" and it.get("text"):
                self.last = it["text"]
                out.append(_assistant([{"type": "text", "text": it["text"]}]))
            elif kind in ("mcp_tool_call", "web_search"):
                if iid not in self.opened:
                    self.opened.add(iid)
                    if kind == "mcp_tool_call":
                        out.append(_assistant([{"type": "tool_use", "id": iid, "name": f"mcp__{it.get('server')}__{it.get('tool')}", "input": it.get("arguments") or {}}]))
                    else:
                        out.append(_assistant([{"type": "tool_use", "id": iid, "name": "WebSearch", "input": {"query": it.get("query", "")}}]))
                if t == "item.completed":
                    if kind == "mcp_tool_call":
                        err = it.get("error")
                        failed = bool(err) or it.get("status") == "failed"
                        out.append(_tool_result(iid, (err or {}).get("message", "") if err else _result_text(it.get("result")), failed))
                    else:
                        out.append(_tool_result(iid, "", False))
        elif t == "turn.completed":
            self.finished = True
            out.append({"type": "result", "subtype": "success", "is_error": False, "result": self.last, "session_id": self.thread})
        elif t == "turn.failed":
            self.finished = True
            msg = ((e.get("error") or {}).get("message")) or "the turn failed"
            out.append({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": msg, "session_id": self.thread})
        return out


# ---- the conversations ----------------------------------------------------------------------------------------------------
class Tab:
    def __init__(self, tid):
        self.id, self.proc, self.clients, self.busy = tid, None, [], False
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

    def say(self, text):
        with self.lock:
            if self.busy:
                return
            self.emit({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}, "isOwnerText": True})
            self.set_busy(True)
            threading.Thread(target=self._turn, args=(text,), daemon=True, name=f"codex-{self.id}").start()

    def _turn(self, text):
        tr, stopped = Translator(), False
        try:
            code, err = self._run(text, tr)
            if self.session and tr.thread is None and not tr.finished and "no rollout found" in err.lower():
                # its thread is gone (CODEX_HOME lost or cleared, found 2026-10-31 with the real CLI): the tab starts a new one instead of failing for ever
                log(f"tab {self.id}: thread {self.session} is gone; a new one")
                self.session = None
                try:
                    os.remove(self.file + ".json")
                except OSError:
                    pass
                tr = Translator()
                code, err = self._run(text, tr)
            stopped = code in (-signal.SIGTERM, -signal.SIGKILL)
            log(f"tab {self.id}: the CLI ended with {code}")
            if not tr.finished:   # it ended in the middle of a turn
                self.emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                           "result": "the turn was interrupted" if stopped else f"the conversation's process ended ({code})"})
        except Exception as e:
            log(f"tab {self.id}: {e}")
            self.emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": f"could not run the turn ({e})"})
        finally:
            self.proc = None
            self.set_busy(False)

    def _run(self, text, tr):
        """One `codex exec` process for the turn: its events go out as they come. Returns its exit code and what it wrote to stderr (also kept in cli.log)."""
        common = ["--json", "--skip-git-repo-check"] + (["-m", MODEL] if MODEL else [])
        cmd = [CLI, "exec", "resume", *common, self.session, "-"] if self.session else [CLI, "exec", *common, "-"]
        CONFIG_LOCK.acquire()
        locked = [True]

        def unlock():
            if locked[0]:
                locked[0] = False
                CONFIG_LOCK.release()
        try:
            prepare(self.id)
            self.proc = subprocess.Popen(cmd, cwd=WORKSPACE, env=cli_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except BaseException:
            unlock()
            raise
        threading.Timer(20.0, unlock).start()   # a CLI that says nothing for long has read its config long ago
        proc, err = self.proc, []
        log(f"tab {self.id}: the CLI started, pid {proc.pid}" + (f", resuming {self.session}" if self.session else ""))

        def drain():
            with open(os.path.join(DATA, "cli.log"), "ab") as f:
                for line in proc.stderr:
                    f.write(line)
                    err.append(line.decode("utf-8", "replace"))
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            proc.stdin.write(text.encode())
            proc.stdin.close()
        except OSError:
            pass
        for raw in proc.stdout:
            unlock()   # it has started: its config (and its MCP servers' tab) is read
            try:
                evt = json.loads(raw)
            except ValueError:
                continue
            for out in tr.feed(evt):
                self.emit(out)
            if tr.thread and tr.thread != self.session:
                self.session = tr.thread
                os.makedirs(TABS, exist_ok=True)
                json.dump({"session": self.session}, open(self.file + ".json", "w"))
        code = proc.wait()
        unlock()
        reader.join(5)
        return code, "".join(err)

    def stop(self):
        """Ends the turn that runs (an interrupt, the end of the backend); the conversation goes on with the next message."""
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

    def get(self, tid):
        with self.lock:
            if tid not in self.tabs:
                self.tabs[tid] = Tab(tid)
            return self.tabs[tid]

    def stop_all(self):
        for t in list(self.tabs.values()):
            t.stop()

    def live(self):
        return [t.id for t in self.tabs.values() if t.busy]


TABS_LIVE = Tabs()


# ---- the owner's login ----------------------------------------------------------------------------------------------------
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)")


class Login:
    """Two ways (owner, 2026-10-31: the browser first):
    * browser: `codex login` with runtime/browser_open.py as its BROWSER: the sign-in page opens in the device browser and comes back to the CLI's own server on
      127.0.0.1:1455 of this machine: nothing to type;
    * device: `codex login --device-auth`: it prints a link and a one-time code, then waits (polling OpenAI) until the owner has entered the code on a phone."""
    URL = re.compile(r"https://\S+")
    CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b")

    def __init__(self):
        self.proc, self.url, self.code, self.state, self.error, self.kind = None, None, None, "idle", "", "device"
        self.lock = threading.Lock()
        self._status = (0.0, None)

    def status(self, fresh=False):
        """{signed_in, method}: the CLI's own `login status` (cached for a few seconds)."""
        at, st = self._status
        if not fresh and st is not None and time.time() - at < 10:
            return st
        try:
            r = subprocess.run([CLI, "login", "status"], env=cli_env(), capture_output=True, text=True, timeout=30)
            text = ANSI.sub("", (r.stdout or "") + (r.stderr or "")).strip()
            st = {"signed_in": r.returncode == 0 and "not logged in" not in text.lower(), "method": "chatgpt" if "chatgpt" in text.lower() else ("api-key" if "api key" in text.lower() else None), "detail": text[:200]}
        except (OSError, subprocess.TimeoutExpired) as e:
            st = {"signed_in": False, "method": None, "error": str(e)}
        self._status = (time.time(), st)
        return st

    def start(self, browser=False):
        with self.lock:
            self._end()
            os.makedirs(HOME, exist_ok=True)
            self.url, self.code, self.error, self.kind = None, None, "", "browser" if browser else "device"
            self.state = "waiting_browser" if browser else "starting"
            if browser:
                cmd, env = [CLI, "login"], dict(cli_env(), BROWSER=browser_command(DATA), BROWSER_OPEN_LABEL="sign-in")
            else:
                cmd, env = [CLI, "login", "--device-auth"], cli_env()
            self.proc = subprocess.Popen(cmd, cwd=HOME, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        end = time.time() + (5 if browser else 30)
        while time.time() < end and (self.state == "starting" or (browser and self.url is None and self.state == "waiting_browser")):
            time.sleep(0.2)
        return self.describe()

    def _pump(self, proc):
        text = ""
        for raw in proc.stdout:
            text += ANSI.sub("", raw.decode("utf-8", "replace"))
            if self.kind == "browser" and self.url is None:   # the link it also prints (shown when the device browser could not take it)
                u = self.URL.search(text[text.lower().find("authenticate"):] if "authenticate" in text.lower() else "")
                if u:
                    self.url = u.group(0).rstrip(".,")
            if self.state == "starting":
                u = self.URL.search(text)
                after = text[text.lower().find("one-time code"):] if "one-time code" in text.lower() else ""
                c = self.CODE.search(after)
                if u and c:
                    self.url, self.code, self.state = u.group(0).rstrip(".,"), c.group(0), "waiting_device"
        code = proc.wait()
        if self.proc is not proc:
            return   # replaced or ended on purpose
        st = self.status(fresh=True)
        if st["signed_in"]:
            self.state = "done"
        else:
            self.state = "failed"
            self.error = ([l for l in text.strip().splitlines() if l.strip()] or ["the sign-in did not finish"])[-1][:200] if code else "the sign-in did not finish"
        log(f"login: {self.state}")

    def key(self, key):
        key = str(key or "").strip()
        if not key:
            raise ValueError("the key is empty")
        self._end()
        os.makedirs(HOME, exist_ok=True)
        r = subprocess.run([CLI, "login", "--with-api-key"], input=key + "\n", env=cli_env(), capture_output=True, text=True, timeout=60)
        self._status = (0.0, None)
        if r.returncode != 0 or not self.status(fresh=True)["signed_in"]:
            self.state, self.error = "failed", "the key was not accepted"
        else:
            self.state, self.error = "done", ""
        return self.describe()

    def logout(self):
        self._end()
        subprocess.run([CLI, "logout"], env=cli_env(), capture_output=True, timeout=30)
        self._status = (0.0, None)
        self.state = "idle"
        TABS_LIVE.stop_all()
        return self.describe()

    def _end(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def describe(self):
        return dict(self.status(), label=LABEL, methods=["browser", "phone", "key"], login={"state": self.state, "kind": self.kind, "url": self.url, "code": self.code, "error": self.error})


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
            if path == "/api/auth/key":
                return self._json(LOGIN.key(body.get("key")))
            if path == "/api/auth/logout":
                return self._json(LOGIN.logout())
            if path == "/api/wizard/dismiss":
                os.makedirs(DATA, exist_ok=True)
                json.dump({"dismissed": True}, open(wizard_file(), "w"))
                return self._json(wizard_state())
            if path == "/api/tabs/clear":   # a new conversation in a tab: its thread and history are forgotten
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
                                                                     "result": "Codex is not signed in: sign in in Settings > Assistant"}})
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
    for d in (DATA, HOME, WORKSPACE, TABS):
        os.makedirs(d, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: (TABS_LIVE.stop_all(), LOGIN._end(), os._exit(0)))
    log(f"codex backend on 127.0.0.1:{PORT} (cli {CLI}, data {DATA})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
