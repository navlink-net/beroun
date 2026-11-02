# Beroun

**Beroun (OS Beroun)** is a Linux-based operating system built around an AI Engine. The AI Engine manages the system: applications,
services, network, memory, power and updates. The owner sets the rules and can see everything the AI Engine does, or do the same by
hand. Beroun is aimed at developers building AI-native devices.

Technical base: Linux, Wayland (Weston), WPE WebKit, A2A-ready agents, ARM64.

Documentation on the web: **https://caroline.partners.solutions/beroun/index.html**

## The six system models

| Model | What it is |
| --- | --- |
| Application | An app is a page, a manifest and a service. Apps share one window, follow a state contract, and services can keep running after the window closes. One manifest describes apps, services and agents. |
| Hardware | One hardware layer, system buttons, power and sleep handling, a memory budget. |
| Network | Wi-Fi as a system service: profiles, access points, network time, system notifications. |
| Agent | Agents declared in the manifest, tasks with a life cycle, a bridge to A2A. |
| Storage | The owner's home directory on the memory card; app state and system data kept separate. |
| System API | Message boxes, sounds, vibration, notifications and the clock, for apps and the AI Engine. |

On top of these sit a system dispatcher, power and memory services, a task broker, and an app store with signed packages.

## Caroline

[Caroline](https://github.com/kostiakhait/caroline) is the system's coordinator and part of the OS, not an app. She accepts tasks,
selects specialists, follows execution and asks the owner for clarification. Her service is never stopped for memory, runs while the device
sleeps and may take the screen. This repository holds the integration (`caroline-overlay/`, `runtime/caroline*.py`, `system/caroline*`);
Caroline herself is a separate project.

## Repository layout

| Path | Content |
| --- | --- |
| `runtime/` | The shell daemon `shelld.py` (system API, launcher, file API, notifications, store, agents, display), manifest parser, service manager, network, time, power and memory daemons, A2A, agents |
| `web-shell/` | The launcher, `platform.js`, on-screen keyboard, focus frame, translations |
| `apps/` | The OS's own apps, one folder with `manifest.json` each |
| `system/` | The system's own views (assistant, Caroline chat, voice and wizard) and `services.json` |
| `caroline-overlay/` | Code that runs inside Caroline's process, without patching her checkout |
| `beroun-tools/` | `beroun_*` tools: every API of the system has one for the assistant |
| `virtual-hardware/` | `vhw.py`: the reference implementation of the hardware layer (uinput gamepad, power-supply files); a target supplies its own |
| `native/` | Native helpers in C (browser host on WPE, talking head, splash screens) |
| `config/` | `apps.json`, `versions.env` (the OS's own; the device's files are the board's layer, see [TARGETS.md](TARGETS.md)) |
| `packages/` | Example portable package |
| `scripts/` | Build, packaging and provisioning scripts |
| `assistants/` | The assistant slot: descriptions of the providers (Caroline, Claude, Codex) and the build's choice |
| `caroline/` | Submodule: [Caroline](https://github.com/kostiakhait/caroline) |
| `docs/` | Technical documentation, see below |

Versions of the stack (Weston, WPE WebKit, libwpe, wpebackend-fdo, cog) are pinned in `config/versions.env`.

## Extensions

A target (a simulator, a device build) adds its own pieces to the shell without the OS knowing about them. `runtime/extensions.py`
loads every folder with an `extension.json` found in `$BEROUN_EXTENSIONS` (folders separated like `PATH`) and gives its `register(api)` a
small door into `shelld`: pages, static folders, GET/POST routes, raw handlers (WebSocket, streams) and trusted pages. A broken extension is
logged and left out; the shell starts without it. The API is described at the top of `runtime/extensions.py`.

## Documentation

Start with [docs/README.md](docs/README.md).

| Document | Covers |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, processes, state and data, principles |
| [SHELL_DAEMON.md](docs/SHELL_DAEMON.md) | `shelld`: HTTP/WebSocket API, services, notifications, time, network, accounts |
| [APPS.md](docs/APPS.md) | App model: manifest, lifecycle, `Platform` API, input model, agents and A2A |
| [SYSTEM_API.md](docs/SYSTEM_API.md) | MessageBox, sounds, vibration, external display API |
| [CAROLINE.md](docs/CAROLINE.md) | Caroline as a privileged part of the system |
| [STORE_AND_RELEASES.md](docs/STORE_AND_RELEASES.md) | App store, package signing, versions |
| [TARGETS.md](docs/TARGETS.md) | The contract between the OS and a target device |
| [app-format.md](docs/app-format.md) | Field-level reference of the manifest |
| [system-api.md](docs/system-api.md) | Field-level reference of the system API |

## License

MIT, see [LICENSE](LICENSE).

Copyright (c) 2026 Konstantin Khait, IT Partners Solutions.

Third-party code keeps its own license: Babylon.js in `web-shell/lib/babylon/` (see `LICENSE.md` there).
