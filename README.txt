TraCI 병목 리팩토링 패치 (프로젝트 루트에서 압축 풀면 같은 경로로 덮어써짐)

루트/
  step_snapshot.py            (신규)
  passenger_manager.py
  passenger_spawn_manager.py
  taxi_manager.py
  measure_wait_time.py
  export_unity_replay.py
module1_simulation/
  run_simulation.py
module2_preprocessing/
  sim_log_recorder.py
module4_dispatch/
  hungarian_dispatcher.py
  rl_env.py
  rl_reposition_maintainer.py

* 덮어쓰기 전에 위 파일들 백업해두세요.
* 새 인자는 전부 기본값 None이라, 빠뜨린 호출부가 있어도 예전처럼 직접 조회합니다.
