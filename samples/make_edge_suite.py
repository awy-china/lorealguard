# -*- coding: utf-8 -*-
"""边缘场景集（G4）：9 张**程序合成**的边界输入 ＋ 逐条 sha256 清单 ＋ 每样本 README.txt。

为什么要有这一集
----------------
官方评分第 2 位问的是「测试数据质量是否满足提交格式，数据是否涵盖**边缘场景**」
（`D:/deliver/compete/loreal-topic2-details.md:21`，原词）。本集把这句原词落成 9 条
**可从冻结语料逐字节重放**的样本（铁律 9）—— 不新增检出目标、不动任何检测器阈值/
口径（铁律 1、2）。

它们**全是良性**（`label="benign"`／README 写「是否伪造：否」）：本集是「误报护栏」的
**证据**，不是检出靶。口径写于测量之前（铁律 1）：每条"应该判到哪一级"写在 `expect` 里，
**全部 = 上限「关注级」**（`关注级` = rank 2、`高风险` = rank 3 ⇒ **不得判违规**）。
本脚本只造图、落清单、打印**真实** sha256；它**不**跑检测器、**不**做任何判定。

纪律备忘
--------
⚠️ **铁律 9**：四个标定形态的样本**调用 `samples/calib_lighting_color.py` 的同名函数重造**，
    **不是**从 `samples/calib/` 把现成 jpg 拷进来 —— 拷进来的东西不叫"生成物"。
⚠️ **通道序陷阱（本脚本最容易静默出错的地方）**：`calib_*` 那几个函数产出的是
    **float32 / cv2(BGR) 序**的数组（它们的 `_jpeg` 用 `cv2.imencode` 落盘），而
    `samples/make_p2_suite.py:save()` 是 **PIL/RGB**。混用会把 `make_global_warm` 的
    暖色**静默**变成冷色 —— **清单里没有任何字段能暴露这件事**（sha256 只证明"两次一样"）。
    故按来源分流：**calib 家族 → `_save_cv2`（q92，与标定集同口径）；底图家族 → `save`**。
⚠️ 只用合成样本（铁律 4）；零网络；零新依赖（`cv2` / `numpy` / `PIL` 已有）。

⚠️ **stdout 编码兜底（下面那两行不是装饰）**：canonical 环境下本机 locale 是 GBK，
    末尾那行 print 里的 `⇒`（U+21D2）编不出来 ⇒ `UnicodeEncodeError`、**退出码 1**。
    产物其实已全部落盘（崩在最后一个 print），但"人手动跑"看到的就是一次失败 ——
    这恰恰是评委/复核者走的路径（`tests/test_rebuild_from_scratch.py:_run` 给子进程设了
    `PYTHONIOENCODING=utf-8`，所以从零重造链不暴露这个洞）。
    ⚠️ 只在本文件兜底；`samples/` 下另外几个生成器同类问题**不动**（铁律 10，另登记）。

如实登记：为什么不列「无 EXIF」那条
  我们的合成底图 samples/base_neutral.jpg 本身零 EXIF（实测 getexif() 条目数 0），
  再"剥离 EXIF"是空操作 ⇒ 产物与主集样本逐字节相同（同 sha256）⇒ 会造出同哈希样本对。
  故**不列**：本条边缘覆盖由主集全体样本天然承担，不需要一条重复样本来充数。
  （换任何替代算子都会再撞重复：`make_p2_suite` 已用过 case_rotate_slight / case_high_iso /
  case_global_warm 等，其中 case_rotate_slight 不吃 rng、重造即与 p2 样本同哈希。）

用法：
    env -u PYTHONPATH .venv/Scripts/python.exe samples/make_edge_suite.py

产物：
    output/edge/suite/<id>.jpg|png   9 张
    output/edge/suite/<id>.README.txt   每样本一份（官方 `:90` 四要素）
    output/edge/manifest.json        清单（条目键逐字对齐 output/p2/manifest.json）
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys

import cv2
import numpy as np

# ⚠️ 见模块头「stdout 编码兜底」：canonical 环境是 GBK locale，`⇒` 编不出来会让退出码变 1。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "edge" / "suite"
MANIFEST = ROOT / "output" / "edge" / "manifest.json"
sys.path.insert(0, str(ROOT))

from samples.calib_lighting_color import (      # noqa: E402
    make_flat_color, make_global_warm, make_grid_same_source, make_splice_cross_source,
)
from samples.make_p2_suite import (              # noqa: E402
    BASE, case_crop_rescale, case_watermark_bar, jpeg_roundtrip, load_base,
    resample_short_side, save,
)

#: 固定种子。⚠️ 本集的图其实**不依赖**这个种子（四个 calib 函数的随机数在函数内部
#: 各自写死 11/21/22/23/31，p2 的 `case_*` 也不用 rng）—— 留着是为了与 p2 同形，
#: 且任何一天有人加了一条**真用 rng** 的工况时，它是那个工况的锚点。
SEED = 20260927

#: 标定家族落盘质量。与 `samples/calib_lighting_color.py` 的 `_jpeg(q=92)` 同口径 ——
#: 观察栏在该质量下的标定数字（R=0.5413 / z=7.902 / ΔE=14.489）才对得上。
CV2_Q = 92


# ------------------------------------------------------------------ 落盘

def _save_cv2(img: np.ndarray, path: pathlib.Path, quality: int = CV2_Q) -> pathlib.Path:
    """**calib 家族专用**：数组是 float32 / **cv2(BGR) 序**，必须用 cv2 落盘。

    ⚠️ 绝不能改走 `save()`：PIL 按 RGB 解释同一块内存 ⇒ 暖色样本会变冷色，
    而清单里没有任何字段能暴露（见模块头「通道序陷阱」）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.clip(img, 0, 255).astype(np.uint8)
    if path.suffix.lower() == ".png":
        ok = bool(cv2.imwrite(str(path), arr))
    else:
        ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            buf.tofile(str(path))
    assert ok, f"落盘失败：{path}"
    return path


