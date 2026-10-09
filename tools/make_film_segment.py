"""把冻结素材剪成成片里的**产品两段**（镜 4 / 镜 8）—— 剪点由**像素**定，不由人猜。

为什么要有这个工具
------------------
1) 项目纪律：手工在时间线上拖出来的东西**不可复造、不可复核**。镜 4 / 镜 8 从"PPT 动效"改成
   "产品实拍"（`loreal-video-script.md` 8.3 的路线 A+B），这就是一次真正的剪辑动作 ——
   必须落成脚本，否则下一轮重录素材后没人知道当初掐在哪一帧。
2) 2026-09-22 那份《掐点表》是**人眼看抽帧拼图量出来的**（读数 D3 前 25s 是纯等待）。
   人工读数会漂。本工具改成让 ffmpeg 的**场景检测**给剪点：四镜各只有**一次**大场景变化，
   正是"结果上屏"那一刻（实测 D3@26.4s / D4@13.27s / D1,D2@2.87s，阈值 0.01/0.03/0.05 三者同值）。
   ⇒ 剪点 = 像素变化的产物，不是常量。素材重录后剪点自动跟着走；若某镜出现**不止一次**变化
   （典型的废片特征：录制期间别人的窗口压上来了），工具**拒绝出片**而不是猜。
3) 每出一段都当场自证，判据与 A/B/C/D 录屏同源（`tools/record_all_demo.py`）：
   ① 几何/时长/帧数  ② **覆盖配音**（段长 ≥ 该镜实测语音）  ③ 每片**尾帧**经百炼 VL 逐字读回后
   与承诺串机械比对（卡片也读 —— 这条同时证明中日韩字形真的烧进画面、不是豆腐块）
   ④ **段首帧 = 源片 onset 帧**（SSIM）——"掐头掐在最紧处"的机械证明，不是"我觉得挺紧了"。
   读不回来就把 mp4 挪进 `output/film/_rejected/` 并以非 0 退出：废片不许冒充成片。

⚠️ 本工具只做**产品两段**。镜 1/2/3/9/10 的画面（PPT 动效 / 实拍）目前**不存在**，
   镜 5/6/7 是终端录屏（已有素材，尚未纳入）—— 这里不假装能拼出整片。

用法
----
    cd /d/LorealGuard && env -u PYTHONPATH .venv/Scripts/python.exe tools/make_film_segment.py
    （只做一段：--only S04 ；不重剪只复核：--verify-only）
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voiceguard.semantic.vl import ask_vl  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))       # 同级工具模块：无源片段（plate）的逐帧绘制器
import plate_render as PR  # noqa: E402

EV = ROOT / "output" / "video_evidence"
FILM = ROOT / "output" / "film"
REJECTED = FILM / "_rejected"
CARDS = FILM / "_cards"

#: 成片导出规格（`loreal-video-script.md` 第六节）：1920×1080 / 30fps。
W, H, FPS = 1920, 1080, 30

#: 配音目录（`voice/make_voice.py` 的产物）。对位判据要用它，不读就没有"装得下"这句话。
VOICE = pathlib.Path("D:/deliver/compete/voice/out")

#: 场景检测阈值。0.01/0.03/0.05 在四镜上给出**同一个** onset（2026-09-22 实测），
#: 所以这个数不敏感；这里写中间值。
SCENE_THRESHOLD = 0.03

#: drawtext 的字体。Windows 下路径里的冒号在 filter 语法里是分隔符，
#: **只有 `'C\:/…'`（单引号 + 反斜杠转义）这一种写法能过**（四种写法里实测唯一可行）。
FONT = "'C\\:/Windows/Fonts/msyh.ttc'"

#: 镜 5 旁注里那个 `✅` 专用的第二个字体。**为什么非拆成两次 drawtext 不可**（2026-09-26 实测）：
#: `✅`（U+2705）在 `msyh.ttc` 里**没有字形**，drawtext 把它画成一个空心豆腐块 □ ——
#: 而这一镜那行字的全部意义就是「达标」的那个勾。
#: 候选的 6 个中文字体（msyh / msyhbd / simsun / simhei / Deng / msjh）把 `✅✔✓`
#: 一律画成**空白**（连豆腐块都没有）；本机只有 `seguisym.ttf` 有这个字形。
#: `fontfile=` 不走字体回退，所以只能拆：中文走 FONT，勾走这个。
FONT_CHECK = "'C\\:/Windows/Fonts/seguisym.ttf'"

#: 旁注版式（只用于镜 5）。下面是**版式取值**（人为选的，不是量出来的结论）；
#: 量出来的结论一律在 `verify()` 里，且写成会红的判据。
OVERLAY_SIZE = 22        # 「小字」：1920 宽下 22px，够读、不抢终端正文
OVERLAY_MARGIN = 40      # 右缘留白
OVERLAY_ZONE = 44        # 第 1 行给 ✅ 预留的横向预算（勾实测宽 ~22px，留一倍余量）
OVERLAY_TOP = 660        # 第 1 行的 y：与终端 ①~⑥ 汇总块同高，眼睛能把两处连起来
OVERLAY_LEADING = 34     # 行距
#: 旁注**只准落在这条线以右**。来历：终端正文最右一个亮像素在源片 x=935 ⇒ 成片 x≈1138。
#: 把「不遮住终端文字」变成一条可判的边界，而不是一句承诺 ——
#: verify 里两个方向都判：线左必须无墨（挡住了就红）、线右必须有墨（没画上也红）。
OVERLAY_MIN_X = 1150

QUESTION = (
    "这是一张视频截图的画面。请**逐字抄写**画面上的文字，"
    "不要解释、不要总结，尤其要准确抄下所有数字和百分号。看不清的字写『?』。"
)


def ok_line(checks: list[str]) -> str:
    """段的成功行。**只列真正跑过的判据** —— 把没跑的判据写进成功行就是假通过。

    抽成函数是为了让 `--self-check-utf8` 与真实路径用**同一份字符串**：
    否则探针打印的和真跑打印的不是一句话，守卫就白守了。
    """
    return f"    ✅ 本轮实跑判据全部通过：{' / '.join(checks)}"


def _utf8_stdout() -> None:
    """把 stdout/stderr 钉成 UTF-8 —— 本工具的成功行带 `✅`，不钉就**自己崩**。

    ⚠️ 这是 `docs/DEBT.md` D-07 那类 bug 的**第六起**，而且中在当天刚写完的这个工具上：
    Windows 上 stdout 不是终端（`> output/x.txt`）时按 locale（本机 GBK）编码，
    打印 `✅`（U+2705）抛 `UnicodeEncodeError` → **exit 1**。
    最坏的地方不是崩，是**崩在最后一行**：前面四项判据全过、mp4 已经落盘，
    却报非 0 退出 —— 看的人会以为**片子剪坏了**，去重剪一段本来没问题的片子。
    与 `tools/check_number_consistency.py:128` 的 `_utf8_stdout()` 同一个修法。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass          # 已被包成非 TextIOWrapper：不拦，继续跑

