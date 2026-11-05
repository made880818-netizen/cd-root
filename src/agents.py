"""agents.py (1부) - 가설 풀 만들기 / 증거 장부 / 코드 채점 / 규칙 기반 결론. LLM 없이 동작한다.
2부(debate.py)에서 Claude가 이 도구들을 호출해 토론한다. 점수는 항상 이 파일의 코드가 계산한다.

사용법:  result = analyze(df, case)      # case = export_case.build_case_from_df(df) 결과
         print(rule_report(result))
"""
import json, time
import numpy as np
import pandas as pd
import export_case as ec

try:
    from logger import get_logger
    log = get_logger("agents")
except Exception:                      # logger.py가 없어도 단독 실행 가능
    import logging
    log = logging.getLogger("agents")

PLACEBO_FIRE = 0.2      # 정상 구간에서 이 비율 이상 같은 신호가 나오면 감점/경고


def f1(v, nd=1):
    """numpy 숫자를 JSON 가능한 float로"""
    try:
        v = float(v)
        return None if np.isnan(v) or np.isinf(v) else round(v, nd)
    except Exception:
        return None


def clip01(v):
    return float(min(max(v, 0.0), 1.0))


# ====================== 증거 장부 ======================
class Ledger:
    """도구 호출 기록. 모든 숫자는 여기 기록된 것만 보고서에 쓸 수 있다."""
    def __init__(self):
        self.rows = []

    def add(self, tool, args, result):
        lid = f"L{len(self.rows) + 1}"
        self.rows.append({"id": lid, "tool": tool, "args": args, "result": result})
        return lid

    def get(self, lid):
        return next((r for r in self.rows if r["id"] == lid), None)


def _z(base, ev):
    """기준 구간 대비 변화의 확실성(노이즈로 나눈 값). export_case와 같은 식"""
    bm, bs = base.mean(), base.std()
    return abs(ev.mean() - bm) / (bs / np.sqrt(len(base)) + bs * 0.5 + abs(bm) * 0.01 + 1e-9)


# ====================== 준비 ======================
def prepare(df, case):
    """DataFrame과 case JSON으로 분석에 필요한 것들을 한 번에 준비한다. 모드: time / group / none"""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if case.get("status") != "ok":
        return {"mode": "none", "case": case}
    roles = ec.classify(df)
    ax = case.get("axis")
    measures = [c for c, (r, _) in roles.items() if r == "measure"]
    flags = [c for c, (r, _) in roles.items() if r == "flag"]
    groups = [c for c, (r, _) in roles.items() if r == "group"]
    for f in flags:
        df[f] = df[f].astype(float)
    metrics = measures + flags
    main = case["main_metric"]
    ctx = {"df": df, "case": case, "metrics": metrics, "groups": groups, "main": main}

    if "group_col" in case:                                   # 시간 축 없는 데이터
        g = case["group_col"]
        sel = df[g].astype(str)
        ctx.update(mode="group", g=g, lo=case["lo"], hi=case["hi"],
                   is_lo=(sel == case["lo"]).values, is_hi=(sel == case["hi"]).values)
        return ctx

    series, meta = ec.make_series(df, ax, metrics)
    win = case["window"]
    b0, e0, ee = win["b0"], win["e0"], win["ev_end"]
    X = series["_x"].values
    sub_x, _ = ec.to_axis_numeric(df[ax])
    ctx.update(mode="time", ax=ax, series=series, meta=meta, X=X, b0=b0, e0=e0, ee=ee, w=max(ee - e0, 1),
               in_base=((sub_x >= X[b0]) & (sub_x < X[e0])).values,
               in_ev=((sub_x >= X[e0]) & (sub_x < (X[ee] if ee < len(X) else np.inf))).values)
    y = series[main].astype(float).values
    bm, bs = y[b0:e0].mean(), y[b0:e0].std()
    ctx["main_on"] = ec.onset_index(y, bm, bs, e0, ee)
    return ctx


