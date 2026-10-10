# LorealGuard · 项目交接卡（Claude Code 开工前必读）

> 这是给 Claude Code 的常驻交接说明：在本仓库开会话时**自动加载**。
> 它同时给人看 —— 任何一条与实际不符，**以源码为准**并直接指出是哪一行过时了。

## 0. 沟通纪律

- **一律中文**；先给结论，再给理由；不要复述已知结论当新建议。
- **现状类结论必须带 `文件:行号`**（谁调用谁、挂了哪条模型、注册了哪些检测器）。
- **文档 ≠ 事实**：文档与源码冲突时以源码为准，并明确指出文档哪一行过时。
- **不许奉承、不许脑补数字**：文件/代码里没有的，标"未核实"，不要编。
- 改动前先说清"改哪个文件、为什么"。**未经用户明确同意，不要 `git add` / `commit` / `push`。**

## 0b. 权限边界（2026-09-21 起预授权）

本项目的 Claude Code 会话**已关闭逐条确认**（不再弹"是否允许执行"），所以下面这些**不是提示、是纪律**，你必须自己守住：

- **不许 `git add` / `commit` / `push`**：仓库历史由用户手动提交，你只改工作区。
- **不许删除**用户文件或目录（生成物也先报告再删）；不许 `rm -rf`。
- **不许读密钥**：`.env`、`settings.json`、`*credentials*` 一律不读、不打印、不写进任何产物（值一律 `[REDACTED]`）。
- **不许上传**：真实人物照片 / 证件类图片绝不外发；只用 `samples/` 合成样图。
- 危险动作（含推送到远端、重置历史、批量删除）**一律先问用户**。

## 1. 这是什么

**LorealGuard（信任守护师）** —— 「欧莱雅美妆科技黑客松 · 赛题2：美妆内容真实性核验」参赛作品。
**守护对象是创作者**：宁可漏判，也不冤枉人 → **误报护栏是第一优先级**（目标误报率 < 10%）。

**赛题硬约束与评分要点**（原文 `D:/deliver/compete/loreal-topic2-details.md`）：

- `loreal-topic2-details.md:10-14` **需使用开源大模型而非闭源模型实现 Agent 系统**；
- `loreal-topic2-details.md:33` 评分第 1 位原话：要「**可运行、可展示的完整项目形态，而非仅停留在模型/算法演示层面**」；
- `loreal-topic2-details.md:78` 演示视频要「**实际使用时的演示，展示核心功能与交互流程**」；
- 决赛提交物含「**交互式 Demo（现场可运行）**」+ 源代码 + 运行指南；初赛只要 PPT + 演示视频 + 数据包。

## 2. 架构真相（已核，别重开）

```
层1 经典取证算法（纯 CPU）     → 出物证：可疑坐标 + 各检测器数字 + 证据图
层2 语义解释（qwen3.8-27b）   → 只解释已定位的疑点，不给它判真假
层3 LLM Agent                 → 见下方"已知空白"，如实说
```

- **大模型不碰"是不是假的"**：坐标由算法给，语义由模型给。**判定链零模型调用是刻意设计，不是缺失。**
- 交付运行时**唯一允许的档**（`voiceguard/config.py:37-38`）：`MODEL_AGENT = MODEL_SEMANTIC = qwen3.8-27b`（Apache-2.0 开源权重，白名单 `OPEN_WEIGHT_MODELS`）；非白名单档**一调用即拒、且不发起任何请求**（`voiceguard/semantic/explain.py:86-90`）。
- `qwen3-vl-plus/flash`、`qwen3.8-max/flash`、`deepseek-v4.1-flash` 全在 `RESEARCH_ONLY_MODELS`：**只许离线研究 / 内部质检，不进交付运行时**。
- ⚠️ **四处已知空白，不许含糊过去**：
  1. ~~`voiceguard/config.py:37` 的 `MODEL_AGENT` 在 `voiceguard/` 下零调用点~~ → **2026-09-27 亲核已过时，以源码为准**：`MODEL_AGENT` 至少两处被引用 —— `voiceguard/agent/planner.py:320`（**F9 Agent 编排层**，已落地：`voiceguard/guard.py:22` 导入、`:508-509` 在 `if self.agent:` 分支里调用 `agent_planner.plan`）与 `voiceguard/semantic/vl.py:86`。**正确口径**：层3 **已实现但默认关闭**（`voiceguard/__main__.py:56 --agent`），且**位置在判定完成之后**（入参是判完的账本，只追加取证与追问，不写判定字段）⇒ 说「**判定链零模型调用**（模型只出现在判定之后）」，不要说「三层未实现」，也不要含糊成「三层 LLM Agent 已实现」。
  2. ~~`voiceguard/forensics/registry.py:48` 只注册了 `ela`（官方点名的光影/阴影一致性、色彩过渡**未实现**）~~ → **2026-09-25 已补**：`lighting.py` / `color_edge.py`（官方 `loreal-topic2-details.md:48` / `loreal-topic2-details.md:50` 点名维度）落地为**观察栏（B 档）**，注册时 `default=False`（`voiceguard/forensics/registry.py:32-33`）⇒ **默认路径不跑它们**。⚠️ 它们**不进左栏物证、不进规则表、不改任何判定字段**（含 `confidence` —— 修前实测会被拖一档，已修，见 `voiceguard/guard.py:_judging()`）。三处出口 = `GuardPipeline(observe=)` / CLI `--observe` / 页面勾选框；守卫 `tests/test_observations_column.py`、`tests/test_cli.py`、`tests/test_demo_page.py`；口径 = `D:/deliver/compete/loreal-f1-prereg-v2.md` §7/§8。
  3. ~~`demo/` 是**空目录**，全仓库零 Web~~ → **2026-09-21 已落地**：`demo/`（本地服务 `serve.py` + 单页 `index.html`，入口＝**图 + 文案 + 评论**，产物落 `output/demo/`）。⚠️ ~~CLI 仍只收图（`voiceguard/__main__.py:40` 不收 text/comments）~~ **这条已过时，以源码为准**：`voiceguard/__main__.py:42 --text`、`voiceguard/__main__.py:44 --comments-file` 都在 ⇒ 三环在命令行上都有出口（守卫 `tests/test_cli.py:146/:161`）。仍如实登记的空白：页面**不做真假终审**，也不放开任意图片（只列 `samples/` 合成样图）。
  4. **良性编辑里「尺寸不变的低质重压缩」会被推上关注级**（2026-09-24 实测，③）：四类单算子编辑中裁剪 / 调色 / 加字幕与未编辑对照均「未触发」，而**原位 q75 重压缩**命中 `R-SPLICE-SUSPECT-REVIEW`。成因：免责闸门 `detector_reliable`（`voiceguard/guard.py:89-92`）只在 `jpeg_history is False` 或 `platform_recompressed is True` 时放行，**尺寸不变的重编码两条都不满足** ⇒ 该加免责的没加。**决定性对照**：同质量、只多一步改尺寸（短边 1080），z 峰值反而更高（25.2 vs 15.8）却被 `PLATFORM_RECODE` 拦下 —— 分界线落在「几何是否变化」上，不在信号强弱上。**这是能力边界、不是待修的阈值**（铁律 1/2）；红线由 `tests/test_benign_edit_invariance.py` 钉住（含两条自证伪），登记在 `docs/DEBT.md` D-12。**答辩口径**：主动说「原位降质重压缩我们会给人看，但那是复核提示，不是判违规」。**2026-10-01 实测证明（本轮）**：这条边界是**可分性缺失（信息论级）**，不是"少了一条检查" —— 拼接**后再重编码**会把两侧噪声场**一起同质化** ⇒ ELA 区域证据与异源噪声台阶**同时失效**，真拼接 `cs_d_s8_q70`（−0.15）与良性原位重压缩 `c_q70`（−0.07）**取值同一**；预注册的修法（要求 `noise_step ≥1.0` 作佐证）被**持出集证伪** ⇒ 判定链**据此未动**（登记 `docs/DEBT.md` **D-42**；预注册与原始测量 `D:/_lg_survey/P1-7-PREREG-noise-step-2026-10-01.md`）。

