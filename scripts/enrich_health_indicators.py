"""
건강검진 ④ 건강지표 지도화 — 시군구별 질환부담 지표를 geojson에 join.

지표(모두 KOSIS long-format, 시군구, 2024, 성별 합계):
  - disease_rate   : 유질환자율   = N098 '유질환자' / '계'          (일반건강검진 판정현황)
  - metabolic_rate : 대사증후군 위험군 비율 = N135 '대사증후군(3~5개)' 인원 / '수검자수' 인원
  - cancer_rate    : 암검진 수검률 = N009 수검인원 / 대상인원 (암검진별 '계', axis=ITM)

핵심 처리:
  - C1 코드 letter가 시도를 인코딩 → 동명 시군구(중구·동구 등 6종) 안전 구분
  - 통합시(부천 등)는 카운트 레벨 합산 후 비율 산출 (analyze_checkup 과 동일한 매칭 규칙)
  - 각 지표 3분위(C1<C2<C3, 높을수록 질환부담 큼=취약) + geojson 필드 기입

usage: python scripts/enrich_health_indicators.py
"""
import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
GEO = DATA / "sigungu_bivariate.geojson"
STATS = DATA / "health_indicator_stats.json"

SIDO_SHORT = {"서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
              "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"}
SIDO_MATCH = {
    "서울": ["서울"], "부산": ["부산"], "대구": ["대구"], "인천": ["인천"],
    "광주": ["광주"], "대전": ["대전"], "울산": ["울산"], "세종": ["세종"],
    "경기": ["경기"], "강원": ["강원"], "충북": ["충청북"], "충남": ["충청남"],
    "전북": ["전북", "전라북"], "전남": ["전남", "전라남"],
    "경북": ["경북", "경상북"], "경남": ["경남", "경상남"], "제주": ["제주"],
}

INDICATORS = [
    {"field": "disease_rate", "table": "DT_35007_N098", "year": "2024",
     "numer": "유질환자", "denom": "계", "item": None, "label": "유질환자율(%)"},
    {"field": "metabolic_rate", "table": "DT_35007_N135", "year": "2024",
     "numer": "대사증후군(3~5개)", "denom": "수검자수", "item": "인원", "label": "대사증후군 위험군 비율(%)"},
    # 암검진: 분자/분모가 ITM_NM(수검/대상인원), C3_NM은 암종 구분 → '계' 고정
    {"field": "cancer_rate", "table": "DT_35007_N009", "year": "2024",
     "numer": "수검인원", "denom": "대상인원", "item": None, "c3": "계", "axis": "ITM",
     "label": "암검진 수검률(%)"},
]


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "X", "x", "..", "…"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def norm(s):
    return (s or "").replace(" ", "")


def parse_counts(spec, data_dir=DATA):
    """returns idx: (sido_short, normname) -> {'n':numer_count, 'd':denom_count}."""
    f = data_dir / (spec["table"] + ".csv")
    fp = open(f, encoding="utf-8-sig", newline="")
    rd = csv.DictReader(fp)
    sido_codes = {}          # code -> sido_short (시도 행)
    sido_totals = {}         # sido_short -> {'n':, 'd':}  (세종 등 무자식 폴백)
    per = {}                 # code -> {'name':, 'n':, 'd':}
    for r in rd:
        if r.get("PRD_DE") != spec["year"]:
            continue
        if (r.get("C2_NM") or "").strip() != "합계":
            continue
        if spec["item"] and (r.get("ITM_NM") or "").strip() != spec["item"]:
            continue
        code = r.get("C1")
        name = (r.get("C1_NM") or "").strip()
        c3 = (r.get("C3_NM") or "").strip()
        if spec.get("c3") and c3 != spec["c3"]:
            continue
        # axis=ITM 이면 분자/분모를 ITM_NM(수검/대상인원 등)으로 구분
        key = (r.get("ITM_NM") or "").strip() if spec.get("axis") == "ITM" else c3
        val = num(r.get("DT"))
        if name in SIDO_SHORT:
            sido_codes[code] = name
            t = sido_totals.setdefault(name, {"n": None, "d": None})
            if key == spec["numer"]:
                t["n"] = val
            elif key == spec["denom"]:
                t["d"] = val
            continue
        d = per.setdefault(code, {"name": name, "n": None, "d": None})
        if key == spec["numer"]:
            d["n"] = val
        elif key == spec["denom"]:
            d["d"] = val
    fp.close()

    # 시도 코드 prefix(끝 '1' 제거) 로 시군구→시도 매핑
    sido_prefix = {code[:-1]: short for code, short in sido_codes.items()}

    def sido_of(code):
        best = None
        for pre, short in sido_prefix.items():
            if code.startswith(pre) and (best is None or len(pre) > len(best[0])):
                best = (pre, short)
        return best[1] if best else None

    idx = {}
    for code, d in per.items():
        short = sido_of(code)
        if not short:
            continue
        idx[(short, norm(d["name"]))] = {"n": d["n"], "d": d["d"]}
    return idx, sido_totals


