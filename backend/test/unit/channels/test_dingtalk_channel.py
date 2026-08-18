from types import SimpleNamespace

import pytest
from yuxi.channels.dingtalk.card_client import DingTalkCardHandle
from yuxi.channels.dingtalk.channel import DingTalkChannel
from yuxi.channels.yuxi_channel_client import RunSseEvent


def _incoming():
    return SimpleNamespace(
        text=SimpleNamespace(content="你好"),
        message_id="msg-1",
        sender_corp_id="corp-1",
        sender_staff_id="staff-1",
        sender_id="union-1",
        conversation_type="2",
        conversation_id="group-1",
    )


class _YuxiClient:
    def __init__(self, *, terminal_status="completed", result=None):
        self.deliveries = []
        self.result_calls = 0
        self.terminal_status = terminal_status
        self.result = result or {"status": "completed", "output": "最终答案"}

    async def deliver(self, payload):
        self.deliveries.append(payload)
        return {"run_id": "run-1", "status": "dispatched"}

    async def stream_run_events(self, **_kwargs):
        yield RunSseEvent(
            event="messages",
            event_id="1-0",
            data={
                "payload": {
                    "items": [{"stream_event": {"type": "message_delta", "message_id": "answer", "content": "流式"}}]
                }
            },
        )
        yield RunSseEvent(
            event="messages",
            event_id="2-0",
            data={
                "payload": {
                    "items": [{"stream_event": {"type": "message_delta", "message_id": "answer", "content": "正文"}}]
                }
            },
        )
        yield RunSseEvent(
            event="end",
            event_id="3-0",
            data={"payload": {"status": self.terminal_status}},
        )

    async def get_run_result(self, **_kwargs):
        self.result_calls += 1
        return self.result


class _CardClient:
    def __init__(self, *, fail_create=False):
        self.fail_create = fail_create
        self.streamed = []
        self.finished = []
        self.failed = []

    async def create_and_deliver(self, target):
        if self.fail_create:
            raise RuntimeError("card unavailable")
        assert target.target_id == "group-1"
        return DingTalkCardHandle("card-1")

    async def stream(self, _card, text, **_kwargs):
        self.streamed.append(text)

    async def finish(self, _card, text):
        self.finished.append(text)

    async def fail(self, _card, text):
        self.failed.append(text)


@pytest.mark.asyncio
async def test_channel_streams_card_and_reconciles_final_result():
    yuxi = _YuxiClient()
    cards = _CardClient()
    replies = []
    channel = DingTalkChannel(account_id="robot-1", yuxi_client=yuxi, card_client=cards)

    async def reply(text):
        replies.append(text)

    await channel.handle(_incoming(), reply)

    assert len(yuxi.deliveries) == 1
    assert cards.streamed == ["流式"]
    assert cards.finished == ["最终答案"]
    assert yuxi.result_calls == 1
    assert replies == []


@pytest.mark.asyncio
async def test_channel_card_failure_waits_same_run_and_replies_once():
    yuxi = _YuxiClient()
    cards = _CardClient(fail_create=True)
    replies = []
    channel = DingTalkChannel(
        account_id="robot-1",
        yuxi_client=yuxi,
        card_client=cards,
        result_poll_interval=0,
        result_timeout=1,
    )

    async def reply(text):
        replies.append(text)

    await channel.handle(_incoming(), reply)

    assert len(yuxi.deliveries) == 1
    assert yuxi.result_calls == 1
    assert replies == ["最终答案"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "result", "expected"),
    [
        ("failed", {"status": "failed", "error": {"message": "模型错误"}}, "处理失败：模型错误"),
        ("cancelled", {"status": "cancelled"}, "运行已取消。"),
        ("interrupted", {"status": "interrupted"}, "运行已中断，等待人工确认；请在 Web 端继续处理。"),
    ],
)
async def test_channel_finalizes_non_completed_terminal_status(status, result, expected):
    yuxi = _YuxiClient(terminal_status=status, result=result)
    cards = _CardClient()
    channel = DingTalkChannel(account_id="robot-1", yuxi_client=yuxi, card_client=cards)

    async def reply(_text):
        raise AssertionError("卡片成功时不应发送降级回复")

    await channel.handle(_incoming(), reply)

    assert cards.finished == [expected]
