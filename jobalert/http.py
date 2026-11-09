"""Polite HTTP client: honest User-Agent, per-host delay, limited retries."""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        min_delay_seconds: float = 1.5,
        timeout_seconds: float = 30,
        retries: int = 3,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json, text/html;q=0.9"},
            timeout=timeout_seconds,
            follow_redirects=True,
        )
        self._min_delay = min_delay_seconds
        self._retries = retries
        self._last_request_at: dict[str, float] = {}

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            remaining = self._min_delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlparse(url).hostname or ""
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            self._wait_for_host(host)
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                log.warning("Network error on %s (attempt %d): %s", host, attempt + 1, exc)
            else:
                if response.status_code not in RETRY_STATUSES:
                    # 4xx such as 403 are not retried: we respect blocks.
                    response.raise_for_status()
                    return response
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                log.warning("HTTP %d from %s (attempt %d)", response.status_code, host, attempt + 1)
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(min(int(retry_after), 60))
            if attempt < self._retries:
                time.sleep(2 ** attempt * 2)
        assert last_error is not None
        raise last_error

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs).json()

    def post_json(self, url: str, payload: Any, **kwargs: Any) -> Any:
        return self.request("POST", url, json=payload, **kwargs).json()

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.request("GET", url, **kwargs).text

    def close(self) -> None:
        self._client.close()
