# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""flashd - putting an OS Beroun image on the device, and what the device knows about its system (slots, boot control, no bricking).

The device's storage has two system slots, A and B, and the boot control block `bootctl.json`. Here the storage is a directory (the simulator's emulated
eMMC, EMMC_DIR): system_a.img, system_b.img, bootctl.json. A slot that holds no image is the system the device was started with ("factory": the tree).

    bootctl.json   {"active": "a"|"b", "locked": false, "slots": {"a": null | {version, build, board, sha256, installed, ...}, "b": ...},
                    "trial": null | {"slot", "previous", "tries"}, "notice": ""}

THE PROCESS (what a device's recovery does; the simulator imitates it, its screen shows the states, web-shell/recovery.js):
    idle -> recovery -> verifying -> writing -> done (then the device restarts)      or   -> failed (a reason, until it is dismissed)
It starts only with the USB cable in and a charged battery. Every check of imagefmt.verify (signature, board, the system opens with the device's key and its hash
is right, the version is not older) has passed before one byte is written.

AGAINST BRICKING:
  * the running slot is never written: the image goes to the other one, as a .part file, is read back and checked, and only then renamed into place;
    a power cut at any moment leaves the old system untouched (a .part file is just removed at the next start);
  * bootctl.json is replaced atomically (written, fsynced, renamed): it is either the old one or the new one;
  * the new system boots as a TRIAL: 3 starts are allowed; it must say it is up (confirm(): the launcher is drawn and stays up), otherwise the 4th start goes
    back to the system that ran before, and the device shows a notice;
  * at every start the active slot is checked against its hash (a damaged storage goes back to the other slot, or to the factory system);
  * flashing is refused when the battery is low and the charger is not connected.

The simulator has no real USB: the cable is a flag (POST /api/usb from the service menu); a device reads its gadget controller.
"""
import hashlib
import calendar
import json
import os
import threading
import time

import imagefmt

TRIES = 3            # starts a new system gets to come up
MIN_BATTERY = 20     # percent: below it flashing wants the charger


class FlashError(Exception):
    pass


def _epoch(iso):
    """The build time of an image.json (UTC, ISO) as epoch seconds; 0 when it is missing."""
    try:
        return int(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0


class Flasher:
    def __init__(self, emmc_dir, board, keys_path, factory_version, reboot=None, rate_mbs=None, pause_s=None, device_key_path=None, power=None):
        """power() -> (percent, charging) or None; reboot() restarts the device; device_key_path: the private key that opens encrypted images."""
        self.dir, self.board, self.keys_path, self.factory_version, self.reboot, self.power = emmc_dir, board, keys_path, factory_version, reboot, power
        self.device_key_path = device_key_path or os.environ.get("BEROUN_DEVICE_KEY") or os.path.expanduser("~/.intellicat/keys/system-enc.pem")
        self.rate = (float(os.environ.get("FLASH_RATE_MBS", "16")) if rate_mbs is None else rate_mbs) * (1 << 20)   # bytes/s of the imitated cable (0: no limit)
        self.pause = float(os.environ.get("FLASH_PAUSE_S", "2")) if pause_s is None else pause_s   # how long the recovery screens stay readable
        self.lock = threading.Lock()
        self.usb = False
        self.st = self._idle()

    @staticmethod
    def _idle():
        return {"state": "idle", "progress": 0.0, "reason": "", "image": "", "from": "", "to": "", "to_build": "", "encrypted": False}

    # ---- the boot control block ---------------------------------------------------------------------------------------------------
    def _path(self, name):
        return os.path.join(self.dir, name)

    def bootctl(self):
        try:
            with open(self._path("bootctl.json"), encoding="utf-8") as f:
                b = json.load(f)
            if b.get("active") in ("a", "b") and isinstance(b.get("slots"), dict):
                b.setdefault("locked", False)
                b.setdefault("trial", None)
                b.setdefault("notice", "")
                return b
        except (OSError, ValueError):
            pass
        return {"active": "a", "locked": False, "slots": {"a": None, "b": None}, "trial": None, "notice": ""}

    def _save(self, b):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path("bootctl.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(b, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path("bootctl.json"))

    def info(self):
        """What the device runs (Settings > About): the version of the active slot, or of the tree it was started from."""
        b = self.bootctl()
        s = b["slots"].get(b["active"])
        return {"version": s["version"] if s else self.factory_version, "build": s["build"] if s else "", "slot": b["active"],
                "built": s.get("built", 0) if s else 0, "source": "image" if s else "factory", "board": self.board, "locked": bool(b["locked"]), "trial": bool(b["trial"])}

    def status(self):
        b = self.bootctl()
        with self.lock:
            st = dict(self.st)
        slots = {k: ({"version": v["version"], "build": v["build"]} if v else {"version": self.factory_version, "build": "factory"} if k == b["active"] or k == "a"
                      else {"version": "-", "build": "empty"}) for k, v in b["slots"].items()}
        st.update(usb=self.usb, board=self.board, active=b["active"], locked=bool(b["locked"]), version=self.info()["version"], slots=slots,
                  trial=b["trial"], notice=b["notice"])
        return st

    def set_usb(self, on):
        self.usb = bool(on)
        return self.usb

    def lock_device(self, locked):
        b = self.bootctl()
        b["locked"] = bool(locked)
        self._save(b)

    def dismiss_notice(self):
        b = self.bootctl()
        if b["notice"]:
            b["notice"] = ""
            self._save(b)

    # ---- starting the device: the trial and the way back ----------------------------------------------------------------------------------
    def _slot_ok(self, b, slot):
        """A slot is fit to start from: the factory system always is, an image slot when its file is there and still has its hash."""
        s = b["slots"].get(slot)
        if s is None:
            return True
        p = self._path(f"system_{slot}.img")
        try:
            h, n = hashlib.sha256(), 0
            with open(p, "rb") as f:
                while True:
                    c = f.read(1 << 20)
                    if not c:
                        break
                    h.update(c)
            return h.hexdigest() == s["sha256"]
        except OSError:
            return False

    def _fall_back(self, b, why):
        trial = b["trial"]
        other = "b" if b["active"] == "a" else "a"
        want = trial["previous"] if trial and trial.get("previous") in ("a", "b") and trial["previous"] != b["active"] else other
        bad = b["active"]
        for cand in (want, other):
            if cand != bad and self._slot_ok(b, cand):
                b["active"] = cand
                break
        else:
            b["active"] = "a" if b["slots"].get("a") is None else "b" if b["slots"].get("b") is None else b["active"]   # the factory system is the last way back
        b["trial"] = None
        s = b["slots"].get(b["active"])
        b["notice"] = f"{why} Back on {s['version'] if s else self.factory_version} (slot {b['active'].upper()})."
        self._save(b)
        return "rolled_back"

    def boot(self):
        """Called at every start of the device. Returns "ok", "trial" (a new system on trial: it has to confirm), "hang" (the simulated broken system: it will not come up)
        or "rolled_back" (the system that did not start was given up; the previous one runs)."""
        b = self.bootctl()
        for n in ("system_a.img.part", "system_b.img.part"):   # what a power cut left
            try:
                os.unlink(self._path(n))
            except OSError:
                pass
        if not self._slot_ok(b, b["active"]):
            return self._fall_back(b, f"The system of slot {b['active'].upper()} is damaged.")
        t = b["trial"]
        if t and t["slot"] == b["active"]:
            if t["tries"] <= 0:
                s = b["slots"][b["active"]]
                return self._fall_back(b, f"The new system {s['version']} did not start {TRIES} times.")
            t["tries"] -= 1
            self._save(b)
            if b["slots"][b["active"]].get("simulate_boot_failure"):
                return "hang"
            return "trial"
        return "ok"

    def confirm(self):
        """The new system says it is up (the launcher is drawn and stays): the trial is over, the slot is the system now."""
        b = self.bootctl()
        if b["trial"] and b["trial"]["slot"] == b["active"] and not b["slots"][b["active"]].get("simulate_boot_failure"):
            b["trial"] = None
            self._save(b)
            return True
        return False

    # ---- the process ------------------------------------------------------------------------------------------------------------------
    def _set(self, **kw):
        with self.lock:
            self.st.update(kw)

    def start(self, path, ota=False):
        """Begins flashing the image at `path` (a file of this machine); the answer is at once, the process runs in a thread. Over the air (ota) there is no cable."""
        if not self.usb and not ota:
            raise FlashError("the USB cable is not connected")
        p = self.power() if self.power else None
        if p and p[0] < MIN_BATTERY and not p[1]:
            raise FlashError(f"the battery is at {p[0]}%: connect the charger before flashing")
        with self.lock:
            if self.st["state"] not in ("idle", "failed"):
                raise FlashError("the device is flashing already")
            self.st = dict(self._idle(), state="recovery", image=os.path.basename(path), **{"from": self.info()["version"]})
            try:
                meta = imagefmt.read_meta(path)[0]
                self.st.update(to=str(meta["version"]), to_build=str(meta["build"]), encrypted=bool(meta["encrypted"]))
            except imagefmt.ImageError:
                pass   # the checks say so, on the device's screen
        threading.Thread(target=self._run, args=(path,), daemon=True, name="flashd").start()

    def set_active(self, slot):
        """fastboot set_active: the slot that starts next (one that holds an image that is good). The running slot is not changed while a flash goes on."""
        with self.lock:
            if self.st["state"] not in ("idle", "failed"):
                raise FlashError("the device is flashing")
            b = self.bootctl()
            if slot not in ("a", "b"):
                raise FlashError("the slot is a or b")
            if slot == b["active"]:
                return
            if (b["slots"].get(slot) is None and slot != "a") or not self._slot_ok(b, slot):   # slot A with no image is the factory system
                raise FlashError(f"slot {slot.upper()} holds no good system")
            b["trial"] = None
            b["active"] = slot
            self._save(b)

    def dismiss(self):
        with self.lock:
            if self.st["state"] == "failed":
                self.st = self._idle()
        self.dismiss_notice()
        return self.status()

    def _run(self, path):
        try:
            time.sleep(self.pause)   # "restarting into recovery"
            b = self.bootctl()
            self._set(state="verifying", progress=0.0)
            keys = imagefmt.load_keys(self.keys_path)
            dev = imagefmt.load_device_key(self.device_key_path)
            meta = imagefmt.verify(path, self.board, self.info()["version"], keys, locked=b["locked"], device_key=dev,
                                   progress=lambda d, n: self._set(progress=d / n if n else 1.0))
            target = "b" if b["active"] == "a" else "a"   # never the running slot
            self._set(state="writing", progress=0.0)
            dest, tmp = self._path(f"system_{target}.img"), self._path(f"system_{target}.img.part")
            os.makedirs(self.dir, exist_ok=True)
            done, size, t0 = 0, meta["squashfs_size"], time.time()
            with open(tmp, "wb") as out:
                for chunk in imagefmt.iter_plain(path, meta, dev, keys.get("sender")):
                    out.write(chunk)
                    done += len(chunk)
                    self._set(progress=done / size if size else 1.0)
                    if self.rate:   # the cable's speed
                        wait = done / self.rate - (time.time() - t0)
                        if wait > 0:
                            time.sleep(wait)
                out.flush()
                os.fsync(out.fileno())
            with open(tmp, "rb") as back:   # what was written is read back before the slot counts
                if imagefmt.sha256_of(back, size) != meta["squashfs_sha256"]:
                    raise imagefmt.ImageError("the slot does not hold what was sent (write error)")
            os.replace(tmp, dest)
            rec = {"version": meta["version"], "build": meta["build"], "board": meta["board"], "sha256": meta["squashfs_sha256"], "built": _epoch(meta.get("built")), "installed": int(time.time())}
            if meta.get("simulate_boot_failure"):
                rec["simulate_boot_failure"] = True
            b["slots"][target] = rec
            b["trial"] = {"slot": target, "previous": b["active"], "tries": TRIES}   # the new system is on trial
            b["active"] = target   # set_active: the last step, atomic
            self._save(b)
            self._set(state="done", progress=1.0, reason=f"{meta['version']} ({meta['build']}) is on slot {target.upper()}")
            time.sleep(self.pause)
            if self.reboot:
                self.reboot()
            self._set(**self._idle())
        except (imagefmt.ImageError, OSError) as e:
            self._fail(str(e))
        except Exception as e:  # noqa: BLE001
            self._fail(f"flashing stopped: {e}")

    def _fail(self, reason):
        for n in ("system_a.img.part", "system_b.img.part"):
            try:
                os.unlink(self._path(n))
            except OSError:
                pass
        self._set(state="failed", reason=reason)
