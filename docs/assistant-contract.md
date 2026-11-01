# The assistant contract of OS Beroun

OS Beroun has one **assistant**: the manager of the system, privileged, part of the firmware, not an app. The assistant is a **slot**: a provider fills it.
Caroline is the first provider and the default one; Claude is the second.

This document is the contract: what OS Beroun gives the assistant, what it asks of a provider, and how each provider does it.

## 1. The provider

A provider is described by `assistants/<id>/assistant.json` and implemented by a module of the runtime (a subclass of `assistant.Provider`, `runtime/assistant.py`).

```json
{
  "format": 1,
  "id": "caroline",
  "name": "Caroline",
  "module": "caroline",
  "service": "caroline-backend",
  "views": {"chat": "caroline", "voice": "caroline-voice", "wizard": "caroline-wizard", "settings": "caroline"},
  "capabilities": ["ask", "chat", "voice", "face", "wizard", "update", "messaging"],
  "tools": "caroline-plugins"
}
```

| Field | Meaning |
| --- | --- |
| `id`, `name` | the provider; `name` is what the UI shows (the tile, the Settings row, the titles of her messages) |
| `module` | the runtime module that implements it (`caroline`, `claude`) |
| `service` | its backend: the id of an entry of `system/services.json` (privileged: never stopped for memory, runs in sleep, may take the window) |
| `views` | its surfaces, each the id of a view of `system/<id>/view.json`: `chat` (the first tile of the launcher), `voice` (the R2 button), `wizard` (the first start after the login), `settings` (its section in Settings). A provider without a view of its own names a standard view of OS Beroun (`assistant-chat`, `assistant-voice`, `assistant-wizard`) |
| `capabilities` | what it can do: `ask` (an app asks it, `POST /api/assistant/ask`), `chat`, `voice` (a continuous voice dialogue on R2), `face` (Visual Mode), `wizard`, `update` (its own updates), `messaging` (it hosts Communicator) |
| `tools` | how it gets the OS tools: `caroline-plugins` (her plugin loader, `caroline-overlay/ic_apps`) or `mcp` (the OS Beroun MCP server, `beroun-tools/beroun_tools/mcp_server.py`) |

Which provider is in the slot is chosen by **the build of the OS, never by the user** (owner, 2026-10-31): `assistants/build.json`, `{"assistant": "<id>"}`, default `caroline` (in SW mode); `INTELLICAT_ASSISTANT` overrides it for one run of the shell (a build step, a test). There is no setting, no API and no tile to change it on the device; Settings > Assistant opens the settings of the provider that is in the slot.
Only the active provider is the assistant: its chat view is the first tile, R2 is its voice (or its chat when it has no voice), its wizard comes after the login, it answers `ask`, it has the role `assistant`.

## 2. What a provider must do

