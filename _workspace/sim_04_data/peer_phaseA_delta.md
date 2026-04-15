# Phase Peer-A — sim-data-integrator delta

작업자: `sim-data-integrator`  
게이트: `sim_04_data_peer_phaseA_complete`

## 0. 생성/수정 파일 목록

| 상태 | 경로 |
|------|------|
| 수정 | `spot-simulator/config/persona_templates.yaml` (append-only merge) |
| 신규 | `spot-simulator/config/skills_catalog.yaml` |
| 신규 | `spot-simulator/config/personas/.gitkeep` (drop-in 디렉토리) |
| 수정 | `spot-simulator/data/loader.py` (`load_skills_catalog`, `load_personas` 추가, `load_persona_templates` anchor skip 호환) |
| 신규 | `_workspace/sim_04_data/peer_phaseA_delta.md` (이 문서) |

## 1. `persona_templates.yaml` 변경 요약

### 1-1. append-only 증거

- 파일 헤더에 "Phase 1~3 legacy invariants" / "Phase Peer-A append-only fields"
  블록 명시.
- 각 persona 섹션은 `# --- Phase 1~3 legacy fields (unchanged) ---` /
  `# --- Phase Peer-A extensions ---` 로 구분.
- Phase 1~3 6개 legacy 필드 값 **100% 동일** (`git diff` 기준 변경분 = 주석 + 새 필드만).
- 최상단에 `_base_persona: &base_persona` anchor 블록 추가.
- 각 persona 블록에 `<<: *base_persona` merge key + peer 필드 추가.

### 1-2. legacy 필드 보존 검증 (실측)

`python3 -c "from data.loader import load_persona_templates; ..."` 결과:

| persona | host_score | preferred_categories | time_preferences keys | budget_level |
|--------|----:|------|----:|----:|
| night_social | 0.7 | `[food, bar, cafe]` | 14 | 2 |
| weekend_explorer | 0.5 | `[exercise, nature, cafe]` | 14 | 2 |
| planner | 0.6 | `[food, culture]` | 14 | 3 |
| spontaneous | 0.75 | `[food, bar, exercise]` | 14 | 1 |
| homebody | 0.15 | `[cafe, culture]` | 14 | 1 |

= Phase 1~3 값 그대로.

### 1-3. peer 필드 추가 요약 (5 persona × 11 필드)

`_base_persona` 에서 자동 merge 되는 기본값:

```
role_preference: "both"
pocket_money_motivation: 0.50
wallet_monthly: 25000
time_budget_weekday: 3
time_budget_weekend: 10
space_level: 1
space_type: "cafe"
equipment: []
social_capital: 0.5
reputation_score: 0.5
skills: {}
```

persona 별 override 값:

