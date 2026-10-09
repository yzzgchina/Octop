/**
 * `P1-16` · 判据面 **B 类 序号 16** —— 共享 i18n mock 的 `t` **引用稳定性**。
 *
 * ## 被断对象
 * `dashboard/src/test/setup.ts` 的 `vi.mock("react-i18next")` 里，
 * `useTranslation: () => ({ t: (…) => … })` **每次调用都新建箭头函数**
 * ⇒ 每次渲染拿到**新的 `t` 引用**。
 *
 * ## 为什么这是真缺口（不是洁癖）
 * 调用方普遍写 `const load = useCallback(async () => …, [projectId, t]);`
 * （见 `pages/Projects/ProjectsPage.tsx` 与 `pages/Projects/Detail/ProjectMemoryTab.tsx`，
 * 两处测试的注释都点到过这个坑）。`t` 引用每渲染必变 ⇒ `load` 每渲染必变 ⇒
 * 依赖 `load` 的 `useEffect` 每渲染重取 ⇒ 若该 effect 又写 state，就是**无限重取**。
 * 生产环境不会这样（真 i18next 的 `t` 在语言不变时是稳定引用），所以**只有测试会中招**
 * ⇒ 组件在测试里表现出的行为与线上不同。
 *
 * ## 口径
 * 1. 同一组件**重渲染**两次 ⇒ `t` **必须是同一引用**（`toBe`，不是 `toEqual`）；
 * 2. 依赖 `[t]` 的 `useEffect` 跨重渲染**只跑一次**（这才是「无限重取」的直接可判形态）。
 *
 * ## 判别性对照（原始输出见 `team/2026-10-08-110000/EVIDENCE-P1-16.md`）
 * 本文件**不注入任何座位**：先在**未修的** `setup.ts` 下跑 ⇒ 两条必须返红（真实 setup.ts 的
 * 对照），再把 `t` 提升到 mock 工厂的模块作用域（单例）⇒ 转绿。
 */

import { render } from "@testing-library/react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { describe, expect, it } from "vitest";

/** 把每次渲染拿到的 `t` 记进 sink，供引用比对。 */
function TranslationProbe({ sink }: { sink: unknown[] }) {
  const { t } = useTranslation();
  sink.push(t);
  return null;
}

/** 镜像真实调用方的形态：`useEffect` 依赖 `t`（线上等价于 `useCallback(load, [t])`）。 */
function EffectProbe({ runs }: { runs: unknown[] }) {
  const { t } = useTranslation();
  useEffect(() => {
    runs.push(t);
  }, [t]);
  return null;
}

describe("P1-16 · 共享 i18n mock 的 t 引用稳定性", () => {
  it("重渲染后 `t` 仍是同一引用（不是新箭头函数）", () => {
    const sink: unknown[] = [];
    const { rerender } = render(<TranslationProbe sink={sink} />);
    rerender(<TranslationProbe sink={sink} />);

    expect(sink).toHaveLength(2);
    expect(sink[1]).toBe(sink[0]);
  });

  it("依赖 `[t]` 的 effect 跨重渲染只跑一次（`t` 不稳 ⇒ 每次重渲染重取）", () => {
    const runs: unknown[] = [];
    const { rerender } = render(<EffectProbe runs={runs} />);
    rerender(<EffectProbe runs={runs} />);
    rerender(<EffectProbe runs={runs} />);

    expect(runs).toHaveLength(1);
  });
});
