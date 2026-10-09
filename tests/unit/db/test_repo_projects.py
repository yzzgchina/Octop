"""Project domain repos — projects, members, and their tasks.

Repo layer only: SQL shape, id allocation, patch semantics (``UNSET`` = leave
alone, explicit ``None`` = clear), and the resource-table convention
(integer ``pk`` + public string id).
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tests.support.postgresql import requires_postgresql

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import PostgresPool, SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.project_content import ProjectCommentRepo
from octop.infra.db.repos.project_tasks import (
    ALLOCATE_TASK_SEQ_SQL,
    TASK_STATUSES,
    ProjectTaskRepo,
    task_number_text,
)
from octop.infra.db.repos.projects import (
    MEMBER_SUBJECT_AGENT,
    MEMBER_SUBJECT_USER,
    PROJECT_ROLES,
    PROJECT_STATUSES,
    ProjectMemberRepo,
    ProjectRepo,
    project_memory_namespace,
)
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def owner(db: SqlitePool) -> int:
    return UserRepo(db).create(username="owner", password_hash="h", role="user")


@pytest.fixture
def other(db: SqlitePool) -> int:
    return UserRepo(db).create(username="other", password_hash="h", role="user")


# ── constants mirror the migration ───────────────────────────────────────────


def test_public_constants_match_the_ddl_comments():
    assert PROJECT_ROLES == ("owner", "admin", "member", "viewer")
    assert PROJECT_STATUSES == ("draft", "active", "paused", "completed", "cancelled", "archived")
    assert TASK_STATUSES == (
        "planning",
        "todo",
        "doing",
        "review",
        "done",
        "blocked",
        "cancelled",
    )


def test_memory_namespace_is_derived_from_the_project_id():
    assert project_memory_namespace("AB12CD") == "project_AB12CD"


# ── ProjectRepo ──────────────────────────────────────────────────────────────


def test_create_derives_memory_namespace_and_defaults(db: SqlitePool, owner: int):
    row = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    assert row.id
    assert row.pk > 0
    assert row.name == "Alpha"
    assert row.goal == ""
    assert row.status == "draft"
    assert row.kb_id is None
    assert row.inject_version == 0
    assert row.memory_namespace == f"project_{row.id}"
    assert row.created_at == row.updated_at


def test_create_allocates_distinct_ids(db: SqlitePool, owner: int):
    repo = ProjectRepo(db)
    ids = {repo.create(owner_user_id=owner, name=f"P{i}").id for i in range(20)}
    assert len(ids) == 20


def test_get_returns_none_for_unknown_id(db: SqlitePool):
    assert ProjectRepo(db).get("nope") is None


def test_list_by_owner_is_scoped(db: SqlitePool, owner: int, other: int):
    repo = ProjectRepo(db)
    mine = repo.create(owner_user_id=owner, name="Mine")
    repo.create(owner_user_id=other, name="Theirs")
    listed = repo.list_by_owner(owner)
    assert [p.id for p in listed] == [mine.id]


def test_list_for_user_includes_owned_and_member_projects(db: SqlitePool, owner: int, other: int):
    projects = ProjectRepo(db)
    members = ProjectMemberRepo(db)
    owned = projects.create(owner_user_id=owner, name="Owned")
    invited = projects.create(owner_user_id=other, name="Invited")
    hidden = projects.create(owner_user_id=other, name="Hidden")
    members.add(
        project_id=invited.id,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(owner),
        role="member",
    )
    ids = {p.id for p in projects.list_for_user(owner)}
    assert ids == {owned.id, invited.id}
    assert hidden.id not in ids


def test_update_patches_only_named_fields(db: SqlitePool, owner: int):
    repo = ProjectRepo(db)
    row = repo.create(owner_user_id=owner, name="Alpha", goal="g")
    patched = repo.update(row.id, name="Beta")
    assert patched is not None
    assert patched.name == "Beta"
    assert patched.goal == "g"  # untouched
    assert patched.status == "draft"


def test_update_distinguishes_omit_from_explicit_none(db: SqlitePool, owner: int):
    repo = ProjectRepo(db)
    row = repo.create(owner_user_id=owner, name="Alpha", due_at=1234)

    omitted = repo.update(row.id, name="Beta")
    assert omitted is not None and omitted.due_at == 1234  # omitted -> kept

    cleared = repo.update(row.id, due_at=None)
    assert cleared is not None and cleared.due_at is None  # explicit None -> cleared


def test_update_with_no_fields_is_a_read(db: SqlitePool, owner: int):
    repo = ProjectRepo(db)
    row = repo.create(owner_user_id=owner, name="Alpha")
    assert repo.update(row.id) == row


def test_set_kb_id_roundtrips(db: SqlitePool, owner: int):
    repo = ProjectRepo(db)
    row = repo.create(owner_user_id=owner, name="Alpha")
    repo.set_kb_id(row.id, "kb1")
    got = repo.get(row.id)
    assert got is not None and got.kb_id == "kb1"
    repo.set_kb_id(row.id, None)
    got = repo.get(row.id)
    assert got is not None and got.kb_id is None


def test_bump_inject_version_is_monotonic(db: SqlitePool, owner: int):
    repo = ProjectRepo(db)
    row = repo.create(owner_user_id=owner, name="Alpha")
    assert repo.bump_inject_version(row.id) == 1
    assert repo.bump_inject_version(row.id) == 2


def test_delete_cascades_to_children(db: SqlitePool, owner: int):
    projects = ProjectRepo(db)
    members = ProjectMemberRepo(db)
    tasks = ProjectTaskRepo(db)
    row = projects.create(owner_user_id=owner, name="Alpha")
    members.add(
        project_id=row.id,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(owner),
        role="owner",
    )
    tasks.create(project_id=row.id, title="T", created_by=owner)

    assert projects.delete(row.id) is True

    assert projects.get(row.id) is None
    assert members.list_by_project(row.id) == []
    assert tasks.list_by_project(row.id) == []


def test_delete_unknown_returns_false(db: SqlitePool):
    assert ProjectRepo(db).delete("nope") is False


# ── ProjectMemberRepo ────────────────────────────────────────────────────────


def test_add_user_member_fills_user_id_from_subject_id(db: SqlitePool, owner: int):
    projects = ProjectRepo(db)
    project = projects.create(owner_user_id=owner, name="Alpha")
    member = ProjectMemberRepo(db).add(
        project_id=project.id,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(owner),
    )
    assert member.role == "member"
    assert member.user_id == owner


def test_add_agent_member_has_no_user_id(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    member = ProjectMemberRepo(db).add(
        project_id=project.id,
        subject_type=MEMBER_SUBJECT_AGENT,
        subject_id="agent-1",
        role="member",
    )
    assert member.user_id is None
    assert member.subject_type == "agent"


def test_add_is_upsert_so_reinviting_changes_the_role(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    members = ProjectMemberRepo(db)
    members.add(project_id=project.id, subject_type=MEMBER_SUBJECT_USER, subject_id=str(owner))
    members.add(
        project_id=project.id,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(owner),
        role="admin",
    )
    assert members.role_of(project.id, MEMBER_SUBJECT_USER, str(owner)) == "admin"
    assert members.count(project.id) == 1


def test_same_subject_can_join_two_projects(db: SqlitePool, owner: int):
    projects = ProjectRepo(db)
    members = ProjectMemberRepo(db)
    a = projects.create(owner_user_id=owner, name="A")
    b = projects.create(owner_user_id=owner, name="B")
    for project in (a, b):
        members.add(
            project_id=project.id,
            subject_type=MEMBER_SUBJECT_USER,
            subject_id=str(owner),
        )
    assert set(members.list_project_ids_for_subject(MEMBER_SUBJECT_USER, str(owner))) == {
        a.id,
        b.id,
    }


def test_remove_member(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    members = ProjectMemberRepo(db)
    members.add(project_id=project.id, subject_type=MEMBER_SUBJECT_USER, subject_id=str(owner))
    assert members.remove(project.id, MEMBER_SUBJECT_USER, str(owner)) is True
    assert members.remove(project.id, MEMBER_SUBJECT_USER, str(owner)) is False
    assert members.role_of(project.id, MEMBER_SUBJECT_USER, str(owner)) is None


def test_role_of_unknown_subject_is_none(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    assert ProjectMemberRepo(db).role_of(project.id, MEMBER_SUBJECT_USER, "999") is None


# ── ProjectTaskRepo ──────────────────────────────────────────────────────────


def test_task_sort_order_auto_increments_per_project(db: SqlitePool, owner: int):
    projects = ProjectRepo(db)
    tasks = ProjectTaskRepo(db)
    a = projects.create(owner_user_id=owner, name="A")
    b = projects.create(owner_user_id=owner, name="B")

    first = tasks.create(project_id=a.id, title="1", created_by=owner)
    second = tasks.create(project_id=a.id, title="2", created_by=owner)
    other_first = tasks.create(project_id=b.id, title="1", created_by=owner)

    assert (first.sort_order, second.sort_order) == (0, 1)
    assert other_first.sort_order == 0  # numbering is per project


def test_task_list_by_project_orders_by_sort_order(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    tasks.create(project_id=project.id, title="third", created_by=owner, sort_order=9)
    tasks.create(project_id=project.id, title="first", created_by=owner, sort_order=1)
    titles = [t.title for t in tasks.list_by_project(project.id)]
    assert titles == ["first", "third"]


def test_task_list_filters_by_status(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    tasks.create(project_id=project.id, title="todo one", created_by=owner)
    done = tasks.create(project_id=project.id, title="done one", created_by=owner)
    tasks.update(done.id, status="done")

    assert [t.title for t in tasks.list_by_project(project.id)] == ["todo one", "done one"]
    assert [t.title for t in tasks.list_by_project(project.id, status="done")] == ["done one"]


def test_task_deps_round_trip_as_a_tuple(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner,
        deps=["a", "b"],
    )
    assert task.deps == ("a", "b")

    updated = tasks.update(task.id, deps=["c"])
    assert updated is not None and updated.deps == ("c",)

    omitted = tasks.update(task.id, title="renamed")
    assert omitted is not None and omitted.deps == ("c",)


def test_task_update_distinguishes_omit_from_explicit_none(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner,
        assignee_type="agent",
        assignee_id="agent-1",
    )

    kept = tasks.update(task.id, title="renamed")
    assert kept is not None and kept.assignee_id == "agent-1"

    cleared = tasks.update(task.id, assignee_id=None)
    assert cleared is not None and cleared.assignee_id is None


def test_task_list_by_thread(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    # thread_id is a soft foreign key, so the thread has to exist first —
    # and threads.agent_id is a hard FK, hence the agent row.
    AgentRepo(db).create(agent_id="a1", user_id=owner, name="Agent 1")
    ThreadRepo(db).insert(
        thread_id="th1",
        agent_id="a1",
        user_id=owner,
        channel_type="dashboard",
        session_key="sk-th1",
    )
    linked = tasks.create(project_id=project.id, title="linked", created_by=owner, thread_id="th1")
    tasks.create(project_id=project.id, title="loose", created_by=owner)
    assert [t.id for t in tasks.list_by_thread("th1")] == [linked.id]


def test_task_thread_must_exist(db: SqlitePool, owner: int):
    """Soft foreign key: a bogus thread_id is rejected, not silently stored."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    with pytest.raises(OctopError) as err:
        ProjectTaskRepo(db).create(
            project_id=project.id, title="T", created_by=owner, thread_id="nope"
        )
    assert err.value.code is ErrorCode.PROJECT_TASK_THREAD_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500


