# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""More than one account for her WhatsApp, Signal and Telegram modules, without editing them.

Her modules know the account names in `ACCOUNTS = ("self", "owner")` and keep one entry per name in a few module-level dicts (status, sidecar, pending
calls...); the start function makes one supervised loop for every name in ACCOUNTS, and her plugin tools accept a name when `is_valid_account` (which reads
ACCOUNTS from the module) says so. So an added account is only a new name: `register()` adds it to ACCOUNTS and to those dicts, and `start_loop()` starts the
loop of that one name through her own start function, with ACCOUNTS narrowed to it for the moment. Her tools then take `account="owner2"` like any other.

The names: "owner2", "owner3"... for more accounts of the owner, "self2"... for Caroline's own. Where her code tells the two roles apart by `account == "self"`
(the note she gets about a new message, whether she may answer), an added "self2" is treated like the owner's: she asks first. That is the safe side.

The accounts that were added are kept in `ic_messaging_accounts.json` in the workspace and put back into her modules when they are imported (hooks.py), before her
start function runs, so that her own startup loops include them after a restart.
"""
import importlib, json, logging, os

log = logging.getLogger("ic_messaging")

SPEC = {
    "whatsapp": {"module": "app.whatsapp_channel", "start": "start_whatsapp_channel", "status": "WhatsAppChannelStatus",
                 "dicts": {"_sidecar": None, "_pending": dict, "_login_requested": False, "_login_requested_tab_id": None}},
    "signal": {"module": "app.signal_channel", "start": "start_signal_channel", "status": "SignalChannelStatus",
               "dicts": {"_writer": None, "_pending": dict, "_next_id": 0, "_login_requested_tab_id": None}},
    "telegram": {"module": "app.telegram_channel", "start": "start_telegram_channel", "status": "TelegramChannelStatus", "dicts": {}},
}
LABELS = {"owner": "the owner's", "self": "your own"}
_started = {}          # service -> {account: True}: the loops that were started (by her start function, or by start_loop)
_args = {}             # service -> the arguments her start function was called with (workspace, inject_proactive)


def split(name):
    """("owner", 2) from "owner2"; ("self", 1) from "self"."""
    role = name.rstrip("0123456789")
    rest = name[len(role):]
    return role, int(rest) if rest else 1


def file_path():
    from . import _workspace
    return os.path.join(_workspace(), "ic_messaging_accounts.json")


def load():
    """{service: [account names]} of the added accounts."""
    try:
        return json.load(open(file_path()))
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(os.path.dirname(file_path()), exist_ok=True)
    json.dump(data, open(file_path(), "w"))


def remember(service, name):
    d = load()
    if name not in d.setdefault(service, []):
        d[service].append(name)
        _save(d)


def forget(service, name):
    d = load()
    if name in d.get(service, []):
        d[service].remove(name)
        _save(d)


def register(service, name, module=None):
    """Make `name` an account of her module for `service`. Idempotent. `module` is for the tests (a stand-in of the module)."""
    spec = SPEC[service]
    m = module or importlib.import_module(spec["module"])
    if name in m.ACCOUNTS:
        return
    role, slot = split(name)
    m.ACCOUNTS = tuple(m.ACCOUNTS) + (name,)
    label = LABELS.get(role, role) + (f" #{slot}" if slot > 1 else "")
    if hasattr(m, "_ACCOUNT_LABEL"):
        m._ACCOUNT_LABEL[name] = label
    m._status[name] = getattr(m, spec["status"])(account=name)
    for attr, initial in spec["dicts"].items():
        getattr(m, attr)[name] = initial() if callable(initial) else initial
    if service == "signal":                                    # every daemon listens on a port of its own; hers are 7592 and 7593
        m.DAEMON_PORT_BY_ACCOUNT[name] = 7600 + 2 * slot + (0 if role == "owner" else 1)


def attach(module, service):
    """Called right after her module was imported (hooks.py): put the saved accounts back, and learn what her start function is called with."""
    for name in load().get(service, []):
        try:
            register(service, name, module)
        except Exception as e:
            log.warning("%s account %s not restored: %r", service, name, e)
    spec = SPEC[service]
    orig = getattr(module, spec["start"], None)
    if orig is None:
        return

    def start(workspace_dir, inject_proactive, *a, **kw):
        _args[service] = (workspace_dir, inject_proactive)
        tasks = orig(workspace_dir, inject_proactive, *a, **kw)
        _started[service] = {n: True for n in module.ACCOUNTS}
        return tasks
    setattr(module, spec["start"], start)
    module._ic_start_orig = orig


def start_loop(service, name, module=None):
    """Start the supervised loop of an account that was added while her backend runs (her start function already ran). Before that, or when the loop
    exists, nothing to do: her startup makes it."""
    m = module or importlib.import_module(SPEC[service]["module"])
    if service not in _args or _started.get(service, {}).get(name):
        return []
    all_names = m.ACCOUNTS
    m.ACCOUNTS = (name,)
    try:
        tasks = m._ic_start_orig(*_args[service])
    finally:
        m.ACCOUNTS = all_names
    _started[service][name] = True
    return tasks
