"""app.py - CD ROOT 관제 터미널 화면. 실행: python -m streamlit run app.py
흐름: 사이드바 입력 -> 상태 갱신 -> 레이아웃(컨테이너/슬롯) -> paint()로 그리기 -> 토론 실행 중에는 이벤트마다 paint()"""
import sys, re, json, uuid, time, pathlib
import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import config, export_case as ec, agents, debate, ui, gpu_queue, quota, store
from logger import get_logger, set_session, get_logs, clear_logs, step

log = get_logger("app")
st.set_page_config(page_title="CD ROOT", page_icon="◈", layout="wide", initial_sidebar_state="auto")
st.html(ui.inject_css())

CY, PU, WARN, CRIT, OK, MUTE = "#00D9FF", "#A78BFA", "#FFB547", "#FF4D6D", "#3DDC97", "#6B7C8C"
S = st.session_state
S.setdefault("sid", uuid.uuid4().hex[:8])
set_session(S["sid"])                                  # 이후 로그에 내 세션 표시
for k in ("src_id", "df", "name", "case", "result", "out", "notice", "pending_metric", "metric_used", "pending_upload", "pending_demo"):
    S.setdefault(k, None)
if st.query_params.get("present") and "present" not in S:   # 주소 끝에 ?present=1 을 붙이면 발표 모드로 시작
    S["present"] = True
if S.get("present"):                                     # 발표 모드: 글자를 1.2배로
    st.html(ui.PRESENT_CSS)
S.setdefault("view", "intro")                          # intro(소개) / app(분석 화면)
S.setdefault("events", [])
S.setdefault("core", "STANDBY")
S.setdefault("go", False)
S.setdefault("load_saved", False)
S.setdefault("mismatch", None)

DEMOS = {"주문 데이터 · 이상 사건 있음": "orders.csv", "재고 데이터 · 사건 없음 예시": "inventory.csv"}
audio_slot = st.empty()


# ---------- 상태 갱신 ----------
def ev(tag, msg, color=CY):
    """타임라인에 사건 기록 추가"""
    S.events.append({"t": time.strftime("%H:%M:%S"), "tag": tag, "msg": msg, "c": color})


def play(kind):
    """음성 안내를 큐에 넣는다. 실제 재생은 실행 끝(flush_audio)에서 한다."""
    S.setdefault("audio_q", []).append(kind)


def flush_audio():
    """큐의 음성을 재생한다. 연달아 쌓인 detecting+found는 이어 붙인 파일 하나로 한 번에 재생한다.
    (서버가 앞 음성이 끝나길 기다리면 그동안 실행이 끝나지 않아 옛 화면이 흐리게 남기 때문에 기다리지 않는다.)
    파일이 없거나 실패하면 소리 없이 넘어간다."""
    q, S["audio_q"] = S.get("audio_q", []), []
    if q[:2] == ["detecting", "found"]:
        q = ["detect_found"] + q[2:]
    for kind in q:
        try:
            f = config.AUDIO_DIR / config.TTS_CLIPS[kind]
            if not (config.TTS_ENABLED and S.get("voice", True) and f.exists()):
                continue
            audio_slot.empty()                         # 같은 파일을 다시 틀어도 새 요소로 재생되게 비운다
            audio_slot.audio(f.read_bytes(), format="audio/mpeg", autoplay=True)
            log.info(f"음성 재생: {kind}")
        except Exception as e:
            log.warning(f"음성 재생 건너뜀: {e}")


def load_source(src_id, reader, name):
    """CSV를 읽어 상태에 저장하고 사건 탐지까지 수행. 실패하면 안내 문구만 남긴다."""
    S.out = S.result = S.case = S.df = None
    S.notice, S.events, S.core, S.metric_used, S.mismatch = None, [], "SCANNING", None, None
    S.pop("gpu_live", None)
    S.pop("metric_pick", None)
    clear_logs()
    try:
        with step(log, f"CSV 읽기 ({name})"):
            df = reader()
        if len(df) > config.MAX_UPLOAD_ROWS:
            S.notice = f"행이 {len(df):,}개로 많아 앞쪽 {config.MAX_UPLOAD_ROWS:,}행만 분석합니다."
            df = df.head(config.MAX_UPLOAD_ROWS)
        S.df, S.name, S.src_id = df, name, src_id
        S.view = "app"                                 # 데이터가 준비되면 분석 화면으로
        ev("데이터", f"{name} 읽기 완료 · {len(df):,}행 × {df.shape[1]}열", CY)
        play("detecting")
        detect()
    except Exception as e:
        log.error(f"CSV 읽기 실패: {type(e).__name__}: {e}")
        S.src_id, S.core, S.view = src_id, "STANDBY", "intro"
        S.notice = f"파일을 읽을 수 없습니다. 인코딩이나 구분자를 확인해 주세요. ({type(e).__name__})"


def detect(metric=None):
    """사건 탐지 + 가설 채점 + 장부 재계산 검증. 주 지표를 바꾸면 이걸 다시 호출한다."""
    S.out = None
    with step(log, "사건 탐지와 가설 채점"):
        S.case = ec.build_case_from_df(S.df, name=S.name, metric=metric)
        S.result = agents.analyze(S.df, S.case)
    if S.case["status"] == "ok" and S.result["status"] == "ok":
        S.mismatch = agents.verify_ledger(S.result["ctx"], S.result["ledger"])
        S.core = "ANOMALY DETECTED"
        ev("사건", f"사건 발견: {S.case['case_name']}", WARN)
        ev("채점", f"가설 {len(S.result['hypotheses'])}개를 코드가 채점했습니다 (장부 재계산 불일치 {len(S.mismatch)}건)", PU)
        play("found")
    else:
        S.core, S.mismatch = "NO ANOMALY", None
        ev("탐지", S.case["evidence"][0]["value"] if S.case["status"] != "ok" else "분석 중 문제가 발생했습니다", OK)


def pick_metric(m):
    """사건 피드에서 지표를 고르면 다음 실행 때 그 지표로 다시 계산"""
    S["pending_metric"] = m


def request_go():
    S["go"] = True


def request_saved():
    S["load_saved"] = True


def on_upload(key):
    """업로더에서 파일이 바뀌면(콜백) 다음 실행 때 처리하도록 맡겨 둔다. 소개 화면과 사이드바 어디서 올려도 같은 경로를 탄다."""
    if S.get(key) is not None:
        S["pending_upload"] = S[key]


def request_demo(fn):
    S["pending_demo"] = fn


def go_intro():
    S["view"] = "intro"


def go_app():
    S["view"] = "app"


