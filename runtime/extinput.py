# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Input for the external display (docs/system-api.md): which button goes where, and the mouse made of the two sticks.

Pure logic with an injected `inject` object (shelld's: XTEST on the external screen's X server; a recorder in the tests/extinput_logic_test.py):

    inject.key(name, down)        a console button as a key of the external screen's page (up, down, left, right, a, b)
    inject.button(n, down)        a mouse button: 1 left, 3 right
    inject.move(x, y)             the pointer, in the pixels of the external screen
    inject.wheel(dx, dy)          one notch of the wheel: dy -1 up, +1 down; dx -1 left, +1 right

The owner's rulings (2026-10-28) and nothing else:
  * SELECT switches the focus of the cursor (D-pad), A and B between the two screens, as long as there is an external screen (without one the button keeps whatever it meant before).
  * The mouse is the external screen's whatever the focus is (the owner, 2026-10-28, correcting the first ruling): the left stick moves the pointer, the right stick scrolls,
    X and Y are the left and the right mouse buttons. With the focus on the console only the cursor, A and B stay the console's.
  * A game on top of the big screen (manifest "external": {"input": "game"}; the owner, 2026-10-28) owns the sticks, X and Y: X and Y are its keys (space, Tab,
    as on the console) whatever the focus, the sticks reach it as the gamepad, and there is no pointer and no wheel while it is up.
    A game that is a program of its own ("external": {"cmd": [...], "input": "game"}, the cloud Minecraft's player) reads the gamepad itself: every button stays the gamepad's, as on the device.
  * Every other button (START, MODE, the shoulder buttons, POWER) goes where it always went, whatever the focus: MODE leaves the app, the volume, Caroline and power stay the system's.
"""
import math, threading

DEADZONE = 0.18             # the stick's travel that is not a move (it is never exactly zero)
MAX_SPEED = 900.0           # pixels per second at 720p, with the stick all the way; it grows with the square of the travel, so that small moves are fine
WHEEL_MAX_HZ = 14.0         # notches per second with the stick all the way
KEY_BUTTONS = ("up", "down", "left", "right", "a", "b")
MOUSE_BUTTONS = {"x": 1, "y": 3}


class ExtInput:
    def __init__(self, inject, display):
        """display: the extdisplay.ExtDisplay (its status() says whether there is a screen, which has the focus, how big it is)."""
        self.inject, self.display = inject, display
        self.sticks = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self.pos = [0.0, 0.0]
        self.held = set()       # the keys and mouse buttons that are down on the external screen
        self.wheel_acc = [0.0, 0.0]
        self.lock = threading.RLock()

    # ---- the buttons -------------------------------------------------------------------------------------------------
    def route(self, name, on):
        """True when the button was dealt with here (it must not go to the console's apps too); False when it goes the old way."""
        st = self.display.status()
        if not st["connected"]:
            return False
        if name == "select":
            if on:
                self.release_all()
                self.display.toggle_focus()
            return True
        if self.display.top_is_native_game():   # it reads the gamepad: the button goes the old way (the pad, the console's page)
            return False
        if name in MOUSE_BUTTONS and self.display.top_is_game():   # a game on the big screen: X and Y are its keys (space, Tab, as on the console), not mouse buttons
            self._hold(("key", name), on, lambda d: self.inject.key(name, d))
            return True
        if name in MOUSE_BUTTONS:       # the mouse is the external screen's whatever the focus is
            n = MOUSE_BUTTONS[name]
            self._hold(("btn", n), on, lambda d: self.inject.button(n, d))
            return True
        if st["focus"] != "external":
            return False
        if name in KEY_BUTTONS:
            self._hold(("key", name), on, lambda d: self.inject.key(name, d))
            return True
        return False

    def _hold(self, ident, on, fn):
        with self.lock:
            if on:
                self.held.add(ident)
                fn(True)
            elif ident in self.held:
                self.held.discard(ident)
                fn(False)

    def release_all(self):
        """The focus moved or the cable went: nothing stays pressed on a screen that no longer hears the buttons."""
        with self.lock:
            for kind, what in sorted(self.held, key=str):
                if kind == "key":
                    self.inject.key(what, False)
                else:
                    self.inject.button(what, False)
            self.held.clear()

    # ---- the sticks --------------------------------------------------------------------------------------------------
    def stick(self, which, x, y):
        with self.lock:
            if which in self.sticks:
                self.sticks[which] = (max(-1.0, min(1.0, float(x))), max(-1.0, min(1.0, float(y))))
                if which == "right":
                    self.wheel_acc = [0.0, 0.0] if self.sticks["right"] == (0.0, 0.0) else self.wheel_acc

    def center(self):
        """The cable was plugged in or the mode changed: the pointer is in the middle of the screen."""
        st = self.display.status()
        with self.lock:
            self.pos = [st["width"] / 2.0, st["height"] / 2.0]
            if st["connected"]:
                self.inject.move(int(self.pos[0]), int(self.pos[1]))

    @staticmethod
    def _travel(x, y):
        """(unit direction, 0..1 travel past the dead zone) of a stick."""
        m = math.hypot(x, y)
        if m <= DEADZONE:
            return 0.0, 0.0, 0.0
        return x / m, y / m, min(1.0, (m - DEADZONE) / (1.0 - DEADZONE))

    def tick(self, dt):
        """One step of the pointer and the wheel (shelld calls it ~60 times a second). They are the external screen's whatever the focus is."""
        st = self.display.status()
        if not st["connected"] or self.display.top_is_game():   # a game on the big screen reads the sticks itself (the gamepad): no pointer, no wheel
            return
        with self.lock:
            w, h = st["width"], st["height"]
            ux, uy, t = self._travel(*self.sticks["left"])
            if t:
                speed = MAX_SPEED * (h / 720.0) * t * t
                old = (int(self.pos[0]), int(self.pos[1]))
                self.pos[0] = max(0.0, min(w - 1.0, self.pos[0] + ux * speed * dt))
                self.pos[1] = max(0.0, min(h - 1.0, self.pos[1] + uy * speed * dt))
                if (int(self.pos[0]), int(self.pos[1])) != old:
                    self.inject.move(int(self.pos[0]), int(self.pos[1]))
            ux, uy, t = self._travel(*self.sticks["right"])
            if t:
                for axis, u in ((0, ux), (1, uy)):
                    self.wheel_acc[axis] += u * WHEEL_MAX_HZ * t * dt
                    while abs(self.wheel_acc[axis]) >= 1.0:
                        step = 1 if self.wheel_acc[axis] > 0 else -1
                        self.wheel_acc[axis] -= step
                        self.inject.wheel(step if axis == 0 else 0, step if axis == 1 else 0)
            else:
                self.wheel_acc = [0.0, 0.0]