def test_task_parent_must_be_in_the_same_project(db: SqlitePool, owner: int):
    projects = ProjectRepo(db)
    tasks = ProjectTaskRepo(db)
    a = projects.create(owner_user_id=owner, name="A")
    b = projects.create(owner_user_id=owner, name="B")
    foreign = tasks.create(project_id=b.id, title="elsewhere", created_by=owner)

    with pytest.raises(OctopError) as err:
        tasks.create(project_id=a.id, title="child", created_by=owner, parent_id=foreign.id)
    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500


def test_task_delete(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(project_id=project.id, title="T", created_by=owner)
    assert tasks.delete(task.id) is True
    assert tasks.delete(task.id) is False
    assert tasks.get(task.id) is None


# ── ProjectTaskRepo · 025 run columns and attempt tokens ─────────────────────


def test_task_run_columns_round_trip_the_four_json_arrays(db: SqlitePool, owner: int):
    """acceptance / in_scope / verify / changed_paths encode like ``deps``."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner,
        kind="review",
        acceptance=["a", "b"],
        in_scope=["src/x.py"],
        verify=["uv run pytest -q"],
        phase="design",
    )

    assert task.kind == "review"
    assert task.acceptance == ("a", "b")
    assert task.in_scope == ("src/x.py",)
    assert task.verify == ("uv run pytest -q",)
    assert task.changed_paths == ()
    assert task.phase == "design"

    patched = tasks.update(
        task.id,
        acceptance=["c"],
        changed_paths=["src/x.py", "src/y.py"],
        round=2,
        verdict="needs_revision",
        started_at=123,
    )
    assert patched is not None
    assert patched.acceptance == ("c",)
    assert patched.changed_paths == ("src/x.py", "src/y.py")
    assert patched.in_scope == ("src/x.py",)  # omitted stays put
    assert (patched.round, patched.verdict, patched.started_at) == (2, "needs_revision", 123)

    # Idempotent: re-dumping what was parsed yields the same tuple.
    again = tasks.update(task.id, acceptance=list(patched.acceptance))
    assert again is not None
    assert again.acceptance == patched.acceptance


def test_task_run_columns_default_to_the_migration_defaults(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    task = ProjectTaskRepo(db).create(project_id=project.id, title="T", created_by=owner)

    assert task.kind == "work"
    assert task.acceptance == task.in_scope == task.verify == task.changed_paths == ()
    assert (task.round, task.attempt) == (1, 0)
    assert (task.verdict, task.attempt_id, task.claimed_by, task.claimed_at) == (
        None,
        None,
        None,
        None,
    )
    assert (task.started_at, task.phase) == (None, None)


def test_task_json_columns_never_raise_on_a_legacy_row(db: SqlitePool, owner: int):
    """``'[]'`` and unparsable text both decode to empty — the reader never crashes."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(project_id=project.id, title="T", created_by=owner)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE project_tasks SET acceptance = ?, changed_paths = ? WHERE task_id = ?",
            ("not json", "[]", task.id),
        )

    row = tasks.get(task.id)
    assert row is not None
    assert row.acceptance == ()  # unparsable ⇒ empty
    assert row.changed_paths == ()  # the migration default ⇒ empty


