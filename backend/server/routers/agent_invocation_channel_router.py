"""纯文本 Channel 消息入口。

Channel 只负责把消息信封转换为统一 Run 提交命令；少量控制命令在普通
消息提交之前处理，避免状态查询或审批决议被错误排进 Agent Request 队列。
"""

from __future__ import annotations

import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse
from yuxi.config.options import dingtalk_bot_opts
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.agent_run_service import (
    cancel_agent_run_view,
    create_agent_run_view,
    get_agent_run_result,
    stream_agent_run_events,
)
from yuxi.services.channel_command_service import parse_slash_command
from yuxi.services.chat_service import get_agent_state_view
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.run_submission_service import RunOrigin, RunSubmissionCommand, submit_run_command
from yuxi.storage.postgres.models_business import User
from yuxi.utils.hash_utils import hash_id

from server.utils.auth_middleware import get_db, get_required_user, require_channel_gateway

agent_invocation_channel_router = APIRouter(prefix="/agent-invocation/channel", tags=["agent-invocation"])


class ChannelTextMessage(BaseModel):
    """Channel 普通文本消息体。"""

    type: Literal["text"] = "text"
    text: str = Field(..., min_length=1, description="纯文本消息")


class ChannelMessageRequest(BaseModel):
    """Channel 消息信封，承载来源账号、线程与幂等标识。"""

    channel: str = Field("cli", max_length=32, description="通道名称")
    account_id: str = Field("default", max_length=128, description="通道账号标识")
    chat_id: str | None = Field(None, description="通道侧会话标识")
    thread_id: str | None = Field(None, description="可选 Yuxi Thread ID")
    sender_id: str | None = Field(None, description="通道侧发送者标识")
    message_id: str | None = Field(None, max_length=128, description="通道侧消息 ID")
    request_id: str | None = Field(None, description="请求幂等 ID")
    agent_slug: str = Field(..., description="目标 Agent slug")
    message: ChannelTextMessage
    queue_policy: Literal["enqueue", "reject", "steer"] = "steer"


class ChannelDeliveryRequest(BaseModel):
    """独立 Channel Gateway 投递的钉钉消息信封。"""

    channel: Literal["dingtalk_bot"] = "dingtalk_bot"
    account_id: str = Field(..., min_length=1, max_length=128)
    tenant_id: str = Field(..., min_length=1, max_length=128)
    chat_id: str = Field(..., min_length=1, max_length=256)
    chat_type: Literal["direct", "group"]
    sender_id: str | None = Field(None, max_length=128)
    sender_union_id: str | None = Field(None, max_length=128)
    message_id: str = Field(..., min_length=1, max_length=128)
    message: ChannelTextMessage


@agent_invocation_channel_router.post("/messages")
async def receive_channel_message(
    payload: ChannelMessageRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """处理纯文本 Channel 消息或最小 slash command。"""
    channel = _normalize_required(payload.channel, "channel")
    account_id = _normalize_required(payload.account_id, "account_id")
    agent_slug = _normalize_required(payload.agent_slug, "agent_slug")
    thread_id = _resolve_thread_id(
        uid=str(current_user.uid),
        channel=channel,
        account_id=account_id,
        chat_id=payload.chat_id,
        requested_thread_id=payload.thread_id,
    )
    message_text = payload.message.text.strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="text 不能为空")
    raw_request_id = str(payload.request_id or "").strip()
    external_id = str(payload.message_id or "").strip() or raw_request_id or str(uuid.uuid4())
    request_id = raw_request_id or hash_id(
        "channel_request_",
        f"{current_user.uid}:{channel}:{account_id}:{payload.chat_id or thread_id}:{external_id}",
        length=64,
    )
    if len(request_id) > 64:
        raise HTTPException(status_code=422, detail="request_id 不能超过 64 个字符")
    origin_metadata = {
        key: value
        for key, value in {
            "account_id": account_id,
            "chat_id": payload.chat_id,
            "sender_id": payload.sender_id,
        }.items()
        if value
    }

    try:
        command = parse_slash_command(message_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if command is not None:
        if command.name == "state":
            _require_no_args(command.name, command.args)
            state = await get_agent_state_view(
                thread_id=thread_id,
                current_user=current_user,
                db=db,
                include_messages=False,
            )
            return {"kind": "command", "command": "state", "thread_id": thread_id, "state": state}
        if command.name == "approve":
            _require_no_args(command.name, command.args)
            return await _approve_latest_run(
                agent_slug=agent_slug,
                thread_id=thread_id,
                request_id=request_id,
                external_id=external_id,
                channel=channel,
                origin_metadata=origin_metadata,
                current_user=current_user,
                db=db,
            )
        raise HTTPException(status_code=422, detail=f"不支持的 slash command: /{command.name}")

    latest_run = await AgentRunRepository(db).get_latest_chat_or_resume_run(
        uid=str(current_user.uid),
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if latest_run and latest_run.status == "interrupted" and latest_run.error_type != "human_approval_required":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ask_user_question_unsupported",
                "message": "当前线程等待用户回答，Channel 暂不支持 ask_user_question",
            },
        )

    result = await submit_run_command(
        command=RunSubmissionCommand(
            agent_slug=agent_slug,
            thread_id=thread_id,
            request_id=request_id,
            input_message=build_chat_input_message(message_text),
            origin=RunOrigin(
                source="channel",
                channel=channel,
                external_id=external_id,
                metadata=origin_metadata,
            ),
            request_metadata={"message_type": "text"},
            queue_policy=payload.queue_policy,
            create_conversation=True,
            conversation_title=f"{channel} Channel Run",
        ),
        current_user=current_user,
        db=db,
    )
    result["kind"] = "run"
    result["channel"] = channel
    return result


