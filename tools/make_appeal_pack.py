#!/usr/bin/env python
"""申诉包（自证包）导出器 —— 把「右栏」从报告的一节变成可提交的产物。

产品位置：`voiceguard/report/ledger.py` 第六节原本写着

    ⏳ 以上第 1–4 步目前需要人工执行；**一键生成申诉材料（自证包）**是待建的下一步能力

本工具就是那一步。它**消费**一份已经判完的报告，不产生新的判定。

四条不变量（任何一条破了都是返工）：

1. **零新判定。**`tier / confidence / evidence / benign / boundaries` 全部**逐字段抄**自
   报告 JSON。本工具不 import 任何检测器实现、不调用规则引擎的 `evaluate`、不调任何模型、
   不发任何网络请求。守卫
   `tests/test_appeal_pack.py::test_tier_is_copied_verbatim_not_recomputed` 会整块比对
   包里与报告里的 `verdict` 必须**完全相等**（不是"等级一样"）。
   唯一从 `voiceguard` 借的是 `guard.sha256_file` —— 要的就是**和算报告指纹时逐字节同一段代码**，
   自己再写一份 sha256 才是新口径。
2. **零自有时间戳。**包里所有时间一律沿用报告的 `generated_at`。理由不是洁癖：
   本工具若取 `datetime.now()`，同一份报告两次导出**字节不同**，
   「这份申诉材料是不是从那份报告导出来的」就永远只能靠嘴说。守卫会连导两次比 sha256。
3. **不含图像字节。**申诉要附的原始文件由创作者自己提交。本包只给 sha256 锚 +
   物证图路径，不在本地产出第二份图像副本（隐私最小化：少一处副本就少一处泄露面）。
   由此包本身是**纯文本包**，也才谈得上逐字节可复算。
4. **不主张内容为真。**申诉的诉求是「不该被怀疑」，不是「内容是真的」——
   后者本系统根本不做（铁律 3）。包里那句免责声明是硬编码的，不是从报告里抄的，
   因为它声明的是**本工具**的边界。

⚠️ 三条**拒发**条件见 `refusal_reason()` 与 `verify_original_image()`。
第一条最要紧：**左栏非空而右栏为空时必须拒发** —— 只有物证、没有「为什么不该怀疑你」的
申诉材料是反效果，等于替对方把物证整理好递上去。

用法：

    python tools/make_appeal_pack.py --report output/demo/item_report.json \\
        --image samples/base_neutral.jpg --outdir output/appeal
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard.guard import sha256_file                      # noqa: E402
from voiceguard.rules.engine import rules_fingerprint         # noqa: E402

#: 硬编码在包里的一句声明（不抄报告：它声明的是**本工具**的边界，不是系统的）。
DISCLAIMER = (
    "本材料**不主张内容为真**，也不做任何真假终审。它只做一件事：把核验系统已经写进"
    "报告右栏的「为什么不该怀疑你」原文，连同可复核的坐标与哈希锚，整理成一份"
    "可以直接附在申诉里的文件。系统本身的能力边界见第三节，本材料同样不越界。"
)

#: 本工具**自己写的**句子（一句都不含来自报告的引用）。
#: 抽成常量是为了让判据能**只扫这些**：报告原文里本来就有「拼接」「伪造」等词
#: （规则描述、法条原文），去扫整份 md 只会把"如实引用"误判成"越权下判"。
OWN_SENTENCES = (
    DISCLAIMER,
    "复核提示，**不是**违规判决",
    "本包不含任何新判定：下列 verdict / benign 逐字段复制自原报告，未重算、未调模型。",
    "本工具自己的时间戳",
    "右栏有内容 ≠ 右栏解释了那个坐标",
    "系统不做真假终审，本材料也不做",
)

#: **自有措辞里**禁止出现的越权断言。这些词不是"提到就算"——上面 `DISCLAIMER`
#: 里就有「真假终审」四个字，否定式地提到它是对的。禁的是**肯定式地宣称结论**。
FORBIDDEN_OWN_CLAIMS = ("确认为伪造", "判定为伪造", "认定为伪造", "确定是假的",
                        "内容确为真实", "证据确凿", "已确认违规")


class AppealRefused(Exception):
    """拒发。**不是**异常处理路径，是产品行为：材料不合格就不出材料。"""


# ---------------------------------------------------------------- 路径口径

def display_path(p: str | Path | None) -> str | None:
    """把**本机绝对路径**降级成"只印文件名"；相对路径原样保留。

    为什么要这一步：本包是**交给第三方**的材料（平台、评审、对方创作者），
    里面印出 `D:\\LorealGuard\\output\\_appeal_probe\\inplace_q75.jpg`
    等于连本机的目录结构一起交出去 —— 与内容无关，却是实打实的隐私泄露面
    （2026-09-25 Hermes 在真产物上抓到）。

    只降级绝对路径、保留相对路径（相对路径本身就是可读的上下文，且不含用户名/盘符）。
    这个函数是**唯一**出口：`APPEAL.md` 与 `appeal.json` 都走它，不各写一份。
    """
    if p is None:
        return None
    s = str(p)
    if not s:
        return s
    q = Path(s)
    return q.name if q.is_absolute() else s


# ---------------------------------------------------------------- 拒发判据

def refusal_reason(report: dict) -> str | None:
    """报告**内容**层面的拒发判据。返回 `None` = 可生成。

    这是判据的**唯一实现** —— 绿断言与它的自证伪共用这一段代码。
    分成两份写会让自证伪证的变成"另一段判据"，而不是守卫本身（沿用
    `tests/test_benign_edit_invariance.py:109 _offenders()` 的同一条纪律）。

    两条：
    * 左栏非空 + 右栏为空 → 拒发。这不是保守，是产品判断：
      一份只有可疑坐标、没有归因的申诉材料，读起来就是「系统指出了这些问题，而申诉方
      没有给出解释」—— 比不提交更差。
    * 某条归因 `detail` 与 `label` 双空 → 拒发。只剩一个代码（如 `NO_TEXT_PROVIDED`）
      的右栏，读材料的人无从判断这条归因到底说了什么。
    """
    ev = report.get("evidence") or []
    bn = report.get("benign") or []
    if ev and not bn:
        return (f"左栏有 {len(ev)} 条可疑证据，右栏为空 —— 只有物证、没有"
                "「为什么不该怀疑你」的申诉材料是反效果（等于替对方把物证整理好递上去）。")
    silent = [str(b.get("code")) for b in bn
              if not str(b.get("detail") or "").strip()
              and not str(b.get("label") or "").strip()]
    if silent:
        return (f"右栏 {silent} 只有代码、没有原文（detail 与 label 双空）—— "
                "读材料的人无从判断这条归因说了什么。")
    return None


def verify_original_image(report: dict, image: str | Path | None) -> dict:
    """核对「创作者附上的图」与「系统判的图」是不是同一张。

    给了图且对不上 → 抛 `AppealRefused`。不给图 → 如实把 `verified` 记成 `None`，
    并在材料里写明"本包无法证明你附的图就是被核验的那张，请自行比对 sha256"，
    **不假装核对过**。
    """
    fp = str(report.get("fingerprint") or "")
    if image is None:
        return {"path": None, "sha256": None, "verified": None}
    if not fp:
        raise AppealRefused("报告里没有指纹（fingerprint 为空），无法核对原图 —— 拒发。")
    got = sha256_file(image)
    if got != fp:
        raise AppealRefused(
            f"原图与报告指纹不一致：\n  报告指纹 {fp}\n  你给的图 {got}（{image}）\n"
            "申诉的必须是**被核验的那张图**，否则整份材料对不上号。")
    return {"path": display_path(image), "sha256": got, "verified": True}


# ---------------------------------------------------------------- 纯函数组装

def attribution_covers_evidence(report: dict) -> bool:
    """右栏里**有没有**哪条归因明确指向左栏的坐标。

    这不是装饰：`tests/test_benign_edit_invariance.py` 记录的边界工况（尺寸不变的原位
    重压缩）里，左栏有 3 条坐标级证据，右栏却只有 `NO_TEXT_PROVIDED`（与编辑无关）
    —— `explains` 是空的。申诉材料必须**如实**把这件事写出来：读材料的人若以为
    「右栏就是对这些坐标的解释」，会得出错误结论。

    左栏为空时返回 True（无可归因之物，谈不上"没覆盖"）。
    """
    ev_ids = {e.get("id") for e in (report.get("evidence") or [])}
    if not ev_ids:
        return True
    for b in (report.get("benign") or []):
        if ev_ids & set(b.get("explains") or []):
            return True
    return False


def own_text_violations() -> list[str]:
    """自有措辞里有没有越权断言。返回违规句子（空 = 干净）。

    在 `build_appeal_pack()` 里**真的调用**，不是只在测试里调 ——
    「一条只在测试里跑的判据」和「构建时真拦一道」是两回事。
    """
    return [s for s in OWN_SENTENCES
            if any(w in s for w in FORBIDDEN_OWN_CLAIMS)]


def build_appeal_json(report: dict, *, rules_fp: str, image_info: dict,
                      copies: "OrderedDict[str, bytes]") -> str:
    """机器可读清单。判定字段**原样搬运**，不做任何再加工。

    清单里**不列**各文件的 sha256：`SHA256SUMS.txt` 已经是那份索引，
    而 `appeal.json` 自己的哈希不可能写进它自己（自指）。分工：
    清单管"这份材料说了什么"，`SHA256SUMS.txt` 管"交付时是哪几个字节"。
    """
    ev = report.get("evidence") or []
    bn = report.get("benign") or []
    fired = (report.get("verdict") or {}).get("fired") or []
    manifest = OrderedDict([
        ("material", "申诉材料（自证包）"),
        ("disclaimer", DISCLAIMER),
        ("rule_no_new_judgement",
         "本包不含任何新判定：下列 verdict / benign 逐字段复制自原报告，未重算、未调模型。"),
        ("source_report", OrderedDict([
            ("content_id", report.get("content_id")),
            ("kind", report.get("kind")),
            ("generated_at", report.get("generated_at")),
            ("fingerprint", report.get("fingerprint")),
            ("image_path", display_path(report.get("image_path"))),
            ("shape", report.get("shape")),
            ("report_version", report.get("version")),
        ])),
        # ---- 逐字搬运：以下两块**不得**在本工具里被改写 ----
        ("verdict", report.get("verdict")),
        ("benign", bn),
        # ---- 本工具自己算的、只有一项：右栏覆不覆盖左栏坐标 ----
        ("attribution_covers_evidence", attribution_covers_evidence(report)),
        ("counts", {"evidence": len(ev), "benign": len(bn), "fired_rules": len(fired)}),
        ("boundaries", report.get("boundaries") or []),
        ("layers", report.get("layers") or []),
        ("identity", OrderedDict([
            ("rules_yaml_sha256_16", rules_fp),
            ("rules_fingerprint_algorithm", "sha256(rules.yaml 字节) 的十六进制前 16 位；"
                                            "实现见 voiceguard/rules/engine.py:rules_fingerprint"),
            ("report_version", report.get("version")),
        ])),
        ("original_image", image_info),
        ("contains_image_bytes", False),
        ("files_included", ["APPEAL.md", "appeal.json", "SHA256SUMS.txt"] + list(copies)),
        ("recompute_with", "python -m voiceguard <原图> --text <文案> --out <目录> --tag <前缀>"),
    ])
    return json.dumps(manifest, ensure_ascii=False, indent=2)


def build_appeal_md(report: dict, *, rules_fp: str, image_info: dict) -> str:
    """给人读的申诉正文。所有引用行都逐字来自报告，本工具**不替报告改口**。"""
    v = report.get("verdict") or {}
    tier, conf = v.get("tier"), v.get("confidence")
    ev = report.get("evidence") or []
    bn = report.get("benign") or []
    fired = v.get("fired") or []
    covers = attribution_covers_evidence(report)
    L: list[str] = []
    a = L.append

    a(f"# 申诉材料（自证包）· 内容 {report.get('content_id')}")
    a("")
    a(f"> {DISCLAIMER}")
    a("")
    a("| 项 | 值 |")
    a("|---|---|")
    a(f"| 被核验物证 sha256 | `{report.get('fingerprint')}` |")
    a(f"| 系统给出的等级 | **{tier}**（置信度 {conf}）—— 复核提示，**不是**违规判决 |")
    a(f"| 核验时间 | {report.get('generated_at')}（本材料不含自己的时间戳） |")
    a(f"| 报告版本 | LorealGuard {report.get('version')} |")
    a(f"| 规则表指纹 | `{rules_fp}`（sha256(rules.yaml)[:16]，可自行复算） |")
    # ② 这条主动承认**必须在摘要表里**（2026-09-25 之前它只埋在第二节的引用块里，
    # 读材料的人扫一眼表就走了，看不到）。它和「系统给出的等级」同级：
    # 一个是"系统说了什么"，一个是"这个说法有多大把握"。
    if covers:
        a("| 归因是否覆盖左栏坐标 | **是** —— 右栏每条归因的 `explains` 都能对上左栏坐标 |")
    else:
        a("| 归因是否覆盖左栏坐标 | **否** —— 右栏有内容 ≠ 右栏解释了那个坐标"
          "（详见第二节「归因覆盖提示」） |")
    a("")

    # ---------------------------------------------------- 一
    a("---")
    a("")
    a("## 一、这份材料要回应的结论")
    a("")
    if fired:
        for h in fired:
            a(f"### 命中规则 `{h.get('rule_id')}` → {h.get('tier')}")
            a("")
            a(f"- 规则描述（原文）：{h.get('desc')}")
            src = h.get("source") or {}
            if src.get("label"):
                a(f"- 依据（原文）：{src.get('label')}")
            if src.get("quote"):
                a(f"- 条文原文：> {src.get('quote')}")
            if src.get("url"):
                a(f"- 来源：{src.get('url')}")
            used = {k: val for k, val in (h.get("signals_used") or {}).items()
                    if not str(k).startswith("_")}
            if used:
                a(f"- 触发时的实测值：`{json.dumps(used, ensure_ascii=False)}`")
            if h.get("advice_creator"):
                a(f"- 系统给创作者的话（原文）：{h.get('advice_creator')}")
            a("")
    else:
        a("**本报告未命中任何风险规则。**")
        a("")
        a("> 这不等于「内容为真」：本系统只在证据足以支撑某个等级时才升级判定，"
          "未命中就是未命中 —— 它是「没判」，不是「判了清白」。")
        a("")

    # ---------------------------------------------------- 二
    a("---")
    a("")
    a("## 二、为什么不该怀疑你（原报告右半栏原文）")
    a("")
    a("这一节是**本材料的核心**：它逐字复制核验报告右侧的归因，不是我们重新组织的说辞。")
    a("")
    if bn:
        a("| 代码 | 归因 | 置信度 | 它解释了哪些证据 |")
        a("|---|---|---|---|")
        for b in bn:
            exp = "、".join(b.get("explains") or []) or "（整图级，无对应坐标）"
            a(f"| `{b.get('code')}` | {b.get('label') or ''} | {b.get('confidence')} | {exp} |")
        a("")
        for b in bn:
            if b.get("detail"):
                a(f"- **`{b.get('code')}`** {b.get('detail')}")
        a("")
    else:
        a("原报告的右半栏为空。（左栏为空时这不构成问题；左栏非空时本工具会拒发。）")
        a("")
    if ev and not covers:
        a("> ⚠️ **归因覆盖提示（请务必读）**：上面这些归因**没有一条指向**本材料第一节那些")
        a("> 可疑坐标（右栏条目的 `explains` 是空的）。也就是说，本系统在这类工况下**没能**把"
          "可疑坐标归因到某个良性解释上。")
        a(">")
        a("> 这不是申诉方的问题，也不是材料写坏了 —— 它是**系统的能力边界**，")
        a("> 已如实登记在报告的第五节与仓库的 `docs/DEBT.md`（D-12）。")
        a("> 我们把它印在这里，是因为读材料的人有权知道：**右栏有内容 ≠ 右栏解释了那个坐标**。")
        a("")

    # ---------------------------------------------------- 三
    a("---")
    a("")
    a("## 三、这个系统**不做**什么（原报告第五节原文）")
    a("")
    a("主动声明局限是可信度的一部分。以下逐条复制自报告：")
    a("")
    for b in (report.get("boundaries") or []):
        a(f"- {b}")
    a("")

    # ---------------------------------------------------- 四
    a("---")
    a("")
    a("## 四、给平台技术同学：可复算的锚")
    a("")
    a(f"- **物证指纹**：`{report.get('fingerprint')}`")
    a("  - 含义：有图输入时 = `sha256(图片文件字节)`；")
    a("    无图输入时 = `sha256(文案 + 条数 + 各条评论)`。")
    a("  - 用途：平台可以拿**自己手里的那张图**算一遍，确认双方说的是同一份物证。")
    a(f"- **规则表指纹**：`{rules_fp}` = `sha256(rules.yaml 字节)[:16]`")
    a("  - 用途：确认分级依据用的是哪一版规则表。报告里的「规则表 v…」是作者手填的版本号，")
    a("    **不等于**规则表字节；指纹改一个字符就变，二者不可互相替代。")
    a(f"- **报告版本**：LorealGuard {report.get('version')}")
    a(f"- **复算入口**：`{manifest_recompute_hint()}`")
    a("  - 注意：复算需要**原始本地文件**。平台上传后元数据会被剥离，")
    a("    所以复算与申诉都必须用本地原图，不是平台上的那份。")
    if report.get("image_path"):
        a(f"- **原图文件名（报告记录）**：`{display_path(report.get('image_path'))}`"
          "（只印文件名：本材料要交给第三方，不印本机目录结构）")
        if report.get("shape"):
            a(f"- **尺寸**：{report.get('shape')[1]}×{report.get('shape')[0]}")
    a("")
    a("各层运行情况（原报告附表）：")
    a("")
    a("| 层 | 状态 |")
    a("|---|---|")
    for l in (report.get("layers") or []):
        a(f"| {l.get('layer')} | {l.get('status')} |")
    a("")

    # ---------------------------------------------------- 五
    a("---")
    a("")
    a("## 五、**你要自己附上的东西**（本包不含）")
    a("")
    a("1. **原始本地文件** —— 带 EXIF 的原始拍摄文件，或 AI 生成工具的原始输出。")
    a("   必须是你**本机上的原图**，不是平台上的那份（平台会剥元数据）。")
    a("2. **原始编辑记录**（若有）—— 发布前的裁剪 / 调色 / 加字幕等操作记录或工程文件，")
    a("   它是证明「那些像素差异来自正常编辑」最直接的东西。")
    a("3. **本包里的 `APPEAL.md` 与 `appeal.json`** —— 连同 `files/` 下两份原报告一起提交。")
    a("")
    if image_info.get("verified") is True:
        a(f"> ✅ 本包已核对：你提供的原图与报告指纹一致（`{image_info.get('sha256')}`）。")
    elif image_info.get("verified") is None:
        a("> ⚠️ **本包没有核对原图**（导出时未提供 `--image`）。也就是说，本包**无法**证明")
        a("> 你附上的图就是被核验的那张。请自行比对上方的「被核验物证 sha256」。")
    a("")

    # ---------------------------------------------------- 六
    a("---")
    a("")
    a("## 六、本材料里**没有**什么")
    a("")
    a("- **没有图像字节**：本包是纯文本包，不复制任何图片。申诉要附的原图由你自己提交。")
    a("- **没有新判定**：第一节的等级、第二节的归因、第三节的边界，全部逐字来自原报告。")
    a("- **没有本工具自己的时间戳**：所有时间都是原报告的 `generated_at`。")
    a("  因此同一份报告导出两次**逐字节相同**，任何一方都能复算这份材料。")
    a("- **没有「内容为真」的主张**：系统不做真假终审，本材料也不做；")
    a("  申诉的诉求是「不该被怀疑」，不是「内容是真的」。")
    a("")
    return "\n".join(L)


def manifest_recompute_hint() -> str:
    return "python -m voiceguard <原图> --out <输出目录> --tag <前缀>"


def build_appeal_pack(report: dict, *, copies: "OrderedDict[str, bytes] | None" = None,
                      image: str | Path | None = None,
                      rules_fp: str | None = None) -> "OrderedDict[str, bytes]":
    """**纯函数**：不读盘、不取当前时间、不联网。返回 `{相对路径: 字节}`。

    这是"逐字节可复算"能被测试的前提 —— 不纯就没法在这里连调两次比 sha256。
    读盘（读报告 md/json 原文、算原图 sha256）全部由调用方 `main()` 先做完。
    """
    bad = own_text_violations()
    if bad:
        raise AppealRefused(f"自有措辞里出现越权断言（铁律 3）：{bad}")
    reason = refusal_reason(report)
    if reason:
        raise AppealRefused(reason)
    fp = rules_fp if rules_fp is not None else rules_fingerprint()
    image_info = verify_original_image(report, image)
    files: "OrderedDict[str, bytes]" = OrderedDict()
    for rel, blob in (copies or {}).items():
        files[rel] = blob
    files["APPEAL.md"] = build_appeal_md(report, rules_fp=fp, image_info=image_info).encode("utf-8")
    files["appeal.json"] = build_appeal_json(
        report, rules_fp=fp, image_info=image_info,
        copies=files).encode("utf-8")
    sums = "".join(f"{_sha(b)}  {rel}\n" for rel, b in files.items())
    files["SHA256SUMS.txt"] = sums.encode("utf-8")
    return files


def _sha(blob: bytes) -> str:
    import hashlib
    return hashlib.sha256(blob).hexdigest()


def write_pack(dest: str | Path, files: "OrderedDict[str, bytes]") -> list[str]:
    d = Path(dest)
    written = []
    for rel, blob in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        written.append(str(p))
    return written


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="make_appeal_pack",
        description="从一份已判完的报告导出可提交的申诉材料（自证包）。零新判定、零联网。")
    ap.add_argument("--report", required=True, help="原报告 JSON（如 output/demo/item_report.json）")
    ap.add_argument("--image", default=None,
                    help="被核验的原始图片；给了就会核对 sha256，对不上直接拒发")
    ap.add_argument("--outdir", default=str(ROOT / "output" / "appeal"), help="输出目录")
    ap.add_argument("--tag", default=None, help="包名（默认取报告文件名去掉 _report.json）")
    args = ap.parse_args(argv)

    rp = Path(args.report)
    if not rp.exists():
        print(f"[FAIL] 找不到报告：{rp}")
        return 2
    report = json.loads(rp.read_text(encoding="utf-8"))

    tag = args.tag or rp.name.replace("_report.json", "") or "appeal"
    dest = Path(args.outdir) / tag

    # 读盘全在这里做完 —— `build_appeal_pack` 保持纯函数。
    copies: "OrderedDict[str, bytes]" = OrderedDict()
    copies["files/report.json"] = rp.read_bytes()
    md = rp.with_suffix(".md")
    if md.exists():
        copies["files/report.md"] = md.read_bytes()
    else:
        print(f"[WARN] 同名 md 不存在，包里只带 json：{md}")

    try:
        files = build_appeal_pack(report, copies=copies, image=args.image)
    except AppealRefused as e:
        print(f"[REFUSED] 拒发申诉包：\n{e}")
        return 3

    write_pack(dest, files)
    print(f"[OK] 申诉包已生成：{dest}")
    for rel in files:
        print(f"     - {rel}")
    tier = (report.get("verdict") or {}).get("tier")
    print(f"[OK] 判定逐字沿用原报告：{tier}"
          f"（可疑证据 {len(report.get('evidence') or [])} / "
          f"免责归因 {len(report.get('benign') or [])}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
