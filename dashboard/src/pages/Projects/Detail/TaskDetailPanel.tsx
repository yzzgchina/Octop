import { useMemo, useState, type ReactNode } from "react";
import { Avatar, Button, Descriptions, Space, Spin, Tag, Tooltip } from "antd";
import {
  ArrowLeft,
  ChevronDown,
  Download,
  MessageSquarePlus,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Plus,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  type ProjectAttachment,
  type ProjectComment,
  type ProjectMember,
  type ProjectTask,
  type ProjectTimelineEvent,
  projectsApi,
} from "../../../api/modules/projects";
import { projectMetadataApi } from "../../../api/modules/projectMetadata";
import { useAsyncResource } from "../../../hooks/useAsyncResource";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import {
  calendarDaysAgo,
  formatServerDateTime,
} from "../../../utils/formatMessageTime";
import { STATUS_COLORS, STATUS_LABEL_KEYS } from "../utils/taskStatus";
import {
  type TaskSubjectRef,
  SubjectAvatar,
  SubjectBadge,
  memberKey,
  resolveAssigneeName,
  subjectDisplayName,
  taskNumberText,
  taskSubjects,
} from "./Board";
import CommentFeed from "./CommentFeed";
import TaskCreateModal, { type TaskPatchValues } from "./TaskCreateModal";
import { taskToValues } from "./taskCreateValues";
import styles from "./TaskDetailPanel.module.less";

/**
 * 任务详情面板（PLAN §4 / G4 + `C3-DETAIL` 五段）。
 *
 * 数据来源 = **路径 ①**（`PLAN §4.2`）：**只用已加载的列表数据**（`task` prop），
 * **不新增单任务 GET**。未命中（`task === undefined` 且非 loading）→ `taskDetailNotFound`
 * 优雅降级，绝不白屏。
 *
 * 编辑 = 复用批次三的 `TaskCreateModal(mode="edit")`（**同一份字段定义 + 差异提交**），
 * 回填走 **唯一** 适配器 `taskCreateValues.ts`（`P2-5` 收敛：此前本文件内还有**第二份**
 * 同名私有 `taskToValues()`），保存成功后 `onChanged()` 重读（非本地乐观）。
 *
 * ★★ `C3-DETAIL`（五段 · 决策 ⑥「按参考图全量对齐」· 参考图 = `reference/detail-reference.webp`）：
 *   ① 标题区：编号（`taskNumberText`，`P2-4` 的冻结形态判定）+ **讨论按钮外观**（`U-3` ⓐ：
 *      「讨论按钮**外观可做**、功能不做」）；
 *   ② meta 行：状态 chip + **双主体两槽**（人 / 执行主体 —— 见下）+ 项目 chip + `⋯` 外观；
 *   ③ 正文分节：**沿用既有分节**（`SPEC.md · G5` 明文「正文分节的**具体节名与顺序**……
 *      `ui` 定稿后**冻结**；本 SPEC 不编造」⇒ 本卡**不重排、不发明**节名）；
 *   ④ **「动态」时间线**（灰底托盘）：事件行 = 头像 + 谁 + 动作 + **相对时间**，且**搬移
 *      项目级留言 feed**（共用 `CommentFeed` ⇒ 同一个 feed 只有**一份**实现）；
 *   ⑤ 底部操作条五项（`AC-24`）：`+ 成员` · `+ Agent` · `项目▾` · `Issue▾` · `附件`。
 *
 * ★★ 双主体两槽 = **只消费** `Board.tsx` 的展示模型 `taskSubjects()`（`P2-4` 的
 *   「唯一展示模型」）与 `SubjectAvatar` / `SubjectBadge` / `subjectDisplayName` /
 *   `SUBJECT_GROUP_LIMIT` ⇒ 与**卡片行④同源**（`PLAN §CD · C3`
 *   逐字：「详情 meta 行的双主体位与卡片行④走**同一个展示函数**（前 2 + `+N`），**禁止**
 *   详情另写一份」），本文件**没有**第二份「前 2 + `+N`」实现。
 *   ★ `SubjectArrow`（`→`）**未复用**：它是卡片/列表**单行**行④的连接符（`UIS-R-07`），
 *   而详情是**两个各自带标签的 meta 槽**（`UI-SPEC §8.4` 的双槽语义）⇒ 形态不同，无位可放。
 *   ★ 与 `UI-SPEC.md §8.4`（槽内多值用 `、` 连接**机器串** `type:id`）的取舍、理由与
 *   一行回退路径登记在 `team/2026-10-08-110000/EVIDENCE-P2-5.md` §1.3。
 *   ★ 镜像兜底（`owners` / `executors` 皆空的旧响应）按**类型归位**：`user` ⇒ 人槽，
 *   其余 ⇒ 执行主体槽（两数组非空时**一律以数组为准**，不看镜像）。
 *
 * ★ 「过程」区 = 既有只读轨迹（⚠️ 诚实边界：**不是**完整状态流转历史）；`C3-DETAIL`
 *   的 `AC-23` 把它**降级为「动态」托盘内的事件行**并改为**相对时间**（`AC-A-6` 的
 *   「绝对时间」形态由本卡的 `AC-23` 取代 —— 重指向登记见证据文件 §7）。
 */

