# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Ratatosk on IntelliCat (docs/usage.md), stage 1: the account and the list of chats.

The backend of the app `apps/ratatosk`, running inside `shelld` (decision D1): a client of the ReForce backend of Ratatosk, the sign-in with its silent re-login, the
sync of the chat list and a small store, behind the API `/api/rt/*` (shelld routes it; only the page of the Ratatosk app may call it).

    GET  status    {configured, signed_in, email, online, syncing, last_sync, error}
    POST signin    {email, password}   -> {ok}  or an error {error, code}: not-configured | bad-credentials | network | protocol
    POST signout                       wipes the account, the session and the store
    GET  chats     [{id, name, is_group, is_admin, peer, last_at, unread}] by last activity
    POST sync      one sync now

How the server is spoken to: Camerlengo API **v2 only** (the legacy v1 API must never be used): one POST endpoint, JSON, the command in `command` (`user:verify`, `group:*`,
`var:getAll` / `var:set`, `file:read` / `write` / `append` / `delete`, `chat:*`), a `.msgid`, the device's v2 key in `key`, the v2 session in `session` once signed in; an answer
carries `.status` ("ok" / "error", with `.reason`). The paths and the fields are the ones the Android client (`ReForceClient.kt`, `ChatRepository.kt`) uses, so the data is the
same. Login is `user:verify` (path `/users`, user = email); when the server says the session is bad the client logs in again with the stored password and retries once (never
signs out by itself). ReForce.send refuses a v1 command (`.command`).

Secrets: **the key is not in this repository** (decision D3). It is a v2 key of the device (service "intellicat-ratatosk": user:verify, the group:* and chat:* commands, var:getAll /
var:set under users/ sw_lastmsg sw_calls, file:* under chats/ users/ group_invites/), read from `$INTELLICAT_RATATOSK_KEY` or from the file `<data>/ratatosk/site_key`. The password and the session are kept in `<data>/ratatosk/account.json` with mode 600 (decision 7: the device keeps the password, as the phone
does, for the silent re-login), never in the SD card, never returned by the API, never logged.
"""
import hashlib, json, os, sqlite3, threading, time, urllib.error, urllib.request, uuid
from urllib.parse import parse_qs, urlparse

DEFAULT_URL = "https://squirrelwisdom.com/"
AUTH_NS = "/users"
SYNC_EVERY_S = 30
GROUP_REFRESH_S = 180        # a group of a list that carries no names is fetched again after this long (a rename by somebody else shows up)
TIMEOUT_S = 15


class RatatoskError(Exception):
    def __init__(self, message, code="error"):
        super().__init__(message)
        self.code = code


def direct_chat_id(a, b):
    """The id of the 1:1 chat of two people: the first 12 hex digits of SHA-256 of the sorted, lowercased pair joined by "|" (ChatRepository.directChatId)."""
    pair = "|".join(sorted(x.lower().strip() for x in (a, b)))
    return hashlib.sha256(pair.encode()).hexdigest()[:12]


def profile_hash(email):
    """Where a person's profile variables live: `users/<this>` (the first 12 hex digits of SHA-256 of the lowercased email)."""
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:12]


class ReForce:
    """The one HTTP gateway: a command in, the answer out. No session handling here (see Ratatosk.send_auth)."""

    def __init__(self, url, key, traffic=None):
        self.url, self.key = url, key
        self.traffic = traffic if traffic is not None else {"requests": 0, "out": 0, "in": 0}   # what this device has sent and received (the cost of keeping watch, plan W2)

    def send(self, command):
        if ".command" in command or "command" not in command:
            raise RatatoskError("only Camerlengo v2 commands are sent (no .command)", "protocol")
        body = dict(command)
        body[".msgid"] = str(uuid.uuid4())
        body["key"] = self.key
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        self.traffic["requests"] += 1
        self.traffic["out"] += len(data)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                text = r.read().decode("utf-8", "replace")
            self.traffic["in"] += len(text)
        except (urllib.error.URLError, OSError) as e:   # includes timeouts and refused connections
            raise RatatoskError(f"no connection to the server: {getattr(e, 'reason', e)}", "network")
        try:
            out = json.loads(text)
        except ValueError:
            raise RatatoskError("the server answered with something that is not JSON", "protocol")
        if not isinstance(out, dict):
            raise RatatoskError("the server's answer is not an object", "protocol")
        return out


