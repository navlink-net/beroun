# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""A2A outbound (docs/architecture.md): an agent of an app whose host is `{"a2a": "<card url>"}` is an external A2A agent that Caroline commands like any other.

The broker (runtime/agents.py) keeps the tasks and speaks its four operations to every host; this module is the transport for the hosts of that kind:

    start   -> SendMessage (returnImmediately), the skill and its input as a data part and as text
    poll    -> GetTask
    answer  -> SendMessage with the taskId of the remote task (the answer to its question)
    cancel  -> CancelTask

and it reads the Agent Card (A2A 1.0, JSON-RPC binding) to learn the address of the agent and its skills. What the remote agent answers is turned back into the broker's words: the state, the
result (the artifacts' text, or the data of a single data part), the question (and its choices), the progress, the failure. The key for an agent that wants one is named in the manifest
(`"key": "<name>"`) and lives in a file of the device (a2a-keys.json in the agents folder: {name: token}); a manifest never holds a secret. Standard library only.
"""
import json, os, urllib.error, urllib.request, uuid
from urllib.parse import urlparse

import agents

TIMEOUT_S = 30
WELL_KNOWN = "/.well-known/agent-card.json"
STATE_OF = {"TASK_STATE_SUBMITTED": "working", "TASK_STATE_WORKING": "working", "TASK_STATE_UNSPECIFIED": "working", "TASK_STATE_INPUT_REQUIRED": "input-required",
            "TASK_STATE_COMPLETED": "completed", "TASK_STATE_FAILED": "failed", "TASK_STATE_REJECTED": "failed", "TASK_STATE_CANCELED": "canceled", "TASK_STATE_AUTH_REQUIRED": "failed"}


class CardError(Exception):
    """The Agent Card could not be read or is not one we speak (A2A 1.0 over JSON-RPC)."""


def _text(parts):
    return "\n".join(p["text"] for p in parts or [] if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()


def result_of(task):
    """A task of A2A as the broker's answer {"state", "output"?, "question"?, "choices"?, "progress"?, "error"?}."""
    st = task.get("status") or {}
    state = STATE_OF.get(st.get("state"), "working")
    said = _text((st.get("message") or {}).get("parts"))
    out = {"state": state}
    if state == "input-required":
        out["question"] = said or "(the agent asks for input)"
        data = [p["data"] for p in (st.get("message") or {}).get("parts", []) if isinstance(p, dict) and isinstance(p.get("data"), dict) and isinstance(p["data"].get("choices"), list)]
        if data:
            out["choices"] = data[0]["choices"]
    elif state == "completed":
        texts = [_text(a.get("parts")) for a in task.get("artifacts") or []]
        datas = [p["data"] for a in task.get("artifacts") or [] for p in a.get("parts") or [] if isinstance(p, dict) and "data" in p]
        out["output"] = "\n".join(t for t in texts if t) if any(texts) else datas[0] if len(datas) == 1 else datas if datas else said
    elif state == "failed":
        why = {"TASK_STATE_REJECTED": "the remote agent rejected the task", "TASK_STATE_AUTH_REQUIRED": "the remote agent needs authentication"}.get(st.get("state"), "the remote agent failed")
        out["error"] = said or why
    elif said:
        out["progress"] = said
    return out


class Client:
    def __init__(self, keys_path=None, timeout=TIMEOUT_S):
        self.keys_path, self.timeout = keys_path, timeout

    # ---- credentials: named in the manifest, kept in a file of the device
    def key(self, name):
        if not name or not self.keys_path:
            return None
        try:
            return json.load(open(self.keys_path, encoding="utf-8")).get(name)
        except (OSError, ValueError):
            return None

    def _headers(self, agent):
        h = {"Content-Type": "application/json", "Accept": "application/json", "A2A-Version": "1.0"}
        k = self.key((agent.get("host") or {}).get("key"))
        if k:
            h["Authorization"] = "Bearer " + k
        return h

    def _open(self, req):
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, OSError) as e:
            raise agents.Unreachable(str(getattr(e, "reason", e)))
        except ValueError as e:
            raise agents.Unreachable(f"not JSON: {e}")

    # ---- the card
    def read_card(self, agent):
        """The card of a remote agent: {"url": the JSON-RPC address, "name", "description", "skills": [{name, description, input, long}]}; CardError when it is not one we speak."""
        url = agent["host"]["a2a"]
        if not url.rstrip("/").endswith("agent-card.json"):
            url = url.rstrip("/") + WELL_KNOWN
        try:
            status, card = self._open(urllib.request.Request(url, headers=self._headers(agent)))
        except agents.Unreachable as e:
            raise CardError(f"the card cannot be read: {e}")
        if status != 200 or not isinstance(card, dict):
            raise CardError(f"the card answered {status}")
        ifs = [i for i in card.get("supportedInterfaces") or [] if isinstance(i, dict) and i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion", "")).startswith("1.")]
        if not ifs:
            raise CardError("the card offers no JSON-RPC interface of A2A 1.0 (older agents are not spoken to)")
        rpc = ifs[0].get("url")
        if not isinstance(rpc, str) or urlparse(rpc).scheme not in ("http", "https"):
            raise CardError("the card's interface has no address")
        skills = [{"name": str(s.get("id") or s.get("name")), "description": str(s.get("description") or s.get("name") or ""), "input": {"text": "string"}, "long": True}
                  for s in card.get("skills") or [] if isinstance(s, dict) and (s.get("id") or s.get("name"))]
        return {"url": rpc, "name": str(card.get("name") or ""), "description": str(card.get("description") or ""), "skills": skills}

    # ---- the broker's operations
    def _rpc(self, agent, method, params):
        remote = agent.get("remote") or {}
        if not remote.get("url"):
            try:
                remote = dict(remote, **self.read_card(agent))
            except CardError as e:
                raise agents.Unreachable(str(e))
            agent["remote"] = remote
        req = urllib.request.Request(remote["url"], json.dumps({"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}).encode(), headers=self._headers(agent))
        status, body = self._open(req)
        if status in (401, 403):
            return 400, {"error": "the remote agent refused the key (name one in the manifest's host.key and put it in a2a-keys.json)"}
        if status >= 400 or not isinstance(body, dict):
            return 400, {"error": f"the remote agent answered {status}"}
        if body.get("error"):
            e = body["error"] if isinstance(body["error"], dict) else {}
            return (404 if e.get("code") == -32001 else 400), {"error": f"{e.get('message') or 'the remote agent refused'} ({e.get('code')})"}
        return 200, body.get("result") or {}

    def _answered(self, status, res, ctx=None):
        if status != 200:
            return status, res
        task = res.get("task") if isinstance(res, dict) and "task" in res else res if isinstance(res, dict) and "status" in res else None
        if task is not None:
            return 200, dict(result_of(task), remote={"id": task.get("id"), "context": task.get("contextId") or ctx})
        msg = res.get("message") if isinstance(res, dict) else None    # the agent answered at once, without a task
        if isinstance(msg, dict):
            return 200, {"state": "completed", "output": _text(msg.get("parts"))}
        return 400, {"error": "the remote agent's answer has neither a task nor a message"}

    def transport(self, agent, body):
        """The broker's transport for hosts of the kind a2a: (http status, answer) or Unreachable."""
        op, rem = body["op"], body.get("remote") or {}
        if op == "start":
            given = body.get("input") or {}
            text = given.get("text") if isinstance(given.get("text"), str) else json.dumps(given, ensure_ascii=False) if given else ""
            # the input as structured data only when the manifest declared the skill (its parameters are known); a skill read from a card takes free text
            spec = {"skill": body["skill"], "input": given} if agent.get("declared_skills") else {"skill": body["skill"]}
            parts = ([{"text": text}] if text else []) + [{"data": spec}]
            msg = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "contextId": body["chain_id"], "parts": parts}
            return self._answered(*self._rpc(agent, "SendMessage", {"message": msg, "configuration": {"returnImmediately": True}}), ctx=body["chain_id"])
        if not rem.get("id"):
            return 404, {"error": "the remote task is not known (the shell restarted before it was started?)"}
        if op == "poll":
            return self._answered(*self._rpc(agent, "GetTask", {"id": rem["id"]}), ctx=rem.get("context"))
        if op == "answer":
            msg = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "taskId": rem["id"], "contextId": rem.get("context"), "parts": [{"text": str(body.get("answer", ""))}]}
            return self._answered(*self._rpc(agent, "SendMessage", {"message": msg, "configuration": {"returnImmediately": True}}), ctx=rem.get("context"))
        if op == "cancel":
            return self._answered(*self._rpc(agent, "CancelTask", {"id": rem["id"]}), ctx=rem.get("context"))
        return 400, {"error": f"unknown operation {op}"}
