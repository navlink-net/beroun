# What the overlay does to Caroline's process

Caroline (`caroline`, a pinned submodule) is upstream. Nothing is patched in the checkout from here: what is
below is done from outside, at the start of her process, except the one exception that the owner decided (an edition of
her build, made as a commit in her own repository, see "Done in Caroline itself"). A change that needs her code to
change is listed under "Open".

## Done by configuration and environment (`runtime/caroline.py`)

| What | How |
| --- | --- |
| Workspace and data on the device card | `CAROLINE_WORKSPACE_DIR`, `XDG_DATA_HOME` |
| Backend port | `CAROLINE_PORT`, `CAROLINE_SUPERVISOR_PORT` |
| Her runtime outside the repository | `CAROLINE_APP_ROOT=~/.intellicat/caroline-runtime` (`scripts/caroline-provision.sh`) |
| No bytecode written into the checkout | `PYTHONPYCACHEPREFIX` |
| The SW engine's client files (`AI.py` ...) | `CAROLINE_CAMERLENGO_PATH` = her `backend-py/camerlengo-vendor-stubs` (what her Makefile copies into a build) |
| No remote listener | `CAROLINE_REMOTE_ACCESS` unset (her default) |
| Her updates | `CAROLINE_UPDATE_PLATFORM=beroun`, `CAROLINE_RUNTIME` (the device's runtime number), `CAROLINE_UPDATE_DIR`; the shell installs what her client downloaded (`docs/caroline-updates.md`) |

## Done by `sitecustomize.py` at her process start

| What | Why it cannot be configuration |
| --- | --- |
| SW service keys from `~/.intellicat/caroline-keys.py` | the keys are in a gitignored module of hers (`app/sw_service_keys_local.py`) that must not be in this repository |
| `INTELLICAT_SW_URL` redirect (tests only) | the SW address is a constant in her code |
| The origin of her chat page on a simulator with an instance name: `CAROLINE_PAGE_ORIGIN` (from `runtime/caroline.py`) replaces `app.origin_guard.LINUX_SHELL_FRONTEND_ORIGIN` right after her module is executed | the page origin is a constant in her code (`http://127.0.0.1:48767`); an instance serves her page from a shifted port |
| Communicator: `ic_messaging` (hooks.py): her send tools are wrapped to announce who sends (actors.py), `slack_channel._account_for_event` is wrapped to read her Socket Mode events; her FastAPI app gets the router `/api/ic/messages/*` and the registry start/stop right after `app.main` is imported; her plugin loader (`discover_plugins` lists only its own folder) lists `caroline-overlay/ic_plugins/` too, which holds the tools `messages_*` | there is no store and no API for a UI in her code, and the live connections of the accounts exist only inside her process (docs/usage.md) |

## Done in Caroline itself (an upstream commit, by the owner's decision of 2026-10-27)

The Claude and OpenAI answer sources are cut out of her **IntelliCat edition** (`make dist-intellicat`; see
`caroline/docs/INTELLICAT_EDITION.md`): engines, sign-in, model choice, their pages, the start wizard's
subscriptions step. This replaces the earlier open question about the default answer engine of a new tab. The overlay
runs that build (`scripts/caroline-build.sh`, then `runtime/caroline.py` starts `dist-intellicat/`), in a runtime from
which the bundled Claude binary is deleted (`scripts/caroline-provision.sh`).

## Open (needs the owner's decision, nothing is applied)

1. **The `claude_agent_sdk` Python package stays** (2 MB): the backend's message types and the plugins' tool decorator.
   Replacing them with its own types is a refactor of its own.
2. **Names of storage**: Claude Code's session-file layout and the `"claude"` key of a tab's session pointer are how SW
   turns are stored; renaming them is a data migration.

## The apps as her tools (`ic_apps`, 2026-10-28)

Done from outside, as the messaging hooks are (`ic_messaging/hooks.py` calls `ic_apps.hooks.loader_loaded` when her plugin loader is imported): `discover_plugins()` of her loader
returns, besides her plugins, one tool group per app with a `caroline` section in its manifest (`app-<id>`) and OS Beroun's group `beroun`; the apps' skill folders are a source of her
skill catalog (`app/skills.py`, `register_source`, which is her own upstream code: `2b0d324`). Nothing of hers is edited. Plan and state: docs/app-format.md.

## Her browser tool on the device (`ic_browser_driver.py`, phase 5)

Her plugin (`app_browser_plugin.py`) uses two modules on Linux, both Playwright + Chromium: `_linux_app_browser` (windows) and
`app_browser_cdp` (the page). The device has neither, and WPE WebKit has no CDP. `sitecustomize.py` answers both module names
with `ic_browser_driver.py`, which drives the device browser (`ic-browser`) over its control channel (a Unix socket,
`IC_BROWSER_CONTROL`, see the head of `native/browser/ic-browser.c`). Her plugin file is unchanged; her element tagging script
is read from her `app_browser_cdp.py` at run time. Not supported on the device yet: file dialogs, downloads, HttpOnly cookies.
Decision note (spike, 2026-10-27): WPE WebDriver was not chosen, because a thin command channel in the browser host that is
already ours needs no extra process and no extra memory, and every operation of her tool surface is a page script plus (for a
trusted click and for the screenshot) the host's own pointer input and frame buffer.

## Her tools for another assistant of the slot (`ic_bridge`, 2026-10-31)

In a build whose assistant is Claude or Codex her backend still runs (Communicator's host). `ic_messaging/hooks.py`, right after `app.main` is imported, mounts the
router of `ic_bridge/api.py` (`/api/ic/bridge/*`: her plugins of the groups it names, called through her own `dispatch()`) and replaces `app.main._on_reminder_due`,
which her startup reads afterwards, with `ic_bridge/reminders.wrap()` of it: a reminder of the tab `assistant:<tab>` goes to the shell, every other one to her code as
before. Nothing of hers is edited.
