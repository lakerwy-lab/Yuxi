"""Yuxi Run SSE 到钉钉可见回复状态的转换。"""

from __future__ import annotations

from dataclasses import dataclass

from yuxi.channels.yuxi_channel_client import RunSseEvent


@dataclass(frozen=True, slots=True)
class RunStreamUpdate:
    """一次 Run 事件产生的钉钉正文或终态更新。"""

    text: str | None = None
    terminal_status: str | None = None


class RunEventAdapter:
    """累计正文 delta，并忽略 reasoning 和工具协议细节。"""

    def __init__(self) -> None:
        self.last_event_id = "0-0"
        self._buffers: dict[str, str] = {}
        self._active_message_id: str | None = None

    @property
    def text(self) -> str:
        """返回当前主回复正文。"""

        if self._active_message_id is None:
            return ""
        return self._buffers.get(self._active_message_id, "")

    def apply(self, event: RunSseEvent) -> RunStreamUpdate:
        """应用一条 SSE 事件并返回必要的可见更新。"""

        if event.event_id and _compare_run_seq(event.event_id, self.last_event_id) <= 0:
            return RunStreamUpdate()
        if event.event_id:
            self.last_event_id = event.event_id

        payload = event.data.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        changed = False
        chunks = payload.get("items") if isinstance(payload.get("items"), list) else []
        if isinstance(payload.get("chunk"), dict):
            chunks = [*chunks, payload["chunk"]]
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            stream_event = chunk.get("stream_event")
            if not isinstance(stream_event, dict) or stream_event.get("type") != "message_delta":
                continue
            content = stream_event.get("content")
            message_id = str(stream_event.get("message_id") or "").strip()
            if not message_id or not isinstance(content, str) or not content:
                continue
            self._buffers[message_id] = self._buffers.get(message_id, "") + content
            self._active_message_id = message_id
            changed = True

        terminal_status = str(payload.get("status") or "").strip() if event.event == "end" else None
        return RunStreamUpdate(text=self.text if changed else None, terminal_status=terminal_status or None)


def _compare_run_seq(left: str, right: str) -> int:
    """比较 Redis Stream 的毫秒-序号游标。"""

    def parts(value: str) -> tuple[int, int]:
        try:
            milliseconds, sequence = value.split("-", 1)
            return int(milliseconds), int(sequence)
        except (TypeError, ValueError):
            return 0, 0

    return (parts(left) > parts(right)) - (parts(left) < parts(right))