| persona | wallet | pmm | space_level | space_type | social_cap | equipment(#) | skills(#) |
|--------|------:|----:|----:|------|----:|----:|----:|
| night_social     | 30000 | 0.75 | 2 | home   | 0.65 | 2 | 4 |
| weekend_explorer | 40000 | 0.55 | 1 | park   | 0.55 | 3 | 4 |
| planner          | 50000 | 0.40 | 2 | home   | 0.45 | 3 | 4 |
| spontaneous      | 22000 | 0.85 | 1 | cafe   | 0.70 | 2 | 4 |
| homebody         | 15000 | 0.50 | 2 | home   | 0.35 | 3 | 4 |

(pmm = pocket_money_motivation. 모든 persona 가 정확히 4 개 non-zero skill
엔트리 = plan §4-3 invariant 6 "3~6개" 범위 내.)

## 2. `skills_catalog.yaml` 18 스킬 필드 표

| SkillTopic | material | studio_rental | gym_rental | equip_rental | default_venue | teach_modes (sum=1.0) | level_floor |
|------------|---------:|--------------:|-----------:|-------------:|---------------|------------------------|------------:|
| 기타           | 0    |  —    |  —    | 3000 | cafe   | 1:1=.6 sg=.3 ws=.1 | 3 |
| 우쿨렐레       | 0    |  —    |  —    | 2000 | cafe   | 1:1=.5 sg=.4 ws=.1 | 3 |
| 피아노 기초    | 0    | 12000 |  —    |    0 | studio | 1:1=.7 sg=.3       | 3 |
| 홈쿡           | 3500 |  —    |  —    |    0 | home   | sg=.7 ws=.3        | 2 |
| 홈베이킹       | 4500 |  —    |  —    |    0 | home   | sg=.6 ws=.3 1:1=.1 | 2 |
| 핸드드립       | 2500 |  —    |  —    |    0 | home   | sg=.6 1:1=.4       | 3 |
| 러닝           |    0 |  —    |  —    |    0 | park   | sg=.7 ws=.3        | 2 |
| 요가 입문      |    0 | 20000 |  —    | 1500 | studio | sg=.8 ws=.2        | 3 |
| 볼더링         |    0 |  —    | 14000 | 2500 | gym    | sg=.7 1:1=.2 ws=.1 | 3 |
| 가벼운 등산    |    0 |  —    |  —    |    0 | park   | sg=.7 ws=.3        | 2 |
| 드로잉         | 3000 |  —    |  —    |    0 | home   | sg=.6 ws=.4        | 3 |
| 스마트폰 사진  |    0 |  —    |  —    |    0 | park   | sg=.6 ws=.4        | 2 |
| 캘리그라피     | 2500 |  —    |  —    |    0 | home   | sg=.6 ws=.4        | 3 |
| 영어 프리토킹  |    0 |  —    |  —    |    0 | cafe   | sg=.7 1:1=.3       | 2 |
| 코딩 입문      |    0 |  —    |  —    |    0 | cafe   | 1:1=.5 sg=.5       | 3 |
| 원예           | 5000 |  —    |  —    |    0 | home   | sg=.7 ws=.3        | 3 |
| 보드게임       |    0 |  —    |  —    |    0 | cafe   | sg=.7 ws=.3        | 2 |
| 타로           |    0 |  —    |  —    |    0 | cafe   | 1:1=.8 sg=.2       | 3 |

(sg = small_group, ws = workshop. `—` = 해당 키 생략.)

### Venue 규약 (catalog 에 **명시하지 않은** venue_rental)

- `home`, `park` → `venue_rental = 0`
- `cafe` → engine 측 공식 `2000 // expected_partners`
- `studio` → `studio_rental_total // expected_partners`
- `gym` → `gym_rental_total // expected_partners`

이 규약은 engine 측 `suggest_fee_breakdown` (`engine/fee.py`, Phase C) 의
책임으로 이관. catalog 는 실비 데이터만 선언.

## 3. `loader.py` 불변식 매핑표

plan §4-3 의 8 가지 불변식 → `_validate_persona()` 내 체크 매핑:

| # | Invariant | 체크 위치 / 판정 |
|---|-----------|-----------------|
| 1 | `_base_persona` anchor 사용 | yaml anchor 차원에서 PyYAML 이 자동 적용. loader 는 underscore prefix key 를 persona 에서 제외 (`load_personas` + `load_persona_templates`). |
| 2 | skills key ⊂ SkillTopic | `catalog_keys` 대조 (skills_catalog.yaml 을 SkillTopic source-of-truth 로 사용). 미스매치 → warn + skip |
| 3 | equipment items ⊂ SkillTopic | 동일 `catalog_keys` 대조 |
| 4 | home_region ∈ region_features | `region_ids` set 대조. 불일치 → warn + skip |
| 5 | teach / learn ∈ [0, 1] | 각 skill profile 검사. 위반 → warn + skip |
| 6 | non-zero skill count ∈ [3, 6] | `level>0 OR teach>0 OR learn>0` 개수. 위반 → warn only (non-fatal) |
| 7 | wallet_monthly ∈ [10000, 60000] | 정수 파싱 + 범위 체크. 위반 → warn + skip |
| 8 | pocket_money_motivation ∈ [0, 1] | float 파싱 + 범위 체크. 위반 → warn + skip |

추가 체크 (legacy 호환):
- Phase 1~3 legacy 6 키 (`host_score`, `join_score`, `home_region`,
  `preferred_categories`, `budget_level`, `time_preferences`) 누락 → warn + skip.
  legacy 호환을 명시적으로 유지하기 위함.

### 정책

- **6 번 (non-zero count)** 만 warn-only. 나머지 7 가지는 해당 persona 를
  dict 에서 제거 → engine 이 나머지 persona 로 계속 실행.
- 모든 warning 은 `warnings.warn(..., UserWarning)` — pytest
  `filterwarnings` 로 구분 수집 가능.

## 4. drop-in 디렉토리

- 경로: `spot-simulator/config/personas/`
- 포함: `.gitkeep` (사용법 주석 포함)
- 스캔 규약: `load_personas()` 가 `sorted(glob("*.yaml"))` 로 파일 순회.
  파일 내부 top-level key 가 그대로 persona id 가 된다.
- 충돌 규칙: 개별 파일이 `persona_templates.yaml` 을 **override**.

### 추가 예시 (실제 검증 통과)

`config/personas/_test_drop.yaml` 에 `side_hustler` 1 개 추가 후
`load_personas()` 호출 결과:

```
personas loaded: 6
keys: ['homebody', 'night_social', 'planner', 'side_hustler',
       'spontaneous', 'weekend_explorer']
side_hustler wallet: 18000
side_hustler skills: ['기타', '우쿨렐레', '영어 프리토킹']
```

(검증 직후 테스트 파일 제거. 현재 `config/personas/` 에는 `.gitkeep` 만 존재.)

## 5. 검증 결과 요약

| # | 검증 | 결과 |
|---|------|------|
| 1 | YAML 파싱 + anchor merge | 5 personas, 모든 legacy 키 + peer 키 존재 |
| 2 | skills_catalog 파싱 + dist sum | 18 스킬, 모든 `teach_mode_distribution` sum=1.0 (±0.01) |
| 3 | `load_personas()` + `load_skills_catalog()` | 5 personas / 18 skills 로드, skills/equipment 모두 catalog 매칭 |
| 4 | drop-in merge | `side_hustler` 자동 인식, count 5 → 6 |
| 5 | 기존 Phase 1~3 pytest | **53 passed** in 0.04s (회귀 없음) |
| 6 | `load_persona_templates()` legacy loader | `_base_persona` 스킵 후 5 personas 정상 로드 — `engine/runner.py` / `analysis/run_validate.py` 호환 |

## 6. Open questions (Phase B 인계용)

- **sim-engine-engineer** — 엔진의 persona 소비자 (`engine/decision.py`,
  `engine/runner.py`) 는 현재 `load_persona_templates()` 를 통해 **dict[str,dict]**
  를 받는다. Peer-A 필드 (wallet_monthly, skills, equipment, ...) 를
  실제로 읽는 쪽은 Phase B 에서 추가 예정. 지금은 key 존재만 해도 무해.
- **sim-model-designer** — `models/skills.py` 의 `SkillTopic` enum value
  집합과 `skills_catalog.yaml` key 집합이 1:1 매치여야 한다. 현재 둘 다
  18개 동일. 이후 skill 추가 시 **두 파일 동시 수정** 필수.
- **sim-analyst-qa** — Phase B 게이트에서 fee 분포 검증 시 plan §3-4
  "Fee 예시 표" 의 8 케이스를 기준선으로 사용. catalog 값이 그 표와 일치.
- loader 의 `_validate_persona` 는 skills dict 가 **빈 dict** 인 legacy-only
  persona 도 통과시킨다 (invariant 6 는 `skills` 가 비어있으면 skip).
  이는 peer 전환 과도기 정책 — Phase B 가 끝나면 비어있는 skills 도
  warn 으로 승격 검토.
