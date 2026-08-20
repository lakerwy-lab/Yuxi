"""HR 考勤 API 客户端：持有 Bearer token，统一请求入口。"""

from __future__ import annotations

import os
from typing import Any

import httpx


class HrApiError(RuntimeError):
    """HR API 请求失败或返回非 200 业务码时的脱敏错误。"""

    def __init__(self, code: int | str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"HR API 错误 [{code}]: {message}")


class HrClient:
    """持有 HR 系统 Bearer token，统一封装 GET 请求。

    token 来自环境变量 HR_API_TOKEN（永久 token），不进入模型上下文、不记录到审计日志。
    """

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """调用 HR API，附加 Authorization，返回响应中的 data 字段。

        params 中值为 None 的键会被丢弃。
        """

        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, params=query, headers=headers)
        except httpx.TimeoutException as exc:
            raise HrApiError("timeout", "HR 服务请求超时") from exc
        except httpx.RequestError as exc:
            raise HrApiError("unavailable", "HR 服务暂不可用") from exc

        if resp.status_code in {401, 403}:
            raise HrApiError(resp.status_code, "HR API 认证失败")
        if resp.status_code != 200:
            raise HrApiError(resp.status_code, f"HR API HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise HrApiError("invalid_response", "HR API 返回了无效响应") from exc
        if not isinstance(body, dict):
            raise HrApiError("invalid_response", "HR API 返回了无效响应")
        code = body.get("code")
        if code != 200:
            message = str(body.get("message") or "HR API 业务请求失败")[:200]
            raise HrApiError(code, message)
        return body.get("data")


_client: HrClient | None = None


def get_hr_client() -> HrClient:
    """惰性创建并缓存全局 HR 客户端，缺失配置时显式失败。"""

    global _client
    if _client is not None:
        return _client
    base_url = os.getenv("HR_API_BASE_URL", "").strip()
    token = os.getenv("HR_API_TOKEN", "").strip()
    if not base_url or not token:
        raise RuntimeError("HR API 未配置：需要 HR_API_BASE_URL 和 HR_API_TOKEN")
    _client = HrClient(base_url, token)
    return _client
