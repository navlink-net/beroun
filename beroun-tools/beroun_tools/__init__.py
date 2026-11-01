# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The OS tools of the assistant (docs/assistant-contract.md, section 3): one table for every provider of the slot.

shell.py      the client of shelld (the role "assistant")
table.py      the tool groups: beroun, agents, one app-<id> per app, messages
mcp_server.py the same tools as an MCP server (stdio), for a provider that speaks MCP
Standard library only: Caroline's runtime and the shell's Python both import it."""
