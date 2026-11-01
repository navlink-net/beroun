# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The manifest of an app (and of a view of the system): ONE format, ours (docs/app-format.md).

    {"format": 1, "id": "notepad", "name": "Notepad", "version": "1.1.0", "icon": "icon.svg",
     "frontend": {"entry": "index.html", "window": "shared"},        # a web page: "entry" (in the app's folder) or "url" (another origin); window: shared | own;
                                                                    #   optional "microphone", "transparent"
     "native":   {"cmd": ["ic-browser", "..."], "page": "p.html", "host": "browser"},    # instead of a frontend: a program of its own ({port}, {sim} are filled in); "page": its stand-in where it cannot run (Android); "host": the Kotlin host's own view that shows that page there (browser: tabs of WebViews under the page)
     "tile":     {"order": 0, "hidden": true},                        # the launcher: place among the tiles; hidden: launched by the system, no tile
     "system":   true,                                               # a system app of OS Beroun (Browser, Settings, ...): the store can update it, never remove it or install it
     "beroun":   "1.0",                                              # the lowest version of OS Beroun (its system API) the app needs; a store package must have it
     "capabilities": [{"name": "Microphone", "scope": {"required": true}}, {"name": "ExternalDisplay"}],   # what of the device the app uses (docs/app-format.md): CATalogue's {name, scope}
     "state":    true,                                               # the contract: the app saves its place when it closes and returns to it (Platform.state)
     "opens":    ["txt"],                                            # the file types the app opens when the file manager chooses one
     "services": [ ... ],                                            # the backends that survive its close (runtime/services.py); a process service may have a "port"
     "endpoints": [{"path": "alarms", "methods": ["GET", "POST"], "service": "alarmd", "access": ["page", "mcp"]}],   # what the shell passes on to a service's port:
                                                                    #   /api/app/<id>/<path> -> 127.0.0.1:<port>/<path>; only the app's own page and its MCP servers may call
     "caroline": {"description": "...", "skills": ["skills"],        # how Caroline drives the app: one line for her list of apps, folders of skills (SKILL.md),
                  "mcp": [{"id": "main", "transport": "stdio", "cmd": ["..."]}],   #   MCP servers of the app (stdio: cmd; http: url), and
                  "tools": [{"name": "open_note", "description": "...", "input": {"path": "string"}}]},   #   tools of its page (declared here, answered by Platform.tools)
     "agents": [{"id": "planner", "description": "...",              # agents of the app (docs/architecture.md): autonomous, Caroline gives a task and gets a result;
                 "skills": [{"name": "plan_week", "description": "...", "input": {"goal": "string"}, "output": "text", "long": true, "confirm": false}],
                 "host": {"service": "calendard", "endpoint": "agent"},   #   where it answers: a service+endpoint of the app, an mcp server of "caroline.mcp", or the "page"
                 "camerlengo": ["ai:*"],                                  #   the Camerlengo API commands it may call through the shell (charged to the owner's wallet)
                 "permissions": ["notifications.post"],                   #   what of the system it may use (PERMISSIONS); the manifest is the approval, nobody clicks
                 "a2a": {"expose": true}}]}                               #   reachable from outside as an A2A agent (runtime/a2a_bridge.py); skills with "confirm" are not offered unless "a2a.confirm"