## 3. 源码地图

| 路径 | 是什么 |
|---|---|
| `voiceguard/forensics/` | 层1 检测器 + 注册表（`registry.py`）；**数检测器只信这里** |
| `tools/check_refs.py` | **交付文档引用校验器**（2026-09-30，正本）：把面向评委文档里的「`文件:行号`」回源码逐条核（文件存在 / 行范围 / 锚词 / 续写形式），`--self-check` 喂假引用必须被抓到、`--list-docs` 给 ⑥ 自报清单。**与交付区 `_check_refs.py` 逐字节同一份**（preflight ⑥ 硬断言：两处各自漂移即红）。起因＝一次"文档自称全通过、实则整体漂移"（`docs/DEBT.md` D-40）|
| `tools/check_pdf_payload.py` | **交付 PDF 的图像负载判据**（2026-09-29）：纯标准库扫 `/Subtype /Image`，报 `pages` / `image_objects` / `image_bytes`；`--self-check` 自证会红。**接在 `tools/preflight.py` ⑤ 作第二层**（下限 200 个 / 1 MB）。起因＝一次 `exit 0`、体积只小 36%、页数对的**静默丢图坏件**（1,908 → 34 个图像对象）⇒ `docs/DEBT.md` D-37；守卫 `tests/test_pdf_payload.py` |
| `voiceguard/forensics/lighting.py` · `color_edge.py` · `noise.py` | **观察级**检测器（`register(..., default=False)`，B 档默认关）：光影一致性 / 色彩过渡（官方 `loreal-topic2-details.md:48` / `loreal-topic2-details.md:50` 点名）＋ **噪声残差一致性**（`noise`，2026-10-01 自建、**非官方点名** —— 它**只作观察**：接进判定会拿误报换漏判，见 `docs/DEBT.md` D-42）；只写附四观察栏 |
| `tools/calib_lighting_color.py` | 标定集生成器 → `samples/calib/`（4 张：平色 / 全图暖色 / 同源网格 / 异源拼接） |
| `voiceguard/report/ledger.py` | 两栏账本（可疑证据 / 免责归因）+ 三档风险 + 免责码 |
| `voiceguard/agent/advisor.py` + `voiceguard/rules/rules.yaml` | 处置决策卡（措辞在 YAML，代码只做映射） |
| `voiceguard/semantic/explain.py` | F3 语义解释；`_TEMPLATE`(`voiceguard/semantic/explain.py:30-39`) 就是"严禁输出真伪结论"的**护栏本体**；`ask=` 注入口在 `voiceguard/semantic/explain.py:77` 附近 |
| `voiceguard/__main__.py` | CLI；`--explain` / `--agent` / `--observe` **三者默认关闭**；收图 + `--text` + `--comments-file` |
| `guard.py` | 主编排 |
| `demo/` | 交互面（2026-09-21 建）：`serve.py`（纯标准库本地服务）+ `index.html`（单页，零外链）+ `README.md`（运行指南）；守卫在 `tests/test_demo_page.py` |
| `tools/make_appeal_pack.py` | **申诉自证包导出器**（2026-09-25）：消费一份已完成报告 → `APPEAL.md` + `appeal.json` + `SHA256SUMS.txt` + `files/report.{md,json}`。**零新判定、零时钟、零网络、不含图像字节**；左栏非空而右栏为空时 `AppealRefused`（exit 3）。守卫在 `tests/test_appeal_pack.py`（13 项，含 AST 纯度扫描） |
| `experiments/` / `tools/` / `tests/` | 跑分与红队 / 打包与口径检查 / 测试 |
| `D:/deliver/compete/` | PPT、视频脚本、赛题原文、独立评审意见（不在本仓库内） |

## 4. 铁律（违反即返工）

