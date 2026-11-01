#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The MCP server of the Clock app (manifest: caroline.mcp, stdio): Caroline's alarm-clock tools that work with the app closed.
It speaks to alarmd through the shell's endpoints (/api/app/clock/..., header X-IntelliCat-Mcp). Usage: clock_mcp.py <shell port>"""
import json, sys, urllib.error, urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8765}/api/app/clock/"
DAYS = "days: list of weekdays, 0 = Monday .. 6 = Sunday; empty or absent = once"
TOOLS = [
    ("list_alarms", "All alarms with id, time, weekdays, label and whether they are on", {}, lambda a: call("GET", "alarms")),
    ("set_alarm", "Adds an alarm (or changes the one with this id)", {"hour": "integer", "minute": "integer", "days": "array", "label": "string", "id": "integer", "enabled": "boolean"},
     lambda a: call("POST", "alarms/save", a)),
    ("toggle_alarm", "Turns an alarm on or off", {"id": "integer", "enabled": "boolean"}, lambda a: call("POST", "alarms/toggle", a)),
    ("delete_alarm", "Deletes an alarm", {"id": "integer"}, lambda a: call("POST", "alarms/delete", a)),
    ("stop_ringing", "Stops the alarm or timer that rings now", {}, lambda a: call("POST", "ringing/dismiss", {})),
    ("snooze", "Snoozes the ringing alarm (default 9 minutes)", {"minutes": "integer"}, lambda a: call("POST", "ringing/snooze", a)),
    ("start_timer", "Starts the countdown timer", {"seconds": "integer", "label": "string"}, lambda a: call("POST", "timer/start", a)),
    ("get_timer", "The running timer (end as epoch seconds) and the console's time now", {}, lambda a: call("GET", "timer")),
    ("cancel_timer", "Cancels the timer", {}, lambda a: call("POST", "timer/cancel", {})),
]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "X-IntelliCat-Mcp": "clock"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def schema(props):
    types = {"integer": {"type": "integer"}, "string": {"type": "string"}, "boolean": {"type": "boolean"}, "array": {"type": "array", "items": {"type": "integer"}, "description": DAYS}}
    return {"type": "object", "properties": {k: types[v] for k, v in props.items()}}


def handle(msg):
    m, i = msg.get("method"), msg.get("id")
    if m == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "clock", "version": "1.0.0"}}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": schema(p)} for n, d, p, _ in TOOLS]}
    if m == "tools/call":
        p = msg.get("params", {})
        for n, _, _, fn in TOOLS:
            if n == p.get("name"):
                out = fn(p.get("arguments") or {})
                return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": "error" in out}
        return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
    if i is not None:
        return {}
    return None


for line in sys.stdin:
    try:
        msg = json.loads(line)
        res = handle(msg)
    except Exception as e:
        print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}), flush=True)
        continue
    if res is not None and msg.get("id") is not None:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
