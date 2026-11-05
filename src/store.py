"""store.py - 완료된 검토 결과를 Supabase(PostgREST)에 저장하고 읽는다. 표준 라이브러리만 사용한다.
저장은 best-effort: 실패해도 앱은 계속 동작한다. 원본 CSV 행은 저장하지 않는다(요약된 증거·점수·AI 발언·보고서만)."""
import json, urllib.request, urllib.error, urllib.parse
import config

TABLE = "investigations"


class StoreError(Exception):
    pass


def enabled():
    return config.STORE_ENABLED


def _call(method, query="", body=None, timeout=6, prefer=None):
    """PostgREST 호출. 키는 예외 메시지와 로그에 넣지 않는다."""
    key = config.SUPABASE_SERVICE_KEY
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):                           # 예전 방식(JWT) 키만 Authorization 헤더도 함께 보낸다
        headers["Authorization"] = f"Bearer {key}"
    if prefer:
        headers["Prefer"] = prefer
    data = None if body is None else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(f"{config.SUPABASE_URL}/rest/v1/{TABLE}{query}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("message", "")[:100]
        except Exception:
            pass
        raise StoreError(f"{method} {TABLE} -> HTTP {e.code} {detail}".strip()) from None
    except Exception as e:
        raise StoreError(f"{method} {TABLE} -> {type(e).__name__}") from None


def build_row(session_id, dataset_label, shape, case, result, out):
    """검토 결과를 저장용 한 행으로 만든다. 원본 CSV 행은 들어가지 않는다."""
    top = result["hypotheses"][0]
    clip = lambda t, n=4000: None if t is None else str(t)[:n]
    return {"session_id": str(session_id), "dataset_label": dataset_label, "n_rows": int(shape[0]), "n_cols": int(shape[1]),
            "case_name": case.get("case_name"), "main_metric": case.get("main_metric"),
            "mode": "llm" if out.get("mode") == "llm" else "rule",
            "top_id": top["id"], "top_title": top["title"], "top_kind": top["kind"], "top_score": top["total"],
            "strength": result["conclusion"]["strength"],
            "hypotheses": [{"id": h["id"], "title": h["title"], "kind": h["kind"], "score": h["total"]} for h in result["hypotheses"]],
            "claims": [{"id": c.get("hypothesis_id"), "claim": clip(c.get("claim")), "weakness": clip(c.get("weakness")),
                        "ungrounded": c.get("ungrounded", [])} for c in out.get("claims", [])],
            "rebuttals": [{"id": r.get("hypothesis_id"), "point": clip(r.get("point")), "ungrounded": r.get("ungrounded", [])}
                          for r in out.get("rebuttals", [])],
            "report": clip(out.get("report"), 20000), "tokens": out.get("tokens"), "seconds": out.get("seconds"),
            "warnings": [str(w)[:300] for w in out.get("warnings", [])]}


def save(row):
    """한 행을 저장한다. 반환: (성공 여부 또는 None(꺼짐), 안내 문구). 어떤 오류도 예외로 던지지 않는다."""
    if not enabled():
        return None, "저장 연결이 꺼져 있어요"
    try:
        _call("POST", body=row, prefer="return=minimal")
        return True, "저장했어요"
    except StoreError as e:
        return False, str(e)


def recent(session_id, limit=10):
    """이 방문(session)에서 저장된 검토 목록. 실패하면 빈 목록."""
    if not enabled():
        return []
    q = (f"?session_id=eq.{urllib.parse.quote(str(session_id), safe='')}&order=created_at.desc&limit={int(limit)}"
         "&select=created_at,dataset_label,case_name,mode,top_title,top_score,tokens,seconds")
    try:
        return _call("GET", q) or []
    except StoreError:
        return []
