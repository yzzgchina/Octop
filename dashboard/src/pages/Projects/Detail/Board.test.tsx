import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectMember, ProjectTask } from "../../../api/modules/projects";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import Board from "./Board";
import styles from "./Board.module.less";

const createTaskMock = vi.fn();
const updateTaskMock = vi.fn();
const timelineMock = vi.fn();
const dispatchTaskMock = vi.fn();
const messageSuccessMock = vi.fn();
const messageErrorMock = vi.fn();

/**
 * `members` 的替身（B-12 / B-13 的计数与失败分支都挂在它上面）。
 *
 * 默认**不落地**：旧用例只关心看板结构，成员表若在用例体之外落地就会触发一次
 * 「not wrapped in act」告警（本仓其他套件均为 0 条）。需要成员名的用例各自
 * `mockResolvedValue(...)`，并在 `act` 内冲掉回调 —— 看板侧无成员名时走
 * `type:#短id` 兜底，与旧行为一致。
 */
const listMembersMock = vi.fn(() => new Promise<ProjectMember[]>(() => {}));

/** 剪贴板唯一入口（`dashboard/src/utils/copyText.ts`）的替身。 */
const copyTextMock = vi.fn();

/**
 * 「可切语言」的 i18n 开关（仅本文件）。默认 `"key"` 模式与
 * `src/test/setup.ts` 的全局 key-mock **逐字等价** ⇒ 既有用例的 key 断言不受影响；
 * 只有菜单文案那两条用例切到 `"zh"` / `"en"`，按真 JSON 逐字断言
 * （`SPEC · B-04`：菜单项文本集合逐字等于 `{打开详情, 复制链接}`）。
 */
const localeMode = vi.hoisted(() => ({
  current: "key" as "key" | "zh" | "en",
}));

vi.mock("@/utils/antdMessage", () => ({
  message: {
    success: (...args: unknown[]) => messageSuccessMock(...args),
    error: (...args: unknown[]) => messageErrorMock(...args),
    warning: vi.fn(),
  },
}));

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

/**
 * `FIND-06`：只 mock 仓内唯一剪贴板入口，**绝不断** `navigator.clipboard.writeText`
 * （jsdom 下 `isSecureContext` / `navigator.clipboard` / `document.execCommand` 全为
 * `undefined` ⇒ 那种断言永不触发）。
 */
vi.mock("../../../utils/copyText", () => ({
  copyText: (...args: unknown[]) => copyTextMock(...args),
}));

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  const fs = await import("node:fs");
  const path = await import("node:path");
  const bundles = {
    zh: JSON.parse(
      fs.readFileSync(
        path.resolve(process.cwd(), "src/locales/zh.json"),
        "utf-8",
      ),
    ) as Record<string, unknown>,
    en: JSON.parse(
      fs.readFileSync(
        path.resolve(process.cwd(), "src/locales/en.json"),
        "utf-8",
      ),
    ) as Record<string, unknown>,
  };

  /** dot-path 取值（真文案只在 locales/*.json 里）。 */
  const lookup = (locale: "zh" | "en", key: string): string | undefined => {
    let node: unknown = bundles[locale];
    for (const part of key.split(".")) {
      if (node === null || typeof node !== "object") return undefined;
      node = (node as Record<string, unknown>)[part];
    }
    return typeof node === "string" ? node : undefined;
  };

  type TOptions = Record<string, unknown> & { defaultValue?: string };

  const interpolate = (template: string, options?: TOptions): string =>
    options
      ? template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
          name in options ? String(options[name]) : match,
        )
      : template;

  return {
    ...actual,
    useTranslation: () => ({
      t: (
        key: string,
        fallback?: string | TOptions,
        options?: TOptions,
      ): string => {
        if (localeMode.current !== "key") {
          const optionsOf =
            fallback !== null && typeof fallback === "object"
              ? fallback
              : options;
          const translated = lookup(localeMode.current, key);
          if (translated !== undefined)
            return interpolate(translated, optionsOf);
        }
        // 以下是 `src/test/setup.ts` 全局 mock 的逐字等价行为（默认模式）。
        if (typeof fallback === "string") return interpolate(fallback, options);
        if (fallback && typeof fallback === "object") {
          return interpolate(fallback.defaultValue ?? key, fallback);
        }
        return key;
      },
      i18n: {
        language: localeMode.current === "key" ? "zh" : localeMode.current,
        changeLanguage: () => Promise.resolve(),
      },
    }),
    Trans: ({ children }: { children?: React.ReactNode }) => children,
  };
});

vi.mock("../../../api/modules/projects", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/projects")
  >();
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      createTask: (...args: unknown[]) => createTaskMock(...args),
      updateTask: (...args: unknown[]) => updateTaskMock(...args),
      dispatchTask: (...args: unknown[]) => dispatchTaskMock(...args),
      timeline: (...args: unknown[]) => timelineMock(...args),
      // 反 N+1 计数（B-12）与失败分支（B-13）都挂在这个替身上。
      listMembers: (...args: unknown[]) => listMembersMock(...args),
    },
  };
});

/** Column order plus the frozen adjacency (PLAN §1.2). */
const STATUS_KEYS = [
  "projects.taskStatusPlanning",
  "projects.taskStatusTodo",
  "projects.taskStatusDoing",
  "projects.taskStatusReview",
  "projects.taskStatusBlocked",
  "projects.taskStatusDone",
  "projects.taskStatusCancelled",
];

const START_AT = 1_750_000_000;

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
    task_number: null,
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

/**
 * 路由探针（`FIND-07` 冻结）：`MemoryRouter` 不写 `window.location` ⇒ 断言内建导航
 * 只能读 `useLocation()`（`window.location.search` 恒为空串，是假判据）。
 */
function RouterSearchProbe() {
  const location = useLocation();
  return <span data-testid="router-search">{location.search}</span>;
}

interface RenderBoardOptions {
  canEdit?: boolean;
  /** 传入即走 prop 回调；不传则走 `Board` 内建 URL 导航（`B-14`）。 */
  onOpenTask?: (taskId: string) => void;
  /** A4 选中态数据通道（PLAN §A4.3）：`?task=` 由 `index.tsx` 解析后传入。 */
  selectedTaskId?: string;
  /** 起始 URL（默认 `?tab=plan`）；用于证明 `Board` 不自行读 `?task=`。 */
  initialEntry?: string;
}

/**
 * 看板渲染入口。`Board` 内建 `useSearchParams`（PLAN §5.1）⇒ **必须**在 Router
 * 上下文内渲染；探针与看板同挂一个 `MemoryRouter`，使内建导航可观测。
 */
function renderBoard(
  tasks: ProjectTask[] = [],
  options: RenderBoardOptions = {},
) {
  const onChanged = vi.fn();
  const view = render(
    <MemoryRouter
      initialEntries={[options.initialEntry ?? "/projects/prj_1?tab=plan"]}
    >
      <Board
        projectId="prj_1"
        tasks={tasks}
        canEdit={options.canEdit ?? true}
        onChanged={onChanged}
        onOpenTask={options.onOpenTask}
        selectedTaskId={options.selectedTaskId}
      />
      <RouterSearchProbe />
    </MemoryRouter>,
  );
  return { onChanged, ...view };
}

/** Column add buttons render in `COLUMN_STATUSES` order. */
const addButtons = () =>
  screen.getAllByRole("button", { name: "projects.boardAdd" });

/* ---------------------------------------------------------------------------
 * T03 新增用例的共用件（SPEC §7.4 · AC-01..AC-17 / B-01..B-20）
 * ------------------------------------------------------------------------- */

/** 成员表行；默认命中 `agent:agent-a`（`memberKey` 的口径见 Board.tsx）。 */
function member(overrides: Partial<ProjectMember> = {}): ProjectMember {
  return {
    subject_type: "agent",
    subject_id: "agent-a",
    user_id: null,
    role: "member",
    created_at: START_AT,
    name: "Bot One",
    ...overrides,
  };
}

/** 标签造数（判据只看条数与顺序，名称/颜色与断言无关）。 */
function tagList(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    tag_id: `tag_${index + 1}`,
    name: `标签${index + 1}`,
    color: "#4f46e5",
  }));
}

/**
 * jsdom 没有 HTML5 拖拽数据层。契约冻结的 `handleCardDragStart` 会读
 * `event.dataTransfer.setData` ⇒ `fireEvent.dragStart` **必须**带这个桩，
 * 否则抛 `Cannot read properties of undefined (reading 'setData')`。
 */
const dragDataTransfer = () => ({
  setData: vi.fn(),
  getData: vi.fn(),
  clearData: vi.fn(),
  setDragImage: vi.fn(),
  effectAllowed: "",
  dropEffect: "",
  types: [] as string[],
  files: [] as unknown[],
});

