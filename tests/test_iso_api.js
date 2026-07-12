const assert = require('node:assert/strict');
const test = require('node:test');

function mockResponse() {
  return {
    headers: {},
    statusCode: 200,
    body: null,
    setHeader(key, value) { this.headers[key] = value; },
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
    send(value) { this.body = value; return this; },
  };
}

test('limits repeated ORS proxy calls from the same forwarded address', async () => {
  process.env.ORS_API_KEY = 'test-key';
  global.fetch = async () => ({
    ok: true,
    status: 200,
    text: async () => '{"type":"FeatureCollection","features":[]}',
  });
  delete require.cache[require.resolve('../api/iso.js')];
  const handler = require('../api/iso.js');

  let upstreamCalls = 0;
  global.fetch = async () => {
    upstreamCalls += 1;
    return {
      ok: true,
      status: 200,
      text: async () => '{"type":"FeatureCollection","features":[]}',
    };
  };

  let last;
  for (let i = 0; i < 31; i += 1) {
    last = mockResponse();
    await handler({
      headers: {
        referer: 'https://korea-medical-analysis.vercel.app/',
        'x-forwarded-for': '203.0.113.10',
      },
      query: { lng: '127.0', lat: '37.5' },
    }, last);
  }

  assert.equal(last.statusCode, 429);
  assert.equal(upstreamCalls, 30);
  assert.equal(last.headers['Retry-After'], '60');
});
