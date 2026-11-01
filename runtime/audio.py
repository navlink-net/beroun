# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The sound of the shell: playing a short file, recording the voice, the volume. One small layer so that the callers never name a program:

  Linux    `aplay`, `arecord` and `pactl`, as the device and the simulator always had them.
  Android  the Kotlin host over the bridge (androidhost, SystemOps.kt): MediaPlayer, AudioRecord and AudioManager's volume.

play(path)  starts a file and returns at once;  Recorder().start(path, seconds) / .stop() writes a 16 kHz mono 16-bit WAV;  volume_get() -> percent | None;  volume_set(percent).
"""
import os, re, signal, subprocess

import androidhost


def env():
    e = dict(os.environ)
    if os.path.exists("/mnt/wslg/PulseServer"):
        e.setdefault("PULSE_SERVER", "unix:/mnt/wslg/PulseServer")
    return e


def play(path):
    if androidhost.ENABLED:
        try:
            androidhost.call("audio_play", path=path)
        except androidhost.BridgeError:
            pass
        return
    try:
        subprocess.Popen(["aplay", "-q", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


class Recorder:
    def __init__(self):
        self.proc = None

    def start(self, path, seconds):
        if androidhost.ENABLED:
            androidhost.call("audio_record_start", path=path, seconds=int(seconds))
            return
        self.proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", "-d", str(seconds), path],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def stop(self):
        """Ends the recording; the file is complete when this returns."""
        if androidhost.ENABLED:
            try:
                androidhost.call("audio_record_stop")
            except androidhost.BridgeError:
                pass
            return
        if self.proc is None:
            return
        try:
            self.proc.send_signal(signal.SIGINT)   # arecord finishes the WAV header on SIGINT
            self.proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            self.proc.kill()


def volume_get():
    """The volume of the default sound output in percent, None when there is no sound server."""
    if androidhost.ENABLED:
        try:
            return int(androidhost.call("volume_get")["percent"])
        except (androidhost.BridgeError, KeyError, ValueError):
            return None
    try:
        out = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], capture_output=True, text=True, env=env(), timeout=3).stdout
        m = re.search(r"(\d+)%", out)
        return int(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def volume_set(percent):
    if androidhost.ENABLED:
        try:
            androidhost.call("volume_set", percent=int(percent))
        except androidhost.BridgeError:
            pass
        return
    subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"], env=env(), timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
