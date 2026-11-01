# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The apps of the device as Caroline's tools (docs/app-format.md, "caroline").

Every app whose manifest has a "caroline" section becomes one tool group of hers ("app-<id>"): the tools of its MCP servers, the tools of its
page, open / close. Its skills join her skill catalog (app/skills.py). She reaches all of it through her usual mechanisms:
list_groups -> list_tools -> describe_tool -> call_tool, and use_skill.
"""
import os as _os, sys as _sys

try:   # the tools themselves are the one table of every provider (beroun-tools/beroun_tools); a backend started by an older shell has only the overlay on its path
    import beroun_tools  # noqa: F401
except ImportError:
    _sys.path.append(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "beroun-tools"))
