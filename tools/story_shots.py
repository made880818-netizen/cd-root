"""story_shots.py - 소개 화면을 스크롤하며 구간별로 캡처한다 (개발용).
python tools/story_shots.py 출력접두사 [폭] [화면높이] [--guide]"""
import sys, time
from playwright.sync_api import sync_playwright

pre = sys.argv[1] if len(sys.argv) > 1 else "story"
w = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1500
h = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 900
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": w, "height": h})
    pg.goto("http://localhost:8501/")
    pg.wait_for_selector(".hero2", timeout=60000)
    time.sleep(3)
    if "--guide" in sys.argv:
        pg.get_by_role("tab", name="사용 가이드").click(); time.sleep(2)
        pg.screenshot(path=f"{pre}_guide.png"); print("saved guide"); b.close(); sys.exit()
    sh = pg.evaluate("() => { const m = document.querySelector('[data-testid=\"stMain\"]'); return m.scrollHeight; }")
    print("전체 높이", sh, "px / 화면", h)
    y, i = 0, 0
    while y < sh:
        pg.evaluate(f"() => document.querySelector('[data-testid=\"stMain\"]').scrollTo(0, {y})")
        time.sleep(1.6)                                   # 스크롤 연동 등장 효과가 끝나도록
        pg.screenshot(path=f"{pre}_{i}.png")
        y += h - 100; i += 1
    print("장면 수:", i)
    ex = pg.locator("[data-testid='stException']").count()
    print("예외:", ex)
    b.close()
