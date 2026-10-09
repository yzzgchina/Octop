import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, beforeEach, vi } from "vitest";
import {
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import type { ProjectMember, ProjectTask } from "../../../api/modules/projects";
import TaskCreateModal, { type TaskCreateValues } from "./TaskCreateModal";

const getMock = vi.fn();
const createTaskMock = vi.fn();
const dispatchTaskMock = vi.fn();
const listMembersMock = vi.fn();
const listTagsMock = vi.fn();
const listFieldsMock = vi.fn();
const uploadMock = vi.fn();
const deleteAttachmentMock = vi.fn();

const messageSuccessMock = vi.fn();
const messageErrorMock = vi.fn();

vi.mock("@/utils/antdMessage", () => ({
  message: {
    success: (...args: unknown[]) => messageSuccessMock(...args),
    error: (...args: unknown[]) => messageErrorMock(...args),
    warning: vi.fn(),
  },
}));

vi.mock("../../../api/modules/projects", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/projects")
  >();
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      get: (...args: unknown[]) => getMock(...args),
      createTask: (...args: unknown[]) => createTaskMock(...args),
      dispatchTask: (...args: unknown[]) => dispatchTaskMock(...args),
      listMembers: (...args: unknown[]) => listMembersMock(...args),
    },
  };
});

vi.mock("../../../api/modules/projectMetadata", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/projectMetadata")
  >();
  return {
    ...actual,
    projectMetadataApi: {
      ...actual.projectMetadataApi,
      listTags: (...args: unknown[]) => listTagsMock(...args),
      listCustomFields: (...args: unknown[]) => listFieldsMock(...args),
      uploadPendingAttachment: (...args: unknown[]) => uploadMock(...args),
      deleteAttachment: (...args: unknown[]) => deleteAttachmentMock(...args),
    },
  };
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
    subject_type: "user",
    subject_id: "7",
    user_id: 7,
    role: "owner",
    created_at: 1,
  },
];

const titleInput = () => screen.getByLabelText("projects.createTaskTitle");
const descriptionInput = () =>
  screen.getByLabelText("projects.createTaskDescription");
const submitButton = () =>
  screen.getByRole("button", { name: /projects\.createTaskSubmit/ });

/** Mounts the dialog and types `value` into the inline title. */
async function open(value: string) {
  const onClose = vi.fn();
  const onCreated = vi.fn();
  render(
    <TaskCreateModal
      open
      projectId="prj_1"
      tasks={[]}
      canEdit
      onClose={onClose}
      onCreated={onCreated}
    />,
  );
  const user = userEvent.setup();
  if (value) await user.type(titleInput(), value);
  return { user, onClose, onCreated };
}

