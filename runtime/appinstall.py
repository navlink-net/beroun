# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Installing, updating and removing the apps of OS Beroun that come from the app store.

A package is a zip from apps.navlink.net: the app's folder (manifest.json at the root) plus `sign.list`, which the store's arbiter adds at approval:
    <path>: <sha256 hex>\\n   for every file, sorted by path, then   \\n---SIGNATURE---\\n   and the base64 Ed25519 signature over the lines above.
This module is the device's side and trusts nothing the server said: it checks the signature against the public key built into OS Beroun, the hash of every file, that
sign.list names exactly the files of the zip, that nothing is compiled code or a link or leaves the folder, and the manifest (runtime/manifest.py, plus the rules of a package:
no native program, no module or privileged service, python3 as the only program). It never imports shelld: the shell registers and unregisters what this module puts on disk.

The steps of an install or an update, so that a failure puts everything back (the shell drives them):
    p = store.prepare(data)      checks and unpacks BESIDE the installed folder; nothing changes yet
    store.activate(p)            the old folder (if any) is moved aside, the new one takes its place
    store.finish(p)              everything worked: the backup is deleted
    store.rollback(p)            something failed after activate: the old folder is put back
    store.remove(app_id)         an app from the store: the folder is deleted; a preinstalled app: hidden. A system app of the firmware is never removed (store.kind)
