"""check_gpu_contract.py - export_case 출력이 gpu_job의 validate/score를 통과하는지 확인하고 입력 JSON 3종을 만든다.
gpu_job.ipynb의 validate/nums/score를 그대로 복사해 쓴다 (노트북 쪽을 고치면 여기도 맞춰야 함)."""
import sys, re, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import logging, export_case as ec
ec.log.setLevel(logging.CRITICAL)

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
    allowed = list(ev_vals) + nums(c["period"]["baseline"]) + nums(c["period"]["event"]) + nums(" ".join(e["label"] for e in c["evidence"]))
    ok = lambda n: any(abs(n - a) < 0.05 for a in allowed)
    found = nums(text)
    bad = [n for n in found if not ok(n)]
    covered = sum(1 for e in c["evidence"] if nums(e["value"]) and all(any(abs(n - v) < 0.05 for n in found) for v in nums(e["value"])))
    return {"unmatched": bad, "coverage": f'{covered}/{len(c["evidence"])}', "n_numeric_ev": sum(1 for e in c["evidence"] if nums(e["value"]))}

def evidence_text(c):
    return "\n".join(f'- {e["id"]} {e["label"]}: {e["value"]}{e["unit"]}' for e in c["evidence"])

if __name__ == "__main__":
    out = ROOT / "gpu_inputs"; out.mkdir(exist_ok=True)
    srcs = {"orders": ("orders.csv", {}), "orders_revenue": ("orders.csv", {"metric": "revenue"}),
            "orders_nodate": ("orders.csv", {"drop": ["date"]})}
    for name, (fn, opt) in srcs.items():
        df = ec.read_csv_any(str(ROOT / "data" / fn)).drop(columns=opt.get("drop", []))
        c = ec.build_case_from_df(df, name=name, metric=opt.get("metric"))
        c["debug"] = None; c.pop("debug")                      # 틸론에는 계약 키만 보낸다
        c = {k: c[k] for k in ("case_name", "period", "evidence")}
        errs = validate(c)
        (out / f"case_input_{name}.json").write_text(json.dumps(c, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        types = sorted({type(e["value"]).__name__ for e in c["evidence"]})
        # 증거 문장을 그대로 베낀 이상적 요약으로 채점해 본다 (모델이 완벽할 때의 상한)
        perfect = score(evidence_text(c) + f" {c['period']['baseline']} {c['period']['event']}", c)
        print(f"[{name}] 검증 {'OK' if not errs else errs} | 증거 {len(c['evidence'])}개, 값 타입 {types} | 완벽 요약 채점: {perfect}")
        print("   증거:", [(e['id'], e['value']) for e in c['evidence']][:12])
