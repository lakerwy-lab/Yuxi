from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.qa_pair_service import (
    create_escalation,
    create_qa_pair,
    filter_qa_pair_hits,
    find_exact_answer,
    normalize_markdown_answer,
    qa_question_match_score,
)
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_qa import QAPairIndexJob


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_create_qa_pair_queues_revisioned_index_job(session):
    item = await create_qa_pair(
        session,
        kb_id="kb-1",
        standard_question="如何申请 VPN？",
        answer_markdown="请提交工单。",
        aliases=["VPN 怎么申请"],
        created_by="admin-1",
    )

    job = await session.scalar(select(QAPairIndexJob).where(QAPairIndexJob.qa_pair_id == item.id))
    assert item.published is True
    assert item.index_status == "pending"
    assert job is not None
    assert job.target_revision == item.revision


async def test_exact_match_uses_milvus_hit_and_preserves_markdown(session, monkeypatch):
    from yuxi.knowledge import runtime

    async def fake_query(*_args, **_kwargs):
        return [
            {
                "score": 0.96,
                "vector_score": 0.96,
                "metadata": {
                    "source_type": "qa_pair",
                    "qa_pair_id": 7,
                    "qa_revision": 2,
                    "standard_question": "如何申请 VPN？",
                    "aliases": ["VPN 怎么申请"],
                    "answer_markdown": "请查看原文。\n\n![截图](/minio/public/rag-agent-assets/image.png)",
                },
            }
        ]

    monkeypatch.setattr(runtime.knowledge_base, "aquery", fake_query)
    hit = await find_exact_answer(session, ["kb-1"], "VPN 怎么申请？")

    assert hit is not None
    assert hit["revision"] == 2
    assert hit["qa_pair_id"] == 7
    assert "/minio/public/" in hit["answer"]
    assert normalize_markdown_answer("![x](/api/v1/internal/knowledge-assets/kb-1/x.png)") != ""


async def test_exact_match_does_not_use_empty_kb_scope(session):
    assert await find_exact_answer(session, [], "如何重置密码") is None


async def test_match_gate_rejects_ambiguous_qa_hits():
    hits = [
        {
            "score": 0.95,
            "metadata": {
                "source_type": "qa_pair",
                "qa_pair_id": 1,
                "standard_question": "邮箱密码怎么重置",
                "aliases": [],
            },
        },
        {
            "score": 0.94,
            "metadata": {
                "source_type": "qa_pair",
                "qa_pair_id": 2,
                "standard_question": "邮箱密码如何重置",
                "aliases": [],
            },
        },
    ]

    assert filter_qa_pair_hits("请问邮箱密码怎么重置", hits, 1) == []


async def test_low_confidence_qa_hit_falls_back_to_regular_documents():
    document = {"score": 0.82, "metadata": {"source_type": "document", "file_id": "doc-1"}}
    qa_hit = {
        "score": 0.4,
        "metadata": {
            "source_type": "qa_pair",
            "qa_pair_id": 3,
            "standard_question": "如何申请邮箱",
            "aliases": [],
        },
    }

    assert filter_qa_pair_hits("打印机坏了", [qa_hit, document], 5) == [document]


async def test_question_match_ignores_common_question_noise():
    assert qa_question_match_score("请问 VPN 怎么申请呀？", "VPN怎么申请") >= 0.98


async def test_escalation_is_persisted_without_webhook(session, monkeypatch):
    monkeypatch.delenv("DINGTALK_ESCALATION_WEBHOOK", raising=False)

    item = await create_escalation(
        session,
        uid="user-1",
        thread_id="thread-1",
        question="这个问题需要人工处理",
        context={"source": "chat"},
    )

    assert item.status == "manual"
    stored = await session.get(type(item), item.id)
    assert stored is not None and stored.context == {"source": "chat"}