describe("TaskCreateModal（R3 / AC-U-8）", () => {
  beforeEach(() => {
    getMock
      .mockReset()
      .mockResolvedValue({ project_id: "prj_1", name: "Apollo" });
    createTaskMock.mockReset().mockResolvedValue({ task_id: "tsk_1" });
    dispatchTaskMock.mockReset();
    listMembersMock.mockReset().mockResolvedValue(MEMBERS);
    listTagsMock
      .mockReset()
      .mockResolvedValue([
        { tag_id: "tag_1", name: "紧急", color: "", created_at: 1 },
      ]);
    listFieldsMock.mockReset().mockResolvedValue([
      {
        field_id: "fld_1",
        key: "note",
        label: "备注",
        type: "text",
        required: false,
        options: [],
        sort_order: 0,
        created_at: 1,
        updated_at: 1,
      },
    ]);
    uploadMock.mockReset();
    deleteAttachmentMock.mockReset().mockResolvedValue({ deleted: true });
  });

  it("AC-U-8: 面包屑 + 内联标题/描述 + 10 个元素 + ⌘↵ 提交按钮", async () => {
    const { user } = await open("");

    // 面包屑「<项目> › 手动创建」。
    expect(await screen.findByText("Apollo")).toBeInTheDocument();
    expect(
      screen.getByText("projects.createTaskBreadcrumb"),
    ).toBeInTheDocument();
    // 内联大标题（无 label 框）+ 内联描述。
    expect(titleInput()).toBeInTheDocument();
    expect(descriptionInput()).toBeInTheDocument();
    // chip 工具条：状态 / 优先级 / 分配 / 标签 / 项目 / ···更多。
    for (const key of [
      "chipStatus",
      "chipPriority",
      "chipAssignee",
      "chipTags",
      "chipProject",
      "chipMore",
    ]) {
      expect(
        screen.getByRole("button", { name: `projects.${key}` }),
      ).toBeInTheDocument();
    }
    // 「···更多」里：截止日期 / 开始日期 / 父任务 / 子任务 / 自定义字段。
    await user.click(screen.getByRole("button", { name: "projects.chipMore" }));
    await waitFor(() =>
      expect(
        screen.getAllByLabelText("projects.chipDueAt").length,
      ).toBeGreaterThan(0),
    );
    for (const key of ["chipDueAt", "chipStartAt", "chipParentTask"]) {
      expect(
        screen.getAllByLabelText(`projects.${key}`).length,
      ).toBeGreaterThan(0);
    }
    expect(screen.getAllByText("projects.chipSubtasks").length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getAllByText("projects.chipCustomFields").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("备注")).toBeInTheDocument();
    // 附件 / 智能体 chip / 继续创建 / 提交。
    expect(screen.getByLabelText("projects.attachFile")).toBeInTheDocument();
    // ★ P2-6 重指向（**只改定位器，不改语义**）：入口上移（PLAN §CD ①）后，「切换到
    //   智能体」那枚底部 chip 已退场，同一个入口现在是 chip 行第 4 枚「智能体」
    //   （`projects.chipAgent`），文案零出现（UI-SPEC §3.1 UIS-C-03）。
    expect(
      screen.getByRole("button", { name: "projects.chipAgent" }),
    ).toBeInTheDocument();
    expect(screen.getByText("projects.createAndContinue")).toBeInTheDocument();
    expect(submitButton()).toBeInTheDocument();
  });

  it("S-5: IME 组合态按 ⌘/Ctrl+Enter 不提交且不 preventDefault；非组合态才提交", async () => {
    await open("中文任务");

    // 组合态：Enter 是「选词」，既不能提交也不能吞掉事件。
    const composing = createEvent.keyDown(titleInput(), {
      key: "Enter",
      metaKey: true,
      isComposing: true,
    });
    fireEvent(titleInput(), composing);
    expect(composing.defaultPrevented).toBe(false);
    expect(createTaskMock).not.toHaveBeenCalled();

    // 组合态下的裸 Enter（标题框）同样不提交。
    const composingPlain = createEvent.keyDown(titleInput(), {
      key: "Enter",
      isComposing: true,
    });
    fireEvent(titleInput(), composingPlain);
    expect(composingPlain.defaultPrevented).toBe(false);
    expect(createTaskMock).not.toHaveBeenCalled();

    // 对照：非组合态的 ⌘+Enter 提交（并 preventDefault）。
    const plain = createEvent.keyDown(titleInput(), {
      key: "Enter",
      metaKey: true,
    });
    fireEvent(titleInput(), plain);
    expect(plain.defaultPrevented).toBe(true);
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
  });

  it("S-5: 标题裸 Enter 提交；描述裸 Enter 换行不提交", async () => {
    await open("任务一");

    fireEvent.keyDown(descriptionInput(), { key: "Enter" });
    expect(createTaskMock).not.toHaveBeenCalled();

    fireEvent.keyDown(titleInput(), { key: "Enter" });
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
  });

  it(
    "AC-U-7/AC-U-8: 一次请求携带目标列与全部已选项（无两段式）",
    async () => {
      const { user } = await open("任务一");

      await user.click(
        screen.getByRole("button", { name: "projects.chipStatus" }),
      );
      await user.click(
        await screen.findByRole("button", { name: "projects.taskStatusDoing" }),
      );
      await user.click(
        screen.getByRole("button", { name: "projects.chipTags" }),
      );
      await user.click(await screen.findByRole("button", { name: /紧急/ }));
      await user.click(
        screen.getByRole("button", { name: "projects.chipPriority" }),
      );
      await user.click(await screen.findByRole("button", { name: "2" }));

      await user.click(submitButton());

      await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
      const [projectId, body] = createTaskMock.mock.calls[0];
      expect(projectId).toBe("prj_1");
      expect(body).toMatchObject({
        title: "任务一",
        status: "doing",
        priority: 2,
        tags: ["tag_1"],
        custom_fields: {},
        attachment_ids: [],
        parent_id: null,
      });
      // 只发一次请求：没有「先建 todo 再 move」的第二段。
      expect(dispatchTaskMock).not.toHaveBeenCalled();
    },
    { timeout: 9500 },
  );

  it("AC-U-21/S-4: 「继续创建」开启时提交后不关弹窗，清空 3 项、保留其余选择", async () => {
    const { user, onClose } = await open("任务一");
    await user.type(descriptionInput(), "描述");
    await user.click(
      screen.getByRole("button", { name: "projects.chipStatus" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "projects.taskStatusDoing" }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: "projects.createAndContinue" }),
    );

    await user.click(submitButton());
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));

    expect(onClose).not.toHaveBeenCalled();
    // 清空 title / description（due_at 未设置）。
    expect(titleInput()).toHaveValue("");
    expect(descriptionInput()).toHaveValue("");
    // 保留状态选择（chip 摘要仍是 doing）。
    expect(
      screen.getByRole("button", { name: "projects.chipStatus" }),
    ).toHaveTextContent("projects.taskStatusDoing");

    // 第二次提交仍带同一状态 → 证明「保留」生效。
    await user.type(titleInput(), "任务二");
    await user.click(submitButton());
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(2));
    expect(createTaskMock.mock.calls[1][1].status).toBe("doing");
  });

  it("AC-U-21: 「继续创建」关闭时提交成功后关闭弹窗", async () => {
    const { user, onClose, onCreated } = await open("任务一");

    await user.click(submitButton());

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onCreated).toHaveBeenCalled();
  });

  it("FIND-3/§8.1: 选择智能体只写双主体数组，不发派单请求", async () => {
    const { user } = await open("任务一");
    const before = createTaskMock.mock.calls.length;

    // ★ P2-6 重指向（**只改定位器，不改任何语义断言**）：本条钉住的语义是「选择智能体
    //   本身不发任何请求（任务尚不存在）」+「提交体只发两数组、不发旧单值对」。入口上移
    //   （PLAN §CD ①）后该入口 = `projects.chipAgent`；下面 4 条断言**逐字未改**。
    await user.click(
      screen.getByRole("button", { name: "projects.chipAgent" }),
    );
    await user.click(
      await screen.findByRole("button", { name: /agent:agent-a/ }),
    );

    // 选择智能体本身不发任何请求（任务尚不存在）。
    expect(createTaskMock.mock.calls.length).toBe(before);
    expect(dispatchTaskMock).not.toHaveBeenCalled();

    await user.click(submitButton());
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
    const body = createTaskMock.mock.calls[0][1];
    // PLAN §B2.1b / §B2.5 (c) V3：写面只发两数组；旧单值对**同给即 422**。
    expect(body.executors).toEqual(["agent:agent-a"]);
    expect(body.owners).toEqual([]);
    expect(body.assignee_type).toBeUndefined();
    expect(body.assignee_id).toBeUndefined();
    expect(dispatchTaskMock).not.toHaveBeenCalled();
  });

  it("AC-U-19/§7.5: 📎 先暂存上传；未提交就关闭则尽力删除暂存件且不阻塞关闭", async () => {
    const { user, onClose } = await open("任务一");
    uploadMock.mockResolvedValue({
      artifact_id: "art_1",
      name: "note.txt",
      size: 5,
      mime: "text/plain",
      created_at: 1,
      uploader: "tester",
      task_id: null,
    });

    const file = new File(["hello"], "note.txt", { type: "text/plain" });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => expect(uploadMock).toHaveBeenCalledWith("prj_1", file));
    expect(await screen.findByText("note.txt")).toBeInTheDocument();

    await user.click(screen.getByLabelText("common.cancel"));
    await waitFor(() =>
      expect(deleteAttachmentMock).toHaveBeenCalledWith("prj_1", "art_1"),
    );
    expect(onClose).toHaveBeenCalled();
  });
});

