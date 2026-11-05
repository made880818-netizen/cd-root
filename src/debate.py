"""debate.py (2부) - 가설 에이전트들이 주장하고, 검증 에이전트가 반박하고, 코드가 점수를 확정한다.
- LLM(Claude)은 '주장, 반박, 서술'만 한다. 점수와 순위는 agents.py의 코드 계산이 정한다.
- 키가 없거나 호출이 실패하면 자동으로 규칙 기반 보고서(rule_report)로 넘어간다.

사용법:  out = run_debate(result, on_event=print)      # result = agents.analyze(...) 결과
         out["mode"]   -> "llm" 또는 "rule"
         out["report"] -> 최종 보고서 (markdown)
"""
import json, re, time, threading, contextvars, hashlib
from concurrent.futures import ThreadPoolExecutor
import agents

try:
    import config
except Exception:                      # config.py 없이도 단독 실행 가능
    import os
    class config:                      # noqa: N801
        ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
        ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "")
        CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
        CLAUDE_MAX_TOKENS = 1500
        CLAUDE_TIMEOUT_SEC = 40
        MAX_TOOL_CALLS_PER_AGENT = 6
        LLM_ENABLED = bool(os.environ.get("ANTHROPIC_API_KEY"))
        CACHE_DIR = None

try:
    from logger import get_logger
    log = get_logger("debate")
except Exception:
    import logging
    log = logging.getLogger("debate")

N_AGENTS = 3                    # 토론에 참여시킬 상위 가설 수
_lock = threading.Lock()        # 장부에 동시에 기록되지 않도록


# ====================== 클라이언트 ======================
def make_client():
    """Claude 클라이언트 생성. 키나 라이브러리가 없으면 None (규칙 기반으로 넘어감)"""
    if not getattr(config, "LLM_ENABLED", False):
        log.warning("LLM 비활성 (키 없음 또는 DISABLE_LLM) -> 규칙 기반 모드")
        return None
    try:
        import anthropic
        kw = {"api_key": config.ANTHROPIC_API_KEY, "timeout": config.CLAUDE_TIMEOUT_SEC, "max_retries": 1}
        if getattr(config, "ANTHROPIC_BASE_URL", ""):
            kw["base_url"] = config.ANTHROPIC_BASE_URL
        return anthropic.Anthropic(**kw)
    except Exception as e:
        log.warning(f"Claude 클라이언트 생성 실패: {type(e).__name__}: {e}")
        return None


# ====================== 도구 정의 (LLM이 호출) ======================
def tool_names(ctx):
    """현재 분석 모드에서 쓸 수 있는 도구 이름"""
    if ctx["mode"] == "time":
        return ["t_change", "t_lead", "t_corr", "t_placebo", "t_group_share", "t_quality"]
    return ["t_gm_effect", "t_gm_strata", "t_gm_drop", "t_gm_cov", "t_gm_quality"]


def tool_schema(ctx):
    """LLM에게 보여줄 도구 설명"""
    names = tool_names(ctx)
    desc = "; ".join(f"{n}: {(agents.TOOLS[n].__doc__ or '').strip()}" for n in names)
    return [{
        "name": "get_evidence",
        "description": "데이터에서 증거를 계산한다. 결과는 증거 장부(L번호)에 기록된다. 도구 목록 - " + desc,
        "input_schema": {"type": "object", "properties": {
            "tool": {"type": "string", "enum": names},
            "col": {"type": "string", "description": f"수치 컬럼 이름. 후보: {ctx['metrics']}"},
            "group": {"type": "string", "description": f"그룹 컬럼 이름. 후보: {ctx['groups']}"}},
            "required": ["tool"]}}]


