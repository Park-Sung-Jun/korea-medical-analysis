'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const baseUrl = (process.env.KMA_BASE_URL || 'https://korea-medical-analysis.vercel.app').replace(/\/$/, '');
const baseOrigin = new URL(baseUrl).origin;
const publicEntryUrls = (process.env.KMA_PUBLIC_ENTRY_URLS || '')
  .split(',')
  .map((value) => value.trim())
  .filter(Boolean);
const outputDir = process.env.KMA_QA_OUTPUT || path.join(os.tmpdir(), 'kma-playwright-qa');
const results = [];
const failures = [];

fs.rmSync(outputDir, { recursive: true, force: true });
fs.mkdirSync(outputDir, { recursive: true });

function shortError(error) {
  return String(error && (error.stack || error.message) || error).split('\n').slice(0, 4).join(' | ');
}

async function step(name, fn) {
  const started = Date.now();
  try {
    const value = await fn();
    results.push({ name, status: 'PASS', ms: Date.now() - started });
    process.stdout.write(`PASS ${name}\n`);
    return { ok: true, value };
  } catch (error) {
    const detail = shortError(error);
    results.push({ name, status: 'FAIL', ms: Date.now() - started, detail });
    failures.push({ name, detail });
    process.stdout.write(`FAIL ${name}: ${detail}\n`);
    return { ok: false };
  }
}

function diagnosticsFor(page, label, expectedOrigin = baseOrigin) {
  const data = {
    label,
    consoleErrors: [],
    pageErrors: [],
    badResponses: [],
    failedRequests: [],
  };
  page.on('console', (message) => {
    if (message.type() === 'error') data.consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => data.pageErrors.push(String(error.message || error)));
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const url = response.url();
    const type = response.request().resourceType();
    if (new URL(url).origin === expectedOrigin || ['document', 'script', 'stylesheet'].includes(type)) {
      data.badResponses.push({ status: response.status(), type, url });
    }
  });
  page.on('requestfailed', (request) => {
    const reason = request.failure() && request.failure().errorText || 'unknown';
    if (/ERR_ABORTED/i.test(reason)) return;
    const url = request.url();
    const type = request.resourceType();
    if (new URL(url).origin === expectedOrigin || ['document', 'script', 'stylesheet'].includes(type)) {
      data.failedRequests.push({ reason, type, url });
    }
  });
  return data;
}

function assertDiagnosticsClean(data) {
  const unique = (rows) => [...new Map(rows.map((row) => [JSON.stringify(row), row])).values()];
  data.badResponses = unique(data.badResponses);
  data.failedRequests = unique(data.failedRequests);
  data.consoleErrors = [...new Set(data.consoleErrors)];
  data.pageErrors = [...new Set(data.pageErrors)];
  assert.deepEqual(data.pageErrors, [], `${data.label} page errors: ${JSON.stringify(data.pageErrors)}`);
  assert.deepEqual(data.badResponses, [], `${data.label} HTTP failures: ${JSON.stringify(data.badResponses)}`);
  assert.deepEqual(data.failedRequests, [], `${data.label} request failures: ${JSON.stringify(data.failedRequests)}`);
  assert.deepEqual(data.consoleErrors, [], `${data.label} console errors: ${JSON.stringify(data.consoleErrors)}`);
}

async function gotoOk(page, route) {
  return gotoAbsoluteOk(page, `${baseUrl}${route}`, baseOrigin);
}

async function gotoAbsoluteOk(page, url, expectedOrigin) {
  const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  assert(response, `no navigation response for ${url}`);
  assert.equal(response.status(), 200, `${url} returned ${response.status()}`);
  await page.waitForFunction(() => document.readyState === 'complete', null, { timeout: 30_000 });
  assert.equal(new URL(page.url()).origin, expectedOrigin);
}

async function verifyDownload(page, selector, expectedName) {
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 45_000 }),
    page.locator(selector).click(),
  ]);
  assert.equal(download.suggestedFilename(), expectedName);
  const filePath = await download.path();
  assert(filePath, `download path missing for ${expectedName}`);
  assert(fs.statSync(filePath).size > 0, `empty download: ${expectedName}`);
}

