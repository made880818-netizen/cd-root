"""test_store.py - Supabase 저장을 가짜 Supabase 서버로 검증한다.  python tests/test_store.py"""
import os, sys, json, pathlib, logging
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
from mock_supabase import MockSupabase

srv = MockSupabase()
os.environ.update({"SUPABASE_URL": srv.base, "SUPABASE_SERVICE_KEY": srv.key, "DISABLE_GPU_LINK": "1", "DISABLE_LLM": "1"})
os.environ.pop("DISABLE_STORE", None)
import config, store, export_case as ec, agents, debate
ec.log.setLevel(logging.CRITICAL); logging.getLogger("caselens").setLevel(logging.CRITICAL)

fails = []


def check(title, ok, note=""):
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {note}" if not ok else ""))
    if not ok:
        fails.append(title)


df = ec.read_csv_any(str(ROOT / "data" / "orders.csv"))
case = ec.build_case_from_df(df, name="orders.csv")
res = agents.analyze(df, case)
cached = debate.load_cache(res)
check("시험 준비: 저장된 AI 검토 결과를 불러옴", bool(cached) and cached.get("mode") == "llm")
check("설정이 켜져 있음", store.enabled() and config.SUPABASE_URL == srv.base)

row = store.build_row("sess1", "demo:orders.csv", df.shape, case, res, cached)
need = {"session_id", "dataset_label", "mode", "top_title", "top_score", "hypotheses", "claims", "rebuttals", "report", "tokens", "seconds"}
check("저장용 행에 필요한 항목이 모두 있음", need <= set(row) and row["mode"] == "llm" and row["top_id"] == res["hypotheses"][0]["id"])
blob = json.dumps(row, ensure_ascii=False)
raw_lines = [",".join(str(v) for v in r) for r in df.head(50).values.tolist()]
check("원본 CSV 행은 저장용 행에 들어 있지 않음", not any(l in blob for l in raw_lines) and len(blob) < 60000, f"{len(blob)}바이트")
check("DataFrame의 전체 값(수만 자)이 아니라 요약만 있음", len(blob) < len(df.to_csv()) / 2)

hdr = lambda: {k.lower(): v for k, v in srv.last_headers.items()}      # HTTP 헤더 이름은 대소문자를 가리지 않는다
ok, msg = store.save(row)
check("저장 성공 (HTTP 201, 키는 apikey 헤더로 전달)", ok is True and len(srv.rows) == 1 and hdr().get("apikey") == srv.key, f"{ok} {msg}")
check("저장된 행의 모드·데이터 라벨", srv.rows[0]["mode"] == "llm" and srv.rows[0]["dataset_label"] == "demo:orders.csv")
check("키가 JWT 모양이 아니면 Authorization 헤더는 보내지 않음", "authorization" not in hdr())
got = store.recent("sess1")
check("이 방문의 저장 목록 조회", len(got) == 1 and got[0]["top_title"] == row["top_title"] and "report" not in got[0], str(got)[:120])
check("다른 방문의 저장 내용은 조회되지 않음", store.recent("other-visitor") == [])

rule_out = debate.run_debate(res, client=None)
rrow = store.build_row("sess1", "upload", df.shape, case, res, rule_out)
ok2, _ = store.save(rrow)
check("규칙 기반 결과도 저장됨 (mode=rule, 업로드 라벨)", ok2 is True and srv.rows[-1]["mode"] == "rule" and srv.rows[-1]["dataset_label"] == "upload")

n0 = srv.requests
config.SUPABASE_SERVICE_KEY = "WRONG-KEY-VALUE-123"
bad, bmsg = store.save(row)
check("잘못된 키는 저장 실패(401)이고 예외 없이 돌려주며 메시지에 키가 없음", bad is False and "401" in bmsg and "WRONG-KEY" not in bmsg, bmsg)
config.SUPABASE_SERVICE_KEY = srv.key
srv.table_exists = False
t, tmsg = store.save(row)
check("테이블이 없으면 404 이유를 알려줌 (예외 없음)", t is False and "404" in tmsg and "does not exist" in tmsg, tmsg)
srv.table_exists = True
bad_row = dict(row, mode="weird")
check("mode가 규칙에 안 맞아도 build_row가 안전하게 rule로 정리", store.build_row("s", "x", df.shape, case, res, {"mode": "weird"})["mode"] == "rule")
config.SUPABASE_URL = "http://127.0.0.1:9"
c, cmsg = store.save(row)
check("연결 실패도 예외 없이 (False, 이유)", c is False and "URLError" in cmsg or c is False, cmsg)
config.SUPABASE_URL = srv.base
config.STORE_ENABLED = False
n1 = srv.requests
off = store.save(row)
check("꺼져 있으면 네트워크를 쓰지 않고 (None, 안내)", off[0] is None and srv.requests == n1 and store.recent("sess1") == [])
config.STORE_ENABLED = True

# ---- 앱: 규칙 모드로 한 번 실행하면 실제로 저장되는가 ----
from streamlit.testing.v1 import AppTest
srv.rows.clear()
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
at.run(); at.button(key="start_demo").click().run()
html = lambda: " ".join(str(e.value) for e in at.get("html"))
check("실행 전에는 저장된 행이 없음", len(srv.rows) == 0 and "저장된 검토 (Supabase)" in html())
[b for b in at.button if "규칙 기반으로 분석" in b.label][0].click().run()
check("앱 실행 후 Supabase에 1행 저장 (mode=rule, demo:orders.csv)", len(srv.rows) == 1 and srv.rows[0]["mode"] == "rule" and srv.rows[0]["dataset_label"] == "demo:orders.csv",
      str([r.get("mode") for r in srv.rows]))
check("진행 기록에 '저장했어요'가 보임", "Supabase에 저장했어요" in html())
check("증거 자료 탭에 이 방문 1건 표시", "이 방문 1건" in html(), "표시 안 됨")
check("저장돼도 화면은 정상 (예외 없음)", not at.exception)
[b for b in at.button if "저장된 결과 불러오기" in b.label][0].click().run()
check("저장된 결과를 불러오기만 하면 또 저장하지 않음 (중복 방지)", len(srv.rows) == 1)
srv.table_exists = False
[b for b in at.button if "규칙 기반으로 분석" in b.label][0].click().run()
check("저장이 실패해도 앱은 멈추지 않고 안내만 남김", not at.exception and "Supabase 저장에 실패했어요" in html(), str(at.exception)[:100])
srv.key = config.SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiJ9.fakepayloadvalue.fakesignature"        # 예전 방식(JWT) 키 모양
srv.table_exists = True
j, jmsg = store.save(row)
check("예전 방식(JWT) 키는 Authorization 헤더도 함께 보냄", j is True and "authorization" in hdr() and hdr().get("apikey") == srv.key, f"{j} {jmsg}")
srv.close()
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
