#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""minecraftd: the service of the app `minecraft`. It talks to the cloud Minecraft's orchestrator with the device's own
SquirrelWisdom session (the shell gives it to this service only: the manifest's permission account.session), and keeps the stream address for
the player (the hidden app `minecraft-player`).

    minecraftd.py <port>

The page reaches it through the shell (/api/app/minecraft/<path>, the manifest's endpoints); the player, on the same machine, directly.

    GET  status    {state, node, balance?, reason?}  state: idle | starting | ready | playing | ended | error
    POST start     starts the node (or takes the running one) and, once it runs, keeps its stream: state ready
    POST stop      stops the node
    GET  stream    (the player) {url, size, output}
    POST ended     (the player) the game was closed: the node is stopped at once
    GET  balance   the wallet

The orchestrator is $MC_ORCHESTRATOR (default https://access.minecraft.squirrelwisdom.com/api/); a test points it at a stub.
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORCH = os.environ.get("MC_ORCHESTRATOR", "https://access.minecraft.squirrelwisdom.com/api/")
SHELL = f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}"
SID = os.environ.get("INTELLICAT_SERVICE_ID", "minecraftd")
START_TIMEOUT_S = 600     # a cold node: up to ~5 minutes (sw_minecraft COLD_START_PLAN.md), with room
POLL_S = float(os.environ.get("MC_POLL_S") or 5)   # how often a provisioning node is asked again (a test makes it short)

lock = threading.Lock()
st = {"state": "idle", "node": "none", "reason": "", "url": "", "size": "640x480", "output": "", "since": time.time()}
_session = {"value": None}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def set_state(state, **kw):
    with lock:
        st.update(kw, state=state, since=time.time())
    log("state", state, kw.get("reason", ""))


def session(fresh=False):
    if _session["value"] and not fresh:
        return _session["value"]
    req = urllib.request.Request(SHELL + "/api/account/session" + ("?fresh=1" if fresh else ""), headers={"X-IntelliCat-Service-Id": SID})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            _session["value"] = json.loads(r.read())["session"]
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
        raise RuntimeError({"auth": "Sign in to the console first", "network": "No network"}.get(body.get("kind"), body.get("error") or "No account"))
    return _session["value"]


def orch(action, **body):
    """One call of the orchestrator; a session it calls expired is minted again once."""
    for attempt in (0, 1):
        data = json.dumps(dict(body, session=session(fresh=bool(attempt)))).encode()
        req = urllib.request.Request(urllib.parse.urljoin(ORCH, action), data, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())
        except urllib.error.URLError as e:
            raise RuntimeError(f"The game server cannot be reached ({getattr(e, 'reason', e)})")
        if out.get("ok") is False and "session" in str(out.get("reason", "")) and attempt == 0:
            continue
        return out
    return out


def start_worker():
    """Starts the node and waits for its stream: provisioning (a cold node takes minutes), then a start again gives the stream path."""
    deadline = time.time() + START_TIMEOUT_S
    try:
        while time.time() < deadline:
            r = orch("start")
            if not r.get("ok"):
                why = str(r.get("reason") or "")
                return set_state("error", reason="The wallet is below zero: top it up first" if why.startswith("negative balance")
                                 else "The game is open on another device" if why == "device already connected"
                                 else why or "The game server refused to start")
            if r.get("status") == "running" and r.get("streamPath"):
                url = urllib.parse.urljoin(ORCH, r["streamPath"])
                return set_state("ready", node="running", url=url, reason="")
            if r.get("status") == "running":    # the orchestrator in its conference-only mode: no game node at all
                return set_state("error", reason="The game server offers no game now")
            with lock:
                st["node"] = r.get("status") or "provisioning"
            time.sleep(POLL_S)
        set_state("error", reason="The game server did not start in time")
    except Exception as e:
        set_state("error", reason=str(e))


def stop_node():
    try:
        r = orch("stop")
        with lock:
            st["node"] = "none" if r.get("ok") else st["node"]
        log("stop:", r)
    except Exception as e:
        log("stop failed:", e)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/").rsplit("/", 1)[-1]
        if path == "status":
            with lock:
                out = {k: st[k] for k in ("state", "node", "reason")}
            return self.reply(out)
        if path == "stream":
            with lock:
                if st["state"] not in ("ready", "playing") or not st["url"]:
                    return self.reply({"error": "no stream"}, 409)
                st["state"] = "playing"
                return self.reply({k: st[k] for k in ("url", "size", "output")})
        if path == "balance":
            try:
                r = orch("balance")
                return self.reply({"balance": r.get("balance"), "currency": r.get("billingCurrency")} if r.get("ok") else {"error": r.get("reason")})
            except Exception as e:
                return self.reply({"error": str(e)})
        if path == "node":   # what the orchestrator says now (the page asks it on opening: a node that still runs can be continued)
            try:
                r = orch("status")
                return self.reply({"node": r.get("status") or "none"})
            except Exception as e:
                return self.reply({"node": "unknown", "error": str(e)})
        self.reply({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/").rsplit("/", 1)[-1]
        b = self.body()
        if path == "start":
            with lock:
                busy = st["state"] in ("starting", "ready", "playing")
                if not busy:
                    st.update(state="starting", node="provisioning", reason="", url="", since=time.time())
                    if b.get("size"):
                        st["size"] = str(b["size"])
                    st["output"] = str(b.get("output") or "")
            if not busy:
                threading.Thread(target=start_worker, daemon=True).start()
            return self.reply({"ok": True})
        if path == "stop":
            set_state("idle")
            threading.Thread(target=stop_node, daemon=True).start()
            return self.reply({"ok": True})
        if path == "ended":
            code = int(b.get("code") or 0)
            set_state("ended", reason={0: "", 2: "The stream could not be opened", 3: "The game server ended the session", 4: "The connection to the game server was lost"}.get(code, f"The player stopped ({code})"))
            threading.Thread(target=stop_node, daemon=True).start()
            return self.reply({"ok": True})
        self.reply({"error": "not found"}, 404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("INTELLICAT_SERVICE_PORT", "48803"))
    log("minecraftd on", port, "orchestrator", ORCH)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
