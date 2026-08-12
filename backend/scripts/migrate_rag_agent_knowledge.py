"""rag-agent 知识库内容到 Yuxi 的一次性迁移脚本。

用法（在 api-dev 容器内执行）::

    docker exec api-dev python scripts/migrate_rag_agent_knowledge.py \
        --target-kb kb_xxx \
        --target-qa-kb kb_yyy

做两件事：
1. 从 rag-agent MinIO 取 IT 知识库的原始文件（pdf/docx），上传到 Yuxi 目标知识库，
   走 Yuxi 既有解析+索引链路重新解析。
2. 从 rag-agent PG 读已发布的问答对，写入 Yuxi qa_pairs 表。

幂等：文件按 checksum 去重，问答对按 standard_question 去重，脚本可重复执行。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import sys
from typing import Any

# rag-agent 数据源配置（从 rag-agent 的 .env 读取，这里硬编码避免依赖）
RAG_PG_HOST = os.getenv("RAG_PG_HOST", "10.100.60.56")
RAG_PG_PORT = int(os.getenv("RAG_PG_PORT", "5432"))
RAG_PG_USER = os.getenv("RAG_PG_USER", "ragAgent")
RAG_PG_PASSWORD = os.getenv("RAG_PG_PASSWORD", "infini_rag_flow")
RAG_PG_KNOWLEDGE_DB = os.getenv("RAG_PG_KNOWLEDGE_DB", "knowledge")
RAG_PG_RAGAGENT_DB = os.getenv("RAG_PG_RAGAGENT_DB", "rag_agent")

RAG_MINIO_ENDPOINT = os.getenv("RAG_MINIO_ENDPOINT", "10.100.60.56:29000")
RAG_MINIO_ACCESS_KEY = os.getenv("RAG_MINIO_ACCESS_KEY", "rag_flow")
RAG_MINIO_SECRET_KEY = os.getenv("RAG_MINIO_SECRET_KEY", "infini_rag_flow")
RAG_MINIO_SECURE = os.getenv("RAG_MINIO_SECURE", "false").lower() == "true"

# 只迁移 IT 知识库的文档（非 qa 类型），实测存量只有 docx/pdf
SKIP_FILE_TYPES = {"qa"}


def _sha256_bytes(data: bytes) -> str:
    """计算 bytes 的 sha256。"""
    return hashlib.sha256(data).hexdigest()


def _get_rag_minio_client():
    """连接 rag-agent 的 MinIO（用 minio 库，非 Yuxi 的 MinIOClient）。"""
    from minio import Minio

    return Minio(
        RAG_MINIO_ENDPOINT,
        access_key=RAG_MINIO_ACCESS_KEY,
        secret_key=RAG_MINIO_SECRET_KEY,
        secure=RAG_MINIO_SECURE,
    )


def _fetch_rag_documents() -> list[dict[str, Any]]:
    """从 rag-agent knowledge 库读 documents 表，返回非 qa 类型的文档列表。"""
    import psycopg

    conn_str = f"host={RAG_PG_HOST} port={RAG_PG_PORT} user={RAG_PG_USER} password={RAG_PG_PASSWORD} dbname={RAG_PG_KNOWLEDGE_DB}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kb_id::text, filename, file_type, file_path, checksum
                FROM documents
                WHERE file_type != ALL(%s)
                ORDER BY created_at
                """,
                (list(SKIP_FILE_TYPES),),
            )
            rows = cur.fetchall()
    return [
        {"kb_id": r[0], "filename": r[1], "file_type": r[2], "file_path": r[3], "checksum": r[4]}
        for r in rows
    ]


def _fetch_rag_qa_pairs() -> list[dict[str, Any]]:
    """从 rag-agent rag_agent 库读已发布的问答对（status=published 且 active_revision 非空）。"""
    import psycopg

    conn_str = f"host={RAG_PG_HOST} port={RAG_PG_PORT} user={RAG_PG_USER} password={RAG_PG_PASSWORD} dbname={RAG_PG_RAGAGENT_DB}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id,
                       r.standard_question,
                       r.aliases_json,
                       r.answer_markdown,
                       r.tags_json,
                       r.category,
                       p.updated_by_union_id
                FROM qa_pair p
                JOIN qa_pair_revision r ON r.qa_id = p.id AND r.revision_no = p.active_revision
                WHERE p.status = 'published' AND p.active_revision IS NOT NULL
                ORDER BY p.created_at
                """
            )
            rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "standard_question": r[1] or "",
            "aliases": _parse_json_list(r[2]),
            "answer_markdown": r[3] or "",
            "tags": _parse_json_list(r[4]),
            "category": r[5] or "",
            "updated_by_union_id": r[6] or "",
        }
        for r in rows
    ]


def _parse_json_list(raw: str | None) -> list[str]:
    """安全解析 rag-agent 的 JSON 数组字符串（aliases_json / tags_json）。"""
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return [str(item) for item in result] if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# 匹配 asset://{32hex} 占位符
_ASSET_RE = re.compile(r"asset://([a-f0-9]{32})")


def _fetch_asset_map(asset_ids: set[str]) -> dict[str, dict[str, Any]]:
    """从 rag-agent 查 knowledge_document_assets 表，返回 asset_id → {object_key, content_type}。"""
    if not asset_ids:
        return {}
    import psycopg

    conn_str = f"host={RAG_PG_HOST} port={RAG_PG_PORT} user={RAG_PG_USER} password={RAG_PG_PASSWORD} dbname={RAG_PG_RAGAGENT_DB}"
    result: dict[str, dict[str, Any]] = {}
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT asset_id, object_key, content_type, storage_backend
                FROM knowledge_document_assets
                WHERE asset_id = ANY(%s) AND storage_backend = 'minio'
                """,
                (list(asset_ids),),
            )
            for row in cur.fetchall():
                result[row[0]] = {
                    "object_key": row[1],
                    "content_type": row[2] or "image/png",
                }
    return result


