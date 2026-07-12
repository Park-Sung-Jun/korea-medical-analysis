# -*- coding: utf-8 -*-
"""KOSIS 건강검진통계 기반 개인단위 합성 건강검진 데이터 생성기.

원리
----
1) 인구학: 시군구 인구피라미드(pop_pyramid.json)를 시도로 합산해 시드로 쓰고,
   KOSIS 수검인원 마진(연령밴드×성별 N002_1, 시도×성별 N056)에 IPF로 맞춘
   결합분포에서 (시도, 연령밴드, 성별)을 추출. 나이는 밴드 내 피라미드 가중으로 1세 단위 추출.
2) 검사수치: 항목별 잠재 정규변수에 의료적 상관구조(BMI→혈압/혈당/지질/간수치,
   크레아티닌→eGFR, 좌안→우안 시력 등)를 부여한 뒤, (연령밴드×성별) 분포를
   (시도×성별) 분포로 레이킹한 KOSIS 구간분포의 역CDF로 분위수 매핑(가우시안 코퓰라).
   → 주변분포는 KOSIS와 일치, 항목 간 상관은 보존.
3) 판정: 검진 기준으로 result_grade(정상A/정상B/질환의심)와 위험군 플래그를 산출하고,
   유질환자는 N099 판정현황의 셀별 비율에 위험잠재변수를 결합한 임계모형으로 부여.

외부 의존성 없음(표준 라이브러리만 사용).
"""

import argparse
import csv
import io
import json
import math
import os
import random
import sys
import time
from bisect import bisect_right
from collections import Counter
from statistics import NormalDist

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SPEC_PATH = os.path.join(DATA_DIR, "synthetic_baseline.json")

_ND = NormalDist()
_PHI = _ND.cdf
_INV = _ND.inv_cdf

# ---------------------------------------------------------------- 상수 정의

AGE_BANDS = [
    "19세 이하", "20 ~ 24세", "25 ~ 29세", "30 ~ 34세", "35 ~ 39세",
    "40 ~ 44세", "45 ~ 49세", "50 ~ 54세", "55 ~ 59세", "60 ~ 64세",
    "65 ~ 69세", "70 ~ 74세", "75 ~ 79세", "80 ~ 84세", "85세 이상",
]
SEXES = ["남자", "여자"]
SIDOS = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
    "경상남도", "제주특별자치도",
]
SIDO_BY_PREFIX = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시",
    "29": "광주광역시", "30": "대전광역시", "31": "울산광역시", "36": "세종특별자치시",
    "41": "경기도", "42": "강원특별자치도", "51": "강원특별자치도",
    "43": "충청북도", "44": "충청남도", "45": "전북특별자치도", "52": "전북특별자치도",
    "46": "전라남도", "47": "경상북도", "48": "경상남도", "50": "제주특별자치도",
}
# KOSIS 연령밴드 → 인구피라미드 5세 밴드(나이 시작값) 매핑
BAND_AGE_RANGE = {
    "19세 이하": (15, 19), "20 ~ 24세": (20, 24), "25 ~ 29세": (25, 29),
    "30 ~ 34세": (30, 34), "35 ~ 39세": (35, 39), "40 ~ 44세": (40, 44),
    "45 ~ 49세": (45, 49), "50 ~ 54세": (50, 54), "55 ~ 59세": (55, 59),
    "60 ~ 64세": (60, 64), "65 ~ 69세": (65, 69), "70 ~ 74세": (70, 74),
    "75 ~ 79세": (75, 79), "80 ~ 84세": (80, 84), "85세 이상": (85, 104),
}
PYR_BANDS = ["0-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39",
             "40-44", "45-49", "50-54", "55-59", "60-64", "65-69", "70-74",
             "75-79", "80-84", "85-89", "90-94", "95-99", "100+"]

DECADES = ["19세 이하", "20대", "30대", "40대", "50대", "60대", "70대", "80세 이상"]


def decade_of(age):
    if age < 20:
        return "19세 이하"
    if age >= 80:
        return "80세 이상"
    return f"{(age // 10) * 10}대"


# 연속형 항목: (연령별 테이블, 시도별 테이블, [(구간라벨, 하한, 상한)], 반올림 자릿수)
# 상·하한의 열린 꼬리는 임상적으로 타당한 범위로 고정.
CONT_SPECS = {
    "bmi": ("N057", "N056", [
        ("저체중(BMI 18.5 미만)", 14.0, 18.5),
        ("정상체중(BMI 18.5~25.0 미만)", 18.5, 25.0),
        ("비만1단계(BMI 25.0~30.0 미만)", 25.0, 30.0),
        ("비만2단계(BMI 30.0~40.0 미만)", 30.0, 40.0),
        ("비만3단계(BMI 40.0 이상)", 40.0, 55.0)], 1),
    "waist": ("N059", "N058", [
        ("75cm 미만", 55.0, 75.0), ("75-79cm", 75.0, 80.0), ("80-84cm", 80.0, 85.0),
        ("85-89cm", 85.0, 90.0), ("90-94cm", 90.0, 95.0), ("95-99cm", 95.0, 100.0),
        ("100-104cm", 100.0, 105.0), ("105-109cm", 105.0, 110.0),
        ("110cm 이상", 110.0, 135.0)], 1),
    "dbp": ("N061", "N060", [
        ("70mmHg 미만", 45, 70), ("70-79mmHg", 70, 80), ("80-89mmHg", 80, 90),
        ("90-99mmHg", 90, 100), ("100-109mmHg", 100, 110), ("110-119mmHg", 110, 120),
        ("120mmHg 이상", 120, 140)], 0),
    "sbp": ("N063", "N062", [
        ("110mmHg 미만", 85, 110), ("110-119mmHg", 110, 120), ("120-129mmHg", 120, 130),
        ("130-139mmHg", 130, 140), ("140-149mmHg", 140, 150), ("150-159mmHg", 150, 160),
        ("160-169mmHg", 160, 170), ("170-179mmHg", 170, 180),
        ("180mmHg 이상", 180, 220)], 0),
    "glu": ("N069", "N068", [
        ("100mg/dL 미만", 65, 100), ("100-109mg/dL", 100, 110), ("110-125mg/dL", 110, 126),
        ("126-139mg/dL", 126, 140), ("140-159mg/dL", 140, 160), ("160-179mg/dL", 160, 180),
        ("180-199mg/dL", 180, 200), ("200mg/dL 이상", 200, 350)], 0),
    "hgb": ("N067", "N066", [
        ("10.0g/dL 미만", 6.0, 10.0), ("10.0-10.9g/dL", 10.0, 11.0),
        ("11.0-11.9g/dL", 11.0, 12.0), ("12.0-12.9g/dL", 12.0, 13.0),
        ("13.0-13.9g/dL", 13.0, 14.0), ("14.0-14.9g/dL", 14.0, 15.0),
        ("15.0-15.5g/dL", 15.0, 15.6), ("15.6-15.9g/dL", 15.6, 16.0),
        ("16.0-16.5g/dL", 16.0, 16.6), ("16.6g/dL 이상", 16.6, 19.5)], 1),
    "tc": ("N071", "N070", [
        ("140mg/dL 미만", 90, 140), ("140-159mg/dL", 140, 160), ("160-179mg/dL", 160, 180),
        ("180-199mg/dL", 180, 200), ("200-219mg/dL", 200, 220), ("220-239mg/dL", 220, 240),
        ("240-259mg/dL", 240, 260), ("260-279mg/dL", 260, 280),
        ("280mg/dL 이상", 280, 400)], 0),
    "hdl": ("N073", "N072", [
        ("30mg/dL 미만", 15, 30), ("30-39mg/dL", 30, 40), ("40-49mg/dL", 40, 50),
        ("50-59mg/dL", 50, 60), ("60-69mg/dL", 60, 70), ("70-79mg/dL", 70, 80),
        ("80-89mg/dL", 80, 90), ("90mg/dL 이상", 90, 130)], 0),
    "tg": ("N075", "N074", [
        ("50mg/dL 미만", 20, 50), ("50-99mg/dL", 50, 100), ("100-149mg/dL", 100, 150),
        ("150-199mg/dL", 150, 200), ("200-299mg/dL", 200, 300), ("300-399mg/dL", 300, 400),
        ("400-499mg/dL", 400, 500), ("500mg/dL 이상", 500, 1200)], 0),
    "ldl": ("N077", "N076", [
        ("70mg/dL 미만", 30, 70), ("70-99mg/dL", 70, 100), ("100-129mg/dL", 100, 130),
        ("130-159mg/dL", 130, 160), ("160-189mg/dL", 160, 190),
        ("190mg/dL 이상", 190, 300)], 0),
    "cr": ("N079", "N078", [
        ("0.9mg/dL 미만", 0.4, 0.9), ("0.9-1.0mg/dL", 0.9, 1.1), ("1.1-1.2mg/dL", 1.1, 1.3),
        ("1.3-1.4mg/dL", 1.3, 1.5), ("1.5-1.6mg/dL", 1.5, 1.7), ("1.7-1.8mg/dL", 1.7, 1.9),
        ("1.9-2.0mg/dL", 1.9, 2.1), ("2.1mg/dL 이상", 2.1, 8.0)], 1),
    "egfr": ("N129", "N128", [
        ("30mL/min/1.73㎡ 미만", 5, 30), ("30-39mL/min/1.73㎡", 30, 40),
        ("40-49mL/min/1.73㎡", 40, 50), ("50-59mL/min/1.73㎡", 50, 60),
        ("60-79mL/min/1.73㎡", 60, 80), ("80-99mL/min/1.73㎡", 80, 100),
        ("100-149mL/min/1.73㎡", 100, 150), ("150mL/min/1.73㎡ 이상", 150, 200)], 0),
    "ast": ("N081", "N080", [
        ("30U/L 이하", 8, 31), ("31-40U/L", 31, 41), ("41-50U/L", 41, 51),
        ("51-60U/L", 51, 61), ("61-70U/L", 61, 71), ("71-80U/L", 71, 81),
        ("81-90U/L", 81, 91), ("91-100U/L", 91, 101), ("101U/L 이상", 101, 400)], 0),
    "alt": ("N083", "N082", [
        ("35U/L 이하", 4, 36), ("36-40U/L", 36, 41), ("41-45U/L", 41, 46),
        ("46-50U/L", 46, 51), ("51-60U/L", 51, 61), ("61-70U/L", 61, 71),
        ("71-80U/L", 71, 81), ("81-90U/L", 81, 91), ("91-100U/L", 91, 101),
        ("101U/L 이상", 101, 400)], 0),
    "ggt": ("N085", "N084", [
        ("8U/L 미만", 2, 8), ("8-10U/L", 8, 11), ("11-35U/L", 11, 36),
        ("36-45U/L", 36, 46), ("46-63U/L", 46, 64), ("64-77U/L", 64, 78),
        ("78-99U/L", 78, 100), ("100U/L 이상", 100, 600)], 0),
}

# 범주형 항목
URINE_CATS = ["음성", "+-", "+1", "+2", "+3", "+4"]
XRAY_CATS = ["정상A", "질환의심", "기타"]
VISION_CATS = ["기타", "0.1 이하", "0.2-0.4", "0.5-0.7", "0.8-1.0", "1.1-1.5", "1.6-2.0"]
VISION_VALUES = {
    "0.1 이하": [0.1], "0.2-0.4": [0.2, 0.3, 0.4], "0.5-0.7": [0.5, 0.6, 0.7],
    "0.8-1.0": [0.8, 0.9, 1.0], "1.1-1.5": [1.1, 1.2, 1.3, 1.4, 1.5],
    "1.6-2.0": [1.6, 1.8, 2.0], "기타": None,
}
BONE_CATS = ["T-score -1.0 이상", "T-score -1.0 미만 -2.5 초과", "T-score -2.5 이하"]
BONE_LABELS = {"T-score -1.0 이상": "정상", "T-score -1.0 미만 -2.5 초과": "골감소증",
               "T-score -2.5 이하": "골다공증"}
