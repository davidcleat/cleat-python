"""Sync and async HTTP clients for the Cleat API."""

from __future__ import annotations

import os
import random
import time
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Iterable, Mapping, Sequence

import httpx

from .errors import (
    CleatConfigurationError,
    CleatError,
    CleatTimeoutError,
    RateLimitError,
    error_from_response,
)
from .models import Line, Message, format_timestamp

__all__ = [
    "CleatClient",
    "AsyncCleatClient",
    "DEFAULT_BASE_URL",
    "API_KEY_ENV_VAR",
    "MAX_LIMIT",
]

DEFAULT_BASE_URL = "https://cleat.so"
API_KEY_ENV_VAR = "CLEAT_API_KEY"
MAX_LIMIT = 200
"""The largest ``limit`` the messages endpoint accepts."""

_LINES_PATH = "/api/v1/lines"
_DEFAULT_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 20.0


def _timestamp_param(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return format_timestamp(value)
    if isinstance(value, str):
        return value
    raise CleatConfigurationError(
        f"Expected a datetime or an ISO 8601 string, got {type(value).__name__}."
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _message_matches(message: Message, from_: str | None, service: str | None) -> bool:
    if from_ is not None and _normalise_sender(message.from_) != _normalise_sender(from_):
        return False
    if service is not None:
        wanted = service.strip().casefold()
        found = message.service
        candidates = {c.casefold() for c in (found.id, found.name) if c} if found else set()
        if wanted not in candidates:
            return False
    return True


def _normalise_sender(value: str) -> str:
    """Compare senders leniently: case-insensitive, and a leading plus is optional.

    The API reports the sender as the network gave it — ``"13055550100"`` or an
    alphanumeric sender id like ``"VERIFY"`` — so a caller who writes
    ``"+13055550100"`` still matches.
    """
    return value.strip().lstrip("+").casefold()


class _BaseClient:
    """Shared configuration, request building and response handling."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        transport: Any | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
        if not key or not key.strip():
            raise CleatConfigurationError(
                "A Cleat API key is required. Pass api_key=... or set the "
                f"{API_KEY_ENV_VAR} environment variable. Keys are created by the "
                "workspace owner in workspace settings and start with clt_."
            )
        if max_retries < 0:
            raise CleatConfigurationError("max_retries cannot be negative.")

        self.api_key = key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # -- request plumbing -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        from . import __version__

        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": f"cleat-python/{__version__}",
        }

    @staticmethod
    def _messages_path(line_id: str) -> str:
        if not isinstance(line_id, str) or not line_id.strip():
            raise CleatConfigurationError("line_id is required.")
        return f"{_LINES_PATH}/{line_id.strip()}/messages"

    @staticmethod
    def _messages_params(
        after: datetime | str | None,
        before: datetime | str | None,
        limit: int | None,
    ) -> dict[str, str | int]:
        params: dict[str, str | int] = {}
        if after is not None:
            params["after"] = _timestamp_param(after)
        if before is not None:
            params["before"] = _timestamp_param(before)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise CleatConfigurationError("limit must be an integer.")
            if not 1 <= limit <= MAX_LIMIT:
                raise CleatConfigurationError(f"limit must be between 1 and {MAX_LIMIT}.")
            params["limit"] = limit
        return params

    def _extract_data(self, response: httpx.Response) -> Sequence[Any]:
        if response.status_code >= 400:
            raise error_from_response(response)
        try:
            payload = response.json()
        except Exception as exc:
            raise CleatError(f"The API returned a body that is not JSON: {exc}") from exc
        if isinstance(payload, Mapping):
            data = payload.get("data")
            if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
                return data
        if isinstance(payload, list):
            return payload
        raise CleatError("The API response did not contain a data array.")

    def _retry_delay(self, attempt: int, error: RateLimitError) -> float:
        """Exponential backoff with jitter, for a 429.

        VERIFIED 2026-09-23: Cleat's 429 sends NO ``Retry-After`` header and no
        rate-limit headers at all, so the wait here is entirely the client's own
        invention. The limit is 120 requests per minute per key. A
        ``Retry-After`` is honoured if one ever appears.
        """
        if error.retry_after is not None:
            return min(float(error.retry_after), _BACKOFF_CAP_SECONDS)
        window = min(_BACKOFF_BASE_SECONDS * (2**attempt), _BACKOFF_CAP_SECONDS)
        # Full jitter over half the window, so parallel workers do not resync.
        return window * (0.5 + random.random() * 0.5)


class CleatClient(_BaseClient):
    """A synchronous client for the Cleat API.

        from cleat import CleatClient

        with CleatClient() as cleat:          # reads CLEAT_API_KEY
            for line in cleat.list_lines():
                print(line.phone_e164, line.status)

    Args:
        api_key: a key from workspace settings, starting with ``clt_``. Falls
            back to the ``CLEAT_API_KEY`` environment variable.
        base_url: override the API host. Rarely useful.
        timeout: per-request timeout in seconds.
        max_retries: how many times to retry a 429 before raising
            :class:`~cleat.errors.RateLimitError`.
        transport: an ``httpx`` transport, for tests (``httpx.MockTransport``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        transport: Any | None = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            transport=transport,
        )
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers=self._headers(),
        )
        self._sleep = time.sleep

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._http.close()

    def __enter__(self) -> "CleatClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- requests ---------------------------------------------------------

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Sequence[Any]:
        attempt = 0
        while True:
            try:
                response = self._http.get(path, params=dict(params or {}))
            except httpx.TimeoutException as exc:
                raise CleatTimeoutError(f"The request to {path} timed out: {exc}") from exc
            try:
                return self._extract_data(response)
            except RateLimitError as error:
                if attempt >= self.max_retries:
                    raise
                self._sleep(self._retry_delay(attempt, error))
                attempt += 1

    def list_lines(self) -> list[Line]:
        """Every line in this key's workspace, newest first.

        Released lines are still listed, so an id in your own records still
        resolves to something.
        """
        return [Line.from_dict(item) for item in self._get(_LINES_PATH) if isinstance(item, Mapping)]

    def list_messages(
        self,
        line_id: str,
        *,
        after: datetime | str | None = None,
        before: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        """The messages a line received.

        Args:
            line_id: the line's uuid.
            after: only messages received strictly after this moment. This also
                switches the order to OLDEST FIRST, so you can walk the results
                in order and keep the last ``received_at_raw`` as your cursor.
            before: only messages received strictly before this moment; results
                come back newest first.
            limit: 1..200, default 50 server-side.

        With neither ``after`` nor ``before`` you get the newest messages,
        newest first.
        """
        return [
            Message.from_dict(item)
            for item in self._get(
                self._messages_path(line_id),
                self._messages_params(after, before, limit),
            )
            if isinstance(item, Mapping)
        ]

    def wait_for_message(
        self,
        line_id: str,
        *,
        since: datetime | str | None = None,
        from_: str | None = None,
        service: str | None = None,
        timeout: float = 120.0,
        poll_interval: float = 3.0,
        require_code: bool = True,
    ) -> Message:
        """Poll the line until a matching message arrives, then return it.

        Args:
            line_id: the line's uuid.
            since: only consider messages received after this moment. Defaults
                to now, so a message that arrived before the call is ignored —
                pass ``since`` explicitly if you want to look slightly back.
            from_: only messages from this sender. Matched case-insensitively,
                with an optional leading plus, against the sender the network
                reported.
            service: only messages Cleat attributed to this service, matched
                case-insensitively against the service's id or name (e.g.
                ``"facebook"`` or ``"Facebook"``). Recognition is conservative,
                so a real message can have no service at all; prefer ``from_``
                if you know the sender.
            timeout: give up after this many seconds.
            poll_interval: seconds between polls. The API allows 120 requests a
                minute per key; the default of 3s leaves room for other work.
            require_code: when True (the default) only a message whose ``code``
                was extracted counts as a match. Set False to take the first
                matching message whether or not a code was found, and read
                ``body`` yourself.

        Raises:
            CleatTimeoutError: nothing matched before ``timeout``.
        """
        deadline = time.monotonic() + timeout
        cursor: datetime | str = since if since is not None else _now()
        while True:
            messages = self.list_messages(line_id, after=cursor, limit=MAX_LIMIT)
            match, cursor = _scan(messages, cursor, from_, service, require_code)
            if match is not None:
                return match
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CleatTimeoutError(_timeout_message(line_id, timeout, from_, service))
            self._sleep(min(poll_interval, remaining))

    def wait_for_code(
        self,
        line_id: str,
        *,
        since: datetime | str | None = None,
        from_: str | None = None,
        service: str | None = None,
        timeout: float = 120.0,
        poll_interval: float = 3.0,
    ) -> str:
        """Poll the line until a code arrives, and return just the code.

        Takes the same arguments as :meth:`wait_for_message`.

        The code is what Cleat extracted from the message for display, and
        extraction is best effort. When the exact text matters — a code split
        oddly, a message carrying more than one number, or the transcript of a
        code read out over a call — use :meth:`wait_for_message` instead and
        read ``message.body`` yourself. The string this returns throws the
        transcript away.

        Raises:
            CleatTimeoutError: no coded message arrived before ``timeout``.
        """
        return self.wait_for_message(
            line_id,
            since=since,
            from_=from_,
            service=service,
            timeout=timeout,
            poll_interval=poll_interval,
            require_code=True,
        ).code or ""


class AsyncCleatClient(_BaseClient):
    """An asyncio client for the Cleat API.

        import asyncio
        from cleat import AsyncCleatClient

        async def main():
            async with AsyncCleatClient() as cleat:
                lines = await cleat.list_lines()
                message = await cleat.wait_for_code(lines[0].id, timeout=180)
                print(message.code)

        asyncio.run(main())

    Takes the same arguments as :class:`CleatClient`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        transport: Any | None = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            transport=transport,
        )
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers=self._headers(),
        )
        import asyncio

        self._sleep = asyncio.sleep

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncCleatClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Sequence[Any]:
        attempt = 0
        while True:
            try:
                response = await self._http.get(path, params=dict(params or {}))
            except httpx.TimeoutException as exc:
                raise CleatTimeoutError(f"The request to {path} timed out: {exc}") from exc
            try:
                return self._extract_data(response)
            except RateLimitError as error:
                if attempt >= self.max_retries:
                    raise
                await self._sleep(self._retry_delay(attempt, error))
                attempt += 1

    async def list_lines(self) -> list[Line]:
        """Every line in this key's workspace, newest first. Released lines are still listed."""
        data = await self._get(_LINES_PATH)
        return [Line.from_dict(item) for item in data if isinstance(item, Mapping)]

    async def list_messages(
        self,
        line_id: str,
        *,
        after: datetime | str | None = None,
        before: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        """The messages a line received. See :meth:`CleatClient.list_messages`."""
        data = await self._get(
            self._messages_path(line_id),
            self._messages_params(after, before, limit),
        )
        return [Message.from_dict(item) for item in data if isinstance(item, Mapping)]

    async def wait_for_message(
        self,
        line_id: str,
        *,
        since: datetime | str | None = None,
        from_: str | None = None,
        service: str | None = None,
        timeout: float = 120.0,
        poll_interval: float = 3.0,
        require_code: bool = True,
    ) -> Message:
        """Poll until a matching message arrives. See :meth:`CleatClient.wait_for_message`."""
        deadline = time.monotonic() + timeout
        cursor: datetime | str = since if since is not None else _now()
        while True:
            messages = await self.list_messages(line_id, after=cursor, limit=MAX_LIMIT)
            match, cursor = _scan(messages, cursor, from_, service, require_code)
            if match is not None:
                return match
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CleatTimeoutError(_timeout_message(line_id, timeout, from_, service))
            await self._sleep(min(poll_interval, remaining))

    async def wait_for_code(
        self,
        line_id: str,
        *,
        since: datetime | str | None = None,
        from_: str | None = None,
        service: str | None = None,
        timeout: float = 120.0,
        poll_interval: float = 3.0,
    ) -> str:
        """Poll until a code arrives and return just the code.

        See :meth:`CleatClient.wait_for_code`, including why you may want
        :meth:`wait_for_message` and the full ``body`` instead.
        """
        message = await self.wait_for_message(
            line_id,
            since=since,
            from_=from_,
            service=service,
            timeout=timeout,
            poll_interval=poll_interval,
            require_code=True,
        )
        return message.code or ""


def _scan(
    messages: Iterable[Message],
    cursor: datetime | str,
    from_: str | None,
    service: str | None,
    require_code: bool,
) -> tuple[Message | None, datetime | str]:
    """Walk one page of ``after=`` results, returning the first match and the new cursor.

    Because ``after`` returns OLDEST FIRST, walking the page in order means the
    cursor only ever moves forward, and advancing it per message means a
    message we have already seen is never fetched twice.
    """
    for message in messages:
        if message.received_at_raw:
            cursor = message.received_at_raw
        elif message.received_at is not None:
            cursor = message.received_at
        if not _message_matches(message, from_, service):
            continue
        if require_code and not message.code:
            continue
        return message, cursor
    return None, cursor


def _timeout_message(line_id: str, timeout: float, from_: str | None, service: str | None) -> str:
    filters = []
    if from_ is not None:
        filters.append(f"from {from_}")
    if service is not None:
        filters.append(f"service {service}")
    suffix = f" matching {' and '.join(filters)}" if filters else ""
    return (
        f"No message{suffix} arrived on line {line_id} within {timeout:g}s. "
        "If the line is new, check that the sending service actually sent to it; "
        "nothing can guarantee that a given service will accept a given number."
    )
