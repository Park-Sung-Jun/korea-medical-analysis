"""
ORS 무료 등시선은 driving-car 기준 최대 60분(3600초)까지만 지원한다(code 3004).
그래서 60분 등시선 밖(access_min=None)인 시군구는 실제 운전시간을 알 수 없다.

이 스크립트는 그 사각지대 시군구의 대표점 -> 47개 상급종합병원 운전시간을
ORS Matrix API(driving-car)로 직접 계산해 access_min을 '실측 분'으로 채운다.

- 입력/출력: data/sigungu_bivariate.geojson (in-place 갱신)
- combine_bivariate.py 실행 후에 돌린다.
- 분류(access_class, bivar_class)는 그대로 유지된다(>60분은 여전히 B3).
  대신 access_min에 실수 분값이 들어가고, access_min_exact=True, access_band(60~90/90~120/>120) 부여.

usage:
  python scripts/fill_access.py
"""
import json, time
from pathlib import Path
import requests
from shapely.geometry import shape
import _env  # noqa: F401  (.env 자동 로드 side-effect)

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
BIV = DATA / "sigungu_bivariate.geojson"
HOSP = DATA / "hospitals.json"
MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"
SLEEP = 1.8     # 40 req/min 제한 회피
RETRY = 3
# 도로 연결 없는 섬 등에서 ORS가 과대값을 줄 수 있어 상한선 플래그(분)
SANE_MAX_MIN = 300


def read_key():
    import os
    k = os.environ.get("ORS_API_KEY", "").strip()
    if k:
        return k
    return (HERE / "ors_key.txt").read_text(encoding="utf-8").strip()


def coarse_band(minutes):
    if minutes is None:
        return None
    if minutes <= 60:
        return "<=60"
    if minutes <= 90:
        return "60~90"
    if minutes <= 120:
        return "90~120"
    return ">120"


def matrix_min(key, src, dests):
    locs = [src] + dests
    body = {"locations": locs, "sources": [0],
            "destinations": list(range(1, len(locs))), "metrics": ["duration"]}
    headers = {"Authorization": key, "Content-Type": "application/json"}
    for attempt in range(1, RETRY + 1):
        r = requests.post(MATRIX_URL, json=body, headers=headers, timeout=120)
        if r.status_code == 200:
            row = r.json()["durations"][0]
            vals = [x for x in row if x is not None]
            return min(vals) if vals else None
        if r.status_code == 429:
            time.sleep(SLEEP * attempt * 2)
            continue
        raise SystemExit(f"ORS matrix 오류 {r.status_code}: {r.text[:300]}")
    raise SystemExit("재시도 초과(429).")


def main():
    key = read_key()
    doc = json.loads(HOSP.read_text(encoding="utf-8"))
    dests = [[h["lon"], h["lat"]] for h in doc["hospitals"]]
    biv = json.loads(BIV.read_text(encoding="utf-8"))

    targets = [f for f in biv["features"] if f["properties"].get("access_min") is None]
    print(f"사각지대(access_min=None) 시군구: {len(targets)}개 -> Matrix 실측 시작")

    filled, flagged = 0, 0
    for i, f in enumerate(targets, 1):
        p = f["properties"]
        rep = shape(f["geometry"]).buffer(0).representative_point()
        src = [rep.x, rep.y]
        sec = matrix_min(key, src, dests)
        if sec is None:
            print(f"  [{i}/{len(targets)}] {p['name']}: 경로없음(None 유지)")
        else:
            mins = round(sec / 60, 1)
            p["access_min"] = mins
            p["access_min_exact"] = True
            p["access_band"] = coarse_band(mins)
            if mins > SANE_MAX_MIN:
                p["access_suspect"] = True   # 섬 등 도로 미연결 추정
                flagged += 1
            filled += 1
            tag = " (의심:도로미연결?)" if mins > SANE_MAX_MIN else ""
            print(f"  [{i}/{len(targets)}] {p['name']}: {mins}분{tag}")
        time.sleep(SLEEP)

    BIV.write_text(json.dumps(biv, ensure_ascii=False), encoding="utf-8")
    print(f"\n저장: {BIV}")
    print(f"실측 채움: {filled}개, 과대값 플래그: {flagged}개")

    # 채운 뒤 60분 초과 분포 요약
    over = [f["properties"]["access_min"] for f in biv["features"]
            if f["properties"].get("access_min_exact")]
    over = [m for m in over if m is not None and not (m > SANE_MAX_MIN)]
    if over:
        over.sort()
        print(f"60분 초과 실측 분포: 중앙값 {over[len(over)//2]:.0f}분, 최대 {over[-1]:.0f}분(섬 제외)")


if __name__ == "__main__":
    main()
