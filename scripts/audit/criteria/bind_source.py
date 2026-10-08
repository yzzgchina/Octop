"""★ `P1-4` 绑定地址单源判据（序号 27 · A 类 · `check_bind_source`）。

★ 唯一契约来源 = `team/2026-10-08-110000/CRITERIA-29-CLASSIFICATION.md` §1 序号 27（+ §3 三条红线）。
★ 内容：Octop 绑定地址的**唯一来源** = `~/.octop/config.json` 的 `bind_host`（plist **不传** `--host`）。
★ 输入 = `~/Library/LaunchAgents/octop.plist` 的 `ProgramArguments` + `~/.octop/config.json` 的 `bind_host`。
★ 返红条件：plist 的 `ProgramArguments` **出现** `--host`（含 `--host=<值>` 形态）⇒ 红（破坏单源）。

★★ 本判据的特殊之处（★ 它决定了三态怎么写）：★ 它的输入**在仓库工作区之外** ⇒ 换台机器 / 换用户时
   这两个路径可能根本不存在 ⇒ ★ **必须 fail-closed**：读不到 plist **或** 读不到 `config.json` ⇒
   `UNKNOWN`（★ 不是绿、**也不是红**）· ★ 绝不「读不到 ⇒ 当作没问题 ⇒ 绿」—— 那正是本项目的头号病
   「真空满足」（承 `CRITERIA-29-CLASSIFICATION.md` §3 红线 2 第 3 条）。
★ `R8`（`team/STANDING-RULES.md`）：★ **三态里的每一个**都在输出里打印**实际探测的基准目录**。
★ 夹具 / 参数化根目录（★ 判别性对照**不碰真实 `~`** · 承 `R8`「调用方必须显式传」）：
   `OCTOP_AUDIT_LAUNCHD_DIR` → 默认 `$HOME/Library/LaunchAgents`；
   `OCTOP_AUDIT_OCTOP_HOME`  → 默认 `$HOME/.octop`。
★ `files` = 两份探测目标的**绝对**路径。★ 两条理由：① `digest_files` 的 `Path(root) / <abs>` = `<abs>`
   ⇒ 输入指纹**真正覆盖这两份仓外文件的内容**（plist / config 一改 ⇒ 树指纹必变）；
   ② 输入面**永不空**（★ 读不到也如实登记为输入）⇒ 本判据自己显式判 `UNKNOWN`，而不是被
   `current_tree.py:48` 的空输入守卫改成 `FAIL` —— ★ 两态都**不是绿**，故不构成假绿通道。
★ 边界（承 §1 序号 27 的「判据面 = 我的产物」· `R6`）：★ 只判 **plist + config 两份文件**，
   ★ **不判**「服务对外实际监听什么」（那是序号 13 / 23 的 `launchd` 域）。
"""

import json
import os
import pathlib
import plistlib

from .. import registry as reg


def _probe_dir(env_name, default_rel):
    """★ 参数化探测根：`env` 覆盖优先（显式传参 · 承 `R8`），否则 `$HOME/<default_rel>`。"""
    raw = os.environ.get(env_name)
    if raw:
        return pathlib.Path(raw).expanduser()
    return pathlib.Path.home() / default_rel


def _host_flag_hits(arguments):
    """★ `--host` / `--host=<值>` ⇒ 命中。

    ★ 只认**旗标名本身**：`--hostname` 这类「同前缀异名」旗标**不得**误判（否则 = 假红）。
    """
    flag = reg.BIND_HOST_FLAG
    return [item for item in arguments if item == flag or item.startswith(flag + "=")]


