"""★ `AUD-C1` `locale.toplevel_unique`（`P1-2` 序号 1）。

★ 输入 = `dashboard/src/locales/{en,zh}.json`（★ 逐字文件清单 = `registry.LOCALE_FILES`）。
★ 手法 = **原文级**取顶层键序列（`json.loads(..., object_pairs_hook=...)`）——★ `dict` 会**静默丢掉**
  重复顶层键（追加到文件末尾造第二个 `chat` 块 ⇒ 既有键被整块覆盖），故不用 `dict` 判。
★ 返红条件：两文件任一出现**重复顶层键**（任意键）⇒ 红。
★ 红线 1（三元组）：`message` 逐字给出「实测量 / 文档声称 / 应改成什么」。
"""

import json
import re

from .. import registry as reg

TOPLEVEL_RE = re.compile(r'^  "((?:[^"\\]|\\.)*)":')


def _top_pairs(text):
    """★ 原文级顶层键序列（★ 保序 ∧ 保重复）；★ 解析失败 ⇒ 抛 `ValueError`。"""
    return json.loads(text, object_pairs_hook=lambda pairs: pairs)


def _dup_lines(lines, key):
    """★ 该顶层键在原文里的行号清单（★ 只认 2 空格缩进的顶层键）。"""
    hits = []
    for line_no, text in enumerate(lines, 1):
        match = TOPLEVEL_RE.match(text)
        if match and match.group(1) == key:
            hits.append(line_no)
    return hits


def _locale_file(relname, text, lines):
    top = _top_pairs(text)
    if not isinstance(top, list):
        return None
    keys = [item[0] for item in top]
    dups = sorted({key for key in keys if keys.count(key) > 1})
    return {
        "keys": len(keys),
        "dups": dups,
        "namespace_count": keys.count(reg.LOCALE_NAMESPACE),
        "lines": {key: _dup_lines(lines, key) for key in dups},
    }


@reg.criterion("AUD-C1", reg.HARD)
def check_locale_toplevel(ctx):
    """★ 顶层命名空间必须唯一（★ 追加到文件末尾 ⇒ 第二个同名块 ⇒ 既有键静默丢失）。"""
    files, facts, scanned = [], {}, []
    for relname in reg.LOCALE_FILES:
        path = ctx.repo / relname
        if not path.is_file():
            return reg.Result(
                "AUD-C1",
                reg.HARD,
                reg.UNKNOWN,
                message=f"判据面文件读不到：{relname} ⇒ 不得记绿（基准目录 = {ctx.repo}）",
                files=files,
            )
        try:
            text = reg.read_source(path)
            lines = reg.read_lines(path)
        except (OSError, UnicodeDecodeError) as exc:
            return reg.Result(
                "AUD-C1",
                reg.HARD,
                reg.UNKNOWN,
                message=f"读不到 {relname}（{exc}）⇒ 不得记绿",
                files=files,
            )
        try:
            item = _locale_file(relname, text, lines)
        except ValueError as exc:
            return reg.Result(
                "AUD-C1",
                reg.HARD,
                reg.UNKNOWN,
                message=f"{relname} 不是可解析 JSON（{exc}）⇒ 读到不认识的格式 ⇒ 不得记绿",
                files=files,
            )
        if item is None:
            return reg.Result(
                "AUD-C1",
                reg.HARD,
                reg.UNKNOWN,
                message=f"{relname} 顶层不是对象 ⇒ 不得记绿",
                files=files,
            )
        files.append(relname)
        facts[relname] = item
        scanned.append(relname)
    if not scanned:
        return reg.Result(
            "AUD-C1",
            reg.HARD,
            reg.UNKNOWN,
            message="判据面为空（0 个文件）⇒ 空跑，不计证据",
            files=files,
        )
    dup_text = []
    for relname, item in sorted(facts.items()):
        for key in item["dups"]:
            dup_text.append(
                f"{relname}:{'/'.join(str(n) for n in item['lines'][key])} 顶层键 {key!r} "
                f"×{len(item['lines'][key])}"
            )
    namespace_text = " · ".join(
        f"{name} 的 {reg.LOCALE_NAMESPACE!r} 出现 {item['namespace_count']} 次"
        for name, item in sorted(facts.items())
    )
    keys_text = " · ".join(
        f"{name} 顶层键 {item['keys']} 个" for name, item in sorted(facts.items())
    )
    checked = len(scanned)
    if dup_text:
        return reg.Result(
            "AUD-C1",
            reg.HARD,
            reg.FAIL,
            checked=checked,
            message=f"实测量：重复顶层键 {len(dup_text)} 处（{' · '.join(dup_text)}）；"
            f"文档声称：{' · '.join(reg.LOCALE_FILES)} 顶层命名空间唯一 ⇒ 可安全在文件末尾追加新键；"
            f"应改成：把键并入**既有命名空间内部**，并删掉重复的同名顶层块（= {keys_text}）",
            files=files,
        )
    return reg.Result(
        "AUD-C1",
        reg.HARD,
        reg.OK,
        checked=checked,
        message=f"实测量：{keys_text}，重复顶层键 0 处（{namespace_text}）；文档声称：可安全在文件末尾追加新顶层键；"
        "应改成：新键必须落到**既有命名空间内部**（★ 文件末尾追加同名顶层块会静默丢掉既有键）",
        files=files,
    )
