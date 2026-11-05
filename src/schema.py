"""
CaseLens 스키마 계층 (범용).

업로드된 표(CSV)의 컬럼 역할(날짜·지표·차원)을 추정하고, 이후 모든 모듈이
원본 컬럼명 대신 DataSpec 만 참조하도록 하는 단일 진입점.

  load_table()        CSV 로드 (utf-8-sig → utf-8 → cp949 순 시도)
  profile()           컬럼별 프로파일
  infer_spec()        DataSpec 자동 추정 (사용자가 UI에서 수정 가능)
  validate_spec()     DataSpec 검증 (오류 문자열 목록)
  aggregate_metric()  지표 집계 (일/주/월, 차원별, 빈 날짜 보정)
  infer_aux_table()   보조 테이블(재고·가격 등)의 역할과 조인 키 추정

원칙: 컬럼명 하드코딩 금지. 힌트 정규식은 '추정 보조'일 뿐이며 최종 결정은 DataSpec.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# ───────────────────────── 이름 힌트 (추정 보조) ─────────────────────────
HINT_DATE = re.compile(r"date|dt$|day|time|일자|날짜|일시|주문일|기준일|ymd", re.I)
HINT_METRIC = re.compile(r"revenue|sales|amount|amt|gmv|매출|금액|판매액|결제", re.I)
HINT_COUNT = re.compile(r"qty|quantity|count|cnt|orders?|수량|건수|주문수", re.I)
HINT_PRICE = re.compile(r"price|단가|가격|rate|ratio|pct|비율|율$", re.I)
HINT_CANCEL = re.compile(r"cancel(?:l?ed|lation)?|refund(?:ed)?|return(?:ed)?|취소|환불|반품", re.I)
HINT_ID = re.compile(r"(^|_)id$|^id_|code$|코드|번호|^no$|index", re.I)
HINT_DIM = re.compile(r"categor|region|area|channel|segment|group|type|brand|store|city|device|"
                      r"카테고리|상품군|분류|지역|채널|매장|브랜드|기기|구분", re.I)
HINT_STOCK = re.compile(r"stock|inventory|on_?hand|재고", re.I)
HINT_PRICE_ROLE = re.compile(r"price|단가|가격", re.I)

MAX_DIM_CARDINALITY = 50
MAX_DIM_RATIO = 0.2          # 고유값 수 / 행 수 상한 (이 이하만 차원 후보)


# ───────────────────────── 데이터 구조 ─────────────────────────
@dataclass
class MetricSpec:
    name: str                       # 표시용 이름 (예: "revenue", "cancel_qty_rate")
    kind: str                       # "sum" | "mean" | "count" | "ratio"
    col: str | None = None          # sum/mean 대상, ratio 분자
    denom_col: str | None = None    # ratio 분모


@dataclass
class DataSpec:
    date_col: str
    metric: MetricSpec                                   # 주 지표 (사건 탐지 기본 대상)
    dim_cols: list[str] = field(default_factory=list)
    extra_metrics: list[MetricSpec] = field(default_factory=list)   # 보조 감시 지표

    def all_metrics(self) -> dict[str, MetricSpec]:
        return {m.name: m for m in [self.metric, *self.extra_metrics]}


@dataclass
class AuxSpec:
    role: str                       # "inventory" | "price" | "unknown"
    date_col: str
    value_col: str
    key_map: dict[str, str]         # {보조 테이블 컬럼: 주 테이블 차원 컬럼}


@dataclass
class ColumnProfile:
    name: str
    kind: str                       # "date" | "numeric" | "text" | "bool" | "other"
    n_unique: int
    null_rate: float
    sample: list


@dataclass
class InferResult:
    spec: DataSpec
    date_candidates: list[tuple[str, float]]
    metric_candidates: list[tuple[str, float]]
    dim_candidates: list[tuple[str, float]]
    warnings: list[str]


# ───────────────────────── 로드·파싱 ─────────────────────────
def load_table(path_or_buf) -> pd.DataFrame:
    """CSV 로드. 한국어 CSV(cp949) 대응. 경로·파일객체(업로더) 모두 허용."""
    last: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            if hasattr(path_or_buf, "seek"):
                path_or_buf.seek(0)
            return pd.read_csv(path_or_buf, encoding=enc)
        except UnicodeDecodeError as e:
            last = e
    raise ValueError(f"CSV 인코딩을 해석할 수 없습니다: {last}")


def parse_dates(s: pd.Series) -> pd.Series:
    """날짜 파싱. 정수는 YYYYMMDD 형식만 허용(그 외 정수가 epoch으로 오인되는 것을 방지)."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if pd.api.types.is_numeric_dtype(s):
        v = pd.to_numeric(s, errors="coerce")
        ok = v.between(19000101, 22001231) & (v == v.round())
        out = pd.to_datetime(v.where(ok).astype("Int64").astype("string"), format="%Y%m%d", errors="coerce")
        return out
    txt = s.astype("string").str.strip()
    if txt.str.fullmatch(r"\d{8}").fillna(False).mean() > 0.9:
        return pd.to_datetime(txt, format="%Y%m%d", errors="coerce")
    out = pd.to_datetime(txt, format="ISO8601", errors="coerce")
    if out.notna().mean() < 0.9:                       # ISO 가 아니면 혼합 형식 허용 (2026/8/3, 08-03-2026 등)
        out = pd.to_datetime(txt, format="mixed", errors="coerce")
    return out


