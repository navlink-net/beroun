# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The client of the Kotlin host's bridge: shelld's Android mode. Instead of cog and cogctl, the windows are layers of the app's
stack of views, asked over JSON lines on a socket. INTELLICAT_ANDROID_BRIDGE is `unix:<path>` (the app's private socket, in the APK), `tcp:<host>:<port>` (the debug build) or `cmd:<command line>` (a program whose stdin and stdout are the
socket: development, with shelld on another computer, e.g. `cmd:adb.exe -s emulator-5570 shell run-as net.navlink.beroun nc -U files/bridge.sock`; no network port is opened)."""
import json
import time
import os
import shlex
import socket
import subprocess
import threading

SPEC = os.environ.get("INTELLICAT_ANDROID_BRIDGE", "")
ENABLED = bool(SPEC)
SHARED = "shared"   # the launcher's window, the layer of the host that is never closed

_lock = threading.Lock()
_sock = None
_file = None
_next_id = 0


class BridgeError(RuntimeError):
    pass


class _Pipe:
    """A program's stdin/stdout as the socket and its file."""
    def __init__(self, argv):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)

    def close(self):
        self.p.kill()

    def write(self, t):
        return self.p.stdin.write(t)

    def flush(self):
        self.p.stdin.flush()

    def readline(self):
        return self.p.stdout.readline()


def _connect():
    kind, _, rest = SPEC.partition(":")
    if kind == "cmd":
        pipe = _Pipe(shlex.split(rest))
        return pipe, pipe
    if kind == "unix":
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(10)
        s.connect(rest)
    elif kind == "tcp":
        host, _, port = rest.rpartition(":")
        s = socket.create_connection((host, int(port)), timeout=10)
    else:
        raise BridgeError(f"bad INTELLICAT_ANDROID_BRIDGE {SPEC!r}")
    return s, s.makefile("rw", encoding="utf-8", newline="\n")


def call(op, **args):
    """One request, its answer (a dict); raises BridgeError when the host says no or does not answer. One connection, kept; made again after an error."""
    global _sock, _file, _next_id
    with _lock:
        for attempt in (0, 1):
            try:
                if _sock is None:
                    _sock, _file = _connect()
                _next_id += 1
                _file.write(json.dumps(dict(args, op=op, id=_next_id)) + "\n")
                _file.flush()
                line = _file.readline()
                if not line:
                    raise OSError("the host closed the bridge")
                break
            except OSError as e:
                try:
                    _sock.close()
                except (OSError, AttributeError):
                    pass
                _sock = _file = None
                if attempt:
                    raise BridgeError(f"the host does not answer: {e}") from e
    r = json.loads(line)
    if not r.get("ok"):
        raise BridgeError(r.get("error", "failed"))
    return r


def window_open(url):
    """The shared window shows this page."""
    call("navigate", view=SHARED, url=url)


def app_open(app_id, url, transparent=False, microphone=False, kind=None):
    """The window of an app of its own: a layer above the others (a transparent one takes no touches). `kind` "browser": the host's browser view (tabs of WebViews under the page)."""
    extra = {"kind": kind} if kind else {}
    call("open", view=app_id, url=url, transparent=transparent, input=not transparent, microphone=microphone, **extra)


def app_close(app_id):
    call("close", view=app_id)


class Layer:
    """The window of an app of its own as a Popen, for the code that tracks apps by process (shelld.procs): a number stands for the pid, poll() asks the host
    whether the layer is still open (it is gone when the app closed itself, e.g. the player's MODE, or the host was restarted)."""
    _seq = [900000]    # above any pid of the phone: no signal ever reaches such a number
    CHECK_S = 1.0

    def __init__(self, app_id, url, transparent=False, microphone=False, kind=None):
        Layer._seq[0] += 1
        self.pid, self.app_id, self.returncode, self._checked = Layer._seq[0], app_id, None, time.time()
        app_open(app_id, url, transparent, microphone, kind)

    def poll(self):
        if self.returncode is None and time.time() - self._checked > self.CHECK_S:
            self._checked = time.time()
            try:
                if not any(l.get("id") == self.app_id for l in call("list").get("layers", [])):
                    self.returncode = 0
            except BridgeError:
                pass    # the host does not answer now: the layer is not known to be gone
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            self.returncode = 0
            try:
                app_close(self.app_id)
            except BridgeError:
                pass

    kill = terminate

    def wait(self, timeout=None):
        end = None if timeout is None else time.time() + timeout
        while self.poll() is None:
            if end is not None and time.time() > end:
                raise TimeoutError("layer")
            time.sleep(0.5)
        return self.returncode
