#!/usr/bin/env python3
"""G-5 门面计数 —— 唯一口径 `G5-1`（calibrated spec）参考实现。

背景：`team/2026-09-30-134228` 批内对同一件事（`api/routers/**` 里"有多少路由处理器
带角色/权限门、带资源级归属校验、两者皆无"）出现过**三套互不相符的读数**：

    R1（RESEARCH.md:44）    = 537 / 239 / 49 / 270
    D1（REPORT.md:89）      = 499 / 213 / 53 / 256
    V1（TEST.md:65）        = 530 / 239 / 53 / 261

三套的**书面口径**（`REPORT.md §3`）无法让任何人复算出同一组数，故 `G-5` 成立。
本文件把口径写死成可执行正文，产出**唯一读数**；`CALIBRATION.md` 是它的说明书。

★ 本脚本**不是**合规结论，**不是**覆盖率；它没有阈值、没有判据、没有 pass/fail。
   它只解决"同一件事只有一套可复算读数"。

★ 归位说明：本脚本的**长期归属**是 `scripts/audit/`（受管审计判据树，与
   `current_tree.py` 同级），等 `P1-17` 接线时整体平移；当前先落
   `docs/planning/tools/`（本工作树唯一被授权的脚本落点）。

用法::

    # 唯一口径（默认）—— CALIBRATION.md 里那条可粘贴命令
    .venv/bin/python docs/planning/tools/g5-route-gate-count.py

    # 阳性对照：换扫描根 / 换装饰器集 ⇒ 读数必须变（证明扫描器在工作）
    .venv/bin/python docs/planning/tools/g5-route-gate-count.py --root src/octop/api
    .venv/bin/python docs/planning/tools/g5-route-gate-count.py --decorators base

    # 归因用：按已被证伪的历史口径复跑（★ 不是口径的一部分，只用于对账）
    .venv/bin/python docs/planning/tools/g5-route-gate-count.py --reconstruct R1

退出码恒为 0（扫描完成 ≠ 通过）。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# §1 唯一口径 G5-1 —— 以下每组常量都是口径的一部分；改任何一个 = 换口径
# ---------------------------------------------------------------------------

#: 口径 G5-1 的扫描根（相对 repo 根）· 递归 · 只取 `*.py` · 排除 `__init__.py`
ROOT = "src/octop/api/routers"
SUBDIR = "browser"  #: 见 `--reconstruct D1`：历史 D1 的文件集 = 非递归 + browser/**

#: 路由装饰器终名。`ext` = base ∪ {api_route, websocket}（口径 G5-1 采用 ext）
BASE_DECORATORS = frozenset({"get", "post", "put", "patch", "delete"})
EXT_DECORATORS = BASE_DECORATORS | {"api_route", "websocket"}

#: 统计对象 = 一个「路由处理器」= 一个带路由装饰器的 FunctionDef/AsyncFunctionDef。
#: ★ 一个函数即使叠了多个路由装饰器也只计 1 次；嵌套函数各自独立判定。

#: 「角色/权限门」——**精确标识符**集合（不是子串、不是行匹配）
PERMISSION_NAMES = frozenset({"require_permission", "require_admin", "_may_govern"})

#: 「资源级归属校验」——**精确标识符**集合 + 一条**锚定**正则
OWNERSHIP_NAMES = frozenset({"_require_agent_access", "require_running_workspace"})
OWNERSHIP_RE = re.compile(r"^_?assert_\w*owner\w*$")

#: ★ 匹配式（本口径最关键的一条，历史三套都缺）：
#:   命中 = 该处理器**函数体或签名**内出现的**标识符终名**（`ast.Name.id` /
#:   `ast.Attribute.attr`）∈ 上表，或匹配上条锚定正则。
#:   —— 不做子串匹配、不做整行文本匹配、不把字符串常量/关键字实参名算作命中。

#: 「两者皆无」= N − |权限门 ∪ 归属校验|（**并集**语义）。
#: ★ 必须写死为并集：三套历史读数里 `N − perm − own` 与 reported `neither` 都对不上，
#:   因为两类可以重叠（同一处理器既有权限门又有归属校验）。

# ---------------------------------------------------------------------------
# §2 对账预设（★ 只用于归因复现，不是口径）
# ---------------------------------------------------------------------------

#: 历史书面口径（`REPORT.md §3`）逐字列出的检测集 —— 注意它写的
#: `require_agent_access` 在本树**根本不存在**（真实标识符是 `_require_agent_access`），
#: 故它只能靠**子串**匹配意外命中。
HIST_PERMISSION = ("require_permission", "require_admin", "require_agent_access", "_may_govern")
HIST_OWNERSHIP = ("require_agent_access", "require_running_workspace")
HIST_OWNERSHIP_UID = HIST_OWNERSHIP + ("owner_user_id",)
HIST_RE = re.compile(r"_assert_\w*owner")  # ★ 未锚定（逐字照抄书面口径）

RECONSTRUCTIONS: dict[str, dict] = {
    # R1 = RESEARCH.md:44 —— 递归 · ext 装饰器 · 文本子串
    "R1": {"fileset": "recursive", "decorators": EXT_DECORATORS, "matcher": "text",
           "perm": HIST_PERMISSION, "own": HIST_OWNERSHIP},
    # V1 = TEST.md:65 —— 递归 · base 装饰器 · 文本子串
    "V1": {"fileset": "recursive", "decorators": BASE_DECORATORS, "matcher": "text",
           "perm": HIST_PERMISSION, "own": HIST_OWNERSHIP_UID},
    # D1 = REPORT.md:89 —— 非递归 core + browser/** · base 装饰器 · 文本子串
    "D1": {"fileset": "core+browser", "decorators": BASE_DECORATORS, "matcher": "text",
           "perm": HIST_PERMISSION, "own": HIST_OWNERSHIP_UID},
}


# ---------------------------------------------------------------------------
# §3 扫描实现
# ---------------------------------------------------------------------------


def terminal_name(node: ast.expr) -> str | None:
    """装饰器终名：`router.get(...)` -> 'get'；`websocket(...)` -> 'websocket'。"""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def identifiers(node: ast.AST) -> set[str]:
    """函数内（含签名与装饰器）出现过的**标识符终名**。

    ★ 用标识符而不是整行文本，是因为 `Depends(require_admin)` 里的
    `require_admin` 是**裸 Name 实参**、`owner_user_id=x` 里的是**关键字实参名**
    （`ast.keyword.arg` 是字符串、根本没有对应 AST 节点）。按文本匹配会把后者
    也计入命中，这正是历史读数的发散来源之一。
    """
    found: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Attribute):
            found.add(item.attr)
        elif isinstance(item, ast.Name):
            found.add(item.id)
    return found


def collect_files(repo: Path, fileset: str) -> list[Path]:
    base = repo / ROOT
    if fileset == "recursive":
        paths = sorted(base.rglob("*.py"))
    elif fileset == "core+browser":
        paths = sorted(base.glob("*.py")) + sorted((base / SUBDIR).rglob("*.py"))
    else:  # pragma: no cover - argparse 已限定取值
        raise SystemExit(f"[G-5] 未知 fileset：{fileset}")
    return [p for p in paths if p.name != "__init__.py"]


def scan(
    repo: Path,
    root: str = ROOT,
    fileset: str = "recursive",
    decorators: frozenset[str] = EXT_DECORATORS,
    matcher: str = "identifier",
    perm: frozenset[str] | tuple[str, ...] = PERMISSION_NAMES,
    own: frozenset[str] | tuple[str, ...] = OWNERSHIP_NAMES,
    own_re: re.Pattern[str] = OWNERSHIP_RE,
) -> dict:
    files = collect_files(repo, fileset) if root == ROOT else [
        p for p in sorted((repo / root).rglob("*.py")) if p.name != "__init__.py"
    ]

    total = n_perm = n_own = n_both = 0
    finger = hashlib.sha256()
    for path in files:
        rel = path.relative_to(repo).as_posix()
        raw = path.read_bytes()
        finger.update(rel.encode("utf-8"))
        finger.update(b"\0")
        finger.update(hashlib.sha256(raw).hexdigest().encode("ascii"))
        finger.update(b"\n")

        tree = ast.parse(raw.decode("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(terminal_name(d) in decorators for d in node.decorator_list):
                continue

            total += 1
            if matcher == "identifier":
                names = identifiers(node)
                has_perm = bool(names & set(perm))
                has_own = bool(names & set(own)) or any(own_re.match(n) for n in names)
            else:  # "text" —— 历史口径的匹配式（子串），★ 已证伪，仅用于对账
                text = ast.unparse(node)
                has_perm = any(x in text for x in perm)
                has_own = any(x in text for x in own) or bool(own_re.search(text))

            n_perm += has_perm
            n_own += has_own
            n_both += has_perm and has_own

    union = n_perm + n_own - n_both
    return {
        "root": root,
        "fileset": fileset,
        "matcher": matcher,
        "decorators": sorted(decorators),
        "files_scanned": len(files),
        "fileset_sha256": finger.hexdigest(),
        "handlers": total,
        "permission_gate": n_perm,
        "ownership_check": n_own,
        "both": n_both,
        "union": union,
        "neither": total - union,
    }


# ---------------------------------------------------------------------------
# §4 输出（★ 逐字稳定：无时间戳、无随机、无环境相关字段）
# ---------------------------------------------------------------------------


def render(d: dict, repo: Path, spec: str) -> str:
    return "\n".join(
        [
            f"== G-5 门面计数 · 口径 {spec} ==",
            f"repo            = {repo}",
            f"scan_root       = {d['root']} ({d['fileset']} · *.py · 排除 __init__.py)",
            f"route_decorators= {{{','.join(d['decorators'])}}}",
            f"matcher         = {d['matcher']}",
            f"files_scanned   = {d['files_scanned']}",
            f"fileset_sha256  = {d['fileset_sha256']}",
            "stat_unit       = 路由处理器（函数定义去重）",
            f"handlers        = {d['handlers']}",
            f"permission_gate = {d['permission_gate']}",
            f"ownership_check = {d['ownership_check']}",
            f"both            = {d['both']}",
            f"union           = {d['union']}",
            f"neither         = {d['neither']}",
            f"reading         = {d['handlers']}/{d['permission_gate']}/"
            f"{d['ownership_check']}/{d['neither']}",
            "== 本读数不是合规结论、不是覆盖率；无阈值、无判据 ==",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G-5 唯一口径 G5-1 参考实现")
    ap.add_argument("--repo", default=None, help="仓库根（默认为本脚本上溯到含 src/octop 的目录）")
    ap.add_argument("--root", default=ROOT, help=f"扫描根（默认 {ROOT}）")
    ap.add_argument("--decorators", default="ext", help="base | ext | 逗号分隔终名清单（默认 ext）")
    ap.add_argument(
        "--reconstruct",
        choices=sorted(RECONSTRUCTIONS),
        default=None,
        help="按已证伪的历史口径复跑（仅用于归因对账，不是口径的一部分）",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args(argv)

    if args.repo:
        repo = Path(args.repo).resolve()
    else:
        repo = Path(__file__).resolve().parent
        while not (repo / "src" / "octop").is_dir():
            if repo.parent == repo:
                raise SystemExit("[G-5] 找不到含 src/octop 的仓库根；请用 --repo")
            repo = repo.parent

    if args.reconstruct:
        preset = RECONSTRUCTIONS[args.reconstruct]
        result = scan(
            repo,
            root=ROOT,
            fileset=preset["fileset"],
            decorators=preset["decorators"],
            matcher=preset["matcher"],
            perm=preset["perm"],
            own=preset["own"],
            own_re=HIST_RE,
        )
        spec = f"{args.reconstruct}（历史口径复跑 · 仅对账）"
    else:
        if args.decorators == "base":
            decorators = BASE_DECORATORS
        elif args.decorators == "ext":
            decorators = EXT_DECORATORS
        else:
            decorators = frozenset(x.strip() for x in args.decorators.split(",") if x.strip())
        result = scan(repo, root=args.root, decorators=decorators)
        spec = "G5-1（唯一口径）"

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render(result, repo, spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
