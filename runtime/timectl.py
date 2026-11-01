# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Clock control of OS Beroun: the time, the time zone and network time (NTP). What the Settings app's "Date & time" uses.

One interface, two implementations (chosen like netctl's):
  FakeClock    the simulator: the host's clock is never touched. The console's time is the host's plus an offset, with a time zone of
               its own; network time "synchronizes" (the offset goes to zero) as soon as there is a network. State is kept in a file.
  SystemClock  the device: OS Beroun keeps the system clock. Network time is BusyBox ntpd run one-shot (`ntpd -q -n -p SERVER`) when
               the setting is turned on, when the network comes, and every INTELLICAT_NTP_INTERVAL seconds; it needs no daemon of its own and
               no systemd. The time zone is /etc/localtime (the image carries tzdata); a time set by hand goes to the system clock and the RTC.
               Not exercised on hardware yet.
  AndroidClock  OS Beroun on Android: shows Android's own date, time, zone and automatic time (the Kotlin host, androidhost); Android lets an ordinary app
               set none of them, so a change opens Android's Date & time settings and says so. Chosen when the host's bridge is there.
Choose with INTELLICAT_CLOCK=fake|system (default fake). Servers: INTELLICAT_NTP_SERVERS (space separated, default "pool.ntp.org time.google.com").

Interface: status() -> {"epoch", "zone", "offset" (seconds east of UTC), "local" {year, month, day, hour, minute, second},
"ntp" (network time is on), "synced" (the clock has been set by the network)};  zones() -> ["Europe/Prague", ...];
set_zone(zone);  set_ntp(on);  set_time(year, month, day, hour, minute) in the console's own time zone (refused while ntp is on).
Raises ClockError with a message fit for the screen.
"""
import datetime as dt, json, os, shutil, subprocess, threading, time
from zoneinfo import ZoneInfo, available_timezones


class ClockError(Exception):
    pass


def _local(epoch, zone):
    d = dt.datetime.fromtimestamp(epoch, ZoneInfo(zone))
    return {"year": d.year, "month": d.month, "day": d.day, "hour": d.hour, "minute": d.minute, "second": d.second}, int(d.utcoffset().total_seconds())


def _check_time(year, month, day, hour, minute):
    """The wall-clock fields as a naive datetime; ClockError for a date that does not exist."""
    try:
        if not 2000 <= int(year) <= 2099:
            raise ValueError
        return dt.datetime(int(year), int(month), int(day), int(hour), int(minute))
    except (TypeError, ValueError):
        raise ClockError("Not a valid date or time")


def _zones():
    return sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "posix/", "right/", "SystemV/", "US/"))) + ["UTC"]


class _Base:
    """The state both clocks keep in a file: the zone and whether network time is on."""

    def __init__(self, path, online):
        self.path, self.online, self._lock = path, online, threading.RLock()
        self.st = {"zone": "Europe/Prague", "ntp": True, "offset": 0.0}
        try:
            self.st.update(json.load(open(path)))
        except (OSError, ValueError):
            pass

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        json.dump(self.st, open(self.path, "w"))

    def zones(self):
        return _zones()

    def _check_zone(self, zone):
        if zone not in _zones():
            raise ClockError("Unknown time zone")


class FakeClock(_Base):
    def status(self):
        with self._lock:
            synced = self.st["ntp"] and self.online()
            if synced and self.st["offset"]:  # the network corrects the clock
                self.st["offset"] = 0.0
                self._save()
            now = time.time() + self.st["offset"]
            local, off = _local(now, self.st["zone"])
            return {"epoch": int(now), "zone": self.st["zone"], "offset": off, "local": local, "ntp": self.st["ntp"], "synced": synced}

    def set_zone(self, zone):
        self._check_zone(zone)
        with self._lock:
            self.st["zone"] = zone
            self._save()

    def set_ntp(self, on):
        with self._lock:
            self.st["ntp"] = bool(on)
            self._save()

    def set_time(self, year, month, day, hour, minute):
        naive = _check_time(year, month, day, hour, minute)
        with self._lock:
            if self.st["ntp"]:
                raise ClockError("Turn off automatic date and time first")
            self.st["offset"] = naive.replace(tzinfo=ZoneInfo(self.st["zone"])).timestamp() - time.time()
            self._save()


class SystemClock(_Base):
    def __init__(self, path, online, run=subprocess.run, zoneinfo_dir="/usr/share/zoneinfo", localtime="/etc/localtime"):
        super().__init__(path, online)
        self.run, self.zoneinfo_dir, self.localtime = run, zoneinfo_dir, localtime
        self.servers = os.environ.get("INTELLICAT_NTP_SERVERS", "pool.ntp.org time.google.com").split()
        self.interval = int(os.environ.get("INTELLICAT_NTP_INTERVAL", "3600"))
        # what sets the clock once: BusyBox ntpd on the device's image; on a desktop distribution (the simulator) ntpdate (package ntpsec-ntpdate)
        self.ntp_cmd = (lambda s: ["ntpd", "-q", "-n", "-p", s]) if shutil.which("ntpd") or not shutil.which("ntpdate") else (lambda s: ["ntpdate", "-b", s])
        self.last_ok = None  # when the network last set the clock
        try:
            self.set_zone(self.st["zone"])  # /etc/localtime follows the saved zone at every start (the root file system may be read-only image)
        except ClockError:
            pass
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="ntp").start()

    def _loop(self):
        wait = 0
        while True:
            self._wake.wait(wait)
            self._wake.clear()
            wait = 15  # the network is not up yet, or the servers did not answer: try again soon
            if self.st["ntp"] and self.online() and self.sync_once():
                wait = self.interval

    def sync_once(self):
        """Ask the servers one after another until one sets the clock (BusyBox ntpd -q: set once and quit)."""
        for server in self.servers:
            try:
                r = self.run(self.ntp_cmd(server), capture_output=True, timeout=40)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0:
                self.last_ok = time.time()
                self._hwclock()
                return True
        return False

    def _hwclock(self):
        try:
            self.run(["hwclock", "-w", "-u"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass  # no RTC (a device may have none of its own): the time is set again at the next start

    def status(self):
        with self._lock:
            now = time.time()
            local, off = _local(now, self.st["zone"])
            return {"epoch": int(now), "zone": self.st["zone"], "offset": off, "local": local, "ntp": self.st["ntp"],
                    "synced": self.st["ntp"] and self.last_ok is not None}

    def set_zone(self, zone):
        self._check_zone(zone)
        with self._lock:
            try:
                tmp = self.localtime + ".new"
                if os.path.lexists(tmp):
                    os.remove(tmp)
                os.symlink(os.path.join(self.zoneinfo_dir, zone), tmp)
                os.replace(tmp, self.localtime)
            except OSError as e:
                raise ClockError("Could not set the time zone") from e
            self.st["zone"] = zone
            self._save()

    def set_ntp(self, on):
        with self._lock:
            self.st["ntp"] = bool(on)
            if not on:
                self.last_ok = None
            self._save()
        if on:
            self._wake.set()  # sync at once

    def set_time(self, year, month, day, hour, minute):
        naive = _check_time(year, month, day, hour, minute)
        with self._lock:
            if self.st["ntp"]:
                raise ClockError("Turn off automatic date and time first")
            epoch = int(naive.replace(tzinfo=ZoneInfo(self.st["zone"])).timestamp())
            try:
                r = self.run(["date", "-u", "-s", f"@{epoch}"], capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError) as e:
                raise ClockError("Could not set the time") from e
            if r.returncode:
                raise ClockError("Could not set the time")
            self._hwclock()


class AndroidClock(_Base):
    def _ask(self):
        import androidhost
        try:
            return androidhost.call("time_status")
        except androidhost.BridgeError as e:
            raise ClockError("Android does not answer: " + str(e))

    def status(self):
        r = self._ask()
        zone = r["zone"] if r["zone"] in _zones() else "UTC"
        now = time.time()
        local, off = _local(now, zone)
        return {"epoch": int(now), "zone": zone, "offset": off, "local": local, "ntp": r["auto_time"], "synced": r["auto_time"] and self.online()}

    def _settings(self):
        import androidhost
        try:
            androidhost.call("time_settings")
        except androidhost.BridgeError:
            pass
        raise ClockError("Android sets the date, time and zone: its settings are open, change them there")

    def set_zone(self, zone):
        self._check_zone(zone)
        self._settings()

    def set_ntp(self, on):
        self._settings()

    def set_time(self, year, month, day, hour, minute):
        _check_time(year, month, day, hour, minute)
        self._settings()


def make(data_dir, online=lambda: True):
    path = os.path.join(data_dir, "clock.json")
    import androidhost
    if androidhost.ENABLED:
        return AndroidClock(path, online)
    if os.environ.get("INTELLICAT_CLOCK", "fake") == "system":
        return SystemClock(path, online)
    return FakeClock(path, online)
