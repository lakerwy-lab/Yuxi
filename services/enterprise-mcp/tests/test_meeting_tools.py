from __future__ import annotations

import pytest

from enterprise_mcp import server as server_module
from enterprise_mcp.domains.meeting.tools import register_meeting_tools
from enterprise_mcp.server import GovernedFastMCP
from yuxi.mcp.meeting import MEETING_MCP_TOOL_NAMES


pytestmark = pytest.mark.asyncio


async def test_meeting_tool_schema_does_not_expose_identity(monkeypatch):
    mcp = GovernedFastMCP(name="meeting-test")
    register_meeting_tools(mcp)
    monkeypatch.setattr(
        server_module,
        "require_invocation_claims",
        lambda: {"tools": sorted(MEETING_MCP_TOOL_NAMES)},
    )

    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == set(MEETING_MCP_TOOL_NAMES)
    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert "uid" not in properties
        assert "union_id" not in properties
        assert "user_id" not in properties
