# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Caroline's tool groups, made from the one table of the assistant's OS tools (beroun-tools/beroun_tools/table.py, docs/assistant-contract.md): every provider of the
assistant slot gets the same tools; here they become her plugins (her loader's Plugin and PluginTool, async handlers).

The groups: beroun (OS Beroun's own tools), agents (the agents of the apps), and one app-<id> per app that has an "assistant" / "caroline" section in its manifest
(docs/app-format.md; her tool-group list shows it as "app-<id> (N tools): <the manifest's description>"). An app's group holds, besides the table's tools of the app:
  * those of the app's MCP servers (names: <id>_<tool>, <id>_<server>_<tool> when the app has several servers), through her pool (pool.py);
The skills of the app are in her skill catalog (hooks.py registers them); the group's guidance names them."""
import os

from beroun_tools import table
from beroun_tools.table import MAX_RESULT_CHARS, json_schema as _json_schema, result as _result, ident as _ident   # noqa: F401  (the old names of this module)

from . import shell
from .pool import POOL


def _skill_names(app_folder):
    """The names of the app's skills, as her catalog has them."""
    from app import skills
    from app.workspace_dir import WORKSPACE_DIR
    root = os.path.realpath(app_folder)
    return [s.name for s in skills.catalog(WORKSPACE_DIR) if os.path.realpath(str(s.folder)).startswith(root + os.sep)]


def _tool(t):
    """A tool of the table as one of hers: her handlers are async and get a progress callback; the table's run on a thread."""
    from app.plugins.loader import PluginTool
    import asyncio

    async def handler(args, _progress, fn=t.handler):
        return await asyncio.to_thread(fn, args or {})
    return PluginTool(name=t.name, description=t.description, input_schema=t.schema, handler=handler)


def _plugin(g, extra=()):
    from app.plugins.loader import Plugin
    return Plugin(name=g.name, description=g.description, tools=[_tool(t) for t in g.tools] + list(extra), usage_instructions=g.guidance)


def _plugin_for(a):
    """An app's group: <id>_open and <id>_close, the tools of its MCP servers, the tools of its page (a name already taken is not offered twice)."""
    from app.plugins.loader import Plugin, PluginTool
    g = table.app_group(a, _skill_names)
    tools, taken = [], set()

    def add(t):
        if t.name not in taken:
            taken.add(t.name)
            tools.append(t)

    for t in g.tools[:2]:
        add(_tool(t))
    servers, ident, folder = a.get("mcp", []), _ident(a["id"]), a["folder"]
    for spec in servers:
        for t in POOL.tools(a["id"], folder, spec):
            name = f"{ident}_{t['name']}" if len(servers) == 1 else f"{ident}_{_ident(spec['id'])}_{t['name']}"

            def make(spec=spec, tool=t["name"]):
                async def call(args, _progress):
                    r = await POOL.call(a["id"], folder, spec, tool, args or {})
                    return _result(r["blocks"], r["is_error"], r["structured"])
                return call
            add(PluginTool(name=name, description=t["description"] or f"{t['name']} of {a['name']}", input_schema=t["input"], handler=make()))
    for t in g.tools[2:]:
        add(_tool(t))
    return Plugin(name=g.name, description=g.description, tools=tools, usage_instructions=g.guidance)


def _beroun_plugin():
    """OS Beroun's own tools: the apps as a whole, and any app's window. They work with an app that declares nothing for the assistant too."""
    return _plugin(table.beroun_group())


def _agents_plugin(agents, asking=()):
    """The agents of the apps (docs/architecture.md): autonomous helpers an app publishes. She gives a task and gets a result; how the agent works is its own business."""
    return _plugin(table.agents_group(agents, asking))


def build():
    """The groups of every app that Caroline can drive, as of now, and the agents of the apps."""
    out = [_beroun_plugin()]
    published = shell.agents()
    if published:
        out.append(_agents_plugin(published, shell.asking()))
    for a in shell.apps():
        try:
            out.append(_plugin_for(a))
        except Exception as e:  # one app's broken section must not cost her the others
            print(f"intellicat-overlay: app {a.get('id')} not offered to Caroline: {e!r}", flush=True)
    return out
