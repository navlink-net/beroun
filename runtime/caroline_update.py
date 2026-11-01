# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Caroline's updates on OS Beroun (the shell's half of Caroline's docs/UPDATE_EVERYWHERE_PLAN.md).

Caroline's backend looks for a signed release itself (app/update_service.py, platform `beroun`), downloads the artifact and checks it against the
signed manifest; it stops at "ready" and says what to do (`/api/update/status`: action install | ask | none, by the rule: inactive installs by itself,
in use asks). Installing is this module's job, because only the shell can stop her backend and start it again:

* the firmware's copy of her (`dist-intellicat`, read-only, part of the system) is never touched;
* an update is applied to a writable copy of it in the owner's data (`<data>/caroline-code/current`): the overlay zip replaces her portable code
  (backend-py/app, wwwroot, skills, a few files; never the runtime) with the overlay installer of Caroline itself, loaded from the copy that runs;
* `choose()` says which tree runs: the copy when it is newer than the firmware and not marked failed, the firmware otherwise. A copy that the firmware
  has outgrown (a new firmware with a newer Caroline) is deleted;
* a copy that does not start (its supervisor keeps dying, or its backend never answers) is marked failed (`fall_back`): the copy before the update runs again,
  or the firmware's when there is none, as the store does with apps. The copy before is kept next to the new one (hard links: it costs no space) until the new one has answered.

Pure standard library; the Caroline of the checkout is loaded by path, never imported as a package (nothing of her is wanted in this process).
"""
import importlib.util, json, os, shutil, sys, threading, time

PLATFORM = "beroun"
RUNTIME_DEFAULT = 1      # the runtime number of this device (what an update cannot carry: Python, Node, models, ic-face-view); raised by a file ROOT/RUNTIME when provisioning changes it
ASK_AGAIN_S = 4 * 3600   # a "later" to the question is asked again after this, never sooner
POLL_S = 30


def _load(app, name):
    path = os.path.join(app, "backend-py", "app", name + ".py")
    spec = importlib.util.spec_from_file_location("ic_caroline_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # her dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


def _link_or_copy(src, dst, *, follow_symlinks=True):
    """A hard link where the folder allows it (the overlay replaces files, it never writes through them), a copy otherwise."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=follow_symlinks)


