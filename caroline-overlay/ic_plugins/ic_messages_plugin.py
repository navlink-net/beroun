# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Caroline's tools over the shared message store (overlay: found by her plugin loader because hooks.py adds this folder to app.plugins)."""
from ic_messaging.tools import make_plugin

PLUGIN = make_plugin()
