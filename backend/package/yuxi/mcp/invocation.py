"""从持久化 AgentRun 构造企业 MCP 的可信调用上下文。"""

from __future__ import annotations

from dataclasses import dataclass

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.manager import pg_manager


class McpInvocationContextError(RuntimeError):
    """表示 AgentRun 事实不足或与运行时声明不一致。"""


@dataclass(frozen=True, slots=True)
class McpInvocationContext:
    """一次企业 MCP 调用的不可变可信主体，不进入模型工具参数。"""

    subject_uid: str
    client_id: str
    agent_slug: str
    run_id: str
    request_id: str
    thread_id: str
    trace_id: str
    source: str
    channel: str
    dingtalk_corp_id: str
    dingtalk_union_id: str
    dingtalk_user_id: str
    purpose: str = "invocation"

    @classmethod
    def for_discovery(cls, subject_uid: str) -> McpInvocationContext:
        """创建仅用于管理员工具发现的短期系统上下文。"""

        uid = str(subject_uid or "").strip()
        if not uid:
            raise McpInvocationContextError("MCP 工具发现缺少操作用户")
        return cls(
            subject_uid=uid,
            client_id="yuxi-admin",
            agent_slug="mcp-admin",
            run_id="admin-discovery",
            request_id="admin-discovery",
            thread_id="admin-discovery",
            trace_id="admin-discovery",
            source="admin",
            channel="web",
            dingtalk_corp_id="",
            dingtalk_union_id="",
            dingtalk_user_id="",
            purpose="discovery",
        )


async def build_mcp_invocation_context(
    *,
    run_id: str,
    subject_uid: str,
    request_id: str,
    thread_id: str,
    trace_id: str | None = None,
    client_id: str = "yuxi",
    require_dingtalk_identity: bool = False,
) -> McpInvocationContext:
    """校验运行时标识并从 AgentRun、User 事实构造可信 MCP 上下文。

    require_dingtalk_identity 为 True 时强制校验钉钉身份（会议室等个人域语义）；
    为 False 时不要求，dingtalk_* 字段填空串，仅用于审计。
    """

    required = {
        "run_id": run_id,
        "subject_uid": subject_uid,
        "request_id": request_id,
        "thread_id": thread_id,
        "client_id": client_id,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise McpInvocationContextError(f"MCP 调用上下文缺少字段: {', '.join(missing)}")

    async with pg_manager.get_async_session_context() as db:
        run = await AgentRunRepository(db).get_run_for_user(str(run_id), str(subject_uid))
        if run is None:
            raise McpInvocationContextError("AgentRun 不存在或不属于当前用户")
        if run.request_id != request_id or run.conversation_thread_id != thread_id:
            raise McpInvocationContextError("AgentRun 与运行时请求上下文不一致")

        user = await UserRepository().get_by_uid_with_db(db, str(subject_uid))
        if user is None or user.is_deleted:
            raise McpInvocationContextError("MCP 调用用户不存在或已停用")
        dingtalk_corp_id = str(user.dingtalk_corp_id or "")
        dingtalk_union_id = str(user.dingtalk_union_id or "")
        dingtalk_user_id = str(user.dingtalk_user_id or "")
        if require_dingtalk_identity and not (dingtalk_corp_id and dingtalk_union_id and dingtalk_user_id):
            raise McpInvocationContextError("当前用户缺少完整钉钉身份，不能调用企业 MCP")

        return McpInvocationContext(
            subject_uid=str(run.uid),
            client_id=str(client_id),
            agent_slug=str(run.agent_slug),
            run_id=str(run.id),
            request_id=str(run.request_id),
            thread_id=str(run.conversation_thread_id),
            trace_id=str(trace_id or run.request_id),
            source=str(run.source),
            channel=str(run.channel),
            dingtalk_corp_id=dingtalk_corp_id,
            dingtalk_union_id=dingtalk_union_id,
            dingtalk_user_id=dingtalk_user_id,
        )
