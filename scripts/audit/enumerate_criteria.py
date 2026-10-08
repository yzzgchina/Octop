#!/usr/bin/env python3
"""★ 分流清单枚举器 —— ★ 把「甲·可真判 41 条 / 已实现 2 条 / 其余 39 条」变成【可复算读数】。

★★ **为什么需要它**（`AUDIT §2` 的「可复算」+ `L` 批「读数不是证据直到它可复算」）：
  ★ `AUDIT §2` / `RESEARCH §1.3` 只落了**三个聚合数**（甲 41 · 乙 34 · 丙 57 · 分母 132）
  ★ 与一张**按【节】归类**的理由表 ⇒ ★★ **【没有逐条名单】** ✗ ——
  ★ 而那张表最大的一格逐字写的是「**乙为主（部分甲）**」⇒ ★★ 它**不能**把 41 还原成 41 个条目 ✗。
  ★★ **注意区分**：★ `41 + 34 + 57 = 132` **恰好相等** ⇒ ★ 当时**确实**逐条分了类 ✓ ——
  ★★ **但那份逐条结果【未落盘】** ✗ ⇒ ★★ **结论 = 名单【丢了】，不是【不存在】** ✓
  ⇒ ★★★ 因此「其余 39 条」**无法【接着做】** ✗ · ★ 只能**在当前树上【重新分流】** ✓（★ 而那是**口径决定** ⇒ ★ 须用户拍板 ✓）。

★★ **本工具只做【可机械推导】的部分** ✓ · ★★ **绝不替用户做那个决定** ✗：
  ① ★ 按**当前树**枚举 `team/LEARNINGS.md` 的节 / 条目（★ 带行号坐标 ✓）；
  ② ★ 套用**冻结的**按节分流映射（★ 来源 = `RESEARCH §1.3` 表 · 逐行誊抄 ⇒ 见 `TRIAGE_MAP` ✓）；
  ③ ★★ 把**推导不出来**的条目【显式标成】`混合·未解析` / `分项节内·未点名` / `未分流` ✗
     —— ★★ **不得**把它们并进「甲」凑数 ✗（★ 那就是**真空满足** ✓）；
  ④ ★ 打印**漂移**：★ 分流时分母 `132` vs 当前实测（★ 两个数都对 · 差在**时点** ✓）。

★★ **本工具【不是】判据** ✗ —— ★ 它**不返红**（★ `exit 0` 恒定 · 除自检失败 ✓）· ★ 它只**出清单**。
  ★ 理由：★「覆盖率该到多少才算合规」**是口径决定** ✗ ⇒ ★ 没有用户拍板前，**任何阈值都是我自己编的** ✗。

★★ **运行口径（必须）**：★ **`./.venv/bin/python`**（★ 与 `AUD-S6` 同 · 见 `plaintext_columns.py` 头部 ✓）。

用法::

    ./.venv/bin/python scripts/audit/enumerate_criteria.py              # 人读清单
    ./.venv/bin/python scripts/audit/enumerate_criteria.py --json       # 机读
    ./.venv/bin/python scripts/audit/enumerate_criteria.py --self-test  # ★ 阳性对照（必跑）
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
LEARNINGS_REL = "team/LEARNINGS.md"

# ─── 分类标签（★ 五类 · ★ 后三类都【不是甲】⇒ 不得凑数）──────────────────────────
JIA = "甲·可真判"
YI = "乙·只提示"
BING = "丙·只能人工"
MIXED = "混合·未解析"  # ★ 来源表逐字「乙为主（部分甲）」⇒ ★ 无法逐条定甲
UNSPEC = "分项节内·未点名"  # ★ 该节只点名了部分条目，本条未被点到
UNTRIAGED = "未分流"  # ★ 该节【不在】分流表里（分流之后新增的节）

NOT_JIA = (YI, BING, MIXED, UNSPEC, UNTRIAGED)

# ─── ★★ 冻结的分流映射（★ 逐行誊抄 `RESEARCH §1.3` 的「分流依据」表 · 不得增删）───────
#
#   行1  §1（i18n）· §3（代码地图）· §4（本机环境）· §6（前端坑）· §16.1–16.4   ⇒ 甲
#   行2  §2 · §7.2–7.5 · §9.5 · §16.2                                          ⇒ 甲
#   行3  §8 · §9.1–9.4 · §10 · §11 · §13 · §14 · §15 · §17 · §19 · §20         ⇒ 乙为主（部分甲）
#   行4  §5.2 · §5.5 · §11.6 · §7.1 · §20.4                                    ⇒ 丙
#   行5  §14.2 · §18.1–18.3                                                    ⇒ 甲（本机可跑）
#
TRIAGE_PROVENANCE = "team/2026-09-29-091422/RESEARCH.md §1.3「分流依据（按节归类）」表"

SECTION_DEFAULT = {
    1: JIA,
    2: JIA,
    3: JIA,
    4: JIA,
    6: JIA,  # 行1 + 行2 的整节项
    8: MIXED,
    10: MIXED,
    11: MIXED,
    13: MIXED,  # 行3
    14: MIXED,
    15: MIXED,
    17: MIXED,
    19: MIXED,
    20: MIXED,
    5: UNSPEC,
    7: UNSPEC,
    9: UNSPEC,
    16: UNSPEC,
    18: UNSPEC,  # ★ 只点名了部分条目的节
}

# ★ 条目级规则（★ 优先于节默认 ✓）：(节, 起, 止) ⇒ 类
ENTRY_RANGES = (
    (16, 1, 4, JIA),  # 行1：§16.1–16.4
    (7, 2, 5, JIA),  # 行2：§7.2–7.5
    (18, 1, 3, JIA),  # 行5：§18.1–18.3
    (9, 1, 4, MIXED),  # 行3：§9.1–9.4
)
# ★ 条目级点名（★ 最高优先 ✓）
ENTRY_EXACT = {
    "9.5": JIA,  # 行2
    "14.2": JIA,  # 行5
    "5.2": BING,
    "5.5": BING,
    "7.1": BING,
    "11.6": BING,
    "20.4": BING,  # 行4
    # ★★ 来自**另一张表** `RESEARCH §2.4`（★ 不是 §1.3 ✓）：★ 它逐条判了两个候选【可真判】：
    #   ★ 「文档数字带坐标」（§16.9 / `O6`）⇒ ★★ **可真判** ✓（★ 逐字 ✓）
    #   ★ 「水位锚定计数」（§3.1(c) ⇒ 对应 §16.4）⇒ 已被 §1.3 行1 覆盖为甲 ✓
    "16.9": JIA,
}
# ★ 点名的来源（★ 逐条可溯 ⇒ 不得只写结论 ✓）
ENTRY_EXACT_PROVENANCE = {
    "9.5": "RESEARCH §1.3 行2",
    "14.2": "RESEARCH §1.3 行5",
    "5.2": "RESEARCH §1.3 行4",
    "5.5": "RESEARCH §1.3 行4",
    "7.1": "RESEARCH §1.3 行4",
    "11.6": "RESEARCH §1.3 行4",
    "20.4": "RESEARCH §1.3 行4",
    "16.9": "RESEARCH §2.4 候选表（逐字「「文档数字带坐标」（§16.9 / O6）」⇒ 可真判）",
}

# ─── ★★ 已实现判据 ⇒ 覆盖到哪条条目（★ 只登记【声明可溯】的 ✓ · 见 `IMPLEMENTED`）──────
COVERED_ENTRIES = {"16.4": "AUD-1", "16.9": "AUD-2"}

# ─── ★★ 分流时的【文档值】（★ 逐字 · 带时点 ⇒ 用于漂移对照）──────────────────────
DOC_TRIAGE = {
    "source": "team/2026-09-29-091422/AUDIT.md §2 + RESEARCH.md §1.3",
    "at": "分流时（RESEARCH 取证那一刻 · 报告未落更细时点）",
    "denominator_hash_hash_hash": 132,
    "jia": 41,
    "yi": 34,
    "bing": 57,
    "sum_check": 41 + 34 + 57,
    "implemented_claim": 2,
    "remaining_claim": 39,
    "remaining_claim_source": "team/HANDOFF-2026-09-29.md §2-C 的 C-2（逐字「41 条「可真判」判据只实现 2 条」）",
}

# ─── ★★ 已实现判据的【声明来源】（★ 逐字取自各判据 docstring ⇒ ★ 不得由我臆造映射）────
#
#   ★★ 关键区分：★ 只有 `AUD-1` / `AUD-2` 能**溯到 132 条清单里的条目** ✓ ——
#   ★ 其余 8 条的声明来源是 **`PLAN §2`**（★ 按 `T-AUDIT-GATE` 卡面「选 3~5 条与安全/数据相关的」✓）
#   ⇒ ★★ 它们**不在**那 132 条的索引里 ✗ ⇒ ★★ **不能**拿它们去冲抵「39」✗。
#
IMPLEMENTED = (
    (
        "AUD-1",
        "PLAN §2（水位锚定）",
        "§16.4 迁移水位断言的真实处数",
        "RESEARCH §3.1(c)「K-2 锚定形态的水位计数」判【可真判】",
    ),
    (
        "AUD-2",
        "PLAN §2（文档数字坐标）",
        "§16.9 时间戳不得编造 / O6",
        "RESEARCH §2.4 逐字「「文档数字带坐标」（§16.9 / O6）」判【可真判】",
    ),
    ("AUD-S1", "PLAN §2（migration.pg_pair）", None, "★ 安全/数据选项 ⇒ 不在 132 条索引内"),
    ("AUD-S2", "PLAN §2（audit_log.append_only）", None, "★ 安全/数据选项 ⇒ 不在 132 条索引内"),
    ("AUD-S3", "PLAN §2（secrets.write_encrypted）", None, "★ 安全/数据选项 ⇒ 不在 132 条索引内"),
    ("AUD-S4", "PLAN §2（perm.discussion_two_lines）", None, "★ 安全/数据选项 ⇒ 不在 132 条索引内"),
    (
        "AUD-S5",
        "PLAN §2（secrets.no_hardcoded_literal）",
        None,
        "★ 安全/数据选项 ⇒ 不在 132 条索引内",
    ),
    (
        "AUD-S6",
        "PLAN §2（结构化凭据列明文落库 · K 批八条硬要求）",
        None,
        "★ 安全/数据选项 ⇒ 不在 132 条索引内",
    ),
    ("AUD-G1", "PLAN §2/§8（未知格式守卫 · 注册表自检）", None, "★ 横切守卫 ⇒ 不在 132 条索引内"),
    ("AUD-G2", "PLAN §2（Makefile @207 依赖行冻结）", None, "★ 回归判据 ⇒ 不在 132 条索引内"),
)

SEC_RE = re.compile(r"^## (?:(\d+)\.\s*)?(.*)$")
ENTRY_RE = re.compile(r"^### (?:(\d+)\.(\d+)\b)?\s*(.*)$")


def classify(sec_num, entry_id):
    """★ 定一条条目的类（★ 点名 > 区间 > 节默认 > 未分流 ✓）。"""
    if sec_num is None:
        return UNTRIAGED
    if entry_id and entry_id in ENTRY_EXACT:
        return ENTRY_EXACT[entry_id]
    if entry_id:
        minor = int(entry_id.split(".")[1])
        for sec, lo, hi, cls in ENTRY_RANGES:
            if sec == sec_num and lo <= minor <= hi:
                return cls
    return SECTION_DEFAULT.get(sec_num, UNTRIAGED)


def parse(text):
    """★ 枚举节 / 条目（★ 带行号 ✓ · ★ 不用 `splitlines()`：见 `registry.read_lines` 的理由 ✓）。"""
    sections, entries = [], []
    sec_num, sec_title, sec_line = None, None, None
    for lineno, line in enumerate(text.split("\n"), 1):
        if line.startswith("## ") and not line.startswith("### "):
            m = SEC_RE.match(line)
            sec_num = int(m.group(1)) if m.group(1) else None
            sec_title, sec_line = m.group(2).strip(), lineno
            sections.append({"num": sec_num, "title": sec_title, "line": lineno})
        elif line.startswith("### "):
            m = ENTRY_RE.match(line)
            entry_id = f"{m.group(1)}.{m.group(2)}" if m.group(1) else None
            entries.append(
                {
                    "id": entry_id,
                    "section": sec_num,
                    "section_title": sec_title,
                    "section_line": sec_line,
                    "line": lineno,
                    "title": m.group(3).strip(),
                    "cls": classify(sec_num, entry_id),
                    "covered_by": COVERED_ENTRIES.get(entry_id or ""),
                    "cls_provenance": ENTRY_EXACT_PROVENANCE.get(
                        entry_id or "", "RESEARCH §1.3 节默认"
                    ),
                }
            )
    return sections, entries


def head_commit():
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short=8", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def tally(entries):
    counts = {}
    for e in entries:
        counts[e["cls"]] = counts.get(e["cls"], 0) + 1
    return counts


def build(text):
    sections, entries = parse(text)
    counts = tally(entries)
    triaged = [e for e in entries if e["cls"] != UNTRIAGED]
    return {
        "at": head_commit(),
        "learnings": LEARNINGS_REL,
        "total_entries": len(entries),
        "total_sections": len(sections),
        "counts": counts,
        "triaged_total": len(triaged),
        "sections": sections,
        "entries": entries,
    }


# ─── 自检（★ 阳性对照 —— ★ 计数类读数【必须】带它，否则「扫描器坏了 ⇒ 命中 0」会被读成绿 ✓）──
def self_test(text):
    """★ 三组对照：① 与独立 `grep` 口径一致 · ② 注入必须被看见 · ③ 注入进【未分流】节不得进甲。"""
    checks = []

    base = build(text)
    # ① 与独立口径（grep -cE "^### "）比对 ⇒ 抓「解析器坏了」
    try:
        out = subprocess.run(
            ["grep", "-cE", "^### ", str(REPO / LEARNINGS_REL)],
            capture_output=True,
            text=True,
            check=False,
        )
        grep_n = int(out.stdout.strip())
    except (OSError, ValueError):
        grep_n = None
    checks.append(
        (
            "与独立 grep 口径一致",
            grep_n is not None and grep_n == base["total_entries"],
            f"解析器 {base['total_entries']} vs grep {grep_n}",
        )
    )

    # ② ★ 阳性对照：往【甲】节注入一条 ⇒ 甲 必须 +1（★ 不动 ⇒ 说明扫描器瞎了 ⇒ 红）
    inj_jia = text.replace("### 1.2 ", "### 1.99 ★ 注入的阳性对照条目\n\n占位\n\n### 1.2 ", 1)
    got = build(inj_jia)
    ok = got["counts"].get(JIA, 0) == base["counts"].get(JIA, 0) + 1
    checks.append(
        (
            "注入甲节条目 ⇒ 甲 +1",
            ok,
            f"甲 {base['counts'].get(JIA, 0)} ⇒ {got['counts'].get(JIA, 0)}",
        )
    )

    # ③ ★ 判别性：注入【未分流】节的条目 ⇒ 只能进「未分流」✗ 不得进甲
    inj_new = text + "\n\n## 999. 注入的未分流节\n\n### 999.1 注入条目\n\n占位\n"
    got3 = build(inj_new)
    ok3 = got3["counts"].get(UNTRIAGED, 0) == base["counts"].get(UNTRIAGED, 0) + 1 and got3[
        "counts"
    ].get(JIA, 0) == base["counts"].get(JIA, 0)
    checks.append(
        (
            "注入未分流节 ⇒ 只进未分流·甲不变",
            ok3,
            f"未分流 {base['counts'].get(UNTRIAGED, 0)} ⇒ {got3['counts'].get(UNTRIAGED, 0)}"
            f" · 甲 {base['counts'].get(JIA, 0)} ⇒ {got3['counts'].get(JIA, 0)}",
        )
    )

    # ④ 冻结 id 集合必须与 registry 一致（★ 防「实现了没登记 / 登记了没实现」漂移）
    sys.path.insert(0, str(REPO))
    from scripts.audit import registry as reg

    ok4 = {cid for cid, _, _, _ in IMPLEMENTED} == set(reg.FROZEN_IDS)
    checks.append(
        (
            "IMPLEMENTED 与 registry.FROZEN_IDS 一致",
            ok4,
            f"差集 = {sorted({c for c, _, _, _ in IMPLEMENTED} ^ set(reg.FROZEN_IDS)) or '空'}",
        )
    )

    print("★ 自检（阳性对照 · 判别性对照）")
    for name, ok_, detail in checks:
        print(f"  [{'PASS' if ok_ else 'FAIL'}] {name} · {detail}")
    failed = [n for n, ok_, _ in checks if not ok_]
    print(f"★ 汇总：{len(checks) - len(failed)}/{len(checks)} PASS")
    return 0 if not failed else 3


def render(data):
    out = []
    add = out.append
    major, minor, micro = sys.version_info[:3]
    add(
        f"★ 分流清单枚举（只读 · 不返红 · 解释器=Python {major}.{minor}.{micro}）· at = {data['at']}"
    )
    add("")
    add(f"── ① 当前树实测（{data['learnings']}）─────────────────────────")
    add(f"  节 = {data['total_sections']} · `### ` 条目 = {data['total_entries']}")
    for cls in (JIA, MIXED, UNSPEC, BING, YI, UNTRIAGED):
        n = data["counts"].get(cls, 0)
        if n:
            add(f"  {cls:<14} = {n:3d}")
    add("")
    add(f"── ② 文档值（{DOC_TRIAGE['source']}）────────────")
    add(
        f"  甲 {DOC_TRIAGE['jia']} · 乙 {DOC_TRIAGE['yi']} · 丙 {DOC_TRIAGE['bing']}"
        f" ⇒ 合计 {DOC_TRIAGE['sum_check']} = 分母 {DOC_TRIAGE['denominator_hash_hash_hash']} ✓"
        "（★ 当时【确实】逐条分了类）"
    )
    add(
        f"  已实现 = {DOC_TRIAGE['implemented_claim']} · 其余 = {DOC_TRIAGE['remaining_claim']}"
        f"（来源：{DOC_TRIAGE['remaining_claim_source']}）"
    )
    add("")
    add("── ③ 差异与【应改成什么】─────────────────────────")
    drift = data["total_entries"] - DOC_TRIAGE["denominator_hash_hash_hash"]
    add(
        f"  ★ 分母漂移：分流时 {DOC_TRIAGE['denominator_hash_hash_hash']} ⇒ 当前 {data['total_entries']}"
        f"（{drift:+d}）⇒ ★ 两个数都对 · 差在【时点】"
    )
    add(
        f"  ★★ 未分流 = {data['counts'].get(UNTRIAGED, 0)} 条"
        "（★ 分流【之后】新增的节 ⇒ 从未被分过类）"
    )
    add(
        "  ★★★ 「甲 41」【不可还原为 41 条名单】✗ —— ★ 根因：来源表最大一格逐字 = 「乙为主（部分甲）」"
    )
    add(
        f"      ⇒ ★ 本工具只能给出【确定甲】= {data['counts'].get(JIA, 0)} 条 · "
        f"而 {data['counts'].get(MIXED, 0)} 条卡在「混合·未解析」· "
        f"{data['counts'].get(UNSPEC, 0)} 条「分项节内·未点名」"
    )
    add("      ⇒ ★★ 即：★ 41 - 2 = 39 这个减法【的两个数都不可复算】✗（★ 分母也已漂移）")
    add(
        "  ★ 应改成什么 = ★★ **在当前树上【重新分流】并把【逐条名单落盘】** ✓（★ 那是口径决定 ⇒ 须用户拍板 ✗）"
    )
    add("")
    add("── ③b ★★ 【可复算】的那个余数（★ 本工具唯一敢给的数 ✓）────────")
    jia_entries = [e for e in data["entries"] if e["cls"] == JIA]
    covered = [e for e in jia_entries if e["covered_by"]]
    covered_note = " · ".join(f"§{e['id']}→{e['covered_by']}" for e in covered) or "无"
    add(
        f"  ★ 确定甲 = {len(jia_entries)} 条 · 其中【已被判据覆盖】= {len(covered)} 条（{covered_note}）"
        f"⇒ ★★ **未覆盖的确定甲 = {len(jia_entries) - len(covered)} 条** ✓"
    )
    add(
        "  ★★ 口径（逐字 · 可跑）："
        f"`./.venv/bin/python scripts/audit/enumerate_criteria.py --cls '{JIA}'`"
    )
    add(
        f"  ★★ 注意：★ 这 {len(jia_entries) - len(covered)} ≠ 「39」✗"
        " —— ★ 它是【另一个口径】的数（★ 只含能逐条溯源的甲 ✓）"
    )
    add("")
    add("── ④ 已实现判据的【声明来源】（★ 逐字 · 不臆造映射）───────")
    traceable = [c for c in IMPLEMENTED if c[2]]
    for cid, src, learn, note in IMPLEMENTED:
        add(f"  {cid:<8} ← {src}")
        head = "溯到 LEARNINGS：" + learn if learn else "★ 无 LEARNINGS 条目映射："
        add(f"           {head} {note}")
    add("")
    add(
        f"  ★★ 可溯到 132 条清单的 = {len(traceable)} 条（{' · '.join(c[0] for c in traceable)}）"
        "⇒ ★ 与文档「已实现 2 条」一致 ✓"
    )
    add(
        f"  ★★ 其余 {len(IMPLEMENTED) - len(traceable)} 条 = 【安全/数据选项 + 横切守卫】"
        "⇒ ★★ **不在那 132 条的索引里** ✗"
    )
    add(
        "     ⇒ ★★★ **不得**拿它们去冲抵「39」✗（★ 那会把【另一条线的成果】记成【本线的进度】= 真空满足）"
    )
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="分流清单枚举器（只读 · 不返红）")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--self-test", action="store_true", help="★ 阳性对照 + 判别性对照（必跑）")
    ap.add_argument("--cls", help="只列某一类的条目（如 甲·可真判 / 混合·未解析 / 未分流）")
    args = ap.parse_args(argv)

    path = REPO / LEARNINGS_REL
    if not path.is_file():
        print(f"★ 读不到 {LEARNINGS_REL} ⇒ 不得记绿", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")

    if args.self_test:
        return self_test(text)

    data = build(text)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(render(data))
    if args.cls:
        print(f"\n── 逐条（{args.cls}）─────────────────────────")
        for e in data["entries"]:
            if e["cls"] == args.cls:
                print(f"  {LEARNINGS_REL}:{e['line']}  §{e['id'] or '?'}  {e['title'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
