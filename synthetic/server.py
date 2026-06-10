# -*- coding: utf-8 -*-
"""합성 건강검진 데이터 대시보드 서버 (표준 라이브러리만 사용).

  python synthetic/server.py            # http://localhost:8081
  python synthetic/server.py --port 8082

엔드포인트
  GET  /                → synthetic/ 정적 파일(대시보드)
  GET  /api/health      → 헬스체크
  GET  /api/meta        → 시도 목록·상태(세션별)
  POST /api/generate    → 합성데이터 생성 {n, sido, seed, corr}
  GET  /api/download    → ?type=a|b|c CSV 다운로드(세션별)

배포(다중 사용자) 주의
  - 생성 결과는 사용자별로 격리한다. 사용자 식별은 oauth2-proxy가 넘기는
    `X-Auth-Request-Email` 헤더(또는 Cf-Access-* / X-Forwarded-*)를 쓰며,
    없으면 단일 'default' 세션이 된다(로컬·단독 사용).
  - 외부 노출은 nginx+oauth2-proxy 뒤에 두고 앱은 127.0.0.1만 바인드한다
    (DEPLOY.md 표준). 대용량(50만 행) 생성은 수 분 걸리므로 nginx
    proxy_read_timeout을 600s로 올린다. 환경변수로 제어:
      SYNTH_HOST(기본 127.0.0.1) · SYNTH_PORT(8081) · SYNTH_MAX_N(500000)
      SYNTH_SESSION_MAX(16) · SYNTH_SESSION_MB(총 세션 메모리 상한, 기본 512)
      SYNTH_TRUST_PROXY(1이면 X-Forwarded-* 신뢰)
  - 세션에는 행 객체가 아니라 직렬화된 CSV 문자열을 저장한다(50만 행 기준
    행 dict ~600MB → CSV ~70MB). 총량이 SYNTH_SESSION_MB를 넘으면 LRU 제거.
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import generator  # noqa: E402

MAX_N = int(os.environ.get("SYNTH_MAX_N", str(generator.MAX_ROWS)))
SESSION_MAX = int(os.environ.get("SYNTH_SESSION_MAX", "16"))
SESSION_MB = int(os.environ.get("SYNTH_SESSION_MB", "512"))
TRUST_PROXY = os.environ.get("SYNTH_TRUST_PROXY", "0") == "1"

# 사용자(세션) 키 → {"csv_a","b","c","meta","bytes"} — 개수·총바이트 LRU
_SESSIONS = OrderedDict()
_LOCK = threading.Lock()
_GEN_BUSY = threading.Lock()

SPEC = None  # main()에서 채움

# 정적 서빙 화이트리스트 — 이 외 경로(.py/.md 등 소스)는 404
STATIC_WHITELIST = {"/", "/index.html", "/app.js", "/styles.css", "/favicon.ico"}


def _store_session(key, data):
    # csv_a는 UTF-8 bytes로 저장 — 메모리 계량이 정확하고 다운로드 시 재인코딩 없음
    data["bytes"] = len(data.get("csv_a") or b"")
    with _LOCK:
        _SESSIONS[key] = data
        _SESSIONS.move_to_end(key)
        total = sum(d["bytes"] for d in _SESSIONS.values())
        while len(_SESSIONS) > 1 and (
                len(_SESSIONS) > SESSION_MAX or total > SESSION_MB * 1_000_000):
            _k, dropped = _SESSIONS.popitem(last=False)
            total -= dropped["bytes"]


def _get_session(key):
    with _LOCK:
        d = _SESSIONS.get(key)
        if d is not None:
            _SESSIONS.move_to_end(key)
        return d


def _load_spec_once():
    t0 = time.time()
    spec = generator.load_spec()
    print(f"[server] 스펙 로드 완료 {time.time() - t0:.1f}s")
    return spec


class Handler(SimpleHTTPRequestHandler):
    timeout = 60  # 유휴/지연 소켓 차단 — 본문 미전송 클라이언트의 스레드 영구 점유 방지

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    # ---- 세션 식별
    def _session_key(self):
        # 인증 프록시가 넘긴 이메일로 사용자별 데이터 격리.
        # oauth2-proxy(X-Auth-Request-Email / X-Forwarded-Email) 또는
        # Cloudflare Access(Cf-Access-Authenticated-User-Email)를 지원한다.
        # 헤더 위조 방지를 위해 프록시 주입 헤더는 전부 TRUST_PROXY=1일 때만
        # 신뢰한다(앱은 127.0.0.1만 바인드하고 외부 포트를 열지 않는 전제).
        if TRUST_PROXY:
            email = (self.headers.get("X-Auth-Request-Email")
                     or self.headers.get("X-Forwarded-Email")
                     or self.headers.get("X-Forwarded-User")
                     or self.headers.get("Cf-Access-Authenticated-User-Email"))
            if email:
                return "u:" + email.strip().lower()
        return "default"

    # ---- 공통 응답 헬퍼
    def end_headers(self):
        # 모든 응답(정적·API·에러)에 기본 보안 헤더 주입
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        # CSP: 자체 호스팅 자산만 사용(외부 CDN 없음). 인라인 스크립트 없음(app.js 분리).
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'self'")
        super().end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _csv(self, data, filename):
        """CSV 응답(엑셀 호환 UTF-8 BOM). data는 str 또는 bytes.

        BOM과 본문을 분리 전송해 대용량 CSV의 concat 사본(순간 2배 메모리)을 만들지 않는다."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(3 + len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b"\xef\xbb\xbf")
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # 콘솔 소음 축소
        # 에러 로깅 경로(log_error)는 args[0]이 HTTPStatus 등 비문자열일 수 있다.
        first = args[0] if args else ""
        if not isinstance(first, str) or "/api/" in first:
            super().log_message(fmt, *args)

    # ---- 라우팅
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json({"ok": True, "status": "healthy",
                               "year": generator.LATEST_YEAR})
        if parsed.path == "/api/meta":
            d = _get_session(self._session_key())
            meta = d["meta"] if d else None
            return self._json({
                "ok": True,
                "sidos": ["전체"] + list(generator.SIDOS),
                "sigungu_map": generator.list_sigungu(SPEC) if SPEC else {},
                "year": generator.LATEST_YEAR,
                "has_data": meta is not None,
                "max_n": MAX_N,
                "last_meta": meta,
            })
        if parsed.path == "/api/download":
            qs = parse_qs(parsed.query)
            typ = (qs.get("type") or ["a"])[0].lower()
            d = _get_session(self._session_key())
            if not d:
                return self._json({"ok": False, "error": "생성된 데이터가 없습니다. 먼저 생성하세요."}, 404)
            if typ == "a":
                return self._csv(d["csv_a"], "synthetic_health_a_individual.csv")
            if typ == "b":
                return self._csv(generator.dicts_to_csv(d["b"], generator.B_FIELDS),
                                 "synthetic_health_b_summary.csv")
            if typ == "c":
                return self._csv(generator.dicts_to_csv(d["c"], generator.C_FIELDS),
                                 "synthetic_health_c_risk_matrix.csv")
            return self._json({"ok": False, "error": "type은 a|b|c 중 하나여야 합니다."}, 400)
        if parsed.path.startswith("/api/"):
            return self._json({"ok": False, "error": "알 수 없는 API"}, 404)
        # 정적 파일은 프론트 자산만 화이트리스트로 노출. server.py/generator.py/
        # DEPLOY.md 등 소스·문서가 인증 사용자에게 다운로드되는 것을 차단(정찰정보 노출 방지).
        if parsed.path not in STATIC_WHITELIST:
            return self._json({"ok": False, "error": "찾을 수 없습니다."}, 404)
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/generate":
            return self._json({"ok": False, "error": "알 수 없는 API"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not (0 <= length <= 1_000_000):
                # 음수 길이는 rfile.read(-1)이 EOF까지 블록하므로 read 전에 차단
                return self._json({"ok": False, "error": "요청 본문 크기가 올바르지 않습니다."}, 400)
            payload = json.loads(self.rfile.read(length) or b"{}")
            n = int(payload.get("n") or 10000)
            sido = str(payload.get("sido") or "전체")
            sigungu = str(payload.get("sigungu") or "").strip() or None
            seed = payload.get("seed")
            seed = int(seed) if seed not in (None, "") else None
            corr = float(payload.get("corr") if payload.get("corr") is not None else 1.0)
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "요청 형식이 올바르지 않습니다."}, 400)

        if not (100 <= n <= MAX_N):
            return self._json({"ok": False, "error": f"표본 수는 100~{MAX_N:,} 범위여야 합니다."}, 400)

        if not _GEN_BUSY.acquire(blocking=False):
            return self._json({"ok": False, "error": "이미 생성 작업이 진행 중입니다. 잠시 후 다시 시도하세요."}, 429)
        # 락 구간은 생성→집계→세션 저장까지만. 응답 직렬화·소켓 전송은 락 해제 후
        # 수행한다(느린 수신 클라이언트가 전역 생성 락을 점유하는 것 방지).
        try:
            resp, status = self._do_generate(n, sido, sigungu, seed, corr)
        except ValueError as e:
            resp, status = {"ok": False, "error": str(e)}, 400
        except Exception as e:  # noqa: BLE001 — 사용자에게 원인 전달
            resp, status = {"ok": False, "error": f"생성 실패: {e}"}, 500
        finally:
            _GEN_BUSY.release()
        try:
            return self._json(resp, status)
        except (ConnectionError, BrokenPipeError):
            return  # 클라이언트가 응답 전송 중 끊음 — 재전송 시도 안 함

    def _do_generate(self, n, sido, sigungu, seed, corr):
        """생성+검증 리포트 일괄 수행(_GEN_BUSY 락 하에서 호출). 반환: (응답 dict, 상태코드)."""
        rows, meta = generator.generate(SPEC, n, sido=sido, seed=seed, corr=corr,
                                        sigungu=sigungu)
        b = generator.summary_b(rows)
        c = generator.matrix_c(rows)
        ver = generator.verify_report(SPEC, rows)
        demo = generator.demographic_verify(SPEC, rows, sido, sigungu=sigungu)
        fidelity = generator.fidelity_breakdown(SPEC, rows)
        privacy = generator.privacy_report(rows)
        gc = generator.grade_compare(SPEC, rows)
        sido_cmp = generator.sido_risk_compare(SPEC, rows)
        grade_cnt = {g: 0 for g in generator.GRADE_CATS}
        for r in rows:
            grade_cnt[r["result_grade"]] += 1
        kpi = {
            "rows": len(rows),
            "elapsed_ms": meta["elapsed_ms"],
            # 헤드라인은 대외 충실도(KOSIS 원시 전국 대비), 내부 정합성은 보조
            "mean_abs_diff_pct": generator.mean_abs_diff(ver, "raw_kosis_pct"),
            "mean_abs_diff_internal_pct": generator.mean_abs_diff(ver, "kosis_pct"),
            "grade_dist": {g: round(grade_cnt[g] * 100.0 / len(rows), 1)
                           for g in generator.GRADE_CATS},
        }
        preview = rows[:200]
        # 세션엔 UTF-8 bytes CSV만 저장(대용량 행 객체 장기 보유 방지 + 정확한 계량)
        csv_a = generator.rows_to_csv(rows).encode("utf-8")
        del rows
        _store_session(self._session_key(),
                       {"csv_a": csv_a, "meta": meta, "b": b, "c": c})
        return {
            "ok": True, "meta": meta, "kpi": kpi,
            "preview": preview,
            "summary_b": b, "matrix_c": c,
            "verify": ver, "grade_compare": gc,
            "demographics": demo, "fidelity": fidelity, "privacy": privacy,
            "sido_compare": sido_cmp,
        }, 200


def main():
    global SPEC
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("SYNTH_PORT", "8081")))
    ap.add_argument("--host", default=os.environ.get("SYNTH_HOST", "127.0.0.1"))
    args = ap.parse_args()
    SPEC = _load_spec_once()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[server] http://{args.host}:{args.port}  (Ctrl+C로 종료)")
    print(f"[server] MAX_N={MAX_N:,} · SESSION_MAX={SESSION_MAX} · TRUST_PROXY={TRUST_PROXY}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] 종료")


if __name__ == "__main__":
    main()
