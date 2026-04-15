"""Synchronous Kakao Local API client.

Plan section 5 defines the three APIs this client wraps:

- ``GET /v2/local/geo/coord2regioncode`` — coordinate to 행정동 lookup
- ``GET /v2/local/search/category`` — category group search
- ``GET /v2/local/search/keyword`` — free-text keyword search

The client owns three responsibilities the plan mandates for *every*
Kakao call: authentication header injection, retry/backoff on 429 and
5xx, and a mandatory 200ms sleep between calls so a single worker never
hammers the rate limit. Pagination is capped at 3 pages (`size=15`) per
plan §5-2, matching the category search's 45-record ceiling.

The client is intentionally synchronous. Batch jobs run inside a Celery
worker where async would add complexity without meaningfully improving
throughput — Kakao's per-app rate limit is the real bottleneck, not
connection concurrency.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class KakaoApiError(RuntimeError):
    """Raised when Kakao returns a non-retryable error or retries exhaust."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class KakaoLocalClient:
    """Thin httpx wrapper around Kakao Local REST endpoints."""

    BASE = "https://dapi.kakao.com"

    # Page / size caps come from the plan and the Kakao docs. Do not bump
    # these without revisiting §5-2 and the daily quota math in §5-6.
    MAX_PAGES = 3
    PAGE_SIZE = 15

    def __init__(
        self,
        *,
        api_key: str,
        sleep_ms: int = 200,
        max_retries: int = 3,
        timeout: float = 10.0,
        read_timeout: float = 15.0,
    ) -> None:
        if not api_key:
            raise KakaoApiError(
                "kakao_rest_api_key is empty. Set KAKAO_REST_API_KEY in .env "
                "before running collector jobs."
            )
        self._api_key = api_key
        self._sleep_s = sleep_ms / 1000.0
        self._max_retries = max_retries
        self._client = httpx.Client(
            base_url=self.BASE,
            headers={
                "Authorization": f"KakaoAK {api_key}",
                "KA": "sdk/1.0.0 os/python lang/ko origin/http%3A%2F%2Flocalhost",
            },
            timeout=httpx.Timeout(timeout, read=read_timeout),
        )

    # ------------------------------------------------------------------
    # Public API wrappers
    # ------------------------------------------------------------------

    def coord2regioncode(self, x: float, y: float) -> list[dict[str, Any]]:
        """Resolve ``(x, y)`` into an array of region descriptors."""

        data = self._get(
            "/v2/local/geo/coord2regioncode.json", params={"x": x, "y": y}
        )
        return data.get("documents", [])

    def search_category(
        self,
        category_group_code: str,
        x: float,
        y: float,
        radius: int,
        *,
        sort: str = "distance",
    ) -> list[dict[str, Any]]:
        """Search places under ``category_group_code`` around ``(x, y)``."""

        return self._paginate(
            "/v2/local/search/category.json",
            base_params={
                "category_group_code": category_group_code,
                "x": x,
                "y": y,
                "radius": radius,
                "sort": sort,
            },
        )

    def search_keyword(
        self,
        query: str,
        *,
        x: float | None = None,
        y: float | None = None,
        radius: int | None = None,
        sort: str = "accuracy",
    ) -> list[dict[str, Any]]:
        """Search places matching ``query``. ``(x, y, radius)`` are optional."""

        params: dict[str, Any] = {"query": query, "sort": sort}
        if x is not None:
            params["x"] = x
        if y is not None:
            params["y"] = y
        if radius is not None:
            params["radius"] = radius
        return self._paginate("/v2/local/search/keyword.json", base_params=params)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KakaoLocalClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001 - stdlib signature
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _paginate(
        self, path: str, *, base_params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Run ``path`` across up to :attr:`MAX_PAGES` pages."""

        documents: list[dict[str, Any]] = []
        for page in range(1, self.MAX_PAGES + 1):
            params = {**base_params, "page": page, "size": self.PAGE_SIZE}
            payload = self._get(path, params=params)
            docs = payload.get("documents", []) or []
            documents.extend(docs)
            meta = payload.get("meta") or {}
            # Kakao sets is_end=True when the last page has been returned.
            # We also short-circuit if the page wasn't full, to save a
            # round trip against the daily quota.
            if meta.get("is_end") or len(docs) < self.PAGE_SIZE:
                break
        return documents

    def _get(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        """Issue ``GET path`` with retry + 200ms pacing."""

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.get(path, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                # Network-level hiccup: treat like a 5xx and back off.
                last_error = exc
                logger.warning(
                    "kakao network error on %s attempt=%d err=%s",
                    path,
                    attempt + 1,
                    exc,
                )
                self._backoff(attempt)
                continue

            status = response.status_code
            if status == 200:
                time.sleep(self._sleep_s)
                try:
                    return response.json()
                except ValueError as exc:
                    raise KakaoApiError(
                        f"kakao returned non-json body for {path}: {exc}"
                    ) from exc

            if status == 429:
                retry_after = self._parse_retry_after(response)
                logger.warning(
                    "kakao 429 on %s attempt=%d retry_after=%.2fs",
                    path,
                    attempt + 1,
                    retry_after,
                )
                time.sleep(retry_after)
                continue

            if 500 <= status < 600:
                logger.warning(
                    "kakao %d on %s attempt=%d body=%s",
                    status,
                    path,
                    attempt + 1,
                    response.text[:200],
                )
                self._backoff(attempt)
                last_error = KakaoApiError(
                    f"kakao 5xx on {path}: {status}", status_code=status
                )
                continue

            # Any other 4xx is a caller bug (bad params, auth, ...). Raise
            # immediately so the region fails loudly and we don't burn
            # quota retrying something that will never succeed.
            raise KakaoApiError(
                f"kakao {status} on {path}: {response.text[:500]}",
                status_code=status,
            )

        # Retries exhausted.
        if isinstance(last_error, KakaoApiError):
            raise last_error
        raise KakaoApiError(
            f"kakao request to {path} failed after {self._max_retries} attempts: "
            f"{last_error!r}"
        )

    def _backoff(self, attempt: int) -> None:
        """Sleep ``2**attempt`` seconds with light jitter."""

        delay = (2 ** attempt) + random.uniform(0, 0.25)
        time.sleep(delay)

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if not raw:
            return 1.0
        try:
            return max(float(raw), 0.1)
        except ValueError:
            # HTTP date format is allowed but Kakao only returns seconds.
            return 1.0


def get_kakao_client(**overrides: Any) -> KakaoLocalClient:
    """Factory that pulls the API key from :func:`get_settings`.

    ``overrides`` lets tests inject ``sleep_ms=0`` or a stub api_key.
    """

    settings = get_settings()
    return KakaoLocalClient(
        api_key=overrides.pop("api_key", settings.kakao_rest_api_key),
        **overrides,
    )
