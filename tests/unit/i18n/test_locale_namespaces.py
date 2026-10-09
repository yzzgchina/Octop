"""`P1-16` · 判据面 **B 类 序号 2** —— dashboard locale 的**命名空间收敛**（禁止整树相等）。

## ★★ 为什么**禁**整树相等断言
`CRITERIA-29-CLASSIFICATION.md` `§1` 序号 2 逐字：`dashboard/src/locales/{en,zh}.json` 的
en/zh **全树键树本就不等** ⇒ 整树相等断言**今天就是红的** = **恒红门**（本项目禁）。
实测（本工作树 `1d3623ee`）：en 展平 **5928** 键 / zh **6047** 键；82 个命名空间里
**10 个**有漂移（`memory` 114 · `scenes` 10 · 其余 8 个各 1）。

## 分工（★ 与 `P1-6` 同源不重复）
| 文件 | 口径 | 判什么 |
|---|---|---|
| `test_locale_drift.py`（`P1-6`） | **键级基线差分**（新增才红） | 新出现的漂移键 |
| 本文件 | **命名空间级收敛** + **反恒红门扫描** | 契约命名空间**逐键全等**；且本目录**不存在**整树相等断言 |

★ 展平 `_flatten` / 命名空间 `_namespace` / 读盘 `_load_locale` / 漂移 `_drift`
**全部 import 自 `P1-6`**（第 2 条硬约束：不得造第二份展平/差分实现）；
本文件**零**重复实现，漂移口径也只有 `_drift` 一处（漂移命名空间 = `_drift` 的键集）。

## 判别性对照（原始输出见 `team/2026-10-08-110000/EVIDENCE-P1-16.md`）
- 收敛断言：把 en 的 `apiErrors.*` 注入一个 zh 没有的键（**临时副本**落 `tmp_path`，
  真实文件**只读**）⇒ 必须返红，且报出该键。
- 反恒红门扫描：在 `tmp_path` 造一个「整树相等断言」的 `.py` ⇒ 必须报出违规行号。

## 已知边界（诚实登记，不假装覆盖）
整表加载后**经显式命名空间过滤**的比较（如 `_flatten(table["apiErrors"])`）是**允许**的；
本扫描只识别**直接**整树取法（`_load_locale(...)` 或直接绑定其结果的变量）。
若先 `t = _load_locale("en")` 再 `x = t["ns"]`，`x` 不算整树——这类**间接**形态扫不到。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

# ★ 复用 P1-6（同目录兄弟模块；pytest 的 prepend 导入模式把它放上 sys.path）
from test_locale_drift import _drift, _flatten, _load_locale, _namespace

_API_ERRORS = "apiErrors"


# ─────────────────────────────────────────────────────────────────────────────
# 命名空间级收敛（纯函数：真实文件与临时副本共用同一套判定）
# ─────────────────────────────────────────────────────────────────────────────


def _namespace_keys(table: dict[str, Any], namespace: str) -> set[str]:
    """表内某命名空间的展平键（展平**复用** P1-6 的 `_flatten`）。"""
    return {key for key in _flatten(table) if _namespace(key) == namespace}


def _namespace_only_keys(
    en_table: dict[str, Any], zh_table: dict[str, Any], namespace: str
) -> tuple[list[str], list[str]]:
    """（仅 en 有, 仅 zh 有）—— 命名空间收敛的差分。"""
    en_keys = _namespace_keys(en_table, namespace)
    zh_keys = _namespace_keys(zh_table, namespace)
    return sorted(en_keys - zh_keys), sorted(zh_keys - en_keys)


def _convergence_message(namespace: str, only_en: list[str], only_zh: list[str]) -> str:
    lines = [
        f"dashboard 命名空间 `{namespace}` 的 en/zh 键树**不收敛**（本判据要求逐键全等）：",
        "",
        f"  仅 en.json 有（{len(only_en)}）：{only_en[:10]}",
        f"  仅 zh.json 有（{len(only_zh)}）：{only_zh[:10]}",
        "",
        "期望：契约命名空间（如 `apiErrors` —— 后端 `ErrorCode` 的镜像）两侧逐键相同。",
        "★ 注意：**不要**改成整树相等断言 —— dashboard 全树本就不等（见本文件模块 docstring）。",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 断言 ① —— 命名空间收敛（**不是**整树相等）
# ─────────────────────────────────────────────────────────────────────────────


def test_api_errors_namespace_converges_between_en_and_zh() -> None:
    """`apiErrors` 是后端↔前端契约命名空间 ⇒ en/zh **逐键全等**（命名空间级口径）。"""
    en_table = _load_locale("en")
    zh_table = _load_locale("zh")
    only_en, only_zh = _namespace_only_keys(en_table, zh_table, _API_ERRORS)

    checked = len(_namespace_keys(en_table, _API_ERRORS))
    assert checked > 0, "`apiErrors` 一个键都没有 —— 本用例会变成空断言"
    assert not only_en and not only_zh, _convergence_message(_API_ERRORS, only_en, only_zh)


def test_drift_is_confined_to_some_namespaces_not_the_whole_tree() -> None:
    """★ 「禁止整树相等」的**结构性**依据：漂移只落在**真子集**上，不是整棵树。

    本用例同时是判据的**报告面**：失败消息里列出今天的漂移命名空间清单。
    ★ 全部收敛（空集）**仍然绿** —— 收敛是改进，不得返红。
    """
    en_drift = _drift("en")
    zh_drift = _drift("zh")
    drifted = set(en_drift) | set(zh_drift)
    all_namespaces = {
        _namespace(key) for key in _flatten(_load_locale("en")) | _flatten(_load_locale("zh"))
    }
    assert all_namespaces, "一个命名空间都没读到 —— 本用例会变成空断言"

    counts = {ns: len(en_drift.get(ns, set()) | zh_drift.get(ns, set())) for ns in drifted}
    assert drifted < all_namespaces, (
        "漂移覆盖了**全部**命名空间 —— 此时才谈得上整树口径；今天的实际分布：\n"
        f"  漂移命名空间（{len(drifted)}/{len(all_namespaces)}）："
        f"{ {ns: counts[ns] for ns in sorted(drifted)} }\n"
        "★ 若真到这一步，请**先**改 `P1-6` 的基线口径，再谈整树断言（今天整树相等必红）。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 断言 ② —— 反恒红门扫描：本目录**不得**出现 dashboard 整树相等断言
#
# 为什么这是判据而不是纪律句：序号 2 的**唯一**硬禁令就是「不得整树相等」；
# 「同一处方两份拷贝」的历史返工都起于「纪律只写在文档里」。故把它变成能返红的扫描。
# ★ 后端全树相等是**允许**的（序号 3 明令要求）⇒ 本扫描**只**管 dashboard 侧的
#   `_load_locale` 取法，**不**把 `all_keys_for_locale` 算作整树。
# ─────────────────────────────────────────────────────────────────────────────

_WHOLE_TREE_LOADERS = {"_load_locale"}
_FILTERED_NODES = (ast.SetComp, ast.DictComp, ast.ListComp, ast.GeneratorExp)


def _is_whole_tree_load(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _WHOLE_TREE_LOADERS
    )


def _whole_tree_names(tree: ast.AST) -> set[str]:
    """**直接**绑定在整表加载结果上的变量名（如 `en_table = _load_locale("en")`）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _is_whole_tree_load(node.value)
        ):
            names.add(node.targets[0].id)

    changed = True
    while changed:  # 传递闭包：`a = en_table`
        changed = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Name)
                and node.value.id in names
                and node.targets[0].id not in names
            ):
                names.add(node.targets[0].id)
                changed = True
    return names


