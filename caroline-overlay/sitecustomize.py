# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""IntelliCat overlay for Caroline's backend: loaded by Python at startup (it is on PYTHONPATH of her process only).

Nothing in the Caroline checkout is changed; what her code cannot be configured to do through the environment is
done here, at start, and listed in PATCHES.md:

* the five SquirrelWisdom service keys (app/sw_service_keys_local.py, gitignored upstream, never in this repository)
  are provided from a file outside the repository: $CAROLINE_SW_KEYS_FILE, default ~/.intellicat/caroline-keys.py
  (the same format as upstream's sw_service_keys_example.py). Without the file the keys are placeholders, so the
  backend starts and every SW call answers with the service's own refusal;
* INTELLICAT_SW_URL (tests only): her code has the SW address hardcoded (https://www.squirrelwisdom.com/), so
  requests to it are redirected, at the HTTP client, to that URL (tests/sw_stub.py);
* her browser tool: the two modules her plugin uses on Linux (app.plugins._linux_app_browser and app.plugins.app_browser_cdp,
  both Playwright + Chromium) are answered by ic_browser_driver.py, which drives the device browser (ic-browser, WPE).
"""
import importlib.abc, importlib.util, os, sys

if os.environ.get("INTELLICAT_KIND") == "android":
    # Android's Python has a sitecustomize of its own (Android/python/sitecustomize.py: the APK's libraries by path); this one takes its place on her PYTHONPATH, so it runs it
    _own = os.path.join(sys.prefix, "lib", "python%d.%d" % sys.version_info[:2], "site-packages", "sitecustomize.py")
    if os.path.isfile(_own):
        _sp = importlib.util.spec_from_file_location("_android_sitecustomize", _own)
        _m = importlib.util.module_from_spec(_sp)
        _sp.loader.exec_module(_m)
    # her code names the C library as on glibc (supervisor.py: ctypes.CDLL("libc.so.6")); bionic's is libc.so
    import ctypes
    _cdll_init = ctypes.CDLL.__init__
    def _android_cdll_init(self, name, *a, **k):
        _cdll_init(self, "libc.so" if name == "libc.so.6" else name, *a, **k)
    ctypes.CDLL.__init__ = _android_cdll_init

_KEYS = ("CAROLINE_SW_KEY", "V2_LOGIN_SERVICE_KEY", "V2_SESSION_LOOKUP_KEY", "EMAIL_SERVICE_KEY", "REFORCE_KEY")
_KEYS_FILE = os.environ.get("CAROLINE_SW_KEYS_FILE", os.path.expanduser("~/.intellicat/caroline-keys.py"))
_SW_HOST = "www.squirrelwisdom.com"


class _KeysFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name != "app.sw_service_keys_local":
            return None
        if os.path.isfile(_KEYS_FILE):
            return importlib.util.spec_from_file_location(name, _KEYS_FILE)
        return importlib.util.spec_from_loader(name, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        for k in _KEYS:
            setattr(module, k, "NOT_CONFIGURED")
        print(f"intellicat-overlay: no SW service keys ({_KEYS_FILE}); SW calls will be refused", file=sys.stderr, flush=True)


sys.meta_path.insert(0, _KeysFinder())


class _BrowserFinder(importlib.abc.MetaPathFinder):
    """app.plugins._linux_app_browser and app.plugins.app_browser_cdp -> ic_browser_driver.py (one file answers for both)."""
    NAMES = ("app.plugins._linux_app_browser", "app.plugins.app_browser_cdp")

    def find_spec(self, name, path, target=None):
        if name not in self.NAMES:
            return None
        return importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(__file__), "ic_browser_driver.py"))


sys.meta_path.insert(0, _BrowserFinder())


class _PageOriginFinder(importlib.abc.MetaPathFinder):
    """app.origin_guard lets the Linux chat page in only from http://127.0.0.1:48767, a constant. A simulator with an instance name serves her page from a shifted port
    (scripts/lib.sh), so the shell names it in CAROLINE_PAGE_ORIGIN, and it is put in place of the constant right after her module is executed: her check reads it on
    every request (main.py and supervisor.py). Without the variable nothing changes."""
    NAME = "app.origin_guard"

    def __init__(self):
        self.busy = False

    def find_spec(self, name, path, target=None):
        origin = os.environ.get("CAROLINE_PAGE_ORIGIN")
        if name != self.NAME or self.busy or not origin:
            return None
        self.busy = True
        try:
            spec = importlib.util.find_spec(name)
        finally:
            self.busy = False
        if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
            return spec
        orig = spec.loader.exec_module

        def exec_module(module):
            orig(module)
            module.LINUX_SHELL_FRONTEND_ORIGIN = origin
        spec.loader.exec_module = exec_module
        return spec


sys.meta_path.insert(0, _PageOriginFinder())


def _redirect_sw(base):
    import httpx
    from urllib.parse import urlsplit
    target = urlsplit(base)

    def moved(request):
        if request.url.host == _SW_HOST:
            request.url = request.url.copy_with(scheme=target.scheme, host=target.hostname, port=target.port)
            request.headers["host"] = target.netloc
        return request

    plain, aio = httpx.Client.send, httpx.AsyncClient.send
    httpx.Client.send = lambda self, request, **kw: plain(self, moved(request), **kw)

    async def send(self, request, **kw):
        return await aio(self, moved(request), **kw)
    httpx.AsyncClient.send = send


if os.environ.get("INTELLICAT_SW_URL"):
    try:
        _redirect_sw(os.environ["INTELLICAT_SW_URL"])
    except Exception as e:  # httpx is part of her runtime; a failure here must not stop the backend
        print(f"intellicat-overlay: SW redirect failed: {e}", file=sys.stderr, flush=True)


# Communicator: the message store, the account registry and the API on her backend, and her tools over the shared store (docs/usage.md)
try:
    import ic_messaging.hooks
    ic_messaging.hooks.install()
except Exception as e:  # a failure here must not stop the backend
    print(f"intellicat-overlay: messaging hooks not installed: {e!r}", file=sys.stderr, flush=True)
