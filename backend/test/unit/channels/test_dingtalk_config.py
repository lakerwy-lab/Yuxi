from __future__ import annotations

import json

import pytest

from yuxi.channels.dingtalk.config import DEFAULT_AI_CARD_TEMPLATE_ID, load_dingtalk_bot_accounts


_DINGTALK_ENV_NAMES = (
    "DINGTALK_BOTS_JSON",
    "DINGTALK_BOT_CLIENT_ID",
    "DINGTALK_BOT_CLIENT_SECRET",
    "DINGTALK_BOT_AGENT_SLUG",
    "DINGTALK_ROBOT_CODE",
    "DINGTALK_CLIENT_ID",
    "DINGTALK_CLIENT_SECRET",
    "DINGTALK_CARD_TEMPLATE_ID",
)


@pytest.fixture(autouse=True)
def clear_dingtalk_environment(monkeypatch: pytest.MonkeyPatch):
    """隔离每个用例使用的钉钉账号环境变量。"""

    for name in _DINGTALK_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_load_dingtalk_bot_accounts_parses_multiple_accounts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DINGTALK_CARD_TEMPLATE_ID", "global-template")
    monkeypatch.setenv(
        "DINGTALK_BOTS_JSON",
        json.dumps(
            [
                {
                    "client_id": "general-client",
                    "client_secret": "general-secret",
                    "agent_slug": "default-chatbot",
                },
                {
                    "client_id": "hr-client",
                    "client_secret": "hr-secret",
                    "robot_code": "hr-robot",
                    "agent_slug": "agent-hr",
                    "card_template_id": "hr-template",
                },
            ]
        ),
    )

    accounts = load_dingtalk_bot_accounts()

    assert [(account.robot_code, account.agent_slug) for account in accounts] == [
        ("general-client", "default-chatbot"),
        ("hr-robot", "agent-hr"),
    ]
    assert accounts[0].card_template_id == "global-template"
    assert accounts[1].card_template_id == "hr-template"


@pytest.mark.parametrize(
    "raw_value",
    [
        "not-json",
        "{}",
        "[]",
        json.dumps([{"client_id": "bot", "client_secret": "secret", "agent_slug": "agent", "extra": 1}]),
        json.dumps([{"client_id": "bot", "client_secret": "", "agent_slug": "agent"}]),
    ],
)
def test_load_dingtalk_bot_accounts_rejects_invalid_json_config(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
):
    monkeypatch.setenv("DINGTALK_BOTS_JSON", raw_value)

    with pytest.raises(ValueError, match="DINGTALK_BOTS_JSON"):
        load_dingtalk_bot_accounts()


@pytest.mark.parametrize("duplicate_field", ["client_id", "robot_code"])
def test_load_dingtalk_bot_accounts_rejects_duplicate_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    duplicate_field: str,
):
    second = {
        "client_id": "client-2",
        "client_secret": "secret-2",
        "robot_code": "robot-2",
        "agent_slug": "same-agent",
    }
    second[duplicate_field] = "client-1" if duplicate_field == "client_id" else "robot-1"
    monkeypatch.setenv(
        "DINGTALK_BOTS_JSON",
        json.dumps(
            [
                {
                    "client_id": "client-1",
                    "client_secret": "secret-1",
                    "robot_code": "robot-1",
                    "agent_slug": "same-agent",
                },
                second,
            ]
        ),
    )

    with pytest.raises(ValueError, match="重复"):
        load_dingtalk_bot_accounts()


def test_load_dingtalk_bot_accounts_keeps_legacy_independent_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DINGTALK_BOT_CLIENT_ID", "bot-client")
    monkeypatch.setenv("DINGTALK_BOT_CLIENT_SECRET", "bot-secret")
    monkeypatch.setenv("DINGTALK_BOT_AGENT_SLUG", "env-agent")

    accounts = load_dingtalk_bot_accounts(
        legacy_agent_slug="database-agent",
        legacy_robot_code="database-robot",
    )

    assert len(accounts) == 1
    assert accounts[0].client_id == "bot-client"
    assert accounts[0].client_secret == "bot-secret"
    assert accounts[0].robot_code == "database-robot"
    assert accounts[0].agent_slug == "database-agent"
    assert accounts[0].card_template_id == DEFAULT_AI_CARD_TEMPLATE_ID


def test_load_dingtalk_bot_accounts_falls_back_to_login_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DINGTALK_CLIENT_ID", "login-client")
    monkeypatch.setenv("DINGTALK_CLIENT_SECRET", "login-secret")

    account = load_dingtalk_bot_accounts()[0]

    assert account.client_id == "login-client"
    assert account.client_secret == "login-secret"
    assert account.robot_code == "login-client"
    assert account.agent_slug == "default-chatbot"


def test_load_dingtalk_bot_accounts_rejects_half_legacy_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DINGTALK_BOT_CLIENT_ID", "bot-client")

    with pytest.raises(ValueError, match="成对"):
        load_dingtalk_bot_accounts()
