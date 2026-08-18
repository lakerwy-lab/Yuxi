"""钉钉 AI Card 创建、投放、流式更新与结束态客户端。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from yuxi.channels.dingtalk.message_adapter import DingTalkTarget

DINGTALK_API_BASE_URL = "https://api.dingtalk.com"
CARD_API_INTERVAL_SECONDS = 0.05
CARD_QPS_BACKOFF_SECONDS = 2.0


class DingTalkCardError(RuntimeError):
    """钉钉 AI Card API 调用失败。"""


@dataclass(slots=True)
class DingTalkCardHandle:
    """一张已投放 AI Card 的运行状态。"""

    out_track_id: str
    inputing_started: bool = False


class CardApiRateLimiter:
    """进程内 Card API 串行预约器，限制总调用速率。"""

    def __init__(self, interval_seconds: float = CARD_API_INTERVAL_SECONDS) -> None:
        self._interval_seconds = interval_seconds
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def wait(self) -> None:
        """等待当前调用的预约时间。"""

        async with self._lock:
            now = time.monotonic()
            scheduled_at = max(now, self._next_allowed_at)
            self._next_allowed_at = scheduled_at + self._interval_seconds
        delay = scheduled_at - now
        if delay > 0:
            await asyncio.sleep(delay)


class DingTalkCardClient:
    """使用显式 HTTP 请求管理钉钉 AI Card 生命周期。"""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        robot_code: str,
        card_template_id: str,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: CardApiRateLimiter | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._robot_code = robot_code
        self._card_template_id = card_template_id
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=DINGTALK_API_BASE_URL, timeout=30.0)
        self._rate_limiter = rate_limiter or CardApiRateLimiter()
        self._access_token = ""
        self._token_expires_at = 0.0

    async def create_and_deliver(self, target: DingTalkTarget) -> DingTalkCardHandle:
        """创建并投放一张初始 AI Card。"""

        out_track_id = f"yuxi_{uuid.uuid4().hex}"
        create_body = {
            "cardTemplateId": self._card_template_id,
            "outTrackId": out_track_id,
            "cardData": {"cardParamMap": {"config": json.dumps({"autoLayout": True})}},
            "callbackType": "STREAM",
            "imGroupOpenSpaceModel": {"supportForward": True},
            "imRobotOpenSpaceModel": {"supportForward": True},
        }
        await self._card_request("POST", "/v1.0/card/instances", json_body=create_body)

        deliver_body: dict[str, Any] = {"outTrackId": out_track_id, "userIdType": 1}
        if target.kind == "group":
            deliver_body.update(
                openSpaceId=f"dtv1.card//IM_GROUP.{target.target_id}",
                imGroupOpenDeliverModel={"robotCode": self._robot_code},
            )
        else:
            deliver_body.update(
                openSpaceId=f"dtv1.card//IM_ROBOT.{target.target_id}",
                imRobotOpenDeliverModel={
                    "spaceType": "IM_ROBOT",
                    "robotCode": self._robot_code,
                    "extension": {"dynamicSummary": "true"},
                },
            )
        await self._card_request("POST", "/v1.0/card/instances/deliver", json_body=deliver_body)
        return DingTalkCardHandle(out_track_id=out_track_id)

    async def stream(self, card: DingTalkCardHandle, content: str, *, finalize: bool = False) -> None:
        """用全量替换模式更新 Card 正文。"""

        normalized = content if finalize else content.rstrip("\n")
        if not card.inputing_started:
            # 首帧：切换 INPUTING 状态，msgContent 必须非空
            await self._put_card_data(card.out_track_id, content=normalized or "正在思考...", flow_status="2")
            card.inputing_started = True

        await self._card_request(
            "PUT",
            "/v1.0/card/streaming",
            json_body={
                "outTrackId": card.out_track_id,
                "guid": uuid.uuid4().hex,
                "key": "msgContent",
                "content": normalized,
                "isFull": True,
                "isFinalize": finalize,
                "isError": False,
            },
        )

    async def finish(self, card: DingTalkCardHandle, content: str) -> None:
        """写入最终正文并结束 Card loading 状态。"""

        final_content = content.strip() or "已处理完成，但没有返回内容。"
        await self.stream(card, final_content, finalize=True)
        await self._put_card_data(card.out_track_id, content=final_content, flow_status="3", final=True)

    async def fail(self, card: DingTalkCardHandle, message: str) -> None:
        """将 Card 标记为失败态。"""

        await self._put_card_data(card.out_track_id, content=message, flow_status="5", final=True)

    async def close(self) -> None:
        """关闭内部创建的 HTTP 客户端。"""

        if self._owns_client:
            await self._client.aclose()

    async def _put_card_data(
        self,
        out_track_id: str,
        *,
        content: str,
        flow_status: str,
        final: bool = False,
    ) -> None:
        card_param_map = {
            "flowStatus": flow_status,
            "msgContent": content,
            "staticMsgContent": "",
            "sys_full_json_obj": json.dumps({"order": ["msgContent"]}),
            "config": json.dumps({"autoLayout": True}),
        }
        body: dict[str, Any] = {
            "outTrackId": out_track_id,
            "cardData": {"cardParamMap": card_param_map},
        }
        if final:
            body["cardUpdateOptions"] = {"updateCardDataByKey": True}
        await self._card_request("PUT", "/v1.0/card/instances", json_body=body)

    async def _card_request(self, method: str, path: str, *, json_body: dict[str, Any]) -> httpx.Response:
        token = await self._get_access_token()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

        await self._rate_limiter.wait()
        response = await self._client.request(method, path, json=json_body, headers=headers)
        if _is_qps_limit(response):
            await asyncio.sleep(CARD_QPS_BACKOFF_SECONDS)
            await self._rate_limiter.wait()
            response = await self._client.request(method, path, json=json_body, headers=headers)
        if response.is_error:
            request_id = response.headers.get("x-acs-request-id", "")
            detail = _response_error(response)
            raise DingTalkCardError(
                f"钉钉 Card API 失败: {method} {path}, status={response.status_code}, "
                f"request_id={request_id or '-'}, detail={detail}"
            )
        return response

    async def _get_access_token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at - 300:
            return self._access_token

        response = await self._client.post(
            "/v1.0/oauth2/accessToken",
            json={"appKey": self._client_id, "appSecret": self._client_secret},
        )
        if response.is_error:
            raise DingTalkCardError(f"获取钉钉 access token 失败: status={response.status_code}")
        payload = response.json()
        token = str(payload.get("accessToken") or "").strip()
        if not token:
            raise DingTalkCardError("获取钉钉 access token 失败: 响应缺少 accessToken")

        expires_in = int(payload.get("expireIn") or 7200)
        self._access_token = token
        self._token_expires_at = now + expires_in
        return token


def _is_qps_limit(response: httpx.Response) -> bool:
    """判断钉钉 QPS 限流响应。"""

    return response.status_code == 403 and "qpslimit" in response.text.lower()


def _response_error(response: httpx.Response) -> str:
    """提取短小且不包含请求凭证的错误信息。"""

    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if not isinstance(payload, dict):
        return str(payload)[:300]
    return str(payload.get("message") or payload.get("errmsg") or payload.get("code") or payload)[:300]
