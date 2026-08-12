"""钉钉会议预订的本地状态模型。"""

from sqlalchemy import Boolean, Column, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


class RoomBooking(Base):
    """会议室预订状态机；schedule_id 保存钉钉日程 event id。"""

    __tablename__ = "room_bookings"

    id = Column(String(64), primary_key=True)
    uid = Column(String(128), nullable=False, index=True)  # 钉钉用户 uid 可达 75 字符
    union_id = Column(String(128), nullable=False, index=True)
    room_id = Column(String(128), nullable=False)
    room_name = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    start_time = Column(String(64), nullable=False, index=True)
    end_time = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="CREATING", index=True)
    calendar_id = Column(String(128), nullable=True)
    schedule_id = Column(String(128), nullable=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    payload = Column(JSON_VALUE, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
    updated_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
    cancelled_at = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_room_bookings_uid_status", "uid", "status"),
        Index("ix_room_bookings_union_start", "union_id", "start_time"),
    )


class BookingConfirmation(Base):
    """一次性短期确认令牌，不代表第二次用户确认。"""

    __tablename__ = "booking_confirmations"

    id = Column(String(64), primary_key=True)
    token = Column(String(128), nullable=False, unique=True, index=True)
    booking_payload = Column(JSON_VALUE, nullable=False)
    uid = Column(String(128), nullable=False, index=True)  # 钉钉用户 uid 可达 75 字符
    expires_at = Column(String(64), nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    used_at = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
