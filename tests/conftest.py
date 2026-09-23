"""Shared fixtures.

Every test runs against ``httpx.MockTransport``: nothing in this suite opens a
socket. The API key is a fake literal, never a real one.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

import httpx
import pytest

from cleatapi import AsyncCleatClient, CleatClient

API_KEY = "clt_example"
LINE_ID = "6f0a0b0c-1111-4222-8333-444455556666"
WEBHOOK_SECRET = "whsec_test_secret"


def line_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": LINE_ID,
        "phone": "13055550100",
        "label": "Cloud console",
        "status": "active",
        "createdAt": "2026-07-01T12:00:00.000Z",
    }
    payload.update(overrides)
    return payload


def message_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "aaaa1111-2222-4333-8444-555566667777",
        "line": {"id": LINE_ID, "phone": "13055550100", "label": "Cloud console"},
        "from": "22395",
        "body": "Your Facebook code is 704118",
        "code": "704118",
        "receivedAt": "2026-09-23T10:00:00.000Z",
        "service": {"id": "facebook", "name": "Facebook", "color": "#0866FF"},
        "contact": None,
        "label": "Facebook",
    }
    payload.update(overrides)
    return payload


def json_response(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", **(headers or {})},
    )


class Recorder:
    """A MockTransport handler that records requests and replays canned responses."""

    def __init__(self, responses: Sequence[httpx.Response | Callable[[httpx.Request], httpx.Response]]):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError(f"Unexpected extra request: {request.method} {request.url}")
        item = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return item(request) if callable(item) else item

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    @property
    def count(self) -> int:
        return len(self.requests)


def make_client(*responses: Any, **kwargs: Any) -> tuple[CleatClient, Recorder]:
    """A sync client whose HTTP is stubbed, with sleeping replaced by a recorder."""
    recorder = Recorder(responses)
    client = CleatClient(API_KEY, transport=httpx.MockTransport(recorder), **kwargs)
    client.slept: list[float] = []  # type: ignore[attr-defined]
    client._sleep = client.slept.append  # type: ignore[attr-defined]
    return client, recorder


def make_async_client(*responses: Any, **kwargs: Any) -> tuple[AsyncCleatClient, Recorder]:
    """An async client whose HTTP is stubbed, with awaiting-sleep replaced by a recorder."""
    recorder = Recorder(responses)
    client = AsyncCleatClient(API_KEY, transport=httpx.MockTransport(recorder), **kwargs)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client.slept = slept  # type: ignore[attr-defined]
    client._sleep = fake_sleep  # type: ignore[attr-defined]
    return client, recorder


@pytest.fixture(autouse=True)
def no_ambient_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure a real key in the developer's environment cannot leak into a test."""
    monkeypatch.delenv("CLEAT_API_KEY", raising=False)
