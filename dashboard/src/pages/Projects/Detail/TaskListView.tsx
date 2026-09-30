import { useMemo, useState } from "react";
import { Avatar, Input, Select, Table, Tag, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Bot, CircleHelp, Pencil, User, Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  projectsApi,
  type ProjectTask,
  type ProjectTaskStatus,
} from "../../../api/modules/projects";
import { useAsyncResource } from "../../../hooks/useAsyncResource";
import { useProjectMembers } from "../../../hooks/useProjectMembers";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import {
  COLUMN_STATUSES,
  STATUS_COLORS,
  STATUS_LABEL_KEYS,
} from "../utils/taskStatus";
import {
  isAgentLike,
  memberKey,
  priorityBars,
  priorityLabelKey,
  priorityTier,
  resolveAssigneeName,
  shortAssigneeId,
  tagsPreview,
} from "./Board";
import styles from "./TaskListView.module.less";

/**
 * Task list view — the `tasks` tab (PLAN.md §9 / §1 F1)。
 *
 * 平表改为**按状态分区**：7 个区块恒显（顺序 = `COLUMN_STATUSES`）、空区显示
 * `taskListEmpty`、筛选**只减少区内行**。分区行与任务行共用**同一张表**，因此
 * 列面仍冻结为 **8 列**（§5.3 机判 ①：`within(getByRole("table"))` 单表 + 8 表头）；
 * 行内编辑入口放进既有「标题」单元格，**不新增列**。
 *
 * 状态词表唯一来源 = `utils/taskStatus.ts`（本文件不得另立映射）。
 */

const DASH = "—";

/**
 * 列面契约（8 列，顺序即表头顺序）。测试用**字面量**独立断言，不从这里推导；
 * 此导出仅供「渲染列数 ↔ 列面」对照（§5.3 机判 ③ 的对照面）。
 */
export const TASK_LIST_COLUMN_KEYS = [
  "projects.taskListColumnStatus",
  "projects.taskListColumnTitle",
  "projects.taskListColumnAssignee",
  "projects.taskListColumnPriority",
  "projects.taskListColumnStartAt",
  "projects.taskListColumnDueAt",
  "projects.taskListColumnTags",
  "projects.taskListColumnUpdatedAt",
] as const;

export interface TaskListViewProps {
  projectId: string;
  /**
   * 任务详情入口。**本轮不渲染交互**（详情页属第四批）——契约保留、仍由父层
   * 传入，第四批接线时直接启用。
   */
  onOpenTask?: (taskId: string) => void;
  /** Bumped by the parent after any task write to trigger a re-read. */
  refreshKey?: number;
  /** 传入才渲染行内编辑入口（§5.3 G9 (a)：不新增列）。 */
  onEditTask?: (taskId: string) => void;
}

/**
 * S7 (PLAN §9): `sort_order` ascending, ties broken by `task_id` ascending.
 * Deliberately not `Array#sort` stability plus backend order — the comparator is
 * total, so the rendering order is deterministic for equal `sort_order` too.
 */
function bySortOrderThenTaskId(a: ProjectTask, b: ProjectTask): number {
  if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
  if (a.task_id === b.task_id) return 0;
  return a.task_id < b.task_id ? -1 : 1;
}

/**
 * Fallback glyph per assignee type (SPEC §3 `R-09`): `user`→`User` ·
 * `agent`→`Bot` · `team`→`Users` · anything else→`CircleHelp`. Same vocabulary
 * as the board card, same 4-icon set (`UI.md` @227 allows lucide-react only).
 */
function assigneeIcon(type: string | null | undefined) {
  if (type === "user") return User;
  if (type === "agent") return Bot;
  if (type === "team") return Users;
  return CircleHelp;
}

/**
 * Text of the priority cell (SPEC §3 `R-01`/`R-03`): the shared `priorityTier` /
 * `priorityLabelKey` decide the wording, an unknown value is shown verbatim and
 * only `null` / `undefined` become `—`. Never blank, never `String(undefined)`.
 */
function priorityText(t: (key: string) => string, priority: unknown): string {
  if (priority === null || priority === undefined) return DASH;
  const labelKey = priorityLabelKey(priorityTier(priority));
  return labelKey ? t(labelKey) : String(priority);
}

