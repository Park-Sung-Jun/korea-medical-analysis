"""
병원별(개별) 운전 등시선을 사전계산해 data/hospital_isochrones.geojson 생성.
union 하지 않고 병원마다 15/30/45/60분 밴드를 따로 저장 → '병원 선택 시 그 병원 커버리지'를
런타임 ORS 호출 없이 즉시 표시(공개 배포에서도 키 불필요).

usage: $env:ORS_API_KEY=...; python scripts/fetch_hospital_isochrones.py
"""
import json, os, time
from pathlib import Path
import requests
from shapely.geometry import shape, mapping
import _env  # noqa: F401  (.env 자동 로드 side-effect)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOSP = ROOT / "data" / "hospitals.json"
OUT = ROOT / "data" / "hospital_isochrones.geojson"
URL = "https://api.openrouteservice.org/v2/isochrones/driving-car"
BANDS = [900, 1800, 2700, 3600]
MAXLOC = 5
SLEEP = 4.0
TOL = 0.004   # 약 400m 단순화


def key():
    k = os.environ.get("ORS_API_KEY", "").strip()
    return k or (HERE / "ors_key.txt").read_text(encoding="utf-8").strip()


def main():
    k = key()
    hosp = json.loads(HOSP.read_text(encoding="utf-8"))["hospitals"]
    feats = []
    for i in range(0, len(hosp), MAXLOC):
        chunk = hosp[i:i + MAXLOC]
        body = {"locations": [[h["lon"], h["lat"]] for h in chunk],
                "range": BANDS, "range_type": "time", "smoothing": 10}
        r = requests.post(URL, json=body,
                          headers={"Authorization": k, "Content-Type": "application/json"}, timeout=120)
        if r.status_code != 200:
            raise SystemExit(f"ORS {r.status_code}: {r.text[:200]}")
        for f in r.json()["features"]:
            gi = f["properties"].get("group_index", 0)
            h = chunk[gi]
            val = int(round(f["properties"]["value"]))
            band = min(BANDS, key=lambda b: abs(b - val))
            geom = shape(f["geometry"]).buffer(0).simplify(TOL, preserve_topology=True)
            feats.append({"type": "Feature",
                          "properties": {"hosp_id": h["id"], "hosp_name": h["name"],
                                         "sido": h.get("sido", ""), "minutes": band // 60},
                          "geometry": mapping(geom)})
        print(f"  {i+len(chunk)}/{len(hosp)} 병원 처리")
        time.sleep(SLEEP)

    # 병원마다 큰 밴드가 아래로 그려지도록 minutes 내림차순 정렬
    feats.sort(key=lambda f: -f["properties"]["minutes"])
    fc = {"type": "FeatureCollection",
          "meta": {"bands_min": [b // 60 for b in BANDS], "per_hospital": True,
                   "hospitals": len(hosp)},
          "features": feats}
    OUT.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {OUT} (features={len(feats)}, {OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
