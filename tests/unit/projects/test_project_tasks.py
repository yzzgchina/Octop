"""Project task CRUD, the task state machine, and timeline writes (T2.3).

``timeline_events`` is the M16 trace, so every task mutation is asserted to
leave exactly one well-formed row.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import UNSET
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.project_tasks import (
    TIMELINE_TASK_ASSIGNED,
    TIMELINE_TASK_CREATED,
    TIMELINE_TASK_DELETED,
    TIMELINE_TASK_STATUS_CHANGED,
    TIMELINE_TASK_UPDATED,
    AssigneeMapping,
    ProjectTaskRepo,
    TimelineRepo,
    actor_ref,
    normalize_assignees,
)
from octop.infra.db.repos.projects import MEMBER_SUBJECT_USER, ProjectMemberRepo, ProjectRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge import service as knowledge_service_module
from octop.infra.projects import service as project_service_module
from octop.infra.projects.service import PROJECT_READ, PROJECT_WRITE, ProjectService
from octop.infra.utils.paths import PathLayout


class Actor:
    def __init__(
        self, user_id: int, *, admin: bool = False, permissions: list[str] | None = None
    ) -> None:
        self.id = user_id
        self._admin = admin
        self.permissions = ["projects", "knowledge_bases"] if permissions is None else permissions

    @property
    def is_admin(self) -> bool:
        return self._admin


@pytest.fixture
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    monkeypatch.setattr(knowledge_service_module, "assert_knowledge_usable", lambda *_a: None)
    monkeypatch.setattr(
        project_service_module, "get_capability", lambda *_a, **_k: {"usable": True}
    )
    return SimpleNamespace(
        db=pool,
        project_repo=ProjectRepo(pool),
        project_member_repo=ProjectMemberRepo(pool),
        project_task_repo=ProjectTaskRepo(pool),
        timeline_repo=TimelineRepo(pool),
        knowledge_repo=KnowledgeRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        agent_repo=AgentRepo(pool),
        thread_repo=ThreadRepo(pool),
        paths=PathLayout.from_env(),
    )


@pytest.fixture
def service(services: SimpleNamespace) -> ProjectService:
    return ProjectService(services)


@pytest.fixture
def owner(services: SimpleNamespace) -> Actor:
    return Actor(services.user_repo.create(username="owner", password_hash="h", role="user"))


@pytest.fixture
def project(service: ProjectService, owner: Actor) -> Any:
    return service.create_project(owner_user=owner, name="Alpha")


def actions(service: ProjectService, project_id: str, owner: Actor) -> list[str]:
    return [e.action for e in service.list_timeline(project_id, user=owner)]


# ── create ───────────────────────────────────────────────────────────────────


def test_create_task_starts_in_planning_and_records_timeline(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="Write the spec")

    assert task.status == "planning"
    assert task.project_id == project.id
    assert task.created_by == owner.id
    assert task.sort_order == 0

    events = service.list_timeline(project.id, user=owner)
    assert [e.action for e in events] == [TIMELINE_TASK_CREATED]
    assert events[0].task_id == task.id
    assert events[0].actor == actor_ref("user", owner.id)
    assert events[0].payload == {"title": "Write the spec"}


def test_create_task_rejects_a_blank_title(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    with pytest.raises(ValueError, match="task title is required"):
        service.create_task(project.id, user=owner, title="   ")


def test_create_task_rejects_a_status_outside_the_vocabulary(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    """Any of the seven states may be chosen up front (R2); anything else is 409."""
    for status in ("done", "review"):
        assert (
            service.create_task(project.id, user=owner, title="T", status=status).status == status
        )
    with pytest.raises(OctopError) as err:
        service.create_task(project.id, user=owner, title="T", status="exploded")
    assert err.value.code is ErrorCode.PROJECT_TASK_STATUS_INVALID
    assert err.value.status == 409


def test_create_task_accepts_explicit_todo(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T", status="todo")
    assert task.status == "todo"


def test_create_task_carries_optional_fields(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    """Optional fields ride through create — ``deps`` included.

    **Changed in T-70**: this used to pass ``deps=["x"]`` with no such task on the
    board, which the old (ungated) path accepted: the deps field was a *soft* foreign
    key and creating a task with a dangling dependency succeeded. G6 now runs on the
    create verb too (``validate_task_graph`` → ``missing-id``), so the dependency is a
    **real** task here — the assertion still covers exactly what it did before (the
    four optional fields survive create), on a legal board.
    """
    dependency = service.create_task(project.id, user=owner, title="dep")
    task = service.create_task(
        project.id,
        user=owner,
        title="T",
        description="d",
        priority=3,
        deps=[dependency.id],
        due_at=1234,
    )
    assert (task.description, task.priority, task.deps, task.due_at) == (
        "d",
        3,
        (dependency.id,),
        1234,
    )


def test_create_task_refuses_a_dangling_dependency(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    """★ The behaviour the case above used to encode, now **explicitly denied** (T-70).

    Pre-T-70 the create verb never ran G6, so ``deps=["x"]`` on a task that does not
    exist was accepted and persisted. That is the old behaviour; this asserts its
    opposite, with the reason (``missing-id``) and the code, so a future revert of the
    gate cannot be silent.
    """
    with pytest.raises(OctopError) as err:
        service.create_task(project.id, user=owner, title="T", deps=["nope"])

    assert err.value.code is ErrorCode.TEAM_TASK_GRAPH_INVALID
    assert err.value.status == 409
    assert (err.value.details or {}).get("code") == "missing-id"
    # 「拒绝」与「没写」是两件事：不留半成品行（create_task 自己的承诺）。
    assert [t.title for t in service.list_tasks(project.id, user=owner)] == []


def test_task_sort_order_is_per_project(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    a = service.create_task(project.id, user=owner, title="1")
    b = service.create_task(project.id, user=owner, title="2")
    assert (a.sort_order, b.sort_order) == (0, 1)


def test_create_task_requires_write(
    service: ProjectService, services: SimpleNamespace, project: Any, owner: Actor
) -> None:
    viewer = Actor(services.user_repo.create(username="viewer", password_hash="h", role="user"))
    service.add_member(
        project.id,
        user=owner,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(viewer.id),
        role="viewer",
        subject_user_id=viewer.id,
    )
    with pytest.raises(OctopError) as err:
        service.create_task(project.id, user=viewer, title="T")
    assert err.value.code is ErrorCode.PROJECT_ROLE_FORBIDDEN


# ── soft foreign keys ────────────────────────────────────────────────────────


def test_parent_task_must_exist(service: ProjectService, project: Any, owner: Actor) -> None:
    with pytest.raises(OctopError) as err:
        service.create_task(project.id, user=owner, title="child", parent_id="nope")
    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500


def test_parent_task_must_belong_to_the_same_project(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    other = service.create_project(owner_user=owner, name="Beta")
    foreign_parent = service.create_task(other.id, user=owner, title="elsewhere")

    with pytest.raises(OctopError) as err:
        service.create_task(project.id, user=owner, title="child", parent_id=foreign_parent.id)
    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500


def test_task_can_nest_in_its_own_project(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    parent = service.create_task(project.id, user=owner, title="parent")
    child = service.create_task(project.id, user=owner, title="child", parent_id=parent.id)
    assert child.parent_id == parent.id


def test_task_cannot_be_its_own_parent(service: ProjectService, project: Any, owner: Actor) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    with pytest.raises(OctopError) as err:
        service.update_task(project.id, task.id, user=owner, parent_id=task.id)
    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND
    assert err.value.status != 500


# ── parent_id, the four frozen shapes (PLAN §5.2 / AC-F4-6) ──────────────────
#
# ① self-reference → 404 (the test above), ② missing parent → 404,
# ③ cross-project parent → 404, ④ **indirect cycle → 400** (the one new code).
# The three 404 shapes are pre-existing behaviour: they are checked before the
# cycle walk and must never move to the new code (two codes for one family is
# the shape this repo keeps re-learning to avoid).


def test_reparenting_to_an_unknown_task_is_not_found(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")

    with pytest.raises(OctopError) as err:
        service.update_task(project.id, task.id, user=owner, parent_id="tsk_missing")

    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500
    assert service.get_task(project.id, task.id, user=owner).parent_id is None


def test_reparenting_to_another_projects_task_is_not_found(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    other = service.create_project(owner_user=owner, name="Beta")
    foreign = service.create_task(other.id, user=owner, title="elsewhere")
    task = service.create_task(project.id, user=owner, title="T")

    with pytest.raises(OctopError) as err:
        service.update_task(project.id, task.id, user=owner, parent_id=foreign.id)

    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500
    assert service.get_task(project.id, task.id, user=owner).parent_id is None


def test_reparenting_onto_a_direct_child_is_rejected_with_400(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    """④ A→B→A: the new parent is this task's own descendant."""
    root = service.create_task(project.id, user=owner, title="root")
    child = service.create_task(project.id, user=owner, title="child", parent_id=root.id)

    with pytest.raises(OctopError) as err:
        service.update_task(project.id, root.id, user=owner, parent_id=child.id)

    assert err.value.code is ErrorCode.PROJECT_TASK_PARENT_INVALID
    assert err.value.status == 400
    assert err.value.status != 500
    # Nothing was written: the rejected move leaves both rows as they were.
    assert service.get_task(project.id, root.id, user=owner).parent_id is None
    assert service.get_task(project.id, child.id, user=owner).parent_id == root.id


