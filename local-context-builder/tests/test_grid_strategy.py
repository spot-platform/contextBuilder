"""Unit tests for :mod:`app.collectors.grid_strategy`.

These tests drive :func:`plan_cells` with lightweight fake region objects
so they never touch the DB or the Kakao API.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.collectors.grid_strategy import (
    DEFAULT_RADIUS_M,
    DENSE_THRESHOLD,
    plan_cells,
)


@dataclass
class FakeRegion:
    """Duck-typed stand-in for :class:`RegionMaster` with only the bits
    :func:`plan_cells` reads."""

    region_code: str = "R001"
    center_lng: float = 127.0
    center_lat: float = 37.25
    bbox_min_lng: float | None = None
    bbox_min_lat: float | None = None
    bbox_max_lng: float | None = None
    bbox_max_lat: float | None = None


def test_probe_none_returns_single_default_cell() -> None:
    region = FakeRegion()
    cells = plan_cells(region, probe_count=None)
    assert cells == [(127.0, 37.25, DEFAULT_RADIUS_M)]


def test_probe_below_threshold_returns_default() -> None:
    region = FakeRegion(
        bbox_min_lng=126.99,
        bbox_min_lat=37.24,
        bbox_max_lng=127.01,
        bbox_max_lat=37.26,
    )
    cells = plan_cells(region, probe_count=DENSE_THRESHOLD - 1)
    assert cells == [(127.0, 37.25, DEFAULT_RADIUS_M)]


def test_dense_region_with_bbox_splits_into_four_cells() -> None:
    region = FakeRegion(
        bbox_min_lng=127.00,
        bbox_min_lat=37.20,
        bbox_max_lng=127.04,
        bbox_max_lat=37.24,
    )
    cells = plan_cells(region, probe_count=DENSE_THRESHOLD)

    assert len(cells) == 4

    centres = {(round(lng, 4), round(lat, 4)) for lng, lat, _ in cells}
    # 2x2 expected centres: at ±1/4 of bbox extent from min/max.
    assert centres == {
        (127.01, 37.21),  # SW
        (127.03, 37.21),  # SE
        (127.01, 37.23),  # NW
        (127.03, 37.23),  # NE
    }

    # Radius positive and within the Kakao 20km cap.
    for _, _, radius in cells:
        assert radius > 0
        assert radius <= 20_000


def test_dense_region_without_bbox_falls_back_to_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    region = FakeRegion()  # no bbox
    with caplog.at_level("WARNING"):
        cells = plan_cells(region, probe_count=DENSE_THRESHOLD + 5)

    assert cells == [(127.0, 37.25, DEFAULT_RADIUS_M)]
    assert any("has no bbox" in rec.message for rec in caplog.records)


def test_threshold_boundary_is_inclusive() -> None:
    """``probe_count == threshold`` must already split."""

    region = FakeRegion(
        bbox_min_lng=127.00,
        bbox_min_lat=37.20,
        bbox_max_lng=127.04,
        bbox_max_lat=37.24,
    )
    cells_at = plan_cells(region, probe_count=DENSE_THRESHOLD)
    cells_below = plan_cells(region, probe_count=DENSE_THRESHOLD - 1)
    assert len(cells_at) == 4
    assert len(cells_below) == 1
