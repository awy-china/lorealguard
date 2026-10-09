# LorealGuard · 交互式 Demo（本地运行指南）

一个能点的页面：拖入/选一条美妆内容 → 当场出**两栏账本**（可疑证据｜免责归因）+ 证据图 + 分级建议。
演示时**默认不需要网络**：判定链是纯算法，拔网线照出账本。

## 一、怎么跑（三步）

```bash
cd /d/LorealGuard
env -u PYTHONPATH .venv/Scripts/python.exe demo/serve.py
# 浏览器会自动打开 http://127.0.0.1:8765/
```

- 换端口：`--port 9000`；不想自动开浏览器：`--no-browser`。
- 只用 Python 标准库，**不需要装任何新依赖**，也不需要联网。

## 二、页面上有什么

| 区域 | 内容 |
|---|---|
| ① 输入 | 图片**两条通路并存**：① 用你自己的图（拖进框里或点「选择本地图片」）；② 下拉列表只列 `samples/` 下的程序合成样图。另有文案、评论列表、F3 语义解释开关 |
| ② 两栏账本 | 左：可疑证据（坐标 / 极性 / 形态 / 实测数字 / 出处）；右：免责与良性归因（为什么不怀疑你） |
| 证据图 | 算法叠图（`*_evidence_sheet.png`），与坐标一一对应 |
| Agent 处置建议 | 等级 → 平台侧 / 创作者侧两条建议 + 人在环上的位置 + 免责声明（措辞在 `voiceguard/rules/rules.yaml`） |
| F3 语义解释 | 开关打开后由开源权重档模型把**算法已定位**的疑点翻成人话；状态如实显示（ok / unavailable / error） |
| F5 评论区 | 给了评论列表才出现；只用给定文本，零大模型、零账号数据 |
| 元数据与标识核验 | 三态（对齐《标识办法》） |
| 能力边界 | 我们明确不做什么（主动声明局限） |

## 三、设计纪律（每条都有会红的测试守着）

1. **不动判定**：页面只调用 `voiceguard.guard.GuardPipeline`，不写阈值、不重算等级；
   页面上的每个数字都从 `output/demo/*.json` **读回来**渲染，HTML 里没有嵌任何判定结果。
   → `tests/test_demo_page.py::test_demo_layer_leaves_verdict_byte_identical`（逐字节比对判定字段）
2. **默认零网络**：不勾「F3 语义解释」时，核验全程不出本机；
   勾选后才会把图送往百炼（开源权重档 qwen3.8-27b），失败如实登记 `unavailable_*` / `error`，**账本照出**。
   → `test_runs_offline_with_dead_proxy`、`test_explain_defaults_off_and_degrades_honestly`
3. **图片两条通路，各自有闸**：
   ① **合成样图**（下拉）—— 白名单限定在 `samples/`（正则 + realpath 双重校验），
   `../`、绝对路径、越界后缀一律 400；② **本地图片**（拖入 / 点选）—— 后缀白名单 `.jpg/.jpeg/.png/.webp`
   + 8 MB 尺寸闸（前端 `demo/index.html:387` `LOCAL_MAX_MB`；服务端 `demo/serve.py:80-82` 另设
   base64 / body 上限，`ALLOWED_IMG_EXT` 在 `demo/serve.py:69`）。
   **上传不出本机**：图片只 POST 到本机 loopback（`demo/serve.py:549` 绑 `127.0.0.1`），
   落到一个随机命名的临时目录、核验跑完**立即删除**（`demo/serve.py:383-388` 的 `finally`），
   产物里连临时路径都被抹成展示用文件名（`demo/serve.py:314` `_scrub_upload_path`）。
   ⚠️ 唯一例外是**你主动勾选**「F3 语义解释」：那一路才会把图送往云端（详见 §六）。
   → `test_api_rejects_out_of_scope_images`、`test_report_endpoint_rejects_traversal`、
   `tests/test_demo_upload.py`（上传通路 ①–⑤）
4. **页面不自带结论**：零外链（无外链脚本/样式/字体），页内所有文本（含模型返回的解释）一律
   `textContent` 写入，不拼 `innerHTML`。
   → `test_page_has_zero_external_requests`、`test_page_never_bakes_verdict_values`

## 四、产物在哪

跑完一次核验，`output/demo/` 下会多出四个文件（`output/` 已在 `.gitignore` 里，不进仓库）：

| 文件 | 是什么 |
|---|---|
| `<tag>_report.json` | 完整报告（页面的数据源就是它） |
| `<tag>_report.md` | 同一份报告的可读版（双向账本 + 申诉指引） |
| `<tag>_decision.json` | Agent 处置建议（等级 → 措辞，措辞在 rules.yaml） |
| `<tag>_evidence_sheet.png` | 证据图 |

`tag` 由**输入内容**算出来（同输入 → 同文件名），所以演示也可以复算，不是一次性的。

## 五、验证

```bash
cd /d/LorealGuard
env -u PYTHONPATH .venv/Scripts/python.exe -m pytest tests/test_demo_page.py
```

## 六、已知边界

- 图片**两条通路**：合成样图（下拉，白名单限 `samples/`）+ 本地图片（拖入或点「选择本地图片」，后缀白名单 + 8 MB 上限）。本地图**不出本机**：只经 loopback 进临时目录、跑完即删。
- ⚠️ **但有一处例外要自己说清**：勾选「F3 语义解释」后，**那张图会被送往云端**（百炼托管口，开源权重档 qwen3.8-27b）。默认不勾 ⇒ 全程零网络；要用本地图演示，要么别勾这一栏，要么先确认这张图可以外发（铁律 4：真实人物照片 / 证件类图片一律不外发）。
- 评论区核验依赖账号维度的结论（注册时长/发帖间隔/IP）一律不判 —— 该数据不在本系统手里。
- 本页不做真假终审：等级是「风险等级」，不是「判决」。
