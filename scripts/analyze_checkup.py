"""
건강검진 ① 접근성 × 수검률.

원천: data/kosis_general_checkup_sigungu.csv (KOSIS 시군구별 일반건강검진 대상·수검, wide, cp949)
  - 계층형 '시군구별' 컬럼: "계"(전국) → "서울"(시도) → "종로구"…(시도접두어 없는 시군구)
  - 동명 시군구(중구/동구/남구/북구…) 구분 위해 직전 시도 컨텍스트 추적

처리:
  1) 2024년 성별=합계의 대상/수검 → 시군구 수검률(%) 계산
  2) (시도,명)으로 data/sigungu_bivariate.geojson(250)에 join → checkup_* 필드 기입
  3) 수검률 3분위(C1<C2<C3) + 접근성/고령화와 상관(Pearson) 산출
  4) data/checkup_stats.json 저장, 매칭 리포트 콘솔 출력

usage: python scripts/analyze_checkup.py
"""
import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SRC = DATA / "kosis_general_checkup_sigungu.csv"
GEO = DATA / "sigungu_bivariate.geojson"
STATS = DATA / "checkup_stats.json"
YEAR_COL = "2024 년"

SIDO_SHORT = {"서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
              "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"}
# 시도 단축명 → geojson sido(전체명) 판별용 부분문자열 후보
SIDO_MATCH = {
    "서울": ["서울"], "부산": ["부산"], "대구": ["대구"], "인천": ["인천"],
    "광주": ["광주"], "대전": ["대전"], "울산": ["울산"], "세종": ["세종"],
    "경기": ["경기"], "강원": ["강원"], "충북": ["충청북"], "충남": ["충청남"],
    "전북": ["전북", "전라북"], "전남": ["전남", "전라남"],
    "경북": ["경북", "경상북"], "경남": ["경남", "경상남"], "제주": ["제주"],
}


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


def parse_checkup(src=SRC):
    """returns dict: (sido_short, name) -> {'target':float,'done':float}, + 시도총계 dict."""
    out = {}
    sido_totals = {}  # sido_short -> {'target','done'} (세종 등 무자식 처리용)
    cur_sido = None
    with open(src, encoding="cp949") as fp:
        rd = csv.DictReader(fp)
        for row in rd:
            region = (row.get("시군구별") or "").strip().strip('"')
            sex = (row.get("성별") or "").strip()
            item = (row.get("항목") or "").strip()
            if sex != "합계":
                continue
            val = num(row.get(YEAR_COL))
            kind = "target" if item.startswith("대상") else ("done" if item.startswith("수검") else None)
            if kind is None:
                continue
            if region == "계":
                continue
            if region in SIDO_SHORT:
                cur_sido = region
                sido_totals.setdefault(cur_sido, {})[kind] = val
                continue
            # 시군구 행
            key = (cur_sido, region)
            out.setdefault(key, {})[kind] = val
    return out, sido_totals


def pearson(pairs):
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs) ** 0.5
    syy = sum((y - my) ** 2 for y in ys) ** 0.5
    return (round(sxy / (sxx * syy), 3) if sxx and syy else None), n