def _migrate_qa_images(
    answer_markdown: str,
    rag_minio_client,
    yuxi_minio,
) -> str:
    """把答案里的 asset://xxx 引用改为 /minio/public/... 格式，图片上传到 Yuxi MinIO。"""
    asset_ids = set(_ASSET_RE.findall(answer_markdown))
    if not asset_ids:
        return answer_markdown

    asset_map = _fetch_asset_map(asset_ids)
    if not asset_map:
        return answer_markdown

    def _replace(match: re.Match) -> str:
        asset_id = match.group(1)
        info = asset_map.get(asset_id)
        if not info:
            return match.group(0)
        try:
            raw = _get_file_from_rag_minio(rag_minio_client, "ragagent-assets", info["object_key"])
            ext = os.path.splitext(info["object_key"])[1] or ".png"
            object_name = f"qa-migration/{asset_id}{ext}"
            yuxi_minio.upload_file(
                bucket_name="public",
                object_name=object_name,
                data=raw,
                content_type=info["content_type"],
            )
            return f"/minio/public/{object_name}"
        except Exception:
            return match.group(0)

    return _ASSET_RE.sub(_replace, answer_markdown)


async def _map_union_id_to_yuxi_user(union_id: str) -> str:
    """把 rag-agent 的钉钉 unionId 映射到 Yuxi 的 username（中文名，用于 created_by 字段）。"""
    if not union_id:
        return "migration"
    # Yuxi uid 格式: dingtalk:{corpId}:{unionId}
    from yuxi import config as conf
    from sqlalchemy import text
    from yuxi.storage.postgres.manager import pg_manager

    corp_id = getattr(conf, "dingtalk_corp_id", None) or os.getenv("DINGTALK_CORP_ID", "")
    # 优先匹配完整 uid（含 corp_id），回退匹配不含 corp_id 的旧格式
    candidates = []
    if corp_id:
        candidates.append(f"dingtalk:{corp_id}:{union_id}")
    candidates.append(f"dingtalk:{union_id}")

    async with pg_manager.get_async_session_context() as db:
        for uid in candidates:
            result = await db.execute(
                text("SELECT username FROM users WHERE uid = :uid AND is_deleted = 0"),
                {"uid": uid},
            )
            row = result.fetchone()
            if row:
                return row[0]
    # 用户不存在，返回 "migration" 兜底
    return "migration"


def _get_file_from_rag_minio(client, bucket: str, object_name: str) -> bytes:
    """从 rag-agent MinIO 取文件原始内容。"""
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


