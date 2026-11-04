#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""booksd: the service of the app `books` (the FB2 books of squirrelwisdom.com/portal/books.html: the public catalog and the owner's own books).

    booksd.py <port>

The catalogs are plain files of the site (contents/Books/books_public.json, contents/Books/books_<hash>.json, the FB2 files beside them), read without a session; the
owner's own books need the hash of the account's login (the first 16 hex characters of its SHA-256, as books.js makes it), which the service gets from the device's
session (permission account.session; the command user:getSessionUser). An FB2 book is downloaded when it is opened, parsed here into chapters of plain blocks (so the
page only paginates), and kept on the device (INTELLICAT_DATA/books/); the list of books, the books read and the place in each stay too: the app works offline.
The reading place is kept here (chapter and paragraph, so a change of the font size does not lose it); the site keeps none.

The page and Caroline's MCP server reach it through the shell (/api/app/books/<path>):

    GET  catalog?scope=public|mine&q=&language=&genre=&sort=title|author   {scope, signedIn, books: [{id, scope, title, author, language, genre, description, downloaded, read}], languages, genres, stale}
    GET  recent                                                           {books: [...]}  the books being read, the last first
    GET  book?scope=&id=        {id, scope, title, author, annotation, chapters: [{title, blocks}], read}   opens the book (downloads and parses it once)
    GET  text?scope=&id=&chapter=N   {chapter, chapters, title, blocks: [{t, x}]}   t: p paragraph, h heading, v verse line, q quotation, e empty line
    GET  find?scope=&id=&q=     {matches: [{chapter, block, text}]}
    POST progress               {scope, id, chapter, block, pct}
    GET  status                 {state, cache_mb, books}
    GET  bookmarks?scope=&id=   {bookmarks: [{id, chapter, block, pct, text, chapterTitle, label, at, device}]}   in reading order
    POST bookmark               {scope, id, chapter, block, label?}   adds a bookmark at that place -> {bookmark, bookmarks}
    POST unbookmark             {scope, id, bookmark_id}              -> {bookmarks}
    GET  settings               {fontScale, theme}                    font scale in percent (100 = the base size), theme dark|light
    POST settings               {fontScale?, theme?}                  -> the settings now in force

Bookmarks, the reading place and the settings are kept on the site too (the plugin `Books`, apps/Books in the reforce repo, per account), so the web reader and every
device read a book the same way. The device's copy is the working one: a change is saved here at once, queued, and sent when the site answers (a poor or no link only
delays it); what the site holds is pulled when a book is opened. A place or a bookmark is an anchor that does not depend on the FB2 parser of the client: chapter and
block (a hint), the start of the paragraph's text (the real anchor) and the percent of the book (the last resort); see locate(). The book of the site is
"<scope>:<file name>", the same on every client.

