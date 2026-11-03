#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The MCP server of the Books app (manifest: caroline.mcp, stdio): Caroline's book tools that work with the app closed.
It speaks to booksd through the shell's endpoints (/api/app/books/..., header X-IntelliCat-Mcp). Usage: books_mcp.py <shell port>"""
import json, sys, urllib.error, urllib.parse, urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8765}/api/app/books/"


def call(method, path, body=None, query=None):
    q = "?" + urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None}) if query else ""
    req = urllib.request.Request(BASE + path + q, data=json.dumps(body or {}).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "X-IntelliCat-Mcp": "books"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def scope(a):
    return "mine" if a.get("scope") == "mine" else "public"


def find_books(a):
    r = call("GET", "catalog", query={"scope": scope(a), "q": a.get("q", ""), "language": a.get("language", "")})
    if "error" in r:
        return r
    r["books"] = [{k: b[k] for k in ("id", "title", "author", "language", "genre", "downloaded") if k in b} for b in r["books"][:30]]
    return r


def chapters(a):
    r = call("GET", "book", query={"scope": scope(a), "id": a.get("id")})
    return r if "error" in r else {"title": r["title"], "author": r["author"], "annotation": r.get("annotation", ""), "read": r.get("read"),
                                    "chapters": [{"n": i, "title": c["title"], "blocks": c["blocks"]} for i, c in enumerate(r["chapters"])]}


def read_chapter(a):
    r = call("GET", "text", query={"scope": scope(a), "id": a.get("id"), "chapter": a.get("chapter", 0)})
    if "error" in r:
        return r
    lim = int(a.get("max_chars") or 12000)
    out, used = [], 0
    for i, b in enumerate(r["blocks"]):
        if i < int(a.get("from_block") or 0):
            continue
        if used + len(b["x"]) > lim and out:
            return {"title": r["title"], "text": "\n".join(out), "next_block": i}
        out.append(b["x"]); used += len(b["x"])
    return {"title": r["title"], "text": "\n".join(out), "next_block": None}


S, N = {"type": "string"}, {"type": "number"}
TOOLS = [
    ("find_books", "Books of the catalog (scope 'public' or 'mine') whose title or author holds every word of q; optional language such as 'ru'", {"q": S, "scope": S, "language": S}, find_books),
    ("book_contents", "The chapters of a book (n, title, block count), the annotation and where the owner stopped", {"id": S, "scope": S}, chapters),
    ("read_chapter", "The text of a chapter, up to max_chars (default 12000) from from_block; next_block tells where to go on", {"id": S, "scope": S, "chapter": N, "from_block": N, "max_chars": N}, read_chapter),
    ("search_in_book", "Places in a book where the words occur: chapter, block and a fragment", {"id": S, "scope": S, "q": S},
     lambda a: call("GET", "find", query={"scope": scope(a), "id": a.get("id"), "q": a.get("q", "")})),
    ("recent_books", "The books the owner is reading, with the chapter and percent where they stopped", {}, lambda a: call("GET", "recent")),
]


def handle(msg):
    m = msg.get("method")
    if m == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "books", "version": "1.0.0"}}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p}} for n, d, p, _ in TOOLS]}
    if m == "tools/call":
        p = msg.get("params", {})
        for n, _, _, fn in TOOLS:
            if n == p.get("name"):
                out = fn(p.get("arguments") or {})
                return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": "error" in out}
        return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
    return {} if msg.get("id") is not None else None


for line in sys.stdin:
    try:
        msg = json.loads(line)
        res = handle(msg)
    except Exception as e:
        print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}), flush=True)
        continue
    if res is not None and msg.get("id") is not None:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
