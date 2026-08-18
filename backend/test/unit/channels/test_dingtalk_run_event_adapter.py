from yuxi.channels.dingtalk.run_event_adapter import RunEventAdapter
from yuxi.channels.yuxi_channel_client import RunSseEvent


def _event(event_id, *, chunks=None, event="messages", status=None):
    payload = {}
    if chunks is not None:
        payload["items"] = chunks
    if status is not None:
        payload["status"] = status
    return RunSseEvent(event=event, event_id=event_id, data={"payload": payload})


def _delta(message_id, content, event_type="message_delta"):
    return {"stream_event": {"type": event_type, "message_id": message_id, "content": content}}


def test_adapter_accumulates_only_message_delta_content():
    adapter = RunEventAdapter()

    first = adapter.apply(_event("1-0", chunks=[_delta("assistant-1", "你"), _delta("tool-1", "隐藏", "tool")]))
    second = adapter.apply(_event("2-0", chunks=[_delta("assistant-1", "好")]))

    assert first.text == "你"
    assert second.text == "你好"
    assert adapter.text == "你好"
    assert adapter.last_event_id == "2-0"


def test_adapter_ignores_replayed_event_and_reports_terminal_status():
    adapter = RunEventAdapter()
    adapter.apply(_event("2-0", chunks=[_delta("assistant-1", "完成")]))

    replay = adapter.apply(_event("1-0", chunks=[_delta("assistant-1", "重复")]))
    terminal = adapter.apply(_event("3-0", event="end", status="completed"))

    assert replay.text is None
    assert adapter.text == "完成"
    assert terminal.terminal_status == "completed"