# ====================== 도구: 시간 축 모드 ======================
def t_change(ctx, col):
    """col이 기준 구간 대비 사건 구간에서 얼마나 변했나"""
    y = ctx["series"][col].astype(float).values
    b, ev = y[ctx["b0"]:ctx["e0"]], y[ctx["e0"]:ctx["ee"]]
    if abs(b.mean()) < 1e-9 or len(ev) == 0:
        return {"pct": None, "z": None, "text": f"{col}: 기준값이 0에 가까워 변화율 계산 불가"}
    pct, z = (ev.mean() - b.mean()) / abs(b.mean()) * 100, _z(b, ev)
    return {"pct": f1(pct), "z": f1(z), "text": f"{col} 변화율 {f1(pct):+}% (확실성 z={f1(z)})"}


def t_lead(ctx, col):
    """col의 변화 시작이 주 지표보다 몇 칸 먼저/늦게인가 (양수=먼저)"""
    y = ctx["series"][col].astype(float).values
    b = y[ctx["b0"]:ctx["e0"]]
    on = ec.onset_index(y, b.mean(), b.std(), max(ctx["b0"] + 3, ctx["e0"] - ctx["w"]), ctx["ee"])
    if on is None or ctx["main_on"] is None:
        return {"lead": None, "text": f"{col}: 변화 시작 시점을 특정하지 못함"}
    step = ctx["meta"]["step"]
    lead = (ctx["X"][ctx["main_on"]] - ctx["X"][on]) / step
    word = "먼저" if lead > 0.5 else ("늦게" if lead < -0.5 else "동시에")
    return {"lead": f1(lead), "text": f"{col} 변화는 {ctx['main']}보다 {abs(f1(lead))}칸 {word} 시작"}


def t_corr(ctx, col):
    """사건 전후 구간에서 col과 주 지표가 함께 움직이는 정도(상관계수)"""
    s = ctx["series"]
    a = s[col].astype(float).values[ctx["b0"]:ctx["ee"]]
    m = s[ctx["main"]].astype(float).values[ctx["b0"]:ctx["ee"]]
    if a.std() < 1e-12 or m.std() < 1e-12:
        return {"r": None, "text": f"{col}: 값이 일정해 상관 계산 불가"}
    r = float(np.corrcoef(a, m)[0, 1])
    return {"r": f1(r, 2), "text": f"{col}와 {ctx['main']}의 상관계수 {f1(r, 2)}"}


def t_placebo(ctx, col):
    """정상 구간(사건 이전)에서도 같은 크기의 변화가 자주 나오는가. 많이 나오면 신호가 약하다"""
    y = ctx["series"][col].astype(float).values
    w, e0 = ctx["w"], ctx["e0"]
    n = fires = 0
    for s in range(w, e0 - 2 * w + 1):
        b, ev = y[max(0, s - 3 * w):s], y[s:s + w]
        if len(b) < w or abs(b.mean()) < 1e-9:
            continue
        pct = (ev.mean() - b.mean()) / abs(b.mean()) * 100
        n += 1
        fires += int(_z(b, ev) >= 3 and abs(pct) >= 5)
    if n < 3:
        return {"fire_rate": None, "n": n, "text": f"{col}: 정상 구간이 짧아 대조 불가"}
    rate = fires / n
    return {"fire_rate": f1(rate, 2), "n": n,
            "text": f"{col}: 정상 구간 {n}곳 중 {fires}곳에서 비슷한 변화 (비율 {f1(rate * 100)}%)"}


