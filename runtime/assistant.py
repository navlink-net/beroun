# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The assistant slot of OS Beroun (docs/assistant-contract.md): the system has one assistant, the manager of the device, and a provider fills the slot.

A provider is described by assistants/<id>/assistant.json and implemented by a subclass of Provider (Caroline: runtime/caroline.py, Claude: runtime/claude_assistant.py).
The Slot knows the providers and which one is in the slot. **The build of the OS chooses it, never the user** (owner, 2026-10-31): assistants/build.json ({"assistant": "<id>"},
Caroline in SW mode when there is none); the environment variable INTELLICAT_ASSISTANT overrides it for a run of the shell (a build step, a test). There is no way to change it on the device.
Only the provider in the slot is the assistant: its chat view is the first tile, R2 is its voice (or its chat), its wizard comes after the login, it answers the apps' requests.
"""
import json, os

FORMAT = 1
FIELDS = {"format", "id", "name", "module", "service", "views", "capabilities", "tools", "files"}   # files: what only this provider brings (paths under ), for scripts/assistant_build.py
VIEWS = {"chat", "voice", "wizard", "settings"}
CAPABILITIES = {"ask", "chat", "voice", "face", "wizard", "update", "messaging"}
TOOLS = {"caroline-plugins", "mcp"}
DEFAULT = "caroline"
# Caroline as the firmware describes her (assistants/caroline/assistant.json): used when a tree has no such folder (a test's copy of the shell), so the shell never runs without her
FALLBACK = {"format": 1, "id": "caroline", "name": "Caroline", "module": "caroline", "service": "caroline-backend",
            "views": {"chat": "caroline", "voice": "caroline-voice", "wizard": "caroline-wizard", "settings": "caroline"},
            "capabilities": ["ask", "chat", "voice", "face", "wizard", "update", "messaging"], "tools": "caroline-plugins"}


class Unavailable(Exception):
    """The assistant's backend does not answer (not running, no account yet, not signed in)."""


class Failed(Exception):
    """It answered with an error, or the connection broke."""


class Timeout(Exception):
    """No answer in time."""


def validate(spec):
    """The errors of a provider's assistant.json (a list; empty when it is right)."""
    e = [f"unknown field '{k}'" for k in sorted(set(spec) - FIELDS)]
    if spec.get("format") != FORMAT:
        e.append(f"format must be {FORMAT}")
    for k in ("id", "name", "module", "service"):
        if not isinstance(spec.get(k), str) or not spec[k]:
            e.append(f"{k} is required")
    views = spec.get("views", {})
    if not isinstance(views, dict) or set(views) - VIEWS or not all(isinstance(v, str) and v for v in views.values()):
        e.append(f"views is an object of view ids: {', '.join(sorted(VIEWS))}")
    caps = spec.get("capabilities", [])
    if not isinstance(caps, list) or set(caps) - CAPABILITIES:
        e.append(f"capabilities is a list of: {', '.join(sorted(CAPABILITIES))}")
    if spec.get("tools") not in TOOLS:
        e.append(f"tools is one of: {', '.join(sorted(TOOLS))}")
    files = spec.get("files", [])
    if not isinstance(files, list) or not all(isinstance(f, str) and f and not f.startswith(("/", "..")) and ".." not in f.split("/") for f in files):
        e.append("files is a list of paths under ")
    return e


def load_specs(root):
    """{id: spec} of the providers of the firmware (assistants/<id>/assistant.json); a wrong one is left out, with the reason printed."""
    out = {}
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        f = os.path.join(root, d, "assistant.json")
        if not os.path.isfile(f):
            continue
        try:
            spec = json.load(open(f, encoding="utf-8"))
        except ValueError as err:
            print(f"assistant {d}: {err}", flush=True)
            continue
        errs = validate(spec) + ([] if spec.get("id") == d else ["id must be the folder's name"])
        if errs:
            print(f"assistant {d} refused: " + "; ".join(errs), flush=True)
            continue
        out[spec["id"]] = spec
    return out


