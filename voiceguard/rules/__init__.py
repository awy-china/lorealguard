"""F7 规则层：分级判定。

对外只需 load_rules / evaluate / describe。
阈值与措辞在 rules.yaml 里，代码里一个阈值数字都没有（改判定标准 = 改表，不改代码）。
"""

from .engine import (  # noqa: F401
    DEFAULT_RULES,
    compare,
    describe,
    evaluate,
    load_rules,
    referenced_laws,
    rule_ids,
    selfcheck,
)

__all__ = ["DEFAULT_RULES", "compare", "describe", "evaluate", "load_rules",
           "referenced_laws", "rule_ids", "selfcheck"]