# 每段的 `pieces` 按顺序拼；`kind="clip"` 是素材，`kind="card"` 是补时值的大字卡。
#   trim="keep"  → 整段用（前缀**故意保留**，理由写在 why 里）
#   trim="onset" → 从场景检测出的"结果上屏"那一帧起用到片尾
# `expect` 是该片**尾帧**上必须读到的串（卡片则是卡片文字本身）。
SEGMENTS = [
    dict(
        #: 槽位 21.0 ＝ 台本 `loreal-video-script.md:45` 的裁定值。**当前**配音 20.86s
        #: （云希 +12%，`voice/out/shot4_zh-CN-YunxiNeural.mp3`）⇒ 余量 +0.14s，仍是全片最薄的一镜 ——
        #: 台本 §8.2 已认过这笔账（「全是换气量，没有可挪的整秒」）。历史：云扬 +4% 时 20.952s / +0.048s。
        #: ⚠️ 2026-09-23 教训（**留在这里，不要重犯**）：本槽位一度被改成 22.0，理由是
        #: 「配音 21.792s 装不下」。那个 21.792s 是**错的** —— 重跑配音时漏了
        #: `--rate=+4%`，用默认 +0% 合成的产物。用 +4% 重出后镜 4 = 20.952s，
        #: 与 22:06 那份正确产物**逐字节相同**（`output/_voice_all_4pct.txt`）。
        #: **判「装不下」之前，先核对配音是不是按台本裁定的语速合成的。**
        tag="S04", out="seg_shot04_product.mp4", shot=4, slot=21.0,
        narration="shot4_zh-CN-YunxiNeural.mp3",
        why="镜 4 槽位 21s：把「三种内容各一套尺子」从 PPT 动效换成**真产品跑出来的**图+文案与图+评论",
        pieces=[
            dict(kind="clip", src="recD1_page_text.mp4", trim="keep",
                 why="填单页那段正是「入口＝图 + 文案」，即赛题要的交互流程",
                 expect=["关注级", "可疑证据", "免责", "1a9f812fd314"]),
            dict(kind="clip", src="recD2_page_comments.mp4", trim="keep",
                 why="入口换成评论线程，同一张图另一个等级",
                 expect=["高风险", "可疑证据", "1a9f812fd314"]),
            dict(kind="card", dur="fill", title="同一张图 · 两种入口",
                 sub="物证指纹逐位相同",
                 expect=["同一张图", "物证指纹逐位相同"]),
        ],
    ),
    dict(
        #: 槽位 18.0 ＝ round19b 任务单给的裁定值（台本 `:78-80` 那一镜）。
        #: 源片实测 18.600s / 279 帧 @15fps ⇒ 30fps 下 **558 帧**，比槽位多 18 帧（0.6s）。
        #: 多出来的**从尾部去掉**：片尾是留白（"command finished…" 之后到录屏结束），
        #: 不是有效画面 —— 台本明令不加速、不 onset，所以只能砍尾巴（`cap="slot"`）。
        tag="S05", out="seg_shot05_bench.mp4", shot=5, slot=18.0,
        narration="shot5_zh-CN-YunxiNeural.mp3",
        why="镜 5 槽位 18s：裁判集跑分实况（①~⑥ 汇总块上屏）—— 整段照收，唯一的后期是右侧两行旁注",
        pieces=[
            dict(kind="clip", src="recA_p2_bench.mp4", trim="keep", cap="slot",
                 #: 旁注**不是装饰**：这一屏是「自造集 + 白盒对抗条件」下跑出来的，
                 #: 只把 PASS 行放出去、不写口径，就是把对照组藏起来 —— 台本 `:78-80` 要求同时给出。
                 #: 第 1 行白字（与终端同为中性色，只作标注）；第 2 行**琥珀**：口径 + 对抗条件。
                 #: 颜色写 `0x` 前缀而不是 `#`：drawtext 的 `fontcolor` 收 `#RRGGBB` 也收
                 #: `0xRRGGBB`，但 `#` 在 filtergraph 里是注释起始符，**写成 `#D98A00` 会被
                 #: 当注释吃掉**（这个坑不值得现场试，直接用 `0x`）。
                 overlay=dict(
                     lines=[
                         dict(spans=[
                             dict(text="目标 <10%", font="cjk", color="white"),
                             #: 勾单独一次 drawtext：msyh 没有 U+2705 的字形（见 FONT_CHECK）
                             dict(text="✅", font="check", color="white"),
                         ]),
                         dict(spans=[
                             dict(text="自造集口径 · 对抗条件见镜 9：白盒陷害侧误报 69.7%、红线被击穿 3 条",
                                  font="cjk", color="0xD98A00"),
                         ]),
                     ],
                     #: **全片常驻**（`drawtext` 不写 `enable=` 即恒真）—— 台本 `:78-80` 要的是
                     #: 「PASS 行与口径同时在屏」，常驻已满足该要求；"到 PASS 行才出现"要按像素
                     #: 找那一帧，不值当（2026-09-26 裁定：不写像素搜索）。
                     #: 落位＝**贴右侧**。依据（2026-09-26 实测，只看 mm:ss 抽帧不够 —— 常驻
                     #: 就得整片量）：终端正文在该横带（成片 y=649~734）内最右墨点到成片 x=793
                     #: （整片 279 帧逐帧量的），旁注左缘约 1143 ⇒ 净空 ~350px；最右那条滚动条
                     #: 在成片 x≈1911，也不挡。所以不放底部。
                 ),
                 expect=["PASS", "60/60"]),
        ],
    ),
    dict(
        #: 槽位 11.0 ＝ round20a 任务单给的裁定值（台本 `:86-87` 那一镜）。
        #: 素材 recB_pack.mp4 实测 1578×888 / 15fps / **101 帧** / 6.733333s
        #: ⇒ 30fps 折算 **202 帧**；槽位 11.0s = 330 帧 ⇒ 余量 **128 帧**（4.266667s）。
        #: 这 128 帧由 **`kind="hold"` 定格片**填：复制素材末帧铺满，**不插黑、不循环源片、
        #: 不造新帧**（赛题要的是"跑完的终端停在结果上"，不是一段新画面）。用定格而不是
        #: 加速/裁短：加速会改终端里那些数字的可读性，裁短会让 sha 那几行来不及看
        #: —— 这与镜 5 的 `cap="slot"` 是同一类判断，只是那一镜的余量是负的。
        #: ⚠️ 台本 `:86-87` 还要的**印章小图与哈希对齐线不在本轮**（下一轮加），
        #: 本轮也不加旁注 —— 这一镜的判据是几何/时长/帧数 + 定格逐像素。
        tag="S06", out="seg_shot06_pack.mp4", shot=6, slot=11.0,
        narration="shot6_zh-CN-YunxiNeural.mp3",
        #: ⚠️ **本片唯一声明无损的段**（2026-09-26 实测后加）：定格段的判据是"每一帧与上一片
        #: 末帧**逐像素相同（差异 0）**"，而 `-crf 18` 下它**不可能为 0** —— 实测片界差 132 个
        #: 像素（上一片末帧的块有残差、去块滤波 bS=2；定格整帧 skip、bS=0），定格段内部从流
        #: 第 250 帧（x264 默认 `keyint=250` 的 IDR，I 帧质量高于 P 帧）起整段跳一档、差
        #: 186 191 个像素。这与定格机制无关（换 `tpad`/`clone` 一样），是**有损编码**的固有性质。
        #: `-qp 0` 下实测三名采样帧全为 0 差异、连第 250 帧那处也是 0。代价 224 267 B →
        #: 416 267 B（+86%，仍 < 0.5 MB），且换来的是**画质上升**，不是下降。
        #: `encode` 缺省仍是 `crf18`：S04/S05/S08 的 ffmpeg 命令行**一个字都没变**。
        encode="lossless",
        why="镜 6 槽位 11s：提交包导出的实况（sha256 / 文件数 / 字节数），末尾定格在结果上",
        pieces=[
            #: `expect` 取自**素材末帧上的实际像素**（先把那一帧抽出来看过，再照着写），
            #: 不是照着台本抄：`7eaa8ac3` 正是 v4 黄金包（`LorealGuard_testset_v4.zip`）的
            #: sha256 前缀 —— 这一段同时是
            #: "录像里那个 sha 与仓库口的黄金 sha 是同一个"的可核证据。
            dict(kind="clip", src="recB_pack.mp4", trim="keep",
                 why="整段照收：包导出的全过程，末帧停在 sha256 与文件数上（唯一的后期是定格补时）",
                 expect=["7eaa8ac3", "包内文件数", "228", "SHA256SUMS"]),
            dict(kind="hold", dur="rest",
                 why="余量 128 帧用**末帧定格**铺满：让 sha / 文件数 / 字节数在屏上停住够读",
                 #: 没有 `expect`：定格片的判据是**逐像素**（与上一片末帧差 0 个像素），
                 #: 不是 VL 逐字读回 —— 它是上一片末帧的复制，读回的是同一张图，
                 #: 不产生新信息（verify 里对 hold 明确走 ⑤，不假装跑过 VL）。
                 ),
        ],
    ),
    dict(
        #: 槽位 16.0 ＝ round20b 任务单给的裁定值（台本里镜 7 那一镜）。
        #: 素材 recC_abstain.mp4 实测 1578×888 / 15fps / **93 帧** / 6.200000s
        #: ⇒ 30fps 折算 **186 帧**；槽位 16.0s = **480 帧** ⇒ 余量 **294 帧 = 9.800000s**。
        #: 这 294 帧由 **`kind="hold"` 定格片**填（与 S06 同一种填法：复制素材末帧铺满，
        #: 不插黑、不循环源片、不造新帧）；**帧数由规划器算，这里不写死 294**。
        #: ⚠️ 已知质量风险（**本轮只登记、不解决**）：294 帧定格 ＝ **9.8s 死画面**，占该镜约
        #: 三分之一。成因是录制器按标题判活、命令跑完就停录 ⇒ 素材只有 6.2s。处置已由用户
        #: 决定（可能重录 recC 到 16s 真画面），届时**只需重跑同一条命令** —— 本轮不许为此
        #: 改槽位、改素材、或发明任何自适应填充。
        #: ⚠️ 台本里这一镜的**五行高亮不在本轮**（下一轮加）；本轮也不加卡片、不加旁注。
        tag="S07", out="seg_shot07_abstain.mp4", shot=7, slot=16.0,
        narration="shot7_zh-CN-YunxiNeural.mp3",
        #: 与 S06 同理、且**同样只声明在这一段上**：定格段与上一片末帧在上游是逐字节相同的
        #: 输入，但 `-crf 18` 出来不逐像素相同（片界差 132 像素 / keyint=250 那处差 18 万，
        #: 见 S06 与 `build` 里的实测记录）⇒ 定格判据要求「差异 = 0」时**必须** `-qp 0`。
        #: `crf18` 段（S01/S04/S05/S06/S08）的 ffmpeg 命令行一个字都没变。
        encode="lossless",
        why="镜 7 槽位 16s：弃权分解表（良性侧=护栏的代价 / 恶意侧=漏报的构成），末尾定格把两类弃权停在屏上读完",
        pieces=[
            #: `expect` 取自**素材末帧上的实际像素**（先把那一帧抽出来看过，再照着写 —— 同 S06），
            #: 不是照着台本抄：末帧停在恶意侧那两行，`PLATFORM_RECODE` / `NO_ANOMALY_REGION`
            #: 正是「良性侧弃权 = 没冤枉它 / 无物证不出结论」这两个设计内归因的码。
            dict(kind="clip", src="recC_abstain.mp4", trim="keep",
                 why="整段照收：弃权分解表逐行上屏，末帧停在两行弃权计数上（唯一的后期是定格补时）",
                 expect=["弃权", "PLATFORM_RECODE", "NO_ANOMALY_REGION"]),
            dict(kind="hold", dur="rest",
                 why="余量 294 帧用**末帧定格**铺满：让「良性侧 38/40 · 恶意侧 5/20」这两行在屏上停住够读",
                 #: 没有 `expect`（同 S06）：定格片的判据是**逐像素**（与上一片末帧差 0 个像素），
                 #: 不是 VL 逐字读回 —— 它读回的是同一张图，不产生新信息。
                 ),
        ],
    ),
    dict(
        tag="S08", out="seg_shot08_product.mp4", shot=8, slot=17.0,
        narration="shot8_zh-CN-YunxiNeural.mp3",
        why="镜 8 槽位 17s：路线 B —— 用 D3/D4（开源模型说人话 + 拔网线）替掉「数字墙」",
        pieces=[
            dict(kind="clip", src="recD3_page_f3.mp4", trim="onset",
                 why="勾 F3：开源权重档把**算法已定位**的疑点翻成人话",
                 expect=["状态", "qwen3.8-27b", "Apache-2.0"]),
            dict(kind="clip", src="recD4_page_offline.mp4", trim="onset",
                 why="模型出口掐断：账本照出、等级照出",
                 expect=["状态", "error", "APIConnectionError"]),
            dict(kind="card", dur="fill", title="判定链 10 字段逐字段相同",
                 sub="勾 F3 前后 · 解释不进判定",
                 expect=["10 字段", "逐字段相同"]),
            dict(kind="card", dur="fill", title="模型够不到，账本照出",
                 sub="状态 error · 等级与证据不变",
                 expect=["模型够不到", "账本照出"]),
        ],
    ),
    dict(
        #: 槽位 13.0 ＝ 台本 §8.5 ② 的**裁定值**（`loreal-video-script.md`），不是可调参数。
        #: 素材 recD5_page_observe.mp4 实测 1578×844 / 15fps / **217 帧** / 14.466667s
        #: （＝ 217/15，逐位核过；2026-10-03 换界面后重录，旧源片是 1578×888 / 209 帧 / 13.933333s）。
        #: ⚠️ 本镜的剪法与前七镜都不同：**砍头**（`trim="from_frame"`），不是 keep、不是 onset。
        #: 为什么砍头：源片 14.466667s 比槽位 13.000s 多 1.466667s ＝ 15fps 下正好 22 帧；
        #: 台本明令这一镜**不加速、不改剪点性质**（加速会改那些读数的可读性，与镜 5 的
        #: `cap="slot"` 是同一类判断），所以只能从**头**去掉这 22 帧。
        #: 去掉的是哪一段（已抽头帧/末帧看过，不是"没核过"的说法）：**观察栏结果上屏之前**
        #: 那段表单页（「① 核验一条内容」的入口屏）。2026-10-03 重测 `SCENE_THRESHOLD=0.03`：
        #: 两处大变化在 3.266667s（结果上屏）与 7.266667s（摇镜硬切）—— **都在保留区内**。
        #: 砍 22 帧 ⇒ 保留源片第 22..216 帧（195 帧）⇒ `fps=30` 数出 **390 帧**
        #: ＝ 13.0×30 ⇒ **余量恰好为 0** ⇒ 本镜**不需要定格片、也不需要卡片**
        #: （`plan()` 里"没有卡片吸收余量"那条分支要求 Σ片长**逐帧等于**槽位，正是它的守卫）。
        #: ⚠️ 22 这个数**不是拍的**：2026-10-03 换了源片（15 fps / 217 帧 / 14.466667 s）后按
        #: 「30 fps 时间轴上砍掉多出的 44 帧 ＝ 22 源帧」算出，`verify` 当场复核恰好 390 帧。
        tag="S11", out="seg_shot11_observe_d5.mp4", shot=11, slot=13.0,
        #: **本镜无配音** —— 这是**显式**声明（`None`），不是漏写：本镜是追加块，
        #: 不进台本那个 179s 版，「配音合计 174.2s」那笔账里没有它。
        #: `verify` 见到 `None` 就**不跑**覆盖配音判据、也不把它写进成功行
        #: （"只列真正跑过的判据"）；而**写成漏键**是红的 —— 见 verify 里的判据。
        narration=None,
        why="镜 11 槽位 13s：勾上观察栏后 D5 页面实况（附四逐字段的真实数字）—— 按台本口径只用原始录屏，不加卡片、不加旁注",
        pieces=[
            #: `expect` 取自**末帧上的实际像素**（2026-10-03 换源片后重抽末帧看过、逐字照着写 —— 同 S06/S07），
            #: 不是照着台本抄：末帧停在 `lighting` 那张观察维度表上（`观察维度 lighting（ok）· 分值 0.6585`），
            #: `0.6585` / `177.58` 正是「观察栏给了真实数字」这句话的可核证据（这一镜的全部意义在这里）。
            dict(kind="clip", src="recD5_page_observe.mp4", trim="from_frame", from_frame=22,
                 why="砍头 22 帧：源片比槽位多出的 1.466667s 正是开头那段（观察栏结果上屏前的表单页），掐掉后正好 13.000000s",
                 expect=["lighting", "0.6585", "theta_main_deg", "177.58"]),
        ],
    ),
    dict(
        #: 槽位 19.0 ＝ round20f 任务单给的裁定值（台本里镜 2 那一镜）。
        #: ⚠️ 本段**没有录屏源片** —— 台本给的是画面意图 + 口播，没有可剪的素材，
        #: 所以整段由 `tools/plate_render.py` 按声明表**逐帧画**出来（`kind="plate"`，
        #: 画面与时刻表在 `plate_render.PLATE_SPECS["s02"]`）：
        #:   左半屏＝真实种草图（`samples/base_neutral.jpg`，**唯一的图像素材**、合成样图）
        #:           被画上红叉；右半屏＝创作者头像**剪影**（纯几何，无任何真人照片）
        #:           被画上红叉；两把叉**先后落下**（不是同时出现）；
        #:   底部「误伤补不回来」六个字**单独放大 0.4s 后回落**。
        #: 帧数 570 写死在 plate 声明表里（＝19.0×30），`plan` 把整个槽位给它（`dur="rest"`，
        #: 与定格片同一套算术）；plate 的时刻表是按**整段帧号**写死的常量 ⇒ 它必须是
        #: **独占的一段**，与别人的帧排在一起会让每个声明时刻平移成假的（`plan` 会拒排）。
        tag="S02", out="seg_shot02_creator.mp4", shot=2, slot=19.0,
        #: 配音 17.470s（`voice/out/shot2_zh-CN-YunxiNeural.mp3`，云希 **+12%** 语速）
        #: ≤ 槽位 19.0s ⇒ 余量 **+1.530s**。这个数由 `verify` 的「覆盖配音」当场量，不写死在这里。
        narration="shot2_zh-CN-YunxiNeural.mp3",
        why="镜 2 槽位 19s：守护对象定成创作者（两把红叉先后落下 + 六个字放大回落）—— 无源片段，整段由 plate 画满",
        pieces=[
            dict(kind="plate", plate="s02", dur="rest",
                 why="整段 570 帧由 plate_render 逐帧画（帧号是唯一时间源；同一 (pid, frames) 两次绘制逐字节相同）",
                 #: 尾帧读回：这一屏上有三处文字 —— 底部那六个字（放大后已回落成常宽），
                 #: 以及左右两半的两条小字标注。**红叉本身读不回来**（VL 只读文字），
                 #: 所以「两把叉先后落下」由 plate 的像素判据（steps red / blob red）证，
                 #: 不由尾帧读回证 —— 两条判据各证各的，都进成功行。
                 #: ⚠️ `expect` 只钉**实测读得回来**的两处（2026-09-26 首次真跑实测）：
                 #: 底部大字读回「误伤补不回来」逐字正确，左半屏标注读回「种草图（合成样图）」
                 #: 逐字正确；右半屏那条 **30px 灰字读不全**（读回「创作?头像（?影）」）——
                 #: 它是 MUTE 灰的 30px 小字，把「读不全」钉进 `expect` 就是**掷硬币式判红**
                 #: （那是误报，不是判据）。右半屏"是什么"由画面本身与 plate 的零红/两叉判据证，
                 #: 这一条只证"中文与那张合成样图真的烧进了成片"。
                 expect=["误伤补不回来", "种草图"]),
        ],
    ),
    dict(
        #: 槽位 19.0 ＝ round20g 任务单给的裁定值（台本里镜 3 那一镜）。
        #: ⚠️ 本段与 S02 同一种片：**没有录屏源片** —— 台本给的是画面意图 + 口播，
        #: 所以整段由 `tools/plate_render.py` 按声明表逐帧画（`kind="plate"`，
        #: 画面与时刻表在 `plate_render.PLATE_SPECS["s03"]`）：
        #:   开场 0.0–2.2s 叠一行**静音大字**「我们不判真假 —— 我们出一本可复算的账」
        #:   （不进口播、不动时间轴，帧号 0..65）；
        #:   三张卡片**逐张推入**（不同时出现，落点相隔 96 帧），每张右侧各出一个小标，
        #:   顺序＝`可复跑` / `可读成话` / `可追溯`；
        #:   三条关键词**逐条上屏、每条停留 1.2s**（36 帧），逐条之间留 6 帧间隔
        #:   ⇒ 像素上是三段可分辨的窗口，而不是一段连续的亮。
        #: 帧数 570 写死在 plate 声明表里（＝19.0×30），`plan` 把整个槽位给它（`dur="rest"`）。
        tag="S03", out="seg_shot03_answer.mp4", shot=3, slot=19.0,
        #: 配音 18.500s（`voice/out/shot3_zh-CN-YunxiNeural.mp3`，云希 **+12%** 语速）
        #: ≤ 槽位 19.0s ⇒ 余量 **+0.500s**。这个数由 `verify` 的「覆盖配音」当场量，不写死在这里。
        narration="shot3_zh-CN-YunxiNeural.mp3",
        why="镜 3 槽位 19s：三条不对称（三张卡片逐张推入 + 三条关键词各停留 1.2s）—— 无源片段，整段由 plate 画满",
        pieces=[
            dict(kind="plate", plate="s03", dur="rest",
                 why="整段 570 帧由 plate_render 逐帧画（帧号是唯一时间源；同一 (pid, frames) 两次绘制逐字节相同）",
                 #: 尾帧读回：片尾那一屏只有三张卡（大字与关键词都已按时退场），
                 #: 所以 `expect` 钉的就是三个卡片标题 —— 它是「中文真的烧进成片」的证据。
                 #: ⚠️ 中卡那条**只钉可读回的前缀**「解释可读成」（2026-09-26 首次真跑实测）：
                 #:   尾帧读回稳定是「解释可读成语」/ 小标稳定是「可读成语」—— 第 6 个字
                 #:   `话` 被读成 `语`。**画面上的字是对的**：`crop_title2.png` / `crop_tag2.png`
                 #:   是同一帧的放大裁切，逐笔是 `讠+舌`，不是 `讠+吾`；VL 读错的是它，不是我们。
                 #:   钉「读不全的那一段」＝**掷硬币式判红**（那是误报，不是判据），
                 #:   换句法（改字、改字号、改位置）＝**为让样本过关动画面**，两条都不许。
                 #:   钉进去的必须是**真话**：`解释可读成` 是画面上真有的 5 个字，
                 #:   错的串（`解释可读成语`）一个字都不许钉。这一条证的是
                 #:   「卡片标题的中文确实烧进了成片且未被编码糊掉」——5/6 字可读即已足够，
                 #:   第 6 个字是什么，由画面本身与 plate 的像素判据证，不由尾帧读回证。
                 #:   ✅ 已落地（2026-09-26）：`:379` 的 `expect` 中卡一条已改为 `解释可读成`，
                 #:   另两条一字未动；登记 `docs/DEBT.md` D-19。
                 #: ⚠️ 三张卡片的**文本来源**（不是照抄任务单，而是对齐 PPT 第 3 页那三张卡）：
                 #:   PPT 第 3 页＝`判定权归算法` / `解释权归语言` / `免责权归创作者`，
                 #:   本段按同一语义写成可交付口径：账本可复跑 / 解释可读成话 / 归因可追溯。
                 #: ⚠️ 为什么**没有**拿任务单点名的 `slides/slide_03.png` 当底图（这是本段唯一
                 #:   与任务单「素材」一条不重合的地方，理由是可量的、不是偏好）：
                 #:   实测那张 1920×1080 里自带 **4249 个红线像素**（脚注 (224,83,63)，
                 #:   `R>=150 且 G<=90 且 B<=90` 判色 proxy 之下）⇒ 用它做底图，
                 #:   「全段红色像素 = 0」当场不成立（交判据 ④ 是一条平的判据，没有豁免）。
                 #:   另有 `plate_render` 的**不变量 2**：进入画面的图像素材只允许
                 #:   `samples/base_neutral.jpg`（合成样图）。两条各自都足以否掉那个底图。
                 #:   故三张卡片与那张卡的内容一律**自绘**（白/琥珀/灰，零红）。
                 expect=["账本可复跑", "解释可读成", "归因可追溯"]),
        ],
    ),
    dict(
        #: 槽位 26.0 ＝ 排片表的裁定值（`cc-dialogue/film-B_图形五镜_hermes_to_cc.md:12`）。
        #: ⚠️ 本段与 S02/S03 同一种片：**无录屏源片** —— 台本给的是画面台词 + 口播，
        #: 整段由 `tools/plate_render.py` 按声明表逐帧画（`kind="plate"`，
        #: 画面与时刻表在 `plate_render.PLATE_SPECS["s09"]`）：
        #:   五条「不做」逐条上屏（每条 5s），**讲过的转灰、正在讲的那条亮**；
        #:   末两行（字号 75 / 60，比正文小 20%）＝我们自己先捅的那一刀，含两处风险红
        #:   （`开卷考试` / `击穿 3 条`）—— 这是我们**主动加的红**，不是判据判出来的红（D-18 口径）。
        #: 帧数 780 写死在 plate 声明表里（＝26.0×30），`plan` 把整个槽位给它（`dur="rest"`）。
        #: ⚠️ 文件名以排片表为准：`seg_shot09_boundary.mp4`（早年几份派单里写成
        #: `boundaries` / `answer` 的口径已作废）。
        tag="S09", out="seg_shot09_boundary.mp4", shot=9, slot=26.0,
        #: 配音 23.930s（`voice/out/shot9_zh-CN-YunxiNeural.mp3`，云希 **+12%** 语速，`ffprobe` 当场量）
        #: ≤ 槽位 26.0s ⇒ 余量 **+2.070s**。这个数不写死在判据里：`verify` 每轮重算。
        narration="shot9_zh-CN-YunxiNeural.mp3",
        why="镜 9 槽位 26s：五条「不做」逐条上屏 + 两处风险红（我们主动加的红）—— 无源片段，整段由 plate 画满",
        pieces=[
            dict(kind="plate", plate="s09", dur="rest",
                 why="整段 780 帧由 plate_render 逐帧画（帧号是唯一时间源；同一 (pid, frames) 两次绘制逐字节相同）",
                 #: 尾帧读回：片尾那一屏是**五条全在** —— 但只有末条是亮的（前四条已转灰），
                 #: 所以 `expect` 只能钉末条 L5 上的字，且必须取**可读回的前缀**：
                 #: ⚠️ 一律避开近形易混字（D-19）：`未改阈值` 里 `未/末` 同框 —— 所以这一条
                 #: 钉到 `登记在案` 就**停住**，不把 `未` 写进判据（`话/语` 已在 S03 踩过一次：
                 #: 画面是对的，读回是错的，钉读不全的一段＝掷硬币式判红，那是误报不是判据）。
                 #: 这一条证的是「L5 那行中文确实烧进了成片且未被编码糊掉」，
                 #: 其余四条的字由 plate 自己的像素判据（逐条 windows + 两处红）证，不由读回证。
                 expect=["把规则表全文交给对手", "登记在案"]),
        ],
    ),
    dict(
        #: 槽位 16.0 ＝ 台本「**镜 10｜02:43–02:59（16s / 82 字）收尾**」。
        #: 无源片段 ⇒ 整段由 `tools/plate_render.py` 的 `PLATE_SPECS["s10"]` 逐帧画：
        #:   封面版式 ⇒ 标题淡出（0–44 帧）⇒ 剩三行字；
        #:   第 375 帧琥珀小字浮出并**定格 60 帧（2.0s）**（那 60 帧存的是同一份位图）；
        #:   末 45 帧（1.5s）黑场 —— 台本 `:132`「结尾黑场 1.5 秒」。
        #: ⚠️ §8.5 ⑥ 裁定「全片风险红预算 = 3 处，只给镜 2 与镜 9」
        #:   ⇒ 本镜第四行走**琥珀**（`AMBER (217,138,0)`，＝镜 5 旁注同色，不另造色）；
        #:   判据里那条 `zero/red` 就是"本镜一个风险红像素都不许有"。
        #: ⚠️ 台本 `:469` 提醒本镜文案含 `69.7%` 落在 D-17（`drawtext` 落单 `%`）雷区 ——
        #:   本镜走 plate（PIL 逐帧画，根本不经过 `drawtext`）⇒ `%` 只是一个普通字符。
        tag="S10", out="seg_shot10_close.mp4", shot=10, slot=16.0,
        #: 配音 15.050s（`voice/out/shot10_zh-CN-YunxiNeural.mp3`，云希 **+12%** 语速，`ffprobe` 当场量）
        #: ≤ 槽位 16.0s ⇒ 余量 **+0.950s**（当前最薄的一镜是镜 4 的 +0.14s）。
        narration="shot10_zh-CN-YunxiNeural.mp3",
        why="镜 10 收尾 16s：封面三行 + 琥珀小字（自造集口径 / 69.7%）+ 定格 2s + 黑场 1.5s —— 无源片段，整段由 plate 画满",
        pieces=[
            dict(kind="plate", plate="s10", dur="rest",
                 #: ⚠️ 片尾是黑场 ⇒ 必须指明读哪一帧（见 `verify` 里 `readback_frame` 那段注释）：
                 #: 钉在**定格段的末帧 434** —— 那一屏正是琥珀小字，也就是本镜要说的那句话。
                 #: 两行小字各取**可读回的前缀**，一律避开近形易混字（D-19：`未/末`、`话/语`）。
                 readback_frame=434,
                 expect=["白盒陷害侧误报", "红线被击穿"]),
        ],
    ),
]


