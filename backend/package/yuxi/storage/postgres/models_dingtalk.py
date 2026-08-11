"""钉钉通讯录快照模型。"""

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive


class DingTalkDepartmentSnapshot(Base):
    """保存某个企业的钉钉部门树快照，不替代 Yuxi 本地部门。"""

    __tablename__ = "dingtalk_departments"

    corp_id = Column(String(128), primary_key=True)
    dept_id = Column(String(128), primary_key=True)
    parent_dept_id = Column(String(128), nullable=True)
    dept_name = Column(String(255), nullable=False)
    dept_path = Column(String(2048), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    synced_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (Index("ix_dingtalk_departments_corp_active", "corp_id", "active"),)


class DingTalkUserDepartmentSnapshot(Base):
    """保存钉钉用户与部门的多对多关系快照。"""

    __tablename__ = "dingtalk_user_departments"

    corp_id = Column(String(128), primary_key=True)
    union_id = Column(String(128), primary_key=True)
    dept_id = Column(String(128), primary_key=True)
    user_id = Column(String(128), nullable=True)
    user_name = Column(String(255), nullable=False)
    job_number = Column(String(128), nullable=True)
    email = Column(String(320), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    synced_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        Index("ix_dingtalk_user_departments_corp_union", "corp_id", "union_id"),
        Index("ix_dingtalk_user_departments_corp_dept", "corp_id", "dept_id"),
    )


class DingTalkDirectorySyncRun(Base):
    """记录一次通讯录同步的状态、统计和失败原因。"""

    __tablename__ = "dingtalk_directory_sync_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_id = Column(String(128), nullable=False)
    sync_type = Column(String(32), nullable=False, default="full")
    status = Column(String(32), nullable=False, default="running")
    department_count = Column(Integer, nullable=False, default=0)
    user_count = Column(Integer, nullable=False, default=0)
    changed_user_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=False, default=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (Index("ix_dingtalk_directory_sync_runs_corp_status", "corp_id", "status"),)
