#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The MCP server of the Video Recorder app (manifest: caroline.mcp, stdio): Caroline's screen-recording tools that work with the app closed.
It speaks to screenrecd through the shell's endpoints (/api/app/video-recorder/..., header X-IntelliCat-Mcp). Usage: recorder_mcp.py <shell port>"""
import json, sys, urllib.error, urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8765}/api/app/video-recorder/"
TOOLS = [
    ("recording_status", "Whether the screen is being recorded (state idle | waiting | recording, the file and the seconds so far) and whether this device can record at all", {}, lambda a: call("GET", "status")),
    ("start_recording", "Starts recording the screen. delay: seconds before it starts (0..10, default 0); audio: also record the sound the console plays (default true)", {"delay": "number", "audio": "boolean"},
     lambda a: call("POST", "start", a)),
    ("stop_recording", "Stops the recording and saves the file (Videos/Screen-<date>.mp4 on the SD card)", {}, lambda a: call("POST", "stop", {})),
    ("take_screenshot", "Takes a screenshot of the screen now (Pictures/Screenshot-<date>.png on the SD card)", {}, lambda a: call("POST", "shot", {})),
    ("list_recordings", "The saved screen recordings and screenshots, newest first: name, kind (video | image), path on the SD card, bytes, time", {}, lambda a: call("GET", "list")),
]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "X-IntelliCat-Mcp": "recorder"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def schema(props):
    types = {"number": {"type": "number"}, "boolean": {"type": "boolean"}}
    return {"type": "object", "properties": {k: types[v] for k, v in props.items()}}


def handle(msg):
    m, i = msg.get("method"), msg.get("id")
    if m == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "recorder", "version": "1.0.0"}}
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
