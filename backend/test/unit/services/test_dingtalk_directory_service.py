from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.dingtalk_directory_service import fetch_directory_snapshot, get_sync_status, sync_directory
from yuxi.storage.postgres.models_business import Base, Department, User
from yuxi.storage.postgres.models_dingtalk import DingTalkDirectorySyncRun, DingTalkUserDepartmentSnapshot


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class FakeDirectoryClient:
    def __init__(self, *, empty_users: bool = False):
        self.empty_users = empty_users

    async def list_sub_departments(self, dept_id: str):
        if dept_id == "1":
            return {"result": [{"dept_id": 2, "name": "研发"}]}
        return {"result": []}

    async def list_department_users(self, dept_id: str, *, cursor: int, size: int):
        if self.empty_users:
            return {"result": {"list": [], "has_more": False}}
        if dept_id == "1":
            return {"result": {"list": [], "has_more": False}}
        if cursor == 0:
            return {
                "result": {
                    "list": [{"unionid": "union-1", "userid": "user-1", "name": "张三"}],
                    "has_more": True,
                    "next_cursor": 1,
                }
            }
        return {"result": {"list": [{"unionid": "union-1", "userid": "user-1", "name": "张三"}], "has_more": False}}


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_fetch_directory_snapshot_paginates_and_keeps_multi_department_relations():
    departments, users = await fetch_directory_snapshot("corp-1", FakeDirectoryClient())

    assert [item["dept_id"] for item in departments] == ["1", "2"]
    assert departments[1]["dept_name"] == "研发"
    assert departments[1]["dept_path"] == "/1/2/"
    assert users == [
        {
            "union_id": "union-1",
            "user_id": "user-1",
            "user_name": "张三",
            "job_number": None,
            "email": None,
            "dept_id": "2",
        }
    ]


async def test_sync_replaces_snapshot_projects_user_and_soft_deletes_on_absence(monkeypatch, session):
    monkeypatch.setenv("DINGTALK_CORP_ID", "corp-1")

    run = await sync_directory(session, client=FakeDirectoryClient())
    assert run.status == "completed"
    assert run.department_count == 2
    assert run.user_count == 1
    user = await session.scalar(select(User).where(User.dingtalk_union_id == "union-1"))
    assert user is not None
    assert user.dingtalk_corp_id == "corp-1"
    assert user.department_id is not None
    department = await session.get(Department, user.department_id)
    assert department is not None and department.name == "研发"
    first_run_id = run.id

    second = await sync_directory(session, client=FakeDirectoryClient(empty_users=True))
    assert second.status == "completed"
    relation = await session.scalar(
        select(DingTalkUserDepartmentSnapshot).where(DingTalkUserDepartmentSnapshot.union_id == "union-1")
    )
    assert relation is not None and relation.active is False
    user = await session.scalar(select(User).where(User.dingtalk_union_id == "union-1"))
    assert user is not None and user.is_deleted == 1
    assert (
        await session.scalar(select(DingTalkDirectorySyncRun).where(DingTalkDirectorySyncRun.id == first_run_id))
        is not None
    )


async def test_sync_does_not_delete_a_manually_bound_local_account(monkeypatch, session):
    monkeypatch.setenv("DINGTALK_CORP_ID", "corp-1")
    local_user = User(
        username="本地用户",
        uid="local-user",
        password_hash="hash",
        role="user",
        dingtalk_corp_id="corp-1",
        dingtalk_union_id="union-1",
    )
    session.add(local_user)
    await session.commit()

    await sync_directory(session, client=FakeDirectoryClient())
    await sync_directory(session, client=FakeDirectoryClient(empty_users=True))

    await session.refresh(local_user)
    assert local_user.is_deleted == 0
    assert local_user.uid == "local-user"


async def test_sync_assigns_the_deepest_department(monkeypatch, session):
    monkeypatch.setenv("DINGTALK_CORP_ID", "corp-1")

    class NestedDirectoryClient(FakeDirectoryClient):
        async def list_sub_departments(self, dept_id: str):
            if dept_id == "1":
                return {"result": [{"dept_id": 2, "name": "研发"}]}
            if dept_id == "2":
                return {"result": [{"dept_id": 3, "name": "平台组"}]}
            return {"result": []}

        async def list_department_users(self, dept_id: str, *, cursor: int, size: int):
            if dept_id in {"2", "3"}:
                return {
                    "result": {
                        "list": [{"unionid": "union-1", "userid": "user-1", "name": "张三"}],
                        "has_more": False,
                    }
                }
            return {"result": {"list": [], "has_more": False}}

    await sync_directory(session, client=NestedDirectoryClient())

    user = await session.scalar(select(User).where(User.dingtalk_union_id == "union-1"))
    department = await session.get(Department, user.department_id)
    assert department is not None
    assert department.name == "平台组"
    assert department.dingtalk_dept_path == "/1/2/3/"


async def test_sync_status_serializes_database_time_as_utc(monkeypatch, session):
    monkeypatch.setenv("DINGTALK_CORP_ID", "corp-1")
    await sync_directory(session, client=FakeDirectoryClient())

    status = await get_sync_status(session, "corp-1")

    assert status is not None
    assert status["started_at"].endswith("Z")
    assert status["completed_at"].endswith("Z")
