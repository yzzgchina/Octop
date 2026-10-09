import { useState } from "react";
import { Avatar, Button, Input, Spin } from "antd";
import { BadgeCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";

import type { ProjectComment } from "../../../api/modules/projects";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import styles from "./DynamicTab.module.less";

/**
 * 留言 feed —— **受 props 驱动的展示组件**（`P2-5`）。
 *
 * ★★ 为什么有本模块（`C3-DETAIL` 的唯一高风险 · `PLAN §CD · C3`）：
 * 参考图要把留言 feed 在**任务详情页的「动态」托盘**里也渲染一遍，而 feed 本体长在
 * **项目级「动态」Tab**（`DynamicTab.tsx`）⇒ 同一个 feed 有**两个渲染位**。「搬移」
 * 的裁定 = **复用（改 props）**，**禁止**复制一份（`PLAN` 逐字「把 feed 抽成受 props
 * 驱动的展示组件，两处消费；禁止复制一份（协议 §34/§35）」）⇒ 本模块就是那**一份**：
 *
 * | 消费方 | 数据源 | 写入口 |
 * |---|---|---|
 * | `Detail/DynamicTab.tsx`（项目级「动态」Tab） | `listComments(projectId)` | `canWrite` ⇒ 结论 / 编辑 / 删除 / 附件 |
 * | `Detail/TaskDetailPanel.tsx`（任务详情「动态」托盘） | `listComments(projectId, { taskId })` | `canWrite=false` ⇒ 只读（`决策 ⑦` / `U-3`：讨论功能本轮不做） |
 *
 * ★ 判据（判别性）：**摘掉**本模块里的任何一条渲染 ⇒ **两个**消费方的用例**同时**转红
 * （见 `EVIDENCE-P2-5.md` §判别性对照）—— 这正是不允许分叉的可机判形态。
 *
 * ★ 样式表**共用** `DynamicTab.module.less`（未新增、未拆分 `.less`：feed 的样式本来
 * 就在那一份里，第二个 `.less` = 造第二个真相）。
 *
 * ★ 编辑态（`editingId` / `editingBody`）是**展示局部状态**，随 JSX 一起搬进本组件
 * （两个消费方都不需要知道它）；落库仍由调用方注入的回调承担。
 */

/**
 * 结论文本 =「结论 · 由 <谁> 采纳」（`P3` 展示形态 / `P3b` 显示口径 / `L30`）。
 *
 * 回退链（**与 `feed-author` 同一条口径**：名字优先 → 回退 → 不空白）：
 *   ① `concluded_by_name`（后端解析好，**直接用**，不自己拼 ✗）
 *   ② 拿不到名字 ⇒ `concluded_by_type:concluded_by_id`（★ **仍能区分「谁」**）
 *   ③ 连 id 都没有 ⇒ `concludedByUnknown`（「已注销用户」，**不得空白**）
 */
function concludedByLabel(
  comment: ProjectComment,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const name = comment.concluded_by_name?.trim();
  if (name) return t("projects.conclusionBy", { name });
  const id = comment.concluded_by_id?.trim();
  if (id) {
    const kind = comment.concluded_by_type?.trim();
    return t("projects.conclusionBy", { name: kind ? `${kind}:${id}` : id });
  }
  return t("projects.concludedByUnknown");
}

export interface CommentFeedProps {
  comments: ProjectComment[];
  timeZone: string;
  /** 写入口（结论 / 编辑 / 删除 / 附件）是否渲染 —— viewer 与任务详情托盘均为 `false`。 */
  canWrite: boolean;
  loading?: boolean;
  /**
   * 空态节点（**调用方**提供：项目级 Tab 有 3 个变体 —— 与我相关 / 按人筛选 / 无留言，
   * 各自的文案与定位锚点不同）。`comments.length > 0` 时不渲染。
   */
  empty: ReactNode;
  /**
   * 每条留言是否带头像（参考图 `.msg` = 头像 + 名字；项目级 Tab 保持**既有**形态 ⇒
   * 默认 `false`，明细由调用方显式打开 ⇒ 项目级既有断言零改动）。
   */
  showAvatar?: boolean;
  onConclude?: (comment: ProjectComment) => void;
  onSaveEdit?: (commentId: string, body: string) => void;
  onDelete?: (commentId: string) => void;
  onAttach?: (commentId: string, file: File) => void;
}

export default function CommentFeed({
  comments,
  timeZone,
  canWrite,
  loading = false,
  empty,
  showAvatar = false,
  onConclude,
  onSaveEdit,
  onDelete,
  onAttach,
}: CommentFeedProps): JSX.Element {
  const { t } = useTranslation();
  /** 正在编辑的留言 id（null = 无）与其草稿 —— 展示局部状态，两个消费方都不需要。 */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingBody, setEditingBody] = useState("");

  if (loading && comments.length === 0) {
    return (
      <div className={styles.centered}>
        <Spin size="small" />
      </div>
    );
  }
  if (comments.length === 0) {
    return <>{empty}</>;
  }

  return (
    <ul className={styles.list} data-testid="feed-list">
      {comments.map((comment) => (
        <li
          key={comment.comment_id}
          className={styles.item}
          data-testid="feed-item"
        >
          <div className={styles.itemHead}>
            {showAvatar ? (
              <Avatar
                size={24}
                style={{ flexShrink: 0 }}
                data-testid="feed-author-avatar"
              >
                {/* 头像取数 = **既有**显示名回退链的首字符（与 `.author` 同一事实，
                    不得空白）；解析不到名字时用稳定标识的首字符而非空白。 */}
                {(
                  comment.name?.trim() ||
                  `${comment.author_type}:${comment.author_id}`
                ).charAt(0)}
              </Avatar>
            ) : null}
            <span className={styles.author} data-testid="feed-author">
              {/* 显示名优先；拿不到 ⇒ 回退 `type:id`（**不得空白**，与成员行同口径）。 */}
              {comment.name?.trim()
                ? comment.name
                : `${comment.author_type}:${comment.author_id}`}
            </span>
            <span className={styles.time}>
              {formatServerDateTime(comment.created_at, timeZone)}
            </span>
            {comment.concluded ? (
              <span
                className={styles.badge}
                data-testid="comment-conclusion-badge"
              >
                <BadgeCheck size={13} aria-hidden />
                {t("projects.conclusionBadge")}
              </span>
            ) : null}
            {comment.concluded ? (
              <span
                className={styles.concludedBy}
                data-testid="comment-conclusion-by"
              >
                {concludedByLabel(comment, t)}
              </span>
            ) : null}
          </div>
          {editingId === comment.comment_id ? (
            <div className={styles.editRow}>
              <Input.TextArea
                className={styles.composerInput}
                value={editingBody}
                autoSize={{ minRows: 1, maxRows: 6 }}
                aria-label={t("projects.feedPost")}
                data-testid="comment-edit-input"
                onChange={(event) => setEditingBody(event.target.value)}
              />
              <Button
                size="small"
                type="primary"
                data-testid="comment-edit-save"
                onClick={() => onSaveEdit?.(comment.comment_id, editingBody)}
              >
                {t("projects.feedPost")}
              </Button>
              <Button
                size="small"
                data-testid="comment-edit-cancel"
                onClick={() => setEditingId(null)}
              >
                {t("common.cancel", "取消")}
              </Button>
            </div>
          ) : (
            <div className={styles.body}>{comment.body}</div>
          )}
          {canWrite ? (
            <div className={styles.actions}>
              <Button
                type="link"
                size="small"
                className={styles.concludeAction}
                onClick={() => onConclude?.(comment)}
              >
                {comment.concluded
                  ? t("projects.unconclude")
                  : t("projects.conclude")}
              </Button>
              <Button
                type="link"
                size="small"
                data-testid="comment-edit"
                onClick={() => {
                  setEditingId(comment.comment_id);
                  setEditingBody(comment.body);
                }}
              >
                {t("common.edit", "编辑")}
              </Button>
              {comment.concluded ? (
                <>
                  {/* ★ 与后端**同一口径**：已采纳 ⇒ 删除【事前禁用】，提示用**同一个键**
                      （`apiErrors.PROJECT_COMMENT_CONCLUDED`，与 409 的 message 同源）。 */}
                  <Button
                    type="link"
                    size="small"
                    disabled
                    data-testid="comment-delete"
                  >
                    {t("common.delete", "删除")}
                  </Button>
                  <span
                    className={styles.blockedHint}
                    data-testid="comment-delete-blocked"
                  >
                    {t("apiErrors.PROJECT_COMMENT_CONCLUDED")}
                  </span>
                </>
              ) : (
                <Button
                  type="link"
                  size="small"
                  data-testid="comment-delete"
                  onClick={() => onDelete?.(comment.comment_id)}
                >
                  {t("common.delete", "删除")}
                </Button>
              )}
              <label className={styles.attachLabel}>
                <input
                  type="file"
                  className={styles.attachInput}
                  data-testid="comment-attachment-input"
                  aria-label={t("projects.attachFile")}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) onAttach?.(comment.comment_id, file);
                    event.target.value = "";
                  }}
                />
                <span data-testid="comment-attach">
                  {t("projects.feedPost")}
                </span>
              </label>
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
