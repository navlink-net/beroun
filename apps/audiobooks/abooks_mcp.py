#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The MCP server of the Audiobooks app (manifest: caroline.mcp, stdio): Caroline's audiobook tools that work with the app closed.
It speaks to abooksd through the shell's endpoints (/api/app/audiobooks/..., header X-IntelliCat-Mcp). Usage: abooks_mcp.py <shell port>"""
import json, sys, urllib.error, urllib.parse, urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8765}/api/app/audiobooks/"


def call(method, path, body=None, query=None):
    q = "?" + urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None}) if query else ""
    req = urllib.request.Request(BASE + path + q, data=json.dumps(body or {}).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "X-IntelliCat-Mcp": "audiobooks"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def scope(a):
    return "mine" if a.get("scope") == "mine" else "public"


def find_audiobooks(a):
    r = call("GET", "catalog", query={"scope": scope(a), "q": a.get("q", ""), "language": a.get("language", "")})
    if "error" in r:
        return r
    r["books"] = [{k: b[k] for k in ("id", "title", "author", "narrator", "language", "genre", "files", "listening") if k in b} for b in r["books"][:30]]
    return r


def audiobook_info(a):
    r = call("GET", "book", query={"scope": scope(a), "id": a.get("id")})
    if "error" in r:
        return r
    return {k: r[k] for k in ("id", "scope", "title", "author", "narrator", "description", "files", "listening", "speed") if k in r}


def set_place(a):
    body = {"scope": scope(a), "id": a.get("id"), "index": a.get("file", 1) - 1 if isinstance(a.get("file"), (int, float)) else 0, "time": a.get("seconds", 0)}
    if a.get("speed") is not None:
        body["speed"] = a["speed"]
    return call("POST", "progress", body)


S, N = {"type": "string"}, {"type": "number"}
TOOLS = [
    ("find_audiobooks", "Audiobooks of the catalog (scope 'public' or 'mine') whose title, author or narrator holds every word of q; optional language such as 'Russian'", {"q": S, "scope": S, "language": S}, find_audiobooks),
    ("audiobook_info", "One audiobook: the annotation, the files (their count is the number of parts) and where the owner stopped (listening: file index from 0, second)", {"id": S, "scope": S}, audiobook_info),
    ("recent_audiobooks", "The audiobooks the owner is listening to, the last first, with the file and the second where they stopped (the same on the device and on the web)", {}, lambda a: call("GET", "recent")),
    ("set_listening_place", "Sets where the owner stopped in an audiobook: file (number from 1) and seconds in it. The next time the book is opened - on any device or on the web - it goes on from there", {"id": S, "scope": S, "file": N, "seconds": N},
     set_place),
    ("list_bookmarks", "The bookmarks of an audiobook in listening order: id, file (from 0), second, label. They are the owner's, the same on the device and on the web", {"id": S, "scope": S},
     lambda a: call("GET", "bookmarks", query={"scope": scope(a), "id": a.get("id")})),
    ("add_bookmark", "Adds a bookmark in an audiobook at a file (number from 1) and second; label is optional", {"id": S, "scope": S, "file": N, "seconds": N, "label": S},
     lambda a: call("POST", "bookmark", {"scope": scope(a), "id": a.get("id"), "index": int(a.get("file") or 1) - 1, "time": a.get("seconds", 0), "label": a.get("label", "")})),
    ("remove_bookmark", "Removes a bookmark of an audiobook by its id (from list_bookmarks)", {"id": S, "scope": S, "bookmark_id": S},
     lambda a: call("POST", "unbookmark", {"scope": scope(a), "id": a.get("id"), "bookmark_id": a.get("bookmark_id")})),
    ("listening_settings", "The playback speed of the owner (1 = normal, 0.5-3); pass speed to change it", {"speed": N},
     lambda a: call("POST", "settings", {"speed": a["speed"]}) if "speed" in a else call("GET", "settings")),
]


def handle(msg):
    m = msg.get("method")
    if m == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "audiobooks", "version": "1.0.0"}}
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
