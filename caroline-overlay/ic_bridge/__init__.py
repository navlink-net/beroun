# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Her tools for another assistant of the slot (docs/assistant-contract.md, section 3).

Her backend runs in every build, as Communicator's host. In a build whose assistant is Claude or Codex her own plugins (the messages and the messengers' send tools,
mail, memory, reminders, calendar, contacts, notes, images, the device browser) are served from her process to that assistant:

* api.py: /api/ic/bridge/* on her FastAPI app: the list of those tools and a call of one, run by her own dispatch() as her model's calls are;
* reminders.py: a reminder scheduled by that assistant comes due in her scheduler and is handed to the shell, which gives it to the assistant's chat;
* beroun-tools/beroun_tools/bridge_server.py: the MCP server (`caroline-tools`) the assistant's backend starts, a client of api.py.

Nothing of hers is edited (hooks.py of ic_messaging mounts the router and wraps the scheduler's callback)."""

TAB_PREFIX = "assistant:"   # the tab of her session context for a call of another assistant: "assistant:<its tab>" (her reminders keep it as their tabId)