# ---------- 사이드바: 데이터 입력과 메뉴 ----------
with st.sidebar:
    st.html('<div class="pn-t" style="margin:.2rem 0 .6rem"><i style="background:#00D9FF;box-shadow:0 0 6px #00D9FF"></i>DATA INGEST</div>')
    st.file_uploader("CSV 업로드", type=["csv"], label_visibility="collapsed", key="up_side", on_change=on_upload, args=("up_side",))
    st.html('<div class="sec">SAMPLE DATASETS</div>')
    for label, fn in DEMOS.items():
        st.button(label, use_container_width=True, key=f"demo_{fn}", on_click=request_demo, args=(fn,))
    S["voice"] = st.toggle("음성 안내", value=S.get("voice", True), disabled=not config.TTS_ENABLED)
    st.toggle("발표 모드 (글자 크게)", key="present", help="심사·발표 때 화면 전체 글자를 1.2배로 키웁니다")
    st.html('<div class="sec">MENU</div>')
    st.button("◂ 소개 · 사용법", use_container_width=True, key="menu_intro", on_click=go_intro)

# 콜백이 맡겨 둔 일을 여기서 처리한다 (업로드 / 시연 시작 / ?demo=orders 주소)
if S.pending_upload is not None:
    up_f, S.pending_upload = S.pending_upload, None
    if up_f.size > config.MAX_UPLOAD_MB * 1024 * 1024:
        S.notice = f"파일이 {config.MAX_UPLOAD_MB}MB를 넘습니다. 더 작은 파일을 올려 주세요."
    else:
        load_source(f"up:{up_f.file_id}", lambda: ec.read_csv_any(up_f), up_f.name)
if S.pending_demo:
    fn, S.pending_demo = S.pending_demo, None
    load_source(f"demo:{fn}", lambda: ec.read_csv_any(str(config.DATA_DIR / fn)), fn)
qd = st.query_params.get("demo")
if S.src_id is None and qd:
    fn = next((f for f in DEMOS.values() if f.startswith(str(qd))), None)
    if fn:
        load_source(f"demo:{fn}", lambda: ec.read_csv_any(str(config.DATA_DIR / fn)), fn)

if S.pending_metric is not None and S.df is not None:   # 사건 피드 클릭 반영
    m, S.pending_metric = S.pending_metric, None
    S.metric_used = None if m == "__auto__" else m
    ev("지표", "주 지표 자동 선택으로 재조사" if m == "__auto__" else f"주 지표를 '{m}'로 바꿔 재조사", PU)
    detect(S.metric_used)



def gh_client():
    return gpu_queue.GH(config.GPU_QUEUE_REPO, config.GPU_QUEUE_TOKEN, base=config.GPU_QUEUE_API, timeout=6)


@st.cache_resource
def hourly_cap():
    return quota.HourlyCap(config.AI_RUNS_PER_HOUR)


def allow_ai_run():
    """AI 검토를 지금 실행해도 되는지 본다 (방문자당 횟수 + 서버 전체 시간당 횟수). 허용하면 사용을 기록한다. 반환: (허용 여부, 안내 문구)"""
    if S.get("ai_runs", 0) >= config.AI_RUNS_PER_SESSION:
        return False, "이 방문에서 쓸 수 있는 AI 검토 횟수를 모두 썼어요. ‘저장된 결과 불러오기’로 결과를 볼 수 있어요."
    if not hourly_cap().try_acquire():
        return False, "지금은 AI 검토 요청이 많아 잠시 쉬고 있어요(공유 예산 보호). ‘저장된 결과 불러오기’를 쓰거나 잠시 뒤에 다시 시도해 주세요."
    S["ai_runs"] = S.get("ai_runs", 0) + 1
    log.info(f"AI 검토 허용: 이 방문 {S['ai_runs']}/{config.AI_RUNS_PER_SESSION}, 서버 전체 최근 1시간 {hourly_cap().used()}/{config.AI_RUNS_PER_HOUR}")
    return True, ""


@st.cache_data(ttl=10, show_spinner=False)
def recent_saved(sid):
    """이 방문에서 Supabase에 저장된 검토 목록 (10초 캐시)"""
    return store.recent(sid)


def save_result():
    """방금 끝난 검토 결과의 요약을 Supabase에 저장한다 (원본 행 제외). 실패해도 화면은 그대로 동작한다."""
    if not store.enabled() or not S.out or S.out.get("from_cache"):
        return
    label = f"demo:{S.name}" if str(S.src_id).startswith("demo:") else "upload"     # 업로드 파일 이름은 저장하지 않는다
    ok, msg = store.save(store.build_row(S["sid"], label, S.df.shape, S.case, S.result, S.out))
    recent_saved.clear()
    ev("저장", "검토 결과를 Supabase에 저장했어요" if ok else f"Supabase 저장에 실패했어요 (화면에는 영향 없음): {msg}", OK if ok else WARN)
    log.info(f"Supabase 저장: {ok} {msg}")


@st.cache_data(ttl=15, show_spinner=False)
def gpu_status():
    """틸론 워커의 하트비트를 본다 (15초 캐시). 연동 설정이 없으면 None."""
    return gpu_queue.status(gh_client(), max_age=config.GPU_ONLINE_MAX_AGE_SEC) if config.GPU_QUEUE_ENABLED else None


_gs = gpu_status()
_gpu_chip = (("TILON GPU · SAVED RESULTS", PU, False) if _gs is None else
             (("TILON GPU ONLINE", OK, True) if _gs["online"] else ("TILON GPU · SAVED RESULTS", PU, False)))
st.html(ui.topbar(config.LLM_ENABLED, bool(S.get("voice", True)) and config.TTS_ENABLED, S.name, S["sid"], gpu=_gpu_chip))


def chart_data(res):
    """이상 탐지 그래프에 넘길 값 (분석 결과의 시계열/그룹 평균에서 그대로 꺼낸다)"""
    ctx = res.get("ctx") or {}
    if ctx.get("mode") == "time":
        X, mt = ctx["X"], ctx["meta"]
        return {"kind": "time", "y": ctx["series"][ctx["main"]].astype(float).tolist(),
                "b0": int(ctx["b0"]), "e0": int(ctx["e0"]), "ee": int(ctx["ee"]), "on": ctx.get("main_on"),
                "x0": ec.fmt_axis(X[0], mt["is_date"], mt["step"]), "x1": ec.fmt_axis(X[-1], mt["is_date"], mt["step"])}
    if ctx.get("mode") == "group":
        df, m = ctx["df"], ctx["main"]
        return {"kind": "group", "bars": [(f"{ctx['g']}={ctx['hi']}", float(df.loc[ctx["is_hi"], m].mean())),
                                          (f"{ctx['g']}={ctx['lo']}", float(df.loc[ctx["is_lo"], m].mean()))]}
    return None