async function verifyInternalLinks(page, expectedOrigin = baseOrigin) {
  const urls = await page.locator('a[href]').evaluateAll((anchors, origin) => {
    const found = new Set();
    for (const anchor of anchors) {
      const href = anchor.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('javascript:')) continue;
      const url = new URL(href, location.href);
      if (url.origin === origin) {
        url.hash = '';
        found.add(url.href);
      }
    }
    return [...found];
  }, expectedOrigin);
  for (const url of urls) {
    const response = await page.request.get(url, { failOnStatusCode: false });
    assert(response.status() < 400, `${url} returned ${response.status()}`);
  }
  return urls.length;
}

async function dashboardSuite(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await context.newPage();
  const diag = diagnosticsFor(page, 'dashboard');

  const loaded = await step('dashboard: load and render', async () => {
    await gotoOk(page, '/');
    await page.waitForFunction(() => document.querySelectorAll('#vviTbl tbody tr').length > 0, null, { timeout: 30_000 });
    await page.waitForFunction(() => document.querySelectorAll('canvas').length >= 4, null, { timeout: 30_000 });
    assert.equal(await page.title(), '대한민국 의료 접근성 분석');
    const targets = await page.locator('#kpiCards .v').evaluateAll((nodes) => nodes.map((node) => Number(node.dataset.t)));
    assert.equal(targets[0], 47);
    assert.equal(targets[1], 252);
  });
  if (!loaded.ok) {
    await context.close();
    return;
  }

  await step('dashboard: current data invariants', async () => {
    const routes = ['/data/hospitals.json', '/data/sigungu_bivariate.geojson', '/data/pop_pyramid.json'];
    const payloads = [];
    for (const route of routes) {
      const response = await page.request.get(`${baseUrl}${route}`);
      assert.equal(response.status(), 200, route);
      payloads.push(await response.json());
    }
    const hospitalCount = Array.isArray(payloads[0]) ? payloads[0].length : (payloads[0].features || payloads[0].hospitals || []).length;
    const regions = payloads[1].features || payloads[1];
    assert.equal(hospitalCount, 47);
    assert.equal(regions.length, 252);
    assert(!regions.some((row) => String((row.properties || row).code) === '41190'), 'retired code 41190 remains');
  });

  await step('dashboard: all internal navigation anchors', async () => {
    const anchors = await page.locator('.wn-topbar-actions a[href^="#"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')));
    assert(anchors.length >= 14, `only ${anchors.length} internal navigation links`);
    for (const href of anchors) {
      assert.equal(await page.locator(href).count(), 1, `missing target ${href}`);
      await page.locator(`.wn-topbar-actions a[href="${href}"]`).click({ force: true });
      await page.waitForFunction((id) => {
        const target = document.getElementById(id);
        if (!target) return false;
        const rect = target.getBoundingClientRect();
        return rect.top < innerHeight && rect.bottom > 56;
      }, href.slice(1), { timeout: 4_000 });
    }
  });

  await step('dashboard: all segmented controls', async () => {
    for (const selector of ['[data-acc]', '[data-m]', '[data-v]']) {
      const count = await page.locator(selector).count();
      assert(count > 1, `${selector} options missing`);
      for (let index = 0; index < count; index += 1) {
        const button = page.locator(selector).nth(index);
        await button.click();
        assert(await button.evaluate((node) => node.classList.contains('on')), `${selector}[${index}] did not activate`);
      }
    }
  });

  await step('dashboard: VVI weights, island filter, and table sorting', async () => {
    const weights = page.locator('input[data-wk]');
    assert((await weights.count()) >= 3, 'VVI weight sliders missing');
    for (let index = 0; index < await weights.count(); index += 1) {
      const slider = weights.nth(index);
      for (const value of ['0', '50', '100']) {
        await slider.fill(value);
        assert.equal(await slider.inputValue(), value);
      }
    }
    const island = page.locator('#vviIncludeIsland');
    for (const checked of [true, false]) {
      await island.setChecked(checked);
      assert.equal(await island.isChecked(), checked);
      assert((await page.locator('#vviTbl tbody tr').count()) > 0);
    }
    for (const table of ['#vviTbl', '#sidoRollupTbl']) {
      const headers = page.locator(`${table} th[data-k]`);
      assert((await headers.count()) > 2, `${table} sortable headers missing`);
      for (let index = 0; index < await headers.count(); index += 1) {
        await headers.nth(index).click();
        assert((await page.locator(`${table} tbody tr`).count()) > 0);
      }
    }
  });

  await step('dashboard: both CSV exports', async () => {
    await verifyDownload(page, '#vviExport', 'medical_vulnerability_index.csv');
    await verifyDownload(page, '#sidoExport', 'sido_rollup.csv');
  });

  await step('dashboard: top button and internal links', async () => {
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForFunction(() => document.getElementById('toTop').classList.contains('show'));
    await page.locator('#toTop').click();
    await page.waitForFunction(() => window.scrollY < 20);
    await verifyInternalLinks(page);
  });

  await step('dashboard: screenshot', async () => {
    await page.screenshot({ path: path.join(outputDir, 'dashboard-desktop.png'), fullPage: true });
  });
  await step('dashboard: browser diagnostics', async () => assertDiagnosticsClean(diag));
  await context.close();
}

