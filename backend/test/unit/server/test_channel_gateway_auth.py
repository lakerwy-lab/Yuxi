"""Channel Gateway 服务认证测试。"""

import pytest
from fastapi import HTTPException
from yuxi.utils.channel_auth import resolve_channel_gateway_token

from server.utils import auth_middleware

pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_channel_gateway_accepts_matching_token(monkeypatch: pytest.MonkeyPatch):
    """完全匹配的 Bearer token 通过认证。"""

    monkeypatch.setattr(auth_middleware, "resolve_channel_gateway_token", lambda: "service-token")
    assert await auth_middleware.require_channel_gateway("Bearer service-token") is None


@pytest.mark.asyncio
async def test_channel_gateway_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch):
    """错误 token 返回 401。"""

    monkeypatch.setattr(auth_middleware, "resolve_channel_gateway_token", lambda: "service-token")

    with pytest.raises(HTTPException) as exc:
        await auth_middleware.require_channel_gateway("Bearer wrong-token")

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_channel_gateway_reports_missing_production_config(monkeypatch: pytest.MonkeyPatch):
    """生产环境缺少服务凭证时显式返回不可用。"""

    def fail_config():
        raise ValueError("生产环境必须配置 YUXI_CHANNEL_GATEWAY_TOKEN")

    monkeypatch.setattr(auth_middleware, "resolve_channel_gateway_token", fail_config)

    with pytest.raises(HTTPException) as exc:
        await auth_middleware.require_channel_gateway("Bearer anything")

    assert exc.value.status_code == 503


def test_channel_gateway_token_requires_minimum_length(monkeypatch: pytest.MonkeyPatch):
    """显式服务凭证过短时拒绝启动，避免弱共享密钥。"""

    monkeypatch.setenv("YUXI_CHANNEL_GATEWAY_TOKEN", "too-short")

    with pytest.raises(ValueError, match="不能少于 32"):
        resolve_channel_gateway_token()