# ----------------------------------------------------------------------------- 剪点


def scene_changes(clip: pathlib.Path, threshold: float = SCENE_THRESHOLD) -> list[tuple[str, float, str, str]]:
    """返回 clip 里所有「大场景变化」：`(精确 PTS, 秒, 前一帧 PTS, 后一帧 PTS)`。

    ⚠️ 为什么返回**有理数**而不是秒（2026-09-22 实测踩到）：`showinfo` 给的是
    `pts: 203776` + 上游 `time_base: 1/15360`，而 `pts_time` 只是它**四舍五入到 6 位**的样子。
    本工具后面要拿这个时刻去 `select=gte(t,...)` 选帧 —— 用秒就等于把"取整"塞回了选帧链，
    正是 `_frame` 里记的那类坑。所以剪点保留 `203776/15360` 的原样：
    ffmpeg 比的是精确有理数，第 197 帧（t=202752/15360）绝不等于它，第 198 帧必然命中。

    前后两帧的 PTS 由同一行的 `duration:`（也是 timebase 单位）加减得到 —— 于是 ④ 能做
    "onset ±1 三帧择近"，**不必知道素材的真实帧率**（D3 是 2440/163、D4 是 715/48，
    都不是 15；早先按 15 乘出来的 onset 帧号是错的）。
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(clip),
         "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")  # ffmpeg 的日志是 UTF-8，见 docs/DEBT.md D-07
    tb, hits = None, []
    for line in (r.stderr or "").splitlines():
        if "time_base:" in line and tb is None:      # `config in time_base: 1/15360, frame_rate: 15/1`
            tok = line.split("time_base:")[1].split(",")[0].strip()
            if "/" in tok:
                tb = int(tok.split("/")[1])
        if "pts:" not in line or "pts_time:" not in line or tb is None:
            continue
        try:
            pts = int(line.split("pts:")[1].split()[0].rstrip(","))
            dur = int(line.split("duration:")[1].split()[0].rstrip(","))
            sec = round(float(line.split("pts_time:")[1].split()[0].rstrip(",")), 3)
        except (ValueError, IndexError):
            continue
        hits.append((f"{pts}/{tb}", sec, f"{pts - dur}/{tb}", f"{pts + dur}/{tb}"))
    # 同一帧可能被 showinfo 的多行 echo（config 行 + frame 行），按有理数去重
    out: list[tuple[str, float, str, str]] = []
    for item in sorted(set(hits), key=lambda x: x[1]):
        if not out or out[-1][0] != item[0]:
            out.append(item)
    return out


def result_onset(clip: pathlib.Path) -> tuple[str, float, str, str]:
    """整段只有一次大变化时，那一刻就是"结果上屏"。不止一次 ⇒ 拒片（废片特征）。"""
    hits = scene_changes(clip)
    if len(hits) != 1:
        raise RuntimeError(
            f"{clip.name} 的场景变化有 {len(hits)} 次（{[s for _, s, _, _ in hits]}）—— "
            f"本工具只认「恰好一次」。多次变化通常意味着录制期间有别的窗口压上来了，"
            f"或页面在中途重绘；**不猜**，请先复核素材。")
    return hits[0]


def probe(clip: pathlib.Path) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,avg_frame_rate",
         "-show_entries", "format=duration", "-of", "default=nw=1", str(clip)],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",  # 同上，D-07
    )
    d = {}
    for line in (p.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def clip_sel(pc: dict, onsets: dict[str, tuple[str, float]]) -> str:
    """该片该从素材的哪一帧起用 —— 以 **select 表达式**表达，不以秒。

    `trim="keep"`       → 整段（空串）
    `trim="onset"`      → `gte(t,<精确 PTS 有理数>)`，即"结果上屏"那一帧起（剪点由像素测出）
    `trim="from_frame"` → `gte(n,<N>)`，即**砍头 N 帧**：源片第 N 帧（0 基）起用，
                          前面 0..N-1 共 N 帧丢掉；N 由同名的 `from_frame` 键给出。

    ⚠️ 为什么 `from_frame` 走 `n`（源片帧号）而不是 `t`（秒），与另外两种剪法不同：
    这两个剪点的**来源**不同。`onset` 是**测出来**的时刻（场景检测给的精确有理数 PTS，
    本身就是 t 的坐标系），所以用 t。`from_frame` 是**裁定值**（台本 §8.5 ② 定死总长
    13.000000s ⇒ 砍 22 帧）：拿"22 帧 @15fps"乘 1/15 得到 1.466667…，再拿这个**十进制
    近似**去和源片 PTS 比大小，就是把取整塞回了选帧链 —— 正是 `_frame` 里记的那类坑
    （`-ss` 静默丢帧）。`n` 是整数计数，在 ffmpeg 里就是"第几个进 select 的帧"，无近似。

    ⚠️ 未知 `trim` **当场红**：不许静默回落到"整段用" —— 那会让"砍了头"变成一句假话，
    而片子看起来仍然是对的（时长由槽位兜底），属于本工具最恨的那种静默。
    """
    trim = pc["trim"]
    if trim == "keep":
        return ""
    if trim == "onset":
        return f"gte(t\\,{onsets[pc['src']][0]})"
    if trim == "from_frame":
        n = pc.get("from_frame")
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            raise RuntimeError(
                f"{pc['src']} 声明了 trim=\"from_frame\"，但 from_frame={n!r} 不是一个正整数 —— "
                f"砍 0 帧或负数帧＝不砍头（那该写 trim=\"keep\"）；键名写错不许静默按整段出片。")
        return f"gte(n\\,{n})"
    raise RuntimeError(
        f"{pc['src']} 的 trim={trim!r} 不认识（只认 keep / onset / from_frame）—— "
        f"未知剪法不许静默回落成「整段用」：那样出来的是**没砍头的**片子，"
        f"而「砍了头」这句话会变成没有依据的假话。")


def clip_chain(sel: str, frames: int | None = None) -> str:
    """素材片的滤镜串 —— **数帧与出片共用这一份**，两处不允许各写一份。

    为什么必须共用：`_clip_len` 数出来的帧数会被写进 `trim=end_frame`。若"数"和"剪"
    用的串差一个字，数出来的数就不是剪出来的数，"帧数对得上"这句话立刻失去依据。
    实测踩过两次：① 漏 `select=` 前缀 → ffmpeg 报 `No such filter: 'gte(t,…)'`；
    ② keep 片（`sel=""`）时只顾拼前缀、把 `fps=/scale=` 丢了 → `-vf` 成空串。

    `frames=None` ⇒ 不卡尾、不重置 PTS —— 那是**数帧**用的形态（只有这里的帧数才是真帧数）。
    ⚠️ `select=` 前缀不能少：`gte(t,…)` 是**表达式**，`select` 才是滤镜名。
    """
    parts = ([f"select={sel}"] if sel else []) + [f"fps={FPS}", f"scale={W}:{H}:flags=lanczos"]
    if frames is not None:
        parts += [f"trim=end_frame={frames}", "setpts=PTS-STARTPTS"]
    parts.append("setsar=1")
    return ",".join(parts)


def _clip_len(src: pathlib.Path, sel: str) -> int:
    """数出「按 `sel` 截取、再 `fps=30` + 缩放后」ffmpeg **真正会生成多少帧**。

    ⚠️ 为什么必须**数**而不是**算**（2026-09-22 实测）：
    原先用 `时长 × 30` 算，D4 算出 178，ffmpeg 实际只给 **176** —— 因为
    `fps=30` 生成的帧落在 `ceil(起点×30) .. floor(末帧PTS×30)`，而素材帧率
    并不是 15（D3 是 2440/163、D4 是 715/48），这个差不是常数，算不出来。
    数字少 2 帧的后果不是"看不出来"：整段短 2 帧 → **尾帧读回抽到不存在的帧号**（实测崩在 509）。
    所以帧数由 ffmpeg 数一遍给出，卡片去吸收余量。
    """
    vf = clip_chain(sel)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-vf", vf,
                        "-an", "-f", "null", "-"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    nums = re.findall(r"frame=\s*(\d+)", r.stderr or "")
    if not nums and r.returncode != 0:
        raise RuntimeError(f"数帧失败（{src.name}）：{(r.stderr or '').strip()[:200]}")
    if not nums:
        raise RuntimeError(f"数帧拿不到 frame= 计数（{src.name}）—— ffmpeg 的日志格式变了？")
    return int(nums[-1])


# ----------------------------------------------------------------------------- 排片


def plan(seg: dict, onsets: dict[str, tuple[str, float]]) -> list[dict]:
    """把一段排成**帧数**：素材片吃自己被数出来的帧数（`cap="slot"` 的片砍到装得下），
    定格片（`kind="hold"`）吃掉**全部余量**，`fill` 卡片平分余下的（余数给最后一张）；
    **没有卡片也没有定格片时 Σ片长必须正好等于槽位**。

    每段排完必须正好等于槽位，否则"装进槽位"这句话是假的 —— 这条由 `build` 末尾的
    `trim=end_frame=<槽位帧数>` 兜底，并由 `verify` 独立复核。
    """
    slot = int(round(seg["slot"] * FPS))
    out = []
    for pc in seg["pieces"]:
        if pc["kind"] == "clip":
            sel = clip_sel(pc, onsets)
            frames = _clip_len(EV / pc["src"], sel)
            # `trim="from_frame"` 与 `cap="slot"` 遵循同一条规矩：**声明过的动作必须有一个
            # 当场可读的数**。cap 那边印"从尾部砍了多少帧"，这边印"从头部丢了多少帧、
            # 还剩多少"。砍头量本身是裁定值、不做"择近"，但**砍到不剩**是结构性的不成立
            # （不是阈值问题），必须在这里拒排，而不是让后面 Σ片长 去背一个看不懂的账。
            if pc["trim"] == "from_frame":
                src_n = int(probe(EV / pc["src"]).get("nb_frames", 0) or 0)
                head = pc["from_frame"]
                if src_n and head >= src_n:
                    raise RuntimeError(
                        f"{seg['tag']}：{pc['src']} 一共 {src_n} 帧，却声明砍头 {head} 帧 —— "
                        f"砍完一帧不剩（不是阈值问题，是排片不成立：该换素材或该缩槽位）。")
                print(f"    from_frame：{pc['src']} 共 {src_n or '?'} 帧 → 丢前 {head} 帧，"
                      f"从源片第 {head} 帧（0 基）起用 ⇒ 数出 {frames} 帧")
            out.append({**pc, "sel": sel, "frames": frames})
        else:
            out.append({**pc, "sel": "", "frames": 0})

    # `cap="slot"`：这一片**没有可用的剪点**（台本明令不加速、不 onset —— 加速会改
    # 终端里那些数字的可读性，onset 会丢掉开工头），只好从**尾部**砍到装得下。
    # 砍多少由「槽位 − 其余片的帧数」决定，**不由任何阈值决定**（百分比 / 秒数阈值
    # 一律不许 —— 铁律 2：不为让样本过关挪口径）。砍掉的帧数**必须打印**：
    # 这个数就是"cap 到底生效没有"的判据本身，砍 ≤0 帧说明声明是空的，直接红。
    for pc in out:
        if pc.get("cap") != "slot":
            continue
        others = sum(p["frames"] for p in out if p is not pc)
        budget = slot - others
        if budget <= 0:
            raise RuntimeError(
                f"{seg['tag']}：{pc['src']} 声明 cap=\"slot\"，但其余片已占 "
                f"{others} 帧 ≥ 槽位 {slot} 帧 —— 这一片没有可砍的空间，"
                f"排片本身不成立（不是阈值问题）。")
        kept = min(pc["frames"], budget)
        cut = pc["frames"] - kept
        print(f"    cap=slot：{pc['src']} 数出 {pc['frames']} 帧 → 取前 {kept} 帧"
              f"（从尾部砍 {cut} 帧 = {cut / FPS:.3f}s；"
              f"槽位 {slot} 帧 − 其余片 {others} 帧 = 预算 {budget} 帧）")
        if cut <= 0:
            raise RuntimeError(
                f"{seg['tag']}：{pc['src']} 声明了 cap=\"slot\" 却一帧都没砍到"
                f"（数出 {pc['frames']} 帧 ≤ 预算 {budget} 帧）—— 这个键没生效："
                f"要么素材已经不溢出槽位（那就不该声明 cap），要么它根本没被读到。")
        pc["frames"] = kept

    # `kind="hold"`：**定格补时**片 —— 复制上一片（素材片）的最后一帧填满余量。
    # 帧数由「槽位 − 素材」决定，与 `cap="slot"` 是同一套算术、方向相反；**不由阈值决定**。
    # 三种"余量吸收者"（素材片/定格片/卡片）里，一段只准有**一个**非素材的：
    # 两个各分一半的余量没有依据，宁可当场拒排。
    holds = [(i, p) for i, p in enumerate(out) if p["kind"] == "hold"]
    fills = [p for p in out if p["kind"] == "card"]
    if len(holds) > 1:
        raise RuntimeError(
            f"{seg['tag']}：这一段里有 {len(holds)} 张定格片 —— 余量只有一个主人，只准一张。")
    if holds and fills:
        raise RuntimeError(
            f"{seg['tag']}：同一段里既有定格片又有卡片 —— 余量归谁没有依据"
            f"（定格补满 或 卡片补满，不许各分一半）。")
    if holds:
        i, h = holds[0]
        if h.get("dur") != "rest":
            raise RuntimeError(
                f"{seg['tag']}：定格片的 dur={h.get('dur')!r} 不认识 —— "
                f"本片种只支持 dur=\"rest\"（＝填满余量），没有别的语义。")
        if i == 0 or i != len(out) - 1:
            raise RuntimeError(
                f"{seg['tag']}：定格片在第 {i + 1}/{len(out)} 片 —— 它必须**紧跟在素材片之后、"
                f"而且是最后一片**：『复制上一片的最后一帧』要求前面确实有一片，"
                f"后面没有别的片才谈得上「填满余量」。")
        if out[i - 1]["kind"] != "clip":
            raise RuntimeError(
                f"{seg['tag']}：定格片前面那片是 kind={out[i - 1]['kind']!r} —— "
                f"本片种只支持复制**素材片**的末帧（卡片/定格片的末帧不是像素链上的上一帧）。")
        total = sum(p["frames"] for p in out)      # 定格片此刻还是 0 帧
        rest = slot - total
        if rest <= 0:
            raise RuntimeError(
                f"{seg['tag']}：素材片已占满槽位（{total} 帧 ≥ 槽位 {slot} 帧），"
                f"定格要补 {rest} 帧 —— **没地方可填不是阈值问题，是排片不成立**"
                f"（该缩槽位或该换素材，不是让定格「少填一点」）。")
        print(f"    hold=rest：{out[i - 1]['src']} 数出 {total} 帧 → 定格补 {rest} 帧"
              f"（{rest / FPS:.6f}s；槽位 {slot} 帧 − 素材 {total} 帧）")
        h["frames"] = rest

    # `kind="plate"`：**无源片** —— 这一镜没有录屏素材，画面由 `tools/plate_render.py`
    # 按声明表**逐帧画**出来（帧号是唯一的时间源，两次绘制逐字节相同）。
    # 它必须是**独占的一段**：plate 的时刻表（红叉落在第几帧、卡片第几帧推入）是按
    # 整段帧号写死的常量，一旦它跟别人的帧拼在一段里，每个声明时刻都会平移成假的。
    plates = [p for p in out if p["kind"] == "plate"]
    if len(plates) > 1:
        raise RuntimeError(
            f"{seg['tag']}：这一段里有 {len(plates)} 片 plate —— plate 是整段画面，只准一片。")
    if plates:
        if (PR.W, PR.H, PR.FPS) != (W, H, FPS):
            raise RuntimeError(
                f"plate_render 的 {PR.W}×{PR.H}@{PR.FPS} 与本工具的 {W}×{H}@{FPS} 不一致 —— "
                f"画布与成片几何对不上时，所有按像素写的判据（红叉框 / 文字条）全部失真。")
        pl = plates[0]
        sp = PR.spec(pl.get("plate"))       # 未知 plate 名 / 漏键 ⇒ 当场红
        if len(out) != 1:
            raise RuntimeError(
                f"{seg['tag']}：plate 片与另外 {len(out) - 1} 片同段 —— plate 的时刻表是按"
                f"整段帧号写死的，跟别人的帧排在一起会让每一个声明时刻都平移到别处。")
        if pl.get("dur") != "rest":
            raise RuntimeError(
                f"{seg['tag']}：plate 片的 dur={pl.get('dur')!r} 不认识 —— "
                f"本片种只支持 dur=\"rest\"（＝整段由它画满），没有别的语义。")
        if "expect" not in pl:
            raise RuntimeError(
                f"{seg['tag']}：plate 片没有 expect 键 —— 尾帧读回要拿它比对，"
                f"漏写不许静默跳过（那会让 verify 少跑一条判据却仍然打印成功行）。")
        pl["frames"] = slot                 # 独占 ⇒ 槽位全给它
        print(f"    plate=rest：{pl['plate']} 逐帧绘制 {slot} 帧"
              f"（{slot / FPS:.6f}s）；声明表 {len(sp['checks'])} 条像素判据："
              + "、".join(c["what"] for c in sp["checks"]))

    total = sum(p["frames"] for p in out)
    if not fills:
        # 没有卡片吸收余量 ⇒ 「装进槽位」只能由断言保证，不能由卡片兜。两个方向都要红：
        #   **少了**：尾部会被 `build` 的 `trim=end_frame=<槽位>` **静默**切掉，
        #            `plan` 报的数不是成片的数（本项目最恨的"静默"）；
        #   **多了**：`concat` 的余料溢出到下一片。
        if total != slot:
            raise RuntimeError(
                f"{seg['tag']}：没有卡片可吸收余量，Σ片长 {total} 帧 ≠ 槽位 {slot} 帧"
                f"（差 {total - slot:+d} 帧 = {(total - slot) / FPS:+.3f}s）—— "
                f"要么给这一镜加一张卡片，要么让素材片各自装对。")
        return out

    rest = slot - total
    if rest < FPS // 2:
        raise RuntimeError(
            f"{seg['tag']}：素材片占满槽位（余 {rest} 帧），卡片塞不下 —— "
            f"要么槽位该加长，要么素材该重录；**不靠改卡片大小掩盖**。")
    each, extra = divmod(rest, len(fills))
    for i, pc in enumerate(fills):
        pc["frames"] = each + (extra if i == len(fills) - 1 else 0)
    return out


# ----------------------------------------------------------------------------- 出片


def _card_filter(idx: int, pc: dict, tmp: pathlib.Path) -> str:
    """一张大字卡：深底 + 标题 + 副标题。文字走 `textfile=`，绕开 filter 语法转义。

    ⚠️ `expansion=none` **不能省**（D-17，2026-09-26 关掉）：
    `drawtext` 默认按 `%{…}` 展开文本，遇到**落单的 `%`** 只往日志写一行
    `Stray % near ''`，然后**整条 drawtext 一个字都不画**，ffmpeg 仍然 exit 0
    ⇒ `build` 不报错、`verify` 不报错（它不查卡片文案），出片 "PASS" 却少了一行字。
    与 `_overlay_drawtexts` 里那条是**同一个坑**（那里 19c 已踩过并已关）。本条由
    `--self-check-card-pct` 用一含 `%` 的自造串做修前/修后最小复现钉住。
    """
    tf = tmp / f"card{idx}_title.txt"
    sf = tmp / f"card{idx}_sub.txt"
    tf.write_text(pc["title"], encoding="utf-8")
    sf.write_text(pc["sub"], encoding="utf-8")
    rel_t = tf.relative_to(ROOT).as_posix()
    rel_s = sf.relative_to(ROOT).as_posix()
    return (
        f"[{idx}:v]trim=end_frame={pc['frames']},setpts=PTS-STARTPTS,"
        f"drawtext=fontfile={FONT}:textfile={rel_t}:fontcolor=white:fontsize=88"
        f":expansion=none:x=(w-text_w)/2:y=(h-text_h)/2-70,"
        f"drawtext=fontfile={FONT}:textfile={rel_s}:fontcolor=0xFFC94A:fontsize=52"
        f":expansion=none:x=(w-text_w)/2:y=(h-text_h)/2+70,"
        f"setsar=1,format=yuv420p[v{idx}]"
    )


def _overlay_drawtexts(idx: int, pc: dict, tmp: pathlib.Path) -> list[str]:
    """旁注 = **链尾追加的 drawtext**（只镜 5 有）。一个 span 一次 drawtext，从右往左排。

    为什么从右往左：`drawtext` 算不出**另一个** `drawtext` 的宽度，所以"整行居中 / 左对齐"
    在 filter 里没有依据，唯一能写死的锚是**右缘**。于是版式规则是
    「最后一个 span 贴右缘留 `OVERLAY_MARGIN`，它左边的 span 再让出后面每个 span 的
    `OVERLAY_ZONE`」。`OVERLAY_ZONE` 因此是**版式常量**（勾实测宽 ~22px，留一倍余量），
    不是量出来的结论。

    文字一律走 `textfile=`（同 `_card_filter`）：第 1 行含 `<`，写 `text=` 就要跟
    filtergraph 的转义规则纠缠，绕开它。

    ⚠️ `expansion=none` **不能省**（2026-09-26 第一版实测踩到，且是**静默**的）：
    `drawtext` 默认按 `%{…}` 展开文本，遇到**落单的 `%`** 只往日志写一行
    `Stray % near ''`，然后**整条 drawtext 一个字都不画**，ffmpeg 仍然 exit 0
    ⇒ `build` 不报错、`verify` 不报错（它不查旁注），出片"PASS"却少了那行字。
    这一镜两行正文各带一个 `%`（`<10%` / `69.7%`）——第一版只有不带 `%` 的 `✅` 上了屏。
    `expansion=none` 关掉的是 `%{}` 与 `\` 转义，本处文本全是字面量，正是想要的。
    ⚠️ `_card_filter`（`duration=fill` 那些大字卡）**也走 textfile、也没关展开** ——
    今天各卡文案里没有 `%`，所以没暴露；哪张卡文案带上 `%`，就会以同样的方式静默空掉。
    这里不改它（无指令不动无关代码），登记在回执里交下一轮。
    """
    ov = pc.get("overlay")
    if not ov:
        return []
    fonts = {"cjk": FONT, "check": FONT_CHECK}
    out: list[str] = []
    for li, line in enumerate(ov["lines"]):
        spans = line["spans"]
        y = OVERLAY_TOP + li * OVERLAY_LEADING
        for j, sp in enumerate(spans):
            if sp["font"] not in fonts:
                raise RuntimeError(
                    f"{pc['src']} 旁注第 {li + 1} 行第 {j + 1} 个 span 的 "
                    f"font={sp['font']!r} 不认识（只认 {sorted(fonts)}）—— "
                    f"字体名写错不许静默回退到中文字体，面具字会变成豆腐块。")
            if sp["color"].startswith("#"):
                # `#` 在 filtergraph 里是注释起始符，`fontcolor=#D98A00` 会被当注释吃掉。
                # 写成会红的判据，不当场试。
                raise RuntimeError(
                    f"{pc['src']} 旁注颜色 {sp['color']!r} 用了 `#` 前缀 —— "
                    f"`#` 是 filtergraph 的注释起始符，颜色必须写 `0x` 前缀。")
            tf = tmp / f"ov{idx}_{li}_{j}.txt"
            tf.write_text(sp["text"], encoding="utf-8")
            rel = tf.relative_to(ROOT).as_posix()
            gap = OVERLAY_MARGIN + OVERLAY_ZONE * (len(spans) - 1 - j)
            out.append(
                f"drawtext=fontfile={fonts[sp['font']]}:textfile={rel}"
                f":expansion=none"
                f":fontcolor={sp['color']}:fontsize={OVERLAY_SIZE}"
                f":x=w-{gap}-text_w:y={y}")
    # 全片常驻：**不写 `enable=`**（drawtext 的默认就是恒真）。台本要的是"PASS 行与口径
    # 同时在屏"，常驻即满足；"到 PASS 行才出现"要按像素去找那一帧，不值当。
    return out


