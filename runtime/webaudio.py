# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The sound of the simulator for a browser on another computer: the hosted simulator.

A local simulator plays into the sound server of the PC (WSLg) and records from its microphone, so nothing is needed there. A hosted machine has a sound server of its own
(hosted/image/pulse-ic.service, scripts/remote-pulse.sh) with two virtual devices: the sink the apps play into, and the sink "webmic" whose monitor (remapped as the source
"webmic_in") is the default source of the machine, which every recorder of the device uses (shelld's arecord, the call engine, WebKit's getUserMedia in Caroline's page).

    GET /audio  (WebSocket, the console page)
      server -> client  binary frames: PCM s16le mono 24000 Hz, 20 ms each: what the device plays (the monitor of the default sink)
      client -> server  text {"t": "mic", "on": true | false}; binary frames: PCM s16le mono 16000 Hz, the visitor's microphone, played into the sink "webmic"
    GET /api/audio      {"remote": the console should carry the sound (INTELLICAT_ONLINE or INTELLICAT_REMOTE_AUDIO), "speaker": bool, "mic": bool}

parec and pacat (pulseaudio-utils) do the work; the WebSocket framing is here because the console's one (display.py) carries text only.
"""
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import threading
import time

from display import WS_GUID   # (one copy of the protocol constant: a wrong one makes every browser refuse the socket)

OUT_RATE, MIC_RATE = 24000, 16000
CHUNK = OUT_RATE * 2 // 50                      # 20 ms of the speaker
MAX_FRAME = 1 << 20


def mic_sink():
    return os.environ.get("INTELLICAT_WEBMIC_SINK", "webmic")


def remote():
    """Whether the console page carries the sound: only a simulator the visitor does not sit at (the hosted one) does; a local one is heard on the PC already."""
    return os.environ.get("INTELLICAT_ONLINE") == "1" or os.environ.get("INTELLICAT_REMOTE_AUDIO") == "1"


def _pulse_env(env):
    e = dict(env or os.environ)
    if "PULSE_SERVER" not in e and os.path.exists("/mnt/wslg/PulseServer"):
        e["PULSE_SERVER"] = "unix:/mnt/wslg/PulseServer"
    return e


def _has_sink(name, env):
    try:
        out = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, env=env, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split("\t")[1:2] == [name] for line in out.splitlines())


def status(env=None):
    env = _pulse_env(env)
    tools = bool(shutil.which("parec") and shutil.which("pacat"))
    return {"remote": remote(), "speaker": tools, "mic": tools and _has_sink(mic_sink(), env)}


# ---- WebSocket: text and binary frames, both ways --------------------------------------------------------------------------------------------------------------------------
def handshake(handler):
    key = handler.headers.get("Sec-WebSocket-Key", "").encode()
    handler.protocol_version = "HTTP/1.1"
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1(key + WS_GUID).digest()).decode())
    handler.end_headers()
    handler.wfile.flush()


def _unmask(payload, mask):
    n = len(payload)
    if not n:
        return b""
    m = int.from_bytes((mask * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(payload, "big") ^ m).to_bytes(n, "big")


def frames(rfile):
    """(opcode, payload) of every frame from a client until it closes; fragments are joined."""
    def read(n):
        d = rfile.read(n)
        if len(d) < n:
            raise EOFError
        return d
    buf, op0 = b"", None
    try:
        while True:
            b1, b2 = read(2)
            fin, op, ln = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", read(8))[0]
            if ln > MAX_FRAME:
                return
            mask = read(4) if b2 & 0x80 else b"\0\0\0\0"
            data = _unmask(read(ln), mask)
            if op >= 8:
                yield op, data
                if op == 8:
                    return
                continue
            if op:
                op0, buf = op, data
            else:
                buf += data
            if fin:
                yield op0, buf
                buf = b""
    except (EOFError, OSError, ValueError):
        return


class Sender:
    """Writes frames to one client from several threads."""

    def __init__(self, wfile):
        self.wfile, self.lock, self.closed = wfile, threading.Lock(), False

    def send(self, op, data):
        n = len(data)
        head = bytes([0x80 | op, n]) if n < 126 else bytes([0x80 | op, 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | op, 127]) + struct.pack(">Q", n)
        with self.lock:
            if self.closed:
                return False
            try:
                self.wfile.write(head + data)
                self.wfile.flush()
                return True
            except OSError:
                self.closed = True
                return False


# ---- one console page ------------------------------------------------------------------------------------------------------------------------------------------------------
class Session:
    def __init__(self, handler, env=None, log=print):
        self.h, self.env, self.log = handler, _pulse_env(env), log
        self.out = Sender(handler.wfile)
        self.speaker = self.mic = None
        self.stopped = threading.Event()

    def _spawn(self, cmd, **kw):
        return subprocess.Popen(cmd, env=self.env, stderr=subprocess.DEVNULL, start_new_session=True, **kw)

    def _speak(self):
        """What the device plays, to the page, 20 ms at a time, until the page goes."""
        while not self.stopped.is_set():
            try:
                p = self.speaker = self._spawn(["parec", "--device=@DEFAULT_MONITOR@", "--raw", "--format=s16le", f"--rate={OUT_RATE}", "--channels=1",
                                                "--latency-msec=20", "--process-time-msec=10"], stdout=subprocess.PIPE)
            except OSError as e:
                self.log(f"audio: no speaker stream ({e})")
                return
            while not self.stopped.is_set():
                d = p.stdout.read(CHUNK)
                if not d:
                    break
                if not self.out.send(2, d):
                    self.stopped.set()
            self._kill(p)
            if not self.stopped.is_set():    # the sound server went away (restarted): try again in a while
                time.sleep(1)

    def _mic(self, on):
        if on and not self.mic:
            try:
                self.mic = self._spawn(["pacat", "--playback", f"--device={mic_sink()}", "--raw", "--format=s16le", f"--rate={MIC_RATE}", "--channels=1",
                                        "--latency-msec=40"], stdin=subprocess.PIPE)
                self.log("audio: the visitor's microphone is on")
            except OSError as e:
                self.log(f"audio: no microphone ({e})")
        elif not on and self.mic:
            self._kill(self.mic)
            self.mic = None
            self.log("audio: the visitor's microphone is off")
        self.out.send(1, json.dumps({"t": "mic", "on": bool(self.mic)}).encode())

    @staticmethod
    def _kill(p):
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(2)
            except subprocess.TimeoutExpired:
                p.kill()

    def run(self):
        handshake(self.h)
        self.out.send(1, json.dumps(dict(status(self.env), t="hello", rate=OUT_RATE, mic_rate=MIC_RATE)).encode())
        threading.Thread(target=self._speak, daemon=True, name="audio-out").start()
        try:
            for op, data in frames(self.h.rfile):
                if op == 2 and self.mic:
                    try:
                        self.mic.stdin.write(data)
                        self.mic.stdin.flush()
                    except (OSError, ValueError):
                        self._kill(self.mic)
                        self.mic = None
                elif op == 1:
                    try:
                        m = json.loads(data)
                    except ValueError:
                        continue
                    if m.get("t") == "mic":
                        self._mic(bool(m.get("on")))
                elif op == 9:
                    self.out.send(10, data)
                elif op == 8:
                    break
        finally:
            self.stopped.set()
            self.out.closed = True
            self._kill(self.speaker)
            self._kill(self.mic)
