# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Ratatosk on IntelliCat, stage 4: the text chat with end-to-end encryption (docs/usage.md; E2E_SPEC.md and the Android client are the references).

The engine behind `/api/rt/chat`, `send`, `read`, `edit`, `delete` of runtime/ratatosk.py: where the keys live on the server, the silent pinning of the contacts' keys, enabling
encryption for a group, reading the day files of a chat (`chats/<gid>/<yyyy-MM-dd>.jsonl`, UTC) with their edit / delete / reaction events, showing a message decrypted, as legacy
plaintext or not at all, sending a message (encrypted whenever the group is), the unread counters. The cryptography is runtime/ratatosk_e2e.py.

Rules kept from the spec: a message that cannot be verified or opened is never shown and never counted (it is only logged); a group whose encryption state cannot be read is never
guessed to be plain; a failed read is never taken for "no such file" (so nothing is created over a record or a meta that exists); the identity is unwrapped from the password in
memory only. This module never creates a user's key record: a key record is made by the phone apps at their login, together with the e-mail of the recovery secret (spec section 3), and
an e-mail is not sent from here.
"""
import base64, json, threading, time, uuid
from datetime import datetime, timedelta, timezone

import ratatosk_e2e as e2e

PIN_OK, PIN_NEW, PIN_MISMATCH = "ok", "new", "mismatch"
ACCESS_EVERY_S = 300
MISS_TTL_S = 30
HISTORY_DAYS = 14


def day(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def new_message_id():
    """Short, roughly time-ordered, URL-safe: the time in base 36 and five random hex digits (as the phone makes them)."""
    return _base36(int(time.time() * 1000)) + uuid.uuid4().hex[:5]


def _base36(n):
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + s
    return s or "0"


def attachment_type(att):
    """image | audio | video | file: the phone writes `type`, the spec's spelling has `mime`; anything else is a file."""
    t = str(att.get("type") or "").lower()
    if t in ("image", "audio", "video", "file"):
        return t
    mime = str(att.get("mime") or "").lower()
    for p in ("image", "audio", "video"):
        if mime.startswith(p + "/"):
            return p
    return "file"


def public_attachments(atts):
    """What the page may see of a message's attachments: the kind, the name, the size, whether there is a thumbnail, the picture's size, and the position `i`.
    NEVER the key and never the address of the file: those stay in the module, which fetches and decrypts on request."""
    out = []
    for i, a in enumerate(atts if isinstance(atts, list) else []):
        if not isinstance(a, dict) or not a.get("url"):
            continue
        out.append({"i": i, "type": attachment_type(a), "name": str(a.get("name") or "file")[:120], "size": int(a.get("size") or 0),
                    "thumb": bool(a.get("thumb")), "w": int(a.get("w") or 0), "h": int(a.get("h") or 0), "encrypted": bool(a.get("key"))})
    return out


