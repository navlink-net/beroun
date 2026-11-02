#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""beroun-flash: flashes a firmware image to the simulator over its virtual USB cable (fastboot over TCP, runtime/fastbootd.py).
    beroun-flash.py [--instance NAME | --port N] info
    beroun-flash.py [...] flash IMAGE.img     look at the image first (version, board, signed?), send it, follow the device's progress
    beroun-flash.py [...] set-active a|b
    beroun-flash.py [...] reboot
The cable has to be plugged in (service menu > USB: in). The default port is 5554 (the simulator without an instance name); with --instance it is 5554 + the instance's offset.
The standard `fastboot -s tcp:localhost:<port> ...` speaks to the same endpoint."""
import argparse
import os
import socket
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "runtime"))
import imagefmt  # noqa: E402


def read(c, n):
    buf = b""
    while len(buf) < n:
        d = c.recv(n - len(buf))
        if not d:
            raise ConnectionError("the device closed the connection (is the USB cable plugged in? service menu > USB: in)")
        buf += d
    return buf


def send(c, b):
    b = b.encode() if isinstance(b, str) else b
    c.sendall(struct.pack(">Q", len(b)) + b)


def packet(c):
    return read(c, struct.unpack(">Q", read(c, 8))[0]).decode("utf-8", "replace")


def connect(port):
    c = socket.create_connection(("127.0.0.1", port), timeout=120)
    c.sendall(b"FB01")
    if read(c, 4) != b"FB01":
        raise ConnectionError("not a fastboot endpoint")
    return c


def command(c, cmd, show=print):
    """Sends a command and returns the text after OKAY; INFO lines go to `show`; FAIL raises."""
    send(c, cmd)
    while True:
        r = packet(c)
        if r.startswith("INFO"):
            show(r[4:])
        elif r.startswith("OKAY"):
            return r[4:]
        elif r.startswith("DATA"):
            return r
        elif r.startswith("FAIL"):
            raise RuntimeError(r[4:])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance")
    ap.add_argument("--port", type=int)
    ap.add_argument("cmd", choices=["info", "flash", "set-active", "reboot"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    port = a.port
    if not port:
        off = 0
        if a.instance:
            off = int(open(os.path.expanduser(f"~/.intellicat/instances/{a.instance}")).read().strip()) * 100
        port = 5554 + off
    try:
        if a.cmd == "flash":
            if not a.arg:
                ap.error("flash needs an image file")
            meta = imagefmt.read_meta(a.arg)[0]
            print(f"{os.path.basename(a.arg)}: firmware {meta['version']} ({meta['build']}), board {meta['board']}, {'encrypted' if meta['encrypted'] else 'in the clear'}")
            size = os.path.getsize(a.arg)
            c = connect(port)
            print(f"device: {command(c, 'getvar:product')}, firmware {command(c, 'getvar:version')}, slot {command(c, 'getvar:current-slot').upper()}")
            command(c, "download:%08x" % size)
            sent = 0
            with open(a.arg, "rb") as f:
                while True:
                    d = f.read(1 << 20)
                    if not d:
                        break
                    send(c, d)
                    sent += len(d)
                    print(f"\rsending {sent * 100 // size}%", end="", flush=True)
            print()
            if not packet(c).startswith("OKAY"):
                raise RuntimeError("the device did not take the image")
            print("Upgrade is ongoing...")
            command(c, "flash:system", show=lambda t: print("  " + t))
            print("done: the device restarts into the new system")
        else:
            c = connect(port)
            if a.cmd == "info":
                print(command(c, "getvar:all", show=print))
            elif a.cmd == "set-active":
                command(c, f"set_active:{a.arg}")
                print(f"slot {a.arg} will start next")
            else:
                command(c, "reboot")
                print("rebooting")
    except (OSError, ConnectionError, RuntimeError, imagefmt.ImageError) as e:
        print(f"beroun-flash: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
