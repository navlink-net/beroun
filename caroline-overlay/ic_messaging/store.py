# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The message store of Communicator and of Caroline: conversations, messages, drafts and the change log, in one SQLite file.

One store for both users of the accounts (the app and her tools), so what one side sent or read is there for the other. Messages are
deduplicated by (account, external id); a message the service reports back as an echo of one that was just sent is matched to the stored
pending message instead of being added twice. Times are seconds since the epoch (float).
"""
import json, sqlite3, threading, time

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, account TEXT NOT NULL, service TEXT NOT NULL, role TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'dm',
  title TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '', muted INTEGER NOT NULL DEFAULT 0,
  unread INTEGER NOT NULL DEFAULT 0, last_ts REAL NOT NULL DEFAULT 0, last_text TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS conv_last ON conversations(last_ts DESC);
CREATE INDEX IF NOT EXISTS conv_account ON conversations(account);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conv TEXT NOT NULL, account TEXT NOT NULL, ext_id TEXT, client_ref TEXT,
  sender TEXT NOT NULL DEFAULT '', sender_name TEXT NOT NULL DEFAULT '', ts REAL NOT NULL, text TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '', outgoing INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'sent', reply_to INTEGER,
  actor TEXT NOT NULL DEFAULT '', attachments TEXT NOT NULL DEFAULT '[]', read INTEGER NOT NULL DEFAULT 1);
CREATE UNIQUE INDEX IF NOT EXISTS msg_ext ON messages(account, ext_id) WHERE ext_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS msg_conv ON messages(conv, ts, id);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (conv TEXT PRIMARY KEY, text TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
ECHO_WINDOW_S = 180.0      # an echo of a message we sent is matched to the pending one if it comes within this time
EVENTS_KEEP = 5000


def _row(cur, r):
    d = {c[0]: r[i] for i, c in enumerate(cur.description)}
    for k in ("outgoing", "muted", "read"):
        if k in d:
            d[k] = bool(d[k])
    if "attachments" in d:
        d["attachments"] = json.loads(d["attachments"] or "[]")
    if "extra" in d:
        d["extra"] = json.loads(d["extra"] or "{}")
    return d


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = None
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)

    def close(self):
        with self.lock:
            self.db.close()

    def _all(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            return [_row(cur, r) for r in cur.fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # ---- conversations ---------------------------------------------------------------------------------------------
    def upsert_conversation(self, conv):
        """conv: id, account, service, role, kind, title, avatar, muted, extra. Counters and the last line are kept."""
        with self.lock:
            old = self._one("SELECT * FROM conversations WHERE id=?", (conv["id"],))
            if old is None:
                self.db.execute(
                    "INSERT INTO conversations(id,account,service,role,kind,title,avatar,muted,extra,last_ts,last_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (conv["id"], conv["account"], conv["service"], conv["role"], conv.get("kind", "dm"), conv.get("title", ""), conv.get("avatar", ""),
                     int(bool(conv.get("muted"))), json.dumps(conv.get("extra") or {}), conv.get("last_ts", 0), conv.get("last_text", "")))
            else:
                self.db.execute("UPDATE conversations SET kind=?,title=?,avatar=?,muted=?,extra=? WHERE id=?",
                                (conv.get("kind", old["kind"]), conv.get("title", old["title"]), conv.get("avatar", old["avatar"]),
                                 int(bool(conv.get("muted", old["muted"]))), json.dumps(conv.get("extra", old["extra"])), conv["id"]))
            return self.get_conversation(conv["id"])

    def get_conversation(self, cid):
        return self._one("SELECT * FROM conversations WHERE id=?", (cid,))

    def list_conversations(self, account=None, service=None, role=None, q=None, limit=200, offset=0, unread_only=False):
        where, args = [], []
        if account:
            where.append("account=?"); args.append(account)
        if service:
            where.append("service=?"); args.append(service)
        if role:
            where.append("role=?"); args.append(role)
        if q:
            where.append("(title LIKE ? OR last_text LIKE ?)"); args += [f"%{q}%"] * 2
        if unread_only:
            where.append("unread>0")
        sql = "SELECT * FROM conversations" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY last_ts DESC, id LIMIT ? OFFSET ?"
        return self._all(sql, (*args, int(limit), int(offset)))

    def find_conversation(self, text, account=None, service=None, role=None):
        """A conversation by id, exact title or a part of the title (the most recently active one)."""
        c = self.get_conversation(text)
        if c:
            return c
        rows = self.list_conversations(account=account, service=service, role=role, limit=2000)
        low = str(text).lower()
        for r in rows:
            if r["title"].lower() == low:
                return r
        for r in rows:
            if low in r["title"].lower():
                return r
        return None

    def delete_account_data(self, account):
        with self.lock:
            self.db.execute("DELETE FROM messages WHERE account=?", (account,))
            self.db.execute("DELETE FROM conversations WHERE account=?", (account,))

    # ---- messages --------------------------------------------------------------------------------------------------
    def add_message(self, m):
        """Returns (message, created). m: conv, account, ext_id|None, client_ref|None, sender, sender_name, ts, text, subject, outgoing,
        state, reply_to, actor, attachments, read."""
        with self.lock:
            ext = m.get("ext_id")
            if ext is not None:
                have = self._one("SELECT * FROM messages WHERE account=? AND ext_id=?", (m["account"], str(ext)))
                if have:
                    self._merge(have, m)
                    return self._one("SELECT * FROM messages WHERE id=?", (have["id"],)), False
                if m.get("outgoing") and not m.get("client_ref"):    # the echo of what was just sent from here
                    want = " ".join(str(m.get("text", "")).split())
                    pend = None
                    for cand in self._all("SELECT * FROM messages WHERE conv=? AND outgoing=1 AND ext_id IS NULL AND ts>=? ORDER BY ts",
                                          (m["conv"], float(m["ts"]) - ECHO_WINDOW_S)):
                        have = " ".join(cand["text"].split())
                        # equal, or the service added something (a mail signature, a quoted history) after what was typed
                        if have == want or (have and want.startswith(have)):
                            pend = cand
                            break
                    if pend:
                        self.db.execute("UPDATE messages SET ext_id=?, state=CASE WHEN state='queued' THEN 'sent' ELSE state END WHERE id=?",
                                        (str(ext), pend["id"]))
                        return self._one("SELECT * FROM messages WHERE id=?", (pend["id"],)), False
            if m.get("client_ref"):
                have = self._one("SELECT * FROM messages WHERE account=? AND client_ref=?", (m["account"], m["client_ref"]))
                if have:
                    self._merge(have, m)
                    return self._one("SELECT * FROM messages WHERE id=?", (have["id"],)), False
            read = 1 if m.get("outgoing") else int(bool(m.get("read", False)))
            cur = self.db.execute(
                "INSERT INTO messages(conv,account,ext_id,client_ref,sender,sender_name,ts,text,subject,outgoing,state,reply_to,actor,attachments,read) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (m["conv"], m["account"], None if ext is None else str(ext), m.get("client_ref"), m.get("sender", ""), m.get("sender_name", ""),
                 float(m["ts"]), m.get("text", ""), m.get("subject", ""), int(bool(m.get("outgoing"))), m.get("state", "sent"),
                 m.get("reply_to"), m.get("actor", ""), json.dumps(m.get("attachments") or []), read))
            mid = cur.lastrowid
            line = (m.get("text") or "").strip().replace("\n", " ")
            if not line and m.get("attachments"):
                line = "[" + (m["attachments"][0].get("kind") or "file") + "]"
            self.db.execute("UPDATE conversations SET last_ts=MAX(last_ts,?), last_text=CASE WHEN ?>=last_ts THEN ? ELSE last_text END, unread=unread+? WHERE id=?",
                            (float(m["ts"]), float(m["ts"]), line[:200], 0 if read else 1, m["conv"]))
            return self._one("SELECT * FROM messages WHERE id=?", (mid,)), True

    def _merge(self, have, m):
        """A second sighting of a message: only the delivery state (never backwards) and a missing external id are taken."""
        order = ["queued", "sent", "delivered", "read"]
        state = m.get("state")
        if state and state in order and have["state"] in order and order.index(state) > order.index(have["state"]):
            self.db.execute("UPDATE messages SET state=? WHERE id=?", (state, have["id"]))
        if m.get("ext_id") is not None and have["ext_id"] is None:
            self.db.execute("UPDATE messages SET ext_id=? WHERE id=?", (str(m["ext_id"]), have["id"]))

    def set_message_state(self, mid, state, ext_id=None):
        with self.lock:
            self.db.execute("UPDATE messages SET state=? WHERE id=?", (state, mid))
            if ext_id is not None:
                try:
                    self.db.execute("UPDATE messages SET ext_id=? WHERE id=? AND ext_id IS NULL", (str(ext_id), mid))
                except sqlite3.IntegrityError:    # the echo got there first: keep that row, drop this duplicate
                    self.db.execute("DELETE FROM messages WHERE id=?", (mid,))
            return self._one("SELECT * FROM messages WHERE id=?", (mid,))

    def get_message(self, mid):
        return self._one("SELECT * FROM messages WHERE id=?", (mid,))

    def history(self, conv, before=None, limit=50):
        """The newest `limit` messages older than message id `before`, oldest first."""
        args = [conv]
        sql = "SELECT * FROM messages WHERE conv=?"
        if before:
            # older than that message in TIME, not in insertion order: history is back-filled newest first
            ref = self._one("SELECT ts, id FROM messages WHERE id=?", (int(before),))
            if ref:
                sql += " AND (ts<? OR (ts=? AND id<?))"; args += [ref["ts"], ref["ts"], ref["id"]]
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        return list(reversed(self._all(sql, args)))

    def search(self, q, service=None, conv=None, role=None, since=None, limit=50):
        sql = ("SELECT m.*, c.title AS conv_title, c.service AS service, c.role AS role FROM messages m JOIN conversations c ON c.id=m.conv "
               "WHERE (m.text LIKE ? OR m.subject LIKE ?)")
        args = [f"%{q}%", f"%{q}%"]
        if service:
            sql += " AND c.service=?"; args.append(service)
        if role:
            sql += " AND c.role=?"; args.append(role)
        if conv:
            sql += " AND m.conv=?"; args.append(conv)
        if since:
            sql += " AND m.ts>=?"; args.append(float(since))
        sql += " ORDER BY m.ts DESC, m.id DESC LIMIT ?"
        args.append(int(limit))
        return self._all(sql, args)

    def mark_read(self, conv, upto=None):
        with self.lock:
            cur = self.db.execute("UPDATE messages SET read=1 WHERE conv=? AND read=0", (conv,))
            self.db.execute("UPDATE conversations SET unread=0 WHERE id=?", (conv,))
            return cur.rowcount

    def unread_total(self, role=None, service=None):
        sql, args = "SELECT COALESCE(SUM(unread),0) AS n FROM conversations WHERE muted=0", []
        if role:
            sql += " AND role=?"; args.append(role)
        if service:
            sql += " AND service=?"; args.append(service)
        return self._one(sql, args)["n"]

    def unread_by_conversation(self, role=None, limit=50):
        sql, args = "SELECT id,title,service,role,unread,last_ts,last_text FROM conversations WHERE unread>0", []
        if role:
            sql += " AND role=?"; args.append(role)
        sql += " ORDER BY last_ts DESC LIMIT ?"
        args.append(int(limit))
        return self._all(sql, args)

    # ---- drafts ----------------------------------------------------------------------------------------------------
    def set_draft(self, conv, text):
        with self.lock:
            if text:
                self.db.execute("INSERT INTO drafts(conv,text,ts) VALUES(?,?,?) ON CONFLICT(conv) DO UPDATE SET text=excluded.text, ts=excluded.ts",
                                (conv, text, time.time()))
            else:
                self.db.execute("DELETE FROM drafts WHERE conv=?", (conv,))

    def get_draft(self, conv):
        r = self._one("SELECT text FROM drafts WHERE conv=?", (conv,))
        return r["text"] if r else ""

    # ---- the change log (what the app polls) -----------------------------------------------------------------------
    def add_event(self, kind, data):
        with self.lock:
            cur = self.db.execute("INSERT INTO events(ts,kind,data) VALUES(?,?,?)", (time.time(), kind, json.dumps(data)))
            seq = cur.lastrowid
            if seq % 500 == 0:
                self.db.execute("DELETE FROM events WHERE seq<?", (seq - EVENTS_KEEP,))
            return seq

    def events_since(self, seq, limit=500):
        rows = self._all("SELECT seq,ts,kind,data FROM events WHERE seq>? ORDER BY seq LIMIT ?", (int(seq), int(limit)))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def last_seq(self):
        r = self._one("SELECT COALESCE(MAX(seq),0) AS s FROM events")
        return r["s"]

    def set_unread(self, conv, n):
        with self.lock:
            self.db.execute("UPDATE conversations SET unread=? WHERE id=?", (int(n), conv))

    def set_muted(self, conv, muted):
        """A muted conversation raises no notification and is left out of the unread total (its own count stays)."""
        with self.lock:
            cur = self.db.execute("UPDATE conversations SET muted=? WHERE id=?", (1 if muted else 0, conv))
            return cur.rowcount > 0

    def mark_read_from_service(self, conv):
        """The service says everything in this conversation was read (on another device): same as mark_read, kept apart for clarity."""
        return self.mark_read(conv)

    def has_ext(self, account, ext_id):
        return self._one("SELECT id FROM messages WHERE account=? AND ext_id=?", (account, str(ext_id))) is not None

    def update_message(self, mid, text=None, attachments=None, subject=None):
        with self.lock:
            if text is not None:
                self.db.execute("UPDATE messages SET text=? WHERE id=?", (text, mid))
            if subject is not None:
                self.db.execute("UPDATE messages SET subject=? WHERE id=?", (subject, mid))
            if attachments is not None:
                self.db.execute("UPDATE messages SET attachments=? WHERE id=?", (json.dumps(attachments), mid))
            m = self.get_message(mid)
            if m and text:
                last = self._one("SELECT id FROM messages WHERE conv=? ORDER BY ts DESC, id DESC LIMIT 1", (m["conv"],))
                if last and last["id"] == mid:
                    self.db.execute("UPDATE conversations SET last_text=? WHERE id=?", (text.strip().replace("\n", " ")[:200], m["conv"]))
            return m

    def messages_needing_body(self, conv, limit=30):
        return self._all("SELECT * FROM messages WHERE conv=? AND text='' AND ext_id IS NOT NULL AND attachments='[]' ORDER BY ts DESC LIMIT ?", (conv, int(limit)))