def read_version(app):
    try:
        with open(os.path.join(app, "backend-py", "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def runtime_number(root):
    """The runtime number the backend is told (CAROLINE_RUNTIME for her update client)."""
    try:
        with open(os.path.join(root, "RUNTIME"), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return RUNTIME_DEFAULT


class CodeLayer:
    def __init__(self, firmware_app, data_dir, log=print):
        self.firmware, self.log = firmware_app, log
        self.dir = os.path.join(data_dir, "caroline-code")
        self.current = os.path.join(self.dir, "current")
        self.previous = os.path.join(self.dir, "previous")
        self.meta_path = os.path.join(self.dir, "state.json")
        self.download_dir = os.path.join(data_dir, "caroline-update")   # where her update client keeps the verified artifact
        self.lock = threading.Lock()

    # -- what runs -------------------------------------------------------------------------------------
    def _meta(self):
        try:
            with open(self.meta_path, encoding="utf-8") as f:
                m = json.load(f)
            return m if isinstance(m, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, meta):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.replace(tmp, self.meta_path)

    def _is_newer(self, a, b):
        return _load(self.firmware, "release_manifest").is_newer(a, b)

    def has_client(self):
        """Whether the firmware's Caroline can update at all (a pin that is older than the update client has no VERSION and no installer)."""
        return all(os.path.isfile(os.path.join(self.firmware, "backend-py", p)) for p in ("VERSION", os.path.join("app", "overlay_install.py"), os.path.join("app", "update_service.py")))

    def choose(self):
        """The tree to run: the copy of an update when it is newer than the firmware's Caroline, whole and not failed; else the firmware's."""
        with self.lock:
            if not os.path.isdir(self.current):
                return self.firmware
            fw, cur = read_version(self.firmware), read_version(self.current)
            ok = (fw is not None and cur is not None and os.path.isfile(os.path.join(self.current, "backend-py", "supervisor.py"))
                  and cur not in self._meta().get("failed", []))
            try:
                ok = ok and self._is_newer(cur, fw)   # a firmware that is as new or newer makes the downloaded copy obsolete
            except Exception:
                ok = False
            if ok:
                return self.current
            if cur is not None and cur in self._meta().get("failed", []):
                return self.firmware   # kept for the record: a failed copy is not retried, and the same version is not downloaded again over it
            self.log(f"caroline update: the copy {cur} is obsolete or broken against the firmware's {fw}; deleted")
            shutil.rmtree(self.current, ignore_errors=True)
            return self.firmware

    def mark_failed(self, version):
        with self.lock:
            meta = self._meta()
            meta["failed"] = sorted(set(meta.get("failed", [])) | {version})
            self._save(meta)
        self.log(f"caroline update: {version} failed to start; the earlier Caroline runs again")

    def fall_back(self, version):
        """The tree of `version` does not work: mark it, put the copy from before it back when there is a good one. Returns the tree to run."""
        self.mark_failed(version)
        with self.lock:
            prev = read_version(self.previous)
            if prev is not None and prev not in self._meta().get("failed", []):
                shutil.rmtree(self.current, ignore_errors=True)
                os.replace(self.previous, self.current)
                self.log(f"caroline update: back to the copy {prev}")
        return self.choose()

    def proven(self):
        """The updated tree has answered: the copy from before it is not needed any more."""
        with self.lock:
            shutil.rmtree(self.previous, ignore_errors=True)

    def failed(self):
        return self._meta().get("failed", [])

    # -- install ---------------------------------------------------------------------------------------
    def apply(self, zip_path, version):
        """Applies the verified overlay to the writable copy (made from the firmware's tree the first time). The backend must be stopped.
        Returns the tree to run. Raises on anything wrong, with the copy as it was."""
        overlay = _load(self.firmware, "overlay_install")
        with self.lock:
            if version in self._meta().get("failed", []):
                raise RuntimeError(f"{version} failed to start on this device before")
            fresh = not os.path.isdir(self.current)
            shutil.rmtree(self.previous, ignore_errors=True)
            if not fresh:   # the copy before the update, kept for the way back
                shutil.copytree(self.current, self.previous, symlinks=True, copy_function=_link_or_copy)
            if fresh:
                os.makedirs(self.dir, exist_ok=True)
                nxt = self.current + ".new"
                shutil.rmtree(nxt, ignore_errors=True)
                shutil.copytree(self.firmware, nxt, symlinks=True)
                os.replace(nxt, self.current)
            try:
                replaced = overlay.apply_overlay(zip_path, self.current)
            except Exception:
                if fresh:
                    shutil.rmtree(self.current, ignore_errors=True)
                raise
            got = read_version(self.current)
            if got != version:   # the artifact is not the release the manifest announced: not trusted, the copy is made again from the firmware's next time
                shutil.rmtree(self.current, ignore_errors=True)
                raise RuntimeError(f"the overlay is {got}, the release {version}")
        self.log(f"caroline update: {version} applied ({len(replaced)} paths)")
        return self.choose()


class Updater:
    """Watches her backend's update state and installs by the rule. `caroline` is the runtime.caroline.Caroline; `ask(version)` shows the question to the owner and
    returns at once; its answer comes back through `confirm()`; `notify(text)` puts a line in the notification panel."""

    def __init__(self, caroline, ask, notify, log=print):
        self.c, self.ask, self.notify, self.log = caroline, ask, notify, log
        self.asked = {}      # version -> time the question was last put
        self.installing = False
        self.refused = set()  # versions that were refused or failed here: not tried again, not announced again
        self.last = None     # the last status seen, for status()
        threading.Thread(target=self._loop, daemon=True, name="caroline-update").start()

    def status(self):
        s = dict(self.last or {})
        s.update(supported=self.c.layer.has_client(), running_tree=self.c.app, firmware=read_version(self.c.layer.firmware), failed=self.c.layer.failed(),
                 installing=self.installing)
        return s

    def check_now(self):
        return self.c.api("POST", "/api/update/check")

    def confirm(self):
        """The owner said yes (the box, or Caroline for the owner): the backend turns "ask" into "install", and this looks at once."""
        r = self.c.api("POST", "/api/update/confirm")
        if r is not None:
            self.step()
        return r

    def step(self):
        if self.installing or not self.c.layer.has_client():
            return
        st = self.c.api("GET", "/api/update/status")
        self.last = st
        if not st or not st.get("ok"):
            return
        act, ver = st.get("action"), st.get("version")
        if ver and (ver in self.refused or ver in self.c.layer.failed()):
            return
        if act == "install" and st.get("path") and ver:
            self.install(st["path"], ver)
        elif act == "ask" and ver and time.time() - self.asked.get(ver, 0) > ASK_AGAIN_S:
            self.asked[ver] = time.time()
            self.notify(f"Caroline {ver} is ready to install. She is busy: it waits for your yes (Settings > Caroline), or installs by itself when she is idle.")
            self.ask(ver)

    def install(self, path, version):
        self.installing = True
        try:
            self.log(f"caroline update: installing {version}")
            self.c.stop_for_update()
            try:
                tree = self.c.layer.apply(path, version)
                self.c.use_tree(tree)
            except Exception as e:
                self.refused.add(version)
                self.log(f"caroline update: {version} refused: {e}")
                self.notify(f"Caroline {version} could not be installed ({e}); the earlier version stays.", )
            self.c.ensure()
        finally:
            self.installing = False

    def _loop(self):
        while True:
            time.sleep(POLL_S)
            try:
                if self.c.proc is not None and self.c.proc.poll() is None:
                    self.step()
            except Exception as e:
                self.log(f"caroline update: watcher error: {e}")
