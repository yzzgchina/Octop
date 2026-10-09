"""P3-2 · ``I18N-GLOBAL-DIFF``：dashboard ``en``/``zh`` 全局键集合差分的口径 + 冻结白名单.

────────────────────────────────────────────────────────────────────────────────
一、判据（本卡「二选一」取 ②；依据是**仓内既有约定**，不是本卡新发明的口径）
────────────────────────────────────────────────────────────────────────────────
* 源卡 `team/2026-09-30-033500/TASKS.json · I18N-GLOBAL-DIFF` 实测：全局键树
  `en_only = 9` / `zh_only = 128`（**递归叶子点路径**计数）⇒「全局 en/zh 键树 EQUAL」
  这条判据在本仓**不成立**。本文件交付后为 `en_only = 0` / `zh_only = 128`。
* 该结论已被仓内既有约定采纳（本卡实读，非推测）：
    - `team/2026-09-30-033500/SPEC.md · A-8`：全局 EQUAL 判据**作废**，作用域必须显式声明；
    - `team/2026-09-30-033500/DECISIONS.md · D8`（2026-09-30）：同上，留痕；
    - `tests/unit/i18n/test_errors.py:512` 只断言**顶层** 82 个命名空间与 `chat` 211 键，
      子命名空间级**没有**任何 parity 断言。
* 口径落定为**「以 zh 为准 + 白名单逐条列出」**：
    (1) `zh ⊇ en`（方向性）：`en` 有的键 `zh` 必须都有 ⇒ `en_only == ∅`。
        违反它 = **zh 用户看到英文**。依据：`dashboard/src/i18n.ts` 的 `fallbackLng`
        是**另一个** locale（zh ⇄ en），所以一个只在 `en` 里的键，zh 用户拿到的是英文字符串；
        `t(key, "中文默认值")` 的第二参数**不会**生效（`workspace.saveFailed` 就是实例：
        代码里写了中文 defaultValue，但 `en` 有该键 ⇒ 先命中 en 回退，拿到英文）。
        本卡已把历史上仅剩的 9 个此类键补进 `zh.json`（见 `_EN_ONLY_FIXED_IN_ZH`）。
    (2) `en` 允许缺少**恰好这 128 个** `zh` 键 ⇒ 逐条白名单 `_ZH_ONLY_WHITELIST`。
        即「全局等值不作判据，但全局差分必须逐条冻结」：任何新增/删除/改名都会让下面的
        等式不成立，并**按键名报出**该补译还是该登记。

────────────────────────────────────────────────────────────────────────────────
二、为什么是「键集合差分」而不是「条数比较」（★ 本 run 的硬教训）
────────────────────────────────────────────────────────────────────────────────
`len(en) == len(zh)` 是**恒真门**：双语同删一个键、单侧改名、或同一 locale 里
「删一个 + 加一个」的计数守恒交换 —— 条数都不变，断言仍然绿。`EVIDENCE-P3-40.md`
已实测复现（双语同删一个键时裸 `len == len` 仍 `passed`）。
本文件比较的是**扁平点路径集合**，因此：
    * 单侧改名（条数不变）⇒ **红** —— 旧条数写法结构性看不见，见 `EVIDENCE-P3-2.md` §4
    * 计数守恒交换（条数不变）⇒ **红**
    * 单侧新增 / 单侧删除 ⇒ **红**
    * **双侧**同步改名 / **双侧**同步新增 ⇒ **绿**（这不是 parity 破坏，刻意如此）
      语义与 `P3-40`/`P3-41` 一致：冻结清单表达的是「**允许的单侧差异**」，
      而不是「冻结全部键名」——所以「加键零维护」与「改名可见」可以同时成立。

────────────────────────────────────────────────────────────────────────────────
三、维护
────────────────────────────────────────────────────────────────────────────────
* **补译**白名单里的键（把 `en` 补上）⇒ 本文件**会红**；把补好的键从
  `_ZH_ONLY_WHITELIST` 删掉即可 —— 清单必须随补译收缩，否则它就是在说谎。
* 重新生成清单（唯一出处；务必用同一条命令复算）::

      python3 - <<'PY'
      import json
      def flat(o, p=""):
          if isinstance(o, dict):
              if not o:
                  return {p} if p else set()
              s = set()
              for k, v in o.items():
                  s |= flat(v, f"{p}.{k}" if p else k)
              return s
          return {p}
      base = "dashboard/src/locales"
      en = flat(json.load(open(f"{base}/en.json")))
      zh = flat(json.load(open(f"{base}/zh.json")))
      print("en_only =", len(en - zh), sorted(en - zh))
      print("zh_only =", len(zh - en), sorted(zh - en))
      PY
"""