/** 卡片根节点（`B-23` 口径：子树内含 `task-card-priority` 的 `[draggable]`）。 */
function cardRootOf(title: string): HTMLElement {
  const root = screen.getByText(title).closest("[draggable]");
  if (root === null) throw new Error(`card root not found: ${title}`);
  return root as HTMLElement;
}

/**
 * `FIND-33` 判据本体（作用域 = **卡片根**）：行③ 标签容器 + 行④ 附加项（`.cardMetaItem`）计数。
 *
 * 旧写法（把定位节点 `closest` 到最近的 `div`）作用域是**行②** `.cardTitleRow`（`card`
 * 命中标题
 * `<button>`，其最近 `div` 的唯一子节点就是它自己）—— chip 在**兄弟**行 `.cardChips`
 * （`Board.tsx:641-662`）、`.cardMetaItem` 也在兄弟行 `.cardBottomRow`（`:664-733`）⇒
 * 作用域内**永不含**附加行，删掉行③/行④ 照样绿（恒真）。故判据必须自 `cardRootOf` 起算。
 */
function metadataRowsOf(title: string) {
  const root = cardRootOf(title);
  return {
    chips: within(root).queryByTestId("task-card-chips"),
    metaItems: root.querySelectorAll('[class*="cardMetaItem"]').length,
  };
}

/**
 * `B-23` ③（`SPEC.md` @119 / @225）的收集函数：卡片根子树内「**无文本** ∧ **无子元素** ∧
 * **无 `data-testid`**」的 `div`/`span`；4 条排除项逐字落地：ⓐ `aria-hidden="true"` 的
 * 图标/装饰承载节点及其子树；ⓑ antd 内部节点（祖先链含 `[class*="ant-"]`）；
 * ⓒ `[data-priority-bars]` 子树（`R-02` 的 3 根柱**本就是**无文本空 `span`）；
 * ⓓ `[data-testid="task-card-chips"]` 内**无文本的 `span`**（`R-04` / `PLAN.md` §4.3
 * 的 3px 圆点 = 纯造型节点）。
 */
function emptyShellNodes(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("div, span")).filter(
    (node) =>
      node.closest('[aria-hidden="true"]') === null &&
      node.closest('[class*="ant-"]') === null &&
      node.closest("[data-priority-bars]") === null &&
      !(
        node.tagName === "SPAN" &&
        node.closest('[data-testid="task-card-chips"]') !== null
      ) &&
      (node.textContent ?? "").trim() === "" &&
      node.children.length === 0 &&
      !node.hasAttribute("data-testid"),
  );
}

/** 行②标题按钮（`AC-15` 的三格：click / Enter / Space）。 */
const titleButtonOf = (title = "Task one"): HTMLElement =>
  screen.getByRole("button", { name: title });

/** 打开 `⋯` 菜单并返回菜单项（顺序固定 = 打开详情 → 复制链接）。 */
async function openCardMenu(): Promise<HTMLElement[]> {
  fireEvent.click(screen.getByTestId("task-card-menu"));
  return screen.findAllByRole("menuitem");
}

/** 优先级柱亮格数（CSS Module 名形如 `_isOn_<hash>`，故按 `isOn` 判）。 */
const litBars = (): number =>
  Array.from(
    screen
      .getByTestId("task-card-priority")
      .querySelectorAll("[data-priority-bars] > *"),
  ).filter((bar) => bar.className.includes("isOn")).length;

/** 优先级文案位（去掉图标与空白）。 */
const priorityText = (): string =>
  (screen.getByTestId("task-card-priority").textContent ?? "").trim();

/** 单主体位整块的文本（头像节点的父级 = `.cardOwnerAgent`）。 */
const assigneeBlockText = (): string =>
  screen.getByTestId("task-card-assignee-avatar").parentElement?.textContent ??
  "";

/** 冲掉 `useProjectMembers` 的 promise 回调（成员名要等它落地才渲染）。 */
async function flushMemberLoad() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** 复位 `members` 替身为「不落地」（理由见 `listMembersMock` 的注释）。 */
function resetListMembers() {
  listMembersMock.mockReset();
  listMembersMock.mockImplementation(
    () => new Promise<ProjectMember[]>(() => {}),
  );
}

/** 写动词（`B-04`）：菜单里出现任何一个即为越权入口。 */
const MENU_WRITE_VERBS =
  /删除|编辑|派单|指派|移动|新建|创建|更新|归档|复制编号|delete|edit|dispatch|move|create|update|remove|number/i;

