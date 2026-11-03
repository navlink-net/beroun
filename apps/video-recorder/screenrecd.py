#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""screenrecd: the backend of the Video Recorder app (a service of OS Beroun, see manifest.json). It records the console's screen into an MP4 on the SD card
(<home>/Videos/Screen-YYYY-MM-DD-HHMMSS.mp4) and survives the close of the app, so the recording goes on while another app is on the screen.

The picture is the one shelld streams to the console window: the display in <state>/console_display, grabbed by ffmpeg (x11grab) and encoded as H.264; the sound is
what the console plays (the monitor of the default PulseAudio sink) as AAC, when there is such a source. The file is fragmented MP4, so it plays even if the
service is killed in the middle. Without a display to grab (a device whose compositor has no such source) the service says so (`can_record` false) and refuses to start.
Endpoints (passed on by the shell as /api/app/video-recorder/<path>): GET status, GET list, POST start {delay?, audio?}, POST stop.
"""
import datetime as dt, json, os, shutil, signal, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("INTELLICAT_SERVICE_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 48806))
SHELL = f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}"
STATE = os.environ.get("INTELLICAT_STATE", os.path.expanduser("~/.intellicat/run"))
HOME = os.environ.get("INTELLICAT_HOME", "/media/sdcard")
FOLDER = "Videos"
SIZE, FPS = os.environ.get("RECORDER_SIZE", "640x480"), 30
MAX_S = 30 * 60              # one recording ends by itself after this long
MIN_FREE = 200 * 1024 * 1024  # no recording starts on a card with less room than this
MAX_DELAY_S = 10
LOCK = threading.RLock()
REC = {"state": "idle", "file": None, "started": 0, "audio": False, "error": None, "proc": None, "timer": None}   # state: idle | waiting | recording


def shell(path, body):
    req = urllib.request.Request(SHELL + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-IntelliCat-Service-Id": os.environ.get("INTELLICAT_SERVICE_ID", "screenrecd")})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read() or b"{}")
    except OSError:
        return {}


def notify(text, level="info"):
    shell("/api/notify", {"text": text, "app": "Video Recorder", "level": level})


def display():
    try:
        return open(os.path.join(STATE, "console_display")).read().strip() or None
    except OSError:
        return None


def sound_source():
    """The monitor of the default sink (what the console plays), or None where there is no PulseAudio."""
    if not shutil.which("pactl"):
        return None
    try:
        sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return sink + ".monitor" if sink else None


def can_record():
    return bool(display() and shutil.which("ffmpeg"))


def out_dir():
    return os.path.join(HOME, FOLDER)


def now_text():
    return dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")


def status():
    with LOCK:
        t = {"state": REC["state"], "can_record": can_record(), "sound": bool(sound_source()), "error": REC["error"], "max_s": MAX_S, "folder": FOLDER}
        if REC["state"] == "recording":
            t.update(file=REC["file"], elapsed=int(time.time() - REC["started"]), audio=REC["audio"])
        return t


def listing():
    d = out_dir()
    items = []
    try:
        for n in os.listdir(d):
            if n.lower().endswith(".mp4"):
                st = os.stat(os.path.join(d, n))
                items.append({"name": n, "path": f"{FOLDER}/{n}", "bytes": st.st_size, "mtime": int(st.st_mtime)})
    except OSError:
        pass
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def _begin(audio):
    """Starts ffmpeg now (after the delay)."""
    with LOCK:
        if REC["state"] != "waiting":
            return
        disp = display()
        os.makedirs(out_dir(), exist_ok=True)
        name = f"Screen-{now_text()}.mp4"
        path = os.path.join(out_dir(), name)
        src = sound_source() if audio else None
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", str(FPS), "-video_size", SIZE, "-draw_mouse", "0", "-i", disp]
        if src:
            cmd += ["-f", "pulse", "-i", src]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p"]
        if src:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof", path]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            REC.update(state="idle", error=f"cannot start ffmpeg: {e}")
            notify(REC["error"], "error")
            return
        REC.update(state="recording", file=f"{FOLDER}/{name}", started=time.time(), audio=bool(src), error=None, proc=proc)
        threading.Thread(target=_watch, args=(proc, path), daemon=True).start()
        REC["timer"] = threading.Timer(MAX_S, stop, kwargs={"why": "the time limit"})
        REC["timer"].daemon = True
        REC["timer"].start()
    notify("Screen recording started")


def _watch(proc, path):
    """ffmpeg ended by itself (the display went away, the disk is full): the recording is over."""
    err = proc.stderr.read().decode(errors="replace").strip()
    proc.wait()
    with LOCK:
        if REC["proc"] is proc:
            REC.update(state="idle", proc=None, error=(err.splitlines()[-1] if err else "the recorder stopped"))
            if REC["timer"]:
                REC["timer"].cancel()
    if err:
        notify("Screen recording stopped: " + err.splitlines()[-1], "error")


def start(delay=0, audio=True):
    with LOCK:
        if REC["state"] != "idle":
            return {"error": "already recording", "kind": "busy"}, 409
        if not can_record():
            return {"error": "this screen cannot be recorded here (no display to grab, or no ffmpeg)", "kind": "unsupported"}, 501
        os.makedirs(out_dir(), exist_ok=True)
        if shutil.disk_usage(out_dir()).free < MIN_FREE:
            return {"error": "not enough room on the card (200 MB are needed)", "kind": "no_space"}, 507
        delay = max(0, min(MAX_DELAY_S, float(delay or 0)))
        REC.update(state="waiting", error=None)
    if delay:
        t = threading.Timer(delay, _begin, args=(bool(audio),))
        t.daemon = True
        t.start()
    else:
        _begin(bool(audio))
    return {"ok": True, "delay": delay}, 200


def stop(why=""):
    with LOCK:
        proc, name = REC["proc"], REC["file"]
        if REC["state"] == "waiting":
            REC.update(state="idle")
            return {"ok": True, "file": None}, 200
        if REC["state"] != "recording" or not proc:
            return {"error": "not recording", "kind": "idle"}, 409
        started = REC["started"]
        REC.update(state="idle", proc=None)
        if REC["timer"]:
            REC["timer"].cancel()
    try:
        proc.stdin.write(b"q")       # ffmpeg's own way to finish: the file is closed properly
        proc.stdin.flush()
    except OSError:
        pass
    try:
        proc.wait(8)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(4)
        except subprocess.TimeoutExpired:
            proc.kill()
    path = os.path.join(HOME, name)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    notify("Screen recording saved: " + os.path.basename(name) + (f" ({why})" if why else ""))
    return {"ok": True, "file": name, "bytes": size, "seconds": int(time.time() - started)}, 200


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0].strip("/")
        if p == "status":
            return self._send(status())
        if p == "list":
            return self._send({"items": listing()})
        self._send({"error": "no such path"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0].strip("/")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send({"error": "bad JSON"}, 400)
        if p == "start":
            r, code = start(body.get("delay", 0), body.get("audio", True))
            return self._send(r, code)
        if p == "stop":
            r, code = stop()
            return self._send(r, code)
        self._send({"error": "no such path"}, 404)


def main():
    def bye(*_):
        stop("the service ended")
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