@reg.criterion("AUD-C11", reg.HARD)
def check_bind_source(ctx):
    """★ 三态：plist 传 `--host` ⇒ `FAIL`；读不到任一输入 ⇒ `UNKNOWN`；其余 ⇒ `OK`。"""
    launchd_dir = _probe_dir(reg.BIND_LAUNCHD_DIR_ENV, reg.BIND_LAUNCHD_DIR_REL)
    octop_dir = _probe_dir(reg.BIND_OCTOP_HOME_ENV, reg.BIND_OCTOP_HOME_REL)
    plist = launchd_dir / reg.BIND_PLIST_NAME
    config = octop_dir / reg.BIND_CONFIG_NAME
    # ★ `R8`：基准目录是**实际探测到的绝对路径**（★ 不是常量表里的默认值）
    base = f"★ 基准目录（实际探测 · 承 R8）：plist = {plist} · config = {config}"
    # ★ `files` 恒为两份探测目标 ⇒ 输入面永不空（★ 见模块 docstring）
    files = [str(plist), str(config)]
    claim = (
        f"文档声称：绑定地址的**唯一来源** = `{reg.BIND_CONFIG_KEY}`（"
        f"`{reg.BIND_OCTOP_HOME_REL}/{reg.BIND_CONFIG_NAME}`）"
        f"· `ProgramArguments` **不传** `{reg.BIND_HOST_FLAG}`"
    )

    if not plist.is_file():
        return reg.Result(
            "AUD-C11",
            reg.HARD,
            reg.UNKNOWN,
            message=f"实测量：plist **读不到**（{plist} 不存在）⇒ ★ 未知 ≠ 绿 ⇒ 不得记绿；{claim}；"
            f"应改成：先确认本机/本用户确有该 plist，或用 `{reg.BIND_LAUNCHD_DIR_ENV}` 指向"
            f"**可读的探测根**后再判 · {base} · ★ 空跑，不计证据",
            files=files,
        )

    try:
        with plist.open("rb") as handle:
            table = plistlib.load(handle)
    except (OSError, ValueError) as exc:
        return reg.Result(
            "AUD-C11",
            reg.HARD,
            reg.UNKNOWN,
            message=f"实测量：plist **解析失败**（{type(exc).__name__}: {exc}）⇒ ★ 未知 ≠ 绿 ⇒ 不得记绿；"
            f"{claim}；应改成：确认 plist 是合法 XML plist 后复跑 · {base} · ★ 空跑，不计证据",
            files=files,
        )

    arguments = table.get("ProgramArguments") if isinstance(table, dict) else None
    if not isinstance(arguments, list) or not arguments:
        return reg.Result(
            "AUD-C11",
            reg.HARD,
            reg.UNKNOWN,
            message=f"实测量：plist 的 `ProgramArguments` **缺失或非列表**（实测 {arguments!r} · "
            f"顶层键 = {sorted(table) if isinstance(table, dict) else type(table).__name__}）"
            f"⇒ ★ 无法判定是否传 `{reg.BIND_HOST_FLAG}` ⇒ 未知 ≠ 绿 ⇒ 不得记绿；{claim}；"
            f"应改成：确认该 plist 的 `ProgramArguments` 形态后再判 · {base}",
            files=files,
        )

    program_arguments = [item for item in arguments if isinstance(item, str)]
    hits = _host_flag_hits(program_arguments)
    if hits:
        return reg.Result(
            "AUD-C11",
            reg.HARD,
            reg.FAIL,
            checked=1,
            message=f"实测量：plist 的 `ProgramArguments` = {program_arguments}，其中**命中** "
            f"`{reg.BIND_HOST_FLAG}` 的实参 = {hits}（{len(hits)} 处）⇒ 绑定地址出现**第二个来源**；"
            f"{claim}；应改成：删掉 plist 里的 {hits}（★ 承 §3 红线 1 的第三元）"
            f"（改绑定地址一律只动 `{reg.BIND_CONFIG_KEY}`）· {base}",
            files=files,
        )

    if not config.is_file():
        return reg.Result(
            "AUD-C11",
            reg.HARD,
            reg.UNKNOWN,
            checked=1,
            message=f"实测量：`config.json` **读不到**（{config} 不存在）⇒ ★ 唯一来源不可证 ⇒ 未知 ≠ 绿 ⇒ "
            f"不得记绿；{claim}；应改成：先确认 `{reg.BIND_OCTOP_HOME_REL}` 下有 `config.json`，"
            f"或用 `{reg.BIND_OCTOP_HOME_ENV}` 指向**可读的探测根**后再判 · {base} · ★ 空跑，不计证据",
            files=files,
        )

    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return reg.Result(
            "AUD-C11",
            reg.HARD,
            reg.UNKNOWN,
            checked=1,
            message=f"实测量：`config.json` **解析失败**（{type(exc).__name__}: {exc}）⇒ ★ 唯一来源不可证 ⇒ "
            f"未知 ≠ 绿 ⇒ 不得记绿；{claim}；应改成：确认 config.json 是合法 JSON 后复跑 · {base} "
            "· ★ 空跑，不计证据",
            files=files,
        )

    bind_host = payload.get(reg.BIND_CONFIG_KEY) if isinstance(payload, dict) else None
    if not isinstance(bind_host, str) or not bind_host:
        return reg.Result(
            "AUD-C11",
            reg.HARD,
            reg.UNKNOWN,
            checked=1,
            message=f"实测量：`config.json` 里 `{reg.BIND_CONFIG_KEY}` **缺失/非字符串/为空**"
            f"（实测 {bind_host!r}）⇒ ★ 被声称的**唯一来源本身不可证** ⇒ 未知 ≠ 绿 ⇒ 不得记绿；"
            f"{claim}；应改成：补上 `{reg.BIND_CONFIG_KEY}`（★ 绑定地址只此一处）· {base}",
            files=files,
        )

    return reg.Result(
        "AUD-C11",
        reg.HARD,
        reg.OK,
        checked=2,
        message=f"实测量：plist 的 `ProgramArguments` = {program_arguments}（`{reg.BIND_HOST_FLAG}` 命中 "
        f"**0** 处）· `config.json` 的 `{reg.BIND_CONFIG_KEY}` = {bind_host!r}；{claim}；"
        f"应改成：保持现状（★ 改绑定地址只动 `{reg.BIND_CONFIG_KEY}`，"
        f"**不得**在 plist 里补 `{reg.BIND_HOST_FLAG}`）· {base}",
        files=files,
    )
