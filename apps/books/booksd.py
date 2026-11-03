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

The key of the site's pages (it is in every page of squirrelwisdom.com, not a secret of the account) is $INTELLICAT_BOOKS_KEY or "books_key" in ~/.intellicat/keys.json
($INTELLICAT_KEYS); it is not in this repository. The address of the site is $INTELLICAT_SW_URL (the shell's setting; a test points it at a stand-in).
"""

import hashlib
import json
import os
import re
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


def get_file(path, limit=MAX_BOOK_BYTES):
    """A file of the site, by its path from the root (contents/Books/...)."""
    req = urllib.request.Request(urllib.parse.urljoin(SW_URL, path), headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise BooksError("notfound" if e.code == 404 else "network" if e.code >= 500 else "service", f"HTTP {e.code} for {path}")
    except (urllib.error.URLError, OSError) as e:
        raise BooksError("network", str(getattr(e, "reason", e)))
    if len(data) > limit:
        raise BooksError("service", f"{path} is too big")
    return data


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
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, OSError) as e:
            raise BooksError("network", str(getattr(e, "reason", e)))
        except ValueError:
            raise BooksError("service", "The site answered with something else than JSON")
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


def op_recent(q):
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
        book["id"], book["scope"] = str(bid), scope
        os.makedirs(path_of("books"), exist_ok=True)
        save_json(os.path.join("books", k + ".json"), book)
        evict()
    with lock:
        _open.update(key=k, book=book)
    return book


def read_of(scope, bid):
    r = read_state().get(f"{scope}:{bid}")
    return {"chapter": r.get("chapter", 0), "block": r.get("block", 0), "pct": r.get("pct", 0)} if r else None


def op_book(q):
    scope, bid = valid_scope(q.get("scope", [""])[0]), q.get("id", [""])[0]
    if not scope or not bid:
        return {"error": "scope and id"}, 400
    b = open_book(scope, bid)
    return {"id": b["id"], "scope": scope, "title": b["title"], "author": b["author"], "annotation": b.get("annotation", ""),
            "chapters": [{"title": c["title"], "blocks": len(c["blocks"])} for c in b["chapters"]], "read": read_of(scope, bid)}


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
    with lock:
        d = read_state()
        old = d.get(f"{scope}:{bid}") or {}
        d[f"{scope}:{bid}"] = {"scope": scope, "id": bid, "title": (meta or {}).get("title") or old.get("title") or "?", "author": (meta or {}).get("author") or old.get("author") or "",
                               "chapter": max(0, ch), "block": max(0, bl), "pct": max(0, min(100, round(pct, 1))), "at": int(time.time())}
        write_state(d)
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
        self.route({"catalog": op_catalog, "recent": op_recent, "book": op_book, "text": op_text, "find": op_find, "status": op_status}, q)

    def do_POST(self):
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return self.reply({"error": "the body is a JSON object"}, 400)
        self.route({"progress": op_progress}, b)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("INTELLICAT_SERVICE_PORT", "48805"))
    os.makedirs(DATA, exist_ok=True)
    log("booksd on", port)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
