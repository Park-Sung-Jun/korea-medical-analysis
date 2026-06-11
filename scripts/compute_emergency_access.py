"""
시군구별 응급의료 접근성 — E-Gen 응급의료기관 → ORS Matrix 실측.

지표(시군구 대표점 → 최근접 기관 운전시간, 분):
  - er_min        : 법정 지정 응급의료기관(권역센터+지역센터+지역기관, 신고기관 제외)
  - er_center_min : 응급의료'센터'급(권역+지역센터)만 — 중증응급 대응 기준

쿼터 절약: 시군구당 Matrix 1콜. 직선거리(haversine) 상위 K 후보(지정기관 K + 센터 K 합집합)만
목적지로 넣고, 한 응답에서 두 지표를 동시에 산출한다. (250콜 ≈ 8분, 일 500콜 한도 내)

usage: python scripts/compute_emergency_access.py
출력: sigungu_bivariate.geojson(in-place) + data/emergency_stats.json
"""
import json
import math
import os
import sys
import time
from pathlib import Path

import requests
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _env  # noqa: F401,E402

DATA = HERE.parent / "data"
BIV = DATA / "sigungu_bivariate.geojson"
EMG = DATA / "emergency.geojson"
MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"
SLEEP = 1.8
RETRY = 4
K = 10                       # 직선거리 후보 수(티어별)
CENTER_CLS = {"권역응급의료센터", "지역응급의료센터"}
DESIG_CLS = CENTER_CLS | {"지역응급의료기관"}   # 신고기관 제외


def hav(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def matrix(key, src, dests):
    locs = [src] + dests
    body = {"locations": locs, "sources": [0],
            "destinations": list(range(1, len(locs))), "metrics": ["duration"]}
    for attempt in range(1, RETRY + 1):
        r = requests.post(MATRIX_URL, json=body,
                          headers={"Authorization": key, "Content-Type": "application/json"},
                          timeout=120)
        if r.status_code == 200:
            return r.json()["durations"][0]
        if r.status_code == 429:
            time.sleep(SLEEP * attempt * 3)
            continue
        raise SystemExit(f"ORS matrix 오류 {r.status_code}: {r.text[:300]}")
    raise SystemExit("재시도 초과(429).")


def band(m):
    if m is None:
        return None
    return "B1" if m <= 10 else ("B2" if m <= 20 else ("B3" if m <= 30 else "B4"))


def pearson(pairs):
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs) ** 0.5
    syy = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(sxy / (sxx * syy), 3) if sxx and syy else None


def main():
    key = os.environ["ORS_API_KEY"].strip()
    emg = json.loads(EMG.read_text(encoding="utf-8"))
    pts = [(f["geometry"]["coordinates"], f["properties"]["cls"], f["properties"]["name"])
           for f in emg["features"]]
    desig = [(c, n) for c, cl, n in pts if cl in DESIG_CLS]
    centers = [(c, n) for c, cl, n in pts if cl in CENTER_CLS]
    print(f"지정기관 {len(desig)} (센터급 {len(centers)})")

    biv = json.loads(BIV.read_text(encoding="utf-8"))
    feats = biv["features"]
    done = 0
    for i, f in enumerate(feats, 1):
        p = f["properties"]
        if p.get("er_min") is not None and p.get("er_center_min") is not None:
            continue  # 재실행 시 이어서
        rep = shape(f["geometry"]).buffer(0).representative_point()
        src = [rep.x, rep.y]
        nd = sorted(desig, key=lambda t: hav(src, t[0]))[:K]
        nc = sorted(centers, key=lambda t: hav(src, t[0]))[:K]
        cand, seen = [], set()
        for c, n in nd + nc:
            kk = (round(c[0], 6), round(c[1], 6))
            if kk not in seen:
                seen.add(kk)
                cand.append((c, n, kk))
        durs = matrix(key, src, [c for c, _, _ in cand])
        center_keys = {(round(c[0], 6), round(c[1], 6)) for c, _ in nc}
        d_all = [d for (c, n, kk), d in zip(cand, durs) if d is not None]
        d_ctr = [d for (c, n, kk), d in zip(cand, durs) if d is not None and kk in center_keys]
        p["er_min"] = round(min(d_all) / 60, 1) if d_all else None
        p["er_center_min"] = round(min(d_ctr) / 60, 1) if d_ctr else None
        p["er_class"] = band(p["er_min"])
        p["er_center_class"] = band(p["er_center_min"])
        done += 1
        print(f"  [{i}/250] {p.get('sido','')} {p['name']}: 지정 {p['er_min']}분 / 센터 {p['er_center_min']}분")
        if done % 25 == 0:  # 중간 저장(쿼터 소진·중단 대비)
            BIV.write_text(json.dumps(biv, ensure_ascii=False), encoding="utf-8")
        time.sleep(SLEEP)

    BIV.write_text(json.dumps(biv, ensure_ascii=False), encoding="utf-8")

    # 통계
    def col(k):
        return [f["properties"].get(k) for f in feats]
    er, ec = col("er_min"), col("er_center_min")
    er_v = sorted(v for v in er if v is not None)
    ec_v = sorted(v for v in ec if v is not None)
    over30_er = [f["properties"] for f in feats if (f["properties"].get("er_min") or 0) > 30]
    over30_ec = [f["properties"] for f in feats if (f["properties"].get("er_center_min") or 0) > 30]
    pair = lambda a, b: list(zip(col(a), col(b)))  # noqa: E731
    stats = {
        "facility_counts": emg.get("meta", {}).get("by_class", {}),
        "designated_n": len(desig), "center_n": len(centers),
        "er": {"median": er_v[len(er_v) // 2], "max": er_v[-1],
               "over30_n": len(over30_er),
               "over30": sorted([{"sido": p.get("sido"), "name": p["name"], "min": p["er_min"]}
                                 for p in over30_er], key=lambda x: -x["min"])},
        "er_center": {"median": ec_v[len(ec_v) // 2], "max": ec_v[-1],
                      "over30_n": len(over30_ec),
                      "over60_n": sum(1 for v in ec_v if v > 60)},
        "corr": {
            "er_vs_tertiary": pearson(pair("er_min", "access_min")),
            "er_center_vs_tertiary": pearson(pair("er_center_min", "access_min")),
            "er_vs_aging": pearson(pair("er_min", "aging_index")),
            "er_center_vs_aging": pearson(pair("er_center_min", "aging_index")),
        },
    }
    (DATA / "emergency_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n저장: geojson + emergency_stats.json")
    print(f"지정기관: 중앙값 {stats['er']['median']}분, >30분 {stats['er']['over30_n']}곳")
    print(f"센터급:   중앙값 {stats['er_center']['median']}분, >30분 {stats['er_center']['over30_n']}곳, >60분 {stats['er_center']['over60_n']}곳")
    print(f"상관: 지정vs상급종합 r={stats['corr']['er_vs_tertiary']}, 센터vs상급종합 r={stats['corr']['er_center_vs_tertiary']}")


if __name__ == "__main__":
    main()
