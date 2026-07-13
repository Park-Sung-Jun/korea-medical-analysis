"""
Nominatim(OSM) 지오코딩으로 hospitals.json 시드 좌표 검증/보정.
- 각 병원 query를 Nominatim에 조회(1 req/sec, User-Agent 필수)
- 시드 좌표와 거리 비교, 임계값(기본 8km) 초과 시 outlier로 플래그
- --apply 옵션 주면 Nominatim 결과(한국 영역 내, 임계값 이내)를 좌표로 갱신

usage:
  python verify_coords.py            # 검증만, 리포트 출력
  python verify_coords.py --apply    # Nominatim 좌표로 보정 적용
"""
import json, sys, time, math, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "hospitals.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# 대한민국 대략 bounding box (제주 포함)
KR_BBOX = (33.0, 39.6, 124.5, 132.0)  # latmin, latmax, lonmin, lonmax
THRESH_KM = 8.0
APPLY = "--apply" in sys.argv


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def geocode(query):
    params = urllib.parse.urlencode({
        "q": query, "format": "json", "limit": 1,
        "countrycodes": "kr", "addressdetails": 0,
    })
    req = urllib.request.Request(
        f"{NOMINATIM}?{params}",
        headers={"User-Agent": "korea-medical-analysis-hospital-verify/1.0 (personal research)"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)
    if not rows:
        return None
    return float(rows[0]["lat"]), float(rows[0]["lon"])


def in_korea(lat, lon):
    la0, la1, lo0, lo1 = KR_BBOX
    return la0 <= lat <= la1 and lo0 <= lon <= lo1


def main():
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    hospitals = doc["hospitals"]
    flagged, applied = [], 0
    for h in hospitals:
        try:
            g = geocode(h["query"])
        except Exception as e:
            print(f"[ERR ] {h['id']:>2} {h['name']}: {e}")
            time.sleep(1.1)
            continue
        if g is None:
            print(f"[MISS] {h['id']:>2} {h['name']}: Nominatim 결과 없음 -> 시드 유지")
            time.sleep(1.1)
            continue
        glat, glon = g
        d = haversine(h["lat"], h["lon"], glat, glon)
        ok_kr = in_korea(glat, glon)
        tag = "OK  "
        if d > THRESH_KM or not ok_kr:
            tag = "FLAG"
            flagged.append((h, glat, glon, d, ok_kr))
        print(f"[{tag}] {h['id']:>2} {h['name']:<22} seed=({h['lat']:.4f},{h['lon']:.4f}) "
              f"osm=({glat:.4f},{glon:.4f}) d={d:5.2f}km kr={ok_kr}")
        if APPLY and ok_kr and d <= THRESH_KM:
            h["lat"], h["lon"] = round(glat, 5), round(glon, 5)
            applied += 1
        time.sleep(1.1)

    print("\n=== 요약 ===")
    print(f"총 {len(hospitals)}개, FLAG {len(flagged)}개")
    for h, glat, glon, d, ok_kr in flagged:
        print(f"  - {h['id']} {h['name']}: d={d:.1f}km kr={ok_kr} osm=({glat:.4f},{glon:.4f})")
    if APPLY:
        doc["meta"]["geocoded"] = True
        DATA.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n적용: {applied}개 좌표를 Nominatim 값으로 갱신, 파일 저장.")
    else:
        print("\n(검증 모드. 보정하려면 --apply)")


if __name__ == "__main__":
    main()
