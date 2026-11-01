# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Telegram: the adapter over Caroline's own `app.telegram_channel` (Telethon, a user account).

It owns nothing: the session file, the live client and the login are hers (`telegram_channel`); this adapter asks for the live client (`get_live_client`),
puts its own event handlers on it, reads dialogs and history through it and sends through it. So the app and her tools use one client and one session.
The channel module is looked up through `channel()` so that the tests can put a stand-in in its place.
"""
import asyncio, logging, os, time

from .adapter import Adapter, AdapterError

log = logging.getLogger("ic_messaging")
_channel = None            # tests set this to a stand-in module


def channel():
    if _channel is not None:
        return _channel
    from app import telegram_channel
    return telegram_channel


def _workspace():
    from . import _workspace as ws
    return ws()


def _cid(chat_id, msg_id):
    return f"{chat_id}:{msg_id}"


def _kind(entity_or_dialog):
    d = entity_or_dialog
    if getattr(d, "is_user", False):
        return "dm"
    if getattr(d, "is_channel", False) and getattr(d, "broadcast", False):
        return "channel"
    return "group"


def _attachments(msg):
    """What a stored message says about its media; the bytes are fetched on demand (download())."""
    media = getattr(msg, "media", None)
    if not media:
        return []
    kind = "photo" if getattr(msg, "photo", None) else "voice" if getattr(msg, "voice", None) else "video" if getattr(msg, "video", None) \
        else "audio" if getattr(msg, "audio", None) else "sticker" if getattr(msg, "sticker", None) else "file"
    f = getattr(msg, "file", None)
    return [{"kind": kind, "name": getattr(f, "name", None) or kind, "size": getattr(f, "size", None) or 0, "mime": getattr(f, "mime_type", None) or "",
             "ref": _cid(msg.chat_id, msg.id)}]


class TelegramAdapter(Adapter):
    MULTI = True                 # extra accounts: her channel module is extended with the name (channels.py)
    CHANNEL = True
    service = "telegram"
    POLL = 2.0                 # how often the watcher looks for her live client
    label = "Telegram"

    def __init__(self, role, slot=1):
        super().__init__()
        self.role = role
        self.slot = slot
        self._client = None
        self._watch = None
        self._synced = False
        self._login = None             # {"phase": "phone"|"code"|"password", "phone": ...}
        self._login_wait = None        # after the code was accepted: waiting for her loop to connect
        self._names = {}               # chat id -> title, from the dialogs

    # ---- state -----------------------------------------------------------------------------------------------------
    def _status(self):
        try:
            return channel().get_telegram_channel_status().get(self.acct, {})
        except Exception:
            return {}

    def capabilities(self):
        return {"history": True, "send": True, "files": True, "read_marks": True, "search": True}

    def state(self):
        ch = channel()
        if self._login:
            return {"state": "connecting", "detail": {"phone": "waiting for the phone number", "code": "waiting for the code", "password": "waiting for the password"}[self._login["phase"]], "since": None}
        st = self._status()
        try:
            has = ch.has_telegram_session(_workspace(), self.acct)
        except Exception:
            has = False
        if not has:
            return {"state": "not-linked", "detail": "", "since": None}
        if st.get("connected"):
            return {"state": "linked", "detail": "", "since": st.get("connected_since")}
        if self._login_wait and time.time() - self._login_wait < 90:
            return {"state": "connecting", "detail": "connecting", "since": None}
        if st.get("last_error"):
            return {"state": "needs-attention", "detail": str(st["last_error"])[:120], "since": None}
        return {"state": "connecting", "detail": "connecting", "since": None}

    # ---- life ------------------------------------------------------------------------------------------------------
    async def start(self):
        self._watch = asyncio.ensure_future(self._watcher())

    async def stop(self):
        if self._watch:
            self._watch.cancel()

    async def _watcher(self):
        """Her loop connects (and reconnects) on its own schedule: whenever a new live client is there, hook it and read the dialogs."""
        last_state = None
        while True:
            try:
                cl = channel().get_live_client(self.acct)
                if cl is not None and cl is not self._client:
                    self._client = cl
                    self._hook(cl)
                    self._synced = False
                    await self.sync()
                    self._login_wait = None
                if cl is None and self._client is not None:
                    self._client = None
                st = self.state()["state"]
                if st != last_state:
                    last_state = st
                    self.ctx.state_changed()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("telegram watcher %s: %r", self.acct, e)
            await asyncio.sleep(self.POLL)

    def _hook(self, cl):
        from telethon import events

        async def on_new(event):
            try:
                await self._store_event(event)
            except Exception as e:
                log.warning("telegram event: %r", e)

        async def on_read(event):
            try:
                if getattr(event, "inbox", False) and self.ctx.read_elsewhere:
                    self.ctx.read_elsewhere(str(event.chat_id))
            except Exception as e:
                log.warning("telegram read event: %r", e)

        cl.add_event_handler(on_new, events.NewMessage())
        cl.add_event_handler(on_read, events.MessageRead())

    # ---- messages --------------------------------------------------------------------------------------------------
    async def _store_event(self, event):
        msg = event.message
        chat = await event.get_chat() if hasattr(event, "get_chat") else None
        title = self._title(chat) or self._names.get(str(event.chat_id), str(event.chat_id))
        self._names[str(event.chat_id)] = title
        sender = None if msg.out else (await event.get_sender() if hasattr(event, "get_sender") else None)
        self._ingest(str(event.chat_id), title, _kind(chat) if chat is not None else "dm", msg, sender)

    @staticmethod
    def _title(entity):
        if entity is None:
            return ""
        t = getattr(entity, "title", None)
        if t:
            return t
        return " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)])) or getattr(entity, "username", None) or ""

    def _ingest(self, chat_id, title, kind, msg, sender=None, read=None):
        text = (getattr(msg, "raw_text", None) or getattr(msg, "message", None) or "").strip()
        att = _attachments(msg)
        if not text and not att:
            return None
        name = "" if msg.out else (self._title(sender) or title)
        return self.ctx.incoming(
            {"id": chat_id, "kind": kind, "title": title, "avatar": "".join(w[0] for w in title.split()[:2]).upper()},
            {"ext_id": _cid(chat_id, msg.id), "sender": "me" if msg.out else str(getattr(msg, "sender_id", "") or chat_id), "sender_name": name,
             "ts": msg.date.timestamp(), "text": text, "outgoing": bool(msg.out), "state": "sent", "attachments": att,
             "reply_to": None, "read": bool(msg.out) if read is None else read})

    async def sync(self):
        cl = self._client
        if cl is None or self._synced:
            return
        self._synced = True
        n = 0
        async for d in cl.iter_dialogs(limit=300):
            n += 1
            cid = str(d.id)
            self._names[cid] = d.name or cid
            msg = d.message
            if msg is not None and (getattr(msg, "raw_text", None) or getattr(msg, "media", None)):
                self._ingest(cid, d.name or cid, _kind(d), msg, None, read=bool(msg.out))
            else:
                self.ctx.incoming({"id": cid, "kind": _kind(d), "title": d.name or cid, "avatar": "".join(w[0] for w in (d.name or "?").split()[:2]).upper()})
            self.ctx.set_unread(cid, int(getattr(d, "unread_count", 0) or 0))

    async def fetch_history(self, conv, before=None, n=50):
        cl = self._client
        if cl is None:
            return 0
        offset = 0
        if before:
            row = self.ctx.store.get_message(int(before))
            if row and row.get("ext_id") and ":" in row["ext_id"]:
                offset = int(row["ext_id"].split(":", 1)[1])
        added = 0
        title = self._names.get(conv, conv)
        async for m in cl.iter_messages(int(conv), limit=int(n), offset_id=offset):
            if self._ingest(conv, title, "dm", m, None, read=True) is not None:
                added += 1
        return added

    # ---- sending ---------------------------------------------------------------------------------------------------
    def _need_client(self):
        if self._client is None:
            raise AdapterError("Telegram is not connected", "not-linked")
        return self._client

    async def send(self, conv, text, subject=None, reply_to=None, client_ref=None):
        cl = self._need_client()
        reply = None
        if reply_to:
            row = self.ctx.store.get_message(int(reply_to))
            if row and row.get("ext_id") and ":" in row["ext_id"]:
                reply = int(row["ext_id"].split(":", 1)[1])
        m = await cl.send_message(int(conv), text, reply_to=reply)
        return _cid(conv, m.id)

    async def send_file(self, conv, path, caption=None, client_ref=None):
        cl = self._need_client()
        m = await cl.send_file(int(conv), path, caption=caption or None)
        return _cid(conv, m.id)

    async def mark_read(self, conv):
        if self._client is not None:
            await self._client.send_read_acknowledge(int(conv))

    async def download(self, ref, dest):
        cl = self._need_client()
        chat, mid = ref.split(":", 1)
        m = await cl.get_messages(int(chat), ids=int(mid))
        if m is None:
            raise AdapterError("the message is gone", "not-found")
        path = await cl.download_media(m, file=dest)
        if not path:
            raise AdapterError("nothing to download", "not-found")
        return path

    # ---- linking (her login functions) -----------------------------------------------------------------------------
    async def link_start(self, params):
        ch = channel()
        if self.state()["state"] == "linked":
            return {"step": "done"}
        self._login = {"phase": "phone"}
        return {"step": "form", "fields": [{"name": "phone", "label": "Phone number (with country code)", "secret": False}]}

    async def link_step(self, data):
        ch, lg = channel(), self._login
        if not lg:
            return {"step": "error", "error": "no login in progress"}
        try:
            if lg["phase"] == "phone":
                phone = str(data.get("phone", "")).strip()
                if not phone:
                    return {"step": "error", "error": "a phone number is needed"}
                await ch.login_start(_workspace(), phone, self.acct)
                lg.update(phase="code", phone=phone)
                return {"step": "code", "prompt": "The code Telegram sent to the phone (the Telegram app or an SMS)"}
            if lg["phase"] in ("code", "password"):
                code = str(data.get("code") or lg.get("code") or "").strip()
                pw = data.get("password") or None
                if lg["phase"] == "code" and not code:
                    return {"step": "error", "error": "the code is needed"}
                lg["code"] = code
                msg = await ch.login_finish(_workspace(), code, self.acct, pw)
                if "two-step" in msg.lower() or "password" in msg.lower() and "connected" not in msg.lower():
                    lg["phase"] = "password"
                    return {"step": "password", "prompt": "Two-step verification password"}
                self._login, self._login_wait = None, time.time()
                return {"step": "done"}
        except Exception as e:
            self._login = None
            try:
                await self._drop_pending()
            except Exception:
                pass
            return {"step": "error", "error": str(e)}
        return {"step": "error", "error": "unexpected step"}

    async def _drop_pending(self):
        pend = getattr(channel(), "_pending", {}).pop(self.acct, None)
        if pend:
            await pend["client"].disconnect()

    async def link_cancel(self):
        self._login = None
        await self._drop_pending()

    async def unlink(self):
        ch = channel()
        cl = self._client or ch.get_live_client(self.acct)
        if cl is not None:
            try:
                await cl.log_out()           # ends the session on Telegram's side too
            except Exception as e:
                log.warning("telegram log_out: %r", e)
                try:
                    await cl.disconnect()
                except Exception:
                    pass
        try:
            ch._session_path(_workspace(), self.acct).unlink()
        except FileNotFoundError:
            pass
        self._client, self._synced, self._login, self._login_wait = None, False, None, None
