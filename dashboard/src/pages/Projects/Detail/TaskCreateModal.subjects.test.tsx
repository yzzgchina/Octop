/**
 * P2-12 · C1 接线补齐 —— 双主体写入面的**线级**判别性取证。
 *
 * ★ 为什么单独一个文件、且**不 mock** `api/modules/projects`：
 * 本文件要证的是「创建/编辑带双主体的任务 ⇒ 真正发出去的那条请求体**含**
 * `owners` / `executors`」（卡面 acceptance ④ 的核心）。若 mock 掉
 * `projectsApi.createTask`，抓到的只是**组件调用 API 函数时的入参**，无法排除
 * `api/` 层把字段丢掉/改名。因此这里只 stub `globalThis.fetch`，
 * 让**真实调用路径**（TaskCreateModal → projectsApi.createTask → request() →
 * fetch）跑完整条链，再直接读 `fetch` 收到的 `body` 原文。
 *
 * ★ 三态语义（卡面判别性 ②）：编辑态的三种事实各自断言 ——
 * 缺字段 = 不改 · `[]` = 清空 · 非空 = 整集替换。
 *
 * ★ 摘线即红（卡面判别性 ③）：把 `submit()` 里的 `owners` / `executors` 去掉、
 * 或把 `buildPatch()` 的两段 diff 去掉 ⇒ 本文件对应用例必须变红（见证据 §）。
 */
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ProjectMember } from "../../../api/modules/projects";
import TaskCreateModal, {
  type TaskCreateValues,
  type TaskPatchValues,
} from "./TaskCreateModal";

/**
 * ``BASE_URL`` 是 Vite 的构建期全局，jsdom 下不存在 ⇒ 真实 `request()` 会在
 * `getApiUrl()` 里先抛 `BASE_URL is not defined`，永远到不了 fetch。
 * 按仓内既有口径（`api/request.setup.test.ts`）把 base 固定为同源相对路径，
 * 这样抓到的 `url` 就是**真实**会发出的那条（`/api/...`）。
 */
vi.mock("../../../api/config", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/config")>();
  // ★ **部分**替换（必须 `importOriginal`）：整模块替换会连该模块的其它导出一起
  // 抽掉，而它们在加载期被别的模块用到 —— 实测整替换会让本弹窗**一处都不渲染**
  // （只覆盖 `getApiUrl` 才既固定 base、又不破坏其余导出）。
  return { ...actual, getApiUrl: (path: string) => `/api${path}` };
});

const MEMBERS: ProjectMember[] = [
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
  {
    subject_type: "user",
    subject_id: "7",
    user_id: 7,
    role: "owner",
    created_at: 1,
  },
];

interface CapturedRequest {
  url: string;
  method: string;
  /** The `body` string handed to fetch — the literal request payload. */
  body: string | null;
  json: Record<string, unknown> | null;
}

/** Resolves a JSON Response; `204` when there is nothing to return. */
function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let captured: CapturedRequest[] = [];
/** Resolves the task POST/PATCH; only its capture matters here. */
let subjectResolve: ((response: Response) => void) | null = null;

function installFetchStub() {
  captured = [];
  subjectResolve = null;
  const stub = vi.fn((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "GET") {
      // The dialog's three read rings, each with its own frozen shape.
      if (url.endsWith("/members"))
        return Promise.resolve(jsonResponse(MEMBERS));
      if (url.endsWith("/tags") || url.endsWith("/custom-fields")) {
        return Promise.resolve(jsonResponse([]));
      }
      return Promise.resolve(
        jsonResponse({
          project_id: "prj_1",
          name: "Apollo",
          goal: "",
          status: "active",
          owner_user_id: 1,
          memory_namespace: "",
          kb_id: null,
          start_at: null,
          due_at: null,
          created_at: 1,
          updated_at: 1,
        }),
      );
    }
    const body =
      typeof init?.body === "string"
        ? init.body
        : init?.body == null
        ? null
        : String(init.body);
    captured.push({
      url,
      method,
      body,
      json: body ? (JSON.parse(body) as Record<string, unknown>) : null,
    });
    // Never resolves: the submit path stays in flight so the test can read the
    // body instead of racing the dialog's post-success state reset.
    return new Promise<Response>((resolve) => {
      subjectResolve = resolve;
    });
  });
  vi.stubGlobal("fetch", stub);
  return stub;
}

