# The system API of OS Beroun

What every app and every service may ask of the system itself: a **MessageBox**, the **system sounds**, the **vibration motor**. The code is `runtime/sysapi.py` (the logic, tested alone
by `tests/sysapi_test.py`); `shelld` routes the requests and `web-shell/platform.js` gives apps the JavaScript side.

**Who may call.** An app's page (the shell knows it by the page's address) or a service of a manifest (header `X-IntelliCat-Service-Id: <service id>`). Nobody else.

**Buttons.** Only the cursor, A (Enter) and B (Escape) have a job, in the MessageBox too: left/right choose a button, A confirms it, B cancels.

## MessageBox

A modal message drawn over the frontend that is in the shared window; while it is up the page hears no keys. One box at a time: the next waits in a queue. A box with no page to show it
(the launcher is in the window) waits for the next page that connects. An app closing or another app taking the window does not lose the box: the new page shows it.

| | |
| --- | --- |
| `POST /api/msgbox` | `{title, text, kind \| buttons, icon, default, timeout, wait}` |
| `GET /api/msgbox/result?id=&wait=<seconds>` | the answer, or `{"id", "pending": true}` |
| `POST /api/msgbox/cancel` | `{id}`: the box goes away, its answer is `cancelled` |

* `kind`: `ok` (the default), `okcancel`, `yesno`, `yesnocancel`; or `buttons`: 1 to 4 texts (up to 24 characters each, translated by the page when the interface has them).
* `icon`: `none` (the default), `info`, `question`, `warning`, `error`. `default`: the index of the button the cursor starts on.
* `timeout`: seconds, `0` = until answered, default 120, at most 3600. `title` up to 60 characters, `text` up to 400.
* `wait: true` holds the request until the box is answered; otherwise the answer is `{id}` and the result is asked for later.
* The answer: `{"id", "index", "button", "reason"}`. `reason`: `button` (A on a button), `cancel` (B; `index` is -1), `timeout`, `cancelled` (by the owner).

JavaScript: `const r = await Platform.messageBox({title: 'Delete?', text: 'The note will be gone.', kind: 'yesno', icon: 'warning'}); if (r.index === 0) ...`

## System sounds

`notify` (a notification), `message`, `ok`, `warning`, `error`, `click`, `alarm`, `ring`: little tunes made by the system (written once to `$STATE/sounds/<name>.wav`) and played through the sound card.
Do-not-disturb silences them, except the **priority** sounds `alarm` and `ring`; `force: true` plays through do-not-disturb.

| | |
| --- | --- |
| `GET /api/sound/list` | `{sounds: [{name, priority, ms}]}` |
| `POST /api/sound/play` | `{name, force}` -> `{played: true}` or `{played: false, reason: "do-not-disturb"}`; an unknown name is 400 |

JavaScript: `Platform.sound.list()`, `Platform.sound.play('ok')`. The platform's own notifications use them: `notify` plays `notify`, `warning` or `error` by its level, a ringing call plays `ring`.

## Vibration

| | |
| --- | --- |
| `GET /api/vibra` | `{enabled, patterns, max_ms}`: `enabled` is the system setting `vibration` |
| `POST /api/vibra/pulse` | `{pattern, strength, force}` -> `{vibrated: true, ms}` or `{vibrated: false, reason: "off" \| "do-not-disturb"}` |
| `POST /api/vibra/stop` | ends a running pattern |

* `pattern`: a name (`tap` 30 ms, `short` 80, `long` 400, `double`, `alarm`, `ring`) or a list of whole milliseconds, on, off, on, ... (at most 20 steps, 2 s each, 10 s in all). `strength`: 0 to 100 (100).
* The setting `vibration` (`GET /api/settings`, `POST /api/settings/set` by Settings; on until switched off) turns the motor off for everyone; do-not-disturb silences it, except the priority patterns
  `alarm` and `ring`; `force: true` goes through both.
* The simulator has no motor: the pattern is logged and the edge of the page lights up while the motor would run. On the device `INTELLICAT_VIBRA_DEV` names the file whose content is the motor's strength
  (0 = off), written step by step. Not exercised on hardware yet.

JavaScript: `Platform.vibra.pulse('double')`, `Platform.vibra.pulse([100, 50, 100], 60)`, `Platform.vibra.stop()`, `Platform.vibra.status()`.

## Caroline

