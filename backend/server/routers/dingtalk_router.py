"""钉钉通讯录、身份绑定和会议预订管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user, get_superadmin_user
from yuxi.services.dingtalk_auth_service import get_dingtalk_auth_config
from yuxi.services.dingtalk_directory_service import (
    create_sync_run,
    get_sync_status,
    list_directory_departments,
    list_directory_users,
)
from yuxi.services.dingtalk_meeting_service import DingTalkMeetingError, MeetingRoomService
from yuxi.services.operation_log_service import log_operation
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


dingtalk = APIRouter(prefix="/dingtalk", tags=["dingtalk"])


class DirectorySyncRequest(BaseModel):
    corp_id: str | None = Field(default=None, min_length=1)


class DingTalkIdentityBinding(BaseModel):
    corp_id: str = Field(min_length=1, max_length=128)
    union_id: str = Field(min_length=1, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)


class MeetingSearchRequest(BaseModel):
    start_time: str | None = None
    end_time: str | None = None


class MeetingPreviewRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=128)
    room_name: str = ""
    title: str = Field(min_length=1, max_length=255)
    start_time: str
    end_time: str
    description: str | None = None


class MeetingConfirmRequest(BaseModel):
    confirm_token: str = Field(min_length=1, max_length=128)


@dingtalk.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def start_directory_sync(
    payload: DirectorySyncRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """提交一次通讯录全量快照同步，实际抓取由 worker 执行。"""
    config = get_dingtalk_auth_config()
    corp_id = config.corp_id.strip()
    if payload.corp_id and payload.corp_id.strip() != corp_id:
        raise HTTPException(status_code=400, detail="请求企业与服务端钉钉配置不一致")
    if not corp_id or not config.client_id or not config.client_secret:
        raise HTTPException(status_code=503, detail="钉钉通讯录配置不完整")
    latest = await get_sync_status(db, corp_id)
    if latest and latest["status"] in {"queued", "running"}:
        return {"run_id": latest["id"], "corp_id": corp_id, "status": latest["status"]}
    run = await create_sync_run(db, corp_id)
    try:
        queue = await get_arq_pool()
        job = await queue.enqueue_job(
            "run_directory_sync_job",
            run.id,
            corp_id,
            _job_id=f"dingtalk-directory:{corp_id}:{run.id}",
        )
        if job is None:
            raise RuntimeError("同步任务未进入队列")
    except Exception as exc:
        run.status = "failed"
        run.error_message = f"无法提交同步任务: {exc}"
        await db.commit()
        logger.exception("failed to enqueue DingTalk directory sync")
        raise HTTPException(status_code=503, detail="同步任务队列不可用") from exc
    return {"run_id": run.id, "corp_id": corp_id, "status": "queued"}


@dingtalk.get("/sync-status")
async def directory_sync_status(
    corp_id: str | None = Query(default=None),
    run_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    configured_corp_id = get_dingtalk_auth_config().corp_id.strip()
    if corp_id and corp_id.strip() != configured_corp_id:
        raise HTTPException(status_code=400, detail="请求企业与服务端钉钉配置不一致")
    corp_id = configured_corp_id
    if not corp_id:
        raise HTTPException(status_code=503, detail="未配置 DINGTALK_CORP_ID")
    result = await get_sync_status(db, corp_id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="同步记录不存在")
    return result


@dingtalk.get("/departments")
async def directory_departments(
    corp_id: str | None = None,
    keyword: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    configured_corp_id = get_dingtalk_auth_config().corp_id.strip()
    if corp_id and corp_id.strip() != configured_corp_id:
        raise HTTPException(status_code=400, detail="请求企业与服务端钉钉配置不一致")
    return {"items": await list_directory_departments(db, configured_corp_id, keyword)}


@dingtalk.get("/users")
async def directory_users(
    corp_id: str | None = None,
    keyword: str | None = None,
    dept_id: str | None = None,
    include_children: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    configured_corp_id = get_dingtalk_auth_config().corp_id.strip()
    if corp_id and corp_id.strip() != configured_corp_id:
        raise HTTPException(status_code=400, detail="请求企业与服务端钉钉配置不一致")
    return {
        "items": await list_directory_users(
            db,
            configured_corp_id,
            keyword=keyword,
            dept_id=dept_id,
            include_children=include_children,
        )
    }


@dingtalk.get("/sync-config")
async def directory_sync_config(
    current_user: User = Depends(get_admin_user),
):
    """返回管理页所需的非敏感同步配置状态。"""

    del current_user
    config = get_dingtalk_auth_config()
    return {
        "configured": bool(config.corp_id and config.client_id and config.client_secret),
        "interval_seconds": config.directory_sync_interval_seconds,
    }


@dingtalk.post("/users/{user_id}/bind")
async def bind_dingtalk_identity(
    user_id: int,
    payload: DingTalkIdentityBinding,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    result = await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    conflict_query = select(User).where(
        User.id != user_id,
        User.dingtalk_corp_id == payload.corp_id,
        User.dingtalk_union_id == payload.union_id,
    )
    if payload.user_id:
        conflict_query = select(User).where(
            User.id != user_id,
            User.dingtalk_corp_id == payload.corp_id,
            (User.dingtalk_union_id == payload.union_id) | (User.dingtalk_user_id == payload.user_id),
        )
    if (await db.execute(conflict_query)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="钉钉身份已绑定其他用户")
    user.dingtalk_corp_id = payload.corp_id
    user.dingtalk_union_id = payload.union_id
    user.dingtalk_user_id = payload.user_id
    await db.commit()
    await log_operation(
        db, current_user.id, "绑定钉钉身份", f"用户 {user.uid} 绑定 {payload.corp_id}/{payload.union_id}", request
    )
    return user.to_dict()


@dingtalk.post("/users/{user_id}/unbind")
async def unbind_dingtalk_identity(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    result = await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.dingtalk_corp_id = None
    user.dingtalk_union_id = None
    user.dingtalk_user_id = None
    await db.commit()
    await log_operation(db, current_user.id, "解绑钉钉身份", f"用户 {user.uid}", request)
    return user.to_dict()


def _meeting_union_id(user: User) -> str:
    union_id = str(user.dingtalk_union_id or "").strip()
    if not union_id:
        raise HTTPException(status_code=400, detail="当前账号未绑定钉钉 unionId")
    return union_id


def _meeting_error(exc: DingTalkMeetingError) -> HTTPException:
    status_code = 409 if exc.code in {"ROOM_ALREADY_BOOKED", "CONFIRM_TOKEN_USED"} else 400
    if exc.code == "NOT_CONFIGURED":
        status_code = 503
    return HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)})


@dingtalk.post("/bookings/rooms")
async def search_meeting_rooms(
    payload: MeetingSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    try:
        rooms = await MeetingRoomService(db).search_rooms(
            _meeting_union_id(current_user), payload.start_time, payload.end_time
        )
        return {"items": rooms}
    except DingTalkMeetingError as exc:
        raise _meeting_error(exc) from exc


@dingtalk.post("/bookings/preview")
async def preview_meeting_booking(
    payload: MeetingPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    try:
        return await MeetingRoomService(db).preview_booking(
            uid=current_user.uid,
            union_id=_meeting_union_id(current_user),
            **payload.model_dump(),
        )
    except DingTalkMeetingError as exc:
        raise _meeting_error(exc) from exc


@dingtalk.post("/bookings/confirm")
async def confirm_meeting_booking(
    payload: MeetingConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    try:
        return await MeetingRoomService(db).confirm_booking(current_user.uid, payload.confirm_token)
    except DingTalkMeetingError as exc:
        raise _meeting_error(exc) from exc


@dingtalk.get("/bookings")
async def list_meeting_bookings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    return {"items": await MeetingRoomService(db).list_bookings(current_user.uid)}


@dingtalk.post("/bookings/{booking_id}/cancel")
async def cancel_meeting_booking(
    booking_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    try:
        return await MeetingRoomService(db).cancel_booking(current_user.uid, booking_id)
    except DingTalkMeetingError as exc:
        raise _meeting_error(exc) from exc