"""
import base64, hashlib, io, json, os, re, shutil, stat, zipfile

import manifest

SIGN_LIST = "sign.list"
MARKER = "\n---SIGNATURE---\n"
MAX_ZIP = 50 << 20
MAX_UNPACKED = 200 << 20
MAX_FILES = 2000
BINARY_EXT = (".so", ".dll", ".exe", ".dylib", ".a", ".o", ".node", ".pyd", ".jar", ".apk", ".elf", ".bin")
BINARY_MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
# The public key of the store's signing key (base64, 32 bytes), built into OS Beroun: the arbiter's key made 2026-10-30 (tunnel_cat deploy/DEPLOY.md,
# "OS Beroun packages: the signing key"); INTELLICAT_BEROUN_PUBKEY overrides it (tests, a private store). With no key nothing can be installed.
STORE_PUBLIC_KEY = "QB1C1qEdJYMKgJc+ZnOBzSajY5lh8reSoofGw8L5bDU="
PORT_RANGE = (49000, 49999)   # the ports of a package's own services; below are the system's and the built-in apps'



class InstallError(Exception):
    """An install, update or removal that cannot be done; `str(e)` says why, `code` is for the callers: bad-package, bad-signature, not-higher, missing-capability, ..."""
    def __init__(self, message, code="bad-package"):
        super().__init__(message)
        self.code = code


# ---- Ed25519 verification (RFC 8032), stdlib only: a signature is checked once per install, so the speed of plain integers does not matter -------------------------
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _x_recover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    if (x * x - xx) % _P:
        raise ValueError("not a point")
    return _P - x if x & 1 else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_x_recover(_BY) % _P, _BY % _P, 1, _x_recover(_BY) * _BY % _P)


def _add(a, b):
    A, B = (a[1] - a[0]) * (b[1] - b[0]) % _P, (a[1] + a[0]) * (b[1] + b[0]) % _P
    C, Dd = 2 * a[3] * b[3] * _D % _P, 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _eq(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _decode(s):
    if len(s) != 32:
        raise ValueError("bad point")
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    if y >= _P:
        raise ValueError("bad point")
    x = _x_recover(y)
    if x & 1 != sign:
        x = _P - x
    return (x, y, 1, x * y % _P)


def ed25519_verify(public_key, signature, message):
    """True when `signature` (64 bytes) is a valid Ed25519 signature of `message` by `public_key` (32 bytes)."""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        a, r = _decode(public_key), _decode(signature[:32])
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
        return _eq(_mul(s, _B), _add(r, _mul(h, a)))
    except ValueError:
        return False


def store_public_key():
    """The key packages must be signed with, as bytes; b"" when none is configured."""
    raw = os.environ.get("INTELLICAT_BEROUN_PUBKEY") or STORE_PUBLIC_KEY
    try:
        key = base64.b64decode(raw, validate=True) if raw else b""
    except ValueError:
        return b""
    return key if len(key) == 32 else b""


# ---- the package --------------------------------------------------------------------------------------------------------------------------------------------------
def _safe_name(name):
    parts = name.split("/")
    return not (name.startswith("/") or "\\" in name or "\x00" in name or ":" in name or any(p in ("", ".", "..") for p in parts))


def read_package(data, public_key):
    """Checks a package against the key and returns {name: bytes} of its files (without sign.list). Raises InstallError."""
    if not public_key:
        raise InstallError("this device has no store key: nothing can be installed", "no-key")
    if not data or len(data) > MAX_ZIP:
        raise InstallError("the package is empty or larger than %d MB" % (MAX_ZIP >> 20))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise InstallError("the package is not a zip file")
    infos = [i for i in zf.infolist() if not i.filename.endswith("/")]
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise InstallError("the package has too many files or is too large unpacked")
    files, seen, compiled = {}, set(), set()
    for i in infos:
        n = i.filename
        low = n.lower()
        if not _safe_name(n):
            raise InstallError("file name not allowed: %r" % n)
        if low in seen:
            raise InstallError("file twice (case-insensitive): %r" % n)
        seen.add(low)
        if stat.S_ISLNK(i.external_attr >> 16):
            raise InstallError("links are not allowed: %r" % n)
        files[n] = zf.read(i)
        if n != SIGN_LIST and (low.endswith(BINARY_EXT) or ".so." in os.path.basename(low) or files[n][:4] in BINARY_MAGIC or files[n][:2] == b"MZ"):
            compiled.add(n)   # allowed only as a binary the manifest names (checked once the signature is: binary_errors)
    raw = files.pop(SIGN_LIST, None)
    if raw is None:
        raise InstallError("the package is not signed", "bad-signature")
    text = raw.decode("utf-8", "replace")
    if MARKER not in text:
        raise InstallError("sign.list has no signature", "bad-signature")
    lines, sig = text.split(MARKER, 1)
    try:
        signature = base64.b64decode(sig.strip(), validate=True)
    except ValueError:
        raise InstallError("sign.list has a bad signature", "bad-signature")
    if not ed25519_verify(public_key, signature, lines.encode("utf-8")):
        raise InstallError("the signature does not verify against this device's store key", "bad-signature")
    listed = {}
    for ln in lines.rstrip("\n").split("\n"):
        path, sep, digest = ln.partition(": ")
        if not sep or path in listed:
            raise InstallError("sign.list is malformed", "bad-signature")
        listed[path] = digest
    if set(listed) != set(files):
        raise InstallError("sign.list does not name exactly the files of the package", "bad-signature")
    for path, digest in listed.items():
        if hashlib.sha256(files[path]).hexdigest() != digest:
            raise InstallError("the file %r does not match its hash" % path, "bad-signature")
    declared = _declared_binaries(files.get("manifest.json"))
    for n in sorted(compiled | set(declared)):
        why = _binary_error(n, files.get(n), declared.get(n))
        if why:
            raise InstallError("%s: %r" % (why, n))
    return files


# The CPU of an ELF file (e_machine) by the target's cpu part; a binary of a package is an ELF of its target's CPU, in bin/<target>/ and named in the manifest's "binaries".
ELF_MACHINE = {"x86_64": 62, "arm64": 183}


def _declared_binaries(raw):
    """{path: target} of the manifest's "binaries" (what is wrong with the manifest itself is for the manifest rules)."""
    try:
        b = json.loads(raw.decode("utf-8")).get("binaries") if raw else None
    except (ValueError, AttributeError):
        return {}
    out = {}
    if isinstance(b, dict):
        for per in b.values():
            if isinstance(per, dict):
                out.update({p: t for t, p in per.items() if isinstance(p, str) and t in manifest.TARGETS})
    return out


def _binary_error(name, blob, target):
    if target is None:
        return "compiled code is not allowed"
    if blob is None:
        return "a binary the manifest names is not in the package"
    if not name.startswith("bin/%s/" % target):
        return "a binary belongs in bin/%s/" % target
    cpu = target.split("-", 1)[1]
    if len(blob) < 20 or blob[:4] != b"\x7fELF" or blob[4] != 2 or blob[5] != 1 or int.from_bytes(blob[18:20], "little") != ELF_MACHINE[cpu]:
        return "a binary for %s must be a 64-bit little-endian ELF of that CPU" % target
    return None


