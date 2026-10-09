import type { ProjectTask } from "../../../api/modules/projects";
import type { TaskCreateValues } from "./TaskCreateModal";

/**
 * `ProjectTask` → 编辑器值的**唯一**适配器（PLAN §5.1：不复制字段定义，只做形状映射；
 * 字段类型全部来自 `TaskCreateModal` 导出的 `TaskCreateValues`）。
 *
 * ★★ 收敛（`P2-5`）：本函数此前在 **两份** 文件里逐字重复 —— `Detail/index.tsx` 与
 * `Detail/TaskDetailPanel.tsx`（`P2-12` 的登记：「`TaskDetailPanel.tsx` 里有第二份同名
 * 私有 `taskToValues()`，返回已废止的 `assignee`；形状变更后 `tsc -b` 强制报 `TS2353`
 * ⇒ 只做了 4 行机械对齐，收敛那份重复是 `P2-5` 的活」）。两份拷贝 = 同一事实两处定义
 * ⇒ 每次形状变更都必须**同步改两处**（漏一处就编译红）。现收敛为**单一出处**，
 * 两个消费方一律 `import { taskToValues } from "./taskCreateValues"`；
 * 判据 = `grep -rn "function taskToValues" dashboard/src` 命中数 **= 1**。
 *
 * ★ 形状（`PLAN §B2.1b` / `§B2.5 (a)`）：`TaskOut.owners` / `executors` **恒为数组**
 * （绝不返回 `null`）⇒ 直接透传；两者与派生的单值镜像 `assignee_type` / `assignee_id`
 * 并存，但**写入**只走这两个数组（`V3` 互斥）。
 */
export function taskToValues(task: ProjectTask): TaskCreateValues {
  return {
    task_id: task.task_id,
    title: task.title,
    description: task.description,
    status: task.status,
    priority: task.priority,
    owners: task.owners ?? [],
    executors: task.executors ?? [],
    tag_ids: (task.tags ?? []).map((tag) => tag.tag_id),
    start_at: task.start_at,
    due_at: task.due_at,
    parent_id: task.parent_id,
    deps: task.deps ?? [],
    custom_fields: Object.fromEntries(
      (task.custom_fields ?? []).map((field) => [field.field_id, field.value]),
    ),
  };
}
