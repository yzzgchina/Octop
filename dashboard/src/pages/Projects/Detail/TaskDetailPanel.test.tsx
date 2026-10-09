import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// 只 mock 传输层（真实走 projectsApi / projectMetadataApi 的路径与请求体）。
vi.mock("../../../api/request", () => ({
  request: vi.fn(),
  requestBlob: vi.fn(),
  requestUpload: vi.fn(),
}));

vi.mock("@/utils/antdMessage", () => ({
  message: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

import { request, requestBlob } from "../../../api/request";
import type { ProjectMember, ProjectTask } from "../../../api/modules/projects";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { STATUS_LABEL_KEYS } from "../utils/taskStatus";
import TaskDetailPanel, { hasTaskDetailParam } from "./TaskDetailPanel";

const mockedRequest = vi.mocked(request);
const mockedRequestBlob = vi.mocked(requestBlob);

const CREATED_AT = 1_750_000_000;
const UPDATED_AT = 1_750_900_000;
const DUE_AT = 1_750_600_000;

function task(overrides: Partial<ProjectTask> = {}): ProjectTask {
  return {
    task_id: "tsk_1",
    project_id: "prj_1",
    parent_id: null,
    title: "Ship it",
    description: "Full description",
    status: "doing",
    assignee_type: "agent",
    assignee_id: "agent01",
    owners: [],
    executors: [],
    priority: 3,
    deps: [],
    thread_id: null,
    origin_node_id: null,
    due_at: DUE_AT,
    start_at: CREATED_AT,
    tags: [{ tag_id: "tag_1", name: "urgent", color: "#ff0000" }],
    custom_fields: [
      {
        field_id: "fld_1",
        key: "env",
        label: "Environment",
        type: "text",
        value: "prod",
      },
    ],
    attachments: [
      {
        artifact_id: "art_1",
        name: "spec.pdf",
        size: 2048,
        mime: "application/pdf",
        created_at: CREATED_AT,
        uploader: "admin",
        task_id: "tsk_1",
      },
    ],
    sort_order: 0,
    created_by: 1,
    created_at: CREATED_AT,
    updated_at: UPDATED_AT,
    ...overrides,
  };
}

/** timeline 路由（既有端点 + `task_id`）。 */
function mockTimeline(events: unknown[]) {
  const previous = mockedRequest.getMockImplementation();
  mockedRequest.mockImplementation(async (path: string, init?: RequestInit) => {
    if (String(path).includes("/timeline")) return events as never;
    return previous ? previous(path, init) : (task() as never);
  });
}

function renderPanel(
  overrides: Partial<{
    task: ProjectTask | undefined;
    loading: boolean;
    canEdit: boolean;
    projectName: string;
    members: ProjectMember[];
    /** 判别性对照用：与面板**同一次 render** 内插入的探针节点。 */
    probe: ReactNode;
  }> = {},
) {
  const onBack = vi.fn();
  const onChanged = vi.fn(async () => {});
  const current = "task" in overrides ? overrides.task : task();
  const view = render(
    <>
      <TaskDetailPanel
        projectId="prj_1"
        task={current}
        loading={overrides.loading ?? false}
        canEdit={overrides.canEdit ?? true}
        tasks={current ? [current] : []}
        projectName={overrides.projectName}
        members={overrides.members}
        onBack={onBack}
        onChanged={onChanged}
      />
      {overrides.probe}
    </>,
  );
  return { onBack, onChanged, view };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedRequest.mockImplementation(async (path: string) => {
    if (path === "/settings/timezone") return { timezone: "UTC" };
    // P2-5：详情「动态」托盘会按 `task_id` 读一次留言 feed（既有端点）。
    if (String(path).includes("/comments")) return [];
    throw new Error(`unexpected request: ${path}`);
  });
});

describe("TaskDetailPanel — 逐字段渲染（AC-G4-2）", () => {
  it("渲染 10 个字段区（描述/状态/优先级/执行主体/父任务/依赖/标签/自定义字段/附件/动态）", async () => {
    renderPanel();

    expect(screen.getByTestId("task-detail-panel")).toBeInTheDocument();
    expect(screen.getByText("Ship it")).toBeInTheDocument();

    // 逐字段 testid（PLAN §4.4 逐字）。
    for (const field of [
      "description",
      "status",
      "priority",
      // ★ P2-5 重指向：本夹具的主体是 `agent:agent01`（镜像列）⇒ 归**执行主体**槽；
      // 「人」槽（`task-detail-assignee`）在无用户主体时**整块不渲染**（不留空壳）。
      "executor",
      "parent",
      "deps",
      "tags",
      "custom-fields",
      "attachments",
      "timeline",
    ]) {
      // ★ P2-5 重指向：`AC-23` 把裸事件行「过程」换成「动态」灰底托盘 ⇒ 容器 testid
      // 由 `task-detail-history` 改为 `task-detail-activity`（逐字段口径本身保留）。
      const testId =
        field === "timeline" ? "task-detail-activity" : `task-detail-${field}`;
      expect(screen.getByTestId(testId)).toBeInTheDocument();
    }

    expect(screen.getByTestId("task-detail-description")).toHaveTextContent(
      "Full description",
    );
    expect(screen.getByTestId("task-detail-status")).toHaveTextContent(
      STATUS_LABEL_KEYS.doing,
    );
    expect(screen.getByTestId("task-detail-priority")).toHaveTextContent("3");
    // ★ P2-5 重指向：`UI-SPEC §8.4` 把 `task-detail-assignee` 的语义钉为「**人**」，
    // 执行主体另立 `task-detail-executor`。本夹具的镜像列是 `agent:agent01`（agent 类）
    // ⇒ 按镜像的 `type` **归位**到执行主体槽（若仍挤进「人」槽 = 本批要修的双主体塌缩）。
    // 兜底文本 = `subjectDisplayName()` 的冻结形态 `type:#末6位`（与卡片行④同一口径）。
    expect(screen.queryByTestId("task-detail-assignee")).toBeNull();
    expect(screen.getByTestId("task-detail-executor")).toHaveTextContent(
      "agent:#gent01",
    );
    expect(screen.getByTestId("task-detail-tags")).toHaveTextContent("urgent");
    expect(screen.getByTestId("task-detail-custom-fields")).toHaveTextContent(
      "Environment",
    );
    expect(screen.getByTestId("task-detail-custom-fields")).toHaveTextContent(
      "prod",
    );
    // 时间用服务端时区格式化（禁裸 toLocaleString）。
    expect(screen.getByTestId("task-detail-attachments")).toHaveTextContent(
      "spec.pdf",
    );
  });

  it("空的可选字段（父任务/依赖/标签/自定义字段/附件）显示占位而非崩溃", async () => {
    renderPanel({
      task: task({
        parent_id: null,
        deps: [],
        tags: [],
        custom_fields: [],
        attachments: [],
        description: "",
      }),
    });

    expect(screen.getByTestId("task-detail-parent")).toBeInTheDocument();
    expect(screen.getByTestId("task-detail-deps")).toBeInTheDocument();
    expect(screen.getByTestId("task-detail-tags")).toBeInTheDocument();
    expect(screen.getByTestId("task-detail-custom-fields")).toBeInTheDocument();
    expect(screen.getByTestId("task-detail-attachments")).toBeInTheDocument();
  });

  it("父任务显示其标题（来自同一份列表数据，不新增读取环）", async () => {
    const parent = task({ task_id: "tsk_p", title: "Parent task" });
    const child = task({
      task_id: "tsk_c",
      title: "Child",
      parent_id: "tsk_p",
    });
    const onBack = vi.fn();
    render(
      <TaskDetailPanel
        projectId="prj_1"
        task={child}
        canEdit={false}
        tasks={[parent, child]}
        onBack={onBack}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByTestId("task-detail-parent")).toHaveTextContent(
      "Parent task",
    );
  });
});

describe("TaskDetailPanel — 完整状态流转史（批次八 AC-A-5 / A-6 / A-7）", () => {
  /** 12 条事件（AC-A-5：**不得截断** ⇒ 节点数 == 12）。 */
  const EVENTS = Array.from({ length: 12 }, (_, index) => ({
    actor: `user:${index + 1}`,
    action: "status_change",
    task_id: "tsk_1",
    payload: { from: `s${index}`, to: `s${index + 1}` },
    at: 1_750_000_000 + index * 60,
  }));

  it("12 条事件 → 渲染 12 个 activity-event（**全量、不 slice**）", async () => {
    mockTimeline(EVENTS);
    renderPanel();

    await waitFor(() =>
      expect(screen.getAllByTestId("activity-event")).toHaveLength(12),
    );
    // 走既有端点 + `task_id` 过滤（不新增端点）。
    expect(
      mockedRequest.mock.calls.some(([p]) =>
        String(p).includes("/projects/prj_1/timeline?task_id=tsk_1"),
      ),
    ).toBe(true);
  });

  it("AC-A-6：每条含 时间（服务器时区）/ 操作人 / 前后状态（payload.from → to）", async () => {
    mockTimeline(EVENTS);
    renderPanel();

    const nodes = await screen.findAllByTestId("activity-event");
    const first = nodes[0];
    expect(first).toHaveTextContent("user:1");
    expect(first).toHaveTextContent("s0 → s1");
    expect(first).toHaveTextContent(formatServerDateTime(1_750_000_000, "UTC"));
  });

  it("★ 操作人显示【名字】而非裸标识；actor_name 缺失 ⇒ 回退 actor（不得空白）", async () => {
    mockTimeline([
      {
        actor: "user:1",
        actor_name: "李四",
        action: "status_change",
        task_id: "tsk_1",
        payload: { from: "a", to: "b" },
        at: 1_750_000_000,
      },
      {
        actor: "agent:agt_9",
        actor_name: null,
        action: "status_change",
        task_id: "tsk_1",
        payload: { from: "b", to: "c" },
        at: 1_750_000_060,
      },
    ]);
    renderPanel();

    const nodes = await screen.findAllByTestId("activity-event");
    const named = within(nodes[0]).getByTestId("timeline-actor");
    expect(named).toHaveTextContent("李四");
    expect(named).not.toHaveTextContent("user:1"); // ★ 不显示稳定标识
    const bare = within(nodes[1]).getByTestId("timeline-actor");
    expect(bare).toHaveTextContent("agent:agt_9"); // 回退（不得空白）
  });

  it("AC-A-7：无流转 → 空态（不报错、不空白）", async () => {
    mockTimeline([]);
    renderPanel();

    expect(
      await screen.findByTestId("task-detail-timeline-empty"),
    ).toHaveTextContent("projects.taskDetailTimelineEmpty");
    expect(screen.queryAllByTestId("activity-event")).toHaveLength(0);
  });

  it("★ 只读：流转区没有任何编辑控件（无 button / input）", async () => {
    mockTimeline(EVENTS);
    renderPanel();
    const list = await screen.findByTestId("task-detail-timeline");
    expect(
      list.querySelectorAll("button, input, textarea, select"),
    ).toHaveLength(0);
  });
});

describe("TaskDetailPanel — 编辑复用 TaskCreateModal 差异提交（AC-G4-3 / AC-G4-6）", () => {
  it("点「编辑」打开弹窗；只改标题 → PATCH 体只含 title，且 onChanged 被调用（重读）", async () => {
    mockedRequest.mockImplementation(
      async (path: string, init?: RequestInit) => {
        if (path === "/settings/timezone") return { timezone: "UTC" };
        if (path === "/projects/prj_1/members") return [];
        if (path === "/projects/prj_1/tasks/tsk_1") {
          return task({ title: "Renamed" });
        }
        throw new Error(`unexpected request: ${init?.method ?? "GET"} ${path}`);
      },
    );

    const current = task();
    const { onChanged } = renderPanel({ task: current });

    await userEvent.click(screen.getByTestId("task-detail-edit"));
    const dialog = await screen.findByRole("dialog");
    const titleInput = within(dialog).getByDisplayValue("Ship it");
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "Renamed");
    await userEvent.click(
      // 提交按钮的可访问名带快捷键后缀（"…⌘↵"）→ 用前缀正则。
      within(dialog).getByRole("button", { name: /projects\.editTaskSubmit/ }),
    );

    await waitFor(() => {
      expect(onChanged).toHaveBeenCalled();
    });
    const patch = mockedRequest.mock.calls.find(
      ([path, init]) =>
        path === "/projects/prj_1/tasks/tsk_1" &&
        (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patch).toBeTruthy();
    // ★ 差异提交：只有改动的字段进请求体。
    expect(JSON.parse(String((patch?.[1] as RequestInit).body))).toEqual({
      title: "Renamed",
    });
  });

  it("canEdit=false（viewer）→ 没有编辑入口", async () => {
    renderPanel({ canEdit: false });
    expect(screen.queryByTestId("task-detail-edit")).toBeNull();
  });

  it("返回列表：点返回调用 onBack（移除 ?task=，列表由 owner 重读）", async () => {
    const { onBack } = renderPanel();
    await userEvent.click(screen.getByTestId("task-detail-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

describe("TaskDetailPanel — 附件只读 + 下载（AC-G4-5）", () => {
  it("列出附件并可下载（走既有下载端点），且**没有**上传/删除控件", async () => {
    mockedRequestBlob.mockResolvedValue(new Blob(["x"]));
    renderPanel();

    const attachments = screen.getByTestId("task-detail-attachments");
    expect(
      within(attachments).getByTestId("task-detail-attachment-art_1"),
    ).toHaveTextContent("spec.pdf");

    await userEvent.click(
      within(attachments).getByTestId("task-detail-download-art_1"),
    );
    await waitFor(() => {
      expect(mockedRequestBlob).toHaveBeenCalledWith(
        "/projects/prj_1/attachments/art_1/download",
      );
    });

    // 本轮不做上传 / 删除：文件选择器与删除按钮都不存在。
    expect(attachments.querySelector('input[type="file"]')).toBeNull();
    expect(
      within(attachments).queryByRole("button", { name: /delete|删除/i }),
    ).toBeNull();
  });

  it("无附件 → 显示空态文案（不崩溃）", async () => {
    renderPanel({ task: task({ attachments: [] }) });
    expect(screen.getByTestId("task-detail-attachments")).toHaveTextContent(
      "projects.attachmentsEmpty",
    );
  });
});

describe("TaskDetailPanel — 缺失 / 优雅降级（AC-G4-1 进入方式）", () => {
  it("★ ?task=<id> 不存在 → 显示 taskDetailNotFound，不白屏", async () => {
    renderPanel({ task: undefined });
    expect(screen.getByTestId("task-detail-not-found")).toHaveTextContent(
      "projects.taskDetailNotFound",
    );
    // 不白屏：面板骨架仍在（可返回列表）。
    expect(screen.getByTestId("task-detail-panel")).toBeInTheDocument();
    expect(screen.getByTestId("task-detail-back")).toBeInTheDocument();
  });

  it("深链重取中（loading=true）→ 不误报「不存在」", async () => {
    renderPanel({ task: undefined, loading: true });
    expect(screen.queryByTestId("task-detail-not-found")).toBeNull();
  });

  it("★ 缺 tab 参数：hasTaskDetailParam 以 task 为准（有 task 即进详情）", () => {
    expect(hasTaskDetailParam(new URLSearchParams("task=tsk_1"))).toBe(true);
    expect(
      hasTaskDetailParam(new URLSearchParams("tab=tasks&task=tsk_1")),
    ).toBe(true);
    expect(hasTaskDetailParam(new URLSearchParams("tab=tasks"))).toBe(false);
    expect(hasTaskDetailParam(new URLSearchParams("task="))).toBe(false);
    expect(hasTaskDetailParam(new URLSearchParams(""))).toBe(false);
  });
});

/* ===========================================================================
 * P2-5 · `C3-DETAIL`（详情页五段 + 动态区搬移 + 收敛）——新增判别性用例
 * ========================================================================= */

/** 项目成员夹具：两位用户 + 一位专家（与 `owners` / `executors` 对齐）。 */
function member(
  subject_type: "user" | "agent" | "team",
  subject_id: string,
  name: string,
  user_id: number | null = null,
): ProjectMember {
  return {
    subject_type,
    subject_id,
    user_id,
    role: "member",
    created_at: CREATED_AT,
    name,
  };
}

const MEMBERS: ProjectMember[] = [
  member("user", "7", "张三", 7),
  member("user", "8", "李四", 8),
  member("user", "9", "王五", 9),
  member("agent", "agent-a", "Bot One"),
  member("team", "backend", "Squad"),
];

/** 双主体任务（2 用户 + 2 执行主体；`P2-4` 的 2+2 样本形态）。 */
function dualSubjectTask(overrides: Partial<ProjectTask> = {}): ProjectTask {
  return task({
    owners: [7, 8],
    executors: ["agent:agent-a", "team:backend"],
    assignee_type: null,
    assignee_id: null,
    ...overrides,
  });
}

/** 追加一个 `/comments` 应答（详情「动态」托盘的 feed 读）。 */
function mockComments(comments: unknown[]) {
  const previous = mockedRequest.getMockImplementation();
  mockedRequest.mockImplementation(async (path: string, init?: RequestInit) => {
    if (String(path).includes("/comments")) return comments as never;
    return previous ? previous(path, init) : ([] as never);
  });
}

describe("P2-5 段① 标题区（AC-21：编号 + 讨论按钮外观）", () => {
  it("★ 编号命中冻结形态 ⇒ `task-detail-number` 存在且文本逐字等于编号", async () => {
    renderPanel({ task: dualSubjectTask({ task_number: "PACE-005" }) });
    expect(screen.getByTestId("task-detail-number")).toHaveTextContent(
      "PACE-005",
    );
  });

  it.each([
    { label: "缺失（null）", value: null },
    { label: "缺失（undefined）", value: undefined },
    { label: "空串", value: "" },
    { label: "位数不足 PRJ-1", value: "PRJ-1" },
    { label: "全小写 pace-001", value: "pace-001" },
    { label: "前缀超长 ABCDEFGHI-001", value: "ABCDEFGHI-001" },
    { label: "前缀以数字开头 1ACE-001", value: "1ACE-001" },
    { label: "5 位序号 PACE-00001", value: "PACE-00001" },
    { label: "分隔符错 PACE_001", value: "PACE_001" },
    { label: "前缀带空格 PACE -001", value: "PACE -001" },
  ])(
    "★ 反向：task_number $label ⇒ 编号位整块不渲染（B-05 / B-06 / B-16）",
    ({ value }) => {
      renderPanel({
        task: dualSubjectTask({ task_number: value as string | null }),
      });
      expect(screen.queryByTestId("task-detail-number")).toBeNull();
      // 同一次 render 的正向对照（防「整卡不渲染」蒙绿）。
      expect(screen.getByTestId("task-detail-title")).toBeInTheDocument();
    },
  );

  it("★ 判别性对照：同一次 render 内临时插入该节点 ⇒ 同一查询**必须**命中", () => {
    renderPanel({
      task: dualSubjectTask({ task_number: undefined }),
      probe: <span data-testid="task-detail-number">PROBE</span>,
    });
    expect(screen.getByTestId("task-detail-number")).toHaveTextContent("PROBE");
  });

  it("★ 讨论按钮 = 外观（`U-3` ⓐ：外观可做、功能不做）⇒ 锚点存在且 disabled", () => {
    renderPanel();
    const discuss = screen.getByTestId("task-detail-discuss");
    expect(discuss).toBeDisabled();
    expect(screen.getByTestId("task-detail-more")).toBeInTheDocument();
  });
});

describe("P2-5 段② meta 行（AC-22：状态 chip + 双主体两槽 + 项目 chip + ⋯）", () => {
  it("★★ 2 用户 + 2 执行主体 ⇒ 两槽各渲染 2 枚主体 + 名字（与卡片行④同源）", () => {
    renderPanel({
      task: dualSubjectTask(),
      members: MEMBERS,
      projectName: "Pace",
    });

    const ownerSlot = screen.getByTestId("task-detail-assignee");
    const executorSlot = screen.getByTestId("task-detail-executor");
    expect(
      ownerSlot.querySelectorAll('[data-testid="task-card-assignee-avatar"]'),
    ).toHaveLength(2);
    expect(
      executorSlot.querySelectorAll(
        '[data-testid="task-card-assignee-avatar"]',
      ),
    ).toHaveLength(2);
    expect(ownerSlot).toHaveTextContent("张三");
    expect(executorSlot).toHaveTextContent("Bot One");
    // 执行主体侧带类型徽标（卡片行④同款组件）。
    expect(
      executorSlot.querySelector('[data-testid="task-card-agent-badge"]'),
    ).not.toBeNull();
    // 全部主体名进 `title`（与卡片同源的无障碍口径）。
    expect(ownerSlot.getAttribute("title")).toBe("张三、李四");
    expect(executorSlot.getAttribute("title")).toBe("Bot One、Squad");
    // 项目 chip + 状态 chip。
    expect(screen.getByTestId("task-detail-project")).toHaveTextContent("Pace");
    expect(screen.getByTestId("task-detail-status")).toBeInTheDocument();
  });

  it("★ 多主体折叠 = 前 2 + `+N`（3 用户 + 2 执行主体 ⇒ 2 枚 + `+1` / 2 枚 + 无 `+N`）", () => {
    renderPanel({
      task: dualSubjectTask({ owners: [7, 8, 9] }),
      members: MEMBERS,
    });

    const ownerSlot = screen.getByTestId("task-detail-assignee");
    expect(
      ownerSlot.querySelectorAll('[data-testid="task-card-assignee-avatar"]'),
    ).toHaveLength(2);
    expect(
      ownerSlot.querySelector('[data-subject-more="owner"]'),
    ).toHaveTextContent("+1");
    const executorSlot = screen.getByTestId("task-detail-executor");
    expect(
      executorSlot.querySelector('[data-subject-more="executor"]'),
    ).toBeNull();
  });

  it("★ 空态（R21）：两侧皆空 ⇒ 两槽都不渲染空壳、不报错；判别性对照：插入 1 个用户主体 ⇒ 同查询命中", () => {
    const empty = renderPanel({
      task: task({
        owners: [],
        executors: [],
        assignee_type: null,
        assignee_id: null,
      }),
    });
    expect(screen.queryByTestId("task-detail-assignee")).toBeNull();
    expect(screen.queryByTestId("task-detail-executor")).toBeNull();
    expect(
      screen.getByTestId("task-detail-panel").querySelector('[role="alert"]'),
    ).toBeNull();
    empty.view.unmount();

    // 判别性对照（防「查询本身恒空 ⇒ 也算绿」）。
    renderPanel({
      task: task({ owners: [7], executors: [] }),
      members: MEMBERS,
    });
    expect(screen.getByTestId("task-detail-assignee")).toHaveTextContent(
      "张三",
    );
  });

  it("★ 镜像列兜底按 `type` 归位：`user:` ⇒ 人槽；`agent:` ⇒ 执行主体槽", () => {
    const asUser = renderPanel({
      task: task({
        owners: [],
        executors: [],
        assignee_type: "user",
        assignee_id: "7",
      }),
      members: MEMBERS,
    });
    expect(screen.getByTestId("task-detail-assignee")).toHaveTextContent(
      "张三",
    );
    expect(screen.queryByTestId("task-detail-executor")).toBeNull();
    asUser.view.unmount();

    renderPanel({
      task: task({
        owners: [],
        executors: [],
        assignee_type: "team",
        assignee_id: "backend",
      }),
      members: MEMBERS,
    });
    expect(screen.getByTestId("task-detail-executor")).toHaveTextContent(
      "Squad",
    );
    expect(screen.queryByTestId("task-detail-assignee")).toBeNull();
  });
});

describe("P2-5 段④ 「动态」时间线（AC-23：头像 + 动作 + 相对时间 / 旧裸事件行不再渲染）", () => {
  const NOW = Math.floor(Date.now() / 1000);

  it("★ 灰底托盘 + 每条 = 头像 + 谁 + 动作 + 相对时间（今天 / 昨天 / N 天前）", async () => {
    mockTimeline([
      {
        actor: "user:7",
        actor_name: "张三",
        action: "status_change",
        task_id: "tsk_1",
        payload: { from: "a", to: "b" },
        at: NOW,
      },
      {
        actor: "user:8",
        actor_name: "李四",
        action: "created",
        task_id: "tsk_1",
        payload: {},
        at: NOW - 86_400,
      },
      {
        actor: "agent:agent-a",
        actor_name: null,
        action: "assigned",
        task_id: "tsk_1",
        payload: { from: "b", to: "c" },
        at: NOW - 3 * 86_400,
      },
    ]);
    renderPanel();

    const nodes = await screen.findAllByTestId("activity-event");
    expect(nodes).toHaveLength(3);
    // 头像（首字符 = 显示名首字；回退时用稳定标识首字符，绝不空白）。
    expect(
      nodes[0].querySelector('[data-testid="activity-avatar"]'),
    ).toHaveTextContent("张");
    expect(
      nodes[2].querySelector('[data-testid="activity-avatar"]'),
    ).toHaveTextContent("a");
    // 谁 + 动作 + 前后状态（AC-A-6 的口径不变）。
    expect(within(nodes[0]).getByTestId("timeline-actor")).toHaveTextContent(
      "张三",
    );
    expect(nodes[0]).toHaveTextContent("status_change");
    expect(nodes[0]).toHaveTextContent("a → b");
    // 相对时间（天粒度）。
    expect(within(nodes[0]).getByTestId("activity-time")).toHaveTextContent(
      "projects.taskDetailTimeToday",
    );
    expect(within(nodes[1]).getByTestId("activity-time")).toHaveTextContent(
      "projects.taskDetailTimeYesterday",
    );
    expect(within(nodes[2]).getByTestId("activity-time")).toHaveTextContent(
      "projects.taskDetailTimeDaysAgo",
    );
    // 灰底托盘（`--fn-bg-group`）。
    expect(screen.getByTestId("task-detail-activity")).toBeInTheDocument();
    expect(
      screen
        .getByTestId("task-detail-activity")
        .innerHTML.includes("--fn-bg-group"),
    ).toBe(true);
  });

  it("★ 未来时间不得渲染为负数（SPEC 已冻结边界）⇒ 回落服务器时区绝对时间", async () => {
    const future = NOW + 86_400;
    mockTimeline([
      {
        actor: "user:7",
        actor_name: "张三",
        action: "created",
        task_id: "tsk_1",
        payload: {},
        at: future,
      },
    ]);
    renderPanel();

    const node = (await screen.findAllByTestId("activity-event"))[0];
    const time = within(node).getByTestId("activity-time");
    expect(time).toHaveTextContent(formatServerDateTime(future, "UTC"));
    expect(time.textContent ?? "").not.toMatch(/-\d/);
  });

  it("★★ 两向：旧「过程」裸事件行（`timeline-node`）**不再渲染**；同一查询对注入的旧行**必须**命中", async () => {
    mockTimeline([
      {
        actor: "user:7",
        actor_name: "张三",
        action: "created",
        task_id: "tsk_1",
        payload: {},
        at: NOW,
      },
    ]);
    renderPanel({
      probe: <span data-testid="timeline-node">LEGACY</span>,
    });

    await screen.findAllByTestId("activity-event");
    const legacy = screen.getAllByTestId("timeline-node");
    // 同一次 render 内：**只**剩判别性探针那一枚（旧行本身没有第二枚）。
    expect(legacy).toHaveLength(1);
    expect(legacy[0]).toHaveTextContent("LEGACY");
    expect(screen.queryByTestId("task-detail-history")).toBeNull();
  });

  it("★★ feed 搬移：按 `task_id` 过滤走**既有**端点，且详情托盘**只读**（无写控件）", async () => {
    mockTimeline([]);
    mockComments([
      {
        comment_id: "c1",
        project_id: "prj_1",
        task_id: "tsk_1",
        thread_id: null,
        author_type: "user",
        author_id: "7",
        name: "张三",
        body: "第一条留言",
        source: "api",
        node_type: "none",
        concluded: false,
        created_at: NOW,
        updated_at: NOW,
      },
      {
        comment_id: "c2",
        project_id: "prj_1",
        task_id: "tsk_1",
        thread_id: null,
        author_type: "agent",
        author_id: "agent-a",
        name: "Bot One",
        body: "结论正文",
        source: "api",
        node_type: "conclusion",
        concluded: true,
        concluded_by_type: "user",
        concluded_by_id: "7",
        concluded_by_name: "张三",
        created_at: NOW,
        updated_at: NOW,
      },
    ]);

    renderPanel();
    const items = await screen.findAllByTestId("feed-item");
    expect(items).toHaveLength(2);
    // 头像 + 名字 + 结论徽标（与项目级「动态」Tab **同一份** `CommentFeed` 实现）。
    expect(
      items[0].querySelector('[data-testid="feed-author-avatar"]'),
    ).toHaveTextContent("张");
    expect(
      within(items[1]).getByTestId("comment-conclusion-badge"),
    ).toHaveTextContent("projects.conclusionBadge");
    // 既有端点 + `task_id` 过滤（不新增端点 / 不新增后端字段）。
    expect(
      mockedRequest.mock.calls.some(([p]) =>
        String(p).includes("/projects/prj_1/comments?task_id=tsk_1"),
      ),
    ).toBe(true);
    // 只读：托盘内不得出现按钮 / 输入（写入口归项目级「动态」Tab）。
    const tray = screen.getByTestId("task-detail-activity");
    expect(
      tray.querySelectorAll("button, input, textarea, select"),
    ).toHaveLength(0);
  });

  it("★ 无留言 ⇒ 托盘内的 feed 空态（`task-detail-activity-empty`）", async () => {
    mockTimeline([]);
    renderPanel();
    expect(
      await screen.findByTestId("task-detail-activity-empty"),
    ).toHaveTextContent("projects.feedEmpty");
  });
});

describe("P2-5 段⑤ 底部操作条（AC-24：五项齐全）", () => {
  it("★ 五项逐条有可定位锚点", () => {
    renderPanel({ projectName: "Pace" });
    const bar = screen.getByTestId("task-detail-bottom-bar");
    for (const testId of [
      "task-detail-bottom-members",
      "task-detail-bottom-agent",
      "task-detail-bottom-project",
      "task-detail-bottom-issue",
      "task-detail-bottom-attachment",
    ]) {
      expect(within(bar).getByTestId(testId)).toBeInTheDocument();
    }
    expect(
      within(bar).getByTestId("task-detail-bottom-members"),
    ).toHaveTextContent("projects.members");
    expect(
      within(bar).getByTestId("task-detail-bottom-project"),
    ).toHaveTextContent("Pace");
    expect(
      within(bar).getByTestId("task-detail-bottom-issue"),
    ).toHaveTextContent("projects.taskDetailIssue");
  });

  it("★ `viewer`（canEdit=false）⇒ 整条不渲染（`REVIEW.md · S-06` 的禁用/不渲染分支）", () => {
    renderPanel({ canEdit: false });
    expect(screen.queryByTestId("task-detail-bottom-bar")).toBeNull();
  });
});

describe("P2-5 ★★ 收敛：`taskToValues()` 与留言 feed 各只有一个出处", () => {
  const panelSource = readFileSync(
    resolve(__dirname, "TaskDetailPanel.tsx"),
    "utf8",
  );
  const indexSource = readFileSync(resolve(__dirname, "index.tsx"), "utf8");
  const dynamicSource = readFileSync(
    resolve(__dirname, "DynamicTab.tsx"),
    "utf8",
  );
  const sharedSource = readFileSync(
    resolve(__dirname, "taskCreateValues.ts"),
    "utf8",
  );
  const feedSource = readFileSync(
    resolve(__dirname, "CommentFeed.tsx"),
    "utf8",
  );

  it("★★ `taskToValues()` 单一出处：两个消费方都 import 它，且**都不再**本地声明", () => {
    // 唯一实现处。
    expect(sharedSource).toContain("export function taskToValues");
    for (const source of [panelSource, indexSource]) {
      expect(source).toContain('from "./taskCreateValues"');
      expect(source).toMatch(
        /import\s*\{\s*taskToValues\s*\}\s*from\s*"\.\/taskCreateValues"/,
      );
      // 第二份拷贝正是本卡要收敛掉的形态。
      expect(source).not.toContain("function taskToValues");
    }
  });

  it("★★ 留言 feed 单一实现：两个消费方都渲染同一个 `CommentFeed`，且都不再内联 feed 的 JSX", () => {
    for (const source of [panelSource, dynamicSource]) {
      expect(source).toContain('from "./CommentFeed"');
      expect(source).toContain("<CommentFeed");
    }
    // feed item 的渲染（`feed-item`）只许出现在共用实现里。
    expect(feedSource).toContain('data-testid="feed-item"');
    expect(dynamicSource).not.toContain('data-testid="feed-item"');
    expect(panelSource).not.toContain('data-testid="feed-item"');
  });

  it("★ 详情页复用 `Board.tsx` 的展示件（不得另立第二套「前 2 + +N」）", () => {
    for (const symbol of [
      "SubjectAvatar",
      "SubjectBadge",
      "subjectDisplayName",
      "taskNumberText",
      "taskSubjects",
    ]) {
      expect(panelSource).toMatch(
        new RegExp(
          `import\\s*\\{[\\s\\S]*?\\b${symbol}\\b[\\s\\S]*?\\}\\s*from\\s*"\\./Board"`,
        ),
      );
      expect(panelSource).not.toContain(`function ${symbol}`);
    }
  });
});

describe("P2-5 编辑回填走**唯一**适配器（与新建同源）", () => {
  it("★ 编辑弹窗的已选主体 = `taskToValues()` 回填的 `owners` / `executors`（无遗漏、不丢字段）", async () => {
    mockedRequest.mockImplementation(
      async (path: string, init?: RequestInit) => {
        if (path === "/settings/timezone") return { timezone: "UTC" };
        if (String(path).includes("/comments")) return [];
        if (String(path).includes("/timeline")) return [];
        if (path === "/projects/prj_1/members") return MEMBERS;
        throw new Error(`unexpected request: ${init?.method ?? "GET"} ${path}`);
      },
    );

    const current = dualSubjectTask();
    renderPanel({ task: current, members: MEMBERS });

    await userEvent.click(screen.getByTestId("task-detail-edit"));
    const dialog = await screen.findByRole("dialog");
    // 双主体两类各 2 个，回填后**一个都不能少**（P2-12 的 chip 摘要口径）。
    await waitFor(() =>
      expect(dialog.textContent ?? "").toContain(
        "user:7、user:8、agent:agent-a、team:backend",
      ),
    );
  });
});