const DASH = "—";

export interface TaskDetailPanelProps {
  projectId: string;
  /** 已加载列表里的那条任务；未命中（不存在 / 已删除 / 深链未取到）→ undefined。 */
  task?: ProjectTask;
  /** owner 为深链触发的一次列表重取进行中；true 时不显示「不存在」。 */
  loading?: boolean;
  /** 写权限（`PROJECT_WRITE` = member+）；viewer → 无编辑入口与底部操作条。 */
  canEdit: boolean;
  /** 同项目任务（编辑弹窗的父任务来源；与列表同一份数据，不新增读取环）。 */
  tasks: ProjectTask[];
  /** 项目名（meta 行的项目 chip；由 owner 传入**页级已加载**的 `project.name`）。 */
  projectName?: string;
  /** 项目成员（「人 / 执行主体」两槽的名字解析；由 owner 传入**页级已加载**的成员表）。 */
  members?: ProjectMember[];
  /** 返回列表（owner 移除 `?task=`，保留 `tab=tasks`）。 */
  onBack: () => void;
  /** 保存成功后重读（owner 的 load()）。 */
  onChanged: () => void | Promise<void>;
}

/**
 * 进入判据（`PLAN §4.1` 冻结）：**有 `task` 参数即以详情为准** —— 缺 `tab` 参数时
 * 同样进详情（`?task=<id>` 隐含 tasks 详情），URL 的 `tab` 解析仍归 owner。
 */
export function hasTaskDetailParam(params: URLSearchParams): boolean {
  const taskId = params.get("task");
  return taskId !== null && taskId !== "";
}

/**
 * 「动态」事件行的**相对时间**（`AC-23` 逐字要求「相对时间」）。
 *
 * ★ 粒度 = **既有的天粒度** `calendarDaysAgo`（`C3-DETAIL.acceptance` 逐字：「只用既有
 *   天粒度 `calendarDaysAgo`；要小时级须另立契约」）⇒ 本卡**不**引 `dayjs.extend(relativeTime)`
 *   （那会新引入全局插件）。
 * ★ 分档 = 与仓内**既有**相对时间实现**同款**（`pages/Agent/Memory/ProfileOverview.tsx` 的
 *   `relTime`：今天 / 昨天 / `N 天前` / 更早回落绝对时间）—— 本卡**不发明**新断点；`SPEC.md`
 *   把「分档粒度的具体断点」明写为待 `ui` 定稿 ⇒ 待定稿后只改本函数一处。
 * ★ 已冻结边界（`SPEC.md` 权威行）：**未来时间不得渲染为负数** ⇒ `days < 0` 一律回落
 *   绝对时间（`formatServerDateTime`，服务器时区）。
 * ★ 入参单位：既有端点返回 **epoch 秒**（`formatServerDateTime` 同款）而 `calendarDaysAgo`
 *   收 **ms / ISO** ⇒ 此处显式 `* 1000`。
 */
