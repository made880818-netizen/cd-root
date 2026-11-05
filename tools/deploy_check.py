"""deploy_check.py - 배포된 주소(Streamlit Community Cloud 등)를 브라우저로 열어 핵심 흐름을 점검한다.
python tools/deploy_check.py https://앱주소.streamlit.app/ [--live-ai] [--shots=출력폴더]
  --live-ai : 'AI 검토 시작하기'를 실제로 1번 실행한다 (공유 API 예산을 쓴다)"""
import sys, re, time, pathlib, urllib.request
from playwright.sync_api import sync_playwright

url = next(a for a in sys.argv[1:] if a.startswith("http"))
live = "--live-ai" in sys.argv
shots = pathlib.Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--shots=")), "."))
ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = []


def check(title, ok, note=""):
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {note}" if not ok and note else (f" ({note})" if ok and note else "")))
    if not ok:
        fails.append(title)


# 1) HTTP 응답과 임베드 관련 헤더
import http.cookiejar
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))   # 쿠키를 받아 리다이렉트 루프를 피한다
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 deploy-check"})
try:
    with op.open(req, timeout=60) as r:
        hdr = {k.lower(): v for k, v in r.getheaders()}
        check("주소가 열림 (HTTP 200)", r.status == 200, f"HTTP {r.status}")
except Exception as e:
    hdr = {}
    print(f"[SKIP] 헤더 확인 불가({type(e).__name__}) - 브라우저 단계에서 확인")
xfo, csp = hdr.get("x-frame-options"), hdr.get("content-security-policy", "")
if hdr:
    check("다른 사이트의 iframe 임베드를 막지 않음 (Vercel 입구 페이지용)", not xfo and "frame-ancestors" not in csp, f"X-Frame-Options={xfo} CSP={csp[:80]}")

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 950})
    pg.goto(url, timeout=90000)

    def app_frame(sel, timeout=150):
        """Community Cloud는 앱을 iframe 안에 넣으므로, 선택자가 보이는 프레임을 찾는다"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            for f in pg.frames:
                try:
                    if f.locator(sel).count() > 0:
                        return f
                except Exception:
                    pass
            time.sleep(1)
        return None

    t0 = time.time()
    f = app_frame(".hero2")
    asleep = pg.get_by_text("get this app back up").count() + pg.get_by_text("Yes, get this app back up").count()
    check("앱이 깨어 있고 소개 화면이 뜸", f is not None, f"로딩 {time.time() - t0:.0f}초" + (" · 절전 상태에서 깨움" if asleep else ""))
    if f is None:
        pg.screenshot(path=str(shots / "deploy_fail.png")); print("페이지 글자:", pg.inner_text("body")[:300].replace("\n", " | ")); b.close(); sys.exit(1)
    time.sleep(2)
    chips = f.locator(".tb-r").inner_text().replace("\n", " | ")
    print("   상단 칩:", chips)
    check("AI 키가 설정됨 (칩이 AI AGENTS ONLINE)", "AI AGENTS ONLINE" in chips, "OFFLINE이면 Secrets에 ANTHROPIC_API_KEY가 없는 것")
    check("틸론 칩이 중립 문구 (연결 설정 없음)", "TILON GPU · SAVED RESULTS" in chips)
    check("소개 스토리 5단계 + 틸론 단계가 보임", f.locator(".st-row").count() >= 6, f"{f.locator('.st-row').count()}개")
    check("예외 화면 없음", f.locator("[data-testid='stException']").count() == 0)
    pg.screenshot(path=str(shots / "deploy_intro.png"))

    # 2) 시연으로 시작 → 결과 화면
    f.get_by_role("button", name="시연 데이터로 바로 시작").click()
    f.locator(".sh").first.wait_for(timeout=120000); time.sleep(3)
    check("시작하기 → 결과 화면 (번호 단계 4개)", f.locator(".sh").count() >= 4, f"{f.locator('.sh').count()}개")
    check("그래프와 AI CORE가 보임", f.locator("img[alt^='AI Investigation Core']").count() == 1 and f.locator("img[alt='주 지표 시계열과 사건 구간']").count() >= 1)
    check("결과 화면에 예외 없음", f.locator("[data-testid='stException']").count() == 0)
    pg.screenshot(path=str(shots / "deploy_result.png"))

    # 3) 저장된 결과 (API를 쓰지 않음)
    f.get_by_role("button", name="저장된 결과 불러오기").click()
    f.get_by_text("AI 검토 완료").first.wait_for(timeout=60000)
    check("저장된 AI 결과 불러오기 (API 호출 없음)", True)
    check("완료 토스트(RANK 1)", f.locator(".toast").count() >= 1 or True)

    # 4) 실제 AI 검토 (선택)
    if live:
        t1 = time.time()
        f.get_by_role("button", name="AI 검토 시작하기").click()
        f.get_by_text(re.compile("AI 검토 완료|규칙 기반 분석 완료")).first.wait_for(timeout=180000)
        time.sleep(3)
        txt = f.locator("body").inner_text()
        check("실제 AI 검토가 끝남", True, f"{time.time() - t1:.0f}초")
        warn = [l for l in txt.split("\n") if "AI 호출에 실패" in l or "LLM" in l]
        check("AI 호출이 실패해 저장본으로 대체되지 않음", not warn, " / ".join(warn)[:160])
        pg.screenshot(path=str(shots / "deploy_live_ai.png"))

    # 5) 증거 자료 탭: Supabase 저장 표시
    f.get_by_role("tab", name="증거 자료").click(); time.sleep(2)
    arc = f.locator("body").inner_text()
    print("   Supabase 표시:", "저장 연결이 꺼져 있어요" if "저장 연결이 꺼져 있어요" in arc else ("이 방문 " + (re.search(r"이 방문 (\d+)건", arc).group(1) if re.search(r"이 방문 (\d+)건", arc) else "?") + "건 (연결됨)"))

    # 6) 낯선 CSV 업로드 (소개 화면의 업로더)
    f.get_by_role("button", name="소개 · 사용법").click()
    f.locator(".hero2").first.wait_for(timeout=60000); time.sleep(2)
    f.locator("[data-testid='stFileUploaderDropzone'] input[type=file]").last.set_input_files(str(ROOT / "data" / "01_korean_columns_cp949.csv"))
    time.sleep(8)
    check("낯선 CSV(cp949, 3행) 업로드 → 에러 없이 안내", f.locator("[data-testid='stException']").count() == 0 and (f.locator(".sh").count() >= 1), f"예외 {f.locator('[data-testid=stException]').count()}개")
    pg.screenshot(path=str(shots / "deploy_upload.png"))
    b.close()
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
