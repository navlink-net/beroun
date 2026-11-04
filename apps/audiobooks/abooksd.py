#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""abooksd: the service of the app `audiobooks` (the audiobooks of squirrelwisdom.com/portal/audiobooks.html: the public catalog and the owner's own books).

    abooksd.py <port>

The catalogs are plain files of the site (contents/AudioBooks/audiobooks.json, contents/AudioBooks/audiobooks_<hash>.json), read without a session; the owner's own
books need the hash of the account's login (the first 16 hex characters of its SHA-256), which the service gets from the device's session (permission account.session;
the command user:getSessionUser). A book is a directory of the site with numbered MP3 files (contents/AudioBooks/<directory>/NNNN.mp3). The sound is not copied: the
page plays the files from the site (the files answer with CORS and Range); this service gives the page the list, the addresses and the place where the listener stopped.

The page and Caroline's MCP server reach it through the shell (/api/app/audiobooks/<path>):

    GET  catalog?scope=public|mine&q=&language=&genre=&sort=title|author   {scope, signedIn, books: [{id, scope, title, author, narrator, language, genre, description, cover, files, listening}], languages, genres, stale}
    GET  recent                                                           {books: [...]}  the books being listened to, the last first
    GET  book?scope=&id=        {id, scope, title, author, narrator, description, cover, base, files: [name], listening: {index, time, speed, file, at}|null, speed}   base: the address the files are under
    POST progress               {scope, id, index, time, speed?}   the place: file number and second in it
    GET  bookmarks?scope=&id=   {bookmarks: [{id, index, file, time, label, at, device}]}   in listening order
    POST bookmark               {scope, id, index, time, label?}  adds a bookmark at that place -> {bookmark, bookmarks}
    POST unbookmark             {scope, id, bookmark_id}          -> {bookmarks}
    GET  settings               {speed}                           playback speed (0.5-3, 1 = normal)
    POST settings               {speed}                           -> the settings now in force
    GET  status                 {state, books}

The place and the speed are kept on the site too (the plugin `Audiobooks`, apps/Audiobooks in the reforce repo, per account), so the web player and every device go on from
the same second. The device's copy is the working one: a change is saved here at once, queued, and sent when the site answers (a poor or no link only delays it); what the
site holds is pulled when a book is opened. The book of the site is "<scope>:<directory>", the same on every client; a place keeps the file's name too, so a book that
gained or lost files keeps it.

