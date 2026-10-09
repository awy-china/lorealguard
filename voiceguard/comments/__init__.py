"""F5 评论区真实性层 —— 包入口。"""

from .evaluate import CommentVerdict, evaluate          # noqa: F401
from .signals import CommentSignals, analyze            # noqa: F401


def evaluate_thread(thread, rules):
    """一行拿到（信号, 结论）—— guard 主流程与命令行演示共用这一个入口。"""
    sig = analyze(thread)
    return sig, evaluate(thread, rules, sig)


__all__ = ["CommentSignals", "CommentVerdict", "analyze", "evaluate", "evaluate_thread"]