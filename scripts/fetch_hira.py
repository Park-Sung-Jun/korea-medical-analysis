"""
건강보험심사평가원(HIRA) 병원정보서비스로 전국 병원을 수집해
(1) 종별(clCdNm) '상급종합병원' 명단을 공식 기준 data/hospitals.json과 교차검증하고,
(2) 종별 '상급종합병원'·'종합병원' 개수를 시군구코드(sgguCd) 기준으로 집계해
    data/sigungu_bivariate.geojson 의 각 feature properties에
    hosp_sup_cnt(상급종합)·hosp_gen_cnt(종합)로 in-place 병합한다.

API: apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList
응답 item 주요 필드:
  yadmNm(기관명), clCd/clCdNm(종별코드/명), sidoCd, sgguCd, sgguCdNm,
  addr, xPos(lng), yPos(lat), drTotCnt(의사수). resultType=json, numOfRows/pageNo 페이징.

주의:
  - h3_tag/scripts/08_load_hospitals.py 는 clCd를 '진료과목'으로 잘못 매핑한다.
    그 버그를 따라하지 말고, 반드시 clCdNm(종별명)으로 종별을 판별한다.
  - 절대 shared_data 의 DuckDB를 건드리지 않는다. 출력은 korea-medical-analysis/data 안에만.

키 우선순위:
  1) 환경변수 DATAGOKR_API_KEY
  2) h3_tag/config.py 의 DATAGOKR_API_KEY
  3) 둘 다 없으면 안내 후 종료(파일 작성 상태는 그대로 둠).

사용법:
  python scripts/fetch_hira.py            # 전체 페이징 수집 → 교차검증 + geojson in-place 병합
  python scripts/fetch_hira.py --dry-run  # 건수만 확인(병합/검증 출력 없음)
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
import _env  # noqa: F401  (.env 자동 로드 side-effect)

# ── 경로 상수 ────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # korea-medical-analysis/
DATA = ROOT / "data"
HOSPITALS_JSON = DATA / "hospitals.json"          # 공식 상급종합 명단
HOSP_POINTS_GEOJSON = DATA / "hospitals.geojson"  # 공식 상급종합 포인트(좌표)
BIVARIATE_GEOJSON = DATA / "sigungu_bivariate.geojson"  # in-place 병합 대상
GEN_OUT_CSV = DATA / "hira_general_hospitals.csv"  # 종합병원 목록(공간조인 결과) 저장

# h3_tag/config.py 가 있는 위치 (워크스페이스 형제 폴더)
H3_TAG_DIR = ROOT.parent / "h3_tag"

# HIRA 병원정보서비스 (종별 정보 포함)
BASE_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"

# 종별명(clCdNm) 판별 기준 문자열
SUP_NAME = "상급종합병원"   # 3차 병원
GEN_NAME = "종합병원"       # 종합병원 (clCdNm 정확히 '종합병원'만; '상급종합병원'은 별도)


# ── 시군구 코드 정규화 (build_sigungu.py 와 동일 로직 재사용) ─────────
def remap_code(sgg: str) -> str:
    """
    행정구역 개편(2023~2024) 반영해 시도코드를 특별자치도 코드로 리맵한다.
    scripts/build_sigungu.py 의 remap_code() 와 동일하게 유지해야 한다.
      - 강원 42xxx -> 51xxx (시군구 하위 3자리 유지)
      - 전북 45xxx -> 52xxx
      - 군위군 47720 -> 27720 (2023.7 경북 -> 대구 편입)
    HIRA sgguCd 는 구(舊) 행정코드를 쓰므로 geojson 의 신(新) code 와 맞추려면 리맵 필요.
    """
    sgg = str(sgg).strip()
    if sgg.startswith("42"):
        return "51" + sgg[2:]
    if sgg.startswith("45"):
        return "52" + sgg[2:]
    if sgg == "47720":   # 군위군
        return "27720"
    return sgg


# ── API 키 해석 ─────────────────────────────────────────────────────
def resolve_api_key() -> str:
    """
    DATAGOKR_API_KEY 를 환경변수 우선, 없으면 h3_tag/config.py 에서 읽는다.
    둘 다 비어 있으면 빈 문자열을 반환한다(호출부에서 안내 후 종료).
    """
    key = os.environ.get("DATAGOKR_API_KEY", "").strip()
    if key:
        print("[KEY] 환경변수 DATAGOKR_API_KEY 사용")
        return key

    # h3_tag/config.py 에서 값 읽기 (sys.path 에 임시 추가)
    cfg_path = H3_TAG_DIR / "config.py"
    if cfg_path.exists():
        sys.path.insert(0, str(H3_TAG_DIR))
        try:
            import importlib
            import config as h3_config  # h3_tag/config.py
            importlib.reload(h3_config)
            key = str(getattr(h3_config, "DATAGOKR_API_KEY", "") or "").strip()
            if key:
                print("[KEY] h3_tag/config.py 의 DATAGOKR_API_KEY 사용")
                return key
        except Exception as e:
            print(f"[KEY] h3_tag/config.py 로드 실패: {e}")
        finally:
            # 임시로 넣은 경로 정리
            if str(H3_TAG_DIR) in sys.path:
                sys.path.remove(str(H3_TAG_DIR))

    return ""


# ── API 수집 ────────────────────────────────────────────────────────
def fetch_all_hospitals(api_key: str):
    """
    getHospBasisList 전체 페이지를 수집한다.
    반환: (items 리스트, ok 여부, 사유 문자열)
      - ok=False 면 401 등 키/권한 문제이거나 응답 구조 이상.
    """
    items_all = []
    page = 1
    page_size = 1000
    total_count = None

    print("[API] HIRA 병원정보서비스 수집 시작 ...")
    while True:
        params = {
            "serviceKey": api_key,   # config 값이 URL-encoded 이면 requests 가 재인코딩하지 않도록 아래 unquote 처리
            "pageNo": page,
            "numOfRows": page_size,
            # 이 v2 엔드포인트는 resultType=json 을 무시하고 XML 을 준다. JSON 강제는 _type=json.
            "_type": "json",
            "resultType": "json",
        }
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except Exception as e:
            return items_all, False, f"네트워크 오류: {e}"

        # 401/403 등은 키 미등록/만료 신호 — 우아하게 종료
        if resp.status_code in (401, 403):
            return items_all, False, f"인증 실패(HTTP {resp.status_code}) — API 키 미등록/만료/미승인"
        if resp.status_code != 200:
            return items_all, False, f"HTTP {resp.status_code} 응답"

        # data.go.kr 는 키 오류 시 JSON 이 아니라 XML(에러)을 주기도 한다.
        try:
            data = resp.json()
        except Exception:
            text = resp.text[:300]
            return items_all, False, f"JSON 파싱 실패(키 오류 가능성). 응답 일부: {text}"

        # 표준 응답: response.header.resultCode == '00' 이 정상
        header = data.get("response", {}).get("header", {})
        result_code = str(header.get("resultCode", "")).strip()
        if result_code and result_code not in ("00", "0"):
            msg = header.get("resultMsg", "")
            return items_all, False, f"API 오류 resultCode={result_code} ({msg})"

        body = data.get("response", {}).get("body", {})
        if total_count is None:
            total_count = int(body.get("totalCount", 0) or 0)
            print(f"  전체 건수: {total_count:,}")

        items = body.get("items", {})
        if not items:
            break
        item_list = items.get("item", [])
        if not item_list:
            break
        if isinstance(item_list, dict):   # 1건이면 dict 로 옴
            item_list = [item_list]

        items_all.extend(item_list)
        fetched = (page - 1) * page_size + len(item_list)
        print(f"  page {page}: {len(item_list)}건 (누적 {fetched:,}/{total_count:,})")

        if total_count and fetched >= total_count:
            break
        page += 1
        time.sleep(0.1)   # 호출 간 짧은 대기

    return items_all, True, "ok"


# ── 종별 분류 (clCdNm 기준) ─────────────────────────────────────────
def classify_clcdnm(item: dict) -> str:
    """
    clCdNm(종별명)으로 'sup'(상급종합) / 'gen'(종합) / None 을 반환한다.
    clCd(코드)는 진료과목이 아니라 종별 코드이지만, 명칭 변동에 견고하도록 명(clCdNm)으로 판별한다.
    """
    name = str(item.get("clCdNm", "")).strip()
    if name == SUP_NAME:
        return "sup"
    if name == GEN_NAME:
        return "gen"
    return None


# ── (1) 상급종합 교차검증 ───────────────────────────────────────────
def cross_check_superior(api_items, manual_path: Path):
    """
    API 상급종합병원 명단과 수작업 hospitals.json 명단을 교차검증해 출력한다.
    """
    print("\n" + "=" * 60)
    print("[1] 상급종합병원 교차검증 (API clCdNm == '상급종합병원')")
    print("=" * 60)

    api_sup = [it for it in api_items if classify_clcdnm(it) == "sup"]
    api_names = sorted({str(it.get("yadmNm", "")).strip() for it in api_sup if it.get("yadmNm")})
    print(f"API 상급종합 기관 수: {len(api_names)}")

    if not manual_path.exists():
        print(f"[WARN] 수작업 명단 없음: {manual_path}")
        return
    manual = json.loads(manual_path.read_text(encoding="utf-8"))
    manual_list = manual.get("hospitals", [])
    manual_names = sorted({str(h.get("name", "")).strip() for h in manual_list if h.get("name")})
    print(f"수작업 명단(hospitals.json) 기관 수: {len(manual_names)}")

    def norm(s: str) -> str:
        # 비교용 정규화: 공백/괄호 내용/'학교' 제거로 표기 차이를 흡수
        s = s.replace(" ", "")
        if "(" in s:
            s = s.split("(", 1)[0]
        return s.replace("학교", "")

    api_norm = {norm(n): n for n in api_names}
    man_norm = {norm(n): n for n in manual_names}

    matched = sorted(set(api_norm) & set(man_norm))
    only_api = sorted(set(api_norm) - set(man_norm))
    only_manual = sorted(set(man_norm) - set(api_norm))

    print(f"\n매칭: {len(matched)}개")
    print(f"API 에만 있음(수작업 누락 후보): {len(only_api)}")
    for k in only_api:
        print(f"    + {api_norm[k]}")
    print(f"수작업에만 있음(API 미검출/표기차/폐지 후보): {len(only_manual)}")
    for k in only_manual:
        print(f"    - {man_norm[k]}")


# ── (2) 시군구 집계 + geojson 병합 ──────────────────────────────────
def _build_locator(features):
    """각 feature geometry 의 bbox + geom 리스트를 만들어 point->feature 인덱스 조회용으로 쓴다."""
    from shapely.geometry import shape
    geoms = []
    for i, f in enumerate(features):
        g = shape(f["geometry"]).buffer(0)
        minx, miny, maxx, maxy = g.bounds
        geoms.append((i, minx, miny, maxx, maxy, g))
    return geoms


def aggregate_and_merge(api_items, geojson_path: Path, hosp_points_path: Path,
                        gen_out_csv: Path = GEN_OUT_CSV):
    """
    HIRA sgguCd 는 행정표준코드가 아닌 자체코드(110001 등)라 geojson 코드와 매칭되지 않는다.
    그래서 좌표(XPos/YPos) 기반 공간조인(point-in-polygon)으로 시군구에 배정한다.
      - 종합병원(clCdNm='종합병원', 상급종합 포함): API 좌표로 집계 -> hosp_gen_cnt
      - 상급종합병원: HIRA가 별도 라벨을 주지 않으므로(상급종합도 clCdNm='종합병원'),
        권위 명단인 공식 병원 포인트(hospitals.geojson) 좌표로 집계 -> hosp_sup_cnt
    """
    import csv as _csv
    from shapely.geometry import Point
    from collections import Counter
    print("\n" + "=" * 60)
    print("[2] 좌표 공간조인으로 시군구별 종합병원/상급종합 집계 → geojson 병합")
    print("=" * 60)

    dist = Counter(str(it.get("clCdNm", "")).strip() for it in api_items)
    print("clCdNm 분포(상위):", dist.most_common(10))

    if not geojson_path.exists():
        print(f"[ERROR] 병합 대상 없음: {geojson_path}")
        return
    fc = json.loads(geojson_path.read_text(encoding="utf-8"))
    feats = fc.get("features", [])
    geoms = _build_locator(feats)

    def locate(lng, lat):
        pt = Point(lng, lat)
        for i, minx, miny, maxx, maxy, g in geoms:
            if minx <= lng <= maxx and miny <= lat <= maxy and g.contains(pt):
                return i
        return None

    sup = [0] * len(feats)
    gen = [0] * len(feats)
    gen_rows = []
    gen_miss = 0

    # (a) 종합병원: API 좌표 공간조인
    for it in api_items:
        if classify_clcdnm(it) != "gen":
            continue
        try:
            lng = float(it.get("XPos")); lat = float(it.get("YPos"))
        except (TypeError, ValueError):
            gen_miss += 1; continue
        idx = locate(lng, lat)
        if idx is None:
            gen_miss += 1; continue
        gen[idx] += 1
        pr = feats[idx].get("properties", {})
        gen_rows.append({
            "name": it.get("yadmNm", ""), "clCdNm": it.get("clCdNm", ""),
            "addr": it.get("addr", ""), "drTotCnt": it.get("drTotCnt", ""),
            "lng": lng, "lat": lat,
            "sigungu_code": pr.get("code", ""), "sigungu_name": pr.get("name", ""),
        })

    # (b) 상급종합: 공식 병원 좌표 공간조인
    sup_miss = 0
    hosp_point_meta = {}
    if hosp_points_path.exists():
        hp = json.loads(hosp_points_path.read_text(encoding="utf-8"))
        hosp_point_meta = hp.get("meta", {})
        for f in hp.get("features", []):
            try:
                lng, lat = f["geometry"]["coordinates"][:2]
            except Exception:
                sup_miss += 1; continue
            idx = locate(float(lng), float(lat))
            if idx is None:
                sup_miss += 1; continue
            sup[idx] += 1
    else:
        print(f"[WARN] 상급종합 포인트 파일 없음: {hosp_points_path}")

    # geojson in-place 병합
    for i, f in enumerate(feats):
        pr = f.setdefault("properties", {})
        pr["hosp_sup_cnt"] = sup[i]
        pr["hosp_gen_cnt"] = gen[i]
        pr["has_general_hosp"] = bool(gen[i] > 0)   # 2차의료 접근 보조 플래그

    meta = fc.setdefault("meta", {})
    meta["hosp_source"] = "HIRA 종합병원 + 보건복지부 제5기 상급종합병원 (좌표 공간조인)"
    meta["hosp_period"] = hosp_point_meta.get("period")
    meta["hosp_fields"] = (f"hosp_sup_cnt=상급종합병원(보건복지부 공식 {sum(sup)}개), "
                           "hosp_gen_cnt=종합병원(상급 포함, clCdNm='종합병원'), "
                           "has_general_hosp=시군구내 종합병원 유무")

    geojson_path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] 병합 저장: {geojson_path}")
    print(f"  종합병원 집계: {sum(gen)}개 (좌표없음/경계밖 {gen_miss}건 제외)")
    print(f"  상급종합 집계: {sum(sup)}개 (배정실패 {sup_miss}건)")
    gen0 = sum(1 for x in gen if x == 0)
    print(f"  종합병원 보유 시군구: {len(feats) - gen0} / {len(feats)}  |  0개(2차 의료 사각): {gen0}")

    if gen_rows:
        with gen_out_csv.open("w", encoding="utf-8-sig", newline="") as fp:
            w = _csv.DictWriter(fp, fieldnames=list(gen_rows[0].keys()))
            w.writeheader(); w.writerows(gen_rows)
        print(f"  종합병원 목록 저장: {gen_out_csv} ({len(gen_rows)}행)")


def load_cached_general_hospitals(cache_path: Path):
    """이전 HIRA 수집 CSV의 원 좌표를 API item 형태로 되돌린다."""
    import csv
    rows = []
    with cache_path.open(encoding="utf-8-sig", newline="") as fp:
        for row in csv.DictReader(fp):
            rows.append({
                "yadmNm": row.get("name", ""),
                "clCdNm": row.get("clCdNm", ""),
                "addr": row.get("addr", ""),
                "drTotCnt": row.get("drTotCnt", ""),
                "XPos": row.get("lng", ""),
                "YPos": row.get("lat", ""),
            })
    if not rows:
        raise ValueError(f"HIRA 캐시가 비어 있습니다: {cache_path}")
    return rows


# ── 메인 ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="HIRA 병원정보 수집 → 교차검증 + 시군구 집계 병합")
    parser.add_argument("--dry-run", action="store_true", help="건수만 확인(병합/검증 미수행)")
    parser.add_argument("--from-cache", type=Path, default=None,
                        help="네트워크 재수집 없이 기존 종합병원 CSV 좌표를 새 경계에 재조인")
    parser.add_argument("--geojson", type=Path, default=BIVARIATE_GEOJSON)
    parser.add_argument("--hospital-points", type=Path, default=HOSP_POINTS_GEOJSON)
    parser.add_argument("--output-csv", type=Path, default=GEN_OUT_CSV)
    args = parser.parse_args()

    if args.from_cache is not None:
        items = load_cached_general_hospitals(args.from_cache)
        print(f"[CACHE] 종합병원 좌표 {len(items)}건 로드: {args.from_cache}")
        if args.dry_run:
            return 0
        aggregate_and_merge(items, args.geojson, args.hospital_points, args.output_csv)
        print("\n완료.")
        return 0

    raw_key = resolve_api_key()
    if not raw_key:
        # 키 없음: 401 을 받기도 전에 안내하고 우아하게 종료(비정상 종료 아님).
        print("\n[안내] DATAGOKR_API_KEY 가 비어 있습니다.")
        print("  - 환경변수 DATAGOKR_API_KEY 를 설정하거나")
        print("  - h3_tag/config.py 의 DATAGOKR_API_KEY 값을 채운 뒤 다시 실행하세요.")
        print("  키가 없으므로 이번 실행에서는 데이터를 받지 못합니다(파일은 그대로 유지).")
        return 0

    # data.go.kr 키는 보통 URL-encoded. requests 가 다시 인코딩하면 % 가 깨지므로 unquote 후 전달.
    api_key = unquote(raw_key)

    items, ok, reason = fetch_all_hospitals(api_key)

    if not ok:
        # 401 등은 우아하게 보고하고 정상 종료(파일은 작성 완료 상태로 둠).
        print(f"\n[안내] API 수집 실패: {reason}")
        print("  키가 미등록/만료이거나 해당 서비스 미승인일 수 있습니다.")
        print("  파일은 그대로 두고 종료합니다(비정상 종료 아님).")
        return 0

    print(f"\n수집 완료: {len(items):,}건")
    if items:
        cols = sorted(items[0].keys())
        print(f"  item 키: {cols}")

    # 종별 분포 요약
    n_sup = sum(1 for it in items if classify_clcdnm(it) == "sup")
    n_gen = sum(1 for it in items if classify_clcdnm(it) == "gen")
    print(f"  종별 — 상급종합: {n_sup}, 종합: {n_gen}")

    if args.dry_run:
        print(f"\n[DRY-RUN] 전체 {len(items):,}건 / 상급종합 {n_sup} / 종합 {n_gen} (병합·검증 미수행)")
        return 0

    if not items:
        print("[WARN] 수집 건수 0 — 교차검증/병합을 건너뜁니다.")
        return 0

    cross_check_superior(items, HOSPITALS_JSON)       # (1)
    aggregate_and_merge(items, args.geojson, args.hospital_points, args.output_csv)  # (2) 좌표 공간조인
    print("\n완료.")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
