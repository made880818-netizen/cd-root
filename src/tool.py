"""CaseLens 조사 도구 (범용).

사건 구간(incident)과 기준 구간(baseline)이 주어졌을 때, 가설을 검증하는 '증거 수집 함수'들.
모든 수치는 schema.aggregate_metric (또는 그 위의 pandas 연산)에서만 나오고,
도구는 컬럼명을 하드코딩하지 않고 DataSpec/AuxSpec 만 참조한다.

도구는 "사실(숫자)"만 반환한다. 원인 여부 판정(채택/기각/검증 불가)은 다음 단계(가설 평가)의 몫.

  build_context()          데이터 + 스펙 + 보조 테이블 + 기간을 묶은 Context 생성
  run_tool(ctx, name, ..)  이름으로 도구 실행 (예외는 ToolResult(status="error")로 변환)
  available_tools(ctx)     현재 데이터로 실행 가능한 도구와 불가 사유

  compare_periods          기준 vs 사건 전체 변화
  contribution_by_dim      차원별 변화 기여도 (+기준 비중 대비 배수 lift)
  rank_dimensions          어느 차원에서 변화가 한쪽에 몰렸는지 순위
  check_ratio_metric       비율 지표(취소율 등) 변화, 차원별 분해
  check_inventory          재고(보조 테이블) 고갈 여부·시작일·회복일
  check_price_change       가격 변동 세그먼트와 그 기여도
  check_timing             세그먼트 지표 변화 시점 vs 보조 신호 변화 시점 (선행/동시/후행)
  check_data_quality       행 수·결측 날짜·중복·음수·결측률 점검 (데이터 오류 가설)

실행 (caselens/ 에서):  python src/tools.py      ← data/ 의 실제 파일로 시연 출력
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

try:                                    # src/ 가 sys.path 에 있을 때
    import schema as sc
except ImportError:                     # 패키지로 import 될 때
    from . import schema as sc

# ───────────────────────── 임계값 (한곳에 모음) ─────────────────────────
LOW_STOCK_RATIO = 0.25        # 정상 재고 중앙값의 25% 이하 = 저재고
LOOKBACK_DAYS = 60            # 정상 수준(중앙값) 산정에 쓰는 사건 직전 일수
POST_DAYS = 14                # 사건 종료 후 회복 확인용 추가 일수
MIN_SHIFT_PCT = 10.0          # 세그먼트 지표 변화가 이보다 작으면 '변화 시점' 판정 안 함
PRICE_CHANGE_PCT = 1.0        # 가격 변동으로 볼 최소 변동률(%)
DQ_ROW_DROP_PCT = 30.0        # 행 수 감소 경고
DQ_DUP_RATE_PCT = 5.0         # 중복 행 비율 경고
DQ_NULL_RISE_PP = 2.0         # 결측률 상승(%p) 경고
MIN_TOTAL_CHANGE_PCT = 5.0    # 전체 변화율이 이보다 작으면 기여도(%)가 불안정(분모≈0)해 해석하지 않음
EPS = 1e-9


# ───────────────────────── 기간·컨텍스트 ─────────────────────────
@dataclass(frozen=True)
class Period:
    start: pd.Timestamp
    end: pd.Timestamp            # 포함

    def __post_init__(self):
        object.__setattr__(self, "start", pd.Timestamp(self.start).normalize())
        object.__setattr__(self, "end", pd.Timestamp(self.end).normalize())
        if self.end < self.start:
            raise ValueError(f"기간의 끝({self.end:%Y-%m-%d})이 시작({self.start:%Y-%m-%d})보다 이릅니다")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        return f"{self.start:%Y-%m-%d} ~ {self.end:%Y-%m-%d}"

    @classmethod
    def coerce(cls, x) -> "Period":
        """Period | {"start_date","end_date"} | {"start","end"} | (start, end)"""
        if isinstance(x, Period):
            return x
        if isinstance(x, dict):
            s = x.get("start_date", x.get("start"))
            e = x.get("end_date", x.get("end"))
            if s is None or e is None:
                raise ValueError("기간 dict 에는 start_date/end_date 가 필요합니다")
            return cls(s, e)
        if isinstance(x, (tuple, list)) and len(x) == 2:
            return cls(x[0], x[1])
        raise ValueError(f"기간 형식을 해석할 수 없습니다: {x!r}")


def default_baseline(incident: Period) -> Period:
    """사건 직전의 같은 길이 구간."""
    end = incident.start - pd.Timedelta(days=1)
    return Period(end - pd.Timedelta(days=incident.days - 1), end)


@dataclass
class AuxEntry:
    name: str
    df: pd.DataFrame
    spec: sc.AuxSpec


@dataclass
class Context:
    df: pd.DataFrame
    spec: sc.DataSpec
    incident: Period
    baseline: Period
    ts: pd.Series                                   # df 의 날짜 컬럼 파싱 결과 (ns, 일 단위로 정규화)
    aux: dict[str, AuxEntry] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def aux_by_role(self, role: str) -> AuxEntry | None:
        for e in self.aux.values():
            if e.spec.role == role:
                return e
        return None


def _ns(x):
    """날짜 인덱스/시리즈를 datetime64[ns] 로 통일 (pandas 버전별 해상도 차이 방지)."""
    if isinstance(x, pd.Series):
        return x.astype("datetime64[ns]")
    return pd.DatetimeIndex(x).astype("datetime64[ns]")


def build_context(df: pd.DataFrame, incident, baseline=None,
                  aux: dict[str, pd.DataFrame] | None = None,
                  spec: sc.DataSpec | None = None) -> Context:
    """주 테이블·보조 테이블·기간을 검증하고 Context 로 묶는다.

    spec 을 주지 않으면 schema.infer_spec 으로 추정한다 (UI에서 사용자가 고친 spec 을 넘길 수 있음).
    """
    notes: list[str] = []
    if spec is None:
        r = sc.infer_spec(df)
        spec = r.spec
        notes += r.warnings
    errs = sc.validate_spec(df, spec)
    if errs:
        raise ValueError("DataSpec 오류: " + "; ".join(errs))

    ts = _ns(sc.parse_dates(df[spec.date_col]).dt.normalize())
    inc = Period.coerce(incident)
    base = Period.coerce(baseline) if baseline is not None else default_baseline(inc)
    if base.end >= inc.start:
        raise ValueError(f"기준 구간({base.label})과 사건 구간({inc.label})이 겹칩니다")
    lo, hi = ts.min(), ts.max()
    if inc.start < lo or inc.end > hi:
        raise ValueError(f"사건 구간({inc.label})이 데이터 범위({lo:%Y-%m-%d} ~ {hi:%Y-%m-%d})를 벗어납니다")
    for name, p in (("기준", base), ("사건", inc)):
        if not ((ts >= p.start) & (ts <= p.end)).any():
            raise ValueError(f"{name} 구간({p.label})에 데이터가 없습니다")

    entries: dict[str, AuxEntry] = {}
    for name, adf in (aux or {}).items():
        a = sc.infer_aux_table(adf, df, spec)
        if a is None:
            notes.append(f"보조 테이블 '{name}' 은 주 테이블 차원과 연결되지 않아 사용하지 않습니다")
        else:
            entries[name] = AuxEntry(name, adf, a)
    return Context(df=df, spec=spec, incident=inc, baseline=base, ts=ts, aux=entries, notes=notes)


# ───────────────────────── 결과 객체 ─────────────────────────
@dataclass
class ToolResult:
    tool: str
    status: str                     # "ok" | "unavailable" | "error"
    args: dict
    data: dict = field(default_factory=dict)        # 핵심 수치 (스칼라·짧은 리스트)
    table: pd.DataFrame | None = None               # 상세 표
    summary: str = ""                               # 수치로 만든 한 줄 요약 (템플릿)
    query: str = ""                                 # 수치가 어떻게 계산됐는지 (출처 표시용)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self, max_rows: int = 50) -> dict:
        return _jsonable({"tool": self.tool, "status": self.status, "args": self.args,
                          "data": self.data, "summary": self.summary, "query": self.query,
                          "notes": self.notes,
                          "table": None if self.table is None else self.table.head(max_rows)})


def _jsonable(o: Any):
    if isinstance(o, pd.DataFrame):
        return [_jsonable(r) for r in o.to_dict(orient="records")]
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    if isinstance(o, Period):
        return o.label
    if isinstance(o, pd.Timestamp):
        return None if pd.isna(o) else o.strftime("%Y-%m-%d")
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if o is pd.NaT:
        return None
    return o


def _unavailable(tool: str, args: dict, why: str) -> ToolResult:
    return ToolResult(tool, "unavailable", args, summary=f"검증 불가: {why}", notes=[why])


def _error(tool: str, args: dict, why: str) -> ToolResult:
    return ToolResult(tool, "error", args, summary=f"오류: {why}", notes=[why])


def _fmt(x, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:,.{digits}f}"


def _pct(a: float, b: float) -> float:
    """(b-a)/a*100, a==0 이면 NaN"""
    return (b - a) / a * 100 if abs(a) > EPS else float("nan")


# ───────────────────────── 내부 공통 함수 ─────────────────────────
def _as_list(x) -> list:
    if x is None:
        return []
    return [x] if isinstance(x, str) else list(x)


def _apply_filters(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    """filters={컬럼: 값 또는 값 리스트}. 값은 문자열로 비교(정수 코드 차원 대응)."""
    for c, v in (filters or {}).items():
        if c not in df.columns:
            raise ValueError(f"필터 컬럼 '{c}' 가 데이터에 없습니다")
        vals = {str(x) for x in (v if isinstance(v, (list, tuple, set)) else [v])}
        df = df[df[c].astype(str).isin(vals)]
    return df


def _metric(ctx: Context, name: str | None) -> sc.MetricSpec:
    ms = ctx.spec.all_metrics()
    key = name or ctx.spec.metric.name
    if key not in ms:
        raise ValueError(f"지표 '{key}' 가 없습니다. 사용 가능: {list(ms)}")
    return ms[key]


def _end_ns(p: Period) -> pd.Timestamp:
    return p.end + pd.Timedelta(days=1) - pd.Timedelta(1, "ns")


def _agg_period(ctx: Context, metric: str, period: Period, by: list[str],
                filters: dict | None = None) -> pd.DataFrame:
    """기간 합계를 차원별로 집계 (schema.aggregate_metric 경유). 결측 차원값은 '(결측)'."""
    sub = _apply_filters(ctx.df, filters)
    cols = ["value"] + (["num", "den"] if _metric(ctx, metric).kind == "ratio" else [])
    if len(sub) == 0:
        return pd.DataFrame(columns=[*by, *cols])
    a = sc.aggregate_metric(sub, ctx.spec, metric=metric, by=by, start=period.start,
                            end=_end_ns(period), fill_missing=False)
    if len(a) == 0:
        return pd.DataFrame(columns=[*by, *cols])
    for c in by:
        a[c] = a[c].astype(object).where(a[c].notna(), "(결측)")
    group = by if by else None
    
    if group is None:                       # 전체 합계 한 행
        row = {c: a[c].sum() for c in cols if c in ("num", "den")}
        if "num" in row:
            den = row["den"]
            row["value"] = row["num"] / den if abs(den) > EPS else float("nan")
        elif _metric(ctx, metric).kind == "mean":
            row["value"] = a["value"].mean()
        else:
            row["value"] = a["value"].sum()
        return pd.DataFrame([row])
    agg = {"value": "sum"} if "num" not in cols else {"num": "sum", "den": "sum"}
    out = a.groupby(by, dropna=False).agg(agg).reset_index()
    if "num" in cols:
        out["value"] = np.where(out["den"].abs() > EPS, out["num"] / out["den"].replace(0, np.nan), np.nan)
    return out


#sum group case summary function
def _desc_query(ctx: Context, metric: str, by: list[str], filters: dict | None, extra: str = "") -> str:
    m = _metric(ctx, metric)
    expr = {"sum": f"SUM({m.col})", "mean": f"AVG({m.col})", "count": "COUNT(*)",
            "ratio": f"SUM({m.col})/SUM({m.denom_col})"}[m.kind]
    where = "".join(f" AND {c}={v!r}" for c, v in (filters or {}).items())
    grp = f" GROUP BY {', '.join(by)}" if by else ""
    return (f"{expr} FROM main WHERE {ctx.spec.date_col} IN 기준[{ctx.baseline.label}] / "
            f"사건[{ctx.incident.label}]{where}{grp}{extra}")


def _daily_series(ctx: Context, metric: str, start: pd.Timestamp, end: pd.Timestamp,
                  filters: dict | None = None) -> pd.Series:
    """[start, end] 의 모든 날짜를 인덱스로 갖는 일별 지표 시리즈. sum/count 빈 날짜=0, 그 외 NaN."""
    idx = _ns(pd.date_range(start, end, freq="D"))
    sub = _apply_filters(ctx.df, filters)
    if len(sub) == 0:
        return pd.Series(np.nan, index=idx)
    a = sc.aggregate_metric(sub, ctx.spec, metric=metric, start=start,
                            end=end + pd.Timedelta(days=1) - pd.Timedelta(1, "ns"), fill_missing=False)
    s = pd.Series(a["value"].to_numpy(), index=_ns(a["date"])).reindex(idx)
    if _metric(ctx, metric).kind in ("sum", "count"):
        s = s.fillna(0)
    return s


def _find_price_col(ctx: Context) -> str | None:
    used = {m.col for m in ctx.spec.all_metrics().values()} | {m.denom_col for m in ctx.spec.all_metrics().values()}
    cands = [c for c in ctx.df.columns
             if c not in used and c != ctx.spec.date_col and sc.HINT_PRICE_ROLE.search(c)
             and pd.api.types.is_numeric_dtype(ctx.df[c])]
    return sorted(cands, key=len)[0] if cands else None


def _aux_series(ctx: Context, entry: AuxEntry, segment: dict | None,
                start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Series, str]:
    """보조 테이블의 일별 값. 세그먼트 조건 중 보조 테이블에 있는 차원만 필터로 적용."""
    a, s = entry.df, entry.spec
    ts = _ns(sc.parse_dates(a[s.date_col]).dt.normalize())
    mask = (ts >= start) & (ts <= end)
    seg = segment or {}
    applied, ignored = [], []
    for k, dim in s.key_map.items():
        if dim in seg:
            mask &= a[k].astype(str) == str(seg[dim])
            applied.append(f"{k}={seg[dim]}")
    ignored = [d for d in seg if d not in s.key_map.values()]
    vals = pd.to_numeric(a.loc[mask, s.value_col], errors="coerce")
    g = vals.groupby(ts[mask])
    ser = g.sum(min_count=1) if s.role == "inventory" else g.mean()
    ser.index = _ns(ser.index)
    ser = ser.reindex(_ns(pd.date_range(start, end, freq="D")))
    scope = ", ".join(applied) if applied else "전체"
    if ignored:
        scope += f" (조건 {ignored} 는 보조 테이블에 없어 미적용)"
    return ser, scope


def _signal_series(ctx: Context, role: str, segment: dict | None, start, end) -> tuple[pd.Series | None, str, str]:
    """role('inventory'|'price') 신호의 일별 시리즈, 범위 설명, 출처 설명. 없으면 (None, why, '')."""
    entry = ctx.aux_by_role(role)
    if entry is not None:
        ser, scope = _aux_series(ctx, entry, segment, start, end)
        return ser, scope, f"보조 테이블 '{entry.name}'.{entry.spec.value_col}"
    if role == "price":
        pc = _find_price_col(ctx)
        if pc is not None:
            sub = _apply_filters(ctx.df, segment)
            ts = ctx.ts.loc[sub.index]
            m = (ts >= start) & (ts <= end)
            ser = pd.to_numeric(sub.loc[m, pc], errors="coerce").groupby(ts[m]).mean()
            ser.index = _ns(ser.index)
            ser = ser.reindex(_ns(pd.date_range(start, end, freq="D")))
            return ser, ", ".join(f"{k}={v}" for k, v in (segment or {}).items()) or "전체", f"주 테이블.{pc}"
    return None, f"'{role}' 역할의 보조 테이블(또는 컬럼)이 없습니다", ""


def _ref_level(s: pd.Series, before: pd.Timestamp, lookback: int) -> tuple[float | None, int]:
    """사건 직전 lookback 일의 중앙값(정상 수준)과 유효 일수."""
    w = s[(s.index < before) & (s.index >= before - pd.Timedelta(days=lookback))].dropna()
    return (float(w.median()) if len(w) >= 5 else None), len(w)


def _run_bounds(mask: pd.Series, lo: pd.Timestamp, hi: pd.Timestamp):
    """[lo, hi] 안에서 처음 True 가 되는 일자를 찾고, 그 연속 True 구간의 (시작, 끝)을 반환."""
    inside = mask[(mask.index >= lo) & (mask.index <= hi)]
    hits = inside[inside]
    if hits.empty:
        return None
    pos = mask.index.get_loc(hits.index[0])
    a = b = pos
    while a > 0 and bool(mask.iloc[a - 1]):
        a -= 1
    while b < len(mask) - 1 and bool(mask.iloc[b + 1]):
        b += 1
    return mask.index[a], mask.index[b]


def _shift_mask(s: pd.Series, ref: float, level: float) -> pd.Series:
    """ref → level 로의 변화 중간 지점을 넘은 날(방향 포함)."""
    thr = ref + 0.5 * (level - ref)
    return (s < thr) if level < ref else (s > thr)


def _signal_onset(ctx: Context, role: str, s: pd.Series, ref: float, low_ratio: float):
    """보조 신호의 변화 시작일. 재고=저재고 구간 시작, 그 외=수준 이동 구간 시작.
    반환: (run_bounds | None, 설명 dict)"""
    inc = ctx.incident
    inc_mean = float(s[(s.index >= inc.start) & (s.index <= inc.end)].mean())
    if role == "inventory":
        thr = low_ratio * ref
        return _run_bounds(s <= thr, inc.start, inc.end), {"threshold": thr}
    if abs(_pct(ref, inc_mean)) < 0.5:          # 사건 기간 평균이 정상 수준과 사실상 같음
        return None, {"threshold": None}
    mask = _shift_mask(s, ref, inc_mean)
    return _run_bounds(mask, inc.start, inc.end), {"threshold": ref + 0.5 * (inc_mean - ref)}


# ───────────────────────── 도구 1: 기간 비교 ─────────────────────────
def compare_periods(ctx: Context, metric: str | None = None, filters: dict | None = None) -> ToolResult:
    """기준 vs 사건의 지표 값과 변화율. sum/count 는 합계, ratio 는 합산 비율, mean 은 일별 평균의 평균."""
    args = {"metric": metric, "filters": filters}
    m = _metric(ctx, metric)
    b = _agg_period(ctx, m.name, ctx.baseline, [], filters)
    i = _agg_period(ctx, m.name, ctx.incident, [], filters)
    if b.empty or i.empty:
        return _error("compare_periods", args, "해당 조건의 데이터가 기준 또는 사건 구간에 없습니다")
    vb, vi = float(b["value"].iloc[0]), float(i["value"].iloc[0])
    data = {"metric": m.name, "kind": m.kind, "baseline": vb, "incident": vi,
            "change": vi - vb, "change_pct": _pct(vb, vi),
            "baseline_days": ctx.baseline.days, "incident_days": ctx.incident.days}
    notes = []
    if m.kind in ("sum", "count") and ctx.baseline.days != ctx.incident.days:
        per_b, per_i = vb / ctx.baseline.days, vi / ctx.incident.days
        data.update(baseline_per_day=per_b, incident_per_day=per_i, change_pct_per_day=_pct(per_b, per_i))
        notes.append("기간 길이가 달라 일평균 기준 변화율을 change_pct_per_day 로 함께 제공")
    if m.kind == "ratio":
        data["change_pp"] = (vi - vb) * 100
    word = "감소" if data["change"] < 0 else "증가"
    where = f" [{', '.join(f'{k}={v}' for k, v in (filters or {}).items())}]" if filters else ""
    summary = (f"{m.name}{where}: 기준 {_fmt(vb if m.kind != 'ratio' else vb * 100, 2)}"
               f"{'%' if m.kind == 'ratio' else ''} → 사건 {_fmt(vi if m.kind != 'ratio' else vi * 100, 2)}"
               f"{'%' if m.kind == 'ratio' else ''} ({abs(data['change_pct']):.1f}% {word})")
    return ToolResult("compare_periods", "ok", args, data, None, summary,
                      _desc_query(ctx, m.name, [], filters), notes)


# ───────────────────────── 도구 2: 차원별 기여도 ─────────────────────────
def _contrib_table(ctx: Context, metric: str, by: list[str], filters: dict | None) -> pd.DataFrame:
    m = _metric(ctx, metric)
    if m.kind not in ("sum", "count"):
        raise ValueError(f"지표 '{m.name}'({m.kind}) 는 합산 가능한 지표가 아니라 기여도 분해를 지원하지 않습니다")
    for c in by:
        if c not in ctx.df.columns:
            raise ValueError(f"차원 컬럼 '{c}' 가 데이터에 없습니다")
    b = _agg_period(ctx, m.name, ctx.baseline, by, filters)[[*by, "value"]].rename(columns={"value": "baseline"})
    i = _agg_period(ctx, m.name, ctx.incident, by, filters)[[*by, "value"]].rename(columns={"value": "incident"})
    t = b.merge(i, on=by, how="outer").fillna({"baseline": 0.0, "incident": 0.0})
    t["baseline"] = t["baseline"].astype(float)
    t["incident"] = t["incident"].astype(float)
    t["change"] = t["incident"] - t["baseline"]
    total_change, total_base = float(t["change"].sum()), float(t["baseline"].sum())
    t["change_pct"] = [_pct(x, y) for x, y in zip(t["baseline"], t["incident"])]
    t["contribution_pct"] = t["change"] / total_change * 100 if abs(total_change) > EPS else np.nan
    t["baseline_share_pct"] = t["baseline"] / total_base * 100 if abs(total_base) > EPS else np.nan
    t["lift"] = np.where(t["baseline_share_pct"] > EPS, t["contribution_pct"] / t["baseline_share_pct"], np.nan)
    t["excess_pp"] = t["contribution_pct"] - t["baseline_share_pct"]
    t["segment"] = t[by].astype(str).agg(" × ".join, axis=1)
    t["dim"] = " × ".join(by)
    sign = -1.0 if total_change < 0 else 1.0           # 총 변화 방향으로 가장 크게 기여한 순
    t = t.assign(_k=t["change"] * sign).sort_values("_k", ascending=False).drop(columns="_k")
    return t.reset_index(drop=True)


def contribution_by_dim(ctx: Context, by: str | list[str], metric: str | None = None,
                        filters: dict | None = None) -> ToolResult:
    """차원(들)별 변화량과 총 변화에 대한 기여도(%).

    contribution_pct = 세그먼트 변화 / 전체 변화 ×100 (총 변화 방향으로 정렬)
    lift = 기여도 / 기준 비중 (1이면 비중대로 줄어든 것, 클수록 한쪽에 몰림)
    """
    by = _as_list(by)
    args = {"by": by, "metric": metric, "filters": filters}
    if not by:
        return _error("contribution_by_dim", args, "by(차원)를 지정해야 합니다")
    m = _metric(ctx, metric)
    t = _contrib_table(ctx, m.name, by, filters)
    if t.empty:
        return _error("contribution_by_dim", args, "해당 조건의 데이터가 없습니다")
    total_change = float(t["change"].sum())
    if abs(total_change) <= EPS:
        return ToolResult("contribution_by_dim", "ok", args,
                          {"metric": m.name, "total_change": 0.0, "top": None}, t,
                          "전체 변화가 0이라 기여도를 정의할 수 없습니다",
                          _desc_query(ctx, m.name, by, filters), ["총 변화=0"])
    top = t.iloc[0]
    top2 = float(t["contribution_pct"].head(2).sum())
    data = {"metric": m.name, "by": by, "total_baseline": float(t["baseline"].sum()),
            "total_incident": float(t["incident"].sum()), "total_change": total_change,
            "total_change_pct": _pct(float(t["baseline"].sum()), float(t["incident"].sum())),
            "n_segments": len(t),
            "top_segment": top["segment"], "top_contribution_pct": float(top["contribution_pct"]),
            "top_baseline_share_pct": float(top["baseline_share_pct"]), "top_lift": float(top["lift"]),
            "top_change_pct": float(top["change_pct"]), "top2_contribution_pct": top2}
    data["unstable"] = abs(data["total_change_pct"]) < MIN_TOTAL_CHANGE_PCT
    notes = ([f"전체 변화율이 {abs(data['total_change_pct']):.1f}% 로 작아 기여도(%)가 불안정합니다 — 사건으로 보기 어렵거나 구간 지정을 확인하세요"]
             if data["unstable"] else [])
    where = f" [{', '.join(f'{k}={v}' for k, v in (filters or {}).items())}]" if filters else ""
    summary = (f"{m.name}{where} 총 {abs(data['total_change_pct']):.1f}% "
               f"{'감소' if total_change < 0 else '증가'}: {' × '.join(by)}={top['segment']} 의 기여도 "
               f"{top['contribution_pct']:.1f}% (기준 비중 {top['baseline_share_pct']:.1f}%, {top['lift']:.1f}배)")
    cols = [*by, "segment", "baseline", "incident", "change", "change_pct",
            "contribution_pct", "baseline_share_pct", "lift", "excess_pp"]
    return ToolResult("contribution_by_dim", "ok", args, data, t[cols], summary,
                      _desc_query(ctx, m.name, by, filters), notes)


# ───────────────────────── 도구 3: 차원 순위 ─────────────────────────
def rank_dimensions(ctx: Context, metric: str | None = None, filters: dict | None = None,
                    dims: list[str] | None = None) -> ToolResult:
    """각 차원에서 '기준 비중 대비 가장 많이 몰린' 세그먼트를 찾아 차원별 순위를 매긴다.

    excess_pp = 기여도 − 기준 비중 (%p). 모든 세그먼트가 비중대로 줄었으면 0 근처,
    특정 세그먼트가 몰려서 줄었으면 크다. 하위 탐색은 filters 로 상위 세그먼트를 고정해 재호출.
    """
    args = {"metric": metric, "filters": filters, "dims": dims}
    m = _metric(ctx, metric)
    cand = [d for d in (dims or ctx.spec.dim_cols) if d not in (filters or {})]
    if not cand:
        return _unavailable("rank_dimensions", args, "탐색할 차원 컬럼이 없습니다")
    rows, notes = [], []
    for d in cand:
        t = _contrib_table(ctx, m.name, [d], filters)
        tb, ti = float(t["baseline"].sum()), float(t["incident"].sum())
        if t.empty or abs(_pct(tb, ti)) < MIN_TOTAL_CHANGE_PCT:
            return _unavailable("rank_dimensions", args,
                                f"전체 변화율이 {abs(_pct(tb, ti)):.1f}% 로 작아 기여도 분해가 불안정합니다 "
                                f"(기준 {MIN_TOTAL_CHANGE_PCT:g}% 미만)")
        if len(t) < 2 or t["contribution_pct"].isna().all():
            notes.append(f"차원 '{d}' 는 세그먼트가 2개 미만이거나 총 변화가 0이라 제외")
            continue
        r = t.sort_values("excess_pp", ascending=False).iloc[0]
        rows.append({"dim": d, "n_segments": len(t), "top_segment": r["segment"],
                     "top_contribution_pct": float(r["contribution_pct"]),
                     "top_baseline_share_pct": float(r["baseline_share_pct"]),
                     "excess_pp": float(r["excess_pp"]), "lift": float(r["lift"]),
                     "top_change_pct": float(r["change_pct"])})
    if not rows:
        return _unavailable("rank_dimensions", args, "; ".join(notes) or "분해 가능한 차원이 없습니다")
    t = pd.DataFrame(rows).sort_values("excess_pp", ascending=False).reset_index(drop=True)
    b = t.iloc[0]
    data = {"metric": m.name, "best_dim": b["dim"], "best_segment": b["top_segment"],
            "best_contribution_pct": float(b["top_contribution_pct"]),
            "best_baseline_share_pct": float(b["top_baseline_share_pct"]),
            "best_excess_pp": float(b["excess_pp"]), "best_lift": float(b["lift"]),
            "ranking": t[["dim", "top_segment", "excess_pp"]].to_dict("records")}
    summary = (f"변화가 가장 한쪽에 몰린 차원: {b['dim']} (세그먼트 {b['top_segment']}, 기여도 "
               f"{b['top_contribution_pct']:.1f}%, 기준 비중 {b['top_baseline_share_pct']:.1f}% 대비 "
               f"+{b['excess_pp']:.1f}%p)")
    return ToolResult("rank_dimensions", "ok", args, data, t, summary,
                      _desc_query(ctx, m.name, cand, filters, " (차원별 개별 집계)"), notes)


# ───────────────────────── 도구 4: 비율 지표 ─────────────────────────
def check_ratio_metric(ctx: Context, metric: str | None = None, by: str | list[str] | None = None,
                       filters: dict | None = None) -> ToolResult:
    """비율 지표(예: 취소율)의 기준 vs 사건 변화. 합산 분자/합산 분모 기준(일별 비율의 평균 아님).

    by 가 없으면 모든 차원을 각각 분해한다. num_contribution_pct 는 분자(예: 취소 수량) 증가분 중 세그먼트 몫.
    """
    args = {"metric": metric, "by": by, "filters": filters}
    ratios = [x for x in ctx.spec.extra_metrics if x.kind == "ratio"]
    if metric is not None:
        m = _metric(ctx, metric)
        if m.kind != "ratio":
            return _error("check_ratio_metric", args, f"'{metric}' 는 비율 지표가 아닙니다")
    elif ratios:
        m = ratios[0]
    elif ctx.spec.metric.kind == "ratio":
        m = ctx.spec.metric
    else:
        return _unavailable("check_ratio_metric", args, "비율 지표(예: 취소/환불 컬럼 ÷ 전체 컬럼)가 데이터에 없습니다")

    ob = _agg_period(ctx, m.name, ctx.baseline, [], filters)
    oi = _agg_period(ctx, m.name, ctx.incident, [], filters)
    if ob.empty or oi.empty:
        return _error("check_ratio_metric", args, "해당 조건의 데이터가 없습니다")
    rb, ri = float(ob["value"].iloc[0]) * 100, float(oi["value"].iloc[0]) * 100
    dims = _as_list(by) or [d for d in ctx.spec.dim_cols if d not in (filters or {})]
    frames = []
    for d in dims:
        b = _agg_period(ctx, m.name, ctx.baseline, [d], filters).rename(
            columns={"value": "baseline_rate", "num": "num_b", "den": "den_b"})
        i = _agg_period(ctx, m.name, ctx.incident, [d], filters).rename(
            columns={"value": "incident_rate", "num": "num_i", "den": "den_i"})
        t = b.merge(i, on=d, how="outer").fillna({"num_b": 0, "num_i": 0, "den_b": 0, "den_i": 0})
        t["segment"] = t[d].astype(str)
        t["dim"] = d
        t["baseline_rate_pct"] = t["baseline_rate"] * 100
        t["incident_rate_pct"] = t["incident_rate"] * 100
        t["change_pp"] = t["incident_rate_pct"] - t["baseline_rate_pct"]
        t["num_change"] = t["num_i"] - t["num_b"]
        tot = float(t["num_change"].sum())
        t["num_contribution_pct"] = t["num_change"] / tot * 100 if abs(tot) > EPS else np.nan
        t["den_change_pct"] = [_pct(x, y) for x, y in zip(t["den_b"], t["den_i"])]
        frames.append(t[["dim", "segment", "baseline_rate_pct", "incident_rate_pct", "change_pp",
                         "num_change", "num_contribution_pct", "den_b", "den_i", "den_change_pct"]]
                      .rename(columns={"den_b": "den_baseline", "den_i": "den_incident"}))
    table = pd.concat(frames, ignore_index=True) if frames else None
    data = {"metric": m.name, "baseline_rate_pct": rb, "incident_rate_pct": ri, "change_pp": ri - rb,
            "numerator": m.col, "denominator": m.denom_col}
    notes = []
    summary = f"{m.name}: 기준 {rb:.2f}% → 사건 {ri:.2f}% ({ri - rb:+.2f}%p)"
    if table is not None and len(table):
        valid = table[table["den_incident"] > 0].sort_values("change_pp", ascending=False)
        if len(valid):
            top = valid.iloc[0]
            data.update(top_dim=top["dim"], top_segment=top["segment"],
                        top_baseline_rate_pct=float(top["baseline_rate_pct"]),
                        top_incident_rate_pct=float(top["incident_rate_pct"]),
                        top_change_pp=float(top["change_pp"]))
            summary += (f" / 상승폭 최대: {top['dim']}={top['segment']} "
                        f"({top['baseline_rate_pct']:.1f}% → {top['incident_rate_pct']:.1f}%)")
    else:
        notes.append("분해할 차원이 없어 전체 값만 제공")
    q = _desc_query(ctx, m.name, dims, filters)
    return ToolResult("check_ratio_metric", "ok", args, data, table, summary, q, notes)


# ───────────────────────── 도구 5: 재고 ─────────────────────────
def check_inventory(ctx: Context, segment: dict | None = None, low_ratio: float = LOW_STOCK_RATIO,
                    lookback_days: int = LOOKBACK_DAYS) -> ToolResult:
    """보조 테이블의 재고가 사건 구간에 고갈(저재고·품절)됐는지.

    정상 수준 = 사건 직전 lookback_days 의 중앙값. 저재고 = 정상 수준 × low_ratio 이하.
    segment 예: {"category": "A"} (보조 테이블에 있는 차원만 필터로 적용).
    """
    args = {"segment": segment, "low_ratio": low_ratio, "lookback_days": lookback_days}
    inc = ctx.incident
    lo, hi = inc.start - pd.Timedelta(days=lookback_days), inc.end + pd.Timedelta(days=POST_DAYS)
    s, scope, source = _signal_series(ctx, "inventory", segment, lo, hi)
    if s is None:
        return _unavailable("check_inventory", args, scope)
    ref, n_ref = _ref_level(s, inc.start, lookback_days)
    inc_s = s[(s.index >= inc.start) & (s.index <= inc.end)]
    if ref is None or inc_s.dropna().empty:
        return _unavailable("check_inventory", args, f"재고 데이터가 부족합니다 (범위: {scope}, 정상 수준 산정 {n_ref}일)")
    base_s = s[(s.index >= ctx.baseline.start) & (s.index <= ctx.baseline.end)]
    thr = low_ratio * ref
    b_mean, i_mean = float(base_s.mean()), float(inc_s.mean())
    zero_days = int((inc_s <= 0).sum())
    low_days = int((inc_s <= thr).sum())
    run = _run_bounds(s <= thr, inc.start, inc.end)
    data = {"source": source, "scope": scope, "normal_level": ref, "low_threshold": thr,
            "baseline_mean": b_mean, "incident_mean": i_mean, "change_pct": _pct(b_mean, i_mean),
            "incident_min": float(inc_s.min()), "zero_days": zero_days, "low_days": low_days,
            "incident_days_with_data": int(inc_s.notna().sum()), "incident_days": inc.days,
            "low_run_start": None, "low_run_end": None, "lead_days": None, "recovery_date": None}
    if run is not None:
        start, end = run
        data.update(low_run_start=start, low_run_end=end, lead_days=(inc.start - start).days)
        nxt = end + pd.Timedelta(days=1)
        if nxt in s.index and not math.isnan(s.loc[nxt]) and s.loc[nxt] > thr:
            data["recovery_date"] = nxt
    where = f"[{scope}] " if scope != "전체" else ""
    if run is None:
        summary = (f"{where}재고: 사건 기간 평균 {_fmt(i_mean, 0)} (정상 {_fmt(ref, 0)}), "
                   f"저재고(≤{_fmt(thr, 0)}) 일자 없음")
    else:
        summary = (f"{where}재고: 사건 {inc.days}일 중 품절 {zero_days}일·저재고 {low_days}일, "
                   f"기준 구간 대비 {data['change_pct']:+.1f}% (평균 {_fmt(b_mean, 0)} → {_fmt(i_mean, 0)}); "
                   f"저재고 시작 {run[0]:%Y-%m-%d}"
                   + (f" (사건 {data['lead_days']}일 전)" if data["lead_days"] > 0 else " (사건 시작일)")
                   + (f", 회복 {data['recovery_date']:%Y-%m-%d}" if data["recovery_date"] is not None else ""))
    t = pd.DataFrame({"date": s.index, "value": s.to_numpy()})
    t["period"] = np.where((t["date"] >= inc.start) & (t["date"] <= inc.end), "incident",
                           np.where((t["date"] >= ctx.baseline.start) & (t["date"] <= ctx.baseline.end),
                                    "baseline", ""))
    t["is_low"] = t["value"] <= thr
    q = (f"{source} (범위: {scope}) 일별 값; 정상 수준=사건 직전 {lookback_days}일 중앙값, "
         f"저재고=정상×{low_ratio}")
    return ToolResult("check_inventory", "ok", args, data, t, summary, q)


# ───────────────────────── 도구 6: 가격 변동 ─────────────────────────
def check_price_change(ctx: Context, by: str | list[str] | None = None, metric: str | None = None,
                       min_change_pct: float = PRICE_CHANGE_PCT) -> ToolResult:
    """차원별 평균 가격의 기준 vs 사건 변화와, 같은 세그먼트의 지표 변화 기여도.

    가격 출처: 주 테이블의 가격 컬럼(이름 힌트) → 없으면 보조 테이블의 가격 역할 컬럼.
    평균은 행 단위 단순 평균이라 구성비 변화 영향이 약간 있으며, min_change_pct 미만은 변동 없음으로 본다.
    """
    args = {"by": by, "metric": metric, "min_change_pct": min_change_pct}
    m = _metric(ctx, metric)
    pc = _find_price_col(ctx)
    if pc is not None:
        frame = ctx.df[[c for c in ctx.spec.dim_cols if c in ctx.df.columns]].copy()
        frame["_p"] = pd.to_numeric(ctx.df[pc], errors="coerce")
        frame["_ts"] = ctx.ts
        source = f"주 테이블.{pc}"
    else:
        entry = ctx.aux_by_role("price")
        if entry is None:
            return _unavailable("check_price_change", args, "가격 컬럼(주 테이블) 또는 가격 보조 테이블이 없습니다")
        a, s = entry.df, entry.spec
        frame = pd.DataFrame({dim: a[k] for k, dim in s.key_map.items()})
        frame["_p"] = pd.to_numeric(a[s.value_col], errors="coerce")
        frame["_ts"] = _ns(sc.parse_dates(a[s.date_col]).dt.normalize())
        source = f"보조 테이블 '{entry.name}'.{s.value_col}"
    avail = [c for c in frame.columns if c not in ("_p", "_ts")]
    dims = [d for d in (_as_list(by) or avail) if d in avail]
    if not dims:
        return _unavailable("check_price_change", args, "가격과 연결되는 차원이 없습니다")

    additive = m.kind in ("sum", "count")
    rows = []
    for d in dims:
        def per(p: Period):
            f = frame[(frame["_ts"] >= p.start) & (frame["_ts"] <= p.end)]
            return f.groupby(f[d].astype(object).where(f[d].notna(), "(결측)"))["_p"].mean()
        pb, pi = per(ctx.baseline), per(ctx.incident)
        ct = _contrib_table(ctx, m.name, [d], None).set_index(d) if additive else None
        for seg in sorted(set(pb.index) | set(pi.index), key=str):
            vb, vi = pb.get(seg, np.nan), pi.get(seg, np.nan)
            chg = _pct(vb, vi) if not (pd.isna(vb) or pd.isna(vi)) else float("nan")
            row = {"dim": d, "segment": str(seg), "price_baseline": vb, "price_incident": vi,
                   "price_change_pct": chg, "changed": bool(abs(chg) >= min_change_pct) if not math.isnan(chg) else False}
            if ct is not None and seg in ct.index:
                row.update(contribution_pct=float(ct.loc[seg, "contribution_pct"]),
                           metric_change_pct=float(ct.loc[seg, "change_pct"]))
            else:
                row.update(contribution_pct=float("nan"), metric_change_pct=float("nan"))
            rows.append(row)
    t = pd.DataFrame(rows).sort_values("price_change_pct", key=lambda x: x.abs(), ascending=False,
                                       na_position="last").reset_index(drop=True)
    changed = t[t["changed"]]
    data = {"source": source, "price_col": pc, "min_change_pct": min_change_pct,
            "n_changed": len(changed),
            "changed_segments": changed[["dim", "segment", "price_change_pct", "contribution_pct"]].to_dict("records")}
    if len(changed):
        summary = "가격 변동 세그먼트: " + "; ".join(
            f"{r['dim']}={r['segment']} {r['price_change_pct']:+.1f}% (지표 기여도 {r['contribution_pct']:.1f}%)"
            if not math.isnan(r["contribution_pct"]) else f"{r['dim']}={r['segment']} {r['price_change_pct']:+.1f}%"
            for _, r in changed.iterrows())
    else:
        summary = f"가격 변동 ±{min_change_pct:g}% 이상인 세그먼트 없음"
    q = (f"AVG({pc or 'price'}) [{source}] 기준[{ctx.baseline.label}] vs 사건[{ctx.incident.label}] "
         f"GROUP BY {', '.join(dims)}")
    return ToolResult("check_price_change", "ok", args, data, t, summary, q)


# ───────────────────────── 도구 7: 시점 선후 ─────────────────────────
def check_timing(ctx: Context, segment: dict | None = None, signal: str = "inventory",
                 metric: str | None = None, low_ratio: float = LOW_STOCK_RATIO,
                 lookback_days: int = LOOKBACK_DAYS, min_shift_pct: float = MIN_SHIFT_PCT) -> ToolResult:
    """세그먼트 지표가 꺾인 날과 보조 신호(재고 고갈·가격 변동)가 시작된 날을 비교.

    lead_days = 지표 변화일 − 신호 변화일. 양수=신호가 먼저(선행), 0=동시, 음수=신호가 나중(후행).
    지표 변화일 = 정상 수준(직전 중앙값)과 사건 평균의 중간 지점을 넘은 구간의 시작일.
    """
    args = {"segment": segment, "signal": signal, "metric": metric, "low_ratio": low_ratio}
    if signal not in ("inventory", "price"):
        return _error("check_timing", args, "signal 은 'inventory' 또는 'price'")
    m = _metric(ctx, metric)
    if m.kind not in ("sum", "count", "mean"):
        return _error("check_timing", args, "시점 비교는 sum/count/mean 지표만 지원합니다")
    inc = ctx.incident
    lo, hi = inc.start - pd.Timedelta(days=lookback_days), inc.end + pd.Timedelta(days=POST_DAYS)

    sig, scope, source = _signal_series(ctx, signal, segment, lo, hi)
    if sig is None:
        return _unavailable("check_timing", args, scope)
    sref, _ = _ref_level(sig, inc.start, lookback_days)
    if sref is None or sig[(sig.index >= inc.start) & (sig.index <= inc.end)].dropna().empty:
        return _unavailable("check_timing", args, f"{signal} 데이터가 부족합니다 (범위: {scope})")
    run, _ = _signal_onset(ctx, signal, sig, sref, low_ratio)

    ms = _daily_series(ctx, m.name, lo, inc.end, segment)
    mref, _ = _ref_level(ms, inc.start, lookback_days)
    notes: list[str] = []
    m_onset, m_change = None, float("nan")
    if mref is not None and abs(mref) > EPS:
        m_inc = float(ms[(ms.index >= inc.start)].mean())
        m_change = _pct(mref, m_inc)
        if abs(m_change) >= min_shift_pct:
            mrun = _run_bounds(_shift_mask(ms, mref, m_inc), inc.start, inc.end)
            m_onset = mrun[0] if mrun else None
        else:
            notes.append(f"지표 변화가 {min_shift_pct:g}% 미만({m_change:+.1f}%)이라 변화 시점을 판정하지 않음")
    else:
        notes.append("지표의 정상 수준을 산정할 수 없음")
    s_onset = run[0] if run else None
    lead, relation = None, "판정불가"
    if m_onset is not None and s_onset is not None:
        lead = int((m_onset - s_onset).days)
        relation = "선행" if lead > 0 else "동시" if lead == 0 else "후행"
    elif s_onset is None:
        notes.append("보조 신호에서 사건 구간 내 변화 시작을 찾지 못함")
    data = {"segment": segment, "signal": signal, "scope": scope, "source": source,
            "metric": m.name, "metric_change_pct": m_change,
            "metric_onset": m_onset, "signal_onset": s_onset,
            "lead_days": lead, "relation": relation}
    seg_txt = ", ".join(f"{k}={v}" for k, v in (segment or {}).items()) or "전체"
    if lead is None:
        summary = f"[{seg_txt}] 시점 비교 판정불가 — " + (notes[-1] if notes else "")
    else:
        summary = (f"[{seg_txt}] {signal} 변화 {s_onset:%Y-%m-%d} → {m.name} 변화 {m_onset:%Y-%m-%d}: "
                   f"신호가 {abs(lead)}일 {relation}" if lead != 0 else
                   f"[{seg_txt}] {signal} 변화와 {m.name} 변화가 같은 날({m_onset:%Y-%m-%d})")
    t = pd.DataFrame({"date": ms.index, "metric": ms.to_numpy()}).merge(
        pd.DataFrame({"date": sig.index, "signal": sig.to_numpy()}), on="date", how="outer").sort_values("date")
    q = (f"{m.name} 일별 시리즈 vs {source} 일별 시리즈 (범위: {scope}); 변화일=정상 수준→사건 평균 중간 지점 통과 구간 시작")
    return ToolResult("check_timing", "ok", args, data, t.reset_index(drop=True), summary, q, notes)


# ───────────────────────── 도구 8: 데이터 품질 ─────────────────────────
def check_data_quality(ctx: Context) -> ToolResult:
    """데이터 오류(수집 누락·중복·음수·결측)가 사건처럼 보이게 만든 것은 아닌지 점검."""
    args: dict = {}
    d, ts, spec = ctx.df, ctx.ts, ctx.spec
    periods = {"baseline": ctx.baseline, "incident": ctx.incident}
    sub = {k: d[(ts >= p.start) & (ts <= p.end)] for k, p in periods.items()}
    issues: list[str] = []
    rows_b, rows_i = len(sub["baseline"]), len(sub["incident"])
    row_change = _pct(rows_b, rows_i)
    if row_change <= -DQ_ROW_DROP_PCT:
        issues.append(f"행 수 {abs(row_change):.0f}% 감소 ({rows_b:,} → {rows_i:,}): 수집 누락 가능")

    span = _ns(pd.date_range(ctx.baseline.start, ctx.incident.end, freq="D"))
    present = set(ts[(ts >= ctx.baseline.start) & (ts <= ctx.incident.end)].unique())
    missing = [x for x in span if x not in present]
    if missing:
        issues.append(f"데이터가 없는 날짜 {len(missing)}일: " + ", ".join(f"{x:%m-%d}" for x in missing[:5])
                      + (" …" if len(missing) > 5 else ""))

    dup = {k: float(v.duplicated().mean() * 100) if len(v) else 0.0 for k, v in sub.items()}
    if dup["incident"] >= DQ_DUP_RATE_PCT and dup["incident"] > dup["baseline"] + EPS:
        issues.append(f"사건 구간 완전 중복 행 {dup['incident']:.1f}% (기준 {dup['baseline']:.1f}%)")

    metric_cols = sorted({c for m in spec.all_metrics().values() for c in (m.col, m.denom_col) if c})
    neg = {c: int((pd.to_numeric(d[c], errors="coerce") < 0).sum()) for c in metric_cols}
    neg = {c: n for c, n in neg.items() if n > 0}
    if neg:
        issues.append("음수 값: " + ", ".join(f"{c} {n}행" for c, n in neg.items()))

    null_rows = []
    for c in dict.fromkeys([*metric_cols, *spec.dim_cols]):
        nb = float(sub["baseline"][c].isna().mean() * 100) if rows_b else 0.0
        ni = float(sub["incident"][c].isna().mean() * 100) if rows_i else 0.0
        null_rows.append({"column": c, "baseline_null_pct": nb, "incident_null_pct": ni})
        if ni - nb >= DQ_NULL_RISE_PP:
            issues.append(f"'{c}' 결측률 상승 {nb:.1f}% → {ni:.1f}%")

    unparsed = float(sc.parse_dates(d[spec.date_col]).isna().mean() * 100)
    if unparsed > 0:
        issues.append(f"날짜로 해석되지 않는 행 {unparsed:.1f}%")

    data = {"rows_baseline": rows_b, "rows_incident": rows_i, "row_change_pct": row_change,
            "missing_dates": [f"{x:%Y-%m-%d}" for x in missing],
            "dup_rate_baseline_pct": dup["baseline"], "dup_rate_incident_pct": dup["incident"],
            "negative_values": neg, "unparsed_date_pct": unparsed,
            "issues": issues, "n_issues": len(issues), "passed": not issues}
    summary = ("데이터 품질 이상 없음 (행 수 " f"{rows_b:,} → {rows_i:,}, 누락 날짜 0, 중복·음수·결측 이상 없음)"
               if not issues else "데이터 품질 이슈 " + f"{len(issues)}건: " + "; ".join(issues))
    q = "행 수·날짜 연속성·중복·음수·결측률을 기준/사건 구간별로 계산"
    return ToolResult("check_data_quality", "ok", args, data, pd.DataFrame(null_rows), summary, q)


# ───────────────────────── 레지스트리 ─────────────────────────
@dataclass
class ToolDef:
    fn: Callable[..., ToolResult]
    description: str
    params: dict[str, str]
    needs: str = ""             # "" | "dims" | "ratio" | "inventory" | "price"


TOOLS: dict[str, ToolDef] = {
    "compare_periods": ToolDef(compare_periods, "기준 vs 사건 구간의 지표 값·변화율",
                               {"metric": "지표명(선택)", "filters": "{컬럼: 값}(선택)"}),
    "contribution_by_dim": ToolDef(contribution_by_dim, "차원별 변화 기여도(%)와 기준 비중 대비 배수",
                                   {"by": "차원 컬럼(들)", "metric": "선택", "filters": "선택"}, "dims"),
    "rank_dimensions": ToolDef(rank_dimensions, "어느 차원에서 변화가 한쪽에 몰렸는지 순위",
                               {"metric": "선택", "filters": "상위 세그먼트 고정(선택)"}, "dims"),
    "check_ratio_metric": ToolDef(check_ratio_metric, "비율 지표(취소율 등) 변화와 차원별 분해",
                                  {"metric": "선택", "by": "선택", "filters": "선택"}, "ratio"),
    "check_inventory": ToolDef(check_inventory, "재고 고갈 여부·시작일·회복일",
                               {"segment": "{차원: 값}"}, "inventory"),
    "check_price_change": ToolDef(check_price_change, "가격 변동 세그먼트와 지표 기여도",
                                  {"by": "선택", "min_change_pct": "선택"}, "price"),
    "check_timing": ToolDef(check_timing, "지표 변화일 vs 재고/가격 신호 변화일 (선행·동시·후행)",
                            {"segment": "{차원: 값}", "signal": "inventory|price"}, "inventory"),
    "check_data_quality": ToolDef(check_data_quality, "행 수·누락 날짜·중복·음수·결측 점검", {}),
}


def available_tools(ctx: Context) -> dict[str, tuple[bool, str]]:
    """도구별 (실행 가능 여부, 불가 사유)."""
    has_ratio = any(m.kind == "ratio" for m in ctx.spec.all_metrics().values())
    has_price = _find_price_col(ctx) is not None or ctx.aux_by_role("price") is not None
    cond = {"": (True, ""),
            "dims": (bool(ctx.spec.dim_cols), "차원 컬럼 없음"),
            "ratio": (has_ratio, "비율 지표 없음"),
            "inventory": (ctx.aux_by_role("inventory") is not None, "재고 보조 테이블 없음"),
            "price": (has_price, "가격 컬럼/테이블 없음")}
    return {n: cond[t.needs] for n, t in TOOLS.items()}


def run_tool(ctx: Context, name: str, **kwargs) -> ToolResult:
    """이름으로 도구 실행. 알 수 없는 도구·잘못된 인자·계산 오류는 ToolResult(status='error')."""
    if name not in TOOLS:
        return _error(name, kwargs, f"알 수 없는 도구 '{name}'. 사용 가능: {list(TOOLS)}")
    try:
        return TOOLS[name].fn(ctx, **kwargs)
    except (ValueError, KeyError, TypeError) as e:
        return _error(name, kwargs, str(e))


# ───────────────────────── 시연 (실제 데이터) ─────────────────────────
def _demo() -> None:
    root = Path(__file__).resolve().parent.parent
    data = root / "data"
    truth_p = data / "ground_truth.json"
    if not (data / "orders.csv").exists() or not truth_p.exists():
        raise SystemExit("data/orders.csv, data/ground_truth.json 이 필요합니다. 먼저 python src/generate_data.py")
    truth = json.loads(truth_p.read_text(encoding="utf-8"))
    aux = {"inventory": sc.load_table(data / "inventory.csv")} if (data / "inventory.csv").exists() else {}
    ctx = build_context(sc.load_table(data / "orders.csv"), truth["incident"], truth["baseline"], aux)
    print(f"[Context] 사건 {ctx.incident.label} / 기준 {ctx.baseline.label}")
    print(f"[Spec] 날짜={ctx.spec.date_col}, 지표={ctx.spec.metric.name}, 차원={ctx.spec.dim_cols}")
    for n, (ok, why) in available_tools(ctx).items():
        print(f"  - {n}: {'가능' if ok else '불가(' + why + ')'}")
    print()
    top = rank_dimensions(ctx)
    best = top.data["best_segment"] if top.ok else None
    seg = {top.data["best_dim"]: best} if best else None
    calls = [("compare_periods", {}), ("rank_dimensions", {}),
             ("contribution_by_dim", {"by": "category"}), ("contribution_by_dim", {"by": "region"}),
             ("check_ratio_metric", {}), ("check_inventory", {"segment": seg}),
             ("check_timing", {"segment": seg}), ("check_price_change", {}), ("check_data_quality", {})]
    for name, kw in calls:
        if name == "contribution_by_dim" and kw["by"] not in ctx.spec.dim_cols:
            continue
        r = run_tool(ctx, name, **kw)
        print(f"[{r.tool}] ({r.status}) {r.summary}")
        print(f"    출처: {r.query}")


if __name__ == "__main__":
    _demo()
