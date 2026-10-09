/**
 * P2-12 · `Detail/index.tsx` 的 `taskToValues()` 回填判别性取证（卡面 acceptance ④）。
 *
 * ★ 为什么这条必须走**真实页面装配**：`taskToValues()` 是 `Detail/index.tsx` 的
 * 模块私有函数，没有导出。若只断言「我传进弹窗的 `initialValues` 里有 owners」，
 * 那是测试自己构造的形状，**不能**证明页面那条读取路径真的把后端返回的
 * `owners` / `executors` 送进了弹窗。因此这里：
 *   ① 用真实路由渲染 `ProjectDetailPage`（`/projects/p1?tab=tasks`）；
 *   ② 只 mock **传输层** `api/request`（与 `ProjectsPage.test.tsx` 同款口径），
 *      让 `projectsApi.listTasks()` → `taskToValues()` → 弹窗链路全为真实代码；
 *   ③ 从任务列表点「编辑」打开弹窗，只改标题（主体**不动**）后提交；
 *   ④ 断言 PATCH 体**含** `owners: [7]` / `executors: ["agent:agent-a"]`。
 *
 * ★ 判别性：若 `taskToValues()` 不送回填，弹窗 baseline 的两数组就是 `[]`，
 * 提交时 `sameNumbers` 会判为「变了」⇒ PATCH 体变成 `[]`（或缺失）⇒ 本用例变红。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { ProjectOut, ProjectTask } from "../../../api/modules/projects";

vi.mock("../../../api/request", () => ({
  request: vi.fn(),
  requestBlob: vi.fn(),
  requestUpload: vi.fn(),
}));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 1, username: "tester", display_name: "Tester" }),
}));

vi.mock("../../../utils/antdMessage", () => ({
  message: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

/**
 * ``Detail/index.tsx`` 把 ``t`` 放进 ``useCallback(load, [projectId, t])`` 的依赖里；
 * 共享 setup 每次渲染都给**新的** ``t`` ⇒ 无限重取（既有测试文件已踩过）。
 * 这里按同一口径钉一个稳定 ``t``。
 */
vi.mock("react-i18next", () => {
  const t = (key: string, fallback?: unknown): string =>
    typeof fallback === "string" ? fallback : key;
  return {
    useTranslation: () => ({
      t,
      i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
    }),
    Trans: ({ children }: { children?: unknown }) => children,
  };
});

import { request } from "../../../api/request";
import ProjectDetailPage from "./index";

const mockedRequest = vi.mocked(request);

const PROJECT: ProjectOut = {
  project_id: "p1",
  name: "Apollo",
  goal: "Ship v2",
  status: "active",
  owner_user_id: 1,
  memory_namespace: "project-p1",
  kb_id: null,
  start_at: null,
  due_at: null,
  created_at: 1,
  updated_at: 1,
};

/** 后端回来的任务：双主体**都已落地**（镜像列同时存在，读侧两者并存）。 */
const TASK: ProjectTask = {
  task_id: "tsk_1",
  project_id: "p1",
  parent_id: null,
  title: "双主体任务",
  description: "原描述",
  status: "todo",
  assignee_type: "agent",
  assignee_id: "agent-a",
  owners: [7],
  executors: ["agent:agent-a"],
  priority: 1,
  deps: [],
  thread_id: null,
  origin_node_id: null,
  due_at: null,
  start_at: null,
  tags: [],
  custom_fields: [],
  attachments: [],
  sort_order: 0,
  created_by: 1,
  created_at: 1,
  updated_at: 1,
};

let routes: Record<string, () => unknown> = {};
const patches: Array<{ path: string; body: Record<string, unknown> }> = [];

beforeEach(() => {
  vi.clearAllMocks();
  // ★ 原地清空（**不要** `patches = []`）：下面的 mock 闭包捕获的是这个数组
  // 本身，重新赋值会让闭包继续往旧数组里推 ⇒ 断言永远看不到（实测踩过）。
  patches.length = 0;
  routes = {
    "GET /settings/timezone": () => ({ timezone: "UTC" }),
    "GET /projects/p1": () => PROJECT,
    "GET /projects/p1/members": () => [
      {
        subject_type: "user",
        subject_id: "1",
        user_id: 1,
        role: "owner",
        created_at: 1,
      },
      {
        subject_type: "user",
        subject_id: "7",
        user_id: 7,
        role: "member",
        created_at: 1,
      },
      // 可选主体：弹窗的 picker 从这里取（R5 单一成员源）。
      {
        subject_type: "agent",
        subject_id: "agent-a",
        user_id: null,
        role: "member",
        created_at: 1,
      },
      {
        subject_type: "team",
        subject_id: "backend",
        user_id: null,
        role: "member",
        created_at: 1,
      },
    ],
    "GET /projects/p1/tasks": () => [TASK],
    "GET /projects/p1/tags": () => [],
    "GET /projects/p1/custom-fields": () => [],
    "PATCH /projects/p1/tasks/tsk_1": () => ({ ...TASK, title: "改后的标题" }),
  };
  mockedRequest.mockImplementation(
    async (path: string, init?: RequestInit): Promise<unknown> => {
      const method = init?.method ?? "GET";
      const handler = routes[`${method} ${path}`];
      if (!handler) throw new Error(`unexpected request: ${method} ${path}`);
      if (method !== "GET" && typeof init?.body === "string") {
        patches.push({ path, body: JSON.parse(init.body) });
      }
      return handler();
    },
  );
});

