"""独立钉钉 Channel 进程入口。"""

from __future__ import annotations

import asyncio
import signal

from yuxi.channels.dingtalk.card_client import DingTalkCardClient
from yuxi.channels.dingtalk.channel import DingTalkChannel
from yuxi.channels.dingtalk.config import load_dingtalk_channel_config
from yuxi.channels.dingtalk.stream_receiver import DingTalkStreamReceiver
from yuxi.channels.yuxi_channel_client import YuxiChannelClient
from yuxi.utils import logger


async def main() -> None:
    """加载配置并运行钉钉 Stream Channel，收到停止信号后回收资源。"""

    config = load_dingtalk_channel_config()
    if not config.enabled:
        logger.info("dingtalk channel disabled")
        await asyncio.Event().wait()

    yuxi_client = YuxiChannelClient(
        base_url=config.yuxi_api_base_url,
        gateway_token=config.gateway_token,
    )
    card_client = DingTalkCardClient(
        client_id=config.client_id,
        client_secret=config.client_secret,
        robot_code=config.robot_code,
        card_template_id=config.card_template_id,
    )
    channel = DingTalkChannel(
        account_id=config.robot_code,
        yuxi_client=yuxi_client,
        card_client=card_client,
    )
    receiver = DingTalkStreamReceiver(
        client_id=config.client_id,
        client_secret=config.client_secret,
        channel=channel,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass

    receiver_task = asyncio.create_task(receiver.run(), name="dingtalk-channel-stream")
    stop_task = asyncio.create_task(stop_event.wait(), name="dingtalk-channel-stop")
    try:
        done, _ = await asyncio.wait({receiver_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if receiver_task in done:
            await receiver_task
    finally:
        stop_task.cancel()
        receiver_task.cancel()
        await asyncio.gather(stop_task, receiver_task, return_exceptions=True)
        await receiver.stop()
        await card_client.close()
        await yuxi_client.close()


if __name__ == "__main__":
    asyncio.run(main())