def t_group_share(ctx, group):
    """group 컬럼에서 변화량을 가장 많이 설명하는 값과 그 비중"""
    main, df = ctx["main"], ctx["df"]
    b, e = df[ctx["in_base"]], df[ctx["in_ev"]]
    nb, ne = max(ctx["e0"] - ctx["b0"], 1), max(ctx["ee"] - ctx["e0"], 1)
    if ctx["meta"]["rules"][main] == "sum":
        bs, es = b.groupby(group)[main].sum() / nb, e.groupby(group)[main].sum() / ne
        bw = bs / bs.sum() if bs.sum() else bs * 0
    else:
        bw = b.groupby(group).size() / max(len(b), 1)
        ew = e.groupby(group).size() / max(len(e), 1)
        bs, es = b.groupby(group)[main].mean() * bw, e.groupby(group)[main].mean() * ew
    idx = bs.index.union(es.index)
    bs, es, bw = bs.reindex(idx).fillna(0), es.reindex(idx).fillna(0), bw.reindex(idx).fillna(0)
    tot = es.sum() - bs.sum()
    if abs(tot) < 1e-9 or abs(tot) / max(abs(bs.sum()), 1e-9) < 0.05:
        return {"share": None, "text": f"{group}: 전체 변화가 작아 기여도가 불안정함"}
    share = (es - bs) / tot * 100
    k = share.idxmax()
    ratio = (share[k] / 100) / bw[k] if bw[k] > 0 else None
    return {"value": str(k), "share": f1(share[k]), "base_weight": f1(bw[k] * 100),
            "ratio": f1(ratio) if ratio else None,
            "text": f"{group}={k}: 변화의 {f1(share[k])}% 차지 (기준 비중 {f1(bw[k] * 100)}%)"}


def t_quality(ctx):
    """주 지표의 데이터 품질: 결측 증가, 값 멈춤, 한 점이 변화를 좌우하는지, 중복 행"""
    df, main = ctx["df"], ctx["main"]
    b, e = df[ctx["in_base"]], df[ctx["in_ev"]]
    miss_b, miss_e = b[main].isna().mean(), e[main].isna().mean()
    dup_b, dup_e = b.duplicated().mean(), e.duplicated().mean()
    y = ctx["series"][main].astype(float).values
    ev, bm = y[ctx["e0"]:ctx["ee"]], y[ctx["b0"]:ctx["e0"]].mean()
    flat = int((np.diff(ev) == 0).sum()) if len(ev) > 1 else 0
    exc = (ev - bm) * (1 if ev.mean() >= bm else -1)
    top1 = float(exc.max() / exc.sum()) if len(ev) >= 3 and exc.sum() > 0 else 0.0
    return {"miss_diff": f1((miss_e - miss_b) * 100), "flat": flat, "n_ev": len(ev), "top1": f1(top1, 2),
            "dup_diff": f1((dup_e - dup_b) * 100),
            "text": f"결측 {f1(miss_b * 100)}→{f1(miss_e * 100)}%, 멈춘 칸 {flat}/{len(ev)}, "
                    f"한 점의 변화 비중 {f1(top1 * 100)}%, 중복행 증가 {f1((dup_e - dup_b) * 100)}%p"}


# ====================== 도구: 그룹 비교 모드 (시간 축 없음) ======================
def _d(a, b):
    """두 집단의 표준화 평균 차이 (효과 크기, 부호 있음)"""
    a, b = pd.Series(a).dropna(), pd.Series(b).dropna()
    if len(a) < 2 or len(b) < 2:
        return 0.0
    sp = np.sqrt((a.var() * (len(a) - 1) + b.var() * (len(b) - 1)) / max(len(a) + len(b) - 2, 1))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else 0.0


def t_gm_effect(ctx):
    """그룹 간 지표 차이의 크기와, 라벨을 섞었을 때 이만큼 차이가 우연히 나올 확률"""
    df, m = ctx["df"], ctx["main"]
    sel = ctx["is_hi"] | ctx["is_lo"]
    v, lab = df.loc[sel, m].values.astype(float), ctx["is_hi"][sel]
    d = _d(v[lab], v[~lab])
    rng, cnt = np.random.default_rng(0), 0
    for _ in range(300):
        p = rng.permutation(lab)
        cnt += abs(_d(v[p], v[~p])) >= abs(d)
    pval = (cnt + 1) / 301
    return {"d": f1(d, 2), "p": f1(pval, 3),
            "text": f"{ctx['g']}={ctx['hi']} 대 {ctx['lo']}: 효과 크기 {f1(d, 2)}, 우연히 나올 확률 {f1(pval, 3)}"}


