# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The shell daemon as the overlay sees it: the one client of every provider of the assistant slot (beroun-tools/beroun_tools/shell.py), under its old name.
This module IS that one (the same object), so what a caller sets here (a test's cache reset) is set there."""
import sys

from beroun_tools import shell as _shell

sys.modules[__name__] = _shell
