# FeTex 협업 가이드

## 작업 경계

- `fetex/core`: 설정·저장소 경로만 관리한다. 다른 모듈의 도메인 로직을 넣지 않는다.
- `fetex/simulation`, `fetex/runtime`: SUMO 환경, 차량·승객 생명주기와 계측을 담당한다.
- `fetex/preprocessing`, `fetex/forecasting`, `fetex/dispatch`: 각각 피처, 예측, 배차·가격·재배치를 담당한다.
- `fetex/geospatial`, `fetex/integrations`, `fetex/validation`: 지도 보조, Unity 연결, 검증 기능을 담당한다.
- `scripts/`는 얇은 CLI만 둔다. 재사용할 로직은 반드시 `fetex/` 안으로 넣는다.

새 import는 항상 `fetex.*`를 사용한다. 루트의 호환 실행 파일은 기존 명령을 유지하기 위한
어댑터이므로 새 기능을 추가하지 않는다.

## 설정·데이터·산출물

- 기본 설정은 루트 `config.json`, 재현 시나리오는 `presets/`에 둔다.
- 상대 경로는 `fetex.core.paths.PROJECT_ROOT` 기준으로 해석한다. `__file__`의 부모를 임의의
프로젝트 루트로 가정하지 않는다.
- 입력 데이터는 `data/`, 모델은 `saved_models/`, 재현 결과는 `results/`에만 둔다.
- 대용량 생성 데이터와 개인 API 키는 커밋하지 않는다. `API_KEYS.md` 같은 비밀 파일의 값은
로그·문서·PR에 복사하지 않는다.

## 변경 절차

1. 기능 경계에 맞는 한 패키지만 수정하고, 경계를 넘는 계약(설정 키·CSV 열·모델 artifact)은 문서화한다.
2. `presets/`의 경로와 `scripts/verify_forecast_contract.py`를 함께 확인한다.
3. 최소한 아래 검증을 실행한다.

```powershell
.venv/Scripts/python.exe tests/test_module2.py
.venv/Scripts/python.exe tests/integration/tests_logic.py
.venv/Scripts/python.exe scripts/verify_forecast_contract.py --config-path presets/forecast_sample.json
```

4. PR에는 변경한 패키지, 실행한 검증, 데이터·모델 artifact의 변경 여부를 한 줄씩 남긴다.

## Git 규칙

- `develop`에는 직접 작업하지 않고 `codex/`, `feat/`, `fix/` 등의 짧은 작업 브랜치를 사용한다.
- 생성 결과의 대규모 갱신과 기능 코드는 별도 커밋으로 나눈다.
- 다른 사람의 변경과 충돌하면 삭제·재생성으로 해결하지 말고, 공용 계약과 결과 재현 여부를 먼저 확인한다.
