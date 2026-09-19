"""F2 标识核验层：文件元数据 / 隐式标识（C2PA、SD 参数、生成器签名）。

对外只需两个名字：read_provenance / finalize_state。
"""

from .metadata import (  # noqa: F401
    AI_TOOL_PATTERNS,
    NAME,
    finalize_state,
    read_provenance,
)

__all__ = ["AI_TOOL_PATTERNS", "NAME", "finalize_state", "read_provenance"]