**She has a tool for every API of the system** (the owner's rule), checked by `tests/caroline_audit_test.py`: every route of `shelld.py` under `/api/` is covered by a tool of hers or is on an explicit list with the reason. The files of the
memory card (`beroun_files_list`, `_read`, `_write`, `_mkdir`, `_rename`, `_copy`, `_move`, `_delete`, `_space`), `beroun_settings` / `beroun_set_setting`, the clock (`beroun_time`, `_time_zones`, `_set_time`, `_set_time_zone`, `_set_ntp`),
the network (`beroun_net_status`, `_net_scan`, `_net_connect`), `beroun_power`, `beroun_sleep`, `beroun_services`, `beroun_notify`, her own updates (`beroun_update_status`, `_update_check`, `_update_now`: the owner's yes, see `caroline-updates.md`) and the external screen (`beroun_display_status`, `_open`, `_close`, `_focus`; the window tools
`beroun_screen_text`, `_ui_tree`, `_click`, `_type`, `_key` take `screen: "external"` to read and press an app's big page) reach the system by `/api/caroline/sys/<area>/<op>` with her as the owner; test: `tests/caroline_sys_test.py`.

Her group `beroun` has a tool for each of the three above: `beroun_message_box` (asks and waits for the button; `timeout` 5..600 s, 60 by default), `beroun_play_sound`, `beroun_vibrate` (a `pattern` name or `milliseconds`, `strength`),
`beroun_stop_vibration` and `beroun_vibration_setting` (the owner's switch: she changes it only when asked). They use the same calls as the apps, with her as the owner, through
`/api/caroline/sys/<what follows /api/>` (`msgbox`, `sound/play`, `vibra/pulse`, `vibra/stop`) and `/api/caroline/sys/vibra/enable` `{on}` for the setting. Code: `caroline-overlay/ic_apps/plugins.py`;
test: `tests/caroline_apps_test.py`.

## The clipboard
One clipboard (plain text, up to 1 MB) for the whole device: every app, system view and assistant view, and the device browser. The text lives in shelld (`sysapi.Clipboard`), not in a page.

| Call | |
|---|---|
| `GET /api/clipboard` | `{text, length, at, source}` (`source`: who set it: an app id, `assistant`, `osk`, `host`) |
| `POST /api/clipboard` `{text}` | sets it; the simulator's consoles write it to the host's clipboard |
| `POST /api/clipboard/clear` | empties it |
| `POST /api/clipboard/pull` | on a simulator asks the consoles for the host's clipboard first (one second at most), then the same answer as `GET`; the keyboard's Paste and the tool `beroun_clipboard_read` use it |

JavaScript: `Platform.clipboard.read({host: true})`, `.write(text)`, `.clear()`. A page of an app or a system view only (the same rule as the rest of this API).

On the device the user works with it in a text field: SELECT turns the selection mode on and off, the arrows move the end of the selection, A opens the menu Copy / Cut / Paste / Select All, B leaves it (`web-shell/osk.js`).
The assistant has `beroun_clipboard_read` and `beroun_clipboard_write`. On a simulator the console page (an extension of the simulator target, `BEROUN_EXTENSIONS`) keeps the device and host clipboards equal: over `/ws` shelld sends `{"t":"clip","text"}` (device -> host) and
`{"t":"clip?"}` (asks for the host's text), the console answers `{"t":"clip","text"}` (`null` when the browser refused). Test: `tests/clipboard_test.py`.

## The external screen (HDMI)

An app with the manifest section `external` ([app-format.md](app-format.md)) has a **big page** for the external screen, an extended desktop with a fullscreen window in which one app's page is shown at a time. The apps that opened
it form a **stack**: the last is on top; when it closes (or takes its page off) the one under it comes back at the place it was saved in; an empty stack shows the splash with the cat. Which screen has the focus (the cursor, A and
B go there) is switched by SELECT; the mouse is the left stick (the right stick scrolls, X and Y are its buttons) and works only with the focus on the external screen. MODE on the console closes the newest app, with its big page.
A game whose big window is a native program (manifest `external` with `cmd` and `"input": "game"`, the cloud Minecraft's player) reads the gamepad itself: while it is on top every button
stays the gamepad's and the sticks are no mouse (SELECT still moves the focus).
Plug in, pull out and the mode of the monitor come from the connector (on the device) or from the SERVICE menu and the Monitor window (the simulator, `POST /api/hdmi`).

JavaScript, on both pages of the app (`status` and `on` for every page):

* `Platform.display.status()` `{connected, width, height, focus: "console" | "external", top, stack}`; `.on(fn)` `fn({event: connected | disconnected | resize | focus | stack, ...status})`, returns the function that stops listening.
* `.open(arg)` puts the app's big page on top (rejects with status 409 when there is no screen, 404 for an app without `external`); `.close()` takes it off.
* `.send(message)` / `.onMessage(fn)`: any JSON (up to 64 KB) between the app's two pages; delivered to the big page only while it is the one on the screen (`{delivered: false}` otherwise).
* On the big page: `.launch()` once, `{arg, resume}` (what `open` gave; `resume` true when the page was under another and comes back: restore `Platform.state`); `.size()` the window's size (it follows the screen's mode, with a `resize` event);
  `Platform.onSuspend(async reason => ...)` is called before the page is unloaded (`covered`, `closed`, `unplugged`, `reopen`): save with `Platform.state.set` (the big page's state is apart from the console page's). The page has 2 s.

HTTP: `GET /api/display`; `POST /api/display/open` `{arg}`, `close`, `send` `{message}` (from the app's own pages, the shell knows which by the address: `?ext=1` is the big page); `GET /api/display/launch`; `POST /api/display/focus` `{to}`
(the console window only). Events and messages go over the `/appws` channel (`{"t": "display"}`, `{"t": "display_msg"}`); a big page's channel is `?screen=big`. Code: `runtime/extdisplay.py` (the rules), `runtime/extscreen.py` (the simulator's screen),
`runtime/extinput.py` (the buttons and sticks). Tests: `tests/extdisplay_test.py`, `tests/extapi_test.py` (two scripted apps, `apps/ext-a` and `apps/ext-b`, hidden from the launcher), `tests/extinput_test.py`, `tests/extscreen_test.py`.

## Agents and Camerlengo

For the agents an app publishes ([architecture.md](architecture.md), the manifest's `agents`): **`POST /api/agents/sw`** `{command, params}` is an agent's call of the owner's Camerlengo service; the shell adds the session and
the family's key (only the family `ai` has one today), answers what the service answered (no key, no session in it) and journals the call with the agent, the command, the outcome and the cost, never the parameters or the result.
Only the commands the agent's manifest lists in `camerlengo` go through (403 otherwise); a service names its agent in `X-IntelliCat-Agent: <app>/<id>`, a page uses its own app's agent. Errors keep the kinds `network` (503),
`auth` (401), `balance` (402), `config` (500), `service` (502). JavaScript: `Platform.sw.call(command, params, agent?)`. The other calls of `/api/agents/*` (the tasks, the summary, the journal) are for Caroline and, read only, for
Settings. Tests: `tests/agents_shell_test.py`.

## The store (`/api/store/*`)

The apps of the store; `runtime/appinstall.py` is the device's side, `store_*` in `runtime/shelld.py` the glue. Only the **Store app's page** (its manifest declares
`apps.manage`; no package may) and **Caroline** (`/api/caroline/sys/store/*`, her tools `beroun_store_catalog`, `_installed`, `_updates`, `_install`, `_remove`) may call it; any other page gets 403.

| Call | Does |
| --- | --- |
| `GET /api/store/catalog?q=` | `{apps, offline}`: **every app the owner can do something about**, one entry per id: what the arbiter lists for `os=beroun` (`INTELLICAT_STORE_URL`, `https://navlink.net`) and what is on the device (the firmware's apps, the store's); offline, the device's own. `kind`: `system` (in the firmware, `"system": true`), `preinstalled` (in the firmware), `store`. `state`: `system` (up to date), `installed`, `update` (a newer version is in the store), `available` (not on the device: in the store, or a preinstalled app that was removed), `unsupported` (needs what the device lacks, `lacking`); `summary` is the rights the store's package asks for |
| (no call: the shell) | **automatic updates**: with the setting `auto_update` on (Settings, "Update apps automatically"; on until switched off) the shell installs every waiting update by itself a minute after the start and every 6 hours, except one that asks for more rights than the installed version or whose app is open; a notification names each one |
| `GET /api/store/installed`, `GET /api/store/updates` | what the store's folder holds (id, name, version, kind: the apps from the store and the newer versions of firmware apps); the apps with a newer version in the store |
| `POST /api/store/install` `{app}` | installs, or updates to the newest version: downloads from `INTELLICAT_STORE_ASSETS` only, checks the store's `sha256`, then the **signature against the store key built into OS Beroun** (`appinstall.STORE_PUBLIC_KEY`, or `INTELLICAT_BEROUN_PUBKEY`), the hash of every file, the package rules and the manifest; the app is on the launcher at once. A failure puts the previous state back. A **system** app is only updated; a **preinstalled** app that was removed comes back from the firmware without a download (`action: restored`), and a newer version is then an update |
| `POST /api/store/remove` `{app}` | an app **from the store**: closes it, stops its services, removes everything it registered and **deletes its folder, its own files (`<data>/apps/<id>/`) and its saved place**. A **preinstalled** app is hidden (the firmware is read-only) and **keeps its data**, so that installing it again brings it back as it was. A **system** app is refused (`system`). The files an app wrote to the memory card always stay |

Errors carry `code`: `offline` 502, `not-found` / `not-installed` 404, `system` 403 (a system app cannot be removed or installed; a package whose `system` does not match the device), `conflict` (a service id or port another app has) / `not-higher` (never a downgrade) / `missing-capability` 409, `bad-signature` 422,
`no-key` 503 (the device has no store key yet: nothing can be installed). Installed apps live in `~/.intellicat/apps/<id>/` (`INTELLICAT_APPS`); their services get `INTELLICAT_DATA` and
`INTELLICAT_APP_DATA` pointing at the app's own folder, and use the ports 49000-49999.

## 3D (Babylon.js)

The system serves one pinned Babylon.js (`BABYLON_VERSION` in `config/versions.env`) at `/lib/babylon/`: `babylon.js` (core), `babylonjs.loaders.min.js` (glTF/GLB, OBJ, STL), `babylonjs.materials.min.js`
and `babylon.gui.min.js`; it is the same file on every target (Linux, the hosted simulator, Android's WebView), works offline, and a package does not carry its own copy. Plan, measurements and the
A page includes what it needs and the helper, which has to come after the core:

```html
<script src="/platform.js"></script>
<script src="/lib/babylon/babylon.js"></script>
<script src="/lib/babylon/babylonjs.loaders.min.js"></script>   <!-- only when it loads models -->
<script src="/lib/beroun-3d.js"></script>
```

`/lib/beroun-3d.js` is for a console that has the cursor, A and B and no mouse (`Beroun3D`):

| | |
| --- | --- |
| `Beroun3D.engine(canvas, opts)` | a `BABYLON.Engine` for the 640x480 screen: `devicePixelRatio` ignored, no antialiasing, no stencil, low-power GPU; follows the window's size. `opts` (`antialias`, `adaptToDeviceRatio`, `stencil`, `preserveDrawingBuffer`) are passed to Babylon |
| `Beroun3D.camera(scene, "orbit", {target, radius, beta, turn})` | turns around `target`: Left/Right turn, Up/Down tilt (a held key moves by time, so the speed does not depend on the frame rate) |
| `Beroun3D.camera(scene, "walk", {position, speed, turn})` | Up/Down walk forward and back along the ground, Left/Right turn |
| `Beroun3D.onAction(fn)` | A (Enter) was pressed; returns a function that removes `fn`. B (Escape) is the app's own: a menu, `Platform.exit()` |
| `Beroun3D.keys` | `{up, down, left, right}`, true while held, for an app that moves things itself |

The camera has none of Babylon's own inputs (mouse, touch, keyboard, gamepad), and while one exists the D-pad does not scroll the page. Pointer Lock does not exist in WPE, so Babylon's stock cameras are not used.
Sticks and the other buttons are read with the Gamepad API by the app (the owner decides per game which buttons it may use). Models, textures and `.wasm` files of an app are served with the right types
(`.glb`, `.gltf`, `.ktx2`, `.wasm` is exactly `application/wasm`) from `/apps/<id>/`. The template is `apps/babylon-hello` (a `.glb` of its own folder, the cursor, A, B, the state), the check
`apps/gl-probe`; tests `tests/gl_probe_test.py`, `tests/babylon_hello_test.py`, `tests/static_serving_test.py`.

An app that draws 3D declares the capability `{"name": "WebGL", "scope": {"required": true}}` in its manifest: the store does not offer it to a device without WebGL, and `Platform.capabilities()` tells the page
whether it is there (every target has it today).

The simulators draw on the CPU (Mesa llvmpipe; the hosted one on two vCPUs: about 20 FPS on the probe's scene), so they show that a 3D app works, not how fast the device's GPU runs it.

## Not yet

* A MessageBox over a native window (the browser, Caroline's chat) and over the launcher; the Settings switch for `vibration`.

## Capabilities and the device

* `Platform.capabilities()` resolves to the names the app declared in its manifest (`capabilities`) **and** this device has (`GET /api/capabilities`, the caller is known by its address; Caroline, with no app page, gets all the device has: `beroun_capabilities`, `beroun_device`). An app checks a capability, never the kind of device.
* `Platform.device()` resolves to `{kind: "intellicat" | "simulator" | "hosted" | "android", touch, width, height}` (`GET /api/device`; the size is the page's): for the layout and hints to the owner, not for the logic of capabilities.
  `INTELLICAT_KIND` sets the kind (the Android app does); otherwise the hosted simulator (`INTELLICAT_ONLINE`) is `hosted`, a console with `INTELLICAT_HARDWARE` is `intellicat`, the rest is `simulator`. Tests: `tests/device_api_test.py`.
