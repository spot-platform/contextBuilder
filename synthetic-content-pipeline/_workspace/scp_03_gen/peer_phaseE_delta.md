# Phase Peer-E Delta — content-generator-engineer

> **완료 마크**: `scp_03_gen_peer_phaseE_complete`
>
> 6 프롬프트 v2 재작성 + persona_tones 또래 존댓말 재작성 + base.py peer 변수 확장 +
> critic schema/scoring 재조정. 기존 Phase 1~4 + Phase Peer-D 153 passed 회귀 0 유지.

---

## 0. 핵심 원칙 — 톤 매트릭스 hard rule

사용자 명시 요구: "**또래 강사 = 반말" 이 아니다.** 또래여도 공개 피드에서는 존댓말이
기본이다. 6 프롬프트 전부 상단 hard rule 블록에 다음 매트릭스를 주입했다.

| Content type | 톤 | 이유 |
|-------------|----|----|
| `feed` | **존댓말 필수**, 친근한 또래 존대 | 공개 게시물 |
| `detail` | **존댓말 필수** | 상세 페이지, 공식 |
| `plan` | **존댓말 필수** | 타임라인 |
| `messages.recruiting_intro` | **존댓말** | 첫 만남 |
| `messages.join_approval` | **존댓말** | 확정 안내 |
| `messages.day_of_notice` | **친근한 존댓말** | 이미 확정된 사이, 살짝 편하게 |
| `messages.post_thanks` | **친근한 존댓말 ~ 반존대 허용** | 이미 만난 사이 |
| `review` | **존댓말 중심, 개인 소감 톤** (일부 반말 자연스러움) | 개인 후기 |

금기 어휘 (모든 파트):
- 프로 강사 톤: 강좌 / 강사 / 수강생 / 수강료 / 강의료 / 자격증 / 원데이 클래스 / 정규 수업 / 개설하여
- 반말 / SNS 톤: "ㅇㄱㄹㅇ", "~할래?" (review 제외한 모든 파트에서 금지)

---

## 1. 변경 파일 (생성 / 수정)

### 1-A. 프롬프트 v2 (6개 신규, v1 병행 유지 — append-only)

| 파일 | 유형 | 톤 매트릭스 주입 |
|---|---|---|
| `config/prompts/feed/v2.j2` | 신규 | feed = 존댓말 필수, 반말 금지 |
| `config/prompts/detail/v2.j2` | 신규 | detail = 존댓말 필수, 과격식 금지 |
| `config/prompts/plan/v2.j2` | 신규 | plan = 존댓말 활동 표현 |
| `config/prompts/messages/v2.j2` | 신규 | 4 snippet 톤 매트릭스 매트릭스 그대로 주입 (recruiting_intro/join_approval 존댓말, day_of_notice 친근 존댓말, post_thanks 따뜻한 반존대 허용) |
| `config/prompts/review/v2.j2` | 신규 | review = 개인 소감 톤, 존댓말 중심 + 일부 반말 OK |
| `config/prompts/critic/v2.j2` | 신규 | critic = 6 평가 항목 + peer_tone_score 감점 룰 |

v1 파일은 삭제하지 않고 그대로 둔다. `get_latest_version()` 이 자동으로 v2 를
선택한다. legacy 테스트 경로에서 v1 을 명시 로드하는 코드 (`test_critic_unit.py`
가 v1 fixture 를 직접 path 로 연다) 는 그대로 동작.

### 1-B. Generator template_id 전환

| 파일 | Before | After |
|---|---|---|
| `src/pipeline/generators/feed.py` | `feed:v1` / `feed/v1.j2` | `feed:v2` / `feed/v2.j2` |
| `src/pipeline/generators/detail.py` | `detail:v1` / `detail/v1.j2` | `detail:v2` / `detail/v2.j2` |
| `src/pipeline/generators/plan.py` | `plan:v1` / `plan/v1.j2` | `plan:v2` / `plan/v2.j2` |
| `src/pipeline/generators/messages.py` | `messages:v1` / `messages/v1.j2` | `messages:v2` / `messages/v2.j2` |
| `src/pipeline/generators/review.py` | `review:v1` / `review/v1.j2` | `review:v2` / `review/v2.j2` |
| `src/pipeline/validators/critic.py` | `_CRITIC_TEMPLATE_ID = "critic:v1"` | `_CRITIC_TEMPLATE_ID = "critic:v2"` |