def _clip_filter(idx: int, pc: dict, tmp: pathlib.Path) -> str:
    """素材片：**先在源上按 `sel` 选帧**（keep 则整段），再 fps=30 + 缩放 + 卡到帧数，
    最后挂上这一片的旁注（若有）。

    顺序有意如此：`sel` 作用在**源帧率**上（`t` 是源帧的时刻），与 `clip_sel` 生成的有理数同源；
    放到 `fps=30` 之后 `t` 的含义就变了。`fps=30` 之后再加一次 `trim=end_frame` 是**双保险**：
    帧数已由 `_clip_len` 数过一遍，这里再卡一次，多出来的料绝不会溢进下一片。
    """
    parts = [clip_chain(pc["sel"], pc["frames"])] + _overlay_drawtexts(idx, pc, tmp)
    return f"[{idx}:v]{','.join(parts)},format=yuv420p[v{idx}]"


def _last_frame_raw(src: pathlib.Path, sel: str, frames: int, out: pathlib.Path) -> pathlib.Path:
    """把某片素材的**末帧**抽成单帧 rawvideo(`yuv420p`) —— 定格片的源。

    ⚠️ 为什么是 rawvideo 而不是临时 PNG（2026-09-26 定）：定格段必须与上一片末帧
    **逐像素相同**，而 PNG 存的是 RGB —— 走 `yuv420p → rgb24 → 文件 → rgb24 → yuv420p`
    这一趟，色彩矩阵的取整**不保证原样返回**（8bit 下 ±1 的差是常态），差 1 个字节
    「逐像素相同」当场为假。rawvideo 把解码出的 yuv 字节原样落盘，一个字节都不动。

    取的是**这一片按自己的滤镜链（`sel` + fps + 缩放 + 卡到 frames）数出来的第 `frames-1` 帧**，
    也就是它在成片里的那一帧 —— 与 `verify` 从成片读回的参照帧同源。若这里用"源文件最后一帧"，
    两边就差了缩放与 `select`，判据会红在一个跟实现无关的地方（或者更糟：绿得没有依据）。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = clip_chain(sel, frames) + f",select=eq(n\\,{frames - 1})"
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(src), "-vf", vf, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "yuv420p", str(out)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    want = W * H * 3 // 2
    got = out.stat().st_size if out.exists() else -1
    if r.returncode != 0 or got != want:
        # 字节数是**结构性判据**：少一个字节就是画幅不对，这种片子混进 concat 会静默错位。
        raise RuntimeError(
            f"定格取末帧失败（{src.name} 的第 {frames - 1} 帧）："
            f"{(r.stderr or '').strip()[:200]}（raw {got} 字节 ≠ {want}）")
    return out


def _hold_filter(idx: int, pc: dict) -> str:
    """定格片：把进来的那一帧**无限重复**，再卡到本片帧数。

    `loop=loop=-1:size=1:start=0` ＝ 单帧无限循环；`trim=end_frame=<本片帧数>` 是**唯一**
    决定长度的东西 —— 不用 `-t <秒>`：秒→帧的取整正是"少一帧"这类静默错的老家
    （见 `_clip_len`：D4 计划 178、实得 176）。帧数由 `plan` 给出，与 Σ片长 是同一个数。

    `-loop 1 -framerate 30 -t <秒> -i <png>`（19c 的写法）在这里被换成 **rawvideo + loop
    滤镜**，两处理由：① PNG 的 RGB 往返（见 `_last_frame_raw`）；② `-t` 是秒，帧数要靠
    取整，而这一片恰恰是"帧数必须精确等于 128"的那一片。
    """
    return (f"[{idx}:v]loop=loop=-1:size=1:start=0,trim=end_frame={pc['frames']},"
            f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{idx}]")


def _piece_filter(idx: int, pc: dict, tmp: pathlib.Path) -> str:
    """按片种分发滤镜链。**只有这一处分发**：输入（`build` 的 `-i`）与滤镜必须一一对应，
    所以那个 `i` 只在这里和 `build` 的输入循环里出现，别处不许再排一遍。

    未知 `kind` **当场红**，不许静默落到卡片分支 —— 一句 `kind="hold "`（多一个空格）
    若被当成卡片，出来的是深色大字卡，而"余量被填满了"这条判据仍然会绿。
    """
    if pc["kind"] == "clip":
        return _clip_filter(idx, pc, tmp)
    if pc["kind"] == "hold":
        return _hold_filter(idx, pc)
    if pc["kind"] == "card":
        return _card_filter(idx, pc, tmp)
    if pc["kind"] == "plate":
        return _plate_filter(idx, pc)
    raise RuntimeError(
        f"第 {idx + 1} 片的 kind={pc['kind']!r} 不认识（只认 clip / hold / card / plate）")


def _plate_filter(idx: int, pc: dict) -> str:
    """无源片：PNG 图像序列进来，一律**不缩放、不重采样**（`plate_render` 就是按
    `W×H` 画的，任何 scale 都只会把判据里的像素坐标挪位）。"""
    return (f"[{idx}:v]trim=end_frame={pc['frames']},setpts=PTS-STARTPTS,"
            f"setsar=1,format=yuv420p[v{idx}]")


# ----------------------------------------------------------------------------- 像素判据
#
# plate 片的判据对象是**颜色像素数随时间怎么变**。做法：把成片解一遍，用 `lutrgb` 把
# 命中判色规则的像素置白、其余置黑，再走 `rawvideo` 按帧读回、逐帧数 255 的个数。
#
# ⚠️ 中间那次 `lut=y='if(gt(val\,128)\,255\,0)'` **不能省**：`format=gray` 给的是
# **有限范围**亮度（黑→16、白→235），直接数 255 会数出 0、数 0 会把背景也算进去 ——
# 那会是一条**恒绿**的判据（本项目红线）。二值化之后 255 才是"命中"的唯一编码。

def _mask_expr(rule: str) -> str:
    """判色规则 → ffmpeg 滤镜串。阈值的**唯一来源**是 `plate_render.RGB_RULES`。"""
    if rule == "bright":
        return f"format=gray,lut=y='if(gt(val,{PR.GRAY_BRIGHT}),255,0)'"
    r = PR.RGB_RULES.get(rule)
    if r is None:
        raise RuntimeError(f"判色规则 {rule!r} 不认识（只认 red / amber / bright）。")
    # ⚠️ 跨通道条件**必须**走 `geq(X,Y)`，不能走 `lutrgb`（2026-09-26 实测，第一次真跑
    # plate 判据时踩到）：本机 ffmpeg 8.1.2 的 `lutrgb` 表达式里**只有一个变量 `val`**
    # （＝当前分量自己的值），`r` / `g` / `b` 三个变量**都没有定义** —— 于是
    # `r='if(gt(r,200)*lt(g,60)*lt(b,60),255,0)'` 报
    # `Undefined constant or missing '(' in 'b,60),255,0)'`，整个掩码链 `exit 1`。
    # 单通道阈值的 `bright` 分支不受影响（它只用 `val`，见上）。
    # `geq` 的 `r(X,Y)`/`g(X,Y)`/`b(X,Y)` 才拿得到**同一像素的另两个通道**。
    # 语义与阈值一字不改：仍是 `RGB_RULES` 生成的严格不等、仍是"命中置 255 其余 0"。
    # 代价：`geq` 慢（实测本机 1920×1080 约 9 帧/秒），换来的是判据能跑。
    terms = []
    for ch, (lo, hi) in r.items():
        if lo is not None:
            terms.append(f"gt({ch}(X,Y),{lo})")
        if hi is not None:
            terms.append(f"lt({ch}(X,Y),{hi})")
    cond = "*".join(terms)
    return (f"format=rgb24,geq=r='if({cond},255,0)':g='if({cond},255,0)':b='if({cond},255,0)',"
            f"format=gray,lut=y='if(gt(val,128),255,0)'")


def _read_exact(f, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def _mask_counts(video: pathlib.Path, rule: str, region: str | None,
                 want: int) -> list[int]:
    """逐帧数"命中判色规则的像素有几个"。`region` 是 `plate_render.REGIONS` 的键名。"""
    vf = _mask_expr(rule)
    cw, ch = W, H
    if region is not None:
        x, y, cw, ch = PR.REGIONS[region]
        vf += f",crop={cw}:{ch}:{x}:{y}"
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vf", vf,
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size, counts = cw * ch, []
    try:
        while True:
            b = _read_exact(p.stdout, size)
            if not b:
                break
            if len(b) != size:
                raise RuntimeError(f"掩码流出到半帧（{len(b)} ≠ {size} 字节）—— 画幅对不上。")
            counts.append(b.count(255))
    finally:
        p.stdout.close()
        err = (p.stderr.read() or b"").decode("utf-8", "replace").strip()
        p.stderr.close()
    if p.wait() != 0:
        raise RuntimeError(f"掩码链失败（{rule}/{region}）：{err[:300]}")
    if len(counts) != want:
        # ⚠️ 这一条是**所有像素判据的地基**：`-fps_mode passthrough` 下掩码流帧数必须
        # 等于成片帧数。少一帧就意味着"数到的是别的东西"，后面每条判据都会静默错位。
        raise RuntimeError(
            f"掩码流给了 {len(counts)} 帧 ≠ 成片 {want} 帧（{rule}/{region}）—— "
            f"帧数对不上时逐帧比对的窗口全部错位，宁可不判。")
    return counts


def _spans(seq: list[int], thr: int) -> list[tuple[int, int]]:
    """把"像素数在涨"的帧号收成几段连续区间（涨＝本帧比上一帧多过 `thr` 个像素）。"""
    out, cur = [], None
    for i in range(1, len(seq)):
        if seq[i] - seq[i - 1] > thr:
            cur = i if cur is None else cur
        elif cur is not None:
            out.append((cur, i - 1))
            cur = None
    if cur is not None:
        out.append((cur, len(seq) - 1))
    return out


def _plate_checks(pl: dict, out: pathlib.Path, bad: list, checks: list) -> None:
    """plate 片的像素判据 —— **声明在 `plate_render.PLATE_SPECS[pid]["checks"]`**，
    这里只负责量。两边都能红：实测与声明不符 ⇒ 进 `bad`；声明本身写错 ⇒ `_validate_check` 当场红。

    审读这份实现时请按同一条标准：**每条判据都要有一个"改动画面就会红"的例子**。
    """
    sp = PR.spec(pl["plate"])
    pid = pl["plate"]
    for ck in sp["checks"]:
        what, kind, rule = ck["what"], ck["kind"], ck["rule"]
        tag = f"{pid}·{what}"
        if kind == "blob":
            det, n = _blob_check(out, sp["frames"] - 1, rule, ck["max_fill"], ck["max_share"])
            if det:
                bad.append(f"{tag}：{det}")
                continue
            checks.append(f"plate[{tag}]")
            print(f"    ✓ {tag}：末帧命中 {n['cnt']} px / 外接框 {n['area']} px "
                  f"= 填充率 {n['fill']:.3f} < {ck['max_fill']}，占全屏 {n['share']:.4f} "
                  f"< {ck['max_share']}")
            continue
        region = ck.get("region")
        seq = _mask_counts(out, rule, region, sp["frames"])
        if kind == "zero":
            nz = [i for i, v in enumerate(seq) if v != 0]
            if nz:
                bad.append(f"{tag}：全片应恒为 0，实测 {len(nz)} 帧有 {rule} 像素（首帧 "
                           f"{nz[0]}，最多 {max(seq)} px）")
                continue
            print(f"    ✓ {tag}：整段 {sp['frames']} 帧，{rule} 像素**恒为 0**")
        elif kind == "steps":
            det = _steps_check(seq, ck)
            if det:
                bad.append(f"{tag}：{det}")
                continue
            print(f"    ✓ {tag}：{len(ck['cues'])} 次落点分别在第 "
                  + " / ".join(str(s) for s, _ in _spans(seq, ck["min_delta"]))
                  + " 帧（声明 " + " / ".join(str(c[0]) for c in ck["cues"]) + "），"
                  + f"其余时段平台（峰值 {max(seq)} px）")
        elif kind == "windows":
            det = _windows_check(seq, ck)
            if det:
                bad.append(f"{tag}：{det}")
                continue
            print(f"    ✓ {tag}：{len(ck['windows'])} 个窗口内逐帧有字、窗口外恒为 0"
                  f"（峰值 {max(seq)} px）")
        elif kind == "zoom":
            det = _zoom_check(seq, ck)
            if det:
                bad.append(f"{tag}：{det}")
                continue
            base = seq[ck["first"] - 1]
            print(f"    ✓ {tag}：{ck['first'] - 1} 帧 {base} px → 峰值 {max(seq)} px"
                  f"（{max(seq) / base:.2f}× ≥ {ck['min_gain']}），"
                  f"之后回落到 {seq[ck['last'] + 1]} px（原位 {base}）")
        checks.append(f"plate[{tag}]")


def _steps_check(seq: list[int], ck: dict) -> str:
    """`steps`：命中像素**只跳声明的那几次**，落点必须在声明的推入窗内，其余时段平台。

    ⚠️ 落点判据的**分辨力上限**（照实写在这里，别当它是"精确到帧"）：
    推入是**滑动**，笔画第几帧入画取决于位移量与裁边 —— 所以允许落点落在
    `[onset, onset+ramp]` 内的任何一帧。越窗即红；但"慢了 1 帧"这种量级在这条判据上
    **分辨不出来**（它测的是"时刻表整体有没有错位"，不是单帧抖动）。
    """
    cues, thr = ck["cues"], ck["min_delta"]
    got = _spans(seq, thr)
    if len(got) != len(cues):
        return (f"落点 {len(got)} 次 ≠ 声明的 {len(cues)} 次"
                f"（实测落点 {got}，阈值 {thr} px/帧）—— 少一次＝该落的没落，"
                f"多一次＝有东西在声明之外的时刻动起来了。")
    for i, ((a, b), (onset, ramp)) in enumerate(zip(got, cues)):
        if not (onset <= a <= onset + ramp):
            return (f"第 {i + 1} 次落点在第 {a} 帧，落在声明的 [{onset}, {onset + ramp}] 之外。")
        if i + 1 < len(got) and b >= got[i + 1][0]:
            return f"第 {i + 1} 次与第 {i + 2} 次落点连成了一段（{b} ≥ {got[i + 1][0]}）。"
    # 声明的第一次落点之前必须干净：这一条把「红叉框没错位到别处」变成可测的。
    if any(seq[:cues[0][0]]):
        return (f"第 {cues[0][0]} 帧之前就有命中像素（最多 "
                f"{max(seq[:cues[0][0]])} px）—— 声明说第一次落点在第 {cues[0][0]} 帧。")
    # 每两次落点之间 / 最后一次之后必须是**平台**：位移停下之后画面不再变。
    edges = [c[0] for c in cues] + [len(seq)]
    for i, (a, b) in enumerate(got):
        seg = seq[b + 1:edges[i + 1]]
        if not seg:
            continue
        lo, hi = min(seg), max(seg)
        if hi and (hi - lo) > ck["plateau_tol"] * hi:
            return (f"第 {i + 1} 次落点之后（第 {b + 1}..{edges[i + 1] - 1} 帧）不是平台："
                    f"{lo}..{hi} px 波动超过 {ck['plateau_tol']:.0%}。")
    return ""


def _windows_check(seq: list[int], ck: dict) -> str:
    """`windows`：命中像素**恰好**只在声明的窗口里 —— 窗口内逐帧有、窗口外逐帧没有。"""
    inw = set()
    for a, b in ck["windows"]:
        inw.update(range(a, b + 1))
    for i, c in enumerate(seq):
        if i in inw and c == 0:
            return f"第 {i} 帧在声明窗口内却是空的（0 px）—— 「停留」比声明的短。"
        if i not in inw and c != 0:
            return f"第 {i} 帧不在任何声明窗口内却有 {c} px —— 有东西在窗口之外亮着。"
    return ""


def _zoom_check(seq: list[int], ck: dict) -> str:
    """`zoom`：某框内的亮像素在声明窗口里涨到 `min_gain` 倍，之后**回落**到原位。

    为什么"回落"必须单列一条：只测"变大了"的话，一个**一直很大**的画面（比如字号
    本来就写错）也会绿 —— 放大是**一涨一落**，两头都要对。
    """
    a, b = ck["first"], ck["last"]
    base = seq[a - 1]
    if base == 0:
        return (f"第 {a - 1} 帧框内是空的（0 px）—— 没有「原样」可作放大倍数的分母，"
                f"这条判据在此片上分辨力为 0（不是通过）。")
    peak = max(seq[a:b + 1])
    after = seq[b + 1]
    if peak < ck["min_gain"] * base:
        return f"窗口内峰值 {peak} px < {ck['min_gain']}×{base} = {ck['min_gain'] * base:.0f} px。"
    if abs(after - base) > ck["flat_tol"] * base:
        return (f"窗口之后没有回落到原位：{after} px vs 原位 {base} px"
                f"（容差 {ck['flat_tol']:.0%}）—— 放大了却没回落，或回落到了别的字号。")
    return ""


def _blob_check(out: pathlib.Path, last: int, rule: str, max_fill: float,
                max_share: float) -> tuple[str, dict]:
    """`blob`：末帧命中像素的**形状** —— 填充率(占自身外接框) / 占屏比。

    ⚠️ 分辨力：这一条**只认"是不是整块/整屏"**，不认笔画形状。填充率 <25% 对两把
    交叉的斜杠（实测 ~9%）有巨大余量，但对"一整块纯红矩形"一定会红。
    """
    from PIL import Image
    png = CARDS / "_probe_masks"
    png.mkdir(parents=True, exist_ok=True)
    f = png / f"{out.stem}_{rule}_{last}.png"
    _frame(out, f"eq(n\\,{last})", f)
    r = PR.RGB_RULES[rule]
    (rl, rh), (gl, gh), (bl, bh) = r["r"], r["g"], r["b"]
    im = Image.open(f).convert("RGB")
    x0, y0, x1, y1, cnt = W, H, -1, -1, 0
    for i, (R, G, B) in enumerate(im.getdata()):
        if (rl is None or R > rl) and (rh is None or R < rh) and \
           (gl is None or G > gl) and (gh is None or G < gh) and \
           (bl is None or B > bl) and (bh is None or B < bh):
            cnt += 1
            x, y = i % W, i // W
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x), max(y1, y)
    if cnt == 0:
        raise RuntimeError(f"{rule} 在末帧一个像素都没有 —— blob 判据没有对象，不许当成通过。")
    area = (x1 - x0 + 1) * (y1 - y0 + 1)
    fill, share = cnt / area, cnt / (W * H)
    if fill >= max_fill:
        return (f"末帧 {rule} 的填充率 {fill:.3f} ≥ {max_fill}"
                f"（{cnt} px 塞在 {area} px 的外接框里）—— 这不是笔画，是一块色块。", {})
    if share >= max_share:
        return (f"末帧 {rule} 占屏 {share:.4f} ≥ {max_share} —— 异常块的面积预算被突破了。", {})
    return "", dict(cnt=cnt, area=area, fill=fill, share=share)


def build(seg: dict, planned: list[dict]) -> pathlib.Path:
    FILM.mkdir(parents=True, exist_ok=True)
    tmp = CARDS / seg["tag"]
    tmp.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for i, pc in enumerate(planned):
        if pc["kind"] == "clip":
            # ⚠️ 这里**故意不用 `-ss`**（2026-09-22 实测踩到）：`-ss` 丢 PTS 早于目标的帧，
            # 与 `fps=30` 叠加会静默少给几帧 —— D4 计划 178、实得 176，整段短 2 帧，
            # 尾帧读回于是抽到不存在的 509 帧。剪点一律由 filter 里的 `sel` 表达。
            # 素材只有 19~33s，全解码的代价可以接受，换来的是"帧号/取整不进这条链"。
            cmd += ["-i", str(EV / pc["src"])]
        elif pc["kind"] == "hold":
            # 上一片必是素材片、且 i>0 —— 由 `plan` 拒排保证，这里不再重复判一遍（判两处会漂）。
            yuv = _last_frame_raw(EV / planned[i - 1]["src"], planned[i - 1]["sel"],
                                  planned[i - 1]["frames"], tmp / f"hold{i}_last.yuv")
            cmd += ["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}",
                    "-r", str(FPS), "-i", str(yuv)]
        elif pc["kind"] == "plate":
            # 无源片：先**逐帧画**成 PNG 序列（`plate_render` 自己保证张数与文件名），
            # 再当图像序列喂进来。`-framerate` 与成片同帧率、不带 `-t`：长度由
            # `_plate_filter` 的 `trim=end_frame=<帧数>` 决定 —— 与定格片同一套算术。
            pix = tmp / f"plate{i}"
            PR.render(pc["plate"], pix, pc["frames"])
            cmd += ["-framerate", str(FPS),
                    "-i", str(pix / f"{pc['plate']}_{pc['frames']:04d}_%05d.png")]
        else:
            cmd += ["-f", "lavfi",
                    "-i", f"color=c=0x0B1020:s={W}x{H}:r={FPS}:d={pc['frames'] / FPS + 0.2:.3f}"]
    parts = [_piece_filter(i, pc, tmp) for i, pc in enumerate(planned)]
    concat_in = "".join(f"[v{i}]" for i in range(len(planned)))
    # 末尾 `trim=end_frame=<槽位帧数>`：把"整段帧数 = 槽位帧数"变成**结构性保证**，
    # 而不是一句算术承诺。卡片是静态大字，多出的 1~2 帧被切掉看不出来；
    # 若反而短了（素材不够），verify 的帧数判据会当场拒收。
    fc = (";".join(parts)
          + f";{concat_in}concat=n={len(planned)}:v=1:a=0,"
            f"trim=end_frame={int(round(seg['slot'] * FPS))},setpts=PTS-STARTPTS[outv]")
    out = FILM / seg["out"]
    # 编码档：默认 `-crf 18`；`encode="lossless"` 走 `-qp 0`（＝x264 无损）。
    # ⚠️ 镜 6（定格片）**必须无损**，这不是画质偏好，是判据能不能成立的问题（2026-09-26 实测）：
    #   定格段与上一片末帧在上游是**逐字节相同**的输入，但 `-crf 18` 出来的片子**不逐像素相同**：
    #   ① 片界 201→202：差 132 个像素（最大 2 级）—— 上一片末帧的块有残差（bS=2 强滤波），
    #      定格片整帧是 skip（bS=0 不过滤），**去块滤波的参数不同**，边缘几行像素就被改了一点；
    #   ② 定格段内部：x264 默认 `keyint=250`，流里第 250 帧是 IDR，而 crf 的 `ipratio` 让 I 帧
    #      比 P 帧编码质量更高 ⇒ 第 250 帧**按输入重编**（错误图样与前一段的 skip 链不同），
    #      于是定格段从第 250 帧（＝定格段第 48 帧）起整段跳一档：差 186 191 个像素、最大 18 级。
    #   ⇒ 「逐像素相同」在有损交付里**根本不可能**成立，与定格机制无关（换成 tpad/clone 一样）。
    #   `-qp 0` 下实测：201 vs 202 / 202 vs 265 / 265 vs 329 **差异字节全为 0**，
    #   连第 250 帧那处也变成 0（无损不走去块、不做 I/P 质量差）。代价：224 267 B → 416 267 B
    #   （+86%，仍不到半兆 —— 终端画面近静止，无损压得动）。
    #   只在声明的段上生效：`crf18` 段的 ffmpeg 命令行**一个字都没变**。
    enc = seg.get("encode", "crf18")
    if enc == "crf18":
        q = ["-crf", "18"]
    elif enc == "lossless":
        q = ["-qp", "0"]
    else:
        raise RuntimeError(f"{seg['tag']}：encode={enc!r} 不认识（只认 \"crf18\" / \"lossless\"）"
                           f"—— 拼错不许静默回落到有损，那会让定格判据红在一个误导人的地方。")
    cmd += ["-filter_complex", fc, "-map", "[outv]", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", *q,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"{seg['tag']} 剪辑失败：{(r.stderr or '').strip()[:400]}")
    return out


# ----------------------------------------------------------------------------- 自证


def _frame(video: pathlib.Path, sel: str, out: pathlib.Path, scale: bool = False) -> pathlib.Path:
    """按 **select 表达式**抽一帧：`eq(n\\,509)` 是"第 509 帧"，`gte(t\\,203776/15360)` 是"该时刻起第一帧"。

    ⚠️ 为什么不敢用 `-ss <秒>`（2026-09-22 实测，踩了两次）：
    `-ss` 是"丢掉 PTS 早于目标时刻的帧"。目标是 18.1167s（想拿第 543 帧、它的 PTS=18.1000）时，
    第 543 帧因为 PTS 早于目标**被丢掉**，拿到的是第 544 帧 —— 静默差一帧。
    更阴的是目标取到 20.9667（第 629 帧的 PTS）时，3 位小数四舍五入成 20.967 > PTS，
    末帧同样被丢 ⇒ ffmpeg **exit 0 却一个字节都不写**。
    所以：一律走 `select`，时间取整这件事根本不进入这条链。
    `not out.exists()` 那一半判据保留 —— 只看 returncode 会把"静默不产出"当成功。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"select={sel}"
    if scale:
        vf += f",scale={W}:{H}:flags=lanczos"
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(video), "-vf", vf, "-frames:v", "1", "-fps_mode", "passthrough",
                        str(out)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"抽帧失败（select={sel}）：{(r.stderr or '').strip()[:200]}")
    return out


