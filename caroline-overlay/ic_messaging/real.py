# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The adapters of the real accounts: one per service and role. A service whose adapter is not written yet (or whose channel module cannot be
imported) shows as unavailable in the app. Each stage of docs/usage.md replaces one of them."""
import logging

from .adapter import Adapter, SERVICES, ROLES

log = logging.getLogger("ic_messaging")
LABELS = {"mail": "Mail", "whatsapp": "WhatsApp", "telegram": "Telegram", "signal": "Signal", "slack": "Slack"}


class Unavailable(Adapter):
    def __init__(self, service, role, why="not implemented yet"):
        super().__init__()
        self.service, self.role, self.label, self._why = service, role, LABELS[service], why

    def state(self):
        return {"state": "unavailable", "detail": self._why, "since": None}


def _make(service, role, slot=1):
    try:
        if service == "mail":
            from .mail_adapter import MailAdapter
            import app.plugins.email_plugin  # noqa: F401  (only inside her backend)
            return MailAdapter(role, slot)
        if service == "signal":
            from .signal_adapter import SignalAdapter
            import app.signal_channel  # noqa: F401  (only inside her backend)
            return SignalAdapter(role, slot)
        if service == "whatsapp":
            from .whatsapp_adapter import WhatsAppAdapter
            import app.whatsapp_channel  # noqa: F401  (only inside her backend)
            return WhatsAppAdapter(role, slot)
        if service == "slack":
            from .slack_adapter import SlackAdapter
            import app.slack_channel  # noqa: F401  (only inside her backend)
            return SlackAdapter(role, slot)
        if service == "telegram":
            from .telegram_adapter import TelegramAdapter
            import app.telegram_channel  # noqa: F401  (only inside her backend)
            return TelegramAdapter(role, slot)
    except Exception as e:
        log.warning("%s:%s adapter unavailable: %r", service, role, e)
        return Unavailable(service, role, f"unavailable: {e}"[:100])
    return Unavailable(service, role)


def _saved_slots():
    """[(service, role, slot)] of the accounts that were added in an earlier run (channels.py keeps the list)."""
    out = []
    try:
        from . import channels
        for service, names in channels.load().items():
            for name in names:
                role, slot = channels.split(name)
                out.append((service, role, slot))
        from .mail_adapter import MailAdapter
        for key in MailAdapter("owner")._mapping():                      # mail: the mailboxes that were linked (the map of the adapter is their record)
            role, _, slot = key.partition("#")
            if slot.isdigit() and int(slot) > 1 and role in ROLES:
                out.append(("mail", role, int(slot)))
    except Exception as e:
        log.warning("added accounts not restored: %r", e)
    return out


def real_adapters():
    return [_make(s, r) for r in ROLES for s in SERVICES] + [_make(s, r, n) for s, r, n in _saved_slots()]