function relativeDayText(
  epochSec: number,
  timeZone: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const days = calendarDaysAgo(epochSec * 1000, timeZone);
  if (Number.isNaN(days) || days < 0)
    return formatServerDateTime(epochSec, timeZone);
  if (days === 0) return t("projects.taskDetailTimeToday");
  if (days === 1) return t("projects.taskDetailTimeYesterday");
  if (days < 7) return t("projects.taskDetailTimeDaysAgo", { days });
  return formatServerDateTime(epochSec, timeZone);
}

/**
 * 主体位的一侧（人 **或** 执行主体）—— 复用 `Board.tsx` 的 `SubjectAvatar` / `SubjectBadge`
 * （卡片与列表用的**同一组**组件）+ `subjectDisplayName` 的**同一条**名字/兜底口径。
 * 「前 2 + `+N`」的折叠由 `taskSubjects()` 给出（本组件**只渲染**它给的 `shown` / `more`）。
 */
function SubjectSlot({
  testId,
  refs,
  more,
  moreSide,
  nameByKey,
  badge = false,
}: {
  testId: string;
  refs: TaskSubjectRef[];
  more: number;
  /** `+N` 的归属侧（普通 `data-*` 锚点，沿用 `P2-4` 的 `data-subject-more`）。 */
  moreSide: "owner" | "executor";
  nameByKey: Map<string, string>;
  /** 执行主体侧带类型徽标（卡片行④同款）。 */
  badge?: boolean;
}): JSX.Element {
  const names = refs.map((ref) => subjectDisplayName(nameByKey, ref));
  return (
    <span
      data-testid={testId}
      // 全部主体名进 `title`（与卡片同款的「不可只渲染折叠标记而无无障碍文本」）。
      title={names.join("、")}
      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
    >
      {refs.map((ref) => (
        <SubjectAvatar
          key={memberKey(ref.type, ref.id)}
          subject={ref}
          name={resolveAssigneeName(nameByKey, ref.type, ref.id)}
          className=""
        />
      ))}
      {more > 0 ? <span data-subject-more={moreSide}>{`+${more}`}</span> : null}
      {badge ? <SubjectBadge subject={refs[0]} className="" /> : null}
      <span>{names[0]}</span>
    </span>
  );
}

