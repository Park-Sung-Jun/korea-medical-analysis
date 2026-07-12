"""제5기 상급종합병원 공식 명단과 일치하는 배포용 병원 목록을 준비한다."""
import argparse
import copy
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SOURCE = DATA / "hospitals.json"
OUT = DATA / "hospitals.next.json"
OFFICIAL_SOURCE = (
    "https://www.mohw.go.kr/board.es?mid=a10503000000&bid=0027&act=view&list_no=1479568"
)


def prepare_official_hospitals(source, excluded_names, expected_count=47):
    result = copy.deepcopy(source)
    hospitals = result.get("hospitals", [])
    names = [str(h.get("name", "")).strip() for h in hospitals]
    if len(names) != len(set(names)):
        raise ValueError("병원명이 중복되어 있습니다.")
    found_exclusions = set(names) & set(excluded_names)
    if found_exclusions != set(excluded_names):
        missing = sorted(set(excluded_names) - found_exclusions)
        raise ValueError(f"제외 대상 병원이 원본에 없습니다: {', '.join(missing)}")
    result["hospitals"] = [h for h in hospitals if str(h.get("name", "")).strip() not in excluded_names]
    if len(result["hospitals"]) != expected_count:
        raise ValueError(f"공식 병원 수 불일치: {len(result['hospitals'])} != {expected_count}")
    meta = dict(result.get("meta", {}))
    meta.update({
        "title": "제5기 상급종합병원 공식 지정 기관",
        "period": "2024-01-01~2026-12-31",
        "source": "보건복지부 제5기 상급종합병원 지정 결과",
        "source_url": OFFICIAL_SOURCE,
        "count": expected_count,
        "excluded_non_designated": sorted(excluded_names),
    })
    result["meta"] = meta
    return result


def main():
    parser = argparse.ArgumentParser(description="공식 제5기 상급종합병원 목록 생성")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--exclude", action="append", default=["강원대학교병원"])
    parser.add_argument("--expect-count", type=int, default=47)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    result = prepare_official_hospitals(source, set(args.exclude), args.expect_count)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {args.out} | 공식 지정 {len(result['hospitals'])}개")
    print(f"제외: {', '.join(result['meta']['excluded_non_designated'])}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
