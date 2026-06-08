"""
Netlify/Vercel 드롭용 정적 배포 번들 생성: isochrone_map/dist/
  - index.html  = report.html (랜딩), iframe src 를 map.html 로 교체
  - map.html    = index.html (인터랙티브 지도)
  - config.js   = ORS 키 제거(공개 노출 방지). vworld/kakao 빈 값 유지
  - data/       = 프론트가 실제 fetch 하는 파일만 (geojson 3종 + report_stats.json)
node_modules·pop 원천·CSV·scripts 등은 제외한다.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

NEED_DATA = [
    "report_stats.json",
    "hospitals.geojson",
    "isochrones.geojson",
    "sigungu_bivariate.geojson",
]


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "data").mkdir(parents=True)

    # 1) 리포트를 랜딩(index.html)으로, iframe 지도 경로 교체
    report = (ROOT / "report.html").read_text(encoding="utf-8")
    report = report.replace('src="index.html"', 'src="map.html"')
    (DIST / "index.html").write_text(report, encoding="utf-8")

    # 2) 지도 -> map.html (그대로)
    shutil.copy2(ROOT / "index.html", DIST / "map.html")

    # 3) config.js — ORS 키 제거(공개 배포 안전)
    cfg = (ROOT / "config.js").read_text(encoding="utf-8")
    import re
    cfg = re.sub(r'ors:\s*"[^"]*"', 'ors: ""   // 공개 배포: 키 비움(클릭형 등시선 비활성)', cfg)
    (DIST / "config.js").write_text(cfg, encoding="utf-8")

    # 4) 필요한 data 파일만
    total = 0
    for name in NEED_DATA:
        src = ROOT / "data" / name
        if not src.exists():
            print(f"[WARN] 없음: {src}")
            continue
        shutil.copy2(src, DIST / "data" / name)
        total += src.stat().st_size

    # 5) Netlify SPA 아님 — 정적 그대로. 캐시 헤더 힌트(netlify)
    (DIST / "netlify.toml").write_text(
        '[build]\n  publish = "."\n', encoding="utf-8")

    files = sorted(p.relative_to(DIST).as_posix() for p in DIST.rglob("*") if p.is_file())
    print("dist 생성:", DIST)
    for f in files:
        print("  ", f)
    print(f"data 합계: {total/1024:.0f} KB")


if __name__ == "__main__":
    main()
