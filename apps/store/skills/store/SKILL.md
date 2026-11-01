---
name: store
description: How to find, install, update and remove the apps of OS Beroun. Use this when the user asks for a new app, wants an app gone, or asks what can be updated.
---

# The store

The store lists the apps of apps.navlink.net for OS Beroun. Every app is signed; the device installs nothing that is not.

1. `beroun_store_catalog` lists every app the owner can act on, with its `state`: `available` (not on the device), `installed`, `update` (a newer version is in the store), `system` (a system app, up to date), `unsupported` (needs what the device lacks). `q` narrows by name.
2. Before installing, tell the owner what the app asks for: `summary` has its capabilities (`required` ones are needed), permissions, background services and agents. Install only when the owner asked: `beroun_store_install` with `app` (the id).
3. `beroun_store_updates` lists what can be updated; `beroun_store_install` on an installed app updates it, and its data stays.
4. `beroun_store_remove` removes an app and deletes its data (for an app that came with the device: it is hidden and its data stays; install it again to bring it back). Only when the owner asked.

System apps (Browser, Settings, Ratatosk, Wi-Fi, the Store) can be updated but never removed. If the device is offline the catalog shows only what is on the device.
