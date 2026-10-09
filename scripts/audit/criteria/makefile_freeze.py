"""★ `AUD-G2` `Makefile` 的 `all:` **必需腿序列**冻结（回归判据 · `PLAN §2` · `P3-23` 改判据结构）。

★ `P3-23`（为什么不再是「逐字行冻结」）：`all:` 那一行会**合法地**长出新腿 —— `lint-frontend`
  就是这么加进来的；逐字冻结的常量（旧 `MAKEFILE_FROZEN_LINE`）跟不上 ⇒ **恒红门**
  （一道永远红的门 = 没有门），还会淹没真回归。★ 逐字冻结是错的工具。
★ 现在的判据 = **结构化比较**（常量单一出处 = `registry.MAKEFILE_ALL_REQUIRED`）：
  ① `all:` 的依赖序列**首位**必须是写者 `format-all`（写后读；与 `AUD-C3` 同因）；
  ② 必需腿必须**按序全部在位**（允许其间/其后**新增**任意合法腿）。
  ⇒ ★ 合法新增腿 = 不红 · ★ 删一条必需腿 / 打乱必需腿顺序 / 把 `format-all` 挪离首位 = 红。
★ 行号**不参与判定**（`all:` 在文件里的行号漂移 = 合法编辑）。
★ 词法（与 `makefile_gate._targets` 同款）：目标行 = 列 0 的 `name:`（排除 tab 配方行与 `#` 注释；
  `(?!=)` 排除 `:=` 赋值行）。
"""

import re

from .. import registry as reg

TARGET_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*):(?!=)(.*)$")


def _all_deps(lines):
    """★ 定位 `all:` 目标行 ⇒ `(lineno, deps)`；★ 找不到 ⇒ `None`（调用方 fail-closed）。"""
    name = reg.MAKEFILE_ALL_TARGET.rstrip(":")
    for index, text in enumerate(lines):
        if not text or text[0] in "\t#":
            continue
        match = TARGET_RE.match(text)
        if match is not None and match.group(1) == name:
            return index + 1, match.group(2).strip().split()
    return None


@reg.criterion("AUD-G2", reg.REGRESSION)
def check_makefile_freeze(ctx):
    path = ctx.repo / reg.MAKEFILE_NAME
    if not path.is_file():
        return reg.Result(
            "AUD-G2", reg.REGRESSION, reg.UNKNOWN, message="Makefile 读不到 ⇒ 不得记绿"
        )
    try:
        lines = reg.read_lines(path)
    except (OSError, UnicodeDecodeError) as exc:
        return reg.Result(
            "AUD-G2", reg.REGRESSION, reg.UNKNOWN, message=f"Makefile 读不到（{exc}）⇒ 不得记绿"
        )
    found = _all_deps(lines)
    if found is None:
        return reg.Result(
            "AUD-G2",
            reg.REGRESSION,
            reg.FAIL,
            message=f"Makefile 无 `{reg.MAKEFILE_ALL_TARGET}` 目标行 ⇒ 必需腿是否在位判不了",
            files=[reg.MAKEFILE_NAME],
        )
    lineno, deps = found
    where = (
        f"{reg.MAKEFILE_NAME}:{lineno} 的 `{reg.MAKEFILE_ALL_TARGET}` 依赖序列 = "
        f"{' '.join(deps) or '（空）'}"
    )
    if not deps:
        return reg.Result(
            "AUD-G2",
            reg.REGRESSION,
            reg.FAIL,
            checked=1,
            message=f"{where} ⇒ ★ 空跑，不计证据",
            files=[reg.MAKEFILE_NAME],
        )
    missing = [leg for leg in reg.MAKEFILE_ALL_REQUIRED if leg not in deps]
    if missing:
        return reg.Result(
            "AUD-G2",
            reg.REGRESSION,
            reg.FAIL,
            checked=1,
            message=f"{where} ⇒ ★ 缺必需腿 {' '.join(missing)}"
            f"（冻结序列 = {' '.join(reg.MAKEFILE_ALL_REQUIRED)}）",
            files=[reg.MAKEFILE_NAME],
        )
    if deps[0] != reg.MAKEFILE_ALL_WRITER:
        return reg.Result(
            "AUD-G2",
            reg.REGRESSION,
            reg.FAIL,
            checked=1,
            message=f"{where} ⇒ ★ 写者 `{reg.MAKEFILE_ALL_WRITER}` 不在首位（实际首位 = "
            f"{deps[0]!r} · 写者落在第 {deps.index(reg.MAKEFILE_ALL_WRITER) + 1} 位）"
            "⇒ 写后读顺序被破坏",
            files=[reg.MAKEFILE_NAME],
        )
    positions = [deps.index(leg) for leg in reg.MAKEFILE_ALL_REQUIRED]
    if positions != sorted(positions):
        detail = " · ".join(
            f"{leg}@{positions[index] + 1}" for index, leg in enumerate(reg.MAKEFILE_ALL_REQUIRED)
        )
        return reg.Result(
            "AUD-G2",
            reg.REGRESSION,
            reg.FAIL,
            checked=1,
            message=f"{where} ⇒ ★ 必需腿顺序错误（要求 = {' '.join(reg.MAKEFILE_ALL_REQUIRED)}"
            f" · 实测位次 {detail}）",
            files=[reg.MAKEFILE_NAME],
        )
    extra = [leg for leg in deps if leg not in reg.MAKEFILE_ALL_REQUIRED]
    return reg.Result(
        "AUD-G2",
        reg.REGRESSION,
        reg.OK,
        checked=1,
        message=f"{where} ⇒ 必需腿 {len(reg.MAKEFILE_ALL_REQUIRED)} 条**按序在位** · "
        f"写者 `{reg.MAKEFILE_ALL_WRITER}` 居首位 · ★ 额外腿 {len(extra)} 个"
        f"（不计红：{' '.join(extra) or '（无）'}）",
        files=[reg.MAKEFILE_NAME],
    )
