# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The HTTP API of Communicator, mounted on Caroline's backend (or on the dev server): /api/ic/messages/*.

Conversation ids contain '/', so they travel as the `conv` query parameter or a field of a JSON body, never as a path part.
Errors are {"error": text, "code": code} with the status that fits the code.
"""
import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from . import media
from .adapter import AdapterError
from .registry import ACTOR_APP

STATUS = {"not-found": 404, "not-linked": 409, "forbidden": 403, "bad-request": 400, "send-failed": 502}
DOWNLOADS = "Downloads"      # where an attachment lands, inside the device's home (the SD card)


def home_root():
    """The device's home (the SD card): files the app sends come from it, downloads go into it. INTELLICAT_HOME is set by the simulator /
    the firmware for the shell and inherited by her backend."""
    h = os.environ.get("INTELLICAT_HOME")
    return os.path.realpath(h) if h else None


def build_router(reg, fake=False):
    r = APIRouter(prefix="/api/ic/messages")

    def err(e):
        return JSONResponse({"error": str(e), "code": e.code}, status_code=STATUS.get(e.code, 400))

    def in_home(rel):
        """A path given relative to the home, resolved and checked to stay inside it (no .., no links out)."""
        root = home_root()
        if not root:
            raise AdapterError("the device home is not known", "bad-request")
        full = os.path.realpath(os.path.join(root, str(rel).lstrip("/\\")))
        if full != root and not full.startswith(root + os.sep):
            raise AdapterError("the file is outside the device's home", "forbidden")
        return full

    async def body(request):
        try:
            return await request.json() or {}
        except Exception:
            return {}

    @r.get("/health")
    async def health():
        return {"ok": True, "accounts": len(reg.adapters), "seq": reg.store.last_seq(), "fake": fake}

    # ---- accounts --------------------------------------------------------------------------------------------------
    @r.get("/accounts")
    async def accounts():
        return {"accounts": reg.accounts(), "unread": reg.store.unread_total(role="owner")}

    @r.post("/accounts")
    async def add_account(request: Request):
        """Add an account: {"service", "role"}. Answers the account to link (the empty place of the service, or a new mailbox slot); link it with /link/start."""
        b = await body(request)
        try:
            a = reg.add_account(str(b.get("service", "")), str(b.get("role", "owner")))
            return {"account": next(x for x in reg.accounts() if x["id"] == a.account_id)}
        except AdapterError as e:
            return err(e)

    @r.delete("/accounts/{aid}")
    @r.post("/accounts/{aid}/delete")          # the shell passes GET, POST and PUT through to this API: the apps delete with the POST
    async def delete_account(aid: str):
        try:
            await reg.remove_account(aid)
            return {"ok": True}
        except AdapterError as e:
            return err(e)

    @r.post("/accounts/{aid}/link/start")
    async def link_start(aid: str, request: Request):
        try:
            return await reg.link_start(aid, await body(request))
        except AdapterError as e:
            return err(e)

    @r.post("/accounts/{aid}/link/step")
    async def link_step(aid: str, request: Request):
        try:
            return await reg.link_step(aid, await body(request))
        except AdapterError as e:
            return err(e)

    @r.get("/accounts/{aid}/qr")
    async def qr(aid: str):
        """The picture of the code to scan while linking an account that links by QR (WhatsApp, Signal): drawn by Caroline's own channel module, served as it is."""
        try:
            png = reg.adapter(aid).qr_image() if hasattr(reg.adapter(aid), "qr_image") else None
        except AdapterError as e:
            return err(e)
        if not png:
            return JSONResponse({"error": "no code to scan right now", "code": "not-found"}, status_code=404)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @r.post("/accounts/{aid}/link/cancel")
    async def link_cancel(aid: str):
        try:
            await reg.link_cancel(aid)
            return {"ok": True}
        except AdapterError as e:
            return err(e)

    @r.post("/accounts/{aid}/unlink")
    async def unlink(aid: str, request: Request):
        try:
            await reg.unlink(aid, forget=bool((await body(request)).get("forget", True)))
            return {"ok": True}
        except AdapterError as e:
            return err(e)

    # ---- conversations and messages --------------------------------------------------------------------------------
    @r.get("/conversations")
    async def conversations(account: str = "", service: str = "", role: str = "", q: str = "", limit: int = 200, offset: int = 0, unread: int = 0):
        rows = reg.store.list_conversations(account or None, service or None, role or None, q or None, limit, offset, bool(unread))
        return {"conversations": rows}

    @r.get("/messages")
    async def messages(conv: str, before: int = 0, limit: int = 50):
        try:
            rows = await reg.history(conv, before or None, min(int(limit), 200))
        except AdapterError as e:
            return err(e)
        c = reg.store.get_conversation(conv)
        return {"conversation": c, "messages": rows, "draft": reg.store.get_draft(conv), "has_more": len(rows) >= int(limit)}

    @r.post("/send")
    async def send(request: Request):
        b = await body(request)
        try:
            files = [in_home(p) for p in (b.get("files") or [])]    # paths relative to the device's home; checked before anything is sent
            conv = b.get("conv", "")
            if not files:
                m = await reg.send(conv, b.get("text", ""), subject=b.get("subject"), reply_to=b.get("reply_to"), actor=ACTOR_APP)
                reg.store.set_draft(conv, "")
                return {"message": m}
            sent = []                                                # the text is the caption of the first file
            for i, path in enumerate(files):
                sent.append(await reg.send_file(conv, path, caption=b.get("text", "") if i == 0 else "", actor=ACTOR_APP))
            reg.store.set_draft(conv, "")
            return {"message": sent[-1], "messages": sent}
        except AdapterError as e:
            return err(e)

    @r.post("/download")
    async def download(request: Request):
        """{conv, id, index} -> the attachment is fetched into <home>/Downloads; the answer carries its path relative to the home."""
        b = await body(request)
        try:
            root = home_root()
            if not root:
                raise AdapterError("the device home is not known", "bad-request")
            got = await reg.download(b.get("conv", ""), b.get("id", 0), b.get("index", 0), os.path.join(root, DOWNLOADS))
            return {"path": os.path.relpath(os.path.realpath(got), root).replace(os.sep, "/")}
        except AdapterError as e:
            return err(e)

    @r.post("/attachment")
    async def attachment(request: Request):
        """{conv, id, index} -> the attachment is fetched into the cache (once) to be shown, not saved: the answer has its path relative to
        the home (for Platform.files.imageUrl / audioUrl / videoUrl), kind, mime, name and size. /download is the owner's copy in Downloads."""
        b = await body(request)
        try:
            root = home_root()
            if not root:
                raise AdapterError("the device home is not known", "bad-request")
            got, att = await reg.cached(b.get("conv", ""), b.get("id", 0), b.get("index", 0), os.path.join(root, media.CACHE_DIR), home=root)
            return {"path": os.path.relpath(os.path.realpath(got), root).replace(os.sep, "/"), "kind": att.get("kind", "file"),
                    "mime": att.get("mime", ""), "name": att.get("name", ""), "size": os.path.getsize(got)}
        except AdapterError as e:
            return err(e)

    @r.post("/mute")
    async def mute(request: Request):
        b = await body(request)
        try:
            return reg.set_muted(b.get("conv", ""), bool(b.get("muted", True)))
        except AdapterError as e:
            return err(e)

    @r.post("/read")
    async def read(request: Request):
        try:
            return await reg.mark_read((await body(request)).get("conv", ""))
        except AdapterError as e:
            return err(e)

    @r.put("/draft")
    async def draft(request: Request):
        b = await body(request)
        reg.store.set_draft(b.get("conv", ""), b.get("text", ""))
        return {"ok": True}

    @r.get("/search")
    async def search(q: str, service: str = "", conv: str = "", role: str = "", since: float = 0, limit: int = 50):
        return {"results": reg.store.search(q, service or None, conv or None, role or None, since or None, limit)}

    @r.get("/unread")
    async def unread():
        return {"total": reg.store.unread_total(role="owner"), "all": reg.store.unread_total(), "conversations": reg.store.unread_by_conversation(role="owner")}

    @r.get("/changes")
    async def changes(since: int = 0, wait: float = 0):
        return await reg.changes(since, wait)

    # ---- the test hooks (only with the fake adapters) --------------------------------------------------------------
    if fake:
        @r.post("/_fake/incoming")
        async def fake_incoming(request: Request):
            b = await body(request)
            reg.adapter(b["account"]).simulate_incoming(b["conv"], b["text"], b.get("sender_name"), b.get("subject", ""), bool(b.get("outgoing")),
                                                        b.get("attachments"))
            return {"ok": True}

        @r.post("/_fake/revoke")
        async def fake_revoke(request: Request):
            reg.adapter((await body(request))["account"]).simulate_revoke()
            return {"ok": True}

        @r.post("/_fake/scan")
        async def fake_scan(request: Request):
            await reg.adapter((await body(request))["account"]).simulate_scan()
            return {"ok": True}

    return r
