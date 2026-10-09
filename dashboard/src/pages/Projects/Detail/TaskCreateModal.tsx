import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Checkbox,
  DatePicker,
  Input,
  InputNumber,
  Modal,
  Select,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  Bot,
  Check,
  Flag,
  MoreHorizontal,
  Paperclip,
  Tag as TagIcon,
  Trash2,
  User,
  Users,
  X,
} from "lucide-react";
import dayjs, { type Dayjs } from "dayjs";
import { useTranslation } from "react-i18next";

import {
  projectsApi,
  type ProjectAttachment,
  type ProjectCustomFieldDefinition,
  type ProjectCustomFieldWriteValue,
  type ProjectMember,
  type ProjectTagDefinition,
  type ProjectTask,
  type ProjectTaskStatus,
  type ProjectTaskUpdateBody,
} from "../../../api/modules/projects";
import { projectMetadataApi } from "../../../api/modules/projectMetadata";
import { useProjectMembers } from "../../../hooks/useProjectMembers";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import { ChipButton, ChipToolbar } from "../../../components/ChipToolbar";
import {
  COLUMN_STATUSES,
  isTransitionAllowed,
  STATUS_LABEL_KEYS,
} from "../utils/taskStatus";
// The **shared** subject display model (P2-4): `taskSubjects()` is the single
// parser for the frozen `owners` / `executors` shape, `subjectDisplayName()`
// the single namer (name, else `type:#last6`) and `memberKey()` the single
// lookup join key. Reusing them here is what keeps this dialog's summary
// word-for-word identical to the card's 行④ — a second copy would fork the
// fallback text (PLAN §CD ①/②, UIS-T-04 "必须与行④逐字同构").
import { memberKey, subjectDisplayName, taskSubjects } from "./Board";
import type { TaskSubjectRef } from "./Board";
import styles from "./TaskCreateModal.module.less";

const { Text } = Typography;

/** PLAN §7.2: single-file ceiling; the server is still authoritative. */
const MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024;

const PRIORITY_OPTIONS = [0, 1, 2, 3, 4];

/**
 * The dialog's value shape — the **single** field definition shared by both
 * modes (PLAN §5.1 / FIND-10). ``start_at`` / ``due_at`` are unix seconds (the
 * API's unit), so a caller can hand a ``ProjectTask`` straight over.
 *
 * ``owners`` / ``executors`` are the frozen two-subject write shape (PLAN
 * §B2.1b): ``owners`` holds ``users.id`` numbers, ``executors`` holds
 * ``agent:<id>`` / ``team:<role>`` strings. The dialog's own value shape uses
 * **ordered arrays** like the API, so ``taskToValues()`` can hand a
 * ``ProjectTask`` straight over and the submit path can diff two sets without
 * an intermediate conversion.
 */
export interface TaskCreateValues {
  /** The task being edited — edit mode needs it for the parent cycle guard. */
  task_id?: string;
  title: string;
  description: string;
  status: ProjectTaskStatus;
  priority: number;
  owners: number[];
  executors: string[];
  tag_ids: string[];
  start_at: number | null;
  due_at: number | null;
  parent_id: string | null;
  deps: string[];
  custom_fields: Record<string, ProjectCustomFieldWriteValue>;
}

/**
 * What an edit submits: only the **changed** keys (PLAN §5.1 diff submit), typed
 * against the API patch so the two cannot drift.
 *
 * ``tags`` / ``deps`` / ``custom_fields`` are **full replacements** (PLAN §5.2):
 * the submitted array / object *is* the final set, an empty one clears it, and
 * an absent key leaves it untouched (the route uses ``exclude_unset``).
 *
 * ``owners`` / ``executors`` follow the frozen **three states** (PLAN §B2.5 (a),
 * verbatim against ``TaskPatch``): absent = leave that side alone, ``[]`` =
 * clear it, non-empty = replace the whole set. They replace the withdrawn
 * single-valued ``assignee_type`` / ``assignee_id`` pair, which the backend
 * refuses with a 422 when a request carries both encodings (V3) — so the old
 * pair must never be sent again.
 */
export type TaskPatchValues = Pick<
  ProjectTaskUpdateBody,
  | "title"
  | "description"
  | "priority"
  | "deps"
  | "start_at"
  | "due_at"
  | "status"
  | "owners"
  | "executors"
  | "parent_id"
  | "tags"
  | "custom_fields"
>;

interface TaskCreateModalProps {
  open: boolean;
  projectId: string;
  /** Existing tasks of this project — the only parent-task source (R5). */
  tasks: ProjectTask[];
  canEdit: boolean;
  onClose: () => void;
  onCreated: () => void | Promise<void>;
  /** Defaults to ``"create"``; ``"edit"`` reuses the very same field set. */
  mode?: "create" | "edit";
  /** Edit mode: the task's current values (PLAN §5.1). */
  initialValues?: Partial<TaskCreateValues>;
  /** Edit mode submit: awaits the caller's patch request, so its failures show here. */
  onSubmitEdit?: (patch: TaskPatchValues) => Promise<void>;
}

function toEpochSeconds(value: Dayjs | null): number | null {
  return value ? Math.floor(value.valueOf() / 1000) : null;
}

/**
 * One row's **lookup key** — the same `"{type}:{id}"` join the card's
 * `nameByKey` uses (`Board.memberKey`). It is the row's `aria-label` (never its
 * visible text: UIS-P-05 moved the machine string out of the copy) and the
 * React key, so the two pickers can never disagree about a member's identity.
 */
