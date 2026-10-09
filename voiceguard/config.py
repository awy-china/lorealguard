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

# ---------------- 合规：赛题硬要求「必须用开源大模型而非闭源模型」 ----------------
# 2026-09-20 核实（HF 官方权重页）：Qwen/Qwen3.8-27B → license:apache-2.0，
#   且 tags 含 image-text-to-text（文/图都能吃）→ **一档覆盖 F3 语义解释，不必另找 VL**。
# 2026-09-20 实测：百炼兼容口调 qwen3.8-27b 读我们自己的合成样图，返回正常。
# 纪律：交付运行时可调用的模型**只能**来自 OPEN_WEIGHT_MODELS —— 白名单写在代码里，
#   并由 tests/test_semantic_explain.py 的守卫钉死，不靠答辩时自觉。
OPEN_WEIGHT_MODELS = {"qwen3.8-27b": "Apache-2.0（Qwen/Qwen3.8-27B）"}
MODEL_AGENT = "qwen3.8-27b"          # Agent 层（层3）：交付运行时唯一允许的档
MODEL_SEMANTIC = "qwen3.8-27b"       # F3 语义解释层：同档；只解释已定位疑点，永不参与判定

# ⚠️ 以下档只允许用于**离线研究**（红队造数据 / 内部 PPT 质检），不得进入交付运行时：
#   · deepseek-v4.1-flash —— 开源权重许可口径未核实（早期造对抗语料用过，已登记）
#   · qwen3.8-max / qwen3.8-flash —— 托管闭源档
#   · qwen3-vl-plus / qwen3-vl-flash —— 托管档、许可待核（仅内部证据图质检）
RESEARCH_ONLY_MODELS = {
    "deepseek-v4.1-flash": "许可口径未核实（离线造对抗语料用）",
    "qwen3.8-max": "闭源托管档",
    "qwen3.8-flash": "闭源托管档",
    "qwen3-vl-plus": "托管档，许可待核（内部质检用）",
    "qwen3-vl-flash": "托管档，许可待核（内部质检用）",
}
# ⚠️ 纪要：曾经的 MODEL_AGENT_SAFE = "qwen3.8-max" 是一张**错的安全网** ——
#    Max 档是闭源托管，答辩被追问时"一行切过去"只会更不合规。该常量已删除：
#    现在的安全档就是 MODEL_AGENT 本身（Apache-2.0 开源权重），不存在第二个名字。
MODEL_VL = "qwen3-vl-plus"           # 保留给离线质检脚本（qa_vl.py）；非交付运行时路径
MODEL_VL_BUDGET = "qwen3-vl-flash"   # 额度独立；主模型额度耗尽时改 config 一行即可切
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

# ---------------- 层1 观察栏检测器参数（预注册 v2，2026-09-25） ----------------
# ⚠️ 落地形态 = **观察栏（B 档）**：只出数字与坐标，**不进左栏物证、不进 tier**。
#    机制（不是自觉）：`rules.yaml` 的所有 when: 只引用 forensics.ela.* / text.* /
#    review.* / provenance.* / semantic.*。只要**不把** forensics.lighting.* /
#    forensics.color_edge.* 写进规则表，规则引擎就不可能命中它们 ⇒ 判定面逐字节不变。
# ⚠️ 本文件里的数字都是**预注册 §2 写死**的工作点；代码里不许再出现裸判据数字。
LIGHTING_CFG = {
    "grid": 8,                  # 分块＝短边/8 → 8×8 网格（大到方向可估、小到能定位）
    "patch": 64,                # 块内取中心 64×64 估 Sobel 方向（块<64 时取整块，不越界）
    "theta_orth_deg": 45.0,     # 局部违背判据 Δθ > 45°（"正交光照"的经典分界，预注册 §2.1）
    "z_extract": 4.0,           # 块级幅值 m_b 稳健 z 的**提取**线（沿用 ELA 的 z=4/6 两层纪律）
    "z_trigger": 6.0,           # **触发**线：某区域 z_peak ≥ 6 才算"局部显著"
    "r_consistency_min": 0.35,  # 全图一致性 R ≥ 0.35：R 高＝本来就一致，局部违背才是证据
    "median_grad_min": 2.0,     # 全图 median(m_b) < 2.0 → 弃权（过曝/过暗/无纹理，测不出方向）
    # ⚠️ 以下两个值 **预注册 §2.1 未列**（已在回执 §七 登记）：不发明新口径，沿用/推导既有值 ——
    "min_area_ratio": 0.005,    # 区域最小面积占比：沿用 ELA_CFG 同名字段的既有值 0.005
    "z_score_full": 12.0,       # score 满量程 = 触发线 z_trigger(6.0) 的 2 倍（只影响 score，不影响触发）
}
COLOR_EDGE_CFG = {
    "k_sigma": 6.0,             # 候选线强度 S > k × median(S)：k=6 与 ELA 的 z=6 同量级
    "min_area_ratio": 0.005,    # 连通域面积比 ≥ 0.5%（滤掉细描边），预注册 §2.2
    "ring_px": 8,               # 跳变线两侧各扩 8px 环带：色温统计与纹理比都在环带里量
    "delta_e_min": 6.0,         # 两侧 (a,b) 均值差 ΔE ≥ 6.0（≈肉眼可辨色差下限）
    "texture_ratio_min": 2.0,   # 两侧 Laplacian 方差之比 ≥ 此值或 ≤ 其倒数 ＝ 纹理不连续
    "median_s_min": 1.5,        # 全图 median(S) < 1.5 → 弃权（单色/灰底，测不出色彩过渡）
    # ⚠️ 预注册 §2.2 未列（已在回执 §七 登记）：score 满量程 = ΔE 判据(6.0) 的 2 倍。
    "delta_e_full": 12.0,
}