async function searchRegion(page, query) {
  await page.locator('#searchInput').fill(query);
  await page.waitForSelector('#searchList:not([hidden]) .s-item');
  const items = page.locator('#searchList .s-item');
  for (let index = 0; index < await items.count(); index += 1) {
    if ((await items.nth(index).locator('.s-t').innerText()).trim() === '시군구') {
      const name = (await items.nth(index).locator('b').innerText()).trim();
      await items.nth(index).click();
      await page.waitForTimeout(1_300);
      assert.equal(await page.locator('#searchInput').inputValue(), name);
      return name;
    }
  }
  throw new Error(`no sigungu search result for ${query}`);
}

async function mapSuite(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    acceptDownloads: true,
    geolocation: { longitude: 126.978, latitude: 37.5665 },
    permissions: ['geolocation'],
  });
  const page = await context.newPage();
  const diag = diagnosticsFor(page, 'map');

  const loaded = await step('map: load data and WebGL map', async () => {
    await gotoOk(page, '/map.html');
    await page.waitForSelector('.maplibregl-canvas', { timeout: 45_000 });
    await page.waitForSelector('#legend h2', { timeout: 45_000 });
    assert.match(await page.locator('#badge-hosp').innerText(), /47개소/);
    assert.equal(await page.locator('#view-seg button').count(), 8);
  });
  if (!loaded.ok) {
    await context.close();
    return;
  }

  await step('map: all eight analytical views', async () => {
    const buttons = page.locator('#view-seg button[data-view]');
    const expected = ['iso', 'bivar', 'aging', 'checkup', 'cancer', 'er', 'disease', 'metabolic'];
    assert.deepEqual(await buttons.evaluateAll((nodes) => nodes.map((node) => node.dataset.view)), expected);
    for (const view of expected) {
      const button = page.locator(`#view-seg button[data-view="${view}"]`);
      await button.click();
      await page.waitForTimeout(250);
      assert(await button.evaluate((node) => node.classList.contains('on')), `${view} not active`);
      assert((await page.locator('#legend h2').innerText()).trim().length > 2, `${view} legend empty`);
      await page.waitForFunction((value) => new URLSearchParams(location.hash.slice(1)).get('v') === value, view);
    }
  });

  await step('map: layer toggles, ER tier, and opacity loop', async () => {
    await page.locator('[data-view="bivar"]').click();
    for (const id of ['toggle-sgg', 'toggle-hosp', 'toggle-traffic']) {
      const control = page.locator(`#${id}`);
      for (const checked of [false, true]) {
        await control.setChecked(checked);
        assert.equal(await control.isChecked(), checked, id);
      }
    }
    await page.locator('[data-view="er"]').click();
    assert(await page.locator('#er-tier-row').isVisible());
    for (const checked of [true, false]) {
      await page.locator('#toggle-ercenter').setChecked(checked);
      assert.equal(await page.locator('#toggle-ercenter').isChecked(), checked);
      assert.match(await page.locator('#legend h2').innerText(), checked ? /센터급/ : /지정기관/);
    }
    for (const value of ['20', '60', '95']) {
      await page.locator('#opacity').fill(value);
      assert.equal(await page.locator('#opacity').inputValue(), value);
    }
  });

  await step('map: search and population pyramid', async () => {
    await page.locator('#toggle-pyramid').setChecked(true);
    const name = await searchRegion(page, '종로');
    assert.match(name, /종로/);
    assert(await page.locator('#pyramid').isVisible());
    assert.match(await page.locator('#py-title').innerText(), /종로/);
  });

  await step('map: mocked geolocation finds nearest hospital', async () => {
    await page.locator('#myloc').click();
    await page.waitForFunction(() => document.getElementById('toast').textContent.includes('최근접:'), null, { timeout: 12_000 });
    assert.match(await page.locator('#toast').innerText(), /최근접:.*km/);
  });

  await step('map: shift-click comparison and clear', async () => {
    const canvas = page.locator('.maplibregl-canvas');
    for (const query of ['종로', '수원']) {
      await searchRegion(page, query);
      const box = await canvas.boundingBox();
      assert(box, 'map canvas has no bounds');
      const point = await page.evaluate(() => {
        const width = map.getCanvas().clientWidth;
        const height = map.getCanvas().clientHeight;
        for (let radius = 0; radius <= Math.max(width, height); radius += 24) {
          for (let y = Math.max(8, height / 2 - radius); y <= Math.min(height - 8, height / 2 + radius); y += 24) {
            for (let x = Math.max(8, width / 2 - radius); x <= Math.min(width - 8, width / 2 + radius); x += 24) {
              const hits = map.queryRenderedFeatures([x, y], { layers: ['sgg-hover'] });
              if (hits.length) return { x, y, code: String(hits[0].properties.code || '') };
            }
          }
        }
        return null;
      });
      assert(point, `no rendered sigungu point after ${query} search`);
      await page.keyboard.down('Shift');
      await page.mouse.click(box.x + point.x, box.y + point.y);
      await page.keyboard.up('Shift');
      await page.waitForTimeout(400);
    }
    assert(await page.locator('#cmpPanel').isVisible(), 'comparison panel stayed hidden');
    assert((await page.locator('#cmpBody').innerText()).trim().length > 10);
    await page.locator('#cmpClear').click();
    assert(await page.locator('#cmpPanel').isHidden());
  });

  await step('map: clear coverage and CSV export', async () => {
    await page.locator('#sim-clear').click();
    await verifyDownload(page, '#exportCsv', 'sigungu_medical_indicators.csv');
  });

  await step('map: every basemap option with key fallback', async () => {
    const values = await page.locator('#basemap option').evaluateAll((nodes) => nodes.map((node) => node.value));
    assert.equal(values.length, 8);
    for (const value of values) {
      await page.locator('#basemap').selectOption(value);
      await page.waitForTimeout(value.startsWith('vworld-') ? 250 : 1_500);
      const current = await page.locator('#basemap').inputValue();
      if (value.startsWith('vworld-')) {
        assert([value, 'carto-positron'].includes(current), `${value} fallback failed: ${current}`);
      } else {
        assert.equal(current, value);
      }
      assert(await page.locator('.maplibregl-canvas').isVisible());
    }
  });

  await step('map: screenshot', async () => {
    await page.locator('#basemap').selectOption('carto-positron');
    await page.locator('[data-view="iso"]').click();
    await page.locator('#toggle-sgg').setChecked(true);
    await page.locator('#toggle-hosp').setChecked(true);
    await page.locator('#toggle-pyramid').setChecked(false);
    await page.locator('#toggle-traffic').setChecked(false);
    await page.locator('#opacity').fill('60');
    await page.evaluate(() => new Promise((resolve) => {
      if (map.loaded()) { resolve(true); return; }
      map.once('idle', () => resolve(true));
      setTimeout(() => resolve(false), 8_000);
    }));
    await page.screenshot({ path: path.join(outputDir, 'map-desktop.png'), fullPage: true });
  });
  await step('map: browser diagnostics', async () => assertDiagnosticsClean(diag));
  await context.close();
}