# ------------------------------------------------------------------ 工况（9 条，id 与变换写死）
# 每条 = 一个工况；`expect` 是**我们对产品的承诺**（与 p2 同一套词表）：
#   max_tier    —— 判定等级上限（良性样本的护栏；关注级 = rank 2 ⇒ 不得判违规）
#   blind_spot  —— 已知能力边界（写下来而不是藏起来；`docs/DEBT.md:291` D-12）

def case_flat_color(a, rng):
    return make_flat_color(), {"writer": "cv2", "ext": ".png"}


def case_global_warm(a, rng):
    return make_global_warm(), {"writer": "cv2"}


def case_grid_same_source(a, rng):
    return make_grid_same_source(), {"writer": "cv2"}


def case_splice_cross_source(a, rng):
    """异源拼接标定图。真值框取自造图函数自己返回的 `gt["bbox"]`（不另算一遍）。"""
    img, gt = make_splice_cross_source()
    return img, {"writer": "cv2", "bbox_xywh": [int(v) for v in gt["bbox"]]}


def case_inplace_q75(a, rng):
    return jpeg_roundtrip(a, 75), {"quality": 75}


def case_resized_q75(a, rng):
    return resample_short_side(a, 1080), {"quality": 75}


def case_tiny_64(a, rng):
    return cv2.resize(a, (64, 64), interpolation=cv2.INTER_AREA), {}


#: 原位 q75 的 D-12 口径（`docs/DEBT.md:291`）—— **完整写出**，原话照抄。
_D12 = (
    "原位降质重压缩（几何未变）会被推上**关注级**：这是 D-12 已登记的能力边界 —— "
    "四类良性编辑里只有它会被推上去，分界线落在「**几何是否变化**」上（同质量、只多一步"
    "改尺寸的对照 `edge_resized_q75` 信号反而更强，却被 `PLATFORM_RECODE` 拦下）。"
    "**我们主动说**：原位降质重压缩我们会给人看，但那是**复核提示，不是判违规** —— "
    "属能力边界，不是待修的阈值。"
)

