# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The one table of the assistant's OS tools (docs/assistant-contract.md, section 3). Every provider gets these names and these handlers:
Caroline as her plugins (caroline-overlay/ic_apps/plugins.py wraps them), an MCP provider from the OS Beroun MCP server (mcp_server.py).

A handler takes the arguments (a dict) and returns {"text", "is_error"?, "content"?}; it is plain blocking code (a wrapper runs it on a thread where that matters).
groups() is built anew on every call: an app installed or changed shows within a turn.

The groups:
  * beroun: OS Beroun's own tools: the apps as a whole, any app's window, the external screen, files, settings, clock, network, sleep, notifications, message box, sounds,
    vibration, the store, the assistant's own updates;
  * agents: the agents of the apps (docs/architecture.md), when any is published;
  * app-<id>: one per app with an "assistant" (old name "caroline") section: the tools of its page and <id>_open / <id>_close. The tools of the app's MCP servers are the
    provider's own business (Caroline: her pool; an MCP provider: the servers themselves).
"""
import json, os, re, time

from . import shell

MAX_RESULT_CHARS = 60000


class Tool:
    def __init__(self, name, description, schema, handler):
        self.name, self.description, self.schema, self.handler = name, description, schema, handler


class Group:
    def __init__(self, name, description, tools, guidance="", app=None):
        self.name, self.description, self.tools, self.guidance, self.app = name, description, tools, guidance, app


def ident(app_id):
    return re.sub(r"[^a-z0-9]+", "_", app_id.lower()).strip("_")


def json_schema(input_spec):
    """A page tool's {parameter: type} as a JSON Schema; a type ending in ? is an optional parameter."""
    types = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean"}
    props = {k: {"type": types.get(v.rstrip("?"), "string")} for k, v in (input_spec or {}).items()}
    return {"type": "object", "properties": props, "required": [k for k, v in (input_spec or {}).items() if not v.endswith("?")]}