def t_gm_strata(ctx, group):
    """다른 그룹 컬럼의 각 값 안에서도 같은 방향의 차이가 나는가"""
    df, m = ctx["df"], ctx["main"]
    same = tot = 0
    sign = np.sign(df.loc[ctx["is_hi"], m].mean() - df.loc[ctx["is_lo"], m].mean())
    for k, sub in df.groupby(group):
        h, l = sub.loc[ctx["is_hi"][sub.index], m], sub.loc[ctx["is_lo"][sub.index], m]
        if len(h) >= 3 and len(l) >= 3:
            tot += 1
            same += int(np.sign(h.mean() - l.mean()) == sign)
    if tot == 0:
        return {"agree": None, "text": f"{group}: 층별 비교 표본 부족"}
    return {"agree": f"{same}/{tot}", "ratio": f1(same / tot, 2),
            "text": f"{group}의 값 {tot}개 중 {same}개에서 같은 방향의 차이"}


def t_gm_drop(ctx, group):
    """group의 특정 값을 빼면 그룹 간 차이가 얼마나 줄어드는가 (가장 크게 줄이는 값 하나)"""
    df, m = ctx["df"], ctx["main"]
    sel = ctx["is_hi"] | ctx["is_lo"]
    d_all = _d(df.loc[ctx["is_hi"], m], df.loc[ctx["is_lo"], m])
    if abs(d_all) < 1e-9:
        return {"shrink": None, "text": "그룹 간 차이가 없어 계산 불가"}
    best = None
    for k in df[group].dropna().unique():
        keep = sel & (df[group] != k).values
        h, l = keep & ctx["is_hi"], keep & ctx["is_lo"]
        if h.sum() < 10 or l.sum() < 10:
            continue
        d2 = _d(df.loc[h, m], df.loc[l, m])
        shrink = 1 - abs(d2) / abs(d_all) if np.sign(d2) == np.sign(d_all) else 1.0
        ph, pl = (df.loc[ctx["is_hi"], group] == k).mean(), (df.loc[ctx["is_lo"], group] == k).mean()
        if best is None or shrink > best["shrink"]:
            best = {"value": str(k), "shrink": f1(shrink, 2), "p_hi": f1(ph * 100), "p_lo": f1(pl * 100)}
    if best is None:
        return {"shrink": None, "text": f"{group}: 값을 빼고 비교할 표본이 부족"}
    best["text"] = (f"{group}={best['value']} 제외 시 차이 {f1(best['shrink'] * 100)}% 감소 "
                    f"(해당 값 비율: {ctx['hi']} {best['p_hi']}% / {ctx['lo']} {best['p_lo']}%)")
    return best


def t_gm_cov(ctx, col):
    """다른 수치 컬럼이 두 그룹 간에 다른 정도와, 주 지표와의 상관"""
    df, m = ctx["df"], ctx["main"]
    if col == m:
        return {"d": None, "r": None, "text": f"{col}는 주 지표 자체라 공변량으로 비교할 수 없음"}
    d = _d(df.loc[ctx["is_hi"], col], df.loc[ctx["is_lo"], col])
    sel = ctx["is_hi"] | ctx["is_lo"]
    pair = df.loc[sel, [col, m]].dropna()
    r = float(pair.corr().iloc[0, 1]) if len(pair) > 3 and pair[col].std() > 0 and pair[m].std() > 0 else None
    return {"d": f1(d, 2), "r": f1(r, 2) if r is not None else None,
            "text": f"{col}: 그룹 간 효과 크기 {f1(d, 2)}, {m}과 상관 {f1(r, 2) if r is not None else '계산불가'}"}