async def _migrate_documents(target_kb_id: str, operator_id: str) -> dict[str, Any]:
    """迁移文档：从 rag-agent MinIO 取原文件 → 上传 Yuxi → 解析+索引。"""
    from yuxi.knowledge.runtime import knowledge_base
    from yuxi.storage.minio import get_minio_client

    docs = _fetch_rag_documents()
    print(f"[文档] 从 rag-agent 读取到 {len(docs)} 个文档")

    rag_minio = _get_rag_minio_client()
    yuxi_minio = get_minio_client()

    imported, skipped, failed = [], [], []
    for doc in docs:
        filename = doc["filename"]
        file_path = doc["file_path"]  # 格式: "{kb_id}/{doc_id}.{ext}"
        bucket, _, object_name = file_path.partition("/")

        try:
            # 1. 从 rag-agent MinIO 取文件
            raw_bytes = _get_file_from_rag_minio(rag_minio, bucket, object_name)
            content_hash = _sha256_bytes(raw_bytes)

            # 2. checksum 去重
            if await knowledge_base.file_existed_in_db(target_kb_id, content_hash):
                print(f"  跳过（已存在）: {filename}")
                skipped.append({"filename": filename, "reason": "checksum exists"})
                continue

            # 3. 上传到 Yuxi MinIO
            ext = os.path.splitext(filename)[1]
            import time

            object_name_yuxi = f"{target_kb_id}/upload/{filename}_{int(time.time())}{ext}"
            upload = await yuxi_minio.aupload_file(
                bucket_name=yuxi_minio.KB_BUCKETS["documents"],
                object_name=object_name_yuxi,
                data=raw_bytes,
                content_type=_guess_content_type(filename),
            )

            # 4. 添加文件记录 → 解析 → 索引
            minio_url = upload.url
            metadata = await knowledge_base.add_file_record(
                target_kb_id,
                minio_url,
                params={
                    "content_hashes": {minio_url: content_hash},
                    "file_sizes": {minio_url: len(raw_bytes)},
                    "content_type": "file",
                    "original_filename": filename,
                },
                operator_id=operator_id,
            )
            file_id = metadata.get("file_id")
            await knowledge_base.parse_file(target_kb_id, file_id, operator_id=operator_id)
            await knowledge_base.index_file(target_kb_id, file_id, operator_id=operator_id)
            print(f"  导入成功: {filename}")
            imported.append({"filename": filename, "file_id": file_id})

        except Exception as exc:
            print(f"  导入失败: {filename} — {exc}")
            failed.append({"filename": filename, "error": str(exc)})

    return {"imported": len(imported), "skipped": len(skipped), "failed": len(failed), "details": failed}


async def _migrate_qa_pairs(target_qa_kb_id: str, operator_id: str) -> dict[str, Any]:
    """迁移问答对：从 rag-agent PG 读已发布 QA → 迁移图片 → 写入 Yuxi qa_pairs 表。"""
    from yuxi.services.qa_pair_service import create_qa_pair
    from yuxi.storage.postgres.manager import pg_manager

    qa_list = _fetch_rag_qa_pairs()
    print(f"[问答对] 从 rag-agent 读取到 {len(qa_list)} 条已发布问答对")

    rag_minio = _get_rag_minio_client()
    from yuxi.storage.minio import get_minio_client

    yuxi_minio = get_minio_client()

    imported, skipped = 0, 0
    async with pg_manager.get_async_session_context() as db:
        for qa in qa_list:
            question = qa["standard_question"].strip()
            answer = qa["answer_markdown"].strip()
            if not question or not answer:
                print(f"  跳过（空问题或空答案）: {qa['id']}")
                skipped += 1
                continue
            try:
                # 迁移答案里的 asset:// 图片引用
                answer = _migrate_qa_images(answer, rag_minio, yuxi_minio)

                # 映射更新人
                created_by = await _map_union_id_to_yuxi_user(qa["updated_by_union_id"])

                await create_qa_pair(
                    db,
                    kb_id=target_qa_kb_id,
                    standard_question=question,
                    answer_markdown=answer,
                    aliases=qa["aliases"],
                    tags=qa["tags"],
                    created_by=created_by,
                )
                print(f"  导入成功: {question[:40]}")
                imported += 1
            except Exception as exc:
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    print(f"  跳过（已存在）: {question[:40]}")
                else:
                    print(f"  导入失败: {question[:40]} — {exc}")
                skipped += 1
                await db.rollback()

    return {"imported": imported, "skipped": skipped}


def _guess_content_type(filename: str) -> str:
    """根据文件名猜 content-type。"""
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
    }.get(ext, "application/octet-stream")


async def main():
    parser = argparse.ArgumentParser(description="rag-agent 知识库内容迁移到 Yuxi")
    parser.add_argument("--target-kb", required=True, help="Yuxi 目标知识库 ID（文档导入）")
    parser.add_argument("--target-qa-kb", default=None, help="Yuxi 目标知识库 ID（问答对导入，不传则跳过问答对）")
    parser.add_argument("--operator", default="migration", help="操作者标识")
    parser.add_argument("--skip-documents", action="store_true", help="跳过文档迁移")
    parser.add_argument("--skip-qa", action="store_true", help="跳过问答对迁移")
    args = parser.parse_args()

    if not args.skip_documents:
        print("=" * 60)
        print("步骤 1: 迁移知识库文档")
        print("=" * 60)
        result = await _migrate_documents(args.target_kb, args.operator)
        print(f"\n文档迁移完成: 导入 {result['imported']}，跳过 {result['skipped']}，失败 {result['failed']}\n")

    if args.target_qa_kb and not args.skip_qa:
        print("=" * 60)
        print("步骤 2: 迁移问答对")
        print("=" * 60)
        result = await _migrate_qa_pairs(args.target_qa_kb, args.operator)
        print(f"\n问答对迁移完成: 导入 {result['imported']}，跳过 {result['skipped']}\n")

    print("迁移全部完成。")


if __name__ == "__main__":
    asyncio.run(main())