1. **口径写于测量之前**：结果不好看**只能改协议并留档**，**绝不许改检测器阈值**。
2. 不许为让样本过关挪阈值 / 放宽口径。
3. **判定权归算法**：模型永不输出"伪造 / 篡改 / 真假 / 拼接"结论。
4. **隐私**：只用合成样本（`samples/base_neutral.jpg`）；**绝不上传真实人物照片 / 证件类图片**。
5. 密钥不进仓库（值一律写 `[REDACTED]`）。
6. 外部模型（含你）只提供解释与建议，**不参与判定链**。
7. 报指标必须同时给**点估计与统计上/下界**（误报给上界、检出给下界）。
8. **数字口径单一来源** = `tools/check_number_consistency.py`；交付包黄金 sha256 不许随手改 —— 真改了必须跑 `tests/test_rebuild_from_scratch.py`。
9. 产物必须"能从冻结语料重放"；**生成器脚本里烙死的旧口径＝真漂移**，必须连生成脚本一起改。
10. **无指令不动无关代码**：旧残留只登记、不顺手重构。

## 5. 验证命令（canonical，不要加 `-q`）

```bash
cd /d/LorealGuard
env -u PYTHONPATH .venv/Scripts/python.exe -m pytest tests/
```

- 命令跑过 ≠ 验证过：**假通过**要核对被判定的字段确有值；**一条不会红的检查等于没有检查**。
- `| tail` 会吃掉上游退出码，别用它判成败。

## 6. 当前待决（用户的决定，别替他定）

- **产品交互面**：✅ **2026-09-21 已决并已做** —— 选 B（本地服务 + 单页），入口＝图 + 文案 + 评论，`demo/` 已落地。历史理由与分歧记录在 `D:/deliver/compete/loreal-interface-decision.md`（含 9/21 更正块）：跳过只读静态页（没有输入，视频里只能翻页）；否决 C（新增编排层＝动架构 + 现场强依赖模型）；`ask=` 注入口的"0.5 天拿 C 的 80%"**被高估**——`ask` 是单次注入、`build_question`(`voiceguard/semantic/explain.py:42-59`) 是写死模板，而"严禁输出真伪结论"的护栏正写在那个模板里 → **放开提问面＝放开护栏**。
- **`--explain` 默认值**：CLAUDE.md 原 §7 建议"默认开"，**实际做成默认关**（页面勾选框）。理由：F3 唯一网络出口是百炼托管口（`voiceguard/semantic/vl.py:40` + `voiceguard/config.py:22`），默认开＝每次演示都依赖网络 + key + 上传。这是一处**有意的偏离**，不是遗漏。
- **物证口径落差**：包内 `EVIDENCE/red_team.json` 写 `"model": "deepseek-v4.1-flash"`（`voiceguard/config.py:44-45` 标"许可口径未核实"），包 README 却称"全部图像与文案均由程序合成"、`generators/` 里**没有**红队语料生成器，而仓库 `samples/make_red_team.py:37` 今天重跑会写成 `qwen3.8-27b` → 生成器与冻结物证对不上。
- **Demo 前端改版与成片一致性（2026-10-03）**：✅ **已闭环** —— `demo/index.html` 可读性改版后，成片的演示镜 D1–D5 已按新界面**同日重录**（五镜全 PASS），成片与现场页面**同一版界面**；顺带修正摇镜落点语义漂移（`docs/DEBT.md` **D-45 关闭 / D-47**）。

## 7. 下一步（2026-09-25 更新）

**已做完**：① `demo/` 那一页（本地服务 + 单页，图 + 文案 + 评论三个入口，默认零网络）；② **层 1 物证扩展（观察栏 B 档）** —— `lighting` / `color_edge` 落地，三处出口齐、4 条新守卫；验收档 `D:/deliver/compete/loreal-round11-verdict.md`，教学卡 `loreal-teaching-card-17-层1物证扩展与观察栏.md`；③ **申诉自证包**（2026-09-25）—— `tools/make_appeal_pack.py` + `tests/test_appeal_pack.py`（13 项守卫），把"右栏 + 坐标 + 哈希锚"从报告里的一节变成**可提交的纯文本产物**；四条不变量：零新判定 / 零自有时间戳 / 不含图像字节 / 不主张内容为真，左栏非空而右栏为空时**拒发**。报告第六节已改为指向它（`voiceguard/report/ledger.py:225-230`）。

④ **决赛材料④「AI Agent 设计文档」**（2026-09-27）—— `D:/deliver/compete/loreal-agent-design.md`（**279 行 / 19,115 B / 纯 LF**；行数＝`wc -l` 口径，2026-09-30 实测回填）：按赛题 `loreal-topic2-details.md:84` 的三分法作答（**数据流转 / 模型调用 / 业务处理闭环**）；**50 条 `文件:行号` 引用全部实测通过**（`tools/check_refs.py` 逐条核，2026-09-30 复算）（存在性 + 行范围，脚本可复跑）；含「已知空白与能力边界」7 条（对我们不利的照登）＋ §6 给评委的复核命令。⚠️ 本轮 CC 亲跑 `Reached max turns (30)`、**零产物**，由 Hermes 起草并逐条核引用（实况见 `cc-dialogue/round27_cc_reply.md`，含一处措辞偏大的证伪与修正）。
**现态硬数（2026-09-29 换版后）**：全量回归 **527 passed / exit 0**（188.99 s，`output/_pytest_r39_final.txt`）；黄金包 **v4 = `lorealguard_testset_v4.zip`**，sha **`7eaa8ac3…`**（**228 文件 / 19,645,182 B**；v1/v2/v3 仍在盘上、未被动过；v4 新增 `EVIDENCE/generation_params.md`）；`tools/check_number_consistency.py` exit 0；**`tools/preflight.py` 6/6**（⑤ 现在有两层：字节+sha16 **＋** 图像负载；⑥ 文档引用可追 —— 正本=副本硬断言 ＋ 自证伪 ＋ 7 份交付文档 82 条引用）；PPTX **17,203,219 B / 19 slides / `0146fe0a463bcc03`**、PDF **3,968,499 B / `3d5c2289348f1187` / pages=19 / 图像对象 2,002（3,111,958 B）**（同日**第四次**重出：封面补齐官方模板三字段 —— 作品名称 / 赛道 / **队伍名称＝小白学习记**）；**回归 524 → 527**（+`tests/test_pdf_payload.py` 3 项；`--fix` 回填 9 处当前声称，历史档 0 处被误改）。

