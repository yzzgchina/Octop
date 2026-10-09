import en from "./en.json";
import zh from "./zh.json";

/**
 * P3-2 · `I18N-GLOBAL-DIFF` —— dashboard 双语 bundle 的**方向性** parity 守卫。
 *
 * 判据只取一半，而且这一半**不需要冻结清单**（零维护）：
 *
 *     flatPaths(en) ⊆ flatPaths(zh)        // zh 是超集
 *
 * 为什么只查这个方向：本仓的全局键树**本就不等值**（`zh` 比 `en` 多 128 个键，
 * 逐条白名单在 `tests/unit/i18n/test_locale_global_diff.py`），所以「全局相等」不是本仓
 * 的判据（依据 `team/2026-09-30-033500/SPEC.md · A-8` 与 `DECISIONS.md · D8`）。
 * 而 `zh ⊇ en` 是**真契约**：`src/i18n.ts` 的 `fallbackLng` 是另一个 locale，
 * 一个只出现在 `en` 里的键会让 zh 用户看到英文字符串（`t(key, "中文默认值")` 的
 * 第二参数救不了它）。
 *
 * 为什么不比条数：`Object.keys(en).length === Object.keys(zh).length` 是恒真门 ——
 * 单侧改名、或「删一个 + 加一个」的计数守恒交换都不改条数，却破坏了 parity。
 * 这里比的是**扁平点路径集合**，所以单侧改名（条数不变）也会红；
 * 第二个用例是本判据的**阴性对照**（证明它抓得住"改了名"而不是只数个数）。
 */
function flatPaths(node: unknown, prefix = ""): Set<string> {
  if (node !== null && typeof node === "object" && !Array.isArray(node)) {
    const entries = Object.entries(node as Record<string, unknown>);
    if (entries.length === 0) return prefix ? new Set([prefix]) : new Set();
    const out = new Set<string>();
    for (const [key, value] of entries) {
      for (const path of flatPaths(value, prefix ? `${prefix}.${key}` : key)) {
        out.add(path);
      }
    }
    return out;
  }
  return new Set([prefix]);
}

describe("dashboard locale key-set parity (P3-2 / I18N-GLOBAL-DIFF)", () => {
  it("every `en` key also exists in `zh` (zh is the superset)", () => {
    const zhKeys = flatPaths(zh);
    const enOnly = [...flatPaths(en)].filter((key) => !zhKeys.has(key)).sort();
    expect(enOnly).toEqual([]);
  });

  it("is a key-set comparison: a count-preserving rename is still caught", () => {
    // 阴性对照：两棵树**键数相同**，只是一个键改了名。
    const before = flatPaths({ memory: { promote: "采纳", reject: "忽略" } });
    const after = flatPaths({ memory: { promoteX: "采纳", reject: "忽略" } });
    expect(after.size).toBe(before.size); // ← 裸条数比较在这里恒绿
    const onlyAfter = [...after].filter((key) => !before.has(key)).sort();
    expect(onlyAfter).toEqual(["memory.promoteX"]);
  });
});