def test_claim_is_a_compare_and_set_so_only_one_owner_wins(db: SqlitePool, owner: int):
    """Two claims that gated on the same attempt cannot both write (PLAN B26)."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(project_id=project.id, title="T", created_by=owner)
    gated = tasks.get(task.id)
    assert gated is not None and gated.attempt_id is None

    winner = tasks.claim(task.id, claimed_by="be", expected_attempt_id=gated.attempt_id)
    assert winner is not None
    assert winner.claimed_by == "be"
    assert winner.claimed_at is not None
    assert winner.attempt == 1
    assert winner.attempt_id  # the token later writes must present

    # The loser still carries the attempt it gated on, so its UPDATE matches no
    # row — the owner it would have twinned is untouched.
    loser = tasks.claim(task.id, claimed_by="fe", expected_attempt_id=gated.attempt_id)
    assert loser is None
    after = tasks.get(task.id)
    assert after is not None
    assert (after.claimed_by, after.attempt, after.attempt_id) == ("be", 1, winner.attempt_id)


def test_claim_accepts_the_dispatch_token_and_a_transfer_bumps_the_attempt(
    db: SqlitePool, owner: int
):
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(project_id=project.id, title="T", created_by=owner)

    first = tasks.claim(task.id, claimed_by="be", expected_attempt_id=None, attempt_id="att-1")
    assert first is not None and first.attempt_id == "att-1"

    # Transfer: the caller re-read, so the new attempt is legitimate.
    assert first is not None
    moved = tasks.claim(task.id, claimed_by="fe", expected_attempt_id=first.attempt_id)
    assert moved is not None
    assert (moved.claimed_by, moved.attempt) == ("fe", 2)
    assert moved.attempt_id != "att-1"

    assert tasks.claim("nope", claimed_by="be", expected_attempt_id=None) is None


def test_claim_writes_nothing_when_the_attempt_moved(db: SqlitePool, owner: int):
    """The guard is on the attempt, so an owner that never landed loses cleanly."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(project_id=project.id, title="T", created_by=owner)

    assert tasks.claim(task.id, claimed_by="be", expected_attempt_id=None) is not None
    assert tasks.claim(task.id, claimed_by="fe", expected_attempt_id=None) is None
    row = tasks.get(task.id)
    assert row is not None and row.claimed_by == "be"


