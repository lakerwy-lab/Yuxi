"""高频问答对、持久化索引任务和转人工记录。"""

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


class QAPair(Base):
    """问答对的当前版本和已发布索引版本。"""

    __tablename__ = "qa_pairs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(80), nullable=False, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    aliases = Column(JSON_VALUE, nullable=False, default=list)
    tags = Column(JSON_VALUE, nullable=False, default=list)
    image_refs = Column(JSON_VALUE, nullable=False, default=list)
    revision = Column(Integer, nullable=False, default=1)
    published = Column(Boolean, nullable=False, default=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    index_status = Column(String(32), nullable=False, default="pending", index=True)
    indexed_revision = Column(Integer, nullable=True)
    indexed_question = Column(Text, nullable=True)
    indexed_answer = Column(Text, nullable=True)
    indexed_aliases = Column(JSON_VALUE, nullable=True)
    content_hash = Column(String(128), nullable=True, index=True)
    index_error = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
    updated_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
    deleted_at = Column(String(64), nullable=True, index=True)

    __table_args__ = (Index("ix_qa_pairs_lookup", "kb_id", "published", "enabled", "index_status"),)

    @property
    def standard_question(self) -> str:
        """返回管理端使用的标准问题字段。"""

        return self.question

    @standard_question.setter
    def standard_question(self, value: str) -> None:
        self.question = value

    @property
    def answer_markdown(self) -> str:
        """返回 Markdown 答案正本。"""

        return self.answer

    @answer_markdown.setter
    def answer_markdown(self, value: str) -> None:
        self.answer = value

    @property
    def status(self) -> str:
        """收敛历史双布尔字段为发布/停用两态。"""

        return "published" if self.published and self.enabled else "disabled"


class QAPairIndexJob(Base):
    """问答对发布后的可重试索引任务。"""

    __tablename__ = "qa_pair_index_jobs"

    id = Column(String(64), primary_key=True)
    qa_pair_id = Column(Integer, ForeignKey("qa_pairs.id", ondelete="CASCADE"), nullable=False, index=True)
    target_revision = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    next_retry_at = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
    updated_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())


class QAEscalation(Base):
    """转人工请求和通知结果。"""

    __tablename__ = "qa_escalations"

    id = Column(String(64), primary_key=True)
    uid = Column(String(64), nullable=False, index=True)
    thread_id = Column(String(64), nullable=True, index=True)
    question = Column(Text, nullable=False)
    context = Column(JSON_VALUE, nullable=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
    updated_at = Column(String(64), nullable=False, default=lambda: utc_now_naive().isoformat())
