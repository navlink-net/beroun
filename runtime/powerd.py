#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""powerd - the power of the simulated console, a service of its own: sleep, power off, power on.

The power button is the POWER key (the owner's decision of 2026-10-27; first it was R1): an input device of its own, "IntelliCat Power Key",
the key code BTN_TRIGGER_HAPPY1 (not the real KEY_POWER: the host logind would power the WSL machine off), like the PMIC key of the real console. powerd reads it straight from evdev, so it works even when shelld hangs:

  short press   sleep / wake: shelld pauses the apps and blanks the screen (POST /api/power/sleep); the same press wakes it
  long press    (HOLD_S, 2 s, acted on while the button is still down) POWER OFF, forced: the farewell screen (the cat, native/screen/ic-screen) for
                a moment, then scripts/stop.sh and everything that is left is killed. powerd itself stays: it is the only thing "alive" in an
                off console
  off           the console window (web-shell/console.html) notices that shelld is gone, shows a black screen and sends the press of POWER to this
                service (HTTP, 127.0.0.1:8764, /button): a press powers the console ON again: scripts/start.sh, the boot screen (the cat) over
                the screen until the launcher is up

Standard library only. Log: <state>/powerd.log. Environment: POWERD_HOLD_S, POWERD_FAREWELL_S, INTELLICAT_STATE, INTELLICAT_API.
"""
import json
import os
import select
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.environ.get("BEROUN_TARGET") or SIM   # the folder of the target (the simulator, a device build) whose scripts/start.sh and stop.sh power it on and off
STATE = os.environ.get("INTELLICAT_STATE", os.path.expanduser("~/.intellicat/run"))
HOLD_S = float(os.environ.get("POWERD_HOLD_S", "2.0"))
FAREWELL_S = float(os.environ.get("POWERD_FAREWELL_S", "2.5"))
OFFSET = int(os.environ.get("INTELLICAT_PORT_OFFSET") or 0)   # a simulator with an instance name (scripts/lib.sh) has its own ports
API = os.environ.get("INTELLICAT_API", f"http://127.0.0.1:{8765 + OFFSET}/api")
PORT = int(os.environ.get("POWERD_PORT", 8764 + OFFSET))
SOCKET = os.environ.get("INTELLICAT_SOCKET", "intellicat")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/mnt/wslg/runtime-dir")
SCREEN_BIN = os.path.join(SIM, "native", "screen", "build", "ic-screen")
SCREEN_PNG = os.path.join(SIM, "web-shell", "screens", "cat.png")
DEVICE_NAME = "IntelliCat Power Key" + (" " + os.environ["INTELLICAT_INSTANCE"] if os.environ.get("INTELLICAT_INSTANCE") else "")
KEY_POWER, EV_KEY = 0x2C0, 1   # the code of vhw.py (never the real KEY_POWER: the host logind would power the WSL machine off)
EVENT = struct.Struct("<qqHHi")

power = {"state": "on", "ignore_term": False}          # on | off | booting; ignore_term: stop.sh is stopping powerd while it does the job
_busy = threading.Lock()         # one power transition at a time


def log(msg):
    line = f"{time.strftime('%F %T')} {msg}\n"
    try:
        with open(os.path.join(STATE, "powerd.log"), "a") as f:
            f.write(line)
    except OSError:
        pass


def find_device():
    """/dev/input/eventN of the virtual gamepad, None while it does not exist (shelld creates it)."""
    try:
        text = open("/proc/bus/input/devices").read()
    except OSError:
        return None
    for block in text.split("\n\n"):
        if f'Name="{DEVICE_NAME}"' in block:
            for part in block.split():
                if part.startswith("event"):
                    return "/dev/input/" + part
    return None


def shelld(path, body=b"{}"):
    req = urllib.request.Request(f"{API}{path}", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.read().decode()


def short_press():
    if power["state"] != "on":
        return
    try:
        log("short press: sleep/wake -> " + shelld("/power/sleep")[:60])
    except Exception as e:  # noqa: BLE001 - shelld may be the thing that hangs
        log(f"short press: shelld did not answer ({e})")


def show_screen(seconds=0):
    """The picture (the cat) over the whole screen, until it is killed or its time is up. Returns the process or None."""
    if not os.path.isfile(SCREEN_BIN) or not os.path.exists(os.path.join(RUNTIME, SOCKET)):
        return None
    env = dict(os.environ, WAYLAND_DISPLAY=SOCKET, XDG_RUNTIME_DIR=RUNTIME)
    try:
        return subprocess.Popen([SCREEN_BIN, SCREEN_PNG, str(seconds)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError as e:
        log(f"ic-screen: {e}")
        return None


def power_off():
    """The long press: the farewell screen for a moment, then everything is stopped, whatever state it is in."""
    if not _busy.acquire(blocking=False):
        return
    try:
        log("long press: power off")
        power["ignore_term"] = True   # stop.sh stops powerd too; this one finishes the job first
        try:
            shelld("/power/sleep", b'{"state": "awake"}')   # a console that is asleep is woken so that the farewell can be seen
        except Exception:  # noqa: BLE001
            pass
        farewell = show_screen(FAREWELL_S + 5)
        log("farewell screen shown" if farewell else "no farewell screen (no program or no screen)")
        time.sleep(FAREWELL_S if farewell else 0.2)
        try:
            subprocess.run(["bash", os.path.join(TARGET, "scripts", "stop.sh")], timeout=30, start_new_session=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa: BLE001
            log(f"stop.sh: {e}")
        if not os.environ.get("INTELLICAT_INSTANCE"):   # (with an instance name stop.sh has ended this simulator's processes: the patterns would end the others')
            for pat in ("runtime/shelld.py", "cog --platform", "ic-face-view", "ic-browser", "ic-screen", "weston", "Xvfb",
                        "ffmpeg -loglevel error -fflags nobuffer"):
                subprocess.run(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        power["state"] = "off"
        try:
            open(os.path.join(STATE, "powerd.pid"), "w").write(str(os.getpid()))   # stop.sh removed it: start.sh must not start a second powerd
        except OSError:
            pass
        power["ignore_term"] = False
        log("off")
    finally:
        _busy.release()


def power_on():
    """A press of POWER in an off console: the system starts again, the boot screen (the cat) stands over the screen until the launcher is up."""
    if power["state"] != "off" or not _busy.acquire(blocking=False):
        return
    try:
        power["state"] = "booting"
        log("power on")
        env = dict(os.environ, INTELLICAT_STATE=STATE)
        try:   # the same RAM as before the power off (start.sh --mem-limit)
            env["INTELLICAT_MEM_LIMIT"] = open(os.path.join(STATE, "mem_limit")).read().strip()
        except OSError:
            pass
        boot = subprocess.Popen(["bash", os.path.join(TARGET, "scripts", "start.sh"), "--console"], env=env, start_new_session=True,
                                stdout=open(os.path.join(STATE, "powerd-start.log"), "ab"), stderr=subprocess.STDOUT)
        screen = None
        for _ in range(300):   # Weston's socket appears within a couple of seconds: from then on the cat covers the screen
            if os.path.exists(os.path.join(RUNTIME, SOCKET)):
                screen = show_screen(60)
                log("boot screen shown" if screen else "no boot screen")
                break
            time.sleep(0.1)
        boot.wait(timeout=90)
        for _ in range(60):    # shelld up, then the launcher needs a few seconds to draw its first picture under the cat
            try:
                urllib.request.urlopen(f"{API}/health", timeout=1).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        # the launcher's window is newer than the cat and would cover it: the cat is put up once more over it, until the launcher has drawn
        for _ in range(40):
            if subprocess.run(["pgrep", "-f", "gapplication-app-id=com.intellicat.shell" + os.environ.get("INTELLICAT_APPID_SUFFIX", "") + " "], stdout=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(0.25)
        time.sleep(1.5)
        screen2 = show_screen(30)
        time.sleep(5)
        for sc in (screen2, screen):
            if sc and sc.poll() is None:
                sc.terminate()
        power["state"] = "on"
        log("on")
    except Exception as e:  # noqa: BLE001
        log(f"power on failed: {e}")
        power["state"] = "off"
    finally:
        _busy.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")   # the console page (another origin: shelld's) tells powerd about POWER when shelld is gone
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        self._send({"state": power["state"]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        if self.path == "/button" and body.get("name") == "power" and body.get("on") and power["state"] == "off":
            threading.Thread(target=power_on, daemon=True).start()
        self._send({"state": power["state"]})


def main():
    os.makedirs(STATE, exist_ok=True)
    log(f"powerd started, long press {HOLD_S} s")
    signal.signal(signal.SIGTERM, lambda *_: None if power["ignore_term"] else sys.exit(0))
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except OSError as e:
        log(f"port {PORT}: {e}")
    fd = None
    down_t, fired = None, False
    while True:
        if fd is None:
            dev = find_device()
            if not dev:
                time.sleep(0.5)
                continue
            try:
                fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
                log(f"reading {dev}")
            except OSError as e:
                log(f"{dev}: {e}")
                time.sleep(1)
                continue
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                data = os.read(fd, EVENT.size * 32)
            except OSError:
                os.close(fd)
                fd = None   # the device went away (the console was switched off or restarted): look for it again
                down_t = None
                continue
            for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, code, value = EVENT.unpack_from(data, off)
                if etype == EV_KEY and code == KEY_POWER:
                    if value == 1:
                        down_t, fired = time.monotonic(), False
                    elif value == 0:
                        if down_t is not None and not fired:
                            short_press()
                        down_t = None
        if down_t is not None and not fired and power["state"] == "on" and time.monotonic() - down_t >= HOLD_S:
            fired = True
            threading.Thread(target=power_off, daemon=True).start()


if __name__ == "__main__":
    main()
