#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""calendard: the backend of the Calendar app (a service of OS Beroun, see manifest.json): events and the task list, kept in INTELLICAT_DATA/calendar.json.
Reminders (notifications of the panel, also while the console sleeps): a timed event 10 minutes before and at its time, an event without a time at 08:00 of its day,
the open tasks due today at 09:00 (one notification, or one per task up to three); by the console's own clock.
Endpoints (passed on by the shell as /api/app/calendar/<path>): GET events[?from=YYYY-MM-DD&to=YYYY-MM-DD], POST events/save, events/delete;
GET tasks[?done=0|1], POST tasks/save, tasks/toggle, tasks/delete. Usage: calendard.py <port>"""
import datetime as dt, json, os, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SHELL = f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}"
EARLY_MIN, ALLDAY_AT, TASKS_AT = 10, "08:00", "09:00"
PORT = int(os.environ.get("INTELLICAT_SERVICE_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 48802))
FILE = os.path.join(os.environ.get("INTELLICAT_DATA", os.path.expanduser("~/.intellicat/data")), "calendar.json")
LOCK = threading.RLock()
DATA = {"events": [], "tasks": [], "next_id": 1}


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


def date(s, required=True):
    if not s and not required:
        return None
    return dt.date.fromisoformat(str(s)).isoformat()


def clock(s):
    if not s:
        return None
    h, m = str(s).split(":")
    if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
        raise ValueError("time is HH:MM")
    return f"{int(h):02d}:{int(m):02d}"


def upsert(kind, fields, body):
    items = DATA[kind]
    old = next((x for x in items if x["id"] == body.get("id")), None)
    if old:
        old.update(fields)
        item = old
    else:
        item = dict(fields, id=DATA["next_id"])
        DATA["next_id"] += 1
        items.append(item)
    items.sort(key=lambda x: (x.get("date") or x.get("due") or "9999", x.get("time") or "", x["id"]))
    save()
    return item


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
        u = urlparse(self.path)
        p, q = u.path.strip("/"), {k: v[0] for k, v in parse_qs(u.query).items()}
        with LOCK:
            if p == "events":
                lo, hi = q.get("from", "0000-01-01"), q.get("to", "9999-12-31")
                return self.reply({"events": [e for e in DATA["events"] if lo <= e["date"] <= hi]})
            if p == "tasks":
                t = DATA["tasks"] if "done" not in q else [x for x in DATA["tasks"] if x["done"] == (q["done"] == "1")]
                return self.reply({"tasks": t})
        self.reply({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.strip("/")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            with LOCK:
                if p == "events/save":      # {id?, title, date, time?, note?}
                    title = str(body["title"]).strip()[:80]
                    if not title:
                        raise ValueError("title is required")
                    return self.reply({"event": upsert("events", {"title": title, "date": date(body["date"]), "time": clock(body.get("time")), "note": str(body.get("note", ""))[:200]}, body)})
                if p == "tasks/save":       # {id?, title, due?}
                    title = str(body["title"]).strip()[:80]
                    if not title:
                        raise ValueError("title is required")
                    old = next((x for x in DATA["tasks"] if x["id"] == body.get("id")), None)
                    return self.reply({"task": upsert("tasks", {"title": title, "due": date(body.get("due"), False), "done": old["done"] if old else False}, body)})
                if p in ("events/delete", "tasks/delete", "tasks/toggle"):
                    kind = p.split("/")[0]
                    x = next((i for i in DATA[kind] if i["id"] == body.get("id")), None)
                    if not x:
                        return self.reply({"error": "no such item"}, 404)
                    if p.endswith("delete"):
                        DATA[kind].remove(x)
                    else:
                        x["done"] = bool(body.get("done", not x["done"]))
                    save()
                    return self.reply({"ok": True, "item": x})
        except (KeyError, ValueError, TypeError) as e:
            return self.reply({"error": str(e)}, 400)
        self.reply({"error": "not found"}, 404)


def shell(path, body=None):
    headers = {"Content-Type": "application/json", "X-IntelliCat-Service-Id": os.environ.get("INTELLICAT_SERVICE_ID", "calendard")}
    req = urllib.request.Request(SHELL + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"{}")


def due_reminders(today, hhmm, minutes_of_day, items):
    """[(key, text)] of what is due at this minute: pure, so that it can be tested. items: {"events": [...], "tasks": [...]}."""
    out = []
    for e in items["events"]:
        if e["date"] != today:
            continue
        if e.get("time"):
            h, m = e["time"].split(":")
            at = int(h) * 60 + int(m)
            if minutes_of_day == at - EARLY_MIN:
                out.append((("early", e["id"], today), f"{e['title']} in {EARLY_MIN} minutes ({e['time']})"))
            if minutes_of_day == at:
                out.append((("now", e["id"], today), f"{e['title']} now"))
        elif hhmm == ALLDAY_AT:
            out.append((("day", e["id"], today), f"Today: {e['title']}"))
    if hhmm == TASKS_AT:
        tasks = [t for t in items["tasks"] if t.get("due") == today and not t["done"]]
        if 0 < len(tasks) <= 3:
            out += [(("task", t["id"], today), f"Task due today: {t['title']}") for t in tasks]
        elif tasks:
            out.append((("tasks", 0, today), f"{len(tasks)} tasks due today"))
    return out


def remind_loop():
    fired = set()
    while True:
        time.sleep(15)
        try:
            l = shell("/api/time/status")["local"]
            today, hhmm = f"{l['year']}-{l['month']:02d}-{l['day']:02d}", f"{l['hour']:02d}:{l['minute']:02d}"
            fired = {k for k in fired if k[2] == today}
            with LOCK:
                items = {"events": list(DATA["events"]), "tasks": list(DATA["tasks"])}
            for key, text in due_reminders(today, hhmm, l["hour"] * 60 + l["minute"], items):
                if key not in fired:
                    fired.add(key)
                    shell("/api/notify", {"text": text, "app": "Calendar", "level": "warning" if key[0] in ("early", "now") else "info",
                                          "sound": key[0] in ("early", "now"), "vibrate": key[0] in ("early", "now")})
        except (OSError, ValueError, KeyError) as e:
            print(f"calendard: reminder check failed: {e}", flush=True)


if __name__ == "__main__":
    load()
    threading.Thread(target=remind_loop, daemon=True, name="reminders").start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
