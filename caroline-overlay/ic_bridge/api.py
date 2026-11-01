# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""/api/ic/bridge/* on her backend: her own plugins for another assistant of the slot (ic_bridge/__init__.py).

    GET  /api/ic/bridge/tools                  {groups: [{name, description, guidance}], tools: [{name, group, description, inputSchema}]}
    GET  /api/ic/bridge/guidance?group=<name>  {group, text}: the usage guidance of a group (her plugin's usage_instructions)
    POST /api/ic/bridge/call {name, args, tab} an MCP tool result {content, isError}

Only the groups of BRIDGED: what the assistant of the slot does not get from the OS Beroun MCP server (her groups beroun, agents and app-<id> are that table), from its own
web tools, or not at all by design (a shell, the desktop's windows, mouse and keyboard, her voice, her engine's own housekeeping)."""
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from . import TAB_PREFIX

BRIDGED = (
    "messages", "telegram", "whatsapp", "signal", "slack", "discord", "messenger", "ratatosk", "companion", "email", "email-batch",   # Communicator: read and send
    "memory", "memory-search", "working_memory", "instruction_memory", "event_memory", "owner_profile", "topics",                     # her memory
    "scheduler",                                                                                                                       # reminders (reminders.py)
    "calendar", "contacts", "notes", "document_service", "summarize",                                                                  # the owner's data
    "image-gen", "appbrowser",                                                                                                         # images, the device browser
)


def plugins():
    """Her plugins of BRIDGED, as her loader finds them now (an import that fails on this device leaves its plugin out, as it does for her)."""
    from app.plugins.loader import discover_plugins
    return [p for p in discover_plugins() if p is not None and p.name in BRIDGED]


def schema(tool):
    """The tool's JSON Schema, its optional parameters really optional (her loader's own rule)."""
    from app.plugins import loader
    s = loader.schema_with_optionals(tool.input_schema)
    if isinstance(s, dict) and "type" in s and "properties" in s:
        return s
    from claude_agent_sdk import SdkMcpTool
    return loader._sdk_build_input_schema(SdkMcpTool(name=tool.name, description=tool.description, input_schema=s, handler=None))


def listing():
    groups, tools = [], []
    for p in plugins():
        groups.append({"name": p.name, "description": p.description or "", "guidance": bool(p.usage_instructions)})
        for t in p.tools:
            tools.append({"name": t.name, "group": p.name, "description": t.description, "inputSchema": schema(t)})
    return {"groups": groups, "tools": tools}


def _text(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def response(envelope):
    """dispatch()'s envelope as an MCP result: a plain {"text"} result as its text, MCP content and images as her loader renders them."""
    from app.plugins import loader
    status, result = envelope.get("status"), envelope.get("result")
    rich = isinstance(result, dict) and (isinstance(result.get("content"), list) or "image_base64" in result)
    if status == "done" and isinstance(result, dict) and "text" in result and not rich:
        out = _text(str(result["text"]), result.get("is_error"))
    elif status == "done" and not rich:
        out = _text(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str))
    elif status == "cancelled":
        out = _text("the call was cancelled", True)
    else:
        out = loader._envelope_to_mcp_response(envelope)
        out["isError"] = bool(out.get("isError"))
    if envelope.get("memory_note"):
        out["content"] = list(out["content"]) + [{"type": "text", "text": envelope["memory_note"]}]
    return out


async def call(name, args, tab):
    """One call, run by her dispatch() to its end (a client of MCP waits; there is no operation to poll), in her session context of the tab "assistant:<tab>"."""
    from app import session_context
    from app.operations import dispatch
    from app.plugins import loader
    found = next(((p, t) for p in plugins() for t in p.tools if t.name == name), None)
    if found is None:
        return _text(f"no tool '{name}' among her tools for the assistant", True)
    plugin, tool = found
    try:
        from ic_messaging import actors   # her send tools leave the note that names the sender of the echo
        tool = actors.wrap_plugin_tool(tool)
    except ImportError:
        pass
    args = {**{k: None for k in loader.optional_keys(tool.input_schema)}, **(args if isinstance(args, dict) else {})}
    token = session_context.set_tab_id(TAB_PREFIX + str(tab or "1"))
    try:
        envelope = await dispatch(plugin.name, tool.name, tool.handler, args, wait_to_end=True)
    except Exception as e:   # noqa: BLE001  a broken handler is the caller's error result, never a broken backend
        return _text(f"{type(e).__name__}: {e}", True)
    finally:
        session_context._current_tab_id.reset(token)
    return response(envelope)


def build_router():
    r = APIRouter(prefix="/api/ic/bridge")

    @r.get("/tools")
    async def tools():
        return listing()

    @r.get("/guidance")
    async def guidance(group: str = Query("")):
        p = next((p for p in plugins() if p.name == group), None)
        if p is None:
            return JSONResponse({"error": f"no group '{group}'"}, status_code=404)
        return {"group": p.name, "text": p.usage_instructions or p.description or ""}

    @r.post("/call")
    async def call_(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "the body is not JSON"}, status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("name"), str):
            return JSONResponse({"error": "name is required"}, status_code=400)
        return await call(body["name"], body.get("args") or {}, body.get("tab"))

    return r
