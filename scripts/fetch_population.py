"""KOSIS 월별 시군구 1세별 인구를 5세 구간으로 집계한다."""

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "data" / "population.json"
BOUNDARY = ROOT.parent / "shared_data" / "admin_boundaries" / "sgg.geojson"

API_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
ORG_ID = "101"
TABLE_ID = "DT_1B04006"
BANDS = [
    "0-4",
    "5-9",
    "10-14",
    "15-19",
    "20-24",
    "25-29",
    "30-34",
    "35-39",
    "40-44",
    "45-49",
    "50-54",
    "55-59",
    "60-64",
    "65-69",
    "70-74",
    "75-79",
    "80-84",
    "85-89",
    "90-94",
    "95-99",
    "100+",
]


def load_api_key():
    value = os.environ.get("KOSIS_API_KEY")
    if value:
        return value

    for path in (ROOT / ".env", Path.home() / ".claude" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("KOSIS_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    raise SystemExit("KOSIS_API_KEY가 없습니다. .env 또는 환경변수를 확인하세요.")


def parse_kosis_json(text):
    quoted = re.sub(r"(?<=[{,])\s*([A-Za-z0-9_]+)\s*:", r'"\1":', text)
    data = json.loads(quoted)
    if isinstance(data, dict):
        raise RuntimeError(
            f"KOSIS 오류 {data.get('err', 'unknown')}: "
            f"{data.get('errMsg', '응답 형식 오류')}"
        )
    return data


def request_rows(session, api_key, *, item_id, period=None, latest=False):
    params = {
        "method": "getList",
        "apiKey": api_key,
        "orgId": ORG_ID,
        "tblId": TABLE_ID,
        "format": "json",
        "prdSe": "M",
        "itmId": item_id,
        "objL1": "00" if latest else "ALL",
        "objL2": "000" if latest else "ALL",
    }
    if latest:
        params["newEstPrdCnt"] = "1"
    else:
        params["startPrdDe"] = period
        params["endPrdDe"] = period

    error = None
    for attempt in range(3):
        try:
            response = session.get(API_URL, params=params, timeout=120)
            response.raise_for_status()
            return parse_kosis_json(response.content.decode("utf-8", errors="replace"))
        except (requests.RequestException, RuntimeError, json.JSONDecodeError) as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"KOSIS 요청 3회 실패: {type(error).__name__}: {error}")


def latest_period(session, api_key):
    rows = request_rows(session, api_key, item_id="T2", latest=True)
    periods = sorted({str(row.get("PRD_DE", "")) for row in rows if row.get("PRD_DE")})
    if len(periods) != 1 or not re.fullmatch(r"\d{6}", periods[0]):
        raise RuntimeError(f"KOSIS 최신 기준월을 확정할 수 없습니다: {periods}")
    return periods[0]


def _age_band_index(label):
    if label == "계":
        return None
    match = re.fullmatch(r"(\d+)세(?: 이상)?", str(label).strip())
    if not match:
        raise ValueError(f"알 수 없는 연령 구분: {label!r}")
    age = int(match.group(1))
    if not 0 <= age <= 100:
        raise ValueError(f"지원 범위를 벗어난 연령: {age}")
    return min(age // 5, len(BANDS) - 1)


def _aggregate_metric(rows, expected_period):
    regions = {}
    seen = set()
    for row in rows:
        period = str(row.get("PRD_DE", ""))
        if period != expected_period:
            raise ValueError(
                f"기준월 불일치: 기대 {expected_period}, 응답 {period or '없음'}"
            )
        index = _age_band_index(row.get("C2_NM", ""))
        if index is None:
            continue

        code = str(row.get("C1", "")).strip()
        name = str(row.get("C1_NM", "")).strip()
        age_name = str(row.get("C2_NM", "")).strip()
        if not code or not name:
            raise ValueError("행정구역 코드 또는 이름이 비어 있습니다.")
        key = (code, age_name)
        if key in seen:
            raise ValueError(f"지역·연령 중복: {code} {age_name}")
        seen.add(key)

        try:
            value = int(str(row.get("DT", "")).replace(",", ""))
        except ValueError as exc:
            raise ValueError(f"인구값이 정수가 아닙니다: {code} {age_name}") from exc
        if value < 0:
            raise ValueError(f"음수 인구값: {code} {age_name}")

        region = regions.setdefault(code, {"name": name, "values": [0] * len(BANDS)})
        if region["name"] != name:
            raise ValueError(f"동일 코드 이름 불일치: {code}")
        region["values"][index] += value
    return regions


def build_population_document(male_rows, female_rows, period):
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError(f"기준월 형식 오류: {period}")
    male = _aggregate_metric(male_rows, period)
    female = _aggregate_metric(female_rows, period)
    if set(male) != set(female):
        only_male = sorted(set(male) - set(female))[:5]
        only_female = sorted(set(female) - set(male))[:5]
        raise ValueError(
            f"성별 지역 코드 불일치: 남성만={only_male}, 여성만={only_female}"
        )

    regions = {}
    for code in sorted(male):
        if male[code]["name"] != female[code]["name"]:
            raise ValueError(f"성별 지역 이름 불일치: {code}")
        m_values = male[code]["values"]
        f_values = female[code]["values"]
        regions[code] = {
            "name": male[code]["name"],
            "m": m_values,
            "f": f_values,
            "total": sum(m_values) + sum(f_values),
        }

    return {
        "meta": {
            "date": f"{period[:4]}-{period[4:]}",
            "source": "KOSIS 주민등록인구",
            "organization": "행정안전부",
            "table_id": TABLE_ID,
            "bands": BANDS,
        },
        "regions": regions,
    }


def validate_boundary_coverage(document):
    boundary = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    boundary_codes = {
        str(feature["properties"]["sggcd"])
        for feature in boundary.get("features", [])
    }
    region_codes = set(document["regions"])
    missing = sorted(boundary_codes - region_codes)
    if missing:
        raise ValueError(f"공유 경계 인구 미매칭 {len(missing)}개: {missing[:10]}")
    return len(boundary_codes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", help="기준월 YYYYMM. 생략하면 KOSIS 최신월")
    args = parser.parse_args()

    api_key = load_api_key()
    with requests.Session() as session:
        period = args.period or latest_period(session, api_key)
        if not re.fullmatch(r"\d{6}", period):
            raise SystemExit(f"잘못된 --period: {period}")
        print(f"KOSIS 주민등록인구 기준월: {period}")
        male_rows = request_rows(session, api_key, item_id="T3", period=period)
        female_rows = request_rows(session, api_key, item_id="T4", period=period)

    document = build_population_document(male_rows, female_rows, period)
    document["meta"]["fetched_at"] = datetime.now(timezone.utc).isoformat()
    boundary_count = validate_boundary_coverage(document)

    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    temporary.replace(OUT)
    print(
        f"저장: {OUT}  지역={len(document['regions'])}, "
        f"공유 시군구 매칭={boundary_count}"
    )


if __name__ == "__main__":
    main()