def t_gm_quality(ctx):
    """그룹 간 차이가 결측, 중복, 극단값 때문은 아닌지"""
    df, m = ctx["df"], ctx["main"]
    h, l = df.loc[ctx["is_hi"], m], df.loc[ctx["is_lo"], m]
    mean_diff = h.mean() - l.mean()
    med_diff = h.median() - l.median()
    outlier = clip01(1 - abs(med_diff) / abs(mean_diff)) if abs(mean_diff) > 1e-12 else 0.0
    dup = df[ctx["is_hi"]].duplicated().mean() - df[ctx["is_lo"]].duplicated().mean()
    miss = h.isna().mean() - l.isna().mean()
    return {"outlier": f1(outlier, 2), "dup_diff": f1(dup * 100), "miss_diff": f1(miss * 100),
            "text": f"평균 차이 대비 중앙값 차이 비율 {f1((1 - outlier) * 100)}%, 결측 차이 {f1(miss * 100)}%p, 중복행 차이 {f1(dup * 100)}%p"}


TOOLS = {f.__name__: f for f in [t_change, t_lead, t_corr, t_placebo, t_group_share, t_quality,
                                 t_gm_effect, t_gm_strata, t_gm_drop, t_gm_cov, t_gm_quality]}


def run_tool(ctx, ledger, name, **args):
    """도구 실행 + 장부 기록. 실패해도 예외 대신 오류 결과를 기록한다. 반환: (장부 id, 결과)"""
    t0 = time.time()
    try:
        res = TOOLS[name](ctx, **args)
    except Exception as e:
        log.warning(f"도구 실패 {name}{args}: {type(e).__name__}: {e}")
        res = {"error": str(e), "text": f"{name} 계산 실패"}
    lid = ledger.add(name, args, res)
    log.info(f"[{lid}] {name}{args or ''} -> {res.get('text')} ({time.time() - t0:.2f}초)")
    return lid, res


def verify_ledger(ctx, ledger):
    """장부의 모든 도구 호출을 같은 인자로 다시 계산해 값이 같은지 확인한다. 반환: 불일치 id 목록"""
    bad = []
    for r in ledger.rows:
        try:
            again = TOOLS[r["tool"]](ctx, **r["args"])
        except Exception:
            again = {"error": "재계산 실패"}
        if json.dumps(again, sort_keys=True, default=str) != json.dumps(r["result"], sort_keys=True, default=str):
            bad.append(r["id"])
    log.info(f"장부 재계산 검증: {len(ledger.rows)}건 중 불일치 {len(bad)}건")
    return bad


# ====================== 가설과 채점 ======================
def comp(name, weight, score, note):
    """채점 항목 하나. score=None이면 계산 불가라서 총점에서 제외"""
    return {"name": name, "weight": weight, "score": None if score is None else f1(clip01(score), 2), "note": note}


def total(comps, how="mean"):
    ok = [c for c in comps if c["score"] is not None]
    if not ok:
        return 0.0
    if how == "max":
        return f1(max(c["score"] for c in ok), 3)
    return f1(sum(c["weight"] * c["score"] for c in ok) / sum(c["weight"] for c in ok), 3)


def hyp(hid, kind, title, claim, ids, comps, how="mean", cap=None):
    """cap: 총점 상한 (필요할 때만 사용)"""
    h = {"id": hid, "kind": kind, "title": title, "claim": claim, "evidence_ids": ids,
         "components": comps, "total": total(comps, how)}
    if cap is not None and h["total"] > cap:
        h["total"] = cap
        h["capped"] = True
    log.info(f"가설 {hid} [{kind}] {title}: 총점 {h['total']} / " +
             ", ".join(f"{c['name']}={c['score']}" for c in comps))
    return h


def _data_comps_time(q):
    n = max(q["n_ev"], 1)
    return [comp("결측 증가", 1, (q["miss_diff"] or 0) / 20, f"결측 {q['miss_diff']}%p 증가"),
            comp("값 멈춤", 1, q["flat"] / max(n * 0.5, 1) if n >= 3 else 0, f"멈춘 칸 {q['flat']}/{n}"),
            comp("한 점이 좌우", 1, (q["top1"] - 0.3) / 0.4, f"한 점의 비중 {f1(q['top1'] * 100)}%"),
            comp("중복행 증가", 1, (q["dup_diff"] or 0) / 20, f"중복행 {q['dup_diff']}%p 증가")]


