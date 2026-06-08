"""
pop_202512.json(3904개 지역, 연령·성별)에서 시군구(250)만 추려
data/pop_pyramid.json 생성 — sigungu_bivariate.geojson 의 code 로 키를 맞춘다.
지도에서 시군구 클릭 시 그 지역 인구 피라미드(연령·성별)를 즉시 렌더.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def reverse_remap(code):
    """sigungu(신코드) -> pop가 쓸 수 있는 구코드 후보."""
    cands = [code]
    if code.startswith("51"):
        cands.append("42" + code[2:])
    if code.startswith("52"):
        cands.append("45" + code[2:])
    if code == "27720":
        cands.append("47720")
    return cands


def main():
    pop = json.loads((DATA / "pop_202512.json").read_text(encoding="utf-8"))
    bands = pop["meta"]["bands"]
    regions = pop["regions"]  # code -> {name,m,f,total}
    # 이름 인덱스(폴백)
    by_name = {}
    for code, r in regions.items():
        by_name.setdefault(r.get("name", ""), code)

    # 연령대 인덱스: 65+ = '65-69' 이후, 0–14 = '15-19' 이전
    i65 = bands.index("65-69")
    i15 = bands.index("15-19")

    biv = json.loads((DATA / "sigungu_bivariate.geojson").read_text(encoding="utf-8"))
    out = {}
    matched = name_fb = miss = 0
    for f in biv["features"]:
        p = f["properties"]
        code = str(p.get("code", "")).strip()
        name = p.get("name", "")
        reg = None; pcode = None
        for c in reverse_remap(code):
            if c in regions:
                reg = regions[c]; pcode = c; break
        if reg is None and name in by_name:
            pcode = by_name[name]; reg = regions[pcode]; name_fb += 1
        if reg is None:
            miss += 1
            continue
        matched += 1
        out[code] = {"name": (p.get("sido", "") + " " + name).strip(),
                     "m": reg["m"], "f": reg["f"], "pcode": pcode}
        # 절대 인구 지표를 시군구 feature에 병합 (지수만으로는 안 보이는 '실제 영향 규모')
        elderly = sum(reg["m"][i65:]) + sum(reg["f"][i65:])   # 65세 이상
        youth = sum(reg["m"][:i15]) + sum(reg["f"][:i15])     # 0–14세
        total = sum(reg["m"]) + sum(reg["f"])
        p["elderly_pop"] = elderly
        p["youth_pop"] = youth
        p["pop_total"] = total

    result = {"bands": bands, "regions": out}
    (DATA / "pop_pyramid.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    # 고령인구 병합된 geojson 저장
    biv.setdefault("meta", {})["pop_fields"] = "elderly_pop=65세이상, youth_pop=0–14세, pop_total (KOSIS 2025.12)"
    (DATA / "sigungu_bivariate.geojson").write_text(json.dumps(biv, ensure_ascii=False), encoding="utf-8")
    print(f"저장: data/pop_pyramid.json  시군구 {matched}/{len(biv['features'])} "
          f"(이름폴백 {name_fb}, 미매칭 {miss})  크기 {(DATA/'pop_pyramid.json').stat().st_size//1024} KB")
    print("sigungu_bivariate.geojson 에 elderly_pop/youth_pop/pop_total 병합 완료")
    # 지수 vs 절대수 대비 예시
    feats = [f["properties"] for f in biv["features"] if f["properties"].get("elderly_pop") is not None]
    hi_idx_low_cnt = sorted([p for p in feats if p.get("aging_index")],
                            key=lambda p: -p["aging_index"])[:5]
    print("고령화지수 최상위 5(지수 / 실제 고령인구):")
    for p in hi_idx_low_cnt:
        print(f"  {p.get('sido','')} {p.get('name','')}: 지수 {p['aging_index']:.0f} / 고령인구 {p['elderly_pop']:,}명")


if __name__ == "__main__":
    main()
