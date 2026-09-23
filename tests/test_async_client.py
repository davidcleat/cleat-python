"""The async client, exercised with asyncio.run so the suite needs no pytest plugin."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from cleat import (
    AsyncCleatClient,
    CleatConfigurationError,
    CleatTimeoutError,
    KeyExpiredError,
    NotFoundError,
    RateLimitError,
)
from conftest import (
    API_KEY,
    LINE_ID,
    json_response,
    line_payload,
    make_async_client,
    message_payload,
)


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(CleatConfigurationError):
        AsyncCleatClient()


def test_api_key_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLEAT_API_KEY", "clt_example_from_env")

    async def main() -> str:
        async with AsyncCleatClient() as client:
            return client.api_key

    assert asyncio.run(main()) == "clt_example_from_env"


def test_list_lines() -> None:
    client, recorder = make_async_client(json_response(200, {"data": [line_payload()]}))

    async def main() -> list:
        async with client:
            return await client.list_lines()

    lines = asyncio.run(main())
    assert lines[0].phone_e164 == "+13055550100"
    assert recorder.last.headers["authorization"] == f"Bearer {API_KEY}"


def test_list_messages_serialises_parameters() -> None:
    client, recorder = make_async_client(json_response(200, {"data": [message_payload()]}))

    async def main() -> list:
        async with client:
            return await client.list_messages(
                LINE_ID, after=datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc), limit=10
            )

    messages = asyncio.run(main())
    assert messages[0].code == "704118"
    assert recorder.last.url.params["after"] == "2026-09-23T09:30:00Z"
    assert recorder.last.url.params["limit"] == "10"


def test_wait_for_code_polls_until_a_code_arrives() -> None:
    client, recorder = make_async_client(
        json_response(200, {"data": []}),
        json_response(200, {"data": [message_payload(id="m1", code=None, receivedAt="2026-09-23T10:00:02.000Z")]}),
        json_response(200, {"data": [message_payload(id="m2", code="551201", receivedAt="2026-09-23T10:00:07.000Z")]}),
    )

    async def main():
        async with client:
            return await client.wait_for_code(
                LINE_ID, since="2026-09-23T10:00:00Z", timeout=30, poll_interval=2
            )

    code = asyncio.run(main())
    assert code == "551201"
    assert isinstance(code, str)
    assert recorder.count == 3
    assert client.slept == [2, 2]
    assert recorder.requests[2].url.params["after"] == "2026-09-23T10:00:02.000Z"


def test_wait_for_message_returns_the_whole_message() -> None:
    client, _ = make_async_client(
        json_response(200, {"data": [message_payload(id="m2", code="551201")]})
    )

    async def main():
        async with client:
            return await client.wait_for_message(LINE_ID, timeout=5)

    message = asyncio.run(main())
    assert message.id == "m2"
    assert message.body == "Your Facebook code is 704118"


def test_wait_for_code_filters_and_times_out() -> None:
    client, _ = make_async_client(
        json_response(200, {"data": [message_payload(id="m1", code="111111", **{"from": "99999"})]})
    )

    async def main() -> None:
        async with client:
            await client.wait_for_code(LINE_ID, from_="22395", timeout=0)

    with pytest.raises(CleatTimeoutError):
        asyncio.run(main())


def test_errors_map_the_same_way() -> None:
    client, _ = make_async_client(json_response(404, {"error": "No line with that id."}))

    async def main() -> None:
        async with client:
            await client.list_messages(LINE_ID)

    with pytest.raises(NotFoundError):
        asyncio.run(main())


def test_key_expired_maps_on_the_async_client_too() -> None:
    client, _ = make_async_client(json_response(401, {"error": "This key expired.", "code": "key_expired"}))

    async def main() -> None:
        async with client:
            await client.list_lines()

    with pytest.raises(KeyExpiredError):
        asyncio.run(main())


def test_429_retries_then_succeeds() -> None:
    client, recorder = make_async_client(
        json_response(429, {"error": "Too many requests."}),
        json_response(200, {"data": [line_payload()]}),
    )

    async def main() -> list:
        async with client:
            return await client.list_lines()

    lines = asyncio.run(main())
    assert len(lines) == 1
    assert recorder.count == 2
    assert len(client.slept) == 1


def test_429_raises_once_retries_are_exhausted() -> None:
    client, recorder = make_async_client(json_response(429, {"error": "Too many requests."}), max_retries=1)

    async def main() -> None:
        async with client:
            await client.list_lines()

    with pytest.raises(RateLimitError):
        asyncio.run(main())
    assert recorder.count == 2
    assert len(client.slept) == 1


def test_aclose_can_be_called_without_the_context_manager() -> None:
    client, _ = make_async_client(json_response(200, {"data": []}))

    async def main() -> None:
        await client.list_lines()
        await client.aclose()

    asyncio.run(main())
