"""test_gpu_queue.py - 틸론 GPU 작업 큐를 가짜 GitHub 서버로 검증한다.  python tests/test_gpu_queue.py"""
import sys, time, threading, pathlib, datetime
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
import gpu_queue as q
from mock_github import MockGitHub

fails = []


def check(title, ok, note=""):
    print(f"[{'OK' if ok else 'FAIL'}] {title}" + (f" -> {note}" if not ok else ""))
    if not ok:
        fails.append(title)


CASE = {"case_name": "시험 사건", "period": {"baseline": "1~10", "event": "11~20"},
        "evidence": [{"id": "E1", "label": "변화율", "value": 85.7, "unit": "%"}, {"id": "E2", "label": "시작 시점", "value": "2026-09-30", "unit": ""}]}
srv = MockGitHub()
gh = q.GH(srv.repo, srv.token, base=srv.base, timeout=5)

check("워커가 없으면 OFFLINE (기록 없음)", q.status(gh)["online"] is False and "기록 없음" in q.status(gh)["reason"])

stop = {"v": False}
fake_run = lambda c: {"model": "stub", "selected": "B", "n_evidence": len(c["evidence"]), "name": c["case_name"]}
logs = []
th = threading.Thread(target=q.worker_loop, args=(gh, fake_run, lambda: {"gpu": "stub-gpu"}),
                      kwargs={"poll": 0.2, "beat_every": 0.5, "should_stop": lambda: stop["v"], "log": logs.append}, daemon=True)
th.start(); time.sleep(1.2)
st = q.status(gh, max_age=30)
check("워커가 켜지면 ONLINE + 정보 전달", st["online"] and st["info"].get("gpu") == "stub-gpu", str(st))

t0 = time.time(); no = q.submit(gh, CASE); res = q.wait_result(gh, no, timeout=10, every=0.3)
check("작업 → 결과 왕복 (웹→큐→워커→큐→웹)", res.get("ok") is True and res["n_evidence"] == 2 and res["name"] == "시험 사건", str(res))
check("결과에 처리 시간이 붙고 작업 이슈가 닫힘", "seconds" in res and srv.issues[no - 1]["state"] == "closed")
print(f"      왕복 {time.time() - t0:.1f}초 (가짜 서버, 폴링 0.2초 기준)")

fake_fail = lambda c: (_ for _ in ()).throw(RuntimeError("모델 오류"))
stop["v"] = True; th.join(timeout=5)
check("워커를 멈추면 하트비트가 꺼짐(OFFLINE)으로 바뀜", q.status(gh)["online"] is False)

no2 = q.submit(gh, CASE)
try:
    q.wait_result(gh, no2, timeout=1.5, every=0.3); check("워커 없이 기다리면 시간 초과", False, "예외가 안 남")
except q.QueueError as e:
    check("워커 없이 기다리면 시간 초과 + 작업을 닫음", "시간 초과" in str(e) and srv.issues[no2 - 1]["state"] == "closed", str(e))

stop2 = {"v": False}
th2 = threading.Thread(target=q.worker_loop, args=(gh, fake_fail, lambda: {}), kwargs={"poll": 0.2, "beat_every": 5, "should_stop": lambda: stop2["v"], "log": logs.append}, daemon=True)
th2.start(); no3 = q.submit(gh, CASE); r3 = q.wait_result(gh, no3, timeout=8, every=0.3); stop2["v"] = True; th2.join(timeout=5)
check("모델 오류가 나도 워커가 죽지 않고 오류 결과를 돌려줌", r3.get("ok") is False and "모델 오류" in r3.get("error", ""), str(r3))

# 하트비트가 오래되면 OFFLINE
hb = next(i for i in srv.issues if "heartbeat" in [x["name"] for x in i["labels"]])
hb["state"] = "open"; hb["body"] = '{"alive": true}'
hb["updated_at"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
s = q.status(gh, max_age=60)
check("하트비트가 5분 전이면 OFFLINE + 사유 표시", s["online"] is False and "마지막 응답" in s["reason"], str(s))

# 입력 정리
try:
    q.clean_case({"case_name": "x", "period": {"baseline": "a", "event": "b"}, "evidence": []}); check("증거가 없으면 거부", False)
except ValueError:
    check("증거가 없으면 거부", True)
big = {"case_name": "n" * 999, "period": {"baseline": "a", "event": "b"},
       "evidence": [{"id": "E1", "label": "L" * 999, "value": float("nan"), "unit": ""}] + [{"id": f"E{i}", "label": "x", "value": i, "unit": ""} for i in range(2, 10)]}
cc = q.clean_case(big)
check("긴 문자열은 잘리고 NaN은 문자열로 바뀜", len(cc["case_name"]) == q.MAX_TEXT and len(cc["evidence"][0]["label"]) == q.MAX_TEXT and isinstance(cc["evidence"][0]["value"], str))
try:
    q.clean_case({"case_name": "x", "period": {"baseline": "a", "event": "b"}, "evidence": [{"id": "E", "label": "l", "value": 1, "unit": ""}] * 31}); check("증거 31개는 거부", False)
except ValueError:
    check("증거 31개는 거부", True)
check("계약 키 밖의 값(debug 등)은 보내지 않음", set(q.clean_case({**CASE, "debug": {"roles": "secret"}, "main_metric": "x"})) == {"case_name", "period", "evidence"})

# 잘못된 토큰
bad = q.GH(srv.repo, "WRONG-TOKEN-VALUE", base=srv.base, timeout=3)
try:
    bad.call("GET", "/issues"); check("잘못된 토큰은 401", False)
except q.QueueError as e:
    check("잘못된 토큰은 401 오류이고 토큰 값이 메시지에 없음", "401" in str(e) and "WRONG-TOKEN-VALUE" not in str(e), str(e))
check("연결 실패도 OFFLINE으로 돌려줌 (예외 없음)", q.status(q.GH(srv.repo, srv.token, base="http://127.0.0.1:9", timeout=1))["online"] is False)

# 깨진 작업은 닫고 건너뜀
srv.issues.append({"number": len(srv.issues) + 1, "title": "junk", "body": "이건 JSON이 아님", "state": "open", "labels": [{"name": "job"}],
                   "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"})
check("깨진 작업은 닫고 건너뜀", q.next_job(gh) is None and srv.issues[-1]["state"] == "closed")

srv.close()
print("\n전체:", "통과" if not fails else f"실패 {fails}")
sys.exit(1 if fails else 0)
