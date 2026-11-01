# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Who wrote a message that left an account: the app, her tools, or somebody else (the phone).

The app's sends carry their actor from the start. A send made by one of her own tools (telegram_send_message, whatsapp_send_message, ...)
reaches the store later, as the echo the service reports back, so the tools are wrapped (hooks.py) to leave a short-lived note here:
"this account is about to send this text". When the echo arrives the note is claimed and the message is stored with her as its actor;
an echo nobody announced came from elsewhere (the owner's phone) and is stored as `external`.
"""
import dataclasses, functools, threading, time

# her send tools -> (service, default account of that tool)
SEND_TOOLS = {
    "telegram_send_message": ("telegram", "self"), "telegram_send_file": ("telegram", "self"),
    "whatsapp_send_message": ("whatsapp", "self"), "whatsapp_send_file": ("whatsapp", "self"),
    "signal_send_message": ("signal", "self"), "signal_send_file": ("signal", "self"),
    "slack_send_message": ("slack", "self"),
}
TTL_S = 180.0
_lock = threading.Lock()
_notes = []        # [account id, text, actor, time]


def _norm(text):
    return " ".join(str(text or "").split())


def expect(account, text, actor="caroline-tool"):
    now = time.time()
    with _lock:
        _notes[:] = [n for n in _notes if now - n[3] < TTL_S]
        _notes.append([account, _norm(text), actor, now])


def claim(account, text):
    """The actor announced for this outgoing text, or None."""
    now, t = time.time(), _norm(text)
    with _lock:
        for i, n in enumerate(_notes):
            if n[0] == account and n[1] == t and now - n[3] < TTL_S:
                del _notes[i]
                return n[2]
    return None


def reset():
    with _lock:
        _notes.clear()


def tracking(tool_name, handler):
    service, default = SEND_TOOLS[tool_name]

    @functools.wraps(handler)
    async def wrapped(args, report_progress):
        try:
            account = str((args or {}).get("account") or default).strip()
            text = (args or {}).get("text") or (args or {}).get("caption") or ""
            expect(f"{service}:{'owner' if account == 'owner' else 'self'}", text)
        except Exception:
            pass
        return await handler(args, report_progress)
    return wrapped


def wrap_plugin_tool(tool):
    """A PluginTool copy whose handler leaves the note, for her send tools; any other tool unchanged."""
    if tool.name in SEND_TOOLS:
        return dataclasses.replace(tool, handler=tracking(tool.name, tool.handler))
    return tool
