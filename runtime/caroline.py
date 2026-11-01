# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Caroline's backend as a service of the shell daemon.

Caroline (the pinned submodule caroline) is upstream and is not changed. This module only starts her
supervisor (backend-py/supervisor.py, which owns run_server.py and restarts it) in her own runtime, with the
environment the device needs, and watches it:

* it runs only when the device has an account (the credentials file) and a network, and only when the runtime is
  installed (scripts/caroline-provision.sh); before that nothing is started and nothing loops;
* it dies with the shell daemon (kernel parent-death signal, plus an explicit stop);
* a supervisor that exits is started again with a growing pause (5 s ... 2 min);
* her updates (runtime/caroline_update.py): the tree that runs is the firmware's copy or, when it is newer, the owner's writable copy with an update applied;
  one that dies at once is dropped and the firmware's runs again;
* status() reports state and memory (the supervisor and everything it started).
"""
import ctypes, json, os, signal, subprocess, threading, time, urllib.request
import assistant, caroline_ask, caroline_update

ROOT = os.environ.get("CAROLINE_RUNTIME", os.path.expanduser("~/.intellicat/caroline-runtime"))
_OFF = int(os.environ.get("INTELLICAT_PORT_OFFSET") or 0)   # a simulator with an instance name has every port shifted (scripts/lib.sh)
PORT = int(os.environ.get("CAROLINE_PORT", 48765 + _OFF))
SUPERVISOR_PORT = int(os.environ.get("CAROLINE_SUPERVISOR_PORT", 48766 + _OFF))
PAGE_PORT = 48767 + _OFF   # where the shell serves her chat page (shelld's CAROLINE_PAGE_PORT): her origin check is told (caroline-overlay/sitecustomize.py)
BACKOFF_S = (5, 10, 20, 40, 80, 120)
STABLE_S = 120  # a run this long resets the pause
QUICK_EXIT_S = 60   # a supervisor of an updated copy that exits sooner than this has not started
QUICK_EXITS = 2     # ... this many times in a row: the update is dropped
PROBATION_S = 150   # an updated copy must answer on her port within this long after it started, or it is dropped (her supervisor restarts a crashing backend for ever)


def _die_with_parent():
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG (the process's own libc: "libc.so.6" on Linux, "libc.so" on Android)


def _children_of(pid):
    """pid and all its descendants (from /proc)."""
    parent, found = {}, [pid]
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    parent[int(d)] = int(f.read().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                pass
    for p in found:
        found.extend(c for c, pp in parent.items() if pp == p and c not in found)
    return found


def _rss_kb(pid):
    total = 0
    for p in _children_of(pid):
        try:
            with open(f"/proc/{p}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
        except (OSError, ValueError):
            pass
    return total


class Caroline(assistant.Provider):
    """Caroline as the provider of the assistant slot (docs/assistant-contract.md). Her backend runs whenever the device has an account and a network, also when another
    provider is the assistant: then it is only Communicator's host (the owner's decision of 2026-10-31), and nothing of hers is on the screen."""

    def __init__(self, sim_dir, state_dir, data_dir, wanted, log=print):
        """wanted() -> bool: the device has an account and a network (the caller knows)."""
        self.src = os.environ.get("CAROLINE_SRC", os.path.join(sim_dir, "caroline"))
        # what runs: her IntelliCat edition (scripts/caroline-build.sh) when it was built, else the checkout itself
        built = os.path.join(self.src, "dist-intellicat")
        self.firmware_app = built if os.path.isfile(os.path.join(built, "EDITION")) else self.src
        self.layer = caroline_update.CodeLayer(self.firmware_app, data_dir, log)
        self.app = self.layer.choose() if self.firmware_app == built else self.firmware_app   # an updated copy only of a build; the checkout itself is a developer's
        self.quick_exits = 0
        self.probation = time.time() + PROBATION_S if self.app != self.firmware_app else None   # an updated tree is on probation until it has answered once
        self.overlay = os.path.join(sim_dir, "caroline-overlay")
        self.state, self.data, self.wanted, self.log = state_dir, data_dir, wanted, log
        self.python = os.path.join(ROOT, "runtime", "python", "bin", "python3")
        self.proc, self.started_at, self.restarts = None, 0.0, 0
        self.stopping = False
        self.updater = None   # caroline_update.Updater, set by the shell once it is made (it needs this object)
        self.face = None      # face.Face, set by the shell: her Visual Mode in the voice view
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._wake.set()  # the first look is at once, not after the first pause
        threading.Thread(target=self._watch, daemon=True, name="caroline-watch").start()

    # -- what can be asked ----------------------------------------------------------------------------
    def installed(self):
        if os.environ.get("BEROUN_PYTHON"):   # Android: her runtime's Python is a link to the shell's launcher
            self._link_native()
        return os.path.isfile(self.python) and os.path.isfile(os.path.join(self.app, "backend-py", "supervisor.py"))

    def ensure(self):
        """Something changed (login, network, start-up): look again now."""
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

    def api(self, method, path, timeout=5):
        """Her backend's own HTTP API, for the shell (the update state): the JSON answer, or None when it does not answer."""
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=b"{}" if method == "POST" else None, method=method, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except Exception:
            return None

    def stop_for_update(self):
        """Stops the supervisor and keeps it stopped until use_tree + ensure."""
        self.stop()

    def use_tree(self, tree):
        self.app = tree
        self.quick_exits = 0
        self.probation = time.time() + PROBATION_S if tree != self.firmware_app else None

    def _drop_updated(self, why):
        """The updated copy does not work: it is marked failed and the firmware's Caroline runs again (the caller stops the process)."""
        self.log(f"caroline: the updated copy {why}")
        self.app, self.quick_exits, self.probation = self.layer.fall_back(caroline_update.read_version(self.app) or "?"), 0, None

    def dialogue(self, enabled=None):
        """Her continuous-dialogue switch (one for the whole app): read it (enabled=None) or set it; None when the backend does not answer."""
        try:
            if enabled is None:
                r = self._get(PORT, "/api/continuous-dialogue")
            else:
                req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/continuous-dialogue", data=json.dumps({"enabled": bool(enabled)}).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    r = json.load(resp)
            return None if r is None else bool(r.get("enabled", enabled))
        except Exception:
            return None

    def _get(self, port, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
                return json.load(r)
        except Exception:
            return None

    def wizard_needed(self):
        """Whether her start wizard has to be shown (no answer yet, or started earlier and not finished). None: the backend
        does not answer (no wizard then, never a broken start)."""
        st = self._get(PORT, "/api/wizard/state")
        return None if st is None else bool(st.get("needed"))

    def wizard_done(self):
        """Whether her start wizard was finished or dismissed, from her own file (<workspace>/wizard.json, the "completed" / "dismissed" of backend-py/app/wizard.py): read from the
        disk, no backend needed, so the shell decides at once after a login whether the wizard comes or the launcher does."""
        try:
            with open(os.path.join(self.data, "caroline", "workspace", "wizard.json"), encoding="utf-8") as f:
                d = json.load(f)
            return bool(d.get("completed") or d.get("dismissed"))
        except (OSError, ValueError, AttributeError):
            return False

    # -- the assistant's operations (assistant.Provider) --------------------------------------------------
    def pid(self):
        proc = self.proc
        return proc.pid if proc is not None and proc.poll() is None else None

    def ask(self, app, text, timeout=120.0):
        try:
            return caroline_ask.ask(PORT, app, text, timeout)
        except caroline_ask.Unavailable as e:
            raise assistant.Unavailable(str(e))
        except caroline_ask.Timeout as e:
            raise assistant.Timeout(str(e))
        except caroline_ask.Failed as e:
            raise assistant.Failed(str(e))

    def chat_port(self):
        return PORT

    def voice_start(self, env, launch):
        """Her continuous dialogue and Visual Mode (the talking head) go on, her voice view is launched (launch()), and her face comes over the screen.
        What was set before is kept for voice_end."""
        self._voice_before = (self.dialogue(), self._visual_mode_file(True))   # Visual Mode is set in her workspace before her page loads
        launch()
        if self.face is not None:
            threading.Thread(target=self.face.start, args=(env,), daemon=True).start()   # a window of its own, transparent around her

    def voice_end(self):
        """After the voice view is gone (R2, a crash, a kill): the face ends, her switches come back as they were (the page's own restore cannot be relied on)."""
        before, vm_before = getattr(self, "_voice_before", (None, False))
        if self.face is not None:
            self.face.stop()
        if before is not None and self.dialogue() != before:
            self.dialogue(before)
        self._visual_mode_file(vm_before)

    def _visual_mode_file(self, enabled):
        """Her Visual Mode switch is a file of her workspace (visualMode.json), read by her backend on every use. With the one model on the card her own
        switch (the checkbox) cannot be used: it wants both of the persona's day-parity files and starts a download. Returns the old value."""
        path = os.path.join(self.data, "caroline", "workspace", "visualMode.json")
        old = False
        try:
            old = bool(json.load(open(path, encoding="utf-8")).get("enabled"))
        except (OSError, ValueError):
            pass
        if old != enabled:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"enabled": enabled}, f, indent=2)
            os.replace(path + ".tmp", path)
        return old

    def update_status(self):
        return self.updater.status() if self.updater else super().update_status()

    def update_check(self):
        if not self.updater:
            return super().update_check()
        self.updater.check_now()
        return self.updater.status()

    def update_now(self):
        if not self.updater:
            return super().update_now()
        self.updater.confirm()
        return self.updater.status()

    def status(self):
        proc = self.proc
        running = bool(proc and proc.poll() is None)
        out = {"installed": self.installed(), "running": running, "port": PORT, "restarts": self.restarts,
               "wanted": bool(self.wanted()), "healthy": False}
        if running:
            out.update(pid=proc.pid, uptime_s=int(time.time() - self.started_at), rss_mb=round(_rss_kb(proc.pid) / 1024, 1))
            out["healthy"] = self._get(PORT, "/api/status") is not None
        return out

    # -- process ---------------------------------------------------------------------------------------
    def _link_native(self):
        """On Android the programs of her runtime (Node for the WhatsApp and Messenger sidecars, signal-cli) are programs of the APK's library folder; her supervisor looks for them
        where a Linux runtime has them, so the places are links to them (the host says where they are in $BEROUN_NODE, $BEROUN_SIGNAL_CLI and $BEROUN_PYTHON)."""
        for var, rel in (("BEROUN_NODE", ("node", "bin", "node")), ("BEROUN_SIGNAL_CLI", ("signal-cli", "signal-cli")), ("BEROUN_PYTHON", ("python", "bin", "python3"))):
            target = os.environ.get(var)
            if not target or not os.path.exists(target):
                continue
            link = os.path.join(ROOT, "runtime", *rel)
            try:
                os.makedirs(os.path.dirname(link), exist_ok=True)
                if os.path.islink(link) and os.readlink(link) == target:
                    continue
                if os.path.lexists(link):
                    os.remove(link)
                os.symlink(target, link)
            except OSError as e:
                self.log(f"caroline: cannot link {link}: {e}")

    def _env(self):
        self._link_native()
        ws = os.path.join(self.data, "caroline", "workspace")
        os.makedirs(ws, exist_ok=True)
        env = dict(os.environ)
        env.update(
            CAROLINE_APP_ROOT=ROOT, CAROLINE_WORKSPACE_DIR=ws, XDG_DATA_HOME=os.path.join(self.data, "caroline", "xdg"),
            CAROLINE_PORT=str(PORT), CAROLINE_SUPERVISOR_PORT=str(SUPERVISOR_PORT), CAROLINE_PAGE_ORIGIN=f"http://127.0.0.1:{PAGE_PORT}",
            CAROLINE_UPDATE_PLATFORM=caroline_update.PLATFORM, CAROLINE_RUNTIME=str(caroline_update.runtime_number(ROOT)),   # her update client: the artifact `beroun`, this device's runtime number
            CAROLINE_UPDATE_DIR=self.layer.download_dir,
            PYTHONPYCACHEPREFIX=os.path.join(ROOT, "pycache"), PYTHONUNBUFFERED="1",  # the checkout stays untouched
            PYTHONPATH=os.pathsep.join([self.overlay, os.path.join(os.path.dirname(self.overlay), "beroun-tools")]), IC_BROWSER_CONTROL=os.path.join(self.state, "browser.sock"))  # her browser tool -> ic-browser
        if self.app == self.src:  # not a build: the SW engine's client files are the ones her Makefile copies into a build
            env["CAROLINE_CAMERLENGO_PATH"] = os.path.join(self.src, "backend-py", "camerlengo-vendor-stubs")
        # her Visual Mode models (one file of 12 GB and more) live on the SD card, which is ext4 for that reason (FAT32 stops at 4 GB)
        models = os.path.join(os.environ.get("INTELLICAT_HOME", "/media/sdcard"), "models")
        if os.path.isdir(os.path.dirname(models)):
            os.makedirs(models, exist_ok=True)
            env["CAROLINE_MODELS_DIR"] = models
        env.pop("CAROLINE_REMOTE_ACCESS", None)  # the backend listens on this machine only
        return env

    def _spawn(self):
        log = open(os.path.join(self.state, "caroline.log"), "ab")
        self.proc = subprocess.Popen([self.python, os.path.join(self.app, "backend-py", "supervisor.py")],
                                     cwd=os.path.join(self.app, "backend-py"), env=self._env(), stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, preexec_fn=_die_with_parent)
        self.started_at = time.time()
        self.log(f"caroline: supervisor started, pid {self.proc.pid}")

    def _watch(self):
        """One long-lived thread owns the process (the parent-death signal follows the thread that started it)."""
        pause = 0
        while True:
            self._wake.wait(15)
            self._wake.clear()
            try:
                if self.stopping or not self.installed() or not self.wanted():
                    if self.proc and not self.wanted() and not self.stopping:
                        self.stop(); self.stopping = False  # account or network gone: wait for them to return
                    continue
                if self.proc and self.proc.poll() is None:
                    if self.probation:
                        if self._get(PORT, "/api/status") is not None:
                            self.probation = None   # it answers: the update is good
                            self.layer.proven()
                        elif time.time() > self.probation:
                            self._drop_updated(f"did not answer in {PROBATION_S} s")
                            self.stop(); self.stopping = False
                            self.restarts += 1
                            continue
                    if time.time() - self.started_at > STABLE_S:
                        pause = 0
                    continue
                if self.proc:  # it exited on its own
                    if self.app != self.firmware_app and time.time() - self.started_at < QUICK_EXIT_S:
                        self.quick_exits += 1
                        if self.quick_exits >= QUICK_EXITS:   # the updated copy does not start: the firmware's Caroline runs again
                            self._drop_updated("exits as soon as it starts")
                            self.proc = None
                            self.restarts += 1
                            continue
                    else:
                        self.quick_exits = 0
                    self.log(f"caroline: supervisor exited with {self.proc.returncode}; again in {BACKOFF_S[pause]} s")
                    self.proc = None
                    self.restarts += 1
                    time.sleep(BACKOFF_S[pause]); pause = min(pause + 1, len(BACKOFF_S) - 1)
                    if self.stopping or not self.wanted():
                        continue
                self._spawn()
            except Exception as e:  # the watcher must survive anything
                self.log(f"caroline: watcher error: {e}")
