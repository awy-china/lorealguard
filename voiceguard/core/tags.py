r"""tag 卫生：产物文件名前缀的**唯一一份**规则（网页与命令行共用）。

为什么必须有这一处（2026-09-25 · round8 缺陷④⑤）：
    `python -m voiceguard <图> --out out --tag '../_ESCAPED'` 修前会把产物写到 `out` 的
    **上级目录**，rc 还是 0 —— 因为 `voiceguard/report/ledger.py:296` 只是
    `d / f"{tag}_report.md"`，tag 一路透传（`voiceguard/__main__.py:35`）；
    `--tag 'a:b'` 在 Windows 上更是裸 `FileNotFoundError` 栈（`:` 不是合法文件名字符）。
    tag 是**输入**：输入边界必须自己把关，且只把关一处。

规则与页面原先那条 `TAG_RE`（`demo/serve.py:58`）**完全一致**，只是搬了个家 ——
所以"合法 tag 的行为与今天一模一样"，产物名一个字都不变。

    ^[A-Za-z0-9_\-]{1,64}$    再排除 Windows 保留设备名（CON / PRN / AUX / NUL / COM1..9 / LPT1..9）

为什么连保留名也挡：Windows 上 `CON_report.md` 至今打不开（保留名连加扩展名都不行），
真跑起来会是又一个"文件写了但读不回来"的哑雷 —— 而它和路径穿越一样，是**输入**带来的。
"""

from __future__ import annotations

import re

#: 长度上限（与页面原口径一致）。
TAG_MAX = 64

#: 唯一的一份白名单：字母 / 数字 / 下划线 / 连字符。
TAG_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

#: Windows 保留设备名（不区分大小写，连带扩展名都不许用）。
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

_SEP = frozenset("/\\")


def tag_error(tag) -> str | None:
    """合法 → None；非法 → **一句中文原因**（受控错误，可直接给用户看）。

    分支顺序是有意的：先给"最像人话的原因"（空 / 太长 / 路径分隔符 / 保留名），
    最后才落到笼统的白名单提示 —— 用户看到 `../_ESCAPED` 被拒时，该看到的是
    "不许含路径分隔符"，而不是"只允许字母数字"。
    """
    if not isinstance(tag, str):
        return "tag 必须是字符串"
    if tag == "":
        return "tag 不许为空"
    if len(tag) > TAG_MAX:
        return f"tag 太长（上限 {TAG_MAX} 字符，给的是 {len(tag)} 个）"
    if _SEP & set(tag):
        return "tag 不许含路径分隔符（/ 或 \\）—— 它只是产物文件名的前缀"
    if tag in (".", ".."):
        return "tag 不许是 . 或 .."
    if tag.upper() in RESERVED_NAMES:
        return f"tag 不许用 Windows 保留设备名（{tag.upper()}）—— 这样的文件名打不开"
    if not TAG_RE.match(tag):
        return "tag 只允许字母/数字/下划线/连字符，长度 1–64"
    return None


def is_valid_tag(tag) -> bool:
    return tag_error(tag) is None


def validate_tag(tag) -> str:
    """非法就抛 ValueError（消息即上面的中文原因），合法原样返回。"""
    err = tag_error(tag)
    if err:
        raise ValueError(err)
    return tag


__all__ = ["TAG_MAX", "TAG_RE", "RESERVED_NAMES", "tag_error", "is_valid_tag", "validate_tag"]
