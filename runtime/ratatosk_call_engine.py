#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The call engine of Ratatosk on IntelliCat (docs/usage.md): one process per call, started by runtime/ratatosk_call.py.

It joins a LiveKit room with a token it was given, publishes the sound of the microphone (or a test tone), plays the sound of the other people, and reports what happens as JSON lines
on stdout; the controller talks to it with lines on stdin. It runs with the Python that has the LiveKit SDK and Pillow (scripts/ratatosk-spike-setup.sh makes it), not with shelld's.

    ratatosk_call_engine.py --url ws://... [--token JWT | $INTELLICAT_CALL_TOKEN] [--source alsa|tone|silence] [--sink alsa|none] [--tone 440]
                            [--ringback] [--video --frames F.jpg [--preview P.jpg] [--camera auto|none|test|dev:/dev/videoN|file:PATH]]

stdin:  `mute` | `unmute` | `camera on` | `camera off` | `hangup`  (end of input means hangup: the controller is gone)
stdout: {"ev": "connected"} {"ev": "remote_joined", "identity": ..} {"ev": "remote_left", "identity": ..} {"ev": "remote_audio", "identity": .., "rms": .., "pitch": .., "frames": ..}
        (every second, for each person whose sound arrives) {"ev": "ringback", "on": bool} {"ev": "remote_video", "on": true, "identity": .., "tile": n, "w": .., "h": ..} /
        {"ev": "remote_video", "on": false, "identity": .., "tile": n} {"ev": "video_stats", "identity": .., "tile": n, "fps": .., "w": .., "h": ..} (every second while pictures arrive)
        {"ev": "camera", "state": "on"|"off"|"none", "reason": ..} {"ev": "reconnecting"} / {"ev": "reconnected"} (the media connection dropped and is trying to come back / is back)
Several people (stage 7): their voices are summed by runtime/ratatosk_mix.py into the one stream of the speaker, and each person with a camera gets a picture tile (up to four).
        {"ev": "disconnected", "reason": ..} {"ev": "error", "message": ..} {"ev": "bye"}

The microphone and the speaker are ALSA (`arecord` / `aplay` on the default device, as shelld records and plays), so what the device has is what is used; in the simulator the default
device is routed to the host's. The token is never printed.

