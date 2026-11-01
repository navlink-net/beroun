# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""A native part of a store package on Android: the process the shell would start on the other platforms is, here, a `.so` with
`int beroun_main(int argc, char **argv)` that the Kotlin native host loads (the app may not run a file of its data folder). This is the Popen-like handle of such a part,
with the interface shelld and the service manager use: pid, poll, wait, terminate, kill. One part runs at a time (the host is one process: Android/.../NativeHost.kt)."""
import os, subprocess, threading, time

import androidhost

_seq = [800000]   # a number above any pid of the phone, as androidhost.Layer's


def is_part(cmd):
    """True when the command is a store package's native part on Android: the host's bridge is there and the program is a .so by absolute path."""
    return bool(androidhost.ENABLED and cmd and os.path.isabs(cmd[0]) and cmd[0].endswith(".so"))


class NativePart:
    def __init__(self, view, cmd, env=None):
        _seq[0] += 1
        self.pid, self.view, self.returncode = _seq[0], view, None
        androidhost.call("native_start", view=view, lib=cmd[0], args=list(cmd[1:]), env=dict(env or {}))
        self._checked = time.time()

    def poll(self):
        if self.returncode is None:
            try:
                if not androidhost.call("native_running", view=self.view).get("running"):
                    self.returncode = 0
            except androidhost.BridgeError:
                self.returncode = 0
        return self.returncode

    def wait(self, timeout=None):
        end = None if timeout is None else time.time() + timeout
        while self.poll() is None:
            if end is not None and time.time() > end:
                raise subprocess.TimeoutExpired(self.view, timeout)
            time.sleep(0.2)
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            try:
                androidhost.call("native_stop", view=self.view)
            except androidhost.BridgeError:
                self.returncode = 0

    kill = terminate
