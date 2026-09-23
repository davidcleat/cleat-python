from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from cleat import (
    LINE_STATUS_ACTIVE,
    CleatClient,
    CleatConfigurationError,
    CleatError,
)
from conftest import (
    API_KEY,
    LINE_ID,
    json_response,
    line_payload,
    make_client,
    message_payload,
)


def test_api_key_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLEAT_API_KEY", "clt_example_from_env")
    with CleatClient() as client:
        assert client.api_key == "clt_example_from_env"


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(CleatConfigurationError) as caught:
        CleatClient()
    assert "CLEAT_API_KEY" in str(caught.value)


def test_blank_api_key_is_rejected() -> None:
    with pytest.raises(CleatConfigurationError):
        CleatClient("   ")


def test_list_lines_parses_and_sends_the_bearer_token() -> None:
    client, recorder = make_client(
        json_response(200, {"data": [line_payload(), line_payload(id="second", status="released", label=None)]})
    )
    with client:
        lines = client.list_lines()

    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/api/v1/lines"
    assert recorder.last.headers["authorization"] == f"Bearer {API_KEY}"
    assert recorder.last.headers["user-agent"].startswith("cleat-python/")

    assert len(lines) == 2
    assert lines[0].id == LINE_ID
    assert lines[0].phone == "13055550100"
    assert lines[0].phone_e164 == "+13055550100"
    assert lines[0].status == LINE_STATUS_ACTIVE
    assert lines[0].is_active is True
    assert lines[0].created_at == datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert lines[0].created_at_raw == "2026-07-01T12:00:00.000Z"
    assert lines[1].label is None
    assert lines[1].status == "released"
    assert lines[1].is_active is False


def test_an_unknown_line_status_does_not_crash_parsing() -> None:
    client, _ = make_client(json_response(200, {"data": [line_payload(status="hibernating")]}))
    with client:
        (line,) = client.list_lines()
    assert line.status == "hibernating"
    assert line.is_active is False


def test_unknown_json_fields_are_kept_not_fatal() -> None:
    client, _ = make_client(
        json_response(200, {"data": [line_payload(portedOut=True)], "nextCursor": "ignored"})
    )
    with client:
        (line,) = client.list_lines()
    assert line.raw["portedOut"] is True


def test_full_message_parses() -> None:
    client, _ = make_client(json_response(200, {"data": [message_payload()]}))
    with client:
        (message, ) = client.list_messages(LINE_ID)

    assert message.from_ == "22395"
    assert message.sender == "22395"
    assert message.code == "704118"
    assert message.body == "Your Facebook code is 704118"
    assert message.received_at == datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    assert message.received_at_raw == "2026-09-23T10:00:00.000Z"
    assert message.service is not None and message.service.id == "facebook"
    assert message.service.color == "#0866FF"
    assert message.contact is None
    assert message.label == "Facebook"
    assert message.line.phone_e164 == "+13055550100"


def test_message_with_no_code_no_service_and_no_contact() -> None:
    client, _ = make_client(
        json_response(
            200,
            {
                "data": [
                    message_payload(
                        body="Hi, are we still on for Thursday?",
                        code=None,
                        service=None,
                        contact=None,
                        label=None,
                    )
                ]
            },
        )
    )
    with client:
        (message,) = client.list_messages(LINE_ID)
    assert message.code is None
    assert message.service is None
    assert message.contact is None
    assert message.label is None


def test_a_contact_wins_over_a_service() -> None:
    client, _ = make_client(
        json_response(
            200,
            {
                "data": [
                    message_payload(
                        contact={"id": "c_1", "name": "Bank of the West", "color": "#112233"},
                        label="Bank of the West",
                    )
                ]
            },
        )
    )
    with client:
        (message,) = client.list_messages(LINE_ID)
    assert message.contact is not None and message.contact.name == "Bank of the West"
    assert message.label == "Bank of the West"


def test_a_call_transcript_is_an_ordinary_message() -> None:
    """Nothing in the payload marks a message as a call: transcript in body, code filled in."""
    transcript = (
        "Hello. Your verification code is 4 4 1 9 0 2. "
        "Again, your verification code is 4 4 1 9 0 2. Goodbye."
    )
    client, _ = make_client(
        json_response(
            200,
            {
                "data": [
                    message_payload(
                        body=transcript,
                        code="441902",
                        service=None,
                        label=None,
                        **{"from": "13055550199"},
                    )
                ]
            },
        )
    )
    with client:
        (message,) = client.list_messages(LINE_ID)
    assert message.body == transcript
    assert message.code == "441902"
    assert message.from_ == "13055550199"


def test_query_parameters_serialise_datetimes_and_limit() -> None:
    client, recorder = make_client(json_response(200, {"data": []}))
    with client:
        client.list_messages(
            LINE_ID,
            after=datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc),
            before="2026-09-23T11:00:00Z",
            limit=25,
        )
    params = recorder.last.url.params
    assert params["after"] == "2026-09-23T09:30:00Z"
    assert params["before"] == "2026-09-23T11:00:00Z"
    assert params["limit"] == "25"
    assert recorder.last.url.path == f"/api/v1/lines/{LINE_ID}/messages"


def test_a_naive_datetime_is_treated_as_utc() -> None:
    client, recorder = make_client(json_response(200, {"data": []}))
    with client:
        client.list_messages(LINE_ID, after=datetime(2026, 9, 23, 9, 30))
    assert recorder.last.url.params["after"] == "2026-09-23T09:30:00Z"


def test_a_non_utc_datetime_is_converted() -> None:
    from datetime import timedelta

    client, recorder = make_client(json_response(200, {"data": []}))
    with client:
        client.list_messages(
            LINE_ID, after=datetime(2026, 9, 23, 11, 30, tzinfo=timezone(timedelta(hours=2)))
        )
    assert recorder.last.url.params["after"] == "2026-09-23T09:30:00Z"


def test_omitted_parameters_are_not_sent() -> None:
    client, recorder = make_client(json_response(200, {"data": []}))
    with client:
        client.list_messages(LINE_ID)
    assert str(recorder.last.url.params) == ""


@pytest.mark.parametrize("bad_limit", [0, 201, -1, 2.5, True])
def test_an_impossible_limit_is_refused_before_the_request(bad_limit: object) -> None:
    client, recorder = make_client(json_response(200, {"data": []}))
    with client:
        with pytest.raises(CleatConfigurationError):
            client.list_messages(LINE_ID, limit=bad_limit)  # type: ignore[arg-type]
    assert recorder.count == 0


def test_an_empty_line_id_is_refused_before_the_request() -> None:
    client, recorder = make_client(json_response(200, {"data": []}))
    with client:
        with pytest.raises(CleatConfigurationError):
            client.list_messages("  ")
    assert recorder.count == 0


def test_a_body_that_is_not_json_raises_cleat_error() -> None:
    client, _ = make_client(httpx.Response(200, content=b"<html>maintenance</html>"))
    with client:
        with pytest.raises(CleatError):
            client.list_lines()


def test_a_json_body_without_data_raises_cleat_error() -> None:
    client, _ = make_client(json_response(200, {"lines": []}))
    with client:
        with pytest.raises(CleatError):
            client.list_lines()


def test_base_url_can_be_overridden() -> None:
    client, recorder = make_client(json_response(200, {"data": []}), base_url="https://example.test/")
    with client:
        client.list_lines()
    assert str(recorder.last.url) == "https://example.test/api/v1/lines"
