import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import {
  Avatar,
  Button,
  Checkbox,
  Dropdown,
  Empty,
  Input,
  Modal,
  Select,
  Spin,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import type { MenuProps } from "antd";
import {
  Bot,
  CalendarDays,
  Check,
  CircleHelp,
  History,
  MoreHorizontal,
  Plus,
  Search,
  Send,
  User,
  Users,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import {
  projectsApi,
  type ProjectCustomFieldType,
  type ProjectCustomFieldWriteValue,
  type ProjectTask,
  type ProjectTaskStatus,
  type ProjectTimelineEvent,
} from "../../../api/modules/projects";
import { useProjectMembers } from "../../../hooks/useProjectMembers";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import { copyText } from "../../../utils/copyText";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import {
  asTaskStatus,
  COLUMN_STATUSES,
  isTransitionAllowed,
  STATUS_COLORS,
  STATUS_LABEL_KEYS,
  STATUS_TRANSITIONS,
  TERMINAL_STATUSES,
} from "../utils/taskStatus";
import TaskCreateModal from "./TaskCreateModal";
import styles from "./Board.module.less";

const { Text } = Typography;

/** Custom drag type: identifies a board card during a native HTML5 drag. */
const TASK_DRAG_TYPE = "application/x-octop-project-task";

/** Timeline actions recorded by the backend (`infra/db/repos/project_tasks.py`). */
const TIMELINE_ACTION_KEYS: Record<string, string> = {
  "task.created": "projects.boardActionCreated",
  "task.updated": "projects.boardActionUpdated",
  "task.status_changed": "projects.boardActionStatusChanged",
  "task.assigned": "projects.boardActionAssigned",
  "task.deleted": "projects.boardActionDeleted",
  "task.dispatched": "projects.boardActionDispatched",
};

/** Every column can be created into directly (R2; the old todo-only limit is gone). */
function canCreateInto(status: ProjectTaskStatus): boolean {
  return (COLUMN_STATUSES as string[]).includes(status);
}

interface BoardProps {
  projectId: string;
  tasks: ProjectTask[];
  /** Archived projects and viewers get a read-only board (no drag, no writes). */
  canEdit: boolean;
  /** Re-read the project tasks after a write so the board mirrors the server. */
  onChanged: () => void | Promise<void>;
  /** Optional: open the task detail. When absent the board navigates itself (§5.1). */
  onOpenTask?: (taskId: string) => void;
}

/** Unix seconds in, server-timezone text out (PLAN §0 invariant 3). */
function formatCustomFieldValue(
  value: ProjectCustomFieldWriteValue | undefined,
  type: ProjectCustomFieldType | undefined,
  timeZone: string,
): string {
  if (value === null || value === undefined || value === "") return "";
  if (type === "date") {
    const seconds = Number(value);
    return Number.isFinite(seconds) && seconds > 0
      ? formatServerDateTime(seconds, timeZone)
      : "";
  }
  return String(value);
}

/* ---------------------------------------------------------------------------
 * Shared card mapping helpers (PLAN §2.4).
 *
 * Exported on purpose: `TaskListView.tsx` renders the same card vocabulary
 * (priority bars, tag chips, single assignee slot) and imports these instead of
 * growing a second implementation (PLAN §1.2 · D4 / AC-14). Pure functions,
 * no React dependency.
 * ------------------------------------------------------------------------- */

/** Coarse priority bucket; the backend accepts any int, so `unknown` is normal. */
export type PriorityTier = "low" | "mid" | "high" | "unknown";

/**
 * 优先级档位（暂定 · 待 SPEC 定档：DECISIONS D2 / SPEC R-01）。
 *
 * `unknown` is a first-class result — the API does not validate `priority`, so
 * non-integer / non-finite / null / non-numeric input reaches the card as-is
 * and must be shown verbatim (`PLAN.md` §4.1). Never throws.
 * Integers `<= 1` (including `-1` / `0`) are *low*, not unknown.
 */
export function priorityTier(p: unknown): PriorityTier {
  if (typeof p !== "number" || !Number.isInteger(p)) return "unknown";
  if (p <= 1) return "low";
  if (p === 2) return "mid";
  return "high";
}

/** Lit cells for the 3-bar priority icon; always length 3 (PLAN §4.1). */
export function priorityBars(tier: PriorityTier): boolean[] {
  switch (tier) {
    case "low":
      return [true, false, false];
    case "mid":
      return [true, true, false];
    case "high":
      return [true, true, true];
    default:
      return [false, false, false];
  }
}

/** i18n key for a tier; `unknown` returns `null` ⇒ show the raw value. */
export function priorityLabelKey(tier: PriorityTier): string | null {
  switch (tier) {
    case "low":
      return "common.cardPriorityLow";
    case "mid":
      return "common.cardPriorityMid";
    case "high":
      return "common.cardPriorityHigh";
    default:
      return null;
  }
}

/** Tag chips: at most `max` shown, the rest folded into a `+N` counter. */
export function tagsPreview<T>(
  tags: T[] | null | undefined,
  max = 3,
): { shown: T[]; more: number } {
  const list = tags ?? [];
  return { shown: list.slice(0, max), more: Math.max(list.length - max, 0) };
}

/** Last 6 characters of an assignee id, `#`-prefixed (shorter ids stay whole). */
export function shortAssigneeId(id: string | null | undefined): string {
  const raw = id === null || id === undefined ? "" : String(id);
  return `#${raw.length > 6 ? raw.slice(-6) : raw}`;
}

/**
 * Join key for the member lookup. Both sides must be stringified: the member
 * row stores `user_id` as a number while `assignee_id` is a string (§3.2).
 */
export function memberKey(
  type: string | null | undefined,
  id: string | null | undefined,
): string {
  return `${String(type)}:${String(id)}`;
}

/** Resolved display name for an assignee, or `null` when it cannot be resolved. */
export function resolveAssigneeName(
  map: Map<string, string>,
  type: string | null | undefined,
  id: string | null | undefined,
): string | null {
  const name = map.get(memberKey(type, id));
  if (name === undefined) return null;
  const trimmed = name.trim();
  return trimmed === "" ? null : trimmed;
}

/** Only an agent / team assignee has a runtime (case-sensitive, §3.1). */
export function isAgentLike(type: string | null | undefined): boolean {
  return type === "agent" || type === "team";
}

/**
 * Accessible name of the title button. The task number does not exist yet, so
 * today this is the title verbatim — never a `·`-prefixed or `task_id` variant.
 */
export function cardAriaLabel(
  title: string,
  taskNumber?: string | null,
): string {
  return taskNumber ? `${taskNumber} · ${title}` : title;
}

/** Fallback glyph per assignee type (R-09): unknown values get a neutral icon. */
function assigneeIcon(type: string | null | undefined) {
  if (type === "user") return User;
  if (type === "agent") return Bot;
  if (type === "team") return Users;
  return CircleHelp;
}

function Board({
  projectId,
  tasks,
  canEdit,
  onChanged,
  onOpenTask,
}: BoardProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [searchParams, setSearchParams] = useSearchParams();

  /** One member read per board — never per card (AC-13 / R-11). */
  const { members } = useProjectMembers(projectId);
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

  const [titleFilter, setTitleFilter] = useState("");
  // Terminal columns start visible: hiding them is an explicit choice, so a
  // card dragged into Done / Cancelled never disappears on its own.
  const [showTerminal, setShowTerminal] = useState(true);
  const [dragTaskId, setDragTaskId] = useState<string | null>(null);
  const [overStatus, setOverStatus] = useState<ProjectTaskStatus | null>(null);
  const [pendingTaskId, setPendingTaskId] = useState<string | null>(null);
  /** Task whose explicit dispatch action is in flight (PLAN §8.1-2). */
  const [dispatchingTaskId, setDispatchingTaskId] = useState<string | null>(
    null,
  );
  const [addingStatus, setAddingStatus] = useState<ProjectTaskStatus | null>(
    null,
  );
  const [newTitle, setNewTitle] = useState("");
  const [creating, setCreating] = useState(false);
  /** Full create dialog (R3); the column "+" keeps the inline quick path (R5). */
  const [createOpen, setCreateOpen] = useState(false);
  /**
   * Column whose header "+" opened the dialog: its status is prefilled through
   * the dialog's existing ``initialValues`` (PLAN §3 — no prop change there).
   * ``null`` = opened from the toolbar, i.e. the dialog's own default status.
   */
  const [createStatus, setCreateStatus] = useState<ProjectTaskStatus | null>(
    null,
  );
  const [timelineTask, setTimelineTask] = useState<ProjectTask | null>(null);
  const [timelineEvents, setTimelineEvents] = useState<ProjectTimelineEvent[]>(
    [],
  );
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<unknown>(null);

  const timelineTaskId = timelineTask?.task_id ?? null;

  /**
   * Timestamp of the last finished drag. HTML5 `dragend` may or may not be
   * followed by a `click` depending on the browser, so the title button only
   * swallows the click that lands right after a drag (PLAN §5.4 · B-29); a
   * boolean flag would already be reset by the time that click arrives.
   */
  const lastDragEndAtRef = useRef(0);

  /** 打开详情 (§5.1): prop first, otherwise the board's own URL navigation. */
  // TODO(L1)：待 index.tsx 稳定后统一为 prop 传递，删除本内建导航
  const openDetail = useCallback(
    (taskId: string) => {
      if (onOpenTask) {
        onOpenTask(taskId);
        return;
      }
      const updated = new URLSearchParams(searchParams);
      updated.set("tab", "tasks");
      updated.set("task", taskId);
      setSearchParams(updated, { replace: false });
    },
    [onOpenTask, searchParams, setSearchParams],
  );

  /** Copy link (§5.3): `copyText` is the only clipboard entry point. */
  const copyTaskLink = async (task: ProjectTask) => {
    const url = `${window.location.origin}${
      window.location.pathname
    }?task=${encodeURIComponent(task.task_id)}&tab=tasks`;
    let ok = false;
    try {
      ok = await copyText(url);
    } catch {
      // Failures are reported through the toast only — never rethrown.
      ok = false;
    }
    if (ok) message.success(t("common.copied"));
    else message.error(t("common.copyFailed"));
  };

  /** Fixed 2-item menu (R-05): no write action, no "copy number". */
  const cardMenuItems: MenuProps["items"] = [
    { key: "open", label: t("common.cardMenuOpen") },
    { key: "copy-link", label: t("common.cardMenuCopyLink") },
  ];

  useEffect(() => {
    if (!timelineTaskId) return;
    let cancelled = false;
    setTimelineLoading(true);
    setTimelineError(null);
    // No `limit`: the API pages from the oldest event, so a bounded read could
    // hide a task's recent history.
    projectsApi
      .timeline(projectId)
      .then((events) => {
        if (cancelled) return;
        setTimelineEvents(
          events.filter((event) => event.task_id === timelineTaskId),
        );
      })
      .catch((error: unknown) => {
        if (!cancelled) setTimelineError(error);
      })
      .finally(() => {
        if (!cancelled) setTimelineLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, timelineTaskId]);

  const filteredTasks = useMemo(() => {
    const query = titleFilter.trim().toLowerCase();
    if (!query) return tasks;
    return tasks.filter((task) => task.title.toLowerCase().includes(query));
  }, [tasks, titleFilter]);

  const tasksByStatus = useMemo(() => {
    const grouped = new Map<ProjectTaskStatus, ProjectTask[]>();
    for (const status of COLUMN_STATUSES) grouped.set(status, []);
    // The API already returns `sort_order, created_at, id` order.
    for (const task of filteredTasks) grouped.get(task.status)?.push(task);
    return grouped;
  }, [filteredTasks]);

  const visibleColumns = COLUMN_STATUSES.filter(
    (status) => showTerminal || !TERMINAL_STATUSES.includes(status),
  );

  const draggedTask = dragTaskId
    ? tasks.find((task) => task.task_id === dragTaskId) ?? null
    : null;

  const canDropInto = (status: ProjectTaskStatus): boolean =>
    canEdit &&
    draggedTask !== null &&
    draggedTask.status !== status &&
    isTransitionAllowed(draggedTask.status, status);

  const endDrag = () => {
    setDragTaskId(null);
    setOverStatus(null);
    // Only *appends* a timestamp — the two resets above stay as they are
    // (they carry the existing drag feedback, PLAN §4.5 · W29).
    lastDragEndAtRef.current = Date.now();
  };

  const moveTask = async (task: ProjectTask, status: ProjectTaskStatus) => {
    if (!canEdit || task.status === status) return;
    if (!isTransitionAllowed(task.status, status)) {
      message.warning(t("projects.boardDropInvalid"));
      return;
    }
    setPendingTaskId(task.task_id);
    try {
      await projectsApi.updateTask(projectId, task.task_id, { status });
    } catch (error) {
      message.error(apiErrorMessage(error, t("projects.boardMoveFailed"), t));
    } finally {
      // Re-read either way: the board only ever shows the server's state.
      await onChanged();
      setPendingTaskId(null);
    }
  };

  /**
   * Explicit dispatch action (PLAN §8.1-2). The create dialog only writes
   * `assignee`; this is where the task is actually handed to the agent/team.
   */
  const dispatchTaskToAssignee = async (task: ProjectTask) => {
    if (!canEdit || dispatchingTaskId) return;
    setDispatchingTaskId(task.task_id);
    try {
      await projectsApi.dispatchTask(projectId, task.task_id);
      message.success(t("projects.boardActionDispatched"));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("apiErrors.PROJECT_TASK_DISPATCH_INVALID"), t),
      );
    } finally {
      setDispatchingTaskId(null);
      // Re-read either way: dispatch changes `thread_id` server-side.
      await onChanged();
    }
  };

  const handleCardDragStart = (
    event: DragEvent<HTMLDivElement>,
    task: ProjectTask,
  ) => {
    if (!canEdit || pendingTaskId === task.task_id) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData(TASK_DRAG_TYPE, task.task_id);
    event.dataTransfer.effectAllowed = "move";
    setDragTaskId(task.task_id);
  };

  const handleColumnDragOver = (
    event: DragEvent<HTMLDivElement>,
    status: ProjectTaskStatus,
  ) => {
    if (!draggedTask) return;
    if (![...event.dataTransfer.types].includes(TASK_DRAG_TYPE)) return;
    event.preventDefault();
    event.stopPropagation();
    const allowed = canDropInto(status);
    event.dataTransfer.dropEffect = allowed ? "move" : "none";
    // Kept for legal *and* illegal columns: the column styling decides which
    // feedback to show, so an illegal drop target is visibly rejected.
    setOverStatus(status);
  };

  const handleColumnDragLeave = (
    event: DragEvent<HTMLDivElement>,
    status: ProjectTaskStatus,
  ) => {
    // `dragleave` also bubbles out of the child cards; ignore those.
    const next = event.relatedTarget as Node | null;
    if (next && event.currentTarget.contains(next)) return;
    setOverStatus((current) => (current === status ? null : current));
  };

  const handleColumnDrop = (
    event: DragEvent<HTMLDivElement>,
    status: ProjectTaskStatus,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const task = draggedTask;
    endDrag();
    if (!task || task.status === status) return;
    if (!isTransitionAllowed(task.status, status)) {
      message.warning(t("projects.boardDropInvalid"));
      return;
    }
    void moveTask(task, status);
  };

  const cancelAdd = () => {
    setAddingStatus(null);
    setNewTitle("");
  };

  const submitNewTask = async (status: ProjectTaskStatus) => {
    const title = newTitle.trim();
    if (!title || creating || !canEdit) return;
    setCreating(true);
    try {
      // One request carrying the target column (R2): the old
      // "create as todo, then move" two-step is gone.
      await projectsApi.createTask(projectId, { title, status });
      message.success(t("projects.boardCreated"));
      setNewTitle("");
      setAddingStatus(null);
    } catch (error) {
      message.error(apiErrorMessage(error, t("projects.boardCreateFailed"), t));
    } finally {
      setCreating(false);
      await onChanged();
    }
  };

  const openTimeline = (task: ProjectTask) => {
    setTimelineEvents([]);
    setTimelineTask(task);
  };

  const timelineItems = [...timelineEvents].reverse().map((event, index) => {
    const actionKey = TIMELINE_ACTION_KEYS[event.action];
    const from = asTaskStatus(event.payload.from);
    const to = asTaskStatus(event.payload.to);
    return {
      key: `${event.at}-${index}`,
      children: (
        <div className={styles.timelineItem}>
          <Text strong>{actionKey ? t(actionKey) : event.action}</Text>
          {from && to ? (
            <Text type="secondary" className={styles.timelineDetail}>
              {`${t(STATUS_LABEL_KEYS[from])} → ${t(STATUS_LABEL_KEYS[to])}`}
            </Text>
          ) : null}
          <Text type="secondary" className={styles.timelineMeta}>
            {`${event.actor} · ${formatServerDateTime(event.at, timeZone)}`}
          </Text>
        </div>
      ),
    };
  });

  const renderCard = (task: ProjectTask) => {
    const pending = pendingTaskId === task.task_id;
    const dragging = dragTaskId === task.task_id;
    // Old caches may predate the never-null guarantee (B-20).
    const tags = task.tags ?? [];
    const { shown: shownTags, more: moreTags } = tagsPreview(tags);
    const tier = priorityTier(task.priority);
    const tierLabelKey = priorityLabelKey(tier);
    // PLAN §4.1 / R-03: an out-of-range value is shown verbatim (never blank,
    // never silently downgraded to "low"); only null / undefined become "—".
    const priorityText =
      task.priority === null || task.priority === undefined
        ? "—"
        : tierLabelKey
        ? t(tierLabelKey)
        : String(task.priority);
    // Moved verbatim out of the deleted `CardMetadata` (custom fields, row ④).
    const fields = (task.custom_fields ?? []).filter(
      (field) =>
        formatCustomFieldValue(field.value, field.type, timeZone) !== "",
    );
    const assigneeName = resolveAssigneeName(
      nameByKey,
      task.assignee_type,
      task.assignee_id,
    );
    // B-03: both sides must be non-empty — either side empty ⇒ no block at all.
    const hasAssignee =
      Boolean(task.assignee_type) && Boolean(task.assignee_id);
    const AssigneeIcon = assigneeIcon(task.assignee_type);
    // Silent fallback (R-09 / R-10): type glyph + `type:#shortid`, never blank
    // and never a literal `null:` / `undefined:` prefix.
    const assigneeFallback = `${
      task.assignee_type ? `${task.assignee_type}:` : ""
    }${shortAssigneeId(task.assignee_id)}`;
    // PLAN §8.1-3: only an agent / team assignee has a runtime to dispatch to.
    const canDispatch =
      task.assignee_type === "agent" || task.assignee_type === "team";
    const dispatchTitle = canDispatch
      ? t("projects.boardDispatch")
      : t("apiErrors.PROJECT_TASK_DISPATCH_INVALID");
    const cardClass = [
      styles.card,
      dragging ? styles.cardDragging : "",
      pending ? styles.cardPending : "",
    ]
      .filter(Boolean)
      .join(" ");
    const handleTitleClick = () => {
      // Swallow the click a drag may leave behind (§5.4 · B-29).
      if (Date.now() - lastDragEndAtRef.current < 200) return;
      openDetail(task.task_id);
    };
    const handleMenuClick: MenuProps["onClick"] = ({ key, domEvent }) => {
      domEvent.stopPropagation();
      if (key === "open") openDetail(task.task_id);
      else if (key === "copy-link") void copyTaskLink(task);
    };
    return (
      <div
        key={task.task_id}
        className={cardClass}
        role="group"
        draggable={canEdit && !pending}
        onDragStart={(event) => handleCardDragStart(event, task)}
        onDragEnd={endDrag}
      >
        {/* 行① 编号位：本轮唯一子节点 = ⋯（编号批次把编号 span 插到本行首）。 */}
        <div className={styles.cardKey}>
          <Dropdown
            menu={{ items: cardMenuItems, onClick: handleMenuClick }}
            trigger={["click"]}
            placement="bottomRight"
          >
            <button
              type="button"
              className={styles.cardMenu}
              data-testid="task-card-menu"
              aria-label={t("common.more")}
              draggable={false}
              onClick={(event) => event.stopPropagation()}
              onDragStart={(event) => {
                // The card itself stays draggable; only this trigger is not.
                event.preventDefault();
                event.stopPropagation();
              }}
            >
              <MoreHorizontal size={14} />
            </button>
          </Dropdown>
        </div>
        {/* 行② 标题（原生 button ⇒ Enter / Space 走原生语义）。 */}
        <div className={styles.cardTitleRow}>
          <button
            type="button"
            className={styles.cardTitleButton}
            aria-label={cardAriaLabel(task.title)}
            onClick={handleTitleClick}
          >
            {task.title}
          </button>
        </div>
        {/* 行③ 标签：0 个整块不渲染；>3 个折成 +N（R-04）。 */}
        {tags.length > 0 ? (
          <div className={styles.cardChips} data-testid="task-card-chips">
            {shownTags.map((tag) => (
              <span key={tag.tag_id} className={styles.cardChip}>
                <span
                  className={styles.cardChipDot}
                  style={{ background: tag.color || undefined }}
                />
                {tag.name}
              </span>
            ))}
            {moreTags > 0 ? (
              <Tooltip title={tags.map((tag) => tag.name).join(", ")}>
                <span
                  className={styles.cardChipMore}
                  data-testid="task-card-chip-more"
                >
                  {`+${moreTags}`}
                </span>
              </Tooltip>
            ) : null}
          </div>
        ) : null}
        {/* 行④ 左：优先级柱 + 文案；中：日期与自定义字段；右：单主体位。 */}
        <div className={styles.cardBottomRow}>
          <span
            className={styles.cardPriority}
            data-testid="task-card-priority"
          >
            <span className={styles.cardPriorityBars} data-priority-bars>
              {priorityBars(tier).map((on, index) => (
                <span
                  key={index}
                  className={
                    on
                      ? `${styles.cardPriorityBar} ${styles.isOn}`
                      : styles.cardPriorityBar
                  }
                />
              ))}
            </span>
            {priorityText}
          </span>
          {task.start_at ? (
            <span className={styles.cardMetaItem}>
              <CalendarDays size={12} />
              <Text className={styles.cardMetaText}>
                {formatServerDateTime(task.start_at, timeZone)}
              </Text>
            </span>
          ) : null}
          {task.due_at ? (
            <span className={styles.cardMetaItem}>
              <CalendarDays size={12} />
              <Text className={styles.cardMetaText}>
                {formatServerDateTime(task.due_at, timeZone)}
              </Text>
            </span>
          ) : null}
          {fields.map((field) => (
            <span key={field.field_id} className={styles.cardMetaItem}>
              <Text className={styles.cardMetaText}>
                {`${field.label}：${formatCustomFieldValue(
                  field.value,
                  field.type,
                  timeZone,
                )}`}
              </Text>
            </span>
          ))}
          {hasAssignee ? (
            <span className={styles.cardOwnerAgent}>
              <Avatar
                size={28}
                className={styles.cardAvatar}
                data-testid="task-card-assignee-avatar"
                icon={assigneeName ? undefined : <AssigneeIcon size={14} />}
              >
                {assigneeName ? assigneeName.charAt(0) : null}
              </Avatar>
              {isAgentLike(task.assignee_type) ? (
                <span
                  className={styles.cardAgentBadge}
                  data-testid="task-card-agent-badge"
                >
                  <AssigneeIcon size={14} />
                </span>
              ) : null}
              <span className={styles.cardAgentName}>
                {assigneeName ?? assigneeFallback}
              </span>
            </span>
          ) : null}
        </div>
        <div className={styles.cardFooter}>
          {canEdit ? (
            <Select<ProjectTaskStatus>
              size="small"
              className={styles.cardStatusSelect}
              value={task.status}
              loading={pending}
              aria-label={t("projects.taskStatus")}
              onChange={(status) => void moveTask(task, status)}
              options={[task.status, ...STATUS_TRANSITIONS[task.status]].map(
                (status) => ({
                  value: status,
                  label: t(STATUS_LABEL_KEYS[status]),
                }),
              )}
            />
          ) : (
            <Tag color={STATUS_COLORS[task.status]}>
              {t(STATUS_LABEL_KEYS[task.status])}
            </Tag>
          )}
          <div className={styles.cardActions}>
            {canEdit ? (
              <Tooltip title={dispatchTitle}>
                <Button
                  type="text"
                  size="small"
                  className={styles.cardDispatch}
                  aria-label={t("projects.boardDispatch")}
                  title={dispatchTitle}
                  disabled={!canDispatch || pending}
                  loading={dispatchingTaskId === task.task_id}
                  icon={<Send size={14} />}
                  onClick={() => void dispatchTaskToAssignee(task)}
                />
              </Tooltip>
            ) : null}
            <Tooltip title={t("projects.boardTimeline")}>
              <Button
                type="text"
                size="small"
                aria-label={t("projects.boardTimeline")}
                icon={<History size={14} />}
                onClick={() => openTimeline(task)}
              />
            </Tooltip>
          </div>
        </div>
      </div>
    );
  };

  const renderColumn = (status: ProjectTaskStatus) => {
    const columnTasks = tasksByStatus.get(status) ?? [];
    // The column the dragged card came from is neither locked nor rejected.
    const isOrigin = draggedTask?.status === status;
    const droppable = canDropInto(status);
    const columnClass = [
      styles.column,
      draggedTask && !droppable && !isOrigin ? styles.columnLocked : "",
      overStatus === status && !isOrigin
        ? droppable
          ? styles.columnOver
          : styles.columnReject
        : "",
    ]
      .filter(Boolean)
      .join(" ");
    return (
      <div
        key={status}
        className={columnClass}
        onDragOver={(event) => handleColumnDragOver(event, status)}
        onDragLeave={(event) => handleColumnDragLeave(event, status)}
        onDrop={(event) => handleColumnDrop(event, status)}
      >
        <div className={styles.columnHeader}>
          <Tag color={STATUS_COLORS[status]}>
            {t(STATUS_LABEL_KEYS[status])}
          </Tag>
          <span className={styles.columnCount}>{columnTasks.length}</span>
          {canEdit ? (
            /* Second entry point (PLAN §3): the full dialog, prefilled with this
               column's status. The inline quick create below stays untouched —
               the two coexist on purpose (R5).
               The accessible name repeats the column so the seven identical
               "+ Add task" buttons stay distinguishable for screen readers. */
            <Button
              type="text"
              size="small"
              style={{ marginInlineStart: "auto" }}
              icon={<Plus size={14} />}
              aria-label={`${t("projects.boardAdd")} · ${t(
                STATUS_LABEL_KEYS[status],
              )}`}
              data-testid={`board-column-add-${status}`}
              onClick={() => {
                setCreateStatus(status);
                setCreateOpen(true);
              }}
            >
              {t("projects.boardAdd")}
            </Button>
          ) : null}
        </div>
        <div className={styles.columnBody}>
          {columnTasks.length === 0 ? (
            <div className={styles.columnEmpty}>{t("projects.tasksEmpty")}</div>
          ) : (
            columnTasks.map(renderCard)
          )}
        </div>
        {canEdit ? (
          addingStatus === status ? (
            <div className={styles.addForm}>
              <Input
                size="small"
                autoFocus
                maxLength={120}
                value={newTitle}
                disabled={creating}
                placeholder={t("projects.boardAddPlaceholder")}
                aria-label={t("projects.boardAddPlaceholder")}
                onChange={(event) => setNewTitle(event.target.value)}
                onPressEnter={() => void submitNewTask(status)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") cancelAdd();
                }}
              />
              <Button
                type="text"
                size="small"
                aria-label={t("common.save")}
                disabled={!newTitle.trim()}
                loading={creating}
                icon={<Check size={14} />}
                onClick={() => void submitNewTask(status)}
              />
              <Button
                type="text"
                size="small"
                aria-label={t("common.cancel")}
                icon={<X size={14} />}
                onClick={cancelAdd}
              />
            </div>
          ) : (
            <Button
              type="text"
              size="small"
              block
              className={styles.addButton}
              icon={<Plus size={14} />}
              disabled={!canCreateInto(status)}
              onClick={() => {
                setNewTitle("");
                setAddingStatus(status);
              }}
            >
              {t("projects.boardAdd")}
            </Button>
          )
        ) : null}
      </div>
    );
  };

  return (
    <div className={styles.board}>
      <div className={styles.toolbar}>
        {canEdit ? (
          <Button
            type="primary"
            size="small"
            icon={<Plus size={14} />}
            onClick={() => {
              // Toolbar entry keeps the dialog's own default status: without
              // this reset it would inherit the last column that was used.
              setCreateStatus(null);
              setCreateOpen(true);
            }}
          >
            {t("projects.createTaskSubmit")}
          </Button>
        ) : null}
        <Input
          size="small"
          allowClear
          className={styles.search}
          value={titleFilter}
          prefix={<Search size={14} />}
          placeholder={t("projects.boardSearchPlaceholder")}
          aria-label={t("projects.boardSearchPlaceholder")}
          onChange={(event) => setTitleFilter(event.target.value)}
        />
        <Checkbox
          checked={showTerminal}
          onChange={(event) => setShowTerminal(event.target.checked)}
        >
          {t("projects.boardShowTerminal")}
        </Checkbox>
        {!canEdit ? (
          <Text type="secondary" className={styles.readOnly}>
            {t("projects.boardReadOnly")}
          </Text>
        ) : null}
      </div>
      <div className={styles.columns}>{visibleColumns.map(renderColumn)}</div>
      <TaskCreateModal
        open={createOpen}
        projectId={projectId}
        tasks={tasks}
        canEdit={canEdit}
        /* Only the column entry prefills; ``initialValues`` is read once per
           open by the dialog, so the status is already correct on the first
           render after the click. */
        initialValues={createStatus ? { status: createStatus } : undefined}
        onClose={() => setCreateOpen(false)}
        onCreated={onChanged}
      />
      <Modal
        open={timelineTask !== null}
        title={t("projects.boardTimelineTitle", {
          title: timelineTask?.title ?? "",
        })}
        footer={null}
        destroyOnHidden
        onCancel={() => setTimelineTask(null)}
      >
        {timelineLoading ? (
          <div className={styles.timelineCentered}>
            <Spin />
          </div>
        ) : timelineError ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={apiErrorMessage(
              timelineError,
              t("projects.boardTimelineLoadFailed"),
              t,
            )}
          />
        ) : timelineItems.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t("projects.boardTimelineEmpty")}
          />
        ) : (
          <Timeline items={timelineItems} />
        )}
      </Modal>
    </div>
  );
}

export default Board;