CASES: list[dict] = [
    dict(id="edge_flat_color", label="benign", cat="AI生成视觉素材",
         desc="平色底图：观察栏 `lighting` 应**如实判「不适用」**（弃权），不许假装「无异常」",
         operation="make_flat_color() → 存 PNG（cv2 无损）", fn=case_flat_color,
         expect={"max_tier": "关注级"}),
    dict(id="edge_global_warm", label="benign", cat="种草内容",
         desc="整图暖色温＝良性调色（全局加性偏置，无局部跳变）",
         operation="make_global_warm() → 存 q92", fn=case_global_warm,
         expect={"max_tier": "关注级"}),
    dict(id="edge_grid_same_source", label="benign", cat="AI生成视觉素材",
         desc="同源九宫格拼版：光照方向/色温/纹理同源 ⇒ **无接缝**（拼版是正常创作）",
         operation="make_grid_same_source() → 存 q92", fn=case_grid_same_source,
         expect={"max_tier": "关注级"}),
    dict(id="edge_splice_cross_source", label="benign", cat="AI生成视觉素材",
         desc="异源拼接**标定图**：观察栏（B 档）会出真实接缝数字，**但不改判定** —— "
              "它不进左栏物证、不进规则表、不改任何判定字段",
         operation="make_splice_cross_source()[0] → 存 q92", fn=case_splice_cross_source,
         expect={"max_tier": "关注级"}),
    dict(id="edge_inplace_q75", label="benign", cat="种草内容", desc=_D12,
         operation="jpeg_roundtrip(base, 75)（尺寸不变）", fn=case_inplace_q75,
         expect={"max_tier": "关注级",
                 "blind_spot": "尺寸不变的低质重压缩会被推上关注级（D-12）：复核提示 ≠ 判违规，"
                               "属已登记的能力边界（见 docs/DEBT.md:291）"}),
    dict(id="edge_resized_q75", label="benign", cat="种草内容",
         desc="与 edge_inplace_q75 **同质量、只多一步改尺寸**的对照（几何变了 ⇒ 走平台重编码口径）",
         operation="resample_short_side(base, 1080) → 存 q75", fn=case_resized_q75,
         expect={"max_tier": "关注级"}),
    dict(id="edge_crop_80", label="benign", cat="种草内容",
         desc="裁剪 80% 再回原尺寸（良性编辑①，几何变了）",
         operation="case_crop_rescale(base, None) → 存 q93",
         fn=lambda a, rng: case_crop_rescale(a, rng),
         expect={"max_tier": "关注级"}),
    dict(id="edge_watermark_bar", label="benign", cat="种草内容",
         desc="底部加字幕条（良性编辑④，常见排版）",
         operation="case_watermark_bar(base, None) → 存 q93",
         fn=lambda a, rng: case_watermark_bar(a, rng),
         expect={"max_tier": "关注级"}),
    dict(id="edge_tiny_64", label="benign", cat="种草内容",
         desc="极小图（64×64）鲁棒输入：尺寸小到接近检测器的可用下限，须如实弃权而非硬判",
         operation="base 缩到 64×64（INTER_AREA）→ 存 q93", fn=case_tiny_64,
         expect={"max_tier": "关注级"}),
]
# ⚠️ **不列**「无 EXIF」那条：底图本身零 EXIF（`getexif()` 条目数 0）⇒ 再"剥离"是空操作
# ⇒ 产物与主集样本逐字节同 sha256 ⇒ 只会造出同哈希样本对。理由见模块头
# 「如实登记：为什么不列「无 EXIF」那条」。**不塞替代品，也不为它改任何口径。**


# ------------------------------------------------------------------ 四要素 README（单一口径）

