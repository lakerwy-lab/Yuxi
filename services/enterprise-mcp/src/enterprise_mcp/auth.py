"""验证 Yuxi 签发的 audience-bound MCP 调用令牌。"""

from __future__ import annotations

from typing import Any

import jwt
from mcp.server.auth.provider import AccessToken

from yuxi.mcp import MCP_TOKEN_ISSUER, decode_mcp_invocation_token, load_mcp_public_key


class YuxiMcpTokenVerifier:
    """把 Yuxi Ed25519 JWT 验证结果转换为 MCP AccessToken。"""

    def __init__(self, *, audience: str, public_key: str | None = None):
        self.audience = audience
        self.scope = audience.replace("enterprise-mcp:", "mcp:", 1)
        self.public_key = public_key or load_mcp_public_key()

    async def verify_token(self, token: str) -> AccessToken | None:
        """校验签名和必要 claims；无效令牌交由 MCP auth middleware 拒绝。"""

        try:
            claims = decode_mcp_invocation_token(
                token,
                audience=self.audience,
                public_key=self.public_key,
                issuer=MCP_TOKEN_ISSUER,
            )
        except jwt.PyJWTError:
            return None

        return AccessToken(
            token=token,
            client_id=str(claims["client_id"]),
            subject=str(claims["sub"]),
            scopes=[self.scope],
            expires_at=int(claims["exp"]),
            resource=self.audience,
            claims=dict(claims),
        )


def require_invocation_claims() -> dict[str, Any]:
    """读取当前 MCP 请求的可信 claims。"""

    from mcp.server.auth.middleware.auth_context import get_access_token

    access_token = get_access_token()
    if access_token is None or not isinstance(access_token.claims, dict):
        raise PermissionError("缺少可信 MCP 调用上下文")
    return dict(access_token.claims)


def require_tool_allowed(tool_name: str) -> dict[str, Any]:
    """校验调用令牌是否授权当前工具。"""

    claims = require_invocation_claims()
    allowed_tools = claims.get("tools")
    if not isinstance(allowed_tools, list) or tool_name not in allowed_tools:
        raise PermissionError(f"当前调用上下文无权使用工具 {tool_name}")
    return claims


def require_dingtalk_user_id() -> tuple[dict[str, Any], str]:
    """读取可信钉钉 userId，禁止业务工具接受模型传入的身份。"""

    claims = require_invocation_claims()
    user_id = str(claims.get("dingtalk_user_id") or "").strip()
    if not user_id:
        raise PermissionError("Enterprise MCP 调用上下文缺少钉钉 userId")
    return claims, user_id
