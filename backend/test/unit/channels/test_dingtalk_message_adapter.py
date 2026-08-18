from types import SimpleNamespace

import pytest
from yuxi.channels.dingtalk.message_adapter import adapt_chatbot_message


def _incoming(**overrides):
    values = {
        "text": SimpleNamespace(content="  你好  "),
        "message_id": "msg-1",
        "sender_corp_id": "corp-1",
        "sender_staff_id": "staff-1",
        "sender_id": "union-1",
        "conversation_type": "2",
        "conversation_id": "group-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_adapt_group_message_builds_delivery_and_card_target():
    inbound = adapt_chatbot_message(_incoming(), account_id="robot-1")

    assert inbound.target.kind == "group"
    assert inbound.target.target_id == "group-1"
    assert inbound.delivery == {
        "channel": "dingtalk_bot",
        "account_id": "robot-1",
        "tenant_id": "corp-1",
        "chat_id": "group-1",
        "chat_type": "group",
        "sender_id": "staff-1",
        "sender_union_id": "union-1",
        "message_id": "msg-1",
        "message": {"type": "text", "text": "你好"},
    }


def test_adapt_direct_message_uses_staff_id_as_target():
    inbound = adapt_chatbot_message(
        _incoming(conversation_type="1", conversation_id="unused"),
        account_id="robot-1",
    )

    assert inbound.target.kind == "direct"
    assert inbound.target.target_id == "staff-1"
    assert inbound.delivery["chat_type"] == "direct"
    assert inbound.delivery["chat_id"] == "staff-1"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"text": SimpleNamespace(content=" ")}, "正文为空"),
        ({"message_id": ""}, "message_id"),
        ({"sender_corp_id": ""}, "corp_id"),
        ({"sender_staff_id": "", "sender_id": ""}, "发送者身份"),
        ({"conversation_type": "3"}, "会话类型"),
    ],
)
def test_adapt_rejects_incomplete_message(overrides, message):
    with pytest.raises(ValueError, match=message):
        adapt_chatbot_message(_incoming(**overrides), account_id="robot-1")
