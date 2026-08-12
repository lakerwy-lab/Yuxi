"""把 kb_0i2nbh1bpf 的 37 条问答对迁移到 kb_ffsjd96uh7 并重新索引写入 Milvus。

背景：
- 之前把 _upsert_qa_pair_index 改成不写 Milvus，导致 QA 虽标记 synced 但实际未入向量库。
- 现已恢复写入逻辑（content 含答案，和 Yuxi QA 分块策略一致），需要重新索引。
- 用户指定新知识库 kb_ffsjd96uh7（名叫"问答对"）单独存放 QA。
"""

import asyncio

from sqlalchemy import select

from yuxi.services.qa_pair_service import create_qa_pair
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_qa import QAPair
from yuxi.utils.datetime_utils import utc_now_naive

SOURCE_KB = "kb_0i2nbh1bpf"
TARGET_KB = "kb_ffsjd96uh7"


async def main() -> None:
    async with pg_manager.get_async_session_context() as db:
        rows = (
            await db.execute(
                select(QAPair).where(QAPair.kb_id == SOURCE_KB, QAPair.deleted_at.is_(None))
            )
        ).scalars().all()
        print(f"源库 {SOURCE_KB} 待迁移 QA: {len(rows)} 条")

        imported = 0
        skipped = 0
        for item in rows:
            try:
                await create_qa_pair(
                    db,
                    kb_id=TARGET_KB,
                    standard_question=item.question,
                    answer_markdown=item.answer,
                    aliases=item.aliases or [],
                    tags=item.tags or [],
                    image_refs=item.image_refs or [],
                    created_by=item.created_by,
                )
                # 软删旧库 QA（避免 Milvus 旧索引残留）
                item.deleted_at = utc_now_naive().isoformat()
                item.updated_at = utc_now_naive().isoformat()
                imported += 1
                print(f"  导入: {item.question[:40]}")
            except Exception as exc:
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    print(f"  跳过（已存在）: {item.question[:40]}")
                else:
                    print(f"  失败: {item.question[:40]} — {exc}")
                skipped += 1
                await db.rollback()

        print(f"\n迁移完成: 导入 {imported} 条, 跳过/失败 {skipped} 条")
        print(f"目标库 {TARGET_KB} 的 QA 将由 ARQ worker 自动写入 Milvus。")


if __name__ == "__main__":
    asyncio.run(main())