# ---- 噪声残差一致性（P1-7 预注册增补，2026-10-01） ----
# ⚠️ 同 B 档约束：只出数字与坐标，**不进左栏物证、不进 tier**。
#    机制 = `rules.yaml` **从不引用** `forensics.noise.*`（信号表里其实有这些键 ——
#    `guard.build_signals` 只看 `_ran()`、不看 `default`，见 `noise.py` 自述与
#    `D:/_lg_survey/round42/probe_signals_r42.txt`；2026-10-01 round42 更正此前「挡在信号表外」的写法）。
#    `guard._judging()` 另挡兜底 confidence（`guard.py:432`）与证据表挑拣（`guard.py:447`）两处。
# ⚠️ 本维度**实测检验过并被证伪**：把它接进判定（要求 `R-SPLICE-SUSPECT-REVIEW` 以它为佐证）
#    会在「拼接**后**再重编码」的样本上同时失去佐证（两侧噪声场被一起同质化）⇒ 误报换漏判。
#    预注册与持出集结果：`D:/_lg_survey/P1-7-PREREG-noise-step-2026-10-01.md`（§6 期望 → §7 证伪）。
NOISE_CFG = {
    "hp_sigma": 1.2,            # 高通 = 灰度 − 高斯(σ=1.2)：只滤掉块级 DC，保留传感器/胶片噪声
    "block": 16,                # 分块尺寸沿用 ELA 的 16，但块内**残差定义不同 ⇒ 数值不可互读**：ELA＝重压缩差取通道最大后块内均值（短边<600 时块降为 8，`ela.py:157`），本量＝灰度高通残差(σ=1.2)取块内**中位**（`noise.py:89`）
    "inner_erode_px": 5,        # 框内域 = bbox 内缩 5px（避开边界过渡带，只量"真框内"）
    "ring_gap_px": 9,           # 框外环带**内**边界 = bbox 膨胀 9px（跳过边界本身）
    "ring_outer_px": 31,        # 框外环带**外**边界 = bbox 膨胀 31px（≈2 个 16px 块）
    "min_px": 80,               # 内域/环带各自的**最小像素数**，不足即弃权（不拿几十像素下结论）
    "step_hetero_min": 1.0,     # **预注册判据**：台阶 ≥ 1.0（框内噪声残差 ≥ 框外 2 倍）＝异源台阶
    "region_z_min": 6.0,        # 参照区门槛：沿用 ELA 触发线 z=6.0（只对"已显著"区域量台阶）
    "step_score_full": 3.0,     # score 满量程 = 判据(1.0) 的 3 倍（只影响 score，不影响任何措辞判决）
    "min_side": 64,             # 短边 < 64px → 弃权（框内域 + 31px 环带装不下）
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