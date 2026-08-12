"""钉钉会议室 API 客户端和一次确认的预订状态机。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_meeting import BookingConfirmation, RoomBooking
from yuxi.utils.datetime_utils import SHANGHAI_TZ, shanghai_now, utc_now_naive


class DingTalkMeetingError(RuntimeError):
    """会议室 API 或本地状态机错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DingTalkMeetingConfig:
    """读取会议室 app API 所需配置。"""

    def __init__(self) -> None:
        self.client_id = os.getenv("DINGTALK_MEETING_CLIENT_ID", os.getenv("DINGTALK_CLIENT_ID", "")).strip()
        self.client_secret = os.getenv(
            "DINGTALK_MEETING_CLIENT_SECRET", os.getenv("DINGTALK_CLIENT_SECRET", "")
        ).strip()
        self.api_base_url = os.getenv("DINGTALK_API_BASE_URL", "https://api.dingtalk.com").strip()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


class DingTalkMeetingClient:
    """封装 app access token、会议室分页和日程补偿 API。"""

    def __init__(self) -> None:
        self.config = DingTalkMeetingConfig()
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def get_access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 300:
            return self._token
        async with self._token_lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            if not self.config.configured:
                raise DingTalkMeetingError("NOT_CONFIGURED", "未配置钉钉会议室 appKey/appSecret")
            body = await self._request_raw(
                "POST",
                "/v1.0/oauth2/accessToken",
                json={"appKey": self.config.client_id, "appSecret": self.config.client_secret},
                authenticated=False,
            )
            token = body.get("accessToken")
            if not isinstance(token, str) or not token:
                raise DingTalkMeetingError("TOKEN_INVALID", "钉钉 token 响应缺少 accessToken")
            self._token = token
            self._expires_at = time.time() + int(body.get("expireIn", 7200))
            return token

    async def list_meeting_rooms(self, union_id: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        rooms: list[dict[str, Any]] = []
        next_token: int | None = 0
        while next_token is not None:
            params = {"unionId": union_id, "maxResults": min(max_results, 100)}
            if next_token:
                params["nextToken"] = next_token
            body = await self._request("GET", "/v1.0/rooms/meetingRoomLists", params=params)
            result = body.get("result")
            page = result if isinstance(result, list) else (result or {}).get("roomList", [])
            rooms.extend(item for item in page if isinstance(item, dict))
            payload = result if isinstance(result, dict) else body
            raw_next = payload.get("nextToken", payload.get("next_token")) if isinstance(payload, dict) else None
            has_more = (
                bool(payload.get("hasMore", payload.get("has_more", False))) if isinstance(payload, dict) else False
            )
            try:
                next_token = int(raw_next) if has_more and raw_next is not None else None
            except (TypeError, ValueError):
                next_token = None
        return rooms

    async def query_room_availability(
        self, union_id: str, room_ids: list[str], start_time: str, end_time: str
    ) -> list[dict[str, Any]]:
        """分批查询会议室忙闲，钉钉 API 对单次 roomIds 数量有限制。"""
        results: list[dict[str, Any]] = []
        batch_size = 20
        for i in range(0, len(room_ids), batch_size):
            batch = room_ids[i : i + batch_size]
            body = await self._request(
                "POST",
                f"/v1.0/calendar/users/{union_id}/meetingRooms/schedules/query",
                json={"roomIds": batch, "startTime": start_time, "endTime": end_time},
            )
            result = body.get("result")
            if not isinstance(result, list):
                result = body.get("scheduleInformation", [])
            results.extend(item for item in result if isinstance(item, dict))
        return results

    async def create_schedule(
        self,
        union_id: str,
        title: str,
        start_time: str,
        end_time: str,
        description: str | None,
        client_token: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1.0/calendar/users/{union_id}/calendars/primary/events",
            json={
                "summary": title,
                "isAllDay": False,
                "start": {"dateTime": start_time, "timeZone": "Asia/Shanghai"},
                "end": {"dateTime": end_time, "timeZone": "Asia/Shanghai"},
                **({"description": description} if description else {}),
            },
            headers={"x-client-token": client_token},
        )

    async def reserve_room(self, union_id: str, room_id: str, calendar_id: str, event_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1.0/calendar/users/{union_id}/calendars/{calendar_id}/events/{event_id}/meetingRooms",
            json={"meetingRoomsToAdd": [{"roomId": room_id}]},
        )

    async def cancel_room(self, union_id: str, room_id: str, calendar_id: str, event_id: str) -> None:
        await self._request(
            "POST",
            f"/v1.0/calendar/users/{union_id}/calendars/{calendar_id}/events/{event_id}/meetingRooms/batchRemove",
            json={"meetingRoomsToRemove": [{"roomId": room_id}]},
        )

    async def delete_schedule(self, union_id: str, calendar_id: str, event_id: str) -> None:
        await self._request(
            "DELETE",
            f"/v1.0/calendar/users/{union_id}/calendars/{calendar_id}/events/{event_id}",
        )

    async def get_user_work_place(self, user_id: str) -> str | None:
        """查钉钉用户办公地（work_place），用于会议室按地区排序。"""

        token = await self.get_access_token()
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(
                    f"{self.config.api_base_url.rstrip('/')}/topapi/v2/user/get",
                    params={"access_token": token},
                    json={"userid": user_id},
                )
                response.raise_for_status()
                result = response.json().get("result", {})
                return result.get("work_place") or None
        except (httpx.HTTPError, ValueError):
            return None

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self.get_access_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["x-acs-dingtalk-access-token"] = token
        return await self._request_raw(method, path, headers=headers, **kwargs)

    async def _request_raw(
        self, method: str, path: str, *, authenticated: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        if authenticated:
            headers["x-acs-dingtalk-access-token"] = await self.get_access_token()
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.request(
                    method,
                    f"{self.config.api_base_url.rstrip('/')}{path}",
                    headers=headers,
                    **kwargs,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DingTalkMeetingError("DINGTALK_API_ERROR", "钉钉会议室接口请求失败") from exc
        if not isinstance(body, dict):
            raise DingTalkMeetingError("DINGTALK_API_ERROR", "钉钉会议室接口返回格式错误")
        code = body.get("code")
        errcode = body.get("errcode")
        if code not in (None, "", 0, "0", "OK", "SUCCESS") or errcode not in (None, "", 0, "0"):
            raise DingTalkMeetingError(
                "DINGTALK_API_ERROR", str(body.get("message") or body.get("errmsg") or "钉钉接口返回业务错误")
            )
        return body


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DingTalkMeetingError("INVALID_TIME_RANGE", "时间必须是 ISO 8601 格式") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def validate_time_range(start_time: str, end_time: str) -> tuple[str, str]:
    """校验并规范会议时间，支持相对日期解析后的 ISO 输入。"""
    start = _parse_time(start_time)
    end = _parse_time(end_time)
    if end <= start or start < shanghai_now():
        raise DingTalkMeetingError("INVALID_TIME_RANGE", "会议时间必须晚于当前时间且结束时间晚于开始时间")
    if start.date() != end.date():
        raise DingTalkMeetingError("INVALID_TIME_RANGE", "会议开始和结束必须在同一天")
    if end - start > timedelta(hours=8):
        raise DingTalkMeetingError("INVALID_TIME_RANGE", "单次会议不能超过 8 小时")
    return start.isoformat(), end.isoformat()


def _is_busy(item: dict[str, Any]) -> bool:
    schedules = item.get("scheduleItems", item.get("schedule_items", item.get("schedules", [])))
    return bool(schedules)


def _normalize_room(raw: dict[str, Any]) -> dict[str, Any]:
    """将钉钉原始房间字段标准化为统一格式，便于过滤和展示。

    钉钉 API 返回：roomId, roomName, roomCapacity, roomLocation(dict),
    roomLabels(list of {labelId, labelName}), roomGroup(dict with groupName)。
    """
    labels = raw.get("roomLabels") or raw.get("equipment") or []
    equipment = []
    for label in labels:
        if isinstance(label, dict):
            name = label.get("labelName") or label.get("name")
            if name:
                equipment.append(name)
        elif isinstance(label, str):
            equipment.append(label)

    location_obj = raw.get("roomLocation") or {}
    if isinstance(location_obj, dict):
        location = location_obj.get("title") or location_obj.get("desc") or ""
    else:
        location = str(location_obj) if location_obj else ""

    group = raw.get("roomGroup") or {}
    building = group.get("groupName") if isinstance(group, dict) else None

    return {
        "roomId": str(raw.get("roomId") or raw.get("room_id") or raw.get("id") or ""),
        "roomName": raw.get("roomName") or raw.get("room_name") or raw.get("name") or "未命名会议室",
        "capacity": int(raw.get("roomCapacity") or raw.get("capacity") or 0),
        "location": location or raw.get("location") or "",
        "building": building or raw.get("building"),
        "floor": str(raw.get("floor")) if raw.get("floor") else None,
        "equipment": equipment,
    }


class MeetingRoomService:
    """会议室查询、一次确认和补偿流程。"""

    def __init__(self, db: AsyncSession, client: DingTalkMeetingClient | None = None):
        self.db = db
        self.client = client or DingTalkMeetingClient()

    async def search_rooms(
        self,
        union_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        *,
        capacity: int = 1,
        building: str | None = None,
        floor: str | None = None,
        equipment: list[str] | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raw_rooms = await self.client.list_meeting_rooms(union_id)
        rooms = [_normalize_room(r) for r in raw_rooms]
        # 排除已停用楼层
        rooms = [r for r in rooms if r.get("building") != "已停用"]
        # 容量过滤
        rooms = [r for r in rooms if int(r.get("capacity", 0) or 0) >= capacity]
        # 楼宇过滤（宽松子串匹配 + location 回退）
        if building:
            rooms = [
                r for r in rooms
                if r.get("building") == building
                or building in (r.get("building") or "")
                or building in (r.get("location") or "")
            ]
        # 楼层过滤
        if floor:
            rooms = [r for r in rooms if str(r.get("floor") or "") == str(floor)]
        # 设施过滤（全匹配）
        if equipment:
            rooms = [
                r for r in rooms
                if all(item in (r.get("equipment") or []) for item in equipment)
            ]
        # 按用户办公地排序：同地区在前，再按容量升序
        work_place = await self.client.get_user_work_place(user_id) if user_id else None
        if work_place:
            rooms.sort(key=lambda r: (
                0 if work_place in (r.get("building") or "") or work_place in (r.get("location") or "") else 1,
                int(r.get("capacity", 0) or 0),
                r.get("roomName") or "",
            ))
        if not start_time or not end_time:
            return rooms
        normalized_start, normalized_end = validate_time_range(start_time, end_time)
        room_ids = [str(r.get("roomId")) for r in rooms if r.get("roomId")]
        availability = await self.client.query_room_availability(union_id, room_ids, normalized_start, normalized_end)
        busy = {str(item.get("roomId") or item.get("room_id")) for item in availability if _is_busy(item)}
        return [
            {**room, "available": str(room.get("roomId")) not in busy}
            for room in rooms
        ]

    async def preview_booking(
        self,
        uid: str,
        union_id: str,
        room_id: str,
        room_name: str,
        title: str,
        start_time: str,
        end_time: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        normalized_start, normalized_end = validate_time_range(start_time, end_time)
        rooms = await self.search_rooms(union_id, normalized_start, normalized_end)
        room = next(
            (item for item in rooms if str(item.get("roomId") or item.get("room_id") or item.get("id")) == room_id),
            None,
        )
        if room is None:
            raise DingTalkMeetingError("ROOM_NOT_FOUND", "会议室不存在或当前用户无权访问")
        if room.get("available") is False:
            raise DingTalkMeetingError("ROOM_ALREADY_BOOKED", "会议室在该时间段已被预订")
        token = secrets.token_urlsafe(32)
        expires_at = shanghai_now() + timedelta(minutes=5)
        payload = {
            "room_id": room_id,
            "room_name": room_name or room.get("roomName") or room.get("name") or room_id,
            "title": title,
            "start_time": normalized_start,
            "end_time": normalized_end,
            "description": description,
            "union_id": union_id,
        }
        self.db.add(
            BookingConfirmation(
                id=f"bc_{secrets.token_hex(8)}",
                token=token,
                booking_payload=payload,
                uid=uid,
                expires_at=expires_at.isoformat(),
            )
        )
        await self.db.commit()
        return {"confirm_token": token, "expires_at": expires_at.isoformat(), "preview": payload}

    async def confirm_booking(self, uid: str, confirm_token: str) -> dict[str, Any]:
        result = await self.db.execute(select(BookingConfirmation).where(BookingConfirmation.token == confirm_token))
        confirmation = result.scalar_one_or_none()
        if confirmation is None or confirmation.uid != uid:
            raise DingTalkMeetingError("CONFIRM_TOKEN_INVALID", "确认令牌无效")
        if confirmation.used:
            raise DingTalkMeetingError("CONFIRM_TOKEN_USED", "确认令牌已使用")
        if _parse_time(confirmation.expires_at) < shanghai_now():
            raise DingTalkMeetingError("CONFIRM_TOKEN_EXPIRED", "确认令牌已过期，请重新选择会议室")
        payload = dict(confirmation.booking_payload or {})
        start_time, end_time = validate_time_range(payload["start_time"], payload["end_time"])
        availability = await self.client.query_room_availability(payload["union_id"], [payload["room_id"]], start_time, end_time)
        if any(_is_busy(item) for item in availability):
            raise DingTalkMeetingError("ROOM_ALREADY_BOOKED", "会议室刚刚被其他人预订，请重新选择")

        idem = hashlib.sha256(f"{uid}:{confirm_token}".encode()).hexdigest()
        existing_result = await self.db.execute(select(RoomBooking).where(RoomBooking.idempotency_key == idem))
        existing = existing_result.scalar_one_or_none()
        if existing and existing.status == "BOOKED":
            return self._serialize_booking(existing)
        if existing and existing.status != "FAILED":
            raise DingTalkMeetingError("BOOKING_IN_PROGRESS", "该预订正在处理中，请稍后重试")
        booking_id = f"bk_{secrets.token_hex(8)}"
        now = utc_now_naive().isoformat()
        if existing:
            booking = existing
            booking.status = "CREATING"
            booking.error_message = None
            booking.updated_at = now
        else:
            booking = RoomBooking(
                id=booking_id,
                uid=uid,
                union_id=payload["union_id"],
                room_id=payload["room_id"],
                room_name=payload["room_name"],
                title=payload["title"],
                description=payload.get("description"),
                start_time=start_time,
                end_time=end_time,
                status="CREATING",
                idempotency_key=idem,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            self.db.add(booking)
        await self.db.commit()
        event_id = ""
        calendar_id = "primary"
        try:
            schedule = await self.client.create_schedule(
                payload["union_id"],
                payload["title"],
                start_time,
                end_time,
                payload.get("description"),
                booking_id,
            )
            event_id = str(schedule.get("id") or schedule.get("eventId") or schedule.get("event_id") or "")
            calendar_id = str(schedule.get("calendarId") or schedule.get("calendar_id") or "primary")
            if not event_id:
                raise DingTalkMeetingError("DINGTALK_API_ERROR", "钉钉日程响应缺少 event id")
            await self.client.reserve_room(payload["union_id"], payload["room_id"], calendar_id, event_id)
        except DingTalkMeetingError as exc:
            compensation_error = None
            if event_id:
                try:
                    await self.client.delete_schedule(payload["union_id"], calendar_id, event_id)
                except DingTalkMeetingError as cleanup_exc:
                    compensation_error = str(cleanup_exc)
            booking.status = "COMPENSATION_REQUIRED" if compensation_error else "FAILED"
            booking.calendar_id = calendar_id if event_id else None
            booking.schedule_id = event_id or None
            booking.error_message = "; ".join(
                message
                for message in (
                    str(exc),
                    f"补偿失败：{compensation_error}" if compensation_error else None,
                )
                if message
            )
            booking.updated_at = utc_now_naive().isoformat()
            await self.db.commit()
            raise
        booking.status = "BOOKED"
        booking.calendar_id = calendar_id
        booking.schedule_id = event_id
        booking.updated_at = utc_now_naive().isoformat()
        confirmation.used = True
        confirmation.used_at = utc_now_naive().isoformat()
        await self.db.commit()
        return self._serialize_booking(booking)

    async def list_bookings(self, uid: str) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(RoomBooking).where(RoomBooking.uid == uid).order_by(RoomBooking.start_time.desc())
        )
        return [self._serialize_booking(item) for item in result.scalars().all()]

    async def cancel_booking(self, uid: str, booking_id: str) -> dict[str, Any]:
        result = await self.db.execute(select(RoomBooking).where(RoomBooking.id == booking_id, RoomBooking.uid == uid))
        booking = result.scalar_one_or_none()
        if booking is None:
            raise DingTalkMeetingError("BOOKING_NOT_FOUND", "预订不存在")
        if booking.status != "BOOKED":
            raise DingTalkMeetingError("BOOKING_NOT_CANCELABLE", "当前状态不可取消")
        booking.status = "CANCELLING"
        booking.updated_at = utc_now_naive().isoformat()
        await self.db.commit()
        room_cancelled = schedule_deleted = True
        error_message = None
        try:
            await self.client.cancel_room(
                booking.union_id, booking.room_id, booking.calendar_id or "primary", booking.schedule_id or ""
            )
        except DingTalkMeetingError as exc:
            room_cancelled = False
            error_message = str(exc)
        try:
            await self.client.delete_schedule(
                booking.union_id, booking.calendar_id or "primary", booking.schedule_id or ""
            )
        except DingTalkMeetingError as exc:
            schedule_deleted = False
            error_message = error_message or str(exc)
        booking.status = "CANCELLED" if room_cancelled and schedule_deleted else "CANCEL_PARTIAL"
        booking.error_message = error_message
        booking.cancelled_at = utc_now_naive().isoformat()
        booking.updated_at = utc_now_naive().isoformat()
        await self.db.commit()
        return self._serialize_booking(booking)

    @staticmethod
    def _serialize_booking(booking: RoomBooking) -> dict[str, Any]:
        return {
            "booking_id": booking.id,
            "room_id": booking.room_id,
            "room_name": booking.room_name,
            "title": booking.title,
            "start_time": booking.start_time,
            "end_time": booking.end_time,
            "status": booking.status,
            "calendar_id": booking.calendar_id,
            "schedule_id": booking.schedule_id,
            "error_message": booking.error_message,
        }


dingtalk_meeting_client = DingTalkMeetingClient()


async def cleanup_expired_booking_confirmations(ctx: dict[str, Any] | None = None) -> int:
    """定期删除已过期的一次性会议预订确认令牌。"""
    del ctx
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            delete(BookingConfirmation).where(BookingConfirmation.expires_at < shanghai_now().isoformat())
        )
        await db.commit()
        return result.rowcount or 0


async def compensate_stale_bookings(ctx: dict[str, Any] | None = None) -> int:
    """周期补偿卡死的会议室预订：CANCEL_PARTIAL 重试取消，CREATING 超时回滚。

    - CANCEL_PARTIAL：取消房间或删除日程部分失败，重试两端操作直到成功。
    - CREATING：创建中超过 10 分钟仍未完成，标记 FAILED 并清理可能创建的日程。
    """
    del ctx
    from yuxi.storage.postgres.manager import pg_manager

    client = DingTalkMeetingClient()
    compensated = 0
    async with pg_manager.get_async_session_context() as db:
        # CANCEL_PARTIAL：重试取消
        result = await db.execute(
            select(RoomBooking).where(RoomBooking.status == "CANCEL_PARTIAL").limit(20)
        )
        for booking in result.scalars().all():
            try:
                if booking.schedule_id and booking.calendar_id:
                    try:
                        await client.cancel_room(
                            booking.union_id, booking.room_id, booking.calendar_id, booking.schedule_id
                        )
                    except DingTalkMeetingError:
                        pass
                    try:
                        await client.delete_schedule(booking.union_id, booking.calendar_id, booking.schedule_id)
                    except DingTalkMeetingError:
                        pass
                booking.status = "CANCELLED"
                booking.updated_at = utc_now_naive().isoformat()
                compensated += 1
            except Exception:
                pass

        # CREATING 超时：超过 10 分钟视为卡死
        stale_threshold = (shanghai_now() - timedelta(minutes=10)).isoformat()
        result2 = await db.execute(
            select(RoomBooking).where(
                RoomBooking.status == "CREATING",
                RoomBooking.created_at < stale_threshold,
            ).limit(20)
        )
        for booking in result2.scalars().all():
            if booking.schedule_id and booking.calendar_id:
                try:
                    await client.delete_schedule(booking.union_id, booking.calendar_id, booking.schedule_id)
                except DingTalkMeetingError:
                    pass
            booking.status = "FAILED"
            booking.error_message = "CREATING 状态超时，已自动回滚"
            booking.updated_at = utc_now_naive().isoformat()
            compensated += 1

        await db.commit()
    return compensated
