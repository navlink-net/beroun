#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The MCP server of the Calendar app (manifest: caroline.mcp, stdio): Caroline's calendar and task-list tools that work with the app closed.
It speaks to calendard through the shell's endpoints (/api/app/calendar/..., header X-IntelliCat-Mcp). Usage: calendar_mcp.py <shell port>"""
import json, sys, urllib.error, urllib.parse, urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8765}/api/app/calendar/"


def call(method, path, body=None, query=None):
    q = "?" + urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None}) if query else ""
    req = urllib.request.Request(BASE + path + q, data=json.dumps(body or {}).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "X-IntelliCat-Mcp": "calendar"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
TOOLS = [
    ("list_events", "Events between two dates (YYYY-MM-DD, both optional, inclusive)", {"from": S, "to": S}, lambda a: call("GET", "events", query={"from": a.get("from"), "to": a.get("to")})),
    ("add_event", "Adds an event (or changes the one with this id): title, date YYYY-MM-DD, optional time HH:MM and note", {"title": S, "date": S, "time": S, "note": S, "id": I},
     lambda a: call("POST", "events/save", a)),
    ("delete_event", "Deletes an event", {"id": I}, lambda a: call("POST", "events/delete", a)),
    ("list_tasks", "The task list; done=true only the finished, done=false only the open ones", {"done": B},
     lambda a: call("GET", "tasks", query={"done": None if "done" not in a else ("1" if a["done"] else "0")})),
    ("add_task", "Adds a task (or changes the one with this id): title and an optional due date YYYY-MM-DD", {"title": S, "due": S, "id": I}, lambda a: call("POST", "tasks/save", a)),
    ("complete_task", "Marks a task done (done=false takes it back)", {"id": I, "done": B}, lambda a: call("POST", "tasks/toggle", a)),
    ("delete_task", "Deletes a task", {"id": I}, lambda a: call("POST", "tasks/delete", a)),
]


def handle(msg):
    m = msg.get("method")
    if m == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "calendar", "version": "1.0.0"}}
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