The key of the site's pages (it is in every page of squirrelwisdom.com, not a secret of the account) is $INTELLICAT_BOOKS_KEY or "books_key" in ~/.intellicat/keys.json
($INTELLICAT_KEYS); it is not in this repository. The address of the site is $INTELLICAT_SW_URL (the shell's setting; a test points it at a stand-in).
"""

import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SW_URL = os.environ.get("INTELLICAT_SW_URL", "https://www.squirrelwisdom.com/")
KEYS_PATH = os.environ.get("INTELLICAT_KEYS", os.path.expanduser("~/.intellicat/keys.json"))
SHELL = f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}"
SID = os.environ.get("INTELLICAT_SERVICE_ID", "booksd")
DATA = os.path.join(os.environ.get("INTELLICAT_DATA", os.path.expanduser("~/.intellicat/data")), "books")
CACHE_MB = float(os.environ.get("BOOKS_CACHE_MB") or 300)      # parsed books kept on the device; the least recently opened go first
LIST_TTL_S = float(os.environ.get("BOOKS_LIST_TTL_S") or 300)  # a catalog is asked again after this
MAX_BOOK_BYTES = 40 * 1024 * 1024
MAX_BOOKMARKS = 300                                           # per book, as on the site
DEVICE = "beroun"
FB2_BLOCKS = ("p", "subtitle", "empty-line", "poem", "cite", "epigraph", "text-author")

lock = threading.RLock()
state = {"hash": None, "lists": {}}      # hash: the account's 16 hex; lists: {scope: (fetched at, [books])}
_session = {"value": None}


class BooksError(Exception):
    """kind: network | signin | config | service | notfound | format"""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- the site ---------------------------------------------------------------------------------------------------------------------------

def books_key():
    k = os.environ.get("INTELLICAT_BOOKS_KEY")
    if not k:
        try:
            k = json.load(open(KEYS_PATH, encoding="utf-8-sig")).get("books_key")
        except (OSError, ValueError):
            k = None
    if not k:
        raise BooksError("config", f"no books_key configured ({KEYS_PATH})")
    return k


RETRIES = int(os.environ.get("BOOKS_RETRIES") or 3)               # tries of one request on a poor link
RETRY_PAUSE_S = float(os.environ.get("BOOKS_RETRY_PAUSE_S") or 1.5)   # the pause before the second try; doubled for each next one


def with_retries(fn, what, tries=None):
    """Runs fn(); a network failure (or a 5xx of the site) is tried again after a growing pause, any other error at once."""
    tries = tries or RETRIES
    for attempt in range(tries):
        try:
            return fn()
        except BooksError as e:
            if e.kind != "network" or attempt == tries - 1:
                raise
            pause = RETRY_PAUSE_S * 2 ** attempt
            log(what, "failed:", e, f"- again in {pause:g} s ({attempt + 2}/{RETRIES})")
            time.sleep(pause)


def get_file(path, limit=MAX_BOOK_BYTES):
    """A file of the site, by its path from the root (contents/Books/...); a poor link is tried again."""
    def once():
        req = urllib.request.Request(urllib.parse.urljoin(SW_URL, path), headers={"Cache-Control": "no-cache"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise BooksError("notfound" if e.code == 404 else "network" if e.code >= 500 else "service", f"HTTP {e.code} for {path}")
        except (urllib.error.URLError, OSError) as e:      # includes a timeout and a connection cut in the middle of the file
            raise BooksError("network", str(getattr(e, "reason", e)))
        if len(data) > limit:
            raise BooksError("service", f"{path} is too big")
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
        raise BooksError("signin" if kind == "auth" else "network" if kind == "network" else "service", body.get("error") or "No account")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise BooksError("service", f"The shell gave no session ({e})")
    return _session["value"]


def user_hash():
    """The 16 hex characters of the SHA-256 of the account's login: the owner's folder of the site. Kept on the device once known."""
    with lock:
        if state["hash"]:
            return state["hash"]
    cached = load_json("account.json", {}).get("hash")
    if cached:
        with lock:
            state["hash"] = cached
        return cached
    key = books_key()
    for attempt in (0, 1):
        body = {"command": "user:getSessionUser", "session": session(fresh=bool(attempt)), ".msgid": int(time.time() * 1000), "key": key}
        req = urllib.request.Request(SW_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})

        def ask():
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    return json.loads(r.read())
            except (urllib.error.URLError, OSError) as e:
                raise BooksError("network", str(getattr(e, "reason", e)))
            except ValueError:
                raise BooksError("service", "The site answered with something else than JSON")
        out = with_retries(ask, "user:getSessionUser")
        if out.get(".status") == "ok" and out.get("user"):
            h = hashlib.sha256(str(out["user"]).encode()).hexdigest()[:16]
            save_json("account.json", {"hash": h})
            with lock:
                state["hash"] = h
            return h
        if attempt == 0:
            continue
    raise BooksError("signin", "The session was refused")


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


def book_key(scope, bid):
    return hashlib.sha1(f"{scope}:{bid}".encode()).hexdigest()[:20]


def valid_scope(scope):
    return scope if scope in ("public", "mine") else None


def read_state():
    return load_json("read.json", {})        # {scope:id: {scope, id, title, author, chapter, block, pct, at}}


def write_state(d):
    save_json("read.json", d)


# ---- the site's copy of the reading state (bookmarks, place, settings) ----------------------------------------------------------------------

SYNC_TIMEOUT_S = float(os.environ.get("BOOKS_SYNC_TIMEOUT_S") or 10)
SYNC_BACKOFF_S = float(os.environ.get("BOOKS_SYNC_BACKOFF_S") or 60)       # after a failed call the site is left alone for this long
REJECT_BACKOFF_S = float(os.environ.get("BOOKS_REJECT_BACKOFF_S") or 300)
MAX_TRIES = 20                                                              # a queued change the site keeps refusing is dropped after this many tries
MAX_PENDING = 500
FONT_MIN, FONT_MAX = 50, 300
state.update(offline_until=0.0)
plock = threading.Lock()          # pending.json
flush_guard = threading.Lock()    # one sender at a time


def sw_call(action, **fields):
    """One action of the site's plugin Books for the account (a v2 plugin:call); the plugin's result dict. kind: network | signin | config | rejected | service"""
    key = books_key()
    for attempt in (0, 1):
        body = dict(fields, command="plugin:call", plugin="Books", query=action, action=action, session=session(fresh=bool(attempt)), key=key)
        body[".msgid"] = int(time.time() * 1000)
        req = urllib.request.Request(SW_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})

        def ask():
            try:
                with urllib.request.urlopen(req, timeout=SYNC_TIMEOUT_S) as r:
                    return json.loads(r.read())
            except (urllib.error.URLError, OSError) as e:
                raise BooksError("network", str(getattr(e, "reason", e)))
            except ValueError:
                raise BooksError("service", "The site answered with something else than JSON")
        out = with_retries(ask, "Books." + action, tries=1)
        if out.get(".status") == "ok" and isinstance(out.get("result"), dict):
            return out["result"]
        reason = str(out.get(".reason") or out.get("reason") or "refused")
        if "session" in reason.lower():
            if attempt == 0:
                continue
            raise BooksError("signin", reason)
        raise BooksError("rejected", reason)
    raise BooksError("signin", "The session was refused")


def try_sync(fn):
    """Runs fn() against the site. A failure never reaches the reader: the device's copy is used and the site is left alone for a while. (ok, result, error)"""
    if time.time() < state["offline_until"]:
        return False, None, None
    try:
        return True, fn(), None
    except BooksError as e:
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


def pending_for(sk):
    with plock:
        return [x for x in load_json("pending.json", []) if x.get("book") == sk]


def apply_pending(items, sk):
    """items {id: bookmark} with the queued adds and removes of the book laid over it."""
    for op in pending_for(sk):
        if op["op"] == "addBookmark":
            items[op["bookmark"]["id"]] = op["bookmark"]
        elif op["op"] == "removeBookmark":
            items.pop(op["id"], None)
    return items


def server_key(scope, bid):
    """The book's key on the site: <scope>:<file name>. None while the file name is not known (the book was never listed)."""
    b = (_open["book"] if _open["key"] == book_key(scope, bid) else None) or load_json(os.path.join("books", book_key(scope, bid) + ".json"), None)
    fn = (b or {}).get("filename")
    if not fn:
        got = state["lists"].get(scope)
        for x in (got[1] if got else load_json(f"list-{scope}.json", [])):
            if str(x.get("id")) == str(bid):
                fn = x.get("filename")
                break
    return f"{scope}:{fn}" if fn else None


def settings_now():
    s = load_json("settings.json", {})
    scale = s.get("fontScale")
    return {"fontScale": scale if isinstance(scale, int) and FONT_MIN <= scale <= FONT_MAX else 100, "theme": "light" if s.get("theme") == "light" else "dark"}


# ---- anchors: a place that does not depend on the parser of the client --------------------------------------------------------------

def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def block_pos(b, ci, bi):
    total = sum(len(c["blocks"]) for c in b["chapters"])
    return sum(len(c["blocks"]) for c in b["chapters"][:ci]) + bi, max(1, total)


def anchor_of(b, ch, bl):
    """The anchor of a place: the first block with text at or after (ch, bl)."""
    chs = b["chapters"]
    ch = max(0, min(len(chs) - 1, ch))
    blocks = chs[ch]["blocks"]
    bl = max(0, min(len(blocks) - 1, bl))
    for i in range(bl, len(blocks)):
        if blocks[i]["x"]:
            bl = i
            break
    n, total = block_pos(b, ch, bl)
    return {"chapter": ch, "block": bl, "pct": round(n / total * 100, 2), "text": blocks[bl]["x"][:120], "chapterTitle": chs[ch]["title"][:200]}


def locate(b, a):
    """(chapter, block) of an anchor in this parse of the book. 1. the hint, if the paragraph there starts as the anchor's text; 2. the paragraph with that start, the
    nearest to the anchor's percent (the client that made it may have counted blocks differently); 3. the percent of the book."""
    chs = b["chapters"]
    ch, bl, key, pct = int(a.get("chapter") or 0), int(a.get("block") or 0), norm(a.get("text"))[:24], float(a.get("pct") or 0)
    if key:
        if 0 <= ch < len(chs) and 0 <= bl < len(chs[ch]["blocks"]) and norm(chs[ch]["blocks"][bl]["x"])[:24] == key:
            return ch, bl
        idx = b.get("_idx")
        if idx is None:
            idx = {}
            total = sum(len(c["blocks"]) for c in chs) or 1
            n = 0
            for ci, c in enumerate(chs):
                for bi, blk in enumerate(c["blocks"]):
                    if blk["x"]:
                        idx.setdefault(norm(blk["x"])[:24], []).append((ci, bi, n / total * 100))
                    n += 1
            b["_idx"] = idx
        near = idx.get(key)
        if near:
            best = min(near, key=lambda c: abs(c[2] - pct))
            return best[0], best[1]
    total = sum(len(c["blocks"]) for c in chs)
    left = int(pct / 100 * total)
    for ci, c in enumerate(chs):
        if left < len(c["blocks"]):
            return ci, left
        left -= len(c["blocks"])
    return len(chs) - 1, max(0, len(chs[-1]["blocks"]) - 1)


# ---- the catalogs -------------------------------------------------------------------------------------------------------------------------

def catalog_paths(scope):
    if scope == "public":
        return "contents/Books/books_public.json", "contents/Books/public/"
    h = user_hash()
    return f"contents/Books/books_{h}.json", f"contents/Books/{h}/"


def fetch_list(scope):
    """The books of a scope, from the site (kept for LIST_TTL_S and on disk); stale: the site did not answer and the copy of the device is shown."""
    with lock:
        got = state["lists"].get(scope)
        if got and time.time() - got[0] < LIST_TTL_S:
            return got[1], False
    try:
        list_path, _ = catalog_paths(scope)
        books = json.loads(get_file(list_path, 20 * 1024 * 1024))
        if not isinstance(books, list):
            raise BooksError("service", "the catalog is not a list")
        books = [b for b in books if isinstance(b, dict) and b.get("id") and b.get("filename") and b.get("status", "complete") == "complete"]
        save_json(f"list-{scope}.json", books)
        stale = False
    except BooksError as e:
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
        raise BooksError("service", "the catalog is not JSON")
    with lock:
        state["lists"][scope] = (time.time() if not stale else time.time() - LIST_TTL_S + 30, books)    # a stale copy is asked again soon
    return books, stale


def find_book(scope, bid):
    books, _ = fetch_list(scope)
    for b in books:
        if str(b.get("id")) == str(bid):
            return b
    raise BooksError("notfound", "no such book")


def brief(scope, b, read):
    r = read.get(f"{scope}:{b['id']}")
    return {"id": b["id"], "scope": scope, "title": b.get("title") or "?", "author": b.get("author") or "", "language": b.get("language") or "", "genre": b.get("genre") or "",
            "description": (b.get("description") or "")[:300], "downloaded": os.path.isfile(path_of("books", book_key(scope, b["id"]) + ".json")),
            "read": ({"chapter": r.get("chapter", 0), "pct": r.get("pct", 0)} if r else None)}


def op_catalog(q):
    scope = valid_scope(q.get("scope", ["public"])[0])
    if not scope:
        return {"error": "scope is public or mine"}, 400
    try:
        books, stale = fetch_list(scope)
    except BooksError as e:
        if scope == "mine" and e.kind in ("signin", "config"):
            return {"scope": scope, "signedIn": False, "books": [], "languages": [], "genres": [], "stale": False, "reason": str(e)}
        raise
    read = read_state()
    words = [w.lower() for w in q.get("q", [""])[0].split() if w]
    lang, genre = q.get("language", [""])[0], q.get("genre", [""])[0]
    out = [b for b in books if (not lang or b.get("language") == lang) and (not genre or b.get("genre") == genre)
           and all(w in f"{b.get('title', '')} {b.get('author', '')}".lower() for w in words)]
    by = q.get("sort", ["title"])[0]
    out.sort(key=lambda b: str(b.get("author" if by == "author" else "title") or "").lower())
    return {"scope": scope, "signedIn": True, "books": [brief(scope, b, read) for b in out],
            "languages": sorted({b.get("language") for b in books if b.get("language")}), "genres": sorted({b.get("genre") for b in books if b.get("genre")}), "stale": stale}


def pull_reading():
    """The places of the books read on other devices (and on the web): a place of the site newer than the device's own becomes the device's place."""
    ok, res, _ = try_sync(lambda: sw_call("listReading", limit=50))
    if not ok:
        return
    with lock:
        d = read_state()
        changed = False
        for sp in res.get("books", []):
            scope, _, fn = str(sp.get("book", "")).partition(":")
            if not valid_scope(scope):
                continue
            got = state["lists"].get(scope)
            meta = next((x for x in (got[1] if got else load_json(f"list-{scope}.json", [])) if x.get("filename") == fn), None)
            if not meta:
                continue                       # a book of the site that this device's catalog does not list (yet)
            k = f"{scope}:{meta['id']}"
            old = d.get(k)
            if old and old.get("ms", int(old.get("at", 0)) * 1000) >= sp.get("at", 0):
                continue
            d[k] = {"scope": scope, "id": str(meta["id"]), "title": sp.get("title") or meta.get("title") or "?", "author": sp.get("author") or meta.get("author") or "",
                    "chapter": sp.get("chapter", 0), "block": sp.get("block", 0), "pct": sp.get("pct", 0), "text": sp.get("text", ""), "at": int(sp.get("at", 0) / 1000), "ms": sp.get("at", 0)}
            changed = True
        if changed:
            write_state(d)


def op_recent(q):
    pull_reading()
    read = read_state()
    items = sorted(read.values(), key=lambda r: -r.get("at", 0))[:20]
    return {"books": [{"id": r["id"], "scope": r["scope"], "title": r.get("title") or "?", "author": r.get("author") or "", "chapter": r.get("chapter", 0), "pct": r.get("pct", 0),
                       "downloaded": os.path.isfile(path_of("books", book_key(r["scope"], r["id"]) + ".json"))} for r in items]}


# ---- FB2 ------------------------------------------------------------------------------------------------------------------------------

def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def flat(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def parse_xml(raw):
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        pass
    for enc in ("utf-8", "windows-1251"):              # a file whose declared encoding is wrong (the web reader tries the same two)
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            continue
    raise BooksError("format", "the file is not a readable FB2 book")


def blocks_of(el, out):
    """The blocks of a section's own content (not of its sub-sections)."""
    for c in el:
        n = local(c.tag)
        if n == "p":
            t = flat(c)
            if t:
                out.append({"t": "p", "x": t})
        elif n == "subtitle":
            t = flat(c)
            if t:
                out.append({"t": "h", "x": t})
        elif n == "empty-line":
            out.append({"t": "e", "x": ""})
        elif n in ("cite", "epigraph"):
            for p in c:
                t = flat(p)
                if t:
                    out.append({"t": "q", "x": t})
        elif n == "poem":
            for st in c.iter():
                if local(st.tag) == "v":
                    t = flat(st)
                    if t:
                        out.append({"t": "v", "x": t})
            out.append({"t": "e", "x": ""})


def parse_fb2(raw):
    root = parse_xml(raw)
    info = None
    for d in root:
        if local(d.tag) == "description":
            info = child(d, "title-info")
    title = flat(child(info, "book-title")) if info is not None and child(info, "book-title") is not None else ""
    author = ""
    if info is not None:
        a = child(info, "author")
        if a is not None:
            author = " ".join(flat(child(a, k)) for k in ("first-name", "middle-name", "last-name") if child(a, k) is not None).strip()
    annotation = ""
    if info is not None and child(info, "annotation") is not None:
        annotation = " ".join(flat(p) for p in child(info, "annotation") if local(p.tag) == "p")[:1500]
    chapters = []
    pending = []                                       # titles of the sections that hold only sub-sections (a part's title goes before its first chapter)

    def title_of(sec):
        t = child(sec, "title")
        return flat(t) if t is not None else ""

    def walk(sec):
        nonlocal pending
        subs = [c for c in sec if local(c.tag) == "section"]
        own = []
        t = title_of(sec)
        blocks_of(sec, own)
        if own or not subs:
            name = " · ".join([x for x in pending + [t] if x]) or f"{len(chapters) + 1}"
            pending = []
            chapters.append({"title": name, "blocks": own})
        elif t:
            pending.append(t)
        for s in subs:
            walk(s)

    for body in root:
        if local(body.tag) != "body" or body.get("name") in ("notes", "comments"):
            continue
        intro = []
        blocks_of(body, intro)
        if intro:
            chapters.append({"title": title_of(body) or title or "…", "blocks": intro})
        for sec in body:
            if local(sec.tag) == "section":
                walk(sec)
    chapters = [c for c in chapters if c["blocks"]]
    if not chapters:
        raise BooksError("format", "the book has no text")
    return {"title": title or "?", "author": author, "annotation": annotation, "chapters": chapters}


# ---- opening a book ---------------------------------------------------------------------------------------------------------------------

_open = {"key": None, "book": None}


def evict():
    """Keeps the parsed books under CACHE_MB: the least recently opened go first."""
    d = path_of("books")
    try:
        files = [(os.path.getmtime(os.path.join(d, f)), os.path.getsize(os.path.join(d, f)), f) for f in os.listdir(d) if f.endswith(".json")]
    except OSError:
        return
    total = sum(s for _, s, _ in files)
    for _, s, f in sorted(files):
        if total <= CACHE_MB * 1024 * 1024 or len(files) <= 1:
            break
        os.remove(os.path.join(d, f))
        total -= s


def open_book(scope, bid):
    k = book_key(scope, bid)
    with lock:
        if _open["key"] == k:
            return _open["book"]
    p = path_of("books", k + ".json")
    book = None
    try:
        with open(p, encoding="utf-8") as f:
            book = json.load(f)
        os.utime(p)
    except (OSError, ValueError):
        book = None
    if book is None:
        meta = find_book(scope, bid)
        _, base = catalog_paths(scope)
        book = parse_fb2(get_file(base + urllib.parse.quote(meta["filename"])))
        book["id"], book["scope"], book["filename"] = str(bid), scope, meta["filename"]
        os.makedirs(path_of("books"), exist_ok=True)
        save_json(os.path.join("books", k + ".json"), book)
        evict()
    with lock:
        _open.update(key=k, book=book)
    return book


def read_of(scope, bid):
    r = read_state().get(f"{scope}:{bid}")
    return {"chapter": r.get("chapter", 0), "block": r.get("block", 0), "pct": r.get("pct", 0)} if r else None


def pull_book(scope, bid, b):
    """What the site holds for this book: its bookmarks (the device's copy is replaced, the queued changes laid over it) and its place, if newer than the device's."""
    sk = server_key(scope, bid)
    if not sk:
        return
    ok, res, _ = try_sync(lambda: sw_call("getBook", book=sk))
    if not ok:
        return
    items = apply_pending({x["id"]: x for x in res.get("bookmarks", []) if isinstance(x, dict) and x.get("id")}, sk)
    with lock:
        marks = load_json("marks.json", {})
        marks[f"{scope}:{bid}"] = list(items.values())
        save_json("marks.json", marks)
        sp = res.get("position")
        if sp:
            d = read_state()
            old = d.get(f"{scope}:{bid}")
            if not old or old.get("ms", int(old.get("at", 0)) * 1000) < sp.get("at", 0):
                ch, bl = locate(b, sp)
                a = anchor_of(b, ch, bl)
                d[f"{scope}:{bid}"] = {"scope": scope, "id": str(bid), "title": b["title"], "author": b["author"], "chapter": a["chapter"], "block": a["block"], "pct": a["pct"],
                                       "text": a["text"], "at": int(sp["at"] / 1000), "ms": sp["at"]}
                write_state(d)


def bookmarks_of(scope, bid, b):
    """The book's bookmarks in reading order, each placed in this parse of the book."""
    out = []
    for x in load_json("marks.json", {}).get(f"{scope}:{bid}", []):
        ch, bl = locate(b, x)
        out.append({"id": x["id"], "chapter": ch, "block": bl, "pct": x.get("pct", 0), "text": x.get("text", ""), "chapterTitle": x.get("chapterTitle", ""), "label": x.get("label", ""),
                    "at": x.get("at", 0), "device": x.get("device", "")})
    out.sort(key=lambda x: (x["chapter"], x["block"], x["at"]))
    return out


def op_book(q):
    scope, bid = valid_scope(q.get("scope", [""])[0]), q.get("id", [""])[0]
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    b = open_book(scope, bid)
    pull_book(scope, bid, b)
    kick()
    return {"id": b["id"], "scope": scope, "title": b["title"], "author": b["author"], "annotation": b.get("annotation", ""),
            "chapters": [{"title": c["title"], "blocks": len(c["blocks"])} for c in b["chapters"]], "read": read_of(scope, bid), "bookmarks": bookmarks_of(scope, bid, b)}


def op_bookmarks(q):
    scope, bid = valid_scope(q.get("scope", [""])[0]), q.get("id", [""])[0]
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    return {"bookmarks": bookmarks_of(scope, bid, open_book(scope, bid))}


def op_bookmark(b):
    scope, bid = valid_scope(b.get("scope")), str(b.get("id") or "")
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    try:
        ch, bl = int(b.get("chapter", 0)), int(b.get("block", 0))
    except (TypeError, ValueError):
        return {"error": "chapter and block are numbers"}, 400
    book = open_book(scope, bid)
    mark = dict(anchor_of(book, ch, bl), id=secrets.token_urlsafe(9), label=str(b.get("label") or "")[:120], device=DEVICE, at=int(time.time() * 1000))
    with lock:
        marks = load_json("marks.json", {})
        mine = marks.setdefault(f"{scope}:{bid}", [])
        if any(x["chapter"] == mark["chapter"] and x["block"] == mark["block"] and x.get("text") == mark["text"] for x in mine):
            return {"error": "this place already has a bookmark"}, 409
        if len(mine) >= MAX_BOOKMARKS:
            return {"error": f"a book holds at most {MAX_BOOKMARKS} bookmarks"}, 409
        mine.append(mark)
        save_json("marks.json", marks)
    sk = server_key(scope, bid)
    if sk:
        queue({"op": "addBookmark", "book": sk, "bookmark": mark})
    return {"bookmark": mark, "bookmarks": bookmarks_of(scope, bid, book)}


def op_unbookmark(b):
    scope, bid, mid = valid_scope(b.get("scope")), str(b.get("id") or ""), str(b.get("bookmark_id") or "")
    if not scope or not bid or not mid:
        return {"error": "scope, id and bookmark_id"}, 400
    book = open_book(scope, bid)
    with lock:
        marks = load_json("marks.json", {})
        marks[f"{scope}:{bid}"] = [x for x in marks.get(f"{scope}:{bid}", []) if x["id"] != mid]
        save_json("marks.json", marks)
    sk = server_key(scope, bid)
    if sk:
        queue({"op": "removeBookmark", "book": sk, "id": mid})
    return {"bookmarks": bookmarks_of(scope, bid, book)}


def pending_all():
    with plock:
        return load_json("pending.json", [])


def op_settings(q):
    ok, res, _ = try_sync(lambda: sw_call("getSettings"))
    if ok and not [x for x in pending_all() if x["op"] == "setSettings"]:
        s = res.get("settings") or {}
        save_json("settings.json", dict(load_json("settings.json", {}), **{k: s[k] for k in ("fontScale", "theme") if k in s}))
    return settings_now()


def op_set_settings(b):
    ch = {}
    if "fontScale" in b:
        try:
            ch["fontScale"] = max(FONT_MIN, min(FONT_MAX, int(b["fontScale"])))
        except (TypeError, ValueError):
            return {"error": "fontScale is a number of percent"}, 400
    if "theme" in b:
        if b["theme"] not in ("dark", "light"):
            return {"error": "theme is dark or light"}, 400
        ch["theme"] = b["theme"]
    if ch:
        with lock:
            save_json("settings.json", dict(load_json("settings.json", {}), **ch))
        queue({"op": "setSettings", "settings": ch})
    return settings_now()


def op_text(q):
    scope, bid = valid_scope(q.get("scope", [""])[0]), q.get("id", [""])[0]
    try:
        n = int(q.get("chapter", ["0"])[0])
    except ValueError:
        return {"error": "chapter is a number"}, 400
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    b = open_book(scope, bid)
    if not 0 <= n < len(b["chapters"]):
        return {"error": "no such chapter"}, 404
    c = b["chapters"][n]
    return {"chapter": n, "chapters": len(b["chapters"]), "title": c["title"], "blocks": c["blocks"]}


def op_find(q):
    scope, bid, words = valid_scope(q.get("scope", [""])[0]), q.get("id", [""])[0], q.get("q", [""])[0].strip().lower()
    if not scope or not bid or not words:
        return {"error": "scope, id and q"}, 400
    b = open_book(scope, bid)
    out = []
    for ci, c in enumerate(b["chapters"]):
        for bi, blk in enumerate(c["blocks"]):
            i = blk["x"].lower().find(words)
            if i >= 0:
                out.append({"chapter": ci, "block": bi, "text": blk["x"][max(0, i - 40): i + 80]})
                if len(out) >= 50:
                    return {"matches": out}
    return {"matches": out}


def op_progress(b):
    scope, bid = valid_scope(b.get("scope")), str(b.get("id") or "")
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    try:
        ch, bl, pct = int(b.get("chapter", 0)), int(b.get("block", 0)), float(b.get("pct", 0))
    except (TypeError, ValueError):
        return {"error": "chapter and block are numbers"}, 400
    try:
        meta = open_book(scope, bid)
    except BooksError:
        meta = None
    text = anchor_of(meta, ch, bl)["text"] if meta else ""
    now = int(time.time() * 1000)
    with lock:
        d = read_state()
        old = d.get(f"{scope}:{bid}") or {}
        d[f"{scope}:{bid}"] = {"scope": scope, "id": bid, "title": (meta or {}).get("title") or old.get("title") or "?", "author": (meta or {}).get("author") or old.get("author") or "",
                               "chapter": max(0, ch), "block": max(0, bl), "pct": max(0, min(100, round(pct, 1))), "text": text, "at": now // 1000, "ms": now}
        write_state(d)
        rec = d[f"{scope}:{bid}"]
    sk = server_key(scope, bid)
    if sk:
        pos = {k: rec[k] for k in ("chapter", "block", "pct", "text", "title", "author")}
        pos.update(at=now, device=DEVICE, chapterTitle=anchor_of(meta, ch, bl)["chapterTitle"] if meta else "")
        queue({"op": "setPosition", "book": sk, "position": pos})
    return {"ok": True}


def op_status(_):
    size = 0
    try:
        size = sum(os.path.getsize(os.path.join(path_of("books"), f)) for f in os.listdir(path_of("books")))
    except OSError:
        pass
    return {"state": "idle", "cache_mb": round(size / 1048576, 1), "books": len(read_state())}


KIND_CODE = {"notfound": 404, "signin": 401, "config": 503, "network": 502, "service": 502, "format": 422}


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
        except BooksError as e:
            return self.reply({"error": str(e), "kind": e.kind}, KIND_CODE.get(e.kind, 502))
        except Exception as e:  # noqa: BLE001
            log("crashed:", repr(e))
            return self.reply({"error": "internal error", "kind": "service"}, 500)
        if isinstance(out, tuple):
            return self.reply(*out)
        self.reply(out)

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.route({"catalog": op_catalog, "recent": op_recent, "book": op_book, "text": op_text, "find": op_find, "status": op_status, "bookmarks": op_bookmarks, "settings": op_settings}, q)

    def do_POST(self):
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return self.reply({"error": "the body is a JSON object"}, 400)
        self.route({"progress": op_progress, "bookmark": op_bookmark, "unbookmark": op_unbookmark, "settings": op_set_settings}, b)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("INTELLICAT_SERVICE_PORT", "48805"))
    os.makedirs(DATA, exist_ok=True)
    log("booksd on", port)
    kick()                                  # what an earlier run could not send
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
