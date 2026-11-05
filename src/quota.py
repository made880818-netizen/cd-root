"""quota.py - 공유 API 예산을 지키기 위한 AI 검토 횟수 제한 (서버 전체의 시간당 상한). 방문자당 상한은 app.py의 세션 값으로 센다."""
import threading, time
from collections import deque


class HourlyCap:
    """최근 window초 안에 허용된 횟수가 limit에 닿으면 막는다. 여러 방문자가 동시에 눌러도 안전하다."""
    def __init__(self, limit, window=3600):
        self.limit, self.window, self._t, self._lock = limit, window, deque(), threading.Lock()

    def _prune(self, now):
        while self._t and now - self._t[0] >= self.window:
            self._t.popleft()

    def used(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            return len(self._t)

    def try_acquire(self, now=None):
        """자리가 있으면 사용 기록을 남기고 True, 없으면 False"""
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            if len(self._t) >= self.limit:
                return False
            self._t.append(now)
            return True