**已闭环（2026-09-29）**：① **官方 PPT 模板已解析**（两件完全相同：3 页骨架 / **0 处页数上限** / 字体推荐**思源黑体 CN** / 封面须注明**作品名称·赛道·队伍名称** / 正文须含**作品背景·技术实现方式·作品使用场景·未来完善方向四大板块**）；② **deck 四板块对齐**——四块内容本来都在，但**字面一处未点名** ⇒ 只改 `build_deck.py` 的 eyebrow（12pt 绿字那行）为「板块名 ｜ 页名」：P2 作品背景、P3+P5 技术实现方式、P6 作品使用场景、P18 未来完善方向；判定字段/数字/版式**一字未动**；③ **抓到一个静默坏件**：重出 PDF 时 `exit 0`、体积只小 36%、页数对，但**图像对象 1,908 → 34**（真因＝挂到上一轮 CC 遗留的 PowerPoint COM 实例，`tasklist` **看不到** `POWERPNT.EXE`）⇒ 新建 `tools/check_pdf_payload.py` **接进 preflight ⑤ 第二层** + 3 条守卫；④ `docs/DEBT.md` **D-37**（静默丢图）/ **D-38**（deck 换版 + 回归基线换 527）已登记。

**现在最该做的（2026-09-29）**：① **封面三字段**（作品名称 / 赛道 / **队伍名称**）—— 模板硬要求，**队伍名称待用户提供**；改完须再走一次重出链（`figs.py` → `build_deck.py` → `export_pdf.py` → `preflight` ⑤）；② **钉钉 Q2 细节 / Q4**（Q1 冲早鸟＝是、Q3 数据包要＝是 已答）；③ **round38 包收尾**（`EVIDENCE/generation_params.md` 实话版 / `pytest.ini` 入包 / round36_c 登记的 5 处残留）；④ **B2b 成片重录**（镜 6 须演最终包 sha）；⑤ 交付区顶层清理 ＋ `output/` 3 个遗留探针；⑥ **70+ 条未提交改动**（deny 闸门硬拒，待用户）。
**已闭环（09-27 23:03）**：**A1 = G3 ＋ G4 同批一次冻结**（共同决策见 `D:/deliver/compete/loreal-round23-decision.md`）。① **G4**：新增**边缘场景集 9 条**（`samples/make_edge_suite.py` → `output/edge/`，全部良性、期望上限写于测量之前），包 **v1→v2**（196→**224** 文件；GOLDEN `e3238bd6…` → **`1db12089…`**），并把 `output/edge/` **接进** `tests/test_rebuild_from_scratch.py` 的从零重造链（此前**不在链上**＝真缺口）；删掉与主集同 sha 的 `edge_no_exif`，理由如实登记在生成器 docstring（底图本身零 EXIF ⇒「剥离」是空操作 ⇒ 会造出同哈希样本对）。② **G3**：deck 新增**第 17 页「合规 · 开源与判定解耦」**（插在收尾页**之前**：`TOTAL 17→18`、`(1,17)→(1,18)`、NOTES 插一条），pptx / pdf / PNG 全部重出。③ **顺手更正一处事实错**：本文件 §2 第 1 条原称「`MODEL_AGENT` 在 `voiceguard/` 下零调用点」，**与源码冲突**（`voiceguard/agent/planner.py:320`、`voiceguard/semantic/vl.py:86`）—— F9 Agent 编排层**已落地**（`voiceguard/guard.py:22` 导入、`:508-509` 在 `if self.agent:` 分支调用、默认关闭）⇒ 口径改为「**判定链零模型调用 —— 模型只出现在判定之后**」，deck 新页 / 本文件 / 答辩卡三处同步。④ 回归网 **515 passed / exit 0**。

**已闭环（2026-10-01 · 噪声残差维度落地 ＋ 本轮两条登记）**：① **`noise` 以 B 档观察栏维度落地** —— 新文件 `voiceguard/forensics/noise.py`（`register(noise_detector, "noise", default=False)`）＋ `config.NOISE_CFG`；三处出口（`GuardPipeline(observe=)` / CLI `--observe` / 页面勾选框）**自动接通** —— `guard._observe_names()` 从注册表推导；**默认路径逐字节不变**（默认只跑 `ela`）由两道机制保证：① `rules.yaml` **从不引用** `forensics.<观察维度>.*`（正面控制：`forensics.ela.` 引用 16 次 —— 该不变式已由 `tests/test_observations_column.py` 钉死）；② `guard._judging()`（`voiceguard/guard.py:141-151`）挡兜底 `confidence`（`voiceguard/guard.py:432`）与证据表挑拣（`voiceguard/guard.py:447`）。⚠️ **2026-10-01 round42 实测更正**：本处此前写的「`_judging()` 把 `default=False` 整类挡在信号表外」**是错的** —— `build_signals`（`voiceguard/guard.py:170`）用 `_ran()`（`:127-133`，只看 `status=="ok"`、不看 `default`）⇒ 观察级跑了**照样进信号表**（`noise` 实测 32 键，`D:/_lg_survey/round42/probe_signals_r42.txt`）。守卫**并入既有用例**（`tests/test_observations_column.py` 的 `OBS`、`tests/test_cli.py:356`、`tests/test_demo_page.py:271/:272/:307` 的维度集合与 `+2 → +3`）⇒ **回归项数刻意不变**（不撞 D-34 的「加用例 ⇒ 冻结 deck 重出」级联）。② **它为什么只能作观察**：接进判定会在「拼接**后**再重编码」的样本上拿误报换漏判 —— 预注册修法被持出集**证伪**（`docs/DEBT.md` **D-42**）；在**未再压缩**区制它的分辨力是干净的（良性 7/7 ≈0 ／ 拼接 3/3 ≥+1.50）。③ **D-41**（ddagrab 量出的客户区含窗口标题栏，实测分界第 **62** 行、裁 62px）已登记，并如实标注「**未核实**是否已固化进录制链」（`tools/record_demo.py` grep 不到该常量）—— 不改代码（铁律 10）。④ 预注册与原始测量：`D:/_lg_survey/P1-7-PREREG-noise-step-2026-10-01.md`（§1 机制 / §2 已否掉的候选 / §6 期望 / §7 证伪照登）＋ `signal_probe.txt` / `noise_step.txt` / `heldout_result.txt` / `heldout/`。

