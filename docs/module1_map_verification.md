# [역할 1번] 지역·지도·외부데이터 검증 보고서

담당: 이동규 / 작성: 2026-09-12 / 대상 브랜치: `ldk`

카톡(2026-09-06) 역할 분담의 1번 항목 세 가지를 검증한 결과다.

| # | 검증 항목 | 결과 |
|---|---|---|
| A | `geo_lookup.py` 좌표 조회 실패 시 `REGION_PRESETS` 폴백 | **정상** (프리셋 5개 지역 전부) — 단, 프리셋 없는 지역에서 결함 2건 |
| B | `weather_lookup.py` 실패 시 `temp_min/max` 폴백 | **정상** (성공·실패 4개 조합 전부) |
| C | `real_map_fetch.py` OSM 변환 경고 중 실제 영향 있는 것 선별 | **완료** — 258건 중 84건(33%)이 실제 영향 |

검증 스크립트를 `tools/`에 넣어 두었으므로 누구나 재현할 수 있다.

---

## A. 좌표 API 폴백 검증

### 방법

실제 API를 호출하지 않고 `geo_lookup.lookup_region`을 강제로 성공·실패시켜
`load_config()` 결과를 비교했다. 네트워크 상태와 무관하게 같은 결과가 나온다.

```
python tools/verify_region_fallback.py
```

### 결과 — 폴백 자체는 정상

| 요청 지역 | 최종 region | lat_min | temp_min~max | 판정 |
|---|---|---|---|---|
| 강남역 | 강남역 | 37.495 | 15.0~25.0 | PASS |
| 홍대입구 | 홍대입구 | 37.550 | 15.0~25.0 | PASS |
| 여의도 | 여의도 | 37.520 | 14.0~24.0 | PASS |
| 제주공항 | 제주공항 | 33.505 | 18.0~28.0 | PASS |
| NYC(맨해튼) | NYC(맨해튼) | 40.755 | 5.0~20.0 | PASS |

프리셋에 있는 지역은 API가 죽어도 좌표·온도가 정확히 복구된다.

### 결함 A-1 — 프리셋 없는 지역이 조용히 다른 도시로 바뀜 (심각)

`config_loader._apply_region_coords()` 마지막 분기:

```python
else:
    print(f"[안내] '{region}' 프리셋 없음 — 홍대입구로 대체합니다.")
    merged.update(REGION_PRESETS["홍대입구"]); merged["region"] = "홍대입구"
```

"부산 해운대"를 넣고 API가 실패하면 **서울 홍대 지도로 시뮬레이션이 돌아간다.**
콘솔에 한 줄 안내가 나가지만 `config_gui.py`로 실행하면 사용자는 보지 못한다.
결과 CSV에는 그냥 정상 데이터로 보이고, 나중에 "강남역 데이터인 줄 알았는데 홍대였다"가 된다.

- 영향 받는 사람: 프리셋 5개 외 지역을 실험하는 전원
- 권고: 대체하지 말고 예외를 던지거나, 최소한 GUI 상단에 경고를 띄운다.

### 결함 A-2 — 프리셋 없는 지역의 온도가 서울 기본값으로 고정

같은 함수의 좌표 성공 분기:

```python
temp = REGION_PRESETS.get(region, {})
merged["temp_min"] = temp.get("temp_min", 15.0); merged["temp_max"] = temp.get("temp_max", 25.0)
```

좌표는 Nominatim으로 제대로 받아왔는데, 온도만 프리셋에 없어서 `15.0~25.0`이 박힌다.
날씨 API가 성공하면 덮이지만 **날씨만 실패하면 부산 좌표에 서울 온도**가 붙는다.

검증 결과: 요청 `부산 해운대` → 좌표 35.155(실측 정상) / 온도 15.0~25.0(서울 기본값).

- 권고: 프리셋에 없으면 온도를 `None`으로 두고, 날씨 API 실패 시 온도 피처를 쓰지 않도록 표시한다.

### 결함 A-3 — API 무응답 시 실행이 최대 15초 멈춤

응답하지 않는 서버를 세워 실측했다.

| 함수 | 코드상 timeout | 실측 지연 |
|---|---|---|
| `geo_lookup.lookup_region` | 10초 | **10.0초** |
| `weather_lookup.get_current_weather` | 5초 | **5.0초** |
| `geo_lookup.search_suggestions` (GUI 자동완성) | 5초 | **5.0초** |

