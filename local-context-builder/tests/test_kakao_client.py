"""Unit tests for :class:`KakaoLocalClient` using respx mocks.

Every HTTP call is intercepted — no real Kakao traffic is ever issued.
The tests also stub out ``time.sleep`` so the 200ms pacing and exponential
backoff do not slow the suite down.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.kakao_local_client import KakaoApiError, KakaoLocalClient


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip real sleeps so retries + 200ms pacing don't slow the suite."""

    monkeypatch.setattr(
        "app.collectors.kakao_local_client.time.sleep", lambda _seconds: None
    )


def _make_client() -> KakaoLocalClient:
    return KakaoLocalClient(api_key="test-key", sleep_ms=0, max_retries=3)


@respx.mock
def test_search_category_returns_documents_and_respects_is_end() -> None:
    route = respx.get(
        "https://dapi.kakao.com/v2/local/search/category.json"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "documents": [
                    {
                        "id": "1",
                        "place_name": "가나다 식당",
                        "x": "127.01",
                        "y": "37.25",
                    }
                ],
                "meta": {"is_end": True},
            },
        )
    )

    client = _make_client()
    try:
        docs = client.search_category("FD6", x=127.0, y=37.25, radius=1000)
    finally:
        client.close()

    assert len(docs) == 1
    assert docs[0]["place_name"] == "가나다 식당"
    # is_end short-circuits after page 1.
    assert route.call_count == 1


@respx.mock
def test_search_category_paginates_until_empty_page() -> None:
    page_docs = [
        {"id": str(i), "place_name": f"p{i}", "x": "127.0", "y": "37.0"}
        for i in range(15)
    ]
    responses = [
        httpx.Response(
            200, json={"documents": page_docs, "meta": {"is_end": False}}
        ),
        httpx.Response(
            200, json={"documents": page_docs, "meta": {"is_end": False}}
        ),
        # Short page on page 3 → paginator exits naturally.
        httpx.Response(
            200,
            json={"documents": page_docs[:5], "meta": {"is_end": True}},
        ),
    ]
    respx.get(
        "https://dapi.kakao.com/v2/local/search/category.json"
    ).mock(side_effect=responses)

    client = _make_client()
    try:
        docs = client.search_category("FD6", x=127.0, y=37.0, radius=1000)
    finally:
        client.close()

    assert len(docs) == 35  # 15 + 15 + 5
    # Only 3 pages ever get requested (MAX_PAGES cap).


@respx.mock
def test_search_category_stops_at_max_pages() -> None:
    full_page = {
        "documents": [
            {"id": str(i), "place_name": f"p{i}", "x": "127.0", "y": "37.0"}
            for i in range(15)
        ],
        "meta": {"is_end": False},
    }
    route = respx.get(
        "https://dapi.kakao.com/v2/local/search/category.json"
    ).mock(return_value=httpx.Response(200, json=full_page))

    client = _make_client()
    try:
        docs = client.search_category("FD6", x=127.0, y=37.0, radius=1000)
    finally:
        client.close()

    assert len(docs) == 45
    assert route.call_count == 3  # hard cap at MAX_PAGES


@respx.mock
def test_429_retries_with_retry_after_header() -> None:
    route = respx.get(
        "https://dapi.kakao.com/v2/local/search/category.json"
    ).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(
                200, json={"documents": [], "meta": {"is_end": True}}
            ),
        ]
    )

    client = _make_client()
    try:
        docs = client.search_category("FD6", x=127.0, y=37.0, radius=1000)
    finally:
        client.close()

    assert docs == []
    assert route.call_count == 2


@respx.mock
def test_5xx_retries_then_succeeds() -> None:
    route = respx.get(
        "https://dapi.kakao.com/v2/local/search/keyword.json"
    ).mock(
        side_effect=[
            httpx.Response(500, text="oops"),
            httpx.Response(503, text="oops"),
            httpx.Response(
                200,
                json={
                    "documents": [
                        {"id": "9", "place_name": "hi", "x": "0", "y": "0"}
                    ],
                    "meta": {"is_end": True},
                },
            ),
        ]
    )

    client = _make_client()
    try:
        docs = client.search_keyword("영통 맛집")
    finally:
        client.close()

    assert len(docs) == 1
    assert route.call_count == 3


@respx.mock
def test_5xx_exhausts_retries_and_raises() -> None:
    respx.get(
        "https://dapi.kakao.com/v2/local/search/keyword.json"
    ).mock(return_value=httpx.Response(502, text="bad gateway"))

    client = _make_client()
    try:
        with pytest.raises(KakaoApiError) as err:
            client.search_keyword("서울 카페")
    finally:
        client.close()

    assert err.value.status_code == 502


@respx.mock
def test_4xx_other_than_429_raises_immediately() -> None:
    route = respx.get(
        "https://dapi.kakao.com/v2/local/search/category.json"
    ).mock(return_value=httpx.Response(401, text="unauthorized"))

    client = _make_client()
    try:
        with pytest.raises(KakaoApiError) as err:
            client.search_category("FD6", x=127.0, y=37.0, radius=1000)
    finally:
        client.close()

    assert err.value.status_code == 401
    # No retries on hard 4xx.
    assert route.call_count == 1


@respx.mock
def test_coord2regioncode_wrapper() -> None:
    respx.get(
        "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "documents": [
                    {"region_type": "H", "region_3depth_name": "영통동"}
                ],
                "meta": {"total_count": 1},
            },
        )
    )

    client = _make_client()
    try:
        regions = client.coord2regioncode(127.05, 37.25)
    finally:
        client.close()

    assert regions[0]["region_3depth_name"] == "영통동"


def test_missing_api_key_raises() -> None:
    with pytest.raises(KakaoApiError):
        KakaoLocalClient(api_key="", sleep_ms=0)
