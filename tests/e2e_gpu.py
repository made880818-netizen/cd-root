"""e2e_gpu.py - 틸론 GPU 실시간 연동을 끝까지 시험한다: 가짜 GitHub + 가짜 틸론 워커 + 실제 Streamlit 서버 + 브라우저.
python tests/e2e_gpu.py [출력접두사]"""
import os, sys, json, time, shutil, tempfile, pathlib, threading, subprocess, urllib.request
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
import gpu_queue as q
from mock_github import MockGitHub
from playwright.sync_api import sync_playwright

pre = sys.argv[1] if len(sys.argv) > 1 else "e2e"
PORT, fails = 8502, []


def check(title, ok, note=""):
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {note}" if not ok else ""))
    if not ok:
        fails.append(title)


srv = MockGitHub()
gh = q.GH(srv.repo, srv.token, base=srv.base, timeout=5)
saved = {k: json.loads((ROOT / "results" / "qwen" / "orders" / f).read_text(encoding="utf-8")) for k, f in
         (("A", "case_summary_A.json"), ("B", "case_summary_B.json"), ("sel", "selected.json"))}
seen = []


def fake_tilon(case):
    """틸론에서 Qwen이 하는 일을 흉내낸다 (저장된 orders 결과를 돌려주되 지연 1.5초). 받은 사건이 맞는지 기록."""
    seen.append(case)
    time.sleep(1.5)
    return {"model": "Qwen2.5-1.5B-Instruct (STUB)", "selected": saved["sel"]["selected"], "reason": saved["sel"]["reason"],
            "A": saved["A"], "B": saved["B"], "peak_vram_gb": 3.14, "gpu": "STUB-GPU"}


stop = {"v": False}
worker = threading.Thread(target=q.worker_loop, args=(gh, fake_tilon, lambda: {"gpu": "STUB-GPU", "model": "stub"}),
                          kwargs={"poll": 0.3, "beat_every": 1, "should_stop": lambda: stop["v"], "log": lambda m: print("   (worker)", m)}, daemon=True)
worker.start(); time.sleep(1.5)

# 진짜 .streamlit/secrets.toml(실제 토큰)을 건드리지 않도록, 프로젝트를 임시 폴더에 복사하고 가짜 설정만 담은 secrets.toml로 실행한다
# (Streamlit은 secrets.toml 값을 환경변수에 덮어쓰기 때문에 환경변수만 바꿔서는 격리되지 않는다)
tmp = pathlib.Path(tempfile.mkdtemp(prefix="cdroot_e2e_")) / "app"
shutil.copytree(ROOT, tmp, ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache", "*.pyc", ".streamlit", "*10MB*", "cache", ".ipynb_checkpoints"))
(tmp / ".streamlit").mkdir()
shutil.copy(ROOT / ".streamlit" / "config.toml", tmp / ".streamlit" / "config.toml")
fake_secrets = [f'GPU_QUEUE_REPO = "{srv.repo}"', f'GPU_QUEUE_TOKEN = "{srv.token}"', f'GPU_QUEUE_API = "{srv.base}"']
(tmp / ".streamlit" / "secrets.toml").write_text("\n".join(fake_secrets) + "\n", encoding="utf-8")
env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "GPU_QUEUE_"))}
env.update({"PYTHONUTF8": "1", "DISABLE_LLM": "1"})
proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(PORT), "--server.headless", "true"],
                        cwd=str(tmp), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            if urllib.request.urlopen(f"http://localhost:{PORT}/_stcore/health", timeout=2).read() == b"ok":
                break
        except Exception:
            time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        pg.goto(f"http://localhost:{PORT}/?demo=orders"); pg.wait_for_selector(".sh", timeout=60000); time.sleep(3)
        chips = pg.evaluate("() => document.querySelector('.tb-r').innerText")
        check("상단 칩: TILON GPU ONLINE", "TILON GPU ONLINE" in chips, chips.replace("\n", " | "))
        pg.get_by_role("button", name="TILON GPU 결과").click(); pg.wait_for_selector("[role=dialog]", timeout=30000); time.sleep(2)
        dlg = pg.evaluate("() => document.querySelector('[role=dialog]').innerText")
        check("팝업에 LIVE 섹션(ONLINE)과 실행 버튼", "LIVE" in dlg and "ONLINE" in dlg and "요약 카드 만들기" in dlg)
        t0 = time.time()
        pg.get_by_role("button", name="틸론 GPU로 요약 카드 만들기").click()
        pg.wait_for_function("document.querySelector('[role=dialog]').innerText.includes('STUB-GPU')", timeout=40000)
        took = time.time() - t0; time.sleep(1.5)
        dlg = pg.evaluate("() => document.querySelector('[role=dialog]').innerText").lower()
        check("실행 결과가 팝업에 표시됨 (LIVE · 시간 · GPU · 프롬프트 B)", "stub-gpu" in dlg and "prompt b" in dlg and "live · 1." in dlg, dlg[:300])
        print(f"      클릭 → 결과 표시 {took:.1f}초 (워커 지연 1.5초 + 폴링)")
        check("워커가 실제로 받은 사건이 앱의 분석 결과와 같음", bool(seen) and seen[0]["case_name"] == "cancel_revenue 변화 사건" and len(seen[0]["evidence"]) == 11, str(seen[:1])[:160])
        check("워커에 계약 키만 전달됨 (debug 등 없음)", set(seen[0]) == {"case_name", "period", "evidence"})
        check("저장된 결과 섹션도 함께 표시", "saved results" in dlg and "orders_nodate" in dlg)
        pg.screenshot(path=f"{pre}_live.png")
        pg.keyboard.press("Escape"); time.sleep(1)
        stop["v"] = True; worker.join(timeout=8)
        time.sleep(16)                                     # 상태 캐시(15초)가 지나도록
        pg.goto(f"http://localhost:{PORT}/?demo=orders"); pg.wait_for_selector(".sh", timeout=60000); time.sleep(3)
        chips = pg.evaluate("() => document.querySelector('.tb-r').innerText")
        check("워커를 멈추면 칩이 중립 문구(TILON GPU · SAVED RESULTS)로 바뀜", "TILON GPU · SAVED RESULTS" in chips and "TILON GPU ONLINE" not in chips and "TILON GPU OFFLINE" not in chips, chips.replace("\n", " | "))
        pg.get_by_role("button", name="TILON GPU 결과").click(); pg.wait_for_selector("[role=dialog]", timeout=30000); time.sleep(2)
        dlg = pg.evaluate("() => document.querySelector('[role=dialog]').innerText").lower()
        check("꺼져 있으면 실행 버튼 없이 안내 + 저장된 결과로 폴백", "저장된 결과를 보여드립니다" in dlg and "요약 카드 만들기" not in dlg and "case · orders" in dlg, dlg[:300])
        pg.screenshot(path=f"{pre}_offline.png")
        b.close()
finally:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    srv.close()
    shutil.rmtree(tmp.parent, ignore_errors=True)
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
