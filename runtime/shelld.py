#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""IntelliCat shell daemon: serves the HTML shell and exposes launcher / hardware APIs.

It is the simulator's stand-in for what the firmware will provide to the HTML shell.
Apps are ordinary Wayland clients (native ones, or `cog` for HTML apps); the daemon only
spawns them and tracks the processes it spawned. Window management stays in Weston.
"""
import base64, hashlib, itertools, json, os, re, shutil, signal, sqlite3, subprocess, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SIM = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(SIM, "virtual-hardware"))
from vhw import VirtualHardware  # noqa: E402
import display  # noqa: E402
import importlib  # noqa: E402
import board  # noqa: E402
import flashd, imagefmt  # noqa: E402
import a2a_bridge, a2a_client, agents, androidhost, audio, appinstall, assistant, caroline, caroline_ask, caroline_update, extdisplay, extensions, extinput, extscreen, face, manifest, netctl, nativeproc, ratatosk, services, stt_checks, sw, sysapi, sysnotice, timectl, webaudio  # noqa: E402
NET = netctl.make()

STATE = os.environ.get("INTELLICAT_STATE", os.path.expanduser("~/.intellicat/run"))
PORT = int(os.environ.get("INTELLICAT_PORT", "8765"))
PORT_OFFSET = int(os.environ.get("INTELLICAT_PORT_OFFSET") or 0)   # a simulator with an instance name has every port shifted (scripts/lib.sh)
SOCKET = os.environ.get("INTELLICAT_SOCKET", "intellicat")
APPS = json.load(open(os.path.join(SIM, "config", "apps.json")))
# the online simulator (the hosted one, every visitor a stranger): the developer programs that give a shell of the machine are not there at all ("shell": true in apps.json;
# hidden is not enough: a hidden app is still launched by its id over the API)
ONLINE = os.environ.get("INTELLICAT_ONLINE") == "1"
APPS = [a for a in APPS if not (ONLINE and a.get("shell"))]


SERVICE_SPECS = []  # (app id, service spec) of every manifest
FIRMWARE_ROOT = os.path.join(SIM, "apps")
FIRMWARE_APPS = {}  # app id -> {name, version, system, hidden} of the apps in the firmware (apps): the store updates them, and removes the ones that are not "system"
ENDPOINTS = {}  # app id -> the manifest's "endpoints": what /api/app/<id>/<path> passes on to the app's service (docs/app-format.md)
APP_CAPABILITIES = {}  # app id -> the capability names its manifest declares (Platform.capabilities() answers with the ones this device has)
CAROLINE_APPS = {}  # app id -> {name, folder, caroline}: the apps Caroline can drive (the manifest section "caroline", docs/app-format.md)
AGENT_SPECS = {}  # app id -> (app name, the manifest's "agents"): the autonomous agents the apps publish for Caroline (runtime/agents.py, docs/architecture.md)
# KeyboardEvent.key of the Monitor window -> an X keysym (the characters go as themselves)
KEY_SYMS = {"Enter": 0xFF0D, "Backspace": 0xFF08, "Tab": 0xFF09, "Escape": 0xFF1B, "Delete": 0xFFFF, "Home": 0xFF50, "End": 0xFF57, "PageUp": 0xFF55, "PageDown": 0xFF56,
            "ArrowLeft": 0xFF51, "ArrowUp": 0xFF52, "ArrowRight": 0xFF53, "ArrowDown": 0xFF54, "Shift": 0xFFE1, "Control": 0xFFE3, "Alt": 0xFFE9, "Insert": 0xFF63}
CONSOLES = {"n": 0, "conns": []}  # how many console windows (and test clients) are connected to /ws: start.sh opens a window only when none has found its way back to a restarted shell
# the on-screen keyboard is up on the console's screen (a field has the focus, osk.js says so): the consoles type into it with the PC's own keyboard while it is ({"t": "typing"} on /ws)
OSK_TYPING = {"on": False}
EXTERNAL = {}  # app id -> the manifest's "external": {"entry" | "url", "state"}, the page for the external (HDMI) screen (docs/system-api.md)
APP_PERMISSIONS = {}  # app id -> the manifest's "permissions" (what the app itself may ask of the system, e.g. "caroline.ask"); the manifest is the approval


# The parts of the system itself (Caroline: her chat layer, her voice view, her wizard, her backend as a service) are part of the FIRMWARE, not apps:
# system/<id>/view.json (the same fields as an app's manifest), served at /system/<id>/..., and system/services.json.
FW_SYSTEM = os.path.realpath(os.path.join(SIM, "system"))


def scan_system():
    return scan_apps(FW_SYSTEM, "view.json", "system")


def scan_apps(root=None, descriptor="manifest.json", prefix="apps", only=None):
    """Registered web apps: apps/<id>/manifest.json {id, name, icon, entry}; with root/descriptor/prefix: the system's views. only: just that folder (an installed app)."""
    root = root or os.path.join(SIM, "apps")
    found = []
    for d in ([only] if only else sorted(os.listdir(root)) if os.path.isdir(root) else []):
        mf = os.path.join(root, d, descriptor)
        if not os.path.isfile(mf):
            continue
        m = json.load(open(mf))
        errs = manifest.validate(m, os.path.join(root, d), prefix == "system")
        if errs:  # one format for every app (docs/app-format.md): what does not fit is not registered
            print(f"manifest {os.path.join(d, descriptor)} refused: " + "; ".join(errs), flush=True)
            continue
        m = manifest.bind_binaries(manifest.normalize(m), os.path.join(root, d), log=lambda t: print(t, flush=True))   # {bin:...} of a package's native parts, for this device's target
        if prefix == "apps" and os.path.realpath(root) == os.path.realpath(FIRMWARE_ROOT):
            FIRMWARE_APPS[m["id"]] = {"name": m["name"], "version": m.get("version", ""), "system": bool(m.get("system")), "hidden": bool(m.get("hidden"))}
        APP_CAPABILITIES[m["id"]] = [c["name"] for c in m.get("capabilities") or []]
        if m.get("assistant") and prefix == "apps":  # how the assistant drives it (the section "assistant", old name "caroline"): its list of apps, the app's skills, MCP servers and page tools
            CAROLINE_APPS[m["id"]] = {"name": m["name"], "folder": os.path.join(root, d), "caroline": m["assistant"]}
        if m.get("endpoints") and prefix == "apps":
            ENDPOINTS[m["id"]] = m["endpoints"]
        if m.get("agents") and prefix == "apps":
            AGENT_SPECS[m["id"]] = (m["name"], m["agents"])
        if m.get("permissions") and prefix == "apps":
            APP_PERMISSIONS[m["id"]] = set(m["permissions"])
        if m.get("external") and prefix == "apps":
            EXTERNAL[m["id"]] = m["external"]
        for s in m.get("services", []):  # the backends of the app that survive its close: the service manager runs them (runtime/services.py)
            if isinstance(s.get("cmd"), list):  # {app} is the app's folder
                s = dict(s, cmd=[c.replace("{app}", os.path.join(root, d)) for c in s["cmd"]])
            SERVICE_SPECS.append((m["id"], s))
        if m.get("cmd") and not (m.get("page") and androidhost.ENABLED):  # a program of its own ({port}, {sim} are filled in at launch), not a web page in cog (on Android its "page")
            app = {"id": m["id"], "name": m["name"], "kind": "native", "cmd": m["cmd"]}
        else:
            app = {"id": m["id"], "name": m["name"], "kind": "html", **({"host": m["host"]} if m.get("host") else {}),
                   "url": (m.get("url") or "").replace(":48767/", f":{48767 + PORT_OFFSET}/") or f"{prefix}/{m['id']}/{m.get('page') or m.get('entry', 'index.html')}"}  # "url": a page of another origin (the wizard)
        if prefix == "system":  # a part of the firmware, not an app (it has its own window; the launcher shows its tile only if it is not hidden)
            app["system"] = True
        if m.get("hidden"):  # not a tile: launched by the system (the wizard, the voice view of Caroline)
            app["hidden"] = True
        if m.get("order") is not None:  # place among the tiles: smaller is earlier (Caroline's text chat is the first, top-left)
            app["order"] = int(m["order"])
        if m.get("window") == "shared":  # its frontend lives in the shared window (the launcher's cog), one frontend at a time; otherwise a cog of its own
            app["window"] = "shared"
        if m.get("microphone"):
            app["microphone"] = True
        if m.get("transparent"):  # the page has no background of its own: what is on the screen below shows through (the voice view)
            app["transparent"] = True
        if m.get("icon"):
            app["icon"] = f"/{prefix}/{m['id']}/{m['icon']}"
        if m.get("opens"):  # file types (extensions) the app opens when one is chosen in the file manager
            app["opens"] = [e.lower().lstrip(".") for e in m["opens"]]
        found.append(app)
    found.sort(key=lambda a: a.get("order", 100))  # stable: apps without an order keep the folder order
    return found


APPS = sorted(scan_system() + scan_apps(), key=lambda a: a.get("order", 100)) + APPS  # the system's tile (Caroline, order 0) comes first; the sort is stable
# Root of the files apps may touch (the "home directory"): the emulated SD card (scripts/sdcard.sh mounts it here).
HOME_ROOT = os.path.realpath(os.environ.get("INTELLICAT_HOME", "/media/sdcard"))
DATA_DIR = os.environ.get("INTELLICAT_DATA", os.path.expanduser("~/.intellicat/data"))  # launcher service data, not reachable through the file API
CLOCK = timectl.make(DATA_DIR, lambda: NET.status()["online"])  # the time, the time zone and network time (runtime/timectl.py)


