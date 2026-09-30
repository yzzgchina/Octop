-- Schema v27: project task numbering, multi-subject assignment, comment quotes.
--
-- Canonical v26 -> v27 DDL for SQLite. One migration pair carries five groups:
--   (a) ``projects.task_prefix`` -- the per-project numbering prefix.
--   (b) ``project_tasks.task_number`` + ``project_task_counters`` + the
--       in-project unique index (PLAN §B1.2).
--   (c) ``project_task_owners`` / ``project_task_executors`` -- the multi-valued
--       "assigned users" and "agent / team executors" child tables
--       (PLAN §B2.3.1). They replace the single-value
--       ``project_tasks.owner_user_id`` / ``project_tasks.executor`` columns,
--       which are **withdrawn** (PLAN §B2.1b) and are deliberately NOT created
--       here: one column cannot hold N subjects.
--   (d) ``project_comments.parent_comment_id`` / ``quote_excerpt`` (PLAN §B3.1).
--   (e) the data backfill, which has no cross-dialect SQL form
--       (``derive_task_prefix`` drops non-ASCII characters and leading digits,
--       and SQLite has no regex) -- it lives in ``migrate.py``'s
--       ``_backfill_project_task_numbering`` and is idempotent (PLAN §B1.5).
--
-- ``ALTER TABLE ... ADD COLUMN`` has no ``IF NOT EXISTS`` on SQLite, so re-run
-- safety comes from the ``_schema_version`` watermark plus that idempotent
-- ``migrate.py`` helper -- the 022/023/024 precedent.
--
-- ``_schema_version`` is the applied-migration watermark: new tables in this
-- same pair do NOT raise it further (PLAN §B2.3.3).

ALTER TABLE projects ADD COLUMN task_prefix TEXT NOT NULL DEFAULT '';

ALTER TABLE project_tasks ADD COLUMN task_number TEXT;
CREATE TABLE IF NOT EXISTS project_task_counters (
  project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
  last_seq   INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_tasks_number
  ON project_tasks(project_id, task_number);

-- No ``CHECK (subject_type IN ('agent','team'))``: the same-domain
-- ``project_members.subject_type`` has none either (019_projects.sql), so the
-- value domain stays a single repo-layer validation point (PLAN §B2.3.1).
--
-- ``user_id ... ON DELETE CASCADE``: lead ruling 2026-09-30 (PLAN §B2.3.1). The
-- first draft left ``ON DELETE`` implicit (= NO ACTION); with
-- ``PRAGMA foreign_keys = ON`` (pool.py @48) that made ``DELETE FROM users``
-- fail for a user who was merely assigned to a task. An assignment is a
-- dependent row -- removing the user removes it (empty set = unassigned, R21)
-- -- and most ``REFERENCES users(id)`` in this repo already cascade
-- (e.g. 001_initial.sql @86).
CREATE TABLE IF NOT EXISTS project_task_owners (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES project_tasks(task_id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE(task_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_project_task_owners_user ON project_task_owners(user_id);

CREATE TABLE IF NOT EXISTS project_task_executors (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id      TEXT NOT NULL REFERENCES project_tasks(task_id) ON DELETE CASCADE,
  subject_type TEXT NOT NULL,
  subject_id   TEXT NOT NULL,
  UNIQUE(task_id, subject_type, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_project_task_executors_subject
  ON project_task_executors(subject_type, subject_id);

-- ``parent_comment_id`` carries no FK on purpose: cross-dialect ON DELETE
-- behaviour and the existing hard-delete comment path would make a cascade
-- ambiguous; readers tolerate a dangling reference (PLAN §B3.1).
ALTER TABLE project_comments ADD COLUMN parent_comment_id TEXT;
ALTER TABLE project_comments ADD COLUMN quote_excerpt TEXT;
CREATE INDEX IF NOT EXISTS idx_project_comments_parent ON project_comments(parent_comment_id);

UPDATE _schema_version SET version = 27;
