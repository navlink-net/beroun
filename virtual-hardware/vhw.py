# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Virtual hardware (the reference implementation of the hardware layer).

Everything hardware-dependent lives here behind the `VirtualHardware` class so the
shell and apps never touch devices directly. On a real device the same data comes
from /dev/input/event* (adc-joystick, gpio-keys), /sys/class/power_supply, ALSA, etc.

Input:   a uinput gamepad (evdev) named "IntelliCat Virtual Gamepad".
Power:   files laid out like /sys/class/power_supply/BAT0 and AC0 under <state>/power_supply.
"""
import fcntl, json, os, struct, threading, time

UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_SET_PROPBIT = 0x4004556E
INPUT_PROP_DIRECT, BTN_TOUCH = 0x01, 0x14A
# NOT KEY_POWER: systemd-logind of the host distro acts on that key from ANY input device, also a uinput one, and powers the whole WSL machine
# off (found the hard way, twice). The power key of the simulated console is an unrelated code on a device of its own.
KEY_POWER = 0x2C0   # BTN_TRIGGER_HAPPY1
SCREEN_W, SCREEN_H = 640, 480
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x03, 0x04, 0x10, 0x11

BUTTONS = {  # name -> evdev key code
    "a": 0x130, "b": 0x131, "x": 0x133, "y": 0x134,
    "l1": 0x136, "r1": 0x137, "l2": 0x138, "r2": 0x139,
    "select": 0x13A, "start": 0x13B, "mode": 0x13C, "l3": 0x13D, "r3": 0x13E,
}
DPAD = {"up": (ABS_HAT0Y, -1), "down": (ABS_HAT0Y, 1), "left": (ABS_HAT0X, -1), "right": (ABS_HAT0X, 1)}
STICKS = {"left": (ABS_X, ABS_Y), "right": (ABS_RX, ABS_RY)}
STICK_RANGE = 1800  # +-1800 (configurable; the real range must be confirmed on hardware)


class VirtualHardware:
    def __init__(self, state_dir, uinput_path="/dev/uinput"):
        self.dir = os.path.join(state_dir, "hw")
        self.ps = os.path.join(self.dir, "power_supply")
        self.uinput_path = uinput_path
        self.fd = None
        self.tfd = None
        self.pfd = None          # the power key: an input device of its own (KEY_POWER), like the PMIC's key of the real console
        self.touch_error = None
        self.power_error = None
        self.lock = threading.Lock()
        self.dpad = {"x": 0, "y": 0}
        self.input_error = None
        self._init_power()

    # ---- power -------------------------------------------------------------
    def _init_power(self):
        for sub in ("BAT0", "AC0"):
            os.makedirs(os.path.join(self.ps, sub), exist_ok=True)
        if not os.path.exists(os.path.join(self.ps, "BAT0", "capacity")):
            self.set_battery(capacity=100, status="Full")
        if not os.path.exists(os.path.join(self.ps, "AC0", "online")):
            self._w("AC0/online", "1")

    def _w(self, rel, val):
        with open(os.path.join(self.ps, rel), "w") as f:
            f.write(str(val) + "\n")

    def _r(self, rel, default=""):
        try:
            with open(os.path.join(self.ps, rel)) as f:
                return f.read().strip()
        except OSError:
            return default

    def set_battery(self, capacity=None, status=None, ac=None):
        if capacity is not None:
            self._w("BAT0/capacity", max(0, min(100, int(capacity))))
        if status is not None:
            if status not in ("Charging", "Discharging", "Full", "Not charging"):
                raise ValueError("bad status")
            self._w("BAT0/status", status)
        if ac is not None:
            self._w("AC0/online", 1 if ac else 0)
            if status is None:
                self._w("BAT0/status", "Charging" if ac else "Discharging")
        self._w("BAT0/type", "Battery")
        self._w("AC0/type", "Mains")

    def power(self):
        return {"capacity": int(self._r("BAT0/capacity", "0")), "status": self._r("BAT0/status"),
                "ac_online": self._r("AC0/online") == "1", "path": self.ps}

    # ---- input -------------------------------------------------------------
    def open_input(self):
        if self.fd is not None:
            return True
        try:
            fd = os.open(self.uinput_path, os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            for code in BUTTONS.values():
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)
            absmin, absmax = [0] * 64, [0] * 64
            for a in (ABS_X, ABS_Y, ABS_RX, ABS_RY):
                fcntl.ioctl(fd, UI_SET_ABSBIT, a)
                absmin[a], absmax[a] = -STICK_RANGE, STICK_RANGE
            for a in (ABS_HAT0X, ABS_HAT0Y):
                fcntl.ioctl(fd, UI_SET_ABSBIT, a)
                absmin[a], absmax[a] = -1, 1
            # an instance names its own pad, as its power key: a native app reading evdev (the Minecraft player) finds the pad of ITS simulator
            name = (b"IntelliCat Virtual Gamepad" + (b" " + os.environ["INTELLICAT_INSTANCE"].encode() if os.environ.get("INTELLICAT_INSTANCE") else b"")).ljust(80, b"\0")
            dev = name + struct.pack("<HHHHI", 0x03, 0x1209, 0x1CA7, 1, 0)
            dev += struct.pack("<64i", *absmax) + struct.pack("<64i", *absmin)
            dev += struct.pack("<64i", *([0] * 64)) + struct.pack("<64i", *([0] * 64))
            os.write(fd, dev)
            fcntl.ioctl(fd, UI_DEV_CREATE)
            time.sleep(0.2)
            self.fd, self.input_error = fd, None
            return True
        except OSError as e:
            self.input_error = f"{self.uinput_path}: {e}"
            return False

    # ---- the power key -------------------------------------------------------
    def open_power(self):
        if self.pfd is not None:
            return True
        try:
            fd = os.open(self.uinput_path, os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            fcntl.ioctl(fd, UI_SET_KEYBIT, KEY_POWER)
            dev = (b"IntelliCat Power Key" + (b" " + os.environ["INTELLICAT_INSTANCE"].encode() if os.environ.get("INTELLICAT_INSTANCE") else b"")).ljust(80, b"\0") + struct.pack("<HHHHI", 0x19, 0x1209, 0x1CA9, 1, 0) + b"\0" * (4 * 64 * 4)
            os.write(fd, dev)
            fcntl.ioctl(fd, UI_DEV_CREATE)
            time.sleep(0.2)
            self.pfd, self.power_error = fd, None
            return True
        except OSError as e:
            self.power_error = f"{self.uinput_path}: {e}"
            return False

    def power_key(self, pressed):
        """Press / release the power key (KEY_POWER on its own input device); powerd reads it."""
        if not self.open_power():
            raise RuntimeError(self.power_error)
        with self.lock:
            t = time.time()
            ev = lambda ty, c, v: struct.pack("<qqHHi", int(t), int((t % 1) * 1e6), ty, c, v)
            os.write(self.pfd, ev(EV_KEY, KEY_POWER, 1 if pressed else 0) + ev(EV_SYN, 0, 0))

    def close(self):
        for attr in ("fd", "tfd", "pfd"):
            fd = getattr(self, attr)
            if fd is not None:
                fcntl.ioctl(fd, UI_DEV_DESTROY)
                os.close(fd)
                setattr(self, attr, None)

    def _emit(self, etype, code, value):
        t = time.time()
        os.write(self.fd, struct.pack("<qqHHi", int(t), int((t % 1) * 1e6), etype, code, value))

    def _sync(self):
        self._emit(EV_SYN, 0, 0)

    def button(self, name, pressed):
        """Press/release a console button (a b x y l1 r1 l2 r2 select start mode l3 r3 up down left right)."""
        if not self.open_input():
            raise RuntimeError(self.input_error)
        with self.lock:
            if name in BUTTONS:
                self._emit(EV_KEY, BUTTONS[name], 1 if pressed else 0)
            elif name in DPAD:
                axis, v = DPAD[name]
                self._emit(EV_ABS, axis, v if pressed else 0)
            else:
                raise ValueError(f"unknown button {name!r}")
            self._sync()

    def stick(self, which, x, y):
        """Move an analog stick; x,y in [-1.0, 1.0]."""
        if which not in STICKS:
            raise ValueError("stick must be left|right")
        if not self.open_input():
            raise RuntimeError(self.input_error)
        ax, ay = STICKS[which]
        with self.lock:
            self._emit(EV_ABS, ax, int(max(-1, min(1, x)) * STICK_RANGE))
            self._emit(EV_ABS, ay, int(max(-1, min(1, y)) * STICK_RANGE))
            self._sync()

    # ---- touchscreen (single touch, direct-input, 640x480 absolute) ----------
    def open_touch(self):
        if self.tfd is not None:
            return True
        try:
            fd = os.open(self.uinput_path, os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH)
            fcntl.ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
            absmax = [0] * 64
            for a, mx in ((ABS_X, SCREEN_W - 1), (ABS_Y, SCREEN_H - 1)):
                fcntl.ioctl(fd, UI_SET_ABSBIT, a)
                absmax[a] = mx
            dev = b"IntelliCat Virtual Touchscreen".ljust(80, b"\0") + struct.pack("<HHHHI", 0x03, 0x1209, 0x1CA8, 1, 0)
            dev += struct.pack("<64i", *absmax) + struct.pack("<64i", *([0] * 64)) * 3
            os.write(fd, dev)
            fcntl.ioctl(fd, UI_DEV_CREATE)
            time.sleep(0.2)
            self.tfd, self.touch_error = fd, None
            return True
        except OSError as e:
            self.touch_error = f"{self.uinput_path}: {e}"
            return False

    def touch(self, x, y, down):
        """Touch at pixel (x, y) of the 640x480 screen; down=False lifts the finger."""
        if not self.open_touch():
            raise RuntimeError(self.touch_error)
        x, y = max(0, min(SCREEN_W - 1, int(x))), max(0, min(SCREEN_H - 1, int(y)))
        with self.lock:
            t = time.time()
            ev = lambda ty, c, v: struct.pack("<qqHHi", int(t), int((t % 1) * 1e6), ty, c, v)
            out = b""
            if down:
                out += ev(EV_ABS, ABS_X, x) + ev(EV_ABS, ABS_Y, y) + ev(EV_KEY, BTN_TOUCH, 1)
            else:
                out += ev(EV_KEY, BTN_TOUCH, 0)
            os.write(self.tfd, out + ev(EV_SYN, 0, 0))

    def state(self):
        return {"power": self.power(), "input": {"ready": self.fd is not None, "error": self.input_error},
                "touch": {"ready": self.tfd is not None, "error": self.touch_error},
                "buttons": sorted(list(BUTTONS) + list(DPAD)),
                "storage": os.path.join(os.path.dirname(self.dir), "storage")}
