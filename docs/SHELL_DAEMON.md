# The shell daemon (`runtime/shelld.py`)

`shelld` serves the launcher and the apps, spawns and tracks processes, and owns every API of the system. About 3250 lines of Python around one
`BaseHTTPRequestHandler` (`class H`), plus helper modules. Default port 8765 (`INTELLICAT_PORT`; `+100K` for the K-th simulator instance).

## Modules

| Module | Role |
| --- | --- |
| `manifest.py` | Reads and validates app manifests and system `view.json` files (format 1); capability and permission names; `BEROUN_VERSION` |
| `services.py` | Service manager: `host` = `module` / `process` / `child`; start rules, priority, memory budget, sleep behaviour, window take-over |
| `sysapi.py` | MessageBox, system sounds, vibration (no dependency on `shelld`) |
| `sysnotice.py` | Notices the system raises by itself: network up/down, low battery, storage full |
| `timectl.py`, `netctl.py` | Clock/NTP/time zone and network; each an interface with a fake and a system implementation |
| `extdisplay.py`, `extinput.py` | External-display rules (stack, focus, messages) and buttons→pointer logic; pure, testable alone |
| `agents.py`, `a2a_bridge.py`, `a2a_client.py` | Broker for apps' agents, and the A2A 1.0 protocol in/out |
| `appinstall.py` | Install, update, remove of store apps with signature verification |
| `caroline.py`, `caroline_ask.py`, `caroline_update.py` | Caroline as a service; apps asking her; installing her updates |
| `face.py` | Caroline's talking head through `ic-face-view` |
| `ratatosk*.py` | The Ratatosk messenger client: account, chats, end-to-end crypto, attachments, groups, LiveKit calls, audio mixing |
| `sw.py` | The shell's SquirrelWisdom (Camerlengo) client: login, session, speech recognition |
| `stt_checks.py` | Sanity pass over speech-recognition output (from Caroline's `stt_sanity.py`) |

## HTTP API (grouped, under `/api/`)

Pages are served from `web-shell/` (`/`, `/platform.js`), `/apps/<id>/…` and `/system/<id>/…`. The caller is recognised by address (an app's page), by header
`X-IntelliCat-Service-Id` (a service), `X-IntelliCat-Mcp` (an MCP server) or `X-IntelliCat-Agent: <app>/<id>` (an agent). Sensitive groups answer 403 to ordinary pages.

| Group | Routes |
| --- | --- |
| Basics | `health`, `about`, `home`, `log`, `exit`, `buttons` |
| Apps | `apps`, `launch`, `launch-arg`, `open`, `close`, `running`, `app/<id>/<path>` (passthrough to an app's service endpoint) |
| Services | `services`, `services/start`, `/stop`, `/limit`, `/register`, `window/takeover` |
| Input and power | `hw`, `hw/button`, `hw/stick`, `hw/touch`, `hw/battery`, `power`, `power/sleep`, `osk/typing`, `beep` |
| Files (storage card only) | `fs/list`, `read`, `write`, `mkdir`, `rename`, `copy`, `move`, `delete`, `space`, `fs/audio`, `fs/video` |
| System API | `msgbox`, `msgbox/result`, `msgbox/cancel`, `sound/list`, `sound/play`, `vibra`, `vibra/pulse`, `vibra/stop` ([SYSTEM_API.md](SYSTEM_API.md)) |
| Notifications | `notify`, `notifications/list`, `/dismiss`, `/clear`, `/read`, `/dnd` |
| Settings, time, network | `settings`, `settings/set`, `time/status`, `time/set`, `time/zone`, `time/zones`, `time/ntp`, `net/status`, `net/scan`, `net/connect` |
| Account | `auth/status`, `auth/login`, `auth/signup`, `auth/key` (technical login for tests), `account/session`, `wizard/…` |
| Speech | `stt/start`, `stt/stop` |
| Per-app storage | `db/get`, `db/set` (the launcher database behind `Platform.state` and app settings) |
| External display | `display`, `display/open`, `/close`, `/send`, `/launch`, `/focus`, plus `hdmi`, `hdmi/input` (target side) |
| Store | `store/catalog`, `installed`, `updates`, `install`, `remove` |
| Agents | `agents`, `agents/…`, `agents/sw` |
| Messaging | `msg/…` (Caroline's `/api/ic/messages/*` re-rooted), `rt/…` (Ratatosk), `rt/attachment`, `rt/call-preview`, `rt/call-video` |
| Caroline | `caroline/start`, `/stop`, `/status`, `/ask`, `/voice`, `/settings`, `/apps`, `/app/prepare`, `/app/tool`, `/notifications…`, `/sys/<area>/<op>` |
| Face | `face/job`, `face/play`, `face/status`, `face/stop` |
| Audio | `audio` (reports whether sound is remote) |

Other listeners: the **A2A** listener (`127.0.0.1:48772`, only when a manifest exposes an agent), the WebSocket `/ws` (input and `typing` events from the console page),
and `/appws` (per-page channel for events and display messages; `?screen=big` for an external-screen page).
Routes used only by a simulator console (`svc/*`, `hdmi*`, `/stream.mjpg`, `/monitor`) are target-side and and are not described here.

## Launching

Apps come from manifests. `config/apps.json` lists extra launchables: `native` entries run with `WAYLAND_DISPLAY=intellicat` (and the XWayland `DISPLAY` for X11 clients);
`html` entries open in their own `cog`; `"shell": true` marks a program that gives a shell (removed in the online profile). Store-installed apps appear and disappear without a restart.

## The shared window

An app with `frontend.window = "shared"` has no process of its own: its page is shown in the launcher's `cog`, **one frontend at a time**. Starting another app asks the open one to close
(it saves); **MODE** closes it the same way and the launcher returns. Services and the system may take over the window (`takeover`, e.g. the Clock ringing an alarm).

## Services

A service is a backend that outlives an app's window: `"services"` in a manifest, or the system's `system/services.json`.

| Field | Meaning |
| --- | --- |
| `host` | `module` (an object of `shelld`'s code), `process` (`cmd`, started and watched by the manager), `child` (started by another service, found among its descendants by `match`: not stoppable by the manager) |
| `start` | `system` (at boot), `open` with `required_by` (with the app), `manual` |
| `priority` | 0–100; over the memory limit the lowest goes first; 90+ never |
| `memory_mb` | declared budget; `GET /api/services` shows state, PSS and the total against `INTELLICAT_MEM_LIMIT_MB` (1900) |
| `sleep` | keeps running while the device sleeps |
| `takeover` | may take the shared window |
| `privileged` | system services only |
| `port`, `cmd`, `endpoints` | for process services: `{port}`/`{app}` placeholders, `INTELLICAT_SERVICE_PORT`; endpoints expose paths through `/api/app/<id>/<path>` to the app's page (`page`), its MCP servers (`mcp`) and its agents (`agent`) |

Store-installed services use ports 49000–49999 and get `INTELLICAT_DATA`/`INTELLICAT_APP_DATA` pointing at the app's own folder. Example services: the Clock's `alarmd` (rings in sleep, takes over the window), the Calendar's `calendard`, the Communicator's WhatsApp/Signal sidecars (children), Ratatosk, `minecraftd`, Caroline.

## Notifications

The status bar shows the newest notification; a tap or SELECT opens the panel: **Do not disturb**, **Clear all**, notifications newest first with app, time and unread marks (the last 100, in memory). A / Enter opens the originating app and removes the notification, X removes it only, B closes the panel. `POST /api/notify` and `Platform.notify` raise one. Sources: `sysnotice` (network, battery at 20 %/10 %, storage 95 % full), Clock alarms/timer, Calendar (10 minutes before and at the time; undated events at 08:00; tasks due today at 09:00), messengers, Notepad save failures, `shelld`. Do-not-disturb drops sound and vibration except the priority sounds `alarm` and `ring`. An app sees the text of its own notifications only; other apps get `{event:"changed", unread, dnd}`.

## Time and network

Time is the OS's: Settings' Date & time page (`/api/time/*`; only Settings and the launcher may change it) offers automatic time (on by default), the time zone and manual time (refused while automatic is on). Network time is a one-shot `ntpd -q` against `INTELLICAT_NTP_SERVERS`, run when the setting is turned on, when a network appears, then every `INTELLICAT_NTP_INTERVAL` (3600 s), retrying every 15 s on failure. Caroline has the same through Settings' tools. How a target implements the clock and network is in [TARGETS.md](TARGETS.md).

## Accounts

The shell logs in to SquirrelWisdom (Camerlengo) itself (`sw.py`, the same credentials file as Caroline). First start shows a login/sign-up screen and a start wizard; Caroline's wizard page is served by her backend. `POST /api/auth/key` signs in a test account by a key (only its SHA-256 is stored); five wrong keys in a minute lock it for a minute. Agents' calls of Camerlengo go through `POST /api/agents/sw`: the shell adds the session and the family key and journals the call (agent, command, outcome, cost; never parameters or results).

## Logs and debugging

Logs: `shell.log`, `app-<id>.log` and per-daemon logs in the state directory. WPE's remote inspector listens on port 9222; the shell page is also served at `http://localhost:8765/` for any browser.
