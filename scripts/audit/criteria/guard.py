"""★ `AUD-G1` 未知格式守卫（横切 · 注册表自检 · `PLAN §2`/`§8`）。
★ `AUD-C10` 树指纹（横切 · `P1-2` 序号 20 —— ★ **不新建模块**，扩面既有横切族）。

★ 两条判据的事实都由入口在跑完其余判据后注入（`ctx.guard_facts` / `ctx.input_digests`）
  ⇒ ★ 因此它们在注册表里都排在**最后**（`current_tree.py` 按 `FROZEN_IDS` 排序）。
"""

from .. import registry as reg

FINGERPRINT_ID = reg.FINGERPRINT_ID


@reg.criterion("AUD-G1", reg.GUARD)
def check_registry_guard(ctx):
    """★ 违规 ⇒ `UNKNOWN` ⇒ `exit 2`（fail-closed · ★ 绝不记绿）。"""
    facts = getattr(ctx, "guard_facts", None)
    if facts is None:
        facts = reg.registry_guard_facts([], [])
    return reg.guard_result(facts)


@reg.criterion(FINGERPRINT_ID, reg.GUARD)
def check_tree_fingerprint(ctx):
    """★ 序号 20：证据必须带**单一**树指纹（派生自各判据 `input_digest` · 与时刻无关）。

    ★ 返红条件（本项的**可判部分**）：① 任一判据的输入面为空（`input_digest.files` 为空 ⇒ 该判据
      本就是 `FAIL`，此处再断言一次）；② 注入的指纹 ≠ 由逐项 `input_digest` 重算的指纹 ⇒ 红；
      ③ 存在性子句：未注入输入面（= 空跑）⇒ `UNKNOWN`。
    ★ 判不了的部分（★ 承分类表 §4 序号 20 的副面）：「现在是不是写者活跃期」是**并发进程状态**
      ⇒ 不在判据面内，只能在报告里写明。
    """
    entries = getattr(ctx, "input_digests", None)
    if not entries:
        return reg.Result(
            FINGERPRINT_ID,
            reg.GUARD,
            reg.UNKNOWN,
            message="未注入各判据 input_digest ⇒ 空跑，不计证据（★ 请经入口 `current_tree.py` 运行）⇒ 不得记绿",
        )
    empty = sorted(cid for cid, (names, _) in entries.items() if not names)
    if empty:
        return reg.Result(
            FINGERPRINT_ID,
            reg.GUARD,
            reg.FAIL,
            checked=len(entries),
            message=f"实测量：输入面为空的判据 {len(empty)} 条（{' · '.join(empty)}）；"
            "文档声称：每条判据都有非空输入面（`current_tree.py:48` 空输入守卫）；"
            "应改成：给缺失判据补上判据面，否则该条属**假绿**",
            files=sorted(entries),
        )
    recomputed = reg.tree_fingerprint(entries)
    injected = getattr(ctx, "tree_fingerprint", None)
    files = sorted({name for names, _ in entries.values() for name in names})
    if injected != recomputed:
        return reg.Result(
            FINGERPRINT_ID,
            reg.GUARD,
            reg.FAIL,
            checked=len(entries),
            message=f"实测量：注入的树指纹 {injected!r} ≠ 由逐项 input_digest 重算的 {recomputed!r}；文档声称："
            "「证据工件在同一指纹下可复算」；应改成：指纹必须**派生自**逐项 input_digest（不得与输入面脱钩）",
            files=files,
        )
    return reg.Result(
        FINGERPRINT_ID,
        reg.GUARD,
        reg.OK,
        checked=len(entries),
        message=f"实测量：树指纹 = {recomputed}（派生自 {len(entries)} 条判据的 input_digest · "
        "逐项复算一致 · 与时刻无关）；文档声称：证据工件里的旧指纹与本指纹一致；应改成：在同一指纹下"
        "复算（★ 指纹变了 ⇒ 旧证据失效）· ★ 判不了的部分：「现在是不是写者活跃期」= 并发进程状态 "
        "⇒ 报告纪律",
        files=files,
    )
