"""钉钉 Channel 运行配置。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from yuxi.utils.channel_auth import resolve_channel_gateway_token

DEFAULT_AI_CARD_TEMPLATE_ID = "02fcf2f4-5e02-4a85-b672-46d1f715543e.schema"
_BOT_ACCOUNT_FIELDS = {"client_id", "client_secret", "robot_code", "agent_slug", "card_template_id"}


@dataclass(frozen=True, slots=True)
class DingTalkBotAccountConfig:
    """单个钉钉机器人账号及其服务端 Agent 绑定。"""

    client_id: str
    client_secret: str
    robot_code: str
    agent_slug: str
    card_template_id: str


@dataclass(frozen=True, slots=True)
class DingTalkChannelConfig:
    """独立钉钉 Channel 的不可变启动配置。"""

    enabled: bool
    accounts: tuple[DingTalkBotAccountConfig, ...]
    yuxi_api_base_url: str
    gateway_token: str


def load_dingtalk_bot_accounts(
    *,
    legacy_agent_slug: str | None = None,
    legacy_robot_code: str | None = None,
) -> tuple[DingTalkBotAccountConfig, ...]:
    """读取多机器人账号，未配置 JSON 时兼容旧单机器人变量。"""

    raw_accounts = os.getenv("DINGTALK_BOTS_JSON", "").strip()
    if raw_accounts:
        return _parse_bot_accounts(raw_accounts)

    return (_load_legacy_bot_account(legacy_agent_slug, legacy_robot_code),)


def load_dingtalk_channel_config() -> DingTalkChannelConfig:
    """从环境变量读取并校验钉钉 Channel 配置。"""

    enabled_value = os.getenv("DINGTALK_CHANNEL_ENABLED")
    if enabled_value is None:
        enabled_value = os.getenv("DINGTALK_BOT_ENABLED", "false")
    enabled = enabled_value.strip().lower() in {"true", "1", "yes", "on"}
    yuxi_api_base_url = os.getenv("YUXI_API_BASE_URL", "http://api:5050/api").strip().rstrip("/")

    if enabled:
        if not yuxi_api_base_url:
            raise ValueError("钉钉 Channel 缺少配置: YUXI_API_BASE_URL")
        accounts = load_dingtalk_bot_accounts()
        gateway_token = resolve_channel_gateway_token()
    else:
        accounts = ()
        gateway_token = ""

    return DingTalkChannelConfig(
        enabled=enabled,
        accounts=accounts,
        yuxi_api_base_url=yuxi_api_base_url,
        gateway_token=gateway_token,
    )


def _parse_bot_accounts(raw_accounts: str) -> tuple[DingTalkBotAccountConfig, ...]:
    """严格解析多机器人 JSON，避免账号配置被部分接受。"""

    try:
        payload = json.loads(raw_accounts)
    except json.JSONDecodeError as exc:
        raise ValueError("DINGTALK_BOTS_JSON 不是合法 JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("DINGTALK_BOTS_JSON 必须是非空数组")

    default_template = os.getenv("DINGTALK_CARD_TEMPLATE_ID", DEFAULT_AI_CARD_TEMPLATE_ID).strip()
    accounts: list[DingTalkBotAccountConfig] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"DINGTALK_BOTS_JSON 第 {index + 1} 项必须是对象")
        unknown_fields = set(item) - _BOT_ACCOUNT_FIELDS
        if unknown_fields:
            fields = ", ".join(sorted(unknown_fields))
            raise ValueError(f"DINGTALK_BOTS_JSON 第 {index + 1} 项包含未知字段: {fields}")

        client_id = _required_account_string(item, "client_id", index)
        account = DingTalkBotAccountConfig(
            client_id=client_id,
            client_secret=_required_account_string(item, "client_secret", index),
            robot_code=_optional_account_string(item, "robot_code", index) or client_id,
            agent_slug=_required_account_string(item, "agent_slug", index),
            card_template_id=_optional_account_string(item, "card_template_id", index) or default_template,
        )
        if not account.card_template_id:
            raise ValueError(f"DINGTALK_BOTS_JSON 第 {index + 1} 项缺少 card_template_id")
        accounts.append(account)

    _validate_unique_account_identifiers(accounts)
    return tuple(accounts)


def _load_legacy_bot_account(
    legacy_agent_slug: str | None,
    legacy_robot_code: str | None,
) -> DingTalkBotAccountConfig:
    """按旧变量优先级构造单机器人账号。"""

    bot_client_id = os.getenv("DINGTALK_BOT_CLIENT_ID", "").strip()
    bot_client_secret = os.getenv("DINGTALK_BOT_CLIENT_SECRET", "").strip()
    if bool(bot_client_id) != bool(bot_client_secret):
        raise ValueError("DINGTALK_BOT_CLIENT_ID 与 DINGTALK_BOT_CLIENT_SECRET 必须成对配置")

    if bot_client_id:
        client_id = bot_client_id
        client_secret = bot_client_secret
    else:
        client_id = os.getenv("DINGTALK_CLIENT_ID", "").strip()
        client_secret = os.getenv("DINGTALK_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise ValueError("钉钉 Channel 缺少机器人 Client ID/Secret")

    robot_code = str(legacy_robot_code or "").strip() or os.getenv("DINGTALK_ROBOT_CODE", "").strip() or client_id
    agent_slug = (
        str(legacy_agent_slug or "").strip() or os.getenv("DINGTALK_BOT_AGENT_SLUG", "").strip() or "default-chatbot"
    )
    card_template_id = os.getenv("DINGTALK_CARD_TEMPLATE_ID", DEFAULT_AI_CARD_TEMPLATE_ID).strip()
    if not card_template_id:
        raise ValueError("钉钉 Channel 缺少配置: DINGTALK_CARD_TEMPLATE_ID")

    return DingTalkBotAccountConfig(
        client_id=client_id,
        client_secret=client_secret,
        robot_code=robot_code,
        agent_slug=agent_slug,
        card_template_id=card_template_id,
    )


def _required_account_string(item: dict[str, Any], field: str, index: int) -> str:
    """读取账号必填字符串，拒绝隐式类型转换。"""

    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"DINGTALK_BOTS_JSON 第 {index + 1} 项缺少 {field}")
    return value.strip()


def _optional_account_string(item: dict[str, Any], field: str, index: int) -> str:
    """读取账号可选字符串，字段存在时仍要求类型正确。"""

    value = item.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"DINGTALK_BOTS_JSON 第 {index + 1} 项的 {field} 必须是字符串")
    return value.strip()


def _validate_unique_account_identifiers(accounts: list[DingTalkBotAccountConfig]) -> None:
    """保证 Client ID 和 RobotCode 都能唯一定位账号。"""

    client_ids = [account.client_id for account in accounts]
    robot_codes = [account.robot_code for account in accounts]
    if len(set(client_ids)) != len(client_ids):
        raise ValueError("DINGTALK_BOTS_JSON 包含重复 Client ID")
    if len(set(robot_codes)) != len(robot_codes):
        raise ValueError("DINGTALK_BOTS_JSON 包含重复 RobotCode")
