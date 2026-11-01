# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Signal: the adapter over Caroline's own `app.signal_channel` (signal-cli as a JSON-RPC daemon per account, linked as a secondary device).

Her module owns the `signal-cli link` process, the daemon and its connection. The adapter reads the daemon's JSON-RPC lines where they go through (hooks.py wraps
`_dispatch_rpc_line`: every `receive` notification, which carries incoming messages and, as `syncMessage`, what the owner wrote on the phone) and the pairing URI where her
module draws the QR (`_render_and_open_qr`), and uses her functions for the rest (`start_login`, `send_message`, `send_file`, `download_attachment`).

What the protocol gives and does not (docs/usage.md):
* **No history call** and no read marks over her daemon connection: the store keeps what arrives after linking. Messages carry the sender's real timestamp, so ids and times are true here
  (unlike WhatsApp's sidecar).
* **The QR code** is the picture her module drew (`qrcode`, a PNG): served through the API, the app shows it as an image. The link URI does not reissue like WhatsApp's.
* **signal-cli**: the Linux *native* build (GraalVM) needs no Java but exists only for x86-64 (111 MB); on the ARM64 device it is the JVM build (needs a JRE). A device without it shows the
  account as unavailable with that reason.
* **Unlinking** closes the daemon connection (her loop then ends the daemon), forgets the number and deletes the signal-cli configuration of the account on this device. The phone
  keeps the device in "Linked devices" until the owner removes it there.
* **Cancelling a link** ends the `signal-cli link` process of the account (found by its `--config` directory: her module keeps no handle on it).
"""
import asyncio, logging, os, shutil, time

from .adapter import Adapter, AdapterError

log = logging.getLogger("ic_messaging")
_channel = None                 # tests put a stand-in module here
ADAPTERS = {}                   # account ("owner" / "self") -> adapter, for the taps
LINK_WAIT_S = 180.0             # how long a started link is waited for before it is called failed


def channel():
    if _channel is not None:
        return _channel
    from app import signal_channel
    return signal_channel


def _workspace():
    from . import _workspace as ws
    return ws()


def tap(account, msg):
    """Every JSON-RPC line of the daemon of `account` (hooks.py). Never raises into her code."""
    try:
        ad = ADAPTERS.get(account)
        if ad is not None and msg.get("method") == "receive":
            ad.on_receive((msg.get("params") or {}).get("envelope") or {})
    except Exception as e:
        log.warning("signal tap: %r", e)


def tap_qr(account, data):
    """The pairing URI her module is about to draw (hooks.py)."""
    ad = ADAPTERS.get(account)
    if ad is not None:
        ad._qr = (str(data), time.time())


def _kind_of(att):
    if att.get("isVoiceNote"):
        return "voice"
    ct = (att.get("contentType") or "").lower()
    return "photo" if ct.startswith("image/") else "video" if ct.startswith("video/") else "audio" if ct.startswith("audio/") else "file"


class SignalAdapter(Adapter):
    MULTI = True                 # extra accounts: her channel module is extended with the name (channels.py)
    CHANNEL = True
    service = "signal"
    label = "Signal"
    POLL = 3.0

    def __init__(self, role, slot=1):
        super().__init__()
        self.role = role
        self.slot = slot
        ADAPTERS[self.acct] = self
        self._qr = None
        self._login_started = None
        self._login_err = ""
        self._watch = None
        self._was = None

    # ---- state -----------------------------------------------------------------------------------------------------
    def _status(self):
        try:
            return channel().get_signal_channel_status().get(self.acct, {})
        except Exception:
            return {}

    def _tool_missing(self):
        tool = getattr(channel(), "SIGNAL_CLI_BAT", "")
        return "" if tool and os.path.exists(tool) else "signal-cli is not on this device"

    def _number(self):
        try:
            return channel()._linked_number(_workspace(), self.acct)
        except Exception:
            return None

    def capabilities(self):
        return {"history": False, "send": True, "files": True, "read_marks": False, "search": False}

    def state(self):
        st, num = self._status(), self._number()
        why = self._tool_missing()
        if why and not (st.get("connected") or num):
            return {"state": "unavailable", "detail": why, "since": None}
        if st.get("connected"):
            return {"state": "linked", "detail": st.get("own_number") or num or "", "since": None}
        if num:
            if st.get("last_error"):
                return {"state": "needs-attention", "detail": str(st["last_error"])[:120], "since": None}
            return {"state": "connecting", "detail": "connecting", "since": None}
        if self._login_started is not None:
            return {"state": "connecting", "detail": "waiting for the code to be scanned", "since": None}
        return {"state": "not-linked", "detail": self._login_err, "since": None}

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
                log.warning("signal watcher %s: %r", self.acct, e)
            await asyncio.sleep(self.POLL)

    # ---- the daemon's notifications ----------------------------------------------------------------------------------
    def on_receive(self, env):
        dm, sent = env.get("dataMessage"), (env.get("syncMessage") or {}).get("sentMessage")
        if dm:
            out = False
            body = dm
            who = env.get("sourceNumber") or env.get("source") or env.get("sourceUuid") or ""
            peer, name = who, env.get("sourceName") or ""
        elif sent:
            out = True
            body = sent
            peer = sent.get("destinationNumber") or sent.get("destination") or sent.get("destinationUuid") or ""
            who, name = "me", ""
        else:
            return                                              # receipts, typing, calls, reactions: not messages
        group = body.get("groupInfo") or {}
        conv = group.get("groupId") or peer
        if not conv:
            return
        text = (body.get("message") or "").strip()
        atts = body.get("attachments") or []
        if not text and not atts:
            return
        ts_ms = body.get("timestamp") or env.get("timestamp") or int(time.time() * 1000)
        # a message that does not name the other party (what the owner wrote on the phone, or a sender whose name is hidden) must not rename a dialogue
        # that is already known under a name
        known = self.ctx.store.get_conversation(f"{self.account_id}/{conv}")
        title = group.get("groupName") or name or (known["title"] if known and known["title"] != conv else "") or peer or conv
        att = [{"kind": _kind_of(a), "name": a.get("filename") or _kind_of(a), "size": a.get("size") or 0, "mime": a.get("contentType") or "",
                "ref": f"{a['id']}|{conv}"} for a in atts if a.get("id")]
        self.ctx.incoming({"id": conv, "kind": "group" if group else "dm", "title": title, "avatar": ""}, {
            "ext_id": f"{conv}|{who}|{ts_ms}", "sender": who if not out else "me", "sender_name": "" if out else (name or who), "ts": ts_ms / 1000.0,
            "text": text, "outgoing": out, "state": "sent", "attachments": att, "reply_to": None, "read": out})

    # ---- sending ---------------------------------------------------------------------------------------------------
    async def send(self, conv, text, subject=None, reply_to=None, client_ref=None):
        try:
            await channel().send_message(self.acct, conv, text)
        except Exception as e:
            raise AdapterError(str(e), "send-failed") from e
        return None

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
            return await channel().download_attachment(self.acct, ref, save_path=dest)
        except Exception as e:
            raise AdapterError(str(e), "not-found") from e

    # ---- linking ---------------------------------------------------------------------------------------------------
    def qr_image(self):
        if self._qr is None:
            return None
        try:
            p = channel()._qr_image_path(_workspace(), self.acct)
            if os.path.getmtime(p) < self._qr[1] - 5:
                return None
            return open(p, "rb").read()
        except Exception:
            return None

    async def link_start(self, params):
        why = self._tool_missing()
        if why:
            return {"step": "error", "error": why}
        if self.state()["state"] == "linked":
            return {"step": "done"}
        self._login_err, self._qr = "", None
        self._login_started = time.time()
        try:
            await channel().start_login(_workspace(), self.acct)
        except Exception as e:
            self._login_started, self._login_err = None, str(e)
            return {"step": "error", "error": str(e)}
        return await self._wait_qr()

    async def _wait_qr(self, secs=25.0):
        end = time.time() + secs
        while time.time() < end:
            if self.state()["state"] == "linked":
                return {"step": "done"}
            if self._qr is not None:
                return {"step": "qr", "data": self._qr[0], "image": f"accounts/{self.account_id}/qr",
                        "prompt": "On the phone: Signal > Settings > Linked devices > Link new device, then scan this code. Messages from before the link are not here."}
            await asyncio.sleep(0.4)
        return {"step": "wait", "prompt": "Waiting for the link code from signal-cli..."}

    async def link_step(self, data):
        s = self.state()["state"]
        if s == "linked":
            return {"step": "done"}
        if self._number():
            return {"step": "wait", "prompt": "Linked; connecting to Signal..."}
        if self._login_started is None:
            return {"step": "error", "error": self._login_err or "no login in progress"}
        if time.time() - self._login_started > LINK_WAIT_S:
            self._login_started = None
            self._kill_link_process()
            return {"step": "error", "error": "the code was not scanned in time"}
        return await self._wait_qr(8.0)

    def _kill_link_process(self):
        """`signal-cli ... --config <this account's dir> link ...`: her module keeps no handle on it, so it is found by its command line."""
        try:
            cfg = str(channel()._config_dir(_workspace(), self.acct))
            for pid in os.listdir("/proc"):
                if not pid.isdigit():
                    continue
                try:
                    args = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace").split("\0")
                except OSError:
                    continue
                if "--config" in args and cfg in args and "link" in args and "daemon" not in args:
                    os.kill(int(pid), 15)
        except Exception as e:
            log.warning("signal: could not end the link process: %r", e)

    async def link_cancel(self):
        fresh = self._login_started is not None and not self._number()
        self._login_started, self._qr = None, None
        if fresh:
            self._kill_link_process()

    async def unlink(self):
        ch = channel()
        if self.state()["state"] in ("not-linked", "unavailable"):
            raise AdapterError("not linked", "not-linked")
        self._kill_link_process()
        try:
            ch._number_file(_workspace(), self.acct).unlink()          # her loop sees no number: after its daemon ends it stays idle
        except FileNotFoundError:
            pass
        w = getattr(ch, "_writer", {}).get(self.acct)
        if w is not None:
            try:
                w.close()                                              # ends her read loop, which ends the daemon
            except Exception as e:
                log.warning("signal: closing the daemon connection: %r", e)
        shutil.rmtree(ch._config_dir(_workspace(), self.acct), ignore_errors=True)
        try:
            st = ch._status[self.acct]
            st.connected = st.linked = False
            st.own_number = None
        except Exception:
            pass
        self._login_started, self._qr = None, None