⚠️ 演示观察栏请用 `samples/calib/calib_splice_cross_source.jpg`（默认样图上 `lighting` 会如实判"不适用"）。要看 **`noise`**（噪声残差一致性）请选 `samples/fw_splice.jpg` —— 它要求 ELA **先出偏亮区域**（`region_z_min=6.0`），标定集上它会**如实弃权**并给出原因。

**已闭环（09-25 20:38）**：PPT 的 pptx/pdf 已按 466 重出 —— 链路 `figs.py`（实时问 pytest 数出 466 项/45 文件 + 刷新 `img/_figs_stamp.json`）→ `build_deck.py`（**新鲜度守卫 OK**：5 图与 sidecar 同版、`regression_count==live`）→ `export_pdf.py`（PowerPoint COM）。产物：pptx 17,177,639 B / 15 slides，pdf 3,208,414 B。**像素读回**（第 13 页渲染图）＝ `466` ✓，同页 `196 / 18.7 MB / e3238bd6`。顺手修掉一处同页自相矛盾：答辩备注 `loreal-ppt/build_deck.py:597-598` 原写「5,597 / 420 / 差 13 倍」，与同页配图（5667/425/13.3）冲突，已改齐；全 pptx 真文字层复扫**零旧数残留**。⚠️ `fig_seal` 亮度 35.9 是深色印章设计使然（旧图同判据同为 35.9，已对照）。

**已闭环（09-27 17:10）**：**成片级验收（todo 108）** —— ① 亲跑实测：全片风险红 **恰 3 处**（镜2 两把红叉 32,431px + 镜9「开卷考试」6,417px）、
自带红 2 类（镜1 图表 16,303px / 镜4 产品页 `#ff6b6b`）按 D-18 排除、字幕 **83 条 / 首 0.000s / 末 179.000s / 覆盖 100.0% / 空档 0.0s / 重叠 0**；
② **常驻化**（CC round22，4.6 min，零改既有文件）：`tools/check_film_content.py`（12,281 B / 纯 LF）+ `tests/test_film_content.py`
（17,329 B / 纯 LF / 7 条用例，合成素材可证伪 + 真产物 opt-in 缺则 skip）；⚠️ 960×540 缩放口径下禁止段有 1–17px 散点 ⇒ 判据用结构性
`blobs(min_px=100)==0`（全分辨率实测为 0）；③ 回归网 508 → **515 passed / exit 0**（`--fix` 改 9 文件，含 3 个未预备份者，事后逐核＝纯数字替换）；
④ PPT/PDF 按 515 重出，**三层读回**：配方复现「N=515 重画 ⇒ 差异像素 0」→ pptx 内嵌图逐字节同 sha（`22428e3d…`）→ 渲染页可读 + PDF
`/SMask` 逐像素同 / 不透明像素 RGB 差 0。验收档 `D:/deliver/compete/loreal-round22-verdict.md`。
⚠️ 两处副作用记账：`docs/DEBT.md` 裸 LF 4→0（patch 归一）；D-28 一句话被 `--fix` 半更新（已修）。

**已闭环（2026-09-30 · 139+142+F3 一笔重打包）**：① **139**：本文件 `:104` 的旧读数（275 行 / 18,875 B / 46 条）
→ 实测真值 **279 行（`wc -l`）/ 19,115 B / 50 条**；② **校验器进闸**：`_check_refs.py` 正本入仓库 **`tools/check_refs.py`**
（交付区那份留作**逐字节同一份的副本**，preflight ⑥ 硬断言；文档清单由 `--list-docs` 自报）⇒ preflight **5/5 → 6/6**，
并做了**突变自证伪**（副本漂移 / 引用越界两种造假都当场红，回原件 sha256 复原）；③ **142**：仓库 `demo/README.md`
与交付区 `README-demo.md` **已对齐成逐字节同一份**（补「自有图上传通路」一节；此前仓库那份 09-21 旧版在源码包里 = 过期指南）；
④ **F3**：`experiments/` 进源码包白名单（官方数据包 README 的「复现（单行）」跑的就是 `experiments/*.py`，
此前**不在任何交付包里**）⇒ 源码包 **133 → 141 文件**；⑤ `docs/DEBT.md` **D-40**。
⚠️ **字节与 sha 不写在这里**：本文件**在源码包内**，声称包自身的字节/sha 就是循环引用（改它即改包）——
写死 sha 的只有**不在包内**的交付区文档（`SUBMIT.md` / `loreal-preflight-checklist.md`）。
**未动**：数据包 v4（`7eaa8ac3…`）/ **成片已换版**（当轮值）⇒ `39a372757e5686c4`（镜 6 配音重合成 +2% 语速 ⇒ 10.824s，原 `cb083a24f13db022` 作废，D-44）/ deck（pptx `0146fe0a463bcc03`、pdf `3d5c2289348f1187`）；
**回归项数刻意不变**（新增用例会撞 D-34 的口径级联与冻结 deck 重出 ⇒ 改为并入既有用例）。

