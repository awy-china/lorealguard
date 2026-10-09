"""层1 取证层：统一接口 + 统一预处理 + 可视化

统一签名（手册 §0.2 约定）：
    detector(img_bgr, cfg=None, meta=None) -> DetectionResult

设计要点（三条贯穿全项目的原则）：
1. 检测器必须自报 confidence —— "不适用"和"无异常"是两回事，绝不能混为一谈。
2. 所有判定都要能指向一张证据图 —— 这是本项目"可解释"的物理基础。
3. 异常 ≠ 伪造：先归因（美颜/平台压缩能不能解释），再打分。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

# ---------------------------------------------------------------- 结果结构


@dataclass
class DetectionResult:
    """检测器统一返回结构。score 越大越可疑；confidence 说明"我这次靠不靠谱"。"""

    name: str
    score: float = 0.0                             # 归一化 [0,1]
    raw: dict = field(default_factory=dict)        # 未归一化的原始量（供融合层重标定）
    heatmap: Optional[np.ndarray] = None           # float32 [0,1]，与图同尺寸
    regions: list = field(default_factory=list)    # [{"bbox","score","reason",...}]
    confidence: str = "mid"                        # high | mid | low
    notes: list = field(default_factory=list)      # 人话解释，Agent 直接引用
    #: 三态：ok（跑成了）/ unavailable（没跑成，原因见 reason）。第三种"无"＝该检测器
    #: 压根不在这一轮的名单里（报告 detectors 里就不会有这一条）—— 三者必须分得开。
    #: 默认 "ok" 是刻意的：既有默认路径的产物 JSON **不加新键**，冻结样张才能逐字节重放。
    status: str = "ok"
    reason: str = ""                               # status != "ok" 时的一行原因

    @classmethod
    def unavailable(cls, name: str, reason: str) -> "DetectionResult":
        """检测器没跑成时的登记结果：**显式写"不适用+原因"，绝不静默缺席**。

        分数一律 0、confidence 一律 low，且 guard.build_signals 会整条跳过它 ——
        "没跑"不许被当成"没异常"，也不许被当成"可疑"。
        """
        one = " ".join(str(reason).split())[:200] or "未给出原因"
        return cls(name=name, confidence="low", status="unavailable", reason=one,
                   notes=[f"不适用（原因：{one}）"])

    def as_dict(self) -> dict:
        """转成可 JSON 序列化的 dict。

        heatmap 默认丢弃；ndarray 不转 list，而是压成形状/dtype/min-max-mean 描述符
        （原因见 _plain 的注释：曾经因此产出 27 MB 一份的报告 JSON）。

        status/reason **只在非默认时输出** —— 这是冻结口径的硬要求：默认路径多一个键，
        三条冻结样张的判定 sha256 就会变（本轮硬门槛）。
        """
        d = {
            "name": self.name,
            "score": round(float(self.score), 4),
            "confidence": self.confidence,
            "raw": {k: _plain(v) for k, v in self.raw.items()},
            "regions": [{k: _plain(v) for k, v in r.items()} for r in self.regions],
            "notes": list(self.notes),
        }
        if self.status != "ok":
            d["status"] = self.status
            d["reason"] = self.reason
        return d

    def brief(self) -> str:
        if self.status != "ok":
            return f"[{self.name}] 不适用（原因：{self.reason}）"
        return f"[{self.name}] score={self.score:.3f} conf={self.confidence} regions={len(self.regions)}"


def _plain(v: Any) -> Any:
    """numpy → 原生 python，**但不把像素数组倒进 JSON**。

    为什么（实测教训）：证据 JSON 的用途是「能被引用、能被复核」，不是当像素仓库。
    一个 ELA 结果的 diff_img / block_mean / z 三个数组 tolist() 之后约 9.8 MB，
    一份完整报告 JSON 因此涨到 27~35 MB —— 人打不开、仓库装不下、评审不会看。
    而真正要看的那部分像素信息，已经落在 heatmap PNG 与四联证据图里了。

    所以数组一律换成**可复算的描述符**（形状 + dtype + min/max/mean）：
    想查细节就拿着报告里的参数把同一份输入重跑一遍 —— 这比从 JSON 里捞像素更符合
    本项目「可复现」的主张。原始数组仍留在内存里的 raw 中，供同进程可视化用。
    """
    if isinstance(v, (np.floating,)):
        return round(float(v), 4)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.ndarray):
        if v.size == 0:
            return {"__ndarray__": list(v.shape), "dtype": str(v.dtype), "empty": True}
        f = v.astype(np.float64, copy=False)
        return {"__ndarray__": list(v.shape), "dtype": str(v.dtype),
                "min": round(float(f.min()), 4), "max": round(float(f.max()), 4),
                "mean": round(float(f.mean()), 4)}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


DetectorFn = Callable[..., DetectionResult]


# ---------------------------------------------------------------- 预处理（手册 §0.3）


def load_and_prepare(path: str | Path) -> tuple[np.ndarray, dict]:
    """读图 + 抽取元数据。中文路径安全（cv2.imread 在 Windows 中文路径下会返回 None）。

    坏文件一律收敛到同一条中文 ValueError：0 字节文件会让 cv2.imdecode 直接抛
    `cv2.error: ... !buf.empty()`（空缓冲是断言失败，不是返回 None），
    而 cv2.error 不是 ValueError 的子类 —— 不接住它，用户看到的就是一行 OpenCV 内部断言，
    而不是"读不到图像"。注意 np.fromfile 留在 try 外：文件不存在时仍照旧抛 FileNotFoundError。
    """
    p = Path(path)
    buf = np.fromfile(str(p), dtype=np.uint8)
    try:
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:                                    # noqa: BLE001 —— 坏文件：cv2 抛 cv2.error
        img = None
    if img is None:
        raise ValueError(f"读不到图像（路径或格式问题）：{p}")

    meta: dict = {
        "path": str(p),
        "shape": tuple(int(x) for x in img.shape),   # (H, W, 3)
        "is_jpeg": p.suffix.lower() in (".jpg", ".jpeg"),
        "jpeg_qtables": None,
        "exif_present": False,
    }
    if meta["is_jpeg"]:
        try:
            from PIL import Image

            with Image.open(p) as im:
                qt = getattr(im, "quantization", None)
                meta["jpeg_qtables"] = {int(k): list(v) for k, v in qt.items()} if qt else None
                meta["exif_present"] = bool(im.getexif())
        except Exception:  # PIL 读不到不影响主流程
            pass
    return img, meta


def is_platform_recompressed(meta: dict) -> tuple[bool, list[str]]:
    """平台二次压缩粗判（手册 §1.6.3）。

    平台重压缩会覆盖原始压缩史 → ELA 整体失效，必须主动降权并声明。
    判据取 2 条以上即认为"疑似平台处理过"，宁可误判为"已处理"也不虚报证据力。
    """
    signals: list[str] = []
    if not meta.get("exif_present"):
        signals.append("EXIF 已剥离（平台上传的典型特征）")
    h, w = meta["shape"][0], meta["shape"][1]
    if min(h, w) in (1080, 1350, 1440, 720, 640):
        signals.append(f"短边={min(h, w)} 命中平台常见输出尺寸")
    return len(signals) >= 2, signals


# ---------------------------------------------------------------- 可视化


def render_heatmap_overlay(img_bgr: np.ndarray, heatmap01: np.ndarray, alpha: float = 0.5) -> np.ndarray:
    """热力图叠在灰度原图上（手册 §1.5 指定的物证形式）。"""
    h, w = img_bgr.shape[:2]
    hm = cv2.resize(heatmap01.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    hm = np.clip(hm, 0, 1)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    colored = cv2.applyColorMap((hm * 255).astype(np.uint8), cv2.COLORMAP_JET)
    return cv2.addWeighted(colored, alpha, cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), 1 - alpha, 0)


def render_diff_amplified(diff: np.ndarray, scale: float | None = None) -> np.ndarray:
    """经典 ELA 观感：差值图按固定/自动系数放大。仅影响可视化，不影响分数。"""
    d = diff.astype(np.float32)
    s = float(scale) if scale else (255.0 / max(float(d.max()), 1e-6))
    return np.clip(d * s, 0, 255).astype(np.uint8)


def draw_regions(img_bgr: np.ndarray, regions: list, thickness: int = 2) -> np.ndarray:
    """画可疑区域框：正向(偏亮→拼接嫌疑)红框，负向(偏暗→美颜嫌疑)蓝框。"""
    out = img_bgr.copy()
    for r in regions:
        x, y, w, h = r["bbox"]
        color = (0, 0, 255) if r.get("polarity") == "brighter" else (255, 128, 0)
        cv2.rectangle(out, (x, y), (x + w, y + h), color, thickness)
        tag = f"{r.get('polarity', '?')[:3]} z={r.get('z', 0):+.1f}"
        cv2.putText(out, tag, (x, max(14, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


_CJK_FONT_CACHE: dict = {}


def _cjk_font(size: int):
    """取中文字体（PIL）。cv2.putText 不能画中文（会变 ???），必须走 PIL。"""
    if size in _CJK_FONT_CACHE:
        return _CJK_FONT_CACHE[size]
    from PIL import ImageFont

    #: 顺序＝优先本机交付机的字体（Windows 的 msyh，字形与成片/证物图一致），
    #: 再退到 Linux 常见的中文字体包 —— 2026-10-09 CI 实测：runner 上只有 wqy 的路径、
    #: 而包里没装那个字体 ⇒ 证据图中文标注会退化成 `???`，守卫当场红。所以补上
    #: `fonts-noto-cjk` 的标准落点（CI 里也装了它）。**Windows 上的解析结果不变**。
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhl.ttc",
                 r"C:\Windows\Fonts\simhei.ttf",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                 "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"):
        if Path(cand).exists():
            try:
                f = ImageFont.truetype(cand, size)
                _CJK_FONT_CACHE[size] = f
                return f
            except Exception:  # noqa: BLE001
                continue
    _CJK_FONT_CACHE[size] = None
    return None


#: 证据图单面板的尺寸上限：宽 ≤ 900 且 高 ≤ 1400（**双向**限幅）。
PANEL_MAX_W, PANEL_MAX_H = 900, 1400


def _fit_panel(panel: np.ndarray, max_w: int = PANEL_MAX_W, max_h: int = PANEL_MAX_H) -> np.ndarray:
    """把单张面板缩进 max_w×max_h 的画框内，保持长宽比（小图仍照旧放大小图，口径不变）。

    为什么（实测教训）：旧口径是"宽度一律归一到 900、高度不管"。
    正常 3:4 图上没问题，但 9×400 的窄高图（长截图 / 聊天记录长图 / 窄长海报的常态）
    会被横向放大 100 倍 → 单面板 900×40000 px，证据图 71.5MB、峰值驻留 2.4GB、9.65s。
    反过来 400×9 的扁宽图只要 0.03s —— 差 1200 倍，瓶颈从来不在算法，在这个尺寸公式。

    用整数交叉相乘比较（w*max_h <= h*max_w ⇔ w/h <= max_w/max_h）而不是浮点 s=min(...)：
    避免 (1200,1600) 这类常规尺寸在浮点边界上算出 674 而不是 675，无谓地扰动旧证据图。
    常规 3:4 / 4:3 面板的结果与旧口径（int(h*900/w)）逐像素同尺寸。
    """
    h, w = panel.shape[:2]
    if w * max_h <= h * max_w:                        # 高度更紧：高顶上限，宽按比例（窄高图走这支）
        return cv2.resize(panel, (max(1, w * max_h // h), max_h))
    return cv2.resize(panel, (max_w, max(1, h * max_w // w)))


def _put_cjk(img: np.ndarray, text: str, org: tuple[int, int], size: int = 26,
             color=(255, 255, 255), center_x: bool = False) -> np.ndarray:
    """在中/英文混排下画标签：有中文字体走 PIL，否则退化为 cv2 ASCII。

    只在**标签落点所在的那一小块 ROI** 上做 BGR→PIL→BGR 往返，不整张画布转。
    为什么（实测教训）：证据图要画 5 个标签，整画布往返每次都要复制一份 W×H×3；
    窄高图的面板被拉高到 40000px 时，这一项单独就把峰值顶到 GB 级。
    像素结果与整画布版一致：文字是位置确定的合成，落在 ROI 内的像素只由该处像素决定，
    而 BGR→RGB 是纯通道置换（无损），ROI 之外的像素本来就一个都不该动。
    """
    font = _cjk_font(size)
    if font is None:
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX,
                    size / 30.0, color, 1, cv2.LINE_AA)
        return img
    from PIL import Image, ImageDraw

    box = font.getbbox(text)                      # 相对绘制原点的墨迹框：左/上/右/下
    x, y = org
    if center_x:
        x = max(0, x - (box[2] - box[0]) // 2)    # 与改动前同一居中口径（同一 font.getbbox）

    h, w = img.shape[:2]
    pad = 6                                       # 抗锯齿会溢出墨迹框 1~2px，留足余量
    x0, y0 = max(0, x + box[0] - pad), max(0, y + box[1] - pad)
    x1, y1 = min(w, x + box[2] + pad), min(h, y + box[3] + pad)
    if x1 <= x0 or y1 <= y0:                      # 标签整个落在画布外：与整画布版一样不画
        return img

    pil = Image.fromarray(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2RGB))
    ImageDraw.Draw(pil).text((x - x0, y - y0), text, font=font,
                             fill=(color[2], color[1], color[0]))
    img[y0:y1, x0:x1] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    return img


def make_evidence_sheet(img_bgr: np.ndarray, res: DetectionResult, path: str | Path,
                        title: str = "", panel_labels: list | None = None) -> str:
    """四联证据图：原图 | 差值图(放大) | 热力图叠加 | 区域框。带中文标题与面板说明。

    为什么要费劲上 PIL：证据图是给人（评审）和给 VL 模型看的，
    cv2 画中文会变成一串 ???，等于没有标注。
    """
    panels = [img_bgr]
    names = ["① 原图"]

    diff = res.raw.get("diff_img")
    if diff is not None:
        d = render_diff_amplified(diff, res.raw.get("viz_scale"))
        panels.append(cv2.cvtColor(d, cv2.COLOR_GRAY2BGR))
        names.append("② ELA 差值图（放大）")
    if res.heatmap is not None:
        panels.append(render_heatmap_overlay(img_bgr, res.heatmap))
        names.append("③ 热力图叠加（红黄=残差异常）")
    panels.append(draw_regions(img_bgr, res.regions) if res.regions else img_bgr.copy())
    names.append("④ 可疑区域框（红=偏亮/拼接嫌疑，蓝=偏暗/美颜嫌疑）")
    if panel_labels:
        names = [f"{n}  {e}" if e else n for n, e in zip(names, panel_labels)]

    scaled = [_fit_panel(p) for p in panels]
    ph = max(p.shape[0] for p in scaled)
    widths = [p.shape[1] for p in scaled]          # 限幅后各面板宽度可能不同（窄高 vs 扁宽）
    title_h, cap_h, gap = 58, 62, 10
    W = sum(widths) + gap * (len(scaled) - 1)
    sheet = np.full((title_h + ph + cap_h, W, 3), 24, np.uint8)

    head = res.brief() if not title else f"{title}  |  {res.brief()}"
    sheet = _put_cjk(sheet, head, (14, 12), size=28, color=(120, 230, 255))

    x = 0
    for i, p in enumerate(scaled):
        pw = widths[i]
        sheet[title_h : title_h + p.shape[0], x : x + pw] = p
        sheet = _put_cjk(sheet, names[i], (x + pw // 2, title_h + ph + 14),
                         size=24, color=(210, 210, 210), center_x=True)
        x += pw + gap
    cv2.line(sheet, (0, title_h - 6), (W, title_h - 6), (70, 70, 70), 1)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", sheet)[1].tofile(str(path))     # 中文路径安全
    return str(path)


def save_result(res: DetectionResult, outdir: str | Path, tag: str) -> dict:
    """落盘：结构化 JSON + 证据图。返回落盘清单。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    js = outdir / f"{tag}_{res.name}.json"
    js.write_text(json.dumps(res.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    files = {"json": str(js)}
    if res.heatmap is not None or res.raw.get("diff_img") is not None:
        files["heatmap"] = str(outdir / f"{tag}_{res.name}_heatmap.png")
        hm = res.heatmap if res.heatmap is not None else np.zeros(res.raw["diff_img"].shape, np.float32)
        cv2.imencode(".png", (np.clip(hm, 0, 1) * 255).astype(np.uint8))[1].tofile(files["heatmap"])
    return files