# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The OS Beroun MCP server (docs/assistant-contract.md, section 3): the one table of the assistant's tools (table.py) for a provider that speaks MCP (Claude, Codex).

    python3 -m beroun_tools.mcp_server        (stdio: JSON-RPC 2.0, one message per line; INTELLICAT_PORT names the shell)

The tools are the groups beroun, agents and app-<id> (the tools of the apps' MCP servers are not here: the provider gets those servers themselves), with the same names
Caroline has. The guidance of every group is the server's `instructions`. The list is built anew on every tools/list; when it changes (an app installed, an agent
published) the client is told (notifications/tools/list_changed). A call runs on a thread of its own: a message box may wait for the owner for minutes.
Standard library only."""
import json, sys, threading, time

from . import table

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
WATCH_S = 5.0
_out = threading.Lock()


def _send(msg):
    with _out:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _log(text):
    print(f"beroun-mcp: {text}", file=sys.stderr, flush=True)


def _groups():
    return table.groups(log=_log)


def instructions(groups):
    head = ("These are the tools of OS Beroun, the operating system of this device; you are its assistant, the manager of the system. The groups, and how to use each:")
    return head + "\n\n" + "\n\n".join(f"[{g.name}] {g.description}" + (f"\n{g.guidance}" if g.guidance else "") for g in groups)


def tool_list(groups):
    seen, out = set(), []
    for g in groups:
        for t in g.tools:
            if t.name not in seen:
                seen.add(t.name)
                out.append({"name": t.name, "description": t.description, "inputSchema": t.schema})
    return out


def _call(rid, name, args):
    tool = next((t for g in _groups() for t in g.tools if t.name == name), None)
    if tool is None:
        return _send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"no tool '{name}' (it may have gone with its app)"}], "isError": True}})
    try:
        r = tool.handler(args or {})
    except Exception as e:   # a wrong argument, the shell's refusal: the assistant reads it as the result
        r = {"text": f"{type(e).__name__}: {e}", "is_error": True}
    content = r.get("content") or [{"type": "text", "text": r.get("text", "")}]
    _send({"jsonrpc": "2.0", "id": rid, "result": {"content": content, "isError": bool(r.get("is_error"))}})


def _watch(state):
    """Tells the client when the tools change."""
    while True:
        time.sleep(WATCH_S)
        if not state["ready"]:
            continue
        try:
            names = [t["name"] for t in tool_list(_groups())]
        except Exception as e:
            _log(f"the list could not be built: {e!r}")
            continue
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
            continue   # a response to something we never ask
        if method == "initialize":
            asked = params.get("protocolVersion")
            groups = _groups()
            _send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "beroun", "title": "OS Beroun", "version": "1.0"},
                "instructions": instructions(groups)}})
        elif method == "notifications/initialized":
            state["ready"] = True
        elif method.startswith("notifications/"):
            pass
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            tools = tool_list(_groups())
            state["names"] = [t["name"] for t in tools]
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            threading.Thread(target=_call, args=(rid, params.get("name"), params.get("arguments")), daemon=True).start()
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