def lookup(idx, short, gname):
    nk = norm(gname)
    base = idx.get((short, nk))
    if base and base.get("d"):
        return base["n"], base["d"]
    # 통합시 단일(부천시) — 자식 구 카운트 합산
    kids = [v for (s, nr), v in idx.items()
            if s == short and nr != nk and nr.startswith(nk) and ("구" in nr[len(nk):])]
    if kids:
        dd = sum(v["d"] for v in kids if v.get("d"))
        nn = sum(v["n"] for v in kids if v.get("n"))
        if dd:
            return nn, dd
    return None, None


def tertile(feats, field):
    vals = sorted(f["properties"][field] for f in feats if f["properties"].get(field) is not None)
    if not vals:
        return None
    q1, q2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    for f in feats:
        v = f["properties"].get(field)
        f["properties"][field + "_class"] = None if v is None else ("C1" if v < q1 else ("C2" if v < q2 else "C3"))
    return {"q1": round(q1, 1), "q2": round(q2, 1), "min": round(vals[0], 1), "max": round(vals[-1], 1)}


def pearson(pairs):
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs) ** 0.5
    syy = sum((y - my) ** 2 for y in ys) ** 0.5
    return (round(sxy / (sxx * syy), 3) if sxx and syy else None), n


def sido_short_of(gsido):
    for s, subs in SIDO_MATCH.items():
        if any(sub in gsido for sub in subs):
            return s
    return None


def main(geo_path=GEO, stats_path=STATS, data_dir=DATA):
    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    feats = geo["features"]
    meta = geo.setdefault("meta", {})
    stats = {"year": 2024, "indicators": {}}

    for spec in INDICATORS:
        idx, sido_totals = parse_counts(spec, data_dir)
        field = spec["field"]
        nat_n = nat_d = 0
        matched = 0
        unmatched = []
        for f in feats:
            p = f["properties"]
            short = sido_short_of(p.get("sido") or "")
            n, d = lookup(idx, short, (p.get("name") or "").strip())
            if not d and short == "세종":
                t = sido_totals.get("세종") or {}
                n, d = t.get("n"), t.get("d")
            if d:
                p[field] = round(n / d * 100, 1)
                nat_n += n
                nat_d += d
                matched += 1
            else:
                p[field] = None
                unmatched.append(f"{p.get('sido')} {p.get('name')}")
        tert = tertile(feats, field)
        meta[field + "_tertiles"] = tert
        # 상관
        def col(k):
            return [(f["properties"].get(k), f["properties"].get(field)) for f in feats]
        r_acc, _ = pearson(col("access_min"))
        r_age, _ = pearson(col("aging_index"))
        r_chk, _ = pearson(col("checkup_rate"))
        stats["indicators"][field] = {
            "label": spec["label"], "matched": matched, "unmatched": unmatched,
            "national_rate": round(nat_n / nat_d * 100, 1) if nat_d else None,
            "range": [tert["min"], tert["max"]] if tert else None, "tertiles": tert,
            "corr": {"access_min": r_acc, "aging_index": r_age, "checkup_rate": r_chk},
        }
        print(f"[{field}] {spec['label']}  매칭 {matched}/{len(feats)}  미매칭 {len(unmatched)}  "
              f"전국 {stats['indicators'][field]['national_rate']}%  범위 {tert['min']}~{tert['max']}%")
        print(f"    상관: 접근성 r={r_acc}  고령화 r={r_age}  수검률 r={r_chk}")
        if unmatched:
            print("    미매칭:", ", ".join(unmatched[:12]))

    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    geo_path.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {geo_path} + {stats_path}")
    total_unmatched = sum(len(v["unmatched"]) for v in stats["indicators"].values())
    if total_unmatched:
        raise SystemExit(f"건강지표 시군구 미매칭 합계 {total_unmatched}건 — 출력 승격 금지")


def parse_args():
    parser = argparse.ArgumentParser(description="시군구 건강지표 병합")
    parser.add_argument("--geojson", type=Path, default=GEO)
    parser.add_argument("--stats", type=Path, default=STATS)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    main(args.geojson, args.stats, args.data_dir)