normalize() turns it into the flat dictionary the shell works with; validate() lists what is wrong. Nothing else is accepted: unknown fields are errors.
"""
import os, re

FORMAT = 1
TOP = {"format", "id", "name", "version", "icon", "frontend", "native", "tile", "state", "opens", "services", "endpoints", "caroline", "assistant", "agents", "permissions", "external", "beroun", "capabilities", "system", "binaries"}
# The native parts of a package: "binaries": {"player": {"linux-x86_64": "bin/linux-x86_64/player", ...}}, used as {bin:player} in a cmd.
TARGETS = ("linux-arm64", "linux-x86_64", "android-arm64", "android-x86_64")   # <os>-<cpu>: the console, the simulators, the phones, the emulator
BIN_REF = re.compile(r"\{bin:([a-z][a-z0-9_-]*)\}")
FRONTEND = {"entry", "url", "window", "microphone", "transparent"}
TILE = {"order", "hidden"}
EXTERNAL = {"entry", "url", "cmd", "env", "state", "input"}   # the page of the app for the external (HDMI) screen, docs/system-api.md
CAROLINE = {"description", "skills", "mcp", "tools"}
MCP = {"id", "transport", "cmd", "url", "env", "service"}
TOOL = {"name", "description", "input"}
ENDPOINT = {"path", "methods", "service", "access"}
AGENT = {"id", "description", "skills", "host", "camerlengo", "permissions", "a2a"}
A2A = {"expose", "confirm", "tags"}   # the agent is reachable from outside as an A2A agent (runtime/a2a_bridge.py); confirm: also the skills with an effect outside the app
AGENT_SKILL = {"name", "description", "input", "output", "long", "confirm"}
AGENT_HOST = {"service", "endpoint", "mcp", "page", "a2a", "key"}   # a2a: the card URL of a remote A2A agent (runtime/a2a_client.py); key: the NAME of the key it wants (the token is in a file of the device)
# What of the system an agent may use besides its own app's data (which needs no permission). The manifest is the approval: declared = allowed.
PERMISSIONS = {"files.read", "files.write", "net.status", "net.manage", "settings.read", "settings.write", "notifications.read", "notifications.post",
               "sound", "vibration", "message_box", "apps.open"}
# What the APP itself (its page and its services, not an agent) may ask of the system. The manifest is the approval: declared = allowed.
APP_PERMISSIONS = {"assistant.ask",  # send the assistant (Caroline, or another provider in the slot) a request and get the answer (POST /api/assistant/ask, docs/assistant-contract.md)
                   "caroline.ask",   # the old name of assistant.ask (POST /api/caroline/ask), accepted for ever
                   "account.session",  # a SERVICE of the app gets the device's SquirrelWisdom session (GET /api/account/session) for a cloud service that checks it (the cloud Minecraft)
                   "apps.manage"}    # install, update and remove the apps of the store (/api/store/*): only the Store app has it; a package may not declare it
# What of the device an app may use (CATalogue's capability contract, TunnelCat/CATalogue/APPSTORE_PLAN.md; ExternalDisplay and WebGL are ours). THE LIST IS OPEN: a manifest may name any
# well-formed capability (CAPABILITY_NAME), also one this OS has never heard of, so that a new capability needs no change of the store's arbiter and a package written for a newer OS still
# installs on an older one. A device that lacks a capability simply does not have it: an optional one is absent, a required one (scope.required) keeps the package from being offered or
# installed there (missing-capability). CAPABILITIES below are the names this OS knows; the device's own set is DEVICE_CAPABILITIES (shelld). `scope` is open per capability; only "required" is read by the store.
CAPABILITY_NAME = re.compile(r"[A-Z][A-Za-z0-9]{1,31}")
CAPABILITIES = {"Camera", "Microphone", "GPS", "Bluetooth", "NFC", "Notification", "FileStorage", "CloudStorage", "Network", "ExternalDisplay", "WebGL"}   # WebGL: the page can make a WebGL context (3D, Babylon.js)
BEROUN_VERSION = "1.0"   # the version of OS Beroun this code is; an app's "beroun" must not be higher
CAMERLENGO_COMMAND = r"[a-z][a-z0-9_]*:(\*|[a-z][a-z0-9_]*)"   # "ai:stt", "email:*"
INPUT_TYPE = r"(string|number|integer|boolean)\??"             # the vocabulary of caroline.tools; "?" = optional
SERVICE = {"id", "name", "host", "module", "start", "required_by", "priority", "memory_mb", "sleep", "takeover", "privileged", "cmd", "cwd", "env", "parent", "match", "port"}


def validate(m, folder=None, system=False):
    """Errors of a manifest (a list of strings; empty when it is right). folder: the app's directory, to check the files it names."""
    e = []
    if not isinstance(m, dict):
        return ["the manifest is not an object"]
    if m.get("format") != FORMAT:
        e.append(f"format must be {FORMAT}")
    for k in set(m) - TOP:
        e.append(f"unknown field '{k}'")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", str(m.get("id", ""))):
        e.append("id must be lowercase letters, digits and '-'")
    elif folder:
        base = os.path.basename(folder.rstrip("/\\"))
        if base != m["id"]:
            e.append(f"id '{m['id']}' differs from the folder name '{base}'")
    if not str(m.get("name", "")).strip():
        e.append("name is required")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(m.get("version", ""))):
        e.append("version must be x.y.z")
    fe, nat = m.get("frontend"), m.get("native")
    if (fe is None) == (nat is None):
        e.append("exactly one of 'frontend' and 'native' is required")
    if fe is not None:
        if not isinstance(fe, dict):
            e.append("frontend must be an object")
        else:
            for k in set(fe) - FRONTEND:
                e.append(f"unknown frontend field '{k}'")
            if bool(fe.get("entry")) == bool(fe.get("url")):
                e.append("frontend needs exactly one of 'entry' and 'url'")
            if fe.get("window", "own") not in ("shared", "own"):
                e.append("frontend.window must be 'shared' or 'own'")
            if fe.get("window") == "shared":
                if not fe.get("entry"):
                    e.append("a frontend in the shared window needs an 'entry' of its own")
                if m.get("state") is not True:
                    e.append("a frontend in the shared window must keep its state (\"state\": true)")
            if folder and fe.get("entry") and not os.path.isfile(os.path.join(folder, fe["entry"])):
                e.append(f"entry '{fe['entry']}' does not exist")
            if folder and m.get("state") is True and fe.get("entry") and os.path.isfile(os.path.join(folder, fe["entry"])):
                if "Platform.state" not in open(os.path.join(folder, fe["entry"]), encoding="utf-8", errors="replace").read():
                    e.append("\"state\": true but the page never uses Platform.state")
    if nat is not None:
        if not isinstance(nat, dict) or not isinstance(nat.get("cmd"), list) or not nat["cmd"] or set(nat) - {"cmd", "page", "host"}:
            e.append("native must be {\"cmd\": [..]}, {\"cmd\": [..], \"page\": \"file.html\"} or with \"host\": \"browser\"")
        elif "page" in nat:
            if not isinstance(nat["page"], str) or not nat["page"]:
                e.append("native.page must be the name of a page of the app")
            elif folder and not os.path.isfile(os.path.join(folder, nat["page"])):
                e.append(f"native.page '{nat['page']}' does not exist")
        if "host" in nat and (nat["host"] != "browser" or "page" not in nat):
            e.append("native.host is \"browser\" and needs native.page")
    tile = m.get("tile", {})
    if not isinstance(tile, dict) or set(tile) - TILE or ("order" in tile and not isinstance(tile["order"], int)) or ("hidden" in tile and not isinstance(tile["hidden"], bool)):
        e.append("tile must be {\"order\": int, \"hidden\": bool}")
    if "state" in m and not isinstance(m["state"], bool):
        e.append("state must be true or false")
    if "system" in m and not isinstance(m["system"], bool):
        e.append("system must be true or false")
    if "external" in m:
        e += _validate_external(m, folder)
    if "opens" in m and (not isinstance(m["opens"], list) or not all(isinstance(x, str) and x for x in m["opens"])):
        e.append("opens must be a list of file extensions")
    if "icon" in m and folder and not os.path.isfile(os.path.join(folder, m["icon"])):
        e.append(f"icon '{m['icon']}' does not exist")
    if not m.get("icon") and not (tile.get("hidden") if isinstance(tile, dict) else False) and not system:
        e.append("an app with a tile needs an icon")
    for i, s in enumerate(m.get("services", []) or []):
        if not isinstance(s, dict):
            e.append(f"services[{i}] is not an object")
            continue
        for k in set(s) - SERVICE:
            e.append(f"services[{i}]: unknown field '{k}'")
        if not s.get("id"):
            e.append(f"services[{i}]: id is required")
        if s.get("host", "process") not in ("module", "process", "child"):
            e.append(f"services[{i}]: host must be module, process or child")
        if s.get("host", "process") == "process" and not isinstance(s.get("cmd"), list):
            e.append(f"services[{i}]: a process service needs cmd")
        if s.get("host") == "child":
            mt = s.get("match")
            if not isinstance(s.get("parent"), str) or not s.get("parent"):
                e.append(f"services[{i}]: a child service needs parent (the id of the service that starts it)")
            if not isinstance(mt, dict) or not (mt.get("argv") or mt.get("env")) or set(mt) - {"argv", "env"}:
                e.append(f"services[{i}]: a child service needs match with argv (a string or a list) and/or env (an object)")
            elif not isinstance(mt.get("argv", ""), (str, list)) or not isinstance(mt.get("env", {}), dict):
                e.append(f"services[{i}]: match.argv is a string or a list, match.env an object")
        if "port" in s and (s.get("host", "process") != "process" or not isinstance(s["port"], int) or not 1024 <= s["port"] <= 65535):
            e.append(f"services[{i}]: port (1024..65535) belongs to a process service")
        if s.get("start", "manual") not in ("system", "open", "manual"):
            e.append(f"services[{i}]: start must be system, open or manual")
        if not isinstance(s.get("priority", 50), int) or not 0 <= s.get("priority", 50) <= 100:
            e.append(f"services[{i}]: priority must be 0..100")
    e += _validate_endpoints(m)
    if "caroline" in m and "assistant" in m:
        e.append("an app has the section assistant or its old name caroline, not both")
    e += _validate_caroline(m, folder, "assistant" if "assistant" in m else "caroline")
    e += _validate_agents(m)
    pm = m.get("permissions")
    if pm is not None:
        if not isinstance(pm, list) or not all(isinstance(x, str) for x in pm) or len(set(pm)) != len(pm):
            e.append("permissions is a list of names, without repeats")
        else:
            e += [f"unknown permission '{x}' (the app's own: {', '.join(sorted(APP_PERMISSIONS))})" for x in pm if x not in APP_PERMISSIONS]
    e += _validate_capabilities(m)
    e += _validate_binaries(m, folder)
    return e