// ── F4 任务编辑（PLAN §5，同组件双模式） ─────────────────────────────────────

const onSubmitEditMock = vi.fn();

const BASE_TASK: ProjectTask = {
  task_id: "tsk_1",
  project_id: "prj_1",
  parent_id: null,
  title: "原任务",
  description: "原描述",
  status: "planning",
  assignee_type: null,
  assignee_id: null,
  owners: [],
  executors: [],
  priority: 0,
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

function task(overrides: Partial<ProjectTask> = {}): ProjectTask {
  return { ...BASE_TASK, ...overrides };
}

function editInitialValues(overrides: Partial<TaskCreateValues> = {}) {
  return {
    task_id: "tsk_1",
    title: "原任务",
    description: "原描述",
    status: "planning" as const,
    priority: 0,
    owners: [],
    executors: [],
    tag_ids: [],
    start_at: null,
    due_at: null,
    parent_id: null,
    deps: [],
    custom_fields: {},
    ...overrides,
  };
}

/** Mounts the same dialog in edit mode (PLAN §5.1 props). */
async function openEdit(
  options: {
    initialValues?: Partial<TaskCreateValues>;
    tasks?: ProjectTask[];
  } = {},
) {
  const onClose = vi.fn();
  render(
    <TaskCreateModal
      open
      mode="edit"
      projectId="prj_1"
      tasks={options.tasks ?? [task()]}
      canEdit
      onClose={onClose}
      onCreated={vi.fn()}
      initialValues={options.initialValues ?? editInitialValues()}
      onSubmitEdit={onSubmitEditMock}
    />,
  );
  const user = userEvent.setup();
  await waitFor(() =>
    expect(screen.getByLabelText("projects.createTaskTitle")).toHaveValue(
      options.initialValues?.title ?? "原任务",
    ),
  );
  return { user, onClose };
}

const editSubmit = () =>
  screen.getByRole("button", { name: /projects\.editTaskSubmit/ });

describe("TaskCreateModal（F4 编辑模式）", () => {
  beforeEach(() => {
    getMock
      .mockReset()
      .mockResolvedValue({ project_id: "prj_1", name: "Apollo" });
    listMembersMock.mockReset().mockResolvedValue(MEMBERS);
    listTagsMock.mockReset().mockResolvedValue([
      { tag_id: "tag_1", name: "紧急", color: "", created_at: 1 },
      { tag_id: "tag_2", name: "后端", color: "", created_at: 2 },
    ]);
    listFieldsMock.mockReset().mockResolvedValue([
      {
        field_id: "fld_1",
        key: "note",
        label: "备注",
        type: "text",
        required: false,
        options: [],
        sort_order: 0,
        created_at: 1,
        updated_at: 1,
      },
    ]);
    onSubmitEditMock.mockReset().mockResolvedValue(undefined);
  });

  it("AC-F4-1/2: 预填现值，且文案与 create 模式区分", async () => {
    await openEdit({
      initialValues: editInitialValues({
        title: "改后的标题",
        description: "改后的描述",
        priority: 3,
        status: "doing",
        executors: ["agent:agent-a"],
        tag_ids: ["tag_1"],
        start_at: 1_750_000_000,
        due_at: 1_750_600_000,
        custom_fields: { fld_1: "已填" },
      }),
    });

    expect(titleInput()).toHaveValue("改后的标题");
    expect(descriptionInput()).toHaveValue("改后的描述");
    expect(screen.getByText("projects.editTaskTitle")).toBeInTheDocument();
    expect(editSubmit()).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /projects\.createTaskSubmit/ }),
    ).toBeNull();
    // Chip 摘要反映现值（状态 / 分配 / 智能体 / 标签）：摘要形如「标签：值」，故先按标签定位按钮。
    expect(
      screen.getByRole("button", { name: /^projects\.chipStatus/ }),
    ).toHaveTextContent("projects.taskStatusDoing");
    // ★ P2-6 重指向：`initialValues.executors = ["agent:agent-a"]` 是**执行主体**，
    //   按 PLAN §CD ② 的归槽规则落「智能体」chip（不再是「分配」chip）。
    //   期望文本随之从裸引用 `agent:agent-a` 变为**共享展示模型**的兜底形态
    //   `agent:#gent-a`（`Board.subjectDisplayName` ⇒ `type:#末6位`；UIS-T-04 逐字
    //   规定「必须与行④ assigneeFallback 同构」，其判据行即 `agent-a ⇒ agent:#gent-a`）。
    expect(
      screen.getByRole("button", { name: /^projects\.chipAgent/ }),
    ).toHaveTextContent("agent:#gent-a");
    expect(
      screen.getByRole("button", { name: /^projects\.chipAssignee/ }),
    ).not.toHaveTextContent("agent:agent-a");
    // ★ The tag *names* come from the async definition read («listTags»), not
    //   from ``initialValues``: ``openEdit`` only waits for the title, so under
    //   load this summary can still be empty. Wait for the data rather than the
    //   clock (V1 round 2: 1 red in 6 full runs, ``Test Files`` flake).
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^projects\.chipTags/ }),
      ).toHaveTextContent("紧急"),
    );
    // ★ 编辑面不含 create 专属件：📎 / 继续创建（PLAN §5.3 冻结字段集）。
    expect(screen.queryByLabelText("projects.attachFile")).toBeNull();
    expect(screen.queryByText("projects.createAndContinue")).toBeNull();
  });

  it("AC-F4-3: 差异提交——只提交被改动字段，未改字段一律不写回", async () => {
    const { user, onClose } = await openEdit({
      initialValues: editInitialValues({ priority: 2, parent_id: null }),
    });

    await user.clear(titleInput());
    await user.type(titleInput(), "只改标题");
    await user.click(editSubmit());

    await waitFor(() => expect(onSubmitEditMock).toHaveBeenCalledTimes(1));
    const patch = onSubmitEditMock.mock.calls[0][0];
    // 恰一个键：priority / parent_id / status … 都未被写回。
    expect(patch).toEqual({ title: "只改标题" });
    expect(onClose).toHaveBeenCalled();
  });

  it("AC-F4-3: 完全未改动 → 不发请求，直接关闭", async () => {
    const { user, onClose } = await openEdit();

    await user.click(editSubmit());

    expect(onSubmitEditMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("AC-F4-7: tags / custom_fields 全量替换；空值 = 显式清空", async () => {
    const { user } = await openEdit({
      initialValues: editInitialValues({
        tag_ids: ["tag_1"],
        custom_fields: { fld_1: "旧值" },
      }),
    });

    // 加第二个标签（提交的数组 = 最终集合，不是增量）。
    await user.click(screen.getByRole("button", { name: "projects.chipTags" }));
    await user.click(await screen.findByRole("button", { name: /后端/ }));
    // 自定义字段在「···更多」里。
    await user.click(screen.getByRole("button", { name: "projects.chipMore" }));
    const field = await screen.findByLabelText("备注");
    await user.clear(field);
    await user.type(field, "新值");

    await user.click(editSubmit());

    await waitFor(() => expect(onSubmitEditMock).toHaveBeenCalledTimes(1));
    const patch = onSubmitEditMock.mock.calls[0][0];
    expect(patch).toEqual({
      tags: ["tag_1", "tag_2"],
      custom_fields: { fld_1: "新值" },
    });
  });

  it("AC-F4-4: 状态只提供合法目标（S4 预校验），非法项禁用", async () => {
    const { user } = await openEdit({
      initialValues: editInitialValues({ status: "planning" }),
    });

    await user.click(
      screen.getByRole("button", { name: "projects.chipStatus" }),
    );

    // planning → {todo, cancelled}（+ 自身）；doing / done 不在状态机出边内。
    const doing = await screen.findByRole("button", {
      name: "projects.taskStatusDoing",
    });
    expect(doing).toHaveAttribute("aria-disabled", "true");
    await user.click(doing);
    // 禁用项点击后不改变选择：合法项仍可选，非法项不生效。
    await user.click(
      screen.getByRole("button", { name: "projects.taskStatusTodo" }),
    );

    await user.click(editSubmit());

    await waitFor(() => expect(onSubmitEditMock).toHaveBeenCalledTimes(1));
    expect(onSubmitEditMock.mock.calls[0][0]).toEqual({ status: "todo" });
  });

  it("AC-F4-4: 服务端 409 走 apiErrorMessage 展示，且不本地乐观改、不关闭", async () => {
    const { user, onClose } = await openEdit();
    const error = new Error(
      'Request failed: {"error":{"code":"PROJECT_TASK_STATUS_INVALID","message":"该任务状态流转不合法。"}}',
    );
    onSubmitEditMock.mockRejectedValueOnce(error);

    await user.click(
      screen.getByRole("button", { name: "projects.chipStatus" }),
    );
    await user.click(
      await screen.findByRole("button", {
        name: "projects.taskStatusCancelled",
      }),
    );
    await user.click(editSubmit());

    await waitFor(() => expect(messageErrorMock).toHaveBeenCalled());
    expect(messageErrorMock.mock.calls[0][0]).toContain(
      "该任务状态流转不合法。",
    );
    expect(onClose).not.toHaveBeenCalled();
    // 提交体只有 status —— 没有把其它字段一并写回。
    expect(onSubmitEditMock.mock.calls[0][0]).toEqual({ status: "cancelled" });
  });

  it("AC-F4-6: 环检测——自身与后代不出现在父任务选项里，且提交被阻止", async () => {
    const self = task({ task_id: "tsk_1", title: "自身" });
    const child = task({
      task_id: "tsk_2",
      title: "子任务",
      parent_id: "tsk_1",
    });
    const grandchild = task({
      task_id: "tsk_3",
      title: "孙任务",
      parent_id: "tsk_2",
    });
    const other = task({ task_id: "tsk_4", title: "无关任务" });
    const { user } = await openEdit({
      tasks: [self, child, grandchild, other],
    });

    // ① 选项面：tsk_1/2/3 全部被排除，tsk_4 保留。
    await user.click(screen.getByRole("button", { name: "projects.chipMore" }));
    await waitFor(() =>
      expect(screen.getByRole("combobox")).toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByRole("combobox"));
    const options = Array.from(
      document.querySelectorAll(".ant-select-item-option"),
    ).map((node) => node.textContent);
    expect(options).toContain("无关任务");
    expect(options).not.toContain("自身");
    expect(options).not.toContain("子任务");
    expect(options).not.toContain("孙任务");
  });

  it("AC-F4-6: 基线本身是指向后代时给出提示并阻止提交（服务端 400 的前端兜底）", async () => {
    const self = task({ task_id: "tsk_1", title: "自身" });
    const child = task({
      task_id: "tsk_2",
      title: "子任务",
      parent_id: "tsk_1",
    });
    const { user, onClose } = await openEdit({
      tasks: [self, child],
      initialValues: editInitialValues({ parent_id: "tsk_2" }),
    });

    expect(
      await screen.findByText("apiErrors.PROJECT_TASK_PARENT_INVALID"),
    ).toBeInTheDocument();

    await user.click(editSubmit());

    expect(onSubmitEditMock).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(messageErrorMock).toHaveBeenCalledWith(
      "apiErrors.PROJECT_TASK_PARENT_INVALID",
    );
  });

  it("AC-F4-3: 标题为空不提交（沿用服务端 422 的前端短路）", async () => {
    const { user } = await openEdit();

    await user.clear(titleInput());
    await user.click(editSubmit());

    expect(onSubmitEditMock).not.toHaveBeenCalled();
  });

  /**
   * ★ 判别性对照（可复跑）：逐个字段改一次，断言提交体恰好带上那个字段。
   * 任何「编辑模式漏传某字段」的变异都会让对应的这一行变红。
   */
  it.each([
    {
      field: "title",
      change: async (user: UserEvent) => {
        await user.clear(titleInput());
        await user.type(titleInput(), "新标题");
      },
      expected: { title: "新标题" },
    },
    {
      field: "description",
      change: async (user: UserEvent) => {
        await user.clear(descriptionInput());
        await user.type(descriptionInput(), "新描述");
      },
      expected: { description: "新描述" },
    },
    {
      field: "priority",
      change: async (user: UserEvent) => {
        await user.click(
          screen.getByRole("button", { name: "projects.chipPriority" }),
        );
        await user.click(await screen.findByRole("button", { name: "2" }));
      },
      expected: { priority: 2 },
    },
    {
      field: "status",
      change: async (user: UserEvent) => {
        await user.click(
          screen.getByRole("button", { name: "projects.chipStatus" }),
        );
        await user.click(
          await screen.findByRole("button", {
            name: "projects.taskStatusTodo",
          }),
        );
      },
      expected: { status: "todo" },
    },
    {
      field: "executors",
      change: async (user: UserEvent) => {
        // ★ P2-6 重指向（只改定位器）：`agent` / `team` 行现在在「智能体」chip。
        //   `expected` 逐字未改 —— 断言的仍是「提交体恰好带 executors」。
        await user.click(
          screen.getByRole("button", { name: "projects.chipAgent" }),
        );
        await user.click(
          await screen.findByRole("button", { name: "agent:agent-a" }),
        );
      },
      expected: { executors: ["agent:agent-a"] },
    },
    {
      field: "tags",
      change: async (user: UserEvent) => {
        await user.click(
          screen.getByRole("button", { name: "projects.chipTags" }),
        );
        await user.click(await screen.findByRole("button", { name: /紧急/ }));
      },
      expected: { tags: ["tag_1"] },
    },
    {
      field: "parent_id",
      change: async (user: UserEvent) => {
        await user.click(
          screen.getByRole("button", { name: "projects.chipMore" }),
        );
        await waitFor(() =>
          expect(screen.getByRole("combobox")).toBeInTheDocument(),
        );
        fireEvent.mouseDown(screen.getByRole("combobox"));
        const option = await waitFor(() => {
          const found = Array.from(
            document.querySelectorAll(".ant-select-item-option"),
          ).find((node) => node.textContent === "另一个任务");
          expect(found).toBeTruthy();
          return found as HTMLElement;
        });
        fireEvent.click(option);
      },
      expected: { parent_id: "tsk_2" },
    },
    {
      field: "custom_fields",
      change: async (user: UserEvent) => {
        await user.click(
          screen.getByRole("button", { name: "projects.chipMore" }),
        );
        const input = await screen.findByLabelText("备注");
        await user.type(input, "值");
      },
      expected: { custom_fields: { fld_1: "值" } },
    },
  ])(
    "★ 判别性对照：改 $field → 提交体恰好带该字段",
    async ({ change, expected }) => {
      const sibling = task({ task_id: "tsk_2", title: "另一个任务" });
      const { user } = await openEdit({
        tasks: [task(), sibling],
        initialValues: editInitialValues({
          tag_ids: [],
          custom_fields: {},
          parent_id: null,
        }),
      });

      await change(user);
      await user.click(editSubmit());

      await waitFor(() => expect(onSubmitEditMock).toHaveBeenCalledTimes(1));
      expect(onSubmitEditMock.mock.calls[0][0]).toEqual(expected);
    },
  );
});

// ── P2-6 · 多主体弹窗（PLAN §CD · C4-CREATE 五项契约 / UI-SPEC §3–§7） ──────────
//
// 本段逐条钉住本卡的四项交付：① 入口上移 ② 两个**独立**多选 picker ③ 空态
// ④ 编辑复用（含「重开必须显式播种」）。每一条都能被一次注入打红 —— 对照见
// `team/2026-10-08-110000/EVIDENCE-P2-6.md` §4（I1–I9）。

/** 富样本：2 用户（有名字）+ 1 专家（`agent`）+ 1 团队 + 1 取值域外伪成员。 */
const MULTI_MEMBERS: ProjectMember[] = [
  {
    subject_type: "user",
    subject_id: "7",
    user_id: 7,
    role: "owner",
    created_at: 1,
    name: "张三",
  },
  {
    subject_type: "user",
    subject_id: "8",
    user_id: 8,
    role: "member",
    created_at: 1,
    name: "李四",
  },
  {
    subject_type: "agent",
    subject_id: "agent-a",
    user_id: null,
    role: "member",
    created_at: 1,
    name: "Bot One",
  },
  {
    subject_type: "team",
    subject_id: "backend",
    user_id: null,
    role: "member",
    created_at: 1,
    name: "Squad",
  },
  // ★ 取值域外的脏数据（`ProjectSubjectType = user | agent | team`）：两个 popover 都必须
  //   **静默丢弃**它且不报错（UI-SPEC §4.4 互斥矩阵末行 / UIS-X-04）。双重断言**只**出现在
  //   本测试夹具里模拟「服务端返回了未知类型」——生产代码一行类型都没有放宽。
  {
    subject_type: "robot",
    subject_id: "r1",
    user_id: null,
    role: "member",
    created_at: 1,
    name: "R2",
  } as unknown as ProjectMember,
];

const chip = (key: string) =>
  screen.getByRole("button", { name: `projects.${key}` });

/** chip 行上各 chip 的可见文本前缀（`：` 之前的标签部分）。 */
function chipRowLabels(): string[] {
  const toolbar = screen.getByTestId("project-chip-toolbar");
  return Array.from(toolbar.querySelectorAll("button")).map(
    (button) => (button.textContent ?? "").split("：")[0],
  );
}

/** Mounts the create dialog against a given member feed. */
async function mountMulti(
  members: ProjectMember[] = MULTI_MEMBERS,
): Promise<{ user: UserEvent }> {
  listMembersMock.mockReset().mockResolvedValue(members);
  render(
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
  await waitFor(() => expect(chip("chipAssignee")).toBeInTheDocument());
  return { user };
}

describe("P2-6 · TaskCreateModal 多主体改造（C4-CREATE）", () => {
  beforeEach(() => {
    getMock
      .mockReset()
      .mockResolvedValue({ project_id: "prj_1", name: "Apollo" });
    createTaskMock.mockReset().mockResolvedValue({ task_id: "tsk_1" });
    dispatchTaskMock.mockReset();
    listMembersMock.mockReset().mockResolvedValue(MULTI_MEMBERS);
    listTagsMock.mockReset().mockResolvedValue([]);
    listFieldsMock.mockReset().mockResolvedValue([]);
    uploadMock.mockReset();
    deleteAttachmentMock.mockReset().mockResolvedValue({ deleted: true });
    messageSuccessMock.mockReset();
    messageErrorMock.mockReset();
  });

  it("① 入口上移：chip 行 7 枚按冻结顺序排列，底部那枚与 footerButton 包裹层一齐退场", async () => {
    await mountMulti();

    // PLAN §CD ① / UI-SPEC §3.1 K1 的冻结顺序（既有 5 枚相对先后逐位不变）。
    expect(chipRowLabels()).toEqual([
      "projects.chipStatus",
      "projects.chipPriority",
      "projects.chipAssignee",
      "projects.chipAgent",
      "projects.chipTags",
      "projects.chipProject",
      "projects.chipMore",
    ]);
    // 入口退场：`switchToAgent` 文案在弹窗 DOM 内**零出现**（UIS-C-03）。
    expect(
      screen.queryByRole("button", { name: "projects.switchToAgent" }),
    ).toBeNull();
    // 底部包裹层一并移除（UIS-C-02）——它曾是唯一会让 chip 与其它 flex item 错位的层。
    expect(document.querySelectorAll('[class*="footerButton"]').length).toBe(0);
  });

  it("②-a 分配 picker：候选**只有** user（无 agent / team / 未知类型，且不读全局目录）", async () => {
    const { user } = await mountMulti();

    await user.click(chip("chipAssignee"));

    for (const key of ["user:7", "user:8"]) {
      expect(
        await screen.findByRole("button", { name: key }),
      ).toBeInTheDocument();
    }
    // 互斥：人侧 popover 里**永不出现**执行主体或未知类型（UIS-X-01/X-02/X-04）。
    for (const key of ["agent:agent-a", "team:backend", "robot:r1"]) {
      expect(screen.queryByRole("button", { name: key })).toBeNull();
    }
    // 可见文本是**名字**，机器键只在 `aria-label`（UIS-P-05）。
    expect(
      screen.getByRole("button", { name: "user:7" }).textContent,
    ).toContain("张三");
    expect(
      screen.getByRole("button", { name: "user:7" }).textContent,
    ).not.toContain("user:7");
  });

  it("②-b 智能体 picker：候选**只有** agent / team，且分「团队 / 专家」两组", async () => {
    const { user } = await mountMulti();

    await user.click(chip("chipAgent"));

    for (const key of ["agent:agent-a", "team:backend"]) {
      expect(
        await screen.findByRole("button", { name: key }),
      ).toBeInTheDocument();
    }
    // 互斥：执行主体侧**永不出现** user 或未知类型（UIS-X-01/X-03/X-04）。
    for (const key of ["user:7", "user:8", "robot:r1"]) {
      expect(screen.queryByRole("button", { name: key })).toBeNull();
    }
    // 两组都有成员 ⇒ 两个组标题都在（UIS-P-09）。
    expect(screen.getByText("projects.pickerGroupTeams")).toBeInTheDocument();
    expect(screen.getByText("projects.pickerGroupExperts")).toBeInTheDocument();
    // 分组是**视觉**区分，机器键仍是 `type:id`（UIS-P-11 同源口径）。
    expect(
      screen.getByRole("button", { name: "team:backend" }).textContent,
    ).toContain("Squad");
  });

  it("②-c 智能体分组：某一组为空 ⇒ 该组标题不渲染（不留空壳，UIS-P-09）", async () => {
    const { user } = await mountMulti(
      MULTI_MEMBERS.filter((member) => member.subject_type === "agent"),
    );

    await user.click(chip("chipAgent"));

    expect(
      await screen.findByRole("button", { name: "agent:agent-a" }),
    ).toBeInTheDocument();
    expect(screen.getByText("projects.pickerGroupExperts")).toBeInTheDocument();
    expect(screen.queryByText("projects.pickerGroupTeams")).toBeNull();
  });

  it("②-d 多选 + 提交体：分配选 2 人、智能体选 1 团队 1 专家 ⇒ owners.length===2 && executors.length===2", async () => {
    const { user } = await mountMulti();
    await user.type(titleInput(), "多主体任务");

    await user.click(chip("chipAssignee"));
    await user.click(await screen.findByRole("button", { name: "user:7" }));
    await user.click(await screen.findByRole("button", { name: "user:8" }));
    // 多选而非单选替换：第一项仍保持选中。
    expect(screen.getByRole("button", { name: "user:7" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(chip("chipAgent"));
    await user.click(
      await screen.findByRole("button", { name: "team:backend" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "agent:agent-a" }),
    );

    await user.click(submitButton());
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
    const body = createTaskMock.mock.calls[0][1];
    expect(body.owners).toHaveLength(2);
    expect(body.executors).toHaveLength(2);
    // RC-16 ②：executors 里 `team:` 与 `agent:` **各 1**。
    expect(
      body.executors.filter((ref: string) => ref.startsWith("team:")),
    ).toHaveLength(1);
    expect(
      body.executors.filter((ref: string) => ref.startsWith("agent:")),
    ).toHaveLength(1);
    expect(body.assignee_type).toBeUndefined();
    expect(body.assignee_id).toBeUndefined();
  });

  it("③-a 空态可保存：两项都不选 ⇒ 两 chip 只有标签、无 alert、按钮可点、保存成功且两数组为 []", async () => {
    const { user } = await mountMulti();
    await user.type(titleInput(), "无主体任务");

    // 未选 ⇒ chip 文本 = 标签本身（`ChipButton` 的 `summary ? … : label`，零新增空态代码）。
    expect(chip("chipAssignee").textContent).toBe("projects.chipAssignee");
    expect(chip("chipAgent").textContent).toBe("projects.chipAgent");
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(submitButton()).toBeEnabled();

    await user.click(submitButton());
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
    const body = createTaskMock.mock.calls[0][1];
    expect(body.owners).toEqual([]);
    expect(body.executors).toEqual([]);
    expect(messageErrorMock).not.toHaveBeenCalled();
  });

  it("③-b 空候选：智能体 chip **不禁用**，点开显示 `noAgentMembers`（禁用会让空态永远看不到）", async () => {
    const { user } = await mountMulti(
      MULTI_MEMBERS.filter((member) => member.subject_type === "user"),
    );

    // UIS-X-08：禁用时 `ChipButton` 根本不渲染 Popover ⇒ 空态说明不可达。
    expect(chip("chipAgent")).not.toHaveAttribute("aria-disabled");
    await user.click(chip("chipAgent"));
    expect(
      await screen.findByText("projects.noAgentMembers"),
    ).toBeInTheDocument();
  });

  it("③-c 空候选：分配 chip 同理显示 `noUserMembers`", async () => {
    const { user } = await mountMulti(
      MULTI_MEMBERS.filter(
        (member) =>
          member.subject_type === "agent" || member.subject_type === "team",
      ),
    );

    expect(chip("chipAssignee")).not.toHaveAttribute("aria-disabled");
    await user.click(chip("chipAssignee"));
    expect(
      await screen.findByText("projects.noUserMembers"),
    ).toBeInTheDocument();
  });

  it("③-d 零成员：两 chip 都不禁用，各自显示**自己的**空态文案（不再共用 `noMembers`）", async () => {
    const { user } = await mountMulti([]);

    await user.click(chip("chipAssignee"));
    expect(
      await screen.findByText("projects.noUserMembers"),
    ).toBeInTheDocument();
    await user.click(chip("chipAgent"));
    expect(
      await screen.findByText("projects.noAgentMembers"),
    ).toBeInTheDocument();
  });

  it("④-a 编辑复用：两个 picker 各回填 2/2，各移除 1 个 ⇒ patch owners / executors 各只剩 1", async () => {
    const onSubmitEdit = vi.fn().mockResolvedValue(undefined);
    listMembersMock.mockResolvedValue(MULTI_MEMBERS);

    render(
      <TaskCreateModal
        open
        mode="edit"
        projectId="prj_1"
        tasks={[task()]}
        canEdit
        onClose={vi.fn()}
        onCreated={vi.fn()}
        initialValues={editInitialValues({
          owners: [7, 8],
          executors: ["agent:agent-a", "team:backend"],
        })}
        onSubmitEdit={onSubmitEdit}
      />,
    );
    const user = userEvent.setup();
    await waitFor(() => expect(titleInput()).toHaveValue("原任务"));

    // chip 摘要 = `{首项名} +{N}`（UI-SPEC §5.3 UIS-T-03）：两侧各 2 个 ⇒ 各折叠 1 枚。
    expect(chip("chipAssignee")).toHaveTextContent("张三 +1");
    expect(chip("chipAgent")).toHaveTextContent("Bot One +1");

    // 回填的**身份**判据：4 个主体逐一在选中态（只数「+1」会放过「换人但数量对」）。
    await user.click(chip("chipAssignee"));
    for (const key of ["user:7", "user:8"]) {
      expect(await screen.findByRole("button", { name: key })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    }
    // 各移除 1 个：分配侧去 user:7，智能体侧去 team:backend。
    await user.click(screen.getByRole("button", { name: "user:7" }));
    await user.click(chip("chipAgent"));
    for (const key of ["agent:agent-a", "team:backend"]) {
      expect(await screen.findByRole("button", { name: key })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    }
    await user.click(screen.getByRole("button", { name: "team:backend" }));

    await user.click(editSubmit());
    await waitFor(() => expect(onSubmitEdit).toHaveBeenCalledTimes(1));
    expect(onSubmitEdit.mock.calls[0][0]).toEqual({
      owners: [8],
      executors: ["agent:agent-a"],
    });
  });

  it("④-b 编辑复用：编辑 A → 关 → 新建 ⇒ **不残留** A 的取值（重开必须显式播种空值）", async () => {
    listMembersMock.mockResolvedValue(MULTI_MEMBERS);
    const base = {
      projectId: "prj_1",
      tasks: [task()],
      canEdit: true,
      onClose: vi.fn(),
      onCreated: vi.fn(),
    };

    const view = render(
      <TaskCreateModal
        {...base}
        open
        mode="edit"
        initialValues={editInitialValues({
          title: "任务 A",
          owners: [7],
          executors: ["agent:agent-a"],
        })}
        onSubmitEdit={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await waitFor(() => expect(titleInput()).toHaveValue("任务 A"));
    // 先证明**真的**预填了 A（防空转）：两枚 chip 都带值。
    expect(chip("chipAssignee")).toHaveTextContent("张三");
    expect(chip("chipAgent")).toHaveTextContent("Bot One");

    // 关闭 ⇒ 再以**新建**模式重开（无 `initialValues`）。
    view.rerender(
      <TaskCreateModal
        {...base}
        open={false}
        mode="edit"
        initialValues={editInitialValues({ title: "任务 A", owners: [7] })}
        onSubmitEdit={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByLabelText("projects.createTaskTitle")).toBeNull(),
    );
    view.rerender(<TaskCreateModal {...base} open mode="create" />);

    await waitFor(() => expect(titleInput()).toHaveValue(""));
    expect(descriptionInput()).toHaveValue("");
    // ★ 核心判据：两枚 chip 回到**纯标签**空态，而不是上一次的 `张三` / `Bot One`。
    expect(chip("chipAssignee").textContent).toBe("projects.chipAssignee");
    expect(chip("chipAgent").textContent).toBe("projects.chipAgent");
    void user;
  });

  it("④-c 拒绝码防线：新建/编辑**只有一个**多主体 picker 实现（无第二套）", () => {
    const modalSource = readFileSync(
      resolve(__dirname, "TaskCreateModal.tsx"),
      "utf8",
    );
    // 唯一的行渲染器：定义 1 处、两枚 chip 各调 1 次 —— 这正是「两套实现 ⇒ failed」的判据。
    expect(modalSource.match(/const renderSubjectPicker/g)).toHaveLength(1);
    expect(modalSource.match(/renderSubjectPicker\(\{/g)).toHaveLength(2);
    // `P2-12` 的合体 picker 与底部的 agent picker 都**不得**复活。
    expect(modalSource).not.toContain("renderAssigneePicker");
    expect(modalSource).not.toContain("renderAgentPicker");
    // 命名空间硬约束：主体展示模型只有 `Board` 一份，本文件**不得**另立同名实现。
    expect(modalSource).toContain('from "./Board"');
    for (const symbol of ["memberKey", "subjectDisplayName", "taskSubjects"]) {
      expect(modalSource).toMatch(
        new RegExp(
          `import\\s*\\{[\\s\\S]*?\\b${symbol}\\b[\\s\\S]*?\\}\\s*from "\\./Board"`,
        ),
      );
      expect(modalSource).not.toContain(`function ${symbol}`);
      expect(modalSource).not.toContain(`const ${symbol}`);
    }
  });
});