@st.cache_data(show_spinner=False)
def gpu_summary():
    """저장된 틸론 결과(orders 사례)에서 발표용 핵심 숫자만 꺼낸다. 없으면 None."""
    d = config.RESULTS_DIR / "orders"
    try:
        A, B, sel = (json.loads((d / f).read_text(encoding="utf-8")) for f in ("case_summary_A.json", "case_summary_B.json", "selected.json"))
        return {"a": A["coverage"], "b": B["coverage"], "bad": len(B.get("unmatched", [])), "vram": sel.get("peak_vram_gb")}
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def story_data():
    """소개 스토리에 쓸 실제 값: 시연 데이터를 코드로 분석한 결과. 저장된 AI 토론이 있으면 그 발언도 쓴다. 실패하면 None."""
    try:
        df = ec.read_csv_any(str(config.DATA_DIR / "orders.csv"))
        case = ec.build_case_from_df(df, name="orders.csv")
        res = agents.analyze(df, case)
        if case["status"] != "ok" or res["status"] != "ok":
            return None
        out = debate.load_cache(res)
        H, main = res["hypotheses"], case["main_metric"]
        cand = case["debug"]["metric_candidates"][main]
        short = lambda t, n=118: t if len(t) <= n else t[:n].rstrip() + "…"
        if out and out.get("claims"):
            agents_ = [(f"AGENT {c['hypothesis_id']}", short(c.get("claim", ""))) for c in out["claims"][:3] if c.get("claim")]
            rb = (out.get("rebuttals") or [{}])[0]
            verifier = (f"VERIFIER → {rb.get('hypothesis_id', '')}", short(rb.get("point", ""))) if rb.get("point") else None
            ung = sum(len(c.get("ungrounded", [])) for c in out["claims"]) + sum(len(r.get("ungrounded", [])) for r in out.get("rebuttals", []))
            mode = "SAVED AI RUN"
        else:                                           # 저장된 토론이 없으면 규칙 기반 가설 문구로 대신 보여준다
            agents_ = [(f"AGENT {h['id']}", short(h["claim"])) for h in H[:3]]
            verifier, ung, mode = None, None, "RULE BASED PREVIEW"
        return {"header": list(df.columns[:6]), "rows": [[str(v) for v in r] for r in df.iloc[:5, :6].values.tolist()],
                "shape": f"{len(df):,}행 × {df.shape[1]}열", "chart": chart_data(res), "main": main,
                "change": f"{cand['pct']:+}%", "z": cand["z"], "period": re.sub(r"\d{4}-", "", case["period"]["event"]),
                "agents": agents_, "verifier": verifier, "mode": mode, "ledger_n": len(res["ledger"].rows),
                "mismatch": len(agents.verify_ledger(res["ctx"], res["ledger"])), "ungrounded": ung,
                "top": {"id": H[0]["id"], "title": H[0]["title"], "kind": H[0]["kind"], "score": H[0]["total"],
                        "label": res["conclusion"]["strength"]},
                "hyps": [(h["id"], h["title"], h["kind"], h["total"]) for h in H[:4]], "gpu": gpu_summary()}
    except Exception as e:
        log.warning(f"소개 화면용 시연 분석 실패: {type(e).__name__}: {e}")
        return None


def render_intro():
    """입장 화면. [소개] 히어로(타깃) → 스크롤하며 5단계(무엇을 넣으면 누가 어디서 무엇을 하는지) → 시작하기/업로드. [사용 가이드] 자세한 조건·설명."""
    if S.notice:
        st.warning(S.notice)
    t_story, t_guide = st.tabs(["소개", "사용 가이드"])
    with t_story:
        d = story_data()
        st.html(ui.story_hero([("쇼핑몰 운영자", "취소가 갑자기 늘었는데, 왜?", CY),
                               ("설비·품질 담당자", "진동이 올랐는데, 뭐가 먼저였지?", WARN),
                               ("실험·연구 담당자", "두 그룹이 왜 갈라졌지?", PU)]))
        _, mid, _ = st.columns([1, 1, 1])
        mid.button("▶ 시연 데이터로 바로 시작", key="hero_start", type="primary", on_click=request_demo, args=("orders.csv",), use_container_width=True)
        if d:
            st.html(ui.story_row("01 · INPUT", "이런 CSV를<br>올리면", "날짜, 숫자, 그룹이 섞여 있으면 충분해요.<br>열 이름은 자유예요.",
                                 "YOU", "내 브라우저 → CD ROOT 서버", ui.mini_table("orders.csv", d["shape"], d["header"], d["rows"]), OK))
            st.html(ui.story_row("02 · DETECT", "코드가 사건을<br>찾아냅니다", "달라진 구간을 계산으로 짚어요.<br>이 단계에는 AI를 쓰지 않아요.",
                                 "CODE", "CD ROOT 서버",
                                 ui.chart(d["chart"], tag=d["main"]) + ui.stats([(d["change"], "변화율", WARN), (d["period"], "사건 구간", CY), (f"z={d['z']}", "변화의 확실성", PU)]),
                                 CY, rev=True))
            items = [(l, t, PU) for l, t in d["agents"]] + ([(d["verifier"][0], d["verifier"][1], WARN)] if d["verifier"] else [])
            st.html(ui.story_row("03 · DEBATE", "AI 에이전트들이<br>서로 반박합니다", "가설마다 에이전트 한 명.<br>증거 장부에 있는 숫자만 쓸 수 있어요.",
                                 "AI", "외부 AI (Claude API)", ui.bubbles(items, d["mode"]), PU))
            ung = "—" if d["ungrounded"] is None else str(d["ungrounded"])
            st.html(ui.story_row("04 · VERIFY", "코드가 숫자를<br>다시 계산합니다", "장부에 없는 숫자는 걸러서 표시해요.<br>하나라도 어긋나면 알려요.",
                                 "CODE", "CD ROOT 서버",
                                 ui.stats([(str(d["ledger_n"]), "증거 장부 항목", CY), (str(d["mismatch"]), "재계산 불일치", OK), (ung, "발언 속 장부 밖 숫자", WARN)]), CY, rev=True))
            st.html(ui.story_row("05 · RESULT", "원인 ‘후보’와<br>점수를 드립니다", "확정이 아니라 후보예요.<br>확인할 일도 함께 드려요.",
                                 "CODE", "내 브라우저 (결과 화면)", ui.result_card(d["top"], d["hyps"]), OK))
            if d.get("gpu"):
                g = d["gpu"]
                st.html(ui.story_row("+ · TILON GPU", "교내 GPU도<br>함께 씁니다", "소형 모델(Qwen2.5-1.5B)이 사건 요약 카드를 만들고,<br>두 가지 프롬프트를 코드로 채점해 비교했어요.",
                                     "TILON GPU", "교내 GPU (결과는 미리 실행해 저장)",
                                     ui.stats([(f"{g['a']}→{g['b']}", "증거 커버리지 A → B", PU), (str(g["bad"]), "장부 밖 숫자", OK), (f"{g['vram']}GB", "최대 VRAM", CY)]), PU, rev=True))
            st.caption("위 화면의 표·그래프·점수는 시연 데이터(합성 이커머스 주문)를 이 앱이 실제로 분석한 값입니다.")
        st.html('<div class="iw"><div class="hero2" style="min-height:0;padding:3.2rem 1rem 1rem"><div class="k">NOW TRY IT</div>'
                '<h1>이제 직접<br><em>해보세요</em></h1><p class="sub">시연 데이터로 바로 시작하거나, 내 CSV를 올려 보세요.</p></div></div>')
        with st.container(key="cta"):
            ca, cb = st.columns(2, gap="small")
            with ca:
                st.button("▶ 시연 데이터로 시작하기", type="primary", key="start_demo", on_click=request_demo, args=("orders.csv",), use_container_width=True)
                st.button("사건이 없는 데이터는 어떻게 보이나요? (재고 예시)", key="start_noevent", on_click=request_demo,
                          args=("inventory.csv",), use_container_width=True)
            with cb:
                st.file_uploader("CSV 업로드", type=["csv"], label_visibility="collapsed", key="up_intro", on_change=on_upload, args=("up_intro",))
                st.caption(f"CSV · {config.MAX_UPLOAD_MB}MB 이하 · 자세한 조건은 ‘사용 가이드’ 탭")
        if S.df is not None:
            st.button("◂ 분석 화면으로 돌아가기", key="back_app", on_click=go_app)
    with t_guide:
        g1, g2 = st.columns(2, gap="small")
        with g1:
            st.html(ui.guide_data())
            st.html(ui.guide_score())
            st.html(ui.guide_gpu())
        with g2:
            st.html(ui.guide_where())
            st.html(ui.guide_limits())