const titleInput = () => screen.getByLabelText("projects.createTaskTitle");
const createSubmit = () =>
  screen.getByRole("button", { name: /projects\.createTaskSubmit/ });
/**
 * Mounts the dialog and waits for its **portal** to carry the inline title.
 *
 * antd renders `Modal` into `document.body` through a portal, and `render()`'s
 * own container stays empty — so a synchronous `screen.…` query right after
 * `render()` cannot see the dialog at all. Waiting here is what makes every
 * later query valid.
 */
async function mount(ui: React.ReactElement) {
  const view = render(ui);
  await screen.findByLabelText("projects.createTaskTitle");
  return view;
}

/**
 * Toggle one member row inside a chip popover.
 *
 * ★ 只在弹层**未开**时才点 chip：antd 的 `Popover` 是 click-trigger，已开时再点
 * 会把它关掉（实测：连选两个主体时第二次会找不到行）。
 */
async function toggleMember(
  user: ReturnType<typeof userEvent.setup>,
  chip: string,
  rowName: string,
) {
  if (!screen.queryByRole("button", { name: rowName })) {
    await user.click(screen.getByRole("button", { name: chip }));
  }
  await user.click(await screen.findByRole("button", { name: rowName }));
}

beforeEach(() => {
  installFetchStub();
});

afterEach(() => {
  // ★ 只解挂在飞的 POST，**不** `vi.unstubAllGlobals()`：它会把 `globalThis.fetch`
  // 置空，下一条用例里 `projectsApi.get()` 的 `.catch(() => undefined)` 会因此
  // 走上错误分支，弹窗会关掉 ⇒ 后续用例根本拿不到 chip（实测踩过）。
  // 每个 `beforeEach` 都重装 stub，隔离由下面这行保证。
  subjectResolve?.(jsonResponse({ task_id: "tsk_1" }));
  subjectResolve = null;
});

