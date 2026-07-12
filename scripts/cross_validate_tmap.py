"""
교통 반영 교차검증: ORS(자유흐름) vs TMAP(실시간 교통).

같은 OD(시군구 대표점 → ORS 최근접 상급종합병원)에 대해
  - ORS matrix(/v2/matrix/driving-car): 자유흐름 소요시간 + 최근접 병원
  - TMAP routes(자동차, 실시간 교통): 같은 OD 소요시간
을 계산해 ratio = TMAP/ORS 로 ORS 등시선의 과소추정(=등시선 과대) 정도를 측정한다.

키:
  ORS_API_KEY (또는 scripts/ors_key.txt), TMAP_APP_KEY (환경변수)
usage:
  $env:TMAP_APP_KEY="..."; python scripts/cross_validate_tmap.py            # 대표 표본(도시12+농촌8)
  python scripts/cross_validate_tmap.py --full                              # 전체 250
"""
import argparse, csv, json, os, sys, time
from pathlib import Path
import requests
from shapely.geometry import shape
try:
    from . import _env  # noqa: F401  (.env 자동 로드 side-effect)
except ImportError:  # `python scripts/cross_validate_tmap.py` 직접 실행
    import _env  # noqa: F401

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
ORS_MATRIX = "https://api.openrouteservice.org/v2/matrix/driving-car"
TMAP_URL = "https://apis.openapi.sk.com/tmap/routes?version=1&format=json"
SLEEP = 0.4


def ratio_values(rows):
    values = []
    for row in rows:
        try:
            value = float(row.get("ratio"))
        except (TypeError, ValueError):
            continue
        values.append(value)
    return sorted(values)


def ors_key():
    k = os.environ.get("ORS_API_KEY", "").strip()
    return k or (HERE / "ors_key.txt").read_text(encoding="utf-8").strip()


def tmap_key():
    k = os.environ.get("TMAP_APP_KEY", "").strip()
    if not k:
        raise SystemExit("TMAP_APP_KEY 환경변수가 필요합니다.")
    return k


def ors_nearest(okey, src, dests):
    """ORS matrix: src->dests 자유흐름 최소 소요(sec)와 최근접 idx."""
    locs = [src] + dests
    body = {"locations": locs, "sources": [0],
            "destinations": list(range(1, len(locs))), "metrics": ["duration"]}
    r = requests.post(ORS_MATRIX, json=body,
                      headers={"Authorization": okey, "Content-Type": "application/json"}, timeout=60)
    r.raise_for_status()
    row = r.json()["durations"][0]
    best_i, best = None, None
    for i, v in enumerate(row):
        if v is not None and (best is None or v < best):
            best, best_i = v, i
    return best, best_i


def tmap_time(tkey, sx, sy, ex, ey):
    """TMAP 자동차(실시간 교통) 총 소요(sec)."""
    body = {"startX": sx, "startY": sy, "endX": ex, "endY": ey,
            "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
            "searchOption": "0", "trafficInfo": "Y"}
    r = requests.post(TMAP_URL, json=body,
                      headers={"appKey": tkey, "Content-Type": "application/json"}, timeout=60)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} {r.text[:120]}"
    for f in r.json().get("features", []):
        pr = f.get("properties", {})
        if "totalTime" in pr:
            return int(pr["totalTime"]), None
    return None, "totalTime 없음"


