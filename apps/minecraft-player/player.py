#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The player of the cloud Minecraft: a native Selkies client for the console.

It joins the Selkies stream of a cloud node (WebRTC with GStreamer's webrtcbin, signaling over Selkies' own WebSocket), shows the picture full
screen, plays the sound, and drives the game with the console's own buttons and sticks, read from evdev. No browser: no touch layer, no Pointer
Lock, no autoplay rule.

    player.py [--url https://host/stream/<token>/] [--size 640x480] [--output NAME] [--pad NAME] [--no-audio] [--log FILE] [--service URL]

Without --url it is the hidden app `minecraft-player`, opened by the page of the app `minecraft`: it asks the app's service (minecraftd, GET /stream)
for the stream, the size and the screen, and tells it when it ends (POST /ended), so the node is stopped at once. Selkies' wire protocol (2.x)
is described where it is used; the buttons are the table of the plan.
"""

import argparse
import json
import os
import struct
import sys
import time
import urllib.parse
import urllib.request
import ssl
import zlib
import base64

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("Soup", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gst, GstSdp, GstWebRTC, Soup  # noqa: E402

LOG = None
SERVICE_PORT = 48803   # minecraftd (apps/minecraft/manifest.json); an instance of the simulator shifts it by INTELLICAT_PORT_OFFSET


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    if LOG:
        with open(LOG, "a") as f:
            f.write(line + "\n")


# ---- X keysyms Selkies takes (kd,<keysym> / ku,<keysym>) ----------------------------------------------------------------------------------
K_SPACE, K_E, K_Q, K_T = 0x20, 0x65, 0x71, 0x74
K_W, K_A, K_S, K_D = 0x77, 0x61, 0x73, 0x64
K_ESCAPE, K_SHIFT, K_CTRL = 0xFF1B, 0xFFE1, 0xFFE3
# Selkies' button mask: 1 left, 2 middle, 4 right, 8 wheel up, 16 wheel down
M_LEFT, M_RIGHT, M_WHEEL_UP, M_WHEEL_DOWN = 1, 4, 8, 16

# ---- the console's gamepad (virtual-hardware/vhw.py; the real console: gpio-keys + adc-joystick) -------------------------------------------
EV_KEY, EV_ABS = 1, 3
BTN = {0x130: "a", 0x131: "b", 0x133: "x", 0x134: "y", 0x136: "l1", 0x137: "r1", 0x138: "l2", 0x139: "r2",
       0x13A: "select", 0x13B: "start", 0x13C: "mode", 0x13D: "l3", 0x13E: "r3"}
ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x03, 0x04, 0x10, 0x11
SYSTEM = {"l1", "l2", "r2", "mode"}   # the volume, Caroline, leaving the app: never the game's (docs/usage.md, "System buttons")
STICK_RANGE = 1800.0
EVENT = struct.Struct("llHHi")


def find_pad(name):
    """/dev/input/eventN of the gamepad of THIS simulator (each instance names its own: 'IntelliCat Virtual Gamepad <instance>')."""
    try:
        text = open("/proc/bus/input/devices").read()
    except OSError:
        return None
    for block in text.split("\n\n"):
        if f'N: Name="{name}"' in block:
            for part in block.split():
                if part.startswith("event"):
                    return "/dev/input/" + part
    return None


def png_transparent(b64):
    """True when a cursor PNG (8-bit RGBA, any filter) has no visible pixel: GLFW hides the cursor in the world with an empty one."""
    try:
        data = base64.b64decode(b64)
        pos, idat, w = 8, b"", 0
        bitdepth = ctype = 0
        while pos < len(data):
            n, kind = struct.unpack(">I4s", data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + n]
            if kind == b"IHDR":
                w, _h, bitdepth, ctype = struct.unpack(">IIBB", body[:10])
            elif kind == b"IDAT":
                idat += body
            pos += 12 + n
        if bitdepth != 8 or ctype != 6:
            return False
        raw = zlib.decompress(idat)
        stride = w * 4 + 1
        # a filtered byte of an all-zero alpha channel stays zero under every PNG filter
        return all(raw[r + 1 + 4 * i + 3] == 0 for r in range(0, len(raw), stride) for i in range(w))
    except Exception:
        return False


class Controls:
    """The button table. World: the sticks walk and look; menus (the cursor is visible): the sticks move the cursor."""

    DEAD = 0.18
    LOOK_PX = 22.0      # the camera's pixels per tick (60 a second) at full deflection
    CURSOR_PX = 9.0     # the menu cursor's

    def __init__(self, send):
        self.send = send             # send(text) on Selkies' input channel
        self.menu = True             # until the first cursor message: the title screen has a cursor
        self.axes = {ABS_X: 0, ABS_Y: 0, ABS_RX: 0, ABS_RY: 0}
        self.keys = set()            # keysyms held at the server
        self.mask = 0                # mouse buttons held
        self.sneak = False
        self.frac = [0.0, 0.0]       # sub-pixel remainder of the relative mouse
        self.screen = (640, 480)     # the node's screen (the video's size)
        self.pos = [320, 240]        # where the menu cursor is on it: Selkies sends its image, never its place, so the player keeps it
        self.on_pointer = None       # on_pointer(x, y): the cursor moved (the player draws it there)

    # -- what goes to Selkies
    def key(self, ks, down):
        if down and ks not in self.keys:
            self.keys.add(ks)
            self.send(f"kd,{ks}")
        elif not down and ks in self.keys:
            self.keys.discard(ks)
            self.send(f"ku,{ks}")

    def tap(self, ks):
        self.key(ks, True)
        GLib.timeout_add(60, lambda: (self.key(ks, False), False)[1])

    def button(self, bit, down):
        self.mask = (self.mask | bit) if down else (self.mask & ~bit)
        self.send(f"m,{self.pos[0]},{self.pos[1]},{self.mask},0" if self.menu else f"m2,0,0,{self.mask},0")

    def wheel(self, up):
        self.send(f"m2,0,0,{self.mask | (M_WHEEL_UP if up else M_WHEEL_DOWN)},1")
        self.send(f"m2,0,0,{self.mask},0")

    def release_all(self):
        for ks in list(self.keys):
            self.key(ks, False)
        if self.mask:
            self.mask = 0
            self.send("m2,0,0,0,0")
        self.sneak = False

    def heartbeat(self):
        """Selkies lets a key go when its heartbeat stops for 2 s (a key-up lost on the way): the held keys are named every 100 ms."""
        if self.keys:
            self.send("kh," + ",".join(str(k) for k in self.keys))
        return True

    def set_menu(self, menu):
        if menu != self.menu:
            log("mode:", "menu" if menu else "world")
            self.release_all()
            self.menu = menu
            if menu:
                # a menu opens with the cursor in the middle (GLFW puts it there); put it there for sure, so the drawn one is where it is
                self.pos = [self.screen[0] // 2, self.screen[1] // 2]
                self.send(f"m,{self.pos[0]},{self.pos[1]},{self.mask},0")
                self.pointer_moved()

    def pointer_moved(self):
        if self.on_pointer:
            self.on_pointer(*self.pos)

    # -- what comes from the console
    def on_button(self, name, down):
        if name in SYSTEM:
            return
        if self.menu:
            if name == "a":
                self.button(M_LEFT, down)
            elif name == "x":
                self.button(M_RIGHT, down)
            elif name in ("b", "start") and down:
                self.tap(K_ESCAPE)
            elif name == "y" and down:
                self.tap(K_E)
            return
        if name == "r1":
            self.button(M_LEFT, down)
        elif name == "x":
            self.button(M_RIGHT, down)
        elif name == "a":
            self.key(K_SPACE, down)
        elif name == "b" and down:
            self.sneak = not self.sneak
            self.key(K_SHIFT, self.sneak)
        elif name == "y" and down:
            self.tap(K_E)
        elif name == "start" and down:
            self.tap(K_ESCAPE)

    def on_hat(self, axis, value):
        if self.menu or value == 0:
            return
        if axis == ABS_HAT0X:
            self.wheel(up=value < 0)      # Minecraft: the wheel down is the next slot; Right = next, Left = the wheel up
        elif value > 0:
            self.tap(K_Q)                 # Down: drop
        else:
            self.tap(K_T)                 # Up: chat

    def on_axis(self, axis, value):
        if axis in self.axes:
            self.axes[axis] = max(-1.0, min(1.0, value / STICK_RANGE))

    @staticmethod
    def shape(v, dead):
        if abs(v) < dead:
            return 0.0
        s = (abs(v) - dead) / (1 - dead)
        return (s ** 1.6) * (1 if v > 0 else -1)

    def tick(self):
        """60 a second: the sticks become keys and relative mouse moves."""
        lx, ly, rx, ry = (self.axes[a] for a in (ABS_X, ABS_Y, ABS_RX, ABS_RY))
        if self.menu:
            dx = self.shape(lx, self.DEAD) + self.shape(rx, self.DEAD)
            dy = self.shape(ly, self.DEAD) + self.shape(ry, self.DEAD)
            self.move(dx * self.CURSOR_PX, dy * self.CURSOR_PX)
            return True
        t = 0.35
        self.key(K_W, ly < -t)
        self.key(K_S, ly > t)
        self.key(K_A, lx < -t)
        self.key(K_D, lx > t)
        self.key(K_CTRL, ly < -0.9)       # pushed all the way forward: sprint
        self.move(self.shape(rx, self.DEAD) * self.LOOK_PX, self.shape(ry, self.DEAD) * self.LOOK_PX)
        return True

    def move(self, fx, fy):
        self.frac[0] += fx
        self.frac[1] += fy
        dx, dy = int(self.frac[0]), int(self.frac[1])
        if dx or dy:
            self.frac[0] -= dx
            self.frac[1] -= dy
            if not self.menu:
                self.send(f"m2,{dx},{dy},{self.mask},0")
                return
            # menus: the place itself (Selkies' "m", the node's screen pixels), so the pointer is exactly where the player draws the cursor
            self.pos[0] = max(0, min(self.screen[0] - 1, self.pos[0] + dx))
            self.pos[1] = max(0, min(self.screen[1] - 1, self.pos[1] + dy))
            self.send(f"m,{self.pos[0]},{self.pos[1]},{self.mask},0")
            self.pointer_moved()


class Player:
    RETRIES = 3     # joins of the same node after a break, 2, 4, 6 s apart

    def __init__(self, url, size, output, pad, audio):
        self.base = url if url.endswith("/") else url + "/"
        self.size = size
        self.output = output
        self.audio = audio
        self.loop = GLib.MainLoop()
        self.ws = None
        self.peer = None
        self.input = None
        self.controls = Controls(self.send_input)
        self.controls.screen = tuple(size)
        self.controls.on_pointer = self.draw_cursor
        self.cursor = None           # the video's gdkpixbufoverlay: the node's cursor, drawn by us (Selkies leaves it out of the picture)
        self.hot = (0, 0)
        self.pad_name = pad
        self.pad_fd = None
        self.started = False
        self.exit_code = 0
        self.frames = 0
        self.retries = 0             # joins of the node since the picture last came
        self.restarting = False
        self.ever_played = False

    # ---- signaling: Selkies' WebSocket (webrtc_signaling_server.py) -------------------------------------------------------------------------
    def ws_url(self):
        u = self.base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return u + "api/webrtc/signaling/"

    def connect(self):
        session = Soup.Session()
        msg = Soup.Message.new("GET", self.ws_url())
        msg.connect("accept-certificate", lambda *a: True)   # a node's own address has an IP certificate; the orchestrator's proxy a normal one
        session.websocket_connect_async(msg, None, None, GLib.PRIORITY_DEFAULT, None, self.on_ws, None)
        self.session = session

    def on_ws(self, session, res, _data):
        try:
            self.ws = session.websocket_connect_finish(res)
        except GLib.Error as e:
            self.lost("signaling failed: " + e.message)
            return
        self.ws.set_max_incoming_payload_size(0)
        self.ws.connect("message", self.on_ws_message)
        self.ws.connect("closed", lambda ws: ws is self.ws and self.lost("signaling closed"))
        w, h = self.size
        hello = {"client_type": "controller", "client_slot": -1, "display_id": "primary", "page_size": [w, h]}   # slot -1: the controller itself, not a shared player
        self.ws.send_text("HELLO client " + json.dumps(hello))

    def on_ws_message(self, _ws, kind, data):
        text = data.get_data().decode("utf-8", "replace")
        if os.environ.get("MC_PLAYER_DEBUG"):
            log("ws <", text[:300])
        if text == "HELLO":
            self.ws.send_text("SESSION server")
            return
        if text.startswith("SESSION_OK"):
            self.server_id = text.split(" ")[1]
            log("session with", self.server_id)
            return
        if text.startswith("ERROR"):
            log("signaling:", text)
            if "server not found" in text:
                GLib.timeout_add(1000, lambda: (self.ws.send_text("SESSION server"), False)[1])
            return
        if text.startswith("SESSION_END"):
            log("the node ended the session")
            self.quit(3)
            return
        try:
            msg = json.loads(text[text.index(" ") + 1:])
        except ValueError:
            return
        if "sdp" in msg and msg["sdp"].get("type") == "offer":
            self.on_offer(msg["sdp"]["sdp"])
        elif "ice" in msg and msg["ice"].get("candidate"):
            self.webrtc.emit("add-ice-candidate", msg["ice"].get("sdpMLineIndex") or 0, msg["ice"]["candidate"])

    def signal(self, obj):
        if self.ws:
            self.ws.send_text(f"{self.server_id} {json.dumps(obj)}")

    # ---- WebRTC ----------------------------------------------------------------------------------------------------------------------------
    def build(self):
        self.pipe = Gst.Pipeline.new("player")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "webrtc")
        self.webrtc.set_property("bundle-policy", GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE)
        self.webrtc.set_property("stun-server", "stun://stun.l.google.com:19302")
        self.webrtc.set_property("latency", 0)
        for turn in self.turn_servers():
            self.webrtc.emit("add-turn-server", turn)
        self.pipe.add(self.webrtc)
        self.webrtc.connect("on-ice-candidate", lambda _w, idx, cand: self.signal({"ice": {"candidate": cand, "sdpMLineIndex": idx}}))
        self.webrtc.connect("pad-added", self.on_pad)
        self.webrtc.connect("on-data-channel", self.on_channel)
        self.webrtc.connect("notify::connection-state", self.on_connection_state)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self.on_error)
        self.pipe.set_state(Gst.State.PLAYING)

    def on_error(self, bus, m):
        log("gst error:", m.parse_error()[0].message, m.parse_error()[1])
        if self.pipe is not None and bus is self.pipe.get_bus():
            GLib.idle_add(lambda: (self.lost("the pipeline failed"), False)[1])

    def on_connection_state(self, webrtc, _pspec):
        state = webrtc.get_property("connection-state")
        if state in (GstWebRTC.WebRTCPeerConnectionState.FAILED, GstWebRTC.WebRTCPeerConnectionState.CLOSED) and webrtc is self.webrtc:
            GLib.idle_add(lambda: (self.lost(f"connection {state.value_nick}"), False)[1])

    def lost(self, why):
        """The stream broke (the network, the node's Selkies restarting its pipeline): join the same node again, a few times, before giving up.
        Giving up ends the player, and the service stops the node: a frozen picture would keep it paid for."""
        if self.restarting:
            return
        log("lost:", why)
        if self.retries >= self.RETRIES:
            self.quit(4 if self.ever_played else 2)
            return
        self.retries += 1
        self.restarting = True
        self.teardown()
        GLib.timeout_add(2000 * self.retries, self.restart)

    def teardown(self):
        self.controls.release_all()
        old_ws, self.ws = self.ws, None
        if old_ws is not None and old_ws.get_state() == Soup.WebsocketState.OPEN:
            try:
                old_ws.close(1000, None)
            except Exception:
                pass
        old_pipe, self.pipe = self.pipe, None
        self.webrtc = self.input = self.cursor = None
        self._input_opened = False
        self.started = False
        if old_pipe is not None:
            old_pipe.get_bus().remove_signal_watch()
            old_pipe.set_state(Gst.State.NULL)

    def restart(self):
        log(f"joining the node again ({self.retries}/{self.RETRIES})")
        self.restarting = False
        self.build()
        self.connect()
        return False

    def turn_servers(self):
        """Selkies' /api/turn (the page reads it for its iceServers): the node's TURN relay for networks that block UDP."""
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            cfg = json.loads(urllib.request.urlopen(self.base + "api/turn", timeout=10, context=ctx).read())
        except Exception as e:
            log("no TURN config:", e)
            return []
        out = []
        for srv in cfg.get("iceServers", []):
            urls = srv.get("urls") or []
            for u in ([urls] if isinstance(urls, str) else urls):
                if u.startswith(("turn:", "turns:")) and srv.get("username"):
                    scheme, rest = u.split(":", 1)
                    user = urllib.parse.quote(srv["username"], safe="")
                    cred = urllib.parse.quote(srv.get("credential", ""), safe="")
                    out.append(f"{scheme}://{user}:{cred}@{rest}")
        log("TURN servers:", len(out))
        return out

    def on_offer(self, sdp_text):
        # As in GStreamer's own webrtc examples: new() + parse_buffer (new_from_text hands Python an SDP the description then owns too: a crash)
        _res, sdp = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), sdp)
        offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, sdp)
        self._offer = offer

        def answered(promise, _):
            promise.wait()
            reply = promise.get_reply()          # kept: the answer lives inside the reply, which a temporary would free under it
            answer = reply.get_value("answer")
            self._answer, self._reply = answer, reply
            self.webrtc.emit("set-local-description", answer, Gst.Promise.new())
            self.signal({"sdp": {"type": "answer", "sdp": answer.sdp.as_text()}})
            log("answer sent")

        def remote_set(promise, _):
            promise.wait()
            self.webrtc.emit("create-answer", None, Gst.Promise.new_with_change_func(answered, None))

        self.webrtc.emit("set-remote-description", offer, Gst.Promise.new_with_change_func(remote_set, None))

    def on_pad(self, _webrtc, pad):
        if pad.get_direction() != Gst.PadDirection.SRC:
            return
        caps = pad.get_current_caps() or pad.query_caps(None)
        enc = (caps.get_structure(0).get_string("encoding-name") or "").upper() if caps and caps.get_size() else ""
        log("media pad", pad.get_name(), enc)
        if enc not in ("H264", "H265", "VP8", "VP9", "AV1", "OPUS"):
            # Selkies offers FlexFEC (and others) besides the picture; nothing decodes them, and an unlinked pad stops the whole receive path
            sink = Gst.ElementFactory.make("fakesink")
            sink.set_property("async", False)
            sink.set_property("sync", False)
            self.pipe.add(sink)
            sink.sync_state_with_parent()
            pad.link(sink.get_static_pad("sink"))
            return
        dec = Gst.ElementFactory.make("decodebin")
        dec.connect("pad-added", self.on_decoded)
        self.pipe.add(dec)
        dec.sync_state_with_parent()
        pad.link(dec.get_static_pad("sink"))

    def on_decoded(self, _dec, pad):
        caps = pad.get_current_caps() or pad.query_caps(None)
        kind = caps.get_structure(0).get_name()
        if kind.startswith("video"):
            chain = ["queue max-size-buffers=1 leaky=downstream", "videoconvert",
                     "gdkpixbufoverlay name=cursor positioning-mode=pixels-absolute alpha=0", "videoconvert", self.video_sink()]
        elif kind.startswith("audio") and self.audio:
            chain = ["queue", "audioconvert", "audioresample", "autoaudiosink sync=false"]
        else:
            chain = ["fakesink"]
        bin_ = Gst.parse_bin_from_description(" ! ".join(chain), True)
        if kind.startswith("video"):
            self.cursor = bin_.get_by_name("cursor")
            self.show_cursor(getattr(self, "last_cursor", None))
            self.frames = 0
            bin_.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, self.count_frame)
            self.retries = 0             # a picture again: the next break gets its tries anew
            self.ever_played = True
        self.pipe.add(bin_)
        bin_.sync_state_with_parent()
        pad.link(bin_.get_static_pad("sink"))
        log("playing", kind)
        self.started = True

    def count_frame(self, pad, _info):
        self.frames += 1
        if self.frames == 1:
            s = pad.get_current_caps().get_structure(0)
            w, h = s.get_int('width')[1], s.get_int('height')[1]
            self.frame_size = f"{w}x{h}"
            GLib.idle_add(lambda: setattr(self.controls, "screen", (w, h)))
        return Gst.PadProbeReturn.OK

    def draw_cursor(self, x, y):
        if self.cursor is not None:
            self.cursor.set_property("offset-x", x - self.hot[0])
            self.cursor.set_property("offset-y", y - self.hot[1])

    def report(self):
        if not self.started:
            return True
        log(f"video {self.frames / 5:.0f} fps {getattr(self, 'frame_size', '?')}")
        self.frames = 0
        return True

    def video_sink(self):
        """The whole screen, the picture scaled to it with its own proportions (the external screen may be bigger than what the node draws)."""
        if os.environ.get("MC_PLAYER_SINK"):        # tests: e.g. "fakesink"
            return os.environ["MC_PLAYER_SINK"]
        if Gst.ElementFactory.find("waylandsink") and os.environ.get("WAYLAND_DISPLAY"):
            out = f" fullscreen-output={self.output}" if self.output else ""
            return f"waylandsink fullscreen=true force-aspect-ratio=true sync=false{out}"
        return "autovideosink sync=false"

    # ---- Selkies' data channels: "input" (ordered) carries input and the cursor; "pointer" carries coalesced motion ----------------------
    def on_channel(self, _webrtc, channel):
        label = channel.get_property("label")
        log("data channel", label)
        if label != "input":
            return
        self.input = channel
        channel.connect("on-open", lambda *_: self.on_input_open())
        channel.connect("on-message-string", self.on_input_message)
        if channel.get_property("ready-state") == GstWebRTC.WebRTCDataChannelState.OPEN:
            self.on_input_open()

    def on_input_open(self):
        if getattr(self, "_input_opened", False):
            return
        self._input_opened = True
        w, h = self.size
        self.send_input(f"r,{w}x{h}")      # with Selkies' resize on, the node's screen follows the size the client reports
        self.send_input("s,1")
        log("input channel open, asked for", f"{w}x{h}")

    def send_input(self, text):
        if self.input is not None and self.input.get_property("ready-state") == GstWebRTC.WebRTCDataChannelState.OPEN:
            self.input.emit("send-string", text)

    def on_input_message(self, _ch, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if msg.get("type") == "cursor":
            d = msg.get("data") or {}
            hidden = not d.get("curdata") or not d.get("width") or png_transparent(d["curdata"])
            self.controls.set_menu(not hidden)
            self.show_cursor(None if hidden else d)

    def show_cursor(self, d):
        """The node's cursor image (a PNG with its hotspot) over the picture, at the place Controls keeps; none in the world."""
        self.last_cursor = d
        if self.cursor is None:
            return    # no picture yet: shown when it comes
        if d:
            try:
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(base64.b64decode(d["curdata"]))
                loader.close()
                self.cursor.set_property("pixbuf", loader.get_pixbuf())
                self.hot = (int(d.get("hotx") or 0), int(d.get("hoty") or 0))
            except (GLib.Error, ValueError) as e:
                log("cursor image:", e)
                return
            self.draw_cursor(*self.controls.pos)
        self.cursor.set_property("alpha", 1.0 if d else 0.0)

    # ---- the gamepad ---------------------------------------------------------------------------------------------------------------------
    def open_pad(self):
        path = find_pad(self.pad_name)
        if not path:
            return True    # not there yet: try again
        try:
            self.pad_fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log("gamepad:", e)
            return True
        log("gamepad", path)
        GLib.io_add_watch(self.pad_fd, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_pad_event)
        return False

    def on_pad_event(self, fd, cond):
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            os.close(fd)
            self.pad_fd = None
            GLib.timeout_add(500, self.open_pad)
            return False
        try:
            data = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            return True
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _s, _u, etype, code, value = EVENT.unpack_from(data, off)
            if etype == EV_KEY and code in BTN and value in (0, 1):
                self.controls.on_button(BTN[code], value == 1)
            elif etype == EV_ABS:
                if code in (ABS_HAT0X, ABS_HAT0Y):
                    self.controls.on_hat(code, value)
                else:
                    self.controls.on_axis(code, value)
        return True

    # ---- run -----------------------------------------------------------------------------------------------------------------------------
    def quit(self, code=0):
        self.exit_code = code
        try:
            self.controls.release_all()
        except Exception:
            pass
        self.loop.quit()

    def run(self):
        self.build()
        self.connect()
        if self.open_pad():
            GLib.timeout_add(500, self.open_pad)
        GLib.timeout_add(16, self.controls.tick)
        GLib.timeout_add(100, self.controls.heartbeat)
        GLib.timeout_add_seconds(5, self.report)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, lambda: (self.quit(0), False)[1])
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, lambda: (self.quit(0), False)[1])
        self.loop.run()
        self.restarting = True       # the closing below is not a break to mend
        self.teardown()
        return self.exit_code


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--size", default="")
    ap.add_argument("--output", default="")
    ap.add_argument("--pad", default="")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--log", default="")
    ap.add_argument("--service", default="", help="the app's service (default: 127.0.0.1, its port + the instance's offset)")
    a = ap.parse_args()
    LOG = a.log or None
    url, size, output = a.url, a.size, a.output
    service = a.service or (f"http://127.0.0.1:{SERVICE_PORT + int(os.environ.get('INTELLICAT_PORT_OFFSET') or 0)}" if not url else "")
    if not url:   # opened by the app: the service of the app has started the node and knows its stream
        try:
            st = json.loads(urllib.request.urlopen(service + "/stream", timeout=5).read())
            url, size, output = st["url"], size or st.get("size", ""), output or st.get("output", "")
        except Exception as e:
            log("no stream address:", e)
            return 2
    w, h = (int(v) for v in (size or "640x480").split("x"))
    inst = os.environ.get("INTELLICAT_INSTANCE", "")
    pad = a.pad or ("IntelliCat Virtual Gamepad" + (" " + inst if inst else ""))
    Gst.init(None)
    code = Player(url, (w, h), output, pad, not a.no_audio).run()
    if service:   # closed (MODE), or the stream ended: the service stops the node at once, the wallet pays per second
        try:
            urllib.request.urlopen(urllib.request.Request(service + "/ended", json.dumps({"code": code}).encode(),
                                                          {"Content-Type": "application/json"}), timeout=5)
        except Exception as e:
            log("could not tell the service:", e)
    return code


if __name__ == "__main__":
    sys.exit(main())
