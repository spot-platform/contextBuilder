from __future__ import annotations

from pipeline.evaluation.style_metrics import (
    generic_phrase_stats,
    mean_pairwise_similarity,
    parse_answer_fields,
    summarize_style_rows,
    title_pattern_stats,
)


def test_parse_answer_fields() -> None:
    fields = parse_answer_fields("title: 연무동 사진 모임\nsummary: 같이 찍어요\ntags: 사진, 연무동")
    assert fields["title"] == "연무동 사진 모임"
    assert fields["summary"] == "같이 찍어요"
    assert fields["tags"] == "사진, 연무동"


def test_generic_phrase_rate_detects_boilerplate() -> None:
    stats = generic_phrase_stats([
        "연무동에서 함께하실 분 모집합니다",
        "골목을 천천히 찍어보실래요",
    ])
    assert stats["generic_phrase_rate"] == 0.5


def test_title_pattern_repetition_detects_templates() -> None:
    stats = title_pattern_stats([
        "연무동 스마트폰 사진 모임",
        "연무동 필름 사진 모임",
        "연무동 골목에서 폰사진 가볍게",
    ])
    assert stats["title_pattern_repetition"] > 0


def test_pairwise_similarity_higher_for_similar_texts() -> None:
    similar = mean_pairwise_similarity([
        "연무동 사진 모임 참가자를 모집합니다",
        "연무동 필름 사진 모임 참가자를 모집합니다",
    ])
    diverse = mean_pairwise_similarity([
        "연무동 사진 모임 참가자를 모집합니다",
        "버터 향 나는 쿠키 반죽을 천천히 같이 만져봐요",
    ])
    assert similar > diverse


def test_summarize_style_rows_by_system() -> None:
    rows = [
        {
            "system": "context_builder",
            "answer": "title: 골목에서 사진 찍어볼까요\nsummary: 역광 피하면서 동네 골목을 찍어요",
            "contexts": ["host_persona_and_taste: taste_facets=골목 사진, 역광 피하기; recent_obsession=동네 골목"],
        },
        {
            "system": "direct_llm",
            "answer": "title: 연무동 사진 모임\nsummary: 함께하실 분 모집합니다",
            "contexts": ["host_persona_and_taste: taste_facets=골목 사진, 역광 피하기; recent_obsession=동네 골목"],
        },
    ]
    summary = summarize_style_rows(rows)
    assert summary["context_builder"]["taste_scene_context_mention_rate"] > 0
    assert summary["direct_llm"]["generic_phrase_rate"] == 1.0