if S.view != "app" or S.df is None:                    # 소개 화면에서는 여기서 멈춘다
    render_intro()
    flush_audio()
    st.stop()

tab_ov, tab_inv, tab_arc, tab_agt = st.tabs(["결과 보기", "AI 토론·보고서", "증거 자료", "에이전트 상태"])


# ---------- 화면 데이터 만들기 ----------
def ok_case():
    return S.df is not None and S.case and S.case["status"] == "ok" and S.result and S.result["status"] == "ok"


ACTIONS = {"covariate": "해당 컬럼이 사건 시점 즈음 바뀐 이유(운영·정책·단가 변경 등)를 확인하세요",
           "origin": "이 지표가 먼저 변한 원인이 데이터 밖(프로모션, 정책, 외부 이벤트)에 있는지 확인하세요",
           "group": "쏠린 그룹만 따로 떼어 변화 시점과 원인을 확인하세요",
           "data": "원천 데이터의 결측·중복·값 멈춤을 고친 뒤 다시 분석하세요",
           "direct": "다른 조건(층별)에서도 같은 차이가 유지되는지 다시 확인하세요",
           "confound": "숨은 그룹 값을 통제한 비교로 다시 검증하세요"}


def risk_list():
    """④ 주의할 점과 검증 결과: (수준, 문장, 색)"""
    res, out = S.result, S.out
    r = []
    for w in res["conclusion"].get("warnings", []):
        r.append(("WARN", w, WARN))
    mm = out.get("ledger_mismatch") if out else S.mismatch
    if mm:
        r.append(("CRIT", f"숫자를 다시 계산했더니 어긋난 항목이 있어요: {mm}", CRIT))
    elif mm == []:
        r.append(("PASS", "증거 기록의 모든 숫자를 코드가 다시 계산해 일치함을 확인했어요", OK))
    if res["conclusion"]["strength"] == "근거 부족":
        r.append(("WARN", "1위 후보의 근거 강도가 낮아 근거가 부족해요", WARN))
    if res["conclusion"].get("gap") is not None and res["conclusion"]["gap"] < 0.1:
        r.append(("INFO", "1위와 2위의 점수 차이가 작아 여러 원인이 함께 작용했을 수 있어요", WARN))
    for w in (out or {}).get("warnings", []):
        r.append(("WARN", w, WARN))
    return r


def certainty(z):
    """변화의 확실성(z)을 쉬운 말로"""
    return "높음" if z == z and z >= 8 else ("보통" if z == z and z >= 3 else "낮음")


def data_caption():
    ctx = (S.result or {}).get("ctx") or {}
    mode = {"time": "시간 흐름 분석", "group": "그룹 간 비교"}.get(ctx.get("mode"), "사건 없음")
    return f"{S.name} · {len(S.df):,}행 × {S.df.shape[1]}열 · {mode}"


def summary_view():
    """① 한 문장 요약(HTML)과 핵심 숫자 3개"""
    case = S.case
    cand = (case.get("debug", {}).get("metric_candidates") or {}).get(case["main_metric"])
    if "group_col" in case:                                           # 시간 축이 없는 그룹 비교
        val = case["evidence"][0]["value"]
        num = isinstance(val, (int, float)) and val == val
        sent = (f"<b>{ui.e(case['group_col'])}</b> 컬럼의 「{ui.e(case['hi'])}」 그룹은 「{ui.e(case['lo'])}」 그룹보다 <b>{ui.e(case['main_metric'])}</b> 평균이 "
                + (f"<b style='color:{WARN}'>{abs(val)}% {'더 커요' if val > 0 else '더 작아요'}</b>." if num else "크게 달라요."))
        return sent, [(f"{x['value']}{x['unit']}", x["label"], c) for x, c in zip(case["evidence"][:3], (WARN, CY, PU))]
    pct = cand["pct"]
    word = "늘었어요" if pct > 0 else "줄었어요"
    short = lambda s: re.sub(r"\d{4}-", "", str(s))                  # 연도를 빼서 문장을 짧게
    sent = (f"<b>{ui.e(short(case['period']['event']))}</b> 동안 <b>{ui.e(case['main_metric'])}</b> 값이 평소보다 "
            f"<b style='color:{WARN}'>{abs(pct)}% {word}</b>.")
    return sent, [(f"{pct:+}%", "평소 대비 변화", WARN), (short(case["period"]["event"]), "사건이 일어난 기간", CY),
                  (certainty(cand["z"]), f"변화의 확실성 (z={cand['z']})", PU)]


def conclusion_view():
    """④ 결론 카드에 쓸 값. AI 검토 전에는 코드 점수 기준의 잠정 결과"""
    res, out = S.result, S.out
    H, concl = res["hypotheses"], res["conclusion"]
    top = H[0]
    claim = top["claim"]
    if out:
        claim = next((c["claim"] for c in out.get("claims", []) if c["hypothesis_id"] == top["id"] and c.get("claim")), claim)
    acts, seen = [], set()
    for h in H[:3]:
        if h["kind"] not in seen and h["kind"] in ACTIONS:
            seen.add(h["kind"]); acts.append(ACTIONS[h["kind"]])
    acts.append("가설은 서로 배타적이지 않으니 상위 2~3개를 함께 검토하세요")
    return {"done": out is not None, "ai": bool(out and out.get("mode") == "llm"), "title": top["title"], "claim": claim, "score": top["total"],
            "label": concl["strength"], "actions": acts[:4], "risks": risk_list()}


