"""make_tilon_cells.py - gpu_inputs/*.json을 틸론 노트북에 붙여넣을 '입력 생성 셀' 코드로 만든다.
사용: python tools/make_tilon_cells.py  -> tools/cell_inputs.py (내용을 노트북 셀에 복사해 실행)"""
import json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
cases = {p.stem.replace("case_input_", ""): json.loads(p.read_text(encoding="utf-8"))
         for p in sorted((ROOT / "gpu_inputs").glob("case_input_*.json"))}
code = ("# 입력 JSON을 이 노트북 폴더에 만든다 (업로드 대신 붙여넣기)\nimport json, os\nCASE_FILES = " +
        "{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in cases.items()) + "\n}" +
        "\nfor n, c in CASE_FILES.items():\n    json.dump(c, open(f'case_input_{n}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)\n"
        "print('[입력 생성]', sorted(f for f in os.listdir('.') if f.startswith('case_input')), '| 폴더:', os.getcwd())\n")
(ROOT / "tools" / "cell_inputs.py").write_text(code, encoding="utf-8")
print(code)
