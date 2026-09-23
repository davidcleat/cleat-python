from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from cleat import CleatTimeoutError
from conftest import LINE_ID, json_response, make_client, message_payload


def test_returns_the_first_code_to_arrive_as_a_string() -> None:
    chat = message_payload(id="m1", code=None, receivedAt="2026-09-23T10:00:01.000Z", body="hey")
    coded = message_payload(id="m2", code="704118", receivedAt="2026-09-23T10:00:05.000Z")
    client, recorder = make_client(
        json_response(200, {"data": []}),
        json_response(200, {"data": [chat]}),
        json_response(200, {"data": [coded]}),
    )
    with client:
        code = client.wait_for_code(LINE_ID, timeout=30, poll_interval=3)

    assert code == "704118"
    assert isinstance(code, str)
    assert recorder.count == 3
    assert client.slept == [3, 3]


def test_wait_for_message_returns_the_whole_message_for_the_same_poll() -> None:
    coded = message_payload(id="m2", code="704118", body="Your Facebook code is 704118")
    client, _ = make_client(json_response(200, {"data": [coded]}))
    with client:
        message = client.wait_for_message(LINE_ID, timeout=5)
    assert message.id == "m2"
    assert message.code == "704118"
    assert message.body == "Your Facebook code is 704118"


def test_the_cursor_walks_forward_because_after_returns_oldest_first() -> None:
    first_page = [
        message_payload(id="m1", code=None, receivedAt="2026-09-23T10:00:01.000Z"),
        message_payload(id="m2", code=None, receivedAt="2026-09-23T10:00:02.000Z"),
        message_payload(id="m3", code=None, receivedAt="2026-09-23T10:00:03.000Z"),
    ]
    coded = message_payload(id="m4", code="551201", receivedAt="2026-09-23T10:00:09.000Z")
    client, recorder = make_client(
        json_response(200, {"data": first_page}),
        json_response(200, {"data": [coded]}),
    )
    with client:
        message = client.wait_for_message(
            LINE_ID, since="2026-09-23T10:00:00Z", timeout=30, poll_interval=1
        )

    assert message.id == "m4"
    # First poll starts at `since`; the second resumes from the NEWEST message of
    # the first page, which is its last element.
    assert recorder.requests[0].url.params["after"] == "2026-09-23T10:00:00Z"
    assert recorder.requests[1].url.params["after"] == "2026-09-23T10:00:03.000Z"
    assert recorder.requests[1].url.params["limit"] == "200"


def test_since_defaults_to_now_so_older_messages_are_ignored() -> None:
    coded = message_payload(code="704118")
    client, recorder = make_client(json_response(200, {"data": [coded]}))
    before = datetime.now(timezone.utc)
    with client:
        client.wait_for_code(LINE_ID, timeout=5)
    sent = recorder.requests[0].url.params["after"]
    assert sent.endswith("Z")
    assert datetime.fromisoformat(sent.replace("Z", "+00:00")) >= before


def test_filters_by_sender() -> None:
    other = message_payload(id="m1", code="111111", **{"from": "44444"})
    wanted = message_payload(id="m2", code="222222", **{"from": "13055550123"})
    client, _ = make_client(json_response(200, {"data": [other, wanted]}))
    with client:
        assert client.wait_for_code(LINE_ID, from_="+13055550123", timeout=5) == "222222"


def test_sender_matching_ignores_case_for_alphanumeric_sender_ids() -> None:
    wanted = message_payload(id="m1", code="222222", **{"from": "VERIFY"})
    client, _ = make_client(json_response(200, {"data": [wanted]}))
    with client:
        assert client.wait_for_code(LINE_ID, from_="verify", timeout=5) == "222222"


def test_filters_by_service_id_or_name() -> None:
    stripe = message_payload(
        id="m1", code="111111", service={"id": "stripe", "name": "Stripe", "color": "#635BFF"}
    )
    facebook = message_payload(id="m2", code="222222")
    payload = json_response(200, {"data": [stripe, facebook]})

    client, _ = make_client(payload)
    with client:
        assert client.wait_for_code(LINE_ID, service="facebook", timeout=5) == "222222"

    client, _ = make_client(payload)
    with client:
        assert client.wait_for_code(LINE_ID, service="Stripe", timeout=5) == "111111"


def test_a_message_with_no_service_never_matches_a_service_filter() -> None:
    unattributed = message_payload(id="m1", code="111111", service=None, label=None)
    client, _ = make_client(json_response(200, {"data": [unattributed]}))
    with client:
        with pytest.raises(CleatTimeoutError):
            client.wait_for_code(LINE_ID, service="facebook", timeout=0)


def test_timeout_raises_and_says_which_line() -> None:
    client, recorder = make_client(json_response(200, {"data": []}))
    with client:
        with pytest.raises(CleatTimeoutError) as caught:
            client.wait_for_code(LINE_ID, timeout=0)
    assert LINE_ID in str(caught.value)
    assert recorder.count == 1  # it always polls at least once


def test_wait_for_message_can_take_a_message_with_no_code() -> None:
    chat = message_payload(id="m1", code=None, body="are we still on for Thursday?")
    client, _ = make_client(json_response(200, {"data": [chat]}))
    with client:
        message = client.wait_for_message(LINE_ID, require_code=False, timeout=5)
    assert message.id == "m1"
    assert message.code is None


def test_wait_for_message_requires_a_code_by_default() -> None:
    chat = message_payload(id="m1", code=None)
    client, _ = make_client(json_response(200, {"data": [chat]}))
    with client:
        with pytest.raises(CleatTimeoutError):
            client.wait_for_message(LINE_ID, timeout=0)


def test_an_api_error_during_a_wait_is_not_swallowed() -> None:
    client, _ = make_client(json_response(402, {"error": "This line is on hold."}))
    from cleat import LineOnHoldError

    with client:
        with pytest.raises(LineOnHoldError):
            client.wait_for_code(LINE_ID, timeout=30)


def test_a_message_whose_timestamp_is_unparseable_does_not_crash_the_walk() -> None:
    odd = message_payload(id="m1", code=None, receivedAt="not a timestamp")
    coded = message_payload(id="m2", code="704118", receivedAt="2026-09-23T10:00:09.000Z")
    client, recorder = make_client(
        json_response(200, {"data": [odd]}),
        json_response(200, {"data": [coded]}),
    )
    with client:
        code = client.wait_for_code(LINE_ID, since="2026-09-23T10:00:00Z", timeout=5, poll_interval=1)
    assert code == "704118"
    # The raw string is still used as the cursor: the server, not the client,
    # decides what a timestamp means.
    assert recorder.requests[1].url.params["after"] == "not a timestamp"
    assert odd["receivedAt"] == "not a timestamp"


def test_http_timeouts_surface_as_cleat_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    from cleat import CleatClient

    with CleatClient("clt_example", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CleatTimeoutError):
            client.list_lines()
