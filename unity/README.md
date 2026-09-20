# Unity 3D Replay 연결

이 폴더는 SUMO에서 계산한 택시·일반 차량·장애물·예측 수요를 제공된 Unity Asset으로
재생하는 연결 계층이다. 원본 Asset은 교육용 제공물이라 저장소에 재배포하지 않는다.

## 1. 제공 Asset 가져오기

`Kakaomobility.zip`을 내려받아 Unity 프로젝트에서 아래 패키지를 차례대로 Import 한다.

1. `Modeling/effect/MapSetting_2.unitypackage`
2. `Modeling/map/street_main.unitypackage`
3. `Modeling/object/car_taxi.unitypackage`
4. `Modeling/object/obs_car_1.unitypackage`, `obs_car_2.unitypackage`, `obs_kickboard.unitypackage`
5. `Modeling/animation/carwheel_move.unitypackage`, `kickboard_fall.unitypackage`

`street_main`을 씬의 도로로 배치하고, 택시·일반차·장애물 prefab을 준비한다. 킥보드
장애물에는 `kickboard_fall` 애니메이션을 연결한다.

## 2. SUMO replay 생성

저장소 루트에서 다음 명령을 실행한다. `forecast_sample.json`은 저장소의 8일 샘플과
43개 피처 모델로 바로 검증할 수 있고, `forecast_demo.json`은 35일 데이터를 새로
만든 뒤 금요일 저녁 실험에 쓴다.

```powershell
python module1_simulation/build_env.py --config-path presets/forecast_sample.json --config-dir results/unity_replay/sumo
python export_unity_replay.py --config-path presets/forecast_sample.json --meta results/unity_replay/sumo/runtime_meta.json --output results/unity_replay
```

생성물은 `map.json`, `patrol.json`, `forecast.json`이다. 두 replay는 동일 호출
fingerprint를 가져야 하며, `forecast.json`에는 5분 단위의 6개 미래 수요·가용 택시·할증
배수·목표 배차 대수가 포함된다.

## 3. Unity 씬 연결

1. `Assets/Scripts/SumoReplayPlayer.cs`를 Unity 프로젝트의 `Assets/Scripts/`에 복사한다.
2. `patrol.json` 또는 `forecast.json`을 Unity의 `StreamingAssets/KakaomobilityReplay/`에 복사한다.
3. 빈 GameObject에 `SumoReplayPlayer`를 붙이고 `Replay Json`에 해당 TextAsset을 지정한다.
4. Inspector에 import한 Taxi / Normal Vehicle / Autonomous Vehicle / Obstacle prefab을 연결한다.
5. `Forecast` replay와 `Patrol` replay를 번갈아 재생해 택시 재배치, 대기 승객, 차량 분포 변화를 화면으로 비교한다.

좌표는 SUMO 중심점을 원점으로 옮긴 미터 단위 `(x, z)`다. Asset 맵의 원점이 다르면
`World Offset`만 조정한다. 이 스크립트는 prefab을 비워 둔 경우에도 primitive fallback을
만들어 JSON 연결을 먼저 확인할 수 있다.