class Provider:
    """What OS Beroun asks of an assistant. A subclass implements what its capabilities promise; what it does not have answers the way "not supported" is said here."""
    spec = {"id": "", "name": "", "views": {}, "capabilities": []}

    def bind(self, spec):
        self.spec = spec
        return self

    @property
    def id(self):
        return self.spec["id"]

    @property
    def name(self):
        return self.spec["name"]

    def view(self, kind):
        """The id of its view of that kind (chat, voice, wizard, settings), or None."""
        return (self.spec.get("views") or {}).get(kind)

    def has(self, capability):
        return capability in (self.spec.get("capabilities") or [])

    # -- its backend's life ---------------------------------------------------------------------------
    def start(self):
        """Called once by the shell after the slot exists: a provider that watches its backend starts doing it here."""

    def installed(self):
        return False

    def wanted(self):
        return False

    def ensure(self):
        pass

    def stop(self):
        pass

    def pid(self):
        return None

    def status(self):
        return {"installed": self.installed(), "running": False, "wanted": bool(self.wanted()), "healthy": False}

    # -- what it does for the system ------------------------------------------------------------------
    def ask(self, app, text, timeout=120.0):
        """An app's request: the answer goes to the app only. Raises Unavailable, Failed or Timeout."""
        raise Unavailable(f"{self.name} does not take requests of apps")

    def chat_port(self):
        """The port of its backend's chat WebSocket (docs/assistant-contract.md, section 4), or None."""
        return None

    def voice_start(self, env, launch):
        """R2: whatever its voice needs, around launch() (which opens its voice view). env: the environment of the screen's programs."""
        launch()

    def voice_end(self):
        """After its voice view has gone."""

    def wizard_needed(self):
        """Whether its first-start wizard has to be shown: True, False, or None (its backend does not answer: no wizard then, never a broken start)."""
        return False

    def wizard_done(self):
        """Whether its wizard was finished or dismissed, without asking its backend (the shell decides at once after a login)."""
        return True

    def update_status(self):
        return {"state": "not_supported", "detail": f"{self.name} is updated with the firmware"}

    def update_check(self):
        return self.update_status()

    def update_now(self):
        return self.update_status()


class Slot:
    """The providers of the firmware and the active one."""

    def __init__(self, specs, providers, build_file, log=print):
        """specs: {id: spec}; providers: {module name: Provider instance}; build_file: the build's choice (assistants/build.json).
        A spec whose module has no implementation is left out."""
        self.providers = {}
        if DEFAULT not in specs and DEFAULT in providers:
            specs = dict(specs, **{DEFAULT: FALLBACK})
        for pid, spec in specs.items():
            impl = providers.get(spec["module"])
            if impl is None:
                log(f"assistant {pid}: no module '{spec['module']}' in this runtime")
                continue
            self.providers[pid] = impl.bind(spec)
        self.file = build_file
        self.log = log

    def ids(self):
        return list(self.providers)

    def get(self, pid):
        return self.providers.get(pid)

    def active_id(self):
        forced = os.environ.get("INTELLICAT_ASSISTANT")
        if forced in self.providers:
            return forced
        try:
            chosen = json.load(open(self.file, encoding="utf-8")).get("assistant")
        except (OSError, ValueError, AttributeError):
            chosen = None
        return chosen if chosen in self.providers else DEFAULT if DEFAULT in self.providers else next(iter(self.providers), None)

    @property
    def active(self):
        return self.providers.get(self.active_id()) or Provider()

    def is_active(self, pid):
        return pid == self.active_id()

    def describe(self):
        """For Settings and the launcher: the providers, the one in the slot, what each can do (read only: the build chose it)."""
        act = self.active_id()
        return {"active": act, "providers": [{"id": p.id, "name": p.name, "capabilities": p.spec.get("capabilities", []), "views": p.spec.get("views", {}),
                                               "installed": bool(p.installed()), "active": p.id == act} for p in self.providers.values()]}
