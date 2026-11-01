# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Caroline's Visual Mode on the device: her talking head, shown directly on the screen by ic-face-view (native/face/face_view.c).

ic-face-view is one process that renders the frames from the model on the SD card (<card>/models/CarolineA.xcfa, the only model), plays the
sound of the answer and draws every frame with an alpha channel in a window of its own over whatever is on the screen; the frames are timed
from the sound card's own position, so the face and the voice stay together. This module only starts it, hands it the audio of a spoken
answer (what her page sends with visual_speech_audio) and tells the voice view when the answer has been played.

Protocol with the process (a Unix socket, lines): PLAY <id> <audio file> / STOP / QUIT; READY <w> <h> <fps>, DONE <id> <1|0> [why].
"""
import json
import os
import socket
import subprocess
import threading
import time

import androidhost
import manifest


FACE_VIEW = "face"   # the layer and the library (libberoun_face.so) of the face in the Android host


class _NativePart:
    """The face as a native part of the Android host (androidhost native_start): the process behind it is the host's, this is its poll/wait/kill."""
    def poll(self):
        try:
            return None if androidhost.call("native_running", view=FACE_VIEW).get("running") else 0
        except androidhost.BridgeError:
            return 0

    def wait(self, timeout):
        end = time.time() + timeout
        while self.poll() is None:
            if time.time() > end:
                raise subprocess.TimeoutExpired("face", timeout)
            time.sleep(0.1)

    def kill(self):
        try:
            androidhost.call("native_stop", view=FACE_VIEW)
        except androidhost.BridgeError:
            pass


class Face:
    def __init__(self, sim_dir, state_dir, home_root, log=print):
        folder = os.path.join(sim_dir, "native", "face")   # a native part: native.json names its builds per target, {bin:...} is filled in for this device's
        try:
            spec = json.load(open(os.path.join(folder, "native.json"), encoding="utf-8"))
        except (OSError, ValueError):
            spec = {"binaries": {}}   # a tree without the face (a test's copy of the shell): no face, as with a missing build, never a shell that cannot start
        self.bin = manifest.fill_bin("{bin:ic-face-view}", spec.get("binaries") or {}, folder) or os.path.join(folder, "bin", manifest.device_target(), "ic-face-view")
        self.state, self.home, self.log = state_dir, home_root, log
        self.sock_path = os.path.join(state_dir, "face.sock")
        self.proc = None
        self.sock = None
        self.info = {"w": 0, "h": 0, "fps": 25}
        self.jobs = {}          # id -> {"state": playing|done|failed, "why": str, "file": path}
        self.lock = threading.RLock()

    def model_path(self):
        """The .xcfa of Visual Mode: CarolineA, the one model of the simulator (owner's decision; her own day-parity A/B and Peter are not used)."""
        path = os.path.join(self.home, "models", "CarolineA.xcfa")
        return path if os.path.isfile(path) else None

    def available(self):
        return (self.on_host() or os.path.isfile(self.bin)) and self.model_path() is not None

    def on_host(self):
        """True on Android: the face is a library of the APK that the native host runs, not a file of this tree."""
        return androidhost.ENABLED and manifest.device_target().startswith("android")

    def running(self):
        return self.proc is not None and self.proc.poll() is None and self.sock is not None

    # -- the process -------------------------------------------------------------------------------------------
    def start(self, env):
        """Starts the face over the screen (env: the environment of an app of the device: WAYLAND_DISPLAY and so on). False: no model or program."""
        with self.lock:
            if self.running():
                return True
            self.stop()
            model = self.model_path()
            if not model or not (self.on_host() or os.path.isfile(self.bin)):
                return False
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
            if self.on_host():
                try:
                    androidhost.call("native_start", view=FACE_VIEW, lib="beroun_face", args=[model, self.sock_path],
                                     env={"BEROUN_LOG": os.path.join(self.state, "face.log"), "BEROUN_FFMPEG": os.environ.get("BEROUN_FFMPEG", "ffmpeg")})
                except androidhost.BridgeError as e:
                    self.log(f"face: the host cannot run it: {e}")
                    return False
                self.proc = _NativePart()
            else:
                logf = open(os.path.join(self.state, "face.log"), "ab")
                # no PDEATHSIG (it fires when the THREAD that started the process ends): the face ends by itself when its socket closes
                self.proc = subprocess.Popen([self.bin, model, self.sock_path], env=env, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            for _ in range(150):
                try:
                    s.connect(self.sock_path)
                    break
                except OSError:
                    if self.proc.poll() is not None:
                        s.close()
                        self.log("face: the program ended while starting (see face.log)")
                        return False
                    time.sleep(0.1)
            else:
                s.close()
                return False
            self.sock = s
            threading.Thread(target=self._reader, args=(s,), daemon=True).start()
            self.log(f"face: started on {os.path.basename(model)}")
            return True

    def stop(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.sendall(b"QUIT\n")
                except OSError:
                    pass
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.wait(2)
                except Exception:
                    self.proc.kill()
            self.proc = None
            for j in self.jobs.values():
                if j["state"] == "playing":
                    j["state"], j["why"] = "failed", "the face is gone"

    def _reader(self, s):
        f = s.makefile("rb")
        try:
            for raw in f:
                p = raw.decode("utf-8", "replace").split()
                if not p:
                    continue
                if p[0] == "READY":
                    self.info = {"w": int(p[1]), "h": int(p[2]), "fps": int(p[3])}
                elif p[0] == "DONE" and len(p) >= 3:
                    with self.lock:
                        j = self.jobs.get(p[1])
                        if j:
                            j["state"] = "done" if p[2] == "1" else "failed"
                            j["why"] = " ".join(p[3:])
                            try:
                                os.unlink(j["file"])
                            except OSError:
                                pass
        except Exception as e:  # noqa: BLE001 - the service must not take shelld down
            self.log(f"face: reader stopped: {e}")
        with self.lock:
            if self.sock is s:
                self.sock = None
            for j in self.jobs.values():
                if j["state"] == "playing":
                    j["state"], j["why"] = "failed", "the face is gone"

    # -- answers -----------------------------------------------------------------------------------------------
    def play(self, job_id, audio_bytes):
        """Plays the answer with the face: render, sound and picture all in the face process. False: the face is not running."""
        with self.lock:
            if not self.running():
                return False
            path = os.path.join(self.state, f"face-{abs(hash(job_id)) % 10**9}.audio")
            with open(path, "wb") as fh:
                fh.write(audio_bytes)
            for old in [k for k, v in self.jobs.items() if v["state"] != "playing"]:   # finished ones are forgotten
                self.jobs.pop(old, None)
            self.jobs[job_id] = {"state": "playing", "why": "", "file": path}
            try:
                self.sock.sendall(f"PLAY {job_id} {path}\n".encode())
            except OSError:
                self.jobs[job_id]["state"] = "failed"
                return False
            return True

    def stop_play(self):
        with self.lock:
            if self.running():
                try:
                    self.sock.sendall(b"STOP\n")
                except OSError:
                    pass

    def job(self, job_id):
        with self.lock:
            j = self.jobs.get(job_id)
            return None if j is None else {"state": j["state"], "why": j["why"]}

    def status(self):
        return {"available": self.available(), "running": self.running(), **self.info}