/** 一行 = 区块标题 / 空区提示 / 任务。三者共用同一张表的列面。 */
type ListRow =
  | { key: string; kind: "section"; status: ProjectTaskStatus; count: number }
  | { key: string; kind: "empty"; status: ProjectTaskStatus }
  | {
      key: string;
      kind: "task";
      status: ProjectTaskStatus;
      task: ProjectTask;
      /** 该区最后一行 —— 卡片底边与圆角画在这里（纯外观，不改语义/列面）。 */
      last: boolean;
    };

export default function TaskListView({
  projectId,
  onOpenTask,
  refreshKey,
  onEditTask,
}: TaskListViewProps): JSX.Element {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();

  /**
   * Display names for the assignee column — **one** member read per list
   * (`useProjectMembers` keys its effect on `projectId`), never one per row
   * (`SPEC §3` `R-11` / `B-12`). Failures stay silent: the hook only records an
   * error, so the column degrades to `type:#shortid` (`R-09` / `R-10`).
   */
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
  const [statuses, setStatuses] =
    useState<ProjectTaskStatus[]>(COLUMN_STATUSES);
  const [keyword, setKeyword] = useState("");

  const { data: tasks, loading } = useAsyncResource<ProjectTask[]>(
    [],
    () => projectsApi.listTasks(projectId),
    [projectId, refreshKey],
    {
      t,
      errorFallback: t("projects.loadFailed"),
      logLabel: "project-task-list",
    },
  );

  /**
   * 分区结构（PLAN §1.1/§1.2 冻结）：
   * - **7 区恒显**，顺序 = `COLUMN_STATUSES`；筛选只减少区内行，**不隐藏空区**；
   * - 空区插入一行空态（复用 `taskListEmpty`，不新增键）；
   * - 区内排序复用全序比较器 `(sort_order, task_id)`。
   */
  const rows = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    const matches = (task: ProjectTask) =>
      statuses.includes(task.status) &&
      (needle === "" ||
        `${task.title}\n${task.description}`.toLowerCase().includes(needle));

    const out: ListRow[] = [];
    for (const status of COLUMN_STATUSES) {
      const zone = tasks
        .filter((task) => task.status === status && matches(task))
        .sort(bySortOrderThenTaskId);
      out.push({
        key: `section-${status}`,
        kind: "section",
        status,
        count: zone.length,
      });
      if (zone.length === 0) {
        out.push({ key: `empty-${status}`, kind: "empty", status });
        continue;
      }
      zone.forEach((task, index) => {
        out.push({
          key: task.task_id,
          kind: "task",
          status,
          task,
          last: index === zone.length - 1,
        });
      });
    }
    return out;
  }, [tasks, statuses, keyword]);

  const isTaskRow = (row: ListRow): row is Extract<ListRow, { kind: "task" }> =>
    row.kind === "task";
  /** 区块行 / 空区行横跨 8 列；任务行正常分列。 */
  const spanAll = (row: ListRow) => (isTaskRow(row) ? {} : { colSpan: 8 });
  const spanNone = (row: ListRow) => (isTaskRow(row) ? {} : { colSpan: 0 });

  /**
   * 任务卡片元素（同源渲染 · `PLAN §2.3` / `AC-14`）。列表与看板共用 `Board`
   * 导出的同一批映射函数与同一批文案键；DOM 形态**不要求**同构（`SPEC §5`
   * 技术栈边界），但 testid 逐字相同（`FIND-11`：`task-card-chips` /
   * `task-card-chip-more` 两侧都落；编号位本轮**不渲染**（`B-08`）。
   */
  const renderPriority = (task: ProjectTask) => {
    const tier = priorityTier(task.priority);
    return (
      <span className={styles.cardPriority} data-testid="task-card-priority">
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
        {priorityText(t, task.priority)}
      </span>
    );
  };

  const renderTags = (task: ProjectTask) => {
    // `task.tags` is never null server-side, but an old cache may disagree (B-20).
    const tags = task.tags ?? [];
    if (tags.length === 0) return DASH;
    const { shown, more } = tagsPreview(tags);
    return (
      <span className={styles.cardChips} data-testid="task-card-chips">
        {shown.map((tag) => (
          <span key={tag.tag_id} className={styles.cardChip}>
            <span
              className={styles.cardChipDot}
              style={{ background: tag.color || undefined }}
            />
            {tag.name}
          </span>
        ))}
        {more > 0 ? (
          /* Tooltip 列出**全部**标签名（不只剩余项，R-04 / B-10）。 */
          <Tooltip title={tags.map((tag) => tag.name).join(", ")}>
            <span
              className={styles.cardChipMore}
              data-testid="task-card-chip-more"
            >
              {`+${more}`}
            </span>
          </Tooltip>
        ) : null}
      </span>
    );
  };

  const renderAssignee = (task: ProjectTask) => {
    // B-03：`assignee_type` 与 `assignee_id` **两者均非空**才算有负责人 ——
    // 单边为空按畸形数据处理，保留既有 `DASH` 口径（列表侧不同形、同源）。
    if (!task.assignee_type || !task.assignee_id) return DASH;
    const name = resolveAssigneeName(
      nameByKey,
      task.assignee_type,
      task.assignee_id,
    );
    const Icon = assigneeIcon(task.assignee_type);
    return (
      <span className={styles.assigneeSlot}>
        <Avatar
          size={28}
          className={styles.cardAvatar}
          data-testid="task-card-assignee-avatar"
          icon={name ? undefined : <Icon size={14} />}
        >
          {name ? name.charAt(0) : null}
        </Avatar>
        {isAgentLike(task.assignee_type) ? (
          <span
            className={styles.cardAgentBadge}
            data-testid="task-card-agent-badge"
          >
            <Icon size={14} />
          </span>
        ) : null}
        {/* 兜底 = `type:#末6位`（R-09）：绝不空白、绝不出现 `null:` / `undefined:`。 */}
        <span className={styles.assigneeName}>
          {name ?? `${task.assignee_type}:${shortAssigneeId(task.assignee_id)}`}
        </span>
      </span>
    );
  };

  const columns: ColumnsType<ListRow> = [
    {
      title: t("projects.taskListColumnStatus"),
      key: "status",
      width: 110,
      onCell: spanAll,
      render: (_, row) => {
        if (row.kind === "section") {
          return (
            <div
              className={styles.sectionHeader}
              /* 卡片锚点（AC-G1-1）：每区一张卡片；既有 testid 只增不改。 */
              data-testid={`task-section-card-${row.status}`}
            >
              <Tag color={STATUS_COLORS[row.status]}>
                <span
                  data-testid={`task-section-title-${row.status}`}
                  className={styles.sectionTitle}
                >
                  {t(STATUS_LABEL_KEYS[row.status])}
                </span>
              </Tag>
              <span
                data-testid={`task-section-count-${row.status}`}
                className={styles.sectionCount}
              >
                {row.count}
              </span>
            </div>
          );
        }
        if (row.kind === "empty") {
          return (
            <span
              data-testid={`task-section-empty-${row.status}`}
              className={styles.sectionEmpty}
            >
              {t("projects.taskListEmpty")}
            </span>
          );
        }
        return (
          <Tag color={STATUS_COLORS[row.status]}>
            {t(STATUS_LABEL_KEYS[row.status])}
          </Tag>
        );
      },
    },
    {
      title: t("projects.taskListColumnTitle"),
      key: "title",
      onCell: spanNone,
      render: (_, row) =>
        isTaskRow(row) ? (
          <span className={styles.titleCell}>
            {/* PLAN §4.1（本轮 G4 推翻 S8(b) 的「纯文本」一半）：标题 = **导航**入口，
                可点击 + 键盘可达（原生 button 自带 Enter/Space）+ aria-label 含标题；
                未传 `onOpenTask` 时退化为纯文本（不留假交互）。
                编辑仍是**独立图标按钮**（下方 `task-row-edit-*`），标题不是编辑入口。 */}
            {onOpenTask ? (
              <button
                type="button"
                className={styles.titleLink}
                data-testid={`task-row-title-${row.task.task_id}`}
                tabIndex={0}
                aria-label={`${t("projects.taskDetailTitle")}：${
                  row.task.title
                }`}
                onClick={() => onOpenTask(row.task.task_id)}
              >
                {row.task.title}
              </button>
            ) : (
              <span
                className={styles.titleText}
                data-testid={`task-row-title-${row.task.task_id}`}
              >
                {row.task.title}
              </span>
            )}
            {onEditTask ? (
              <Tooltip title={t("projects.editTaskTitle")}>
                <button
                  type="button"
                  className={styles.editButton}
                  data-testid={`task-row-edit-${row.task.task_id}`}
                  aria-label={t("projects.editTaskTitle")}
                  onClick={() => onEditTask(row.task.task_id)}
                >
                  <Pencil size={14} aria-hidden />
                </button>
              </Tooltip>
            ) : null}
          </span>
        ) : null,
    },
    {
      title: t("projects.taskListColumnAssignee"),
      key: "assignee",
      width: 170,
      onCell: spanNone,
      render: (_, row) => (isTaskRow(row) ? renderAssignee(row.task) : null),
    },
    {
      title: t("projects.taskListColumnPriority"),
      key: "priority",
      width: 90,
      onCell: spanNone,
      render: (_, row) => (isTaskRow(row) ? renderPriority(row.task) : null),
    },
    {
      title: t("projects.taskListColumnStartAt"),
      key: "start_at",
      width: 170,
      onCell: spanNone,
      render: (_, row) =>
        isTaskRow(row)
          ? formatServerDateTime(row.task.start_at ?? 0, timeZone)
          : null,
    },
    {
      title: t("projects.taskListColumnDueAt"),
      key: "due_at",
      width: 170,
      onCell: spanNone,
      render: (_, row) =>
        isTaskRow(row)
          ? formatServerDateTime(row.task.due_at ?? 0, timeZone)
          : null,
    },
    {
      title: t("projects.taskListColumnTags"),
      key: "tags",
      onCell: spanNone,
      render: (_, row) => (isTaskRow(row) ? renderTags(row.task) : null),
    },
    {
      title: t("projects.taskListColumnUpdatedAt"),
      key: "updated_at",
      width: 180,
      onCell: spanNone,
      render: (_, row) =>
        isTaskRow(row)
          ? formatServerDateTime(row.task.updated_at, timeZone)
          : null,
    },
  ];

  return (
    <div className={styles.wrapper} data-testid="project-task-list">
      <div className={styles.toolbar}>
        <span className={styles.title}>{t("projects.taskListTitle")}</span>
        <Select<ProjectTaskStatus[]>
          mode="multiple"
          className={styles.statusFilter}
          aria-label={t("projects.taskListFilterStatus")}
          value={statuses}
          onChange={setStatuses}
          maxTagCount="responsive"
          options={COLUMN_STATUSES.map((status) => ({
            value: status,
            label: t(STATUS_LABEL_KEYS[status]),
          }))}
        />
        <Input
          className={styles.keyword}
          allowClear
          value={keyword}
          placeholder={t("projects.taskListFilterKeyword")}
          aria-label={t("projects.taskListFilterKeyword")}
          onChange={(event) => setKeyword(event.target.value)}
        />
      </div>

      <Table<ListRow>
        rowKey="key"
        size="small"
        className={styles.table}
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 1020 }}
        onRow={(row) => {
          // 批次四 · 首个里程碑：7 个区块各自一张带边框的卡片（纯行内外观，
          // 列面 / testid / 语义一律不动）。
          if (row.kind === "section") {
            return {
              className: styles.zoneHeader,
              "data-testid": `task-section-${row.status}`,
              "data-section": row.status,
            } as React.HTMLAttributes<HTMLElement>;
          }
          if (row.kind === "empty") {
            return {
              className: styles.zoneEmptyRow,
              "data-testid": `task-section-empty-row-${row.status}`,
              "data-section": row.status,
            } as React.HTMLAttributes<HTMLElement>;
          }
          return {
            className: row.last
              ? `${styles.zoneRow} ${styles.zoneLast}`
              : styles.zoneRow,
            "data-testid": `task-row-${row.task.task_id}`,
            "data-section": row.status,
          } as React.HTMLAttributes<HTMLElement>;
        }}
      />
    </div>
  );
}
