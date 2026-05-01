from __future__ import annotations

import json
from pathlib import Path

from pipeline.evaluation.ragas_feed import (
    feed_payload_to_answer,
    make_ragas_record,
    read_jsonl,
    spec_to_contexts,
    write_jsonl,
)
from pipeline.llm.prompt_loader import render
from pipeline.spec.models import (
    ActivityConstraints,
    Budget,
    ContentSpec,
    HostPersona,
    Participants,
    Schedule,
)


def _minimal_spec() -> ContentSpec:
    return ContentSpec(
        spot_id="T001",
        region="연무동",
        category="스마트폰 사진",
        host_persona=HostPersona(
            type="supporter_neutral",
            tone="편안한",
            communication_style="구체적으로 안내",
        ),
        participants=Participants(expected_count=4, persona_mix=[]),
        schedule=Schedule(date="2026-05-23", start_time="14:00", duration_minutes=180),
        budget=Budget(price_band=2, expected_cost_per_person=15000),
        activity_constraints=ActivityConstraints(indoor=False, beginner_friendly=True, supporter_required=True),
        plan_outline=["집결", "촬영", "사진 공유"],
        skill_topic="스마트폰 사진",
        venue_type="park",
        teach_mode="small_group",
        taste_facets=["골목 사진"],
        recent_obsession="필름 카메라로 동네 다시 보기",
        curiosity_hooks=["수동 노출"],
    )


def test_feed_payload_to_answer_flattens_schema_fields() -> None:
    answer = feed_payload_to_answer(
        {
            "title": "연무동 골목 사진 산책",
            "summary": "가볍게 같이 찍어봐요.",
            "tags": ["연무동", "사진"],
            "price_label": "1인 약 1.5만원",
            "region_label": "수원시 연무동",
            "time_label": "5/23(토) 14:00",
            "status": "recruiting",
        }
    )

    assert "title: 연무동 골목 사진 산책" in answer
    assert "tags: 연무동, 사진" in answer
    assert "status" not in answer


def test_make_ragas_record_has_question_answer_contexts() -> None:
    spec = _minimal_spec()
    record = make_ragas_record(
        case_id="001_T001",
        system="context_builder",
        spec=spec,
        payload={"title": "연무동 사진 모임", "summary": "같이 찍어봐요."},
    )

    assert record["case_id"] == "001_T001"
    assert record["system"] == "context_builder"
    assert "연무동" in record["question"]
    assert "title: 연무동 사진 모임" in record["answer"]
    assert len(record["contexts"]) >= 3
    assert any("simulation_constraints" in ctx for ctx in record["contexts"])


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    spec = _minimal_spec()
    row = make_ragas_record(
        case_id="001_T001",
        system="direct_llm",
        spec=spec,
        payload={"title": "사진 모임", "summary": "수원에서 만나요."},
    )
    out = tmp_path / "feed_eval.jsonl"
    write_jsonl(out, [row])

    loaded = read_jsonl(out)
    assert loaded == [json.loads(out.read_text(encoding="utf-8"))]


def test_spec_to_contexts_contains_contextbuilder_artifacts() -> None:
    contexts = spec_to_contexts(_minimal_spec())

    joined = "\n".join(contexts)
    assert "local_and_activity_attributes" in joined
    assert "host_persona_and_taste" in joined
    assert "골목 사진" in joined


def test_direct_feed_prompt_is_long_form_baseline() -> None:
    prompt = render(
        "direct_feed:v1",
        {
            "region": "수원시",
            "case_index": 7,
            "batch_size": 100,
        },
    )

    assert "수원시에 올라갈 로컬 취미/모임 피드 100개" in prompt
    assert "7번째 피드" in prompt
    assert "충분히 구체적이고 긴 피드" in prompt
    assert "summary는 220~360자" in prompt
    assert "tags는" in prompt and "4~6개" in prompt
    assert "지역 범위와 출력 구조 외의 세부 context는 받지 못했다" in prompt
    assert "스마트폰 사진" not in prompt
    assert "2026-05-23" not in prompt
    assert "15000" not in prompt