export default function TaskDetailPanel({
  projectId,
  task,
  loading = false,
  canEdit,
  tasks,
  projectName,
  members,
  onBack,
  onChanged,
}: TaskDetailPanelProps): JSX.Element {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [editing, setEditing] = useState(false);

  /** 附件只读下载：走**既有**端点，不新增上传/删除路径（`AC-G4-5`）。 */
  const download = async (attachment: ProjectAttachment) => {
    const blob = await projectMetadataApi.downloadAttachment(
      projectId,
      attachment.artifact_id,
    );
    // jsdom 无 createObjectURL；真实浏览器里触发一次下载。
    if (typeof URL.createObjectURL !== "function") return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = attachment.name;
    link.click();
    URL.revokeObjectURL(url);
  };

  const submitEdit = async (patch: TaskPatchValues) => {
    if (!task) return;
    await projectsApi.updateTask(projectId, task.task_id, patch);
    await onChanged();
  };

  // 完整流转史：既有端点 + `task_id`（**全量**，不传 limit ⇒ 不截断）。
  const { data: timelineEvents, loading: historyLoading } = useAsyncResource<
    ProjectTimelineEvent[]
  >(
    [],
    () => projectsApi.timeline(projectId, { taskId: task?.task_id ?? "" }),
    [projectId, task?.task_id],
    {
      enabled: Boolean(task?.task_id),
      errorFallback: t("projects.loadFailed"),
      t,
      logLabel: "task-history",
    },
  );
  /** 三要素（时间 / 操作人 / 前后状态）逐条归一 —— `payload` 是自由形状，安全取值。 */
  const events = useMemo(
    () =>
      timelineEvents.map((event) => {
        const payload = event.payload ?? {};
        const from = typeof payload.from === "string" ? payload.from : "";
        const to = typeof payload.to === "string" ? payload.to : "";
        return { ...event, from, to };
      }),
    [timelineEvents],
  );

  // 留言 feed（★「动态」托盘 = **搬移**项目级 feed）：**按 `task_id` 过滤**走既有端点
  // （`GET …/comments?task_id=`，后端早已支持 ⇒ **不新增任何后端字段/接口**）。
  // ★ 只读：写入口归项目级「动态」Tab（`决策 ⑦` / `U-3`：讨论功能本轮不做）。
  const { data: comments, loading: commentsLoading } = useAsyncResource<
    ProjectComment[]
  >(
    [],
    () => projectsApi.listComments(projectId, { taskId: task?.task_id ?? "" }),
    [projectId, task?.task_id],
    {
      enabled: Boolean(task?.task_id),
      errorFallback: t("projects.loadFailed"),
      t,
      logLabel: "task-activity",
    },
  );

  /** 成员名查表（与 `Board.tsx` 同款：`subject_type:subject_id` ⇒ 名字）。 */
  const nameByKey = useMemo(
    () =>
      new Map(
        (members ?? []).map((member) => [
          memberKey(member.subject_type, member.subject_id),
          (member.name ?? "").trim(),
        ]),
      ),
    [members],
  );

  /**
   * 双主体两槽的**取值**（★ 只消费 `taskSubjects()` 的输出，不另写展示模型）：
   * 两数组非空 ⇒ 用数组；两数组皆空且有镜像（旧响应 / 旧缓存）⇒ 按镜像的 `type` 归位。
   */
  const subjects = useMemo(() => (task ? taskSubjects(task) : null), [task]);
  const mirrored =
    subjects && subjects.owners.length + subjects.executors.length === 0;
  const ownerRefs = subjects
    ? subjects.owners.length > 0
      ? subjects.owners
      : mirrored && subjects.single?.type === "user"
      ? [subjects.single]
      : []
    : [];
  const executorRefs = subjects
    ? subjects.executors.length > 0
      ? subjects.executors
      : mirrored && subjects.single && subjects.single.type !== "user"
      ? [subjects.single]
      : []
    : [];
  const ownerShown =
    subjects && subjects.owners.length > 0 ? subjects.ownerShown : ownerRefs;
  const ownerMore =
    subjects && subjects.owners.length > 0 ? subjects.ownerMore : 0;
  const executorShown =
    subjects && subjects.executors.length > 0
      ? subjects.executorShown
      : executorRefs;
  const executorMore =
    subjects && subjects.executors.length > 0 ? subjects.executorMore : 0;

  const parent = task?.parent_id
    ? tasks.find((item) => item.task_id === task.parent_id)
    : undefined;
  const numberText = task ? taskNumberText(task.task_number) : null;

  /**
   * meta 行（`AC-22` 四元素：状态 chip / 双主体两槽 / 项目 chip / `⋯`）——
   * ★ **空态不渲染空壳**（`SPEC.md` 边界 Case + 本卡 acceptance：两侧皆空 ⇒ 该位不渲染、
   * 不报错）：人槽 / 执行主体槽各自在自己的主体为空时**整块不渲染**。
   */
  const metaItems: ReactNode[] = [];
  if (task) {
    metaItems.push(
      <Descriptions.Item
        key="status"
        label={t("projects.taskListColumnStatus")}
      >
        <span data-testid="task-detail-status">
          {t(STATUS_LABEL_KEYS[task.status])}
        </span>
      </Descriptions.Item>,
      <Descriptions.Item
        key="priority"
        label={t("projects.taskListColumnPriority")}
      >
        <span data-testid="task-detail-priority">{task.priority}</span>
      </Descriptions.Item>,
    );
    if (ownerShown.length > 0) {
      metaItems.push(
        <Descriptions.Item
          key="owners"
          label={t("projects.taskListColumnAssignee")}
        >
          <SubjectSlot
            testId="task-detail-assignee"
            refs={ownerShown}
            more={ownerMore}
            moreSide="owner"
            nameByKey={nameByKey}
          />
        </Descriptions.Item>,
      );
    }
    if (executorShown.length > 0) {
      metaItems.push(
        <Descriptions.Item
          key="executors"
          label={t("projects.taskDetailExecutor")}
        >
          <SubjectSlot
            testId="task-detail-executor"
            refs={executorShown}
            more={executorMore}
            moreSide="executor"
            nameByKey={nameByKey}
            badge
          />
        </Descriptions.Item>,
      );
    }
    metaItems.push(
      <Descriptions.Item key="project" label={t("projects.chipProject")}>
        <span data-testid="task-detail-project">
          {projectName?.trim() ? projectName : DASH}
        </span>
      </Descriptions.Item>,
    );
    metaItems.push(
      <Descriptions.Item key="parent" label={t("projects.taskDetailParent")}>
        <span data-testid="task-detail-parent">
          {parent ? parent.title : task.parent_id || DASH}
        </span>
      </Descriptions.Item>,
      <Descriptions.Item
        key="start_at"
        label={t("projects.taskListColumnStartAt")}
      >
        {formatServerDateTime(task.start_at ?? 0, timeZone)}
      </Descriptions.Item>,
      <Descriptions.Item key="due_at" label={t("projects.taskListColumnDueAt")}>
        {formatServerDateTime(task.due_at ?? 0, timeZone)}
      </Descriptions.Item>,
    );
  }

  return (
    <div className={styles.panel} data-testid="task-detail-panel">
      <div className={styles.header}>
        <button
          type="button"
          className={styles.back}
          data-testid="task-detail-back"
          onClick={onBack}
        >
          <ArrowLeft size={14} aria-hidden />
          {t("projects.taskDetailBack")}
        </button>
        <span className={styles.headerTitle}>
          {t("projects.taskDetailTitle")}
        </span>
        {canEdit && task ? (
          <button
            type="button"
            className={styles.edit}
            data-testid="task-detail-edit"
            onClick={() => setEditing(true)}
          >
            <Pencil size={14} aria-hidden />
            {t("projects.editTaskTitle")}
          </button>
        ) : null}
      </div>

      {!task ? (
        loading ? (
          <div className={styles.loading} data-testid="task-detail-loading">
            <Spin size="small" />
          </div>
        ) : (
          <div className={styles.notFound} data-testid="task-detail-not-found">
            {t("projects.taskDetailNotFound")}
          </div>
        )
      ) : (
        <>
          {/* ① 标题区：编号 + 标题 + 状态 chip + 「讨论」外观（`U-3` ⓐ：外观可做、功能不做）
              + `⋯` 外观（★ 菜单项集合待 `ui` 定稿 ⇒ 只落**锚点**，不发明菜单项、不留死按钮）。 */}
          <div className={styles.titleRow}>
            {numberText ? (
              <span
                data-testid="task-detail-number"
                style={{
                  color: "var(--fn-text-secondary)",
                  fontSize: 12,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {numberText}
              </span>
            ) : null}
            <h3 className={styles.title} data-testid="task-detail-title">
              {task.title}
            </h3>
            <Tag color={STATUS_COLORS[task.status]}>
              {t(STATUS_LABEL_KEYS[task.status])}
            </Tag>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                marginInlineStart: "auto",
              }}
            >
              <Button
                size="small"
                disabled
                data-testid="task-detail-discuss"
                title={t("projects.taskDetailDiscuss")}
                icon={<MessageSquarePlus size={14} aria-hidden />}
              >
                {t("projects.taskDetailDiscuss")}
              </Button>
              <Button
                size="small"
                disabled
                data-testid="task-detail-more"
                title={t("projects.chipMore")}
                aria-label={t("projects.chipMore")}
                icon={<MoreHorizontal size={14} aria-hidden />}
              />
            </span>
          </div>

          {/* ② meta 行。 */}
          <Descriptions column={2} size="small" className={styles.meta}>
            {metaItems}
          </Descriptions>

          {/* ③ 正文分节：**沿用既有分节**（节名与顺序待 `ui` 定稿 ⇒ 本卡不重排、不发明）。 */}
          <section className={styles.section}>
            <div className={styles.sectionTitle}>
              {t("projects.taskDetailDescription")}
            </div>
            <div
              className={styles.description}
              data-testid="task-detail-description"
            >
              {task.description || DASH}
            </div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionTitle}>
              {t("projects.taskDetailDeps")}
            </div>
            <div className={styles.inline} data-testid="task-detail-deps">
              {task.deps?.length ? task.deps.join(", ") : DASH}
            </div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionTitle}>
              {t("projects.taskListColumnTags")}
            </div>
            <div className={styles.inline} data-testid="task-detail-tags">
              {task.tags?.length ? (
                <Space size={4} wrap>
                  {task.tags.map((tag) => (
                    <Tag key={tag.tag_id} color={tag.color || undefined}>
                      {tag.name}
                    </Tag>
                  ))}
                </Space>
              ) : (
                DASH
              )}
            </div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionTitle}>
              {t("projects.customFieldsTitle")}
            </div>
            <div
              className={styles.inline}
              data-testid="task-detail-custom-fields"
            >
              {task.custom_fields?.length ? (
                <Space direction="vertical" size={2}>
                  {task.custom_fields.map((field) => (
                    <span key={field.field_id}>
                      <span className={styles.fieldLabel}>{field.label}</span>
                      {": "}
                      {String(field.value ?? DASH)}
                    </span>
                  ))}
                </Space>
              ) : (
                DASH
              )}
            </div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionTitle}>
              {t("projects.taskDetailAttachments")}
            </div>
            <div
              className={styles.inline}
              data-testid="task-detail-attachments"
            >
              {task.attachments?.length ? (
                <Space direction="vertical" size={4}>
                  {task.attachments.map((attachment) => (
                    <span
                      key={attachment.artifact_id}
                      className={styles.attachment}
                      data-testid={`task-detail-attachment-${attachment.artifact_id}`}
                    >
                      <Paperclip size={12} aria-hidden />
                      <span className={styles.attachmentName}>
                        {attachment.name}
                      </span>
                      <Tooltip title={t("projects.download")}>
                        <button
                          type="button"
                          className={styles.iconButton}
                          data-testid={`task-detail-download-${attachment.artifact_id}`}
                          aria-label={t("projects.download")}
                          onClick={() => void download(attachment)}
                        >
                          <Download size={13} aria-hidden />
                        </button>
                      </Tooltip>
                    </span>
                  ))}
                </Space>
              ) : (
                t("projects.attachmentsEmpty")
              )}
            </div>
          </section>

          {/* ④「动态」时间线（灰底托盘 `--fn-bg-group`，参考图 `.timeline-tray`）：
              事件行 = 头像 + 谁 + 动作 + **相对时间**；其下 = **搬移进来的留言 feed**
              （`CommentFeed`，与项目级「动态」Tab **同一份实现**）。
              ⚠️ 诚实边界：事件来源仍是既有 `…/timeline?task_id=`（审计事件表），
              **不声称**是完整业务动态流；`AC-23` 的「旧「过程」裸事件行不再渲染」=
              旧行 testid `timeline-node`（无头像、绝对时间）**已消失**（两向断言见测试）。
              灰底与托盘圆角走**内联样式**（`P2-4` 的先例：不新增/不改 `.module.less`）。 */}
          <section
            className={styles.section}
            data-testid="task-detail-activity"
          >
            <div className={styles.sectionTitle}>
              {t("projects.tabDynamic")}
            </div>
            <div
              style={{
                background: "var(--fn-bg-group)",
                borderRadius: 14,
                padding: 12,
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              {historyLoading && events.length === 0 ? (
                <div className={styles.loading}>
                  <Spin size="small" />
                </div>
              ) : events.length === 0 ? (
                <div
                  className={styles.sectionEmpty}
                  data-testid="task-detail-timeline-empty"
                >
                  {t("projects.taskDetailTimelineEmpty")}
                </div>
              ) : (
                <ul
                  data-testid="task-detail-timeline"
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    padding: 0,
                    margin: 0,
                    listStyle: "none",
                  }}
                >
                  {events.map((event, index) => {
                    // `actor_name` 优先、拿不到回退稳定标识（**不得空白**，AC-A-6 口径不变）。
                    const actorText = event.actor_name?.trim()
                      ? event.actor_name
                      : event.actor;
                    return (
                      <li
                        key={`${event.at}-${event.actor}-${index}`}
                        data-testid="activity-event"
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          minHeight: 28,
                          fontSize: 12,
                        }}
                      >
                        <Avatar
                          size={24}
                          style={{ flexShrink: 0 }}
                          data-testid="activity-avatar"
                        >
                          {actorText.charAt(0)}
                        </Avatar>
                        <span
                          className={styles.timelineNode}
                          data-testid="timeline-actor"
                        >
                          {actorText}
                        </span>
                        <span className={styles.timelineNode}>
                          {event.action}
                          {event.from || event.to
                            ? ` ${event.from || DASH} → ${event.to || DASH}`
                            : ""}
                        </span>
                        <span
                          className={styles.timelineTime}
                          data-testid="activity-time"
                          style={{ marginInlineStart: "auto" }}
                        >
                          {relativeDayText(event.at, timeZone, t)}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}

              <CommentFeed
                comments={comments}
                timeZone={timeZone}
                canWrite={false}
                loading={commentsLoading}
                showAvatar
                empty={
                  <div
                    className={styles.sectionEmpty}
                    data-testid="task-detail-activity-empty"
                  >
                    {t("projects.feedEmpty")}
                  </div>
                }
              />
            </div>
          </section>

          {/* ⑤ 底部操作条五项（`AC-24`）。★ 本批**不接线**任何动作（后端行为没有任何
              契约；发明动作 = 越界）⇒ 五项以 `disabled` 呈现，只落**可定位锚点**；
              `viewer`（`canEdit=false`）⇒ 整条不渲染（`REVIEW.md · S-06` 的「禁用/不渲染」）。 */}
          {canEdit ? (
            <div
              data-testid="task-detail-bottom-bar"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                flexWrap: "wrap",
                marginTop: 16,
                paddingTop: 12,
                borderTop: "1px solid var(--fn-border-primary)",
              }}
            >
              <Button
                size="small"
                disabled
                data-testid="task-detail-bottom-members"
                icon={<Plus size={14} aria-hidden />}
              >
                {t("projects.members")}
              </Button>
              <Button
                size="small"
                disabled
                data-testid="task-detail-bottom-agent"
                icon={<Plus size={14} aria-hidden />}
              >
                {t("projects.taskDetailAddAgent")}
              </Button>
              <Button
                size="small"
                disabled
                data-testid="task-detail-bottom-project"
                icon={<ChevronDown size={14} aria-hidden />}
                iconPosition="end"
              >
                {projectName?.trim() ? projectName : t("projects.chipProject")}
              </Button>
              <Button
                size="small"
                disabled
                data-testid="task-detail-bottom-issue"
                icon={<ChevronDown size={14} aria-hidden />}
                iconPosition="end"
              >
                {t("projects.taskDetailIssue")}
              </Button>
              <Button
                size="small"
                disabled
                data-testid="task-detail-bottom-attachment"
                icon={<Paperclip size={14} aria-hidden />}
              >
                {t("projects.taskDetailAttachments")}
              </Button>
            </div>
          ) : null}

          <TaskCreateModal
            open={editing}
            projectId={projectId}
            tasks={tasks}
            canEdit={canEdit}
            mode="edit"
            initialValues={taskToValues(task)}
            onSubmitEdit={submitEdit}
            onClose={() => setEditing(false)}
            onCreated={onChanged}
          />
        </>
      )}
    </div>
  );
}
