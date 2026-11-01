# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""One adapter per service and role: the same interface for Telegram, WhatsApp, Signal, Slack and mail.

An adapter does not keep messages (the store does) and does not decide who may send (the registry does). It knows how to reach one real
account: link it, read its conversations and history, send, and tell the registry about what arrives (`ctx.incoming`). What a protocol cannot
do is said by `capabilities()`; the app greys such controls out instead of pretending.
"""

SERVICES = ("mail", "whatsapp", "telegram", "signal", "slack")
ROLES = ("owner", "self")        # the human's own account, and Caroline's own
STATES = ("linked", "connecting", "needs-attention", "not-linked", "unavailable")

# the link flow is a sequence of steps; each answer of link_start/link_step is one of these
#   {"step": "qr", "data": "<text to draw as a QR code>"}             draw it, then the adapter finishes by itself (state events)
#   {"step": "code", "prompt": "..."}                                 ask for a code, answer with link_step({"code": ...})
#   {"step": "password", "prompt": "..."}                             ask for a password (2FA), answer with link_step({"password": ...})
#   {"step": "form", "fields": [{"name","label","secret","preset"}]}  ask for several values, answer with link_step({name: value, ...})
#   {"step": "open_page", "url": "...", "prompt": "..."}              a page to finish the login in
#   {"step": "wait", "prompt": "..."}                                 nothing to do for the user
#   {"step": "done"}                                                  linked
#   {"step": "error", "error": "..."}                                 failed, nothing is half done


class AdapterError(Exception):
    def __init__(self, message, code="error"):
        super().__init__(message)
        self.code = code


class Context:  # incoming(conv, msg); set_unread(local_conv_id, n); read_elsewhere(local_conv_id)
    """What the registry gives an adapter: the store and the ways to report things."""

    def __init__(self, store, emit, incoming, state_changed, set_unread=None, read_elsewhere=None):
        self.store, self.emit, self.incoming, self.state_changed = store, emit, incoming, state_changed
        self.set_unread, self.read_elsewhere = set_unread, read_elsewhere


class Adapter:
    service = ""
    role = "owner"
    label = ""
    MULTI = False        # True: the service may have more than one account per role (mail: any number of mailboxes); the others are bound to the one
                         # account per role that Caroline's own channel module serves
    CHANNEL = False      # True: served by one of her channel modules (channels.py makes an added account known to it)
    slot = 1             # 1 = the first account of the service and role (id "mail:owner"); 2... = the added ones (id "mail:owner:2")

    def __init__(self):
        self.ctx = None

    @property
    def account_id(self):
        return f"{self.service}:{self.role}" + (f":{self.slot}" if self.slot > 1 else "")

    @property
    def acct(self):
        """The name of this account in Caroline's channel modules (their `account` argument): "owner" / "self" for the first, "owner2" / "self2"... for the added ones."""
        return self.role if self.slot == 1 else f"{self.role}{self.slot}"

    def attach(self, ctx):
        self.ctx = ctx

    def on_added(self):
        """The registry added this account (a slot after the first): a service served by one of her channel modules makes the name known to it and starts its loop."""
        if self.slot > 1 and self.CHANNEL:
            from . import channels
            channels.register(self.service, self.acct)
            channels.remember(self.service, self.acct)
            channels.start_loop(self.service, self.acct)

    def on_removed(self):
        if self.slot > 1 and self.CHANNEL:
            from . import channels
            channels.forget(self.service, self.acct)

    # ---- what it can do ------------------------------------------------------------------------------------------
    def capabilities(self):
        return {"history": False, "send": True, "files": False, "read_marks": False, "search": False}

    def state(self):
        """{"state": one of STATES, "detail": str, "since": float|None}"""
        return {"state": "unavailable", "detail": "not implemented yet", "since": None}

    # ---- lifecycle -------------------------------------------------------------------------------------------------
    async def start(self):
        """Open the live connection if the account is linked; report events through self.ctx."""

    async def stop(self):
        pass

    # ---- linking ---------------------------------------------------------------------------------------------------
    async def link_start(self, params):
        return {"step": "error", "error": "linking is not available for this service yet"}

    async def link_step(self, data):
        return {"step": "error", "error": "no login in progress"}

    async def link_cancel(self):
        pass

    async def unlink(self):
        raise AdapterError("not linked", "not-linked")

    def prefill(self):
        """The values an edit of this account starts from (link_start with edit=True): {field name: value}; empty where the service has nothing to edit."""
        return {}

    # ---- data ------------------------------------------------------------------------------------------------------
    async def sync(self):
        """Read the conversation list (and anything new) and give it to the store through self.ctx."""

    async def fetch_history(self, conv, before=None, n=50):
        """Ask the service for older messages of a conversation and store them; returns how many were added. 0 where the protocol has no history."""
        return 0

    async def send(self, conv, text, subject=None, reply_to=None, client_ref=None):
        """Send text to a stored conversation; returns the external id of the message (or None when the service gives none)."""
        raise AdapterError("sending is not available for this service yet")

    async def send_file(self, conv, path, caption=None, client_ref=None):
        raise AdapterError("files are not available for this service yet")

    async def mark_read(self, conv):
        pass

    async def prepare(self, conv, rows):
        """Called when a conversation is opened, with the stored messages about to be shown: fill in what was stored only in part (a mail body
        that was fetched lazily). Returns True when it changed something."""
        return False

    async def download(self, ref, dest):
        raise AdapterError("attachments are not available for this service yet")