The key is the ONE app key of the site (the one in every page of squirrelwisdom.com, the same as notes_key and books_key; not a key per device, not a secret of the account;
its grants on the site include user:getSessionUser and plugin:call for the plugin Audiobooks). It is $INTELLICAT_BOOKS_KEY or "books_key" in ~/.intellicat/keys.json
($INTELLICAT_KEYS); it is not in this repository. The address of the site is $INTELLICAT_SW_URL (the shell's setting; a test points it at a stand-in).
"""

import hashlib
import json
import os
import secrets
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
SID = os.environ.get("INTELLICAT_SERVICE_ID", "abooksd")
DATA = os.path.join(os.environ.get("INTELLICAT_DATA", os.path.expanduser("~/.intellicat/data")), "audiobooks")
LIST_TTL_S = float(os.environ.get("ABOOKS_LIST_TTL_S") or 300)   # a catalog is asked again after this
MAX_LIST_BYTES = 20 * 1024 * 1024
DEVICE = "beroun"
SPEED_MIN, SPEED_MAX = 0.5, 3.0
BASE = "contents/AudioBooks/"

lock = threading.RLock()
state = {"hash": None, "lists": {}}      # hash: the account's 16 hex; lists: {scope: (fetched at, [books])}
_session = {"value": None}


class AbError(Exception):
    """kind: network | signin | config | service | notfound | rejected"""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- the site ---------------------------------------------------------------------------------------------------------------------------

def app_key():
    k = os.environ.get("INTELLICAT_BOOKS_KEY")
    if not k:
        try:
            k = json.load(open(KEYS_PATH, encoding="utf-8-sig")).get("books_key")
        except (OSError, ValueError):
            k = None
    if not k:
        raise AbError("config", f"no books_key configured ({KEYS_PATH})")
    return k


RETRIES = int(os.environ.get("ABOOKS_RETRIES") or 3)
RETRY_PAUSE_S = float(os.environ.get("ABOOKS_RETRY_PAUSE_S") or 1.5)


def with_retries(fn, what, tries=None):
    """Runs fn(); a network failure (or a 5xx of the site) is tried again after a growing pause, any other error at once."""
    tries = tries or RETRIES
    for attempt in range(tries):
        try:
            return fn()
        except AbError as e:
            if e.kind != "network" or attempt == tries - 1:
                raise
            pause = RETRY_PAUSE_S * 2 ** attempt
            log(what, "failed:", e, f"- again in {pause:g} s ({attempt + 2}/{tries})")
            time.sleep(pause)


def get_file(path, limit=MAX_LIST_BYTES):
    def once():
        req = urllib.request.Request(urllib.parse.urljoin(SW_URL, path), headers={"Cache-Control": "no-cache"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise AbError("notfound" if e.code == 404 else "network" if e.code >= 500 else "service", f"HTTP {e.code} for {path}")
        except (urllib.error.URLError, OSError) as e:
            raise AbError("network", str(getattr(e, "reason", e)))
        if len(data) > limit:
            raise AbError("service", f"{path} is too big")
        return data
    return with_retries(once, path)


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
        raise AbError("signin" if kind == "auth" else "network" if kind == "network" else "service", body.get("error") or "No account")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise AbError("service", f"The shell gave no session ({e})")
    return _session["value"]


def post_site(body, timeout, tries=None, what="site"):
    req = urllib.request.Request(SW_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})

    def ask():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError) as e:
            raise AbError("network", str(getattr(e, "reason", e)))
        except ValueError:
            raise AbError("service", "The site answered with something else than JSON")
    return with_retries(ask, what, tries=tries)


def user_hash():
    """The 16 hex characters of the SHA-256 of the account's login: the owner's name on the site. Kept on the device once known."""
    with lock:
        if state["hash"]:
            return state["hash"]
    cached = load_json("account.json", {}).get("hash")
    if cached:
        with lock:
            state["hash"] = cached
        return cached
    key = app_key()
    for attempt in (0, 1):
        out = post_site({"command": "user:getSessionUser", "session": session(fresh=bool(attempt)), ".msgid": int(time.time() * 1000), "key": key}, 40, what="user:getSessionUser")
        if out.get(".status") == "ok" and out.get("user"):
            h = hashlib.sha256(str(out["user"]).encode()).hexdigest()[:16]
            save_json("account.json", {"hash": h})
            with lock:
                state["hash"] = h
            return h
    raise AbError("signin", "The session was refused")


# ---- the device's copy -----------------------------------------------------------------------------------------------------------------

def path_of(*parts):
    return os.path.join(DATA, *parts)


def load_json(name, default):
    try:
        with open(path_of(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(name, obj):
    os.makedirs(os.path.dirname(path_of(name)) or DATA, exist_ok=True)
    tmp = path_of(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path_of(name))


def valid_scope(scope):
    return scope if scope in ("public", "mine") else None


def listen_state():
    return load_json("listen.json", {})       # {scope:id: {scope, id, title, author, index, file, time, speed, at(s), ms}}


# ---- the site's copy of the listening state (place, speed) ---------------------------------------------------------------------------------

SYNC_TIMEOUT_S = float(os.environ.get("ABOOKS_SYNC_TIMEOUT_S") or 10)
SYNC_BACKOFF_S = float(os.environ.get("ABOOKS_SYNC_BACKOFF_S") or 60)       # after a failed call the site is left alone for this long
REJECT_BACKOFF_S = float(os.environ.get("ABOOKS_REJECT_BACKOFF_S") or 300)
MAX_TRIES = 20                                                              # a queued change the site keeps refusing is dropped after this many tries
MAX_PENDING = 500
MAX_BOOKMARKS = 300                                                          # per book, as on the site
FLUSH_EVERY_S = float(os.environ.get("ABOOKS_FLUSH_EVERY_S") or 30)         # what the site has not got is offered again this often, with no new change needed
state.update(offline_until=0.0)
plock = threading.Lock()          # pending.json
flush_guard = threading.Lock()    # one sender at a time


def sw_call(action, **fields):
    """One action of the site's plugin Audiobooks for the account (a v2 plugin:call); the plugin's result dict. kind: network | signin | config | rejected | service"""
    key = app_key()
    for attempt in (0, 1):
        body = dict(fields, command="plugin:call", plugin="Audiobooks", query=action, action=action, session=session(fresh=bool(attempt)), key=key)
        body[".msgid"] = int(time.time() * 1000)
        out = post_site(body, SYNC_TIMEOUT_S, tries=1, what="Audiobooks." + action)
        if out.get(".status") == "ok" and isinstance(out.get("result"), dict):
            return out["result"]
        reason = str(out.get(".reason") or out.get("reason") or "refused")
        if "session" in reason.lower():
            if attempt == 0:
                continue
            raise AbError("signin", reason)
        raise AbError("rejected", reason)
    raise AbError("signin", "The session was refused")


def try_sync(fn):
    """Runs fn() against the site. A failure never reaches the listener: the device's copy is used and the site is left alone for a while. (ok, result, error)"""
    if time.time() < state["offline_until"]:
        return False, None, None
    try:
        return True, fn(), None
    except AbError as e:
        state["offline_until"] = time.time() + (REJECT_BACKOFF_S if e.kind == "rejected" else SYNC_BACKOFF_S)
        log("site sync:", e.kind, e)
        return False, None, e


def queue(op):
    """Adds a change to the queue of what the site has not got yet and starts sending. A newer place of a book / newer settings replace the older ones."""
    with plock:
        p = load_json("pending.json", [])
        if op["op"] == "setPosition":
            p = [x for x in p if not (x["op"] == "setPosition" and x["book"] == op["book"])]
        elif op["op"] == "setSettings":
            old = [x for x in p if x["op"] == "setSettings"]
            if old:
                op = {"op": "setSettings", "settings": dict(old[-1]["settings"], **op["settings"])}
            p = [x for x in p if x["op"] != "setSettings"]
        p.append(op)
        save_json("pending.json", p[-MAX_PENDING:])
    kick()


def kick():
    threading.Thread(target=flush, daemon=True).start()


def flush():
    if not flush_guard.acquire(blocking=False):
        return
    try:
        while True:
            with plock:
                p = load_json("pending.json", [])
            if not p:
                return
            op = p[0]
            ok, _, err = try_sync(lambda: sw_call(op["op"], **{k: v for k, v in op.items() if k not in ("op", "tries")}))
            with plock:
                p = load_json("pending.json", [])
                if ok:
                    p = [x for x in p if x != op]
                elif err is not None and err.kind == "rejected":
                    op2 = dict(op, tries=op.get("tries", 0) + 1)
                    p = [x for x in p if x != op] if op2["tries"] >= MAX_TRIES else [op2 if x == op else x for x in p]
                save_json("pending.json", p)
            if not ok:
                return
    finally:
        flush_guard.release()


def apply_pending(items, sk):
    """items {id: bookmark} with the queued adds and removes of the book laid over it."""
    with plock:
        ops = [x for x in load_json("pending.json", []) if x.get("book") == sk]
    for op in ops:
        if op["op"] == "addBookmark":
            items[op["bookmark"]["id"]] = op["bookmark"]
        elif op["op"] == "removeBookmark":
            items.pop(op["id"], None)
    return items


def bookmarks_of(scope, bid, files):
    """The bookmarks of a book: the site's (when it answers) or the device's copy, with what is not sent yet laid over. Each gets its file index in THIS book by the file's name."""
    sk = server_key(scope, bid)
    ok, res, _ = try_sync(lambda: sw_call("getBook", book=sk))
    with lock:
        d = load_json("marks.json", {})
        if ok and isinstance(res.get("bookmarks"), list):
            d[sk] = {m["id"]: m for m in res["bookmarks"] if isinstance(m, dict) and m.get("id")}
            save_json("marks.json", d)
        items = apply_pending(dict(d.get(sk) or {}), sk)
    out = []
    for m in items.values():
        m = dict(m)
        if files and m.get("file") in files:
            m["index"] = files.index(m["file"])
        out.append(m)
    return sorted(out, key=lambda m: (m.get("index", 0), m.get("time", 0)))


def settings_now():
    sp = load_json("settings.json", {}).get("speed")
    return {"speed": sp if isinstance(sp, (int, float)) and SPEED_MIN <= sp <= SPEED_MAX else 1.0}


def clamp_speed(v):
    return max(SPEED_MIN, min(SPEED_MAX, round(float(v), 2)))


# ---- the catalogs -------------------------------------------------------------------------------------------------------------------------

def catalog_path(scope):
    return BASE + ("audiobooks.json" if scope == "public" else f"audiobooks_{user_hash()}.json")


def fetch_list(scope):
    """The books of a scope, from the site (kept for LIST_TTL_S and on disk); stale: the site did not answer and the copy of the device is shown."""
    with lock:
        got = state["lists"].get(scope)
        if got and time.time() - got[0] < LIST_TTL_S:
            return got[1], False
    try:
        books = json.loads(get_file(catalog_path(scope)))
        if not isinstance(books, list):
            raise AbError("service", "the catalog is not a list")
        books = [b for b in books if isinstance(b, dict) and b.get("directory") and isinstance(b.get("files"), list) and b["files"]
                 and b.get("status", "complete") in ("complete", "ready", "published")]
        save_json(f"list-{scope}.json", books)
        stale = False
    except AbError as e:
        if e.kind == "notfound" and scope == "mine":     # an account that has not added a book has no list file yet
            books, stale = [], False
            save_json(f"list-{scope}.json", books)
            with lock:
                state["lists"][scope] = (time.time(), books)
            return books, False
        books = load_json(f"list-{scope}.json", None)
        if books is None:
            raise
        log("catalog", scope, "from the copy:", e.kind, e)
        stale = True
    except ValueError:
        raise AbError("service", "the catalog is not JSON")
    with lock:
        state["lists"][scope] = (time.time() if not stale else time.time() - LIST_TTL_S + 30, books)    # a stale copy is asked again soon
    return books, stale


def find_book(scope, bid):
    books, _ = fetch_list(scope)
    for b in books:
        if str(b.get("directory")) == str(bid):
            return b
    raise AbError("notfound", "no such audiobook")


def cover_of(b):
    c = str(b.get("coverUrl") or "")
    return c if c.startswith(BASE) else ""


def brief(scope, b, listen):
    r = listen.get(f"{scope}:{b['directory']}")
    return {"id": b["directory"], "scope": scope, "title": b.get("title") or "?", "author": b.get("author") or "", "narrator": b.get("narrator") or "",
            "language": b.get("language") or "", "genre": b.get("genre") or "", "description": (b.get("description") or "")[:300], "cover": cover_of(b), "files": len(b["files"]),
            "listening": ({"index": r.get("index", 0), "time": r.get("time", 0)} if r else None)}


def op_catalog(q):
    scope = valid_scope(q.get("scope", ["public"])[0])
    if not scope:
        return {"error": "scope is public or mine"}, 400
    try:
        books, stale = fetch_list(scope)
    except AbError as e:
        if scope == "mine" and e.kind in ("signin", "config"):
            return {"scope": scope, "signedIn": False, "books": [], "languages": [], "genres": [], "stale": False, "reason": str(e)}
        raise
    listen = listen_state()
    words = [w.lower() for w in q.get("q", [""])[0].split() if w]
    lang, genre = q.get("language", [""])[0], q.get("genre", [""])[0]
    out = [b for b in books if (not lang or b.get("language") == lang) and (not genre or b.get("genre") == genre)
           and all(w in f"{b.get('title', '')} {b.get('author', '')} {b.get('narrator', '')}".lower() for w in words)]
    by = q.get("sort", ["title"])[0]
    out.sort(key=lambda b: str(b.get("author" if by == "author" else "title") or "").lower())
    return {"scope": scope, "signedIn": True, "books": [brief(scope, b, listen) for b in out],
            "languages": sorted({b.get("language") for b in books if b.get("language")}), "genres": sorted({b.get("genre") for b in books if b.get("genre")}), "stale": stale}


def server_key(scope, bid):
    return f"{scope}:{bid}"


def place_in(files, p):
    """The index of the file a place points to in this book: by the file's name (the book may have gained files), else the hint."""
    if p.get("file") in files:
        return files.index(p["file"])
    return max(0, min(len(files) - 1, int(p.get("index") or 0)))


def pull_listening():
    """The places of the books listened to on other devices (and on the web): a place of the site newer than the device's own becomes the device's place."""
    ok, res, _ = try_sync(lambda: sw_call("listListening", limit=50))
    if not ok:
        return
    with lock:
        d = listen_state()
        changed = False
        for sp in res.get("books", []):
            scope, _, bid = str(sp.get("book", "")).partition(":")
            if not valid_scope(scope) or not bid:
                continue
            old = d.get(f"{scope}:{bid}")
            if old and old.get("ms", int(old.get("at", 0)) * 1000) >= sp.get("at", 0):
                continue
            d[f"{scope}:{bid}"] = {"scope": scope, "id": bid, "title": sp.get("title") or "?", "author": sp.get("author") or "", "index": sp.get("index", 0), "file": sp.get("file", ""),
                                   "time": sp.get("time", 0), "speed": sp.get("speed", 1.0), "at": int(sp.get("at", 0) / 1000), "ms": sp.get("at", 0)}
            changed = True
        if changed:
            save_json("listen.json", d)


def op_recent(q):
    pull_listening()
    items = sorted(listen_state().values(), key=lambda r: -r.get("at", 0))[:20]
    return {"books": [{"id": r["id"], "scope": r["scope"], "title": r.get("title") or "?", "author": r.get("author") or "", "index": r.get("index", 0), "time": r.get("time", 0)} for r in items]}


def op_book(q):
    scope, bid = valid_scope(q.get("scope", [""])[0]), q.get("id", [""])[0]
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    b = find_book(scope, bid)
    files = [str(f) for f in b["files"]]
    sk = server_key(scope, bid)
    with lock:
        mine = listen_state().get(f"{scope}:{bid}")
    best = mine
    ok, res, _ = try_sync(lambda: sw_call("getBook", book=sk))         # what other devices and the web did
    if ok and res.get("position"):
        sp = res["position"]
        if not mine or mine.get("ms", int(mine.get("at", 0)) * 1000) < sp.get("at", 0):
            best = {"scope": scope, "id": bid, "title": sp.get("title") or b.get("title") or "?", "author": sp.get("author") or b.get("author") or "", "index": sp.get("index", 0),
                    "file": sp.get("file", ""), "time": sp.get("time", 0), "speed": sp.get("speed", 1.0), "at": int(sp.get("at", 0) / 1000), "ms": sp.get("at", 0)}
            with lock:
                d = listen_state()
                d[f"{scope}:{bid}"] = best
                save_json("listen.json", d)
    listening = None
    if best:
        listening = {"index": place_in(files, best), "time": best.get("time", 0), "speed": best.get("speed", 1.0), "file": best.get("file", ""), "at": best.get("at", 0)}
    return {"id": bid, "scope": scope, "title": b.get("title") or "?", "author": b.get("author") or "", "narrator": b.get("narrator") or "", "description": b.get("description") or "",
            "cover": cover_of(b), "base": BASE + urllib.parse.quote(str(b["directory"])) + "/", "site": SW_URL, "files": files, "listening": listening, "bookmarks": bookmarks_of(scope, bid, files), **settings_now()}


def op_progress(b):
    scope, bid = valid_scope(b.get("scope")), str(b.get("id") or "")
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    try:
        index, t = int(b.get("index", 0)), float(b.get("time", 0))
        speed = clamp_speed(b["speed"]) if b.get("speed") is not None else settings_now()["speed"]
    except (TypeError, ValueError):
        return {"error": "index, time and speed are numbers"}, 400
    try:
        meta = find_book(scope, bid)
    except AbError:
        meta = None
    files = [str(f) for f in meta["files"]] if meta else []
    index = max(0, min(len(files) - 1, index)) if files else max(0, index)
    now = int(time.time() * 1000)
    with lock:
        d = listen_state()
        old = d.get(f"{scope}:{bid}") or {}
        rec = {"scope": scope, "id": bid, "title": (meta or {}).get("title") or old.get("title") or "?", "author": (meta or {}).get("author") or old.get("author") or "",
               "index": index, "file": files[index] if files else old.get("file", ""), "time": max(0.0, round(t, 1)), "speed": speed, "at": now // 1000, "ms": now}
        d[f"{scope}:{bid}"] = rec
        save_json("listen.json", d)
    queue({"op": "setPosition", "book": server_key(scope, bid),
           "position": {"index": rec["index"], "file": rec["file"], "time": rec["time"], "speed": speed, "title": rec["title"], "author": rec["author"], "device": DEVICE, "at": now}})
    return {"ok": True}


def op_bookmarks(q):
    scope, bid = valid_scope(q.get("scope", [""])[0]), q.get("id", [""])[0]
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    return {"bookmarks": bookmarks_of(scope, bid, [str(f) for f in find_book(scope, bid)["files"]])}


def op_bookmark(b):
    scope, bid = valid_scope(b.get("scope")), str(b.get("id") or "")
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    files = [str(f) for f in find_book(scope, bid)["files"]]
    try:
        index, t = int(b.get("index", 0)), max(0.0, round(float(b.get("time", 0)), 1))
    except (TypeError, ValueError):
        return {"error": "index and time are numbers"}, 400
    if not 0 <= index < len(files):
        return {"error": "no such file"}, 400
    sk = server_key(scope, bid)
    have = bookmarks_of(scope, bid, files)
    if len(have) >= MAX_BOOKMARKS:
        return {"error": f"A book holds at most {MAX_BOOKMARKS} bookmarks"}, 409
    if any(m["index"] == index and abs(m["time"] - t) < 3 for m in have):
        return {"error": "this place already has a bookmark"}, 409
    mark = {"id": "bm-" + secrets.token_hex(6), "index": index, "file": files[index], "time": t, "label": str(b.get("label") or "")[:120], "device": DEVICE, "at": int(time.time() * 1000)}
    with lock:
        d = load_json("marks.json", {})
        d.setdefault(sk, {})[mark["id"]] = mark
        save_json("marks.json", d)
    queue({"op": "addBookmark", "book": sk, "bookmark": mark})
    return {"bookmark": mark, "bookmarks": bookmarks_of(scope, bid, files)}


def op_unbookmark(b):
    scope, bid, mid = valid_scope(b.get("scope")), str(b.get("id") or ""), str(b.get("bookmark_id") or "")
    if not scope or not bid or not mid:
        return {"error": "scope, id and bookmark_id"}, 400
    sk = server_key(scope, bid)
    with lock:
        d = load_json("marks.json", {})
        d.get(sk, {}).pop(mid, None)
        save_json("marks.json", d)
    queue({"op": "removeBookmark", "book": sk, "id": mid})
    return {"bookmarks": bookmarks_of(scope, bid, [str(f) for f in find_book(scope, bid)["files"]])}


def op_settings(_):
    ok, res, _err = try_sync(lambda: sw_call("getSettings"))
    if ok and isinstance(res.get("settings"), dict) and "speed" in res["settings"] and not any(x["op"] == "setSettings" for x in load_json("pending.json", [])):
        with lock:
            save_json("settings.json", dict(load_json("settings.json", {}), speed=clamp_speed(res["settings"]["speed"])))
    return settings_now()


def op_set_settings(b):
    if "speed" not in b:
        return settings_now()
    try:
        sp = clamp_speed(b["speed"])
    except (TypeError, ValueError):
        return {"error": "speed is a number from 0.5 to 3"}, 400
    with lock:
        save_json("settings.json", dict(load_json("settings.json", {}), speed=sp))
    queue({"op": "setSettings", "settings": {"speed": sp}})
    return settings_now()


def op_status(_):
    return {"state": "idle", "books": len(listen_state())}


KIND_CODE = {"notfound": 404, "signin": 401, "config": 503, "network": 502, "service": 502, "rejected": 502}


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
        try:
            out = fn(arg)
        except AbError as e:
            return self.reply({"error": str(e), "kind": e.kind}, KIND_CODE.get(e.kind, 502))
        except Exception as e:  # noqa: BLE001
            log("crashed:", repr(e))
            return self.reply({"error": "internal error", "kind": "service"}, 500)
        if isinstance(out, tuple):
            return self.reply(*out)
        self.reply(out)

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.route({"catalog": op_catalog, "recent": op_recent, "book": op_book, "status": op_status, "bookmarks": op_bookmarks, "settings": op_settings}, q)

    def do_POST(self):
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return self.reply({"error": "the body is a JSON object"}, 400)
        self.route({"progress": op_progress, "bookmark": op_bookmark, "unbookmark": op_unbookmark, "settings": op_set_settings}, b)


def flusher():
    while True:
        time.sleep(FLUSH_EVERY_S)
        if load_json("pending.json", []):
            flush()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("INTELLICAT_SERVICE_PORT", "48806"))
    os.makedirs(DATA, exist_ok=True)
    log("abooksd on", port)
    kick()                                  # what an earlier run could not send
    threading.Thread(target=flusher, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
