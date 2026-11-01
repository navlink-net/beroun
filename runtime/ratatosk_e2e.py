# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Ratatosk end-to-end encryption v1 for IntelliCat (docs/usage.md; the specification is E2E_SPEC.md of the Ratatosk repository).

A port of the Kotlin reference (`data/e2e/E2eCrypto.kt`, `E2eKeys.kt`, `E2eMessage.kt`) to Python's `cryptography`, checked byte for byte against the canonical
vectors `Ratatosk/e2e/test_vectors_v1.json` by `tests/ratatosk_e2e_test.py`. Everything that needs randomness takes it as an argument (so the vectors reproduce) and has a
`*_random` form that draws it from the system; nothing here touches the network or the disk.

What is in it: the primitives (PBKDF2-HMAC-SHA256, HKDF-SHA256, AES-256-GCM, X25519, Ed25519, length-prefixed AAD, base32), the key record of a user (create, unwrap by
password or by recovery secret, change the password, new recovery secret), the sealing of group keys to a member, and the encryption, signing, opening and verifying of a
message. The state of a chat (where keys live on the server, pins) is the business of runtime/ratatosk.py.
"""
import base64, os, unicodedata
from hashlib import pbkdf2_hmac

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

IV_SIZE, KEY_SIZE = 12, 32
PBKDF2_ITERATIONS = 600_000
RECOVERY_SECRET_BYTES = 16
VERSION = "1"

# domain-separation labels: never reuse one for a second purpose
INFO_WRAP = "ratatosk-e2e-wrap-v1"
INFO_RECOVERY = "ratatosk-e2e-recovery-v1"
INFO_SEAL = "ratatosk-e2e-seal-v1"
LABEL_PRIV = "ratatosk-e2e/priv/v1"
LABEL_SEAL = "ratatosk-e2e/seal/v1"
LABEL_MSG = "ratatosk-e2e/msg/v1"
LABEL_SIG = "ratatosk-e2e/sig/v1"


class E2eError(Exception):
    """A wrong password, a damaged record, a key that was not sealed to this identity."""


def random_bytes(n):
    return os.urandom(n)


# ---- encoding --------------------------------------------------------------------------------------------------------------
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64u_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def length_prefixed(*parts):
    """Every part preceded by its length as 4 bytes big-endian, so distinct field lists can never give the same bytes."""
    return b"".join(len(p).to_bytes(4, "big") + p for p in parts)


def aad(*fields):
    return length_prefixed(*(f.encode("utf-8") for f in fields))


def password_bytes(password):
    """The password as typed, NFC-normalised, UTF-8: every platform must do the same."""
    return unicodedata.normalize("NFC", password).encode("utf-8")


# ---- KDFs and AES-GCM ------------------------------------------------------------------------------------------------------
def pbkdf2_sha256(password, salt, iterations, dk_len=32):
    return pbkdf2_hmac("sha256", password, salt, iterations, dk_len)


def hkdf(ikm, salt, info, length=KEY_SIZE):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt or None, info=info.encode("utf-8")).derive(ikm)


def aes_gcm_seal(key, iv, plaintext, aad_bytes):
    """ciphertext || 16-byte tag"""
    assert len(key) == KEY_SIZE and len(iv) == IV_SIZE
    return AESGCM(key).encrypt(iv, plaintext, aad_bytes)


def aes_gcm_open(key, iv, ciphertext_and_tag, aad_bytes):
    assert len(key) == KEY_SIZE and len(iv) == IV_SIZE
    try:
        return AESGCM(key).decrypt(iv, ciphertext_and_tag, aad_bytes)
    except InvalidTag:
        raise E2eError("authentication failed")


# ---- X25519 / Ed25519 ------------------------------------------------------------------------------------------------------
def x25519_public(priv):
    return X25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def x25519_shared(priv, pub):
    return X25519PrivateKey.from_private_bytes(priv).exchange(X25519PublicKey.from_public_bytes(pub))


def ed25519_public(seed):
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def ed25519_sign(seed, message):
    return Ed25519PrivateKey.from_private_bytes(seed).sign(message)


def ed25519_verify(public_key, signature, message):
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


# ---- base32 for the recovery secret (RFC 4648, no padding) -----------------------------------------------------------------
def base32(b):
    return base64.b32encode(b).decode("ascii").rstrip("=")


def base32_decode(text):
    clean = "".join(c for c in text.upper() if c not in "- ")
    try:
        return base64.b32decode(clean + "=" * (-len(clean) % 8))
    except Exception:
        raise E2eError("bad recovery secret")


def format_recovery_secret(secret):
    """As shown to the user: groups of four separated by hyphens."""
    s = base32(secret)
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4))


# ---- the key record of a user (spec section 3) -----------------------------------------------------------------------------
class Identity:
    """The private key material of one user: the X25519 private key and the Ed25519 seed."""

    def __init__(self, x_priv, sign_seed):
        self.x_priv, self.sign_seed = x_priv, sign_seed

    @property
    def x_pub(self):
        return x25519_public(self.x_priv)

    @property
    def sign_pub(self):
        return ed25519_public(self.sign_seed)

    def blob(self):
        return self.x_priv + self.sign_seed

    @classmethod
    def from_blob(cls, blob):
        if len(blob) != 64:
            raise E2eError("bad private blob size")
        return cls(blob[:32], blob[32:])

    @classmethod
    def random(cls):
        return cls(random_bytes(32), random_bytes(32))


def _pw_wrap_key(password, salt, iterations):
    return hkdf(pbkdf2_sha256(password_bytes(password), salt, iterations, 32), b"", INFO_WRAP)


def _rec_wrap_key(secret, salt):
    return hkdf(secret, salt, INFO_RECOVERY)


def _wrap_aad(kind, pub, sign_pub):
    """Binds a wrapping to the public keys it protects and to which wrapping it is."""
    return aad(LABEL_PRIV, kind, pub, sign_pub)


def create_record(identity, password, recovery_secret, kdf_salt, iv_pw, rec_salt, iv_rec, created, iterations=PBKDF2_ITERATIONS):
    pub, sign_pub = b64u(identity.x_pub), b64u(identity.sign_pub)
    blob = identity.blob()
    ct_pw = aes_gcm_seal(_pw_wrap_key(password, kdf_salt, iterations), iv_pw, blob, _wrap_aad("pw", pub, sign_pub))
    ct_rec = aes_gcm_seal(_rec_wrap_key(recovery_secret, rec_salt), iv_rec, blob, _wrap_aad("rec", pub, sign_pub))
    return {"v": 1, "pub": pub, "sign_pub": sign_pub,
            "kdf": {"alg": "pbkdf2-sha256", "iter": iterations, "salt": b64u(kdf_salt)},
            "wrap_pw": {"iv": b64u(iv_pw), "ct": b64u(ct_pw)},
            "wrap_rec": {"salt": b64u(rec_salt), "iv": b64u(iv_rec), "ct": b64u(ct_rec)},
            "created": created}


def create_random(password, now_seconds, iterations=PBKDF2_ITERATIONS):
    """(record, identity, recovery secret): the secret is to be e-mailed to the user (spec section 3), never stored on the server."""
    ident, secret = Identity.random(), random_bytes(RECOVERY_SECRET_BYTES)
    rec = create_record(ident, password, secret, random_bytes(16), random_bytes(IV_SIZE), random_bytes(16), random_bytes(IV_SIZE), now_seconds, iterations)
    return rec, ident, secret


def _check_record(rec):
    if rec.get("v") != 1:
        raise E2eError("unsupported key record version")
    if (rec.get("kdf") or {}).get("alg") != "pbkdf2-sha256":
        raise E2eError("unsupported kdf")


def _check_identity(rec, ident):
    # the unwrapped keys must match the published ones, otherwise the record was tampered with
    if b64u(ident.x_pub) != rec["pub"] or b64u(ident.sign_pub) != rec["sign_pub"]:
        raise E2eError("unwrapped keys do not match the record")
    return ident


def unwrap_with_password(rec, password):
    _check_record(rec)
    key = _pw_wrap_key(password, b64u_decode(rec["kdf"]["salt"]), int(rec["kdf"]["iter"]))
    blob = aes_gcm_open(key, b64u_decode(rec["wrap_pw"]["iv"]), b64u_decode(rec["wrap_pw"]["ct"]), _wrap_aad("pw", rec["pub"], rec["sign_pub"]))
    return _check_identity(rec, Identity.from_blob(blob))


def unwrap_with_recovery(rec, recovery_text):
    _check_record(rec)
    key = _rec_wrap_key(base32_decode(recovery_text), b64u_decode(rec["wrap_rec"]["salt"]))
    blob = aes_gcm_open(key, b64u_decode(rec["wrap_rec"]["iv"]), b64u_decode(rec["wrap_rec"]["ct"]), _wrap_aad("rec", rec["pub"], rec["sign_pub"]))
    return _check_identity(rec, Identity.from_blob(blob))


def rewrap_password(rec, ident, new_password, new_salt=None, new_iv=None):
    """Password change: only wrap_pw changes (new salt, new iv); wrap_rec stays valid."""
    new_salt, new_iv = new_salt or random_bytes(16), new_iv or random_bytes(IV_SIZE)
    ct = aes_gcm_seal(_pw_wrap_key(new_password, new_salt, int(rec["kdf"]["iter"])), new_iv, ident.blob(), _wrap_aad("pw", rec["pub"], rec["sign_pub"]))
    out = {**rec, "kdf": {**rec["kdf"], "salt": b64u(new_salt)}, "wrap_pw": {"iv": b64u(new_iv), "ct": b64u(ct)}}
    return out


def reissue_recovery(rec, ident):
    """A new recovery secret while signed in; the old one stops working. -> (record, secret)"""
    secret, salt, iv = random_bytes(RECOVERY_SECRET_BYTES), random_bytes(16), random_bytes(IV_SIZE)
    ct = aes_gcm_seal(_rec_wrap_key(secret, salt), iv, ident.blob(), _wrap_aad("rec", rec["pub"], rec["sign_pub"]))
    return {**rec, "wrap_rec": {"salt": b64u(salt), "iv": b64u(iv), "ct": b64u(ct)}}, secret


# ---- group keys sealed to a member (spec sections 4, 5) --------------------------------------------------------------------
def new_group_key():
    return random_bytes(KEY_SIZE)


def new_kid():
    return b64u(random_bytes(6))


def _seal_key(shared, eph_pub, member_pub):
    return hkdf(shared, eph_pub + member_pub, INFO_SEAL)


def _seal_aad(gid, kid, member_id):
    return aad(LABEL_SEAL, gid, kid, member_id)


def seal_group_key(group_key, gid, kid, member_id, member_pub, eph_priv=None, iv=None):
    eph_priv, iv = eph_priv or random_bytes(32), iv or random_bytes(IV_SIZE)
    eph_pub = x25519_public(eph_priv)
    key = _seal_key(x25519_shared(eph_priv, member_pub), eph_pub, member_pub)
    ct = aes_gcm_seal(key, iv, group_key, _seal_aad(gid, kid, member_id))
    return {"v": 1, "eph": b64u(eph_pub), "iv": b64u(iv), "ct": b64u(ct)}


def open_group_key(sealed, gid, kid, member_id, ident):
    """Raises E2eError if the key was not sealed to this identity for these ids."""
    if sealed.get("v") != 1:
        raise E2eError("unsupported sealed key version")
    eph_pub = b64u_decode(sealed["eph"])
    key = _seal_key(x25519_shared(ident.x_priv, eph_pub), eph_pub, ident.x_pub)
    return aes_gcm_open(key, b64u_decode(sealed["iv"]), b64u_decode(sealed["ct"]), _seal_aad(gid, kid, member_id))


# ---- messages (spec section 7) ---------------------------------------------------------------------------------------------
def message_aad(gid, mid, sender, ts, kid):
    """v | gid | id | from | ts | kid, each length-prefixed, in this fixed order."""
    return aad(VERSION, gid, mid, sender, str(ts), kid)


def _signed_bytes(gid, mid, sender, ts, kid, e2_raw):
    return length_prefixed(LABEL_SIG.encode("utf-8"), message_aad(gid, mid, sender, ts, kid), e2_raw)


def plain_json(text, reply=None, attachments=None):
    """The content hidden inside e2, in the order the Android client writes it (text, reply, attachments), compact."""
    import json
    o = {"text": text}
    if reply:
        o["reply"] = {k: reply[k] for k in ("id", "from", "text") if reply.get(k)}
    if attachments:
        o["attachments"] = attachments
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def encrypt_message(group_key, gid, mid, sender, ts, kid, plain, sign_seed, iv=None):
    """-> {"e2", "sig"} (base64url): e2 = iv || ciphertext || tag; sig covers the label, the AAD and e2."""
    iv = iv or random_bytes(IV_SIZE)
    raw = iv + aes_gcm_seal(group_key, iv, plain.encode("utf-8"), message_aad(gid, mid, sender, ts, kid))
    sig = ed25519_sign(sign_seed, _signed_bytes(gid, mid, sender, ts, kid, raw))
    return {"e2": b64u(raw), "sig": b64u(sig)}


MISSING_FIELD, BAD_ENCODING, BAD_SIGNATURE, BAD_TAG = "missing-field", "bad-encoding", "bad-signature", "bad-tag"


def decrypt_message(group_key, gid, mid, sender, ts, kid, e2, sig, sender_sign_pub):
    """-> (plaintext string, None) or (None, why it cannot be shown). The signature is checked first, against the pinned sign_pub (spec 5.1), then the tag."""
    if not e2 or not sig:
        return None, MISSING_FIELD
    try:
        raw, sig_bytes = b64u_decode(e2), b64u_decode(sig)
    except Exception:
        return None, BAD_ENCODING
    if len(raw) < IV_SIZE + 16:
        return None, BAD_ENCODING
    if not ed25519_verify(sender_sign_pub, sig_bytes, _signed_bytes(gid, mid, sender, ts, kid, raw)):
        return None, BAD_SIGNATURE
    try:
        return aes_gcm_open(group_key, raw[:IV_SIZE], raw[IV_SIZE:], message_aad(gid, mid, sender, ts, kid)).decode("utf-8"), None
    except E2eError:
        return None, BAD_TAG
    except UnicodeDecodeError:
        return None, BAD_ENCODING


# ---- attachment files (spec section 7; E2eFile.kt) -------------------------------------------------------------------------
# A random per-file key, AES-256-GCM, the blob is iv(12) || ciphertext || tag(16). `kind` ("file" or "thumb") is bound into the AAD, so a thumbnail cannot pass as the
# file or the other way round although both use the same key. The key travels only inside the encrypted message payload.
LABEL_FILE = "ratatosk-e2e/file/v1"


def file_new_key():
    return random_bytes(KEY_SIZE)


def file_encrypt(key, plain, kind, iv=None):
    iv = iv or random_bytes(IV_SIZE)
    return iv + aes_gcm_seal(key, iv, plain, aad(LABEL_FILE, kind))


def file_decrypt(key, blob, kind):
    """-> the bytes; raises E2eError when the blob is too short, was altered, or the key or the kind is wrong."""
    if len(blob) < IV_SIZE + 16:
        raise E2eError("blob too short")
    return aes_gcm_open(key, blob[:IV_SIZE], blob[IV_SIZE:], aad(LABEL_FILE, kind))
