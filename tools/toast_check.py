"""toast_check.py - 완료 토스트가 5초쯤 떴다 사라지는지 브라우저에서 측정한다 (개발용).
python tools/toast_check.py [출력접두사]"""
import sys, time
from playwright.sync_api import sync_playwright

pre = sys.argv[1] if len(sys.argv) > 1 else "toast"
JS = """() => { const t = document.querySelector('.toast'); if (!t) return null;
  const s = getComputedStyle(t), r = t.getBoundingClientRect();
  return {opacity: +(+s.opacity).toFixed(2), visibility: s.visibility, pe: s.pointerEvents, pos: s.position,
          x: Math.round(r.left + r.width / 2), y: Math.round(r.top), w: Math.round(r.width), text: t.innerText.replace(/\\n/g, ' | ').slice(0, 120)}; }"""


def run(reduced):
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1500, "height": 900}, reduced_motion="reduce" if reduced else "no-preference")
        pg = ctx.new_page()
        pg.goto("http://localhost:8501/?demo=orders")
        pg.wait_for_selector(".sh", timeout=60000)
        time.sleep(4)
        print(f"--- 움직임 줄이기: {'켬' if reduced else '끔'} | 완료 전 .toast:", pg.evaluate("() => document.querySelectorAll('.toast').length"))
        pg.get_by_text("저장된 결과 불러오기").first.click()
        pg.wait_for_selector(".toast", timeout=20000)
        t0 = time.time()
        shot = False
        for target in (0.25, 0.7, 2.5, 4.5, 4.95, 5.5, 6.2):
            time.sleep(max(0, target - (time.time() - t0)))
            if target == 0.7 and not shot:
                pg.screenshot(path=f"{pre}_{'rm' if reduced else 'on'}_0p7s.png"); shot = True
            r = pg.evaluate(JS)
            print(f"  +{time.time() - t0:4.2f}s", r if r is None else {k: v for k, v in r.items() if k != "text"})
        if not reduced:
            print("  내용:", pg.evaluate(JS)["text"] if False else "(위 0.7s 스크린샷 참고)")
        # 이후 다른 조작에서는 다시 뜨지 않아야 한다
        pg.get_by_text("발표 모드").first.click()
        time.sleep(4)
        print("  다른 조작(AUTO 클릭) 뒤 .toast 개수:", pg.evaluate("() => document.querySelectorAll('.toast').length"))
        b.close()


run(False)
run(True)