@agent_invocation_channel_router.post("/deliveries")
async def receive_channel_delivery(
    payload: ChannelDeliveryRequest,
    _gateway: None = Depends(require_channel_gateway),
    db: AsyncSession = Depends(get_db),
):
    """接收独立钉钉 Channel 的消息，并以映射后的真实用户提交 Run。"""

    del _gateway
    if not payload.sender_id and not payload.sender_union_id:
        raise HTTPException(status_code=422, detail="sender_id 或 sender_union_id 至少提供一个")

    config = await dingtalk_bot_opts.get(db)
    enabled_value = config.get("enabled")
    if enabled_value is None:
        enabled_value = os.getenv("DINGTALK_BOT_ENABLED")
    if not _option_enabled(enabled_value):
        raise HTTPException(status_code=503, detail="钉钉机器人未启用")

    configured_account = str(config.get("robot_code") or "").strip()
    if configured_account and configured_account != payload.account_id:
        raise HTTPException(status_code=403, detail="钉钉机器人账号不匹配")

    user = await UserRepository().get_by_dingtalk_identity_with_db(
        db,
        corp_id=payload.tenant_id,
        user_id=payload.sender_id,
        union_id=payload.sender_union_id,
    )
    if user is None:
        raise HTTPException(status_code=403, detail="未在平台绑定钉钉身份")
    if not user.department_id:
        raise HTTPException(status_code=400, detail="当前用户未绑定部门")

    agent_slug = str(config.get("agent_slug") or "default-chatbot").strip() or "default-chatbot"
    scoped_chat_id = f"{payload.tenant_id}:{payload.chat_type}:{payload.chat_id}"
    result = await receive_channel_message(
        ChannelMessageRequest(
            channel=payload.channel,
            account_id=payload.account_id,
            chat_id=scoped_chat_id,
            sender_id=payload.sender_id or payload.sender_union_id,
            message_id=payload.message_id,
            agent_slug=agent_slug,
            message=payload.message,
            queue_policy="steer",
        ),
        current_user=user,
        db=db,
    )

    run_id = result.get("run_id")
    if run_id:
        base = f"/api/agent-invocation/channel/runs/{run_id}"
        result["stream_url"] = f"{base}/events"
        result["result_url"] = f"{base}/result"
    return result


