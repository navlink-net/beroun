# Apps

The field-by-field manifest reference is `docs/app-format.md`. This document explains the model around it.

## What an app is

A folder with a `manifest.json` (format 1): `apps/<id>/` for the firmware's apps (served at `/apps/<id>/…`), `~/.intellicat/apps/<id>/` for apps installed from the store.
A **view of the system** (Caroline's chat, voice view, wizard, the external screen's cat) is `system/<id>/view.json` in the same format, served at `/system/<id>/…`; it is never an app
and cannot be moved or removed.

| Kind | How it runs |
| --- | --- |
| Web app, shared window (`frontend.window: "shared"`; most apps) | Its page is shown in the launcher's `cog`, one at a time; must keep its state |
| Web app, own window (the default if omitted) | A `cog` of its own |
| Native (`native.cmd`) | A program of its own (the Browser's `ic-browser`) |
| Hidden (`tile.hidden`) | Launched by the system with no tile (e.g. the Minecraft player) |

Any app may also declare services, endpoints, a big page for the external display, permissions, capabilities, a `caroline` section and agents.

## The firmware's apps

| App | Notes |
| --- | --- |
| Browser (system) | Native `ic-browser` on WPE, tabs; refuses `file:` in the online profile |
| Settings (system) | Network, accounts (owner's and Caroline's), date & time, do-not-disturb, vibration, auto-update of apps, Agents (read-only), About, Caroline's panel |
| Wi-Fi (system) | Scan/connect through `netctl` |
| Ratatosk (system) | Messenger with end-to-end encryption, groups, attachments, LiveKit audio/video calls; a module service inside `shelld` |
| Store (system) | Install/update/remove apps; permission `apps.manage` |
| Communicator | Mail, WhatsApp, Telegram, Signal, Slack in one style; backend is Caroline's message store (`ic_messaging`) |
| Notes | The owner's SquirrelWisdom notes (the same as on the website), kept on the device and synced by `notesd` (permission `account.session`; key `notes_key` in `keys.json`); Caroline's own `Caroline:*` folders are hidden |
| Books | FB2 reader for the SquirrelWisdom library (public and own books), parsed and kept on the device by `booksd` (permission `account.session`; key `books_key` in `keys.json`, only to learn the account folder); the place of reading is kept per book; local `.fb2` files from Files come later |
| Files, Notepad, Calculator | File manager (`opens` maps file types to apps), plain-text notes, calculator |
| Music, Video | Players with a remembered place; WPE needs a user gesture before sound |
| Calendar (`calendard`), Clock (`alarmd`) | Services that ring in sleep |
| Knights & Ladies, Pirates, Quidditch | Games |
| Minecraft (`minecraftd`) | Client of the cloud Minecraft of SquirrelWisdom; big page is a native player (`minecraft-player`) |
| `ext-a`, `ext-b`, `mc-probe` | Hidden test apps |

*System* apps can be updated by the store but never removed; apps that ship with the firmware without `system` are *preinstalled* (the store can hide and restore them, their data stays);
everything else is *from the store*.

## The contract: state

A shared-window app sets `"state": true`, uses `Platform.state` and `Platform.onClose`, and **comes back to the same place** when opened again (open file, folder, dialogue, scroll). The manifest
checker verifies that the page uses `Platform.state`. An app must save its place **before** calling `Platform.exit()` (closing is immediate).

```js
Platform.onClose(async () => Platform.state.set({ file, scroll }));
const saved = await Platform.state.get();      // object or null
```

## The `Platform` API (`web-shell/platform.js`)

Every page includes `/platform.js`. Areas:

* lifecycle: `state`, `onClose`, `exit`; translations: `t` (one key per phrase in `web-shell/i18n/<lang>.json`, named `{placeholders}`)
* system: `messageBox`, `sound`, `vibra`, `notify`, `notifications` (+ `.on`), `display` (external display), see [SYSTEM_API.md](SYSTEM_API.md)
* Caroline: `tools.handle(name, fn)` answers her page tools; apps with `caroline.ask` may ask her (`POST /api/caroline/ask`)
* agents: `sw.call(command, params, agent?)` — an agent's call of Camerlengo through the shell
* files over `/api/fs/*` (the storage card only); DOM helpers (`dom_*`) she uses to read, press and type in an app
* planned for Android, not built: `capabilities()`, `device()`
* 3D: the system serves Babylon.js at `/lib/babylon/` and the cursor-key helper `/lib/beroun-3d.js`; template `apps/babylon-hello`, see "3D (Babylon.js)" in `docs/system-api.md`

## The logical input model

The OS is driven by a gamepad-style input set plus touch: D-pad, sticks, **A, B, X, Y**, **L1, L2, R1, R2**, **START, SELECT, MODE**, **POWER**. Rules (owner's decisions, 2026-10-27/28):

* Free for apps: the **cursor** (D-pad), **A** (Enter) and **B** (Escape / back; at an app's top level it leaves the app).
* **MODE** always leaves the app. **L1/L2** are volume. **POWER** is the power key (short press sleep, long press off). **R2** is Caroline's voice dialogue. No app sees these.
* X, Y, R1, START and SELECT are not given a job in any app without the owner's say-so. SELECT moves the focus between screens when an external display is in.
* With the on-screen keyboard (`osk.js`) open, B backspaces while the field has text; on an empty field B reaches the page as Escape ("leave the field", then "back"); Y hides the keyboard. Treat Escape in a field as "leave the field".
* Left/Right page through long lists where a screen has no other use for them; a finger drag (a mouse in the simulator) scrolls everywhere (`platform.js`).

Which physical control produces which logical button is a target's business (see [TARGETS.md](TARGETS.md)).

## Caroline drives apps

An app's `caroline` section turns it into her tool group `app-<id>`: `description` (required), `skills` (folders with `SKILL.md` joining her catalog), `mcp` (stdio or http MCP servers with `{app}`/`{port}`), `tools` (page tools
answered by `Platform.tools.handle`; the app is opened first). Without the section an app is still reachable through her `beroun_*` group (list, open, close, read, press, type). Code: `caroline-overlay/ic_apps`.

## Agents and A2A

An app may publish **agents**: `id`, `description`, `skills` (name, input types, output text/json, `long`, `confirm`), `host` (a service endpoint, an MCP server, the page, or a remote A2A agent), the Camerlengo commands it may call (`camerlengo`)
and system `permissions` (`files.read`, `files.write`, `net.status`, `net.manage`, `settings.read`, `settings.write`, `notifications.read`, `notifications.post`, `sound`, `vibration`, `message_box`, `apps.open`). The manifest is the approval.

* **Broker** (`runtime/agents.py`): registry from manifests; tasks `submitted → working → input-required → completed | failed | canceled`; questions to the owner (with choices; unanswered for `INTELLICAT_AGENT_ASK_TIMEOUT_S` = 600 s fails the task); the journal (`agents/journal.jsonl`, trimmed at 256 KiB); totals of calls and spend (`totals.json`).
* Caroline's group `agents`: `agent_list`, `agent_start`, `agent_status`, `agent_answer`, `agent_cancel`. App pages may not command agents; Settings may look (read-only).
* **A2A inbound** (`a2a_bridge.py`): `"a2a": {"expose": true}` gives an Agent Card at `/a2a/<app>/<agent>/.well-known/agent-card.json` and JSON-RPC (A2A 1.0: `SendMessage`, `SendStreamingMessage`, `SubscribeToTask`, `GetTask`, `CancelTask`, `ListTasks`) on `127.0.0.1:48772`; callers send `Authorization: Bearer` with the device key from `agents/a2a.key` (mode 600). Skills marked `confirm` are not offered unless `a2a.confirm`. Store packages may not expose agents yet.
* **A2A outbound** (`a2a_client.py`): `host: {"a2a": "<card url>", "key": "<name>"}`; the key's value lives in `agents/a2a-keys.json`, never in the manifest. What Caroline gives such an agent leaves the device.

## Writing a new app

1. Create `apps/<id>/` with `manifest.json`, `index.html`, `icon.svg`; set `tile.order`; include `/platform.js`.
2. Save and restore state with `Platform.state`; use only the cursor, A and B.
3. Use `Platform.messageBox`/`notify`/`sound` instead of building your own; translate every phrase through `Platform.t` into all `web-shell/i18n/*.json`.
4. Declare only what you use: `permissions`, `capabilities` (mark `required` ones), `beroun` (minimum OS version).
5. Add a `caroline` section (at least a description) so she can drive it.
6. Run `tests/manifest_test.py` and the app's own test; for the store see [STORE_AND_RELEASES.md](STORE_AND_RELEASES.md).
