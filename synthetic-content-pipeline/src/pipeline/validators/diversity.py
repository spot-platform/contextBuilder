"""Layer 5 — Diversity Check (반복 패턴 탐지).

synthetic_content_pipeline_plan.md §5 Layer 5 표.

3 가지 시그널을 measure 한 뒤 최대값을 기반으로 diversity_score 산정:

1. n-gram 3-gram Jaccard overlap (Counter 기반, 외부 의존성 없음)
2. TF-IDF cosine similarity — sklearn 시도, 없으면 pure-python token frequency cosine
3. 템플릿 regex 패턴 매치율 (config/rules/diversity_patterns.yaml)

공식:
    diversity_score = 1.0 - max(ngram_overlap, tfidf_sim, template_repeat_rate)

배치 내 후보들(및 approved_cache)에 대해 pair-wise 로 측정하고, 한 후보당 가장
유사한 이웃을 기준으로 점수를 매긴다.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

log = logging.getLogger(__name__)

__all__ = [
    "compute_diversity",
    "extract_text",
    "load_diversity_patterns",
]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATTERNS_PATH = _REPO_ROOT / "config" / "rules" / "diversity_patterns.yaml"

# ---------------------------------------------------------------------------
# sklearn 시도 / 폴백
# ---------------------------------------------------------------------------

try:  # pragma: no cover — 환경에 따라 분기
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore

    def _tfidf_cosine(texts: Sequence[str]) -> List[List[float]]:
        """sklearn 기반 TF-IDF cosine 매트릭스. texts 길이 N → N×N."""
        if len(texts) < 2:
            return [[1.0]] if texts else [[]]
        try:
            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))
            matrix = vec.fit_transform(texts)
            # cosine = normalized dot product (TfidfVectorizer 는 이미 L2 정규화 가능).
            normed = matrix.toarray()
            sims: List[List[float]] = []
            for i in range(len(texts)):
                row = []
                for j in range(len(texts)):
                    a = normed[i]
                    b = normed[j]
                    denom = (math.sqrt((a * a).sum()) * math.sqrt((b * b).sum())) or 1.0
                    row.append(float((a * b).sum() / denom))
                sims.append(row)
            return sims
        except Exception as exc:  # noqa: BLE001
            log.warning("sklearn TF-IDF failed (%s) — pure python fallback", exc)
            return _tfidf_cosine_pure(texts)

    _TFIDF_BACKEND = "sklearn"
except ImportError:  # pragma: no cover
    _TFIDF_BACKEND = "pure_python"

    def _tfidf_cosine(texts: Sequence[str]) -> List[List[float]]:
        return _tfidf_cosine_pure(texts)


def _tfidf_cosine_pure(texts: Sequence[str]) -> List[List[float]]:
    """pure-python token-frequency cosine (sklearn 대체).

    토큰화: 한국어는 공백 + 문자 bigram 조합. 간단히 char bigram 이용.
    """
    if not texts:
        return [[]]

    def _tokens(text: str) -> Counter:
        clean = re.sub(r"\s+", " ", text).strip().lower()
        if len(clean) < 2:
            return Counter([clean]) if clean else Counter()
        return Counter(clean[i : i + 2] for i in range(len(clean) - 1))

    # DF 집계
    doc_tokens = [_tokens(t) for t in texts]
    df: Dict[str, int] = {}
    for toks in doc_tokens:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    n_docs = len(texts)

    def _tfidf_vec(toks: Counter) -> Dict[str, float]:
        total = sum(toks.values()) or 1
        out: Dict[str, float] = {}
        for t, c in toks.items():
            tf = c / total
            idf = math.log((1 + n_docs) / (1 + df.get(t, 0))) + 1.0
            out[t] = tf * idf
        return out

    vecs = [_tfidf_vec(t) for t in doc_tokens]

    def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a.keys()) & set(b.keys())
        num = sum(a[k] * b[k] for k in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return num / (na * nb)

    sims: List[List[float]] = []
    for i in range(n_docs):
        row = []
        for j in range(n_docs):
            row.append(1.0 if i == j else _cosine(vecs[i], vecs[j]))
        sims.append(row)
    return sims


# ---------------------------------------------------------------------------
# 텍스트 추출
# ---------------------------------------------------------------------------


def extract_text(payload: Mapping[str, Any], content_type: str) -> str:
    """content_type 별 비교용 텍스트 추출.

    - feed: title + summary
    - detail: title + description
    - plan: join(activities)
    - messages: 4 snippet concat
    - review: review_text (+ rating tag 포함 안 함)
    """
    if payload is None:
        return ""
    ct = (content_type or "").lower()
    if ct == "feed":
        return " ".join(str(payload.get(k, "")) for k in ("title", "summary")).strip()
    if ct == "detail":
        return " ".join(
            str(payload.get(k, "")) for k in ("title", "description")
        ).strip()
    if ct == "plan":
        steps = payload.get("steps") or []
        parts = []
        for s in steps:
            if isinstance(s, Mapping):
                parts.append(str(s.get("activity", "")))
            else:
                parts.append(str(s))
        return " ".join(parts).strip()
    if ct == "messages":
        return " ".join(
            str(payload.get(k, ""))
            for k in (
                "recruiting_intro",
                "join_approval",
                "day_of_notice",
                "post_thanks",
            )
        ).strip()
    if ct == "review":
        return str(payload.get("review_text", "")).strip()
    # fallback: 모든 string 값 concat
    return " ".join(
        str(v) for v in payload.values() if isinstance(v, (str, int, float))
    ).strip()


# ---------------------------------------------------------------------------
# n-gram overlap
# ---------------------------------------------------------------------------


def _char_ngrams(text: str, n: int = 3) -> Counter:
    clean = re.sub(r"\s+", "", text)
    if len(clean) < n:
        return Counter()
    return Counter(clean[i : i + n] for i in range(len(clean) - n + 1))


def _jaccard(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    ka, kb = set(a.keys()), set(b.keys())
    inter = ka & kb
    union = ka | kb
    if not union:
        return 0.0
    return len(inter) / len(union)


# ---------------------------------------------------------------------------
# 템플릿 패턴
# ---------------------------------------------------------------------------


def load_diversity_patterns(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """diversity_patterns.yaml 로드."""
    p = path or _DEFAULT_PATTERNS_PATH
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return list(data.get("patterns") or [])


def _template_repeat_rate(
    text: str, patterns: Sequence[Mapping[str, Any]]
) -> float:
    """패턴 매치 횟수 / max(1, 문장수) → 0.0~1.0 범위로 clip."""
    if not text or not patterns:
        return 0.0
    total_matches = 0
    for p in patterns:
        regex = p.get("regex")
        if not regex:
            continue
        try:
            total_matches += len(re.findall(regex, text))
        except re.error:
            continue
    # 간단한 정규화: 문장 수 기준 (., ?, !, 줄바꿈).
    sentences = max(1, len(re.findall(r"[.!?\n]", text)) + 1)
    rate = total_matches / sentences
    return min(1.0, max(0.0, rate))


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def _candidate_id(c: Any, fallback_idx: int) -> str:
    """후보 객체의 식별자 추출. Candidate.meta['seed_hash'] 우선, 없으면 variant + idx."""
    if hasattr(c, "meta") and isinstance(getattr(c, "meta", None), Mapping):
        seed = c.meta.get("seed_hash")
        if seed:
            return str(seed)
    if hasattr(c, "variant"):
        return f"{getattr(c, 'variant', 'x')}_{fallback_idx}"
    return f"cand_{fallback_idx}"


def compute_diversity(
    candidates: Sequence[Any],
    content_type: str,
    approved_cache: Sequence[Any] = (),
    *,
    patterns: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, float]:
    """후보 각각에 대해 diversity_score 산정.

    Returns
    -------
    Dict[candidate_id, diversity_score]
        ``diversity_score = 1.0 - max(ngram_overlap, tfidf_sim, template_repeat_rate)``
        (0.0 ~ 1.0).
    """
    if not candidates:
        return {}

    if patterns is None:
        patterns = load_diversity_patterns()

    # 텍스트 추출
    cand_texts: List[str] = []
    cand_ids: List[str] = []
    for i, c in enumerate(candidates):
        payload = getattr(c, "payload", None)
        if payload is None and isinstance(c, Mapping):
            payload = c  # 편의: dict 도 받아준다
        cand_texts.append(extract_text(payload or {}, content_type))
        cand_ids.append(_candidate_id(c, i))

    cache_texts: List[str] = []
    for i, c in enumerate(approved_cache or ()):
        payload = getattr(c, "payload", None)
        if payload is None and isinstance(c, Mapping):
            payload = c
        cache_texts.append(extract_text(payload or {}, content_type))

    all_texts = cand_texts + cache_texts

    # n-gram counters
    ngram_counters = [_char_ngrams(t, 3) for t in all_texts]

    # TF-IDF matrix
    tfidf_sims = _tfidf_cosine(all_texts) if len(all_texts) >= 2 else None

    result: Dict[str, float] = {}
    n_cand = len(cand_texts)
    for i in range(n_cand):
        max_ngram = 0.0
        max_tfidf = 0.0
        for j in range(len(all_texts)):
            if i == j:
                continue
            ng = _jaccard(ngram_counters[i], ngram_counters[j])
            if ng > max_ngram:
                max_ngram = ng
            if tfidf_sims is not None:
                tf = tfidf_sims[i][j]
                if tf > max_tfidf:
                    max_tfidf = tf
        template_rate = _template_repeat_rate(cand_texts[i], patterns)
        worst = max(max_ngram, max_tfidf, template_rate)
        score = 1.0 - worst
        if score < 0.0:
            score = 0.0
        if score > 1.0:
            score = 1.0
        result[cand_ids[i]] = score
    return result