def call_tool(ctx, ledger, args):
    """LLM이 요청한 도구 실행. 잘못된 인자는 오류 문구를 돌려줘 스스로 고치게 한다."""
    name = args.get("tool")
    if name not in tool_names(ctx):
        return None, {"error": f"이 데이터에서 쓸 수 없는 도구: {name}. 가능: {tool_names(ctx)}"}
    kw = {}
    if name in ("t_change", "t_lead", "t_corr", "t_placebo", "t_gm_cov"):
        if args.get("col") not in ctx["metrics"]:
            return None, {"error": f"col은 {ctx['metrics']} 중 하나여야 함"}
        kw["col"] = args["col"]
    if name in ("t_group_share", "t_gm_strata", "t_gm_drop"):
        if args.get("group") not in ctx["groups"]:
            return None, {"error": f"group은 {ctx['groups']} 중 하나여야 함"}
        kw["group"] = args["group"]
    with _lock:
        lid, res = agents.run_tool(ctx, ledger, name, **kw)
    return lid, {"ledger_id": lid, **res}


# ====================== LLM 호출 ======================
def _text_of(resp):
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()


def ask(client, system, user, ctx=None, ledger=None, max_calls=None, who="agent"):
    """Claude에게 질문. ctx가 있으면 도구 호출(최대 max_calls회)을 허용한다. 반환: (텍스트, 사용한 도구 id 목록, 토큰)"""
    max_calls = config.MAX_TOOL_CALLS_PER_AGENT if max_calls is None else max_calls
    msgs, used, tokens, calls = [{"role": "user", "content": user}], [], 0, 0
    tools = tool_schema(ctx) if ctx is not None and max_calls > 0 else None
    for _ in range(max_calls + 2):
        kw = dict(model=config.CLAUDE_MODEL, max_tokens=config.CLAUDE_MAX_TOKENS, system=system, messages=msgs)
        if tools and calls < max_calls:
            kw["tools"] = tools
        t0 = time.time()
        resp = client.messages.create(**kw)
        u = getattr(resp, "usage", None)
        tokens += (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "output_tokens", 0) or 0)
        log.info(f"[{who}] 응답 {time.time() - t0:.1f}초, 종료 사유={resp.stop_reason}")
        if resp.stop_reason != "tool_use":
            return _text_of(resp), used, tokens
        msgs.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if getattr(b, "type", "") != "tool_use":
                continue
            calls += 1
            if calls > max_calls:
                out = {"error": "도구 호출 횟수 초과. 지금까지의 증거로 답하세요."}
            else:
                lid, out = call_tool(ctx, ledger, dict(b.input))
                if lid:
                    used.append(lid)
                log.info(f"[{who}] 도구 요청 {dict(b.input)} -> {lid or '오류'}")
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str)})
        msgs.append({"role": "user", "content": results})
    return _text_of(resp), used, tokens


def parse_json(text):
    """LLM 답변에서 JSON을 꺼낸다. 실패하면 None"""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ====================== 숫자 검사 (장부에 없는 숫자 거르기) ======================
_NAMENUM = re.compile(r"[A-Za-z]+\d+")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def numbers(text):
    t = _NAMENUM.sub(" ", str(text)).replace(",", "")
    out = []
    for m in _NUM.finditer(t):
        v, tail = float(m.group(0)), t[m.end():m.end() + 1]
        if "." in m.group(0) or tail == "%" or abs(v) >= 10:     # '2위' 같은 작은 정수는 검사 제외
            out.append(abs(v))
    return out


def allowed_numbers(result):
    """보고서에 써도 되는 숫자: 장부, 가설 점수표, 기간, 증거 목록에 있는 숫자"""
    src = [json.dumps(r["result"], ensure_ascii=False, default=str) for r in result["ledger"].rows]
    for h in result["hypotheses"]:
        src += [h["title"], h["claim"], str(h["total"])]
        src += [f"{c['score']} {c['note']}" for c in h["components"]]
    case = result["ctx"].get("case", {})
    src.append(json.dumps(result["conclusion"].get("gap")))        # 코드가 계산해 준 1·2위 점수차
    src += [json.dumps(case.get("period", {}), ensure_ascii=False),
            json.dumps(case.get("evidence", []), ensure_ascii=False, default=str)]
    return [n for s in src for n in numbers(s)]