def build_time_hypotheses(ctx, ledger):
    main, H, later = ctx["main"], [], []
    # 1) 다른 컬럼이 먼저 변했다 (공변량)
    cands = []
    for m in ctx["metrics"]:
        if m == main:
            continue
        lid, ch = run_tool(ctx, ledger, "t_change", col=m)
        if ch.get("z") is not None and ch["z"] >= 3 and abs(ch["pct"]) >= 5:
            cands.append((ch["z"], m, lid, ch))
    for _, m, lid_ch, ch in sorted(cands, reverse=True)[:3]:
        lid_ld, ld = run_tool(ctx, ledger, "t_lead", col=m)
        lid_co, co = run_tool(ctx, ledger, "t_corr", col=m)
        lid_pl, pl = run_tool(ctx, ledger, "t_placebo", col=m)
        lead = ld.get("lead")
        lead_s = None if lead is None else (1.0 if lead > 0.5 else 0.5 if lead >= -0.5 else 0.1)
        fire = pl.get("fire_rate")
        if lead is not None and lead < -0.5:      # 주 지표보다 늦게 변함 -> 원인 순위에서 제외, 출발점 가설의 근거로 사용
            later.append((m, ld["text"], lid_ld))
            continue
        H.append(hyp(f"H{len(H) + 1}", "covariate", f"'{m}' 변화가 원인 후보",
                     f"{m}가 사건 구간에서 {ch['pct']:+}% 변했고, {ld['text']}.",
                     [lid_ch, lid_ld, lid_co, lid_pl],
                     [comp("변화 크기", 2, ch["z"] / 10, ch["text"]),
                      comp("선행성", 2, lead_s, ld["text"]),
                      comp("동행 상관", 1, None if co.get("r") is None else abs(co["r"]), co["text"]),
                      comp("정상 구간 대조", 2, None if fire is None else 1 - fire * 3, pl["text"])]))
    if later:                                     # 다른 변한 컬럼이 뒤따랐다 -> 주 지표 자체가 출발점 후보
        n_early = sum(1 for h in H if h["kind"] == "covariate")
        lid_c, chm = run_tool(ctx, ledger, "t_change", col=main)
        lid_p, plm = run_tool(ctx, ledger, "t_placebo", col=main)
        fire = plm.get("fire_rate")
        names = ", ".join(m for m, _, _ in later)
        H.append(hyp(f"H{len(H) + 1}", "origin", f"'{main}'가 데이터 안에서 가장 먼저 변한 출발점 후보",
                     f"{main}가 먼저 변했고 {names}는 뒤따라 변했다. 이 변화의 원인은 데이터 밖에 있을 수 있다.",
                     [lid_c, lid_p] + [i for _, _, i in later],
                     [comp("다른 컬럼보다 먼저", 3, len(later) / (len(later) + n_early),
                           f"변한 컬럼 {len(later) + n_early}개 중 {len(later)}개가 뒤따름"),
                      comp("변화 크기", 2, (chm["z"] or 0) / 10, chm["text"]),
                      comp("정상 구간 대조", 2, None if fire is None else 1 - fire * 3, plm["text"])]))
    # 2) 특정 그룹에 쏠렸다
    gs = []
    for g in ctx["groups"]:
        if ctx["df"][g].nunique() > 30:
            continue
        lid, r = run_tool(ctx, ledger, "t_group_share", group=g)
        if r.get("share") is not None:
            gs.append((r["share"], g, lid, r))
    for _, g, lid, r in sorted(gs, reverse=True)[:2]:
        ratio = r.get("ratio")
        H.append(hyp(f"H{len(H) + 1}", "group", f"'{g}={r['value']}'에 변화가 쏠렸다는 후보",
                     f"{r['text']}.", [lid],
                     [comp("변화 기여도", 3, r["share"] / 100, f"변화의 {r['share']}% 설명"),
                      comp("쏠림(기준 비중 대비)", 2, None if ratio is None else (ratio - 1) / 2,
                           f"기준 비중 {r['base_weight']}% 대비 {ratio}배")]))
    # 3) 데이터 오류
    lid, q = run_tool(ctx, ledger, "t_quality")
    H.append(hyp(f"H{len(H) + 1}", "data", "데이터 오류(결측, 멈춤, 이상값)라는 후보",
                 f"데이터 품질 점검: {q['text']}.", [lid], _data_comps_time(q), how="max"))
    return H


