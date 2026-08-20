from __future__ import annotations

from typing import Any

import httpx
import pytest

from enterprise_mcp.hr_client import HrApiError, HrClient


pytestmark = pytest.mark.asyncio


class FakeResponse:
    """提供 HrClient 所需的最小 HTTP 响应。"""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body

    def json(self) -> Any:
        """返回预设 JSON 响应。"""

        return self.body


class FakeAsyncClient:
    """记录请求参数的异步 HTTP 客户端。"""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.request: tuple[str, dict[str, Any], dict[str, str]] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]):
        """记录 GET 请求并返回预设响应。"""

        self.request = (url, params, headers)
        return self.response


async def test_hr_client_adds_bearer_and_returns_data(monkeypatch):
    fake_http = FakeAsyncClient(FakeResponse(200, {"code": 200, "data": [{"id": 1}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: fake_http)
    client = HrClient("http://hr.example/api/", "test-token")

    data = await client.get("/attendance/sign-records", {"ftalkId": "001", "unused": None})

    assert data == [{"id": 1}]
    assert fake_http.request == (
        "http://hr.example/api/attendance/sign-records",
        {"ftalkId": "001"},
        {"Authorization": "Bearer test-token"},
    )


async def test_hr_client_masks_authentication_error_body(monkeypatch):
    fake_http = FakeAsyncClient(FakeResponse(401, {"message": "upstream-sensitive-body"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: fake_http)
    client = HrClient("http://hr.example/api", "test-token")

    with pytest.raises(HrApiError, match="HR API 认证失败") as error:
        await client.get("/attendance/summary")

    assert "upstream-sensitive-body" not in str(error.value)
    assert "test-token" not in str(error.value)
