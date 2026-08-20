from __future__ import annotations

import pytest

from enterprise_mcp import server as server_module
from enterprise_mcp.server import GovernedFastMCP


pytestmark = pytest.mark.asyncio


async def test_list_tools_filters_by_signed_claim(monkeypatch):
    mcp = GovernedFastMCP(name="test")

    @mcp.tool(name="read_tool")
    async def read_tool() -> str:
        return "read"

    @mcp.tool(name="write_tool")
    async def write_tool() -> str:
        return "write"

    monkeypatch.setattr(server_module, "require_invocation_claims", lambda: {"tools": ["read_tool"]})

    tools = await mcp.list_tools()

    assert [tool.name for tool in tools] == ["read_tool"]


async def test_call_tool_checks_authorization_again(monkeypatch):
    mcp = GovernedFastMCP(name="test")

    @mcp.tool(name="write_tool")
    async def write_tool() -> str:
        return "write"

    def reject(_name: str):
        raise PermissionError("denied")

    monkeypatch.setattr(server_module, "require_tool_allowed", reject)

    with pytest.raises(PermissionError, match="denied"):
        await mcp.call_tool("write_tool", {})


async def test_discovery_token_cannot_call_tool(monkeypatch):
    mcp = GovernedFastMCP(name="test")

    @mcp.tool(name="read_tool")
    async def read_tool() -> str:
        return "read"

    monkeypatch.setattr(
        server_module,
        "require_tool_allowed",
        lambda _name: {"tools": ["read_tool"], "purpose": "discovery"},
    )

    with pytest.raises(PermissionError, match="发现令牌"):
        await mcp.call_tool("read_tool", {})