def test_report_refuses_a_stale_attempt_and_leaves_the_row_alone(db: SqlitePool, owner: int):
    """PLAN G8: a superseded attemptId is refused, a current one lands."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(project_id=project.id, title="T", created_by=owner)
    claimed = tasks.claim(task.id, claimed_by="be", expected_attempt_id=None)
    assert claimed is not None and claimed.attempt_id is not None
    token = claimed.attempt_id

    stale = tasks.report(
        task.id,
        attempt_id="att-old",
        verdict="pass",
        changed_paths=["src/secret.py"],
        round=2,
    )
    assert stale is None
    untouched = tasks.get(task.id)
    assert untouched is not None
    assert (untouched.verdict, untouched.changed_paths, untouched.round) == (None, (), 1)

    reported = tasks.report(
        task.id,
        attempt_id=token,
        verdict="needs_revision",
        changed_paths=["src/x.py"],
        round=2,
        phase="implement",
    )
    assert reported is not None
    assert reported.verdict == "needs_revision"
    assert reported.changed_paths == ("src/x.py",)
    assert (reported.round, reported.phase) == (2, "implement")
    assert reported.attempt_id == token  # the token itself is not rewritten here

    # Same patch semantics as `update`: omitted fields stay put.
    kept = tasks.report(task.id, attempt_id=token)
    assert kept is not None and kept.verdict == "needs_revision"

    assert tasks.report("nope", attempt_id=token) is None


# ── task numbering (PLAN §B1.2 / AC-16) ──────────────────────────────────────

TASK_NUMBER_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{3,4}$")

PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


def _child_row_counts(db: SqlitePool, task_id: str) -> tuple[int, int]:
    with db.connect() as conn:
        owners = conn.execute(
            "SELECT COUNT(*) AS c FROM project_task_owners WHERE task_id = ?", (task_id,)
        ).fetchone()
        executors = conn.execute(
            "SELECT COUNT(*) AS c FROM project_task_executors WHERE task_id = ?", (task_id,)
        ).fetchone()
    return int(owners["c"]), int(executors["c"])


def _child_row_ids(db: SqlitePool, table: str, task_id: str) -> list[int]:
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
    return [int(r["id"]) for r in rows]


def test_project_create_derives_the_task_prefix(db: SqlitePool, owner: int):
    projects = ProjectRepo(db)
    assert projects.create(owner_user_id=owner, name="Alpha").task_prefix == "ALPHA"
    # A pure-CJK name has no ASCII core to keep, so the id carries the prefix.
    cjk = projects.create(owner_user_id=owner, name="项目甲")
    assert PREFIX_PATTERN.match(cjk.task_prefix)


def test_task_prefix_is_patchable_and_never_renumbers(db: SqlitePool, owner: int):
    """PLAN §B1.3: a prefix change only affects numbers allocated afterwards."""
    projects = ProjectRepo(db)
    tasks = ProjectTaskRepo(db)
    project = projects.create(owner_user_id=owner, name="Alpha")

    first = tasks.create(project_id=project.id, title="1", created_by=owner)
    assert first.task_number == "ALPHA-001"
    renamed = projects.update(project.id, task_prefix="BETA")
    assert renamed is not None and renamed.task_prefix == "BETA"
    second = tasks.create(project_id=project.id, title="2", created_by=owner)
    assert second.task_number == "BETA-002"

    refetched = tasks.get(first.id)
    assert refetched is not None and refetched.task_number == "ALPHA-001"


def test_numbering_is_per_project(db: SqlitePool, owner: int):
    projects = ProjectRepo(db)
    tasks = ProjectTaskRepo(db)
    a = projects.create(owner_user_id=owner, name="Alpha")
    b = projects.create(owner_user_id=owner, name="Bee")
    assert tasks.create(project_id=a.id, title="1", created_by=owner).task_number == "ALPHA-001"
    assert tasks.create(project_id=b.id, title="1", created_by=owner).task_number == "BEE-001"
    assert tasks.create(project_id=a.id, title="2", created_by=owner).task_number == "ALPHA-002"


def test_single_character_name_falls_back_to_the_project_id(db: SqlitePool, owner: int):
    """PLAN §B1.1 step 4: ``len(core) < 2`` ⇒ the id carries the prefix."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="A")
    assert PREFIX_PATTERN.match(project.task_prefix)
    assert project.task_prefix != "A"


