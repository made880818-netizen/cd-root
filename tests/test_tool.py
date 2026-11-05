"""tools.py 테스트 — 합성 데이터가 아니라 generate_data.py 가 만든 실제 파일(data/)로 검증.

실행: (caselens/ 에서)  python -m pytest tests/test_tools.py -v
선행: python src/generate_data.py  (data/orders.csv, inventory.csv, ground_truth.json)

검증 방식
  1) 도구 출력 수치 = ground_truth.json 의 정답 수치
  2) 같은 실제 데이터를 컬럼명·날짜형식만 바꾼 변형본(영문/한글)에서도 동일한 결과 (범용성)
  3) 실제 데이터를 일부러 망가뜨려(행 삭제·중복·음수·결측) 데이터 품질 도구가 잡는지
  4) 정상 구간끼리 비교하는 음성 대조군 — 도구가 없는 사건을 만들어내지 않는지
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import schema as sc  # noqa: E402
import tools as tl  # noqa: E402

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


@pytest.fixture(scope="module")
def ctx(orders, inventory, truth):
    return tl.build_context(orders, truth["incident"], truth["baseline"], {"inventory": inventory})


def _day(truth, n):
    """ground_truth 의 n일차 → Timestamp (사건 시작일 = 61일차)."""
    return pd.Timestamp(truth["incident"]["start_date"]) + pd.Timedelta(days=n - truth["incident"]["start_day"])


def _variant_en(df):
    return df.rename(columns={"date": "order_date", "category": "product_group", "region": "area",
                              "revenue": "sales_amt", "qty": "quantity", "unit_price": "price",
                              "cancel_qty": "cancelled_qty", "cancel_revenue": "cancelled_amt",
                              "net_revenue": "net_sales"})


def _variant_ko(df):
    out = df.rename(columns={"date": "일자", "category": "상품군", "region": "지역", "revenue": "매출액",
                             "qty": "수량", "unit_price": "단가", "cancel_qty": "취소수량",
                             "cancel_revenue": "취소금액", "net_revenue": "순매출액"})
    out["일자"] = pd.to_datetime(out["일자"]).dt.strftime("%Y%m%d").astype(int)
    return out


def _cat_dim(c):
    """'A' 값을 가진 차원 컬럼 (컬럼명이 달라져도 찾기)."""
    return next(d for d in c.spec.dim_cols if "A" in set(c.df[d].astype(str)))


def _region_dim(c):
    return next(d for d in c.spec.dim_cols if d != _cat_dim(c))


# ───────── 1. 컨텍스트 ─────────
def test_context_infers_spec_and_aux(ctx):
    assert ctx.spec.date_col == "date" and ctx.spec.metric.col == "revenue"
    assert set(ctx.spec.dim_cols) == {"category", "region"}
    assert ctx.aux_by_role("inventory") is not None
    assert ctx.incident.days == 7 and ctx.baseline.days == 7


def test_default_baseline_equals_ground_truth_baseline(orders, inventory, truth):
    c = tl.build_context(orders, truth["incident"], aux={"inventory": inventory})   # baseline 생략
    assert c.baseline.label == tl.Period.coerce(truth["baseline"]).label


def test_context_rejects_bad_periods(orders, truth):
    with pytest.raises(ValueError, match="겹칩니다"):
        tl.build_context(orders, truth["incident"], {"start_date": "2026-10-01", "end_date": "2026-10-03"})
    with pytest.raises(ValueError, match="벗어납니다"):
        tl.build_context(orders, {"start_date": "2027-01-01", "end_date": "2027-01-07"})
    with pytest.raises(ValueError, match="이릅니다"):
        tl.Period("2026-10-08", "2026-10-02")


def test_unrelated_aux_table_is_skipped(orders, truth):
    other = pd.DataFrame({"date": ["2026-08-03"], "weather": ["rain"], "temp": [20.5]})
    c = tl.build_context(orders, truth["incident"], truth["baseline"], {"weather": other})
    assert c.aux == {} and any("weather" in n for n in c.notes)


# ───────── 2. 수치 = ground_truth ─────────
def test_compare_periods_matches_truth(ctx, truth):
    r = tl.compare_periods(ctx)
    a = truth["actual"]
    assert r.ok
    assert r.data["baseline"] == a["baseline_revenue"]
    assert r.data["incident"] == a["incident_revenue"]
    assert -r.data["change_pct"] == pytest.approx(a["total_drop_pct"], abs=0.01)
    assert "30.4%" in r.summary and "SUM(revenue)" in r.query


def test_compare_periods_with_filter_and_ratio_kind(ctx):
    r = tl.compare_periods(ctx, filters={"category": "A"})
    assert r.data["baseline"] == 78_210_000 and r.data["incident"] == 5_400_000   # data/ 실제 합계
    rr = tl.compare_periods(ctx, metric="cancel_qty_rate")
    assert rr.data["kind"] == "ratio" and rr.data["change_pp"] > 4


def test_compare_periods_different_lengths_gives_per_day(orders, inventory, truth):
    c = tl.build_context(orders, truth["incident"], {"start_date": "2026-09-18", "end_date": "2026-10-01"})
    r = tl.compare_periods(c)
    assert r.data["baseline_days"] == 14
    assert r.data["change_pct_per_day"] == pytest.approx(-30.4, abs=1.0)    # 일평균 기준은 거의 같다
    assert r.data["change_pct"] < -60                                       # 합계 기준은 왜곡
    assert r.notes


def test_category_contribution_matches_truth(ctx, truth):
    r = tl.contribution_by_dim(ctx, "category")
    assert r.ok
    t = r.table.set_index("category")
    for cat, expected in truth["actual"]["contribution_by_category"].items():
        assert t.loc[cat, "contribution_pct"] == pytest.approx(expected, abs=0.01)
    assert r.data["top_segment"] == "A"
    assert r.data["top_contribution_pct"] == pytest.approx(truth["actual"]["A_contribution_pct"], abs=0.01)
    assert r.data["top_lift"] > 2.5                                         # 기준 비중 22.5% → 기여 68.7%
    assert t["contribution_pct"].sum() == pytest.approx(100.0)


def test_region_contribution_matches_truth_and_is_not_concentrated(ctx, truth):
    r = tl.contribution_by_dim(ctx, "region")
    t = r.table.set_index("region")
    for reg, expected in truth["actual"]["contribution_by_region"].items():
        assert t.loc[reg, "contribution_pct"] == pytest.approx(expected, abs=0.01)
    assert t["lift"].between(0.8, 1.3).all()                                # 지역은 비중대로 줄었다


def test_multi_dim_and_filters(ctx):
    r = tl.contribution_by_dim(ctx, ["category", "region"])
    assert len(r.table) == 15 and r.table["contribution_pct"].sum() == pytest.approx(100.0)
    assert r.table.iloc[0]["category"] == "A"
    inA = tl.contribution_by_dim(ctx, "region", filters={"category": "A"})        # 상위 세그먼트 고정 후 하위 탐색
    assert inA.table["contribution_pct"].sum() == pytest.approx(100.0)
    assert inA.data["total_baseline"] == 78_210_000


def test_rank_dimensions_finds_category_a(ctx):
    r = tl.rank_dimensions(ctx)
    assert r.data["best_dim"] == "category" and r.data["best_segment"] == "A"
    assert r.data["best_excess_pp"] > 40
    reg = r.table.set_index("dim").loc["region"]
    assert abs(reg["excess_pp"]) < 5                                        # 지역은 몰림 없음
    sub = tl.rank_dimensions(ctx, filters={"category": "A"})                 # A 안에서는 어느 지역도 몰리지 않음
    assert list(sub.table["dim"]) == ["region"] and sub.data["best_excess_pp"] < 5


def test_ratio_metric_matches_truth(ctx, truth):
    r = tl.check_ratio_metric(ctx)
    tot = truth["actual"]["cancel_total"]
    assert r.data["baseline_rate_pct"] == pytest.approx(tot["baseline_cancel_rate_pct"], abs=0.01)
    assert r.data["incident_rate_pct"] == pytest.approx(tot["incident_cancel_rate_pct"], abs=0.01)
    assert (r.data["top_dim"], r.data["top_segment"]) == ("category", "A")
    t = r.table[r.table["dim"] == "category"].set_index("segment")
    for cat, v in truth["actual"]["cancel_by_category"].items():
        assert t.loc[cat, "baseline_rate_pct"] == pytest.approx(v["baseline_cancel_rate_pct"], abs=0.01)
        assert t.loc[cat, "incident_rate_pct"] == pytest.approx(v["incident_cancel_rate_pct"], abs=0.01)
    assert t.loc["C", "change_pp"] < 1                                       # 디코이 C 는 취소율 변화 없음


def test_ratio_metric_by_single_dim_and_errors(ctx):
    r = tl.check_ratio_metric(ctx, by="region")
    assert set(r.table["dim"]) == {"region"}
    assert tl.check_ratio_metric(ctx, metric="revenue").status == "error"
    assert tl.run_tool(ctx, "contribution_by_dim", by="category", metric="cancel_qty_rate").status == "error"


# ───────── 3. 재고·시점 ─────────
def test_inventory_stockout_matches_truth(ctx, truth):
    r = tl.check_inventory(ctx, segment={"category": "A"})
    rc = truth["root_cause"]
    assert r.ok
    assert r.data["zero_days"] == len(rc["stockout_days"]) == 7
    lead = [_day(truth, n) for n in rc["low_stock_lead_days"]]
    assert lead[0] <= r.data["low_run_start"] <= lead[-1]                    # 57~60일차 사이에 저재고 시작
    assert r.data["lead_days"] > 0
    assert r.data["low_run_end"] == _day(truth, rc["stockout_days"][-1])
    assert r.data["recovery_date"] == _day(truth, rc["stockout_days"][-1] + 1)   # 68일차 입고


def test_inventory_control_segments_not_depleted(ctx):
    for cat in ["B", "C", "D", "E"]:
        r = tl.check_inventory(ctx, segment={"category": cat})
        assert r.data["zero_days"] == 0 and r.data["low_run_start"] is None, cat


def test_inventory_ignores_dims_missing_in_aux_with_note(ctx):
    r = tl.check_inventory(ctx, segment={"region": "수도권"})                  # 재고 테이블에는 region 없음
    assert r.ok and "미적용" in r.data["scope"]


def test_timing_stock_precedes_revenue_drop(ctx, truth):
    r = tl.check_timing(ctx, segment={"category": "A"})
    assert r.data["metric_onset"] == _day(truth, 61)                         # A 매출은 61일차에 꺾임
    assert r.data["signal_onset"] < r.data["metric_onset"]
    assert r.data["relation"] == "선행" and r.data["lead_days"] >= 1
    assert f"{r.data['lead_days']}일 선행" in r.summary


def test_timing_price_signal_is_simultaneous_for_c_but_metric_unchanged(ctx, truth):
    r = tl.check_timing(ctx, segment={"category": "C"}, signal="price")
    assert r.data["signal_onset"] == _day(truth, 61)                         # C 가격은 61일차에 오름
    assert r.data["metric_onset"] is None and r.data["relation"] == "판정불가"   # C 매출은 거의 안 변함
    assert r.data["metric_change_pct"] > -10


def test_timing_without_segment_change_is_not_judged(ctx):
    r = tl.check_timing(ctx, segment={"category": "C"})                      # C 는 재고 고갈 없음
    assert r.ok and r.data["relation"] == "판정불가"


# ───────── 4. 가격 ─────────
def test_price_change_finds_decoy_c_only_in_category(ctx, truth):
    r = tl.check_price_change(ctx)
    dec = next(d for d in truth["decoys"] if d["type"] == "가격 변경")
    ch = [x for x in r.data["changed_segments"] if x["dim"] == "category"]
    assert [x["segment"] for x in ch] == [dec["category"]]
    assert ch[0]["price_change_pct"] == pytest.approx(dec["price_change_pct"], abs=0.01)
    assert ch[0]["contribution_pct"] == pytest.approx(truth["actual"]["contribution_by_category"]["C"], abs=0.01)
    assert not [x for x in r.data["changed_segments"] if x["dim"] == "region"]   # 구성비 효과는 임계값 아래


def test_price_from_aux_table_when_main_has_no_price(orders, inventory, truth):
    price_tbl = (orders.groupby(["date", "category"], as_index=False)["unit_price"].mean()
                 .rename(columns={"unit_price": "price"}))                    # 실제 주문에서 만든 가격표
    c = tl.build_context(orders.drop(columns=["unit_price"]), truth["incident"], truth["baseline"],
                         {"price": price_tbl, "inventory": inventory})
    assert tl._find_price_col(c) is None and c.aux_by_role("price") is not None
    r = tl.check_price_change(c)
    assert r.ok and "보조 테이블" in r.data["source"]
    assert [x["segment"] for x in r.data["changed_segments"]] == ["C"]


# ───────── 5. 데이터 품질 ─────────
def test_data_quality_clean_on_real_data(ctx):
    r = tl.check_data_quality(ctx)
    assert r.data["passed"] and r.data["n_issues"] == 0
    assert r.data["rows_baseline"] == r.data["rows_incident"] == 105


def _ctx_of(df, truth, inventory):
    return tl.build_context(df, truth["incident"], truth["baseline"], {"inventory": inventory})


def _incident_mask(df, truth):
    i = truth["incident"]
    return (df["date"] >= i["start_date"]) & (df["date"] <= i["end_date"])


def test_data_quality_detects_row_loss(orders, inventory, truth):
    m = _incident_mask(orders, truth)
    bad = pd.concat([orders[~m], orders[m].sample(frac=0.4, random_state=0)])      # 사건 구간 행 60% 삭제
    r = tl.check_data_quality(_ctx_of(bad, truth, inventory))
    assert not r.data["passed"] and any("행 수" in s for s in r.data["issues"])


def test_data_quality_detects_missing_date(orders, inventory, truth):
    bad = orders[orders["date"] != "2026-10-05"]
    r = tl.check_data_quality(_ctx_of(bad, truth, inventory))
    assert r.data["missing_dates"] == ["2026-10-05"]


def test_data_quality_detects_duplicates_negatives_nulls(orders, inventory, truth):
    m = _incident_mask(orders, truth)
    dup = pd.concat([orders, orders[m]])
    assert any("중복" in s for s in tl.check_data_quality(_ctx_of(dup, truth, inventory)).data["issues"])
    neg = orders.copy()
    neg.loc[neg.index[:3], "revenue"] = -1000
    assert tl.check_data_quality(_ctx_of(neg, truth, inventory)).data["negative_values"] == {"revenue": 3}
    nul = orders.copy()
    nul.loc[m, "region"] = np.nan
    r = tl.check_data_quality(_ctx_of(nul, truth, inventory))
    assert any("region" in s and "결측률" in s for s in r.data["issues"])


# ───────── 6. 음성 대조군: 정상 구간끼리 비교 ─────────
def test_negative_control_no_fake_incident(orders, inventory):
    """사건이 없는 정상 구간(8/17~8/23 vs 8/10~8/16)에서 도구가 몰림·고갈을 만들어내지 않는다."""
    c = tl.build_context(orders, {"start_date": "2026-08-17", "end_date": "2026-08-23"},
                         {"start_date": "2026-08-10", "end_date": "2026-08-16"}, {"inventory": inventory})
    assert abs(tl.compare_periods(c).data["change_pct"]) < 1                       # 실제 +0.1%
    rk = tl.rank_dimensions(c)                                                     # 분모≈0 → 기여도 해석 거부
    assert rk.status == "unavailable" and "불안정" in rk.summary
    cb = tl.contribution_by_dim(c, "category")
    assert cb.data["unstable"] and cb.notes                                        # 경고 플래그
    assert tl.check_ratio_metric(c).data["change_pp"] < 1
    assert tl.check_price_change(c).data["n_changed"] == 0
    assert tl.check_data_quality(c).data["passed"]
    for cat in "ABCDE":
        assert tl.check_inventory(c, segment={"category": cat}).data["zero_days"] == 0


def test_real_incident_is_not_flagged_unstable(ctx):
    assert tl.contribution_by_dim(ctx, "category").data["unstable"] is False


# ───────── 7. 범용성: 컬럼명·날짜형식 변형 ─────────
VARIANTS = [("en", _variant_en), ("ko", _variant_ko)]


@pytest.fixture(params=VARIANTS, ids=[v[0] for v in VARIANTS])
def vctx(request, orders, inventory, truth):
    main = request.param[1](orders)
    cat = next(c for c in main.columns if c in ("product_group", "상품군"))
    aux = inventory.rename(columns={"category": "sku_group", "stock_qty": "on_hand"})
    return tl.build_context(main, truth["incident"], truth["baseline"], {"stock": aux})


def test_variant_contribution_and_rank(vctx, truth):
    cat, reg = _cat_dim(vctx), _region_dim(vctx)
    r = tl.contribution_by_dim(vctx, cat)
    assert r.data["top_segment"] == "A"
    assert r.data["top_contribution_pct"] == pytest.approx(truth["actual"]["A_contribution_pct"], abs=0.01)
    rk = tl.rank_dimensions(vctx)
    assert (rk.data["best_dim"], rk.data["best_segment"]) == (cat, "A")
    assert abs(rk.table.set_index("dim").loc[reg, "excess_pp"]) < 5


def test_variant_ratio_inventory_timing_price_quality(vctx, truth):
    cat = _cat_dim(vctx)
    ratio = tl.check_ratio_metric(vctx)
    assert ratio.data["baseline_rate_pct"] == pytest.approx(truth["actual"]["cancel_total"]["baseline_cancel_rate_pct"], abs=0.01)
    assert (ratio.data["top_dim"], ratio.data["top_segment"]) == (cat, "A")
    inv = tl.check_inventory(vctx, segment={cat: "A"})
    assert inv.data["zero_days"] == 7 and inv.data["lead_days"] > 0
    tm = tl.check_timing(vctx, segment={cat: "A"})
    assert tm.data["relation"] == "선행" and tm.data["metric_onset"] == _day(truth, 61)
    pr = tl.check_price_change(vctx)
    assert [x["segment"] for x in pr.data["changed_segments"] if x["dim"] == cat] == ["C"]
    assert tl.check_data_quality(vctx).data["passed"]


# ───────── 8. 레지스트리·직렬화·불가 처리 ─────────
def test_available_tools_and_unavailable_without_aux(orders, truth):
    c = tl.build_context(orders, truth["incident"], truth["baseline"])               # 보조 테이블 없음
    av = tl.available_tools(c)
    assert av["check_inventory"][0] is False and av["check_timing"][0] is False
    assert av["compare_periods"][0] and av["check_price_change"][0] and av["check_ratio_metric"][0]
    r = tl.run_tool(c, "check_inventory", segment={"category": "A"})
    assert r.status == "unavailable" and "검증 불가" in r.summary and not r.ok


def test_no_ratio_metric_is_unavailable(orders, truth):
    slim = orders[["date", "category", "region", "revenue"]]
    c = tl.build_context(slim, truth["incident"], truth["baseline"])
    assert tl.available_tools(c)["check_ratio_metric"][0] is False
    assert tl.run_tool(c, "check_ratio_metric").status == "unavailable"
    assert tl.run_tool(c, "check_price_change").status == "unavailable"


def test_run_tool_converts_errors(ctx):
    assert tl.run_tool(ctx, "nope").status == "error"
    assert tl.run_tool(ctx, "contribution_by_dim", by="ghost").status == "error"
    assert tl.run_tool(ctx, "compare_periods", filters={"ghost": 1}).status == "error"
    assert tl.run_tool(ctx, "compare_periods", bad_arg=1).status == "error"
    assert tl.run_tool(ctx, "contribution_by_dim", by=[]).status == "error"
    assert tl.run_tool(ctx, "compare_periods", filters={"category": "ZZZ"}).status == "error"   # 데이터 없음


def test_every_result_is_json_serializable_with_query(ctx):
    calls = [("compare_periods", {}), ("contribution_by_dim", {"by": "category"}), ("rank_dimensions", {}),
             ("check_ratio_metric", {}), ("check_inventory", {"segment": {"category": "A"}}),
             ("check_price_change", {}), ("check_timing", {"segment": {"category": "A"}}),
             ("check_data_quality", {})]
    assert {n for n, _ in calls} == set(tl.TOOLS)                             # 모든 도구를 다룸
    for name, kw in calls:
        r = tl.run_tool(ctx, name, **kw)
        assert r.ok and r.summary and r.query, name
        d = json.loads(json.dumps(r.to_dict()))                               # NaN/Timestamp/numpy 도 직렬화 가능
        assert d["tool"] == name and d["status"] == "ok"


def test_summary_numbers_come_from_data(ctx):
    r = tl.contribution_by_dim(ctx, "category")
    assert f"{r.data['top_contribution_pct']:.1f}%" in r.summary
    assert f"{abs(r.data['total_change_pct']):.1f}%" in r.summary
