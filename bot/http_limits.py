"""Bound reads from the installed market-data SDK without changing order clients."""
from typing import Any


def bound_market_client(client: Any) -> None:
    # The supported alpaca-py version exposes requests.Session at this boundary.
    # Fail initialization if that contract changes rather than allow unbounded IO.
    session = getattr(client, "_session", None)
    if session is None or not callable(getattr(session, "request", None)):
        raise RuntimeError("Market SDK request boundary is unsupported.")
    request = session.request

    def bounded_request(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = (10, 30)
        return request(*args, **kwargs)

    session.request = bounded_request
    client._retry = 2
    client._retry_wait = 3
