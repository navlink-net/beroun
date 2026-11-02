#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""memd - the device's swap, a service of its own (the board's layer/swap.conf; docs/architecture.md, the device's memory).

  start     zram0 as swap: the zram module the device has (scripts/zram-module.sh: writeback, lz4) when it is built, else WSL's own
            (lzo-rle, no writeback: logged); a backing file on the eMMC (a loop device) when the module can write back; then the device's
            cgroup may swap as much as zram holds (memory.swap.max; start.sh leaves it at 0 until zram is up)
  running   writeback: every WRITEBACK_IDLE_S the pages marked idle the time before and not touched since go to the eMMC, then all are
            marked again; at most WRITEBACK_DAILY a day. The limit (start.sh --mem-limit): the compressed pages live in the RAM, but the
            kernel charges them to no cgroup, so the device's memory.max is kept at the limit less what zram takes
  SIGTERM   (stop.sh) swap off, zram reset, the loop device detached: the console is off

Runs as root, inside the device's cgroup. Standard library only. Log: <state>/memd.log. Environment: INTELLICAT_STATE, INTELLICAT_MEM_LIMIT
(as start.sh's: 1800M, max), INTELLICAT_CGROUP_DEVICE, EMMC_DIR, INTELLICAT_PREFIX.
"""
import os
import re
import signal
import subprocess
import sys
import time

import board

SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("INTELLICAT_STATE", os.path.expanduser("~/.intellicat/run"))
PREFIX = os.environ.get("INTELLICAT_PREFIX", os.path.expanduser("~/.intellicat/prefix"))
EMMC = os.environ.get("EMMC_DIR", os.path.expanduser("~/.intellicat/emmc"))
CG = os.environ.get("INTELLICAT_CGROUP_DEVICE", "")
ZRAM = "/sys/block/zram0"
TICK_S = 1.0
FLOOR = 256 << 20   # memory.max never goes below this, whatever zram takes


def log(msg):
    with open(os.path.join(STATE, "memd.log"), "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def size(v):
    """1024M, 2G, 512K, a byte count, or max/0."""
    v = v.strip()
    if v in ("", "max"):
        return None
    m = re.fullmatch(r"(\d+)([KMG]?)", v.upper())
    if not m:
        raise ValueError(f"not a size: {v}")
    return int(m.group(1)) << {"": 0, "K": 10, "M": 20, "G": 30}[m.group(2)]


def config():
    conf = {}
    for line in open(board.layer("swap.conf")):
        line = line.split("#", 1)[0].strip()
        if "=" in line:   # NAME="${NAME:-default}"
            k, v = line.split("=", 1)
            v = v.strip().strip('"')
            m = re.fullmatch(r"\$\{\w+:-(.*)\}", v)
            conf[k.strip()] = os.environ.get(k.strip(), m.group(1) if m else v)
    return conf


def write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return ""


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def own_module():
    d = os.path.join(PREFIX, "lib", "zram-modules", os.uname().release)
    return d if os.path.exists(os.path.join(d, "zram.ko")) else None


def teardown(loop_file=None):
    if os.path.exists(ZRAM):
        if "/dev/zram0" in read("/proc/swaps"):
            r = run("swapoff", "/dev/zram0")
            if r.returncode:
                log(f"swapoff: {r.stderr.strip()}")
        write(f"{ZRAM}/reset", 1)
    if loop_file:
        for line in run("losetup", "-j", loop_file).stdout.splitlines():
            run("losetup", "-d", line.split(":", 1)[0])
    if CG and os.path.exists(f"{CG}/memory.swap.max"):
        write(f"{CG}/memory.swap.max", 0)


def load_module():
    """The device's zram (writeback, lz4) when built, else the kernel's own. A zram already loaded but not used is replaced."""
    d = own_module()
    loaded = os.path.exists("/sys/module/zram")
    if d and loaded and not os.path.exists(f"{ZRAM}/backing_dev"):
        run("rmmod", "zram")
        loaded = False
    if d and not loaded:
        if not os.path.exists("/sys/module/lz4_compress"):
            run("insmod", os.path.join(d, "lz4_compress.ko"))
        run("modprobe", "zsmalloc")
        r = run("insmod", os.path.join(d, "zram.ko"), "num_devices=1")
        if r.returncode:
            log(f"the device's zram did not load ({r.stderr.strip()}): the kernel's own instead")
    if not os.path.exists("/sys/module/zram"):
        run("modprobe", "zram", "num_devices=1")
    return os.path.exists(ZRAM)


def setup(conf):
    """zram0 as swap; returns the loop file when writeback is on, else None. Raises when there is no zram at all."""
    teardown(os.path.join(EMMC, conf["WRITEBACK_FILE"]))
    if not load_module():
        raise RuntimeError("no zram in this kernel")
    algos = read(f"{ZRAM}/comp_algorithm")
    if conf["ZRAM_ALGO"] in algos.replace("[", " ").replace("]", " ").split():
        write(f"{ZRAM}/comp_algorithm", conf["ZRAM_ALGO"])
    else:
        log(f"{conf['ZRAM_ALGO']} is not in this zram ({algos}): the default is used (scripts/zram-module.sh builds the device's)")
    loop_file = None
    wb = size(conf["WRITEBACK_SIZE"] or "0") or 0
    if wb and os.path.exists(f"{ZRAM}/backing_dev"):
        os.makedirs(EMMC, exist_ok=True)
        loop_file = os.path.join(EMMC, conf["WRITEBACK_FILE"])
        if not os.path.exists(loop_file) or os.path.getsize(loop_file) != wb:
            run("fallocate", "-l", str(wb), loop_file)
        dev = run("losetup", "-f", "--show", loop_file).stdout.strip()
        write(f"{ZRAM}/backing_dev", dev)
    elif wb:
        log("this zram cannot write back (no CONFIG_ZRAM_WRITEBACK): nothing goes to the eMMC (scripts/zram-module.sh builds the device's)")
    write(f"{ZRAM}/disksize", size(conf["ZRAM_SIZE"]))
    if loop_file:
        write(f"{ZRAM}/writeback_limit_enable", 1)
        write(f"{ZRAM}/writeback_limit", (size(conf["WRITEBACK_DAILY"]) or 0) >> 12)   # in 4 KiB pages
    run("mkswap", "/dev/zram0")
    r = run("swapon", "-p", conf["ZRAM_PRIORITY"], "/dev/zram0")
    if r.returncode:
        raise RuntimeError(f"swapon: {r.stderr.strip()}")
    if CG and os.path.exists(f"{CG}/memory.swap.max"):
        write(f"{CG}/memory.swap.max", size(conf["ZRAM_SIZE"]))
    log(f"zram0 up: {conf['ZRAM_SIZE']}, {read(f'{ZRAM}/comp_algorithm')}, writeback {'to ' + loop_file if loop_file else 'off'}")
    return loop_file


def zram_used():
    """The RAM zram takes now: mem_used_total of mm_stat (compressed pages and the allocator's overhead)."""
    f = read(f"{ZRAM}/mm_stat").split()
    return int(f[2]) if len(f) > 2 else 0


def main():
    os.makedirs(STATE, exist_ok=True)
    conf = config()
    limit = size(os.environ.get("INTELLICAT_MEM_LIMIT", "max"))
    try:
        write("/proc/self/oom_score_adj", -1000)   # a system service: the OOM killer of the device's cgroup takes an app, not this
    except OSError:
        pass
    try:
        loop_file = setup(conf)
    except Exception as e:
        log(f"no swap: {e}")
        teardown()
        loop_file = None
    signal.signal(signal.SIGTERM, lambda *_: (teardown(loop_file), log("zram0 down"), sys.exit(0)))
    idle_s = int(conf["WRITEBACK_IDLE_S"])
    marked = day = time.monotonic()
    shown = None
    while True:
        time.sleep(TICK_S)
        if limit and CG:
            want = max(FLOOR, limit - zram_used()) & ~0xFFFFF
            if want != shown:
                try:
                    write(f"{CG}/memory.max", want)
                    shown = want
                except OSError as e:
                    log(f"memory.max: {e}")
        now = time.monotonic()
        if loop_file and now - marked >= idle_s:
            try:
                write(f"{ZRAM}/writeback", "idle")   # the pages marked last time and not touched since
            except OSError:
                pass   # the day's budget is used up (or nothing to write)
            write(f"{ZRAM}/idle", "all")
            marked = now
        if loop_file and now - day >= 86400:
            write(f"{ZRAM}/writeback_limit", (size(conf["WRITEBACK_DAILY"]) or 0) >> 12)
            day = now


if __name__ == "__main__":
    main()