def package_errors(m):
    """The rules of a package on top of the manifest's own (runtime/manifest.py): code that runs on any device, nothing that belongs to the system."""
    e = []
    if "native" in m:
        e.append("a package may not have a native program")
    if not m.get("beroun"):
        e.append('"beroun" (the lowest OS Beroun version) is required in a package')
    if not isinstance(m.get("frontend"), dict):
        e.append("a package has a web frontend")
    for i, s in enumerate(m.get("services") or []):
        if not isinstance(s, dict):
            continue
        if s.get("host", "process") == "module":
            e.append("services[%d]: a module service is the system's code" % i)
        if s.get("privileged"):
            e.append("services[%d]: privileged is for the system" % i)
        if "port" in s and not (isinstance(s["port"], int) and PORT_RANGE[0] <= s["port"] <= PORT_RANGE[1]):
            e.append("services[%d]: the port of a package is %d-%d" % (i, *PORT_RANGE))
        if s.get("host", "process") == "process" and not _runs_ours(s.get("cmd")):
            e.append("services[%d]: a package runs python3 or a binary of its own only" % i)
    key = "assistant" if "assistant" in m else "caroline"
    for i, c in enumerate((m.get(key) or {}).get("mcp") or []):
        if isinstance(c, dict) and c.get("transport") == "stdio" and not _runs_ours(c.get("cmd")):
            e.append("%s.mcp[%d]: a package runs python3 or a binary of its own only" % (key, i))
    for p in m.get("permissions") or []:
        if p not in ("assistant.ask", "caroline.ask"):
            e.append("permission %r is not for a package" % p)
    for i, a in enumerate(m.get("agents") or []):
        if isinstance(a, dict) and isinstance(a.get("a2a"), dict) and a["a2a"].get("expose"):
            e.append("agents[%d]: an installed app's agent cannot be exposed as an A2A agent yet" % i)
    if isinstance(m.get("external"), dict) and "cmd" in m["external"] and not (_runs_ours(m["external"]["cmd"]) and m["external"]["cmd"][0] != "python3"):
        e.append("external.cmd is allowed in a package only as a binary of its own ({bin:...})")
    return e


def _runs_ours(cmd):
    """A package starts python3 or one of the binaries it carries ({bin:name}, named in its manifest's "binaries"), nothing else."""
    return isinstance(cmd, list) and bool(cmd) and isinstance(cmd[0], str) and (cmd[0] == "python3" or manifest.BIN_REF.fullmatch(cmd[0]) is not None)


def rights(m):
    """What an app asks for, as a set: from its manifest or from the store's summary of one (the arbiter's BerounSummary has the same names). An automatic update is installed
    only when the new version asks for nothing outside the set of the version on the device (shelld.store_auto_update)."""
    out = set()
    for c in m.get("capabilities") or []:
        out.add(("capability", c.get("name") if isinstance(c, dict) else c))
    out |= {("permission", p) for p in m.get("permissions") or []}
    out |= {("service", x.get("id")) for x in m.get("services") or [] if isinstance(x, dict)}
    for a in m.get("agents") or []:
        if isinstance(a, dict):
            out.add(("agent", a.get("id")))
            out |= {("agent permission", a.get("id"), p) for p in a.get("permissions") or []}
            out |= {("camerlengo", a.get("id"), c) for c in a.get("camerlengo") or []}
    return out


def version_tuple(v):
    return tuple(int(x) for x in str(v).split("."))


class Prepared:
    def __init__(self, app_id, version, name, folder, update, old_version, manifest_, kind):
        self.app_id, self.version, self.name, self.folder = app_id, version, name, folder
        self.update, self.old_version, self.manifest, self.kind = update, old_version, manifest_, kind
        self.backup = None


