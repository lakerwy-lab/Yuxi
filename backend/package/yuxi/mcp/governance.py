"""企业 MCP 域注册表：统一维护各业务域的 audience、工具名和身份要求。

新增企业 MCP 域时只需在此注册一行，Yuxi 侧和 enterprise-mcp 侧共享同一套标识。
"""

from __future__ import annotations

from yuxi.mcp.meeting import MEETING_MCP_AUDIENCE, MEETING_MCP_SERVER_SLUG, MEETING_MCP_TOOL_NAMES

# server_slug -> (audience, tool_names, 是否要求钉钉身份)
_ENTERPRISE_MCP_SERVERS: dict[str, tuple[str, frozenset[str], bool]] = {
    MEETING_MCP_SERVER_SLUG: (MEETING_MCP_AUDIENCE, MEETING_MCP_TOOL_NAMES, True),
    "hr": (
        "enterprise-mcp:hr",
        frozenset(
            {
                "hr_attendance_sign_records",
                "hr_attendance_daily_detail",
                "hr_attendance_summary",
            }
        ),
        True,
    ),
}


def is_enterprise_mcp_server(server_slug: str) -> bool:
    """判断 MCP Server 是否使用 Yuxi 可信调用协议。"""

    return server_slug in _ENTERPRISE_MCP_SERVERS


def enterprise_mcp_audience(server_slug: str) -> str:
    """获取目标 MCP 域的 audience。"""

    return _ENTERPRISE_MCP_SERVERS[server_slug][0]


def enterprise_mcp_tool_names(server_slug: str) -> frozenset[str]:
    """获取目标 MCP 域已知的全部工具名。"""

    return _ENTERPRISE_MCP_SERVERS[server_slug][1]


def enterprise_mcp_require_dingtalk(server_slug: str) -> bool:
    """该 MCP 域是否要求调用者具备完整钉钉身份。"""

    return _ENTERPRISE_MCP_SERVERS[server_slug][2]


def all_enterprise_mcp_server_slugs() -> tuple[str, ...]:
    """全部企业 MCP 域的 server_slug。"""

    return tuple(_ENTERPRISE_MCP_SERVERS)
