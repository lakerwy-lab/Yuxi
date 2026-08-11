"""高频问答命中后的转人工工具。"""

from __future__ import annotations

from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field
from sqlalchemy import select

from yuxi.agents.toolkits.registry import tool
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.services.qa_pair_service import create_escalation


class EscalateQuestionInput(BaseModel):
    question: str = Field(min_length=1, description="需要转人工处理的问题")
    context: dict[str, Any] = Field(default_factory=dict, description="问题相关的对话上下文")


def _runtime_value(runtime: ToolRuntime, key: str) -> str | None:
    context = getattr(runtime, "context", None)
    value = context.get(key) if isinstance(context, dict) else getattr(context, key, None)
    return str(value).strip() if value else None


@tool(
    category="buildin",
    tags=["问答", "转人工"],
    display_name="转人工处理",
    description="记录当前问题并通知人工客服。只有在知识库没有可靠答案或用户明确要求人工时使用。",
    args_schema=EscalateQuestionInput,
)
async def escalate_question(question: str, context: dict[str, Any], runtime: ToolRuntime) -> dict[str, Any]:
    """创建可追踪、可重试的转人工记录。"""
    uid = _runtime_value(runtime, "uid")
    if not uid:
        raise ValueError("当前运行缺少 uid")
    thread_id = _runtime_value(runtime, "thread_id")
    async with pg_manager.get_async_session_context() as db:
        user = await db.scalar(select(User).where(User.uid == uid))
        if user is None:
            raise ValueError("当前用户不存在")
        item = await create_escalation(
            db,
            uid=user.uid,
            question=question,
            thread_id=thread_id,
            context=context,
        )
    return {
        "type": "qa_escalation",
        "id": item.id,
        "status": item.status,
        "message": "已记录转人工请求，客服会继续处理。",
    }


__all__ = ["escalate_question"]
