"""钉钉 Stream 长连接接收器。"""

from __future__ import annotations

import asyncio
import logging

import dingtalk_stream
from dingtalk_stream import AckMessage, ChatbotMessage

from yuxi.channels.dingtalk.channel import DingTalkChannel
from yuxi.utils import logger


class DingTalkStreamReceiver:
    """维护 Stream 连接，并把回调快速 ACK 后交给后台 Channel 任务。"""

    def __init__(self, *, client_id: str, client_secret: str, channel: DingTalkChannel) -> None:
        credential = dingtalk_stream.Credential(client_id, client_secret)
        sdk_logger = logging.getLogger("yuxi.dingtalk_stream")
        sdk_logger.setLevel(logging.INFO)
        self._client = dingtalk_stream.DingTalkStreamClient(credential, logger=sdk_logger)
        self._handler = _ChatbotHandler(channel)
        self._client.register_callback_handler(ChatbotMessage.TOPIC, self._handler)

    async def run(self) -> None:
        """持续运行 SDK 自带重连的 Stream 客户端。"""

        logger.info("dingtalk channel stream connecting")
        await self._client.start()

    async def stop(self) -> None:
        """取消仍在处理的消息任务。"""

        await self._handler.stop()


class _ChatbotHandler(dingtalk_stream.ChatbotHandler):
    """Chatbot 回调处理器，避免在 ACK 路径等待 Agent 执行。"""

    def __init__(self, channel: DingTalkChannel) -> None:
        super().__init__()
        self._channel = channel
        self._tasks: set[asyncio.Task[None]] = set()

    async def process(self, callback: dingtalk_stream.CallbackMessage) -> tuple[str, str]:
        """解析消息并派发后台任务，立即返回成功 ACK。"""

        try:
            incoming = ChatbotMessage.from_dict(callback.data)
            task = asyncio.create_task(self._handle(incoming), name=f"dingtalk-message-{incoming.message_id}")
            self._tasks.add(task)
            task.add_done_callback(self._task_done)
        except Exception as exc:  # noqa: BLE001 - 不让畸形消息中断 Stream
            logger.error(f"dingtalk channel callback parse failed: {exc}")
        return AckMessage.STATUS_OK, "OK"

    async def stop(self) -> None:
        """取消并回收未完成的消息任务。"""

        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _handle(self, incoming: ChatbotMessage) -> None:
        """把消息交给 Channel，并提供 SDK Markdown 降级回复。"""

        await self._channel.handle(incoming, lambda text: self._reply_markdown(incoming, text))

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """回收消息任务，并记录未被 Channel 边界处理的异常。"""

        self._tasks.discard(task)
        if task.cancelled():
            return
        if error := task.exception():
            logger.error(f"dingtalk channel message task failed: {error}")

    async def _reply_markdown(self, incoming: ChatbotMessage, text: str) -> None:
        """在线程中调用 SDK 的同步 Markdown 回复方法。"""

        if text:
            await asyncio.to_thread(self.reply_markdown, "智能助手", text, incoming)
