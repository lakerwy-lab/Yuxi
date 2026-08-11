"""知识库表单问答对管理、匹配和转人工接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from server.utils.knowledge_permissions import ensure_knowledge_base_permission
from yuxi.permissions import ResourcePermission
from yuxi.services.qa_pair_service import (
    create_escalation,
    create_qa_pair,
    disable_qa_pair,
    find_exact_answer,
    serialize_qa_pair,
    update_qa_pair,
)
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_qa import QAEscalation, QAPair, QAPairIndexJob
from yuxi.utils.logging_config import logger


qa_pairs = APIRouter(prefix="/qa-pairs", tags=["qa-pairs"])


class QAPairRequest(BaseModel):
    kb_id: str = Field(min_length=1, max_length=80)
    standard_question: str = Field(min_length=1, validation_alias=AliasChoices("standard_question", "question"))
    answer_markdown: str = Field(min_length=1, validation_alias=AliasChoices("answer_markdown", "answer"))
    aliases: list[str] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list)
    image_refs: list[str] = Field(default_factory=list)


class QAPairUpdateRequest(BaseModel):
    standard_question: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("standard_question", "question"),
    )
    answer_markdown: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("answer_markdown", "answer"),
    )
    aliases: list[str] | None = Field(default=None, max_length=50)
    tags: list[str] | None = None
    image_refs: list[str] | None = None


class EscalationRequest(BaseModel):
    question: str = Field(min_length=1)
    thread_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


async def _require_kb(kb_id: str, current_user: User, permission: ResourcePermission) -> None:
    await ensure_knowledge_base_permission(kb_id, current_user, permission)


async def _dispatch_index_job(job: QAPairIndexJob) -> None:
    try:
        queue = await get_arq_pool()
        await queue.enqueue_job(
            "process_qa_pair_index_job",
            job.id,
            job.qa_pair_id,
            _job_id=f"qa-index:{job.id}",
        )
    except Exception:
        logger.exception("failed to enqueue QA pair index job %s; compensation cron will retry", job.id)


async def _latest_job(db: AsyncSession, qa_pair_id: int) -> QAPairIndexJob | None:
    result = await db.execute(
        select(QAPairIndexJob)
        .where(QAPairIndexJob.qa_pair_id == qa_pair_id)
        .order_by(QAPairIndexJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _operator_names(db: AsyncSession, items: list[QAPair]) -> dict[str, str]:
    uids = {str(item.updated_by) for item in items if item.updated_by}
    if not uids:
        return {}
    result = await db.execute(select(User.uid, User.username).where(User.uid.in_(uids)))
    return {str(uid): str(username) for uid, username in result.all()}


@qa_pairs.get("")
async def list_qa_pairs(
    kb_id: str = Query(min_length=1, max_length=80),
    query: str | None = None,
    qa_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    await _require_kb(kb_id, current_user, ResourcePermission.MANAGE)
    filters = [QAPair.kb_id == kb_id, QAPair.deleted_at.is_(None)]
    if query and query.strip():
        filters.append(QAPair.question.ilike(f"%{query.strip()}%"))
    if qa_status == "published":
        filters.extend([QAPair.published.is_(True), QAPair.enabled.is_(True), QAPair.index_status == "synced"])
    elif qa_status == "disabled":
        filters.append(QAPair.enabled.is_(False))
    elif qa_status == "pending":
        filters.extend([QAPair.enabled.is_(True), QAPair.index_status.in_(["pending", "queued", "running"])])
    elif qa_status == "failed":
        filters.append(QAPair.index_status == "failed")

    total = int(await db.scalar(select(func.count(QAPair.id)).where(*filters)) or 0)
    result = await db.execute(
        select(QAPair)
        .where(*filters)
        .order_by(QAPair.updated_at.desc(), QAPair.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(result.scalars().all())
    names = await _operator_names(db, items)
    return {
        "items": [serialize_qa_pair(item, updated_by_name=names.get(str(item.updated_by))) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@qa_pairs.post("", status_code=status.HTTP_201_CREATED)
async def create_qa_pair_endpoint(
    payload: QAPairRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    await _require_kb(payload.kb_id, current_user, ResourcePermission.MANAGE)
    item = await create_qa_pair(db, **payload.model_dump(), created_by=current_user.uid)
    if job := await _latest_job(db, item.id):
        await _dispatch_index_job(job)
    return serialize_qa_pair(item, updated_by_name=current_user.username)


@qa_pairs.put("/{qa_pair_id}")
async def update_qa_pair_endpoint(
    qa_pair_id: int,
    payload: QAPairUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    item = await db.get(QAPair, qa_pair_id)
    if item is None or item.deleted_at:
        raise HTTPException(status_code=404, detail="问答对不存在")
    await _require_kb(item.kb_id, current_user, ResourcePermission.MANAGE)
    values = {key: value for key, value in payload.model_dump().items() if value is not None}
    item = await update_qa_pair(db, item, values, current_user.uid)
    if job := await _latest_job(db, item.id):
        await _dispatch_index_job(job)
    return serialize_qa_pair(item, updated_by_name=current_user.username)


@qa_pairs.post("/{qa_pair_id}/disable")
async def disable_qa_pair_endpoint(
    qa_pair_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    item = await db.get(QAPair, qa_pair_id)
    if item is None or item.deleted_at:
        raise HTTPException(status_code=404, detail="问答对不存在")
    await _require_kb(item.kb_id, current_user, ResourcePermission.MANAGE)
    item = await disable_qa_pair(db, item, current_user.uid)
    return serialize_qa_pair(item, updated_by_name=current_user.username)


@qa_pairs.delete("/{qa_pair_id}")
async def delete_qa_pair_endpoint(
    qa_pair_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    item = await db.get(QAPair, qa_pair_id)
    if item is None or item.deleted_at:
        raise HTTPException(status_code=404, detail="问答对不存在")
    await _require_kb(item.kb_id, current_user, ResourcePermission.MANAGE)
    await disable_qa_pair(db, item, current_user.uid, deleted=True)
    return {"id": qa_pair_id, "deleted": True}


@qa_pairs.post("/match")
async def match_qa_pair_endpoint(
    query: str = Query(min_length=1),
    kb_id: list[str] = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    for item in kb_id:
        await _require_kb(item, current_user, ResourcePermission.READ)
    return await find_exact_answer(db, kb_id, query) or {"matched": False}


@qa_pairs.post("/escalate", status_code=status.HTTP_201_CREATED)
async def escalate_qa_question(
    payload: EscalationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    item = await create_escalation(
        db,
        uid=current_user.uid,
        question=payload.question,
        thread_id=payload.thread_id,
        context=payload.context,
    )
    return {
        "id": item.id,
        "status": item.status,
        "message": "已记录转人工请求" if item.status != "failed" else "转人工通知发送失败，已保留待重试记录",
    }


@qa_pairs.get("/statistics")
async def qa_statistics(
    kb_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    filters = [QAPair.deleted_at.is_(None)]
    if kb_id:
        await _require_kb(kb_id, current_user, ResourcePermission.MANAGE)
        filters.append(QAPair.kb_id == kb_id)
    total = int(await db.scalar(select(func.count(QAPair.id)).where(*filters)) or 0)
    published = int(
        await db.scalar(
            select(func.count(QAPair.id)).where(
                *filters,
                QAPair.published.is_(True),
                QAPair.enabled.is_(True),
            )
        )
        or 0
    )
    synced = int(await db.scalar(select(func.count(QAPair.id)).where(*filters, QAPair.index_status == "synced")) or 0)
    escalated = int(await db.scalar(select(func.count(QAEscalation.id))) or 0)
    failed = int(await db.scalar(select(func.count(QAEscalation.id)).where(QAEscalation.status == "failed")) or 0)
    return {
        "total": total,
        "published": published,
        "index_ready": synced,
        "escalations": escalated,
        "escalation_failures": failed,
    }