### 1-C. 기반 코드 (수정)

| 파일 | 수정 내용 |
|---|---|
| `src/pipeline/generators/base.py` | `COMMON_VARIABLE_KEYS` 16 → **37** (peer 21 신규 append). `spec_to_variables` 가 ContentSpec peer 필드 21개를 variables dict 로 매핑. sanity assert 여전히 통과. |
| `src/pipeline/generators/persona_tones.py` | 전면 재작성. MVP 5 persona (night_social/weekend_explorer/planner/spontaneous/homebody) 각 4 예문 + legacy supporter_* 3 persona (Phase 1 골든 호환 유지) + default. **모든 예문 또래 존댓말**. |
| `src/pipeline/llm/schemas/critic.json` | `peer_tone_score` 추가 (properties + required 동시 — OpenAI strict mode 준수). 총 8 required. |
| `src/pipeline/validators/critic.py` | `CriticResult.peer_tone_score` 필드 신규 (default 0.85). `evaluate_critic` 가 response 파싱 시 peer_tone_score 도 `_clip01` 통과시켜 저장. `deterministic_default` / `to_dict` 동기화. |
| `src/pipeline/validators/scoring.py` | `SCORING_WEIGHTS` 에 `peer_tone_fit: 0.15` 추가. persona_fit 0.20→0.15, region_fit 0.15→0.10, business_rule_fit 0.10→0.05 로 재조정. 합계 1.00 유지. `compute_quality_score` 가 `critic.peer_tone_score` (없으면 0.85) 를 반영. breakdown components/weighted 도 7 키. |
| `src/pipeline/validators/rules.py` | `DEFAULT_FORBIDDEN_PRO_KEYWORDS` 12개 상수 + `rule_no_pro_keywords` 신규 rule 함수 + `_FEED_RULE_FUNCTIONS` 튜플 append. |
| `config/rules/shared_rules.yaml` | `forbidden_pro_keywords` 12개 리스트 추가. |
| `config/weights/scoring_weights.json` | 새 가중치 dict 로 재작성. |

### 1-D. Stub fixture (Phase D 경계면 버그 방지)

Phase D 메모: `COMMON_VARIABLE_KEYS` 확장 후 sanity assert 가 깨지지 않도록
stub fixture 도 함께 확장해야 한다. generator template_id 가 v2 로 바뀌면
`codex_client._load_stub_response` 가 `codex_stub/{type}/v2/` 에서 fixture 를
찾으므로 v2 폴더를 신규 생성했다. v1 fixture 는 그대로 유지 (legacy 경로 소유).

| 신규 파일 | 용도 |
|---|---|
| `tests/fixtures/codex_stub/feed/v2/default.json` | feed:v2 stub fallback |
| `tests/fixtures/codex_stub/detail/v2/default.json` | detail:v2 stub fallback |
| `tests/fixtures/codex_stub/plan/v2/default.json` | plan:v2 stub fallback |
| `tests/fixtures/codex_stub/messages/v2/default.json` | messages:v2 stub fallback |
| `tests/fixtures/codex_stub/review/v2/default.json` | review:v2 stub fallback |
| `tests/fixtures/codex_stub/critic/v2/default.json` | critic:v2 stub fallback (peer_tone_score 0.92) |
| `tests/fixtures/codex_stub/critic/v2/critic_reject_sample.json` | critic:v2 reject 샘플 (peer_tone_score 0.48) |

**v1 fixture 업데이트** (schema 변경으로 강제):
- `tests/fixtures/codex_stub/critic/v1/default.json` — peer_tone_score 0.92 추가
- `tests/fixtures/codex_stub/critic/v1/critic_reject_sample.json` — peer_tone_score 0.48 추가

이유: critic.json schema 에 peer_tone_score 가 required 로 추가되었고,
`test_critic_unit.py::TestCriticSchema` 는 v1 fixture 를 jsonschema.validate 로
검증한다. fixture 에 새 키가 없으면 fail.

