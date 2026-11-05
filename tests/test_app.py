"""test_app.py - Streamlit 화면을 실제로 구동해 본다.  python tests/test_app.py [--llm]"""
import sys, pathlib
from streamlit.testing.v1 import AppTest

ROOT = pathlib.Path(__file__).resolve().parent.parent
use_llm = "--llm" in sys.argv
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
fails = []


def check(title, at, cond=True, note=""):
    errs = [e.value for e in at.exception]
    ok = not errs and cond
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {errs or note}" if not ok else ""))
    if not ok:
        fails.append(title)


def html_text(at):
    return " ".join(str(e.value) for e in at.get("html"))


at.run()
check("입장하면 소개 화면 (타깃 히어로 → 5단계 스토리)", at,
      all(k in html_text(at) for k in ("AUTONOMOUS DATA INVESTIGATION", "01 · INPUT", "02 · DETECT", "03 · DEBATE", "04 · VERIFY", "05 · RESULT")))
check("스토리 5단계마다 누가·어디서 실행하는지 표시", at, html_text(at).count("WHO · ") >= 5 and html_text(at).count("WHERE · ") >= 5)
check("소개 화면 탭은 [소개, 사용 가이드]뿐", at, [t.label for t in at.tabs] == ["소개", "사용 가이드"], str([t.label for t in at.tabs]))
check("자세한 조건·설명은 사용 가이드 탭에", at, all(k in html_text(at) for k in ("DATA CONDITIONS", "WHAT RUNS WHERE", "LIMITS")))
check("소개 끝에 TILON GPU 단계 + 큰 숫자(커버리지 A→B, VRAM)", at, "TILON GPU" in html_text(at) and "증거 커버리지 A → B" in html_text(at) and "최대 VRAM" in html_text(at))
check("연동 미설정이면 칩이 중립 문구(TILON GPU · SAVED RESULTS)", at, "TILON GPU · SAVED RESULTS" in html_text(at) and "LINK OFF" not in html_text(at))
at.toggle(key="present").set_value(True).run()
check("발표 모드 켜면 글자 1.2배 CSS 적용", at, "clamp(14.4px" in html_text(at))
at.toggle(key="present").set_value(False).run()
check("발표 모드 끄면 원래대로", at, "clamp(14.4px" not in html_text(at))
check("시작하기/업로드 안내 버튼 존재", at, any("시연 데이터로 시작하기" in b.label for b in at.button))
at.button(key="start_demo").click().run()
check("시작하기 -> 분석 화면(AI CORE)", at, "AI INVESTIGATION CORE" in html_text(at))
labels = [t.label for t in at.tabs]
check("탭 4개 (우리말 이름, GPU는 사이드바 팝업)", at, labels == ["결과 보기", "AI 토론·보고서", "증거 자료", "에이전트 상태"], str(labels))
heads = ["무슨 일이 일어났나요?", "원인 후보는 무엇인가요?", "AI 에이전트가 함께 검토합니다", "결론과 다음에 할 일"]
pos = [html_text(at).find(h) for h in heads]
check("결과 화면이 번호 순서(1→2→3→4)로 위에서 아래로 나옴", at, all(x >= 0 for x in pos) and pos == sorted(pos), str(pos))
check("단계 번호 1·2·3·4 머리글", at, all(f'class="sh-n">{n}<' in html_text(at) for n in "1234"))
check("AI CORE는 그대로 유지", at, "AI INVESTIGATION CORE" in html_text(at))
check("영어 약어 패널이 사라짐 (INSPECTOR/RECALC/UNGROUNDED/LEDGER)", at, not any(k in html_text(at) for k in ("INVESTIGATION INSPECTOR", "RECALC CHECK", "UNGROUNDED #", "ROOT CAUSE CANDIDATES")))
check("사이드바에 TILON GPU 버튼", at, any("TILON GPU" in b.label for b in at.sidebar.button))
at.button(key="menu_intro").click().run()
check("소개·사용법 메뉴 -> 소개 화면 복귀", at, "01 · INPUT" in html_text(at) and "AI INVESTIGATION CORE" not in html_text(at))
at.button(key="back_app").click().run()
check("분석 화면으로 돌아가기 -> 데이터 유지", at, "AI INVESTIGATION CORE" in html_text(at))
sel = at.selectbox(key="metric_pick")
idx = next((i for i, o in enumerate(sel.options) if "cancel_qty" in o), None)
check("다른 지표 드롭다운에 후보 표시", at, idx is not None, str(sel.options))
if idx is not None:
    sel.select_index(idx).run()
    check("지표를 고르면 그 지표로 다시 분석 (문장에 반영)", at, "<b>cancel_qty</b> 값이" in html_text(at), "재계산 반영 안 됨")
at.selectbox(key="metric_pick").select_index(0).run()
check("자동 선택으로 복귀 -> cancel_revenue", at, "<b>cancel_revenue</b> 값이" in html_text(at))
check("AI 검토 전에는 '잠정 결과'로 표시", at, "AI 검토 전 · 코드 점수 기준 잠정 결과" in html_text(at))
go = lambda: [b for b in at.button if "검토 시작" in b.label or "규칙 기반으로 분석" in b.label][0]
if use_llm:
    go().click().run()
    check("AI 검토 실행 -> 결론 카드가 'AI 검토 완료'", at, "AI 검토 완료" in html_text(at), "완료 문구 없음")
    print("   chat_message 수:", len(at.chat_message), "| 경고:", [w.value[:50] for w in at.warning])
else:
    go().click().run()
    check("규칙 모드 실행 -> 'AI는 쓰지 않았어요'로 정직하게 표시", at, "규칙 기반 분석 완료" in html_text(at) and "AI 검토 완료" not in html_text(at))
check("완료 토스트(RANK 1) 표시", at, "RANK 1" in html_text(at), "토스트 없음")
at.selectbox(key="metric_pick").select_index(0).run()
check("다른 조작 뒤에는 토스트가 다시 뜨지 않음", at, "RANK 1" not in html_text(at), "토스트가 남아 있음")
at.button(key="demo_inventory.csv").click().run()
check("시연: 재고 -> 사건 없음 안내(친절한 화면)", at, "사건을 찾지 못했어요" in html_text(at))
at.button(key="demo_orders.csv").click().run()
check("시연 버튼 재선택(업로드 덮어쓰기 시나리오는 미시험)", at, "orders.csv" in html_text(at) and "무슨 일이 일어났나요?" in html_text(at))
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
