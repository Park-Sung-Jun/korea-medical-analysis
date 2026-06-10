# -*- coding: utf-8 -*-
"""generator.py 단위 테스트 — `python synthetic/test_generator.py` 로 실행."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import generator as g  # noqa: E402

SPEC = g.load_spec()


class TestSpec(unittest.TestCase):
    def test_dist_cells_normalized(self):
        for key, d in list(SPEC["dist"].items()) + list(SPEC["cat"].items()):
            for band in g.AGE_BANDS:
                for sex in g.SEXES:
                    p = d["age"][band][sex]
                    self.assertAlmostEqual(sum(p), 1.0, places=6, msg=f"{key} {band} {sex}")
            for sido in g.SIDOS + ["계"]:
                for sex in g.SEXES:
                    p = d["sido"][sido][sex]
                    self.assertAlmostEqual(sum(p), 1.0, places=6, msg=f"{key} {sido} {sex}")

    def test_grade_shares(self):
        for band in g.AGE_BANDS:
            for sex in g.SEXES:
                tot = sum(SPEC["grade"][band][sex][c] for c in g.GRADE_CATS)
                self.assertAlmostEqual(tot, 1.0, places=6)

    def test_demo_joint_positive(self):
        for sex in g.SEXES:
            tot = sum(SPEC["demo_joint"][sex][s][b] for s in g.SIDOS for b in g.AGE_BANDS)
            self.assertGreater(tot, 1_000_000)  # 수검자 수백만 명 규모

    def test_height_means_plausible(self):
        for sido in list(SPEC["height_mean"].keys()):
            for dec, by_sex in SPEC["height_mean"][sido].items():
                for sex, v in by_sex.items():
                    self.assertTrue(140 <= v <= 185, f"{sido} {dec} {sex} {v}")


class TestGenerate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.meta = g.generate(SPEC, 800, seed=7)

    def test_columns_and_id(self):
        self.assertEqual(self.meta["columns"], g.COLUMNS)
        self.assertEqual(len(self.rows), 800)
        self.assertEqual(self.rows[0]["synthetic_id"], "S000001")
        for r in self.rows[:50]:
            self.assertEqual(list(r.keys()), g.COLUMNS)

    def test_age_matches_band(self):
        for r in self.rows:
            lo, hi = g.BAND_AGE_RANGE[r["age_group"]]
            self.assertTrue(lo <= r["age"] <= hi,
                            f"나이 {r['age']}가 밴드 {r['age_group']} 밖")
            self.assertEqual(r["age_decade"], g.decade_of(r["age"]))

    def test_bmi_weight_height_consistent(self):
        for r in self.rows:
            implied = r["weight"] / (r["height"] / 100.0) ** 2
            self.assertLessEqual(abs(implied - r["bmi"]), 0.06,
                                 f"BMI 불일치: {r['bmi']} vs {implied:.2f}")

    def test_clinical_ranges(self):
        checks = [
            ("height", 135, 200), ("weight", 25, 220), ("bmi", 13.5, 55),
            ("waist", 55, 135), ("sbp", 85, 220), ("dbp", 45, 140),
            ("fasting_glucose", 65, 350), ("hemoglobin", 6, 19.5),
            ("total_cholesterol", 90, 400), ("hdl", 15, 130), ("ldl", 10, 300),
            ("triglyceride", 20, 1200), ("creatinine", 0.4, 8.0),
            ("egfr", 5, 200), ("ast", 8, 400), ("alt", 4, 400), ("ggt", 2, 600),
        ]
        for r in self.rows:
            for field, lo, hi in checks:
                v = r[field]
                self.assertTrue(lo <= v <= hi, f"{field}={v} 범위 밖 [{lo},{hi}]")
            self.assertLess(r["ldl"], r["total_cholesterol"], "LDL ≥ TC")
            # Friedewald 식 일치는 test_ldl_friedewald_derived에서 정확 검증.
            # 여기선 하한(10, 측정한계) 위로 상한만 점검.
            if r["ldl"] > 10 and r["triglyceride"] < 400:
                cap = r["total_cholesterol"] - r["hdl"] - r["triglyceride"] / 5.0
                self.assertLessEqual(r["ldl"], cap + 0.5 + 1e-9, "Friedewald 상한 위반")
            self.assertIn(r["urine_protein"], g.URINE_CATS)
            self.assertIn(r["chest_xray"], g.XRAY_CATS)
            self.assertIn(r["result_grade"], g.GRADE_CATS)
            for vf in ("vision_left", "vision_right"):
                if r[vf] is not None:
                    self.assertTrue(0.1 <= r[vf] <= 2.0)

    def test_egfr_is_ckd_epi_derived(self):
        # eGFR가 Cr·연령·성별의 CKD-EPI 결정함수와 일치하는지(클램프 구간 제외)
        for r in self.rows:
            expected = g._ckd_epi_2021(r["creatinine"], r["age"], r["sex"] == "남자")
            if 5.0 <= expected <= 200.0:
                self.assertLessEqual(abs(r["egfr"] - expected), 0.6,
                                     f"eGFR {r['egfr']} ≠ CKD-EPI {expected:.1f}")
            # eGFR는 Cr 역방향 — 높은 Cr이 낮은 eGFR로
        pairs = sorted((r["creatinine"], r["egfr"]) for r in self.rows)
        self.assertGreaterEqual(pairs[0][1], pairs[-1][1])

    def test_bone_density_only_target_women(self):
        for r in self.rows:
            if r["bone_density"]:
                self.assertEqual(r["sex"], "여자")
                self.assertIn(r["age"], (54, 66))
                self.assertIn(r["bone_density"], ("정상", "골감소증", "골다공증"))
            elif r["sex"] == "여자" and r["age"] in (54, 66):
                self.fail("골밀도 대상인데 값 없음")

    def test_deterministic_with_seed(self):
        rows2, _ = g.generate(SPEC, 800, seed=7)
        self.assertEqual(self.rows[0], rows2[0])
        self.assertEqual(self.rows[-1], rows2[-1])

    def test_sido_filter(self):
        rows, _ = g.generate(SPEC, 300, sido="서울특별시", seed=11)
        self.assertTrue(all(r["sido"] == "서울특별시" for r in rows))

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            g.generate(SPEC, 10)
        with self.assertRaises(ValueError):
            g.generate(SPEC, g.MAX_ROWS + 1)  # 상한 50만 초과
        with self.assertRaises(ValueError):
            g.generate(SPEC, 1000, sido="서울시")
        with self.assertRaises(ValueError):
            g.generate(SPEC, 1000, corr=float("nan"))
        with self.assertRaises(ValueError):
            g.generate(SPEC, 1000, corr=float("inf"))
        with self.assertRaises(ValueError):
            g.generate(SPEC, 1000, sigungu="없는구")
        with self.assertRaises(ValueError):
            # 시군구가 선택한 시도 소속이 아니면 거부
            g.generate(SPEC, 1000, sido="부산광역시", sigungu="11110")

    def test_max_rows_is_500k(self):
        self.assertEqual(g.MAX_ROWS, 500000)

    def test_corr_bounds_clamped(self):
        # 범위 밖 corr는 클램프되어 정상 생성(예외 없음)
        for c in (-1.0, 5.0, 0.0):
            rows, meta = g.generate(SPEC, 200, seed=3, corr=c)
            self.assertEqual(len(rows), 200)
            self.assertTrue(0.0 <= meta["corr"] <= 1.5)


class TestFidelityAndAgg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.meta = g.generate(SPEC, 6000, seed=42)
        cls.verify = g.verify_report(SPEC, cls.rows)

    def test_marginals_close_to_kosis(self):
        # 내부 정합성(분위수 매핑 충실도): 파생항목(eGFR) 제외 구간 오차 ≤2.5%p
        for v in self.verify:
            if v.get("derived"):
                continue
            self.assertLess(v["max_diff_pct"], 2.5,
                            f"{v['key']} 내부 정합성 오차 과대: {v['max_diff_pct']}%p")

    def test_egfr_derived_fidelity_reasonable(self):
        # eGFR는 Cr 분포 기반 파생이라 KOSIS eGFR 분포와 다소 어긋날 수 있으나 과대하면 안 됨
        ev = next(v for v in self.verify if v["key"] == "egfr")
        self.assertTrue(ev["derived"])
        self.assertLess(ev["raw_max_diff_pct"], 12.0,
                        f"eGFR 파생 분포 KOSIS 괴리 과대: {ev['raw_max_diff_pct']}%p")

    def test_ldl_friedewald_derived(self):
        # LDL은 Friedewald 식으로 파생 — TG<400 레코드는 정확히 식과 일치
        lv = next(v for v in self.verify if v["key"] == "ldl")
        self.assertTrue(lv["derived"])
        for r in self.rows:
            if r["triglyceride"] < 400:
                expected = max(10, round(r["total_cholesterol"] - r["hdl"]
                                         - r["triglyceride"] / 5.0))
                self.assertEqual(r["ldl"], expected, "LDL이 Friedewald 식과 불일치")

    def test_mean_abs_diff_small(self):
        self.assertLess(g.mean_abs_diff(self.verify), 1.0)  # 내부 정합성
        self.assertLess(g.mean_abs_diff(self.verify, "raw_kosis_pct"), 2.0)  # 대외 충실도

    def test_summary_b(self):
        b = g.summary_b(self.rows)
        self.assertEqual(len(b), len(g.B_ITEMS))
        for item in b:
            tot = item["normal_pct"] + item["caution_pct"] + item["disease_pct"]
            self.assertAlmostEqual(tot, 100.0, delta=0.3)

    def test_matrix_c(self):
        c = g.matrix_c(self.rows)
        self.assertGreater(len(c), 50)  # 17개 시도 × 다수 연령대
        for cell in c:
            self.assertIn(cell["risk_level"], ("낮음", "보통", "높음"))
            self.assertGreaterEqual(cell["n"], 1)

    def test_grade_compare_shape(self):
        gc = g.grade_compare(SPEC, self.rows)
        self.assertEqual(gc["bins"], g.GRADE_CATS)
        self.assertAlmostEqual(sum(gc["synth_pct"]), 100.0, delta=0.5)
        self.assertAlmostEqual(sum(gc["kosis_pct"]), 100.0, delta=0.5)

    def test_csv_serialization(self):
        text = g.rows_to_csv(self.rows[:10])
        lines = text.strip().split("\n")
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[0].split(",")[0], "synthetic_id")

    def test_fidelity_has_mae_stats(self):
        fid = g.fidelity_breakdown(SPEC, self.rows)
        self.assertEqual(len(fid), len(g.FIDELITY_KEYS))
        for item in fid:
            for dim in ("age_sex", "sido"):
                st = item[dim]
                if st["mean"] is not None:
                    self.assertIsNotNone(st["mae_mean"])
                    # 평균 절대오차 ≤ 최대 절대오차(정의상)
                    self.assertLessEqual(st["mae_mean"], st["mean"] + 1e-9)
                    self.assertLessEqual(st["mae_max"], st["max"] + 1e-9)

    def test_sido_risk_compare(self):
        sc = g.sido_risk_compare(SPEC, self.rows)
        self.assertIsNotNone(sc)
        self.assertEqual(len(sc["bins"]), len(sc["synth_pct"]))
        self.assertEqual(len(sc["bins"]), len(sc["kosis_pct"]))
        # KOSIS 시도 비만율은 합리적 범위(20~60%)여야 함
        for v in sc["kosis_pct"]:
            self.assertTrue(20.0 <= v <= 60.0, f"KOSIS 비만율 비정상: {v}")


class TestSigungu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.meta = g.generate(SPEC, 2000, sigungu="11110", seed=21)

    def test_spec_has_sigungu(self):
        self.assertEqual(SPEC.get("spec_version"), g.SPEC_VERSION)
        self.assertGreaterEqual(len(SPEC["sigungu"]), 200)
        lst = g.list_sigungu(SPEC)
        self.assertIn("서울특별시", lst)
        self.assertGreaterEqual(len(lst["서울특별시"]), 25)

    def test_sigungu_rows(self):
        self.assertTrue(all(r["sido"] == "서울특별시" for r in self.rows))
        self.assertTrue(all(r["sigungu"] == "종로구" for r in self.rows))
        self.assertEqual(self.meta["sigungu"], "종로구")
        self.assertEqual(self.meta["sigungu_code"], "11110")

    def test_sigungu_by_name(self):
        rows, meta = g.generate(SPEC, 300, sigungu="서귀포시", seed=3)
        self.assertEqual(meta["sigungu_code"], "50130")
        self.assertTrue(all(r["sido"] == "제주특별자치도" for r in rows))

    def test_sigungu_demo_verify(self):
        demo = g.demographic_verify(SPEC, self.rows, "서울특별시", sigungu="11110")
        keys = [d["key"] for d in demo]
        self.assertEqual(keys, ["sex", "age"])  # 단일 지역 — 시도 카드 없음
        for d in demo:
            self.assertLess(d["max_diff_pct"], 6.0,
                            f"{d['key']} 시군구 인구 구성 오차 과대")

    def test_non_sigungu_rows_have_empty_sigungu(self):
        rows, _ = g.generate(SPEC, 200, seed=1)
        self.assertTrue(all(r["sigungu"] == "" for r in rows))


class TestPrivacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, _ = g.generate(SPEC, 5000, seed=13)
        cls.report = g.privacy_report(cls.rows)

    def test_k_rows_fields(self):
        for k in self.report["k_anonymity"]:
            for f in ("classes", "min_k", "unique_pct", "ge3_pct", "ge5_pct",
                      "l_min", "l1_rec_pct", "t_mean", "t_max"):
                self.assertIn(f, k)
            self.assertGreaterEqual(k["ge3_pct"], k["ge5_pct"])  # k≥3이 더 느슨
            self.assertTrue(1 <= k["l_min"] <= len(g.GRADE_CATS))
            self.assertTrue(0.0 <= k["t_mean"] <= 1.0)
            self.assertLessEqual(k["t_mean"], k["t_max"] + 1e-9)

    def test_level_uses_k3(self):
        # 5000행 전국이면 5세 QI에서 k≥3 충족률이 95% 이상이어야 정상
        demo = self.report["k_anonymity"][1]
        self.assertGreaterEqual(demo["ge3_pct"], 95.0)
        self.assertIn(self.report["level"], ("낮음", "보통", "주의"))

    def test_t_closeness_emd_correctness(self):
        # 수작업 검증: 전체 분포와 동일한 클래스 → t=0
        rows = [{"sido": "서울특별시", "sigungu": "", "age": 40, "age_group": "40 ~ 44세",
                 "age_decade": "40대", "sex": "남자", "result_grade": gc}
                for gc in g.GRADE_CATS * 25]
        rep = g.privacy_report(rows)
        for k in rep["k_anonymity"]:
            self.assertEqual(k["t_mean"], 0.0)
            self.assertEqual(k["l_min"], 4)

    def test_sigungu_mode_qi_label(self):
        rows, _ = g.generate(SPEC, 500, sigungu="11110", seed=2)
        rep = g.privacy_report(rows)
        self.assertTrue(rep["k_anonymity"][0]["qi"].startswith("시군구"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
