# Contributing

Bug reports and small, focused pull requests are welcome.

Setup:

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

A few house rules:

- The only runtime dependency is `httpx`. Please do not add another one.
- Tests must not touch the network. Stub HTTP with `httpx.MockTransport`, as the
  existing tests do.
- Never commit a real API key, webhook secret, phone number or message. Fixtures use
  obviously fake values such as `clt_example`, `whsec_test_secret` and `13055550100`.
- Keep the sync and async clients in step: if you add a method to one, add it to the other,
  and test both.
- The README and the docstrings describe what the API actually does. If you are unsure
  whether a behaviour is real, check <https://cleat.so/openapi.json> rather than guessing,
  and do not promise that any particular service will accept a Cleat number.
