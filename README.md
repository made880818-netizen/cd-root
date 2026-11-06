# CD ROOT 🔎

임의의 CSV에서 **이상 사건을 자동으로 찾고**, 서로 다른 가설을 맡은 AI 에이전트들이 **증거로 경쟁**해 가장 근거가 강한 가설을 고르는 서비스.

- 배포 주소: https://cd-root-6tpqyp5agfmdjta7iwq4hp.streamlit.app/ (입구: https://cd-root.vercel.app/)
## 핵심 원칙
| 원칙 | 구현 |
|---|---|
| 숫자와 점수는 코드가 계산 | `export_case.py`, `agents.py`가 CSV에서 계산. 가설 점수·순위는 코드가 확정 |
| LLM은 주장·반박·서술만 | Claude 에이전트 3명이 가설을 주장, 검증 에이전트가 반박, Judge가 보고서 작성 |
| 장부 밖 숫자 차단 | 발언 속 숫자가 증거 장부(L번호)에 없으면 표시, 보고서에 있으면 규칙 기반 보고서로 교체 |
| 원인은 후보 | "원인 확정" 표현 금지. 가설은 서로 배타적이지 않음 |
| 어떤 CSV든 | 컬럼명 하드코딩 없음. 값의 성질로 역할(축/수치/그룹/플래그) 분류, 실패 시 폴백 |

## 동작 흐름
1. CSV 업로드 → 인코딩 자동 판별(utf-8/cp949 등), 컬럼 역할 분류
2. 사건 구간 자동 탐지(노이즈 대비 변화가 가장 큰 창). 시간 축이 없으면 그룹 비교 모드. 사건이 없으면 안내 화면
3. 주 지표 후보 드롭다운(자동은 변화의 확실성 z 기준) → 선택 시 재계산
4. 가설 풀 생성과 코드 채점 → 에이전트 토론 → 보고서
5. 어느 단계에서 실패해도 규칙 기반 보고서로 폴백, 저장된 토론 결과(`cache/`) 재사용 가능

## 실행
```bash
python -m pip install -r requirements.txt
# 키는 환경변수 또는 .streamlit/secrets.toml (양식: .streamlit/secrets.toml.example)
python -m streamlit run app.py
```
- `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`(선택), `CLAUDE_MODEL`(선택), `DISABLE_LLM=1`(AI 끄기), `DISABLE_TTS=1`
- 키가 없거나 호출이 실패하면 규칙 기반으로 동작한다. **키는 코드·로그·저장소에 넣지 않는다.**

## 배포 구조
```
방문자 ─▶ Vercel(vercel/, 입구 페이지) ─ 자동 이동 ─▶ Streamlit Community Cloud(app.py) ─▶ Claude(AI 검토) / Supabase(결과 저장)
```
- **앱 본체(Streamlit)는 Streamlit Community Cloud**에서 실행한다. Streamlit은 지속적인 웹소켓 연결이 필요해서 Vercel 서버리스에서는 안정적으로 돌지 않는다.
- **Vercel에는 입구 페이지(`vercel/index.html`)만** 올린다. 앱 주소(`vercel/config.js` 한 줄)로 자동 이동시킨다. (iframe 임베드는 실제 브라우저에서 막혀 쓰지 않는다)
- **키는 저장소에 없다.** 배포 환경의 Secrets에만 둔다. 예시: `.streamlit/secrets.toml.example`
- **AI 검토 횟수 제한**: 방문자당 3번, 서버 전체 시간당 30번(공유 API 예산 보호). 저장된 결과 보기와 규칙 기반 분석은 제한이 없다.
- **Supabase 저장(선택)**: 끝난 검토의 요약(증거·점수·AI 발언·보고서)을 `investigations` 테이블에 저장한다. **원본 CSV 행과 업로드 파일 이름은 저장하지 않는다.**
  테이블은 `supabase/migrations/`의 SQL로 만든다(`npx supabase db push`). RLS를 켜고 정책을 두지 않아, 서버의 service_role 키로만 접근된다.
  설정이 없으면 저장하지 않고 앱은 그대로 동작한다. 시험: `python tests/test_store.py`
- 틸론 GPU 실시간 연동은 배포에서는 끄고(Secrets에 `GPU_QUEUE_*`를 넣지 않음) 저장된 결과를 보여 준다.

## 폴더
```
app.py                  Streamlit 화면
src/export_case.py      CSV -> 사건 증거 JSON (계약: case_name, period, evidence[id,label,value,unit])
src/agents.py           증거 장부, 도구 11개, 가설 채점, 재계산 검증, 규칙 기반 보고서
src/debate.py           Claude 토론, 숫자 검사, 캐시
src/config.py, logger.py 설정, 로그(키 마스킹, 화면 디버그 패널)
gpu_inputs/             틸론 GPU 입력 JSON (export_case.py가 실제 CSV에서 계산)
results/qwen/           틸론 GPU 결과 (웹은 읽기만)
assets/audio/           미리 만든 음성 안내 3개
tools/                  틸론 셀 생성, 결과 풀기, TTS 생성
tests/                  낯선 CSV 시험, 화면 시험, GPU 입력 계약 검증
```