def _date_ratio(s: pd.Series) -> float:
    nn = s.dropna()
    if nn.empty:
        return 0.0
    return float(parse_dates(nn).notna().mean())


# ───────────────────────── 프로파일링 ─────────────────────────
def profile(df: pd.DataFrame) -> list[ColumnProfile]:
    out = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s):
            kind = "bool"
        elif pd.api.types.is_datetime64_any_dtype(s) or _date_ratio(s) >= 0.9 and not _is_plain_number(s, c):
            kind = "date"
        elif pd.api.types.is_numeric_dtype(s):
            kind = "numeric"
        elif pd.api.types.is_string_dtype(s) or s.dtype == object:
            kind = "text"
        else:
            kind = "other"
        out.append(ColumnProfile(c, kind, int(s.nunique(dropna=True)), float(s.isna().mean()),
                                 s.dropna().head(3).tolist()))
    return out


def _is_plain_number(s: pd.Series, name: str) -> bool:
    """숫자형인데 날짜 힌트 이름이 아니면 일반 수치로 본다 (YYYYMMDD 정수는 이름 힌트가 있어야 날짜)."""
    return pd.api.types.is_numeric_dtype(s) and not HINT_DATE.search(name)


# ───────────────────────── 추정 ─────────────────────────
def _score_date(df: pd.DataFrame, c: str) -> float:
    s = df[c]
    if _is_plain_number(s, c):
        return 0.0
    r = _date_ratio(s)
    if r < 0.9:
        return 0.0
    score = 2.0 * r
    if HINT_DATE.search(c):
        score += 2.0
    d = parse_dates(s.dropna())
    if d.nunique() < len(d):          # 일 단위 반복(여러 차원 행) → 시계열 테이블에 전형적
        score += 0.5
    return score


def _is_numeric_measure(df: pd.DataFrame, c: str) -> bool:
    s = df[c]
    if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
        return False
    if HINT_ID.search(c):
        return False
    if s.nunique() <= 1:
        return False
    if pd.api.types.is_integer_dtype(s) and s.nunique() == len(s) and not HINT_METRIC.search(c):
        return False                   # 고유 정수 = ID 가능성
    return True


def _score_metric(df: pd.DataFrame, c: str) -> float:
    if not _is_numeric_measure(df, c):
        return -1.0
    score = 1.0
    if HINT_METRIC.search(c):
        score += 3.0
    if HINT_COUNT.search(c):
        score += 1.0
    if HINT_PRICE.search(c):
        score -= 2.0                   # 단가·비율은 합계 지표로 부적합
    if HINT_CANCEL.search(c):
        score -= 2.0                   # 취소량은 보조 지표
    if re.search(r"net|순", c, re.I):
        score -= 0.3                   # 동점 시 총액 기준 컬럼 우선
    return score


def _score_dim(df: pd.DataFrame, c: str, date_col: str, metric_cols: set[str]) -> float:
    if c == date_col or c in metric_cols:
        return -1.0
    s = df[c]
    n_unique = s.nunique(dropna=True)
    is_text = pd.api.types.is_string_dtype(s) or s.dtype == object or isinstance(s.dtype, pd.CategoricalDtype)
    is_lowint = pd.api.types.is_integer_dtype(s) and HINT_DIM.search(c) and not HINT_ID.search(c)
    if not (is_text or is_lowint):
        return -1.0
    if n_unique < 2 or n_unique > MAX_DIM_CARDINALITY or n_unique > max(MAX_DIM_RATIO * len(df), 5):
        return -1.0
    if _date_ratio(s) >= 0.9 and not pd.api.types.is_numeric_dtype(s):
        return -1.0
    score = 1.0 + (2.0 if HINT_DIM.search(c) else 0.0)
    score += 0.5 / n_unique            # 고유값이 적을수록 해석이 쉬움 (동점 정렬용)
    return score


_SYNONYMS = [(re.compile(r"quantity|qty|수량"), "qty"),
             (re.compile(r"amount|amt|금액"), "amt"),
             (re.compile(r"count|cnt|건수"), "cnt")]