def device_target():
    """The target of this device, <os>-<cpu>: what {bin:...} is filled in for (INTELLICAT_TARGET overrides it: tests)."""
    import platform, sys
    if os.environ.get("INTELLICAT_TARGET"):
        return os.environ["INTELLICAT_TARGET"]
    cpu = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
    return ("android-" if hasattr(sys, "getandroidapilevel") or os.environ.get("INTELLICAT_KIND") == "android" else "linux-") + cpu


def binary_paths(m):
    """Every file path a manifest names in "binaries" (all targets)."""
    b = m.get("binaries")
    return {p for t in b.values() if isinstance(t, dict) for p in t.values() if isinstance(p, str)} if isinstance(b, dict) else set()


def _cmds(m):
    """Every command list of a manifest: the native program, the services', the MCP servers' and the external screen's."""
    out = []
    if isinstance(m.get("native"), dict):
        out.append(("native", m["native"].get("cmd")))
    for i, s in enumerate(m.get("services") or []):
        if isinstance(s, dict):
            out.append((f"services[{i}]", s.get("cmd")))
    sec = assistant_section(m)
    for i, c in enumerate((sec.get("mcp") if isinstance(sec, dict) else None) or []):
        if isinstance(c, dict):
            out.append((f"mcp[{i}]", c.get("cmd")))
    if isinstance(m.get("external"), dict):
        out.append(("external", m["external"].get("cmd")))
    return [(w, c) for w, c in out if isinstance(c, list)]


