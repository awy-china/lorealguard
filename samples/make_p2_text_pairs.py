"""P2-T：**文案通路**配对集生成器（F4 功效宣称层的压力测试）—— 只生成"图 + 文案"的配对清单。

为什么必须补这一块（赛题 FOCUS 第一条点名"种草文章、评论区"）：
    P2 主集的 60 条**全部**是无文案工况，逐条免责归因里 60/60 都带 `NO_TEXT_PROVIDED`。
    也就是说：三道保守闸门、生理时间尺度、功效宣称号称这些**最有独创性的逻辑**，
    在 P2 主报告里触发 0 次。评委问"文案轴有什么数字"，主报告答不出来。

配对设计（这是本集唯一值得强调的方法学）：
    30 张**良性图**（P2 主集自带的 b01–b30，已经是真实创作者的多步发布链路）不变，
    每张图配两套文案：
        · V1 合规话术（含"因人而异/仅供参考"、只讲主观感受、不做绝对承诺）
        · V2 夸大功效（"7 天根除痘印""三天祛斑"）
    于是同一张图上**只有文案被改过** → 任何等级差异 100% 归因于文案通路，
    而不是"图和文一起换了"。这种配对设计比两组不同图各测一遍强得多，
    也是唯一能把"文案通路误报率"与"图像通路误报率"分开的数字。

⚠️ 与主集同一套纪律：
    · 承诺先写下、再实测（expect 在生成阶段就固定，跑完不许回头改）；
    · 跑不出来的如实登记为盲区，**绝不调阈值**；
    · 两条**故意留的盲区探针**（模糊时间表述）已公告：见 items 里的 text_blind_spot。

跑法（单行）：
    env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_text_pairs.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

P2 = ROOT / "output" / "p2"
SUITE = P2 / "suite"
OUT = P2 / "text_pairs.json"

#: 每套文案配几张图（3 张 → 每侧 30 条）：n=30 是**刻意选的**——
#: 零误报时 Clopper-Pearson 95% 上界 = 9.5% < 10%，与主集 40 条的小样本纪律同源。
IMAGES_PER_TEXT = 3
IMAGE_IDS = [f"b{i:02d}" for i in range(1, 31)]     # 只用良性图（见文件头"配对设计"）

# ============================================================ V1 合规话术（期望：不得 ≥ 关注级）
# 每一条都刻意踩在某个闸门或某个"容易被误伤"的点上，注释写明它测的是什么。
BENIGN_TEXTS: list[tuple[str, str]] = [
    ("用了一周，皮肤没那么干了，很滋润，个人感受仅供参考。",
     "保湿类下界为 0 → 门①：生理上可即时成立，永不判不可行"),
    ("每天早晚洁面后使用，清洁力温和，卸妆也卸得干净。",
     "清洁类下界 0；且『卸妆』『清洁』属弱宣称 → 门①+门②双重保护"),
    ("28 天下来肤色提亮了一些，具体效果因人而异。",
     "美白 28 天 + 承诺 28 天，恰好落在可信尺度上 → 门②（强度『中』）"),
    ("成分表里的烟酰胺浓度不低，保湿不错，因人而异。",
     "有宣称但**没有时间承诺** → 门③：无可对照周期即弃权"),
    ("先乳后水，配合卸妆使用，肤感舒服，不闷痘。",
     "『不闷痘』不含任何祛痘领域词 → 测词典精度（不该抽成功效宣称）"),
    ("用了 7 天，皮肤摸起来滑了，不过痘印还在，效果因人而异。",
     "最容易被误伤的一条：痘印(28天)+7 天，但宣称强度为『无』 → 门②弃权"),
    ("我是干皮，换季容易泛红，这款舒缓做得比较温和，主观感受。",
     "舒缓类下界 0 + 弱宣称 → 门①+门②；真实创作者最常见的表述方式"),
    ("连续用了一个月，细纹没有变多，抗老这种事得长期看，个体差异大。",
     "真实承诺(30 天 ≥ 抗皱 28 天) → 应判『可行』而不是弃权：测正向判定"),
    ("质地清爽，控油一般，一天下来 T 区还是会出油，真实反馈。",
     "控油(3 天)但无承诺 → 门③；且是**负面**评价，绝不该被当成营销话术"),
    ("淡纹需要长期坚持，我用了两周还看不出变化，仅供参考。",
     "淡纹(28 天)+两周(14 天)：若没有门②，这条会被冤判 → 门②的活体样本"),
    # ⚠️ 这一条是**为门①专门造的**：跑完第一版才发现闸门分解表里「门① 下界为 0」是 0/0
    #    —— 因为门②（强度）在代码里**先短路**，而"保湿+弱强度"的组合全被门②截走了。
    #    要让门①真正被执行到，必须同时满足「强度 ≥ 强」**且**「类别下界为 0」。
    #    没有这条的话，"下界为 0 的类别永不判不可行"这条核心铁律在本集**零覆盖**。
    ("一秒钟彻底去除脏污，清洁力温和不紧绷。",
     "⚡门①活体样本：强度『绝对』(彻底去除)+清洁类下界 0 → 必须判『可行』、永不判不可行"),
]

# ============================================================ V2 夸大功效（期望：≥ 关注级）
# 每条都是"图可以完全没改、但话在生理上不成立"——像素层看不见的造假。
MALICIOUS_TEXTS: list[tuple[str, dict]] = [
    ("7 天根除痘印，素颜也能打。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "痘印依赖表皮更替(28 天)，承诺 7 天；强度『绝对』(根除)"}),
    ("三天祛斑，斑点彻底去除。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "美白淡斑 28 天，承诺 3 天；强度『绝对』(彻底去除)"}),
    ("7 天淡化色斑，28 天彻底美白。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "取**最短**承诺 7 天 < 28 天；同句里混一个真实周期，测是否被平均掉"}),
    ("5 天祛除红血丝，修复皮肤屏障。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "屏障修护保守下界 7 天，承诺 5 天——差得不多，测下界附近的分辨力"}),
    ("即刻祛痘，一晚见效。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "『即刻』= 承诺 0 天；祛痘类下界 3 天 → 0 < 3"}),
    ("7 天紧致提拉，细纹消失。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "紧致/抗皱均 28 天，承诺 7 天；强度『强』(紧致/提拉)"}),
    ("一周淡化痘印，皮肤白到发光。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "中文数字周期(一周=7)解析；痘印 28 天"}),
    ("28 天抗皱，第 7 天就能看到皱纹平复。",
     {"expect": {"min_tier": "关注级", "must_fire": "R-EFFICACY-TIMELINE-IMPLAUSIBLE"},
      "why": "打卡体『第7天』与『28 天』并存，必须取最短 —— 取长的就漏判了"}),
    # ---- 两条**故意留的盲区探针**：不是承诺，是公告 ----
    ("彻底祛除色斑，永不复发。",
     # ⚠️ v1 这里写的是 {"max_tier": "未触发"}，实测 59/60 —— 唯一那条"违规"是 t56m
     #    得到「提示级」，根因是它配到了带 AI 标识的 b19，图像轴合法命中
     #    R-AI-LABEL-DECLARED（第六条（一）：已声明 AI 加提示即可，**不是误报**）。
     #    这是**协议写错了**（承诺越过了图像轴），不是检测器错了 → v2 改为与良性侧
     #    同一约定「max_tier: 提示级」。v1 的 59/60 原样留在报告里，不覆盖。
     {"expect": {"max_tier": "提示级", "blind_spot": True},
      "why": "**已知盲区**：无任何时间承诺 → 门③弃权（设计内：没有可对照的尺度就不出结论）",
      "text_blind_spot": "无周期承诺的绝对化宣称：本层刻意弃权，交由平台侧规则处理"}),
    ("半个月祛除色斑，斑点淡得快。",
     {"expect": {"max_tier": "提示级", "blind_spot": True},
      "why": "**已知盲区**：『半个月』不在周期正则里（中文数字表无『半』）→ 解析不到承诺 → 弃权",
      "text_blind_spot": "模糊时间表述（半个月 / 一星期半 / 十来天）当前解析不到，属**真缺口**，需补正则"}),
    # ⚠️ 与上一条良性样本**成对**：同样的"绝对强度 + 零下界类别"，但这次它出现在一条
    #    **夸大文案**里。期望是**仍然不触发** —— 因为这是"下界为 0 的类别永不判不可行"这条
    #    铁律的承诺。把"宁可漏判不可冤枉"写成一条**会被跑到的刹车测试**，而不是一句口号。
    ("即刻彻底去除角质，一次就干净。",
     {"expect": {"max_tier": "提示级", "blind_spot": True},
      "why": "**设计内弃权**：清洁/去角质下界为 0（物理过程即时成立）→ 门① 跳过，永不判不可行。"
             "⚠️ 这是**主动选择漏判**：哪怕文案看着夸张，只要类别下界为 0 就不出结论",
      "text_blind_spot": "零下界类别的绝对化宣称：刻意不判（误伤代价 > 漏判代价），已公告"}),
]


def build_manifest() -> dict:
    items: list[dict] = []
    n = 0
    for i, (txt, note) in enumerate(BENIGN_TEXTS):
        for j in range(IMAGES_PER_TEXT):
            img = IMAGE_IDS[(i + j * 10) % len(IMAGE_IDS)]
            n += 1
            items.append({
                "id": f"t{n:02d}b", "variant": "benign_text", "image_id": img,
                "file": f"{img}_*.jpg", "text": txt,
                "expect": {"max_tier": "提示级"},
                "why": note,
            })
    for i, (txt, meta) in enumerate(MALICIOUS_TEXTS):
        for j in range(IMAGES_PER_TEXT):
            img = IMAGE_IDS[(i + j * 10) % len(IMAGE_IDS)]
            n += 1
            items.append({
                "id": f"t{n:02d}m", "variant": "malicious_text", "image_id": img,
                "file": f"{img}_*.jpg", "text": txt,
                "expect": meta["expect"], "why": meta["why"],
                **({"text_blind_spot": meta["text_blind_spot"]} if "text_blind_spot" in meta else {}),
            })
    return {
        "name": "P2-T 文案通路配对集（同一张良性图 × 两套文案）",
        "version": "v3",
        "protocol_revisions": [
            {"v": "v1", "result": "承诺兑现 59/60",
             "violation": "t56m（探针『彻底祛除色斑，永不复发。』× 图 b19）得「提示级」，"
                          "违反 v1 写下的 max_tier=未触发",
             "root_cause": "图 b19 自带 AI 标识声明 → 图像轴合法命中 R-AI-LABEL-DECLARED；"
                           "按《标识办法》第六条（一），已声明 AI 加提示即可，**这不是误报**。"
                           "根因是**协议把承诺越过了图像轴**，不是检测器错了。",
             "fix": "v2 把探针的 expect 改为 max_tier=提示级（与良性侧同一约定）；"
                    "**不动任何检测逻辑、不动阈值**；v1 的 59/60 原样留档在本报告里。"},
            {"v": "v2 → v3", "result": "闸门分解表显示「门① 下界为 0」触发 **0/0**",
             "root_cause": "门②（强度 < 强）在代码里**先短路**，所以「保湿+弱强度」的组合全被门②截走；"
                           "要让门①真正被执行到，必须同时是「强度 ≥ 强」**且**「类别下界为 0」——"
                           "而 v2 的文案里没有这种组合，于是「下界为 0 的类别永不判不可行」这条核心铁律"
                           "在本集**零覆盖**。",
             "fix": "**只加样本，不动逻辑**：补 1 条良性（绝对强度 + 清洁类，应判『可行』）"
                    "+ 1 条恶意探针（同样组合，期望**仍然不触发**）。注意新探针的期望是「不触发」，"
                    "所以这一步是**把承诺写得更严**，不是放宽。"},
        ],
        "generated": date.today().isoformat(),
        "design": (f"{len(IMAGE_IDS)} 张良性图（P2 主集 b01–b30，真实创作者多步发布链路）× "
                   f"每张配 V1 合规话术与 V2 夸大功效 → 每侧 {len(BENIGN_TEXTS) * IMAGES_PER_TEXT} 条。"
                   "同一张图只改文案，等级差异 100% 归因于文案通路。"),
        "counts": {"images": len(IMAGE_IDS),
                   "benign_text": len(BENIGN_TEXTS) * IMAGES_PER_TEXT,
                   "malicious_text": len(MALICIOUS_TEXTS) * IMAGES_PER_TEXT,
                   "texts_benign": len(BENIGN_TEXTS), "texts_malicious": len(MALICIOUS_TEXTS),
                   "blind_spot_probes": 3 * IMAGES_PER_TEXT},
        "why_n": (f"零误报时 n={len(BENIGN_TEXTS) * IMAGES_PER_TEXT} 的 95% 上界 = "
                  f"{(1 - 0.05 ** (1 / (len(BENIGN_TEXTS) * IMAGES_PER_TEXT))) * 100:.2f}% < 10%"
                  f"（与主集 40 条同一套小样本纪律）：样本量不是凑的，是按承诺倒推的。"),
        "how_to_reproduce": [
            "env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_text_pairs.py",
            "env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_text_bench.py",
        ],
        "privacy": "只引用 P2 主集已有的程序合成样图；不新增图片、不收集任何真实创作者文案。",
        "scope": "这是**我们自己的**测评集，不是平台真实数据 —— 结论只在本集内可复算（见 docs/DEBT.md D-04）。",
        "items": items,
    }


def main() -> int:
    if not SUITE.exists():
        print(f"缺样本目录 {SUITE}（先跑 samples/make_p2_suite.py）")
        return 2
    d = build_manifest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    c = d["counts"]
    print(f"{c['benign_text']} 条良性文案 + {c['malicious_text']} 条恶意文案 "
          f"（{c['images']} 张图 × 2 套文案；含 {c['blind_spot_probes']} 条盲区探针）")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())