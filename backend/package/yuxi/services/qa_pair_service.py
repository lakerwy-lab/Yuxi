"""表单问答对发布、Milvus 索引和原文短路服务。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.minio.client import normalize_public_minio_url
from yuxi.storage.postgres.models_qa import QAEscalation, QAPair, QAPairIndexJob
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


_MARKDOWN_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^\)]+)(\))")
_QA_QUESTION_NOISE = (
    "请问",
    "如何",
    "怎么",
    "怎样",
    "能否",
    "是否",
    "可以",
    "我想",
    "想要",
    "帮我",
    "请帮我",
    "请",
    "一下",
    "吗",
    "呢",
    "什么",
    "怎么办",
    "哪些",
    "哪个",
    "怎么弄",
    "哪里",
    "在哪",
    "在哪里",
    "能不能",
    "呀",
)


def normalize_question(value: Any) -> str:
    """只保留字母和数字并统一大小写，用于中文问句匹配。"""

    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _question_core(value: Any) -> str:
    normalized = normalize_question(value)
    for noise in sorted(_QA_QUESTION_NOISE, key=len, reverse=True):
        normalized = normalized.replace(noise, "")
    return normalized


def qa_question_match_score(query: str, candidate: str) -> float:
    """按 rag-agent 规则计算标准问题匹配分数。"""

    query_text = normalize_question(query)
    candidate_text = normalize_question(candidate)
    if not query_text or not candidate_text:
        return 0.0
    if query_text == candidate_text:
        return 1.0

    query_core = _question_core(query)
    candidate_core = _question_core(candidate)
    if not query_core or not candidate_core:
        return 0.0
    if query_core == candidate_core:
        return 0.98

    query_chars = set(query_core)
    candidate_chars = set(candidate_core)
    overlap = len(query_chars & candidate_chars)
    if not overlap:
        return 0.0
    score = overlap / len(query_chars) * 0.7 + overlap / len(candidate_chars) * 0.3
    query_grams = {query_core[index : index + 2] for index in range(len(query_core) - 1)}
    candidate_grams = {candidate_core[index : index + 2] for index in range(len(candidate_core) - 1)}
    if query_grams and candidate_grams:
        score = max(score, 2 * len(query_grams & candidate_grams) / (len(query_grams) + len(candidate_grams)))
    return score


def _deduplicate(values: list[str] | None, *, limit: int = 50) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = str(value).strip()
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def normalize_markdown_answer(value: str) -> str:
    """规范化问答 Markdown 中已存在的 MinIO 图片地址。"""

    def replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}{normalize_public_minio_url(match.group(2)) or match.group(2)}{match.group(3)}"

    return _MARKDOWN_IMAGE_RE.sub(replace, str(value or "")).strip()


def _extract_image_refs(answer_markdown: str) -> list[str]:
    return _deduplicate([match.group(2) for match in _MARKDOWN_IMAGE_RE.finditer(answer_markdown)])


def _content_hash(question: str, answer: str, aliases: list[str]) -> str:
    payload = json.dumps([normalize_question(question), answer, sorted(aliases)], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def serialize_qa_pair(item: QAPair, *, updated_by_name: str | None = None) -> dict[str, Any]:
    """输出兼容旧字段、以标准问题和 Markdown 答案为主的管理端结构。"""

    return {
        "id": item.id,
        "kb_id": item.kb_id,
        "standard_question": item.question,
        "question": item.question,
        "answer_markdown": item.answer,
        "answer": item.answer,
        "aliases": item.aliases or [],
        "tags": item.tags or [],
        "image_refs": item.image_refs or [],
        "revision": item.revision,
        "status": item.status,
        "published": bool(item.published),
        "enabled": bool(item.enabled),
        "index_status": item.index_status,
        "indexed_revision": item.indexed_revision,
        "index_error": item.index_error,
        "created_by": item.created_by,
        "updated_by": item.updated_by,
        "updated_by_name": updated_by_name or item.updated_by,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


async def create_qa_pair(
    db: AsyncSession,
    *,
    kb_id: str,
    standard_question: str,
    answer_markdown: str,
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
    image_refs: list[str] | None = None,
    created_by: str | None = None,
) -> QAPair:
    """创建即发布，数据库提交后由持久化任务写入 Milvus。"""

    aliases = _deduplicate(aliases)
    tags = _deduplicate(tags)
    answer_markdown = normalize_markdown_answer(answer_markdown)
    item = QAPair(
        kb_id=kb_id,
        question=standard_question.strip(),
        answer=answer_markdown,
        aliases=aliases,
        tags=tags,
        image_refs=_deduplicate(image_refs) or _extract_image_refs(answer_markdown),
        published=True,
        enabled=True,
        index_status="pending",
        content_hash=_content_hash(standard_question, answer_markdown, aliases),
        created_by=created_by,
        updated_by=created_by,
        created_at=utc_now_naive().isoformat(),
        updated_at=utc_now_naive().isoformat(),
    )
    db.add(item)
    await db.flush()
    await _enqueue_index_job(db, item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_qa_pair(db: AsyncSession, item: QAPair, values: dict[str, Any], operator: str | None) -> QAPair:
    """保存编辑并重新发布；旧有效索引在新版本成功前继续服务。"""

    if "standard_question" in values:
        item.question = str(values["standard_question"]).strip()
    if "answer_markdown" in values:
        item.answer = normalize_markdown_answer(values["answer_markdown"])
    if "aliases" in values:
        item.aliases = _deduplicate(values["aliases"])
    if "tags" in values:
        item.tags = _deduplicate(values["tags"])
    if "image_refs" in values:
        item.image_refs = _deduplicate(values["image_refs"])
    else:
        item.image_refs = _extract_image_refs(item.answer)

    item.revision += 1
    item.published = True
    item.enabled = True
    item.deleted_at = None
    item.index_status = "pending"
    item.index_error = None
    item.updated_by = operator
    item.updated_at = utc_now_naive().isoformat()
    item.content_hash = _content_hash(item.question, item.answer, item.aliases or [])
    await _enqueue_index_job(db, item)
    await db.commit()
    await db.refresh(item)
    return item


async def _enqueue_index_job(db: AsyncSession, item: QAPair) -> QAPairIndexJob:
    job = QAPairIndexJob(
        id=f"qaj_{secrets.token_hex(10)}",
        qa_pair_id=item.id,
        target_revision=item.revision,
        status="queued",
        created_at=utc_now_naive().isoformat(),
        updated_at=utc_now_naive().isoformat(),
    )
    db.add(job)
    return job


async def _upsert_qa_pair_index(item: QAPair) -> None:
    from yuxi.knowledge.implementations.milvus import MilvusKB
    from yuxi.knowledge.runtime import knowledge_base

    config = await knowledge_base.get_kb_config(item.kb_id)
    executor = await knowledge_base.get_kb_executor(item.kb_id)
    if not isinstance(executor, MilvusKB):
        raise ValueError("表单问答对仅支持 Milvus 知识库")
    await executor.upsert_qa_pair_index(
        kb_id=item.kb_id,
        qa_pair_id=item.id,
        revision=item.revision,
        standard_question=item.question,
        answer=item.answer,
        aliases=item.aliases or [],
        config=config,
        previous_revision=item.indexed_revision,
    )


async def _delete_qa_pair_index(item: QAPair) -> None:
    from yuxi.knowledge.implementations.milvus import MilvusKB
    from yuxi.knowledge.runtime import knowledge_base

    executor = await knowledge_base.get_kb_executor(item.kb_id)
    if not isinstance(executor, MilvusKB):
        return
    await executor.delete_qa_pair_index(item.kb_id, item.id, item.indexed_revision)


async def process_qa_pair_index_job(ctx: dict[str, Any], job_id: str, qa_pair_id: int):
    """ARQ worker 入口：真实写入 Milvus，成功后切换有效版本。"""

    del ctx
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        job = await db.get(QAPairIndexJob, job_id)
        item = await db.get(QAPair, qa_pair_id)
        if job is None or item is None:
            return
        if job.status in {"completed", "superseded"}:
            return
        if item.deleted_at or not item.enabled or not item.published:
            job.status = "completed"
            job.updated_at = utc_now_naive().isoformat()
            await db.commit()
            return
        if job.target_revision != item.revision:
            job.status = "superseded"
            job.updated_at = utc_now_naive().isoformat()
            await db.commit()
            return

        job.status = "running"
        job.attempts += 1
        job.updated_at = utc_now_naive().isoformat()
        await db.commit()
        try:
            await _upsert_qa_pair_index(item)
            item.indexed_revision = item.revision
            item.indexed_question = normalize_question(item.question)
            item.indexed_answer = normalize_markdown_answer(item.answer)
            item.indexed_aliases = [item.question, *(item.aliases or [])]
            item.index_status = "synced"
            item.index_error = None
            job.status = "completed"
            job.error_message = None
            job.next_retry_at = None
        except Exception as exc:
            item.index_status = "failed"
            item.index_error = str(exc)[:2000]
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            job.next_retry_at = (utc_now_naive() + timedelta(minutes=min(2**job.attempts, 30))).isoformat()
            logger.exception("QA pair index failed: qa_pair_id=%s revision=%s", item.id, item.revision)
            raise
        finally:
            job.updated_at = utc_now_naive().isoformat()
            item.updated_at = utc_now_naive().isoformat()
            await db.commit()


async def retry_pending_qa_index_jobs(ctx: dict[str, Any] | None = None) -> int:
    """周期补偿未完成的问答对索引任务。"""

    from yuxi.services.run_queue_service import get_arq_pool
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            select(QAPairIndexJob)
            .where(QAPairIndexJob.status.in_(["queued", "failed"]), QAPairIndexJob.attempts < 5)
            .order_by(QAPairIndexJob.created_at)
            .limit(20)
        )
        jobs = list(result.scalars().all())

    queue = (ctx or {}).get("redis") or await get_arq_pool()
    submitted = 0
    now = utc_now_naive()
    for job in jobs:
        if job.next_retry_at and datetime.fromisoformat(job.next_retry_at) > now:
            continue
        queued = await queue.enqueue_job(
            "process_qa_pair_index_job",
            job.id,
            job.qa_pair_id,
            _job_id=f"qa-index:{job.id}",
        )
        submitted += int(queued is not None)
    return submitted


def _hit_match_score(question: str, hit: dict[str, Any]) -> float:
    metadata = hit.get("metadata") or {}
    candidates = [metadata.get("standard_question"), *(metadata.get("aliases") or [])]
    return max((qa_question_match_score(question, str(value)) for value in candidates if value), default=0.0)


def _lexical_overlap(question: str, candidate: str) -> float:
    query = _question_core(question)
    content = _question_core(candidate)
    if not query or not content:
        return 0.0
    grams = {query[index : index + 2] for index in range(max(len(query) - 1, 1))} or {query}
    content_grams = {content[index : index + 2] for index in range(max(len(content) - 1, 1))} or {content}
    return len(grams & content_grams) / len(grams)


def filter_qa_pair_hits(question: str, hits: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """QA 高置信命中优先；歧义候选不进入 LLM。"""

    qa_hits = [hit for hit in hits if (hit.get("metadata") or {}).get("source_type") == "qa_pair"]
    document_hits = [hit for hit in hits if (hit.get("metadata") or {}).get("source_type") != "qa_pair"]
    if not qa_hits:
        return document_hits[:top_k]

    threshold = float(os.getenv("KNOWLEDGE_QA_MATCH_THRESHOLD", "0.72"))
    min_margin = float(os.getenv("KNOWLEDGE_QA_MIN_MARGIN", "0.10"))
    min_vector = float(os.getenv("KNOWLEDGE_QA_MIN_VECTOR_SIMILARITY", "0.82"))
    min_lexical = float(os.getenv("KNOWLEDGE_RETRIEVAL_MIN_LEXICAL_OVERLAP", "0.45"))
    scored = sorted(
        ((_hit_match_score(question, hit), hit) for hit in qa_hits),
        key=lambda pair: (pair[0], float(pair[1].get("score") or 0.0)),
        reverse=True,
    )
    if not scored or scored[0][0] < threshold:
        return document_hits[:top_k]
    if len(scored) > 1 and scored[0][0] - scored[1][0] < min_margin:
        return document_hits[:top_k]

    best_score, best = scored[0]
    metadata = best.get("metadata") or {}
    candidates = [metadata.get("standard_question"), *(metadata.get("aliases") or [])]
    lexical = max(
        (_lexical_overlap(question, str(value)) for value in candidates if value),
        default=0.0,
    )
    vector_score = float(best.get("vector_score", best.get("score", 0.0)) or 0.0)
    if vector_score < min_vector and lexical < min_lexical:
        return document_hits[:top_k]
    best["qa_match_score"] = best_score
    return [best] if top_k > 0 else []


async def enrich_qa_pair_hits(hits: list[dict[str, Any]]) -> None:
    """将 Milvus 合成文件命中补齐为可校验的问答对元数据。"""

    ids = {
        int(metadata["qa_pair_id"])
        for hit in hits
        if isinstance((metadata := hit.get("metadata")), dict)
        and metadata.get("source_type") == "qa_pair"
        and str(metadata.get("qa_pair_id", "")).isdigit()
    }
    if not ids:
        return

    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            select(QAPair).where(
                QAPair.id.in_(ids),
                QAPair.published.is_(True),
                QAPair.enabled.is_(True),
                QAPair.deleted_at.is_(None),
                QAPair.index_status == "synced",
            )
        )
        items = {item.id: item for item in result.scalars().all()}
    for hit in hits:
        metadata = hit.get("metadata") or {}
        item = items.get(metadata.get("qa_pair_id"))
        if item is None or metadata.get("qa_revision") != item.indexed_revision:
            metadata["source_type"] = "stale_qa_pair"
            continue
        metadata.update(
            standard_question=item.question,
            aliases=item.aliases or [],
            answer_markdown=normalize_markdown_answer(item.indexed_answer or item.answer),
        )


async def find_exact_answer(db: AsyncSession, kb_ids: list[str] | None, query: str) -> dict[str, Any] | None:
    """通过 Milvus 召回并执行高置信门控，命中后返回 Markdown 原文。"""

    del db
    normalized_kb_ids = [str(item).strip() for item in kb_ids or [] if str(item).strip()]
    if not normalized_kb_ids or not normalize_question(query):
        return None

    from yuxi.knowledge.runtime import knowledge_base

    hits: list[dict[str, Any]] = []
    for kb_id in normalized_kb_ids:
        try:
            results = await knowledge_base.aquery(
                query,
                kb_id,
                search_mode="vector",
                final_top_k=50,
                recall_top_k=50,
                similarity_threshold=0.0,
                use_graph_retrieval=False,
                use_reranker=False,
            )
        except Exception:
            logger.exception("QA retrieval failed for kb_id=%s", kb_id)
            continue
        for hit in results or []:
            if (hit.get("metadata") or {}).get("source_type") == "qa_pair":
                hit["kb_id"] = kb_id
                hits.append(hit)

    matched = filter_qa_pair_hits(query, hits, 1)
    if not matched:
        return None
    best = matched[0]
    metadata = best.get("metadata") or {}
    answer = str(metadata.get("answer_markdown") or "").strip()
    if not answer:
        return None
    return {
        "qa_pair_id": metadata["qa_pair_id"],
        "kb_id": best["kb_id"],
        "answer": answer,
        "score": round(float(best.get("qa_match_score") or 0.0), 4),
        "revision": metadata.get("qa_revision"),
    }


async def disable_qa_pair(db: AsyncSession, item: QAPair, operator: str | None, *, deleted: bool = False) -> QAPair:
    """先移除有效索引，再将问答对停用或软删除。"""

    await _delete_qa_pair_index(item)
    item.enabled = False
    item.published = False
    item.index_status = "disabled"
    item.updated_by = operator
    item.updated_at = utc_now_naive().isoformat()
    if deleted:
        item.deleted_at = item.updated_at
    await db.commit()
    await db.refresh(item)
    return item


async def create_escalation(
    db: AsyncSession,
    *,
    uid: str,
    question: str,
    thread_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> QAEscalation:
    """记录转人工请求并尽力发送钉钉 webhook，失败状态可重试。"""

    escalation = QAEscalation(
        id=f"esc_{secrets.token_hex(10)}",
        uid=uid,
        thread_id=thread_id,
        question=question.strip(),
        context=context or {},
        status="queued",
        created_at=utc_now_naive().isoformat(),
        updated_at=utc_now_naive().isoformat(),
    )
    db.add(escalation)
    await db.commit()
    webhook = os.getenv("DINGTALK_ESCALATION_WEBHOOK", "").strip()
    if not webhook:
        escalation.status = "manual"
        await db.commit()
        return escalation
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(
                webhook,
                json={"msgtype": "text", "text": {"content": f"转人工请求\n用户：{uid}\n问题：{question}"}},
            )
            response.raise_for_status()
        escalation.status = "sent"
        escalation.error_message = None
    except httpx.HTTPError as exc:
        logger.warning("failed to send QA escalation: %s", exc)
        escalation.status = "failed"
        escalation.error_message = str(exc)[:2000]
    escalation.updated_at = utc_now_naive().isoformat()
    await db.commit()
    return escalation