def ungrounded(text, allowed):
    return sorted({n for n in numbers(text) if not any(abs(n - a) < 0.06 for a in allowed)})


# ====================== 에이전트 ======================
AGENT_SYS = ("당신은 데이터 사건 분석 에이전트다. 배정된 가설이 맞다고 주장하되 증거로만 말한다.\n"
             "규칙: 숫자는 주어진 증거 장부나 도구 결과에 있는 것만 쓴다. 새로 계산하지 않는다. "
             "원인을 확정하지 말고 '후보'로 표현한다. 필요하면 get_evidence 도구로 증거를 더 모은다.\n"
             '마지막 답변은 JSON 한 개만: {"claim":"주장 2문장 이내","evidence_ids":["L1"],"weakness":"이 가설의 약점 한 문장"}')

VERIFIER_SYS = ("당신은 검증 에이전트다. 각 가설 주장의 허점을 찾아 반박한다. "
                "선행성, 정상 구간 대조, 다른 가설과의 겹침, 데이터 오류 가능성을 본다. "
                "숫자는 증거 장부나 도구 결과에 있는 것만 쓴다. 필요하면 get_evidence로 반증 증거를 계산한다.\n"
                '마지막 답변은 JSON 한 개만: {"rebuttals":[{"hypothesis_id":"H1","point":"반박 1~2문장",'
                '"evidence_ids":["L3"]}],"overall":"전체 평가 한 문장"}')

JUDGE_SYS = ("당신은 사건 보고서 작성자다. 점수표와 증거 장부만 보고 한국어 보고서를 쓴다. "
             "순위와 점수는 코드가 정한 것이므로 바꾸지 않는다. 장부에 없는 숫자는 쓰지 않는다. "
             "원인을 확정하지 말고, 가설끼리 배타적이지 않음을 밝힌다. 개조식 문장, 250단어 이내. "
             "구성: 1) 개요 2) 가설별 근거와 반박 3) 결론 4) 한계")


def hyp_brief(h, ledger):
    ev = []
    for lid in h["evidence_ids"]:
        r = ledger.get(lid)
        if r:
            ev.append(f"{lid}: {r['result'].get('text')}")
    comps = "; ".join(f"{c['name']}={c['score']}" for c in h["components"])
    return f"가설 {h['id']} [{h['kind']}] {h['title']}\n코드 점수 {h['total']} ({comps})\n증거:\n" + "\n".join(ev)


def run_agent(client, result, h, ctx_copy):
    """가설 하나를 맡은 에이전트 실행"""
    ctx, ledger = result["ctx"], result["ledger"]
    user = f"사건: {ctx['case']['case_name']} (기간 {ctx['case']['period']})\n\n{hyp_brief(h, ledger)}\n\n이 가설을 주장해라."
    txt, used, tok = ask(client, AGENT_SYS, user, ctx, ledger, who=f"Agent {h['id']}")
    j = parse_json(txt) or {"claim": txt[:300], "evidence_ids": [], "weakness": ""}
    return {"hypothesis_id": h["id"], "claim": j.get("claim", ""), "weakness": j.get("weakness", ""),
            "evidence_ids": j.get("evidence_ids", []), "extra_ledger_ids": used, "tokens": tok, "raw": txt}


