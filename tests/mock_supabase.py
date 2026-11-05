"""mock_supabase.py - 시험용 가짜 Supabase(PostgREST). investigations 테이블의 저장/조회만 흉내낸다."""
import json, threading, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()


class MockSupabase:
    def __init__(self, key="test-service-key", table_exists=True):
        self.key, self.table_exists, self.rows, self.requests, self.last_headers = key, table_exists, [], 0, {}
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                raw = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)

            def _handle(self, method):
                u = urlparse(self.path); q = parse_qs(u.query)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                with outer.lock:
                    outer.requests += 1
                    outer.last_headers = dict(self.headers)
                    if self.headers.get("apikey") != outer.key:
                        return self._send(401, {"message": "Invalid API key"})
                    if u.path != "/rest/v1/investigations":
                        return self._send(404, {"message": "not found"})
                    if not outer.table_exists:
                        return self._send(404, {"message": 'relation "public.investigations" does not exist'})
                    if method == "POST":
                        if not body or not body.get("session_id") or not body.get("dataset_label") or body.get("mode") not in ("llm", "rule"):
                            return self._send(400, {"message": 'new row violates check constraint "investigations_mode_check"'})
                        outer.rows.append({**body, "id": f"id-{len(outer.rows) + 1}", "created_at": now()})
                        return self._send(201)
                    if method == "GET":
                        sid = q.get("session_id", [""])[0].removeprefix("eq.")
                        rows = [r for r in outer.rows if not sid or r["session_id"] == sid][::-1][: int(q.get("limit", ["100"])[0])]
                        cols = q.get("select", [""])[0].split(",") if q.get("select") else None
                        return self._send(200, [{c: r.get(c) for c in cols} if cols else r for r in rows])
                    return self._send(405, {"message": "?"})

            do_GET = lambda self: self._handle("GET")
            do_POST = lambda self: self._handle("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()
