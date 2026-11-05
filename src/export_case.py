"""export_case.py - 어떤 CSV든 읽어서 '사건 증거 JSON'을 만든다. (컬럼 이름은 쓰지 않고 값의 성질만 본다)

사용법:  python export_case.py 파일.csv [출력.json] [--metric=컬럼] [--axis=컬럼]
웹에서:  from export_case import build_case_from_df
결과 JSON: {case_name, period, evidence[{id,label,value,unit}], status, debug}
"""
import sys, json, logging, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')   # 날짜 형식 추측 경고 숨김
log = logging.getLogger("export_case")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)

MAX_BINS = 200      # 시간축 칸 수 상한
MIN_BINS = 8        # 이보다 칸이 적으면 사건 탐지를 포기


# ---------- 1. 읽기 ----------
def read_csv_any(path_or_file):
    # 인코딩이 뭐든 읽히게 순서대로 시도
    for enc in ("utf-8-sig", "cp949", "euc-kr", "latin-1"):
        try:
            if hasattr(path_or_file, "seek"):
                path_or_file.seek(0)
            df = pd.read_csv(path_or_file, encoding=enc, sep=None, engine="python")
            log.info(f"읽기 성공 (인코딩 {enc}): {df.shape[0]}행 x {df.shape[1]}열")
            return df
        except Exception as e:
            log.debug(f"인코딩 {enc} 실패: {e}")
    raise ValueError("CSV를 읽을 수 없습니다 (인코딩/구분자 확인)")


