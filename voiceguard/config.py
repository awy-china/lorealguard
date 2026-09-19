"""LorealGuard · 全局配置

一切"可调的东西"集中在这里：API 端点、模型名、检测器参数、融合权重、阈值。
上层代码不允许散落魔法数字 —— 标定只在 baseline 集上做，改这里就够了。

⚠️ 密钥不进仓库：只从环境变量 / 本机 Hermes 的 .env 读取（赛题规则6：提交成果须开源部分内容）。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------- 路径 ----------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
SAMPLES_DIR = PROJECT_ROOT / "samples"
DOCS_DIR = PROJECT_ROOT / "docs"

# ---------------- 层2/层3：阿里云百炼（DashScope 国内站） ----------------
# 必须国内站：免费额度只在华北2(北京)；国际站是另一套账号体系
BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_KEY_ENV = "DASHSCOPE_API_KEY"
# 本机现成的 key 来源（Hermes 的 .env），仅本地开发用；仓库内绝不落盘
LOCAL_ENV_FILES = [
    Path.home() / "AppData/Local/hermes/.env",
    PROJECT_ROOT / ".env",
]

# 语义层（层2）：只解释已定位的疑点，不判真假
MODEL_VL = "qwen3-vl-plus"
MODEL_VL_BUDGET = "qwen3-vl-flash"      # 额度独立；主模型额度耗尽时改 config 一行即可切

# Agent 层（层3）
# ⚠️ 合规待办（决赛设计文档必写）：赛题要求"必须用开源大模型"。
#    Qwen3 系 = Apache-2.0 开源权重，无争议；DeepSeek 系需在文档中引用其开源权重许可。
#    若答辩被追问，一行切到 MODEL_AGENT_SAFE。
MODEL_AGENT = "deepseek-v4.1-flash"
MODEL_AGENT_SAFE = "qwen3.8-max"
API_TIMEOUT = 90
API_RETRY = 3          # CN 网络间歇 RST 是常态，别把网络失败当代码 bug

# ---------------- 层1 检测器参数（手册 1.4 推荐值，待 baseline 重标定） ----------------
ELA_CFG = {
    # ⚠️ 实测陷阱（2026-09-18，4032×3024 相机图 + 局部磨皮样本）：
    #   q=95（≈相机自身质量）→ 磨皮区 ELA 反升，极性变"偏亮"，会被误判成拼接！
    #   q=90/80/75（低于原图质量）→ 磨皮区转为"偏暗"，方向正确。
    #   规则：**重压缩质量必须低于原图自身质量**，否则极性反转。
    #   P1 待办：从 JPEG 量化表反推原图质量，自适应取 q = q_orig − 5~10。
    "quality": 90,             # 主重压缩质量（工作点）
    "quality_cross": 75,       # 交叉验证质量
    "q_reversal_warn": 95,     # ≥ 此值报警：易与相机质量重合导致极性反转
    "block": 16,               # 统计块；短边<600 时降到 8
    "robust_z": 4.0,           # 稳健 z 阈值（待用 baseline 集按误报率 5% 重标定）
    "min_area_ratio": 0.005,   # 最小可疑区域面积占比
    "w_pos": 1.0,              # "更亮"残差权重 —— 拼接/亮度调整方向
    "w_neg": 0.33,             # "更暗"残差权重 —— 优先归因美颜磨皮（手册 1.6.1）
    "score_full_area": 0.15,   # 加权可疑面积占到 15% 即打满分
    "viz_scale": 15.0,         # 固定放大系数（仅可视化）
}

# ---------------- 场景先验：平台二次压缩（手册 1.6.3） ----------------
PLATFORM_SHORT_SIDES = (1080, 1350, 1440, 720, 640)   # 常见平台输出短边
PLATFORM_PENALTY = 0.3                                 # 判定为平台重压缩 → 分数乘 0.3
LOW_CONF_PENALTY = 0.3                                 # 无 JPEG 史等失效场景


def get_api_key() -> str | None:
    """按 env → .env 的顺序找 key。返回 None 表示没配。"""
    k = os.environ.get(BAILIAN_KEY_ENV)
    if k:
        return k.strip()
    for f in LOCAL_ENV_FILES:
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith(BAILIAN_KEY_ENV + "="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
        except OSError:
            continue
    return None