def edge_readme_txt(item: dict) -> str:
    """官方 `loreal-topic2-details.md:90` 的四要素 ＋ 期望上限 ＋ 说明。

    ⚠️ 这是**唯一**的模板：本脚本把它落到 `output/edge/suite/<id>.README.txt`，
    `tools/make_submission_pack.py` 打包时**逐字节拷贝**它进包 —— 包内那份与仓库这份
    因此同源，第三方拿 `generators/` 也能重造出同样的 README（不另写第二套措辞）。
    """
    lines = [
        "LorealGuard 测试数据包 —— 边缘场景样本说明",
        "=" * 46,
        f"样本编号：{item['id']}",
        "来源平台：本仓库程序合成（无第三方数据、无真实人物影像）",
        f"样本类型：{item['category']}",
        "是否伪造：否",
        f"伪造方式：无（{item['operation']}）",
        "期望上限：不得判违规（本批是「误报护栏」的证据，不是检出靶）",
        f"说明：{item['desc']}",
    ]
    if item["expect"].get("blind_spot"):
        lines.append(f"公告盲区：{item['expect']['blind_spot']}  ← 我们**主动声明**这条属于已知边界")
    lines += [
        f"sha256：{item['sha256']}",
        f"字节数：{item['bytes']}",
        f"真值框：{item['gt_bbox_xywh']}  (x, y, w, h)" if item["gt_bbox_xywh"]
        else "真值框：无（本样本不含局部篡改区域 —— 它不是检出靶）",
        "-" * 46,
        "复现：env -u PYTHONPATH .venv/Scripts/python.exe samples/make_edge_suite.py",
        "校验：sha256sum -c SHA256SUMS.txt（在数据包根目录执行）",
        "说明：本集只用于核验「LorealGuard」的**误报护栏**；样本均为程序合成，不含真实人物/个人信息。",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 清单

def _build_manifest() -> list[dict]:
    # ⚠️ **先清空自己的产物再写**：删掉一条工况后，旧文件仍会躺在盘上变成孤儿
    #    （`<id>.jpg` ＋ `<id>.README.txt` 不在新清单里，却会被打包器按目录扫进去 ⇒ 包内多出两条
    #    对不上清单的文件）。只清本脚本自己的两个产物 —— `OUT` 与 `MANIFEST`，
    #    不碰 `output/` 下任何别的东西。
    shutil.rmtree(OUT, ignore_errors=True)
    MANIFEST.unlink(missing_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    base = load_base()
    rows: list[dict] = []

    for c in CASES:
        arr, meta = c["fn"](base, None)
        ext = meta.get("ext", ".jpg")
        path = OUT / f"{c['id']}{ext}"
        if meta.get("writer") == "cv2":
            _save_cv2(arr, path, int(meta.get("quality", CV2_Q)))
        else:
            save(arr, path, quality=int(meta.get("quality", 93)))

        rows.append({
            "id": c["id"], "label": c["label"], "category": c["cat"], "desc": c["desc"],
            "operation": c["operation"], "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "gt_bbox_xywh": meta.get("bbox_xywh"), "expect": c["expect"],
        })
        # 每样本 README（与清单同一口径，逐条落盘；打包时逐字节拷进包）
        (OUT / f"{c['id']}.README.txt").write_text(
            edge_readme_txt(rows[-1]), encoding="utf-8", newline="\n")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "name": "LorealGuard 边缘场景集（误报护栏的证据）",
        "version": "v1", "generated": "2026-09-27", "seed": SEED, "base": BASE.name,
        "count": len(rows),
        "counts": {"benign": sum(1 for r in rows if r["label"] == "benign"),
                   "malicious": sum(1 for r in rows if r["label"] == "malicious")},
        "dataset_revisions": [
            "v1（9 条 = 良性 9 + 恶意 0）：首版。全部是**良性边界输入**，用途是"
            "误报护栏的证据（官方评分第 2 位点名「数据是否涵盖边缘场景」，"
            "`loreal-topic2-details.md:21`），**不新增检出靶**；期望上限统一为「关注级」"
            "（关注级 = rank 2 ⇒ 不得判违规）。其中 edge_inplace_q75 是 D-12 已登记的能力"
            "边界，按 docs/DEBT.md:291 的口径如实写明（不藏，也不为它改阈值）。",
            "v1 修订（同日，10 条 → 9 条）：**删去 edge_no_exif**。原因如实登记 —— 合成底图"
            " samples/base_neutral.jpg 本身零 EXIF（getexif() 条目数实测 0），再"
            "\"剥离 EXIF\"是空操作，产物与主集样本逐字节同 sha256 ⇒ 包内会出现一对同哈希"
            "样本，覆盖不增且像凑数。**不塞替代品**（p2 已用掉同类算子，换哪个都会再撞重复）："
            "本条边缘覆盖由主集全体样本天然承担。删条同时给生成器加了"
            "\"先清空 OUT/MANIFEST 再写\"，避免旧文件变孤儿。",
        ],
        "how_to_reproduce": "env -u PYTHONPATH .venv/Scripts/python.exe samples/make_edge_suite.py"
                            "  → 比对每条的 sha256",
        "privacy": "全部程序合成，不含任何个人数据；底图 samples/base_neutral.jpg 亦为程序生成，"
                   "且不含 EXIF（只有 JFIF 段）。",
        "items": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def main() -> int:
    rows = _build_manifest()
    for r in rows:
        box = "有真值框" if r["gt_bbox_xywh"] else "—"
        print(f"{r['id']:24} {r['label']:7} {r['category']:10} {r['bytes']//1024:>5} KB  "
              f"{r['sha256'][:16]}  {r['file']:28} {box}")
    # 计数一律**按 rows 现算**，不写死 —— 删条/加条时这里必须自己跟上（本行曾是 `len(rows)` 配
    # 死写的「恶性 0」，删条后仍然"对"，但那种"对"是巧合，不是可验证的）。
    n_benign = sum(1 for r in rows if r["label"] == "benign")
    n_mal = sum(1 for r in rows if r["label"] == "malicious")
    print(f"\n{len(rows)} 条（良性 {n_benign} / 恶意 {n_mal}）→ {MANIFEST}")
    print(f"每样本 README.txt 已落 {OUT}")
    print("口径：全部期望上限＝关注级（关注级 = rank 2 ⇒ 不得判违规）—— 本集是误报护栏的证据，不是检出靶。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
