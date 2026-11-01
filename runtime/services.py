# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The service manager (docs/architecture.md, the web-app model): a service is a backend that survives the close of the app.

A service is declared in the manifest of an app (apps/<id>/manifest.json, "services": [...]):

    {"id": "caroline-backend", "name": "...",
     "host": "module" | "process" | "child",    module: an object of shelld's code, registered by name (Caroline's backend); process: "cmd" (and "cwd", "env"), started and watched here;
                                                child: a process that another service starts (WhatsApp's sidecar, signal-cli: her backend does it, per account); it is found among the
                                                descendants of "parent" by "match" and is listed, measured and budgeted on its own; its parent's measure is without it
     "module": "caroline",           (host module) the registered name
     "parent": "caroline-backend",   (host child) the service whose process tree holds it
     "match": {"argv": "index.js", "env": {"WHATSAPP_AUTH_DIR": "auth_owner"}},   (host child) how to know it: the command line holds every "argv" string (a string or a list), the
                                     environment holds every key with a value that contains the given text; the topmost process that fits is the service
     "start": "system" | "open" | "manual",   system: it runs from the start of the system (a module starts itself once there is an account and a network);
                                              open: at the first open of an app that needs it ("required_by"), and then it stays; manual: only by the API
     "required_by": ["communicator"],  the apps whose opening starts it
     "priority": 0..100,             when the memory runs out the lowest priority is stopped first; 90 and above is never stopped (messages, calls)
     "memory_mb": 350,               the budget the service promises: measured PSS above it is reported (over_budget)
     "sleep": true | false,          true: it runs while the console sleeps (messages, mail, calls); false: it is paused with the apps
     "port": 48801,                  (host process) the port the service listens on: {port} in "cmd" and INTELLICAT_SERVICE_PORT are filled in; the manifest's "endpoints"
                                     are passed on to it (/api/app/<app>/<path>)
     "takeover": true | false}       it may take over the shared window (an incoming call): POST /api/window/takeover

