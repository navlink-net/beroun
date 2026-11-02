# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""imagefmt - the OS Beroun image file, its signature and its encryption (docs/TARGETS.md; IntelliCat Simulator/docs/image-ota-plan.md).

An image is one file, beroun-<version>-<board>.img: a tar of three members
    image.json       {"format", "board", "arch", "version", "build", "built", "key_id", "recovery_keys", "payload", "payload_size",
                      "squashfs_size", "squashfs_sha256", "encrypted", "enc"}
    image.sig        the base64 Ed25519 signature over the bytes of image.json
    system.enc       the system, encrypted     (or system.squashfs: the same, in the clear; only an unlocked device takes it)
The simulator's system is OS Beroun's own files and the board's layer packed as a squashfs (no Linux in it: the host has one).

SIGNATURE: image.json is signed (Ed25519, key_id "dev" or "release", keys in config/system-keys.json). It holds the size and the SHA-256 of the system,
so the signature covers the system too. The device checks it BEFORE it decrypts anything.
ENCRYPTION: X25519 + HKDF-SHA256 + AES-256-GCM, with the DEVELOPER's key: the image key comes from the shared secret of the developer's static X25519 key ("sender")
and the device's key ("enc"), with a salt of the image. Only the two PRIVATE keys can make that secret: the developer's (which only the developer has) and the device's
(the boot ROM's / secure storage on a console; the simulator keeps it in ~/.intellicat/keys/system-enc.pem). The public keys are in config/system-keys.json and
anybody may read them, but nobody else can make an image that opens: no other hand can encrypt a firmware for the device, as none can sign one. The device takes
an image only from the sender it knows (enc.sender must be the key in the file). The system is encrypted in chunks of 1 MiB: nonce = 8 random bytes + the chunk
number, additional data = the chunk number and whether it is the last one (a chunk cannot be moved, dropped or the end cut off).
A check that fails raises ImageError with a sentence the device can put on its screen. Needs `cryptography` only to decrypt; signing is checked by appinstall's
stdlib Ed25519.
"""
import base64
import hashlib
import json
import os
import re
import struct
import tarfile

import appinstall

FORMAT = 1
CHUNK = 1 << 20
TAG = 16
ALG = "x25519-hkdf-sha256-aes256gcm"
PAYLOADS = ("system.enc", "system.squashfs")
MAX_META = 64 * 1024
HKDF_INFO = b"beroun-image-v1"


class ImageError(Exception):
    pass


def version_key(v):
    """'1.10.2' -> (1, 10, 2): versions compare by their numbers."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def load_keys(path):
    """config/system-keys.json: {"dev": <base64 of the 32-byte Ed25519 public key>, "release": <same or "">, "sender": <the developer's X25519 public key>, "enc": <the device's X25519 public key>} -> {name: bytes}."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: base64.b64decode(v) for k, v in raw.items() if not k.startswith("_") and v}


def load_device_key(path):
    """The device's private X25519 key (what decrypts images), None when the device has none."""
    try:
        from cryptography.hazmat.primitives import serialization
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), None)
    except (OSError, ValueError, ImportError):
        return None


def read_meta(path):
    """Opens the tar, checks its layout and returns (meta, meta bytes, signature bytes)."""
    try:
        tf = tarfile.open(path, "r:")
    except (tarfile.TarError, OSError) as e:
        raise ImageError("not an OS Beroun image (cannot be read)") from e
    with tf:
        ms = tf.getmembers()
        names = sorted(m.name for m in ms)
        if len(ms) != 3 or "image.json" not in names or "image.sig" not in names or not any(n in PAYLOADS for n in names) or not all(m.isreg() for m in ms):
            raise ImageError("not an OS Beroun image (wrong content)")
        meta_m, sig_m = tf.getmember("image.json"), tf.getmember("image.sig")
        if meta_m.size > MAX_META or sig_m.size > MAX_META:
            raise ImageError("not an OS Beroun image (wrong content)")
        meta_bytes = tf.extractfile(meta_m).read()
        sig = tf.extractfile(sig_m).read()
        payload_name = next(n for n in names if n in PAYLOADS)
        payload_size = tf.getmember(payload_name).size
    try:
        meta = json.loads(meta_bytes)
    except ValueError as e:
        raise ImageError("not an OS Beroun image (bad image.json)") from e
    need = ("format", "board", "version", "build", "squashfs_size", "squashfs_sha256", "key_id", "payload", "payload_size")
    if not isinstance(meta, dict) or any(k not in meta for k in need) or meta["payload"] != payload_name or meta["payload_size"] != payload_size:
        raise ImageError("not an OS Beroun image (bad image.json)")
    meta["encrypted"] = bool(meta.get("encrypted"))
    if meta["encrypted"] != (payload_name == "system.enc") or (meta["encrypted"] and not isinstance(meta.get("enc"), dict)):
        raise ImageError("not an OS Beroun image (bad image.json)")
    return meta, meta_bytes, sig


def peek(path):
    """What an image says about itself, for a menu (not checked: the device checks when it flashes)."""
    meta = read_meta(path)[0]
    return {k: meta.get(k) for k in ("board", "arch", "version", "build", "built", "key_id", "encrypted", "squashfs_size")}


