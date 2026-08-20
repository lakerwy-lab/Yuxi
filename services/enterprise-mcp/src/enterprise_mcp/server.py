"""带工具可见性过滤和调用二次校验的 FastMCP Server。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ContentBlock, Tool

from enterprise_mcp.auth import require_invocation_claims, require_tool_allowed


class GovernedFastMCP(FastMCP):
    """根据可信令牌同时约束 tools/list 和 tools/call。"""

    async def list_tools(self) -> list[Tool]:
        """只返回当前调用上下文允许的工具。"""

        claims = require_invocation_claims()
        allowed_tools = claims.get("tools")
        if not isinstance(allowed_tools, list):
            raise PermissionError("MCP 调用上下文缺少工具授权")
        tools = await super().list_tools()
        return [tool for tool in tools if tool.name in allowed_tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[ContentBlock] | dict[str, Any]:
        """在实际执行工具前再次校验授权。"""

        claims = require_tool_allowed(name)
        if claims.get("purpose") != "invocation":
            raise PermissionError("工具发现令牌不能执行工具")
        return await super().call_tool(name, arguments)
