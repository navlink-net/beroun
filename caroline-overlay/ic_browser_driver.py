# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Caroline's browser tool on the device: the embedded browser is `ic-browser` (WPE WebKit), not Playwright + Chromium.

Her plugin (app/plugins/app_browser_plugin.py) calls two modules on Linux: `_linux_app_browser` (windows: open, navigate, close,
list, screenshot, scroll, click by coordinates) and `app_browser_cdp` (the page: snapshot, find, click, type, key, evaluate).
Both need Playwright and Chromium, which the device does not have, and WPE has no CDP. This one module answers for both (the
overlay's sitecustomize.py maps the two module names to this file), talking to ic-browser over its control channel
(native/browser/ic-browser.c: a Unix socket, IC_BROWSER_CONTROL). The page logic (which elements to offer, how to click or
type) is her own: the element tagging script is read from her app_browser_cdp.py, not copied.

A "window" is a labelled tab of ic-browser. The "CDP port" her plugin passes around is only a token for the label.
Not supported on the device (yet): file dialogs (fill_file_dialog), downloads, cookies marked HttpOnly.
"""
from __future__ import annotations

import asyncio
import ast
import base64
import json
import os
import struct
import urllib.request
import zlib
from pathlib import Path
from typing import Any

SOCKET = os.environ.get("IC_BROWSER_CONTROL") or os.path.expanduser("~/.intellicat/run/browser.sock")
SHELLD = f"http://127.0.0.1:{os.environ.get('INTELLICAT_PORT', '8765')}"
MAX_TABS = 5

_conn: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
_pending: dict[str, asyncio.Future] = {}
_seq = 0
_lock = asyncio.Lock()
_ports: dict[str, int] = {}      # label -> token, and back
_labels: dict[int, str] = {}


# ---- the control channel -------------------------------------------------------------------------------------------
def _launch_browser() -> None:
    req = urllib.request.Request(f"{SHELLD}/api/launch", data=b'{"id":"browser"}', method="POST")
    urllib.request.urlopen(req, timeout=5).read()


async def _reader(reader: asyncio.StreamReader) -> None:
    global _conn
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            parts = line.decode().rstrip("\n").split(" ", 2)
            rid, status, b64 = parts + [""] * (3 - len(parts))
            fut = _pending.pop(rid, None)
            if fut and not fut.done():
                fut.set_result((status, base64.b64decode(b64) if b64 else b""))
    finally:
        _conn = None
        for fut in _pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("the browser closed"))
        _pending.clear()


async def _connect() -> None:
    global _conn
    if _conn is not None:
        return
    for attempt in range(2):
        try:
            r, w = await asyncio.open_unix_connection(SOCKET, limit=64 * 1024 * 1024)
            _conn = (r, w)
            asyncio.ensure_future(_reader(r))
            return
        except (FileNotFoundError, ConnectionRefusedError):
            if attempt == 0:
                await asyncio.get_running_loop().run_in_executor(None, _launch_browser)  # the browser app is not running: start it
            for _ in range(60):
                await asyncio.sleep(0.25)
                if os.path.exists(SOCKET):
                    break
    raise RuntimeError("the device browser is not reachable (no control socket)")


async def _call(op: str, label: str = "-", payload: str | bytes = "", timeout: float = 30.0) -> bytes:
    global _seq
    async with _lock:
        await _connect()
        _seq += 1
        rid = f"r{_seq}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        _pending[rid] = fut
        data = payload.encode() if isinstance(payload, str) else payload
        _conn[1].write(f"{rid} {op} {label} {base64.b64encode(data).decode()}\n".encode())
        await _conn[1].drain()
    status, out = await asyncio.wait_for(fut, timeout)
    if status != "ok":
        raise RuntimeError(out.decode("utf-8", "replace") or "browser error")
    return out


async def _eval(label: str, js_expr: str, timeout: float = 30.0) -> Any:
    """Runs a JS expression in the tab; the value comes back through JSON."""
    out = await _call("eval", label, f"(function(){{try{{return JSON.stringify({{v:({js_expr})}});}}catch(e){{return JSON.stringify({{e:String(e)}});}}}})()", timeout)
    res = json.loads(out.decode("utf-8", "replace") or "{}")
    if "e" in res:
        raise RuntimeError(res["e"])
    return res.get("v")


async def _tabs() -> list[dict[str, Any]]:
    return json.loads((await _call("list")).decode() or "[]")


async def _wait_loaded(label: str, seconds: float = 20.0) -> dict[str, Any] | None:
    for _ in range(int(seconds * 5)):
        tab = next((t for t in await _tabs() if t["label"] == label), None)
        if tab and not tab["loading"]:
            return tab
        await asyncio.sleep(0.2)
    return next((t for t in await _tabs() if t["label"] == label), None)


def _token(label: str) -> int:
    if label not in _ports:
        _ports[label] = 9300 + len(_ports)
        _labels[_ports[label]] = label
    return _ports[label]


# ---- windows (what _linux_app_browser provides) ---------------------------------------------------------------------
async def open_window(label: str, url: str | None) -> dict[str, Any]:
    if len([t for t in await _call_list_labelled()]) >= MAX_TABS and not any(t["label"] == label for t in await _tabs()):
        raise RuntimeError(f"at most {MAX_TABS} windows can be open on the device")
    await _call("open", label, url or "about:blank")
    tab = await _wait_loaded(label)
    return {"label": label, "cdpPort": _token(label), "url": tab["uri"] if tab else url}


async def _call_list_labelled() -> list[dict[str, Any]]:
    return [t for t in await _tabs() if t["label"]]


async def navigate(label: str, url: str) -> dict[str, Any]:
    if not any(t["label"] == label for t in await _tabs()):
        return await open_window(label, url)
    await _call("navigate", label, url)
    tab = await _wait_loaded(label)
    return {"label": label, "url": tab["uri"] if tab else url}


async def close_window(label: str) -> dict[str, Any]:
    exists = any(t["label"] == label for t in await _tabs())
    if exists:
        await _call("close", label)
    return {"label": label, "closed": exists}


async def list_windows() -> list[dict[str, Any]]:
    return [{"label": t["label"], "url": t["uri"]} for t in await _call_list_labelled()]


async def get_port(label: str) -> int | None:
    return _token(label) if any(t["label"] == label for t in await _tabs()) else None


async def get_cookies(label: str, url: str) -> list[dict[str, Any]]:
    if not any(t["label"] == label for t in await _tabs()):
        return []
    raw = await _eval(label, "document.cookie")  # HttpOnly cookies are not visible to a page
    return [{"key": k, "value": v} for k, _, v in (p.strip().partition("=") for p in (raw or "").split(";")) if k]


def _png(w: int, h: int, bgra: bytes) -> bytes:
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    rows = b"".join(b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


async def screenshot(label: str, x: int | None, y: int | None, width: int | None, height: int | None, max_width: int | None) -> dict[str, Any]:
    out = await _call("shot", label)
    head, _, pix = out.partition(b"\n")
    w, h = (int(v) for v in head.split())
    if all(v is not None for v in (x, y, width, height)):  # a crop: the rows and columns asked for
        x0, y0 = max(0, min(x, w - 1)), max(0, min(y, h - 1))
        cw, ch = max(1, min(width, w - x0)), max(1, min(height, h - y0))
        pix = b"".join(pix[((y0 + r) * w + x0) * 4:((y0 + r) * w + x0 + cw) * 4] for r in range(ch))
        w, h = cw, ch
    elif max_width and w > max_width:  # a smaller picture: every n-th pixel (nearest neighbour)
        nw = max_width
        nh = max(1, round(h * nw / w))
        cols = [min(w - 1, int(c * w / nw)) for c in range(nw)]
        rows = []
        for r in range(nh):
            sy = min(h - 1, int(r * h / nh))
            line = pix[sy * w * 4:(sy + 1) * w * 4]
            rows.append(b"".join(line[c * 4:c * 4 + 4] for c in cols))
        pix, w, h = b"".join(rows), nw, nh
    return {"imageBase64": base64.b64encode(_png(w, h, pix)).decode("ascii")}


_TARGET_JS = """(function (ref, sel) {
  const el = ref ? document.querySelector('[data-mcp-ref="' + ref + '"]') : document.querySelector(sel);
  if (!el) throw new Error('no such element');
  return el;
})"""


def _target(ref: str | None, selector: str | None) -> str:
    if not ref and not selector:
        raise ValueError("Provide either ref (from app_browser_snapshot/app_browser_find) or selector.")
    return f"{_TARGET_JS}({json.dumps(ref)}, {json.dumps(selector)})"


async def scroll(label: str, ref: str | None, selector: str | None, x: int | None, y: int | None, clicks: int | None) -> dict[str, Any]:
    delta_y = -(clicks if clicks is not None else -3) * 100
    if ref or selector:
        await _eval(label, f"({_target(ref, selector)}).scrollIntoView({{block:'center'}})")
    await _eval(label, f"window.scrollBy(0,{delta_y})")
    return {"label": label, "scrolled": delta_y}


async def click_coords(label: str, x: int, y: int, button: str | None) -> dict[str, Any]:
    await _call("click", label, f"{int(x)} {int(y)}")  # a real pointer click, trusted by the page
    return {"label": label, "clicked": [x, y]}


async def real_os_click(label: str, ref: str | None, selector: str | None) -> dict[str, Any]:
    x, y = await _center(label, ref, selector)
    return await click_coords(label, x, y, None)


async def is_visible_on_top(label: str) -> dict[str, Any]:
    tab = next((t for t in await _tabs() if t["label"] == label), None)
    if not tab:
        return {"label": label, "visible": False, "reason": "not open"}
    return {"label": label, "visible": bool(tab["active"])}  # the active tab of the browser app is what is on the screen


async def fill_file_dialog(paths: list[str], _timeout_ms: int | None) -> dict[str, Any]:
    raise RuntimeError("file dialogs are not supported by the device browser yet")


# ---- the page (what app_browser_cdp provides); `cdp_port` is the label's token ----------------------------------------
def _label(cdp_port: int) -> str:
    if cdp_port not in _labels:
        raise RuntimeError("unknown browser window")
    return _labels[cdp_port]


def _upstream_tag_js() -> str:
    """Her element tagging script, read from her app_browser_cdp.py (parsed, not imported: it needs Playwright)."""
    import app.plugins
    src = (Path(list(app.plugins.__path__)[0]) / "app_browser_cdp.py").read_text(encoding="utf-8")
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_TAG_VISIBLE_ELEMENTS_JS":
            return ast.literal_eval(n.value)
    raise RuntimeError("the element tagging script was not found upstream")


async def snapshot(cdp_port: int) -> list[dict[str, Any]]:
    return await _eval(_label(cdp_port), f"({_upstream_tag_js()})()")


async def find(cdp_port: int, text: str) -> list[dict[str, Any]]:
    needle = text.lower()
    return [t for t in await snapshot(cdp_port) if needle in (t.get("name") or "").lower()]


async def _center(label: str, ref: str | None, selector: str | None) -> tuple[int, int]:
    r = await _eval(label, f"(function(){{const el={_target(ref, selector)};el.scrollIntoView({{block:'center'}});"
                           f"const b=el.getBoundingClientRect();return [Math.round(b.x+b.width/2),Math.round(b.y+b.height/2)];}})()")
    return int(r[0]), int(r[1])


async def click(cdp_port: int, ref: str | None = None, selector: str | None = None) -> str:
    label = _label(cdp_port)
    x, y = await _center(label, ref, selector)
    await _call("click", label, f"{x} {y}")
    return "clicked"


async def type_text(cdp_port: int, text: str, ref: str | None = None, selector: str | None = None) -> str:
    js = ("(function(){const el=%s;el.focus();if(window.__osk)window.__osk.hideSoft();const t=%s;"  # her typing is not the user's: no keyboard
          "if(el.isContentEditable){document.execCommand('selectAll');document.execCommand('insertText',false,t);return 'typed (editable)';}"
          "const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
          "Object.getOwnPropertyDescriptor(proto,'value').set.call(el,t);"
          "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return 'typed (fill)';})()"
          % (_target(ref, selector), json.dumps(text)))
    return await _eval(_label(cdp_port), js)


async def press_key(cdp_port: int, key: str) -> str:
    # __osk marks an event as not coming from the device's buttons: the on-screen keyboard (opened by the focus) must not take it
    js = ("(function(){const k=%s,el=document.activeElement||document.body;"
          "const o={key:k,code:k.length===1?'Key'+k.toUpperCase():k,bubbles:true,cancelable:true};"
          "const ev=(t)=>{const e=new KeyboardEvent(t,o);e.__osk=true;return el.dispatchEvent(e);};"
          "ev('keydown');if(k.length===1)ev('keypress');ev('keyup');"
          "if(k==='Enter'&&el.form&&el.tagName==='INPUT'){el.form.requestSubmit?el.form.requestSubmit():el.form.submit();}"
          "return 'pressed '+k;})()" % json.dumps(key))
    return await _eval(_label(cdp_port), js)


async def evaluate(cdp_port: int, fn: str, ref: str | None = None) -> Any:
    label = _label(cdp_port)
    arg = _target(ref, None) if ref else "undefined"
    start = ("(window.__icr={done:false},(function(){try{const f=(0,eval)('('+%s+')');const r=typeof f==='function'?f(%s):f;"
             "Promise.resolve(r).then(v=>{window.__icr={done:true,v:v===undefined?null:v}},e=>{window.__icr={done:true,e:String(e)}});}"
             "catch(e){window.__icr={done:true,e:String(e)}}})(),true)" % (json.dumps(fn), arg))
    await _eval(label, start)
    for _ in range(100):
        res = await _eval(label, "window.__icr")
        if res and res.get("done"):
            if "e" in res:
                raise RuntimeError(res["e"])
            return res.get("v")
        await asyncio.sleep(0.1)
    raise RuntimeError("the script did not finish in 10 s")
