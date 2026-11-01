# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""What the system tells the owner by itself (the launcher's notification panel): the network coming and going, a low battery, a full SD card.
One look at the system gives the notices that are due; `seen` keeps what was told, so that each is said once and again after it was put right.
Used by shelld's watcher thread (tests/system_notices_test.py)."""
import re

BATTERY_WARN, BATTERY_LOW = 20, 10   # percent: a notice at each level, and again after a charge
SD_FULL = 95                         # percent of the card used


def battery_level(cap, charging):
    """0 fine (or charging), 1 low, 2 very low."""
    return 0 if charging else 2 if cap <= BATTERY_LOW else 1 if cap <= BATTERY_WARN else 0


def notices(seen, net, power, used_pct):
    """[(text, level, loud)] due now. seen: the state of the last look (changed in place); net: {"online", "ssid"}; power: {"capacity", "status"};
    used_pct: percent of the card used, None when unknown. loud: with sound and vibration."""
    out = []
    online = bool(net["online"])
    if seen.get("online") is not None and online != seen["online"]:
        out.append((f"Network connected{': ' + net['ssid'] if net.get('ssid') else ''}" if online else "Network connection lost", "info" if online else "warning", False))
    seen["online"] = online
    status = power.get("status", "")
    charging = bool(re.search("charg", status, re.I)) and not re.search("dis", status, re.I)
    lvl = battery_level(power["capacity"], charging)
    if lvl > seen.get("battery", 0):
        out.append((f"Battery low: {power['capacity']}%", "warning" if lvl == 1 else "error", True))
        seen["battery"] = lvl
    elif charging or power["capacity"] > BATTERY_WARN + 5:
        seen["battery"] = 0
    if used_pct is not None:
        if used_pct >= SD_FULL and not seen.get("card"):
            out.append((f"SD card is {used_pct:.0f}% full", "warning", False))
            seen["card"] = True
        elif used_pct < SD_FULL - 5:
            seen["card"] = False
    return out
