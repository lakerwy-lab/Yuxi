"""钉钉通讯录快照、投影和查询服务。"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.services.dingtalk_auth_service import DingTalkAuthClient, DingTalkAuthError, get_dingtalk_auth_config
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_dingtalk import (
    DingTalkDepartmentSnapshot,
    DingTalkDirectorySyncRun,
    DingTalkUserDepartmentSnapshot,
)
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


class DingTalkDirectoryError(RuntimeError):
    """钉钉通讯录同步失败。"""


_directory_locks: dict[str, asyncio.Lock] = {}


def _local_lock(corp_id: str) -> asyncio.Lock:
    return _directory_locks.setdefault(corp_id, asyncio.Lock())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _format_db_utc(value: datetime | None) -> str | None:
    """将数据库中的 UTC 时间序列化为带时区的 ISO 字符串。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")


def _result_payload(body: dict[str, Any]) -> dict[str, Any]:
    result = body.get("result")
    return result if isinstance(result, dict) else body


def _list_payload(body: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    payload = _result_payload(body)
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(body.get("result"), list):
        return [item for item in body["result"] if isinstance(item, dict)]
    return []


def _next_cursor(body: dict[str, Any]) -> int | None:
    payload = _result_payload(body)
    value = payload.get("next_cursor", payload.get("nextCursor"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_more(body: dict[str, Any]) -> bool:
    payload = _result_payload(body)
    return bool(payload.get("has_more", payload.get("hasMore", False)))


def _normalize_department(item: dict[str, Any], parent_id: str | None, path: str) -> dict[str, Any]:
    dept_id = _text(item.get("dept_id", item.get("deptId", item.get("id"))))
    if not dept_id:
        raise DingTalkDirectoryError("钉钉部门响应缺少 dept_id")
    name = _text(item.get("name", item.get("dept_name", item.get("deptName")))) or dept_id
    return {
        "dept_id": dept_id,
        "parent_dept_id": _text(item.get("parent_id", item.get("parentDeptId"))) or parent_id,
        "dept_name": name,
        "dept_path": f"{path}{dept_id}/",
    }


def _normalize_user(item: dict[str, Any], dept_id: str) -> dict[str, Any] | None:
    union_id = _text(item.get("unionid", item.get("unionId")))
    if not union_id:
        return None
    return {
        "union_id": union_id,
        "user_id": _text(item.get("userid", item.get("userId"))),
        "user_name": _text(item.get("name", item.get("nick"))) or union_id,
        "job_number": _text(item.get("job_number", item.get("jobNumber"))),
        "email": _text(item.get("email")),
        "dept_id": dept_id,
    }


async def fetch_directory_snapshot(
    corp_id: str,
    client: DingTalkAuthClient | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从钉钉完整拉取部门树和成员关系，不修改本地数据库。"""

    del corp_id  # client 的 token 已经绑定企业；保留参数用于调用方和测试的显式边界。
    client = client or DingTalkAuthClient()
    root = {
        "dept_id": "1",
        "parent_dept_id": None,
        "dept_name": "根部门",
        "dept_path": "/1/",
    }
    departments = [root]
    current_layer = [root]
    visited = {"1"}
    department_semaphore = asyncio.Semaphore(2)

    async def load_children(parent: dict[str, Any]) -> list[dict[str, Any]]:
        async with department_semaphore:
            try:
                response = await client.list_sub_departments(parent["dept_id"])
            except DingTalkAuthError as exc:
                raise DingTalkDirectoryError(f"拉取部门 {parent['dept_id']} 失败: {exc}") from exc
        return [
            _normalize_department(item, parent["dept_id"], parent["dept_path"])
            for item in _list_payload(response, "dept_list", "deptList", "departments")
        ]

    while current_layer:
        children_by_parent = await asyncio.gather(*(load_children(parent) for parent in current_layer))
        next_layer: list[dict[str, Any]] = []
        for children in children_by_parent:
            for child in children:
                if child["dept_id"] in visited:
                    continue
                visited.add(child["dept_id"])
                departments.append(child)
                next_layer.append(child)
        current_layer = next_layer

    semaphore = asyncio.Semaphore(3)

    async def load_users(dept_id: str) -> list[dict[str, Any]]:
        async with semaphore:
            cursor = 0
            result: list[dict[str, Any]] = []
            while True:
                try:
                    response = await client.list_department_users(dept_id, cursor=cursor, size=100)
                except DingTalkAuthError as exc:
                    raise DingTalkDirectoryError(f"拉取部门 {dept_id} 成员失败: {exc}") from exc
                result.extend(
                    normalized
                    for item in _list_payload(response, "list", "user_list", "userList")
                    if (normalized := _normalize_user(item, dept_id)) is not None
                )
                next_cursor = _next_cursor(response)
                if not _has_more(response) or next_cursor is None or next_cursor == cursor:
                    return result
                cursor = next_cursor

    loaded = await asyncio.gather(*(load_users(item["dept_id"]) for item in departments))
    relations = {(item["union_id"], item["dept_id"]): item for items in loaded for item in items}
    return departments, list(relations.values())


async def _try_acquire_advisory_lock(db: AsyncSession, corp_id: str) -> bool:
    """使用 PostgreSQL 会话锁跨进程互斥；SQLite 单测回退到进程锁。"""

    try:
        result = await db.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"yuxi:dingtalk-directory:{corp_id}"},
        )
        return bool(result.scalar())
    except Exception:
        await db.rollback()
        return True


async def _release_advisory_lock(db: AsyncSession, corp_id: str) -> None:
    try:
        await db.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"yuxi:dingtalk-directory:{corp_id}"},
        )
    except Exception:
        return


async def _upsert_department_snapshot(
    db: AsyncSession, corp_id: str, item: dict[str, Any], synced_at: datetime
) -> None:
    existing = await db.get(DingTalkDepartmentSnapshot, (corp_id, item["dept_id"]))
    values = {
        "corp_id": corp_id,
        "dept_id": item["dept_id"],
        "parent_dept_id": item.get("parent_dept_id"),
        "dept_name": item["dept_name"],
        "dept_path": item.get("dept_path"),
        "active": True,
        "synced_at": synced_at,
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
    else:
        db.add(DingTalkDepartmentSnapshot(**values))


async def _upsert_user_department_snapshot(
    db: AsyncSession, corp_id: str, item: dict[str, Any], synced_at: datetime
) -> None:
    key = (corp_id, item["union_id"], item["dept_id"])
    existing = await db.get(DingTalkUserDepartmentSnapshot, key)
    values = {
        "corp_id": corp_id,
        "union_id": item["union_id"],
        "dept_id": item["dept_id"],
        "user_id": item.get("user_id"),
        "user_name": item["user_name"],
        "job_number": item.get("job_number"),
        "email": item.get("email"),
        "active": True,
        "synced_at": synced_at,
    }
    if existing:
        for key_name, value in values.items():
            setattr(existing, key_name, value)
    else:
        db.add(DingTalkUserDepartmentSnapshot(**values))


async def _get_or_create_projection_department(
    db: AsyncSession,
    corp_id: str,
    dept: DingTalkDepartmentSnapshot,
) -> Department:
    result = await db.execute(
        select(Department).where(
            Department.dingtalk_corp_id == corp_id,
            Department.dingtalk_dept_id == dept.dept_id,
        )
    )
    local_department = result.scalar_one_or_none()
    if local_department:
        local_department.name = await _available_department_name(
            db,
            dept.dept_name,
            dept.dept_id,
            exclude_department_id=local_department.id,
        )
        local_department.description = f"钉钉部门：{dept.dept_name}（{dept.dept_path or dept.dept_id}）"
        local_department.dingtalk_parent_dept_id = dept.parent_dept_id
        local_department.dingtalk_dept_path = dept.dept_path
        local_department.dingtalk_active = True
        return local_department
    local_department = Department(
        name=await _available_department_name(db, dept.dept_name, dept.dept_id),
        description=f"钉钉部门：{dept.dept_name}（{dept.dept_path or dept.dept_id}）",
        dingtalk_corp_id=corp_id,
        dingtalk_dept_id=dept.dept_id,
        dingtalk_parent_dept_id=dept.parent_dept_id,
        dingtalk_dept_path=dept.dept_path,
        dingtalk_active=True,
    )
    db.add(local_department)
    await db.flush()
    return local_department


async def _available_department_name(
    db: AsyncSession,
    preferred_name: str,
    dept_id: str,
    *,
    exclude_department_id: int | None = None,
) -> str:
    """保留真实部门名；仅在全局重名时附加钉钉部门 ID。"""

    base = preferred_name.strip()[:50] or dept_id
    suffix = f"（钉钉 {dept_id}）"
    candidates = [base, f"{base[: max(1, 50 - len(suffix))]}{suffix}"]
    for candidate in candidates:
        query = select(Department.id).where(Department.name == candidate)
        if exclude_department_id is not None:
            query = query.where(Department.id != exclude_department_id)
        if (await db.execute(query.limit(1))).scalar_one_or_none() is None:
            return candidate
    digest = hashlib.sha1(dept_id.encode()).hexdigest()[:8]
    return f"{base[:39]}-{digest}"


async def _project_users(db: AsyncSession, corp_id: str) -> int:
    result = await db.execute(
        select(DingTalkUserDepartmentSnapshot)
        .where(
            DingTalkUserDepartmentSnapshot.corp_id == corp_id,
            DingTalkUserDepartmentSnapshot.active.is_(True),
        )
        .order_by(DingTalkUserDepartmentSnapshot.union_id, DingTalkUserDepartmentSnapshot.dept_id)
    )
    relations = result.scalars().all()
    by_union: dict[str, list[DingTalkUserDepartmentSnapshot]] = defaultdict(list)
    for relation in relations:
        by_union[relation.union_id].append(relation)

    dept_result = await db.execute(
        select(DingTalkDepartmentSnapshot).where(
            DingTalkDepartmentSnapshot.corp_id == corp_id,
            DingTalkDepartmentSnapshot.active.is_(True),
        )
    )
    departments = {item.dept_id: item for item in dept_result.scalars().all()}
    existing_result = await db.execute(select(Department).where(Department.dingtalk_corp_id == corp_id))
    for local_department in existing_result.scalars().all():
        local_department.dingtalk_active = False

    local_departments: dict[str, Department] = {}
    for department in sorted(departments.values(), key=lambda item: item.dept_path or item.dept_id):
        local_departments[department.dept_id] = await _get_or_create_projection_department(db, corp_id, department)

    changed = 0
    for union_id, items in by_union.items():
        relation = max(
            items,
            key=lambda item: len((departments.get(item.dept_id).dept_path or "/").strip("/").split("/"))
            if departments.get(item.dept_id)
            else 0,
        )
        result = await db.execute(
            select(User).where(
                User.dingtalk_corp_id == corp_id,
                User.dingtalk_union_id == union_id,
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            result = await db.execute(select(User).where(User.uid == f"dingtalk:{corp_id}:{union_id}"))
            user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=await _available_username(db, relation.user_name, union_id),
                uid=f"dingtalk:{corp_id}:{union_id}",
                password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
                role="user",
                department_id=local_departments[relation.dept_id].id,
            )
            db.add(user)
            await db.flush()
        elif user.dingtalk_corp_id not in (None, corp_id):
            logger.warning("skip cross-corp DingTalk projection for user id=%s", user.id)
            continue
        user.dingtalk_corp_id = corp_id
        user.dingtalk_union_id = union_id
        user.dingtalk_user_id = relation.user_id
        if user.uid.startswith(f"dingtalk:{corp_id}:"):
            user.username = await _available_username(db, relation.user_name, union_id, exclude_user_id=user.id)
        user.is_deleted = 0
        user.deleted_at = None
        if relation.dept_id in local_departments:
            user.department_id = local_departments[relation.dept_id].id
        changed += 1

    active_unions = set(by_union)
    result = await db.execute(select(User).where(User.dingtalk_corp_id == corp_id, User.is_deleted == 0))
    for user in result.scalars().all():
        if user.dingtalk_union_id not in active_unions and user.uid.startswith(f"dingtalk:{corp_id}:"):
            user.is_deleted = 1
            user.deleted_at = utc_now_naive()
            changed += 1
    return changed


async def _available_username(
    db: AsyncSession,
    preferred_name: str | None,
    union_id: str,
    *,
    exclude_user_id: int | None = None,
) -> str:
    """为同步创建的账号生成稳定且可读的唯一用户名。"""

    base = (preferred_name or "钉钉用户").strip()[:20] or "钉钉用户"
    candidates = [base, f"{base}-{union_id[-6:]}"]
    for candidate in candidates:
        query = select(User.id).where(User.username == candidate)
        if exclude_user_id is not None:
            query = query.where(User.id != exclude_user_id)
        if (await db.execute(query.limit(1))).scalar_one_or_none() is None:
            return candidate
    return f"钉钉用户-{hashlib.sha1(union_id.encode()).hexdigest()[:10]}"


async def sync_directory(
    db: AsyncSession,
    corp_id: str | None = None,
    *,
    client: DingTalkAuthClient | None = None,
    run: DingTalkDirectorySyncRun | None = None,
) -> DingTalkDirectorySyncRun:
    """完成一次全量快照同步，再将变化投影到 Yuxi 用户主表。"""

    corp_id = (corp_id or get_dingtalk_auth_config().corp_id).strip()
    if not corp_id:
        raise DingTalkDirectoryError("未配置 DINGTALK_CORP_ID")
    async with _local_lock(corp_id):
        if not await _try_acquire_advisory_lock(db, corp_id):
            raise DingTalkDirectoryError("该企业的通讯录同步正在执行")
        try:
            if run is None:
                run = DingTalkDirectorySyncRun(corp_id=corp_id, status="running")
                db.add(run)
                await db.commit()
                await db.refresh(run)
            run.status = "running"
            await db.commit()
            try:
                departments, users = await fetch_directory_snapshot(corp_id, client)
                synced_at = utc_now_naive()
                await db.execute(
                    text("UPDATE dingtalk_departments SET active = false WHERE corp_id = :corp_id"),
                    {"corp_id": corp_id},
                )
                await db.execute(
                    text("UPDATE dingtalk_user_departments SET active = false WHERE corp_id = :corp_id"),
                    {"corp_id": corp_id},
                )
                for item in departments:
                    await _upsert_department_snapshot(db, corp_id, item, synced_at)
                for item in users:
                    await _upsert_user_department_snapshot(db, corp_id, item, synced_at)
                await db.flush()
                changed_user_count = await _project_users(db, corp_id)
                run.status = "completed"
                run.department_count = len(departments)
                run.user_count = len({item["union_id"] for item in users})
                run.changed_user_count = changed_user_count
                run.completed_at = utc_now_naive()
                run.error_message = None
                await db.commit()
                return run
            except Exception as exc:
                await db.rollback()
                run.status = "failed"
                run.completed_at = utc_now_naive()
                run.error_message = str(exc)[:2000]
                db.add(run)
                await db.commit()
                raise
        finally:
            await _release_advisory_lock(db, corp_id)


async def create_sync_run(db: AsyncSession, corp_id: str) -> DingTalkDirectorySyncRun:
    """创建可由 worker 执行的同步记录。"""
    run = DingTalkDirectorySyncRun(corp_id=corp_id, status="queued")
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def list_directory_departments(
    db: AsyncSession, corp_id: str, keyword: str | None = None
) -> list[dict[str, Any]]:
    query = select(DingTalkDepartmentSnapshot).where(
        DingTalkDepartmentSnapshot.corp_id == corp_id,
        DingTalkDepartmentSnapshot.active.is_(True),
    )
    if keyword:
        query = query.where(DingTalkDepartmentSnapshot.dept_name.ilike(f"%{keyword.strip()}%"))
    result = await db.execute(query.order_by(DingTalkDepartmentSnapshot.dept_path))
    return [
        {
            "corp_id": item.corp_id,
            "dept_id": item.dept_id,
            "parent_dept_id": item.parent_dept_id,
            "dept_name": item.dept_name,
            "dept_path": item.dept_path,
        }
        for item in result.scalars().all()
    ]


async def list_directory_users(
    db: AsyncSession,
    corp_id: str,
    *,
    keyword: str | None = None,
    dept_id: str | None = None,
    include_children: bool = False,
) -> list[dict[str, Any]]:
    query = (
        select(DingTalkUserDepartmentSnapshot, DingTalkDepartmentSnapshot)
        .join(
            DingTalkDepartmentSnapshot,
            (DingTalkDepartmentSnapshot.corp_id == DingTalkUserDepartmentSnapshot.corp_id)
            & (DingTalkDepartmentSnapshot.dept_id == DingTalkUserDepartmentSnapshot.dept_id),
        )
        .where(
            DingTalkUserDepartmentSnapshot.corp_id == corp_id,
            DingTalkUserDepartmentSnapshot.active.is_(True),
        )
    )
    if dept_id:
        if include_children:
            dept = await db.get(DingTalkDepartmentSnapshot, (corp_id, dept_id))
            if not dept or not dept.dept_path:
                return []
            query = query.where(DingTalkDepartmentSnapshot.dept_path.like(f"{dept.dept_path}%"))
        else:
            query = query.where(DingTalkUserDepartmentSnapshot.dept_id == dept_id)
    if keyword:
        needle = f"%{keyword.strip()}%"
        query = query.where(
            DingTalkUserDepartmentSnapshot.user_name.ilike(needle)
            | DingTalkUserDepartmentSnapshot.union_id.ilike(needle)
        )
    result = await db.execute(query.order_by(DingTalkUserDepartmentSnapshot.user_name))
    return [
        {
            "corp_id": relation.corp_id,
            "union_id": relation.union_id,
            "user_id": relation.user_id,
            "user_name": relation.user_name,
            "job_number": relation.job_number,
            "email": relation.email,
            "dept_id": relation.dept_id,
            "dept_name": dept.dept_name,
            "dept_path": dept.dept_path,
        }
        for relation, dept in result.all()
    ]


async def get_sync_status(db: AsyncSession, corp_id: str, run_id: int | None = None) -> dict[str, Any] | None:
    query = select(DingTalkDirectorySyncRun).where(DingTalkDirectorySyncRun.corp_id == corp_id)
    if run_id is not None:
        query = query.where(DingTalkDirectorySyncRun.id == run_id)
    else:
        query = query.order_by(DingTalkDirectorySyncRun.id.desc()).limit(1)
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    if not run:
        return None
    return {
        "id": run.id,
        "corp_id": run.corp_id,
        "sync_type": run.sync_type,
        "status": run.status,
        "department_count": run.department_count,
        "user_count": run.user_count,
        "changed_user_count": run.changed_user_count,
        "started_at": _format_db_utc(run.started_at),
        "completed_at": _format_db_utc(run.completed_at),
        "error_message": run.error_message,
    }


async def run_directory_sync_job(ctx: dict[str, Any], run_id: int, corp_id: str):
    """ARQ worker 入口。"""
    del ctx
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        run = await db.get(DingTalkDirectorySyncRun, run_id)
        if run is None:
            raise DingTalkDirectoryError(f"同步记录不存在: {run_id}")
        await sync_directory(db, corp_id, run=run)


async def recover_stale_directory_sync_runs(ctx: dict[str, Any] | None = None) -> int:
    """将超过半小时仍未结束的通讯录任务标记为失败，保留旧快照。"""
    del ctx
    from datetime import timedelta

    from yuxi.storage.postgres.manager import pg_manager

    cutoff = utc_now_naive() - timedelta(minutes=30)
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            select(DingTalkDirectorySyncRun).where(
                DingTalkDirectorySyncRun.status.in_(["queued", "running"]),
                DingTalkDirectorySyncRun.started_at < cutoff,
            )
        )
        runs = result.scalars().all()
        for run in runs:
            run.status = "failed"
            run.completed_at = utc_now_naive()
            run.error_message = "同步任务超过 30 分钟未完成，已回收；上一份有效快照保留"
        if runs:
            await db.commit()
    return len(runs)


async def enqueue_due_directory_sync(ctx: dict[str, Any] | None = None) -> int:
    """按配置周期提交通讯录同步；实际拉取仍由独立 ARQ 任务执行。"""

    from yuxi.services.run_queue_service import get_arq_pool
    from yuxi.storage.postgres.manager import pg_manager

    config = get_dingtalk_auth_config()
    if (
        config.directory_sync_interval_seconds <= 0
        or not config.corp_id
        or not config.client_id
        or not config.client_secret
    ):
        return 0

    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            select(DingTalkDirectorySyncRun)
            .where(DingTalkDirectorySyncRun.corp_id == config.corp_id)
            .order_by(DingTalkDirectorySyncRun.id.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        if latest and latest.status in {"queued", "running"}:
            return 0
        last_time = (latest.completed_at or latest.started_at) if latest else None
        if last_time and utc_now_naive() - last_time < timedelta(seconds=config.directory_sync_interval_seconds):
            return 0
        run = await create_sync_run(db, config.corp_id)

    queue = (ctx or {}).get("redis") or await get_arq_pool()
    job = await queue.enqueue_job(
        "run_directory_sync_job",
        run.id,
        config.corp_id,
        _job_id=f"dingtalk-directory:{config.corp_id}:{run.id}",
    )
    if job is not None:
        return 1

    async with pg_manager.get_async_session_context() as db:
        stale_run = await db.get(DingTalkDirectorySyncRun, run.id)
        if stale_run:
            stale_run.status = "failed"
            stale_run.completed_at = utc_now_naive()
            stale_run.error_message = "同步任务未进入队列"
            await db.commit()
    return 0
