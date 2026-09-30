"""Per-project task-numbering prefix derivation (PLAN §B1.1).

``derive_task_prefix`` is the single definition of the prefix rule. It lives
here -- next to the numbered migration pair, not in ``migrate.py`` and not in
``infra/db/repos/`` -- because two callers need it while neither may import the
other (AGENTS.md §5):

* ``infra/db/migrate.py`` backfills ``projects.task_prefix`` for the v27
  migration, and
* ``infra/db/repos/project_tasks.py`` derives the prefix for a newly created
  project (card B2-REPO).

The rule is deterministic and pure, so both callers and the unit tests see one
implementation of it.
"""

from __future__ import annotations


def derive_task_prefix(name: str, project_id: str) -> str:
    """Return the numbering prefix for a project (PLAN §B1.1, five steps).

    ``^[A-Z][A-Z0-9]{1,7}$`` is the frozen shape: uppercase, 2-8 characters,
    first character a letter. Non-ASCII characters are dropped, so a pure-CJK
    project name falls back to the ``project_id`` (a Crockford base32 short id).
    ``"TASK"`` is the constant of last resort when neither input yields ASCII.
    """
    core = "".join(c for c in name.upper() if c.isascii() and c.isalnum())
    core = core.lstrip("0123456789")
    core = core[:8]
    if len(core) < 2:
        core = "".join(c for c in project_id.upper() if c.isascii() and c.isalnum())
        core = core.lstrip("0123456789")[:8]
    return core[:8] or "TASK"
