"""make_tts.py - 음성 안내 mp3를 미리 만든다 (배포 앱은 이 파일만 재생, 실시간 생성 없음).
python tools/make_tts.py                 # 음성 3개를 새로 만들고 이어 붙인 파일도 만든다 (인터넷 필요)
python tools/make_tts.py --combine-only  # 이미 있는 파일로 이어 붙인 파일만 다시 만든다"""
import sys, asyncio, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import config

VOICE = "en-US-AriaNeural"
PHRASES = {"detecting": "Detecting", "found": "Anomaly Detected", "solved": "Root Cause Identified"}


async def generate():
    import edge_tts
    config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for kind, text in PHRASES.items():
        out = config.AUDIO_DIR / config.TTS_CLIPS[kind]
        await edge_tts.Communicate(text, VOICE).save(str(out))
        print(f"[만듦] {out.name}  '{text}'  {out.stat().st_size} bytes")


def combine():
    """detecting + found 를 한 파일로 이어 붙인다. 같은 인코더가 만든 CBR mp3라 바이트를 그대로 이어도 재생된다."""
    a, b = (config.AUDIO_DIR / config.TTS_CLIPS[k] for k in ("detecting", "found"))
    out = config.AUDIO_DIR / config.TTS_CLIPS["detect_found"]
    out.write_bytes(a.read_bytes() + b.read_bytes())
    print(f"[만듦] {out.name}  {out.stat().st_size} bytes (= {a.name} + {b.name})")


if __name__ == "__main__":
    if "--combine-only" not in sys.argv:
        asyncio.run(generate())
    combine()