def metric_picker():
    """다른 지표로 다시 보기 (고르면 그 지표로 사건 탐지부터 다시 한다)"""
    cands = S.case.get("debug", {}).get("metric_candidates") or {}
    if not cands:
        return
    order = ["__auto__"] + [m for m, _ in sorted(cands.items(), key=lambda kv: -(kv[1]["z"] if kv[1]["z"] == kv[1]["z"] else -1))]

    def fmt(m):
        if m == "__auto__":
            return "자동 선택 (변화가 가장 뚜렷한 지표)"
        v = cands[m]
        return f"{m}   {v['pct']:+}%" if v["pct"] == v["pct"] else f"{m}   계산 불가"
    cur = S.metric_used if S.metric_used in cands else "__auto__"
    st.selectbox("다른 지표로 다시 보기", order, index=order.index(cur), format_func=fmt, key="metric_pick",
                 on_change=lambda: pick_metric(S["metric_pick"]), help="변화가 큰 순서로 정렬돼 있어요. 고르면 그 지표로 처음부터 다시 분석합니다.")


def paint():
    """슬롯을 현재 상태로 다시 그린다 (토론 이벤트마다 호출). 조사가 끝났을 때(S.out 있음)만 완료 애니메이션이 붙는다."""
    if S.df is None or "core" not in L:
        return
    case, res = S.case, S.result
    done = S.out is not None
    main = case.get("main_metric", "-")
    cand = (case.get("debug", {}).get("metric_candidates") or {}).get(main)
    win = case.get("window")
    L["core"].html(ui.core(S.core, sub=str(S.name), metric=main, change=f"{cand['pct']:+}%" if cand else "-",
                           window=f"{win['ev_end'] - win['e0']} BINS" if win else "-"))
    L["log"].html(ui.timeline(S.events[-6:], anim=done))
    if config.LLM_ENABLED:
        if S.get("quota_msg"):
            L["quota"].warning(S["quota_msg"])
        else:
            left = max(0, config.AI_RUNS_PER_SESSION - S.get("ai_runs", 0))
            L["quota"].caption(f"AI 검토는 한 번 방문에 {config.AI_RUNS_PER_SESSION}번까지예요 (공유 예산을 아끼기 위한 제한 · 남은 횟수 {left}번). "
                               "‘저장된 결과 불러오기’는 횟수와 상관없이 쓸 수 있어요.")
    v = conclusion_view()
    L["s2"].html(ui.candidates(res["hypotheses"], v["label"], v["claim"], anim=done))
    L["s4"].html(ui.conclusion(v))


# ---------- 결과 보기: 번호 순서대로 위에서 아래로 (① 무슨 일 → ② 원인 후보 → ③ AI 검토 → ④ 결론) ----------
L = {}
with tab_ov:
    if S.notice:
        st.warning(S.notice)
    st.caption(data_caption())
    st.html(ui.sec_head("1", "무슨 일이 일어났나요?", "코드가 CSV에서 찾은 사건이에요", "코드가 계산", WARN, first=True))
    if S.case["status"] != "ok":
        st.html(ui.panel("사건을 찾지 못했어요", f'<div class="txt">{ui.e(S.case["evidence"][0]["value"])}</div>'
                         '<div class="txt dim" style="margin-top:.4rem">아래에서 다른 지표를 골라 보거나, 다른 CSV를 올려 보세요. 날짜 열과 숫자 열이 함께 있는 데이터가 가장 잘 맞아요.</div>', tone=OK))
        metric_picker()
    elif not ok_case():
        st.error("분석 중 문제가 생겼어요. 다른 지표나 다른 CSV로 시도해 주세요.")
        metric_picker()
    else:
        sentence, tiles = summary_view()
        st.html(f'<div class="sum-s">{sentence}</div>')
        with st.container(key="ov_sum"):
            cg, cr = st.columns([3, 2], gap="small")
            with cg:
                st.html(ui.chart(chart_data(S.result), tag=S.case.get("main_metric", "")))
            with cr:
                st.html(ui.stats(tiles, one=True))
                metric_picker()
        st.html(ui.sec_head("2", "원인 후보는 무엇인가요?", "사건이 왜 일어났는지, 후보 가설을 코드가 증거로 점수 매겼어요", "코드가 계산", PU))
        L["s2"] = st.empty()
        with st.expander("점수가 어떻게 나왔나요? (가설별 점수 근거 보기)"):
            st.html(ui.compare(S.result["hypotheses"]))
        st.html(ui.sec_head("3", "AI 에이전트가 함께 검토합니다", "후보마다 AI가 주장하고 반박한 뒤, 코드가 숫자를 다시 확인해요", "AI가 검토", PU))
        with st.container(key="ov_run"):
            ca, cb = st.columns([1.15, 1], gap="small")
            with ca:
                L["core"] = st.empty()
            with cb:
                st.html(ui.panel("이렇게 진행돼요", ui.ul(["후보마다 AI 에이전트가 한 명씩 <b>증거로 주장</b>해요",
                                                      "검증 에이전트가 허점을 찾아 <b>반박</b>해요",
                                                      "코드가 모든 숫자를 <b>다시 계산</b>해 틀린 숫자를 걸러내요"]), tag="AI + 코드", tone=PU))
                cached = debate.load_cache(S.result)
                with st.container(key="btnrow"):
                    c1, c2 = st.columns(2)
                    c1.button("▶ AI 검토 시작하기" if config.LLM_ENABLED else "▶ 규칙 기반으로 분석하기", type="primary", on_click=request_go, use_container_width=True)
                    c2.button("◂ 저장된 결과 불러오기", on_click=request_saved, disabled=not cached, use_container_width=True,
                              help="API 호출 없이 이전 검토 결과를 불러옵니다")
                if not config.LLM_ENABLED:
                    st.caption("AI가 꺼져 있어 규칙 기반 보고서로 동작해요.")
                L["quota"] = st.empty()                          # AI 검토 남은 횟수 / 한도 안내
                L["log"] = st.empty()
        st.html(ui.sec_head("4", "결론과 다음에 할 일", "원인을 확정하지 않고, 확인해 볼 후보와 방법을 알려드려요", "내가 확인", OK))
        L["s4"] = st.empty()
        st.caption("더 자세한 근거(계산된 증거 목록·컬럼 분류·증거 기록)는 ‘증거 자료’ 탭, AI 대화와 전체 보고서는 ‘AI 토론·보고서’ 탭에서 볼 수 있어요.")

