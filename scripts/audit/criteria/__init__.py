"""★ `scripts/audit/criteria` 包标记 —— ★ 导入即完成 **22** 条判据的注册（`AUD-G1` 与 `AUD-C10`
由注册表自检 / 入口事实注入产出）。"""

from . import (
    audit_log,  # noqa: F401
    bind_source,  # noqa: F401
    docs_numbers,  # noqa: F401
    guard,  # noqa: F401
    inscope_residue,  # noqa: F401
    locale_toplevel,  # noqa: F401
    makefile_freeze,  # noqa: F401
    makefile_gate,  # noqa: F401
    migrations,  # noqa: F401
    permissions,  # noqa: F401
    plaintext_columns,  # noqa: F401
    secrets,  # noqa: F401
    tree_discipline,  # noqa: F401
)
