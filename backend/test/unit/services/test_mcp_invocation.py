from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from yuxi.mcp import McpInvocationContext, McpInvocationTokenSigner, decode_mcp_invocation_token
from yuxi.mcp import invocation as invocation_module


def _key_pair() -> tuple[str, str]:
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


def test_mcp_invocation_context_is_frozen():
    context = _context()

    with pytest.raises(FrozenInstanceError):
        context.run_id = "forged"  # type: ignore[misc]


def test_mcp_token_binds_audience_subject_run_and_tools():
    private_key, public_key = _key_pair()
    token = McpInvocationTokenSigner(private_key).issue(
        _context(),
        audience="enterprise-mcp:meeting",
        allowed_tools={"search_available_rooms", "confirm_booking"},
    )

    claims = decode_mcp_invocation_token(
        token,
        audience="enterprise-mcp:meeting",
        public_key=public_key,
    )

    assert claims["sub"] == "dingtalk:corp:union"
    assert claims["run_id"] == "run-1"
    assert claims["tools"] == ["confirm_booking", "search_available_rooms"]


def test_mcp_token_rejects_wrong_audience():
    private_key, public_key = _key_pair()
    token = McpInvocationTokenSigner(private_key).issue(
        _context(),
        audience="enterprise-mcp:meeting",
        allowed_tools={"search_available_rooms"},
    )

    with pytest.raises(jwt.InvalidAudienceError):
        decode_mcp_invocation_token(token, audience="enterprise-mcp:wms", public_key=public_key)


@pytest.mark.asyncio
async def test_build_context_rechecks_persisted_run_and_user(monkeypatch):
    """可信上下文必须以 AgentRun 和用户表事实为准。"""

    db = object()

    @asynccontextmanager
    async def fake_session_context():
        yield db

    class FakeRunRepository:
        def __init__(self, actual_db):
            assert actual_db is db

        async def get_run_for_user(self, run_id, uid):
            assert (run_id, uid) == ("run-1", "uid-1")
            return SimpleNamespace(
                id="run-1",
                uid="uid-1",
                agent_slug="assistant",
                request_id="request-1",
                conversation_thread_id="thread-1",
                source="chat",
                channel="dingtalk",
            )

    class FakeUserRepository:
        async def get_by_uid_with_db(self, actual_db, uid):
            assert (actual_db, uid) == (db, "uid-1")
            return SimpleNamespace(
                is_deleted=False,
                dingtalk_corp_id="corp",
                dingtalk_union_id="union",
                dingtalk_user_id="user",
            )

    monkeypatch.setattr(invocation_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(invocation_module, "AgentRunRepository", FakeRunRepository)
    monkeypatch.setattr(invocation_module, "UserRepository", FakeUserRepository)

    context = await invocation_module.build_mcp_invocation_context(
        run_id="run-1",
        subject_uid="uid-1",
        request_id="request-1",
        thread_id="thread-1",
        trace_id="trace-1",
    )

    assert context.subject_uid == "uid-1"
    assert context.agent_slug == "assistant"
    assert context.dingtalk_union_id == "union"


@pytest.mark.asyncio
async def test_build_context_rejects_runtime_mismatch(monkeypatch):
    """运行时声明与持久化 Run 不一致时必须 fail closed。"""

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeRunRepository:
        def __init__(self, _db):
            pass

        async def get_run_for_user(self, _run_id, _uid):
            return SimpleNamespace(request_id="persisted-request", conversation_thread_id="thread-1")

    monkeypatch.setattr(invocation_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(invocation_module, "AgentRunRepository", FakeRunRepository)

    with pytest.raises(invocation_module.McpInvocationContextError, match="不一致"):
        await invocation_module.build_mcp_invocation_context(
            run_id="run-1",
            subject_uid="uid-1",
            request_id="forged-request",
            thread_id="thread-1",
        )


@pytest.mark.asyncio
async def test_build_context_hr_domain_requires_dingtalk_identity(monkeypatch):
    """HR 域要求钉钉身份：无 dingtalk 字段的用户必须被拒绝。"""

    db = object()

    @asynccontextmanager
    async def fake_session_context():
        yield db

    class FakeRunRepository:
        def __init__(self, _db):
            pass

        async def get_run_for_user(self, _run_id, _uid):
            return SimpleNamespace(
                id="run-1",
                uid="uid-1",
                agent_slug="hr-assistant",
                request_id="request-1",
                conversation_thread_id="thread-1",
                source="chat",
                channel="web",
            )

    class FakeUserRepository:
        async def get_by_uid_with_db(self, _db, _uid):
            return SimpleNamespace(is_deleted=False, dingtalk_corp_id="", dingtalk_union_id="", dingtalk_user_id="")

    monkeypatch.setattr(invocation_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(invocation_module, "AgentRunRepository", FakeRunRepository)
    monkeypatch.setattr(invocation_module, "UserRepository", FakeUserRepository)

    with pytest.raises(invocation_module.McpInvocationContextError, match="钉钉身份"):
        await invocation_module.build_mcp_invocation_context(
            run_id="run-1",
            subject_uid="uid-1",
            request_id="request-1",
            thread_id="thread-1",
            require_dingtalk_identity=True,
        )


@pytest.mark.asyncio
async def test_build_context_meeting_domain_requires_dingtalk(monkeypatch):
    """会议室域要求钉钉身份：缺钉钉身份时 fail closed。"""

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeRunRepository:
        def __init__(self, _db):
            pass

        async def get_run_for_user(self, _run_id, _uid):
            return SimpleNamespace(
                id="run-1",
                uid="uid-1",
                agent_slug="assistant",
                request_id="request-1",
                conversation_thread_id="thread-1",
                source="chat",
                channel="dingtalk",
            )

    class FakeUserRepository:
        async def get_by_uid_with_db(self, _db, _uid):
            return SimpleNamespace(is_deleted=False, dingtalk_corp_id="", dingtalk_union_id="", dingtalk_user_id="")

    monkeypatch.setattr(invocation_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(invocation_module, "AgentRunRepository", FakeRunRepository)
    monkeypatch.setattr(invocation_module, "UserRepository", FakeUserRepository)

    with pytest.raises(invocation_module.McpInvocationContextError, match="钉钉身份"):
        await invocation_module.build_mcp_invocation_context(
            run_id="run-1",
            subject_uid="uid-1",
            request_id="request-1",
            thread_id="thread-1",
            require_dingtalk_identity=True,
        )