const memberRef = (member: ProjectMember): TaskSubjectRef => ({
  type: member.subject_type,
  id: member.subject_id,
});

/** A row's **visible** text: the member name, else its bare `subject_id` (UIS-P-03). */
const memberRowText = (member: ProjectMember): string =>
  (member.name ?? "").trim() || member.subject_id;

/**
 * A **user** member's ``users.id`` — the only value ``owners`` carries (PLAN
 * §B2.1b). The member row stores that id as text in ``subject_id`` (the owner is
 * added with ``subject_id=str(user.id)``), and ``user_id`` is the same integer
 * when the row resolved one; either source is the frozen integer, so this reads
 * whichever the row carries and never invents an id. A user row with neither
 * (an unresolved legacy row) has no owner id to send ⇒ ``null``.
 */
function memberOwnerId(member: ProjectMember): number | null {
  if (member.subject_type !== "user") return null;
  if (typeof member.user_id === "number") return member.user_id;
  const parsed = Number(member.subject_id);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * An ``agent`` / ``team`` member's ``executors`` element — ``"{type}:{id}"`` via
 * the shared ``memberKey`` join. ``user`` rows are **not** executors (V2 rejects
 * a ``user:…`` prefix), and any unknown ``subject_type`` is not a candidate on
 * either side, so both return ``null`` (PLAN §CD ② 的「互斥且完备」).
 */
function memberExecutorRef(member: ProjectMember): string | null {
  if (member.subject_type !== "agent" && member.subject_type !== "team") {
    return null;
  }
  return memberKey(member.subject_type, member.subject_id);
}

/**
 * The two pickers' **single** row-rendering spec (PLAN §CD ②/③/⑤).
 *
 * The dialog renders one list of member rows; the **only** thing that differs
 * between 分配 and 智能体 is which candidates they offer and which side of the
 * two-subject shape they write. Both modes (create / edit) go through the same
 * function, so "为编辑另写一套 picker" is structurally impossible.
 */
interface SubjectPickerSpec {
  /** Candidates, already narrowed to this slot's subject types (never global). */
  candidates: ProjectMember[];
  /** Localized text when this slot has no candidate at all. */
  emptyText: string;
  /** Optional headings (`{ subjectType, title }`); an empty group renders no heading. */
  groups?: { subjectType: string; title: string }[];
  /** Is this member currently on this slot? */
  isActive: (member: ProjectMember) => boolean;
  /** Add / remove this member from this slot (a toggle, never a replace). */
  onToggle: (member: ProjectMember) => void;
}

/** ``owners`` / ``executors`` order-insensitively equal (the API is set-valued). */
function sameNumbers(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort((x, y) => x - y);
  const sortedB = [...b].sort((x, y) => x - y);
  return sortedA.every((value, index) => value === sortedB[index]);
}

function sameStrings(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((value, index) => value === sortedB[index]);
}

/** Edit-mode baseline: the same shape as the form, from ``initialValues``. */
function toValues(
  source: Partial<TaskCreateValues> | undefined,
): TaskCreateValues {
  return {
    task_id: source?.task_id,
    title: source?.title ?? "",
    description: source?.description ?? "",
    status: source?.status ?? "planning",
    priority: source?.priority ?? 0,
    owners: source?.owners ?? [],
    executors: source?.executors ?? [],
    tag_ids: source?.tag_ids ?? [],
    start_at: source?.start_at ?? null,
    due_at: source?.due_at ?? null,
    parent_id: source?.parent_id ?? null,
    deps: source?.deps ?? [],
    custom_fields: source?.custom_fields ?? {},
  };
}

function sameIds(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((value, index) => value === sortedB[index]);
}

function sameCustomFields(
  a: Record<string, ProjectCustomFieldWriteValue>,
  b: Record<string, ProjectCustomFieldWriteValue>,
): boolean {
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((key) => a[key] === b[key]);
}

/**
 * Collect a task and every descendant, following ``parent_id``. ``tasks`` is the
 * project's own list (the only task source this dialog has), and the walk is
 * guarded so a cycle in the data cannot loop forever.
 */
function descendantIds(taskId: string, tasks: ProjectTask[]): Set<string> {
  const childrenOf = new Map<string, string[]>();
  for (const task of tasks) {
    if (!task.parent_id) continue;
    const bucket = childrenOf.get(task.parent_id) ?? [];
    bucket.push(task.task_id);
    childrenOf.set(task.parent_id, bucket);
  }
  const found = new Set<string>();
  const stack = [taskId];
  while (stack.length > 0) {
    const current = stack.pop() as string;
    for (const child of childrenOf.get(current) ?? []) {
      if (found.has(child)) continue;
      found.add(child);
      stack.push(child);
    }
  }
  return found;
}

/**
 * One dialog, two modes (PLAN §5, F4).
 *
 * ``create`` (R3, SPEC AC-U-8): breadcrumb + inline title + inline description +
 * chip toolbar (status / priority / assignee / agent / tags / project / more) +
 * attachments + "keep creating" + ⌘↵ submit.
 *
 * ★ 两个多选 picker（`P2-6` / PLAN §CD ②）：「分配」只列项目成员里的 ``user``，
 * 「智能体」只列 ``agent`` / ``team``（= 团队 / 专家）—— 两个候选集是
 * ``ProjectSubjectType`` 的全划分，**互斥且完备**。两者都**多选**，且都走同一个
 * ``renderSubjectPicker``，所以「多选」不在两处各写一份。已选可为空（都不是必选）：
 * 空 ⇒ chip 只显示标签、**不报错、保存可成功**。
 *
 * ``edit`` reuses **the same fields, the same pickers and the same validation** —
 * no second form component, no duplicated field definition, no edit-only picker —
 * and differs only in three ways:
 * it seeds from ``initialValues``, it submits a **diff** through
 * ``onSubmitEdit``, and its title / button / success copy come from the
 * ``editTask*`` keys. Status still moves through the state machine: an illegal
 * target is disabled up front (PLAN S4) and the server's 409 is what the user
 * sees if one slips through — there is no optimistic local status write.
 *
 * ★ 复用的播种纪律（见下方 ``useEffect(…, [open])``）：本组件**不是** rc-field-form，
 * 故 ``initialValues`` 的「只合并、不重置」之坑在此表现为 state 残留。每次 ``open``
 * 翻真都**显式**把**全部**字段（含两个主体数组的**空值**）从 ``initialValues`` 播种
 * 一遍 ⇒ 上一次的取值绝不残留（`P1-16` 序号 17 同款判据）。
 *
 * Keyboard (PLAN §10.1): ⌘/Ctrl+Enter submits from anywhere in the dialog,
 * a bare Enter in the title submits, Enter in the description inserts a
 * newline, Esc closes and discards. Every submit path short-circuits while an
 * IME composition is active (S-5) — Enter there picks a candidate, it must
 * never create a task.
 */
export default function TaskCreateModal({
  open,
  projectId,
  tasks,
  canEdit,
  onClose,
  onCreated,
  mode = "create",
  initialValues,
  onSubmitEdit,
}: TaskCreateModalProps) {
  const { t } = useTranslation();
  const editing = mode === "edit";

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState<ProjectTaskStatus>("planning");
  const [priority, setPriority] = useState(0);
  const [owners, setOwners] = useState<number[]>([]);
  const [executors, setExecutors] = useState<string[]>([]);
  const [tagIds, setTagIds] = useState<string[]>([]);
  const [startAt, setStartAt] = useState<Dayjs | null>(null);
  const [dueAt, setDueAt] = useState<Dayjs | null>(null);
  const [parentId, setParentId] = useState<string | null>(null);
  const [customValues, setCustomValues] = useState<
    Record<string, ProjectCustomFieldWriteValue>
  >({});
  const [staged, setStaged] = useState<ProjectAttachment[]>([]);
  const [keepCreating, setKeepCreating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);

  const [projectName, setProjectName] = useState("");
  const [tags, setTags] = useState<ProjectTagDefinition[]>([]);
  const [definitions, setDefinitions] = useState<
    ProjectCustomFieldDefinition[]
  >([]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  // The values the current open started from: the diff is computed against
  // these, so an untouched field is never written back (PLAN §5.1).
  const baselineRef = useRef<TaskCreateValues>(toValues(undefined));
  const initialValuesRef = useRef(initialValues);
  initialValuesRef.current = initialValues;
  // Members only while the dialog is open: the hook is the single member feed
  // (R5 / AC-U-24) and must never touch the global user directory.
  const { members, loading: membersLoading } = useProjectMembers(
    open ? projectId : null,
  );

  // Tag + custom-field definitions are read once per open.
  useEffect(() => {
    if (!open || !projectId) return;
    let cancelled = false;
    projectsApi
      .get(projectId)
      .then((project) => {
        if (!cancelled) setProjectName(project.name);
      })
      .catch(() => undefined);
    projectMetadataApi
      .listTags(projectId)
      .then((rows) => {
        if (!cancelled) setTags(rows);
      })
      .catch(() => undefined);
    projectMetadataApi
      .listCustomFields(projectId)
      .then((rows) => {
        if (!cancelled) setDefinitions(rows);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open, projectId]);

  /**
   * 智能体候选 = `subject_type ∈ {agent, team}` —— 与「专家 = `subject_type ===
   * 'agent'`」的既有约定同判据（PLAN §CD ②：**不新增主体类型**，`ProjectSubjectType`
   * 的取值域只有 `user | agent | team`）。与 `ownerCandidates` 是 `ProjectSubjectType`
   * 的**全划分** ⇒ 两侧候选集**互斥且完备**，同一主体永不出现在两个 popover 里。
   */
  const agentMembers = useMemo(
    () =>
      members.filter(
        (member) =>
          member.subject_type === "agent" || member.subject_type === "team",
      ),
    [members],
  );

  /** 分配候选 = `subject_type === "user"`（PLAN §CD ② 冻结的收窄）。 */
  const ownerCandidates = useMemo(
    () => members.filter((member) => member.subject_type === "user"),
    [members],
  );

  /**
   * 成员名索引 —— 与卡片行④ `Board.tsx` 的 `nameByKey` **同键、同取值**
   * （`memberKey(subject_type, subject_id)` ⇒ `(name ?? "").trim()`），所以摘要里的
   * 名字解析与卡片逐字一致（UIS-T-04）。
   */
  const nameByKey = useMemo(
    () =>
      new Map(
        members.map((member) => [
          memberKey(member.subject_type, member.subject_id),
          (member.name ?? "").trim(),
        ]),
      ),
    [members],
  );

  /**
   * 当前双主体选择，**只经共享展示模型** `taskSubjects()` 读（PLAN §CD ②：
   * `executors` 的 `"type:id"` 拆分规则只有一份 —— 复用 Board 那份，不在此另写）。
   * `assignee_type` / `assignee_id` 恒传 `null`：写入面不再走单值镜像（V3 互斥）。
   */
  const subjectSlots = useMemo(
    () =>
      taskSubjects({
        owners,
        executors,
        assignee_type: null,
        assignee_id: null,
      }),
    [owners, executors],
  );

  /**
   * 「已选摘要」= 首项名 + `+{N}`（`N = 已选数 − 1`），空 ⇒ `null` ⇒ chip 只显示标签
   * （UI-SPEC §5.3 UIS-T-03；空态**零新增代码**：`ChipButton` 的 `summary ? … : label`
   * 天然给出 `分配` / `智能体` 两个纯标签 —— PLAN §CD ④）。
   */
  const selectionSummary = (refs: TaskSubjectRef[]): string | null => {
    if (refs.length === 0) return null;
    const head = subjectDisplayName(nameByKey, refs[0]);
    return refs.length > 1 ? `${head} +${refs.length - 1}` : head;
  };
  const ownersSummary = selectionSummary(subjectSlots.owners);
  const executorsSummary = selectionSummary(subjectSlots.executors);

  /** Drop staged (never bound) attachments — best effort, never blocks close. */
  const discardStaged = useCallback(
    (items: ProjectAttachment[]) => {
      for (const item of items) {
        void projectMetadataApi
          .deleteAttachment(projectId, item.artifact_id)
          .catch(() => undefined);
      }
    },
    [projectId],
  );

  const resetForm = useCallback(() => {
    setTitle("");
    setDescription("");
    setStatus("planning");
    setPriority(0);
    setOwners([]);
    setExecutors([]);
    setTagIds([]);
    setStartAt(null);
    setDueAt(null);
    setParentId(null);
    setCustomValues({});
    setStaged([]);
    setKeepCreating(false);
  }, []);

  // ★ 复用组件的**显式播种**（PLAN §CD ⑤ / P1-16 序号 17）：本弹窗被新建与编辑
  // 复用（``mode`` 只切标题/按钮文案与提交路径），所以**每次 ``open`` 翻真**都必须把
  // **全部**字段重新播种一遍 —— 包括两个主体数组的**空值**（``toValues(undefined)``
  // 给出 ``owners: []`` / ``executors: []``）。只播种一次或依赖「初值合并」会让上一次
  // 的取值残留（rc-field-form 的 ``initialValues`` 就是这个坑的形态；本组件用裸 state，
  // 等价风险由本 effect 承担）。★ 判据 = 「填值 ⇒ 关 ⇒ 重开（无 initialValues）⇒ 空」。
  // Keyed on ``open`` only — re-seeding on every render would clobber what the
  // user is typing (the values are read through a ref).
  useEffect(() => {
    if (!open) return;
    const seeded = toValues(initialValuesRef.current);
    baselineRef.current = seeded;
    setTitle(seeded.title);
    setDescription(seeded.description);
    setStatus(seeded.status);
    setPriority(seeded.priority);
    setOwners(seeded.owners);
    setExecutors(seeded.executors);
    setTagIds(seeded.tag_ids);
    setStartAt(seeded.start_at ? dayjs(seeded.start_at * 1000) : null);
    setDueAt(seeded.due_at ? dayjs(seeded.due_at * 1000) : null);
    setParentId(seeded.parent_id);
    setCustomValues(seeded.custom_fields);
    setStaged([]);
    setKeepCreating(false);
  }, [open]);

  /** The edited node and everything below it — illegal new parents (PLAN §5.2 ④). */
  const forbiddenParentIds = useMemo(() => {
    const editedId = initialValuesRef.current?.task_id;
    if (!editing || !editedId) return new Set<string>();
    return new Set([editedId, ...descendantIds(editedId, tasks)]);
  }, [editing, tasks]);

  /**
   * PLAN S4: an edit may only move the status along the frozen machine, so
   * illegal targets are disabled up front — exactly like the board's dropdown.
   * Creation is not a transition, so create mode keeps all seven.
   */
  const statusOptions = useMemo(() => {
    if (!editing) return COLUMN_STATUSES;
    const current = baselineRef.current.status;
    // The board's dropdown offers the current status plus every legal target;
    // this mirrors it exactly (PLAN S4: same experience at both entry points).
    return COLUMN_STATUSES.filter(
      (option) => option === current || isTransitionAllowed(current, option),
    );
  }, [editing]);

  const parentCycleBlocked =
    editing && parentId !== null && forbiddenParentIds.has(parentId);

  const handleCancel = () => {
    // PLAN §7.5.6: give up the staged files explicitly; uploads that were
    // already used by a create are bound and must not be touched.
    discardStaged(staged);
    resetForm();
    onClose();
  };

  /** Edit mode: only the keys that actually changed (PLAN §5.1). */
  const buildPatch = (trimmed: string): TaskPatchValues => {
    const baseline = baselineRef.current;
    const patch: TaskPatchValues = {};
    if (trimmed !== baseline.title) patch.title = trimmed;
    const nextDescription = description.trim();
    if (nextDescription !== baseline.description) {
      patch.description = nextDescription;
    }
    if (priority !== baseline.priority) patch.priority = priority;
    if (status !== baseline.status) patch.status = status;
    if (toEpochSeconds(startAt) !== baseline.start_at) {
      patch.start_at = toEpochSeconds(startAt);
    }
    if (toEpochSeconds(dueAt) !== baseline.due_at) {
      patch.due_at = toEpochSeconds(dueAt);
    }
    if (parentId !== baseline.parent_id) patch.parent_id = parentId;
    // Full replacements: the array / object *is* the final set (PLAN §5.2).
    // ``deps`` is part of the frozen edit field set but has no key in the frozen
    // i18n table, so no editor is rendered for it and it is never written back
    // (an absent key leaves the column untouched — the frozen "缺键 = 不改").
    if (!sameIds(tagIds, baseline.tag_ids)) patch.tags = tagIds;
    if (!sameCustomFields(customValues, baseline.custom_fields)) {
      patch.custom_fields = customValues;
    }
    // Two-subject three states (PLAN §B2.5 (a)): a side the user did not touch is
    // **absent** (leave it alone); a side whose final set differs is sent whole —
    // ``[]`` clears it, a non-empty array replaces it. Comparing sets rather than
    // tracking a dirty flag is what makes "did not touch" exact.
    if (!sameNumbers(owners, baseline.owners)) patch.owners = owners;
    if (!sameStrings(executors, baseline.executors)) {
      patch.executors = executors;
    }
    return patch;
  };

  const submitEdit = async (trimmed: string) => {
    if (!onSubmitEdit) return;
    const patch = buildPatch(trimmed);
    // Nothing changed: close instead of sending an empty patch.
    if (Object.keys(patch).length === 0) {
      resetForm();
      onClose();
      return;
    }
    await onSubmitEdit(patch);
    message.success(t("projects.editTaskSaved"));
    resetForm();
    onClose();
  };

  const submit = async () => {
    if (!canEdit || submitting) return;
    const trimmed = title.trim();
    if (!trimmed) return;
    // Front-end backstop for the cycle guard: the picker already hides these,
    // and the server answers 400 PROJECT_TASK_PARENT_INVALID if one arrives.
    if (parentCycleBlocked) {
      message.error(t("apiErrors.PROJECT_TASK_PARENT_INVALID"));
      return;
    }
    if (editing && !onSubmitEdit) return;
    setSubmitting(true);
    try {
      if (editing) {
        await submitEdit(trimmed);
        return;
      }
      await projectsApi.createTask(projectId, {
        title: trimmed,
        description: description.trim() || undefined,
        status,
        priority,
        // The frozen two-subject write shape (PLAN §B2.1b): ``owners`` are
        // ``users.id`` numbers, ``executors`` are ``agent:<id>`` / ``team:<role>``
        // strings. The withdrawn single-valued ``assignee_type`` / ``assignee_id``
        // pair is deliberately **not** sent — the backend refuses a request that
        // carries both encodings (V3, 422).
        owners,
        executors,
        due_at: toEpochSeconds(dueAt),
        start_at: toEpochSeconds(startAt),
        parent_id: parentId,
        tags: tagIds,
        custom_fields: customValues,
        attachment_ids: staged.map((item) => item.artifact_id),
      });
      message.success(t("projects.boardCreated"));
      await onCreated();
      if (keepCreating) {
        // PLAN §10.2 / S-4: clear title + description + due_at, keep the other
        // eight selections; attachments are already bound, so the list clears.
        setTitle("");
        setDescription("");
        setDueAt(null);
        setStaged([]);
      } else {
        resetForm();
        onClose();
      }
    } catch (error) {
      message.error(
        apiErrorMessage(
          error,
          t(
            editing ? "projects.boardMoveFailed" : "projects.boardCreateFailed",
          ),
          t,
        ),
      );
    } finally {
      setSubmitting(false);
    }
  };

  /** S-5: an IME composition owns Enter — never submit, never preventDefault. */
  const composing = (event: React.KeyboardEvent) =>
    event.nativeEvent.isComposing === true;

  const handleContainerKeyDown = (
    event: React.KeyboardEvent<HTMLDivElement>,
  ) => {
    if (event.key !== "Enter") return;
    if (!(event.metaKey || event.ctrlKey)) return;
    if (composing(event)) return;
    event.preventDefault();
    void submit();
  };

  const handleTitleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    // ⌘/Ctrl+Enter belongs to the container handler — avoid a double submit.
    if (event.metaKey || event.ctrlKey) return;
    if (composing(event)) return;
    event.preventDefault();
    void submit();
  };

  const handleFileChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !projectId) return;
    if (file.size > MAX_ATTACHMENT_BYTES) {
      message.error(t("projects.attachmentTooLarge"));
      return;
    }
    setUploading(true);
    try {
      const uploaded = await projectMetadataApi.uploadPendingAttachment(
        projectId,
        file,
      );
      setStaged((current) => [...current, uploaded]);
    } catch (error) {
      message.error(apiErrorMessage(error, t("projects.attachmentBadType"), t));
    } finally {
      setUploading(false);
    }
  };

  const removeStaged = (artifact: ProjectAttachment) => {
    setStaged((current) =>
      current.filter((item) => item.artifact_id !== artifact.artifact_id),
    );
    void projectMetadataApi
      .deleteAttachment(projectId, artifact.artifact_id)
      .catch(() => undefined);
  };

  const moreCount =
    (dueAt ? 1 : 0) +
    (startAt ? 1 : 0) +
    (parentId ? 1 : 0) +
    definitions.filter((definition) => {
      const value = customValues[definition.field_id];
      return value !== undefined && value !== null && value !== "";
    }).length;
  const statusSummary = t(STATUS_LABEL_KEYS[status]);
  const tagSummary =
    tagIds.length > 0
      ? tags
          .filter((tag) => tagIds.includes(tag.tag_id))
          .map((tag) => tag.name)
          .join("、")
      : null;

  const renderStatusPicker = () => (
    <div className={styles.optionList}>
      {COLUMN_STATUSES.map((option) => {
        // PLAN S4: an illegal target is refused here, never silently sent.
        const allowed = statusOptions.includes(option);
        return (
          <button
            key={option}
            type="button"
            className={`${styles.option} ${
              option === status ? styles.optionActive : ""
            } ${allowed ? "" : styles.optionDisabled}`}
            aria-disabled={allowed ? undefined : true}
            onClick={() => {
              if (!allowed) return;
              setStatus(option);
            }}
          >
            <span>{t(STATUS_LABEL_KEYS[option])}</span>
            {option === status ? <Check size={14} /> : null}
          </button>
        );
      })}
    </div>
  );

  const renderPriorityPicker = () => (
    <div className={styles.optionList}>
      {PRIORITY_OPTIONS.map((option) => (
        <button
          key={option}
          type="button"
          className={`${styles.option} ${
            option === priority ? styles.optionActive : ""
          }`}
          onClick={() => setPriority(option)}
        >
          <span>{option}</span>
          {option === priority ? <Check size={14} /> : null}
        </button>
      ))}
    </div>
  );

  /**
   * The dialog's **one and only** subject-row list (PLAN §CD ②/③/⑤).
   *
   * Both pickers — 分配 (人) and 智能体 (团队 / 专家) — and both modes
   * (create / edit) render through here; they differ **only** by `candidates`
   * and by which side of the frozen two-subject shape `onToggle` writes. The
   * click is a **toggle** (a second click removes), never a replace-on-click
   * slot: that is what lets the dialog carry 1..N owners *and* 1..M executors at
   * once and what makes each selected item individually removable. Rows are
   * native `<button>`s, so the selected state and the removal entry are both
   * keyboard reachable (PLAN §CD ③).
   *
   * The candidate list is **always** the project-member feed (`useProjectMembers`)
   * and never the global user / agent directory (R5 / AC-U-24).
   */
  const renderSubjectPicker = ({
    candidates,
    emptyText,
    groups,
    isActive,
    onToggle,
  }: SubjectPickerSpec) => {
    if (membersLoading) {
      return (
        <div className={styles.pickerCentered}>
          <Spin size="small" />
        </div>
      );
    }
    // Empty state: this slot has no candidate at all. The text is slot-specific
    // (`noUserMembers` / `noAgentMembers`), so "no people" and "no agents" are
    // never confused — and the chip stays **enabled**, because a disabled chip
    // renders no popover at all (`ChipToolbar.tsx:104`) and would hide this line.
    if (candidates.length === 0) {
      return <div className={styles.pickerEmpty}>{emptyText}</div>;
    }
    const renderRow = (member: ProjectMember) => {
      const ref = memberRef(member);
      const label = memberKey(ref.type, ref.id);
      const selected = isActive(member);
      const icon =
        member.subject_type === "team" ? (
          <Users size={14} aria-hidden />
        ) : member.subject_type === "agent" ? (
          <Bot size={14} aria-hidden />
        ) : null;
      return (
        <button
          key={label}
          type="button"
          className={`${styles.option} ${selected ? styles.optionActive : ""}`}
          // The machine key stays in the accessible name only — the visible copy
          // is the human name (UIS-P-05), while `type:id` remains the locator.
          aria-label={label}
          aria-pressed={selected}
          onClick={() => onToggle(member)}
        >
          <span className={styles.optionName}>
            {icon}
            <span className={styles.optionLabel}>{memberRowText(member)}</span>
          </span>
          {selected ? <Check size={14} aria-hidden /> : null}
        </button>
      );
    };
    if (groups) {
      return (
        <div className={styles.optionList}>
          {groups.map((group) => {
            const rows = candidates.filter(
              (member) => member.subject_type === group.subjectType,
            );
            // 组为空 ⇒ 不渲染标题（不留空壳；UI-SPEC §4.3 UIS-P-09）。
            if (rows.length === 0) return null;
            return (
              <div key={group.subjectType} className={styles.optionGroup}>
                <div className={styles.optionGroupTitle}>{group.title}</div>
                {rows.map(renderRow)}
              </div>
            );
          })}
        </div>
      );
    }
    return <div className={styles.optionList}>{candidates.map(renderRow)}</div>;
  };

  /** 分配 picker：人侧。候选 = `user`；写入 = `owners`（`users.id`）。 */
  const renderOwnerPicker = () =>
    renderSubjectPicker({
      candidates: ownerCandidates,
      emptyText: t("projects.noUserMembers"),
      isActive: (member) => {
        const ownerId = memberOwnerId(member);
        return ownerId !== null && owners.includes(ownerId);
      },
      onToggle: (member) => {
        const ownerId = memberOwnerId(member);
        if (ownerId === null) return;
        const selected = owners.includes(ownerId);
        setOwners((current) =>
          selected
            ? current.filter((id) => id !== ownerId)
            : [...current, ownerId],
        );
      },
    });

  /** 智能体 picker：执行主体侧。候选 = `agent` | `team`；写入 = `executors`。 */
  const renderExecutorPicker = () =>
    renderSubjectPicker({
      candidates: agentMembers,
      emptyText: t("projects.noAgentMembers"),
      groups: [
        { subjectType: "team", title: t("projects.pickerGroupTeams") },
        { subjectType: "agent", title: t("projects.pickerGroupExperts") },
      ],
      isActive: (member) => {
        const ref = memberExecutorRef(member);
        return ref !== null && executors.includes(ref);
      },
      onToggle: (member) => {
        // PLAN §8.1: the dialog only writes the subject — dispatch stays an
        // explicit task-card action, because the task does not exist yet.
        const ref = memberExecutorRef(member);
        if (ref === null) return;
        const selected = executors.includes(ref);
        setExecutors((current) =>
          selected ? current.filter((item) => item !== ref) : [...current, ref],
        );
      },
    });

  const renderTagPicker = () => {
    if (tags.length === 0) {
      return <div className={styles.pickerEmpty}>{t("projects.noTags")}</div>;
    }
    return (
      <div className={styles.optionList}>
        {tags.map((tag) => {
          const active = tagIds.includes(tag.tag_id);
          return (
            <button
              key={tag.tag_id}
              type="button"
              className={`${styles.option} ${
                active ? styles.optionActive : ""
              }`}
              onClick={() =>
                setTagIds((current) =>
                  active
                    ? current.filter((id) => id !== tag.tag_id)
                    : [...current, tag.tag_id],
                )
              }
            >
              <Tag color={tag.color || undefined}>{tag.name}</Tag>
              {active ? <Check size={14} /> : null}
            </button>
          );
        })}
      </div>
    );
  };

  const renderCustomFieldInput = (definition: ProjectCustomFieldDefinition) => {
    const value = customValues[definition.field_id];
    const setValue = (next: ProjectCustomFieldWriteValue) =>
      setCustomValues((current) => ({
        ...current,
        [definition.field_id]: next,
      }));

    if (definition.type === "number") {
      return (
        <InputNumber
          className={styles.fieldControl}
          value={typeof value === "number" ? value : null}
          onChange={(next) => setValue(next ?? null)}
          aria-label={definition.label}
        />
      );
    }
    if (definition.type === "date") {
      const seconds = typeof value === "number" ? value : null;
      return (
        <DatePicker
          className={styles.fieldControl}
          showTime={{ format: "HH:mm" }}
          format="YYYY-MM-DD HH:mm"
          value={seconds ? dayjs(seconds * 1000) : null}
          onChange={(next) => setValue(next ? toEpochSeconds(next) : null)}
          aria-label={definition.label}
        />
      );
    }
    if (definition.type === "select") {
      return (
        <Select
          className={styles.fieldControl}
          allowClear
          value={typeof value === "string" && value ? value : undefined}
          placeholder={definition.label}
          aria-label={definition.label}
          onChange={(next) => setValue(next ?? null)}
          options={definition.options.map((option) => ({
            value: option,
            label: option,
          }))}
        />
      );
    }
    return (
      <Input
        className={styles.fieldControl}
        value={typeof value === "string" ? value : ""}
        placeholder={definition.label}
        aria-label={definition.label}
        onChange={(event) => setValue(event.target.value)}
      />
    );
  };

  const renderMorePanel = () => (
    <div className={styles.morePanel}>
      <div className={styles.moreRow}>
        <span className={styles.moreLabel}>{t("projects.chipDueAt")}</span>
        <DatePicker
          className={styles.fieldControl}
          showTime={{ format: "HH:mm" }}
          format="YYYY-MM-DD HH:mm"
          value={dueAt}
          onChange={(next) => setDueAt(next)}
          aria-label={t("projects.chipDueAt")}
        />
      </div>
      <div className={styles.moreRow}>
        <span className={styles.moreLabel}>{t("projects.chipStartAt")}</span>
        <DatePicker
          className={styles.fieldControl}
          showTime={{ format: "HH:mm" }}
          format="YYYY-MM-DD HH:mm"
          value={startAt}
          onChange={(next) => setStartAt(next)}
          aria-label={t("projects.chipStartAt")}
        />
      </div>
      <div className={styles.moreRow}>
        <span className={styles.moreLabel}>{t("projects.chipParentTask")}</span>
        <Select
          className={styles.fieldControl}
          allowClear
          value={parentId ?? undefined}
          placeholder={
            tasks.length === 0
              ? t("projects.noTasks")
              : t("projects.chipParentTask")
          }
          aria-label={t("projects.chipParentTask")}
          onChange={(next) => setParentId(next ?? null)}
          // PLAN §5.2 ④: the edited task and its descendants can never become
          // its parent — the same rule the server enforces with a 400.
          options={tasks
            .filter((task) => !forbiddenParentIds.has(task.task_id))
            .map((task) => ({
              value: task.task_id,
              label: task.title,
            }))}
        />
      </div>

      <div className={styles.moreRow}>
        <span className={styles.moreLabel}>{t("projects.chipSubtasks")}</span>
        {/* Reverse relation: other tasks point at this one, so nothing can be
            attached before the task exists (SPEC §边界 Case 父/子任务). */}
        <Text type="secondary" className={styles.moreHint}>
          {t("projects.noTasks")}
        </Text>
      </div>
      {definitions.length > 0 ? (
        <>
          <div className={styles.moreDivider} />
          <div className={styles.moreLabel}>
            {t("projects.chipCustomFields")}
          </div>
          {definitions.map((definition) => (
            <div key={definition.field_id} className={styles.moreRow}>
              <span className={styles.moreLabel}>
                {definition.label}
                {definition.required ? (
                  <span className={styles.requiredMark}>*</span>
                ) : null}
              </span>
              {renderCustomFieldInput(definition)}
            </div>
          ))}
        </>
      ) : null}
    </div>
  );

  return (
    <Modal
      open={open}
      onCancel={handleCancel}
      footer={null}
      width={760}
      destroyOnHidden
      className={styles.modal}
      title={
        <div className={styles.breadcrumb}>
          {projectName ? (
            <>
              <span className={styles.breadcrumbParent}>{projectName}</span>
              <span className={styles.breadcrumbSep} aria-hidden>
                ›
              </span>
            </>
          ) : null}
          <span className={styles.breadcrumbCurrent}>
            {t(
              editing
                ? "projects.editTaskTitle"
                : "projects.createTaskBreadcrumb",
            )}
          </span>
        </div>
      }
    >
      <div className={styles.shell} onKeyDown={handleContainerKeyDown}>
        <input
          className={styles.titleInput}
          value={title}
          maxLength={120}
          autoFocus
          placeholder={t("projects.createTaskTitle")}
          aria-label={t("projects.createTaskTitle")}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={handleTitleKeyDown}
        />

        <Input.TextArea
          className={styles.description}
          value={description}
          placeholder={t("projects.createTaskDescription")}
          aria-label={t("projects.createTaskDescription")}
          autoSize={{ minRows: 3, maxRows: 8 }}
          onChange={(event) => setDescription(event.target.value)}
        />

        <ChipToolbar>
          <ChipButton
            icon={<Flag size={14} />}
            label={t("projects.chipStatus")}
            value={statusSummary}
            active
            ariaLabel={t("projects.chipStatus")}
          >
            {renderStatusPicker()}
          </ChipButton>
          <ChipButton
            icon={<Flag size={14} />}
            label={t("projects.chipPriority")}
            value={String(priority)}
            ariaLabel={t("projects.chipPriority")}
          >
            {renderPriorityPicker()}
          </ChipButton>
          <ChipButton
            icon={<User size={14} />}
            label={t("projects.chipAssignee")}
            value={ownersSummary}
            ariaLabel={t("projects.chipAssignee")}
          >
            {renderOwnerPicker()}
          </ChipButton>
          {/*
            ★ 入口上移（PLAN §CD ①）：底部那枚「切换到智能体」chip 移入本行，与
            「分配」同属「谁来做」语义族故**相邻**；既有 5 枚 chip 的相对先后**逐位
            不变**。它复用同一个 `ChipButton`、同一组 props 形状（标签 / 值 /
            aria-label / popover children），且**不再**被 `footerButton` span 包住 ——
            那个 span 会作为 flex item 参与 `align-items` / `gap` 计算，是旧布局的
            唯一来源（UI-SPEC §3.2 实现禁令 1）。popover 不经 `disabled` 关掉：候选
            为空时 chip 仍可点开显示空态文案（UIS-X-08）。
          */}
          <ChipButton
            icon={<Bot size={14} />}
            label={t("projects.chipAgent")}
            value={executorsSummary}
            ariaLabel={t("projects.chipAgent")}
          >
            {renderExecutorPicker()}
          </ChipButton>
          <ChipButton
            icon={<TagIcon size={14} />}
            label={t("projects.chipTags")}
            value={tagSummary}
            ariaLabel={t("projects.chipTags")}
          >
            {renderTagPicker()}
          </ChipButton>
          <ChipButton
            icon={<Users size={14} />}
            label={t("projects.chipProject")}
            value={projectName || null}
            ariaLabel={t("projects.chipProject")}
          />
          <ChipButton
            icon={<MoreHorizontal size={14} />}
            label={t("projects.chipMore")}
            value={moreCount > 0 ? String(moreCount) : null}
            ariaLabel={t("projects.chipMore")}
          >
            {renderMorePanel()}
          </ChipButton>
        </ChipToolbar>

        {parentCycleBlocked ? (
          <div className={styles.cycleHint} role="alert">
            {t("apiErrors.PROJECT_TASK_PARENT_INVALID")}
          </div>
        ) : null}

        {editing ? null : staged.length > 0 ? (
          <div className={styles.attachments}>
            {staged.map((item) => (
              <span key={item.artifact_id} className={styles.attachment}>
                <Paperclip size={12} />
                <span className={styles.attachmentName}>{item.name}</span>
                <button
                  type="button"
                  className={styles.attachmentRemove}
                  aria-label={t("projects.attachmentDeleteConfirm")}
                  onClick={() => removeStaged(item)}
                >
                  <Trash2 size={12} />
                </button>
              </span>
            ))}
          </div>
        ) : (
          <div className={styles.attachmentsEmpty}>
            {t("projects.attachmentsEmpty")}
          </div>
        )}

        <div className={styles.footer}>
          <div className={styles.footerLeft}>
            {editing ? null : (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  className={styles.fileInput}
                  onChange={(event) => void handleFileChange(event)}
                />
                <Tooltip title={t("projects.attachFile")}>
                  <Button
                    type="text"
                    aria-label={t("projects.attachFile")}
                    loading={uploading}
                    icon={<Paperclip size={16} />}
                    onClick={() => fileInputRef.current?.click()}
                  />
                </Tooltip>
              </>
            )}
            {editing ? null : (
              <Checkbox
                className={styles.keepCreating}
                checked={keepCreating}
                onChange={(event) => setKeepCreating(event.target.checked)}
              >
                {t("projects.createAndContinue")}
              </Checkbox>
            )}
          </div>
          <div className={styles.footerRight}>
            <Tooltip title={t("common.cancel")}>
              <Button
                type="text"
                aria-label={t("common.cancel")}
                icon={<X size={16} />}
                onClick={handleCancel}
              />
            </Tooltip>
            <Button
              type="primary"
              loading={submitting}
              disabled={!canEdit || !title.trim()}
              onClick={() => void submit()}
            >
              <span className={styles.submitLabel}>
                {t(
                  editing
                    ? "projects.editTaskSubmit"
                    : "projects.createTaskSubmit",
                )}
                <span className={styles.shortcutHint}>⌘↵</span>
              </span>
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