# ---------- INVESTIGATION ----------
with tab_inv:
    chat = st.container()
    rep_slot = st.empty()


def show_claim(box, c, titles):
    with box.chat_message("assistant", avatar="🧑‍⚖️"):
        st.markdown(f"**AGENT {c['hypothesis_id']}** · {titles.get(c['hypothesis_id'], '')}")
        st.write(c.get("claim", ""))
        if c.get("weakness"):
            st.caption(f"자인한 약점: {c['weakness']}")
        if c.get("ungrounded"):
            st.warning(f"장부에 없는 숫자 포함: {c['ungrounded']}")


def show_rebuttal(box, r, titles):
    with box.chat_message("user", avatar="🛡️"):
        st.markdown(f"**VERIFIER → {r.get('hypothesis_id')}** · {titles.get(r.get('hypothesis_id'), '')}")
        st.write(r.get("point", ""))
        if r.get("ungrounded"):
            st.warning(f"장부에 없는 숫자 포함: {r['ungrounded']}")


def replay_chat(box, out, titles):
    for c in out.get("claims", []):
        show_claim(box, c, titles)
    for r in out.get("rebuttals", []):
        show_rebuttal(box, r, titles)
    if out.get("overall"):
        box.caption(f"검증 에이전트 총평: {out['overall']}")


def run_live(box, titles):
    """토론을 실행하면서 이벤트마다 채팅, AI Core, 타임라인을 갱신한다."""
    S.core = "INVESTIGATING"
    ev("시작", "에이전트 토론을 시작합니다", CY)
    play("detecting")
    flush_audio()                                      # 시작 즉시 안내 (토론은 수십 초 걸리므로 기다리지 않고 바로 재생)
    paint()

    def on_event(stage, p=None):
        if stage == "debate_start":
            S.core = "INVESTIGATING"
            ev("에이전트", f"가설 에이전트 {len(p['agents'])}명 투입: {', '.join(p['agents'])}", CY)
        elif stage == "agent_claim":
            S.core = "HYPOTHESIS ANALYSIS"
            ev("주장", f"{p['hypothesis_id']} 주장 도착 · 증거 도구 {len(p.get('extra_ledger_ids', []))}회 사용" + (" · 실패→기본 주장" if p.get("failed") else ""), PU)
            show_claim(box, p, titles)
        elif stage == "rebuttal":
            S.core = "EVIDENCE VALIDATION"
            ev("반박", f"검증 에이전트가 {p.get('hypothesis_id')}를 반박", WARN)
            show_rebuttal(box, p, titles)
        elif stage == "verdict":
            S.core = "EVIDENCE VALIDATION"
            ev("검증", f"발언 속 숫자를 장부와 대조 · 장부 밖 숫자 {p['ungrounded']}개", CY if not p["ungrounded"] else WARN)
        paint()
    with step(log, "에이전트 토론"):
        out = debate.run_debate(S.result, on_event=on_event)
    if out["mode"] == "rule" and config.LLM_ENABLED:     # AI를 쓰려 했는데 호출이 실패했을 때만 저장된 AI 결과로 대체 (AI를 끈 경우는 규칙 기반 그대로)
        cached = debate.load_cache(S.result)
        if cached and cached.get("mode") == "llm":
            cached["warnings"] = out["warnings"] + ["AI 호출에 실패해 저장된 이전 토론 결과를 표시합니다"]
            out = cached
            ev("저장본", "AI 호출 실패 → 저장된 결과로 대체", WARN)
    elif out["mode"] == "llm":
        debate.save_cache(S.result, out)
    return out


def show_toast():
    """조사 완료 직후 1위 결과를 5초 동안 오버레이로 보여준다 (CSS가 스스로 사라지게 함). 완료된 실행에서만 한 번 호출."""
    top, c = S.result["hypotheses"][0], S.result["conclusion"]
    st.html(ui.toast({"id": top["id"], "title": top["title"], "kind": top["kind"], "score": top["total"], "label": c["strength"],
                      "gap": c.get("gap", "-"), "mode": "AI + CODE CHECK" if S.out.get("mode") == "llm" else "RULE BASED"}))


def paint_report():
    with rep_slot.container():
        if not ok_case():
            st.html(ui.panel("INVESTIGATION", '<div class="txt dim">사건이 발견되면 이곳에 토론과 보고서가 표시됩니다.</div>', tone=MUTE))
            return
        out = S.out
        st.html(ui.panel("FINAL REPORT", '<div class="txt dim">' + ("점수와 순위는 코드가 계산했고, 가설은 서로 배타적이지 않으며 원인을 확정하지 않습니다."
                         if out else "AI 검토 전 규칙 기반 미리보기입니다. ‘결과 보기’ 탭의 ‘AI 검토 시작하기’를 누르세요.") + '</div>',
                         tag=("AI + CODE CHECK" if out["mode"] == "llm" else "RULE BASED") if out else "PREVIEW", tone=OK if out else MUTE))
        if out:
            st.caption(f"{out.get('seconds', '-')}초 · 토큰 {out.get('tokens', '-')}" + (" · 저장된 결과" if out.get("from_cache") else ""))
            for w in out.get("warnings", []):
                st.warning(w)
            st.markdown(out["report"])
        else:
            st.markdown(agents.rule_report(S.result))


# ---------- 실행 (토론 / 저장 결과) ----------
titles = {h["id"]: h["title"] for h in S.result["hypotheses"]} if ok_case() else {}
paint()
if ok_case():
    if S.go:
        S.go = False
        allowed, why = allow_ai_run() if config.LLM_ENABLED else (True, "")      # 규칙 기반 분석은 AI를 안 쓰므로 제한 없음
        S["quota_msg"] = why
        if not allowed:
            ev("한도", why, WARN)
        else:
            S.out = run_live(chat, titles)
            S.core = "CONCLUSION"
            S.mismatch = S.out.get("ledger_mismatch")
            ev("완료", f"조사 종료 · {'AI + 코드 검증' if S.out['mode'] == 'llm' else '규칙 기반'} · {S.out.get('seconds', '-')}초", OK)
            save_result()
            play("solved")
            show_toast()
    elif S.load_saved:
        S.load_saved = False
        S.out = debate.load_cache(S.result)
        if S.out:
            S.core = "CONCLUSION"
            ev("저장본", "저장된 토론 결과를 불러왔습니다", PU)
            replay_chat(chat, S.out, titles)
            play("solved")
            show_toast()
    elif S.out:
        replay_chat(chat, S.out, titles)
    paint()
paint_report()

