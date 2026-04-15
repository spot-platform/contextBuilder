# Phase 2 Generators — Delta Report

Phase 1 의 `FeedGenerator` 패턴을 detail/plan/messages/review 4종으로 확장한 결과.

## 1. 생성된 파일

### 소스
- `src/pipeline/generators/detail.py` — `SpotDetailGenerator`
- `src/pipeline/generators/plan.py` — `SpotPlanGenerator`
- `src/pipeline/generators/messages.py` — `MessagesGenerator`
- `src/pipeline/generators/review.py` — `ReviewGenerator`
- `src/pipeline/generators/__init__.py` — 4개 generator export 추가

### 프롬프트
- `config/prompts/detail/v1.j2`
- `config/prompts/plan/v1.j2`
- `config/prompts/messages/v1.j2`
- `config/prompts/review/v1.j2`

### 설정
- `config/weights/review_rating_distribution.json` — placeholder 에서 실제 분포 (base / positive / neutral / negative) 로 확장

### 산출물
- `_workspace/scp_03_gen/sample_outputs_phase2.jsonl` — 4 types × 3 spot_ids (S_0001, S_0006, S_0050) × 2 variants = 24 행

## 2. 각 generator 의 추가 변수 (공용 16개 외)

모든 generator 는 `BaseGenerator.spec_to_variables()` 의 공용 16개 키(`COMMON_VARIABLE_KEYS`)
를 그대로 상속하고 아래 키만 추가한다.

| Generator | 추가 변수 | 용도 |
|---|---|---|
| `SpotDetailGenerator` | `tone_examples`, `materials_bucket`, `description_length_bucket` | §7-2 자연스러움 분포. deterministic seed = `hash(spot_id + "materials"/"description_length")` |
| `SpotPlanGenerator` | `tone_examples`, `schedule_duration_minutes`, `plan_draft` | cross-reference total_duration 강제 + 결정성 5-step 초안 (0/15/30/duration-20/duration-10분) |
| `MessagesGenerator` | `tone_examples`, `host_trust_level`, `recruit_status` | supporter_required → trusted/neutral, activity_result 유무 → closed/recruiting |
| `ReviewGenerator` | `tone_examples`, `target_rating`, `target_sentiment`, `review_length_bucket`, `issues_context`, `noshow_happened`, `no_show_count`, `actual_participants` | §7-3 별점 분포 + §7-2 리뷰 길이 + activity_result 정합 가이드 |

### ReviewGenerator 별점 샘플링 분포 (§7-3 반영)

```
positive → {5:0.55, 4:0.30, 3:0.10, 2:0.03, 1:0.02}
neutral  → {5:0.25, 4:0.35, 3:0.25, 2:0.10, 1:0.05}
negative → {5:0.05, 4:0.15, 3:0.30, 2:0.30, 1:0.20}
```

seed = `hash(spot_id + variant + "rating")` — 재현성 보장.
`target_sentiment` 은 최종 rating 기준으로 재계산 (rating≥4 → positive / ==3 → neutral / ≤2 → negative) 하여
prompt 에 전달되므로 sentiment 불일치 발생 불가.

## 3. 프롬프트 길이 hard rule (plan §5 Layer 1 대응)

| Template | hard rule |
|---|---|
| `detail/v1.j2` | **description 80~800자, 정확히 3~6문장**. title 12~60자. cost_breakdown 합계 ≈ budget_cost_per_person. |
| `plan/v1.j2` | **steps 3~7개, 각 activity 4~40자, total_duration_minutes 정확히 spec.schedule.duration_minutes**. |
| `messages/v1.j2` | recruiting_intro 40~200자 2~4문장 / join_approval 20~150자 1~2문장 / day_of_notice 30~200자 1~3문장 / post_thanks 20~150자 1~2문장. **4개 snippet 한 호스트 톤 일관.** |
| `review/v1.j2` | **rating=target_rating 고정, sentiment=target_sentiment 고정**, review_text 15~400자. 길이 버킷 short/medium/long 에 맞춘 문장 수. |

## 4. Phase 1 교훈 — "프롬프트와 plan §5 Layer 1 제약 일치"

Phase 1 gate 에서 feed/v1.j2 의 summary 지시가 schema 의 문장 수 제약과 어긋나 reject 가 났던 교훈을
4종에 다음과 같이 적용:

1. **Schema 의 min/maxLength 를 프롬프트 상단 hard rule 에 문자열로 그대로 박았다.**
   예: detail 의 "description: 80~800자, 정확히 3~6문장" — schema 값과 문장 수를 명시적으로 요구.
2. **"넘기면 즉시 reject" 라는 문구를 모든 길이 지시 뒤에 붙였다.**
   LLM 이 길이를 선택 사항으로 오해하지 않도록.
3. **길이 버킷(`desired_length_bucket`) 은 허용 범위 내에서의 조절 수단**이라고 명확히 표시했다
   (hard rule 을 변경하지 못함).
4. **plan 은 결정성 초안(`plan_draft`) 을 generator 에서 만들어 프롬프트에 주입**했다.
   LLM 이 steps 수와 시간을 새로 계산할 여지를 없애서 total_duration_minutes ↔ spec 불일치를 원천 차단.
5. **review 는 target_rating / target_sentiment 를 generator 가 확정한 후 프롬프트에 고정값으로 전달**한다.
   LLM 이 별점 분포를 임의 해석하지 못하게 하고, sentiment 정합성을 sample 단계에서 이미 보장.

## 5. Cross-reference 맥락 인식 (Phase 2 핵심)

각 프롬프트에 "Cross-reference 맥락" 섹션을 추가해 스팟 단위 일관성 rule 을 명시:

- **detail**: spot_id / 지역 / 인원 / 금액 / 카테고리 feed 와 100% 일치.
- **plan**: total_duration_minutes === spec.schedule.duration_minutes.
- **messages**: recruit_status(recruiting/closed) 에 따른 톤 매칭, 4 snippet 간 동일 호스트 말투 강제.
- **review**: actual_participants / no_show_count 고려, noshow_happened=True 면 "전원 참여" 문구 금지.

## 6. 검증 결과 (직접 실행 완료)

1. **4종 generator × generate() stub 호출**: 각 2 후보 반환 확인 (primary / alternative).
   ```
   SpotDetailGenerator -> primary alternative | keys: ['title','description','activity_purpose','progress_style',...]
   SpotPlanGenerator -> primary alternative | keys: ['steps','total_duration_minutes']
   MessagesGenerator -> primary alternative | keys: ['recruiting_intro','join_approval','day_of_notice','post_thanks']
   ReviewGenerator -> primary alternative | keys: ['rating','review_text','satisfaction_tags','recommend',...]
   ```
2. **Jinja2 StrictUndefined 컴파일**: 4종 템플릿 모두 ok (compile + render both pass).
3. **COMMON_VARIABLE_KEYS 커버리지**: 4종 모두 16 key superset 확인.

## 7. Schema 파일 의존성

4종 generator 는 모두 `src/pipeline/llm/schemas/{detail,plan,messages,review}.json` 를
`schema_path` 클래스 상수로 참조한다. Phase 2 병렬 작업 중 codex-bridge-engineer 가 이미 해당
파일들을 작성했음을 확인했다 (모두 존재). generator 생성자에서 `schema_path.exists()` 체크 후
없으면 warning 만 출력하고 계속 진행하도록 fallback 구현 — live 모드 진입 전 bridge 가 resolve 해야 함.
