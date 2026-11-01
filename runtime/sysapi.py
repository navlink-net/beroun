# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The system API of OS Beroun: MessageBox, system sounds, vibration (docs/system-api.md).

One module, three parts, no dependency on shelld (it is given what it needs), so each can be tested alone (tests/sysapi_test.py):

  Sounds     the system sounds by name (notify, message, ok, warning, error, click, alarm, ring): little tunes made here, written once as WAV files
             and played through the sound card. Do-not-disturb silences them, except the priority sounds (alarm, ring) and a call with force.
  Vibra      the vibration motor: a pattern by name (tap, short, long, double, alarm, ring) or a list of milliseconds (on, off, on, ...), and a strength 0..100.
             Off by the system setting "vibration"; do-not-disturb silences it like the sounds. The simulator has no motor: the pattern goes to the console window
             (it flashes the status bar) and to the log; on the device INTELLICAT_VIBRA_DEV names the file the motor's strength is written to.
  MsgBoxes   a modal message over whatever frontend is in the shared window: title, text, icon, buttons (cursor left/right, A confirms, B cancels). One box at a time,
             the next waits; a box with no page to show it waits for the next page that connects; the answer comes back by id (or the call waits for it).

handle() is the HTTP side: it returns (code, object) for the paths of this API, or None for any other path.
"""
import json, math, os, struct, subprocess, threading, time, uuid, wave
import audio

RATE = 16000

# ---- sounds ------------------------------------------------------------------------------------------------------------
# a sound is a list of (frequency Hz, milliseconds); frequency 0 is a rest
SOUNDS = {
    "notify":  {"priority": False, "notes": [(880, 90), (0, 40), (1175, 130)]},
    "message": {"priority": False, "notes": [(988, 70), (1319, 110)]},
    "ok":      {"priority": False, "notes": [(660, 80), (880, 80), (1320, 140)]},
    "warning": {"priority": False, "notes": [(740, 160), (0, 60), (740, 160)]},
    "error":   {"priority": False, "notes": [(330, 180), (0, 40), (220, 320)]},
    "click":   {"priority": False, "notes": [(1500, 25)]},
    "alarm":   {"priority": True,  "notes": [(1047, 120), (0, 60), (1047, 120), (0, 60), (1047, 120), (0, 60), (1319, 260)]},
    "ring":    {"priority": True,  "notes": [(784, 200), (0, 60), (988, 200), (0, 60), (784, 200), (0, 60), (988, 200)]},
}


def _wav(path, notes, volume=0.45):
    frames = []
    for hz, ms in notes:
        n = int(RATE * ms / 1000)
        for i in range(n):
            env = min(1.0, i / 160, (n - i) / 160) if hz else 0.0   # a short fade in and out: no clicks
            frames.append(struct.pack("<h", int(32767 * volume * env * math.sin(2 * math.pi * hz * i / RATE))) if hz else struct.pack("<h", 0))
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b"".join(frames))
    os.replace(tmp, path)


class Sounds:
    def __init__(self, state_dir, quiet=lambda: False, player=None):
        """quiet() -> do-not-disturb is on; player(path) starts playing a file (default: aplay)."""
        self.dir = os.path.join(state_dir, "sounds")
        self.quiet = quiet
        self.player = player or self._aplay

    @staticmethod
    def _aplay(path):
        audio.play(path)

    def list(self):
        return [{"name": n, "priority": s["priority"], "ms": sum(ms for _, ms in s["notes"])} for n, s in SOUNDS.items()]

    def play(self, name, force=False):
        if name not in SOUNDS:
            raise ValueError(f"unknown sound '{name}' (one of {', '.join(SOUNDS)})")
        if self.quiet() and not (SOUNDS[name]["priority"] or force):
            return {"played": False, "reason": "do-not-disturb"}
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, name + ".wav")
        if not os.path.exists(path):
            _wav(path, SOUNDS[name]["notes"])
        self.player(path)
        return {"played": True, "name": name}


# ---- vibration ---------------------------------------------------------------------------------------------------------
PATTERNS = {"tap": [30], "short": [80], "long": [400], "double": [80, 80, 80], "alarm": [500, 300, 500, 300, 500], "ring": [300, 200, 300, 200, 600]}
PRIORITY_PATTERNS = {"alarm", "ring"}
MAX_STEP_MS, MAX_STEPS, MAX_TOTAL_MS = 2000, 20, 10000


class Vibra:
    def __init__(self, enabled=lambda: True, quiet=lambda: False, drive=None, emit=None, log=print):
        """enabled() -> the system setting "vibration"; quiet() -> do-not-disturb; drive(strength 0..100) sets the motor (0 = off);
        emit(event) tells the console window (the simulator's stand-in for the motor)."""
        self.enabled, self.quiet, self.drive, self.emit, self.log = enabled, quiet, drive or self._device(), emit, log
        self.gen = 0
        self.lock = threading.Lock()

    @staticmethod
    def _device():
        dev = os.environ.get("INTELLICAT_VIBRA_DEV")
        if not dev:
            return lambda strength: None

        def drive(strength):
            try:
                with open(dev, "w") as f:
                    f.write(str(int(strength)))
            except OSError:
                pass
        return drive

    @staticmethod
    def resolve(pattern):
        """A pattern name or a list of milliseconds -> the list (checked)."""
        if isinstance(pattern, str):
            if pattern not in PATTERNS:
                raise ValueError(f"unknown pattern '{pattern}' (one of {', '.join(PATTERNS)}, or a list of milliseconds)")
            return list(PATTERNS[pattern])
        if not isinstance(pattern, list) or not pattern or len(pattern) > MAX_STEPS or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= MAX_STEP_MS for x in pattern):
            raise ValueError(f"a pattern is a name or a list of 1..{MAX_STEPS} whole milliseconds (0..{MAX_STEP_MS}): on, off, on, ...")
        if sum(pattern) > MAX_TOTAL_MS:
            raise ValueError(f"a pattern lasts at most {MAX_TOTAL_MS // 1000} seconds")
        return list(pattern)

    def status(self):
        return {"enabled": bool(self.enabled()), "patterns": {k: v for k, v in PATTERNS.items()}, "max_ms": MAX_TOTAL_MS}

    def pulse(self, pattern="short", strength=100, force=False):
        steps = self.resolve(pattern)
        if not isinstance(strength, (int, float)) or isinstance(strength, bool) or not 0 <= strength <= 100:
            raise ValueError("strength is 0..100")
        priority = isinstance(pattern, str) and pattern in PRIORITY_PATTERNS
        if not self.enabled() and not force:
            return {"vibrated": False, "reason": "off"}
        if self.quiet() and not (priority or force):
            return {"vibrated": False, "reason": "do-not-disturb"}
        with self.lock:
            self.gen += 1
            gen = self.gen
        self.log(f"vibrate: {steps} strength {int(strength)}")
        if self.emit:
            self.emit({"t": "vibra", "pattern": steps, "strength": int(strength)})
        threading.Thread(target=self._run, args=(steps, strength, gen), daemon=True, name="vibra").start()
        return {"vibrated": True, "ms": sum(steps)}

    def _run(self, steps, strength, gen):
        try:
            for i, ms in enumerate(steps):
                if self.gen != gen:
                    return
                self.drive(strength if i % 2 == 0 else 0)
                time.sleep(ms / 1000)
        finally:
            if self.gen == gen:
                self.drive(0)

    def stop(self):
        with self.lock:
            self.gen += 1
        self.drive(0)
        if self.emit:
            self.emit({"t": "vibra", "pattern": [], "strength": 0})
        return {"ok": True}


