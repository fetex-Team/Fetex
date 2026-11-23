"""메인 루프 최상단에서 스텝당 한 번 부르는 맵 전체 상태 조회 헬퍼.

person/vehicle ID 목록은 루프에서 직접 set(traci...getIDList())로 가져오고,
예외 처리가 필요한 택시 플릿 조회만 여기서 처리한다.
"""
import traci


def fetch_idle_taxis() -> list:
    """완전히 빈 택시(getTaxiFleet(0)) 목록. 실패하면 빈 리스트."""
    try:
        return list(traci.vehicle.getTaxiFleet(0))
    except traci.exceptions.TraCIException:
        return []