async function syntheticSuite(browser, syntheticUrl) {
  const syntheticBase = syntheticUrl.replace(/\/$/, '');
  const syntheticOrigin = new URL(syntheticBase).origin;
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await context.newPage();
  const diag = diagnosticsFor(page, 'synthetic', syntheticOrigin);

  const loaded = await step('synthetic: load metadata and controls', async () => {
    await gotoAbsoluteOk(page, `${syntheticBase}/`, syntheticOrigin);
    await page.waitForFunction(() => document.querySelectorAll('#yearSelect option').length > 0, null, { timeout: 30_000 });
    await page.waitForFunction(() => document.querySelectorAll('#sidoSelect option').length > 1, null, { timeout: 30_000 });
    assert.equal(await page.locator('[data-dl]').count(), 6);
    assert(await page.locator('[data-dl]').evaluateAll((nodes) => nodes.every((node) => node.disabled)));
  });
  if (!loaded.ok) {
    await context.close();
    return;
  }

  await step('synthetic: all select options and dependent regions', async () => {
    if (!await page.locator('#advBox').evaluate((node) => node.open)) {
      await page.locator('#advBox summary').click();
    }
    for (const selector of ['#yearSelect', '#corrPreset', '#sexSelect', '#anchorSelect']) {
      const values = await page.locator(`${selector} option`).evaluateAll((nodes) => nodes.map((node) => node.value));
      assert(values.length > 0, `${selector} has no options`);
      for (const value of values) {
        await page.locator(selector).selectOption({ value });
        assert.equal(await page.locator(selector).inputValue(), value);
      }
    }
    const sidos = await page.locator('#sidoSelect option').evaluateAll((nodes) => nodes.map((node) => node.value));
    for (const sido of sidos) {
      await page.locator('#sidoSelect').selectOption({ value: sido });
      assert.equal(await page.locator('#sidoSelect').inputValue(), sido);
      if (sido === '전체') {
        assert(await page.locator('#sigunguSelect').isDisabled());
      } else {
        assert(await page.locator('#sigunguSelect').isEnabled(), `${sido} sigungu disabled`);
        assert((await page.locator('#sigunguSelect option').count()) > 1, `${sido} sigungu missing`);
      }
    }
    await page.locator('#sidoSelect').selectOption('전체');
  });

  await step('synthetic: ranges and client validation', async () => {
    if (!await page.locator('#advBox').evaluate((node) => node.open)) {
      await page.locator('#advBox summary').click();
    }
    for (const [selector, values, output] of [
      ['#corrRange', ['0', '0.75', '1.5'], '#corrValue'],
      ['#missingRange', ['0', '25', '50'], '#missingValue'],
    ]) {
      for (const value of values) {
        await page.locator(selector).fill(value);
        assert.equal(await page.locator(selector).inputValue(), value);
        assert((await page.locator(output).innerText()).includes(value === '0.75' ? '0.75' : String(Number(value))));
      }
    }
    await page.locator('#nSelect').selectOption('custom');
    await page.locator('#nCustom').fill('99');
    await page.locator('#genBtn').click();
    assert(await page.locator('#errorBox').isVisible());
    assert.match(await page.locator('#errorBox').innerText(), /100/);
    await page.locator('#ageMin').fill('80');
    await page.locator('#ageMax').fill('20');
    await page.locator('#nCustom').fill('100');
    await page.locator('#genBtn').click();
    assert.match(await page.locator('#errorBox').innerText(), /최소 > 최대/);
  });

  await step('synthetic: generate 100 rows', async () => {
    await page.locator('#ageMin').fill('20');
    await page.locator('#ageMax').fill('80');
    await page.locator('#sexSelect').selectOption('');
    await page.locator('#anchorSelect').selectOption('cr');
    await page.locator('#missingRange').fill('0');
    await page.locator('#repInput').fill('1');
    await page.locator('#seedInput').fill('20260713');
    await page.locator('#corrRange').fill('1');
    await page.locator('#genBtn').click();
    await page.waitForFunction(() => !document.getElementById('genBtn').disabled && !document.querySelector('[data-dl="a"]').disabled, null, { timeout: 60_000 });
    assert.equal((await page.locator('#kpiRows').innerText()).replace(/,/g, ''), '100');
    assert(await page.locator('#errorBox').isHidden());
    assert.match(await page.locator('#seedUsed').innerText(), /20260713/);
  });

  await step('synthetic: all result tabs and paged preview', async () => {
    for (const key of ['a', 'b', 'c', 'v', 'p']) {
      const tab = page.locator(`#tab-${key}`);
      await tab.click();
      assert.equal(await tab.getAttribute('aria-selected'), 'true');
      assert(await page.locator(`#panel-${key}`).isVisible(), `${key} panel hidden`);
      assert(await page.locator(`#panel-${key} [data-body]`).isVisible(), `${key} body hidden`);
    }
    await page.locator('#tab-a').click();
    const first = await page.locator('#pageInfo').innerText();
    if (await page.locator('#pageNext').isEnabled()) {
      await page.locator('#pageNext').click();
      assert.notEqual(await page.locator('#pageInfo').innerText(), first);
      await page.locator('#pagePrev').click();
      assert.equal(await page.locator('#pageInfo').innerText(), first);
    }
  });

  await step('synthetic: all heatmap and verification segments', async () => {
    await page.locator('#tab-c').click();
    for (const selector of ['[data-metric]']) {
      const buttons = page.locator(selector);
      assert((await buttons.count()) > 1, `${selector} missing`);
      for (let index = 0; index < await buttons.count(); index += 1) {
        const button = buttons.nth(index);
        await button.click();
        assert(await button.evaluate((node) => node.classList.contains('on')), `${selector}[${index}] not active`);
      }
    }
    await page.locator('#tab-v').click();
    for (const selector of ['[data-em]', '[data-tol]']) {
      const buttons = page.locator(selector);
      assert((await buttons.count()) > 1, `${selector} missing`);
      for (let index = 0; index < await buttons.count(); index += 1) {
        const button = buttons.nth(index);
        await button.click();
        assert(await button.evaluate((node) => node.classList.contains('on')), `${selector}[${index}] not active`);
      }
    }
  });

  await step('synthetic: all six downloads', async () => {
    const downloads = [
      ['a', 'synthetic_health_a_individual.csv'],
      ['json', 'synthetic_health_a_individual.json'],
      ['b', 'synthetic_health_b_summary.csv'],
      ['c', 'synthetic_health_c_risk_matrix.csv'],
      ['card', 'synthetic_health_datacard.json'],
      ['batch', 'synthetic_health_batch.zip'],
    ];
    for (const [type, filename] of downloads) {
      await verifyDownload(page, `[data-dl="${type}"]`, filename);
    }
  });

  await step('synthetic: internal links and screenshot', async () => {
    await verifyInternalLinks(page, syntheticOrigin);
    await page.screenshot({ path: path.join(outputDir, 'synthetic-desktop.png'), fullPage: true });
  });
  await step('synthetic: browser diagnostics', async () => assertDiagnosticsClean(diag));
  await context.close();
}