`load_config()` 한 번에 좌표+날씨 = 최대 15초. 그리고 `config_loader`는
모듈 import 시점에 `CFG = load_config()`를 실행하므로 **import만 해도 네트워크를 탄다.**
`build_env.py`는 `--config-path`를 받으면 `load_config()`를 한 번 더 호출하므로
프로세스당 2회다. `parallel_dispatch_orchestrator.py`처럼 워커를 N개 띄우면 N배가 된다.

- 권고: timeout을 3초로 줄이고, 실패한 지역명을 프로세스 내에서 캐시해 재시도를 막는다.

### 결함 A-4 — 격자 슬라이더를 건드리면 좌표 캐시가 무효화됨

`region_cache.json`의 키는 `지역명::m<반경>` 형식이고, 반경은
`max(grid_x*grid_length, grid_y*grid_length)/2`로 계산된다.

| grid_length | 계산된 반경 | 캐시 키 | 결과 |
|---|---|---|---|
| 100 | 500.0 | `강남역::m500.0` | 적중 |
| 150 | 750.0 | `강남역::m750.0` | **미스 → API 재호출** |
| 200 | 1000.0 | `강남역::m1000.0` | 적중 |

GUI에서 격자 크기를 조금만 바꿔도 Nominatim을 다시 부른다.
Nominatim은 초당 1회 정책이고 코드에도 `time.sleep(1.0)`이 들어 있다.

또한 현재 `region_cache.json`에는 반경 없는 레거시 키 `"강남역"`, `"홍대입구"` 2개가
남아 있는데, 조회 코드가 `::` 붙은 키만 찾으므로 **영원히 사용되지 않는다.**

- 권고: 캐시 조회를 2단계로 한다. 정확한 반경 키가 없으면 같은 지역명의 다른 반경 키에서
  중심점을 꺼내 반경만 다시 계산한다. Nominatim 재호출이 사라진다.

---

## B. 날씨 API 폴백 검증

좌표 API 성공·실패 × 날씨 API 성공·실패 네 가지 조합을 전부 확인했다.

| 지역 | 좌표 API | 날씨 API | temp_min~max | current_temperature | 판정 |
|---|---|---|---|---|---|
| 강남역 | 성공 | 성공(31.4°C) | 29.4~33.4 | 31.4 | PASS |
| 강남역 | 성공 | 실패 | 15.0~25.0 (프리셋) | 없음 | PASS |
| 강남역 | 실패 | 성공(31.4°C) | 29.4~33.4 | 31.4 | PASS |
| 강남역 | 실패 | 실패 | 15.0~25.0 (프리셋) | 없음 | PASS |
| 제주공항 | 실패 | 실패 | 18.0~28.0 (프리셋) | 없음 | PASS |

폴백 로직 자체에는 결함이 없다. 날씨 API가 성공하면 `현재기온 ±2도`로 범위를 만들고,
실패하면 `_apply_live_weather`가 예외를 삼키고 프리셋 온도가 그대로 남는다.

### 참고 — 이 온도가 어디에 쓰이는지

`module2_preprocessing/external_data_merge.py`가 `temp_min~temp_max` 구간에서
`np.random.uniform`으로 행마다 온도를 뽑는다. 즉 **폴백이 정상 동작해도
날씨 피처는 난수**다. 이 부분은 3번(전처리/EDA) 담당 항목이므로
별도 문서 `docs/weather_coefficient.md`에 실측 기반 대안을 정리해 두었다.

---

## C. netconvert 경고 분류

### 방법

`real_map_fetch.py`와 동일한 옵션으로 실제 변환을 수행하고 stderr를 분류했다.
대상은 `module1_simulation/sumo_config/region.osm.xml` (강남역, 3.4MB).

```
python tools/analyze_netconvert_warnings.py nc_stderr.txt
```

netconvert는 같은 유형을 5회까지만 출력하고 마지막에 총계를 알려주므로,
겉보기 92줄이지만 **실제 발생은 258건(26종)**이다.

### 분류 결과

| 판정 | 건수 | 비율 |
|---|---|---|
| ■ 시뮬레이션에 실제 영향 | **84** | 33% |
| ▲ 영향 경미 | 14 | 5% |
| · 무시 가능 | 160 | 62% |
| ? 미분류 | 0 | 0% |

### ■ 실제 영향 있는 것 — 두 종류뿐

