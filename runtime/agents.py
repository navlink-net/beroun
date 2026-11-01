# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The broker of the agents of the apps (docs/architecture.md): registry, tasks with states, journal.

Caroline gives an agent a task and gets a result; how the agent solves it is its own business. The broker only keeps the books:

    submitted -> working -> completed
                    |  ^      failed
            input-required    canceled

An agent whose host is a service (manifest agents[].host = {service, endpoint}) answers ONE endpoint with a JSON body {"op", "task_id", ...}:

    {"op": "start",  "task_id", "skill", "input", "chain_id"}   ->  {"state", "output"?, "question"?, "progress"?, "error"?}
    {"op": "poll",   "task_id"}                                  ->  the same
    {"op": "answer", "task_id", "answer"}                        ->  the same
    {"op": "cancel", "task_id"}                                  ->  the same (best effort: the task is canceled here whatever it says)

A question to the owner (state "input-required") may carry "choices" (1 to 4 short texts: the answer must be one of them, so Caroline can ask with a MessageBox whose
buttons are the choices) and "timeout_s" (how long the question may wait, 30 s to 1 h; the default is ASK_TIMEOUT_S). A question nobody answers in time fails the task
("the owner did not answer"): an agent never waits for ever. The answer to a question with choices that is none of them is refused.

