# 技术债登记（DEBT）—— 主动登记，不藏

> 为什么一个参赛项目要写这份文件：本项目的主张是「**把盲区和代价写在报告里**」。
> 如果代码里的债不登记，那主张就只活在 PPT 上。这份文件是**自我举报清单**，
> 每一条都必须能被复查（有文件、有命令、有判据）。

## D-01 `voiceguard/pipeline.py` 是遗留骨架（未删）

- **性质**：P0 阶段"一根线"草稿，只跑检测器倒裸 dict；无账本契约、无免责归因、无分级。
- **现状**：现行入口是 `voiceguard/guard.py::GuardPipeline`；本文件仅被
  `tests/test_contract.py`（兼容性）与 `tests/test_pipeline_legacy.py`（回归）引用。
- **不删的理由**：`analyze_forensics()` 是诊断脚本的便捷入口；删除会让兼容性断言失效。
- **代价**：新人可能照它改 → 已在 docstring 顶部写明 DEPRECATED + 指向 `guard.py`。
- **判据/复查**：`grep -rn "from voiceguard import pipeline\|pipeline\." --include=*.py .`
- **处置**：P1 合并进 `guard.py` 或彻底删除，并同步删掉两个测试文件的对应断言。

## D-02 `guard.bat` / `py.bat` / `run.bat` 是 Windows 专用入口

- **性质**：一键验收脚本只覆盖 Windows + git-bash 环境。
- **代价**：Linux/macOS 评审需手敲 `python -m pytest tests/`；
- **缓解**：`.github/workflows/ci.yml` 已给出等价的 Linux 步骤（且**已在本机逐条实测**，
  见交付文档；CI 本身未在 GitHub 上真跑过 —— 这一点不许说成"CI 通过"）。
- **处置**：P2 补 `Makefile`（`make verify` = 同一串命令）。

## D-03 依赖下限用 `>=`（可能跨大版本）

- **性质**：`requirements.txt` 全是 `>=`；实测装到 **opencv-python 5.0.0.93**（4.x→5.x 是大版本跳）。
- **代价**：同一份代码在不同机器上可能装出行为不同的版本。
- **缓解**：`requirements.lock`（`pip freeze` 逐字留档）+ `pyproject.toml` 里 `requires-python >=3.11`。
- **处置**：保持双文件；lock 由 `pip freeze > requirements.lock` 重生成（`tr -d '\r'` 去 CRLF）。

## D-04 阈值与规则**未经外部数据校准**

- **性质**：P2 数据集自造自判（"开卷考试"），规则阈值是在自造集上调出来的。
- **代价**：换到真实平台数据上，① 误报率上界可能失效。
- **这一点必须主动说**：它是 8 追问自测里唯一的 🔴 结构性弱点。
- **唯一解**：外部真实数据（合规获取路径待定），**补自造样本改善不了它**。
- **处置**：登记在案；答辩口径写死为「**我们报的是自造集上的可复算下界，不是平台实测。**」

## D-05 未接入的层（F3 语义解释 / F5 评论真实性 / P1 像素级生成痕迹）

- **现状**：`semantic/vl.py` 是接口壳；F5 约 50 行未接入；copy-move 类拼接检不出（已知盲区）。
- **代价**：P2 恶意侧 5 条漏报全部来自这些缺口（m01/m04/m05/m06/m08）。
- **诚实口径**：这些是**已公告的盲区**，不是"没测到"。
- **处置**：见 `output/p2/bench_report.md` 第七节（恶意侧弃权分解）。