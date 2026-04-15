"""test_validators_schema — Layer 1 schema + 내장 규칙."""
from __future__ import annotations

import copy
import json

import pytest

from pipeline.validators.schema import validate_feed_schema


@pytest.fixture(scope="session")
def stub_default_payload(repo_root):
    path = repo_root / "tests" / "fixtures" / "codex_stub" / "feed" / "v1" / "default.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def test_positive_stub_default_passes(stub_default_payload, feed_schema_path):
    """stub default.json 원본 → ok=True."""
    result = validate_feed_schema(stub_default_payload, feed_schema_path)
    assert result.ok, f"rejections: {[r.reason for r in result.rejections]}"
    assert result.meta.get("used_schema") is True


def _mutate(payload, **changes):
    out = copy.deepcopy(payload)
    for k, v in changes.items():
        if v is ...:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def test_title_too_short(stub_default_payload, feed_schema_path):
    p = _mutate(stub_default_payload, title="짧음")
    result = validate_feed_schema(p, feed_schema_path)
    assert not result.ok
    assert any("title_too_short" == r.reason or "schema_violation" == r.reason for r in result.rejections)
    assert any(r.rejected_field == "title" or r.rejected_field.startswith("title") for r in result.rejections)


def test_title_too_long(stub_default_payload, feed_schema_path):
    p = _mutate(stub_default_payload, title="가" * 50)
    result = validate_feed_schema(p, feed_schema_path)
    assert not result.ok
    assert any("title_too_long" == r.reason or "schema_violation" == r.reason for r in result.rejections)


def test_tags_only_one_item(stub_default_payload, feed_schema_path):
    p = _mutate(stub_default_payload, tags=["하나만"])
    result = validate_feed_schema(p, feed_schema_path)
    assert not result.ok
    # jsonschema 가 minItems 위반 감지
    assert any("tags" in r.rejected_field for r in result.rejections)


def test_status_enum_violation(stub_default_payload, feed_schema_path):
    p = _mutate(stub_default_payload, status="pending_review")
    result = validate_feed_schema(p, feed_schema_path)
    assert not result.ok
    assert any(r.rejected_field == "status" for r in result.rejections)


def test_price_label_empty_string(stub_default_payload, feed_schema_path):
    p = _mutate(stub_default_payload, price_label="")
    result = validate_feed_schema(p, feed_schema_path)
    assert not result.ok
    assert any(r.rejected_field == "price_label" for r in result.rejections)


def test_supporter_label_null(stub_default_payload, feed_schema_path):
    p = _mutate(stub_default_payload, supporter_label=None)
    result = validate_feed_schema(p, feed_schema_path)
    assert not result.ok
    assert any(r.rejected_field == "supporter_label" for r in result.rejections)
