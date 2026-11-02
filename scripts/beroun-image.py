#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""beroun-image - makes and checks the image of OS Beroun for a board.

  beroun-image.py keygen [--name dev|release] [--no-passphrase] [--force]
        the DEVELOPER's keys, made by the developer on the developer's machine, under a passphrase (asked, or BEROUN_KEY_PASSPHRASE): the signing key
        ~/.intellicat/keys/system-<name>.pem ("dev": local builds, "release": what a locked device takes) and the sender key system-sender.pem (X25519, what encrypts).
        Nobody but their owner has these two, so nobody else can sign or encrypt a firmware for a device. Also made once: the DEVICE's key system-enc.pem
        (the simulator's stand-in for the key in a console's boot ROM; no passphrase). Only the public halves go to config/system-keys.json (the repository).
  beroun-image.py build [--board DIR] [--out DIR] [--version V] [--key-id dev|release] [--no-encrypt] [--simulate-boot-failure]
  beroun-image.py verify FILE [--board DIR] [--locked] [--device-key PEM]

An image is signed (Ed25519) and encrypted (X25519 + AES-256-GCM) by default: runtime/imagefmt.py. --simulate-boot-failure makes an image whose system "never
comes up" (to try the rollback of a device that does not start); it is for the simulator's tests only.
For the simulator an image is only OS Beroun's own files and the board's layer packed as a squashfs (no Linux is built: the simulator runs on the host's);
it takes about a minute. The console's image, a whole Linux system, is another matter (Buildroot, the plan's stage 10). Run it where `mksquashfs` is (WSL).
The board (default: BEROUN_BOARD_DIR, runtime/board.py) names the image: board.env's BOARD_NAME.
"""
import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OS_DIR = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(OS_DIR, "runtime"))
import board as boardmod  # noqa: E402
import imagefmt  # noqa: E402

KEYS_JSON = os.path.join(OS_DIR, "config", "system-keys.json")
DEV_KEY = os.path.expanduser("~/.intellicat/keys/system-dev.pem")
ENC_KEY = os.path.expanduser("~/.intellicat/keys/system-enc.pem")
SENDER_KEY = os.path.expanduser("~/.intellicat/keys/system-sender.pem")


def sign_key_path(name):
    return os.path.expanduser(f"~/.intellicat/keys/system-{name}.pem")
# what is not part of the system: Caroline's checkout (her edition is added if built), the docs, the tests' leftovers
SKIP = ("caroline", "docs", ".gitattributes", ".gitignore")
# built programs that the tree does not track but the system needs (if they are built)
BUILT = ("native/screen/build", "native/face/build", "native/face/bin", "caroline/dist-intellicat")


def die(msg):
    print(f"beroun-image: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args):
    return subprocess.run(["git", "-C", OS_DIR, *args], capture_output=True, text=True).stdout.strip()


def env_version():
    for line in open(os.path.join(OS_DIR, "config", "versions.env"), encoding="utf-8"):
        if line.startswith("INTELLICAT_VERSION="):
            return line.split("=", 1)[1].split("#")[0].strip().strip('"')
    die("no INTELLICAT_VERSION in config/versions.env")


def passphrase(confirm=False):
    """The developer's passphrase: BEROUN_KEY_PASSPHRASE, or asked at the terminal."""
    pw = os.environ.get("BEROUN_KEY_PASSPHRASE")
    if pw is None:
        if not sys.stdin.isatty():
            die("the developer's keys are under a passphrase: set BEROUN_KEY_PASSPHRASE or run this in a terminal")
        import getpass
        pw = getpass.getpass("passphrase of the developer's keys: ")
        if confirm and getpass.getpass("again: ") != pw:
            die("the passphrases differ")
    if not pw:
        die("an empty passphrase (--no-passphrase is for throwaway development keys)")
    return pw.encode()


