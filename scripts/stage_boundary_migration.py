"""새 행정경계용 의료지표 GeoJSON을 운영 파일과 분리해 준비한다.

기본 접근성·고령화 필드는 최신 경계/인구에서 다시 계산하고, 코드가 유지된
지역의 네트워크 비용 지표만 기존 파일에서 이어받는다. 폐지된 코드를 새 코드로
복제하거나 분할하지 않는다.
"""
import argparse
import json
import sys
from pathlib import Path

try:
    from .combine_bivariate import build_bivariate
except ImportError:  # `python scripts/stage_boundary_migration.py` 직접 실행
    from combine_bivariate import build_bivariate


HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
DEFAULT_ISO = DATA / "isochrones.geojson"
DEFAULT_SGG = DATA / "sigungu.geojson"
DEFAULT_OLD = DATA / "sigungu_bivariate.geojson"
DEFAULT_OUT = DATA / "sigungu_bivariate.next.geojson"

REQUIRED_BUCHEON_CODES = {"41192", "41194", "41196"}
RETIRED_CODES = {"41190"}
BASE_FIELDS = {
    "code",
    "name",
    "sido",
    "aging_index",
    "pop_total",
    "elderly_share",
    "youth_share",
    "access_min",
    "access_min_exact",
    "access_band",
    "access_suspect",
    "access_class",
    "access_label",
    "aging_class",
    "aging_label",
    "bivar_class",
}


def _codes(document):
    return [str(f.get("properties", {}).get("code", "")).strip() for f in document.get("features", [])]


def _reclassify_tmap(properties):
    value = properties.get("access_min_tmap")
    if value is None:
        return
    value = float(value)
    access_class = 1 if value <= 30 else (2 if value <= 60 else 3)
    properties["access_class_tmap"] = access_class
    aging_class = properties.get("aging_class")
    properties["bivar_class_tmap"] = f"A{aging_class}B{access_class}" if aging_class else None


def carry_forward_enrichments(new_document, old_document):
    """동일 코드 지역의 비기본 지표만 복사하고 새 분류 기준을 다시 적용한다."""
    new_codes = set(_codes(new_document))
    old_by_code = {
        str(f.get("properties", {}).get("code", "")).strip(): f.get("properties", {})
        for f in old_document.get("features", [])
    }
    matched = 0
    for feature in new_document.get("features", []):
        properties = feature.setdefault("properties", {})
        code = str(properties.get("code", "")).strip()
        old_properties = old_by_code.get(code)
        if old_properties is None:
            continue
        matched += 1
        for key, value in old_properties.items():
            if key not in BASE_FIELDS:
                properties[key] = value
        _reclassify_tmap(properties)

    new_meta = new_document.setdefault("meta", {})
    for key, value in old_document.get("meta", {}).items():
        if key not in new_meta:
            new_meta[key] = value
    retired = sorted(set(old_by_code) - new_codes)
    return {"matched": matched, "new": len(new_codes) - matched, "retired": retired}


def validate_stage(document, expected_count=252):
    codes = _codes(document)
    if len(codes) != expected_count:
        raise ValueError(f"시군구 수 불일치: {len(codes)} != {expected_count}")
    if any(not code for code in codes):
        raise ValueError("빈 시군구 코드가 있습니다.")
    if len(set(codes)) != len(codes):
        raise ValueError("중복 시군구 코드가 있습니다.")
    retired = sorted(RETIRED_CODES & set(codes))
    if retired:
        raise ValueError(f"폐지 코드가 남아 있습니다: {', '.join(retired)}")
    missing = sorted(REQUIRED_BUCHEON_CODES - set(codes))
    if missing:
        raise ValueError(f"부천시 신설 구 코드가 없습니다: {', '.join(missing)}")
    return {"count": len(codes), "unique": len(set(codes)), "bucheon": sorted(REQUIRED_BUCHEON_CODES)}


def parse_args():
    parser = argparse.ArgumentParser(description="최신 행정경계용 의료지표 스테이징 파일 생성")
    parser.add_argument("--iso", type=Path, default=DEFAULT_ISO)
    parser.add_argument("--sgg", type=Path, default=DEFAULT_SGG)
    parser.add_argument("--old", type=Path, default=DEFAULT_OLD)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--expect-count", type=int, default=252)
    return parser.parse_args()


def main():
    args = parse_args()
    iso = json.loads(args.iso.read_text(encoding="utf-8"))
    sgg = json.loads(args.sgg.read_text(encoding="utf-8"))
    old = json.loads(args.old.read_text(encoding="utf-8"))
    staged = build_bivariate(iso, sgg)
    carry_stats = carry_forward_enrichments(staged, old)
    validation = validate_stage(staged, expected_count=args.expect_count)
    staged.setdefault("meta", {})["migration"] = {
        "method": "latest boundary base + same-code enrichment carry-forward",
        "copied_existing_codes": carry_stats["matched"],
        "new_codes_without_parent_value_copy": carry_stats["new"],
        "retired_codes": carry_stats["retired"],
    }
    args.out.write_text(json.dumps(staged, ensure_ascii=False), encoding="utf-8")
    print(
        f"저장: {args.out} | {validation['count']}개 고유 코드 | "
        f"기존 지표 {carry_stats['matched']}개 보존 | 신규 {carry_stats['new']}개"
    )
    print(f"폐지 코드: {', '.join(carry_stats['retired']) or '없음'}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