def _validate_binaries(m, folder):
    """"binaries": {name: {target: path}}: the path is bin/<target>/<file> in the package; every {bin:name} of a command names one, and every binary is used."""
    b = m.get("binaries")
    used = {r for _w, c in _cmds(m) for t in c if isinstance(t, str) for r in BIN_REF.findall(t)}
    if b is None:
        return [f"{w}: {{bin:{r}}} but the manifest has no binaries" for w, c in _cmds(m) for t in c if isinstance(t, str) for r in BIN_REF.findall(t)]
    if not isinstance(b, dict) or not b:
        return ["binaries must be an object {name: {target: path}}"]
    e = []
    for name, per in b.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", str(name)):
            e.append(f"binaries: name '{name}' must be lowercase letters, digits, '_' and '-'")
        if not isinstance(per, dict) or not per:
            e.append(f"binaries.{name} must be an object {{target: path}}")
            continue
        for t, p in per.items():
            if t not in TARGETS:
                e.append(f"binaries.{name}: unknown target '{t}' ({', '.join(TARGETS)})")
            elif not (isinstance(p, str) and re.fullmatch(r"bin/%s/[A-Za-z0-9_][A-Za-z0-9_.+-]*" % re.escape(t), p)):
                e.append(f"binaries.{name}.{t}: the path must be bin/{t}/<file>")
            elif folder and t == device_target() and not os.path.isfile(os.path.join(folder, *p.split("/"))):   # a device holds only its own target's files
                e.append(f"binaries.{name}.{t}: '{p}' does not exist")
        if name not in used:
            e.append(f"binaries.{name} is never used as {{bin:{name}}} in a command")
    e += [f"{w}: {{bin:{r}}} is not in binaries" for w, c in _cmds(m) for t in c if isinstance(t, str) for r in BIN_REF.findall(t) if r not in b]
    return e


def fill_bin(text, binaries, folder, target=None):
    """{bin:name} in a text -> the absolute path of that binary for `target` (this device's by default). None when the package has no build for the target."""
    target = target or device_target()
    def one(mo):
        p = ((binaries or {}).get(mo.group(1)) or {}).get(target)
        if p is None:
            raise KeyError(mo.group(1))
        return os.path.join(folder, *p.split("/"))
    try:
        return BIN_REF.sub(one, text)
    except KeyError:
        return None


def fill_bin_list(cmd, binaries, folder, target=None):
    """A command with every {bin:...} filled in; None when a binary has no build for the target (the part cannot run here)."""
    out = [fill_bin(c, binaries, folder, target) for c in cmd]
    return None if None in out else out


def bind_binaries(m, folder, target=None, log=print):
    """A manifest (normalized or not) with {bin:...} filled in for the target in every command. A service, an MCP server or an external program whose binary has no build
    for the target is left out (logged): the rest of the app still runs. Nothing changes in a manifest without "binaries"."""
    if not m.get("binaries"):
        return m
    import copy
    m, b = copy.deepcopy(m), m["binaries"]
    def fill(c, what):
        out = fill_bin_list(c, b, folder, target)
        if out is None:
            log(f"manifest: {m.get('id')} {what} has no build of its binary for {target or device_target()}: left out")
        return out
    if isinstance(m.get("services"), list):
        keep = []
        for s in m["services"]:
            if isinstance(s, dict) and isinstance(s.get("cmd"), list):
                s["cmd"] = fill(s["cmd"], f"service {s.get('id')}")
                if s["cmd"] is None:
                    continue
            keep.append(s)
        m["services"] = keep
    for key in ("assistant", "caroline"):
        sec = m.get(key)
        if isinstance(sec, dict) and isinstance(sec.get("mcp"), list):
            sec["mcp"] = [c for c in sec["mcp"] if not isinstance(c, dict) or not isinstance(c.get("cmd"), list)
                          or (c.update(cmd=fill(c["cmd"], f"mcp {c.get('id')}")) or c["cmd"] is not None)]
    x = m.get("external")
    if isinstance(x, dict) and isinstance(x.get("cmd"), list):
        x["cmd"] = fill(x["cmd"], "external")
        if x["cmd"] is None:
            del m["external"]
    n = m.get("native")
    if isinstance(n, dict) and isinstance(n.get("cmd"), list):
        n["cmd"] = fill(n["cmd"], "native program")
    if m.get("cmd"):
        m["cmd"] = fill(m["cmd"], "native program")
    return m