describe("P2-12 · index.tsx taskToValues() 双主体回填（真实页面装配）", () => {
  /**
   * ★ 本用例装配的是**整页**（真实路由 + 三路并发读 + 两次开弹窗），全量并发下会
   * 超过 5s 默认预算 ⇒ 用第三个参数放宽本用例（不动全局 `testTimeout`）。
   */
  it("★ 后端返回 owners/executors ⇒ 编辑提交的 PATCH 体仍含原双主体（= 回填生效）", async () => {
    render(
      <MemoryRouter initialEntries={["/projects/p1?tab=tasks"]}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    // 任务列表里出现该任务，并用其行内「编辑」入口打开弹窗。
    const editButton = await screen.findByTestId("task-row-edit-tsk_1");
    const user = userEvent.setup();
    await user.click(editButton);

    const title = await screen.findByLabelText("projects.createTaskTitle");
    // 弹窗已按后端形状回填双主体。★ P2-6 重指向：`PLAN §CD ②` 把「人 + 智能体」合体
    // chip 拆成**两枚独立 chip** —— 人侧进「分配」、执行主体侧进「智能体」；摘要文本改走
    // **共享展示模型** `Board.subjectDisplayName`（本夹具的成员无 `name` ⇒ 兜底形态
    // `type:#末6位`，UI-SPEC §5.3 UIS-T-04）。断言的语义（两侧都被回填）逐字未变。
    const ownerChip = await screen.findByRole("button", {
      name: /^projects\.chipAssignee/,
    });
    expect(ownerChip.textContent).toContain("user:#7");
    const agentChip = await screen.findByRole("button", {
      name: /^projects\.chipAgent/,
    });
    expect(agentChip.textContent).toContain("agent:#gent-a");

    // 只改标题，主体**不动** ⇒ 未改动的主体侧按三态「缺字段 = 不改」**不该出现**。
    await user.clear(title);
    await user.type(title, "改后的标题");
    await user.click(
      screen.getByRole("button", { name: /projects\.editTaskSubmit/ }),
    );

    await waitFor(() => expect(patches.length).toBeGreaterThan(0));
    const untouched = patches[0];
    expect(untouched.body).toEqual({ title: "改后的标题" });
    expect(untouched.path).toBe("/projects/p1/tasks/tsk_1");

    // ★ 回填的**判别性**取法：再开一次编辑，加一个 executor ⇒ 提交的集合必须
    // 由「回填进来的 agent:agent-a」+「新点的 team:backend」组成。若
    // `taskToValues()` 没把 executors 送进弹窗，baseline 就是 `[]`，这里只会
    // 得到 `["team:backend"]`（或 `[]`）⇒ 本断言变红。
    patches.length = 0;
    // ★ 竞态护栏（原为同步 `getByTestId`）：上一步提交会**异步**关闭弹窗并重读任务列表，
    //   冷缓存 / 高负载下同步查询会偶发 `Unable to find`（本卡实跑观测到 3 次，含
    //   `findBy*` 默认 1s 宽限仍不够的那次）。这里只把「等待元素出现」的宽限放长到 5s ——
    //   查询目标与后续断言语义**逐字不变**，不会掩盖任何真实缺陷（元素真缺失仍会超时红）。
    await user.click(
      await screen.findByTestId("task-row-edit-tsk_1", {}, { timeout: 5_000 }),
    );
    await screen.findByLabelText("projects.createTaskTitle");
    // 人（owners）侧加第二个用户；执行主体（executors）侧加一个团队。
    await user.click(
      screen.getByRole("button", { name: "projects.chipAssignee" }),
    );
    // user:7 已由回填选中（点它会**取消**，正是回填生效的旁证）；这里点未被选中的
    // user:1，使 owners 侧由 [7] 成长为 [7, 1]。
    await user.click(await screen.findByRole("button", { name: "user:1" }));
    // ★ P2-6：执行主体侧是**另一枚** chip，必须另开它的 popover 才看得到行。
    await user.click(
      screen.getByRole("button", { name: "projects.chipAgent" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "team:backend" }),
    );
    await user.click(
      screen.getByRole("button", { name: /projects\.editTaskSubmit/ }),
    );

    await waitFor(() => expect(patches.length).toBeGreaterThan(0));
    const grown = patches[0];
    // 请求体原文（逐字）：回填生效 ⇒ 整个新集合含**回填来的**那个主体。
    expect(grown.body).toEqual({
      owners: [7, 1],
      executors: ["agent:agent-a", "team:backend"],
    });
  }, 20_000);
});
