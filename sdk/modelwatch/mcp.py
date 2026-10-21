"""Wrap a FastMCP-style server so every registered tool is monitored.

Usage:
    from mcp.server.fastmcp import FastMCP
    from modelwatch import wrap_mcp_server

    mcp = FastMCP("my-server")

    @mcp.tool()
    async def search(q: str) -> dict: ...

    wrap_mcp_server(mcp, agent_id="my-mcp-server")
"""

from __future__ import annotations

from typing import Any

from modelwatch.decorator import monitor


def wrap_mcp_server(server: Any, agent_id: str | None = None) -> Any:
    """Wrap every tool on a FastMCP-style server with @monitor.

    For other frameworks, put @monitor on each tool function.
    """
    tool_manager = getattr(server, "_tool_manager", None) or getattr(server, "tool_manager", None)
    if tool_manager is None:
        raise RuntimeError(
            "wrap_mcp_server: could not find a tool manager on this server. "
            "Add @monitor() to each tool function instead."
        )

    tools = getattr(tool_manager, "_tools", None) or getattr(tool_manager, "tools", None)
    if tools is None:
        raise RuntimeError("wrap_mcp_server: tool manager has no tool registry")

    items = tools.items() if hasattr(tools, "items") else enumerate(tools)
    for name, tool in items:
        original = getattr(tool, "fn", None) or getattr(tool, "func", None) or getattr(tool, "callable", None)
        if original is None:
            continue
        wrapped = monitor(tool_name=str(name), agent_id=agent_id)(original)
        for attr in ("fn", "func", "callable"):
            if hasattr(tool, attr):
                setattr(tool, attr, wrapped)
                break
    return server