def test_task_number_widens_to_four_digits_from_1000():
    assert task_number_text("ALPHA", 1) == "ALPHA-001"
    assert task_number_text("ALPHA", 999) == "ALPHA-999"
    assert task_number_text("ALPHA", 1000) == "ALPHA-1000"
    assert TASK_NUMBER_PATTERN.match(task_number_text("ALPHA", 1000))


def test_allocation_is_one_upsert_statement():
    """AC-2: the statement *is* the allocator — no read-modify-write, no lock."""
    sql = ALLOCATE_TASK_SEQ_SQL
    assert sql.startswith("INSERT INTO project_task_counters(project_id, last_seq) VALUES (?, 1)")
    assert (
        "ON CONFLICT(project_id) DO UPDATE SET last_seq = project_task_counters.last_seq + 1" in sql
    )
    assert sql.endswith("RETURNING last_seq")
    upper = sql.upper()
    assert "SELECT" not in upper, "MAX(...)+1 style read-modify-write is forbidden"
    assert "FOR UPDATE" not in upper


def test_the_counter_row_is_the_sequence_source(db: SqlitePool, owner: int):
    """AC-2 at the database level: the sequence comes from the counter row.

    A ``SELECT MAX(...) + 1`` implementation never writes
    ``project_task_counters``, so this assertion is what turns red when the
    forbidden shape is reintroduced (it cannot be caught by a concurrency case on
    SQLite, whose single shared connection serializes the writers anyway).
    """
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    tasks = ProjectTaskRepo(db)
    for i in range(3):
        tasks.create(project_id=project.id, title=f"T{i}", created_by=owner)

    with db.connect() as conn:
        row = conn.execute(
            "SELECT last_seq FROM project_task_counters WHERE project_id = ?", (project.id,)
        ).fetchone()
    assert row is not None, "allocation must seed the counter row"
    assert int(row["last_seq"]) == 3


