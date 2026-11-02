# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The over-the-air update of the system (OTA): asks an update server which firmware is the newest for this board, downloads the image and hands it to flashd.py, which
checks it (signature, board, version, decryption) and writes it to the other slot exactly as a flash over the USB cable does. The server is static files:
    <url>/<board>/latest.json   {"version": "1.4", "build": "...", "file": "beroun-1.4-<board>.img", "size": N, "sha256": "<of the file>", "notes": "..."}
    <url>/<board>/<file>        the signed (and encrypted) image of beroun-image.py
Nothing here is trusted: a wrong or changed image is refused by flashd.py (the developer's signature and encryption), an older one too, so the server cannot do more than
offer. Where the server is: INTELLICAT/BEROUN_UPDATE_URL, or config/update.json {"url": ...}; http(s):// or file://."""
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import flashd
import imagefmt

CHECK_EVERY = 6 * 3600


class Updater:
    def __init__(self, flash, board, url, ota_dir, enabled=True, every=CHECK_EVERY):
        self.flash, self.board, self.url, self.dir, self.enabled, self.every = flash, board, (url or "").rstrip("/"), ota_dir, enabled, every
        self.lock = threading.Lock()
        self.st = {"state": "idle", "progress": 0.0, "available": None, "checked": 0, "error": ""}

    def status(self):
        with self.lock:
            return dict(self.st, current=self.flash.info()["version"], server=bool(self.url), enabled=self.enabled)

    def _set(self, **kw):
        with self.lock:
            self.st.update(kw)

    def _get(self, rel, timeout=15):
        return urllib.request.urlopen(f"{self.url}/{self.board}/{urllib.parse.quote(rel)}", timeout=timeout)

    def start_auto(self):
        """A check a minute after the start and then every few hours (no server set: nothing)."""
        def loop():
            time.sleep(60)
            while True:
                try:
                    self.check()
                except flashd.FlashError:
                    pass
                time.sleep(self.every)
        if self.enabled and self.url:
            threading.Thread(target=loop, daemon=True, name="updated").start()

    def check(self):
        if not self.enabled:
            raise flashd.FlashError("updates are not available on this device")
        if not self.url:
            raise flashd.FlashError("no update server is set")
        with self.lock:
            if self.st["state"] in ("downloading", "installing"):
                raise flashd.FlashError("an update is in progress")
            self.st.update(state="checking", error="")
        try:
            with self._get("latest.json") as r:
                info = json.loads(r.read(65536))
            for k in ("version", "file", "size", "sha256"):
                if k not in info:
                    raise ValueError(f"latest.json has no {k}")
            if os.path.basename(info["file"]) != info["file"]:
                raise ValueError("latest.json names a file outside the folder")
            newer = imagefmt.version_key(str(info["version"])) > imagefmt.version_key(self.flash.info()["version"])
            self._set(state="available" if newer else "current", available=info if newer else None, checked=int(time.time()))
        except Exception as e:  # noqa: BLE001
            self._set(state="failed", error=f"the update server could not be asked: {e}", checked=int(time.time()))
        return self.status()

    def install(self):
        """Downloads the offered image, then flashes it (the device restarts into it). The answer is at once."""
        with self.lock:
            if self.st["state"] != "available" or not self.st["available"]:
                raise flashd.FlashError("no update is offered: check first")
            info = dict(self.st["available"])
            self.st.update(state="downloading", progress=0.0, error="")
        threading.Thread(target=self._run, args=(info,), daemon=True, name="updated-install").start()
        return self.status()

    def _run(self, info):
        part = os.path.join(self.dir, info["file"] + ".part")
        try:
            os.makedirs(self.dir, exist_ok=True)
            h, got, size = hashlib.sha256(), 0, int(info["size"])
            with self._get(info["file"], timeout=60) as r, open(part, "wb") as out:
                while True:
                    c = r.read(1 << 20)
                    if not c:
                        break
                    out.write(c)
                    h.update(c)
                    got += len(c)
                    self._set(progress=min(1.0, got / size) if size else 0.0)
            if got != size or h.hexdigest() != info["sha256"]:
                raise ValueError("the download is damaged (size or hash differs from latest.json)")
            self._set(state="installing", progress=1.0)
            self.flash.start(part, ota=True)
            while self.flash.status()["state"] not in ("idle", "failed"):
                time.sleep(0.3)
            s = self.flash.status()
            if s["state"] == "failed":
                self.flash.dismiss()
                raise flashd.FlashError(s["reason"])
            self._set(state="current", available=None)
        except Exception as e:  # noqa: BLE001
            self._set(state="failed", error=str(e))
        finally:
            try:
                os.unlink(part)
            except OSError:
                pass
