"""config.py - 설정을 한곳에 모은다. 다른 파일은 여기서만 값을 가져간다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 프로젝트 최상위 폴더
DATA_DIR = ROOT / "data"                           # 시연용 CSV
RESULTS_DIR = ROOT / "results" / "qwen"            # 틸론에서 받은 결과 (웹은 읽기만)
CACHE_DIR = ROOT / "cache"                         # API 실패 대비 저장 결과
AUDIO_DIR = ROOT / "assets" / "audio"              # 미리 만든 음성 파일


def get_secret(name, default=""):
    """키는 환경변수 -> Streamlit secrets 순서로 찾는다. 코드에는 절대 직접 쓰지 않는다."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, default))
    except Exception:                               # secrets 파일이 없으면 그냥 기본값
        return default


# --- Claude (토론, 검증, 결론) ---
ANTHROPIC_API_KEY = get_secret("ANTHROPIC_API_KEY")
ANTHROPIC_BASE_URL = get_secret("ANTHROPIC_BASE_URL", "")    # 비워두면 공식 주소 사용
CLAUDE_MODEL = get_secret("CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_MAX_TOKENS = 1500
CLAUDE_TIMEOUT_SEC = 40

# --- 에이전트 토론 ---
MAX_TOOL_CALLS_PER_AGENT = 6        # 에이전트 한 명이 증거를 요청할 수 있는 최대 횟수
MAX_UPLOAD_MB = 20                  # 업로드 CSV 크기 제한
MAX_UPLOAD_ROWS = 200_000           # 이보다 많으면 앞부분만 사용

# --- 동작 모드 ---
# 키가 없거나 호출이 실패하면 LLM 없이 규칙 기반으로 자동 전환한다.
LLM_ENABLED = bool(ANTHROPIC_API_KEY) and get_secret("DISABLE_LLM", "0") != "1"

# --- AI 검토 횟수 제한 (대회용 공유 API 예산 보호). 규칙 기반 분석과 저장된 결과 보기는 제한 없음 ---
AI_RUNS_PER_SESSION = int(get_secret("AI_RUNS_PER_SESSION", "3"))     # 방문자 한 명이 AI 검토를 실행할 수 있는 횟수
AI_RUNS_PER_HOUR = int(get_secret("AI_RUNS_PER_HOUR", "30"))          # 서버 전체에서 한 시간에 허용하는 AI 검토 횟수

# --- Supabase 저장 (완료된 검토 결과 요약을 DB에 남긴다). 설정이 없으면 저장하지 않고 앱은 그대로 동작 ---
SUPABASE_URL = get_secret("SUPABASE_URL", "").rstrip("/")             # 예: https://abcdxxxx.supabase.co
SUPABASE_SERVICE_KEY = get_secret("SUPABASE_SERVICE_KEY", "")         # service_role 키: 서버(Secrets)에만 둔다. 브라우저·GitHub에 노출 금지
STORE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY) and get_secret("DISABLE_STORE", "0") != "1"

# --- 틸론 GPU 실시간 연동 (GitHub Issues 작업 큐). 설정이 없으면 저장된 결과만 보여준다 ---
GPU_QUEUE_REPO = get_secret("GPU_QUEUE_REPO", "")                     # 예: 계정/저장소이름 (비공개 저장소)
GPU_QUEUE_TOKEN = get_secret("GPU_QUEUE_TOKEN", "")                   # 그 저장소 Issues 읽기/쓰기 권한만 가진 토큰
GPU_QUEUE_API = get_secret("GPU_QUEUE_API", "https://api.github.com")
GPU_QUEUE_ENABLED = bool(GPU_QUEUE_REPO and GPU_QUEUE_TOKEN) and get_secret("DISABLE_GPU_LINK", "0") != "1"   # DISABLE_GPU_LINK=1이면 저장된 결과만
GPU_JOB_TIMEOUT_SEC = 45            # 결과를 기다리는 최대 시간
GPU_ONLINE_MAX_AGE_SEC = 60         # 하트비트가 이보다 오래되면 OFFLINE
GPU_JOBS_PER_SESSION = 6            # 방문자 한 명이 올릴 수 있는 작업 수 (GPU 남용 방지)

# --- 음성 (TTS) ---
TTS_ENABLED = get_secret("DISABLE_TTS", "0") != "1"
TTS_CLIPS = {                       # 상황 -> 미리 만든 파일 이름
    "detecting": "detecting.mp3",
    "found": "case_found.mp3",
    "solved": "case_solved.mp3",
    "detect_found": "detect_found.mp3",   # detecting + found를 이어 붙인 파일 (연달아 나올 때 서버가 기다리지 않도록)
}

# --- 로그 ---
LOG_LEVEL = get_secret("LOG_LEVEL", "INFO")
LOG_BUFFER_SIZE = 500               # 화면 디버그 패널에 보관할 최대 줄 수


def summary():
    """시작할 때 로그에 찍을 설정 요약. 키 값은 보여주지 않고 있음/없음만 표시."""
    return {
        "claude_key": "있음" if ANTHROPIC_API_KEY else "없음",
        "base_url": "있음" if ANTHROPIC_BASE_URL else "기본값",
        "claude_model": CLAUDE_MODEL,
        "llm_enabled": LLM_ENABLED,
        "gpu_live_link": "설정됨" if GPU_QUEUE_ENABLED else "꺼짐(저장된 결과만)",
        "supabase_store": "설정됨" if STORE_ENABLED else "꺼짐(저장 안 함)",
        "tts_enabled": TTS_ENABLED,
        "results_dir_exists": RESULTS_DIR.exists(),
        "cache_dir_exists": CACHE_DIR.exists(),
    }