# ---- MessageBox --------------------------------------------------------------------------------------------------------
KINDS = {"ok": ["OK"], "okcancel": ["OK", "Cancel"], "yesno": ["Yes", "No"], "yesnocancel": ["Yes", "No", "Cancel"]}
ICONS = ("none", "info", "question", "warning", "error")
MAX_TIMEOUT_S, DEFAULT_TIMEOUT_S = 3600, 120


class Clipboard:
    """The one clipboard of the device (plain text), shared by every app and system view. The text lives here, not in a page.
    on_change(text, source) tells the consoles (their host clipboards follow); source: who set it (an app id, "assistant", "osk", "host")."""
    MAX = 1_000_000

    def __init__(self, on_change=None, now=time.time):
        self.text, self.at, self.source = "", 0.0, ""
        self.on_change, self.now = on_change, now
        self._lock = threading.Lock()
        self._cond = threading.Condition()   # the consoles' answers to the last "clip?"
        self._answers = 0

    def get(self):
        with self._lock:
            return {"text": self.text, "length": len(self.text), "at": self.at, "source": self.source}

    def set(self, text, source=""):
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if len(text.encode("utf-8")) > self.MAX:
            raise ValueError("the clipboard holds at most 1 MB of text")
        with self._lock:
            self.text, self.at, self.source = text, self.now(), str(source)
        if self.on_change and source != "host":
            self.on_change(text, source)
        return self.get()

    def host_answer(self, text):
        """A console read the host's clipboard (null: it could not, or it is empty): a text becomes the device's."""
        if isinstance(text, str) and text and text != self.text:
            self.set(text, "host")
        with self._cond:
            self._answers += 1
            self._cond.notify_all()

    def pull(self, ask, timeout=1.0):
        """Before a paste: asks the consoles for the host's clipboard (ask() sends the question and returns how many consoles it went to) and waits until all have answered or the
        time is up; the device's own text stands when none had a text."""
        with self._cond:
            self._answers = 0
        n = int(ask())
        if n > 0:
            end = time.time() + timeout
            with self._cond:
                while self._answers < n and time.time() < end:
                    self._cond.wait(end - time.time())
        return self.get()


