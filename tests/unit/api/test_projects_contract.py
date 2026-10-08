"""The board payload contract and its refusal surface (PLAN §B2.5 (c) / §B1.2).

Three negative families, each with its positive control driven over the wire:

* an **illegal payload** — a non-positive owner id, a ``user:`` / third-kind
  executor, a repeated element, both subject shapes in one request, a malformed
  ``task_prefix`` — is a **422**, and nothing is written;
* a **reference that does not resolve** — an unknown project, an unknown task, a
  ``parent_id`` that is missing or belongs to another project — is a **404** with
  the project error code (never a 200, never a 500);
* a **reference that is not mine** — another user's project, or a viewer's write —
  is a **403**, while the legitimate owner / member path stays 2xx. The code is
  asserted, not just the status: a 404 carrying the wrong code would mean a
  different guard fired.

The last section pins the *exposure* side of the contract — ``task_prefix``,
``task_number``, ``owners`` and ``executors`` really reach the wire, ``[]`` rather
than ``null``, and the three patch states stay distinguishable.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, create_user, resolve_user_id

PROJECTS = "/api/projects"

#: ``<prefix>-001`` up to ``-999``, four digits from 1000 (PLAN §B1.2).
TASK_NUMBER_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{3,4}$")


def _error_code(response: httpx.Response) -> str:
    """Code from the standard ``{"error": {"code": ...}}`` envelope."""
    return str(response.json()["error"]["code"])


def _rejected_fields(response: httpx.Response) -> set[str]:
    """Field names named by a FastAPI 422 body (``detail[*].loc``).

    Asserting the offending field — not only the status — is what stops a 422 for
    the wrong reason (a typo'd test payload) from passing as a guarded rejection.
    """
    return {str(part) for entry in response.json()["detail"] for part in entry["loc"]}


async def _create_project(client: httpx.AsyncClient, auth: dict[str, str], name: str) -> str:
    r = await client.post(PROJECTS, headers=auth, json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["project_id"])


async def _create_task(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    project_id: str,
    title: str,
    **fields: Any,
) -> dict[str, Any]:
    r = await client.post(
        f"{PROJECTS}/{project_id}/tasks", headers=auth, json={"title": title, **fields}
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _board(
    client: httpx.AsyncClient, auth: dict[str, str], project_id: str
) -> list[dict[str, Any]]:
    r = await client.get(f"{PROJECTS}/{project_id}/tasks", headers=auth)
    assert r.status_code == 200, r.text
    return list(r.json())


async def _add_member(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    project_id: str,
    user_id: int,
    role: str,
) -> httpx.Response:
    return await client.post(
        f"{PROJECTS}/{project_id}/members",
        headers=auth,
        json={"subject_type": "user", "subject_id": str(user_id), "role": role},
    )


@pytest.fixture
async def api(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]]]:
    """Client, three authenticated actors, and their user ids."""
    async with octop_client(tmp_octop_home) as (client, _srv):
        await bootstrap_admin(client, tmp_octop_home)
        admin = await auth_header(client)
        auths = {
            "admin": admin,
            "alice": await create_user(client, admin, username="alice"),
            "bob": await create_user(client, admin, username="bob"),
        }
        ids = {name: await resolve_user_id(client, admin, name) for name in auths}
        yield client, auths, ids


# ── family 1: an illegal payload is a 422, and nothing is written ────────────


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"owners": ["abc"]}, id="owner-not-an-integer"),
        pytest.param({"owners": [0]}, id="owner-zero"),
        pytest.param({"owners": [-3]}, id="owner-negative"),
        pytest.param({"owners": "7"}, id="owners-not-an-array"),
    ],
)
async def test_a_malformed_owner_id_is_a_422(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
    payload: dict[str, Any],
) -> None:
    """V1: an ``owners`` element must be a positive integer (PLAN §B2.5 (c))."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    r = await client.post(f"{PROJECTS}/{pid}/tasks", headers=alice, json={"title": "T", **payload})

    assert r.status_code == 422, r.text
    assert "owners" in _rejected_fields(r), r.text
    assert await _board(client, alice, pid) == [], "a rejected payload must not create a task"


@pytest.mark.parametrize(
    "executor",
    [
        pytest.param("user:7", id="a-user-is-not-an-executor"),
        pytest.param("expert:9", id="a-third-subject-kind"),
        pytest.param("agent", id="no-separator"),
        pytest.param("team:", id="empty-id"),
        pytest.param("Agent:1", id="wrong-case-prefix"),
    ],
)
async def test_a_third_subject_kind_is_a_422(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
    executor: str,
) -> None:
    """V2: executors are ``agent:<id>`` / ``team:<id>`` — nothing else (PLAN §B2.1b).

    The user's own wording is the reason ``user:`` is refused here: assignment to a
    person goes through ``owners``, and only teams / experts go through executors.
    """
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    r = await client.post(
        f"{PROJECTS}/{pid}/tasks", headers=alice, json={"title": "T", "executors": [executor]}
    )

    assert r.status_code == 422, r.text
    assert "executors" in _rejected_fields(r), r.text
    assert await _board(client, alice, pid) == []


async def test_the_two_legal_executor_prefixes_are_accepted(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """The control for V2: ``agent:`` and ``team:`` pass and are stored verbatim."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    task = await _create_task(client, alice, pid, "T", executors=["agent:a1", "team:t1"])

    assert task["executors"] == ["agent:a1", "team:t1"], task


async def test_both_subject_shapes_in_one_request_are_a_422(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """V3: the arrays and the withdrawn single pair are two encodings of one fact."""
    client, auths, ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    tid = (await _create_task(client, alice, pid, "T"))["task_id"]

    created = await client.post(
        f"{PROJECTS}/{pid}/tasks",
        headers=alice,
        json={
            "title": "both",
            "owners": [ids["alice"]],
            "assignee_type": "user",
            "assignee_id": str(ids["alice"]),
        },
    )
    assert created.status_code == 422, created.text

    patched = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}",
        headers=alice,
        json={"executors": ["agent:a1"], "assignee_type": "agent", "assignee_id": "a1"},
    )
    assert patched.status_code == 422, patched.text

    # The legacy pair alone is still the sanctioned path (it is *promoted*, not refused).
    legacy_only = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}",
        headers=alice,
        json={"assignee_type": "user", "assignee_id": str(ids["alice"])},
    )
    assert legacy_only.status_code == 200, legacy_only.text
    assert legacy_only.json()["owners"] == [ids["alice"]], legacy_only.text


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"owners": [1, 1]}, id="owners-repeat"),
        pytest.param({"executors": ["agent:a1", "agent:a1"]}, id="executors-repeat"),
    ],
)
async def test_a_repeated_element_is_a_422(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
    payload: dict[str, Any],
) -> None:
    """V4: one array may not name the same subject twice."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    tid = (await _create_task(client, alice, pid, "T"))["task_id"]

    created = await client.post(
        f"{PROJECTS}/{pid}/tasks", headers=alice, json={"title": "T", **payload}
    )
    assert created.status_code == 422, created.text

    patched = await client.patch(f"{PROJECTS}/{pid}/tasks/{tid}", headers=alice, json=payload)
    assert patched.status_code == 422, patched.text


async def test_distinct_elements_are_accepted(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """The control for V4: the same payload with distinct elements is a 2xx."""
    client, auths, ids = api
    admin, alice = auths["admin"], auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    # The second owner must be a real user: a positive id that names nobody is its
    # own refusal (below), so the control has to use two existing accounts.
    assert (await _add_member(client, alice, pid, ids["bob"], "member")).status_code == 201

    task = await _create_task(client, alice, pid, "T", owners=[ids["alice"], ids["bob"]])

    assert task["owners"] == [ids["alice"], ids["bob"]], task
    assert admin  # the admin session is part of the fixture's coverage


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("bad", id="leading-lowercase"),
        pytest.param("1ABC", id="leading-digit"),
        pytest.param("A", id="too-short"),
        pytest.param("ABCDEFGHI", id="too-long"),
        pytest.param("AB-C", id="illegal-character"),
        pytest.param("", id="empty"),
    ],
)
async def test_a_malformed_task_prefix_is_a_422(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
    prefix: str,
) -> None:
    """``task_prefix`` is pattern-checked on the request model, not in the service."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    r = await client.patch(f"{PROJECTS}/{pid}", headers=alice, json={"task_prefix": prefix})

    assert r.status_code == 422, r.text
    assert "task_prefix" in _rejected_fields(r), r.text


async def test_an_owner_that_names_no_user_is_a_422_not_a_500(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """A positive id that resolves to nobody is a named 422 (the repo's soft FK).

    This is the *reference* half of the contract inside the assignment payload: the
    id is well-shaped, so the request model passes, and the refusal comes from the
    existence check with its own code instead of a database ``IntegrityError`` 500.
    """
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    r = await client.post(
        f"{PROJECTS}/{pid}/tasks", headers=alice, json={"title": "T", "owners": [10**7]}
    )

    assert r.status_code == 422, r.text
    assert _error_code(r) == "PROJECT_TASK_ASSIGNEE_INVALID", r.text
    assert await _board(client, alice, pid) == []


# ── family 2: a reference that does not resolve is a 404 ─────────────────────


async def test_an_unknown_project_is_a_404(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    client, auths, _ids = api
    alice = auths["alice"]

    read = await client.get(f"{PROJECTS}/01JZZZZZZZZZZZZZZZZZZZZZZZ", headers=alice)
    assert read.status_code == 404, read.text
    assert _error_code(read) == "PROJECT_NOT_FOUND", read.text

    patched = await client.patch(
        f"{PROJECTS}/01JZZZZZZZZZZZZZZZZZZZZZZZ", headers=alice, json={"task_prefix": "OCT2"}
    )
    assert patched.status_code == 404, patched.text
    assert _error_code(patched) == "PROJECT_NOT_FOUND", patched.text


async def test_an_unknown_task_is_a_404(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    r = await client.patch(
        f"{PROJECTS}/{pid}/tasks/01JZZZZZZZZZZZZZZZZZZZZZZZ", headers=alice, json={"title": "T"}
    )

    assert r.status_code == 404, r.text
    assert _error_code(r) == "PROJECT_TASK_NOT_FOUND", r.text


async def test_an_unknown_task_is_a_404_even_for_an_outside_caller(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """The ``{project_id}/{task_id}`` pair is the identity, so it is resolved first.

    A non-member therefore gets the same 404 as the owner would — the path never
    reveals whether that project holds that task (contrast the 403 above, where the
    pair does resolve and only membership refuses).
    """
    client, auths, ids = api
    alice, bob = auths["alice"], auths["bob"]
    pid = await _create_project(client, alice, "Apollo")

    r = await client.patch(
        f"{PROJECTS}/{pid}/tasks/01JZZZZZZZZZZZZZZZZZZZZZZZ",
        headers=bob,
        json={"owners": [ids["bob"]]},
    )

    assert r.status_code == 404, r.text
    assert _error_code(r) == "PROJECT_TASK_NOT_FOUND", r.text


async def test_a_parent_that_does_not_exist_is_a_404(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    r = await client.post(
        f"{PROJECTS}/{pid}/tasks",
        headers=alice,
        json={"title": "child", "parent_id": "01JZZZZZZZZZZZZZZZZZZZZZZZ"},
    )

    assert r.status_code == 404, r.text
    assert _error_code(r) == "PROJECT_TASK_NOT_FOUND", r.text


async def test_a_parent_from_another_project_is_a_404(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """A cross-project reference is refused without leaking that the row exists."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid_a = await _create_project(client, alice, "Apollo")
    pid_b = await _create_project(client, alice, "Borealis")
    foreign_parent = (await _create_task(client, alice, pid_b, "In B"))["task_id"]

    refused = await client.post(
        f"{PROJECTS}/{pid_a}/tasks",
        headers=alice,
        json={"title": "child", "parent_id": foreign_parent},
    )
    assert refused.status_code == 404, refused.text
    assert _error_code(refused) == "PROJECT_TASK_NOT_FOUND", refused.text

    # Control: the same reference inside its own project is accepted.
    accepted = await client.post(
        f"{PROJECTS}/{pid_b}/tasks",
        headers=alice,
        json={"title": "child", "parent_id": foreign_parent},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["parent_id"] == foreign_parent


# ── family 3: a reference that is not mine is a 403 ──────────────────────────


async def test_another_users_project_is_a_403_for_a_non_member(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """Non-members are refused by membership, not by the coarse permission key."""
    client, auths, ids = api
    alice, bob = auths["alice"], auths["bob"]
    pid = await _create_project(client, alice, "Apollo")
    tid = (await _create_task(client, alice, pid, "T"))["task_id"]

    for response in (
        await client.get(f"{PROJECTS}/{pid}", headers=bob),
        await client.patch(f"{PROJECTS}/{pid}", headers=bob, json={"name": "hijacked"}),
        await client.post(f"{PROJECTS}/{pid}/tasks", headers=bob, json={"title": "T"}),
        # A **real** task of somebody else's project: the pair resolves, so the
        # membership check is what refuses — 403, not the identity 404 below.
        await client.patch(
            f"{PROJECTS}/{pid}/tasks/{tid}", headers=bob, json={"owners": [ids["bob"]]}
        ),
    ):
        assert response.status_code == 403, response.text
        assert _error_code(response) == "PROJECT_FORBIDDEN", response.text

    # Control: the owner sees the same project, unrenamed.
    control = await client.get(f"{PROJECTS}/{pid}", headers=alice)
    assert control.status_code == 200, control.text
    assert control.json()["name"] == "Apollo"


async def test_a_viewer_cannot_change_subjects_but_a_member_can(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """R20: assigning stays on the existing ``PROJECT_WRITE`` level — viewer 403."""
    client, auths, ids = api
    alice, bob = auths["alice"], auths["bob"]
    pid = await _create_project(client, alice, "Apollo")
    tid = (await _create_task(client, alice, pid, "T"))["task_id"]

    assert (await _add_member(client, alice, pid, ids["bob"], "viewer")).status_code == 201
    refused = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}", headers=bob, json={"owners": [ids["bob"]]}
    )
    assert refused.status_code == 403, refused.text
    assert _error_code(refused) == "PROJECT_ROLE_FORBIDDEN", refused.text

    # Promoting the same subject to member keeps the route usable (idempotent add).
    assert (await _add_member(client, alice, pid, ids["bob"], "member")).status_code == 201
    allowed = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}", headers=bob, json={"owners": [ids["bob"]]}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["owners"] == [ids["bob"]], allowed.text


# ── exposure: the new fields really reach the wire ───────────────────────────


async def test_a_created_task_carries_its_number_and_both_subject_arrays(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    client, auths, ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    created = await _create_task(
        client,
        alice,
        pid,
        "T",
        owners=[ids["alice"]],
        executors=["agent:a1", "team:t1"],
    )

    assert created["task_number"] is not None, created
    assert TASK_NUMBER_RE.match(str(created["task_number"])), created
    assert created["owners"] == [ids["alice"]], created
    assert created["executors"] == ["agent:a1", "team:t1"], created

    # Both read paths carry the same two arrays (the board and the single-task patch).
    listed = (await _board(client, alice, pid))[0]
    assert listed["owners"] == [ids["alice"]], listed
    assert listed["executors"] == ["agent:a1", "team:t1"], listed
    patched = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{created['task_id']}", headers=alice, json={"title": "renamed"}
    )
    assert patched.json()["owners"] == [ids["alice"]], patched.text
    assert patched.json()["executors"] == ["agent:a1", "team:t1"], patched.text
    assert patched.json()["task_number"] == created["task_number"], patched.text


async def test_an_unassigned_task_reports_empty_arrays_not_null(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    created = await _create_task(client, alice, pid, "T")

    assert created["owners"] == [], created
    assert created["executors"] == [], created
    listed = (await _board(client, alice, pid))[0]
    assert listed["owners"] == [] and listed["executors"] == [], listed


async def test_the_project_payload_exposes_its_task_prefix(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    created = (await client.get(f"{PROJECTS}/{pid}", headers=alice)).json()
    assert isinstance(created["task_prefix"], str) and created["task_prefix"], created

    renamed = await client.patch(f"{PROJECTS}/{pid}", headers=alice, json={"task_prefix": "OCT2"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["task_prefix"] == "OCT2", renamed.text
    listed = (await client.get(PROJECTS, headers=alice)).json()
    assert [p["task_prefix"] for p in listed if p["project_id"] == pid] == ["OCT2"], listed


async def test_an_omitted_side_is_untouched_and_an_empty_array_clears(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """R21 / PLAN §B2.5 (a): the three states must stay distinguishable."""
    client, auths, ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    task = await _create_task(client, alice, pid, "T", owners=[ids["alice"]], executors=["team:t1"])
    tid = task["task_id"]

    # Omitted side (a title-only patch) leaves both sides exactly as they were.
    untouched = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}", headers=alice, json={"title": "renamed"}
    )
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["owners"] == [ids["alice"]], untouched.text
    assert untouched.json()["executors"] == ["team:t1"], untouched.text

    # ``[]`` clears one side and, by omission, leaves the other alone.
    cleared = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}", headers=alice, json={"owners": []}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["owners"] == [], cleared.text
    assert cleared.json()["executors"] == ["team:t1"], cleared.text

    # A non-empty array replaces the whole set (not an append).
    replaced = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}", headers=alice, json={"executors": ["agent:a9"]}
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["executors"] == ["agent:a9"], replaced.text

    stored = (await _board(client, alice, pid))[0]
    assert stored["owners"] == [] and stored["executors"] == ["agent:a9"], stored


async def test_changing_the_prefix_does_not_renumber_existing_tasks(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """PLAN §B1.3: ``task_prefix`` only feeds the numbers allocated after the write."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    first = await _create_task(client, alice, pid, "first")
    before = str(first["task_number"])

    renamed = await client.patch(f"{PROJECTS}/{pid}", headers=alice, json={"task_prefix": "ZZ9"})
    assert renamed.status_code == 200, renamed.text

    second = await _create_task(client, alice, pid, "second")
    board = {t["task_id"]: t for t in await _board(client, alice, pid)}

    # The stored number of the existing task is unchanged, verbatim.
    assert board[first["task_id"]]["task_number"] == before, board
    assert str(second["task_number"]).startswith("ZZ9-"), second


async def test_a_null_task_prefix_leaves_the_stored_prefix_alone(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """The column is NOT NULL: an explicit null is not a third "clear" state."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    before = (await client.get(f"{PROJECTS}/{pid}", headers=alice)).json()["task_prefix"]

    response = await client.patch(f"{PROJECTS}/{pid}", headers=alice, json={"task_prefix": None})

    assert response.status_code == 200, response.text
    assert response.json()["task_prefix"] == before, response.text


# ── family 4: V5 — an owner must be a member of *this* project ───────────────


async def test_a_non_member_owner_is_a_400(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """PLAN §B2.5 (c) V5: bob exists as a user but is not in alice's project.

    Existence alone is not membership — the repo's soft-FK check accepts bob, so
    only the service-level member rule can refuse this.
    """
    client, auths, ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    refused = await client.post(
        f"{PROJECTS}/{pid}/tasks",
        headers=alice,
        json={"title": "T", "owners": [ids["bob"]]},
    )

    assert refused.status_code == 400, refused.text
    assert _error_code(refused) == "PROJECT_MEMBER_INVALID", refused.text


async def test_the_legacy_assignee_pair_cannot_smuggle_a_non_member_owner(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """R18 ⓒ: the old pair is *promoted*, then checked by the same rule.

    Without promotion-then-validate this payload would be a way around V5.
    """
    client, auths, ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    refused = await client.post(
        f"{PROJECTS}/{pid}/tasks",
        headers=alice,
        json={"title": "T", "assignee_type": "user", "assignee_id": str(ids["bob"])},
    )

    assert refused.status_code == 400, refused.text
    assert _error_code(refused) == "PROJECT_MEMBER_INVALID", refused.text


async def test_a_legal_member_owner_is_a_201_and_nothing_is_written_when_refused(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """The positive control, plus the refusal leaving no half-built task behind."""
    client, auths, ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    refused = await client.post(
        f"{PROJECTS}/{pid}/tasks",
        headers=alice,
        json={"title": "refused", "owners": [ids["bob"]]},
    )
    assert refused.status_code == 400, refused.text

    created = await _create_task(client, alice, pid, "accepted", owners=[ids["alice"]])
    assert created["owners"] == [ids["alice"]], created

    # Exactly one task: the refused create ran before the first write.
    board = await _board(client, alice, pid)
    assert [t["title"] for t in board] == ["accepted"], board

    # A member added afterwards becomes assignable — the rule tracks the table.
    assert (await _add_member(client, alice, pid, ids["bob"], "member")).status_code == 201
    promoted = await _create_task(client, alice, pid, "now legal", owners=[ids["bob"]])
    assert promoted["owners"] == [ids["bob"]], promoted


async def test_a_non_member_owner_is_a_400_on_patch_too(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """V5 is shared by both write verbs; the edit path cannot bypass it either."""
    client, auths, ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    tid = (await _create_task(client, alice, pid, "T"))["task_id"]

    refused = await client.patch(
        f"{PROJECTS}/{pid}/tasks/{tid}", headers=alice, json={"owners": [ids["bob"]]}
    )

    assert refused.status_code == 400, refused.text
    assert _error_code(refused) == "PROJECT_MEMBER_INVALID", refused.text
    assert (await _board(client, alice, pid))[0]["owners"] == [], "no partial write"


# ── family 5: §B3.2 Q1-Q4 — a quoted comment must be on the same line ────────


COMMENTS = "comments"


async def test_quoting_a_missing_comment_is_a_404(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """Q1."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    refused = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={"body": "quoting nobody", "parent_comment_id": "NOSUCH"},
    )

    assert refused.status_code == 404, refused.text
    assert _error_code(refused) == "PROJECT_NOT_FOUND", refused.text


async def test_quoting_a_comment_from_another_project_is_a_404(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """Q2 — and the answer is identical to Q1, so existence never leaks."""
    client, auths, _ids = api
    alice = auths["alice"]
    other = await _create_project(client, alice, "Borealis")
    source = await client.post(
        f"{PROJECTS}/{other}/{COMMENTS}", headers=alice, json={"body": "in Borealis"}
    )
    assert source.status_code == 201, source.text
    foreign = source.json()["comment_id"]

    pid = await _create_project(client, alice, "Apollo")
    refused = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={"body": "quoting across projects", "parent_comment_id": foreign},
    )

    assert refused.status_code == 404, refused.text
    assert _error_code(refused) == "PROJECT_NOT_FOUND", refused.text


async def test_quoting_across_tasks_is_a_404(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """Q3 — including the "one side null" shape, which counts as different."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    first = (await _create_task(client, alice, pid, "T1"))["task_id"]
    second = (await _create_task(client, alice, pid, "T2"))["task_id"]

    on_first = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={"body": "on task 1", "task_id": first},
    )
    assert on_first.status_code == 201, on_first.text
    quoted = on_first.json()["comment_id"]

    # Different task id.
    cross = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={"body": "cross", "task_id": second, "parent_comment_id": quoted},
    )
    assert cross.status_code == 404, cross.text
    assert _error_code(cross) == "PROJECT_NOT_FOUND", cross.text

    # Task-scoped parent, project-level writer (one side null ⇒ 404 as well).
    null_side = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={"body": "project level", "parent_comment_id": quoted},
    )
    assert null_side.status_code == 404, null_side.text
    assert _error_code(null_side) == "PROJECT_NOT_FOUND", null_side.text


async def test_a_legal_parent_is_a_201(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """The positive control for Q1-Q3: same project, same line ⇒ accepted."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")
    tid = (await _create_task(client, alice, pid, "T1"))["task_id"]

    source = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={"body": "the original", "task_id": tid},
    )
    assert source.status_code == 201, source.text

    quoted = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={
            "body": "the quote",
            "task_id": tid,
            "parent_comment_id": source.json()["comment_id"],
            "quote_excerpt": "the original",
        },
    )

    assert quoted.status_code == 201, quoted.text
    assert quoted.json()["parent_comment_id"] == source.json()["comment_id"], quoted.text


async def test_both_quote_fields_round_trip_through_the_read_path(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """The wiring is both halves or neither: write and read the two fields.

    A create-only change would be silently dropped, and an output-only change
    would be permanently null — so both endpoints are asserted on the *stored*
    value, not just on the field existing.
    """
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    source = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}", headers=alice, json={"body": "quoted text"}
    )
    assert source.status_code == 201, source.text
    parent = source.json()["comment_id"]

    created = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={
            "body": "quoting it",
            "parent_comment_id": parent,
            "quote_excerpt": "quoted text",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["parent_comment_id"] == parent, created.text
    assert created.json()["quote_excerpt"] == "quoted text", created.text

    listed = (await client.get(f"{PROJECTS}/{pid}/{COMMENTS}", headers=alice)).json()
    stored = {row["comment_id"]: row for row in listed}[created.json()["comment_id"]]
    assert stored["parent_comment_id"] == parent, stored
    assert stored["quote_excerpt"] == "quoted text", stored

    # The unquoted comment stays null on both fields rather than becoming "".
    first = {row["comment_id"]: row for row in listed}[parent]
    assert first["parent_comment_id"] is None, first
    assert first["quote_excerpt"] is None, first


async def test_an_over_long_quote_excerpt_is_a_422(
    api: tuple[httpx.AsyncClient, dict[str, dict[str, str]], dict[str, int]],
) -> None:
    """PLAN §B3.2 Q7 — the length cap lives in the request model."""
    client, auths, _ids = api
    alice = auths["alice"]
    pid = await _create_project(client, alice, "Apollo")

    refused = await client.post(
        f"{PROJECTS}/{pid}/{COMMENTS}",
        headers=alice,
        json={"body": "x", "quote_excerpt": "z" * 201},
    )

    assert refused.status_code == 422, refused.text
    assert "quote_excerpt" in _rejected_fields(refused), refused.text
