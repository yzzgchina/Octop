import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Only the transport is mocked, so the view really goes through
// ``projectsApi.listTasks`` (the frozen data face) and the shared status module.
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

import { request } from "../../../api/request";
import type {
  ProjectMember,
  ProjectTask,
  ProjectTaskStatus,
} from "../../../api/modules/projects";
import zh from "../../../locales/zh.json";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import {
  COLUMN_STATUSES,
  STATUS_COLORS,
  STATUS_LABEL_KEYS,
} from "../utils/taskStatus";
import TaskListView, { TASK_LIST_COLUMN_KEYS } from "./TaskListView";
/**
 * CSS Modules 的类名是**哈希**（`_isOn_eba01a`），断言里不得硬编码 —— 从**同一个**
 * 模块读回来，才能既判「亮格数」又不与哈希值耦合。
 */
import styles from "./TaskListView.module.less";

const mockedRequest = vi.mocked(request);

const START_AT = 1_750_000_000;
const DUE_AT = 1_750_600_000;
const UPDATED_AT = 1_750_900_000;

/** ★ 列面期望值 = **字面量**（不从被测模块推导，避免自证循环）。 */
const COLUMN_KEYS = [
  "projects.taskListColumnStatus",
  "projects.taskListColumnTitle",
  "projects.taskListColumnAssignee",
  "projects.taskListColumnPriority",
  "projects.taskListColumnStartAt",
  "projects.taskListColumnDueAt",
  "projects.taskListColumnTags",
  "projects.taskListColumnUpdatedAt",
];

/** 独立取自 i18n 资源侧的 zh 文案（字面量，用于第二条机判的交叉锚）。 */
const COLUMN_ZH = [
  "状态",
  "标题",
  "负责人",
  "优先级",
  "开始",
  "截止",
  "标签",
  "更新时间",
];

/**
 * 列面断言辅助 —— 机判 ①② 与 ③ 用**同一套**断言，因此 ③ 的判别性对照
 * （9 列 fixture 必失败）直接证明 ①② 有判别力（不是自证循环）。
 */
function assertColumnFace(headers: string[]) {
  expect(headers).toHaveLength(8);
  expect(headers).toEqual(COLUMN_KEYS);
}

/**
 * 卡片判据辅助（G1 AC-G1-1）——「7 张卡片且顺序 = 词表序」。
 * 判别性对照用**同一套**辅助函数，故 ③ 的对照直接证明本判据有判别力。
 */
function assertSevenCards(ids: string[]) {
  expect(ids).toHaveLength(7);
  expect(ids).toEqual(
    COLUMN_STATUSES.map((status) => `task-section-card-${status}`),
  );
}

function task(overrides: Partial<ProjectTask> = {}): ProjectTask {
  return {
    task_id: "tsk_1",
    project_id: "prj_1",
    parent_id: null,
    title: "Task one",
    description: "",
    status: "todo",
    assignee_type: null,
    assignee_id: null,
    owners: [],
    executors: [],
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
    created_at: START_AT,
    updated_at: START_AT,
    ...overrides,
  };
}

function renderList(
  refreshKey?: number,
  onEditTask?: (taskId: string) => void,
) {
  const onOpenTask = vi.fn();
  const view = render(
    <TaskListView
      projectId="prj_1"
      onOpenTask={onOpenTask}
      refreshKey={refreshKey}
      onEditTask={onEditTask}
    />,
  );
  return { onOpenTask, view };
}

/**
 * **允许的请求路径集合**（`AC-F1-3` · `B-12`）—— **恰**这三条：
 *   ① `/projects/prj_1/tasks`   —— 数据源（`projectsApi.listTasks`，**恰 1 次**）
 *   ② `/projects/prj_1/members` —— 显示名（`useProjectMembers`，**恰 1 次**，`B-12` 反 N+1）
 *   ③ `/settings/timezone`      —— 服务器时区（既有，与数据面无关）
 * 任何第四条路径（分页 / 新端点）都会让 `AC-F1-3` 失败。
 */
const ALLOWED_REQUEST_PATHS: readonly string[] = [
  "/projects/prj_1/tasks",
  "/projects/prj_1/members",
  "/settings/timezone",
];

function mockTasks(rows: ProjectTask[], members: ProjectMember[] = []) {
  mockedRequest.mockImplementation(async (path: string) => {
    if (path === "/settings/timezone") return { timezone: "UTC" };
    if (path === "/projects/prj_1/tasks") return rows;
    // `B-12`：显示名走**每块 1 次**的成员表读；不补这一支即抛
    // `unexpected request`，被 hook 静默吞掉 ⇒ 兜底分支被误当成正常态。
    if (path === "/projects/prj_1/members") return members;
    throw new Error(`unexpected request: ${path}`);
  });
}

/**
 * 当前渲染出的表头文本。
 *
 * ⚠️ 与 PLAN §5.3 ① 的**字面**口径（`getAllByRole("columnheader", {hidden:true})`
 * == 8）的偏差与原因（已回报 Lead）：antd 在 `scroll.x` 下会往 tbody 里塞一行
 * `ant-table-measure-row`（8 个 `<th class="ant-table-measure-cell">`，内容就是同样
 * 的 8 个列名）→ 字面口径恒为 16，**改造前的旧代码也是 16**（PLAN 机判缺陷）。
 * 这里采用 PLAN **同一句给出的替代口径** `table.querySelectorAll("thead th")`，
 * 它同样包含隐藏元素（`querySelectorAll` 不过滤 `aria-hidden`/`hidden`），
 * 并对「第 9 列」仍然敏感（③ 的判别性对照用同一套辅助函数）。
 */
