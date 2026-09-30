"""Schema v27: task numbering, multi-subject assignment, comment quotes.

One migration pair (``027_project_task_numbering``) carries the five DDL groups
of PLAN §B1.4 in a single watermark step: ``projects.task_prefix`` (a),
``project_tasks.task_number`` + ``project_task_counters`` (b),
``project_task_owners`` / ``project_task_executors`` (c),
``project_comments.parent_comment_id`` / ``quote_excerpt`` (d), and the
idempotent data backfill (e).

Unlike 023/024, v27 DOES add a ``migrate.py`` helper -- the backfill has no
cross-dialect SQL form. The last test therefore asserts the positive (the helper
exists and a re-run writes 0 rows) instead of the 023/024 negative.

Uses ``tmp_path`` throughout so the suite runs on Windows and POSIX alike.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from octop.infra.db import migrate
from octop.infra.db.migrate import _backfill_project_task_numbering, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo

SQLITE_FILE = "027_project_task_numbering.sql"
PG_FILE = "027_project_task_numbering.pg.sql"
NEW_TABLES = ("project_task_counters", "project_task_owners", "project_task_executors")
NEW_INDEXES = {
    "idx_project_tasks_number": "project_tasks",
    "idx_project_task_owners_user": "project_task_owners",
    "idx_project_task_executors_subject": "project_task_executors",
    "idx_project_comments_parent": "project_comments",
}
# SPEC.md 「编号形态」 authority line, quoted verbatim by PLAN §B1.1/§B1.2.
TASK_NUMBER_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{3,4}$")


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"


def _version(db: SqlitePool) -> int:
    with db.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()["version"])


def _columns(db: SqlitePool, table: str) -> dict[str, sqlite3.Row]:
    with db.connect() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]): row for row in rows}


def _table_names(db: SqlitePool) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _index_names(db: SqlitePool, table: str) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _count(db: SqlitePool, table: str) -> int:
    with db.connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def _seed_project(
    db: SqlitePool, project_id: str = "prj_1", name: str = "Demo", task_id: str = "task_1"
) -> int:
    uid = UserRepo(db).create(username=f"owner_{project_id}", password_hash="h", role="user")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO projects (project_id, name, owner_user_id, memory_namespace,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, name, uid, f"project_{project_id}", 1, 1),
        )
        conn.execute(
            "INSERT INTO project_tasks (task_id, project_id, title, created_by,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, project_id, "Task", uid, 1, 1),
        )
    return uid


# ── the watermark ────────────────────────────────────────────────────────────


def test_migration_027_is_the_applied_watermark(db: SqlitePool):
    assert _version(db) == 27, "one migration pair is one watermark step (§B2.3.3)"


def test_migration_027_is_idempotent(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_migrations(pool)  # must not raise: guarded DDL + a guarded helper
    assert _version(pool) == 27
    assert set(NEW_TABLES) <= _table_names(pool)


# ── columns, tables, indexes ─────────────────────────────────────────────────


def test_migration_027_adds_the_four_columns(db: SqlitePool):
    prefix = _columns(db, "projects")["task_prefix"]
    assert "TEXT" in str(prefix["type"]).upper()
    assert prefix["notnull"] == 1, "SQLite requires a non-null default on ADD COLUMN"
    assert prefix["dflt_value"] == "''", "the default is the empty prefix, backfilled later"

    number = _columns(db, "project_tasks")["task_number"]
    assert "TEXT" in str(number["type"]).upper()
    assert number["notnull"] == 0, "NULL = not numbered yet"

    comments = _columns(db, "project_comments")
    assert "TEXT" in str(comments["parent_comment_id"]["type"]).upper()
    assert "TEXT" in str(comments["quote_excerpt"]["type"]).upper()


def test_migration_027_creates_the_new_tables_and_indexes(db: SqlitePool):
    missing = sorted(set(NEW_TABLES) - _table_names(db))
    assert missing == [], f"missing v27 tables: {missing}"

    for index, table in NEW_INDEXES.items():
        assert index in _index_names(db, table), f"{index} missing on {table}"


def test_migration_027_does_not_create_the_withdrawn_single_value_columns(db: SqlitePool):
    # PLAN §B2.1b: ``owner_user_id`` / ``executor`` on project_tasks are withdrawn.
    task_columns = set(_columns(db, "project_tasks"))
    assert "owner_user_id" not in task_columns
    assert "executor" not in task_columns


# ── constraints and cascades ─────────────────────────────────────────────────


def test_owner_unique_is_enforced(db: SqlitePool):
    uid = _seed_project(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_owners(task_id, user_id) VALUES ('task_1', ?)", (uid,)
        )
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_owners(task_id, user_id) VALUES ('task_1', ?)", (uid,)
        )


def test_executor_unique_is_enforced_per_subject(db: SqlitePool):
    _seed_project(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_executors(task_id, subject_type, subject_id)"
            " VALUES ('task_1', 'agent', 'agent_1')"
        )
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_executors(task_id, subject_type, subject_id)"
            " VALUES ('task_1', 'agent', 'agent_1')"
        )
    # The same id under another subject type is a different subject: the
    # UNIQUE is on the triple, not on (task_id, subject_id).
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_executors(task_id, subject_type, subject_id)"
            " VALUES ('task_1', 'team', 'agent_1')"
        )
    assert _count(db, "project_task_executors") == 2


def test_deleting_a_task_cascades_to_both_child_tables(db: SqlitePool):
    uid = _seed_project(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_owners(task_id, user_id) VALUES ('task_1', ?)", (uid,)
        )
        conn.execute(
            "INSERT INTO project_task_executors(task_id, subject_type, subject_id)"
            " VALUES ('task_1', 'team', 'team_1')"
        )
        conn.execute("DELETE FROM project_tasks WHERE task_id = 'task_1'")
    assert _count(db, "project_task_owners") == 0
    assert _count(db, "project_task_executors") == 0


# ── the v26 -> v27 backfill ──────────────────────────────────────────────────


def _insert_task(
    db: SqlitePool,
    task_id: str,
    uid: int,
    sort_order: int,
    assignee: tuple[str, str] | None,
    project_id: str = "prj_1",
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_tasks (task_id, project_id, title, assignee_type,"
            " assignee_id, sort_order, created_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)",
            (
                task_id,
                project_id,
                task_id,
                assignee[0] if assignee else None,
                assignee[1] if assignee else None,
                sort_order,
                uid,
            ),
        )


def _number_tasks(db: SqlitePool, project_id: str = "prj_1") -> dict[str, str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT task_id, task_number FROM project_tasks WHERE project_id = ?", (project_id,)
        ).fetchall()
    return {str(row["task_id"]): str(row["task_number"]) for row in rows}


def test_backfill_numbers_legacy_tasks_and_maps_the_single_subject(tmp_path: Path, monkeypatch):
    """The versioned path: stop at 024, seed v26-era rows, then apply 027."""
    pool = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [item for item in real_discover(dialect) if item[0] <= 24],
    )
    run_migrations(pool)
    assert _version(pool) == 24, "the pre-027 baseline the backfill has to upgrade"

    uid = _seed_project(pool)
    # Inserted out of order: the numbers must follow (sort_order, created_at, id).
    _insert_task(pool, "task_c", uid, sort_order=2, assignee=("agent", "agent_9"))
    _insert_task(pool, "task_a", uid, sort_order=0, assignee=("user", str(uid)))
    _insert_task(pool, "task_d", uid, sort_order=3, assignee=None)
    _insert_task(pool, "task_b", uid, sort_order=1, assignee=("user", "not-a-number"))
    # A CJK-only project name falls back to the project id (§B1.1 step 4).
    uid_2 = _seed_project(pool, project_id="prj_2", name="定价改版", task_id="task_e")
    assert uid_2 != uid

    monkeypatch.undo()
    run_migrations(pool)
    assert _version(pool) == 27

    with pool.connect() as conn:
        prefixes = {
            str(row["project_id"]): str(row["task_prefix"])
            for row in conn.execute("SELECT project_id, task_prefix FROM projects").fetchall()
        }
    assert prefixes == {"prj_1": "DEMO", "prj_2": "PRJ2"}

    assert _number_tasks(pool) == {
        "task_1": "DEMO-001",
        "task_a": "DEMO-002",
        "task_b": "DEMO-003",
        "task_c": "DEMO-004",
        "task_d": "DEMO-005",
    }
    assert all(TASK_NUMBER_RE.match(number) for number in _number_tasks(pool).values())

    with pool.connect() as conn:
        counter = conn.execute(
            "SELECT last_seq FROM project_task_counters WHERE project_id = 'prj_1'"
        ).fetchone()
    assert counter is not None and int(counter["last_seq"]) == 5

    # 'user' with a real id -> one owner; 'agent'/'team' -> one executor;
    # a non-numeric 'user' id -> neither side (PLAN §B2.3.2).
    with pool.connect() as conn:
        owners = [
            (str(row["task_id"]), int(row["user_id"]))
            for row in conn.execute(
                "SELECT task_id, user_id FROM project_task_owners ORDER BY task_id"
            ).fetchall()
        ]
        executors = [
            (str(row["task_id"]), str(row["subject_type"]), str(row["subject_id"]))
            for row in conn.execute(
                "SELECT task_id, subject_type, subject_id FROM project_task_executors"
                " ORDER BY task_id"
            ).fetchall()
        ]
    assert owners == [("task_a", uid)]
    assert executors == [("task_c", "agent", "agent_9")]


def test_backfill_helper_exists_and_a_rerun_writes_nothing(tmp_path: Path, monkeypatch):
    """The 023/024 negative does not apply: 027 must ship a migrate.py helper."""
    migrate_src = (Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrate.py").read_text(
        encoding="utf-8"
    )
    assert "_backfill_project_task_numbering" in migrate_src
    assert "INSERT INTO audit_log" not in migrate_src, (
        "an idempotent rebuild is not an exception report (§B2.3.2)"
    )

    pool = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [item for item in real_discover(dialect) if item[0] <= 24],
    )
    run_migrations(pool)
    uid = _seed_project(pool)
    _insert_task(pool, "task_a", uid, sort_order=0, assignee=("user", str(uid)))
    monkeypatch.undo()
    run_migrations(pool)

    counts = _backfill_project_task_numbering(pool)
    assert counts == dict.fromkeys(counts, 0), "a second pass writes and skips nothing"

    # Nothing was renumbered or duplicated either.
    assert _number_tasks(pool) == {"task_1": "DEMO-001", "task_a": "DEMO-002"}
    assert _count(pool, "project_task_owners") == 1
    assert _count(pool, "project_task_counters") == 1


# ── the migration pair itself ────────────────────────────────────────────────


def test_the_pair_is_self_contained_on_both_dialects():
    sqlite_sql = (_migrations_dir() / SQLITE_FILE).read_text(encoding="utf-8")
    pg_sql = (_migrations_dir() / PG_FILE).read_text(encoding="utf-8")

    assert "ALTER TABLE projects ADD COLUMN task_prefix TEXT NOT NULL DEFAULT '';" in sqlite_sql
    assert "ALTER TABLE project_tasks ADD COLUMN task_number TEXT;" in sqlite_sql
    assert "ALTER TABLE project_comments ADD COLUMN parent_comment_id TEXT;" in sqlite_sql
    assert "ALTER TABLE project_comments ADD COLUMN quote_excerpt TEXT;" in sqlite_sql
    assert "ADD COLUMN IF NOT EXISTS task_prefix TEXT NOT NULL DEFAULT '';" in pg_sql
    assert "ADD COLUMN IF NOT EXISTS task_number TEXT;" in pg_sql

    for table in NEW_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sqlite_sql
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in pg_sql
    for index in NEW_INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {index}" in sqlite_sql or (
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index}" in sqlite_sql
        )
        assert f"CREATE INDEX IF NOT EXISTS {index}" in pg_sql or (
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index}" in pg_sql
        )

    # The withdrawn single-value columns must not appear as DDL (they may be
    # named in the header comment as withdrawn).
    for text in (sqlite_sql, pg_sql):
        assert "ADD COLUMN owner_user_id" not in text
        assert "ADD COLUMN executor" not in text
        assert "UPDATE _schema_version SET version = 27;" in text

    for statement in pg_sql.split(";\n"):
        body = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body:
            assert "$$" not in body, f"a DO block would be torn apart by the splitter: {body}"
            assert body.upper().startswith(
                ("ALTER TABLE", "CREATE TABLE", "CREATE UNIQUE INDEX", "CREATE INDEX", "UPDATE")
            ), body