The manager measures the PSS of every service (its process tree), the total of the device's processes, and when the total passes the limit it stops
services of low priority, one at a time; a service stopped for memory comes back by itself when there is room again.
"""
import ctypes, json, os, shutil, signal, subprocess, threading, time
import nativeproc

BACKOFF_S = (3, 6, 12, 24, 48, 96)
STABLE_S = 120
TICK_S = 5
ROOM_MARGIN_MB = 100  # a service stopped for memory returns when its budget fits under the limit by this much, twice in a row
NEVER_STOP = 90       # priority from which a service is never stopped for memory


def _die_with_parent():
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG (the process's own libc: "libc.so.6" on Linux, "libc.so" on Android)


def _ppids():
    out = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    out[int(d)] = int(f.read().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                pass
    return out


def tree(roots, ppids=None):
    """The pids of the roots and all their descendants."""
    ppids = ppids or _ppids()
    kids = {}
    for p, pp in ppids.items():
        kids.setdefault(pp, []).append(p)
    found = [r for r in roots if r in ppids]
    for p in found:
        found.extend(c for c in kids.get(p, []) if c not in found)
    return found


def pss_mb(pids):
    total = 0
    for p in pids:
        try:
            with open(f"/proc/{p}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss:"):
                        total += int(line.split()[1])
        except (OSError, ValueError):
            pass
    return total / 1024


def _matches(pid, match):
    """Does the process fit the "match" of a child service (see the head of this file)?"""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().replace(b"\0", b" ").decode(errors="replace")
        want = match.get("argv") or []
        if any(a not in argv for a in ([want] if isinstance(want, str) else want)):
            return False
        env = match.get("env") or {}
        if env:
            with open(f"/proc/{pid}/environ", "rb") as f:
                have = dict(kv.split("=", 1) for kv in f.read().decode(errors="replace").split("\0") if "=" in kv)
            if any(text not in have.get(k, "") for k, text in env.items()):
                return False
    except OSError:
        return False
    return bool(match.get("argv") or match.get("env"))


def find_child(parent_pid, match, ppids):
    """The topmost process among the descendants of parent_pid that fits `match`, or None."""
    hits = {p for p in tree([parent_pid], ppids) if p != parent_pid and _matches(p, match)}
    top = [p for p in hits if ppids.get(p) not in hits]
    return min(top) if top else None


class Service:
    def __init__(self, spec, app=""):
        self.id = spec["id"]
        self.name = spec.get("name", self.id)
        self.app = app
        self.host = spec.get("host", "process")
        self.module = spec.get("module", self.id)
        self.start_policy = spec.get("start", "manual")
        self.required_by = list(spec.get("required_by", []))
        self.priority = int(spec.get("priority", 50))
        self.memory_mb = float(spec.get("memory_mb", 0))
        self.privileged = bool(spec.get("privileged", False))  # a part of the system itself: never stopped for memory, always runs in sleep, may take the window
        self.sleep = bool(spec.get("sleep", False)) or self.privileged
        self.takeover = bool(spec.get("takeover", False)) or self.privileged
        if self.privileged:
            self.priority = 100
        self.port = spec["port"] + int(os.environ.get("INTELLICAT_PORT_OFFSET") or 0) if spec.get("port") else spec.get("port")  # (an instance name shifts the ports, scripts/lib.sh)
        self.cmd, self.cwd, self.env = spec.get("cmd"), spec.get("cwd"), spec.get("env") or {}
        self.parent, self.match, self.child_pid = spec.get("parent"), spec.get("match") or {}, None   # host child
        self.proc = None
        self.should_run = self.start_policy == "system"
        self.stopped_for = None      # None | "memory" | "manual"
        self.restarts, self.started_at, self.pause = 0, 0.0, 0
        self.pss = 0.0
        self.room_ticks = 0

    def info(self, running, pid):
        return {"id": self.id, "name": self.name, "app": self.app, "system": not self.app, "privileged": self.privileged,
                "host": self.host, "port": self.port, "start": self.start_policy, "priority": self.priority,
                "memory_mb": self.memory_mb, "sleep": self.sleep, "takeover": self.takeover, "required_by": self.required_by,
                "state": "running" if running else ("idle" if self.host == "child" else "stopped-for-memory" if self.stopped_for == "memory" else "stopped"),
                "pid": pid if running else None, "pss_mb": round(self.pss, 1), "over_budget": bool(self.memory_mb and self.pss > self.memory_mb),
                "restarts": self.restarts, "parent": self.parent, "stoppable": self.host != "child"}


class Manager:
    def __init__(self, state_dir, log=print, device_roots=lambda: [], limit_mb=2048):
        """device_roots() -> pids whose trees are the device's processes (shelld, Weston, the launcher's cog)."""
        self.state, self.log, self.device_roots, self.limit_mb = state_dir, log, device_roots, limit_mb
        self.services = {}
        self.modules = {}            # name -> {"start": fn, "stop": fn, "pid": fn}
        self.total_mb = 0.0
        self.asleep = False
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None

    # -- registry -----------------------------------------------------------------------------------------
    def register_module(self, name, start, stop, pid):
        self.modules[name] = {"start": start, "stop": stop, "pid": pid}

    def add(self, spec, app=""):
        with self.lock:
            s = Service(spec, app)
            old = self.services.get(s.id)
            if old and old.host != "child":
                self._stop(old, "manual")
            self.services[s.id] = s
        return s

    def remove_app(self, app_id):
        """An app is removed: its services are stopped and forgotten."""
        with self.lock:
            for sid in [k for k, s in self.services.items() if s.app == app_id]:
                self._stop(self.services[sid], "manual")
                del self.services[sid]

    def load(self, specs):
        """specs: [(app id, service spec)] collected from the manifests."""
        for app, spec in specs:
            self.add(spec, app)

    def start_thread(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="services")
            self._thread.start()
        for s in list(self.services.values()):
            if s.start_policy == "system" and s.host == "process":
                self.ensure(s.id)

    # -- start and stop -----------------------------------------------------------------------------------
    def _pid(self, s):
        if s.host == "child":
            return s.child_pid if s.child_pid and os.path.exists(f"/proc/{s.child_pid}") else None
        if s.host == "module":
            m = self.modules.get(s.module)
            try:
                return m["pid"]() if m else None
            except Exception:
                return None
        return s.proc.pid if s.proc and s.proc.poll() is None else None

    def ensure(self, sid):
        """Start the service (and keep it running). Raises KeyError for an unknown one."""
        with self.lock:
            s = self.services[sid]
            if s.host == "child":   # its parent starts it (her backend, when an account is linked): to ask for it is to ask for the parent
                if s.parent in self.services:
                    self.ensure(s.parent)
                return
            s.should_run, s.stopped_for, s.room_ticks = True, None, 0
            if s.host == "module":
                m = self.modules.get(s.module)
                if m:
                    m["start"]()
                return
            s.next_try = 0
        # a process is started by the manager's own long-lived thread (the kernel's parent-death signal follows the thread that spawned it, and a request's
        # thread ends at once); the caller waits a moment for it
        self._wake.set()
        for _ in range(60):
            if self._pid(s) is not None:
                break
            time.sleep(0.05)

    def stop(self, sid, reason="manual"):
        with self.lock:
            s = self.services[sid]
            if s.host == "child":
                # her loop would start the process again within seconds (it runs while an account is linked), and the messages are never to be touched:
                # a child is measured and budgeted here, it is stopped by the adapter's own code (unlink, cancel)
                raise ValueError(f"{sid} is a child of {s.parent}: it is started and stopped by its own code")
            self._stop(s, reason)

    def _stop(self, s, reason):
        s.should_run = False
        s.stopped_for = reason
        if s.host == "module":
            m = self.modules.get(s.module)
            if m:
                m["stop"]()
            return
        proc, s.proc = s.proc, None
        if isinstance(proc, nativeproc.NativePart):
            proc.terminate()
        elif proc and proc.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    proc.wait(4)
                    break
                except subprocess.TimeoutExpired:
                    pass

    def _spawn(self, s):
        if not s.cmd:
            return
        env = dict(os.environ)
        env.update({k: str(v) for k, v in s.env.items()})
        env["INTELLICAT_SERVICE_ID"] = s.id
        cmd = s.cmd
        if s.port:
            env["INTELLICAT_SERVICE_PORT"] = str(s.port)
            cmd = [c.replace("{port}", str(s.port)) for c in cmd]
        if nativeproc.is_part(cmd):   # a store package's native part on Android: loaded by the host, not run
            s.proc = nativeproc.NativePart("svc-" + s.id, cmd, {k: v for k, v in env.items() if k.startswith("INTELLICAT_") or k in s.env})
            s.started_at = time.time()
            self.log(f"services: {s.id} started as a native part")
            return
        # the program by its full path: the arm64 build under the x86 emulator's translation cannot start a program named bare ("Unable to get realpath of python3")
        found = shutil.which(cmd[0], path=env.get("PATH")) if os.sep not in cmd[0] else None
        cmd = [found, *cmd[1:]] if found else cmd
        log = open(os.path.join(self.state, f"service-{s.id}.log"), "ab")
        s.proc = subprocess.Popen(cmd, cwd=s.cwd or None, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=_die_with_parent)
        s.started_at = time.time()
        self.log(f"services: {s.id} started, pid {s.proc.pid}")

    def required_by(self, app_id):
        """An app is opened: the services that need it start (and stay)."""
        for s in list(self.services.values()):
            if app_id in s.required_by and s.stopped_for != "manual" and s.start_policy in ("open", "system"):
                try:
                    self.ensure(s.id)
                except Exception as e:  # opening an app never fails for a service
                    self.log(f"services: {s.id} did not start for {app_id}: {e}")

    # -- sleep --------------------------------------------------------------------------------------------
    def sleep_now(self, on):
        """The console sleeps: the services that do not run in sleep are paused with the apps; they go on when it wakes."""
        with self.lock:
            self.asleep = on
            for s in self.services.values():
                if s.sleep:
                    continue
                pid = self._pid(s)
                if pid is None:
                    continue
                try:
                    if s.host == "child":   # it shares the process group of its parent: only its own tree is paused
                        for p in tree([pid]):
                            os.kill(p, signal.SIGSTOP if on else signal.SIGCONT)
                    else:
                        os.killpg(os.getpgid(pid), signal.SIGSTOP if on else signal.SIGCONT)
                except (ProcessLookupError, PermissionError):
                    pass

    # -- watching -----------------------------------------------------------------------------------------
    def _loop(self):
        while True:
            self._wake.wait(TICK_S)
            self._wake.clear()
            try:
                self.tick()
            except Exception as e:  # the manager must survive anything
                self.log(f"services: tick failed: {e}")

    def tick(self):
        with self.lock:
            ppids = _ppids()
            for s in self.services.values():
                pid = self._pid(s)
                s.pss = pss_mb(tree([pid], ppids)) if pid else 0.0
                # a process service that exited on its own comes back with a growing pause
                if s.host == "process" and s.should_run and pid is None and not self.asleep:
                    if s.proc is not None:
                        s.restarts += 1
                        s.proc = None
                        s.pause = min(s.pause + 1, len(BACKOFF_S))
                        s.next_try = time.time() + BACKOFF_S[s.pause - 1]
                        self.log(f"services: {s.id} exited; again in {BACKOFF_S[s.pause - 1]} s")
                    elif time.time() >= getattr(s, "next_try", 0):
                        self._spawn(s)
                elif pid and time.time() - s.started_at > STABLE_S:
                    s.pause = 0
            # the children: found in the tree of their parent and measured on their own; the parent's figure is then without them
            claimed = {}
            for s in self.services.values():
                if s.host != "child":
                    continue
                parent = self.services.get(s.parent)
                ppid = self._pid(parent) if parent else None
                s.child_pid = find_child(ppid, s.match, ppids) if ppid else None
                s.pss = pss_mb(tree([s.child_pid], ppids)) if s.child_pid else 0.0
                if s.child_pid:
                    claimed.setdefault(parent.id, set()).update(tree([s.child_pid], ppids))
            for pid_, gone in claimed.items():
                p = self.services[pid_]
                p.pss = pss_mb([x for x in tree([self._pid(p)], ppids) if x not in gone])
            self.total_mb = pss_mb(tree([p for p in self.device_roots() if p], ppids))
            self._memory(ppids)

    def _memory(self, ppids):
        if self.total_mb > self.limit_mb:
            victims = [s for s in self.services.values() if self._pid(s) and s.priority < NEVER_STOP and not s.privileged and s.host != "child"]
            if victims:
                v = min(victims, key=lambda s: (s.priority, -s.pss))
                self.log(f"services: {self.total_mb:.0f} MB over the limit {self.limit_mb:.0f} MB: stopping {v.id} (priority {v.priority}, {v.pss:.0f} MB)")
                self._stop(v, "memory")
            return
        # room again: the stopped-for-memory services come back, the most important first
        for s in sorted([s for s in self.services.values() if s.stopped_for == "memory"], key=lambda s: -s.priority):
            need = s.memory_mb or 50
            if self.total_mb + need + ROOM_MARGIN_MB <= self.limit_mb:
                s.room_ticks += 1
                if s.room_ticks >= 2:
                    self.log(f"services: room again ({self.total_mb:.0f} MB of {self.limit_mb:.0f}): {s.id} comes back")
                    try:
                        self.ensure(s.id)
                    except Exception as e:
                        self.log(f"services: {s.id} did not come back: {e}")
                    return  # one at a time: the next tick measures again
            else:
                s.room_ticks = 0

    def status(self):
        with self.lock:
            return {"limit_mb": self.limit_mb, "total_mb": round(self.total_mb, 1), "asleep": self.asleep,
                    "services": [s.info(self._pid(s) is not None, self._pid(s)) for s in sorted(self.services.values(), key=lambda s: -s.priority)]}

    def takeover_allowed(self, sid):
        s = self.services.get(sid)
        return bool(s and s.takeover)