from __future__ import annotations

import json
from pathlib import Path

_LOCALES = Path(__file__).resolve().parents[3] / "dashboard" / "src" / "locales"


def _flat_paths(node: object, prefix: str = "") -> set[str]:
    """把 JSON 树摊平成「点路径叶子」集合。

    空对象**也**留下一条路径（否则 `{}` 会从集合里整个消失，成为盲区）；
    「对象 → 标量」的类型变化同样会改变这个集合。这一条正是它能看见
    「条数不变」的改名 / 计数守恒交换的原因。
    """
    if isinstance(node, dict):
        if not node:
            return {prefix} if prefix else set()
        out: set[str] = set()
        for key, value in node.items():
            out |= _flat_paths(value, f"{prefix}.{key}" if prefix else key)
        return out
    return {prefix}


def _load(locale: str) -> dict:
    return json.loads((_LOCALES / f"{locale}.json").read_text(encoding="utf-8"))


# 本卡补进 ``zh.json`` 的 9 个键（历史基线 ``en_only = 9``）：**逐键断言值**，
# 理由同 ``test_errors.py`` 的既有惯例 —— 只比键集合看不出「值悄悄退回英文」。
_EN_ONLY_FIXED_IN_ZH: dict[str, str] = {
    "cronJobs.deleteFailed": "删除失败",
    "embedding.done": "已完成",
    "experts.removeMapping": "移除",
    "memory.vs.done": "就绪",
    "models.deleteProviderFailedSimple": "删除失败",
    "observability.saveFailed": "保存设置失败",
    "remoteBrowser.reload": "重新加载",
    "voice.browserOnly": "由浏览器处理",
    "workspace.saveFailed": "保存失败",
}

# 「en 允许缺少」的 128 个 zh 键（逐条白名单）。**人工冻结物**，不现算（现算 = 恒真）。
# 改动方向只有两种：补译 ⇒ 收缩；新漂移 ⇒ 扩张 + 登记。
_ZH_ONLY_WHITELIST: frozenset[str] = frozenset(
    {
        "channels.dmPolicyDisabled",
        "cronJobs.col.enabled",
        "experts.agentDisable",
        "memory.atomDetail",
        "memory.candidates.confirmPromote",
        "memory.candidates.confirmReject",
        "memory.candidates.detail",
        "memory.candidates.empty",
        "memory.candidates.kindLabel",
        "memory.candidates.promote",
        "memory.candidates.promoteOk",
        "memory.candidates.reject",
        "memory.candidates.rejectHint",
        "memory.candidates.rejectOk",
        "memory.candidates.rejectReasonPlaceholder",
        "memory.candidates.rejectTitle",
        "memory.candidates.statusLabel",
        "memory.entityType.concept",
        "memory.entityType.event",
        "memory.entityType.organization",
        "memory.entityType.other",
        "memory.entityType.person",
        "memory.entityType.place",
        "memory.entityType.project",
        "memory.entityType.tool",
        "memory.episodeDetail",
        "memory.episodes.disclaimer",
        "memory.importance.high",
        "memory.importance.low",
        "memory.importance.medium",
        "memory.importance.title",
        "memory.journal.action.create",
        "memory.journal.action.deprecate",
        "memory.journal.action.merge",
        "memory.journal.action.promote",
        "memory.journal.action.reject",
        "memory.journal.action.update",
        "memory.kind.conflict",
        "memory.kind.decision",
        "memory.kind.fact",
        "memory.kind.preference",
        "memory.kind.task",
        "memory.library.hintAtoms",
        "memory.library.hintTree",
        "memory.library.viewAtoms",
        "memory.library.viewTree",
        "memory.list.importanceMin",
        "memory.list.kind",
        "memory.migrate.alreadyAdopted",
        "memory.migrate.alreadyAdoptedAt",
        "memory.migrate.buttonLabel",
        "memory.migrate.chooseFile",
        "memory.migrate.comparePkgLabel",
        "memory.migrate.confirmDesc",
        "memory.migrate.confirmImport",
        "memory.migrate.conflictLabel",
        "memory.migrate.conflictReplace",
        "memory.migrate.conflictSkip",
        "memory.migrate.doctorAllPassed",
        "memory.migrate.doctorDesc",
        "memory.migrate.doctorFailed",
        "memory.migrate.doctorLabel",
        "memory.migrate.doctorTitle",
        "memory.migrate.dryRunResult",
        "memory.migrate.exportBtn",
        "memory.migrate.exportDesc",
        "memory.migrate.exportLabel",
        "memory.migrate.exportSuccess",
        "memory.migrate.exportTitle",
        "memory.migrate.hostRewriteKeep",
        "memory.migrate.hostRewriteLabel",
        "memory.migrate.hostRewriteTarget",
        "memory.migrate.importBtn",
        "memory.migrate.importDone",
        "memory.migrate.importLabel",
        "memory.migrate.importStats",
        "memory.migrate.importSuccess",
        "memory.migrate.importTitle",
        "memory.migrate.previewBtn",
        "memory.migrate.rowCountDiff",
        "memory.migrate.runDoctor",
        "memory.migrate.selectFile",
        "memory.migrate.selectFileLabel",
        "memory.migrate.targetHostLabel",
        "memory.migrate.targetNs",
        "memory.migrate.willApply",
        "memory.overview.dirtyPages",
        "memory.overview.dirtyPagesClean",
        "memory.overview.dirtyPagesHint",
        "memory.overview.entitiesShort",
        "memory.overview.heroTitle",
        "memory.overview.indexedEntities",
        "memory.overview.indexedEntitiesHint",
        "memory.overview.memories",
        "memory.overview.pendingReview",
        "memory.overview.pendingReviewClean",
        "memory.overview.pendingReviewHint",
        "memory.overview.processingStatus",
        "memory.overview.profileEyebrow",
        "memory.overview.profileSubtitle",
        "memory.overview.statusEmpty",
        "memory.overview.subtitleRawEventsUnit",
        "memory.overview.weeklyNewAtoms",
        "memory.overview.weeklyNewAtomsHint",
        "memory.terminal.aboutMe",
        "memory.terminal.aboutMeDesc",
        "memory.terminal.atomCount",
        "memory.terminal.currentFocus",
        "memory.terminal.currentFocusDesc",
        "memory.terminal.emptyAboutMe",
        "memory.terminal.emptyEntities",
        "memory.terminal.emptyFocus",
        "memory.terminal.emptyToldMe",
        "memory.terminal.entities",
        "memory.terminal.entitiesDesc",
        "memory.terminal.toldMe",
        "memory.terminal.toldMeDesc",
        "scenes.currentSceneTab",
        "scenes.drawerApply",
        "scenes.drawerConfigFiles",
        "scenes.drawerSkillFiles",
        "scenes.moreScenesComing",
        "scenes.moreScenesSub",
        "scenes.noSceneApplied",
        "scenes.noSceneAppliedHint",
        "scenes.sceneSelectTab",
        "scenes.yourConfiguration",
        "wizard.steps.finish",
    }
)


