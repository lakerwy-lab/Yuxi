"""会议室 Enterprise MCP 的稳定协议标识。"""

MEETING_MCP_AUDIENCE = "enterprise-mcp:meeting"
MEETING_MCP_SERVER_SLUG = "meeting"
MEETING_MCP_TOOL_NAMES = frozenset(
    {
        "search_available_rooms",
        "preview_booking",
        "confirm_booking",
        "cancel_booking",
        "get_my_bookings",
    }
)
