---
name: wifi-network
description: How to see and change the device's Wi-Fi connection. Use this when the user asks about the network, wants to connect to Wi-Fi or the device is offline.
---

# Wi-Fi

1. `wifi_status` says whether the device is online and to which network.
2. `wifi_networks` scans and lists the networks (the list is needed before connecting).
3. `wifi_connect` with `ssid` connects: a saved or open network needs nothing else, a secured one needs `password`, which only the user can tell you. Ask for it; never guess.

The device keeps the networks it has joined. Connecting can take several seconds; the tool returns the result.
