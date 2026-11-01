# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""A2A inbound (docs/architecture.md): an agent of an app that the manifest exposes (`"a2a": {"expose": true}`) is reachable from outside as an A2A agent.

A thin layer on the broker of runtime/agents.py: the Agent Card is built from the manifest, the JSON-RPC calls of A2A (protocol 1.0: SendMessage, SendStreamingMessage, GetTask,
CancelTask, SubscribeToTask, ListTasks) are mapped on the broker's tasks. The task states are the same ones under A2A's names, the context id is the `chain_id`.

    GET  /a2a/<app>/<agent>/.well-known/agent-card.json      the card (public: it only says what the agent can do and how to ask)
    POST /a2a/<app>/<agent>/                                 JSON-RPC 2.0; `Authorization: Bearer <the device's key>`

The listener is its own (not the shell's port 8765, which is the device's own business): it binds INTELLICAT_A2A_BIND (127.0.0.1 by default) and is started only when some
agent is exposed. Authentication: one key the device makes (a2a.key in the agents folder); the card names the scheme. Standard library only, like the shell.
"""
import hmac, json, os, re, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agents

PROTOCOL = "1.0"
WAIT_S = 110                  # SendMessage waits for the task to finish or to ask something, at most this long
POLL_S = 0.25
STATE = {"submitted": "TASK_STATE_SUBMITTED", "working": "TASK_STATE_WORKING", "input-required": "TASK_STATE_INPUT_REQUIRED",
         "completed": "TASK_STATE_COMPLETED", "failed": "TASK_STATE_FAILED", "canceled": "TASK_STATE_CANCELED"}
# JSON-RPC error codes of A2A
PARSE, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
TASK_NOT_FOUND, TASK_NOT_CANCELABLE, PUSH_UNSUPPORTED, UNSUPPORTED = -32001, -32002, -32003, -32004


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def load_key(folder):
    """The device's key for A2A callers: made once, kept in a file only the owner of the shell can read."""
    p = os.path.join(folder, "a2a.key")
    try:
        k = open(p, encoding="utf-8").read().strip()
        if k:
            try:
                os.chmod(p, 0o600)    # a key put there by hand is protected the same way
            except OSError:
                pass
            return k
    except OSError:
        pass
    k = secrets.token_urlsafe(24)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k + "\n")
    return k


def exposed(agent):
    return bool((agent.get("a2a") or {}).get("expose"))


def offered_skills(agent):
    """The skills an outsider may use: those the agent declares, except the ones with an effect outside the app unless the manifest says `"confirm": true` in its `a2a` section."""
    allow = bool((agent.get("a2a") or {}).get("confirm"))
    return [s for s in agent.get("skills", []) if allow or not s.get("confirm")]


def card(agent, base):
    """The Agent Card of one exposed agent (`base`: the address the caller reached us at, http://host:port)."""
    a2a = agent.get("a2a") or {}
    skills = []
    for s in offered_skills(agent):
        params = ", ".join(f"{k.rstrip('?')}{'?' if v.endswith('?') else ''}: {v.rstrip('?')}" for k, v in (s.get("input") or {}).items())
        skills.append({"id": s["name"], "name": s["name"], "description": s.get("description", "") + (f" Input: {params}." if params else ""), "tags": list(a2a.get("tags", [])) or [agent["app"]],
                       "inputModes": ["text/plain", "application/json"], "outputModes": ["application/json" if s.get("output") == "json" else "text/plain"]})
    return {"name": f"{agent.get('app_name') or agent['app']} / {agent['id']}", "description": agent.get("description", ""), "version": "1.0.0",
            "supportedInterfaces": [{"url": f"{base}/a2a/{agent['key']}/", "protocolBinding": "JSONRPC", "protocolVersion": PROTOCOL}],
            "provider": {"organization": "OS Beroun", "url": base},
            "capabilities": {"streaming": True, "pushNotifications": False, "extendedAgentCard": False},
            "securitySchemes": {"deviceKey": {"httpAuthSecurityScheme": {"scheme": "Bearer", "description": "The key of the device (the file a2a.key of its agents folder)"}}},
            "securityRequirements": [{"schemes": {"deviceKey": {"list": []}}}],
            "defaultInputModes": ["text/plain", "application/json"], "defaultOutputModes": ["text/plain", "application/json"], "skills": skills}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _msg(task, text, data=None):
    parts = [{"text": text}] if text else []
    if data is not None:
        parts.append({"data": data})
    return {"messageId": f"{task['id']}-{int(task['updated'] * 1000)}", "taskId": task["id"], "contextId": task["chain_id"], "role": "ROLE_AGENT", "parts": parts}