**1) 회전 금지(turn restriction) 무시 — 60건**

```
Ignoring restriction relation 'X'.                              17회
Ignoring restriction relation 'X' with unknown to-way.          13회
Ignoring restriction relation 'X' with unknown from-way.        13회
from-edge 'X' of restriction relation could not be determined   11회
to-edge 'X' of restriction relation could not be determined      6회
```

OSM의 "여기서 좌회전 금지" 같은 규칙이 도로망에 반영되지 않는다.
원인은 bbox로 지도를 잘라 받아서 relation이 참조하는 way 한쪽이 범위 밖에 있는 것이다.
결과적으로 **실제로는 불가능한 회전을 차량과 택시가 수행**한다.
경로가 실제보다 짧아지고, 배차 후 픽업 소요시간이 낙관적으로 나온다.

**2) 비보호 좌회전 속도 과대 — 24건**

```
Minor green from edge 'X' to edge 'X' exceeds N m/s.
Maybe a left-turn lane is missing.
```

좌회전 전용 차선 정보가 없어 비보호 좌회전을 직진과 같은 속도로 통과하도록 설정된다.
**교차로 지연이 실제보다 짧게 나온다.**

두 경고 모두 같은 방향으로 작용한다 — 시뮬레이션의 통행 시간이 현실보다 짧다.
`measure_wait_time.py`로 뽑는 평균 대기시간의 절대값을 현실과 비교하면 안 되고,
**patrol vs prepositioned 같은 상대 비교로만 써야 한다.** 이 단서를 REPORT.md에 넣어야 한다.

### · 무시해도 되는 것 — 160건

- **대중교통 정류장·노선 관련 127건** (`pt stop`, `pt line`, `Removed invalid stop` 등)
  이 프로젝트는 SUMO에서 버스·지하철을 운행시키지 않는다. 승객은 택시와 도보만 쓴다.
  `poi_extractor.py`도 버스정류장·지하철입구를 **OSM 원본 노드에서 직접 파싱**하므로
  netconvert가 pt stop을 버려도 구역 배정에는 영향이 없다.
- **bbox 밖 relation 참조 33건** (`No node/way found for reference`)
  지도를 사각형으로 잘라 받았으니 경계에서 반드시 발생한다. 정상이다.

### ▲ 경미 14건

램프 생성 실패 2건, 교차로 형상 보정 3건, 급격한 굴절 3건, 신호등 관련 2건 등.
통행 자체는 가능하다.

---

## D. 부수적으로 확인한 것

### D-1. `filter_osm_data()`는 실제로 도로를 늘린다

철도·수로·항공 27개 제거 + 버스전용차로 13개를 일반도로로 재분류하는 전처리의 효과를 측정했다.

| | 통행 가능 edge | 최대 SCC | 고립 | 신호등 | junction | 경고 |
|---|---|---|---|---|---|---|
| 필터 없이 변환 | 382 | 371 | 11 | 23 | 171 | 104줄 |
| `filter_osm_data` 적용 | **399** | **382** | 17 | **31** | 185 | 92줄 |

버스전용차로를 `tertiary`로 되돌린 덕에 통행 가능 도로가 17개, 왕복 가능한 도로가 11개 늘었다.
이 전처리는 유지해야 한다.

### D-2. 고립 도로는 이미 처리되고 있다

변환 결과 404개 edge 중 **17개(4.3%)가 최대 SCC 밖**이다.
이 도로에서 출발하거나 도착하면 경로가 없어 `findRoute` 실패가 난다.

`build_env.py` 157행에서 `filter_to_largest_scc()`가 적용되고,
159~163행에서 **zones에도 동일한 SCC 필터를 교집합으로 적용**한다.
즉 POI 구역이 고립 도로에 잡혀도 후보에서 빠진다. **추가 조치 불필요.**

### D-3. `pyproj`가 없으면 POI 구역이 조용히 랜덤이 된다 (중요)

`poi_extractor.map_pois_to_edges()`는 `net.convertLonLat2XY()`로 좌표를 변환하는데,
이 함수는 **pyproj가 없으면 `RuntimeError`를 던진다.**

```python
try:
    x, y = net.convertLonLat2XY(lon, lat)
except Exception:
    continue          # ← 모든 좌표가 여기로 빠짐
```

