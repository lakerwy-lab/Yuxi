"""钉钉原生 OAuth 认证适配。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


FRONTEND_CALLBACK_PATH = "/auth/oidc/callback"
FRONTEND_LOGIN_PATH = "/login"
_DINGTALK_LEGACY_API_BASE_URL = "https://oapi.dingtalk.com"


class DingTalkAuthError(RuntimeError):
    """钉钉认证接口调用失败。"""


class DingTalkAuthConfig:
    """读取钉钉登录所需的非持久化配置。"""

    def __init__(self) -> None:
        self.client_id = os.environ.get("DINGTALK_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("DINGTALK_CLIENT_SECRET", "").strip()
        self.corp_id = os.environ.get("DINGTALK_CORP_ID", "").strip()
        self.api_base_url = os.environ.get("DINGTALK_API_BASE_URL", "https://api.dingtalk.com").strip()
        self.redirect_uri = os.environ.get("DINGTALK_OAUTH_REDIRECT_URI", "").strip()
        if not self.redirect_uri:
            public_url = os.environ.get("APP_PUBLIC_URL", "").strip().rstrip("/")
            if public_url:
                self.redirect_uri = f"{public_url}/api/auth/dingtalk/pc/callback"
        self.provider_name = os.environ.get("DINGTALK_PROVIDER_NAME", "钉钉登录").strip()
        self.default_department = os.environ.get("DINGTALK_DEFAULT_DEPARTMENT", "钉钉用户").strip()
        self.default_role = os.environ.get("DINGTALK_DEFAULT_ROLE", "user").strip()
        self.auto_create_user = os.environ.get("DINGTALK_AUTO_CREATE_USER", "true").lower() == "true"
        try:
            self.directory_sync_interval_seconds = max(
                int(os.environ.get("DINGTALK_DIRECTORY_SYNC_INTERVAL_SECONDS", "3600")),
                0,
            )
        except ValueError:
            self.directory_sync_interval_seconds = 3600

    @property
    def is_configured(self) -> bool:
        """判断 PC OAuth 是否具备完整配置。"""
        return bool(self.client_id and self.client_secret and self.redirect_uri)


def get_dingtalk_auth_config() -> DingTalkAuthConfig:
    """读取当前进程中的钉钉登录配置。"""
    return DingTalkAuthConfig()


def dingtalk_is_configured() -> bool:
    """判断是否应把登录页的第三方登录指向钉钉。"""
    return get_dingtalk_auth_config().is_configured


class DingTalkAuthClient:
    """封装钉钉 PC OAuth 与用户身份查询接口。"""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._access_token_lock = asyncio.Lock()
        self._request_rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def exchange_pc_auth_code(self, auth_code: str) -> dict[str, Any]:
        """用 PC OAuth 授权码兑换用户访问令牌。"""
        config = get_dingtalk_auth_config()
        if not config.client_id or not config.client_secret:
            raise DingTalkAuthError("钉钉 client_id 或 client_secret 未配置")
        return await self._request_json(
            f"{config.api_base_url.rstrip('/')}/v1.0/oauth2/userAccessToken",
            json={
                "clientId": config.client_id,
                "clientSecret": config.client_secret,
                "code": auth_code,
                "grantType": "authorization_code",
            },
            headers=None,
        )

    async def get_current_user(self, user_access_token: str) -> dict[str, Any]:
        """通过用户令牌获取 unionId 与昵称。"""
        config = get_dingtalk_auth_config()
        return await self._request_json(
            f"{config.api_base_url.rstrip('/')}/v1.0/contact/users/me",
            json=None,
            headers={"x-acs-dingtalk-access-token": user_access_token},
            method="GET",
        )

    async def get_h5_user(self, auth_code: str) -> dict[str, Any]:
        """通过 H5 免登授权码获取用户身份。"""
        token = await self.get_access_token()
        body = await self._request_json(
            f"{_DINGTALK_LEGACY_API_BASE_URL}/topapi/v2/user/getuserinfo",
            json={"code": auth_code},
            params={"access_token": token},
            headers=None,
        )
        result = body.get("result")
        if not isinstance(result, dict):
            raise DingTalkAuthError("钉钉 H5 免登未返回用户信息")
        return result

    async def get_user_id_by_union_id(self, union_id: str) -> str:
        """通过 unionId 查询企业内 userId。"""
        token = await self.get_access_token()
        body = await self._request_json(
            f"{_DINGTALK_LEGACY_API_BASE_URL}/topapi/user/getbyunionid",
            json={"unionid": union_id},
            params={"access_token": token},
            headers=None,
        )
        result = body.get("result")
        user_id = result.get("userid") if isinstance(result, dict) else result
        if not isinstance(user_id, str) or not user_id:
            raise DingTalkAuthError("钉钉未返回企业用户 ID")
        return user_id

    async def get_user_detail(self, user_id: str) -> dict[str, Any]:
        """通过 userId 获取姓名、头像和 unionId。"""
        token = await self.get_access_token()
        body = await self._request_json(
            f"{_DINGTALK_LEGACY_API_BASE_URL}/topapi/v2/user/get",
            json={"userid": user_id},
            params={"access_token": token},
            headers=None,
        )
        result = body.get("result")
        if not isinstance(result, dict):
            raise DingTalkAuthError("钉钉未返回用户详情")
        return result

    async def list_sub_departments(self, dept_id: str) -> dict[str, Any]:
        """读取一个部门的直接子部门。"""
        token = await self.get_access_token()
        return await self._request_json(
            f"{_DINGTALK_LEGACY_API_BASE_URL}/topapi/v2/department/listsub",
            json={"dept_id": int(dept_id) if str(dept_id).isdigit() else dept_id},
            params={"access_token": token},
            headers=None,
        )

    async def list_department_users(self, dept_id: str, *, cursor: int = 0, size: int = 100) -> dict[str, Any]:
        """分页读取部门成员。"""
        token = await self.get_access_token()
        return await self._request_json(
            f"{_DINGTALK_LEGACY_API_BASE_URL}/topapi/v2/user/list",
            json={
                "dept_id": int(dept_id) if str(dept_id).isdigit() else dept_id,
                "cursor": cursor,
                "size": min(max(size, 1), 100),
                "contain_access_limit": True,
            },
            params={"access_token": token},
            headers=None,
        )

    async def get_access_token(self) -> str:
        """获取并缓存企业内部应用 access token。"""
        if self._access_token and time.time() < self._access_token_expires_at - 300:
            return self._access_token

        async with self._access_token_lock:
            if self._access_token and time.time() < self._access_token_expires_at - 300:
                return self._access_token

            config = get_dingtalk_auth_config()
            if not config.client_id or not config.client_secret:
                raise DingTalkAuthError("钉钉 client_id 或 client_secret 未配置")
            body = await self._request_json(
                f"{config.api_base_url.rstrip('/')}/v1.0/oauth2/accessToken",
                json={"appKey": config.client_id, "appSecret": config.client_secret},
                headers=None,
            )
            access_token = body.get("accessToken")
            if not isinstance(access_token, str) or not access_token:
                raise DingTalkAuthError("钉钉 token 响应缺少 accessToken")
            self._access_token = access_token
            self._access_token_expires_at = time.time() + int(body.get("expireIn", 7200))
            return access_token

    async def _request_json(
        self,
        url: str,
        *,
        json: dict[str, Any] | None,
        headers: dict[str, str] | None,
        method: str = "POST",
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """发送钉钉请求并统一校验 HTTP 与业务错误。"""
        retry_delays = (0, 1, 2, 4, 8, 16, 32)
        transient_error_codes = {88, 90002, 90018}
        last_error: Exception | None = None
        for attempt, delay in enumerate(retry_delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._wait_for_request_slot()
                async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                    response = await client.request(method, url, json=json, params=params, headers=headers)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError("钉钉接口暂时不可用", request=response.request, response=response)
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < len(retry_delays) - 1:
                    continue
                raise DingTalkAuthError("钉钉认证接口请求失败") from exc

            if not isinstance(body, dict):
                raise DingTalkAuthError("钉钉认证接口返回格式错误")
            if body.get("errcode", 0) in transient_error_codes and attempt < len(retry_delays) - 1:
                continue
            if body.get("errcode", 0) != 0:
                raise DingTalkAuthError(str(body.get("errmsg") or "钉钉认证接口返回业务错误"))
            return body

        raise DingTalkAuthError("钉钉认证接口请求失败") from last_error

    async def _wait_for_request_slot(self) -> None:
        """将同一进程的钉钉请求限制在约 3 QPS，避免大通讯录触发企业级限流。"""

        async with self._request_rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < 0.35:
                await asyncio.sleep(0.35 - elapsed)
            self._last_request_at = time.monotonic()


dingtalk_auth_client = DingTalkAuthClient()


async def dingtalk_login_url_handler(redirect_path: str = "/") -> dict[str, str]:
    """生成钉钉 PC OAuth 登录地址。"""
    config = get_dingtalk_auth_config()
    if not config.is_configured:
        raise HTTPException(status_code=503, detail="钉钉 OAuth 登录暂不可用，请联系管理员")

    oidc_utils = _get_oidc_utils()
    state = oidc_utils.generate_state(redirect_path)
    query = urlencode(
        {
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "client_id": config.client_id,
            "scope": "openid",
            "state": state,
            "prompt": "consent",
        }
    )
    return {"login_url": f"https://login.dingtalk.com/oauth2/auth?{query}"}


async def dingtalk_callback_handler(
    auth_code: str,
    state: str,
    db: AsyncSession,
    request: Request | None = None,
) -> RedirectResponse:
    """处理钉钉 PC OAuth 回调并跳转到 Yuxi 前端回调页。"""
    oidc_utils = _get_oidc_utils()
    if not oidc_utils.verify_state(state):
        return _redirect_to_login_with_error("登录会话已过期，请返回登录页重试")

    try:
        identity = await _resolve_pc_identity(auth_code)
        user = await _get_or_create_user(identity, db)
        response_data = await _build_token_response(user, db)
        await _log_login(db, user.id, request)
    except HTTPException:
        raise
    except DingTalkAuthError as exc:
        logger.warning("钉钉登录失败: %s", exc)
        return _redirect_to_login_with_error("钉钉身份解析失败，请稍后重试")

    exchange_code = oidc_utils.generate_login_code(response_data)
    return RedirectResponse(
        url=f"{FRONTEND_CALLBACK_PATH}?{urlencode({'code': exchange_code})}",
        status_code=302,
    )


async def dingtalk_h5_login_handler(
    auth_code: str,
    db: AsyncSession,
    request: Request | None = None,
) -> dict[str, Any]:
    """处理钉钉 H5 免登并返回 Yuxi JWT。"""
    try:
        identity = await _resolve_h5_identity(auth_code)
        user = await _get_or_create_user(identity, db)
        response_data = await _build_token_response(user, db)
        await _log_login(db, user.id, request)
        return response_data
    except HTTPException:
        raise
    except DingTalkAuthError as exc:
        logger.warning("钉钉 H5 登录失败: %s", exc)
        raise HTTPException(status_code=502, detail="钉钉身份解析失败，请稍后重试") from exc


async def _resolve_pc_identity(auth_code: str) -> dict[str, str]:
    """完成 PC OAuth 授权码到用户身份的解析。"""
    token_data = await dingtalk_auth_client.exchange_pc_auth_code(auth_code)
    user_access_token = token_data.get("accessToken")
    if not isinstance(user_access_token, str) or not user_access_token:
        raise DingTalkAuthError("钉钉 userAccessToken 响应缺少 accessToken")

    current_user = await dingtalk_auth_client.get_current_user(user_access_token)
    union_id = str(current_user.get("unionId") or current_user.get("unionid") or "")
    if not union_id:
        raise DingTalkAuthError("钉钉当前用户信息缺少 unionId")

    user_id = await dingtalk_auth_client.get_user_id_by_union_id(union_id)
    detail = await dingtalk_auth_client.get_user_detail(user_id)
    return {
        "user_id": user_id,
        "union_id": str(detail.get("unionid") or detail.get("unionId") or union_id),
        "name": str(detail.get("name") or current_user.get("nick") or user_id),
        "avatar": str(current_user.get("avatarUrl") or ""),
    }


async def _resolve_h5_identity(auth_code: str) -> dict[str, str]:
    """完成 H5 免登授权码到用户身份的解析。"""
    current_user = await dingtalk_auth_client.get_h5_user(auth_code)
    user_id = str(current_user.get("userid") or current_user.get("userId") or "")
    if not user_id:
        raise DingTalkAuthError("钉钉 H5 用户信息缺少 userId")

    detail = await dingtalk_auth_client.get_user_detail(user_id)
    return {
        "user_id": user_id,
        "union_id": str(
            current_user.get("unionid")
            or current_user.get("unionId")
            or detail.get("unionid")
            or detail.get("unionId")
            or ""
        ),
        "name": str(detail.get("name") or current_user.get("name") or user_id),
        "avatar": "",
    }


async def _get_or_create_user(identity: dict[str, str], db: AsyncSession) -> User:
    """按 unionId 查找或创建 Yuxi 用户。"""
    union_id = identity["union_id"].strip()
    if not union_id:
        raise DingTalkAuthError("钉钉用户缺少 unionId，无法建立稳定账号")

    config = get_dingtalk_auth_config()
    corp_id = config.corp_id or None
    scoped_uid = f"dingtalk:{corp_id}:{union_id}" if corp_id else f"dingtalk:{union_id}"

    if corp_id:
        result = await db.execute(
            select(User).where(
                User.dingtalk_corp_id == corp_id,
                User.dingtalk_union_id == union_id,
            )
        )
        user = result.scalar_one_or_none()
    else:
        user = None

    if user is None:
        result = await db.execute(select(User).where(User.uid == scoped_uid))
        user = result.scalar_one_or_none()

    # 兼容启用 corp 隔离前已创建的 dingtalk:<unionId> 账号，只回填身份字段，不强制改 uid。
    if user is None and corp_id:
        result = await db.execute(select(User).where(User.uid == f"dingtalk:{union_id}"))
        user = result.scalar_one_or_none()

    if user is not None and user.dingtalk_corp_id not in (None, corp_id):
        raise DingTalkAuthError("钉钉用户已绑定其他企业，拒绝跨企业复用账号")

    if user is None:
        if not config.auto_create_user:
            raise HTTPException(status_code=403, detail="用户未注册，请联系管理员开通账号")
        department_id = await _get_or_create_department(db, config.default_department)
        user = User(
            username=await _build_unique_username(db, identity["name"], union_id),
            uid=scoped_uid,
            password_hash=AuthUtils.hash_password(secrets.token_urlsafe(32)),
            role=config.default_role if config.default_role in {"user", "admin", "superadmin"} else "user",
            department_id=department_id,
            dingtalk_corp_id=corp_id,
            dingtalk_union_id=union_id,
            dingtalk_user_id=identity.get("user_id") or None,
        )
        db.add(user)
    else:
        user.dingtalk_corp_id = corp_id
        user.dingtalk_union_id = union_id
        if identity.get("user_id"):
            user.dingtalk_user_id = identity["user_id"]
        if user.is_deleted:
            user.is_deleted = 0
            user.deleted_at = None
            if user.username.startswith("已注销用户-"):
                user.username = await _build_unique_username(db, identity["name"], union_id)
        if user.department_id is None:
            user.department_id = await _get_or_create_department(db, config.default_department)

    if identity.get("avatar"):
        user.avatar = identity["avatar"]
    user.last_login = utc_now_naive()
    await db.commit()
    await db.refresh(user)
    return user


async def _get_or_create_department(db: AsyncSession, name: str) -> int:
    """获取默认部门，不存在时创建。"""
    department_name = name or "钉钉用户"
    result = await db.execute(select(Department).where(Department.name == department_name))
    department = result.scalar_one_or_none()
    if department is None:
        department = Department(name=department_name, description="钉钉登录用户")
        db.add(department)
        await db.flush()
    return department.id


async def _build_unique_username(db: AsyncSession, preferred_name: str, union_id: str) -> str:
    """为钉钉用户生成不冲突的显示名。"""
    base_name = " ".join(preferred_name.split())[:100] or f"钉钉用户-{union_id[:8]}"
    result = await db.execute(select(User.id).where(User.username == base_name))
    if result.scalar_one_or_none() is None:
        return base_name

    suffix = hashlib.sha256(union_id.encode()).hexdigest()[:8]
    candidate = f"{base_name[:91]}-{suffix}"
    result = await db.execute(select(User.id).where(User.username == candidate))
    if result.scalar_one_or_none() is None:
        return candidate
    return f"{candidate}-{secrets.token_hex(2)}"


async def _build_token_response(user: User, db: AsyncSession) -> dict[str, Any]:
    """构造与 Yuxi 现有登录接口一致的 JWT 响应。"""
    result = await db.execute(select(Department.name).where(Department.id == user.department_id))
    department_name = result.scalar_one_or_none()
    return {
        "access_token": AuthUtils.create_access_token({"sub": str(user.id)}),
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "uid": user.uid,
        "phone_number": user.phone_number,
        "avatar": user.avatar,
        "role": user.role,
        "department_id": user.department_id,
        "department_name": department_name,
    }


async def _log_login(db: AsyncSession, user_id: int, request: Request | None) -> None:
    """记录钉钉登录操作。"""
    from yuxi.services.operation_log_service import log_operation

    await log_operation(db, user_id, "钉钉登录", request=request)


def _redirect_to_login_with_error(error_message: str) -> RedirectResponse:
    """将认证错误安全地带回登录页。"""
    url = f"{FRONTEND_LOGIN_PATH}?{urlencode({'oidc_error': error_message})}"
    return RedirectResponse(url=url, status_code=302)


def _get_oidc_utils():
    """延迟获取 OIDC 工具，避免认证模块循环导入。"""
    from yuxi.services.oidc_service import OIDCUtils

    return OIDCUtils
