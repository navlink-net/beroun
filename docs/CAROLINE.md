# Caroline inside OS Beroun

Caroline (the owner's assistant; her own repository has its own documentation) is **part of the system, not an app**. She is pinned as the submodule `caroline` and treated as upstream: nothing is
patched in her checkout from here.

## Two ways in

| Way | What | Closed by |
| --- | --- | --- |
| First tile of the launcher | Her **text chat** (`system/caroline`): tabs, typing, voice input/playback one message at a time | MODE |
| **R2** | Her **continuous voice dialogue over the current screen**: her talking head (`ic-face-view`, model `CarolineA`) on a transparent background; the invisible page `caroline-voice` runs her chat meanwhile. The continuous-dialogue setting is on while it runs and restored after | R2 again |

Both show the same Caroline (conversations live in her backend). Settings has a Caroline row opening her panel; her start wizard page is served by her backend.

## How she runs

`runtime/caroline.py` runs her supervisor (`backend-py/supervisor.py`, which owns `run_server.py`) as the privileged service `caroline-backend`
(`system/services.json`: priority 100, `memory_mb` 350, `sleep: true`, `takeover: true`), once there is an account and a network, and watches it.
Environment it sets:

| Variable | Why |
| --- | --- |
| `CAROLINE_WORKSPACE_DIR`, `XDG_DATA_HOME` | Workspace and data on the device |
| `CAROLINE_PORT`, `CAROLINE_SUPERVISOR_PORT` | Ports (+100K per instance) |
| `CAROLINE_APP_ROOT=~/.intellicat/caroline-runtime` | Her runtime outside the repository (`scripts/caroline-provision.sh`: CPython 3.12.15 from python-build-standalone with her pinned requirements; no Codex, Node, signal-cli or Playwright) |
| `PYTHONPYCACHEPREFIX` | No bytecode written into her checkout |
| `CAROLINE_CAMERLENGO_PATH` | Her `backend-py/camerlengo-vendor-stubs` |
| `CAROLINE_REMOTE_ACCESS` unset | No remote listener |
| `CAROLINE_UPDATE_PLATFORM=beroun`, `CAROLINE_RUNTIME`, `CAROLINE_UPDATE_DIR` | Her update client |

When the shell dies her processes die with it (kernel parent-death signal).

## The SquirrelWisdom-only edition

OS Beroun answers through SquirrelWisdom only. Her repository builds an edition for it (named *IntelliCat edition* there; `make dist-intellicat`, run by `scripts/caroline-build.sh`; output
`caroline/dist-intellicat`): the Claude and OpenAI sources, their sign-in, model choice and wizard step are cut out. Her runtime has the 231 MB bundled Claude binary deleted, so no Claude or Codex process exists.
The pinned commit must contain the edition and the update client (Caroline `34ff9da` or later; `CodeLayer.has_client()` checks).

## The overlay (`caroline-overlay/`)

Applied from outside at her process start (`sitecustomize.py`), for code that has to run in her process:

| Part | What |
| --- | --- |
| SW service keys | Loaded from `~/.intellicat/caroline-keys.py` into her gitignored `sw_service_keys_local` (never in this repository); `INTELLICAT_SW_URL` redirects the SW address in tests |
| `ic_messaging/` | The Communicator's backend, mounted on her FastAPI app at `/api/ic/messages/*` (re-rooted by the shell as `/api/msg/*`): store, accounts, adapters for mail, WhatsApp, Telegram, Signal, Slack; scripted `fake.py` (`IC_MESSAGING_FAKE=1` or `unlinked`); `channels.py` for extra accounts per service (`owner2`, `self2`…); `devserver.py` to design the app without her backend. Her send tools are wrapped to announce who sends |
| `ic_plugins/ic_messages_plugin.py` | Tools `messages_history`, `messages_search`, `messages_unread`, `messages_mark_read` (her plugin loader is extended to list this folder) |
| `ic_apps/` | Tool groups for apps (`app-<id>`), the `beroun_*` system group, `agents`, hooks and a pool for page tools |
| `ic_browser_driver.py` | Her browser tool driving the WPE Browser instead of Chromium |

`PATCHES.md` records what is done by configuration, by `sitecustomize`, in her repository (the edition), and what is open (the `claude_agent_sdk` package stays for its message types; storage names such as the session pointer's `"claude"` key).

## Her tools for the system

Rule (owner): **a tool for every API of the system**. `beroun_*` covers files, settings, the clock, network, power, sleep, services, notifications, MessageBox/sound/vibration, her updates, the external display and the store,
through `/api/caroline/sys/<area>/<op>` as the owner. `tests/caroline_audit_test.py` fails if a `shelld` route under `/api/` is neither covered by a tool nor on an explicit exemption list. Apps ask her through
`POST /api/caroline/ask` (permission `caroline.ask`; the answer goes back to that app only). Settings gives her `settings_get_accounts`, `settings_remove_account`, `settings_open_accounts` (adding or editing an account needs the owner's passwords, so she opens the page).

## Updates

The rule is hers: **inactive → she updates herself; in use → the owner is asked quietly; a ready update installs at the first inactive moment.** Her backend (`update_service.py`, platform `beroun`) checks the signed `release.json`
every 30 minutes (and 10 s after start), downloads the `beroun` artifact (an overlay zip of her portable code), verifies signature, sha256 and size, and reports `ready` and an `action` (`install` | `ask` | `none`). The shell's `Updater`
(`runtime/caroline_update.py`, every 30 s while she runs) stops her, applies the overlay (her own `overlay_install.py`: owned paths only, directory swaps with rollback), restarts her. For `ask` it posts a notification and a MessageBox
("Install now" / "Later"; "Later" returns after 4 hours).

* The firmware's copy is read-only and never touched. Updates go to a writable copy `<data>/caroline-code/current` (hard links where possible); the verified download is in `<data>/caroline-update/`. The copy runs only when its `backend-py/VERSION` is newer than the firmware's; a newer firmware deletes it.
* **The way back:** an updated tree is on probation — it must answer on her port within 150 s and her supervisor must not die twice in a minute; otherwise the previous copy (or the firmware's) is put back, and that version is marked failed in `caroline-code/state.json` and not tried again.
* A release needing a higher runtime number (`CAROLINE_RUNTIME`, from `caroline-runtime/RUNTIME`, 1 if absent) waits as `needs_runtime` until the target itself is updated.
* Tools: `beroun_update_status`, `beroun_update_check`, `beroun_update_now` (only on the owner's yes). Routes `/api/caroline/sys/update/status|check|now`.

Tested: `tests/caroline_update_test.py` (22 checks) and live on a private simulator (1.1.0 installed by itself, a broken 1.2.0 rolled back, 1.3.0 installed). Not tried live: the "in use" question.

## Resource figures (measured on the simulator, x86-64; ARM64 will differ)

| Item | Figure |
| --- | --- |
| Runtime on disk | ~470 MB for the SW-only edition (~700 MB with the Claude binary) |
| Backend RSS idle / in a turn | 293–314 MB (supervisor ~56 MB; "Caroline total" ~350 MB) |
| Chat layer in WPE | ~530 MB with one tab (+~5 MB per extra tab) |
| Talking head | 42–68 MB while on screen |

## Tests

`caroline_start_test.sh`, `caroline_voice_test.sh`, `caroline_wizard_test.sh`, `caroline_settings_test.sh`, `caroline_probe.sh`, `caroline_tabs_memory.sh`, `caroline_apps_test.py`, `caroline_audit_test.py`, `caroline_sys_test.py`,
`caroline_messages_test.py`, `caroline_ask_test.py`, `caroline_update_test.py`, `caroline_browser_test.*`, `messaging_test.py`. Group run: `tests/run.sh --console --caroline` (needs her runtime, an account and a network).
