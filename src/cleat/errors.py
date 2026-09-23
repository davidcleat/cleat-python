"""Exceptions raised by the Cleat client.

Every error the library raises derives from :class:`CleatError`, so a caller that
does not care about the detail can catch one thing.  Errors that came back from
the API derive from :class:`CleatAPIError` and carry the HTTP status, the
sentence the API meant to be shown to a person, the optional machine-readable
``code``, and the raw response.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "CleatError",
    "CleatConfigurationError",
    "CleatAPIError",
    "BadRequestError",
    "AuthenticationError",
    "KeyExpiredError",
    "LineOnHoldError",
    "VerificationRequiredError",
    "NotFoundError",
    "RateLimitError",
    "CleatTimeoutError",
    "CleatSignatureError",
    "MalformedSignatureHeaderError",
    "SignatureTimestampError",
    "InvalidSignatureError",
    "error_from_response",
]


class CleatError(Exception):
    """Base class for everything this library raises."""


class CleatConfigurationError(CleatError, ValueError):
    """The client was built without an API key, or with an unusable setting."""


class CleatAPIError(CleatError):
    """The API answered with an HTTP error status.

    Attributes:
        status: the HTTP status code.
        message: the sentence from the ``error`` field, fit to show a person.
        code: the machine-readable ``code`` field, when the API sent one.
        response: the underlying ``httpx.Response``, or None.
        body: the decoded JSON body, when it was JSON.
    """

    def __init__(
        self,
        status: int,
        message: str,
        code: str | None = None,
        response: Any | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{status}] {message}" if code is None else f"[{status}] {message} (code={code})")
        self.status = status
        self.message = message
        self.code = code
        self.response = response
        self.body = body


class BadRequestError(CleatAPIError):
    """400 — ``after``/``before`` was not an ISO 8601 timestamp, or ``limit`` was out of 1..200."""


class AuthenticationError(CleatAPIError):
    """401 — the key is missing, malformed, revoked, or its workspace owner is disabled."""


class KeyExpiredError(AuthenticationError):
    """401 with ``code == "key_expired"`` — the key had an expiry date and it has passed.

    Make a new key in workspace settings; the old one will not start working again.
    """


class LineOnHoldError(CleatAPIError):
    """402 — the line is on hold (status ``grace``).

    The line keeps receiving and storing texts, but none can be read until the
    subscription is paid again.
    """


class VerificationRequiredError(CleatAPIError):
    """403 with ``code == "verify_first"`` — the workspace owner has not verified their identity yet.

    The line runs and keeps every text it receives; nothing can be read until
    the owner finishes verification once, in the Cleat dashboard.
    """


class NotFoundError(CleatAPIError):
    """404 — no line with that id in this key's workspace.

    A line that exists but is OUTSIDE THIS KEY'S SCOPE answers 404 as well, and
    the response is indistinguishable from a line that belongs to somebody
    else's workspace.  If you get this for a line you can see in the dashboard,
    check whether the key was scoped to a subset of lines.
    """


class RateLimitError(CleatAPIError):
    """429 — more than 120 requests in a minute on this key.

    Raised once the client's own retries are exhausted.  Note that Cleat's 429
    carries no ``Retry-After`` and no rate-limit headers, so ``retry_after`` is
    normally None.
    """

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class CleatTimeoutError(CleatError):
    """A wait (for example :meth:`CleatClient.wait_for_code`) ran out of time."""


class CleatSignatureError(CleatError):
    """A webhook could not be verified.  Never trust the body when this is raised."""


class MalformedSignatureHeaderError(CleatSignatureError):
    """The ``cleat-signature`` header was absent, empty, or not ``t=...,v1=...``."""


class SignatureTimestampError(CleatSignatureError):
    """The signed timestamp is further from now than the tolerance allows (replay defence)."""


class InvalidSignatureError(CleatSignatureError):
    """The signature did not match the body — wrong secret, or the body was changed."""


_STATUS_MAP: dict[int, type[CleatAPIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: LineOnHoldError,
    403: VerificationRequiredError,
    404: NotFoundError,
    429: RateLimitError,
}

_DEFAULT_MESSAGES: dict[int, str] = {
    400: "The request was rejected as malformed.",
    401: "The API key is missing, malformed, revoked, or its workspace owner is disabled.",
    402: "This line is on hold. Resubscribe to read its texts.",
    403: "The workspace owner has not verified their identity yet.",
    404: "No line with that id in this key's workspace.",
    429: "Rate limit reached for this API key.",
}


def _decode_body(response: Any) -> Mapping[str, Any] | None:
    try:
        body = response.json()
    except Exception:  # not JSON, or empty
        return None
    return body if isinstance(body, Mapping) else None


def error_from_response(response: Any) -> CleatAPIError:
    """Turn an error response into the most specific exception we have for it."""
    status = int(response.status_code)
    body = _decode_body(response)

    message: str | None = None
    code: str | None = None
    if body is not None:
        raw_message = body.get("error")
        if isinstance(raw_message, str) and raw_message.strip():
            message = raw_message
        raw_code = body.get("code")
        if isinstance(raw_code, str) and raw_code.strip():
            code = raw_code
    if message is None:
        message = _DEFAULT_MESSAGES.get(status, f"The API answered with HTTP {status}.")

    cls: type[CleatAPIError] = _STATUS_MAP.get(status, CleatAPIError)
    if status == 401 and code == "key_expired":
        cls = KeyExpiredError

    if cls is RateLimitError:
        return RateLimitError(
            status,
            message,
            code,
            response,
            body,
            retry_after=_retry_after_seconds(response),
        )
    return cls(status, message, code, response, body)


def _retry_after_seconds(response: Any) -> float | None:
    """Read a ``Retry-After`` header if the response happens to carry one.

    VERIFIED 2026-09-23: Cleat's 429 sends no ``Retry-After`` and no
    rate-limit headers, so this is almost always None and the client falls back
    to its own backoff.  It is read anyway so the client would honour one the
    day it appears.
    """
    try:
        raw = response.headers.get("retry-after")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None  # HTTP-date form; not worth parsing, our backoff covers it
    return value if value >= 0 else None