### 1-E. 테스트 유지보수 (2건)

`tests/test_scoring_unit.py` 두 테스트가 SCORING_WEIGHTS key set 을 6개로
하드코딩. Phase E 가중치 재조정으로 7개 (peer_tone_fit 추가) 로 업데이트.

| 테스트 | 변경 |
|---|---|
| `test_scoring_weights_keys_match_plan` | expected set 6 → 7 (peer_tone_fit 추가) |
| `test_breakdown_has_required_keys` | expected_keys 6 → 7 |

### 1-F. 델타 문서 (신규)

- `_workspace/scp_03_gen/peer_phaseE_delta.md` (이 문서)

---

## 2. COMMON_VARIABLE_KEYS 확장 목록

Phase Peer-D `peer_phaseD_delta.md §5` 의 21 신규 키를 그대로 append:

```
# 기존 16
spot_id, region_label, category, host_persona,
participants_expected_count, schedule_date, schedule_time,
schedule_day_type, schedule_time_slot, budget_price_band,
budget_cost_per_person, activity_constraints, plan_outline,
activity_result, desired_length_bucket, sample_variant,

# Phase Peer-E 신규 21
skill_topic, host_skill_level, teach_mode, venue_type, fee_breakdown,
origination_mode, originating_voice, is_request_matched,
originating_request_summary, responded_at_tick,
had_renegotiation, renegotiation_history, original_target_partner_count,
final_partner_count,
bonded_partner_count, bond_updates_at_settlement, friend_upgrades,
referrals_triggered, host_reputation_before, host_reputation_after,
host_earn_from_this_spot, peer_tone_required,
```

총 37 키. `BaseGenerator.spec_to_variables` 가 전부 채운다. `fee_breakdown` 은
`FeeBreakdownSpec` 을 dict (peer_labor_fee / material_cost / venue_rental /
equipment_rental / total / passthrough_total) 로 평탄화 — 프롬프트에서 바로
`{{ fee_breakdown.passthrough_total }}` 참조 가능.

---

## 3. critic.json schema 변경

Before (7 required):
```
naturalness_score, consistency_score, regional_fit_score,
persona_fit_score, safety_score, reject, reasons
```

After (8 required, OpenAI strict mode 준수):
```
naturalness_score, consistency_score, regional_fit_score,
persona_fit_score, safety_score, peer_tone_score, reject, reasons
```

`peer_tone_score`:
- type: number
- range: 0.0 ~ 1.0
- required: ✅ (strict mode — properties 와 required 가 100% 일치)

검증 결과 (Draft7Validator):
```
strict_ok = True    props - required = set()
critic/v1/default.json            errs = 0
critic/v1/critic_reject_sample.json  errs = 0
critic/v2/default.json            errs = 0
critic/v2/critic_reject_sample.json  errs = 0
```

---

## 4. scoring 가중치 재조정

| 항목 | Before (Phase 3) | After (Phase Peer-E) | 소스 |
|---|---|---|---|
| naturalness | 0.25 | 0.25 | critic.naturalness_score |
| consistency | 0.20 | 0.20 | critic.consistency_score |
| persona_fit | 0.20 | **0.15** | critic.persona_fit_score |
| region_fit | 0.15 | **0.10** | critic.regional_fit_score |
| business_rule_fit | 0.10 | **0.05** | layer123 warnings |
| diversity | 0.10 | 0.10 | Layer 5 diversity_score |
| **peer_tone_fit** | — | **0.15** (신규) | `critic.peer_tone_score` (없으면 0.85) |
| **합계** | 1.00 | **1.00** | — |

승인 임계값은 그대로 (0.80 approved / 0.65 conditional).

---

## 5. 프롬프트 v2 본문에 주입된 것 요약 (한 줄씩)

