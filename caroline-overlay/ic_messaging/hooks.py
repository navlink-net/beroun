# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""How the overlay reaches into Caroline's process without editing her code (called from sitecustomize.py):

* her plugin loader finds modules under app/plugins/: this folder's `ic_plugins/` is appended to that package's path, so the tools of
  tools.py are loaded like her own;
* her FastAPI app (app.main.app) gets the router of api.py and the start/stop of the registry, added right after her module is
  imported (her own routes and middleware are untouched).
"""
import importlib.abc, importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(os.path.dirname(HERE), "ic_plugins")
_busy = set()


class _AfterImport(importlib.abc.MetaPathFinder):
    """Imports `name` as usual and runs `after(module)` once it has been executed."""

    def __init__(self, name, after):
        self.name, self.after = name, after

    def find_spec(self, name, path, target=None):
        if name != self.name or name in _busy:
            return None
        _busy.add(name)
        try:
            spec = importlib.util.find_spec(name)
        finally:
            _busy.discard(name)
        if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
            return spec
        orig, after = spec.loader.exec_module, self.after

        def exec_module(module):
            orig(module)
            try:
                after(module)
            except Exception as e:                      # the overlay must never stop her backend from starting
                print(f"intellicat-overlay: messaging hook for {name} failed: {e!r}", file=sys.stderr, flush=True)
        spec.loader.exec_module = exec_module
        return spec


def _plugins_loaded(module):
    if PLUGINS_DIR not in module.__path__:
        module.__path__.append(PLUGINS_DIR)


def _loader_loaded(module):
    """discover_plugins() lists only its own folder (pkgutil.iter_modules([package_dir])): its pkgutil is replaced by one that also lists ours."""
    import pkgutil

    class _Pkgutil:
        def __getattr__(self, name):
            return getattr(pkgutil, name)

        @staticmethod
        def iter_modules(paths=None, prefix=""):
            found = list(pkgutil.iter_modules(paths, prefix))
            if paths is not None and any(os.path.basename(os.path.normpath(str(p))) == "plugins" for p in paths):
                found += [m for m in pkgutil.iter_modules([PLUGINS_DIR], prefix) if m.name not in {x.name for x in found}]
            return iter(found)
    module.pkgutil = _Pkgutil()
    _loader_wrap(module)
    try:  # the apps of the device as her tool groups (ic_apps)
        import ic_apps.hooks
        ic_apps.hooks.loader_loaded(module)
    except Exception as e:
        print(f"intellicat-overlay: apps not offered to Caroline: {e!r}", file=sys.stderr, flush=True)


def _loader_wrap(module):
    """her send tools leave a note (actors.py) so that the echo of what she sent is stored with her as the actor."""
    from . import actors
    orig = module.wrap_tool
    module.wrap_tool = lambda plugin_name, plugin_tool: orig(plugin_name, actors.wrap_plugin_tool(plugin_tool))


def _main_loaded(module):
    import ic_messaging
    reg = ic_messaging.get_registry()
    from .api import build_router
    module.app.include_router(build_router(reg, fake=ic_messaging.is_fake()))
    module.app.on_event("startup")(reg.start)
    module.app.on_event("shutdown")(reg.stop)
    print("intellicat-overlay: Communicator backend mounted (/api/ic/messages)", file=sys.stderr, flush=True)
    try:   # her tools for another assistant of the slot (ic_bridge): the router, and its reminders out of her scheduler's callback
        import ic_bridge.api, ic_bridge.reminders
        module.app.include_router(ic_bridge.api.build_router())
        module._on_reminder_due = ic_bridge.reminders.wrap(module._on_reminder_due)   # read by her startup, after this import
        print("intellicat-overlay: her tools for the assistant mounted (/api/ic/bridge)", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"intellicat-overlay: her tools not offered to the assistant: {e!r}", file=sys.stderr, flush=True)


def install():
    if os.environ.get("IC_MESSAGING_OFF"):
        return
    sys.meta_path.insert(0, _AfterImport("app.slack_channel", _slack_loaded))
    sys.meta_path.insert(0, _AfterImport("app.whatsapp_channel", _whatsapp_loaded))
    sys.meta_path.insert(0, _AfterImport("app.signal_channel", _signal_loaded))
    sys.meta_path.insert(0, _AfterImport("app.telegram_channel", _telegram_loaded))
    sys.meta_path.insert(0, _AfterImport("app.plugins", _plugins_loaded))
    sys.meta_path.insert(0, _AfterImport("app.plugins.loader", _loader_loaded))
    sys.meta_path.insert(0, _AfterImport("app.main", _main_loaded))


def _whatsapp_loaded(module):
    """Her module starts one Node sidecar per account and hands its stdout lines (qr, ready, loggedOut, message...) to a callback of its own, which turns them into text for
    Caroline. SidecarProcess is replaced by a function that builds the real one with that callback wrapped: the adapter sees every line first, hers runs unchanged."""
    from . import channels, whatsapp_adapter
    channels.attach(module, "whatsapp")          # the added accounts are back in her module before her start function runs
    orig = module.SidecarProcess

    def sidecar_process(*args, **kw):
        on_line, label = kw.get("on_line"), str(kw.get("label") or "")
        if on_line is not None and label.startswith("whatsapp_"):
            account = label[len("whatsapp_"):]

            def tapped(line, _on=on_line, _account=account):
                whatsapp_adapter.tap(_account, line)
                return _on(line)
            kw["on_line"] = tapped
        return orig(*args, **kw)
    module.SidecarProcess = sidecar_process


def _signal_loaded(module):
    """Two functions of her module are the places where Signal's data passes: `_dispatch_rpc_line(account, msg, inject)` gets every JSON-RPC line of a daemon (incoming messages
    and, as sync messages, what the owner wrote on the phone), and `_render_and_open_qr(workspace, account, uri)` gets the pairing URI of a link. Both are wrapped; hers run unchanged."""
    from . import channels, signal_adapter
    channels.attach(module, "signal")
    dispatch, draw = module._dispatch_rpc_line, module._render_and_open_qr

    def dispatch_rpc_line(account, msg, inject_proactive):
        signal_adapter.tap(account, msg)
        return dispatch(account, msg, inject_proactive)

    def render_and_open_qr(workspace_dir, account, data):
        result = draw(workspace_dir, account, data)
        signal_adapter.tap_qr(account, data)           # after hers: the picture is on disk by now (qr_image allows a few seconds of difference)
        return result
    module._dispatch_rpc_line, module._render_and_open_qr = dispatch_rpc_line, render_and_open_qr


def _telegram_loaded(module):
    """Nothing is tapped (the adapter reads her live client); only the added accounts are put back into her module."""
    from . import channels
    channels.attach(module, "telegram")


def _slack_loaded(module):
    """Her Socket Mode connection is the only one; it calls _account_for_event with every message payload, so the adapter reads the events there."""
    from . import slack_adapter
    orig = module._account_for_event

    def account_for_event(payload):
        slack_adapter.tap(payload)
        return orig(payload)
    module._account_for_event = account_for_event