def tree_version():
    """The version of the tree this shell runs from (config/versions.env): the device's factory system."""
    try:
        m = re.search(r'^INTELLICAT_VERSION="([^"]+)"', open(os.path.join(SIM, "config", "versions.env"), encoding="utf-8").read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def _restart_device():
    """After a flash the device restarts (powerd: off, then on); the simulator without powerd just goes on."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{8764 + PORT_OFFSET}/reboot", data=b"{}", method="POST"), timeout=5).read()
    except Exception as e:  # noqa: BLE001
        print(f"flash: no restart ({e})", flush=True)


# the system slots and the flashing (runtime/flashd.py); the storage is the device's eMMC (EMMC_DIR), the keys are OS Beroun's


def _battery():
    """(percent, on the charger) for flashd: a flash wants a charged battery."""
    p = HW.state()["power"]
    return int(p["capacity"]), bool(p.get("ac_online")) or "charg" in str(p.get("status", "")).lower() and "dis" not in str(p.get("status", "")).lower()


FLASH = flashd.Flasher(os.environ.get("EMMC_DIR", os.path.expanduser("~/.intellicat/emmc")), board.env("BOARD_NAME", "unknown"),
                       os.path.join(SIM, "config", "system-keys.json"), tree_version(), reboot=_restart_device, power=_battery)
# the start of the device: the slot is checked, a new system is on trial (it confirms below), one that did not start 3 times is given up (flashd.py)
BOOT = FLASH.boot()
print(f"boot: {BOOT} (system {FLASH.info()['version']}, slot {FLASH.info()['slot'].upper()})", flush=True)
if BOOT == "hang":   # the simulated broken system: the launcher never comes up, the watchdog restarts the device
    threading.Thread(target=lambda: (time.sleep(10), print("boot: the system did not come up, restarting", flush=True), _restart_device()), daemon=True, name="boot-hang").start()
MAX_READ = 1024 * 1024
CLOSE_GRACE = 2.0  # seconds an app gets to save and answer "closed" after MODE, then it is killed
BUTTONS = json.load(open(board.layer("buttons.json")))
HW = VirtualHardware(STATE)
_cd = os.path.join(STATE, "console_display")  # written by start.sh --console
CONSOLE_DISPLAY = open(_cd).read().strip() if os.path.exists(_cd) else None
STREAM = display.Streamer(CONSOLE_DISPLAY) if CONSOLE_DISPLAY else None
XIN = display.XInput(CONSOLE_DISPLAY) if CONSOLE_DISPLAY else None
# Caroline's backend: runs while the device has an account and a network (see caroline.py)
RATATOSK = ratatosk.Ratatosk(DATA_DIR, log=lambda m: print(m, flush=True))   # the account of the Ratatosk app and its chat list (stage 1)
CAROLINE = caroline.Caroline(SIM, STATE, DATA_DIR, lambda: sw.load_credentials() is not None and NET.status()["online"],
                             lambda m: print(m, flush=True))
# The assistant slot (runtime/assistant.py, docs/assistant-contract.md): the providers of the firmware (assistants/<id>/assistant.json) and the active one
FW_ASSISTANTS = os.path.realpath(os.path.join(SIM, "assistants"))
# The other providers (runtime/claude_assistant.py, runtime/codex_assistant.py): a build that does not ship one (scripts/assistant_build.py) has no such module, and the slot has no such provider.
# Their backends run only while they are in the slot (and there is an account and a network).
PROVIDER_MODULES = {}
for _mod, _cls in (("claude_assistant", "Claude"), ("codex_assistant", "Codex")):
    try:
        _impl = getattr(importlib.import_module(_mod), _cls)
    except ImportError:
        continue
    PROVIDER_MODULES[_mod.split("_")[0]] = _impl(SIM, STATE, DATA_DIR, lambda _id=_mod.split("_")[0]: sw.load_credentials() is not None and NET.status()["online"] and ASSISTANT.is_active(_id),
                                                 lambda m: print(m, flush=True))
ASSISTANT = assistant.Slot(assistant.load_specs(FW_ASSISTANTS), {"caroline": CAROLINE, **PROVIDER_MODULES}, os.path.join(FW_ASSISTANTS, "build.json"), lambda m: print(m, flush=True))
for _p in PROVIDER_MODULES.values():
    _p.start()   # their watchers ask the slot (wanted): only now that it exists
# The service manager: the backends that survive the close of an app (manifests "services"), their priority, budget, sleep and the take-over of the window
def _device_roots():
    roots = [os.getpid()]
    for f in ("weston.pid", "shell.pid"):
        try:
            roots.append(int(open(os.path.join(STATE, f)).read().strip()))
        except (OSError, ValueError):
            pass
    return roots


SERVICES = services.Manager(STATE, lambda m: print(m, flush=True), _device_roots, float(os.environ.get("INTELLICAT_MEM_LIMIT_MB", "1900")))
SERVICES.load([("", s) for s in json.load(open(os.path.join(FW_SYSTEM, "services.json")))])  # the system's own services (Caroline's backend, privileged): no app
SERVICES.load(SERVICE_SPECS)
SERVICES.register_module("caroline", CAROLINE.ensure, CAROLINE.stop, CAROLINE.pid)
for _id, _p in PROVIDER_MODULES.items():
    SERVICES.register_module(_id, _p.ensure, _p.stop, _p.pid)
# The broker of the apps' agents: the registry from the manifests, tasks with states, the journal (runtime/agents.py). An agent hosted by a service is asked
# at its endpoint; the service is started on demand, as when an app's page calls one of its endpoints.
def agents_registry(q):
    """The registry of the agents the apps publish: key (app/agent), app, description and the capabilities: the skills (name, description, input, output, long, confirm), the Camerlengo
    commands and the permissions the manifest declares. Filters (all optional, together): app (an app id), key (app/agent), q (a text in the key, description, skill names and descriptions)."""
    found = AGENTS.list_agents()
    if q.get("app"):
        found = [a for a in found if a["app"] == q["app"]]
    if q.get("key"):
        found = [a for a in found if a["key"] == q["key"]]
    if q.get("q"):
        needle = q["q"].lower()
        found = [a for a in found if needle in " ".join([a["key"], a.get("description", "")] + [f"{s['name']} {s.get('description', '')}" for s in a.get("skills", [])]).lower()]
    return [{k: a.get(k, [] if k in ("skills", "camerlengo", "permissions") else "") for k in ("key", "app", "app_name", "id", "description", "skills", "camerlengo", "permissions")} for a in found]


def _agent_port(agent):
    svc = SERVICES.services.get(agent["host"]["service"])
    if not svc or not svc.port:
        raise agents.Unreachable("the service has no port")
    if svc.stopped_for != "manual":
        try:
            SERVICES.ensure(svc.id)
        except Exception as e:
            raise agents.Unreachable(str(e))
    return svc.port


AGENTS = agents.Broker(os.path.join(DATA_DIR, "agents"), agents.http_transport(_agent_port), lambda m: print(m, flush=True),
                       ask_timeout=float(os.environ.get("INTELLICAT_AGENT_ASK_TIMEOUT_S") or agents.ASK_TIMEOUT_S),   # how long a question to the owner may wait (the tests shorten it)
                       a2a=a2a_client.Client(os.path.join(DATA_DIR, "agents", "a2a-keys.json")))   # agents whose host is a remote A2A agent (stage 8); the keys they name live in that file
for _app_id, (_app_name, _agent_list) in AGENT_SPECS.items():
    AGENTS.register(_app_id, _app_name, _agent_list)


def _discover_loop():
    """Reads the Agent Cards of the remote A2A agents: at the start, again when one could not be read (every 30 s) and now and then (every 10 min)."""
    while True:
        try:
            AGENTS.discover_due()
        except Exception as e:
            print(f"a2a: discovery failed: {e}", flush=True)
        time.sleep(5)


if any("a2a" in (_a.get("host") or {}) for _a in AGENTS.list_agents()):
    threading.Thread(target=_discover_loop, daemon=True).start()
# A2A inbound (runtime/a2a_bridge.py, docs/architecture.md): only when the manifest of some app exposes an agent; its own listener, bound to INTELLICAT_A2A_BIND (127.0.0.1 by default)
A2A_SERVER = A2A_KEY = None
if any(a2a_bridge.exposed(_a) for _a in AGENTS.list_agents()):
    try:
        A2A_SERVER, A2A_KEY = a2a_bridge.serve(AGENTS, os.environ.get("INTELLICAT_A2A_BIND") or "127.0.0.1", int(os.environ.get("INTELLICAT_A2A_PORT") or 48772 + PORT_OFFSET),
                                        os.path.join(DATA_DIR, "agents"), lambda m: print(m, flush=True))
    except OSError as _e:
        print(f"a2a: the listener could not start: {_e}", flush=True)
# Visual Mode: her talking head, shown directly on the screen by native/face/bin/<target>/ic-face-view from the model on the card (see face.py)
FACE = face.Face(SIM, STATE, HOME_ROOT, lambda m: print(m, flush=True))
CAROLINE.face = FACE
CAROLINE_PAGE_PORT = caroline.PAGE_PORT  # her chat page; her backend is told this origin (CAROLINE_PAGE_ORIGIN, caroline-overlay/sitecustomize.py)
# Her pages: the IntelliCat edition's own when it is built (its chat page has no Claude / OpenAI mode and does not ask for chat_mode_get
# and the other operations the edition's backend deliberately does not have; only chat.html and chat.js differ), her plain ones otherwise.
_EDITION_WWWROOT = os.path.join(SIM, "caroline", "dist-intellicat", "wwwroot")
CAROLINE_WWWROOT = _EDITION_WWWROOT if os.path.isdir(_EDITION_WWWROOT) else os.path.join(SIM, "caroline", "Windows", "Caroline", "wwwroot")
CAROLINE_SHELL_DIR = os.path.join(SIM, "caroline", "linux-shell")


def caroline_assets():
    """(CSS, glyph script) of her Linux shell: Selawik for "Segoe UI", drawn icons for the emoji the device has no glyphs for.
    Read from her caroline_shell.py (parsed, not imported: it needs GTK) so an update of the pin brings the new ones."""
    import ast
    tree = ast.parse(open(os.path.join(CAROLINE_SHELL_DIR, "caroline_shell.py"), encoding="utf-8").read())
    consts = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ("FONT_CSS", "ICON_CSS", "GLYPH_JS")}
    origin = f"http://127.0.0.1:{CAROLINE_PAGE_PORT}"
    return (consts["FONT_CSS"] + consts["ICON_CSS"]).replace("__ORIGIN__", origin), consts["GLYPH_JS"].replace("__ORIGIN__", origin)


procs = {}  # pid -> {"app": id, "name": str, "popen": Popen}
lock = threading.Lock()


inspector_ports = itertools.cycle(range(9223 + PORT_OFFSET, 9300 + PORT_OFFSET))  # the shell itself owns 9222; an app's port is free again long before the range comes round (it would run out after 77 launches otherwise)


def app_env():
    env = dict(os.environ, WAYLAND_DISPLAY=SOCKET, GDK_BACKEND="wayland", XDG_SESSION_TYPE="wayland")
    env["WEBKIT_INSPECTOR_SERVER"] = f"0.0.0.0:{next(inspector_ports)}"
    env["COG_PLATFORM_WL_VIEW_FULLSCREEN"] = "1"  # cog window fills the output instead of its default size
    env["IC_BROWSER_CONTROL"] = os.path.join(STATE, "browser.sock")  # the browser app's control channel (Caroline's browser tool)
    try:  # X11 apps (XWayland of the nested Weston); WSLg's own :0 is not ours
        env["DISPLAY"] = open(os.path.join(STATE, "xdisplay")).read().strip()
    except OSError:
        env.pop("DISPLAY", None)
    return env


def launch_env(app):
    env = app_env()
    if app.get("transparent"):  # a fullscreen window gets a black backdrop from the shell; a maximized one is blended with what is below
        env.pop("COG_PLATFORM_WL_VIEW_FULLSCREEN", None)
        env["COG_PLATFORM_WL_VIEW_MAXIMIZE"] = "1"
    return env


launch_args = {}  # app id -> what the app was started with (e.g. {"open": "Notepad/a.txt"}); the app takes it once

# The shared window: the launcher's own cog. The frontends of the apps with "window": "shared" are pages of it, one at a time (the launcher is one of
# them); an app that is closed saves its state, and the launcher takes its place. "next" is the frontend that comes when the current one has closed.
APPID_SUFFIX = os.environ.get("INTELLICAT_APPID_SUFFIX", "")   # "_<instance>": a cog's GApplication id is one per session bus, so every simulator names its own
SHELL_APPID = "com.intellicat.shell" + APPID_SUFFIX
LAUNCHER_URL = f"http://127.0.0.1:{PORT}/"
shared = {"app": None, "name": None, "closing": False, "next": None}


def shell_pid():
    """The pid of the launcher's cog (written by start.sh): the process the shared frontends live in. On Android (INTELLICAT_ANDROID_BRIDGE) the window is a layer of
    the Kotlin host and we are its owner."""
    if androidhost.ENABLED:
        return os.getpid()
    try:
        return int(open(os.path.join(STATE, "shell.pid")).read().strip())
    except (OSError, ValueError):
        return None


def window_open(url):
    """Navigate the shared window to a page: the running cog's own "open" action (a second cog does not hand its URL over). On Android: the host's shared layer."""
    if androidhost.ENABLED:
        try:
            return androidhost.window_open(url)
        except androidhost.BridgeError as e:
            ilog(f"window: cannot open {url}: {e}")
            raise RuntimeError("the shared window does not answer") from e
    env = dict(os.environ)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/0"), "bus"))
    try:
        r = subprocess.run(["cogctl", "-A", SHELL_APPID, "open", url], env=env, capture_output=True, timeout=10)
        if r.returncode:
            raise OSError(r.stderr.decode(errors="replace")[:200])
    except (OSError, subprocess.SubprocessError) as e:
        ilog(f"window: cannot open {url}: {e}")
        raise RuntimeError("the shared window does not answer") from e


def _shared_start(app, arg):
    """Show a shared frontend in the window (nothing is in it but the launcher)."""
    with lock:
        shared.update(app=app["id"], name=app["name"], closing=False, next=None)
    launch_args[app["id"]] = arg
    wizard_yield()
    url = app["url"] if "://" in app["url"] else f"http://127.0.0.1:{PORT}/{app['url']}"
    try:
        window_open(url)
    except RuntimeError:  # the window did not take the page: nothing is in it but the launcher
        with lock:
            shared.update(app=None, name=None, closing=False, next=None)
        raise


def launch_shared(app, arg):
    """One frontend at a time: when another is in the window it is asked to close (it saves its state) and this one comes after it."""
    with lock:
        cur = shared["app"]
        if cur == app["id"] and not shared["closing"]:
            return shell_pid()
        if cur:
            shared["next"] = app["id"]
            launch_args[app["id"]] = arg
    if cur:
        shared_close()
    else:
        _shared_start(app, arg)
    return shell_pid()


def shared_close():
    """MODE (or another app wanting the window): the frontend saves and answers "closed" over its /appws channel; killed (the window navigates away) after CLOSE_GRACE."""
    with lock:
        app_id = shared["app"]
        if not app_id or shared["closing"]:
            return
        conns = list(app_conns.get(app_id, []))
        shared["closing"] = True
    if not conns:  # no platform script in the page: nothing to wait for
        ilog(f"close: shared {app_id} has no channel, window goes on")
        return shared_finish(app_id)
    for c in conns:
        try:
            with _send_lock:
                display.ws_send(c, json.dumps({"t": "close"}))
        except OSError:
            pass
    ilog(f"close: asked shared {app_id} to close, {CLOSE_GRACE:g}s grace")
    threading.Timer(CLOSE_GRACE, shared_finish, (app_id, True)).start()


def shared_finish(app_id, forced=False):
    """The frontend is done (closed itself, was asked in vain, or exited): the next one, else the launcher, takes the window."""
    with lock:
        if shared["app"] != app_id:
            return  # already gone (the timer of a close that was answered)
        if forced and not shared["closing"]:
            return
    ext_gone(app_id)
    with lock:
        nxt, shared["app"], shared["name"], shared["closing"], shared["next"] = shared["next"], None, None, False, None
    app = next((a for a in APPS if a["id"] == nxt), None) if nxt else None
    if forced:
        ilog(f"close: shared {app_id} did not close in time, the window goes on")
    try:
        if app:
            _shared_start(app, launch_args.get(nxt))
        else:
            window_open(LAUNCHER_URL)
    except RuntimeError:
        pass


def launch(app_id, arg=None):
    app = next((a for a in APPS if a["id"] == app_id), None)
    if not app:
        raise KeyError(app_id)
    SERVICES.required_by(app_id)  # the services this app needs start (and stay after it closes)
    if app.get("window") == "shared":
        return launch_shared(app, arg)
    with lock:  # one instance per app
        for pid, v in procs.items():
            if v["app"] == app_id and v["popen"].poll() is None and pid not in closing:
                return pid
    if app["kind"] == "html" and androidhost.ENABLED:   # the window of an app of its own is a layer of the Kotlin host, not a cog
        url = app["url"] if "://" in app["url"] else f"http://127.0.0.1:{PORT}/{app['url']}"
        launch_args[app_id] = arg
        p = androidhost.Layer(app_id, url, bool(app.get("transparent")), bool(app.get("microphone")), app.get("host"))
        with lock:
            procs[p.pid] = {"app": app_id, "name": app["name"], "popen": p}
        if app_id in EXTERNAL:
            threading.Thread(target=lambda: (p.wait(), ext_gone(app_id)), daemon=True).start()
        return p.pid
    if app["kind"] == "html":
        url = app["url"] if "://" in app["url"] else f"http://127.0.0.1:{PORT}/{app['url']}"
        # distinct GApplication id: otherwise a second cog hands its URL to the first instance and exits
        cmd = ["cog", "--platform=wl", f"--gapplication-app-id=com.intellicat.app.{app_id.replace('-', '_')}{APPID_SUFFIX}", url]
        if app.get("transparent"):
            cmd[2:2] = ["--bg-color=#00000000"]
        if app.get("microphone"):  # the app's manifest asks for the microphone (only the Caroline layer): cog denies it by default
            cmd[2:2] = ["--set-permissions=all", "--enable-media-stream=true", "--media-playback-requires-user-gesture=false"]  # her answers are played without a tap
    else:
        cmd = [c.replace("{port}", str(PORT)).replace("{sim}", SIM) for c in app["cmd"]]
    log = open(os.path.join(STATE, f"app-{app_id}.log"), "ab")
    launch_args[app_id] = arg
    if nativeproc.is_part(cmd):   # a store package's native part on Android: the host loads it, its window is a layer
        p = nativeproc.NativePart(app_id, cmd, {"INTELLICAT_STATE": STATE, "INTELLICAT_PORT": str(PORT)})
        with lock:
            procs[p.pid] = {"app": app_id, "name": app["name"], "popen": p}
        return p.pid
    p = subprocess.Popen(cmd, env=launch_env(app), start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
    with lock:
        procs[p.pid] = {"app": app_id, "name": app["name"], "popen": p}
    if app_id in EXTERNAL:   # the process ended (closed, killed): its big page leaves the external screen with it
        threading.Thread(target=lambda: (p.wait(), ext_gone(app_id)), daemon=True).start()
    return p.pid


_ilog = open(os.path.join(STATE, "input.log"), "a", buffering=1) if os.path.isdir(STATE) else None


def ilog(msg):
    """Input diagnostics: what the console page sent and what the HTML pages actually received."""
    if _ilog:
        _ilog.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


# What a target adds to the shell (runtime/extensions.py): loaded at start from $BEROUN_EXTENSIONS, the simulator's console among them.
EXTENSIONS = extensions.Extensions(lambda m: (print(m, flush=True), ilog(m)))


notes = []  # notifications shown in the launcher's status bar and kept in its panel: {"id", "app", "text", "level", "vibrate", "ts", "at" (the clock's HH:MM), "read"}
_beep_path = None


def beep():
    """A short beep through the sound card (a device has a speaker; the simulator uses the WSLg sound)."""
    global _beep_path
    import math, struct, wave
    if _beep_path is None:
        _beep_path = os.path.join(STATE, "beep.wav")
        with wave.open(_beep_path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            n = int(16000 * 0.15)
            w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 880 * i / 16000) * min(1, i / 160, (n - i) / 160))) for i in range(n)))
    audio.play(_beep_path)


def notify(text, app="", level="info", sound=False, vibrate=False):
    """A system notification: text in the status bar; an error (or sound=True) also beeps; vibrate drives the vibration
    motor on the device (the simulator only logs it and flashes the bar). With do-not-disturb on the text still comes, but without sound and vibration."""
    if level not in ("info", "warning", "error"):
        level = "info"
    if dnd_on():
        sound = vibrate = False
        quiet = True
    else:
        quiet = False
    l = CLOCK.status()["local"]
    with lock:
        nid = notes[-1]["id"] + 1 if notes else 1
        n = {"id": nid, "app": app, "text": str(text), "level": level, "vibrate": bool(vibrate), "ts": time.time(), "at": f"{l['hour']:02d}:{l['minute']:02d}", "read": False}
        notes.append(n)
        del notes[:-100]
    if (level == "error" or sound) and not quiet:
        SYSSOUNDS.play({"error": "error", "warning": "warning"}.get(level, "notify"), force=True)   # quiet was decided above
    if vibrate:
        VIBRA.pulse("short")
    notes_event("posted", [n])
    if androidhost.ENABLED:   # also Android's own notification, so it shows with the app off the screen
        threading.Thread(target=_android_notify, args=(nid, n["app"], n["text"], level), daemon=True).start()
    return nid


def _android_notify(nid, title, text, level):
    try:
        androidhost.call("notify", id=nid, title=title, text=text, level=level)
    except androidhost.BridgeError:
        pass


# ---- the notification panel: what the launcher shows and what apps and Caroline may do with it ------------------------
def dnd_on():
    return bool(settings_get().get("dnd"))


def notes_list(scope=None):
    """The notifications, newest first. scope: an app id (an app sees only its own) or None (the launcher, Settings, Caroline see all)."""
    with lock:
        items = [dict(n) for n in reversed(notes) if scope is None or n["app"] == scope]
    return {"items": items, "unread": sum(1 for n in items if not n["read"]), "dnd": dnd_on()}


def _push(conns, msg):
    data = json.dumps(msg)
    for c in conns:
        try:
            with _send_lock:
                display.ws_send(c, data)
        except OSError:
            pass


# ---- the system API (runtime/sysapi.py, docs/system-api.md): system sounds, the vibration motor, MessageBox ----------------
def _page_push(msg):
    """To the frontend in the shared window over its /appws channel, or to the launcher (its own channel, id "launcher") while no app is there; True when a page took it."""
    conns = ext_conns(shared["app"] or "launcher", "console")
    if msg.get("t") == "msgbox_close" and shared["app"]:   # a box the launcher was showing before an app came is not left on it
        _push(ext_conns("launcher", "console"), msg)
    if conns:
        _push(conns, msg)
    return bool(conns)


def _all_pages_push(msg):
    with lock:
        conns = [c for cs in app_conns.values() for c in cs]
    _push(conns, msg)


SYSSOUNDS = sysapi.Sounds(STATE, dnd_on)
VIBRA = sysapi.Vibra(lambda: settings_get().get("vibration") is not False, dnd_on, emit=_all_pages_push, log=lambda m: ilog(m))
MSGBOX = sysapi.MsgBoxes(_page_push, lambda m: ilog(m))


def _clip_to_consoles(text, source):
    """The device clipboard changed: the consoles write it to the host's clipboard."""
    with lock:
        conns = list(CONSOLES["conns"])
    _push(conns, {"t": "clip", "text": text})


def _clip_ask():
    """Asks the consoles for the host's clipboard; how many there were."""
    with lock:
        conns = list(CONSOLES["conns"])
    _push(conns, {"t": "clip?"})
    return len(conns)


CLIP = sysapi.Clipboard(_clip_to_consoles)
CLIP_OSK_WINDOW_S = 3.0   # the keyboard may use the clipboard this long after the last touch or button
SYSAPI_PREFIXES = ("/api/msgbox", "/api/sound/", "/api/vibra", "/api/clipboard")


def _caroline_update_ask(version):
    """She is in use and an update is ready: the question goes to the owner (a MessageBox on the screen); yes lets the backend turn "ask" into "install"."""
    bid = MSGBOX.show("Caroline update", f"Caroline {version} is ready to install. She is busy; install it now? (Later: it installs by itself when she is idle.)",
                      buttons=["Install now", "Later"], icon="info", timeout=300, owner="system")
    threading.Thread(target=lambda: MSGBOX.result(bid, wait=305).get("index") == 0 and CAROLINE_UPDATER.confirm(), daemon=True, name="caroline-update-ask").start()


CAROLINE_UPDATER = caroline_update.Updater(CAROLINE, _caroline_update_ask, lambda text: notify(text, "System"), lambda m: print(m, flush=True))
CAROLINE.updater = CAROLINE_UPDATER


# ---- the external display (HDMI): docs/system-api.md ---------------------------------------------------------
# The logic is extdisplay.ExtDisplay (the stack of apps, the focus, the cable); the simulator's screen is extscreen.ExtScreen (a second Xvfb + Weston + the one big cog, started while
# the cable is plugged in); this driver joins them. The apps' big pages are the manifest's "external" (EXTERNAL above).
EXT = extscreen.ExtScreen(STATE, PORT, SOCKET, PORT_OFFSET // 100, lambda m: ilog(m), APPID_SUFFIX)


def ext_url(app):
    """The page the big window shows: the cat when no app asks for the screen."""
    if app is None:
        return f"http://127.0.0.1:{PORT}/system/ext-cat/index.html"
    x = EXTERNAL[app]
    if x.get("url"):
        return x["url"].replace("{port}", str(PORT))
    return f"http://127.0.0.1:{PORT}/apps/{app}/{x['entry']}?ext=1"


SUSPEND_GRACE = 2.0     # a big page that is covered, closed or unplugged has this long to save its state
ext_suspends = {}       # app id -> the Event of a big page that was asked to save and has not answered yet
ext_launch = {}         # app id -> {"arg", "resume"}: what its big page was opened with, given out once (GET /api/display/launch)


def ext_conns(app, screen):
    """The /appws channels of one page of an app: the page of the console ("console") or the big page ("big")."""
    with lock:
        return [c for c in app_conns.get(app, []) if getattr(c, "screen", "console") == screen]


def ext_suspend(app, reason):
    """The big page of `app` is about to be unloaded: it saves its state (Platform.onSuspend) and answers "suspended"; waits at most SUSPEND_GRACE."""
    conns = ext_conns(app, "big")
    if not conns:
        return
    ev = ext_suspends[app] = threading.Event()
    _push(conns, {"t": "suspend", "reason": reason})
    if not ev.wait(SUSPEND_GRACE):
        ilog(f"external screen: the page of {app} did not save in {SUSPEND_GRACE:g} s ({reason}), it goes on")
    ext_suspends.pop(app, None)


# THE KEYBOARD OF THE EXTERNAL SCREEN IS ON THE CONSOLE (the owner's rule: the big screen has no touchscreen, a keyboard is never drawn on it). A page of the big screen (osk.js with
# window.__icExternal: the apps' big pages, the sites in the browser's window there) tells the shell over /oskws that one of its fields has the focus; the console's page of the app on the
# big screen (else the app in front of the console) draws the keyboard ({"t": "osk_remote"} on its /appws) and sends back what is pressed ({"t": "osk_edit"}), which goes to that field.
# While the keyboard is up the cursor, A and B are the console's (they drive the keyboard); the sticks, X and Y stay the big screen's mouse.
ext_osk = {"field": None, "page": [], "type": ""}   # field: the /oskws channel of the page whose field has the focus; page: the console's channels that draw the keyboard


def osk_console_pages():
    """The console's page that draws the keyboard: the app on top of the external screen if its console page is open, else the app in front of the console."""
    top = EXTD.status()["top"]
    if top and ext_conns(top, "console"):
        return ext_conns(top, "console")
    ps = [r["app"] for r in running()]
    for app in reversed(ps):
        if ext_conns(app, "console"):
            return ext_conns(app, "console")
    return []


def osk_show(field, kind):
    with lock:
        ext_osk.update(field=field, type=kind)
        old = ext_osk["page"]
    pages = osk_console_pages()
    if old and old != pages:
        _push(old, {"t": "osk_remote", "show": False})
    with lock:
        ext_osk["page"] = pages
    _push(pages, {"t": "osk_remote", "show": True, "type": kind})
    EXTIN.release_all()   # a key held on the big screen does not stay down while the console takes the cursor
    ilog(f"osk: a field of the external screen, the keyboard is on the console ({len(pages)} page(s))")


def osk_hide(tell_page=True):
    with lock:
        pages, ext_osk["page"], ext_osk["field"] = ext_osk["page"], [], None
    if pages and tell_page:
        _push(pages, {"t": "osk_remote", "show": False})


def osk_up():
    return bool(ext_osk["page"])


def ext_gone(app):
    """The app was closed (MODE, it closed itself, it was killed): it leaves the external screen too."""
    try:
        EXTD.app_closed(app)
    except Exception as e:   # the screen must never stop an app from closing
        ilog(f"external screen: {app} left badly: {e}")


MONITOR_SEEN = {"t": 0.0}      # when the Monitor page last asked for the status: while it does, the window is open and is not opened a second time
EXT_WINDOW = {"open": True}    # POST /api/hdmi {"window": false}: plug the cable in without opening the Monitor window (the tests)


def ext_open_monitor():
    """The Monitor window of the simulator: the picture of the external screen, in a window of its own that can be stretched (an Edge app window, like the console's).
    One window at a time: it is not opened again while the page of the open one is still asking."""
    why = ("the window is switched off (POST /api/hdmi window=false)" if not EXT_WINDOW["open"]
           else f"a Monitor page asked {time.time() - MONITOR_SEEN['t']:.1f} s ago (a window of an earlier start is still open)" if time.time() - MONITOR_SEEN["t"] < 4 else "")
    if why:
        ilog(f"external screen: the Monitor window is NOT opened: {why}")
        return
    try:
        p = subprocess.Popen(["cmd.exe", "/c", "start", "msedge", f"--app=http://localhost:{PORT}/monitor", f"--window-size={EXTD.width + 16},{EXTD.height + 40}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        ilog(f"external screen: the Monitor window is asked for (cmd.exe pid {p.pid})")
    except OSError as e:
        ilog(f"external screen: no Monitor window ({e}); open http://localhost:{PORT}/monitor in a browser")


class ExtDriver:
    def show(self, app, arg, leaving=None, reason=None, resume=False):
        if leaving:
            ext_suspend(leaving, reason or "covered")   # the page that is left saves its state first
        if not EXT.running:
            EXT.start(EXTD.width, EXTD.height)
            ext_open_monitor()
        ext_launch[app] = {"arg": arg, "resume": bool(resume)} if app else None
        x = EXTERNAL.get(app) if app else None
        if x and x.get("cmd"):   # a native program is the window (the browser)
            fill = lambda t: t.replace("{port}", str(PORT)).replace("{sim}", SIM).replace("{state}", STATE)  # noqa: E731
            EXT.show_native([fill(c) for c in x["cmd"]], {k: fill(v) for k, v in (x.get("env") or {}).items()}, app_env())
        else:
            EXT.show(ext_url(app))

    def hide(self, leaving, reason):
        if leaving:
            ext_suspend(leaving, reason)
        EXT.stop()

    def resize(self, width, height):
        EXT.set_mode(width, height)

    def deliver(self, app, to, message):
        _push(ext_conns(app, to), {"t": "display_msg", "message": message})

    def broadcast(self, event):
        _all_pages_push(dict(event, t="display"))
        if event["event"] in ("stack", "disconnected"):
            osk_hide()            # another page is on the big screen (or none): its field is gone
        if event["event"] in ("focus", "disconnected"):
            EXTIN.release_all()          # nothing stays pressed on a screen that no longer hears the buttons
        if event["event"] in ("connected", "resize"):
            EXTIN.center()


EXTD = extdisplay.ExtDisplay(ExtDriver())
for _app_id, _x in EXTERNAL.items():   # the apps with a page for the external screen (the manifests' "external")
    EXTD.register_app(_app_id, _x)


class ExtInject:
    """What extinput.ExtInput does on the external screen: XTEST on its X server (made when the screen comes up).
    Every call is written to the input log ("ext:" lines): the moves and the wheel as a sum once in 0.5 s, the keys and buttons one by one; a call with no X connection says so."""
    _acc = {"t": 0.0, "move": 0, "wheel": 0, "last": None}

    def _flush(self, force=False):
        a, now = self._acc, time.time()
        if (a["move"] or a["wheel"]) and (force or now - a["t"] >= 0.5):
            ilog(f"ext: inject {a['move']} moves (last {a['last']}), {a['wheel']} wheel notches")
            a.update(t=now, move=0, wheel=0)

    def key(self, name, down):
        x = EXT.xinput
        ilog(f"ext: inject key {name} {'down' if down else 'up'}{'' if x else '  -- NO X CONNECTION, dropped'}")
        if x:
            x.key(name, down)

    def button(self, n, down):
        x = EXT.xinput
        self._flush(True)
        ilog(f"ext: inject mouse button {n} {'down' if down else 'up'}{'' if x else '  -- NO X CONNECTION, dropped'}")
        if x:
            x.button(n, down)

    def move(self, px, py):
        x = EXT.xinput
        self._acc["move"] += 1
        self._acc["last"] = (px, py) if x else "NO X CONNECTION, dropped"
        self._flush()
        if x:
            x.move(px, py)

    def wheel(self, dx, dy):
        x = EXT.xinput
        self._acc["wheel"] += 1
        if not x:
            self._acc["last"] = "NO X CONNECTION, dropped"
        self._flush()
        if x:
            x.wheel(dx, dy)


EXTIN = extinput.ExtInput(ExtInject(), EXTD)


_stick_log = {}


def ext_stick_log(which, x, y):
    """The sticks from the console window, logged when one leaves or enters its centre and then once in 0.5 s while it is held (not 60 times a second); with the external screen's state."""
    moving = abs(x) > 0.18 or abs(y) > 0.18
    prev = _stick_log.get(which, (False, 0.0))
    now = time.time()
    if moving != prev[0] or (moving and now - prev[1] >= 0.5):
        _stick_log[which] = (moving, now)
        st = EXTD.status()
        ilog(f"ext: stick {which} {x:.2f},{y:.2f} {'held' if moving else 'released'}; screen connected={st['connected']} focus={st['focus']} x-connection={'yes' if EXT.xinput else 'NO'}")


def ext_pointer_loop():
    """The pointer and the wheel of the external screen, made of the two sticks, 60 times a second, whatever the focus is (the focus is for the cursor, A and B)."""
    last = time.time()
    while True:
        time.sleep(1 / 60)
        now = time.time()
        dt, last = min(now - last, 0.1), now
        try:
            if EXTD.connected:
                EXTIN.tick(dt)
        except Exception as e:   # a stick must never stop the loop
            ilog(f"external screen: pointer loop: {e!r}")


def notes_event(event, items=()):
    """Tells the apps over their /appws channels ({"t": "notifications", event, items, unread, dnd}; Platform.notifications.on).
    event: posted | dismissed | read | dnd. The app the notifications belong to gets them with their text; every other app only
    {event: "changed", unread, dnd}, the counts, never what the notifications say; a do-not-disturb change goes to all as event "dnd"."""
    owners = {n["app"] for n in items}
    with lock:
        unread = sum(1 for n in notes if not n["read"])
        conns = {a: list(c) for a, c in app_conns.items() if c}
    dnd = dnd_on()
    for app, cs in conns.items():
        if app in owners:
            mine = [{k: n[k] for k in ("id", "text", "level", "at")} for n in items if n["app"] == app]
            _push(cs, {"t": "notifications", "event": event, "items": mine, "unread": unread, "dnd": dnd})
        else:
            _push(cs, {"t": "notifications", "event": "dnd" if event == "dnd" else "changed", "items": [], "unread": unread, "dnd": dnd})


def note_dismiss(nid, scope=None):
    with lock:
        gone = [n for n in notes if n["id"] == nid and (scope is None or n["app"] == scope)]
        notes[:] = [n for n in notes if n not in gone]
    notes_event("dismissed", gone)
    return {"removed": len(gone)}


def note_clear(app=None, scope=None):
    """Removes the notifications of one app, or all of them (scope None only: an app may clear only its own)."""
    if scope is not None and app not in (None, scope):
        raise PermissionError("an app can clear only its own notifications")
    app = app or scope
    with lock:
        gone = [n for n in notes if app is None or n["app"] == app]
        notes[:] = [n for n in notes if n not in gone]
    notes_event("dismissed", gone)
    return {"removed": len(gone)}


def note_read(scope=None):
    with lock:
        seen = [n for n in notes if not n["read"] and (scope is None or n["app"] == scope)]
        for n in seen:
            n["read"] = True
    notes_event("read", seen)
    return {"read": len(seen)}


def note_dnd(on):
    settings_set("dnd", bool(on))
    notes_event("dnd")
    return {"dnd": bool(on)}


# ---- the system's own notifications: the network coming and going, a low battery, a full card (runtime/sysnotice.py) ---------------------
def system_watch():
    seen = {}
    while True:
        time.sleep(10)
        try:
            sp = fs_space()
            used = 100.0 * sp["used"] / sp["total"] if sp.get("total") else None
            for text, level, loud in sysnotice.notices(seen, NET.status(), HW.state()["power"], used):
                notify(text, "System", level=level, sound=loud, vibrate=loud)
        except Exception as e:  # noqa: BLE001  (the watcher must not die: the hardware or the card may be absent for a moment)
            ilog(f"system notices: {e}")


threading.Thread(target=system_watch, daemon=True, name="system-notices").start()


def android_battery_watch():
    """On Android the battery is the phone's: its level and charging state are copied into the virtual battery that the status bar and the system notices read."""
    while True:
        try:
            p = androidhost.call("power")
            HW.set_battery(capacity=p["capacity"], status=p["status"], ac=p["ac"])
        except Exception as e:  # noqa: BLE001  (the host may not answer for a moment)
            ilog(f"android battery: {e}")
        time.sleep(30)


if androidhost.ENABLED:
    threading.Thread(target=android_battery_watch, daemon=True, name="android-battery").start()


# ---- voice input: hold-to-talk recording, ai:stt, Caroline's transcript checks -------------------------------------
MAX_RECORD_S = 300
_rec = None  # {"proc", "path"}


def stt_start():
    global _rec
    with lock:
        if _rec:
            return {"ok": True}
        path = os.path.join(STATE, "voice.wav")
        rec = audio.Recorder()
        rec.start(path, MAX_RECORD_S)
        _rec = {"proc": rec, "path": path}
    return {"ok": True}


def stt_stop():
    """Stops the recording and returns the recognized text ("" when nothing was said); raises sw.SwError."""
    global _rec
    with lock:
        rec, _rec = _rec, None
    if not rec:
        return {"text": ""}
    rec["proc"].stop()
    try:
        wav = open(rec["path"], "rb").read()
    except OSError:
        return {"text": ""}
    if len(wav) < 44 + 16000 * 2 * 0.3:  # shorter than 0.3 s: a stray touch, not speech
        return {"text": ""}
    text = sw.stt(wav, "wav")
    return {"text": stt_checks.check_transcript(text, sw.resolve)}


def browser_open(url, label="link"):
    """Opens `url` (http or https) in the device browser, in its own tab `label` brought to the front; the browser is started when it is not running.
    Through the browser's control channel (native/browser/ic-browser.c, the one Caroline's browser tool uses). Used by a sign-in that runs in a browser (an assistant's CLI)."""
    import socket as _socket
    url = str(url or "").strip()
    if not re.match(r"^https?://\S+$", url):
        raise ValueError("url must be an http or https address")
    path = os.path.join(STATE, "browser.sock")
    if not any(p["app"] == "browser" for p in running()):
        launch("browser")
    end = time.time() + 20
    while True:
        try:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(15)
            s.connect(path)
            break
        except OSError:
            s.close()
            if time.time() > end:
                raise RuntimeError("the browser did not come up")
            time.sleep(0.25)
    try:
        s.sendall(f"o1 open {re.sub(r'[^A-Za-z0-9_-]', '_', label) or 'link'} {base64.b64encode(url.encode()).decode()}\n".encode())
        line = b""
        while not line.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            line += chunk
    finally:
        s.close()
    parts = line.decode("utf-8", "replace").strip().split(" ", 2)
    if len(parts) < 2 or parts[1] != "ok":
        raise RuntimeError(base64.b64decode(parts[2]).decode("utf-8", "replace") if len(parts) > 2 else "the browser did not answer")
    return {"ok": True}


def stt_audio(body):
    """Speech a page has recorded itself ({audio: base64, format: wav|webm|ogg|mp3}) -> {"text"}: the same ai:stt and checks as hold-to-talk (the voice view of the assistant)."""
    fmt = str(body.get("format") or "wav")
    if fmt not in ("wav", "webm", "ogg", "mp3"):
        raise ValueError("format is one of wav, webm, ogg, mp3")
    try:
        audio = base64.b64decode(str(body.get("audio") or ""), validate=True)
    except ValueError:
        raise ValueError("audio is not base64")
    if len(audio) < 1000:
        return {"text": ""}   # nothing that could be speech
    return {"text": stt_checks.check_transcript(sw.stt(audio, fmt), sw.resolve)}


_MD = [(re.compile(r"```.*?```", re.S), " "), (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"), (re.compile(r"<[a-z/][^>]*>", re.I), " "),
       (re.compile(r"^\s*(?:#{1,6}\s+|>\s?|[-*+•]\s+)", re.M), ""), (re.compile(r"(\*\*|\*|__|~~|`+)(?=\S)(.+?)(?<=\S)\1"), r"\2"), (re.compile(r"[ \t]*\|[ \t]*"), ", ")]


def speech_text(text):
    """What of an answer is said aloud: its words without the marks of formatting (code blocks, links, emphasis, list marks, tables)."""
    for rx, rep in _MD:
        text = rx.sub(rep, text)
    return re.sub(r"\s+", " ", text).strip()


def assistant_speech(body):
    """The voice of an assistant without a voice of its own (system/assistant-voice): {text} -> {audio: base64 MP3, mime} by ai:tts on the owner's SW subscription."""
    text = speech_text(str(body.get("text") or ""))
    if not text:
        return {"audio": "", "mime": "audio/mpeg"}
    return {"audio": sw.tts(text, str(body.get("voice") or "Nova")), "mime": "audio/mpeg"}


def notifications(since):
    with lock:
        out = {"items": [n for n in notes if n["id"] > since], "last": notes[-1]["id"] if notes else 0}
    out["dnd"] = dnd_on()  # the bar shows a DND chip
    return out


app_conns = {}  # app id -> [handler of its /appws connection]
closing = set()  # pids that were asked to close
_send_lock = threading.Lock()


def home():
    """The device's MODE button: ask the newest app to close so the launcher is visible again."""
    if sw.load_credentials() is None and not NET.status()["online"]:
        return  # first start without a network: the Wi-Fi app cannot be left, and the login cannot be skipped
    ps = running()
    if voice_running(ps):
        return  # only R2 ends the voice dialogue (MODE does not touch it)
    if ps:
        request_close(ps[-1]["pid"], ps[-1]["app"])


def voice_view():
    """The view R2 opens: the active assistant's voice view, or its chat when it has no voice (docs/assistant-contract.md)."""
    a = ASSISTANT.active
    return a.view("voice") if a.has("voice") else a.view("chat")


def voice_running(ps=None):
    """Whether the voice view of an assistant (a view of kind "voice" of any provider) is open: only R2 ends it."""
    views = {p.view("voice") for p in ASSISTANT.providers.values() if p.has("voice")} - {None}
    return any(p["app"] in views for p in (running() if ps is None else ps))


def start_button(arg=None):
    """The device's R2 button (the lower right shoulder button) is the assistant. Caroline: the continuous voice dialogue, her voice view (system/caroline-voice) shown over
    whatever is on the screen, and R2 ends it; input and output are only voice, with the animation. A provider without a voice: R2 opens its chat, and R2 closes it again.
    The text chat is also the first tile of the launcher, an ordinary app closed by MODE. The conversations stay in the backend; the view's page ends when it is closed
    (a lighter way than keeping a hidden view, which Weston's desktop shell has no protocol for)."""
    if sw.load_credentials() is None:
        return  # no account yet: the first-start flow comes first
    a, view = ASSISTANT.active, voice_view()
    if not view:
        return
    for p in running():
        if p["app"] == view:
            return request_close(p["pid"], view)
    if not a.has("voice"):
        launch(view, arg)
        return
    a.voice_start(app_env(), lambda: launch(view, arg))
    threading.Thread(target=_voice_restore, args=(a, view), daemon=True).start()


def _voice_restore(a, view):
    """Waits until the voice view is gone (R2, a crash, a kill) and lets the provider put back what its voice changed."""
    time.sleep(3)
    while any(p["app"] == view for p in running()):
        time.sleep(0.5)
    a.voice_end()


REMIND_TRIES, REMIND_PAUSE_S = 30, 20.0   # a busy tab is tried again for up to ten minutes


def assistant_remind(tab, text):
    """A reminder that the assistant in the slot scheduled with Caroline's tools (caroline-overlay/ic_bridge) is due: it runs as a turn of the tab that scheduled it,
    and what the assistant says is shown as a notification under its name. Taken at once (her scheduler waits for the answer); False when it cannot be now."""
    a = ASSISTANT.active
    if a.id == "caroline" or not a.chat_port():
        return False   # her own reminders never come here; with her in the slot there is nobody else to give it to
    st = a.status()
    if not st.get("healthy") or not st.get("signed_in"):
        return False
    threading.Thread(target=_remind_run, args=(a, str(tab or "1"), text), daemon=True, name="assistant-remind").start()
    return True


def _remind_run(a, tab, text):
    for _ in range(REMIND_TRIES):
        try:
            answer = caroline_ask.turn(a.chat_port(), tab, text, 600.0)
            break
        except caroline_ask.Busy:
            time.sleep(REMIND_PAUSE_S)
        except (caroline_ask.Unavailable, caroline_ask.Failed, caroline_ask.Timeout) as e:
            ilog(f"assistant: a reminder on tab {tab} failed: {e}")
            return
    else:
        ilog(f"assistant: a reminder on tab {tab} found it busy for {int(REMIND_TRIES * REMIND_PAUSE_S)} s; dropped")
        return
    ilog(f"assistant: a reminder ran on tab {tab} ({len(answer)} characters)")
    if answer.strip():
        notify(answer.strip()[:400], a.id)


_wizard = {"pid": None, "suppressed": False, "yielded": False}


def wizard_view():
    """The active assistant's first-start wizard (a view id), or None."""
    a = ASSISTANT.active
    return a.view("wizard") if a.has("wizard") else None


def assistant_view_hidden(app_id):
    """A view of an assistant that is not the active one is not offered (its tile): only the assistant in the slot is on the screen."""
    mine = set((ASSISTANT.active.spec.get("views") or {}).values())
    others = {v for p in ASSISTANT.providers.values() if not ASSISTANT.is_active(p.id) for v in (p.spec.get("views") or {}).values()}
    return app_id in others - mine


def wizard_yield():
    """A frontend comes into the shared window (the launcher's cog, which is under the wizard's own view): the wizard steps aside so it is seen (found 2026-10-29: the clock,
    an alarm ringing, any shared app opened by the API or by Caroline was loaded hidden under it). Not "later": it comes back when the window is the launcher's again."""
    pid = next((p["pid"] for p in running() if p["app"] == wizard_view()), None)
    if pid:
        _wizard["yielded"] = True
        ilog("wizard: steps aside for a frontend of the shared window")
        try:
            close(pid)
        except (KeyError, OSError):
            pass


def wizard_tick():
    """The first-start flow after the login: while her wizard is needed it is shown (its own view over the launcher), and it
    ends by itself when the page finishes or dismisses it ("finish later"). Closing it with MODE also means later: it does
    not come back before the next start of the device. It steps aside while a frontend is in the shared window (wizard_yield)
    and comes back after it. No account or no network: no backend, so no wizard."""
    a, view = ASSISTANT.active, wizard_view()
    if not view:
        return
    needed = a.wizard_needed() if sw.load_credentials() is not None else None
    pid = next((p["pid"] for p in running() if p["app"] == view), None)
    if _wizard["yielded"] and not pid and not running():
        # the screen is the launcher's again (no shared frontend and no window of its own: one opened meanwhile, the browser, would be covered): the wizard comes back
        # (its old pid is not a "closed from outside")
        _wizard["yielded"], _wizard["pid"] = False, None
    if _wizard["pid"] and not pid and needed and not _wizard["yielded"]:
        _wizard["suppressed"] = True  # it was closed from outside while still needed
    _wizard["pid"] = pid
    if needed and not pid and not _wizard["suppressed"] and not _wizard["yielded"]:
        launch(view)
    elif pid and not needed and needed is not None:
        close(pid)  # answered or dismissed in the page


def wizard_pending():
    """Will her start wizard come now (decided without her backend: signed in, she is installed and wanted, her wizard.json does not say finished or dismissed, it was not
    closed from outside, and it is not already up)? Then the launcher shows the wait screen until the wizard takes the window (wizard_tick starts it when her backend answers)."""
    a, view = ASSISTANT.active, wizard_view()
    if not view or sw.load_credentials() is None or _wizard["suppressed"] or not a.installed() or not a.wanted() or a.wizard_done():
        return False
    return not any(p["app"] == view for p in running())


def wizard_watch():
    while True:
        try:
            wizard_tick()
        except Exception as e:  # noqa: BLE001 - a failing check must never stop the device
            print(f"wizard watch: {e}", flush=True)
        time.sleep(3)


# ---- Communicator in the shell: the unread count on its tile, and one notification per incoming message --------------
BADGES = {}   # app id -> a count shown on its tile (/api/apps carries it)
NEWS_S = 180  # a message older than this when it is stored is not announced


def messaging_url(path):
    target = int(os.environ.get("COMMUNICATOR_BACKEND_PORT") or caroline.PORT)
    return f"http://127.0.0.1:{target}/api/ic/messages/{path}"


def messaging_get(path, timeout):
    with urllib.request.urlopen(messaging_url(path), timeout=timeout) as r:
        return json.load(r)


def message_note(ev):
    """The notification of one `message` event of the messaging backend, or None when it is not for the owner's ears: what the owner
    wrote himself, what arrived on Caroline's own accounts, and anything in a muted conversation make no sound."""
    d = ev.get("data") or {}
    if d.get("outgoing") or d.get("role") != "owner" or d.get("muted"):
        return None
    if d.get("ts") and time.time() - float(d["ts"]) > NEWS_S:   # an adapter's catch-up after a start or a link brings old messages: not news
        return None
    who = d.get("title") or d.get("sender_name") or d.get("service") or ""
    body = (d.get("text") or "").strip() or ("[file]" if d.get("files") else "")
    return f"{who}: {body}"[:120] if body else None


def messaging_tick(state):
    """One round: refresh the tile count and wait (a long poll) for what happened; `state['seq']` is where the log was read up to.
    Returns False when the backend is not there (the caller waits a little)."""
    try:
        if state["seq"] is None:                                  # first contact: what happened before now is not news
            state["seq"] = messaging_get("changes?since=0", 5)["seq"]
        BADGES["communicator"] = int(messaging_get("unread", 5).get("total", 0))
        res = messaging_get(f"changes?since={state['seq']}&wait=20", 30)
    except (OSError, ValueError, KeyError):
        BADGES.pop("communicator", None)
        state["seq"] = None
        return False
    state["seq"] = res.get("seq", state["seq"])
    for ev in res.get("events", []):
        if ev.get("kind") == "message":
            text = message_note(ev)
            if text:
                notify(text, app="communicator", level="info", sound=True, vibrate=True)
    return True


def messaging_watch():
    state = {"seq": None}
    while True:
        try:
            ok = messaging_tick(state)
        except Exception as e:  # noqa: BLE001
            print(f"messaging watch: {e}", flush=True)
            ok = False
        time.sleep(0.2 if ok else 5)


# The buttons that belong to the system and never reach an application (owner, 2026-10-27): L1 and L2 are the volume rocker (L1 down, L2 up),
# POWER is the power key (its own input device, as on the real console: short press sleep, long press power off, both by powerd), R2 starts
# Caroline (it ends her too). R1 and START are ordinary buttons again.
SYSTEM_BUTTONS = ("l1", "l2", "r2", "power")
VOLUME_STEP = 5
_vol_held = {}
_vol_lock = threading.Lock()


_audio_env = audio.env
volume_get = audio.volume_get


def volume_step(delta):
    """One step of the volume rocker: clamped to 0..100 %, shown in the status bar."""
    with _vol_lock:
        cur = volume_get()
        if cur is None:
            return None
        new = max(0, min(100, cur + delta))
        if new != cur:
            audio.volume_set(new)
        notify(f"Volume {new}%", "System")
        return new


def volume_button(up, on):
    """L2 (up) / L1 (down): a step at the press, then repeats while it is held."""
    key = "up" if up else "down"
    if not on:
        _vol_held[key] = False
        return
    _vol_held[key] = True
    delta = VOLUME_STEP if up else -VOLUME_STEP
    volume_step(delta)

    def repeat():
        time.sleep(0.45)
        while _vol_held.get(key):
            volume_step(delta)
            time.sleep(0.15)
    threading.Thread(target=repeat, daemon=True).start()


# ---- sleep: the apps are paused and the screen is off; the power button (R1, short press, seen by powerd) wakes ---------
POWER = {"asleep": False, "groups": []}
LAST_INPUT = {"t": 0.0}   # when a button or the screen was last touched (a call that woke the console sends it back to sleep if nobody has touched it since)
_power_lock = threading.Lock()


def asleep():
    return POWER["asleep"]


def sleep_toggle(want=None):
    """Sleep or wake. want: "asleep" / "awake" to be sure of the state (powerd wakes the console before the farewell screen), None: the other one."""
    with _power_lock:
        if want == "awake" and not POWER["asleep"] or want == "asleep" and POWER["asleep"]:
            return {"asleep": POWER["asleep"]}
        if POWER["asleep"]:
            for g in POWER["groups"]:
                try:
                    os.killpg(g, signal.SIGCONT)
                except (ProcessLookupError, PermissionError):
                    pass
            POWER["groups"] = []
            SERVICES.sleep_now(False)
            if STREAM:
                STREAM.set_blank(False)
            POWER["asleep"] = False
            ilog("power: awake")
        else:
            groups = []
            with lock:
                pids = [pid for pid, v in procs.items() if v["popen"].poll() is None]
            if FACE.proc is not None and FACE.proc.poll() is None:
                pids.append(FACE.proc.pid)
            if shared["app"] and shell_pid():  # a frontend in the shared window is paused like an app of its own (the launcher alone is not)
                pids.append(shell_pid())
            for pid in pids:
                try:
                    g = os.getpgid(pid)
                    if g != os.getpgrp() and g not in groups:
                        os.killpg(g, signal.SIGSTOP)
                        groups.append(g)
                except (ProcessLookupError, PermissionError):
                    pass
            POWER["groups"] = groups
            SERVICES.sleep_now(True)  # the services that do not run in sleep are paused too
            if STREAM:
                STREAM.set_blank(True)
            POWER["asleep"] = True
            ilog(f"power: asleep ({len(groups)} process groups paused)")
        return {"asleep": POWER["asleep"]}


def hw_button(name, on):
    if asleep() and name != "power":
        return  # asleep: the power key is the only one that is heard
    LAST_INPUT["t"] = time.time()
    if name == "power":
        HW.power_key(on)  # powerd reads it (short press: sleep and wake, long press: power off)
        return
    HW.button(name, on)
    if name == "r2" and on:
        start_button()
    elif name in ("l1", "l2"):
        volume_button(name == "l2", on)


def request_close(pid, app_id):
    """Ask the app (over its /appws channel) to save and close; kill it if it has not by CLOSE_GRACE."""
    if shared["app"] == app_id and pid == shell_pid():  # a frontend in the shared window: the window stays, the page is replaced
        return shared_close()
    with lock:
        if pid in closing:
            return
        conns = list(app_conns.get(app_id, []))
    if not conns:  # no platform script in the page (or a native app): nothing to wait for
        ilog(f"close: {app_id} has no channel, terminating")
        return close(pid)
    with lock:
        closing.add(pid)
    for c in conns:
        try:
            with _send_lock:
                display.ws_send(c, json.dumps({"t": "close"}))
        except OSError:
            pass
    ilog(f"close: asked {app_id} (pid {pid}) to close, {CLOSE_GRACE:g}s grace")
    threading.Timer(CLOSE_GRACE, force_kill, (pid, app_id)).start()


def force_kill(pid, app_id):
    with lock:
        p = procs.get(pid)
        closing.discard(pid)
    if p and p["popen"].poll() is None:
        ilog(f"close: {app_id} (pid {pid}) did not close in time, killed")
        if isinstance(p["popen"], (androidhost.Layer, nativeproc.NativePart)):
            return p["popen"].terminate()
        os.killpg(os.getpgid(pid), signal.SIGKILL)


app_tool_waits = {}  # call id -> {"event", "answer"}: Caroline's calls of a tool of an app's page, waiting for the page's answer
_tool_seq = [0]
TOOL_OPEN_WAIT = 12.0  # an app Caroline asks to act in is opened first; its page has this long to connect


def app_tool(app_id, tool, args, timeout=30.0, screen="console"):
    """A tool of an app's page, called by Caroline: the page (Platform.tools) answers over its /appws channel. The tool is one the manifest declares
    ("caroline": {"tools": [...]}) or one of the built-in dom_* tools of platform.js, which every app has. The app is opened first when it is not open."""
    declared = {t["name"] for t in (CAROLINE_APPS.get(app_id) or {}).get("caroline", {}).get("tools", [])}
    if tool not in declared and not tool.startswith("dom_"):
        raise KeyError(f"{app_id} has no page tool '{tool}'")
    if not any(a["id"] == app_id for a in APPS):
        raise KeyError(f"no app '{app_id}'")
    if screen == "console" and not ext_conns(app_id, "console"):
        launch(app_id)
        end = time.time() + TOOL_OPEN_WAIT
        while not ext_conns(app_id, "console") and time.time() < end:
            time.sleep(0.1)
    conns = ext_conns(app_id, "big" if screen == "external" else "console")
    with lock:
        _tool_seq[0] += 1
        cid = _tool_seq[0]
        wait = app_tool_waits[cid] = {"event": threading.Event(), "answer": None}
    if not conns:
        app_tool_waits.pop(cid, None)
        if screen == "external":
            return {"text": f"{app_id} has no page on the external screen", "is_error": True}
        return {"text": f"{app_id} did not open its page in time", "is_error": True}
    try:
        for c in conns:
            with _send_lock:
                display.ws_send(c, json.dumps({"t": "tool", "id": cid, "name": tool, "args": args or {}}))
        if not wait["event"].wait(timeout):
            return {"text": f"the page of {app_id} did not answer '{tool}' in {timeout:g} s", "is_error": True}
        return wait["answer"]
    finally:
        app_tool_waits.pop(cid, None)


def app_closed(app_id):
    """The app answered "closed": it has saved, end its process."""
    ext_gone(app_id)
    if shared["app"] == app_id:  # its frontend was a page of the shared window: the launcher (or the next frontend) takes it
        ilog(f"close: shared {app_id} closed itself")
        return shared_finish(app_id)
    with lock:
        pids = [k for k, v in procs.items() if v["app"] == app_id and k in closing]
    for pid in pids:
        ilog(f"close: {app_id} (pid {pid}) closed itself")
        try:
            close(pid)
        except (KeyError, ProcessLookupError):
            pass


_db = None
_db_lock = threading.Lock()


def db():
    """Launcher database (SQLite): per-app key/value settings, offered to apps as a service."""
    global _db
    if _db is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _db = sqlite3.connect(os.path.join(DATA_DIR, "launcher.db"), check_same_thread=False)
        _db.execute("CREATE TABLE IF NOT EXISTS kv (app TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY (app, key))")
        _db.commit()
    return _db


def db_get(app, key):
    with _db_lock:
        row = db().execute("SELECT value FROM kv WHERE app=? AND key=?", (app, key)).fetchone()
    return {"value": row[0] if row else None}


def db_set(app, key, value):
    with _db_lock:
        db().execute("INSERT INTO kv (app, key, value) VALUES (?,?,?) ON CONFLICT(app, key) DO UPDATE SET value=excluded.value", (app, key, value))
        db().commit()
    return {"ok": True}


WEB_SHELL = os.path.join(SIM, "web-shell")
# The types the static handler serves (the shell's pages, /apps/, /lib/): what a page and 3D (Babylon.js) load. .wasm must be exactly application/wasm
# (WebAssembly.instantiateStreaming refuses anything else); anything not listed is application/octet-stream.
STATIC_TYPES = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json",
                ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".ico": "image/x-icon",
                ".wasm": "application/wasm", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".ktx2": "image/ktx2", ".bin": "application/octet-stream",
                ".env": "application/octet-stream", ".babylon": "application/octet-stream", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
                ".woff2": "font/woff2", ".ttf": "font/ttf", ".map": "application/json", ".txt": "text/plain", ".md": "text/markdown"}
LANGUAGES = json.load(open(os.path.join(WEB_SHELL, "i18n", "languages.json"), encoding="utf-8"))
LAYOUT_IDS = json.load(open(os.path.join(WEB_SHELL, "layouts", "index.json")))
OSK_LABELS = ["space", "Enter", "Hide", "Select", "Copy", "Cut", "Paste", "Select All"]  # the keyboard's own words, translated into the bundle


def _settings_table():
    with _db_lock:
        db().execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db().commit()


def about():
    """The device (Settings > About, Caroline's beroun_about): the software version (config/versions.env), when it was built and the hardware. The simulator has no build of its own:
    its software is the commit it runs, so the build time is that commit's (the device's image writes its own). The time is shown in the device's time zone."""
    fi = FLASH.info()
    version = fi["version"]
    built = None
    try:
        r = subprocess.run(["git", "-C", SIM, "log", "-1", "--format=%ct"], capture_output=True, text=True, timeout=5)
        built = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    if built is None:
        built = int(os.path.getmtime(os.path.abspath(__file__)))
    loc = timectl._local(built, CLOCK.status()["zone"])[0]
    return {"version": version, "build": f"{loc['year']}-{loc['month']:02d}-{loc['day']:02d} {loc['hour']:02d}:{loc['minute']:02d}", "build_epoch": built,
            "hardware": os.environ.get("INTELLICAT_HARDWARE", "Simulator"),
            "board": fi["board"], "slot": fi["slot"].upper(), "image": fi["build"] or None, "trial": fi["trial"]}


def settings_get():
    """System settings (all apps read them; the Settings app and the first-start language screen write them)."""
    _settings_table()
    with _db_lock:
        rows = dict(db().execute("SELECT key, value FROM settings").fetchall())
    out = {k: json.loads(rows[k]) if k in rows else None for k in ("language", "layouts", "dnd", "vibration", "auto_update")}
    out["dnd"] = bool(out["dnd"])  # do-not-disturb: off until switched on
    out["vibration"] = out["vibration"] is not False  # the vibration motor: on until switched off
    out["auto_update"] = out["auto_update"] is not False  # updates from the store install themselves (store_watch): on until switched off
    return out


def settings_set(key, value):
    _settings_table()
    if key == "language":
        if value not in [l["code"] for l in LANGUAGES]:
            raise ValueError("unknown language")
    elif key == "layouts":
        if not isinstance(value, list) or not value or any(v not in LAYOUT_IDS for v in value) or len(set(value)) != len(value):
            raise ValueError("layouts must be a non-empty list of known layouts")
    elif key in ("dnd", "vibration", "auto_update"):
        value = {"true": True, "false": False}.get(value, value) if isinstance(value, str) else value   # Caroline's tool sends text
        if not isinstance(value, bool):
            raise ValueError(f"{ {'dnd': 'do-not-disturb', 'auto_update': 'automatic updates'}.get(key, key)} is on or off")
    else:
        raise ValueError("unknown setting")
    with _db_lock:
        db().execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        db().commit()
    return {"ok": True}


def osk_bundle():
    """osk.js with the enabled layouts and the keyboard's words in the current language, as one script (any page can load it)."""
    s = settings_get()
    lang, ids = s["language"] or "en", s["layouts"] or ["en"]
    layouts = {i: json.load(open(os.path.join(WEB_SHELL, "layouts", i + ".json"), encoding="utf-8")) for i in ids}
    strings = {}
    p = os.path.join(WEB_SHELL, "i18n", lang + ".json")
    if lang != "en" and os.path.isfile(p):
        d = json.load(open(p, encoding="utf-8"))
        strings = {k: d[k] for k in OSK_LABELS if k in d}
    cfg = {"order": ids, "layouts": layouts, "language": lang, "strings": strings}
    return "window.__OSK_CONFIG=" + json.dumps(cfg, ensure_ascii=False) + ";\n" + open(os.path.join(WEB_SHELL, "osk.js"), encoding="utf-8").read()


def exit_app(app_id):
    """An app asks to be closed (e.g. Notepad on B in the file dialog)."""
    if shared["app"] == app_id:  # it has done its saving: the window goes on at once
        shared_finish(app_id)
        return {"ok": True}
    with lock:
        pids = [k for k, v in procs.items() if v["app"] == app_id and v["popen"].poll() is None]
    for pid in pids:
        close(pid)
    return {"ok": True}


def fs_resolve(rel):
    """Map an app-supplied path (relative to the home root) to a real path, refusing anything that leaves the root."""
    full = os.path.realpath(os.path.join(HOME_ROOT, str(rel or "").lstrip("/")))
    if full != HOME_ROOT and not full.startswith(HOME_ROOT + os.sep):
        raise PermissionError("path outside the home directory")
    return full


def fs_list(rel, ext):
    d = fs_resolve(rel)
    entries = []
    for name in os.listdir(d):
        isdir = os.path.isdir(os.path.join(d, name))
        if isdir or not ext or name.lower().endswith("." + ext.lower()):
            entries.append({"name": name, "dir": isdir})
    entries.sort(key=lambda e: (not e["dir"], e["name"].casefold()))
    rel = os.path.relpath(d, HOME_ROOT)
    return {"path": "" if rel == "." else rel.replace(os.sep, "/"), "entries": entries}


def fs_space():
    """Capacity of the card: bytes total, used and free."""
    st = os.statvfs(HOME_ROOT)
    total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    return {"total": total, "free": free, "used": total - st.f_bfree * st.f_frsize}


def fs_read(rel):
    f = fs_resolve(rel)
    if os.path.getsize(f) > MAX_READ:
        raise OverflowError("file is larger than 1 MB")
    return {"text": open(f, encoding="utf-8", errors="replace").read()}


AUDIO_TYPES = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac",
               ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac"}


VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
               ".mov": "video/quicktime", ".ogv": "video/ogg"}


def fs_audio(rel, types=AUDIO_TYPES):
    """(real path, content type) of an audio (or, with VIDEO_TYPES, a video) file of the card, for streaming to a media element."""
    f = fs_resolve(rel)
    ctype = types.get(os.path.splitext(f)[1].lower())
    if not ctype:
        raise PermissionError("not a media file of this kind")
    if not os.path.isfile(f):
        raise FileNotFoundError(f)
    return f, ctype


def fs_write(rel, text, create):
    f = fs_resolve(rel)
    if create and os.path.exists(f):
        raise FileExistsError(rel)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    tmp = f + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, f)  # atomic: a kill in the middle of a write never leaves a half-written file
    return {"ok": True}


def _name_ok(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name and chr(0) not in name


def _inside_root(rel):
    f = fs_resolve(rel)
    if f == HOME_ROOT:
        raise PermissionError("the root cannot be changed")
    return f


def fs_mkdir(rel):
    f = fs_resolve(rel)
    if not _name_ok(os.path.basename(f)):
        raise ValueError("invalid name")
    os.mkdir(f)  # FileExistsError / FileNotFoundError come out as 409 / 404
    return {"ok": True}


def fs_rename(rel, name):
    f = _inside_root(rel)
    if not _name_ok(name):
        raise ValueError("invalid name")
    dst = os.path.join(os.path.dirname(f), name)
    if os.path.lexists(dst):
        raise FileExistsError(name)
    os.rename(f, dst)
    return {"ok": True}


def _target(src_rel, dir_rel):
    src, d = _inside_root(src_rel), fs_resolve(dir_rel)
    if not os.path.isdir(d):
        raise NotADirectoryError(dir_rel)
    if os.path.isdir(src) and (d == src or d.startswith(src + os.sep)):
        raise ValueError("a folder cannot be put into itself")
    dst = os.path.join(d, os.path.basename(src))
    if os.path.lexists(dst):
        raise FileExistsError(os.path.basename(src))
    return src, dst


def fs_copy(src_rel, dir_rel):
    src, dst = _target(src_rel, dir_rel)
    if os.path.isdir(src):
        shutil.copytree(src, dst, copy_function=shutil.copyfile)
    else:
        shutil.copyfile(src, dst)
    return {"ok": True}


def fs_move(src_rel, dir_rel):
    src, dst = _target(src_rel, dir_rel)
    shutil.move(src, dst)
    return {"ok": True}


def fs_delete(rel):
    f = _inside_root(rel)
    if os.path.isdir(f) and not os.path.islink(f):
        shutil.rmtree(f)
    else:
        os.remove(f)
    return {"ok": True}


def open_file(rel):
    """The file manager chose a file: start the app that opens its type. {"error"} (404) when none does."""
    f = fs_resolve(rel)
    if not os.path.isfile(f):
        raise FileNotFoundError(rel)
    ext = os.path.splitext(f)[1].lower().lstrip(".")
    app = next((a for a in APPS if ext and ext in a.get("opens", [])), None)
    if not app:
        raise FileNotFoundError("no app opens ." + ext)
    return {"app": app["id"], "pid": launch(app["id"], {"open": os.path.relpath(f, HOME_ROOT).replace(os.sep, "/")})}


def caroline_apps():
    """The apps Caroline can drive, for her tools (the overlay's ic_apps plugin): the manifest's section, the skill folders as absolute paths."""
    out = []
    for app_id, a in sorted(CAROLINE_APPS.items()):
        c = a["caroline"]
        out.append({"id": app_id, "name": a["name"], "description": c["description"], "folder": a["folder"],
                    "skills": [os.path.join(a["folder"], x) for x in c.get("skills", [])],
                    "mcp": c.get("mcp", []), "tools": c.get("tools", [])})
    return out


def running():
    with lock:
        for pid in [k for k, v in procs.items() if v["popen"].poll() is not None]:
            del procs[pid]
        out = [{"pid": k, "app": v["app"], "name": v["name"]} for k, v in procs.items()]
        if shared["app"]:  # the lowest layer: the shared window (pid: the cog it lives in)
            out.insert(0, {"pid": shell_pid(), "app": shared["app"], "name": shared["name"], "window": "shared"})
        return out


def close(pid):
    if shared["app"] and pid == shell_pid():  # never the launcher's cog itself: a shared frontend is closed by replacing its page
        return shared_finish(shared["app"])
    with lock:
        if pid not in procs:
            raise KeyError(pid)
        popen = procs[pid]["popen"]
    if isinstance(popen, (androidhost.Layer, nativeproc.NativePart)):   # on Android an app's window is a layer of the host (a native part too)
        return popen.terminate()
    os.killpg(os.getpgid(pid), signal.SIGTERM)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _fs(self, fn):
        try:
            return self._json(fn())
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except FileExistsError:
            return self._json({"error": "exists"}, 409)
        except (FileNotFoundError, NotADirectoryError):
            return self._json({"error": "not found"}, 404)
        except OverflowError as e:
            return self._json({"error": str(e)}, 413)
        except (ValueError, OSError) as e:  # a name the file system refuses, no space left, ...
            return self._json({"error": str(e)}, 400)

    def _audio(self, rel, types=AUDIO_TYPES):
        """An audio (or video) file of the card with Range support (the media element seeks by byte ranges)."""
        try:
            f, ctype = fs_audio(rel, types)
            size = os.path.getsize(f)
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError) as e:
            return self._json({"error": str(e)}, 403 if isinstance(e, PermissionError) else 404)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:  # the last N bytes
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):  # the player seeked or stopped: it closes the connection
            pass

    def _ext_call(self, hit, arg):
        """A route of an extension: fn(handler, query | body) answers with an object (errors as in the file API), or a raw one answers itself."""
        _, _, fn, fs = hit
        return self._fs(lambda: fn(self, arg)) if fs else fn(self)

    def _console_only(self, mutating=False, pages=None):
        """The calls that only the host's own window may make (the console's service menu, the tests' controls). The apps of the
        device share this origin and a page of another site could ask too: the page must come from a page an extension trusts
        (the simulator's /console) on this very host, and a change needs a header that a foreign page cannot send without a CORS
        preflight, which is never answered."""
        pages = pages or EXTENSIONS.trusted
        host, ref = self.headers.get("Host", ""), urlparse(self.headers.get("Referer", ""))
        if host not in (f"localhost:{PORT}", f"127.0.0.1:{PORT}") or ref.netloc != host or ref.path not in pages:
            raise PermissionError("only available from the console window")
        if mutating and self.headers.get("X-IntelliCat-Service") != "1":
            raise PermissionError("only available from the console window")

    def _sw(self, fn):
        """A call to SquirrelWisdom: its failure is answered as {"error", "kind"} with a status the page can tell apart."""
        try:
            return self._json(fn())
        except sw.SwError as e:
            code = {"network": 503, "auth": 401, "balance": 402, "config": 500, "exists": 409}.get(e.kind, 502)
            return self._json({"error": str(e), "kind": e.kind}, code)

    def _login(self, body, signup=False, key=False):
        """The login of the device's account (shared with Caroline), or with `signup` a new account and its login. The online simulator asks the service for the welcome gift (sw.welcome: once per account, ever) and tells the user
        with a message box, which waits for the launcher page if it is not up yet."""
        email = sw.key_login(body.get("key")) if key else (sw.signup if signup else sw.login)(body["email"], body["password"])
        out = {"ok": True, "email": email, "caroline": (CAROLINE.ensure(), ASSISTANT.active.ensure())[0]}
        out["wizard"] = wizard_pending()   # decided at once from her file: the launcher shows a wait screen until the wizard takes the window, or goes on at once
        gift = sw.welcome()
        if gift and gift["granted"]:
            MSGBOX.show("Welcome to IntelliCat", f"{gift['amount']:,} piastres have been added to your wallet as a welcome gift.", kind="ok", icon="info", timeout=0, owner="system")
            out["welcome"] = gift
        return out

    def _net(self, fn):
        """Network control: only the Wi-Fi app may look at the radio or change the connection."""
        try:
            return self._json(fn())
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except netctl.NetError as e:
            return self._json({"error": str(e)}, 400)

    def _clock(self, fn):
        """Clock control: everybody may read the time; only Settings and the launcher change it."""
        try:
            return self._json(fn())
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except timectl.ClockError as e:
            return self._json({"error": str(e)}, 400)

    def _notes(self, fn):
        """The notification panel: the launcher and Settings see and change all of it, an app only its own; Caroline has her own paths."""
        try:
            return self._json(fn())
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)

    def _notes_scope(self):
        p = urlparse(self.headers.get("Referer", "")).path
        if p in ("/", "/index.html") or p.startswith("/apps/settings/"):
            return None
        return self._app()

    def _wifi_app(self):
        if self._app() != "wifi":
            raise PermissionError("only the Wi-Fi app can control the network")

    def _browser_open(self, body):
        body = body if isinstance(body, dict) else {}
        try:
            return self._json(browser_open(body.get("url"), str(body.get("label") or "link")))
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except (RuntimeError, KeyError, OSError) as e:
            return self._json({"error": str(e)}, 503)

    def _stt_stop(self):
        try:
            return stt_stop()
        except sw.SwError as e:
            msg = {"network": "No network", "balance": "Insufficient balance", "auth": "Please sign in again",
                   "config": "Speech recognition key is not configured"}.get(e.kind, "Speech recognition failed")
            notify(msg, "System", level="error")
            raise

    def _app(self):
        """The app a request comes from: the app page's URL (Referer) /apps/<id>/... ; services are per app."""
        m = re.match(r"^/(?:apps|system)/([^/]+)/", urlparse(self.headers.get("Referer", "")).path)
        if not m:
            raise PermissionError("request does not come from an app page")
        return m.group(1)

    def _db_owner(self):
        """Whose key/value settings a request reads and writes: an app's, or the launcher's own (where the user has put the apps)."""
        return "launcher" if urlparse(self.headers.get("Referer", "")).path in ("/", "/index.html") else self._app()

    def _settings_writer(self):
        """Only the Settings app and the launcher itself (its first-start language screen) may change the system settings."""
        p = urlparse(self.headers.get("Referer", "")).path
        if not (p in ("/", "/index.html") or p.startswith("/apps/settings/")):
            raise PermissionError("only Settings and the launcher can change the system settings")

    def _rt(self, method, rest, body):
        """Ratatosk's API (runtime/ratatosk.py): only the page of the Ratatosk app may ask (the account and its chats are nobody else's)."""
        try:
            if self._app() != "ratatosk":
                raise PermissionError("only the Ratatosk app can use this")
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        code, out = RATATOSK.handle(method, rest, body)
        return self._json(out, code)

    def _clip_osk(self, method, raw):
        """The keyboard's door to the clipboard (osk.js): any page the keyboard is in, also a site of the device browser, so it answers with CORS and is
        guarded by a recent touch or button of the device (CLIP_OSK_WINDOW_S): a page cannot read or fill the clipboard on its own, only while the user is using the device.
        GET: the clipboard, the host's text first on a simulator; POST (text/plain): the text of Copy / Cut."""
        def out(obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        if time.time() - LAST_INPUT["t"] > CLIP_OSK_WINDOW_S:
            return out({"error": "the clipboard is for the user's own touch or button"}, 403)
        try:
            return out(CLIP.pull(_clip_ask) if method == "GET" else CLIP.set(raw.decode("utf-8"), "osk"))
        except (ValueError, UnicodeDecodeError) as e:
            return out({"error": str(e)}, 400)

    def _sysapi(self, method, path, q, body):
        """The system API (MessageBox, sounds, vibration): an app page or a registered service may use it."""
        try:
            owner = self._app()
        except PermissionError:
            owner = self.headers.get("X-IntelliCat-Service-Id", "")
            if owner not in SERVICES.services:
                return self._json({"error": "only an app page or a service may use the system API"}, 403)
        res = sysapi.handle({"sounds": SYSSOUNDS, "vibra": VIBRA, "msgbox": MSGBOX, "clipboard": CLIP, "clip_ask": _clip_ask, "owner": owner}, method, path, q, body)
        return self._json(*res[::-1]) if res else self._json({"error": "not found"}, 404)

    def _usb(self, body):
        """The USB cable to the PC (the simulator's: a device reads its gadget controller): {connected} from the SERVICE menu of the console window. Not on the hosted simulator."""
        try:
            self._console_only(mutating=True)
            if ONLINE:
                raise PermissionError("not available on the hosted simulator")
            if "connected" in body:
                FLASH.set_usb(bool(body["connected"]))
            return self._json({"connected": FLASH.usb})
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)

    def _hdmi(self, body):
        """The cable and the mode of the external screen (the simulator's: the device reads its connector): from the SERVICE menu of the console window and from the Monitor window,
        which reports its size when it is stretched. {connected, width, height} or {width, height} for a new mode only."""
        try:
            self._console_only(mutating=True, pages=("/console", "/monitor"))
            EXT_WINDOW["open"] = body.get("window") is not False
            w, h = body.get("width"), body.get("height")
            st = EXTD.set_connected(bool(body["connected"]), w, h) if "connected" in body else (EXTD.resize(w, h) if w is not None or h is not None else EXTD.status())
            if body.get("show"):   # a page for the big window by its path (the tests and work on the big screen; the apps' own pages come with the manifest's "external")
                page = str(body["show"])
                if not page.startswith(("/examples/", "/apps/", "/system/")) or ".." in page:
                    raise extdisplay.ExtError("show: a path of /examples/, /apps/ or /system/")
                EXT.show(f"http://127.0.0.1:{PORT}{page}{'&' if '?' in page else '?'}ext=1")   # ext=1: the page knows it is on the big screen
            return self._json(st)
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except extdisplay.ExtError as e:
            return self._json({"error": str(e)}, e.code)
        except extscreen.ScreenError as e:
            return self._json({"error": str(e)}, 500)

    def _page_screen(self):
        """Which page of the app a request comes from: the big page of the external screen (its address has ext=1) or the page of the console."""
        return "big" if re.search(r"(^|&)ext=1(&|$)", urlparse(self.headers.get("Referer", "")).query) else "console"

    def _display(self, sub, body):
        """The external screen for the pages of apps (Platform.display, docs/system-api.md): open / close the app's big page, send a message to the app's other page,
        what the big page was opened with. The focus is the console window's (SELECT does the same inside the system)."""
        try:
            if sub == "focus":
                self._console_only(mutating=True, pages=("/console", "/monitor"))
                return self._json(EXTD.set_focus(body.get("to")))
            app = self._app()
            if sub == "launch":
                if self._page_screen() != "big":
                    raise extdisplay.ExtError("only the big page was opened with something", 400)
                return self._json(ext_launch.pop(app, None) or {"arg": None, "resume": False})
            if sub == "open":
                return self._json(EXTD.open(app, body.get("arg")))
            if sub == "close":
                return self._json(EXTD.close(app))
            if sub == "send":
                return self._json(EXTD.send(app, self._page_screen(), body.get("message")))
            return self._json({"error": "not found"}, 404)
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except extdisplay.ExtError as e:
            return self._json({"error": str(e)}, e.code)
        except extscreen.ScreenError as e:
            return self._json({"error": str(e)}, 500)

    def _hdmi_input(self, body):
        """The Monitor window's own mouse and keyboard (the screen's, as a USB mouse and keyboard on a real monitor would be): {t: "move", x, y} in the pixels of the screen | {t: "button", n, down}
        | {t: "wheel", dx, dy} | {t: "key", key: <a KeyboardEvent.key>, down}. From the Monitor page only; it works with the focus anywhere (it is not the console's buttons)."""
        try:
            self._console_only(mutating=True, pages=("/monitor",))
            x = EXT.xinput
            if not x:
                raise extdisplay.ExtError("there is no external screen", 409)
            t = body.get("t")
            if t == "move":
                x.move(max(0, min(EXTD.width - 1, int(body["x"]))), max(0, min(EXTD.height - 1, int(body["y"]))))
            elif t == "button":
                x.button(int(body["n"]), bool(body["down"]))
            elif t == "wheel":
                x.wheel(int(body.get("dx", 0)), int(body.get("dy", 0)))
            elif t == "key":
                k = str(body["key"])
                sym = KEY_SYMS.get(k) or (ord(k) if len(k) == 1 and ord(k) < 0x100 else 0x1000000 + ord(k) if len(k) == 1 else None)
                if sym is None:
                    return self._json({"ok": False})
                x.key(sym, bool(body["down"]))
            else:
                raise extdisplay.ExtError("t is move, button, wheel or key")
            return self._json({"ok": True})
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except extdisplay.ExtError as e:
            return self._json({"error": str(e)}, e.code)
        except (KeyError, ValueError, TypeError) as e:
            return self._json({"error": f"bad input: {e}"}, 400)

    def _caroline_os(self, method, rest, q, body):
        """The rest of the OS API for Caroline (her tools beroun_files_*, beroun_settings_*, beroun_time_*, beroun_net_*, beroun_sleep, beroun_notify): the same functions the app pages reach by
        /api/fs/*, /api/settings*, /api/time/*, /api/net/*, /api/power/sleep, /api/notify, with her as the owner (no app page to ask for, no Settings or Wi-Fi app to be)."""
        area, _, op = rest.partition("/")
        key = (method, op)
        if area == "files":
            fn = {("GET", "list"): lambda: fs_list(q.get("path", ""), q.get("ext", "")), ("GET", "read"): lambda: fs_read(q.get("path", "")), ("GET", "space"): fs_space,
                  ("POST", "write"): lambda: fs_write(body["path"], body["text"], bool(body.get("create"))), ("POST", "mkdir"): lambda: fs_mkdir(body["path"]),
                  ("POST", "rename"): lambda: fs_rename(body["path"], body["name"]), ("POST", "copy"): lambda: fs_copy(body["path"], body["to"]),
                  ("POST", "move"): lambda: fs_move(body["path"], body["to"]), ("POST", "delete"): lambda: fs_delete(body["path"])}.get(key)
            return self._fs(fn) if fn else self._json({"error": "not found"}, 404)
        if area == "settings":
            if key == ("GET", "get"):
                return self._json(settings_get())
            if key == ("POST", "set"):
                return self._fs(lambda: settings_set(body["key"], body["value"]))
        if area == "time":
            fn = {("GET", "status"): CLOCK.status, ("GET", "zones"): lambda: {"zones": CLOCK.zones()},
                  ("POST", "set"): lambda: (CLOCK.set_time(body["year"], body["month"], body["day"], body["hour"], body["minute"]), CLOCK.status())[1],
                  ("POST", "zone"): lambda: (CLOCK.set_zone(body["zone"]), CLOCK.status())[1], ("POST", "ntp"): lambda: (CLOCK.set_ntp(body["on"]), CLOCK.status())[1]}.get(key)
            return self._clock(fn) if fn else self._json({"error": "not found"}, 404)
        if area == "net":
            fn = {("GET", "status"): NET.status, ("GET", "scan"): lambda: {"networks": NET.scan()},
                  ("POST", "connect"): lambda: (NET.connect(body["ssid"], body.get("password")), {"ok": True})[1]}.get(key)
            return self._net(fn) if fn else self._json({"error": "not found"}, 404)
        if area == "power":
            if key == ("GET", "status"):
                return self._json({"asleep": asleep(), "volume": volume_get()})
            if key == ("POST", "sleep"):
                return self._json(sleep_toggle(body.get("state")))
        if area == "about" and method == "GET":
            return self._json(about())
        if area == "browser" and key == ("POST", "open"):   # a page in the device browser (beroun_browser_open)
            return self._browser_open(body)
        if area == "update":   # the assistant's own updates (Caroline: runtime/caroline_update.py): the state, a look for a release now, and the owner's yes
            a = ASSISTANT.active
            if key == ("GET", "status"):
                return self._json(a.update_status())
            if key == ("POST", "check"):
                return self._json(a.update_check())
            if key == ("POST", "now"):   # only on the owner's word: the assistant says what it does first
                return self._json(a.update_now())
        if area == "assistant":   # the assistant of this build (her tool beroun_assistants): the providers and which one is in the slot (chosen by the build, never changed here)
            if key == ("GET", "list"):
                return self._json(ASSISTANT.describe())
        if area == "services" and key == ("GET", "status"):
            return self._json(SERVICES.status())
        if area == "notify" and method == "POST":
            return self._json({"id": notify(body["text"], ASSISTANT.active.id, body.get("level", "info"), bool(body.get("sound")), bool(body.get("vibrate")))})
        return self._json({"error": "not found"}, 404)

    def _caroline_sys(self, method, rest, q, body):
        """Caroline's way to the system API (her tools beroun_message_box, beroun_play_sound, beroun_vibrate, ...): the same calls with her as the owner,
        and the switch of the vibration setting (the Settings app's, here hers by the owner's word)."""
        if rest.split("/", 1)[0] in ("files", "settings", "time", "net", "power", "services", "notify", "about", "update", "assistant"):
            return self._caroline_os(method, rest, q, body)
        if rest.split("/", 1)[0] == "store":   # the apps of the store: her tools beroun_store_*
            return self._store(method, rest[len("store/"):], q, body, owner="assistant")
        if rest.startswith("display/"):   # the external screen: her tools beroun_display_*; she may open the big page of any app that has one
            try:
                sub = rest[len("display/"):]
                if sub == "status" and method == "GET":
                    return self._json(EXTD.status())
                if method != "POST":
                    return self._json({"error": "not found"}, 404)
                if sub == "open":
                    return self._json(EXTD.open(str(body.get("app", "")), body.get("arg")))
                if sub == "close":
                    return self._json(EXTD.close(str(body.get("app", ""))))
                if sub == "focus":
                    return self._json(EXTD.set_focus(body.get("to")))
                return self._json({"error": "not found"}, 404)
            except extdisplay.ExtError as e:
                return self._json({"error": str(e)}, e.code)
            except extscreen.ScreenError as e:
                return self._json({"error": str(e)}, 500)
        if rest == "vibra/enable" and method == "POST":
            return self._fs(lambda: settings_set("vibration", bool(body.get("on"))))
        res = sysapi.handle({"sounds": SYSSOUNDS, "vibra": VIBRA, "msgbox": MSGBOX, "clipboard": CLIP, "clip_ask": _clip_ask, "owner": "assistant"}, method, "/api/" + rest, q, body)
        return self._json(*res[::-1]) if res else self._json({"error": "not found"}, 404)

    def _store(self, method, rest, q, body, owner=None):
        """/api/store/*: the apps of the store. Only the Store app's page (it declares "apps.manage"; no package may) and Caroline (by the owner's word,
        /api/caroline/sys/store/*, her tools beroun_store_*) may call it. GET installed | catalog?q= | updates; POST install {app} | remove {app}."""
        if owner != "assistant":
            try:
                who = self._app()
            except PermissionError:
                who = None
            if who != "store" or "apps.manage" not in APP_PERMISSIONS.get("store", ()):
                return self._json({"error": "only the Store app (and Caroline) may manage the apps"}, 403)
        codes = {"offline": 502, "not-found": 404, "not-installed": 404, "system": 403, "conflict": 409, "not-higher": 409, "missing-capability": 409, "no-key": 503, "bad-signature": 422}
        app_id = str(body.get("app", "")) if isinstance(body, dict) else ""
        try:
            if method == "GET" and rest == "installed":
                return self._json(STORE.installed())
            if method == "GET" and rest == "catalog":
                return self._json(store_catalog(q.get("q", "")))
            if method == "GET" and rest == "updates":
                return self._json(store_updates())
            if method == "POST" and rest in ("install", "remove"):
                if not re.fullmatch(r"[a-z][a-z0-9-]*", app_id):
                    return self._json({"error": "app: the id of an app"}, 400)
                return self._json(store_install_from_catalog(app_id) if rest == "install" else store_remove(app_id))
        except appinstall.InstallError as e:
            return self._json({"error": str(e), "code": e.code}, codes.get(e.code, 400))
        return self._json({"error": "not found"}, 404)

    def _app_endpoint(self, method, raw=b""):
        """/api/app/<id>/<path>: passed on to the port of the service the manifest's "endpoints" name. Callers: the app's own page (Referer) or its MCP server (X-IntelliCat-Mcp)."""
        import urllib.request, urllib.error
        url = urlparse(self.path)
        m = re.fullmatch(r"/api/app/([a-z][a-z0-9-]*)/(.+)", url.path)
        if not m:
            return self._json({"error": "not found"}, 404)
        app_id, rest = m.groups()
        ep = next((d for d in ENDPOINTS.get(app_id, []) if rest == d["path"] or rest.startswith(d["path"] + "/")), None)
        if not ep or method not in ep.get("methods", ["GET"]):
            return self._json({"error": "no such endpoint"}, 404)
        try:
            from_page = self._app() == app_id
        except PermissionError:
            from_page = False
        access = ep.get("access", ["page", "mcp"])
        try:
            other_page = f"page:{self._app()}" in access   # the page of another app that the manifest names
        except PermissionError:
            other_page = False
        if not (((from_page or other_page) and ("page" in access or other_page)) or (self.headers.get("X-IntelliCat-Mcp") == app_id and "mcp" in access)):
            return self._json({"error": "this endpoint is not for you"}, 403)
        svc = SERVICES.services.get(ep["service"])
        if not svc or not svc.port:
            return self._json({"error": "the service has no port"}, 502)
        if svc.stopped_for != "manual":
            try:
                SERVICES.ensure(svc.id)
            except Exception as e:
                return self._json({"error": str(e)}, 502)
        req = urllib.request.Request(f"http://127.0.0.1:{svc.port}/{rest}" + ("?" + url.query if url.query else ""), data=raw if method == "POST" else None,
                                     method=method, headers={"Content-Type": "application/json"})
        for _ in range(20):  # a service just started needs a moment to listen
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return self._json(json.loads(r.read() or b"{}"), r.status)
            except urllib.error.HTTPError as e:
                return self._json(json.loads(e.read() or b"{}"), e.code)
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.25)
        return self._json({"error": "the service does not answer"}, 502)

    def _agent_sw(self, body):
        """POST /api/agents/sw {command, params}: an agent's call of the owner's Camerlengo service (docs/architecture.md). The agent is named in X-IntelliCat-Agent (<app>/<id>) by its
        service, or by its app's page (which may leave it out when the app has one agent, and may use no other app's agent). Only what the manifest's `camerlengo` lists goes through;
        the session and the key are the shell's and are never given out."""
        key = self.headers.get("X-IntelliCat-Agent", "")
        try:
            page = self._app()
        except PermissionError:
            page = None
        if page:
            mine = [a["key"] for a in AGENTS.list_agents() if a["app"] == page]
            key = key or (mine[0] if len(mine) == 1 else "")
            if key not in mine:
                return self._json({"error": "a page may use only the agents of its own app (name one in X-IntelliCat-Agent)", "kind": "forbidden"}, 403)
        if not key:
            return self._json({"error": "X-IntelliCat-Agent is missing", "kind": "bad_request"}, 400)
        try:
            return self._sw(lambda: AGENTS.sw_call(key, body.get("command"), body.get("params") or {}, sw.command))
        except agents.AgentError as e:
            return self._json({"error": str(e), "kind": e.kind}, {"not_found": 404, "bad_request": 400, "conflict": 409, "forbidden": 403}[e.kind])

    def _account_session(self, fresh):
        """GET /api/account/session[?fresh=1]: the device's SquirrelWisdom session, for a cloud service that checks it (the cloud Minecraft's orchestrator).
        Who: only a SERVICE of an app whose manifest lists "account.session" (never a page: what a page holds, a page of another origin can be led to show).
        fresh=1 mints a new one (the cloud said the old one expired)."""
        sid = self.headers.get("X-IntelliCat-Service-Id", "")
        app = SERVICES.services[sid].app if sid in SERVICES.services else ""
        if not app or "account.session" not in APP_PERMISSIONS.get(app, ()):
            return self._json({"error": "only a service of an app whose manifest lists account.session", "kind": "forbidden"}, 403)
        return self._sw(lambda: {"session": sw.session(force=fresh), "email": (sw.load_credentials() or {}).get("email")})

    def _caroline_ask(self, body):
        """POST /api/caroline/ask {text, timeout?}: an app asks Caroline, she thinks, the answer goes back to the app only (runtime/caroline_ask.py: a tab of its own, never her chat window).
        Who: the page of an app, or a service of an app, whose manifest lists the permission "caroline.ask"."""
        try:
            app = self._app()
        except PermissionError:
            sid = self.headers.get("X-IntelliCat-Service-Id", "")
            app = SERVICES.services[sid].app if sid in SERVICES.services else ""
        if not app or not {"assistant.ask", "caroline.ask"} & APP_PERMISSIONS.get(app, set()):
            return self._json({"error": "the manifest of this app does not list the permission assistant.ask (or its old name caroline.ask)", "kind": "forbidden"}, 403)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "text is required", "kind": "bad_request"}, 400)
        timeout = min(max(float(body.get("timeout") or 120), 5.0), 600.0)
        a = ASSISTANT.active
        ilog(f"assistant: the app {app} asks {a.id} ({len(text)} characters)")
        try:
            return self._json({"app": app, "text": a.ask(app, text, timeout), "assistant": a.id})
        except assistant.Unavailable as e:
            return self._json({"error": str(e), "kind": "unavailable"}, 503)
        except assistant.Timeout as e:
            return self._json({"error": str(e), "kind": "timeout"}, 504)
        except assistant.Failed as e:
            return self._json({"error": str(e), "kind": "failed"}, 502)

    def _agents(self, method, rest, q, body):
        """/api/agents/...: the apps' agents (runtime/agents.py). Caroline's backend gives the tasks; the pages of apps may not command agents, and only the
        Settings app may look (the owner's read-only view)."""
        if method == "POST" and rest == "sw":
            return self._agent_sw(body)
        if method == "GET" and rest == "":   # the REGISTRY: who the agents are and what each can do; open to every agent and app of the device
            return self._json(agents_registry(q))
        try:
            page = self._app()
        except PermissionError:
            page = None
        # The tasks are open to the agents installed with apps (the page of an app that publishes agents): they call each other through them, after finding each other in the registry.
        # A task they start carries who asked (source "app:<id>"), so the journal says; they see, answer and cancel only their own tasks.
        by_agent = page if page and page in AGENT_SPECS and (rest == "tasks" or rest.startswith("tasks/")) else None
        if page and not by_agent and (method != "GET" or page != "settings"):
            return self._json({"error": "the agents are for Caroline"}, 403)
        source = f"app:{by_agent}" if by_agent else None
        try:
            m = re.fullmatch(r"tasks/([0-9a-f]+)(?:/(answer|cancel))?", rest)
            if by_agent and m and AGENTS.get_local(m.group(1)).get("source") != source:
                return self._json({"error": "this task is not yours"}, 403)
            if method == "GET" and rest == "summary":   # the owner's view in Settings: what each agent declares, its tasks, its calls of Camerlengo and what they cost
                return self._json(AGENTS.summary())
            if method == "GET" and rest == "tasks":
                return self._json([t for t in AGENTS.tasks_list(q.get("agent"), q.get("state")) if not by_agent or t.get("source") == source])
            if method == "GET" and rest == "journal":
                return self._json(AGENTS.journal(int(q.get("limit") or 100)))
            if method == "POST" and rest == "tasks":
                return self._json(AGENTS.start(body.get("agent", ""), body.get("skill", ""), body.get("input") or {}, body.get("chain_id"), source), 201)
            if m and method == "GET" and not m.group(2):
                return self._json(AGENTS.get(m.group(1)))
            if m and method == "POST" and m.group(2) == "answer":
                return self._json(AGENTS.answer(m.group(1), body.get("answer", "")))
            if m and method == "POST" and m.group(2) == "cancel":
                return self._json(AGENTS.cancel(m.group(1)))
        except agents.AgentError as e:
            return self._json({"error": str(e), "kind": e.kind}, {"not_found": 404, "bad_request": 400, "conflict": 409}[e.kind])
        return self._json({"error": "not found"}, 404)

    def _alias(self):
        """/api/assistant/* is the name of the assistant's API (docs/assistant-contract.md); /api/caroline/*, its old name, stays for ever: both are served by the same handlers."""
        if self.path.startswith("/api/assistant/"):
            self.path = "/api/caroline/" + self.path[len("/api/assistant/"):]

    def do_GET(self):
        self._alias()
        url = urlparse(self.path)
        if url.path.startswith("/api/app/"):
            return self._app_endpoint("GET")
        path, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        if path == "/api/agents" or path.startswith("/api/agents/"):
            return self._agents("GET", path[len("/api/agents/"):], q, {})
        if path == "/api/clipboard/osk":
            return self._clip_osk("GET", b"")
        if path.startswith(SYSAPI_PREFIXES):
            return self._sysapi("GET", path, q, {})
        if path.startswith("/api/store/"):
            return self._store("GET", path[len("/api/store/"):], q, {})
        if path.startswith("/api/caroline/sys/"):
            return self._caroline_sys("GET", path[len("/api/caroline/sys/"):], q, {})
        if path in ("/api/rt/call-video", "/api/rt/call-preview"):  # the pictures of a video call (a motion JPEG for an <img>): the page of the Ratatosk app only
            try:
                if self._app() != "ratatosk":
                    raise PermissionError("only the Ratatosk app can use this")
            except PermissionError as e:
                return self._json({"error": str(e)}, 403)
            tile = q.get("t", "0")                                   # a group call has up to four pictures: ?t=0..3
            return RATATOSK.calls.serve_frames(self, ("remote" if tile in ("", "0") else f"remote-{tile}") if path.endswith("video") else "local")
        if path == "/api/rt/attachment":  # a picture of a message, decrypted in memory for an <img>: the page of the Ratatosk app only
            try:
                if self._app() != "ratatosk":
                    raise PermissionError("only the Ratatosk app can use this")
                gid = RATATOSK.chat_info(q.get("id", ""))["id"]
            except PermissionError as e:
                return self._json({"error": str(e)}, 403)
            except ratatosk.RatatoskError as e:
                return self._json({"error": str(e)}, 404)
            return RATATOSK.attachments.serve(self, gid, q.get("msg", ""), q.get("i", "0"), q.get("kind", "file"))
        if path.startswith("/api/rt/"):
            return self._rt("GET", path[len("/api/rt/"):] + ("?" + url.query if url.query else ""), {})
        if path.startswith("/api/msg/"):
            return self.msg_proxy("GET", None)
        if path == "/api/about":   # the software version, the build, the hardware (Settings > About): anyone may ask
            return self._json(about())
        if path == "/api/settings":
            return self._json(settings_get())
        if path == "/osk-bundle.js":
            data = osk_bundle().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/caroline/status":   # the active assistant's backend (/api/assistant/status)
            a = ASSISTANT.active
            return self._json(dict(a.status(), assistant=a.id, name=a.name))
        if path == "/api/caroline/providers":   # the assistant slot: the providers, the active one (/api/assistant/providers)
            return self._json(ASSISTANT.describe())
        if path == "/api/net/status":  # anyone may ask whether there is a network
            return self._json(NET.status())
        if path == "/api/notifications/list":
            return self._notes(lambda: notes_list(self._notes_scope()))
        if path == "/api/caroline/notifications":
            return self._json(notes_list())
        if path == "/api/time/status":
            return self._clock(CLOCK.status)
        if path == "/api/time/zones":
            return self._clock(lambda: {"zones": CLOCK.zones()})
        if path == "/api/net/scan":
            return self._net(lambda: (self._wifi_app(), {"networks": NET.scan()})[1])
        if path == "/api/account/session":
            return self._account_session(q.get("fresh") == "1")
        if path == "/api/wizard/pending":   # the launcher's wait screen after a login: is her wizard still to come, and how long she has been starting
            st = ASSISTANT.active.status()
            return self._json({"pending": wizard_pending(), "running": st["running"], "uptime_s": st.get("uptime_s", 0),
                               "online": os.environ.get("INTELLICAT_ONLINE") == "1", "assistant": {"id": ASSISTANT.active.id, "name": ASSISTANT.active.name}})
        if path == "/api/auth/status":
            creds = sw.load_credentials()
            return self._json({"logged_in": creds is not None, "email": creds["email"] if creds else None})
        if path == "/api/db/get":
            return self._fs(lambda: db_get(self._db_owner(), q.get("key", "")))
        if path == "/api/fs/list":
            return self._fs(lambda: fs_list(q.get("path", ""), q.get("ext", "")))
        if path == "/api/fs/space":
            return self._fs(fs_space)
        if path == "/api/fs/read":
            return self._fs(lambda: fs_read(q.get("path", "")))
        if path == "/api/fs/audio":
            return self._audio(q.get("path", ""))
        if path == "/api/fs/video":
            return self._audio(q.get("path", ""), VIDEO_TYPES)
        hit = EXTENSIONS.find("GET", path)
        if hit:
            return self._ext_call(hit, q)
        if path == "/api/power":
            return self._json({"asleep": asleep(), "volume": volume_get()})
        if path == "/api/caroline/apps":  # the apps Caroline can drive: description, skills, MCP servers, tools of the page
            return self._json(caroline_apps())
        if path == "/api/device":   # Platform.device(): where it runs
            return self._json(device_info())
        if path == "/api/capabilities":   # Platform.capabilities(): what the calling app declared and this device has
            try:
                caps = app_capabilities(self._app())
            except PermissionError:   # not an app page (Caroline): everything the device has
                caps = sorted(DEVICE_CAPABILITIES)
            return self._json({"capabilities": caps})
        if path == "/api/services":  # the service manager: every service with its state, memory, priority; the total of the device against the limit
            return self._json(SERVICES.status())
        if path == "/api/face/status":  # Visual Mode: is there a model and a program, and is the face on the screen
            return self._json(FACE.status())
        if path == "/api/face/job":  # an answer being played: state playing | done | failed
            j = FACE.job(q.get("id", ""))
            return self._json(j) if j else self._json({"error": "no such answer"}, 404)
        if path == "/api/launch-arg":  # what this app was started with, once
            return self._fs(lambda: {"arg": launch_args.pop(self._app(), None)})
        if path == "/oskws":   # a page of the external screen: the focus of its fields (osk.js), what the console's keyboard types into them
            return self.osk_websocket()
        if path == "/appws":
            return self.app_websocket(q.get("id", ""), q.get("screen", "console"))
        # hidden apps (test/demo) are not listed but stay launchable by id
        routes = {"/api/apps": lambda: [dict(a, badge=BADGES[a["id"]]) if BADGES.get(a["id"]) else a for a in APPS if not a.get("hidden") and not assistant_view_hidden(a["id"])],
                  "/api/running": running, "/api/hw": HW.state,
                  "/api/buttons": lambda: BUTTONS, "/api/flash": FLASH.status, "/api/usb": lambda: {"connected": FLASH.usb}, "/api/health": lambda: {"ok": True, "socket": SOCKET, "consoles": CONSOLES["n"]}}
        if path in routes:
            return self._json(routes[path]())
        if path == "/api/notifications":
            since = int((self.path.split("since=")[1:] or ["0"])[0].split("&")[0] or 0)
            return self._json(notifications(since))
        if path == "/api/display/launch":
            return self._display("launch", {})
        if path == "/api/display":
            if urlparse(self.headers.get("Referer", "")).path == "/monitor":
                MONITOR_SEEN["t"] = time.time()
            return self._json(dict(EXTD.status(), monitor=time.time() - MONITOR_SEEN["t"] < 4))   # monitor: a Monitor window is showing the screen (the console offers one when not)
        if path == "/ext.mjpg":
            if not EXT.serve(self):
                return self._json({"error": "no external screen"}, 404)
            return
        if path == "/stream.mjpg":
            return STREAM.serve(self) if STREAM else self._json({"error": "console mode not running"}, 404)
        if path == "/ws":
            return self.websocket()
        if path == "/audio":   # the sound both ways for a console page on another computer (the hosted simulator: runtime/webaudio.py)
            return webaudio.Session(self, _audio_env(), ilog).run()
        if path == "/api/audio":
            return self._json(webaudio.status(_audio_env()))
        if self.server.server_port == CAROLINE_PAGE_PORT and (path == "/wizard" or path.startswith("/api/wizard/")):
            return self.wizard_proxy("GET", None)
        if self.server.server_port == CAROLINE_PAGE_PORT:  # her page origin: her files first, then ours
            rel = path.lstrip("/") or "chat.html"
            if os.path.isfile(os.path.join(CAROLINE_WWWROOT, rel)) or rel.split("/", 1)[0] in ("fonts", "icons"):
                return self.caroline_page(rel)
        rel = "index.html" if path == "/" else path.lstrip("/")
        ext_file = EXTENSIONS.resolve_static(path)
        if ext_file:   # a page or a folder of an extension
            base, rel = ext_file
        elif rel.startswith("examples/"):
            base = os.path.join(SIM, "examples"); rel = rel[len("examples/"):]
        elif rel.startswith("apps/"):
            base = os.path.join(SIM, "apps"); rel = rel[len("apps/"):]
            if rel.split("/", 1)[0] in INSTALLED_IDS:   # an app of the store: its own folder (never a dot-folder of an install in progress)
                base = INSTALLED_ROOT
        elif rel.startswith("system/"):  # the firmware's own views (Caroline)
            base = FW_SYSTEM; rel = rel[len("system/"):]
        else:
            base = os.path.join(SIM, "web-shell")
        full = os.path.normpath(os.path.join(base, rel))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ctype = STATIC_TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        size = os.path.getsize(full)
        self.send_response(200)
        # a charset belongs to text only: "application/wasm; charset=..." is not a type WebAssembly.instantiateStreaming accepts everywhere
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(size))
        # the shell's own pages are small and local: always revalidate; Babylon.js (/lib/babylon/, megabytes) changes only with the OS, so it is cached
        self.send_header("Cache-Control", "public, max-age=86400" if rel.startswith("lib/babylon/") and base.endswith("web-shell") else "no-cache")
        self.end_headers()
        try:
            with open(full, "rb") as f:   # in blocks: the libraries and models are megabytes
                while True:
                    block = f.read(256 * 1024)
                    if not block:
                        break
                    self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):   # the page went away (a closed app) in the middle of a file
            pass

    def caroline_page(self, rel):
        """Caroline's wwwroot at /caroline/. chat.html gets the host shim and the keyboard injected at serve time."""
        base = os.path.realpath(CAROLINE_WWWROOT)
        if rel.split("/", 1)[0] in ("fonts", "icons"):  # Selawik and the drawn icons her Linux shell bundles
            base = os.path.realpath(os.path.join(CAROLINE_SHELL_DIR))
        full = os.path.realpath(os.path.join(base, rel or "chat.html"))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        data = open(full, "rb").read()
        if os.path.basename(full) == "chat.html":
            css, glyphs = caroline_assets()
            data = data.replace(b"<head>", b'<head><style>' + css.encode() + b'</style><script src="/caroline-shim.js"></script>'
                                b'<script src="/osk-bundle.js"></script><script>OSK.attach()</script><script src="/focusframe.js"></script><script>' + glyphs.encode() + b'</script>', 1)
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json",
                 ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".woff2": "font/woff2",
                 ".ttf": "font/ttf"}.get(os.path.splitext(full)[1], "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/json")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def osk_websocket(self):
        """The channel of a page of the external screen for the keyboard (see osk_show): {"t": "focus", "type"} / {"t": "blur"} from it, {"t": "edit", "e"} to it."""
        display.ws_handshake(self)
        try:
            for text in display.ws_messages(self):
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if not EXTD.status()["connected"]:
                    continue
                if msg.get("t") == "focus":
                    osk_show(self, str(msg.get("type") or ""))
                elif msg.get("t") == "blur" and ext_osk["field"] is self:
                    osk_hide()
        finally:
            if ext_osk["field"] is self:
                osk_hide()

    def app_websocket(self, app_id, screen="console"):
        """Channel between an app page (platform.js) and the shell: the shell asks it to close, it answers "closed". The big page of an app (the external screen,
        ?screen=big) is on the same channel; it answers "suspended" when it was asked to save (covered, closed, unplugged), never "closed"."""
        display.ws_handshake(self)
        self.screen = "big" if screen == "big" else "console"
        with lock:
            app_conns.setdefault(app_id, []).append(self)
        ilog(f"appws: {app_id} connected" + (" (big page)" if self.screen == "big" else ""))
        if self.screen == "console":
            MSGBOX.deliver()  # a message box that waits for a page is shown by this one
        try:
            for text in display.ws_messages(self):
                try:
                    msg = json.loads(text)
                    if msg.get("t") == "closed" and self.screen == "console":
                        app_closed(app_id)
                    elif msg.get("t") == "suspended":   # the big page has saved
                        ev = ext_suspends.get(app_id)
                        if ev and self.screen == "big":
                            ev.set()
                    elif msg.get("t") == "msgbox_result":  # the page's answer to a MessageBox: the button's index, -1 for B
                        MSGBOX.answer(str(msg.get("id", "")), msg.get("index"))
                    elif msg.get("t") == "osk_edit" and self in ext_osk["page"]:   # a key of the console's keyboard for the field of the external screen
                        f = ext_osk["field"]
                        if f:
                            _push([f], {"t": "edit", "e": msg.get("e") or {}})
                        if (msg.get("e") or {}).get("type") == "enter":
                            osk_hide(tell_page=False)
                    elif msg.get("t") == "osk_closed" and self in ext_osk["page"]:   # put away on the console (Hide, Enter)
                        osk_hide(tell_page=False)
                    elif msg.get("t") == "tool_result":  # the page's answer to a tool call of Caroline's
                        w = app_tool_waits.get(msg.get("id"))
                        if w:
                            w["answer"] = {k: msg[k] for k in ("text", "result", "is_error") if k in msg}
                            w["event"].set()
                except ValueError:
                    pass
        finally:
            with lock:
                app_conns[app_id].remove(self)
                pids = [k for k, v in procs.items() if v["app"] == app_id and k in closing]
            ilog(f"appws: {app_id} disconnected")
            if self.screen == "console":   # (a page of the external screen going away closes nothing of the app)
                if shared["app"] == app_id and shared["closing"]:  # its page went away while it was asked to close
                    shared_finish(app_id)
                for pid in pids:  # page went away while closing
                    try:
                        close(pid)
                    except (KeyError, ProcessLookupError):
                        pass

    def websocket(self):
        """Low-latency input channel of the console window (pointer, touch, buttons, sticks)."""
        display.ws_handshake(self)
        ilog("ws: console connected")
        with lock:
            CONSOLES["n"] += 1
            CONSOLES["conns"].append(self)
        if OSK_TYPING["on"]:
            _push([self], {"t": "typing", "on": True})
        last_down = None
        for text in display.ws_messages(self):
            try:
                m = json.loads(text)
                t = m["t"]
                if t != "ptr" or m["down"] != last_down:  # pointer moves are only logged when the touch state changes
                    ilog(f"ws: {text}")
                if t == "ptr":
                    last_down = m["down"]
                if t == "ptr" and asleep():
                    pass  # asleep: the screen does not hear touches
                elif t == "ptr":  # pointer/touch on the screen, coordinates in 640x480 pixels
                    LAST_INPUT["t"] = time.time()
                    if XIN:
                        XIN.pointer(m["x"], m["y"], m["down"])
                    HW.touch(m["x"], m["y"], m["down"])
                elif t == "btn":
                    if not asleep() and not (osk_up() and m["name"] in ("up", "down", "left", "right", "a", "b")) and EXTIN.route(m["name"], m["on"]):   # SELECT moves the focus; with it on the external screen the cursor, A, B, X, Y are for that screen
                        LAST_INPUT["t"] = time.time()
                        continue
                    hw_button(m["name"], m["on"])
                    if m["name"] == "mode" and m["on"] and not asleep():
                        home()
                    if XIN and not asleep() and m["name"] not in SYSTEM_BUTTONS:  # the volume, power and Caroline buttons never reach an app
                        XIN.key(m["name"], m["on"])
                elif t == "stick" and not asleep():
                    ext_stick_log(m["which"], m["x"], m["y"])
                    HW.stick(m["which"], m["x"], m["y"])
                    EXTIN.stick(m["which"], m["x"], m["y"])   # the left stick is the external screen's mouse, the right one its wheel
                elif t == "clip":   # the console read (or was asked to read) the host's clipboard; text null: it could not
                    CLIP.host_answer(m.get("text"))
                elif t == "key" and XIN and not asleep():   # typed on the PC (the console window in KEYS: TYPING): a character is its keysym, never a button name ("a" is not A)
                    LAST_INPUT["t"] = time.time()
                    n = m["name"]
                    XIN.key(ord(n) if len(n) == 1 and ord(n) < 0x100 else 0x1000000 + ord(n) if len(n) == 1 else n, m["on"])
            except Exception as e:  # a bad message or missing device must not drop the channel
                print("ws input error:", repr(e), flush=True)
                ilog(f"ws: ERROR {e!r}")
        ilog("ws: console disconnected")
        with lock:
            CONSOLES["n"] -= 1
            if self in CONSOLES["conns"]:
                CONSOLES["conns"].remove(self)

    def wizard_proxy(self, method, raw):
        """Caroline's start wizard (served by her backend) through the page origin, so the keyboard can be injected into it
        (a page of another origin cannot be reached from the layer). The wizard only talks to /api/wizard/*."""
        req = urllib.request.Request(f"http://127.0.0.1:{caroline.PORT}{self.path}", data=raw, method=method,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/json")} if raw else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data, code, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, code, ctype = e.read(), e.code, e.headers.get("Content-Type", "application/json")
        except OSError:
            return self._json({"error": "Caroline is not running"}, 503)
        if self.path == "/wizard":
            data = data.replace(b"<head>", b'<head><script src="/osk-bundle.js"></script><script>OSK.attach()</script>', 1)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def msg_proxy(self, method, raw):
        """Communicator's API (the overlay's ic_messaging, mounted on Caroline's backend: /api/ic/messages/*) through the page origin.
        The app, her tools and the shell all use the same accounts, so there is nothing to translate: the path is only re-rooted."""
        target = int(os.environ.get("COMMUNICATOR_BACKEND_PORT") or caroline.PORT)
        req = urllib.request.Request(f"http://127.0.0.1:{target}/api/ic/messages/{self.path[len('/api/msg/'):]}", data=raw, method=method,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/json")} if raw else {})
        try:
            with urllib.request.urlopen(req, timeout=70) as r:   # a long poll waits up to 55 s
                data, code, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, code, ctype = e.read(), e.code, e.headers.get("Content-Type", "application/json")
        except OSError:
            return self._json({"error": "the messaging backend is not running", "code": "unavailable"}, 503)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        self._alias()
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.path.startswith("/api/msg/"):
            return self.msg_proxy("PUT", raw)
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        self._alias()
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.server.server_port == CAROLINE_PAGE_PORT and self.path.startswith("/api/wizard/"):
            return self.wizard_proxy("POST", raw)
        if self.path.startswith("/api/app/"):
            return self._app_endpoint("POST", raw)
        if self.path.startswith("/api/msg/"):
            return self.msg_proxy("POST", raw)
        if self.path == "/api/clipboard/osk":
            return self._clip_osk("POST", raw)
        if self.path.startswith("/api/rt/"):
            try:
                rt_body = json.loads(raw or b"{}")
            except ValueError:
                return self._json({"error": "the body is not JSON"}, 400)
            return self._rt("POST", self.path[len("/api/rt/"):], rt_body if isinstance(rt_body, dict) else {})
        body = json.loads(raw or b"{}")
        if self.path == "/api/caroline/ask":
            return self._caroline_ask(body if isinstance(body, dict) else {})
        if self.path == "/api/caroline/remind":   # Caroline's scheduler (ic_bridge): a reminder of the assistant in the slot is due ({tab, text}); never an app's page
            try:
                return self._json({"error": f"the app {self._app()} may not do this", "kind": "forbidden"}, 403)
            except PermissionError:
                pass
            if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"].strip():
                return self._json({"error": "text is required"}, 400)
            return self._json({"delivered": assistant_remind(body.get("tab"), body["text"])})
        if self.path.startswith("/api/store/"):
            return self._store("POST", self.path.split("?")[0][len("/api/store/"):], {}, body if isinstance(body, dict) else {})
        if self.path.startswith("/api/agents/"):
            return self._agents("POST", self.path.split("?")[0][len("/api/agents/"):], {}, body if isinstance(body, dict) else {})
        if self.path.startswith(SYSAPI_PREFIXES):
            return self._sysapi("POST", self.path.split("?")[0], {}, body)
        if self.path == "/api/usb":
            return self._usb(body if isinstance(body, dict) else {})
        if self.path == "/api/flash/dismiss":   # the recovery screen's B: a failed flash (or a rollback notice) is acknowledged
            return self._json(FLASH.dismiss())
        if self.path == "/api/boot/confirm":   # the launcher is drawn and has stayed up: a system on trial is the system now (flashd.py)
            return self._json({"confirmed": FLASH.confirm()})
        if self.path == "/api/hdmi":
            return self._hdmi(body)
        if self.path == "/api/hdmi/input":
            return self._hdmi_input(body if isinstance(body, dict) else {})
        if self.path.startswith("/api/display/"):
            return self._display(self.path.split("?")[0][len("/api/display/"):], body if isinstance(body, dict) else {})
        if self.path.startswith("/api/caroline/sys/"):
            return self._caroline_sys("POST", self.path.split("?")[0][len("/api/caroline/sys/"):], {}, body)
        try:
            if self.path == "/api/launch":
                return self._json({"pid": launch(body["id"], body.get("arg"))})
            if self.path == "/api/caroline/voice":  # the R2 button, with an optional argument ({say: text}: a test types it into her page)
                start_button(body.get("arg"))
                return self._json({"ok": True})
            if self.path == "/api/caroline/settings":  # the Settings app's section of the assistant: its own settings view (Caroline: her panel, in her layer)
                if sw.load_credentials() is None:
                    return self._json({"error": "sign in first"}, 409)
                view = ASSISTANT.active.view("settings")
                if not view:
                    return self._json({"error": f"{ASSISTANT.active.name} has no settings"}, 404)
                return self._json({"pid": launch(view, {"settings": True})})
            if self.path == "/api/close":
                close(int(body["pid"]))
                return self._json({"ok": True})
            if self.path == "/api/caroline/app/prepare":  # Caroline: the services the app needs start, as when it is opened, without its window
                SERVICES.required_by(body["app"])
                return self._json({"ok": True})
            if self.path == "/api/caroline/app/tool":  # Caroline: a tool of an app's page ({app, tool, args}); the app is opened if needed
                try:
                    return self._json(app_tool(body["app"], body["tool"], body.get("args") or {}, screen=body.get("screen") or "console"))
                except KeyError as e:
                    return self._json({"error": str(e.args[0])}, 404)
            if self.path == "/api/settings/set":
                return self._fs(lambda: (self._settings_writer(), settings_set(body["key"], body["value"]))[1])
            if self.path == "/api/notifications/dismiss":
                return self._notes(lambda: note_dismiss(int(body["id"]), self._notes_scope()))
            if self.path == "/api/notifications/clear":
                return self._notes(lambda: note_clear(body.get("app"), self._notes_scope()))
            if self.path == "/api/notifications/read":
                return self._notes(lambda: note_read(self._notes_scope()))
            if self.path == "/api/notifications/dnd":
                return self._notes(lambda: (self._settings_writer(), note_dnd(body["on"]))[1])
            if self.path == "/api/caroline/notifications/dismiss":
                return self._json(note_dismiss(int(body["id"])))
            if self.path == "/api/caroline/notifications/clear":
                return self._json(note_clear(body.get("app")))
            if self.path == "/api/caroline/notifications/dnd":
                return self._json(note_dnd(body["on"]))
            if self.path == "/api/time/set":
                return self._clock(lambda: (self._settings_writer(), CLOCK.set_time(body["year"], body["month"], body["day"], body["hour"], body["minute"]), CLOCK.status())[2])
            if self.path == "/api/time/zone":
                return self._clock(lambda: (self._settings_writer(), CLOCK.set_zone(body["zone"]), CLOCK.status())[2])
            if self.path == "/api/time/ntp":
                return self._clock(lambda: (self._settings_writer(), CLOCK.set_ntp(body["on"]), CLOCK.status())[2])
            if self.path == "/api/net/connect":
                return self._net(lambda: (self._wifi_app(), NET.connect(body["ssid"], body.get("password")), {"ok": True})[2])
            if self.path == "/api/auth/login":
                return self._sw(lambda: self._login(body))
            if self.path == "/api/auth/key":     # the technical login: a key issued by the owner stands for the test account (sw.key_login)
                return self._sw(lambda: self._login(body, key=True))
            if self.path == "/api/auth/signup":  # a new account, then the same login (the welcome gift, Caroline, her wizard)
                return self._sw(lambda: self._login(body, signup=True))
            if self.path == "/api/caroline/start":
                ASSISTANT.active.ensure()
                return self._json({"ok": True})
            if self.path == "/api/caroline/stop":
                ASSISTANT.active.stop()
                return self._json({"ok": True})
            if self.path in ("/api/services/start", "/api/services/stop"):  # by hand (the console): a stopped service stays stopped until started
                self._console_only(mutating=True)
                (SERVICES.ensure if self.path.endswith("start") else SERVICES.stop)(body["id"])
                return self._json({"ok": True})
            if self.path == "/api/services/register":  # a service added at run time (tests, the console): {"app": "", "service": {manifest entry}}
                self._console_only(mutating=True)
                SERVICES.add(body["service"], body.get("app", ""))
                return self._json({"ok": True})
            if self.path == "/api/services/limit":  # the memory limit of the device (MB), to try the budget
                self._console_only(mutating=True)
                SERVICES.limit_mb = float(body["mb"])
                return self._json({"ok": True, "limit_mb": SERVICES.limit_mb})
            if self.path == "/api/net/panel":   # Android: its own Wi-Fi panel (on/off is not given to an app there); anywhere else there is nothing to open
                return self._net(lambda: (self._wifi_app(), NET.panel() if hasattr(NET, "panel") else None, {"ok": True})[2])
            if self.path == "/api/alarm/schedule":   # {epoch}: the next time the Clock is due (0 = nothing); Android's alarm wakes the phone for it. Only a service that may take over the window
                sid = self.headers.get("X-IntelliCat-Service-Id", "")
                if not SERVICES.takeover_allowed(sid):
                    return self._json({"error": "this service may not set the alarm"}, 403)
                if androidhost.ENABLED:
                    try:
                        androidhost.call("alarm_set", epoch_ms=int(float(body.get("epoch") or 0) * 1000))
                    except androidhost.BridgeError as e:
                        return self._json({"error": str(e)}, 502)
                return self._json({"ok": True, "android": androidhost.ENABLED})
            if self.path == "/api/window/takeover":  # a service takes over the shared window (an incoming call): only if its manifest permits
                sid = self.headers.get("X-IntelliCat-Service-Id", "")
                if not SERVICES.takeover_allowed(sid):
                    return self._json({"error": "this service may not take over the window"}, 403)
                app = next((a for a in APPS if a["id"] == body["app"] and a.get("window") == "shared"), None)
                if not app:
                    return self._json({"error": "no such app with a shared window"}, 404)
                ilog(f"window: {sid} takes over the window for {app['id']}")
                return self._json({"pid": launch_shared(app, body.get("arg"))})
            if self.path == "/api/stt/start":
                return self._json(stt_start())
            if self.path == "/api/stt/stop":
                return self._sw(self._stt_stop)
            if self.path == "/api/browser/open":   # {url}: the device browser shows it (an assistant's sign-in in a browser: runtime/browser_open.py)
                return self._browser_open(body)
            if self.path == "/api/stt/audio":   # speech a page recorded itself (the assistant's voice view): ai:stt and the transcript checks
                try:
                    return self._sw(lambda: stt_audio(body if isinstance(body, dict) else {}))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if self.path == "/api/caroline/speech":   # the voice of an assistant without its own (/api/assistant/speech): ai:tts on the owner's SW subscription
                return self._sw(lambda: assistant_speech(body if isinstance(body, dict) else {}))
            if self.path == "/api/beep":
                beep()
                return self._json({"ok": True})
            if self.path == "/api/db/set":
                return self._fs(lambda: db_set(self._db_owner(), body["key"], body["value"]))
            if self.path == "/api/exit":
                return self._fs(lambda: exit_app(self._app()))
            hit = EXTENSIONS.find("POST", self.path)
            if hit:   # a route an extension brings (the simulator's service menu)
                return self._ext_call(hit, body)
            if self.path == "/api/face/play":  # {id, audio: base64}: the face plays the answer (render, sound and picture in its own process)
                import base64
                return self._json({"ok": FACE.play(body["id"], base64.b64decode(body["audio"]))})
            if self.path == "/api/face/stop":
                FACE.stop_play()
                return self._json({"ok": True})
            if self.path == "/api/fs/mkdir":
                return self._fs(lambda: fs_mkdir(body["path"]))
            if self.path == "/api/fs/rename":
                return self._fs(lambda: fs_rename(body["path"], body["name"]))
            if self.path == "/api/fs/copy":
                return self._fs(lambda: fs_copy(body["path"], body["to"]))
            if self.path == "/api/fs/move":
                return self._fs(lambda: fs_move(body["path"], body["to"]))
            if self.path == "/api/fs/delete":
                return self._fs(lambda: fs_delete(body["path"]))
            if self.path == "/api/open":
                return self._fs(lambda: open_file(body["path"]))
            if self.path == "/api/fs/write":
                return self._fs(lambda: fs_write(body["path"], body["text"], bool(body.get("create"))))
            if self.path == "/api/log":
                ilog("page: " + str(body.get("msg", "")))
                return self._json({"ok": True})
            if self.path == "/api/notify":
                return self._json({"id": notify(body["text"], body.get("app", ""), body.get("level", "info"),
                                                bool(body.get("sound")), bool(body.get("vibrate")))})
            if self.path == "/api/home":
                home()
                return self._json({"ok": True})
            if self.path == "/api/power/sleep":  # from powerd: a short press of the power button
                return self._json(sleep_toggle(body.get("state")))
            if self.path == "/api/osk/typing":   # osk.js on the console's screen: its keyboard is up (a field has the focus) or gone; the consoles type with the PC's keyboard while it is up
                on = bool(isinstance(body, dict) and body.get("on"))
                with lock:
                    changed, OSK_TYPING["on"], conns = OSK_TYPING["on"] != on, on, list(CONSOLES["conns"])
                if changed:
                    ilog(f"osk: a field {'has' if on else 'no longer has'} the focus, the PC's keyboard {'types' if on else 'is the buttons again'}")
                    _push(conns, {"t": "typing", "on": on})
                return self._json({"ok": True})
            if self.path == "/api/hw/button":
                hw_button(body["name"], bool(body["pressed"]))
                if body["name"] == "mode" and body["pressed"] and not asleep():
                    home()
                return self._json({"ok": True})
            if self.path == "/api/hw/stick":
                HW.stick(body["which"], float(body["x"]), float(body["y"]))
                EXTIN.stick(body["which"], float(body["x"]), float(body["y"]))
                return self._json({"ok": True})
            if self.path == "/api/hw/touch":
                HW.touch(body["x"], body["y"], bool(body["down"]))
                return self._json({"ok": True})
            if self.path == "/api/hw/battery":
                HW.set_battery(body.get("capacity"), body.get("status"), body.get("ac"))
                return self._json(HW.power())
        except PermissionError as e:  # the guards (the service menu, the services API): an answer, not a dropped connection
            return self._json({"error": str(e)}, 403)
        except (KeyError, ValueError) as e:
            return self._json({"error": str(e)}, 400)
        except RuntimeError as e:
            return self._json({"error": str(e)}, 503)
        self._json({"error": "not found"}, 404)


# ---- the apps of the store: install, update, remove (runtime/appinstall.py) -------------------------------------------------------------------
INSTALLED_ROOT = os.path.realpath(os.environ.get("INTELLICAT_APPS") or os.path.expanduser("~/.intellicat/apps"))   # one folder per installed app, beside the built-in apps
APP_DATA_ROOT = os.path.join(DATA_DIR, "apps")   # an installed app's services keep their files in <APP_DATA_ROOT>/<id>/ (INTELLICAT_DATA of those services)
INSTALLED_IDS = set()                            # the ids registered from INSTALLED_ROOT now
STORE_LOCK = threading.RLock()
STORE_URL = (os.environ.get("INTELLICAT_STORE_URL") or "https://navlink.net").rstrip("/")   # the arbiter of apps.navlink.net: /api/apps/list?os=beroun
STORE_ASSETS = os.environ.get("INTELLICAT_STORE_ASSETS") or "https://apps.navlink.net/assets/"   # packages are downloaded from here only (the signature is what is trusted)
# What this device has of CATalogue's capability names (runtime/manifest.py CAPABILITIES): no camera, GPS, Bluetooth or NFC; INTELLICAT_CAPABILITIES (a comma list) replaces it (tests)
DEVICE_CAPABILITIES = set(filter(None, (os.environ["INTELLICAT_CAPABILITIES"].split(",") if "INTELLICAT_CAPABILITIES" in os.environ else
                                        ["Notification", "FileStorage", "Network", "Microphone", "ExternalDisplay", "WebGL"])))
if androidhost.ENABLED and "INTELLICAT_CAPABILITIES" not in os.environ:   # on Android it is what the phone has (SystemOps.capabilities)
    try:
        DEVICE_CAPABILITIES = set(androidhost.call("capabilities").get("capabilities") or DEVICE_CAPABILITIES)
    except androidhost.BridgeError:
        pass


def device_info():
    """Where this runs (Platform.device(), docs/system-api.md): kind intellicat | simulator | hosted | android, for layout and hints, never for the logic of capabilities.
    INTELLICAT_KIND sets it (the Android app does); otherwise the hosted simulator is ONLINE, the console has its INTELLICAT_HARDWARE, anything else is the simulator."""
    kind = os.environ.get("INTELLICAT_KIND")
    if kind not in ("intellicat", "simulator", "hosted", "android"):
        hw = os.environ.get("INTELLICAT_HARDWARE", "Simulator")
        kind = "hosted" if ONLINE else "simulator" if hw == "Simulator" else "intellicat"
    return {"kind": kind, "touch": True}


def app_capabilities(app_id):
    """The capabilities the app declared and this device has."""
    return [c for c in APP_CAPABILITIES.get(app_id, []) if c in DEVICE_CAPABILITIES]


def _services_in_use():
    return ({s.id: s.app for s in SERVICES.services.values()}, {s.port: s.app for s in SERVICES.services.values() if s.port})


STORE = appinstall.Store(INSTALLED_ROOT, lambda: FIRMWARE_APPS, device_capabilities=lambda: DEVICE_CAPABILITIES, log=lambda m: print(m, flush=True), in_use=_services_in_use)


def app_register(app_id, root=None):
    """Puts an app on the device without a restart: its tile, services, endpoints, agents, permissions, big screen page and Caroline's tools. root: the store's folder (the default)
    or FIRMWARE_ROOT (the firmware's own copy, when the store's newer version is gone or a removed app comes back). Whatever is registered under the id is replaced."""
    root = root or INSTALLED_ROOT
    if any(a["id"] == app_id for a in APPS):
        app_unregister(app_id, keep_data=True)
    n0 = len(SERVICE_SPECS)
    found = scan_apps(root, only=app_id)
    if not found:
        raise RuntimeError(f"{app_id}: the manifest is refused")
    app = found[0]
    from_store = root == INSTALLED_ROOT
    app["installed"] = from_store
    isolated = STORE.kind(app_id) == "store"   # an app that came with the firmware keeps using the shared data folder (its files stay as they were when it is updated)
    datadir = os.path.join(APP_DATA_ROOT, app_id)
    if isolated:
        os.makedirs(datadir, exist_ok=True)
    for _, spec in SERVICE_SPECS[n0:]:
        if isolated:
            spec = dict(spec, env=dict(spec.get("env") or {}, INTELLICAT_DATA=datadir, INTELLICAT_APP_DATA=datadir))
        SERVICES.add(spec, app_id)
        if spec.get("start") == "system" and spec.get("host", "process") == "process":
            SERVICES.ensure(spec["id"])
    if app_id in AGENT_SPECS:
        AGENTS.register(app_id, *AGENT_SPECS[app_id])
    if app_id in EXTERNAL:
        EXTD.register_app(app_id, EXTERNAL[app_id])
    APPS.append(app)
    APPS.sort(key=lambda a: a.get("order", 100))
    if from_store:
        INSTALLED_IDS.add(app_id)


def app_unregister(app_id, keep_data=True):
    """The reverse of app_register: the app is closed, its services stop, everything it registered goes. keep_data False also deletes its own files and its saved place."""
    for r in running():
        if r["app"] == app_id:
            close(r["pid"])
    SERVICES.remove_app(app_id)
    SERVICE_SPECS[:] = [x for x in SERVICE_SPECS if x[0] != app_id]
    AGENTS.unregister(app_id)
    for reg in (CAROLINE_APPS, ENDPOINTS, AGENT_SPECS, APP_PERMISSIONS, EXTERNAL, BADGES):
        reg.pop(app_id, None)
    EXTD.unregister_app(app_id)
    APPS[:] = [a for a in APPS if a["id"] != app_id]
    INSTALLED_IDS.discard(app_id)
    if not keep_data:
        with _db_lock:
            db().execute("DELETE FROM kv WHERE app=?", (app_id,))
            db().commit()
        shutil.rmtree(os.path.join(APP_DATA_ROOT, app_id), ignore_errors=True)


def app_register_current(app_id):
    """Whatever the device has under this id now: the store's folder over the firmware's copy; nothing for an app that is not there."""
    if STORE.installed_version(app_id) is not None:
        app_register(app_id)
    elif app_id in FIRMWARE_APPS and app_id not in STORE.removed:
        app_register(app_id, FIRMWARE_ROOT)


def store_install(data):
    """Installs or updates an app from a package; every step that fails puts the previous state back."""
    with STORE_LOCK:
        p = STORE.prepare(data)
        try:
            if p.update:
                app_unregister(p.app_id, keep_data=True)
            STORE.activate(p)
            app_register(p.app_id)
            STORE.finish(p)
        except Exception as e:  # noqa: BLE001
            print(f"store: {p.app_id} {p.version} failed ({e}); the previous state is put back", flush=True)
            try:
                app_unregister(p.app_id, keep_data=True)
            except Exception:  # noqa: BLE001
                pass
            STORE.rollback(p)
            app_register_current(p.app_id)
            raise appinstall.InstallError(f"{p.app_id} {p.version} could not be installed: {e}", "failed")
        print(f"store: {p.app_id} {p.version} {'updated from ' + p.old_version if p.update else 'installed'}", flush=True)
        return {"id": p.app_id, "name": p.name, "version": p.version, "action": "updated" if p.update else "installed", "was": p.old_version}


def store_remove(app_id):
    """An app from the store goes with its data. A preinstalled app is hidden and keeps its data, so that restoring it brings it back as it was. A system app is never removed."""
    with STORE_LOCK:
        STORE.check_removable(app_id)   # raises the right InstallError
        preinstalled = STORE.kind(app_id) == "preinstalled"
        app_unregister(app_id, keep_data=preinstalled)
        STORE.remove(app_id)
        print(f"store: {app_id} removed" + (" (hidden, its data kept)" if preinstalled else ""), flush=True)
        return {"id": app_id, "removed": True, "data_kept": preinstalled}


def store_restore(app_id):
    """A removed preinstalled app comes back, as the firmware has it."""
    with STORE_LOCK:
        STORE.restore(app_id)
        app_register(app_id, FIRMWARE_ROOT)
        print(f"store: {app_id} restored", flush=True)
        return {"id": app_id, "name": FIRMWARE_APPS[app_id]["name"], "version": FIRMWARE_APPS[app_id]["version"], "action": "restored"}


def _store_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "OS-Beroun-Store"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(appinstall.MAX_ZIP + 1)


def _vt(v):
    try:
        return appinstall.version_tuple(v)
    except (ValueError, TypeError):
        return (0,)


def store_catalog(q=""):
    """Every app the owner can do something about: what the arbiter lists for Beroun and what is on the device (the firmware's apps, the store's), one entry per id.
    {"apps": [...], "offline": bool}; an app's `state`: system (a system app, up to date), installed, update (a newer version is in the store), available (not on the device: in the
    store, or a preinstalled app that was removed), unsupported (needs what the device lacks); `kind`: system | preinstalled | store."""
    from urllib.parse import quote
    listing, offline, error = [], False, None
    try:
        listing = json.loads(_store_get(f"{STORE_URL}/api/apps/list?os=beroun" + (f"&q={quote(q)}" if q else "")))
    except (OSError, ValueError) as e:
        offline, error = True, f"the store cannot be reached: {e}"
    cat = {}
    for item in listing if isinstance(listing, list) else []:
        dl = next((d for d in item.get("downloads") or [] if d.get("platform") == "beroun" and d.get("appId")), None)
        if dl:
            cat[dl["appId"]] = (item, dl)
    ql = q.lower()
    local = {i: {"name": f["name"], "description": "", "hidden": f["hidden"]} for i, f in FIRMWARE_APPS.items() if not ql or ql in f["name"].lower() or ql in i}
    for a in STORE.installed():
        local.setdefault(a["id"], {"name": a["name"], "description": "", "hidden": False})
    out = []
    for app_id in sorted(set(cat) | set(local)):
        item, dl = cat.get(app_id, ({}, {}))
        loc = local.get(app_id) or {}
        if loc.get("hidden") and app_id not in STORE.removed:
            continue   # a dev app without a tile
        kind, have = STORE.kind(app_id), STORE.version(app_id)
        summary = dl.get("summary") or {}
        lacking = [c["name"] for c in summary.get("capabilities") or [] if c.get("required") and c["name"] not in DEVICE_CAPABILITIES]
        foreign = bool(summary.get("system")) != (kind == "system") and bool(dl)   # the store's package says "system" and this device has no such system app, or the reverse
        newer = bool(dl) and have is not None and not foreign and _vt(dl.get("version")) > _vt(have)
        if have is None:
            state = "unsupported" if dl and (foreign or lacking) else "available"
        else:
            state = "update" if newer else "system" if kind == "system" else "installed"
        out.append({"id": app_id, "name": item.get("name") or loc.get("name") or app_id, "description": item.get("description") or "", "category": item.get("category"),
                    "author": item.get("author"), "version": dl.get("version") or have, "size": dl.get("size"), "installed": have, "kind": kind, "state": state, "lacking": lacking,
                    "summary": summary or None, "icon": item.get("iconUrl"), "url": dl.get("url"), "sha256": dl.get("sha256"), "in_store": bool(dl)})
    return {"apps": out, "offline": offline, "error": error}


def store_install_from_catalog(app_id):
    if app_id in STORE.removed:   # a preinstalled app that was removed: the firmware has it, no network needed (a newer version is then an update)
        return store_restore(app_id)
    entry = next((e for e in store_catalog()["apps"] if e["id"] == app_id and e["in_store"]), None)
    if not entry:
        raise appinstall.InstallError(f"{app_id!r} is not in the store", "not-found")
    if not str(entry.get("url") or "").startswith(STORE_ASSETS):
        raise appinstall.InstallError("the store lists a download from somewhere else; refused", "bad-package")
    try:
        data = _store_get(entry["url"])
    except OSError as e:
        raise appinstall.InstallError(f"the package could not be downloaded: {e}", "offline")
    if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise appinstall.InstallError("the downloaded package does not match the store's hash", "bad-package")
    return store_install(data)


def store_updates():
    return [e for e in store_catalog()["apps"] if e["state"] == "update"]


def _current_manifest(app_id):
    """The manifest of the version of an app that is on the device now (the store's folder over the firmware's copy); {} when there is none."""
    root = INSTALLED_ROOT if STORE.installed_version(app_id) is not None else FIRMWARE_ROOT
    try:
        return json.load(open(os.path.join(root, app_id, "manifest.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def store_auto_update():
    """The setting "auto_update" (on until switched off): every waiting update installs itself, the same way as A on Update in the Store (the signature, the hashes, the rules,
    the previous state put back on a failure), and a notification says so. Two kinds wait for the owner instead: an update that asks for more than the version on the device
    (appinstall.rights: a capability, a permission, a service, an agent or its rights), and one of an app that is open now (it is tried again soon). Returns how many wait for that."""
    busy, later = {r["app"] for r in running()}, 0
    for e in store_updates():
        if e["id"] in busy:
            later += 1
            continue
        more = appinstall.rights(e.get("summary") or {}) - appinstall.rights(_current_manifest(e["id"]))
        if more:
            print(f"store: {e['id']} {e['version']} waits for the owner (it asks for more: {sorted(more)})", flush=True)
            continue
        try:
            r = store_install_from_catalog(e["id"])
        except Exception as ex:  # noqa: BLE001
            print(f"store: the automatic update of {e['id']} failed: {ex}", flush=True)
            continue
        notify(f"{r['name']} was updated to v{r['version']}.", app="Store")   # named as Clock and Calendar name themselves (the panel finds the app by name too)
    return later


def store_watch():
    """The Store's tile shows how many updates wait, and with "auto_update" on they install themselves first (store_auto_update): looked for a minute after the start and then
    every 6 hours (15 minutes while an update waits for its app to be closed), while the device is online. INTELLICAT_STORE_WATCH_START: the first look, in seconds (tests)."""
    time.sleep(float(os.environ.get("INTELLICAT_STORE_WATCH_START", "60")))
    while True:
        later = 0
        try:
            if appinstall.store_public_key() and NET.status()["online"]:
                if settings_get()["auto_update"]:
                    later = store_auto_update()
                BADGES["store"] = len(store_updates())
        except Exception as e:  # noqa: BLE001
            print(f"store watch: {e}", flush=True)
        time.sleep(15 * 60 if later else 6 * 3600)


for _d in sorted(STORE.removed):   # firmware apps the owner removed stay away
    if _d in FIRMWARE_APPS:
        app_unregister(_d, keep_data=True)
for _d in sorted(os.listdir(INSTALLED_ROOT)):   # at the start: the installed apps come back (one that is refused stays on disk and is reported, nothing else is affected)
    if not _d.startswith(".") and os.path.isdir(os.path.join(INSTALLED_ROOT, _d)):
        try:
            app_register(_d)
        except Exception as _e:  # noqa: BLE001
            print(f"store: {_d} not registered: {_e}", flush=True)


if __name__ == "__main__":
    os.makedirs(STATE, exist_ok=True)
    os.makedirs(os.path.join(HOME_ROOT, "Notepad"), exist_ok=True)
    EXTENSIONS.load(sys.modules[__name__])
    try:
        HW.open_input()
        HW.open_power()  # the power key exists from the start: powerd reads it
    except Exception as e:  # noqa: BLE001
        print(f"virtual gamepad: {e}", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    # Caroline's backend only accepts pages from the origin of her own Linux shell (origin_guard.py): the same page,
    # same port. Everything her wwwroot has is served there; the rest (the shim, /api/*) is this same handler.
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", CAROLINE_PAGE_PORT), H).serve_forever, daemon=True).start()
    threading.Thread(target=wizard_watch, daemon=True, name="wizard-watch").start()
    threading.Thread(target=messaging_watch, daemon=True, name="messaging-watch").start()
    threading.Thread(target=store_watch, daemon=True, name="store-watch").start()
    SERVICES.start_thread()

    WOKEN_BY_CALL = {"at": 0.0}  # when a call woke the sleeping console (0: it did not)

    def ratatosk_incoming(c):  # somebody calls: the console wakes if it sleeps, the notification (with the beep) and the app comes up on its call screen (decisions D5, 6w)
        if asleep():  # the screen is off and the apps are paused: the ring needs both (the watcher itself never sleeps: it is in this process)
            ilog("ratatosk: an incoming call wakes the console")
            WOKEN_BY_CALL["at"] = time.time()
            sleep_toggle("awake")
        notify(f"Ratatosk: call from {(c.get('from') or '').split('@')[0]} ({c.get('name')})", app="ratatosk", level="warning", sound=True, vibrate=True)
        try:
            launch("ratatosk")
        except Exception as e:  # noqa: BLE001
            print(f"ratatosk: the call could not bring the app up: {e}", flush=True)
    RATATOSK.on_incoming = ratatosk_incoming
    RATATOSK.asleep = asleep
    RATATOSK.seen_since = lambda ts: LAST_INPUT["t"] >= ts   # a missed call is seen when a finger or a button has touched the console since

    def ratatosk_ring(c):  # the ring goes on while the call rings (every few seconds): the sound and the vibration, no new line in the status bar (quiet with do-not-disturb, as notify)
        if dnd_on():
            return
        SYSSOUNDS.play("ring", force=True)
        VIBRA.pulse("ring", force=True)
    RATATOSK.on_ring = ratatosk_ring

    def back_to_sleep_later():
        """A call woke the console and nobody touched it since (it was missed): after a short pause it goes back to sleep, as a phone does."""
        woke = WOKEN_BY_CALL["at"]
        WOKEN_BY_CALL["at"] = 0.0
        if not woke:
            return

        def later():
            time.sleep(4)
            if LAST_INPUT["t"] < woke and not asleep() and RATATOSK.calls.status()["state"] in ("idle", "ended"):
                ilog("ratatosk: the call that woke the console was missed and nobody touched it: back to sleep")
                sleep_toggle("asleep")
        threading.Thread(target=later, daemon=True, name="ratatosk-resleep").start()

    def ratatosk_missed(c, n):  # a call rang and nobody answered here: the badge of the tile (the count) and one quiet notification; the app clears it when it is opened
        if n:
            BADGES["ratatosk"] = n
        else:
            BADGES.pop("ratatosk", None)
        if c:
            notify(f"Ratatosk: missed call from {(c.get('from') or '').split('@')[0]} ({c.get('name')})", app="ratatosk", level="info")
            back_to_sleep_later()
    RATATOSK.on_missed = ratatosk_missed
    # the background part of Ratatosk is a service of its own (manifest): it runs while the console sleeps (so a sleeping console rings), may take the window for a call, is never
    # stopped for memory; it lives in this process, so its figure in /api/services is that of shelld's tree
    SERVICES.register_module("ratatosk", RATATOSK.start, RATATOSK.stop, os.getpid)
    RATATOSK.attachments.card_root = HOME_ROOT  # files are read from, played from and saved to the card only
    RATATOSK.attachments.purge()                 # copies made for playing in an earlier run do not stay
    if RATATOSK.calls.missed:
        BADGES["ratatosk"] = len(RATATOSK.calls.missed)  # the count survives a restart
    RATATOSK.start()  # the sync of the chats and the watcher of incoming calls run while there is an account, with the app open or not
    signal.signal(signal.SIGTERM, lambda *_: ([SERVICES.stop(s) for s in list(SERVICES.services) if SERVICES.services[s].host == "process"], [p.stop() for p in ASSISTANT.providers.values()], CAROLINE.stop(), EXT.stop(), HW.close(), os._exit(0)))
    print(f"shelld on 127.0.0.1:{PORT}", flush=True)
    threading.Thread(target=ext_pointer_loop, daemon=True, name="ext-pointer").start()
    m = re.fullmatch(r"(\d+)x(\d+)", os.environ.get("INTELLICAT_HDMI", ""))
    if m:  # the simulator starts with the cable plugged in (INTELLICAT_HDMI=1280x720): for the tests and for work on the big screen
        threading.Thread(target=lambda: EXTD.set_connected(True, int(m.group(1)), int(m.group(2))), daemon=True, name="hdmi-start").start()
    try:
        srv.serve_forever()
    finally:
        HW.close()