def _ssim(a: pathlib.Path, b: pathlib.Path) -> float:
    """两张同尺寸图的结构相似度。用来证"段首帧就是源片 onset 那帧"。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
                        "-lavfi", "ssim", "-f", "null", "-"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in (r.stderr or "").splitlines():
        if "All:" in line:
            return float(line.split("All:")[1].split()[0])
    return 0.0


def _pixel_diff(a: pathlib.Path, b: pathlib.Path) -> int:
    """两张**同尺寸**图的差异像素数（逐像素相减，非零的格子计数）。

    ⚠️ 为什么不算文件 sha256 比一比：那比的是**编码器**（PNG 的压缩参数、时间戳元数据），
    两张像素完全相同的图可以有不同哈希 —— 会把"像素相同"证成假；反过来更糟：
    哈希相等只说明字节一样，说明不了是**逐像素**判据。判据本身只能是逐像素相减。
    取 `gray` 输出：每像素一个字节，非零 = 这一格不一样，直接数。
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(a), "-i", str(b),
         "-lavfi", "[0:v][1:v]blend=all_mode=difference,format=gray",
         "-f", "rawvideo", "-"],
        cwd=str(ROOT), capture_output=True)
    if r.returncode != 0 or len(r.stdout) != W * H:
        # 尺寸不等 ⇒ 字节数不对 ⇒ 这一条**不是"跳过"而是失败**：`blend` 对尺寸不等会报错，
        # 若这里放行，"0 个差异像素"就成了一句没有依据的话。
        raise RuntimeError(
            f"差异像素数算不出来（{a.name} vs {b.name}）：拿到 {len(r.stdout)} 字节，"
            f"要 {W * H}（两张图尺寸不一致，或 ffmpeg 出错）")
    return sum(1 for v in r.stdout if v)