# ====================== 토론 실행 ======================
def run_debate(result, client="auto", on_event=None):
    """토론 전체 실행. 어떤 실패도 규칙 기반 보고서로 이어지게 한다."""
    emit = on_event or (lambda stage, payload=None: None)
    t_all = time.time()
    out = {"mode": "rule", "claims": [], "rebuttals": [], "overall": "", "warnings": [], "tokens": 0,
           "hypotheses": result.get("hypotheses", []), "report": ""}
    if result["status"] != "ok" or not result["hypotheses"]:
        out["report"] = agents.rule_report(result)
        emit("solved" if result["status"] == "ok" else "no_event", out)
        return out
    emit("found", {"case": result["ctx"]["case"]["case_name"]})
    if client == "auto":
        client = make_client()
    bad = agents.verify_ledger(result["ctx"], result["ledger"])        # 장부 재계산 검증 (코드)
    out["ledger_mismatch"] = bad
    if bad:
        out["warnings"].append(f"장부 재계산 불일치: {bad}")
    if client is None:
        out["report"] = agents.rule_report(result)
        out["warnings"].append("LLM을 사용할 수 없어 규칙 기반 보고서로 대체함")
        emit("solved", out)
        return out

    try:
        H = result["hypotheses"][:N_AGENTS]
        emit("debate_start", {"agents": [h["id"] for h in H]})
        # 1) 에이전트 병렬 주장. 한 명이 실패해도 나머지는 계속한다.
        def work(h):
            try:
                ctxv = contextvars.copy_context()
                return ctxv.run(run_agent, client, result, h, None)
            except Exception as e:
                log.warning(f"Agent {h['id']} 실패: {type(e).__name__}: {e}")
                return {"hypothesis_id": h["id"], "claim": h["claim"], "weakness": "",
                        "evidence_ids": h["evidence_ids"], "extra_ledger_ids": [], "tokens": 0,
                        "failed": True, "raw": ""}
        with ThreadPoolExecutor(max_workers=len(H)) as ex:
            claims = list(ex.map(work, H))
        for c in claims:
            emit("agent_claim", c)
        out["claims"] = claims
        if all(c.get("failed") for c in claims):
            raise RuntimeError("모든 에이전트 호출 실패")

        # 2) 검증 에이전트 반박
        brief = "\n\n".join(hyp_brief(h, result["ledger"]) + f"\n주장: {c['claim']}\n자인한 약점: {c['weakness']}"
                            for h, c in zip(H, claims))
        txt, used, tok = ask(client, VERIFIER_SYS, f"가설과 주장 목록:\n\n{brief}", result["ctx"],
                             result["ledger"], who="Verifier")
        vj = parse_json(txt) or {"rebuttals": [], "overall": txt[:300]}
        out["rebuttals"], out["overall"] = vj.get("rebuttals", []), vj.get("overall", "")
        for r in out["rebuttals"]:
            emit("rebuttal", r)
        out["tokens"] = sum(c["tokens"] for c in claims) + tok

        # 3) 코드 검증: 주장과 반박 속 숫자가 장부에 있는지, 장부가 다시 계산해도 같은지
        bad = agents.verify_ledger(result["ctx"], result["ledger"])
        out["ledger_mismatch"] = bad
        allowed = allowed_numbers(result)
        for c in claims:
            c["ungrounded"] = ungrounded(c["claim"] + " " + c["weakness"], allowed)
        for r in out["rebuttals"]:
            r["ungrounded"] = ungrounded(r.get("point", ""), allowed)
        n_bad = sum(len(c["ungrounded"]) for c in claims) + sum(len(r["ungrounded"]) for r in out["rebuttals"])
        if n_bad:
            out["warnings"].append(f"장부에 없는 숫자 {n_bad}개가 토론 발언에 있어 해당 발언에 표시함")
        emit("verdict", {"ranking": [(h["id"], h["total"]) for h in result["hypotheses"]], "ungrounded": n_bad})

        # 4) 보고서: 점수표 기준으로 서술. 장부에 없는 숫자가 있으면 규칙 기반 보고서로 교체
        table = "\n".join(f"{i}. {h['id']} {h['title']} 점수 {h['total']}" for i, h in enumerate(result["hypotheses"], 1))
        ledger_txt = "\n".join(f"{r['id']}: {r['result'].get('text')}" for r in result["ledger"].rows)
        user = (f"사건: {result['ctx']['case']['case_name']} / 기간 {result['ctx']['case']['period']}\n"
                f"판정: {result['conclusion']['strength']}, 2위와 점수차 {result['conclusion'].get('gap')}\n"
                f"경고: {result['conclusion'].get('warnings')}\n\n[점수표]\n{table}\n\n[증거 장부]\n{ledger_txt}\n\n"
                f"[에이전트 주장]\n" + "\n".join(f"{c['hypothesis_id']}: {c['claim']}" for c in claims) +
                f"\n\n[검증 에이전트 반박]\n" + "\n".join(f"{r.get('hypothesis_id')}: {r.get('point')}" for r in out["rebuttals"]) +
                f"\n전체: {out['overall']}")
        report, _, tok = ask(client, JUDGE_SYS, user, max_calls=0, who="Judge")
        out["tokens"] += tok
        bad_nums = ungrounded(report, allowed + [float(x) for x in numbers(ledger_txt)])
        if bad_nums or not report:
            log.warning(f"보고서에 장부 밖 숫자 {bad_nums} -> 규칙 기반 보고서로 교체")
            out["warnings"].append(f"LLM 보고서의 장부 밖 숫자 {bad_nums}를 발견해 규칙 기반 보고서로 교체함")
            out["report_llm_rejected"] = report
            out["report"] = agents.rule_report(result)
        else:
            out["report"], out["mode"] = report, "llm"
        out["hypotheses"] = result["hypotheses"]
    except Exception as e:
        log.exception("토론 실패 -> 규칙 기반으로 전환")
        out["warnings"].append(f"LLM 토론 실패({type(e).__name__}: {e}) -> 규칙 기반 보고서로 대체함")
        out["report"] = agents.rule_report(result)
        out["mode"] = "rule"
    out["seconds"] = round(time.time() - t_all, 1)
    log.info(f"토론 종료: mode={out['mode']}, {out['seconds']}초, 토큰 {out['tokens']}, 경고 {len(out['warnings'])}건")
    emit("solved", out)
    return out


