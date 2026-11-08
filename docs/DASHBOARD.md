# 대시보드 — 결과 비교 · 조건을 바꿔 시뮬레이션 실행 · Unity 재생 파일

```bash
pip install -r requirements.txt     # flask 포함
python dashboard/app.py             # http://127.0.0.1:8050 이 브라우저에서 열림 (끝내려면 Ctrl+C)
```

인터넷 없이 동작한다(외부 라이브러리·CDN 없음). 내 컴퓨터(127.0.0.1)에서만 접속된다.

## 화면

| 탭 | 하는 일 | 데이터 |
|---|---|---|
| 개요 | 헤드라인 숫자 4개(7일 추세선), **디지털 트윈 지도**(SUMO 도로망 382구간 + H3 칸별 하루 호출 + 실제 신호 교차로) + 칸별 순위·시간대별 호출, 전략 비교 표(신뢰구간 막대), 날짜별 점 그래프, 조건·근거 표 | `results/repeat/twin_v3/`, `grid.net.xml`, 수요 생성기, 근거 대장 |
| 실험 비교 | 저장된 실험을 골라 전략별 평균과 순찰 대비 차이(95% 신뢰구간·부호 검정), 날짜별 그래프 | `results/repeat/<이름>/runs/*.json` |
| 민감도 | 가정을 하나씩 바꾼 실험이 기준 실험의 같은 날짜 대비 얼마나 달라졌는지 | `sens_*`, `sens3_*` vs `twin_v2`, `twin_v3` |
| 새 시뮬레이션 | 조건(택시 수·손님 위치 방식·포기 시간·카카오 몫·하차 비율·시간대·요일·복귀 지연·배차 비용·도로 설정)과 전략·날짜를 골라 실제 시뮬레이션 실행 | `tools/repeat_experiments.py` 호출 |
| 작업 | 실행 중·끝난 작업의 진행률, 로그, 멈춤 | `results/dashboard_jobs/` |
| Unity 재생 | 고른 날짜·조건으로 Unity 재생 파일(map.json + 전략별 .json.gz) 만들기, Unity 폴더로 자동 복사 | `export_unity_replay.py` 호출 → `results/unity_replay/<이름>/` |

## 설계

