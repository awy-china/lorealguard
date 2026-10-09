"""F9 Agent 编排层：**判定之后**的追加取证与追问。永不参与判定、永不动判定数字。

为什么要有这一层（以及它为什么必须长这样）：
    赛题要求「用开源大模型实现 Agent 系统」。但本项目最核心的设计决策是
    **判定链零模型**（见 CLAUDE.md §2：坐标由算法给、语义由模型给）。
    于是 Agent 只能做一件事：在账本**已经算完**之后，决定"还该补哪一步取证"、
    "要让创作者补什么信息" —— 而不是"这张图是不是假的"。后者是 F1–F7 的活。

四条硬约束（每条都有测试 + 可证伪的变异体，见 tests/test_agent_planner.py）：
    ① **零判定权**：本模块入参是算完的 `report`，返回一个独立 dict；
       不写 report 的任何判定字段，`run_detector` 的结果**只进附三**，不进左右两栏账本。
       guard.py 里的调用点排在 `RiskReport(...)` 构造**之后** —— 代码顺序即不变量。
    ② **白名单**：只允许 `config.MODEL_AGENT`，且用的是与 F3 同一份
       `config.OPEN_WEIGHT_MODELS`（赛题硬要求「开源大模型而非闭源」）。
    ③ **工具确定性**：模型只能从 6 个白名单动作里挑一个，参数还要过 ROI / 检测器 /
       步数三道校验（G3/G4）；所有工具本身零网络、可复算。
    ④ **护栏本体**：模型文本里的结论词与编造数字**在进报告之前**就被拦/替换
       （G1/G2）—— 不是"提示词里写了不许"，是代码里挡住了。

默认关闭（`--agent`）：不开时报告与今天**逐字节相同**（`agent_trace` 只在非空时才进 JSON）。

用法（CLI）：
    python -m voiceguard samples/base_neutral.jpg --agent
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from voiceguard import config
from voiceguard.forensics import registry

#: F9 允许的模型（开源权重白名单）—— 与 semantic/explain.py 同一个来源，单一事实
ALLOWED_MODELS: dict = dict(config.OPEN_WEIGHT_MODELS)

#: 步数上限（G4）。超了就收尾，不许无限自转。
MAX_STEPS = 6

#: 放大切片的倍率区间（crop_zoom）。
MIN_SCALE, MAX_SCALE = 1, 8

#: 单次追问的条数上限。
MAX_QUESTIONS = 5

#: 工具白名单（全部确定性、零网络）。
TOOLS: tuple = ("read_ledger", "read_meta", "run_detector", "crop_zoom", "ask_user", "finish")

#: G1 结论词表。模型输出里出现任何一个 → 该步拦下、原文不入报告。
#: 这不是"提示词里劝它别说"，是拦在报告生成之前的一道闸。
CONCLUSION_WORDS: tuple = ("伪造", "篡改", "真假", "拼接", "p图", "换脸")

#: G1 命中时写进 `blocked_reason` 的话。**刻意不复述被拦下的原词** ——
#: 复述一遍就等于把那个词写进了报告，护栏会自己拆自己。
_G1_REASON = "护栏 G1：文本含结论式词（命中第 {i} 项/共 {n} 项）→ 该步拦下，原文不入报告"

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_REDACTED = "[未核实]"

_SYSTEM = (
    "你是「LorealGuard」取证流程里的**编排助手**，不是判官。"
    "你只能从给定工具里挑一个动作，并且**只输出一个 JSON 对象**，不要输出任何别的文字。"
    "严禁输出『伪造/篡改/真假/拼接/P图/换脸』这类结论式词；"
    "严禁建议修改判定结果或调整任何阈值；严禁编造数字 —— "
    "任何数字都必须来自下面给你的账本与元数据。"
    "你的职责只有两件：① 还该补做哪一步取证；② 要让创作者补什么信息。"
)


# ---------------------------------------------------------------- 只读输入
def read_ledger(report) -> str:
    """三栏摘要（物证 / 免责归因 / 弃权边界）。**只用 id 与数字**，不引用规则措辞
    —— 规则描述里有"拼接嫌疑"这类字眼，抄进提示词等于自己往模型嘴里递结论词。"""
    v = report.verdict
    L = [f"风险等级={v.tier.value}；置信度={v.confidence}；"
         f"物证锚前16位={str(report.fingerprint)[:16]}",
         f"左半栏·可疑证据 {len(report.evidence)} 条："]
    for e in report.evidence:
        nums = "；".join(f"{k}={_fmt(x)}" for k, x in list(e.metrics.items())[:4])
        L.append(f"- {e.id} 坐标={e.bbox or '整图'} 置信度={e.confidence}"
                 + (f" 实测：{nums}" if nums else ""))
    L.append(f"右半栏·免责归因 {len(report.benign)} 条：")
    for b in report.benign:
        L.append(f"- {b.code}" + (f"（解释 {len(b.explains)} 项）" if b.explains else ""))
    L.append("弃权与边界：")
    for b in (report.boundaries or [])[:6]:
        L.append(f"- {b}")
    return "\n".join(L)


def read_meta(report, meta: dict | None = None) -> str:
    """尺寸 / 是否含 EXIF / JPEG 史 / 平台重压缩标记。**只读**。"""
    m = meta or {}
    shape = m.get("shape") or (report.shape or None)
    L = [f"尺寸={'x'.join(str(x) for x in shape) if shape else '无图像'}",
         f"是否 JPEG={m.get('is_jpeg')}",
         f"含 EXIF={m.get('exif_present')}"]
    for d in (report.detectors or []):
        if d.get("name") == "ela":
            raw = d.get("raw") or {}
            L.append(f"JPEG 史={raw.get('jpeg_history')}")
            L.append(f"平台重压缩标记={raw.get('platform_recompressed')}")
    if report.provenance is not None:
        L.append(f"标识核验状态={report.provenance.state.value}")
    return "；".join(L)


def _fmt(x) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def traceable_numbers(report, meta: dict | None = None) -> frozenset:
    """账本 + 元数据里**出现过的所有数字**（G2 的判据）。

    口径就是字面意思：把报告 JSON 与元数据序列化后扫出全部数字字面量。
    ⚠️ 这条护栏能挡住的是「账本里根本没出现过的数字」（例如凭空的 `5597`）；
    账本里出现过的数字，它无法区分"是不是同一个统计量" —— **这是能力边界，
    不是待修的缺口**（见报告附三的自我声明）。
    """
    blob = json.dumps(report.as_dict(), ensure_ascii=False) + "\n" + json.dumps(
        _plain(meta or {}), ensure_ascii=False, default=str)
    return frozenset(_NUM_RE.findall(blob))


def _plain(o):
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(x) for x in o]
    if hasattr(o, "tolist"):
        return o.tolist()
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    return str(o)


# ---------------------------------------------------------------- 护栏本体
def conclusion_hit(text) -> int:
    """G1：返回命中的结论词序号（1 起），没命中返回 0。大小写不敏感（`P图`/`p图`）。"""
    t = str(text).lower()
    for i, w in enumerate(CONCLUSION_WORDS, 1):
        if w in t:
            return i
    return 0


def redact_numbers(text: str, allowed: frozenset) -> tuple:
    """G2：账本里找不到的数字 → `[未核实]`。返回 (新文本, 替换处数)。

    只记处数、**不记被替换掉的原值** —— 把原值写进报告等于护栏白做。
    """
    n = 0

    def sub(m):
        nonlocal n
        if m.group(0) in allowed:
            return m.group(0)
        n += 1
        return _REDACTED

    return _NUM_RE.sub(sub, text), n


def _scan_strings(o):
    """递归取出一个 JSON 动作里**所有字符串值**（G1/G2 的检查面）。"""
    if isinstance(o, dict):
        for v in o.values():
            yield from _scan_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _scan_strings(v)
    elif isinstance(o, str):
        yield o


# ---------------------------------------------------------------- 动作校验
def safe_name(x) -> str:
    """把模型给的名字**安全地**回显到报告里。

    模型输出的任何一段文本都不可信：直接 `{name!r}` 拼进报告，等于给了一条
    "让报告里出现任意字符串"的通道（提示词注入的落点）。只放行"像名字的"。
    """
    return x if isinstance(x, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", x) \
        else "[非名字文本]"


def safe_roi(roi) -> str:
    """同理：只回显**像坐标的** 4 个整数，否则只说"非法"。

    ⚠️ 2026-09-25 收口：原先只要求"4 个 int"就回显 —— 于是
    `[-5, 0, 999999, 999999]` 被**原样抄进报告**。数字看着无害，但 JSON 里的
    整数没有长度上限（`10**100000` 是合法的 JSON int）→ 报告体积由模型决定。
    现在加上坐标的物理边界（`0 ≤ v ≤ 65535`，没有任何图的一边会超过它），
    越界的坐标就只说 `[非法 ROI]` —— 反馈该给模型的部分（"你给的 ROI 不合法"）
    一点没少，能塞进报告的东西却重新变成有限集合。
    """
    if isinstance(roi, (list, tuple)) and len(roi) == 4 \
            and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 65535
                    for v in roi):
        return str(list(roi))
    return "[非法 ROI]"


def _valid_roi(roi, img) -> bool:
    """ROI 必须是图内的合法 bbox：4 个非负整数、w/h>0、且**完全落在图内**。"""
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        return False
    if any(isinstance(v, bool) or not isinstance(v, int) for v in roi):
        return False
    if img is None:
        return False
    h, w = img.shape[:2]
    x, y, bw, bh = roi
    if x < 0 or y < 0 or bw <= 0 or bh <= 0:
        return False
    return x + bw <= w and y + bh <= h


def _norm_questions(raw):
    """把追问清单规范化成 [{'what','why','then'}]；不合格返回 None（交给 G3 记档）。"""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or len(raw) > MAX_QUESTIONS:
        return None
    out = []
    for q in raw:
        if isinstance(q, str):
            q = {"what": q}
        if not isinstance(q, dict):
            return None
        what = str(q.get("what") or "").strip()
        if not what:
            return None
        out.append({"what": what, "why": str(q.get("why") or "").strip(),
                    "then": str(q.get("then") or "").strip()})
    return out


def _extract_json(text: str):
    """从模型回答里抠出**第一个完整的 JSON 对象**。抠不出 → None（G4：记档并终止）。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):                       # 容忍 ```json 围栏
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s, flags=re.S).strip()
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:i + 1])
                except Exception:                 # noqa: BLE001  解析失败＝非法返回
                    return None
                return obj if isinstance(obj, dict) else None
    return None


