"""钉钉 Channel 运行配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from yuxi.utils.channel_auth import resolve_channel_gateway_token

DEFAULT_AI_CARD_TEMPLATE_ID = "02fcf2f4-5e02-4a85-b672-46d1f715543e.schema"


@dataclass(frozen=True, slots=True)
class DingTalkChannelConfig:
    """独立钉钉 Channel 的不可变启动配置。"""

    enabled: bool
    client_id: str
    client_secret: str
    robot_code: str
    card_template_id: str
    yuxi_api_base_url: str
    gateway_token: str


def load_dingtalk_channel_config() -> DingTalkChannelConfig:
    """从环境变量读取并校验钉钉 Channel 配置。"""

    enabled_value = os.getenv("DINGTALK_CHANNEL_ENABLED")
    if enabled_value is None:
        enabled_value = os.getenv("DINGTALK_BOT_ENABLED", "false")
    enabled = enabled_value.strip().lower() in {"true", "1", "yes", "on"}

    client_id = os.getenv("DINGTALK_CLIENT_ID", "").strip()
    client_secret = os.getenv("DINGTALK_CLIENT_SECRET", "").strip()
    robot_code = os.getenv("DINGTALK_ROBOT_CODE", "").strip()
    card_template_id = os.getenv("DINGTALK_CARD_TEMPLATE_ID", DEFAULT_AI_CARD_TEMPLATE_ID).strip()
    yuxi_api_base_url = os.getenv("YUXI_API_BASE_URL", "http://api:5050/api").strip().rstrip("/")

    if enabled:
        missing = [
            name
            for name, value in {
                "DINGTALK_CLIENT_ID": client_id,
                "DINGTALK_CLIENT_SECRET": client_secret,
                "DINGTALK_ROBOT_CODE": robot_code,
                "DINGTALK_CARD_TEMPLATE_ID": card_template_id,
                "YUXI_API_BASE_URL": yuxi_api_base_url,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"钉钉 Channel 缺少配置: {', '.join(missing)}")
        gateway_token = resolve_channel_gateway_token()
    else:
        gateway_token = ""

    return DingTalkChannelConfig(
        enabled=enabled,
        client_id=client_id,
        client_secret=client_secret,
        robot_code=robot_code,
        card_template_id=card_template_id,
        yuxi_api_base_url=yuxi_api_base_url,
        gateway_token=gateway_token,
    )
