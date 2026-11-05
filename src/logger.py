"""logger.py - 콘솔 로그 + 화면 디버그 패널용 로그 + 단계 시간 측정.
사용법:  from logger import get_logger, step, set_session, get_logs
"""
import logging, re, time, sys, contextvars
from collections import deque
from contextlib import contextmanager
import config

_session = contextvars.ContextVar("sid", default="-")   # 방문자 구분 (로그가 섞이지 않게)
_buffer = deque(maxlen=config.LOG_BUFFER_SIZE)
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|hf_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})")   # 키 모양 문자열


class MaskFilter(logging.Filter):
    """로그에 키가 섞여 들어가면 가린다."""
    def filter(self, record):
        record.msg = _SECRET.sub("***", str(record.getMessage()))
        record.args = ()
        return True


class BufferHandler(logging.Handler):
    """화면 디버그 패널에 보여줄 로그를 메모리에 쌓는다."""
    def emit(self, record):
        _buffer.append({"sid": _session.get(), "time": time.strftime("%H:%M:%S"),
                        "level": record.levelname, "name": record.name,
                        "msg": record.getMessage()})


def set_session(sid):
    """app.py에서 실행 시작할 때마다 호출. 이후 로그에 이 방문자 표시가 붙는다."""
    _session.set(str(sid))


def get_logs(sid=None, min_level="DEBUG"):
    """디버그 패널용: 내 세션의 로그만 돌려준다."""
    sid = sid or _session.get()
    order = ["DEBUG", "INFO", "WARNING", "ERROR"]
    ok = order[order.index(min_level):] if min_level in order else order
    return [r for r in _buffer if r["sid"] == sid and r["level"] in ok]


def clear_logs(sid=None):
    sid = sid or _session.get()
    keep = [r for r in _buffer if r["sid"] != sid]
    _buffer.clear(); _buffer.extend(keep)


def _setup():
    root = logging.getLogger("caselens")
    if root.handlers:                       # Streamlit 재실행 때 중복 방지
        return root
    root.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
    root.propagate = False
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
    for h in (console, BufferHandler()):
        h.addFilter(MaskFilter())
        root.addHandler(h)
    # export_case.py의 로그도 디버그 패널에 보이게 연결 (콘솔은 자체 출력 사용)
    ec = logging.getLogger("export_case")
    if not any(isinstance(h, BufferHandler) for h in ec.handlers):
        b = BufferHandler(); b.addFilter(MaskFilter()); ec.addHandler(b)
    root.info(f"설정: {config.summary()}")
    return root


_root = _setup()


def get_logger(name):
    """파일마다 get_logger(__name__)로 사용"""
    return logging.getLogger(f"caselens.{name}")


@contextmanager
def step(log, title):
    """단계 시작/끝과 걸린 시간을 기록. 실패하면 오류도 남기고 다시 던진다.
    with step(log, "가설 검증"): ...
    """
    t0 = time.time()
    log.info(f"▶ {title} 시작")
    try:
        yield
        log.info(f"✔ {title} 완료 ({time.time()-t0:.2f}초)")
    except Exception as e:
        log.error(f"✖ {title} 실패 ({time.time()-t0:.2f}초): {type(e).__name__}: {e}")
        raise
