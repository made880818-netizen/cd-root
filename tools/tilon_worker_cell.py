# ===== 틸론 워커: 이 셀은 계속 실행 상태로 둡니다 (끝내려면 ■ 중지) =====
# 앞 셀들(모델 로드, validate, PROMPTS, evidence_text, generate, parse, score, rank)이 실행되어 있어야 합니다.
import getpass, importlib, torch
import gpu_queue as gq
importlib.reload(gq)

def run_case(c):
    """웹이 보낸 사건 하나를 A/B 프롬프트로 요약하고 채점해 선택까지 돌려준다 (앞 셀의 함수를 그대로 쓴다)"""
    errs = validate(c)
    if errs:
        raise ValueError("; ".join(errs))
    torch.cuda.reset_peak_memory_stats()
    res = {}
    for key, tmpl in PROMPTS.items():
        p = tmpl.format(name=c["case_name"], period=f'{c["period"]["baseline"]} -> {c["period"]["event"]}', ev=evidence_text(c))
        raw = generate(p)
        title, summary = parse(raw)
        res[key] = {"prompt_type": key, "title": title, "summary": summary, "raw": raw, **score(raw, c)}
    best = max(res.values(), key=rank)["prompt_type"]
    reason = " / ".join(f"{k}: 순점수 {res[k]['net_score']}, 커버리지 {res[k]['coverage']}, 불일치 {len(res[k]['unmatched'])}" for k in ("A", "B"))
    return {"model": "Qwen2.5-1.5B-Instruct", "selected": best, "reason": reason, "A": res["A"], "B": res["B"],
            "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2), "gpu": torch.cuda.get_device_name(0)}

REPO = input("작업 큐 저장소 (예: 계정/저장소이름): ").strip()
gh = gq.GH(REPO, getpass.getpass("GitHub 토큰 (화면에 표시되지 않음): ").strip())
print("[워커 시작] 웹에서 작업이 오면 처리합니다. 중지하려면 ■ 를 누르세요.")
try:
    gq.worker_loop(gh, run_case, lambda: {"gpu": torch.cuda.get_device_name(0), "model": "Qwen2.5-1.5B-Instruct"})
except KeyboardInterrupt:
    print("[워커 중지] 웹에는 OFFLINE으로 표시됩니다.")
