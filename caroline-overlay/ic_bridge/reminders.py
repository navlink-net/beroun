# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""A reminder that another assistant of the slot scheduled (her tabId "assistant:<tab>") comes due in her scheduler: it goes to the shell
(POST /api/assistant/remind {tab, text}), which runs it as a turn of that assistant's tab and shows what it says as a notification. Hers stay hers."""
from beroun_tools import shell

from . import TAB_PREFIX

SERVICE = ("Nobody prompted you for this: it is routine maintenance that does not concern the owner. Do it now, silently, and reply with exactly [[NO_UPDATE]] "
           "unless something is broken and needs the owner's attention.")
USER = ("Nobody prompted you for this: it is a follow-up you scheduled yourself. Do it now, without narrating the steps, and when you are done tell the owner "
        "the outcome in one short message (it is shown as a notification).")


def text_of(reminder):
    last = (reminder.get("lastReport") or {}).get("text")
    kind = SERVICE if reminder.get("eventType") == "service" else USER
    return (f"⏰ Reminder due (you scheduled this for {reminder.get('dueAtIso')}, id {reminder.get('id')}): {reminder.get('note')}"
            + (f"\n\nWhat you reported last time; check everything fresh, as of now:\n{last}" if last else "") + f"\n\n{kind}")


def deliver(tab, text):
    """Whether the shell took it (it answers at once: the turn runs on its own); False: her scheduler tries again on its next tick."""
    try:
        return bool(shell._request("/api/assistant/remind", {"tab": tab, "text": text}, timeout=3.0).get("delivered"))
    except Exception:   # noqa: BLE001  the shell or the assistant is not there now
        return False


def wrap(on_due):
    """Her scheduler's callback (app.main._on_reminder_due) with the reminders of another assistant taken out of it."""
    def on_reminder_due(reminder):
        tab = str(reminder.get("tabId") or "")
        if not tab.startswith(TAB_PREFIX):
            return on_due(reminder)
        return deliver(tab[len(TAB_PREFIX):], text_of(reminder))
    on_reminder_due.__wrapped__ = on_due
    return on_reminder_due
