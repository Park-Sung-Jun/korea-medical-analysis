# 배포 가이드 (Netlify / Vercel)

정적 배포 번들은 `dist/`에 생성합니다.
랜딩 = 리포트(`index.html`), 지도 = `map.html`. 데이터는 `dist/data/`.

## 방법 A — Netlify Drop (가장 쉬움, CLI/계정 가입만)
1. https://app.netlify.com/drop 접속 (로그인)
2. **`dist` 폴더를 통째로 드래그&드롭** (또는 `dist.zip` 업로드)
3. 즉시 `https://<random>.netlify.app` 공개 URL 발급. Site settings에서 이름 변경 가능.

## 방법 B — Vercel
- CLI: `npm i -g vercel` → `cd dist && vercel --prod`
- 또는 vercel.com 대시보드에서 `dist` 폴더 import

## 배포 전 점검(이미 처리됨)
- `node_modules`, `population.json`(재생성 원천), CSV, scripts 제외
- **ORS 키는 브라우저에 넣지 않음**: Vercel 프로젝트의 `ORS_API_KEY` 환경변수와 `api/iso.js` 프록시를 사용합니다.
  - 환경변수가 없으면 메인 정적 분석은 동작하지만 "여기서 등시선"만 사용할 수 없습니다.
  - VWorld 배경지도를 쓰려면 `vworld` 키 + 발급 콘솔에 배포 도메인(예: `https://<name>.netlify.app`) 등록.
- 데이터는 전부 공개 출처(보건복지부·KOSIS·HIRA·OSM)라 공개 안전.

## 재빌드
데이터/리포트 수정 후: `python scripts/build_dist.py` → `dist/` 갱신 → 다시 드롭.
