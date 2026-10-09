"""★ `AUD-C2` `makefile.gate_test_pytest_only`（`P1-2` 序号 5 · 18 同一判据）·
★ `AUD-C3` `makefile.gate_write_first`（序号 22 · 与 `AUD-C2` **同模块**，只追加函数）。

★ 输入 = `Makefile`（`registry.MAKEFILE_NAME`）⇒ ★ 不新增常量：`all:` 行号锚点复用
  `registry.MAKEFILE_FROZEN_LINENO`（与 `AUD-G2` **同一出处**）· 前端 runner 词表复用
  `registry.FRONTEND_RUNNER_TOKENS`。
★ 离线可用（★ 只读 `Makefile` + 只读 `git status/diff`，无网络）。
"""

import fnmatch
import re

from .. import registry as reg

TARGET_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*):(?!=)(.*)$")
FRONTEND_GLOBS = (reg.FRONTEND_TEST_GLOB, reg.FRONTEND_TEST_GLOB_TSX)


def _targets(lines):
    """★ 解析目标体 ⇒ `{name: {"lineno", "deps", "body"}}`（★ 只认列 0 的目标行 + tab 配方）。"""
    targets, index = {}, 0
    while index < len(lines):
        text = lines[index]
        match = TARGET_RE.match(text) if text and text[0] not in "\t#" else None
        if match is None:
            index += 1
            continue
        body, cursor = [], index + 1
        while cursor < len(lines):
            nxt = lines[cursor]
            if nxt.startswith("\t") or nxt.rstrip().endswith("\\"):
                body.append(nxt)
                cursor += 1
            elif not nxt.strip():
                peek = cursor
                while peek < len(lines) and not lines[peek].strip():
                    peek += 1
                if peek < len(lines) and lines[peek].startswith("\t"):
                    body.extend(lines[cursor:peek])
                    cursor = peek
                else:
                    break
            else:
                break
        targets[match.group(1)] = {
            "lineno": index + 1,
            "deps": match.group(2).strip().split(),
            "body": body,
        }
        index = cursor
    return targets


def _read_makefile(ctx):
    """⇒ `(lines, None)` 或 `(None, 错误说明)`（★ 读不到 ⇒ 调用方输出 `UNKNOWN` ⇒ 不得记绿）。"""
    path = ctx.repo / reg.MAKEFILE_NAME
    if not path.is_file():
        return None, f"Makefile 读不到 ⇒ 不得记绿（基准目录 = {ctx.repo}）"
    try:
        return reg.read_lines(path), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"Makefile 读不到（{exc}）⇒ 不得记绿"


def _working_frontend_tests(root):
    """★ 本轮**新增/改动**的 `dashboard/**/*.test.ts?(x)`（★ 未提交工作树 ⇒ 只读 `git`）。"""
    status = reg._git(root, ["status", "--porcelain", "--untracked-files=all"])
    diff = reg._git(root, ["diff", "--name-only", "HEAD"])
    if status is None or diff is None or status.returncode != 0 or diff.returncode != 0:
        return None
    names = set()
    for line in status.stdout.split("\n"):
        if not line.strip():
            continue
        names.add(line[3:].strip().split(" -> ")[-1])
    names.update(item.strip() for item in diff.stdout.split("\n") if item.strip())
    return sorted(
        name for name in names if any(fnmatch.fnmatch(name, pattern) for pattern in FRONTEND_GLOBS)
    )