| 항목 | 결정 | 이유 |
|---|---|---|
| 실행 경로 | 새 실험도 보고서와 같은 `tools/repeat_experiments.py`로 돌리고 같은 폴더 형식으로 저장 | 대시보드 결과와 보고서 결과가 같은 코드·같은 통계 → 따로 검증할 필요 없음 |
| 바꿀 수 있는 조건 | `dashboard/params.py`의 화이트리스트(14개)만, 범위·선택지 검사 | 근거 없는 임의 설정이 실험에 섞이지 않게 |
| 기본값·근거 | 기본값은 `config.json`+`config_loader.py`, 등급·출처는 근거 대장에서 읽음 | 화면의 근거와 문서가 항상 같다 |
| 조건 기록 | 새 실험마다 `results/repeat/<이름>/conditions.json`(설명·날짜·전략·바꾼 조건·시각) | 나중에 무엇을 돌렸는지 재현 |
| 동시 실행 | 한 번에 한 작업 (SUMO가 CPU를 많이 씀), 멈춤 시 SUMO 자식 프로세스까지 종료 (Windows는 taskkill /T) | 노트북 과부하 방지 |
| 이어 하기 | 끝난 실행(runs/*.json)은 다시 돌리지 않음 | 멈추거나 끊겨도 처음부터 다시 할 필요 없음 |
| 보고서 실험 보호 | 보고서에 쓰인 실험 이름(twin_v3 등)으로는 새 실험을 만들 수 없음 | 근거 자료가 덮어써지지 않게 |

## 화면 설계 — 참고한 레퍼런스와 적용

라이트 전용. 각 결정은 아래 공개 자료의 규칙을 그대로 따랐다.

| 레퍼런스 | 가져온 규칙 | 대시보드 적용 |
|---|---|---|
| [Vercel Geist 디자인 규칙](https://vercel.com/design.md) | 단색 캔버스, 색은 상태·데이터에만 / 섹션을 카드로 감싸지 않음 / 장식 그림자·알약 배지 금지 / 숫자 고정폭·오른쪽 정렬 / 막대는 0 기준 / 범례보다 직접 라벨 | 흰 바탕 + 섹션은 여백과 가는 선으로만 구분, 카드·그림자 제거(툴팁만 예외), 표 숫자 오른쪽 정렬, 근거 등급은 알약 대신 작은 글자 칸 |
| [Stripe Apps 차트 배치](https://docs.stripe.com/stripe-apps/patterns/chart-layout) · [Stripe 대시보드 분석](https://www.925studios.co/blog/stripe-dashboard-design-breakdown) | 헤드라인 숫자를 차트 위에 / 차트 높이 고정(180·320) / 한 줄에 차트 3개 이하 / 단색 추세선 / 색은 상태에만 | 상단 숫자 4개 + 비교 문장 + 회색 추세선, 시간대별 호출 180px, 지도 2/3 + 옆 패널 1/3 |
| [kepler.gl (Uber)](https://www.uber.com/en-CA/blog/keplergl/) · [Mapbox Light](https://blog.mapbox.com/light-and-dark-maps-for-data-visualization-3c4aed88b2e8) | 지도가 주인공, 바탕 지도는 조용하게, 데이터 레이어를 위에 | 도로는 회색(간선은 차로 수만큼 굵게), 칸별 호출만 색, 칸 번호 ↔ 옆 표 연동(마우스 올리면 지도에 표시) |
| [Carbon 데이터 시각화 색](https://carbondesignsystem.com/data-visualization/color-palettes/) | 분류 색 고정 순서, 라이트에선 진할수록 큰 값(단색 순차), 경고 색 별도 | 전략 4색 Purple70·Cyan50·Magenta50·Yellow50(dataviz 검사 통과), 호출은 Teal 단색 순차, 하락 Red60·상승 Green60 |
| [Statsig Pulse 결과 읽기](https://docs.statsig.com/experiments/interpreting-results/read-results) | 지표별 차이와 95% 신뢰구간, 유의한 개선 초록·악화 빨강·유의하지 않음 회색 | 전략 비교 표 안에 신뢰구간 막대, 0을 지나면 회색 |
| [Pretendard](https://github.com/orioncactus/pretendard) (OFL-1.1) | 한국어 UI 표준 서체 | `dashboard/static/fonts/`에 KS X 1001 부분 글꼴 3굵기(약 800KB) 포함 → 인터넷 없이 같은 모양 |

그 밖에: 위쪽 탭 내비게이션(Vercel), 설정 화면은 왼쪽 설명·오른쪽 입력 + 아래 고정 실행 막대(Vercel 설정), 날짜별 결과는 점 그래프(0 기준이 필요 없는 위치 인코딩), 1100px 이하 1열·760px 이하 휴대폰 배치(390px에서 가로 넘침 없음 확인).

지도 데이터는 `dashboard/twin_map.py`가 만들고 `results/dashboard_cache/`에 저장한다(도로망·설정이 바뀌면 다시 만듦).

## Unity 연결

1. 대시보드 "Unity 재생" 탭에서 날짜·전략·조건을 고르고 "재생 파일 만들기" (전략마다 약 10분)
2. Unity StreamingAssets 폴더를 적으면 끝난 뒤 자동 복사, 아니면 `results/unity_replay/<이름>/`의 파일을 복사
3. Unity(ldk-unity 브랜치)에서 FetexReplayPlayer로 재생 — 재생 포맷은 `docs/replay_motion.md`

## 테스트

`tests/test_dashboard.py` — 조건 화이트리스트·범위, 날짜·이름 검사, 읽기 API, 실행 명령 조립(시뮬레이션은 돌리지 않음), Unity 임시 설정, 지도(도로 382·교차로 170·신호 31, 칸별 호출 합 = 시간대별 합 = 하루 호출).

실제 실행 점검: 대시보드에서 `dash_e2e_taxi70`(택시 70대, 1일×순찰)을 돌려 `results/repeat/dash_e2e_taxi70/`에 저장되고 실험 비교에 나타나는 것을 확인.
