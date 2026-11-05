# results.zip 대신: 결과 JSON 전체를 텍스트로 출력 (이 출력을 통째로 복사해서 보내면 된다)
import glob
for p in sorted(glob.glob("results/qwen/*/*.json")):
    print(f"=== {p} ===")
    print(open(p, encoding="utf-8").read())