function tableHeadCells(): HTMLElement[] {
  return Array.from(
    screen.getByRole("table").querySelectorAll<HTMLElement>("thead th"),
  );
}

function renderedHeaders(): string[] {
  return tableHeadCells().map((cell) => cell.textContent ?? "");
}

/** 区块容器 testid，按 DOM 顺序。 */
function zoneIds(): string[] {
  return screen
    .getAllByTestId(
      /^task-section-(planning|todo|doing|review|blocked|done|cancelled)$/,
    )
    .map((node) => node.getAttribute("data-testid") ?? "");
}

/** 某个区块内的任务行标题（DOM 顺序 = 区内顺序）。 */
function zoneRowTitles(status: ProjectTaskStatus): string[] {
  return screen
    .getAllByTestId(/^task-row-title-/)
    .filter(
      (node) =>
        node.closest("[data-section]")?.getAttribute("data-section") === status,
    )
    .map((node) => node.textContent ?? "");
}

/** 全部任务行标题，按 DOM 顺序。 */
function rowTitles(): string[] {
  return screen
    .getAllByTestId(/^task-row-title-/)
    .map((node) => node.textContent ?? "");
}

beforeEach(() => {
  vi.clearAllMocks();
  mockTasks([]);
});

describe("TaskListView", () => {
  it("renders the eight frozen columns from the shared status vocabulary", async () => {
    mockTasks([
      task({
        task_id: "tsk_1",
        title: "Ship it",
        status: "doing",
        assignee_type: "agent",
        assignee_id: "agent01",
        priority: 3,
        start_at: START_AT,
        due_at: DUE_AT,
        updated_at: UPDATED_AT,
        tags: [{ tag_id: "tag_1", name: "urgent", color: "#ff0000" }],
      }),
    ]);

    renderList();

    await waitFor(() => {
      expect(screen.getByText("Ship it")).toBeInTheDocument();
    });

    // Status cell: shared label key + shared antd colour (区块标题也用同一文案，
    // 因此按 class 挑出状态 Tag 本身)。
    const statusTag = screen
      .getAllByText(STATUS_LABEL_KEYS.doing)
      .find((node) =>
        node.classList.contains(`ant-tag-${STATUS_COLORS.doing}`),
      );
    expect(statusTag).toBeTruthy();

    // 负责人 = `assignee_type:短id`，短 id = `assignee_id` **末 6 位**（口径 = `SPEC · R-09`
    // 「`type:#末 6 位`」）。本 fixture 的 `agent01` 是 7 位 ⇒ 末 6 位 = `gent01`；
    // 成员表为空 ⇒ 走 R-09 兜底分支（不是旧口径 `type:整串`）。
    expect(screen.getByText("agent:#gent01")).toBeInTheDocument();
    // 判别性负向断言：旧口径「整串 id」必须已经消失。
    expect(screen.queryByText("agent:agent01")).toBeNull();
    // 优先级 = 3 柱 + 档位文案（`R-01`/`R-02`）：3 ⇒ 高；该格不再出现裸数字。
    expect(screen.getByText("common.cardPriorityHigh")).toBeInTheDocument();
    expect(screen.getByText("urgent")).toBeInTheDocument();

    // Dates: formatServerDateTime(epochSec, serverTimeZone), never toLocaleString.
    expect(
      screen.getByText(formatServerDateTime(START_AT, "UTC")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(formatServerDateTime(DUE_AT, "UTC")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(formatServerDateTime(UPDATED_AT, "UTC")),
    ).toBeInTheDocument();
  });

  it("G4 推翻 S8(b) 的「纯文本」一半：标题可点；编辑仍是独立图标按钮（S8(b) 保留项）", async () => {
    mockTasks([task({ task_id: "tsk_42", title: "Open me" })]);
    const onEditTask = vi.fn();

    const { view } = renderList(undefined, onEditTask);
    const title = await screen.findByTestId("task-row-title-tsk_42");

    // PLAN §4.1：标题 = 导航入口（可点元素）。★ 本条取代批次三 S8(b) 的「非 button」断言。
    expect(title.tagName).toBe("BUTTON");
    // 编辑入口与标题是**两个不同元素**（不得把标题变成编辑入口）。
    const editNode = screen.getByTestId("task-row-edit-tsk_42");
    expect(editNode).not.toBe(title);
    expect(title.contains(editNode)).toBe(false);

    // 编辑入口：常显图标按钮 + 非空 aria-label + 点击回调。
    const edit = screen.getByTestId("task-row-edit-tsk_42");
    expect(edit).toHaveAttribute("aria-label", "projects.editTaskTitle");
    fireEvent.click(edit);
    expect(onEditTask).toHaveBeenCalledWith("tsk_42");

    // 未传 onEditTask → 无编辑按钮。
    view.unmount();
    mockTasks([task({ task_id: "tsk_43", title: "No edit" })]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_43");
    expect(screen.queryByTestId("task-row-edit-tsk_43")).toBeNull();
  });

  it("sorts by (sort_order, task_id) so ties are deterministic (S7)", async () => {
    // Deliberately unsorted, and two rows share sort_order 5.
    mockTasks([
      task({ task_id: "tsk_b", title: "B", status: "todo", sort_order: 5 }),
      task({ task_id: "tsk_c", title: "C", status: "todo", sort_order: 1 }),
      task({ task_id: "tsk_a", title: "A", status: "todo", sort_order: 5 }),
    ]);

    renderList();

    await waitFor(() => {
      expect(zoneRowTitles("todo")).toEqual(["C", "A", "B"]);
    });
  });

  it("filters by status, with every status selected by default", async () => {
    mockTasks([
      task({ task_id: "tsk_1", title: "Todo task", status: "todo" }),
      task({
        task_id: "tsk_2",
        title: "Doing task",
        status: "doing",
        sort_order: 1,
      }),
    ]);

    renderList();
    await waitFor(() => {
      expect(rowTitles()).toEqual(["Todo task", "Doing task"]);
    });

    // Deselect `todo` in the status filter.
    fireEvent.mouseDown(screen.getByRole("combobox"));
    const dropdown = document.querySelector(
      ".ant-select-dropdown",
    ) as HTMLElement;
    expect(dropdown).toBeTruthy();
    fireEvent.click(
      Array.from(dropdown.querySelectorAll(".ant-select-item-option")).find(
        (option) => option.textContent === STATUS_LABEL_KEYS.todo,
      ) as HTMLElement,
    );

    await waitFor(() => {
      expect(rowTitles()).toEqual(["Doing task"]);
    });
  });

  it("filters by keyword across title and description", async () => {
    mockTasks([
      task({ task_id: "tsk_1", title: "Alpha", description: "first" }),
      task({
        task_id: "tsk_2",
        title: "Beta",
        description: "mentions gamma",
        sort_order: 1,
      }),
    ]);

    renderList();
    await waitFor(() => {
      expect(rowTitles()).toEqual(["Alpha", "Beta"]);
    });

    const keyword = screen.getByPlaceholderText(
      "projects.taskListFilterKeyword",
    );
    // Description match.
    fireEvent.change(keyword, { target: { value: "gamma" } });
    await waitFor(() => {
      expect(rowTitles()).toEqual(["Beta"]);
    });

    // Title match, case-insensitively.
    fireEvent.change(keyword, { target: { value: "ALPHA" } });
    await waitFor(() => {
      expect(rowTitles()).toEqual(["Alpha"]);
    });
  });

  it("re-reads when the parent bumps refreshKey", async () => {
    mockTasks([task({ task_id: "tsk_1", title: "First" })]);

    const { view } = renderList(0);
    await screen.findByTestId("task-row-title-tsk_1");
    const callsBefore = mockedRequest.mock.calls.filter(
      ([path]) => path === "/projects/prj_1/tasks",
    ).length;

    view.rerender(
      <TaskListView projectId="prj_1" onOpenTask={vi.fn()} refreshKey={1} />,
    );

    await waitFor(() => {
      expect(
        mockedRequest.mock.calls.filter(
          ([path]) => path === "/projects/prj_1/tasks",
        ).length,
      ).toBe(callsBefore + 1);
    });
  });
});

describe("F1 按状态分区（PLAN §1）", () => {
  it("AC-F1-1: 7 个区块恒显，顺序 == COLUMN_STATUSES，标题/计数各带 testid", async () => {
    mockTasks([
      task({ task_id: "tsk_1", title: "Todo task", status: "todo" }),
      task({
        task_id: "tsk_2",
        title: "Todo two",
        status: "todo",
        sort_order: 1,
      }),
      task({
        task_id: "tsk_3",
        title: "Doing task",
        status: "doing",
        sort_order: 2,
      }),
    ]);

    renderList();
    await waitFor(() => {
      expect(zoneIds()).toHaveLength(7);
    });

    // 区块顺序 = 词表序。
    expect(zoneIds()).toEqual(
      COLUMN_STATUSES.map((status) => `task-section-${status}`),
    );

    // 标题 = 共享词表的本地化文案；计数 = 该区行数。
    for (const status of COLUMN_STATUSES) {
      expect(
        screen.getByTestId(`task-section-title-${status}`),
      ).toHaveTextContent(STATUS_LABEL_KEYS[status]);
      const expected = status === "todo" ? 2 : status === "doing" ? 1 : 0;
      expect(
        screen.getByTestId(`task-section-count-${status}`),
      ).toHaveTextContent(String(expected));
      expect(zoneRowTitles(status)).toHaveLength(expected);
    }
  });

  it("AC-F1-2: 空区显示 taskListEmpty（不隐藏区块）；筛选只减少区内行", async () => {
    mockTasks([
      task({ task_id: "tsk_1", title: "Alpha", status: "todo" }),
      task({ task_id: "tsk_2", title: "Beta", status: "doing", sort_order: 1 }),
    ]);

    renderList();
    await waitFor(() => expect(zoneIds()).toHaveLength(7));

    // 全空态：除 todo/doing 外的 5 个区各一行空态。
    const emptyZones = COLUMN_STATUSES.filter(
      (status) => status !== "todo" && status !== "doing",
    );
    for (const status of emptyZones) {
      expect(
        screen.getByTestId(`task-section-empty-${status}`),
      ).toHaveTextContent("projects.taskListEmpty");
    }

    // 关键字筛掉 todo → 该区变空区，但**仍是 7 个区块**。
    fireEvent.change(
      screen.getByPlaceholderText("projects.taskListFilterKeyword"),
      { target: { value: "beta" } },
    );
    await waitFor(() => {
      expect(rowTitles()).toEqual(["Beta"]);
    });
    expect(zoneIds()).toHaveLength(7);
    expect(screen.getByTestId("task-section-empty-todo")).toBeInTheDocument();
    expect(screen.getByTestId("task-section-count-todo")).toHaveTextContent(
      "0",
    );
  });

  it("AC-F1-2/S5G5: 状态多选取消某状态后仍 7 区（只是该区变空）", async () => {
    mockTasks([
      task({ task_id: "tsk_1", title: "Alpha", status: "todo" }),
      task({ task_id: "tsk_2", title: "Beta", status: "doing", sort_order: 1 }),
    ]);

    renderList();
    await waitFor(() => expect(rowTitles()).toEqual(["Alpha", "Beta"]));

    fireEvent.mouseDown(screen.getByRole("combobox"));
    const dropdown = document.querySelector(
      ".ant-select-dropdown",
    ) as HTMLElement;
    fireEvent.click(
      Array.from(dropdown.querySelectorAll(".ant-select-item-option")).find(
        (option) => option.textContent === STATUS_LABEL_KEYS.doing,
      ) as HTMLElement,
    );

    await waitFor(() => expect(rowTitles()).toEqual(["Alpha"]));
    expect(zoneIds()).toHaveLength(7);
    expect(screen.getByTestId("task-section-empty-doing")).toHaveTextContent(
      "projects.taskListEmpty",
    );
  });

  it("AC-F1-3: 数据源不变（单次 listTasks，无分页/新端点）", async () => {
    mockTasks([task({ task_id: "tsk_1", title: "Alpha" })]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_1");
    // 等成员表读**落地**（不是只发出）：`B-12` 的「恰 1 次」才是终态计数。
    await waitFor(() => {
      expect(
        mockedRequest.mock.calls.filter(
          ([path]) => path === "/projects/prj_1/members",
        ),
      ).toHaveLength(1);
    });

    const paths = mockedRequest.mock.calls.map(([path]) => path);
    // 恰一次 listTasks（无分页、无新端点）。
    expect(
      paths.filter((path) => path === "/projects/prj_1/tasks"),
    ).toHaveLength(1);
    // 成员表也**恰一次**（`B-12`：显示名每块 1 次，**不是**每行 1 次）。
    expect(
      paths.filter((path) => path === "/projects/prj_1/members"),
    ).toHaveLength(1);
    // 允许路径集合（见文件顶部 `ALLOWED_REQUEST_PATHS`，已含 `/projects/prj_1/members`）
    // 之外的路径 = ∅。
    expect(
      paths.filter((path) => !ALLOWED_REQUEST_PATHS.includes(path)),
    ).toEqual([]);
  });

  it("AC-F1-5: 传 onEditTask 时每行都有编辑按钮；未传时没有", async () => {
    mockTasks([
      task({ task_id: "tsk_1", title: "Alpha" }),
      task({ task_id: "tsk_2", title: "Beta", sort_order: 1 }),
    ]);

    const onEditTask = vi.fn();
    const { view } = renderList(undefined, onEditTask);
    await waitFor(() => {
      expect(screen.getAllByTestId(/^task-row-edit-/)).toHaveLength(2);
    });

    for (const button of screen.getAllByTestId(/^task-row-edit-/)) {
      expect(button.getAttribute("aria-label")).toBeTruthy();
    }
    fireEvent.click(screen.getByTestId("task-row-edit-tsk_2"));
    expect(onEditTask).toHaveBeenCalledWith("tsk_2");

    view.unmount();
    mockTasks([task({ task_id: "tsk_1", title: "Alpha" })]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_1");
    expect(screen.queryAllByTestId(/^task-row-edit-/)).toHaveLength(0);
  });
});

describe("列面冻结 8 列（PLAN §5.3 机判 ①②③）", () => {
  it("① 单个 table 内 8 个 columnheader（hidden:true），并与导出列面对照", async () => {
    mockTasks([task({ task_id: "tsk_1", title: "Alpha", status: "doing" })]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_1");

    const table = screen.getByRole("table");
    // (a) 真实列面 = thead 的 8 个 th（`querySelectorAll` 含隐藏列 → 防第 9 列漏判）。
    expect(table.querySelectorAll("thead th")).toHaveLength(8);
    // (b) antd 的测宽行同为 8 列（防第 9 列只出现在测宽行里）。
    const measureCells = table.querySelectorAll(".ant-table-measure-row th");
    expect(measureCells).toHaveLength(8);
    // (c) `hidden:true` 口径下的 columnheader 只允许来自 thead + 测宽行，别无其它
    //     （这才是「有没有第 9 列」的真正判据）。
    const roleHeaders = within(table).getAllByRole("columnheader", {
      hidden: true,
    });
    expect(roleHeaders).toHaveLength(
      tableHeadCells().length + measureCells.length,
    );
    expect(
      roleHeaders.filter(
        (cell) =>
          !cell.closest("thead") && !cell.closest(".ant-table-measure-row"),
      ),
    ).toHaveLength(0);
    // 与导出的列面长度对照。
    expect(TASK_LIST_COLUMN_KEYS).toHaveLength(8);
    expect(renderedHeaders()).toHaveLength(TASK_LIST_COLUMN_KEYS.length);
  });

  it("② 表头文本【有序数组】等于 8 个字面量（断顺序，不只断数量）", async () => {
    mockTasks([task({ task_id: "tsk_1", title: "Alpha", status: "doing" })]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_1");

    assertColumnFace(renderedHeaders());
    // 独立锚：这 8 个键在 zh 资源里映射到 8 个互不相同的文案。
    const copy = zh.projects as Record<string, string>;
    expect(
      COLUMN_KEYS.map((key) => copy[key.replace(/^projects\./, "")]),
    ).toEqual(COLUMN_ZH);
    expect(new Set(COLUMN_ZH).size).toBe(8);
  });

  it("③ 判别性对照（可复跑）：9 列 fixture 必须让同一断言辅助函数失败", async () => {
    // 正例：真实 DOM 走同一套辅助函数 → 通过。
    mockTasks([task({ task_id: "tsk_1", title: "Alpha", status: "doing" })]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_1");
    expect(() => assertColumnFace(renderedHeaders())).not.toThrow();

    // 反例：多一列（第 9 列「操作」）→ 必失败。
    const nineColumns = [...COLUMN_KEYS, "projects.taskListColumnActions"];
    expect(() => assertColumnFace(nineColumns)).toThrow();
    // 少一列 / 顺序错 → 也必失败（防止「只断数量」的退化）。
    expect(() => assertColumnFace(COLUMN_KEYS.slice(0, 7))).toThrow();
    expect(() => assertColumnFace([...COLUMN_KEYS].reverse())).toThrow();
  });
});

describe("status mapping single source (PLAN §9)", () => {
  const boardSource = readFileSync(resolve(__dirname, "Board.tsx"), "utf8");
  const viewSource = readFileSync(
    resolve(__dirname, "TaskListView.tsx"),
    "utf8",
  );

  it("the shared module owns the seven statuses in board order", () => {
    expect(COLUMN_STATUSES).toEqual([
      "planning",
      "todo",
      "doing",
      "review",
      "blocked",
      "done",
      "cancelled",
    ]);
    expect(new Set(Object.keys(STATUS_LABEL_KEYS))).toEqual(
      new Set(COLUMN_STATUSES),
    );
    expect(new Set(Object.keys(STATUS_COLORS))).toEqual(
      new Set(COLUMN_STATUSES),
    );
  });

  it.each([
    ["Board.tsx", boardSource],
    ["TaskListView.tsx", viewSource],
  ])("%s imports the mapping instead of copying it", (_name, source) => {
    expect(source).toContain('from "../utils/taskStatus"');
    for (const symbol of [
      "STATUS_LABEL_KEYS",
      "STATUS_COLORS",
      "COLUMN_STATUSES",
      "TERMINAL_STATUSES",
      "STATUS_TRANSITIONS",
    ]) {
      expect(source).not.toContain(`const ${symbol}`);
      expect(source).not.toContain(`function ${symbol}`);
    }
    // Neither file re-declares the transition table or its guard.
    expect(source).not.toContain("const isTransitionAllowed");
    expect(source).not.toContain("const asTaskStatus");
  });
});

describe("G1 分区卡片 + G4 标题可点（PLAN §4.1 FIND-11）", () => {
  it("AC-G1-1: 7 个分区各有一张卡片 testid，顺序 = COLUMN_STATUSES；空区仍保留卡片", async () => {
    mockTasks([
      task({ task_id: "tsk_1", title: "Alpha", status: "todo" }),
      task({ task_id: "tsk_2", title: "Beta", status: "doing", sort_order: 1 }),
    ]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_1");

    const ids = screen
      .getAllByTestId(/^task-section-card-/)
      .map((node) => node.getAttribute("data-testid") ?? "");
    assertSevenCards(ids);

    // 空区（5 个）仍有卡片 + 计数 0 + 空态文案。
    for (const status of COLUMN_STATUSES) {
      const hasTasks = status === "todo" || status === "doing";
      if (hasTasks) continue;
      expect(
        screen.getByTestId(`task-section-card-${status}`),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId(`task-section-count-${status}`),
      ).toHaveTextContent("0");
      expect(
        screen.getByTestId(`task-section-empty-${status}`),
      ).toHaveTextContent("projects.taskListEmpty");
    }
  });

  it("③ 判别性对照（可复跑）：少一张 / 多一张 / 顺序错 → 卡片辅助函数必须失败", async () => {
    mockTasks([task({ task_id: "tsk_1", title: "Alpha" })]);
    renderList();
    await screen.findByTestId("task-row-title-tsk_1");
    const ids = screen
      .getAllByTestId(/^task-section-card-/)
      .map((node) => node.getAttribute("data-testid") ?? "");
    // 正例：真实 DOM 走同一套辅助函数 → 通过。
    expect(() => assertSevenCards(ids)).not.toThrow();
    // 反例：缺一张 / 多一张 / 顺序颠倒 → 必失败。
    expect(() => assertSevenCards(ids.slice(0, 6))).toThrow();
    expect(() =>
      assertSevenCards([...ids, "task-section-card-extra"]),
    ).toThrow();
    expect(() => assertSevenCards([...ids].reverse())).toThrow();
  });

  it("AC-G4-1: 标题是可点元素（非编辑入口）→ 点击调用 onOpenTask(task_id)", async () => {
    mockTasks([task({ task_id: "tsk_77", title: "Open me" })]);
    const onEditTask = vi.fn();
    const { onOpenTask } = renderList(undefined, onEditTask);
    const title = await screen.findByTestId("task-row-title-tsk_77");

    // 可点元素：button 或 role=link（本轮推翻批次三 S8(b) 的「纯文本」）。
    const clickable =
      title.tagName === "BUTTON" || title.getAttribute("role") === "link";
    expect(clickable).toBe(true);
    // 键盘可达：可聚焦 + aria-label 含任务标题。
    expect(title).toHaveAttribute("tabindex", "0");
    expect(title.getAttribute("aria-label")).toContain("Open me");

    await userEvent.click(title);
    expect(onOpenTask).toHaveBeenCalledWith("tsk_77");
    // ★ 标题**不是**编辑入口（S8(b) 保留的另一半）。
    expect(onEditTask).not.toHaveBeenCalled();
  });

  it("AC-G4-1: 标题键盘可达 —— Enter 与 Space 都能触发 onOpenTask", async () => {
    mockTasks([
      task({ task_id: "tsk_e", title: "Enter me" }),
      task({ task_id: "tsk_s", title: "Space me", sort_order: 1 }),
    ]);
    const { onOpenTask } = renderList();
    const enterTitle = await screen.findByTestId("task-row-title-tsk_e");

    enterTitle.focus();
    expect(document.activeElement).toBe(enterTitle);
    await userEvent.keyboard("{Enter}");
    expect(onOpenTask).toHaveBeenCalledWith("tsk_e");

    const spaceTitle = screen.getByTestId("task-row-title-tsk_s");
    spaceTitle.focus();
    await userEvent.keyboard(" ");
    expect(onOpenTask).toHaveBeenCalledWith("tsk_s");
  });

  it("AC-G4-1: 未传 onOpenTask 时标题退化（无假交互），编辑按钮仍在", async () => {
    mockTasks([task({ task_id: "tsk_9", title: "No open" })]);
    const onEditTask = vi.fn();
    render(<TaskListView projectId="prj_1" onEditTask={onEditTask} />);
    const title = await screen.findByTestId("task-row-title-tsk_9");
    expect(title.tagName).toBe("SPAN");
    // 编辑入口仍是独立图标按钮（不受标题形态影响）。
    const edit = screen.getByTestId("task-row-edit-tsk_9");
    await userEvent.click(edit);
    expect(onEditTask).toHaveBeenCalledWith("tsk_9");
  });
});

describe("AC-14 卡面同源（列表视图 · PLAN §2.3 / SPEC R-01·R-02·R-04·R-09）", () => {
  const viewSource = readFileSync(
    resolve(__dirname, "TaskListView.tsx"),
    "utf8",
  );

  /** `Board.tsx` 导出的同一批映射函数（`AC-14`「同源」= 同一实现 + 同一批文案键）。 */
  const BOARD_MAPPING_SYMBOLS = [
    "isAgentLike",
    "memberKey",
    "priorityBars",
    "priorityLabelKey",
    "priorityTier",
    "resolveAssigneeName",
    "shortAssigneeId",
    "tagsPreview",
  ];

  /** 某任务行的优先级格（`task-card-priority`）。 */
  function priorityCell(taskId: string): HTMLElement {
    return within(screen.getByTestId(`task-row-${taskId}`)).getByTestId(
      "task-card-priority",
    );
  }

  /** 优先级柱：总数恒 3；亮格 = 带同一个 `styles.isOn` 的柱。 */
  function barsOf(cell: HTMLElement): { total: number; lit: number } {
    const bars = Array.from(
      cell.querySelectorAll<HTMLElement>("[data-priority-bars] > *"),
    );
    return {
      total: bars.length,
      lit: bars.filter((bar) => bar.classList.contains(styles.isOn)).length,
    };
  }

  /** 某个任务行（负责人的头像 / 徽标 / 名称与标签 chips 都在行内）。 */
  function taskRow(taskId: string) {
    return within(screen.getByTestId(`task-row-${taskId}`));
  }

  function member(
    subject_type: ProjectMember["subject_type"],
    subject_id: string,
    name: string | null,
  ): ProjectMember {
    return {
      subject_type,
      subject_id,
      user_id: null,
      role: "member",
      created_at: 0,
      name,
    };
  }

  it("AC-14 同源：四类元素复用 Board 的 8 个映射函数，不另立第二套", () => {
    // 具名导入同一实现（不是本地副本、不是第二套口径）。
    expect(viewSource).toContain('from "./Board"');
    for (const symbol of BOARD_MAPPING_SYMBOLS) {
      expect(viewSource).toMatch(
        new RegExp(
          `import\\s*\\{[\\s\\S]*?\\b${symbol}\\b[\\s\\S]*?\\}\\s*from "\\./Board"`,
        ),
      );
      // 本地**不得**再声明一份：第二套实现正是 `AC-14` 要禁止的。
      expect(viewSource).not.toContain(`function ${symbol}`);
      expect(viewSource).not.toContain(`const ${symbol}`);
    }
  });

  it("AC-14 标签同源：≤3 chips + `+N`，`+N` 的 Tooltip 列出**全部**标签名（FIND-11 / S-6）", async () => {
    const four = ["alpha", "beta", "gamma", "delta"].map((name, index) => ({
      tag_id: `tag_${index}`,
      name,
      color: "#ff0000",
    }));

    mockTasks([
      task({ task_id: "tsk_tags4", title: "Four tags", tags: four }),
      task({
        task_id: "tsk_tags3",
        title: "Three tags",
        tags: four.slice(0, 3),
        sort_order: 1,
      }),
      task({ task_id: "tsk_tags0", title: "No tags", tags: [], sort_order: 2 }),
    ]);
    renderList();
    await screen.findByTestId("task-row-tsk_tags4");

    // 4 个标签 ⇒ chips 容器存在 · 前 3 个成 chip · `+N` = `+1`（`R-04` 上限 3）。
    const fourTags = taskRow("tsk_tags4");
    const chips = fourTags.getByTestId("task-card-chips");
    expect(chips).toBeInTheDocument();
    expect(
      Array.from(chips.querySelectorAll(`.${styles.cardChip}`)).map(
        (chip) => chip.textContent,
      ),
    ).toEqual(["alpha", "beta", "gamma"]);
    const more = fourTags.getByTestId("task-card-chip-more");
    expect(more).toHaveTextContent("+1");

    // Tooltip 列**全部** 4 个标签名 —— 含已显示的那 3 个；只列剩余项（`delta`）即失败。
    await userEvent.hover(more);
    await waitFor(() => {
      expect(document.querySelector(".ant-tooltip-inner")).toBeTruthy();
    });
    const tip = document.querySelector(".ant-tooltip-inner") as HTMLElement;
    for (const name of ["alpha", "beta", "gamma", "delta"]) {
      expect(tip).toHaveTextContent(name);
    }

    // 3 个标签 ⇒ 无 `+N`；0 个标签 ⇒ chips 整块不渲染（`B-10` / `B-01`）。
    expect(
      taskRow("tsk_tags3").queryByTestId("task-card-chip-more"),
    ).toBeNull();
    expect(
      taskRow("tsk_tags3").getByTestId("task-card-chips"),
    ).toBeInTheDocument();
    expect(taskRow("tsk_tags0").queryByTestId("task-card-chips")).toBeNull();
  });

  it("AC-14 优先级同源：3 柱 + 档位文案键（低/中/高 · 未知值 `String(p)` + 0 格亮）", async () => {
    mockTasks([
      task({ task_id: "tsk_p_low", title: "Low", priority: 1 }),
      task({
        task_id: "tsk_p_neg1",
        title: "Negative one",
        priority: -1,
        sort_order: 1,
      }),
      task({
        task_id: "tsk_p_neg99",
        title: "Negative 99",
        priority: -99,
        sort_order: 2,
      }),
      task({ task_id: "tsk_p_mid", title: "Mid", priority: 2, sort_order: 3 }),
      task({
        task_id: "tsk_p_high",
        title: "High",
        priority: 3,
        sort_order: 4,
      }),
      task({
        task_id: "tsk_p_float",
        title: "Float",
        priority: 1.5,
        sort_order: 5,
      }),
      task({
        task_id: "tsk_p_nan",
        title: "Not a number",
        priority: Number.NaN,
        sort_order: 6,
      }),
      task({
        task_id: "tsk_p_null",
        title: "Null priority",
        priority: null as unknown as number,
        sort_order: 7,
      }),
      task({
        task_id: "tsk_p_undef",
        title: "Undefined priority",
        priority: undefined as unknown as number,
        sort_order: 8,
      }),
      task({
        task_id: "tsk_p_str",
        title: "String priority",
        priority: "abc" as unknown as number,
        sort_order: 9,
      }),
    ]);
    renderList();
    await screen.findByTestId("task-row-tsk_p_low");

    // 整数 `≤1`（含 `-1` / `-99`）⇒ **低**（1 格亮）—— 不得按未知值处理（`B-02` 反向格）。
    for (const id of ["tsk_p_low", "tsk_p_neg1", "tsk_p_neg99"]) {
      const cell = priorityCell(id);
      expect(barsOf(cell)).toEqual({ total: 3, lit: 1 });
      expect(cell).toHaveTextContent("common.cardPriorityLow");
    }
    // `=== 2` ⇒ 中（2 格亮）；`≥3` ⇒ 高（3 格亮）。
    expect(barsOf(priorityCell("tsk_p_mid"))).toEqual({ total: 3, lit: 2 });
    expect(priorityCell("tsk_p_mid")).toHaveTextContent(
      "common.cardPriorityMid",
    );
    expect(barsOf(priorityCell("tsk_p_high"))).toEqual({ total: 3, lit: 3 });
    expect(priorityCell("tsk_p_high")).toHaveTextContent(
      "common.cardPriorityHigh",
    );

    // 未知值 5 格（`R-03`）：原值 `String(p)` · **0 格亮** · 非空文本 · 不出现 `undefined` / `null`。
    const unknown: Array<[string, string]> = [
      ["tsk_p_float", "1.5"],
      // ★ 全文**唯一**允许出现 `NaN` 字样的格（`B-02`：即 `String(NaN)`）。
      ["tsk_p_nan", "NaN"],
      ["tsk_p_null", "—"],
      ["tsk_p_undef", "—"],
      ["tsk_p_str", "abc"],
    ];
    for (const [id, text] of unknown) {
      const cell = priorityCell(id);
      expect(barsOf(cell)).toEqual({ total: 3, lit: 0 });
      expect(cell.textContent).toContain(text);
      expect((cell.textContent ?? "").trim()).not.toBe("");
      expect(cell.textContent).not.toContain("undefined");
      expect(cell.textContent).not.toContain("null");
    }
  });

  it("AC-14 头像 / Agent 徽标同源：命中成员 ⇒ 首字母 + 名称；仅 `agent`/`team` 有徽标", async () => {
    mockTasks(
      [
        task({
          task_id: "tsk_a_agent",
          title: "Agent assignee",
          assignee_type: "agent",
          assignee_id: "agent01",
        }),
        task({
          task_id: "tsk_a_team",
          title: "Team assignee",
          assignee_type: "team",
          assignee_id: "team01",
          sort_order: 1,
        }),
        task({
          task_id: "tsk_a_user",
          title: "User assignee",
          assignee_type: "user",
          assignee_id: "3",
          sort_order: 2,
        }),
        task({
          task_id: "tsk_a_unknown",
          title: "Unknown assignee",
          assignee_type: "bot",
          assignee_id: "ghost_1",
          sort_order: 3,
        }),
      ],
      [
        member("agent", "agent01", "Worker One"),
        member("team", "team01", "Squad"),
        member("user", "3", "张三"),
      ],
    );
    renderList();
    await screen.findByTestId("task-row-tsk_a_agent");

    // 命中成员 ⇒ 头像 = 名称**首字母**（不再退化为类型图标），名称文本 = `member.name`。
    await waitFor(() => {
      expect(
        taskRow("tsk_a_agent").getByTestId("task-card-assignee-avatar"),
      ).toHaveTextContent("W");
    });
    expect(
      taskRow("tsk_a_agent")
        .getByTestId("task-card-assignee-avatar")
        .querySelector("svg"),
    ).toBeNull();
    expect(taskRow("tsk_a_agent").getByText("Worker One")).toBeInTheDocument();
    expect(
      taskRow("tsk_a_user").getByTestId("task-card-assignee-avatar"),
    ).toHaveTextContent("张");

    // 徽标**仅** `agent` / `team`（`isAgentLike`）：`user` 与未知类型都没有。
    expect(
      taskRow("tsk_a_agent").getByTestId("task-card-agent-badge"),
    ).toBeInTheDocument();
    expect(
      taskRow("tsk_a_team").getByTestId("task-card-agent-badge"),
    ).toBeInTheDocument();
    expect(
      taskRow("tsk_a_user").queryByTestId("task-card-agent-badge"),
    ).toBeNull();
    expect(
      taskRow("tsk_a_unknown").queryByTestId("task-card-agent-badge"),
    ).toBeNull();
  });

  it("AC-14 兜底同源：成员表未命中 ⇒ 类型图标 + `type:#末 6 位`，页面无 undefined/NaN/null", async () => {
    mockTasks(
      [
        task({
          task_id: "tsk_f_agent",
          title: "Agent fallback",
          assignee_type: "agent",
          assignee_id: "abcdef123456",
        }),
        task({
          task_id: "tsk_f_user",
          title: "User fallback",
          assignee_type: "user",
          assignee_id: "3",
          sort_order: 1,
        }),
        task({
          task_id: "tsk_f_unknown",
          title: "Unknown fallback",
          assignee_type: "bot",
          assignee_id: "ghost_1",
          sort_order: 2,
        }),
      ],
      // 成员表**非空**但主键不匹配 ⇒ 走 R-09 兜底（排除「表为空」这一退化解释）。
      [member("agent", "someone_else", "Other")],
    );
    const { view } = renderList();
    await screen.findByTestId("task-row-tsk_f_agent");

    const expected: Array<[string, string, string]> = [
      // `abcdef123456` ⇒ 末 6 位 `123456`；`ghost_1`（7 位）⇒ 末 6 位 `host_1`。
      ["tsk_f_agent", "agent:#123456", "lucide-bot"],
      ["tsk_f_user", "user:#3", "lucide-user"],
      ["tsk_f_unknown", "bot:#host_1", "lucide-circle-question-mark"],
    ];
    for (const [id, text, icon] of expected) {
      const row = taskRow(id);
      const avatar = row.getByTestId("task-card-assignee-avatar");
      // 兜底 = **类型图标**（不是首字母、不是空头像）+ `type:#短id` 文本。
      expect(avatar.querySelector(`svg.${icon}`)).not.toBeNull();
      await waitFor(() => {
        expect(row.getByText(text)).toBeInTheDocument();
      });
      expect(row.getByTestId("task-card-assignee-avatar")).toBeInTheDocument();
    }

    // `B-17`（显示名族）：页面 HTML 不含 `undefined` / `NaN` / `null`。
    for (const needle of ["undefined", "NaN", "null"]) {
      expect(view.container.innerHTML).not.toContain(needle);
    }
  });

  it('AC-14 键盘可达：标题是原生 `<button type="button">` ⇒ Enter / Space 各触发**恰 1 次**', async () => {
    mockTasks([task({ task_id: "tsk_k", title: "Keyboard reachable" })]);
    const { onOpenTask } = renderList();
    const title = await screen.findByTestId("task-row-title-tsk_k");

    // 原生 button 语义（不是 `div` + `role` / `div` + 手搓 keydown）。
    expect(title.tagName).toBe("BUTTON");
    expect(title).toHaveAttribute("type", "button");

    title.focus();
    expect(document.activeElement).toBe(title);
    await userEvent.keyboard("{Enter}");
    expect(onOpenTask).toHaveBeenCalledTimes(1);
    expect(onOpenTask).toHaveBeenLastCalledWith("tsk_k");

    await userEvent.keyboard(" ");
    expect(onOpenTask).toHaveBeenCalledTimes(2);
    expect(onOpenTask).toHaveBeenLastCalledWith("tsk_k");
  });
});
