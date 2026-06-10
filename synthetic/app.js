/* 합성 건강검진 데이터 생성기 — 프론트엔드 (vanilla JS)
 * 서버 데이터는 전부 textContent / createElement / setAttribute로만 삽입한다.
 */
'use strict';

(function () {
  // ---------- 상수 ----------
  var COLUMN_LABELS = {
    synthetic_id: 'ID',
    year: '연도',
    sido: '시도',
    sigungu: '시군구',
    sex: '성별',
    age: '나이',
    age_group: '연령구간',
    age_decade: '연령대',
    height: '신장(cm)',
    weight: '체중(kg)',
    bmi: 'BMI(kg/m²)',
    waist: '허리둘레(cm)',
    sbp: '수축기혈압(mmHg)',
    dbp: '이완기혈압(mmHg)',
    fasting_glucose: '공복혈당(mg/dL)',
    hemoglobin: '혈색소(g/dL)',
    total_cholesterol: '총콜레스테롤(mg/dL)',
    hdl: 'HDL(mg/dL)',
    ldl: 'LDL(mg/dL)',
    triglyceride: '중성지방(mg/dL)',
    creatinine: '크레아티닌(mg/dL)',
    egfr: 'eGFR(mL/min)',
    ast: 'AST(U/L)',
    alt: 'ALT(U/L)',
    ggt: '감마지티피(U/L)',
    urine_protein: '요단백',
    chest_xray: '흉부방사선',
    vision_left: '시력(좌)',
    vision_right: '시력(우)',
    bone_density: '골밀도',
    result_grade: '판정',
    risk_group: '위험군'
  };

  var GRADE_ORDER = ['정상A', '정상B', '질환의심', '유질환자'];
  var GRADE_BADGE = {
    '정상A': 'badge--gA',
    '정상B': 'badge--gB',
    '질환의심': 'badge--gS',
    '유질환자': 'badge--gD'
  };

  // 생성기 DECADES와 라벨을 정확히 일치시킨다('19세 이하'가 '20대'보다 앞).
  var DECADE_ORDER = ['19세 이하', '20대', '30대', '40대', '50대', '60대', '70대', '80세 이상'];

  var GRADE_DESC = {
    '정상A': '건강 양호',
    '정상B': '이상은 없으나 자기관리·예방조치 필요(경계)',
    '질환의심': '질환 의심 — 2차 정밀검사 필요',
    '유질환자': '고혈압·당뇨 등 현재 치료 중인 질환 보유'
  };

  var METRICS = {
    risk_score: { label: '종합위험점수', unit: '점' },
    obesity_pct: { label: '비만', unit: '%' },
    htn_pct: { label: '고혈압', unit: '%' },
    dm_pct: { label: '당뇨', unit: '%' },
    dyslip_pct: { label: '이상지질', unit: '%' }
  };

  var PAGE_SIZE = 20;
  var SVG_NS = 'http://www.w3.org/2000/svg';

  // ---------- 상태 ----------
  var state = {
    data: null,          // 마지막 /api/generate 응답
    maxN: 500000,
    sigunguMap: {},      // 시도 → [{code, name}]
    generating: false,
    genTimer: null,      // 생성 경과 표시 타이머
    page: 1,
    sortKey: null,
    sortDir: 1,          // 1=오름, -1=내림
    colTypes: {},        // 컬럼별 숫자 여부
    metric: 'risk_score',
    errMetric: 'max',    // 신뢰도 오차 지표: max | mae
    errTol: 0.5          // 신뢰도 판정 기준(±%p): 일치 ≤ t, 근접 ≤ 3t
  };

  // ---------- DOM ----------
  function $(id) { return document.getElementById(id); }

  var el = {
    form: $('genForm'),
    nSelect: $('nSelect'),
    nCustom: $('nCustom'),
    sidoSelect: $('sidoSelect'),
    sigunguSelect: $('sigunguSelect'),
    corrRange: $('corrRange'),
    corrValue: $('corrValue'),
    seedInput: $('seedInput'),
    seedUsed: $('seedUsed'),
    errMetricSeg: $('errMetricSeg'),
    errTolSeg: $('errTolSeg'),
    errRuleNote: $('errRuleNote'),
    genBtn: $('genBtn'),
    genSpinner: $('genSpinner'),
    genLabel: $('genLabel'),
    errorBox: $('errorBox'),
    kpiRows: $('kpiRows'),
    kpiRowsSub: $('kpiRowsSub'),
    kpiTime: $('kpiTime'),
    kpiTimeSub: $('kpiTimeSub'),
    kpiDiff: $('kpiDiff'),
    kpiGrade: $('kpiGrade'),
    tableA: $('tableA'),
    pagePrev: $('pagePrev'),
    pageNext: $('pageNext'),
    pageInfo: $('pageInfo'),
    tableB: $('tableB'),
    tableC: $('tableC'),
    metricSeg: $('metricSeg'),
    heatNote: $('heatNote'),
    verifyList: $('verifyList'),
    verifyNote: $('verifyNote'),
    kpiDiffSub: $('kpiDiffSub'),
    demoList: $('demoList'),
    demoNote: $('demoNote'),
    fidelityTable: $('fidelityTable'),
    fidelityNote: $('fidelityNote'),
    privacySummary: $('privacySummary'),
    privacyNote: $('privacyNote'),
    kanonTable: $('kanonTable'),
    privacyChecks: $('privacyChecks')
  };

  // ---------- 포맷 ----------
  function fmtInt(v) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('ko-KR');
  }

  function fmt1(v) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  function fmtPct(v) {
    return v == null || isNaN(v) ? '—' : fmt1(v) + '%';
  }

  function fmtCell(v) {
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'number') {
      return Number.isInteger(v) ? fmtInt(v) : fmt1(v);
    }
    return String(v);
  }

  // ---------- 에러 ----------
  function showError(msg) {
    el.errorBox.textContent = msg;
    el.errorBox.hidden = false;
  }

  function hideError() {
    el.errorBox.hidden = true;
    el.errorBox.textContent = '';
  }

  // ---------- 패널 상태 ----------
  function setPanelsReady(ready) {
    var panels = document.querySelectorAll('.panel');
    for (var i = 0; i < panels.length; i++) {
      var empty = panels[i].querySelector('[data-empty]');
      var body = panels[i].querySelector('[data-body]');
      if (empty) empty.hidden = ready;
      if (body) body.hidden = !ready;
    }
  }

  // ---------- 메타 ----------
  function loadMeta() {
    fetch('/api/meta')
      .then(function (res) { return res.json(); })
      .then(function (json) {
        if (!json || !json.ok) {
          throw new Error((json && json.error) || '메타 응답 형식 오류');
        }
        if (typeof json.max_n === 'number' && json.max_n > 0) {
          state.maxN = json.max_n;
          el.nCustom.max = String(json.max_n);
          syncNOptions();
        }
        state.sigunguMap = json.sigungu_map || {};
        fillSidos(Array.isArray(json.sidos) ? json.sidos : ['전체']);
        fillSigungu();
      })
      .catch(function (err) {
        showError('시도 목록을 불러오지 못했습니다: ' + err.message);
      });
  }

  // 표본 수 드롭다운을 서버 상한(max_n)에 맞춤 — 상한 초과 정적 옵션 제거.
  // (배포 서버는 메모리 안전상 max_n이 로컬보다 작을 수 있어, 초과 옵션을
  //  그대로 두면 선택 시 서버가 400을 반환한다.)
  function syncNOptions() {
    var opts = el.nSelect.options;
    var selectedRemoved = false;
    for (var i = opts.length - 1; i >= 0; i--) {
      var v = opts[i].value;
      if (v === 'custom') continue;
      if (Number(v) > state.maxN) {
        if (opts[i].selected) selectedRemoved = true;
        el.nSelect.remove(i);
      }
    }
    if (selectedRemoved) {
      // 제거된 옵션이 선택돼 있었으면 상한 이하 최대 프리셋으로 대체
      var best = null;
      for (var j = 0; j < el.nSelect.options.length; j++) {
        var ov = el.nSelect.options[j].value;
        if (ov !== 'custom') best = ov;
      }
      el.nSelect.value = best || 'custom';
      el.nCustom.hidden = el.nSelect.value !== 'custom';
    }
  }

  function fillSidos(sidos) {
    while (el.sidoSelect.firstChild) el.sidoSelect.removeChild(el.sidoSelect.firstChild);
    for (var i = 0; i < sidos.length; i++) {
      var opt = document.createElement('option');
      opt.value = sidos[i];
      opt.textContent = sidos[i];
      el.sidoSelect.appendChild(opt);
    }
  }

  function fillSigungu() {
    if (!el.sigunguSelect) return;
    var sido = el.sidoSelect.value || '전체';
    var list = state.sigunguMap[sido] || [];
    while (el.sigunguSelect.firstChild) el.sigunguSelect.removeChild(el.sigunguSelect.firstChild);
    var all = document.createElement('option');
    all.value = '';
    all.textContent = sido === '전체' ? '전체 (시도 먼저 선택)' : '전체';
    el.sigunguSelect.appendChild(all);
    for (var i = 0; i < list.length; i++) {
      var opt = document.createElement('option');
      opt.value = list[i].code;
      opt.textContent = list[i].name;
      el.sigunguSelect.appendChild(opt);
    }
    el.sigunguSelect.disabled = list.length === 0;
  }

  // ---------- 입력 ----------
  function readN() {
    var n = el.nSelect.value !== 'custom'
      ? parseInt(el.nSelect.value, 10)
      : Math.round(Number(el.nCustom.value));
    if (!isFinite(n) || n < 100 || n > state.maxN) {
      throw new Error('표본 수는 100 ~ ' + fmtInt(state.maxN) + ' 사이의 숫자여야 합니다.');
    }
    return n;
  }

  function readSeed() {
    var raw = el.seedInput.value.trim();
    if (raw === '') return null;
    var v = Number(raw);
    if (!isFinite(v) || Math.floor(v) !== v) {
      throw new Error('시드는 정수로 입력하세요.');
    }
    return v;
  }

  // ---------- 생성 ----------
  function setGenerating(on, bigN) {
    state.generating = on;
    el.genBtn.disabled = on;
    el.genSpinner.hidden = !on;
    setDownloadEnabled(!on && !!state.data);
    if (state.genTimer) { clearInterval(state.genTimer); state.genTimer = null; }
    if (on) {
      var t0 = Date.now();
      var label = bigN ? '생성 중(대용량)… ' : '생성 중… ';
      el.genLabel.textContent = label.trim();
      state.genTimer = setInterval(function () {
        el.genLabel.textContent = label + Math.round((Date.now() - t0) / 1000) + 's';
      }, 1000);
    } else {
      el.genLabel.textContent = '생성';
    }
  }

  function setDownloadEnabled(enabled) {
    var btns = document.querySelectorAll('[data-dl]');
    for (var i = 0; i < btns.length; i++) btns[i].disabled = !enabled;
  }

  function generate() {
    if (state.generating) return;
    hideError();

    var payload;
    try {
      payload = {
        n: readN(),
        sido: el.sidoSelect.value || '전체',
        sigungu: (el.sigunguSelect && el.sigunguSelect.value) || '',
        seed: readSeed(),
        corr: parseFloat(el.corrRange.value)
      };
    } catch (err) {
      showError(err.message);
      return;
    }

    setGenerating(true, payload.n >= 200000);

    fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        return res.json()
          .catch(function () { throw new Error('HTTP ' + res.status); })
          .then(function (json) {
            if (!res.ok || !json.ok) {
              throw new Error((json && json.error) || ('HTTP ' + res.status));
            }
            return json;
          });
      })
      .then(function (json) {
        state.data = json;
        state.page = 1;
        state.sortKey = null;
        state.sortDir = 1;
        renderAll();
        setPanelsReady(true);
        showSeedUsed(json.meta);
      })
      .catch(function (err) {
        showError('생성 실패: ' + err.message);
      })
      .then(function () {
        setGenerating(false);
      });
  }

  // ---------- 재현 시드 표시 ----------
  function showSeedUsed(meta) {
    if (!el.seedUsed || !meta || meta.seed == null) return;
    while (el.seedUsed.firstChild) el.seedUsed.removeChild(el.seedUsed.firstChild);
    el.seedUsed.appendChild(document.createTextNode('이번 생성 시드 ' + meta.seed + ' '));
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn--sm';
    btn.textContent = '시드 고정';
    btn.title = '이 시드를 입력칸에 넣어 같은 조건으로 동일 데이터를 재현합니다';
    btn.addEventListener('click', function () {
      el.seedInput.value = String(meta.seed);
      el.seedInput.focus();
    });
    el.seedUsed.appendChild(btn);
    el.seedUsed.hidden = false;
  }

  // ---------- KPI ----------
  function renderKpi() {
    var kpi = state.data.kpi || {};
    el.kpiRows.textContent = fmtInt(kpi.rows);
    el.kpiRowsSub.textContent = '행';

    if (kpi.elapsed_ms != null) {
      el.kpiTime.textContent = fmt1(kpi.elapsed_ms / 1000) + '초';
      el.kpiTimeSub.textContent = fmtInt(kpi.elapsed_ms) + ' ms';
    } else {
      el.kpiTime.textContent = '—';
      el.kpiTimeSub.textContent = ' ';
    }

    el.kpiDiff.textContent = fmtPct(kpi.mean_abs_diff_pct);
    if (el.kpiDiffSub && kpi.mean_abs_diff_internal_pct != null) {
      el.kpiDiffSub.textContent = 'KOSIS 원시 · 내부 ' + fmtPct(kpi.mean_abs_diff_internal_pct);
    }

    var dist = kpi.grade_dist || {};
    var keys = GRADE_ORDER.filter(function (g) { return dist[g] != null; });
    Object.keys(dist).forEach(function (k) {
      if (keys.indexOf(k) < 0) keys.push(k);
    });
    el.kpiGrade.textContent = keys.length
      ? keys.map(function (g) { return g + ' ' + fmtPct(dist[g]); }).join(' · ')
      : '—';
  }

  // ---------- A안: 미리보기 표 ----------
  function getColumns() {
    var meta = state.data.meta || {};
    if (Array.isArray(meta.columns) && meta.columns.length) return meta.columns;
    var preview = state.data.preview || [];
    return preview.length ? Object.keys(preview[0]) : [];
  }

  function detectColTypes(columns, rows) {
    var types = {};
    columns.forEach(function (c) {
      var numeric = false;
      var allNumOrNull = true;
      for (var i = 0; i < rows.length; i++) {
        var v = rows[i][c];
        if (v === null || v === undefined || v === '') continue;
        if (typeof v === 'number') numeric = true;
        else { allNumOrNull = false; break; }
      }
      types[c] = numeric && allNumOrNull;
    });
    return types;
  }

  function sortedPreview() {
    var rows = (state.data.preview || []).slice();
    var key = state.sortKey;
    if (!key) return rows;
    var dir = state.sortDir;
    var numeric = !!state.colTypes[key];
    rows.sort(function (x, y) {
      var a = x[key], b = y[key];
      var aEmpty = a === null || a === undefined || a === '';
      var bEmpty = b === null || b === undefined || b === '';
      if (aEmpty && bEmpty) return 0;
      if (aEmpty) return 1;   // 빈 값은 항상 아래로
      if (bEmpty) return -1;
      var r = numeric ? (a - b) : String(a).localeCompare(String(b), 'ko');
      return r * dir;
    });
    return rows;
  }

  function renderTableA() {
    var columns = getColumns();
    var preview = state.data.preview || [];
    state.colTypes = detectColTypes(columns, preview);

    // thead
    var thead = el.tableA.tHead;
    while (thead.firstChild) thead.removeChild(thead.firstChild);
    var hr = document.createElement('tr');
    columns.forEach(function (c) {
      var th = document.createElement('th');
      th.className = 'sortable' + (state.colTypes[c] ? ' num' : '');
      th.textContent = COLUMN_LABELS[c] || c;
      if (state.sortKey === c) {
        var ind = document.createElement('span');
        ind.className = 'sort-ind';
        ind.textContent = state.sortDir === 1 ? '▲' : '▼';
        th.appendChild(ind);
      }
      th.addEventListener('click', function () {
        if (state.sortKey === c) {
          state.sortDir = -state.sortDir;
        } else {
          state.sortKey = c;
          state.sortDir = 1;
        }
        state.page = 1;
        renderTableA();
      });
      hr.appendChild(th);
    });
    thead.appendChild(hr);

    // tbody (페이징)
    var rows = sortedPreview();
    var total = rows.length;
    var totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (state.page > totalPages) state.page = totalPages;
    var start = (state.page - 1) * PAGE_SIZE;
    var end = Math.min(start + PAGE_SIZE, total);

    var tbody = el.tableA.tBodies[0];
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    for (var i = start; i < end; i++) {
      var row = rows[i];
      var tr = document.createElement('tr');
      columns.forEach(function (c) {
        var td = document.createElement('td');
        var v = row[c];
        if (c === 'result_grade' && typeof v === 'string' && v !== '') {
          var badge = document.createElement('span');
          badge.className = 'badge ' + (GRADE_BADGE[v] || '');
          badge.textContent = v;
          if (GRADE_DESC[v]) badge.title = v + ' — ' + GRADE_DESC[v];
          td.appendChild(badge);
        } else {
          td.textContent = fmtCell(v);
          if (state.colTypes[c]) td.className = 'num';
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }

    el.pageInfo.textContent = total === 0
      ? '0행'
      : (start + 1) + '–' + end + ' / 총 ' + fmtInt(total) + '행';
    el.pagePrev.disabled = state.page <= 1;
    el.pageNext.disabled = state.page >= totalPages;
  }

  // ---------- B안: 검사항목 요약 ----------
  function barSegment(cls, pct, label) {
    var seg = document.createElement('span');
    seg.className = cls;
    var p = Math.max(0, Number(pct) || 0);
    seg.style.width = p + '%';
    seg.title = label + ' ' + fmtPct(pct);
    if (p >= 9) seg.textContent = fmt1(pct);
    return seg;
  }

  function renderTableB() {
    var tbody = el.tableB.tBodies[0];
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    var items = state.data.summary_b || [];
    items.forEach(function (item) {
      var tr = document.createElement('tr');

      var tdName = document.createElement('td');
      tdName.textContent = item.indicator || item.key || '';
      tdName.style.fontWeight = '600';
      tr.appendChild(tdName);

      var tdCrit = document.createElement('td');
      tdCrit.className = 'crit';
      tdCrit.textContent = item.criteria || '';
      tr.appendChild(tdCrit);

      var tdDist = document.createElement('td');
      tdDist.className = 'dist';

      var bar = document.createElement('div');
      bar.className = 'bbar';
      bar.appendChild(barSegment('seg-ok', item.normal_pct, '정상'));
      bar.appendChild(barSegment('seg-warn', item.caution_pct, '주의'));
      bar.appendChild(barSegment('seg-bad', item.disease_pct, '질환의심'));
      tdDist.appendChild(bar);

      var labels = document.createElement('div');
      labels.className = 'bdist';
      [['dot--ok', '정상', item.normal_pct],
       ['dot--warn', '주의', item.caution_pct],
       ['dot--bad', '질환의심', item.disease_pct]].forEach(function (d) {
        var span = document.createElement('span');
        var dot = document.createElement('span');
        dot.className = 'dot ' + d[0];
        span.appendChild(dot);
        span.appendChild(document.createTextNode(d[1] + ' ' + fmtPct(d[2])));
        labels.appendChild(span);
      });
      tdDist.appendChild(labels);

      tr.appendChild(tdDist);
      tbody.appendChild(tr);
    });
  }

  // ---------- C안: 히트맵 ----------
  function renderHeatmap() {
    var matrix = state.data.matrix_c || [];
    var metric = state.metric;
    var mconf = METRICS[metric];
    el.heatNote.textContent = '셀 농도 = ' + mconf.label + (mconf.unit === '%' ? '(%)' : '(점)');

    // 시도(행) — 등장 순서, 연령대(열) — 고정 순서 + 미정의 값 뒤에 추가
    var sidoList = [];
    var decadesSeen = {};
    var lookup = {}; // sido -> decade -> row
    matrix.forEach(function (r) {
      if (!lookup[r.sido]) {
        lookup[r.sido] = {};
        sidoList.push(r.sido);
      }
      lookup[r.sido][r.age_decade] = r;
      decadesSeen[r.age_decade] = true;
    });

    var decades = DECADE_ORDER.filter(function (d) { return decadesSeen[d]; });
    Object.keys(decadesSeen).forEach(function (d) {
      if (decades.indexOf(d) < 0) decades.push(d);
    });

    var maxVal = 0;
    matrix.forEach(function (r) {
      var v = Number(r[metric]);
      if (isFinite(v) && v > maxVal) maxVal = v;
    });

    // thead
    var thead = el.tableC.tHead;
    while (thead.firstChild) thead.removeChild(thead.firstChild);
    var hr = document.createElement('tr');
    var corner = document.createElement('th');
    corner.className = 'rowh';
    corner.textContent = '시도 \\ 연령대';
    hr.appendChild(corner);
    decades.forEach(function (d) {
      var th = document.createElement('th');
      th.className = 'num';
      th.textContent = d;
      hr.appendChild(th);
    });
    thead.appendChild(hr);

    // tbody
    var tbody = el.tableC.tBodies[0];
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    sidoList.forEach(function (sido) {
      var tr = document.createElement('tr');
      var th = document.createElement('td');
      th.className = 'rowh';
      th.textContent = sido;
      tr.appendChild(th);

      decades.forEach(function (d) {
        var td = document.createElement('td');
        var row = lookup[sido][d];
        if (!row) {
          td.className = 'cell cell--empty';
          td.textContent = '·';
          td.title = sido + ' · ' + d + ' — 데이터 없음';
        } else {
          td.className = 'cell';
          var v = Number(row[metric]);
          var lowN = Number(row.n) < 30; // 표본 부족 셀 — 수치가 불안정하므로 참고용 표시
          td.textContent = isFinite(v) ? fmt1(v) + (lowN ? '*' : '') : '—';
          var t = maxVal > 0 && isFinite(v) ? Math.max(0, v) / maxVal : 0;
          td.style.background = 'rgba(180,35,24,' + (0.04 + t * 0.72).toFixed(3) + ')';
          if (t > 0.55) td.style.color = '#ffffff';
          if (lowN) td.classList.add('cell--lown');
          td.title = sido + ' · ' + d +
            '\n표본 n ' + fmtInt(row.n) + (lowN ? ' — 표본 부족(<30), 참고용' : '') +
            '\n비만 ' + fmtPct(row.obesity_pct) + ' · 고혈압 ' + fmtPct(row.htn_pct) +
            '\n당뇨 ' + fmtPct(row.dm_pct) + ' · 이상지질 ' + fmtPct(row.dyslip_pct) +
            '\n종합위험 ' + fmt1(row.risk_score) + '점 (' + (row.risk_level || '—') + ')';
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  // ---------- 검증 탭 ----------
  // 오차 판정: 일치 ≤ t, 근접 ≤ 3t, 차이 > 3t (t = state.errTol, 사용자 선택)
  function diffBadge(diff, prefix) {
    var span = document.createElement('span');
    var d = (diff == null) ? NaN : Number(diff); // null이 0으로 변환돼 '일치'로 오표시되는 것 방지
    var t = state.errTol;
    var cls, word;
    if (!isFinite(d)) { cls = 'vbadge--warn'; word = '—'; }
    else if (d <= t) { cls = 'vbadge--ok'; word = '일치'; }
    else if (d <= t * 3) { cls = 'vbadge--warn'; word = '근접'; }
    else { cls = 'vbadge--bad'; word = '차이'; }
    span.className = 'vbadge ' + cls;
    var lead = prefix ? prefix + ' ' : '';
    var metricWord = state.errMetric === 'mae' ? '평균' : '최대';
    span.textContent = isFinite(d)
      ? lead + word + ' · ' + metricWord + ' ±' + fmt1(d) + '%p'
      : lead + word;
    return span;
  }

  // 구간 배열에서 평균 절대오차(%p) — 평균 오차 지표용(클라이언트 계산)
  function maeOf(kosis, synth) {
    if (!kosis || !synth || !kosis.length) return null;
    var s = 0;
    for (var i = 0; i < kosis.length; i++) {
      s += Math.abs((Number(kosis[i]) || 0) - (Number(synth[i]) || 0));
    }
    return s / kosis.length;
  }

  // 카드 지표 선택: 최대 오차는 서버 제공값, 평균 오차는 배열에서 직접 계산
  function pickDiff(maxDiff, kosis, synth) {
    return state.errMetric === 'mae' ? maeOf(kosis, synth) : maxDiff;
  }

  function svgEl(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    for (var k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  function buildPairChart(bins, kosis, synth) {
    var barW = 20, pairGap = 5, groupGap = 26;
    var padL = 10, padR = 10, padT = 16, chartH = 110;
    var groupW = barW * 2 + pairGap;
    var n = bins.length;
    // 긴 구간 라벨은 겹치므로 회전 처리
    var maxLabelLen = 0;
    for (var li = 0; li < n; li++) {
      maxLabelLen = Math.max(maxLabelLen, String(bins[li] == null ? '' : bins[li]).length);
    }
    var rotate = maxLabelLen > 6;
    var labelH = rotate ? 44 : 26;
    var W = padL + padR + n * groupW + Math.max(0, n - 1) * groupGap;
    var H = padT + chartH + labelH;

    var svg = svgEl('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      width: W, height: H,
      role: 'img'
    });

    // 기준선
    svg.appendChild(svgEl('line', {
      x1: padL - 4, x2: W - padR + 4,
      y1: padT + chartH + 0.5, y2: padT + chartH + 0.5,
      stroke: '#e5e7eb', 'stroke-width': 1
    }));

    var maxVal = 0;
    for (var i = 0; i < n; i++) {
      maxVal = Math.max(maxVal, Number(kosis[i]) || 0, Number(synth[i]) || 0);
    }
    if (maxVal <= 0) maxVal = 1;

    for (var j = 0; j < n; j++) {
      var x0 = padL + j * (groupW + groupGap);
      var kv = Number(kosis[j]) || 0;
      var sv = Number(synth[j]) || 0;
      var kh = (kv / maxVal) * chartH;
      var sh = (sv / maxVal) * chartH;

      var kRect = svgEl('rect', {
        x: x0, y: padT + chartH - kh, width: barW, height: Math.max(kh, 0.5),
        fill: '#c7ccd3', rx: 1.5
      });
      var kTitle = document.createElementNS(SVG_NS, 'title');
      kTitle.textContent = bins[j] + ' — KOSIS ' + fmtPct(kv);
      kRect.appendChild(kTitle);
      svg.appendChild(kRect);

      var sRect = svgEl('rect', {
        x: x0 + barW + pairGap, y: padT + chartH - sh, width: barW, height: Math.max(sh, 0.5),
        fill: '#111111', rx: 1.5
      });
      var sTitle = document.createElementNS(SVG_NS, 'title');
      sTitle.textContent = bins[j] + ' — 합성 ' + fmtPct(sv);
      sRect.appendChild(sTitle);
      svg.appendChild(sRect);

      // 값 라벨
      var kText = svgEl('text', {
        x: x0 + barW / 2, y: padT + chartH - kh - 3,
        'text-anchor': 'middle', 'font-size': 9, fill: '#8a9099'
      });
      kText.textContent = fmt1(kv);
      svg.appendChild(kText);

      var sText = svgEl('text', {
        x: x0 + barW + pairGap + barW / 2, y: padT + chartH - sh - 3,
        'text-anchor': 'middle', 'font-size': 9, fill: '#202020', 'font-weight': 700
      });
      sText.textContent = fmt1(sv);
      svg.appendChild(sText);

      // 구간 라벨 (길면 회전)
      var lx = x0 + groupW / 2;
      var ly = padT + chartH + (rotate ? 13 : 16);
      var bAttrs = { x: lx, y: ly, 'font-size': 9, fill: '#6b7280' };
      if (rotate) {
        bAttrs['text-anchor'] = 'end';
        bAttrs.transform = 'rotate(-32 ' + lx + ' ' + ly + ')';
      } else {
        bAttrs['text-anchor'] = 'middle';
      }
      var bText = svgEl('text', bAttrs);
      bText.textContent = bins[j];
      svg.appendChild(bText);
    }
    return svg;
  }

  function buildVerifyCard(opts) {
    var card = document.createElement('div');
    card.className = 'vcard';

    var head = document.createElement('div');
    head.className = 'vhead';

    var t = document.createElement('div');
    t.className = 'vtitle';
    t.textContent = opts.title;
    head.appendChild(t);

    var badges = document.createElement('div');
    badges.className = 'vbadges';
    // 메인: KOSIS 원시 전국 대비(대외 충실도). 보조: 내부 정합성(분위수 매핑 충실도).
    badges.appendChild(diffBadge(opts.mainDiff, opts.mainPrefix));
    if (opts.subDiff != null) badges.appendChild(diffBadge(opts.subDiff, opts.subPrefix));
    head.appendChild(badges);

    var legend = document.createElement('div');
    legend.className = 'vlegend';
    [['swatch--kosis', opts.kosisLabel || 'KOSIS'], ['swatch--synth', '합성']].forEach(function (d) {
      var item = document.createElement('span');
      var sw = document.createElement('span');
      sw.className = 'swatch ' + d[0];
      item.appendChild(sw);
      item.appendChild(document.createTextNode(d[1]));
      legend.appendChild(item);
    });
    head.appendChild(legend);
    card.appendChild(head);

    var chart = document.createElement('div');
    chart.className = 'vchart';
    chart.appendChild(buildPairChart(opts.bins, opts.kosis, opts.synth));
    card.appendChild(chart);

    if (opts.caption) {
      var cap = document.createElement('p');
      cap.className = 'vcaption';
      cap.textContent = opts.caption;
      card.appendChild(cap);
    }
    return card;
  }

  function renderDemographics() {
    if (!el.demoList) return;
    while (el.demoList.firstChild) el.demoList.removeChild(el.demoList.firstChild);
    if (el.demoNote) {
      var sg = state.data.meta && state.data.meta.sigungu;
      el.demoNote.textContent = sg
        ? ('시군구(' + sg + ') 모드 — 성·연령 구성은 「시군구 인구구조 × 소속 시도 수검률」 '
           + '보정 기대분포와 비교합니다. 검사수치 분포는 KOSIS 제공 한계로 소속 시도 기준이며, '
           + '시군구별 수검률(uptake) 차이는 반영되지 않습니다.')
        : ('합성 표본의 성별·연령대·지역 구성이 KOSIS 수검 인원 마진과 얼마나 일치하는지 봅니다. '
           + '(시도 한 곳만 생성하면 지역 구성은 생략됩니다.)');
    }
    var demo = state.data.demographics || [];
    demo.forEach(function (item) {
      el.demoList.appendChild(buildVerifyCard({
        title: item.label || item.key || '',
        bins: item.bins || [],
        kosis: item.kosis_pct || [],
        synth: item.synth_pct || [],
        kosisLabel: 'KOSIS 마진',
        mainDiff: pickDiff(item.max_diff_pct, item.kosis_pct, item.synth_pct),
        mainPrefix: 'KOSIS 마진',
        subDiff: null,
        caption: null
      }));
    });

    // 지역 격차 검증 — C안에서 보이는 시도 간 차이가 KOSIS 원천 격차인지 직접 비교
    var sc = state.data.sido_compare;
    if (sc && Array.isArray(sc.bins) && sc.bins.length > 1) {
      el.demoList.appendChild(buildVerifyCard({
        title: sc.label || '시도별 비만율(BMI≥25)',
        bins: sc.bins,
        kosis: sc.kosis_pct || [],
        synth: sc.synth_pct || [],
        kosisLabel: 'KOSIS 시도분포',
        mainDiff: pickDiff(sc.max_diff_pct, sc.kosis_pct, sc.synth_pct),
        mainPrefix: 'KOSIS',
        subDiff: null,
        caption: '시도 간 차이는 KOSIS 시도별 분포에 실재하는 지역 격차를 반영한 것입니다. '
          + '합성과 KOSIS 막대가 시도별로 같이 움직이면 정상이며, 시도당 표본이 '
          + '작을수록(특히 세종) 합성 쪽 막대에 샘플링 오차가 섞입니다. '
          + 'KOSIS 막대는 공표 원값이 아니라 합성 코호트의 시도 내 성비로 표준화한 기대값입니다.'
      }));
    }
  }

  function fidelityCell(stat) {
    var td = document.createElement('td');
    if (!stat || stat.mean == null) {
      td.textContent = '표본 부족';
      td.className = 'fid-na';
      return td;
    }
    var useMae = state.errMetric === 'mae' && stat.mae_mean != null;
    var meanV = useMae ? stat.mae_mean : stat.mean;
    var maxV = useMae ? stat.mae_max : stat.max;
    var mean = document.createElement('span');
    mean.className = 'fid-badge ' + diffClass(meanV);
    mean.textContent = fmt1(meanV) + '%p';
    var max = document.createElement('span');
    max.className = 'fid-max';
    max.textContent = ' / 최대 ' + fmt1(maxV) + '%p';
    td.appendChild(mean);
    td.appendChild(max);
    if (stat.cells_excluded > 0) {
      var ex = document.createElement('span');
      ex.className = 'fid-ex';
      ex.textContent = ' (' + stat.cells_used + '셀, ' + stat.cells_excluded + '셀 제외)';
      td.appendChild(ex);
    }
    return td;
  }

  // 셀 단위 오차는 샘플링 노이즈가 커서 카드보다 느슨한 기준(2t/6t)을 쓴다.
  function diffClass(d) {
    var v = Number(d);
    var t = state.errTol;
    if (!isFinite(v)) return 'fid--na';
    if (v <= t * 2) return 'fid--ok';
    if (v <= t * 6) return 'fid--warn';
    return 'fid--bad';
  }

  function renderFidelity() {
    if (!el.fidelityTable) return;
    var tbody = el.fidelityTable.querySelector('tbody');
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
    if (el.fidelityNote) {
      var nrows = (state.data.meta && state.data.meta.n) || 0;
      el.fidelityNote.textContent =
        '각 검사항목이 (성·연령) 셀과 (시도) 셀에서 KOSIS 분포와 얼마나 일치하는지(최대 절대오차 %p). '
        + '셀 오차는 셀당 표본이 작을수록 샘플링 노이즈를 포함하므로 표본을 키우면 줄어듭니다'
        + (nrows ? ' (현재 ' + fmtInt(nrows) + '행).' : '.')
        + ' 시군구 셀은 KOSIS가 검사수치를 시도·연령까지만 제공해 검증할 수 없습니다.';
    }
    var items = state.data.fidelity || [];
    items.forEach(function (item) {
      var tr = document.createElement('tr');
      var name = document.createElement('td');
      name.className = 'fid-name';
      name.textContent = item.label || item.key || '';
      tr.appendChild(name);

      var overall = document.createElement('td');
      var ov = findVerifyDiff(item.key);
      if (ov == null) {
        overall.textContent = '—';
      } else {
        var ob = document.createElement('span');
        ob.className = 'fid-badge ' + diffClassCard(ov);
        ob.textContent = fmt1(ov) + '%p';
        overall.appendChild(ob);
      }
      tr.appendChild(overall);

      tr.appendChild(fidelityCell(item.age_sex));
      tr.appendChild(fidelityCell(item.sido));
      tbody.appendChild(tr);
    });
  }

  function findVerifyDiff(key) {
    var items = state.data.verify || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].key === key) {
        var it = items[i];
        if (state.errMetric === 'mae') {
          return maeOf(it.raw_kosis_pct || it.kosis_pct, it.synth_pct);
        }
        return it.raw_max_diff_pct != null ? it.raw_max_diff_pct : it.max_diff_pct;
      }
    }
    return null;
  }

  // 카드와 동일한 판정 기준(t/3t) — '전체 오차' 열은 카드의 분포 오차와 같은 값이므로
  // 셀용 완화 기준(2t/6t) 대신 이 기준으로 색을 매겨 카드 배지와 일관되게 한다.
  function diffClassCard(d) {
    var v = Number(d);
    var t = state.errTol;
    if (d == null || !isFinite(v)) return 'fid--na';
    if (v <= t) return 'fid--ok';
    if (v <= t * 3) return 'fid--warn';
    return 'fid--bad';
  }

  function renderPrivacy() {
    if (!el.privacySummary) return;
    var p = state.data.privacy || {};

    // 종합 등급
    while (el.privacySummary.firstChild) el.privacySummary.removeChild(el.privacySummary.firstChild);
    var level = p.level || '—';
    var lvlCls = level === '낮음' ? 'risk--ok' : (level === '보통' ? 'risk--warn' : 'risk--bad');
    var badge = document.createElement('div');
    badge.className = 'risk-badge ' + lvlCls;
    var bl = document.createElement('span');
    bl.className = 'risk-badge-label';
    bl.textContent = '재식별 위험';
    var bv = document.createElement('span');
    bv.className = 'risk-badge-value';
    bv.textContent = level;
    badge.appendChild(bl);
    badge.appendChild(bv);
    el.privacySummary.appendChild(badge);
    var lvlNote = document.createElement('div');
    lvlNote.className = 'risk-note';
    lvlNote.textContent = p.level_note || '';
    el.privacySummary.appendChild(lvlNote);

    if (el.privacyNote) el.privacyNote.textContent = p.note || '';

    // k-익명성 표
    var tbody = el.kanonTable.querySelector('tbody');
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
    (p.k_anonymity || []).forEach(function (k) {
      var tr = document.createElement('tr');
      var ge3 = Number(k.ge3_pct);
      var lmin = Number(k.l_min);
      [
        [k.qi, 'fid-name', null, null],
        [fmtInt(k.classes), null, null, null],
        [String(k.min_k), null, null, null],
        [fmt1(k.unique_pct) + '%', null, null, null],
        // 기준 지표: k≥3 충족률(99% 일치 / 95% 근접)
        [fmt1(k.ge3_pct) + '%', null,
         ge3 >= 99 ? 'fid--ok' : (ge3 >= 95 ? 'fid--warn' : 'fid--bad'), null],
        [k.ge5_pct != null ? fmt1(k.ge5_pct) + '%' : '—', null, null, null],
        [k.l_min != null ? String(k.l_min) : '—', null,
         k.l_min == null ? null : (lmin >= 2 ? 'fid--ok' : 'fid--warn'),
         k.l1_rec_pct != null ? '판정 단일(l=1) 클래스 소속 레코드 ' + fmt1(k.l1_rec_pct) + '%' : null],
        [k.t_mean != null ? String(k.t_mean) : '—', null, null,
         k.t_max != null ? '클래스 가중평균 t · 최대 t=' + k.t_max + ' (순서형 EMD, 0~1)' : null]
      ].forEach(function (cell) {
        var td = document.createElement('td');
        if (cell[1]) td.className = cell[1];
        if (cell[3]) td.title = cell[3];
        if (cell[2]) {
          var b = document.createElement('span');
          b.className = 'fid-badge ' + cell[2];
          b.textContent = cell[0];
          td.appendChild(b);
        } else {
          td.textContent = cell[0];
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });

    // 구조적 점검 체크리스트
    while (el.privacyChecks.firstChild) el.privacyChecks.removeChild(el.privacyChecks.firstChild);
    (p.checks || []).forEach(function (c) {
      var row = document.createElement('div');
      row.className = 'check-row';
      var dot = document.createElement('span');
      dot.className = 'check-dot check--' + (c.status || 'info');
      dot.textContent = c.status === 'safe' ? '✓' : (c.status === 'info' ? 'ℹ' : '!');
      var body = document.createElement('div');
      body.className = 'check-body';
      var it = document.createElement('div');
      it.className = 'check-item';
      it.textContent = c.item || '';
      var de = document.createElement('div');
      de.className = 'check-detail';
      de.textContent = c.detail || '';
      body.appendChild(it);
      body.appendChild(de);
      row.appendChild(dot);
      row.appendChild(body);
      el.privacyChecks.appendChild(row);
    });
  }

  function renderVerify() {
    renderDemographics();
    renderFidelity();
    while (el.verifyList.firstChild) el.verifyList.removeChild(el.verifyList.firstChild);

    if (el.verifyNote) {
      el.verifyNote.textContent =
        'KOSIS 원시: 발행된 전국 분포 대비(코호트 구성 차이 포함). '
        + '내부: 코호트를 통제한 분위수 매핑 충실도. '
        + '파생 항목(LDL·eGFR)은 임상 공식으로 산출해 KOSIS 분포와 의도적으로 다를 수 있습니다.';
    }

    var items = state.data.verify || [];
    items.forEach(function (item) {
      var derived = !!item.derived;
      var rawK = item.raw_kosis_pct || item.kosis_pct || [];
      var rawMax = item.raw_max_diff_pct != null ? item.raw_max_diff_pct : item.max_diff_pct;
      el.verifyList.appendChild(buildVerifyCard({
        title: item.label || item.key || '',
        bins: item.bins || [],
        kosis: rawK,
        synth: item.synth_pct || [],
        kosisLabel: 'KOSIS 원시',
        mainDiff: pickDiff(rawMax, rawK, item.synth_pct),
        mainPrefix: 'KOSIS 원시',
        subDiff: derived ? null : pickDiff(item.max_diff_pct, item.kosis_pct, item.synth_pct),
        subPrefix: '내부',
        caption: derived
          ? '파생값(임상 공식으로 산출) — KOSIS 분포와의 차이는 정상입니다.'
          : null
      }));
    });

    var gc = state.data.grade_compare;
    if (gc && Array.isArray(gc.bins)) {
      var maxDiff = 0;
      for (var i = 0; i < gc.bins.length; i++) {
        var d = Math.abs((Number(gc.kosis_pct[i]) || 0) - (Number(gc.synth_pct[i]) || 0));
        if (d > maxDiff) maxDiff = d;
      }
      el.verifyList.appendChild(buildVerifyCard({
        title: '판정 등급',
        bins: gc.bins,
        kosis: gc.kosis_pct || [],
        synth: gc.synth_pct || [],
        mainDiff: pickDiff(maxDiff, gc.kosis_pct, gc.synth_pct),
        mainPrefix: 'KOSIS',
        subDiff: null,
        caption: '판정 등급 — 참고용: 판정은 규칙 기반 산출이라 차이가 있을 수 있음. '
          + '정상A 건강 양호 · 정상B 자기관리 필요(경계) · 질환의심 2차 검진 필요 · 유질환자 치료 중'
      }));
    }
  }

  // ---------- 전체 렌더 ----------
  function renderAll() {
    renderKpi();
    renderTableA();
    renderTableB();
    renderHeatmap();
    renderVerify();
    renderPrivacy();
  }

  // ---------- 탭 전환 ----------
  function switchTab(key) {
    var tabs = document.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].dataset.tab === key;
      tabs[i].classList.toggle('is-active', on);
      tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
    }
    ['a', 'b', 'c', 'v', 'p'].forEach(function (k) {
      var panel = $('panel-' + k);
      if (panel) panel.hidden = k !== key;
    });
  }

  // ---------- 이벤트 ----------
  function bindEvents() {
    el.form.addEventListener('submit', function (e) {
      e.preventDefault();
      generate();
    });

    el.nSelect.addEventListener('change', function () {
      var custom = el.nSelect.value === 'custom';
      el.nCustom.hidden = !custom;
      if (custom) el.nCustom.focus();
    });

    el.sidoSelect.addEventListener('change', fillSigungu);

    // 신뢰도 탭 — 오차 지표·판정 기준 선택
    function bindSeg(seg, attr, apply) {
      if (!seg) return;
      var btns = seg.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        btns[i].addEventListener('click', function (e) {
          var v = e.currentTarget.dataset[attr];
          if (v == null) return;
          for (var x = 0; x < btns.length; x++) {
            btns[x].classList.toggle('on', btns[x] === e.currentTarget);
          }
          apply(v);
          updateErrRule();
          if (state.data) renderVerify();
        });
      }
    }
    bindSeg(el.errMetricSeg, 'em', function (v) { state.errMetric = v; });
    bindSeg(el.errTolSeg, 'tol', function (v) { state.errTol = parseFloat(v); });

    el.corrRange.addEventListener('input', function () {
      el.corrValue.textContent = parseFloat(el.corrRange.value).toFixed(2);
    });

    // 다운로드는 fetch+Blob — location.href 이동 방식은 서버 세션 소멸(재시작/LRU) 시
    // 대시보드가 404 JSON 페이지로 교체되는 문제가 있다.
    var DL_NAMES = {
      a: 'synthetic_health_a_individual.csv',
      b: 'synthetic_health_b_summary.csv',
      c: 'synthetic_health_c_risk_matrix.csv'
    };
    var dlBtns = document.querySelectorAll('[data-dl]');
    for (var i = 0; i < dlBtns.length; i++) {
      dlBtns[i].addEventListener('click', function (e) {
        var type = e.currentTarget.dataset.dl;
        hideError();
        fetch('/api/download?type=' + encodeURIComponent(type))
          .then(function (res) {
            if (!res.ok) {
              return res.json()
                .catch(function () { throw new Error('HTTP ' + res.status); })
                .then(function (j) { throw new Error(j.error || ('HTTP ' + res.status)); });
            }
            return res.blob();
          })
          .then(function (blob) {
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = DL_NAMES[type] || ('synthetic_' + type + '.csv');
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
          })
          .catch(function (err) {
            showError('다운로드 실패: ' + err.message + ' — 데이터를 다시 생성한 뒤 시도하세요.');
          });
      });
    }

    el.pagePrev.addEventListener('click', function () {
      if (state.page > 1) { state.page--; renderTableA(); }
    });
    el.pageNext.addEventListener('click', function () {
      state.page++;
      renderTableA();
    });

    var tabs = document.querySelectorAll('.tab');
    for (var j = 0; j < tabs.length; j++) {
      tabs[j].addEventListener('click', function (e) {
        switchTab(e.currentTarget.dataset.tab);
      });
    }

    var metricBtns = el.metricSeg.querySelectorAll('button');
    for (var k = 0; k < metricBtns.length; k++) {
      metricBtns[k].addEventListener('click', function (e) {
        var m = e.currentTarget.dataset.metric;
        if (!METRICS[m] || m === state.metric) return;
        state.metric = m;
        for (var x = 0; x < metricBtns.length; x++) {
          metricBtns[x].classList.toggle('on', metricBtns[x] === e.currentTarget);
        }
        if (state.data) renderHeatmap();
      });
    }
  }

  // ---------- 오차 판정 기준 안내 ----------
  function updateErrRule() {
    if (!el.errRuleNote) return;
    var t = state.errTol;
    var m = state.errMetric === 'mae' ? '평균 절대오차' : '최대 절대오차';
    el.errRuleNote.textContent =
      m + ' 기준 — 일치 ≤ ±' + t + '%p · 근접 ≤ ±' + (t * 3) + '%p · 차이 > ±' + (t * 3) + '%p';
  }

  // ---------- 초기화 ----------
  bindEvents();
  setPanelsReady(false);
  setDownloadEnabled(false);
  updateErrRule();
  loadMeta();
})();
