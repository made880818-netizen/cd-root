"""flow_check.py - 소개 → 시작/업로드 → 분석 → 소개 복귀 → GPU 팝업 흐름을 브라우저에서 끝까지 확인한다 (개발용).
python tools/flow_check.py [출력접두사]"""
import sys, time, pathlib
from playwright.sync_api import sync_playwright

pre = sys.argv[1] if len(sys.argv) > 1 else "flow"
ROOT = pathlib.Path(__file__).resolve().parent.parent
URL = "http://localhost:8501/"


def state(pg):
    return pg.evaluate("""() => ({intro: !!document.querySelector(".hero2"), dash: !!document.querySelector('.sh'),
      tabs: [...document.querySelectorAll('button[role=tab]')].map(t => t.innerText.trim()),
      dialog: !!document.querySelector('[role=dialog]'), exc: document.querySelectorAll("[data-testid='stException']").length,
      dataset: (document.querySelector('.tb-r')||{innerText:''}).innerText.split('\\n').find(x => x.startsWith('DATASET')) || 'NO DATASET'})""")


with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    pg.goto(URL); pg.wait_for_selector(".hero2", timeout=60000); time.sleep(2)
    print("1 입장        ", state(pg))
    pg.screenshot(path=f"{pre}_1_intro.png", full_page=False)
    pg.get_by_role("button", name="시연 데이터로 시작하기").click()
    pg.wait_for_selector(".sh", timeout=60000); time.sleep(3)
    print("2 시연 시작    ", state(pg))
    pg.get_by_role("button", name="소개 · 사용법").click()
    pg.wait_for_selector(".hero2", timeout=30000); time.sleep(1.5)
    s = state(pg); print("3 소개로 복귀  ", s, "| '돌아가기' 버튼:", pg.get_by_role("button", name="분석 화면으로 돌아가기").count())
    pg.get_by_role("button", name="분석 화면으로 돌아가기").click()
    pg.wait_for_selector(".sh", timeout=30000); time.sleep(2)
    print("4 분석 복귀    ", state(pg))
    pg.get_by_role("button", name="TILON GPU 결과").click()
    pg.wait_for_selector("[role=dialog]", timeout=30000); time.sleep(2.5)
    d = pg.evaluate("() => { const t = document.querySelector('[role=dialog]').innerText; return {len: t.length, has_cases: ['orders', 'orders_nodate', 'orders_revenue'].filter(c => t.toLowerCase().includes(c)), live: t.includes('실시간으로 호출하지 않')}; }")
    print("5 GPU 팝업     ", state(pg)["dialog"], d)
    pg.screenshot(path=f"{pre}_5_gpu.png")
    pg.keyboard.press("Escape"); time.sleep(1)
    # 내 CSV 업로드 (소개 화면의 업로더)
    pg.get_by_role("button", name="소개 · 사용법").click()
    pg.wait_for_selector(".hero2", timeout=30000); time.sleep(1.5)
    pg.locator("[data-testid='stFileUploaderDropzone'] input[type=file]").last.set_input_files(str(ROOT / "data" / "sample-missing-values.csv"))
    time.sleep(7)
    print("6 업로드       ", state(pg))
    pg.screenshot(path=f"{pre}_6_upload.png")
    b.close()
