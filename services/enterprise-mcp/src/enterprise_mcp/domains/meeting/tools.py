"""会议室 MCP 工具；身份只来自已验签调用上下文。"""

from __future__ import annotations

from typing import Any

from enterprise_mcp.auth import require_invocation_claims
from enterprise_mcp.server import GovernedFastMCP
from yuxi.services.dingtalk_meeting_service import MeetingRoomService
from yuxi.storage.postgres.manager import pg_manager


def _meeting_identity() -> tuple[str, str, str]:
    """从可信 claims 读取 Yuxi uid、钉钉 unionId 和 userId。"""

    claims = require_invocation_claims()
    uid = str(claims.get("sub") or "").strip()
    union_id = str(claims.get("dingtalk_union_id") or "").strip()
    user_id = str(claims.get("dingtalk_user_id") or "").strip()
    if not uid or not union_id or not user_id:
        raise PermissionError("会议室 MCP 调用上下文缺少钉钉身份")
    return uid, union_id, user_id


def register_meeting_tools(mcp: GovernedFastMCP) -> None:
    """向 MCP Server 注册职责单一的会议室工具。"""

    @mcp.tool(name="search_available_rooms", structured_output=True)
    async def search_available_rooms(
        start_time: str | None = None,
        end_time: str | None = None,
        capacity: int = 1,
        building: str | None = None,
        floor: str | None = None,
        equipment: list[str] | None = None,
    ) -> dict[str, Any]:
        """查询当前用户可见且满足时间、容量和位置条件的会议室。"""

        _, union_id, user_id = _meeting_identity()
        async with pg_manager.get_async_session_context() as db:
            rooms = await MeetingRoomService(db).search_rooms(
                union_id,
                start_time,
                end_time,
                capacity=capacity,
                building=building,
                floor=floor,
                equipment=equipment,
                user_id=user_id,
            )
        return {"type": "meeting_rooms", "items": rooms}

    @mcp.tool(name="preview_booking", structured_output=True)
    async def preview_booking(
        room_id: str,
        room_name: str,
        title: str,
        start_time: str,
        end_time: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """校验会议室并生成绑定当前用户和参数的一次性预订确认。"""

        uid, union_id, _ = _meeting_identity()
        async with pg_manager.get_async_session_context() as db:
            return await MeetingRoomService(db).preview_booking(
                uid,
                union_id,
                room_id,
                room_name,
                title,
                start_time,
                end_time,
                description,
            )

    @mcp.tool(name="confirm_booking", structured_output=True)
    async def confirm_booking(confirm_token: str) -> dict[str, Any]:
        """使用当前用户的一次性确认令牌创建日程并预订会议室。"""

        uid, _, _ = _meeting_identity()
        async with pg_manager.get_async_session_context() as db:
            return await MeetingRoomService(db).confirm_booking(uid, confirm_token)

    @mcp.tool(name="cancel_booking", structured_output=True)
    async def cancel_booking(booking_id: str) -> dict[str, Any]:
        """取消属于当前用户的会议室预订。"""

        uid, _, _ = _meeting_identity()
        async with pg_manager.get_async_session_context() as db:
            return await MeetingRoomService(db).cancel_booking(uid, booking_id)

    @mcp.tool(name="get_my_bookings", structured_output=True)
    async def get_my_bookings(status: str | None = None) -> dict[str, Any]:
        """查询当前用户的会议室预订，可按状态过滤。"""

        uid, _, _ = _meeting_identity()
        async with pg_manager.get_async_session_context() as db:
            items = await MeetingRoomService(db).list_bookings(uid)
        if status:
            items = [item for item in items if item.get("status") == status]
        return {"type": "meeting_bookings", "items": items}