describe("P2-12 · 双主体写入面（线级：真实 request() → fetch）", () => {
  it("★ 创建带双主体的任务 ⇒ 真正发出的请求体含 owners 与 executors（无旧单值键）", async () => {
    await mount(
      <TaskCreateModal
        open
        projectId="prj_1"
        tasks={[]}
        canEdit
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.type(titleInput(), "双主体任务");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "projects.chipAssignee" }),
      ).toBeInTheDocument(),
    );

    // 一个 user 主体（owners 侧）+ 一个 agent 主体（executors 侧）。
    // ★ P2-6 重指向（**只改定位器，不改任何语义断言**）：`P2-6` / PLAN §CD ② 把既有
    //   那个「人 + 智能体」合体 chip 拆成**两个独立 picker** —— `user` 行只在
    //   `projects.chipAssignee`，`agent` / `team` 行只在 `projects.chipAgent`。本用例
    //   断言的仍是「同一次提交里 owners 与 executors 各自带值」这件事本身。
    await toggleMember(user, "projects.chipAssignee", "user:7");
    await toggleMember(user, "projects.chipAgent", "agent:agent-a");

    await user.click(createSubmit());

    await waitFor(() => expect(captured.length).toBe(1));
    const call = captured[0];

    // ★ 请求体原文（逐字），本用例的核心证据。

    expect(call.url).toBe("/api/projects/prj_1/tasks");
    expect(call.method).toBe("POST");
    expect(call.json).toMatchObject({
      title: "双主体任务",
      owners: [7],
      executors: ["agent:agent-a"],
    });
    // PLAN §B2.5 (c) V3：旧单值对与两数组**同给即 422** ⇒ 写入面不得再发它们。
    expect(call.json).not.toHaveProperty("assignee_type");
    expect(call.json).not.toHaveProperty("assignee_id");
  });

  it("三态①「缺字段 = 不改」：编辑未触碰主体 ⇒ patch 原文不含 owners / executors", async () => {
    const onSubmitEdit = vi.fn().mockResolvedValue(undefined);
    await mount(
      <TaskCreateModal
        open
        mode="edit"
        projectId="prj_1"
        tasks={[]}
        canEdit
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onSubmitEdit={onSubmitEdit}
        initialValues={
          {
            task_id: "tsk_1",
            title: "原任务",
            description: "原描述",
            status: "planning",
            priority: 0,
            owners: [7],
            executors: ["agent:agent-a"],
            tag_ids: [],
            start_at: null,
            due_at: null,
            parent_id: null,
            deps: [],
            custom_fields: {},
          } satisfies Partial<TaskCreateValues>
        }
      />,
    );
    const user = userEvent.setup();
    // 只改标题：两个主体数组原样不动。
    await user.clear(titleInput());
    await user.type(titleInput(), "改标题");
    await user.click(
      screen.getByRole("button", { name: /projects\.editTaskSubmit/ }),
    );

    await waitFor(() => expect(onSubmitEdit).toHaveBeenCalledTimes(1));
    const patch = onSubmitEdit.mock.calls[0][0] as TaskPatchValues;
    expect(patch).toEqual({ title: "改标题" });
    expect(patch).not.toHaveProperty("owners");
    expect(patch).not.toHaveProperty("executors");
  });

  it("三态②「[] = 清空」：把主体全部点掉 ⇒ patch 原文 owners: [] / executors: []", async () => {
    const onSubmitEdit = vi.fn().mockResolvedValue(undefined);
    await mount(
      <TaskCreateModal
        open
        mode="edit"
        projectId="prj_1"
        tasks={[]}
        canEdit
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onSubmitEdit={onSubmitEdit}
        initialValues={
          {
            task_id: "tsk_1",
            title: "原任务",
            description: "原描述",
            status: "planning",
            priority: 0,
            owners: [7],
            executors: ["agent:agent-a"],
            tag_ids: [],
            start_at: null,
            due_at: null,
            parent_id: null,
            deps: [],
            custom_fields: {},
          } satisfies Partial<TaskCreateValues>
        }
      />,
    );
    const user = userEvent.setup();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "projects.chipAssignee" }),
      ).toBeInTheDocument(),
    );
    await toggleMember(user, "projects.chipAssignee", "user:7");
    // ★ P2-6 重指向（只改定位器）：智能体现在在 `projects.chipAgent`。
    await toggleMember(user, "projects.chipAgent", "agent:agent-a");
    await user.click(
      screen.getByRole("button", { name: /projects\.editTaskSubmit/ }),
    );

    await waitFor(() => expect(onSubmitEdit).toHaveBeenCalledTimes(1));
    const patch = onSubmitEdit.mock.calls[0][0] as TaskPatchValues;
    expect(patch).toEqual({ owners: [], executors: [] });
  });

  it("三态③「非空 = 整集替换」：加一个 executor ⇒ patch 带**整个**新集合（不是增量）", async () => {
    const onSubmitEdit = vi.fn().mockResolvedValue(undefined);
    await mount(
      <TaskCreateModal
        open
        mode="edit"
        projectId="prj_1"
        tasks={[]}
        canEdit
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onSubmitEdit={onSubmitEdit}
        initialValues={
          {
            task_id: "tsk_1",
            title: "原任务",
            description: "原描述",
            status: "planning",
            priority: 0,
            owners: [7],
            executors: ["agent:agent-a"],
            tag_ids: [],
            start_at: null,
            due_at: null,
            parent_id: null,
            deps: [],
            custom_fields: {},
          } satisfies Partial<TaskCreateValues>
        }
      />,
    );
    const user = userEvent.setup();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "projects.chipAssignee" }),
      ).toBeInTheDocument(),
    );
    // 只加 executor：集合由 ["agent:agent-a"] 变为两个元素。
    // ★ P2-6 重指向（只改定位器）：`team` 行现在只在 `projects.chipAgent`。
    await toggleMember(user, "projects.chipAgent", "team:backend");
    await user.click(
      screen.getByRole("button", { name: /projects\.editTaskSubmit/ }),
    );

    await waitFor(() => expect(onSubmitEdit).toHaveBeenCalledTimes(1));
    const patch = onSubmitEdit.mock.calls[0][0] as TaskPatchValues;
    // 未触碰的 owners 侧**不出现**；executors 侧是完整集合。
    expect(patch).toEqual({
      executors: ["agent:agent-a", "team:backend"],
    });
  });
});
