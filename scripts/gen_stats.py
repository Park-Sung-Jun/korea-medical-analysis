"""
리포트용 통계 번들 생성: 모든 data/ 산출물을 읽어 data/report_stats.json 으로 요약.
report.html 이 이 JSON 을 fetch 해 KPI/표/상관을 렌더한다.
"""
import csv, json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch_ohca import sido_access_metrics, latest_by_sido, find_col, BIV  # noqa

DATA = HERE.parent / "data"


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs) ** 0.5
    syy = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(sxy / (sxx * syy), 3) if sxx and syy else None


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    biv = json.loads((DATA / "sigungu_bivariate.geojson").read_text(encoding="utf-8"))
    F = [f["properties"] for f in biv["features"]]
    meta = biv.get("meta", {})
    hosp = json.loads((DATA / "hospitals.json").read_text(encoding="utf-8"))["hospitals"]

    # 바이베리엇 분포
    from collections import Counter
    bivar = dict(Counter(p.get("bivar_class") for p in F))

    # 접근성
    over60 = [p for p in F if num(p.get("access_min")) is not None and num(p["access_min"]) > 60]
    exact = [p for p in F if p.get("access_min_exact")]
    susp = [p["name"] for p in F if p.get("access_suspect")]
    over_vals = sorted(num(p["access_min"]) for p in exact
                       if num(p.get("access_min")) and not p.get("access_suspect"))

    # 병원 공급
    gen_total = sum(p.get("hosp_gen_cnt", 0) for p in F)
    sup_total = sum(p.get("hosp_sup_cnt", 0) for p in F)
    gen0 = [p for p in F if p.get("hosp_gen_cnt", 0) == 0]

    # 삼중취약: A3 × >60분 × 종합 0
    triple = [p for p in F if p.get("aging_class") == 3
              and num(p.get("access_min")) is not None and num(p["access_min"]) > 60
              and p.get("hosp_gen_cnt", 0) == 0]
    triple_sorted = sorted(triple, key=lambda p: -num(p["access_min"]))
    triple_rows = [{
        "sido": p.get("sido"), "name": p.get("name"),
        "access_min": round(num(p["access_min"]), 1),
        "aging_index": round(num(p.get("aging_index")), 0) if num(p.get("aging_index")) else None,
        "elderly_pop": p.get("elderly_pop"),
        "hosp_gen_cnt": p.get("hosp_gen_cnt", 0),
        "suspect": bool(p.get("access_suspect")),
    } for p in triple_sorted]

    # 최원거리 시군구 top10(섬 제외 표시)
    far = sorted([p for p in F if num(p.get("access_min")) is not None],
                 key=lambda p: -num(p["access_min"]))[:12]
    far_rows = [{"sido": p.get("sido"), "name": p.get("name"),
                 "access_min": round(num(p["access_min"]), 1),
                 "elderly_pop": p.get("elderly_pop"),
                 "suspect": bool(p.get("access_suspect"))} for p in far]

    # 절대 고령인구 관점: 지수만으로는 안 보이는 '실제 영향 규모'
    eld = lambda p: p.get("elderly_pop") or 0
    deadzone_elderly = sum(eld(p) for p in over60)                       # 60분 초과 시군구의 65+ 합
    nogen_elderly = sum(eld(p) for p in gen0)                            # 종합병원 0개 시군구의 65+ 합
    triple_elderly = sum(eld(p) for p in triple)                        # 삼중취약 65+ 합
    # 고령화지수 최상위지만 고령인구는 적은 사례(지수 vs 절대수 괴리)
    hi_index = sorted([p for p in F if num(p.get("aging_index"))], key=lambda p: -num(p["aging_index"]))[:6]
    # 고령인구 절대수 최다(실제 규모 큰 곳)
    most_elderly = sorted(F, key=lambda p: -eld(p))[:6]
    elderly = {
        "deadzone_over60_elderly": deadzone_elderly,
        "no_general_elderly": nogen_elderly,
        "triple_elderly": triple_elderly,
        "national_elderly": sum(eld(p) for p in F),
        "hi_index_examples": [{"sido": p.get("sido"), "name": p.get("name"),
                               "aging_index": round(num(p["aging_index"]), 0), "elderly_pop": eld(p)} for p in hi_index],
        "most_elderly": [{"sido": p.get("sido"), "name": p.get("name"),
                          "elderly_pop": eld(p), "aging_index": round(num(p.get("aging_index")), 0) if num(p.get("aging_index")) else None,
                          "access_min": round(num(p["access_min"]), 1) if num(p.get("access_min")) is not None else None} for p in most_elderly],
    }

    # OHCA 시도 검증
    acc = sido_access_metrics(BIV)
    surv_rows = list(csv.DictReader((DATA / "ohca_survival_sido.csv").open(encoding="utf-8-sig")))
    inc_rows = list(csv.DictReader((DATA / "ohca_incidence_sido.csv").open(encoding="utf-8-sig")))
    sl, sk = latest_by_sido(surv_rows)
    il, ik = latest_by_sido(inc_rows)
    scol, icol = find_col(sk, "질병_표준"), find_col(ik, "질병_표준")
    sidos = [s for s in acc if s in sl]
    dead = [acc[s]["deadzone_share"] for s in sidos]
    aging = [acc[s]["mean_aging"] for s in sidos]
    surv = [num(sl[s][1].get(scol)) for s in sidos]
    inc = [num(il[s][1].get(icol)) if s in il else None for s in sidos]
    ex = [(d, sv) for s, d, sv in zip(sidos, dead, surv) if s != "제주특별자치도"]
    ohca = {
        "n": len(sidos),
        "r_dead_surv": pearson(dead, surv),
        "r_dead_surv_exJeju": pearson([d for d, _ in ex], [sv for _, sv in ex]),
        "r_aging_surv": pearson(aging, surv),
        "r_dead_inc": pearson(dead, [i for i in inc if i is not None]) if all(i is not None for i in inc) else None,
        "sido_table": sorted([{
            "sido": s, "deadzone_pct": round(acc[s]["deadzone_share"] * 100, 0),
            "aging": round(acc[s]["mean_aging"], 0),
            "survival": round(num(sl[s][1].get(scol)), 1),
            "incidence": round(num(il[s][1].get(icol)), 1) if s in il and num(il[s][1].get(icol)) else None,
        } for s in sidos], key=lambda r: -r["deadzone_pct"]),
    }

    # 교통 반영(TMAP) 교차검증 — geojson에 access_ratio 있을 때만
    traffic = None
    rats = sorted(num(p.get("access_ratio")) for p in F if num(p.get("access_ratio")))
    if rats:
        metro = lambda p: any((p.get("sido") or "").endswith(s) for s in ("특별시", "광역시", "특별자치시"))
        urban = sorted(num(p["access_ratio"]) for p in F if num(p.get("access_ratio")) and metro(p))
        rural = sorted(num(p["access_ratio"]) for p in F if num(p.get("access_ratio")) and not metro(p))
        worst = sorted([p for p in F if num(p.get("access_ratio"))], key=lambda p: -num(p["access_ratio"]))[:6]
        med = lambda a: round(a[len(a) // 2], 2) if a else None
        traffic = {
            "n": len(rats), "median_ratio": med(rats), "mean_ratio": round(sum(rats) / len(rats), 2),
            "urban_median": med(urban), "rural_median": med(rural),
            "over60_ors": sum(1 for p in F if num(p.get("access_min")) is not None and num(p["access_min"]) > 60),
            "over60_tmap": sum(1 for p in F if num(p.get("access_min_tmap")) is not None and num(p["access_min_tmap"]) > 60),
            "a3b3_ors": bivar.get("A3B3", 0),
            "a3b3_tmap": sum(1 for p in F if p.get("bivar_class_tmap") == "A3B3"),
            "worst": [{"sido": p.get("sido"), "name": p.get("name"),
                       "ors_min": round(num(p.get("access_min_ors_exact")), 0) if num(p.get("access_min_ors_exact")) is not None else None,
                       "tmap_min": round(num(p["access_min_tmap"]), 0), "ratio": round(num(p["access_ratio"]), 2)} for p in worst],
        }

    stats = {
        "generated_note": "isochrone_map 통계 번들 (수치는 data/ 산출물 기준)",
        "kpi": {
            "hospitals": len(hosp),
            "sigungu": len(F),
            "iso_bands_min": meta.get("bands_min", [15, 30, 45, 60]),
            "deadzone_over60": len(over60),
            "deadzone_filled_exact": len(exact),
            "a3b3": bivar.get("A3B3", 0),
            "gen_hosp_total": gen_total,
            "sup_hosp_total": sup_total,
            "sigungu_no_general": len(gen0),
            "triple_vulnerable": len(triple_rows),
            "deadzone_elderly": deadzone_elderly,
        },
        "aging_tertiles": meta.get("aging_tertiles"),
        "bivar_dist": bivar,
        "access": {
            "over60_count": len(over60),
            "exact_filled": len(exact),
            "median_over60": round(over_vals[len(over_vals) // 2], 0) if over_vals else None,
            "max_over60_excl_island": round(over_vals[-1], 0) if over_vals else None,
            "island_suspects": susp,
        },
        "supply": {
            "general_hospitals": gen_total, "superior_hospitals": sup_total,
            "sigungu_with_general": len(F) - len(gen0), "sigungu_no_general": len(gen0),
        },
        "triple_vulnerable": triple_rows,
        "farthest": far_rows,
        "elderly": elderly,
        "traffic": traffic,
        "ohca": ohca,
    }
    out = DATA / "report_stats.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out}")
    print(f"  KPI: 병원{stats['kpi']['hospitals']} 시군구{stats['kpi']['sigungu']} "
          f"삼중취약{stats['kpi']['triple_vulnerable']} 종합0={stats['kpi']['sigungu_no_general']}")
    print(f"  OHCA r(사각vs생존)={ohca['r_dead_surv']} (제주제외 {ohca['r_dead_surv_exJeju']}) "
          f"r(고령vs생존)={ohca['r_aging_surv']}")


if __name__ == "__main__":
    main()
