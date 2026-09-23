"""Frozen dataclasses for the objects the Cleat API returns.

Parsing is deliberately forgiving.  Fields the API adds later are kept in
``raw`` and never crash a parse, and an unrecognised ``Line.status`` is passed
through as a plain string.  If a field matters to you and it is new, read it
from ``raw`` until the library catches up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = [
    "Line",
    "Message",
    "MessageLine",
    "Service",
    "Contact",
    "WebhookEvent",
    "LINE_STATUS_ACTIVE",
    "LINE_STATUS_GRACE",
    "LINE_STATUS_RELEASED",
    "LINE_STATUSES",
    "EVENT_MESSAGE_RECEIVED",
    "EVENT_TEST",
    "parse_timestamp",
    "format_timestamp",
]

#: ``Line.status`` values Cleat documents today.  ``status`` is typed ``str``
#: rather than an enum so a value added later still parses.
LINE_STATUS_ACTIVE = "active"
"""Receiving normally."""
LINE_STATUS_GRACE = "grace"
"""Unpaid: texts are still stored, but reading them answers HTTP 402."""
LINE_STATUS_RELEASED = "released"
"""The number is gone.  Released lines are still listed."""
LINE_STATUSES = (LINE_STATUS_ACTIVE, LINE_STATUS_GRACE, LINE_STATUS_RELEASED)

EVENT_MESSAGE_RECEIVED = "message.received"
"""``WebhookEvent.type`` for a real delivery."""
EVENT_TEST = "test"
"""``WebhookEvent.type`` for a test delivery sent from workspace settings."""

_TRAILING_FRACTION = re.compile(r"^(?P<head>.*\.\d{6})\d+(?P<tail>.*)$")


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware UTC ``datetime``.

    Handles the ``Z`` suffix and over-long fractional seconds, which bare
    ``datetime.fromisoformat`` rejects on Python 3.10.  A timestamp with no
    offset is read as UTC.  Raises ``ValueError`` if it cannot be parsed.
    """
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    match = _TRAILING_FRACTION.match(text)
    if match is not None:
        text = match.group("head") + match.group("tail")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """Render a ``datetime`` the way the API's query parameters want it.

    A naive ``datetime`` is treated as UTC, which is what a caller who wrote
    ``datetime.utcnow()`` meant.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _maybe_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_timestamp(value)
    except ValueError:
        return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class Service:
    """A sender Cleat recognised from the text of the message.

    Recognition is conservative — several services share one short code — so
    this is None whenever it is not clear.
    """

    id: str
    name: str
    color: str
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Service":
        return cls(
            id=_text(payload.get("id")),
            name=_text(payload.get("name")),
            color=_text(payload.get("color")),
            raw=dict(payload),
        )

    _parse = from_dict


@dataclass(frozen=True)
class Contact:
    """A name the workspace saved for a sender.  A contact always wins over a service."""

    id: str
    name: str
    color: str
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Contact":
        return cls(
            id=_text(payload.get("id")),
            name=_text(payload.get("name")),
            color=_text(payload.get("color")),
            raw=dict(payload),
        )

    _parse = from_dict


@dataclass(frozen=True)
class MessageLine:
    """The line a message arrived on, as embedded in a message."""

    id: str
    phone: str
    label: str | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def phone_e164(self) -> str:
        """``phone`` with the leading plus the API leaves off."""
        return _with_plus(self.phone)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MessageLine":
        return cls(
            id=_text(payload.get("id")),
            phone=_text(payload.get("phone")),
            label=_optional_text(payload.get("label")),
            raw=dict(payload),
        )

    _parse = from_dict


@dataclass(frozen=True)
class Line:
    """A rented number in the key's workspace."""

    id: str
    phone: str
    """E.164 without the plus, e.g. ``"13055550100"``."""
    label: str | None
    status: str
    """``"active"``, ``"grace"`` or ``"released"`` today; any future value passes through."""
    created_at: datetime | None
    """``created_at_raw`` parsed to UTC, or None if it could not be parsed."""
    created_at_raw: str
    """Exactly the string the API sent."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def phone_e164(self) -> str:
        return _with_plus(self.phone)

    @property
    def is_active(self) -> bool:
        return self.status == LINE_STATUS_ACTIVE

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Line":
        created_raw = _text(payload.get("createdAt"))
        return cls(
            id=_text(payload.get("id")),
            phone=_text(payload.get("phone")),
            label=_optional_text(payload.get("label")),
            status=_text(payload.get("status")),
            created_at=_maybe_timestamp(payload.get("createdAt")),
            created_at_raw=created_raw,
            raw=dict(payload),
        )

    _parse = from_dict


@dataclass(frozen=True)
class Message:
    """One received text, or the transcript of one received call.

    Nothing in the payload marks a message as having come from a call: a code
    read out by an automated call arrives with the transcript in ``body``, the
    calling number in ``from_``, and ``code`` filled in.
    """

    id: str
    line: MessageLine
    from_: str
    """The sender as the network reported it: a number, or an alphanumeric sender id."""
    body: str
    """The full text.  For a call, the transcript."""
    code: str | None
    """The code as extracted, for display.  Best effort — read ``body`` when it matters."""
    received_at: datetime | None
    """``received_at_raw`` parsed to UTC, or None if it could not be parsed."""
    received_at_raw: str
    """Exactly the string the API sent.  Use this as a polling cursor."""
    service: Service | None
    contact: Contact | None
    label: str | None
    """What to show: the contact's name, else the service's name, else None."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def sender(self) -> str:
        """Alias for ``from_``, for code that reads better without the underscore."""
        return self.from_

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Message":
        service_payload = payload.get("service")
        contact_payload = payload.get("contact")
        return cls(
            id=_text(payload.get("id")),
            line=MessageLine.from_dict(_mapping(payload.get("line"))),
            from_=_text(payload.get("from")),
            body=_text(payload.get("body")),
            code=_optional_text(payload.get("code")),
            received_at=_maybe_timestamp(payload.get("receivedAt")),
            received_at_raw=_text(payload.get("receivedAt")),
            service=Service.from_dict(service_payload) if isinstance(service_payload, Mapping) else None,
            contact=Contact.from_dict(contact_payload) if isinstance(contact_payload, Mapping) else None,
            label=_optional_text(payload.get("label")),
            raw=dict(payload),
        )

    _parse = from_dict


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook delivery.

    The same message id can arrive more than once: a delivery that failed is
    retried on a backoff, so handlers must be idempotent on ``data.id``.
    """

    type: str
    """``"message.received"``, or ``"test"`` for a test delivery from settings."""
    data: Message | None
    """The message.  None only if a future event type carries something else."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_test(self) -> bool:
        return self.type == EVENT_TEST

    @property
    def message(self) -> Message | None:
        """Alias for ``data``."""
        return self.data

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WebhookEvent":
        data = payload.get("data")
        return cls(
            type=_text(payload.get("type")),
            data=Message.from_dict(data) if isinstance(data, Mapping) else None,
            raw=dict(payload),
        )

    _parse = from_dict


def _with_plus(phone: str) -> str:
    return phone if phone.startswith("+") else "+" + phone
