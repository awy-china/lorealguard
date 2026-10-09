"""Agent 处置建议层（`voiceguard.agent`）。"""

from .advisor import Decision, advise, render_decision          # noqa: F401

__all__ = ["Decision", "advise", "render_decision"]