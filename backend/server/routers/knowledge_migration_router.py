"""rag-agent 知识库迁移 dry-run 和显式执行接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from server.utils.auth_middleware import get_admin_user
from yuxi.services.knowledge_migration_service import KnowledgeMigrationError, dry_run_migration, import_manifest
from yuxi.storage.postgres.models_business import User


knowledge_migrations = APIRouter(prefix="/knowledge/migrations/rag-agent", tags=["knowledge-migration"])


class MigrationDryRunRequest(BaseModel):
    source_path: str | None = None
    target_kb_id: str | None = None


class MigrationImportRequest(MigrationDryRunRequest):
    manifest: dict[str, Any] | None = None


@knowledge_migrations.post("/dry-run")
async def migration_dry_run(
    payload: MigrationDryRunRequest,
    current_user: User = Depends(get_admin_user),
):
    del current_user
    return await dry_run_migration(payload.source_path, payload.target_kb_id)


@knowledge_migrations.post("/import", status_code=status.HTTP_202_ACCEPTED)
async def migration_import(
    payload: MigrationImportRequest,
    current_user: User = Depends(get_admin_user),
):
    if not payload.target_kb_id:
        raise HTTPException(status_code=400, detail="必须指定 target_kb_id")
    manifest = payload.manifest
    source_path = payload.source_path or (manifest or {}).get("source")
    preflight = await dry_run_migration(source_path, payload.target_kb_id)
    if not preflight.get("can_import"):
        raise HTTPException(status_code=409, detail=preflight)
    current_manifest = preflight["manifest"]
    if manifest is not None and manifest != current_manifest:
        raise HTTPException(status_code=409, detail="源文件已变化，请重新执行 dry-run 后再导入")
    manifest = current_manifest
    try:
        return await import_manifest(manifest, payload.target_kb_id, current_user.uid)
    except KnowledgeMigrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