**已闭环（2026-10-03 · Demo 前端可读性改版）**：① `demo/index.html` 视觉层重做（**837 行**；判定链与 20+ 条守卫字符串**一字未动**）：首屏四步流程条、**一键示例**（`#example`，只做 `#fill` + `#run` 两个 DOM 动作 ⇒ 与人工点选完全等价）、输入区 `grid2` 并排两列（左图右字）、三开关收进一张卡（长解释折进 `<details>`）、结果区**等级横幅**（左边框由 render() 取等级同色）+ 两栏**计数徽标** + 证据图点击按原尺寸看；② 实测：页面不变量 **77 passed**、全量回归 **527 passed / exit 0**（135.61 s，`output/_pytest_r48_demo_ui.txt`）、引用闸 **71 条 / 0 失败**（`LOCAL_MAX_MB` 333→387 等 **10 处**行号引用一并搬走，`demo/README.md`↔交付区 `README-demo.md` 仍**逐字节同一份**）、一键示例亲跑跑通（`fw_splice.jpg` + `t34m` ⇒ 关注级 / 左栏 2 条 / 横幅边框取到等级同色 `rgb(240,180,41)` / 流程条点亮）；③ **唯一换 sha 的是源码包**（字节与 sha 由**包外**文档记 —— 本文件在包内，D-40 不声称自身 sha）；数据包 v4 `7eaa8ac3a3cc3675`（重造后同 sha）/ 成片 / deck 三件均未动；④ `docs/DEBT.md` **D-45**（成片演示镜与现场界面不是同一版，待拍板是否重录）。
**已闭环（2026-10-03 · 成片按新界面重录 ＋ 摇镜落点语义修正）**：① **成片演示镜 D1–D5 重录**（`tools/record_demo_page.py --only D1..D5`，五镜全 PASS；VL 逐字读回 5/5、4/4、4/4、4/4、14/14）⇒ S04/S08/S11 三段重建（S04 **630 帧** / S08 **510 帧** / S11 **恰好 390 帧**）⇒ 母版重拼（E1 PASS：几何 / 时长 / 帧数 / 抽样 20 时刻 SSIM ≥ 0.99 / 解码路径自洽逐像素 0 差）⇒ 成片重出 **8,878,108 B / `1e5699db6206631c`**（179.000 s / 5370 帧 / 1920×1080 —— 三项与旧片相同，旧值 `8,718,928 / 39a372757e5686c4`）⇒ `tools/preflight.py` ④ 基线同步 ＋ 交付区同名件已同步；② **顺带抓到一个真缺陷并修掉**（`docs/DEBT.md` **D-47**）：观察栏摇镜的第二落点写成 `hs[hs.length - 1]`（最后一块）—— 两块时等价于「第二块」，10-01 起了第三维度 `noise` 之后**跳过中间的 `lighting`**（官方点名两维度少一个），而守卫当时钉的是**表达式**不是语义 ⇒ 照绿；三处同批改（页面改取 `hs[1]` / 守卫改**精确正向**断言 / 录制器期望串只取前两块 ＋ 哨兵），突变自证伪红→绿（按 sha256 字节还原 `a13762dd9b20ab27`）；③ 连带换版：D5 源片 1578×844 / 15fps / **217 帧** / 14.466667s ⇒ `S11` 砍头 14 → **22** 帧、`tests/test_film_falsifiability.py` 的 `SEG_BYTES` 快照 103533 → **103683** 同步；④ 常规闸：`preflight` **6/6**（⑥ 引用 **80 条**全通过）、成片与录制类守卫 **71 passed**、数据包 v4 / deck 两件**未动**。

**已闭环（2026-10-05 · 配音换云希 ＋ 字幕 `**` 真缺陷修复 ⇒ 成片重出）**：① **音色** 云扬（`zh-CN-YunyangNeural`，新闻播报腔）→ **云希（`zh-CN-YunxiNeural`）＋12%**（依据＝用户从 7 条试音里裁定；云希原生语速慢 ~8%，+12% 才 10/10 镜装得下，镜 4 余量 +0.14 s 最紧）；② **字幕真缺陷**：`voice/make_srt.py` 把口播原文（含 markdown `**`）**原样写进 SRT**（**当时**在 `:153`；同日二次修后该行已重写，见 **D-51**） ⇒ 8 处星号被烧进画面；修法＝写盘前 `re.sub(MARKDOWN, "", x)`（`MARKDOWN` 正则早存于 `:39`，零新符号）—— **时间轴零风险已实证**：83 条时刻码与修复前**逐字节相同**，全文差异恰 8 行纯剥星号；③ **换配音影响面**：`make_film_shots.py` 的 `plan()` 按「配音实测长 ÷ 槽位」**缩放闪动锚点**且**硬断言 `mp3 < 槽位`** ⇒ 必须重渲染 S01（锚点系数 0.9855 → **0.9510**）；S02–S10 的配音只用于校验、不参与排片 ⇒ 画面一字未动；④ **成片重出**：**8,791,419 B / `fa427b95071b3338`**（179.000 s / 5370 帧 / 1920×1080 —— **三项未变**；旧值 `8,878,108 / 1e5699db6206631c`）⇒ `tools/preflight.py` ④ 基线、交付区同名件、提交包三处同步；⑤ **取证（不是自述）**：配音轨是立体声，被我的脚本按单声道误读成「358 s」；改正后**10/10 镜的云希 mp3 精确落在各自槽位起点**（包络峰 0.94–1.11；云扬那批 0.19–0.46 且位置全错），成片音轨 ↔ 配音轨同窗包络相关 **+0.936**；⑥ 字节快照同步 `tests/test_film_falsifiability.py` 的 `SEG_BYTES` 103683 → **103800**（注释压回原行数 ⇒ `tools/make_film_segment.py:775` 等文档引用**零位移**）；⑦ 常规闸：全量回归 **527 passed / exit 0**、引用闸 **82 条 / 0 失败**；数据包 v4 / deck 两件**未动**。

