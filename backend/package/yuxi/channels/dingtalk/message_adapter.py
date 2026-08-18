"""钉钉 ChatbotMessage 到 Yuxi Delivery 的转换。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DingTalkTarget:
    """AI Card 的钉钉投放目标。"""

    kind: str
    target_id: str


@dataclass(frozen=True, slots=True)
class DingTalkInbound:
    """标准化后的钉钉入站消息。"""

    delivery: dict[str, Any]
    target: DingTalkTarget


def adapt_chatbot_message(incoming: Any, *, account_id: str) -> DingTalkInbound:
    """校验并转换钉钉文本消息，缺少稳定标识时显式失败。"""

    text = (incoming.text.content if incoming.text else "").strip()
    message_id = str(incoming.message_id or "").strip()
    corp_id = str(incoming.sender_corp_id or "").strip()
    staff_id = str(incoming.sender_staff_id or "").strip()
    union_id = str(incoming.sender_id or "").strip()
    conversation_type = str(incoming.conversation_type or "").strip()
    conversation_id = str(incoming.conversation_id or "").strip()

    if not text:
        raise ValueError("钉钉消息正文为空")
    if not message_id:
        raise ValueError("钉钉消息缺少 message_id")
    if not corp_id:
        raise ValueError("钉钉消息缺少 corp_id")
    if not staff_id and not union_id:
        raise ValueError("钉钉消息缺少发送者身份")

    if conversation_type == "2":
        if not conversation_id:
            raise ValueError("钉钉群消息缺少 conversation_id")
        chat_type = "group"
        chat_id = conversation_id
        target = DingTalkTarget(kind="group", target_id=conversation_id)
    elif conversation_type == "1":
        direct_id = staff_id or union_id
        chat_type = "direct"
        chat_id = direct_id
        target = DingTalkTarget(kind="direct", target_id=direct_id)
    else:
        raise ValueError(f"不支持的钉钉会话类型: {conversation_type or '空'}")

    return DingTalkInbound(
        delivery={
            "channel": "dingtalk_bot",
            "account_id": account_id,
            "tenant_id": corp_id,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "sender_id": staff_id or None,
            "sender_union_id": union_id or None,
            "message_id": message_id,
            "message": {"type": "text", "text": text},
        },
        target=target,
    )
