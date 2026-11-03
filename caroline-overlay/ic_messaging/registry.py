# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The account registry: the one place where the app and Caroline meet.

It owns the adapters (so there is one connection per account), computes each account's state from the adapter (which reads the real session
Caroline keeps; the registry has no state of its own that could disagree with it), runs one send queue per account, records who acted, and
keeps the change log the app polls. Both users of an account call the same methods.
"""
import asyncio, logging, mimetypes, os, re, threading, time, uuid

from . import actors, media
from .adapter import AdapterError, Context, ROLES, SERVICES

log = logging.getLogger("ic_messaging")


def assistant_name():
    """The name of the assistant of this build (the role "self" is its accounts): asked of the shell, Caroline when it does not answer (her own build, a test)."""
    try:
        from beroun_tools import shell
        return next((p["name"] for p in shell._request("/api/assistant/providers", timeout=2.0).get("providers", []) if p.get("active")), "Caroline")
    except Exception:   # noqa: BLE001
        return "Caroline"

ACTOR_APP = "owner-app"
ACTORS_CAROLINE = ("caroline-tool", "caroline-autonomous")
ACTOR_EXTERNAL = "external"          # seen arriving from elsewhere: the phone, the other party


class Registry:
    def __init__(self, store, adapters, ledger=None, owner_may_write_self=True, factory=None):
        # owner_may_write_self: the owner may write from Caroline's own accounts in the app (decided 2026-10-27); every such send is recorded
        # with the actor `owner-app`, so it can be told from what she wrote herself. False brings back the read-only rule.
        self.store = store
        self.adapters = {a.account_id: a for a in adapters}
        self.ledger = ledger                       # ledger(actor, account, conv_title, text, subject) or None
        self.owner_may_write_self = owner_may_write_self
        self.factory = factory                     # factory(service, role, slot) -> a new, unlinked adapter: how an account is added (None: none can be)
        self._locks = {}                           # account id -> asyncio.Lock (the send queue)
        self._loop = None
        self._wake_ev = None
        self._tlock = threading.Lock()
        self._started = False
        for a in adapters:
            self._attach(a)

    def _attach(self, a):
        a.attach(Context(self.store, self.emit, self._incoming_for(a), lambda a=a: self._state_changed(a),
                         lambda local, n, a=a: self._set_unread(a, local, n), lambda local, a=a: self._read_elsewhere(a, local)))

    # ---- life ------------------------------------------------------------------------------------------------------
    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._wake_ev = asyncio.Event()
        if self._started:
            return
        self._started = True
        for a in self.adapters.values():
            try:
                await a.start()
                await a.sync()
            except Exception as e:                       # one broken service must not stop the others
                log.warning("adapter %s did not start: %r", a.account_id, e)
                self.emit("account", {"account": a.account_id, "note": f"start failed: {e}"})

    async def stop(self):
        for a in self.adapters.values():
            try:
                await a.stop()
            except Exception:
                pass

    # ---- the change log (long poll) --------------------------------------------------------------------------------
    def emit(self, kind, data):
        seq = self.store.add_event(kind, data)
        loop, ev = self._loop, self._wake_ev
        if loop is not None and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                pass
        return seq

    async def changes(self, since, wait=0.0, limit=500):
        """Events after `since`; waits up to `wait` seconds for the first one. The answer always carries `seq` to ask from next time."""
        deadline = time.monotonic() + max(0.0, min(float(wait), 55.0))
        while True:
            ev = self.store.events_since(since, limit)
            if ev or time.monotonic() >= deadline:
                return {"events": ev, "seq": ev[-1]["seq"] if ev else self.store.last_seq()}
            if self._wake_ev is None:
                await asyncio.sleep(0.2)
                continue
            self._wake_ev.clear()
            if self.store.events_since(since, 1):
                continue
            try:
                await asyncio.wait_for(self._wake_ev.wait(), timeout=max(0.05, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                pass

    # ---- accounts --------------------------------------------------------------------------------------------------
    def adapter(self, account_id):
        a = self.adapters.get(account_id)
        if a is None:
            raise AdapterError(f"no such account: {account_id}", "not-found")
        return a

    def accounts(self):
        out = []
        for a in self.adapters.values():
            st = a.state()
            linked = st["state"] == "linked"
            out.append({"id": a.account_id, "service": a.service, "role": a.role, "slot": a.slot, "label": a.label or a.service.capitalize(),
                        "state": st["state"], "detail": st.get("detail", ""), "since": st.get("since"),
                        "capabilities": a.capabilities(), "multi": a.MULTI,
                        "removable": a.slot > 1 or st["state"] != "not-linked",      # an unlinked first slot is the empty place of the service, there is nothing to delete
                        "may_send": linked and (a.role == "owner" or self.owner_may_write_self),
                        "unread": self.store.unread_total(role=a.role, service=a.service)})
        order = {s: i for i, s in enumerate(SERVICES)}
        out.sort(key=lambda x: (ROLES.index(x["role"]), order.get(x["service"], 99), x["slot"]))
        return out

    def add_account(self, service, role):
        """The account to link next for this service and role: the first slot while it is empty, or (services that allow it) a new slot after the last.
        Nothing is stored for the new slot until it is linked; if the link is cancelled or fails the empty slot goes again (see link_cancel / prune)."""
        if service not in SERVICES or role not in ROLES:
            raise AdapterError(f"unknown service or role: {service}/{role}", "bad-request")
        mine = [a for a in self.adapters.values() if a.service == service and a.role == role]
        free = next((a for a in sorted(mine, key=lambda x: x.slot) if a.state()["state"] == "not-linked"), None)
        if free is not None:
            return free
        if mine and all(a.state()["state"] == "unavailable" for a in mine):
            raise AdapterError(f"{mine[0].label} is not available on this device yet", "bad-request")
        if not mine or not mine[0].MULTI or self.factory is None:
            raise AdapterError(f"{mine[0].label if mine else service} takes one account for {'you' if role == 'owner' else assistant_name()}; delete or edit the one that is there", "bad-request")
        slot = max(a.slot for a in mine) + 1
        a = self.factory(service, role, slot)
        a.on_added()
        self.adapters[a.account_id] = a
        self._attach(a)
        if self._loop is not None:
            asyncio.ensure_future(a.start())
        self.emit("account", {"account": a.account_id, "added": True})
        return a

    def prune(self, account_id):
        """An added slot that was never linked (cancelled, failed) is dropped again, so the list does not fill with empty places."""
        a = self.adapters.get(account_id)
        if a is not None and a.slot > 1 and a.state()["state"] == "not-linked":
            self.adapters.pop(account_id, None)
            self._locks.pop(account_id, None)
            a.on_removed()
            asyncio.ensure_future(a.stop())
            self.emit("account", {"account": account_id, "removed": True})

    async def remove_account(self, account_id):
        """Delete an account: unlink it (the service, Caroline's copy of the credentials, the stored messages) and, for an added slot, take it out of the list.
        The first slot of a service stays as the empty place to link into."""
        a = self.adapter(account_id)
        if a.state()["state"] != "not-linked":
            await self.unlink(account_id)
        else:
            self.store.delete_account_data(account_id)
        if a.slot > 1:
            self.adapters.pop(account_id, None)
            self._locks.pop(account_id, None)
            a.on_removed()
            await a.stop()
            self.emit("account", {"account": account_id, "removed": True})

    def _state_changed(self, adapter):
        st = adapter.state()
        self.emit("account", {"account": adapter.account_id, "state": st["state"], "detail": st.get("detail", "")})

    async def link_start(self, account_id, params=None):
        a = self.adapter(account_id)
        params = params or {}
        if params.get("edit") and not a.prefill() and a.state()["state"] != "not-linked":
            await self.unlink(account_id)             # nothing to edit in place (a messenger): editing means linking another account in its place
        step = await a.link_start(params)
        self._state_changed(a)
        return step

    async def link_step(self, account_id, data):
        a = self.adapter(account_id)
        step = await a.link_step(data or {})
        self._state_changed(a)
        if step.get("step") == "done":
            try:
                await a.sync()
            except Exception as e:
                log.warning("sync after link %s: %r", account_id, e)
        return step

    async def link_cancel(self, account_id):
        a = self.adapter(account_id)
        await a.link_cancel()
        self._state_changed(a)
        self.prune(account_id)

    async def unlink(self, account_id, forget=True):
        a = self.adapter(account_id)
        await a.unlink()
        if forget:
            self.store.delete_account_data(account_id)
        self._state_changed(a)
        self.emit("conversations", {"account": account_id, "reset": True})

    # ---- what adapters report --------------------------------------------------------------------------------------
    def _incoming_for(self, adapter):
        def incoming(conv, msg=None):
            """conv: id (service-local), kind, title, avatar...; msg: ext_id, sender, sender_name, ts, text, subject, outgoing, attachments, state."""
            c = dict(conv)
            c.update(account=adapter.account_id, service=adapter.service, role=adapter.role)
            c["id"] = f"{adapter.account_id}/{conv['id']}"
            self.store.upsert_conversation(c)
            if msg is None:
                self.emit("conversation", {"conv": c["id"], "account": adapter.account_id})
                return None
            m = dict(msg)
            m.update(conv=c["id"], account=adapter.account_id)
            if msg.get("outgoing") and not m.get("actor"):
                m["actor"] = actors.claim(adapter.account_id, m.get("text", "")) or ACTOR_EXTERNAL
            saved, created = self.store.add_message(m)
            if created:
                now = self.store.get_conversation(c["id"]) or {}
                self.emit("message", {"conv": c["id"], "id": saved["id"], "account": adapter.account_id, "outgoing": saved["outgoing"],
                                      "service": adapter.service, "role": adapter.role, "title": c.get("title", ""),
                                      "text": saved["text"][:200], "sender_name": saved["sender_name"], "muted": bool(now.get("muted")),
                                      "files": len(saved.get("attachments") or []), "ts": saved.get("ts")})
            elif saved:
                self.emit("message-state", {"conv": c["id"], "id": saved["id"], "state": saved["state"]})
            return saved
        return incoming

    # ---- sending ---------------------------------------------------------------------------------------------------
    def _check_may_send(self, adapter, actor):
        st = adapter.state()["state"]
        if st != "linked":
            raise AdapterError(f"{adapter.account_id} is not linked ({st})", "not-linked")
        if adapter.role == "self" and actor == ACTOR_APP and not self.owner_may_write_self:
            raise AdapterError("Caroline's own accounts are read-only in the app", "forbidden")
        if actor not in (ACTOR_APP, *ACTORS_CAROLINE):
            raise AdapterError(f"unknown actor: {actor}", "forbidden")

    async def send(self, conv_id, text, subject=None, reply_to=None, actor=ACTOR_APP, files=None):
        conv = self.store.get_conversation(conv_id)
        if conv is None:
            raise AdapterError(f"no such conversation: {conv_id}", "not-found")
        a = self.adapter(conv["account"])
        self._check_may_send(a, actor)
        text = (text or "").strip()
        if not text and not files:
            raise AdapterError("nothing to send", "bad-request")
        ref = uuid.uuid4().hex
        pending, _ = self.store.add_message({
            "conv": conv_id, "account": a.account_id, "ext_id": None, "client_ref": ref, "sender": "me", "sender_name": "",
            "ts": time.time(), "text": text, "subject": subject or "", "outgoing": True, "state": "queued", "reply_to": reply_to, "actor": actor})
        self.emit("message", {"conv": conv_id, "id": pending["id"], "account": a.account_id, "outgoing": True, "service": a.service,
                              "role": a.role, "title": conv["title"], "text": text[:200], "actor": actor})
        lock = self._locks.setdefault(a.account_id, asyncio.Lock())
        async with lock:                                      # one queue per account, for both users of it
            try:
                ext = await a.send(conv_id.split("/", 1)[1], text, subject=subject, reply_to=reply_to, client_ref=ref)
                done = self.store.set_message_state(pending["id"], "sent", ext_id=ext)
            except Exception as e:
                done = self.store.set_message_state(pending["id"], "failed")
                self.emit("message-state", {"conv": conv_id, "id": pending["id"], "state": "failed", "error": str(e)})
                if isinstance(e, AdapterError):
                    raise
                raise AdapterError(f"send failed: {e}", "send-failed") from e
        self.emit("message-state", {"conv": conv_id, "id": pending["id"], "state": done["state"] if done else "sent"})
        if self.ledger:
            try:
                self.ledger(actor, a.account_id, conv["title"], text, subject or "")
            except Exception as e:
                log.warning("ledger: %r", e)
        return done or pending

    async def send_file(self, conv_id, path, caption=None, actor=ACTOR_APP):
        """One file (a path on this machine) into a conversation, with an optional caption; the same queue, state and actor record as text."""
        conv = self.store.get_conversation(conv_id)
        if conv is None:
            raise AdapterError(f"no such conversation: {conv_id}", "not-found")
        a = self.adapter(conv["account"])
        self._check_may_send(a, actor)
        if not a.capabilities().get("files"):
            raise AdapterError(f"{a.service} does not take files here", "bad-request")
        if not os.path.isfile(path):
            raise AdapterError("no such file", "bad-request")
        name = os.path.basename(path)
        caption = (caption or "").strip()
        ref = uuid.uuid4().hex
        mime = media.mime_of(name)
        att = [{"kind": media.kind_of(mime, name), "name": name, "size": os.path.getsize(path), "mime": mime, "ref": "", "local": path}]   # local: shown from the file we sent
        pending, _ = self.store.add_message({
            "conv": conv_id, "account": a.account_id, "ext_id": None, "client_ref": ref, "sender": "me", "sender_name": "",
            "ts": time.time(), "text": caption, "outgoing": True, "state": "queued", "actor": actor, "attachments": att})
        self.emit("message", {"conv": conv_id, "id": pending["id"], "account": a.account_id, "outgoing": True, "service": a.service,
                              "role": a.role, "title": conv["title"], "text": caption[:200], "actor": actor, "files": 1})
        async with self._locks.setdefault(a.account_id, asyncio.Lock()):
            try:
                ext = await a.send_file(conv_id.split("/", 1)[1], path, caption=caption or None, client_ref=ref)
                done = self.store.set_message_state(pending["id"], "sent", ext_id=ext)
            except Exception as e:
                done = self.store.set_message_state(pending["id"], "failed")
                self.emit("message-state", {"conv": conv_id, "id": pending["id"], "state": "failed", "error": str(e)})
                if isinstance(e, AdapterError):
                    raise
                raise AdapterError(f"send failed: {e}", "send-failed") from e
        self.emit("message-state", {"conv": conv_id, "id": pending["id"], "state": done["state"] if done else "sent"})
        if self.ledger:
            try:
                self.ledger(actor, a.account_id, conv["title"], f"[file {name}] {caption}".strip(), "")
            except Exception as e:
                log.warning("ledger: %r", e)
        return done or pending

    async def download(self, conv_id, message_id, index, dest_dir):
        """Fetch one attachment of a stored message into dest_dir; returns the path of the file."""
        conv = self.store.get_conversation(conv_id)
        m = self.store.get_message(int(message_id))
        if conv is None or m is None or m["conv"] != conv_id:
            raise AdapterError("no such message", "not-found")
        atts = m.get("attachments") or []
        if not 0 <= int(index) < len(atts):
            raise AdapterError("no such attachment", "not-found")
        att = atts[int(index)]
        os.makedirs(dest_dir, exist_ok=True)
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", att.get("name") or "file").strip(". ") or "file"
        dest = os.path.join(dest_dir, name)
        base, ext = os.path.splitext(dest)
        n = 1
        while os.path.exists(dest):                 # never overwrite what the owner already has
            dest = f"{base} ({n}){ext}"; n += 1
        try:
            got = await self.adapter(conv["account"]).download(att.get("ref") or "", dest)
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"download failed: {e}", "send-failed") from e
        return got or dest

    async def cached(self, conv_id, message_id, index, cache_dir, home=None):
        """One attachment to be shown: the file we sent (still where it was, inside `home`), else the copy in cache_dir, fetched on the first
        call only. Returns (path, attachment). The cache is trimmed after each fetch; Downloads is not touched."""
        conv = self.store.get_conversation(conv_id)
        m = self.store.get_message(int(message_id))
        if conv is None or m is None or m["conv"] != conv_id:
            raise AdapterError("no such message", "not-found")
        atts = m.get("attachments") or []
        if not 0 <= int(index) < len(atts):
            raise AdapterError("no such attachment", "not-found")
        att = atts[int(index)]
        local = att.get("local")
        if local and os.path.isfile(local) and home and os.path.realpath(local).startswith(home + os.sep):
            return os.path.realpath(local), att
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(cache_dir, f"{int(message_id)}-{int(index)}-{media.ensure_ext(att.get('name'), att.get('mime', ''), att.get('kind', 'file'))}")
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            os.utime(dest, None)                          # used: it stays longer
            return dest, self._learn_size(m, atts, int(index), dest)
        part = dest + ".part"
        try:
            got = await self.adapter(conv["account"]).download(att.get("ref") or "", part)
            os.replace(got or part, dest)
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"download failed: {e}", "send-failed") from e
        finally:
            if os.path.exists(part):
                os.remove(part)
        media.trim(cache_dir, keep=dest)
        return dest, self._learn_size(m, atts, int(index), dest)

    def _learn_size(self, m, atts, index, path):
        """A service that did not say how big an attachment is (mail) gets the real size once the file is in hand."""
        att = atts[index]
        if not att.get("size"):
            atts[index] = att = {**att, "size": os.path.getsize(path)}
            self.store.update_message(m["id"], attachments=atts)
        return att

    def set_muted(self, conv_id, muted):
        if not self.store.set_muted(conv_id, muted):
            raise AdapterError(f"no such conversation: {conv_id}", "not-found")
        self.emit("conversation", {"conv": conv_id, "muted": bool(muted)})
        return {"muted": bool(muted)}

    async def mark_read(self, conv_id):
        conv = self.store.get_conversation(conv_id)
        if conv is None:
            raise AdapterError(f"no such conversation: {conv_id}", "not-found")
        try:                                   # the adapter first: it may need to see which messages are still unread
            await self.adapter(conv["account"]).mark_read(conv_id.split("/", 1)[1])
        except Exception as e:
            log.warning("mark_read on service: %r", e)
        n = self.store.mark_read(conv_id)
        self.emit("read", {"conv": conv_id, "account": conv["account"], "n": n})
        return {"marked": n}

    async def history(self, conv_id, before=None, n=50):
        """The stored past; when the store has fewer than n older messages, the adapter is asked for more (protocols that have history)."""
        conv = self.store.get_conversation(conv_id)
        if conv is None:
            raise AdapterError(f"no such conversation: {conv_id}", "not-found")
        rows = self.store.history(conv_id, before, n)
        if len(rows) < n and self.adapter(conv["account"]).capabilities().get("history"):
            oldest = rows[0]["id"] if rows else before
            try:
                added = await self.adapter(conv["account"]).fetch_history(conv_id.split("/", 1)[1], oldest, n - len(rows))
                if added:
                    rows = self.store.history(conv_id, before, n)
            except Exception as e:
                log.warning("fetch_history %s: %r", conv_id, e)
        try:
            if await self.adapter(conv["account"]).prepare(conv_id.split("/", 1)[1], rows):
                rows = self.store.history(conv_id, before, n)
        except Exception as e:
            log.warning("prepare %s: %r", conv_id, e)
        return rows

    def _set_unread(self, adapter, local, n):
        cid = f"{adapter.account_id}/{local}"
        self.store.set_unread(cid, n)
        self.emit("conversation", {"conv": cid, "account": adapter.account_id})

    def _read_elsewhere(self, adapter, local):
        cid = f"{adapter.account_id}/{local}"
        n = self.store.mark_read(cid)
        self.emit("read", {"conv": cid, "account": adapter.account_id, "n": n})
