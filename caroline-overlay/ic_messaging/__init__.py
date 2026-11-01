# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Communicator's backend: the account registry, the message store and the API, shared by the app and Caroline's tools.
See docs/usage.md. Not part of her code: it lives in the overlay and is mounted on her backend by hooks.py."""
import os

_registry = None
_fake = False


def _workspace():
    return os.environ.get("CAROLINE_WORKSPACE_DIR") or os.path.expanduser("~/.intellicat/messaging")


def _ledger(actor, account, title, text, subject):
    """The send goes into her action ledger like one of her own: she sees it in what was done on the owner's behalf."""
    from app import action_ledger
    action_ledger.append(_workspace(), None, "communicator_send", {"chat": f"{title} ({account}) by {actor}", "text": text, "subject": subject})


def build(workspace=None, fake=None, db=None, **fake_kw):
    """The registry for this process. fake=True: scripted accounts (no network). Extra keyword arguments go to the fake adapters."""
    global _registry, _fake
    from .registry import Registry
    from .store import Store
    from .adapter import SERVICES, ROLES
    ws = workspace or _workspace()
    os.makedirs(ws, exist_ok=True)
    store = Store(db or os.environ.get("IC_MESSAGING_DB") or os.path.join(ws, "ic_messaging.db"))
    _fake = bool(os.environ.get("IC_MESSAGING_FAKE")) if fake is None else fake
    if _fake:
        from .fake import FakeAdapter
        mode = os.environ.get("IC_MESSAGING_FAKE", "")
        kw = dict(fake_kw)
        forced = kw.pop("linked", None)
        def initially(s, r):
            if forced is not None:
                return forced
            return mode != "unlinked" and (r == "owner" or s in ("telegram", "mail"))
        adapters = [FakeAdapter(s, r, linked=initially(s, r), **kw) for r in ROLES for s in SERVICES]
        factory = lambda s, r, n: FakeAdapter(s, r, linked=False, slot=n, **kw)      # noqa: E731
    else:
        from .real import real_adapters, _make as factory
        adapters = real_adapters()
    ledger = None
    try:
        import app.action_ledger  # noqa: F401  (only inside her backend)
        ledger = _ledger
    except Exception:
        pass
    _registry = Registry(store, adapters, ledger=ledger, factory=factory)
    return _registry


def get_registry():
    if _registry is None:
        build()
    return _registry


def is_fake():
    return _fake