@reg.criterion("AUD-C2", reg.HARD)
def check_makefile_gate_test(ctx):
    """★ 序号 5：`test:` **只跑 pytest**（不含前端 runner）—— 三元组给出该事实；
    ★ 序号 18（附加）：本轮新增/改动的前端测试若不被 `test:` 覆盖 ⇒ 红并逐文件列出。"""
    lines, error = _read_makefile(ctx)
    if error is not None:
        return reg.Result("AUD-C2", reg.HARD, reg.UNKNOWN, message=error)
    targets = _targets(lines)
    test = targets.get(reg.MAKEFILE_TEST_TARGET.rstrip(":"))
    frontend = targets.get(reg.MAKEFILE_FRONTEND_TARGET.rstrip(":"))
    if test is None:
        return reg.Result(
            "AUD-C2",
            reg.HARD,
            reg.UNKNOWN,
            message=f"Makefile 无 {reg.MAKEFILE_TEST_TARGET} 目标 ⇒ 不得记绿",
            files=[reg.MAKEFILE_NAME],
        )
    if not test["body"]:
        return reg.Result(
            "AUD-C2",
            reg.HARD,
            reg.UNKNOWN,
            message=f"{reg.MAKEFILE_TEST_TARGET} 目标体为空 ⇒ 空跑，不计证据",
            files=[reg.MAKEFILE_NAME],
        )
    runner_hits = []
    for offset, text in enumerate(test["body"]):
        for token in reg.FRONTEND_RUNNER_TOKENS:
            if token in text:
                runner_hits.append(f"Makefile:{test['lineno'] + offset} ⇒ {token!r}")
    new_tests = _working_frontend_tests(ctx.repo)
    frontend_text = (
        f"另有 {reg.MAKEFILE_FRONTEND_TARGET}（{reg.MAKEFILE_NAME}:{frontend['lineno']}）"
        if frontend
        else f"★ 无 {reg.MAKEFILE_FRONTEND_TARGET}"
    )
    checked = len(test["body"])
    if runner_hits:
        return reg.Result(
            "AUD-C2",
            reg.HARD,
            reg.FAIL,
            checked=checked,
            message=f"实测量：{reg.MAKEFILE_TEST_TARGET} 目标体 {checked} 条命令行里出现前端 runner "
            f"{len(runner_hits)} 处（{' · '.join(runner_hits)}）；文档声称：「`make all` 的门禁已覆盖"
            f"前端测试与前端类型检查」；应改成：把前端 runner 移出 `{reg.MAKEFILE_TEST_TARGET}`，"
            f"并写成「前端另行 `npm run test`（{reg.MAKEFILE_FRONTEND_TARGET}）+ `npx tsc -b`」",
            files=[reg.MAKEFILE_NAME],
        )
    base = (
        f"实测量：`{reg.MAKEFILE_TEST_TARGET}` 目标体 {checked} 条命令行（{reg.MAKEFILE_NAME}:"
        f"{test['lineno']}）**不含**任何前端 runner（词表 = {'/'.join(reg.FRONTEND_RUNNER_TOKENS)}）；"
        f"文档声称：「`make all` 的门禁已覆盖前端测试」；应改成：「前端另行 `npm run test` + "
        f"`npx tsc -b`」（{frontend_text}）"
    )
    if new_tests is None:
        return reg.Result(
            "AUD-C2",
            reg.HARD,
            reg.OK,
            checked=checked,
            skipped=1,
            message=f"{base} · ★ 序号 18 子句：git 不可用 ⇒ 未观察本轮新增前端测试 ⇒ 空跑，"
            "不计证据",
            files=[reg.MAKEFILE_NAME],
            hints=["★ git 不可用 ⇒ 序号 18 子句未判（不得当作已覆盖）"],
        )
    if not new_tests:
        return reg.Result(
            "AUD-C2",
            reg.HARD,
            reg.OK,
            checked=checked,
            skipped=1,
            message=f"{base} · ★ 序号 18 子句：本轮新增/改动前端测试 = 0 ⇒ 空跑，不计证据",
            files=[reg.MAKEFILE_NAME],
            hints=[
                "★ 空跑（R14）：`git status --porcelain` ∪ `git diff --name-only HEAD` 里 0 个 dashboard/**/*.test.ts?(x) ⇒ 不得当作「门禁已覆盖本轮新增测试」"
            ],
        )
    return reg.Result(
        "AUD-C2",
        reg.HARD,
        reg.FAIL,
        checked=checked,
        message=f"实测量：本轮新增/改动前端测试 {len(new_tests)} 个（{' · '.join(new_tests)}），而 "
        f"`{reg.MAKEFILE_TEST_TARGET}` 目标体的 {checked} 条命令行**没有**任何前端 runner；"
        "文档声称：「门禁覆盖本轮新增测试」；应改成：另跑 `npm run test` 并逐文件复核",
        files=[reg.MAKEFILE_NAME] + new_tests,
    )


