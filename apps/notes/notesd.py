#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""notesd: the service of the app `notes` (the owner's SquirrelWisdom notes, the same ones as squirrelwisdom.com/portal/notes.html).

    notesd.py <port>

The notes are kept on the device (INTELLICAT_DATA/notes.json) and synchronised with the account's Notes plugin in the background: the app works without
a network, a note written offline goes up when the network is back. The protocol is portal/NOTES_API.md of the squirrel_wisdom repository (`plugin:call`,
plugin `Notes`: readIndex, writeNoteFile, patchIndex); the session is the device's own (the shell gives it to this service only: permission account.session).
Conflicts are decided as the web client does (mergeNotes): the note with the newer `updatedAt` wins, per note. Deleting is soft (`deleted: true`).

The page and Caroline's MCP server reach it through the shell (/api/app/notes/<path>):

    GET  list?folder=A/B      {folder, all: [every folder path], folders: [{name, path, count}], notes: [{id, title, preview, updatedAt, deadline}], sync}
    GET  search?q=words       {notes: [{id, title, preview, folder, updatedAt}]}   (every word must occur, any case)
    GET  note?id=             {id, text, folder, updatedAt, deadline, reminderLead}
    POST save                 {id?, text, folder?, deadline?, reminderLead?} -> {id}    no id: a new note
    POST delete               {id}
    POST move                 {id, folder}
    POST folder               {op: create | rename | delete, path, to?}
    POST sync                 starts a synchronisation now
    GET  status               {state, last, error, dirty, notes}   state: idle | syncing | offline | signin | config | error

