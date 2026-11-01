#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""alarmd: the backend of the Clock app (a service of OS Beroun, see manifest.json). It survives the close of the app and runs while the console sleeps.

Keeps the alarms and the timer in INTELLICAT_DATA/clock.json, rings an alarm by taking over the shared window (POST /api/window/takeover, which the manifest
permits) and beeping until it is dismissed or snoozed. The time is the console's own (GET /api/time/status), not the host's.
Endpoints (passed on by the shell as /api/app/clock/<path>): GET alarms, POST alarms/save, alarms/delete, alarms/toggle; GET ringing, POST ringing/dismiss,
ringing/snooze; GET timer, POST timer/start, timer/cancel.
"""
import datetime as dt, json, os, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("INTELLICAT_SERVICE_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 48801))
SHELL = f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}"
FILE = os.path.join(os.environ.get("INTELLICAT_DATA", os.path.expanduser("~/.intellicat/data")), "clock.json")
SNOOZE_MIN, RING_MAX_S = 9, 300
LOCK = threading.RLock()
DATA = {"alarms": [], "timer": None, "next_id": 1}
RING = {"alarm": None, "since": 0}   # what rings now: {"id", "label", "kind": "alarm" | "timer", "hour", "minute"}


def load():
    try:
        DATA.update(json.load(open(FILE, encoding="utf-8")))
    except (OSError, ValueError):
        pass


def save():
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + ".tmp"
    json.dump(DATA, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, FILE)


def shell(path, body=None):
    headers = {"Content-Type": "application/json", "X-IntelliCat-Service-Id": os.environ.get("INTELLICAT_SERVICE_ID", "alarmd")}
    req = urllib.request.Request(SHELL + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"{}")


def now():
    """The console's time: (epoch, weekday 0=Monday, hour, minute)."""
    st = shell("/api/time/status")
    l = st["local"]
    return st["epoch"], dt.date(l["year"], l["month"], l["day"]).weekday(), l["hour"], l["minute"]


def next_due():
    """The epoch of the next alarm, snooze or timer, or 0 when there is none. The shell hands it to the phone's own alarm (on Android a Python service is not woken
    reliably in sleep; Android wakes the app at this time and alarmd rings as always)."""
    st = shell("/api/time/status")
    epoch, l = st["epoch"], st["local"]
    wd0 = dt.date(l["year"], l["month"], l["day"]).weekday()
    since_midnight = l["hour"] * 3600 + l["minute"] * 60 + l["second"]
    best = []
    with LOCK:
        t = DATA.get("timer")
        if t:
            best.append(t["end"])
        for a in DATA["alarms"]:
            if not a["enabled"]:
                continue
            if a.get("snoozed_until") is not None:
                best.append(a["snoozed_until"])
                continue
            for day in range(8):
                when = epoch - since_midnight + day * 86400 + a["hour"] * 3600 + a["minute"] * 60
                if when > epoch and (not a["days"] or (wd0 + day) % 7 in a["days"]):
                    best.append(when)
                    break
    return min(best) if best else 0


def schedule():
    try:
        shell("/api/alarm/schedule", {"epoch": next_due()})
    except Exception as e:  # noqa: BLE001  (the shell is not up yet, or the phone's alarm refused: the loop below still rings by itself while awake)
        print(f"alarmd: the shell's alarm was not set: {e}", flush=True)


def clean(a):
    h, m = int(a.get("hour", 7)), int(a.get("minute", 0))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("hour 0..23, minute 0..59")
    days = sorted({int(d) for d in a.get("days", [])})
    if any(not 0 <= d <= 6 for d in days):
        raise ValueError("days are 0 (Monday) .. 6 (Sunday); an empty list is once")
    return {"hour": h, "minute": m, "days": days, "label": str(a.get("label", ""))[:40], "enabled": bool(a.get("enabled", True)), "snoozed_until": None}


def ring(alarm, kind):
    with LOCK:
        RING.update(alarm={"id": alarm.get("id"), "label": alarm.get("label", ""), "kind": kind, "hour": alarm.get("hour"), "minute": alarm.get("minute")}, since=time.time())
    try:
        shell("/api/window/takeover", {"app": "clock", "arg": {"ring": True}})
        shell("/api/notify", {"text": alarm.get("label") or ("Timer" if kind == "timer" else "Alarm"), "app": "Clock", "level": "warning", "sound": True, "vibrate": True})
    except OSError as e:
        print(f"alarmd: the window was not taken: {e}", flush=True)