def load_private(path):
    from cryptography.hazmat.primitives import serialization
    data = open(path, "rb").read()
    try:
        return serialization.load_pem_private_key(data, None)
    except TypeError:   # encrypted: the developer's passphrase
        try:
            return serialization.load_pem_private_key(data, passphrase())
        except ValueError:
            die("wrong passphrase")


def cmd_keygen(a):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    sign_path = sign_key_path(a.name)
    todo = [(sign_path, Ed25519PrivateKey, a.name, True), (SENDER_KEY, X25519PrivateKey, "sender", True), (ENC_KEY, X25519PrivateKey, "enc", False)]
    todo = [t for t in todo if a.force or not os.path.exists(t[0])]
    if not todo:
        die(f"{sign_path}, {SENDER_KEY} and {ENC_KEY} exist (--force makes new ones; images made for the old keys stop being accepted)")
    os.makedirs(os.path.dirname(sign_path), exist_ok=True)
    enc_alg = serialization.NoEncryption()
    if a.no_passphrase:
        print("WARNING: no passphrase: these keys are for throwaway development only")
    elif any(t[3] for t in todo):
        enc_alg = serialization.BestAvailableEncryption(passphrase(confirm=True))
    keys = json.load(open(KEYS_JSON, encoding="utf-8")) if os.path.exists(KEYS_JSON) else {}
    keys["_comment"] = ("PUBLIC keys only (base64, 32 bytes) of the system images; the private halves are their owner's. 'dev': signs local builds (a locked device refuses it); "
                        "'release': the developer's signing key; 'sender': the developer's X25519 key, the only one that can encrypt an image for a device; "
                        "'enc': the device's X25519 key (its private half is in the device's boot ROM / secure storage).")
    for path, gen, name, developers in todo:
        k = gen.generate()
        with open(path, "wb") as f:
            f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc_alg if developers else serialization.NoEncryption()))
        os.chmod(path, 0o600)
        keys[name] = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        print(f"{name}: {path}" + (" (under a passphrase)" if developers and not a.no_passphrase else ""))
    keys.setdefault("dev", "")
    keys.setdefault("release", "")
    order = ["_comment", "dev", "release", "sender", "enc"]
    with open(KEYS_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump({k: keys[k] for k in order if k in keys}, f, indent=2)
        f.write("\n")
    print(f"public halves: {KEYS_JSON}")


def stage_tree(dest):
    """Copies what the system is made of into dest."""
    files = [f for f in git("ls-files", "-z").split("\0") if f and f.split("/")[0] not in SKIP]
    for rel in files:
        src = os.path.join(OS_DIR, rel)
        if not os.path.isfile(src):
            continue
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copy2(src, os.path.join(dest, rel))
    for rel in BUILT:
        src = os.path.join(OS_DIR, rel)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, rel), dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "node_modules", "*.o"))


