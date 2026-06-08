"""
보건복지부 급성심장정지(OHCA) 통계(odcloud) 수집 + 시도 단위 접근성 검증.

이 데이터는 **시도(17) 단위**다(시군구 아님). 시군구 분석축으로 붙이지 않고,
'상급종합 접근성이 나쁜 시도가 실제로 OHCA 결과(생존율)가 나쁜가'를 시도 스케일에서 검증한다.

- 발생률: namespace 15127892, 2023 스냅샷
- 생존율: namespace 15127893, 2023 스냅샷  (접근성의 핵심 결과지표)
컬럼은 질병(의학적/심장성)·질병외(외상성)로 나뉘며, '질병_표준화'(연령표준화)를 사용한다.

키: 환경변수 DATAGOKR_API_KEY (odcloud 동일 serviceKey).
usage: python scripts/fetch_ohca.py
"""
import json, os, csv
from pathlib import Path
from urllib.parse import unquote
import requests
import _env  # noqa: F401  (.env 자동 로드 side-effect)

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
BIV = DATA / "sigungu_bivariate.geojson"

ODCLOUD = "https://api.odcloud.kr/api"
INCID_UDDI = "15127892/v1/uddi:3c6ba00b-f813-446d-bd96-9487a268773c"   # 발생률 2023
SURV_UDDI = "15127893/v1/uddi:43c28659-7d57-4ca6-a967-d253da15ace8"   # 생존율 2023

SIDO_MAP = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전라북도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
    "제주": "제주특별자치도",
}


def read_key():
    k = os.environ.get("DATAGOKR_API_KEY", "").strip()
    if not k:
        kf = HERE.parent.parent / "h3_tag" / "config.py"
        if kf.exists():
            import sys, importlib
            sys.path.insert(0, str(kf.parent))
            import config as c; importlib.reload(c)
            k = str(getattr(c, "DATAGOKR_API_KEY", "") or "").strip()
    if not k:
        raise SystemExit("DATAGOKR_API_KEY 가 없습니다.")
    return unquote(k)


def fetch_odcloud(key, uddi, per=500):
    r = requests.get(f"{ODCLOUD}/{uddi}",
                     params={"serviceKey": key, "page": 1, "perPage": per, "returnType": "JSON"},
                     timeout=40)
    r.raise_for_status()
    return r.json().get("data", [])


def find_col(keys, *subs):
    for k in keys:
        if all(s in k for s in subs):
            return k
    return None


def latest_by_sido(rows):
    """시도별 최신 연도 행만 추린다. 반환: ({geojson_sido: (year, row)}, keys)."""
    if not rows:
        return {}, []
    ks = list(rows[0].keys())
    c_sido = find_col(ks, "시도") or ks[0]
    c_year = find_col(ks, "연도") or find_col(ks, "년")
    out = {}
    for r in rows:
        sd = str(r.get(c_sido, "")).split()[0].strip()   # '서울 Seoul' -> '서울'
        sido = SIDO_MAP.get(sd)
        if not sido:
            continue
        yr = int(r.get(c_year, 0) or 0)
        if sido not in out or yr > out[sido][0]:
            out[sido] = (yr, r)
    return out, ks


def save_csv(rows, ks, path):
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=ks)
        w.writeheader(); w.writerows(rows)


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs) ** 0.5
    syy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sxy / (sxx * syy) if sxx and syy else None


