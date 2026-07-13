# 배포 가이드 (Vercel)

정적 배포 번들은 `dist/`에 생성합니다.
랜딩 = 리포트(`index.html`), 지도 = `map.html`. 데이터는 `dist/data/`.

## 배포
- CLI: `npm i -g vercel` → `cd dist && vercel --prod`
- 또는 vercel.com 대시보드에서 `dist` 폴더 import
- 정본 프로젝트와 URL은 `korea-medical-analysis` / `https://korea-medical-analysis.vercel.app`입니다.

## 배포 전 점검(이미 처리됨)
- `node_modules`, `population.json`(재생성 원천), CSV, scripts 제외
- **ORS 키는 브라우저에 넣지 않음**: Vercel 프로젝트의 `ORS_API_KEY` 환경변수와 `api/iso.js` 프록시를 사용합니다.
  - 환경변수가 없으면 메인 정적 분석은 동작하지만 "여기서 등시선"만 사용할 수 없습니다.
  - VWorld 배경지도를 쓰려면 `vworld` 키 + 발급 콘솔에 정본 배포 도메인을 등록.
- 데이터는 전부 공개 출처(보건복지부·KOSIS·HIRA·OSM)라 공개 안전.

## 재빌드
데이터/리포트 수정 후: `python scripts/build_dist.py` → `dist/` 갱신 → Vercel에 배포.