class MsgBoxes:
    def __init__(self, push, log=print):
        """push(message) -> True when a page took it (the frontend in the shared window); the page answers with answer(id, index)."""
        self.push, self.log = push, log
        self.boxes, self.order = {}, []     # id -> box; ids in the order they were asked, until they are done
        self.lock = threading.RLock()

    def show(self, title="", text="", kind=None, buttons=None, icon="none", default=0, timeout=None, owner=""):
        labels = list(buttons) if buttons is not None else list(KINDS.get(kind or "ok", []))
        if kind is not None and buttons is None and kind not in KINDS:
            raise ValueError(f"unknown kind '{kind}' (one of {', '.join(KINDS)}, or buttons)")
        if not labels or len(labels) > 4 or not all(isinstance(b, str) and b.strip() and len(b) <= 24 for b in labels):
            raise ValueError("buttons: 1..4 texts of up to 24 characters")
        if icon not in ICONS:
            raise ValueError(f"unknown icon '{icon}' (one of {', '.join(ICONS)})")
        if not isinstance(default, int) or not 0 <= default < len(labels):
            raise ValueError("default is the index of a button")
        timeout = DEFAULT_TIMEOUT_S if timeout is None else timeout
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 <= timeout <= MAX_TIMEOUT_S:
            raise ValueError(f"timeout is 0..{MAX_TIMEOUT_S} seconds (0: until answered)")
        box = {"id": uuid.uuid4().hex[:12], "title": str(title)[:60], "text": str(text)[:400], "buttons": labels, "icon": icon, "default": default,
               "owner": owner, "result": None, "event": threading.Event()}
        with self.lock:
            self.boxes[box["id"]] = box
            self.order.append(box["id"])
        if timeout:
            t = threading.Timer(timeout, self._finish, args=(box["id"], -1, "timeout"))
            t.daemon = True
            t.start()
        self.deliver()
        return box["id"]

    def _current(self):
        return self.boxes[self.order[0]] if self.order else None

    def deliver(self):
        """(Re)sends the box that is up to the page: called when one is asked, when a page connects, and when one is done."""
        with self.lock:
            b = self._current()
            if b:
                self.push({"t": "msgbox", "id": b["id"], "title": b["title"], "text": b["text"], "buttons": b["buttons"], "icon": b["icon"], "default": b["default"]})

    def _finish(self, box_id, index, reason):
        with self.lock:
            b = self.boxes.get(box_id)
            if not b or b["result"]:
                return False
            b["result"] = {"id": box_id, "index": index, "button": b["buttons"][index] if 0 <= index < len(b["buttons"]) else None, "reason": reason}
            self.order.remove(box_id)
        b["event"].set()
        self.push({"t": "msgbox_close", "id": box_id})
        self.deliver()
        # a finished box is forgotten a little later: its owner may still ask for the result
        t = threading.Timer(300, lambda: self.boxes.pop(box_id, None))
        t.daemon = True
        t.start()
        return True

    def answer(self, box_id, index):
        """The page: a button was chosen (index) or B was pressed (-1 or None)."""
        with self.lock:
            b = self.boxes.get(box_id)
        if not b:
            return False
        if isinstance(index, int) and 0 <= index < len(b["buttons"]):
            return self._finish(box_id, index, "button")
        return self._finish(box_id, -1, "cancel")

    def cancel(self, box_id, reason="cancelled"):
        return self._finish(box_id, -1, reason)

    def result(self, box_id, wait=0):
        with self.lock:
            b = self.boxes.get(box_id)
        if not b:
            raise KeyError(box_id)
        if not b["result"] and wait:
            b["event"].wait(min(float(wait), MAX_TIMEOUT_S))
        return b["result"] or {"id": box_id, "pending": True}


