"""Unit tests for ``app.services.publisher_service.verify_quality``.

We don't spin up a real Postgres here — the integration-qa agent
covers that. Instead we stub the SQLAlchemy session surface
``publisher_service.verify_quality`` actually touches (``execute``
and ``scalars``) with a scripted in-memory fake. This keeps the test
readable and isolated from query-string internals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.services import publisher_service


@dataclass
class _FeatureRow:
    region_id: int
    dataset_version: str
    raw_place_count: int = 10
    food_density: float = 0.3
    cafe_density: float = 0.2
    activity_density: float = 0.1
    nightlife_density: float = 0.1
    lesson_density: float = 0.05
    park_access_score: float = 0.3
    culture_score: float = 0.4
    night_liveliness_score: float = 0.2
    casual_meetup_score: float = 0.5
    lesson_spot_score: float = 0.3
    solo_activity_score: float = 0.35
    group_activity_score: float = 0.4
    kakao_raw_score: float = 0.4
    real_data_score: float = 0.0
    blended_score: float = 0.4


@dataclass
class _PRWRow:
    dataset_version: str
    persona_type: str
    region_id: int
    affinity_score: float = 0.5
    create_offer_score: float = 0.5
    create_request_score: float = 0.5
    join_score: float = 0.5


@dataclass
class _SSSRow:
    dataset_version: str
    region_id: int
    spot_type: str
    category: str
    final_weight: float = 0.5


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        if not self._rows:
            return 0
        v = self._rows[0]
        return v[0] if isinstance(v, tuple) else v

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        v = self._rows[0]
        return v[0] if isinstance(v, tuple) else v

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeScalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _sql_text(stmt) -> str:
    try:
        return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    except Exception:
        return str(stmt).lower()


def _from_tables(stmt) -> set[str]:
    try:
        return {t.name.lower() for t in stmt.get_final_froms()}
    except Exception:
        return set()


@dataclass
class FakeSession:
    active_region_ids: list[int]
    features: list[_FeatureRow]
    prws: list[_PRWRow]
    ssss: list[_SSSRow]
    committed: bool = field(default=False, init=False)
    added: list[Any] = field(default_factory=list, init=False)

    def execute(self, stmt):
        sql = _sql_text(stmt)
        tables = _from_tables(stmt)
        # Ordered queries that ``verify_quality`` + ``publish`` issue:
        # 1. select(RegionMaster.id)   where active, target_city
        # 2. select(RegionFeature.region_id) where dataset_version=...
        # 3. select(DatasetVersion.version_name) previous success → tests: none
        # 4. select(func.count(RegionFeature.id)) — cur / prev counts
        # 5. select(func.count(SpotSeedDataset.id)) — place_count
        # 6. select(DatasetVersion) where version_name=... (for existing check)
        if "count(" in sql:
            if "spot_seed_dataset" in tables or "spot_seed_dataset" in sql:
                return _FakeResult([(len(self.ssss),)])
            if "region_feature" in tables or "region_feature" in sql:
                return _FakeResult([(len(self.features),)])
            return _FakeResult([(0,)])
        if "region_master" in tables:
            return _FakeResult([(rid,) for rid in self.active_region_ids])
        if "region_feature" in tables:
            return _FakeResult([(f.region_id,) for f in self.features])
        if "dataset_version" in tables:
            return _FakeResult([])
        return _FakeResult([])

    def scalars(self, stmt):
        tables = _from_tables(stmt)
        if "region_feature" in tables:
            return _FakeScalars(self.features)
        if "persona_region_weight" in tables:
            return _FakeScalars(self.prws)
        if "spot_seed_dataset" in tables:
            return _FakeScalars(self.ssss)
        return _FakeScalars([])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


def _make_session(active_ids, features, prws, ssss):
    return FakeSession(
        active_region_ids=active_ids,
        features=features,
        prws=prws,
        ssss=ssss,
    )


# ---- Tests --------------------------------------------------------------


def test_verify_quality_passes_on_clean_data():
    features = [
        _FeatureRow(region_id=1, dataset_version="v_test"),
        _FeatureRow(region_id=2, dataset_version="v_test"),
    ]
    prws = [
        _PRWRow(
            dataset_version="v_test",
            persona_type="casual_foodie",
            region_id=1,
        ),
        _PRWRow(
            dataset_version="v_test",
            persona_type="casual_foodie",
            region_id=2,
        ),
    ]
    ssss = [
        _SSSRow(
            dataset_version="v_test",
            region_id=1,
            spot_type="casual_meetup",
            category="food",
            final_weight=0.6,
        ),
    ]
    session = _make_session([1, 2], features, prws, ssss)
    issues = publisher_service.verify_quality(session, "v_test", "suwon")
    assert issues == [], f"expected no issues, got: {issues}"


def test_verify_quality_flags_missing_region_feature():
    features = [_FeatureRow(region_id=1, dataset_version="v_test")]
    session = _make_session([1, 2, 3], features, [], [])
    issues = publisher_service.verify_quality(session, "v_test", "suwon")
    assert any("missing" in i for i in issues)


def test_verify_quality_flags_zero_raw_place_count():
    features = [
        _FeatureRow(region_id=1, dataset_version="v_test", raw_place_count=0)
    ]
    session = _make_session([1], features, [], [])
    issues = publisher_service.verify_quality(session, "v_test", "suwon")
    assert any("raw_place_count" in i for i in issues)


def test_verify_quality_flags_nonfinite_persona_weight():
    features = [_FeatureRow(region_id=1, dataset_version="v_test")]
    prws = [
        _PRWRow(
            dataset_version="v_test",
            persona_type="casual_foodie",
            region_id=1,
            affinity_score=float("nan"),
        )
    ]
    session = _make_session([1], features, prws, [])
    issues = publisher_service.verify_quality(session, "v_test", "suwon")
    assert any("NaN" in i or "inf" in i for i in issues)


def test_verify_quality_flags_out_of_range_final_weight():
    features = [_FeatureRow(region_id=1, dataset_version="v_test")]
    ssss = [
        _SSSRow(
            dataset_version="v_test",
            region_id=1,
            spot_type="casual_meetup",
            category="food",
            final_weight=1.5,
        ),
        _SSSRow(
            dataset_version="v_test",
            region_id=1,
            spot_type="casual_meetup",
            category="cafe",
            final_weight=float("inf"),
        ),
    ]
    session = _make_session([1], features, [], ssss)
    issues = publisher_service.verify_quality(session, "v_test", "suwon")
    assert any("final_weight" in i for i in issues)


def test_verify_quality_flags_non_finite_feature_fields():
    bad = _FeatureRow(region_id=1, dataset_version="v_test")
    bad.food_density = float("nan")
    session = _make_session([1], [bad], [], [])
    issues = publisher_service.verify_quality(session, "v_test", "suwon")
    assert any("NaN" in i or "inf" in i for i in issues)


def test_math_guard_sanity():
    assert not math.isfinite(float("nan"))
    assert not math.isfinite(float("inf"))
    assert math.isfinite(0.0)