# ---------------------------------------------------------------- 提示词
def build_prompt(ledger_text: str, meta_text: str, hist: list, step: int, max_steps: int) -> str:
    """每一轮的提问文本：**状态 + 工具清单 + 已做步骤 + 还剩几步**。纯函数、可单测。"""
    L = [
        "【账本（算法已算完，判定已定，你不许改）】",
        ledger_text,
        "",
        "【元数据】",
        meta_text,
        "",
        f"【可用检测器】{', '.join(registry.names())}",
        "",
        "【可用工具（只能选一个）】",
        '- read_ledger()                              读三栏账本摘要',
        '- read_meta()                                读尺寸/EXIF/JPEG 史/平台重压缩标记',
        '- run_detector(name, roi=None)               追加跑一个检测器（roi=[x,y,w,h]，缺省整图）',
        '- crop_zoom(bbox, scale)                     出放大切片（bbox=[x,y,w,h]，scale 1–8）',
        '- ask_user(questions)                        追问清单，每项 {"what","why","then"}',
        '- finish(summary)                            结束并写总结',
        "",
        "【回法】只回一个 JSON 对象，例如：",
        '{"action": "run_detector", "args": {"name": "ela", "roi": [10, 20, 64, 64]}}',
        '{"action": "finish", "args": {"summary": "一句话总结"}}',
        "",
    ]
    if hist:
        L.append(f"【已执行 {len(hist)} 步】")
        L.extend(hist)
        L.append("")
    L.append(f"这是第 {step} 步（上限 {max_steps} 步）。请给出下一步动作。")
    return "\n".join(L)


