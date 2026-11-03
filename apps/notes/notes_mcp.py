#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The MCP server of the Notes app (manifest: caroline.mcp, stdio): Caroline's note tools that work with the app closed.
It speaks to notesd through the shell's endpoints (/api/app/notes/..., header X-IntelliCat-Mcp). Usage: notes_mcp.py <shell port>"""
import json, sys, urllib.error, urllib.parse, urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8765}/api/app/notes/"


def call(method, path, body=None, query=None):
    q = "?" + urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None}) if query else ""
    req = urllib.request.Request(BASE + path + q, data=json.dumps(body or {}).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "X-IntelliCat-Mcp": "notes"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def append(a):
    old = call("GET", "note", query={"id": a.get("id")})
    if "error" in old:
        return old
    text = old["text"] + ("" if not old["text"] or old["text"].endswith("\n") else "\n") + str(a.get("text", ""))
    return call("POST", "save", {"id": a["id"], "text": text})


S = {"type": "string"}
TOOLS = [
    ("list_notes", "The notes and sub-folders of a folder ('' is the top), newest first; each note has its id, title and a line of preview", {"folder": S},
     lambda a: call("GET", "list", query={"folder": a.get("folder", "")})),
    ("search_notes", "Notes whose text or folder holds every word of the query", {"q": S}, lambda a: call("GET", "search", query={"q": a.get("q", "")})),
    ("get_note", "The whole text of a note", {"id": S}, lambda a: call("GET", "note", query={"id": a.get("id")})),
    ("create_note", "Creates a note; the first line is its title. Optional folder such as 'Work/Projects'", {"text": S, "folder": S},
     lambda a: call("POST", "save", {"text": a.get("text", ""), "folder": a.get("folder", "")})),
    ("append_to_note", "Adds text at the end of a note (a new line first)", {"id": S, "text": S}, append),
    ("replace_note", "Replaces the whole text of a note; read it first with get_note so that nothing that must stay is lost", {"id": S, "text": S},
     lambda a: call("POST", "save", {"id": a.get("id"), "text": a.get("text", "")})),
    ("move_note", "Moves a note into a folder ('' is the top)", {"id": S, "folder": S}, lambda a: call("POST", "move", a)),
    ("delete_note", "Deletes a note (the account keeps it as deleted)", {"id": S}, lambda a: call("POST", "delete", a)),
    ("create_folder", "Creates an empty folder", {"path": S}, lambda a: call("POST", "folder", {"op": "create", "path": a.get("path")})),
    ("rename_folder", "Renames or moves a folder with everything in it", {"path": S, "to": S}, lambda a: call("POST", "folder", {"op": "rename", "path": a.get("path"), "to": a.get("to")})),
    ("delete_folder", "Deletes a folder with every note in it", {"path": S}, lambda a: call("POST", "folder", {"op": "delete", "path": a.get("path")})),
]


def handle(msg):
    m = msg.get("method")
    if m == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "notes", "version": "1.0.0"}}
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
