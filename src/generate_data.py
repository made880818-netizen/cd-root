"""CaseLens 합성 데이터 생성기 (v3).

90일치 일별 이커머스 데이터를 생성하고 data/ 에 저장한다.
  - orders.csv        : date, category, region, qty, unit_price, revenue,
                        cancel_qty, cancel_revenue, net_revenue   (v3 추가: 취소)
                        * revenue = 주문금액(취소 포함). v2와 동일 값 → 기존 수치 유지
                        * net_revenue = revenue - cancel_revenue
  - inventory.csv     : date, category, stock_qty
  - ground_truth.json : 주입 사건 정보 + '저장된 CSV에서 다시 계산한' 실제 수치

사건 설계
  - 기준기간 54~60일차, 사건기간 61~67일차 (각 7일 = 요일 구성 동일 → 계절성 상쇄)
  - A 카테고리: 57~60일차 재고 급감(선행 신호), 61~67일차 재고 0, 매출 거의 0
  - 디코이 1: C 카테고리 가격 +3% (61일차부터, 수량 소폭 감소)
  - 디코이 2: 호남 지역 전 카테고리 소폭 감소 (원인과 무관)
  - 동반 하락: B/D/E는 A 품절 영향(교차구매 감소)으로 완만히 하락
  - [v3] 취소 연쇄: A 재고 급감(57~60일차) 시 A 취소 소폭 상승 → 품절(61~67일차) A 취소율 급등,
    묶음주문 영향으로 B/D/E 취소율도 상승. C/지역 디코이는 취소와 무관.
    취소는 별도 난수 스트림(SEED+1)을 사용하므로 기존 매출·재고 값은 v2와 동일.
실행: python src/generate_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# ───────────────────────── 파라미터 ─────────────────────────
SEED = 42
N_DAYS = 90
START_DATE = "2026-08-03"  # 월요일 → 1일차 = 월요일
DAILY_REVENUE = 50_000_000  # 정상 시 일 평균 총매출(원)

CATEGORIES = ["A", "B", "C", "D", "E"]
# A 비중: 'A 매출 거의 0' + '전체 -30% / A 기여 68%'를 동시에 만족하려면 약 20~23% 필요
# (A 비중을 45%로 두면 A가 0이 될 때 전체가 45% 빠져 -30% 목표와 모순)
CAT_SHARE = {"A": 0.22, "B": 0.24, "C": 0.20, "D": 0.18, "E": 0.16}
UNIT_PRICE = {"A": 30_000, "B": 20_000, "C": 15_000, "D": 12_000, "E": 8_000}

REGIONS = ["수도권", "영남", "호남"]
REGION_SHARE = {"수도권": 0.50, "영남": 0.30, "호남": 0.20}

WEEKDAY_FACTOR = [0.95, 0.95, 1.00, 1.00, 1.10, 1.15, 0.85]  # 월~일
NOISE_SIGMA = 0.05  # 로그정규 노이즈

# 사건 기간 (1-indexed day)
BASELINE = (54, 60)
INCIDENT = (61, 67)
LOWSTOCK_DAYS = {57: 0.20, 58: 0.12, 59: 0.07, 60: 0.03}  # A 재고 급감(정상 재고 대비 비율)

A_RESIDUAL = 0.07          # 사건기간 A 잔존 매출 비율 (예약·취소 지연분)
OTHERS_DROP = {"B": 0.17, "D": 0.17, "E": 0.17}  # A 품절 동반 하락(교차구매 감소)
C_PRICE_UP = 0.03          # 디코이1: C 가격 인상
C_PRICE_ELASTICITY = -0.5  # 가격 +3% → 수량 -1.5%
DECOY_REGION = "호남"
DECOY_REGION_DROP = 0.03   # 디코이2: 호남 -3%

# [v3] 주문 취소 파라미터
CANCEL_SEED = SEED + 1
CANCEL_BASE = 0.03                                   # 평시 취소율
CANCEL_A_LOWSTOCK = {57: 0.04, 58: 0.06, 59: 0.10, 60: 0.15}   # A 재고 급감기 취소율(선행 신호)
CANCEL_A_STOCKOUT = 0.60                             # A 품절기 취소율
CANCEL_OTHERS_STOCKOUT = {"B": 0.09, "D": 0.09, "E": 0.09}     # 묶음주문 영향

OUT_DIR = Path(__file__).resolve().parent.parent / "data"


def in_range(day: int, rng_: tuple[int, int]) -> bool:
    return rng_[0] <= day <= rng_[1]


def cancel_rate(cat: str, day: int) -> float:
    if cat == "A":
        if in_range(day, INCIDENT):
            return CANCEL_A_STOCKOUT
        if day in CANCEL_A_LOWSTOCK:
            return CANCEL_A_LOWSTOCK[day]
    if in_range(day, INCIDENT) and cat in CANCEL_OTHERS_STOCKOUT:
        return CANCEL_OTHERS_STOCKOUT[cat]
    return CANCEL_BASE


def generate_orders(rng: np.random.Generator, crng: np.random.Generator, dates: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    for day, date in enumerate(dates, start=1):
        season = WEEKDAY_FACTOR[date.weekday()]
        incident = in_range(day, INCIDENT)
        for cat in CATEGORIES:
            price = UNIT_PRICE[cat]
            if cat == "C" and day >= INCIDENT[0]:
                price = round(price * (1 + C_PRICE_UP))
            for reg in REGIONS:
                base_rev = DAILY_REVENUE * CAT_SHARE[cat] * REGION_SHARE[reg] * season
                factor = 1.0
                if incident:
                    if cat == "A":
                        factor *= A_RESIDUAL
                    factor *= 1 - OTHERS_DROP.get(cat, 0.0)
                    if cat == "C":
                        factor *= 1 + C_PRICE_ELASTICITY * C_PRICE_UP
                    if reg == DECOY_REGION:
                        factor *= 1 - DECOY_REGION_DROP
                noise = rng.lognormal(mean=0.0, sigma=NOISE_SIGMA)
                qty = max(int(round(base_rev * factor * noise / price)), 0)
                cancel_qty = int(crng.binomial(qty, cancel_rate(cat, day)))  # 별도 스트림
                rows.append(
                    {
                        "date": date.strftime("%Y-%m-%d"),
                        "category": cat,
                        "region": reg,
                        "qty": qty,
                        "unit_price": price,
                        "revenue": qty * price,
                        "cancel_qty": cancel_qty,
                        "cancel_revenue": cancel_qty * price,
                        "net_revenue": (qty - cancel_qty) * price,
                    }
                )
    return pd.DataFrame(rows)


def generate_inventory(orders: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """일 판매량을 차감하고 재주문점 이하이면 보충하는 단순 재고 시뮬레이션."""
    daily_qty = orders.groupby(["date", "category"])["qty"].sum().unstack()
    rows = []
    for cat in CATEGORIES:
        full_stock = int(daily_qty[cat].mean() * 14)  # 약 14일치 재고
        reorder_point = int(full_stock * 0.30)
        stock = full_stock
        for day, date in enumerate(dates, start=1):
            key = date.strftime("%Y-%m-%d")
            stock = max(stock - int(daily_qty.loc[key, cat]), 0)
            shown = stock
            if cat == "A":
                if day in LOWSTOCK_DAYS:
                    shown = int(full_stock * LOWSTOCK_DAYS[day])
                    stock = shown
                elif in_range(day, INCIDENT):
                    shown, stock = 0, 0
                elif day == INCIDENT[1] + 1:
                    shown = stock = full_stock  # 68일차 입고
            if stock < reorder_point and not (cat == "A" and 55 <= day <= INCIDENT[1]):
                stock = shown = full_stock
            rows.append({"date": key, "category": cat, "stock_qty": int(shown)})
    return pd.DataFrame(rows)


def contribution(orders: pd.DataFrame, dates: pd.DatetimeIndex, dim: str = "category") -> pd.DataFrame:
    """저장된 데이터 기준 기여도 계산 (decompose_contribution의 원형)."""
    d = orders.assign(day=orders["date"].map({x.strftime("%Y-%m-%d"): i + 1 for i, x in enumerate(dates)}))
    base = d[d["day"].between(*BASELINE)].groupby(dim)["revenue"].sum()
    inc = d[d["day"].between(*INCIDENT)].groupby(dim)["revenue"].sum()
    out = pd.DataFrame({"baseline": base, "incident": inc}).fillna(0)
    out["delta"] = out["baseline"] - out["incident"]
    total_drop = out["delta"].sum()
    out["contribution_pct"] = out["delta"] / total_drop * 100
    return out.sort_values("contribution_pct", ascending=False)


def main() -> None:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range(START_DATE, periods=N_DAYS, freq="D")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    crng = np.random.default_rng(CANCEL_SEED)
    orders = generate_orders(rng, crng, dates)
    inventory = generate_inventory(orders, dates)
    orders.to_csv(OUT_DIR / "orders.csv", index=False, encoding="utf-8-sig")
    inventory.to_csv(OUT_DIR / "inventory.csv", index=False, encoding="utf-8-sig")

    # 저장된 CSV를 다시 읽어 실제 값 계산 (정답표 = 파일 기준)
    orders_chk = pd.read_csv(OUT_DIR / "orders.csv")
    by_cat = contribution(orders_chk, dates, "category")
    by_reg = contribution(orders_chk, dates, "region")
    base_total = float(by_cat["baseline"].sum())
    inc_total = float(by_cat["incident"].sum())
    drop_pct = (base_total - inc_total) / base_total * 100
    a_contrib = float(by_cat.loc["A", "contribution_pct"])

    d_chk = orders_chk.assign(day=orders_chk["date"].map({x.strftime("%Y-%m-%d"): i + 1 for i, x in enumerate(dates)}))

    def cancel_stats(sub: pd.DataFrame) -> dict:
        b = sub[sub["day"].between(*BASELINE)]
        i = sub[sub["day"].between(*INCIDENT)]
        br = b["cancel_qty"].sum() / b["qty"].sum() * 100
        ir = i["cancel_qty"].sum() / i["qty"].sum() * 100
        return {"baseline_cancel_rate_pct": round(float(br), 2), "incident_cancel_rate_pct": round(float(ir), 2)}

    cancel_total = cancel_stats(d_chk)
    cancel_by_cat = {c: cancel_stats(d_chk[d_chk["category"] == c]) for c in CATEGORIES}
    net_b = float(d_chk[d_chk["day"].between(*BASELINE)]["net_revenue"].sum())
    net_i = float(d_chk[d_chk["day"].between(*INCIDENT)]["net_revenue"].sum())

    day_date = lambda d: dates[d - 1].strftime("%Y-%m-%d")  # noqa: E731
    truth = {
        "seed": SEED,
        "incident": {
            "metric": "revenue",
            "start_day": INCIDENT[0], "end_day": INCIDENT[1],
            "start_date": day_date(INCIDENT[0]), "end_date": day_date(INCIDENT[1]),
        },
        "baseline": {
            "start_day": BASELINE[0], "end_day": BASELINE[1],
            "start_date": day_date(BASELINE[0]), "end_date": day_date(BASELINE[1]),
        },
        "root_cause": {
            "category": "A",
            "type": "재고 부족",
            "stockout_days": list(range(INCIDENT[0], INCIDENT[1] + 1)),
            "low_stock_lead_days": list(LOWSTOCK_DAYS.keys()),
        },
        "decoys": [
            {"type": "가격 변경", "category": "C", "price_change_pct": C_PRICE_UP * 100, "start_day": INCIDENT[0]},
            {"type": "지역 소폭 변동", "region": DECOY_REGION, "change_pct": -DECOY_REGION_DROP * 100},
        ],
        "actual": {
            "baseline_revenue": int(base_total),
            "incident_revenue": int(inc_total),
            "total_drop_pct": round(drop_pct, 2),
            "A_contribution_pct": round(a_contrib, 2),
            "contribution_by_category": {k: round(float(v), 2) for k, v in by_cat["contribution_pct"].items()},
            "contribution_by_region": {k: round(float(v), 2) for k, v in by_reg["contribution_pct"].items()},
            "net_revenue_drop_pct": round((net_b - net_i) / net_b * 100, 2),
            "cancel_total": cancel_total,
            "cancel_by_category": cancel_by_cat,
        },
        "chain": ["A 재고 급감(57~60일차)", "A 품절(61~67일차)", "총매출 감소 + 취소율 상승(61일차~)"],
    }
    (OUT_DIR / "ground_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")

    # ───────── 콘솔 출력 ─────────
    print(f"[저장] {OUT_DIR}/orders.csv ({len(orders):,}행), inventory.csv ({len(inventory):,}행), ground_truth.json")
    print(f"[기준기간] {day_date(BASELINE[0])} ~ {day_date(BASELINE[1])}  매출 {base_total:,.0f}원")
    print(f"[사건기간] {day_date(INCIDENT[0])} ~ {day_date(INCIDENT[1])}  매출 {inc_total:,.0f}원")
    print(f"[전체 매출 감소율] {drop_pct:.1f}%  (목표 약 30%)")
    print(f"[A 기여도]        {a_contrib:.1f}%  (목표 약 68%)")
    print("\n[카테고리별 기여도]")
    print(by_cat.round({"contribution_pct": 1}).to_string())
    print("\n[지역별 기여도]")
    print(by_reg.round({"contribution_pct": 1}).to_string())
    print("\n[취소율] 기준기간 {baseline_cancel_rate_pct}% → 사건기간 {incident_cancel_rate_pct}%".format(**cancel_total))
    for c in CATEGORIES:
        print(f"  {c}: {cancel_by_cat[c]['baseline_cancel_rate_pct']}% → {cancel_by_cat[c]['incident_cancel_rate_pct']}%")
    ok = abs(drop_pct - 30) <= 3 and abs(a_contrib - 68) <= 4
    print("\n[검증]", "목표 범위 내 (감소율 30±3%, A 기여도 68±4%)" if ok else "목표 이탈 → 파라미터 조정 필요")


if __name__ == "__main__":
    main()