class Chats:
    def __init__(self, rt):
        self.rt = rt
        self.me = None
        self.identity = None
        self.keys, self.metas = {}, {}                # (gid, kid) -> key ; gid -> meta
        self.meta_miss, self.key_miss = {}, {}        # when a lookup came back empty
        self.contact_cache = {}                       # email -> (time, record or None)
        self.last_access = {}
        self.hidden = set()                           # (gid, id) already logged
        self.hidden_count = 0
        self.lock = threading.RLock()
        rt.db.executescript(
            "CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, chat TEXT, sender TEXT, ts INTEGER, text TEXT, reply TEXT, attachments TEXT, edited_at INTEGER,"
            " deleted_at INTEGER, reactions TEXT DEFAULT '{}', ev_ts INTEGER DEFAULT 0, secured INTEGER DEFAULT 0);"
            "CREATE INDEX IF NOT EXISTS messages_chat ON messages (chat, ts);"
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, chat TEXT, target TEXT, type TEXT, ts INTEGER, text TEXT, likes TEXT, applied INTEGER DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS pins (email TEXT PRIMARY KEY, pub TEXT, sign_pub TEXT, pinned_at INTEGER);"
            "CREATE TABLE IF NOT EXISTS chat_state (chat TEXT PRIMARY KEY, last_read INTEGER DEFAULT 0, synced INTEGER DEFAULT 0, history INTEGER DEFAULT 0);")
        try:   # chats gained a preview in this stage
            rt.db.execute("ALTER TABLE chats ADD COLUMN preview TEXT")
        except Exception:
            pass

    # ---- the files of ReForce ------------------------------------------------------------------------------------------
    def read_file(self, path):
        """-> ("found", text) | ("notfound", None) | ("error", reason): a failed read is NOT "no such file" (nothing may be created over it)."""
        try:
            resp = self.rt.send_auth({"command": "file:read", "path": path})
        except Exception as ex:
            return "error", str(ex)
        st = str(resp.get(".status", ""))
        if st == "ok":                            # v2 file:read: {"path", "content": base64}
            try:
                return "found", base64.b64decode(resp.get("content") or "").decode("utf-8", "replace")
            except Exception:
                return "error", "bad content"
        reason = str(resp.get(".reason", ""))
        if st == "error" and (str(resp.get(".errcode")) == "102" or "not found" in reason.lower()):
            return "notfound", None
        return "error", f"status={st[:40]} reason={reason}"

    def write_file(self, path, text):
        resp = self.rt.send_auth({"command": "file:write", "path": path, "content": base64.b64encode(text.encode("utf-8")).decode()})
        return resp.get(".status") == "ok"

    def append_line(self, path, line):
        # (v2 file:append has no server-side de-duplication by "id"; a line that comes twice after a retry is one message here: the store is keyed by its id)
        resp = self.rt.send_auth({"command": "file:append", "path": path, "content": base64.b64encode(line.encode("utf-8")).decode()})
        return resp.get(".status") == "ok"

    # ---- who I am (the identity lives in memory only) -----------------------------------------------------------------
    def unlock(self, email, password):
        """Unwrap the user's own private keys with the password. No key record, or one that does not open: no identity (the log says which); never creates one."""
        email = email.strip().lower()
        with self.lock:
            self.me, self.identity = email, None
        st, text = self.read_file(f"users/{email}/e2e_keys.json")
        if st != "found":
            self.rt.log(f"ratatosk: no key record of {email} ({st}): encrypted chats are not readable until the phone app has made one")
            return None
        try:
            ident = e2e.unwrap_with_password(json.loads(text), password)
        except (e2e.E2eError, ValueError, KeyError) as ex:
            self.rt.log(f"ratatosk: the key record of {email} did not open: {ex}")
            return None
        with self.lock:
            self.identity = ident
        return ident

    def ident(self):
        a = self.rt._account()
        if not a:
            return None
        if self.identity is None or self.me != a["email"]:
            if self.me == a["email"] and time.time() - getattr(self, "_tried", 0) < 60:
                return None
            self._tried = time.time()
            self.unlock(a["email"], a["password"])
        return self.identity

    def forget(self):
        with self.lock:
            self.me, self.identity = None, None
            self.keys.clear(); self.metas.clear(); self.meta_miss.clear(); self.key_miss.clear(); self.contact_cache.clear(); self.last_access.clear(); self.hidden.clear()
            self.rt.db.executescript("DELETE FROM messages; DELETE FROM events; DELETE FROM pins; DELETE FROM chat_state;")
            self.rt.db.commit()

    # ---- the keys of the other people, pinned silently on first sight (spec 5.1) -----------------------------------------
    def contact_record(self, email):
        email = email.strip().lower()
        c = self.contact_cache.get(email)
        if c and time.time() - c[0] < 60:
            return c[1]
        st, text = self.read_file(f"users/{email}/e2e_keys.json")
        rec = None
        if st == "found":
            try:
                rec = json.loads(text)
                if rec.get("v") != 1 or not rec.get("pub") or not rec.get("sign_pub"):
                    rec = None
            except ValueError:
                rec = None
        if st != "error":
            self.contact_cache[email] = (time.time(), rec)
        return rec

    def check_pin(self, email, pub, sign_pub):
        email = email.strip().lower()
        with self.rt.lock:
            row = self.rt.db.execute("SELECT pub, sign_pub FROM pins WHERE email=?", (email,)).fetchone()
            if row is None:
                self.rt.db.execute("INSERT INTO pins (email, pub, sign_pub, pinned_at) VALUES (?,?,?,?)", (email, pub, sign_pub, int(time.time())))
                self.rt.db.commit()
                return PIN_NEW
        if row == (pub, sign_pub):
            return PIN_OK
        self.rt.log(f"ratatosk: pin mismatch for {email}: not trusting the current record")
        return PIN_MISMATCH

    def resolve(self, email):
        """-> ("ok", x_pub, sign_pub) | ("no-record",) | ("mismatch",)"""
        rec = self.contact_record(email)
        if rec is None:
            return ("no-record",)
        if self.check_pin(email, rec["pub"], rec["sign_pub"]) == PIN_MISMATCH:
            return ("mismatch",)
        return ("ok", e2e.b64u_decode(rec["pub"]), e2e.b64u_decode(rec["sign_pub"]))

    def sender_sign_pub(self, email):
        if email.strip().lower() == self.me:
            return self.identity.sign_pub if self.identity else None
        with self.rt.lock:
            row = self.rt.db.execute("SELECT sign_pub FROM pins WHERE email=?", (email.strip().lower(),)).fetchone()
        if row:
            return e2e.b64u_decode(row[0])
        r = self.resolve(email)
        return r[2] if r[0] == "ok" else None

    # ---- groups: meta and sealed keys (spec 4, 5) -----------------------------------------------------------------------
    def meta_state(self, gid, refresh=False):
        """-> ("encrypted", meta) | ("plain", None) | ("unknown", None)"""
        if not refresh:
            if gid in self.metas:
                return "encrypted", self.metas[gid]
            if time.time() - self.meta_miss.get(gid, 0) < MISS_TTL_S:
                return "plain", None
        st, text = self.read_file(f"chats/{gid}/e2e/meta.json")
        if st == "notfound":
            self.meta_miss[gid] = time.time()
            return "plain", None
        if st == "error":
            self.rt.log(f"ratatosk: group meta read failed gid={gid}: {text}")
            return "unknown", None
        try:
            m = json.loads(text)
            if m.get("v") != 1:
                raise ValueError("version")
            meta = {"enabled_from": int(m["enabled_from"]), "current_kid": m["current_kid"], "kids": list(m.get("kids") or [m["current_kid"]])}
        except (ValueError, KeyError, TypeError):
            self.rt.log(f"ratatosk: corrupt group meta for {gid}")
            return "unknown", None
        self.meta_miss.pop(gid, None)
        self.metas[gid] = meta
        return "encrypted", meta

    def group_key(self, gid, kid):
        ident = self.ident()
        if ident is None:
            return None
        if (gid, kid) in self.keys:
            return self.keys[(gid, kid)]
        if time.time() - self.key_miss.get((gid, kid), 0) < MISS_TTL_S:
            return None
        st, text = self.read_file(f"chats/{gid}/e2e/keys/{kid}/{self.me}.json")
        if st != "found":
            self.key_miss[(gid, kid)] = time.time()
            return None
        try:
            key = e2e.open_group_key(json.loads(text), gid, kid, self.me, ident)
        except (e2e.E2eError, ValueError, KeyError):
            self.rt.log(f"ratatosk: cannot open the group key gid={gid} kid={kid}")
            return None
        self.keys[(gid, kid)] = key
        return key

    def seal_to(self, gid, kid, key, member):
        r = self.resolve(member)
        if r[0] != "ok":
            self.rt.log(f"ratatosk: not sealing gid={gid} kid={kid} to {member}: {r[0]}")
            return False
        sealed = e2e.seal_group_key(key, gid, kid, member.strip().lower(), r[1])
        return self.write_file(f"chats/{gid}/e2e/keys/{kid}/{member.strip().lower()}.json", json.dumps(sealed, separators=(",", ":")))

    def members_of(self, gid):
        resp = self.rt.send_auth({"command": "group:get", "group_id": gid})
        g = resp.get("group") if resp.get(".status") == "ok" else None
        if not isinstance(g, dict):
            return None
        out = []
        for k in ("admins", "members", "observers"):
            out += [p.strip().lower() for p in (g.get(k) or []) if isinstance(p, str)]
        return list(dict.fromkeys(out))

    def ensure_enabled(self, gid, members):
        """Enable encryption for a group that has none (spec 4): a new key sealed to me and to every member that has a key record, the meta written last.
        Idempotent; a meta that already exists (another client won) is used as it is."""
        with self.lock:
            st, meta = self.meta_state(gid, refresh=True)
            if st == "encrypted":
                return meta
            if st == "unknown" or self.ident() is None:
                return None
            kid, key = e2e.new_kid(), e2e.new_group_key()
            sealed_me = False
            for m in dict.fromkeys(x.strip().lower() for x in members + [self.me]):
                ok = self.seal_to(gid, kid, key, m)
                sealed_me = sealed_me or (ok and m == self.me)
            if not sealed_me:
                self.rt.log(f"ratatosk: enabling gid={gid} failed: could not seal to myself")
                return None
            st, meta = self.meta_state(gid, refresh=True)   # somebody else may have enabled it while we were sealing: theirs wins
            if st == "encrypted":
                return meta
            if st == "unknown":
                return None
            meta = {"enabled_from": int(time.time() * 1000), "current_kid": kid, "kids": [kid]}
            if not self.write_file(f"chats/{gid}/e2e/meta.json", json.dumps({"v": 1, **meta}, separators=(",", ":"))):
                return None
            self.keys[(gid, kid)], self.metas[gid] = key, meta
            self.rt.log(f"ratatosk: encryption enabled for gid={gid} kid={kid} members={len(members)}")
            return meta

    def sync_member_keys(self, gid, members):
        """Seal every key I can open to the members that have no copy yet (a member added later, or one who only now made a key record)."""
        with self.lock:
            st, meta = self.meta_state(gid, refresh=True)
            if st != "encrypted":
                return
            for kid in meta["kids"]:
                key = self.group_key(gid, kid)
                if key is None:
                    continue
                for m in dict.fromkeys(x.strip().lower() for x in members):
                    if self.read_file(f"chats/{gid}/e2e/keys/{kid}/{m}.json")[0] == "found":
                        continue
                    self.seal_to(gid, kid, key, m)

    def rotate(self, gid, remaining, keep_me=True):
        """A member left or was removed (spec 5): a new key for the new messages, sealed to the members that remain (and me, unless I am the one leaving: `keep_me=False`) only; the old keys
        stay, so what was written is still readable by those who had it, the removed member keeps what they already had and gets nothing new.
        -> the new meta, or None (not encrypted, no identity, could not seal to me or write the meta)."""
        with self.lock:
            st, old = self.meta_state(gid, refresh=True)
            if st != "encrypted" or self.ident() is None:
                return None
            kid, key, sealed_me = e2e.new_kid(), e2e.new_group_key(), not keep_me
            others = [x.strip().lower() for x in remaining if x.strip().lower() != self.me]
            for m in dict.fromkeys(others + ([self.me] if keep_me else [])):
                ok = self.seal_to(gid, kid, key, m)
                sealed_me = sealed_me or (ok and m == self.me)
            if not sealed_me:
                self.rt.log(f"ratatosk: rotating gid={gid} failed: could not seal to myself")
                return None
            meta = {"enabled_from": old["enabled_from"], "current_kid": kid, "kids": list(old["kids"]) + [kid]}
            if not self.write_file(f"chats/{gid}/e2e/meta.json", json.dumps({"v": 1, **meta}, separators=(",", ":"))):
                return None
            self.keys[(gid, kid)], self.metas[gid] = key, meta
            self.rt.log(f"ratatosk: group key rotated gid={gid} kid={kid}")
            return meta

    def on_access(self, gid, force=False):
        """The first access of a group by this client (spec 4): enable encryption, keep the members' sealed copies complete. At most every five minutes per chat."""
        now = time.time()
        if not force and now - self.last_access.get(gid, 0) < ACCESS_EVERY_S:
            return
        self.last_access[gid] = now
        if self.ident() is None:
            return
        members = self.members_of(gid)
        if not members:
            return
        if self.meta_state(gid, refresh=True)[0] == "plain":
            self.ensure_enabled(gid, members)
        else:
            self.sync_member_keys(gid, members)

    # ---- one message: out and in -----------------------------------------------------------------------------------------
    def ready(self, gid):
        """-> ("ok", meta) | ("failed", the reason): can this account write to the chat at all (the encryption state is readable, the account has keys, the chat is encrypted or gets
        encryption now, and the key of the chat is available)? Asked before anything is uploaded, so that a file is never stored for a message that cannot be sent."""
        st, meta = self.meta_state(gid)
        if st == "unknown":
            return ("failed", "the encryption state of this chat could not be read: nothing was sent")
        if self.ident() is None:
            return ("failed", "this account has no encryption keys on the server: sign in once in the Ratatosk phone app, then try again. Nothing was sent")
        if st == "plain":                         # a chat that has no encryption yet: the first use enables it (spec 4), as the phones do
            members = self.members_of(gid)
            if not members:
                return ("failed", "the members of this chat could not be read: nothing was sent")
            meta = self.ensure_enabled(gid, members)
            if meta is None:
                st2, meta = self.meta_state(gid, refresh=True)
                if st2 != "encrypted":
                    return ("failed", "encryption could not be enabled in this chat: nothing was sent")
        if self.group_key(gid, meta["current_kid"]) is None:
            return ("failed", "the key of this chat is not available to this account yet: nothing was sent")
        return ("ok", meta)

    def encrypt_outgoing(self, gid, mid, ts, plain):
        """-> ("secured", kid, e2, sig) | ("failed", the reason). Encryption is always on (owner, 2026-10-27, no switch): a message that cannot be encrypted is not
        sent at all, never sent in the clear, whatever the reason (no keys of this account on the server, a state that cannot be read, a key not yet sealed to us)."""
        r = self.ready(gid)
        if r[0] == "failed":
            return r
        meta = r[1]
        key = self.group_key(gid, meta["current_kid"])
        s = e2e.encrypt_message(key, gid, mid, self.me, ts, meta["current_kid"], plain, self.ident().sign_seed)
        return ("secured", meta["current_kid"], s["e2"], s["sig"])

    def hide(self, gid, mid, reason):
        if (gid, mid) not in self.hidden:
            self.hidden.add((gid, mid))
            self.hidden_count += 1
            self.rt.log(f"ratatosk: hidden message chat={gid} id={mid}: {reason}")
        return ("hidden", reason)

    def open_incoming(self, gid, obj, mid, sender, ts):
        """-> ("clear",) | ("opened", content dict) | ("hidden", why): the rule of spec section 8."""
        if str(obj.get("secured", "")) != "true":
            st, meta = self.meta_state(gid)
            if st == "unknown":
                return self.hide(gid, mid, "the group's encryption state is unknown")
            if st == "plain" or ts < meta["enabled_from"]:
                return ("clear",)
            return self.hide(gid, mid, "plaintext after encryption was enabled")
        kid = str(obj.get("kid") or "")
        if not kid:
            return self.hide(gid, mid, "missing kid")
        if str(obj.get("v", "")) != e2e.VERSION:
            return self.hide(gid, mid, f"unsupported version {obj.get('v')}")
        key = self.group_key(gid, kid)
        if key is None:
            return self.hide(gid, mid, f"no key for kid={kid}")
        sp = self.sender_sign_pub(sender)
        if sp is None:
            return self.hide(gid, mid, f"no trusted signing key for {sender}")
        plain, why = e2e.decrypt_message(key, gid, mid, sender, ts, kid, obj.get("e2"), obj.get("sig"), sp)
        if plain is None:
            return self.hide(gid, mid, f"verification failed: {why}")
        try:
            content = json.loads(plain)
            if not isinstance(content, dict):
                raise ValueError
        except ValueError:
            return self.hide(gid, mid, "the content is not JSON")
        return ("opened", content)

    # ---- reading ---------------------------------------------------------------------------------------------------------
    def _line_to_store(self, gid, obj):
        mid, ts, sender = str(obj.get("id") or ""), int(obj.get("ts") or 0), str(obj.get("from") or "").strip()
        if not mid or ts <= 0 or not sender or sender == "null":
            return
        typ = str(obj.get("type") or "")
        if typ in ("edit", "delete", "reaction"):
            target = str(obj.get("targetId") or "")
            if not target:
                return
            text = None
            if typ == "edit":
                r = self.open_incoming(gid, obj, mid, sender, ts)
                if r[0] == "hidden":
                    return
                text = r[1].get("text", "") if r[0] == "opened" else str(obj.get("text") or "")
            likes = json.dumps(obj["likes"]) if isinstance(obj.get("likes"), dict) else None
            with self.rt.lock:
                self.rt.db.execute("INSERT OR IGNORE INTO events (id, chat, target, type, ts, text, likes) VALUES (?,?,?,?,?,?,?)", (mid, gid, target, typ, ts, text, likes))
            return
        if typ == "call":
            text, reply, atts, secured = "(call)", None, None, 0
        else:
            r = self.open_incoming(gid, obj, mid, sender, ts)
            if r[0] == "hidden":
                return
            if r[0] == "opened":
                c = r[1]
                text, reply, atts, secured = c.get("text", ""), c.get("reply"), c.get("attachments"), 1
            else:
                text, reply, atts, secured = str(obj.get("text") or ""), obj.get("replyTo"), obj.get("attachments"), 0
        with self.rt.lock:
            self.rt.db.execute("INSERT OR IGNORE INTO messages (id, chat, sender, ts, text, reply, attachments, secured) VALUES (?,?,?,?,?,?,?,?)",
                               (mid, gid, sender, ts, text, json.dumps(reply) if reply else None, json.dumps(atts) if atts else None, secured))

    def _apply_events(self, gid):
        with self.rt.lock:
            evs = self.rt.db.execute("SELECT id, target, type, ts, text, likes FROM events WHERE chat=? AND applied=0 ORDER BY ts", (gid,)).fetchall()
            for eid, target, typ, ts, text, likes in evs:
                row = self.rt.db.execute("SELECT ev_ts FROM messages WHERE id=? AND chat=?", (target, gid)).fetchone()
                if row is None:
                    continue                      # the message is not here (yet): the event waits
                if typ == "edit":
                    self.rt.db.execute("UPDATE messages SET text=?, edited_at=?, ev_ts=MAX(ev_ts, ?) WHERE id=?", (text, ts, ts, target))
                elif typ == "delete":
                    self.rt.db.execute("UPDATE messages SET deleted_at=?, ev_ts=MAX(ev_ts, ?) WHERE id=?", (ts, ts, target))
                elif typ == "reaction" and likes is not None and ts >= row[0]:
                    self.rt.db.execute("UPDATE messages SET reactions=?, ev_ts=? WHERE id=?", (likes, ts, target))
                self.rt.db.execute("UPDATE events SET applied=1 WHERE id=?", (eid,))
            self.rt.db.commit()

    def _read_day(self, gid, date):
        st, text = self.read_file(f"chats/{gid}/{date}.jsonl")
        if st != "found":
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                try:
                    self._line_to_store(gid, obj)
                except Exception as ex:           # one bad line never stops the rest
                    self.rt.log(f"ratatosk: a line of {gid} {date} was skipped: {ex!r}")

    def sync_messages(self, gid, force_access=False):
        """Yesterday, today and tomorrow (the time zones of the senders), as the phone does; on the first look at a chat also the last days. Then the events, the counters."""
        self.on_access(gid, force=force_access)
        now = int(time.time() * 1000)
        with self.rt.lock:
            row = self.rt.db.execute("SELECT history FROM chat_state WHERE chat=?", (gid,)).fetchone()
        days = [day(now + d * 86_400_000) for d in (-1, 0, 1)]
        if not row or not row[0]:
            days = [day(now - d * 86_400_000) for d in range(HISTORY_DAYS, 1, -1)] + days
        for d in dict.fromkeys(days):
            self._read_day(gid, d)
        self._apply_events(gid)
        with self.rt.lock:
            self.rt.db.execute("INSERT INTO chat_state (chat, history, synced) VALUES (?,1,?) ON CONFLICT(chat) DO UPDATE SET history=1, synced=excluded.synced", (gid, now))
            self.rt.db.commit()
        self.refresh_summary(gid)

    def refresh_summary(self, gid):
        """The unread count, the last message and its time, from the messages that are shown (spec 8.4)."""
        with self.rt.lock:
            lr = (self.rt.db.execute("SELECT last_read FROM chat_state WHERE chat=?", (gid,)).fetchone() or (0,))[0]
            unread = self.rt.db.execute("SELECT COUNT(*) FROM messages WHERE chat=? AND sender!=? AND ts>? AND deleted_at IS NULL", (gid, self.me or "", lr)).fetchone()[0]
            last = self.rt.db.execute("SELECT text, ts FROM messages WHERE chat=? AND deleted_at IS NULL ORDER BY ts DESC LIMIT 1", (gid,)).fetchone()
            if last:
                self.rt.db.execute("UPDATE chats SET unread=?, preview=?, last_at=MAX(COALESCE(last_at,0), ?) WHERE id=?", (unread, (last[0] or "")[:80], last[1], gid))
            else:
                self.rt.db.execute("UPDATE chats SET unread=? WHERE id=?", (unread, gid))
            self.rt.db.commit()

    def messages(self, gid, limit=200):
        with self.rt.lock:
            rows = self.rt.db.execute("SELECT id, sender, ts, text, reply, attachments, edited_at, reactions FROM messages WHERE chat=? AND deleted_at IS NULL ORDER BY ts DESC LIMIT ?",
                                      (gid, limit)).fetchall()
        out = []
        for mid, sender, ts, text, reply, atts, edited, reactions in reversed(rows):
            out.append({"id": mid, "from": sender, "mine": sender == self.me, "ts": ts, "text": text or "", "reply": json.loads(reply) if reply else None,
                        "attachments": public_attachments(json.loads(atts) if atts else []), "edited": bool(edited), "reactions": json.loads(reactions or "{}")})
        return out

    def mark_read(self, gid):
        with self.rt.lock:
            last = self.rt.db.execute("SELECT MAX(ts) FROM messages WHERE chat=?", (gid,)).fetchone()[0] or 0
            self.rt.db.execute("INSERT INTO chat_state (chat, last_read) VALUES (?,?) ON CONFLICT(chat) DO UPDATE SET last_read=MAX(last_read, excluded.last_read)", (gid, last))
            self.rt.db.commit()
        self.refresh_summary(gid)

    # ---- writing ---------------------------------------------------------------------------------------------------------
    def _send_line(self, gid, obj, ts):
        return self.append_line(f"chats/{gid}/{day(ts)}.jsonl", json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    def send(self, gid, text, reply=None, attachments=None):
        """-> the id of the message. Raises RuntimeError when the group is encrypted and this message could not be (it is not sent in the clear).
        `attachments`: the list the files were already stored for (runtime/ratatosk_attach.py), which travels inside the encrypted content."""
        text = (text or "").strip()
        if not text and not attachments:
            raise RuntimeError("an empty message")
        if self.me is None:
            self.ident()
        ts, mid = int(time.time() * 1000), new_message_id()
        rep = None
        if reply:
            rep = {k: reply[k] for k in ("id", "from", "text") if reply.get(k)}
        out = self.encrypt_outgoing(gid, mid, ts, e2e.plain_json(text, rep, attachments))
        if out[0] == "failed":
            raise RuntimeError(out[1])
        line = {"id": mid, "ts": ts, "from": self.me, "text": "", "secured": "true", "kid": out[1], "v": e2e.VERSION, "e2": out[2], "sig": out[3]}
        if not self._send_line(gid, line, ts):
            raise RuntimeError("the server did not take the message")
        self.rt.send_auth({"command": "var:set", "path": f"sw_lastmsg/{gid}", "value": str(ts)})
        with self.rt.lock:
            self.rt.db.execute("INSERT OR IGNORE INTO messages (id, chat, sender, ts, text, reply, attachments, secured) VALUES (?,?,?,?,?,?,?,?)",
                               (mid, gid, self.me, ts, text, json.dumps(rep) if rep else None, json.dumps(attachments) if attachments else None, 1))
            self.rt.db.commit()
        self.refresh_summary(gid)
        return mid

    def _event(self, gid, typ, target, text=None, likes=None):
        ts, eid = int(time.time() * 1000), new_message_id()
        line = {"id": eid, "ts": ts, "from": self.me, "type": typ, "targetId": target}
        if typ == "edit" and text is not None:
            out = self.encrypt_outgoing(gid, eid, ts, e2e.plain_json(text))
            if out[0] == "failed":
                raise RuntimeError(out[1])
            line.update({"text": "", "secured": "true", "kid": out[1], "v": e2e.VERSION, "e2": out[2], "sig": out[3]})
        if likes is not None:
            line["likes"] = likes
        if not self._send_line(gid, line, ts):
            raise RuntimeError("the server did not take it")
        with self.rt.lock:
            self.rt.db.execute("INSERT OR IGNORE INTO events (id, chat, target, type, ts, text, likes) VALUES (?,?,?,?,?,?,?)",
                               (eid, gid, target, typ, ts, text, json.dumps(likes) if likes is not None else None))
        self._apply_events(gid)
        self.refresh_summary(gid)

    def edit(self, gid, target, text):
        self._owned(gid, target)
        self._event(gid, "edit", target, text=text)

    def delete(self, gid, target):
        self._owned(gid, target)
        self._event(gid, "delete", target)

    def _owned(self, gid, target):
        with self.rt.lock:
            row = self.rt.db.execute("SELECT sender FROM messages WHERE id=? AND chat=?", (target, gid)).fetchone()
        if not row or row[0] != self.me:
            raise RuntimeError("only your own messages can be changed")
