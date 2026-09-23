"""Verifying Cleat webhook deliveries.

Cleat POSTs ``{"type": "message.received", "data": <Message>}`` to each
configured endpoint as soon as it has stored the message, and signs it with the
endpoint's ``whsec_`` secret.  Verify over the RAW REQUEST BYTES, before any
JSON parsing or framework re-serialisation.

    from cleat import verify_webhook, CleatSignatureError

    try:
        event = verify_webhook(SECRET, request.body, request.headers["cleat-signature"])
    except CleatSignatureError:
        return Response(status=400)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import (
    InvalidSignatureError,
    MalformedSignatureHeaderError,
    SignatureTimestampError,
)
from .models import WebhookEvent

__all__ = [
    "SIGNATURE_HEADER",
    "DEFAULT_TOLERANCE_SECONDS",
    "SignatureHeader",
    "parse_signature_header",
    "verify_webhook",
]

SIGNATURE_HEADER = "cleat-signature"
"""The request header carrying the signature: ``t=<unix seconds>,v1=<hex>``."""

DEFAULT_TOLERANCE_SECONDS = 300
"""How far the signed timestamp may be from now.  300s is the tolerance in Cleat's own example."""


@dataclass(frozen=True)
class SignatureHeader:
    """The parsed pieces of a ``cleat-signature`` header."""

    timestamp: int
    """Unix seconds, as signed."""
    signatures: tuple[str, ...]
    """Every ``v1`` value in the header, lowercase hex.

    Cleat sends exactly one.  This is a tuple, and the check below accepts any of them,
    only so that a header carrying a second signature would not be read as a forgery.
    """


def parse_signature_header(header: str) -> SignatureHeader:
    """Parse ``t=<unix seconds>,v1=<hex>`` into its pieces.

    Unknown ``k=v`` pairs are ignored so a future scheme version does not break
    this one.  Raises :class:`MalformedSignatureHeaderError` if there is no
    usable timestamp or no ``v1`` value.
    """
    if not isinstance(header, str) or not header.strip():
        raise MalformedSignatureHeaderError(
            f"The {SIGNATURE_HEADER} header is missing or empty."
        )

    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            raise MalformedSignatureHeaderError(
                f"The {SIGNATURE_HEADER} header is not in t=<unix seconds>,v1=<hex> form."
            )
        key = key.strip()
        value = value.strip()
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                raise MalformedSignatureHeaderError(
                    f"The {SIGNATURE_HEADER} timestamp is not an integer."
                ) from None
        elif key == "v1":
            if value:
                signatures.append(value.lower())

    if timestamp is None:
        raise MalformedSignatureHeaderError(f"The {SIGNATURE_HEADER} header has no t= timestamp.")
    if not signatures:
        raise MalformedSignatureHeaderError(f"The {SIGNATURE_HEADER} header has no v1= signature.")
    return SignatureHeader(timestamp=timestamp, signatures=tuple(signatures))


def _expected_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    signed_payload = str(timestamp).encode("ascii") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def verify_webhook(
    secret: str,
    raw_body: bytes | str,
    header: str,
    *,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> WebhookEvent:
    """Verify a webhook delivery and return the parsed event.

    Args:
        secret: the endpoint's signing secret, which starts with ``whsec_``.
            It is shown once, when the endpoint is created.
        raw_body: the request body EXACTLY as received.  A ``str`` is encoded as
            UTF-8; passing something your framework re-serialised will fail
            verification, correctly.
        header: the value of the ``cleat-signature`` request header.
        tolerance: how many seconds the signed timestamp may differ from now,
            in either direction.  Pass 0 to skip the timestamp check entirely
            (only sensible when replaying a captured delivery in a test).
        now: unix seconds to compare against, for tests.

    Raises:
        MalformedSignatureHeaderError: the header was not ``t=...,v1=...``.
        SignatureTimestampError: the signed timestamp is outside ``tolerance``.
        InvalidSignatureError: the body does not match the signature — either it
            was changed in flight or it was signed with a different secret.

    All three derive from :class:`CleatSignatureError`, so catching that one is
    enough when you only intend to answer 400.
    """
    if not isinstance(secret, str) or not secret:
        raise InvalidSignatureError("A webhook signing secret is required to verify a delivery.")

    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    parsed = parse_signature_header(header)

    if tolerance:
        current = time.time() if now is None else now
        drift = current - parsed.timestamp
        if drift > tolerance:
            raise SignatureTimestampError(
                f"The webhook timestamp is {int(drift)}s old, outside the {tolerance}s tolerance."
            )
        if -drift > tolerance:
            raise SignatureTimestampError(
                f"The webhook timestamp is {int(-drift)}s in the future, outside the {tolerance}s tolerance."
            )

    expected = _expected_signature(secret, parsed.timestamp, body)
    # Constant-time comparison against every v1 in the header.  Cleat sends one; a
    # second one would not be rejected out of hand.
    if not any(hmac.compare_digest(expected, candidate) for candidate in parsed.signatures):
        raise InvalidSignatureError(
            "The webhook signature does not match the body: wrong secret, or the body was modified."
        )

    try:
        payload: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidSignatureError(f"The webhook body is signed but is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise InvalidSignatureError("The webhook body is signed but is not a JSON object.")
    return WebhookEvent.from_dict(payload)
