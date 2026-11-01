# OS Beroun: architecture

## Layers

```
 Frontends (HTML/JS in a WPE WebKit view)  launcher, apps, Caroline's views
 ─────────────────────────────────────────
 Shell daemon  shelld        system API, launcher, file API, notifications, store, agents, display
 Service manager             backends that outlive an app's window
 Caroline                    privileged assistant service + her tools for the system
 ─────────────────────────────────────────
 Window system               Weston (desktop-shell); apps are Wayland/XWayland clients or pages in a cog
 Target contract             hardware, clock, network, power, display (see TARGETS.md)
```

| Layer | Where | Responsibility |
| --- | --- | --- |
| Frontends | `web-shell/`, `apps/*`, `system/*` | The launcher and apps as HTML pages; Caroline's chat, voice and wizard views |
| Shell daemon | `runtime/shelld.py` and helpers | Spawns and tracks apps, serves pages, owns every API of the system |
| Services | `runtime/services.py`, manifests' `services`, `system/services.json` | Backends that survive an app being closed, with memory and priority rules |
| Caroline | submodule `caroline/`, `caroline-overlay/`, `runtime/caroline.py` | The assistant; started and watched as a privileged service |
| Native helpers | `native/` (C) | `ic-browser` (browser host on WPE), `ic-face-view` (talking head), `ic-screen` (splash screens) |
| Window system | Weston 14 | Window management stays entirely in Weston; the HTML shell only asks `shelld` to spawn and shows what it spawned |

The OS stack is **Weston 14 (desktop-shell) + WPE WebKit 2.52.6 (libwpe 1.16.3, wpebackend-fdo 1.16.1, cog 0.18.5)**, Python for the daemon and services,
C for native helpers. Versions are pinned in `config/versions.env`; a target's image must pin the same. The OS version is
`INTELLICAT_VERSION` there (1.0; shown in Settings > About and `GET /api/about`). The variable name predates the split.

## Main processes

| Process | Role |
| --- | --- |
| Weston | The compositor ("the screen", 640x480 on the reference device) |
| `shelld.py` | The shell daemon, one HTTP server (default port 8765) |
| `cog` instances | WPE web views: the launcher's shared window, and apps with their own window |
| Services | `alarmd`, `calendard`, messaging sidecars, `ratatosk`, Caroline's backend … declared in manifests |
| `ic-browser`, `ic-face-view` | Native helpers |
| Target-side daemons | Power, swap, temp-dir watchdog (see TARGETS.md) |

## Repository map of the OS

| Path | Content |
| --- | --- |
| `runtime/` | `shelld.py`, `manifest.py`, `services.py`, `sysapi.py`, `sysnotice.py`, `timectl.py`, `netctl.py`, `appinstall.py`, `agents.py`, `a2a_*.py`, `caroline*.py`, `face.py`, `ratatosk*.py`, `sw.py`, `extdisplay.py`, `extinput.py`, `stt_checks.py` |
| `web-shell/` | Launcher (`index.html`), `platform.js`, on-screen keyboard (`osk.js`), `focusframe.js`, `gameext.js`, translations in `i18n/` |
| `apps/` | The firmware's apps, one folder with `manifest.json` each |
| `caroline/` | Submodule: Caroline (`github.com/kostiakhait/caroline`) |
| `caroline-overlay/` | Code that runs inside Caroline's process, without patching her checkout |
| `native/` | Native helpers (C) |
| `system/` | The system's own views (`caroline`, `caroline-voice`, `caroline-wizard`, `ext-cat`) and `services.json` |
| `config/` | `apps.json` (non-manifest launchables), `buttons.json`, `weston.ini`, `versions.env` |

Everything that is specific to simulating a console (WSLg windows, the skinned console page, virtual gamepad, SD image, cgroups) is the
the simulator target's and is not part of this repository.

## State and data

The OS separates **system data**, **app data**, and the **user's files**:

| What | Location on the simulator target | Notes |
| --- | --- | --- |
| Logs and pid files | `~/.intellicat/run/` (`INTELLICAT_STATE`) | `shell.log`, `app-<id>.log`, per-daemon logs |
| System data | `~/.intellicat/data/` (`INTELLICAT_DATA`) | Launcher database (`/api/db/*`: `Platform.state`, app settings), settings, login, `agents/`, Caroline's workspace and code copy |
| Installed apps | `~/.intellicat/apps/<id>/` (`INTELLICAT_APPS`) | Writable; firmware apps are read-only |
| App data of store apps | `<data>/apps/<id>/` (`INTELLICAT_APP_DATA`) | Deleted with the app |
| User's files | The storage card, mounted at `SD_MOUNT` | The apps' file root; the file API (`/api/fs/*`) reaches nothing outside it |
| Account | `~/.mcp-notes/credentials.json` | SquirrelWisdom login shared with Caroline and other SW tools |

Paths and environment names carry `INTELLICAT_` because they were written for the simulator; a target may place them elsewhere.

## Principles

* **Caroline is part of the system, not an app.** Her views are `system/*`; her backend is a privileged service that is never stopped for memory, runs in sleep and may take over the window. Apps can never be privileged.
* **One manifest format** for apps and system views (`runtime/manifest.py`); unknown fields are errors.
* **The manifest is the approval.** Declared permissions, capabilities and agent rights are what an app may do; nobody clicks anything. What is not declared is not given.
* **Apps keep their place.** A shared-window app saves its state on close and returns to it (`Platform.state`).
* **Everything that touches a device is behind one interface** so a target supplies another backend (see TARGETS.md).
* **Every API of the system has a tool for Caroline** (`beroun_*`), enforced by `tests/caroline_audit_test.py`.
* **A small input model:** apps get the cursor, A and B; the rest is the system's or needs the owner's decision (see APPS.md).
* **Memory is a budget.** Services declare `memory_mb` and `priority`; over the limit the lowest priority is stopped first.