The key of the Notes plugin is not in this repository: $INTELLICAT_NOTES_KEY or "notes_key" in ~/.intellicat/keys.json ($INTELLICAT_KEYS).
The address of the service is $INTELLICAT_SW_URL (the shell's own setting; a test points it at a stand-in).
"""

import json
import os
import secrets
import string
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SW_URL = os.environ.get("INTELLICAT_SW_URL", "https://www.squirrelwisdom.com/")
KEYS_PATH = os.environ.get("INTELLICAT_KEYS", os.path.expanduser("~/.intellicat/keys.json"))
SHELL = f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}"
SID = os.environ.get("INTELLICAT_SERVICE_ID", "notesd")
DATA = os.environ.get("INTELLICAT_DATA", os.path.expanduser("~/.intellicat/data"))
FILE = os.path.join(DATA, "notes.json")
SYNC_EVERY_S = float(os.environ.get("NOTES_SYNC_S") or 60)
PUSH_DELAY_S = float(os.environ.get("NOTES_PUSH_DELAY_S") or 1)     # a burst of edits goes up as one
ID_CHARS = string.ascii_letters + string.digits
MAX_TEXT = 1_000_000

lock = threading.RLock()
wake = threading.Event()
cache = {"notes": {}, "dirty": []}       # notes: {id: note object as in NOTES_API.md}; dirty: ids written here and not yet sent
sync = {"state": "idle", "last": 0, "error": ""}
_session = {"value": None}


class NotesError(Exception):
    """kind: signin | network | config | service"""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- the local copy ----------------------------------------------------------------------------------------------------------------------

def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("notes"), dict):
            cache["notes"] = d["notes"]
            cache["dirty"] = [i for i in d.get("dirty", []) if i in d["notes"]]
    except (OSError, ValueError):
        pass


def save_file():
    os.makedirs(DATA, exist_ok=True)
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, FILE)


def touch(nid, note):
    """A note written here: stored with a new updatedAt (never lower than the old one: two edits in one millisecond), sent on the next push."""
    with lock:
        old = cache["notes"].get(nid) or {}
        note["updatedAt"] = max(int(time.time() * 1000), int(old.get("updatedAt") or 0) + 1)
        cache["notes"][nid] = note
        if nid not in cache["dirty"]:
            cache["dirty"].append(nid)
        save_file()
    wake.set()


# ---- the account -------------------------------------------------------------------------------------------------------------------------

def notes_key():
    k = os.environ.get("INTELLICAT_NOTES_KEY")
    if not k:
        try:
            k = json.load(open(KEYS_PATH, encoding="utf-8-sig")).get("notes_key")
        except (OSError, ValueError):
            k = None
    if not k:
        raise NotesError("config", f"no notes_key configured ({KEYS_PATH})")
    return k


def session(fresh=False):
    if _session["value"] and not fresh:
        return _session["value"]
    req = urllib.request.Request(SHELL + "/api/account/session" + ("?fresh=1" if fresh else ""), headers={"X-IntelliCat-Service-Id": SID})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            _session["value"] = json.loads(r.read())["session"]
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
        kind = body.get("kind")
        raise NotesError("signin" if kind == "auth" else "network" if kind == "network" else "service", body.get("error") or "No account")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise NotesError("service", f"The shell gave no session ({e})")
    return _session["value"]


def plugin(action, **params):
    """One call of the Notes plugin; a session it calls expired is minted again once. Returns the plugin's own result."""
    key = notes_key()
    for attempt in (0, 1):
        body = dict(params, command="plugin:call", plugin="Notes", query=action, action=action, key=key, session=session(fresh=bool(attempt)))
        req = urllib.request.Request(SW_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise NotesError("network" if e.code >= 500 else "service", f"HTTP {e.code} from the notes service")   # 5xx: a proxy with nothing behind it
        except (urllib.error.URLError, OSError) as e:
            raise NotesError("network", str(getattr(e, "reason", e)))
        except ValueError:
            raise NotesError("service", "The notes service answered with something else than JSON")
        if out.get(".status") == "ok":
            return out.get("result") or {}
        why = str(out.get(".reason") or "")
        if attempt == 0 and "session" in why.lower():
            continue
        raise NotesError("signin" if "session" in why.lower() else "service", why or "The notes service refused")
    raise NotesError("signin", "The session was refused")


# ---- synchronisation (the web client's rule: the newer updatedAt wins, per note) -----------------------------------------------------------

def sync_once():
    remote = plugin("readIndex").get("notes") or {}
    pull = 0
    with lock:
        for nid, rn in remote.items():
            ln = cache["notes"].get(nid)
            if ln is None or int(rn.get("updatedAt") or 0) > int(ln.get("updatedAt") or 0):
                cache["notes"][nid] = rn         # theirs is newer (also over a note written here meanwhile: the web client does the same)
                if nid in cache["dirty"]:
                    cache["dirty"].remove(nid)
                pull += 1
        todo = [(i, dict(cache["notes"][i])) for i in cache["dirty"] if i in cache["notes"]]
        save_file()
    sent = 0
    if todo:
        for nid, note in todo:
            plugin("writeNoteFile", id=nid, note=note)
        plugin("patchIndex", entries={nid: note for nid, note in todo})     # after the files, as NOTES_API.md says
        with lock:
            for nid, note in todo:
                if cache["notes"].get(nid, {}).get("updatedAt") == note["updatedAt"] and nid in cache["dirty"]:
                    cache["dirty"].remove(nid)   # not edited again while it went up
            save_file()
        sent = len(todo)
    log(f"sync: {pull} pulled, {sent} sent, {len(cache['notes'])} notes")


def sync_loop():
    while True:
        woken = wake.wait(SYNC_EVERY_S)
        if woken:
            time.sleep(PUSH_DELAY_S)         # more edits may follow at once
            wake.clear()
        with lock:
            sync["state"] = "syncing"
        try:
            sync_once()
            with lock:
                sync.update(state="idle", last=int(time.time()), error="")
        except NotesError as e:
            with lock:
                sync.update(state={"network": "offline", "signin": "signin", "config": "config"}.get(e.kind, "error"), error=str(e))
            log("sync failed:", e.kind, e)
        except Exception as e:  # noqa: BLE001 - the loop must not die
            with lock:
                sync.update(state="error", error=str(e))
            log("sync crashed:", repr(e))


# ---- the notes ----------------------------------------------------------------------------------------------------------------------------

def norm_folder(raw):
    return "/".join(s.strip() for s in str(raw or "").replace("\\", "/").split("/") if s.strip())


def active():
    return {i: n for i, n in cache["notes"].items() if not n.get("deleted")}


def title_of(text):
    for line in str(text or "").split("\n"):
        if line.strip():
            return line.strip()[:120]
    return ""


def preview_of(text):
    lines = [l.strip() for l in str(text or "").split("\n") if l.strip()]
    return lines[1][:100] if len(lines) > 1 else ""


def order_of(n):
    return float(n["order"]) if n.get("order") is not None else float(n.get("updatedAt") or 0)


def brief(i, n, with_folder=False):
    out = {"id": i, "title": title_of(n.get("text")) or "(empty)", "preview": preview_of(n.get("text")), "updatedAt": n.get("updatedAt") or 0}
    if n.get("deadline"):
        out["deadline"] = n["deadline"]
    if with_folder:
        out["folder"] = norm_folder(n.get("folder"))
    return out


def in_folder(n, path):
    f = norm_folder(n.get("folder"))
    return f == path or f.startswith(path + "/")


def op_list(q):
    base = norm_folder(q.get("folder", [""])[0])
    with lock:
        act = active()
        kids = {}
        for n in act.values():
            f = norm_folder(n.get("folder"))
            if not f or f == base or (base and not f.startswith(base + "/")):
                continue
            name = (f[len(base) + 1:] if base else f).split("/")[0]
            kids.setdefault(name, 0)
            if not n.get("isFolderMarker"):
                kids[name] += 1
        notes = sorted(((i, n) for i, n in act.items() if not n.get("isFolderMarker") and norm_folder(n.get("folder")) == base), key=lambda p: -order_of(p[1]))
        every = set()
        for n in act.values():
            parts = norm_folder(n.get("folder")).split("/")
            every.update("/".join(parts[:k]) for k in range(1, len(parts) + 1) if parts[0])
        return {"folder": base, "all": sorted(every, key=str.lower), "folders": [{"name": k, "path": (base + "/" if base else "") + k, "count": c} for k, c in sorted(kids.items(), key=lambda kv: kv[0].lower())],
                "notes": [brief(i, n) for i, n in notes], "sync": status()}


def op_search(q):
    words = [w.lower() for w in q.get("q", [""])[0].split() if w]
    if not words:
        return {"notes": []}
    with lock:
        hits = [(i, n) for i, n in active().items() if not n.get("isFolderMarker") and all(w in (str(n.get("text") or "") + " " + norm_folder(n.get("folder"))).lower() for w in words)]
    hits.sort(key=lambda p: -order_of(p[1]))
    return {"notes": [brief(i, n, True) for i, n in hits[:100]]}


def op_note(q):
    nid = q.get("id", [""])[0]
    with lock:
        n = cache["notes"].get(nid)
        if not n or n.get("deleted") or n.get("isFolderMarker"):
            return {"error": "no such note"}, 404
        return {"id": nid, "text": n.get("text") or "", "folder": norm_folder(n.get("folder")), "updatedAt": n.get("updatedAt") or 0,
                "deadline": n.get("deadline"), "reminderLead": n.get("reminderLead")}


def new_id():
    while True:
        nid = "".join(secrets.choice(ID_CHARS) for _ in range(12))
        if nid not in cache["notes"]:
            return nid


def op_save(b):
    text = b.get("text")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        return {"error": "text must be a string of at most 1 000 000 characters"}, 400
    with lock:
        nid = str(b.get("id") or "")
        if nid:
            old = cache["notes"].get(nid)
            if not old or old.get("deleted"):
                return {"error": "no such note"}, 404
            note = dict(old)               # fields this service does not know stay as they are
        else:
            nid, note = new_id(), {"deleted": False}
        note["text"] = text
        if "folder" in b:
            note["folder"] = norm_folder(b["folder"])
        note.setdefault("folder", "")
        for k in ("deadline", "reminderLead"):
            if k in b:
                if b[k] in (None, ""):
                    note.pop(k, None)
                else:
                    note[k] = b[k]
        touch(nid, note)
    return {"id": nid}


def op_delete(b):
    with lock:
        n = cache["notes"].get(str(b.get("id")))
        if not n or n.get("deleted"):
            return {"error": "no such note"}, 404
        touch(str(b["id"]), dict(n, deleted=True))
    return {"ok": True}


def op_move(b):
    with lock:
        n = cache["notes"].get(str(b.get("id")))
        if not n or n.get("deleted"):
            return {"error": "no such note"}, 404
        touch(str(b["id"]), dict(n, folder=norm_folder(b.get("folder"))))
    return {"ok": True}


def op_folder(b):
    op, path, to = b.get("op"), norm_folder(b.get("path")), norm_folder(b.get("to"))
    if not path or op not in ("create", "rename", "delete"):
        return {"error": "op is create, rename or delete and path is a folder"}, 400
    with lock:
        if op == "create":
            if any(norm_folder(n.get("folder")) == path for n in active().values()):
                return {"ok": True}
            touch(new_id(), {"text": "", "deleted": False, "folder": path, "isFolderMarker": True})
            return {"ok": True}
        hit = [(i, n) for i, n in active().items() if in_folder(n, path)]
        if op == "rename":
            if not to:
                return {"error": "to is the new path"}, 400
            if to == path or to.startswith(path + "/"):
                return {"error": "a folder cannot move into itself"}, 400
            for i, n in hit:
                touch(i, dict(n, folder=to + norm_folder(n.get("folder"))[len(path):]))
        else:
            for i, n in hit:
                touch(i, dict(n, deleted=True))
    return {"ok": True, "notes": len(hit)}


def status():
    with lock:
        return {"state": sync["state"], "last": sync["last"], "error": sync["error"], "dirty": len(cache["dirty"]), "notes": len(active())}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def route(self, table, arg):
        path = urllib.parse.urlparse(self.path).path.rstrip("/").rsplit("/", 1)[-1]
        fn = table.get(path)
        if not fn:
            return self.reply({"error": "not found"}, 404)
        out = fn(arg)
        if isinstance(out, tuple):
            return self.reply(*out)
        self.reply(out)

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.route({"list": op_list, "search": op_search, "note": op_note, "status": lambda _: status()}, q)

    def do_POST(self):
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return self.reply({"error": "the body is a JSON object"}, 400)
        self.route({"save": op_save, "delete": op_delete, "move": op_move, "folder": op_folder, "sync": lambda _: (wake.set(), {"ok": True})[1]}, b)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("INTELLICAT_SERVICE_PORT", "48804"))
    load()
    threading.Thread(target=sync_loop, daemon=True, name="notes-sync").start()
    wake.set()
    log("notesd on", port, "notes", len(cache["notes"]))
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
