"""★ `P1-2` 树纪律判据族（★ 同族合并在一个模块 ⇒ 不造第二份拷贝）：

  `AUD-C4` `skip_ledger`（序号 7）    · `AUD-C5` `doc_paths`（序号 8）
  `AUD-C6` `symbol_home`（序号 9）    · `AUD-C7` `dead_tables`（序号 10）
  `AUD-C8` `dispatch_write`（序号 11）· `AUD-C9` `dashboard_stale`（序号 15）

★ 全部**离线可用**（只读仓库内文件 + 只读 `git`）· ★ 全部含**存在性子句**（空集 ⇒ `UNKNOWN`，
  ★ 不记绿 —— 承 `R14`）· ★ 全部输出**三元组**（实测量 / 文档声称 / 应改成什么 —— 承红线 1）。
★ 常量一律引用 `registry.py`（★ 禁判据内另写 `020` / 表名白名单 / 词表 —— 承红线 3）。
"""

import ast
import pathlib
import re

from .. import registry as reg

# ─── `AUD-C4` skip 门控清单 ──────────────────────────────────────────────────
SKIP_DECORATOR_RE = re.compile(
    r"^\s*@(?:requires_postgresql\b|pytest\.mark\.skipif\b|pytest\.mark\.skip\b)"
)
SKIP_CALL_RE = re.compile(r"\bpytest\.skip\(")

# ─── `AUD-C5` 文档路径坐标 ──────────────────────────────────────────────────
DOC_SCAN_DIRS = ("docs", "team")
DOC_SUFFIXES = (".md",)
DOC_COORD_RE = re.compile(
    "`((?:src|tests|scripts|dashboard|docs)/[A-Za-z0-9_./-]+?\\.(?:py|ts|tsx|sql|json|md|sh|toml|yml|yaml|html|css|js)):(\\d+)`"
)
DOC_BARE_RE = re.compile(
    r"`((?:src|tests|scripts|dashboard|docs)/[A-Za-z0-9_./-]+?\.(?:py|ts|tsx|sql|json|md))`"
)

# ─── `AUD-C7` 迁移建表 ──────────────────────────────────────────────────────
CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
MIGRATION_NUM_RE = re.compile(r"^(\d+)")

# ─── `AUD-C8` 派单写点 ──────────────────────────────────────────────────────
UPDATE_CALL_ATTRS = ("update", "update_many", "update_one", "set")
SQL_UPDATE_RE = re.compile(rf"UPDATE\s+{reg.DISPATCH_TABLE}\b", re.I)