def test_en_key_set_is_a_subset_of_zh_after_backfilling_the_nine():
    """`zh ⊇ en`：方向性判据（不成立时 zh 用户会看到英文）。"""
    en_keys, zh_keys = _flat_paths(_load("en")), _flat_paths(_load("zh"))
    en_only = sorted(en_keys - zh_keys)
    assert en_only == [], (
        f"en 有而 zh 没有的键 ⇒ zh 用户会看到英文（fallbackLng 是另一个 locale）: {en_only}"
    )
    zh = _load("zh")
    for key, expected in _EN_ONLY_FIXED_IN_ZH.items():
        node: object = zh
        for part in key.split("."):
            assert isinstance(node, dict) and part in node, f"zh 缺键 {key}"
            node = node[part]
        assert node == expected, f"zh {key} = {node!r}，期望 {expected!r}"


def test_global_key_tree_diff_matches_the_frozen_whitelist():
    """全局键集合差分 == 冻结白名单（双向，按键名报出）。"""
    en_keys, zh_keys = _flat_paths(_load("en")), _flat_paths(_load("zh"))
    zh_only = zh_keys - en_keys
    newly_diverged = sorted(zh_only - _ZH_ONLY_WHITELIST)
    now_paired = sorted(_ZH_ONLY_WHITELIST - zh_only)
    assert zh_only == _ZH_ONLY_WHITELIST, (
        "dashboard en/zh 全局键集合差分已漂移（白名单是人工冻结物，见模块 docstring §三）：\n"
        f"  新只有 zh 的键 {len(newly_diverged)} 个（补 en 或登记进白名单）: {newly_diverged}\n"
        f"  白名单里已不再只有 zh 的键 {len(now_paired)} 个"
        f"（已配对、或已删除/改名 ⇒ 从白名单删掉）: {now_paired}"
    )
    # 冻结表**自检**（对象是测试内的字面量、不随产品数据增长）：防止清单被截断。
    assert len(_ZH_ONLY_WHITELIST) == 128
