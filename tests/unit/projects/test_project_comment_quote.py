"""Quoting a comment — PLAN §B3.2, and specifically the Q4 self-reference.

Q1 (missing), Q2 (cross-project) and Q3 (cross-task) are driven end-to-end
through HTTP in ``tests/unit/api/test_projects_contract.py``, where the request
and response models are also asserted. This module carries the one shape that
**cannot** be reached over HTTP this batch:

Q4 is ``parent_comment_id == comment_id`` on the *quoting* comment, and the plan
notes it is "仅 ``PATCH`` 路径可达". Concrete reason, verified against the repo —
``create`` allocates the id itself (``ProjectCommentRepo._allocate_id()``, called
after the request body is parsed) and ``CommentPatch`` carries only ``body``, so
no request can name the id its own row is about to receive. The guard is written
and unit-tested here so the rule is enforced wherever the surface arrives later;
it is *not* claimed to be reachable through the current API.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.project_artifacts import ProjectArtifactRepo
from octop.infra.db.repos.project_content import ProjectCommentRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo, TimelineRepo
from octop.infra.db.repos.projects import MEMBER_SUBJECT_USER, ProjectMemberRepo, ProjectRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge import service as knowledge_service_module
from octop.infra.projects import service as project_service_module
from octop.infra.projects.discussion import ProjectDiscussion
from octop.infra.projects.service import ProjectService
from octop.infra.utils.paths import PathLayout


class Actor:
    """Minimal user stand-in: ``id`` + ``is_admin`` + ``permissions``."""

    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.permissions = ["projects", "knowledge_bases"]

    @property
    def is_admin(self) -> bool:
        return False


@pytest.fixture
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    # Project creation is the fixture's setup step, not what this file tests.
    monkeypatch.setattr(knowledge_service_module, "assert_knowledge_usable", lambda *_a: None)
    monkeypatch.setattr(
        project_service_module, "get_capability", lambda *_a, **_k: {"usable": True}
    )
    return SimpleNamespace(
        db=pool,
        project_repo=ProjectRepo(pool),
        project_member_repo=ProjectMemberRepo(pool),
        project_task_repo=ProjectTaskRepo(pool),
        project_comment_repo=ProjectCommentRepo(pool),
        timeline_repo=TimelineRepo(pool),
        knowledge_repo=KnowledgeRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        agent_repo=AgentRepo(pool),
        audit_repo=AuditRepo(pool),
        project_artifact_repo=ProjectArtifactRepo(pool),
        paths=PathLayout.from_env(),
    )


def test_a_comment_may_not_quote_itself(services: SimpleNamespace) -> None:
    """Q4 ⇒ 404 ``PROJECT_NOT_FOUND``, the same code the other three shapes use.

    The id is passed both as the quoted comment and as the quoting one, which is
    the state a self-reference would describe. Asserting the code (not only the
    404) is what keeps the refusal from arriving via an unrelated lookup failure.
    """
    service = ProjectService(services)
    discussion = ProjectDiscussion(services)
    owner = Actor(services.user_repo.create(username="owner", password_hash="h", role="user"))
    project = service.create_project(owner_user=owner, name="Alpha")
    member = Actor(services.user_repo.create(username="member", password_hash="h", role="user"))
    service.add_member(
        project.id,
        user=owner,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(member.id),
        role="member",
        subject_user_id=member.id,
    )
    comment = discussion.add_comment(project.id, user=member, body="the original")

    with pytest.raises(OctopError) as err:
        discussion.assert_quote_ok(
            project.id,
            parent_comment_id=comment.id,
            task_id=None,
            comment_id=comment.id,
        )

    assert err.value.code is ErrorCode.PROJECT_NOT_FOUND
    assert err.value.status == 404


def test_quoting_oneself_is_fine_when_the_ids_differ(services: SimpleNamespace) -> None:
    """Control: the guard keys on equality, not on "the caller quoted something"."""
    service = ProjectService(services)
    discussion = ProjectDiscussion(services)
    owner = Actor(services.user_repo.create(username="owner", password_hash="h", role="user"))
    project = service.create_project(owner_user=owner, name="Alpha")
    member = Actor(services.user_repo.create(username="member", password_hash="h", role="user"))
    service.add_member(
        project.id,
        user=owner,
        subject_type=MEMBER_SUBJECT_USER,
        subject_id=str(member.id),
        role="member",
        subject_user_id=member.id,
    )
    comment = discussion.add_comment(project.id, user=member, body="the original")

    other = discussion.assert_quote_ok(
        project.id,
        parent_comment_id=comment.id,
        task_id=None,
        comment_id="SOMEOTHERID",
    )

    assert other.id == comment.id


def test_the_quote_pair_is_stored_and_read_back(services: SimpleNamespace) -> None:
    """§B3 wiring at the service boundary: the add path must really persist it.

    The HTTP layer proves the round trip; this pins the service default, so a
    future caller cannot get a silently-dropped quote by omitting an argument.
    """
    service = ProjectService(services)
    discussion = ProjectDiscussion(services)
    owner = Actor(services.user_repo.create(username="owner", password_hash="h", role="user"))
    project = service.create_project(owner_user=owner, name="Alpha")
    source = discussion.add_comment(project.id, user=owner, body="the original")

    quoting = discussion.add_comment(
        project.id,
        user=owner,
        body="quoting it",
        parent_comment_id=source.id,
        quote_excerpt="the original",
    )

    assert quoting.parent_comment_id == source.id
    assert quoting.quote_excerpt == "the original"
    listed = {row.id: row for row in discussion.list_comments(project.id, user=owner)}
    assert listed[quoting.id].parent_comment_id == source.id
    assert listed[quoting.id].quote_excerpt == "the original"
