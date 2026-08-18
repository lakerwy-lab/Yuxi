"""Channel Gateway 服务认证凭证解析。"""

from __future__ import annotations

import hashlib
import os


def resolve_channel_gateway_token() -> str:
    """读取 Channel Gateway token；开发环境可由现有钉钉凭证派生迁移 token。"""

    configured = os.getenv("YUXI_CHANNEL_GATEWAY_TOKEN", "").strip()
    if configured:
        if len(configured) < 32:
            raise ValueError("YUXI_CHANNEL_GATEWAY_TOKEN 不能少于 32 个字符")
        return configured

    environment = os.getenv("YUXI_ENV", "development").strip().lower()
    if environment == "production":
        raise ValueError("生产环境必须配置 YUXI_CHANNEL_GATEWAY_TOKEN")

    client_id = os.getenv("DINGTALK_CLIENT_ID", "").strip()
    client_secret = os.getenv("DINGTALK_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ValueError("未配置 YUXI_CHANNEL_GATEWAY_TOKEN，且无法从钉钉凭证派生开发凭证")

    material = f"yuxi-channel-gateway:{client_id}:{client_secret}"
    return hashlib.sha256(material.encode()).hexdigest()
