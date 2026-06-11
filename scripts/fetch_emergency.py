"""
E-Gen(국립중앙의료원) 전국 응급의료기관 목록 취득 → data/emergency.geojson

API: data.go.kr B552657 ErmctInfoInqireService.getEgytListInfoInqire (자동승인형)
키: .env DATAGOKR_API_KEY (HIRA와 동일 키, raw 그대로 사용 — 이미 인코딩된 키)

응급의료기관 분류(dutyEmclsName):
  권역응급의료센터 / 지역응급의료센터 / 지역응급의료기관 (+ 전문응급의료센터 등)
좌표: wgs84Lon / wgs84Lat

usage: python scripts/fetch_emergency.py
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _env  # noqa: F401,E402  (.env 자동 로드)

DATA = HERE.parent / "data"
URL = "https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire"


def fetch_all():
    key = os.environ["DATAGOKR_API_KEY"]
    rows, page = [], 1
    while True:
        # 키는 이미 인코딩됨 — params= 로 병합하면 requests가 %를 재인코딩해 403
        r = requests.get(
            f"{URL}?serviceKey={key}&pageNo={page}&numOfRows=1000&_type=json", timeout=60)
        r.raise_for_status()
        body = r.json()["response"]["body"]
        items = body.get("items") or {}
        item = items.get("item") if isinstance(items, dict) else items
        if not item:
            break
        if isinstance(item, dict):
            item = [item]
        rows.extend(item)
        total = int(body.get("totalCount") or 0)
        print(f"  page {page}: +{len(item)} (누적 {len(rows)}/{total})")
        if len(rows) >= total:
            break
        page += 1
        time.sleep(0.3)
    return rows


def main():
    rows = fetch_all()
    feats, skipped = [], 0
    cls_count = {}
    for r in rows:
        try:
            lon, lat = float(r["wgs84Lon"]), float(r["wgs84Lat"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        def s(k):  # 일부 필드가 숫자로 옴(전화번호 등) — 문자열 정규화
            v = r.get(k)
            return "" if v is None else str(v).strip()
        cls = s("dutyEmclsName")
        cls_count[cls] = cls_count.get(cls, 0) + 1
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "name": s("dutyName"),
                "cls": cls,                              # 응급의료기관 분류
                "addr": s("dutyAddr"),
                "tel": s("dutyTel1"),
                "hpid": s("hpid"),
            },
        })
    out = {"type": "FeatureCollection",
           "meta": {"source": "E-Gen getEgytListInfoInqire", "count": len(feats),
                    "by_class": cls_count},
           "features": feats}
    (DATA / "emergency.geojson").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"저장: data/emergency.geojson  기관 {len(feats)}개 (좌표결측 {skipped})")
    for k, v in sorted(cls_count.items(), key=lambda x: -x[1]):
        print(f"  {k or '(미분류)'}: {v}")


if __name__ == "__main__":
    main()