def tick(last):
    """One look at the clock; returns the minute in which an alarm last fired."""
    epoch, wd, h, m = now()
    minute_key = epoch // 60
    with LOCK:
        if RING["alarm"]:
            if time.time() - RING["since"] > RING_MAX_S:   # nobody answered: it stops by itself
                RING["alarm"] = None
            else:
                try:
                    shell("/api/beep", {})
                except OSError:
                    pass
            return last
        t = DATA.get("timer")
        if t and epoch >= t["end"]:
            DATA["timer"] = None
            save()
            ring({"label": t.get("label", "")}, "timer")
            schedule()
            return last
        for a in DATA["alarms"]:
            if not a["enabled"]:
                continue
            su = a.get("snoozed_until")
            due = epoch >= su if su is not None else (minute_key != last and (h, m) == (a["hour"], a["minute"]) and (not a["days"] or wd in a["days"]))
            if due:
                a["snoozed_until"] = None
                if not a["days"]:
                    a["enabled"] = False   # a single alarm rings once
                save()
                ring(a, "alarm")
                schedule()
                return minute_key   # the minute an alarm fired in: no other alarm of the same minute fires again after it is dismissed
    return last


def loop():
    last = None
    while True:
        try:
            last = tick(last)
        except Exception as e:  # the alarm clock must survive anything (the shell restarting, a bad answer)
            print(f"alarmd: tick failed: {e}", flush=True)
        time.sleep(2 if RING["alarm"] else 1)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0].strip("/")
        with LOCK:
            if p == "alarms":
                return self.reply({"alarms": DATA["alarms"]})
            if p == "ringing":
                return self.reply({"ringing": RING["alarm"]})
            if p == "timer":
                return self.reply({"timer": DATA["timer"], "now": now()[0]})
        self.reply({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.strip("/")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            with LOCK:
                if p == "alarms/save":      # {id?, hour, minute, days, label, enabled}: a new alarm without id
                    a = clean(body)
                    old = next((x for x in DATA["alarms"] if x["id"] == body.get("id")), None)
                    if old:
                        old.update(a)
                        a = old
                    else:
                        a["id"] = DATA["next_id"]
                        DATA["next_id"] += 1
                        DATA["alarms"].append(a)
                    DATA["alarms"].sort(key=lambda x: (x["hour"], x["minute"]))
                    save()
                    schedule()
                    return self.reply({"alarm": a})
                if p in ("alarms/delete", "alarms/toggle"):
                    a = next((x for x in DATA["alarms"] if x["id"] == body.get("id")), None)
                    if not a:
                        return self.reply({"error": "no such alarm"}, 404)
                    if p.endswith("delete"):
                        DATA["alarms"].remove(a)
                    else:
                        a["enabled"] = bool(body.get("enabled", not a["enabled"]))
                        a["snoozed_until"] = None
                    save()
                    schedule()
                    return self.reply({"ok": True, "alarm": a})
                if p == "ringing/dismiss":
                    RING["alarm"] = None
                    schedule()
                    return self.reply({"ok": True})
                if p == "ringing/snooze":
                    r = RING["alarm"]
                    a = next((x for x in DATA["alarms"] if x["id"] == r["id"]), None) if r and r["kind"] == "alarm" else None
                    if a:
                        a["enabled"], a["snoozed_until"] = True, now()[0] + int(body.get("minutes", SNOOZE_MIN)) * 60
                        save()
                    RING["alarm"] = None
                    schedule()
                    return self.reply({"ok": True})
                if p == "timer/start":      # {seconds, label?}
                    s = int(body["seconds"])
                    if not 1 <= s <= 24 * 3600:
                        raise ValueError("seconds 1..86400")
                    DATA["timer"] = {"end": now()[0] + s, "seconds": s, "label": str(body.get("label", ""))[:40]}
                    save()
                    schedule()
                    return self.reply({"timer": DATA["timer"]})
                if p == "timer/cancel":
                    DATA["timer"] = None
                    save()
                    schedule()
                    return self.reply({"ok": True})
        except (KeyError, ValueError, TypeError) as e:
            return self.reply({"error": str(e)}, 400)
        self.reply({"error": "not found"}, 404)


if __name__ == "__main__":
    load()
    threading.Thread(target=lambda: (time.sleep(5), schedule()), daemon=True).start()   # once the shell answers: what is saved is set in Android's alarm again (a reboot clears it)
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
