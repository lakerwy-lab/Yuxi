import json

import httpx
import pytest
from yuxi.channels.dingtalk import card_client as card_client_module
from yuxi.channels.dingtalk.card_client import (
    CardApiRateLimiter,
    DingTalkCardClient,
    DingTalkCardError,
)
from yuxi.channels.dingtalk.message_adapter import DingTalkTarget


@pytest.mark.asyncio
async def test_card_client_runs_explicit_card_lifecycle():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body, dict(request.headers)))
        if request.url.path.endswith("/oauth2/accessToken"):
            return httpx.Response(200, json={"accessToken": "token", "expireIn": 7200})
        return httpx.Response(200, json={})

    http_client = httpx.AsyncClient(
        base_url="https://api.dingtalk.com",
        transport=httpx.MockTransport(handler),
    )
    client = DingTalkCardClient(
        client_id="client",
        client_secret="secret",
        robot_code="robot-1",
        card_template_id="template.schema",
        http_client=http_client,
        rate_limiter=CardApiRateLimiter(0),
    )

    card = await client.create_and_deliver(DingTalkTarget(kind="group", target_id="group-1"))
    await client.stream(card, "第一段")
    await client.finish(card, "最终答案")
    await http_client.aclose()

    assert [item[:2] for item in requests] == [
        ("POST", "/v1.0/oauth2/accessToken"),
        ("POST", "/v1.0/card/instances"),
        ("POST", "/v1.0/card/instances/deliver"),
        ("PUT", "/v1.0/card/instances"),
        ("PUT", "/v1.0/card/streaming"),
        ("PUT", "/v1.0/card/streaming"),
        ("PUT", "/v1.0/card/instances"),
    ]
    assert requests[1][2]["cardTemplateId"] == "template.schema"
    assert requests[2][2]["openSpaceId"] == "dtv1.card//IM_GROUP.group-1"
    assert requests[2][2]["imGroupOpenDeliverModel"]["robotCode"] == "robot-1"
    assert requests[3][2]["cardData"]["cardParamMap"]["flowStatus"] == "2"
    assert requests[4][2]["isFinalize"] is False
    assert requests[5][2]["isFinalize"] is True
    assert requests[6][2]["cardData"]["cardParamMap"]["flowStatus"] == "3"
    assert requests[1][3]["x-acs-dingtalk-access-token"] == "token"


@pytest.mark.asyncio
async def test_card_client_error_contains_request_id_without_credentials():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/accessToken"):
            return httpx.Response(200, json={"accessToken": "token", "expireIn": 7200})
        return httpx.Response(
            400,
            headers={"x-acs-request-id": "request-1"},
            json={"code": "InvalidParameter", "message": "bad card"},
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.dingtalk.com",
        transport=httpx.MockTransport(handler),
    )
    client = DingTalkCardClient(
        client_id="client",
        client_secret="secret",
        robot_code="robot-1",
        card_template_id="template.schema",
        http_client=http_client,
        rate_limiter=CardApiRateLimiter(0),
    )

    with pytest.raises(DingTalkCardError, match="request_id=request-1") as exc_info:
        await client.create_and_deliver(DingTalkTarget(kind="direct", target_id="staff-1"))
    await http_client.aclose()

    assert "secret" not in str(exc_info.value)
    assert "token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_card_client_retries_qps_limit_once(monkeypatch: pytest.MonkeyPatch):
    card_create_calls = 0
    sleeps = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal card_create_calls
        if request.url.path.endswith("/oauth2/accessToken"):
            return httpx.Response(200, json={"accessToken": "token", "expireIn": 7200})
        if request.url.path == "/v1.0/card/instances" and request.method == "POST":
            card_create_calls += 1
            if card_create_calls == 1:
                return httpx.Response(403, json={"code": "QpsLimit"})
        return httpx.Response(200, json={})

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(card_client_module.asyncio, "sleep", fake_sleep)
    http_client = httpx.AsyncClient(
        base_url="https://api.dingtalk.com",
        transport=httpx.MockTransport(handler),
    )
    client = DingTalkCardClient(
        client_id="client",
        client_secret="secret",
        robot_code="robot-1",
        card_template_id="template.schema",
        http_client=http_client,
        rate_limiter=CardApiRateLimiter(0),
    )

    await client.create_and_deliver(DingTalkTarget(kind="group", target_id="group-1"))
    await http_client.aclose()

    assert card_create_calls == 2
    assert sleeps == [2.0]
