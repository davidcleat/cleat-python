# cleatapi

Python client for the [Cleat](https://cleat.so) API: read the SMS/2FA codes and call transcripts that arrive on your Cleat phone lines.

Cleat rents ID-verified US mobile numbers that receive text messages and transcripts of incoming calls. A line is $24.99 a month or $249.90 a year. A line belongs to one identity-verified owner, and teammates in the same workspace read the same inbox at no extra cost. Codes show up in a web inbox, by email, on Telegram, by signed webhook, through this REST API, and via an MCP server. Lines are receive-only: no outbound texts, no outbound calls, no 911.

## Install

```
pip install cleatapi
```

```python
import cleatapi
```

Python 3.10 or newer. The only runtime dependency is [httpx](https://www.python-httpx.org/).

Both the distribution and the import name are [`cleatapi`](https://pypi.org/project/cleatapi/).
The bare name `cleat` on PyPI belongs to an unrelated project, so `pip install cleat` would
install something else. The repository keeps the name `cleat-python`.

## Example

Waiting for a code, for example in an end-to-end test that signs your own QA account
into your own staging app:

```python
import os

from cleatapi import CleatClient, CleatTimeoutError

with CleatClient(os.environ["CLEAT_API_KEY"]) as cleat:
    lines = cleat.list_lines()
    for line in lines:
        print(line.id, line.phone_e164, line.status, line.label)

    line = lines[0]

    # Trigger the code however your app does it, then wait for it to land.
    # wait_for_code only looks at messages that arrive after this call, so
    # start it before (or right after) asking for the code.
    try:
        code = cleat.wait_for_code(line.id, timeout=180)
    except CleatTimeoutError as error:
        raise SystemExit(error)

    print("code:", code)
```

`wait_for_code` returns the code Cleat extracted from the message. Extraction is best effort,
so when the exact text matters — a code split oddly, a message with more than one number in
it, or a code read out over a call — wait for the whole message instead and read `body`
yourself:

```python
message = cleat.wait_for_message(line.id, timeout=180)
print(message.code, "from", message.from_)
print(message.body)          # the full text, or the call transcript
```

Reading history instead of waiting:

```python
from datetime import datetime, timedelta, timezone

since = datetime.now(timezone.utc) - timedelta(days=1)
for message in cleat.list_messages(line.id, after=since, limit=200):
    print(message.received_at, message.label or message.from_, message.body)
```

Passing `after` returns messages oldest first, so you can walk them in order and keep the
last `received_at_raw` as your cursor for the next call. Passing `before` pages backwards,
newest first.

There is an async client with the same surface:

```python
import asyncio

from cleatapi import AsyncCleatClient


async def main() -> None:
    async with AsyncCleatClient() as cleat:      # reads CLEAT_API_KEY
        line = (await cleat.list_lines())[0]
        print(await cleat.wait_for_code(line.id, timeout=180))


asyncio.run(main())
```

### Webhooks

Cleat POSTs `{"type": "message.received", "data": <message>}` to your endpoint as soon as it
has stored a message, signed with the endpoint's `whsec_` secret. Verify over the raw request
bytes, before anything parses or re-serialises them. Here it is in Flask:

```python
import os

from flask import Flask, request

from cleatapi import CleatSignatureError, verify_webhook

app = Flask(__name__)
SECRET = os.environ["CLEAT_WEBHOOK_SECRET"]


@app.post("/cleat-webhook")
def cleat_webhook():
    try:
        event = verify_webhook(SECRET, request.get_data(), request.headers.get("cleat-signature", ""))
    except CleatSignatureError:
        return "", 400

    message = event.data
    if message is not None and not event.is_test:
        # A failed delivery is retried, so the same message id can arrive more
        # than once. Make this idempotent on message.id.
        handle(message.id, message.code, message.body)
    return "", 204
```

Answer any 2xx within 10 seconds. If your endpoint was down, catch up with
`list_messages(line_id, after=<the receivedAt of the last message you handled>)`.

## Getting an API key

1. Create a Cleat account at [cleat.so](https://cleat.so) and subscribe a line.
2. Verify your identity once, when Cleat asks. Until that is done the line still receives and
   keeps every message, but nothing can be read through the API (HTTP 403, `verify_first`).
3. In workspace settings, create an API key. Only the workspace owner can do this. A key
   belongs to one workspace, and can optionally be limited to named lines and given an expiry
   date.
4. Put it in the environment as `CLEAT_API_KEY`, or pass it to the client. Revoking a key
   stops it working immediately.

Webhook endpoints are created in the same place, also by the workspace owner, up to five per
workspace. The signing secret is shown once.

## Limits worth knowing before you build

- The lines are **receive-only**. No outbound texts, no outbound calls, no 911.
- US mobile numbers only.
- The workspace owner verifies their identity once. Until then, reading messages answers 403 (`verify_first`); `list_lines` works regardless.
- One identity-verified owner per line. Teammates in the workspace share the inbox.
- Most services that refuse VoIP numbers accept a real mobile line, but nobody can promise
  that a particular service will accept a particular number.
- 120 requests per minute per API key. Over that, the API answers 429 — with no `Retry-After`
  and no rate-limit headers, so this client backs off on its own (exponential with jitter,
  three retries by default; set `max_retries` to change it).
- A line whose subscription lapses goes to `grace`: it keeps receiving and storing texts, but
  reading them answers 402 until it is resubscribed.
- A line outside a scoped key's allow-list answers 404, exactly like a line in somebody
  else's workspace. If a line you can see in the dashboard 404s, check the key's scope.
- Cleat recognises the sending service conservatively, because several services share one
  short code, so `message.service` is often `None` on a perfectly ordinary message. Filter on
  the sender if you know it.
- A code read out by an automated call arrives as an ordinary message: transcript in `body`,
  the calling number in `from_`, `code` filled in. Nothing marks it as a call.

## API surface

| | |
| --- | --- |
| `CleatClient(api_key=None, *, base_url, timeout, max_retries, transport)` | Sync client. Falls back to `CLEAT_API_KEY`. Context manager, plus `close()`. |
| `AsyncCleatClient(...)` | Same arguments and methods, `async with`, plus `aclose()`. |
| `.list_lines()` | Every line in the key's workspace, newest first. Released lines included. |
| `.list_messages(line_id, *, after, before, limit)` | Messages on a line. `after` and `before` take a `datetime` or an ISO 8601 string. |
| `.wait_for_code(line_id, *, since, from_, service, timeout, poll_interval)` | Polls until a code arrives and returns it as a `str`. Raises `CleatTimeoutError`. |
| `.wait_for_message(line_id, ...)` | The same poll, returning the whole `Message` — use it when you need `body`, `from_` or the call transcript. `require_code=False` takes the first matching message whether or not a code was extracted. |
| `verify_webhook(secret, raw_body, header, *, tolerance=300)` | Module-level. Verifies a delivery and returns a `WebhookEvent`. |
| `parse_signature_header(header)` | The pieces of a `cleat-signature` header, if you need them. |

Models are frozen dataclasses: `Line`, `Message`, `MessageLine`, `Service`, `Contact`,
`WebhookEvent`. Timestamps are parsed to timezone-aware UTC `datetime`, with the original
string kept alongside as `created_at_raw` / `received_at_raw`. Every model keeps the whole
decoded payload in `.raw`, and a field this version does not know about never breaks a parse.

Errors all derive from `CleatError`: `CleatAPIError` (with `status`, `message`, `code`,
`response`) and its subclasses `BadRequestError` (400), `AuthenticationError` (401),
`KeyExpiredError` (401 `key_expired`), `LineOnHoldError` (402), `VerificationRequiredError`
(403 `verify_first`), `NotFoundError` (404), `RateLimitError` (429), plus `CleatTimeoutError`,
`CleatConfigurationError`, and `CleatSignatureError` with
`MalformedSignatureHeaderError` / `SignatureTimestampError` / `InvalidSignatureError`.

## Development

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The tests stub every HTTP call with `httpx.MockTransport` and open no sockets.

## Links

- [cleat.so](https://cleat.so)
- [cleat.so/for/developers](https://cleat.so/for/developers) — the API reference and OpenAPI document
- [CONTRIBUTING.md](CONTRIBUTING.md)

MIT licensed.