def sido_access_metrics(biv_path):
    fc = json.loads(biv_path.read_text(encoding="utf-8"))
    agg = {}
    for f in fc["features"]:
        p = f["properties"]
        sido = p.get("sido")
        if not sido:
            continue
        a = agg.setdefault(sido, {"n": 0, "dead": 0, "acc": [], "aging": [], "gen0": 0})
        a["n"] += 1
        try:
            amv = float(p.get("access_min"))
        except (TypeError, ValueError):
            amv = None
        if amv is not None:
            a["acc"].append(amv)
            if amv > 60:
                a["dead"] += 1
        ag = p.get("aging_index")
        if isinstance(ag, (int, float)):
            a["aging"].append(ag)
        if p.get("hosp_gen_cnt", 0) == 0:
            a["gen0"] += 1
    res = {}
    for sido, a in agg.items():
        acc = sorted(a["acc"])
        res[sido] = {
            "n": a["n"],
            "deadzone_share": a["dead"] / a["n"],
            "median_access": acc[len(acc) // 2] if acc else None,
            "mean_aging": (sum(a["aging"]) / len(a["aging"])) if a["aging"] else None,
            "gen0_share": a["gen0"] / a["n"],
        }
    return res


def fval(row, col):
    v = row.get(col) if row else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    key = read_key()
    acc = sido_access_metrics(BIV)

    inc_rows = fetch_odcloud(key, INCID_UDDI)
    inc_latest, inc_ks = latest_by_sido(inc_rows)
    inc_col = find_col(inc_ks, "질병_표준")           # 질병성(심장성) 표준화 발생률
    save_csv(inc_rows, inc_ks, DATA / "ohca_incidence_sido.csv")

    surv_rows = fetch_odcloud(key, SURV_UDDI)
    surv_latest, surv_ks = latest_by_sido(surv_rows)
    surv_col = find_col(surv_ks, "질병_표준")          # 질병성 표준화 생존율
    save_csv(surv_rows, surv_ks, DATA / "ohca_survival_sido.csv")

    print(f"[발생률] rows={len(inc_rows)} col='{inc_col}'  → data/ohca_incidence_sido.csv")
    print(f"[생존율] rows={len(surv_rows)} col='{surv_col}'  → data/ohca_survival_sido.csv")

    print("\n시도별 — 상급종합 접근성 vs OHCA 결과(질병성 표준화, 최신년 2023)")
    print(f"{'시도':<10}{'사각%':>7}{'중앙접근':>8}{'고령화':>8}{'발생률':>8}{'생존율':>8}")
    d_dead, d_acc, d_age, y_inc, y_surv = [], [], [], [], []
    rows_for_surv = []
    for sido in sorted(acc, key=lambda s: -acc[s]["deadzone_share"]):
        m = acc[sido]
        inc = fval(inc_latest.get(sido, (0, {}))[1], inc_col)
        surv = fval(surv_latest.get(sido, (0, {}))[1], surv_col)
        print(f"{sido:<10}{m['deadzone_share']*100:>6.0f}%{str(round(m['median_access'],1)):>8}"
              f"{str(round(m['mean_aging'],0)):>8}"
              f"{(f'{inc:.1f}' if inc is not None else '-'):>8}"
              f"{(f'{surv:.1f}' if surv is not None else '-'):>8}")
        if surv is not None:
            d_dead.append(m["deadzone_share"]); d_acc.append(m["median_access"])
            d_age.append(m["mean_aging"]); y_surv.append(surv)
            rows_for_surv.append((sido, m["deadzone_share"], surv))
        if inc is not None:
            y_inc.append(inc)

    print("\n[상관] (n=%d, 시도 단위)" % len(y_surv))
    r_di = pearson(d_dead, y_inc) if len(y_inc) == len(d_dead) else None
    r_ds = pearson(d_dead, y_surv)
    r_as = pearson(d_acc, y_surv)
    r_ages = pearson(d_age, y_surv)
    # 제주(섬, 접근성 과대) 제외 민감도
    ex = [(dd, ss) for (sd, dd, ss) in rows_for_surv if sd != "제주특별자치도"]
    r_ds_ex = pearson([d for d, _ in ex], [s for _, s in ex])
    print(f"  사각지대비율 vs 발생률   r = {r_di:.3f}" if r_di is not None else "  발생률 n/a")
    print(f"  사각지대비율 vs 생존율   r = {r_ds:.3f}   (제주 제외 r = {r_ds_ex:.3f})")
    print(f"  중앙접근시간 vs 생존율   r = {r_as:.3f}")
    print(f"  고령화      vs 생존율   r = {r_ages:.3f}")
    print("\n해석: 생존율은 음(-)의 상관일수록 '접근 나쁜 시도가 생존율 낮다'는 가설을 지지.")
    print("      시도(17) 해상도라 신호가 희석되며, 섬(제주)·소표본(n=17) 주의.")


if __name__ == "__main__":
    main()