def test_concurrent_allocation_is_unique_and_contiguous(db: SqlitePool, owner: int):
    """AC-3: N parallel creators ⇒ no duplicate, and the run is 1..N."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    tasks = ProjectTaskRepo(db)
    count = 12
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(tasks.create, project_id=project.id, title=f"T{i}", created_by=owner)
            for i in range(count)
        ]
        created = [f.result() for f in futures]

    numbers = [t.task_number for t in created]
    assert all(n is not None and TASK_NUMBER_PATTERN.match(n) for n in numbers), numbers
    assert len(set(numbers)) == count
    sequences = sorted(int(str(n).rsplit("-", 1)[1]) for n in numbers)
    assert sequences == list(range(1, count + 1))


def test_owners_and_executors_keep_array_order_and_replace_whole(
    db: SqlitePool, owner: int, other: int
):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner,
        owners=[other, owner],
        executors=["team:t1", "agent:a1"],
    )
    assert task.owners == (other, owner)
    assert task.executors == ("team:t1", "agent:a1")
    # The mirror is derived with executors[0] first, never with the owner.
    assert (task.assignee_type, task.assignee_id) == ("team", "t1")

    updated = tasks.update(task.id, owners=[owner], executors=["agent:a9"])
    assert updated is not None
    assert updated.owners == (owner,)
    assert updated.executors == ("agent:a9",)
    assert (updated.assignee_type, updated.assignee_id) == ("agent", "a9")
    assert len(_child_row_ids(db, "project_task_owners", task.id)) == 1
    assert len(_child_row_ids(db, "project_task_executors", task.id)) == 1

    # Re-sending the same arrays reproduces the same rows (idempotent), and the
    # replacement keeps the client order rather than appending.
    again = tasks.update(task.id, owners=[other, owner], executors=["team:t1", "agent:a1"])
    assert again is not None
    assert (again.owners, again.executors) == ((other, owner), ("team:t1", "agent:a1"))
    with db.connect() as conn:
        stored = [
            int(r["user_id"])
            for r in conn.execute(
                "SELECT user_id FROM project_task_owners WHERE task_id = ? ORDER BY id",
                (task.id,),
            ).fetchall()
        ]
    assert stored == [other, owner]


def test_duplicate_elements_are_deduped_with_unique_as_the_db_backstop(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner,
        owners=[owner, owner],
        executors=["agent:a1", "agent:a1"],
    )
    assert task.owners == (owner,)
    assert task.executors == ("agent:a1",)

    # Even if the application layer were bypassed, the database refuses a
    # duplicate row (PLAN §B2.3 ②: the UNIQUE constraints are the backstop).
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_owners(task_id, user_id) VALUES (?, ?)",
            (task.id, owner),
        )
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_executors(task_id, subject_type, subject_id)"
            " VALUES (?, ?, ?)",
            (task.id, "agent", "a1"),
        )


def test_task_list_reads_the_child_tables_once_each(db: SqlitePool, owner: int):
    """No N+1: one query per child table for the whole page, not one per task."""
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    tasks = ProjectTaskRepo(db)
    for i in range(5):
        tasks.create(
            project_id=project.id,
            title=f"T{i}",
            created_by=owner,
            owners=[owner],
            executors=["agent:a1"],
        )

    statements: list[str] = []
    db._conn.set_trace_callback(statements.append)  # type: ignore[attr-defined]
    try:
        rows = tasks.list_by_project(project.id)
    finally:
        db._conn.set_trace_callback(None)  # type: ignore[attr-defined]

    assert [r.owners for r in rows] == [(owner,)] * 5
    assert [r.executors for r in rows] == [("agent:a1",)] * 5
    child_queries = [
        s for s in statements if "project_task_owners" in s or "project_task_executors" in s
    ]
    assert len(child_queries) == 2, child_queries


# ── cascade delete of the two child tables (PLAN §B2.3 ③) ────────────────────


def test_delete_task_cascades_to_both_child_tables(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    tasks = ProjectTaskRepo(db)
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner,
        owners=[owner],
        executors=["team:t1"],
    )
    assert _child_row_counts(db, task.id) == (1, 1)

    assert tasks.delete(task.id) is True
    assert _child_row_counts(db, task.id) == (0, 0)


def _pg_conninfo() -> str:
    return os.environ["OCTOP_TEST_DATABASE_URL"]


def _reset_public_schema(pool: PostgresPool) -> None:
    with pool.connect() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
        conn.execute("COMMIT")


@requires_postgresql
@pytest.mark.postgresql
def test_delete_task_cascades_to_both_child_tables_postgresql():
    """The same cascade on PostgreSQL (gated: needs ``OCTOP_TEST_DATABASE_URL``)."""
    pool = PostgresPool(_pg_conninfo())
    try:
        _reset_public_schema(pool)
        run_migrations(pool)
        owner = UserRepo(pool).create(
            username=f"pg_owner_{uuid.uuid4().hex[:8]}", password_hash="h", role="user"
        )
        project = ProjectRepo(pool).create(owner_user_id=owner, name="Alpha")
        tasks = ProjectTaskRepo(pool)
        task = tasks.create(
            project_id=project.id,
            title="T",
            created_by=owner,
            owners=[owner],
            executors=["team:t1"],
        )
        assert task.task_number is not None and TASK_NUMBER_PATTERN.match(task.task_number)
        assert _child_row_counts(pool, task.id) == (1, 1)  # type: ignore[arg-type]

        assert tasks.delete(task.id) is True
        assert _child_row_counts(pool, task.id) == (0, 0)  # type: ignore[arg-type]
    finally:
        pool.close()


# ── comment quotes (PLAN §B3.1) ──────────────────────────────────────────────


def test_comment_quote_columns_round_trip(db: SqlitePool, owner: int):
    project = ProjectRepo(db).create(owner_user_id=owner, name="Alpha")
    comments = ProjectCommentRepo(db)
    root = comments.create(
        project_id=project.id, author_type="user", author_id=str(owner), body="first"
    )
    assert (root.parent_comment_id, root.quote_excerpt) == (None, None)

    reply = comments.create(
        project_id=project.id,
        author_type="user",
        author_id=str(owner),
        body="second",
        parent_comment_id=root.id,
        quote_excerpt="first",
    )
    fetched = comments.get(reply.id)
    assert fetched is not None
    assert (fetched.parent_comment_id, fetched.quote_excerpt) == (root.id, "first")
    assert [
        (c.parent_comment_id, c.quote_excerpt) for c in comments.list_by_project(project.id)
    ] == [(None, None), (root.id, "first")]
