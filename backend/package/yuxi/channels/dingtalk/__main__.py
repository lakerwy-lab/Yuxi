"""独立钉钉 Channel 进程入口。"""

from __future__ import annotations

import asyncio
import signal

from yuxi.channels.dingtalk.card_client import DingTalkCardClient
from yuxi.channels.dingtalk.channel import DingTalkChannel
from yuxi.channels.dingtalk.config import DingTalkBotAccountConfig, DingTalkChannelConfig, load_dingtalk_channel_config
from yuxi.channels.dingtalk.stream_receiver import DingTalkStreamReceiver
from yuxi.channels.yuxi_channel_client import YuxiChannelClient
from yuxi.utils import logger

DINGTALK_STREAM_RETRY_INITIAL_SECONDS = 1.0
DINGTALK_STREAM_RETRY_MAX_SECONDS = 30.0


async def main() -> None:
    """加载配置并运行全部钉钉机器人账号。"""

    config = load_dingtalk_channel_config()
    if not config.enabled:
        logger.info("dingtalk channel disabled")
        await asyncio.Event().wait()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass

    await run_dingtalk_channel(config, stop_event)


async def run_dingtalk_channel(config: DingTalkChannelConfig, stop_event: asyncio.Event) -> None:
    """用一个 Yuxi 客户端运行多个相互隔离的钉钉机器人账号。"""

    yuxi_client = YuxiChannelClient(
        base_url=config.yuxi_api_base_url,
        gateway_token=config.gateway_token,
    )
    card_clients: list[DingTalkCardClient] = []
    supervisor_tasks: list[asyncio.Task[None]] = []
    try:
        for account in config.accounts:
            card_client = DingTalkCardClient(
                client_id=account.client_id,
                client_secret=account.client_secret,
                robot_code=account.robot_code,
                card_template_id=account.card_template_id,
            )
            card_clients.append(card_client)
            channel = DingTalkChannel(
                account_id=account.robot_code,
                yuxi_client=yuxi_client,
                card_client=card_client,
            )
            supervisor_tasks.append(
                asyncio.create_task(
                    supervise_dingtalk_account(account, channel, stop_event),
                    name=f"dingtalk-stream-{account.robot_code}",
                )
            )

        logger.info(f"dingtalk channel started with {len(supervisor_tasks)} bot account(s)")
        await stop_event.wait()
    finally:
        for task in supervisor_tasks:
            task.cancel()
        if supervisor_tasks:
            await asyncio.gather(*supervisor_tasks, return_exceptions=True)
        if card_clients:
            await asyncio.gather(*(client.close() for client in card_clients), return_exceptions=True)
        await yuxi_client.close()


async def supervise_dingtalk_account(
    account: DingTalkBotAccountConfig,
    channel: DingTalkChannel,
    stop_event: asyncio.Event,
) -> None:
    """独立监督一个 Stream 连接，退出时只重建当前机器人账号。"""

    retry_seconds = DINGTALK_STREAM_RETRY_INITIAL_SECONDS
    while not stop_event.is_set():
        receiver = DingTalkStreamReceiver(
            client_id=account.client_id,
            client_secret=account.client_secret,
            channel=channel,
        )
        try:
            await receiver.run()
            if not stop_event.is_set():
                logger.error(f"dingtalk stream stopped unexpectedly: robot_code={account.robot_code}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单账号失败不能结束其他机器人
            logger.error(f"dingtalk stream failed: robot_code={account.robot_code}, error_type={type(exc).__name__}")
        finally:
            try:
                await receiver.stop()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不得阻断其他账号回收
                logger.error(
                    f"dingtalk stream cleanup failed: robot_code={account.robot_code}, error_type={type(exc).__name__}"
                )

        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=retry_seconds)
        except TimeoutError:
            retry_seconds = min(retry_seconds * 2, DINGTALK_STREAM_RETRY_MAX_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
