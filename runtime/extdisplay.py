# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The external display of OS Beroun (HDMI): the stack of apps, the focus, the events (docs/system-api.md).

Pure logic, no input or output of its own: what has to happen on the screen goes through a **driver** that the caller gives (shelld's, later; a recorder in the tests), so the rules can be
tested alone (tests/extdisplay_test.py). The driver has three methods:

    show(app, arg, leaving=None, reason=None, resume=False)   the window (the one fullscreen cog of the external screen) loads the big page of `app`; app None is the cat splash.
                                                              `leaving` is the app whose page is in the window now: it is asked to save its state first (Platform.state), `reason` says why
                                                              ("covered" by another app, "closed", "reopen"). resume: the app was in the stack under another one, its page restores its state.
    resize(width, height)                                     the mode of the screen changed (the window is stretched, another mode of a monitor).
    hide(leaving, reason)                                     the screen is gone (the cable is out): the page in the window (app `leaving`, or None for the cat) saves and the window closes.
    deliver(app, to, message)                                 a message for one of the two pages of an app: to is "console" or "big".
    broadcast(event)                                          an event for every page: {"event": ..., **status}.

The rules (the owner's, 2026-10-28):
  * One window on the external screen, one frontend in it at a time. The apps that asked for it form a stack: the last one is on top; when it goes, the one under it comes back (and restores its
    state); an empty stack shows the cat.
  * Only an app that declared an "external" page in its manifest (register_app) may open the screen, and only when there is a screen.
  * The cable can be pulled any time: the stack stays; plugged in again, the top of the stack is loaded again.
  * The focus (which screen the cursor, A and B go to) is switched by SELECT; there is nothing to switch to without a screen.
  * An app and its two pages: the page of the console (the remote) and the big page talk to each other; a message to the big page is delivered only while that page is the one in the window.
"""
import json, threading

MIN_W, MAX_W, MIN_H, MAX_H = 320, 3840, 200, 2160
DEFAULT_W, DEFAULT_H = 1280, 720
MAX_MESSAGE_BYTES = 64 * 1024


class ExtError(Exception):
    """An error fit for the screen; `code` is the HTTP status the API will answer with."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class ExtDisplay:
    def __init__(self, driver, connected=False, width=DEFAULT_W, height=DEFAULT_H):
        self.driver = driver
        self.connected = False
        self.width, self.height = self._size(width, height)
        self.focus = "console"
        self.stack = []          # [{"app", "arg"}], the top is the last
        self.apps = {}           # app id -> its "external" section of the manifest
        self.lock = threading.RLock()
        if connected:
            self.connected = True

    # ---- reading -----------------------------------------------------------------------------------------------------
    @staticmethod
    def _size(w, h):
        if not (isinstance(w, int) and isinstance(h, int)) or isinstance(w, bool) or isinstance(h, bool) or not (MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H):
            raise ExtError(f"the size is whole pixels, {MIN_W}..{MAX_W} by {MIN_H}..{MAX_H}")
        return w, h

    def status(self):
        with self.lock:
            return {"connected": self.connected, "width": self.width, "height": self.height, "focus": self.focus,
                    "top": self.stack[-1]["app"] if self.stack else None, "stack": [e["app"] for e in self.stack]}

    def _top(self):
        return self.stack[-1] if self.stack else None

    def top_is_game(self):
        """A game is on top of a connected screen (manifest "external": {"input": "game"}): the sticks, X and Y are its, not the screen's mouse (the owner, 2026-10-28)."""
        with self.lock:
            top = self._top()
            return bool(self.connected and top and (self.apps.get(top["app"]) or {}).get("input") == "game")

    def top_is_native_game(self):
        """The game on top is a program of its own ("external": {"cmd": [...], "input": "game"}, the cloud Minecraft's player): it reads the gamepad itself, so every button
        stays the gamepad's, as on the device, and nothing is turned into keys or a mouse of the screen."""
        with self.lock:
            top = self._top()
            x = (self.apps.get(top["app"]) or {}) if self.connected and top else {}
            return x.get("input") == "game" and bool(x.get("cmd"))

    def _focus_follows_top(self):
        """A game that comes on top takes the cursor, A and B with it; an empty screen (the cat) gives them back to the console, there is nothing to press on it."""
        top = self._top()
        want = None
        if top and (self.apps.get(top["app"]) or {}).get("input") == "game":
            want = "external"
        elif not top:
            want = "console"
        if want and self.connected and want != self.focus:
            self.focus = want
            self._event("focus")

    def _event(self, name):
        self.driver.broadcast(dict(self.status(), event=name))

    # ---- the apps that may use the screen ----------------------------------------------------------------------------
    def register_app(self, app, external):
        """The manifests' "external" sections: {"entry": ..., "state": true}. An app that is no longer there leaves the stack."""
        with self.lock:
            self.apps[app] = external

    def unregister_app(self, app):
        with self.lock:
            self.apps.pop(app, None)
            self.app_closed(app)

    # ---- the stack ---------------------------------------------------------------------------------------------------
    def open(self, app, arg=None):
        """The big page of `app` goes on top of the stack (or comes to the top, when the app is in it already)."""
        with self.lock:
            if app not in self.apps:
                raise ExtError(f"{app} has no page for the external screen", 404)
            if not self.connected:
                raise ExtError("there is no external screen", 409)
            top = self._top()
            if top and top["app"] == app:
                return self.status()          # already on the screen: nothing to load again
            existing = next((e for e in self.stack if e["app"] == app), None)
            if existing:
                self.stack.remove(existing)
                entry = {"app": app, "arg": existing["arg"]}
                resume = True
            else:
                entry = {"app": app, "arg": arg}
                resume = False
            self.stack.append(entry)
            self.driver.show(app, None if resume else arg, leaving=top["app"] if top else None, reason="covered" if top else None, resume=resume)
            self._event("stack")
            self._focus_follows_top()
            return self.status()

    def close(self, app):
        """The app takes itself off the stack (its big page is no longer needed)."""
        with self.lock:
            if not any(e["app"] == app for e in self.stack):
                raise ExtError(f"{app} is not on the external screen", 404)
            self.app_closed(app)
            return self.status()

    def app_closed(self, app):
        """The app is gone (MODE on the console, or it closed itself): it leaves the stack wherever it was; when it was on top the one under it comes back."""
        with self.lock:
            entry = next((e for e in self.stack if e["app"] == app), None)
            if not entry:
                return False
            was_top = self._top() is entry
            self.stack.remove(entry)
            if was_top and self.connected:
                nxt = self._top()
                self.driver.show(nxt["app"] if nxt else None, None, leaving=app, reason="closed", resume=bool(nxt))
            self._event("stack")
            if was_top:
                self._focus_follows_top()
            return True

    # ---- the cable and the mode --------------------------------------------------------------------------------------
    def set_connected(self, on, width=None, height=None):
        """The cable: plugged in (with the mode the monitor reports) or pulled out. The stack stays; the focus goes back to the console."""
        with self.lock:
            if width is not None or height is not None:
                self.width, self.height = self._size(width if width is not None else self.width, height if height is not None else self.height)
            on = bool(on)
            if on == self.connected:
                if on and (width is not None or height is not None):
                    self.driver.resize(self.width, self.height)
                    self._event("resize")
                return self.status()
            self.connected = on
            top = self._top()
            if on:
                self.driver.show(top["app"] if top else None, None, resume=bool(top))
                self._event("connected")
                self._focus_follows_top()
            else:
                self.driver.hide(top["app"] if top else None, "unplugged")
                if self.focus != "console":
                    self.focus = "console"
                    self._event("focus")
                self._event("disconnected")
            return self.status()

    def resize(self, width, height):
        """The mode of the screen changed (the monitor window was stretched, another mode of a monitor): the pages get the new size."""
        with self.lock:
            w, h = self._size(width, height)
            if not self.connected:
                raise ExtError("there is no external screen", 409)
            if (w, h) != (self.width, self.height):
                self.width, self.height = w, h
                self.driver.resize(w, h)
                self._event("resize")
            return self.status()

    # ---- the focus ---------------------------------------------------------------------------------------------------
    def set_focus(self, to):
        with self.lock:
            if to not in ("console", "external"):
                raise ExtError("the focus is console or external")
            if not self.connected:
                raise ExtError("there is no external screen to give the focus to", 409)
            if to != self.focus:
                self.focus = to
                self._event("focus")
            return self.status()

    def toggle_focus(self):
        """SELECT. With no screen it does nothing (and says so), so that the button keeps its old meaning in the apps."""
        with self.lock:
            if not self.connected:
                return None
            return self.set_focus("external" if self.focus == "console" else "console")

    # ---- the two pages of an app -------------------------------------------------------------------------------------
    def send(self, app, sender, message):
        """A message from one page of `app` to the other. {"delivered": bool}: the big page hears it only while it is the one in the window; the console page is always there (the app is open)."""
        with self.lock:
            if sender not in ("console", "big"):
                raise ExtError("the sender is console or big")
            if app not in self.apps:
                raise ExtError(f"{app} has no page for the external screen", 404)
            try:
                size = len(json.dumps(message))
            except (TypeError, ValueError):
                raise ExtError("a message is JSON")
            if size > MAX_MESSAGE_BYTES:
                raise ExtError(f"a message is at most {MAX_MESSAGE_BYTES // 1024} KB")
            to = "big" if sender == "console" else "console"
            top = self._top()
            if to == "big" and not (self.connected and top and top["app"] == app):
                return {"delivered": False}
            self.driver.deliver(app, to, message)
            return {"delivered": True}