# ---------------------------------------------------------------- 主循环
def plan(report, *, img=None, meta: dict | None = None, image_path=None,
         text: str = "", comments=None, outdir=None, model: str | None = None,
         ask=None, max_steps: int = MAX_STEPS) -> dict:
    """在**已判定**的 report 上跑一轮编排。返回独立 dict（含 status），绝不改 report。

    参数 ask 可注入：`ask(prompt, model=...) -> {ok, answer, error}`（测试用假模型 → 零网络）。
    不注入则走 `semantic/vl.py`（唯一网络出口），且**没 key 时一次都不调**。
    """
    m = model or config.MODEL_AGENT
    base = {"model": m, "license": ALLOWED_MODELS.get(m, ""), "status": "ok", "note": "",
            "steps": [], "summary": "", "questions": [], "n_tool_calls": 0,
            "context": f"文案 {len(text or '')} 字 / 评论 {len(list(comments or []))} 条"}

    # —— 约束②：白名单。不在开源权重白名单里的档，一律拒绝（且不发起任何调用）
    if m not in ALLOWED_MODELS:
        why = config.RESEARCH_ONLY_MODELS.get(m, "未知档")
        return {**base, "status": "refused_non_whitelist",
                "note": (f"模型 {m} 不在开源权重白名单内（{why}）；"
                         f"赛题要求必须用开源大模型 → F9 拒绝调用。"
                         f"可用档：{', '.join(sorted(ALLOWED_MODELS))}")}

    # —— 取图：优先用调用方已经加载好的（guard.py 里已 load 过一次，不再重复读盘）
    if img is None and image_path:
        p = Path(image_path)
        if p.exists():
            from voiceguard.forensics.base import load_and_prepare
            img, loaded_meta = load_and_prepare(p)
            meta = meta or loaded_meta

    allowed = traceable_numbers(report, meta)
    ledger_text = read_ledger(report)
    meta_text = read_meta(report, meta)

    # —— 约束③降级：没 key 就不发起任何调用（"假 client 调用次数 = 0" 的那条闸）
    if ask is None:
        if not config.get_api_key():
            return {**base, "status": "unavailable_no_key",
                    "note": "未配置 API key → F9 不运行（判定链 F1–F7 不受任何影响）"}
        from voiceguard.semantic.vl import ask_text

        def ask(prompt, model=None):              # noqa: E306  局部适配，vl.py 仍是唯一出口
            return ask_text(prompt, model=model or m, system=_SYSTEM)

    out = Path(outdir) if outdir else Path(getattr(config, "OUTPUT_DIR", "output")) / "agent"
    status, note = "ok", ""
    hist: list = []
    steps: list = []
    summary = ""

    for i in range(1, max_steps + 1):
        prompt = build_prompt(ledger_text, meta_text, hist, i, max_steps)
        try:
            res = ask(prompt, model=m)
        except Exception as e:                    # noqa: BLE001  调用失败不该让报告失败
            msg = f"{type(e).__name__}: {e}"
            if "key" in msg.lower():
                return {**base, "steps": steps, "status": "unavailable_no_key",
                        "note": "未配置 API key → F9 不运行（判定链不受影响）"}
            status, note = "error", f"调用失败：{msg[:200]}"
            break
        if not res or not res.get("ok"):
            err = str((res or {}).get("error") or "返回空内容")
            if "key" in err.lower():
                return {**base, "steps": steps, "status": "unavailable_no_key",
                        "note": "未配置 API key → F9 不运行（判定链不受影响）"}
            status, note = "error", f"模型未给出可用回答：{err[:200]}"
            break

        action = _extract_json(res.get("answer", ""))

        # —— G4：非法 JSON → 记档并终止（附三写「未完成」），不崩
        if action is None:
            steps.append(_step(i, "[非法 JSON]", {}, "",
                               "护栏 G4：模型未返回一个 JSON 动作 → 记档并终止（本层本次未完成）"))
            status, note = "unfinished_invalid_json", "模型返回非法 JSON → 本层本次未完成"
            break

        # —— G1：结论词 → 整步拦下，原文（含被拦的那句）**不入报告**
        hit = conclusion_hit(" ".join(_scan_strings(action)))
        if hit:
            steps.append(_step(i, "[已拦截]", {}, "",
                               _G1_REASON.format(i=hit, n=len(CONCLUSION_WORDS))))
            status, note = "blocked_by_guardrail", "模型输出含结论式词 → 该步拦下并终止"
            break

        name = action.get("action")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}

        # —— 未知动作：G3 记档（名字过了 G1 才回显，且只回显像名字的）
        if name not in TOOLS:
            shown = name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", name) \
                else "[非白名单动作]"
            steps.append(_step(i, shown, {}, "",
                               f"护栏 G3：动作不在白名单 {TOOLS} → 拒绝执行"))
            hist.append(f"- 第 {i} 步 {shown}：被拒（不在白名单）")
            continue

        step, hist_line = _execute(name, args, report, img, meta, out, i, allowed)
        steps.append(step)
        if step.get("blocked_reason"):
            hist.append(f"- 第 {i} 步 {name}：被拒（{step['blocked_reason']}）")
            continue
        base["n_tool_calls"] += 1
        if hist_line:
            hist.append(hist_line)
        if name == "ask_user":
            base["questions"].extend(step["args"]["questions"])
        if name == "finish":
            summary = step["args"].get("summary", "")
            status = "ok"
            break

    else:
        # for 正常走完（没 break）＝ 用满步数也没 finish
        status, note = "max_steps", f"已达步数上限（{max_steps} 步）仍未调用 finish"

    if status != "ok" and not note:
        note = "本层本次未完成；判定链 F1–F7 不受任何影响"
    base.update({"steps": steps, "summary": summary, "status": status, "note": note})
    return base


