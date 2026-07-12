// Vercel 서버리스 함수 — OpenRouteService 등시선 프록시.
// ORS 키를 서버측 환경변수(ORS_KEY)에 보관해 브라우저로 노출하지 않는다.
// 호출: /api/iso?lng=127.0&lat=37.5  → ORS isochrone GeoJSON 반환.
// 한국 영역 경계(이 밖의 좌표는 거부 → 일반 ORS 프록시 남용 방지)
const KR = { minLng: 124, maxLng: 132, minLat: 33, maxLat: 39 };

// 허용 호스트 완전일치 목록. 추가 운영 도메인은 ALLOWED_HOST(쉼표구분 가능) 환경변수로 지정.
// 과거 host.endsWith('.vercel.app')는 임의의 *.vercel.app(공격자 배포 포함)을 통과시켜
// 오픈프록시·ORS 쿼터 남용 표면이 되므로 제거하고 완전일치로 좁힘.
const ALLOWED_HOSTS = new Set(
  ['localhost', '127.0.0.1', 'korea-medical-analysis.vercel.app'].concat(
    (process.env.ALLOWED_HOST || '').split(',').map((s) => s.trim()).filter(Boolean)
  )
);

// 서버리스 인스턴스별 보조 제한. 분산 인스턴스 전체를 묶는 WAF 제한을 대체하지는 않지만,
// 동일 출발지의 연속적인 캐시 미스가 ORS 쿼터를 소진하는 것을 완화한다.
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 30;
const rateBuckets = new Map();

function takeRateLimit(req, now = Date.now()) {
  const forwarded = req.headers['x-forwarded-for'];
  const address = String(forwarded || req.socket?.remoteAddress || 'unknown').split(',')[0].trim();
  let bucket = rateBuckets.get(address);
  if (!bucket || now >= bucket.resetAt) {
    bucket = { count: 0, resetAt: now + RATE_LIMIT_WINDOW_MS };
    rateBuckets.set(address, bucket);
  }
  bucket.count += 1;

  if (rateBuckets.size > 5000) {
    for (const [key, value] of rateBuckets) {
      if (now >= value.resetAt) rateBuckets.delete(key);
    }
  }

  return {
    allowed: bucket.count <= RATE_LIMIT_MAX,
    remaining: Math.max(0, RATE_LIMIT_MAX - bucket.count),
    resetSeconds: Math.max(1, Math.ceil((bucket.resetAt - now) / 1000)),
  };
}

module.exports = async (req, res) => {
  // 오리진 가드(공개 엔드포인트 쿼터 남용 완화 — referer는 스푸핑 가능, 심층방어용)
  const ref = req.headers.referer || req.headers.origin || '';
  let host = '';
  try { host = new URL(ref).hostname; } catch (_) { /* no/invalid referer */ }
  if (!ALLOWED_HOSTS.has(host)) {
    return res.status(403).json({ error: 'origin not allowed' });
  }

  const limit = takeRateLimit(req);
  res.setHeader('RateLimit-Limit', String(RATE_LIMIT_MAX));
  res.setHeader('RateLimit-Remaining', String(limit.remaining));
  res.setHeader('RateLimit-Reset', String(limit.resetSeconds));
  if (!limit.allowed) {
    res.setHeader('Retry-After', String(limit.resetSeconds));
    res.setHeader('Cache-Control', 'no-store');
    return res.status(429).json({ error: '요청이 너무 많습니다. 잠시 후 다시 시도하세요.' });
  }

  let lng = parseFloat(req.query.lng);
  let lat = parseFloat(req.query.lat);
  if (!isFinite(lng) || !isFinite(lat) ||
      lng < KR.minLng || lng > KR.maxLng || lat < KR.minLat || lat > KR.maxLat) {
    return res.status(400).json({ error: 'lng, lat 가 필요하며 한국 영역 안이어야 합니다.' });
  }
  // 좌표를 소수 3자리(~110m 격자)로 양자화 — 인근 클릭의 CDN 캐시 적중률을 높여
  // ORS 무료 쿼터 소모를 줄인다(표시·계산 모두 라운딩 좌표로 일관 사용).
  lng = Math.round(lng * 1000) / 1000;
  lat = Math.round(lat * 1000) / 1000;
  const key = process.env.ORS_API_KEY || process.env.ORS_KEY;
  if (!key) {
    return res.status(503).json({ error: '등시선 서비스를 사용할 수 없습니다.' });
  }
  try {
    const r = await fetch('https://api.openrouteservice.org/v2/isochrones/driving-car', {
      method: 'POST',
      headers: { Authorization: key, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        locations: [[lng, lat]],
        range: [900, 1800, 2700, 3600],
        range_type: 'time',
      }),
    });
    const text = await r.text();
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    // 성공(2xx)만 장기 CDN 캐시. 오류(429/5xx 등)는 캐시하지 않아 일시 장애가 고정되지 않게 함.
    if (r.ok) {
      res.setHeader('Cache-Control', 's-maxage=86400, stale-while-revalidate=604800');
    } else {
      res.setHeader('Cache-Control', 'no-store');
    }
    return res.status(r.status).send(text);
  } catch (e) {
    // 예외 상세(업스트림 URL·스택)를 응답에 노출하지 않음. 서버 로그로만 남김.
    console.error('ORS proxy error:', e);
    return res.status(502).json({ error: 'ORS 호출 실패' });
  }
};