def _hold_scan(out: pathlib.Path, ref_n: int, last: int) -> tuple[list[int], int]:
    """把定格段**每一帧**（不抽样）与上一片末帧（第 `ref_n` 帧）逐字节比。

    为什么在「抽 3 帧」之外还要全扫（2026-09-26 实测，不是理论担忧）：
    拿一份**只把未采样区间涂黑**（成片第 210~220 / 280~290 帧）的样本去喂 `verify()`，
    3 个采样点（202 / 265 / 329）**全落在干净区间** ⇒ 判据判了 PASS，而画面里实有 22 帧是黑的。
    抽样判据的盲区是**能过的**，所以补一趟全扫。代价：定格段每帧 3 MB 过一遍管道
    （本片 129 帧 ≈ 0.4 GB，秒级），流式读、不驻留内存。
    返回 (差异帧号, 扫过几帧)。
    """
    nbytes = W * H * 3 // 2
    total = last - ref_n + 1
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(out), "-vf", f"select=gte(n\\,{ref_n})",
         "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        stdout=subprocess.PIPE)
    try:
        ref = p.stdout.read(nbytes)
        if len(ref) != nbytes:
            raise RuntimeError(f"定格全帧扫描：参照帧只拿到 {len(ref)} 字节（要 {nbytes}）")
        bad: list[int] = []
        for k in range(1, total):
            b = p.stdout.read(nbytes)
            if len(b) != nbytes:
                raise RuntimeError(
                    f"定格全帧扫描：只读到第 {ref_n + k} 帧就断了"
                    f"（应扫到第 {last} 帧，共 {total} 帧）—— 段长与帧数对不上")
            if b != ref:
                bad.append(ref_n + k)
    finally:
        p.stdout.close()
        p.wait()
    return bad, total - 1


