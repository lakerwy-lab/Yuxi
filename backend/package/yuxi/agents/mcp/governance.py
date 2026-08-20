"""为 Enterprise MCP 工具调用动态注入可信 AgentRun 令牌。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection

from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult

from yuxi.mcp import McpInvocationTokenSigner, build_mcp_invocation_context
from yuxi.mcp.governance import (
    enterprise_mcp_audience,
    enterprise_mcp_require_dingtalk,
    is_enterprise_mcp_server,
)

__all__ = [
    "EnterpriseMcpToolCallInterceptor",
    "build_invocation_context_from_runtime",
    "is_enterprise_mcp_server",
]


async def build_invocation_context_from_runtime(runtime_context, *, server_slug: str, trace_id: str | None = None):
    """从 BaseContext 标识回查持久化 AgentRun，构造不可变可信上下文。"""

    return await build_mcp_invocation_context(
        run_id=str(getattr(runtime_context, "run_id", "") or ""),
        subject_uid=str(getattr(runtime_context, "uid", "") or ""),
        request_id=str(getattr(runtime_context, "request_id", "") or ""),
        thread_id=str(getattr(runtime_context, "thread_id", "") or ""),
        trace_id=trace_id,
        require_dingtalk_identity=enterprise_mcp_require_dingtalk(server_slug),
    )


class EnterpriseMcpToolCallInterceptor:
    """在每次 tools/call 前重新签发短期、目标绑定的调用令牌。"""

    def __init__(self, server_slug: str, allowed_tools: Collection[str]):
        self.server_slug = server_slug
        self.allowed_tools = frozenset(allowed_tools)

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
    ) -> MCPToolCallResult:
        """从 ToolRuntime 构造可信上下文，拒绝脱离 AgentRun 的企业工具调用。"""

        runtime_context = getattr(request.runtime, "context", None)
        if runtime_context is None:
            raise PermissionError("Enterprise MCP 工具只能在可信 AgentRun 中调用")

        runtime_config = getattr(request.runtime, "config", None)
        metadata = runtime_config.get("metadata", {}) if isinstance(runtime_config, dict) else {}
        trace_id = str(metadata.get("trace_id") or "") if isinstance(metadata, dict) else ""
        invocation = await build_invocation_context_from_runtime(
            runtime_context, server_slug=self.server_slug, trace_id=trace_id or None
        )
        signer = McpInvocationTokenSigner.from_env()
        token = signer.issue(
            invocation,
            audience=enterprise_mcp_audience(self.server_slug),
            allowed_tools=self.allowed_tools,
        )
        return await handler(request.override(headers={"Authorization": f"Bearer {token}"}))