**已闭环（2026-10-05 二次修 · 字幕锚真实句界 ＋ BGM 钉种子 ＋ S01 闪点归位 ⇒ 成片第 4 次重出）**：① **起因**＝用户看/听换配音后的成片报「**视频字幕和配音不对应**」；② **取证**＝成片内 `_film_subs.ass` 逐条 vs 逐镜实测音长 ⇒ **每镜末条字幕都晚于配音**，合计 **10.88 s**（镜 8 +2.12 / 镜 9 +2.07 最松）；**真因**＝`voice/make_srt.py` 旧口径把**整个槽位**按纯字比例铺满（自检还要求「末条 = 槽位末」），而槽位是排片窗口、**比配音长**（十镜留白 **10.9 s**：计划 179.0 ／ 实测口播 168.1）⇒ 字幕天然比配音慢；旧音色离槽位近看不出，换云希 +12% 后留白变大才暴露；③ **修法**＝字幕**锚在 edge-tts 实测句界**（`make_voice.py --all` 加 `--write-subtitles` 落 `shot{N}_{音色}.srt`；台本细句按纯字比例落进真实句窗、窗内按字数分真实时长、>3.5s 递归拆）；缺边界文件 ⇒ **退回旧口径 ＋ 告警 ＋ 非 0 退出**；④ **音频零风险实证**＝同文本/音色/语速下多带该参数，mp3 **逐字节不变**（全 10 镜复核 **10/10 同 sha256**）；⑤ **口径变更（数字全换）**：字幕 **83 → 75 条**、覆盖 **100.0% → 93.1%**、最大空档 **0.0 → 2.27 s（只落镜尾静音留白，镜内零空洞）**、最长单条 3.47 s ≤3.5 ✓、重叠仍 0；旧读数只属历史语境（**D-51**）；⑥ **守卫**＝`tools/check_film_content.py` 新增 `subtitle_voice_alignment()`（`last_off` / `gap_in_tail`）＋ `subtitle_stats` 加 `max_dur`；`tests/test_film_content.py` 旧用例**同函数改写**为 `test_字幕贴住配音_不悬空`（保 **527** 项）—— **自证伪实测**：旧 SRT ⇒ `last_off` 2.17 s（红）／新 SRT ⇒ 0.00 s（绿）；⑦ **顺带修掉两条**：**D-52** 成片 BGM 粉噪**无种子** ⇒ 音轨每次重拼都变、成片不可重放（违反铁律 9；本轮就被它绊过一次 —— 只改字幕却整段音轨不同、两版 PCM 相关 0.64）⇒ 加 `seed=20261005`，**连拼两次 sha 完全相同**；**D-51 补记** `make_film_shots.narration_anchor/plan` 的 `k = mp3 ÷ 槽位` 压缩（旧 SRT 的补偿）在新 SRT 下会**把描边闪放错位置** —— 镜 1「十七比一」真实 **13.504 s**，压缩后 12.842 s（早 0.66）／上一版 14.310 s（**晚 0.81**）⇒ `plan()` 不乘 k、**S01 重渲**（闪点 13.504→13.954 ✓ 与口播同点）；抓它的是**既有守卫** `tests/test_film_shots.py::test_产物_尾帧无描边残留`（`d_mid = 0` ⇒ 红）；⑧ **成片第 4 次重出**：**8,776,324 B / `ee2b2067f4c44057`**（179.000 s / 5370 帧 / 1920×1080 —— 三项仍未变；旧值 `8,775,477 / 2ad4a403782c69e0f82d`），S01 段 **133,730 B / `35fdfe24195773e0`**（旧 `2756e51f19eae567`）；⑨ 四处同步：`tools/preflight.py` ④ 基线、交付区同名件、提交包、源码包。

⚠️ **2026-10-03 护栏（下个会话别踩）**：`voiceguard/rules/rules.yaml` 的**全文 sha256 是红队靶子指纹**
（`docs/REDTEAM-PROTOCOL.md:33`）⇒ **改它任何一个字符**（含注释）都会让 `tests/test_red_team.py:104-110` 变红、
跑分器按 `:115-121` 拒出分；要合法改必须先按协议**重造对手样本**（数字全变，deck/PDF/成片连带）。
实测发现：`voiceguard/rules/rules.yaml:119` 的「PLANNED」注释**不只是过时** —— `R-COMMENT-BOT-BURST`（`voiceguard/rules/rules.yaml:111`）是**不可达规则**
（主引擎 `voiceguard/guard.py:410-411` 先跑、评论信号 `:417-419` 后合并；评论层只吃 `comment_rules`，见 `voiceguard/comments/evaluate.py:111`），
同类形态由 `C-REVIEW-*` 三条覆盖 ⇒ **零漏检、零误报**。已登记 `docs/DEBT.md` **D-46** + 答辩卡第 22 问；**本轮按纪律不修**。

**已闭环（2026-10-03 · 源码包补发三个 Windows 启动器 ＝ `docs/DEBT.md` D-48）**：① **解包实测发现真缺陷** ——
r50 源码包里**一个 `.bat` 都没有**（`tools/make_source_pack.py` 的 `WHITELIST_FILES` 只列 `.toml/.txt/.lock/.md/.ini`
与 `LICENSE`），而包内 `README.md:106-108` 与交付面 `RUN-GUIDE.md:69-71` 都点名 `guard.bat` / `run.bat` / `py.bat`
⇒ 解包者照指南点不到文件，且包内 `tests/test_acceptance_launcher.py`（守的正是 run.bat 本体，`tests/test_acceptance_launcher.py:34`）**7 failed**；
② 三处已修（白名单补三个 `.bat`；既有用例 `test_真实源码包按白名单打包且不含内部件` **并入**「README 点名件 ⊆ 包内文件」
的**关系**守卫 —— 并入而非新开＝不撞 D-34 项数级联；`test_run_bat_referenced_paths_exist` 按前缀跳过 `.venv\…`
**环境生成物**，否则评委解包跑测试仍红）；③ 实测：解包副本启动器守卫 **7 passed**（改前 7 failed），
自证伪＝挪走 `run.bat` 即 1 failed、原样还原（sha16 一致）即 passed；包体 **142 → 145 文件**；同类扫描
（包内 5 份文档里以反引号点名的仓库件逐条核包内文件）⇒ README / CLAUDE **零缺件**、**无同族缺陷**。
⚠️ **新登记 `docs/DEBT.md` D-49（如实登记，未修）**：包内跑**全量** pytest ＝ **18 failed / 501 绿 / 9 skipped**，
18 条全部落在这 5 个模块（`test_submission_pack` 8 / `test_red_team_report_reproduces` 5 / `test_p5_comment` 3 /
`test_number_consistency` 1 / `test_rebuild_from_scratch` 1），真因＝它们要的是**交付面 / 本机生成语料**
（`output/`、`dist/`、交付区 doc —— `make_source_pack.BANNED_DIRS` 明确排除，且**不该**随源码包发）
⇒ 只承诺「**解包后启动器守卫 7/7 绿**」，**不承诺「解包后全量绿」**。是否给这 5 个模块加「缺具体外部语料则
**显式 skip**」的闸（配哨兵防 skip 吞真红）＝**待用户拍板**。

