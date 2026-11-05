"""gpu_queue.py - 틸론 GPU 작업 큐. GitHub 비공개 저장소의 Issues를 우편함으로 쓴다 (표준 라이브러리만 사용).
웹: 작업(issue, 라벨 job)을 올리고 결과(댓글)를 기다린다.  틸론 노트북: 밖으로만 접속해 작업을 가져가 처리하고 결과를 올린다.
하트비트: 라벨 heartbeat 이슈의 본문을 틸론이 주기적으로 갱신 -> 웹이 ONLINE/OFFLINE을 판단한다. 이 파일은 틸론에도 그대로 둔다."""
import json, math, time, uuid, urllib.request, urllib.error
from datetime import datetime, timezone

API = "https://api.github.com"
MARK = "CDROOT-RESULT"                 # 결과 댓글의 머리말
MAX_EVIDENCE, MAX_TEXT = 30, 200       # 틸론으로 보낼 증거 개수와 문자열 길이 상한


class QueueError(Exception):
    pass


class GH:
    """GitHub REST 호출 최소 구현. 토큰은 예외 메시지와 로그에 넣지 않는다."""
    def __init__(self, repo, token, base=API, timeout=10):
        self.repo, self.token, self.base, self.timeout = repo, token, base.rstrip("/"), timeout

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/repos/{self.repo}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cd-root-gpu-queue", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise QueueError(f"{method} {path.split('?')[0]} -> HTTP {e.code}") from None
        except Exception as e:
            raise QueueError(f"{method} {path.split('?')[0]} -> {type(e).__name__}") from None


# ---------- 웹 쪽 ----------
def clean_case(case):
    """틸론으로 보낼 사건 JSON을 계약 키(case_name, period, evidence)만 남기고 크기를 제한한다. 형식이 틀리면 ValueError."""
    ev = case.get("evidence") or []
    if not 1 <= len(ev) <= MAX_EVIDENCE:
        raise ValueError(f"증거는 1~{MAX_EVIDENCE}개여야 합니다")
    cut = lambda x: str(x)[:MAX_TEXT]
    val = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else cut(v)
    return {"case_name": cut(case["case_name"]),
            "period": {"baseline": cut(case["period"]["baseline"]), "event": cut(case["period"]["event"])},
            "evidence": [{"id": cut(e["id"]), "label": cut(e["label"]), "value": val(e["value"]), "unit": cut(e.get("unit", ""))} for e in ev]}


def submit(gh, case):
    """작업을 올리고 이슈 번호를 돌려준다."""
    payload = {"id": uuid.uuid4().hex[:8], "case": clean_case(case)}
    issue = gh.call("POST", "/issues", {"title": f"job {payload['id']}", "body": json.dumps(payload, ensure_ascii=False), "labels": ["job"]})
    return issue["number"]


def wait_result(gh, number, timeout=45, every=2):
    """결과 댓글이 달릴 때까지 기다린다. 시간이 지나면 작업을 닫고 QueueError."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for c in gh.call("GET", f"/issues/{number}/comments?per_page=10") or []:
            if c["body"].startswith(MARK):
                return json.loads(c["body"][len(MARK):].strip())
        time.sleep(every)
    try:
        gh.call("PATCH", f"/issues/{number}", {"state": "closed"})      # 뒤늦게 틸론이 집어 가지 않도록
    except QueueError:
        pass
    raise QueueError("시간 초과: 틸론 워커가 응답하지 않습니다")


def status(gh, max_age=60):
    """틸론 워커가 살아 있는지. 마지막 갱신 시각은 GitHub 서버 시각(updated_at)을 쓴다. 어떤 오류도 예외 대신 offline으로 돌려준다."""
    try:
        for i in gh.call("GET", "/issues?state=open&labels=heartbeat&per_page=1") or []:
            info = json.loads(i.get("body") or "{}")
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(i["updated_at"].replace("Z", "+00:00"))).total_seconds()
            alive = bool(info.get("alive", True)) and age <= max_age
            return {"online": alive, "age": round(age), "info": info, "reason": "" if alive else f"마지막 응답 {round(age)}초 전"}
        return {"online": False, "age": None, "info": {}, "reason": "워커 기록 없음"}
    except Exception as e:
        return {"online": False, "age": None, "info": {}, "reason": str(e)[:80]}


# ---------- 틸론(워커) 쪽 ----------
def beat(gh, info, number=None):
    """하트비트 갱신 (이슈가 없으면 만든다). 이슈 번호를 돌려준다."""
    body = json.dumps(info, ensure_ascii=False)
    if number is None:
        found = gh.call("GET", "/issues?state=open&labels=heartbeat&per_page=1") or []
        if not found:
            return gh.call("POST", "/issues", {"title": "heartbeat", "body": body, "labels": ["heartbeat"]})["number"]
        number = found[0]["number"]
    gh.call("PATCH", f"/issues/{number}", {"body": body})
    return number


def next_job(gh):
    """가장 오래된 열린 작업 하나. 형식이 깨진 작업은 닫고 건너뛴다. 없으면 None."""
    for i in gh.call("GET", "/issues?state=open&labels=job&sort=created&direction=asc&per_page=1") or []:
        try:
            return i["number"], json.loads(i["body"])
        except Exception:
            gh.call("PATCH", f"/issues/{i['number']}", {"state": "closed"})
    return None


def post_result(gh, number, result):
    gh.call("POST", f"/issues/{number}/comments", {"body": MARK + "\n" + json.dumps(result, ensure_ascii=False)})
    gh.call("PATCH", f"/issues/{number}", {"state": "closed"})


def worker_loop(gh, run_case, info, poll=3, beat_every=20, should_stop=lambda: False, log=print):
    """작업을 가져와 run_case(case)로 처리하고 결과를 올린다. 멈추면(중단/오류 종료) 하트비트를 꺼짐으로 바꾼다."""
    hb, last = None, 0
    try:
        while not should_stop():
            try:
                if time.time() - last >= beat_every:
                    hb, last = beat(gh, {**info(), "alive": True}, hb), time.time()
                job = next_job(gh)
                if not job:
                    time.sleep(poll)
                    continue
                number, payload = job
                t0 = time.time()
                try:
                    res = {"ok": True, **run_case(payload["case"])}
                except Exception as e:
                    res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
                res["seconds"] = round(time.time() - t0, 1)
                post_result(gh, number, res)
                log(f"[작업 #{number}] {'완료' if res['ok'] else '실패'} {res['seconds']}초")
            except QueueError as e:
                log(f"[큐 오류] {e}")
                time.sleep(5)
    finally:
        try:
            beat(gh, {**info(), "alive": False}, hb)
        except Exception:
            pass
