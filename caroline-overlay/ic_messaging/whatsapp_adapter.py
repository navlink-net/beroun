# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""WhatsApp: the adapter over Caroline's own `app.whatsapp_channel` (one Node sidecar per account, Baileys, an unofficial protocol).

Her channel module owns the sidecar and its stdout lines; the adapter reads them where they go through (hooks.py wraps `SidecarProcess`, so `tap(account, line)` sees
every line the sidecar prints: `qr`, `ready`, `loggedOut`, `message`) and uses her own functions for everything else (`start_login`, `send_message`, `send_file`,
`download_media`). One connection per account stays hers.

What the protocol does not give, said plainly (docs/usage.md):
* **No history call.** The store keeps what arrives after the account is linked; the app says so. Nothing is invented for the time before.
* **No ids and no times in the sidecar's message line**: a message gets the time it arrived and an id made of the chat, the sender and the arrival (the id of a media
  message is its media ref, `<jid>:<message id>`, which is also what a download needs). A message sent from the phone is matched to a pending one by its text, as everywhere.
* **No read marks**, no search on the service: the store answers.
* **The QR code** is the picture her module already draws (`qrcode`, a PNG in her workspace). The adapter serves that PNG (`qr_image()`, the API route `/accounts/<id>/qr`);
  the app shows it as an image and asks for it again while the link dialog is open (WhatsApp reissues the code every 20-60 s).
* **Unlinking** stops the sidecar and deletes its saved session on this device. The sidecar has no logout command, so the device stays in the phone's "Linked devices" list
  until the owner removes it there; the app says so.
