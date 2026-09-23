from __future__ import annotations

import pytest

from cleat import (
    AuthenticationError,
    BadRequestError,
    CleatAPIError,
    KeyExpiredError,
    LineOnHoldError,
    NotFoundError,
    RateLimitError,
    VerificationRequiredError,
)
from conftest import LINE_ID, json_response, make_client, message_payload


def test_401_missing_or_revoked_key() -> None:
    client, _ = make_client(json_response(401, {"error": "Your API key is not valid."}))
    with client:
        with pytest.raises(AuthenticationError) as caught:
            client.list_lines()
    error = caught.value
    assert type(error) is AuthenticationError
    assert not isinstance(error, KeyExpiredError)
    assert error.status == 401
    assert error.code is None
    assert error.message == "Your API key is not valid."


def test_401_with_key_expired_is_its_own_error() -> None:
    client, _ = make_client(
        json_response(401, {"error": "This API key expired on 1 September 2026.", "code": "key_expired"})
    )
    with client:
        with pytest.raises(KeyExpiredError) as caught:
            client.list_lines()
    assert caught.value.code == "key_expired"
    # It is still an AuthenticationError, so a caller that only cares about auth
    # can catch the parent.
    assert isinstance(caught.value, AuthenticationError)


def test_402_line_on_hold() -> None:
    client, _ = make_client(
        json_response(402, {"error": "This line is on hold. Resubscribe to read its texts."})
    )
    with client:
        with pytest.raises(LineOnHoldError) as caught:
            client.list_messages(LINE_ID)
    assert caught.value.status == 402
    assert "Resubscribe" in caught.value.message


def test_403_verify_first() -> None:
    client, _ = make_client(
        json_response(
            403,
            {"error": "Verify your identity to read texts on this line.", "code": "verify_first"},
        )
    )
    with client:
        with pytest.raises(VerificationRequiredError) as caught:
            client.list_messages(LINE_ID)
    assert caught.value.code == "verify_first"


def test_404_unknown_or_out_of_scope_line() -> None:
    client, _ = make_client(json_response(404, {"error": "No line with that id."}))
    with client:
        with pytest.raises(NotFoundError) as caught:
            client.list_messages(LINE_ID)
    assert caught.value.status == 404


def test_400_bad_timestamp() -> None:
    client, _ = make_client(json_response(400, {"error": "after must be an ISO 8601 timestamp."}))
    with client:
        with pytest.raises(BadRequestError) as caught:
            client.list_messages(LINE_ID, after="last tuesday")
    assert caught.value.status == 400


def test_an_unmapped_status_still_raises_cleat_api_error() -> None:
    client, _ = make_client(json_response(500, {"error": "Something went wrong."}))
    with client:
        with pytest.raises(CleatAPIError) as caught:
            client.list_lines()
    assert caught.value.status == 500


def test_an_error_body_that_is_not_json_still_maps_by_status() -> None:
    import httpx

    client, _ = make_client(httpx.Response(401, content=b"unauthorized"))
    with client:
        with pytest.raises(AuthenticationError) as caught:
            client.list_lines()
    assert caught.value.message  # a usable default sentence
    assert caught.value.body is None


def test_429_retries_then_succeeds() -> None:
    responses = [
        json_response(429, {"error": "Too many requests."}),
        json_response(429, {"error": "Too many requests."}),
        json_response(200, {"data": [message_payload()]}),
    ]
    client, recorder = make_client(*responses)
    with client:
        messages = client.list_messages(LINE_ID)

    assert len(messages) == 1
    assert recorder.count == 3
    # Two backoff waits, injected rather than really slept.
    assert len(client.slept) == 2
    assert all(0 < delay <= 20 for delay in client.slept)
    # Exponential: the second wait cannot be shorter than the first's floor.
    assert client.slept[1] >= 0.5


def test_429_raises_once_retries_are_exhausted() -> None:
    too_many = json_response(429, {"error": "Too many requests."})
    client, recorder = make_client(too_many, max_retries=2)
    with client:
        with pytest.raises(RateLimitError) as caught:
            client.list_lines()

    assert recorder.count == 3  # the first try plus two retries
    assert len(client.slept) == 2
    # VERIFIED: Cleat's 429 carries no Retry-After, so there is nothing to honour.
    assert caught.value.retry_after is None


def test_429_with_max_retries_zero_does_not_retry() -> None:
    client, recorder = make_client(json_response(429, {"error": "Too many requests."}), max_retries=0)
    with client:
        with pytest.raises(RateLimitError):
            client.list_lines()
    assert recorder.count == 1
    assert client.slept == []


def test_a_retry_after_header_is_honoured_if_one_ever_appears() -> None:
    responses = [
        json_response(429, {"error": "Too many requests."}, headers={"retry-after": "7"}),
        json_response(200, {"data": []}),
    ]
    client, _ = make_client(*responses)
    with client:
        client.list_lines()
    assert client.slept == [7.0]