| 파일 | 톤 매트릭스 주입 요약 |
|---|---|
| `feed/v2.j2` | "feed 는 존댓말 필수, 반말 금지" + "수업/강좌/수강생/수강료/자격증/원데이 클래스 어휘 금지" + fee_breakdown.peer_labor_fee==0 분기에 "실비만 운영" 톤 허용 |
| `detail/v2.j2` | "detail 는 존댓말 필수, 과격식 금지" + host_intro 에 "저는 OO N년째 하고 있는 또래 호스트예요" 강제 + cost_breakdown 에 fee_breakdown 내역 1:1 복제 권장 |
| `plan/v2.j2` | "plan 는 존댓말 필수" + step.activity 를 "간단한 인사 나누기 / 본 활동 진행 / 마무리 후기 공유" 같은 짧은 존댓말 표현으로 강제 |
| `messages/v2.j2` | 4 snippet 각각 톤 주입: recruiting_intro/join_approval 존댓말, day_of_notice 친근 존댓말 (물결/가벼운 이모지 허용), post_thanks 따뜻한 존댓말 (반존대 일부 OK) |
| `review/v2.j2` | "review 는 개인 소감 톤" + is_request_matched 분기에 "제가 요청 올렸는데 호스트분이 답해주셔서" learner voice, bonded_partner_count 에 "이번이 N번째 참여인데" 허용, had_renegotiation 에 인원 변동 언급 허용 |
| `critic/v2.j2` | 6번째 평가 항목 `peer_tone_score` + 감점 룰 (feed/detail/plan 반말 → 크게 감점, 프로 어휘 매 단어 -0.1, post_thanks 지나친 격식 감점, review 프로 톤 감점) + reject 조건 `(peer_tone_score<0.55 AND naturalness_score<0.65)` 추가 |

---

## 6. 검증 결과 (총 5개 게이트)

### 6-1. Jinja2 v2 StrictUndefined 컴파일

```
feed/v2.j2 ok
detail/v2.j2 ok
plan/v2.j2 ok
messages/v2.j2 ok
review/v2.j2 ok
critic/v2.j2 ok
```

### 6-2. critic.json strict mode

```
strict_ok = True   props - required = set()
critic/v1/default.json             errs = 0
critic/v1/critic_reject_sample.json  errs = 0
critic/v2/default.json             errs = 0
critic/v2/critic_reject_sample.json  errs = 0
```

### 6-3. 5 generator stub smoke (peer spec S_0001)

```
spec.skill_topic = 핸드드립   spec.origination_mode = offer
FeedGenerator         -> primary alternative  template_id: feed:v2
SpotDetailGenerator   -> primary alternative  template_id: detail:v2
SpotPlanGenerator     -> primary alternative  template_id: plan:v2
MessagesGenerator     -> primary alternative  template_id: messages:v2
ReviewGenerator       -> primary alternative  template_id: review:v2
```

stub lookup 이 v2 로 정확히 라우팅. default.json fallback 로그 정상 출력.
(spot_id + variant 해시로 fixture 를 조회하고 매칭 없으면 default.json 사용)

### 6-4. Live codex 호출 — feed:v2 1건 (S_0002 request_matched)

`SCP_LLM_MODE=live SCP_LLM_CACHE=off` 로 실 codex exec 1회:

target = S_0002 / 영어 프리토킹 / request_matched / learner voice

```json
{
  "title": "수원시 신촌동 영어 프리토킹 함께해요",
  "summary": "영어 프리토킹 배우고 싶다는 요청을 보고, 저도 함께할 수 있을 것 같아서 열어봤어요. 카페에서 소규모로 가볍게 이야기 나누실 분이면 부담 없이 오셔도 됩니다.",
  "tags": [
    "수원시 신촌동",
    "영어 프리토킹",
    "카페",
    "새벽",
    "초면환영"
  ],
  "price_label": "1인 약 1,890원 (재료/대관비 포함)",
  "region_label": "수원시 신촌동",
  "time_label": "4/18(토) 01:00",
  "status": "recruiting",
  "supporter_label": "supporter_neutral"
}
```

육안 체크:
- ✅ title / summary 모두 존댓말 + 또래 톤
- ✅ "저도 함께할 수 있을 것 같아서" → request_matched voice (학습자 주도) 정확히 반영
- ✅ 프로 강사 어휘 없음 (강좌/수강생/자격증)
- ✅ "수업료" 대신 "1인 약 1,890원" 참가비 표기
- ✅ 지역 (수원시 신촌동), 스킬 (영어 프리토킹), 시간 (4/18 01:00) 전부 spec 그대로
- ✅ fee_breakdown 의 venue_rental=1890 반영 → "(재료/대관비 포함)" 병기

