"""
차량 TraCI 구독 공용 관리.

왜 필요한가
- 택시 매니저가 매 스텝 택시 50대마다 getPersonIDList / getRoadID / getRoute / getRouteIndex를 따로 물어서
  시뮬레이션 시간의 약 70%가 TraCI 왕복에 쓰였다(cProfile, 30분 구간 62초 중 43초).
- 구독(subscribe)을 걸면 SUMO가 simulationStep 응답에 값을 같이 실어 보내서, 스텝당 왕복 1번으로 끝난다.
- traci.vehicle.subscribe는 같은 차량에 다시 걸면 변수 목록을 **덮어쓴다.** 그래서 Unity 기록기와 택시 매니저가
  서로의 구독을 지우지 않도록, 차량별로 필요한 변수의 합집합을 여기서 관리한다.

값의 시점
- 구독 값은 직전 simulationStep 끝의 상태다. 차량 위치·탑승 승객·현재 도로는 스텝 사이에만 바뀌므로 그대로 정확하다.
  경로(route)는 같은 스텝 안의 changeTarget·dispatchTaxi로 바뀔 수 있어, 쓰는 쪽에서 그 점을 고려해야 한다
  (택시 매니저는 스텝 맨 앞에서 실행되고, 배차된 택시는 getStops를 직접 물어 먼저 걸러낸다).
"""
import traci

_VARS: dict[str, frozenset] = {}


def ensure(vehicle_ids, variables) -> None:
    """vehicle_ids 각각에 variables 구독을 보장한다(이미 있는 변수와 합친다)."""
    want = frozenset(variables)
    for vid in vehicle_ids:
        have = _VARS.get(vid)
        if have is not None and want <= have:
            continue
        merged = want | (have or frozenset())
        try:
            traci.vehicle.subscribe(vid, sorted(merged))
            _VARS[vid] = merged
        except traci.exceptions.TraCIException:
            continue


def get(vid) -> dict:
    """구독 결과 {변수: 값} (TraCI 왕복 없음). 없으면 빈 dict."""
    return traci.vehicle.getSubscriptionResults(vid) or {}


def all_results() -> dict:
    return traci.vehicle.getAllSubscriptionResults()


def forget(alive_ids) -> None:
    """사라진 차량의 기록 정리 (SUMO는 사라진 차량의 구독을 스스로 지운다)."""
    for vid in [v for v in _VARS if v not in alive_ids]:
        del _VARS[vid]


def reset() -> None:
    """새 시뮬레이션(traci.start) 전에 호출."""
    _VARS.clear()
