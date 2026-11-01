# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Network control of the shell: what the Wi-Fi app and the launcher's first-start gate use.

One interface, two implementations:
  FakeNet  the simulator: the host's network is always there (INTELLICAT_FAKE_NET=off pretends there is none) and a
           few made-up Wi-Fi networks can be "connected" to, so the Wi-Fi app can be developed without a radio.
  WpaNet   the device (Yocto, wpa_supplicant): drives wpa_supplicant through wpa_cli. Not exercised on hardware yet.
  AndroidNet  OS Beroun on Android: Android's own Wi-Fi through the Kotlin host (androidhost, SystemOps.kt). Chosen when the
           host's bridge is there. Android lets an app neither switch Wi-Fi on and off nor drive the radio: a network is joined as a suggestion (Android asks the
           owner once), and the Wi-Fi panel of Android is opened for the rest.
Choose with INTELLICAT_NET=fake|wpa (default fake); the Wi-Fi interface is INTELLICAT_WIFI_IFACE (default wlan0).

Interface: status() -> {"online", "ssid", "ip"};  scan() -> [{"ssid", "signal" 0..100, "secured", "known", "connected"}];
connect(ssid, password=None); raises NetError with a message fit for the screen.
"""
import os, subprocess, threading, time


class NetError(Exception):
    pass


class FakeNet:
    NETWORKS = [("Home-5G", True, 82), ("IntelliCat Lab", True, 70), ("Cafe Guest", False, 55), ("Neighbor", True, 30)]

    def __init__(self, wired=True):
        self.wired, self.ssid, self.known, self._lock = wired, None, set(), threading.Lock()

    def status(self):
        with self._lock:
            online = self.wired or self.ssid is not None
            return {"online": online, "ssid": self.ssid, "ip": "192.168.0.23" if online else None}

    def scan(self):
        time.sleep(1.5)  # a real scan takes a while: the Wi-Fi app shows "Scanning…" meanwhile
        with self._lock:
            return [{"ssid": s, "signal": sig, "secured": sec, "known": s in self.known, "connected": s == self.ssid}
                    for s, sec, sig in self.NETWORKS]

    def connect(self, ssid, password=None):
        time.sleep(1.0)  # joining takes a moment
        net = next((n for n in self.NETWORKS if n[0] == ssid), None)
        if net is None:
            raise NetError("Network not found")
        with self._lock:
            if net[1] and ssid not in self.known and len(password or "") < 8:  # the made-up networks accept any password of 8+ characters
                raise NetError("Wrong password")
            self.ssid = ssid
            self.known.add(ssid)


class WpaNet:
    def __init__(self, iface="wlan0"):
        self.iface = iface

    def _cli(self, *args):
        r = subprocess.run(["wpa_cli", "-i", self.iface, *args], capture_output=True, text=True, timeout=15)
        return r.stdout

    def _networks(self):
        """{ssid: network id} of the saved networks."""
        out = {}
        for line in self._cli("list_networks").splitlines()[1:]:
            f = line.split("\t")
            if len(f) >= 2:
                out[f[1]] = f[0]
        return out

    def status(self):
        kv = dict(line.split("=", 1) for line in self._cli("status").splitlines() if "=" in line)
        online = kv.get("wpa_state") == "COMPLETED" and bool(kv.get("ip_address"))
        return {"online": online, "ssid": kv.get("ssid") if online else None, "ip": kv.get("ip_address")}

    def scan(self):
        self._cli("scan")
        time.sleep(1.5)
        known, cur = self._networks(), self.status()["ssid"]
        best = {}
        for line in self._cli("scan_results").splitlines()[1:]:
            f = line.split("\t")
            if len(f) < 5 or not f[4]:
                continue
            ssid, dbm, flags = f[4], int(f[2]), f[3]
            sig = max(0, min(100, 2 * (dbm + 100)))
            if ssid not in best or sig > best[ssid]["signal"]:
                best[ssid] = {"ssid": ssid, "signal": sig, "secured": any(x in flags for x in ("WPA", "RSN", "WEP")),
                              "known": ssid in known, "connected": ssid == cur}
        return sorted(best.values(), key=lambda n: -n["signal"])

    def connect(self, ssid, password=None):
        known = self._networks()
        new = ssid not in known
        nid = known.get(ssid) or self._cli("add_network").strip()
        if new:
            self._cli("set_network", nid, "ssid", ssid.encode().hex())  # hex: any bytes in a name are safe
            if password:
                self._cli("set_network", nid, "psk", f'"{password}"')
            else:
                self._cli("set_network", nid, "key_mgmt", "NONE")
        self._cli("enable_network", nid)
        self._cli("select_network", nid)
        for _ in range(30):
            time.sleep(1)
            st = self.status()
            if st["online"] and st["ssid"] == ssid:
                self._cli("save_config")
                return
        if new:
            self._cli("remove_network", nid)
        raise NetError("Could not connect (wrong password?)")


class AndroidNet:
    def _call(self, op, **a):
        import androidhost
        try:
            return androidhost.call(op, **a)
        except androidhost.BridgeError as e:
            msg = str(e)
            if msg.startswith("permission:"):
                raise NetError(msg.split(":", 1)[1].strip().capitalize())
            raise NetError("Android does not answer: " + msg)

    def status(self):
        r = self._call("wifi_status")
        return {"online": r["online"], "ssid": r["ssid"], "ip": r["ip"]}

    def scan(self):
        self._call("wifi_scan")
        time.sleep(2.5)   # the radio takes a moment; Android hands back its last results if it limited the scan
        return self._call("wifi_results")["networks"]

    def connect(self, ssid, password=None):
        st = self._call("wifi_connect", ssid=ssid, password=password or "")["status"]
        # 0 success, 2 internal error, 3 not allowed, 4 duplicate, 5 exceeds the limit, 6 removed by the owner, 7 restricted by the owner (WifiManager.STATUS_NETWORK_SUGGESTIONS_*)
        if st not in (0, 4):
            raise NetError({3: "Android does not allow this app to add networks", 5: "Too many saved networks", 6: "The owner removed this network", 7: "Network suggestions are turned off for this app in Android's settings"}.get(st, "Android refused the network"))
        for _ in range(30):   # Android joins a suggested network by itself, after the owner approved it; the first approval is a notification
            time.sleep(1)
            s = self.status()
            if s["online"] and s["ssid"] == ssid:
                return
        raise NetError("Could not connect: approve the network in the notification of Android, then try again (wrong password?)")

    def panel(self):
        """Android's own Wi-Fi panel (turning Wi-Fi on or off is Android's alone)."""
        self._call("wifi_panel")


def make():
    import androidhost
    if androidhost.ENABLED:
        return AndroidNet()
    if os.environ.get("INTELLICAT_NET", "fake") == "wpa":
        return WpaNet(os.environ.get("INTELLICAT_WIFI_IFACE", "wlan0"))
    return FakeNet(wired=os.environ.get("INTELLICAT_FAKE_NET", "on") != "off")
