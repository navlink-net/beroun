# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The simulator's external screen (docs/system-api.md): a second private Xvfb, a second Weston (x11 backend) in it, one fullscreen cog for the one big window,
a streamer of its picture for the Monitor window (`/ext.mjpg`) and the set_mode of a stretched window.

It lives only while the cable is "plugged in" (start / stop); nothing is paid without a screen. The device has the real HDMI connector instead; the rest of the system sees only
extdisplay.ExtDisplay and its driver (shelld's), never this class.

What the stage 0 spike (tests/extscreen_spike.sh) found, and this code relies on: Weston's x11 backend follows the size of its X window, so a stretched Monitor window is one
ConfigureWindow on the Weston window; the cog must be fullscreen (COG_PLATFORM_WL_VIEW_FULLSCREEN=1), then its page follows the output exactly.
"""
import os, re, subprocess, threading, time

import display

SCREEN_W, SCREEN_H = 3840, 2160     # the Xvfb screen: the largest mode the external screen can have
WESTON_INI = """[core]
shell=desktop
idle-time=0

[shell]
panel-position=none
background-color=0xff0a0f14

# Weston's own on-screen keyboard is replaced by the HTML one (web-shell/osk.js), as on the console (config/weston.ini): a program that only sleeps stands in for weston-keyboard
[input-method]
path=@NO_IM@
"""


class ScreenError(Exception):
    pass


class ExtScreen:
    def __init__(self, state_dir, shell_port, socket, k=0, log=print, appid_suffix=""):
        self.state, self.port, self.log = state_dir, shell_port, log
        self.display = f":{30 + k}"                 # the console's is :9 + k
        self.k = k
        self.socket = f"{socket}-ext"
        self.appid = f"com.intellicat.ext{appid_suffix}"
        self.width = self.height = 0
        self.procs = {}                              # "xvfb" | "weston" | "cog" -> Popen
        self.win = None                              # the X window id of Weston's output
        self.streamer = None
        self.last_url, self.cog_restarts = None, 0     # the page the cog shows now; how often it was started again after dying on its own
        self.xinput = None                           # display.XInput on this X server: the mouse and the keys of the external screen (extinput.py)
        self.lock = threading.RLock()

    # ---- the screen ----------------------------------------------------------------------------------------------
    @property
    def running(self):
        p = self.procs.get("weston")
        return bool(p and p.poll() is None)

    def _wait(self, cond, what, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return
            time.sleep(0.1)
        raise ScreenError(f"the external screen did not come up: {what}")

    def start(self, width, height):
        """Xvfb, Weston at the given mode, the streamer. The big window (the cog) is started by show()."""
        with self.lock:
            if self.running:
                return
            self.stop()
            self._check_mode(width, height)
            runtime = os.environ.get("XDG_RUNTIME_DIR", "/mnt/wslg/runtime-dir")
            ini = os.path.join(self.state, "weston-ext.ini")
            noim = os.path.join(self.state, "no-input-method-ext.sh")
            open(noim, "w").write(f"#!/bin/sh\nexec sleep {2147483647 - self.k}\n")   # the same as start.sh's: stop.sh ends it, and only this simulator's
            os.chmod(noim, 0o755)
            open(ini, "w").write(WESTON_INI.replace("@NO_IM@", noim))
            xnum = self.display[1:]
            xsock = f"/tmp/.X11-unix/X{xnum}"
            try:
                os.unlink(f"/tmp/.X{xnum}-lock")
            except OSError:
                pass
            self.procs["xvfb"] = subprocess.Popen(
                ["Xvfb", self.display, "-screen", "0", f"{SCREEN_W}x{SCREEN_H}x24", "-nolisten", "tcp", "-noreset"],
                stdout=open(os.path.join(self.state, "Xvfb-ext.log"), "ab"), stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=display.to_simulator_group)
            self._wait(lambda: os.path.exists(xsock), "Xvfb")
            wlog = os.path.join(self.state, "weston-ext.log")
            open(wlog, "w").close()
            env = dict(os.environ, DISPLAY=self.display, XDG_RUNTIME_DIR=runtime)
            env.pop("WAYLAND_DISPLAY", None)
            self.procs["weston"] = subprocess.Popen(
                ["weston", "--backend=x11", "--renderer=pixman", f"--width={width}", f"--height={height}", f"--socket={self.socket}", f"--config={ini}"],
                env=env, stdout=open(wlog, "ab"), stderr=subprocess.STDOUT, start_new_session=True)
            self._wait(lambda: self.running and os.path.exists(os.path.join(runtime, self.socket)), "Weston")
            m = None
            for _ in range(50):
                m = re.search(r"window id (\d+)", open(wlog, errors="replace").read())
                if m:
                    break
                time.sleep(0.1)
            if not m:
                self.stop()
                raise ScreenError("the external Weston has no output window")
            self.win = int(m.group(1))
            self.width, self.height = width, height
            self.cog_restarts = 0
            self.streamer = display.Streamer(self.display, size=f"{width}x{height}")
            self.xinput = display.XInput(self.display)
            self.log(f"external screen: {width}x{height} on {self.display}, socket {self.socket}")

    def stop(self):
        with self.lock:
            if self.streamer:
                self.streamer.stop()
                self.streamer = None
            if self.xinput and self.xinput.d:
                try:
                    self.xinput.d.close()
                except Exception:
                    pass
            self.xinput = None
            for name in ("cog", "native", "weston", "xvfb"):
                p = self.procs.pop(name, None)
                if p and p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(4)
                    except subprocess.TimeoutExpired:
                        p.kill()
            # the cog is started by `cog` itself under its application id: anything of ours that is left
            subprocess.run(["pkill", "-f", f"gapplication-app-id={self.appid} "], capture_output=True)
            self.win, self.width, self.height = None, 0, 0

    @staticmethod
    def _check_mode(width, height):
        if not (isinstance(width, int) and isinstance(height, int)) or not (320 <= width <= SCREEN_W and 200 <= height <= SCREEN_H):
            raise ScreenError(f"the external screen's mode is up to {SCREEN_W}x{SCREEN_H}")

    def set_mode(self, width, height):
        """The Monitor window was stretched: the Weston window gets the new size (its output and the cog's page follow), the stream starts again at it."""
        with self.lock:
            if not self.running:
                return
            self._check_mode(width, height)
            if (width, height) == (self.width, self.height):
                return
            from Xlib import display as xdisplay
            d = xdisplay.Display(self.display)
            try:
                d.create_resource_object("window", self.win).configure(width=width, height=height)
                d.sync()
            finally:
                d.close()
            self.width, self.height = width, height
            if self.streamer:
                self.streamer.resize(f"{width}x{height}")

    # ---- the one big window ---------------------------------------------------------------------------------------
    def _end_native(self):
        p = self.procs.pop("native", None)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(4)
            except subprocess.TimeoutExpired:
                p.kill()

    def show_native(self, cmd, env_extra=None, env_base=None):
        """The big window is a native program of an app (the browser): it replaces whatever is in the window (the cog, or the program of another app) and is fullscreen on this
        screen like the cog. The program that was in the window has saved already (extdisplay asks the page first)."""
        with self.lock:
            if not self.running:
                raise ScreenError("there is no external screen")
            self._end_native()
            cog = self.procs.pop("cog", None)
            if cog and cog.poll() is None:
                cog.terminate()
                try:
                    cog.wait(4)
                except subprocess.TimeoutExpired:
                    cog.kill()
                subprocess.run(["pkill", "-f", f"gapplication-app-id={self.appid} "], capture_output=True)
            env = dict(env_base if env_base is not None else os.environ, WAYLAND_DISPLAY=self.socket, XDG_SESSION_TYPE="wayland", **(env_extra or {}))
            env.pop("DISPLAY", None)
            self.procs["native"] = subprocess.Popen(cmd, env=env, stdout=open(os.path.join(self.state, "native-ext.log"), "ab"), stderr=subprocess.STDOUT, start_new_session=True)
            self.log(f"external screen: the window is {os.path.basename(cmd[0])}")

    def show(self, url):
        """The big window shows this page: the cog is started on the first page, and navigated (its own `open` action) afterwards."""
        with self.lock:
            if not self.running:
                raise ScreenError("there is no external screen")
            self._end_native()
            self.last_url = url
            cog = self.procs.get("cog")
            if cog and cog.poll() is None:
                env = dict(os.environ, WAYLAND_DISPLAY=self.socket)
                r = subprocess.run(["cogctl", "-A", self.appid, "open", url], env=env, capture_output=True, timeout=10)
                if r.returncode == 0:
                    return
                self.log(f"external screen: cogctl open failed ({r.stderr.decode(errors='replace').strip()}); the cog is started again")
                cog.terminate()
            env = dict(os.environ, WAYLAND_DISPLAY=self.socket, GDK_BACKEND="wayland", XDG_SESSION_TYPE="wayland", COG_PLATFORM_WL_VIEW_FULLSCREEN="1")
            env.pop("DISPLAY", None)
            self.procs["cog"] = subprocess.Popen(["cog", "--platform=wl", f"--gapplication-app-id={self.appid}", url], env=env,
                                                 stdout=open(os.path.join(self.state, "cog-ext.log"), "ab"), stderr=subprocess.STDOUT, start_new_session=True)
            threading.Thread(target=self._watch_cog, args=(self.procs["cog"],), daemon=True, name="ext-cog-watch").start()

    def _watch_cog(self, p):
        """The cog of the big window died on its own (the first one after the machine boots crashes in the graphics start now and then): it is started again with the page it was showing,
        a few times. A cog that was ended or replaced on purpose is not touched."""
        p.wait()
        time.sleep(0.5)
        with self.lock:
            if not self.running or self.procs.get("cog") is not p:
                return
            if self.cog_restarts >= 4:
                self.log("external screen: the cog keeps dying, given up")
                return
            self.cog_restarts += 1
            self.log(f"external screen: the cog died, started again ({self.cog_restarts})")
            self.procs.pop("cog", None)
            url = self.last_url
        try:
            self.show(url)
        except ScreenError:
            pass

    def serve(self, handler):
        """GET /ext.mjpg: the picture of the screen for the Monitor window."""
        s = self.streamer
        if not (self.running and s):
            return False
        s.serve(handler)
        return True
