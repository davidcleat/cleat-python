"""Python client for the Cleat API.

Cleat rents ID-verified US mobile numbers that receive SMS/2FA codes and
transcripts of incoming calls. The lines are receive-only: no outbound texts,
no outbound calls, no 911.

    import os
    from cleatapi import CleatClient

    with CleatClient(os.environ["CLEAT_API_KEY"]) as cleat:
        line = cleat.list_lines()[0]
        message = cleat.wait_for_code(line.id, timeout=180)
        print(message.code)

https://cleat.so — https://cleat.so/for/developers
"""

from __future__ import annotations

__version__ = "0.1.0"

from .client import (
    API_KEY_ENV_VAR,
    DEFAULT_BASE_URL,
    MAX_LIMIT,
    AsyncCleatClient,
    CleatClient,
)
from .errors import (
    AuthenticationError,
    BadRequestError,
    CleatAPIError,
    CleatConfigurationError,
    CleatError,
    CleatSignatureError,
    CleatTimeoutError,
    InvalidSignatureError,
    KeyExpiredError,
    LineOnHoldError,
    MalformedSignatureHeaderError,
    NotFoundError,
    RateLimitError,
    SignatureTimestampError,
    VerificationRequiredError,
)
from .models import (
    EVENT_MESSAGE_RECEIVED,
    EVENT_TEST,
    LINE_STATUS_ACTIVE,
    LINE_STATUS_GRACE,
    LINE_STATUS_RELEASED,
    LINE_STATUSES,
    Contact,
    Line,
    Message,
    MessageLine,
    Service,
    WebhookEvent,
    format_timestamp,
    parse_timestamp,
)
from .webhooks import (
    DEFAULT_TOLERANCE_SECONDS,
    SIGNATURE_HEADER,
    SignatureHeader,
    parse_signature_header,
    verify_webhook,
)

__all__ = [
    "__version__",
    # clients
    "CleatClient",
    "AsyncCleatClient",
    "DEFAULT_BASE_URL",
    "API_KEY_ENV_VAR",
    "MAX_LIMIT",
    # webhooks
    "verify_webhook",
    "parse_signature_header",
    "SignatureHeader",
    "SIGNATURE_HEADER",
    "DEFAULT_TOLERANCE_SECONDS",
    # models
    "Line",
    "Message",
    "MessageLine",
    "Service",
    "Contact",
    "WebhookEvent",
    "parse_timestamp",
    "format_timestamp",
    "LINE_STATUS_ACTIVE",
    "LINE_STATUS_GRACE",
    "LINE_STATUS_RELEASED",
    "LINE_STATUSES",
    "EVENT_MESSAGE_RECEIVED",
    "EVENT_TEST",
    # errors
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
]
