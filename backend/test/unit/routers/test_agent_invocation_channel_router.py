from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

router = importlib.import_module("server.routers.agent_invocation_channel_router")


def _payload(text: str, **kwargs):
    values = {
        "agent_slug": "default-chatbot",
        "thread_id": "thread-1",
        "message": {"type": "text", "text": text},
        "message_id": "message-1",
    }
    values.update(kwargs)
    return router.ChannelMessageRequest(
        **values,
    )


def test_channel_rejects_overlong_message_id_at_schema_boundary():
    with pytest.raises(ValidationError):
        _payload("你好", message_id="x" * 129)


def test_channel_rejects_overlong_channel_at_schema_boundary():
    with pytest.raises(ValidationError):
        _payload("你好", channel="x" * 33)


@pytest.mark.asyncio
async def test_plain_channel_text_uses_shared_submission(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_submit_run_command(*, command, **_kwargs):
        calls["command"] = command
        return {"run_id": "run-1", "thread_id": command.thread_id, "status": "dispatched"}

    class EmptyRunRepo:
        def __init__(self, db):
            del db

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return None

    monkeypatch.setattr(router, "submit_run_command", fake_submit_run_command)
    monkeypatch.setattr(router, "AgentRunRepository", EmptyRunRepo)
    result = await router.receive_channel_message(
        _payload("你好", channel="cli", account_id="local", chat_id="chat-1"),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert result["kind"] == "run"
    assert calls["command"].origin.source == "channel"
    assert calls["command"].origin.channel == "cli"
    assert calls["command"].origin.external_id == "message-1"
    assert calls["command"].queue_policy == "steer"


@pytest.mark.asyncio
async def test_channel_rejects_whitespace_text_before_submission(monkeypatch: pytest.MonkeyPatch):
    async def fail_submit(**_kwargs):
        raise AssertionError("空白消息不应进入提交服务")

    monkeypatch.setattr(router, "submit_run_command", fail_submit)

    with pytest.raises(HTTPException) as exc:
        await router.receive_channel_message(
            _payload("   "),
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "text 不能为空"


@pytest.mark.asyncio
async def test_channel_uses_request_id_as_external_id_when_message_id_missing(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_submit_run_command(*, command, **_kwargs):
        calls["command"] = command
        return {"run_id": "run-1", "thread_id": command.thread_id, "status": "dispatched"}

    class EmptyRunRepo:
        def __init__(self, db):
            del db

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return None

    monkeypatch.setattr(router, "submit_run_command", fake_submit_run_command)
    monkeypatch.setattr(router, "AgentRunRepository", EmptyRunRepo)
    await router.receive_channel_message(
        router.ChannelMessageRequest(
            agent_slug="default-chatbot",
            thread_id="thread-1",
            request_id="request-1",
            message={"type": "text", "text": "你好"},
        ),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert calls["command"].request_id == "request-1"
    assert calls["command"].origin.external_id == "request-1"


@pytest.mark.asyncio
async def test_state_command_does_not_submit(monkeypatch: pytest.MonkeyPatch):
    async def fail_submit(**_kwargs):
        raise AssertionError("/state must not create a Request")

    async def fake_state(**_kwargs):
        return {"agent_state": {"todos": []}}

    monkeypatch.setattr(router, "submit_run_command", fail_submit)
    monkeypatch.setattr(router, "get_agent_state_view", fake_state)
    result = await router.receive_channel_message(
        _payload("/state"),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )
    assert result == {
        "kind": "command",
        "command": "state",
        "thread_id": "thread-1",
        "state": {"agent_state": {"todos": []}},
    }


@pytest.mark.asyncio
async def test_approve_command_creates_resume_without_submit(monkeypatch: pytest.MonkeyPatch):
    class RunRepo:
        def __init__(self, db):
            del db

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return SimpleNamespace(id="run-parent", status="interrupted", error_type="human_approval_required")

    calls: dict[str, object] = {}

    async def fake_create_agent_run_view(**kwargs):
        calls["kwargs"] = kwargs
        return {"run_id": "run-resume", "status": "pending", "thread_id": kwargs["thread_id"]}

    async def fail_submit(**_kwargs):
        raise AssertionError("/approve must not create a Request")

    monkeypatch.setattr(router, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(router, "create_agent_run_view", fake_create_agent_run_view)
    monkeypatch.setattr(router, "submit_run_command", fail_submit)
    result = await router.receive_channel_message(
        _payload("/approve"),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert result["command"] == "approve"
    assert result["run"]["run_id"] == "run-resume"
    assert calls["kwargs"]["created_by_run_id"] == "run-parent"
    assert calls["kwargs"]["resume"] == {"decisions": [{"type": "approve"}]}


@pytest.mark.asyncio
async def test_approve_command_reuses_existing_resume_when_it_is_latest(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="resume-run",
        uid="user-1",
        agent_slug="default-chatbot",
        conversation_thread_id="thread-1",
        run_type="resume",
        status="pending",
        request_id="request-1",
        created_by_run_id="parent-run",
    )

    class RunRepo:
        def __init__(self, db):
            del db

        async def get_run_by_request_id(self, request_id: str):
            assert request_id == "request-1"
            return existing_run

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return existing_run

    async def fake_create_agent_run_view(**kwargs):
        assert kwargs["created_by_run_id"] == "parent-run"
        return {
            "run_id": existing_run.id,
            "thread_id": existing_run.conversation_thread_id,
            "status": existing_run.status,
            "request_id": existing_run.request_id,
            "stream_url": f"/api/agent/runs/{existing_run.id}/events",
        }

    monkeypatch.setattr(router, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(router, "create_agent_run_view", fake_create_agent_run_view)

    result = await router.receive_channel_message(
        _payload("/approve", request_id="request-1"),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert result["command"] == "approve"
    assert result["run"]["run_id"] == "resume-run"


@pytest.mark.asyncio
async def test_approve_command_rejects_request_id_from_older_resume(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="old-resume",
        uid="user-1",
        agent_slug="default-chatbot",
        conversation_thread_id="thread-1",
        run_type="resume",
        status="completed",
        request_id="request-1",
        created_by_run_id="old-parent",
    )
    latest_run = SimpleNamespace(
        id="new-parent",
        status="interrupted",
        error_type="human_approval_required",
    )

    class RunRepo:
        def __init__(self, db):
            del db

        async def get_run_by_request_id(self, request_id: str):
            assert request_id == "request-1"
            return existing_run

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return latest_run

    monkeypatch.setattr(router, "AgentRunRepository", RunRepo)

    with pytest.raises(HTTPException) as exc:
        await router.receive_channel_message(
            _payload("/approve", request_id="request-1"),
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "request_id 冲突"


@pytest.mark.asyncio
async def test_channel_does_not_treat_question_interrupt_as_approval(monkeypatch: pytest.MonkeyPatch):
    class RunRepo:
        def __init__(self, db):
            del db

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return SimpleNamespace(id="run-parent", status="interrupted", error_type="ask_user_question_required")

    monkeypatch.setattr(router, "AgentRunRepository", RunRepo)
    with pytest.raises(HTTPException) as exc:
        await router.receive_channel_message(
            _payload("继续"),
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "ask_user_question_unsupported"


@pytest.mark.asyncio
async def test_dingtalk_delivery_resolves_user_and_uses_server_agent(monkeypatch: pytest.MonkeyPatch):
    """外部钉钉投递由服务端映射用户和 Agent，Gateway 不能传入 uid。"""

    class BotOptions:
        async def get(self, db):
            assert db is not None
            return {"enabled": "true", "agent_slug": "it-agent", "robot_code": "robot-1"}

    user = SimpleNamespace(uid="user-1", department_id=3)

    class Users:
        async def get_by_dingtalk_identity_with_db(self, db, **identity):
            assert db is not None
            assert identity == {"corp_id": "corp-1", "user_id": "staff-1", "union_id": "union-1"}
            return user

    calls: dict[str, object] = {}

    async def fake_receive(payload, *, current_user, db):
        calls.update(payload=payload, user=current_user, db=db)
        return {"kind": "run", "run_id": "run-1", "status": "dispatched"}

    monkeypatch.setattr(router, "dingtalk_bot_opts", BotOptions())
    monkeypatch.setattr(router, "UserRepository", Users)
    monkeypatch.setattr(router, "receive_channel_message", fake_receive)

    result = await router.receive_channel_delivery(
        router.ChannelDeliveryRequest(
            account_id="robot-1",
            tenant_id="corp-1",
            chat_id="conversation-1",
            chat_type="group",
            sender_id="staff-1",
            sender_union_id="union-1",
            message_id="message-1",
            message={"type": "text", "text": "查库存"},
        ),
        _gateway=None,
        db=object(),
    )

    payload = calls["payload"]
    assert calls["user"] is user
    assert payload.agent_slug == "it-agent"
    assert payload.chat_id == "corp-1:group:conversation-1"
    assert payload.queue_policy == "steer"
    assert result["stream_url"] == "/api/agent-invocation/channel/runs/run-1/events"
    assert result["result_url"] == "/api/agent-invocation/channel/runs/run-1/result"


@pytest.mark.asyncio
async def test_dingtalk_delivery_rejects_unbound_user(monkeypatch: pytest.MonkeyPatch):
    """未绑定用户不能借助服务凭证创建系统账号 Run。"""

    class BotOptions:
        async def get(self, _db):
            return {"enabled": True, "agent_slug": "it-agent", "robot_code": "robot-1"}

    class Users:
        async def get_by_dingtalk_identity_with_db(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(router, "dingtalk_bot_opts", BotOptions())
    monkeypatch.setattr(router, "UserRepository", Users)

    with pytest.raises(HTTPException) as exc:
        await router.receive_channel_delivery(
            router.ChannelDeliveryRequest(
                account_id="robot-1",
                tenant_id="corp-1",
                chat_id="staff-1",
                chat_type="direct",
                sender_id="staff-1",
                message_id="message-1",
                message={"type": "text", "text": "你好"},
            ),
            _gateway=None,
            db=object(),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "未在平台绑定钉钉身份"


@pytest.mark.asyncio
async def test_channel_run_scope_rejects_other_account(monkeypatch: pytest.MonkeyPatch):
    """服务 token 不能跨 Channel 账号读取 Run。"""

    run = SimpleNamespace(
        id="run-1",
        source="channel",
        channel="dingtalk_bot",
        origin_metadata={"account_id": "robot-1"},
    )

    class Runs:
        def __init__(self, _db):
            pass

        async def get_run(self, run_id):
            assert run_id == "run-1"
            return run

    monkeypatch.setattr(router, "AgentRunRepository", Runs)

    with pytest.raises(HTTPException) as exc:
        await router._require_channel_run(
            object(),
            run_id="run-1",
            channel="dingtalk_bot",
            account_id="robot-2",
        )

    assert exc.value.status_code == 404
