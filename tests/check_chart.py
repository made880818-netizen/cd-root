"""check_chart.py - 이상 탐지 그래프가 실제 시계열 값으로 그려지는지 검증한다.
그래프 이미지(SVG) 안의 좌표를 꺼내 원본 값과 대조한다.  python tests/check_chart.py"""
import sys, re, base64, logging, pathlib
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import export_case as ec, agents, ui
ec.log.setLevel(logging.CRITICAL); logging.getLogger("caselens").setLevel(logging.CRITICAL)

df = ec.read_csv_any(str(ROOT / "data" / "orders.csv"))
fails, seen = 0, []
for metric in (None, "cancel_qty", "revenue"):
    res = agents.analyze(df, ec.build_case_from_df(df, name="o", metric=metric))
    ctx = res["ctx"]
    y = ctx["series"][ctx["main"]].astype(float).values
    data = {"kind": "time", "y": y.tolist(), "b0": int(ctx["b0"]), "e0": int(ctx["e0"]), "ee": int(ctx["ee"]),
            "on": ctx.get("main_on"), "x0": "a", "x1": "b"}
    html = ui.chart(data, "t")
    svg = base64.b64decode(re.search(r"base64,([A-Za-z0-9+/=]+)", html).group(1)).decode()
    d = re.search(r' d="(M[^"]+)"', svg).group(1)
    pts = np.array([[float(a) for a in p.split(",")] for p in d.replace("M", "").split(" L")])
    r = np.corrcoef(pts[:, 1], y)[0, 1]
    ok = len(pts) == len(y) and r < -0.999 and ui.fmt_num(y.max()) in svg
    seen.append(d)
    fails += not ok
    print(f"[{'OK' if ok else 'FAIL'}] {ctx['main']:15s} 시리즈 {len(y)}칸 | 그려진 점 {len(pts)}개 | 픽셀y-원본값 상관 {r:+.4f} | 최대값 라벨 {ui.fmt_num(y.max())}")
distinct = len(set(seen)) == len(seen)
print(f"[{'OK' if distinct else 'FAIL'}] 지표를 바꾸면 선이 달라짐: {distinct}")
sys.exit(1 if fails or not distinct else 0)
