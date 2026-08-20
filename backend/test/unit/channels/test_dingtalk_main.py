from __future__ import annotations

import asyncio
import importlib
from collections import Counter

import pytest

from yuxi.channels.dingtalk.config import DingTalkBotAccountConfig, DingTalkChannelConfig

channel_main = importlib.import_module("yuxi.channels.dingtalk.__main__")


def _config() -> DingTalkChannelConfig:
    return DingTalkChannelConfig(
        enabled=True,
        accounts=(
            DingTalkBotAccountConfig(
                client_id="general-client",
                client_secret="general-secret",
                robot_code="general-robot",
                agent_slug="default-chatbot",
                card_template_id="template",
            ),
            DingTalkBotAccountConfig(
                client_id="hr-client",
                client_secret="hr-secret",
                robot_code="hr-robot",
                agent_slug="agent-hr",
                card_template_id="template",
            ),
        ),
        yuxi_api_base_url="http://api:5050/api",
        gateway_token="gateway-token",
    )


async def _wait_until(predicate, *, attempts: int = 100) -> None:
    """等待异步启动断言成立，避免测试依赖固定长延时。"""

    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待钉钉账号 runtime 启动超时")


@pytest.mark.asyncio
async def test_run_dingtalk_channel_starts_and_closes_all_accounts(monkeypatch: pytest.MonkeyPatch):
    created_channels = []
    created_cards = []
    created_receivers = []
    yuxi_clients = []

    class FakeYuxiClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = 0
            yuxi_clients.append(self)

        async def close(self):
            self.closed += 1

    class FakeCardClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = 0
            created_cards.append(self)

        async def close(self):
            self.closed += 1

    class FakeChannel:
        def __init__(self, **kwargs):
            self.account_id = kwargs["account_id"]
            self.kwargs = kwargs
            created_channels.append(self)

    class FakeReceiver:
        def __init__(self, *, channel, **_kwargs):
            self.account_id = channel.account_id
            self.stopped = 0
            created_receivers.append(self)

        async def run(self):
            await asyncio.Event().wait()

        async def stop(self):
            self.stopped += 1

    monkeypatch.setattr(channel_main, "YuxiChannelClient", FakeYuxiClient)
    monkeypatch.setattr(channel_main, "DingTalkCardClient", FakeCardClient)
    monkeypatch.setattr(channel_main, "DingTalkChannel", FakeChannel)
    monkeypatch.setattr(channel_main, "DingTalkStreamReceiver", FakeReceiver)

    stop_event = asyncio.Event()
    task = asyncio.create_task(channel_main.run_dingtalk_channel(_config(), stop_event))
    await _wait_until(lambda: len(created_receivers) == 2)
    stop_event.set()
    await task

    assert [channel.account_id for channel in created_channels] == ["general-robot", "hr-robot"]
    assert created_channels[0].kwargs["yuxi_client"] is created_channels[1].kwargs["yuxi_client"]
    assert created_cards[0] is not created_cards[1]
    assert all(card.closed == 1 for card in created_cards)
    assert all(receiver.stopped == 1 for receiver in created_receivers)
    assert len(yuxi_clients) == 1
    assert yuxi_clients[0].closed == 1


@pytest.mark.asyncio
async def test_run_dingtalk_channel_restarts_only_failed_account(monkeypatch: pytest.MonkeyPatch):
    receiver_attempts: Counter[str] = Counter()
    stopped_attempts: Counter[str] = Counter()

    class FakeYuxiClient:
        def __init__(self, **_kwargs):
            pass

        async def close(self):
            pass

    class FakeCardClient:
        def __init__(self, **_kwargs):
            pass

        async def close(self):
            pass

    class FakeChannel:
        def __init__(self, **kwargs):
            self.account_id = kwargs["account_id"]

    class FakeReceiver:
        def __init__(self, *, channel, **_kwargs):
            self.account_id = channel.account_id
            receiver_attempts[self.account_id] += 1
            self.attempt = receiver_attempts[self.account_id]

        async def run(self):
            if self.account_id == "general-robot" and self.attempt == 1:
                raise RuntimeError("stream disconnected")
            await asyncio.Event().wait()

        async def stop(self):
            stopped_attempts[self.account_id] += 1

    monkeypatch.setattr(channel_main, "YuxiChannelClient", FakeYuxiClient)
    monkeypatch.setattr(channel_main, "DingTalkCardClient", FakeCardClient)
    monkeypatch.setattr(channel_main, "DingTalkChannel", FakeChannel)
    monkeypatch.setattr(channel_main, "DingTalkStreamReceiver", FakeReceiver)
    monkeypatch.setattr(channel_main, "DINGTALK_STREAM_RETRY_INITIAL_SECONDS", 0.01)

    stop_event = asyncio.Event()
    task = asyncio.create_task(channel_main.run_dingtalk_channel(_config(), stop_event))
    await _wait_until(lambda: receiver_attempts["general-robot"] >= 2)

    assert receiver_attempts["hr-robot"] == 1
    stop_event.set()
    await task

    assert stopped_attempts["general-robot"] == receiver_attempts["general-robot"]
    assert stopped_attempts["hr-robot"] == 1
