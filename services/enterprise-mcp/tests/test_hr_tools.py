from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from enterprise_mcp import server as server_module
from enterprise_mcp.domains.hr import tools as hr_tools_module
from enterprise_mcp.domains.hr.tools import register_hr_tools
from enterprise_mcp.server import GovernedFastMCP


pytestmark = pytest.mark.asyncio

HR_TOOL_NAMES = {
    "hr_attendance_sign_records",
    "hr_attendance_daily_detail",
    "hr_attendance_summary",
}


class FakeHrClient:
    """记录 HR 工具发出的请求，避免单元测试访问真实接口。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any]) -> Any:
        """记录请求并返回与接口类型匹配的最小数据。"""

        self.calls.append((path, params))
        if path == "/attendance/summary":
            return {"overtime": [], "leave": [], "trip": []}
        return []


def _register_test_tools(monkeypatch, client: FakeHrClient) -> GovernedFastMCP:
    """注册使用可信测试身份和 fake client 的 HR 工具。"""

    mcp = GovernedFastMCP(name="hr-test")
    register_hr_tools(mcp)
    monkeypatch.setattr(
        server_module,
        "require_invocation_claims",
        lambda: {"tools": sorted(HR_TOOL_NAMES)},
    )
    monkeypatch.setattr(
        server_module,
        "require_tool_allowed",
        lambda name: {"tools": sorted(HR_TOOL_NAMES), "purpose": "invocation"},
    )
    monkeypatch.setattr(
        hr_tools_module,
        "require_dingtalk_user_id",
        lambda: ({"sub": "uid"}, "001234567890"),
    )
    monkeypatch.setattr(hr_tools_module, "get_hr_client", lambda: client)
    return mcp


async def test_hr_tool_schema_only_exposes_date_range(monkeypatch):
    client = FakeHrClient()
    mcp = _register_test_tools(monkeypatch, client)

    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == HR_TOOL_NAMES
    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert set(properties) == {"start_date", "end_date"}
        assert set(tool.inputSchema.get("required", [])) == {"start_date", "end_date"}


@pytest.mark.parametrize(
    ("tool_name", "path"),
    [
        ("hr_attendance_sign_records", "/attendance/sign-records"),
        ("hr_attendance_daily_detail", "/attendance/daily-detail"),
        ("hr_attendance_summary", "/attendance/summary"),
    ],
)
async def test_hr_tools_use_trusted_user_id_and_expected_endpoint(monkeypatch, tool_name, path):
    client = FakeHrClient()
    mcp = _register_test_tools(monkeypatch, client)

    await mcp.call_tool(tool_name, {"start_date": "2026-01-01", "end_date": "2026-01-31"})

    assert client.calls == [
        (
            path,
            {
                "ftalkId": "001234567890",
                "startDate": "2026-01-01",
                "endDate": "2026-01-31",
            },
        )
    ]


@pytest.mark.parametrize(
    ("start_date", "end_date", "message"),
    [
        ("2026/01/01", "2026-01-31", "yyyy-MM-dd"),
        ("2026-02-01", "2026-01-31", "不能晚于"),
    ],
)
async def test_hr_tools_reject_invalid_date_range_before_request(monkeypatch, start_date, end_date, message):
    client = FakeHrClient()
    mcp = _register_test_tools(monkeypatch, client)

    with pytest.raises(ToolError, match=message):
        await mcp.call_tool(
            "hr_attendance_sign_records",
            {"start_date": start_date, "end_date": end_date},
        )

    assert client.calls == []


async def test_hr_tools_reject_missing_dingtalk_identity_before_request(monkeypatch):
    client = FakeHrClient()
    mcp = _register_test_tools(monkeypatch, client)

    def reject_missing_identity():
        raise PermissionError("Enterprise MCP 调用上下文缺少钉钉 userId")

    monkeypatch.setattr(hr_tools_module, "require_dingtalk_user_id", reject_missing_identity)

    with pytest.raises(ToolError, match="userId"):
        await mcp.call_tool(
            "hr_attendance_sign_records",
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )

    assert client.calls == []
