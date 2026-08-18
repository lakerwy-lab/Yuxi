import httpx
import pytest
from yuxi.channels.yuxi_channel_client import YuxiChannelClient


@pytest.mark.asyncio
async def test_stream_run_events_parses_sse_and_sends_resume_cursor():
    captured_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                "id: 2-0\n"
                "event: messages\n"
                'data: {"payload":{"items":[]}}\n\n'
                "id: 3-0\n"
                "event: end\n"
                'data: {"payload":{"status":"completed"}}\n\n'
            ),
        )

    http_client = httpx.AsyncClient(
        base_url="http://api:5050/api",
        transport=httpx.MockTransport(handler),
    )
    client = YuxiChannelClient(base_url="unused", gateway_token="gateway", http_client=http_client)

    events = [
        event
        async for event in client.stream_run_events(
            run_id="run-1",
            channel="dingtalk_bot",
            account_id="robot-1",
            after_seq="1-0",
        )
    ]
    await http_client.aclose()

    assert [(event.event, event.event_id) for event in events] == [("messages", "2-0"), ("end", "3-0")]
    assert events[1].data["payload"]["status"] == "completed"
    assert captured_headers["authorization"] == "Bearer gateway"
    assert captured_headers["last-event-id"] == "1-0"