**已闭环（2026-10-05 · 引用闸覆盖面缺口：包内两份内部文档 80 条断引用 → 0）**：① **起因**＝用自家尺子
（`tools/check_refs.py`）去核**不在默认清单里的**两份包内文档：`CLAUDE.md` **61 条 / 46 条失败**、
`docs/DEBT.md` **114 条 / 34 条失败** ⇒ 闸全绿、它们带病交付；② **断因**＝简写缺前缀（真身都在 `voiceguard/` 下）
＋ 真漂移：语义护栏 `_TEMPLATE` 的现址是第 30-39 行（此前全称被写成第 3000 行）、`_hold_scan(` 定格守卫在第 224 行、
`--rate` 默认值在第 215-219 行、`_card_filter()` 定义在第 749 行、`RUN-GUIDE.md` 第 153 行越界（该文件共 95 行）；
③ **修法**＝一次性脚本（反引号锚定 ＋ 逐条计数断言 ＋ 先断言后落盘）改写 **44 处前缀 ＋ 23 处字面**，
行尾与行数（169 / 1149）未变；④ **顺手修掉尺子两类假阳性**（切片 `xs[:200]` 曾被当续写引用；文档 `_run()` 对
源码 `def _run(` 曾被判漂移）—— 两条都进了 `--self-check` 断言；⑤ **补闸**＝并入既有用例
`tests/test_source_pack.py::test_真实源码包按白名单打包且不含内部件`（带**阴性对照**：喂假引用必须报错），
并入而非新开 ⇒ 回归项数仍 **527**；⑥ **实测**：两份 **61/0** 与 **114/0**、`preflight` **6/6**、
引用闸 7 份交付文档仍 **82/0**、口径闸 exit 0；**唯一换 sha 的是源码包**（145 文件未增删；字节与 sha 由
**包外**文档记 —— 本文件在包内，D-40 不声称自身 sha）；数据包 v4 / 成片 / deck 三件**未动**；⑦ `docs/DEBT.md` **D-53**。
**已闭环（2026-10-09 · 开源发布 ＝ GitHub `awy-china/lorealguard`）**：① 公开发布（Apache-2.0；内容＝**与源码包白名单同一份口径**：`output/` · `dist/` · `cc-dialogue/` · `_` 前缀探针 · `round*` 过程笔记**一律不进仓**，`.gitignore` 补了这五类）；② **CI 闸**：全量 pytest 里有 **19 条**判的正是交付面/本机语料（`docs/DEBT.md` **D-49**），GitHub runner 上必然红 ⇒ 新增 `tests/corpus_guard.py`（**缺具体语料 ⇒ 显式弃权并点名；在场 ⇒ 照旧真跑**），五个模块按**具体缺件**加闸；哨兵**并入**既有用例（`tests/test_rebuild_from_scratch.py` 的编码守卫）⇒ 收集数仍 **527**（不撞 D-34 的冻结 deck 重出级联）；③ 顺手：`docs/DEBT.md` 两条指向 `output/`（生成物、按设计不分发）的引用改成文字、`README.md` 加 CI/许可徽章 ⇒ `README.md:106-108`（两处引用同步）；④ **实测**：仓库全量 **527 passed / 0 skipped / exit 0**；「缺语料」副本：通过 **497** 例 ／ 弃权 27 例 ／ 失败 0（弃权生效、不吞真红）。
**已闭环（2026-10-10 · CI 徽章真绿 ＝ 4 轮正修）**：① 公开仓 CI 从「必然红」修到 **success** —— `tools/plate_render.py` 字体崩改**延迟到画字时**；`voiceguard/rules/rules.yaml` 用 `.gitattributes -text` 钉住行尾（它是**逐字节红队靶子指纹** ⇒ 独立取证＝git blob sha1 `4d737768…` = **28,257 B = CRLF**）；`_cjk_font` 补 POSIX 候选；appeal 路径改**仓库根拼绝对路径**；`tests/test_number_consistency.py` 补**漏掉的 `import pytest`**（否则我的弃权在 Linux 上是 `NameError` —— Windows 走不到那行）；全程**不新增用例**（收集数仍 **527**，不撞 D-34 的冻结重出级联）。② **第 4 轮 run** `37956823624` = **success**：Linux 子集**通过 358 例 ／ 弃权 29 例**，**12 个平台专属模块逐条列名**跳过（**不冒充绿勾**）。③ 本机全量波动**如实登记**：同代码两跑 9 红 ／ 13 红（`x264` · `MemoryError` · 收集数读不出），涉事用例单跑**均 exit 0** ⇒ 判为**本机内存重压**（空闲 0.9–4.8 GB ／ 15.2 GB）；上次干净全量＝ **527 passed / 0 skipped**（`ea5e211` 态）。④ 源码包随本轮重造（字节／sha **由包外文档记** —— 本文件在包内，D-40 不声称自身 sha）。
