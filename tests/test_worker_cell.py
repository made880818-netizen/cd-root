"""test_worker_cell.py - 틸론에 붙일 워커 셀의 run_case가 노트북 함수들과 맞물려 저장된 결과와 같은 모양을 내는지 검증한다.
노트북의 validate/PROMPTS/evidence_text/parse/score/rank는 틸론 노트북 코드를 그대로 옮겨 쓰고, 모델 호출(generate)과 torch만 가짜로 바꾼다.
python tests/test_worker_cell.py"""
import re, sys, json, pathlib, types
ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = []


def check(title, ok, note=""):
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {note}" if not ok else ""))
    if not ok:
        fails.append(title)


# ---- 틸론 노트북 함수 (붙여 주신 셀에서 그대로 옮김) ----
NAME_NUM = re.compile(r"[A-Za-z]+\d+"); NUM = re.compile(r"\d+(?:\.\d+)?"); ID_PAT = re.compile(r"\bE\d+\b")
def validate(c):
    errs = []
    for k in ["case_name", "period", "evidence"]:
        if k not in c: errs.append(f"키 없음: {k}")
    if "period" in c:
        for k in ["baseline", "event"]:
            if k not in c["period"]: errs.append(f"period.{k} 없음")
    for i, e in enumerate(c.get("evidence", [])):
        for k in ["id", "label", "value", "unit"]:
            if k not in e: errs.append(f"evidence[{i}].{k} 없음")
    return errs
def nums(text):
    t = NAME_NUM.sub(" ", str(text)).replace(",", "")
    return [float(x) for x in NUM.findall(t)]
def score(text, c):
    ev_vals = []
    for e in c["evidence"]: ev_vals += nums(e["value"])
    allowed = list(ev_vals)
    allowed += nums(c["period"]["baseline"]) + nums(c["period"]["event"])
    allowed += nums(" ".join(e["label"] for e in c["evidence"]))
    ok = lambda n: any(abs(n - a) < 0.05 for a in allowed)
    found = nums(text)
    bad = [n for n in found if not ok(n)]
    ev_hit = [n for n in found if any(abs(n - v) < 0.05 for v in ev_vals)]
    covered = sum(1 for e in c["evidence"] if nums(e["value"]) and all(any(abs(n - v) < 0.05 for n in found) for v in nums(e["value"])))
    return {"numbers": found, "unmatched": bad, "grounding_rate": round((len(found) - len(bad)) / len(found), 3) if found else 0.0,
            "net_score": len(ev_hit) - 2 * len(bad), "coverage": f'{covered}/{len(c["evidence"])}', "coverage_n": covered, "id_leak": len(ID_PAT.findall(text))}
def rank(r):
    return (r["coverage_n"], -len(r["unmatched"]), -r["id_leak"], r["prompt_type"] == "B")
def parse(text):
    t = re.search(r"제목\s*[:：]\s*(.+)", text); s = re.search(r"요약\s*[:：]\s*(.+)", text, re.S)
    return (t.group(1).strip() if t else ""), (s.group(1).strip() if s else text)
COMMON = "형식:\n제목: ...\n요약: ...\n\n사건: {name}\n기간: {period}\n증거:\n{ev}"
PROMPTS = {"A": "자유\n" + COMMON, "B": "전부 인용\n" + COMMON}
def evidence_text(c):
    return "\n".join(f'- {e["label"]}: {e["value"]}{e["unit"]}' for e in c["evidence"])
calls = []
def generate(p):                                       # 모델 대신: 프롬프트 종류에 따라 정해진 답
    calls.append(p)
    return ("제목: 취소 급증 사건\n\n요약: 변화율 85.7% 증가했고 net_revenue는 -27.7%였다." if p.startswith("전부")
            else "제목: 변화\n\n요약: 취소가 크게 늘었다.")
class _Cuda:
    reset_peak_memory_stats = staticmethod(lambda: None)
    max_memory_allocated = staticmethod(lambda: 3.14e9)
    get_device_name = staticmethod(lambda i=0: "FAKE-GPU")
torch = types.SimpleNamespace(cuda=_Cuda)

# ---- 워커 셀에서 run_case 함수 부분만 꺼내 실행 ----
cell = (ROOT / "tools" / "tilon_worker_cell.py").read_text(encoding="utf-8")
src = cell[cell.index("def run_case"):cell.index("REPO = input")]
ns = dict(globals()); exec(src, ns)
run_case = ns["run_case"]

case = json.loads((ROOT / "gpu_inputs" / "case_input_orders.json").read_text(encoding="utf-8"))
res = run_case(case)
saved_A = json.loads((ROOT / "results" / "qwen" / "orders" / "case_summary_A.json").read_text(encoding="utf-8"))
saved_sel = json.loads((ROOT / "results" / "qwen" / "orders" / "selected.json").read_text(encoding="utf-8"))
check("A/B 프롬프트로 2번 생성", len(calls) == 2 and calls[0].startswith("자유") and calls[1].startswith("전부"))
check("틸론 노트북 결과(case_summary_A.json)와 같은 키 구성", set(res["A"]) == set(saved_A), f"{set(res['A']) ^ set(saved_A)}")
check("선택·이유가 selected.json과 같은 형식으로 나옴", res["selected"] in ("A", "B") and res["reason"].startswith("A: 순점수") and " / B: 순점수" in res["reason"])
check("모델 정보와 VRAM·GPU 이름 포함", res["model"].startswith("Qwen") and res["peak_vram_gb"] == 3.14 and res["gpu"] == "FAKE-GPU")
check("B(증거 인용)가 A보다 높은 커버리지면 B 선택", res["selected"] == "B" and res["B"]["coverage_n"] > res["A"]["coverage_n"], f'{res["A"]["coverage"]} vs {res["B"]["coverage"]}')
check("결과가 JSON으로 직렬화됨", bool(json.dumps(res, ensure_ascii=False)))
try:
    run_case({"case_name": "x"}); check("계약 키가 없으면 ValueError", False)
except ValueError as e:
    check("계약 키가 없으면 ValueError (워커는 오류 결과로 돌려줌)", "키 없음" in str(e))
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
