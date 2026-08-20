from __future__ import annotations

import asyncio

import pytest

from yuxi.services.channel_session_service import ChannelSessionKey, ChannelSessionRegistry


def _key(**overrides: str) -> ChannelSessionKey:
    values = {
        "uid": "user-1",
        "channel": "dingtalk_bot",
        "account_id": "robot-1",
        "agent_slug": "agent-1",
        "tenant_id": "corp-1",
        "chat_type": "direct",
        "chat_id": "staff-1",
    }
    values.update(overrides)
    return ChannelSessionKey(**values)


@pytest.mark.asyncio
async def test_channel_session_reuses_thread_until_idle_threshold():
    now = 100.0
    registry = ChannelSessionRegistry(clock=lambda: now)

    first = await registry.resolve_thread_id(_key(), idle_seconds=60)
    now = 159.9
    second = await registry.resolve_thread_id(_key(), idle_seconds=60)
    now = 219.8
    third = await registry.resolve_thread_id(_key(), idle_seconds=60)

    assert second == first
    assert third == first


@pytest.mark.asyncio
async def test_channel_session_creates_new_thread_at_idle_threshold():
    now = 100.0
    registry = ChannelSessionRegistry(clock=lambda: now)

    first = await registry.resolve_thread_id(_key(), idle_seconds=60)
    now = 160.0
    second = await registry.resolve_thread_id(_key(), idle_seconds=60)

    assert second != first
    assert first.startswith("channel_")
    assert len(first) == 64


@pytest.mark.asyncio
async def test_channel_session_isolates_identity_account_agent_and_chat():
    registry = ChannelSessionRegistry(clock=lambda: 100.0)
    keys = [
        _key(),
        _key(uid="user-2"),
        _key(account_id="robot-2"),
        _key(agent_slug="agent-2"),
        _key(tenant_id="corp-2"),
        _key(chat_type="group"),
        _key(chat_id="chat-2"),
    ]

    thread_ids = {await registry.resolve_thread_id(key, idle_seconds=60) for key in keys}

    assert len(thread_ids) == len(keys)


@pytest.mark.asyncio
async def test_new_channel_session_registry_resets_current_thread():
    first_registry = ChannelSessionRegistry(clock=lambda: 100.0)
    second_registry = ChannelSessionRegistry(clock=lambda: 100.0)

    first = await first_registry.resolve_thread_id(_key(), idle_seconds=60)
    after_restart = await second_registry.resolve_thread_id(_key(), idle_seconds=60)

    assert after_restart != first


@pytest.mark.asyncio
async def test_concurrent_first_messages_share_one_thread():
    registry = ChannelSessionRegistry(clock=lambda: 100.0)

    thread_ids = await asyncio.gather(
        registry.resolve_thread_id(_key(), idle_seconds=60),
        registry.resolve_thread_id(_key(), idle_seconds=60),
    )

    assert thread_ids[0] == thread_ids[1]
