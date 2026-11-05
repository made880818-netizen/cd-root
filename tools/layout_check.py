"""layout_check.py - 창 폭별로 가로 넘침과 패널 겹침을 측정하고 스크린샷을 저장한다 (개발용).
python tools/layout_check.py [출력접두사] [폭,폭,...]"""
import sys, time
from playwright.sync_api import sync_playwright

pre = sys.argv[1] if len(sys.argv) > 1 else "lay"
widths = [int(w) for w in (sys.argv[2] if len(sys.argv) > 2 else "1600,1280,1000,800,560").split(",")]
JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); const d = e.closest('details'); return r.width > 20 && r.height > 12 && !(d && !d.open); };
  const els = [...document.querySelectorAll('.pn, .sh')].filter(vis);
  const over = [];
  for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
    const a = els[i], b = els[j];
    if (a.contains(b) || b.contains(a)) continue;
    const r1 = a.getBoundingClientRect(), r2 = b.getBoundingClientRect();
    const w = Math.min(r1.right, r2.right) - Math.max(r1.left, r2.left);
    const h = Math.min(r1.bottom, r2.bottom) - Math.max(r1.top, r2.top);
    if (w > 3 && h > 3) over.push((a.querySelector('.pn-t')||a).innerText.slice(0, 22) + ' x ' + (b.querySelector('.pn-t')||b).innerText.slice(0, 22));
  }
  const clipped = [...document.querySelectorAll('.kv .v, .tl-m, .tile .v, .pn-t')].filter(e => e.scrollWidth > e.clientWidth + 2 && getComputedStyle(e).overflow !== 'visible').length;
  const main = document.querySelector('[data-testid="stMain"]') || document.documentElement;
  const fs = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return {overlaps: over, hOverflow: main.scrollWidth - main.clientWidth, clipped, rootFont: fs, panels: els.length};
}"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    for w in widths:
        pg = b.new_page(viewport={"width": w, "height": 1000})
        pg.goto("http://localhost:8501/?demo=orders")
        pg.wait_for_selector(".sh", timeout=60000)
        time.sleep(3)
        r = pg.evaluate(JS)
        pg.screenshot(path=f"{pre}_{w}.png")
        print(f"[{w:4d}px] 루트글자 {r['rootFont']:.1f}px | 패널 {r['panels']}개 | 겹침 {len(r['overlaps'])} | 가로넘침 {r['hOverflow']}px | 잘린텍스트 {r['clipped']}"
              + (f" | 예: {r['overlaps'][:2]}" if r["overlaps"] else ""))
        pg.close()
    b.close()