class Store:
    """The apps of the device by where they come from (`kind`):
        system        in the firmware, manifest "system": true (Browser, Settings, ...). The store can only UPDATE them: never remove one, never install one that is not there.
        preinstalled  in the firmware, an ordinary app (Calculator, ...). The store can update it, remove it (it is hidden: the firmware is read-only) and install it again.
        store         everything else: installed from the store, removed for good.
    A newer version of a firmware app, or one installed again, lives in `root` and takes the firmware copy's place."""

    def __init__(self, root, firmware=lambda: {}, public_key=None, device_capabilities=lambda: set(), log=print, in_use=lambda: ({}, {})):
        """root: the folder of installed apps; firmware() -> {id: {"version", "system"}} of the apps in the firmware; device_capabilities() -> names of the capabilities this device has;
        in_use() -> ({service id: app}, {port: app}) of every service on the device, so that an app cannot take the id or the port another app's service has."""
        self.root, self.firmware, self.log, self.in_use = root, firmware, log, in_use
        self.public_key = public_key if public_key is not None else store_public_key()
        self.device_capabilities = device_capabilities
        os.makedirs(root, exist_ok=True)
        self.recover()
        try:
            self.removed = set(json.load(open(os.path.join(root, ".removed.json"), encoding="utf-8")))
        except (OSError, ValueError):
            self.removed = set()

    def _save_removed(self):
        tmp = os.path.join(self.root, ".removed.json.tmp")
        json.dump(sorted(self.removed), open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, os.path.join(self.root, ".removed.json"))

    def recover(self):
        """After a crash in the middle of an update: an unfinished new folder is dropped, a moved-aside old one is put back."""
        for n in sorted(os.listdir(self.root)):
            p = os.path.join(self.root, n)
            if n.startswith(".new-"):
                shutil.rmtree(p, ignore_errors=True)
            elif n.startswith(".old-"):
                target = os.path.join(self.root, n[5:])
                if os.path.isdir(target):
                    shutil.rmtree(target, ignore_errors=True)   # the new version was in place but never finished: the old one wins
                os.rename(p, target)
                self.log("appinstall: restored %s after an unfinished update" % n[5:])

    def path(self, app_id):
        return os.path.join(self.root, app_id)

    def kind(self, app_id):
        """system | preinstalled | store (also for an id the device has never heard of)."""
        fw = self.firmware().get(app_id)
        return "store" if fw is None else "system" if fw.get("system") else "preinstalled"

    def system_ids(self):
        return {i for i, f in self.firmware().items() if f.get("system")}

    def installed_version(self, app_id):
        """The version in the store's folder (an app from the store, or a newer version of a firmware app), None if there is none."""
        try:
            return json.load(open(os.path.join(self.path(app_id), "manifest.json"), encoding="utf-8")).get("version")
        except (OSError, ValueError):
            return None

    def version(self, app_id):
        """The version of the app that is on the device now: the store's folder over the firmware's copy; None if it is not there (not installed, or a firmware app that was removed)."""
        v = self.installed_version(app_id)
        if v is None and app_id in self.firmware() and app_id not in self.removed:
            v = self.firmware()[app_id].get("version")
        return v

    def installed(self):
        """What the store's folder holds: [{id, name, version, ...}]."""
        out = []
        for n in sorted(os.listdir(self.root)):
            if n.startswith("."):
                continue
            try:
                m = json.load(open(os.path.join(self.root, n, "manifest.json"), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"id": m.get("id", n), "name": m.get("name", n), "version": m.get("version", ""), "beroun": m.get("beroun", ""),
                        "capabilities": m.get("capabilities", []), "kind": self.kind(m.get("id", n))})
        return out

    def prepare(self, data):
        files = read_package(data, self.public_key)
        if "manifest.json" not in files:
            raise InstallError("manifest.json must be at the root of the package")
        try:
            m = json.loads(files["manifest.json"].decode("utf-8"))
        except ValueError:
            raise InstallError("manifest.json is not JSON")
        errs = package_errors(m) if isinstance(m, dict) else ["the manifest is not an object"]
        if not errs:
            errs = manifest.validate(m, None)
        if errs:
            raise InstallError("the manifest is refused: " + "; ".join(errs))
        app_id, version = m["id"], m["version"]
        kind = self.kind(app_id)
        if bool(m.get("system")) != (kind == "system"):
            raise InstallError("%r is %s" % (app_id, "a system app of OS Beroun: its package must say \"system\": true" if kind == "system"
                                              else "not a system app of this device: its package may not say \"system\": true"), "system")
        ids, ports = self.in_use()
        for s in m.get("services") or []:
            for what, key, used in (("service id", s.get("id"), ids), ("port", s.get("port"), ports)):
                if key is not None and used.get(key) not in (None, app_id):
                    raise InstallError("the %s %s is used by the app %s" % (what, key, used[key]), "conflict")
        if not manifest.has_target(m):
            raise InstallError("the app has no build for this device (%s)" % manifest.device_target(), "missing-capability")
        lacking = [n for n in manifest.required_capabilities(m) if n not in self.device_capabilities()]
        if lacking:
            raise InstallError("this device lacks what the app cannot work without: " + ", ".join(lacking), "missing-capability")
        old = self.version(app_id)   # a removed firmware app has none: it is installed again, of any version
        if old is not None and version_tuple(version) <= version_tuple(old):
            raise InstallError("version %s is not higher than the installed %s" % (version, old), "not-higher")
        tmp = os.path.join(self.root, ".new-" + app_id)
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            target, ours = manifest.device_target(), manifest.binary_paths(m)
            for name, blob in files.items():
                if name in ours and not name.startswith("bin/%s/" % target):
                    continue   # another target's build: signed with the rest, never unpacked
                dst = os.path.join(tmp, *name.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(blob)
                if name in ours:
                    os.chmod(dst, 0o444 if target.startswith("android") else 0o755)   # a program on Linux; read-only on Android, where the shell loads it
            errs = manifest.validate(m, tmp)   # the files it names (entry, icon, skills) must exist; the folder name is the id only after the move
            errs = [x for x in errs if "differs from the folder name" not in x]
            if errs:
                raise InstallError("the manifest is refused: " + "; ".join(errs))
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return Prepared(app_id, version, m.get("name", app_id), tmp, old is not None, old, m, kind)

    def activate(self, p):
        final = self.path(p.app_id)
        if os.path.isdir(final):   # a newer version of an app the store installed before: the old folder waits aside until the new one works
            p.backup = os.path.join(self.root, ".old-" + p.app_id)
            shutil.rmtree(p.backup, ignore_errors=True)
            os.rename(final, p.backup)
        os.rename(p.folder, final)
        p.folder = final
        if p.app_id in self.removed:   # a firmware app installed again
            self.removed.discard(p.app_id)
            self._save_removed()
            p.was_removed = True

    def finish(self, p):
        if p.backup:
            shutil.rmtree(p.backup, ignore_errors=True)
            p.backup = None

    def rollback(self, p):
        """Put the previous state back: the old folder if there was one, nothing for a first install; a firmware app that was removed stays removed."""
        final = self.path(p.app_id)
        if os.path.isdir(final) and p.folder == final:
            shutil.rmtree(final, ignore_errors=True)
        if p.backup and os.path.isdir(p.backup):
            os.rename(p.backup, final)
        p.backup = None
        if getattr(p, "was_removed", False):
            self.removed.add(p.app_id)
            self._save_removed()
        shutil.rmtree(os.path.join(self.root, ".new-" + p.app_id), ignore_errors=True)

    def discard(self, p):
        """A prepared package that is not going to be activated."""
        shutil.rmtree(p.folder, ignore_errors=True)

    def check_removable(self, app_id):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", app_id):
            raise InstallError("%r is not an app" % app_id, "not-installed")
        kind = self.kind(app_id)
        if kind == "system":
            raise InstallError("%r is a system app of OS Beroun: it can be updated but not removed" % app_id, "system")
        if self.version(app_id) is None:
            raise InstallError("%r is not installed" % app_id, "not-installed")

    def remove(self, app_id):
        """An app from the store: its folder is deleted. A preinstalled app: the store's newer version is deleted too and the firmware copy is hidden (restore() brings it back)."""
        self.check_removable(app_id)
        shutil.rmtree(self.path(app_id), ignore_errors=True)
        if self.kind(app_id) == "preinstalled":
            self.removed.add(app_id)
            self._save_removed()

    def restore(self, app_id):
        """A removed preinstalled app comes back, as the firmware has it (no network needed)."""
        if app_id not in self.removed:
            raise InstallError("%r was not removed" % app_id, "not-installed")
        self.removed.discard(app_id)
        self._save_removed()