def _aes(meta, device_key, sender_pub):
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as e:
        raise ImageError("this device cannot open encrypted images (no crypto library)") from e
    if device_key is None:
        raise ImageError("this device has no key to open an encrypted image")
    e = meta["enc"]
    try:
        sender = base64.b64decode(e["sender"])
        if not sender_pub or sender != sender_pub:
            raise ImageError("the image was not encrypted by the developer this device trusts")
        shared = device_key.exchange(X25519PublicKey.from_public_bytes(sender))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(e["salt"]), info=HKDF_INFO).derive(shared)
        return AESGCM(key), base64.b64decode(e["nonce"])
    except (KeyError, ValueError, TypeError) as ex:
        raise ImageError("the image cannot be opened by this device") from ex


def chunk_aad(index, last):
    return struct.pack(">QB", index, 1 if last else 0)


def iter_plain(path, meta, device_key=None, sender_pub=None):
    """The system, decrypted when it is encrypted, in chunks. Every chunk is authenticated: a changed, moved, dropped or cut-off chunk raises ImageError."""
    tf = tarfile.open(path, "r:")
    f = tf.extractfile(tf.getmember(meta["payload"]))
    try:
        if not meta["encrypted"]:
            while True:
                b = f.read(CHUNK)
                if not b:
                    return
                yield b
        from cryptography.exceptions import InvalidTag
        aes, prefix = _aes(meta, device_key, sender_pub)
        total, n = meta["squashfs_size"], 0
        count = max(1, -(-total // CHUNK))
        for i in range(count):
            plain_len = min(CHUNK, total - i * CHUNK)
            blob = f.read(plain_len + TAG)
            if len(blob) != plain_len + TAG:
                raise ImageError("the image is damaged (it ends too early)")
            try:
                yield aes.decrypt(prefix + struct.pack(">I", i), blob, chunk_aad(i, i == count - 1))
            except InvalidTag as ex:
                raise ImageError("the image is damaged or was not made for this device (decryption failed)") from ex
            n += plain_len
        if f.read(1):
            raise ImageError("the image is damaged (more data than expected)")
    finally:
        f.close()
        tf.close()


def verify(path, board, installed_version, keys, locked=False, device_key=None, progress=None):
    """Everything the device checks before it writes: layout, format, the board, the signature (a locked device takes no dev key and no image in the clear),
    that the system opens and its hash matches, and that the version is not older than the one running (no way back). Returns the meta."""
    meta, meta_bytes, sig = read_meta(path)
    if meta["format"] != FORMAT:
        raise ImageError(f"image format {meta['format']} is not understood")
    if meta["board"] != board:
        raise ImageError(f"this image is for the board {meta['board']}, not {board}")
    key = keys.get(meta["key_id"])
    if key is None or meta["key_id"] in ("enc", "sender"):
        raise ImageError("the image is not signed by a key this device knows")
    if locked and meta["key_id"] == "dev":
        raise ImageError("the device is locked: it takes no image signed with a development key")
    try:
        ok = len(key) == 32 and appinstall.ed25519_verify(key, base64.b64decode(sig), meta_bytes)
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise ImageError("the signature of the image is wrong")
    if locked and not meta["encrypted"]:
        raise ImageError("the device is locked: it takes encrypted images only")
    # the signature is right: now the system is opened (an encrypted one needs the device's key) and measured
    h, done, size = hashlib.sha256(), 0, meta["squashfs_size"]
    for chunk in iter_plain(path, meta, device_key, keys.get("sender")):
        h.update(chunk)
        done += len(chunk)
        if progress:
            progress(done, size)
    if done != size:
        raise ImageError("the image is damaged (wrong size)")
    if h.hexdigest() != meta["squashfs_sha256"]:
        raise ImageError("the image is damaged (the hash of the system does not match)")
    if version_key(meta["version"]) < version_key(installed_version):
        raise ImageError(f"the image {meta['version']} is older than the system {installed_version}: no way back")
    return meta


def encrypt_chunks(read, total, device_public, sender_private):
    """The developer's side: (enc, chunks) for a system of `total` bytes that `read(n)` gives, for the device whose X25519 public key is `device_public` (32 bytes),
    with the developer's private X25519 key (`sender_private`, an X25519PrivateKey: only the developer has it). `enc` goes into image.json; `chunks` yields the
    ciphertext, chunk after chunk (what the member system.enc holds)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    sender = sender_private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = sender_private.exchange(X25519PublicKey.from_public_bytes(device_public))
    salt, prefix = os.urandom(16), os.urandom(8)
    aes = AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=HKDF_INFO).derive(shared))
    enc = {"alg": ALG, "sender": base64.b64encode(sender).decode(), "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(prefix).decode(), "chunk": CHUNK}
    count = max(1, -(-total // CHUNK))

    def gen():
        for i in range(count):
            plain = read(min(CHUNK, total - i * CHUNK))
            yield aes.encrypt(prefix + struct.pack(">I", i), plain, chunk_aad(i, i == count - 1))
    return enc, gen()


def sha256_of(f, size, progress=None):
    """SHA-256 of what `f` reads (for the builder and the write check)."""
    h, done = hashlib.sha256(), 0
    while True:
        b = f.read(CHUNK)
        if not b:
            break
        h.update(b)
        done += len(b)
        if progress:
            progress(done, size)
    return h.hexdigest()
