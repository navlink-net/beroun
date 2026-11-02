# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The device's end of the virtual USB cable: fastboot over TCP (the transport of the standard `fastboot -s tcp:localhost:<port>`), served while the cable is plugged in
(flashd.Flasher.usb). A packet is an 8-byte big-endian length and the bytes; the session opens with "FB01" both ways. The subset:
    getvar:<name>        product, version, current-slot, slot-count, secure, max-download-size, all
    download:<hex size>  the image comes (a .img of beroun-image.py), then flash:<partition> writes it with the checks of flashd.py ("system": the other slot)
    flash:<name>         INFO lines show the progress, OKAY when the image is on its slot, FAIL<reason> otherwise
    set_active:<a|b>     the slot to start next (one that holds an image)
    reboot, reboot-recovery
Port: 5554 + the instance's offset (INTELLICAT_PORT_OFFSET); loopback only."""
import os
import socket
import struct
import threading
import time

import flashd

MAX_DOWNLOAD = 0x20000000   # 512 MiB: an image is about 120 MB


def _read(c, n):
    buf = b""
    while len(buf) < n:
        d = c.recv(n - len(buf))
        if not d:
            raise ConnectionError("closed")
        buf += d
    return buf


def _send(c, text):
    b = text.encode() if isinstance(text, str) else text
    c.sendall(struct.pack(">Q", len(b)) + b)


def _packet(c):
    return _read(c, struct.unpack(">Q", _read(c, 8))[0])


class Server:
    def __init__(self, flash, port, reboot):
        self.flash, self.port, self.reboot = flash, port, reboot
        self.sock = None

    def start(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(2)
        threading.Thread(target=self._accept, daemon=True, name="fastbootd").start()

    def _accept(self):
        while True:
            c, _ = self.sock.accept()
            threading.Thread(target=self._session, args=(c,), daemon=True).start()

    def _vars(self):
        i, st = self.flash.info(), self.flash.bootctl()
        return {"product": self.flash.board, "version": i["version"], "current-slot": i["slot"], "slot-count": "2", "secure": "yes" if st["locked"] else "no",
                "max-download-size": hex(MAX_DOWNLOAD), "is-userspace": "no", "slot-version:a": (st["slots"].get("a") or {}).get("version", "-"),
                "slot-version:b": (st["slots"].get("b") or {}).get("version", "-")}

    def _session(self, c):
        part = os.path.join(self.flash.dir, "download.part")
        got = None
        try:
            c.settimeout(120)
            if _read(c, 4) != b"FB01":
                return
            c.sendall(b"FB01")
            if not self.flash.usb:   # the cable is out: nothing answers on it
                return
            while True:
                cmd = _packet(c).decode("utf-8", "replace")
                name, _, arg = cmd.partition(":")
                if name == "getvar":
                    v = self._vars()
                    if arg == "all":
                        for k, val in v.items():
                            _send(c, f"INFO{k}: {val}")
                        _send(c, "OKAY")
                    elif arg in v:
                        _send(c, "OKAY" + v[arg])
                    else:
                        _send(c, "FAILunknown variable")
                elif name == "download":
                    size = int(arg, 16)
                    if size <= 0 or size > MAX_DOWNLOAD:
                        _send(c, "FAILthe image is too big for the device")
                        continue
                    os.makedirs(self.flash.dir, exist_ok=True)
                    _send(c, "DATA%08x" % size)
                    with open(part, "wb") as f:
                        left = size
                        while left:
                            d = _packet(c)   # the data comes in packets as well
                            if len(d) > left:
                                raise ConnectionError("more than announced")
                            f.write(d)
                            left -= len(d)
                    got = part
                    _send(c, "OKAY")
                elif name == "flash":
                    if not got:
                        _send(c, "FAILnothing was downloaded")
                    elif arg not in ("system", "system_a", "system_b", "boot", ""):
                        _send(c, "FAILno such partition")
                    else:
                        self._flash(c, got)
                        got = None
                elif name == "set_active":
                    try:
                        self.flash.set_active(arg)
                        _send(c, "OKAY")
                    except flashd.FlashError as e:
                        _send(c, "FAIL" + str(e))
                elif name in ("reboot", "reboot-recovery", "continue"):
                    _send(c, "OKAY")
                    if self.flash.status()["state"] == "idle":
                        threading.Thread(target=self.reboot, daemon=True).start()
                    return
                else:
                    _send(c, "FAILunknown command")
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            c.close()
            try:
                os.unlink(part)
            except OSError:
                pass

    def _flash(self, c, path):
        try:
            self.flash.start(path)
        except flashd.FlashError as e:
            _send(c, "FAIL" + str(e))
            return
        last = None
        while True:
            time.sleep(0.2)
            s = self.flash.status()
            line = f"{s['state']} {int(s['progress'] * 100)}%"
            if line != last and s["state"] in ("verifying", "writing"):
                _send(c, "INFO" + line)
                last = line
            if s["state"] == "failed":
                _send(c, "FAIL" + s["reason"])
                self.flash.dismiss()
                return
            if s["state"] in ("done", "idle"):
                _send(c, "INFO" + (s["reason"] or "done"))
                _send(c, "OKAY")
                return