def _display(root, path):
    """★ 只输出【仓库相对路径】；★ 树外（`team/` 是符号链接）⇒ `None`（★ 不判 ⇒ 逐条登记）。"""
    try:
        return pathlib.Path(path).resolve().relative_to(pathlib.Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _py_files(root, subdir):
    base = pathlib.Path(root) / subdir
    if not base.is_dir():
        return []
    return sorted(path for path in base.rglob("*.py") if path.is_file())


# ═══ `AUD-C4` `skip_ledger`（序号 7）══════════════════════════════════════════


@reg.criterion("AUD-C4", reg.HARD)
def check_skip_ledger(ctx):
    """★ 逐项对账「已知门控清单」（`registry.SKIP_GATE_LEDGER` = **独立出处**）。
    ★ 空集（本轮 0 个门控）⇒ `UNKNOWN`（空跑，不计证据 —— 承 `R14`）。"""
    measured, files = {}, []
    for path in _py_files(ctx.repo, "tests"):
        relname = reg.rel(ctx.repo, path)
        try:
            lines = reg.read_lines(path)
        except (OSError, UnicodeDecodeError) as exc:
            return reg.Result(
                "AUD-C4",
                reg.HARD,
                reg.UNKNOWN,
                message=f"读不到 {relname}（{exc}）⇒ 不得记绿",
                files=files,
            )
        count = sum(1 for text in lines if SKIP_DECORATOR_RE.search(text)) + sum(
            1 for text in lines if SKIP_CALL_RE.search(text)
        )
        if count:
            measured[relname] = count
            files.append(relname)
    total = sum(measured.values())
    ledger_total = sum(reg.SKIP_GATE_LEDGER.values())
    if not measured:
        return reg.Result(
            "AUD-C4",
            reg.HARD,
            reg.UNKNOWN,
            message="tests/ 里 skip 门控 0 处 ⇒ 空跑，不计证据（★ 空集不得记绿）",
        )
    delta = sorted(
        name
        for name in set(measured) | set(reg.SKIP_GATE_LEDGER)
        if measured.get(name, 0) != reg.SKIP_GATE_LEDGER.get(name, 0)
    )
    if delta:
        detail = " · ".join(
            f"{name} 实测 {measured.get(name, 0)} ≠ 冻结 {reg.SKIP_GATE_LEDGER.get(name, 0)}"
            for name in delta
        )
        return reg.Result(
            "AUD-C4",
            reg.HARD,
            reg.FAIL,
            checked=total,
            message=f"实测量：skip 门控 {total} 处 / {len(measured)} 文件（冻结清单 = {ledger_total} 处 / "
            f"{len(reg.SKIP_GATE_LEDGER)} 文件）· 差集 {len(delta)} 个文件（{detail}）；文档声称："
            "「本机 skip 都是已知环境门控（PG / 平台）」；应改成：把新增门控逐条登记到 "
            "`registry.SKIP_GATE_LEDGER`（★ 禁从当次 pytest 输出抄）",
            files=files,
        )
    return reg.Result(
        "AUD-C4",
        reg.HARD,
        reg.OK,
        checked=total,
        message=f"实测量：skip 门控 {total} 处 / {len(measured)} 文件（= 冻结清单 {ledger_total} 处 / "
        f"{len(reg.SKIP_GATE_LEDGER)} 文件，差集 0）；文档声称：「本机 skip 都是已知环境门控"
        "（PG / 平台）」；应改成：新增门控必须登记到 `registry.SKIP_GATE_LEDGER`（★ 判据用的清单"
        "**不是**从当次输出抄的）",
        files=files,
    )


# ═══ `AUD-C5` `doc_paths`（序号 8）════════════════════════════════════════════


@reg.criterion("AUD-C5", reg.HARD)
def check_doc_paths(ctx):
    """★ 判据面 = `docs/**` ∪ `team/**` 里**带行号坐标**的仓库路径引用（`` `path.ext:NN` ``）。
    ★ 归一化后**不在仓库内**的引用（`team/` 是符号链接 ⇒ 指向树外）⇒ **不判**，逐条登记。
    ★ 裸路径引用（无 `:NN`，含占位符 / 别名 / 未来文件）⇒ **不计入判据面**，逐条登记为提示。"""
    files, broken, escaped, bare = [], [], [], []
    for subdir in DOC_SCAN_DIRS:
        base = ctx.repo / subdir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in DOC_SUFFIXES:
                continue
            relname = _display(ctx.repo, path)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                if relname is not None:
                    return reg.Result(
                        "AUD-C5",
                        reg.HARD,
                        reg.UNKNOWN,
                        message=f"读不到文档 {relname} ⇒ 不得记绿",
                        files=files,
                    )
                continue
            if relname is None:
                escaped.append(path.name)
                continue
            files.append(relname)
            for match in DOC_COORD_RE.finditer(text):
                target_rel, lineno = match.group(1), int(match.group(2))
                target = ctx.repo / target_rel
                if _display(ctx.repo, target) is None:
                    escaped.append(f"{relname} → {target_rel}:{lineno}")
                    continue
                if not target.is_file():
                    broken.append(f"{relname} ⇒ {target_rel!r} 不存在")
                    continue
                try:
                    total = len(reg.read_lines(target))
                except (OSError, UnicodeDecodeError):
                    broken.append(f"{relname} ⇒ {target_rel!r} 读不到")
                    continue
                if lineno > total:
                    broken.append(
                        f"{relname} ⇒ {target_rel!r}:{lineno} 越界（该文件仅 {total} 行）"
                    )
            bare.extend(f"{relname} ⇒ {m.group(1)!r}" for m in DOC_BARE_RE.finditer(text))
    if not files:
        return reg.Result(
            "AUD-C5",
            reg.HARD,
            reg.UNKNOWN,
            message="判据面为空（docs/** ∪ team/** 无仓库内常规文件）⇒ 空跑，不计证据",
        )
    hints = []
    if bare:
        hints.append(
            f"★ 裸路径引用 {len(bare)} 处（无 `:NN` 坐标 ⇒ 不在判据面内 · 含占位符/别名/未来文件 "
            f"⇒ 判它必假红）· 例：{' · '.join(bare[:5])}"
        )
    if escaped:
        hints.append(
            f"★ 归一化后不在本仓库内的引用 {len(escaped)} 处（`team/` 是符号链接 ⇒ 指向树外 · 承 "
            f"`registry` 的 H3 口径）⇒ 不判：{' · '.join(escaped[:5])}"
        )
    if broken:
        return reg.Result(
            "AUD-C5",
            reg.HARD,
            reg.FAIL,
            checked=len(files),
            message=f"实测量：带坐标(`path:NN`)引用里失效 {len(broken)} 处"
            f"（{' · '.join(broken[:8])}）；文档声称：这些坐标指向仓库里真实存在的文件与行；"
            "应改成：把引用改到该符号的**真实出处文件:行**，或删掉该引用",
            files=files,
            hints=hints,
        )
    return reg.Result(
        "AUD-C5",
        reg.HARD,
        reg.OK,
        checked=len(files),
        message=f"实测量：`docs/**` ∪ `team/**` 常规文件 {len(files)} 个 · 带坐标引用失效 **0** 处"
        f"（裸引用 {len(bare)} 处、树外引用 {len(escaped)} 处均登记为边界）；文档声称：文档里的 "
        "`path:NN` 坐标都指向真实文件与行；应改成：新增坐标引用必须指向**存在**的文件且行号在范围内",
        files=files,
        hints=hints,
    )


# ═══ `AUD-C6` `symbol_home`（序号 9）══════════════════════════════════════════


def _defined_symbols(path):
    """★ 定义点（`AnnAssign` / `Assign` / `def` / `class`）⇒ `{name: [行号]}`；★ 解析失败 ⇒ `None`。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.setdefault(node.name, []).append(node.lineno)
            continue
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        for target in targets:
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
            if isinstance(name, str):
                found.setdefault(name, []).append(node.lineno)
    return found


@reg.criterion("AUD-C6", reg.HARD)
def check_symbol_home(ctx):
    """★ 冻结「符号 → 唯一出处」⇒ 双向判：**出处必须定义它** ∧ 冻结的**非出处**必须不定义它。"""
    if not reg.SYMBOL_HOMES:
        return reg.Result(
            "AUD-C6", reg.HARD, reg.UNKNOWN, message="冻结的符号清单为空 ⇒ 空跑，不计证据"
        )
    scan_dir = ctx.repo / reg.SYMBOL_SCAN_DIR
    if not scan_dir.is_dir():
        return reg.Result(
            "AUD-C6",
            reg.HARD,
            reg.UNKNOWN,
            message=f"符号扫描面不存在：{reg.SYMBOL_SCAN_DIR} ⇒ 不得记绿（基准目录 = {ctx.repo}）",
        )
    problems, files, measured = [], [], {}
    for entry in reg.SYMBOL_HOMES:
        symbol = entry["symbol"]
        actual = []
        for path in sorted(scan_dir.glob("*.py")):
            defined = _defined_symbols(path)
            if defined is None:
                return reg.Result(
                    "AUD-C6",
                    reg.HARD,
                    reg.UNKNOWN,
                    message=f"AST 解析失败：{reg.rel(ctx.repo, path)} ⇒ 读到不认识的格式 ⇒ 不得记绿",
                    files=files,
                )
            if symbol in defined:
                relname = reg.rel(ctx.repo, path)
                actual.append(relname)
                files.append(relname)
                measured.setdefault(symbol, {})[relname] = defined[symbol]
        if not actual:
            return reg.Result(
                "AUD-C6",
                reg.HARD,
                reg.UNKNOWN,
                message=f"符号 {symbol!r} 在 {reg.SYMBOL_SCAN_DIR} 里零定义点 ⇒ 空跑，不计证据",
                files=files,
            )
        if entry["home"] not in actual:
            problems.append(
                "{!r} 不在其冻结出处 {} 里定义（实测定义处 = {}）".format(
                    symbol, entry["home"], ", ".join(actual)
                )
            )
        for forbidden in entry["forbidden"]:
            if forbidden in actual:
                problems.append(
                    "{!r} 出现在冻结的**非出处** {} 里（实测定义处 = {}）".format(
                        symbol, forbidden, ", ".join(actual)
                    )
                )
            files.append(forbidden)
        files.append(entry["home"])
    if not files:
        return reg.Result("AUD-C6", reg.HARD, reg.UNKNOWN, message="判据面为空 ⇒ 空跑，不计证据")
    actual_text = " · ".join(
        "{!r} 定义处 = [{}]".format(symbol, ", ".join(sorted(where)))
        for symbol, where in sorted(measured.items())
    )
    claims = "{} / 非出处 {}".format(
        "/".join(entry["home"] for entry in reg.SYMBOL_HOMES),
        "/".join(f for entry in reg.SYMBOL_HOMES for f in entry["forbidden"]),
    )
    if problems:
        return reg.Result(
            "AUD-C6",
            reg.HARD,
            reg.FAIL,
            checked=len(measured),
            message="实测量：{}；文档声称：符号的唯一出处 = {}；应改成：把引用指向冻结出处，或更新冻结清单（★ 实际问题 = {}）".format(
                actual_text, claims, " · ".join(problems)
            ),
            files=files,
        )
    return reg.Result(
        "AUD-C6",
        reg.HARD,
        reg.OK,
        checked=len(measured),
        message=f"实测量：{actual_text}；文档声称：符号的唯一出处 = {claims}；应改成：保持现状（引用该符号时必须指向"
        "冻结出处）",
        files=files,
    )


# ═══ `AUD-C7` `dead_tables`（序号 10）═════════════════════════════════════════


def _migration_tables(root):
    """★ `{表名: [(迁移文件, 行号)]}` —— 只取 `NNN > registry.DEAD_TABLE_BASELINE_MAX` 的迁移。"""
    base = pathlib.Path(root) / reg.MIGRATIONS_DIR
    if not base.is_dir():
        return None
    tables = {}
    for path in sorted(base.glob("*.sql")):
        match = MIGRATION_NUM_RE.match(path.name)
        if not match or int(match.group(1)) <= reg.DEAD_TABLE_BASELINE_MAX:
            continue
        try:
            lines = reg.read_lines(path)
        except (OSError, UnicodeDecodeError):
            return None
        relname = reg.rel(root, path)
        for line_no, text in enumerate(lines, 1):
            for name in CREATE_TABLE_RE.findall(text):
                tables.setdefault(name, []).append((relname, line_no))
    return tables


def _code_sites(root, table):
    """★ 生产代码（`src/**`，撇除 docstring ∧ 撇除迁移目录）里的**读写点** ⇒ `(sites, None)`。"""
    sites = []
    base = pathlib.Path(root) / "src"
    if not base.is_dir():
        return None
    migrations = pathlib.Path(root) / reg.MIGRATIONS_DIR
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".sql"):
            continue
        if migrations in path.parents:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        relname = reg.rel(root, path)
        if table not in text:
            continue
        if path.suffix == ".sql":
            sites.append((relname, 0))
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return None
        docstrings = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
            ):
                docstrings.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if (
                (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and table in node.value
                    and id(node) not in docstrings
                )
                or isinstance(node, ast.Name)
                and node.id == table
                or isinstance(node, ast.Attribute)
                and node.attr == table
            ):
                sites.append((relname, node.lineno))
    return sites


@reg.criterion("AUD-C7", reg.HARD)
def check_dead_tables(ctx):
    """★ 只判「**本轮新增**迁移建的表在全仓零读写点」⇒ 基线冻结为「截至 `019`」（★ 防一次抓出历史
    死表 ⇒ 恒红 ⇒ 装饰性判据）。★ `020+` 零建表 ⇒ `UNKNOWN`（空跑，不计证据）。"""
    tables = _migration_tables(ctx.repo)
    if tables is None:
        return reg.Result(
            "AUD-C7",
            reg.HARD,
            reg.UNKNOWN,
            message=f"迁移目录读不到：{reg.MIGRATIONS_DIR} ⇒ 不得记绿（基准目录 = {ctx.repo}）",
        )
    floor = reg.DEAD_TABLE_BASELINE_MAX + 1
    if not tables:
        return reg.Result(
            "AUD-C7",
            reg.HARD,
            reg.UNKNOWN,
            message=f"`{floor}+` 迁移零建表 ⇒ 空跑，不计证据（★ 空集不得记绿）",
        )
    files, dead, hits_total = [], [], 0
    for table, where in sorted(tables.items()):
        sites = _code_sites(ctx.repo, table)
        if sites is None:
            return reg.Result(
                "AUD-C7",
                reg.HARD,
                reg.UNKNOWN,
                message=f"扫描 {table} 失败 ⇒ 不得记绿",
                files=files,
            )
        files.extend(relname for relname, _ in where)
        if not sites:
            dead.append("{}（由 {} 建立）".format(table, ", ".join(sorted({w[0] for w in where}))))
            continue
        hits_total += len(sites)
        files.extend(relname for relname, _ in sites)
    files = sorted(set(files))
    if dead:
        return reg.Result(
            "AUD-C7",
            reg.HARD,
            reg.FAIL,
            checked=len(tables),
            message=f"实测量：`{floor}+` 迁移新建表 {len(tables)} 个，其中**零读写点** {len(dead)} 个"
            f"（{' · '.join(dead)}）；文档声称：「新增的表都有读写点」；应改成：删表或补上读写点"
            "（或把基线显式登记为历史死表）",
            files=files,
        )
    return reg.Result(
        "AUD-C7",
        reg.HARD,
        reg.OK,
        checked=len(tables),
        message=f"实测量：`{floor}+` 迁移新建表 {len(tables)} 个，零读写点 **0** 个（生产代码命中 "
        f"{hits_total} 处）；文档声称：「新增的表都有读写点」；应改成：保持现状（★ 基线冻结在 "
        f"`{reg.DEAD_TABLE_BASELINE_MAX}` ⇒ `{reg.DEAD_TABLE_BASELINE_MAX}` 及以前的历史死表"
        "不在判据面内）",
        files=files,
    )


# ═══ `AUD-C8` `dispatch_write`（序号 11）═════════════════════════════════════


def _string_chunks(tree):
    """★ 逐个**字符串字面量分片**（含 f-string 的常量段）⇒ `[(行号, 文本)]`。

    ★ 口径：**不跨分片**做窗口匹配 —— 否则同一文件里的「`UPDATE … SET {join}`」与别处的
      `"thread_id"` 字面量会被误拼成一条语句 ⇒ 假红。
    """
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            chunks.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            for item in node.values:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    chunks.append((node.lineno, item.value))
    return chunks


@reg.criterion("AUD-C8", reg.HARD)
def check_dispatch_write(ctx):
    """★ `project_tasks.thread_id` 的**唯一**写入路径 = 派单（`registry.DISPATCH_WRITE_ALLOWED`）。
    ★ 建单（`create_task(thread_id=…)` / `INSERT`）**不入**写点面（逐条登记为边界）。"""
    writes, boundaries, files = [], [], []
    base = ctx.repo / "src"
    if not base.is_dir():
        return reg.Result(
            "AUD-C8",
            reg.HARD,
            reg.UNKNOWN,
            message=f"src/ 不存在（基准目录 = {ctx.repo}）⇒ 不得记绿",
        )
    for path in sorted(base.rglob("*.py")):
        relname = reg.rel(ctx.repo, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return reg.Result(
                "AUD-C8",
                reg.HARD,
                reg.UNKNOWN,
                message=f"读不到 {relname}（{exc}）⇒ 不得记绿",
                files=files,
            )
        if reg.DISPATCH_COLUMN not in text and reg.DISPATCH_TABLE not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return reg.Result(
                "AUD-C8",
                reg.HARD,
                reg.UNKNOWN,
                message=f"AST 解析失败 {relname}（{exc}）⇒ 不得记绿",
                files=files,
            )
        files.append(relname)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in UPDATE_CALL_ATTRS
            ):
                for keyword in node.keywords:
                    if keyword.arg == reg.DISPATCH_COLUMN:
                        writes.append(
                            f"{relname}:{node.lineno} ⇒ ORM `.{node.func.attr}(…, "
                            f"{reg.DISPATCH_COLUMN}=…)`"
                        )
        for line_no, chunk in _string_chunks(tree):
            if not SQL_UPDATE_RE.search(chunk):
                continue
            if reg.DISPATCH_COLUMN in chunk:
                writes.append(
                    f"{relname}:{line_no} ⇒ SQL `UPDATE {reg.DISPATCH_TABLE} SET "
                    f"…{reg.DISPATCH_COLUMN}…`"
                )
            else:
                boundaries.append(
                    f"{relname}:{line_no} ⇒ 通用 SET 子句站点（列名运行时决定 ⇒ 静态不可判）: "
                    f"{chunk.strip()[:70]}"
                )
    if not files:
        return reg.Result(
            "AUD-C8",
            reg.HARD,
            reg.UNKNOWN,
            message=f"src/ 里零 `{reg.DISPATCH_TABLE}` / `{reg.DISPATCH_COLUMN}` 命中 ⇒ 空跑，不计证据",
        )
    hints = []
    if boundaries:
        hints.append(
            f"★ 边界登记（不逼 `exit 2`）：通用 SET 子句站点 {len(boundaries)} 处 ⇒ "
            f"{' · '.join(sorted(set(boundaries))[:4])}"
        )
    if not writes:
        return reg.Result(
            "AUD-C8",
            reg.HARD,
            reg.UNKNOWN,
            checked=len(files),
            message=f"`{reg.DISPATCH_TABLE}.{reg.DISPATCH_COLUMN}` 写点 0 处 ⇒ 空跑，不计证据（★ 空集不得记绿）",
            files=files,
            hints=hints,
        )
    outside = [
        item
        for item in writes
        if not any(item.startswith(allowed + ":") for allowed in reg.DISPATCH_WRITE_ALLOWED)
    ]
    if outside:
        return reg.Result(
            "AUD-C8",
            reg.HARD,
            reg.FAIL,
            checked=len(writes),
            message=f"实测量：`{reg.DISPATCH_TABLE}.{reg.DISPATCH_COLUMN}` 写点 {len(writes)} 处"
            f"（允许清单 = {' · '.join(reg.DISPATCH_WRITE_ALLOWED)}），越界 {len(outside)} 处"
            f"（{' · '.join(outside)}）；文档声称："
            f"「`{reg.DISPATCH_TABLE}.{reg.DISPATCH_COLUMN}` 的唯一写入路径 = 派单」；应改成：删掉派单"
            f"之外的写点，或经唯一入口 {' · '.join(reg.DISPATCH_WRITE_ALLOWED)}",
            files=files,
            hints=hints,
        )
    return reg.Result(
        "AUD-C8",
        reg.HARD,
        reg.OK,
        checked=len(writes),
        message=f"实测量：`{reg.DISPATCH_TABLE}.{reg.DISPATCH_COLUMN}` 写点 {len(writes)} 处"
        f"（{' · '.join(writes)}）· 全部落在允许清单 {' · '.join(reg.DISPATCH_WRITE_ALLOWED)} 内；"
        f"文档声称：「唯一写入路径 = 派单」；应改成：保持现状（建单 "
        f"`create_task({reg.DISPATCH_COLUMN}=…)` 不入写点面）",
        files=files,
        hints=hints,
    )


# ═══ `AUD-C9` `dashboard_stale`（序号 15）════════════════════════════════════


@reg.criterion("AUD-C9", reg.HARD)
def check_dashboard_stale(ctx):
    """★ 输入 = `dashboard/public/**`（`vite` **原样 copy** 到 outDir 根的源资产）vs
    `src/octop/dashboard/**`（构建产物）。★ 源码树有、产物树无 ⇒ 红（= 改了没构建）。
    ★ 产物树缺失 ⇒ `UNKNOWN` + **打印基准目录**（★ `R8`）⇒ 绝不记绿。"""
    public = ctx.repo / reg.DASHBOARD_PUBLIC_REL
    artifact = ctx.repo / reg.DASHBOARD_ARTIFACT_REL
    if not public.is_dir() or not artifact.is_dir():
        state = " · ".join(
            "{} = {}".format(name, "存在" if path.is_dir() else "缺失")
            for name, path in (
                (reg.DASHBOARD_PUBLIC_REL, public),
                (reg.DASHBOARD_ARTIFACT_REL, artifact),
            )
        )
        # ★ `R8`：输出**基准目录**；★ 输入面取**可读的那一侧**（两侧都缺 ⇒ 空输入面 ⇒ 由
        #   `current_tree.py:48` 的既有空输入守卫判 `FAIL` —— fail-closed，不绕它）。
        readable = public if public.is_dir() else artifact
        base = reg.DASHBOARD_PUBLIC_REL if public.is_dir() else reg.DASHBOARD_ARTIFACT_REL
        known = sorted(
            f"{base}/{path.relative_to(readable).as_posix()}"
            for path in readable.rglob("*")
            if path.is_file()
        )
        return reg.Result(
            "AUD-C9",
            reg.HARD,
            reg.UNKNOWN,
            message=f"产物树 / 源资产目录不齐 ⇒ 不得记绿（★ 未知 ≠ 绿）· 基准目录 = {ctx.repo} · "
            f"{state} · 可读输入面 = {len(known)} 个文件",
            files=known,
        )
    source_files = sorted(
        path.relative_to(public).as_posix() for path in public.rglob("*") if path.is_file()
    )
    artifact_files = {
        path.relative_to(artifact).as_posix() for path in artifact.rglob("*") if path.is_file()
    }
    if not source_files:
        return reg.Result(
            "AUD-C9",
            reg.HARD,
            reg.UNKNOWN,
            message=f"{reg.DASHBOARD_PUBLIC_REL} 为空 ⇒ 空跑，不计证据",
        )
    missing_items = [item for item in source_files if item not in artifact_files]
    files = [f"{reg.DASHBOARD_PUBLIC_REL}/{item}" for item in source_files]
    if missing_items:
        return reg.Result(
            "AUD-C9",
            reg.HARD,
            reg.FAIL,
            checked=len(source_files),
            message=f"实测量：源资产 {reg.DASHBOARD_PUBLIC_REL} 有 {len(source_files)} 个文件，其中 "
            f"{len(missing_items)} 个在产物树 {reg.DASHBOARD_ARTIFACT_REL} 里**没有**对应项"
            f"（{' · '.join(missing_items[:8])}）；文档声称：「{reg.DASHBOARD_ARTIFACT_REL} 已是当次"
            "源码的构建产物」；应改成：跑 `make build-frontend` 重新构建（★ 时间戳只作证据 · 不作判据）",
            files=files,
        )
    return reg.Result(
        "AUD-C9",
        reg.HARD,
        reg.OK,
        checked=len(source_files),
        message=f"实测量：源资产 {reg.DASHBOARD_PUBLIC_REL} 的 {len(source_files)} 个文件在产物树 "
        f"{reg.DASHBOARD_ARTIFACT_REL} 里都有对应项（缺失 0）· 基准目录 = {ctx.repo}；文档声称："
        f"「{reg.DASHBOARD_ARTIFACT_REL} 已是当次源码的构建产物」；应改成：保持现状"
        "（`make build-frontend` 后复核）· ★ 边界：`dashboard/src/**` → 哈希包名**不可按路径镜像判** "
        "⇒ 只判 `public/**` 这一路（不拿绿冒充覆盖）",
        files=files,
    )
