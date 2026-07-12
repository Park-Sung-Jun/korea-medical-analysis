"""공유 시군구 경계 정본과 KOSIS 인구를 결합해 ``sigungu.geojson``을 만든다.

경계 정본: ``shared_data/admin_boundaries/sgg.geojson`` (admdongkor 20251231)
인구 기준: ``data/population.json``의 KOSIS 최신 확정 월

고령화지수 = (65세 이상 인구 / 0-14세 인구) * 100
  - 0-14세: 0-4, 5-9, 10-14
  - 65세 이상: 65-69 ... 100+

출력 properties: code, name, sido, aging_index, pop_total,
elderly_share(%), youth_share(%)
"""

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
POP = DATA / "population.json"
OUT = DATA / "sigungu.geojson"
BOUNDARY = ROOT.parent / "shared_data" / "admin_boundaries" / "sgg.geojson"

BOUNDARY_SOURCE = "shared_data/admin_boundaries/sgg.geojson (admdongkor)"
BOUNDARY_BASIS_DATE = "2025-12-31"


def aging_from_pop(reg):
    m, f = reg["m"], reg["f"]
    youth = sum(m[0:3]) + sum(f[0:3])
    elderly = sum(m[13:21]) + sum(f[13:21])
    total = reg.get("total", sum(m) + sum(f))
    idx = (elderly / youth * 100.0) if youth else None
    return {
        "aging_index": round(idx, 1) if idx is not None else None,
        "pop_total": total,
        "elderly_share": round(elderly / total * 100, 1) if total else None,
        "youth_share": round(youth / total * 100, 1) if total else None,
    }


def main():
    if not BOUNDARY.exists():
        raise SystemExit(f"공유 시군구 경계 정본 없음: {BOUNDARY}")
    if not POP.exists():
        raise SystemExit(f"인구 데이터 없음: {POP}")

    population_document = json.loads(POP.read_text(encoding="utf-8"))
    population = population_document["regions"]
    population_meta = population_document.get("meta", {})
    population_source = population_meta.get("source")
    population_basis_date = population_meta.get("date")
    if not population_source or not population_basis_date:
        raise SystemExit("인구 데이터 meta.source 또는 meta.date가 없습니다.")
    boundary = json.loads(BOUNDARY.read_text(encoding="utf-8"))

    features = []
    missing_population = []
    seen_codes = set()

    for feature in boundary.get("features", []):
        source_props = feature.get("properties") or {}
        code = str(source_props.get("sggcd", "")).strip()
        name = str(source_props.get("sggnm", "")).strip()
        sido = str(source_props.get("sidonm", "")).strip()
        geometry = feature.get("geometry")

        if not code or not name or not sido or not geometry:
            raise SystemExit(f"경계 필수값 누락: code={code!r}, name={name!r}")
        if code in seen_codes:
            raise SystemExit(f"경계 코드 중복: {code}")
        seen_codes.add(code)

        props = {"code": code, "name": name, "sido": sido}
        if code not in population:
            missing_population.append((code, name))
        else:
            props.update(aging_from_pop(population[code]))

        features.append(
            {"type": "Feature", "properties": props, "geometry": geometry}
        )

    if not features:
        raise SystemExit("공유 시군구 경계에 feature가 없습니다.")
    if missing_population:
        sample = ", ".join(f"{code} {name}" for code, name in missing_population[:10])
        raise SystemExit(
            f"인구 데이터 미매칭 {len(missing_population)}개: {sample}"
        )

    collection = {
        "type": "FeatureCollection",
        "meta": {
            "boundary_source": BOUNDARY_SOURCE,
            "boundary_basis_date": BOUNDARY_BASIS_DATE,
            "boundary_status": "shared_canonical",
            "population_source": population_source,
            "population_basis_date": population_basis_date,
            "aging_formula": "65+/(0-14)*100",
            "count": len(features),
        },
        "features": features,
    }
    OUT.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")

    values = sorted(
        feature["properties"]["aging_index"]
        for feature in features
        if feature["properties"]["aging_index"] is not None
    )
    print(f"저장: {OUT}  시군구={len(features)}")
    if values:
        n = len(values)
        print(
            f"고령화지수 분포 n={n} min={values[0]} "
            f"t33={values[n // 3]} t66={values[2 * n // 3]} max={values[-1]}"
        )


if __name__ == "__main__":
    main()
