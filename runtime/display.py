# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Console-window display path: frame streaming and input injection (replaces VNC).

Frames:  ffmpeg x11grab of the private X server that Weston renders into -> MJPEG frames,
         pushed to browsers as multipart/x-mixed-replace (`/stream.mjpg`) with no JS decoding.
Input:   a tiny WebSocket endpoint (`/ws`) receives pointer / button messages and injects
         them into that X server with XTEST (pointer for Weston clients) and into the
         virtual evdev devices (gamepad, touchscreen) for apps that read evdev directly.
"""
import base64, hashlib, json, os, struct, subprocess, threading, time

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
KEYSYMS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right", "a": "Return", "b": "Escape",
           "x": "space", "y": "Tab", "l1": "Prior", "r1": "Next", "l2": "Home", "r2": "End",
           "start": "F1", "select": "F2", "mode": "F3"}
# the cgroup of the simulator's own processes (scripts/lib.sh): the console window's ffmpeg is not charged to the device's memory
SIM_CGROUP = os.environ.get("INTELLICAT_CGROUP_SIM", "")


def to_simulator_group():
    """preexec_fn of a process the device will not have: it leaves the device's cgroup before it allocates anything."""
    if SIM_CGROUP:
        try:
            with open(os.path.join(SIM_CGROUP, "cgroup.procs"), "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass


class Streamer:
    """One shared ffmpeg process; every client gets the newest frame (slow clients drop frames)."""

    def __init__(self, display, fps=60, quality=4, size="640x480"):
        self.display, self.fps, self.quality, self.size = display, fps, quality, size
        self.frame, self.seq = b"", 0
        self.blank, self.black = False, None   # the screen is off (sleep): what is served is a black picture
        self.cond = threading.Condition()
        self.proc = None
        self.lock = threading.Lock()

    def _start(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            self.proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-fflags", "nobuffer", "-f", "x11grab", "-framerate", str(self.fps),
                 "-video_size", self.size, "-draw_mouse", "0", "-i", self.display,
                 "-c:v", "mjpeg", "-q:v", str(self.quality), "-flush_packets", "1", "-f", "image2pipe", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, preexec_fn=to_simulator_group)
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        buf = b""
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:  # JPEG: starts FFD8, ends FFD9 (FFD9 cannot occur inside entropy data: FF is stuffed)
                end = buf.find(b"\xff\xd9")
                if end < 0:
                    break
                frame, buf = buf[:end + 2], buf[end + 2:]
                start = frame.find(b"\xff\xd8")
                if start >= 0:
                    with self.cond:
                        self.frame, self.seq = (self.black if self.blank and self.black else frame[start:]), self.seq + 1
                        self.cond.notify_all()

    def resize(self, size):
        """The screen has another size (the external screen's mode): the grab starts again at that size; the clients stay connected and get the new frames."""
        with self.lock:
            self.size, self.black = size, None
            proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._start()

    def set_blank(self, on):
        """Screen off / on: while it is off every frame served is black (the real screen is simply not lit)."""
        if on and self.black is None:
            r = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={self.size}", "-frames:v", "1",
                                "-c:v", "mjpeg", "-f", "image2pipe", "-"], capture_output=True, timeout=10)
            self.black = r.stdout or None
        self.blank = bool(on)
        with self.cond:
            if self.blank and self.black:
                self.frame, self.seq = self.black, self.seq + 1
                self.cond.notify_all()

    def serve(self, handler):
        self._start()
        handler.send_response(200)
        handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        last = 0
        try:
            while True:
                with self.cond:
                    if not self.cond.wait_for(lambda: self.seq != last, timeout=5):
                        if self.proc.poll() is not None:
                            self._start()
                        continue
                    frame, last = self.frame, self.seq
                handler.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame))
                handler.wfile.write(frame + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class XInput:
    """XTEST injection into the console's private X server (where Weston's window lives)."""

    def __init__(self, display):
        self.name, self.d, self.lock, self.buttons = display, None, threading.Lock(), 0

    def _dpy(self):
        if self.d is None:
            from Xlib import display
            from Xlib.ext import xtest  # noqa: F401  (registers the extension)
            self.d = display.Display(self.name)
        return self.d

    def _fake(self, kind, detail=0, x=0, y=0):
        from Xlib import X
        from Xlib.ext import xtest
        d = self._dpy()
        xtest.fake_input(d, {"move": X.MotionNotify, "bdown": X.ButtonPress, "bup": X.ButtonRelease,
                             "kdown": X.KeyPress, "kup": X.KeyRelease}[kind], detail, X.CurrentTime, X.NONE, x, y)
        d.flush()

    def pointer(self, x, y, down):
        with self.lock:
            self._fake("move", x=int(x), y=int(y))
            if down and not self.buttons:
                self._fake("bdown", 1)
            elif not down and self.buttons:
                self._fake("bup", 1)
            self.buttons = 1 if down else 0

    def move(self, x, y):
        """The pointer to a place (the external screen's mouse, made of a stick)."""
        with self.lock:
            self._fake("move", x=int(x), y=int(y))

    def button(self, n, down):
        """A mouse button: 1 left, 2 middle, 3 right."""
        with self.lock:
            self._fake("bdown" if down else "bup", int(n))

    def wheel(self, dx, dy):
        """One notch of the wheel: dy -1 up, +1 down, dx -1 left, +1 right. X buttons 4 and 5 are up and down; for the sideways ones Weston's x11 backend takes 6 as "to the
        right" and 7 as "to the left" (the other way round from the X convention: tests/extinput_test.py found it)."""
        n = 4 if dy < 0 else 5 if dy > 0 else 7 if dx < 0 else 6 if dx > 0 else 0
        if n:
            with self.lock:
                self._fake("bdown", n)
                self._fake("bup", n)

    def key(self, name, down):
        """A key by a console button's name or an X keysym name; an int is the keysym itself (a character of the Monitor window's keyboard)."""
        from Xlib import XK
        if isinstance(name, int):
            sym = name
        elif name in KEYSYMS or len(name) != 1:
            sym = XK.string_to_keysym(KEYSYMS.get(name, name))
        else:   # one character typed on the PC ("@", ".", "T"): its keysym is its code (Latin-1); a name lookup knows only "at", "period", ...
            sym = ord(name) if ord(name) < 0x100 else 0x1000000 + ord(name)
        if not sym:
            return
        with self.lock:
            d = self._dpy()
            code = d.keysym_to_keycode(sym)
            if not code:
                return
            # a character on the shifted level of its key ("@" on "2", capitals) is typed with Shift around it
            shift = d.keycode_to_keysym(code, 0) != sym and d.keycode_to_keysym(code, 1) == sym
            sc = d.keysym_to_keycode(XK.XK_Shift_L) if shift else 0
            if down and sc:
                self._fake("kdown", sc)
            self._fake("kdown" if down else "kup", code)
            if not down and sc:
                self._fake("kup", sc)


def ws_handshake(handler):
    key = handler.headers.get("Sec-WebSocket-Key", "").encode()
    accept = base64.b64encode(hashlib.sha1(key + WS_GUID).digest()).decode()
    handler.protocol_version = "HTTP/1.1"  # browsers reject a 101 on HTTP/1.0
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    handler.wfile.flush()


def ws_send(handler, text):
    """Send one text message (server -> client frames are not masked)."""
    data = text.encode("utf-8")
    n = len(data)
    head = bytes([0x81, n]) if n < 126 else bytes([0x81, 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x81, 127]) + struct.pack(">Q", n)
    handler.wfile.write(head + data)
    handler.wfile.flush()


def ws_messages(handler):
    """Yield decoded text messages from a client until it closes."""
    rf = handler.rfile

    def read(n):
        data = rf.read(n)
        if len(data) < n:
            raise EOFError
        return data
    try:
        while True:
            b1, b2 = read(2)
            opcode, masked, ln = b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", read(8))[0]
            mask = read(4) if masked else b"\0\0\0\0"
            payload = bytearray(read(ln))
            for i in range(len(payload)):
                payload[i] ^= mask[i & 3]
            if opcode == 8:
                return
            if opcode == 9:  # ping -> pong
                handler.wfile.write(bytes([0x8A, len(payload)]) + bytes(payload))
            elif opcode == 1:
                yield payload.decode("utf-8", "replace")
    except (EOFError, OSError):
        return
