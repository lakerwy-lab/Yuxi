"""单进程 Enterprise MCP 网关 ASGI 入口。"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack, asynccontextmanager

from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from enterprise_mcp.auth import YuxiMcpTokenVerifier
from enterprise_mcp.domains.meeting.tools import register_meeting_tools
from enterprise_mcp.hr_app import create_hr_app
from enterprise_mcp.server import GovernedFastMCP
from yuxi.mcp.meeting import MEETING_MCP_AUDIENCE

MCP_TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["enterprise-mcp:8010", "127.0.0.1:*", "localhost:*"],
    allowed_origins=["http://enterprise-mcp:8010", "http://127.0.0.1:*", "http://localhost:*"],
)


class EnterpriseMcpGateway:
    """按稳定路径分发到独立鉴权的 FastMCP 子应用。"""

    def __init__(self, meeting_app: ASGIApp, hr_app: ASGIApp, lifecycle_app: ASGIApp):
        self.lifecycle_app = lifecycle_app
        self.endpoint_apps = {
            "/mcp/meeting": meeting_app,
            "/.well-known/oauth-protected-resource/mcp/meeting": meeting_app,
            "/mcp/hr": hr_app,
            "/.well-known/oauth-protected-resource/mcp/hr": hr_app,
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """保留原始请求路径，使两个子应用可共享端口而不改变 MCP URL。"""

        if scope["type"] == "lifespan":
            await self.lifecycle_app(scope, receive, send)
            return

        target = self.endpoint_apps.get(scope.get("path", ""), self.lifecycle_app)
        await target(scope, receive, send)


def create_app() -> EnterpriseMcpGateway:
    """创建同时发布 meeting 和 HR endpoint 的单进程网关。"""

    meeting_app = create_meeting_app()
    hr_app = create_hr_app()

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(meeting_app.router.lifespan_context(meeting_app))
            await stack.enter_async_context(hr_app.router.lifespan_context(hr_app))
            yield

    lifecycle_app = Starlette(routes=[Route("/health", health)], lifespan=lifespan)
    return EnterpriseMcpGateway(meeting_app, hr_app, lifecycle_app)


def create_meeting_app() -> Starlette:
    """创建会议室 FastMCP 子应用。"""

    resource_base_url = os.getenv("YUXI_ENTERPRISE_MCP_PUBLIC_URL", "http://enterprise-mcp:8010").rstrip("/")
    issuer_url = os.getenv("YUXI_MCP_ISSUER_URL", "http://api:5050").rstrip("/")
    mcp = GovernedFastMCP(
        name="Xinbo Meeting MCP",
        instructions="提供受 Xinbo 身份和工具授权约束的企业会议室能力。",
        streamable_http_path="/mcp/meeting",
        json_response=True,
        stateless_http=True,
        host="0.0.0.0",
        transport_security=MCP_TRANSPORT_SECURITY,
        token_verifier=YuxiMcpTokenVerifier(audience=MEETING_MCP_AUDIENCE),
        auth=AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=f"{resource_base_url}/mcp/meeting",
            required_scopes=["mcp:meeting"],
        ),
    )
    register_meeting_tools(mcp)
    return mcp.streamable_http_app()


async def health(_request: Request) -> JSONResponse:
    """返回聚合网关健康状态和已挂载业务域。"""

    return JSONResponse({"status": "ok", "domains": ["meeting", "hr"]})


app = create_app()
