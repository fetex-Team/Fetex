# 결과 요약 — 근거 기반 강남역 디지털 트윈 (ldk, 2026-11-08)

> **결론: 실데이터로 맞춘 디지털 트윈에서 배차 방법 간 차이는 없고, 결과는 택시 수가 좌우한다.**

| 구분 | 항목 | 결과 | 근거 | 원자료·문서 |
|---|---|---|---|---|
| **현실화** | 도로 속도 | 실제보다 2.1배 빠름 → **0.93배** | 서울시 TOPIS 택시 GPS 실측 속도 | `results/topis_speed/eval_validation/`, `docs/design/07_road_realism.md` |
| | 버스차로 | 일반도로 → **버스 전용** | 서울 중앙버스전용차로 24시간 운영 | 〃 |
| | 제한속도 | 100km/h → **50·30km/h** | 안전속도 5030 (2021-04-17 시행) | `module1_simulation/typemap/seoul_5030.typ.xml` |
| | 신호 | 추정 → **실제 주기 140~170초** | 경찰청 교차로 신호 운영계획 (강남역·교보타워사거리) | `data/external/signal_plans_gangnam.json` |
| **실데이터 교체** | 손님 위치 | 건물 배수(가정) → **생활인구 비례** | 서울 열린데이터광장 생활인구(250m) | `data/external/seoul_living_pop/` |
| | 택시 대수 | 50대(가정) → **61대** | 국토교통부 노드링크별 택시 통행량 × TOPIS 속도 × 공차 비율 | `results/taxi_supply/`, `module4_dispatch/experiments/estimate_taxi_supply.py` |
| | 포기 시간 | 500초(가정) → **600초** | 서울연구원 2024 택시 이용 시민만족도 조사 (앱 이용자 98.2% 10분 안 탑승) | `presets/default/기본.json` |
| | 구역 안 하차 | 9%(가정) → **5.4%** | 한국교통연구원 택시 기종점 통행량 | `data/external/taxi_od/`, `data/loaders/extract_taxi_od.py` |
| | 근거 기록 | 설정 **42개** 출처·등급 | 근거 대장 + 설정과 대장 일치 테스트 | `docs/design/08_evidence_register.md`, `tests/test_evidence_register.py` |
| **최종 결과** | 순찰 | **95.7%**, 대기 153초 | 7일 × 4전략, 택시 61대, 같은 손님 목록 | `results/repeat/twin_v3/summary.md` |
| | 예측 재배치 | **95.8%**, 대기 154초 | 〃 | 〃 |
| | 시간표 선배치 | **95.5%**, 대기 166초 | 〃 | 〃 |
| | RL | **95.7%**, 대기 180초 (7일 모두 김, p=0.016) | 〃 (같은 환경에서 재학습) | 〃, `docs/design/05_rl_retraining.md` |
| | 평일 바쁜 날 | 88~91% | 9/10 목, 9/18 금 | 〃 |
| **민감도** | 택시 61→50대 | **−21%p** | 평일 3일, 하나씩 바꿈 | `results/repeat/sens_fleet50/` |
| | 나간 택시 늦게 복귀 | **−22%p** | 〃 | `results/repeat/sens_external_away/` |
| | 강남역 밤 시간대 적용 | −8.5%p | 〃 (기종점 자료, 표본 편차로 기본값 미사용) | `results/repeat/sens3_hourly_gangnam/` |
| | 손님 쪽 가정들 | ±2.3%p 안 | 요일·하차 비율·포기 시간·승차 위치 | `results/repeat/sens_*/`, `sens3_internal_exp/` |
| **검증** | 도로 속도 | 실측의 0.93배 (오차 18%) | TOPIS, 보정에 안 쓴 평가 7일 | `results/topis_speed/eval_validation/summary.md` |
| | 배차 예상시간 | 실제의 0.81배 | 한국교통연구원 택시 기종점 통행시간 (90개 칸 쌍) | `results/topis_speed/od_time_validation.csv`, `module1_simulation/checks/validate_od_time.py` |
| | 탑승 성공률 | 95.7% vs 카카오 발표 94% | 카카오모빌리티 2025 상반기 (범위가 달라 보정엔 미사용) | `REPORT.md` 5.9 |
| **결론** | | **방법 차이 없음, 택시 수가 좌우** | | `REPORT.md` 5.8~5.9 |

## 남은 한계

| 항목 | 상태 | 이유 |
|---|---|---|
| 나간 택시 복귀 시간 | 가정 (운행시간 × 2) | 택시가 언제 돌아오는지 공개 자료 없음 |
| 요일 배율 | 2022년 1월 통계 | 2025년판에 요일 표 없음, 기종점 자료는 평일만 |
| 신호 | 2곳만 실제, 29곳 추정 | 경찰청 공개 범위 |
| TOPIS 2026년 10월 속도 | 2025년 같은 달로 대체 | 2026-11-10쯤 공개 예정 |

## 재현

```bash
python module4_dispatch/experiments/repeat_experiments.py --name twin_v3 --strategies patrol prepositioned rl_prepositioned forecast --jobs 2
python -m pytest tests
```