### 6-5. 전체 회귀 (pytest 153 passed 유지)

```
$ python3 -m pytest tests/ -m "not live_codex" -q
153 passed, 6 deselected, 5 xfailed, 67 warnings in 1.39s
```

- Phase Peer-D 기준 153 passed 완벽 유지
- xfailed 5건은 Phase 1 경계면 golden region_mismatch 로 기존부터 expected-fail
- 수정된 2개 테스트 (`test_scoring_weights_keys_match_plan`, `test_breakdown_has_required_keys`) 는 peer_tone_fit 추가 반영 후 PASS

---

## 7. 업스트림 / 다운스트림 영향

### 업스트림 (Phase D 인계사항 반영)

- ✅ COMMON_VARIABLE_KEYS 확장 + stub fixture 동시 업데이트 (경계면 버그 방지 메모 해결)
- ✅ ContentSpec 21 peer 필드 전부 `spec_to_variables` 에서 매핑
- ✅ category 가 한국어 skill_topic 으로 들어오는 경우 프롬프트가 `skill_topic` 우선 / category fallback 구조로 대응
- ✅ fee_breakdown.peer_labor_fee == 0 케이스를 프롬프트 `{% if %}` 로 분기 — "실비만으로 운영" 톤 허용

### 다운스트림

- **validator-engineer**: `rule_no_pro_keywords` 신규 rule 을 `_FEED_RULE_FUNCTIONS` 에 편입. `rules/shared_rules.yaml.forbidden_pro_keywords` 리스트를 validator-engineer 가 오너로 유지. feed 만 아니라 detail/messages/plan/review 의 rule 모듈에서도 동일 함수를 재사용하려면 validator-engineer 가 import 해서 wiring 추가 필요 (Phase E 범위 밖).
- **codex-bridge-engineer**: critic.json schema 에 peer_tone_score 추가됨. bridge 의 `--output-schema` 가 자동으로 새 필드를 강제한다 (schema 파일 1개 소유권 단일).
- **pipeline-qa**: scoring 가중치가 재조정되어 기존 goldens 의 quality_score 값이 변동한다 (peer_tone_fit 0.15 기여). Phase F 에서 재측정 필요.

---

## 8. 완료 마크

- [x] 6 프롬프트 v2 신규 생성 (feed/detail/plan/messages/review/critic)
- [x] 상단 hard rule 블록에 제품 DNA + 톤 매트릭스 주입
- [x] 프로 강사 금기 어휘 12종 정의 (shared_rules.yaml + DEFAULT_FORBIDDEN_PRO_KEYWORDS)
- [x] `rule_no_pro_keywords` 신규 rule + `_FEED_RULE_FUNCTIONS` 편입
- [x] `COMMON_VARIABLE_KEYS` 16 → 37 (peer 21 append)
- [x] `spec_to_variables` 가 ContentSpec peer 필드 21개 variables 매핑
- [x] `persona_tones.py` 또래 존댓말 재작성 (5 peer + 3 legacy + default)
- [x] `critic.json` schema 에 peer_tone_score (required, 0~1) 추가 — strict mode 준수
- [x] `CriticResult.peer_tone_score` / `evaluate_critic` 파싱 / `deterministic_default` 동기화
- [x] SCORING_WEIGHTS 재조정 (peer_tone_fit 0.15, 합계 1.00) + `compute_quality_score` 반영
- [x] `scoring_weights.json` 재작성
- [x] generator template_id v1 → v2 전환 (feed/detail/plan/messages/review + critic)
- [x] stub fixture v2 경로 7개 신규 + critic v1 fixture 2개 schema 적응
- [x] 5 검증 게이트 전부 PASS (Jinja2 컴파일 / critic strict mode / 5 generator stub / live feed:v2 1건 / pytest 153 passed)

**scp_03_gen_peer_phaseE_complete**