def _contains_whole_tree(node: ast.AST, names: set[str]) -> bool:
    """节点（含后代）里是否出现 dashboard **整树**取法；显式过滤的推导式**不算**。"""
    if isinstance(node, _FILTERED_NODES):
        return False  # `{k for k in _flatten(...) if ...}` = 命名空间级过滤
    if _is_whole_tree_load(node) or (isinstance(node, ast.Name) and node.id in names):
        return True
    return any(_contains_whole_tree(child, names) for child in ast.iter_child_nodes(node))


def _whole_tree_offenders(source: str, filename: str) -> list[str]:
    """返回该源码里「dashboard 整树相等/不等断言」的违规清单（`filename:行:说明`）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # 解析失败 = 判据不可判 ⇒ 报告出来，不静默放过
        return [f"{filename}: 无法解析（{exc}）"]

    names = _whole_tree_names(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            both = sum(_contains_whole_tree(op, names) for op in operands) >= 2
            if both and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                out.append(
                    f"{filename}:{node.lineno}: 整树相等/不等断言"
                    "（比较两侧都是 dashboard 全树取法 ⇒ 恒红门）"
                )
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and _contains_whole_tree(node.operand, names)
        ):
            out.append(f"{filename}:{node.lineno}: 整树断言的否定式（not <全树取法>）")
    return out


def test_no_whole_tree_equality_assertion_in_this_directory() -> None:
    """本目录**不得**出现 dashboard 整树相等断言（序号 2 的唯一硬禁令）。"""
    scanned = sorted(Path(__file__).resolve().parent.glob("*.py"))
    assert len(scanned) >= 10, f"只扫到 {len(scanned)} 个测试文件 —— 扫描面不足，不计证据"

    offenders: list[str] = []
    for path in scanned:
        offenders += _whole_tree_offenders(path.read_text(encoding="utf-8"), path.name)

    assert not offenders, (
        "tests/unit/i18n/ 出现 dashboard **整树相等**断言（今日必红 = 恒红门）：\n  "
        + "\n  ".join(offenders)
        + '\n应改成**命名空间收敛**（如 `_namespace_only_keys(..., "apiErrors")`）。'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 判别性对照（**只吃 `tmp_path` 临时副本**；真实 locale 文件只读）
# ─────────────────────────────────────────────────────────────────────────────


def _tampered_tables() -> dict[str, dict[str, Any]]:
    """真实文件的**内存副本**（`json` 往返）—— 篡改只发生在副本上。"""
    return {
        "en": json.loads(json.dumps(_load_locale("en"))),
        "zh": json.loads(json.dumps(_load_locale("zh"))),
    }


def _copies_on_disk(tmp_path: Path, tables: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """把（已篡改的）表落盘到 `tmp_path` 再读回 —— 断言吃的是**磁盘上的临时副本**。"""
    reloaded: dict[str, dict[str, Any]] = {}
    for locale, table in tables.items():
        path = tmp_path / f"{locale}.json"
        path.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
        reloaded[locale] = json.loads(path.read_text(encoding="utf-8"))
    return reloaded


def test_convergence_check_catches_injected_en_only_key(tmp_path: Path) -> None:
    """en 的 `apiErrors` 多一个 zh 没有的键 ⇒ 收敛断言**必须**返红并报出该键。"""
    tables = _tampered_tables()
    key = "apiErrors.P116_INJECTED_EN_ONLY"
    tables["en"]["apiErrors"]["P116_INJECTED_EN_ONLY"] = "injected"
    copies = _copies_on_disk(tmp_path, tables)

    only_en, only_zh = _namespace_only_keys(copies["en"], copies["zh"], _API_ERRORS)

    assert only_en == [key], f"注入的键没被抓到：only_en={only_en}"
    assert not only_zh


def test_convergence_check_is_namespace_scoped(tmp_path: Path) -> None:
    """漂移注入到**别的**命名空间 ⇒ `apiErrors` 收敛断言**必须仍然绿**。

    这就是「按命名空间收敛」与「整树相等」的判别性差别：同一份被污染的树，
    整树口径会红，命名空间口径不该红。
    """
    tables = _tampered_tables()
    tables["en"]["chat"]["P116_INJECTED_OTHER_NAMESPACE"] = "injected"
    copies = _copies_on_disk(tmp_path, tables)

    only_en, only_zh = _namespace_only_keys(copies["en"], copies["zh"], _API_ERRORS)

    assert not only_en and not only_zh, _convergence_message(_API_ERRORS, only_en, only_zh)
    # ★ 反向确认注入**真的**生效了（否则本用例只是空转）
    drifted = _namespace_only_keys(copies["en"], copies["zh"], "chat")[0]
    assert drifted == ["chat.P116_INJECTED_OTHER_NAMESPACE"]


def test_whole_tree_scanner_flags_injected_offenders(tmp_path: Path) -> None:
    """把「整树相等断言」写进 `tmp_path` 的 `.py` ⇒ 扫描**必须**报出违规行号。

    覆盖 3 种写法（直接取法 / 变量绑定 / 差集否定式）——`not (A - B)` 等价于
    「整树相等」，是最容易被写出来的那一种。
    """
    cases = {
        "direct": (
            "from test_locale_drift import _flatten, _load_locale\n"
            "def test_x():\n"
            "    assert _flatten(_load_locale('en')) == _flatten(_load_locale('zh'))\n",
            "整树相等/不等断言",
        ),
        "bound": (
            "from test_locale_drift import _flatten, _load_locale\n"
            "def test_x():\n"
            "    en = _load_locale('en')\n"
            "    zh = _load_locale('zh')\n"
            "    assert not (_flatten(en) - _flatten(zh))\n",
            "否定式",
        ),
        "comprehension_is_allowed": (
            "from test_locale_drift import _flatten, _load_locale\n"
            "def test_x():\n"
            "    en = {k for k in _flatten(_load_locale('en')) if k.startswith('apiErrors.')}\n"
            "    zh = {k for k in _flatten(_load_locale('zh')) if k.startswith('apiErrors.')}\n"
            "    assert not (en - zh)\n",
            "",  # 显式过滤 ⇒ **不得**报
        ),
    }

    for name, (source, expected) in cases.items():
        path = tmp_path / f"test_p116_{name}.py"
        path.write_text(source, encoding="utf-8")
        offenders = _whole_tree_offenders(path.read_text(encoding="utf-8"), path.name)
        if expected:
            assert offenders, f"注入的整树断言（{name}）没被抓到"
            assert any(expected in line for line in offenders), offenders
        else:
            assert not offenders, f"命名空间级推导式被误报：{offenders}"
