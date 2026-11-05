"""test_quota.py - AI 검토 횟수 제한을 검증한다 (서버 전체 시간당 상한 + 앱의 방문자당 상한).  python tests/test_quota.py"""
import os, sys, pathlib, threading
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import quota

fails = []


def check(title, ok, note=""):
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {note}" if not ok else ""))
    if not ok:
        fails.append(title)


cap = quota.HourlyCap(3, window=100)
check("상한 3까지는 허용", all(cap.try_acquire(now=t) for t in (0, 1, 2)))
check("4번째는 막힘", cap.try_acquire(now=3) is False and cap.used(now=3) == 3)
check("시간 창(100초)이 지나면 오래된 기록이 빠져 다시 허용", cap.used(now=101) == 1 and cap.try_acquire(now=101) is True and cap.used(now=101) == 2, f"사용 {cap.used(now=101)}")
cap2 = quota.HourlyCap(50, window=3600)
res = []
ths = [threading.Thread(target=lambda: res.append(cap2.try_acquire())) for _ in range(200)]
[t.start() for t in ths]; [t.join() for t in ths]
check("동시에 200번 눌러도 정확히 50번만 허용", sum(res) == 50 and cap2.used() == 50, f"허용 {sum(res)}")

# 앱: 방문자당 0회로 두고 AI가 켜진 상태에서 AI 검토를 누르면 API를 부르지 않고 안내만 나와야 한다
os.environ.update({"AI_RUNS_PER_SESSION": "0", "DISABLE_GPU_LINK": "1", "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "test-key-not-used")})
os.environ.pop("DISABLE_LLM", None)
from streamlit.testing.v1 import AppTest
import time
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
at.run(); at.button(key="start_demo").click().run()
btn = [b for b in at.button if "AI 검토 시작" in b.label]
check("AI가 켜진 상태면 'AI 검토 시작하기' 버튼이 보임", bool(btn), str([b.label for b in at.button])[:200])
if btn:
    t0 = time.time(); btn[0].click().run(); took = time.time() - t0
    warns = " ".join(w.value for w in at.warning)
    check("한도를 넘으면 AI를 호출하지 않고 안내가 나옴 (즉시 끝남)", "횟수를 모두 썼어요" in warns and took < 8, f"{took:.1f}초 | {warns[:80]}")
    check("한도 때문에 막혀도 결과 화면은 그대로 (예외 없음)", not at.exception and "원인 후보는 무엇인가요?" in " ".join(str(e.value) for e in at.get("html")))
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
