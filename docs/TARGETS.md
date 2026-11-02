# Targets: what the OS needs from the platform under it

OS Beroun does not know what device it runs on. Everything device-specific sits behind a small set of interfaces, each with a simulator
implementation (a *fake*) and a slot for the real one.

| Interface | In the code | Simulator implementation | What a real target provides |
| --- | --- | --- | --- |
| Input and sensors | `virtual-hardware/vhw.py` (`VirtualHardware`) | uinput gamepad and touchscreen; files for battery/AC | Linux input events for the logical buttons, sticks, touch; `/sys/class/power_supply` |
| Power | `runtime/powerd.py` | A process that sleeps/powers off/on the simulated console | Sleep on a short POWER press, shutdown on a long one; powering on from off is the target's hardware |
| Clock and time zone | `runtime/timectl.py` | `FakeClock`: host time plus an offset, own zone; the host clock is never touched | `SystemClock`: BusyBox `ntpd -q`, `/etc/localtime`, `hwclock` |
| Network | `runtime/netctl.py` | `FakeNet`: the host's network, made-up Wi-Fi networks to connect to | Wi-Fi control on `INTELLICAT_WIFI_IFACE` |
| Audio | ALSA / PulseAudio via the board's `layer/asound.conf` | Routed to the host's sound server (or a private one when hosted) | ALSA directly |
| Vibration | `runtime/sysapi.py` | Logged; no motor | Write strength into the file named by `INTELLICAT_VIBRA_DEV` |
| Display | Weston output 640x480 | Weston on a private X server or a nested window | DRM/KMS panel |
| External display | `runtime/extdisplay.py` (rules), a driver | A second private X server and Weston; plug via `POST /api/hdmi` | Connector hot-plug and mode from DRM/udev; a uinput pointer of its own |
| Storage | The card at `SD_MOUNT` | A sparse ext4 image | The device's SD card |
| Swap | `runtime/memd.py` | zram + an eMMC backing file in a directory | zram with writeback to eMMC |
| Temp-dir pressure | `runtime/tmpwatch.py` | Watches the host's tmpfs `/tmp` | Same if `/tmp` is RAM-backed |

## Required of any target

* Linux with Weston 14.x, WPE WebKit 2.52.x (libwpe, wpebackend-fdo, cog), Python 3 for the daemon, GStreamer with H.264/AAC and VP8/Opus decoding for media.
* A 640x480 display, a gamepad-style input set and touch (the UI is designed for them; see the input model in [APPS.md](APPS.md)).
* About 2 GB of RAM for everything open (measured peak on the simulator: 1695 MB under an 1800 MB limit).
* Ports for services: shell 8765, power 8764, Caroline 48765–48767, A2A 48772, store apps 49000–49999, inspector 9222 (+100 per simulator instance).

## Targets today

* **Simulators** (local and hosted) implement the same contract on a desktop.
* **Android**: the equivalents are Android APIs instead of Linux interfaces. There the equivalents are Android APIs instead of Linux interfaces.

## Adding a target interface

Add the method to `VirtualHardware` (or the matching module), expose it in `shelld`, document the real-target counterpart in the table above, and add a Caroline tool for any new `/api/` route.

## The device layer and the image

A target is described by a **board** (for the simulator: IntelliCat's `Firmware/board/intellicat-sim/`): `board.env` (name, architecture, display
geometry, `BEROUN_RECOVERY_KEYS`) and `layer/`, the files that belong to the device and not to the OS: `buttons.json` (keyboard → button map), `weston.ini`,
`asound.conf`, `swap.conf`. The target's scripts name the board in `BEROUN_BOARD_DIR`; the OS finds the files through `runtime/board.py`. What stays in
`config/` is the OS's own: `apps.json`, `versions.env`.

The **image** of a device (the whole system as one read-only filesystem, built from the OS and the board's layer) is planned in IntelliCat's
`Simulator/docs/image-ota-plan.md`.

**Flashing and keeping the device alive** (`runtime/imagefmt.py`, `runtime/flashd.py`, `scripts/beroun-image.py`): an image is signed (Ed25519) and encrypted (X25519 +
AES-256-GCM) with the **developer's** keys, which only the developer holds (the repository has public keys only, `config/system-keys.json`); a device takes only what the
developer made, in this order: signature, decryption, hash, version (never an older one), then it writes the slot that is not running. A new system is on trial (3 starts,
it confirms with `POST /api/boot/confirm`), otherwise the device goes back to the previous one; a damaged slot is never booted. What a target provides: the two slots and a
boot control block (the simulator: a directory, the emulated eMMC), the USB cable state, the battery. See IntelliCat `Simulator/docs/image-ota-plan.md` and `usage.md`. Also: `runtime/fastbootd.py` (the virtual USB cable: fastboot over TCP, client `scripts/beroun-flash.py`), `runtime/updated.py` (OTA from a static update server, `scripts/beroun-image.py publish/serve`).
