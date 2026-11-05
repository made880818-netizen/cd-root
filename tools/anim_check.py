"""anim_check.py - 조사 완료 시 하단 패널 애니메이션이 실제로 재생되는지 브라우저에서 측정한다 (개발용).
python tools/anim_check.py [출력접두사]"""
import sys, time
from playwright.sync_api import sync_playwright

pre = sys.argv[1] if len(sys.argv) > 1 else "anim"
JS = """() => { const a = document.getAnimations(); const c = {};
  a.forEach(x => { const n = x.animationName || x.constructor.name; c[n] = (c[n] || 0) + 1; });
  const sc = [...document.querySelectorAll('.cnt')].map(e => getComputedStyle(e, '::after').content);
  return {total: a.length, byName: c, counterTexts: sc}; }"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 1900})
    pg.goto("http://localhost:8501/?demo=orders")
    pg.wait_for_selector(".sh", timeout=60000)
    time.sleep(4)
    before = pg.evaluate(JS)
    print("완료 전  fx 패널:", pg.locator(".pn.fx").count(), "| 애니메이션:", {k: v for k, v in before["byName"].items() if k in ("grow", "count", "slidein", "sweep", "glow")})
    pg.get_by_text("저장된 결과 불러오기").first.click()
    pg.wait_for_function("document.body.innerText.includes('AI 검토 완료') || document.body.innerText.includes('규칙 기반 분석 완료')", timeout=30000)
    t0 = time.time()
    early = pg.evaluate(JS)
    pg.screenshot(path=f"{pre}_early.png")
    time.sleep(0.6)
    pg.screenshot(path=f"{pre}_mid.png")
    time.sleep(3.5)
    late = pg.evaluate(JS)
    pg.screenshot(path=f"{pre}_end.png")
    keep = ("grow", "count", "slidein", "sweep", "glow")
    print("완료 직후 fx 패널:", pg.locator(".pn.fx").count(), "| 애니메이션:", {k: v for k, v in early["byName"].items() if k in keep})
    print("카운터 표시값(직후):", early["counterTexts"][:6])
    print("4초 뒤 진행 중 애니메이션(핵심):", {k: v for k, v in late["byName"].items() if k in keep}, "| 카운터 표시값:", late["counterTexts"][:6])
    b.close()
