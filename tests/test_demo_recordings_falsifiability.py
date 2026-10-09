"""录屏守卫的**变异自证**：证明 `tests/test_demo_recordings.py` 那 7 条不是装饰品。

做法（照仓库里 `test_record_demo_guard_falsifiability.py` 的先例）：把**真实踩过的历史故障**
原文注入进被测对象，要求每条变异至少让**对应那条**守卫变红；再断言真文件下守卫全绿。

变异体全部取自 2026-09-20 的实际事故与已修bug，不是"等价改写"：
  M1 `.bat` 里写 `set PYTHONPATH=`   → 空值 PYTHONPATH，Python 启动即崩（三段录屏全废）
  M2 DEMO 命令里带中文              → `.bat` 以 ASCII 写盘，中文被替换成 `?`
  M3 去掉 user32 的 argtypes 声明    → 句柄被按 c_int 截断，ClientToScreen 静默失败
  M4 去掉"矩形不可信"闸门            → 小矩形也照录，画面混进别的窗口
  M5 去掉结束标记轮询                → 退回"固定秒数猜跑完没有"（汇总那屏进不了画面）
  M6 taskkill 精确匹配标题           → 命令跑完标题已变成 "<title> DONE"，残窗杀不掉
  M7 去掉 UNAWARE 硬闸门             → 150% 缩放下逻辑/物理坐标错位，抓错地方
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import record_all_demo as rad  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "tg", ROOT / "tests" / "test_demo_recordings.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)
# 演示页（autorun 那一层）的守卫住在另一个测试文件里 —— 一起加载进来，
# 这样"页面也被变异守住"这件事不必新开一个自证文件。
spec2 = importlib.util.spec_from_file_location(
    "tp", ROOT / "tests" / "test_demo_page.py")
tp = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(tp)
REAL_SRC = tg.SRC
REAL_PAGE_SRC = tg.PAGE_SRC
REAL_SERVE = tg.SERVE_SRC
REAL_HTML = tp.HTML

#: 变异目标可以点名"改哪个文件"：(模块属性名, 目标串)。写成裸字符串 = 改 record_demo.py。
#: SRC=录屏工具｜PAGE_SRC=页面录制工具｜SERVE_SRC=演示服务（图片路径闸门）｜HTML=演示页本身（autorun 那一层）。
#: ⚠️ 键名 = 变异时要 setattr 的**属性名**，所以服务端这条必须叫 `SERVE_SRC`
#: （叫 `SERVE` 会凭空多出一个没人读的属性，变异静默失效 —— 2026-09-25 实测踩到）。
SRC_OF = {"SRC": REAL_SRC, "PAGE_SRC": REAL_PAGE_SRC, "SERVE_SRC": REAL_SERVE,
          "HTML": REAL_HTML}

# (变异体, 变异后的 SRC, 或 变异 DEMO 的动作, 期望变红的那条守卫)
MUTANTS = [
    ("M1 cmd 里写 set PYTHONPATH=", None,
     lambda d: d.__setitem__(0, {**d[0], "cmd": d[0]["cmd"] + " && set PYTHONPATH= && echo x"}),
     "test_no_empty_pythonpath_in_recorded_commands"),
    ("M2 命令里带中文小节名", None,
     lambda d: d.__setitem__(0, {**d[0], "cmd": d[0]["cmd"] + " --section 弃权分解"}),
     "test_demo_commands_are_ascii_only"),
    ("M3 不声明 GetClientRect.argtypes", "GetClientRect.argtypes", None,
     "test_window_api_argtypes_declared"),
    ("M4 去掉矩形闸门", "客户区矩形不可信", None, "test_window_api_argtypes_declared"),
    ("M5 去掉结束标记轮询", "FindWindowW(None, title + DONE_SUFFIX)", None,
     "test_record_waits_for_done_marker_not_fixed_seconds"),
    ("M6 taskkill 改精确匹配", "WINDOWTITLE eq {title}*", None,
     "test_record_waits_for_done_marker_not_fixed_seconds"),
    ("M7 去掉 UNAWARE 硬闸门", "DPI 感知=UNAWARE", None, "test_dpi_awareness_gate_before_measuring"),
    # 最后一条守卫（DEMO 字段完整性）也要有变异体，否则它是"没人守的装饰"
    ("M8 某段丢掉承诺数字", None,
     lambda d: d.__setitem__(0, {**d[0], "expect": []}), "test_demo_case_schema"),
    ("M9 段标签被改名", None,
     lambda d: d.__setitem__(1, {**d[1], "tag": "D"}), "test_demo_case_schema"),
    # ---- 页面录制（2026-09-21 新增入口）的变异体 ----
    # M10/M11 是**没踩过但会致命**的两个：录制端把旧产物当成新产物 = 页面还在跑就收尾；
    # 录制端顺手 taskkill 浏览器 = 把用户自己的窗口一起杀掉。
    ("M10 判活不看开录时刻", "st_mtime >= t0", None,
     "test_page_recorder_waits_for_a_new_artifact"),
    ("M11 录完不取消置顶", "_un_topmost(hwnd)", None,
     "test_page_recorder_never_kills_a_window_it_did_not_open"),
    ("M12 没等到落盘信号也放行", ("PAGE_SRC", 'if r.get("finished_after") is None'), None,
     "test_page_recorder_waits_for_a_new_artifact"),
    ("M13 页面期望值不再从产物读", ("PAGE_SRC", "rep.get("), None,
     "test_page_recorder_expectations_come_from_the_artifacts"),
    # ---- 自动演示模式（autorun）的变异体：录屏要靠它，它坏了视频就没有"用产品"的那一镜 ----
    ("M14 不等证据图就先滚", ("HTML", "img.complete"), None,
     "test_autorun_waits_for_the_evidence_image_before_scrolling"),
    ("M15 autorun 变成默认就跑", ("HTML", 'q.get("autorun") !== "1"'), None,
     "test_autorun_is_opt_in_and_touches_nothing_but_the_form"),
    # ---- D3 事故（2026-09-21）的变异体：完成信号 + 期望值来源 ----
    # 探针实测：证据图 +1.0s 落盘、报告 +18.6s 落盘 → 拿目录当信号，1.5s 就收尾。
    ("M16 完成信号退回【目录里有新文件】", ("SRC", 'if any(ch in pat for ch in "*?[")',
                                    "if target.is_dir():"), None,
     "test_page_recorder_waits_for_a_new_artifact"),
    ("M17 完成信号丢掉通配闸门", ("SRC", 'if any(ch in pat for ch in "*?[")', "if True:"), None,
     "test_page_recorder_waits_for_a_new_artifact"),
    ("M18 期望值不筛开录时刻", ("PAGE_SRC", "p.stat().st_mtime >= t0"), None,
     "test_page_expectations_come_only_from_this_run"),
    # ---- 渲染完成标记（镜 D1 事故：只等 #result 不隐藏 → 滚到半成品文档上）----
    ("M21 render() 结尾不挂渲染完成标记",
     ("HTML", 'document.documentElement.dataset.rendered = "1"'), None,
     "test_autorun_waits_for_render_to_finish_not_just_the_panel"),
    # ---- 「勾 F3 判定不变」与滚动目标（D3 二次事故）的变异体 ----
    ("M24 期望值不按滚动目标分叉",
     ("PAGE_SRC", 'case.get("scroll") == "f3"'), None,
     "test_page_f3_shot_scrolls_to_the_card_it_reads_back"),
    ("M25 主流程不比对判定",
     ("PAGE_SRC", "verdict_unchanged(base, rep)"), None,
     "test_page_recorder_proves_f3_does_not_move_the_verdict"),
    ("M26 比对集混进 explanation",
     ("PAGE_SRC", "VERDICT_KEYS = (\"content_id\"",
      "VERDICT_KEYS = (\"explanation\", \"content_id\""), None,
     "test_page_recorder_proves_f3_does_not_move_the_verdict"),
    ("M27 没比过也不吭声",
     ("PAGE_SRC", "判定不变这一条本次没有比对"), None,
     "test_page_recorder_proves_f3_does_not_move_the_verdict"),
    ("M28 页面滚动目标写死账本",
     ("HTML", 'q.get("scroll") === "f3"'), None,
     "test_autorun_scroll_target_is_selectable"),
    ("M22 render() 开头不撤标记",
     ("HTML", "delete document.documentElement.dataset.rendered"), None,
     "test_autorun_waits_for_render_to_finish_not_just_the_panel"),
    ("M23 autorun 不等渲染完成标记",
     ("HTML", 'dataset.rendered !== "1"'), None,
     "test_autorun_waits_for_render_to_finish_not_just_the_panel"),
    ("M19 调用处不传开录时刻", ("PAGE_SRC", 'report_since(r.get("t0"))'), None,
     "test_page_expectations_come_only_from_this_run"),
    ("M20 退回 newest_report", ("PAGE_SRC", "def report_since("), None,
     "test_page_expectations_come_only_from_this_run"),
    # ---- id 撞车（镜 D3 真 bug：`explain` 撞 `explain` → 卡片正文被写进 <input>）----
    ("M29 F3 勾选框改回 explain",
     ("HTML", 'id="explain-toggle"'), None,
     "test_no_duplicate_element_ids"),
    ("M30 人为造一个重复 id",
     ("HTML", 'id="comments"', 'id="explain-toggle"'), None,
     "test_no_duplicate_element_ids"),
    ("M31 layers 不削 F3 那一行",
     ("PAGE_SRC", 'startswith("F3")'), None,
     "test_page_recorder_proves_f3_does_not_move_the_verdict"),
    # ---- 外部输入滚跑画面（2026-09-21 实测：置顶窗口收到滚轮/按键，页面被平滑滚到文档底部）----
    ("M32 autorun 不钉视口（只滚一次）",
     ("HTML", "Math.abs(window.scrollY - target())"), None,
     "test_autorun_pins_the_viewport_against_stray_input"),
    # ---- 前置等待 + 开页前起钟（2026-09-21：产物 t0+1.01s 落盘，余量不到 0.2s）----
    ("M33 录制端不给前置等待", ("PAGE_SRC", "&lead="), None,
     "test_page_recorder_gives_the_page_a_lead_in"),
    ("M34 页面不认 lead", ("HTML", 'q.get("lead")'), None,
     "test_autorun_lead_in_is_opt_in_and_off_by_default"),
    ("M35 开页后起钟", ("PAGE_SRC", "t0=t_open"), None,
     "test_page_recorder_starts_the_clock_before_the_page_opens"),
    # ---- 置顶只在开场压一次（2026-09-21 镜 D3：录 23s，片尾整幅成了编辑器窗口；
    #      同一轮 D1/D2 只有 11s 没事 —— 而复核抽的正是片尾那一帧）----
    ("M36 置顶只在开场压一次",
     ("SRC",
      "        if topmost and time.time() >= next_pin:\n"
      "            _re_topmost(hwnd)\n"
      "            next_pin = time.time() + KEEP_TOPMOST_EVERY\n",
      "        if False:                   # 变异：开场压过一次就够了\n"
      "            pass\n"), None,
     "test_page_recorder_keeps_the_window_on_top_for_the_whole_take"),
    ("M37 收尾退回 6 秒大 sleep",
     ("SRC",
      "                deadline = time.time() + tail\n"
      "                while time.time() < deadline:\n"
      "                    _re_topmost(hwnd)\n"
      "                    time.sleep(0.25)\n",
      "                time.sleep(tail)\n"), None,
     "test_page_recorder_keeps_the_window_on_top_for_the_whole_take"),
    ("M38 不量『被谁盖住』",
     ("SRC", "_top_window_at(x + cw // 2, y + ch // 2)", '(0, "")'), None,
     "test_page_recorder_keeps_the_window_on_top_for_the_whole_take"),
    # ---- 镜 D4「拔网线照出账本」（2026-09-22）：出口必须真掐断，否则这一镜是假的 ----
    ("M39 起服务不算上黑洞环境",
     ("PAGE_SRC", "env=env,", "env=None,"), None,
     "test_page_recorder_can_cut_the_model_egress_for_the_offline_shot"),
    ("M40 没有走离线档的镜",
     ("PAGE_SRC", "offline=True,", "offline=False,"), None,
     "test_page_recorder_can_cut_the_model_egress_for_the_offline_shot"),
    ("M41 不清 NO_PROXY 白名单",
     ("PAGE_SRC", 'for k in ("NO_PROXY", "no_proxy"):', "for k in ():"), None,
     "test_page_recorder_can_cut_the_model_egress_for_the_offline_shot"),
    # ---- 2026-09-22 实测：2.0s 一次的重压顶挡不住别的窗口挤进 30s 那一镜 ----
    ("M42 重压顶间隔太稀（2s→30s）",
     ("SRC", "KEEP_TOPMOST_EVERY = 0.5", "KEEP_TOPMOST_EVERY = 30.0"), None,
     "test_page_recorder_keeps_the_window_on_top_for_the_whole_take"),
    ("M43 不单独量片尾那几秒",
     ("SRC", "covered_at_end", "MUTATED_COVERED"), None,
     "test_page_recorder_keeps_the_window_on_top_for_the_whole_take"),
    # ---- 镜 D5（观察栏）+ 图片路径闸门（2026-09-25 新增）的变异体 ----
    # M44 是**真踩到过的缺陷**：`_cmp_key` 少带输入身份，镜 D5 就会落到 D1 的键上，
    # 把 D1 留的基准覆盖掉 → D3 拿 D5 的报告当基准比 → 凭空报一个"判定变了"。
    # 这正是"加新镜必须先看比对键"这条教训的可执行版本。
    ("M44 比对键丢掉输入身份（撞键覆盖基准）",
     ("PAGE_SRC", 'return (case["preset_id"], case.get("image", ""), bool(case.get("observe", 0)),',
      'return (case["preset_id"],'), None,
     "test_page_case_schema"),
    ("M45 D5 不指标定集那张图",
     ("PAGE_SRC", 'image="samples/calib/calib_splice_cross_source.jpg"', 'image=""'), None,
     "test_page_case_schema"),
    # 放宽路径闸门是**动安全边界**：删掉一条条件也必须被抓到（只靠行为测试会漏）。
    ("M46 图片路径闸门不限层数",
     ("SERVE_SRC", "len(parts) > 2", "False"), None,
     "test_serve_image_gate_only_widens_depth_never_escapes"),
    ("M47 图片路径闸门不拒 `..`",
     ("SERVE_SRC", '".." in parts', "False"), None,
     "test_serve_image_gate_only_widens_depth_never_escapes"),
]


def _run(name: str) -> str:
    """跑一条守卫：'green' / 'RED'。守卫在哪个模块里就调哪个模块。"""
    mod = tp if hasattr(tp, name) else tg
    try:
        getattr(mod, name)()
        return "green"
    except AssertionError:
        return "RED"


def test_every_mutant_makes_its_guard_red():
    original_demo = [dict(c) for c in rad.DEMO]
    try:
        for label, needle, demo_mut, guard in MUTANTS:
            # --- 变异 ---
            tg.SRC, tg.PAGE_SRC, tg.SERVE_SRC, tp.HTML = (REAL_SRC, REAL_PAGE_SRC,
                                                         REAL_SERVE, REAL_HTML)
            if needle:
                # 变异目标三种写法：裸串（改 record_demo.py 里这一处）、
                # (属性名, 目标串)、以及 (属性名, 目标串, 换成什么)。
                if isinstance(needle, str):
                    attr, target, repl = "SRC", needle, "___MUTATED___"
                else:
                    attr, target = needle[0], needle[1]
                    repl = needle[2] if len(needle) > 2 else "___MUTATED___"
                base = SRC_OF[attr]
                assert target in base, f"{label}: 变异目标串不在 {attr} 里（守卫已改？）"
                # 全部替换（不传 count）：同一个闸门现在在**两个录制入口**里各有一份
                # （record() 录我们自己开的 cmd 窗口、record_window() 录浏览器窗口），
                # 只拿掉一处等于闸门还在 —— 那样"M4 去掉矩形闸门"会假绿。
                # 三元的变异体给的是**替换成什么**：M16 要真的把通配闸门换成"目录里有文件"
                # （换成哑字符串的话 `is_dir() 不在里面` 那条断言就永远碰不到）。
                setattr(tp if attr == "HTML" else tg, attr, base.replace(target, repl))
            if demo_mut:
                demo_mut(rad.DEMO)
            # --- 期望：对应守卫变红 ---
            got = _run(guard)
            assert got == "RED", f"变异体没被抓到：{label} → {guard} 仍是 {got}"
            # --- 还原 ---
            rad.DEMO[:] = [dict(c) for c in original_demo]
    finally:
        # ⚠️ 两个源码属性都要还原：只还原 SRC 的话，PAGE_SRC 带着变异活到最后，
        # 下一条"真源码下守卫必须全绿"会莫名其妙变红（本轮就是这么被抓出来的）。
        tg.SRC, tg.PAGE_SRC, tg.SERVE_SRC, tp.HTML = (REAL_SRC, REAL_PAGE_SRC,
                                                     REAL_SERVE, REAL_HTML)
        rad.DEMO[:] = [dict(c) for c in original_demo]


def test_real_sources_keep_all_guards_green():
    """反向断言：真源码/真 DEMO 下所有静态守卫必须全绿（否则守卫自己在说谎）。

    只跑"不需要 fixture 的"那些 —— 带 `demo` 夹具的守卫是行为测试，各有自己的文件管。
    """
    for name in [n for n in dir(tg) if n.startswith("test_") and n != "test_real_sources_keep_all_guards_green"]:
        assert _run(name) == "green", f"真源码下守卫没绿：{name}"
