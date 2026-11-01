#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""tmpfs watchdog: keeps a RAM-backed temp directory (WSL's /tmp is a tmpfs) from filling up and killing services.

A full tmpfs makes every program that needs scratch space fail, and its data lives in RAM, so a big one is memory pressure as well.
This watchdog looks at the watched tmpfs mounts every few seconds and, when one fills up, deletes only what is safe to delete,
in tiers (percent of the capacity used):

  soft      (default 70)   our own leftovers: entries named intellicat-*, face-*, ic-face* or holding a ".intellicat-tmp" marker,
                           not touched for 1 minute
  hard      (default 85)   also generic stale entries of the same user (python's tmpXXXX, ...), not touched for 30 minutes
  emergency (default 95)   the same with 2 minutes, and ours at once: services are about to fail

It stops as soon as the usage is back at `target` (default 55). Never touched, whatever the pressure: anything with an open file, a
working directory or a memory mapping inside it; entries of another user; hidden names, sockets, FIFOs, symlinks and devices (an
entry whose tree holds one is skipped as a whole); the protected names (systemd-private-*, tmux-*, ssh-*, pulse-*, runtime-*, ...);
anything that is not a tmpfs/ramfs mount point. Every deletion is logged with what, how much and why. When the tmpfs is above the
emergency level and nothing may be removed, an error is logged and the file `tmpfs-full` is left next to the log.

    tmpwatch.py                       run forever (what scripts/start.sh does)
    tmpwatch.py --once [--dry-run]    one pass
    tmpwatch.py --status              JSON: usage and what would be removed at each tier
    tmpwatch.py --clean hard          one pass as if the usage were at that tier, until nothing eligible is left (ignores `target`)
Settings: environment (TMPWATCH_DIRS, TMPWATCH_INTERVAL, TMPWATCH_SOFT, ...) or the same names in config/simulator.env.
"""
import argparse, fnmatch, json, os, shutil, signal, stat, sys, time

DEFAULT_PROTECT = [".*", "systemd-private-*", "snap-*", "snap.*", "tmux-*", "ssh-*", "pulse-*", "dbus-*", "runtime-*", "wslg*",
                   "lost+found", "lost+found*"]
DEFAULT_OWN = ["intellicat-", "face-", "ic-face"]
MARKER = ".intellicat-tmp"
STATE = os.environ.get("INTELLICAT_STATE", os.path.expanduser("~/.intellicat/run"))
LEVELS = ("soft", "hard", "emergency")


def env(name, default, cast=str):
    v = os.environ.get("TMPWATCH_" + name)
    return cast(v) if v not in (None, "") else default


def config():
    return {
        "dirs": [d for d in env("DIRS", "/tmp").split(":") if d],
        "interval": env("INTERVAL", 10.0, float),
        "soft": env("SOFT", 70.0, float), "hard": env("HARD", 85.0, float), "emergency": env("EMERGENCY", 95.0, float),
        "target": env("TARGET", 55.0, float),
        "own_age": env("OWN_AGE", 60.0, float), "generic_age": env("GENERIC_AGE", 1800.0, float),
        "emergency_age": env("EMERGENCY_AGE", 120.0, float),
        "own": [p for p in env("OWN_PREFIXES", ":".join(DEFAULT_OWN)).split(":") if p],
        "protect": [p for p in env("PROTECT", ":".join(DEFAULT_PROTECT)).split(":") if p],
        "log": env("LOG", os.path.join(STATE, "tmpwatch.log")),
    }


def log(cfg, msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    try:
        os.makedirs(os.path.dirname(cfg["log"]), exist_ok=True)
        with open(cfg["log"], "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- the filesystem

def fs_type(path):
    """The file system type of `path` if it is exactly a mount point, else None."""
    real = os.path.realpath(path)
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                p = line.split()
                if len(p) > 9 and p[4].replace("\\040", " ") == real:
                    sep = p.index("-")
                    return p[sep + 1]
    except OSError:
        pass
    return None


def usage(path):
    """(percent used, bytes used, bytes total)"""
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    return (100.0 * used / total if total else 0.0), used, total


def scan(path, limit=300000):
    """(bytes on disk, newest mtime, reason it must not be touched or None) of the tree at path, symlinks never followed."""
    size, newest, n = 0, 0.0, 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            st = os.lstat(cur)
        except OSError:
            continue
        mode = st.st_mode
        n += 1
        if n > limit:
            return size, newest, "too many files to inspect"
        if stat.S_ISLNK(mode):
            return size, newest, "contains a symlink"
        if stat.S_ISSOCK(mode):
            return size, newest, "contains a socket"
        if stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
            return size, newest, "contains a FIFO or a device"
        size += st.st_blocks * 512
        newest = max(newest, st.st_mtime)
        if stat.S_ISDIR(mode):
            try:
                with os.scandir(cur) as it:
                    stack.extend(e.path for e in it)
            except OSError:
                return size, newest, "unreadable"
    return size, newest, None


def open_paths():
    """Every path some process has open, uses as its working directory or root, or maps (from /proc)."""
    out = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        base = "/proc/" + pid
        for link in ("cwd", "exe", "root"):
            try:
                out.add(os.readlink(base + "/" + link))
            except OSError:
                pass
        try:
            for fd in os.listdir(base + "/fd"):
                try:
                    out.add(os.readlink(base + "/fd/" + fd))
                except OSError:
                    pass
        except OSError:
            pass
        try:
            with open(base + "/maps") as f:
                for line in f:
                    parts = line.split(None, 5)
                    if len(parts) == 6 and parts[5].startswith("/"):
                        out.add(parts[5].strip())
        except OSError:
            pass
    return {p[:-10] if p.endswith(" (deleted)") else p for p in out}


def is_open(path, opened):
    pre = path + "/"
    return any(p == path or p.startswith(pre) for p in opened)


# ---------------------------------------------------------------- what may go

def candidates(cfg, d, now=None):
    """Entries directly under the watched directory d with what is known about them. Not filtered by tier."""
    now = now if now is not None else time.time()
    me = os.geteuid()
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for name in names:
        path = os.path.join(d, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        info = {"path": path, "name": name, "ours": False, "skip": None, "size": 0, "age": 0.0}
        if any(fnmatch.fnmatch(name, g) for g in cfg["protect"]):
            info["skip"] = "protected name"
        elif st.st_uid != me:
            info["skip"] = "owned by another user"
        elif stat.S_ISLNK(st.st_mode):
            info["skip"] = "a symlink"
        elif not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            info["skip"] = "not a plain file or directory"
        else:
            size, newest, reason = scan(path)
            info["size"], info["age"] = size, now - newest
            info["skip"] = reason
            info["ours"] = any(name.startswith(p) for p in cfg["own"]) or os.path.exists(os.path.join(path, MARKER))
        out.append(info)
    return out


def eligible(cfg, entries, level):
    """The entries that may be removed at this tier (level 0 soft, 1 hard, 2 emergency), ours first, oldest first."""
    res = []
    for e in entries:
        if e["skip"]:
            continue
        if e["ours"]:
            need = 0.0 if level >= 2 else cfg["own_age"]
        elif level >= 1:
            need = cfg["emergency_age"] if level >= 2 else cfg["generic_age"]
        else:
            continue
        if e["age"] >= need:
            res.append(e)
    res.sort(key=lambda e: (not e["ours"], -e["age"]))
    return res


def level_of(cfg, pct):
    return 2 if pct >= cfg["emergency"] else 1 if pct >= cfg["hard"] else 0 if pct >= cfg["soft"] else -1


def human(n):
    for u in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or u == "GiB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024.0


def run_once(cfg, dry=False, force_level=None):
    """One pass over every watched directory. Returns a list of per-directory reports (dicts)."""
    reports = []
    for d in cfg["dirs"]:
        rep = {"dir": d, "removed": [], "freed": 0, "level": None, "error": None}
        reports.append(rep)
        ft = fs_type(d)
        if ft not in ("tmpfs", "ramfs"):
            rep["error"] = f"{d} is not a tmpfs mount point (type {ft}): not touched"
            log(cfg, "ERROR " + rep["error"])
            continue
        pct, used, total = usage(d)
        rep.update(percent=round(pct, 1), used=used, total=total)
        level = force_level if force_level is not None else level_of(cfg, pct)
        rep["level"] = LEVELS[level] if level >= 0 else None
        if level < 0:
            if os.path.exists(os.path.join(STATE, "tmpfs-full")) and pct < cfg["soft"]:
                os.remove(os.path.join(STATE, "tmpfs-full"))
            continue
        cands = eligible(cfg, candidates(cfg, d), level)
        if cands:
            opened = open_paths()
            cands = [c for c in cands if not is_open(c["path"], opened)]
        if not cands:
            if level >= 2 and not dry:
                log(cfg, f"ERROR {d} is {pct:.0f}% full ({human(used)} of {human(total)}) and nothing may be removed: services are at risk")
                try:
                    open(os.path.join(STATE, "tmpfs-full"), "w").write(time.strftime("%F %T") + f" {d} {pct:.0f}%\n")
                except OSError:
                    pass
            continue
        log(cfg, f"{d} {pct:.0f}% full ({human(used)} of {human(total)}): {LEVELS[level]} tier, {len(cands)} removable "
                 f"({'ours' if cands[0]['ours'] else 'generic'} first)")
        for c in cands:
            if force_level is None and usage(d)[0] <= cfg["target"]:
                break
            why = f"{'ours' if c['ours'] else 'generic stale'}, {c['age'] / 60:.0f} min old, {LEVELS[level]} tier"
            if dry:
                log(cfg, f"  would remove {c['path']} ({human(c['size'])}; {why})")
            else:
                try:
                    if os.path.isdir(c["path"]):
                        shutil.rmtree(c["path"])
                    else:
                        os.remove(c["path"])
                    log(cfg, f"  removed {c['path']} ({human(c['size'])}; {why})")
                except OSError as ex:
                    log(cfg, f"  could not remove {c['path']}: {ex}")
                    continue
            rep["removed"].append(c["path"])
            rep["freed"] += c["size"]
        if not dry:
            now_pct = usage(d)[0]
            rep["percent_after"] = round(now_pct, 1)
            log(cfg, f"  {d} now {now_pct:.0f}% (freed {human(rep['freed'])})")
    return reports


def status(cfg):
    out = []
    for d in cfg["dirs"]:
        ft = fs_type(d)
        item = {"dir": d, "fstype": ft}
        if ft in ("tmpfs", "ramfs"):
            pct, used, total = usage(d)
            entries = candidates(cfg, d)
            item.update(percent=round(pct, 1), used=used, total=total, level=LEVELS[level_of(cfg, pct)] if level_of(cfg, pct) >= 0 else None,
                        removable={LEVELS[l]: [{"path": e["path"], "size": e["size"], "age_min": round(e["age"] / 60, 1), "ours": e["ours"]}
                                               for e in eligible(cfg, entries, l)] for l in range(3)},
                        skipped=[{"path": e["path"], "why": e["skip"]} for e in entries if e["skip"]])
        out.append(item)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--clean", choices=LEVELS)
    ap.add_argument("--dirs")
    ap.add_argument("--log")
    a = ap.parse_args()
    cfg = config()
    if a.dirs:
        cfg["dirs"] = [d for d in a.dirs.split(":") if d]
    if a.log:
        cfg["log"] = a.log
    if a.status:
        print(json.dumps(status(cfg), indent=1))
        return 0
    if a.clean:
        reps = run_once(cfg, a.dry_run, LEVELS.index(a.clean))
        return 1 if any(r["error"] for r in reps) else 0
    if a.once:
        reps = run_once(cfg, a.dry_run)
        return 1 if any(r["error"] for r in reps) else 0
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log(cfg, f"tmpwatch started: {':'.join(cfg['dirs'])} every {cfg['interval']:.0f} s, tiers {cfg['soft']:.0f}/{cfg['hard']:.0f}/{cfg['emergency']:.0f}%, target {cfg['target']:.0f}%")
    while True:
        try:
            run_once(cfg)
        except Exception as ex:  # the watchdog itself must not die of one bad pass
            log(cfg, f"ERROR pass failed: {ex!r}")
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    sys.exit(main())
