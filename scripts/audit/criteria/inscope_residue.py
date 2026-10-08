#!/usr/bin/env python3
"""★ `AUD-C12`（`P1-17`：迁移 + 注册 + 接线）：`inScope` 残留 + 覆盖完整性预检（★★ 阻断级）。

★ 来历（承 `P1-7`，逐字可复用）：`P1-7` 把 `team/2026-09-29-075707/tools/inscope-residue-check.py`
  修好（① 去掉硬编码 `parent.parent` ⇒ 显式 `--run-dir`/`--root` 且 **打印基准目录**（`R8`）；
  ② 新增 ⑤ **`inScope` 覆盖完整性**；③ 改为 **阻断**：`0/1/2/3` 四码）—— ★ 但该文件在 `team/`
  下 **不受版本控制**（`.git/info/exclude` 整目录排除），且 **无任何 `make`/CI 目标调用它**
  ⇒ ★ **没有任何地方会因它非 0 而失败**。

★ 迁移三件套（★ 缺一即 `failed`）：
  ① **迁到受管位置** = 本模块（受 `git ls-files` / 评审 / CI 覆盖；★ 原文件**不删**，只读复制）；
  ② **注册进** `registry.FROZEN_IDS`（+ `HARD_IDS`）—— ★ 只加模块不注册 = **静默不被检查（假绿）**，
     比报错更糟（`registry.registry_guard_facts` 有 id 集合硬断言）；
  ③ **接进 `make audit`** —— ★ 注册即接线：`current_tree.py` 按 `FROZEN_IDS` 跑本判据，其
     `FAIL`/`UNKNOWN` 直接决定入口退出码 ⇒ `make audit` 非 0；★ 另有独立目标 `make audit-inscope`
     供**显式传参**单独复核（★ **绝不动** `all:` 那一行：它被 `AUD-G2` 逐字冻结）。

★★ 五类判据（① ② ④ 为「卡面与实际不符」）：
  ① **层级错配**：卡是【前端】（`npx vitest` / `dashboard/` / `tsc -b`）而 `inScope` **声明的路径
     【全部】**属【后端】⇒ 红 —— ★★ **承 lead 裁定**（`P1-21`）：同时声明两层路径的**跨层卡不报**
     （★ 旧口径「只要含一个后端路径就红」对双域卡**双向**报红 ⇒ 实测 11 条假阳性；★ 真阳性仍在）
  ② **反向错配**：卡是【后端】（`uv run pytest` / `tests/unit` / `tests/integration`）而 `inScope`
     **声明的路径【全部】**属【前端】⇒ 红（★ 同上：跨层卡不报）
  ③ **路径存在性**：`inScope` 里【不存在的路径】⇒ 红 —— ★★ **承 lead 裁定**（`P1-17.notes`）：
     卡的 `status` ∈ {`pending`, `in_progress`} ⇒ 其 `inScope` 指向的路径**允许尚不存在**（计划新建，
     不判 ③ 命中）；`status == completed` ⇒ 路径仍不存在 ⇒ **红**（该卡声称改了却查无此文件）。
     ★ 依据：`P1-7` 实测本 run 有大量「计划新建」路径（含 `P1-2…P1-5` 的模块目录）⇒ 不按此口径
     接线，门禁一接上就**长期红**。
     ★ 其它 / 缺失 `status` ⇒ **`UNKNOWN`**（fail-closed：★ 既不得记绿，也不误判红）。
     ★★ **③ 对目录【不放宽】**（承 `P1-21` 裁定）：目录型条目仍须**真实存在** ⇒ 不得因为它是目录
        就跳过存在性检查（⑤ 的目录子树口径**只在 ⑤ 生效**）。
  ④ **载体错位**：同一脚本在 runDir 与仓库两份 ⇒ 必须登记【仓库那份】
  ⑤ **覆盖完整性**：本批【实际改动的产品文件】必须出现在某卡的 `inScope` / `referencedFiles` 内
     ★★ **承 lead 裁定**（`P1-21`）：**目录型条目视为覆盖其子树** —— `inScope` 条目**以 `/` 结尾
     或实际是目录** ⇒ 其子树内的改动文件视为被覆盖（★ 旧口径逐字精确匹配 ⇒ 实测 2 条假阳性；
     ★ 本 run 共 37 处目录型 `inScope`）。★ 未在任何卡 `inScope`（文件或目录子树）里声明的改动
     文件**仍报红**（真阳性仍在）。

★★ 退出码（CLI 模式 · 与 `current_tree.py` 同构）：
   `0` 干净 ✓ · `1` 发现越界 ✗ · `2` **无法判定**（fail-closed ⇒ ★ **不得输出绿**）· `3` 用法/参数错误
   ★ 判据模式把 `1`/`2` 映射成 `FAIL`/`UNKNOWN` ⇒ `make audit` 退出码**因此非 0** ⇒ ★ **阻断生效**。

★★ 输入（`R8`：调用方**必须显式传**；★ 缺一律 `UNKNOWN`，**绝不绿**）：
   判据模式（`current_tree.py` 调 `check_inscope_residue`）：env `OCTOP_AUDIT_RUN_DIR` +
   `OCTOP_AUDIT_CHANGED_LIST` / `OCTOP_AUDIT_GIT_RANGE`（★ 常量单一出处 = `registry.py`）；
   CLI 模式：`--run-dir`（必需）· `--root`（缺省 = `git rev-parse --show-toplevel`，再退 cwd）·
   `--changed-file`（可重复）/ `--changed-list` / `--git-range` **三选一**。
   ★★ 三态里的**每一个**都在输出里打印**实际探测的基准目录**（`R8`）。

★★ 防御性形态（`P1-7` 实测：本 run 的 `P2-1…P2-9` 的 `inScope` 曾是**字符串被拆成单字符列表**）：
   「list 且**每元素长度 1**」⇒ **`UNKNOWN`**（fail-closed · ★ 不输出绿）。★ 迁移版把阈值从 `P1-7`
   的「≥2 个」收窄到「≥1 个」：★ 仓库相对路径不可能只有 1 个字符 ⇒ ★ 仍在「不得记绿」一侧，
   只是不再漏判 `["a"]` 这一形态（★ 阈值与理由见 `task_scope`）。

用法::

    ./.venv/bin/python scripts/audit/criteria/inscope_residue.py \\
        --run-dir team/2026-10-08-110000 --git-range HEAD~1..HEAD
    make audit-inscope                     # ★ 独立目标（★ 解释器 = ./.venv/bin/python）

★ 解释器一律 `./.venv/bin/python`（3.12.14）；★ 裸 `python3` = 3.9.6 ⇒ **假绿**。
★ 本模块**只读**被审计文件 · **不落盘**（承 `PLAN §8` / `AC-5`）。
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

sys.dont_write_bytecode = True  # ★ 只读：禁写 __pycache__（AC-5）

# ★ 双入口（同 `current_tree.py` 的写法）：直接跑脚本（`make audit-inscope`）/ 作为包成员被导入
if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
    from scripts.audit import registry as reg
else:
    from .. import registry as reg  # ★ 判据 id / env 常量 / 三态 的单一出处

CRITERION_ID = "AUD-C12"

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_UNKNOWN = 2
EXIT_USAGE = 3

STATE_TO_EXIT = {reg.OK: EXIT_OK, reg.FAIL: EXIT_VIOLATION, reg.UNKNOWN: EXIT_UNKNOWN}

# ─── ★ 词表 / 判定域（★ 与 `P1-7` 逐字一致 ⇒ 迁移不改判据口径）────────────────────
FE_MARK = ("npx vitest", "dashboard/", "tsc -b")
BE_MARK = ("uv run pytest", "tests/unit", "tests/integration")
FE_PATH = ("dashboard/",)
BE_PATH = ("src/octop/", "tests/")
# ★ 非产品文件：run 证据 / 工作树 / 依赖 / git 内部 ⇒ ★ 不纳入 ⑤ 覆盖判定（与 `R3`「产品文件」口径一致）
NON_PRODUCT = ("team/", ".wt/", ".git/", "dashboard/node_modules/")


class Usage(Exception):
    """★ 用法/参数错误 ⇒ `exit 3`。"""


class CannotJudge(Exception):
    """★ 无法判定 ⇒ `exit 2`（fail-closed）。"""


def norm(p):
    p = str(p).strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def task_scope(card):
    """★ 取一张卡的 `inScope` ∪ `referencedFiles`。

    ⇒ 返回 `(paths, unknown_reason)`：
       · `paths` = 可判定的路径集合（`str` 列表按原样）
       · `unknown_reason` 非 `None` ⇒ ★ 该卡形态【判不了】⇒ ★ **不得**当作「什么都没覆盖」✗
    """
    paths = []
    for key in ("inScope", "referencedFiles"):
        if key not in card:
            if key == "inScope":
                return [], "缺 `inScope` 键"
            continue
        value = card[key]
        if not isinstance(value, list):
            return [], f"`{key}` 不是列表（是 {type(value).__name__}）"
        for element in value:
            if not isinstance(element, str):
                return [], f"`{key}` 含非字符串元素（{type(element).__name__}）"
        strings = [norm(element) for element in value]
        # ★★ 字符拆分裂形态：JSON 字符串被当成 list ⇒ 迭代出单个字符
        #   （★ 实读 run `2026-10-08-110000` 的 `P2-1…P2-9` 即此形态）✗
        #   ★ `P1-7` 的阈值是「≥2 个」；★ 迁移版收窄到「≥1 个」—— 仓库相对路径不可能只有 1 个字符，
        #     故这一收紧**只会**把「更细的畸形」也判成 UNKNOWN（仍在「不得记绿」一侧）✓
        if strings and all(len(s) == 1 for s in strings):
            return [], (f"`{key}` 疑似【字符串被拆成字符列表】（{len(strings)} 个单字符元素，"
                        f"如 {strings[:4]!r}）✗")
        paths.extend(strings)
    return [p for p in paths if p], None


def missing_verdict(card):
    """★ ③ 的**状态相关**口径（★ 承 lead 裁定 · 单一出处 = `registry` 的两个元组）。

    ⇒ `"allowed"`（`pending`/`in_progress`：计划新建，允许尚不存在）·
      `"hit"`（`completed`：声称改了却查无此文件 ⇒ 红）·
      `"unknown"`（其它/缺失 `status` ⇒ 判不了 ⇒ fail-closed，★ 不记绿也不误判红）
    """
    status = card.get("status")
    if isinstance(status, str) and status in reg.INSCOPE_MISSING_ALLOWED_STATUS:
        return "allowed"
    if status == reg.INSCOPE_COMPLETED_STATUS:
        return "hit"
    return "unknown"


class Report:
    """★ 一次判定的完整读数（★ CLI 与判据模式**共用** ⇒ 语义单一 · 不可能两处不一致）。"""

    def __init__(self, state, run_dir, root, tasks_file, changed_src, changed_n,
                 product_n, run_id, cards, judged, planned, hits, unknowns):
        self.state = state
        self.run_dir = run_dir
        self.root = root
        self.tasks_file = tasks_file
        self.changed_src = changed_src
        self.changed_n = changed_n
        self.product_n = product_n
        self.run_id = run_id
        self.cards = cards
        self.judged = judged
        self.planned = planned
        self.hits = list(hits)
        self.unknowns = list(unknowns)

    def base_dirs(self):
        """★ `R8`：三态里的每一个都必须打印**实际探测的基准目录**。"""
        return (f"★ 基准目录（实际探测 · 承 R8）：RUN_DIR = {self.run_dir} · ROOT = {self.root} · "
                f"TASKS_FILE = {self.tasks_file}")


def evaluate(run_dir, root, changed, changed_src):
    """★ 判据核心（★ 只读 · 纯函数式 ⇒ CLI 与判据模式共用同一语义）。"""
    tasks_file = run_dir / reg.INSCOPE_TASKS_NAME

    def report(state, run_id=None, cards=0, judged=0, planned=0, hits=(), unknowns=(), product_n=0):
        return Report(state, run_dir, root, tasks_file, changed_src, len(changed), product_n,
                      run_id, cards, judged, planned, hits, unknowns)

    if not changed:
        return report(reg.UNKNOWN, unknowns=[
            f"改动清单【为空】（来源 = {changed_src}）⇒ ★ 无法判定覆盖完整性 ⇒ fail-closed "
            "⇒ ★ 不得输出绿"])

    try:
        tasks, run_id = load_tasks(tasks_file)
    except CannotJudge as exc:
        return report(reg.UNKNOWN, unknowns=[f"{exc} ⇒ ★ 无法判定 ⇒ fail-closed ⇒ 不得输出绿"])

    hits, unknowns = [], []
    scopes, planned = {}, 0

    # ── ①②③④ 卡面残留（★ 形态判不了的卡只记 UNKNOWN ⇒ ★ 不误报红 ✗）─────────────
    for card in tasks:
        tid = card["id"]
        scope, why = task_scope(card)
        if why is not None:
            unknowns.append(f"卡 {tid}：{why} ⇒ ★ 形态判不了 ⇒ fail-closed（不得记绿，也不误判红）")
            continue
        scopes[tid] = scope
        text = str(card.get("acceptance", "")) + " " + str(card.get("verify", ""))
        is_fe = any(key in text for key in FE_MARK)
        is_be = any(key in text for key in BE_MARK)
        # ★★ ①② 的「双域卡」口径（★ 承 lead 裁定 · `P1-21`）：**只在该卡声明的路径【全部】属
        #   另一层时才报红**；同时声明两层路径的**跨层卡不报**。
        #   ★ 依据（实测）：`P0-5` / `P1-6` / `P1-16` / `P3-2` / `P3-3` / `P3-7` 的 `acceptance`/
        #     `verify` **同时**含前端与后端标记（`P1-7` 冻结的词表）⇒ 旧口径对同一张卡**双向**报红
        #     （实测 11 条假阳性）；这些卡本就**跨层**（后端测试 + 前端产物同属一张卡）。
        #   ★ 真阳性**仍在**：卡只声明另一层的路径（本层路径 0 条）⇒ ①/② 照旧报红。
        product_scope = [item for item in scope if not item.startswith("team/")]
        declares_fe = any(item.startswith(FE_PATH) for item in product_scope)
        declares_be = any(item.startswith(BE_PATH) for item in product_scope)
        for path in product_scope:
            if is_fe and not declares_fe and path.startswith(BE_PATH):
                hits.append((tid, path, "① 卡是【前端】而 `inScope` 路径**全部**属【后端】✗"))
            if is_be and not declares_be and path.startswith(FE_PATH):
                hits.append((tid, path, "② 卡是【后端】而 `inScope` 路径**全部**属【前端】✗"))
        for path in scope:
            if path.startswith("team/") or (root / path).exists():
                continue
            verdict = missing_verdict(card)
            if verdict == "allowed":
                planned += 1
            elif verdict == "hit":
                hits.append((tid, path, f"③ 路径【不存在】而卡 `status={card.get('status')}`"
                                        "（= 已完成 ⇒ ★ 不允许「计划中」）⇒ ★ 疑似【路径笔误 / 卡面与实际不符】✗"))
            else:
                unknowns.append(f"卡 {tid}：`status` = {card.get('status')!r} 不在已知集合内 ⇒ ★ ③ 无法"
                                "判定该不存在的路径是否被允许 ⇒ fail-closed")
        run_tools = [path for path in scope if path.startswith("team/") and "/tools/" in path]
        for tool in run_tools:
            name = tool.rsplit("/", 1)[-1]
            repo_copy = f"dashboard/scripts/{name}"
            if (root / repo_copy).exists() and repo_copy not in scope:
                hits.append((tid, tool, f"④ 载体错位：★ 仓库已有 `{repo_copy}` ⇒ ★ 必须登记【仓库路径】✗"))

    # ── ⑤ `inScope` 覆盖完整性（★ 本批改动的产品文件必须被某卡覆盖）───────────────
    covered = set()
    for scope in scopes.values():
        covered.update(scope)
    # ★★ ⑤ 的「目录项视为其子树覆盖」（★ 承 lead 裁定 · `P1-21`）：`inScope` 条目**以 `/` 结尾
    #   或实际是目录** ⇒ 其**子树内的文件**视为被该卡覆盖。★ 依据（实测）：本 run 共 37 处目录型
    #   `inScope`（如 `scripts/audit/criteria/`），旧口径是**逐字精确匹配** ⇒ 目录声明不可能覆盖
    #   其子文件 ⇒ 实测 2 条⑤假阳性。
    #   ★★ ③ **不随之放宽**：③ 里目录仍须**真实存在**（下面的存在性检查一字未动）——
    #      「目录型条目」只在 ⑤ 的覆盖判定里生效，不得拿它跳过 ③。
    covered_dirs = tuple(prefix if prefix.endswith("/") else prefix + "/"
                         for prefix in sorted(covered)
                         if prefix.endswith("/") or (root / prefix).is_dir())
    unjudgeable = bool(unknowns)
    product_changed = [item for item in changed if not item.startswith(NON_PRODUCT)]
    for path in product_changed:
        if path in covered or path.startswith(covered_dirs):
            continue
        line = (f"⑤ 覆盖缺失：本批改动的产品文件 `{path}` 不在 run `{run_id}` 的任何卡的 "
                f"`inScope`/`referencedFiles` 里（★ 含目录型条目的子树 · 本 run 共 {len(tasks)} 张卡）")
        if unjudgeable:
            unknowns.append(line + " ⇒ ★ 但有卡的 `inScope` 形态判不了 ⇒ fail-closed ⇒ 判 UNKNOWN ✗")
        else:
            hits.append(("(本批)", path, line + " ✗"))

    state = reg.UNKNOWN if unknowns else (reg.FAIL if hits else reg.OK)
    return report(state, run_id=run_id, cards=len(tasks), judged=len(scopes), planned=planned,
                  hits=hits, unknowns=unknowns, product_n=len(product_changed))


def load_changed(changed_files, changed_list, git_range, root):
    """★ 取本批改动清单（★ 三个来源互斥 · 由调用方保证）⇒ `(changed, source_text)`。"""
    if changed_files:
        return sorted({norm(p) for p in changed_files if norm(p)}), "--changed-file（逐条显式）"
    if changed_list:
        list_file = pathlib.Path(changed_list).expanduser()
        if not list_file.is_file():
            raise Usage(f"`--changed-list` 指向的文件不存在：{list_file}")
        # ★ 按 `\n` 切行（与 `registry.read_lines` 同口径；不用 `splitlines()`）
        lines = [norm(item) for item in list_file.read_text(encoding="utf-8").split("\n")]
        return sorted({item for item in lines if item and not item.startswith("#")}), \
            f"--changed-list {list_file}"
    if git_range:
        cmd = ["git", "-C", str(root), "diff", "--name-only", str(git_range)]
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode != 0:
            raise Usage(f"`--git-range {git_range}` 无法解析：{' '.join(cmd)} ⇒ "
                        f"{out.stderr.strip() or out.stdout.strip()}")
        return sorted({norm(item) for item in out.stdout.split("\n") if norm(item)}), \
            f"--git-range {git_range}（在 {root} 内 `git diff --name-only`）"
    raise Usage("★ 必须给出改动清单来源：`--changed-file` / `--changed-list` / `--git-range` 三者之一"
                "（R8：不得依赖默认值）")


def load_tasks(tasks_file):
    """★ 读 `TASKS.json`；★ 缺失 / 坏 / 结构不认识 ⇒ `CannotJudge`（⇒ `exit 2`）。"""
    if not tasks_file.is_file():
        raise CannotJudge(f"TASKS.json 不存在：{tasks_file}")
    try:
        doc = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise CannotJudge(f"TASKS.json 不可解析：{exc!r}") from exc
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list):
        raise CannotJudge(f"TASKS.json 顶层结构不认识：需 `{{'tasks': [...]}}`，实读 "
                          f"{type(doc).__name__}/tasks={type(tasks).__name__}")
    out = []
    for card in tasks:
        if not isinstance(card, dict) or not isinstance(card.get("id"), str):
            raise CannotJudge(f"任务表元素不认识（需含字符串 `id`）：{type(card).__name__} "
                              f"{str(card)[:80]!r}")
        out.append(card)
    if not out:
        raise CannotJudge("任务表为空（tasks = []）⇒ ★ 无法判定覆盖完整性")
    # ★ 结构层：★ 若【无任何卡】带 list 型 `inScope`/`referencedFiles` ⇒ 结构不认识 ⇒ 不得输出绿 ✗
    if not any(isinstance(card.get(key), list) for card in out for key in ("inScope", "referencedFiles")):
        raise CannotJudge("无任何卡带 list 型 `inScope`/`referencedFiles` ⇒ 结构不认识")
    run_id = doc.get("runId")
    if not isinstance(run_id, str) or not run_id.strip():
        run_id = tasks_file.parent.name
    return out, run_id


# ─── ★ 判据形态（`current_tree.py` 导入即注册）──────────────────────────────────

@reg.criterion(CRITERION_ID, reg.HARD)
def check_inscope_residue(ctx):
    """★ 三态：干净 ⇒ `OK` · 命中 ⇒ `FAIL` · 输入缺失/形态判不了 ⇒ `UNKNOWN`（★ 绝不绿）。

    ★ 输入一律**显式**（env · 承 `R8`）⇒ ★ 缺 ⇒ `UNKNOWN`：★ 判据**不得**替调用方猜 run 目录
      （旧版硬编码 `parent.parent` 正是 `P1-7` 修掉的缺陷 ✗）。
    """
    root = pathlib.Path(ctx.repo).resolve()
    run_raw = os.environ.get(reg.INSCOPE_RUN_DIR_ENV, "").strip()
    list_raw = os.environ.get(reg.INSCOPE_CHANGED_LIST_ENV, "").strip()
    range_raw = os.environ.get(reg.INSCOPE_GIT_RANGE_ENV, "").strip()
    run_dir = pathlib.Path(run_raw).expanduser().resolve() if run_raw else None
    # ★ 输入面**永不空**（承 `AUD-C11`）：★ 读不到也如实登记为输入 ⇒ 不被 `current_tree.py:48`
    #   的空输入守卫改成 `FAIL`，而是本判据自己判 `UNKNOWN`（★ 两态都**不是绿** ⇒ 无假绿通道）
    files = [str(run_dir / reg.INSCOPE_TASKS_NAME) if run_dir
             else f"<未绑定 {reg.INSCOPE_RUN_DIR_ENV}>/{reg.INSCOPE_TASKS_NAME}"]
    if list_raw:
        files.append(str(pathlib.Path(list_raw).expanduser()))

    missing = []
    if run_dir is None:
        missing.append(f"未显式传 run 目录（env `{reg.INSCOPE_RUN_DIR_ENV}`）")
    if not list_raw and not range_raw:
        missing.append(f"未显式传改动清单来源（env `{reg.INSCOPE_CHANGED_LIST_ENV}` / "
                       f"`{reg.INSCOPE_GIT_RANGE_ENV}`）")
    if missing:
        return reg.Result(
            CRITERION_ID, reg.HARD, reg.UNKNOWN,
            message=f"实测量：{' · '.join(missing)} ⇒ ★ 输入未绑定 ⇒ 判不了（承 R8：调用方必须显式传）"
                    f"⇒ 未知 ≠ 绿 ⇒ 不得记绿；应改成：`{reg.INSCOPE_RUN_DIR_ENV}=<run 目录> "
                    f"{reg.INSCOPE_CHANGED_LIST_ENV}=<改动清单> ./.venv/bin/python scripts/audit/"
                    "current_tree.py`（或用 `make audit-inscope`）· "
                    f"★ 基准目录（实际探测 · 承 R8）：RUN_DIR = {run_dir or '（未绑定）'} · ROOT = {root}",
            files=files,
        )

    try:
        changed, changed_src = load_changed(None, list_raw or None, range_raw or None, root)
    except Usage as exc:
        return reg.Result(
            CRITERION_ID, reg.HARD, reg.UNKNOWN,
            message=f"实测量：改动清单来源不可用（{exc}）⇒ ★ 无法判定 ⇒ fail-closed ⇒ 不得记绿 · "
                    f"★ 基准目录（实际探测 · 承 R8）：RUN_DIR = {run_dir} · ROOT = {root}",
            files=files,
        )

    report = evaluate(run_dir, root, changed, changed_src)
    claim = ("文档声称：卡的 `inScope` 路径与其层级/域一致 ∧ 不存在的路径要么是**计划新建**"
             f"（`status` ∈ {{{', '.join(reg.INSCOPE_MISSING_ALLOWED_STATUS)}}} · 承 lead 裁定）、"
             f"要么已落盘（`status == `{reg.INSCOPE_COMPLETED_STATUS}`）∧ 本批改动的产品文件被某卡的 "
             "`inScope`/`referencedFiles` 覆盖（`R3` 两条硬规则之一）· ★ 承 `P1-21` 裁定：①② 只对"
             "**单层卡**（声明路径全部属另一层）报红 · ⑤ 里**目录型条目视为覆盖其子树**")
    fix = ("应改成：修卡面（路径笔误 / 把描述串写成路径 / 跨树路径）或在 `completed` 卡声称的路径上"
           "真的落文件；★ 计划新建的卡保持 `status=pending`/`in_progress`；★ 不得用 `|| true` / "
           "放宽判据凑绿")
    head = (f"实测量：run `{report.run_id}` · 卡 {report.cards} 张（形态可判 {report.judged}）· "
            f"计划新建而暂缺的路径 {report.planned} 条（★ 承裁定 ⇒ 不判红）· 本批产品改动 "
            f"{report.product_n} 个文件（来源 {report.changed_src}）")
    hints = [f"{tid}: {path} ⇒ {why}" for tid, path, why in report.hits]
    hints += report.unknowns

    if report.state == reg.OK:
        return reg.Result(
            CRITERION_ID, reg.HARD, reg.OK, checked=report.judged + report.changed_n,
            message=f"{head}；①②③④ 命中 **0** · ⑤ 覆盖完整；{claim}；{fix} · {report.base_dirs()}",
            files=files, hints=hints,
        )
    if report.state == reg.FAIL:
        detail = " ; ".join(f"{tid}:{path} ⇒ {why}" for tid, path, why in report.hits[:5])
        tail = f"（★ 全量 {len(report.hits)} 条见提示）" if len(report.hits) > 5 else ""
        return reg.Result(
            CRITERION_ID, reg.HARD, reg.FAIL, checked=report.judged + report.changed_n,
            message=f"{head} ⇒ ★★ 命中 {len(report.hits)} 条（★★ 阻断 ✗）：{detail}{tail}；{claim}；"
                    f"{fix} · {report.base_dirs()}",
            files=files, hints=hints,
        )
    detail = " ; ".join(report.unknowns[:5])
    tail = f"（★ 全量 {len(report.unknowns)} 条见提示）" if len(report.unknowns) > 5 else ""
    return reg.Result(
        CRITERION_ID, reg.HARD, reg.UNKNOWN, checked=report.judged + report.changed_n,
        message=f"{head} ⇒ ★★ 无法判定 {len(report.unknowns)} 条（fail-closed ⇒ ★ 不得记绿）："
                f"{detail}{tail}；{claim}；{fix} · {report.base_dirs()}",
        files=files, hints=hints,
    )


# ─── ★ CLI 形态（独立目标 `make audit-inscope` · 与判据模式共用 `evaluate`）───────

def build_parser():
    ap = argparse.ArgumentParser(
        prog="scripts/audit/criteria/inscope_residue.py",
        description="★ D-9 / AUD-C12：inScope 残留 + 覆盖完整性预检（★ 阻断级 · 非 0 = 红）",
    )
    ap.add_argument(
        "--run-dir", default=None,
        help="★ 目标 run 目录（其下读 TASKS.json）。★ R8：调用方【必须显式传】——"
             "★ 迁移后本工具不再有「自身所在 run 目录」这种默认值。",
    )
    ap.add_argument(
        "--root", default=None,
        help="★ 仓库根（③ 路径存在性 / ④ 载体错位 / ⑤ 改动清单基准）。缺省 = "
             "`git rev-parse --show-toplevel`，再退 cwd（★ 会打印它是什么）。",
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--changed-file", action="append", default=None, metavar="PATH",
                     help="本批改动的文件（可重复）。")
    src.add_argument("--changed-list", default=None, metavar="FILE",
                     help="本批改动文件清单（一行一路径）。")
    src.add_argument("--git-range", default=None, metavar="BASE..HEAD",
                     help="用 `git diff --name-only BASE..HEAD` 取改动清单（在 --root 内执行）。")
    return ap


def resolve_root(raw):
    """★ `--root` 缺省链（★ `R8`：用了默认值就**打印它是什么**）⇒ `(root, source_text)`。"""
    if raw:
        return pathlib.Path(raw).expanduser().resolve(), "★ 显式传入 --root"
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=str(pathlib.Path.cwd()), capture_output=True, text=True, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return pathlib.Path(out.stdout.strip()).resolve(), "★ 默认值 = `git rev-parse --show-toplevel`"
    except OSError:
        pass
    return pathlib.Path.cwd().resolve(), "★★ 默认值 = cwd（R8：调用方应显式传 --root）"


def main(argv=None):
    args = build_parser().parse_args(argv)

    # ── ★ R8：基准目录必须显式声明并【打印】 ──────────────────────────────
    if args.run_dir and args.run_dir.strip():
        run_dir = pathlib.Path(args.run_dir).expanduser().resolve()
        run_dir_src = "★ 显式传入 --run-dir"
    else:
        print("  ✗ 用法错误：★ 必须显式传 `--run-dir`（承 R8：迁移后**不得**再依赖「本工具所在的 run "
              "目录」这种隐式默认值 ⇒ exit 3）")
        return EXIT_USAGE
    root, root_src = resolve_root(args.root)
    tasks_file = run_dir / reg.INSCOPE_TASKS_NAME
    registered = any(item["id"] == CRITERION_ID for item in reg.REGISTRY)

    print(f"★★ {CRITERION_ID}：inScope 残留 + 覆盖完整性预检（★★ 阻断级 · 非 0 退出）")
    print(f"  ★ 判据 id             = {CRITERION_ID}   [注册表 = "
          f"{'★ 已注册 ✓（registry.FROZEN_IDS ⇒ make audit 会跑它）' if registered else '★ 未注册 ⇒ 假绿 ✗'}]")
    print(f"  ★ 基准目录 RUN_DIR    = {run_dir}   [{run_dir_src}]")
    print(f"  ★ 基准目录 ROOT       = {root}   [{root_src}]")
    print(f"  ★ 任务表   TASKS_FILE = {tasks_file}")

    # ── ★ 改动清单（⑤ 的输入）⇒ 缺失 = 用法错误 3 ──────────────────────────
    try:
        changed, changed_src = load_changed(args.changed_file, args.changed_list, args.git_range, root)
    except Usage as exc:
        print(f"  ✗ 用法错误：{exc}")
        return EXIT_USAGE
    print(f"  ★ 改动来源 CHANGED    = {changed_src}")
    print(f"  ★ 改动文件数          = {len(changed)}")

    report = evaluate(run_dir, root, changed, changed_src)
    print(f"  ★ run id              = {report.run_id if report.run_id else '（未读到 · 见下）'}"
          "   [来源 = TASKS.json `runId`，缺省退到目录名]")
    print(f"  ★ 卡数                = {report.cards}   [★ 形态可判 = {report.judged}]")
    print(f"  ★ 计划新建而暂缺的路径 = {report.planned} 条（★ 承 lead 裁定：`pending`/`in_progress` "
          f"允许尚不存在 ⇒ 不判 ③ 命中）")
    print(f"  ★ 产品改动文件数（⑤ 判定域，已排除 {'/'.join(NON_PRODUCT)}）= {report.product_n}")
    if report.hits:
        print(f"  ✗✗ 发现越界 {len(report.hits)} 条：")
        for tid, path, why in report.hits:
            print(f"  · [{why}] {tid}: {path}")
    if report.unknowns:
        print(f"  ✗✗ 无法判定 {len(report.unknowns)} 条（fail-closed ⇒ ★ 不得输出绿）：")
        for item in report.unknowns:
            print(f"  · {item}")
    if report.state == reg.OK:
        print("  ★ 未发现层级错配 ✓ · ★ ⑤ 覆盖完整 ✓")
    print(f"  {report.base_dirs()}")

    if report.state == reg.UNKNOWN:
        print(f"★★ 结论：无法判定 ⇒ exit {EXIT_UNKNOWN}（fail-closed ✗）")
    elif report.state == reg.FAIL:
        print(f"★★ 结论：发现越界 ⇒ exit {EXIT_VIOLATION}（★★ 阻断 ✗）")
    else:
        print(f"★★ 结论：干净 ⇒ exit {EXIT_OK} ✓")
    return STATE_TO_EXIT[report.state]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