전 좌표가 걸러지면 카테고리가 전부 비고, `get_zones()`가
"POI를 지도에서 찾지 못해 임의 배정으로 대체합니다"를 출력하며 **랜덤 구역으로 넘어간다.**
시뮬레이션은 정상 실행되고 승객도 생성되므로 아무도 눈치채지 못한다.

실측 비교 (강남역 지도, POI 786개 파싱됨):

| 구역 | pyproj 있음 (실제 POI) | pyproj 없음 (랜덤) |
|---|---|---|
| school | 4 | 39 |
| residential | 28 | 124 |
| company | 51 | 79 |
| restaurant | 157 | 59 |
| subway_entrance | 16 | 39 |
| bus_stop | 28 | 59 |

랜덤 쪽은 학교가 39개, 음식점이 59개로 **실제 지도와 정반대**가 된다.
이 상태로 만든 수요 로그는 Module 2·3의 전제를 무너뜨린다.

`pyproj`는 `geopandas`의 의존성이라 정상 설치되면 따라온다. 그런데
`SETUP_GUIDE.md` FAQ에 "Windows에서 geopandas 설치 에러" 항목이 이미 있다.
geopandas 설치가 실패한 채로 넘어가면 정확히 이 상태가 된다.

- 권고 1: `requirements.txt`에 `pyproj`를 명시한다 (geopandas와 무관하게 설치됨).
- 권고 2: `map_pois_to_edges()`에서 전 좌표 변환이 실패하면 조용히 넘어가지 말고
  명시적으로 경고를 띄운다. → **3번(윤세빈) 담당 파일이므로 전달 필요**

### D-4. `rtree` 미설치 시 POI 매핑이 느려진다

```
UserWarning: Module 'rtree' not available. Using brute-force fallback.
```

`net.getNeighboringEdges()`가 공간 인덱스 없이 전수 탐색한다.
POI 786개 × edge 404개 규모에서는 체감되지 않지만, 지도를 넓히면(반경 1km 이상) 눈에 띈다.
`requirements.txt`에 `rtree` 추가를 권고한다.

---

## E. 조치 우선순위

| 순위 | 항목 | 담당 | 비고 |
|---|---|---|---|
| 1 | `requirements.txt`에 `pyproj`, `rtree` 추가 | 1번 | 조용한 오작동 방지, 1줄 |
| 2 | 프리셋 없는 지역 → 홍대 대체 제거 (A-1) | 1번 | 데이터 신뢰성 |
| 3 | POI 변환 전량 실패 시 경고 (D-3 권고 2) | **3번 전달** | |
| 4 | 좌표 캐시 2단계 조회 (A-4) | 1번 | Nominatim 정책 준수 |
| 5 | timeout 10초 → 3초, 실패 지역 프로세스 캐시 (A-3) | 1번 | 병렬 실행 속도 |
| 6 | 프리셋 없는 지역 온도 하드코딩 제거 (A-2) | 1번 | |

## F. 전달 사항

- **2번(송신화)**: netconvert 경고 때문에 시뮬레이션 통행 시간이 현실보다 짧게 나온다.
  `measure_wait_time.py` 결과는 절대값이 아니라 전략 간 상대 비교로만 쓸 것.
- **3번(윤세빈)**: `pyproj` 없으면 POI 구역이 랜덤이 된다(D-3). zone 검증 전에 먼저 확인할 것.
  `external_data_merge.py`의 난수 날씨 대체안은 `docs/weather_coefficient.md` 참고.
- **5번(김준성)**: 위 C절의 "통행 시간 낙관 편향" 단서를 REPORT.md 한계 항목에 넣어야 한다.

## G. 재현 방법

```bash
# 좌표/날씨 폴백 검증 (네트워크 불필요)
python tools/verify_region_fallback.py

# netconvert 경고 분류
cd module1_simulation/sumo_config
netconvert --osm-files region_filtered.osm.xml -o grid.net.xml \
  --geometry.remove true --ramps.guess true --junctions.join true \
  --remove-edges.isolated true --ramps.no-split true --edges.join true \
  --keep-edges.by-vclass passenger --no-internal-links false \
  --tls.guess true --tls.join true  2> nc_stderr.txt
cd ../..
python tools/analyze_netconvert_warnings.py module1_simulation/sumo_config/nc_stderr.txt
```

검증 환경: Eclipse SUMO netconvert 1.27.1 / Python 3.11 / sumolib 1.27.x
