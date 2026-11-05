"""shot.py - 실행 중인 앱을 Edge로 열어 화면을 캡처한다 (개발용).
python tools/shot.py 출력.png [--url=...] [--tab=INVESTIGATION] [--click=텍스트] [--wait=초] [--w=1600] [--h=1000] [--full]"""
import sys, time
from playwright.sync_api import sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
opt = {a.split("=")[0]: a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--") and "=" in a}
out = args[0] if args else "shot.png"
url = opt.get("--url", "http://localhost:8501/?demo=orders")
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": int(opt.get("--w", 1600)), "height": int(opt.get("--h", 1000))})
    msgs = []
    pg.on("console", lambda m: msgs.append(m.text) if m.type == "error" else None)
    pg.goto(url)
    pg.wait_for_selector(".pn, .stApp [data-testid='stException']", timeout=60000)
    time.sleep(float(opt.get("--wait", 3)))
    if "--click" in opt:
        pg.get_by_text(opt["--click"], exact=False).first.click()
        time.sleep(float(opt.get("--wait2", 6)))
    pg.screenshot(path=out, full_page="--full" in sys.argv)
    if "--tab" in opt:                                   # 탭 화면은 *_tab.png로 따로 저장
        pg.get_by_role("tab", name=opt["--tab"]).click()
        time.sleep(2)
        pg.screenshot(path=out.replace(".png", "_tab.png"), full_page="--full" in sys.argv)
    ex = pg.locator("[data-testid='stException']").count()
    print(f"saved {out} | exceptions on page: {ex} | console errors: {len(msgs)}")
    for m in msgs[:5]:
        print("  console:", m[:160])
    b.close()