def cmd_build(a):
    if not shutil.which("mksquashfs"):
        die("mksquashfs is not installed (run this in WSL: apt install squashfs-tools)")
    board_dir = a.board or boardmod.dir()
    os.environ["BEROUN_BOARD_DIR"] = board_dir
    name, arch = boardmod.env("BOARD_NAME"), boardmod.env("BOARD_ARCH")
    if not name:
        die(f"{board_dir}/board.env has no BOARD_NAME")
    key_path = sign_key_path(a.key_id)
    if not os.path.exists(key_path):
        die(f"no signing key {key_path}: the developer makes it with beroun-image.py keygen --name {a.key_id}")
    key = load_private(key_path)
    version = a.version or env_version()
    sha = git("rev-parse", "--short", "HEAD") or "nogit"
    build = sha + ("-dirty" if git("status", "--porcelain") else "")
    with tempfile.TemporaryDirectory(prefix="beroun-image-") as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(os.path.join(root, "beroun"))
        stage_tree(os.path.join(root, "beroun"))
        shutil.copytree(board_dir, os.path.join(root, "board"), ignore=shutil.ignore_patterns("README.md"))
        sq = os.path.join(tmp, "system.squashfs")
        r = subprocess.run(["mksquashfs", root, sq, "-noappend", "-quiet", "-all-root", "-no-xattrs", "-comp", "xz", "-mkfs-time", "0", "-all-time", "0"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            die("mksquashfs: " + (r.stderr or r.stdout).strip())
        size = os.path.getsize(sq)
        with open(sq, "rb") as f:
            digest = imagefmt.sha256_of(f, size)
        encrypt = not a.no_encrypt
        meta = {"format": imagefmt.FORMAT, "board": name, "arch": arch, "version": version, "build": build,
                "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "squashfs_size": size, "squashfs_sha256": digest,
                "key_id": a.key_id, "recovery_keys": boardmod.env("BEROUN_RECOVERY_KEYS"), "encrypted": encrypt,
                "payload": "system.enc" if encrypt else "system.squashfs"}
        if a.simulate_boot_failure:
            meta["simulate_boot_failure"] = True
        payload = sq
        if encrypt:
            pub = imagefmt.load_keys(KEYS_JSON).get("enc")
            if not pub or not os.path.exists(SENDER_KEY):
                die("no device key in config/system-keys.json or no developer's sender key (beroun-image.py keygen)")
            sender = load_private(SENDER_KEY)
            payload = os.path.join(tmp, "system.enc")
            with open(sq, "rb") as src, open(payload, "wb") as dst:
                meta["enc"], chunks = imagefmt.encrypt_chunks(src.read, size, pub, sender)
                for c in chunks:
                    dst.write(c)
        meta["payload_size"] = os.path.getsize(payload)
        meta_bytes = (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode()
        sig = base64.b64encode(key.sign(meta_bytes))
        out = os.path.abspath(a.out)
        os.makedirs(out, exist_ok=True)
        img = os.path.join(out, f"beroun-{version}-{name}.img")
        with tarfile.open(img + ".part", "w:", format=tarfile.USTAR_FORMAT) as tf:
            for member, data in (("image.json", meta_bytes), ("image.sig", sig)):
                ti = tarfile.TarInfo(member)
                ti.size, ti.mtime = len(data), 0
                tf.addfile(ti, io.BytesIO(data))
            tf.add(payload, arcname=meta["payload"], filter=lambda ti: (setattr(ti, "mtime", 0), setattr(ti, "uid", 0), setattr(ti, "gid", 0), ti)[3])
        os.replace(img + ".part", img)
    print(f"{img}\n  board {name}, version {version}, build {build}, system {size / 1048576:.1f} MB, signed ({a.key_id})" + (", encrypted" if encrypt else ", NOT encrypted"))


def cmd_verify(a):
    if a.board:
        os.environ["BEROUN_BOARD_DIR"] = a.board
    keys = imagefmt.load_keys(KEYS_JSON)
    try:
        m = imagefmt.verify(a.file, boardmod.env("BOARD_NAME"), a.installed, keys, locked=a.locked, device_key=imagefmt.load_device_key(a.device_key))
    except imagefmt.ImageError as e:
        print(f"REFUSED: {e}")
        sys.exit(2)
    print(f"ok: board {m['board']}, version {m['version']}, build {m['build']}, signed by {m['key_id']}, "
          + ("encrypted, opens with this device's key" if m["encrypted"] else "not encrypted"))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--force", action="store_true")
    k.add_argument("--name", default="dev", choices=("dev", "release"))
    k.add_argument("--no-passphrase", action="store_true")
    b = sub.add_parser("build")
    b.add_argument("--board")
    b.add_argument("--out", default=os.path.expanduser("~/beroun-images"))
    b.add_argument("--version")
    b.add_argument("--key-id", default="dev", choices=("dev", "release"))
    b.add_argument("--no-encrypt", action="store_true")
    b.add_argument("--simulate-boot-failure", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("file")
    v.add_argument("--board")
    v.add_argument("--locked", action="store_true")
    v.add_argument("--installed", default="0")
    v.add_argument("--device-key", default=ENC_KEY)
    a = p.parse_args()
    {"keygen": cmd_keygen, "build": cmd_build, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    main()