Video (stage 3): the pictures of the other side are scaled to fit 640x400 and written, at most 15 a second, as JPEG to --frames (written beside it and renamed, so a reader never sees half a
picture); the controller serves that file to the page. The own camera (when the device has one: a V4L2 device, or `file:` / `test` for the tests) is published at 320x240, 15 a second, and a small
copy of it is written to --preview. A device without a camera receives only. Both files are removed when the call ends.
"""
import argparse, asyncio, json, math, os, signal, subprocess, sys, time

import numpy as np
from livekit import rtc

from ratatosk_mix import Mixer

RATE, FRAME = 48000, 480          # 10 ms of mono
MAX_TILES = 4                     # pictures of other people shown at once (the screen is 640x480); more people are heard and listed, not drawn
CAM_W, CAM_H, CAM_FPS = 320, 240, 15
SHOW_W, SHOW_H, SHOW_FPS = 640, 400, 15   # the largest picture of the other side that the screen shows, and how often it changes


def emit(**o):
    print(json.dumps(o), flush=True)


def write_jpeg(path, img, quality):
    """The picture as JPEG at `path`, whole or not at all (written beside it, then renamed)."""
    tmp = path + ".tmp"
    img.save(tmp, "JPEG", quality=quality)
    os.replace(tmp, path)


def rm(path):
    try:
        if path:
            os.remove(path)
    except OSError:
        pass


def tile_path(base, n):
    """The file of picture tile n: tile 0 is `base` itself (remote.jpg), the others `remote-1.jpg`, ..."""
    if not base or n == 0:
        return base
    stem, ext = os.path.splitext(base)
    return f"{stem}-{n}{ext}"


class Engine:
    def __init__(self, a):
        self.a, self.muted, self.stop = a, False, asyncio.Event()
        self.room = rtc.Room()
        self.remote_seen = set()
        self.play = None
        self.cam = None            # {"pub", "task"} while the own camera is published
        self.vtasks = {}           # track sid -> the task that shows the video track of another participant
        self.tiles = {}            # track sid -> the number of its picture tile
        self.mixer = Mixer()       # the voices of everybody (and the ringing tone) end up here; play_loop is the only writer to the speaker

    def start_player(self):
        if self.a.sink == "alsa":
            self.play = subprocess.Popen(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-D", "default"],
                                         stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def to_player(self, data):
        if self.play is not None and self.play.stdin:
            try:
                self.play.stdin.write(data.tobytes())
                self.play.stdin.flush()
            except (BrokenPipeError, ValueError):
                self.play = None

    async def play_loop(self):
        """The one writer to the speaker: every 10 ms the next frame of the mix (silence when nobody speaks, so the device is never starved)."""
        n, t0 = 0, time.monotonic()
        while not self.stop.is_set():
            self.to_player(self.mixer.pull())
            n += 1
            delay = t0 + n * 0.01 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -0.5:                                          # we fell behind (the machine stalled): do not rush to catch up
                n, t0 = 0, time.monotonic()

    async def publish(self):
        src = rtc.AudioSource(RATE, 1)
        await self.room.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("voice", src),
                                                        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        rec = None
        if self.a.source == "alsa":
            rec = subprocess.Popen(["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-D", "default"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        loop = asyncio.get_running_loop()
        n, t0 = 0, time.monotonic()
        try:
            while not self.stop.is_set():
                frame = rtc.AudioFrame.create(RATE, 1, FRAME)
                buf = np.frombuffer(frame.data, dtype=np.int16)
                if rec is not None:
                    raw = await loop.run_in_executor(None, rec.stdout.read, FRAME * 2)
                    if len(raw) < FRAME * 2:
                        buf[:] = 0
                        if not raw:
                            await asyncio.sleep(0.01)
                    else:
                        buf[:] = np.frombuffer(raw, dtype=np.int16)
                else:
                    if self.a.source == "tone":
                        t = (np.arange(FRAME) + n * FRAME) / RATE
                        buf[:] = (0.3 * 32767 * np.sin(2 * math.pi * self.a.tone * t)).astype(np.int16)
                    else:
                        buf[:] = 0
                    n += 1
                    delay = t0 + n * 0.01 - time.monotonic()           # a generated source keeps the pace of the clock
                    if delay > 0:
                        await asyncio.sleep(delay)
                if self.muted:
                    buf[:] = 0
                await src.capture_frame(frame)
        finally:
            if rec is not None:
                rec.kill()

    async def ringback(self):
        """The tone of a call that rings at the other end: 425 Hz for 1 s, silence for 4 s, until somebody joins the room (stage 2)."""
        emit(ev="ringback", on=True)
        n, t0 = 0, time.monotonic()
        try:
            while not self.stop.is_set() and not self.remote_seen:
                chunk = np.zeros(FRAME, dtype=np.int16)
                if n % 500 < 100:
                    t = (np.arange(FRAME) + n * FRAME) / RATE
                    chunk = (0.25 * 32767 * np.sin(2 * math.pi * 425 * t)).astype(np.int16)
                self.mixer.push("ringback", chunk)
                n += 1
                delay = t0 + n * 0.01 - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
        finally:
            emit(ev="ringback", on=False)

    async def hear(self, track, identity="", key=None):
        window, key = [], key or identity
        try:
            async for ev in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
                data = np.frombuffer(ev.frame.data, dtype=np.int16).copy()
                self.mixer.push(key, data)                              # summed with the others by play_loop
                window.append(data)
                if sum(len(w) for w in window) >= RATE:
                    x = np.concatenate(window).astype(np.float64)
                    spec = np.abs(np.fft.rfft(x[:RATE] * np.hanning(RATE)))
                    emit(ev="remote_audio", identity=identity, rms=round(float(np.sqrt(np.mean(x * x))), 1), pitch=round(float(np.argmax(spec[20:]) + 20), 1), frames=len(window))
                    window = []
        finally:
            self.mixer.drop(key)

    # ---- video: the other side (stage 3) ---------------------------------------------------------------------------------
    async def watch_video(self, track, identity="", tile=0):
        from PIL import Image
        shown, n, last, t0 = False, 0, 0.0, time.monotonic()
        out = tile_path(self.a.frames, tile)
        try:
            async for ev in rtc.VideoStream(track, format=rtc.VideoBufferType.RGB24):
                f = ev.frame
                if f.width < 16:
                    continue                                           # the SDK hands a small placeholder before the first real picture
                now = time.monotonic()
                if now - last < 1.0 / SHOW_FPS:
                    continue
                last = now
                if f.type != rtc.VideoBufferType.RGB24:
                    f = f.convert(rtc.VideoBufferType.RGB24)
                img = Image.frombuffer("RGB", (f.width, f.height), bytes(f.data), "raw", "RGB", 0, 1)
                img.thumbnail((SHOW_W, SHOW_H) if len(self.tiles) <= 1 else (SHOW_W // 2, SHOW_H // 2))   # several people share the screen: smaller pictures
                write_jpeg(out, img, 70)
                n += 1
                if not shown:
                    shown = True
                    emit(ev="remote_video", on=True, identity=identity, tile=tile, w=f.width, h=f.height)
                if now - t0 >= 1.0:
                    emit(ev="video_stats", identity=identity, tile=tile, fps=round(n / (now - t0), 1), w=f.width, h=f.height)
                    n, t0 = 0, now
        finally:
            if shown:
                emit(ev="remote_video", on=False, identity=identity, tile=tile)
            rm(out)

    # ---- video: the own camera (stage 3) ---------------------------------------------------------------------------------
    def camera_source(self):
        spec = self.a.camera
        if spec == "auto":
            spec = "dev:/dev/video0" if os.path.exists("/dev/video0") else "none"
        return spec

    async def camera_on(self):
        if self.cam is not None:
            return
        spec = self.camera_source()
        if spec == "none":
            emit(ev="camera", state="none", reason="no camera")
            return
        src = rtc.VideoSource(CAM_W, CAM_H)
        track = rtc.LocalVideoTrack.create_video_track("cam", src)
        pub = await self.room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
        self.cam = {"pub": pub, "task": asyncio.ensure_future(self.pump_camera(src, spec))}
        emit(ev="camera", state="on")

    async def camera_off(self):
        c, self.cam = self.cam, None
        if c is None:
            return
        c["task"].cancel()
        try:
            await self.room.local_participant.unpublish_track(c["pub"].sid)
        except Exception:
            pass
        rm(self.a.preview)
        emit(ev="camera", state="off")

    async def pump_camera(self, src, spec):
        from PIL import Image
        n, t0, ff = 0, time.monotonic(), None
        loop = asyncio.get_running_loop()
        try:
            if spec.startswith("dev:") or spec.startswith("file:"):
                inp = ["-f", "v4l2", "-framerate", str(CAM_FPS), "-video_size", f"{CAM_W}x{CAM_H}", "-i", spec[4:]] if spec.startswith("dev:") else \
                      ["-re", "-stream_loop", "-1", "-i", spec[5:], "-vf", f"scale={CAM_W}:{CAM_H},fps={CAM_FPS}"]
                ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", *inp, "-f", "rawvideo", "-pix_fmt", "rgba", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            xs = np.arange(CAM_W, dtype=np.uint16)
            while not self.stop.is_set():
                if ff is not None:
                    raw = await loop.run_in_executor(None, ff.stdout.read, CAM_W * CAM_H * 4)
                    if len(raw) < CAM_W * CAM_H * 4:
                        emit(ev="camera", state="off", reason="the camera stopped")
                        self.cam = None
                        return
                else:                                                  # the test picture: moving colour bars
                    img = np.zeros((CAM_H, CAM_W, 4), dtype=np.uint8)
                    img[:, :, 0] = ((xs + n * 8) % 256).astype(np.uint8)[None, :]
                    img[:, :, 1] = ((np.arange(CAM_H, dtype=np.uint16) * 2 + n * 4) % 256).astype(np.uint8)[:, None]
                    img[:, :, 2], img[:, :, 3] = 128, 255
                    raw = img.tobytes()
                    delay = t0 + (n + 1) / CAM_FPS - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                src.capture_frame(rtc.VideoFrame(CAM_W, CAM_H, rtc.VideoBufferType.RGBA, raw))
                n += 1
                if self.a.preview and n % 2 == 0:
                    p = Image.frombuffer("RGBA", (CAM_W, CAM_H), raw, "raw", "RGBA", 0, 1).convert("RGB")
                    p.thumbnail((160, 120))
                    write_jpeg(self.a.preview, p, 60)
        finally:
            if ff is not None:
                ff.kill()

    async def commands(self):
        loop = asyncio.get_running_loop()
        while not self.stop.is_set():
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break                                                  # the controller is gone
            cmd = line.strip()
            if cmd == "mute":
                self.muted = True
            elif cmd == "unmute":
                self.muted = False
            elif cmd == "camera on" and self.a.video:
                await self.camera_on()
            elif cmd == "camera off":
                await self.camera_off()
            elif cmd == "hangup":
                break
        self.stop.set()

    async def run(self):
        room, tasks = self.room, []

        @room.on("track_subscribed")
        def on_track(track, publication, participant):
            ident = participant.identity
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                tasks.append(asyncio.ensure_future(self.hear(track, ident, track.sid)))
            elif track.kind == rtc.TrackKind.KIND_VIDEO and self.a.video and self.a.frames:
                free = [n for n in range(MAX_TILES) if n not in self.tiles.values()]
                if not free:
                    return                                   # all tiles are taken: this person is heard, not drawn
                self.tiles[track.sid] = free[0]
                t = asyncio.ensure_future(self.watch_video(track, ident, free[0]))
                self.vtasks[track.sid] = t
                tasks.append(t)

        @room.on("track_unsubscribed")
        def on_untrack(track, publication, participant):
            t = self.vtasks.pop(track.sid, None)            # the other side switched its camera off: the picture goes away at once
            self.tiles.pop(track.sid, None)
            if t is not None:
                t.cancel()

        @room.on("participant_connected")
        def on_join(p):
            self.remote_seen.add(p.identity)
            emit(ev="remote_joined", identity=p.identity)

        @room.on("participant_disconnected")
        def on_leave(p):
            emit(ev="remote_left", identity=p.identity)

        @room.on("reconnecting")
        def on_reconnecting():                                   # the network went away: the SDK tries to resume; the call is not over, the controller shows it and waits
            emit(ev="reconnecting")

        @room.on("reconnected")
        def on_reconnected():
            emit(ev="reconnected")

        @room.on("disconnected")
        def on_disc(reason=None):
            emit(ev="disconnected", reason=str(reason))
            self.stop.set()

        try:
            await room.connect(self.a.url, self.a.token)
        except Exception as e:
            emit(ev="error", message=f"cannot join the room: {e}")
            return 2
        self.start_player()
        emit(ev="connected")
        for p in room.remote_participants.values():                   # somebody was in the room before us
            self.remote_seen.add(p.identity)
            emit(ev="remote_joined", identity=p.identity)
        tasks += [asyncio.ensure_future(self.publish()), asyncio.ensure_future(self.commands()), asyncio.ensure_future(self.play_loop())]
        if self.a.ringback and not self.remote_seen:
            tasks.append(asyncio.ensure_future(self.ringback()))
        if self.a.video:
            await self.camera_on()                                     # "none" says so and the call goes on receiving only
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        await self.camera_off()
        try:
            await room.disconnect()
        except Exception:
            pass
        for n in range(MAX_TILES):
            rm(tile_path(self.a.frames, n))
        rm(self.a.preview)
        if self.play is not None:
            try:
                self.play.stdin.close()
                self.play.wait(2)
            except Exception:
                self.play.kill()
        emit(ev="bye")
        return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--token", default=os.environ.get("INTELLICAT_CALL_TOKEN"), help="default: $INTELLICAT_CALL_TOKEN (not on the command line, `ps` shows it)")
    p.add_argument("--source", choices=["alsa", "tone", "silence"], default="alsa")
    p.add_argument("--sink", choices=["alsa", "none"], default="alsa")
    p.add_argument("--tone", type=float, default=440.0)
    p.add_argument("--ringback", action="store_true", help="play the ringing tone until somebody joins (the call we placed)")
    p.add_argument("--video", action="store_true", help="a video call: show the pictures of the others, publish the own camera if there is one")
    p.add_argument("--frames", default=None, help="where the picture of the first other person is written (JPEG); the next ones go beside it as NAME-1.jpg, NAME-2.jpg, NAME-3.jpg")
    p.add_argument("--preview", default=None, help="where a small copy of the own camera is written (JPEG)")
    p.add_argument("--camera", default="auto", help="auto | none | test | dev:/dev/videoN | file:PATH")
    a = p.parse_args()
    if not a.token:
        p.error("no token: pass --token or set INTELLICAT_CALL_TOKEN")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    async def go():
        return await Engine(a).run()          # the Room wants a running loop (Python 3.14)
    return asyncio.run(go())


if __name__ == "__main__":
    sys.exit(main())
