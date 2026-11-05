"""mock_github.py - 시험용 가짜 GitHub Issues API (실제 GitHub 없이 gpu_queue의 HTTP 경로를 검증한다)."""
import json, re, threading, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

now = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MockGitHub:
    def __init__(self, repo="me/queue", token="test-token"):
        self.repo, self.token, self.issues, self.comments, self.requests = repo, token, [], {}, 0
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
                body = json.loads(self.rfile.read(n)) if n else {}
                with outer.lock:
                    outer.requests += 1
                    if self.headers.get("Authorization") != f"Bearer {outer.token}":
                        return self._send(401, {"message": "Bad credentials"})
                    m = re.fullmatch(rf"/repos/{re.escape(outer.repo)}/issues(?:/(\d+))?(/comments)?", u.path)
                    if not m:
                        return self._send(404, {"message": "Not Found"})
                    num, com = (int(m.group(1)) if m.group(1) else None), m.group(2)
                    if com and num:
                        if method == "GET":
                            return self._send(200, outer.comments.get(num, []))
                        c = {"id": len(outer.comments.get(num, [])) + 1, "body": body["body"]}
                        outer.comments.setdefault(num, []).append(c)
                        return self._send(201, c)
                    if num is None and method == "POST":
                        i = {"number": len(outer.issues) + 1, "title": body["title"], "body": body.get("body", ""), "state": "open",
                             "labels": [{"name": x} for x in body.get("labels", [])], "created_at": now(), "updated_at": now()}
                        outer.issues.append(i)
                        return self._send(201, i)
                    if num is None and method == "GET":
                        st, lb = q.get("state", ["open"])[0], q.get("labels", [""])[0]
                        res = [i for i in outer.issues if (st == "all" or i["state"] == st) and (not lb or lb in [x["name"] for x in i["labels"]])]
                        if q.get("direction", ["desc"])[0] == "desc":
                            res = res[::-1]
                        return self._send(200, res[: int(q.get("per_page", ["30"])[0])])
                    if num and method == "PATCH":
                        i = outer.issues[num - 1]
                        i.update({k: v for k, v in body.items() if k in ("state", "body", "title")}); i["updated_at"] = now()
                        return self._send(200, i)
                    return self._send(405, {"message": "?"})

            do_GET = lambda self: self._handle("GET")
            do_POST = lambda self: self._handle("POST")
            do_PATCH = lambda self: self._handle("PATCH")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()
