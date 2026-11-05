"""stress_csv.py - 낯선 CSV들이 에러 없이 폴백까지 도는지 시험한다.  python tests/stress_csv.py [폴더]"""
import sys, time, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import config, export_case as ec, agents, debate

folder = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data"
use_llm = "--llm" in sys.argv
rows = []
for f in sorted(folder.glob("*.csv")):
    t0, size = time.time(), f.stat().st_size / 1e6
    try:
        df = ec.read_csv_any(str(f))
        if len(df) > config.MAX_UPLOAD_ROWS:
            df = df.head(config.MAX_UPLOAD_ROWS)
        case = ec.build_case_from_df(df, name=f.name)
        res = agents.analyze(df, case)
        out = debate.run_debate(res, client="auto" if use_llm else None)
        ok = bool(out["report"])
        rows.append((f.name, f"{size:.1f}MB", f"{len(df)}x{df.shape[1]}", case["status"], res["status"],
                     out["mode"], "OK" if ok else "빈보고서", f"{time.time()-t0:.1f}s"))
    except Exception as e:
        rows.append((f.name, f"{size:.1f}MB", "-", "-", "-", "-", f"예외 {type(e).__name__}: {e}", f"{time.time()-t0:.1f}s"))
print("\n=== 결과 ===")
for r in rows:
    print(" | ".join(r))
