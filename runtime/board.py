# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""board - where the device's own files are. OS Beroun does not know what device it runs on: the buttons' map, the swap's settings, the
compositor's and the sound's configuration belong to the board (docs/TARGETS.md). The target names the board's directory in BEROUN_BOARD_DIR
(its lib.sh, or the image's init); the files are in its layer/ subdirectory. Checked out as the submodule of IntelliCat, with nothing set,
the board is IntelliCat's Firmware/board/intellicat-sim next to it (programs started by hand, the tests)."""
import os

_SIBLING = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "Firmware", "board", "intellicat-sim")


def dir():
    d = os.environ.get("BEROUN_BOARD_DIR") or _SIBLING
    if not os.path.isdir(d):
        raise RuntimeError("BEROUN_BOARD_DIR does not name the board's directory (the target sets it: Simulator/scripts/lib.sh)")
    return d


def layer(name):
    """The path of a file of the board's layer."""
    p = os.path.join(dir(), "layer", name)
    if not os.path.isfile(p):
        raise RuntimeError(f"the board has no layer/{name} ({dir()})")
    return p


def env(name, default=""):
    """A value of the board's board.env (NAME="value" lines)."""
    try:
        with open(os.path.join(dir(), "board.env"), encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return default
