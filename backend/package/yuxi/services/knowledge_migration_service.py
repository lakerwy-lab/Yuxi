"""rag-agent 到 Yuxi 的只读预检、manifest 和可重跑导入流程。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from yuxi.knowledge.runtime import knowledge_base
from yuxi.storage.minio import get_minio_client


SUPPORTED_EXTENSIONS = {".md", ".txt", ".html", ".htm", ".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".csv"}


class KnowledgeMigrationError(RuntimeError):
    """迁移预检或执行失败。"""


def _configured_root() -> Path | None:
    value = os.getenv("RAG_AGENT_MIGRATION_ROOT", "").strip()
    return Path(value).expanduser().resolve() if value else None


def _resolve_source(source_path: str | None) -> Path:
    root = _configured_root()
    if root is None:
        raise KnowledgeMigrationError(
            "未配置 RAG_AGENT_MIGRATION_ROOT；为避免误读生产目录，当前仅允许显式配置迁移根目录"
        )
    candidate = Path(source_path or root).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise KnowledgeMigrationError("source_path 必须位于 RAG_AGENT_MIGRATION_ROOT 下") from exc
    if not candidate.exists():
        raise KnowledgeMigrationError(f"迁移源不存在: {candidate}")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(source_path: str | None) -> dict[str, Any]:
    """只读扫描迁移源并返回可审计 manifest，不连接源库也不写目标库。"""
    source = _resolve_source(source_path)
    if source.is_file() and source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise KnowledgeMigrationError("JSON 快照必须是对象")
        files = payload.get("files") or []
        if not isinstance(files, list):
            raise KnowledgeMigrationError("JSON 快照 files 必须是数组")
        return {
            "source": str(source),
            "source_type": "json_snapshot",
            "read_only": True,
            "knowledge_bases": payload.get("knowledge_bases") or [],
            "embedding_model_spec": payload.get("embedding_model_spec"),
            "embedding_dimension": payload.get("embedding_dimension"),
            "metric_type": payload.get("metric_type"),
            "acl": payload.get("acl") or [],
            "resources": payload.get("resources") or [],
            "files": files,
            "file_count": len(files),
            "total_bytes": sum(int(item.get("size") or 0) for item in files if isinstance(item, dict)),
            "chunk_count": int(payload.get("chunk_count") or 0),
            "resource_count": len(payload.get("resources") or []),
        }

    if not source.is_dir():
        raise KnowledgeMigrationError("迁移源必须是目录或 JSON 快照")
    files: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        relative = path.relative_to(source).as_posix()
        files.append(
            {
                "relative_path": relative,
                "filename": path.name,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
                "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "source_path": str(path),
            }
        )
    return {
        "source": str(source),
        "source_type": "directory",
        "read_only": True,
        "knowledge_bases": [],
        "embedding_model_spec": None,
        "embedding_dimension": None,
        "metric_type": None,
        "acl": [],
        "resources": [],
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "chunk_count": 0,
        "resource_count": 0,
    }


async def dry_run_migration(source_path: str | None, target_kb_id: str | None) -> dict[str, Any]:
    """生成 manifest 并执行目标 KB、embedding 和文件来源预检。"""
    try:
        manifest = build_manifest(source_path)
    except (OSError, ValueError, json.JSONDecodeError, KnowledgeMigrationError) as exc:
        return {"status": "blocked", "can_import": False, "reasons": [str(exc)], "manifest": None}

    reasons: list[str] = []
    if not target_kb_id:
        reasons.append("必须指定 target_kb_id")
    target_config = None
    if target_kb_id:
        try:
            target_config = await knowledge_base.get_kb_config(target_kb_id)
        except Exception as exc:
            reasons.append(f"目标知识库不可用: {exc}")
    source_embedding = manifest.get("embedding_model_spec")
    target_embedding = getattr(target_config, "embedding_model_spec", None) if target_config else None
    if source_embedding and target_embedding and source_embedding != target_embedding:
        reasons.append("源 embedding_model_spec 与目标不一致，禁止直接写入向量库")
    if manifest["source_type"] == "json_snapshot":
        if any(not item.get("source_path") for item in manifest.get("files", []) if isinstance(item, dict)):
            reasons.append("JSON 快照缺少可读取的 source_path；当前只能 dry-run")
    if manifest["file_count"] == 0:
        reasons.append("没有发现可导入的支持格式文件")
    target_dimension = None
    if target_embedding:
        try:
            from yuxi.models.providers.cache import model_cache

            model_info = model_cache.get_model_info(target_embedding)
            target_dimension = getattr(model_info, "dimension", None) if model_info else None
            if model_info is None:
                reasons.append(f"目标 embedding 模型不可用: {target_embedding}")
        except Exception as exc:
            reasons.append(f"目标 embedding 模型预检失败: {exc}")
    return {
        "status": "ready" if not reasons else "blocked",
        "can_import": not reasons,
        "reasons": reasons,
        "target_kb_id": target_kb_id,
        "manifest": manifest,
        "preflight": {
            "source_read_only": True,
            "source_embedding_dimension": manifest.get("embedding_dimension"),
            "target_embedding_model_spec": target_embedding,
            "target_embedding_dimension": target_dimension,
            "target_metric_type": "COSINE",
            "chunk_count": manifest.get("chunk_count", 0),
            "resource_count": manifest.get("resource_count", 0),
            "acl_count": len(manifest.get("acl") or []),
            "estimated_source_bytes": manifest.get("total_bytes", 0),
            "vector_write_path": "Yuxi knowledge manager -> parser -> Milvus",
        },
    }


async def import_manifest(
    manifest: dict[str, Any],
    target_kb_id: str,
    operator_id: str,
) -> dict[str, Any]:
    """按 manifest 逐文件上传并走 Yuxi 现有解析/索引链路，重复文件可重跑。"""
    if manifest.get("source_type") != "directory":
        raise KnowledgeMigrationError("只有带本地 source_path 的目录 manifest 才能执行导入")
    source_root = _resolve_source(str(manifest.get("source") or ""))
    if not source_root.is_dir():
        raise KnowledgeMigrationError("manifest source 必须是已配置迁移根目录下的目录")
    minio = get_minio_client()
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for entry in manifest.get("files", []):
        source_path = Path(str(entry.get("source_path") or "")).resolve()
        try:
            source_path.relative_to(source_root)
        except ValueError as exc:
            raise KnowledgeMigrationError("manifest 中的 source_path 超出 manifest source 目录") from exc
        if not source_path.is_file():
            raise KnowledgeMigrationError(f"manifest 文件不存在: {source_path}")
        content_hash = str(entry.get("sha256") or _sha256(source_path))
        object_name = f"rag-agent-migration/{target_kb_id}/{content_hash}/{source_path.name}"
        existing = await knowledge_base.file_existed_in_db(target_kb_id, content_hash)
        if existing:
            skipped.append({"filename": source_path.name, "content_hash": content_hash})
            continue
        upload = await minio.aupload_file(
            bucket_name=minio.KB_BUCKETS["documents"],
            object_name=object_name,
            data=source_path.read_bytes(),
            content_type=entry.get("content_type"),
        )
        metadata = await knowledge_base.add_file_record(
            target_kb_id,
            upload.url,
            params={
                "content_hash": content_hash,
                "file_size": source_path.stat().st_size,
                "content_type": entry.get("content_type") or "file",
                "original_filename": source_path.name,
            },
            operator_id=operator_id,
        )
        file_id = metadata.get("file_id")
        await knowledge_base.parse_file(target_kb_id, file_id, operator_id=operator_id)
        indexed = await knowledge_base.index_file(target_kb_id, file_id, operator_id=operator_id)
        imported.append({"filename": source_path.name, "file_id": file_id, "indexed": indexed})
    return {"status": "completed", "imported": imported, "skipped": skipped, "count": len(imported)}