* **Node.js** is needed (her sidecar). A device image without it shows the account as unavailable with that reason (stage 0 of the plan decides whether the device gets it).
"""
import asyncio, hashlib, logging, os, shutil, time

from .adapter import Adapter, AdapterError

log = logging.getLogger("ic_messaging")
_channel = None                 # tests put a stand-in module here
ADAPTERS = {}                   # account ("owner" / "self") -> adapter, for the line tap
NEWS = ("message",)


def channel():
    if _channel is not None:
        return _channel
    from app import whatsapp_channel
    return whatsapp_channel


def _workspace():
    from . import _workspace as ws
    return ws()


def tap(account, line):
    """Every line the sidecar of `account` prints (hooks.py). Never raises into her code."""
    try:
        ad = ADAPTERS.get(account)
        if ad is not None:
            ad.on_line(line)
    except Exception as e:
        log.warning("whatsapp tap: %r", e)


def _title(line, jid):
    name = (line.get("chatName") or "").strip()
    return name if name and name != jid else jid.split("@")[0]


def _kind(jid):
    return "group" if jid.endswith("@g.us") else "dm"


class WhatsAppAdapter(Adapter):
    MULTI = True                 # extra accounts: her channel module is extended with the name (channels.py)
    CHANNEL = True
    service = "whatsapp"
    label = "WhatsApp"
    POLL = 3.0

    def __init__(self, role, slot=1):
        super().__init__()
        self.role = role
        self.slot = slot
        ADAPTERS[self.acct] = self
        self._qr = None                     # (data, time it arrived) of the code the sidecar printed last
        self._login_started = None
        self._fresh = False                 # the link in progress is a first one (no saved session before it)
        self._login_err = ""
        self._watch = None
        self._was = None
        self._seq = 0

    # ---- state -----------------------------------------------------------------------------------------------------
    def _status(self):
        try:
            return channel().get_whatsapp_channel_status().get(self.acct, {})
        except Exception:
            return {}

    def _node_missing(self):
        ch = channel()
        node = getattr(ch, "NODE_EXE", "")
        side = getattr(ch, "SIDECAR_DIR", "")
        if not node or not os.path.exists(node):
            return "Node.js is not on this device (WhatsApp's sidecar needs it)"
        if not side or not os.path.exists(os.path.join(side, "index.js")):
            return "the WhatsApp sidecar is not installed"
        return ""

    def capabilities(self):
        return {"history": False, "send": True, "files": True, "read_marks": False, "search": False}

    def state(self):
        why = self._node_missing()
        ch = channel()
        st = self._status()
        has = False
        try:
            has = ch._has_prior_session(_workspace(), self.acct)
        except Exception:
            pass
        if why and not (st.get("connected") or has):
            return {"state": "unavailable", "detail": why, "since": None}
        if st.get("connected"):
            return {"state": "linked", "detail": st.get("own_jid") or "", "since": None}
        if self._login_started is not None and not has:
            return {"state": "connecting", "detail": "waiting for the code to be scanned", "since": None}
        if not has and not st.get("linked"):
            return {"state": "not-linked", "detail": self._login_err, "since": None}
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
        while True:
            try:
                s = self.state()["state"]
                if s != self._was:
                    self._was = s
                    if s == "linked":
                        self._login_started, self._qr = None, None
                    self.ctx.state_changed()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("whatsapp watcher %s: %r", self.acct, e)
            await asyncio.sleep(self.POLL)

    # ---- the sidecar's lines -----------------------------------------------------------------------------------------
    def on_line(self, line):
        kind = line.get("type")
        if kind == "qr":
            self._qr = (str(line.get("data") or ""), time.time())
        elif kind in ("ready", "loggedOut"):
            if kind == "ready":
                self._login_started, self._qr = None, None
            self.ctx.state_changed()
        elif kind == "message":
            self._store(line)

    def _store(self, line):
        jid = str(line.get("from") or "")
        if not jid or jid.startswith("status@") or jid.endswith("@broadcast"):
            return                                               # status updates and broadcast lists are not conversations
        text = (line.get("text") or "").strip()
        media = line.get("media")
        ref = line.get("mediaRef") or ""
        if not text and not media:
            return
        out = bool(line.get("fromMe"))
        self._seq += 1
        arrived = time.time()
        sender = "me" if out else str(line.get("sender") or jid)
        group = _kind(jid) == "group"
        if out:
            who = ""
        elif group:
            who = line.get("senderName") or line.get("senderPhone") or sender.split("@")[0]
        else:
            who = _title(line, jid)
        att = []
        if media:
            label = str(media)
            name = label.split('"')[1] if label.startswith("file \"") and label.count('"') >= 2 else label
            att = [{"kind": {"photo": "photo", "video": "video", "audio": "audio", "voice message": "voice", "sticker": "sticker"}.get(label, "file"),
                    "name": name, "size": 0, "mime": "", "ref": ref}]
        ext = ref or "wa-" + hashlib.sha1(f"{jid}|{sender}|{text}|{int(arrived)}|{self._seq}".encode()).hexdigest()[:20]
        title = _title(line, jid)
        if title == jid.split("@")[0]:                             # the line names nobody: a chat already known under a name keeps it
            known = self.ctx.store.get_conversation(f"{self.account_id}/{jid}")
            if known and known["title"] and known["title"] != title:
                title = known["title"]
        self.ctx.incoming({"id": jid, "kind": _kind(jid), "title": title, "avatar": ""}, {
            "ext_id": ext, "sender": sender, "sender_name": who, "ts": arrived, "text": text, "outgoing": out, "state": "sent",
            "attachments": att, "reply_to": None, "read": out})

    # ---- sending ---------------------------------------------------------------------------------------------------
    async def send(self, conv, text, subject=None, reply_to=None, client_ref=None):
        try:
            await channel().send_message(self.acct, conv, text)
        except Exception as e:
            raise AdapterError(str(e), "send-failed") from e
        return None                          # the sidecar reports no id; the stored message stays the pending one

    async def send_file(self, conv, path, caption=None, client_ref=None):
        try:
            await channel().send_file(self.acct, conv, path, caption=caption)
        except Exception as e:
            raise AdapterError(str(e), "send-failed") from e
        return None

    async def download(self, ref, dest):
        if not ref:
            raise AdapterError("nothing to download", "not-found")
        try:
            return await channel().download_media(self.acct, ref, save_path=dest)
        except Exception as e:                # the sidecar remembers only the last 200 media messages
            raise AdapterError(str(e), "not-found") from e

    # ---- linking ---------------------------------------------------------------------------------------------------
    def qr_image(self):
        """The PNG her module drew for the code on screen now (bytes), or None when there is no fresh code."""
        if self._qr is None:
            return None
        try:
            p = channel()._qr_image_path(_workspace(), self.acct)
            if os.path.getmtime(p) < self._qr[1] - 5:
                return None                   # the picture of an older code
            return open(p, "rb").read()
        except Exception:
            return None

    async def link_start(self, params):
        why = self._node_missing()
        if why:
            return {"step": "error", "error": why}
        if self.state()["state"] == "linked":
            return {"step": "done"}
        self._login_err = ""
        self._login_started = time.time()
        self._qr = None
        try:
            self._fresh = not channel()._has_prior_session(_workspace(), self.acct)    # no session yet: this is a first link, not a reconnect
        except Exception:
            self._fresh = True
        try:
            await channel().start_login(_workspace(), self.acct)
        except Exception as e:
            self._login_started = None
            self._login_err = str(e)
            return {"step": "error", "error": str(e)}
        return await self._wait_qr()

    async def _wait_qr(self, secs=25.0):
        end = time.time() + secs
        while time.time() < end:
            if self.state()["state"] == "linked":
                return {"step": "done"}
            if self._qr is not None:
                return {"step": "qr", "data": self._qr[0], "image": f"accounts/{self.account_id}/qr",
                        "prompt": "On the phone: WhatsApp > Linked devices > Link a device, then scan this code. WhatsApp can restrict an account that uses an "
                                  "unofficial client: use a number you can afford to lose. Messages from before the link are not here."}
            await asyncio.sleep(0.4)
        return {"step": "wait", "prompt": "Waiting for the code from WhatsApp..."}

    async def link_step(self, data):
        if self.state()["state"] == "linked":
            return {"step": "done"}
        if self._login_started is None:
            return {"step": "error", "error": self._login_err or "no login in progress"}
        return await self._wait_qr(8.0)

    def _stop_sidecar(self, forget_session):
        ch = channel()
        side = getattr(ch, "_sidecar", {}).get(self.acct)
        if side is not None:
            try:
                side.stop()
            except Exception as e:
                log.warning("whatsapp sidecar stop: %r", e)
        try:
            ch._login_requested[self.acct] = False
        except Exception:
            pass
        if forget_session:
            shutil.rmtree(ch._auth_dir(_workspace(), self.acct), ignore_errors=True)    # her loop sees no session any more and stays idle
        try:
            ch._status[self.acct].connected = False
            if forget_session:
                ch._status[self.acct].linked = False
                ch._status[self.acct].own_jid = None
        except Exception:
            pass

    async def link_cancel(self):
        """A link that was started and not finished: the sidecar of this account only waits for a scan nobody will make (~110 MB measured), so it is stopped and
        the half-made session removed. A linked account, or one that only reconnects, is not touched."""
        fresh = self._fresh
        self._login_started, self._qr, self._fresh = None, None, False
        if fresh and self.state()["state"] != "linked":
            self._stop_sidecar(forget_session=True)

    async def unlink(self):
        ch = channel()
        st = self.state()["state"]
        if st in ("not-linked", "unavailable"):
            raise AdapterError("not linked", "not-linked")
        self._stop_sidecar(forget_session=True)
        self._login_started, self._qr, self._fresh = None, None, False
