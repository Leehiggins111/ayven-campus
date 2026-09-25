"""Small local MCP server used by tests and the validation preflight.

It is not a product integration. Config points the client here; the client
does not hard-code this module.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("ayven-local")


@server.tool(description="Read a topic from the local Ayven fixture server.")
def ayven_lookup(topic: str) -> str:
    return f"AYVEN_MCP_OK:{topic}"


@server.tool(description="Write action. Ayven must approval-gate this before calling it.")
def ayven_send(message: str) -> str:
    return f"AYVEN_MCP_SENT:{message}"


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