def _session_error(resp):
    reason = str(resp.get(".reason", "")).lower()
    return resp.get(".status") == "error" and ("session" in reason or "not authenticated" in reason or "permission denied" in reason)


def ratatosk_attach_error():
    import ratatosk_attach
    return ratatosk_attach.AttachError


def ratatosk_group_error():
    import ratatosk_groups
    return ratatosk_groups.GroupError


class Ratatosk:
    def __init__(self, data_dir, log=print, url=None):
        self.dir = os.path.join(data_dir, "ratatosk")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.url = url or os.environ.get("INTELLICAT_RATATOSK_URL") or DEFAULT_URL
        self.log = log
        self.lock = threading.RLock()          # the account, the store
        self.login_lock = threading.Lock()     # one re-login at a time: the others wait and use its session
        self.sync_lock = threading.Lock()      # one sync at a time
        self.last_login = 0.0
        self.state = {"online": None, "syncing": False, "last_sync": None, "error": None}
        self._thread = None
        self._wake = threading.Event()
        self._stopped = threading.Event()        # set by stop(): the loops end (the service manager, or a test)
        self.asleep = lambda: False              # set by shelld: is the console asleep (the watcher then looks less often, it never stops)
        self.seen_since = lambda ts: True        # set by shelld: was the console touched after this time (a missed call counts as seen only then)
        self.traffic = {"requests": 0, "out": 0, "in": 0}   # every request of this device to the server, counted here
        self.watch_stats = {"polls": 0, "errors": 0, "interval": 0.0, "since": time.time(), "failing": 0}
        self.db = sqlite3.connect(os.path.join(self.dir, "ratatosk.db"), check_same_thread=False)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, name TEXT, peer TEXT, is_group INTEGER, is_admin INTEGER, last_at INTEGER, unread INTEGER DEFAULT 0, server_name TEXT);"
            "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT);")
        import ratatosk_chat   # the chat itself (stage 4): its tables join the store, it needs this object's session
        self.chats = ratatosk_chat.Chats(self)
        import ratatosk_attach   # the attachments (stage 5): the files of the messages, encrypted
        self.attachments = ratatosk_attach.Attachments(self, self.chats, log=log)
        self._group_seen = {}                    # group id -> when its record was last fetched (for a server whose list of groups carries no names)
        import ratatosk_groups   # the groups themselves (stage 7b): members, new chats, invitations, with the keys that go with them
        self.groups = ratatosk_groups.Groups(self, self.chats)
        import ratatosk_call   # the calls (stage 2)
        self.on_incoming = None                 # set by shelld: what happens when somebody calls (the app is brought up, the ring)
        self.on_missed = None                   # set by shelld: a call rang here and was not answered (the notification and the badge of the tile); (record, count), or (None, 0) when cleared
        self.on_ring = None                     # set by shelld: a ringing call repeats its sound and vibration (every few seconds, until it is answered or over)
        self.calls = ratatosk_call.Calls(self, on_incoming=lambda c: self.on_incoming and self.on_incoming(c), log=log,
                                         on_missed=lambda c, n: self.on_missed and self.on_missed(c, n), on_ring=lambda c: self.on_ring and self.on_ring(c),
                                         can_clear=lambda ts: self.seen_since(ts))
        self._watch_thread = None

    # ---- the account (the file is the only place of the password and the session) ----------------------------------------
    def _account_path(self):
        return os.path.join(self.dir, "account.json")

    def _account(self):
        try:
            with open(self._account_path(), encoding="utf-8") as f:
                a = json.load(f)
            return a if a.get("email") and a.get("password") else None
        except (OSError, ValueError):
            return None

    def _save_account(self, a):
        tmp = self._account_path() + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(a, f)
        os.replace(tmp, self._account_path())
        os.chmod(self._account_path(), 0o600)

    def _key(self):
        k = os.environ.get("INTELLICAT_RATATOSK_KEY", "").strip()
        if k:
            return k
        try:
            with open(os.path.join(self.dir, "site_key"), encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def _client(self):
        key = self._key()
        if not key:
            raise RatatoskError("the site key of the server is not on this device (see docs/usage.md)", "not-configured")
        return ReForce(self.url, key, self.traffic)

    # ---- sign-in and the session -----------------------------------------------------------------------------------------
    def _verify(self, email, password):
        resp = self._client().send({"command": "user:verify", "path": AUTH_NS, "user": email, "password": password})
        if resp.get(".status") == "ok" and str(resp.get("session", "")).strip():   # v2 user:verify: {"path", "user", "session"}
            return str(resp["session"])
        if resp.get(".status") == "ok":
            raise RatatoskError("wrong email or password", "bad-credentials")
        raise RatatoskError(str(resp.get(".reason") or "the server refused the sign-in"), "bad-credentials")

    def sign_in(self, email, password):
        email = (email or "").strip().lower()
        if not email or not password:
            raise RatatoskError("email and password are needed", "bad-credentials")
        session = self._verify(email, password)
        with self.lock:
            self._save_account({"email": email, "password": password, "session": session})
            self.last_login = time.time()
            self.state.update({"online": True, "error": None})
        try:
            self.chats.unlock(email, password)   # the keys of the user, in memory only (no key record: encrypted chats stay closed, the list works)
        except Exception as e:
            self.log(f"ratatosk: unlocking the keys failed: {e}")
        self.start()
        self._wake.set()

    def sign_out(self):
        with self.lock:
            try:
                os.remove(self._account_path())
            except OSError:
                pass
            self.db.executescript("DELETE FROM chats; DELETE FROM users;")
            self.db.commit()
            self.chats.forget()                  # the messages, the pins and the keys in memory go with the account
            self.attachments.purge()             # the decrypted copies made for playing go too (what the owner saved stays)
            self.state.update({"last_sync": None, "error": None})

    def _relogin(self, stale_session):
        """Log in again with the stored password, once for all callers: a caller that waited finds a newer session and uses it."""
        with self.login_lock:
            a = self._account()
            if not a:
                raise RatatoskError("not signed in", "not-signed-in")
            if a.get("session") != stale_session and time.time() - self.last_login < 10:
                return a["session"]
            a["session"] = self._verify(a["email"], a["password"])
            with self.lock:
                self._save_account(a)
                self.last_login = time.time()
            self.log("ratatosk: session renewed")
            return a["session"]

    def send_auth(self, command):
        """The command with the session; on a session error one silent re-login and one retry; never signs out by itself."""
        a = self._account()
        if not a:
            raise RatatoskError("not signed in", "not-signed-in")
        client = self._client()
        session = a.get("session") or self._relogin(None)
        resp = client.send({**command, "session": session})
        if _session_error(resp):
            resp = client.send({**command, "session": self._relogin(session)})
        return resp

    # ---- the sync of the chat list ---------------------------------------------------------------------------------------
    def _ok(self, resp, what):
        if resp.get(".status") != "ok":
            raise RatatoskError(f"{what}: {resp.get('.reason') or resp.get('.status')}", "protocol")
        return resp

    def sync_chats(self):
        a = self._account()
        if not a:
            raise RatatoskError("not signed in", "not-signed-in")
        me = a["email"]
        groups = self._ok(self.send_auth({"command": "group:getUserGroups", "user": me}), "getUserGroups").get("groups") or []
        seen = []
        for item in groups:
            gid = item if isinstance(item, str) else (item.get("group_id") if isinstance(item, dict) else None)
            if not gid:
                continue
            seen.append(gid)
            hint = ((item.get("meta") or {}).get("name") or "").strip() if isinstance(item, dict) else ""
            with self.lock:
                row = self.db.execute("SELECT server_name, is_group FROM chats WHERE id=?", (gid,)).fetchone()
            # a chat we know is kept as it is; a group is fetched again when the name the server gives it has changed, or, when the list carries no names, every few minutes
            stale = bool(row and row[1] and not hint and time.time() - self._group_seen.get(gid, 0) > GROUP_REFRESH_S)
            if row and not stale and (not row[1] or not hint or (row[0] or "") == hint):
                continue
            self._group_seen[gid] = time.time()
            try:
                g = self._ok(self.send_auth({"command": "group:get", "group_id": gid}), "getGroup").get("group")
            except RatatoskError as e:
                if e.code in ("network", "not-signed-in"):
                    raise
                self.log(f"ratatosk: getGroup {gid} failed: {e}")
                continue
            if isinstance(g, dict):
                self._store_group(gid, g, me)
        with self.lock:   # chats the user is no longer in
            for (gid,) in self.db.execute("SELECT id FROM chats").fetchall():
                if gid not in seen:
                    self.db.execute("DELETE FROM chats WHERE id=?", (gid,))
            self.db.commit()
        self._resolve_names()
        self._last_times()
        self._sync_messages()
        with self.lock:
            self.state.update({"last_sync": int(time.time()), "online": True, "error": None})

    def _sync_messages(self):
        """The chats whose last message is newer than our last look at them (one variable tells it for all: `sw_lastmsg`); the others cost nothing."""
        with self.lock:
            rows = self.db.execute("SELECT c.id, c.last_at, COALESCE(s.synced, 0) FROM chats c LEFT JOIN chat_state s ON s.chat = c.id").fetchall()
        for gid, last_at, synced in rows:
            if last_at and last_at > synced:
                try:
                    self.chats.sync_messages(gid)
                except RatatoskError as e:
                    if e.code in ("network", "not-signed-in"):
                        raise
                    self.log(f"ratatosk: messages of {gid}: {e}")
                except Exception as e:     # one chat that cannot be read never stops the others
                    self.log(f"ratatosk: messages of {gid} failed: {e!r}")

    def _store_group(self, gid, g, me):
        meta = g.get("meta") or {}
        server_name = (meta.get("name") or "").strip() or None
        people = []
        for k in ("admins", "members", "observers"):
            people += [p for p in (g.get(k) or []) if isinstance(p, str)]
        people = list(dict.fromkeys(people))
        other = next((p for p in people if p != me), "") if len(people) == 2 else ""
        # a DM only when the id is the deterministic hash of the pair; a two-person group with another id is a group ("dialog" suffix, as on the phone)
        is_dm = bool(other) and gid == direct_chat_id(me, other)
        if is_dm:
            name = other
        elif other:
            name = f"{other} (dialog {gid[:8].upper()})"
        else:
            name = server_name or f"Group {gid[:6]}"
        with self.lock:
            old = self.db.execute("SELECT last_at, unread FROM chats WHERE id=?", (gid,)).fetchone()
            self.db.execute("INSERT OR REPLACE INTO chats (id, name, peer, is_group, is_admin, last_at, unread, server_name) VALUES (?,?,?,?,?,?,?,?)",
                            (gid, name, other if is_dm else "", 0 if is_dm else 1, 1 if me in (g.get("admins") or []) else 0, old[0] if old else None, old[1] if old else 0,
                             server_name or ""))
            self.db.commit()

    def _resolve_names(self):
        """A DM shows the person's name from the profile (`users/<hash>`: firstName, lastName) instead of the email, when there is one; asked once per person."""
        with self.lock:
            peers = [r[0] for r in self.db.execute("SELECT DISTINCT peer FROM chats WHERE peer != ''").fetchall()]
            known = {r[0]: r[1] for r in self.db.execute("SELECT email, name FROM users").fetchall()}
        for email in peers:
            if email not in known:
                name = ""
                try:
                    v = self._ok(self.send_auth({"command": "var:getAll", "path": f"users/{profile_hash(email)}"}), "profile").get(".value") or {}
                    name = " ".join(x for x in ((v.get("firstName") or "").strip(), (v.get("lastName") or "").strip()) if x)
                except RatatoskError as e:
                    if e.code in ("network", "not-signed-in"):
                        raise
                with self.lock:
                    self.db.execute("INSERT OR REPLACE INTO users (email, name) VALUES (?,?)", (email, name or email))
                    self.db.commit()
                known[email] = name or email
            if known[email] and known[email] != email:
                with self.lock:
                    self.db.execute("UPDATE chats SET name=? WHERE peer=? AND is_group=0", (known[email], email))
                    self.db.commit()

    def _last_times(self):
        """One variable has the time of the last message of every chat (`sw_lastmsg`, written on every send)."""
        resp = self.send_auth({"command": "var:getAll", "path": "sw_lastmsg"})
        vals = resp.get(".value") if resp.get(".status") == "ok" else None
        if isinstance(vals, dict):
            with self.lock:
                for gid, ts in vals.items():
                    try:
                        ts = int(ts)
                    except (TypeError, ValueError):
                        continue
                    self.db.execute("UPDATE chats SET last_at=? WHERE id=? AND (last_at IS NULL OR last_at < ?)", (ts, gid, ts))
                self.db.commit()

    def chat_list(self):
        with self.lock:
            rows = self.db.execute("SELECT id, name, peer, is_group, is_admin, last_at, unread, preview FROM chats ORDER BY COALESCE(last_at, 0) DESC, name COLLATE NOCASE").fetchall()
        return [{"id": r[0], "name": r[1], "peer": r[2], "is_group": bool(r[3]), "is_admin": bool(r[4]), "last_at": r[5], "unread": r[6], "preview": r[7] or ""} for r in rows]

    def chat_info(self, gid):
        with self.lock:
            r = self.db.execute("SELECT id, name, is_group FROM chats WHERE id=?", (gid,)).fetchone()
        if not r:
            raise RatatoskError("no such chat", "not-found")
        return {"id": r[0], "name": r[1], "is_group": bool(r[2])}

    # ---- the loop and the API --------------------------------------------------------------------------------------------
    def sync_now(self):
        """One sync; a caller that arrives while another sync runs waits for it and then does its own (so its answer is a list that is up to date)."""
        with self.sync_lock:
            self.state["syncing"] = True
            try:
                self.sync_chats()
            except RatatoskError as e:
                with self.lock:
                    self.state.update({"error": str(e), "online": False if e.code == "network" else self.state["online"]})
                self.log(f"ratatosk: sync failed ({e.code}): {e}")
            finally:
                self.state["syncing"] = False

    def start(self):
        with self.lock:
            self._stopped.clear()
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="ratatosk-sync")
                self._thread.start()
            if self._watch_thread is None:
                self._watch_thread = threading.Thread(target=self._watch, daemon=True, name="ratatosk-calls")
                self._watch_thread.start()

    def stop(self):
        """The background part stops (the service manager, a test): the loops end at their next look. A call in progress is not touched."""
        self._stopped.set()
        self._wake.set()
        with self.lock:
            self._thread = self._watch_thread = None

    def watch_delay(self, fails):
        """The pause before the next look at the calls: 3 s awake, 5 s asleep, doubled for every failure in a row up to 30 s (no network, a server that does not answer)."""
        import ratatosk_call
        base = ratatosk_call.WATCH_ASLEEP_S if self.asleep() else ratatosk_call.WATCH_S
        return min(ratatosk_call.WATCH_BACKOFF_MAX_S, base * (2 ** min(fails, 4)))

    def _watch(self):
        """The watcher of incoming calls (decision 6): a look at `sw_calls` every few seconds while there is an account, also while the console sleeps. It does not hammer a server
        that does not answer: the pause doubles with every failure and is the normal one again at the first answer."""
        fails = 0
        while not self._stopped.is_set():
            delay = self.watch_delay(fails)
            self.watch_stats["interval"] = delay
            if self._stopped.wait(delay):
                return
            if self._account() and self._key():
                try:
                    self.calls.watch_once()
                    self.watch_stats["polls"] += 1
                    if fails:
                        self.log("ratatosk: the call watcher has the server again")
                    fails = 0
                except Exception as e:  # noqa: BLE001
                    fails += 1
                    self.watch_stats["errors"] += 1
                    if fails == 1:                      # one line for a whole outage, not one for every try
                        self.log(f"ratatosk: call watcher: {e} (it tries again, less and less often)")
                self.watch_stats["failing"] = fails

    def _loop(self):
        while not self._stopped.is_set():
            if self._account() and self._key():
                self.sync_now()
                try:
                    self.groups.accept_invites()       # the invitations that wait for this account (at most once a minute)
                except Exception as e:  # noqa: BLE001
                    self.log(f"ratatosk: invitations: {e!r}")
            self._wake.wait(SYNC_EVERY_S)
            self._wake.clear()

    def status(self):
        a = self._account()
        return {"configured": self._key() is not None, "signed_in": a is not None, "email": a["email"] if a else None,
                "online": self.state["online"], "syncing": self.state["syncing"], "last_sync": self.state["last_sync"], "error": self.state["error"]}

    def watcher(self):
        """What keeping watch costs, measured (plan W2): the looks and failures so far, the pause now, and every request of this device with its bytes, since the start."""
        w, t = self.watch_stats, self.traffic
        return {"polls": w["polls"], "errors": w["errors"], "failing": w["failing"], "interval_s": w["interval"], "asleep": bool(self.asleep()),
                "requests": t["requests"], "bytes_out": t["out"], "bytes_in": t["in"], "seconds": int(time.time() - w["since"])}

    def handle(self, method, path, body):
        """(HTTP status, JSON object) of one API call; `path` is what follows /api/rt/."""
        url = urlparse(path)
        path, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if method == "GET" and path == "status":
                self.start()
                return 200, self.status()
            if method == "GET" and path == "chats":
                return 200, {"chats": self.chat_list()}
            if method == "GET" and path == "watcher":
                return 200, self.watcher()
            if method == "GET" and path == "chat":                  # one chat: its messages (a sync of it first; when the network is down, the ones we have)
                info, note = self.chat_info(q.get("id", "")), None
                try:
                    self.chats.sync_messages(info["id"], force_access=True)
                except RatatoskError as e:
                    if e.code != "network":
                        raise
                    note = str(e)
                self.chats.mark_read(info["id"])
                return 200, {"chat": info, "messages": self.chats.messages(info["id"]), "me": self.chats.me, "offline": note}
            if method == "GET" and path == "group":                 # who is in a chat
                return 200, self.groups.info(self.chat_info(q.get("id", ""))["id"])
            if method == "POST" and path in ("users-search", "groups-search"):
                return 200, {"results": (self.groups.search_users if path == "users-search" else self.groups.search_groups)(body.get("query", ""))}
            if method == "POST" and path == "dm-create":
                return 200, {"id": self.groups.create_dm(body.get("email"))}
            if method == "POST" and path == "group-create":
                return 200, {"id": self.groups.create_group(body.get("name"), body.get("members") or [])}
            if method == "POST" and path in ("group-add", "group-remove", "group-leave", "group-delete", "group-rename"):
                gid = self.chat_info(str(body.get("id", "")))["id"]
                if path == "group-add":
                    return 200, {"result": self.groups.add_member(gid, body.get("email"))}
                if path == "group-remove":
                    self.groups.remove_member(gid, body.get("email"))
                elif path == "group-leave":
                    self.groups.leave(gid)
                elif path == "group-delete":
                    self.groups.delete_group(gid)
                else:
                    self.groups.rename(gid, body.get("name"))
                return 200, {"ok": True}
            if method == "POST" and path in ("send", "edit", "delete", "read", "send-file", "attachment-open"):
                gid = self.chat_info(str(body.get("id", "")))["id"]
                if path == "send":
                    return 200, {"ok": True, "id": self.chats.send(gid, body.get("text"), body.get("reply"))}
                if path == "send-file":        # a file of the card, with the typed text as its caption
                    return 200, {"ok": True, "id": self.attachments.send_file(gid, body.get("path"), body.get("text") or "", body.get("reply"))}
                if path == "attachment-open":  # a decrypted copy on the card: to play (short-lived) or to keep
                    return 200, self.attachments.to_card(gid, str(body.get("msg", "")), body.get("i", 0), "save" if body.get("mode") == "save" else "play")
                if path == "edit":
                    self.chats.edit(gid, str(body.get("message_id", "")), str(body.get("text", "")))
                elif path == "delete":
                    self.chats.delete(gid, str(body.get("message_id", "")))
                else:
                    self.chats.mark_read(gid)
                return 200, {"ok": True}
            if path == "call" or path.startswith("call-"):
                return self.calls.handle(method, path, body)
            if method == "POST" and path == "signin":
                self.sign_in(body.get("email"), body.get("password"))
                return 200, {"ok": True}
            if method == "POST" and path == "signout":
                self.sign_out()
                return 200, {"ok": True}
            if method == "POST" and path == "sync":
                self.sync_now()
                return 200, self.status()
            return 404, {"error": "not found"}
        except RatatoskError as e:
            return {"not-configured": 409, "bad-credentials": 401, "not-signed-in": 401, "network": 503, "protocol": 502, "not-found": 404}.get(e.code, 400), {"error": str(e), "code": e.code}
        except ratatosk_group_error() as e:
            return {"not-found": 404, "bad": 422, "refused": 403, "not-signed-in": 401}.get(e.code, 400), {"error": str(e), "code": e.code}
        except ratatosk_attach_error() as e:
            return {"not-found": 404, "too-big": 413, "unsupported": 415, "network": 503, "protocol": 502, "no-card": 409}.get(e.code, 422), {"error": str(e), "code": e.code}
        except RuntimeError as e:   # a message that could not be sent (the group is encrypted and it could not be), an edit of somebody else's message
            return 409, {"error": str(e), "code": "failed"}
