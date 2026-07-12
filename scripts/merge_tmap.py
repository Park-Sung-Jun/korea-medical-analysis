"""
TMAP 전수 교차검증 결과(tmap_xcheck_full.csv)를 sigungu_bivariate.geojson 에 병합하고
교통반영 접근성으로 바이베리엇을 재분류한다.

추가 필드:
  access_min_tmap  : 교통반영(TMAP) 운전시간(분)
  access_ratio     : TMAP/ORS 비율
  access_class_tmap: 1(<=30) / 2(30~60) / 3(>60)
  bivar_class_tmap : A{고령화}B{교통반영접근}

ORS(자유흐름) 기준과 비교 통계도 출력.
"""
import argparse, csv, json, sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def aclass(m):
    if m is None:
        return 3
    if m <= 30:
        return 1
    if m <= 60:
        return 2
    return 3


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


def _number(value):
    if value in (None, ""):
        return None
    return float(value)


def apply_tmap_row(properties, row):
    """TMAP 행을 병합하고 60분 등시선 밖 지역은 ORS Matrix 값으로 복원한다."""
    tmap_minutes = _number(row.get("tmap_min"))
    ors_minutes = _number(row.get("ors_min"))
    properties["access_min_tmap"] = tmap_minutes
    properties["access_min_ors_exact"] = ors_minutes
    properties["access_ratio"] = _number(row.get("ratio"))

    if properties.get("access_min") is None and ors_minutes is not None:
        properties["access_min"] = ors_minutes
        properties["access_min_exact"] = True
        properties["access_band"] = coarse_band(ors_minutes)
        properties["access_suspect"] = ors_minutes > 300
        access_class = aclass(ors_minutes)
        properties["access_class"] = access_class
        properties["access_label"] = (
            "<=30분" if access_class == 1 else "30~60분" if access_class == 2 else ">60분/사각지대"
        )
        aging_class = properties.get("aging_class")
        properties["bivar_class"] = f"A{aging_class}B{access_class}" if aging_class else None

    tmap_class = aclass(tmap_minutes)
    properties["access_class_tmap"] = tmap_class
    aging_class = properties.get("aging_class")
    properties["bivar_class_tmap"] = f"A{aging_class}B{tmap_class}" if aging_class else None


def main():
    parser = argparse.ArgumentParser(description="TMAP 교차검증 결과를 의료 접근성 GeoJSON에 병합")
    parser.add_argument("--src", type=Path, default=DATA / "tmap_xcheck_full.csv")
    parser.add_argument("--biv", type=Path, default=DATA / "sigungu_bivariate.geojson")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--require-codes", default="", help="반드시 병합돼야 할 쉼표 구분 코드")
    args = parser.parse_args()
    src = args.src
    if not src.exists():
        raise SystemExit(f"{src} 없음 — 먼저 cross_validate_tmap.py --full 실행")
    by = {}
    for r in csv.DictReader(src.open(encoding="utf-8-sig")):
        code = r["code"]
        if code in by:
            raise SystemExit(f"TMAP CSV 중복 코드: {code}")
        by[code] = r

    biv = json.loads(args.biv.read_text(encoding="utf-8"))
    changed = matched = 0
    matched_codes = set()
    from collections import Counter
    dist = Counter()
    for f in biv["features"]:
        p = f["properties"]
        row = by.get(str(p.get("code", "")))
        if not row:
            continue
        matched += 1
        matched_codes.add(str(p.get("code", "")))
        apply_tmap_row(p, row)
        ac = p["access_class_tmap"]
        dist[p["bivar_class_tmap"]] += 1
        if ac != p.get("access_class"):
            changed += 1

    biv.setdefault("meta", {})["tmap_fields"] = \
        "access_min_tmap=교통반영(TMAP)분, access_ratio=TMAP/ORS, bivar_class_tmap=교통반영 바이베리엇"
    required_codes = {code.strip() for code in args.require_codes.split(",") if code.strip()}
    missing = sorted(required_codes - matched_codes)
    if missing:
        raise SystemExit(f"필수 TMAP 코드 미병합: {', '.join(missing)}")
    out = args.out or args.biv
    out.write_text(json.dumps(biv, ensure_ascii=False), encoding="utf-8")

    # 통계
    F = [f["properties"] for f in biv["features"]]
    def num(x):
        try: return float(x)
        except (TypeError, ValueError): return None
    ratios = sorted(num(p.get("access_ratio")) for p in F if num(p.get("access_ratio")))
    over60_ors = [p for p in F if num(p.get("access_min")) is not None and num(p["access_min"]) > 60]
    over60_tmap = [p for p in F if num(p.get("access_min_tmap")) is not None and num(p["access_min_tmap"]) > 60]
    a3b3_ors = sum(1 for p in F if p.get("bivar_class") == "A3B3")
    a3b3_tmap = sum(1 for p in F if p.get("bivar_class_tmap") == "A3B3")
    triple_tmap = [p for p in F if p.get("aging_class") == 3
                   and num(p.get("access_min_tmap")) is not None and num(p["access_min_tmap"]) > 60
                   and p.get("hosp_gen_cnt", 0) == 0]

    print(f"병합: {matched}/{len(F)} 시군구")
    if ratios:
        print(f"TMAP/ORS 비율  중앙값 {ratios[len(ratios)//2]:.2f}  평균 {sum(ratios)/len(ratios):.2f}")
    print(f"접근 등급(B) ORS와 달라진 시군구: {changed}개")
    print(f"60분 초과: ORS {len(over60_ors)} → TMAP {len(over60_tmap)}")
    print(f"최취약 A3B3: ORS {a3b3_ors} → TMAP {a3b3_tmap}")
    print(f"교통반영 바이베리엇 분포: {dict(sorted(dist.items()))}")
    print(f"교통반영 삼중취약(A3·60분초과·종합0): {len(triple_tmap)}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
