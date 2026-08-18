"""独立 Channel 访问 Yuxi Delivery 与 Run API 的客户端。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class RunSseEvent:
    """解析后的单条 Run SSE 事件。"""

    event: str
    data: dict[str, Any]
    event_id: str | None


class YuxiChannelClient:
    """以服务凭证调用 Yuxi Channel API。"""

    def __init__(
        self,
        *,
        base_url: str,
        gateway_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60.0)
        self._headers = {"Authorization": f"Bearer {gateway_token}"}

    async def deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        """提交一条外部 Channel 消息。"""

        response = await self._client.post(
            "/agent-invocation/channel/deliveries",
            json=payload,
            headers=self._headers,
        )
        response.raise_for_status()
        return response.json()

    async def stream_run_events(
        self,
        *,
        run_id: str,
        channel: str,
        account_id: str,
        after_seq: str = "0-0",
    ) -> AsyncIterator[RunSseEvent]:
        """消费精简 Run SSE，并在断线重连时携带最后事件 ID。"""

        headers = dict(self._headers)
        if after_seq and after_seq != "0-0":
            headers["Last-Event-ID"] = after_seq

        async with self._client.stream(
            "GET",
            f"/agent-invocation/channel/runs/{run_id}/events",
            params={"channel": channel, "account_id": account_id},
            headers=headers,
        ) as response:
            response.raise_for_status()

            event_type = "message"
            event_id: str | None = None
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if not line:
                    event = _build_sse_event(event_type, event_id, data_lines)
                    if event:
                        yield event
                    event_type = "message"
                    event_id = None
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip() or "message"
                elif line.startswith("id:"):
                    event_id = line[3:].strip() or None
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

            event = _build_sse_event(event_type, event_id, data_lines)
            if event:
                yield event

    async def get_run_result(self, *, run_id: str, channel: str, account_id: str) -> dict[str, Any]:
        """读取 Channel Run 的权威结果。"""

        response = await self._client.get(
            f"/agent-invocation/channel/runs/{run_id}/result",
            params={"channel": channel, "account_id": account_id},
            headers=self._headers,
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        """关闭内部创建的 HTTP 客户端。"""

        if self._owns_client:
            await self._client.aclose()


def _build_sse_event(event_type: str, event_id: str | None, data_lines: list[str]) -> RunSseEvent | None:
    """把一个完整 SSE block 转成结构化事件。"""

    if not data_lines:
        return None
    data = json.loads("\n".join(data_lines))
    if not isinstance(data, dict):
        return None
    return RunSseEvent(event=event_type, data=data, event_id=event_id)
