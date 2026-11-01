# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The call engine of Ratatosk on Android: the same engine as runtime/ratatosk_call_engine.py, but the media runs in a WebView.

Android has no LiveKit for Python, and its WebView already has WebRTC, so the engine is a page (apps/ratatosk/call_engine.html with `livekit-client`) in a hidden layer of the host.
This class stands in for the subprocess that Calls._join starts on the other platforms and has the part of its interface that Calls uses: `stdin.write` / `flush` (the
commands `mute`, `unmute`, `camera on|off`, `hangup`), `stdout` (the events as JSON lines), `poll`, `wait`, `kill`. The page and this class meet over shelld's HTTP API
(`call-engine-config`, `call-engine-poll`, `call-engine-event`, `call-engine-frame`), so the token is not in the page's address.
"""
import base64, json, os, queue, subprocess, threading, time

import androidhost

LAYER = "ratatosk-call"
POLL_WAIT_S = 20.0           # how long the page's poll for commands is held open
PAGE = "/apps/ratatosk/call_engine.html"


class WebEngine:
    def __init__(self, url, token, mode, outgoing, frames, base):
        self.cfg = {"url": url, "token": token, "video": mode == "video", "ringback": bool(outgoing)}
        self._frames = frames            # (remote path, local path) or None: where the pictures are written for serve_frames
        self._cmds, self._events = queue.Queue(), queue.Queue()
        self._done = threading.Event()
        self._started = time.time()
        self._seen_page = False
        self.stdin = self
        self.stdout = self._lines()
        self.layer = androidhost.Layer(LAYER, base + PAGE, transparent=True, microphone=True)
        threading.Thread(target=self._watch, daemon=True, name="ratatosk-webengine").start()

    # ---- the interface of a subprocess (what Calls uses) ----------------------------------------------------------------
    def write(self, line):
        for l in line.splitlines():
            if l:
                self._cmds.put(l)
                if l == "hangup":
                    threading.Timer(2.5, self.kill).start()    # the page says `bye` when it has left the room; if it does not, the layer is closed all the same

    def flush(self):
        pass

    def poll(self):
        return 0 if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("webengine", timeout)
        return 0

    def kill(self):
        if self._done.is_set():
            return
        self._done.set()
        self.layer.terminate()          # the microphone is released with the page
        self._events.put(None)

    def _lines(self):
        while True:
            line = self._events.get()
            if line is None:
                return
            yield line

    def _watch(self):
        """The page never reached us, or the layer was closed under us: the call has no media then."""
        while not self._done.is_set():
            time.sleep(1.0)
            if not self._seen_page and time.time() - self._started > 15:
                self._events.put(json.dumps({"ev": "error", "message": "the call page did not start"}))
                self._seen_page = True
            if self.layer.poll() is not None and not self._done.is_set():
                self._done.set()
                self._events.put(json.dumps({"ev": "disconnected", "reason": "the call page was closed"}))
                self._events.put(None)

    # ---- the API the page uses ---------------------------------------------------------------------------------------------
    def config(self):
        self._seen_page = True
        return self.cfg

    def next_command(self):
        """The commands waiting for the page (a long poll: an empty list after POLL_WAIT_S)."""
        out = []
        try:
            out.append(self._cmds.get(timeout=POLL_WAIT_S))
            while True:
                out.append(self._cmds.get_nowait())
        except queue.Empty:
            pass
        return out

    def event(self, ev):
        if not isinstance(ev, dict) or not isinstance(ev.get("ev"), str):
            return
        if ev["ev"] == "bye":
            self.kill()
            return
        self._events.put(json.dumps(ev))

    def frame(self, which, data):
        """A picture of the call (JPEG, base64): written whole or not at all, as the engine of the other platforms writes it."""
        if not self._frames or which not in ("remote", "local") and not (which.startswith("remote-") and which[7:] in ("1", "2", "3")):
            return
        path = self._frames[1] if which == "local" else self._frames[0]
        if which.startswith("remote-"):
            stem, ext = os.path.splitext(self._frames[0])
            path = f"{stem}-{which[7:]}{ext}"
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            return
        if len(raw) > 2_000_000 or raw[:2] != b"\xff\xd8":
            return
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
