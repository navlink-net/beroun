#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Packages of OS Beroun's own apps for the store.

    scripts/beroun-pack.py OUT_DIR [app ...]      the apps named, or every app in PUBLISHED

Each app is taken from git HEAD (never the working tree: another change may be half done there) and written as
OUT_DIR/<id>-<version>.zip, with OUT_DIR/<id>-<version>.json beside it: the listing's name, description, category
and the icon's path in the zip, which scripts/beroun-publish.sh sends with the package. A firmware manifest needs no
"beroun" field, a package does: it gets manifest.BEROUN_VERSION when it has none. A package that breaks the device's
rules (appinstall.package_errors) is not written.

Not in PUBLISHED, they are updated with the firmware: Browser (a native program), Calendar and Clock (their services'
ports are the system's, below 49000), Ratatosk (a module service), Store (apps.manage); Ext A / Ext B are hidden test
apps."""
import io, json, os, shutil, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SIM, "runtime"))
import appinstall, manifest  # noqa: E402

# id -> (category, description) of the listing; the name is the manifest's
PUBLISHED = {
    "audioplayer": ("Media application", "Music from the SD card: a file, a folder or a playlist, with pause, skip and seek."),
    "videoplayer": ("Media application", "Videos from the SD card, one or a whole folder, with pause, skip and seek."),
    "calculator": ("Tools", "Key in a sum on the pad and read the result."),
    "notepad": ("Tools", "Plain-text notes: write, read and open them on the screen."),
    "files": ("Tools", "The folders and files of the SD card."),
    "communicator": ("Messenger", "Mail, WhatsApp, Telegram, Signal and Slack in one place, with Caroline to read and answer."),
    "knights": ("Games", "Knights & Ladies, a single-player game for the console."),
    "pirates": ("Games", "Pirates, a single-player game for the console."),
    "quidditch": ("Games", "A single-player match against the AI as one of the seven players of a team."),
    "settings": ("System", "Interface language, keyboard layouts, date, time, time zone, network time and app updates."),
    "wifi": ("System", "The connection state, the networks around and connecting to one."),
}


def git(*args, binary=False):
    out = subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=SIM, capture_output=True, check=True).stdout
    return out if binary else out.decode()


# The compiler of each target: a native part is built per target, statically on Linux so that one build runs on any distribution.
# An Android target needs the NDK and the host's `.so` shape (stage 5): not here yet. A target that cannot be built stops the package.
TOOLCHAINS = {"linux-x86_64": ["gcc", "-static"], "linux-arm64": ["aarch64-linux-gnu-gcc", "-static"]}
# An Android build is a shared library for the native host (it has `beroun_main`), made by the NDK's clang (Linux NDK, $ANDROID_NDK_ROOT), aligned for 16 KB pages
_NDK = os.environ.get("ANDROID_NDK_ROOT") or "/root/android/ndk/27.3.13750724"
for _t, _triple in (("android-arm64", "aarch64-linux-android26"), ("android-x86_64", "x86_64-linux-android26")):
    TOOLCHAINS[_t] = [f"{_NDK}/toolchains/llvm/prebuilt/linux-x86_64/bin/{_triple}-clang", "-shared", "-fPIC", "-Wl,-z,max-page-size=16384"]


def build_binaries(app_id, m, sources, files):
    """Compiles each binary the manifest names, for each of its targets, from the package source's `src/<name>.c` (git HEAD, like the rest), into files["bin/<target>/<name>"]."""
    with tempfile.TemporaryDirectory(prefix="beroun-pack-") as tmp:
        for name, per in m["binaries"].items():
            src = sources.get(f"src/{name}.c")
            if src is None:
                raise SystemExit(f"{app_id}: binary {name}: src/{name}.c is not in git HEAD")
            c = os.path.join(tmp, f"{name}.c")
            with open(c, "wb") as f:
                f.write(src)
            for target, path in per.items():
                tool = TOOLCHAINS.get(target)
                if not tool or not shutil.which(tool[0]):
                    raise SystemExit(f"{app_id}: no compiler for {target} here ({tool[0] if tool else 'target not supported yet'}): the package is not written")
                out = os.path.join(tmp, f"{name}-{target}")
                r = subprocess.run([*tool, "-O2", "-s", "-o", out, c], capture_output=True)
                if r.returncode:
                    raise SystemExit(f"{app_id}: {target} did not build:\n" + r.stderr.decode(errors="replace"))
                files[path] = open(out, "rb").read()


def pack(app_id, out_dir):
    # apps/<id>/ is a firmware app that is also published; packages/<id>/ is only for the store (its `src/` holds the native parts' sources and is not shipped)
    for root in ("apps", "packages"):
        prefix = f"{root}/{app_id}/"
        names = [n for n in git("ls-tree", "-r", "--name-only", "HEAD", prefix).splitlines()
                 if "/__pycache__/" not in n and not n.endswith(".pyc")]
        if names:
            break
    else:
        raise SystemExit(f"{app_id}: no such app in git HEAD")
    files = {n[len(prefix):]: git("show", f"HEAD:{n}", binary=True) for n in names}
    sources = {k: files.pop(k) for k in [k for k in files if k.startswith("src/")]} if root == "packages" else {}
    m = json.loads(files["manifest.json"])
    m.setdefault("beroun", manifest.BEROUN_VERSION)
    errs = appinstall.package_errors(m) + manifest.validate(m, None)
    if errs:
        raise SystemExit(f"{app_id}: not a package: " + "; ".join(errs))
    if m.get("binaries"):
        build_binaries(app_id, m, sources, files)
    files["manifest.json"] = (json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode()
    base = os.path.join(out_dir, f"{app_id}-{m['version']}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(files):
            z.writestr(rel, files[rel])
    with open(base + ".zip", "wb") as f:
        f.write(buf.getvalue())
    category, description = PUBLISHED.get(app_id, ("", ""))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"id": app_id, "version": m["version"], "name": m["name"], "category": category,
                   "description": description, "icon": m["icon"]}, f, ensure_ascii=False, indent=1)
    print(f"{base}.zip  {m['name']} {m['version']}  {len(files)} files, {len(buf.getvalue())} bytes")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    for app_id in sys.argv[2:] or PUBLISHED:
        pack(app_id, out_dir)


if __name__ == "__main__":
    main()
