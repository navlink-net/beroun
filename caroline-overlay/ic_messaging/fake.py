# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Scripted accounts for design, tests and demos: no network, no real account. Used when IC_MESSAGING_FAKE is set (and by the tests).

It behaves like a real adapter in everything the registry sees: link flows of the four kinds, a conversation list, history that is
paged, live incoming messages, delivery states, echoes of what was sent, a revoked link. `simulate_*` are the hooks the tests pull
(the phone writing, the other party answering, the service revoking the link).
"""
import asyncio, random, time

from .adapter import Adapter, AdapterError

NAMES = ["Anna Petrova", "Boris Kane", "Carla Mendez", "Dmitri Volkov", "Eva Lindgren", "Farid Haddad", "Grace Okafor", "Hiro Tanaka",
         "Irene Costa", "Jonas Weber", "Katya Ivanova", "Liam O'Brien", "Maya Patel", "Nikolai Orlov", "Olga Smirnova", "Pablo Reyes"]
GROUPS = ["Family", "Project Cat", "Weekend plans", "Support rota", "Book club", "Neighbours"]
CHANNELS = ["#general", "#random", "#engineering", "#support", "#releases"]
PHRASES = ["See you at six", "Did you get my last message?", "Sending the file now", "Thanks, that works", "Can we move it to Thursday?",
           "Looks good to me", "I'll call you later", "Which one do you prefer?", "On my way", "Please check the attached document",
           "Happy birthday!", "The meeting is cancelled", "I forgot the keys again", "Sounds like a plan", "Let me think about it"]
SUBJECTS = ["Invoice 2026-103", "Meeting notes", "Re: Travel dates", "Question about the contract", "Photos from the trip", "Welcome aboard"]
QUOTE = "\n\nOn Mon, 12 Oct 2026, 09:14 the other side wrote:\n> previous text of the thread\n> and more of it\n\n-- \nBest regards"

LABELS = {"mail": "Mail", "whatsapp": "WhatsApp", "telegram": "Telegram", "signal": "Signal", "slack": "Slack"}


class FakeAdapter(Adapter):
    def __init__(self, service, role="owner", linked=True, count=8, seed=1, auto_reply=False, history=None, pace=0.0, slot=1):
        super().__init__()
        self.service, self.role, self.label, self.slot = service, role, LABELS[service], slot
        self.MULTI = True
        self._linked = linked
        self._count = count
        self._seed = seed
        self._auto_reply = auto_reply
        self._history = history if history is not None else service in ("telegram", "slack", "mail")
        self._since = time.time() if linked else None
        self._detail = ""
        self._flow = None            # the link flow in progress: {"phase": ...}
        self._revoked = False
        self._pace = pace
        self._synced = False
        self.sent = []               # what was sent through this adapter (tests look here)

    # ---- state -----------------------------------------------------------------------------------------------------
    def capabilities(self):
        return {"history": self._history, "send": True, "files": self.service != "signal", "read_marks": self.service in ("telegram", "whatsapp", "signal", "mail"),
                "search": self._history}

    def state(self):
        if self._revoked:
            return {"state": "needs-attention", "detail": "the service ended this session; link again", "since": self._since}
        if self._flow:
            return {"state": "connecting", "detail": self._flow.get("detail", ""), "since": None}
        if self._linked:
            return {"state": "linked", "detail": self._detail, "since": self._since}
        return {"state": "not-linked", "detail": "", "since": None}

    async def start(self):
        pass

    # ---- linking ---------------------------------------------------------------------------------------------------
    def prefill(self):
        return {"address": self._detail, "imap_host": "imap.example.com", "smtp_host": "smtp.example.com"} if self.service == "mail" else {}

    async def link_start(self, params):
        if (params or {}).get("edit") and self.service == "mail" and self._linked:
            self._flow = {"phase": "form", "detail": "waiting for the mailbox settings", "edit": True}
            return {"step": "form", "prompt": "The mailbox settings", "fields": [
                {"name": "address", "label": "E-mail address", "secret": False, "preset": self._detail},
                {"name": "password", "label": "Password", "secret": True},
                {"name": "imap_host", "label": "IMAP server", "secret": False, "preset": "imap.example.com"},
                {"name": "smtp_host", "label": "SMTP server", "secret": False, "preset": "smtp.example.com"}]}
        if self._linked and not self._revoked:
            return {"step": "done"}
        self._revoked = False
        s = self.service
        if s in ("whatsapp", "signal"):
            self._flow = {"phase": "qr", "detail": "waiting for the scan"}
            if self._pace:
                asyncio.get_running_loop().call_later(self._pace, lambda: asyncio.ensure_future(self.simulate_scan()))
            return {"step": "qr", "data": f"fake-link://{s}/{self.role}/{int(time.time())}"}
        if s == "telegram":
            self._flow = {"phase": "phone", "detail": "waiting for the phone number"}
            return {"step": "form", "fields": [{"name": "phone", "label": "Phone number", "secret": False}]}
        if s == "slack":
            self._flow = {"phase": "page", "detail": "waiting for the sign-in in the browser"}
            return {"step": "open_page", "url": "https://slack.example/oauth/fake", "prompt": "Sign in to Slack in the browser, then press Continue."}
        self._flow = {"phase": "form", "detail": "waiting for the mailbox settings"}
        return {"step": "form", "fields": [
            {"name": "address", "label": "E-mail address", "secret": False},
            {"name": "password", "label": "Password", "secret": True},
            {"name": "imap_host", "label": "IMAP server", "secret": False, "preset": "imap.example.com"},
            {"name": "smtp_host", "label": "SMTP server", "secret": False, "preset": "smtp.example.com"}]}

    async def link_step(self, data):
        f = self._flow
        if not f:
            return {"step": "error", "error": "no login in progress"}
        s, ph = self.service, f["phase"]
        if s == "telegram":
            if ph == "phone":
                if not str(data.get("phone", "")).strip():
                    return {"step": "error", "error": "a phone number is needed"}
                f.update(phase="code", phone=str(data["phone"]), detail="waiting for the code")
                return {"step": "code", "prompt": "The code Telegram sent to your other devices"}
            if ph == "code":
                if data.get("code") != "12345":
                    return {"step": "error", "error": "wrong code"}
                if f["phone"].endswith("2"):
                    f.update(phase="password", detail="waiting for the 2FA password")
                    return {"step": "password", "prompt": "Two-step verification password"}
                return await self._finish()
            if ph == "password":
                if data.get("password") != "hunter2":
                    return {"step": "error", "error": "wrong password"}
                return await self._finish()
        if s == "slack" and ph == "page":
            return await self._finish()
        if s == "mail" and ph == "form":
            if "@" not in str(data.get("address", "")) or not data.get("password"):
                return {"step": "error", "error": "address and password are needed"}
            if self._detail and self._detail != data["address"]:      # edited to another mailbox: the old messages go
                self.ctx.store.delete_account_data(self.account_id)
                self._synced = False
            self._detail = data["address"]
            return await self._finish()
        if s in ("whatsapp", "signal"):
            return {"step": "wait", "prompt": "Scan the code with the phone"}
        return {"step": "error", "error": "unexpected step"}

    async def _finish(self):
        self._flow, self._linked, self._since, self._revoked = None, True, time.time(), False
        return {"step": "done"}

    async def link_cancel(self):
        self._flow = None

    async def unlink(self):
        if not self._linked and not self._flow:
            raise AdapterError("not linked", "not-linked")
        self._flow, self._linked, self._revoked, self._since, self._synced = None, False, False, None, False

    async def simulate_scan(self):
        """The phone scanned the QR code."""
        if self._flow and self._flow["phase"] == "qr":
            await self._finish()
            self.ctx.state_changed()
            await self.sync()

    def simulate_revoke(self):
        """The service ended this session."""
        self._revoked = True
        self.ctx.state_changed()

    # ---- data ------------------------------------------------------------------------------------------------------
    def _plan(self):
        rnd = random.Random(f"{self._seed}/{self.service}/{self.role}" + (f"/{self.slot}" if self.slot > 1 else ""))
        if self.role == "self":
            names = ["Owner (Konstantin)", "Reforce bot", "Alerts", "Anna Petrova"]
        else:
            names = list(NAMES)
            rnd.shuffle(names)
        out = []
        for i in range(self._count):
            if self.service == "slack":
                kind, title = ("channel", CHANNELS[i % len(CHANNELS)]) if i % 3 == 0 else ("dm", names[i % len(names)])
            elif self.service in ("telegram", "whatsapp", "signal") and i % 4 == 3:
                kind, title = "group", GROUPS[(i // 4) % len(GROUPS)]
            else:
                kind, title = "dm", names[i % len(names)]
            if i >= len(names):
                title = f"{title} {i // len(names) + 1}"
            out.append({"local": f"c{i:04d}", "kind": kind, "title": title,
                        "rnd": random.Random(f"{self._seed}/{self.service}/{self.role}" + (f"/{self.slot}" if self.slot > 1 else "") + f"/{i}")})
        return out

    async def sync(self):
        if not self._linked or self._revoked or self._synced:
            return
        self._synced = True
        now = time.time()
        for i, c in enumerate(self._plan()):
            self.ctx.incoming({"id": c["local"], "kind": c["kind"], "title": c["title"], "avatar": "".join(w[0] for w in c["title"].split()[:2]).upper()})
            rnd = c["rnd"]
            n = rnd.randint(3, 14) if self._history else rnd.randint(1, 3)
            t = now - (i * 1500 + 60) - n * 600       # the last message is older than now - i * 25 min
            for j in range(n):
                t += rnd.randint(30, 540)
                out = rnd.random() < 0.4
                text = rnd.choice(PHRASES)
                subject = rnd.choice(SUBJECTS) if self.service == "mail" and (j == 0 or rnd.random() < 0.25) else ""
                if self.service == "mail" and rnd.random() < 0.3:
                    text += QUOTE
                last = j == n - 1
                self.ctx.incoming({"id": c["local"]}, {
                    "ext_id": f"{c['local']}-{j}", "sender": "me" if out else c["local"], "sender_name": "" if out else c["title"].split()[0],
                    "ts": t, "text": text, "subject": subject, "outgoing": out, "state": "read" if out else "sent",
                    "read": not (last and not out and i % 3 == 0)})

    async def fetch_history(self, conv, before=None, n=50):
        return 0

    async def send(self, conv, text, subject=None, reply_to=None, client_ref=None):
        if not self._linked or self._revoked:
            raise AdapterError("not linked", "not-linked")
        if text.startswith("!fail"):
            raise AdapterError("the service refused the message", "send-failed")
        if self._pace:
            await asyncio.sleep(self._pace)
        ext = f"out-{len(self.sent)}-{int(time.time() * 1000)}"
        self.sent.append({"conv": conv, "text": text, "subject": subject, "ext_id": ext})
        # the service reports the sent message back as an echo, with its own id
        asyncio.get_running_loop().call_soon(lambda: self.ctx.incoming({"id": conv}, {
            "ext_id": ext, "sender": "me", "ts": time.time(), "text": text, "subject": subject or "", "outgoing": True, "state": "sent"}))
        if self._auto_reply:
            asyncio.get_running_loop().call_later(max(0.6, self._pace), lambda: self.simulate_incoming(conv, "Got it: " + text[:40]))
        return ext

    async def mark_read(self, conv):
        pass

    async def send_file(self, conv, path, caption=None, client_ref=None):
        if not self._linked or self._revoked:
            raise AdapterError("not linked", "not-linked")
        ext = f"outfile-{len(self.sent)}-{int(time.time() * 1000)}"
        self.sent.append({"conv": conv, "text": caption or "", "file": path, "ext_id": ext})
        return ext

    async def download(self, ref, dest):
        """The scripted attachments carry their text in the ref ("fake:<text>"): the file is that text."""
        if not ref.startswith("fake:"):
            raise AdapterError("nothing to download", "not-found")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(ref[5:])
        return dest

    # ---- what the tests pull ---------------------------------------------------------------------------------------
    def simulate_incoming(self, conv, text, sender_name=None, subject="", outgoing=False, attachments=None):
        """The other party (or, with outgoing=True, the owner's phone) wrote something."""
        c = next((x for x in self._plan() if x["local"] == conv), None)
        title = c["title"] if c else conv
        self.ctx.incoming({"id": conv, "kind": c["kind"] if c else "dm", "title": title}, {
            "ext_id": f"sim-{conv}-{time.time_ns()}", "sender": "me" if outgoing else conv, "sender_name": "" if outgoing else (sender_name or title.split()[0]),
            "ts": time.time(), "text": text, "subject": subject, "outgoing": outgoing, "state": "sent", "attachments": attachments or []})
