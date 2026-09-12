# 합성 데이터 규격

`generate.py`는 `build_env.py`에서 만든 도로/H3 매핑을 입력으로 호출과 외부 관측을 생성한다. 실제 카카오모빌리티·기상청·교통량 기록이 아니다.

- 호출: `request_id, pickup_datetime, latitude, longitude, h3_index, from_edge, to_edge`
- 외부 관측: `time_bucket, h3_index, temperature, precipitation, traffic_index, event_flag, is_holiday`
- 시간은 시나리오 지역의 naive local time이며 5분 단위. 다른 타임존 자료는 입력 전 통일해야 한다.
- GPS는 도로 대표점이다. 실제 OSM은 투영 좌표를 역변환하고 합성 격자는 지역 bbox로 선형 매핑한다. 도로 하나를 H3 셀 하나로 취급하는 근사이다.
- `synthetic_rate`: 셀당 호출 강도. 날짜/공간별 피크 및 요일/강수/이벤트 가중치를 가진 포아송 과정이다.
- `is_holiday`: 합성 시나리오의 8월 15일 플래그이며 공식 전체 공휴일 달력이 아니다.
- 날짜별 독립 난수 발생기를 사용하며 seed 0도 유효하다. 생성 기간을 바꿔도 같은 날짜의 호출은 같다.
- 학습 생성 자료는 `generated/calls.csv`, `generated/external.csv`로 저장하며 대용량 파일은 Git에서 제외한다.

실제 자료를 넣을 때는 GPS 유효성·타임존·중복 호출 ID를 먼저 검사하고, 전체 시간×셀 범위를 지정해 0수요를 보존한 뒤 외부 자료를 시간/H3로 결합한다. 미래값으로 결측치를 역방향 보간하지 않는다.
