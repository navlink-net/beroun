# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Joins the apps to Caroline's process (called from ic_messaging/hooks.py when her plugin loader has been imported; nothing of hers is edited):

* discover_plugins() of her loader returns, besides her own plugins, one group per app (plugins.py) - on every call, so an app installed
  or changed shows in her tool list within a turn;
* the skills of the apps are a source of her skill catalog (app/skills.py, register_source).
"""
import sys


def loader_loaded(module):
    from . import plugins, shell
    orig = module.discover_plugins

    def discover_plugins():
        return orig() + plugins.build()
    module.discover_plugins = discover_plugins

    from app import skills
    skills.register_source("apps", lambda: [folder for a in shell.apps() for folder in a.get("skills", [])])
    print("intellicat-overlay: apps offered to Caroline (tool groups app-<id>, skills of the apps)", file=sys.stderr, flush=True)
