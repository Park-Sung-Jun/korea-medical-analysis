// 지도 키 설정. 이 파일을 config.js 로 복사해 사용.
//  - VWorld/Kakao 키는 도메인 등록형이라 클라이언트 노출이 정상 → config.js 에 두고 커밋 가능.
//    (VWorld 콘솔에서 사용 도메인(localhost, 배포도메인) 등록/제한 권장)
//  - ORS 키는 도메인 제한이 안 되므로 config.js 에 두지 말 것:
//      · 공개 배포: 서버리스 함수 api/iso.js + Vercel 환경변수 ORS_KEY 사용
//      · 로컬 개발: config.local.js(.gitignore)에서 window.MAP_KEYS.ors 로 주입
window.MAP_KEYS = {
  vworld: "",   // 예: XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX (VWorld WMTS UUID, 도메인 등록형)
  kakao: "",    // MapLibre에선 카카오 타일 미사용(좌표계/SDK 비호환)
  ors: ""       // 비워둘 것(로컬은 config.local.js, 공개는 서버리스)
};
// 로컬 전용 config.local.js 예:
//   if (window.MAP_KEYS) window.MAP_KEYS.ors = "<ORS 키>";
