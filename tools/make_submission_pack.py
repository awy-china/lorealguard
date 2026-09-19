# -*- coding: utf-8 -*-
"""把 P2 系列样本导出成**官方要求的提交格式**测试数据包。

官方原文（赛题2）：测试数据包 zip，**每样本一文件夹**：
    文案 `.txt`  +  图 `.jpg`  +  `README.txt`（注明 来源 / 类型 / 是否伪造 / 伪造方式）

本脚本产出的目录结构：
    dist/submission_pack/lorealguard_testset_<ver>/
        samples/<id>/
            image.jpg        ← 图像（合成图或篡改图）
            text.txt         ← 该样本配的文案（无文案时写明"本样本仅图像通路"）
            README.txt       ← 来源 / 类型 / 是否伪造 / 伪造方式 / 期望等级 / 期望规则 / sha256
        MANIFEST.json        ← 机器可读全量清单（含逐条 sha256、gt 框、expect）
        SHA256SUMS.txt       ← 逐文件哈希（校验完整性）
        PRIVACY.txt          ← 隐私与合规声明
        README.md            ← 人读说明 + 官方格式对照 + 单行复现命令
        generators/          ← 生成器脚本（可复现性：别人能重造出一模一样的样本）
    dist/submission_pack/lorealguard_testset_<ver>.zip   ← 提交用压缩包
    dist/submission_pack/lorealguard_testset_<ver>.zip.sha256

用法（单行）：
    env -u PYTHONPATH .venv/Scripts/python.exe tools/make_submission_pack.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE_DIR = ROOT / "output" / "p2" / "suite"
MAIN_MANIFEST = ROOT / "output" / "p2" / "manifest.json"
TEXT_MANIFEST = ROOT / "output" / "p2" / "text_pairs.json"
OUT_DIR = ROOT / "dist" / "submission_pack"

GENERATORS = [
    "samples/make_neutral_base.py",
    "samples/make_p2_suite.py",
    "samples/make_p2_text_pairs.py",
]

# 固定时间戳 → zip 逐字节可复现（同一份输入永远同一 sha256）
FIXED_DATE = (2026, 9, 19, 0, 0, 0)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def build_text_index(tp: dict) -> dict[str, list[dict]]:
    """image_id → [该图上配的每套文案]"""
    idx: dict[str, list[dict]] = {}
    for item in tp.get("items", []):
        idx.setdefault(item["image_id"], []).append(item)
    return idx


def readme_txt(item: dict, texts: list[dict]) -> str:
    """官方要求的四要素：来源 / 类型 / 是否伪造 / 伪造方式。"""
    is_fake = item["label"] == "malicious"
    lines = [
        "LorealGuard 测试数据包 —— 单样本说明",
        "=" * 46,
        f"样本编号 : {item['id']}",
        f"来源     : 程序合成（生成器见 ../generators/，无任何个人数据、无网络素材）",
        f"类型     : {'篡改/伪造图' if is_fake else '正常拍摄图（良性对照）'}",
        f"是否伪造 : {'是' if is_fake else '否'}",
        f"伪造方式 : {item['operation'] if is_fake else '不适用（良性样本，未做任何篡改）'}",
        f"样本说明 : {item['desc']}",
        f"期望等级 : {item['expect'].get('max_tier', '（未标注）')}   ← LorealGuard 对它的期望判定上限",
        f"期望规则 : {item['expect'].get('must_fire') or '（本样本期望不命中具体规则，见 MANIFEST.json）'}",
    ]
    if item.get("gt_bbox_xywh"):
        lines.append(f"真值框   : {item['gt_bbox_xywh']}  (x, y, w, h)  ← 用于评估定位精度 IoU")
    if item.get("blind_spot"):
        lines.append(f"公告盲区 : {item['blind_spot']}  ← 我们**主动声明**这条属于已知盲区")
    lines += [
        f"sha256   : {item.get('sha256', '（见 MANIFEST.json）')}",
        f"字节数   : {item.get('bytes', '（见 MANIFEST.json）')}",
        "",
        "文案（如有）：",
    ]
    if texts:
        for t in texts:
            lines.append(f"  [{t['variant']}] {t['text']}")
            lines.append(f"      期望上限 {t['expect'].get('max_tier')}  理由：{t.get('why', '')}")
    else:
        lines.append("  （本样本仅图像通路，未配文案）")
    lines += [
        "",
        "-" * 46,
        "复现：env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_suite.py",
        "校验：sha256sum -c SHA256SUMS.txt（在数据包根目录执行）",
        "说明：本包只用于评测「LorealGuard」的判定能力，样本均为程序合成，不含真实人物/个人信息。",
    ]
    return "\n".join(lines) + "\n"


PRIVACY = """LorealGuard 测试数据包 —— 隐私与合规声明
================================================