# ---------- EVIDENCE ARCHIVE ----------
with tab_arc:
    if store.enabled():
        saved = recent_saved(S["sid"])
        st.html(ui.panel("저장된 검토 (Supabase)", '<div class="txt dim">끝난 검토의 요약(증거·점수·AI 발언·보고서)이 DB에 저장돼요. 원본 CSV 행은 저장하지 않아요.</div>',
                         tag=f"이 방문 {len(saved)}건", tone=OK))
        if saved:
            st.dataframe(pd.DataFrame([{"저장 시각": r["created_at"][:19].replace("T", " "), "데이터": r["dataset_label"], "1위 후보": r["top_title"],
                                        "근거 강도": r["top_score"], "방식": "AI+코드" if r["mode"] == "llm" else "규칙 기반", "토큰": r["tokens"], "초": r["seconds"]}
                                       for r in saved]), hide_index=True, use_container_width=True)
    else:
        st.html(ui.panel("저장된 검토 (Supabase)", '<div class="txt dim">저장 연결이 꺼져 있어요. Supabase 설정이 없으면 검토 결과를 저장하지 않아요.</div>', tag="꺼짐", tone=MUTE))
    if not S.df is None and S.case:
        st.html(ui.panel("CASE EVIDENCE · export_case.py", '<div class="txt dim">CSV에서 코드가 계산한 사건 증거입니다. 모든 보고서 숫자의 출처입니다.</div>', tag=S.case["status"].upper(), tone=CY))
        st.dataframe(pd.DataFrame([{"ID": x["id"], "항목": x["label"], "값": f"{x['value']}{x['unit']}"} for x in S.case["evidence"]]),
                     hide_index=True, use_container_width=True)
        roles = [{"컬럼": c, "역할": v.split(" (")[0], "판단 이유": v.partition(" (")[2].rstrip(")")}
                 for c, v in (S.case.get("debug", {}).get("roles") or {}).items()]
        st.html(ui.panel("COLUMN ROLES", '<div class="txt dim">컬럼 이름이 아니라 값의 성질로 자동 분류한 결과입니다.</div>', tone=PU))
        st.dataframe(pd.DataFrame(roles), hide_index=True, use_container_width=True)
        if ok_case():
            st.html(ui.panel("EVIDENCE LEDGER", '<div class="txt dim">도구가 계산한 모든 증거(L번호). 코드가 다시 계산해 일치를 검증합니다.</div>', tag=f"{len(S.result['ledger'].rows)} ENTRIES", tone=WARN))
            st.dataframe(pd.DataFrame([{"ID": r["id"], "도구": r["tool"], "인자": json.dumps(r["args"], ensure_ascii=False), "결과": r["result"].get("text", "")}
                                       for r in S.result["ledger"].rows]), hide_index=True, use_container_width=True)
    else:
        st.html(ui.panel("EVIDENCE ARCHIVE", '<div class="txt dim">데이터를 올리면 사건 증거와 장부가 이곳에 보관됩니다.</div>', tone=MUTE))
    files = sorted(config.CACHE_DIR.glob("debate_*.json")) if config.CACHE_DIR.exists() else []
    st.html(ui.panel("SAVED INVESTIGATIONS", f'<div class="txt dim">저장된 토론 결과 {len(files)}건 (API 실패 대비)</div>', tone=OK))
    if files:
        st.dataframe(pd.DataFrame([{"파일": f.name, "크기(KB)": round(f.stat().st_size / 1024, 1),
                                    "저장 시각": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))} for f in files]),
                     hide_index=True, use_container_width=True)