def has_target(m, target=None):
    """Whether a package can run on a target: every binary it names has a build for it (a package with no binaries fits every device)."""
    target = target or device_target()
    return all(target in per for per in (m.get("binaries") or {}).values() if isinstance(per, dict))


def _version_tuple(v):
    p = [int(x) for x in v.split(".")]
    return tuple(p + [0] * (3 - len(p)))


def _validate_capabilities(m):
    """"beroun": the lowest OS Beroun the app needs ("1.0" or "1.0.0", not above this OS); "capabilities": [{"name", "scope"}], each name once, well-formed (the list is open)."""
    e = []
    b = m.get("beroun")
    if b is not None:
        if not isinstance(b, str) or not re.fullmatch(r"\d+\.\d+(\.\d+)?", b):
            e.append("beroun must be a version, x.y or x.y.z")
        elif _version_tuple(b) > _version_tuple(BEROUN_VERSION):
            e.append(f"the app needs OS Beroun {b}; this is {BEROUN_VERSION}")
    caps = m.get("capabilities")
    if caps is None:
        return e
    if not isinstance(caps, list):
        return e + ["capabilities must be a list"]
    names = []
    for i, c in enumerate(caps):
        if not isinstance(c, dict) or set(c) - {"name", "scope"}:
            e.append(f"capabilities[{i}] must be {{\"name\", \"scope\"}}")
            continue
        if not isinstance(c.get("name"), str) or not CAPABILITY_NAME.fullmatch(c["name"]):
            e.append(f"capabilities[{i}]: a capability name is a capital letter, then letters and digits, 2 to 32 characters (got {c.get('name')!r})")
        names.append(c.get("name"))
        sc = c.get("scope", {})
        if not isinstance(sc, dict):
            e.append(f"capabilities[{i}]: scope must be an object")
        elif "required" in sc and not isinstance(sc["required"], bool):
            e.append(f"capabilities[{i}]: scope.required must be true or false")
    if len(set(names)) != len(names):
        e.append("a capability is declared once")
    return e


def required_capabilities(m):
    """The names of the capabilities the app cannot work without (scope.required)."""
    return [c["name"] for c in m.get("capabilities") or [] if (c.get("scope") or {}).get("required")]