def pick_sample(feats):
    """도시 12(고령인구 최다) + 농촌 8(접근성 사각 최다) 표본."""
    def eld(p): return p.get("elderly_pop") or 0
    def amin(p):
        try: return float(p.get("access_min"))
        except (TypeError, ValueError): return None
    urban = sorted(feats, key=lambda p: -eld(p))[:12]
    rural = sorted([p for p in feats if amin(p) is not None],
                   key=lambda p: -(amin(p) or 0))[:8]
    seen, out = set(), []
    for p in urban + rural:
        c = p.get("code")
        if c not in seen:
            seen.add(c); out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--slot", default=None,
                    help="시간슬롯 라벨(예: weekday_am, weekday_pm, weekday_eve, weekend). "
                         "지정 시 data/tmap_slots/<slot>.csv 로 저장 — 다시간대 수집용.")
    ap.add_argument("--codes", default="", help="쉼표로 구분한 선택 시군구 코드")
    ap.add_argument("--biv", type=Path, default=DATA / "sigungu_bivariate.geojson")
    ap.add_argument("--hospitals", type=Path, default=DATA / "hospitals.json")
    ap.add_argument("--out", type=Path, default=None, help="출력 CSV 경로")
    ap.add_argument("--merge-from", type=Path, default=None,
                    help="기존 전수 CSV를 유효 코드만 보존하고 이번 결과로 갱신")
    args = ap.parse_args()

    okey, tkey = ors_key(), tmap_key()
    hosp = json.loads(args.hospitals.read_text(encoding="utf-8"))["hospitals"]
    dests = [[h["lon"], h["lat"]] for h in hosp]
    biv = json.loads(args.biv.read_text(encoding="utf-8"))
    feats = [f for f in biv["features"]]
    props = [f["properties"] for f in feats]

    requested_codes = {code.strip() for code in args.codes.split(",") if code.strip()}
    if requested_codes and args.out is None:
        raise SystemExit("--codes 사용 시 기존 CSV 보호를 위해 --out 경로가 필요합니다.")
    if requested_codes:
        targets = [f for f in feats if str(f["properties"].get("code", "")) in requested_codes]
        found_codes = {str(f["properties"].get("code", "")) for f in targets}
        missing = sorted(requested_codes - found_codes)
        if missing:
            raise SystemExit(f"요청 코드가 GeoJSON에 없습니다: {', '.join(missing)}")
    elif args.full:
        targets = feats
    else:
        chosen = pick_sample(props)
        codes = {p["code"] for p in chosen}
        targets = [f for f in feats if f["properties"].get("code") in codes]

    scope = "선택" if requested_codes else ("전체" if args.full else "표본")
    print(f"교차검증 대상: {len(targets)}개 시군구 ({scope})")
    rows = []
    for i, f in enumerate(targets, 1):
        p = f["properties"]
        rep = shape(f["geometry"]).buffer(0).representative_point()
        src = [rep.x, rep.y]
        try:
            ors_sec, idx = ors_nearest(okey, src, dests)
        except Exception as e:
            print(f"  [{i}] {p.get('name')}: ORS 오류 {e}"); continue
        if ors_sec is None or idx is None:
            print(f"  [{i}] {p.get('name')}: ORS 경로없음"); continue
        h = hosp[idx]
        tmap_sec, err = tmap_time(tkey, rep.x, rep.y, h["lon"], h["lat"])
        if tmap_sec is None:
            print(f"  [{i}] {p.get('name')}: TMAP 실패 {err}"); time.sleep(SLEEP); continue
        ratio = tmap_sec / ors_sec if ors_sec else None
        rows.append({"code": p.get("code"), "sido": p.get("sido"), "name": p.get("name"),
                     "elderly_pop": p.get("elderly_pop"), "pop_total": p.get("pop_total"),
                     "nearest_hosp": h["name"],
                     "ors_min": round(ors_sec / 60, 1), "tmap_min": round(tmap_sec / 60, 1),
                     "ratio": round(ratio, 2) if ratio else None})
        print(f"  [{i}/{len(targets)}] {p.get('sido','')} {p.get('name')}: "
              f"ORS {ors_sec/60:.1f}분 → TMAP {tmap_sec/60:.1f}분 (×{ratio:.2f}) [{h['name']}]")
        time.sleep(SLEEP)

    if args.out is not None:
        out = args.out
    elif args.slot:
        slot_dir = DATA / "tmap_slots"
        slot_dir.mkdir(exist_ok=True)
        out = slot_dir / f"{args.slot}.csv"
    else:
        out = DATA / ("tmap_xcheck_full.csv" if args.full else "tmap_xcheck_sample.csv")
    if len(rows) != len(targets):
        raise SystemExit(f"TMAP/ORS 결과 불완전: {len(rows)}/{len(targets)} — 출력 승격 금지")
    if args.merge_from is not None:
        merged = {}
        valid_codes = {str(f["properties"].get("code", "")) for f in feats}
        with args.merge_from.open(encoding="utf-8-sig", newline="") as fp:
            for row in csv.DictReader(fp):
                code = str(row.get("code", ""))
                if code in merged:
                    raise SystemExit(f"기존 TMAP CSV 중복 코드: {code}")
                if code in valid_codes:
                    merged[code] = row
        merged.update({str(row["code"]): row for row in rows})
        missing = sorted(valid_codes - set(merged))
        if missing:
            raise SystemExit(f"병합 TMAP CSV 미수록 코드 {len(missing)}개: {', '.join(missing[:10])}")
        rows = [merged[code] for code in sorted(merged)]
    if rows:
        with out.open("w", encoding="utf-8-sig", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
    ratios = ratio_values(rows)
    if ratios:
        med = ratios[len(ratios) // 2]
        mean = sum(ratios) / len(ratios)
        big = [r for r in rows
               if ratio_values([r]) and ratio_values([r])[0] >= 1.5]
        print(f"\n저장: {out}  (n={len(rows)})")
        print(f"ratio(TMAP/ORS)  중앙값 {med:.2f}  평균 {mean:.2f}  최대 {ratios[-1]:.2f}")
        print(f"교통으로 1.5배 이상 늘어난 곳: {len(big)}개")
        for r in sorted(big, key=lambda x: -ratio_values([x])[0])[:8]:
            print(f"  {r['sido']} {r['name']}: {r['ors_min']}→{r['tmap_min']}분 ×{r['ratio']}")
        print("\n해석: ratio가 1보다 클수록 ORS가 시간을 과소추정(=등시선 과대). "
              "도시·정체구간에서 클 것으로 예상.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
