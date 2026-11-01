# The system API

Full reference: `docs/system-api.md` (code: `runtime/sysapi.py`, `runtime/extdisplay.py`, `web-shell/platform.js`). Summary.

**Who may call.** An app's page (known by its address) or a service of a manifest (header `X-IntelliCat-Service-Id`). Nobody else.
**Buttons in system dialogs.** Only the cursor, A and B have a job.

## MessageBox

A modal message drawn over the frontend in the shared window; one at a time, the next waits in a queue. A box with no page to show it waits for the next page that connects; an app closing does not lose it.

| Route | Body |
| --- | --- |
| `POST /api/msgbox` | `{title, text, kind \| buttons, icon, default, timeout, wait}` |
| `GET /api/msgbox/result?id=&wait=` | the answer, or `{id, pending: true}` |
| `POST /api/msgbox/cancel` | `{id}` |

`kind`: `ok`, `okcancel`, `yesno`, `yesnocancel`, or `buttons` (1–4 texts ≤ 24 characters). `icon`: `none|info|question|warning|error`. `timeout` seconds (0 = until answered, default 120, max 3600); title ≤ 60, text ≤ 400 characters.
Answer: `{id, index, button, reason}`, `reason` ∈ `button | cancel | timeout | cancelled`. JS: `await Platform.messageBox({...})`.

## System sounds

`notify`, `message`, `ok`, `warning`, `error`, `click`, `alarm`, `ring` — tunes generated once into `$STATE/sounds/<name>.wav`. Do-not-disturb silences them except the priority sounds `alarm` and `ring`; `force: true` overrides.
`GET /api/sound/list`, `POST /api/sound/play {name, force}`. JS: `Platform.sound.list()/.play(name)`.

## Vibration

`GET /api/vibra` (`enabled`, `patterns`, `max_ms`), `POST /api/vibra/pulse {pattern, strength, force}`, `POST /api/vibra/stop`. `pattern`: a name (`tap` 30 ms, `short` 80, `long` 400, `double`, `alarm`, `ring`) or a list of whole milliseconds on/off/on… (≤ 20 steps, 2 s each, 10 s total); `strength` 0–100. The system setting `vibration` turns the motor off for everyone; do-not-disturb silences it except `alarm`/`ring`.
JS: `Platform.vibra.pulse('double')`, `.pulse([100,50,100], 60)`, `.stop()`, `.status()`.

## The external display

An app with the manifest section `external` has a **big page** for a second screen, an extended desktop with one fullscreen window. Apps that opened a big page form a **stack** (top shown; empty stack shows the cat splash, `system/ext-cat`).
When the top closes, the one below returns at its saved place. SELECT switches input focus between the screens; the left stick is the mouse, the right the wheel, X/Y mouse buttons (focus on the external screen only); MODE closes the newest app with its big page.
A game (`external.input: "game"`) keeps all buttons as the gamepad's while on top.

* JS (both pages): `Platform.display.status()` → `{connected, width, height, focus, top, stack}`; `.on(fn)` events `connected | disconnected | resize | focus | stack`; `.open(arg)` (409 without a screen, 404 for an app without `external`); `.close()`; `.send(message)` / `.onMessage(fn)` (JSON ≤ 64 KB, delivered only while the big page is on the screen).
* Big page: `.launch()` once → `{arg, resume}`; `.size()`; `Platform.onSuspend(async reason => …)` called before unloading (`covered | closed | unplugged | reopen`), 2 s to save with `Platform.state.set` (the big page's state is separate).
* HTTP: `GET /api/display`, `POST /api/display/open|close|send`, `GET /api/display/launch`, `POST /api/display/focus {to}` (console window only). A big page is the app's page with `?ext=1`; its `/appws` channel is `?screen=big`.
* Plug/unplug and the monitor's mode come from the target (see [TARGETS.md](TARGETS.md)). Audio stays on the device's speaker.

## Agents and Camerlengo

`POST /api/agents/sw {command, params}`: an agent's call of the owner's Camerlengo service. The shell adds the session and the family's key (only family `ai` has one today), returns what the service answered and journals the call (agent, command, outcome, cost; never parameters or results). Only commands listed in the agent's `camerlengo` go through (403 otherwise). Error kinds: `network` 503, `auth` 401, `balance` 402, `config` 500, `service` 502. JS: `Platform.sw.call(command, params, agent?)`.

## The store (`/api/store/*`)

See [STORE_AND_RELEASES.md](STORE_AND_RELEASES.md). Only the Store app's page (`apps.manage`) and Caroline may call it.

## Caroline and the system API

She has a tool for every route (`beroun_*`, reached through `/api/caroline/sys/<area>/<op>` as the owner): files, settings, clock, network, power, sleep, services, notifications, MessageBox (`beroun_message_box`, waits for the button; `timeout` 5–600 s), sounds, vibration (`beroun_vibrate`, `beroun_stop_vibration`, `beroun_vibration_setting` — only when asked), her updates, the external display (`beroun_display_*`; window tools take `screen: "external"`) and the store. Test: `tests/caroline_sys_test.py`, `tests/caroline_audit_test.py`.

Not yet: a MessageBox over a native window (the browser, Caroline's chat) and over the launcher.
