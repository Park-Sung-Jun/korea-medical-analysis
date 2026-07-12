"""
OpenRouteService isochrone API로 47개 상급종합병원의 운전 등시선을 받아
"가장 가까운 상급종합병원까지 운전 시간" 계층형 GeoJSON을 생성한다.

동작:
  1) hospitals.json 좌표 읽기
  2) ORS /v2/isochrones/driving-car 호출(최대 5개 location/요청)
  3) 밴드별로 모든 병원 등시선을 union -> coverage[band]
  4) 계층 차집합: ring(b) = coverage[b] - coverage[b-1]  (색 겹침 방지)
  5) data/isochrones.geojson + data/hospitals.geojson 저장

API 키:
  환경변수 ORS_API_KEY 또는 scripts/ors_key.txt 파일에서 읽음.
  무료 키 발급: https://openrouteservice.org/dev/#/signup

usage:
  $env:ORS_API_KEY="..."; python scripts/fetch_isochrones.py
  python scripts/fetch_isochrones.py --bands 900,1800,3600,5400
"""
import json, os, sys, time, argparse
from pathlib import Path

import requests
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
try:
    from . import _env  # noqa: F401  (.env 자동 로드 side-effect)
except ImportError:  # `python scripts/fetch_isochrones.py` 직접 실행
    import _env  # noqa: F401

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOSPITALS = ROOT / "data" / "hospitals.json"
OUT_ISO = ROOT / "data" / "isochrones.geojson"
OUT_PTS = ROOT / "data" / "hospitals.geojson"

ORS_URL = "https://api.openrouteservice.org/v2/isochrones/driving-car"
DEFAULT_BANDS = [900, 1800, 2700, 3600]   # 15 / 30 / 45 / 60 분 (ORS 무료 최대 60분)
MAX_LOCS = 5            # 무료 티어: 요청당 최대 5개 location
SLEEP_SEC = 4.0         # 20 req/min 제한 회피
SIMPLIFY_TOL = 0.0015   # 약 150m, 파일 크기 축소
RETRY = 3


def read_key():
    key = os.environ.get("ORS_API_KEY", "").strip()
    if key:
        return key
    kf = HERE / "ors_key.txt"
    if kf.exists():
        return kf.read_text(encoding="utf-8").strip()
    sys.exit("ORS API 키가 없습니다. 환경변수 ORS_API_KEY 또는 scripts/ors_key.txt 를 설정하세요.\n"
             "무료 발급: https://openrouteservice.org/dev/#/signup")


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def fetch_chunk(key, locations, bands):
    body = {
        "locations": locations,
        "range": bands,
        "range_type": "time",
        "location_type": "start",
        "attributes": ["area"],
        "smoothing": 15,
    }
    headers = {"Authorization": key, "Content-Type": "application/json"}
    for attempt in range(1, RETRY + 1):
        r = requests.post(ORS_URL, json=body, headers=headers, timeout=120)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            wait = SLEEP_SEC * attempt * 2
            print(f"  429 rate-limit, {wait:.0f}s 대기 후 재시도({attempt}/{RETRY})")
            time.sleep(wait)
            continue
        # 400 등은 본문 출력 후 종료
        raise SystemExit(f"ORS 오류 {r.status_code}: {r.text[:500]}")
    raise SystemExit("재시도 초과(429).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bands", default=",".join(map(str, DEFAULT_BANDS)),
                    help="초 단위 등시선 밴드, 쉼표 구분 (오름차순)")
    ap.add_argument("--hospitals", type=Path, default=HOSPITALS)
    ap.add_argument("--out-iso", type=Path, default=OUT_ISO)
    ap.add_argument("--out-points", type=Path, default=OUT_PTS)
    ap.add_argument("--expect-hospitals", type=int, default=None)
    args = ap.parse_args()
    bands = sorted(int(x) for x in args.bands.split(","))

    key = read_key()
    doc = json.loads(args.hospitals.read_text(encoding="utf-8"))
    hospitals = doc["hospitals"]
    if args.expect_hospitals is not None and len(hospitals) != args.expect_hospitals:
        raise SystemExit(f"병원 수 불일치: {len(hospitals)} != {args.expect_hospitals}")
    locations = [[h["lon"], h["lat"]] for h in hospitals]
    print(f"병원 {len(hospitals)}개, 밴드(분): {[b//60 for b in bands]}")

    # band(sec) -> list[shapely polygon]
    polys_by_band = {b: [] for b in bands}
    n_req = 0
    for chunk in chunks(locations, MAX_LOCS):
        n_req += 1
        print(f"[{n_req}] 요청: {len(chunk)} locations ...")
        data = fetch_chunk(key, chunk, bands)
        for feat in data["features"]:
            val = int(round(feat["properties"]["value"]))
            # value가 정확히 밴드와 안 맞으면 가장 가까운 밴드에 매핑
            band = min(bands, key=lambda b: abs(b - val))
            polys_by_band[band].append(shape(feat["geometry"]).buffer(0))
        time.sleep(SLEEP_SEC)

    # 밴드별 union (누적 coverage)
    coverage = {}
    for b in bands:
        if polys_by_band[b]:
            coverage[b] = unary_union(polys_by_band[b]).buffer(0)
        else:
            coverage[b] = None
            print(f"  경고: {b}s 밴드에 폴리곤 없음")

    # 작은 밴드가 큰 밴드에 포함되도록 누적(상위 = 하위 ∪ 자기 자신)
    cum = {}
    acc = None
    for b in bands:
        cov = coverage[b]
        acc = cov if acc is None else (unary_union([acc, cov]) if cov is not None else acc)
        cum[b] = acc

    # 계층 차집합: ring(b) = cum[b] - cum[b-1]
    features = []
    prev = None
    for b in bands:
        cur = cum[b]
        if cur is None:
            continue
        ring = cur if prev is None else cur.difference(prev)
        ring = ring.simplify(SIMPLIFY_TOL, preserve_topology=True)
        if not ring.is_empty:
            features.append({
                "type": "Feature",
                "properties": {"seconds": b, "minutes": b // 60, "band_index": bands.index(b)},
                "geometry": mapping(ring),
            })
        prev = cur

    fc = {
        "type": "FeatureCollection",
        "meta": {"bands_sec": bands, "bands_min": [b // 60 for b in bands],
                 "source": "OpenRouteService driving-car isochrones",
                 "hospitals": len(hospitals),
                 "hospital_source": doc.get("meta", {}).get("source"),
                 "hospital_period": doc.get("meta", {}).get("period")},
        "features": features,
    }
    args.out_iso.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {args.out_iso}  (features={len(features)})")

    # 병원 포인트 GeoJSON
    pts = {
        "type": "FeatureCollection",
        "meta": {"source": doc.get("meta", {}).get("source"),
                 "source_url": doc.get("meta", {}).get("source_url"),
                 "period": doc.get("meta", {}).get("period"),
                 "count": len(hospitals)},
        "features": [{
            "type": "Feature",
            "properties": {"id": h["id"], "name": h["name"], "region": h["region"], "sido": h["sido"]},
            "geometry": {"type": "Point", "coordinates": [h["lon"], h["lat"]]},
        } for h in hospitals],
    }
    args.out_points.write_text(json.dumps(pts, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {args.out_points}  (points={len(hospitals)})")
    print("완료.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