| Operation (`assistant.Provider`) | What it is | Caroline | Claude | Codex |
| --- | --- | --- | --- | --- |
| `installed()` | its runtime is on the device | her runtime and tree (`scripts/caroline-provision.sh`) | the Claude Code CLI (`scripts/claude-provision.sh`) | the Codex CLI (`scripts/codex-provision.sh`) |
| `wanted()` | it should run now | an account and a network (also when not active: she hosts Communicator) | an account, a network, and it is active | the same |
| `ensure()`, `stop()`, `pid()`, `status()` | its backend's life | `runtime/caroline.py`: her supervisor, restarts with a growing pause, update probation | `runtime/claude_assistant.py`: `runtime/claude_backend.py`, the same restart policy | `runtime/codex_assistant.py`: `runtime/codex_backend.py`, the same restart policy |
| `ask(app, text, timeout)` | an app's request, answered to the app only | a tab `ask-<app>` of her chat WebSocket (`runtime/caroline_ask.py`) | a session `ask-<app>` of its backend, the same WebSocket protocol (section 4) | the same; a turn is one `codex exec --json` / `codex exec resume <thread> --json` process, nothing is kept between turns |
| `voice_start()`, `voice_end()` | around its voice view | her continuous dialogue on, Visual Mode on, the face over the screen; all put back after | nothing to do: R2 opens the standard voice view `assistant-voice` (the continuous dialogue: the microphone, `POST /api/stt/audio` (ai:stt), its tab `voice`, `POST /api/assistant/speech` (ai:tts), played; no face) | the same |
| `wizard_needed()`, `wizard_done()` | its first-start wizard | her wizard (`/wizard` of her backend, `wizard.json`) | the standard view `assistant-wizard` (also its settings view): the sign-in to Claude (the owner's Claude Code login) in the device browser (`claude auth login`, its `BROWSER` = `runtime/browser_open.py`), or with a phone (a QR code, the code typed back) | the same view: the sign-in to ChatGPT in the device browser (`codex login`), with a phone (`codex login --device-auth`: a QR code and a one-time code entered on the phone), or an OpenAI API key typed or pasted (`POST /api/auth/key`; its backend lists `methods`) |
| `update_status()`, `update_check()`, `update_now()` | its own updates | `runtime/caroline_update.py` | not supported (the CLI comes with the firmware) | the same |

## 3. What OS Beroun gives the assistant

**The role `assistant`.** The calls of the system API with the assistant as the owner (`owner="assistant"`): `/api/assistant/sys/<area>/<op>` (files, settings, time, network, power, services, store, display, notify, message boxes, sounds, vibration, its updates),
`/api/assistant/notifications/*`, `/api/assistant/apps`, `/api/assistant/app/prepare`, `/api/assistant/app/tool`, and the tasks of the apps' agents (`/api/agents/*`). It is the only caller that may manage the store besides the Store app.

**The OS tools.** One table (`beroun-tools/beroun_tools/table.py`) defines every tool: the group `beroun` (the apps, any window, the external screen, files, settings, clock, network, sleep, notifications, message box, sounds, vibration, the store, its own updates),
the group `agents` (the apps' agents), and one group `app-<id>` per app with a `"caroline"` / `"assistant"` section (its page tools, `<id>_open`, `<id>_close`). The same names for every provider:

* Caroline gets them as her plugins (`caroline-overlay/ic_apps/plugins.py` wraps the table) and the apps' MCP servers through her pool;
* an MCP provider gets them from the OS Beroun MCP server (stdio, standard library only), the apps' MCP servers as servers of their own (`app-<id>`), and the apps' skills (`SKILL.md`) as its skills;
* **her own tools** (her plugins that the table does not have: the group `messages` of Communicator's store, the messengers' and mail's send tools, her memory, reminders,
  calendar, contacts, notes, images, the device browser) stay hers in her process; an MCP provider gets them from her backend, which runs in every build (section 6), through the
  MCP server `caroline-tools` (`beroun-tools/beroun_tools/bridge_server.py`, a client of `/api/ic/bridge/*`, `caroline-overlay/ic_bridge`). The names are hers; a call runs her
  `dispatch()` in her session context of the tab `assistant:<tab>` of the provider's conversation. A reminder it schedules comes due in her scheduler and goes to the shell
  (`POST /api/assistant/remind {tab, text}`), which runs it as a turn of that tab (`caroline_ask.turn`) and shows the answer as a notification under the provider's name.

`tests/caroline_audit_test.py` checks that every route of the OS API is covered by a tool of the table (or is on its allow-list with the reason), so every provider reaches every OS API.

## 4. The chat protocol

A provider's backend that is used by a standard view, or by `ask`, speaks this WebSocket protocol (Caroline's backend speaks it already; it is the subset of hers that OS Beroun uses):

    ws://127.0.0.1:<port>/?tab=<id>
    -> {"type": "user_message", "text": "..."}
    -> {"type": "interrupt"}                                         (stops the turn; optional)
    <- {"type": "sdk_message", "message": <event>, "isFiller"?, "isNarration"?}

`<event>` is a message of the Claude Agent SDK's stream: `{"type": "system", "subtype": "init", ...}`, `{"type": "assistant", "message": {"content": [{"type": "text", "text"} | {"type": "tool_use", ...}]}}`,
`{"type": "user", ...}` (tool results), and, when the turn is over, `{"type": "result", "subtype", "is_error"}`. A tab is one conversation; it keeps its context between turns and across restarts of the backend.
The standard chat view also uses `<- {"type": "history", "events": [...]}` (sent on connect: the tab's conversation so far) and `<- {"type": "busy", "busy": true|false}`.

## 5. Backward compatibility

| Old (stays for ever) | New | Both lead to |
| --- | --- | --- |
| `/api/caroline/*` | `/api/assistant/*` | the active provider (the same handlers) |
| the manifest section `"caroline"` | `"assistant"` | the same validation and the same tools; an app has one of them, not both |
| the permission `caroline.ask` | `assistant.ask` | the same check |
| the tool names `beroun_*`, `agent_*`, `<app>_*` | unchanged | |
| `Platform.tools.handle` in the pages | unchanged | |

With the setting `assistant` = `caroline` (the default) the device behaves exactly as before the slot.

**The SW account (owner, 2026-10-31).** The device's login is the owner's SquirrelWisdom account, in every build: without it nothing of the assistant runs
(`wanted()` and R2 ask for it). Whatever provider is in the slot, the system's speech recognition (`ai:stt`) and speech (`ai:tts`) are paid by that subscription.

## 6. Communicator

Communicator rests on Caroline's own channel modules (Telegram, Slack, Signal, WhatsApp, mail): their live connections run in her backend. By the owner's decision (2026-10-31) **her backend stays Communicator's host** when another provider is active:
it runs as the Communicator service, not as the assistant (no chat, no voice, no wizard of hers), and the active provider reaches the messages, the send tools and the rest of
her own tools through her backend (section 3, `caroline-tools`).
