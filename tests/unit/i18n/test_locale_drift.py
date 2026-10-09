"""`G-1` · 前端 `dashboard/src/locales/{en,zh}.json` **键树漂移守卫**（基线差分口径）。

## 为什么**不是**「整树相等」断言
`CRITERIA-29-CLASSIFICATION.md` `§1` 分类表**序号 2** 逐字：dashboard 的 en/zh
**全树键树本就不等** ⇒ **禁止整树相等断言**（否则必然假红）。
实测（`fix/audit-directions-3to6`）：en 展平 **5928** 键 / zh **6047** 键
⇒ 仅 en **9** · 仅 zh **128**。整树相等断言**今天就是红的** = **恒红门**（本项目禁）。

## 口径：**已知漂移基线差分**（新增才红）
`locale_drift_baseline.json` 冻结**今天已经存在**的漂移键（en_only 9 / zh_only 128，
按命名空间分组，见 `G-1` 的命名空间分布）。守卫只判：

    当天漂移 − 基线漂移 ≠ ∅  ⇒ 红

即「**新增**漂移才红」：修复**历史**漂移**不返红**（不必同步基线）——这是刻意的取舍，
避免与并行的 locale 修复工作线（如补齐 `memory` 的 114 条）互相卡住；但**任何一侧
新出现的键**都会返红。

## 为什么今天为绿 / 为什么对漂移会红
- **绿**：基线是**一次性**从真实文件生成的**已提交字面量**（不是运行时自算 ——
  运行时自算就是**恒绿门**）；基线 ⊇ 当天漂移 ⇒ 差集为空。
- **红**：注入「en 加一个 zh 没有的键」⇒ 该键进入 `en_only` 且不在基线里 ⇒ 差集非空。
  判别性对照见 `team/2026-10-08-110000/EVIDENCE-P1-6.md`（在**临时副本**里注入，
  不污染真实 locale）。

## 刷新基线（仅当有意收编新漂移或收编修复时）
```bash
uv run python tests/unit/i18n/test_locale_drift.py    # 重写 locale_drift_baseline.json
```
★ 刷新是**人工决定**，守卫**从不**自动写入基线。

## 已知边界（诚实登记）
「新增才红」的代价：若某键**先被修好**、此后**又被改坏**，而基线里仍留着它 ⇒
**不返红**。刷新基线可消除该残留。该取舍换来的是与并行 locale 修复线**零摩擦**。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_LOCALES_DIR = Path(__file__).resolve().parents[3] / "dashboard" / "src" / "locales"
_BASELINE_PATH = Path(__file__).resolve().parent / "locale_drift_baseline.json"


# ─────────────────────────────────────────────────────────────────────────────
# 展平与漂移计算（纯函数：同一套逻辑既用于真实文件，也用于判别性对照的临时副本）
# ─────────────────────────────────────────────────────────────────────────────


def _flatten(node: Any, prefix: str = "") -> set[str]:
    """把 i18next JSON 展平成点分键集合；数组/标量视为叶子。"""
    if not isinstance(node, dict):
        return {prefix} if prefix else set()
    keys: set[str] = set()
    for name, value in node.items():
        keys |= _flatten(value, f"{prefix}.{name}" if prefix else name)
    return keys


def _namespace(key: str) -> str:
    return key.split(".", 1)[0]


def _load_locale(locale: str) -> dict[str, Any]:
    path = _LOCALES_DIR / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _drift(locale: str) -> dict[str, set[str]]:
    """`locale` **独有**（对侧没有）的展平键，按命名空间分组。"""
    other = "zh" if locale == "en" else "en"
    diff = _flatten(_load_locale(locale)) - _flatten(_load_locale(other))
    grouped: dict[str, set[str]] = {}
    for key in diff:
        grouped.setdefault(_namespace(key), set()).add(key)
    return grouped


def _baseline() -> dict[str, Any]:
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def _new_drift(locale: str) -> dict[str, list[str]]:
    """当天漂移 − 基线漂移（= **新增**漂移），按命名空间分组。"""
    known = _baseline()[f"{locale}_only"]
    fresh: dict[str, list[str]] = {}
    for namespace, keys in _drift(locale).items():
        added = sorted(keys - set(known.get(namespace, [])))
        if added:
            fresh[namespace] = added
    return fresh


def _message(locale: str, new: dict[str, list[str]]) -> str:
    other = "zh" if locale == "en" else "en"
    lines = [
        f"前端 locale 键树出现**新增**漂移：{locale}.json 独有（{other}.json 缺失）",
        "",
    ]
    for namespace in sorted(new):
        lines.append(f"  命名空间 {namespace}（+{len(new[namespace])}）")
        lines += [f"    {key}" for key in new[namespace]]
    lines += [
        "",
        f"实际：以上键只在 {locale}.json 里 ⇒ 对侧会回退英文 / 显示原始 key。",
        "期望：新增键必须**同时**落在 en.json 与 zh.json（AGENTS.md §7 i18n 清单）。",
        f"处置：补齐 {other}.json；若确属有意漂移，用 "
        "`uv run python tests/unit/i18n/test_locale_drift.py` 收编进基线。",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 两个面各自独立（一个 locale 一个用例：哪一面坏了只看得到那一条）
# ─────────────────────────────────────────────────────────────────────────────


def test_no_new_english_only_keys() -> None:
    """en 独有键**新增** ⇒ 红。"""
    new = _new_drift("en")
    assert not new, _message("en", new)


def test_no_new_chinese_only_keys() -> None:
    """zh 独有键**新增** ⇒ 红。"""
    new = _new_drift("zh")
    assert not new, _message("zh", new)


def test_baseline_namespaces_match_their_keys() -> None:
    """基线必须自洽（键的前缀 = 它所在的分组），防手工编辑写错桶。"""
    baseline = _baseline()
    for locale in ("en", "zh"):
        for namespace, keys in baseline[f"{locale}_only"].items():
            mismatched = [key for key in keys if _namespace(key) != namespace]
            assert not mismatched, f"基线 {locale}_only[{namespace}] 里的键与分组不符：{mismatched}"


# ─────────────────────────────────────────────────────────────────────────────
# 基线刷新入口（**仅**人工执行；守卫从不自动写基线）
# ─────────────────────────────────────────────────────────────────────────────


def _refresh() -> None:
    payload = {
        "schema": 1,
        "source": "dashboard/src/locales/{en,zh}.json",
        "note": (
            "G-1 已知漂移基线（冻结的既有漂移键，按命名空间分组）。"
            "守卫判「当天漂移 − 基线漂移 ≠ ∅」⇒ 新增才红。"
        ),
        "en_only": {ns: sorted(keys) for ns, keys in sorted(_drift("en").items())},
        "zh_only": {ns: sorted(keys) for ns, keys in sorted(_drift("zh").items())},
    }
    payload["counts"] = {
        "en_only": sum(len(v) for v in payload["en_only"].values()),
        "zh_only": sum(len(v) for v in payload["zh_only"].values()),
    }
    _BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":  # pragma: no cover - 人工刷新基线
    _refresh()
