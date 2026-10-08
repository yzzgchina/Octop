"""Project task rows and the append-only project timeline.

``project_tasks`` and ``timeline_events`` live together because every task
mutation writes a timeline row.

Columns that have no database foreign key (``parent_id``, ``thread_id``) are
validated at write time instead — the plan asks for soft-FK existence checks so
a bogus reference cannot silently create an orphan.

Timeline actions use a ``<subject>.<verb>`` vocabulary so a reader can filter by
prefix; the persisted values are the constants below rather than ad-hoc strings.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, cast

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    UNSET,
    DbRow,
    insert_returning_id,
    map_rows,
    now_ts,
    optional_updates,
    sql_in_placeholders,
)
from octop.infra.db.task_prefix import derive_task_prefix
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_short_id

TASK_STATUSES: tuple[str, ...] = (
    "planning",
    "todo",
    "doing",
    "review",
    "done",
    "blocked",
    "cancelled",
)

#: Who a task can be assigned to (``assignee_type``) — the **mirror** column's
#: value domain, kept for the pre-existing dispatch/read paths.
TASK_ASSIGNEE_TYPES: tuple[str, ...] = ("user", "agent", "team")

#: The subjects an ``executors`` element may name (PLAN §B2.1b). ``user`` is
#: deliberately absent: people are carried by ``owners``.
TASK_EXECUTOR_TYPES: tuple[str, ...] = ("agent", "team")

#: The **one** task-number allocation statement (PLAN §B1.2, AC-2). A single
#: statement, so the read-modify-write race of ``SELECT MAX(...) + 1`` cannot
#: exist, and no lock is taken on a counter row that does not exist yet
#: (``SELECT … FOR UPDATE`` cannot lock a missing row; the UPSERT covers exactly
#: that case). ``RETURNING`` works on SQLite 3.35+ and on PostgreSQL; the pool
#: rewrites ``?`` to ``%s`` for PostgreSQL.
ALLOCATE_TASK_SEQ_SQL = (
    "INSERT INTO project_task_counters(project_id, last_seq) VALUES (?, 1)"
    " ON CONFLICT(project_id) DO UPDATE SET"
    " last_seq = project_task_counters.last_seq + 1 RETURNING last_seq"
)

# ── timeline action vocabulary ───────────────────────────────────────────────
TIMELINE_TASK_CREATED = "task.created"
TIMELINE_TASK_UPDATED = "task.updated"
TIMELINE_TASK_STATUS_CHANGED = "task.status_changed"
TIMELINE_TASK_ASSIGNED = "task.assigned"
TIMELINE_TASK_DELETED = "task.deleted"
TIMELINE_TASK_DISPATCHED = "task.dispatched"


def actor_ref(kind: str, actor_id: object) -> str:
    """Timeline actor string, e.g. ``user:12`` / ``agent:ab12cd``."""
    return f"{kind}:{actor_id}"


def task_number_text(prefix: str, seq: int) -> str:
    """``f"{prefix}-{seq:03d}"`` up to 999, four digits from 1000 (PLAN §B1.2)."""
    return f"{prefix}-{seq:03d}" if seq <= 999 else f"{prefix}-{seq:04d}"


# ── assignees: the one normalization and the derived mirror (PLAN §B2.3 ④) ───


def executor_ref(subject_type: str, subject_id: object) -> str:
    """``executors`` element form, e.g. ``agent:ab12cd`` (PLAN §B2.1b).

    The type prefix is not decoration: an executor array has to carry
    ``agent`` / ``team`` apart, and the frontend already builds the same key via
    ``memberKey(type, id)``.
    """
    return f"{subject_type}:{subject_id}"


@dataclass(frozen=True)
class AssigneeMapping:
    """The final subject set of one task, as the child tables will hold it.

    ``owners`` / ``executors`` are the **authority** (``project_task_owners`` and
    ``project_task_executors`` rows, in client array order); ``assignee_type`` /
    ``assignee_id`` are the **derived mirror** on ``project_tasks``, never a
    second place a caller may set from the request (PLAN §B2.3 ④ / R19).
    ``owners_changed`` / ``executors_changed`` say whether that side is written at
    all: a side the request did not mention keeps its rows *and their ids*.
    """

    owners: tuple[int, ...]
    executors: tuple[str, ...]
    assignee_type: str | None
    assignee_id: str | None
    owners_changed: bool
    executors_changed: bool


def _dedupe_owners(values: Iterable[object]) -> tuple[int, ...]:
    """Array order with duplicates removed (first occurrence wins)."""
    unique: list[int] = []
    for value in values:
        user_id = int(str(value))
        if user_id not in unique:
            unique.append(user_id)
    return tuple(unique)


def _dedupe_executors(values: Iterable[object]) -> tuple[str, ...]:
    """Array order, duplicates removed, ``"{type}:{id}"`` shape enforced.

    The child table carries no ``CHECK`` on ``subject_type`` (PLAN §B2.3.1), so
    this is the repo layer's single value-domain guard: only ``agent`` / ``team``
    pass, and ``user:…`` is refused on this side as well as by the request model.
    """
    unique: list[str] = []
    for value in values:
        ref = str(value)
        subject_type, sep, subject_id = ref.partition(":")
        if not sep or not subject_id or subject_type not in TASK_EXECUTOR_TYPES:
            raise ValueError(
                f"executors elements must be '<agent|team>:<id>' (PLAN §B2.1b): {ref!r}"
            )
        if ref not in unique:
            unique.append(ref)
    return tuple(unique)


def _promote_legacy(
    assignee_type: object, assignee_id: object
) -> tuple[tuple[int, ...], tuple[str, ...], bool, bool]:
    """Lift the withdrawn single pair into the two-sided model (PLAN §B2.3.2).

    Returns ``(owners, executors, owners_touched, executors_touched)``. The old
    pair carries **single-subject** semantics — one task, one assignee — so the
    only faithful lift is a **whole-pair replacement**: ``user`` becomes the one
    owner *and clears the executors*, ``agent`` / ``team`` becomes the one
    executor *and clears the owners*. Either way **both** sides are touched.

    Touching only the named side would keep the other side's stale rows, and
    because the mirror is derived ``executors[0]`` first, a re-assignment from
    ``team`` / ``agent`` to ``user`` would leave the mirror — and with it
    dispatchability — parked on the old subject; the reverse direction would
    leave an invisible stale owner behind.

    A given ``NULL / NULL`` clears both sides for the same reason: the old
    "clear" semantics has no other faithful mapping. An unknown type, or a half
    pair, names no subject and is treated like that clear rather than silently
    keeping a stale subject. Both keywords unset means the pair is absent and
    touches neither side.
    """
    if assignee_type is UNSET and assignee_id is UNSET:
        return ((), (), False, False)
    kind = None if assignee_type is UNSET else str(assignee_type)
    ident = None if assignee_id is UNSET else assignee_id
    if kind == "user" and ident is not None:
        return ((int(str(ident)),), (), True, True)
    if kind in TASK_EXECUTOR_TYPES and ident:
        return ((), (executor_ref(kind, ident),), True, True)
    return ((), (), True, True)


def normalize_assignees(
    *,
    owners: object = UNSET,
    executors: object = UNSET,
    assignee_type: object = UNSET,
    assignee_id: object = UNSET,
    current_owners: Iterable[object] = (),
    current_executors: Iterable[object] = (),
) -> AssigneeMapping | None:
    """The **only** place a request's subjects become child-table rows + mirror.

    Pure (no DB, no request objects), so the rule is unit-testable on its own and
    cannot be re-implemented per caller (PLAN §B2.3 ④, R18/R19):

    * ``owners`` given ⇒ ``project_task_owners`` rows in array order, deduplicated
      and order-preserving, possibly empty (empty = unassigned).
    * ``executors`` given ⇒ ``project_task_executors`` rows the same way; the two
      sides are independent and may both carry values.
    * the legacy pair given ⇒ the **whole pair** is replaced, because the old
      pair means one single subject: ``user`` is promoted to
      ``owners=[int(id)]`` **with ``executors=[]``**, ``agent`` / ``team`` to
      ``executors=['<type>:<id>']`` **with ``owners=[]``**, and ``NULL / NULL``
      clears both sides. Lifting only the named side would leave the other
      side's rows standing, so a cross-side re-assignment would keep the mirror
      (below) on the old subject.
    * a side the request does **not** name resolves to ``current_owners`` /
      ``current_executors`` and its ``*_changed`` flag is false, so that side
      keeps its rows and their ids. This is the per-side three-state contract of
      PLAN §B2.5 (a): absent = keep, ``[]`` = clear, non-empty = replace.
    * **all four keywords unset** ⇒ ``None``: the patch says nothing about
      subjects, so the caller must leave both child tables *and* both mirror
      columns untouched (the ``UNSET`` sentinel precedent, ``update()`` below).

    When a request carries both shapes, a side given as a new array wins and the
    legacy pair only applies to the side the request left unset. The
    self-contradicting request is a ``422`` in the HTTP layer (B3-API, R18 ⓑ);
    this function stays total and deterministic so that guard is not a hidden
    re-implementation of the mapping.

    The mirror is derived here and nowhere else, **``executors[0]`` first**: the
    mirror decides dispatchability (``infra/projects/dispatch.py`` reads
    ``assignee_type ∈ {agent, team}``), so a "user first" rule would silently
    strip dispatch from a task that has an executor.
    """
    if owners is UNSET and executors is UNSET and assignee_type is UNSET and assignee_id is UNSET:
        return None
    legacy_owners, legacy_executors, legacy_owners_set, legacy_executors_set = _promote_legacy(
        assignee_type, assignee_id
    )
    owners_changed = owners is not UNSET or legacy_owners_set
    executors_changed = executors is not UNSET or legacy_executors_set
    if owners is not UNSET:
        final_owners = _dedupe_owners(cast("Iterable[object]", owners))
    elif legacy_owners_set:
        final_owners = legacy_owners
    else:
        final_owners = _dedupe_owners(current_owners)
    if executors is not UNSET:
        final_executors = _dedupe_executors(cast("Iterable[object]", executors))
    elif legacy_executors_set:
        final_executors = legacy_executors
    else:
        final_executors = _dedupe_executors(current_executors)
    if final_executors:
        mirror_type, _, mirror_id = final_executors[0].partition(":")
    elif final_owners:
        mirror_type, mirror_id = "user", str(final_owners[0])
    else:
        mirror_type, mirror_id = None, None
    return AssigneeMapping(
        owners=final_owners,
        executors=final_executors,
        assignee_type=mirror_type,
        assignee_id=mirror_id,
        owners_changed=owners_changed,
        executors_changed=executors_changed,
    )


def _parse_deps(raw: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(raw) if raw else "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(x) for x in parsed)


def _dump_deps(deps: Iterable[object]) -> str:
    return json.dumps([str(d) for d in deps], ensure_ascii=False)


def _json_array(value: object) -> object:
    """Codec for a JSON-array column in a patch: ``UNSET`` stays ``UNSET``.

    The patch semantics (``UNSET`` = leave alone, explicit value = write) decide
    what to do *before* the codec runs, so the sentinel passes through untouched.
    """
    if value is UNSET:
        return UNSET
    return _dump_deps(cast("Iterable[object]", value))


def _parse_payload(raw: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw) if raw else "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ── project_tasks ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProjectTaskRow:
    id: str
    pk: int
    project_id: str
    parent_id: str | None
    title: str
    description: str
    status: str
    assignee_type: str | None
    assignee_id: str | None
    #: In-project number, ``f"{prefix}-{seq:03d}"`` (PLAN §B1.2); NULL only for a
    #: row written before the v27 backfill ran.
    task_number: str | None
    priority: int
    deps: tuple[str, ...]
    thread_id: str | None
    origin_node_id: str | None
    start_at: int | None
    due_at: int | None
    sort_order: int
    created_by: int
    created_at: int
    updated_at: int
    # 025 team-run columns (PLAN「数据模型②·加列」). The four JSON arrays reuse the
    # ``deps`` codec above; the claim/attempt tokens are written by ``claim`` alone
    # so a stale writer can never move them.
    kind: str = "work"
    acceptance: tuple[str, ...] = ()
    in_scope: tuple[str, ...] = ()
    verify: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    round: int = 1
    verdict: str | None = None
    attempt: int = 0
    attempt_id: str | None = None
    claimed_by: str | None = None
    claimed_at: int | None = None
    started_at: int | None = None
    phase: str | None = None
    #: The two child tables, in child-row (``id``) order — which is the order the
    #: client sent, because a save is a whole-set replacement. ``from_row`` cannot
    #: see them (they live in other tables), so the repo attaches them; a row built
    #: straight from ``project_tasks`` reads as unassigned rather than as a lie.
    owners: tuple[int, ...] = ()
    executors: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, r: DbRow) -> ProjectTaskRow:
        return cls(
            id=str(r["task_id"]),
            pk=int(r["id"]),
            project_id=str(r["project_id"]),
            parent_id=(str(r["parent_id"]) if r["parent_id"] is not None else None),
            title=str(r["title"]),
            description=str(r["description"] or ""),
            status=str(r["status"]),
            assignee_type=(str(r["assignee_type"]) if r["assignee_type"] is not None else None),
            assignee_id=(str(r["assignee_id"]) if r["assignee_id"] is not None else None),
            task_number=(str(r["task_number"]) if r["task_number"] is not None else None),
            priority=int(r["priority"]),
            deps=_parse_deps(r["deps"]),
            thread_id=(str(r["thread_id"]) if r["thread_id"] is not None else None),
            origin_node_id=(str(r["origin_node_id"]) if r["origin_node_id"] is not None else None),
            start_at=(int(r["start_at"]) if r["start_at"] is not None else None),
            due_at=(int(r["due_at"]) if r["due_at"] is not None else None),
            sort_order=int(r["sort_order"]),
            created_by=int(r["created_by"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
            kind=str(r["kind"]),
            acceptance=_parse_deps(r["acceptance"]),
            in_scope=_parse_deps(r["in_scope"]),
            verify=_parse_deps(r["verify"]),
            changed_paths=_parse_deps(r["changed_paths"]),
            round=int(r["round"]),
            verdict=(str(r["verdict"]) if r["verdict"] is not None else None),
            attempt=int(r["attempt"]),
            attempt_id=(str(r["attempt_id"]) if r["attempt_id"] is not None else None),
            claimed_by=(str(r["claimed_by"]) if r["claimed_by"] is not None else None),
            claimed_at=(int(r["claimed_at"]) if r["claimed_at"] is not None else None),
            started_at=(int(r["started_at"]) if r["started_at"] is not None else None),
            phase=(str(r["phase"]) if r["phase"] is not None else None),
        )


class ProjectTaskRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def _allocate_id(self) -> str:
        for _ in range(16):
            task_id = new_short_id()
            if self.get(task_id) is None:
                return task_id
        raise RuntimeError("failed to allocate unique task id")

    # ── soft foreign keys (no DB constraint to lean on) ──────────────────────

    def _assert_parent_ok(self, project_id: str, parent_id: str | None) -> None:
        if parent_id is None:
            return
        parent = self.get(parent_id)
        if parent is None:
            raise OctopError(
                ErrorCode.PROJECT_TASK_NOT_FOUND,
                f"parent task {parent_id} does not exist",
            )
        if parent.project_id != project_id:
            raise OctopError(
                ErrorCode.PROJECT_TASK_NOT_FOUND,
                f"parent task {parent_id} belongs to another project",
            )

    def _assert_thread_exists(self, thread_id: str | None) -> None:
        if thread_id is None:
            return
        with self._db.connect() as conn:
            row = conn.execute("SELECT 1 FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is None:
            raise OctopError(
                ErrorCode.PROJECT_TASK_THREAD_NOT_FOUND,
                f"thread {thread_id} does not exist",
            )

    def _assert_parent_not_descendant(self, task_id: str, parent_id: str) -> None:
        """Reject an **indirect** cycle: the new parent is a descendant of the task.

        Only reachable once a task may be re-parented (``update``). Walking up
        from the proposed parent must never reach ``task_id``; if it does, the
        move would create ``A -> B -> A`` and the row would become unreachable
        from any root.

        The self-reference (``parent_id == task_id``), a missing parent and a
        cross-project parent stay ``404 PROJECT_TASK_NOT_FOUND`` — they are
        checked before this runs and must not move to this code (batch 3 §5.2).

        The walk is bounded by ``seen``: a chain that is already cyclic (dirty
        data written before this guard existed) and a chain that ends in a
        missing row both stop instead of looping forever.
        """
        seen = {task_id}
        current: str | None = parent_id
        while current is not None:
            if current in seen:
                raise OctopError(
                    ErrorCode.PROJECT_TASK_PARENT_INVALID,
                    f"parent task {parent_id} is a descendant of task {task_id}",
                )
            seen.add(current)
            row = self.get(current)
            current = row.parent_id if row is not None else None

    # ── reads ────────────────────────────────────────────────────────────────

    def next_sort_order(self, project_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m FROM project_tasks WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["m"]) + 1 if row else 0

    def get(self, task_id: str) -> ProjectTaskRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM project_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if r is None:
                return None
            return self._attach_assignees(conn, [ProjectTaskRow.from_row(r)])[0]

    def list_by_project(
        self, project_id: str, *, status: str | None = None
    ) -> list[ProjectTaskRow]:
        sql = "SELECT * FROM project_tasks WHERE project_id = ?"
        params: list[object] = [project_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY sort_order, created_at, id"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return self._attach_assignees(conn, map_rows(rows, ProjectTaskRow))

    def list_by_thread(self, thread_id: str) -> list[ProjectTaskRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM project_tasks WHERE thread_id = ? ORDER BY created_at",
                (thread_id,),
            ).fetchall()
            return self._attach_assignees(conn, map_rows(rows, ProjectTaskRow))

    def count_by_project(self, project_id: str, *, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM project_tasks WHERE project_id = ?"
        params: list[object] = [project_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        with self._db.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    # ── the child tables are read and written only from here ─────────────────

    def _attach_assignees(self, conn: Any, rows: list[ProjectTaskRow]) -> list[ProjectTaskRow]:
        """Fill ``owners`` / ``executors`` for a whole page with **two** queries.

        One query per child table for the entire page (never one per task) — the
        N+1 the card forbids. Child-row ``id`` order reproduces the order the
        client sent, because a save replaces the whole set.
        """
        if not rows:
            return rows
        ids = [row.id for row in rows]
        placeholders = sql_in_placeholders(len(ids))
        owners: dict[str, list[int]] = {}
        for r in conn.execute(
            "SELECT task_id, user_id FROM project_task_owners"
            f" WHERE task_id IN ({placeholders}) ORDER BY task_id, id",
            ids,
        ).fetchall():
            owners.setdefault(str(r["task_id"]), []).append(int(r["user_id"]))
        executors: dict[str, list[str]] = {}
        for r in conn.execute(
            "SELECT task_id, subject_type, subject_id FROM project_task_executors"
            f" WHERE task_id IN ({placeholders}) ORDER BY task_id, id",
            ids,
        ).fetchall():
            executors.setdefault(str(r["task_id"]), []).append(
                executor_ref(str(r["subject_type"]), str(r["subject_id"]))
            )
        return [
            replace(
                row,
                owners=tuple(owners.get(row.id, ())),
                executors=tuple(executors.get(row.id, ())),
            )
            for row in rows
        ]

    @staticmethod
    def _replace_assignees(conn: Any, task_id: str, mapping: AssigneeMapping) -> None:
        """Write the sides the request named, inside the caller's transaction.

        Delete a named side, then insert its final rows in array order: the stored
        order is the client's order, the operation is idempotent, and a re-send of
        the same array reproduces the same rows. A side the request did not name
        is not touched at all (its rows keep their ids). Duplicates are already
        removed by ``normalize_assignees``; ``UNIQUE(task_id, user_id)`` /
        ``UNIQUE(task_id, subject_type, subject_id)`` are the database backstop
        behind it (a duplicate that reaches this INSERT fails loudly instead of
        being silently swallowed).
        """
        if mapping.owners_changed:
            conn.execute("DELETE FROM project_task_owners WHERE task_id = ?", (task_id,))
            for user_id in mapping.owners:
                conn.execute(
                    "INSERT INTO project_task_owners(task_id, user_id) VALUES (?, ?)",
                    (task_id, user_id),
                )
        if mapping.executors_changed:
            conn.execute("DELETE FROM project_task_executors WHERE task_id = ?", (task_id,))
            for ref in mapping.executors:
                subject_type, _, subject_id = ref.partition(":")
                conn.execute(
                    "INSERT INTO project_task_executors(task_id, subject_type, subject_id)"
                    " VALUES (?, ?, ?)",
                    (task_id, subject_type, subject_id),
                )

    def _next_task_number(self, conn: Any, project_id: str) -> str:
        """Allocate this project's next number **on the caller's transaction**.

        A plain read of the prefix, then the single UPSERT of
        ``ALLOCATE_TASK_SEQ_SQL``. The returned ``last_seq`` *is* this
        allocation's sequence, so two concurrent creators cannot share a number;
        both statements run in the task's own transaction, so a failed insert
        rolls the counter back with it.
        """
        row = conn.execute(
            "SELECT name, task_prefix FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        prefix = str(row["task_prefix"] or "") if row is not None else ""
        if not prefix:
            # ``NOT NULL DEFAULT ''`` plus the v27 backfill normally fill this in.
            # Deriving here (from the one implementation in ``task_prefix.py``)
            # keeps a row written on a database whose watermark skipped the
            # backfill numberable instead of unnumbered.
            prefix = derive_task_prefix(
                str(row["name"] or "") if row is not None else "", project_id
            )
        seq_row = conn.execute(ALLOCATE_TASK_SEQ_SQL, (project_id,)).fetchone()
        if seq_row is None:
            raise RuntimeError(f"task number allocation returned no row: {project_id}")
        return task_number_text(prefix, int(seq_row["last_seq"]))

    # ── writes ───────────────────────────────────────────────────────────────

    def create(
        self,
        *,
        project_id: str,
        title: str,
        created_by: int,
        description: str = "",
        status: str = "todo",
        parent_id: str | None = None,
        owners: object = UNSET,
        executors: object = UNSET,
        assignee_type: object = UNSET,
        assignee_id: object = UNSET,
        priority: int = 0,
        deps: Iterable[object] = (),
        thread_id: str | None = None,
        origin_node_id: str | None = None,
        start_at: int | None = None,
        due_at: int | None = None,
        sort_order: int | None = None,
        kind: str = "work",
        acceptance: Iterable[object] = (),
        in_scope: Iterable[object] = (),
        verify: Iterable[object] = (),
        phase: str | None = None,
        task_id: str | None = None,
    ) -> ProjectTaskRow:
        """Create a task: number allocation, row, and both subject sides.

        ``assignee_type`` / ``assignee_id`` are the **legacy** input pair (the
        mirror columns); a caller may still pass them and they are promoted by
        ``normalize_assignees``. Omitting every subject keyword on a brand-new
        task means exactly ``owners=[]`` + ``executors=[]`` — there is no earlier
        state for "leave it alone" to mean.
        """
        self._assert_parent_ok(project_id, parent_id)
        self._assert_thread_exists(thread_id)
        # A caller-supplied id is honoured verbatim (the plan's API takes ``id?`` so a
        # run can name its own tasks); omitted, the id is allocated as before -- every
        # existing caller is untouched. Uniqueness is the caller's to enforce *before*
        # this insert (``validate_task_graph`` does), because the UNIQUE constraint's
        # failure would surface as a 500 rather than the plan's 409.
        task_id = task_id or self._allocate_id()
        ts = now_ts()
        if sort_order is None:
            sort_order = self.next_sort_order(project_id)
        mapping = normalize_assignees(
            owners=owners,
            executors=executors,
            assignee_type=assignee_type,
            assignee_id=assignee_id,
        )
        if mapping is None:
            mapping = normalize_assignees(owners=[], executors=[])
        assert mapping is not None  # both-empty input is never the UNSET sentinel
        with self._db.transaction() as conn:
            task_number = self._next_task_number(conn, project_id)
            conn.execute(
                "INSERT INTO project_tasks("
                "task_id, project_id, parent_id, title, description, status, "
                "assignee_type, assignee_id, task_number, priority, deps, thread_id, "
                "origin_node_id, start_at, due_at, sort_order, created_by, created_at, updated_at, "
                "kind, acceptance, in_scope, verify, phase"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    project_id,
                    parent_id,
                    title,
                    description,
                    status,
                    mapping.assignee_type,
                    mapping.assignee_id,
                    task_number,
                    priority,
                    _dump_deps(deps),
                    thread_id,
                    origin_node_id,
                    start_at,
                    due_at,
                    sort_order,
                    created_by,
                    ts,
                    ts,
                    kind,
                    _dump_deps(acceptance),
                    _dump_deps(in_scope),
                    _dump_deps(verify),
                    phase,
                ),
            )
            self._replace_assignees(conn, task_id, mapping)
        row = self.get(task_id)
        if row is None:
            raise RuntimeError(f"project task insert failed: {task_id}")
        return row

    def update(
        self,
        task_id: str,
        *,
        title: object = UNSET,
        description: object = UNSET,
        status: object = UNSET,
        parent_id: object = UNSET,
        owners: object = UNSET,
        executors: object = UNSET,
        assignee_type: object = UNSET,
        assignee_id: object = UNSET,
        priority: object = UNSET,
        deps: object = UNSET,
        thread_id: object = UNSET,
        origin_node_id: object = UNSET,
        start_at: object = UNSET,
        due_at: object = UNSET,
        sort_order: object = UNSET,
        kind: object = UNSET,
        acceptance: object = UNSET,
        in_scope: object = UNSET,
        verify: object = UNSET,
        changed_paths: object = UNSET,
        round: object = UNSET,
        verdict: object = UNSET,
        phase: object = UNSET,
        started_at: object = UNSET,
    ) -> ProjectTaskRow | None:
        """Patch fields; every keyword defaults to the ``UNSET`` sentinel.

        Subjects are a **three-state** patch (PLAN §B2.5 (a)): omit all four
        keywords ⇒ the child tables and both mirror columns are untouched;
        ``owners=[]`` / ``executors=[]`` ⇒ that side is cleared; a non-empty array
        ⇒ that side is replaced whole. ``assignee_type`` / ``assignee_id`` remain
        accepted as the legacy pair and are promoted, never written verbatim: the
        mirror is derived from the final set (R19).
        """
        current = self.get(task_id)
        if current is None:
            return None
        if parent_id is not UNSET:
            parent_value = cast("str | None", parent_id)
            if parent_value == task_id:
                raise OctopError(
                    ErrorCode.PROJECT_TASK_NOT_FOUND,
                    "a task cannot be its own parent",
                )
            self._assert_parent_ok(current.project_id, parent_value)
            if parent_value is not None:
                # ④ indirect cycle only; the three 404 shapes above are frozen.
                self._assert_parent_not_descendant(task_id, parent_value)
        if thread_id is not UNSET:
            self._assert_thread_exists(cast("str | None", thread_id))

        mapping = normalize_assignees(
            owners=owners,
            executors=executors,
            assignee_type=assignee_type,
            assignee_id=assignee_id,
            current_owners=current.owners,
            current_executors=current.executors,
        )
        pairs: list[tuple[str, object]] = [
            ("title", title),
            ("description", description),
            ("status", status),
            ("parent_id", parent_id),
            ("priority", priority),
            (
                "deps",
                _dump_deps(cast("Iterable[object]", deps)) if deps is not UNSET else UNSET,
            ),
            ("thread_id", thread_id),
            ("origin_node_id", origin_node_id),
            ("start_at", start_at),
            ("due_at", due_at),
            ("sort_order", sort_order),
            ("kind", kind),
            ("acceptance", _json_array(acceptance)),
            ("in_scope", _json_array(in_scope)),
            ("verify", _json_array(verify)),
            ("changed_paths", _json_array(changed_paths)),
            ("round", round),
            ("verdict", verdict),
            ("phase", phase),
            ("started_at", started_at),
        ]
        if mapping is not None:
            pairs.append(("assignee_type", mapping.assignee_type))
            pairs.append(("assignee_id", mapping.assignee_id))
        clauses, params = optional_updates(pairs)
        if not clauses:
            return current
        clauses.append("updated_at = ?")
        params.append(now_ts())
        params.append(task_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE project_tasks SET {', '.join(clauses)} WHERE task_id = ?",
                params,
            )
            if mapping is not None:
                self._replace_assignees(conn, task_id, mapping)
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM project_tasks WHERE task_id = ?", (task_id,))
        return bool(cur.rowcount)

    # ── attempts (claim / report) ────────────────────────────────────────────
    #
    # ``attempt_id`` is the token a run hands out when it claims a task and the
    # credential every later write must present (PLAN G8): a superseded token
    # cannot move the row. These two methods are the **only** writers of the
    # claim/attempt columns, which is what keeps that guarantee in one place.

    def _attempt_guard(self, task_id: str, expected: str | None) -> tuple[str, list[object]]:
        """``WHERE`` body pinning the row to the attempt the caller last read.

        ``attempt_id`` is nullable, so the guard is spelled in two shapes instead
        of ``attempt_id IS ?``: the PostgreSQL proxy rewrites ``?`` to ``%s`` and
        PostgreSQL rejects a placeholder after ``IS``. Same idiom as
        ``ArtifactRepo.bind`` (``... AND task_id IS NULL``).
        """
        if expected is None:
            return "task_id = ? AND attempt_id IS NULL", [task_id]
        return "task_id = ? AND attempt_id = ?", [task_id, expected]

    def claim(
        self,
        task_id: str,
        *,
        claimed_by: str,
        expected_attempt_id: str | None,
        attempt_id: str | None = None,
    ) -> ProjectTaskRow | None:
        """Claim *task_id* for *claimed_by* under a new attempt.

        Compare-and-set, and nothing else: one ``UPDATE`` whose ``WHERE`` carries
        the attempt the caller gated on. Two racing claims therefore cannot both
        win — the loser's statement matches no row and gets ``None``, which the
        caller answers with ``TEAM_TASK_CLAIM_CONFLICT``. The decision is taken by
        the database, not by a read-then-``if`` in Python (PLAN B26: the loser
        must never twin the owner).

        *expected_attempt_id* is what the caller read **before** its gates ran
        (``None`` until the first claim); passing a freshly re-read value instead
        is a deliberate transfer, which the plan allows. *attempt_id* is the token
        the new attempt runs under — omitted, a fresh one is minted; either way
        ``attempt`` is bumped. ``None`` also covers a task that no longer exists.
        """
        guard, guard_params = self._attempt_guard(task_id, expected_attempt_id)
        ts = now_ts()
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE project_tasks SET claimed_by = ?, claimed_at = ?, attempt_id = ?, "
                f"attempt = attempt + 1, updated_at = ? WHERE {guard}",
                (claimed_by, ts, attempt_id or new_short_id(), ts, *guard_params),
            )
        if not cur.rowcount:
            return None
        return self.get(task_id)

    def report(
        self,
        task_id: str,
        *,
        attempt_id: str,
        verdict: object = UNSET,
        changed_paths: object = UNSET,
        round: object = UNSET,
        phase: object = UNSET,
    ) -> ProjectTaskRow | None:
        """Record an attempt's outcome; ``None`` when *attempt_id* is stale.

        A write carrying a superseded token is refused (PLAN G8) — the row is left
        untouched and the caller answers ``TEAM_ATTEMPT_STALE``. The guard is
        re-checked inside the ``UPDATE`` as well, so an attempt that is superseded
        between the read and the write is refused too. Patch semantics are the
        same as :meth:`update`: omitted fields stay, explicit ``None`` clears.
        """
        current = self.get(task_id)
        if current is None:
            return None
        if current.attempt_id != attempt_id:
            return None
        clauses, params = optional_updates(
            [
                ("verdict", verdict),
                ("changed_paths", _json_array(changed_paths)),
                ("round", round),
                ("phase", phase),
            ]
        )
        if not clauses:
            return current
        guard, guard_params = self._attempt_guard(task_id, current.attempt_id)
        clauses.append("updated_at = ?")
        params.append(now_ts())
        with self._db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE project_tasks SET {', '.join(clauses)} WHERE {guard}",
                [*params, *guard_params],
            )
        if not cur.rowcount:
            return None
        return self.get(task_id)


# ── timeline_events ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TimelineEventRow:
    pk: int
    project_id: str
    task_id: str | None
    actor: str
    action: str
    payload: dict[str, Any]
    at: int

    @classmethod
    def from_row(cls, r: DbRow) -> TimelineEventRow:
        return cls(
            pk=int(r["id"]),
            project_id=str(r["project_id"]),
            task_id=(str(r["task_id"]) if r["task_id"] is not None else None),
            actor=str(r["actor"]),
            action=str(r["action"]),
            payload=_parse_payload(r["payload"]),
            at=int(r["at"]),
        )


class TimelineRepo:
    """Append-only project timeline — rows are never updated or deleted."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def append(
        self,
        *,
        project_id: str,
        actor: str,
        action: str,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TimelineEventRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            # RETURNING id rather than cursor.lastrowid: the PostgreSQL proxy
            # does not expose lastrowid.
            event_pk = insert_returning_id(
                conn,
                "INSERT INTO timeline_events(project_id, task_id, actor, action, payload, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    task_id,
                    actor,
                    action,
                    json.dumps(payload or {}, ensure_ascii=False),
                    ts,
                ),
            )
        row = self._get_by_pk(event_pk)
        if row is None:
            raise RuntimeError("timeline insert failed")
        return row

    def _get_by_pk(self, pk: int) -> TimelineEventRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM timeline_events WHERE id = ?", (pk,)).fetchone()
        return TimelineEventRow.from_row(r) if r else None

    def list_by_project(
        self, project_id: str, *, limit: int | None = None, task_id: str | None = None
    ) -> list[TimelineEventRow]:
        """Chronological order — oldest first, which is the replay order.

        ``task_id`` filters **in SQL** (a post-filter would interact with ``limit``
        and drop rows); omitting it leaves the statement byte-identical to before.
        """
        sql = "SELECT * FROM timeline_events WHERE project_id = ?"
        params: list[object] = [project_id]
        if task_id is not None:
            sql += " AND task_id = ?"
            params.append(task_id)
        sql += " ORDER BY at, id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, TimelineEventRow)

    def list_by_task(self, task_id: str, *, limit: int | None = None) -> list[TimelineEventRow]:
        sql = "SELECT * FROM timeline_events WHERE task_id = ? ORDER BY at, id"
        params: list[object] = [task_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, TimelineEventRow)

    def count_by_project(self, project_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM timeline_events WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["c"]) if row else 0
