"""schema.py 테스트 — 합성 데이터가 아니라 generate_data.py 가 만든 실제 파일(data/)을 읽어 검증.

실행: (caselens/ 에서)  python -m pytest tests/test_schema.py -v
선행: python src/generate_data.py  (data/*.csv, ground_truth.json 이 있어야 함)

범용성 검증: 같은 실제 데이터를 컬럼명·날짜형식·인코딩만 바꿔 만든 변형본에서도
코드 수정 없이 동일한 수치가 나오는지 확인한다.
"""
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import schema as sc  # noqa: E402

DATA = ROOT / "data"


# ───────── 실제 데이터 fixture ─────────
@pytest.fixture(scope="module")
def truth():
    return json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def orders():
    return sc.load_table(DATA / "orders.csv")


@pytest.fixture(scope="module")
def inventory():
    return sc.load_table(DATA / "inventory.csv")


def _variant_en(df):
    return df.rename(columns={"date": "order_date", "category": "product_group", "region": "area",
                              "revenue": "sales_amt", "qty": "quantity", "unit_price": "price",
                              "cancel_qty": "cancelled_qty", "cancel_revenue": "cancelled_amt",
                              "net_revenue": "net_sales"})


def _variant_ko(df):
    out = df.rename(columns={"date": "일자", "category": "상품군", "region": "지역", "revenue": "매출액",
                             "qty": "수량", "unit_price": "단가", "cancel_qty": "취소수량",
                             "cancel_revenue": "취소금액", "net_revenue": "순매출액"})
    out["일자"] = pd.to_datetime(out["일자"]).dt.strftime("%Y%m%d").astype(int)   # 20260803 정수형
    return out


# ───────── 1. 추정 ─────────
def test_infer_on_real_orders(orders):
    r = sc.infer_spec(orders)
    s = r.spec
    assert s.date_col == "date"
    assert s.metric.col == "revenue"                         # unit_price·cancel_*·net_revenue 가 아님
    assert set(s.dim_cols) == {"category", "region"}         # qty·price 등 수치 컬럼은 차원 아님
    ratios = {m.name: (m.col, m.denom_col) for m in s.extra_metrics}
    assert ratios["cancel_qty_rate"] == ("cancel_qty", "qty")
    assert ratios["cancel_revenue_rate"] == ("cancel_revenue", "revenue")
    assert sc.validate_spec(orders, s) == []


@pytest.mark.parametrize("make,date,metric,dims,ratio", [
    (_variant_en, "order_date", "sales_amt", {"product_group", "area"}, ("cancelled_qty", "quantity")),
    (_variant_ko, "일자", "매출액", {"상품군", "지역"}, ("취소수량", "수량")),
])
def test_infer_renamed_variants(orders, make, date, metric, dims, ratio):
    v = make(orders)
    r = sc.infer_spec(v)
    assert r.spec.date_col == date
    assert r.spec.metric.col == metric
    assert set(r.spec.dim_cols) == dims
    assert any((m.col, m.denom_col) == ratio for m in r.spec.extra_metrics)
    assert sc.validate_spec(v, r.spec) == []


def test_numeric_columns_are_not_dates(orders):
    r = sc.infer_spec(orders)
    assert all(c in {"date"} for c, _ in r.date_candidates)  # qty/revenue 가 epoch 날짜로 오인되지 않음


# ───────── 2. 집계 수치 = ground_truth ─────────
def _sum(df, spec, start, end, metric=None, by=None):
    a = sc.aggregate_metric(df, spec, metric=metric, by=by, start=start, end=end)
    return a


def test_period_totals_match_ground_truth(orders, truth):
    spec = sc.infer_spec(orders).spec
    b, i = truth["baseline"], truth["incident"]
    base = _sum(orders, spec, b["start_date"], b["end_date"])["value"].sum()
    inc = _sum(orders, spec, i["start_date"], i["end_date"])["value"].sum()
    assert base == truth["actual"]["baseline_revenue"]
    assert inc == truth["actual"]["incident_revenue"]