@agent_invocation_channel_router.get("/runs/{run_id}/events")
async def stream_channel_run_events(
    run_id: str,
    channel: str = Query(..., max_length=32),
    account_id: str = Query(..., max_length=128),
    after_seq: str = "0-0",
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    _gateway: None = Depends(require_channel_gateway),
    db: AsyncSession = Depends(get_db),
):
    """向所属 Channel Gateway 输出精简 Run SSE。"""

    del _gateway
    run = await _require_channel_run(db, run_id=run_id, channel=channel, account_id=account_id)
    cursor = last_event_id or after_seq
    return StreamingResponse(
        stream_agent_run_events(run_id=run_id, after_seq=cursor, current_uid=str(run.uid), verbose=False),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@agent_invocation_channel_router.get("/runs/{run_id}/result")
async def get_channel_run_result(
    run_id: str,
    channel: str = Query(..., max_length=32),
    account_id: str = Query(..., max_length=128),
    _gateway: None = Depends(require_channel_gateway),
    db: AsyncSession = Depends(get_db),
):
    """读取所属 Channel Run 的权威最终结果。"""

    del _gateway
    run = await _require_channel_run(db, run_id=run_id, channel=channel, account_id=account_id)
    return await get_agent_run_result(run_id=run_id, current_uid=str(run.uid), db=db)


@agent_invocation_channel_router.post("/runs/{run_id}/cancel")
async def cancel_channel_run(
    run_id: str,
    channel: str = Query(..., max_length=32),
    account_id: str = Query(..., max_length=128),
    _gateway: None = Depends(require_channel_gateway),
    db: AsyncSession = Depends(get_db),
):
    """取消所属 Channel 的 Run。"""

    del _gateway
    run = await _require_channel_run(db, run_id=run_id, channel=channel, account_id=account_id)
    return await cancel_agent_run_view(run_id=run_id, current_uid=str(run.uid), db=db)


async def _approve_latest_run(
    *,
    agent_slug: str,
    thread_id: str,
    request_id: str,
    external_id: str,
    channel: str,
    origin_metadata: dict[str, str],
    current_user: User,
    db: AsyncSession,
) -> dict:
    """审批当前等待中的工具调用，并优先复用同 request_id 的恢复 run。"""
    run_repo = AgentRunRepository(db)
    existing_run = await run_repo.get_run_by_request_id(request_id)
    latest_run = await run_repo.get_latest_chat_or_resume_run(
        uid=str(current_user.uid),
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if existing_run:
        if (
            existing_run.uid != str(current_user.uid)
            or existing_run.agent_slug != agent_slug
            or existing_run.conversation_thread_id != thread_id
            or existing_run.run_type != "resume"
            or not existing_run.created_by_run_id
            or latest_run is None
            or latest_run.id != existing_run.id
        ):
            raise HTTPException(status_code=409, detail="request_id 冲突")
        parent_run_id = existing_run.created_by_run_id
    else:
        if not latest_run or latest_run.status != "interrupted":
            raise HTTPException(
                status_code=409,
                detail={"code": "no_pending_approval", "message": "没有待审批的运行"},
            )
        if latest_run.error_type != "human_approval_required":
            raise HTTPException(
                status_code=409,
                detail={"code": "ask_user_question_unsupported", "message": "当前中断不是工具审批，暂不支持处理"},
            )
        parent_run_id = latest_run.id

    result = await create_agent_run_view(
        input_message=None,
        agent_slug=agent_slug,
        thread_id=thread_id,
        meta={"request_id": request_id, "source": "channel", "channel": channel},
        current_uid=str(current_user.uid),
        db=db,
        resume={"decisions": [{"type": "approve"}]},
        created_by_run_id=parent_run_id,
        source="channel",
        channel=channel,
        external_id=external_id,
        origin_metadata=origin_metadata,
    )
    return {"kind": "command", "command": "approve", "thread_id": thread_id, "run": result}


def _resolve_thread_id(
    *,
    uid: str,
    channel: str,
    account_id: str,
    chat_id: str | None,
    requested_thread_id: str | None,
) -> str:
    """根据显式 thread 或通道会话信息解析稳定 Yuxi Thread ID。"""
    if requested_thread_id and requested_thread_id.strip():
        return requested_thread_id.strip()
    if not chat_id or not chat_id.strip():
        raise HTTPException(status_code=422, detail="thread_id 或 chat_id 至少提供一个")
    return hash_id("channel_", f"{uid}:{channel}:{account_id}:{chat_id.strip()}", length=64)


def _normalize_required(value: str | None, field_name: str) -> str:
    """校验并清理必填字符串字段。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能为空")
    return normalized


def _option_enabled(value: object) -> bool:
    """解析 options 中的布尔开关。"""

    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


async def _require_channel_run(
    db: AsyncSession,
    *,
    run_id: str,
    channel: str,
    account_id: str,
):
    """校验 Run 属于当前 Channel 账号，失败时统一返回不存在。"""

    run = await AgentRunRepository(db).get_run(run_id)
    metadata = run.origin_metadata if run and isinstance(run.origin_metadata, dict) else {}
    if (
        run is None
        or run.source != "channel"
        or run.channel != channel
        or str(metadata.get("account_id") or "") != account_id
    ):
        raise HTTPException(status_code=404, detail="运行任务不存在")
    return run


def _require_no_args(name: str, args: tuple[str, ...]) -> None:
    """拒绝当前不支持参数的 slash command 变体。"""
    if args:
        raise HTTPException(status_code=422, detail=f"/{name} 不接受参数")
