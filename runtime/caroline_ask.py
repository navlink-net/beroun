# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""An app asks Caroline, she thinks, the answer goes back to the app only (docs/architecture.md).

Her backend takes a message from the chat page over a WebSocket (ws://127.0.0.1:<port>/?tab=<id>: {"type": "user_message", "text"}) and answers with events, each in an envelope
{"type": "sdk_message", "message": <event>, "isFiller"?, "isNarration"?}: {"type": "assistant", "message": {"content": [{"type": "text", "text"} | {"type": "tool_use"} ...]}} and, when the turn
is over, {"type": "result", "subtype", "is_error"}. (Found 2026-10-29: this module read the events without the envelope and never got an answer from the real Caroline.)
This module is such a client (the standard library only) on a tab of its own, "ask-<app>": nothing of the conversation reaches her chat window or the owner's screen, and an app's questions
keep their own context, apart from the owner's and from other apps'. Her code is not touched: to her it is one more tab."""
import base64, json, os, socket, struct, threading, time

NO_UPDATE = "[[NO_UPDATE]]"
_locks, _locks_lock = {}, threading.Lock()


class Unavailable(Exception):
    """Her backend does not answer (not running, no account yet)."""


class Failed(Exception):
    """She answered with an error, or the connection broke."""


class Timeout(Exception):
    """No answer in time."""


class Busy(Failed):
    """The tab is in the middle of another turn (the backend refused the message)."""


def _frame(opcode, payload):
    n, head = len(payload), bytearray([0x80 | opcode])
    head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)
    return bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise Failed("the connection to her backend closed")
        buf += chunk
    return buf


def _read_frame(sock):
    """(opcode, payload, fin) of the next frame of the server (unmasked)."""
    b1, b2 = _recv_exact(sock, 2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if b2 & 0x80 else None
    data = _recv_exact(sock, n)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return b1 & 0x0F, data, bool(b1 & 0x80)


def _connect(port, tab, timeout):
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    except OSError as e:
        raise Unavailable(f"her backend does not answer ({e})")
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f"GET /?tab={tab} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    sock.settimeout(5)
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise Unavailable("her backend closed the connection at once")
        head += chunk
        if len(head) > 65536:
            break
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
        sock.close()
        raise Unavailable("her backend refused the connection: " + head.split(b"\r\n", 1)[0].decode("latin-1")[:80])
    leftover = head.split(b"\r\n\r\n", 1)[1]
    if leftover:    # frames that came together with the handshake: put them back in front of the stream
        sock = _Prefixed(sock, leftover)
    return sock


class _Prefixed:
    """A socket that first gives back bytes already read."""

    def __init__(self, sock, data):
        self.sock, self.data = sock, data

    def recv(self, n):
        if self.data:
            out, self.data = self.data[:n], self.data[n:]
            return out
        return self.sock.recv(n)

    def __getattr__(self, name):
        return getattr(self.sock, name)


def _app_lock(app):
    with _locks_lock:
        return _locks.setdefault(app, threading.Lock())


def unwrap(evt):
    """The event inside her `sdk_message` envelope, with the envelope's flags (isFiller, isNarration) kept; any other message as it is."""
    if evt.get("type") == "sdk_message" and isinstance(evt.get("message"), dict):
        return {**evt["message"], **{k: v for k, v in evt.items() if k.startswith("is")}}
    return evt


def frame_text(evt):
    """The text a turn's `assistant` event offers the user: its text blocks, unless the same message calls a tool (then it is narration), unless a filler or narration remark or the no-update sentinel."""
    if evt.get("isFiller") or evt.get("isNarration"):
        return ""
    blocks = (evt.get("message") or {}).get("content") or []
    if any(b.get("type") == "tool_use" for b in blocks):
        return ""
    return "\n".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text") and NO_UPDATE not in b["text"])


def ask(port, app, text, timeout=120.0):
    """Gives her `text` as a request of the app `app` on its own tab and returns what she answers. Asks of one app go one after another."""
    prompt = (f"[This request comes from the app '{app}' of this device, not from the owner. Answer it for the app, plainly and briefly, in the form the app asks for. "
              f"Your answer goes to the app only: the owner does not see this conversation.]\n\n{text}")
    with _app_lock(app):
        return turn(port, f"ask-{app}", prompt, timeout, strict=False)


def turn(port, tab, text, timeout=120.0, strict=True):
    """One turn of `text` on the tab `tab`; returns what the assistant answers (its text, the no-update sentinel left out).
    strict: the answer is read only after the backend's echo of this very message (the backends of the slot send it), so a turn of somebody else that was
    running on the tab is never taken for it; the backend's refusal of a busy tab is Busy. Caroline's backend has no echo: her ask tabs are not strict."""
    deadline = time.time() + timeout
    sock = _connect(port, tab, timeout)
    try:
        sock.sendall(_frame(1, json.dumps({"type": "user_message", "text": text}).encode()))
        parts, buf, started = [], b"", not strict
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise Timeout(f"no answer in {int(timeout)} s")
            sock.settimeout(left)
            try:
                op, data, fin = _read_frame(sock)
            except socket.timeout:
                raise Timeout(f"no answer in {int(timeout)} s")
            except OSError as e:
                raise Failed(f"the connection to the backend broke ({e})")
            if op == 9:
                sock.sendall(_frame(10, data))
                continue
            if op == 8:
                raise Failed("the backend closed the connection before the answer")
            if op not in (0, 1):
                continue
            buf += data
            if not fin:
                continue
            raw, buf = buf, b""
            try:
                evt = unwrap(json.loads(raw))
            except (ValueError, AttributeError):
                continue
            if not started:
                if evt.get("type") == "error":
                    raise Busy(str(evt.get("message") or "the tab is busy"))
                if evt.get("type") == "result" and str(evt.get("subtype", "")).startswith("error_not"):
                    raise Failed(str(evt.get("result") or "the assistant cannot take it"))
                blocks = (evt.get("message") or {}).get("content") or []
                started = evt.get("type") == "user" and evt.get("isOwnerText") and any(b.get("type") == "text" and b.get("text") == text for b in blocks)
                continue
            if evt.get("type") == "assistant":
                t = frame_text(evt)
                if t:
                    parts.append(t)
            elif evt.get("type") == "error":
                raise Failed(str(evt.get("message") or evt.get("error") or "the backend reported an error"))
            elif evt.get("type") == "result":
                if evt.get("is_error") or str(evt.get("subtype", "")).startswith("error"):
                    raise Failed("the answer could not be finished")
                return "\n\n".join(parts)
    finally:
        try:
            sock.sendall(_frame(8, b""))
        except OSError:
            pass
        sock.close()
