from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.dingtalk_meeting_service import (
    DingTalkMeetingClient,
    DingTalkMeetingError,
    MeetingRoomService,
)
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_meeting import RoomBooking
from yuxi.utils.datetime_utils import shanghai_now


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class FakeMeetingClient:
    def __init__(self, *, reserve_should_fail: bool = False):
        self.calls = []
        self.reserve_should_fail = reserve_should_fail

    async def list_meeting_rooms(self, union_id: str, *, max_results: int = 100):
        return [{"roomId": "room-1", "roomName": "A 会议室"}]

    async def query_room_availability(self, union_id, room_ids, start_time, end_time):
        self.calls.append(("availability", room_ids))
        return []

    async def create_schedule(self, union_id, title, start_time, end_time, description, client_token):
        self.calls.append(("schedule", client_token))
        return {"id": "event-1", "calendarId": "primary"}

    async def reserve_room(self, union_id, room_id, calendar_id, event_id):
        self.calls.append(("reserve", room_id, event_id))
        if self.reserve_should_fail:
            raise DingTalkMeetingError("DINGTALK_API_ERROR", "reserve failed")
        return {}

    async def cancel_room(self, union_id, room_id, calendar_id, event_id):
        self.calls.append(("cancel_room", room_id, event_id))

    async def delete_schedule(self, union_id, calendar_id, event_id):
        self.calls.append(("delete_schedule", event_id))


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def future_range() -> tuple[str, str]:
    start = (shanghai_now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(hours=1)).isoformat()


async def test_meeting_client_follows_room_pages(monkeypatch):
    client = DingTalkMeetingClient()
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append(kwargs.get("params", {}))
        if len(calls) == 1:
            return {"result": {"roomList": [{"roomId": "1"}], "hasMore": True, "nextToken": 9}}
        return {"result": {"roomList": [{"roomId": "2"}], "hasMore": False}}

    monkeypatch.setattr(client, "_request", fake_request)
    rooms = await client.list_meeting_rooms("union-1")

    assert [room["roomId"] for room in rooms] == ["1", "2"]
    assert calls[1]["nextToken"] == 9


async def test_booking_requires_one_token_and_compensates_on_cancel(session):
    client = FakeMeetingClient()
    service = MeetingRoomService(session, client)
    start_time, end_time = future_range()
    preview = await service.preview_booking(
        uid="user-1",
        union_id="union-1",
        room_id="room-1",
        room_name="A 会议室",
        title="项目评审",
        start_time=start_time,
        end_time=end_time,
    )
    booked = await service.confirm_booking("user-1", preview["confirm_token"])

    assert booked["status"] == "BOOKED"
    assert len([call for call in client.calls if call[0] == "schedule"]) == 1
    with pytest.raises(DingTalkMeetingError) as exc_info:
        await service.confirm_booking("user-1", preview["confirm_token"])
    assert exc_info.value.code == "CONFIRM_TOKEN_USED"

    cancelled = await service.cancel_booking("user-1", booked["booking_id"])
    assert cancelled["status"] == "CANCELLED"
    record = await session.scalar(select(RoomBooking).where(RoomBooking.id == booked["booking_id"]))
    assert record is not None and record.status == "CANCELLED"


async def test_booking_deletes_schedule_when_room_reservation_fails(session):
    client = FakeMeetingClient(reserve_should_fail=True)
    service = MeetingRoomService(session, client)
    start_time, end_time = future_range()
    preview = await service.preview_booking(
        uid="user-1",
        union_id="union-1",
        room_id="room-1",
        room_name="A 会议室",
        title="项目评审",
        start_time=start_time,
        end_time=end_time,
    )

    with pytest.raises(DingTalkMeetingError):
        await service.confirm_booking("user-1", preview["confirm_token"])

    assert ("delete_schedule", "event-1") in client.calls
    record = await session.scalar(select(RoomBooking).where(RoomBooking.status == "FAILED"))
    assert record is not None
