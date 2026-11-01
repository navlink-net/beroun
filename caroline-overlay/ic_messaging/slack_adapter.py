# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Slack: the adapter over Caroline's own `app.slack_channel` (OAuth tokens, one Socket Mode connection) and the Slack Web API.

Her Socket Mode connection is the only one: Slack hands each event to one of the connections of an app, so a second one of ours would steal half of her events. The adapter
therefore reads the events from hers (hooks.py wraps `slack_channel._account_for_event`, which she calls with every message payload) and uses the Web API with her
tokens for everything else: conversations, history, sending, files, read marks. Both identities (`owner` = the user token, `self` = her bot token) are served.

More workspaces: one more account (slot 2...) is another OAuth sign-in of the same Slack app, with its tokens kept in a store of its own (`<workspace>/slack-ws/<account>/`, the same
layout her module reads, so her own login code is used unchanged). The app token is the app's, so her one connection also receives the events of the added workspaces; `tap`
hands each event to the account of its team and role. The first account therefore has to be linked for live events; an added one reads and sends through the Web API on its own.
"""
import asyncio, logging, os, time, types

from .adapter import Adapter, AdapterError

log = logging.getLogger("ic_messaging")
_channel = None                 # tests put a stand-in module here
_web = None                     # tests put a stand-in for slack_sdk's AsyncWebClient here: web(token) -> client
ADAPTERS = {}                   # account name ("owner", "self2"...) -> adapter, for the event tap


def channel():
    if _channel is not None:
        return _channel
    from app import slack_channel
    return slack_channel


def web(token):
    if _web is not None:
        return _web(token)
    from slack_sdk.web.async_client import AsyncWebClient
    return AsyncWebClient(token=token)


def _workspace():
    from . import _workspace as ws
    return ws()


def tap(payload):
    """Called with the envelope of every message event her connection receives (see hooks.py); the event is stored for the identity it belongs to."""
    try:
        ev = payload.get("event") or {}
        if ev.get("type") not in ("message", "app_mention") or ev.get("subtype") not in (None, "file_share", "thread_broadcast"):
            return
        auths = [a for a in payload.get("authorizations") or [] if isinstance(a, dict)]
        role = "self" if any(a.get("is_bot") for a in auths) else "owner"
        team = next((a.get("team_id") for a in auths if a.get("team_id")), None) or payload.get("team_id") or ev.get("team")
        mine = [a for a in ADAPTERS.values() if a.role == role]
        hit = [a for a in mine if team and a._team == team]
        if not hit:                                                  # a team nobody knows (yet): the first account, as before the added ones
            hit = [a for a in mine if a.slot == 1 and (not a._team or not team)]
        for ad in hit:
            asyncio.ensure_future(ad.on_event(ev))
    except Exception as e:
        log.warning("slack tap: %r", e)


class SlackAdapter(Adapter):
    service = "slack"
    label = "Slack"
    POLL = 3.0
    MULTI = True

    def __init__(self, role, slot=1):
        super().__init__()
        self.role, self.slot = role, slot
        ADAPTERS[self.acct] = self
        self._team = None           # the Slack team (workspace) id, from auth.test once linked: the tap routes the events of a workspace to its accounts
        self._me = None             # the user id of this identity in that workspace
        self._users = {}            # user id -> display name
        self._titles = {}           # channel id -> title
        self._kinds = {}            # channel id -> dm / group / channel
        self._login = None          # the OAuth task
        self._login_err = ""
        self._synced = False
        self._watch = None
        self._was = None

    # ---- state -----------------------------------------------------------------------------------------------------
    def _ws(self):
        """The workspace dir her token code reads: hers for the first account, a store of its own for an added one."""
        return _workspace() if self.slot == 1 else os.path.join(_workspace(), "slack-ws", self.acct)

    def _token(self):
        try:
            return channel().get_token_for_account(self._ws(), self.role)
        except Exception:
            return None

    def _status(self):
        if self.slot > 1:                                            # her status is that of her connection: an added account is "connected" when its token works
            return {"connected": bool(self._me), "last_error": ""}
        try:
            return channel().get_slack_channel_status()
        except Exception:
            return {}

    def capabilities(self):
        return {"history": True, "send": True, "files": True, "read_marks": self.role == "owner", "search": True}

    def state(self):
        if self._login and not self._login.done():
            return {"state": "connecting", "detail": "waiting for the sign-in in the browser", "since": None}
        if not self._token():
            return {"state": "not-linked", "detail": self._login_err, "since": None}
        st = self._status()
        if st.get("connected"):
            return {"state": "linked", "detail": "", "since": None}
        if st.get("last_error"):
            return {"state": "needs-attention", "detail": str(st["last_error"])[:120], "since": None}
        return {"state": "connecting", "detail": "connecting", "since": None}

    # ---- life ------------------------------------------------------------------------------------------------------
    async def start(self):
        self._watch = asyncio.ensure_future(self._watcher())

    async def stop(self):
        if self._watch:
            self._watch.cancel()

    async def _watcher(self):
        while True:
            try:
                s = self.state()["state"]
                if s in ("linked", "connecting") and self._token() and not self._me:
                    await self._identify()
                    s = self.state()["state"]
                if s != self._was:
                    self._was = s
                    self.ctx.state_changed()
                if s == "linked" and not self._synced:
                    await self.sync()
                if s == "not-linked":
                    self._synced = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("slack watcher %s: %r", self.role, e)
            await asyncio.sleep(self.POLL)

    async def _identify(self):
        try:
            r = await web(self._token()).auth_test()
            self._me, self._team = r.get("user_id"), r.get("team_id")
        except Exception as e:
            log.warning("slack auth.test %s: %r", self.acct, e)

    # ---- reading ---------------------------------------------------------------------------------------------------
    def _client(self):
        tok = self._token()
        if not tok:
            raise AdapterError("Slack is not linked", "not-linked")
        return web(tok)

    async def _user_name(self, uid):
        if not uid:
            return ""
        if uid in self._users:
            return self._users[uid]
        try:
            r = await self._client().users_info(user=uid)
            u = r["user"]
            p = u.get("profile") or {}
            name = p.get("display_name") or p.get("real_name") or u.get("real_name") or u.get("name") or uid
        except Exception:
            name = uid
        self._users[uid] = name
        return name

    async def _title(self, c):
        if c.get("is_im"):
            return await self._user_name(c.get("user"))
        n = c.get("name") or c.get("id")
        return ("#" if c.get("is_channel") or c.get("is_group") and not c.get("is_mpim") else "") + n

    @staticmethod
    def _kind(c):
        return "dm" if c.get("is_im") else "group" if c.get("is_mpim") or c.get("is_private") else "channel"

    async def sync(self):
        if self._synced or not self._token():
            return
        cl = self._client()
        self._synced = True
        convs, cursor = [], None
        while True:
            r = await cl.conversations_list(types="public_channel,private_channel,mpim,im", exclude_archived=True, limit=200, cursor=cursor)
            convs += [c for c in r["channels"] if c.get("is_im") or c.get("is_member", True)]
            cursor = (r.get("response_metadata") or {}).get("next_cursor")
            if not cursor or len(convs) >= 400:
                break
        n = 0
        for c in convs:
            title = await self._title(c)
            self._titles[c["id"]] = title
            self._kinds[c["id"]] = self._kind(c)
            self.ctx.incoming({"id": c["id"], "kind": self._kind(c), "title": title, "avatar": "".join(w[0] for w in title.lstrip("#").split()[:2]).upper() or "#"})
            if n < 40:                                              # the last line of the most recent ones; the rest fill in when opened
                n += 1
                try:
                    h = await cl.conversations_history(channel=c["id"], limit=1)
                    for m in h.get("messages", []):
                        await self._ingest(c["id"], m, read=True)
                except Exception as e:
                    log.warning("slack history(1) %s: %r", c["id"], e)

    async def _ingest(self, chan, m, read=False):
        if m.get("subtype") not in (None, "file_share", "thread_broadcast", "bot_message"):
            return None
        text = (m.get("text") or "").strip()
        files = m.get("files") or []
        atts = [{"kind": "photo" if str(f.get("mimetype", "")).startswith("image/") else "file", "name": f.get("name") or "file", "size": f.get("size") or 0,
                 "mime": f.get("mimetype") or "", "ref": f.get("url_private_download") or f.get("url_private") or ""} for f in files]
        if not text and not atts:
            return None
        st = self._status()
        me = self._me or (st.get("own_bot_user_id") if self.role == "self" else st.get("own_user_id"))
        out = bool(me) and m.get("user") == me
        uid = m.get("user") or m.get("bot_id") or ""
        title = self._titles.get(chan) or chan
        return self.ctx.incoming(
            {"id": chan, "kind": self._kinds.get(chan, "channel"), "title": title},
            {"ext_id": f"{chan}:{m['ts']}", "sender": "me" if out else uid, "sender_name": "" if out else await self._user_name(m.get("user")) or m.get("username", ""),
             "ts": float(m["ts"]), "text": text, "outgoing": out, "state": "sent", "attachments": atts, "read": read or out})

    async def on_event(self, ev):
        chan = ev.get("channel")
        if not chan:
            return
        if chan not in self._titles:                                 # a conversation that appeared after the sync
            try:
                r = await self._client().conversations_info(channel=chan)
                self._titles[chan] = await self._title(r["channel"])
                self._kinds[chan] = self._kind(r["channel"])
            except Exception:
                pass
        await self._ingest(chan, ev)

    async def fetch_history(self, conv, before=None, n=50):
        cl = self._client()
        latest = None
        if before:
            row = self.ctx.store.get_message(int(before))
            if row and row.get("ext_id") and ":" in row["ext_id"]:
                latest = row["ext_id"].split(":", 1)[1]
        r = await cl.conversations_history(channel=conv, limit=int(n), latest=latest, inclusive=False)
        added = 0
        for m in r.get("messages", []):
            if await self._ingest(conv, m, read=True) is not None:
                added += 1
        return added

    # ---- sending ---------------------------------------------------------------------------------------------------
    async def send(self, conv, text, subject=None, reply_to=None, client_ref=None):
        r = await self._client().chat_postMessage(channel=conv, text=text)
        return f"{conv}:{r['ts']}"

    async def send_file(self, conv, path, caption=None, client_ref=None):
        r = await self._client().files_upload_v2(channel=conv, file=path, initial_comment=caption or None, filename=os.path.basename(path))
        return None

    async def mark_read(self, conv):
        if self.role != "owner":
            return                                                   # a bot token cannot mark conversations read
        row = self.ctx.store.history(f"{self.account_id}/{conv}", None, 1)
        if row and row[-1].get("ext_id"):
            try:
                await self._client().conversations_mark(channel=conv, ts=row[-1]["ext_id"].split(":", 1)[1])
            except Exception as e:
                log.warning("slack mark: %r", e)

    async def download(self, ref, dest):
        import httpx
        async with httpx.AsyncClient(follow_redirects=True) as h:
            r = await h.get(ref, headers={"Authorization": f"Bearer {self._token()}"})
            r.raise_for_status()
            open(dest, "wb").write(r.content)
        return dest

    # ---- linking (her OAuth login) ---------------------------------------------------------------------------------
    async def link_start(self, params):
        ch = channel()
        if self.state()["state"] in ("linked", "connecting") and self._token():
            return {"step": "done"}
        if self._login and not self._login.done():
            return {"step": "wait", "prompt": "Finish the sign-in in the browser window."}
        self._login_err = ""
        patched = types.SimpleNamespace(open=lambda url, *a, **k: asyncio.ensure_future(self._open_page(url)) and True)
        ch.webbrowser = patched                                      # her login opens the OAuth page through `webbrowser`: show it in the device browser
        self._login = asyncio.ensure_future(self._run_login())
        return {"step": "wait", "prompt": "The Slack sign-in page opens in the browser. Sign in there; this screen finishes by itself."}

    async def _open_page(self, url):
        try:
            from app.plugins import _linux_app_browser as b
            await b.open_window("slack-login", url)
        except Exception as e:
            log.warning("could not open the sign-in page in the device browser: %r (the URL is in the log)", e)
            log.warning("slack sign-in url: %s", url)

    async def _run_login(self):
        try:
            await channel().run_oauth_login(self._ws())
        except Exception as e:
            self._login_err = str(e)
            log.warning("slack login: %r", e)
        finally:
            self.ctx.state_changed()

    async def link_step(self, data):
        if self._login and not self._login.done():
            return {"step": "wait", "prompt": "Finish the sign-in in the browser window."}
        if self._token():
            await self._identify()
            return {"step": "done"}
        return {"step": "error", "error": self._login_err or "the sign-in did not finish"}

    async def link_cancel(self):
        if self._login and not self._login.done():
            self._login.cancel()
        self._login = None

    async def unlink(self):
        ch = channel()
        if not self._token():
            raise AdapterError("not linked", "not-linked")
        for p in ((ch._user_token_path if self.role == "owner" else ch._bot_token_path)(self._ws()),):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        if not ch.get_user_token(self._ws()) and not ch.get_bot_token(self._ws()):
            try:
                ch._app_token_path(self._ws()).unlink()
            except FileNotFoundError:
                pass
        self._synced = False
        self._me = self._team = None

    # ---- added accounts --------------------------------------------------------------------------------------------
    def on_added(self):
        if self.slot > 1:
            from . import channels
            channels.remember("slack", self.acct)

    def on_removed(self):
        ADAPTERS.pop(self.acct, None)
        if self.slot > 1:
            import shutil
            from . import channels
            channels.forget("slack", self.acct)
            shutil.rmtree(self._ws(), ignore_errors=True)