GRADE_CATS = ["정상A", "정상B", "질환의심", "유질환자"]

# 신장 표준편차 가정(cm) — KOSIS는 평균만 제공하므로 국민체위 통계 수준의 SD를 가정
HEIGHT_SD = {
    "남자": {"19세 이하": 6.0, "20대": 5.8, "30대": 5.7, "40대": 5.6, "50대": 5.5,
             "60대": 5.4, "70대": 5.3, "80세 이상": 5.2},
    "여자": {"19세 이하": 5.6, "20대": 5.4, "30대": 5.3, "40대": 5.2, "50대": 5.1,
             "60대": 5.0, "70대": 4.9, "80세 이상": 4.8},
}

COLUMNS = [
    "synthetic_id", "year", "sido", "sigungu", "sex", "age", "age_group", "age_decade",
    "height", "weight", "bmi", "waist", "sbp", "dbp", "fasting_glucose",
    "hemoglobin", "total_cholesterol", "hdl", "ldl", "triglyceride",
    "creatinine", "egfr", "ast", "alt", "ggt", "urine_protein", "chest_xray",
    "vision_left", "vision_right", "bone_density", "result_grade", "risk_group",
]

LATEST_YEAR = 2024
MAX_ROWS = 500000      # 생성 상한(서버 SYNTH_MAX_N과 동기)
SPEC_VERSION = 2       # 스펙 캐시 스키마 버전(시군구 데이터 포함)

# 항목 간 상관강도(corr) 프리셋 — UI 공용
CORR_PRESETS = [
    {"key": "independent", "label": "독립(상관 없음)", "value": 0.0},
    {"key": "weak", "label": "약한 상관", "value": 0.5},
    {"key": "standard", "label": "표준(권장)", "value": 1.0},
    {"key": "strong", "label": "강한 상관", "value": 1.3},
]

# 판정 등급 설명(국가건강검진 종합판정 기준) — UI·문서 공용
GRADE_DESC = {
    "정상A": "건강이 양호한 상태",
    "정상B": "건강에 이상은 없으나 식이·운동 등 자기관리와 예방조치가 필요한 상태(경계)",
    "질환의심": "질환으로 발전할 가능성이 있어 2차 검진 등 추가 정밀검사가 필요한 상태",
    "유질환자": "고혈압·당뇨병 등으로 현재 진단·치료 중인 상태(N099 판정현황 비율 반영)",
}


# ---------------------------------------------------------------- CSV 파싱

# build_spec가 설정하는 대상 연도(None=최신). _read_latest가 이 값으로 연도를 고른다.
_BUILD_YEAR = None
_AVAIL_YEARS = None  # available_years() 캐시


def _read_latest(tid):
    """KOSIS long-format CSV에서 대상연도(_BUILD_YEAR, None이면 최신) 행만 반환."""
    path = os.path.join(DATA_DIR, f"DT_35007_{tid}.csv")
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    years = sorted({r.get("PRD_DE", "") for r in rows if r.get("PRD_DE")})
    if not years:
        raise ValueError(f"{tid}: 연도 데이터 없음")
    target = str(_BUILD_YEAR) if (_BUILD_YEAR and str(_BUILD_YEAR) in years) else years[-1]
    return [r for r in rows if r.get("PRD_DE") == target], target


# build_spec가 실제 소비하는 핵심 테이블(연도 교집합 산정용)
_CORE_TABLES = ["N002_1", "N056", "N057", "N058", "N059", "N060", "N061", "N062",
                "N063", "N064", "N065", "N066", "N067", "N068", "N069", "N070",
                "N071", "N072", "N073", "N074", "N075", "N076", "N077", "N078",
                "N079", "N080", "N081", "N082", "N083", "N084", "N085", "N086",
                "N087", "N094", "N099", "N121", "N122", "N128", "N129", "N130"]


def available_years():
    """핵심 테이블이 모두 보유한 연도(교집합) — CSV 원본이 있을 때만 유효. 캐시."""
    global _AVAIL_YEARS
    if _AVAIL_YEARS is not None:
        return _AVAIL_YEARS
    common = None
    for tid in _CORE_TABLES:
        path = os.path.join(DATA_DIR, f"DT_35007_{tid}.csv")
        if not os.path.exists(path):
            _AVAIL_YEARS = []
            return _AVAIL_YEARS
        with open(path, encoding="utf-8-sig", newline="") as f:
            ys = {r.get("PRD_DE", "") for r in csv.DictReader(f) if r.get("PRD_DE")}
        common = ys if common is None else (common & ys)
    _AVAIL_YEARS = sorted(int(y) for y in common) if common else []
    return _AVAIL_YEARS


def _spec_path(year=None):
    """연도별 스펙 캐시 경로. year=None이면 기본(최신) 캐시."""
    if year is None:
        return SPEC_PATH
    return os.path.join(DATA_DIR, f"synthetic_baseline_{int(year)}.json")