def _validate_external(m, folder):
    """"external": {"entry": "big.html", "state": true}: the app has a page for the external screen (Platform.display.open). Exactly one of `entry` (a page of the app's folder, in the
    big window), `url` (a page of another origin in it) and `cmd` (a native program of its own started on the external screen, the browser: {port}, {sim}, {state} are filled in; `env`: its
    environment). The page of a `cmd` is the program's business, but it still keeps its state (a page of the app talking to the shell with ?ext=1 saves in Platform.onSuspend)."""
    x = m["external"]
    if not isinstance(x, dict):
        return ["external must be an object"]
    e = [f"external: unknown field '{k}'" for k in sorted(set(x) - EXTERNAL)]
    if sum(bool(x.get(k)) for k in ("entry", "url", "cmd")) != 1:
        e.append("external needs exactly one of 'entry', 'url' and 'cmd'")
    if "cmd" in x and (not isinstance(x["cmd"], list) or not x["cmd"] or not all(isinstance(c, str) and c for c in x["cmd"])):
        e.append("external.cmd must be a list of texts")
    if "env" in x and (not x.get("cmd") or not isinstance(x["env"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in x["env"].items())):
        e.append("external.env is a {name: text} object and goes with cmd")
    if x.get("state") is not True:
        e.append("external must keep its state (\"state\": true): the page is covered by another app and comes back to the same place")
    if "input" in x and x["input"] != "game":
        e.append("external.input is \"game\" (a game on the big screen gets the sticks, X and Y; without it they are the screen's mouse)")
    if x.get("entry"):
        if folder and not os.path.isfile(os.path.join(folder, str(x["entry"]))):
            e.append(f"external.entry '{x['entry']}' does not exist")
        elif folder and x.get("state") is True and "Platform.state" not in open(os.path.join(folder, x["entry"]), encoding="utf-8", errors="replace").read():
            e.append("external has \"state\": true but its page never uses Platform.state")
    return e


def _validate_endpoints(m):
    """The section "endpoints": the paths of /api/app/<id>/ that the shell passes on to a port of the app's own service."""
    eps = m.get("endpoints")
    if eps is None:
        return []
    if not isinstance(eps, list):
        return ["endpoints must be a list"]
    svc = {x.get("id"): x for x in m.get("services", []) or [] if isinstance(x, dict)}
    e, seen = [], set()
    for i, d in enumerate(eps):
        if not isinstance(d, dict):
            e.append(f"endpoints[{i}] is not an object")
            continue
        e += [f"endpoints[{i}]: unknown field '{k}'" for k in sorted(set(d) - ENDPOINT)]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_/-]*", str(d.get("path", ""))) or ".." in str(d.get("path")) or d.get("path") in seen:
            e.append(f"endpoints[{i}]: path must be unique, lowercase letters, digits, '-', '_' and '/', without a leading '/'")
        seen.add(d.get("path"))
        ms = d.get("methods", ["GET"])
        if not isinstance(ms, list) or not ms or not set(ms) <= {"GET", "POST"}:
            e.append(f"endpoints[{i}]: methods is a list of GET and POST")
        ac = d.get("access", ["page", "mcp"])
        # "page:<app id>": the page of another app of the same package (the hidden player of the cloud Minecraft has an identity of its own: its window, MODE, exit)
        if not isinstance(ac, list) or not ac or not all(a in ("page", "mcp", "agent") or (isinstance(a, str) and re.fullmatch(r"page:[a-z][a-z0-9-]*", a)) for a in ac):
            e.append(f"endpoints[{i}]: access is a list of page, page:<app id>, mcp and agent")
        sv = svc.get(d.get("service"))
        if sv is None:
            e.append(f"endpoints[{i}]: service '{d.get('service')}' is not one of the app's services")
        elif not isinstance(sv.get("port"), int) or not 1024 <= sv["port"] <= 65535:
            e.append(f"endpoints[{i}]: service '{d.get('service')}' needs a port (1024..65535) to receive them")
    return e


def assistant_section(m):
    """The section "assistant" of a manifest, or its old name "caroline" (docs/assistant-contract.md); None when the app has neither."""
    return m.get("assistant") if "assistant" in m else m.get("caroline")


def _validate_caroline(m, folder, key="caroline"):
    """The section "assistant" (old name "caroline"): what the system's assistant gets about the app: a line for its list of apps, skills, MCP servers, tools of the page."""
    c = m.get(key)
    if c is None:
        return []
    if not isinstance(c, dict):
        return [f"{key} must be an object"]
    e = [f"{key}: unknown field '{k}'" for k in sorted(set(c) - CAROLINE)]
    if not isinstance(c.get("description"), str) or not c["description"].strip():
        e.append(f"{key}.description is required: one line saying what the assistant can do with the app")
    skills = c.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(x, str) and x for x in skills):
        e.append(f"{key}.skills must be a list of folders")
    else:
        for x in skills:
            parts = x.replace("\\", "/").split("/")
            if x.startswith("/") or ".." in parts:
                e.append(f"{key}.skills '{x}' must be a folder inside the app")
            elif folder and not os.path.isdir(os.path.join(folder, x)):
                e.append(f"{key}.skills '{x}' does not exist")
    ids = set()
    for i, s in enumerate(c.get("mcp", []) or []):
        if not isinstance(s, dict):
            e.append(f"{key}.mcp[{i}] is not an object")
            continue
        e += [f"{key}.mcp[{i}]: unknown field '{k}'" for k in sorted(set(s) - MCP)]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", str(s.get("id", ""))) or s["id"] in ids:
            e.append(f"{key}.mcp[{i}]: id must be unique lowercase letters, digits and '-'")
        ids.add(s.get("id"))
        if s.get("transport") == "stdio":
            if not isinstance(s.get("cmd"), list) or not s["cmd"] or s.get("url"):
                e.append(f"{key}.mcp[{i}]: a stdio server needs cmd (a list) and no url")
        elif s.get("transport") == "http":
            if not isinstance(s.get("url"), str) or not s["url"].startswith("http") or s.get("cmd"):
                e.append(f"{key}.mcp[{i}]: an http server needs url (http...) and no cmd")
        else:
            e.append(f"{key}.mcp[{i}]: transport must be stdio or http")
        if "env" in s and not (isinstance(s["env"], dict) and all(isinstance(v, str) for v in s["env"].values())):
            e.append(f"{key}.mcp[{i}]: env must be an object of texts")
        if s.get("service") is not None and s["service"] not in {x.get("id") for x in m.get("services", []) or [] if isinstance(x, dict)}:
            e.append(f"{key}.mcp[{i}]: service '{s['service']}' is not one of the app's services")
    names = set()
    for i, t in enumerate(c.get("tools", []) or []):
        if not isinstance(t, dict):
            e.append(f"{key}.tools[{i}] is not an object")
            continue
        e += [f"{key}.tools[{i}]: unknown field '{k}'" for k in sorted(set(t) - TOOL)]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(t.get("name", ""))) or t["name"] in names:
            e.append(f"{key}.tools[{i}]: name must be unique lowercase letters, digits and '_'")
        names.add(t.get("name"))
        if not isinstance(t.get("description"), str) or not t["description"].strip():
            e.append(f"{key}.tools[{i}]: description is required")
        if "input" in t and not (isinstance(t["input"], dict) and all(isinstance(v, str) and re.fullmatch(INPUT_TYPE, v) for v in t["input"].values())):
            e.append(f"{key}.tools[{i}]: input is an object {{parameter: type}}, type: string, number, integer or boolean, with ? when the parameter is optional")
    if c.get("tools") and m.get("frontend") is None:
        e.append(f"{key}.tools are the page's: the app needs a frontend")
    return e


