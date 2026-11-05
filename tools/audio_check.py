"""audio_check.py - 클릭 동작에서 음성이 어떤 순서로 실제 재생되는지 브라우저에서 측정한다 (개발용).
python tools/audio_check.py [시나리오: demo | start | url]
  demo : 사이드바 시연 버튼 클릭          start: 시연 로드 후 AI 검토 시작하기 클릭(저장된 결과 사용 X, API 호출함)
  url  : ?demo=orders 주소로 직접 열기(클릭 없음)"""
import sys, time
from playwright.sync_api import sync_playwright

mode = sys.argv[1] if len(sys.argv) > 1 else "demo"
SIZES = {11232: "detecting", 13392: "found", 14976: "solved"}
HOOK = """() => { window.__log = []; const t0 = performance.now();
  const note = (k, a) => window.__log.push({k, t: Math.round(performance.now() - t0), src: a.currentSrc || a.src, dur: a.duration});
  ['play', 'playing', 'ended', 'pause', 'error'].forEach(ev => document.addEventListener(ev, e => { if (e.target.tagName === 'AUDIO') note(ev, e.target); }, true));
  new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { const a = n.querySelector ? (n.tagName === 'AUDIO' ? n : n.querySelector('audio')) : null; if (a) note('added', a); }))).observe(document.body, {childList: true, subtree: true}); }"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 900})
    pg.goto("http://localhost:8501/?demo=orders" if mode == "url" else "http://localhost:8501/")
    pg.wait_for_selector(".sh, .hero2", timeout=60000)
    pg.evaluate(HOOK)
    time.sleep(2)
    if mode in ("demo", "start"):
        pg.get_by_role("button", name="주문 데이터").click()
        time.sleep(7)
    if mode == "start":
        pg.get_by_role("button", name="AI 검토 시작하기").click()
        pg.wait_for_function("document.body.innerText.includes('AI 검토 완료') || document.body.innerText.includes('규칙 기반 분석 완료')", timeout=150000)
        time.sleep(6)
    log = pg.evaluate("() => window.__log")
    # 파일 크기로 어떤 음성인지 식별
    names = {}
    for e in log:
        s = e["src"]
        if s and s not in names:
            n = pg.evaluate("async (u) => (await (await fetch(u)).arrayBuffer()).byteLength", s)
            names[s] = SIZES.get(n, f"{n}B")
    print(f"시나리오: {mode} | 이벤트 {len(log)}개")
    for e in log:
        if e["k"] in ("added", "play", "playing", "ended", "error"):
            print(f"  {e['t']:6d}ms  {e['k']:8s} {names.get(e['src'], '?'):10s} 길이 {e['dur'] if e['dur'] == e['dur'] else '-'}")
    b.close()