def _step(i, action, args, result, blocked=None) -> dict:
    return {"step": i, "action": action, "args": args, "result": result,
            "blocked_reason": blocked}


def _execute(name, args, report, img, meta, out, i, allowed):
    """执行一个白名单动作。返回 (step dict, 给下一轮的历史行)。**只有这里碰工具。**"""
    if name == "read_ledger":
        return _step(i, name, {}, read_ledger(report)), ""
    if name == "read_meta":
        return _step(i, name, {}, read_meta(report, meta)), ""

    if name == "run_detector":
        dn = args.get("name")
        roi = args.get("roi")
        if dn not in registry.names():                    # G3-②：非白名单检测器
            return _step(i, name, {"name": safe_name(dn)}, "",
                         f"护栏 G3：检测器 {safe_name(dn)} 不在注册表 {registry.names()} → 拒绝执行"), ""
        if img is None:                                   # 无像素 → 不适用（不是"无异常"）
            return _step(i, name, {"name": dn}, "", "无图像输入 → 检测器不适用"), ""
        if roi is not None and not _valid_roi(roi, img):   # G3-①：越界 ROI
            return _step(i, name, {"name": dn, "roi": safe_roi(roi)}, "",
                         f"护栏 G3：ROI {safe_roi(roi)} 非法或越出图像范围 → 拒绝执行"), ""
        try:
            r = registry.get(dn)(img, None, meta)
            txt = r.brief()
            if r.status != "ok":
                txt += f"（{r.reason}）"
        except Exception as e:                            # noqa: BLE001
            txt = f"[{dn}] 异常 {type(e).__name__}: {e}"
        return _step(i, name, {"name": dn, "roi": roi}, txt), f"- 第 {i} 步 追加取证 {txt}"

    if name == "crop_zoom":
        bbox, scale = args.get("bbox"), args.get("scale", 2)
        if img is None or not _valid_roi(bbox, img):
            return _step(i, name, {"bbox": safe_roi(bbox)}, "",
                         f"护栏 G3：bbox {safe_roi(bbox)} 非法或越界（或无图像输入）→ 拒绝执行"), ""
        if isinstance(scale, bool) or not isinstance(scale, int) or not (MIN_SCALE <= scale <= MAX_SCALE):
            shown = scale if isinstance(scale, int) else "[非法倍率]"
            return _step(i, name, {"bbox": bbox, "scale": shown}, "",
                         f"护栏 G3：scale {shown} 不在 {MIN_SCALE}–{MAX_SCALE} → 拒绝执行"), ""
        import cv2
        x, y, bw, bh = bbox
        crop = img[y:y + bh, x:x + bw]
        # INTER_NEAREST：放大取证切片时**不许插值** —— 插值会引入原图没有的像素，
        # 那是"我们造出来的痕迹"，拿它当物证等于自己污染证据。
        big = cv2.resize(crop, (bw * scale, bh * scale), interpolation=cv2.INTER_NEAREST)
        out.mkdir(parents=True, exist_ok=True)
        fp = out / f"agent_zoom_{i}_{x}-{y}-{bw}-{bh}_x{scale}.png"
        cv2.imencode(".png", big)[1].tofile(str(fp))
        txt = f"已出放大切片 {fp.name}（{bw}x{bh} ×{scale} → {bw * scale}x{bh * scale}）"
        return _step(i, name, {"bbox": bbox, "scale": scale}, txt), f"- 第 {i} 步 {txt}"

    if name == "ask_user":
        qs = _norm_questions(args.get("questions"))
        if qs is None:
            return _step(i, name, {}, "",
                         f"护栏 G3：追问清单不合格（须 1–{MAX_QUESTIONS} 条，每条要有 what）→ 拒绝"), ""
        red, n = _redact_questions(qs, allowed)
        note = f"；护栏 G2：{n} 处数字替换为 {_REDACTED}" if n else ""
        return _step(i, name, {"questions": red}, f"记录 {len(red)} 条追问（见下）{note}"), \
            f"- 第 {i} 步 追问 {len(red)} 条"

    # finish
    raw = args.get("summary")
    if not isinstance(raw, str) or not raw.strip():
        return _step(i, name, {}, "", "护栏 G3：finish 缺少 summary 文本 → 拒绝"), ""
    txt, n = redact_numbers(raw.strip(), allowed)
    note = f"；护栏 G2：{n} 处数字替换为 {_REDACTED}" if n else ""
    return _step(i, name, {"summary": txt}, f"结束{note}"), ""