def result(blocks, is_error=False, structured=None):
    text = "\n".join(b["text"] for b in blocks if b.get("type") == "text")
    if not text and structured is not None:
        text = json.dumps(structured, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n[... cut at {MAX_RESULT_CHARS} characters]"
    out = {"text": text or "(empty result)"}
    if any(b.get("type") == "image" for b in blocks):
        out["content"] = ([{"type": "text", "text": text}] if text else []) + [b for b in blocks if b.get("type") == "image"]
    if is_error:
        out["is_error"] = True
    return out


def skill_names_of(folder):
    """The names of the skills under an app's folder, from the "name:" of each SKILL.md (the frontmatter), else the folder's name."""
    out = []
    for root, _dirs, files in os.walk(folder):
        if "SKILL.md" in files:
            name = os.path.basename(root)
            try:
                for line in open(os.path.join(root, "SKILL.md"), encoding="utf-8").read().split("\n---", 1)[0].splitlines():
                    if line.startswith("name:"):
                        name = line[5:].strip().strip("\"'") or name
                        break
            except OSError:
                pass
            out.append(name)
    return sorted(out)


# ---- app-<id> -----------------------------------------------------------------------------------------------------------
def app_guidance(a, skill_names):
    lines = [f"{a['name']}: {a['description']}"]
    if a.get("tools"):
        lines.append("The tools marked (page) work through the app's open window; the app is opened for you when it is not open.")
    names = skill_names(a["folder"])
    if names:
        lines.append("Skills of this app (open one with use_skill before the work it describes): " + ", ".join(names) + ".")
    lines.append(f"Closing the app ({ident(a['id'])}_close) makes it save its place; opening it again brings it back to the same place.")
    return "\n".join(lines)


def app_group(a, skill_names=skill_names_of):
    """The group of one app (without the tools of its MCP servers): <id>_open, <id>_close and its page tools."""
    i = ident(a["id"])

    def open_app(args):
        shell.launch(a["id"], shell.launch_arg((args or {}).get("arg")))
        return {"text": f"{a['name']} is open."}

    def close_app(_args):
        return {"text": f"{a['name']} was asked to save and close." if shell.close(a["id"]) else f"{a['name']} is not open."}

    tools = [Tool(f"{i}_open", f"Opens {a['name']} (its window), at the place where it was closed. `arg` is optional: what the app opens (a file path for an app that opens files).",
                  {"type": "object", "properties": {"arg": {"type": "string"}}}, open_app),
             Tool(f"{i}_close", f"Closes {a['name']}: it saves its place first.", {"type": "object", "properties": {}}, close_app)]
    for t in a.get("tools", []):
        def page(args, tool=t["name"]):
            r = shell.page_tool(a["id"], tool, args or {})
            return result([{"type": "text", "text": r.get("text", "")}], bool(r.get("is_error")), r.get("result"))
        tools.append(Tool(f"{i}_{t['name']}", f"(page) {t['description']}", json_schema(t.get("input")), page))
    return Group(f"app-{a['id']}", a["description"], tools, app_guidance(a, skill_names), app=a)


# ---- beroun -------------------------------------------------------------------------------------------------------------
def _on(args):
    return str((args or {}).get("on")).lower() in ("true", "on", "yes", "1")


def _schema(**props):
    return {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "required": [k for k in props if k in ("value", "key")]}


def _obj(required=(), **props):
    return {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "required": list(required)}


# (name, what it does, area/operation, GET or POST, {field: type}, required fields): the rest of the OS API, shelld's /api/assistant/sys/<path>
OS_TOOLS = [
    ("beroun_browser_open", "Opens a web address (http or https) in the device's browser, in a tab of its own brought to the front; the browser is started when it is not running. `label` names the tab (opening the same label again reuses it).", "browser/open", "POST", {"url": "string", "label": "string"}, ("url",)),
    ("beroun_files_list", "Lists a folder of the device's memory card: `path` (empty for the top), `ext` (only files of that type). Each entry has its name and whether it is a folder.", "files/list", "GET", {"path": "string", "ext": "string"}, ()),
    ("beroun_files_read", "Reads a text file of the memory card (up to 1 MB): `path`.", "files/read", "GET", {"path": "string"}, ("path",)),
    ("beroun_files_write", "Writes a text file of the memory card: `path`, `text`; `create` true makes a new file (otherwise the file must exist). The whole file is replaced.", "files/write", "POST", {"path": "string", "text": "string", "create": "boolean"}, ("path", "text")),
    ("beroun_files_mkdir", "Makes a folder on the memory card: `path`.", "files/mkdir", "POST", {"path": "string"}, ("path",)),
    ("beroun_files_rename", "Renames a file or folder of the memory card: `path`, and the new `name` (a name only, not a path).", "files/rename", "POST", {"path": "string", "name": "string"}, ("path", "name")),
    ("beroun_files_copy", "Copies a file or folder of the memory card into the folder `to`.", "files/copy", "POST", {"path": "string", "to": "string"}, ("path", "to")),
    ("beroun_files_move", "Moves a file or folder of the memory card into the folder `to`.", "files/move", "POST", {"path": "string", "to": "string"}, ("path", "to")),
    ("beroun_files_delete", "Deletes a file or folder of the memory card (for good): do it only when asked.", "files/delete", "POST", {"path": "string"}, ("path",)),
    ("beroun_files_space", "How much room the memory card has: total, used and free, in bytes.", "files/space", "GET", {}, ()),
    ("beroun_about", "The device itself: the version of its software, when that software was built, and the hardware it runs on (Settings > About).", "about", "GET", {}, ()),
    ("beroun_device", "Where OS Beroun runs: `kind` (intellicat, simulator, hosted or android) and whether there is touch.", "device", "GET", {}, ()),
    ("beroun_capabilities", "What of the device the apps can use (Microphone, Camera, ExternalDisplay, ...): the capabilities this device has.", "capabilities", "GET", {}, ()),
    ("beroun_settings", "The device's settings: language, keyboard layouts, do-not-disturb, vibration, automatic app updates (auto_update) and the rest, as the Settings app shows them.", "settings/get", "GET", {}, ()),
    ("beroun_set_setting", "Changes one setting of the device: `key` (as beroun_settings shows it) and the new `value`. The owner's settings: change them only when asked.", "settings/set", "POST", {"key": "string", "value": "string"}, ("key", "value")),
    ("beroun_time", "The device's clock: the time, the zone, whether the network sets it (NTP).", "time/status", "GET", {}, ()),
    ("beroun_time_zones", "The time zones the clock knows.", "time/zones", "GET", {}, ()),
    ("beroun_set_time", "Sets the clock by hand: `year`, `month`, `day`, `hour`, `minute` (turn NTP off first, or the network sets it back).", "time/set", "POST", {"year": "integer", "month": "integer", "day": "integer", "hour": "integer", "minute": "integer"}, ("year", "month", "day", "hour", "minute")),
    ("beroun_set_time_zone", "Sets the time zone (a name from beroun_time_zones, e.g. Europe/Prague).", "time/zone", "POST", {"zone": "string"}, ("zone",)),
    ("beroun_set_ntp", "Lets the network set the clock (`on`: true) or not.", "time/ntp", "POST", {"on": "boolean"}, ("on",)),
    ("beroun_net_status", "The network: whether the device is online, the Wi-Fi it is joined to.", "net/status", "GET", {}, ()),
    ("beroun_net_scan", "Looks for Wi-Fi networks in range.", "net/scan", "GET", {}, ()),
    ("beroun_net_connect", "Joins a Wi-Fi network: `ssid`, and the `password` if it has one. Only when the owner asked and gave the password.", "net/connect", "POST", {"ssid": "string", "password": "string"}, ("ssid",)),
    ("beroun_power", "Whether the device is asleep, and the volume.", "power/status", "GET", {}, ()),
    ("beroun_services", "The device's background services (alarms, calendar, the assistant's own, ...): each one's state, the memory it uses and its priority, and the total against the device's limit.", "services/status", "GET", {}, ()),
    ("beroun_sleep", "Puts the device to sleep (`state` true), wakes it (false), or switches it (no `state`), as a short press of the power button does.", "power/sleep", "POST", {"state": "boolean"}, ()),
    ("beroun_store_catalog", "The apps of the store (apps.navlink.net, platform Beroun) this device can use: id, name, description, version, size, the rights each asks for (`summary`: capabilities, permissions, services, agents), and its state here: available, installed, update, unsupported (needs what the device lacks). `q` narrows by name or description.", "store/catalog", "GET", {"q": "string"}, ()),
    ("beroun_store_installed", "The apps installed from the store: id, name, version.", "store/installed", "GET", {}, ()),
    ("beroun_store_updates", "The installed apps for which the store has a newer version.", "store/updates", "GET", {}, ()),
    ("beroun_store_install", "Installs an app from the store, or updates it to the newest version: `app` is its id (from beroun_store_catalog). Only when the owner asked; tell the owner what rights the app asks for (`summary`) first.", "store/install", "POST", {"app": "string"}, ("app",)),
    ("beroun_store_remove", "Removes an installed app and deletes its own data. Only when the owner asked. The built-in apps of OS Beroun cannot be removed.", "store/remove", "POST", {"app": "string"}, ("app",)),
    ("beroun_update_status", "The assistant's own updates (yours): the version that runs, the firmware's, the state of the newest release (up_to_date, available, downloading, ready, needs_runtime, failed; not_supported when you come with the firmware), whether it waits for the owner's yes (`action` ask) and which versions failed to start here.", "update/status", "GET", {}, ()),
    ("beroun_update_check", "Looks for a newer release of yourself now (and downloads it); same answer as beroun_update_status.", "update/check", "POST", {}, ()),
    ("beroun_update_now", "Installs your ready update now (your backend restarts, so this conversation is cut for a moment). Only when the owner said yes; it installs by itself when you are idle anyway.", "update/now", "POST", {}, ()),
    ("beroun_assistants", "The assistant of this build of OS Beroun (you): which provider it is and what it can do. The build chooses it; nobody changes it on the device.", "assistant/list", "GET", {}, ()),
    ("beroun_notify", "Puts a notification in the launcher's panel: `text`; `level` info (default), warning or error; `sound` and `vibrate` true to call attention (do-not-disturb silences them).", "notify", "POST", {"text": "string", "level": "string", "sound": "boolean", "vibrate": "boolean"}, ("text",)),
]


def _os_call(path, method="POST", fields=()):
    """A call of the rest of the OS API: the answer is shown as it is, the text of a file as its text."""
    from urllib.parse import quote

    def call(args):
        given = {k: args[k] for k in fields if (args or {}).get(k) is not None}
        try:
            if method == "GET":
                q = "&".join(f"{k}={quote(str(v))}" for k, v in given.items())
                r = shell.system_api(path + ("?" + q if q else ""))
            else:
                r = shell.system_api(path, given, 20.0)
        except Exception as e:   # the shell's own words (no such file, a wrong password, ...)
            return {"text": str(e), "is_error": True}
        if isinstance(r, dict) and set(r) == {"text"}:
            return {"text": r["text"]}
        return {"text": json.dumps(r, ensure_ascii=False)[:MAX_RESULT_CHARS]}
    return call


def beroun_group():
    def target(args):
        app = (args or {}).get("app") or shell.current_app()
        if not app:
            raise ValueError("no app is open (or several are): say which one with `app`, or open one first")
        return app

    def apps_(_args):
        driven = {a["id"] for a in shell.apps()}
        running = {r["app"] for r in shell.running()}
        lines = [f"{a['id']}: {a['name']}" + (" [open]" if a["id"] in running else "") + (f" [group app-{a['id']}]" if a["id"] in driven else "") for a in shell.installed()]
        return {"text": "\n".join(lines) or "(no apps)"}

    def open_(args):
        shell.launch(args["app"], shell.launch_arg(args.get("arg")))
        return {"text": f"{args['app']} is open."}

    def close_(args):
        return {"text": f"{args['app']} was asked to save and close." if shell.close(args["app"]) else f"{args['app']} is not open."}

    def notes_(_args):
        r = shell.notifications()
        lines = [f"[{n['id']}] {n['at']} {n['app'] or 'system'}{' (error)' if n['level'] == 'error' else ''}: {n['text']}{'' if n['read'] else ' [new]'}" for n in r["items"]]
        return {"text": f"Do not disturb: {'on' if r['dnd'] else 'off'}. {r['unread']} unread.\n" + ("\n".join(lines) or "(no notifications)")}

    def note_dismiss_(args):
        r = shell.dismiss_notification(int(args["id"]))
        return {"text": "Dismissed." if r.get("removed") else f"No notification {args['id']}."}

    def notes_clear_(args):
        r = shell.clear_notifications(args.get("app"))
        return {"text": f"{r.get('removed', 0)} notification(s) cleared" + (f" of {args['app']}." if args.get("app") else ".")}

    def dnd_(args):
        on = _on(args)
        shell.do_not_disturb(on)
        return {"text": f"Do not disturb is {'on: notifications come without sound and vibration' if on else 'off'}."}

    def clipboard_read_(_args):
        r = shell.system_api("clipboard/pull", {}, 5.0)   # on a simulator the host's clipboard first
        return {"text": r["text"] if r.get("text") else "(the clipboard is empty)"}

    def clipboard_write_(args):
        text = args.get("text")
        if not isinstance(text, str):
            raise ValueError("`text` is required")
        r = shell.system_api("clipboard", {"text": text}, 5.0)
        return {"text": f"The clipboard holds {r['length']} characters now: the user can paste them with Paste in the keyboard's menu."}

    def message_box_(args):
        wait = min(max(int(args.get("timeout") or 60), 5), 600)
        body = {k: args[k] for k in ("title", "text", "kind", "icon") if args.get(k)}
        if args.get("buttons"):
            body["buttons"] = [str(b) for b in args["buttons"]]
        r = shell.system_api("msgbox", dict(body, wait=True, timeout=wait), wait + 10)
        if r.get("reason") == "button":
            return {"text": f"The user chose \"{r['button']}\" (button {r['index'] + 1})."}
        return {"text": {"cancel": "The user closed the message with B (no choice made).", "timeout": f"Nobody answered within {wait} seconds."}.get(r.get("reason"), "The message was withdrawn.")}

    def play_sound_(args):
        r = shell.system_api("sound/play", {"name": args["name"], **({"force": True} if args.get("force") else {})})
        return {"text": f"Played {args['name']}." if r.get("played") else f"Not played: {r.get('reason')}."}

    def vibrate_(args):
        pattern = [int(x) for x in args["milliseconds"]] if args.get("milliseconds") else args.get("pattern") or "short"
        body = {"pattern": pattern, **({"strength": int(args["strength"])} if args.get("strength") is not None else {}), **({"force": True} if args.get("force") else {})}
        r = shell.system_api("vibra/pulse", body)
        return {"text": f"Vibrating for {r['ms']} ms." if r.get("vibrated") else f"Not vibrated: {r.get('reason')}."}

    def vibrate_stop_(_args):
        shell.system_api("vibra/stop", {})
        return {"text": "The vibration is stopped."}

    def vibration_setting_(args):
        on = _on(args)
        shell.system_api("vibra/enable", {"on": on})
        return {"text": f"Vibration is {'on' if on else 'off: nothing vibrates, notifications and calls included'}."}

    def page(tool, fields):
        def call(args):
            args = args or {}
            screen = "external" if args.get("screen") == "external" else "console"
            if screen == "external" and not args.get("app"):   # the big page of the app on top of the external screen
                top = shell.display_status().get("top")
                if not top:
                    return {"text": "nothing of an app is on the external screen (the cat is there)", "is_error": True}
                args = dict(args, app=top)
            app = target(args)
            r = shell.page_tool(app, tool, {k: args[k] for k in fields if args.get(k) is not None}, 40.0, screen)
            return {"text": r.get("text", ""), **({"is_error": True} if r.get("is_error") else {})}
        return call

    def display_status_(_args):
        st = shell.display_status()
        if not st.get("connected"):
            return {"text": "No external screen is connected (the HDMI cable is out)."}
        return {"text": f"External screen: {st['width']}x{st['height']}. Focus (cursor, A, B go there): {st['focus']}. On top: {st['top'] or 'the cat splash'}. Stack, the top last: {', '.join(st['stack']) or '(empty)'}."}

    def display_open_(args):
        try:
            shell.display_open(args["app"], args.get("arg"))
        except Exception as e:   # the shell's own words: no screen, the app has no big page
            return {"text": str(e), "is_error": True}
        return {"text": f"The big page of {args['app']} is on the external screen. Read it with beroun_screen_text (screen=external, app={args['app']})."}

    def display_close_(args):
        try:
            shell.display_close(args["app"])
        except Exception as e:
            return {"text": str(e), "is_error": True}
        return {"text": f"The big page of {args['app']} is off the external screen; the one under it (or the cat) is shown."}

    def display_focus_(args):
        try:
            st = shell.display_focus(args["to"])
        except Exception as e:
            return {"text": str(e), "is_error": True}
        return {"text": f"The focus is on the {st.get('focus', args['to'])} screen."}

    os_tools = [Tool(n, d, _obj(req, **props), _os_call(path, m, tuple(props))) for n, d, path, m, props, req in OS_TOOLS]
    app_hint = " `app` is optional: the app in the window by default. `screen`: console (the default) or external, the app's big page on the HDMI screen (then `app` is the one on top by default)."
    return Group(
        "beroun", "OS Beroun: the apps of the device (list, open, close), any app's window (read, press, type), the external HDMI screen, the memory card's files, settings, the clock, the network, sleep, the notification panel, a message box, system sounds and vibration",
        [
            Tool("beroun_apps", "Lists the apps of the device: id and name, [open] when running, and the tool group of those that have one.", _schema(), apps_),
            Tool("beroun_open_app", "Opens an app by id (its window), at the place where it was closed. `arg`: what it opens (a file path).", _schema(app="string", arg="string") | {"required": ["app"]}, open_),
            Tool("beroun_close_app", "Closes an app by id: it saves its place first.", _schema(app="string") | {"required": ["app"]}, close_),
            Tool("beroun_notifications", "Lists the launcher's notification panel: id, time, app and text of each notification, newest first, and whether do-not-disturb is on.", _schema(), notes_),
            Tool("beroun_dismiss_notification", "Takes one notification out of the panel by its `id` (from beroun_notifications).", {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, note_dismiss_),
            Tool("beroun_clear_notifications", "Clears the notification panel: all of it, or only the notifications of the app `app`.", _schema(app="string"), notes_clear_),
            Tool("beroun_do_not_disturb", "Turns do-not-disturb on or off (`on`: true / false): with it on notifications still show but come without sound and vibration.", {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}, dnd_),
            Tool("beroun_clipboard_read", "Reads the device's clipboard (text): what the user copied with Copy or Cut in a field, and on a simulator what is on the host's clipboard.", _schema(), clipboard_read_),
            Tool("beroun_clipboard_write", "Puts `text` on the device's clipboard, for the user to paste into a field with Paste (SELECT, then A, in the keyboard's menu).", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, clipboard_write_),
            Tool("beroun_message_box", "Shows the user a message over whatever is on the screen and waits for the answer (the cursor chooses, A confirms, B closes). `kind`: ok (the default), okcancel, yesno, yesnocancel; or `buttons` (1 to 4 texts). `icon`: none, info, question, warning, error. `timeout`: seconds to wait (default 60). Returns the button the user chose. Use it to ask something that needs a decision on the device, not for ordinary replies.",
                 {"type": "object", "properties": {"title": {"type": "string"}, "text": {"type": "string"}, "kind": {"type": "string"}, "buttons": {"type": "array", "items": {"type": "string"}}, "icon": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["text"]}, message_box_),
            Tool("beroun_play_sound", "Plays a system sound: notify, message, ok, warning, error, click, alarm, ring. Do-not-disturb silences all but alarm and ring, unless `force` is true.",
                 {"type": "object", "properties": {"name": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["name"]}, play_sound_),
            Tool("beroun_vibrate", "Runs the vibration motor once: `pattern` a name (tap, short, long, double, alarm, ring; short by default) or `milliseconds`, a list of whole milliseconds, on, off, on... (at most 20 steps, 2 s each, 10 s in all); `strength` 0..100. Silent when vibration is off or do-not-disturb is on (alarm and ring excepted), unless `force`.",
                 {"type": "object", "properties": {"pattern": {"type": "string"}, "milliseconds": {"type": "array", "items": {"type": "integer"}}, "strength": {"type": "integer"}, "force": {"type": "boolean"}}}, vibrate_),
            Tool("beroun_stop_vibration", "Stops a vibration that is running.", _schema(), vibrate_stop_),
            Tool("beroun_vibration_setting", "Turns the device's vibration on or off for everything (`on`: true / false): the owner's setting, change it only when asked.", {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}, vibration_setting_),
            *os_tools,
            Tool("beroun_display_status", "The external (HDMI) screen: whether a cable is in, its size, which screen has the focus (the cursor, A and B go there), the app on top and the stack of apps.", _schema(), display_status_),
            Tool("beroun_display_open", "Puts the big page of an app on the external (HDMI) screen: it goes on top of the stack, the one under it waits. `arg`: what the page opens with. Fails with no screen, or for an app that has no big page.", _schema(app="string", arg="string") | {"required": ["app"]}, display_open_),
            Tool("beroun_display_close", "Takes an app's big page off the external screen: the app under it comes back, or the cat splash.", _schema(app="string") | {"required": ["app"]}, display_close_),
            Tool("beroun_display_focus", "Moves the focus (the cursor, A and B) to the `console` or the `external` screen, as the SELECT button does.", _schema(to="string") | {"required": ["to"]}, display_focus_),
            Tool("beroun_screen_text", "The text an app's window shows." + app_hint, _schema(app="string", screen="string"), page("dom_text", [])),
            Tool("beroun_ui_tree", "What can be pressed or typed into in an app's window, numbered: use the number as `ref` in beroun_click / beroun_type." + app_hint, _schema(app="string", screen="string"), page("dom_tree", [])),
            Tool("beroun_click", "Presses an element of an app's window: `ref` (from beroun_ui_tree) or `text` (a part of its label)." + app_hint, _schema(app="string", screen="string", ref="integer", text="string"), page("dom_click", ["ref", "text"])),
            Tool("beroun_type", "Types `value` into a field of an app's window: `ref` (from beroun_ui_tree) or `text` (a part of the field's label)." + app_hint, _schema(app="string", screen="string", ref="integer", text="string", value="string"), page("dom_type", ["ref", "text", "value"])),
            Tool("beroun_key", "Sends a key (ArrowUp, Enter, Escape, a letter...) to an app's window, as the console's buttons do." + app_hint, _schema(app="string", screen="string", key="string"), page("dom_key", ["key"])),
        ],
        "Apps with their own group (app-<id>) offer their own, better tools: use those first, and use_skill for the app's skills. These tools are for the rest and for what a group does not cover: "
        "read the window (beroun_screen_text), see what can be pressed (beroun_ui_tree), then press / type by `ref`. After a press look again: the window may have changed and the numbers with it.")


# ---- agents -------------------------------------------------------------------------------------------------------------
def _signature(s):
    """plan_week(goal: string, week?: string) [long]"""
    params = ", ".join(f"{k.rstrip('?')}{'?' if v.endswith('?') else ''}: {v.rstrip('?')}" for k, v in (s.get("input") or {}).items())
    return f"{s['name']}({params})" + (" [long: a task, ask again with agent_status]" if s.get("long") else "") + (" [effect outside its app]" if s.get("confirm") else "")


def agent_catalog(agents):
    lines = []
    for a in agents:
        lines.append(f"{a['key']}: {a['description']}")
        lines += [f"  - {_signature(s)}: {s['description']}" for s in a.get("skills", [])]
    return "\n".join(lines)


def task_text(t):
    """What a task is now, for the assistant: the state, then what it does next."""
    head = f"Task {t['id']} ({t['agent']} / {t['skill']})"
    st = t["state"]
    if st == "completed":
        out = t.get("output")
        return f"{head} is completed.\nResult: " + (out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)) if out is not None else f"{head} is completed."
    if st == "failed":
        return f"{head} failed: {t.get('error') or 'no reason given'}."
    if st == "canceled":
        return f"{head} was canceled."
    if st == "input-required":
        choices = t.get("choices")
        if choices:
            how = (f"The answer is one of: {', '.join(choices)}. Ask the owner with beroun_message_box (buttons: {json.dumps(choices, ensure_ascii=False)}; the cursor chooses, A confirms, B closes) "
                   f"and give the text of the button they chose with agent_answer (task_id {t['id']}). If they close the box or do not answer, ask again in the chat, or cancel the task with agent_cancel.")
        else:
            how = f"Ask the owner (in the chat, or by voice if you are talking) and give their answer with agent_answer (task_id {t['id']})."
        wait = ""
        if t.get("ask_expires"):
            wait = f" The question waits about {max(1, round((t['ask_expires'] - time.time()) / 60))} min; unanswered, the task fails."
        return f"{head} asks: {t.get('question')}\n{how}{wait}"
    return f"{head} is {st}" + (f" ({t['progress']})" if t.get("progress") else "") + f". Look again later with agent_status (task_id {t['id']}); other work can go on meanwhile."


def asking_text(asking):
    """The tasks that wait for the owner now, for the guidance: an earlier turn may have started them."""
    if not asking:
        return ""
    return ("\nWaiting for the owner's answer now (ask them, then agent_answer; agent_status shows the choices and the time left):\n" +
            "\n".join(f"  - task {t['id']} ({t['agent']} / {t['skill']}): {t.get('question')}" + (f" [one of: {', '.join(t['choices'])}]" if t.get("choices") else "") for t in asking))


def agents_group(agents, asking=()):
    """The agents of the apps (docs/architecture.md): autonomous helpers an app publishes. The assistant gives a task and gets a result; how the agent works is its own business."""
    def call(fn, *args):
        try:
            return result([{"type": "text", "text": task_text(fn(*args))}])
        except RuntimeError as e:   # the shell's refusal (unknown agent or skill, a wrong parameter, a task in the wrong state)
            return result([{"type": "text", "text": str(e)}], True)

    task_id = {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}
    return Group(
        "agents", "Agents of the apps: autonomous helpers; give one a task and get the result (agent_list, agent_start, agent_status, agent_answer, agent_cancel)",
        [
            Tool("agent_list", "Lists the agents of the apps with their skills (name, parameters, whether it is a long task or has an effect outside its app).", {"type": "object", "properties": {}},
                 lambda _args: {"text": agent_catalog(shell.agents(True)) or "(no agents)"}),
            Tool("agent_start", "Gives an agent a task: `agent` (its key, app/id), `skill`, `input` (an object with the skill's parameters). A short skill returns its result at once; a long one returns a task to look at later.",
                 {"type": "object", "properties": {"agent": {"type": "string"}, "skill": {"type": "string"}, "input": {"type": "object"}}, "required": ["agent", "skill"]},
                 lambda args: call(shell.agent_start, args["agent"], args["skill"], args.get("input") or {})),
            Tool("agent_status", "The state of a task: working (with progress), asking the owner something, completed with the result, failed with the reason, canceled.", task_id,
                 lambda args: call(shell.agent_task, args["task_id"])),
            Tool("agent_answer", "Gives the answer to the question a task asked (`task_id`, `answer`): the task goes on.",
                 {"type": "object", "properties": {"task_id": {"type": "string"}, "answer": {"type": "string"}}, "required": ["task_id", "answer"]},
                 lambda args: call(shell.agent_answer, args["task_id"], str(args.get("answer", "")))),
            Tool("agent_cancel", "Cancels a task that is still running.", task_id, lambda args: call(shell.agent_cancel, args["task_id"])),
        ],
        "Agents of the apps (key: what it is for, and its skills):\n" + agent_catalog(agents) +
        "\nPick the agent by what it is for and give it the task with agent_start. A skill marked [effect outside its app] changes something outside the agent's own app (sends, deletes, changes a setting): "
        "weigh it as you do any such action of your own. When a task asks a question, the owner answers it, not you, unless they told you the answer. "
        "A question that nobody answers in time fails its task." + asking_text(asking))


# ---- all of it ----------------------------------------------------------------------------------------------------------
def groups(skill_names=skill_names_of, log=print):
    """The groups as of now: beroun, agents (when any is published), one per app the assistant can drive."""
    out = [beroun_group()]
    published = shell.agents()
    if published:
        out.append(agents_group(published, shell.asking()))
    for a in shell.apps():
        try:
            out.append(app_group(a, skill_names))
        except Exception as e:  # one app's broken section must not cost the others
            log(f"beroun-tools: app {a.get('id')} not offered: {e!r}")
    return out
