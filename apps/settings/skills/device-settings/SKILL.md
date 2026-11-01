---
name: device-settings
description: How to read and change the device's interface language, keyboard layouts, date, time, time zone and network time. Use this when the user asks to change the language, the keyboard layouts, the clock or the time zone of the device.
---

# Device settings

1. `settings_get_settings` returns the current language, the enabled keyboard layouts, every available code, and the date, time, time zone and whether automatic date and time (NTP) is on and synchronized.
2. `settings_set_language` with a `code` changes the language of the whole interface. The Settings page reloads in the new language; tell the user it was done.
3. `settings_set_layouts` with `codes` such as `en,ru` sets which layouts the on-screen keyboard offers (at least one stays on).
4. `settings_set_timezone` with `zone` (an IANA name like `Europe/Prague`) sets the time zone.
5. `settings_set_auto_time` with `on` = `true` / `false` turns network time on or off. With it on the clock follows the network and cannot be set by hand.
6. `settings_set_time` with `date` (`YYYY-MM-DD`) and `time` (`HH:MM`) sets the clock by hand, in the device's time zone. It is refused while automatic date and time is on: turn that off first, and only if the user wants a manual time.

7. `settings_get_accounts` lists the mail and messenger accounts (the same ones Communicator and you use): id such as `mail:owner` or `mail:owner:2`, service, whose, state.
8. `settings_remove_account` with `id` deletes one account and its stored messages. Ask the owner first: it cannot be undone.
9. `settings_open_accounts` opens Settings > Accounts. Adding or editing an account needs the owner's password, code or phone, so open the page and let the owner do it; do not ask for passwords in the chat. Every service takes any number of accounts (ids like `whatsapp:owner:2`; her own WhatsApp, Signal and Telegram tools take `account="owner2"` for it; an added Slack workspace is reached through the `messages_*` tools).

Change these only when the user asks. The language applies to every app at once. The network is in the Wi-Fi app (`app-wifi`), not here.
