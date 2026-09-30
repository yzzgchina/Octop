# -*- coding: utf-8 -*-
"""Verify migration numbering after the upstream v1.0.2b5 realignment.

The fork's project-domain / comment / team-run migrations were shifted one
number up (``019``-``025`` -> ``020``-``026``) so that upstream's
``019_bridge_connections`` keeps the number it already shipped with. Run this
after resolving the merge; it catches the ways such a renumber can be silently
half-done:

  1. two migrations sharing a version number (``_discover`` raises at runtime,
     so this would break every DB operation, not just one test)
  2. a migration file whose *watermark* does not match its filename prefix
  3. the fixups that always follow a version bump -- the ``if version == N``
     branch, the idempotent helper, and the schema-version assertions

NOT scanned on purpose: the planning docs and the archived one-shot renumber
scripts (``renumber_018_to_019.py`` / ``update_plan_after_renumber.py``) name
the numbers they were written with -- a whole-repo grep reports them as false
positives. Only the live tree is checked here.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "src/octop/infra/db/migrations"
MIGRATE_PY = REPO / "src/octop/infra/db/migrate.py"

EXPECTED_MAX = 26
UPSTREAM_PAIR = ("019_bridge_connections.sql", "019_bridge_connections.pg.sql")
FORK_PAIRS = (
    "020_projects",
    "021_project_task_metadata",
    "022_project_config",
    "023_project_comment_concluded_by",
    "024_comment_edit_delete_attachments",
    "025_comment_mentions",
    "026_team_runs",
)
# The basenames the fork used before the realignment (one number lower); none
# may survive. Derived from FORK_PAIRS so the denylist cannot drift from the
# migration set it guards.
STALE_FILES = tuple(
    f"{int(base[:3]) - 1:03d}{base[3:]}.{suffix}"
    for base in FORK_PAIRS
    for suffix in ("sql", "pg.sql")
)

failures: list[str] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    if not passed:
        failures.append(label)
    print(f"  [{'OK ' if passed else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")


def discover_versions() -> dict[tuple[int, str], list[str]]:
    """Group by (version, dialect) — ``_discover`` treats the pair as one migration."""
    seen: dict[tuple[int, str], list[str]] = {}
    for path in sorted(MIGRATIONS.iterdir()):
        m = re.match(r"^(\d{3})_.*\.sql$", path.name)
        if m:
            dialect = "postgresql" if path.name.endswith(".pg.sql") else "sqlite"
            seen.setdefault((int(m.group(1)), dialect), []).append(path.name)
    return seen


def watermark(path: Path) -> int | None:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"UPDATE _schema_version SET version = (\d+);", text)
    return int(m.group(1)) if m else None


def main() -> int:
    print("1) one migration per (version, dialect)")
    versions = discover_versions()
    dupes = {k: names for k, names in versions.items() if len(names) > 1}
    ok("no duplicate version numbers", not dupes, str(dupes) if dupes else "")
    top = max((v for v, _ in versions), default=0)
    ok(f"max version is {EXPECTED_MAX}", top == EXPECTED_MAX, f"got {top}")

    print("2) upstream 019 keeps its number; the fork pair follows it")
    for name in UPSTREAM_PAIR:
        ok(f"{name} exists (upstream, must not be renumbered)", (MIGRATIONS / name).exists())
    for base in FORK_PAIRS:
        for suffix in ("sql", "pg.sql"):
            name = f"{base}.{suffix}"
            ok(f"{name} exists", (MIGRATIONS / name).exists())
    stale = [name for name in STALE_FILES if (MIGRATIONS / name).exists()]
    ok("no stale old-numbered migration file", not stale, str(stale) if stale else "")

    print("3) watermark matches the filename prefix")
    for name in (*UPSTREAM_PAIR, *(f"{b}.{s}" for b in FORK_PAIRS for s in ("sql", "pg.sql"))):
        path = MIGRATIONS / name
        if not path.exists():
            ok(f"{name} watermark", False, "file missing")
            continue
        want = int(name[:3])
        got = watermark(path)
        ok(f"{name} sets version {want}", got == want, f"got {got}")

    print("4) migrate.py is wired to 020 (and left upstream 019 alone)")
    src = MIGRATE_PY.read_text(encoding="utf-8")
    ok("helper reads 020_projects.pg.sql", '"020_projects.pg.sql"' in src)
    ok("helper reads 020_projects.sql", '"020_projects.sql"' in src)
    ok("watermark replace targets 20", '"UPDATE _schema_version SET version = 20;"' in src)
    ok("sqlite branch is `if version == 20:`", re.search(r"if version == 20:", src) is not None)
    ok(
        "no `if version == 19:` branch -- upstream 019 uses the generic SQL path",
        re.search(r"if version == 19:", src) is None,
    )
    ok(
        "`_ensure_projects_schema` is called in run_migrations",
        src.count("_ensure_projects_schema(db)") >= 2,
        f"calls={src.count('_ensure_projects_schema(db)')}",
    )
    ok("upstream's bridge-connections helper survived the merge",
       "_ensure_bridge_connections_schema" in src)
    ok("upstream's user-role work survived the merge",
       "_ensure_user_role_schema" in src or "if version == 18:" in src)

    print(f"5) schema-version assertions were re-bumped to {EXPECTED_MAX}")
    # Only ``tests/`` is scanned: the planning docs and the archived one-shot
    # renumber scripts mention the old numbers on purpose (they describe the
    # renumber), so a whole-repo grep reports them as false positives.
    stale_pat = (
        r"assert (v|version) == 25|schema_version\"\] == 25|runtime_schema_version\": 25"
    )
    files = subprocess.run(
        ["git", "grep", "-l", "-E", stale_pat, "--", "tests/"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
    ).stdout.split()
    ok("no test still asserts the old head 25", not files, str(files) if files else "")
    hits = subprocess.run(
        ["git", "grep", "-c", "-E",
         rf"assert (v|version) == {EXPECTED_MAX}|schema_version\"\] == {EXPECTED_MAX}"
         rf"|runtime_schema_version\": {EXPECTED_MAX}", "--", "tests/"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    ok(f"at least one test asserts the new head {EXPECTED_MAX}", bool(hits.strip()))
    print(f"     {EXPECTED_MAX}-assertions per file:")
    for line in hits.splitlines():
        print(f"       {line}")

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
