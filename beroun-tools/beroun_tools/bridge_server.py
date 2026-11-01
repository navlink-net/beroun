# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Caroline's own tools for another assistant of the slot: the MCP server `caroline-tools`.

    python3 -m beroun_tools.bridge_server        (stdio: JSON-RPC 2.0, one message per line)

Her backend runs in every build, as Communicator's host; her plugins that the OS Beroun MCP server (mcp_server.py) does not have (the messages and the messengers'
send tools, mail, memory, reminders, calendar, contacts, notes, images, the device browser) are served by it at /api/ic/bridge/* (caroline-overlay/ic_bridge).
This server is the client of that API for Claude and Codex: the tools keep her names, a call runs in her process. The environment says where:
CAROLINE_PORT (her backend), INTELLICAT_ASSISTANT_TAB (the conversation of the assistant that started this server: a reminder it schedules comes back there).
When her backend does not answer (no account or network yet, or starting), the list holds only `caroline_tools_status`, which says so; the client is told when the list
changes. Standard library only."""
import json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
PORT = os.environ.get("CAROLINE_PORT", str(48765 + int(os.environ.get("INTELLICAT_PORT_OFFSET") or 0)))
TAB = os.environ.get("INTELLICAT_ASSISTANT_TAB", "1")
CALL_TIMEOUT_S = 600.0   # a call runs to its end (a mail batch, an image): the client's own timeout is the shorter one
WATCH_S = 15.0
GUIDE = "caroline_tool_guidance"
STATUS = "caroline_tools_status"
_out = threading.Lock()
_cache = {"at": 0.0, "list": None}


def _send(msg):
    with _out:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _api(path, body=None, timeout=10.0):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/ic/bridge/{path}", data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def listing(fresh=False):
    """Her answer to /tools ({groups, tools}), or None when her backend does not answer (cached for a few seconds)."""
    if fresh or time.time() - _cache["at"] > 5.0:
        try:
            _cache["list"] = _api("tools")
        except (OSError, ValueError, urllib.error.URLError):
            _cache["list"] = None
        _cache["at"] = time.time()
    return _cache["list"]


def instructions(lst):
    if not lst:
        return ("Caroline's own tools (messages and messengers, mail, memory, reminders, calendar, contacts, notes, images, the device browser) are served by her backend, "
                f"which does not answer now. Call {STATUS} to see whether it is back.")
    head = ("Caroline's own tools, served from her backend for you, the assistant of this device: she runs as the host of Communicator. A reminder you schedule "
            "(schedule_reminder) comes back to this conversation when it is due. Ask the owner before anything that sends a message as the owner, deletes or buys. "
            f"Her groups ({GUIDE} gives the guidance of one):")
    return head + "\n" + "\n".join(f"- {g['name']}: {g['description']}" if g.get("description") else f"- {g['name']}" for g in lst["groups"])


def tool_list(lst):
    guide = {"name": GUIDE, "description": "The usage guidance of one group of Caroline's tools (its conventions; read it before the first use of a group)",
             "inputSchema": {"type": "object", "properties": {"group": {"type": "string", "description": "the group, as the server's instructions list them"}}, "required": ["group"]}}
    status = {"name": STATUS, "description": "Whether Caroline's backend, which serves these tools, answers now; and her groups",
              "inputSchema": {"type": "object", "properties": {}}}
    if not lst:
        return [status]
    return [{"name": t["name"], "description": f"[{t['group']}] {t['description']}", "inputSchema": t["inputSchema"]} for t in lst["tools"]] + [guide, status]


def _text(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def call(name, args):
    try:
        if name == STATUS:
            lst = listing(fresh=True)
            return _text(instructions(lst) if lst else "Her backend does not answer now (no account or network yet, or it is starting).", not lst)
        if name == GUIDE:
            g = str((args or {}).get("group") or "")
            return _text(_api("guidance?group=" + urllib.parse.quote(g)).get("text") or f"the group {g} has no guidance of its own")
        return _api("call", {"name": name, "args": args or {}, "tab": TAB}, timeout=CALL_TIMEOUT_S)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("error", "")
        except Exception:   # noqa: BLE001
            detail = ""
        return _text(f"her backend refused it ({e.code}{': ' + detail if detail else ''})", True)
    except (OSError, ValueError, urllib.error.URLError) as e:
        return _text(f"her backend does not answer ({e})", True)


def _call(rid, name, args):
    r = call(name, args)
    _send({"jsonrpc": "2.0", "id": rid, "result": {"content": r.get("content") or [], "isError": bool(r.get("isError"))}})


def _watch(state):
    """Tells the client when the list changes (her backend came up or went, a plugin came)."""
    while True:
        time.sleep(WATCH_S)
        if not state["ready"]:
            continue
        names = [t["name"] for t in tool_list(listing(fresh=True))]
        if state["names"] is not None and names != state["names"]:
            _send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        state["names"] = names


def main():
    state = {"ready": False, "names": None}
    threading.Thread(target=_watch, args=(state,), daemon=True).start()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:
            continue
        if method == "initialize":
            asked = params.get("protocolVersion")
            _send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "caroline-tools", "title": "Caroline's tools", "version": "1.0"},
                "instructions": instructions(listing())}})
        elif method == "notifications/initialized":
            state["ready"] = True
        elif method.startswith("notifications/"):
            pass
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            tools = tool_list(listing())
            state["names"] = [t["name"] for t in tools]
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            threading.Thread(target=_call, args=(rid, params.get("name"), params.get("arguments")), daemon=True).start()
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
