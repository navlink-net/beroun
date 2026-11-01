<!-- Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License. -->
# OS Beroun: technical documentation

**OS Beroun** is an operating environment for small handheld-style devices: an HTML-based launcher and apps, a shell daemon that owns the
system API, a service manager, an app store with signed packages, and a built-in privileged assistant, **Caroline**. It is a product of its own
and nothing in this documentation depends on a particular piece of hardware. Web version: https://caroline.partners.solutions/beroun/index.html

## Targets (where OS Beroun runs)

| Target | Status |
| --- | --- |
| A device running Linux with Weston and WPE WebKit | The reference target |
| A desktop simulator (WSL2, or a hosted online copy) | Running |
| Android (an app with a WebView shell) | Android APIs in place of Linux interfaces |

What a target must supply is in [TARGETS.md](TARGETS.md).

## Documents

| Document | What it covers |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layers, processes, repository map of the OS, state and data, principles |
| [SHELL_DAEMON.md](SHELL_DAEMON.md) | `shelld`: the HTTP/WebSocket API, services manager, notifications, time, network, accounts, logging |
| [APPS.md](APPS.md) | The app model: manifest, lifecycle and saved state, `Platform` API, logical input model, agents and A2A |
| [SYSTEM_API.md](SYSTEM_API.md) | MessageBox, sounds, vibration, the external display API, agents' Camerlengo calls |
| [CAROLINE.md](CAROLINE.md) | Caroline as a privileged part of the system: runtime, overlay, tools, updates |
| [STORE_AND_RELEASES.md](STORE_AND_RELEASES.md) | App store, package signing, packaging scripts, versions |
| [TARGETS.md](TARGETS.md) | The contract between the OS and a target (hardware interface, clock, network, power, display) |

Field-level references: [app-format.md](app-format.md) (the manifest), [system-api.md](system-api.md) (the system API),
[assistant-contract.md](assistant-contract.md) (the assistant slot).

## Code layout

The sources sit at the root of the repository: `runtime/`, `web-shell/`, `apps/`, `system/`, `assistants/`, `caroline-overlay/`,
`beroun-tools/`, `native/`, `config/`, `packages/`, `scripts/`. Caroline is the submodule `caroline/`.
