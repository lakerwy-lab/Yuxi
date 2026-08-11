from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services import dingtalk_auth_service, oidc_service
from yuxi.storage.postgres.models_business import Department, User


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def dingtalk_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Department.__table__.create)
        await conn.run_sync(User.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def test_dingtalk_login_url_uses_native_oauth(monkeypatch):
    monkeypatch.setenv("DINGTALK_CLIENT_ID", "client-id")
    monkeypatch.setenv("DINGTALK_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "DINGTALK_OAUTH_REDIRECT_URI", "https://example.test/api/auth/dingtalk/pc/callback"
    )

    result = await dingtalk_auth_service.dingtalk_login_url_handler("/agent")
    query = parse_qs(urlparse(result["login_url"]).query)

    assert urlparse(result["login_url"]).netloc == "login.dingtalk.com"
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["https://example.test/api/auth/dingtalk/pc/callback"]
    assert query["scope"] == ["openid"]
    assert dingtalk_auth_service.dingtalk_is_configured()
    assert await oidc_service.get_oidc_config_handler() == {
        "enabled": True,
        "provider_name": "钉钉登录",
    }


async def test_get_or_create_user_binds_union_id_and_department(monkeypatch, dingtalk_session):
    monkeypatch.setenv("DINGTALK_DEFAULT_DEPARTMENT", "客服部")
    monkeypatch.setenv("DINGTALK_AUTO_CREATE_USER", "true")
    monkeypatch.setenv("DINGTALK_CORP_ID", "corp-1")

    user = await dingtalk_auth_service._get_or_create_user(
        {
            "user_id": "user-1",
            "union_id": "union-1",
            "name": "张三",
            "avatar": "https://example.test/avatar.png",
        },
        dingtalk_session,
    )

    assert user.uid == "dingtalk:corp-1:union-1"
    assert user.dingtalk_corp_id == "corp-1"
    assert user.dingtalk_union_id == "union-1"
    assert user.dingtalk_user_id == "user-1"
    assert user.username == "张三"
    assert user.department_id is not None
    assert user.avatar == "https://example.test/avatar.png"

    second = await dingtalk_auth_service._get_or_create_user(
        {
            "user_id": "user-1",
            "union_id": "union-1",
            "name": "张三",
            "avatar": "",
        },
        dingtalk_session,
    )

    assert second.id == user.id


async def test_dingtalk_identity_is_scoped_by_corp(monkeypatch, dingtalk_session):
    monkeypatch.setenv("DINGTALK_AUTO_CREATE_USER", "true")

    monkeypatch.setenv("DINGTALK_CORP_ID", "corp-1")
    first = await dingtalk_auth_service._get_or_create_user(
        {"user_id": "user-1", "union_id": "same-union", "name": "张三", "avatar": ""},
        dingtalk_session,
    )

    monkeypatch.setenv("DINGTALK_CORP_ID", "corp-2")
    second = await dingtalk_auth_service._get_or_create_user(
        {"user_id": "user-2", "union_id": "same-union", "name": "张三", "avatar": ""},
        dingtalk_session,
    )

    assert first.id != second.id
    assert first.dingtalk_corp_id == "corp-1"
    assert second.dingtalk_corp_id == "corp-2"


async def test_dingtalk_legacy_requests_pass_headers_argument(monkeypatch):
    client = dingtalk_auth_service.DingTalkAuthClient()
    calls = []

    async def fake_get_access_token():
        return "access-token"

    async def fake_request_json(url, *, json, headers, method="POST", params=None):
        calls.append({"url": url, "json": json, "headers": headers, "params": params})
        if url.endswith("getuserinfo"):
            return {"result": {"userid": "user-1"}}
        if url.endswith("getbyunionid"):
            return {"result": {"userid": "user-1"}}
        return {"result": {"name": "张三", "unionid": "union-1"}}

    monkeypatch.setattr(client, "get_access_token", fake_get_access_token)
    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert (await client.get_h5_user("auth-code"))["userid"] == "user-1"
    assert await client.get_user_id_by_union_id("union-1") == "user-1"
    assert (await client.get_user_detail("user-1"))["name"] == "张三"

    assert len(calls) == 3
    assert all(call["headers"] is None for call in calls)
