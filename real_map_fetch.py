import os
import sys
import urllib.request
import subprocess
import xml.etree.ElementTree as ET
import tempfile
import time

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def fetch_osm_data(lat_min, lat_max, lng_min, lng_max, output_path):
    """Overpass API / OpenStreetMap API를 사용하여 위경도 영역 OSM 데이터 다운로드"""
    url = f"https://api.openstreetmap.org/api/0.6/map?bbox={lng_min},{lat_min},{lng_max},{lat_max}"
    print(f"[안내] OSM 실제 지도 데이터 다운로드 중... (bbox={lng_min},{lat_min},{lng_max},{lat_max})")
    
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        payload = response.read()

    # Windows에서 기존 region.osm.xml을 직접 'wb'로 열면, Explorer 미리 보기나
    # 백신의 순간 검사 때문에 간헐적으로 [Errno 22] Invalid argument가 난다.
    # 먼저 같은 폴더의 임시 파일에 완성본을 쓴 뒤 교체하면 실패해도 기존 지도는 보존된다.
    output_dir = os.path.dirname(os.path.abspath(output_path))
    fd, temp_path = tempfile.mkstemp(prefix="region_download_", suffix=".osm.xml", dir=output_dir)
    try:
        with os.fdopen(fd, 'wb') as out_file:
            out_file.write(payload)
        for attempt in range(3):
            try:
                os.replace(temp_path, output_path)
                break
            except OSError as exc:
                if attempt == 2:
                    raise OSError(
                        f"OSM 파일을 교체할 수 없습니다: {output_path}. "
                        "SUMO/Explorer에서 이 파일을 열었다면 닫은 뒤 다시 시도하세요."
                    ) from exc
                time.sleep(0.5)
    finally:
        # os.replace 성공 뒤에는 temp_path가 사라지고, 실패한 경우에만 정리한다.
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    
    file_size_kb = os.path.getsize(output_path) / 1024
    print(f"[완료] OSM 원본 저장: {output_path} ({file_size_kb:.1f} KB)")


def filter_osm_data(input_osm, output_osm):
    """불필요한 OSM 요소(철도, 수로, 버스전용차로 제한 등) 정제"""
    tree = ET.parse(input_osm)
    root = tree.getroot()

    removed_count = 0
    busway_fixed = 0

    for way in list(root.findall('way')):
        tags = {tag.attrib['k']: tag.attrib['v'] for tag in way.findall('tag')}
        
        # 1. 철도/수로/항공 관련 요소 제거
        if any(k in tags for k in ['railway', 'waterway', 'aeroway']):
            root.remove(way)
            removed_count += 1
            continue

        # 2. 버스전용차로(busway) 태그 정제 (일반 차량 통행 가능하도록 보정)
        if tags.get('access') in ['no', 'bus'] or tags.get('highway') == 'busway':
            for tag in way.findall('tag'):
                if tag.attrib['k'] == 'access':
                    tag.attrib['v'] = 'yes'
                if tag.attrib['k'] == 'highway' and tag.attrib['v'] == 'busway':
                    tag.attrib['v'] = 'tertiary'
            busway_fixed += 1

    tree.write(output_osm, encoding='utf-8', xml_declaration=True)
    print(f"[안내] 철도/수로/항공 요소 {removed_count}개 제거, 버스전용차로 {busway_fixed}개 일반도로 재분류 완료 → {output_osm}")


def convert_to_sumo_net(filtered_path, net_path):
    """netconvert를 사용하여 OSM 파일을 SUMO 네트워크(.net.xml)로 변환"""
    cmd = [
        'netconvert',
        '--osm-files', filtered_path,
        '-o', net_path,
        '--geometry.remove', 'true',
        '--ramps.guess', 'true',
        '--junctions.join', 'true',
        '--remove-edges.isolated', 'true',
        '--ramps.no-split', 'true',
        '--edges.join', 'true',
        # [핵심] 차도 전용 추출 (인도/계단 제거)
        '--keep-edges.by-vclass', 'passenger',
        '--no-internal-links', 'false',
        # [핵심] 교차로 꽉 막힘 방지를 위해 주요 교차로에 신호등 자동 배치
        '--tls.guess', 'true',
        '--tls.join', 'true'
        
    ]
    print("[안내] netconvert로 SUMO 네트워크 변환 중...")
    subprocess.run(cmd, check=True)


def _cached_osm_matches(raw_osm, lat_min, lat_max, lng_min, lng_max, tol=1e-6):
    """이미 받아 둔 OSM 원본의 <bounds>가 요청 범위와 같으면 True.

    매 실행마다 OSM API에서 다시 받으면 (1) 네트워크가 없을 때 실행이 멈추고
    (2) 그 사이 OSM이 수정되면 지도(도로 수·edge id)가 바뀌어 실험 재현이 깨진다.
    그래서 같은 범위면 기존 파일을 재사용하고, FETEX_REFRESH_OSM=1일 때만 새로 받는다."""
    if os.environ.get("FETEX_REFRESH_OSM") == "1" or not os.path.exists(raw_osm):
        return False
    try:
        for _event, elem in ET.iterparse(raw_osm, events=("start",)):
            if elem.tag == "bounds":
                got = [float(elem.get(k)) for k in ("minlat", "maxlat", "minlon", "maxlon")]
                want = [lat_min, lat_max, lng_min, lng_max]
                return all(abs(a - b) <= tol for a, b in zip(got, want))
            if elem.tag in ("node", "way"):
                return False  # bounds 없이 바로 데이터가 시작되면 범위를 확인할 수 없음
    except ET.ParseError:
        return False
    return False


def build_real_map_network(lat_min, lat_max, lng_min, lng_max, output_dir):
    """전체 매핑 파이프라인 수행.
    반환값이 (net_path, raw_osm_path) 튜플로 바뀌었습니다.
    raw_osm_path(필터링 전 원본)는 poi_extractor.py가 학교/주택/회사/음식점/
    지하철입구/버스정류장 태그를 파싱하는 데 사용합니다 — filter_osm_data()를
    거치면 도로 외 요소가 일부 정리되므로, POI 파싱은 반드시 필터링 전 원본으로 합니다."""
    os.makedirs(output_dir, exist_ok=True)
    
    raw_osm = os.path.join(output_dir, "region.osm.xml")
    filtered_osm = os.path.join(output_dir, "region_filtered.osm.xml")
    net_path = os.path.join(output_dir, "grid.net.xml")

    if _cached_osm_matches(raw_osm, lat_min, lat_max, lng_min, lng_max):
        print(f"[안내] 같은 범위의 OSM 원본이 이미 있어 재사용합니다: {raw_osm} "
              "(새로 받으려면 환경변수 FETEX_REFRESH_OSM=1)")
    else:
        # 다른 설정 폴더(sumo_config, sumo_config_A ...)에 같은 범위 원본이 있으면 복사해서 쓴다
        import glob
        import shutil
        sibling = next((p for p in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(output_dir)),
                                                                 "*", "region.osm.xml")))
                        if os.path.abspath(p) != os.path.abspath(raw_osm)
                        and _cached_osm_matches(p, lat_min, lat_max, lng_min, lng_max)), None)
        if sibling:
            shutil.copyfile(sibling, raw_osm)
            print(f"[안내] 같은 범위의 OSM 원본을 다른 설정 폴더에서 복사해 재사용합니다: {sibling}")
        else:
            fetch_osm_data(lat_min, lat_max, lng_min, lng_max, raw_osm)
    filter_osm_data(raw_osm, filtered_osm)
    convert_to_sumo_net(filtered_osm, net_path)

    return net_path, raw_osm
