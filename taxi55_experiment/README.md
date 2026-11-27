# 택시 55대 재학습·비교 실험

## 무엇을 하나
| 항목 | 값 |
|---|---|
| 조건 | 「근거기반_현실화」 프리셋 + **택시 55대** (실제 신호·TOPIS 속도·버스전용차로·대기 한도 600초) |
| 학습 | RL **9만 스텝** × 보상 방식 2가지 (`default`, `gravityVer`) — 수요 예측(demand_v2)은 그대로 두고 RL만 학습 |
| 저장본 고르기 | 3만·5만·7만·9만 스텝 저장본 + 최종 모델을 검증 3일(9/15, 9/30, 10/21)로 돌려 가장 좋은 것 선택 |
| 최종 비교 | 고정 7일(9/7, 9/10, 9/18, 10/13, 10/14, 10/18, 10/24) × 순찰·시간표 선배치·예측 재배치·RL 3종(KJS, 새 default, 새 gravityVer) |

검증 날짜와 최종 비교 날짜를 다르게 둔 이유: 같은 날짜로 고르고 같은 날짜로 평가하면 성적이 실제보다 좋게 나온다.

## 실행 방법
PowerShell에서 프로젝트 폴더로 이동한 뒤:

```powershell
cd C:\Users\User\Desktop\AIMobility
C:\Users\User\Desktop\AIMobility-venv\Scripts\python.exe taxi55_experiment\taxi55.py all
```

`all` = 학습 2번 → 저장본 고르기 → 7일 비교 → 결과 표. 단계별로 하려면 `all` 대신 `train`, `pick`, `compare`, `report`.
한 가지 보상 방식만 학습: `train --only gravityVer`.

**예상 시간** (16코어 PC 기준 대략): 학습 약 5~6시간 × 2, 저장본 고르기 약 1.5시간, 7일 비교 약 1.5시간.
중간에 창을 닫거나 꺼져도 다시 실행하면 끝난 부분은 건너뛴다 (단, 학습 도중에 멈추면 그 보상 방식 학습은 처음부터).

## 결과가 생기는 곳
| 파일·폴더 | 내용 |
|---|---|
| `taxi55_experiment/결과_7일비교.md` | **최종 비교표** (순찰 대비 차이·신뢰구간 포함) |
| `taxi55_experiment/결과_저장본고르기.md` | 저장본별 검증 성적과 선택 결과 |
| `saved_models_backups/taxi55_*` , `kjs_best1111` | 모델 세트 (설정 GUI의 A/B 창에서도 골라 쓸 수 있음) |
| `taxi55_experiment/checkpoints/<보상방식>/` | 학습 중간 저장본 |
| `taxi55_experiment/training_logs/<보상방식>/` | 학습 보상 기록 |
| `results/repeat/taxi55_*` | 시뮬레이션 원본 결과 |

## 알아둘 점
- 학습이 끝나면 `module4_dispatch/rl_reposition_model.zip`(지금 쓰는 RL 모델)이 마지막 학습 모델(gravityVer)로 바뀐다. 이전 모델은 `saved_models_backups/`와 `rl_model_backups/`에 자동 보관된다.
- KJS 모델은 택시 61대 조건에서 학습한 모델이라, 55대 조건에서는 새 모델보다 불리할 수 있다. 이 점을 감안해서 비교 결과를 해석할 것.
- RL은 평일 날짜로만 학습한다(팀 기본값 `rl_train_day_type=weekday`). 최종 7일에는 주말 2일(10/18 일, 10/24 토)이 들어 있다.
- 설정 파일 `config_default.json`, `config_gravityVer.json`은 보상 방식만 다르다. 개인 설정 `config.json`은 건드리지 않는다.