def _stem(name: str) -> str:
    """취소 토큰을 제거하고 약어를 정규화한 비교용 어간. cancelled_qty / quantity → 'qty'."""
    t = re.sub(r"[_\s]+", "", HINT_CANCEL.sub("", name)).lower()
    for pat, rep in _SYNONYMS:
        t = pat.sub(rep, t)
    return t


def _infer_ratio_metrics(df: pd.DataFrame) -> list[MetricSpec]:
    """취소/환불 컬럼과 같은 stem 의 전체 컬럼을 짝지어 비율 지표를 만든다. (cancel_qty ↔ qty)"""
    out = []
    cols = [c for c in df.columns if _is_numeric_measure(df, c)]
    for c in cols:
        if not HINT_CANCEL.search(c):
            continue
        stem = _stem(c)
        for d in cols:
            if d != c and not HINT_CANCEL.search(d) and _stem(d) == stem and stem:
                out.append(MetricSpec(name=f"{c}_rate", kind="ratio", col=c, denom_col=d))
    return out


def infer_spec(df: pd.DataFrame) -> InferResult:
    warnings: list[str] = []

    date_scores = sorted(((c, _score_date(df, c)) for c in df.columns), key=lambda x: -x[1])
    date_cands = [(c, s) for c, s in date_scores if s > 0]
    if not date_cands:
        raise ValueError("날짜 컬럼을 찾지 못했습니다. 날짜 컬럼을 직접 지정해 주세요.")
    date_col = date_cands[0][0]
    if len(date_cands) > 1 and date_cands[0][1] - date_cands[1][1] < 0.5:
        warnings.append(f"날짜 컬럼 후보가 여러 개입니다: {[c for c, _ in date_cands]} → '{date_col}' 선택")

    metric_scores = sorted(((c, _score_metric(df, c)) for c in df.columns if c != date_col),
                           key=lambda x: (-x[1], len(x[0])))
    metric_cands = [(c, s) for c, s in metric_scores if s > 0]
    if not metric_cands:
        raise ValueError("수치 지표 컬럼을 찾지 못했습니다. 지표 컬럼을 직접 지정해 주세요.")
    metric_col = metric_cands[0][0]
    if len(metric_cands) > 1 and metric_cands[0][1] - metric_cands[1][1] < 0.5:
        warnings.append(f"지표 후보가 여러 개입니다: {[c for c, _ in metric_cands[:4]]} → '{metric_col}' 선택")

    dim_scores = sorted(((c, _score_dim(df, c, date_col, {c2 for c2, _ in metric_cands})) for c in df.columns),
                        key=lambda x: -x[1])
    dim_cands = [(c, s) for c, s in dim_scores if s > 0]
    dim_cols = [c for c, _ in dim_cands[:5]]
    if not dim_cols:
        warnings.append("차원(분류) 컬럼 후보가 없습니다. 원인 분해(기여도) 기능이 제한됩니다.")

    spec = DataSpec(date_col=date_col,
                    metric=MetricSpec(name=metric_col, kind="sum", col=metric_col),
                    dim_cols=dim_cols,
                    extra_metrics=_infer_ratio_metrics(df))
    return InferResult(spec, date_cands, metric_cands, dim_cands, warnings)


# ───────────────────────── 검증 ─────────────────────────
def validate_spec(df: pd.DataFrame, spec: DataSpec) -> list[str]:
    """오류 메시지 목록. 빈 리스트면 사용 가능."""
    errs: list[str] = []
    if spec.date_col not in df.columns:
        errs.append(f"날짜 컬럼 '{spec.date_col}' 없음")
    elif _date_ratio(df[spec.date_col]) < 0.9:
        errs.append(f"'{spec.date_col}' 컬럼의 90% 이상이 날짜로 해석되지 않음")
    for m in spec.all_metrics().values():
        if m.kind not in {"sum", "mean", "count", "ratio"}:
            errs.append(f"지표 '{m.name}': 지원하지 않는 kind '{m.kind}'")
            continue
        needed = [m.col] if m.kind in {"sum", "mean"} else [m.col, m.denom_col] if m.kind == "ratio" else []
        for c in needed:
            if c is None or c not in df.columns:
                errs.append(f"지표 '{m.name}': 컬럼 '{c}' 없음")
            elif not pd.api.types.is_numeric_dtype(df[c]):
                errs.append(f"지표 '{m.name}': 컬럼 '{c}' 가 수치형이 아님")
    for d in spec.dim_cols:
        if d not in df.columns:
            errs.append(f"차원 컬럼 '{d}' 없음")
        elif d == spec.date_col:
            errs.append(f"차원 컬럼 '{d}' 가 날짜 컬럼과 같음")
    return errs


# ───────────────────────── 집계 ─────────────────────────
_PERIOD = {"D": "D", "W": "W-SUN", "M": "M"}