def cached_years():
    """디스크에 빌드된 스펙 캐시가 있는 연도 목록(서버 배포 환경용 — CSV 없이도 동작)."""
    import glob
    years = set()
    for p in glob.glob(os.path.join(DATA_DIR, "synthetic_baseline_*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                y = json.load(f).get("year")
            if isinstance(y, int):
                years.add(y)
        except (OSError, ValueError):
            continue
    if os.path.exists(SPEC_PATH):  # 기본 캐시(최신)
        try:
            with open(SPEC_PATH, encoding="utf-8") as f:
                y = json.load(f).get("year")
            if isinstance(y, int):
                years.add(y)
        except (OSError, ValueError):
            pass
    return sorted(years)


def _val(r):
    s = (r.get("DT") or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _norm(vec):
    s = sum(vec)
    return [v / s for v in vec] if s > 0 else [0.0] * len(vec)


def _fill_age_gaps(key, acc, labels):
    """검사주기·대상연령 제한으로 비는 연령셀은 가장 가까운 밴드 분포로 폴백."""
    out = {}
    for sex in SEXES:
        have = {i for i, band in enumerate(AGE_BANDS)
                if sum(acc.get((band, sex), [0.0])) > 0}
        if not have:
            raise ValueError(f"{key}: {sex} 연령분포가 전부 비어있음")
        for i, band in enumerate(AGE_BANDS):
            src = i if i in have else min(have, key=lambda j: (abs(j - i), j))
            out.setdefault(band, {})[sex] = _norm(acc[(AGE_BANDS[src], sex)])
    return out


def _parse_dist(key, labels, age_tid, sido_tid):
    """연령별·시도별 분포 테이블 → {bins, age{band{sex}}, sido{sido{sex}}} (확률 정규화)."""
    lab_idx = {lab: i for i, lab in enumerate(labels)}

    rows, _ = _read_latest(age_tid)
    acc = {}
    for r in rows:
        band, sex, lab = r["C1_NM"].strip(), r["C2_NM"].strip(), r["C3_NM"].strip()
        if band not in BAND_AGE_RANGE or sex not in SEXES or lab not in lab_idx:
            continue
        acc.setdefault((band, sex), [0.0] * len(labels))[lab_idx[lab]] += _val(r)
    out_age = _fill_age_gaps(key, acc, labels)

    rows, _ = _read_latest(sido_tid)
    acc = {}
    for r in rows:
        sido, sex, lab = r["C1_NM"].strip(), r["C2_NM"].strip(), r["C3_NM"].strip()
        if sex not in SEXES or lab not in lab_idx:
            continue
        if sido not in SIDOS and sido != "계":
            continue
        acc.setdefault((sido, sex), [0.0] * len(labels))[lab_idx[lab]] += _val(r)
    out_sido = {}
    for sex in SEXES:
        nat = acc.get(("계", sex))
        if nat is None or sum(nat) <= 0:
            raise ValueError(f"{key}: 전국 분포 비어있음 ({sido_tid}/{sex})")
        for sido in SIDOS + ["계"]:
            vec = acc.get((sido, sex))
            if vec is None or sum(vec) <= 0:
                vec = nat  # 시도셀이 비면 전국 분포 폴백
            out_sido.setdefault(sido, {})[sex] = _norm(vec)
    return {"bins": list(labels), "age": out_age, "sido": out_sido}


def _parse_vision():
    """시력 N122/N121: C3 코드 001xx=좌안, 002xx=우안. 구간명은 좌우 동일하므로 코드로 구분."""
    def side_of(code):
        if len(code) <= 3:
            return None
        return "l" if code.startswith("001") else ("r" if code.startswith("002") else None)

    out = {}
    for side_key, tid, dim_col, dim_all in (
            ("age", "N122", "C1_NM", AGE_BANDS), ("sido", "N121", "C1_NM", SIDOS + ["계"])):
        rows, _ = _read_latest(tid)
        acc = {}
        for r in rows:
            side = side_of((r.get("C3") or "").strip())
            if side is None:
                continue
            grp, sex, lab = r[dim_col].strip(), r["C2_NM"].strip(), r["C3_NM"].strip()
            if sex not in SEXES or lab not in VISION_CATS:
                continue
            if side_key == "age" and grp not in BAND_AGE_RANGE:
                continue
            if side_key == "sido" and grp not in SIDOS and grp != "계":
                continue
            vec = acc.setdefault((side, grp, sex), [0.0] * len(VISION_CATS))
            vec[VISION_CATS.index(lab)] += _val(r)
        for side in ("l", "r"):
            key = f"vision_{side}"
            out.setdefault(key, {"bins": list(VISION_CATS), "age": {}, "sido": {}})
            for grp in dim_all:
                tgt = out[key][side_key].setdefault(grp, {})
                for sex in SEXES:
                    vec = acc.get((side, grp, sex))
                    if vec is None or sum(vec) <= 0:
                        raise ValueError(f"vision_{side}: 셀 비어있음 {grp}/{sex}")
                    tgt[sex] = _norm(vec)
    return out["vision_l"], out["vision_r"]


def _parse_grade():
    """N099 판정현황 → 밴드×성별 4범주 비율. C3코드: 002 정상A, 003 정상B, 00401 질환의심(실인원), 005 유질환자."""
    code_map = {"002": "정상A", "003": "정상B", "00401": "질환의심", "005": "유질환자"}
    rows, _ = _read_latest("N099")
    acc = {}
    for r in rows:
        cat = code_map.get((r.get("C3") or "").strip())
        band, sex = r["C1_NM"].strip(), r["C2_NM"].strip()
        if cat is None or band not in BAND_AGE_RANGE or sex not in SEXES:
            continue
        acc.setdefault((band, sex), {})[cat] = acc.setdefault((band, sex), {}).get(cat, 0.0) + _val(r)
    out = {}
    for band in AGE_BANDS:
        out[band] = {}
        for sex in SEXES:
            d = acc.get((band, sex), {})
            tot = sum(d.values())
            if tot <= 0:
                raise ValueError(f"grade: 셀 비어있음 {band}/{sex}")
            out[band][sex] = {c: d.get(c, 0.0) / tot for c in GRADE_CATS}
    return out


def _parse_bone():
    """N094 골밀도(시도×구간, 성별 차원 없음 — 검사대상이 54·66세 여성)."""
    rows, _ = _read_latest("N094")
    acc = {}
    for r in rows:
        sido, lab = r["C1_NM"].strip(), r["C2_NM"].strip()
        if lab not in BONE_CATS:
            continue
        if sido not in SIDOS and sido != "계":
            continue
        vec = acc.setdefault(sido, [0.0] * len(BONE_CATS))
        vec[BONE_CATS.index(lab)] += _val(r)
    out = {}
    for sido in SIDOS + ["계"]:
        vec = acc.get(sido)
        out[sido] = _norm(vec) if vec and sum(vec) > 0 else None
    if out["계"] is None:
        raise ValueError("bone: 전국 분포 없음")
    for sido in SIDOS:
        if out[sido] is None:
            out[sido] = list(out["계"])
    return out


def _parse_means(tid):
    """N130/N132 평균 신장·체중 → {sido{decade{sex: 값}}} ('계'/'전체' 폴백 포함)."""
    rows, _ = _read_latest(tid)
    out = {}
    for r in rows:
        sido, sex, dec = r["C1_NM"].strip(), r["C2_NM"].strip(), r["C3_NM"].strip()
        if sex not in SEXES:
            continue
        if sido not in SIDOS and sido != "계":
            continue
        if dec not in DECADES and dec != "전체":
            continue
        v = _val(r)
        if v > 0:
            out.setdefault(sido, {}).setdefault(dec, {})[sex] = v
    return out


def _parse_examinees():
    """N002_1 수검인원(ITM 002) → {band{sex: 명}}."""
    rows, _ = _read_latest("N002_1")
    out = {}
    for r in rows:
        if (r.get("ITM_ID") or "").strip() != "002":
            continue
        band, sex = r["C1_NM"].strip(), r["C2_NM"].strip()
        if band not in BAND_AGE_RANGE or sex not in SEXES:
            continue
        out.setdefault(band, {})[sex] = _val(r)
    for band in AGE_BANDS:
        for sex in SEXES:
            if out.get(band, {}).get(sex) is None:
                raise ValueError(f"examinees: 셀 없음 {band}/{sex}")
    return out


def _parse_sido_margin():
    """N056 체질량 시도별 '계'(C3=001) 행 → {sido{sex: 명}} (일반검진 수검자 규모)."""
    rows, _ = _read_latest("N056")
    out = {}
    for r in rows:
        if (r.get("C3") or "").strip() != "001":
            continue
        sido, sex = r["C1_NM"].strip(), r["C2_NM"].strip()
        if sido not in SIDOS or sex not in SEXES:
            continue
        out.setdefault(sido, {})[sex] = _val(r)
    for sido in SIDOS:
        for sex in SEXES:
            if out.get(sido, {}).get(sex) is None:
                raise ValueError(f"sido margin: 셀 없음 {sido}/{sex}")
    return out


def _load_pyramid():
    """pop_pyramid.json(시군구) → 시도 합산 {sido{sex: [21밴드 인구]}}."""
    with open(os.path.join(DATA_DIR, "pop_pyramid.json"), encoding="utf-8") as f:
        pyr = json.load(f)
    bands = pyr["bands"]
    if bands != PYR_BANDS:
        raise ValueError("pop_pyramid 밴드 구조가 예상과 다름")
    agg = {s: {"남자": [0.0] * len(bands), "여자": [0.0] * len(bands)} for s in SIDOS}
    for code, reg in pyr["regions"].items():
        sido = SIDO_BY_PREFIX.get(str(code)[:2])
        if sido is None:
            continue
        for i in range(len(bands)):
            agg[sido]["남자"][i] += reg["m"][i]
            agg[sido]["여자"][i] += reg["f"][i]
    return agg


def _load_sigungu():
    """pop_pyramid.json 시군구 원본(21밴드)을 스펙에 내장 — 시군구 단위 생성용.

    검사수치 분포는 KOSIS가 시도·연령까지만 제공하므로, 시군구 모드는
    '시군구 인구구조(연령·성) × 시도 수검률'로 인구학만 시군구화하고
    검사수치는 소속 시도 분포를 쓴다(README 한계 항목 참조)."""
    with open(os.path.join(DATA_DIR, "pop_pyramid.json"), encoding="utf-8") as f:
        pyr = json.load(f)
    if pyr["bands"] != PYR_BANDS:
        raise ValueError("pop_pyramid 밴드 구조가 예상과 다름")
    out = {}
    for code, reg in pyr["regions"].items():
        sido = SIDO_BY_PREFIX.get(str(code)[:2])
        if sido is None:
            continue
        name = reg["name"]
        short = name.split(" ", 1)[1] if " " in name else name
        out[str(code)] = {"name": name, "short": short, "sido": sido,
                          "m": list(reg["m"]), "f": list(reg["f"])}
    if not out:
        raise ValueError("pop_pyramid에서 시군구를 읽지 못함")
    return out


def _band_seed(pyr_sido_sex):
    """피라미드 21밴드 → KOSIS 15밴드 시드값."""
    idx = {b: i for i, b in enumerate(PYR_BANDS)}
    out = {}
    out["19세 이하"] = pyr_sido_sex[idx["15-19"]]
    for band in AGE_BANDS[1:-1]:
        lo, _hi = BAND_AGE_RANGE[band]
        out[band] = pyr_sido_sex[idx[f"{lo}-{lo + 4}"]]
    out["85세 이상"] = sum(pyr_sido_sex[idx[b]] for b in ("85-89", "90-94", "95-99", "100+"))
    return out


def _ipf_joint(pyramid, examinees, sido_margin, iters=60):
    """성별로 시도×연령밴드 IPF → {sex{sido{band: 수검자수}}}."""
    out = {}
    for sex in SEXES:
        m = {s: dict(_band_seed(pyramid[s][sex]).items()) for s in SIDOS}
        col_target = {b: examinees[b][sex] for b in AGE_BANDS}
        row_target = {s: sido_margin[s][sex] for s in SIDOS}
        # 마진 총합 불일치 보정(연령마진을 시도마진 총합에 스케일)
        rt, ct = sum(row_target.values()), sum(col_target.values())
        if ct > 0:
            col_target = {b: v * rt / ct for b, v in col_target.items()}
        for _ in range(iters):
            for s in SIDOS:  # 행 스케일
                cur = sum(m[s].values())
                if cur > 0:
                    f = row_target[s] / cur
                    for b in AGE_BANDS:
                        m[s][b] *= f
            for b in AGE_BANDS:  # 열 스케일
                cur = sum(m[s][b] for s in SIDOS)
                if cur > 0:
                    f = col_target[b] / cur
                    for s in SIDOS:
                        m[s][b] *= f
        out[sex] = m
    return out


def _age_within(pyramid):
    """{sido{sex{band: [[나이, 가중치]...]}}} — 밴드 내 1세 단위 가중(5세 밴드 균등 분할)."""
    idx = {b: i for i, b in enumerate(PYR_BANDS)}
    out = {}
    for sido in SIDOS:
        out[sido] = {}
        for sex in SEXES:
            vec = pyramid[sido][sex]
            per_band = {}
            for band in AGE_BANDS:
                lo, hi = BAND_AGE_RANGE[band]
                ages = []
                for a in range(lo, hi + 1):
                    p5 = "100+" if a >= 100 else f"{(a // 5) * 5}-{(a // 5) * 5 + 4}"
                    w = vec[idx[p5]] / 5.0
                    ages.append([a, max(w, 1e-9)])
                per_band[band] = ages
            out[sido][sex] = per_band
    return out


def build_spec(verbose=True, year=None):
    """KOSIS CSV 전체를 파싱해 (지정 연도) 생성기 스펙을 만들고 JSON 캐시로 저장.

    year=None이면 보유 최신연도. 해당 연도 데이터가 없으면 ValueError."""
    global _BUILD_YEAR
    t0 = time.time()
    yrs = available_years()
    if year is None:
        target = yrs[-1] if yrs else LATEST_YEAR
    else:
        target = int(year)
        if yrs and target not in yrs:
            raise ValueError(f"{target}년 데이터가 없습니다. 가능: {yrs}")
    _BUILD_YEAR = target
    try:
        spec = {"year": target, "age_bands": AGE_BANDS, "sidos": SIDOS, "sexes": SEXES,
                "dist": {}, "cat": {}}
        for key, (age_tid, sido_tid, bins, _dec) in CONT_SPECS.items():
            spec["dist"][key] = _parse_dist(key, [b[0] for b in bins], age_tid, sido_tid)
        spec["cat"]["urine"] = _parse_dist("urine", URINE_CATS, "N065", "N064")
        spec["cat"]["xray"] = _parse_dist("xray", XRAY_CATS, "N087", "N086")
        vl, vr = _parse_vision()
        spec["cat"]["vision_l"], spec["cat"]["vision_r"] = vl, vr
        spec["grade"] = _parse_grade()
        spec["bone"] = _parse_bone()
        spec["height_mean"] = _parse_means("N130")
        pyramid = _load_pyramid()
        examinees = _parse_examinees()
        sido_margin = _parse_sido_margin()
        spec["demo_joint"] = _ipf_joint(pyramid, examinees, sido_margin)
        spec["age_within"] = _age_within(pyramid)
        spec["sigungu"] = _load_sigungu()
        spec["spec_version"] = SPEC_VERSION
    finally:
        _BUILD_YEAR = None
    path = _spec_path(year)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    if verbose:
        print(f"[spec] 빌드 완료 {target}년 {time.time() - t0:.1f}s -> {path}")
    return spec


def load_spec(rebuild=False, year=None):
    """연도별 스펙 로드(캐시 우선). year=None이면 기본(최신) 캐시.

    배포 환경처럼 CSV 원본이 없으면 빌드가 불가하므로, 캐시가 있으면 그대로 쓴다."""
    path = _spec_path(year)
    if not rebuild and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
        ok_year = (year is None) or (spec.get("year") == int(year))
        if (spec.get("age_bands") == AGE_BANDS
                and spec.get("spec_version") == SPEC_VERSION and ok_year):
            return spec
    return build_spec(year=year)


def build_all_years(verbose=True):
    """가용한 모든 연도의 스펙 캐시를 빌드(배포용 — 로컬 CSV 보유 시)."""
    built = []
    yrs = available_years()
    if not yrs:
        raise ValueError("KOSIS CSV 원본이 없어 연도 빌드 불가")
    for y in yrs:
        build_spec(verbose=verbose, year=y)
        built.append(y)
    # 최신연도는 기본 캐시(synthetic_baseline.json)로도 복제
    build_spec(verbose=verbose, year=None)
    return built


# ---------------------------------------------------------------- 생성 코어

def _rake(p_age, p_sido, p_nat):
    """연령분포를 시도/전국 비율로 레이킹: p ∝ p_age × (p_sido / p_nat)."""
    out = []
    for a, s, n in zip(p_age, p_sido, p_nat):
        out.append(a * (s / n) if n > 1e-12 else a)
    tot = sum(out)
    return [v / tot for v in out] if tot > 0 else list(p_age)


class _Cells:
    """(시도,밴드,성별)별 레이킹 확률·컷포인트 캐시."""

    def __init__(self, spec):
        self.spec = spec
        self._probs = {}
        self._cuts = {}

    def probs(self, table, key, sido, band, sex):
        ck = (table, key, sido, band, sex)
        hit = self._probs.get(ck)
        if hit is None:
            d = self.spec[table][key]
            hit = _rake(d["age"][band][sex], d["sido"][sido][sex], d["sido"]["계"][sex])
            self._probs[ck] = hit
        return hit

    def cuts(self, table, key, sido, band, sex):
        """범주형 임계모형용 표준정규 컷포인트."""
        ck = (table, key, sido, band, sex)
        hit = self._cuts.get(ck)
        if hit is None:
            p = self.probs(table, key, sido, band, sex)
            cum, cuts = 0.0, []
            for v in p[:-1]:
                cum = min(cum + v, 1.0 - 1e-9)
                cuts.append(_INV(max(cum, 1e-9)))
            hit = cuts
            self._cuts[ck] = hit
        return hit


def _ckd_epi_2021(scr, age, male):
    """CKD-EPI 2021 크레아티닌 기반 추정 사구체여과율(인종 비포함, mL/min/1.73㎡).

    검진 결과지의 eGFR는 혈청 크레아티닌·연령·성별의 결정함수이므로, 같은 식으로
    파생해 개인 내 (Cr, eGFR) 일관성을 보장한다."""
    k = 0.9 if male else 0.7
    a = -0.302 if male else -0.241
    r = scr / k
    egfr = 142.0 * (min(r, 1.0) ** a) * (max(r, 1.0) ** -1.200) * (0.9938 ** age)
    if not male:
        egfr *= 1.012
    return egfr


def _inv_ckd_epi_2021(egfr_target, age, male):
    """CKD-EPI를 Cr에 대해 수치 역산(이분법). eGFR은 Cr에 단조감소라 안정적.
    anchor='egfr' 모드에서 목표 eGFR 분위 → Cr 도출에 사용."""
    lo, hi = 0.3, 12.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if _ckd_epi_2021(mid, age, male) > egfr_target:
            lo = mid  # Cr↑ → eGFR↓: 더 큰 Cr 필요
        else:
            hi = mid
    return min(max((lo + hi) / 2.0, 0.4), 8.0)


# 결측 주입 대상(검사 항목 row 키) — 인구학·판정·식별자·파생일관 키는 제외
_MISSING_FIELDS = [
    "waist", "sbp", "dbp", "fasting_glucose", "hemoglobin", "total_cholesterol",
    "hdl", "ldl", "triglyceride", "creatinine", "egfr", "ast", "alt", "ggt",
    "urine_protein", "chest_xray", "vision_left", "vision_right",
]


def _norm_missing(missing):
    """결측 비율 정규화 → {field: rate} (0~0.9). 단일 숫자면 전 항목 동일 적용."""
    if not missing:
        return {}
    if isinstance(missing, (int, float)):
        rate = min(max(float(missing), 0.0), 0.9)
        return {f: rate for f in _MISSING_FIELDS} if rate > 0 else {}
    out = {}
    for f, v in dict(missing).items():
        if f in _MISSING_FIELDS:
            rate = min(max(float(v), 0.0), 0.9)
            if rate > 0:
                out[f] = rate
    return out


def _icdf_binned(bins, probs, u, dec):
    """구간분포 역CDF: u∈(0,1) → 구간 내 선형보간 값(구간 밖으로 반올림 이탈 방지)."""
    cum = 0.0
    step = 10 ** (-dec) if dec > 0 else 1
    for (label, lo, hi), p in zip(bins, probs):
        if p <= 0:
            continue
        if u <= cum + p or (label, lo, hi) == bins[-1]:
            frac = (u - cum) / p
            frac = min(max(frac, 0.0), 1.0)
            v = lo + (hi - lo) * frac
            v = round(v, dec) if dec > 0 else int(round(v))
            # 반올림으로 구간 경계를 넘지 않게 고정
            upper = round(hi - step, dec) if dec > 0 else int(hi - step)
            lower = round(lo, dec) if dec > 0 else int(math.ceil(lo))
            return min(max(v, lower), upper)
        cum += p
    return round(bins[-1][1], dec) if dec else int(bins[-1][1])


# 잠재변수 의존 구조(가우시안 코퓰라의 상관 네트워크).
# "normalize"가 붙은 항목은 합성 가중치를 단위분산으로 정규화(상관강도 스케일 미적용).
LATENT_DEFS = [
    ("bmi", [], None),
    ("waist", [(0.80, "bmi")], None),
    ("sbp", [(0.35, "bmi")], None),
    ("dbp", [(0.55, "sbp"), (0.20, "bmi")], None),
    ("glu", [(0.40, "bmi")], None),
    ("tc", [(0.20, "bmi")], None),
    ("hdl", [(-0.30, "bmi"), (0.20, "tc")], None),
    ("tg", [(0.45, "bmi"), (-0.20, "hdl")], None),
    ("ldl", [(0.90, "tc"), (-0.15, "hdl"), (-0.10, "tg")], None),
    ("alt", [(0.45, "bmi")], None),
    ("ast", [(0.70, "alt")], None),
    ("ggt", [(0.35, "bmi"), (0.30, "alt")], None),
    ("cr", [(0.10, "bmi")], None),
    # eGFR는 잠재변수가 아니라 Cr·연령·성별의 CKD-EPI 결정함수로 파생한다(아래).
    ("hgb", [(-0.18, "cr")], None),
    ("up", [(0.30, "glu"), (0.30, "sbp")], None),
    ("xray", [], None),
    ("vl", [], None),
    ("vr", [(0.75, "vl")], None),
    ("risk", [(0.25, "sbp"), (0.25, "glu"), (0.25, "bmi"), (0.25, "cr")], "normalize"),
    ("dis", [(0.45, "risk")], None),
]


def _latent_model(corr_scale):
    """교차공분산을 정확히 전파해 각 잠재변수가 정확히 단위분산이 되도록
    (계수, 잔차표준편차)를 사전 계산. → 분위수 매핑의 주변분포 왜곡 방지."""
    cov = {}

    def get_cov(a, b):
        if a == b:
            return 1.0
        return cov.get((a, b) if a < b else (b, a), 0.0)

    model = []
    seen = []
    for name, terms, mode in LATENT_DEFS:
        scale = 1.0 if mode == "normalize" else corr_scale
        t = [(c * scale, s) for c, s in terms]
        var = sum(ci * cj * get_cov(si, sj) for ci, si in t for cj, sj in t)
        if mode == "normalize" and var > 1e-12:
            f = 1.0 / math.sqrt(var)
            t = [(c * f, s) for c, s in t]
            var = 1.0
        elif var > 0.92:  # 잔차 확보(상관강도 1.5 등 과대 설정 보호)
            f = math.sqrt(0.92 / var)
            t = [(c * f, s) for c, s in t]
            var = 0.92
        resid = math.sqrt(max(1.0 - var, 0.0))
        for other in seen:
            cv = sum(c * get_cov(s, other) for c, s in t)
            cov[(name, other) if name < other else (other, name)] = cv
        model.append((name, t, resid))
        seen.append(name)
    return model


def _draw_latents(rng, model):
    z = {}
    for name, terms, resid in model:
        v = sum(c * z[s] for c, s in terms)
        if resid > 0:
            v += resid * rng.gauss(0.0, 1.0)
        z[name] = v
    return z


def _pick_weighted(rng, items):
    """items=[[값, 가중치]...]"""
    tot = sum(w for _, w in items)
    x = rng.random() * tot
    for v, w in items:
        x -= w
        if x <= 0:
            return v
    return items[-1][0]


def list_sigungu(spec):
    """{시도: [{code, name}]} — UI 셀렉트용(코드순)."""
    out = {s: [] for s in SIDOS}
    for code, reg in sorted((spec.get("sigungu") or {}).items()):
        out[reg["sido"]].append({"code": code, "name": reg["short"]})
    return out


def _resolve_sigungu(spec, sigungu):
    """코드 또는 이름(축약/전체)으로 시군구 식별 → (code, reg)."""
    smap = spec.get("sigungu") or {}
    if not smap:
        raise ValueError("스펙에 시군구 데이터가 없습니다 — 스펙 재빌드 필요(--rebuild-spec)")
    code = str(sigungu).strip()
    if code in smap:
        return code, smap[code]
    matches = [c for c, r in smap.items() if code in (r["short"], r["name"])]
    if len(matches) != 1:
        raise ValueError(f"알 수 없는 시군구: {sigungu}")
    return matches[0], smap[matches[0]]


def _sigungu_demo(spec, code):
    """시군구 (밴드,성별) 수검 가중치 = 시군구 인구 × (시도 수검자 ÷ 시도 인구).

    시군구의 연령·성 구조를 유지하면서 소속 시도의 연령·성별 검진 수검률을
    적용한다. KOSIS 수검 마진이 시도까지만 있어 이것이 최선의 근사다."""
    reg = spec["sigungu"][code]
    sido = reg["sido"]
    sido_seed = {sex: [0.0] * len(PYR_BANDS) for sex in SEXES}
    for r2 in spec["sigungu"].values():
        if r2["sido"] != sido:
            continue
        for i in range(len(PYR_BANDS)):
            sido_seed["남자"][i] += r2["m"][i]
            sido_seed["여자"][i] += r2["f"][i]
    joint = spec["demo_joint"]
    weights = {}
    for sex in SEXES:
        reg15 = _band_seed(reg["m"] if sex == "남자" else reg["f"])
        sido15 = _band_seed(sido_seed[sex])
        for band in AGE_BANDS:
            pop = sido15[band]
            rate = (joint[sex][sido][band] / pop) if pop > 0 else 0.0
            weights[(band, sex)] = reg15[band] * rate
    if sum(weights.values()) <= 0:
        raise ValueError(f"시군구 수검 가중치가 0: {reg['name']}")
    return reg, weights


def _sigungu_age_within(reg):
    """시군구 1세 단위 나이 가중(밴드 내 5세 균등 분할) — _age_within의 단일지역판."""
    idx = {b: i for i, b in enumerate(PYR_BANDS)}
    out = {}
    for sex in SEXES:
        vec = reg["m"] if sex == "남자" else reg["f"]
        per_band = {}
        for band in AGE_BANDS:
            lo, hi = BAND_AGE_RANGE[band]
            ages = []
            for a in range(lo, hi + 1):
                p5 = "100+" if a >= 100 else f"{(a // 5) * 5}-{(a // 5) * 5 + 4}"
                ages.append([a, max(vec[idx[p5]] / 5.0, 1e-9)])
            per_band[band] = ages
        out[sex] = per_band
    return out


def _height_lookup(spec, sido, decade, sex):
    hm = spec["height_mean"]
    for s in (sido, "계"):
        node = hm.get(s) or {}
        for d in (decade, "전체"):
            v = (node.get(d) or {}).get(sex)
            if v:
                return v
    return 170.0 if sex == "남자" else 157.0


def generate(spec, n, sido="전체", seed=None, corr=1.0, sigungu=None,
             age_min=None, age_max=None, sex=None, missing=None, anchor="cr"):
    """합성 검진 레코드 n건 생성. 반환: (rows, meta)

    sigungu(코드 또는 이름)를 주면 인구학(연령·성·나이)은 해당 시군구 인구구조에
    시도 수검률을 적용해 추출하고, 검사수치는 소속 시도 분포를 쓴다.

    age_min/age_max/sex: 코호트 필터(밴드 단위 절단). sex='남자'|'여자'.
    missing: 결측 주입 비율(0~0.5 단일값 또는 {항목:비율} dict). MCAR 기본.
    anchor: 'cr'(기본, Cr→eGFR 파생) | 'egfr'(eGFR을 N128 분포에 맞추고 Cr 역산)."""
    if not (100 <= n <= MAX_ROWS):
        raise ValueError(f"표본 수는 100~{MAX_ROWS:,} 범위여야 합니다")
    if sido != "전체" and sido not in SIDOS:
        raise ValueError(f"알 수 없는 시도: {sido}")
    if sex is not None and sex not in SEXES:
        raise ValueError(f"알 수 없는 성별: {sex}")
    if anchor not in ("cr", "egfr"):
        raise ValueError("anchor는 cr 또는 egfr여야 합니다")
    sel_sexes = SEXES if sex is None else [sex]
    sex_filter = sex  # 원본 필터 보존(아래 루프에서 sex가 행별로 덮어써짐)
    lo_cut = int(age_min) if age_min not in (None, "") else None
    hi_cut = int(age_max) if age_max not in (None, "") else None
    miss = _norm_missing(missing)
    corr = float(corr)
    if math.isnan(corr) or math.isinf(corr):
        raise ValueError("상관강도(corr)는 유한한 수여야 합니다")
    corr = min(max(corr, 0.0), 1.5)
    if seed is None:
        seed = random.SystemRandom().randrange(1, 10 ** 9)
    seed = int(seed)
    rng = random.Random(seed)
    t0 = time.time()
    year_val = spec.get("year", LATEST_YEAR)

    sig_code, sig_reg, sig_ages = None, None, None
    if sigungu:
        sig_code, sig_reg = _resolve_sigungu(spec, sigungu)
        if sido not in ("전체", sig_reg["sido"]):
            raise ValueError(f"{sig_reg['name']}은(는) {sido} 소속이 아닙니다")
        sido = sig_reg["sido"]
        sig_ages = _sigungu_age_within(sig_reg)

    def _band_ok(band):
        blo, bhi = BAND_AGE_RANGE[band]
        if lo_cut is not None and bhi < lo_cut:
            return False
        if hi_cut is not None and blo > hi_cut:
            return False
        return True

    cells = _Cells(spec)
    joint = spec["demo_joint"]
    flat, wsum = [], 0.0
    if sig_reg is not None:
        _reg, wmap = _sigungu_demo(spec, sig_code)
        for sx in sel_sexes:
            for band in AGE_BANDS:
                if not _band_ok(band):
                    continue
                w = wmap[(band, sx)]
                if w > 0:
                    flat.append((sido, band, sx, w))
                    wsum += w
    else:
        sel_sidos = SIDOS if sido == "전체" else [sido]
        for sx in sel_sexes:
            for s in sel_sidos:
                for band in AGE_BANDS:
                    if not _band_ok(band):
                        continue
                    w = joint[sx][s][band]
                    if w > 0:
                        flat.append((s, band, sx, w))
                        wsum += w
    if wsum <= 0:
        raise ValueError("선택 코호트(지역·연령·성별)의 수검 인구 가중치가 0입니다")
    cum, acc = [], 0.0
    for _s, _b, _x, w in flat:
        acc += w / wsum
        cum.append(acc)

    bins_of = {k: CONT_SPECS[k][2] for k in CONT_SPECS}
    dec_of = {k: CONT_SPECS[k][3] for k in CONT_SPECS}

    def cont(key, s, band, sex, z):
        return _icdf_binned(bins_of[key], cells.probs("dist", key, s, band, sex),
                            _PHI(z), dec_of[key])

    def ordinal(key, cats, s, band, sex, z):
        cuts = cells.cuts("cat", key, s, band, sex)
        return cats[bisect_right(cuts, z)]

    model = _latent_model(corr)
    rows = []
    for i in range(n):
        idx = bisect_right(cum, rng.random())
        s, band, sex, _w = flat[min(idx, len(flat) - 1)]
        age_src = sig_ages[sex][band] if sig_ages else spec["age_within"][s][sex][band]
        if lo_cut is not None or hi_cut is not None:
            age_src = [aw for aw in age_src
                       if (lo_cut is None or aw[0] >= lo_cut)
                       and (hi_cut is None or aw[0] <= hi_cut)] or age_src
        age = _pick_weighted(rng, age_src)
        decade = decade_of(age)
        z = _draw_latents(rng, model)

        # ① 체형
        bmi_t = cont("bmi", s, band, sex, z["bmi"])
        h_mean = _height_lookup(spec, s, decade, sex)
        h_sd = HEIGHT_SD[sex][decade]
        height = round(min(max(rng.gauss(h_mean, h_sd), 135.0), 200.0), 1)
        weight = round(bmi_t * (height / 100.0) ** 2, 1)
        bmi = round(weight / (height / 100.0) ** 2, 1)
        waist = cont("waist", s, band, sex, z["waist"])

        # ② 혈압·혈당 (BMI 조건부, 연령·성별·시도는 셀 분포로 반영)
        sbp = cont("sbp", s, band, sex, z["sbp"])
        dbp = cont("dbp", s, band, sex, z["dbp"])
        glu = cont("glu", s, band, sex, z["glu"])

        # ③ 지질 (LDL은 Friedewald 관계를 잠재상관으로 반영 후 분위수 매핑)
        tc = cont("tc", s, band, sex, z["tc"])
        hdl = cont("hdl", s, band, sex, z["hdl"])
        hdl = min(hdl, tc - 25)  # HDL이 TC에 근접·초과하는 생리적 모순 차단
        tg = cont("tg", s, band, sex, z["tg"])
        # LDL은 한국 검진 표준인 Friedewald 식(LDL = TC-HDL-TG/5)으로 직접 파생해
        # (TC,HDL,TG,LDL) 4중 일관성을 완전히 보장한다. TG≥400은 식이 부정확해
        # KOSIS 분포에서 분위수 매핑하되 약한 상한만 적용(드문 케이스).
        if tg < 400:
            ldl = max(10, int(round(tc - hdl - tg / 5.0)))
        else:
            ldl = cont("ldl", s, band, sex, z["ldl"])
            ldl = max(10, min(ldl, tc - hdl - 80))

        # ④ 간기능 (성별 차이는 셀 분포가 내장)
        ast = cont("ast", s, band, sex, z["ast"])
        alt = cont("alt", s, band, sex, z["alt"])
        ggt = cont("ggt", s, band, sex, z["ggt"])

        # ⑤ 신장기능·혈색소
        # anchor='cr'(기본): Cr를 N078/79 분포로 매핑 → eGFR을 CKD-EPI로 파생(Cr 주변분포 정합).
        # anchor='egfr': eGFR을 N128/129 분포로 매핑 → CKD-EPI 역산으로 Cr 도출(eGFR 주변분포 정합).
        male = sex == "남자"
        if anchor == "egfr":
            egfr_t = cont("egfr", s, band, sex, z["cr"])
            cr = round(_inv_ckd_epi_2021(egfr_t, age, male), 1)
            egfr = int(round(min(max(_ckd_epi_2021(cr, age, male), 5.0), 200.0)))
        else:
            cr = cont("cr", s, band, sex, z["cr"])
            egfr = int(round(min(max(_ckd_epi_2021(cr, age, male), 5.0), 200.0)))
        hgb = cont("hgb", s, band, sex, z["hgb"])

        # ⑥ 기타 검사
        urine = ordinal("urine", URINE_CATS, s, band, sex, z["up"])
        xray = ordinal("xray", XRAY_CATS, s, band, sex, z["xray"])
        vl_cat = ordinal("vision_l", VISION_CATS, s, band, sex, z["vl"])
        vr_cat = ordinal("vision_r", VISION_CATS, s, band, sex, z["vr"])
        vision_l = rng.choice(VISION_VALUES[vl_cat]) if VISION_VALUES[vl_cat] else None
        vision_r = rng.choice(VISION_VALUES[vr_cat]) if VISION_VALUES[vr_cat] else None
        bone = ""
        if sex == "여자" and age in (54, 66):
            p = spec["bone"].get(s) or spec["bone"]["계"]
            r_ = rng.random()
            bone = BONE_LABELS[BONE_CATS[0]] if r_ < p[0] else (
                BONE_LABELS[BONE_CATS[1]] if r_ < p[0] + p[1] else BONE_LABELS[BONE_CATS[2]])

        # ⑦ 판정 — 유질환자는 N099 비율 임계모형, 나머지는 검진 기준 규칙
        p_dis = spec["grade"][band][sex]["유질환자"]
        flags, grade = _judge(sex, bmi, waist, sbp, dbp, glu, hgb, tc, hdl, ldl, tg,
                              cr, egfr, ast, alt, ggt, urine, xray)
        if _PHI(z["dis"]) < p_dis:
            grade = "유질환자"

        row = {
            "synthetic_id": f"S{i + 1:06d}", "year": year_val, "sido": s,
            "sigungu": sig_reg["short"] if sig_reg else "", "sex": sex,
            "age": age, "age_group": band, "age_decade": decade,
            "height": height, "weight": weight, "bmi": bmi, "waist": waist,
            "sbp": sbp, "dbp": dbp, "fasting_glucose": glu, "hemoglobin": hgb,
            "total_cholesterol": tc, "hdl": hdl, "ldl": ldl, "triglyceride": tg,
            "creatinine": cr, "egfr": egfr, "ast": ast, "alt": alt, "ggt": ggt,
            "urine_protein": urine, "chest_xray": xray,
            "vision_left": vision_l, "vision_right": vision_r, "bone_density": bone,
            "result_grade": grade, "risk_group": flags,
        }
        # 결측 주입(MCAR) — 판정 후 측정 셀만 비움(값은 있었으나 미기록된 상황 모사).
        # 판정·집계는 결측을 건너뛰도록 가드되어 있다(_b_status·matrix_c·verify).
        if miss:
            for f, rate in miss.items():
                if rng.random() < rate:
                    row[f] = None
        rows.append(row)

    meta = {"n": n, "sido": sido, "seed": seed, "corr": corr, "year": year_val,
            "sigungu": sig_reg["short"] if sig_reg else None,
            "sigungu_code": sig_code,
            "age_min": lo_cut, "age_max": hi_cut, "sex_filter": sex_filter,
            "anchor": anchor, "missing": miss or None,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "columns": list(COLUMNS)}
    return rows, meta


def _judge(sex, bmi, waist, sbp, dbp, glu, hgb, tc, hdl, ldl, tg,
           cr, egfr, ast, alt, ggt, urine, xray):
    """위험군 플래그와 규칙기반 판정등급(정상A/정상B/질환의심)."""
    male = sex == "남자"
    flags = []
    suspect = False  # 질환의심
    border = False   # 정상B

    if bmi >= 25.0:
        flags.append("비만")
        border = True
    elif bmi < 18.5:
        flags.append("저체중")
        border = True
    if (male and waist >= 90) or (not male and waist >= 85):
        flags.append("복부비만")
        border = True

    if sbp >= 140 or dbp >= 90:
        flags.append("고혈압의심")
        suspect = True
    elif sbp >= 120 or dbp >= 80:
        border = True

    if glu >= 126:
        flags.append("당뇨의심")
        suspect = True
    elif glu >= 100:
        flags.append("공복혈당장애")
        border = True

    dyslip = tc >= 240 or ldl >= 160 or hdl < 40 or tg >= 200
    if dyslip:
        flags.append("이상지질혈증")
    if tc >= 240 or ldl >= 160 or tg >= 500:
        suspect = True
    elif tc >= 200 or ldl >= 130 or hdl < 40 or tg >= 150:
        border = True

    ggt_uln = 77 if male else 45
    if ast > 40 or alt > 40 or ggt > ggt_uln:
        flags.append("간수치이상")
    if ast >= 101 or alt >= 101 or ggt >= ggt_uln * 3:
        suspect = True
    elif ast > 40 or alt > 40 or ggt > ggt_uln:
        border = True

    if egfr < 60 or cr >= 1.5:
        flags.append("신기능저하")
    if egfr < 60 or cr >= 1.7:
        suspect = True
    elif cr >= 1.3:
        border = True

    anemia_cut = 13.0 if male else 12.0
    if hgb < anemia_cut:
        flags.append("빈혈")
        if hgb < 10.0:
            suspect = True
        else:
            border = True

    if urine in ("+2", "+3", "+4"):
        flags.append("단백뇨")
        suspect = True
    elif urine in ("+-", "+1"):
        if urine == "+1":
            flags.append("단백뇨")
        border = True

    if xray == "질환의심":
        suspect = True

    grade = "질환의심" if suspect else ("정상B" if border else "정상A")
    return (";".join(flags) if flags else "없음"), grade


# ---------------------------------------------------------------- 집계(B안·C안·검증)

B_ITEMS = [
    ("bmi", "비만도(BMI)", "정상 18.5~24.9 · 주의 25~29.9 또는 저체중 · 질환의심 30 이상"),
    ("waist", "허리둘레", "정상 <90/85cm(남/여) · 주의 90~99/85~94 · 질환의심 100/95 이상"),
    ("bp", "혈압", "정상 <120/80 · 주의 120~139/80~89 · 질환의심 140/90 이상"),
    ("glu", "공복혈당", "정상 <100 · 주의 100~125 · 질환의심 126 이상"),
    ("tc", "총콜레스테롤", "정상 <200 · 주의 200~239 · 질환의심 240 이상"),
    ("ldl", "LDL콜레스테롤", "정상 <130 · 주의 130~159 · 질환의심 160 이상"),
    ("hdl", "HDL콜레스테롤", "정상 60 이상 · 주의 40~59 · 질환의심(낮음) 40 미만"),
    ("tg", "중성지방", "정상 <150 · 주의 150~199 · 질환의심 200 이상"),
    ("liver", "간기능(AST·ALT·γ-GTP)", "정상 기준치 이내 · 주의 경도상승 · 질환의심 100 초과 등"),
    ("kidney", "신장기능(Cr·eGFR)", "정상 eGFR≥90 · 주의 60~89 또는 Cr≥1.3 · 질환의심 eGFR<60"),
    ("anemia", "빈혈(혈색소)", "정상 ≥13/12 · 주의 10~기준 미만 · 질환의심 <10"),
    ("urine", "요단백", "정상 음성 · 주의 ±/+1 · 질환의심 +2 이상"),
]


# B안 지표별 필요한 row 필드(하나라도 결측이면 그 지표는 집계 제외)
_B_FIELDS_REQ = {
    "bmi": ("bmi",), "waist": ("waist",), "bp": ("sbp", "dbp"),
    "glu": ("fasting_glucose",), "tc": ("total_cholesterol",), "ldl": ("ldl",),
    "hdl": ("hdl",), "tg": ("triglyceride",), "liver": ("ast", "alt", "ggt"),
    "kidney": ("egfr", "creatinine"), "anemia": ("hemoglobin",), "urine": ("urine_protein",),
}


def _b_status(key, r):
    # 결측(None)이 하나라도 있으면 None 반환 → 집계에서 제외
    for f in _B_FIELDS_REQ.get(key, ()):
        if r.get(f) is None:
            return None
    male = r["sex"] == "남자"
    if key == "bmi":
        b = r["bmi"]
        return 2 if b >= 30 else (1 if (b >= 25 or b < 18.5) else 0)
    if key == "waist":
        w, c1, c2 = r["waist"], (90 if male else 85), (100 if male else 95)
        return 2 if w >= c2 else (1 if w >= c1 else 0)
    if key == "bp":
        s, d = r["sbp"], r["dbp"]
        return 2 if (s >= 140 or d >= 90) else (1 if (s >= 120 or d >= 80) else 0)
    if key == "glu":
        g = r["fasting_glucose"]
        return 2 if g >= 126 else (1 if g >= 100 else 0)
    if key == "tc":
        v = r["total_cholesterol"]
        return 2 if v >= 240 else (1 if v >= 200 else 0)
    if key == "ldl":
        v = r["ldl"]
        return 2 if v >= 160 else (1 if v >= 130 else 0)
    if key == "hdl":
        v = r["hdl"]
        return 2 if v < 40 else (1 if v < 60 else 0)
    if key == "tg":
        v = r["triglyceride"]
        return 2 if v >= 200 else (1 if v >= 150 else 0)
    if key == "liver":
        uln = 77 if male else 45
        if r["ast"] >= 101 or r["alt"] >= 101 or r["ggt"] >= uln * 3:
            return 2
        if r["ast"] > 40 or r["alt"] > 40 or r["ggt"] > uln:
            return 1
        return 0
    if key == "kidney":
        if r["egfr"] < 60 or r["creatinine"] >= 1.7:
            return 2
        if r["egfr"] < 90 or r["creatinine"] >= 1.3:
            return 1
        return 0
    if key == "anemia":
        cut = 13.0 if male else 12.0
        h = r["hemoglobin"]
        return 2 if h < 10 else (1 if h < cut else 0)
    if key == "urine":
        u = r["urine_protein"]
        return 2 if u in ("+2", "+3", "+4") else (1 if u in ("+-", "+1") else 0)
    raise KeyError(key)


def summary_b(rows):
    out = []
    for key, label, crit in B_ITEMS:
        cnt = [0, 0, 0]
        for r in rows:
            st = _b_status(key, r)
            if st is not None:
                cnt[st] += 1
        valid = sum(cnt)
        d = valid or 1  # 결측 제외 유효표본 기준 비율
        out.append({
            "key": key, "indicator": label, "criteria": crit,
            "normal_pct": round(cnt[0] * 100.0 / d, 1),
            "caution_pct": round(cnt[1] * 100.0 / d, 1),
            "disease_pct": round(cnt[2] * 100.0 / d, 1),
            "valid_n": valid,
        })
    return out


def matrix_c(rows):
    cells = {}
    for r in rows:
        k = (r["sido"], r["age_decade"])
        c = cells.setdefault(k, {"n": 0, "ob": 0, "ht": 0, "dm": 0, "dl": 0,
                                 "n_ob": 0, "n_ht": 0, "n_dm": 0, "n_dl": 0})
        c["n"] += 1
        # 결측 항목은 분모(n_*)에서도 제외해 비율 왜곡 방지
        if r["bmi"] is not None:
            c["n_ob"] += 1
            if r["bmi"] >= 25:
                c["ob"] += 1
        if r["sbp"] is not None and r["dbp"] is not None:
            c["n_ht"] += 1
            if r["sbp"] >= 140 or r["dbp"] >= 90:
                c["ht"] += 1
        if r["fasting_glucose"] is not None:
            c["n_dm"] += 1
            if r["fasting_glucose"] >= 126:
                c["dm"] += 1
        if None not in (r["total_cholesterol"], r["ldl"], r["hdl"], r["triglyceride"]):
            c["n_dl"] += 1
            if (r["total_cholesterol"] >= 240 or r["ldl"] >= 160 or r["hdl"] < 40
                    or r["triglyceride"] >= 200):
                c["dl"] += 1
    out = []
    sido_order = {s: i for i, s in enumerate(SIDOS)}
    dec_order = {d: i for i, d in enumerate(DECADES)}
    for (s, d), c in sorted(cells.items(),
                            key=lambda kv: (sido_order.get(kv[0][0], 99),
                                            dec_order.get(kv[0][1], 99))):
        n = c["n"]
        ob = c["ob"] * 100.0 / (c["n_ob"] or 1)
        ht = c["ht"] * 100.0 / (c["n_ht"] or 1)
        dm = c["dm"] * 100.0 / (c["n_dm"] or 1)
        dl = c["dl"] * 100.0 / (c["n_dl"] or 1)
        score = ob * 0.2 + ht * 0.3 + dm * 0.3 + dl * 0.2
        level = "낮음" if score < 12 else ("보통" if score < 22 else "높음")
        out.append({"sido": s, "age_decade": d, "n": n,
                    "obesity_pct": round(ob, 1), "htn_pct": round(ht, 1),
                    "dm_pct": round(dm, 1), "dyslip_pct": round(dl, 1),
                    "risk_score": round(score, 1), "risk_level": level})
    return out


VERIFY_LABELS = {
    "bmi": "체질량(BMI)", "waist": "허리둘레", "sbp": "수축기혈압", "dbp": "이완기혈압",
    "glu": "공복혈당", "hgb": "혈색소", "tc": "총콜레스테롤", "hdl": "HDL콜레스테롤",
    "tg": "중성지방", "ldl": "LDL콜레스테롤", "cr": "혈청크레아티닌", "egfr": "신사구체여과율",
    "ast": "AST", "alt": "ALT", "ggt": "γ-GTP", "urine": "요단백", "xray": "흉부방사선",
    "vision_l": "시력(좌안)", "vision_r": "시력(우안)",
}

ROW_FIELD = {
    "bmi": "bmi", "waist": "waist", "sbp": "sbp", "dbp": "dbp", "glu": "fasting_glucose",
    "hgb": "hemoglobin", "tc": "total_cholesterol", "hdl": "hdl", "tg": "triglyceride",
    "ldl": "ldl", "cr": "creatinine", "egfr": "egfr", "ast": "ast", "alt": "alt",
    "ggt": "ggt",
}


def _short_bin(label):
    """검증 차트용 구간 라벨 축약."""
    return (label.replace("mmHg", "").replace("mg/dL", "").replace("g/dL", "")
            .replace("mL/min/1.73㎡", "").replace("U/L", "").replace("cm", "")
            .replace("(BMI ", "(").strip())


# 라벨이 너무 길어 검증 차트에서 겹치는 항목은 짧은 구간명으로 덮어쓴다.
VERIFY_BIN_OVERRIDE = {
    "bmi": ["저체중", "정상", "비만1", "비만2", "비만3"],
}


def verify_report(spec, rows):
    """합성 분포 vs KOSIS 분포 비교(두 기준).

    - kosis_pct(내부 정합성): 합성 코호트 구성으로 가중한 레이킹 목표분포.
      코호트 구성을 통제하므로 모델의 분위수 매핑 충실도를 본다(샘플링 잡음에 가까움).
    - raw_kosis_pct(KOSIS 원시 전국): 발행된 전국('계') 분포를 합성 코호트 성별
      구성으로만 가중. 코호트 연령·시도 구성 차이까지 포함한 대외 충실도를 본다.
    """
    cells = _Cells(spec)
    comp = {}
    for r in rows:
        comp[(r["sido"], r["age_group"], r["sex"])] = \
            comp.get((r["sido"], r["age_group"], r["sex"]), 0) + 1
    n = len(rows)
    sex_w = {sex: 0.0 for sex in SEXES}
    for (_s, _b, sex), cnt in comp.items():
        sex_w[sex] += cnt / n

    def internal(table, key, nbins):
        mix = [0.0] * nbins
        for (s, band, sex), cnt in comp.items():
            p = cells.probs(table, key, s, band, sex)
            w = cnt / n
            for i in range(nbins):
                mix[i] += w * p[i]
        return mix

    def raw_national(table, key, nbins):
        d = spec[table][key]
        mix = [0.0] * nbins
        for sex in SEXES:
            p = d["sido"]["계"][sex]
            for i in range(nbins):
                mix[i] += sex_w[sex] * p[i]
        return mix

    def make_row(key, label, bin_labels, table, nbins, counts, derived=False):
        tot = sum(counts) or 1  # 결측 제외 유효표본 기준(분모)
        synth = [c / tot for c in counts]
        kosis = internal(table, key, nbins)
        raw = raw_national(table, key, nbins)
        idiff = [abs(a - b) * 100 for a, b in zip(kosis, synth)]
        rdiff = [abs(a - b) * 100 for a, b in zip(raw, synth)]
        # 기대 샘플링 오차(단순임의표집 가정 95% 반폭) — 관측 오차가 잡음 한계 이내인지 판단용
        se = [1.96 * math.sqrt(max(p * (1 - p), 0.0) / tot) * 100 for p in synth]
        return {"key": key, "label": label, "bins": bin_labels, "derived": derived,
                "kosis_pct": [round(v * 100, 2) for v in kosis],
                "raw_kosis_pct": [round(v * 100, 2) for v in raw],
                "synth_pct": [round(v * 100, 2) for v in synth],
                "max_diff_pct": round(max(idiff), 2),
                "raw_max_diff_pct": round(max(rdiff), 2),
                "sampling_err_pct": round(max(se), 2), "valid_n": int(tot)}

    out = []
    for key in ("bmi", "waist", "sbp", "dbp", "glu", "hgb", "tc", "hdl", "tg",
                "ldl", "cr", "egfr", "ast", "alt", "ggt"):
        bins = CONT_SPECS[key][2]
        labels = VERIFY_BIN_OVERRIDE.get(key) or [_short_bin(b[0]) for b in bins]
        cnt = [0] * len(bins)
        field = ROW_FIELD[key]
        for r in rows:
            v = r[field]
            if v is None:
                continue
            for i, (_lab, lo, hi) in enumerate(bins):
                if v < hi or i == len(bins) - 1:
                    cnt[i] += 1
                    break
        suffix = {"egfr": "(파생·CKD-EPI)", "ldl": "(파생·Friedewald)"}.get(key, "")
        out.append(make_row(key, VERIFY_LABELS[key] + suffix, labels, "dist",
                            len(bins), cnt, derived=(key in ("egfr", "ldl"))))

    cat_field = {"urine": ("urine_protein", URINE_CATS), "xray": ("chest_xray", XRAY_CATS)}
    for key, (field, cats) in cat_field.items():
        cnt = [0] * len(cats)
        for r in rows:
            if r[field] is None:
                continue
            cnt[cats.index(r[field])] += 1
        out.append(make_row(key, VERIFY_LABELS[key], list(cats), "cat", len(cats), cnt))

    for key, field in (("vision_l", "vision_left"), ("vision_r", "vision_right")):
        cnt = [0] * len(VISION_CATS)
        for r in rows:
            v = r[field]
            if v is None:
                cnt[0] += 1
            else:
                for i, c in enumerate(VISION_CATS[1:], start=1):
                    vals = VISION_VALUES[c]
                    if vals and vals[0] <= v <= vals[-1]:
                        cnt[i] += 1
                        break
        out.append(make_row(key, VERIFY_LABELS[key], list(VISION_CATS), "cat",
                            len(VISION_CATS), cnt))
    return out


def grade_compare(spec, rows):
    comp = {}
    for r in rows:
        k = (r["age_group"], r["sex"])
        comp[k] = comp.get(k, 0) + 1
    n = len(rows)
    kosis = [0.0] * len(GRADE_CATS)
    for (band, sex), cnt in comp.items():
        g = spec["grade"][band][sex]
        for i, c in enumerate(GRADE_CATS):
            kosis[i] += g[c] * cnt / n
    cnt = [0] * len(GRADE_CATS)
    for r in rows:
        cnt[GRADE_CATS.index(r["result_grade"])] += 1
    synth = [c / n for c in cnt]
    return {"bins": list(GRADE_CATS),
            "kosis_pct": [round(v * 100, 1) for v in kosis],
            "synth_pct": [round(v * 100, 1) for v in synth]}


def mean_abs_diff(verify, field="kosis_pct"):
    """검증 항목별 평균 절대오차의 평균.
    field='kosis_pct'면 내부 정합성, 'raw_kosis_pct'면 KOSIS 원시 전국 대비."""
    vals = []
    for v in verify:
        ref = v.get(field, v["kosis_pct"])
        diffs = [abs(a - b) for a, b in zip(ref, v["synth_pct"])]
        vals.append(sum(diffs) / len(diffs))
    return round(sum(vals) / len(vals), 2) if vals else 0.0


# ---------------------------------------------------------------- 인구 구성 검증

SIDO_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주",
}


def _short_band(b):
    return (b.replace(" ~ ", "–").replace("세 이상", "+").replace("세 이하", "↓")
            .replace("세", "").strip())


def demographic_verify(spec, rows, sido="전체", sigungu=None):
    """성별·연령대·지역(시도) 구성이 KOSIS 수검 마진과 일치하는지 검증.

    합성 코호트의 인구 구성을 IPF 결합분포(demo_joint) 마진과 비교한다.
    시군구 모드는 '시군구 인구×시도 수검률' 보정 기대분포와 비교한다.
    검사수치가 아니라 '누가 표본에 들어갔나'의 충실도를 본다."""
    n = len(rows)
    dj = spec["demo_joint"]
    scope = SIDOS if sido == "전체" else [sido]
    sex_c = Counter(r["sex"] for r in rows)
    band_c = Counter(r["age_group"] for r in rows)
    sido_c = Counter(r["sido"] for r in rows)
    wmap = None
    if sigungu:
        code, _reg = _resolve_sigungu(spec, sigungu)
        _reg, wmap = _sigungu_demo(spec, code)

    def card(key, label, bin_labels, target, synth_counts):
        tot = sum(target)
        kosis = [t / tot for t in target] if tot else [0.0] * len(target)
        synth = [c / n for c in synth_counts]
        diffs = [abs(a - b) * 100 for a, b in zip(kosis, synth)]
        pct = [round(v * 100, 2) for v in kosis]
        return {"key": key, "label": label, "bins": bin_labels, "derived": False,
                "kosis_pct": pct, "raw_kosis_pct": pct,
                "synth_pct": [round(v * 100, 2) for v in synth],
                "max_diff_pct": round(max(diffs), 2),
                "raw_max_diff_pct": round(max(diffs), 2)}

    if wmap is not None:
        sex_t = [sum(wmap[(b, sx)] for b in AGE_BANDS) for sx in SEXES]
        age_t = [sum(wmap[(b, sx)] for sx in SEXES) for b in AGE_BANDS]
    else:
        sex_t = [sum(dj[sx][s][b] for s in scope for b in AGE_BANDS) for sx in SEXES]
        age_t = [sum(dj[sx][s][b] for s in scope for sx in SEXES) for b in AGE_BANDS]
    out = [
        card("sex", "성별 구성", list(SEXES), sex_t,
             [sex_c.get(sx, 0) for sx in SEXES]),
        card("age", "연령대 구성", [_short_band(b) for b in AGE_BANDS], age_t,
             [band_c.get(b, 0) for b in AGE_BANDS]),
    ]
    if sido == "전체" and wmap is None:
        out.append(card("sido", "지역(시도) 구성", [SIDO_SHORT[s] for s in SIDOS],
                        [sum(dj[sx][s][b] for sx in SEXES for b in AGE_BANDS) for s in SIDOS],
                        [sido_c.get(s, 0) for s in SIDOS]))
    return out


# ---------------------------------------------------------------- 셀별 일치율

def _bin_index(v, bins):
    for i, (_lab, _lo, hi) in enumerate(bins):
        if v < hi or i == len(bins) - 1:
            return i
    return len(bins) - 1


FIDELITY_KEYS = ["bmi", "waist", "sbp", "dbp", "glu", "hgb", "tc", "hdl", "tg",
                 "ldl", "cr", "egfr", "ast", "alt", "ggt"]


def fidelity_breakdown(spec, rows, min_cell=30):
    """검사항목별 (연령밴드×성별)·(시도×성별) 셀 단위 일치율 요약.

    각 셀에서 합성 구간분포 vs KOSIS 원시 셀분포의 최대 절대오차(%p)를 구해
    셀 표본수로 가중 평균/최대를 낸다. 표본 부족 셀(min_cell 미만)은 제외.
    주의: KOSIS는 검사수치를 시도·연령까지만 제공 → 시군구 셀 검증은 불가."""
    acc_as = {k: {} for k in FIDELITY_KEYS}
    acc_sido = {k: {} for k in FIDELITY_KEYS}
    binmap = {k: CONT_SPECS[k][2] for k in FIDELITY_KEYS}
    for r in rows:
        bs = (r["age_group"], r["sex"])
        ds = (r["sido"], r["sex"])
        for k in FIDELITY_KEYS:
            v = r[ROW_FIELD[k]]
            if v is None:  # 결측 제외
                continue
            bins = binmap[k]
            idx = _bin_index(v, bins)
            a = acc_as[k].get(bs)
            if a is None:
                a = acc_as[k][bs] = [0] * len(bins)
            a[idx] += 1
            d = acc_sido[k].get(ds)
            if d is None:
                d = acc_sido[k][ds] = [0] * len(bins)
            d[idx] += 1

    def summarize(acc, dim):
        out = {}
        for k in FIDELITY_KEYS:
            wsum, wmd, worst, excl, used = 0.0, 0.0, 0.0, 0, 0
            wmae, worst_mae = 0.0, 0.0
            for cell, cnt in acc[k].items():
                tot = sum(cnt)
                if tot < min_cell:
                    excl += 1
                    continue
                synth = [c / tot for c in cnt]
                if dim == "age":
                    kosis = spec["dist"][k]["age"][cell[0]][cell[1]]
                else:
                    kosis = spec["dist"][k]["sido"][cell[0]][cell[1]]
                diffs = [abs(a - b) for a, b in zip(synth, kosis)]
                md = max(diffs) * 100
                mae = sum(diffs) / len(diffs) * 100
                wsum += tot
                wmd += tot * md
                wmae += tot * mae
                worst = max(worst, md)
                worst_mae = max(worst_mae, mae)
                used += 1
            out[k] = {
                "mean": round(wmd / wsum, 2) if wsum else None,
                "max": round(worst, 2) if used else None,
                "mae_mean": round(wmae / wsum, 2) if wsum else None,
                "mae_max": round(worst_mae, 2) if used else None,
                "cells_used": used, "cells_excluded": excl,
            }
        return out

    as_sum = summarize(acc_as, "age")
    sido_sum = summarize(acc_sido, "sido")
    out = []
    for k in FIDELITY_KEYS:
        suffix = {"egfr": "(파생)", "ldl": "(파생)"}.get(k, "")
        out.append({
            "key": k, "label": VERIFY_LABELS[k] + suffix,
            "derived": k in ("egfr", "ldl"),
            "age_sex": as_sum[k], "sido": sido_sum[k],
        })
    return out


def sido_risk_compare(spec, rows, min_cell=30):
    """시도별 비만율(BMI≥25) — 합성 vs KOSIS 시도분포(성별가중).

    C안 히트맵에서 보이는 시도 간 격차가 생성기 인공물이 아니라 KOSIS
    시도별 분포에 실재하는 지역 격차임을 직접 확인하는 카드용 데이터."""
    if not rows:
        return None
    by = {}
    for r in rows:
        d = by.setdefault(r["sido"], {"n": 0, "ob": 0, "n_bmi": 0, "남자": 0, "여자": 0})
        d["n"] += 1
        d[r["sex"]] += 1
        if r["bmi"] is not None:  # 결측 제외
            d["n_bmi"] += 1
            if r["bmi"] >= 25.0:
                d["ob"] += 1
    bins_def = CONT_SPECS["bmi"][2]
    ob_idx = [i for i, (_l, lo, _h) in enumerate(bins_def) if lo >= 25.0]
    bins, kosis, synth, ns = [], [], [], []
    for s in SIDOS:
        d = by.get(s)
        if not d or d["n"] < min_cell:
            continue
        kp = 0.0
        for sex in SEXES:
            w = d[sex] / d["n"]
            p = spec["dist"]["bmi"]["sido"][s][sex]
            kp += w * sum(p[i] for i in ob_idx)
        bins.append(SIDO_SHORT[s])
        kosis.append(round(kp * 100, 2))
        synth.append(round(d["ob"] * 100.0 / (d["n_bmi"] or 1), 2))
        ns.append(d["n"])
    if len(bins) < 2:
        return None
    diffs = [abs(a - b) for a, b in zip(kosis, synth)]
    return {"key": "sido_obesity", "label": "시도별 비만율(BMI≥25) — 지역 격차 검증",
            "bins": bins, "kosis_pct": kosis, "raw_kosis_pct": kosis,
            "synth_pct": synth, "cell_n": ns, "derived": False,
            "max_diff_pct": round(max(diffs), 2),
            "raw_max_diff_pct": round(max(diffs), 2)}


# ---------------------------------------------------------------- 재식별 위험성 평가

def privacy_report(rows):
    """k-익명성(k≥3 기준) + l-다양성 + t-근접성 + 구조적 안전성 체크.

    - k-익명성: 준식별자(QI) 조합별 동일 클래스 크기. 기준 k≥3은 연구·내부 공유에서
      통용되는 실무 하한(구 비식별 조치 가이드라인(2016) 적정성평가 사례 수준)이며,
      현행 가명정보 처리 가이드라인은 고정 최소 k를 규정하지 않는다. 보건의료
      데이터엔 k≥5가 보수적 통념이라 k≥5 충족률을 반드시 병기한다.
    - l-다양성: 클래스 내 민감속성(종합판정) 값의 다양성. l=1이면 해당 조합의
      판정값이 그대로 노출되는 속성 노출(attribute disclosure) 구조.
    - t-근접성: 클래스 내 판정 분포와 전체 분포의 거리(순서형 EMD, 0~1).
      연령이 판정과 실제로 상관(고령→유질환자↑)하므로 t가 0이 될 수 없으며,
      이는 KOSIS 공표 통계에 이미 있는 집단 수준 정보다(개인 누출 아님)."""
    n = len(rows)
    sig_mode = bool(rows and rows[0].get("sigungu"))
    region_field = "sigungu" if sig_mode else "sido"
    region_label = "시군구" if sig_mode else "시도"
    qi_defs = [
        (f"{region_label} + 나이(1세) + 성별",
         lambda r: (r[region_field], r["age"], r["sex"])),
        (f"{region_label} + 연령구간(5세) + 성별",
         lambda r: (r[region_field], r["age_group"], r["sex"])),
        (f"{region_label} + 연령대(10세) + 성별",
         lambda r: (r[region_field], r["age_decade"], r["sex"])),
    ]
    gidx = {c: i for i, c in enumerate(GRADE_CATS)}
    m = len(GRADE_CATS)
    gtot = [0] * m
    for r in rows:
        gtot[gidx[r["result_grade"]]] += 1
    gp = [v / n for v in gtot]  # 전체 판정 분포(t-근접성 기준)

    k_rows = []
    for label, fn in qi_defs:
        groups = {}
        for r in rows:
            key = fn(r)
            grp = groups.get(key)
            if grp is None:
                grp = groups[key] = [0] * m
            grp[gidx[r["result_grade"]]] += 1
        sizes = [sum(grp) for grp in groups.values()]
        uniq = sum(s for s in sizes if s == 1)
        lt3 = sum(s for s in sizes if s < 3)
        lt5 = sum(s for s in sizes if s < 5)
        # l-다양성(구별 l)과 t-근접성(순서형 EMD: 누적분포 차의 평균)
        l_min, l1_records = m, 0
        t_max, t_wsum = 0.0, 0.0
        for grp in groups.values():
            tot = sum(grp)
            l_val = sum(1 for v in grp if v > 0)
            l_min = min(l_min, l_val)
            if l_val == 1:
                l1_records += tot
            cum, emd = 0.0, 0.0
            for i in range(m - 1):
                cum += grp[i] / tot - gp[i]
                emd += abs(cum)
            emd /= (m - 1)
            t_max = max(t_max, emd)
            t_wsum += emd * tot
        k_rows.append({
            "qi": label, "classes": len(groups), "min_k": min(sizes),
            "unique_pct": round(uniq * 100.0 / n, 2),
            "ge3_pct": round((n - lt3) * 100.0 / n, 2),
            "ge5_pct": round((n - lt5) * 100.0 / n, 2),
            "l_min": l_min, "l1_rec_pct": round(l1_records * 100.0 / n, 2),
            "t_mean": round(t_wsum / n, 3), "t_max": round(t_max, 3),
        })

    demo = k_rows[1]  # 지역+5세 연령구간+성별(표준 준식별자)
    if demo["ge3_pct"] >= 99.0:
        level = "낮음"
        level_note = "표준 준식별자에서 k≥3을 거의 만족 (k≥3 기준 판정 · k≥5 충족률 {0}%)".format(
            demo["ge5_pct"])
    elif demo["ge3_pct"] >= 95.0:
        level, level_note = "보통", "일부 희소 조합 존재 — 연령대 일반화 권장"
    else:
        level, level_note = "주의", "희소 조합 많음 — 표본 확대 또는 연령대 일반화 필요"

    k_status = "safe" if demo["ge3_pct"] >= 99 else (
        "warn" if demo["ge3_pct"] >= 95 else "bad")
    l_status = "safe" if demo["l1_rec_pct"] <= 1.0 else (
        "warn" if demo["l1_rec_pct"] <= 5.0 else "bad")
    checks = [
        {"item": "원천 데이터", "status": "safe",
         "detail": "KOSIS 집계 통계·인구 집계만 사용 — 개인 마이크로데이터 미사용"},
        {"item": "레코드 생성", "status": "safe",
         "detail": "분포에서 난수 독립 추출 — 실제 개인 값 미복제"},
        {"item": "식별자", "status": "safe",
         "detail": "synthetic_id는 순번(S000001…) — 외부 연결 키 없음"},
        {"item": "멤버십 추론", "status": "safe",
         "detail": "학습셋이 집계표라 '학습 포함 여부' 개념 불성립"},
        {"item": "파생 공식", "status": "safe",
         "detail": "CKD-EPI·Friedewald는 공개 식 — 누출원 아님"},
        {"item": f"k-익명성({region_label}+5세연령+성별)", "status": k_status,
         "detail": "k≥3 {0}% 충족(k≥5는 {1}%) · 최소 k={2}".format(
             demo["ge3_pct"], demo["ge5_pct"], demo["min_k"])},
        {"item": "l-다양성(민감속성: 종합판정)", "status": l_status,
         "detail": "최소 l={0} · 판정 단일(l=1) 클래스 소속 레코드 {1}%".format(
             demo["l_min"], demo["l1_rec_pct"])},
        {"item": "t-근접성(민감속성: 종합판정)", "status": "info",
         "detail": ("가중평균 t={0} · 최대 t={1} — 연령↔판정의 실제 상관을 반영한 "
                    "집단 수준 정보로, KOSIS 공표 통계 이상의 개인 정보 누출이 아님"
                    ).format(demo["t_mean"], demo["t_max"])},
        {"item": "형식적 보장(차분 프라이버시)", "status": "info",
         "detail": "미적용 — 입력이 이미 공개 집계라 보호 대상 개인 부재"},
    ]
    note = ("합성 레코드는 실존 인물과 1:1 대응이 없어 직접 재식별 위험은 구조적으로 낮습니다. "
            "k-익명성은 '공개 형식 안전성' 지표이며, 판정 기준 k≥3은 실무 통용 하한입니다"
            "(현행 가명정보 처리 가이드라인은 고정 k를 규정하지 않으며, 보건의료 데이터엔 "
            "k≥5가 보수적 통념이라 충족률을 병기). 검사 수치는 외부에서 알기 어려운 "
            "민감속성이라 준식별자로 보지 않습니다. l-다양성·t-근접성은 민감속성을 "
            "종합판정으로 두고 측정합니다. 표본이 작거나 1세 단위를 쓰면 희소도가 올라가니 "
            "연령대 일반화를 권장합니다.")
    return {"level": level, "level_note": level_note, "n": n,
            "sensitive_attr": "result_grade",
            "k_anonymity": k_rows, "checks": checks, "note": note}


# ---------------------------------------------------------------- CSV 직렬화

def rows_to_csv(rows, columns=None):
    """A안 CSV 직렬화. columns 지정 시 그 컬럼만(프로젝션, 항상 synthetic_id 포함)."""
    fields = _project_cols(columns)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        rr = {k: ("" if r.get(k) is None else r.get(k)) for k in fields}
        w.writerow(rr)
    return buf.getvalue()


def _project_cols(columns):
    """요청 컬럼을 COLUMNS 화이트리스트로 교집합. 식별·인구학 핵심은 항상 포함."""
    if not columns:
        return list(COLUMNS)
    req = [c.strip() for c in (columns.split(",") if isinstance(columns, str) else columns)]
    keep = [c for c in COLUMNS if c in set(req)]
    base = ["synthetic_id", "year", "sido", "sex", "age", "age_group"]
    ordered = [c for c in COLUMNS if c in keep or c in base]
    return ordered or list(COLUMNS)


def rows_to_json_bytes(rows, columns=None):
    """A안을 JSON 배열(UTF-8 bytes)로. columns 프로젝션 지원."""
    fields = _project_cols(columns)
    out = [{k: r.get(k) for k in fields} for r in rows]
    return json.dumps(out, ensure_ascii=False).encode("utf-8")


def build_datacard(meta, spec=None):
    """생성 조건·출처·면책을 담은 데이터카드(재현·인용용). dict 반환."""
    used_tables = sorted({t for v in CONT_SPECS.values() for t in v[:2]}
                         | {"N064", "N065", "N086", "N087", "N094", "N099",
                            "N121", "N122", "N130", "N132", "N002_1"})
    return {
        "title": "합성 건강검진 데이터 — 데이터카드(provenance)",
        "generator_version": SPEC_VERSION,
        "generated_at": meta.get("generated_at"),
        "parameters": {
            "n": meta.get("n"), "year": meta.get("year"), "seed": meta.get("seed"),
            "corr": meta.get("corr"), "sido": meta.get("sido"),
            "sigungu": meta.get("sigungu"), "sigungu_code": meta.get("sigungu_code"),
            "age_min": meta.get("age_min"), "age_max": meta.get("age_max"),
            "sex_filter": meta.get("sex_filter"), "anchor": meta.get("anchor"),
            "missing": meta.get("missing"),
        },
        "reproduce": "동일 파라미터+동일 seed로 generate() 호출 시 완전히 동일한 데이터가 재현됩니다.",
        "source": {
            "provider": "KOSIS 국민건강보험공단 건강검진통계",
            "year": meta.get("year"),
            "tables": used_tables,
            "license": "공공누리(KOGL) 출처표시 — KOSIS 이용약관 준수",
        },
        "columns": {c: COLUMN_LABELS_KO.get(c, c) for c in COLUMNS},
        "disclaimer": ("KOSIS 집계 분포로부터 생성한 가상 개인 데이터입니다. 실존 인물과 "
                       "무관하며 연구·개발·교육용으로만 사용하십시오. 실측 데이터가 아닙니다. "
                       "LDL(Friedewald)·eGFR(CKD-EPI)는 임상 공식 파생값입니다."),
    }


# 컬럼 한글 라벨(데이터카드용 — 프론트 COLUMN_LABELS와 동기)
COLUMN_LABELS_KO = {
    "synthetic_id": "합성ID", "year": "연도", "sido": "시도", "sigungu": "시군구",
    "sex": "성별", "age": "나이", "age_group": "연령구간(5세)", "age_decade": "연령대(10세)",
    "height": "신장(cm)", "weight": "체중(kg)", "bmi": "체질량지수", "waist": "허리둘레(cm)",
    "sbp": "수축기혈압", "dbp": "이완기혈압", "fasting_glucose": "공복혈당",
    "hemoglobin": "혈색소", "total_cholesterol": "총콜레스테롤", "hdl": "HDL",
    "ldl": "LDL(Friedewald 파생)", "triglyceride": "중성지방",
    "creatinine": "크레아티닌", "egfr": "eGFR(CKD-EPI 파생)", "ast": "AST", "alt": "ALT",
    "ggt": "γ-GTP", "urine_protein": "요단백", "chest_xray": "흉부방사선",
    "vision_left": "시력(좌)", "vision_right": "시력(우)", "bone_density": "골밀도",
    "result_grade": "종합판정", "risk_group": "위험군",
}


def dicts_to_csv(items, fields):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    for it in items:
        w.writerow({k: it.get(k, "") for k in fields})
    return buf.getvalue()


B_FIELDS = ["key", "indicator", "criteria", "normal_pct", "caution_pct", "disease_pct"]
C_FIELDS = ["sido", "age_decade", "n", "obesity_pct", "htn_pct", "dm_pct",
            "dyslip_pct", "risk_score", "risk_level"]


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="KOSIS 기반 합성 건강검진 데이터 생성기")
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--sido", default="전체")
    ap.add_argument("--sigungu", default=None, help="시군구 코드 또는 이름(예: 11110, 종로구)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--corr", type=float, default=1.0)
    ap.add_argument("--year", type=int, default=None, help="대상 연도(미지정=최신)")
    ap.add_argument("--rebuild-spec", action="store_true")
    ap.add_argument("--build-all-years", action="store_true",
                    help="가용 모든 연도 스펙 캐시 빌드(배포용) 후 종료")
    ap.add_argument("--out-dir", default=None, help="CSV 3종(A/B/C안) 저장 폴더")
    args = ap.parse_args(argv)

    if args.build_all_years:
        yrs = build_all_years()
        print(f"[spec] 전체 연도 빌드 완료: {yrs}")
        return 0

    spec = load_spec(rebuild=args.rebuild_spec, year=args.year)
    rows, meta = generate(spec, args.n, sido=args.sido, seed=args.seed, corr=args.corr,
                          sigungu=args.sigungu)
    ver = verify_report(spec, rows)
    print(f"[generate] n={meta['n']} sido={meta['sido']} seed={meta['seed']} "
          f"corr={meta['corr']} elapsed={meta['elapsed_ms']}ms "
          f"mean|diff|={mean_abs_diff(ver)}%p")
    gc = grade_compare(spec, rows)
    print("[grade] KOSIS", gc["kosis_pct"], "/ 합성", gc["synth_pct"], "(", gc["bins"], ")")
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        with open(os.path.join(args.out_dir, "synthetic_health_a.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            f.write(rows_to_csv(rows))
        with open(os.path.join(args.out_dir, "synthetic_health_b.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            f.write(dicts_to_csv(summary_b(rows), B_FIELDS))
        with open(os.path.join(args.out_dir, "synthetic_health_c.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            f.write(dicts_to_csv(matrix_c(rows), C_FIELDS))
        print(f"[csv] 저장 완료 -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
