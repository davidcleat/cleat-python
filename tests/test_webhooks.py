from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from cleatapi import (
    EVENT_MESSAGE_RECEIVED,
    EVENT_TEST,
    CleatSignatureError,
    InvalidSignatureError,
    MalformedSignatureHeaderError,
    SignatureTimestampError,
    parse_signature_header,
    verify_webhook,
)
from conftest import WEBHOOK_SECRET, message_payload


def sign(body: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> tuple[str, int]:
    """Build a cleat-signature header the way Cleat does: HMAC-SHA256 over "<t>.<raw body>"."""
    t = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{t}".encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}", t


def delivery(event_type: str = EVENT_MESSAGE_RECEIVED, **overrides: object) -> bytes:
    return json.dumps({"type": event_type, "data": message_payload(**overrides)}).encode()


def test_accepts_a_correctly_signed_body() -> None:
    body = delivery()
    header, _ = sign(body)
    event = verify_webhook(WEBHOOK_SECRET, body, header)
    assert event.type == EVENT_MESSAGE_RECEIVED
    assert event.is_test is False
    assert event.data is not None
    assert event.data.code == "704118"
    assert event.message is event.data


def test_accepts_a_test_delivery() -> None:
    body = delivery(EVENT_TEST)
    header, _ = sign(body)
    event = verify_webhook(WEBHOOK_SECRET, body, header)
    assert event.type == EVENT_TEST
    assert event.is_test is True


def test_a_str_body_is_encoded_as_utf8() -> None:
    text = json.dumps({"type": EVENT_MESSAGE_RECEIVED, "data": message_payload(body="café code 1234")})
    header, _ = sign(text.encode("utf-8"))
    event = verify_webhook(WEBHOOK_SECRET, text, header)
    assert event.data is not None


def test_rejects_a_tampered_body() -> None:
    body = delivery()
    header, _ = sign(body)
    tampered = body.replace(b"704118", b"000000")
    with pytest.raises(InvalidSignatureError):
        verify_webhook(WEBHOOK_SECRET, tampered, header)


def test_rejects_a_signature_made_with_the_wrong_secret() -> None:
    body = delivery()
    header, _ = sign(body, secret="whsec_another_secret")
    with pytest.raises(InvalidSignatureError):
        verify_webhook(WEBHOOK_SECRET, body, header)


def test_rejects_a_stale_timestamp() -> None:
    body = delivery()
    header, _ = sign(body, timestamp=int(time.time()) - 900)
    with pytest.raises(SignatureTimestampError):
        verify_webhook(WEBHOOK_SECRET, body, header)


def test_rejects_a_timestamp_from_the_future() -> None:
    body = delivery()
    header, _ = sign(body, timestamp=int(time.time()) + 900)
    with pytest.raises(SignatureTimestampError):
        verify_webhook(WEBHOOK_SECRET, body, header)


def test_a_stale_timestamp_is_still_rejected_when_the_signature_is_valid() -> None:
    """Order matters: a replayed-but-authentic delivery must not be accepted."""
    body = delivery()
    header, _ = sign(body, timestamp=1_700_000_000)
    with pytest.raises(SignatureTimestampError):
        verify_webhook(WEBHOOK_SECRET, body, header)


def test_tolerance_can_be_widened_and_disabled() -> None:
    body = delivery()
    old = int(time.time()) - 900
    header, _ = sign(body, timestamp=old)
    assert verify_webhook(WEBHOOK_SECRET, body, header, tolerance=1200).data is not None
    assert verify_webhook(WEBHOOK_SECRET, body, header, tolerance=0).data is not None


def test_now_can_be_injected() -> None:
    body = delivery()
    header, t = sign(body, timestamp=1_700_000_000)
    event = verify_webhook(WEBHOOK_SECRET, body, header, now=t + 10)
    assert event.data is not None


@pytest.mark.parametrize(
    "header",
    [
        "",
        "   ",
        "nonsense",
        "t=abc,v1=deadbeef",
        "v1=deadbeef",
        "t=1700000000",
        "t=1700000000,v1=",
    ],
)
def test_rejects_a_malformed_header(header: str) -> None:
    body = delivery()
    with pytest.raises(MalformedSignatureHeaderError):
        verify_webhook(WEBHOOK_SECRET, body, header)


def test_every_signature_error_is_catchable_as_one_thing() -> None:
    body = delivery()
    header, _ = sign(body, secret="whsec_wrong")
    with pytest.raises(CleatSignatureError):
        verify_webhook(WEBHOOK_SECRET, body, header)
    with pytest.raises(CleatSignatureError):
        verify_webhook(WEBHOOK_SECRET, body, "garbage")


def test_a_missing_secret_is_refused() -> None:
    body = delivery()
    header, _ = sign(body)
    with pytest.raises(CleatSignatureError):
        verify_webhook("", body, header)


def test_parse_signature_header_ignores_unknown_pairs_and_keeps_every_v1() -> None:
    parsed = parse_signature_header("t=1700000000,v1=AABB,v2=future,v1=ccdd")
    assert parsed.timestamp == 1_700_000_000
    assert parsed.signatures == ("aabb", "ccdd")


def test_a_rotation_that_sends_two_signatures_verifies_with_either_secret() -> None:
    body = delivery()
    t = int(time.time())
    old = hmac.new(b"whsec_old", f"{t}".encode() + b"." + body, hashlib.sha256).hexdigest()
    new = hmac.new(WEBHOOK_SECRET.encode(), f"{t}".encode() + b"." + body, hashlib.sha256).hexdigest()
    header = f"t={t},v1={old},v1={new}"
    assert verify_webhook(WEBHOOK_SECRET, body, header).data is not None
    assert verify_webhook("whsec_old", body, header).data is not None


def test_a_signed_body_that_is_not_json_is_rejected() -> None:
    body = b"not json at all"
    header, _ = sign(body)
    with pytest.raises(InvalidSignatureError):
        verify_webhook(WEBHOOK_SECRET, body, header)


def test_an_unknown_event_type_still_parses() -> None:
    body = json.dumps({"type": "message.redacted", "data": message_payload()}).encode()
    header, _ = sign(body)
    event = verify_webhook(WEBHOOK_SECRET, body, header)
    assert event.type == "message.redacted"
    assert event.data is not None


def test_an_event_with_no_data_object_still_parses() -> None:
    body = json.dumps({"type": "workspace.something", "data": None}).encode()
    header, _ = sign(body)
    event = verify_webhook(WEBHOOK_SECRET, body, header)
    assert event.data is None
