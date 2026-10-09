"""`P1-16` · 判据面 **B 类 序号 3** —— **后端** `src/octop/i18n` 的 en/zh 键树**全树相等**。

## 为什么**只有后端**能判全树相等
`CRITERIA-29-CLASSIFICATION.md` `§1` 序号 3 逐字：**只有后端** `src/octop/i18n` 的
en/zh 键树**全等**，**dashboard 不全等**。
⇒ 后端：全树相等断言（本文件）；dashboard：**禁**整树相等（序号 2，见
`tests/unit/i18n/test_locale_namespaces.py`，那里还带一条**反恒红门扫描**）。
实测（本工作树 `1d3623ee`）：后端 en **733** 键 = zh **733** 键（双向差集为空）。

## 与现存断言的关系 = **合并**，不是第二份实现
- `test_desktop.py:8` 已用 `all_keys_for_locale` 判 `desktop.*` **前缀**；本文件把同一 API
  扩到**全树**（判据要求的「扩面」）。
- ★ 本工作树 `1d3623ee` 的 `test_errors.py` 里原有一份 `test_backend_locales_have_identical_key_trees`
  —— 它**自带第二份展平实现**（局部 `tree()`），判的是同一事实。为消除「同一事实两份拷贝」，
  该函数**已删除**，其判别力由本文件接管：`tree()` 相对 `flatten_keys` 多出来的那点力量
  （非字符串叶子）由 `test_backend_leaf_values_are_all_strings` 显式补上（今天 en/zh 各
  **733** 个叶子**全为字符串**，两个口径等价；若将来出现非字符串叶子，该用例会红并指向
  `flatten_keys` 的盲点）。
- 展平**复用** `octop.i18n.loader` 的 `flatten_keys`（`all_keys_for_locale` 的同一实现）。
  本文件**零**重复展平实现。

## 判据（逐字照序号 3 的返红条件）
`all_keys_for_locale("en") != all_keys_for_locale("zh")` ⇒ 红，并**逐键列出**双向差集。

## 判别性对照（原始输出见 `team/2026-10-08-110000/EVIDENCE-P1-16.md`）
从 zh 的**临时副本**删掉一个键（副本落 `tmp_path`，真实 bundle 只读），把这份副本喂给
**真的** `all_keys_for_locale`（monkeypatch `loader._load_all`）⇒ 必须返红并指名该键。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octop.i18n import all_keys_for_locale

# ★ 用**模块属性**取 `_load_all`（不是 `from ... import _load_all`）：后者把函数对象
#   绑定到本模块，monkeypatch 改 `loader._load_all` 时**不会**生效（探针实测踩到过）。
from octop.i18n import loader as i18n_loader


def _tree_gaps(en_keys: set[str], zh_keys: set[str]) -> tuple[list[str], list[str]]:
    """（仅 en 有, 仅 zh 有）—— 全树键树差分；喂给它的键集**只能**来自 `all_keys_for_locale`。"""
    return sorted(en_keys - zh_keys), sorted(zh_keys - en_keys)


def _message(only_en: list[str], only_zh: list[str]) -> str:
    lines = [
        "后端 locale `src/octop/i18n/{en,zh}.json` 的键树**不等**（本判据要求全等）：",
        "",
        f"  仅 en.json 有（{len(only_en)}）：{only_en[:20]}",
        f"  仅 zh.json 有（{len(only_zh)}）：{only_zh[:20]}",
        "",
        "实际：缺键的一侧在运行时**静默**回退英文（`loader.lookup` 的 fallback 分支）。",
        "期望：同一键必须同时落在 en.json 与 zh.json（AGENTS.md §7 i18n 清单第 1 条）。",
    ]
    return "\n".join(lines)


def test_backend_key_trees_are_identical() -> None:
    """后端 en/zh 键树**全树相等**（序号 3 的正向判据）。"""
    en_keys = all_keys_for_locale("en")
    zh_keys = all_keys_for_locale("zh")

    assert en_keys, "后端 locale 一个键都没读到 —— 本用例会变成空断言"
    only_en, only_zh = _tree_gaps(en_keys, zh_keys)
    assert not only_en and not only_zh, _message(only_en, only_zh)


def test_backend_leaf_values_are_all_strings() -> None:
    """★ `flatten_keys` **只收字符串叶子** —— 本用例钉住这个前提。

    （`P1-6` 的 `_flatten` 与之不同：它对非字典叶子一律记键。二者今天等价，仅因为
    en/zh 的 **733** 个叶子全是字符串。）若将来出现 list/int 叶子，上一条用例会**看不见**它，
    本用例会红 —— 这就是「两口径等价」的可判前提，不是文档承诺。
    """
    tables = i18n_loader._load_all()
    offenders: dict[str, list[str]] = {}
    for locale in ("en", "zh"):
        leaves = _leaves(tables[locale])
        assert leaves, f"{locale}.json 一个叶子都没读到 —— 本用例会变成空断言"
        bad = [path for path, value in leaves if not isinstance(value, str)]
        if bad:
            offenders[locale] = bad[:10]
    assert not offenders, (
        f"后端 locale 出现**非字符串**叶子：{offenders}\n"
        "⇒ `all_keys_for_locale`（= `flatten_keys`）会**跳过**这些键，"
        "全树相等判据对它们不可见；请改用能覆盖叶子类型的口径。"
    )


def _leaves(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """叶子的（点分路径, 值）清单 —— 只服务于上一条「前提」用例。"""
    if isinstance(node, dict):
        out: list[tuple[str, Any]] = []
        for name, value in node.items():
            out += _leaves(value, f"{prefix}.{name}" if prefix else name)
        return out
    return [(prefix, node)]


def test_tree_check_catches_an_injected_missing_zh_key(tmp_path: Path, monkeypatch: Any) -> None:
    """★ 判别性对照：zh 少一个键（**临时副本**）⇒ 全树判据**必须**返红并指名该键。

    喂进去的是**真的** `all_keys_for_locale`（只把 `loader._load_all` 指向临时副本），
    所以这里证明的是判据本身有判别力，而不是「副本能解析」。
    """
    tables = i18n_loader._load_all()
    tampered: dict[str, dict[str, Any]] = {
        "en": json.loads(json.dumps(tables["en"])),
        "zh": json.loads(json.dumps(tables["zh"])),
    }
    dropped = "errors.AGENT_NAME_TAKEN"
    del tampered["zh"]["errors"]["AGENT_NAME_TAKEN"]

    reloaded: dict[str, dict[str, Any]] = {}
    for locale, table in tampered.items():  # 篡改只落在 tmp_path 的副本上
        path = tmp_path / f"{locale}.json"
        path.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
        reloaded[locale] = json.loads(path.read_text(encoding="utf-8"))

    monkeypatch.setattr(i18n_loader, "_load_all", lambda: reloaded)

    only_en, only_zh = _tree_gaps(all_keys_for_locale("en"), all_keys_for_locale("zh"))

    assert only_en == [dropped], f"注入的缺键没被抓到：only_en={only_en}"
    assert not only_zh
