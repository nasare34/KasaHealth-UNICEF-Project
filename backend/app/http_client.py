"""One shared HTTP client for all outbound calls, so connections get reused
instead of being opened fresh every time."""
from __future__ import annotations

import httpx

from .errors import UpstreamError

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
# 401/403 mean the provider will keep saying no (bad key, blocked network),
# so I don't retry those; I move on to the next provider.
FATAL_STATUS = {401, 403}


def build_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50, keepalive_expiry=30),
        headers={"User-Agent": "KasaHealth/8.0"},
        follow_redirects=False,
    )


def raise_for_upstream(service: str, response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    snippet = response.text[:300].replace("\n", " ")
    raise UpstreamError(
        service,
        f"HTTP {response.status_code}: {snippet}",
        status=response.status_code,
        retryable=response.status_code in RETRYABLE_STATUS,
        fatal_for_provider=response.status_code in FATAL_STATUS,
    )


async def send(service: str, client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Make a request and turn network problems into UpstreamError."""
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise UpstreamError(service, f"timeout: {exc!r}", retryable=True) from exc
    except httpx.TransportError as exc:  # DNS, connection reset, TLS, etc.
        raise UpstreamError(service, f"network: {exc!r}", retryable=True) from exc
    raise_for_upstream(service, response)
    return response
