"""
회전 금지(turn restriction) 규칙이 SUMO 도로망에 왜 빠지는지 분석.

    python tools/analyze_turn_restrictions.py
    (module1_simulation/sumo_config/region_filtered.osm.xml 기준, netconvert 필요)

예전 진단(9/12)은 "지도를 영역 크기로 잘라 받아서 relation이 참조하는 도로가 범위 밖"이라고 봤다.
이 도구로 다시 확인한 결과 (2026-11, SUMO 1.28):
- OSM 회전 금지 relation 44개 중 21개는 OSM 데이터 자체에 from/to 도로가 빠져 있음 (삭제·분할된 도로 참조)
- 멤버가 완전한 23개 중 17개도 netconvert 옵션과 무관하게 적용되지 않음 (기본 옵션으로 변환해도 동일)
- 지도를 영역보다 넓게 받아 변환한 뒤 자르는 방식으로도 영역 안 규칙 27개 중 25개가 그대로 무시됨
=> 범위 문제는 원인이 아니다. 지도 범위 확장은 효과가 없어 적용하지 않았다.
"""
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OSM = os.path.join(ROOT, "module1_simulation", "sumo_config", "region_filtered.osm.xml")
PROJECT_OPTS = ["--geometry.remove", "true", "--ramps.guess", "true", "--junctions.join", "true",
                "--remove-edges.isolated", "true", "--ramps.no-split", "true", "--edges.join", "true",
                "--keep-edges.by-vclass", "passenger", "--no-internal-links", "false",
                "--tls.guess", "true", "--tls.join", "true"]


def restrictions(osm_path):
    root = ET.parse(osm_path).getroot()
    ways = {w.get("id") for w in root.findall("way")}
    rels = {}
    for rel in root.findall("relation"):
        tags = {t.get("k"): t.get("v") for t in rel.findall("tag")}
        if tags.get("type") != "restriction":
            continue
        roles = {m.get("role"): m.get("ref") for m in rel.findall("member")}
        complete = all(k in roles for k in ("from", "to", "via")) and roles["from"] in ways and roles["to"] in ways
        rels[rel.get("id")] = {"kind": tags.get("restriction"), "complete": complete}
    return rels


def ignored_ids(osm_path, opts):
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(["netconvert", "--osm-files", osm_path, "-o", os.path.join(tmp, "n.net.xml"),
                            "--aggregate-warnings", "-1", *opts], capture_output=True, text=True)
    return set(re.findall(r"restriction relation '(\d+)'", p.stderr))


def main():
    osm = sys.argv[1] if len(sys.argv) > 1 else OSM
    rels = restrictions(osm)
    complete = {k for k, v in rels.items() if v["complete"]}
    print(f"OSM 회전 금지 relation {len(rels)}개 | 멤버(from/to/via) 완전 {len(complete)}개 | "
          f"OSM 자체에서 멤버 누락 {len(rels) - len(complete)}개")
    for label, opts in (("프로젝트 옵션", PROJECT_OPTS),
                        ("netconvert 기본 옵션", ["--keep-edges.by-vclass", "passenger", "--no-internal-links", "false"])):
        ign = ignored_ids(osm, opts)
        print(f"[{label}] 무시됨 {len(ign & set(rels))}개 (그중 멤버 완전한데 무시 {len(ign & complete)}개)")


if __name__ == "__main__":
    main()
