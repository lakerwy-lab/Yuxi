"""创建挂载到统一 Enterprise MCP 网关的 HR 子应用。"""

from __future__ import annotations

import os

from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from enterprise_mcp.auth import YuxiMcpTokenVerifier
from enterprise_mcp.domains.hr.tools import register_hr_tools
from enterprise_mcp.server import GovernedFastMCP

HR_MCP_AUDIENCE = "enterprise-mcp:hr"

MCP_TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["enterprise-mcp:8010", "127.0.0.1:*", "localhost:*"],
    allowed_origins=["http://enterprise-mcp:8010", "http://127.0.0.1:*", "http://localhost:*"],
)


def create_hr_app():
    """创建仅发布 Streamable HTTP 的 HR 企业 MCP 子应用。

    HR client 延迟到首次工具调用时初始化，缺失配置时报错而非阻止 app 启动。
    """

    resource_base_url = os.getenv("YUXI_ENTERPRISE_MCP_PUBLIC_URL", "http://enterprise-mcp:8010").rstrip("/")
    issuer_url = os.getenv("YUXI_MCP_ISSUER_URL", "http://api:5050").rstrip("/")
    mcp = GovernedFastMCP(
        name="Xinbo HR MCP",
        instructions="按可信钉钉 userId 提供当前用户本人的 HR 考勤只读查询能力。",
        streamable_http_path="/mcp/hr",
        json_response=True,
        stateless_http=True,
        host="0.0.0.0",
        transport_security=MCP_TRANSPORT_SECURITY,
        token_verifier=YuxiMcpTokenVerifier(audience=HR_MCP_AUDIENCE),
        auth=AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=f"{resource_base_url}/mcp/hr",
            required_scopes=["mcp:hr"],
        ),
    )
    register_hr_tools(mcp)
    return mcp.streamable_http_app()