describe("Board 7 列与全部列可建", () => {
  beforeEach(() => {
    createTaskMock.mockReset();
    updateTaskMock.mockReset();
    timelineMock.mockReset();
    dispatchTaskMock.mockReset();
    messageSuccessMock.mockReset();
    messageErrorMock.mockReset();
    createTaskMock.mockResolvedValue(task());
    dispatchTaskMock.mockResolvedValue(task());
  });

  it("AC-U-6/S-1: 7 列按序渲染，planning 在列首且不受「显示终态」开关影响", async () => {
    const user = userEvent.setup();
    renderBoard();

    for (const key of STATUS_KEYS) {
      expect(screen.getByText(key)).toBeInTheDocument();
    }
    // planning 在最左（列头 Tag 的 DOM 顺序）。
    const headers = STATUS_KEYS.map((key) => screen.getByText(key));
    for (let i = 1; i < headers.length; i += 1) {
      expect(
        headers[i - 1].compareDocumentPosition(headers[i]) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }

    // 关闭终态开关后 planning 仍可见（S-1：planning 不进 TERMINAL_STATUSES）。
    await user.click(
      screen.getByRole("checkbox", { name: "projects.boardShowTerminal" }),
    );
    expect(screen.getByText("projects.taskStatusPlanning")).toBeInTheDocument();
    expect(
      screen.queryByText("projects.taskStatusDone"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("projects.taskStatusCancelled"),
    ).not.toBeInTheDocument();
  });

  it("AC-U-6/AC-U-7: 7 列每列都有可用的建任务入口（旧限制已作废）", () => {
    renderBoard();

    const buttons = addButtons();
    expect(buttons.length).toBe(7);
    for (const button of buttons) {
      expect(button).toBeEnabled();
    }
  });

  it("AC-U-7/AC-U-9: 内联快速创建保留，且只发一次请求携带目标列状态", async () => {
    const user = userEvent.setup();
    const { onChanged } = renderBoard();

    // 第 3 个入口 = doing 列（列序 planning, todo, doing, …）。
    await user.click(addButtons()[2]);
    const input = screen.getByPlaceholderText("projects.boardAddPlaceholder");
    expect(input).toHaveFocus();
    await user.type(input, "Ship it{Enter}");

    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
    expect(createTaskMock).toHaveBeenCalledWith("prj_1", {
      title: "Ship it",
      status: "doing",
    });
    // 旧的「先建 todo 再 move」两段式已废除。
    expect(updateTaskMock).not.toHaveBeenCalled();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  const COLUMN_ADD = [
    "planning",
    "todo",
    "doing",
    "review",
    "blocked",
    "done",
    "cancelled",
  ] as const;

  /** The full create dialog: antd renders it as a dialog role. */
  const dialog = () => within(screen.getByRole("dialog"));

  const statusChipText = () =>
    dialog().getByRole("button", { name: "projects.chipStatus" }).textContent ??
    "";

  it("AC-D-1: 列头「+ 新建任务」打开 TaskCreateModal（create 模式），7 列各有入口", async () => {
    const user = userEvent.setup();
    renderBoard();

    for (const status of COLUMN_ADD) {
      expect(
        screen.getByTestId(`board-column-add-${status}`),
      ).toBeInTheDocument();
    }
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("board-column-add-doing"));

    // 打开的是复用弹窗（标题输入 + 描述），不是内联输入。
    expect(
      dialog().getByPlaceholderText("projects.createTaskTitle"),
    ).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("projects.boardAddPlaceholder"),
    ).not.toBeInTheDocument();
    expect(createTaskMock).not.toHaveBeenCalled();
  });

  it("AC-D-2: doing 列 → 弹窗 status 初值 == doing；planning 列 → planning（非常量）", async () => {
    const user = userEvent.setup();
    renderBoard();

    await user.click(screen.getByTestId("board-column-add-doing"));
    expect(statusChipText()).toContain("projects.taskStatusDoing");

    await user.click(dialog().getByRole("button", { name: "common.cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    // 换一列：预填跟随该列，而不是写死的 doing。
    await user.click(screen.getByTestId("board-column-add-planning"));
    expect(statusChipText()).toContain("projects.taskStatusPlanning");
  });

  it("AC-D-2: 工具栏入口仍用弹窗默认 status（不被上一次列头预填污染）", async () => {
    const user = userEvent.setup();
    renderBoard();

    await user.click(screen.getByTestId("board-column-add-doing"));
    expect(statusChipText()).toContain("projects.taskStatusDoing");
    await user.click(dialog().getByRole("button", { name: "common.cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    await user.click(
      screen.getByRole("button", { name: "projects.createTaskSubmit" }),
    );
    // 弹窗自身默认 planning（toValues(undefined)）≠ 刚才那一列。
    expect(statusChipText()).toContain("projects.taskStatusPlanning");
  });

  it("AC-D-2/AC-D-4: 列头入口的预填进入提交载荷，并在成功后重读（非本地乐观）", async () => {
    const user = userEvent.setup();
    const { onChanged } = renderBoard();

    await user.click(screen.getByTestId("board-column-add-doing"));
    await user.type(
      dialog().getByPlaceholderText("projects.createTaskTitle"),
      "From header",
    );
    // 弹窗提交按钮的文本还带快捷键提示（⌘↵）→ 用正则匹配包含关系。
    await user.click(
      dialog().getByRole("button", { name: /projects\.createTaskSubmit/ }),
    );

    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
    expect(createTaskMock).toHaveBeenCalledWith(
      "prj_1",
      expect.objectContaining({ title: "From header", status: "doing" }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("AC-D-3: 双入口并存 —— 7 个内联入口（boardAdd 精确名）与 7 个列头入口同在", async () => {
    renderBoard();

    // 内联入口的可访问名仍是 projects.boardAdd（列头按钮带上了列名，故不计入）。
    expect(addButtons().length).toBe(7);
    for (const button of addButtons()) expect(button).toBeEnabled();
    for (const status of COLUMN_ADD) {
      expect(screen.getByTestId(`board-column-add-${status}`)).toBeEnabled();
    }
  });

  it("内联创建的 Esc 取消不发请求", async () => {
    const user = userEvent.setup();
    renderBoard();

    await user.click(addButtons()[0]);
    const input = screen.getByPlaceholderText("projects.boardAddPlaceholder");
    await user.type(input, "Discarded{Escape}");
    expect(
      screen.queryByPlaceholderText("projects.boardAddPlaceholder"),
    ).not.toBeInTheDocument();
    expect(createTaskMock).not.toHaveBeenCalled();
  });

  it("AC-U-13/AC-U-14/AC-CF-5: 卡片展示标签、自定义字段值与开始日期（服务端时区）", () => {
    renderBoard([
      task({
        tags: [{ tag_id: "tag_1", name: "紧急", color: "#ff0000" }],
        custom_fields: [
          {
            field_id: "fld_1",
            key: "note",
            label: "备注",
            type: "text",
            value: "hello",
          },
          {
            field_id: "fld_2",
            key: "when",
            label: "日期字段",
            type: "date",
            value: START_AT,
          },
        ],
        start_at: START_AT,
      }),
    ]);

    expect(screen.getByText("紧急")).toBeInTheDocument();
    expect(screen.getByText("备注：hello")).toBeInTheDocument();
    expect(
      screen.getByText(`日期字段：${formatServerDateTime(START_AT, "UTC")}`),
    ).toBeInTheDocument();
    // 开始日期用同一个服务端时区格式化助手渲染。
    expect(
      screen.getAllByText(formatServerDateTime(START_AT, "UTC")).length,
    ).toBeGreaterThanOrEqual(1);
  });

  // `FIND-33` / `PLAN.md` §8 `R4`：作用域必须是**卡片根**（`cardRootOf`），且判据必须
  // **可失败** —— 旧写法（标题节点 `closest` 到最近的 `div`）落在行② `.cardTitleRow` 上，
  // chip（行③）与 `.cardMetaItem`（行④）都在**兄弟**行 ⇒ 作用域内永不含它们，
  // 删掉整条附加行也照样绿（恒真）。两格成对：负向 + 同一查询的正向对照。
  it("卡片元数据为空时不渲染附加行（FIND-33：作用域 = 卡片根）", () => {
    renderBoard([task()]);

    // tags=[] ∧ custom_fields=[] ∧ 无 start_at/due_at ⇒ 行③ 容器与行④ 附加项都不渲染。
    expect(metadataRowsOf("Task one")).toEqual({ chips: null, metaItems: 0 });
  });

  it("FIND-33 判别性对照: 带标签 / 带自定义字段的卡 ⇒ 同一查询必须命中（证明上一格非恒真）", () => {
    renderBoard([
      task({
        tags: [{ tag_id: "tag_1", name: "紧急", color: "#ff0000" }],
        custom_fields: [
          {
            field_id: "fld_1",
            key: "note",
            label: "备注",
            type: "text",
            value: "hello",
          },
        ],
        start_at: START_AT,
      }),
    ]);

    const rows = metadataRowsOf("Task one");
    expect(rows.chips).not.toBeNull();
    expect(rows.chips?.textContent).toContain("紧急");
    expect(rows.metaItems).toBeGreaterThan(0);
  });
});

describe("Board 派单（FIND-12 / PLAN §8.1）", () => {
  const dispatchButtons = () =>
    screen.getAllByRole("button", { name: "projects.boardDispatch" });

  beforeEach(() => {
    createTaskMock.mockReset().mockResolvedValue(task());
    dispatchTaskMock.mockReset().mockResolvedValue(task());
    timelineMock.mockReset();
    messageSuccessMock.mockReset();
    messageErrorMock.mockReset();
  });

  it("agent 负责人：按钮可用，点击调用 dispatchTask(projectId, taskId) 并提示成功", async () => {
    const user = userEvent.setup();
    const { onChanged } = renderBoard([
      task({ assignee_type: "agent", assignee_id: "agent-a" }),
    ]);

    const button = dispatchButtons()[0];
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute("title", "projects.boardDispatch");

    await user.click(button);

    await waitFor(() =>
      expect(dispatchTaskMock).toHaveBeenCalledWith("prj_1", "tsk_1"),
    );
    expect(dispatchTaskMock).toHaveBeenCalledTimes(1);
    expect(messageSuccessMock).toHaveBeenCalledTimes(1);
    expect(messageErrorMock).not.toHaveBeenCalled();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("team 负责人同样可用", async () => {
    const user = userEvent.setup();
    renderBoard([task({ assignee_type: "team", assignee_id: "team-1" })]);

    const button = dispatchButtons()[0];
    expect(button).toBeEnabled();
    await user.click(button);
    await waitFor(() =>
      expect(dispatchTaskMock).toHaveBeenCalledWith("prj_1", "tsk_1"),
    );
  });

  it("user / 空负责人：disabled + title 复用 apiErrors 键，且不调用", async () => {
    const user = userEvent.setup();
    renderBoard([
      task({
        task_id: "tsk_user",
        title: "User task",
        assignee_type: "user",
        assignee_id: "7",
      }),
      task({
        task_id: "tsk_none",
        title: "No assignee",
        assignee_type: null,
        assignee_id: null,
      }),
    ]);

    const buttons = dispatchButtons();
    expect(buttons.length).toBe(2);
    for (const button of buttons) {
      expect(button).toBeDisabled();
      // 复用既有 apiErrors 键作为提示，不新增第二个文案键。
      expect(button).toHaveAttribute(
        "title",
        "apiErrors.PROJECT_TASK_DISPATCH_INVALID",
      );
    }

    await user.click(buttons[0]);
    await user.click(buttons[1]);
    expect(dispatchTaskMock).not.toHaveBeenCalled();
    expect(messageErrorMock).not.toHaveBeenCalled();
  });

  it("失败路径（409）：给出错误提示且不抛异常，仍刷新看板", async () => {
    const user = userEvent.setup();
    dispatchTaskMock.mockRejectedValue(
      new Error(
        'Request failed: 409 Conflict - {"error":{"code":"PROJECT_TASK_DISPATCH_INVALID","message":"该任务的负责人不是可运行的智能体或团队。"}}',
      ),
    );
    const { onChanged } = renderBoard([
      task({ assignee_type: "agent", assignee_id: "agent-a" }),
    ]);

    await user.click(dispatchButtons()[0]);

    await waitFor(() => expect(messageErrorMock).toHaveBeenCalledTimes(1));
    expect(messageSuccessMock).not.toHaveBeenCalled();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("只读看板不渲染派单入口", () => {
    render(
      <MemoryRouter initialEntries={["/projects/prj_1?tab=plan"]}>
        <Board
          projectId="prj_1"
          tasks={[task({ assignee_type: "agent" })]}
          canEdit={false}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(
      screen.queryByRole("button", { name: "projects.boardDispatch" }),
    ).not.toBeInTheDocument();
  });
});

describe("Board 卡片 testid（AC-01a/b/c 正 · AC-01d 显式未满足）", () => {
  beforeEach(() => {
    resetListMembers();
    messageErrorMock.mockReset();
  });

  it("AC-01a: task-card-priority 存在，且子树内有非 SVG 的图标承载节点 [data-priority-bars]", () => {
    renderBoard([task({ priority: 2 })]);

    const priority = screen.getByTestId("task-card-priority");
    expect(priority.querySelector("[data-priority-bars]")).not.toBeNull();
    // 图标真在承重：中档 ⇒ 2 格亮（不是裸数字 Tag）。
    expect(litBars()).toBe(2);
  });

  it("AC-01b/AC-01c: (agent, agent-a) ⇒ 头像 testid 存在 + Agent 徽标存在", () => {
    renderBoard([task({ assignee_type: "agent", assignee_id: "agent-a" })]);

    expect(screen.getByTestId("task-card-assignee-avatar")).toBeInTheDocument();
    expect(screen.getByTestId("task-card-agent-badge")).toBeInTheDocument();
  });

  it("AC-01b/AC-01c: (team, team-1) ⇒ 头像与徽标同源存在", () => {
    renderBoard([task({ assignee_type: "team", assignee_id: "team-1" })]);

    expect(screen.getByTestId("task-card-assignee-avatar")).toBeInTheDocument();
    expect(screen.getByTestId("task-card-agent-badge")).toBeInTheDocument();
  });

  it("AC-01c 负向: (user, 3) ⇒ 头像在、徽标不在", () => {
    renderBoard([task({ assignee_type: "user", assignee_id: "3" })]);

    expect(screen.getByTestId("task-card-assignee-avatar")).toBeInTheDocument();
    expect(screen.queryByTestId("task-card-agent-badge")).toBeNull();
  });

  // ★ P2-4 重指向（登记见 EVIDENCE-P2-4.md §重指向）：原断言「编号批次落地前
  // `task-card-number` 必须缺席」钉的是**改造前的旧行为**（编号批次现已落地）。
  // 现按 `SPEC.md · AC-16`（两向）+ `B-05` / `B-06`（缺失/形态不符 ⇒ 整块不渲染）改写：
  // ① 正向、② 负向（缺 + 畸形）、③ 判别性对照（注入同 testid ⇒ 同一查询非空）。
  it("AC-16 ①/B-05: task_number 命中冻结形态 ⇒ `task-card-number` 存在且文本逐字等于编号", () => {
    renderBoard([
      task({
        task_number: "APOLLO-002",
        assignee_type: "agent",
        assignee_id: "agent-a",
      }),
    ]);

    // 同一次 render 的正向对照：卡片其余 testid 也在（防「整卡不渲染也能绿」）。
    expect(screen.getByTestId("task-card-priority")).toBeInTheDocument();
    expect(screen.getByTestId("task-card-assignee-avatar")).toBeInTheDocument();
    expect(screen.getByTestId("task-card-number")).toHaveTextContent(
      "APOLLO-002",
    );
    // 编号位承载节点**逐字**是后端给出的编号（不复用 `task_id`、不加前后缀）。
    expect(screen.getByTestId("task-card-number").textContent).toBe(
      "APOLLO-002",
    );
    // `cardAriaLabel(title, taskNumber)` 的既有签名不改（PLAN §B2.4）。
    expect(
      screen.getByRole("button", { name: "APOLLO-002 · Task one" }),
    ).toBeInTheDocument();
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
    "AC-16 ②/B-05/B-06/B-16: task_number $label ⇒ 编号位整块不渲染（不用 task_id 顶替）",
    ({ value }) => {
      renderBoard([task({ task_number: value as string | null })]);
      expect(screen.queryByTestId("task-card-number")).toBeNull();
      // 同一次 render 的正向对照（防「整卡不渲染」蒙绿）。
      expect(screen.getByTestId("task-card-priority")).toBeInTheDocument();
    },
  );

  it("AC-16 ②/B-05 判别性对照: 同一次 render 临时注入同名节点 ⇒ 同一查询必须非空", () => {
    renderBoard([task({ assignee_type: "agent", assignee_id: "agent-a" })]);

    // ② 先证「确实缺席」。
    expect(screen.queryByTestId("task-card-number")).toBeNull();

    // ③ 判别性对照：临时插入同名节点（只在本用例内构造，不得落进实现）
    //    ⇒ 同一查询必须非空，证明 ② 的 null 不是恒真。字面量取自 SPEC · AC-16。
    const probe = document.createElement("span");
    probe.setAttribute("data-testid", "task-card-number");
    probe.textContent = "PRJ-001";
    cardRootOf("Task one").appendChild(probe);
    expect(screen.getByTestId("task-card-number")).toHaveTextContent("PRJ-001");
  });

  // `FIND-34`：`B-23` ③（`SPEC.md` @119 / @225）此前**判据 0 落地**（`aria-hidden`
  // 0 命中 · `querySelectorAll` 仅 2 处）。本格按规格逐字补齐：收集函数 + 4 排除项
  // + 判别性对照（注入同口径空 `div` ⇒ 同一收集函数必须报出）。
  it("B-23 ③: 卡片根子树内无「无文本 ∧ 无子元素 ∧ 无 data-testid」的 div/span（4 排除项 + 注入对照）", () => {
    renderBoard([
      task({
        tags: tagList(2),
        custom_fields: [
          {
            field_id: "fld_1",
            key: "note",
            label: "备注",
            type: "text",
            value: "hello",
          },
        ],
        start_at: START_AT,
        assignee_type: "agent",
        assignee_id: "agent-a",
      }),
    ]);

    // ① 先证「卡片确实渲染了」且排除项的载体都在树上（防整卡不渲染也能绿）：
    //    排除项 ⓒ 的 3 根柱、ⓓ 的 chip 圆点、ⓑ 的 antd 节点、ⓐ 的图标承载。
    const root = cardRootOf("Task one");
    expect(within(root).getByTestId("task-card-priority")).toBeInTheDocument();
    expect(within(root).getByTestId("task-card-chips")).toBeInTheDocument();
    expect(
      root.querySelectorAll("[data-priority-bars] > *").length,
    ).toBeGreaterThan(0);
    expect(
      within(root).getByTestId("task-card-assignee-avatar"),
    ).toBeInTheDocument();

    // ② 判据本体：4 条排除项之外，「无文本 ∧ 无子元素 ∧ 无 data-testid」的
    //    div/span 一个都不许有（空壳容器 = `B-23` 禁止的「空占位」）。
    expect(emptyShellNodes(root)).toEqual([]);

    // ③ ★ 判别性对照：临时向卡片根 appendChild 一个同口径空 div ⇒ **同一收集函数**
    //    必须报出该节点（证明 ② 的 [] 不是恒真）。临时节点只在本用例内构造，
    //    **不得**落进实现。
    const probe = document.createElement("div");
    root.appendChild(probe);
    const reported = emptyShellNodes(root);
    expect(reported.length).toBe(1);
    expect(reported[0]).toBe(probe);
  });
});

/**
 * P2-4（`C2-CARD`）行④**主体位多值**：`N` 用户 + `M` 执行主体。
 *
 * 契约来源：`PLAN.md` §B2.1b（`owners: number[]` / `executors: string[]`）+ UI-SPEC §8.3
 * （`[人·头像组] → [执行主体·头像组] + [名字]`，每侧**前 2 + `+N``**、`→` 仅两侧都有值
 * 时渲染、合计 = 1 时与改造前**逐字一致**）。★ 与 `PLAN.md` §CD「两侧合计前 2 + `+N`」
 * 的差异与处置登记在 `team/2026-10-08-110000/EVIDENCE-P2-4.md`。
 */
describe("P2-4 行④ 主体位多值（N 用户 + M 执行主体 · → 箭头）", () => {
  beforeEach(() => {
    resetListMembers();
  });

  /** 某张卡的「主体位」容器（`.cardOwnerAgent`）。多主体时 `getByTestId` 会命中多枚头像。 */
  function subjectBlockOf(title: string): HTMLElement {
    const block = cardRootOf(title).querySelector<HTMLElement>(
      `.${styles.cardOwnerAgent}`,
    );
    if (block === null) throw new Error(`subject block not found: ${title}`);
    return block;
  }

  function subjectBlockOrNull(title: string): HTMLElement | null {
    return cardRootOf(title).querySelector<HTMLElement>(
      `.${styles.cardOwnerAgent}`,
    );
  }

  /** 主体位内的头像节点（**文档顺序** = `owners` 在前、`executors` 在后）。 */
  function avatarsOf(title: string): HTMLElement[] {
    return Array.from(
      subjectBlockOrNull(title)?.querySelectorAll<HTMLElement>(
        '[data-testid="task-card-assignee-avatar"]',
      ) ?? [],
    );
  }

  const arrowOf = (title: string): Element | null =>
    subjectBlockOrNull(title)?.querySelector("svg.lucide-arrow-right") ?? null;

  const badgeOf = (title: string): Element | null =>
    subjectBlockOrNull(title)?.querySelector(
      '[data-testid="task-card-agent-badge"]',
    ) ?? null;

  const moreOf = (
    title: string,
    side: "owner" | "executor",
  ): HTMLElement | null =>
    subjectBlockOrNull(title)?.querySelector<HTMLElement>(
      `[data-subject-more="${side}"]`,
    ) ?? null;

  /** 4 个主体（2 用户 + 2 执行主体）的成员表。 */
  function fourSubjects() {
    return [
      member({
        subject_type: "user",
        subject_id: "7",
        user_id: 7,
        name: "张三",
      }),
      member({
        subject_type: "user",
        subject_id: "8",
        user_id: 8,
        name: "李四",
      }),
      member({ subject_type: "agent", subject_id: "agent-a", name: "Bot One" }),
      member({ subject_type: "team", subject_id: "team-1", name: "Squad" }),
    ];
  }

  it("★★ 2 用户 + 2 执行主体 ⇒ 卡片上**真的渲染 4 个主体** + `→` + 执行主体名", async () => {
    listMembersMock.mockResolvedValue(fourSubjects());
    renderBoard([
      task({
        title: "四主体",
        owners: [7, 8],
        executors: ["agent:agent-a", "team:team-1"],
        // 镜像列 = 后端由最终集合派生（`executors[0]` 优先，PLAN §B2.3 ④）。
        assignee_type: "agent",
        assignee_id: "agent-a",
      }),
    ]);
    await flushMemberLoad();

    // ① 头像节点 = 4（N=2 用户 + M=2 执行主体 ⇒ 4 个主体**全部**可见，不是只显示第一个）。
    const avatars = avatarsOf("四主体");
    expect(avatars).toHaveLength(4);
    // ② 顺序 = `owners` 在前、`executors` 在后（各自按数组顺序）⇒ 首字 张/李/B/S。
    expect(avatars.map((node) => node.textContent)).toEqual([
      "张",
      "李",
      "B",
      "S",
    ]);
    // ③ `→` 落在「人组」与「执行主体组」之间（DOM 第 3 个子节点 = 第 3 枚主体之前）。
    const block = subjectBlockOf("四主体");
    expect(block.children).toHaveLength(7);
    expect(
      block.children[2].querySelector("svg.lucide-arrow-right"),
    ).not.toBeNull();
    expect(arrowOf("四主体")).not.toBeNull();
    // ④ 名字位 = 执行主体首位名；执行主体徽标（冻结 testid）仍在。
    expect(block.textContent).toContain("Bot One");
    expect(badgeOf("四主体")).not.toBeNull();
    // ⑤ 无折叠（每侧 2 枚 ⇒ `N = 0`）。
    expect(moreOf("四主体", "owner")).toBeNull();
    expect(moreOf("四主体", "executor")).toBeNull();
    // ⑥ 全部主体进无障碍文本（不可只渲染 `+N` 而无文本，UIS-R-10）。
    expect(block.getAttribute("title")).toBe("张三、李四、Bot One、Squad");
  });

  it("★ ≥8 主体（5 用户 + 3 执行主体）⇒ 每侧**前 2 + `+N`**，节点数 O(1)、不展开全部（R17）", async () => {
    listMembersMock.mockResolvedValue([
      ...[1, 2, 3, 4, 5].map((userId) =>
        member({
          subject_type: "user",
          subject_id: String(userId),
          user_id: userId,
          name: `用户${userId}`,
        }),
      ),
      member({ subject_type: "agent", subject_id: "a1", name: "专家一" }),
      member({ subject_type: "agent", subject_id: "a2", name: "专家二" }),
      member({ subject_type: "team", subject_id: "t1", name: "团队一" }),
    ]);
    renderBoard([
      task({
        title: "大样本",
        owners: [1, 2, 3, 4, 5],
        executors: ["agent:a1", "agent:a2", "team:t1"],
      }),
    ]);
    await flushMemberLoad();

    // 8 个主体 ⇒ 头像**恒 4 枚**（每侧前 2），其余折成文本 ⇒ 节点数与 N 无关。
    expect(avatarsOf("大样本")).toHaveLength(4);
    expect(avatarsOf("大样本").map((node) => node.textContent)).toEqual([
      "用",
      "用",
      "专",
      "专",
    ]);
    expect(moreOf("大样本", "owner")).toHaveTextContent("+3");
    expect(moreOf("大样本", "executor")).toHaveTextContent("+1");
    // 折叠掉的主体不丢信息：全部 8 个进 `title`。
    expect(subjectBlockOf("大样本").getAttribute("title")).toBe(
      "用户1、用户2、用户3、用户4、用户5、专家一、专家二、团队一",
    );
  });

  it("UIS-R-07 判别性对照: `→` **仅两侧都有值**时渲染（同一次 render 三张卡对照）", async () => {
    listMembersMock.mockResolvedValue(fourSubjects());
    renderBoard([
      task({
        task_id: "tsk_both",
        title: "两侧都有",
        owners: [7],
        executors: ["agent:agent-a"],
        assignee_type: "agent",
        assignee_id: "agent-a",
        sort_order: 0,
      }),
      task({
        task_id: "tsk_owner_only",
        title: "只有人侧",
        owners: [7, 8],
        assignee_type: "user",
        assignee_id: "7",
        sort_order: 1,
      }),
      task({
        task_id: "tsk_executor_only",
        title: "只有执行主体侧",
        executors: ["agent:agent-a", "team:team-1"],
        assignee_type: "agent",
        assignee_id: "agent-a",
        sort_order: 2,
      }),
      task({
        task_id: "tsk_none",
        title: "两侧都空",
        owners: [],
        executors: [],
        sort_order: 3,
      }),
    ]);
    await flushMemberLoad();

    // ① 两侧都有值 ⇒ 箭头在（证明下面的 null 不是恒真）。
    expect(arrowOf("两侧都有")).not.toBeNull();
    expect(avatarsOf("两侧都有")).toHaveLength(2);
    // ② 只有一侧 ⇒ 无箭头（且该侧多值仍然渲染：2 枚头像）。
    expect(arrowOf("只有人侧")).toBeNull();
    expect(avatarsOf("只有人侧")).toHaveLength(2);
    expect(arrowOf("只有执行主体侧")).toBeNull();
    expect(avatarsOf("只有执行主体侧")).toHaveLength(2);
    // ③ 两侧都空 ⇒ 主体块**整块不渲染**（无空壳、无占位符）。
    expect(subjectBlockOrNull("两侧都空")).toBeNull();
    expect(cardRootOf("两侧都空")).toBeInTheDocument();
  });

  it("合计 = 1 防回归: 数组侧单主体 ⇒ 逐字一致（无 `→`、无 `+N`，同 testid/class）", async () => {
    listMembersMock.mockResolvedValue(fourSubjects());
    renderBoard([task({ title: "单主体", owners: [7] })]);
    await flushMemberLoad();

    const block = subjectBlockOf("单主体");
    expect(avatarsOf("单主体")).toHaveLength(1);
    expect(avatarsOf("单主体")[0].textContent).toBe("张");
    expect(avatarsOf("单主体")[0].className).toContain(styles.cardAvatar);
    expect(block.children).toHaveLength(2);
    expect(arrowOf("单主体")).toBeNull();
    expect(moreOf("单主体", "owner")).toBeNull();
    expect(moreOf("单主体", "executor")).toBeNull();
    // 名字位仍是解析到的成员名（`Avatar` 首字 + 名字位两段文本，与改造前同构）。
    expect(block.textContent).toBe("张张三");
    // 单主体不写 `title`（与改造前逐字一致）。
    expect(block.getAttribute("title")).toBeNull();
  });
});

/**
 * A4 选中态**数据通道**（`PLAN.md §A4.3` / `SPEC.md · AC-12`）。
 *
 * 语义 = 「该卡详情已打开」（`?task=` 命中）。本批只落地**数据侧**：卡片根的
 * `data-selected` 与 `styles.cardSelected`（视觉 `border-left` 在 `Board.module.less`，
 * 归 `A-BOARD-CSS`）。★ 唯一判定处在 `Board` 的 `renderCard` 内、**只读 prop**。
 */
describe("Board 卡片选中态数据通道（PLAN §A4.3 / AC-12）", () => {
  beforeEach(() => {
    resetListMembers();
  });

  it('命中卡: 卡片根 data-selected="true" + cardSelected 类（同 render 内其余 testid 仍在）', () => {
    renderBoard([task({ task_id: "tsk_1" })], { selectedTaskId: "tsk_1" });

    const root = cardRootOf("Task one");
    // 新增的冻结 testid（`SPEC.md · testid 名` 权威行）确实挂在卡片根上。
    expect(root).toHaveAttribute("data-testid", "task-card");
    expect(root).toHaveAttribute("data-selected", "true");
    expect(root.classList.contains(styles.cardSelected)).toBe(true);

    // ★ 同一次 render 的正向对照：卡片其余 testid 仍在（防「整卡不渲染也能绿」）。
    expect(within(root).getByTestId("task-card-priority")).toBeInTheDocument();
    expect(within(root).getByTestId("task-card-menu")).toBeInTheDocument();
  });

  it('两向 · 同一次 render: 命中卡 "true" / 未命中卡 "false" 且未命中卡无该类', () => {
    renderBoard(
      [
        task({ task_id: "tsk_1", title: "Task one" }),
        task({ task_id: "tsk_2", title: "Task two" }),
      ],
      { selectedTaskId: "tsk_2" },
    );

    // 两张卡都在树上（否则下面的 false / 无类可能是「卡根本没渲染」）。
    expect(screen.getAllByTestId("task-card")).toHaveLength(2);

    const hit = cardRootOf("Task two");
    expect(hit).toHaveAttribute("data-selected", "true");
    expect(hit.classList.contains(styles.cardSelected)).toBe(true);

    const miss = cardRootOf("Task one");
    expect(miss).toHaveAttribute("data-selected", "false");
    expect(miss.classList.contains(styles.cardSelected)).toBe(false);
    expect(within(miss).getByTestId("task-card-priority")).toBeInTheDocument();

    // ★ 判别性对照：临时把选中类加到未命中卡上 ⇒ **同一查询**必须变真
    //   （证明上面的 false 不是「该类名恒不出现」的恒真断言）。只在用例内构造。
    miss.classList.add(styles.cardSelected);
    expect(miss.classList.contains(styles.cardSelected)).toBe(true);
  });

  it("不传 selectedTaskId（详情未打开）⇒ 所有卡均为 false 且无该类", () => {
    renderBoard([task({ task_id: "tsk_1" })]);

    const root = cardRootOf("Task one");
    expect(root).toHaveAttribute("data-selected", "false");
    expect(root.classList.contains(styles.cardSelected)).toBe(false);
  });

  it("唯一来源 = prop: URL 里 ?task= 命中该卡但 prop 未命中 ⇒ 仍为 false（Board 不读 URL）", () => {
    renderBoard([task({ task_id: "tsk_2", title: "Task two" })], {
      initialEntry: "/projects/prj_1?tab=plan&task=tsk_2",
    });

    // 路由探针确认 URL 里确实带着 ?task=（MemoryRouter 不写 window.location）。
    expect(screen.getByTestId("router-search")).toHaveTextContent("task=tsk_2");

    const root = cardRootOf("Task two");
    expect(root).toHaveAttribute("data-selected", "false");
    expect(root.classList.contains(styles.cardSelected)).toBe(false);
  });

  it("只读用户（canEdit=false）同样带选中标记 —— 语义是「详情已打开」，与编辑权限无关", () => {
    renderBoard([task({ task_id: "tsk_1" })], {
      canEdit: false,
      selectedTaskId: "tsk_1",
    });

    const root = cardRootOf("Task one");
    expect(root).toHaveAttribute("data-selected", "true");
    expect(root.classList.contains(styles.cardSelected)).toBe(true);
  });
});

describe("Board members 失败分支（B-13 · 403 / 404 / reject 均静默）", () => {
  beforeEach(() => {
    resetListMembers();
    messageErrorMock.mockReset();
  });

  it.each([
    {
      label: "403",
      error: new Error(
        'Request failed: 403 Forbidden - {"error":{"code":"FORBIDDEN","message":"无权限"}}',
      ),
    },
    {
      label: "404",
      error: new Error(
        'Request failed: 404 Not Found - {"error":{"code":"NOT_FOUND","message":"项目不存在"}}',
      ),
    },
    { label: "reject", error: new Error("members rejected") },
  ])(
    "B-13: listMembers $label ⇒ 卡片仍渲染 ∧ messageErrorMock 未调用 ∧ 落 type:#短id 兜底",
    async ({ error }) => {
      listMembersMock.mockRejectedValue(error);
      renderBoard([task({ assignee_type: "agent", assignee_id: "ghost_1" })]);
      await flushMemberLoad();

      // 看板没被打断：卡片、头像、兜底文本都在。
      expect(listMembersMock).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId("task-card-priority")).toBeInTheDocument();
      expect(
        screen.getByTestId("task-card-assignee-avatar"),
      ).toBeInTheDocument();
      const text = assigneeBlockText();
      expect(text.trim()).not.toBe("");
      expect(text).toContain("agent:#host_1");
      expect(text).not.toContain("undefined");
      expect(text).not.toContain("NaN");
      expect(text).not.toContain("null");
      // 静默（R-10）：不弹错；抛错会以未处理拒绝的形式直接炸掉本用例。
      expect(messageErrorMock).not.toHaveBeenCalled();
    },
  );
});

describe("Board 优先级 / 标签 / 单主体位（SPEC §7.4 第 3-5 条）", () => {
  beforeEach(() => {
    resetListMembers();
    messageErrorMock.mockReset();
  });

  it.each([1, 0, -1, -99])(
    "AC-02: priority=%i（≤1 的整数）⇒ 1 格亮 + 低档文案（不得按未知值处理）",
    (priority) => {
      renderBoard([task({ priority })]);

      expect(litBars()).toBe(1);
      expect(priorityText()).toBe("common.cardPriorityLow");
    },
  );

  it("AC-03: priority=2 ⇒ 2 格亮 + 中档文案", () => {
    renderBoard([task({ priority: 2 })]);

    expect(litBars()).toBe(2);
    expect(priorityText()).toBe("common.cardPriorityMid");
  });

  it.each([3, 7, 999])(
    "AC-04: priority=%i（≥3）⇒ 3 格亮 + 高档文案",
    (priority) => {
      renderBoard([task({ priority })]);

      expect(litBars()).toBe(3);
      expect(priorityText()).toBe("common.cardPriorityHigh");
    },
  );

  // `NaN` 一格是全文唯一允许出现 `NaN` 字样的格（期望文本 = String(NaN)）。
  it.each([
    { label: "1.5", raw: 1.5, expected: "1.5", forbidNaN: true },
    { label: "NaN", raw: Number.NaN, expected: "NaN", forbidNaN: false },
    { label: "null", raw: null, expected: "—", forbidNaN: true },
    { label: "undefined", raw: undefined, expected: "—", forbidNaN: true },
    { label: '"abc"', raw: "abc", expected: "abc", forbidNaN: true },
  ])(
    "AC-05/B-02: 未知值 $label ⇒ 0 格亮 + 原值 $expected（不回退成「低」）",
    ({ raw, expected, forbidNaN }) => {
      renderBoard([task({ priority: raw as number })]);

      expect(litBars()).toBe(0);
      expect(priorityText()).toBe(expected);
      expect(priorityText()).not.toContain("undefined");
      expect(priorityText()).not.toContain("null");
      if (forbidNaN) expect(priorityText()).not.toContain("NaN");
    },
  );

  it("AC-06/B-01: tags=0 ⇒ 标签行整块不渲染（无空容器、无「暂无标签」）", () => {
    renderBoard([task({ tags: [] })]);

    expect(screen.queryByTestId("task-card-chips")).toBeNull();
  });

  it("B-20: tags=undefined（旧缓存脏数据）⇒ 同样不渲染标签行且不抛错", () => {
    renderBoard([task({ tags: undefined })]);

    expect(screen.queryByTestId("task-card-chips")).toBeNull();
    expect(screen.getByTestId("task-card-priority")).toBeInTheDocument();
  });

  it("AC-07/B-11: tags=3 ⇒ 3 个 chip 且无 +N", () => {
    renderBoard([task({ tags: tagList(3) })]);

    expect(screen.getByTestId("task-card-chips").children.length).toBe(3);
    expect(screen.queryByTestId("task-card-chip-more")).toBeNull();
  });

  it.each([0, 1, 2, 3])(
    "B-11 负向: tags=%i ⇒ queryByTestId(task-card-chip-more) === null",
    (count) => {
      renderBoard([task({ tags: tagList(count) })]);

      expect(screen.queryByTestId("task-card-chip-more")).toBeNull();
    },
  );

  it("AC-08/B-10/B-11 正向对照: tags=4 ⇒ 3 chip + `+1`（标签节点共 4 个）", () => {
    renderBoard([task({ tags: tagList(4) })]);

    expect(screen.getByTestId("task-card-chips").children.length).toBe(4);
    expect(screen.getByTestId("task-card-chip-more").textContent).toBe("+1");
  });

  it("AC-09: (user, 3) 命中成员 ⇒ 头像为首字母 + 名称文本非空 + 无徽标", async () => {
    listMembersMock.mockResolvedValue([
      member({
        subject_type: "user",
        subject_id: "3",
        user_id: 3,
        name: "Zhang San",
      }),
    ]);
    renderBoard([task({ assignee_type: "user", assignee_id: "3" })]);
    await flushMemberLoad();

    expect(screen.getByTestId("task-card-assignee-avatar").textContent).toBe(
      "Z",
    );
    expect(assigneeBlockText()).toContain("Zhang San");
    expect(assigneeBlockText().trim()).not.toBe("");
    expect(screen.queryByTestId("task-card-agent-badge")).toBeNull();
  });

  it("AC-10: (agent, agent-a) ⇒ 头像 + 徽标 + 名称 trim() 非空 + 无「→」连接符", async () => {
    listMembersMock.mockResolvedValue([member()]);
    renderBoard([task({ assignee_type: "agent", assignee_id: "agent-a" })]);
    await flushMemberLoad();

    expect(screen.getByTestId("task-card-assignee-avatar").textContent).toBe(
      "B",
    );
    expect(screen.getByTestId("task-card-agent-badge")).toBeInTheDocument();
    expect(assigneeBlockText()).toContain("Bot One");
    expect(assigneeBlockText().trim()).not.toBe("");
    // 单主体口径（UI.md @384）：不渲染「→」连接符。
    expect(screen.queryByText("→")).toBeNull();
  });

  it.each([
    { label: "(null, null)", type: null, id: null },
    { label: '("user", null)', type: "user", id: null },
    { label: '(null, "3")', type: null, id: "3" },
  ])(
    "B-03/AC-01b: 单主体位 $label ⇒ 整块不渲染（两者均非空才渲染）",
    ({ type, id }) => {
      renderBoard([task({ assignee_type: type, assignee_id: id })]);

      expect(screen.queryByTestId("task-card-assignee-avatar")).toBeNull();
      expect(screen.queryByTestId("task-card-agent-badge")).toBeNull();
      // 判别性对照：卡片本身确实渲染了（防「整卡不渲染」蒙绿）。
      expect(screen.getByTestId("task-card-priority")).toBeInTheDocument();
    },
  );

  it.each([
    { label: "未知类型 bot", type: "bot", id: "b1" },
    { label: "大小写敏感 Agent", type: "Agent", id: "a1" },
  ])(
    "B-16: assignee_type=$label ⇒ 徽标不渲染 + 兜底文本 $type:#短id 非空",
    async ({ type, id }) => {
      // 成员表里只有 `agent:<id>`：未知 / 大小写不同的类型必然查不中（大小写敏感）。
      listMembersMock.mockResolvedValue([
        member({ subject_type: "agent", subject_id: id }),
      ]);
      renderBoard([task({ assignee_type: type, assignee_id: id })]);
      await flushMemberLoad();

      expect(screen.queryByTestId("task-card-agent-badge")).toBeNull();
      const text = assigneeBlockText();
      expect(text.trim()).not.toBe("");
      expect(text).toContain(`${type}:`);
      expect(text).not.toContain("undefined");
    },
  );

  it('B-16: assignee_type=""（空值）⇒ 走 B-03 口径整块不渲染，徽标 0 个', () => {
    renderBoard([task({ assignee_type: "", assignee_id: "x" })]);

    expect(screen.queryByTestId("task-card-agent-badge")).toBeNull();
    expect(screen.queryByTestId("task-card-assignee-avatar")).toBeNull();
  });

  it.each([
    {
      label: "成员 name=null",
      members: [member({ name: null })],
      assignee: { assignee_type: "agent", assignee_id: "agent-a" } as const,
      expected: "agent:#gent-a",
    },
    {
      label: "成员表为空 + 非成员 id（B-06 幽灵引用）",
      members: [] as ProjectMember[],
      assignee: { assignee_type: "agent", assignee_id: "ghost_1" } as const,
      expected: "agent:#host_1",
    },
  ])(
    "B-17/B-06: $label ⇒ 头像在 ∧ 文本 trim() 非空 ∧ 无 undefined/NaN/null",
    async ({ members, assignee, expected }) => {
      listMembersMock.mockResolvedValue(members);
      renderBoard([task(assignee)]);
      await flushMemberLoad();

      expect(
        screen.getByTestId("task-card-assignee-avatar"),
      ).toBeInTheDocument();
      const text = assigneeBlockText();
      expect(text.trim()).not.toBe("");
      expect(text).toContain(expected);
      expect(text).not.toContain("undefined");
      expect(text).not.toContain("NaN");
      expect(text).not.toContain("null");
    },
  );
});

describe("Board ⋯ 菜单 / 键盘 / 内建导航 / 复制链接（AC-11/AC-12/AC-15/AC-16 · B-04/B-05/B-14/B-15/B-18/B-19）", () => {
  beforeEach(() => {
    resetListMembers();
    copyTextMock.mockReset();
    copyTextMock.mockResolvedValue(true);
    messageSuccessMock.mockReset();
    messageErrorMock.mockReset();
    localeMode.current = "key";
  });

  afterEach(() => {
    localeMode.current = "key";
  });

  it("AC-11/B-14/B-15/B-23: 不传 onOpenTask ⇒ 菜单恰 2 项且逐项文本非空（无「复制编号」）", async () => {
    renderBoard([task()]);

    const items = await openCardMenu();
    expect(items).toHaveLength(2);
    for (const item of items) expect(item.textContent?.trim()).not.toBe("");
    expect(items.map((item) => item.textContent?.trim())).toEqual([
      "common.cardMenuOpen",
      "common.cardMenuCopyLink",
    ]);
    // 第 3 项「复制编号」既不渲染、也不留空壳项位。
    expect(
      items.every((item) => !/编号|number/i.test(item.textContent ?? "")),
    ).toBe(true);
  });

  it("B-04(zh): 菜单文案逐字 = {打开详情, 复制链接}，且不含任何写动词", async () => {
    localeMode.current = "zh";
    renderBoard([task()]);

    const items = await openCardMenu();
    expect(items.map((item) => item.textContent?.trim())).toEqual([
      "打开详情",
      "复制链接",
    ]);
    expect(items.map((item) => item.textContent).join(" ")).not.toMatch(
      MENU_WRITE_VERBS,
    );
  });

  it("B-04(en): 菜单文案逐字 = {Open details, Copy link}，且不含任何写动词", async () => {
    localeMode.current = "en";
    renderBoard([task()]);

    const items = await openCardMenu();
    expect(items.map((item) => item.textContent?.trim())).toEqual([
      "Open details",
      "Copy link",
    ]);
    expect(items.map((item) => item.textContent).join(" ")).not.toMatch(
      MENU_WRITE_VERBS,
    );
  });

  it("B-04/B-05/AC-17: 只读用户 ⇒ ⋯ 仍渲染、菜单仍恰 2 项且无写项、卡片不可拖拽", async () => {
    renderBoard([task()], { canEdit: false });

    const items = await openCardMenu();
    expect(items).toHaveLength(2);
    expect(items.map((item) => item.textContent).join(" ")).not.toMatch(
      MENU_WRITE_VERBS,
    );
    expect(cardRootOf("Task one").getAttribute("draggable")).not.toBe("true");
  });

  it("AC-11/B-14: 不传 onOpenTask ⇒ 点「打开详情」后路由探针含 task=tsk_1（不用 window.location.search）", async () => {
    renderBoard([task()]);

    const items = await openCardMenu();
    fireEvent.click(items[0]);

    await waitFor(() =>
      expect(screen.getByTestId("router-search").textContent).toContain(
        "task=tsk_1",
      ),
    );
    // MemoryRouter 不写 window.location ⇒ 这条负向证明判据只可能来自探针。
    expect(window.location.search).not.toContain("task=tsk_1");
    expect(messageErrorMock).not.toHaveBeenCalled();
  });

  it("AC-11: 传 onOpenTask ⇒ 走 prop 回调，内建导航不触发（探针不变）", async () => {
    const onOpenTask = vi.fn();
    renderBoard([task()], { onOpenTask });

    const items = await openCardMenu();
    fireEvent.click(items[0]);

    expect(onOpenTask).toHaveBeenCalledWith("tsk_1");
    expect(screen.getByTestId("router-search").textContent).not.toContain(
      "task=tsk_1",
    );
  });

  it("AC-12 正向: 「复制链接」⇒ copyText 恰 1 次且入参含 ?task=tsk_1", async () => {
    copyTextMock.mockResolvedValue(true);
    renderBoard([task()]);

    const items = await openCardMenu();
    fireEvent.click(items[1]);

    await waitFor(() => expect(copyTextMock).toHaveBeenCalledTimes(1));
    expect(copyTextMock.mock.calls[0][0]).toContain("?task=tsk_1");
    await waitFor(() => expect(messageSuccessMock).toHaveBeenCalledTimes(1));
    expect(messageErrorMock).not.toHaveBeenCalled();
  });

  it("AC-12 失败路径: copyText 返回 false ⇒ 出现 common.copyFailed 且不抛错", async () => {
    copyTextMock.mockResolvedValue(false);
    renderBoard([task()]);

    const items = await openCardMenu();
    fireEvent.click(items[1]);

    await waitFor(() =>
      expect(messageErrorMock).toHaveBeenCalledWith("common.copyFailed"),
    );
    expect(copyTextMock).toHaveBeenCalledTimes(1);
    expect(messageSuccessMock).not.toHaveBeenCalled();
  });

  it("AC-12 失败路径: copyText 抛错 ⇒ 同样落 common.copyFailed（不冒泡）", async () => {
    copyTextMock.mockRejectedValue(new Error("clipboard exploded"));
    renderBoard([task()]);

    const items = await openCardMenu();
    fireEvent.click(items[1]);

    await waitFor(() =>
      expect(messageErrorMock).toHaveBeenCalledWith("common.copyFailed"),
    );
    expect(messageSuccessMock).not.toHaveBeenCalled();
  });

  it("AC-15 正向: 标题按钮 click / Enter / Space 各触发一次（aria-label = 标题全文）", async () => {
    const user = userEvent.setup();
    const onOpenTask = vi.fn();
    renderBoard([task()], { onOpenTask });

    const button = titleButtonOf();
    // 本轮无编号 ⇒ `cardAriaLabel(title, undefined)` 即标题全文。
    expect(button).toHaveAttribute("aria-label", "Task one");
    button.focus();
    expect(button).toHaveFocus();

    fireEvent.click(button);
    expect(onOpenTask).toHaveBeenCalledTimes(1);

    // 原生 `<button type="button">` ⇒ Enter / Space 走原生语义（无手写 onKeyDown）。
    await user.keyboard("{Enter}");
    expect(onOpenTask).toHaveBeenCalledTimes(2);

    await user.keyboard(" ");
    expect(onOpenTask).toHaveBeenCalledTimes(3);
    expect(onOpenTask).toHaveBeenLastCalledWith("tsk_1");
  });

  it("B-19 负向: ⋯ 上按 Enter ⇒ openDetail 0 次（与标题是兄弟节点）", async () => {
    const user = userEvent.setup();
    const onOpenTask = vi.fn();
    renderBoard([task()], { onOpenTask });

    const menuButton = screen.getByTestId("task-card-menu");
    menuButton.focus();
    expect(menuButton).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(onOpenTask).not.toHaveBeenCalled();
  });

  it("AC-15 负向: 卡片根节点无 onClick / onKeyDown（各 fireEvent 一次 ⇒ 0 次）", () => {
    const onOpenTask = vi.fn();
    renderBoard([task()], { onOpenTask });

    const root = cardRootOf("Task one");
    fireEvent.click(root);
    fireEvent.keyDown(root, { key: "Enter" });

    expect(onOpenTask).not.toHaveBeenCalled();
  });

  it("AC-16/B-18: ⋯ 的 draggable=false，且其 dragStart 不把卡片拖入拖拽态", () => {
    renderBoard([task()]);

    const menuButton = screen.getByTestId("task-card-menu");
    expect(menuButton).toHaveAttribute("draggable", "false");

    fireEvent.dragStart(menuButton, { dataTransfer: dragDataTransfer() });

    expect(cardRootOf("Task one").className).not.toContain("cardDragging");
    expect(updateTaskMock).not.toHaveBeenCalled();
  });

  it("B-18 判别性对照: 卡片本体 dragStart ⇒ 进入拖拽态（证明上面的负向非恒真）", () => {
    renderBoard([task()]);

    // 与「只读用户 ⇒ draggable !== true」成对：可编辑时属性必须为 true。
    expect(cardRootOf("Task one")).toHaveAttribute("draggable", "true");

    fireEvent.dragStart(cardRootOf("Task one"), {
      dataTransfer: dragDataTransfer(),
    });

    expect(cardRootOf("Task one").className).toContain("cardDragging");
  });
});

describe("Board 反 N+1 与拖拽抑制窗口（B-12 / B-29 · S-13 判别性对照）", () => {
  beforeEach(() => {
    resetListMembers();
    updateTaskMock.mockReset();
    updateTaskMock.mockResolvedValue(task());
    messageSuccessMock.mockReset();
    messageErrorMock.mockReset();
  });

  it("B-12/AC-13: N=3 张卡 ⇒ members === 1；拖拽 + 状态变更后仍 === 1", async () => {
    listMembersMock.mockResolvedValue([member()]);
    renderBoard([
      task({ task_id: "tsk_1", title: "Task one" }),
      task({ task_id: "tsk_2", title: "Task two" }),
      task({ task_id: "tsk_3", title: "Task three" }),
    ]);
    await flushMemberLoad();
    expect(listMembersMock).toHaveBeenCalledTimes(1);

    // 拖拽（start ⇒ end）不触发成员表重读。
    fireEvent.dragStart(cardRootOf("Task one"), {
      dataTransfer: dragDataTransfer(),
    });
    fireEvent.dragEnd(cardRootOf("Task one"));

    // 状态变更（todo ⇒ doing，走卡片底部的状态选择器）同样不重读。
    fireEvent.mouseDown(within(cardRootOf("Task one")).getByRole("combobox"));
    const dropdown = await waitFor(() => {
      const node = document.querySelector(".ant-select-dropdown");
      if (node === null) throw new Error("select dropdown not open");
      return node as HTMLElement;
    });
    const option = Array.from(
      dropdown.querySelectorAll(".ant-select-item-option"),
    ).find((node) => node.textContent === "projects.taskStatusDoing");
    expect(option).toBeTruthy();
    fireEvent.click(option as HTMLElement);

    await waitFor(() => expect(updateTaskMock).toHaveBeenCalledTimes(1));
    expect(updateTaskMock).toHaveBeenCalledWith("prj_1", "tsk_1", {
      status: "doing",
    });
    expect(listMembersMock).toHaveBeenCalledTimes(1);
  });

  it("S-13/B-29 ①: 该次 render 内拖拽后 click 标题 ⇒ openDetail 0 次（假时钟钉住 200ms 窗口）", () => {
    // 默认假时钟基准 = 真实「现在」（**不是** `setSystemTime(0)`：`0 - 0 < 200`
    // 会把第 ② 格一起吞掉，期望 1 次不可得）。
    vi.useFakeTimers();
    try {
      const onOpenTask = vi.fn();
      renderBoard([task()], { onOpenTask });
      const root = cardRootOf("Task one");

      // 三次同步 fireEvent（dragStart ⇒ dragEnd ⇒ click）在冻结时钟下必然落在
      // 抑制窗口内 ⇒ 不依赖「机器跑得比 200ms 快」。
      fireEvent.dragStart(root, { dataTransfer: dragDataTransfer() });
      fireEvent.dragEnd(root);
      fireEvent.click(titleButtonOf());
      expect(onOpenTask).toHaveBeenCalledTimes(0);

      // ② 推进假时钟跨过抑制窗口（`Date.now() - lastDragEndAt >= 200`）后，同一棵树
      //    上的 click 必须生效 —— 两格成对才构成对「点击恒不生效」的判别。
      vi.setSystemTime(Date.now() + 201);
      fireEvent.click(titleButtonOf());
      expect(onOpenTask).toHaveBeenCalledTimes(1);
      expect(onOpenTask).toHaveBeenCalledWith("tsk_1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("S-13/B-29 ②: 未拖拽直接 click 标题 ⇒ openDetail 1 次（独立 render ⇒ 冷启动不误抑制）", () => {
    const onOpenTask = vi.fn();
    renderBoard([task()], { onOpenTask });

    fireEvent.click(titleButtonOf());

    expect(onOpenTask).toHaveBeenCalledTimes(1);
  });
});