The transport (shelld's: the service is started on demand, its port found, the endpoint called) is handed in, so this module needs no shell.
Hosts of the other kinds (an mcp server, the page) are not served yet: a task for them fails at once with that reason.
"""
import json, os, re, threading, time, urllib.error, urllib.request, uuid

STATES = ("submitted", "working", "input-required", "completed", "failed", "canceled")
TERMINAL = ("completed", "failed", "canceled")
JOURNAL_MAX = 256 * 1024     # bytes; past it the older half of the journal is dropped
KEEP_FINISHED = 100          # finished tasks kept (and listed)
ASK_TIMEOUT_S = 600          # how long a question to the owner may wait for its answer; an agent may ask for another time ("timeout_s"), within the limits below
ASK_MIN_S, ASK_MAX_S = 30, 3600


COST_FIELDS = ("cost", "price", "charged")   # the fields of an answer of the service that may carry what the call cost (to check against the real service)


def sw_allowed(patterns, command):
    """Whether a command is on the agent's `camerlengo` list: an exact command ('ai:stt') or a family ('ai:*')."""
    for p in patterns or []:
        if p == command or (p.endswith(":*") and command.startswith(p[:-1]) and len(command) > len(p) - 1):
            return True
    return False


class AgentError(Exception):
    """kind: "not_found", "bad_request", "forbidden" (not in the manifest) or "conflict" (the task is in a state that does not allow it)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class Unreachable(Exception):
    """The transport could not reach the agent (its service does not answer)."""


def _type_ok(t, v):
    t = t.rstrip("?")
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    return isinstance(v, (int, float)) and not isinstance(v, bool)    # number


def check_input(schema, given):
    """Errors of a task's input against the skill's {param: type}; a type ending in ? is optional, an unknown parameter is refused."""
    if not isinstance(given, dict):
        return ["input must be an object"]
    e = [f"unknown parameter '{k}'" for k in sorted(set(given) - set(schema))]
    for k, t in schema.items():
        if k not in given:
            if not t.endswith("?"):
                e.append(f"parameter '{k}' is required")
        elif not _type_ok(t, given[k]):
            e.append(f"parameter '{k}' must be {t.rstrip('?')}")
    return e


class Broker:
    def __init__(self, folder, transport, log=print, now=time.time, ask_timeout=ASK_TIMEOUT_S, a2a=None):
        """folder: where the journal and the tasks live; transport(agent, body) -> (http status, dict), raises Unreachable.
        a2a: the client for agents whose host is a remote A2A agent (runtime/a2a_client.py: .transport(agent, body) and .read_card(agent))."""
        self.folder, self.transport, self.log, self.now, self.ask_timeout, self.a2a = folder, transport, log, now, ask_timeout, a2a
        self.lock = threading.RLock()
        self.agents = {}      # "<app>/<id>" -> {key, app, id, description, skills, host, camerlengo, permissions}
        self.tasks = {}       # task id -> task (a dict; see _new)
        self.totals = {}      # "<app>/<id>" -> the agent's calls of Camerlengo: {calls, ok, refused, failed, spent, last} (totals.json; the owner's view, docs/architecture.md)
        os.makedirs(folder, exist_ok=True)
        try:
            self.totals = json.load(open(os.path.join(folder, "totals.json"), encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self._load()

    # ---- the registry: agents come from the manifests, nothing else registers them
    def register(self, app_id, app_name, agents):
        with self.lock:
            self.unregister(app_id)
            for a in agents or []:
                key = f"{app_id}/{a['id']}"
                d = dict(a, key=key, app=app_id, app_name=app_name, skills=list(a.get("skills") or []), declared_skills=bool(a.get("skills")))
                if "a2a" in (a.get("host") or {}):
                    d["remote"] = {"status": "unknown"}    # filled by discover(): the address from the card, whether it answers
                self.agents[key] = d

    def discover(self, agent_key):
        """Reads the Agent Card of an agent whose host is a remote A2A agent (stage 8): its address, and its skills unless the manifest declares them. True when the card was read."""
        with self.lock:
            a = self.agents.get(agent_key)
            if a is None or "a2a" not in (a.get("host") or {}) or self.a2a is None:
                return False
        try:
            card = self.a2a.read_card(a)
        except Exception as e:
            with self.lock:
                if agent_key in self.agents:
                    self.agents[agent_key]["remote"] = dict(self.agents[agent_key].get("remote") or {}, status="unreachable", error=str(e), at=self.now())
            return False
        with self.lock:
            a = self.agents.get(agent_key)
            if a is None:
                return False
            a["remote"] = {"status": "ok", "url": card["url"], "name": card["name"], "at": self.now()}
            if not a.get("declared_skills"):
                a["skills"] = card["skills"]
            if not a.get("description"):
                a["description"] = card["description"] or card["name"]
        return True

    def discover_due(self, max_age=600, retry=30):
        """The remote agents whose card is not known, could not be read (tried again after `retry` s) or is older than `max_age` s are read now. For a thread that runs it now and then."""
        with self.lock:
            due = [k for k, a in self.agents.items() if "a2a" in (a.get("host") or {}) and (lambda r: r.get("status") != "ok" and self.now() - r.get("at", 0) >= retry or self.now() - r.get("at", 0) >= max_age)(a.get("remote") or {})]
        return [k for k in due if self.discover(k)]

    def unregister(self, app_id):
        with self.lock:
            for k in [k for k, a in self.agents.items() if a["app"] == app_id]:
                del self.agents[k]

    def list_agents(self):
        with self.lock:
            return [dict(a) for a in sorted(self.agents.values(), key=lambda a: a["key"])]

    # ---- tasks
    def start(self, agent_key, skill, given, chain_id=None, source=None):
        with self.lock:
            a = self.agents.get(agent_key)
            if a is None:
                raise AgentError("not_found", f"no agent '{agent_key}'")
            unread = "a2a" in (a.get("host") or {}) and not a["skills"]
        if unread:    # a remote agent whose card has not been read yet: read it now
            self.discover(agent_key)
        with self.lock:
            a = self.agents.get(agent_key)
            if a is None:
                raise AgentError("not_found", f"no agent '{agent_key}'")
            sk = next((s for s in a["skills"] if s["name"] == skill), None)
            if sk is None:
                why = (a.get("remote") or {}).get("error")
                raise AgentError("not_found", f"agent '{agent_key}' has no skill '{skill}'" + (f" (its card cannot be read: {why})" if why and not a["skills"] else ""))
            errs = check_input(sk.get("input", {}), given if given is not None else {})
            if errs:
                raise AgentError("bad_request", "; ".join(errs))
            t = {"id": uuid.uuid4().hex, "agent": agent_key, "skill": skill, "input": given or {}, "chain_id": chain_id or uuid.uuid4().hex,
                 "long": bool(sk.get("long")), "output_kind": sk.get("output", "text"), "state": "submitted", "created": self.now(), "updated": self.now()}
            if source:    # who asked, when it is not Caroline (an A2A caller): the journal and the owner's view say so
                t["source"] = source
            self.tasks[t["id"]] = t
            self._journal("task", t)
            host = a.get("host") or {}
        if "service" not in host and not ("a2a" in host and self.a2a is not None):
            self._set(t["id"], {"state": "failed", "error": f"the host of this agent ({next(iter(host), '?')}) is not served yet"})
        else:
            self._ask(t["id"], {"op": "start", "task_id": t["id"], "skill": skill, "input": t["input"], "chain_id": t["chain_id"]})
        return self.get_local(t["id"])

    def get(self, task_id):
        """The task as it is now: a task that is still running is asked about first."""
        self.expire()
        with self.lock:
            t = self._task(task_id)
            live = t["state"] not in TERMINAL
        if live:
            self._ask(task_id, {"op": "poll", "task_id": task_id})
        return self.get_local(task_id)

    def get_local(self, task_id):
        with self.lock:
            return dict(self._task(task_id))

    def tasks_list(self, agent=None, state=None):
        self.expire()
        with self.lock:
            out = [dict(t) for t in self.tasks.values() if (not agent or t["agent"] == agent) and (not state or t["state"] == state)]
        return sorted(out, key=lambda t: t["created"], reverse=True)

    def answer(self, task_id, text):
        self.expire()
        with self.lock:
            t = self._task(task_id)
            if t["state"] != "input-required":
                raise AgentError("conflict", f"the task is {t['state']}, it asks for no answer")
            if t.get("choices") and text not in t["choices"]:
                raise AgentError("bad_request", "the answer must be one of: " + ", ".join(t["choices"]))
            self._journal("answer", t)
            self._set(task_id, {"state": "working", "question": None})
        self._ask(task_id, {"op": "answer", "task_id": task_id, "answer": text})
        return self.get_local(task_id)

    def cancel(self, task_id):
        with self.lock:
            t = self._task(task_id)
            if t["state"] in TERMINAL:
                raise AgentError("conflict", f"the task is already {t['state']}")
            self._set(task_id, {"state": "canceled", "question": None})
            self._journal("cancel", self.tasks[task_id])
        try:
            agent = self._agent_of(task_id)
            self._via(agent)(agent, self._with_remote(task_id, {"op": "cancel", "task_id": task_id}))   # best effort: it is canceled here whatever happens there
        except Exception:
            pass
        return self.get_local(task_id)

    def sw_call(self, agent_key, command, params, do_call, poll=0.1):
        """An agent's call of the owner's Camerlengo service (docs/architecture.md). do_call(command, params) makes it (the shell's: it adds the session and the key).
        Only what the agent's manifest lists in `camerlengo` goes through; the answer comes back unchanged, an error of do_call is raised as it is. Every call is journaled with
        the agent, the command, the outcome, the chain of the task that was running and the cost when the answer carries one (never the parameters or the result).
        A call whose task is canceled meanwhile is dropped: the agent gets a conflict, not the answer."""
        with self.lock:
            a = self.agents.get(agent_key)
            if a is None:
                raise AgentError("not_found", f"no agent '{agent_key}'")
            if not isinstance(command, str) or not sw_allowed(a.get("camerlengo"), command):
                self._journal_sw(agent_key, command, "refused", None)
                raise AgentError("forbidden", f"the agent '{agent_key}' may not call '{command}' (its manifest allows: {', '.join(a.get('camerlengo') or []) or 'nothing'})")
            running = [t["id"] for t in self.tasks.values() if t["agent"] == agent_key and t["state"] in ("submitted", "working")]
            chain = self.tasks[running[0]]["chain_id"] if len(running) == 1 else None
        box = {}

        def work():
            try:
                box["ok"] = do_call(command, params or {})
            except BaseException as e:    # handed back to the caller as it is
                box["err"] = e
        th = threading.Thread(target=work, daemon=True)
        th.start()

        def canceled():
            with self.lock:
                return bool(running) and all(self.tasks.get(i, {}).get("state") == "canceled" for i in running)
        while th.is_alive():
            th.join(poll)
            if th.is_alive() and canceled():
                self._journal_sw(agent_key, command, "dropped", chain)
                raise AgentError("conflict", "the task was canceled; the call is dropped")
        if canceled():
            self._journal_sw(agent_key, command, "dropped", chain)
            raise AgentError("conflict", "the task was canceled; the call is dropped")
        if "err" in box:
            self._journal_sw(agent_key, command, getattr(box["err"], "kind", "error"), chain)
            raise box["err"]
        ans = box["ok"]
        self._journal_sw(agent_key, command, "ok", chain, next((ans[k] for k in COST_FIELDS if isinstance(ans, dict) and k in ans), None))
        return ans

    def _journal_sw(self, agent_key, command, outcome, chain, cost=None):
        line = {"t": round(self.now(), 3), "event": "sw", "agent": agent_key, "command": str(command)[:80], "outcome": outcome, "chain_id": chain}
        if cost is not None:
            line["cost"] = cost
        self._write_journal(line)
        with self.lock:    # the totals outlive the journal, which drops its older half when it is full
            tot = self.totals.setdefault(agent_key, {"calls": 0, "ok": 0, "refused": 0, "failed": 0, "spent": 0.0, "last": 0})
            tot["calls"] += 1
            tot["ok" if outcome == "ok" else "refused" if outcome == "refused" else "failed"] += 1
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                tot["spent"] = round(tot["spent"] + cost, 6)
            tot["last"] = line["t"]
            try:
                tmp = os.path.join(self.folder, "totals.json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.totals, f)
                os.replace(tmp, os.path.join(self.folder, "totals.json"))
            except OSError as e:
                self.log(f"agents: the totals cannot be saved: {e}")

    def summary(self):
        """What the owner's read-only view (Settings, "Agents") shows: per agent what it declares, its tasks by state, its calls of Camerlengo and what they cost; and the sums."""
        with self.lock:
            out = []
            for a in sorted(self.agents.values(), key=lambda a: a["key"]):
                tasks = {}
                for t in self.tasks.values():
                    if t["agent"] == a["key"]:
                        tasks[t["state"]] = tasks.get(t["state"], 0) + 1
                tot = dict({"calls": 0, "ok": 0, "refused": 0, "failed": 0, "spent": 0.0, "last": 0}, **self.totals.get(a["key"], {}))
                out.append({"key": a["key"], "app": a["app"], "app_name": a.get("app_name"), "id": a["id"], "description": a.get("description", ""),
                            "skills": a.get("skills", []), "camerlengo": a.get("camerlengo", []), "permissions": a.get("permissions", []), "tasks": tasks,
                            "where": next(iter(a.get("host") or {}), "?"),    # service | mcp | page | a2a (a remote A2A agent: the task's input leaves the device)
                            "remote": ({"card": a["host"]["a2a"], "status": (a.get("remote") or {}).get("status", "unknown"), "error": (a.get("remote") or {}).get("error")} if "a2a" in (a.get("host") or {}) else None),
                            **tot})
            return {"agents": out, "calls": sum(x["calls"] for x in out), "spent": round(sum(x["spent"] for x in out), 6)}

    def expire(self):
        """A question that waited longer than its time fails the task (the agent is told to stop, best effort). Called whenever the tasks are looked at."""
        with self.lock:
            late = [t["id"] for t in self.tasks.values() if t["state"] == "input-required" and t.get("ask_expires") and t["ask_expires"] <= self.now()]
        for tid in late:
            with self.lock:
                t = self.tasks[tid]
                mins = max(1, round((t["ask_expires"] - t["asked"]) / 60))
                why = f"the owner did not answer in time ({mins} min); the question was: {t.get('question')}"
            self._set(tid, {"state": "failed", "error": why, "question": None})
            self._journal("ask-timeout", self.tasks[tid])
            try:
                agent = self._agent_of(tid)
                self._via(agent)(agent, self._with_remote(tid, {"op": "cancel", "task_id": tid}))
            except Exception:
                pass

    def journal(self, limit=100):
        try:
            with open(os.path.join(self.folder, "journal.jsonl"), encoding="utf-8") as f:
                lines = f.read().splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out

    # ---- inside
    def _task(self, task_id):
        t = self.tasks.get(task_id)
        if t is None:
            raise AgentError("not_found", f"no task '{task_id}'")
        return t

    def _via(self, agent):
        """The transport for the agent's host: a service (the shell's) or a remote A2A agent."""
        return self.a2a.transport if "a2a" in (agent.get("host") or {}) and self.a2a is not None else self.transport

    def _with_remote(self, task_id, body):
        with self.lock:
            rem = self.tasks.get(task_id, {}).get("remote")
        return dict(body, remote=rem) if rem else body

    def _agent_of(self, task_id):
        with self.lock:
            return self.agents.get(self.tasks[task_id]["agent"])

    def _ask(self, task_id, body):
        """Ask the agent and apply its answer. An agent that cannot be reached, or that does not know the task, fails the task with the reason."""
        agent = self._agent_of(task_id)
        if agent is None:
            return self._set(task_id, {"state": "failed", "error": "the agent is gone (its app was removed)"})
        try:
            status, ans = self._via(agent)(agent, self._with_remote(task_id, body))
        except Unreachable as e:
            return self._set(task_id, {"state": "failed", "error": f"the agent does not answer: {e}"})
        except Exception as e:
            return self._set(task_id, {"state": "failed", "error": f"the agent could not be asked: {e}"})
        if status >= 400 or not isinstance(ans, dict):
            return self._set(task_id, {"state": "failed", "error": (ans.get("error") if isinstance(ans, dict) else None) or f"the agent answered {status}"})
        state = ans.get("state")
        if state not in STATES[1:]:
            return self._set(task_id, {"state": "failed", "error": f"the agent answered with a state '{state}' that does not exist"})
        ch = {"state": state}
        for k in ("output", "question", "progress", "error", "remote"):    # remote: the id of the task at a remote A2A agent, kept so the next poll finds it
            if k in ans:
                ch[k] = ans[k]
        if state == "input-required":
            ch.update(self._ask_terms(task_id, ans))
        else:
            ch.setdefault("question", None)
            ch.update(choices=None, ask_expires=None)
        if state == "failed" and not ch.get("error"):
            ch["error"] = "the agent reported a failure"
        self._set(task_id, ch)

    def _ask_terms(self, task_id, ans):
        """The choices of a question and its deadline. The deadline starts when the question is first asked: polling the same question again does not extend it."""
        given = ans.get("choices")
        picked = [c for c in given if isinstance(c, str) and c][:4] if isinstance(given, list) else []
        out = {"choices": picked or None}
        with self.lock:
            t = self.tasks[task_id]
            if t["state"] == "input-required" and t.get("question") == ans.get("question") and t.get("ask_expires"):
                return out
        secs = ans.get("timeout_s")
        secs = min(ASK_MAX_S, max(ASK_MIN_S, float(secs))) if isinstance(secs, (int, float)) and not isinstance(secs, bool) else self.ask_timeout
        out.update(asked=self.now(), ask_expires=self.now() + secs)
        return out

    def _set(self, task_id, changes):
        """A change of the task. A finished task is final: whatever an agent says later is ignored."""
        with self.lock:
            t = self.tasks.get(task_id)
            if t is None or t["state"] in TERMINAL:
                return
            before = t["state"]
            t.update(changes)
            if t["state"] != "input-required":    # a question that is not asked has no choices and no deadline
                t["choices"] = t["ask_expires"] = None
            t["updated"] = self.now()
            if t["state"] != before or "progress" in changes:
                self._journal("state", t)
            self._save()

    def _journal(self, event, t):
        line = {"t": round(self.now(), 3), "event": event, "task": t["id"], "agent": t["agent"], "skill": t["skill"], "chain_id": t["chain_id"], "state": t["state"]}
        if t.get("source"):
            line["source"] = t["source"]
        if t.get("error"):
            line["error"] = t["error"]
        self._write_journal(line)

    def _write_journal(self, line):
        p = os.path.join(self.folder, "journal.jsonl")
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            if os.path.getsize(p) > JOURNAL_MAX:
                lines = open(p, encoding="utf-8").read().splitlines()
                with open(p, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines[len(lines) // 2:]) + "\n")
        except OSError as e:
            self.log(f"agents: the journal cannot be written: {e}")

    def _save(self):
        done = sorted((t for t in self.tasks.values() if t["state"] in TERMINAL), key=lambda t: t["updated"])   # stable: equal times keep the order of creation
        for t in done[:max(0, len(done) - KEEP_FINISHED)]:
            del self.tasks[t["id"]]
        try:
            tmp = os.path.join(self.folder, "tasks.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self.tasks.values()), f)
            os.replace(tmp, os.path.join(self.folder, "tasks.json"))
        except OSError as e:
            self.log(f"agents: the tasks cannot be saved: {e}")

    def _load(self):
        """After a restart of the shell a task survives only if its skill is long; the others cannot be told about and fail with that reason."""
        try:
            tasks = json.load(open(os.path.join(self.folder, "tasks.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return
        for t in tasks:
            if t.get("state") not in TERMINAL and not t.get("long"):
                t.update(state="failed", error="the shell restarted while the task ran", question=None, updated=self.now())
            self.tasks[t["id"]] = t


def http_transport(find, log=print):
    """The transport for service hosts. find(agent) -> the port of the agent's service, after starting the service if it is not running (raises Unreachable if there is none);
    the agent's host.endpoint is the path. Retries a little: a service just started needs a moment to listen."""
    def call(agent, body):
        host = agent["host"]
        port = find(agent)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/{host['endpoint']}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        last = "no answer"
        for _ in range(20):
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read() or b"{}")
                except ValueError:
                    return e.code, {}
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                last = str(getattr(e, "reason", e))
                time.sleep(0.25)
        raise Unreachable(last)
    return call