def test_category_contribution_from_aggregate(orders, truth):
    """집계 함수만으로 A 기여도(ground_truth)를 재현."""
    spec = sc.infer_spec(orders).spec
    b, i = truth["baseline"], truth["incident"]
    base = _sum(orders, spec, b["start_date"], b["end_date"], by="category").groupby("category")["value"].sum()
    inc = _sum(orders, spec, i["start_date"], i["end_date"], by="category").groupby("category")["value"].sum()
    delta = base - inc
    contrib = (delta / delta.sum() * 100).round(2)
    for cat, expected in truth["actual"]["contribution_by_category"].items():
        assert contrib[cat] == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("make", [_variant_en, _variant_ko])
def test_variants_give_identical_numbers(orders, truth, make):
    """컬럼명·날짜형식이 달라도 같은 수치 (범용성 핵심 검증)."""
    v = make(orders)
    spec = sc.infer_spec(v).spec
    b = truth["baseline"]
    total = _sum(v, spec, b["start_date"], b["end_date"])["value"].sum()
    assert total == truth["actual"]["baseline_revenue"]


def test_ratio_metric_matches_ground_truth(orders, truth):
    spec = sc.infer_spec(orders).spec
    b, i = truth["baseline"], truth["incident"]
    for period, key in ((b, "baseline_cancel_rate_pct"), (i, "incident_cancel_rate_pct")):
        a = sc.aggregate_metric(orders, spec, metric="cancel_qty_rate", start=period["start_date"], end=period["end_date"])
        rate = a["num"].sum() / a["den"].sum() * 100          # 일별 비율의 평균이 아니라 합산 비율
        assert rate == pytest.approx(truth["actual"]["cancel_total"][key], abs=0.01)


def test_fill_missing_dates(orders):
    spec = sc.infer_spec(orders).spec
    holey = orders[orders["date"] != "2026-08-10"]            # 하루 제거
    a = sc.aggregate_metric(holey, spec)
    assert len(a) == 90
    assert a.loc[a["date"] == "2026-08-10", "value"].iloc[0] == 0


def test_weekly_aggregation_conserves_total(orders):
    spec = sc.infer_spec(orders).spec
    d = sc.aggregate_metric(orders, spec, freq="D")["value"].sum()
    w = sc.aggregate_metric(orders, spec, freq="W")["value"].sum()
    assert d == w == orders["revenue"].sum()


# ───────── 3. 검증·인코딩 ─────────
def test_validate_catches_bad_spec(orders):
    bad = sc.DataSpec("nope", sc.MetricSpec("x", "sum", "region"), ["category", "ghost"])
    errs = sc.validate_spec(orders, bad)
    assert len(errs) == 3                                     # 날짜 없음, 수치형 아님, 없는 차원
    same = sc.DataSpec("date", sc.MetricSpec("revenue", "sum", "revenue"), ["date"])
    assert any("날짜 컬럼과 같음" in e for e in sc.validate_spec(orders, same))
    with pytest.raises(ValueError):
        sc.aggregate_metric(orders, bad)


def test_cp949_csv_loads(orders):
    buf = io.BytesIO(_variant_ko(orders).to_csv(index=False).encode("cp949"))
    df = sc.load_table(buf)
    assert "상품군" in df.columns and len(df) == len(orders)


# ───────── 4. 보조 테이블 (재고) ─────────
def test_infer_inventory_table(orders, inventory):
    spec = sc.infer_spec(orders).spec
    aux = sc.infer_aux_table(inventory, orders, spec)
    assert aux is not None
    assert aux.role == "inventory"
    assert aux.value_col == "stock_qty"
    assert aux.key_map == {"category": "category"}


def test_inventory_join_with_renamed_main_and_aux(orders, inventory):
    """주 테이블 컬럼명이 달라도, 재고 테이블 키가 값 겹침으로 연결된다."""
    main = _variant_en(orders)                                # category → product_group
    aux_df = inventory.rename(columns={"category": "sku_group", "stock_qty": "on_hand"})
    spec = sc.infer_spec(main).spec
    aux = sc.infer_aux_table(aux_df, main, spec)
    assert aux.role == "inventory"
    assert aux.key_map == {"sku_group": "product_group"}


def test_unrelated_table_returns_none(orders):
    other = pd.DataFrame({"date": ["2026-08-03"], "weather": ["rain"], "temp": [20.5]})
    assert sc.infer_aux_table(other, orders, sc.infer_spec(orders).spec) is None
