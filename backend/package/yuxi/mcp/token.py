"""签发和验证 audience-bound 企业 MCP 调用令牌。"""

from __future__ import annotations

import base64
import os
import time
import uuid
from collections.abc import Collection
from pathlib import Path
from typing import Any

import jwt

from yuxi.mcp.invocation import McpInvocationContext

MCP_TOKEN_ISSUER = "yuxi"
MCP_PRIVATE_KEY_ENV = "YUXI_MCP_SIGNING_PRIVATE_KEY_B64"
MCP_PUBLIC_KEY_ENV = "YUXI_MCP_SIGNING_PUBLIC_KEY_B64"
MCP_PRIVATE_KEY_FILE_ENV = "YUXI_MCP_SIGNING_PRIVATE_KEY_FILE"
MCP_PUBLIC_KEY_FILE_ENV = "YUXI_MCP_SIGNING_PUBLIC_KEY_FILE"


def _load_pem(*, encoded_env: str, file_env: str) -> str:
    """优先读取兼容的 Base64 PEM，否则读取项目持久化密钥文件。"""

    encoded = os.getenv(encoded_env, "").strip()
    if encoded:
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"{encoded_env} 不是有效的 base64 PEM") from exc

    file_path = os.getenv(file_env, "").strip()
    if not file_path:
        raise RuntimeError(f"未配置 {encoded_env} 或 {file_env}")
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"无法读取 {file_env}: {file_path}") from exc


class McpInvocationTokenSigner:
    """使用 Ed25519 私钥签发短期企业 MCP 调用令牌。"""

    def __init__(self, private_key: str, *, issuer: str = MCP_TOKEN_ISSUER):
        self.private_key = private_key
        self.issuer = issuer

    @classmethod
    def from_env(cls) -> McpInvocationTokenSigner:
        """从环境变量创建签名器。"""

        return cls(_load_pem(encoded_env=MCP_PRIVATE_KEY_ENV, file_env=MCP_PRIVATE_KEY_FILE_ENV))

    def issue(
        self,
        context: McpInvocationContext,
        *,
        audience: str,
        allowed_tools: Collection[str],
        ttl_seconds: int = 90,
    ) -> str:
        """签发绑定目标 MCP、主体、Run 与允许工具集合的短期令牌。"""

        if not audience.strip():
            raise ValueError("MCP token audience 不能为空")
        if not 30 <= ttl_seconds <= 300:
            raise ValueError("MCP token TTL 必须在 30~300 秒之间")

        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": audience,
            "sub": context.subject_uid,
            "client_id": context.client_id,
            "agent": context.agent_slug,
            "run_id": context.run_id,
            "request_id": context.request_id,
            "thread_id": context.thread_id,
            "trace_id": context.trace_id,
            "source": context.source,
            "channel": context.channel,
            "dingtalk_corp_id": context.dingtalk_corp_id,
            "dingtalk_union_id": context.dingtalk_union_id,
            "dingtalk_user_id": context.dingtalk_user_id,
            "purpose": context.purpose,
            "tools": sorted(set(allowed_tools)),
            "jti": str(uuid.uuid4()),
            "iat": now,
            "nbf": now - 2,
            "exp": now + ttl_seconds,
        }
        return jwt.encode(claims, self.private_key, algorithm="EdDSA")


def load_mcp_public_key() -> str:
    """从环境变量读取企业 MCP 验签公钥。"""

    return _load_pem(encoded_env=MCP_PUBLIC_KEY_ENV, file_env=MCP_PUBLIC_KEY_FILE_ENV)


def decode_mcp_invocation_token(
    token: str,
    *,
    audience: str,
    public_key: str | None = None,
    issuer: str = MCP_TOKEN_ISSUER,
) -> dict[str, Any]:
    """验签并校验 issuer、audience、有效期和必需调用字段。"""

    key = public_key or _load_pem(encoded_env=MCP_PUBLIC_KEY_ENV, file_env=MCP_PUBLIC_KEY_FILE_ENV)
    claims = jwt.decode(
        token,
        key,
        algorithms=["EdDSA"],
        audience=audience,
        issuer=issuer,
        options={
            "require": [
                "iss",
                "aud",
                "sub",
                "client_id",
                "agent",
                "run_id",
                "request_id",
                "thread_id",
                "trace_id",
                "source",
                "channel",
                "dingtalk_corp_id",
                "dingtalk_union_id",
                "dingtalk_user_id",
                "purpose",
                "tools",
                "jti",
                "iat",
                "nbf",
                "exp",
            ]
        },
    )
    if not isinstance(claims.get("tools"), list) or not all(isinstance(item, str) for item in claims["tools"]):
        raise jwt.InvalidTokenError("MCP token tools claim 必须是字符串数组")
    return claims
