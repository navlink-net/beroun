# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Ratatosk on IntelliCat, stage 2: the audio calls (docs/usage.md; the references are CallRepository.kt of the Android client and apps/chat/main.py of ReForce).

The controller inside `shelld`: it speaks the call protocol of the server (a call is the variable `sw_calls/<group>` plus the commands `chat:call`, `chat:call_accept`,
`chat:call_decline`, `chat:call_cancel`, `chat:call_end`, and the poll `chat:call_state` whose answer is the call record of the server's state machine: ringing -> active -> ended,
the recipients pending / accepted / declined / left / missed, a ring timeout), asks the token service for a LiveKit token, and runs the media in a process of its own
(runtime/ratatosk_call_engine.py). The page of the app only shows `status()` and presses the buttons.

    GET  call         -> status        POST call-start {id, mode: audio|video} | call-accept | call-decline | call-hangup | call-mute {on} | call-camera {on} | call-missed-clear
    GET  call-video | call-preview  -> the pictures of the other side / of the own camera as a motion JPEG (served by shelld through serve_frames; video calls, stage 3)

States: idle, dialing (we called, it rings), incoming (somebody calls), connecting (we answered), active (the other one is in the room), ended (shown for a few seconds with the reason).
Rules kept from the phone (PDD.md section 5): a call is never ended on one bad poll (two misses in a row; a state "ended" is a fact, not a miss), the microphone and the engine are
released when the call ends, only one call at a time (a second one is refused as busy, the first is not touched).

Secrets: the token service wants a shared secret (decision D3: it is not shipped, the plan moves it to a server command later). It is read from `$INTELLICAT_RATATOSK_TOKEN_SECRET` or
`<data>/ratatosk/call.json` (`{"token_url": ..., "token_secret": ...}`), never from the repository.
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request, uuid

import androidhost

DEFAULT_TOKEN_URL = "https://vconf.partners.solutions"
POLL_S = 2.0                 # the poll of an own call (the phone: 2 s)
WATCH_S = 3.0                # the watcher of incoming calls (decision 6: 3 s while awake)
WATCH_ASLEEP_S = 5.0         # while the console sleeps (the screen is off, the CPU runs): a look every 5 s, so a ring is noticed within about 10 s and the radio works less
WATCH_BACKOFF_MAX_S = 30.0   # no network or a server that does not answer: the watcher doubles its pause (3, 6, 12, 24, 30 s) instead of hammering; it is 3 s again at the first answer
RING_EVERY_S = 3.0           # a ringing call repeats its sound and vibration this often (the first one comes with the notification) ...
RING_MAX_S = 60.0            # ... for at most the ring timeout of the server
RECONNECT_GIVE_UP_S = 45.0   # the media connection has been trying to come back this long: the call is over (a loss of 20 s is survived, plan C6)
MISSES_TO_END = 2            # the phone never ends a call on one bad poll
SHOW_ENDED_S = 4.0
STALE_CALL_S = 120           # a sw_calls entry older than this is not a ringing call
EMPTY_GRACE_S = 4.0          # nobody left in the room for this long: the call is over (the phone's grace, so a flicker of the participant list does not end it)
HERE = os.path.dirname(os.path.abspath(__file__))


def group_room_id(name_or_id):
    """The room of a chat: the first 4 bytes of SHA-256 of its id, base 36, upper case, padded to 8 (CallRepository.groupRoomId)."""
    n = int.from_bytes(hashlib.sha256(name_or_id.strip().encode()).digest()[:4], "big")
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + s
    return (s or "0").upper().rjust(8, "0")


class CallError(Exception):
    def __init__(self, message, code="error"):
        super().__init__(message)
        self.code = code