1. 数据来源
   本包内**全部图像与文案均由程序合成**（生成器脚本随包提供于 generators/）。
   不包含任何真实人物的照片、不包含任何个人信息（无姓名/证件/联系方式/地理位置），
   也不包含任何从互联网抓取的素材。底图为程序生成的合成人像，非真实拍摄。

2. 为什么不直接分发"真实内容"
   真实美妆内容（种草笔记、评论）属于创作者的个人作品与可能的个人数据；
   我们**不抓取、不存储、不再分发**任何真实创作者内容。
   我们分发的对象是**我们自己造的评测样本 + 造它的生成器** ——
   任何人都能用生成器重造出一模一样的样本（逐字节一致，见 SHA256SUMS.txt）。

3. 可复现性
   同一份生成器 + 同一 seed  →  逐字节相同的样本集。
   第三方可以：① 校验我们提交的样本 ② 自行加样本 ③ 用我们公开的跑分器复算我们的每一个数字。

4. 关于 AI 生成内容标识
   部分样本模拟的是"平台对上传图片做过处理"的情形，用于测试标识核验通路。
   我们**不对样本的真假做终审**：LorealGuard 输出的是物证+可解释依据+免责说明，
   最终判定权交由平台与创作者，我们的"不做"清单见项目 README。

5. 免责
   本包仅用于技术评测。LorealGuard 的目标是**降低对真实创作者的误伤**，
   任何单条结论都必须附带"我们不能据此判定你，因为 X"的免责说明。
"""


def write_zip(src_dir: Path, zip_path: Path) -> None:
    """确定性打包：固定时间戳 + 排序 → 同一输入永远同一 sha256。"""
    files = sorted(p for p in src_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in files:
            rel = p.relative_to(src_dir.parent).as_posix()
            info = zipfile.ZipInfo(rel, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())


def main() -> int:
    for p in (MAIN_MANIFEST, TEXT_MANIFEST):
        if not p.exists():
            print(f"[FAIL] 缺少清单 {p} —— 请先跑 samples/make_p2_suite.py 与 make_p2_text_pairs.py")
            return 2
    if not SUITE_DIR.exists():
        print(f"[FAIL] 缺少样本目录 {SUITE_DIR}")
        return 2

    main_m = load_json(MAIN_MANIFEST)
    text_m = load_json(TEXT_MANIFEST)
    text_idx = build_text_index(text_m)
    ver = f"v{main_m.get('version', '0').lstrip('v')}"
    pack_name = f"lorealguard_testset_{ver}"
    pack_root = OUT_DIR / pack_name
    if pack_root.exists():
        shutil.rmtree(pack_root)
    (pack_root / "samples").mkdir(parents=True, exist_ok=True)
    (pack_root / "generators").mkdir(parents=True, exist_ok=True)

    n_img = 0
    for item in main_m["items"]:
        img = SUITE_DIR / item["file"]
        if not img.exists():
            print(f"[WARN] 样本文件缺失，跳过：{img.name}")
            continue
        sdir = pack_root / "samples" / item["id"]
        sdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(img, sdir / "image.jpg")
        texts = text_idx.get(item["id"], [])
        (sdir / "text.txt").write_text(
            texts[0]["text"] + "\n" if len(texts) == 1 else
            "".join(f"[{t['variant']}] {t['text']}\n" for t in texts) if texts
            else "（本样本仅图像通路，未配文案）\n",
            encoding="utf-8",
        )
        (sdir / "README.txt").write_text(readme_txt(item, texts), encoding="utf-8")
        n_img += 1

    for g in GENERATORS:
        src = ROOT / g
        if src.exists():
            shutil.copy2(src, pack_root / "generators" / Path(g).name)

    (pack_root / "MANIFEST.json").write_text(
        json.dumps({"image_channel": main_m, "text_channel": text_m},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (pack_root / "PRIVACY.txt").write_text(PRIVACY, encoding="utf-8")

    # 人读说明
    readme = f"""# {pack_name} —— LorealGuard 测试数据包