def aggregate_metric(df: pd.DataFrame, spec: DataSpec, metric: str | None = None,
                     by: list[str] | str | None = None, freq: str = "D",
                     start=None, end=None, fill_missing: bool = True) -> pd.DataFrame:
    """지표를 날짜(·차원)별로 집계. 반환 컬럼: date, [by...], value (ratio 는 num, den 추가).

    - sum/count 의 빈 날짜는 0, mean/ratio 의 빈 날짜는 NaN
    - 모든 도구(기여도·탐지·재고 검증)가 이 함수로 수치를 얻는다 → 수치 출처 단일화
    """
    errs = validate_spec(df, spec)
    if errs:
        raise ValueError("DataSpec 오류: " + "; ".join(errs))
    m = spec.all_metrics()[metric or spec.metric.name]
    by_cols = [by] if isinstance(by, str) else list(by or [])
    if freq not in _PERIOD:
        raise ValueError(f"freq 는 {list(_PERIOD)} 중 하나")

    d = df.copy()
    d["_ts"] = parse_dates(d[spec.date_col])
    d = d.dropna(subset=["_ts"])
    if start is not None:
        d = d[d["_ts"] >= pd.Timestamp(start)]
    if end is not None:
        d = d[d["_ts"] <= pd.Timestamp(end)]
    d["date"] = d["_ts"].dt.to_period(_PERIOD[freq]).dt.start_time if freq != "D" else d["_ts"].dt.normalize()

    d["date"] = pd.to_datetime(d["date"]).astype("datetime64[ns]")   # 해상도 통일 (reindex/merge 불일치 방지)
    keys = ["date", *by_cols]
    g = d.groupby(keys, dropna=False)
    if m.kind == "sum":
        out = g[m.col].sum().rename("value").reset_index()
    elif m.kind == "mean":
        out = g[m.col].mean().rename("value").reset_index()
    elif m.kind == "count":
        out = g.size().rename("value").reset_index()
    else:  # ratio
        out = g[[m.col, m.denom_col]].sum().rename(columns={m.col: "num", m.denom_col: "den"}).reset_index()
        out["value"] = np.where(out["den"] != 0, out["num"] / out["den"].replace(0, np.nan), np.nan)

    if fill_missing and len(out):
        if freq == "D":
            full = pd.date_range(out["date"].min(), out["date"].max(), freq="D")
        else:
            full = pd.period_range(out["date"].min(), out["date"].max(), freq=_PERIOD[freq]).start_time
        grid = pd.DataFrame({"date": pd.DatetimeIndex(full).astype("datetime64[ns]")})
        for c in by_cols:
            grid = grid.merge(pd.DataFrame({c: out[c].unique()}), how="cross")
        out = grid.merge(out, on=keys, how="left")
        zero_cols = ["value"] if m.kind in {"sum", "count"} else []
        zero_cols += ["num", "den"] if m.kind == "ratio" else []
        out[zero_cols] = out[zero_cols].fillna(0)
    return out.sort_values(keys).reset_index(drop=True)


# ───────────────────────── 보조 테이블 ─────────────────────────
def infer_aux_table(aux: pd.DataFrame, main: pd.DataFrame, spec: DataSpec) -> AuxSpec | None:
    """재고·가격 등 보조 테이블의 (역할, 날짜 컬럼, 값 컬럼, 조인 키)를 추정. 연결 불가면 None.

    조인 키: 보조 테이블의 텍스트 컬럼 중 값 집합이 주 테이블 차원 컬럼 값의 80% 이상 겹치는 것.
    (컬럼명이 달라도 값이 겹치면 연결 → 예: sku_group ↔ category)
    """
    date_scores = sorted(((c, _score_date(aux, c)) for c in aux.columns), key=lambda x: -x[1])
    if not date_scores or date_scores[0][1] <= 0:
        return None
    date_col = date_scores[0][0]

    key_map: dict[str, str] = {}
    for c in aux.columns:
        if c == date_col or pd.api.types.is_numeric_dtype(aux[c]):
            continue
        a_vals = set(aux[c].dropna().unique())
        best, best_ov = None, 0.0
        for dcol in spec.dim_cols:
            m_vals = set(main[dcol].dropna().unique())
            if not m_vals or not a_vals:
                continue
            ov = len(a_vals & m_vals) / len(a_vals)
            if ov >= 0.8 and ov > best_ov and dcol not in key_map.values():
                best, best_ov = dcol, ov
        if best:
            key_map[c] = best
    if not key_map:
        return None

    vals = [c for c in aux.columns if c != date_col and c not in key_map and _is_numeric_measure(aux, c)]
    if not vals:
        return None
    role, value_col = "unknown", vals[0]
    for c in vals:
        if HINT_STOCK.search(c):
            role, value_col = "inventory", c
            break
        if HINT_PRICE_ROLE.search(c):
            role, value_col = "price", c
    return AuxSpec(role=role, date_col=date_col, value_col=value_col, key_map=key_map)