def status(task):
    """The task's status in A2A's words: a question carries its text (and the choices as data), a failure its reason, progress its text."""
    st, m = task["state"], None
    if st == "input-required":
        m = _msg(task, task.get("question") or "", {"choices": task["choices"]} if task.get("choices") else None)
    elif st == "failed":
        m = _msg(task, task.get("error") or "the agent failed")
    elif st == "working" and task.get("progress"):
        m = _msg(task, str(task["progress"]))
    out = {"state": STATE[st], "timestamp": _iso(task["updated"])}
    if m:
        out["message"] = m
    return out


def artifact(task):
    out = task.get("output")
    if out is None:
        return None
    part = {"data": out} if task.get("output_kind") == "json" or not isinstance(out, str) else {"text": out}
    return {"artifactId": f"{task['id']}-result", "name": "result", "parts": [part]}


def task_json(task):
    t = {"id": task["id"], "contextId": task["chain_id"], "status": status(task)}
    if task["state"] == "completed" and artifact(task):
        t["artifacts"] = [artifact(task)]
    return t


def resolve(agent, message):
    """Which skill, with which input, an A2A message asks for. A data part {"skill", "input"} (or the message's metadata) names it; a text goes to the one string parameter of the skill;
    an agent with one skill needs no name."""
    skills = {s["name"]: s for s in offered_skills(agent)}
    parts = message.get("parts") or []
    if not isinstance(parts, list) or not parts:
        raise RpcError(INVALID_PARAMS, "the message has no parts")
    spec = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    name, given = spec.get("skill"), spec.get("input")
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("data"), dict):
            name = p["data"].get("skill", name)
            given = p["data"].get("input", given)
    text = "\n".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()
    if name is None:
        if len(skills) != 1:
            raise RpcError(INVALID_PARAMS, "name the skill: a data part {\"skill\": ..., \"input\": {...}}; this agent offers: " + ", ".join(skills))
        name = next(iter(skills))
    sk = skills.get(name)
    if sk is None:
        raise RpcError(INVALID_PARAMS, f"this agent offers no skill '{name}' (it offers: {', '.join(skills)})")
    if given is None:
        given = {}
        schema = sk.get("input") or {}
        strs = [k for k, v in schema.items() if v.rstrip("?") == "string"]
        req = [k for k in strs if not schema[k].endswith("?")]
        target = strs[0] if len(strs) == 1 else req[0] if len(req) == 1 else None
        if text and target:
            given[target] = text
    return name, given


