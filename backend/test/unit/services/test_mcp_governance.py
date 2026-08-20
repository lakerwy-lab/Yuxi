from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from langchain_mcp_adapters.interceptors import MCPToolCallRequest

from yuxi.agents.mcp import governance
from yuxi.mcp import McpInvocationContext, decode_mcp_invocation_token


pytestmark = [pytest.mark.unit]


def _keys() -> tuple[str, str]:
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
    return private_pem, public_pem


def _context() -> McpInvocationContext:
    return McpInvocationContext(
        subject_uid="dingtalk:corp:union",
        client_id="yuxi",
        agent_slug="assistant",
        run_id="run-1",
        request_id="request-1",
        thread_id="thread-1",
        trace_id="trace-1",
        source="chat",
        channel="web",
        dingtalk_corp_id="corp",
        dingtalk_union_id="union",
        dingtalk_user_id="user",
    )


async def test_enterprise_interceptor_injects_fresh_run_token(monkeypatch):
    private_pem, public_pem = _keys()
    monkeypatch.setenv("YUXI_MCP_SIGNING_PRIVATE_KEY_B64", base64.b64encode(private_pem.encode()).decode())

    async def fake_build(_runtime_context, *, server_slug, trace_id=None):
        assert server_slug == "meeting"
        assert trace_id == "trace-from-runtime"
        return _context()

    monkeypatch.setattr(governance, "build_invocation_context_from_runtime", fake_build)
    runtime = SimpleNamespace(
        context=SimpleNamespace(),
        config={"metadata": {"trace_id": "trace-from-runtime"}},
    )
    request = MCPToolCallRequest(
        name="search_available_rooms",
        args={},
        server_name="meeting",
        runtime=runtime,
    )
    captured = {}

    async def handler(next_request):
        captured.update(next_request.headers or {})
        return SimpleNamespace()

    interceptor = governance.EnterpriseMcpToolCallInterceptor("meeting", {"search_available_rooms"})
    await interceptor(request, handler)

    token = captured["Authorization"].removeprefix("Bearer ")
    claims = decode_mcp_invocation_token(
        token,
        audience="enterprise-mcp:meeting",
        public_key=public_pem,
    )
    assert claims["run_id"] == "run-1"
    assert claims["tools"] == ["search_available_rooms"]


async def test_enterprise_interceptor_rejects_calls_without_tool_runtime():
    request = MCPToolCallRequest(
        name="search_available_rooms",
        args={},
        server_name="meeting",
        runtime=None,
    )

    async def handler(_request):
        raise AssertionError("handler should not run")

    interceptor = governance.EnterpriseMcpToolCallInterceptor("meeting", {"search_available_rooms"})
    with pytest.raises(PermissionError, match="AgentRun"):
        await interceptor(request, handler)


def test_hr_domain_registered_as_enterprise_mcp_with_dingtalk_identity():
    """HR 域注册为企业 MCP，并要求可信钉钉身份。"""

    from yuxi.mcp.governance import (
        enterprise_mcp_audience,
        enterprise_mcp_require_dingtalk,
        enterprise_mcp_tool_names,
        is_enterprise_mcp_server,
    )

    assert is_enterprise_mcp_server("hr")
    assert enterprise_mcp_audience("hr") == "enterprise-mcp:hr"
    assert enterprise_mcp_require_dingtalk("hr") is True
    assert enterprise_mcp_tool_names("hr") == {
        "hr_attendance_sign_records",
        "hr_attendance_daily_detail",
        "hr_attendance_summary",
    }
