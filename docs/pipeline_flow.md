# Python 실행 흐름

1. `fetex.simulation.build_env`: OSM 또는 합성 격자 → 연결 도로·차량 XML → 설정 스냅샷·도로/H3 매핑.
2. `data/generate.py`: 같은 날짜·시드로 재현되는 호출/외부 관측 생성. 학습 기간은 시뮬레이션 날짜 이전.
3. `fetex.preprocessing.time_series_prep`: 전체 시간×H3 격자에 0수요 보존 → 지역별 lag/이동평균/달력/공간 피처 → 다음 6구간 타깃.
4. `scripts/train_dispatch_model.py`: 공통 시간 경계 train/validation/test, 타깃 경계 purge → 검증 자료로 모델 선택 → 테스트 평가·모델 저장.
5. `fetex.runtime.measure_wait_time`: 고정 호출 재생 → 탑승·타임아웃 계측 → 현재 호출 배차 → 5분마다 미래 수요 기반 빈 택시 재배치.
6. `fetex.dispatch.forecast_dispatcher`: 완료된 관측만 추론 → 수요/공급 불균형 → 할증 가중 목표 분포 → 정수 택시 예산 배분 → 실제 도로 이동시간으로 목적지 선정.
7. `compare_runs`: 동일 입력 fingerprint 검증 → 승객 결과·차량 분포·예측·재배치 CSV/JSON 저장.

`prepositioned`는 고정 시간대 규칙이며 학습 예측이 아니다. `hungarian`은 현재 호출 매칭이고 `forecast`는 빈 택시의 선제 재배치다. 두 기능의 효과를 구분해서 평가한다.

Unity 표현 계층은 `fetex.integrations.unity_replay` → `map.json`/`patrol.json`/`forecast.json` → `unity/Assets/Scripts/SumoReplayPlayer.cs`로 연결한다. Unity에서 제공 Asset prefab을 Inspector에 지정하면 SUMO 좌표·차량 상태·forecast 시점을 재생할 수 있다. Asset import 절차는 `unity/README.md`를 따른다.