def build_group_hypotheses(ctx, ledger):
    m, g, H = ctx["main"], ctx["g"], []
    lid_e, ef = run_tool(ctx, ledger, "t_gm_effect")
    strata, ids = [], [lid_e]
    for o in ctx["groups"]:
        if o != g and 2 <= ctx["df"][o].nunique() <= 30:
            lid, r = run_tool(ctx, ledger, "t_gm_strata", group=o)
            ids.append(lid); strata.append(r)
    ratios = [s["ratio"] for s in strata if s.get("ratio") is not None]
    H.append(hyp("H1", "direct", f"'{g}' 자체가 차이를 만들었다는 후보",
                 f"{ef['text']}.", ids,
                 [comp("차이 크기", 2, abs(ef["d"]) / 1.5, f"효과 크기 {ef['d']}"),
                  comp("우연 배제", 2, 1 - ef["p"] * 10, f"우연히 나올 확률 {ef['p']}"),
                  comp("다른 조건에서도 일관", 2, min(ratios) if ratios else None,
                       "; ".join(s["text"] for s in strata) or "비교할 다른 그룹 컬럼 없음")]))
    for o in ctx["groups"]:                                   # 다른 그룹 컬럼이 숨은 원인
        if o == g or not (2 <= ctx["df"][o].nunique() <= 30):
            continue
        lid, r = run_tool(ctx, ledger, "t_gm_drop", group=o)
        if r.get("shrink") is None:
            continue
        conc = abs(r["p_hi"] - r["p_lo"]) / 100
        H.append(hyp(f"H{len(H) + 1}", "confound", f"'{o}={r['value']}'가 차이를 만들었다는 후보",
                     f"{r['text']}.", [lid],
                     [comp("제외 시 차이 감소", 3, r["shrink"], r["text"]),
                      comp("두 그룹 간 분포 차이", 1, conc / 0.3, f"해당 값 비율 차 {f1(conc * 100)}%p")]))
    cov = []
    for c in ctx["metrics"]:
        if c != m:
            lid, r = run_tool(ctx, ledger, "t_gm_cov", col=c)
            cov.append((abs(r["d"] or 0), c, lid, r))
    for _, c, lid, r in sorted(cov, reverse=True)[:2]:
        H.append(hyp(f"H{len(H) + 1}", "covariate", f"'{c}'가 함께 달라서라는 후보",
                     f"{r['text']}.", [lid],
                     [comp("그룹 간 차이", 2, abs(r["d"]) / 1.0, f"효과 크기 {r['d']}"),
                      comp("지표와 상관", 2, None if r["r"] is None else abs(r["r"]), f"상관 {r['r']}")]))
    lid, q = run_tool(ctx, ledger, "t_gm_quality")
    H.append(hyp(f"H{len(H) + 1}", "data", "데이터 오류(극단값, 결측, 중복)라는 후보",
                 f"데이터 품질 점검: {q['text']}.", [lid],
                 [comp("극단값이 좌우", 1, q["outlier"], f"극단값 영향 {q['outlier']}"),
                  comp("결측 차이", 1, abs(q["miss_diff"] or 0) / 20, f"{q['miss_diff']}%p"),
                  comp("중복행 차이", 1, abs(q["dup_diff"] or 0) / 20, f"{q['dup_diff']}%p")], how="max"))
    return H