# ---- HTTP --------------------------------------------------------------------------------------------------------------
def handle(api, method, path, query, body):
    """api: {"sounds", "vibra", "msgbox", "clipboard", "clip_ask", "owner"} (owner: who asks: an app id or a service id). Returns (code, object), or None when the path is not of this API."""
    try:
        if path == "/api/sound/list" and method == "GET":
            return 200, {"sounds": api["sounds"].list()}
        if path == "/api/sound/play" and method == "POST":
            return 200, api["sounds"].play(str(body.get("name", "")), bool(body.get("force")))
        if path == "/api/vibra" and method == "GET":
            return 200, api["vibra"].status()
        if path == "/api/vibra/pulse" and method == "POST":
            return 200, api["vibra"].pulse(body.get("pattern", "short"), body.get("strength", 100), bool(body.get("force")))
        if path == "/api/vibra/stop" and method == "POST":
            return 200, api["vibra"].stop()
        if path == "/api/clipboard" and method == "GET":
            return 200, api["clipboard"].get()
        if path == "/api/clipboard" and method == "POST":
            return 200, api["clipboard"].set(body.get("text"), api["owner"])
        if path == "/api/clipboard/clear" and method == "POST":
            return 200, api["clipboard"].set("", api["owner"])
        if path == "/api/clipboard/pull" and method == "POST":
            return 200, api["clipboard"].pull(api["clip_ask"])
        if path == "/api/msgbox" and method == "POST":
            mb = api["msgbox"]
            wait = bool(body.get("wait"))
            box_id = mb.show(body.get("title", ""), body.get("text", ""), body.get("kind"), body.get("buttons"), body.get("icon", "none"), body.get("default", 0), body.get("timeout"), api["owner"])
            return 200, (mb.result(box_id, MAX_TIMEOUT_S) if wait else {"id": box_id})
        if path == "/api/msgbox/result" and method == "GET":
            return 200, api["msgbox"].result(query.get("id", ""), float(query.get("wait", 0) or 0))
        if path == "/api/msgbox/cancel" and method == "POST":
            return 200, {"ok": api["msgbox"].cancel(str(body.get("id", "")))}
    except KeyError:
        return 404, {"error": "no such message box"}
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    return None
