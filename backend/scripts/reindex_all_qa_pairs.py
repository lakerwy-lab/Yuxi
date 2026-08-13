"""一次性脚本：重建所有 QA 对的 Milvus 索引（content 只存问题）。

用法：
    docker exec api-dev python scripts/reindex_all_qa_pairs.py [--kb-id <kb_id>]

可选 --kb-id 只重建指定知识库的 QA 索引，不传则重建全部。
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from yuxi.services.qa_pair_service import _upsert_qa_pair_index
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_qa import QAPair
from yuxi.utils.logging_config import logger


async def reindex_all(kb_id: str | None = None) -> None:
    """遍历所有启用的 QA 对，逐条重新写入 Milvus 索引。"""

    async with pg_manager.get_async_session_context() as session:
        query = select(QAPair).where(QAPair.enabled.is_(True), QAPair.deleted_at.is_(None))
        if kb_id:
            query = query.where(QAPair.kb_id == kb_id)
        items = list((await session.execute(query)).scalars().all())

    if not items:
        logger.info("No QA pairs found to reindex.")
        return

    logger.info(f"Reindexing {len(items)} QA pairs" + (f" (kb_id={kb_id})" if kb_id else ""))
    success = 0
    for item in items:
        try:
            await _upsert_qa_pair_index(item)
            success += 1
        except Exception:
            logger.exception("Failed to reindex QA pair id=%s", item.id)

    logger.info(f"Reindex complete: {success}/{len(items)} succeeded")


def main() -> None:
    parser = argparse.ArgumentParser(description="重建 QA 对 Milvus 索引")
    parser.add_argument("--kb-id", default=None, help="只重建指定知识库的 QA 索引")
    args = parser.parse_args()
    asyncio.run(reindex_all(args.kb_id))


if __name__ == "__main__":
    sys.exit(main() or 0)