## 교내 GPU(틸론) 활용
**역할**: 소형 모델(Qwen2.5-1.5B-Instruct)이 사건 요약 카드를 만든다. 프롬프트 두 가지를 같은 조건에서 비교해 코드로 채점하고 더 나은 쪽을 고른다. 추론과 보고서는 Claude와 코드가 맡는다.

**저장된 결과(항상 동작)**: 호스트에서 `export_case.py`로 입력 JSON 생성 → 틸론 노트북 실행 → 결과 JSON을 `results/qwen/`에 저장 → 웹은 읽기만 한다.

**실시간 연동(선택, 틸론이 켜져 있을 때만)**: 틸론은 외부에서 직접 접속할 수 없고 밖으로만 나갈 수 있어서, GitHub 비공개 저장소의 Issues를 우편함으로 쓴다.
웹이 사건 요약 JSON을 작업(issue)으로 올리면 → 틸론 노트북의 워커(`tools/tilon_worker_cell.py`)가 3초마다 가져가 Qwen으로 A/B 요약·채점 → 결과를 댓글로 올리면 → 웹이 받아 표시한다.
워커의 하트비트로 상단에 `TILON GPU ONLINE / OFFLINE`을 표시하고, 꺼져 있으면 저장된 결과로 자동 폴백한다. (`src/gpu_queue.py`, 설정: `GPU_QUEUE_REPO`, `GPU_QUEUE_TOKEN`)
원본 행은 보내지 않고 요약된 증거만 전달한다. 시험: `python tests/test_gpu_queue.py`, `python tests/e2e_gpu.py` (가짜 GitHub·가짜 워커로 전 과정 검증).

| 구분 | 내용 |
|---|---|
| 블록 | 텍스트 |
| 바꾼 조건 | 프롬프트 방식 (A 자유 서술 / B 증거 값 전부 인용·단정 금지) |
| 고정한 조건 | 모델, 입력 사건 데이터, `max_new_tokens=400`, greedy 생성 |
| 환경 | RTX PRO 6000 Blackwell 16GB, Python 3.11, torch 2.7.1, transformers 5.22.1, 최대 VRAM 3.14GB |
| 후보 모델 | `Qwen/Qwen2.5-3B-Instruct` — 필요 VRAM은 fp16 약 6~7GB(추정, 미실측) |

| 사례 | A 커버리지 | B 커버리지 | 선택 |
|---|---|---|---|
| orders (시간 축 사건) | 1/11 | **7/11** | B |
| orders_revenue (지표 변경) | 0/12 | 3/12 | B |
| orders_nodate (그룹 비교) | 4/4 | 4/4 | B (동점이면 B 규칙) |

선택 이유(한 문장): 같은 입력에서 B가 증거 커버리지를 1/11에서 7/11로 높였고 장부 밖 숫자는 0개였기 때문. 결과 파일: `results/qwen/*/case_summary_{A,B}.json`, `selected.json`.

## 알려진 한계
- **토론은 점수를 바꾸지 않는다.** 순위는 코드가 증거로 정한다. "AI가 순위를 정한다"가 아니다.
- 숫자 검사는 10 미만 정수와 소수점 없는 값을 검사하지 않고, 문장의 **의미·방향 오류는 못 잡는다.**
- 소형 모델 요약은 숫자는 맞아도 의미가 틀린다. 예: 선행/후행 방향을 뒤집음(orders B), `revenue`를 "금융 서비스 비용"으로 오해(orders_revenue B), 동점 규칙이 더 부정확한 쪽을 고름(orders_nodate). 그래서 요약 카드만 맡기고 추론은 Claude와 코드가 한다.
- 주 지표 자동 선택은 변화의 확실성(z) 기준이라 사용자가 원하는 지표(예: 매출)가 아닐 수 있다 → 드롭다운으로 변경.
- 그룹 쏠림 점수에는 우연 대조가 없다. 시간 축 없이 행마다 무작위 그룹이 붙은 데이터에서는 과대평가될 수 있다.
- 사건 구간은 가장 크게 변한 창 하나만 잡는다. 행이 너무 적은 CSV는 사건 없음으로 처리한다.

## 시험
```bash
python tests/stress_csv.py          # 낯선 CSV 폴더(data/)가 에러 없이 폴백까지 도는지
python tests/test_app.py [--llm]    # Streamlit 화면 구동 (--llm: 실제 API 토론)
python tests/check_gpu_contract.py  # export_case 출력이 gpu_job 검증을 통과하는지, gpu_inputs 생성
```
