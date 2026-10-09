import { useMemo, useState } from "react";
import { Button, Input, Typography } from "antd";
import { Send } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";

import {
  projectsApi,
  type ProjectComment,
} from "../../../api/modules/projects";
import { useAsyncResource } from "../../../hooks/useAsyncResource";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useProjectMembers } from "../../../hooks/useProjectMembers";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import CommentFeed from "./CommentFeed";
import styles from "./DynamicTab.module.less";

const { Text } = Typography;

/** 与 QuickInput 同一口径的可写角色集（`PROJECT_WRITE`）。 */
const WRITE_ROLES = new Set(["owner", "admin", "member"]);

/**
 * 动态 Tab —— 项目留言 feed（PLAN §1.2 / 批次八 A2）。
 *
 * 结构（逐字）：`feed-composer`（textarea + 发布）→ `feed-list`/`feed-item`
 * （作者 / 时间（**服务器时区**格式化）/ 正文 / **结论徽标**）→ 空态 `feed-empty`。
 *
 * ★ `dynamicPlaceholder`/`dynamicComingSoon` **保留为键但不再渲染**（`PLAN §9 O4`）。
 * ★ 本批**不做**（`O3`）：与我相关 / 成员动态 / 嵌套回复 / 通知 / 附件 / 编辑删除。
 * ★ 作者由**服务端**按登录身份判定（`CommentCreate` 无作者字段）⇒ 前端无作者选择器。
 */