# ====================== 캐시 (시연용 사전 결과) ======================
def cache_key(result):
    c = result["ctx"].get("case", {})
    return hashlib.md5(json.dumps([c.get("case_name"), c.get("period"), c.get("evidence")],
                                  ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]


def save_cache(result, out):
    d = getattr(config, "CACHE_DIR", None)
    if not d:
        return None
    try:
        import pathlib
        p = pathlib.Path(d); p.mkdir(parents=True, exist_ok=True)
        f = p / f"debate_{cache_key(result)}.json"
        keep = {k: v for k, v in out.items() if k != "hypotheses"}
        f.write_text(json.dumps(keep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return f
    except Exception as e:
        log.warning(f"캐시 저장 실패: {e}")
        return None


def load_cache(result):
    d = getattr(config, "CACHE_DIR", None)
    if not d:
        return None
    try:
        import pathlib
        f = pathlib.Path(d) / f"debate_{cache_key(result)}.json"
        if f.exists():
            out = json.loads(f.read_text(encoding="utf-8"))
            out["hypotheses"], out["from_cache"] = result["hypotheses"], True
            return out
    except Exception as e:
        log.warning(f"캐시 읽기 실패: {e}")
    return None


if __name__ == "__main__":
    import sys
    import export_case as ec
    path = next((a for a in sys.argv[1:] if not a.startswith("--")), "data/orders.csv")
    metric = next((a.split("=")[1] for a in sys.argv if a.startswith("--metric=")), None)
    df = ec.read_csv_any(path)
    res = agents.analyze(df, ec.build_case_from_df(df, name=path, metric=metric))
    o = run_debate(res, on_event=lambda s, p=None: print(f"  <이벤트> {s}"))
    print(f"\n[모드] {o['mode']} | 경고 {o['warnings']}\n")
    print(o["report"])
