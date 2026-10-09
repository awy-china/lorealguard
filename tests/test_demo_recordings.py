"""录屏素材的守卫：把 2026-09-20 同一小时里踩的三起事故钉成常驻测试。

三起事故的共同点很危险 —— **"文件在、非黑帧、时长合格"三条全绿**，
只看产物存在性是过不了关的，必须看屏幕内容：
  1) 命令里写 `set PYTHONPATH=`：这是 Git-Bash `env -u PYTHONPATH`（真取消）的
     误译。cmd 里它留下一个**空值** PYTHONPATH，Python 启动即
     `Fatal Python error: error evaluating path` → 三段录屏全是崩溃画面。
  2) ctypes 调 user32 没声明 argtypes/restype：HWND 被按 c_int 传，
     GetClientRect/ClientToScreen 静默失败 → 抓取矩形退回屏幕左上角 (0,0,…)，
     录到的画面里**混进了别的窗口**（VL 在画面里读到了聊天界面和代码 diff）。
  3) 用固定秒数猜"命令跑完没有"：录屏时 CPU 被 libx264 抢走，同一个
     p2_bench.py 单跑 13s、录屏时 43s 还没跑完 → 结果汇总那一屏没进画面。

本文件只做静态断言（不录屏、不联网、秒级），任一条变红就说明老坑回来了。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import record_all_demo as rad  # noqa: E402
import record_demo as rd  # noqa: E402

SRC = (ROOT / "tools" / "record_demo.py").read_text(encoding="utf-8")
PAGE_SRC = (ROOT / "tools" / "record_demo_page.py").read_text(encoding="utf-8")
#: 演示服务的源码文本。加进来是为了让**图片路径闸门**也能被变异自证 ——
#: 2026-09-25 为 `samples/calib/` 放开"一层子目录"时新增的那道闸门是**安全边界**，
#: 只靠行为测试会躺成一个没人能证伪的断言（变异体 M46/M47 各拿它一条）。
SERVE_SRC = (ROOT / "demo" / "serve.py").read_text(encoding="utf-8")


def _body(name: str) -> str:
    """切出 record_demo.py 里某个**顶层**函数的源码（到下一个顶层 def 为止）。

    录屏工具现在有两个入口：`record()`（我们自己开的 cmd 窗口）与
    `record_window()`（浏览器等**别人开的**窗口）。井字断言"某某行在源码里"会
    把两处混在一起数，所以按函数切开再断言。
    """
    head = f"\ndef {name}("
    assert head in SRC, f"record_demo.py 里没有 {name}()"
    return SRC.split(head, 1)[1].split("\ndef ", 1)[0]


def test_demo_commands_are_ascii_only():
    """事故 0 的兄弟：.bat 是以 ASCII 写盘的，命令里的中文会被替换成 '?'。

    （真踩过：`--section 弃权分解` 到了 .bat 里变成 `--section ???`。）
    """
    for c in rad.DEMO:
        assert c["cmd"].isascii(), f"段 {c['tag']} 的命令含非 ASCII 字符：{c['cmd']}"


def test_no_empty_pythonpath_in_recorded_commands():
    """事故 1：`set PYTHONPATH=` 会留下空值 PYTHONPATH → Python 启动崩溃。"""
    for c in rad.DEMO:
        assert "set PYTHONPATH=" not in c["cmd"], f"段 {c['tag']} 用了空值 PYTHONPATH"
        assert "env -u PYTHONPATH" not in c["cmd"], (
            f"段 {c['tag']} 用了 Git-Bash 语法，在 cmd 窗口里会报"
            "'env 不是内部或外部命令'"
        )


def test_window_api_argtypes_declared():
    """事故 2：user32 句柄必须声明签名，否则量出来的矩形会退回屏幕左上角。"""
    for api in ("FindWindowW", "GetClientRect", "ClientToScreen", "SetWindowPos"):
        assert f"{api}.argtypes" in SRC, f"{api} 没声明 argtypes（句柄会被截断）"
    assert "_u32()" in SRC, "缺少统一的 user32 封装"
    # 量矩形失败必须**报错**，不能静默返回 (0,0)
    assert "GetClientRect 失败" in SRC and "ClientToScreen 失败" in SRC
    # 录制前必须过"矩形闸门"：不可信的矩形要拒绝录制
    assert "客户区矩形不可信" in SRC, "缺少矩形闸门：小矩形会录进别的窗口"


def test_record_waits_for_done_marker_not_fixed_seconds():
    """事故 3：收尾要靠"命令跑完"的标记，不能靠固定秒数猜。"""
    assert rd.DONE_SUFFIX.strip(), "缺少结束标记后缀"
    # .bat 里要埋标记（命令跑完后改标题）
    assert "{title}{DONE_SUFFIX}" in SRC, ".bat 没埋结束标记"
    # 录制端要轮询这个标记，并且在看到之后多录一小段（让结果汇总停在片尾）
    assert "FindWindowW(None, title + DONE_SUFFIX)" in SRC, "录制端没等结束标记"
    assert "finished_after" in SRC, "没把'命令跑完时刻'量出来"
    # 杀掉自己开的窗口时要用通配匹配（标题已被改成 "<title> DONE"）
    assert "WINDOWTITLE eq {title}*" in SRC, "_kill_window 精确匹配会杀掉不残窗"


def test_dpi_awareness_gate_before_measuring():
    """事故 4（最隐蔽的一个）：缩放 150% 下，UNAWARE 进程量的是逻辑坐标、
    ddagrab 抓的是物理像素 → 区域偏左上且偏小（画面混进别的窗口、下半截汇总被切掉）。"""
    assert "def _dpi_awareness()" in SRC, "没有 DPI 感知级别查询"
    assert "_dpi_aware()" in SRC, "没有声明 DPI 感知"
    assert "DPI 感知=UNAWARE" in SRC, "缺少 UNAWARE 硬闸门（会录出坐标系错位的废片）"
    body = SRC.split("def record(")[1]
    assert body.index("aw = _dpi_awareness()") < body.index("_client_rect_on_screen(hwnd)"), (
        "DPI 感知必须在量矩形之前声明，否则量到的坐标系就是错的"
    )


def test_demo_case_schema():
    """三段素材的字段要齐：命令、期望数字、输出名、上限秒数。"""
    assert [c["tag"] for c in rad.DEMO] == ["A", "B", "C"]
    for c in rad.DEMO:
        for k in ("title", "out", "cmd", "expect", "seconds"):
            assert c.get(k), f"段 {c['tag']} 缺字段 {k}"
        assert c["expect"], f"段 {c['tag']} 没有承诺数字"
        assert c["out"].endswith(".mp4")


# --------------------------------------------------------------- 页面录制（镜 D）
# 2026-09-21 新增：演示页要录进视频，就得有自己的守卫。
# 危险点与 cmd 三段**不同**：浏览器窗口不是我们开的 —— 录完绝不能顺手 taskkill，
# 否则会把用户自己的浏览器一起杀掉；而"跑完了没有"也不能靠标题标记（标题是页面的，
# 改了就是伪造），只能靠产物落盘。

def test_page_recorder_never_kills_a_window_it_did_not_open():
    """别人的窗口只许"取消置顶"，不许杀。"""
    body = _body("record_window")
    assert "_un_topmost(hwnd)" in body, "录完没有取消置顶（窗口会一直压在别人上面）"
    assert "_kill_window" not in body, (
        "record_window 里出现了 _kill_window：浏览器窗口不是我们开的，"
        "按标题 taskkill 会连带杀掉用户自己的窗口"
    )
    assert "_write_bat" not in body, "record_window 不该写 .bat（那是 cmd 入口的事）"
    # 对照锚点：证明上面那条"不许出现"不是永远为真的空断言 ——
    # 同一个工具里，cmd 入口 record() 确实是**要**杀自己开的窗口的。
    assert "_kill_window" in _body("record"), (
        "record() 里也该有 _kill_window（否则'不许杀'那条断言区分不出任何东西）"
    )


def test_page_recorder_gates_are_the_same_as_the_cmd_path():
    """两个入口共用同一批坑：DPI 感知、矩形闸门、区域抓屏、跑完再录 tail 秒。"""
    body = _body("record_window")
    assert "_dpi_awareness()" in body and "DPI 感知=UNAWARE" in body, "缺 DPI 硬闸门"
    assert body.index("_dpi_awareness()") < body.index("_client_rect_on_screen(hwnd)"), (
        "DPI 感知必须在量矩形之前声明"
    )
    assert "客户区矩形不可信" in body, "缺矩形闸门"
    assert "_ddagrab_cmd(" in body, "没走共用的区域抓屏命令（会漏掉 GDI 黑帧那个坑）"
    # 2026-09-21 起这 tail 秒不再是**一个大 sleep**，改成按 0.25s 走的截止循环：
    # 几秒的 sleep 里没人管置顶，而被盖住的那一帧正好是抽帧复核用的那一帧。
    assert "deadline = time.time() + tail" in body, "没在'跑完'之后再录 tail 秒（结果那屏会缺）"


def test_page_recorder_waits_for_a_new_artifact():
    """旧产物不算"跑完了"：mtime 必须晚于开录时刻，而且必须是**收尾产物**。

    两起实测：
      · 靠固定秒数猜跑完没有 → 汇总那屏根本没进画面；
      · 拿"目录里有新文件"当信号 → 2026-09-21 探针实测：证据图 +1.0s 就落盘、
        报告 +18.6s 才落（中间那段在网络里跑 F3）→ 1.5s 就收尾，片尾停在页面
        顶部的表单上，期望值还顺手取了**上一镜**的报告（D3 读回 0/4）。
    """
    body = _body("_done_seen")
    assert "st_mtime >= t0" in body, "_done_seen 没比对开录时刻（旧产物会被当成新产物）"
    # ⚠️ "目录里有任何新文件就算跑完"必须先禁掉，再谈通配：先落盘的证据图会被当收尾信号
    assert "is_dir()" not in body, (
        "把'目录里有新文件'当完成信号：证据图先落盘就会被误判成跑完，"
        "F3 那段（实测 17s）根本没进画面"
    )
    assert 'any(ch in pat for ch in "*?[")' in body, (
        "完成信号没有通配匹配：认不出'哪一份才是收尾产物'"
    )
    assert "t0" in _body("record_window"), "record_window 必须把开录时刻传给判活函数"
    # t0 必须在"找到窗口"之前取：被录的窗口是叫我们之前就开好的，页面自己会先跑完，
    # t0 取晚了 → 产物 mtime 永远 < t0 → 信号一次都不触发（实测：录满 91s 上限才收尾）
    rw = _body("record_window")
    assert rw.index("t0 = time.time()") < rw.index("_live_window(title)"), (
        "t0 取晚了：必须在找窗口之前取，否则'产物比 t0 新'永远不成立"
    )
    assert 'if r.get("finished_after") is None' in PAGE_SRC, (
        "没等到落盘信号却照样通过 —— 那等于退回'按秒数猜跑完没有'"
    )
    # 收尾产物必须点名（通配），而不是把整个产物目录丢给判活函数
    assert '"t0": t0' in rw, "record_window 没把开录时刻交回调用方（挑不出'本次运行'的产物）"
    assert "done_target=REPORTS" in PAGE_SRC and '*_report.json' in PAGE_SRC, (
        "录制端没有点名收尾产物（报告才是页面的最后一步）"
    )


def test_page_expectations_come_only_from_this_run():
    """期望值只认**本次运行**写出的报告：按开录时刻筛，不认"目录里最新那份"。"""
    assert "def report_since(" in PAGE_SRC, "缺少'本次运行产物'的筛选函数（退回 newest_report）"
    body = PAGE_SRC.split("def report_since(", 1)[1].split("\ndef ", 1)[0]
    assert "st_mtime >= t0" in body, (
        "取报告没比对开录时刻：上一镜的报告就在同一个目录里（D3 那次读了 D2 的）"
    )
    assert 'report_since(r.get("t0"))' in PAGE_SRC, (
        "调用处没把本次开录时刻传进去 —— 那筛的就不是'本次运行'"
    )


def test_page_case_schema():
    """页面三镜的字段要齐：预置语料 id、是否勾 F3、输出名、上限秒数、证明什么。"""
    import record_demo_page as rdp
    assert [c["tag"] for c in rdp.PAGE_DEMO] == ["D1", "D2", "D3", "D4", "D5"]
    for c in rdp.PAGE_DEMO:
        for k in ("preset_id", "out", "seconds", "why"):
            assert c.get(k), f"镜 {c['tag']} 缺字段 {k}"
        assert c["out"].endswith(".mp4")
        assert isinstance(c["explain"], int)
    # 至少要有一镜勾了 F3 —— 开源权重档参与演示是赛题硬约束，不能只写在 PPT 里
    assert any(c["explain"] for c in rdp.PAGE_DEMO), "没有任何一镜跑 F3（开源模型就没进演示）"
    # 镜 D4 与镜 D1 必须是**同一份预置语料**、只差"勾 F3 + 掐断出口"，
    # 否则"模型够不着时判定也没动"这句话就没有可比对象。
    d1 = next(c for c in rdp.PAGE_DEMO if c["tag"] == "D1")
    d4 = next(c for c in rdp.PAGE_DEMO if c["tag"] == "D4")
    assert d4["preset_id"] == d1["preset_id"], "离线那一镜与基准镜不是同一份预置语料"
    # 镜 D5（观察栏）的三个字段 —— 断在**源码文本**上（理由同下条：变异只能改源码
    # 字符串，读模块对象的那条永远变不红）。少了任何一个，这一镜都白录：
    # 不勾观察栏 = 没东西可看；不指标定集那张图 = 两个维度只会判 `unavailable`
    # （`samples/` 根目录三张图实测如此，2026-09-25 逐图跑 `--observe`）；
    # 不滚到附四卡 = 屏幕上是账本，观察栏在下面看不见。
    d5_src = PAGE_SRC.split('tag="D5"', 1)[1][:600]
    assert "observe=1" in d5_src, "镜 D5 没勾观察栏（那它什么都证明不了）"
    assert 'image="samples/calib/' in d5_src, \
        "镜 D5 用的不是标定集那张图（根目录三张图上 lighting 实测全 unavailable ⇒ 白录）"
    assert 'scroll="obs"' in d5_src, "镜 D5 没滚到附四卡（屏幕上看不到观察栏）"
    # 比对键必须带上**完整输入身份**（预置语料 + 图 + 观察栏档）。
    # ⚠️ 这条断言在**源码文本**上，不在导入的模块对象上 —— 变异测试只能改源码字符串，
    # 断言读模块对象的话它永远变不红（同本文件上面那两条的先例；变异体 M44 拿它）。
    # 少带字段的后果是实测到的：D5 会落到 D1 的键上，**把 D1 留的基准覆盖掉** →
    # D3 随后拿 D5 的报告当基准比 → 凭空报一个"判定变了"。
    assert 'case.get("image", ""), bool(case.get("observe", 0))' in PAGE_SRC, \
        "比对键没带完整输入身份（图 / 观察栏档）：不同输入会撞键、互相覆盖基准"
    # 再在真模块上核一遍"同键必须同输入"（D3/D4 是同一份输入，共键是对的）。
    # 这条是保险带：它没有专属变异体，但真出事时会红（比如有人把 D5 的图换成 D1 的）。
    by_key: dict = {}
    for c in rdp.PAGE_DEMO:
        by_key.setdefault(rdp._cmp_key(c), set()).add(
            (c["preset_id"], c.get("image", ""), bool(c.get("observe", 0))))
    for k, inputs in by_key.items():
        assert len(inputs) == 1, f"比对键 {k} 被两个不同的输入共用了：{inputs}"


def test_serve_image_gate_only_widens_depth_never_escapes():
    """放开 `samples/calib/` 之后，那道闸门**只有"层数"变宽** —— 其余一律还得拒。

    为什么单独立一条：放宽图片路径闸门是**动安全边界**。只靠行为测试
    （`tests/test_demo_page.py` 那组 400）不够 —— 闸门哪天被人删掉一条条件，
    行为测试完全可能仍然绿。所以这里直接钉住源码里那几条拒收条件，
    外加最后一道 `resolve()` 包含性检查。变异体 M46/M47 各拿一条。
    """
    body = SERVE_SRC.split("def _pick_image", 1)[1].split("\ndef ", 1)[0]
    assert "len(parts) > 2" in body, "没限制层数：三级路径（samples/a/b/c.jpg）也进得来"
    assert '".." in parts' in body, "没拒 `..`（`samples/calib/../..` 能出去）"
    assert '"" in parts' in body, "没拒空段（`samples//x.jpg` 这种）"
    assert 'startswith(".")' in body, "没拒隐藏段（`.hidden/` 这种）"
    assert "relative_to(SAMPLES_DIR" in body, "丢了 resolve() 的包含性检查（最后一道）"
    assert 'rel.startswith("samples/")' in body, "丢了 samples/ 前缀要求"


def test_page_recorder_can_cut_the_model_egress_for_the_offline_shot():
    """镜 D4（拔网线照出账本）必须**真的**把出口掐断，不是在页面上写死一个 error。

    本机网线不能真拔（会连当前这个会话一起断），掐的是 F3 唯一那条出口：
    起演示服务时给一个黑洞代理（`http://127.0.0.1:9`）并**清掉 NO_PROXY 白名单**
    —— `openai`→httpx 默认 `trust_env=True`，代理环境变量就是它唯一认的路。
    实测（2026-09-22）：`status=error` / `调用失败：APIConnectionError: Connection error.`，
    而同一次运行的账本是 `base_neutral_7a4ab438` / 指纹 `1a9f812fd314` / 关注级 / E1，
    **与联网那一镜逐字段相同**（判定链零模型调用，这是铁律 3 的可执行版本）。
    """
    assert "BLACKHOLE_PROXY" in PAGE_SRC, "没有'掐断模型出口'的档"
    assert '"NO_PROXY", "no_proxy"' in PAGE_SRC, (
        "没清 NO_PROXY 白名单：本机若配了白名单，百炼会被放行 → 这一镜是假的拔网线"
    )
    body = PAGE_SRC.split("def start_server(", 1)[1].split("\ndef ", 1)[0]
    assert "env=env" in body, "起服务时没把黑洞环境交给子进程（那 F3 照样连得上）"
    # 断言在**源码文本**上而不是在导入的 PAGE_DEMO 上：变异测试只能改源码字符串，
    # 断言读模块对象的话，这一条永远变不红 —— 那就成了"没人守的装饰"。
    assert "offline=True" in PAGE_SRC, "没有任何一镜走离线档"
    # 期望值仍然只能来自产物：工具里手写 'error' 就是伪造（跟手写等级常量同一类错）
    exp = PAGE_SRC.split("def expectations(", 1)[1].split("\ndef ", 1)[0]
    assert '"error"' not in exp and "'error'" not in exp, "期望值里手写了 error（那是伪造）"


def test_page_recorder_expectations_come_from_the_artifacts():
    """承诺字样必须**从本次运行的 report.json 里读**，不许在工具里手写常量。"""
    assert "def expectations(" in PAGE_SRC, "缺少从产物生成期望值的函数"
    body = PAGE_SRC.split("def expectations(", 1)[1].split("\ndef ", 1)[0]
    assert "rep.get(" in body, "期望值没有从报告里取"
    for baked in ("关注级", "提示级", "高风险"):
        assert baked not in body, f"expectations 里手写了等级常量 {baked}（应为从产物读）"
    # 判定"读不回来"时必须是废片照拒，不许留在证据目录里
    assert "_rejected_page" in PAGE_SRC and "reject(c, why)" in PAGE_SRC, "缺拒收路径"

def test_page_recorder_proves_f3_does_not_move_the_verdict():
    """「勾上 F3 判定不变」必须是**比出来的**，不能只写在注释和 PPT 里。

    镜子 D1（不勾 F3）与 D3（勾 F3）跑的是**同一份预置语料**，所以两份报告的
    判定链字段必须逐字段相同 —— 这是铁律 3（判定权归算法，模型永不参与判定）的
    可执行版本。反面：把 explanation 也塞进比对集，等于让"模型产物"混进判定链。
    """
    assert "def verdict_unchanged(" in PAGE_SRC, "没有把'判定不变'变成可执行的比对"
    assert "verdict_unchanged(base, rep)" in PAGE_SRC, (
        "主流程里没调用比对：录完就放行，等于没验"
    )
    keys = PAGE_SRC.split("VERDICT_KEYS = (", 1)[1].split(")", 1)[0]
    for volatile in ("explanation", "generated_at", "evidence_files", "version"):
        assert volatile not in keys, f"判定链比对集里混进了 {volatile}（那不是判定）"
    for must in ("fingerprint", "detectors", "verdict", "evidence"):
        assert must in keys, f"判定链比对集漏了 {must}"
    # 没比过就必须说出来（--only D3 时基准镜没跑），不能让人以为验过了
    assert "判定不变这一条本次没有比对" in PAGE_SRC, "没比过的时候不吭声"
    # `layers` 里 F3 那一行记的是"语义解释层跑没跑"，勾/不勾本来就不同 ——
    # 不削掉它，第一次比对就会红在 ['layers'] 上（实测踩到）。
    vo = PAGE_SRC.split("def verdict_only(", 1)[1].split("\ndef ", 1)[0]
    assert 'startswith("F3")' in vo, (
        "layers 没削掉 F3 那一行：会把'跑没跑变了'误报成'判定变了'"
    )


def test_page_f3_shot_scrolls_to_the_card_it_reads_back():
    """勾 F3 那一镜滚到 F3 卡片，期望值也必须取自 F3 卡片。

    实测（2026-09-21）：卡片在账本**下面**，原来滚的是账本 → 要求读回屏幕外
    的模型名，判"读不回来"把好片拒收了。期望值要跟着那一屏走。
    """
    assert 'scroll="f3"' in PAGE_SRC, "没有让 D3 滚到 F3 卡片（模型名在屏幕外）"
    assert 'case.get("scroll") == "f3"' in PAGE_SRC, (
        "期望值没按滚动目标分叉：会要求读回屏幕上根本没有的字"
    )


def test_page_recorder_gives_the_page_a_lead_in():
    """开跑前要给录制端留出就位时间，否则录到的是"已经跑完的静态页"。

    实测（2026-09-21，三镜一致）：产物落盘于 **t0+1.01s**，而录制端那 1.2s 里还要
    找窗口、`_show_no_activate`、量矩形、起 ffmpeg —— 余量不到 0.2s，机器稍忙
    "用产品"的过程就整段录不到，只剩一张跑完的静态页（赛题 `:33` 要的正是过程）。
    页面那一侧（真的等、且默认关）由 `tests/test_demo_page.py` 守。
    """
    assert "LEAD_IN_MS" in PAGE_SRC, "录制端没有给页面前置等待量"
    assert "&lead=" in PAGE_SRC, "open_page 没把前置等待量写进 URL"


def test_page_recorder_keeps_the_window_on_top_for_the_whole_take():
    """置顶要**一直在**：开场压一次，长镜的片尾就录成了别人的窗口。

    实测（2026-09-21，镜 D3）：同一轮里 D1/D2 只录 11s 没事，D3 录 23s —— 片尾那一帧
    整幅是编辑器/聊天窗。抓屏抓的是屏幕合成后的画面，被录窗口一被盖住录到的就是别人的
    界面，而产物照样"文件在、非黑帧、时长合格"。更糟的是**片尾那一帧正是抽帧复核用的
    那一帧**（`dur-0.4s`），而原来收尾处是一个 6 秒的 `time.sleep(tail)` —— 复核帧整段
    没人管置顶。
    """
    assert "def _re_topmost(" in SRC, "缺少「只置顶、不动几何」的辅助函数"
    helper = SRC.split("def _re_topmost(", 1)[1].split("\ndef ", 1)[0]
    for flag in ("SWP_NOMOVE", "SWP_NOSIZE", "SWP_NOACTIVATE"):
        assert flag in helper, (
            f"_re_topmost 少了 {flag}：重压顶时挪动/缩放被录窗口，"
            "ffmpeg 的抓取矩形（开录前量好写死的）就和窗口错开了"
        )
    body = _body("record_window")
    assert "_re_topmost(hwnd)" in body, "录制过程中没有重新压顶"
    loop = body.split("while rec.poll() is None:", 1)[1]
    # （1）轮询里按**计时器**周期重压 —— 不是开场压一次就完事
    assert "KEEP_TOPMOST_EVERY" in loop, (
        "重压顶没有周期：开场 `_show_no_activate` 压一次，之后 20 多秒里谁盖上来都认了"
    )
    assert "_re_topmost(hwnd)" in loop, "轮询里没有重新压顶"
    # （2）收尾留帧那一段也必须一直压着 —— 复核抽的就是那一段里的帧
    assert "time.sleep(tail)" not in loop, (
        "收尾还是一个 6 秒的大 sleep：那几秒里没人管置顶，而抽帧复核抽的正是那一段"
    )
    tail_seg = loop.split("finished_after = round(", 1)[1].split("rec.stdin.write", 1)[0]
    assert "_re_topmost(hwnd)" in tail_seg, "收尾留帧那一段没有继续压顶"
    # 被盖住要**量出来、点名**：只说"读不回来"是查不出是谁盖的
    assert "_top_window_at(" in body and '"covered_by"' in SRC, (
        "没有量'抓取区域被谁盖住'：出了废片只知道读不回来，不知道是谁盖的"
    )
    # 片尾留帧那几秒要**单独量**：复核抽的就是那几帧，被盖住就必废
    assert "covered_at_end" in SRC, "片尾那几秒被谁盖住没有单独量出来"
    assert "tail_started" in body, "没把'片尾留帧开始了'这个状态记下来"
    # 重压顶的间隔：2026-09-22 实测 2.0s 太稀 —— 30s 那一镜照样被别的窗口挤进来
    # （探针点到了名字：另一个 Edge 窗口 + VS Code），10–20s 的短镜则全过。
    every = SRC.split("KEEP_TOPMOST_EVERY = ", 1)[1].split("\n", 1)[0]
    assert float(every) <= 1.0, f"重压顶间隔 {every}s 太稀（实测 2.0s 挡不住）"


def test_page_recorder_starts_the_clock_before_the_page_opens():
    """钟要在**开页之前**起 —— 产物是页面写的，页面比钟早跑完就永远等不到信号。"""
    body = PAGE_SRC.split("def main()", 1)[1]
    assert "t_open = time.time()" in body, "没有在开页之前取时刻"
    assert body.index("t_open = time.time()") < body.index("open_page("), (
        "t_open 取晚了：必须在 open_page 之前取，否则'产物比 t0 新'可能整段不成立"
    )
    assert "t0=t_open" in body, "开页前的时刻没传给录制端（钟还是从找窗口才开始走）"