def _validate_agents(m):
    """The section "agents" (docs/architecture.md): the autonomous agents the app publishes for Caroline. Declared = allowed: no other approval exists."""
    ags = m.get("agents")
    if ags is None:
        return []
    if not isinstance(ags, list):
        return ["agents must be a list"]
    svc = {x.get("id"): x for x in m.get("services", []) or [] if isinstance(x, dict)}
    eps = [x for x in m.get("endpoints", []) or [] if isinstance(x, dict)]
    car = assistant_section(m) if isinstance(assistant_section(m), dict) else {}
    mcps = {x.get("id") for x in car.get("mcp", []) or [] if isinstance(x, dict)}
    e, ids = [], set()
    for i, a in enumerate(ags):
        p = f"agents[{i}]"
        if not isinstance(a, dict):
            e.append(f"{p} is not an object")
            continue
        e += [f"{p}: unknown field '{k}'" for k in sorted(set(a) - AGENT)]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", str(a.get("id", ""))) or a["id"] in ids:
            e.append(f"{p}: id must be unique lowercase letters, digits and '-'")
        ids.add(a.get("id"))
        if not isinstance(a.get("description"), str) or not a["description"].strip():
            e.append(f"{p}: description is required: one line telling Caroline what the agent is for")
        sk = a.get("skills")
        remote = isinstance(a.get("host"), dict) and "a2a" in a["host"]
        if remote and sk is None:    # a remote A2A agent: its skills are read from its card
            sk = []
        elif not isinstance(sk, list) or not sk:
            e.append(f"{p}: skills must be a non-empty list" + (" (a remote A2A agent may leave them out: they come from its card)" if remote else ""))
            sk = []
        names = set()
        for j, s in enumerate(sk):
            q = f"{p}.skills[{j}]"
            if not isinstance(s, dict):
                e.append(f"{q} is not an object")
                continue
            e += [f"{q}: unknown field '{k}'" for k in sorted(set(s) - AGENT_SKILL)]
            if not re.fullmatch(r"[a-z][a-z0-9_]*", str(s.get("name", ""))) or s["name"] in names:
                e.append(f"{q}: name must be unique lowercase letters, digits and '_'")
            names.add(s.get("name"))
            if not isinstance(s.get("description"), str) or not s["description"].strip():
                e.append(f"{q}: description is required")
            if "input" in s and not (isinstance(s["input"], dict) and all(isinstance(v, str) and re.fullmatch(INPUT_TYPE, v) for v in s["input"].values())):
                e.append(f"{q}: input is an object {{parameter: type}}, type: string, number, integer or boolean, with ? when the parameter is optional")
            if s.get("output", "text") not in ("text", "json"):
                e.append(f"{q}: output must be text or json")
            for k in ("long", "confirm"):
                if k in s and not isinstance(s[k], bool):
                    e.append(f"{q}: {k} must be true or false")
        h = a.get("host")
        if not isinstance(h, dict) or set(h) - AGENT_HOST:
            e.append(f"{p}: host must be an object with service+endpoint, mcp or page")
        else:
            kinds = [k for k in ("service", "mcp", "page", "a2a") if k in h]
            if len(kinds) != 1 or ("endpoint" in h) != ("service" in h) or ("key" in h and "a2a" not in h):
                e.append(f"{p}: host needs exactly one of service (with endpoint), mcp, page and a2a (key only with a2a)")
            elif "a2a" in h:
                u = h["a2a"]
                if not (isinstance(u, str) and re.fullmatch(r"https?://[^\s/]+(/\S*)?", u)):
                    e.append(f"{p}: host.a2a is the URL of the agent's card (http:// or https://)")
                if "key" in h and not (isinstance(h["key"], str) and re.fullmatch(r"[a-z][a-z0-9_-]*", h["key"])):
                    e.append(f"{p}: host.key is the name of a key of the device (lowercase letters, digits, '-', '_'), never the key itself")
                if a.get("camerlengo") or a.get("permissions"):
                    e.append(f"{p}: a remote A2A agent is not our code: no camerlengo and no permissions")
                if "a2a" in a:
                    e.append(f"{p}: a remote A2A agent cannot be exposed again (a2a)")
            elif "service" in h:
                if h["service"] not in svc:
                    e.append(f"{p}: host.service '{h['service']}' is not one of the app's services")
                elif not any(d.get("path") == h["endpoint"] and d.get("service") == h["service"] and "agent" in d.get("access", ["page", "mcp"]) for d in eps):
                    e.append(f"{p}: host.endpoint '{h['endpoint']}' must be an endpoint of that service with access including 'agent'")
            elif "mcp" in h:
                if h["mcp"] not in mcps:
                    e.append(f"{p}: host.mcp '{h['mcp']}' is not one of caroline.mcp")
            elif h.get("page") is not True or m.get("frontend") is None:
                e.append(f"{p}: host.page is true and the app needs a frontend")
        if "a2a" in a:
            x = a["a2a"]
            if not isinstance(x, dict) or set(x) - A2A or not isinstance(x.get("expose"), bool):
                e.append(f"{p}: a2a is an object {{\"expose\": true|false, \"confirm\"?: bool, \"tags\"?: [text]}}")
            else:
                if "confirm" in x and not isinstance(x["confirm"], bool):
                    e.append(f"{p}: a2a.confirm must be true or false")
                if "tags" in x and not (isinstance(x["tags"], list) and all(isinstance(t, str) and t for t in x["tags"])):
                    e.append(f"{p}: a2a.tags is a list of texts")
                if x["expose"] and not any(not s.get("confirm") or x.get("confirm") for s in sk if isinstance(s, dict)):
                    e.append(f"{p}: a2a exposes the agent but none of its skills may be offered (every one has an effect outside the app; set a2a.confirm)")
        cm = a.get("camerlengo", [])
        if not isinstance(cm, list) or not all(isinstance(x, str) and re.fullmatch(CAMERLENGO_COMMAND, x) for x in cm) or len(set(cm)) != len(cm):
            e.append(f"{p}: camerlengo is a list of commands like 'ai:stt' or 'email:*', without repeats")
        pm = a.get("permissions", [])
        if not isinstance(pm, list) or not all(isinstance(x, str) for x in pm) or len(set(pm)) != len(pm):
            e.append(f"{p}: permissions is a list of names, without repeats")
        else:
            e += [f"{p}: unknown permission '{x}'" for x in pm if x not in PERMISSIONS]
    return e