class Calls:
    def __init__(self, rt, on_incoming=None, log=print, on_missed=None, on_ring=None, can_clear=None):
        self.rt, self.on_incoming, self.on_missed, self.on_ring, self.log = rt, on_incoming, on_missed, on_ring, log
        self.can_clear = can_clear       # None: anybody may clear (the tests); shelld: only after the console was touched since the missed call
        self.lock = threading.RLock()
        self.s = self._idle()
        self.engine = None
        self._thread = None
        self._token_cfg = None
        self.seen_calls = set()          # incoming calls already announced (one call, one ring)
        self._frames = None              # the private folder of the pictures of the call in progress (video calls)
        self._remotes = {}               # identity -> {"level", "pitch", "tile"} of everybody else who is in the room now (a group call has several)
        # A call belongs to this background service, not to the app's page (owner, 2026-10-28): leaving the app (MODE) does NOT end it; the way to end it is to open the app again
        # (it shows the call that is going on) and hang up.
        self.missed = self._load_missed()

    @staticmethod
    def _idle():
        return {"state": "idle", "call_id": None, "group_id": None, "name": "", "mode": "audio", "from": "", "incoming": False, "since": 0, "connected_at": None,
                "reason": None, "muted": False, "remote": None, "level": 0, "pitch": 0, "ended_at": None,
                "remote_video": False, "camera": "none", "fps": 0, "ringback": False, "remotes": [], "max_remotes": 0, "reconnecting": False}

    # ---- missed calls: kept across restarts, shown as the badge of the tile and a line in the app -------------------------
    def _missed_path(self):
        return os.path.join(self.rt.dir, "missed.json")

    def _load_missed(self):
        try:
            with open(self._missed_path(), encoding="utf-8") as f:
                m = json.load(f)
            return m if isinstance(m, list) else []
        except (OSError, ValueError):
            return []

    def _save_missed(self):
        try:
            with open(self._missed_path(), "w", encoding="utf-8") as f:
                json.dump(self.missed, f)
        except OSError as e:
            self.log(f"ratatosk call: the missed calls were not saved: {e}")

    def _add_missed(self, s):
        """A call that rang here and was not answered or declined here: kept, announced (notification and badge are shelld's, through on_missed)."""
        with self.lock:
            if any(m.get("call_id") == s["call_id"] for m in self.missed):
                return
            rec = {"call_id": s["call_id"], "from": s["from"], "name": s["name"], "group_id": s["group_id"], "mode": s["mode"], "ts": int(time.time())}
            self.missed = (self.missed + [rec])[-20:]
            self._save_missed()
            n = len(self.missed)
        if self.on_missed:
            try:
                self.on_missed(rec, n)
            except Exception as e:
                self.log(f"ratatosk call: the missed-call notice failed: {e}")

    def clear_missed(self):
        """The owner has seen them. `can_clear(time of the newest)` (shelld: the screen or a button was touched since) says whether somebody really looked: a page that came up by itself
        for a call and went back to sleep with the console has not shown anything to anybody, and the badge stays until the console is touched."""
        with self.lock:
            if self.can_clear and self.missed and not self.can_clear(max(int(m.get("ts") or 0) for m in self.missed)):
                return
            self.missed = []
            self._save_missed()
        if self.on_missed:
            try:
                self.on_missed(None, 0)
            except Exception:
                pass

    # ---- the configuration of the media side (nothing in the repository) ------------------------------------------------
    def token_config(self):
        url, secret = os.environ.get("INTELLICAT_RATATOSK_TOKEN_URL"), os.environ.get("INTELLICAT_RATATOSK_TOKEN_SECRET")
        if not secret:
            try:
                with open(os.path.join(self.rt.dir, "call.json"), encoding="utf-8") as f:
                    c = json.load(f)
                url, secret = url or c.get("token_url"), c.get("token_secret")
            except (OSError, ValueError):
                pass
        return (url or DEFAULT_TOKEN_URL).rstrip("/"), secret

    def engine_python(self):
        return os.environ.get("INTELLICAT_RATATOSK_PYTHON") or os.path.expanduser("~/.intellicat/ratatosk-spike/venv/bin/python")

    def configured(self):
        if androidhost.ENABLED:     # the media runs in a WebView (ratatosk_webengine.py): no Python engine to find
            return bool(self.token_config()[1])
        py = self.engine_python()
        return bool(self.token_config()[1]) and os.path.exists(py)

    def fetch_token(self, room, identity):
        url, secret = self.token_config()
        if not secret:
            raise CallError("the token service of the calls is not configured on this device (docs/usage.md)", "not-configured")
        req = urllib.request.Request(f"{url}/livekit/token?room={urllib.parse.quote(room)}&identity={urllib.parse.quote(identity)}", headers={"x-secret": secret})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode())
            return d["token"], d["url"]
        except urllib.error.HTTPError as e:
            raise CallError(f"the token service refused ({e.code})", "token")
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            # v1 has no way round a blocked or absent route to the calls server (the phones' SNC bridge is not on this console): say so plainly, nothing is left ringing
            raise CallError(f"the calls server cannot be reached ({getattr(e, 'reason', e)}): calls need a direct connection to it on this console", "network")

    # ---- the protocol ---------------------------------------------------------------------------------------------------
    def _cmd(self, command, **fields):
        return self.rt.send_auth({"command": command, **fields})

    def _me(self):
        a = self.rt._account()
        if not a:
            raise CallError("not signed in", "not-signed-in")
        return a["email"]

    def _name(self, gid):
        try:
            return self.rt.chat_info(gid)["name"]
        except Exception:
            return gid

    def _expire_ended(self):
        """A call that is over is shown for a few seconds and then the controller is idle again. This must not depend on a page asking for the status: while the console sleeps the
        page is paused, and a controller left in "ended" would not hear the next call (the watcher looks only while idle)."""
        with self.lock:
            if self.s["state"] == "ended" and self.s["ended_at"] and time.time() - self.s["ended_at"] > SHOW_ENDED_S:
                self.s = self._idle()

    def status(self):
        self._expire_ended()
        with self.lock:
            s = dict(self.s)
        s["configured"] = self.configured()
        s["video"] = s["mode"] == "video"
        s["missed"] = len(self.missed)
        s["missed_list"] = [{"from": m.get("from", ""), "name": m.get("name", ""), "ts": m.get("ts"), "mode": m.get("mode")} for m in self.missed]
        return s

    def _set(self, **kw):
        with self.lock:
            self.s.update(kw)

    def start(self, gid, mode="audio"):
        me = self._me()
        with self.lock:
            if self.status()["state"] != "idle":
                raise CallError("there is a call already", "busy")
            if not self.configured():
                raise CallError("calls are not set up on this device: the token service secret or the call runtime is missing", "not-configured")
            room, call_id, ts = group_room_id(gid), str(uuid.uuid4()), int(time.time())
            # the one in the group: somebody else's call that is going on is joined by answering it, not by starting another
            cur = self._swcall(gid)
            if (cur and cur.get("from") != me and ts - int(cur.get("ts") or 0) < STALE_CALL_S) or self.ongoing(gid):
                raise CallError("a call is going on in this chat: join it", "busy")   # a second call would be a second room for the same people
            self.s = {**self._idle(), "state": "dialing", "call_id": call_id, "group_id": gid, "name": self._name(gid), "mode": mode, "from": me, "since": time.time()}
        try:
            self._cmd("var:set", path=f"sw_calls/{gid}", value=json.dumps({"from": me, "roomId": room, "mode": mode, "ts": ts, "callId": call_id}))
            r = self._cmd("chat:call", groupId=gid, chatName=self._name(gid), **{"from": me}, roomId=room, mode=mode, ts=str(ts), callId=call_id)
            if r.get(".status") != "ok":
                raise CallError(str(r.get(".reason") or "the server did not take the call"), "protocol")
            self._join(room, me, mode, outgoing=True)
        except Exception as e:
            self._abort(gid, call_id, str(e))
            raise
        self._monitor()
        return call_id

    def _swcall(self, gid):
        try:
            r = self._cmd("var:getAll", path="sw_calls")
            v = (r.get(".value") or {}).get(gid)
            if isinstance(v, str) and v not in ("", "0"):
                v = json.loads(v)
            return v if isinstance(v, dict) else None
        except Exception:
            return None

    def _abort(self, gid, call_id, why):
        """The call could not be set up: tell the server it is over and show the reason."""
        for c in (lambda: self._cmd("var:set", path=f"sw_calls/{gid}", value=0), lambda: self._cmd("chat:call_cancel", callId=call_id, groupId=gid, **{"from": self._me()})):
            try:
                c()
            except Exception:
                pass
        self._stop_engine()
        self._set(state="ended", reason=why, ended_at=time.time())

    def accept(self):
        me = self._me()
        with self.lock:
            s = self.s
            if s["state"] != "incoming":
                raise CallError("no call to answer", "idle")
            gid, call_id, room, mode = s["group_id"], s["call_id"], s["room"] if "room" in s else group_room_id(s["group_id"]), s["mode"]
            self._set(state="connecting")
        try:
            r = self._cmd("chat:call_accept", callId=call_id, groupId=gid, **{"from": me})
            if r.get(".status") != "ok" or r.get("state") == "ended":
                raise CallError("the call is over already" if r.get("state") == "ended" else str(r.get(".reason") or "the server did not take the answer"), "ended")
            self._join(room, me, mode, outgoing=False)
        except Exception as e:
            # we told the server we answer and then could not get into the room (no route to the calls server, a refused token): tell it we are not coming, or the caller waits for nobody
            self._finish(f"{e}", tell_server=True)
            raise
        self._monitor()

    def decline(self):
        me = self._me()
        with self.lock:
            s = self.s
            if s["state"] != "incoming":
                raise CallError("no call to decline", "idle")
            gid, call_id = s["group_id"], s["call_id"]
        try:
            self._cmd("chat:call_decline", callId=call_id, groupId=gid, **{"from": me})
        finally:
            self._set(state="ended", reason="declined", ended_at=time.time())

    def hangup(self):
        with self.lock:
            s = dict(self.s)
        if s["state"] in ("idle", "ended"):
            return
        self._finish("hung up", tell_server=True)

    def mute(self, on):
        with self.lock:
            self.s["muted"] = bool(on)
        self._to_engine("mute" if on else "unmute")

    # ---- the media ------------------------------------------------------------------------------------------------------
    def frame_path(self, which):
        """The picture of the other side ("remote") or the own camera ("local") of the call in progress: a file in a private folder (mode 0700) on the memory disk, never on the card;
        the folder is removed when the call ends."""
        with self.lock:
            if self._frames is None:
                base = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
                self._frames = os.path.join(base, f"intellicat-ratatosk-{os.getuid()}-{os.getpid()}-{id(self) % 100000}")
            os.makedirs(self._frames, mode=0o700, exist_ok=True)
            os.chmod(self._frames, 0o700)
            if not re.fullmatch(r"remote|local|remote-[1-3]", which):   # only the files the engine makes: a name from the page never reaches the file system
                which = "remote"
            return os.path.join(self._frames, which + ".jpg")

    def _join(self, room, identity, mode="audio", outgoing=False):
        token, url = self.fetch_token(room, identity)
        if androidhost.ENABLED:
            import ratatosk_webengine
            frames = (self.frame_path("remote"), self.frame_path("local")) if mode == "video" else None
            self.engine = ratatosk_webengine.WebEngine(url, token, mode, outgoing, frames, f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}")
            threading.Thread(target=self._read_engine, args=(self.engine,), daemon=True, name="ratatosk-engine").start()
            return
        env = dict(os.environ, INTELLICAT_CALL_TOKEN=token)       # not on the command line: `ps` does not show it
        cmd = [self.engine_python(), os.environ.get("INTELLICAT_RATATOSK_ENGINE") or os.path.join(HERE, "ratatosk_call_engine.py"), "--url", url,
               "--source", os.environ.get("INTELLICAT_RATATOSK_CALL_SOURCE", "alsa"), "--sink", os.environ.get("INTELLICAT_RATATOSK_CALL_SINK", "alsa")]
        if os.environ.get("INTELLICAT_RATATOSK_CALL_TONE"):         # the tests give each device its own pitch
            cmd += ["--tone", os.environ["INTELLICAT_RATATOSK_CALL_TONE"]]
        if outgoing:
            cmd.append("--ringback")                               # the tone of a call that rings at the other end
        if mode == "video":
            cmd += ["--video", "--frames", self.frame_path("remote"), "--preview", self.frame_path("local"),
                    "--camera", os.environ.get("INTELLICAT_RATATOSK_CALL_CAMERA", "auto")]
        dbg = os.environ.get("INTELLICAT_RATATOSK_CALL_LOG")        # the engine's own messages (the SDK is chatty, and the token is not among them) go here when asked
        self.engine = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(dbg, "ab") if dbg else subprocess.DEVNULL, text=True, start_new_session=True)
        threading.Thread(target=self._read_engine, args=(self.engine,), daemon=True, name="ratatosk-engine").start()

    def _to_engine(self, line):
        e = self.engine
        if e and e.poll() is None and e.stdin:
            try:
                e.stdin.write(line + "\n")
                e.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass

    def _stop_engine(self):
        e, self.engine = self.engine, None
        if e is None:
            with self.lock:
                if self._frames:
                    shutil.rmtree(self._frames, ignore_errors=True)
                    self._frames = None
            return
        if e.poll() is None and e.stdin:                          # (self.engine is already None here: write to the process itself, or it is killed after 3 s)
            try:
                e.stdin.write("hangup\n")
                e.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass
        try:
            e.wait(3)
        except subprocess.TimeoutExpired:
            e.kill()                                               # the microphone is released with the process
        with self.lock:                                            # the pictures of the call do not outlive it
            if self._frames:
                shutil.rmtree(self._frames, ignore_errors=True)
                self._frames = None
            self.s.update(remote_video=False, camera="none", fps=0, ringback=False, remotes=[], reconnecting=False)

    def camera(self, on):
        """The own camera on or off during a video call (a device without a camera says so and stays receive-only)."""
        with self.lock:
            if self.s["mode"] != "video" or self.s["state"] in ("idle", "ended"):
                raise CallError("no video call", "idle")
        self._to_engine("camera on" if on else "camera off")

    def serve_frames(self, handler, which):
        """The pictures as a motion JPEG (multipart/x-mixed-replace) for an <img> of the call screen; it ends with the call."""
        path = self.frame_path(which)
        handler.send_response(200)
        handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        last = None
        try:
            while self.s["state"] not in ("idle", "ended"):
                try:
                    m = os.stat(path).st_mtime_ns
                    if m == last:
                        raise OSError
                    with open(path, "rb") as f:
                        data = f.read()
                    last = m
                except OSError:
                    time.sleep(0.04)
                    continue
                handler.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data))
                handler.wfile.write(data + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _read_engine(self, e):
        for line in e.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("ev")
            with self.lock:
                if e is not self.engine:
                    continue
                who = ev.get("identity")
                if kind == "remote_joined":
                    self._remotes.setdefault(who, {"level": 0, "pitch": 0, "tile": None})
                    self.s.update(state="active", connected_at=self.s["connected_at"] or time.time())
                    self._sync_remotes()
                elif kind == "remote_audio":
                    r = self._remotes.get(who)                     # only somebody who is in the room: a late event of one who has left must not bring them back
                    if r is not None:
                        r["level"], r["pitch"] = ev.get("rms", 0), ev.get("pitch", 0)
                        self._sync_remotes()
                elif kind == "remote_video":
                    r = self._remotes.get(who)
                    if r is not None:
                        r["tile"] = ev.get("tile", 0) if ev.get("on") else None
                        self._sync_remotes()
                elif kind == "video_stats":
                    self.s.update(fps=ev.get("fps", 0))
                elif kind == "camera":
                    self.s.update(camera=ev.get("state", "none"))
                elif kind == "ringback":
                    self.s.update(ringback=bool(ev.get("on")))
                elif kind == "reconnecting":                       # the media connection dropped and is trying to come back: the call is NOT over, it is shown as reconnecting
                    if not self.s["reconnecting"]:
                        self.s.update(reconnecting=True)
                        threading.Thread(target=self._reconnect_watch, args=(e,), daemon=True, name="ratatosk-reconnect").start()
                elif kind == "reconnected":
                    self.s.update(reconnecting=False)
                    if not self._remotes and self.s["state"] == "active":   # whoever was here may have gone meanwhile: the empty-room rule applies again
                        threading.Thread(target=self._after_empty, daemon=True, name="ratatosk-empty").start()
                elif kind == "remote_left":
                    self._remotes.pop(who, None)
                    self._sync_remotes()
                    if not self._remotes:                          # the room is empty: only when it stays so for a few seconds is the call over (a group call goes on while somebody is in it)
                        threading.Thread(target=self._after_empty, daemon=True, name="ratatosk-empty").start()
                elif kind == "error":
                    self.log(f"ratatosk call: engine: {ev.get('message')}")
                    threading.Thread(target=self._finish, args=(str(ev.get("message")), True), daemon=True).start()
                elif kind == "disconnected" and self.s["state"] in ("dialing", "connecting", "active"):
                    threading.Thread(target=self._finish, args=("the connection to the room was lost", True), daemon=True).start()

    def _sync_remotes(self):
        """The list of the others in the room for the page, and the single values the earlier screens use (called with the lock held)."""
        r = self._remotes
        loud = max(r.values(), key=lambda v: v["level"], default=None)
        self.s.update(remotes=[{"identity": k, "name": (k or "").split("@")[0], "level": v["level"], "pitch": v["pitch"], "tile": v["tile"]} for k, v in r.items()],
                      remote=next(iter(r), None), level=loud["level"] if loud else 0, pitch=loud["pitch"] if loud else 0,
                      remote_video=any(v["tile"] is not None for v in r.values()), max_remotes=max(self.s.get("max_remotes", 0), len(r)))

    def _after_empty(self):
        time.sleep(EMPTY_GRACE_S)
        with self.lock:
            s = dict(self.s)
            if s["state"] != "active" or self._remotes or s["reconnecting"]:   # while our own connection is down the room looks empty: that says nothing about the others
                return
        self._finish("everybody left" if s["max_remotes"] > 1 else "the other side left", True)

    def _reconnect_watch(self, engine):
        """The media connection has been down for RECONNECT_GIVE_UP_S: it is not coming back, the call is over (the engine reports `disconnected` itself when the SDK gives up earlier)."""
        end = time.time() + RECONNECT_GIVE_UP_S
        while time.time() < end:
            time.sleep(0.25)
            with self.lock:
                if engine is not self.engine or not self.s["reconnecting"] or self.s["state"] in ("idle", "ended"):
                    return
        self._finish("the connection to the calls server was lost", True)

    def _finish(self, reason, tell_server=True):
        with self.lock:
            s = dict(self.s)
            if s["state"] in ("idle", "ended"):
                return
            self.s["state"], self.s["reason"], self.s["ended_at"] = "ended", reason, time.time()
            self._remotes = {}
        if tell_server and s["call_id"] and s["group_id"]:
            try:
                if s["state"] == "dialing" and s["from"] == self._me():
                    r = self._cmd("chat:call_cancel", callId=s["call_id"], groupId=s["group_id"], **{"from": s["from"]})
                else:
                    r = self._cmd("chat:call_end", callId=s["call_id"], groupId=s["group_id"], **{"from": self._me()})
                if r.get("state") == "ended":                      # the call is over for everybody: the marker of the chat goes. A group call that goes on for the others keeps it
                    self._cmd("var:set", path=f"sw_calls/{s['group_id']}", value=0)
            except Exception as e:
                self.log(f"ratatosk call: telling the server failed: {e}")
        self._stop_engine()

    # ---- group calls (stage 7): a call that is going on in a chat, and joining it ---------------------------------------------
    def ongoing(self, gid):
        """The call going on in a chat (ringing or active), from the server's record, or None: who started it, its kind, who is in it now."""
        r = self._cmd("chat:call_state", groupId=gid)
        if r.get(".status") != "ok" or r.get("state") not in ("ringing", "active"):
            return None
        who = [e for e, v in (r.get("recipients") or {}).items() if v.get("status") == "accepted"]
        if r.get("from") and not r.get("callerLeft"):
            who.insert(0, r["from"])
        return {"group_id": gid, "call_id": r.get("callId"), "from": r.get("from"), "mode": "video" if r.get("mode") == "video" else "audio", "state": r["state"],
                "room": r.get("roomId") or group_room_id(gid), "participants": who}

    def join(self, gid):
        """Go into the call that is going on in a chat. On the phone this is no step of the protocol: the same room, a token of our own (a member added after the call began is
        not a recipient of the server's record and joins just the same); `chat:call_accept` is told as a best effort, so that a member who was rung, did not answer or left is shown as in."""
        me = self._me()
        with self.lock:
            if self.status()["state"] != "idle":
                raise CallError("there is a call already", "busy")
            if not self.configured():
                raise CallError("calls are not set up on this device: the token service secret or the call runtime is missing", "not-configured")
        o = self.ongoing(gid)
        if not o:
            raise CallError("no call is going on in this chat", "idle")
        with self.lock:
            self.s = {**self._idle(), "state": "connecting", "call_id": o["call_id"], "group_id": gid, "name": self._name(gid), "mode": o["mode"], "from": o["from"],
                      "since": time.time(), "room": o["room"], "joined": True}
        try:
            try:
                self._cmd("chat:call_accept", callId=o["call_id"], groupId=gid, **{"from": me})
            except Exception:
                pass
            self._join(o["room"], me, o["mode"], outgoing=False)
        except Exception as e:
            self._finish(str(e), tell_server=False)
            raise
        self._monitor()

    def _monitor(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="ratatosk-call-poll")
            self._thread.start()

    def _monitor_loop(self):
        misses = 0
        while True:
            time.sleep(POLL_S)
            with self.lock:
                s = dict(self.s)
            if s["state"] not in ("dialing", "connecting", "active", "incoming"):
                return
            r = {}
            try:
                r = self._cmd("chat:call_state", callId=s["call_id"], groupId=s["group_id"])
                st = r.get("state") if r.get(".status") == "ok" else None
            except Exception:
                st = None
            if s["state"] == "incoming" and st in ("active", "ringing"):
                # somebody else's device answered or declined for the account: the ring stops here (the phone does the same), and it is not a missed call
                rec = (r.get("recipients") or {}).get(self._me()) or {}
                if st == "active" or rec.get("status") in ("accepted", "declined"):
                    self._finish("answered on another device" if st == "active" else "declined on another device", tell_server=False)
                    return
            if st == "ended":                                      # a fact: the server says it is over
                why = {"all_declined": "declined", "timeout": "nobody answered", "cancelled": "the caller hung up", "ended_by_peer": "the other side ended the call"}.get(r.get("endReason"), r.get("endReason") or "ended")
                if s["state"] == "incoming" and r.get("endReason") in ("timeout", "cancelled"):
                    self._add_missed(s)                            # it rang here and nobody here answered
                    why = "missed call"
                self._finish(why, tell_server=False)
                return
            if st in (None, "none"):
                misses += 1
                if misses >= MISSES_TO_END and not s.get("remote") and not s.get("reconnecting"):   # never on one bad poll; not while the other one is heard; not while our own link is down
                    if s["state"] == "incoming":
                        self._add_missed(s)
                    self._finish("the call is gone", tell_server=False)
                    return
            else:
                misses = 0

    # ---- incoming calls (the watcher: decision 6, 3 s while awake) ---------------------------------------------------
    def watch_once(self):
        """One look at `sw_calls`: an entry for a chat of ours, started by somebody else, still ringing for us, while we are idle -> the call is announced."""
        me = self._me()
        self._expire_ended()
        if self.s["state"] != "idle":
            return
        r = self._cmd("var:getAll", path="sw_calls")
        vals = (r.get(".value") or {}) if r.get(".status") == "ok" else {}
        now = int(time.time())
        mine = {c["id"] for c in self.rt.chat_list()}
        for gid, raw in vals.items():
            if gid not in mine or raw in (None, 0, "0", ""):
                continue
            try:
                v = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                continue
            if not isinstance(v, dict) or v.get("from") == me or now - int(v.get("ts") or 0) > STALE_CALL_S:
                continue
            cid = v.get("callId") or ""
            if not cid or cid in self.seen_calls:
                continue
            st = self._cmd("chat:call_state", callId=cid, groupId=gid)
            rec = (st.get("recipients") or {}).get(me) if st.get(".status") == "ok" else None
            if st.get("state") != "ringing" or not rec or rec.get("status") != "pending":
                continue
            self.seen_calls.add(cid)
            with self.lock:
                if self.s["state"] != "idle":
                    continue                                       # busy with another call: this one rings on the phones, not here
                self.s = {**self._idle(), "state": "incoming", "call_id": cid, "group_id": gid, "name": self._name(gid), "mode": v.get("mode", "audio"), "from": v.get("from", ""),
                          "incoming": True, "since": time.time(), "room": v.get("roomId") or group_room_id(gid)}
            self.log(f"ratatosk call: incoming call from {v.get('from')} in {gid}")
            threading.Thread(target=self._ringer, args=(cid,), daemon=True, name="ratatosk-ringer").start()
            if self.on_incoming:                       # the wake, the notification and the app coming up may take their time (the shared window can be slow to answer): not in the watcher's thread
                threading.Thread(target=self._announce, args=(dict(self.s),), daemon=True, name="ratatosk-announce").start()
            self._monitor()
            return

    def _announce(self, snapshot):
        try:
            self.on_incoming(snapshot)
        except Exception as e:  # noqa: BLE001
            self.log(f"ratatosk call: the announcement failed: {e}")

    def _ringer(self, call_id):
        """A ringing call is heard until it is answered, declined, over, or the ring timeout has passed: `on_ring` (sound and vibration, shelld's) every RING_EVERY_S."""
        end = time.time() + RING_MAX_S
        while time.time() < end:
            time.sleep(RING_EVERY_S)
            with self.lock:
                if self.s["state"] != "incoming" or self.s["call_id"] != call_id:
                    return
                snapshot = dict(self.s)
            if self.on_ring:
                try:
                    self.on_ring(snapshot)
                except Exception as e:  # noqa: BLE001
                    self.log(f"ratatosk call: the ring failed: {e}")

    def _engine_api(self, method, path, body):
        e = self.engine
        if not hasattr(e, "next_command"):
            return 404, {"error": "no web engine"}
        if path == "call-engine-config":
            return 200, e.config()
        if path == "call-engine-poll":
            return 200, {"commands": e.next_command()}
        if path == "call-engine-event":
            e.event(body)
        elif path == "call-engine-frame":
            e.frame(str(body.get("which", "")), str(body.get("jpeg", "")))
        else:
            return 404, {"error": "not found"}
        return 200, {"ok": True}

    def handle(self, method, path, body):
        """(HTTP status, JSON) of the call API of runtime/ratatosk.py."""
        try:
            if method == "GET" and path == "call":
                return 200, self.status()
            if path.startswith("call-engine-"):     # the page of the engine on Android (ratatosk_webengine.py)
                return self._engine_api(method, path, body)
            if method == "POST" and path == "call-start":
                self.start(str(body.get("id", "")), "video" if body.get("mode") == "video" else "audio")
            elif method == "POST" and path == "call-accept":
                self.accept()
            elif method == "POST" and path == "call-decline":
                self.decline()
            elif method == "POST" and path == "call-hangup":
                self.hangup()
            elif method == "POST" and path == "call-mute":
                self.mute(bool(body.get("on")))
            elif method == "POST" and path == "call-join":
                self.join(str(body.get("id", "")))
            elif method == "POST" and path == "call-ongoing":      # the call going on in a chat, for the banner (None when there is none)
                return 200, {"ongoing": self.ongoing(str(body.get("id", "")))}
            elif method == "POST" and path == "call-camera":
                self.camera(bool(body.get("on")))
            elif method == "POST" and path == "call-missed-clear":
                self.clear_missed()
            else:
                return 404, {"error": "not found"}
            return 200, self.status()
        except CallError as e:
            return {"busy": 409, "not-configured": 409, "idle": 409, "ended": 410, "network": 503, "token": 502, "not-signed-in": 401}.get(e.code, 400), {"error": str(e), "code": e.code}
