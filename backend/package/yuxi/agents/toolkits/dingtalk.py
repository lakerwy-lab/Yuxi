"""钉钉会议室与转人工工具。"""

from __future__ import annotations

from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field
from sqlalchemy import select

from yuxi.agents.toolkits.registry import tool
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User


class MeetingSearchInput(BaseModel):
    start_time: str | None = Field(default=None, description="ISO 8601 开始时间，必须含时区或使用 Asia/Shanghai")
    end_time: str | None = Field(default=None, description="ISO 8601 结束时间，必须含时区或使用 Asia/Shanghai")


class MeetingPreviewInput(BaseModel):
    room_id: str = Field(description="会议室 ID")
    room_name: str = Field(default="", description="会议室名称")
    title: str = Field(description="会议主题")
    start_time: str = Field(description="ISO 8601 开始时间")
    end_time: str = Field(description="ISO 8601 结束时间")
    description: str | None = Field(default=None, description="会议说明")


class MeetingConfirmInput(BaseModel):
    confirm_token: str = Field(description="preview_booking 返回的确认令牌")


class MeetingCancelInput(BaseModel):
    booking_id: str = Field(description="预订 ID")


class MyBookingsInput(BaseModel):
    """“我的会议室预订”不接收模型侧参数。"""


def _runtime_value(runtime: ToolRuntime, key: str) -> str | None:
    context = getattr(runtime, "context", None)
    value = context.get(key) if isinstance(context, dict) else getattr(context, key, None)
    return str(value).strip() if value else None


async def _current_dingtalk_user(runtime: ToolRuntime) -> User:
    uid = _runtime_value(runtime, "uid")
    if not uid:
        raise ValueError("当前运行缺少 uid")
    async with pg_manager.get_async_session_context() as db:
        user = await db.scalar(select(User).where(User.uid == uid))
        if user is None or not user.dingtalk_union_id:
            raise ValueError("当前用户未绑定钉钉 unionId")
        return user


@tool(
    category="buildin",
    tags=["钉钉", "会议室"],
    display_name="搜索钉钉会议室",
    description="按可选时间段查询当前用户可见的钉钉会议室。会议预订前必须先调用此工具。",
    args_schema=MeetingSearchInput,
)
async def search_meeting_rooms(start_time: str | None, end_time: str | None, runtime: ToolRuntime) -> dict[str, Any]:
    """查询会议室并返回结构化卡片数据。"""
    from yuxi.services.dingtalk_meeting_service import MeetingRoomService

    user = await _current_dingtalk_user(runtime)
    async with pg_manager.get_async_session_context() as db:
        rooms = await MeetingRoomService(db).search_rooms(user.dingtalk_union_id, start_time, end_time)
    return {"type": "meeting_rooms", "items": rooms}


@tool(
    category="buildin",
    tags=["钉钉", "会议室"],
    display_name="预览会议室预订",
    description=(
        "校验会议室和时间并生成短期确认令牌。随后必须只用一次 ask_user_question 让用户选择并确认，不能重复确认。"
    ),
    args_schema=MeetingPreviewInput,
)
async def preview_booking(
    room_id: str,
    room_name: str,
    title: str,
    start_time: str,
    end_time: str,
    description: str | None,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """生成一次用户确认所需的预览。"""
    from yuxi.services.dingtalk_meeting_service import MeetingRoomService

    user = await _current_dingtalk_user(runtime)
    async with pg_manager.get_async_session_context() as db:
        return await MeetingRoomService(db).preview_booking(
            uid=user.uid,
            union_id=user.dingtalk_union_id,
            room_id=room_id,
            room_name=room_name,
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
        )


@tool(
    category="buildin",
    tags=["钉钉", "会议室"],
    display_name="确认会议室预订",
    description="使用用户已经确认的 preview_booking 令牌完成预订；该工具不会再次询问用户。",
    args_schema=MeetingConfirmInput,
)
async def confirm_booking(confirm_token: str, runtime: ToolRuntime) -> dict[str, Any]:
    """在一次用户确认后执行日程创建、会议室预订和失败补偿。"""
    from yuxi.services.dingtalk_meeting_service import MeetingRoomService

    user = await _current_dingtalk_user(runtime)
    async with pg_manager.get_async_session_context() as db:
        return await MeetingRoomService(db).confirm_booking(user.uid, confirm_token)


@tool(
    category="buildin",
    tags=["钉钉", "会议室"],
    display_name="取消会议室预订",
    description="取消当前用户的钉钉会议室预订，并在部分失败时保留补偿状态。",
    args_schema=MeetingCancelInput,
)
async def cancel_booking(booking_id: str, runtime: ToolRuntime) -> dict[str, Any]:
    """取消会议室预订。"""
    from yuxi.services.dingtalk_meeting_service import MeetingRoomService

    user = await _current_dingtalk_user(runtime)
    async with pg_manager.get_async_session_context() as db:
        return await MeetingRoomService(db).cancel_booking(user.uid, booking_id)


@tool(
    category="buildin",
    tags=["钉钉", "会议室"],
    display_name="我的会议室预订",
    description="查询当前用户的钉钉会议室预订记录。",
    args_schema=MyBookingsInput,
)
async def my_bookings(runtime: ToolRuntime) -> dict[str, Any]:
    """查询当前用户预订。"""
    from yuxi.services.dingtalk_meeting_service import MeetingRoomService

    user = await _current_dingtalk_user(runtime)
    async with pg_manager.get_async_session_context() as db:
        return {"type": "meeting_bookings", "items": await MeetingRoomService(db).list_bookings(user.uid)}


__all__ = ["cancel_booking", "confirm_booking", "my_bookings", "preview_booking", "search_meeting_rooms"]