# ====================== 분석 실행 ======================
def analyze(df, case):
    """가설 풀을 만들고 채점해 순위를 매긴다. 어떤 입력이어도 예외 대신 status를 돌려준다."""
    ledger = Ledger()
    try:
        ctx = prepare(df, case)
        if ctx["mode"] == "none":
            log.warning("사건이 없어 가설을 만들지 않음")
            return {"status": "no_event", "ctx": ctx, "ledger": ledger, "hypotheses": [],
                    "conclusion": {"strength": "없음", "text": case["evidence"][0]["value"]}}
        H = build_time_hypotheses(ctx, ledger) if ctx["mode"] == "time" else build_group_hypotheses(ctx, ledger)
        H.sort(key=lambda h: h["total"], reverse=True)
        warns = []
        if ctx["mode"] == "time":                             # 사건 자체가 정상 구간에도 흔한 신호인가
            lid, pl = run_tool(ctx, ledger, "t_placebo", col=ctx["main"])
            if (pl.get("fire_rate") or 0) >= PLACEBO_FIRE:
                warns.append(f"주 지표는 정상 구간에서도 비슷한 변화가 잦아 사건이 불확실함 ({pl['text']})")
        top = H[0] if H else None
        gap = f1(top["total"] - H[1]["total"], 3) if len(H) > 1 else None
        strength = "근거 부족" if not top or top["total"] < 0.4 else ("유력" if top["total"] >= 0.6 else "후보")
        concl = {"strength": strength, "top_id": top["id"] if top else None, "gap": gap, "warnings": warns}
        log.info(f"결론: 1위 {top['id'] if top else '-'} ({strength}), 2위와 점수차 {gap}")
        return {"status": "ok", "ctx": ctx, "ledger": ledger, "hypotheses": H, "conclusion": concl}
    except Exception as e:
        log.exception("analyze 실패")
        return {"status": "error", "ctx": {}, "ledger": ledger, "hypotheses": [],
                "conclusion": {"strength": "없음", "text": f"분석 중 오류: {type(e).__name__}: {e}"}}


# ====================== 규칙 기반 보고서 (LLM 폴백) ======================
def rule_report(result):
    """LLM 없이 점수표와 증거 장부만으로 보고서 문장을 만든다. 원인은 확정하지 않고 후보로 쓴다."""
    case, c = result["ctx"].get("case", {}), result["conclusion"]
    if result["status"] != "ok":
        return f"[{result['status']}] {c.get('text', '')}"
    H = result["hypotheses"]
    out = [f"## {case.get('case_name')}",
           f"- 기준 구간: {case['period']['baseline']} / 사건 구간: {case['period']['event']}"]
    for w in c.get("warnings", []):
        out.append(f"- 주의: {w}")
    out.append("\n### 가설 순위 (점수는 코드가 계산, 후보이며 인과 확정이 아님)")
    for i, h in enumerate(H, 1):
        out.append(f"{i}. [{h['total']}] {h['title']}")
        out.append(f"   - 주장: {h['claim']}")
        for cp in h["components"]:
            out.append(f"   - {cp['name']}: {cp['score'] if cp['score'] is not None else '계산불가'} ({cp['note']})")
    top = H[0]
    out.append(f"\n### 결론\n가장 근거가 강한 후보: {top['title']} (점수 {top['total']}, 판정: {c['strength']}).")
    if c.get("gap") is not None:
        out.append(f"2위와의 점수 차이는 {c['gap']}입니다. " +
                   ("차이가 작아 여러 원인이 함께 작용했을 가능성이 있습니다." if c["gap"] < 0.1 else ""))
    out.append("가설은 서로 배타적이지 않으며, 위 점수는 데이터에 나타난 증거의 강도일 뿐 원인을 확정하지 않습니다.")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    path = next((a for a in sys.argv[1:] if not a.startswith("--")), "data/orders.csv")
    df = ec.read_csv_any(path)
    metric = next((a.split("=")[1] for a in sys.argv if a.startswith("--metric=")), None)
    case = ec.build_case_from_df(df, name=path, metric=metric)
    res = analyze(df, case)
    print("\n" + rule_report(res))
    if res["status"] == "ok":
        print("\n[검증] 재계산 불일치:", verify_ledger(res["ctx"], res["ledger"]))