def _redact_questions(qs, allowed):
    """对追问清单里每一段文本过 G2。返回 (新清单, 替换处数)。"""
    out, n = [], 0
    for q in qs:
        item = {}
        for k in ("what", "why", "then"):
            item[k], k_n = redact_numbers(q[k], allowed)
            n += k_n
        out.append(item)
    return out, n


# ---------------------------------------------------------------- 渲染
def render_section(tr: dict) -> list[str]:
    """把编排轨迹渲染成报告 md 的一节（附三）。返回行列表。"""
    L: list[str] = []
    a = L.append
    a("## 附三、Agent 编排与追问（F9，**非判定**）")
    a("")
    a("这一节是**编排**，不是**判定**：Agent 在账本算完之后运行，只做两件事 —— "
      "追加取证、列出该向创作者追什么信息。它**不产出**任何真伪结论，也不改变上面第一至四节。"
      "开启方式：`--agent`（默认关闭）。")
    a("")
    if not tr:
        a("本项未运行（默认关闭）。判定链 F1–F7 为纯算法，**零模型、零网络**即可复算。")
        a("")
        return L
    a(f"- 状态：**{tr.get('status')}**")
    a(f"- 模型：`{tr.get('model')}`" + (f"（许可：{tr['license']}）" if tr.get("license") else ""))
    a(f"- 输入上下文：{tr.get('context', '')}")
    if tr.get("note"):
        a(f"- 说明：{tr['note']}")
    a("")
    a("| 步 | 动作 | 参数 | 结果 / 拦截原因 |")
    a("|---|---|---|---|")
    for s in (tr.get("steps") or []):
        arg = json.dumps(s.get("args") or {}, ensure_ascii=False)
        cell = s.get("blocked_reason") or s.get("result") or "—"
        a(f"| {s.get('step')} | `{s.get('action')}` | `{_cell(arg)}` | {_cell(cell)} |")
    a("")
    if tr.get("questions"):
        a("**向创作者追问（缺什么 / 为什么需要 / 拿到后能干什么）**：")
        a("")
        for q in tr["questions"]:
            a(f"- 缺 **{q['what']}**｜为什么：{q.get('why') or '—'}｜拿到后：{q.get('then') or '—'}")
        a("")
    if tr.get("summary"):
        a("**本层总结**：")
        a("")
        a("> " + tr["summary"].replace("\n", "\n> "))
        a("")
    a("**本层的两条能力边界（主动声明）**：")
    a("")
    a("1. `run_detector` 的追加结果**只出现在这一节**，不进第一至四节的双向账本，"
      "更不改变风险等级 —— 编排层没有判定权，这是设计而非疏漏。")
    a(f"2. 数字护栏（G2）只能挡住「账本里根本没出现过的数字」；"
      f"账本里出现过的数字，它无法区分是不是同一个统计量，会原样放行（命中记为 {_REDACTED} 的才是被挡下的）。")
    a("")
    return L


def _cell(s) -> str:
    """表格单元格里不许出现竖线/换行，否则会把表撑坏。"""
    return str(s).replace("|", "\\|").replace("\n", " ")[:300]


__all__ = ["ALLOWED_MODELS", "MAX_STEPS", "TOOLS", "CONCLUSION_WORDS", "plan",
           "read_ledger", "read_meta", "build_prompt", "render_section",
           "conclusion_hit", "redact_numbers", "traceable_numbers"]
