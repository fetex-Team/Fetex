"""근거 기반 수요 설정 (docs/design/08_evidence_register.md) 검증."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demand_model as dm  # noqa: E402


def test_grid250_code_matches_seoul_format():
    # 강남역 사거리(37.49799, 127.02770)는 250m 격자 다사58254425 (500m 격자 다사116088 안)
    x, y = dm.latlng_to_utmk(37.49799, 127.02770)
    assert dm.grid250_code_of(x, y) == "다사58254425"
    assert dm.grid_code_of(x, y) == "다사116088"    # 같은 점의 500m 격자: 250m 원점 58,250/44,250m -> 116/88번 칸


def test_day_type():
    assert dm.day_type_of(dt.date(2026, 10, 14)) == "weekday"      # 수
    assert dm.day_type_of(dt.date(2026, 10, 24)) == "saturday"     # 토
    assert dm.day_type_of(dt.date(2026, 10, 18)) == "sunday"       # 일
    assert dm.day_type_of(dt.date(2026, 10, 9), is_holiday=True) == "sunday"    # 평일 공휴일
    assert dm.day_type_of(dt.date(2026, 10, 24), is_holiday=True) == "saturday"  # 주말을 공휴일로 넘겨도 토요일


def test_living_pop_loader(tmp_path):
    p = tmp_path / "lp.csv"
    p.write_text("date,hour,adm_code,grid250,pop\n"
                 "20261026,09,1,다사58004425,100\n"      # 월
                 "20261026,09,2,다사58004425,50\n"       # 같은 격자 다른 행정동 -> 합산
                 "20261027,09,1,다사58004425,250\n"      # 화
                 "20261031,09,1,다사58004425,*\n"        # 토, 비식별 -> 0
                 "20261101,09,1,다사58004425,40\n", encoding="utf-8")
    lp = dm.load_living_pop(str(p))
    assert lp[("weekday", 9)]["다사58004425"] == 200.0     # (150 + 250) / 2일
    assert lp[("saturday", 9)]["다사58004425"] == 0.0
    assert lp[("sunday", 9)]["다사58004425"] == 40.0


def test_real_files_cover_all_day_types():
    lp = dm.load_living_pop()
    for t in dm.DAY_TYPES:
        for h in range(24):
            assert lp[(t, h)], (t, h)


def test_dow_flat_option_is_validated():
    import pytest
    meta = {"edges": [], "edge_latlng": {}, "config": {}}
    with pytest.raises(ValueError):
        dm.RealDemandModel(meta, {"real_dow_factors": "nope"})
    with pytest.raises(ValueError):
        dm.RealDemandModel(meta, {"real_spatial_weights": "nope"})
