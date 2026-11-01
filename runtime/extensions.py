# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Extensions of the shell daemon: code and pages that a target adds to OS Beroun without the OS knowing about it.

A target (the simulator, a device build) keeps its own pieces next to the OS: the simulator's console window, its service menu. The OS
loads them at start and gives them a small door into the shell; nothing in the OS refers to them by name.

Where: $BEROUN_EXTENSIONS, folders separated like PATH. Each folder in it that has an `extension.json` is one extension:

    {"id": "console", "name": "Simulator console", "python": "ext.py"}      ("python" is optional)

`ext.py` defines `register(api)`; `api` is the Door below. A broken extension is logged and left out, the shell starts without it.

What the door gives:
    api.shelld            the shell module: its helpers and constants (PORT, HOME_ROOT, fs_resolve, ilog ...)
    api.root              the extension's own folder
    api.page(url, file)   serve root/file at url (an HTML page, an image)
    api.static(prefix, d) serve the folder root/d under the url prefix (the prefix ends with "/")
    api.get(path, fn)     GET  path: fn(handler, query) -> a JSON-able object (errors map as in the file API: 403, 404, 409 ...)
    api.post(path, fn)    POST path: fn(handler, body) -> a JSON-able object
    api.get/post(..., prefix=True)  the route is every path that starts with `path`; fn gets the rest as `handler.ext_rest`
    api.raw(path, fn, prefix=False) any method: fn(handler) answers by itself (a WebSocket, a stream)
    api.trust(url)        a page whose requests the shell's local-only calls accept (see H._console_only)
"""
import importlib.util
import json
import os
import traceback


class Door:
    def __init__(self, ext, shelld):
        self.ext, self.shelld, self.root = ext, shelld, ext.root

    def page(self, url, file):
        self.ext.owner.pages[url] = (self.root, file)

    def static(self, prefix, directory):
        self.ext.owner.statics.append((prefix, os.path.join(self.root, directory)))

    def _route(self, method, path, fn, prefix, fs):
        self.ext.owner.routes.append((method, path, prefix, fn, fs))

    def get(self, path, fn, prefix=False):
        self._route("GET", path, fn, prefix, True)

    def post(self, path, fn, prefix=False):
        self._route("POST", path, fn, prefix, True)

    def raw(self, path, fn, prefix=False, methods=("GET", "POST")):
        for m in methods:
            self._route(m, path, fn, prefix, False)

    def trust(self, url):
        self.ext.owner.trusted.add(url)


class Extension:
    def __init__(self, owner, root, manifest):
        self.owner, self.root, self.manifest = owner, root, manifest
        self.id = manifest["id"]


class Extensions:
    def __init__(self, log=print):
        self.log = log
        self.loaded = []
        self.pages = {}      # url -> (folder, file)
        self.statics = []    # (url prefix, folder)
        self.routes = []     # (method, path, prefix, fn, fs)
        self.trusted = set()

    def load(self, shelld, spec=None):
        spec = os.environ.get("BEROUN_EXTENSIONS", "") if spec is None else spec
        for base in [p for p in spec.split(os.pathsep) if p]:
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                root = os.path.join(base, name)
                mf = os.path.join(root, "extension.json")
                if not os.path.isfile(mf):
                    continue
                try:
                    manifest = json.load(open(mf, encoding="utf-8"))
                    ext = Extension(self, root, manifest)
                    py = manifest.get("python")
                    if py:
                        s = importlib.util.spec_from_file_location("beroun_ext_" + ext.id.replace("-", "_"), os.path.join(root, py))
                        mod = importlib.util.module_from_spec(s)
                        s.loader.exec_module(mod)
                        mod.register(Door(ext, shelld))
                    self.loaded.append(ext)
                    self.log(f"extension {ext.id} loaded from {root}")
                except Exception:
                    self.log(f"extension {name} not loaded:\n{traceback.format_exc()}")

    def resolve_static(self, path):
        """(folder, relative file) for a url one of the extensions serves, else None."""
        if path in self.pages:
            return self.pages[path]
        for prefix, folder in self.statics:
            if path.startswith(prefix) and len(path) > len(prefix):
                return folder, path[len(prefix):]
        return None

    def find(self, method, path):
        for m, p, prefix, fn, fs in self.routes:
            if m == method and (path.startswith(p) if prefix else path == p):
                return p, prefix, fn, fs
        return None
