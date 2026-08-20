from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from yuxi.mcp import McpInvocationContext, McpInvocationTokenSigner, decode_mcp_invocation_token
from yuxi.mcp.keygen import ensure_mcp_signing_key_pair


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
        channel="dingtalk",
        dingtalk_corp_id="corp",
        dingtalk_union_id="union",
        dingtalk_user_id="user",
    )


def test_keygen_persists_matching_key_pair_without_rotation(tmp_path: Path):
    private_path, public_path = ensure_mcp_signing_key_pair(tmp_path)
    first_private = private_path.read_bytes()

    repeated_private, repeated_public = ensure_mcp_signing_key_pair(tmp_path)

    assert repeated_private.read_bytes() == first_private
    private_key = serialization.load_pem_private_key(first_private, password=None)
    assert isinstance(private_key, Ed25519PrivateKey)
    assert repeated_public.read_bytes() == private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert public_path == repeated_public


def test_token_signer_and_verifier_read_generated_key_files(tmp_path: Path, monkeypatch):
    private_path, public_path = ensure_mcp_signing_key_pair(tmp_path)
    monkeypatch.delenv("YUXI_MCP_SIGNING_PRIVATE_KEY_B64", raising=False)
    monkeypatch.delenv("YUXI_MCP_SIGNING_PUBLIC_KEY_B64", raising=False)
    monkeypatch.setenv("YUXI_MCP_SIGNING_PRIVATE_KEY_FILE", str(private_path))
    monkeypatch.setenv("YUXI_MCP_SIGNING_PUBLIC_KEY_FILE", str(public_path))

    token = McpInvocationTokenSigner.from_env().issue(
        _context(),
        audience="enterprise-mcp:meeting",
        allowed_tools={"get_my_bookings"},
    )
    claims = decode_mcp_invocation_token(token, audience="enterprise-mcp:meeting")

    assert claims["sub"] == "uid"
    assert claims["tools"] == ["get_my_bookings"]
