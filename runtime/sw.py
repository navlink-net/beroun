# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""SquirrelWisdom (Camerlengo) client of the shell: login, session, speech recognition.

Follows Caroline (backend-py/app/plugins/sw_api.py, login_api.py, voice_api.py): the same account and the
same credentials file (~/.mcp-notes/credentials.json, {"email", "password"}), a v2 session minted with
`user:verify`, speech recognition through `ai:stt`. The service keys are not part of this repository: they
come from ~/.intellicat/keys.json {"login_key", "stt_key", "notes_key" (the Notes app's service, apps/notes/notesd.py)} (or INTELLICAT_SW_LOGIN_KEY / INTELLICAT_SW_STT_KEY); the optional technical login (key_login) from the same file.
"""
import base64, json, os, threading, urllib.error, urllib.request

API_URL = os.environ.get("INTELLICAT_SW_URL", "https://www.squirrelwisdom.com/")
CREDENTIALS_PATH = os.environ.get("INTELLICAT_CREDENTIALS", os.path.expanduser("~/.mcp-notes/credentials.json"))
KEYS_PATH = os.environ.get("INTELLICAT_KEYS", os.path.expanduser("~/.intellicat/keys.json"))
STT_TIMEOUT_S = 120
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class SwError(Exception):
    """kind: "network" (no connection), "auth" (login refused), "balance", "config" (no key), "service"."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _key(name):
    env = os.environ.get("INTELLICAT_SW_" + name.upper())
    if env:
        return env
    try:
        k = json.load(open(KEYS_PATH, encoding="utf-8-sig")).get(name)  # utf-8-sig: tolerate a BOM written by Windows tools
    except (OSError, ValueError):
        k = None
    if not k:
        raise SwError("config", f"no {name} configured ({KEYS_PATH})")
    return k


def _post(body, timeout=30):
    req = urllib.request.Request(API_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SwError("service", f"HTTP {e.code} for {body.get('command')}")
    except (urllib.error.URLError, OSError) as e:  # DNS failure, refused, timeout: nothing reachable
        raise SwError("network", str(getattr(e, "reason", e)))


def load_credentials():
    try:
        c = json.load(open(CREDENTIALS_PATH, encoding="utf-8-sig"))  # the file is shared with Caroline and Windows tools (BOM)
        return c if c.get("email") and c.get("password") else None
    except (OSError, ValueError):
        return None


def save_credentials(email, password):
    os.makedirs(os.path.dirname(CREDENTIALS_PATH), exist_ok=True)
    fd = os.open(CREDENTIALS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the password: owner only
    with os.fdopen(fd, "w") as f:
        json.dump({"email": email, "password": password}, f, indent=2)
    os.chmod(CREDENTIALS_PATH, 0o600)


def _mint(email, password):
    data = _post({"command": "user:verify", "key": _key("login_key"), "path": "/users", "user": email, "password": password})
    if data.get(".status") != "ok" or not data.get("session"):
        raise SwError("auth", str(data.get(".reason") or "login refused"))
    return data["session"]


_session = None
_lock = threading.Lock()


def login(email, password):
    """Checks the account; on success it becomes the device's account (shared with Caroline)."""
    global _session
    email = email.strip().lower()  # Camerlengo namespaces by the exact login string
    session = _mint(email, password)
    save_credentials(email, password)
    with _lock:
        _session = session
    return email


_key_fails = []   # monotonic times of the wrong technical keys of the last minute


def key_login(key):
    """The technical login (for the developer's own tests, e.g. Claude on a hosted simulator): a long random key stands for one account. keys.json {"tech_key_sha256", "tech_email", "tech_password"}
    (or INTELLICAT_SW_TECH_*); only the hash of the key is on the machine. Five wrong keys in a minute close the door for the rest of that minute."""
    import hashlib, hmac, time
    now = time.monotonic()
    with _lock:
        _key_fails[:] = [t for t in _key_fails if now - t < 60]
        if len(_key_fails) >= 5:
            raise SwError("auth", "too many wrong keys, wait a minute")
    want = _key("tech_key_sha256")
    if not hmac.compare_digest(hashlib.sha256(str(key or "").encode()).hexdigest(), want.lower()):
        with _lock:
            _key_fails.append(now)
        raise SwError("auth", "wrong key")
    return login(_key("tech_email"), _key("tech_password"))


def signup(email, password):
    """A new account (user:add, public: no key), then the login of it. kind "exists" when the e-mail already has one."""
    email = email.strip().lower()
    data = _post({"command": "user:add", "path": "/users", "user": email, "password": password})
    if data.get(".status") != "ok":
        raise SwError("exists" if str(data.get(".errcode")) == "201" else "auth", str(data.get(".reason") or "sign-up refused"))
    return login(email, password)


def session(force=False):
    global _session
    with _lock:
        if force:
            _session = None
        if _session:
            return _session
    creds = load_credentials()
    if not creds:
        raise SwError("auth", "not logged in")
    s = _mint(creds["email"], creds["password"])
    with _lock:
        _session = s
    return s


def _call(body, timeout):
    """One command with the session; a rejected session is minted again once."""
    for attempt in (0, 1):
        data = _post({**body, "session": session(force=bool(attempt))}, timeout)
        if data.get(".status") == "ok":
            return data
        reason = str(data.get(".reason") or "")
        # a spent wallet: the service answers 402, or (seen live in Caroline, a model proxied through OpenRouter) HTTP 200 with errcode 998 and "Insufficient funds" in the reason
        if data.get(".errcode") == "402" or "insufficient funds" in reason.lower():
            raise SwError("balance", "insufficient balance")
        if attempt == 0 and "session" in reason.lower():
            continue
        raise SwError("service", reason or "request failed")


def welcome():
    """The welcome gift of the ONLINE simulator: the service's plugin WelcomeBonus credits piastres to an account on its first claim and never again (the flag is a
    variable of the service, the amount is the plugin's). Only when INTELLICAT_WELCOME is set (the image of the hosted simulator); the device and a local simulator never ask.
    Returns {"granted": bool, "amount": n}, or None when it is off or the call did not decide (the login goes on; the next login asks again). Never raises."""
    if os.environ.get("INTELLICAT_WELCOME", "") in ("", "0"):
        return None
    try:
        data = _call({"command": "plugin:call", "plugin": "WelcomeBonus", "query": "claim", "key": _key("welcome_key")}, 20)
    except SwError as e:
        print(f"welcome: {e.kind}: {e}", flush=True)
        return None
    r = data.get("result") if isinstance(data.get("result"), dict) else data
    if not r.get("ok"):
        print(f"welcome: not decided: {r.get('reason')}", flush=True)
        return None
    return {"granted": bool(r.get("granted")), "amount": int(r.get("amount") or 0)}


def stt(audio, fmt):
    """Speech -> text (ai:stt). `audio` is bytes."""
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise SwError("service", "audio is empty or larger than 25 MB")
    data = _call({"command": "ai:stt", "key": _key("stt_key"), "audio": base64.b64encode(audio).decode("ascii"),
                  "format": fmt, "timeout": STT_TIMEOUT_S}, STT_TIMEOUT_S + 60)
    if not isinstance(data.get("result"), str):
        raise SwError("service", "no result")
    return data["result"]


TTS_MAX_CHARS = 4000
TTS_TIMEOUT_S = 60


def tts(text, voice="Nova"):
    """Text -> speech (ai:tts, Caroline's voice service, voice_api._synthesize_speech_via_camerlengo): base64 MP3. The owner's SW subscription pays (owner, 2026-10-31)."""
    text = str(text or "").strip()
    if not text:
        raise SwError("service", "nothing to say")
    data = _call({"command": "ai:tts", "key": _key("stt_key"), "text": text[:TTS_MAX_CHARS], "voice": voice}, TTS_TIMEOUT_S)
    if not isinstance(data.get("result"), str) or not data["result"]:
        raise SwError("service", "no audio")
    return data["result"]


KEY_OF_FAMILY = {"ai": "stt_key"}   # the service key a family of commands is called with; a family without a key here cannot be called by the shell
RESERVED = ("command", "key", "session")


def command(name, params=None, timeout=60):
    """Any command of the owner's Camerlengo service, for an agent of an app (runtime/agents.py): the shell adds the session and the family's key, the agent never sees either.
    The answer is the service's own (the keys are never in it); the errors are SwError with the kinds above."""
    family = str(name).split(":", 1)[0]
    if ":" not in str(name) or family not in KEY_OF_FAMILY:
        raise SwError("config", f"the shell has no key for the commands '{family}:*'")
    params = params or {}
    if not isinstance(params, dict) or any(k in params for k in RESERVED):
        raise SwError("service", "the parameters must be an object without command, key or session")
    data = _call({**params, "command": name, "key": _key(KEY_OF_FAMILY[family])}, timeout)
    return {k: v for k, v in data.items() if k not in ("key", "session")}


def resolve(prompt):
    """A short model answer (ai:resolve), used by the transcript check."""
    data = _call({"command": "ai:resolve", "key": _key("stt_key"), "question": prompt, "model": "SMALL"}, 10)
    return str(data.get("result") or "")
