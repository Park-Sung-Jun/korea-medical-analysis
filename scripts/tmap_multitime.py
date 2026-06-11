"""
TMAP 다시간대 교통 접근성 집계.

cross_validate_tmap.py --slot <라벨> 로 여러 시간대(평일 오전/오후/저녁/주말)에 반복 수집한
data/tmap_slots/*.csv 들을 시군구별로 병합해 소요시간·ratio의 중앙값과 범위(첨두~비첨두)를
data/tmap_multitime.json 으로 산출한다.

주의: TMAP 무료 API는 '수집 시점'의 실시간 교통만 준다(미래 예약 라우팅 없음). 따라서 진짜
다시간대 대표값을 얻으려면 실제 벽시계 시각에 맞춰 아래 cron 예시처럼 반복 수집해야 한다.
1회 스냅샷은 그 시각의 근사일 뿐이다(여러 날 누적 권장).

usage:
  python scripts/tmap_multitime.py          # data/tmap_slots/*.csv 병합 → tmap_multitime.json
  python scripts/tmap_multitime.py --cron   # cron/작업스케줄러 예시 출력

cron 예시(평일 08/14/19시 + 일요일 14시, 전체 250):
  0 8  * * 1-5  cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekday_am
  0 14 * * 1-5  cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekday_pm
  0 19 * * 1-5  cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekday_eve
  0 14 * * 0    cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekend
  0 3  * * 1    cd /opt/iso && python scripts/tmap_multitime.py   # 주 1회 병합
"""
import argparse
import csv
import json
import sys
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SLOT_DIR = DATA / "tmap_slots"

CRON = """\
# /etc/cron.d 또는 crontab -e (서버 시간대 KST 가정). 경로는 실제 배포 경로로 교체.
0 8  * * 1-5  cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekday_am
0 14 * * 1-5  cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekday_pm
0 19 * * 1-5  cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekday_eve
0 14 * * 0    cd /opt/iso && python scripts/cross_validate_tmap.py --full --slot weekend
0 3  * * 1    cd /opt/iso && python scripts/tmap_multitime.py
# Windows 작업 스케줄러: 위 명령을 각 트리거(요일/시각)로 등록.
"""


def load_slots():
    """data/tmap_slots/*.csv → {code: {slot: row}} 누적(같은 슬롯 재수집 시 최신 덮어씀)."""
    if not SLOT_DIR.exists():
        return {}, []
    by_code, slots = {}, []
    for csv_path in sorted(SLOT_DIR.glob("*.csv")):
        slot = csv_path.stem
        slots.append(slot)
        with csv_path.open(encoding="utf-8-sig", newline="") as fp:
            for r in csv.DictReader(fp):
                code = r.get("code")
                if not code:
                    continue
                try:
                    r["_tmap"] = float(r["tmap_min"])
                    r["_ratio"] = float(r["ratio"]) if r.get("ratio") else None
                except (ValueError, KeyError):
                    continue
                by_code.setdefault(code, {})[slot] = r
    return by_code, slots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cron", action="store_true", help="cron/스케줄러 예시 출력 후 종료")
    args = ap.parse_args()
    if args.cron:
        print(CRON)
        return 0

    by_code, slots = load_slots()
    if not by_code:
        print(f"수집된 슬롯 CSV가 없습니다: {SLOT_DIR}/*.csv\n"
              f"먼저 'cross_validate_tmap.py --full --slot <라벨>'을 시간대별로 실행하세요.\n"
              f"(예시는 --cron 으로 확인)", file=sys.stderr)
        return 1

    out = {}
    for code, per in by_code.items():
        tmaps = [v["_tmap"] for v in per.values() if v.get("_tmap") is not None]
        ratios = [v["_ratio"] for v in per.values() if v.get("_ratio") is not None]
        if not tmaps:
            continue
        any_row = next(iter(per.values()))
        out[code] = {
            "sido": any_row.get("sido"), "name": any_row.get("name"),
            "ors_min": float(any_row.get("ors_min") or 0),
            "tmap_min_median": round(median(tmaps), 1),
            "tmap_min_min": round(min(tmaps), 1), "tmap_min_max": round(max(tmaps), 1),
            "ratio_median": round(median(ratios), 2) if ratios else None,
            "ratio_min": round(min(ratios), 2) if ratios else None,
            "ratio_max": round(max(ratios), 2) if ratios else None,
            "slots": sorted(per.keys()),
        }
    dest = DATA / "tmap_multitime.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {dest}  (시군구 {len(out)}곳 · 슬롯 {sorted(set(slots))})")
    # 변동 큰 곳 요약
    spread = sorted(
        (v for v in out.values() if v["ratio_max"] and v["ratio_min"]),
        key=lambda v: -(v["ratio_max"] - v["ratio_min"]))[:8]
    if spread:
        print("시간대 변동(첨두~비첨두 ratio 범위) 큰 곳:")
        for v in spread:
            print(f"  {v['sido']} {v['name']}: ratio {v['ratio_min']}~{v['ratio_max']} "
                  f"(TMAP {v['tmap_min_min']}~{v['tmap_min_max']}분)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
