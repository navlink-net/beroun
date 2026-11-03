# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""What an attachment is, for every service the same way, and the cache the app shows attachments from.

An adapter says what it knows (WhatsApp: voice message, Telegram: sticker); where it knows nothing (mail, Slack, a file we sent ourselves)
the kind comes from the mime type, or from the name when there is no mime type. The cache holds the attachments fetched to be shown (not
the owner's copies in Downloads): bounded in size and age, the oldest go first.
"""
import mimetypes, os, re, time

KINDS = ("photo", "video", "audio", "voice", "sticker", "file")
CACHE_DIR = os.path.join(".cache", "messaging")      # inside the device's home, so the shell's media routes can stream from it
CACHE_MAX_BYTES = 256 * 1024 * 1024
CACHE_MAX_AGE = 14 * 24 * 3600


def mime_of(name, mime=""):
    mime = (mime or "").split(";")[0].strip().lower()
    return mime or (mimetypes.guess_type(name or "")[0] or "")


def kind_of(mime, name="", hint=""):
    """photo, video, audio, voice, sticker or file. A hint that names a special kind (voice, sticker) is kept; 'file' and '' are guessed."""
    if hint in ("voice", "sticker", "video", "audio", "photo"):
        return hint
    m = mime_of(name, mime)
    if m.startswith("image/"):
        return "photo"
    if m.startswith("video/"):
        return "video"
    if m.startswith("audio/"):
        return "audio"
    return "file"


def normalize(atts):
    """The attachments of a message with a kind and a mime type filled in (a copy; the adapters' own values win where they are specific)."""
    out = []
    for a in atts or []:
        a = dict(a)
        a["mime"] = mime_of(a.get("name", ""), a.get("mime", ""))
        a["kind"] = kind_of(a["mime"], a.get("name", ""), a.get("kind", ""))
        out.append(a)
    return out


EXT_OF_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
               "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "video/x-matroska": ".mkv", "video/ogg": ".ogv",
               "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/mp4": ".m4a", "audio/aac": ".aac",
               "audio/flac": ".flac", "audio/wav": ".wav", "audio/x-wav": ".wav"}
EXT_OF_KIND = {"photo": ".jpg", "sticker": ".webp", "video": ".mp4", "voice": ".ogg", "audio": ".mp3"}   # when the service names neither type nor extension


def ensure_ext(name, mime="", kind="file"):
    """A cache name the shell can stream from: a name without an extension (a Telegram voice message is just 'voice') gets one from its type."""
    name = safe_name(name)
    if os.path.splitext(name)[1]:
        return name
    return name + (EXT_OF_MIME.get(mime_of(name, mime)) or EXT_OF_KIND.get(kind, ""))


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "file").strip(". ") or "file"


def trim(cache_dir, keep=None, max_bytes=CACHE_MAX_BYTES, max_age=CACHE_MAX_AGE, now=None):
    """Delete cache files that are too old, then the oldest until the folder fits; the file `keep` (just fetched) is never deleted."""
    now = now or time.time()
    rows = []
    for name in os.listdir(cache_dir) if os.path.isdir(cache_dir) else []:
        p = os.path.join(cache_dir, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if os.path.isfile(p):
            rows.append([st.st_mtime, st.st_size, p])
    rows.sort()
    total = sum(r[1] for r in rows)
    for mtime, size, p in rows:
        if p == keep:
            continue
        if now - mtime > max_age or total > max_bytes:
            try:
                os.remove(p)
                total -= size
            except OSError:
                pass
