# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Codex as a provider of the assistant slot (docs/assistant-contract.md): its backend (runtime/codex_backend.py) as a service of the shell daemon.

* it runs only when Codex is the active assistant, the device has an account and a network, and the Codex CLI is installed (scripts/codex-provision.sh);
* it dies with the shell daemon (kernel parent-death signal, plus an explicit stop); one that exits is started again with a growing pause (5 s ... 2 min);
* its account is the owner's ChatGPT login (or an OpenAI key), made in its wizard (system/assistant-wizard): until then the wizard comes after the device's login;
* an app's request (ask) and the chat speak the chat protocol of the contract with the backend; it has no voice of its own, so R2 opens its chat.
"""
import ctypes, json, os, signal, subprocess, sys, threading, time, urllib.request
import assistant, caroline_ask

_OFF = int(os.environ.get("INTELLICAT_PORT_OFFSET") or 0)   # a simulator with an instance name has every port shifted (scripts/lib.sh)
PORT = int(os.environ.get("CODEX_BACKEND_PORT", 48776 + _OFF))
CLI = os.environ.get("CODEX_CLI", os.path.expanduser("~/.intellicat/codex-runtime/node_modules/.bin/codex"))
BACKOFF_S = (5, 10, 20, 40, 80, 120)
STABLE_S = 120


def _die_with_parent():
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG


class Codex(assistant.Provider):
    def __init__(self, sim_dir, state_dir, data_dir, wanted, log=print):
        """wanted() -> bool: the device has an account and a network, and Codex is the active assistant (the caller knows)."""
        self.backend = os.path.join(sim_dir, "runtime", "codex_backend.py")
        self.state, self.data, self.wanted, self.log = state_dir, os.path.join(data_dir, "codex"), wanted, log
        self.proc, self.started_at, self.restarts, self.stopping = None, 0.0, 0, False
        self._wake, self._lock = threading.Event(), threading.Lock()
        self._wake.set()

    def start(self):
        """The watcher starts once the shell's slot exists (wanted() asks the slot; found 2026-10-31: started in __init__, it met an undefined slot at every start)."""
        threading.Thread(target=self._watch, daemon=True, name="codex-watch").start()

    # -- its backend's life ---------------------------------------------------------------------------
    def installed(self):
        return os.path.isfile(CLI) and os.path.isfile(self.backend)

    def ensure(self):
        self.stopping = False
        self._wake.set()

    def stop(self):
        self.stopping = True
        with self._lock:
            proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    proc.wait(5)
                    break
                except subprocess.TimeoutExpired:
                    pass

    def pid(self):
        proc = self.proc
        return proc.pid if proc is not None and proc.poll() is None else None

    def api(self, method, path, body=None, timeout=5):
        """The backend's HTTP API: the JSON answer, or None when it does not answer."""
        try:
            data = json.dumps(body or {}).encode() if method == "POST" else None
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception:
            return None

    def status(self):
        proc = self.proc
        running = bool(proc and proc.poll() is None)
        out = {"installed": self.installed(), "running": running, "port": PORT, "restarts": self.restarts, "wanted": bool(self.wanted()), "healthy": False}
        if running:
            out.update(pid=proc.pid, uptime_s=int(time.time() - self.started_at))
            st = self.api("GET", "/api/status")
            out["healthy"] = st is not None
            if st:
                out.update(signed_in=st.get("signed_in"), live=st.get("live"))
        return out

    # -- the assistant's operations -------------------------------------------------------------------
    def ask(self, app, text, timeout=120.0):
        st = self.api("GET", "/api/status")
        if st is None:
            raise assistant.Unavailable("Codex's backend does not answer (not running, or no account yet)")
        if not st.get("signed_in"):
            raise assistant.Unavailable("Codex is not signed in (the owner signs in in Settings > Assistant)")
        try:
            return caroline_ask.ask(PORT, app, text, timeout)   # the chat protocol of the contract: the same client as Caroline's
        except caroline_ask.Unavailable as e:
            raise assistant.Unavailable(str(e))
        except caroline_ask.Timeout as e:
            raise assistant.Timeout(str(e))
        except caroline_ask.Failed as e:
            raise assistant.Failed(str(e))

    def chat_port(self):
        return PORT

    def wizard_needed(self):
        st = self.api("GET", "/api/wizard/state", timeout=40)
        return None if st is None else bool(st.get("needed"))

    def wizard_done(self):
        """Signed in or dismissed, from its files (no backend needed): its CODEX_HOME holds the credentials once the owner has signed in."""
        try:
            if json.load(open(os.path.join(self.data, "wizard.json"))).get("dismissed"):
                return True
        except (OSError, ValueError, AttributeError):
            pass
        return os.path.isfile(os.path.join(self.data, "home", "auth.json"))

    # -- process ---------------------------------------------------------------------------------------
    def _env(self):
        env = dict(os.environ, CODEX_BACKEND_PORT=str(PORT), CODEX_DATA=self.data, CODEX_CLI=CLI, PYTHONUNBUFFERED="1")
        return env

    def _spawn(self):
        os.makedirs(self.data, exist_ok=True)
        log = open(os.path.join(self.state, "codex.log"), "ab")
        self.proc = subprocess.Popen([sys.executable, self.backend], env=self._env(), stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, preexec_fn=_die_with_parent)
        self.started_at = time.time()
        self.log(f"codex: backend started, pid {self.proc.pid}")

    def _watch(self):
        """One long-lived thread owns the process (the parent-death signal follows the thread that started it)."""
        pause = 0
        while True:
            self._wake.wait(15)
            self._wake.clear()
            try:
                if self.stopping or not self.installed() or not self.wanted():
                    if self.proc and not self.wanted() and not self.stopping:
                        self.stop(); self.stopping = False   # not the assistant any more, or no account or network: it waits for them
                    continue
                if self.proc and self.proc.poll() is None:
                    if time.time() - self.started_at > STABLE_S:
                        pause = 0
                    continue
                if self.proc:
                    self.log(f"codex: backend exited with {self.proc.returncode}; again in {BACKOFF_S[pause]} s")
                    self.proc = None
                    self.restarts += 1
                    time.sleep(BACKOFF_S[pause]); pause = min(pause + 1, len(BACKOFF_S) - 1)
                    if self.stopping or not self.wanted():
                        continue
                self._spawn()
            except Exception as e:
                self.log(f"codex: watcher error: {e}")