async function mobileLoop(browser, syntheticUrl) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
  });
  const routes = [
    { url: `${baseUrl}/`, origin: baseOrigin, key: 'dashboard', selector: '#kpi' },
    { url: `${baseUrl}/map.html`, origin: baseOrigin, key: 'map', selector: '.maplibregl-canvas' },
  ];
  if (syntheticUrl) {
    const url = `${syntheticUrl.replace(/\/$/, '')}/`;
    routes.push({ url, origin: new URL(url).origin, key: 'synthetic', selector: '#genForm' });
  }
  for (const item of routes) {
    const page = await context.newPage();
    const diag = diagnosticsFor(page, `mobile-${item.key}`, item.origin);
    const loaded = await step(`mobile: ${item.key} render`, async () => {
      await gotoAbsoluteOk(page, item.url, item.origin);
      await page.waitForSelector(item.selector, { timeout: 45_000 });
      assert(await page.locator(item.selector).isVisible());
      const viewport = await page.evaluate(() => ({
        visualWidth: visualViewport.width,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      assert.equal(viewport.visualWidth, 390);
      assert(viewport.scrollWidth <= 392, `${item.key} horizontal overflow: ${viewport.scrollWidth}px`);
      if (item.key === 'dashboard') {
        const reveals = page.locator('.reveal');
        for (let index = 0; index < await reveals.count(); index += 1) {
          await reveals.nth(index).scrollIntoViewIfNeeded();
          await page.waitForTimeout(60);
        }
        assert.equal(await page.locator('.reveal:not(.in)').count(), 0, 'dashboard reveal sections not activated');
        await page.evaluate(() => window.scrollTo(0, 0));
      }
      await page.screenshot({ path: path.join(outputDir, `${item.key}-mobile.png`), fullPage: true });
    });
    if (loaded.ok) await step(`mobile: ${item.key} browser diagnostics`, async () => assertDiagnosticsClean(diag));
    await page.close();
  }
  await context.close();
}

async function publicEntryLoop(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  for (const entryUrl of publicEntryUrls) {
    const expected = new URL(entryUrl);
    const key = expected.hostname.split('.')[0];
    const page = await context.newPage();
    await step(`public entry: ${key} direct OAuth boundary`, async () => {
      const response = await page.goto(entryUrl, { waitUntil: 'domcontentloaded', timeout: 45_000 });
      assert(response, `no navigation response for ${entryUrl}`);
      await page.waitForLoadState('load', { timeout: 30_000 }).catch(() => {});
      const finalUrl = new URL(page.url());
      const isDirectApp = finalUrl.origin === expected.origin;
      const isGoogleOAuth = finalUrl.hostname === 'accounts.google.com';
      assert(isDirectApp || isGoogleOAuth, `unexpected redirect target: ${finalUrl.href}`);
      assert(!/^(link|list)\.tms-ai-lab\.com$/i.test(finalUrl.hostname), `legacy host used: ${finalUrl.hostname}`);
      if (isDirectApp) {
        assert.equal(response.status(), 200, `${entryUrl} returned ${response.status()}`);
        if (key === 'links') {
          assert((await page.locator('a[href="https://korea-medical-analysis.vercel.app"]').count()) > 0,
            'canonical medical-analysis link missing');
        }
        if (key === 'hc-mkdata') assert((await page.locator('#genForm').count()) > 0, 'generator form missing');
      } else {
        assert(/accounts\.google\.com/i.test(page.url()), 'Google OAuth gate missing');
      }
      await page.screenshot({ path: path.join(outputDir, `${key}-public-entry.png`), fullPage: true });
    });
    await page.close();
  }
  await context.close();
}

async function main() {
  const syntheticUrl = process.env.KMA_SYNTHETIC_URL || '';
  const browser = await chromium.launch({ headless: true });
  try {
    await dashboardSuite(browser);
    await mapSuite(browser);
    if (syntheticUrl) await syntheticSuite(browser, syntheticUrl);
    await mobileLoop(browser, syntheticUrl);
    if (publicEntryUrls.length) await publicEntryLoop(browser);
  } finally {
    await browser.close();
  }

  const report = {
    baseUrl,
    finishedAt: new Date().toISOString(),
    passed: results.filter((row) => row.status === 'PASS').length,
    failed: failures.length,
    results,
    failures,
    screenshots: fs.readdirSync(outputDir).filter((name) => name.endsWith('.png')),
  };
  fs.writeFileSync(path.join(outputDir, 'report.json'), JSON.stringify(report, null, 2));
  process.stdout.write(`REPORT ${path.join(outputDir, 'report.json')}\n`);
  process.stdout.write(`SUMMARY pass=${report.passed} fail=${report.failed}\n`);
  if (failures.length) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${shortError(error)}\n`);
  process.exitCode = 1;
});