class Service:
    """The JSON-RPC methods of one broker; the handler below only does HTTP."""

    def __init__(self, broker, wait_s=WAIT_S, poll_s=POLL_S):
        self.broker, self.wait_s, self.poll_s = broker, wait_s, poll_s

    def agent(self, key):
        a = next((x for x in self.broker.list_agents() if x["key"] == key), None)
        if a is None or not exposed(a):
            return None
        return a

    def _task(self, agent, tid):
        if not isinstance(tid, str):
            raise RpcError(INVALID_PARAMS, "id is required")
        try:
            t = self.broker.get_local(tid)
        except agents.AgentError:
            raise RpcError(TASK_NOT_FOUND, f"no task '{tid}'")
        if t["agent"] != agent["key"]:
            raise RpcError(TASK_NOT_FOUND, f"no task '{tid}'")
        return t

    def _settled(self, tid):
        """Waits until the task has finished or asks something (or the time is up)."""
        end = time.time() + self.wait_s
        while True:
            t = self.broker.get(tid)
            if t["state"] not in ("submitted", "working") or time.time() >= end:
                return t
            time.sleep(self.poll_s)

    def _send(self, agent, params):
        message = params.get("message")
        if not isinstance(message, dict) or message.get("role") not in ("ROLE_USER", None, "ROLE_UNSPECIFIED"):
            raise RpcError(INVALID_PARAMS, "message (from the user) is required")
        try:
            if message.get("taskId"):    # an answer to the question of a task
                t = self._task(agent, message["taskId"])
                if t["state"] != "input-required":
                    raise RpcError(UNSUPPORTED, f"the task is {t['state']}, it asks for no answer")
                text = "\n".join(p["text"] for p in message.get("parts") or [] if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()
                self.broker.answer(t["id"], text)
                return t["id"]
            skill, given = resolve(agent, message)
            ctx = message.get("contextId")
            return self.broker.start(agent["key"], skill, given, str(ctx)[:64] if ctx else None, source="a2a")["id"]
        except agents.AgentError as e:
            raise RpcError(INVALID_PARAMS if e.kind in ("bad_request", "forbidden") else TASK_NOT_FOUND if e.kind == "not_found" else UNSUPPORTED, str(e))

    def call(self, agent, method, params):
        """(result) of a call that answers at once; raises RpcError."""
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        if method == "SendMessage":
            tid = self._send(agent, params)
            cfg = params.get("configuration") if isinstance(params.get("configuration"), dict) else {}
            t = self.broker.get_local(tid) if cfg.get("returnImmediately") else self._settled(tid)
            return {"task": task_json(t)}
        if method == "GetTask":
            return task_json(self.broker.get(self._task(agent, params.get("id"))["id"]))
        if method == "CancelTask":
            t = self._task(agent, params.get("id"))
            try:
                return task_json(self.broker.cancel(t["id"]))
            except agents.AgentError:
                raise RpcError(TASK_NOT_CANCELABLE, f"the task is already {t['state']}")
        if method == "ListTasks":
            mine = [t for t in self.broker.tasks_list(agent["key"])]
            return {"tasks": [task_json(t) for t in mine], "nextPageToken": "", "pageSize": len(mine), "totalSize": len(mine)}
        if method in ("CreateTaskPushNotificationConfig", "GetTaskPushNotificationConfig", "ListTaskPushNotificationConfigs", "DeleteTaskPushNotificationConfig"):
            raise RpcError(PUSH_UNSUPPORTED, "push notifications are not supported")
        if method == "GetExtendedAgentCard":
            raise RpcError(UNSUPPORTED, "no extended card")
        raise RpcError(METHOD_NOT_FOUND, f"no method '{method}'")

    def stream(self, agent, method, params):
        """The events of SendStreamingMessage / SubscribeToTask: the task first, then every change; it ends when the task has finished or asks something."""
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        if method == "SendStreamingMessage":
            tid = self._send(agent, params)
        else:
            tid = self._task(agent, params.get("id"))["id"]
            if self.broker.get_local(tid)["state"] in agents.TERMINAL:
                raise RpcError(UNSUPPORTED, "the task has finished: nothing to subscribe to")
        t = self.broker.get_local(tid)
        yield {"task": task_json(t)}
        if t["state"] == "input-required":    # waiting for the caller: the stream has said all there is
            return
        last = (t["state"], t.get("progress"), t.get("question"))
        end = time.time() + 3600
        while time.time() < end and t["state"] not in agents.TERMINAL:
            time.sleep(self.poll_s)
            t = self.broker.get(tid)
            now = (t["state"], t.get("progress"), t.get("question"))
            if now != last:
                last = now
                if t["state"] == "completed" and artifact(t):
                    yield {"artifactUpdate": {"taskId": tid, "contextId": t["chain_id"], "artifact": artifact(t), "lastChunk": True}}
                yield {"statusUpdate": {"taskId": tid, "contextId": t["chain_id"], "status": status(t)}}
                if t["state"] == "input-required":
                    return


def make_handler(service, key):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, obj, code=200, headers=None):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _route(self):
            m = re.fullmatch(r"/a2a/([^/]+/[^/]+)(/.*)?", self.path.split("?")[0])
            if not m:
                return None, None
            return service.agent(m.group(1)), m.group(2) or "/"

        def _base(self):
            return f"http://{self.headers.get('Host') or 'localhost'}"

        def do_GET(self):
            agent, rest = self._route()
            if agent is None or rest != "/.well-known/agent-card.json":
                return self._send({"error": "not found"}, 404)
            self._send(card(agent, self._base()))

        def do_POST(self):
            agent, rest = self._route()
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if agent is None or rest != "/":
                return self._send({"error": "not found"}, 404)
            auth = self.headers.get("Authorization", "")
            if not (auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), key)):
                return self._send({"error": "the key of the device is needed (Authorization: Bearer ...)"}, 401, {"WWW-Authenticate": "Bearer"})
            rid = None
            try:
                try:
                    req = json.loads(raw or b"{}")
                except ValueError:
                    raise RpcError(PARSE, "not JSON")
                if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
                    raise RpcError(INVALID_REQUEST, "not a JSON-RPC 2.0 request")
                rid, method, params = req.get("id"), req["method"], req.get("params") or {}
                if method in ("SendStreamingMessage", "SubscribeToTask"):
                    events = service.stream(agent, method, params)
                    first = next(events)    # the refusals come before the stream starts
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    for ev in [first, *events]:
                        self.wfile.write(b"data: " + json.dumps({"jsonrpc": "2.0", "id": rid, "result": ev}).encode() + b"\n\n")
                        self.wfile.flush()
                    return
                self._send({"jsonrpc": "2.0", "id": rid, "result": service.call(agent, method, params)})
            except RpcError as e:
                self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": str(e)}})
            except (BrokenPipeError, ConnectionError):
                pass
            except Exception as e:    # never take the listener down
                self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": INTERNAL, "message": f"{type(e).__name__}: {e}"}})
    return H


def serve(broker, host, port, folder, log=print):
    """Starts the listener in a thread; returns (server, key)."""
    key = load_key(folder)
    srv = ThreadingHTTPServer((host, port), make_handler(Service(broker), key))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"a2a: listening on {host}:{port}")
    return srv, key