def normalize(m):
    """The flat dictionary of the shell (the fields scan_apps reads) from a format-1 manifest."""
    fe, nat, tile = m.get("frontend") or {}, m.get("native"), m.get("tile") or {}
    flat = {"id": m["id"], "name": m["name"], "version": m.get("version", ""), "services": m.get("services", [])}
    if m.get("icon"):
        flat["icon"] = m["icon"]
    if m.get("opens"):
        flat["opens"] = m["opens"]
    if nat:
        flat["cmd"] = nat["cmd"]
        if nat.get("page"):   # the page that takes the program's place on a device where it cannot run (Android: no GStreamer)
            flat["page"] = nat["page"]
        if nat.get("host"):   # Android: the kind of view the host makes for the page
            flat["host"] = nat["host"]
    else:
        for k in ("entry", "url", "microphone", "transparent"):
            if fe.get(k):
                flat[k] = fe[k]
        if fe.get("window") == "shared":
            flat["window"] = "shared"
    if m.get("endpoints"):
        flat["endpoints"] = m["endpoints"]
    if assistant_section(m):   # one key for the shell, whichever name the manifest uses
        flat["caroline"] = flat["assistant"] = assistant_section(m)
    if m.get("agents"):
        flat["agents"] = m["agents"]
    if m.get("permissions"):
        flat["permissions"] = m["permissions"]
    if m.get("external"):
        flat["external"] = m["external"]
    if m.get("capabilities"):
        flat["capabilities"] = m["capabilities"]
        if any(c["name"] == "Microphone" for c in m["capabilities"]):   # the capability is the app's ask for the microphone (a view of the system says frontend.microphone)
            flat["microphone"] = True
    if m.get("beroun"):
        flat["beroun"] = m["beroun"]
    if m.get("system"):
        flat["system"] = True
    if m.get("binaries"):
        flat["binaries"] = m["binaries"]
    if tile.get("hidden"):
        flat["hidden"] = True
    if "order" in tile:
        flat["order"] = tile["order"]
    return flat