@reg.criterion("AUD-C3", reg.HARD)
def check_makefile_gate_order(ctx):
    """★ 序号 22：`all:` 的 `format-all`（**会改树的写者**）必须排在所有检查类目标之前。"""
    lines, error = _read_makefile(ctx)
    if error is not None:
        return reg.Result("AUD-C3", reg.HARD, reg.UNKNOWN, message=error)
    targets = _targets(lines)
    all_target = targets.get(reg.MAKEFILE_ALL_TARGET.rstrip(":"))
    if all_target is None:
        return reg.Result(
            "AUD-C3",
            reg.HARD,
            reg.UNKNOWN,
            message=f"Makefile 无 {reg.MAKEFILE_ALL_TARGET} 目标 ⇒ 不得记绿",
            files=[reg.MAKEFILE_NAME],
        )
    deps = all_target["deps"]
    if not deps:
        return reg.Result(
            "AUD-C3",
            reg.HARD,
            reg.UNKNOWN,
            message=f"{reg.MAKEFILE_ALL_TARGET} 依赖列为空 ⇒ 空跑，不计证据",
            files=[reg.MAKEFILE_NAME],
        )
    anchor = (
        f"（all: 行号锚点 = {reg.MAKEFILE_NAME}:{reg.MAKEFILE_FROZEN_LINENO} · "
        f"实际出现于 :{all_target['lineno']}）"
    )
    if "format-all" not in deps:
        return reg.Result(
            "AUD-C3",
            reg.HARD,
            reg.FAIL,
            checked=len(deps),
            message=f"实测量：`{reg.MAKEFILE_ALL_TARGET}` 依赖序列 = {' '.join(deps)}（"
            f"{reg.MAKEFILE_NAME}:{all_target['lineno']}）里**没有** format-all{anchor}；文档声称："
            f"「门禁自身不改工作树」；应改成：把 format-all 放回首位（或把 "
            f"`{reg.MAKEFILE_ALL_TARGET}` 显式标为写者）",
            files=[reg.MAKEFILE_NAME],
        )
    if deps[0] != "format-all":
        before = deps[: deps.index("format-all")]
        return reg.Result(
            "AUD-C3",
            reg.HARD,
            reg.FAIL,
            checked=len(deps),
            message=f"实测量：`{reg.MAKEFILE_ALL_TARGET}` 依赖序列 = {' '.join(deps)}（"
            f"{reg.MAKEFILE_NAME}:{all_target['lineno']}）· format-all 之前有 {len(before)} 个检查类"
            f"目标（{' '.join(before)}）{anchor}；文档声称：「门禁自身不改工作树」；应改成：保持 "
            "format-all **首位**（写后读 ⇒ 否则「被检查的树」≠「最终树」，指纹会漂移）",
            files=[reg.MAKEFILE_NAME],
        )
    return reg.Result(
        "AUD-C3",
        reg.HARD,
        reg.OK,
        checked=len(deps),
        message=f"实测量：`{reg.MAKEFILE_ALL_TARGET}` 依赖序列 = {' '.join(deps)}（"
        f"{reg.MAKEFILE_NAME}:{all_target['lineno']}）· format-all 在**首位**（其后检查类目标 "
        f"{len(deps) - 1} 个）{anchor}；文档声称：「门禁自身不改工作树」；应改成：保持现状"
        "（format-all 首位 = 写后读顺序正确）",
        files=[reg.MAKEFILE_NAME],
    )