# ---------- 2. 컬럼 역할 분류 ----------
def classify(df):
    """각 컬럼을 axis/measure/group/flag/skip 으로 분류. 반환: {컬럼: (역할, 이유)}"""
    roles, n = {}, len(df)
    for c in df.columns:
        s = df[c]
        nun = s.nunique(dropna=True)
        if nun <= 1:
            roles[c] = ("skip", "값이 하나뿐"); continue
        if pd.api.types.is_numeric_dtype(s):
            if nun == 2 and set(s.dropna().unique()) <= {0, 1}:
                roles[c] = ("flag", "0/1 값"); continue
            sd = s.dropna()
            ux = np.sort(sd.unique())
            if sd.is_monotonic_increasing and nun >= 8 and len(ux) > 1 and len(np.unique(np.round(np.diff(ux), 6))) <= 3:
                roles[c] = ("axis", "일정 간격으로 증가하는 숫자"); continue
            is_int = (sd % 1 == 0).all()
            if is_int and nun <= min(5, max(2, n // 20)):
                roles[c] = ("group", f"작은 정수 코드({nun}종)"); continue
            if is_int and nun == n and n > 20:
                roles[c] = ("skip", "행마다 다른 정수(ID로 추정)"); continue
            roles[c] = ("measure", "수치"); continue
        # 문자열 계열: 날짜 먼저 시도
        parsed = pd.to_datetime(s, errors="coerce")
        if parsed.notna().mean() > 0.9 and nun > 3:
            roles[c] = ("axis", "날짜/시각"); continue
        if nun <= max(2, min(30, n // 5)):
            roles[c] = ("group", f"범주({nun}종)")
        else:
            roles[c] = ("skip", "고유값 많은 문자(ID/자유텍스트)")
    # 축이 여러 개면 첫 번째만 유지
    axes = [c for c, (r, _) in roles.items() if r == "axis"]
    for c in axes[1:]:
        roles[c] = ("skip", "보조 시간 컬럼")
    return roles


# ---------- 3. 축 만들기 + 집계 ----------
def to_axis_numeric(s):
    """축 컬럼을 숫자로. 반환 (숫자 Series, 날짜형 여부)"""
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float), False
    t = pd.to_datetime(s, errors="coerce")
    ns = t.astype("datetime64[ns]").astype("int64")          # pandas 버전별 시간 단위 차이를 ns로 통일
    return ns.where(t.notna()) / 1e9, True                   # 초 단위


def fmt_axis(v, is_date, step):
    if not is_date:
        return f"{v:g}"
    t = pd.to_datetime(v, unit="s")
    return t.strftime("%Y-%m-%d") if step >= 86400 else t.strftime("%Y-%m-%d %H:%M:%S")


def agg_rule(df, col, dup_per_bin):
    """한 칸에 여러 행이 있을 때 합칠지(sum) 평균낼지(mean) 정한다"""
    s = df[col].dropna()
    if dup_per_bin <= 1.5:
        return "mean"
    if (s >= 0).all() and s.mean() > 0 and s.std() / s.mean() > 0.1:
        return "sum"        # 값이 크게 흩어진 음이 아닌 수(금액·수량 등)는 합산
    return "mean"


def make_series(df, axis, measures, groups=None):
    """축 기준 시계열 표 만들기. 반환 (표, 메타)"""
    x, is_date = to_axis_numeric(df[axis])
    d = df.assign(_x=x).dropna(subset=["_x"])
    ux = np.sort(d["_x"].unique())
    step = float(np.median(np.diff(ux))) if len(ux) > 1 else 1.0
    if len(ux) > MAX_BINS:                       # 칸이 너무 많으면 묶는다
        edges = np.linspace(ux[0], ux[-1] + step, MAX_BINS + 1)
        d["_b"] = np.digitize(d["_x"], edges[1:-1])
        starts = {b: edges[b] for b in range(MAX_BINS)}
        step = (edges[1] - edges[0])
    else:
        d["_b"] = d["_x"]
        starts = None
    n_bins = d["_b"].nunique()
    dup = len(d) / max(n_bins, 1)
    rules = {m: agg_rule(d, m, dup) for m in measures}
    g = d.groupby("_b").agg(rules).sort_index()
    n_nan = int(g.isna().sum().sum())
    if n_nan:                                    # 칸이 통째로 결측이면 앞뒤 값으로 메워 계산이 NaN으로 번지지 않게 함
        log.info(f"시계열 칸 결측 {n_nan}개를 보간해서 사용 (원본 결측률은 품질 점검에서 따로 확인)")
        g = g.interpolate(limit_direction="both")
    idx = g.index.values
    g["_x"] = [starts[b] if starts else b for b in idx]
    g = g.reset_index(drop=True)
    return g, {"is_date": is_date, "step": step, "rules": rules, "dup": dup}


# ---------- 4. 사건 구간 탐지 ----------
def onset_index(vals, base_mean, base_std, lo, hi, k=3.0, need=2):
    """lo~hi 구간에서 기준에서 k*표준편차 이상 벗어나는 상태가 need칸 이어지는 첫 위치"""
    thr = k * max(base_std, abs(base_mean) * 0.01, 1e-9)
    run = 0
    for i in range(lo, hi):
        if abs(vals[i] - base_mean) > thr:
            run += 1
            if run >= need:
                return i - need + 1
        else:
            run = 0
    return None


def detect_window(y):
    """한 지표에서 '직전 구간 대비 가장 크게 변한' 창을 찾는다. 반환 dict 또는 None"""
    n = len(y)
    w = max(3, n // 10)
    if n < 2 * w + 2:
        return None
    best = None
    for e0 in range(w, n - w + 1):
        b = y[max(0, e0 - 3 * w):e0]          # 기준: 사건 직전 최대 3w칸
        ev = y[e0:e0 + w]
        bm, bs = b.mean(), b.std() + 1e-9
        z = abs(ev.mean() - bm) / (bs / np.sqrt(len(b)) + bs * 0.5 + abs(bm) * 0.01)   # 노이즈 대비 변화
        if best is None or z > best["z"]:
            best = {"e0": e0, "w": w, "b0": max(0, e0 - 3 * w), "z": z,
                    "base_mean": bm, "base_std": bs, "ev_mean": ev.mean()}
    if best["base_mean"] == 0:
        return None
    best["pct"] = (best["ev_mean"] - best["base_mean"]) / abs(best["base_mean"]) * 100
    return best


# ---------- 5. 증거 만들기 ----------
class Ev:
    def __init__(self): self.items = []
    def add(self, label, value, unit=""):
        self.items.append({"id": f"E{len(self.items)+1}", "label": label, "value": value, "unit": unit})


def r1(v): return round(float(v), 1)


def no_event(name, reason, debug):
    log.warning(f"사건 없음: {reason}")
    ev = Ev(); ev.add("분석 결과", reason, "")
    return {"case_name": name, "period": {"baseline": "-", "event": "-"},
            "evidence": ev.items, "status": "no_event", "debug": debug}


def build_case_from_df(df, name="분석 사건", metric=None, axis=None):
    """DataFrame -> 증거 JSON. 어떤 단계에서 실패해도 예외 대신 no_event 결과를 돌려준다."""
    debug = {}
    try:
        return _build(df, name, metric, axis, debug)
    except Exception as e:                                   # 폴백: 최후의 안전장치
        log.exception("예기치 않은 오류")
        return no_event(name, f"분석 중 오류가 발생했습니다 ({type(e).__name__}: {e})", debug)


def _build(df, name, metric, axis, debug):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    roles = classify(df)
    debug["roles"] = {c: f"{r} ({why})" for c, (r, why) in roles.items()}
    for c, v in debug["roles"].items():
        log.info(f"컬럼 {c}: {v}")
    if axis:
        roles[axis] = ("axis", "사용자 지정")
    measures = [c for c, (r, _) in roles.items() if r == "measure"]
    flags = [c for c, (r, _) in roles.items() if r == "flag"]
    groups = [c for c, (r, _) in roles.items() if r == "group"]
    axes = [c for c, (r, _) in roles.items() if r == "axis"]
    if not measures and not flags:
        return no_event(name, "분석할 수치 컬럼이 없습니다", debug)
    for f in flags:                                          # 0/1 컬럼은 비율 지표로 취급
        df[f] = df[f].astype(float)
    metrics = measures + flags
    if not axes:
        log.info("순서 축 없음 -> 그룹 비교 모드")
        return group_compare(df, name, metrics, groups, debug)

    ax = axes[0]
    series, meta = make_series(df, ax, metrics)
    debug["axis"] = ax; debug["rules"] = meta["rules"]
    log.info(f"축={ax}, 칸 수={len(series)}, 칸 간격={meta['step']:g}, 집계={meta['rules']}")
    if len(series) < MIN_BINS:
        return group_compare(df, name, metrics, groups, debug) if groups else \
            no_event(name, f"시간 축 칸이 {len(series)}개뿐이라 사건을 찾을 수 없습니다", debug)

    # 지표별 최대 변화 창 탐지 -> 가장 뚜렷한 지표를 주 지표로
    wins = {}
    for m in metrics:
        w = detect_window(series[m].astype(float).values)
        if w: wins[m] = w
    if not wins:
        return no_event(name, "변화를 계산할 수 있는 지표가 없습니다", debug)
    if metric and metric in wins:
        main = metric
    else:
        big = {m: v for m, v in wins.items() if abs(v["pct"]) >= 5} or wins   # 변화폭 5% 이상만 후보
        main = max(big, key=lambda m: big[m]["z"])
    W = wins[main]
    debug["metric_candidates"] = {m: {"z": r1(v["z"]), "pct": r1(v["pct"])} for m, v in wins.items()}
    log.info(f"주 지표 후보: {debug['metric_candidates']}")
    log.info(f"선택된 주 지표: {main} (z={W['z']:.1f}, 변화 {W['pct']:+.1f}%)")
    if not (np.isfinite(W["z"]) and np.isfinite(W["pct"])):
        return no_event(name, f"{main}의 결측이 많아 변화를 계산할 수 없습니다", debug)
    if W["z"] < 3 or abs(W["pct"]) < 5:
        return no_event(name, f"뚜렷한 사건을 찾지 못했습니다 (최대 변화 {main} {W['pct']:+.1f}%, 노이즈 수준)", debug)

    X = series["_x"].values
    is_date, step = meta["is_date"], meta["step"]
    e0, w, b0 = W["e0"], W["w"], W["b0"]
    ev_end = min(e0 + w, len(series))
    P = lambda i: fmt_axis(X[i], is_date, step)
    base_txt, ev_txt = f"{P(b0)} ~ {P(e0-1)}", f"{P(e0)} ~ {P(ev_end-1)}"
    log.info(f"기준 구간 {base_txt} | 사건 구간 {ev_txt}")

    ev = Ev()
    kind = "비율 지표" if main in flags else ("합계" if meta["rules"][main] == "sum" else "평균")
    ev.add(f"{main} {kind} 변화율", r1(W["pct"]), "%")
    ev.add(f"{main} 기준 구간 값", r1(W["base_mean"]), "")
    ev.add(f"{main} 사건 구간 값", r1(W["ev_mean"]), "")

    # 주 지표의 변화 시작 시점
    y = series[main].astype(float).values
    main_on = onset_index(y, W["base_mean"], W["base_std"], e0, ev_end)
    if main_on is not None:
        ev.add(f"{main} 변화 시작 시점", float(f"{X[main_on]:g}") if not is_date else P(main_on), "")

    # 그룹별 기여도(합계 지표) / 그룹별 변화율(평균·비율 지표)
    sub = df.copy()
    sub["_x"], _ = to_axis_numeric(sub[ax])
    in_base = (sub["_x"] >= X[b0]) & (sub["_x"] < X[e0])
    in_ev = (sub["_x"] >= X[e0]) & (sub["_x"] < (X[ev_end] if ev_end < len(X) else np.inf))
    nb, ne = max(e0 - b0, 1), max(ev_end - e0, 1)
    top_groups = []
    for g in groups:
        if sub[g].nunique() > 30: continue
        if meta["rules"][main] == "sum":
            b = sub[in_base].groupby(g)[main].sum() / nb
            e = sub[in_ev].groupby(g)[main].sum() / ne
            tot = e.sum() - b.sum()
            if abs(tot) < 1e-9 or abs(tot) / max(abs(b.sum()), 1e-9) < 0.05: continue
            share = ((e - b) / tot * 100).sort_values(ascending=False)
            top_groups.append((g, share.index[0], float(share.iloc[0]), "기여도"))
        else:
            b = sub[in_base].groupby(g)[main].mean()
            e = sub[in_ev].groupby(g)[main].mean()
            ch = ((e - b) / b.abs().replace(0, np.nan) * 100).dropna()
            if len(ch):
                k = ch.abs().idxmax()
                top_groups.append((g, k, float(ch[k]), "변화율"))
    top_groups.sort(key=lambda t: abs(t[2]), reverse=True)
    for g, k, v, kind2 in top_groups[:2]:
        if kind2 == "기여도" and v < 40:   # 한쪽으로 쏠리지 않으면 증거로 쓰지 않음
            log.info(f"그룹 {g}: 쏠림 약함({v:.1f}%) - 제외")
            continue
        ev.add(f"{g}={k} {main} {kind2}", r1(v), "%")
        log.info(f"그룹 증거: {g}={k} {kind2} {v:.1f}%")
    debug["top_groups"] = [(g, str(k), r1(v)) for g, k, v, _ in top_groups[:4]]

    # 다른 수치 컬럼(공변량): 변화율, 시작 시점, 주 지표보다 앞섰는지
    cov_rows = []
    for m in metrics:
        if m == main: continue
        yy = series[m].astype(float).values
        bm, bs = yy[b0:e0].mean(), yy[b0:e0].std()
        em = yy[e0:ev_end].mean()
        if abs(bm) < 1e-9: continue
        pct = (em - bm) / abs(bm) * 100
        z = abs(em - bm) / (bs / np.sqrt(max(e0 - b0, 1)) + bs * 0.5 + abs(bm) * 0.01 + 1e-9)
        on = onset_index(yy, bm, bs, max(b0 + 3, e0 - w), ev_end)
        cov_rows.append({"col": m, "pct": pct, "z": z, "on": on})
    cov_rows = [c for c in cov_rows if c["z"] >= 3 and abs(c["pct"]) >= 5]
    cov_rows.sort(key=lambda c: c["z"], reverse=True)
    debug["covariates"] = [{"col": c["col"], "pct": r1(c["pct"]), "z": r1(c["z"]),
                            "onset": None if c["on"] is None else P(c["on"])} for c in cov_rows]
    for c in cov_rows[:3]:
        ev.add(f"{c['col']} 변화율", r1(c["pct"]), "%")
        if c["on"] is not None and main_on is not None:
            lead = (X[main_on] - X[c["on"]]) / step          # 칸 단위
            if lead > 0.5:
                ev.add(f"{c['col']} 변화가 {main}보다 먼저 시작한 칸 수", r1(lead), "칸")
            elif lead < -0.5:
                ev.add(f"{c['col']} 변화가 {main}보다 늦게 시작한 칸 수", r1(-lead), "칸")
            else:
                ev.add(f"{c['col']} 변화가 {main}와 동시에 시작", 0, "칸")
        log.info(f"공변량 {c['col']}: {c['pct']:+.1f}% z={c['z']:.1f}")

    # 데이터 품질: 결측과 값 멈춤
    na = float(df[main].isna().mean() * 100)
    if na > 0: ev.add(f"{main} 결측 비율", r1(na), "%")
    flat = int((np.diff(y[e0:ev_end]) == 0).sum()) if ev_end - e0 > 1 else 0
    if flat >= max(3, (ev_end - e0) // 2):
        ev.add(f"{main} 사건 구간에서 값이 변하지 않은 칸 수", flat, "칸")

    return {"case_name": f"{main} 변화 사건", "period": {"baseline": base_txt, "event": ev_txt},
            "evidence": ev.items, "status": "ok", "main_metric": main, "axis": ax,
            "window": {"b0": int(b0), "e0": int(e0), "ev_end": int(ev_end)}, "debug": debug}


# ---------- 6. 폴백: 순서 축이 없을 때 그룹 비교 ----------
def group_compare(df, name, metrics, groups, debug):
    """시간 축이 없으면, 그룹 간 평균 차이가 가장 큰 (그룹, 지표) 조합을 찾는다"""
    best = None
    for g in groups:
        k = df[g].nunique()
        if k < 2 or k > 30: continue
        for m in metrics:
            s = df[[g, m]].dropna()
            means = s.groupby(g)[m].mean()
            sd = s[m].std()
            if sd == 0 or len(means) < 2: continue
            hi, lo = means.idxmax(), means.idxmin()
            d = (means[hi] - means[lo]) / sd          # 표준화된 차이
            if best is None or d > best["d"]:
                best = {"g": g, "m": m, "hi": hi, "lo": lo, "d": d, "mh": means[hi], "ml": means[lo]}
    if not best or best["d"] < 0.5:
        return no_event(name, "순서 축이 없고 그룹 간 뚜렷한 차이도 찾지 못했습니다", debug)
    ev = Ev()
    pct = (best["mh"] - best["ml"]) / abs(best["ml"]) * 100 if best["ml"] else float("nan")
    ev.add(f"{best['m']} 그룹 간 평균 차이 ({best['g']}={best['hi']} 대 {best['lo']})",
           r1(pct) if pct == pct else "계산불가", "%")
    ev.add(f"{best['g']}={best['hi']} {best['m']} 평균", r1(best["mh"]), "")
    ev.add(f"{best['g']}={best['lo']} {best['m']} 평균", r1(best["ml"]), "")
    ev.add("표준화 효과 크기", r1(best["d"]), "")
    log.info(f"그룹 비교: {best['g']} 별 {best['m']} (효과 크기 {best['d']:.2f})")
    return {"case_name": f"{best['g']}별 {best['m']} 차이",
            "period": {"baseline": f"{best['g']}={best['lo']}", "event": f"{best['g']}={best['hi']}"},
            "evidence": ev.items, "status": "ok", "main_metric": best["m"],
            "group_col": best["g"], "lo": str(best["lo"]), "hi": str(best["hi"]), "debug": debug}

# ---------- 7. 실행 ----------
if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = {a.split("=")[0]: a.split("=")[1] for a in sys.argv[1:] if a.startswith("--") and "=" in a}
    if not args:
        print("사용법: python export_case.py 파일.csv [출력.json] [--metric=컬럼] [--axis=컬럼]"); sys.exit(1)
    d = read_csv_any(args[0])
    out = build_case_from_df(d, name=args[0], metric=opt.get("--metric"), axis=opt.get("--axis"))
    path = args[1] if len(args) > 1 else args[0].rsplit(".", 1)[0] + "_case.json"
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print(f"\n[{out['status']}] {out['case_name']} | 기간 {out['period']}")
    for e in out["evidence"]:
        print(f"  {e['id']} {e['label']}: {e['value']}{e['unit']}")
    print("저장:", path)
