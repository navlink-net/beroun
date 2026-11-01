# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Ratatosk on IntelliCat, stage 5: the attachments of the chat (docs/usage.md; E2E_SPEC.md section 7, E2eFile.kt, AttachmentLoader.kt and
AttachmentUploader.kt of the Android client are the references).

What a message carries: `attachments: [{type, url, name, thumb?, w?, h?, key, mime?, size?}]` inside the encrypted content (`type`: image | audio | video | file). The file is a
blob `iv || ciphertext || tag` made with a random per-file key (ratatosk_e2e.file_encrypt; the AAD binds `file` or `thumb`), stored with the ordinary `write` command at
`chats/<gid>/files/<12 hex>.<ext>` (an image also gets `<12 hex>_thumb.jpg`, 128 px, encrypted the same way, made HERE: the server's resize API never gets a plaintext) and
fetched with a plain unauthenticated GET of the server's address plus the path.

Receiving: the page never sees a key or an address. It asks this module for the picture bytes (`serve`, decrypted in memory, nothing on disk) or for a copy on the card
(`to_card`: "play" puts a short-lived copy in `.ratatosk-play/` of the card so that Music / Video can open it, bounded and emptied at sign-out; "save" puts one in
`Downloads/Ratatosk/` for good, because the owner asked for it). A blob that does not authenticate is refused, never shown half decrypted.
Sending: `send_file` reads a file of the card, checks the size BEFORE anything is written, asks that the chat can take a message at all, stores the blob (and thumbnail), then appends
the message; if the message cannot be appended the blobs are deleted again. Encryption is always on: nothing is ever stored or sent in the clear.
"""
import base64, json, mimetypes, os, re, shutil, subprocess, urllib.error, urllib.parse, urllib.request, uuid

import ratatosk_e2e as e2e
from ratatosk_chat import attachment_type

MAX_SEND = int(os.environ.get("INTELLICAT_RATATOSK_MAX_FILE", 25 * 1024 * 1024))   # one file; the server's own limit is not known yet (plan, S5a)
MAX_FETCH = 64 * 1024 * 1024
PLAY_BUDGET = 64 * 1024 * 1024
PLAY_DIR = ".ratatosk-play"
SAVE_DIR = os.path.join("Downloads", "Ratatosk")
THUMB_PX, POSTER_PX = 128, 320


class AttachError(Exception):
    def __init__(self, message, code="attachment"):
        super().__init__(message)
        self.code = code


def safe_name(name, default="file"):
    """A file name that is only a name: no folders, no control characters, not hidden, not too long; the extension is kept."""
    n = os.path.basename(str(name or "").replace("\\", "/"))
    n = re.sub(r"[^\w.\- ()]", "_", n, flags=re.UNICODE).strip(" .")
    if len(n) > 80:
        stem, ext = os.path.splitext(n)
        n = stem[:80 - len(ext)] + ext
    return n or default


def guess_type(path):
    mime = mimetypes.guess_type(path)[0] or ""
    kind = "image" if mime.startswith("image/") else "audio" if mime.startswith("audio/") else "video" if mime.startswith("video/") else "file"
    return kind, mime or "application/octet-stream"


def _ffmpeg(args, timeout=20):
    try:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", *args], capture_output=True, timeout=timeout)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def jpeg_of(path, px, seek=None):
    """A JPEG of the picture (or of one frame of a video) no larger than `px` on its long side, or None (no ffmpeg, not a picture)."""
    pre = ["-ss", str(seek)] if seek is not None else []
    return _ffmpeg([*pre, "-i", path, "-frames:v", "1", "-vf", f"scale='min({px},iw)':'min({px},ih)':force_original_aspect_ratio=decrease", "-f", "mjpeg", "-q:v", "5", "-"])


def picture_size(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True, timeout=10)
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0, 0


def sniff_mime(data, name=""):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class Attachments:
    def __init__(self, rt, chats, log=print):
        self.rt, self.chats, self.log = rt, chats, log
        self.card_root = None          # set by shelld: the folder of the SD card (the only place a file may be read from, played from or saved to)

    # ---- where the files are ------------------------------------------------------------------------------------------------
    def _url(self, path):
        if path.startswith(("http://", "https://")):
            return path
        return self.rt.url.rstrip("/") + "/" + path.lstrip("/")

    def _stored(self, gid, mid, i):
        with self.rt.lock:
            row = self.rt.db.execute("SELECT attachments FROM messages WHERE id=? AND chat=?", (mid, gid)).fetchone()
        try:
            atts = json.loads(row[0]) if row and row[0] else []
            a = atts[int(i)]
        except (ValueError, IndexError, TypeError):
            raise AttachError("no such attachment", "not-found")
        if not isinstance(a, dict) or not a.get("url"):
            raise AttachError("no such attachment", "not-found")
        return a

    def _fetch(self, path):
        """The bytes at `path` on the files host (an unauthenticated GET), at most MAX_FETCH."""
        try:
            with urllib.request.urlopen(urllib.request.Request(self._url(path)), timeout=30) as r:
                data = r.read(MAX_FETCH + 1)
        except urllib.error.HTTPError as e:
            raise AttachError("the file is not on the server" if e.code == 404 else f"the server answered {e.code}", "not-found" if e.code == 404 else "network")
        except (urllib.error.URLError, OSError) as e:
            raise AttachError(f"the file could not be fetched: {getattr(e, 'reason', e)}", "network")
        if len(data) > MAX_FETCH:
            raise AttachError("the file is larger than this console takes (64 MB)", "too-big")
        return data

    def plain(self, gid, mid, i, kind="file"):
        """-> (bytes, attachment): the decrypted file (kind `file`) or thumbnail (kind `thumb`) of attachment `i` of a message. Raises AttachError."""
        a = self._stored(gid, mid, i)
        if "video_playback.html" in str(a.get("url")):
            raise AttachError("this video is a link to a web player of the server, which this console cannot play", "unsupported")
        if kind == "thumb":
            t = a.get("thumb")
            if not t:
                raise AttachError("no thumbnail", "not-found")
            if str(t).startswith("data:"):                       # the poster frame of a video: a data URL inside the encrypted content, nothing to fetch
                try:
                    return base64.b64decode(str(t).split(",", 1)[1]), a
                except (IndexError, ValueError):
                    raise AttachError("bad thumbnail", "bad")
            path = str(t)
        else:
            path = str(a["url"])
        blob = self._fetch(path)
        key = a.get("key")
        if not key:                                              # an old attachment of a chat that had no encryption: stored in the clear
            return blob, a
        try:
            return e2e.file_decrypt(e2e.b64u_decode(key), blob, kind), a
        except (e2e.E2eError, ValueError):
            self.log(f"ratatosk: an attachment does not authenticate chat={gid} message={mid} #{i} ({kind})")
            raise AttachError("this file does not authenticate: it was changed or the key is wrong, so it is not shown", "bad")

    # ---- the page asks for a picture ---------------------------------------------------------------------------------------
    def serve(self, handler, gid, mid, i, kind):
        """The decrypted picture (or thumbnail) as the answer of an <img>: in memory, never written anywhere. Anything else is a short JSON error."""
        try:
            data, a = self.plain(gid, mid, i, "thumb" if kind == "thumb" else "file")
            if attachment_type(a) != "image" and kind != "thumb":
                raise AttachError("not a picture", "unsupported")
        except AttachError as e:
            out = json.dumps({"error": str(e), "code": e.code}).encode()
            handler.send_response({"not-found": 404, "unsupported": 415, "too-big": 413, "network": 502}.get(e.code, 422))
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(out)))
            handler.end_headers()
            handler.wfile.write(out)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", sniff_mime(data, str(a.get("name") or "")) if kind != "thumb" else "image/jpeg")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        try:
            handler.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ---- a copy on the card (to play it, or to keep it) ---------------------------------------------------------------------
    def _card(self):
        if not self.card_root:
            raise AttachError("the card is not available", "no-card")
        return os.path.realpath(self.card_root)

    def to_card(self, gid, mid, i, mode):
        """-> {"path": the path on the card, "type", "name"}. mode `play`: a short-lived copy in `.ratatosk-play/` (bounded, emptied at sign-out); `save`: `Downloads/Ratatosk/`, kept."""
        root = self._card()
        data, a = self.plain(gid, mid, i, "file")
        name = safe_name(a.get("name"))
        if mode == "save":
            rel_dir = SAVE_DIR
        else:
            rel_dir, name = PLAY_DIR, f"{mid}-{i}-{name}"
        d = os.path.join(root, rel_dir)
        os.makedirs(d, exist_ok=True)
        stem, ext = os.path.splitext(name)
        target, n = os.path.join(d, name), 1
        if mode == "save":
            while os.path.exists(target):                         # a saved file never overwrites another
                target, n = os.path.join(d, f"{stem} ({n}){ext}"), n + 1
        if os.path.realpath(os.path.dirname(target)) != os.path.realpath(d):
            raise AttachError("bad file name", "bad")
        with open(target, "wb") as f:
            f.write(data)
        if mode != "save":
            self._trim_play(root, keep=target)
        return {"path": os.path.relpath(target, root).replace(os.sep, "/"), "type": attachment_type(a), "name": a.get("name") or name}

    def _trim_play(self, root, keep):
        """The copies for playing stay within PLAY_BUDGET: the oldest go first (never the one just made)."""
        d = os.path.join(root, PLAY_DIR)
        try:
            files = sorted(((os.stat(p).st_mtime, os.path.getsize(p), p) for p in (os.path.join(d, n) for n in os.listdir(d)) if os.path.isfile(p)))
        except OSError:
            return
        total = sum(s for _, s, _ in files)
        for _, size, p in files:
            if total <= PLAY_BUDGET:
                break
            if p != keep:
                try:
                    os.remove(p)
                    total -= size
                except OSError:
                    pass

    def purge(self):
        """Nothing decrypted outlives the account on the card but what the owner saved: the copies for playing are removed."""
        if not self.card_root:
            return
        shutil.rmtree(os.path.join(os.path.realpath(self.card_root), PLAY_DIR), ignore_errors=True)

    # ---- sending -----------------------------------------------------------------------------------------------------------
    def _write(self, path, blob):
        r = self.rt.send_auth({"command": "file:write", "path": path, "content": base64.b64encode(blob).decode()})
        if r.get(".status") != "ok":
            raise AttachError(f"the server did not take the file ({r.get('.reason') or r.get('.status')})", "protocol")

    def _delete(self, path):
        try:
            self.rt.send_auth({"command": "file:delete", "path": path})
        except Exception as e:  # noqa: BLE001
            self.log(f"ratatosk: could not delete {path}: {e}")

    def send_file(self, gid, rel, text="", reply=None):
        """Send the file `rel` of the card into the chat (with `text` as its caption). -> the id of the message. Raises AttachError / RuntimeError; nothing is left on the server then."""
        root = self._card()
        full = os.path.realpath(os.path.join(root, str(rel or "").lstrip("/")))
        if full != root and not full.startswith(root + os.sep):
            raise AttachError("the file is outside the card", "bad")
        if not os.path.isfile(full):
            raise AttachError("no such file", "not-found")
        size = os.path.getsize(full)
        if size <= 0:
            raise AttachError("the file is empty", "bad")
        if size > MAX_SEND:                                       # before anything is read or written
            raise AttachError(f"the file is {size // (1024 * 1024)} MB; this console sends up to {MAX_SEND // (1024 * 1024)} MB", "too-big")
        r = self.chats.ready(gid)                                 # a file is never stored for a message that cannot be sent
        if r[0] == "failed":
            raise RuntimeError(r[1])
        with open(full, "rb") as f:
            data = f.read()
        name = safe_name(os.path.basename(full))
        kind, mime = guess_type(name)
        ext = re.sub(r"[^a-z0-9]", "", os.path.splitext(name)[1].lower())[:8] or "bin"
        key, stem = e2e.file_new_key(), uuid.uuid4().hex[:12]
        written, att = [], {"type": kind, "name": name, "mime": mime, "size": size}
        try:
            path = f"chats/{gid}/files/{stem}.{ext}"
            self._write(path, e2e.file_encrypt(key, data, "file"))
            written.append(path)
            att["url"] = "/" + path
            thumb = None
            if kind == "image":
                thumb = jpeg_of(full, THUMB_PX)
                att["w"], att["h"] = picture_size(full)
                if thumb:
                    tpath = f"chats/{gid}/files/{stem}_thumb.jpg"
                    self._write(tpath, e2e.file_encrypt(key, thumb, "thumb"))
                    written.append(tpath)
                    att["thumb"] = "/" + tpath
            elif kind == "video":
                poster = jpeg_of(full, POSTER_PX, seek=0.5) or jpeg_of(full, POSTER_PX)
                if poster:
                    att["thumb"] = "data:image/jpeg;base64," + base64.b64encode(poster).decode()   # as the phones do: the poster frame travels inside the encrypted content
            att["key"] = e2e.b64u(key)
            return self.chats.send(gid, text, reply, [att])
        except BaseException:
            for p in written:                                     # the message was not appended: its files go again
                self._delete(p)
            raise