function DynamicTab() {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const { projectId = "" } = useParams<{ projectId: string }>();
  const currentUser = useCurrentUser();
  const { members, loading: membersLoading } = useProjectMembers(projectId);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  /** 正在编辑的留言 id（null = 无）与其草稿。 */
  /** ★「与我相关」= **筛选参数**（`relevance=me`），**不是新视图**（P3 冻结）。 */
  const [relevanceOnly, setRelevanceOnly] = useState(false);
  /** ★ 按人筛选（`author_id` + `author_type` **成对**）；null = 不筛。 */
  const [authorFilter, setAuthorFilter] = useState<{
    id: string;
    type: string;
    label: string;
  } | null>(null);
  /** ★ 提及：**只由显式 UI 动作产生**（不做文本解析）；`touched` 区分"没动过"与"显式清空"。 */
  const [mentions, setMentions] = useState<
    Array<{ type: string; id: string; label: string }>
  >([]);
  const [mentionsTouched, setMentionsTouched] = useState(false);

  const {
    data: comments,
    loading,
    refresh,
  } = useAsyncResource<ProjectComment[]>(
    [],
    () =>
      projectsApi.listComments(projectId, {
        ...(relevanceOnly ? { relevance: "me" as const } : {}),
        ...(authorFilter
          ? { authorId: authorFilter.id, authorType: authorFilter.type }
          : {}),
      }),
    [projectId, relevanceOnly, authorFilter],
    {
      enabled: projectId !== "",
      errorFallback: t("projects.loadFailed"),
      t,
      logLabel: "project-feed",
    },
  );

  /** UX 门（服务端仍在）：`viewer` 不渲染写入口。 */
  const canWrite = useMemo(() => {
    if (currentUser?.id === undefined) return false;
    const mine = members.find(
      (member) =>
        member.subject_type === "user" && member.user_id === currentUser.id,
    );
    return mine ? WRITE_ROLES.has(mine.role) : false;
  }, [currentUser, members]);

  const publish = async () => {
    const body = draft.trim();
    if (!body || saving) return;
    setSaving(true);
    try {
      await projectsApi.createComment(projectId, {
        body,
        // ★ `[]`（显式"没 @ 任何人"）与省略（NULL）是**两种事实**，不得塌缩。
        ...(mentionsTouched
          ? { mentions: mentions.map(({ type, id }) => ({ type, id })) }
          : {}),
      });
      setDraft("");
      setMentions([]);
      setMentionsTouched(false);
      await refresh();
    } catch (error) {
      message.error(apiErrorMessage(error, t("projects.feedPost"), t));
    } finally {
      setSaving(false);
    }
  };

  const toggleConclusion = async (comment: ProjectComment) => {
    try {
      if (comment.concluded) {
        await projectsApi.unconcludeComment(projectId, comment.comment_id);
      } else {
        await projectsApi.concludeComment(projectId, comment.comment_id);
      }
      await refresh();
    } catch (error) {
      message.error(apiErrorMessage(error, t("projects.conclude"), t));
    }
  };

  /** 编辑保存（`PATCH …/comments/{cid}`）。正文由 `CommentFeed` 注入（展示局部状态）。 */
  const saveEdit = async (commentId: string, body: string) => {
    const next = body.trim();
    if (!next) return;
    try {
      await projectsApi.updateComment(projectId, commentId, next);
      await refresh();
    } catch (error) {
      message.error(apiErrorMessage(error, t("projects.loadFailed"), t));
    }
  };

  /** 硬删（`DELETE …/comments/{cid}`）。★ 已采纳留言**事前禁用**入口（服务端另有 409 兜底）。 */
  const removeComment = async (commentId: string) => {
    try {
      await projectsApi.deleteComment(projectId, commentId);
      await refresh();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("apiErrors.PROJECT_COMMENT_CONCLUDED"), t),
      );
    }
  };

  /** 附件挂载：先暂存（`POST …/attachments`），再绑到该留言（`PATCH …/attachments/{id}`）。 */
  const attachToComment = async (commentId: string, file: File) => {
    try {
      const staged = await projectsApi.uploadStagedAttachment(projectId, file);
      await projectsApi.bindAttachment(projectId, staged.artifact_id, {
        commentId,
      });
      await refresh();
    } catch (error) {
      message.error(apiErrorMessage(error, t("projects.loadFailed"), t));
    }
  };

  /** 候选集 = **项目成员**（`PLAN §1.2b` 冻结；不是全体用户 ✗）。 */
  const memberCandidates = useMemo(
    () =>
      members
        .filter(
          (member) =>
            member.subject_type === "user" || member.subject_type === "agent",
        )
        .map((member) => ({
          type: member.subject_type,
          id: member.subject_id,
          label: member.name?.trim()
            ? member.name
            : `${member.subject_type}:${member.subject_id}`,
        })),
    [members],
  );

  const roleKnown = !membersLoading;

  return (
    <div className={styles.wrapper} data-testid="project-dynamic">
      {/* ★ 筛选条：与我相关（P3 = 筛选参数，不是新视图）+ 按人筛选（候选 = 项目成员）。 */}
      <div className={styles.filters}>
        <Button
          size="small"
          type={relevanceOnly ? "primary" : "default"}
          data-testid="feed-relevance-toggle"
          onClick={() => setRelevanceOnly((value) => !value)}
        >
          {t("projects.feedRelevanceMine")}
        </Button>
        <select
          className={styles.authorSelect}
          data-testid="feed-author-filter"
          aria-label={t("projects.feedFilterByAuthor")}
          value={authorFilter ? `${authorFilter.type}:${authorFilter.id}` : ""}
          onChange={(event) => {
            const picked = memberCandidates.find(
              (candidate) =>
                `${candidate.type}:${candidate.id}` === event.target.value,
            );
            setAuthorFilter(picked ?? null);
          }}
        >
          <option value="">{t("projects.feedFilterAll")}</option>
          {memberCandidates.map((candidate) => (
            <option
              key={`${candidate.type}:${candidate.id}`}
              value={`${candidate.type}:${candidate.id}`}
              data-testid={`feed-author-option-${candidate.id}`}
            >
              {candidate.label}
            </option>
          ))}
        </select>
        {authorFilter ? (
          <Button
            size="small"
            type="link"
            data-testid="feed-filter-clear"
            onClick={() => setAuthorFilter(null)}
          >
            {t("projects.feedFilterClear")}
          </Button>
        ) : null}
      </div>
      {roleKnown && canWrite ? (
        <div className={styles.composer} data-testid="feed-composer">
          <Input.TextArea
            className={styles.composerInput}
            value={draft}
            autoSize={{ minRows: 1, maxRows: 4 }}
            placeholder={t("projects.feedComposerPlaceholder")}
            aria-label={t("projects.feedComposerPlaceholder")}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button
            type="primary"
            icon={<Send size={14} />}
            loading={saving}
            disabled={draft.trim().length === 0}
            onClick={() => void publish()}
          >
            {t("projects.feedPost")}
          </Button>
          <select
            className={styles.mentionSelect}
            data-testid="feed-mention-picker"
            aria-label={t("projects.feedMentions")}
            value=""
            onChange={(event) => {
              const picked = memberCandidates.find(
                (candidate) =>
                  `${candidate.type}:${candidate.id}` === event.target.value,
              );
              if (!picked) return;
              setMentionsTouched(true);
              setMentions((current) =>
                current.some(
                  (item) => item.id === picked.id && item.type === picked.type,
                )
                  ? current
                  : [...current, picked],
              );
            }}
          >
            <option value="">{t("projects.feedMentions")}</option>
            {memberCandidates.map((candidate) => (
              <option
                key={`${candidate.type}:${candidate.id}`}
                value={`${candidate.type}:${candidate.id}`}
              >
                {candidate.label}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {mentions.length > 0 ? (
        <div className={styles.mentionChips} data-testid="feed-mention-chips">
          {mentions.map((item) => (
            <span
              key={`${item.type}:${item.id}`}
              className={styles.mentionChip}
              data-testid={`feed-mention-chip-${item.id}`}
            >
              @{item.label}
            </span>
          ))}
          {/* ★ 显式清空 ⇒ 提交 `mentions: []`（"显式声明没 @ 任何人"），与**未动过**（NULL）两存。 */}
          <Button
            size="small"
            type="link"
            data-testid="feed-mentions-clear"
            onClick={() => {
              setMentionsTouched(true);
              setMentions([]);
            }}
          >
            {t("projects.feedFilterClear")}
          </Button>
        </div>
      ) : null}

      <CommentFeed
        comments={comments}
        timeZone={timeZone}
        canWrite={canWrite}
        loading={loading}
        empty={
          relevanceOnly ? (
            <Text type="secondary" data-testid="feed-empty-relevance">
              {t("projects.feedEmptyRelevance")}
            </Text>
          ) : authorFilter ? (
            <Text type="secondary" data-testid="feed-empty-filtered">
              {t("projects.feedEmptyFiltered")}
            </Text>
          ) : (
            <Text type="secondary" data-testid="feed-empty">
              {t("projects.feedEmpty")}
            </Text>
          )
        }
        onConclude={(comment) => void toggleConclusion(comment)}
        onSaveEdit={(commentId, body) => void saveEdit(commentId, body)}
        onDelete={(commentId) => void removeComment(commentId)}
        onAttach={(commentId, file) => void attachToComment(commentId, file)}
      />
    </div>
  );
}

export default DynamicTab;
