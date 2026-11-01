# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The shell daemon (shelld) as the assistant's tools see it (docs/assistant-contract.md): the apps it can drive, and what it may ask of the system as the role "assistant".
One client for every provider: Caroline's plugins (caroline-overlay/ic_apps) and the OS Beroun MCP server (mcp_server.py) both use it."""
import json, os, threading, time, urllib.error, urllib.request

PORT = os.environ.get("INTELLICAT_PORT", "8765")
BASE = f"http://127.0.0.1:{PORT}"
TTL_S = 5.0

_lock = threading.Lock()
_cache = {"at": 0.0, "apps": [], "agents_at": 0.0, "agents": []}


def _request(path, body=None, timeout=3.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("error", "")
        except Exception:
            detail = ""
        raise RuntimeError(f"{path}: {e.code} {detail}".strip())


def apps(fresh=False):
    """[{id, name, description, folder, skills: [abs folders], mcp: [...], tools: [...]}]; the last answer when the shell does not answer."""
    with _lock:
        if not fresh and time.time() - _cache["at"] < TTL_S:
            return _cache["apps"]
    try:
        found = _request("/api/assistant/apps", timeout=1.5)
    except Exception:
        found = None
    with _lock:
        if found is not None:
            _cache.update(at=time.time(), apps=found)
        else:
            _cache["at"] = time.time() - TTL_S + 1.0  # try again in a second, not at every call
        return _cache["apps"]


def agents(fresh=False):
    """The agents the apps publish (docs/architecture.md): [{key, app, app_name, id, description, skills, camerlengo, permissions}]; the last answer when the shell does not answer."""
    with _lock:
        if not fresh and time.time() - _cache["agents_at"] < TTL_S:
            return _cache["agents"]
    try:
        found = _request("/api/agents", timeout=1.5)
    except Exception:
        found = None
    with _lock:
        if found is not None:
            _cache.update(agents_at=time.time(), agents=found)
        else:
            _cache["agents_at"] = time.time() - TTL_S + 1.0
        return _cache["agents"]


def asking():
    """The tasks that wait for an answer of the owner (state input-required): a new turn of hers learns of them without having to remember. Cached like the agents."""
    with _lock:
        if time.time() - _cache.get("asking_at", 0.0) < TTL_S:
            return _cache.get("asking", [])
    try:
        found = _request("/api/agents/tasks?state=input-required", timeout=1.5)
    except Exception:
        found = None
    with _lock:
        if found is not None:
            _cache.update(asking_at=time.time(), asking=found)
        else:
            _cache["asking_at"] = time.time() - TTL_S + 1.0
        return _cache.get("asking", [])


def agent_start(agent, skill, given, timeout=40.0):
    """Gives an agent a task: the task as the broker has it (a short skill is already completed in the answer)."""
    return _request("/api/agents/tasks", {"agent": agent, "skill": skill, "input": given or {}}, timeout=timeout)


def agent_task(task_id, timeout=40.0):
    return _request(f"/api/agents/tasks/{task_id}", timeout=timeout)


def agent_answer(task_id, answer, timeout=40.0):
    return _request(f"/api/agents/tasks/{task_id}/answer", {"answer": answer}, timeout=timeout)


def agent_cancel(task_id):
    return _request(f"/api/agents/tasks/{task_id}/cancel", {})


def running():
    return _request("/api/running")


def launch(app_id, arg=None):
    return _request("/api/launch", {"id": app_id, **({"arg": arg} if arg is not None else {})})


def launch_arg(text):
    """What an app is started with: the text the assistant gave (a file path) as {"open": path}, the form the file manager uses; None for nothing."""
    return {"open": str(text)} if text else None


def close(app_id):
    """Asks the app to save and close (what MODE does); False when it is not running."""
    pids = [r["pid"] for r in running() if r.get("app") == app_id]
    for pid in pids:
        _request("/api/close", {"pid": pid})
    return bool(pids)


def prepare(app_id):
    """Starts the services the app needs (what opening it does) without opening its window."""
    return _request("/api/assistant/app/prepare", {"app": app_id})


def page_tool(app_id, name, args, timeout=40.0, screen="console"):
    """A tool of the app's page: the shell opens the app if needed, asks its page and returns {text, result?, is_error?}. screen "external": the app's big page on the HDMI screen."""
    return _request("/api/assistant/app/tool", dict({"app": app_id, "tool": name, "args": args}, **({"screen": screen} if screen != "console" else {})), timeout=timeout)


def display_status():
    """The external (HDMI) screen: {connected, width, height, focus, top, stack}."""
    return system_api("display/status")


def display_open(app, arg=None):
    return system_api("display/open", {"app": app, "arg": arg}, 20.0)


def display_close(app):
    return system_api("display/close", {"app": app}, 20.0)


def display_focus(to):
    return system_api("display/focus", {"to": to})


def notifications():
    """The launcher's notification panel: {items: [{id, app, text, level, at, read}], unread, dnd}, newest first."""
    return _request("/api/assistant/notifications")


def dismiss_notification(nid):
    return _request("/api/assistant/notifications/dismiss", {"id": nid})


def clear_notifications(app=None):
    return _request("/api/assistant/notifications/clear", {"app": app} if app else {})


def do_not_disturb(on):
    return _request("/api/assistant/notifications/dnd", {"on": bool(on)})


def system_api(path, body=None, timeout=3.0):
    """OS Beroun's system API (docs/system-api.md) with the assistant as the owner: path is what follows /api/ (msgbox, sound/play, vibra/pulse, ...)."""
    return _request("/api/assistant/sys/" + path, body, timeout=timeout)


def installed():
    """Every app on the launcher (not the system's): [{id, name, ...}]."""
    return _request("/api/apps")


def current_app():
    """The app whose frontend is in the shared window, else the only app running; None when there is no telling."""
    run = running()
    shared = [r["app"] for r in run if r.get("window") == "shared"]
    if shared:
        return shared[0]
    ids = sorted({r["app"] for r in run})
    return ids[0] if len(ids) == 1 else None
