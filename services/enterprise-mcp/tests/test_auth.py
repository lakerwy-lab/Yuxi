from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from enterprise_mcp import auth as auth_module
from enterprise_mcp.auth import YuxiMcpTokenVerifier
from yuxi.mcp import McpInvocationContext, McpInvocationTokenSigner


def _context() -> McpInvocationContext:
    return McpInvocationContext(
        subject_uid="uid",
        client_id="yuxi",
        agent_slug="assistant",
        run_id="run",
        request_id="request",
        thread_id="thread",
        trace_id="trace",
        source="chat",
        channel="web",
        dingtalk_corp_id="corp",
        dingtalk_union_id="union",
        dingtalk_user_id="user",
    )


async def test_token_verifier_returns_mcp_access_token():
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    token = McpInvocationTokenSigner(private_pem).issue(
        _context(),
        audience="enterprise-mcp:meeting",
        allowed_tools={"search_available_rooms"},
    )

    access_token = await YuxiMcpTokenVerifier(
        audience="enterprise-mcp:meeting",
        public_key=public_pem,
    ).verify_token(token)

    assert access_token is not None
    assert access_token.subject == "uid"
    assert access_token.scopes == ["mcp:meeting"]
    assert access_token.claims["tools"] == ["search_available_rooms"]


async def test_token_verifier_uses_audience_specific_scope():
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    token = McpInvocationTokenSigner(private_pem).issue(
        _context(),
        audience="enterprise-mcp:hr",
        allowed_tools={"hr_attendance_sign_records"},
    )

    access_token = await YuxiMcpTokenVerifier(
        audience="enterprise-mcp:hr",
        public_key=public_pem,
    ).verify_token(token)

    assert access_token is not None
    assert access_token.scopes == ["mcp:hr"]


def test_dingtalk_user_id_is_required_for_business_tools(monkeypatch):
    monkeypatch.setattr(
        auth_module,
        "require_invocation_claims",
        lambda: {"sub": "uid", "dingtalk_user_id": "dingtalk-user"},
    )
    claims, user_id = auth_module.require_dingtalk_user_id()

    assert claims["sub"] == "uid"
    assert user_id == "dingtalk-user"

    monkeypatch.setattr(auth_module, "require_invocation_claims", lambda: {"sub": "uid"})
    with pytest.raises(PermissionError, match="userId"):
        auth_module.require_dingtalk_user_id()