# ---------- TILON GPU (사이드바 버튼으로 여는 팝업) ----------
def jread(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def body(d):
    if not isinstance(d, dict):
        return str(d)
    for k in ("summary", "text", "output", "generated", "response"):
        if isinstance(d.get(k), str):
            return d[k]
    return ""


def gpu_case_view(name, A, B, sel, tag="TILON GPU"):
    """A/B 요약 카드와 채점, 선택 이유를 보여준다 (저장된 결과와 실시간 결과가 같은 화면을 쓴다)"""
    pick = (sel or {}).get("selected", "-")
    st.html(ui.panel(f"CASE · {name}", f'<div class="txt dim">같은 입력으로 프롬프트 A와 B를 비교한 결과 · 선택 {ui.chip("PROMPT " + str(pick), OK)}</div>', tag=tag, tone=CY))
    ca, cb = st.columns(2)
    for col, label, data in ((ca, "A · FREE-FORM", A), (cb, "B · CITE ALL EVIDENCE", B)):
        with col:
            st.markdown(f"**PROMPT {label}**")
            if data is None:
                st.caption("파일 없음")
                continue
            if data.get("title"):
                st.markdown(f"*{data['title']}*")
            st.write(body(data) or "(본문 키 없음)")
            m1, m2, m3 = st.columns(3)
            m1.metric("증거 커버리지", data.get("coverage", "-"))
            m2.metric("순점수", data.get("net_score", "-"))
            m3.metric("장부 밖 숫자", len(data.get("unmatched", [])))
            with st.expander("채점 상세"):
                st.json({k: v for k, v in data.items() if k not in ("summary", "text", "output", "generated", "response", "raw")})
    if sel:
        st.success(f"선택: 프롬프트 {sel.get('selected', sel.get('choice', ''))}  \n이유: {sel.get('reason', '')}")
        st.caption(f"바꾼 조건: {sel.get('changed', '-')}  \n고정한 조건: {sel.get('fixed', '-')}  \n"
                   f"모델: {sel.get('model', '-')} · 최대 VRAM {sel.get('peak_vram_gb', '-')}GB")


def live_view(res):
    """틸론 워커가 돌려준 결과를 저장된 결과와 같은 모양(A, B, 선택 정보)으로 바꾼다"""
    sel = {"selected": res.get("selected"), "reason": res.get("reason", ""),
           "changed": "프롬프트 방식 (A 자유 서술, B 증거 값 전부 인용·단정 금지)", "fixed": "모델, 입력 사건 데이터, 최대 출력 길이, greedy 생성",
           "model": res.get("model", "-"), "peak_vram_gb": res.get("peak_vram_gb", "-")}
    return res.get("A"), res.get("B"), sel


@st.dialog("TILON GPU · Qwen2.5-1.5B-Instruct", width="large")
def gpu_dialog():
    st.html(ui.panel("TILON GPU · QWEN2.5-1.5B-INSTRUCT",
                     '<div class="txt">교내 GPU(틸론)에서 소형 모델로 사건 요약 카드를 두 가지 프롬프트로 만들고, 증거 숫자 일치·커버리지를 코드로 채점해 더 나은 쪽을 골랐습니다. '
                     '<span class="dim">A: 자유 서술 / B: 증거 값 전부 인용·단정 금지.</span></div>', tag="TILON GPU", tone=PU))
    g = gpu_summary()
    if g:
        st.html(ui.stats([(f"{g['a']}→{g['b']}", "증거 커버리지 A → B", PU), (str(g["bad"]), "장부 밖 숫자", OK), (f"{g['vram']}GB", "최대 VRAM", CY)]))
    gs = gpu_status()
    live_on = bool(gs and gs["online"])
    st.html(ui.panel("LIVE · 내 데이터를 틸론 GPU로",
                     '<div class="txt dim">지금 분석한 CSV의 사건 요약 카드를 틸론 GPU에서 즉석으로 만듭니다. 원본 행은 보내지 않고 요약된 증거만 보냅니다.</div>',
                     tag="ONLINE" if live_on else "SAVED RESULTS ONLY", tone=OK if live_on else MUTE))
    if gs is None:
        st.caption("실시간 연동이 설정되지 않아 저장된 결과만 표시합니다.")
    elif not gs["online"]:
        st.info("틸론 GPU 실시간 연결이 꺼져 있어 저장된 결과를 보여드립니다. (틸론에서 워커를 켜면 내 데이터로 실시간 생성도 가능합니다)")
    elif not ok_case():
        st.info("사건이 발견된 CSV를 분석한 뒤 이 메뉴를 다시 열어 주세요.")
    else:
        left = config.GPU_JOBS_PER_SESSION - S.get("gpu_jobs", 0)
        cool = max(0, 15 - (time.time() - S.get("gpu_last", 0)))
        if st.button(f"▶ 틸론 GPU로 요약 카드 만들기 (남은 {left}회)", type="primary", disabled=left <= 0 or cool > 0, key="gpu_run"):
            S["gpu_jobs"], S["gpu_last"] = S.get("gpu_jobs", 0) + 1, time.time()
            try:
                with st.spinner("틸론 GPU에서 생성 중입니다 (보통 10초 안팎)..."):
                    gh = gh_client()
                    no = gpu_queue.submit(gh, S.case)
                    S["gpu_live"] = {"name": S.name, "res": gpu_queue.wait_result(gh, no, config.GPU_JOB_TIMEOUT_SEC)}
                log.info(f"틸론 GPU 실시간 결과 수신 (작업 #{no})")
            except Exception as e:
                log.warning(f"틸론 GPU 실시간 호출 실패: {e}")
                S["gpu_live"] = {"name": S.name, "error": str(e)[:120]}
        elif cool > 0 and S.get("gpu_jobs"):
            st.caption(f"연속 요청 방지: {cool:.0f}초 뒤에 다시 누를 수 있습니다.")
    lv = S.get("gpu_live")
    if lv and lv.get("name") == S.name:
        if lv.get("error"):
            st.error(f"틸론 GPU 호출에 실패했습니다: {lv['error']} · 아래 저장된 결과를 참고하세요.")
        elif not lv["res"].get("ok"):
            st.error(f"틸론에서 생성 중 오류가 났습니다: {lv['res'].get('error', '')}")
        else:
            A, B, sel = live_view(lv["res"])
            gpu_case_view(lv["name"], A, B, sel, tag=f"LIVE · {lv['res'].get('seconds', '-')}s · {lv['res'].get('gpu', 'GPU')}")
    st.html(ui.panel("SAVED RESULTS · 틸론에서 미리 실행해 저장한 결과",
                     '<div class="txt dim">시연 데이터 3개 사례의 결과입니다. 틸론이 꺼져 있어도 항상 볼 수 있고, 이 부분은 읽기만 합니다.</div>', tag="READ ONLY", tone=PU))
    dirs = sorted(p for p in config.RESULTS_DIR.glob("*") if p.is_dir()) if config.RESULTS_DIR.exists() else []
    if not dirs:
        st.info("아직 불러올 저장된 결과가 없습니다. (results/qwen/<사례>/ 에 case_summary_A.json, case_summary_B.json, selected.json)")
    for d in dirs:
        gpu_case_view(d.name, jread(d / "case_summary_A.json"), jread(d / "case_summary_B.json"), jread(d / "selected.json"), tag="SAVED")


with st.sidebar:                                       # 메뉴: 틸론 GPU 결과는 작은 버튼 하나로 연다
    if st.button("🖥 TILON GPU 결과 보기", use_container_width=True, key="menu_gpu"):
        gpu_dialog()
    st.caption("교내 GPU(틸론)로 만든 요약 카드 A/B 비교")
    st.caption("점수와 순위는 코드가 계산합니다. AI는 주장·반박·서술만 하며 원인을 확정하지 않습니다.")

# ---------- AGENT STATUS ----------
with tab_agt:
    out, rows = S.out, []
    H = S.result["hypotheses"][:debate.N_AGENTS] if ok_case() else []
    claims = {c["hypothesis_id"]: c for c in (out or {}).get("claims", [])}
    for h in H:
        c = claims.get(h["id"])
        st_, col = ("IDLE", MUTE) if not c else (("FALLBACK", WARN) if c.get("failed") else ("DONE", OK))
        rows.append(ui.agent_row(f"HYPOTHESIS AGENT {h['id']}", h["title"], st_, f"{len((c or {}).get('extra_ledger_ids', []))} tool calls · {(c or {}).get('tokens', 0)} tok", col))
    nre = len((out or {}).get("rebuttals", []))
    rows.append(ui.agent_row("VERIFIER AGENT", "가설 주장의 허점을 찾아 반박", "DONE" if out and nre else "IDLE", f"{nre} rebuttals", OK if out and nre else MUTE))
    rows.append(ui.agent_row("JUDGE", "점수표와 증거 장부만 보고 보고서 작성", ("AI" if out["mode"] == "llm" else "RULE") if out else "IDLE",
                             f"{(out or {}).get('seconds', '-')} s", PU if out and out["mode"] == "llm" else MUTE))
    mm = (out or {}).get("ledger_mismatch", S.mismatch)
    rows.append(ui.agent_row("CODE VERIFIER", "장부 재계산 · 발언 속 숫자 대조", "PASS" if mm == [] else ("FAIL" if mm else "IDLE"),
                             "no mismatch" if mm == [] else str(mm or "-"), OK if mm == [] else (CRIT if mm else MUTE)))
    st.html(ui.panel("AI AGENT STATUS", "".join(rows), tag="LIVE FROM LAST RUN", tone=PU))
    st.html(ui.panel("SYSTEM CONFIG", ui.kv([(k, ui.e(v)) for k, v in config.summary().items()]), tag="NO SECRETS SHOWN", tone=CY))
    logs = get_logs()
    st.html(ui.panel("DEBUG LOG", '<div class="txt dim">이 방문의 단계별 로그 (키 값은 자동으로 가려집니다)</div>', tag=f"{len(logs)} LINES", tone=MUTE))
    if logs:
        st.dataframe(pd.DataFrame(logs)[["time", "level", "name", "msg"]], hide_index=True, use_container_width=True)

flush_audio()                                          # 화면을 전부 그린 뒤, 대기 중인 음성을 차례로 재생
