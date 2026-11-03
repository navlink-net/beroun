# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Mail through the SW API, shown as a messenger: one dialogue per correspondent.

The adapter calls the same functions her own email tools run (`app.plugins.email_plugin`: email_list_folders, email_list_messages, email_get_message, email_send,
email_send_reply, email_download_attachment, email_mark), so the SW session, the saved mailbox credentials (`email_credentials`, filled in by address) and the
Camerlengo `email:*` commands are hers and shared. Mail has no push: the adapter polls. A dialogue merges the Inbox messages from a correspondent with the Sent
messages to them; the subject is a field of each message (the app shows it as a line when it changes).
"""
import asyncio, hashlib, html, json, logging, os, re, time
from email.utils import getaddresses, parsedate_to_datetime

from .adapter import Adapter, AdapterError

log = logging.getLogger("ic_messaging")
_plugin = None             # tests put a stand-in for app.plugins.email_plugin here
_creds = None              # ... and for app.plugins.email_credentials
_sw_ready = None           # ... and for the check "does the device have an SW login"

PRESETS = [("gmail.com", "imap.gmail.com", "smtp.gmail.com"), ("googlemail.com", "imap.gmail.com", "smtp.gmail.com"),
           ("outlook.com", "outlook.office365.com", "smtp.office365.com"), ("hotmail.com", "outlook.office365.com", "smtp.office365.com"),
           ("live.com", "outlook.office365.com", "smtp.office365.com"), ("yandex.ru", "imap.yandex.com", "smtp.yandex.com"),
           ("yandex.com", "imap.yandex.com", "smtp.yandex.com"), ("ya.ru", "imap.yandex.com", "smtp.yandex.com"),
           ("mail.ru", "imap.mail.ru", "smtp.mail.ru"), ("inbox.ru", "imap.mail.ru", "smtp.mail.ru"), ("list.ru", "imap.mail.ru", "smtp.mail.ru"),
           ("bk.ru", "imap.mail.ru", "smtp.mail.ru"), ("icloud.com", "imap.mail.me.com", "smtp.mail.me.com"), ("me.com", "imap.mail.me.com", "smtp.mail.me.com")]
DAYS = 30
FETCH_CAP = 40             # message bodies fetched per pull; the rest are stored as headers and filled in when the dialogue is opened


def plugin():
    if _plugin is not None:
        return _plugin
    from app.plugins import email_plugin
    return email_plugin


def creds():
    if _creds is not None:
        return _creds
    from app.plugins import email_credentials
    return email_credentials


def sw_ready():
    if _sw_ready is not None:
        return _sw_ready()
    from app.plugins import sw_api
    return sw_api.load_credentials() is not None


def _workspace():
    from . import _workspace as ws
    return ws()


def preset_for(address):
    dom = address.rsplit("@", 1)[-1].lower()
    for d, imap, smtp in PRESETS:
        if dom == d:
            return imap, smtp
    return f"imap.{dom}", f"smtp.{dom}"


def _strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _addrs(value):
    """[(display name, lower-case address)] from a header string."""
    return [(n.strip().strip('"'), a.lower()) for n, a in getaddresses([value or ""]) if a]


def _ts(date, fallback):
    try:
        return parsedate_to_datetime(date).timestamp()
    except Exception:
        return fallback


def _size(a):
    """The size the mail server reports for a part, when it does (field names differ between servers); 0 = unknown until the file is fetched."""
    for k in ("size", "sizeBytes", "contentLength", "length"):
        try:
            n = int(a.get(k) or 0)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return 0


class MailAdapter(Adapter):
    service = "mail"
    label = "Mail"
    POLL = 60.0
    MULTI = True                 # any number of mailboxes: her mail tools take the address in every call

    def __init__(self, role, slot=1):
        super().__init__()
        self.role = role
        self.slot = slot
        self._watch = None
        self._link = None
        self._error = ""
        self._folders = {}
        self._synced = False
        self._since = None

    # ---- which mailbox this account is ----------------------------------------------------------------------------
    def _map_path(self):
        return os.path.join(_workspace(), "ic_messaging_mail.json")

    def _mapping(self):
        try:
            return json.load(open(self._map_path()))
        except (OSError, ValueError):
            return {}

    @property
    def _key(self):
        return self.role if self.slot == 1 else f"{self.role}#{self.slot}"       # the first mailbox keeps the key it always had

    @property
    def address(self):
        return (self._mapping().get(self._key) or {}).get("address", "")

    def _set_address(self, address):
        m = self._mapping()
        if address:
            m[self._key] = {"address": address, "linked_at": time.time()}
        else:
            m.pop(self._key, None)
        os.makedirs(os.path.dirname(self._map_path()), exist_ok=True)
        json.dump(m, open(self._map_path(), "w"))

    def prefill(self):
        known = creds().load_local(self.address) if self.address else None
        return {"address": self.address, "imap_host": (known or {}).get("imapHost", ""), "smtp_host": (known or {}).get("smtpHost", "")}

    # ---- state -----------------------------------------------------------------------------------------------------
    def capabilities(self):
        return {"history": True, "send": True, "files": True, "read_marks": True, "search": True}

    def state(self):
        if self._link:
            return {"state": "connecting", "detail": "waiting for the mailbox settings", "since": None}
        if not self.address:
            return {"state": "not-linked", "detail": "", "since": None}
        if not sw_ready():
            return {"state": "needs-attention", "detail": "log in to SquirrelWisdom: the mail goes through its API", "since": None}
        if self._error:
            return {"state": "needs-attention", "detail": self._error[:120], "since": None}
        return {"state": "linked", "detail": self.address, "since": self._since}

    # ---- calls to the SW API through her functions ----------------------------------------------------------------
    async def _j(self, fn_name, **args):
        args = {"address": self.address, **args}
        try:
            res = await getattr(plugin(), fn_name)(args, None)
        except Exception as e:
            raise AdapterError(str(e), "send-failed" if "send" in fn_name else "error") from e
        text = res.get("text") if isinstance(res, dict) else None
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}

    # ---- life ------------------------------------------------------------------------------------------------------
    async def start(self):
        self._watch = asyncio.ensure_future(self._watcher())

    async def stop(self):
        if self._watch:
            self._watch.cancel()

    async def _watcher(self):
        while True:
            try:
                if self.address and sw_ready():
                    if not self._synced:
                        await self.sync()
                    else:
                        await self._pull_all(first=False)
                    if self._error:
                        self._error = ""
                        self.ctx.state_changed()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = str(e)
                if msg != self._error:
                    self._error = msg
                    self.ctx.state_changed()
                log.warning("mail %s: %r", self.role, e)
            await asyncio.sleep(self.POLL)

    # ---- reading ---------------------------------------------------------------------------------------------------
    async def sync(self):
        if not self.address or not sw_ready():
            return
        f = await self._j("email_list_folders")
        self._folders = {"inbox": f.get("inbox") or "INBOX", "sent": f.get("sent")}
        self._synced = True
        self._since = self._since or time.time()
        await self._pull_all(first=True)

    async def _pull_all(self, first):
        await self._pull(self._folders.get("inbox") or "INBOX", 60 if first else 20, incoming=True)
        if self._folders.get("sent"):
            await self._pull(self._folders["sent"], 40 if first else 10, incoming=False)

    async def _pull(self, folder, limit, incoming):
        since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - DAYS * 86400))
        heads = await self._j("email_list_messages", folder=folder, limit=limit, sinceIso=since)
        if not isinstance(heads, list):
            return
        fetched = 0
        for h in heads:                                     # newest first
            uid = str(h.get("uid"))
            ext = f"{folder}:{uid}"
            if self.ctx.store.has_ext(self.account_id, ext):
                continue
            full = None
            if fetched < FETCH_CAP and (not incoming or True):
                try:
                    full = await self._j("email_get_message", folder=folder, uid=int(uid) if uid.isdigit() else uid)
                    fetched += 1
                except AdapterError as e:
                    log.warning("mail getMessage %s: %s", ext, e)
            self._ingest(folder, h, full, incoming)

    def _ingest(self, folder, head, full, incoming):
        uid = str(head.get("uid"))
        src = full or {}
        frm = src.get("from") or head.get("from") or ""
        if incoming:
            people = _addrs(frm)[:1]
        else:
            people = _addrs(src.get("to") or "")
            if not people:
                return None                                 # a Sent header alone does not say to whom: the dialogue cannot be placed yet
        people = [p for p in people if p[1] != self.address.lower()] or people
        if len(people) == 1:
            key, title = people[0][1], people[0][0] or people[0][1]
        else:
            addrs = sorted({a for _, a in people})
            key = "grp-" + hashlib.sha1(",".join(addrs).encode()).hexdigest()[:12]
            title = ", ".join((n.split()[0] if n else a.split("@")[0]) for n, a in people[:3]) + (f" +{len(people) - 3}" if len(people) > 3 else "")
        prior = self.ctx.store.get_conversation(f"{self.account_id}/{key}")
        if prior and len(people) == 1 and not people[0][0] and prior["title"] != key:
            title = prior["title"]                          # a mail without a display name must not rename a dialogue that has one
        text = src.get("text") or _strip_html(src.get("html") or "")
        atts = [{"kind": "file", "name": a.get("filename") or "attachment", "size": _size(a), "mime": a.get("contentType") or "",
                 "ref": f"{folder}:{uid}:{a.get('index', i)}"} for i, a in enumerate(src.get("attachments") or [])]
        flags = head.get("flags") or []
        now = time.time()
        return self.ctx.incoming(
            {"id": key, "kind": "dm" if len(people) == 1 else "group", "title": title, "extra": {"addresses": [a for _, a in people]},
             "avatar": "".join(w[0] for w in title.split()[:2]).upper()},
            {"ext_id": f"{folder}:{uid}", "sender": "me" if not incoming else key, "sender_name": "" if not incoming else (people[0][0] or people[0][1]),
             "ts": _ts(src.get("date") or head.get("date"), now), "text": text.strip(), "subject": (src.get("subject") or head.get("subject") or "").strip(),
             "outgoing": not incoming, "state": "sent", "attachments": atts, "read": (not incoming) or ("\\Seen" in flags)})

    async def prepare(self, conv, rows):
        """Mail bodies beyond the first batch were stored as headers only: fetch the ones of this dialogue now."""
        changed = False
        for r in self.ctx.store.messages_needing_body(f"{self.account_id}/{conv}", 25):
            folder, _, uid = r["ext_id"].rpartition(":")
            try:
                full = await self._j("email_get_message", folder=folder, uid=int(uid) if uid.isdigit() else uid)
            except AdapterError:
                continue
            text = (full.get("text") or _strip_html(full.get("html") or "")).strip()
            atts = [{"kind": "file", "name": a.get("filename") or "attachment", "size": _size(a), "mime": a.get("contentType") or "",
                     "ref": f"{folder}:{uid}:{a.get('index', i)}"} for i, a in enumerate(full.get("attachments") or [])]
            self.ctx.store.update_message(r["id"], text=text or "(no text)", attachments=atts)
            changed = True
        return changed

    async def fetch_history(self, conv, before=None, n=50):
        return 0                # the first pull already holds the last 30 days; older mail is not part of the dialogues

    # ---- sending ---------------------------------------------------------------------------------------------------
    async def send(self, conv, text, subject=None, reply_to=None, client_ref=None):
        cid = f"{self.account_id}/{conv}"
        c = self.ctx.store.get_conversation(cid)
        addrs = (c or {}).get("extra", {}).get("addresses") or [conv]
        rows = self.ctx.store.history(cid, None, 100)
        last_in = next((r for r in reversed(rows) if not r["outgoing"] and r["ext_id"]), None)
        last_subject = next((r["subject"] for r in reversed(rows) if r["subject"]), "")
        default = last_subject if re.match(r"(?i)\s*re:", last_subject) else (f"Re: {last_subject}" if last_subject else "")
        subj = (subject or "").strip()
        if last_in and (not subj or subj == default):
            folder, _, uid = last_in["ext_id"].rpartition(":")
            await self._j("email_send_reply", folder=folder, uid=int(uid) if uid.isdigit() else uid, text=text)
        else:
            await self._j("email_send", to=addrs, subject=subj or (text.strip().splitlines()[0][:60] if text.strip() else "(no subject)"), text=text)
        asyncio.get_running_loop().call_later(6, lambda: asyncio.ensure_future(self._pull_sent_soon()))
        return None             # the copy in Sent becomes the echo (matched by its text)

    async def _pull_sent_soon(self):
        try:
            if self._folders.get("sent"):
                await self._pull(self._folders["sent"], 5, incoming=False)
        except Exception as e:
            log.warning("mail sent pull: %r", e)

    async def send_file(self, conv, path, caption=None, client_ref=None):
        cid = f"{self.account_id}/{conv}"
        c = self.ctx.store.get_conversation(cid)
        addrs = (c or {}).get("extra", {}).get("addresses") or [conv]
        await self._j("email_send", to=addrs, subject=os.path.basename(path), text=caption or "", attachments=[{"path": path}])
        asyncio.get_running_loop().call_later(6, lambda: asyncio.ensure_future(self._pull_sent_soon()))
        return None

    async def mark_read(self, conv):
        cid = f"{self.account_id}/{conv}"
        for r in self.ctx.store.history(cid, None, 100):
            if not r["outgoing"] and r["ext_id"] and not r["read"]:
                folder, _, uid = r["ext_id"].rpartition(":")
                try:
                    await self._j("email_mark", folder=folder, uid=int(uid) if uid.isdigit() else uid, flag="\\Seen", set=True)
                except AdapterError:
                    break

    async def download(self, ref, dest):
        folder, uid, idx = ref.rsplit(":", 2)
        await self._j("email_download_attachment", folder=folder, uid=int(uid) if uid.isdigit() else uid, attachmentIndex=int(idx), savePath=dest)
        return dest

    # ---- linking ---------------------------------------------------------------------------------------------------
    async def link_start(self, params):
        if (params or {}).get("edit") and self.address:      # change the password or the servers (or the address itself): the whole form at once
            pre = self.prefill()
            self._link = {"phase": "edit", "old": self.address}
            return {"step": "form", "prompt": "The mailbox settings", "fields": [
                {"name": "address", "label": "E-mail address", "secret": False, "preset": pre["address"]},
                {"name": "password", "label": "Password", "secret": True},
                {"name": "imap_host", "label": "IMAP server", "secret": False, "preset": pre["imap_host"] or preset_for(pre["address"])[0]},
                {"name": "smtp_host", "label": "SMTP server", "secret": False, "preset": pre["smtp_host"] or preset_for(pre["address"])[1]}]}
        if self.address and self.state()["state"] == "linked":
            return {"step": "done"}
        self._link = {"phase": "address"}
        return {"step": "form", "prompt": "The address of the mailbox", "fields": [{"name": "address", "label": "E-mail address", "secret": False}]}

    def _used_elsewhere(self, addr):
        return any(k != self._key and (v or {}).get("address") == addr for k, v in self._mapping().items())

    async def link_step(self, data):
        lk = self._link
        if not lk:
            return {"step": "error", "error": "no login in progress"}
        try:
            if lk["phase"] in ("address", "edit"):
                addr = str(data.get("address", "")).strip().lower()
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
                    return {"step": "error", "error": "that is not an e-mail address"}
                if self._used_elsewhere(addr):
                    self._link = None
                    return {"step": "error", "error": "this mailbox is already added"}
                if lk["phase"] == "edit":
                    if not data.get("password"):
                        return {"step": "error", "error": "the password is needed"}
                    lk.update(address=addr)
                    return await self._verify(addr, {"password": data["password"], "imapHost": data.get("imap_host") or preset_for(addr)[0],
                                                     "smtpHost": data.get("smtp_host") or preset_for(addr)[1]})
                lk.update(phase="creds", address=addr)
                known = creds().load_local(addr)
                if known and known.get("password") and known.get("imapHost"):      # a mailbox she already uses: nothing to ask
                    return await self._verify(addr, {})
                imap, smtp = preset_for(addr)
                note = " (Gmail needs an app password, not the account password)" if addr.endswith(("gmail.com", "googlemail.com")) else ""
                return {"step": "form", "prompt": "Mailbox settings" + note, "fields": [
                    {"name": "password", "label": "Password", "secret": True},
                    {"name": "imap_host", "label": "IMAP server", "secret": False, "preset": imap},
                    {"name": "smtp_host", "label": "SMTP server", "secret": False, "preset": smtp}]}
            if lk["phase"] == "creds":
                if not data.get("password"):
                    return {"step": "error", "error": "the password is needed"}
                return await self._verify(lk["address"], {"password": data["password"], "imapHost": data.get("imap_host") or preset_for(lk["address"])[0],
                                                          "smtpHost": data.get("smtp_host") or preset_for(lk["address"])[1]})
        except AdapterError as e:
            self._link = None
            return {"step": "error", "error": str(e)}
        return {"step": "error", "error": "unexpected step"}

    async def _verify(self, addr, creds_args):
        if not sw_ready():
            self._link = None
            return {"step": "error", "error": "log in to SquirrelWisdom first: the mail goes through its API"}
        try:                                                    # her wrapper keeps the credentials once a call with them worked
            res = await plugin().email_list_folders({"address": addr, **creds_args}, None)
        except Exception as e:
            self._link = None
            return {"step": "error", "error": f"the mailbox did not accept these settings: {e}"}
        old = (self._link or {}).get("old")
        if old and old != addr:                                 # edited to another mailbox: the old one and its messages are gone from this account
            try:
                await creds().forget(old)
            except Exception as e:
                log.warning("mail forget %s: %r", old, e)
            self.ctx.store.delete_account_data(self.account_id)
        self._set_address(addr)
        self._link, self._error, self._synced, self._since = None, "", False, time.time()
        asyncio.ensure_future(self._safe_sync())
        return {"step": "done"}

    async def _safe_sync(self):
        try:
            await self.sync()
        except Exception as e:
            self._error = str(e)
            self.ctx.state_changed()

    async def link_cancel(self):
        self._link = None

    async def unlink(self):
        addr = self.address
        if not addr:
            raise AdapterError("not linked", "not-linked")
        try:
            await creds().forget(addr)                          # her saved copy and the vault note go too: the account is gone for both
        except Exception as e:
            log.warning("mail forget %s: %r", addr, e)
        self._set_address("")
        self._synced, self._error, self._link = False, "", None
