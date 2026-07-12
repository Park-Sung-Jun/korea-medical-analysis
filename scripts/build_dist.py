"""
Vercel/Netlify 드롭용 정적 배포 번들 생성: korea-medical-analysis/dist/

현행 파일 역할(2026 기준):
  - index.html = 랜딩 리포트(의료취약지) — iframe으로 map.html 임베드
  - map.html   = 인터랙티브 지도(MapLibre)
  - config.js  = ORS 키 제거(공개 노출 방지). vworld/kakao 빈 값 유지(VWorld는 도메인등록형)
  - data/      = 리포트·지도가 실제 fetch 하는 파일만 복사

node_modules·KOSIS 원본 CSV·scripts·synthetic·시크릿(config.local.js)은 제외한다.
"""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# 리포트(index.html) + 지도(map.html)가 런타임에 fetch 하는 데이터 전부
NEED_DATA = [
    "report_stats.json",          # 리포트 핵심 지표
    "checkup_stats.json",         # 검진 수검률
    "checkup_trend.json",         # 검진 추이·성별
    "health_indicator_stats.json",  # 유질환·대사증후군
    "emergency_stats.json",       # 응급 통계(있으면)
    "sigungu_bivariate.geojson",  # 250 시군구 전수(VVI·지도 공용)
    "hospitals.geojson",          # 병원 위치
    "isochrones.geojson",         # 상급종합 등시선
    "hospital_isochrones.geojson",  # 병원별 등시선(커버리지)
    "emergency.geojson",          # 응급기관 위치(있으면)
    "pop_pyramid.json",           # 인구 피라미드(호버)
]

# 정적 페이지 + 클라이언트 스크립트(있는 것만 복사)
PAGES = ["index.html", "map.html", "app.js", "styles.css"]

# 배포 설정·서버리스 함수·공유 브랜드 자산
EXTRA_FILES = [
    "vercel.json",
    "api/iso.js",
    "assets/wavenet_logo2.svg",
]


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "data").mkdir(parents=True)

    for name in PAGES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, DIST / name)

    for name in EXTRA_FILES:
        src = ROOT / name
        if not src.exists():
            raise SystemExit(f"필수 배포 파일 누락: {name}")
        dst = DIST / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # config.js — ORS 키 제거(공개 배포 안전). VWorld는 도메인등록형이라 유지.
    cfg_src = ROOT / "config.js"
    if cfg_src.exists():
        cfg = cfg_src.read_text(encoding="utf-8")
        cfg = re.sub(r'ors:\s*"[^"]*"',
                     'ors: ""   // 공개 배포: 키 비움(클릭형 등시선은 /api/iso 프록시 사용)', cfg)
        (DIST / "config.js").write_text(cfg, encoding="utf-8")

    total, missing = 0, []
    for name in NEED_DATA:
        src = ROOT / "data" / name
        if not src.exists():
            missing.append(name)
            continue
        shutil.copy2(src, DIST / "data" / name)
        total += src.stat().st_size

    (DIST / "netlify.toml").write_text('[build]\n  publish = "."\n', encoding="utf-8")

    files = sorted(p.relative_to(DIST).as_posix() for p in DIST.rglob("*") if p.is_file())
    print("dist 생성:", DIST)
    for f in files:
        print("  ", f)
    print(f"data 합계: {total / 1024:.0f} KB")
    if missing:
        print("[WARN] 누락(런타임에서 graceful 처리되나 확인 권장):", ", ".join(missing))


if __name__ == "__main__":
    main()