def verify(seg: dict, planned: list[dict], out: pathlib.Path,
           onsets: dict[str, float]) -> tuple[bool, list[str], list[str]]:
    """几何 / 时长 / 覆盖配音 / 尾帧逐字读回 / 段首帧对齐。返回 (是否通过, 问题清单, 实跑判据)。

    ⚠️ 第三个返回值是**真正跑过**的判据名，不是一份固定清单：
    ④「段首对齐」只在有 `trim="onset"` 的片上存在 —— S04 两片都是 `keep`，
    它压根没跑。把没跑的判据写进成功行就是**假通过**（本项目的红线），
    所以成功行由这里的 `checks` 拼出来，而不是写死一句话。
    """
    bad: list[str] = []
    checks: list[str] = ["几何", "时长", "帧数"]
    info = probe(out)
    dur, nframes = float(info.get("duration", 0) or 0), int(info.get("nb_frames", 0) or 0)
    want_frames = int(round(seg["slot"] * FPS))
    print(f"    ffprobe: {info.get('width')}x{info.get('height')} "
          f"{info.get('avg_frame_rate')} {dur:.3f}s / {nframes} 帧")
    if (info.get("width"), info.get("height")) != (str(W), str(H)):
        bad.append(f"几何不对：{info.get('width')}x{info.get('height')}（要 {W}x{H}）")
    if abs(dur - seg["slot"]) > 0.15:
        bad.append(f"时长 {dur:.3f}s 与槽位 {seg['slot']}s 差得超过 0.15s")
    if nframes != want_frames:
        bad.append(f"帧数 {nframes} ≠ 槽位帧数 {want_frames}")

    # ② 覆盖配音：段长必须 ≥ 该镜实测语音，否则"装进槽位"是假的。
    #    ⚠️ `narration=None` ＝ **显式**声明"本镜无配音"（S11 追加块不进 179s 版，
    #    「配音合计 174.2s」那笔账里没有它）—— 这时本条判据**不跑**、也**不写进成功行**
    #    （本工具的红线：只列真正跑过的判据）。它同样**不会**因此变红：无配音的段
    #    "段长 ≥ 语音"这件事根本没有对象，判它等于凭空造一个判据。
    #    但**键缺失**（不是 None）当场红：漏写一行 `narration=` 与"确定这一镜没有配音"
    #    是两件事，前者静默降级成后者，正是本工具最恨的那种静默。
    if "narration" not in seg:
        raise RuntimeError(
            f"{seg['tag']}：表项里没有 narration 键 —— 要么写配音文件名（相对 {VOICE}），"
            f"要么**显式**写 narration=None 声明本镜无配音；漏写不许静默当成「无配音」。")
    if seg["narration"] is None:
        print("    配音：本镜**无配音**（表项显式声明 narration=None ⇒ 覆盖配音判据不跑，"
              "也不进成功行）")
    else:
        mp3 = VOICE / seg["narration"]
        if not mp3.exists():
            bad.append(f"找不到配音 {mp3} —— 对位判据没法做（不是「跳过」，是失败）")
        else:
            checks.append("覆盖配音")
            voice = float(probe(mp3).get("duration", 0) or 0)
            print(f"    配音 {seg['narration']}: {voice:.3f}s ／ 段长 {dur:.3f}s "
                  f"→ 余量 {dur - voice:+.3f}s")
            if voice > dur + 1e-6:
                bad.append(f"配音 {voice:.3f}s 比段长 {dur:.3f}s 还长 —— 这一镜会被念到下一镜去")

    # ③ 每片尾帧逐字读回（卡片也读：证明中文字形真的烧进画面了）
    cursor = 0
    for pc, plan_pc in zip(seg["pieces"], planned):
        first, last = cursor, cursor + plan_pc["frames"] - 1
        label = (pc.get("src")
                 or (f"卡『{pc.get('title')}』" if pc["kind"] == "card"
                     else (f"无源片『{pc.get('plate')}』本片自绘"
                           if pc["kind"] == "plate"
                           else f"定格片（复制上一片末帧，源帧 {first - 1}）")))

        # ④（同一片里做了）：掐头掐在**最紧处** —— 该片首帧必须**不早于**源片 onset 那一帧、
        #    且必须**就是**它。判据是"源片上 onset 前一帧 / 后一帧"三张图的相对比较：
        #    成段首帧与 onset 帧的 SSIM 必须**并列最高**。
        #    为什么不卡绝对阈值（"SSIM ≥ 0.995"）：成段经过缩放 + H.264，与源帧不可能逐像素相同，
        #    绝对阈值只会变成拍脑袋的数；相对判据与编码无关。
        #    ⚠️ 这条判据**不是万能的**，两处盲区必须说清，不包装成"已证明"：
        #      ① 若源片这三帧本身就几乎相同（静态页面的常态，实测三帧 SSIM 全是 0.9965），
        #         判据毫无分辨力 —— 所以把"三帧互差"当场印出来，小于 1e-4 就直说分辨力为 0。
        #      ② **"掐晚一帧"在硬切素材上原理上不可判**：硬切后 onset 与 onset+1 逐像素相同，
        #         无论起点滑到哪一帧，content 比出来的相似度都一样。能判的只有"掐早"。
        #         （真实录屏里 D3/D4 的 onset 前一帧互差 0.28/0.26，所以"掐早"一定抓得住。）
        #         晚一帧的兜底是帧数守恒 + ③ 的尾帧读回：段长必须等于槽位，掐晚就少一帧、
        #         由卡片补齐，内容整体前移一帧 —— **这一层确实兜不住**，登记在 DEBT 里。
        if pc["kind"] == "clip" and pc["trim"] == "onset":
            if "段首对齐" not in checks:
                checks.append("段首对齐")
            src = EV / pc["src"]
            rational, sec, r_prev, r_next = onsets[pc["src"]]
            b = _frame(out, f"eq(n\\,{first})", FILM / "_frames" / f"{seg['tag']}_f{first:04d}.png")
            # `gte(t,<某帧的精确 PTS>)` 取到的**第一帧就是那一帧**（别用 `lt` —— 那是源片第一帧）
            cands = {}
            for k, rat in ((-1, r_prev), (0, onsets[pc["src"]][0]), (1, r_next)):
                a = _frame(src, f"gte(t\\,{rat})",
                           FILM / "_frames" / f"src_{pc['src']}_{k:+d}.png", scale=True)
                cands[k] = _ssim(a, b)
            top = max(cands.values())
            spread = top - min(cands.values())
            print(f"    掐头择近：成段第 {first} 帧 对 源片 onset['{rational}'] 相邻三帧的 SSIM = "
                  + " / ".join(f"[{k:+d}] {v:.4f}" for k, v in sorted(cands.items()))
                  + f"（onset={sec:.3f}s，三帧互差 {spread:.4f}）")
            if spread < 1e-4:
                print("      ⚠️ 源片这三帧几乎逐像素相同 ⇒ **本判据在本片上的分辨力为 0**，"
                      "「掐头最紧」不由它证明（见 ③ 尾帧逐字读回）")
            elif cands[0] < top - 1e-4:
                # 只有"能分辨、且 onset 不是并列最高"才算掐错 —— 避免在静态页面上误报
                best = max(cands, key=lambda k: cands[k])
                bad.append(f"{label} 的片首帧最像源片 onset{best:+d} 那一帧而不是 onset 帧"
                           f"（掐头差了 {abs(best)} 帧；三帧互差 {spread:.4f}，分辨得出来）")

        # ⑤ 定格片：**每一帧都必须是上一片末帧的复制**（差异像素数 = 0）。
        #    判据本体＝**全帧扫描**（`_hold_scan`：`select=gte(n\\,ref_n)` 流式过管道、逐帧比，
        #    不抽样）。为什么抽 3 帧不够（2026-09-26 实测）：只把未采样区间涂黑的样本，
        #    3 个采样点全落在干净区间 ⇒ 抽样判据 PASS，而画面里实有 22 帧是黑的
        #    （`docs/DEBT.md` D-22）。下面那三帧数字**降级为打印**：看得见，但不再据它判
        #    PASS/FAIL —— 抽样判据的盲区是**能过的**（原委记在 `_hold_scan` 的 docstring）。
        #    为什么这里敢要求**绝对为 0**（别处一律不敢卡绝对阈值）：定格片与上一片末帧
        #    在上游是**同一份字节**（见 `_last_frame_raw`：rawvideo 原样过手），
        #    编码器对逐字节相同的输入取 skip，解码回来就该一模一样。
        #    「差不多像」在这里是放水 —— 定格段一旦与末帧不同，观众看到的就是**抖动**。
        if pc["kind"] == "hold":
            if "定格逐像素" not in checks:
                checks.append("定格逐像素")
            if first == 0:
                bad.append(f"{seg['tag']}：定格片是第 1 片 —— 前面没有「上一片的末帧」可复制")
            else:
                ref_n = first - 1
                # 判据本体：全帧扫描。差异帧表非空 ⇒ 本片校验失败（报告里逐帧列出来）。
                hold_bad, scanned = _hold_scan(out, ref_n, last)
                print(f"    定格全帧扫描：第 {ref_n + 1}~{last} 帧 对 上一片末帧第 {ref_n} 帧 "
                      f"→ 差异帧 {hold_bad or '无'}（共扫 {scanned} 帧，定格段长 "
                      f"{last - first + 1} 帧）")
                if hold_bad:
                    bad.append(
                        f"定格段第 {' / '.join(str(n) for n in hold_bad)} 帧 与上一片末帧"
                        f"（第 {ref_n} 帧）不逐像素相同（差异帧 {len(hold_bad)} 个，"
                        f"全帧扫过 {scanned} 帧）—— 定格段不是末帧的逐像素复制")
                # 三帧数字**只打印**（判据已上移到全帧扫描）：留着是为了报告里看得见
                # 首/中/末三点的差异像素数，不是为了判 PASS/FAIL。
                ref = _frame(out, f"eq(n\\,{ref_n})",
                             FILM / "_frames" / f"{seg['tag']}_hold_ref{ref_n:04d}.png")
                picks = (("首帧", first), ("中点帧", (first + last) // 2), ("末帧", last))
                for name, n in picks:
                    png = _frame(out, f"eq(n\\,{n})",
                                 FILM / "_frames" / f"{seg['tag']}_hold{n:04d}.png")
                    diff = _pixel_diff(ref, png)
                    print(f"    定格{name} 第 {n} 帧 对 上一片末帧第 {ref_n} 帧："
                          f"差异像素 {diff}（抽检，仅打印）")
        else:
            if "尾帧读回" not in checks:
                checks.append("尾帧读回")

            #: ⚠️ `last` 同时是**游标来源**（紧接着的 `cursor = last + 1`），不许改它 ——
            #: 所以"读哪一帧"单开一个 `rn`：默认读片尾（`last`），黑场收尾的片
            #: （镜 10：末 45 帧黑场）显式声明 `readback_frame` 指向**内容所在的那一帧**。
            #: 这不是豁免：声明的那一帧仍要逐字读回 `expect`；黑场本身由 plate 的像素判据
            #: `windows(bright, s10full, [(0, 434)])` 正面证（"亮像素到 434 帧为止"）。
            rn = int(pc.get("readback_frame", last))
            png = _frame(out, f"eq(n\\,{rn})", FILM / "_frames" / f"{seg['tag']}_{rn:04d}.png")
            res = ask_vl(png, QUESTION)
            ans = res.get("answer") or ""
            miss = [e for e in pc["expect"] if e not in ans]
            print(f"    帧 {rn}（{label}）| VL ok={res.get('ok')} "
                  f"命中 {len(pc['expect']) - len(miss)}/{len(pc['expect'])}")
            if miss:
                bad.append(f"{label} 第 {rn} 帧没读回 {miss}")
                print("      --- VL 逐字读回原文 ---")
                for ln in (ans or f"(空) err={res.get('error')}").splitlines():
                    print(f"      | {ln}")
        cursor = last + 1

    # ⑥ plate 片的**像素判据**：量什么由 `plate_render.PLATE_SPECS[pid]["checks"]` 声明，
    #    怎么量在这里。它替换不了 ③ 的尾帧读回（读回证的是"画面说的是人话"，这里证的是
    #    "画面对得上时刻表"），两条都跑、都进成功行。
    for pc, plan_pc in zip(seg["pieces"], planned):
        if pc["kind"] == "plate":
            _plate_checks(plan_pc, out, bad, checks)

    return (not bad), bad, checks


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只做某一段（S04 / S08）")
    ap.add_argument("--verify-only", action="store_true", help="不重剪，只复核已有成段")
    ap.add_argument("--self-check-utf8", action="store_true",
                    help="自证伪：只走成功行那条路径（打印 ✅ 后退出），给编码守卫当探针")
    a = ap.parse_args()

    if a.self_check_utf8:
        # 成功行是本工具**唯一**带非 GBK 字符的出口，也正是"四项判据全过、片子已落盘、
        # 却报非 0 退出"那个坑的现场。这里把它单独走一遍，好让守卫能在秒级内证伪。
        print(ok_line(["几何", "时长", "帧数"]))
        return 0

    segs = [s for s in SEGMENTS if not a.only or s["tag"] == a.only]
    if not segs:
        print(f"[X] 没有匹配的段：--only {a.only}")
        return 2

    onsets: dict[str, tuple[str, float, str, str]] = {}
    for seg in segs:
        print(f"\n===== {seg['tag']}｜镜 {seg['shot']}｜槽位 {seg['slot']}s｜{seg['why']} =====")
        for pc in seg["pieces"]:
            if pc["kind"] == "clip" and pc["trim"] == "onset" and pc["src"] not in onsets:
                onsets[pc["src"]] = result_onset(EV / pc["src"])
                print(f"    场景检测：{pc['src']} 结果上屏于 {onsets[pc['src']][1]:.3f}s "
                      f"＝ PTS {onsets[pc['src']][0]}（唯一一次大变化）")
        planned = plan(seg, onsets)
        for pc in planned:
            label = (pc.get("src")
                     or (f"卡『{pc.get('title')}』" if pc["kind"] == "card"
                         else "定格片（复制上一片末帧）"))
            print(f"    {label}: {pc['frames']} 帧 ({pc['frames'] / FPS:.3f}s)")
        enc = seg.get("encode", "crf18")
        print(f"    编码档 {enc}"
              + ("（libx264 -qp 0 无损）" if enc == "lossless" else "（libx264 -crf 18）"))
        out = FILM / seg["out"]
        if not a.verify_only:
            out = build(seg, planned)
            print(f"    出片 {out.relative_to(ROOT)}")
        if not out.exists():
            print(f"    ❌ 缺文件 {out}")
            return 1
        ok, bad, checks = verify(seg, planned, out, onsets)
        if not ok:
            REJECTED.mkdir(parents=True, exist_ok=True)
            dst = REJECTED / seg["out"]
            shutil.move(str(out), str(dst))
            (REJECTED / f"{seg['tag']}_WHY.txt").write_text(
                f"{seg['out']} 被拒收：\n" + "\n".join(f"  - {b}" for b in bad) + "\n",
                encoding="utf-8")
            print(f"    ❌ 拒收 → {dst.relative_to(ROOT)}")
            for b in bad:
                print(f"       - {b}")
            return 1
        print(ok_line(checks))
    print("\n成片段判定：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
