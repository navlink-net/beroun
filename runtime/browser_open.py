# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The "browser" of a program that signs in through a web page (the Claude Code and Codex CLIs, `BROWSER=<this>`): the address goes to the device browser.

    python3 browser_open.py <url>        (INTELLICAT_PORT names the shell; BROWSER_OPEN_LABEL the tab, "sign-in" by default)

The shell opens it (POST /api/browser/open): the owner signs in on the device's own screen, and the page comes back to the program's own callback on 127.0.0.1 of the
same machine (the device, a simulator, a hosted one), so nothing has to be typed or pasted. Exit 0 when the browser took it, 1 when not (the program then shows its link).
Standard library only."""
import json, os, sys, urllib.request


def main(argv):
    url = next((a for a in argv[1:] if a.startswith(("http://", "https://"))), None)
    if not url:
        print("browser_open: no address", file=sys.stderr)
        return 1
    port = os.environ.get("INTELLICAT_PORT", "8765")
    body = json.dumps({"url": url, "label": os.environ.get("BROWSER_OPEN_LABEL", "sign-in")}).encode()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/browser/open", data=body, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            return 0 if json.load(r).get("ok") else 1
    except Exception as e:   # noqa: BLE001  the shell or the browser is not there: the program's own link is the way left
        print(f"browser_open: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
