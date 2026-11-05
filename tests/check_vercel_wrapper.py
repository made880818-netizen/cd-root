"""check_vercel_wrapper.py - Vercel 입구 페이지(vercel/)가 Streamlit 앱을 iframe으로 제대로 보여주는지 로컬에서 확인한다.
(실제 Vercel/Streamlit Cloud가 아니라 로컬 서버 두 개로 흉내낸 것이다.)  앱이 http://localhost:8501 에서 켜져 있어야 한다.
python tests/check_vercel_wrapper.py"""
import sys, shutil, tempfile, threading, functools, pathlib, time, http.server
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="vercel_wrapper_"))
shutil.copytree(ROOT / "vercel", tmp / "site")
(tmp / "site" / "config.js").write_text('window.CDROOT_APP_URL = "http://localhost:8501/";\n', encoding="utf-8")   # 시험용으로만 로컬 주소로 바꾼 복사본
h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp / "site"))
h.log_message = lambda *a, **k: None
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}/"
fails = []


def check(title, ok, note=""):
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {note}" if not ok else ""))
    if not ok:
        fails.append(title)


try:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.goto(base)
        fr = pg.frame_locator("#app")
        fr.locator(".hero2").first.wait_for(timeout=60000)
        check("입구 페이지 안에 앱 소개 화면이 뜸 (iframe)", fr.locator(".hero2").count() >= 1)
        check("iframe이 화면 전체를 채움", pg.evaluate("() => { const r = document.getElementById('app').getBoundingClientRect(); return r.width >= innerWidth - 2 && r.height >= innerHeight - 2; }"))
        check("새 탭 열기 링크가 앱 주소를 가리킴", pg.get_attribute("#open", "href") == "http://localhost:8501/")
        check("iframe에 autoplay 허용이 걸려 있음 (음성 안내용)", "autoplay" in (pg.get_attribute("#app", "allow") or ""))
        fr.get_by_role("button", name="시연 데이터로 바로 시작").click()
        fr.locator(".sh").first.wait_for(timeout=60000)
        check("iframe 안에서 클릭하면 결과 화면으로 넘어감 (앱이 정상 동작)", fr.locator(".sh").count() >= 4, f"{fr.locator('.sh').count()}개")
        check("결과 화면에 AI CORE와 그래프가 있음", fr.locator("img[alt^='AI Investigation Core']").count() == 1)
        pg.screenshot(path=str(tmp / "wrapper.png"))
        b.close()
finally:
    httpd.shutdown()
print("스크린샷:", tmp / "wrapper.png")
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
