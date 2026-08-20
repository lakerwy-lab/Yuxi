"""外部 Channel 当前会话的进程内空闲重置服务。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from yuxi.utils.hash_utils import hash_id


@dataclass(frozen=True, slots=True)
class ChannelSessionKey:
    """由可信身份、机器人、Agent 和外部会话组成的隔离键。"""

    uid: str
    channel: str
    account_id: str
    agent_slug: str
    tenant_id: str
    chat_type: str
    chat_id: str


@dataclass(slots=True)
class ChannelSessionRecord:
    """当前进程内正在复用的 Channel 线程指针。"""

    thread_id: str
    last_interaction_at: float


class ChannelSessionRegistry:
    """按空闲阈值原子解析当前线程，不持久化任何对话事实。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        nonce_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._records: dict[ChannelSessionKey, ChannelSessionRecord] = {}
        self._lock = asyncio.Lock()

    async def resolve_thread_id(self, key: ChannelSessionKey, *, idle_seconds: int) -> str:
        """复用未超时线程，首次访问或达到阈值时生成新线程。"""

        if idle_seconds <= 0:
            raise ValueError("idle_seconds 必须大于 0")

        async with self._lock:
            now = self._clock()
            record = self._records.get(key)
            if record is None or now - record.last_interaction_at >= idle_seconds:
                record = ChannelSessionRecord(
                    thread_id=hash_id("channel_", f"{key}:{self._nonce_factory()}", length=64),
                    last_interaction_at=now,
                )
                self._records[key] = record
            else:
                record.last_interaction_at = now

            self._remove_expired_records(now, idle_seconds, current_key=key)
            return record.thread_id

    def _remove_expired_records(
        self,
        now: float,
        idle_seconds: int,
        *,
        current_key: ChannelSessionKey,
    ) -> None:
        """在入站消息路径机会性删除其他过期指针。"""

        expired_keys = [
            key
            for key, record in self._records.items()
            if key != current_key and now - record.last_interaction_at >= idle_seconds
        ]
        for key in expired_keys:
            del self._records[key]


channel_session_registry = ChannelSessionRegistry()