def test_reparenting_onto_a_deeper_descendant_is_rejected_with_400(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    """④ The walk is transitive: a grandchild is a descendant too."""
    root = service.create_task(project.id, user=owner, title="root")
    child = service.create_task(project.id, user=owner, title="child", parent_id=root.id)
    grandchild = service.create_task(project.id, user=owner, title="grandchild", parent_id=child.id)

    with pytest.raises(OctopError) as err:
        service.update_task(project.id, root.id, user=owner, parent_id=grandchild.id)

    assert err.value.code is ErrorCode.PROJECT_TASK_PARENT_INVALID
    assert err.value.status == 400
    assert service.get_task(project.id, root.id, user=owner).parent_id is None


def test_a_legal_reparent_inside_the_same_project_is_accepted(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    """The new 400 must not block the legitimate case it was carved out of."""
    first = service.create_task(project.id, user=owner, title="first")
    second = service.create_task(project.id, user=owner, title="second")
    leaf = service.create_task(project.id, user=owner, title="leaf", parent_id=first.id)

    moved = service.update_task(project.id, leaf.id, user=owner, parent_id=second.id)

    assert moved.parent_id == second.id


def test_the_ancestor_walk_terminates_on_a_dirty_cycle(
    services: SimpleNamespace, service: ProjectService, project: Any, owner: Actor
) -> None:
    """★ Termination: a chain that is already cyclic must stop, not loop forever.

    The cycle is written straight into the row (no service path can create one
    any more, which is the point of the guard), so this asserts the escape hatch
    of the walk rather than a user-reachable state.
    """
    first = service.create_task(project.id, user=owner, title="first")
    second = service.create_task(project.id, user=owner, title="second", parent_id=first.id)
    third = service.create_task(project.id, user=owner, title="third", parent_id=second.id)
    with services.db.transaction() as conn:
        conn.execute(
            "UPDATE project_tasks SET parent_id = ? WHERE task_id = ?",
            (third.id, first.id),
        )
    other = service.create_task(project.id, user=owner, title="other")

    with pytest.raises(OctopError) as err:
        service.update_task(project.id, other.id, user=owner, parent_id=second.id)

    assert err.value.code is ErrorCode.PROJECT_TASK_PARENT_INVALID
    assert err.value.status == 400
    assert err.value.status != 500


def test_the_ancestor_walk_stops_at_a_missing_row(
    services: SimpleNamespace, service: ProjectService, project: Any, owner: Actor
) -> None:
    """★ Termination: a dangling ``parent_id`` above the target ends the walk."""
    root = service.create_task(project.id, user=owner, title="root")
    middle = service.create_task(project.id, user=owner, title="middle", parent_id=root.id)
    services.project_task_repo.delete(root.id)
    leaf = service.create_task(project.id, user=owner, title="leaf")

    moved = service.update_task(project.id, leaf.id, user=owner, parent_id=middle.id)

    assert moved.parent_id == middle.id


def test_unknown_thread_is_rejected(service: ProjectService, project: Any, owner: Actor) -> None:
    with pytest.raises(OctopError) as err:
        service.create_task(project.id, user=owner, title="T", thread_id="th_missing")
    assert err.value.code is ErrorCode.PROJECT_TASK_THREAD_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500


def test_unknown_thread_is_rejected_on_the_update_path_too(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    """``update`` takes the same soft-FK check, so it must answer the same code."""
    task = service.create_task(project.id, user=owner, title="T")

    with pytest.raises(OctopError) as err:
        service.update_task(project.id, task.id, user=owner, thread_id="th_missing")

    assert err.value.code is ErrorCode.PROJECT_TASK_THREAD_NOT_FOUND
    assert err.value.status == 404
    assert err.value.status != 500
    assert service.get_task(project.id, task.id, user=owner).thread_id is None


def _insert_thread(services: SimpleNamespace, thread_id: str, user_id: int) -> None:
    """A dashboard thread for ``user_id``; threads.agent_id is a hard FK."""
    services.agent_repo.create(agent_id=f"ag-{thread_id}", user_id=user_id, name="Agent")
    services.thread_repo.insert(
        thread_id=thread_id,
        agent_id=f"ag-{thread_id}",
        user_id=user_id,
        channel_type="dashboard",
        session_key=f"sk-{thread_id}",
    )


def test_known_thread_is_accepted(
    service: ProjectService, services: SimpleNamespace, project: Any, owner: Actor
) -> None:
    _insert_thread(services, "th1", owner.id)
    task = service.create_task(project.id, user=owner, title="T", thread_id="th1")
    assert task.thread_id == "th1"
    assert [t.id for t in services.project_task_repo.list_by_thread("th1")] == [task.id]


# ── state machine ────────────────────────────────────────────────────────────


def test_happy_path_todo_doing_review_done(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T", status="todo")
    for target in ("doing", "review", "done"):
        assert (
            service.transition_task(project.id, task.id, user=owner, target=target).status == target
        )

    assert actions(service, project.id, owner) == [
        TIMELINE_TASK_CREATED,
        TIMELINE_TASK_STATUS_CHANGED,
        TIMELINE_TASK_STATUS_CHANGED,
        TIMELINE_TASK_STATUS_CHANGED,
    ]
    events = service.list_timeline(project.id, user=owner)
    assert [e.payload for e in events[1:]] == [
        {"from": "todo", "to": "doing"},
        {"from": "doing", "to": "review"},
        {"from": "review", "to": "done"},
    ]


def test_todo_cannot_jump_to_done(service: ProjectService, project: Any, owner: Actor) -> None:
    task = service.create_task(project.id, user=owner, title="T", status="todo")
    with pytest.raises(OctopError) as err:
        service.transition_task(project.id, task.id, user=owner, target="done")
    assert err.value.code is ErrorCode.PROJECT_TASK_STATUS_INVALID
    assert service.get_task(project.id, task.id, user=owner).status == "todo"


def test_blocked_is_reachable_and_recoverable(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T", status="todo")
    assert (
        service.transition_task(project.id, task.id, user=owner, target="blocked").status
        == "blocked"
    )
    assert (
        service.transition_task(project.id, task.id, user=owner, target="doing").status == "doing"
    )
    assert (
        service.transition_task(project.id, task.id, user=owner, target="blocked").status
        == "blocked"
    )
    assert service.transition_task(project.id, task.id, user=owner, target="todo").status == "todo"


def test_done_can_only_be_reopened_to_doing(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T", status="todo")
    service.transition_task(project.id, task.id, user=owner, target="doing")
    service.transition_task(project.id, task.id, user=owner, target="done")

    for target in ("todo", "review", "blocked"):
        with pytest.raises(OctopError) as err:
            service.transition_task(project.id, task.id, user=owner, target=target)
        assert err.value.code is ErrorCode.PROJECT_TASK_STATUS_INVALID

    assert (
        service.transition_task(project.id, task.id, user=owner, target="doing").status == "doing"
    )


def test_cancelled_is_terminal(service: ProjectService, project: Any, owner: Actor) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    service.transition_task(project.id, task.id, user=owner, target="cancelled")
    for target in ("todo", "doing", "review", "done", "blocked", "cancelled"):
        with pytest.raises(OctopError) as err:
            service.transition_task(project.id, task.id, user=owner, target=target)
        assert err.value.code is ErrorCode.PROJECT_TASK_STATUS_INVALID


def test_unknown_task_status_is_rejected_with_its_code(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    with pytest.raises(OctopError) as err:
        service.transition_task(project.id, task.id, user=owner, target="exploded")
    assert err.value.code is ErrorCode.PROJECT_TASK_STATUS_INVALID
    assert err.value.status == 409, "an unknown enum value is a 4xx, never a 500"


def test_no_timeline_row_when_the_transition_is_rejected(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    with pytest.raises(OctopError):
        service.transition_task(project.id, task.id, user=owner, target="done")
    assert actions(service, project.id, owner) == [TIMELINE_TASK_CREATED]


def test_task_status_is_not_patchable_through_update_task(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    with pytest.raises(OctopError) as err:
        service.update_task(project.id, task.id, user=owner, status="done")
    assert err.value.code is ErrorCode.PROJECT_TASK_STATUS_INVALID
    assert err.value.status == 409, "status changes route through the state machine"


# ── update / delete ──────────────────────────────────────────────────────────


def test_update_task_patches_and_records(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    updated = service.update_task(project.id, task.id, user=owner, title="Renamed", priority=5)

    assert (updated.title, updated.priority) == ("Renamed", 5)
    events = service.list_timeline(project.id, user=owner)
    assert [e.action for e in events] == [TIMELINE_TASK_CREATED, TIMELINE_TASK_UPDATED]
    assert events[1].payload == {"fields": ["priority", "title"]}


def test_update_task_with_no_fields_is_a_noop(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    assert service.update_task(project.id, task.id, user=owner) == task
    assert actions(service, project.id, owner) == [TIMELINE_TASK_CREATED]


def test_assignment_records_its_own_event(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    service.update_task(
        project.id, task.id, user=owner, assignee_type="agent", assignee_id="agent-1"
    )

    events = service.list_timeline(project.id, user=owner)
    assert [e.action for e in events] == [
        TIMELINE_TASK_CREATED,
        TIMELINE_TASK_UPDATED,
        TIMELINE_TASK_ASSIGNED,
    ]
    assert events[-1].payload == {"assignee_type": "agent", "assignee_id": "agent-1"}


def test_delete_task_records_but_keeps_the_history(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    assert service.delete_task(project.id, task.id, user=owner) is True

    with pytest.raises(OctopError) as err:
        service.get_task(project.id, task.id, user=owner)
    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND

    events = service.list_timeline(project.id, user=owner)
    assert [e.action for e in events] == [TIMELINE_TASK_CREATED, TIMELINE_TASK_DELETED]
    assert events[-1].task_id == task.id, "the deleted task id must survive in history"


def test_delete_unknown_task_is_not_found(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    with pytest.raises(OctopError) as err:
        service.delete_task(project.id, "nope", user=owner)
    assert err.value.code is ErrorCode.PROJECT_TASK_NOT_FOUND


# ── listing / permissions ────────────────────────────────────────────────────


def test_list_tasks_filters_by_status(service: ProjectService, project: Any, owner: Actor) -> None:
    first = service.create_task(project.id, user=owner, title="1", status="todo")
    service.create_task(project.id, user=owner, title="2", status="todo")
    service.transition_task(project.id, first.id, user=owner, target="doing")

    assert [t.title for t in service.list_tasks(project.id, user=owner)] == ["1", "2"]
    assert [t.title for t in service.list_tasks(project.id, user=owner, status="todo")] == ["2"]
    assert [t.title for t in service.list_tasks(project.id, user=owner, status="doing")] == ["1"]


def test_viewer_can_read_tasks_but_not_change_them(
    service: ProjectService, services: SimpleNamespace, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    viewer = Actor(services.user_repo.create(username="viewer", password_hash="h", role="user"))
    service.add_member(
        project.id,
        user=owner,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(viewer.id),
        role="viewer",
        subject_user_id=viewer.id,
    )

    assert [t.id for t in service.list_tasks(project.id, user=viewer)] == [task.id]
    assert service.get_task(project.id, task.id, user=viewer).id == task.id
    assert [e.action for e in service.list_timeline(project.id, user=viewer)] == [
        TIMELINE_TASK_CREATED
    ]

    for call in (
        lambda: service.transition_task(project.id, task.id, user=viewer, target="doing"),
        lambda: service.update_task(project.id, task.id, user=viewer, title="X"),
        lambda: service.delete_task(project.id, task.id, user=viewer),
    ):
        with pytest.raises(OctopError) as err:
            call()
        assert err.value.code is ErrorCode.PROJECT_ROLE_FORBIDDEN
    assert service.assert_project_role(project.id, user=viewer, required=PROJECT_READ) == "viewer"
    with pytest.raises(OctopError):
        service.assert_project_role(project.id, user=viewer, required=PROJECT_WRITE)


def test_non_member_cannot_reach_a_task(
    service: ProjectService, services: SimpleNamespace, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    outsider = Actor(services.user_repo.create(username="out", password_hash="h", role="user"))

    with pytest.raises(OctopError) as err:
        service.get_task(project.id, task.id, user=outsider)
    assert err.value.code is ErrorCode.PROJECT_FORBIDDEN


def test_archived_project_makes_tasks_read_only(
    service: ProjectService, project: Any, owner: Actor
) -> None:
    task = service.create_task(project.id, user=owner, title="T")
    service.transition_project(project.id, user=owner, target="active")
    service.transition_project(project.id, user=owner, target="archived")

    assert service.get_task(project.id, task.id, user=owner).id == task.id
    with pytest.raises(OctopError) as err:
        service.transition_task(project.id, task.id, user=owner, target="doing")
    assert err.value.code is ErrorCode.PROJECT_FORBIDDEN


def test_timeline_limit(service: ProjectService, project: Any, owner: Actor) -> None:
    for i in range(3):
        service.create_task(project.id, user=owner, title=f"T{i}")
    assert len(service.list_timeline(project.id, user=owner, limit=2)) == 2


def test_timeline_is_ordered_oldest_first(
    service: ProjectService, services: SimpleNamespace, project: Any, owner: Actor
) -> None:
    first = service.create_task(project.id, user=owner, title="1", status="todo")
    second = service.create_task(project.id, user=owner, title="2")
    service.transition_task(project.id, first.id, user=owner, target="doing")

    events = service.list_timeline(project.id, user=owner)
    assert [e.task_id for e in events] == [first.id, second.id, first.id]
    assert [e.pk for e in events] == sorted(e.pk for e in events)


# ── R18 / R21 · the assignee normalization and the derived mirror ────────────
#
# PLAN §B2.3 ④ + §B2.5 (a). The mapping is a module-level pure function, so the
# whole output face (both child-table row sets **and** the mirror pair) is one
# expression per case. The 422 for a request that contradicts itself is B3-API's
# (Pydantic model validator); the mapping below stays total and deterministic so
# there is no second, hidden copy of the rule.


def _assert_mapping(
    mapping: AssigneeMapping | None,
    *,
    owners: tuple[int, ...],
    executors: tuple[str, ...],
    mirror: tuple[str | None, str | None],
    changed: tuple[bool, bool],
) -> None:
    """Assert the whole output face: both row sets, the mirror, and which sides
    the request actually writes (a side it did not name must keep its rows)."""
    assert mapping is not None
    assert mapping.owners == owners
    assert mapping.executors == executors
    assert (mapping.assignee_type, mapping.assignee_id) == mirror
    assert (mapping.owners_changed, mapping.executors_changed) == changed


def test_normalize_new_arrays_only() -> None:
    # owners only ⇒ the mirror falls back to ('user', owners[0])
    _assert_mapping(
        normalize_assignees(owners=[7, 3]),
        owners=(7, 3),
        executors=(),
        mirror=("user", "7"),
        changed=(True, False),
    )
    # executors only ⇒ the mirror is executors[0]
    _assert_mapping(
        normalize_assignees(executors=["team:t1", "agent:a1"]),
        owners=(),
        executors=("team:t1", "agent:a1"),
        mirror=("team", "t1"),
        changed=(False, True),
    )
    # both sides at once
    _assert_mapping(
        normalize_assignees(owners=[7], executors=["team:t1"]),
        owners=(7,),
        executors=("team:t1",),
        mirror=("team", "t1"),
        changed=(True, True),
    )
    # empty arrays are a legal value (unassigned), not "missing"
    _assert_mapping(
        normalize_assignees(owners=[], executors=[]),
        owners=(),
        executors=(),
        mirror=(None, None),
        changed=(True, True),
    )


def test_mirror_prefers_the_executor_over_the_owner() -> None:
    """★ The mirror must be ``executors[0]`` first, never 'user first'.

    ``infra/projects/dispatch.py`` decides dispatchability from
    ``assignee_type ∈ {agent, team}``: a 'user first' rule would silently strip
    dispatch from a task that does have an executor.
    """
    both = normalize_assignees(owners=[7], executors=["agent:a1"])
    assert both is not None
    assert (both.assignee_type, both.assignee_id) == ("agent", "a1")
    owners_only = normalize_assignees(owners=[7])
    assert owners_only is not None
    assert (owners_only.assignee_type, owners_only.assignee_id) == ("user", "7")


def test_normalize_promotes_the_legacy_pair() -> None:
    _assert_mapping(
        normalize_assignees(assignee_type="user", assignee_id="9"),
        owners=(9,),
        executors=(),
        mirror=("user", "9"),
        changed=(True, False),
    )
    for kind in ("agent", "team"):
        _assert_mapping(
            normalize_assignees(assignee_type=kind, assignee_id="x1"),
            owners=(),
            executors=(f"{kind}:x1",),
            mirror=(kind, "x1"),
            changed=(False, True),
        )
    # the old NULL/NULL clear has exactly one faithful mapping: both sides empty
    _assert_mapping(
        normalize_assignees(assignee_type=None, assignee_id=None),
        owners=(),
        executors=(),
        mirror=(None, None),
        changed=(True, True),
    )


def test_normalize_with_both_shapes_is_deterministic() -> None:
    """A request naming both shapes is a 422 in the HTTP layer (R18 ⓑ); the
    mapping still answers, with the new array owning the side it named."""
    _assert_mapping(
        normalize_assignees(owners=[2], assignee_type="agent", assignee_id="a1"),
        owners=(2,),
        executors=("agent:a1",),
        mirror=("agent", "a1"),
        changed=(True, True),
    )
    _assert_mapping(
        normalize_assignees(
            owners=[2], executors=["team:t1"], assignee_type="user", assignee_id="9"
        ),
        owners=(2,),
        executors=("team:t1",),
        mirror=("team", "t1"),
        changed=(True, True),
    )


def test_normalize_returns_none_when_all_six_are_absent() -> None:
    assert normalize_assignees() is None
    assert (
        normalize_assignees(owners=UNSET, executors=UNSET, assignee_type=UNSET, assignee_id=UNSET)
        is None
    )


def test_normalize_keeps_an_unnamed_side_from_the_stored_state() -> None:
    """R21 ②: the absent side is *not* rewritten — it resolves to what is stored."""
    _assert_mapping(
        normalize_assignees(owners=[], current_owners=[7], current_executors=["team:t1"]),
        owners=(),
        executors=("team:t1",),
        mirror=("team", "t1"),
        changed=(True, False),
    )
    _assert_mapping(
        normalize_assignees(executors=["agent:a1"], current_owners=[7]),
        owners=(7,),
        executors=("agent:a1",),
        mirror=("agent", "a1"),
        changed=(False, True),
    )


def test_normalize_dedupes_and_keeps_the_array_order() -> None:
    _assert_mapping(
        normalize_assignees(owners=[3, 1, 3]),
        owners=(3, 1),
        executors=(),
        mirror=("user", "3"),
        changed=(True, False),
    )
    _assert_mapping(
        normalize_assignees(executors=["agent:a1", "team:t1", "agent:a1"]),
        owners=(),
        executors=("agent:a1", "team:t1"),
        mirror=("agent", "a1"),
        changed=(False, True),
    )


def test_normalize_refuses_a_non_executor_subject() -> None:
    """The child table has no CHECK (PLAN §B2.3.1), so this is the domain guard."""
    for bad in ("user:1", "expert:x", "agent", "agent:"):
        with pytest.raises(ValueError, match="executors elements"):
            normalize_assignees(executors=[bad])


def _assignee_snapshot(
    db: SqlitePool, task_id: str
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """The four things R18/R21 talk about: both child tables, row by row."""
    with db.connect() as conn:
        owners = [
            tuple(r)
            for r in conn.execute(
                "SELECT id, user_id FROM project_task_owners WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]
        executors = [
            tuple(r)
            for r in conn.execute(
                "SELECT id, subject_type, subject_id FROM project_task_executors"
                " WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]
    return owners, executors


def test_legacy_pair_is_promoted_into_the_child_tables(
    services: SimpleNamespace, owner: Actor
) -> None:
    """R18 ②: a request that only knows the old pair still lands in the new model."""
    project = services.project_repo.create(owner_user_id=owner.id, name="Alpha")
    member = services.user_repo.create(username="mate", password_hash="h", role="user")
    tasks = services.project_task_repo

    from_user = tasks.create(
        project_id=project.id,
        title="legacy user",
        created_by=owner.id,
        assignee_type="user",
        assignee_id=str(member),
    )
    assert from_user.owners == (member,)
    assert from_user.executors == ()
    assert (from_user.assignee_type, from_user.assignee_id) == ("user", str(member))
    owner_rows, executor_rows = _assignee_snapshot(services.db, from_user.id)
    assert [row[1] for row in owner_rows] == [member]
    assert executor_rows == []

    from_agent = tasks.create(
        project_id=project.id,
        title="legacy agent",
        created_by=owner.id,
        assignee_type="agent",
        assignee_id="a1",
    )
    assert from_agent.owners == ()
    assert from_agent.executors == ("agent:a1",)
    assert (from_agent.assignee_type, from_agent.assignee_id) == ("agent", "a1")


def test_mirror_is_derived_and_cannot_be_hand_written(
    services: SimpleNamespace, owner: Actor
) -> None:
    """R18 ⓐ: even a caller passing the old pair next to the arrays cannot make
    the mirror disagree with ``executors[0]``."""
    project = services.project_repo.create(owner_user_id=owner.id, name="Alpha")
    tasks = services.project_task_repo
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner.id,
        owners=[owner.id],
        executors=["team:t1"],
    )

    updated = tasks.update(
        task.id,
        owners=[owner.id],
        executors=["agent:a1"],
        assignee_type="user",
        assignee_id=str(owner.id),
    )
    assert updated is not None
    assert (updated.assignee_type, updated.assignee_id) == ("agent", "a1")
    assert updated.executors == ("agent:a1",)


def test_patch_without_subject_fields_leaves_all_four_untouched(
    services: SimpleNamespace, owner: Actor
) -> None:
    """R18 ④ / R21 ②: the field is absent ⇒ both child tables and the mirror stay."""
    project = services.project_repo.create(owner_user_id=owner.id, name="Alpha")
    member = services.user_repo.create(username="mate", password_hash="h", role="user")
    tasks = services.project_task_repo
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner.id,
        owners=[owner.id, member],
        executors=["team:t1"],
    )
    before = _assignee_snapshot(services.db, task.id)

    patched = tasks.update(task.id, title="renamed")

    assert patched is not None
    assert _assignee_snapshot(services.db, task.id) == before
    assert (patched.assignee_type, patched.assignee_id) == ("team", "t1")
    assert patched.owners == (owner.id, member)


def test_explicit_empty_owners_clears_that_side_only(
    services: SimpleNamespace, owner: Actor
) -> None:
    """R21 ①: ``PATCH {owners: []}`` ⇒ 0 rows on that side, and the response
    side is an array (``()`` at row level, ``[]`` over HTTP)."""
    project = services.project_repo.create(owner_user_id=owner.id, name="Alpha")
    tasks = services.project_task_repo
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner.id,
        owners=[owner.id],
        executors=["agent:a1"],
    )

    cleared = tasks.update(task.id, owners=[])

    assert cleared is not None
    assert cleared.owners == ()
    assert cleared.executors == ("agent:a1",)
    assert _assignee_snapshot(services.db, task.id)[0] == []
    # the mirror follows the executor that is still there
    assert (cleared.assignee_type, cleared.assignee_id) == ("agent", "a1")

    both_cleared = tasks.update(task.id, owners=[], executors=[])
    assert both_cleared is not None
    assert (both_cleared.owners, both_cleared.executors) == ((), ())
    assert _assignee_snapshot(services.db, task.id) == ([], [])
    assert (both_cleared.assignee_type, both_cleared.assignee_id) == (None, None)


def test_task_rows_expose_both_subject_sides(services: SimpleNamespace, owner: Actor) -> None:
    """The child rows take part in SELECT and row mapping, not just in the DDL."""
    project = services.project_repo.create(owner_user_id=owner.id, name="Alpha")
    member = services.user_repo.create(username="mate", password_hash="h", role="user")
    tasks = services.project_task_repo
    task = tasks.create(
        project_id=project.id,
        title="T",
        created_by=owner.id,
        owners=[member],
        executors=["team:t1"],
    )

    listed = services.project_task_repo.list_by_project(project.id)
    assert [(t.id, t.owners, t.executors) for t in listed] == [(task.id, (member,), ("team:t1",))]
