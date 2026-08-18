"""钉钉消息到 Yuxi Run 与 AI Card 的完整编排。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from yuxi.channels.dingtalk.card_client import DingTalkCardClient, DingTalkCardHandle
from yuxi.channels.dingtalk.message_adapter import adapt_chatbot_message
from yuxi.channels.dingtalk.run_event_adapter import RunEventAdapter
from yuxi.channels.yuxi_channel_client import YuxiChannelClient
from yuxi.utils import logger

CHANNEL_NAME = "dingtalk_bot"
CARD_STREAM_INTERVAL_SECONDS = 0.8
RUN_RESULT_POLL_INTERVAL_SECONDS = 1.0
RUN_RESULT_TIMEOUT_SECONDS = 600.0
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}

FallbackReply = Callable[[str], Awaitable[None]]


class DingTalkChannel:
    """接收钉钉消息，并把一个 Yuxi Run 映射为一张流式 AI Card。"""

    def __init__(
        self,
        *,
        account_id: str,
        yuxi_client: YuxiChannelClient,
        card_client: DingTalkCardClient,
        result_poll_interval: float = RUN_RESULT_POLL_INTERVAL_SECONDS,
        result_timeout: float = RUN_RESULT_TIMEOUT_SECONDS,
    ) -> None:
        self._account_id = account_id
        self._yuxi_client = yuxi_client
        self._card_client = card_client
        self._result_poll_interval = result_poll_interval
        self._result_timeout = result_timeout

    async def handle(self, incoming: Any, fallback_reply: FallbackReply) -> None:
        """处理单条钉钉消息；卡片链路失败时仅发送一次最终 Markdown。"""

        try:
            inbound = adapt_chatbot_message(incoming, account_id=self._account_id)
        except ValueError as exc:
            logger.warning(f"dingtalk channel ignored invalid message: {exc}")
            return

        try:
            submission = await self._yuxi_client.deliver(inbound.delivery)
        except httpx.HTTPStatusError as exc:
            await fallback_reply(_format_delivery_error(exc.response))
            return
        except Exception as exc:  # noqa: BLE001 - Channel 边界必须给用户可见结果
            logger.error(f"dingtalk channel delivery failed: {exc}")
            await fallback_reply("消息提交失败，请稍后重试。")
            return

        run_id = str(submission.get("run_id") or "").strip()
        if not run_id:
            await fallback_reply(_format_submission(submission))
            return

        card: DingTalkCardHandle | None = None
        session: _CardStreamSession | None = None
        try:
            card = await self._card_client.create_and_deliver(inbound.target)
            session = _CardStreamSession(self._card_client, card)
            result = await self._consume_run(run_id=run_id, session=session)
            await session.finish(_format_result(result))
            return
        except Exception as exc:  # noqa: BLE001 - 保持 Run 单次提交，统一降级到最终 Markdown
            logger.error(f"dingtalk channel card stream failed for run {run_id}: {exc}")
            if card is not None:
                try:
                    await self._card_client.fail(card, "流式卡片更新失败，请查看后续回复。")
                except Exception as card_exc:  # noqa: BLE001 - 原始异常更重要
                    logger.warning(f"dingtalk channel card fail status update failed: {card_exc}")

        try:
            result = await self._wait_for_result(run_id)
            await fallback_reply(_format_result(result))
        except Exception as exc:  # noqa: BLE001 - 降级链路的最后边界
            logger.error(f"dingtalk channel fallback result failed for run {run_id}: {exc}")
            await fallback_reply("回复推送失败，请稍后重试。")

    async def _consume_run(self, *, run_id: str, session: _CardStreamSession) -> dict[str, Any]:
        """消费紧凑 SSE，终态后用 Result API 对账。"""

        adapter = RunEventAdapter()
        reconnects = 0
        while reconnects < 3:
            terminal_status: str | None = None
            try:
                async for event in self._yuxi_client.stream_run_events(
                    run_id=run_id,
                    channel=CHANNEL_NAME,
                    account_id=self._account_id,
                    after_seq=adapter.last_event_id,
                ):
                    update = adapter.apply(event)
                    if update.text is not None:
                        await session.offer(update.text)
                    if update.terminal_status:
                        terminal_status = update.terminal_status
                        break
            except (httpx.HTTPError, OSError) as exc:
                reconnects += 1
                logger.warning(
                    f"dingtalk channel SSE disconnected for run {run_id}, "
                    f"after={adapter.last_event_id}, retry={reconnects}: {exc}"
                )
                continue

            if terminal_status in TERMINAL_RUN_STATUSES:
                return await self._yuxi_client.get_run_result(
                    run_id=run_id,
                    channel=CHANNEL_NAME,
                    account_id=self._account_id,
                )
            reconnects += 1

        return await self._wait_for_result(run_id)

    async def _wait_for_result(self, run_id: str) -> dict[str, Any]:
        """SSE 不可用时轮询权威结果，但绝不重复提交 Run。"""

        deadline = time.monotonic() + self._result_timeout
        while True:
            result = await self._yuxi_client.get_run_result(
                run_id=run_id,
                channel=CHANNEL_NAME,
                account_id=self._account_id,
            )
            if str(result.get("status") or "") in TERMINAL_RUN_STATUSES:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待 Run {run_id} 结果超时")
            await asyncio.sleep(self._result_poll_interval)


class _CardStreamSession:
    """按卡片维度节流，保留最新全量正文并在结束时强制刷新。"""

    def __init__(self, client: DingTalkCardClient, card: DingTalkCardHandle) -> None:
        self._client = client
        self._card = card
        self._last_text = ""
        self._last_flush_at = 0.0

    async def offer(self, text: str) -> None:
        """接收最新全量正文，达到间隔时才调用 Card API。"""

        if not text or text == self._last_text:
            return
        self._last_text = text
        now = time.monotonic()
        if self._last_flush_at and now - self._last_flush_at < CARD_STREAM_INTERVAL_SECONDS:
            return
        await self._client.stream(self._card, text)
        self._last_flush_at = now

    async def finish(self, final_text: str) -> None:
        """用权威最终正文完成 Card，不受节流间隔影响。"""

        await self._client.finish(self._card, final_text)


def _format_result(result: dict[str, Any]) -> str:
    """把 Run Result 转为钉钉可见 Markdown。"""

    status = str(result.get("status") or "").strip()
    if status == "completed":
        return str(result.get("output") or "").strip() or "已处理完成，但没有返回内容。"
    if status == "interrupted":
        return "运行已中断，等待人工确认；请在 Web 端继续处理。"
    if status == "cancelled":
        return "运行已取消。"
    if status == "failed":
        error = result.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        return f"处理失败：{message or '请稍后重试'}"
    return f"当前状态：{status or '未知'}。"


def _format_submission(submission: dict[str, Any]) -> str:
    """格式化不产生 Run 的控制命令响应。"""

    if submission.get("kind") == "command":
        state = submission.get("state")
        if state is not None:
            return f"```json\n{json.dumps(state, ensure_ascii=False, indent=2)}\n```"
    return _format_result(submission)


def _format_delivery_error(response: httpx.Response) -> str:
    """提取 Delivery API 的安全错误文案。"""

    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("code")
    if response.status_code == 403:
        return str(detail or "当前钉钉账号未绑定 Yuxi 用户。")
    if response.status_code == 409:
        return str(detail or "当前会话正在等待处理，请稍后重试。")
    logger.error(f"dingtalk channel delivery HTTP {response.status_code}: {detail or '-'}")
    return "消息提交失败，请稍后重试。"
