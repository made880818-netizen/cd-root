"""ui.py - CD.ROOT 화면 조각(CSS + HTML). 계산은 하지 않고 받은 값을 그리기만 한다.
사용자 데이터(컬럼명, 파일명, LLM 문장)는 모두 e()로 escape해서 넣는다."""
import html, math, base64

e = lambda x: html.escape(str(x), quote=True)

KIND_COLOR = {"covariate": "#00D9FF", "origin": "#A78BFA", "group": "#F472B6",
              "data": "#FF4D6D", "direct": "#2DD4BF", "confound": "#FB923C"}
KIND_LABEL = {"covariate": "COVARIATE", "origin": "ORIGIN", "group": "GROUP",
              "data": "DATA QUALITY", "direct": "DIRECT", "confound": "CONFOUND"}
# 상태 -> (진행 단계 0~6, 색, 한글 안내)
STATES = {
    "STANDBY": (0, "#6B7C8C", "데이터를 올리면 조사를 시작합니다"),
    "SCANNING": (1, "#00D9FF", "데이터에서 사건을 찾는 중"),
    "NO ANOMALY": (1, "#3DDC97", "뚜렷한 사건을 찾지 못했습니다"),
    "ANOMALY DETECTED": (2, "#FFB547", "사건 구간을 찾았습니다"),
    "INVESTIGATING": (3, "#00D9FF", "에이전트들이 증거를 모으는 중"),
    "HYPOTHESIS ANALYSIS": (4, "#A78BFA", "가설별 주장이 도착했습니다"),
    "EVIDENCE VALIDATION": (5, "#00D9FF", "반박과 증거 장부 대조 중"),
    "CONCLUSION": (6, "#3DDC97", "조사가 끝났습니다 (후보 제시)"),
}
STEPS = ["STANDBY", "SCAN", "DETECT", "INVESTIGATE", "HYPOTHESIS", "VALIDATE", "CONCLUDE"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Noto+Sans+KR:wght@400;500;700&display=swap');
:root{--bg:#080B10;--panel:#0D131B;--panel2:#111A25;--line:rgba(120,160,190,.18);--line2:rgba(120,160,190,.34);
--text:#E6EDF3;--dim:#A6B6C6;--mute:#6B7C8C;--cy:#00D9FF;--pu:#A78BFA;--warn:#FFB547;--crit:#FF4D6D;--ok:#3DDC97;
--mono:'JetBrains Mono','IBM Plex Mono',Consolas,monospace;--sans:'Inter','Noto Sans KR','Malgun Gothic',system-ui,sans-serif}
.stApp{background:radial-gradient(1200px 600px at 50% -10%,#0f1a28 0%,#080B10 60%);color:var(--text);font-family:var(--sans)}
header[data-testid="stHeader"]{background:transparent}
[data-testid="stToolbar"],[data-testid="stDecoration"],footer,#MainMenu{display:none!important}
.block-container{padding:.4rem 1.1rem 2rem!important;max-width:100%!important}
section[data-testid="stSidebar"]{background:var(--panel);border-right:1px solid var(--line)}
section[data-testid="stSidebar"]{font-family:var(--sans)}
[data-testid="stSidebarCollapseButton"],[data-testid="stSidebarCollapsedControl"]{display:none!important}
section[data-testid="stSidebar"][data-collapsed="true"]{transform:none!important;width:300px!important;min-width:300px!important;visibility:visible!important}
[data-testid="stAudio"]{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;pointer-events:none}
.st-key-feed{gap:.3rem!important}
.st-key-feed .stButton>button>div{justify-content:flex-start!important;width:100%}
.st-key-feed .stButton>button p{text-align:left!important;font-size:.74rem}
.st-key-feed .stButton>button{padding:.38rem .7rem;min-height:0}
h1,h2,h3,h4{font-family:var(--sans);letter-spacing:-.01em}
.stMarkdown p,.stMarkdown li{color:var(--text)}
[data-testid="stCaptionContainer"]{color:var(--dim)}
/* 탭 = 상단 내비게이션 */
.stTabs [data-baseweb="tab-list"]{gap:0;border-bottom:1px solid var(--line);background:rgba(13,19,27,.6)}
.stTabs button[data-baseweb="tab"]{font-family:var(--mono);font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;
color:var(--dim);padding:.7rem 1.1rem;height:auto}
.stTabs button[data-baseweb="tab"][aria-selected="true"]{color:var(--cy)}
.stTabs [data-baseweb="tab-highlight"]{background:var(--cy);height:2px}
.stTabs [data-baseweb="tab-border"]{display:none}
/* 버튼 */
.stButton>button,.stDownloadButton>button{border-radius:2px;border:1px solid var(--line2);background:var(--panel2);color:var(--text);
font-family:var(--mono);font-size:.74rem;letter-spacing:.04em;transition:all .15s}
.stButton>button:hover{border-color:var(--cy);color:var(--cy);background:rgba(0,217,255,.06)}
.stButton>button[kind="primary"],.stButton>button[data-testid="stBaseButton-primary"]{background:rgba(0,217,255,.14);border-color:var(--cy);color:var(--cy)}
.stButton>button:disabled{opacity:.4}
.st-key-feed .stButton>button{justify-content:flex-start;text-align:left;width:100%;padding:.5rem .7rem;border-left-width:3px}
.st-key-feed .stButton>button p{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
[class*="st-key-sev_crit"] button{border-left-color:var(--crit)!important}
[class*="st-key-sev_warn"] button{border-left-color:var(--warn)!important}
[class*="st-key-sev_info"] button{border-left-color:var(--mute)!important}
[class*="st-key-sev_auto"] button{border-left-color:var(--pu)!important}
[data-testid="stExpander"]{border:1px solid var(--line);border-radius:2px;background:var(--panel)}
[data-testid="stChatMessage"]{background:var(--panel);border:1px solid var(--line);border-radius:2px;animation:fadein .4s ease both}
[data-testid="stDataFrame"]{border:1px solid var(--line)}
[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);padding:.5rem .7rem}
[data-testid="stMetricLabel"]{font-family:var(--mono);font-size:.7rem;color:var(--dim)}
/* 패널 */
.pn{background:var(--panel);border:1px solid var(--line);border-radius:2px;margin-bottom:.55rem;animation:fadein .5s ease both}
.pn-h{display:flex;justify-content:space-between;align-items:center;padding:.38rem .7rem;border-bottom:1px solid var(--line);background:rgba(17,26,37,.7)}
.pn-t{font-family:var(--mono);font-size:.68rem;letter-spacing:.14em;color:var(--dim);text-transform:uppercase;display:flex;align-items:center;gap:.5rem;white-space:nowrap}
.pn-t i{width:6px;height:6px;border-radius:50%;display:inline-block;flex:none}
.pn-g{font-family:var(--mono);font-size:.62rem;color:var(--mute);letter-spacing:.08em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:45%}
.pn-b{padding:.6rem .7rem}
.kv{display:flex;justify-content:space-between;gap:.8rem;padding:.22rem 0;border-bottom:1px dashed rgba(120,160,190,.1);font-size:.8rem}
.kv:last-child{border-bottom:none}
.kv .k{font-family:var(--mono);font-size:.68rem;color:var(--mute);letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}
.kv .v{color:var(--text);text-align:right;word-break:break-word}
.mono{font-family:var(--mono)}
.chip{display:inline-block;font-family:var(--mono);font-size:.62rem;letter-spacing:.08em;padding:.08rem .38rem;border:1px solid;border-radius:2px;margin:.05rem .15rem .05rem 0}
.bar{height:5px;background:rgba(120,160,190,.14);border-radius:1px;overflow:hidden}
.bar b{display:block;height:100%}
.sec{font-family:var(--mono);font-size:.62rem;letter-spacing:.16em;color:var(--mute);margin:.7rem 0 .3rem;text-transform:uppercase}
.sec:first-child{margin-top:0}
.txt{font-size:.82rem;line-height:1.55;color:var(--text)}
.dim{color:var(--dim)}
/* 상단 바 */
.tb{display:flex;justify-content:space-between;align-items:center;padding:.5rem .3rem .55rem}
.tb-l{display:flex;align-items:baseline;gap:.8rem}
.logo{font-family:var(--mono);font-weight:700;letter-spacing:.2em;color:var(--cy);font-size:1.05rem}
.logo span{color:var(--text)}
.tb-s{font-family:var(--mono);font-size:.62rem;letter-spacing:.2em;color:var(--mute)}
.tb-r{display:flex;gap:.45rem;flex-wrap:wrap;justify-content:flex-end}
.st{font-family:var(--mono);font-size:.62rem;letter-spacing:.1em;color:var(--dim);border:1px solid var(--line);padding:.18rem .5rem;border-radius:2px;display:inline-flex;align-items:center;gap:.4rem;background:var(--panel)}
.dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.dot.on{animation:blink 2.2s ease-in-out infinite}
/* AI Core */
.core{display:flex;flex-direction:column;align-items:center;padding:.2rem 0 .4rem}
.core svg{width:100%;max-width:400px;height:auto}
.spin{transform-origin:180px 180px;animation:spin 60s linear infinite}
.spin2{transform-origin:180px 180px;animation:spin 90s linear infinite reverse}
.halo{transform-origin:180px 180px;animation:pulse 3.2s ease-in-out infinite}
.sig{animation:blink 1.6s ease-in-out infinite}
.stepper{display:flex;gap:3px;width:100%;margin-top:.2rem}
.stepper div{flex:1;text-align:center;font-family:var(--mono);font-size:.54rem;letter-spacing:.06em;color:var(--mute);padding-top:.3rem;border-top:2px solid rgba(120,160,190,.18)}
.stepper div.done{border-top-color:var(--cy);color:var(--dim)}
.stepper div.now{color:var(--text);box-shadow:0 -6px 12px -8px currentColor}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:.4rem;width:100%;margin:.5rem 0 .2rem}
.tile{border:1px solid var(--line);background:var(--panel2);padding:.4rem .55rem}
.tile .k{font-family:var(--mono);font-size:.58rem;letter-spacing:.12em;color:var(--mute)}
.tile .v{font-family:var(--mono);font-size:.95rem;font-weight:500;margin-top:.1rem;word-break:break-all}
/* 타임라인 */
.tl{position:relative;padding-left:1rem;max-height:300px;overflow:auto}
.tl:before{content:"";position:absolute;left:3px;top:4px;bottom:4px;width:1px;background:var(--line2)}
.tl-i{position:relative;padding:.18rem 0 .5rem;animation:fadein .4s ease both}
.tl-i:before{content:"";position:absolute;left:-1.07rem;top:.42rem;width:7px;height:7px;border-radius:50%;background:var(--c,var(--cy));box-shadow:0 0 8px var(--c,var(--cy))}
.tl-t{font-family:var(--mono);font-size:.62rem;color:var(--mute)}
.tl-m{font-size:.8rem;line-height:1.4}
/* 표 */
.tbl{width:100%;border-collapse:collapse;font-size:.78rem}
.tbl th{font-family:var(--mono);font-size:.6rem;letter-spacing:.12em;color:var(--mute);text-align:left;padding:.25rem .4rem;border-bottom:1px solid var(--line2);font-weight:500}
.tbl td{padding:.35rem .4rem;border-bottom:1px solid rgba(120,160,190,.1);vertical-align:top}
.cmp-c{display:grid;grid-template-columns:1fr 1fr;gap:.1rem .8rem;margin-top:.3rem}
.cmp-c div{font-size:.68rem;color:var(--dim)}
.risk{display:flex;gap:.5rem;align-items:flex-start;padding:.3rem .5rem;border-left:3px solid var(--c);background:rgba(120,160,190,.05);margin-bottom:.3rem;font-size:.78rem;line-height:1.4}
.risk b{font-family:var(--mono);font-size:.6rem;letter-spacing:.1em;color:var(--c);white-space:nowrap;padding-top:.15rem}
.led{font-family:var(--mono);font-size:.7rem;padding:.2rem 0;border-bottom:1px dashed rgba(120,160,190,.1);color:var(--dim)}
.led b{color:var(--cy);font-weight:500;margin-right:.4rem}
.agent{display:grid;grid-template-columns:auto 1fr auto;gap:.8rem;align-items:center;padding:.5rem .1rem;border-bottom:1px solid rgba(120,160,190,.1)}
.hero{padding:2.2rem 1.4rem;text-align:center}
.hero h1{font-family:var(--mono);letter-spacing:.18em;font-size:1.6rem;margin:.2rem 0;color:var(--text)}
.hero h1 span{color:var(--cy)}
.hero p{color:var(--dim);max-width:640px;margin:.4rem auto}
.hero .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem;margin:1.4rem auto 0;max-width:900px;text-align:left}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:.35;transform:scale(.97)}50%{opacity:.8;transform:scale(1.03)}}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
/* ---- 가독성 개선: .u 패널에만 적용 (AI CORE 패널은 .u가 없어 그대로) ---- */
:root{--mute2:#8FA3B5}
.pn.u .pn-t{font-size:.76rem;color:var(--text)}
.pn.u .pn-g{font-size:.66rem;color:var(--mute2)}
.pn.u .kv{font-size:.88rem;padding:.3rem 0}
.pn.u .kv .k{font-size:.72rem;color:var(--mute2)}
.pn.u .sec{font-size:.68rem;color:var(--mute2)}
.pn.u .txt{font-size:.9rem}
.pn.u .chip{font-size:.68rem}
.pn.u .bar{height:6px}
.pn.u .tbl{font-size:.86rem}
.pn.u .tbl th{font-size:.66rem;color:var(--mute2)}
.pn.u .cmp-c div{font-size:.76rem}
.pn.u .risk{font-size:.88rem}
.pn.u .risk b{font-size:.66rem}
.pn.u .led{font-size:.78rem}
.pn.u .tl-t{font-size:.7rem;color:var(--mute2)}
.pn.u .tl-m{font-size:.9rem}
/* 요약 띠 */
.hl{display:grid;grid-template-columns:1.15fr .9fr 2fr 1.25fr;gap:0;background:var(--panel);border:1px solid var(--line);border-top:2px solid var(--c);margin-bottom:.55rem;animation:fadein .5s ease both}
.hl>div{padding:.6rem .9rem;border-right:1px solid var(--line);min-width:0}
.hl>div:last-child{border-right:none}
.hl-k{font-family:var(--mono);font-size:.64rem;letter-spacing:.16em;color:var(--mute2);margin-bottom:.25rem}
.hl-s{font-family:var(--mono);font-size:1.05rem;font-weight:700;letter-spacing:.08em;color:var(--c)}
.hl-n{font-family:var(--mono);font-size:2.1rem;font-weight:700;line-height:1.1;color:var(--c)}
.hl-m{font-size:.9rem;color:var(--text);margin-top:.2rem;word-break:break-all}
.hl-sub{font-size:.8rem;color:var(--dim);margin-top:.2rem}
.hl-t{font-size:1rem;font-weight:600;line-height:1.35;margin:.1rem 0 .3rem}
.hl-sc{font-family:var(--mono);font-size:.9rem;color:var(--dim)}
.hl-sc b{font-size:1.5rem;font-weight:700;margin:0 .35rem}
.hl-ck{display:flex;flex-direction:column;gap:.3rem}
.hl-ck div{display:flex;justify-content:space-between;gap:.5rem;font-size:.78rem;color:var(--dim)}
/* 완료 시 애니메이션 (.fx가 붙은 패널만) */
@property --n{syntax:'\\3c integer\\3e';initial-value:0;inherits:false}
.cnt:after{content:attr(data-v)}
.fx .cnt{counter-reset:n var(--n);animation:count 1.1s cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--i,0)*70ms + .1s)}
.fx .cnt:after{content:"0." counter(n,decimal-leading-zero)}
.pn.fx,.hl.fx{animation:sweep 1.5s ease both}
.fx .bar b{transform-origin:left center;animation:grow .9s cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--i,0)*70ms + .1s)}
.fx .tl-i,.fx .risk,.fx .led,.fx tr.row{animation:slidein .5s ease both;animation-delay:calc(var(--i,0)*70ms)}
.fx .risk.pass{animation:slidein .5s ease both,glow 1.4s ease .5s 2;animation-delay:calc(var(--i,0)*70ms),.5s}
@keyframes count{from{--n:0}to{--n:var(--v)}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes slidein{from{opacity:0;transform:translateX(-12px)}to{opacity:1;transform:none}}
@keyframes sweep{0%{box-shadow:0 0 0 0 rgba(0,217,255,0);border-color:var(--cy)}35%{box-shadow:0 0 22px -4px rgba(0,217,255,.5);border-color:var(--cy)}100%{box-shadow:none}}
@keyframes glow{0%,100%{background:rgba(61,220,151,.06)}50%{background:rgba(61,220,151,.28)}}
@media (prefers-reduced-motion:reduce){.fx .cnt:after{content:attr(data-v)!important}}
/* ---- 반응형: 글자는 화면 폭에 따라 줄고, 열은 메인 영역 폭에 따라 접힌다 ---- */
html{font-size:clamp(12px,.55vw + 7.2px,16px)!important}
.tb{flex-wrap:wrap;gap:.5rem}
.logo{white-space:nowrap}
.pn{max-width:100%;overflow:hidden}
.pn-h{flex-wrap:wrap;gap:.15rem .6rem}
.pn.u .pn-t{white-space:normal}
.pn-b{overflow-x:auto;overflow-wrap:anywhere}
.kv{flex-wrap:wrap}
.kv .v{flex:1 1 auto;min-width:0;overflow-wrap:anywhere;word-break:normal}
.st-key-btnrow .stButton>button p{white-space:normal;overflow-wrap:anywhere;line-height:1.2}
.st-key-btnrow .stButton>button{min-height:2.4rem}
.hlw{container-name:hlw;container-type:inline-size}
.st-key-ov_top,.st-key-ov_bot{container-name:ov;container-type:inline-size}
@media (max-width:900px){.tb-s{display:none}}
@container ov (min-width:1021px) and (max-width:1250px){
.stepper div{font-size:.46rem;letter-spacing:0;overflow:hidden}
.tile .v{font-size:.8rem;overflow-wrap:anywhere;word-break:normal}}
@container ov (max-width:1020px){
.st-key-ov_top>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"],
.st-key-ov_bot>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}
.st-key-ov_top>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
.st-key-ov_bot>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{flex:1 1 calc(50% - .5rem)!important;min-width:calc(50% - .5rem)!important;width:auto!important}
.st-key-ov_top>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
.st-key-ov_bot>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2){flex-basis:100%!important;min-width:100%!important;order:-1}}
@container ov (max-width:700px){
.st-key-ov_top>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
.st-key-ov_bot>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{flex-basis:100%!important;min-width:100%!important}}
@container hlw (max-width:1100px){
.hl{grid-template-columns:1fr 1fr}
.hl>div:nth-child(2){border-right:none}
.hl>div:nth-child(-n+2){border-bottom:1px solid var(--line)}}
@container hlw (max-width:620px){
.hl{grid-template-columns:1fr}
.hl>div{border-right:none;border-bottom:1px solid var(--line)}
.hl>div:last-child{border-bottom:none}
.hl-n{font-size:1.7rem}}
/* 소개 화면 (스토리) */
.iw{container-name:iw;container-type:inline-size}
.ul{margin:0;padding-left:1.1rem}
.ul li{margin:.4rem 0;line-height:1.55;font-size:.92rem;color:var(--text)}
.hero2{min-height:64vh;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:3rem 1rem 1.5rem}
.hero2 .k{font-family:var(--mono);letter-spacing:.3em;font-size:.7rem;color:var(--mute2)}
.hero2 h1{font-size:clamp(2.1rem,5vw,3.9rem);font-weight:800;line-height:1.18;letter-spacing:-.03em;margin:.9rem 0;padding:0;color:var(--text)}
.hero2 h1 em{font-style:normal;color:var(--cy);text-shadow:0 0 28px rgba(0,217,255,.45)}
.hero2 .sub{font-size:1.15rem;color:var(--dim);max-width:640px;line-height:1.7;margin:.2rem 0 0}
.hero2 .sub b{color:var(--text)}
.persona{display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem;max-width:960px;width:100%;margin-top:2.4rem;text-align:left}
.persona>div{border:1px solid var(--line);background:var(--panel);padding:.9rem 1.05rem;border-top:2px solid var(--c)}
.persona b{display:block;font-size:1.02rem}
.persona span{display:block;color:var(--dim);font-size:.92rem;margin-top:.35rem;line-height:1.5}
.cue{margin-top:2.2rem;font-family:var(--mono);font-size:.72rem;letter-spacing:.22em;color:var(--mute2);animation:blink 2.4s ease-in-out infinite}
.st-row{display:grid;grid-template-columns:1fr 1.3fr;gap:2.6rem;align-items:center;min-height:58vh;padding:2.8rem .4rem;border-top:1px solid var(--line)}
.st-row.rev>.st-t{order:2}
.st-n{font-family:var(--mono);font-size:.8rem;letter-spacing:.22em;color:var(--c)}
.st-h{font-size:clamp(1.6rem,3vw,2.55rem);font-weight:800;line-height:1.25;letter-spacing:-.02em;margin:.55rem 0}
.st-s{font-size:1.05rem;color:var(--dim);line-height:1.65;margin-bottom:1rem}
.st-v .pn{margin:0 0 .6rem}
.mini{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:.74rem}
.mini th{color:var(--mute2);text-align:left;padding:.3rem .45rem;border-bottom:1px solid var(--line2);font-weight:500}
.mini td{padding:.28rem .45rem;border-bottom:1px solid rgba(120,160,190,.1);color:var(--text);white-space:nowrap}
.big3{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem;margin-top:.6rem}
.bt{border:1px solid var(--line);border-top:2px solid var(--c);background:var(--panel2);padding:.8rem .9rem}
.bt .n{font-family:var(--mono);font-size:1.9rem;font-weight:700;color:var(--c);line-height:1.15;word-break:break-word}
.bt .l{font-size:.8rem;color:var(--dim);margin-top:.2rem}
.bub{border-left:3px solid var(--c);background:var(--panel2);padding:.55rem .85rem;margin-bottom:.5rem;font-size:.9rem;line-height:1.55}
.bub b{display:block;font-family:var(--mono);font-size:.68rem;letter-spacing:.1em;color:var(--c);margin-bottom:.15rem}
.spec{display:grid;grid-template-columns:8.5rem 1fr;gap:.1rem 1rem}
.spec .k{font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;color:var(--mute2);padding:.45rem 0;border-bottom:1px dashed rgba(120,160,190,.12)}
.spec .v{font-size:.92rem;line-height:1.5;padding:.4rem 0;border-bottom:1px dashed rgba(120,160,190,.12)}
@supports (animation-timeline:view()){.st-row,.persona>div{animation:rise linear both;animation-timeline:view();animation-range:entry 0% entry 40%}}
@keyframes rise{from{opacity:0;transform:translateY(46px)}to{opacity:1;transform:none}}
@container iw (max-width:900px){.st-row{grid-template-columns:1fr;min-height:0;gap:1.3rem;padding:2rem .2rem}.st-row.rev>.st-t{order:0}.persona{grid-template-columns:1fr}.big3{grid-template-columns:1fr 1fr}}
@container iw (max-width:560px){.spec{grid-template-columns:1fr}.spec .k{border-bottom:none;padding-bottom:0}.big3{grid-template-columns:1fr}}
/* 결과 화면: 번호가 붙은 단계 */
.sh{display:flex;align-items:center;gap:1rem;margin:2.6rem 0 1rem;padding-top:1.6rem;border-top:1px solid var(--line)}
.sh.first{margin-top:.8rem;border-top:none;padding-top:0}
.sh-n{flex:none;width:2.9rem;height:2.9rem;border-radius:50%;border:2px solid var(--c);color:var(--c);display:grid;place-items:center;font-family:var(--mono);font-weight:700;font-size:1.3rem;box-shadow:0 0 18px -6px var(--c)}
.sh-b{flex:1;min-width:0}
.sh-t{font-size:1.6rem;font-weight:800;letter-spacing:-.02em;line-height:1.25}
.sh-s{color:var(--dim);font-size:1rem;margin-top:.2rem}
.sh-w{flex:none}
.sum-s{font-size:1.5rem;font-weight:600;line-height:1.6;margin:.2rem 0 1rem}
.sum-s b{font-weight:800}
.legend{display:flex;flex-wrap:wrap;gap:.3rem 1.2rem;font-size:.82rem;color:var(--dim);margin-top:.2rem}
.legend i{display:inline-block;width:.8rem;height:.8rem;margin-right:.4rem;vertical-align:-.1rem}
.cd-top{border:1px solid var(--line2);border-left:4px solid var(--c);background:var(--panel2);padding:1rem 1.2rem;margin-bottom:.4rem}
.cd-k{font-family:var(--mono);font-size:.72rem;letter-spacing:.12em;color:var(--mute2)}
.cd-t{font-size:1.35rem;font-weight:800;line-height:1.4;margin:.35rem 0 .6rem}
.cd-s{display:flex;align-items:center;flex-wrap:wrap;gap:.6rem;font-size:.98rem;color:var(--dim);margin:.5rem 0}
.cd-s b{font-family:var(--mono);font-size:1.7rem;font-weight:700}
.cd-c{font-size:.98rem;line-height:1.65;color:var(--text);margin-top:.5rem}
.cd-row{display:grid;grid-template-columns:1.6rem 1fr 4rem;gap:.6rem;align-items:center;padding:.55rem 0;border-top:1px dashed rgba(120,160,190,.14);font-size:.98rem}
.cd-row .rk{font-family:var(--mono);color:var(--mute2)}
.cd-row .sc{font-family:var(--mono);text-align:right;font-weight:700}
.fx .cd-row{animation:slidein .5s ease both;animation-delay:calc(var(--i,0)*80ms)}
.cc{display:grid;grid-template-columns:1.3fr 1fr;gap:.8rem}
.cc .pn{margin:0}
.ol{margin:0;padding-left:1.3rem}
.ol li{margin:.6rem 0;line-height:1.65;font-size:1rem}
.big3.one{grid-template-columns:1fr}
.big3.one .bt{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;padding:.7rem .9rem}
.big3.one .bt .l{margin:0;text-align:right}
.st-key-ov_sum,.st-key-ov_run{container-name:ov2;container-type:inline-size}
@container ov2 (max-width:900px){
.st-key-ov_sum>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"],
.st-key-ov_run>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}
.st-key-ov_sum>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
.st-key-ov_run>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{flex-basis:100%!important;min-width:100%!important}}
@container iw (max-width:760px){.cc{grid-template-columns:1fr}}
@media (max-width:760px){.sh-t{font-size:1.25rem}.sh-w{display:none}.sum-s{font-size:1.2rem}}
/* 틸론 GPU 강조: 상단 칩과 사이드바 버튼 */
.st.big{font-size:.74rem;padding:.3rem .65rem;color:var(--text);border-color:rgba(167,139,250,.65);background:rgba(167,139,250,.13);box-shadow:0 0 18px -6px #A78BFA}
.st-key-menu_gpu button{border-color:#A78BFA!important;background:rgba(167,139,250,.16)!important;color:#E4DBFF!important;font-size:.9rem!important;min-height:3rem;box-shadow:0 0 18px -8px #A78BFA}
.st-key-menu_gpu button:hover{background:rgba(167,139,250,.3)!important}
/* 완료 토스트: 5초 뒤 CSS만으로 사라진다 (클릭은 통과). 시간을 바꾸려면 아래 5s 세 곳과 keyframes 비율을 함께 바꾼다 */
.toast{position:fixed;top:13%;left:50%;width:min(580px,92vw);z-index:99999;pointer-events:none;animation:toast 5s ease both}
.toast-in{position:relative;overflow:hidden;background:#0a0f16;border:1px solid var(--c);padding:1rem 1.3rem 1.2rem;
box-shadow:0 0 0 1px rgba(0,0,0,.6),0 0 44px -6px var(--c),0 24px 70px rgba(0,0,0,.65)}
.toast-k{font-family:var(--mono);font-size:.68rem;letter-spacing:.2em;color:var(--mute2)}
.toast-r{display:flex;align-items:center;flex-wrap:wrap;gap:.5rem;margin:.55rem 0 .35rem}
.toast-rank{font-family:var(--mono);font-weight:700;font-size:1.7rem;letter-spacing:.12em;color:var(--c);text-shadow:0 0 16px var(--c)}
.toast-t{font-size:1.25rem;font-weight:700;line-height:1.4}
.toast-s{font-family:var(--mono);font-size:.95rem;color:var(--dim);margin-top:.55rem}
.toast-s b{font-size:1.9rem;font-weight:700;margin:0 .45rem}
.toast-n{font-size:.78rem;color:var(--mute2);margin-top:.5rem}
.toast-bar{position:absolute;left:0;bottom:0;height:3px;width:100%;background:var(--c);transform-origin:left;animation:toastbar 5s linear both}
@keyframes toast{0%{opacity:0;transform:translate(-50%,-18px) scale(.96)}5%{opacity:1;transform:translate(-50%,0) scale(1)}94%{opacity:1;transform:translate(-50%,0) scale(1)}100%{opacity:0;transform:translate(-50%,-8px) scale(.98);visibility:hidden}}
@keyframes toastbar{from{transform:scaleX(1)}to{transform:scaleX(0)}}
@keyframes toastrm{0%,94%{opacity:1}100%{opacity:0;visibility:hidden}}
@media (prefers-reduced-motion:reduce){.toast{transform:translateX(-50%);animation:toastrm 5s linear both!important}.toast-bar{animation:none!important}}
</style>
"""


def inject_css():
    return CSS


# 발표 모드: 모든 크기가 rem 기준이라 루트 글자만 1.2배 키우면 화면 전체가 함께 커진다
PRESENT_CSS = "<style>html{font-size:clamp(14.4px,.66vw + 8.6px,19.2px)!important}</style>"


def svg_img(svg, alt, style="width:100%;height:auto;display:block"):
    """st.html은 인라인 <svg>를 제거하므로 data-URI 이미지로 넣는다 (SVG 안의 CSS 애니메이션은 그대로 동작)"""
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img alt="{e(alt)}" style="{style}" src="data:image/svg+xml;base64,{b64}">'


SVG_STYLE = ("<style>.spin{transform-origin:180px 180px;animation:spin 60s linear infinite}"
             ".spin2{transform-origin:180px 180px;animation:spin 90s linear infinite reverse}"
             ".halo{transform-origin:180px 180px;animation:pulse 3.2s ease-in-out infinite}"
             ".sig{animation:blink 1.6s ease-in-out infinite}"
             "@keyframes spin{to{transform:rotate(360deg)}}"
             "@keyframes pulse{0%,100%{opacity:.35;transform:scale(.97)}50%{opacity:.8;transform:scale(1.03)}}"
             "@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}"
             "@media (prefers-reduced-motion:reduce){*{animation:none!important}}</style>")


# ---------- 공통 조각 ----------
def chip(text, color):
    return f'<span class="chip" style="color:{color};border-color:{color}66;background:{color}14">{e(text)}</span>'


def bar(pct, color):
    return f'<div class="bar"><b style="width:{max(0, min(100, pct)):.0f}%;background:{color}"></b></div>'


def score_color(v):
    return "#3DDC97" if v >= 0.6 else ("#FFB547" if v >= 0.4 else "#6B7C8C")


def panel(title, body, tag="", tone="#00D9FF", extra=""):
    return (f'<div class="pn u {extra}"><div class="pn-h"><span class="pn-t"><i style="background:{tone};box-shadow:0 0 6px {tone}"></i>{e(title)}</span>'
            f'<span class="pn-g">{e(tag)}</span></div><div class="pn-b">{body}</div></div>')


def kv(rows):
    return "".join(f'<div class="kv"><span class="k">{e(k)}</span><span class="v">{v}</span></div>' for k, v in rows)


def sec(t):
    return f'<div class="sec">{e(t)}</div>'


# ---------- 상단 바 / 첫 화면 ----------
def topbar(llm_on, voice_on, dataset, sid, gpu=None):
    """gpu: (라벨, 색, 깜빡임) 틸론 GPU 연결 상태 칩. None이면 표시하지 않는다."""
    def st(label, color, blink=False, cls=""):
        return f'<span class="st{cls}"><i class="dot{" on" if blink else ""}" style="background:{color};box-shadow:0 0 6px {color}"></i>{e(label)}</span>'
    chips = [st("AI AGENTS ONLINE" if llm_on else "AI OFFLINE · RULE MODE", "#3DDC97" if llm_on else "#FFB547", llm_on)]
    if gpu:
        chips.append(st(*gpu, cls=" big"))
    chips += [st("VOICE ON" if voice_on else "VOICE OFF", "#00D9FF" if voice_on else "#6B7C8C"),
             st(f"DATASET · {dataset}" if dataset else "NO DATASET", "#A78BFA" if dataset else "#6B7C8C"),
             st(f"SESSION {sid}", "#6B7C8C")]
    return (f'<div class="tb"><div class="tb-l"><span class="logo">CD <span>ROOT</span></span>'
            f'<span class="tb-s">AUTONOMOUS DATA INVESTIGATION SYSTEM</span></div><div class="tb-r">{"".join(chips)}</div></div>')


# ---------- 소개 화면: 스크롤하며 한 단계씩 보여주는 스토리 (한 화면 한 메시지) ----------
def _ul(items):
    return '<ul class="ul">' + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def story_hero(personas):
    """첫 화면: 큰 문구 두 줄 + 이런 분께 3카드. personas: [(누구, 한 줄 고민, 색)]"""
    cards = "".join(f'<div style="--c:{c}"><b>{e(t)}</b><span>{e(s)}</span></div>' for t, s, c in personas)
    return ('<div class="iw"><div class="hero2"><div class="k">AUTONOMOUS DATA INVESTIGATION</div>'
            '<h1>숫자가 갑자기 변했는데,<br><em>왜</em>인지 모르겠다면.</h1>'
            '<p class="sub">CSV 하나만 올리세요.<br><b>CD ROOT</b>가 사건을 찾고, 원인 후보를 증거와 함께 가려 드립니다.</p>'
            f'<div class="persona">{cards}</div><div class="cue">↓ SCROLL · 어떻게 동작하는지 볼까요</div></div></div>')


def story_row(no, title, sub, who, where, visual, color, rev=False):
    """스토리 한 단계: 왼쪽에 번호·제목·설명·(누가/어디서), 오른쪽에 실제 결과 화면. title/sub는 코드에 박힌 문구(HTML 허용)."""
    wc = {"CODE": "#00D9FF", "AI": "#A78BFA", "YOU": "#3DDC97", "TILON": "#A78BFA"}.get(who.split(" ")[0], "#6B7C8C")
    return (f'<div class="iw"><div class="st-row{" rev" if rev else ""}" style="--c:{color}">'
            f'<div class="st-t"><div class="st-n">{e(no)}</div><div class="st-h">{title}</div><div class="st-s">{sub}</div>'
            f'<div>{chip("WHO · " + who, wc)}{chip("WHERE · " + where, "#6B7C8C")}</div></div>'
            f'<div class="st-v">{visual}</div></div></div>')


def mini_table(name, shape, header, rows):
    th = "".join(f"<th>{e(h)}</th>" for h in header)
    tr = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in rows)
    body = (f'<div style="overflow-x:auto"><table class="mini"><tr>{th}</tr>{tr}</table></div>'
            f'<div class="txt dim" style="margin-top:.5rem">{e(shape)} · 날짜·수치·그룹 열이 섞인 평범한 CSV</div>')
    return panel(name, body, tag="YOUR CSV", tone="#3DDC97")


def stats(tiles, one=False):
    """큰 숫자 타일. tiles: [(값, 라벨, 색)]"""
    return f'<div class="big3{" one" if one else ""}">' + "".join(f'<div class="bt" style="--c:{c}"><div class="n"{" style=\"font-size:1.2rem;white-space:nowrap\"" if len(str(v)) > 8 else ""}>{e(v)}</div><div class="l">{e(l)}</div></div>'
                                        for v, l, c in tiles) + "</div>"


def bubbles(items, tag):
    """에이전트 말풍선. items: [(라벨, 텍스트, 색)]"""
    return panel("AGENT DEBATE", "".join(f'<div class="bub" style="--c:{c}"><b>{e(l)}</b>{e(t)}</div>' for l, t, c in items),
                 tag=tag, tone="#A78BFA")


def result_card(top, hyps):
    """결과 화면 미리보기: RANK 1 카드 + 가설 순위 막대"""
    c = score_color(top["score"])
    rows = "".join(f'<div style="margin-top:.55rem"><div style="display:flex;justify-content:space-between;font-size:.86rem">'
                   f'<span>{chip(h[0], KIND_COLOR.get(h[2], "#00D9FF"))}{e(h[1][:34])}</span><span class="mono" style="color:{score_color(h[3])}">{h[3]:.2f}</span></div>'
                   f'{bar(h[3] * 100, score_color(h[3]))}</div>' for h in hyps)
    body = (f'<div style="--c:{c}"><div class="toast-r"><span class="toast-rank">RANK 1</span>'
            f'{chip(KIND_LABEL.get(top["kind"], top["kind"]), KIND_COLOR.get(top["kind"], "#00D9FF"))}</div>'
            f'<div class="toast-t">{e(top["title"])}</div>'
            f'<div class="toast-s">SCORE<span class="mono" style="color:{c};font-size:1.9rem;font-weight:700;margin:0 .6rem">{top["score"]:.2f}</span>{chip(top["label"], c)}</div></div>'
            f'{sec("ALL CANDIDATES")}{rows}'
            '<div class="toast-n" style="margin-top:.7rem">후보이며 원인을 확정하지 않습니다 · 점수는 코드가 계산합니다</div>')
    return panel("RESULT", body, tag="CANDIDATES ONLY", tone=c)


# ---------- 사용 가이드 탭 (자세한 설명) ----------
def guide_data():
    rows = [("형식", "CSV (UTF-8, 한글 CP949 자동 인식)"),
            ("크기", "20MB · 20만 행 이하 (넘으면 앞부분만 분석)"),
            ("꼭 필요한 열", "<b>숫자 열 1개 이상</b>. 행은 최소 수십 개를 권장"),
            ("날짜(시간) 열", "있으면 <b>시간에 따른 변화</b>, 없으면 <b>그룹 간 차이</b>를 분석"),
            ("열 이름", "자유. 이름이 아니라 값의 성질로 자동 분류"),
            ("사건이 없으면", "변화가 노이즈 수준이면 억지로 만들지 않고 “사건 없음”으로 안내")]
    body = '<div class="spec">' + "".join(f'<div class="k">{e(k)}</div><div class="v">{v}</div>' for k, v in rows) + "</div>"
    return panel("DATA CONDITIONS · 어떤 파일을 올리나요", body, tag="CSV ONLY", tone="#FFB547")


def guide_where():
    rows = [("1  읽기 · 열 분류", "코드", "CD ROOT 서버", "없음"),
            ("2  사건 탐지 · 가설 채점", "코드", "CD ROOT 서버", "없음"),
            ("3  가설 토론", "AI (Claude)", "외부 AI API", "요약된 증거 · 열 이름 · 그룹 값 (원본 행은 보내지 않음)"),
            ("4  증거 검증", "코드", "CD ROOT 서버", "없음"),
            ("5  결과 표시", "코드", "내 브라우저", "없음"),
            ("6  GPU 요약 카드 (선택)", "틸론 GPU (Qwen)", "틸론 노트북", "요약된 증거 (GitHub 비공개 저장소를 거쳐 전달, 버튼을 눌렀을 때만)"),
            ("7  검토 결과 저장 (선택)", "코드", "Supabase (DB)", "검토 결과 요약: 증거·점수·AI 발언·보고서 (원본 행과 업로드 파일 이름은 저장하지 않음)")]
    tr = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td><td>{e(d)}</td></tr>" for a, b, c, d in rows)
    body = (f'<table class="tbl"><tr><th>단계</th><th>누가</th><th>어디서</th><th>밖으로 나가는 것</th></tr>{tr}</table>'
            '<div class="txt dim" style="margin-top:.5rem">AI를 끄면(규칙 모드) 외부로 나가는 정보는 없습니다.</div>')
    return panel("WHAT RUNS WHERE · 무엇이 어디서 실행되나요", body, tag="PRIVACY", tone="#00D9FF")


def guide_score():
    return panel("HOW IT DECIDES · 순위는 어떻게 정해지나요",
                 _ul(["점수는 <b>코드가 항목별로 0~1점</b>을 매깁니다 (변화 크기, 먼저 변했는지, 정상 구간에도 흔한 변화인지, 쏠림 정도 등)",
                      "<b>토론은 점수를 바꾸지 않습니다.</b> AI는 주장·반박·서술만 하고, 순위는 증거로 정해집니다",
                      "발언 속 숫자는 증거 장부에 있는 값만 허용하고, 보고서에 장부 밖 숫자가 있으면 규칙 기반 보고서로 바꿉니다",
                      "가설은 서로 배타적이지 않고, 원인을 확정하지 않고 <b>후보</b>로만 제시합니다"]), tag="BY CODE", tone="#A78BFA")


def guide_limits():
    return panel("LIMITS · 알려진 한계",
                 _ul(["숫자 검사는 10 미만 정수와 소수점 없는 값을 보지 않고, <b>문장의 의미 오류는 못 잡습니다</b>",
                      "주 지표는 변화가 가장 확실한 것을 자동 선택합니다. 원하는 지표는 사건 피드에서 직접 고르세요",
                      "그룹 쏠림 점수에는 우연 대조가 없어, 시간 축 없이 무작위 그룹이 붙은 데이터에서는 과대평가될 수 있습니다",
                      "사건 구간은 가장 크게 변한 창 하나만 잡습니다"]), tag="HONEST NOTES", tone="#FF4D6D")


def guide_gpu():
    return panel("TILON GPU · 교내 GPU 활용",
                 _ul(["틸론 GPU에서 소형 모델(Qwen2.5-1.5B)이 만든 <b>사건 요약 카드</b>를 두 가지 프롬프트로 비교합니다",
                      "틸론이 켜져 있으면(<b>ONLINE</b>) 사이드바 <b>TILON GPU 결과</b> 버튼에서 <b>내 데이터로 실시간 생성</b>할 수 있습니다 (GitHub 작업 큐를 거쳐 틸론이 가져가 처리)",
                      "틸론이 꺼져 있으면(<b>OFFLINE</b>) 틸론에서 미리 실행해 저장한 결과를 보여줍니다"]), tag="LIVE + SAVED", tone="#A78BFA")


# ---------- AI Core ----------
def core(state, sub="", metric="-", change="-", window="-"):
    step, color, hint = STATES.get(state, STATES["STANDBY"])
    circ = 2 * math.pi * 128
    prog = circ * step / 6
    ticks = "".join(
        f'<line x1="180" y1="{180-152}" x2="180" y2="{180-(144 if i % 5 == 0 else 148)}" stroke="{color if i % 5 == 0 else "#2a3a4b"}" '
        f'stroke-width="{1.6 if i % 5 == 0 else 1}" transform="rotate({i*6} 180 180)"/>' for i in range(60))
    words = state.split(" ")
    lines = [" ".join(words[:1]), " ".join(words[1:])] if len(words) > 1 else [state, ""]
    t1 = f'<text x="180" y="{172 if lines[1] else 180}" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="15" font-weight="700" letter-spacing="2" fill="{color}">{e(lines[0])}</text>'
    t2 = (f'<text x="180" y="190" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="15" font-weight="700" letter-spacing="2" fill="{color}">{e(lines[1])}</text>' if lines[1] else "")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 360" width="360" height="360">{SVG_STYLE}
<defs><radialGradient id="g" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="{color}" stop-opacity=".28"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient></defs>
<circle cx="180" cy="180" r="170" fill="url(#g)" class="halo"/>
<g class="spin"><circle cx="180" cy="180" r="166" fill="none" stroke="#3a4f63" stroke-width="1" stroke-dasharray="2 9"/></g>
<g class="spin2"><circle cx="180" cy="180" r="116" fill="none" stroke="{color}" stroke-opacity=".35" stroke-width="1" stroke-dasharray="24 10 4 10"/></g>
{ticks}
<circle cx="180" cy="180" r="128" fill="none" stroke="#1b2735" stroke-width="6"/>
<circle cx="180" cy="180" r="128" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="butt" stroke-dasharray="{prog:.1f} {circ:.1f}" transform="rotate(-90 180 180)" style="filter:drop-shadow(0 0 5px {color})"/>
<circle cx="180" cy="180" r="92" fill="#0a1017" stroke="{color}" stroke-opacity=".7" stroke-width="1.2"/>
<circle cx="180" cy="180" r="84" fill="none" stroke="{color}" stroke-opacity=".18" stroke-width="1"/>
<circle class="sig" cx="180" cy="128" r="3" fill="{color}"/>
{t1}{t2}
<text x="180" y="218" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="9" letter-spacing="2" fill="#6B7C8C">STEP {step}/6</text>
</svg>'''
    svg = svg_img(svg, f"AI Investigation Core: {state}", "width:100%;max-width:400px;height:auto;display:block")
    stepper = "".join(f'<div class="{"now" if i == step else ("done" if i < step else "")}" style="{"color:"+color if i == step else ""}">{n}</div>'
                      for i, n in enumerate(STEPS))
    tiles = "".join(f'<div class="tile"><div class="k">{k}</div><div class="v" style="color:{c}">{e(v)}</div></div>'
                    for k, v, c in (("MAIN METRIC", metric, "#E6EDF3"), ("CHANGE", change, color), ("WINDOW", window, "#E6EDF3")))
    return (f'<div class="pn"><div class="pn-h"><span class="pn-t"><i style="background:{color};box-shadow:0 0 6px {color}"></i>AI INVESTIGATION CORE</span>'
            f'<span class="pn-g">{e(sub)}</span></div><div class="pn-b"><div class="core">{svg}'
            f'<div class="mono" style="color:{color};font-size:.72rem;letter-spacing:.06em;margin:-.2rem 0 .3rem">{e(hint)}</div>'
            f'<div class="stepper">{stepper}</div><div class="tiles">{tiles}</div></div></div></div>')


# ---------- 좌측 ----------
def fmt_num(v):
    """큰 수를 짧게 (1.50M, 12.3K)"""
    a = abs(v)
    return f"{v/1e6:.2f}M" if a >= 1e6 else (f"{v/1e3:.1f}K" if a >= 1e4 else f"{v:,.1f}")


def chart(data, tag=""):
    """이상 탐지 그래프. data: time {y,b0,e0,ee,on,x0,x1} 또는 group {bars:[(라벨,값)]}"""
    if not data:
        return panel("값의 변화 그래프", '<div class="txt dim">그래프를 그릴 데이터가 없습니다.</div>', tone="#FFB547")
    if data["kind"] == "group":
        bars = data["bars"]
        mx = max(abs(v) for _, v in bars) or 1
        out = []
        for i, (lab, v) in enumerate(bars):
            w = abs(v) / mx * 100
            c = "#FFB547" if i == 0 else "#00D9FF"
            out.append(f'<div class="kv"><span class="k">{e(lab)}</span><span class="v mono">{v:,.1f}</span></div>{bar(w, c)}')
        return panel("그룹별 평균 비교", "".join(out), tag=tag or "시간 축 없음", tone="#FFB547")
    # 실제 시계열 값으로 그린 그래프 (좌표는 모두 data["y"]에서 계산)
    W, H, pl, pr, pt, pb = 360, 176, 40, 10, 20, 30
    y = data["y"]
    n = len(y)
    lo, hi = min(y), max(y)
    span = (hi - lo) or 1
    X = lambda i: pl + i * (W - pl - pr) / max(n - 1, 1)
    Y = lambda v: pt + (1 - (v - lo) / span) * (H - pt - pb)
    b0, e0, ee = data["b0"], data["e0"], min(data["ee"], n)
    bm = sum(y[b0:e0]) / max(e0 - b0, 1)
    em = sum(y[e0:ee]) / max(ee - e0, 1)
    pk = max(range(e0, ee), key=lambda i: abs(y[i] - bm)) if ee > e0 else n - 1
    F = 'font-family="JetBrains Mono,Consolas,monospace"'
    grid = "".join(f'<line x1="{pl}" y1="{Y(v):.1f}" x2="{W-pr}" y2="{Y(v):.1f}" stroke="#1d2a38" stroke-width="1"/>'
                   f'<text x="{pl-5}" y="{Y(v)+3.5:.1f}" text-anchor="end" font-size="10" fill="#8FA3B5" {F}>{fmt_num(v)}</text>'
                   for v in (hi, (hi + lo) / 2, lo))
    band = lambda a, b, c: f'<rect x="{X(a):.1f}" y="{pt-8}" width="{max(X(b)-X(a), 1):.1f}" height="{H-pt-pb+8}" fill="{c}"/>'
    d = "M" + " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(y))
    on = data.get("on")
    marker = (f'<line x1="{X(on):.1f}" y1="{pt-8}" x2="{X(on):.1f}" y2="{H-pb}" stroke="#FF4D6D" stroke-width="1.2" stroke-dasharray="4 3"/>'
              f'<text x="{X(on)-4:.1f}" y="{pt+2}" text-anchor="end" font-size="10" font-weight="700" fill="#FF4D6D" {F}>ONSET</text>') if on is not None else ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
           '<style>.ln{stroke-dasharray:1000;stroke-dashoffset:1000;animation:draw 1.2s ease-out .15s forwards}'
           '.pk{animation:pop .5s ease 1.1s both}@keyframes draw{to{stroke-dashoffset:0}}'
           '@keyframes pop{from{opacity:0}to{opacity:1}}@media (prefers-reduced-motion:reduce){.ln{animation:none;stroke-dashoffset:0}.pk{animation:none}}</style>'
           f'{grid}{band(b0, e0, "rgba(0,217,255,.09)")}{band(e0, ee, "rgba(255,181,71,.16)")}'
           f'<line x1="{X(b0):.1f}" y1="{Y(bm):.1f}" x2="{X(e0):.1f}" y2="{Y(bm):.1f}" stroke="#8FA3B5" stroke-dasharray="3 3" stroke-width="1"/>'
           f'<line x1="{X(e0):.1f}" y1="{Y(em):.1f}" x2="{X(ee-1):.1f}" y2="{Y(em):.1f}" stroke="#FFB547" stroke-dasharray="3 3" stroke-width="1"/>'
           f'<path class="ln" pathLength="1000" d="{d}" fill="none" stroke="#00D9FF" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>{marker}'
           f'<g class="pk"><circle cx="{X(pk):.1f}" cy="{Y(y[pk]):.1f}" r="3.6" fill="#FFB547" stroke="#0D131B" stroke-width="1.5"/>'
           f'<text x="{min(X(pk)+7, W-pr-34):.1f}" y="{max(Y(y[pk])-6, pt+12):.1f}" font-size="10" font-weight="700" fill="#FFB547" {F}>{fmt_num(y[pk])}</text></g>'
           f'<text x="{X(b0)+4:.1f}" y="{H-16}" font-size="10" fill="#8FA3B5" {F}>BASE {fmt_num(bm)}</text>'
           f'<text x="{X(e0)+4:.1f}" y="{H-16}" font-size="10" fill="#FFB547" {F}>EVENT {fmt_num(em)}</text>'
           f'<text x="{pl}" y="{H-3}" font-size="9.5" fill="#6B7C8C" {F}>{e(data.get("x0", ""))}</text>'
           f'<text x="{W-pr}" y="{H-3}" text-anchor="end" font-size="9.5" fill="#6B7C8C" {F}>{e(data.get("x1", ""))}</text></svg>')
    cap = (f'<div class="kv" style="margin-top:.2rem"><span class="k">BASE → EVENT 평균</span>'
           f'<span class="v mono">{fmt_num(bm)} → <b style="color:#FFB547">{fmt_num(em)}</b></span></div>')
    return panel("값의 변화 그래프", svg_img(svg, "주 지표 시계열과 사건 구간") + legend() + cap, tag=tag, tone="#00D9FF")


# ---------- 우측 Inspector ----------
# ---------- 하단 ----------
def cnt(v, color, size=""):
    """점수(0~1). 완료 애니메이션(.fx) 안에서는 0에서 값까지 올라가고, 아니면 값을 그대로 보여준다"""
    iv = round(v * 100)
    if iv >= 100 or iv < 0:
        return f'<span style="color:{color};{size}">{v:.2f}</span>'
    return f'<span class="cnt" data-v="{v:.2f}" style="--v:{iv};color:{color};{size}"></span>'


def timeline(events, anim=False):
    if not events:
        return panel("진행 기록", '<div class="txt dim">아직 기록이 없습니다.</div>', tone="#00D9FF")
    items = "".join(f'<div class="tl-i" style="--c:{ev["c"]};--i:{i}"><div class="tl-t">{e(ev["t"])} · {e(ev["tag"])}</div><div class="tl-m">{e(ev["msg"])}</div></div>'
                    for i, ev in enumerate(reversed(events)))
    return panel("진행 기록", f'<div class="tl">{items}</div>', tag=f"{len(events)}건", tone="#00D9FF", extra="fx" if anim else "")


def compare(H, anim=False):
    if not H:
        return panel("가설별 점수 근거", '<div class="txt dim">가설이 없습니다.</div>', tone="#A78BFA")
    rows = []
    for i, h in enumerate(H, 1):
        c = score_color(h["total"])
        parts = []
        for cp in h["components"]:
            sv = cp["score"] or 0
            txt = "-" if cp["score"] is None else f"{sv:.2f}"
            parts.append(f'<div title="{e(cp["note"])}">{e(cp["name"])} <span class="mono" style="color:{score_color(sv)}">{txt}</span>'
                         f'{bar(sv * 100, score_color(sv))}</div>')
        comps = "".join(parts)
        rows.append(f'<tr class="row" style="--i:{i}"><td class="mono dim">{i}</td><td>{chip(h["id"], KIND_COLOR.get(h["kind"], "#00D9FF"))}</td>'
                    f'<td><div>{e(h["title"])}</div><div class="cmp-c">{comps}</div></td>'
                    f'<td style="width:22%"><div class="mono" style="text-align:right;font-size:1.15rem;font-weight:700">{cnt(h["total"], c)}</div>{bar(h["total"]*100, c)}</td></tr>')
    return panel("가설별 점수 근거", f'<table class="tbl"><tr><th>순위</th><th>번호</th><th>가설 · 점수 항목</th><th>점수</th></tr>{"".join(rows)}</table>',
                 tag="점수는 코드가 계산", tone="#A78BFA", extra="fx" if anim else "")


def toast(t):
    """조사 완료 때 1위 결과를 5초 동안 크게 보여주는 오버레이. t: id, title, kind, score, label, gap, mode"""
    c = score_color(t["score"])
    kc = KIND_COLOR.get(t["kind"], "#00D9FF")
    return (f'<div class="toast fx" style="--c:{c}"><div class="toast-in">'
            f'<div class="toast-k">INVESTIGATION COMPLETE · {e(t["mode"])}</div>'
            f'<div class="toast-r"><span class="toast-rank">RANK 1</span>{chip(KIND_LABEL.get(t["kind"], t["kind"]), kc)}{chip(t["id"], "#6B7C8C")}</div>'
            f'<div class="toast-t">{e(t["title"])}</div>'
            f'<div class="toast-s">SCORE{cnt(t["score"], c, "font-size:1.9rem;font-weight:700;margin:0 .6rem")}{chip(t["label"], c)} GAP {e(t["gap"])}</div>'
            f'<div class="toast-n">후보이며 원인을 확정하지 않습니다 · 점수는 코드가 계산합니다</div>'
            f'<div class="toast-bar"></div></div></div>')


# ---------- 결과 화면: 번호가 붙은 4단계 (무슨 일 → 원인 후보 → AI 검토 → 결론) ----------
KIND_PLAIN = {"origin": "가장 먼저 변한 지표", "covariate": "함께 변한 지표", "group": "특정 그룹 쏠림",
              "data": "데이터 오류 가능성", "direct": "그룹 자체의 영향", "confound": "숨은 요인"}
ul = _ul


def sec_head(no, title, sub, who="", color="#00D9FF", first=False):
    """단계 머리글: 큰 번호 + 쉬운 제목 + 한 줄 설명 + 누가 하는지"""
    wc = {"코드가 계산": "#00D9FF", "AI가 검토": "#A78BFA", "내가 확인": "#3DDC97"}
    tag = chip(who, wc.get(who, "#6B7C8C")) if who else ""
    return (f'<div class="sh{" first" if first else ""}" style="--c:{color}"><span class="sh-n">{e(no)}</span>'
            f'<div class="sh-b"><div class="sh-t">{e(title)}</div><div class="sh-s">{e(sub)}</div></div><div class="sh-w">{tag}</div></div>')


def legend():
    items = [("#00D9FF", "파란 선: 값의 변화"), ("rgba(0,217,255,.35)", "하늘색 구간: 평소"), ("rgba(255,181,71,.55)", "주황 구간: 사건"), ("#FF4D6D", "빨간 점선: 변화가 시작된 때")]
    return '<div class="legend">' + "".join(f'<span><i style="background:{c}"></i>{e(t)}</span>' for c, t in items) + "</div>"


def candidates(H, label, claim, anim=False):
    """② 원인 후보: 1위 카드 + 나머지 순위. 점수는 코드가 계산한 값"""
    top = H[0]
    c = score_color(top["total"])
    topcard = (f'<div class="cd-top" style="--c:{c}"><div class="cd-k">1위 후보 · {e(KIND_PLAIN.get(top["kind"], top["kind"]))}</div>'
               f'<div class="cd-t">{e(top["title"])}</div>{bar(top["total"] * 100, c)}'
               f'<div class="cd-s">근거 강도 {cnt(top["total"], c, "font-size:1.7rem;font-weight:700")}{chip(label, c)}</div>'
               f'<div class="cd-c">{e(claim)}</div></div>')
    rows = "".join(f'<div class="cd-row" style="--i:{i}"><span class="rk">{i}</span><div><div>{chip(KIND_PLAIN.get(h["kind"], h["kind"]), KIND_COLOR.get(h["kind"], "#00D9FF"))} {e(h["title"])}</div>'
                   f'{bar(h["total"] * 100, score_color(h["total"]))}</div><span class="sc" style="color:{score_color(h["total"])}">{h["total"]:.2f}</span></div>'
                   for i, h in enumerate(H[1:5], 2))
    return panel("원인 후보 순위", topcard + (sec("다른 후보") + rows if rows else ""), tag="점수는 코드가 CSV에서 계산", tone=c, extra="fx" if anim else "")


def conclusion(v):
    """④ 결론 카드 + 다음에 확인할 일 + 주의할 점. v: done, title, claim, score, label, actions, risks"""
    lv = {"PASS": "확인됨", "WARN": "주의", "INFO": "참고", "CRIT": "문제"}
    c = score_color(v["score"])
    fx = "fx" if v["done"] else ""
    if not v["done"]:
        state = chip("AI 검토 전 · 코드 점수 기준 잠정 결과", "#FFB547")
    elif v.get("ai"):
        state = chip("AI 검토 완료 · 코드가 숫자 재검증", "#3DDC97")
    else:
        state = chip("규칙 기반 분석 완료 · AI는 쓰지 않았어요", "#3DDC97")
    left = (f'<div style="margin-bottom:.5rem">{state}</div><div class="cd-t">{e(v["title"])}</div>'
            f'<div class="cd-s">근거 강도 <b style="color:{c}">{v["score"]:.2f}</b>{chip(v["label"], c)}</div><div class="cd-c">{e(v["claim"])}</div>'
            '<div class="toast-n" style="margin-top:.7rem">원인을 확정하지 않는 후보예요. 다른 후보와 함께 검토하세요.</div>')
    acts = '<ol class="ol">' + "".join(f"<li>{e(a)}</li>" for a in v["actions"]) + "</ol>"
    risks = "".join(f'<div class="risk" style="--c:{col}"><b>{e(lv.get(l, l))}</b><span>{e(m)}</span></div>' for l, m, col in v["risks"]) \
        or '<div class="txt dim">지금 눈에 띄는 주의 사항은 없어요.</div>'
    return ('<div class="iw"><div class="cc">' + panel("결론 (원인 후보)", left, tag="AI + 코드" if v.get("ai") else "코드 점수", tone=c, extra=fx)
            + panel("다음에 확인할 일", acts, tag="내가 직접", tone="#3DDC97", extra=fx) + "</div>"
            + '<div style="margin-top:.8rem">' + panel("주의할 점과 검증 결과", risks, tag="코드가 점검", tone="#FFB547", extra=fx) + "</div></div>")


# ---------- 에이전트 상태 ----------
def agent_row(name, role, status, detail, color):
    return (f'<div class="agent"><span class="chip" style="color:{color};border-color:{color}66;background:{color}14">{e(status)}</span>'
            f'<div><b>{e(name)}</b><div class="dim" style="font-size:.74rem">{e(role)}</div></div>'
            f'<span class="mono dim" style="font-size:.7rem;text-align:right">{e(detail)}</span></div>')