def main(src=SRC, geo_path=GEO, stats_path=STATS):
    checkup, sido_totals = parse_checkup(src)
    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    feats = geo["features"]

    # 공백 정규화 인덱스 (창원시 의창구 ↔ 창원시의창구)
    def norm(s):
        return (s or "").replace(" ", "")
    idx = {(s, norm(nm)): rec for (s, nm), rec in checkup.items()}

    def valid(rec):
        return bool(rec) and rec.get("target") not in (None, 0) and rec.get("done") is not None

    def lookup(short, gname):
        nk = norm(gname)
        rec = idx.get((short, nk))
        if valid(rec):
            return rec
        # 통합시 단일(부천시) — 부모 값 공백이면 자식 구 합산
        kids = [r for (s, nr), r in idx.items()
                if s == short and nr != nk and nr.startswith(nk) and ("구" in nr[len(nk):]) and r]
        if kids:
            tt = sum(r.get("target") or 0 for r in kids)
            dd = sum(r.get("done") or 0 for r in kids)
            if tt:
                return {"target": tt, "done": dd}
        return None

    matched, unmatched_geo = 0, []
    rates = []
    for f in feats:
        p = f["properties"]
        gsido = p.get("sido") or ""
        gname = (p.get("name") or "").strip()
        # 이 feature의 시도 단축명 찾기
        short = None
        for s, subs in SIDO_MATCH.items():
            if any(sub in gsido for sub in subs):
                short = s
                break
        rec = lookup(short, gname)
        # 세종 등: 시군구명이 시도와 동일하거나 자식 없음 → 시도총계로 대체
        if not valid(rec) and short == "세종":
            rec = sido_totals.get("세종")
        if not valid(rec):
            unmatched_geo.append(f"{gsido} {gname}")
            p["checkup_target"] = None
            p["checkup_done"] = None
            p["checkup_rate"] = None
            continue
        rate = round(rec["done"] / rec["target"] * 100, 1)
        p["checkup_target"] = int(rec["target"])
        p["checkup_done"] = int(rec["done"])
        p["checkup_rate"] = rate
        rates.append(rate)
        matched += 1

    # 3분위 클래스(낮을수록 취약: C1=하위, C3=상위)
    srt = sorted(rates)
    if srt:
        q1 = srt[len(srt) // 3]
        q2 = srt[2 * len(srt) // 3]
        for f in feats:
            r = f["properties"].get("checkup_rate")
            if r is None:
                f["properties"]["checkup_class"] = None
            else:
                f["properties"]["checkup_class"] = "C1" if r < q1 else ("C2" if r < q2 else "C3")
        geo.setdefault("meta", {})["checkup_tertiles"] = {"q1": q1, "q2": q2,
                                                          "min": srt[0], "max": srt[-1]}

    # 상관분석
    def col(k):
        return [(f["properties"].get(k), f["properties"].get("checkup_rate")) for f in feats]
    r_acc, n_acc = pearson(col("access_min"))
    r_tmap, n_tmap = pearson(col("access_min_tmap"))
    r_aging, n_aging = pearson(col("aging_index"))
    r_eldsh, n_eldsh = pearson(col("elderly_share"))

    # 접근성 사각(>60분) vs 양호(<=30분) 수검률 비교
    def amin(f):
        return num(f["properties"].get("access_min"))
    far = [f["properties"]["checkup_rate"] for f in feats
           if amin(f) is not None and amin(f) > 60 and f["properties"].get("checkup_rate") is not None]
    near = [f["properties"]["checkup_rate"] for f in feats
            if amin(f) is not None and amin(f) <= 30 and f["properties"].get("checkup_rate") is not None]

    def avg(a):
        return round(sum(a) / len(a), 1) if a else None

    # C. 저수검(하위 1/3=C1) 유형화: 도시형(접근좋은데 저수검=자발) vs 농촌형(접근/고령 구조적)
    urban, rural = [], []
    for f in feats:
        p = f["properties"]
        if p.get("checkup_class") != "C1" or p.get("checkup_rate") is None:
            p["checkup_type"] = None
            continue
        a = amin(f)
        t = "도시형" if (a is not None and a <= 30) else "농촌형"
        p["checkup_type"] = t
        (urban if t == "도시형" else rural).append(p)

    def ex(lst):
        return [{"sido": p.get("sido"), "name": p.get("name"), "rate": p["checkup_rate"],
                 "access_min": p.get("access_min"),
                 "aging_index": round(num(p.get("aging_index")), 0) if num(p.get("aging_index")) else None}
                for p in sorted(lst, key=lambda x: x["checkup_rate"])[:6]]
    typology = {
        "urban_n": len(urban), "rural_n": len(rural),
        "urban_mean_rate": avg([p["checkup_rate"] for p in urban]),
        "rural_mean_rate": avg([p["checkup_rate"] for p in rural]),
        "urban_mean_aging": avg([num(p.get("aging_index")) for p in urban if num(p.get("aging_index"))]),
        "rural_mean_aging": avg([num(p.get("aging_index")) for p in rural if num(p.get("aging_index"))]),
        "urban_ex": ex(urban), "rural_ex": ex(rural),
    }

    # 최저 수검률 시군구 top12
    low = sorted([f["properties"] for f in feats if f["properties"].get("checkup_rate") is not None],
                 key=lambda p: p["checkup_rate"])[:12]
    low_rows = [{"sido": p.get("sido"), "name": p.get("name"), "rate": p["checkup_rate"],
                 "access_min": p.get("access_min"), "access_min_tmap": p.get("access_min_tmap"),
                 "aging_index": round(num(p.get("aging_index")), 0) if num(p.get("aging_index")) else None}
                for p in low]

    stats = {
        "year": 2024,
        "matched": matched, "total": len(feats),
        "unmatched": unmatched_geo,
        "national_rate": round(17517365 / 23181731 * 100, 1),
        "rate_min": srt[0] if srt else None, "rate_max": srt[-1] if srt else None,
        "rate_median": srt[len(srt) // 2] if srt else None,
        "corr": {
            "access_min(ORS)": {"r": r_acc, "n": n_acc},
            "access_min_tmap": {"r": r_tmap, "n": n_tmap},
            "aging_index": {"r": r_aging, "n": n_aging},
            "elderly_share": {"r": r_eldsh, "n": n_eldsh},
        },
        "deadzone_over60_mean_rate": avg(far), "deadzone_n": len(far),
        "near30_mean_rate": avg(near), "near_n": len(near),
        "typology": typology,
        "lowest12": low_rows,
    }
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    geo_path.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")

    print(f"매칭: {matched}/{len(feats)}  미매칭 {len(unmatched_geo)}건")
    if unmatched_geo:
        print("  미매칭:", ", ".join(unmatched_geo[:20]))
    print(f"전국 수검률 {stats['national_rate']}%  / 시군구 범위 {stats['rate_min']}~{stats['rate_max']}% (중앙 {stats['rate_median']})")
    print("상관(Pearson, 수검률 대비):")
    for k, v in stats["corr"].items():
        print(f"  {k:18} r={v['r']}  n={v['n']}")
    print(f"접근 사각(>60분) 평균수검률 {stats['deadzone_over60_mean_rate']}% (n={len(far)})  vs  양호(<=30분) {stats['near30_mean_rate']}% (n={len(near)})")
    print("최저 수검률 시군구:", ", ".join(f"{r['name']}({r['rate']}%)" for r in low_rows[:8]))
    print(f"[유형화] 도시형(접근좋은데 저수검) {typology['urban_n']}곳 평균 {typology['urban_mean_rate']}%(고령화 {typology['urban_mean_aging']})"
          f"  vs  농촌형 {typology['rural_n']}곳 평균 {typology['rural_mean_rate']}%(고령화 {typology['rural_mean_aging']})")
    print("  도시형 예:", ", ".join(p["name"] for p in typology["urban_ex"]))
    if unmatched_geo:
        raise SystemExit(f"건강검진 시군구 미매칭 {len(unmatched_geo)}건 — 출력 승격 금지")


def parse_args():
    parser = argparse.ArgumentParser(description="시군구 건강검진 수검률 분석")
    parser.add_argument("--source", type=Path, default=SRC)
    parser.add_argument("--geojson", type=Path, default=GEO)
    parser.add_argument("--stats", type=Path, default=STATS)
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    main(args.source, args.geojson, args.stats)
