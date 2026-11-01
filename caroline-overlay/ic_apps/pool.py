# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The MCP servers of the apps: one long-lived client session per server, kept on a thread and loop of its own (her backend's loop is busy with her turns).

Her tool list is built synchronously on every turn, so tools() answers from a cache and only waits (a few seconds) the first time a server is looked at; a server that
fails is not asked again for a while. A call that finds its session dead opens it again once."""
import asyncio, contextlib, os, threading, time

FIRST_WAIT_S = 6.0
STALE_S = 120.0
RETRY_AFTER_FAIL_S = 30.0
CALL_TIMEOUT_S = 120.0


def _expand(text, folder):
    from . import shell
    return str(text).replace("{app}", folder).replace("{port}", shell.PORT)


class _Server:
    def __init__(self, key, app_id, folder, spec):
        self.key, self.app_id, self.folder, self.spec = key, app_id, folder, spec
        self.tools, self.at, self.failed_at, self.error = None, 0.0, 0.0, ""
        self.client, self.stack = None, None
        self.lock = None  # asyncio.Lock, made on the pool's loop


class Pool:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.servers = {}
        self._mutex = threading.Lock()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="ic-apps-mcp").start()

    # -- sessions (all on the pool's loop) -----------------------------------------------------------------
    async def _open(self, s):
        from mcp import Client, StdioServerParameters
        from . import shell
        spec = s.spec
        if spec.get("service"):
            await asyncio.to_thread(shell.prepare, s.app_id)
        if spec["transport"] == "stdio":
            cmd = [_expand(x, s.folder) for x in spec["cmd"]]
            env = {**os.environ, **{k: _expand(v, s.folder) for k, v in (spec.get("env") or {}).items()}}
            target = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env, cwd=s.folder)
        else:
            target = _expand(spec["url"], s.folder)
        stack = contextlib.AsyncExitStack()
        try:
            client = await stack.enter_async_context(Client(target))
        except BaseException:
            await stack.aclose()
            raise
        s.client, s.stack = client, stack

    async def _drop(self, s):
        stack, s.client, s.stack = s.stack, None, None
        if stack:
            with contextlib.suppress(BaseException):
                await stack.aclose()

    async def _with_client(self, s, fn):
        if s.lock is None:
            s.lock = asyncio.Lock()
        async with s.lock:
            for attempt in (0, 1):
                if s.client is None:
                    await self._open(s)
                try:
                    return await fn(s.client)
                except Exception:
                    await self._drop(s)
                    if attempt:
                        raise

    async def _refresh(self, s):
        try:
            result = await self._with_client(s, lambda c: c.list_tools())
            s.tools = [{"name": t.name, "description": t.description or "", "input": t.input_schema or {"type": "object", "properties": {}}}
                       for t in result.tools]
            s.at, s.error = time.time(), ""
        except BaseException as e:  # a server that does not start must not cost the other tools
            s.failed_at, s.error = time.time(), f"{type(e).__name__}: {e}"

    async def _call(self, s, name, args):
        result = await asyncio.wait_for(self._with_client(s, lambda c: c.call_tool(name, args)), CALL_TIMEOUT_S)
        blocks = []
        for b in result.content or []:
            if getattr(b, "type", "") == "text":
                blocks.append({"type": "text", "text": b.text})
            elif getattr(b, "type", "") == "image":
                blocks.append({"type": "image", "data": b.data, "mimeType": b.mime_type})
        return {"blocks": blocks, "is_error": bool(result.is_error), "structured": getattr(result, "structured_content", None)}

    # -- for the synchronous side ---------------------------------------------------------------------------
    def server(self, app_id, folder, spec):
        key = (app_id, spec["id"])
        with self._mutex:
            s = self.servers.get(key)
            if s is None or s.spec != spec:
                s = self.servers[key] = _Server(key, app_id, folder, spec)
            return s

    def tools(self, app_id, folder, spec):
        """The tools of one server: the cache, refreshed in the background once stale; waits only the first time."""
        s = self.server(app_id, folder, spec)
        now = time.time()
        if s.tools is None:
            if now - s.failed_at > RETRY_AFTER_FAIL_S:
                try:
                    asyncio.run_coroutine_threadsafe(self._refresh(s), self.loop).result(FIRST_WAIT_S)
                except Exception:
                    s.failed_at = time.time()
        elif now - s.at > STALE_S and now - s.failed_at > RETRY_AFTER_FAIL_S:
            asyncio.run_coroutine_threadsafe(self._refresh(s), self.loop)
        return s.tools or []

    async def call(self, app_id, folder, spec, name, args):
        s = self.server(app_id, folder, spec)
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._call(s, name, args), self.loop))

    def status(self):
        return {f"{a}/{b}": {"tools": None if s.tools is None else len(s.tools), "error": s.error, "connected": s.client is not None}
                for (a, b), s in self.servers.items()}


POOL = Pool()
