"""Automatic style/context metrics for generated feed cards.

These metrics are intentionally lightweight and deterministic so they can be
reported alongside RAGAS without human annotation:

- generic_phrase_rate: lower is less boilerplate/template-like.
- distinct_1 / distinct_2: higher is more lexically diverse.
- title_pattern_repetition: lower means titles repeat fewer skeletons.
- mean_pairwise_similarity: lower means outputs are less mutually similar.
- taste_scene_context_mention_rate: higher means generated cards mention
  taste/scene/context cues from the structured context.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

GENERIC_PHRASES = [
    "모집합니다",
    "모집합니다.",
    "함께하실 분",
    "함께 하실 분",
    "소규모 모임",
    "진행 예정",
    "진행할 예정",
    "편하게 참여",
    "부담 없이 참여",
    "예상 인원",
    "참가비는",
    "참가비는 1인",
    "함께 해보실 분",
    "함께해요",
    "모임입니다",
    "모임으로 진행",
]

STOP_TOKENS = {
    "title",
    "summary",
    "tags",
    "price_label",
    "region_label",
    "time_label",
    "supporter_label",
    "status",
    "recruiting",
}

_FIELD_RE = re.compile(r"(?:^|\n)(title|summary|tags|price_label|region_label|time_label|supporter_label):\s*", re.I)
_KO_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")


def parse_answer_fields(answer: str) -> dict[str, str]:
    """Parse flattened `key: value` answer text into fields."""
    matches = list(_FIELD_RE.finditer(answer))
    if not matches:
        return {"text": answer}
    fields: dict[str, str] = {}
    for idx, match in enumerate(matches):
        key = match.group(1).lower()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(answer)
        fields[key] = answer[start:end].strip()
    return fields


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _KO_TOKEN_RE.findall(text) if t.lower() not in STOP_TOKENS]


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    if len(tokens) < n:
        return []
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def distinct_n(texts: Iterable[str], n: int) -> float:
    all_ngrams: list[tuple[str, ...]] = []
    for text in texts:
        all_ngrams.extend(ngrams(tokenize(text), n))
    if not all_ngrams:
        return 0.0
    return len(set(all_ngrams)) / len(all_ngrams)


def generic_phrase_stats(texts: Iterable[str]) -> dict[str, Any]:
    total = 0
    hits = 0
    by_phrase = Counter()
    for text in texts:
        total += 1
        found = False
        for phrase in GENERIC_PHRASES:
            count = text.count(phrase)
            if count:
                found = True
                by_phrase[phrase] += count
        if found:
            hits += 1
    return {
        "generic_phrase_rate": hits / total if total else 0.0,
        "generic_phrase_hits": hits,
        "generic_phrase_total": total,
        "top_generic_phrases": by_phrase.most_common(10),
    }


def normalize_title_pattern(title: str) -> str:
    """Collapse title into a rough skeleton for repetition measurement."""
    text = title.lower().strip()
    replacements = {
        r"연무동|수원시|수원": "{region}",
        r"스마트폰\s*사진|폰사진|필름\s*사진|카페\s*드로잉|러닝\s*입문|홈베이킹|쿠키|드로잉|러닝|사진": "{skill}",
        r"\d+명|\d+": "{num}",
    }
    for pat, repl in replacements.items():
        text = re.sub(pat, repl, text)
    text = re.sub(r"[가-힣a-z0-9{}]+", lambda m: m.group(0), text)
    text = re.sub(r"\s+", " ", text)
    return text


def title_pattern_stats(titles: list[str]) -> dict[str, Any]:
    patterns = [normalize_title_pattern(t) for t in titles]
    counts = Counter(patterns)
    repeated = sum(c for c in counts.values() if c > 1)
    total = len(patterns)
    return {
        "title_pattern_repetition": repeated / total if total else 0.0,
        "unique_title_pattern_rate": len(counts) / total if total else 0.0,
        "top_title_patterns": counts.most_common(10),
    }


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def mean_pairwise_similarity(texts: list[str]) -> float:
    vecs = [Counter(ngrams(tokenize(t), 2) or [(tok,) for tok in tokenize(t)]) for t in texts]
    if len(vecs) < 2:
        return 0.0
    sims = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            sims.append(cosine(vecs[i], vecs[j]))
    return sum(sims) / len(sims) if sims else 0.0


def _context_keywords(row: Mapping[str, Any]) -> set[str]:
    """Extract taste/scene/context keywords from row contexts."""
    contexts = "\n".join(row.get("contexts") or row.get("retrieved_contexts") or [])
    keywords: set[str] = set()

    # Explicit semicolon-separated values from our context serializer.
    for label in ("taste_facets", "recent_obsession", "curiosity_hooks", "plan_outline"):
        m = re.search(rf"{label}=([^.;\n]+(?:[,>\- ][^.;\n]+)*)", contexts)
        if m:
            keywords.update(tokenize(m.group(1)))

    # POI/scene words and venue names from curated contexts.
    for token in tokenize(contexts):
        if len(token) >= 2 and token not in {
            "context",
            "true",
            "false",
            "region",
            "category",
            "skill",
            "topic",
            "expected",
            "participants",
            "schedule",
            "duration",
            "minutes",
            "price",
            "band",
            "person",
        }:
            if token in {
                "골목",
                "역광",
                "필름",
                "색감",
                "성곽길",
                "창가",
                "스케치",
                "소품",
                "페이스",
                "초보",
                "루트",
                "버터",
                "반죽",
                "공원",
                "카페",
                "스튜디오",
                "연습",
                "후기",
                "결과물",
            } or token.endswith(("카페", "공원", "다방", "스튜디오")):
                keywords.add(token)
    return keywords


def context_mention_stats(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    rates = []
    hit_counts = []
    total_counts = []
    for row in rows:
        answer_tokens = set(tokenize(str(row.get("answer") or row.get("response") or "")))
        keywords = _context_keywords(row)
        if not keywords:
            continue
        hits = len(answer_tokens & keywords)
        rates.append(hits / len(keywords))
        hit_counts.append(hits)
        total_counts.append(len(keywords))
    return {
        "taste_scene_context_mention_rate": sum(rates) / len(rates) if rates else 0.0,
        "context_keyword_hits_mean": sum(hit_counts) / len(hit_counts) if hit_counts else 0.0,
        "context_keyword_total_mean": sum(total_counts) / len(total_counts) if total_counts else 0.0,
    }


def summarize_style_rows(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_system: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_system[str(row["system"])].append(row)

    summary: dict[str, Any] = {}
    for system, sys_rows in by_system.items():
        answers = [str(r.get("answer") or r.get("response") or "") for r in sys_rows]
        fields = [parse_answer_fields(a) for a in answers]
        titles = [f.get("title", "") for f in fields]
        summaries = [f.get("summary", "") for f in fields]
        full_texts = [" ".join([f.get("title", ""), f.get("summary", ""), f.get("tags", "")]) for f in fields]

        summary[system] = {
            "n": len(sys_rows),
            "distinct_1": round(distinct_n(full_texts, 1), 4),
            "distinct_2": round(distinct_n(full_texts, 2), 4),
            "mean_pairwise_similarity": round(mean_pairwise_similarity(full_texts), 4),
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in generic_phrase_stats(full_texts).items()},
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in title_pattern_stats(titles).items()},
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in context_mention_stats(list(sys_rows)).items()},
            "avg_title_tokens": round(sum(len(tokenize(t)) for t in titles) / len(titles), 4) if titles else 0.0,
            "avg_summary_tokens": round(sum(len(tokenize(s)) for s in summaries) / len(summaries), 4) if summaries else 0.0,
        }
    return summary


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_style_report(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    rows = read_jsonl(input_path)
    report = {
        "input": str(input_path),
        "summary": summarize_style_rows(rows),
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