> 提交通道：天池·欧莱雅美妆科技黑客松 2026 · 赛题2「信任守护师」

## 官方格式对照

| 官方要求 | 本包对应文件 |
|---|---|
| 每样本一文件夹 | `samples/<样本编号>/`（共 {n_img} 个） |
| 文案 `.txt` | `samples/<编号>/text.txt` |
| 图 `.jpg` | `samples/<编号>/image.jpg` |
| `README.txt` 注明 来源/类型/是否伪造/伪造方式 | `samples/<编号>/README.txt`（四要素逐条写明） |

## 包里有什么

| 文件 | 说明 |
|---|---|
| `MANIFEST.json` | 机器可读全量清单：逐条 sha256、真值框、**期望等级与期望规则** |
| `SHA256SUMS.txt` | 逐文件哈希，`sha256sum -c` 校验 |
| `PRIVACY.txt` | 隐私与合规声明（**全部程序合成、无个人数据**） |
| `generators/` | 生成器脚本：任何人可重造出逐字节相同的样本 |

## 两条通路的样本

- **图像通路**：{main_m['counts']['benign']} 良性 + {main_m['counts']['malicious']} 恶意
  （恶意样本覆盖多种篡改几何，用于测「定位」与「漏报」）。
- **文案通路**：{text_m['counts'].get('benign_text', '?')} 合规话术 + {text_m['counts'].get('malicious_text', '?')} 夸大话术，
  **配对设计**：同一张良性图配两套文案 → 等级差异只能归因文案通路。

## 一个数字请特别看

本数据包是**承诺制**的：每一条的期望等级**在看结果之前就写死在清单里**，
跑分器只做逐条比对。所以「承诺兑现率」是我们愿意被复查的指标 ——
不合期望的条目**如实在案**，不删、不挪阈值。

## 复现（单行）

```bash
# 1. 重造样本（与包内逐字节一致）
env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_suite.py
env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_text_pairs.py

# 2. 复算我们的每个数字（含误报上界与检出的置信下界）
env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_bench.py
env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_text_bench.py
```

## 我们**不**声称什么

- 不声称能对内容真假做终审（我们给物证与可解释依据，最终判定权交平台与创作者）。
- 不声称这个自造集能替代真实世界评测（这是本包**唯一拆不掉的弱点**，已公开登记）。
- 不声称能覆盖全部伪造手法（盲区清单与漏报构成在跑分报告里逐条公开）。
"""
    (pack_root / "README.md").write_text(readme, encoding="utf-8")

    # 逐文件哈希（相对包根，排序 → 稳定）
    sums = []
    for p in sorted(pack_root.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS.txt":
            rel = p.relative_to(pack_root).as_posix()
            sums.append(f"{sha256_file(p)}  {rel}")
    (pack_root / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")

    zip_path = OUT_DIR / f"{pack_name}.zip"
    write_zip(pack_root, zip_path)
    zip_sha = sha256_file(zip_path)
    (OUT_DIR / f"{pack_name}.zip.sha256").write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")

    print(f"[OK] 样本文件夹 {n_img} 个 → {pack_root}")
    print(f"[OK] 提交压缩包 → {zip_path}")
    print(f"[OK] zip sha256 = {zip_sha}")
    total_files = sum(1 for p in pack_root.rglob("*") if p.is_file())
    total_bytes = sum(p.stat().st_size for p in pack_root.rglob("*") if p.is_file())
    # 硬断言：SHA256SUMS 必须覆盖除自身以外的每一个文件（差一个 = 交付包有漏网文件）
    assert len(sums) + 1 == total_files, f"SHA256SUMS 覆盖不全：{len(sums) + 1} != {total_files}"
    print(f"[OK] 包内文件数 = {total_files}（{len(sums)} 条进 SHA256SUMS，清单自身不计入）")
    print(f"[OK] 解压后总字节 = {total_bytes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())