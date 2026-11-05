"""unpack_results.py - 틸론 셀이 출력한 텍스트('=== 경로 ===' 구분)를 results/qwen/ 파일로 풀어 저장한다.
사용: python tools/unpack_results.py 붙여넣은텍스트.txt"""
import sys, re, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
parts = re.split(r"^=== (results[/\\]qwen[/\\]\S+\.json) ===\s*$", text, flags=re.M)
n = 0
for path, body in zip(parts[1::2], parts[2::2]):
    path = path.replace("\\", "/")
    try:
        data = json.loads(body.strip())
    except Exception as e:
        print(f"[건너뜀] {path}: JSON 오류 {e}"); continue
    dest = ROOT / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n += 1; print("[저장]", path)
print(f"총 {n}개 저장 (기대: 사례 수 x 3)")
