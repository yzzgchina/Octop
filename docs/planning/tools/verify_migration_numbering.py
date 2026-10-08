"""Verify migration numbering — branch-agnostic, no hard-coded version.

Run this after every rebase / merge onto a moving ``develop``. The version
ceiling is **read from ``src/octop/infra/db/migrations/``**, never pinned, so the
same script is valid on any branch at any water level (025 on
``fix/audit-directions-3to6``, 027 on ``feature/task-card-content-fixups``, ...).

Kept discriminations — each of these turns the script red:

  1. two migrations sharing a (version, dialect) pair — ``migrate._discover``
     raises at runtime, so this breaks every DB operation, not just one test
  2. a gap in the ``001..max`` sequence (e.g. 017 missing between 001 and 025)
  3. an unpaired migration — ``00N_x.sql`` without ``00N_x.pg.sql`` (or vice
     versa), including a stem mismatch between the two dialects
  4. a migration whose embedded ``_schema_version`` watermark disagrees with its
     own filename prefix (``001_initial`` legitimately carries no watermark)
  5. the schema-version assertions in ``tests/unit/db/test_db_pool.py`` not
     bumped to the current max — the fixup that always follows a new migration

Retired on purpose (this is why the ceiling became dynamic): the 018 -> 019
renumber fixups — the hard-coded ``019_projects`` pair, ``migrate.py`` wired to
``19``, and "no test asserts 18". They could only ever be true for one branch at
one instant, and they are renumber-specific rather than numbering checks. In
particular "the migrate helper is wired to the max version" is *not* an
invariant: 025_team_runs on this branch has no ``if version == 25`` helper and
relies on the generic ``_discover`` loop.

Usage::

    python docs/planning/tools/verify_migration_numbering.py [repo_root]

``repo_root`` defaults to the repository this script lives in.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DIALECTS = ("sqlite", "postgresql")
TEST_DB_POOL = "tests/unit/db/test_db_pool.py"

failures: list[str] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    if not passed:
        failures.append(label)
    print(f"  [{'OK ' if passed else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")


def repo_root(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1]).expanduser().resolve()
    # docs/planning/tools/<this file> -> repository root
    return Path(__file__).resolve().parents[3]


def collect(migrations: Path) -> dict[int, dict[str, list[str]]]:
    """Map ``version -> dialect -> stems``.

    The dialect split mirrors ``migrate._discover``: ``*.pg.sql`` is PostgreSQL
    and is not also counted as SQLite; every other ``NNN_*.sql`` is SQLite.
    """
    table: dict[int, dict[str, list[str]]] = {}
    if not migrations.is_dir():
        return table
    for path in sorted(migrations.iterdir()):
        name = path.name
        if not name.endswith(".sql"):
            continue
        m = re.match(r"^(\d{3})_(.*)\.sql$", name)
        if not m:
            continue
        version = int(m.group(1))
        stem = m.group(2)
        dialect = "sqlite"
        if stem.endswith(".pg"):
            stem = stem[: -len(".pg")]
            dialect = "postgresql"
        table.setdefault(version, {}).setdefault(dialect, []).append(stem)
    for per_dialect in table.values():
        for stems in per_dialect.values():
            stems.sort()
    return table


def file_name(version: int, dialect: str, stem: str) -> str:
    return f"{version:03d}_{stem}" + (".pg.sql" if dialect == "postgresql" else ".sql")


def main() -> int:
    root = repo_root(sys.argv)
    migrations = root / "src/octop/infra/db/migrations"
    test_db_pool = root / TEST_DB_POOL
    print(f"repo root : {root}")
    print(f"migrations: {migrations}")

    table = collect(migrations)
    ok("migration directory is readable and non-empty", bool(table), f"versions={len(table)}")

    print("1) one migration per (version, dialect)   [mirrors migrate._discover]")
    dupes = {
        f"{version:03d}/{dialect}": stems
        for version, per_dialect in table.items()
        for dialect, stems in per_dialect.items()
        if len(stems) > 1
    }
    ok("no duplicate version numbers", not dupes, str(dupes) if dupes else "")

    print("2) the version sequence is contiguous from 001 (no gap)")
    gaps: dict[str, list[str]] = {}
    ceilings: dict[str, int] = {}
    for dialect in DIALECTS:
        present = sorted(v for v, per in table.items() if dialect in per)
        ceilings[dialect] = present[-1] if present else 0
        missing = sorted(set(range(1, ceilings[dialect] + 1)) - set(present))
        if missing:
            gaps[dialect] = [f"{v:03d}" for v in missing]
    ok("no gap in the version sequence", not gaps, str(gaps) if gaps else "")

    print("3) every NNN_<stem>.sql has a matching NNN_<stem>.pg.sql, and vice versa")
    unpaired = {
        f"{version:03d}": f"sqlite={per.get('sqlite', [])} postgresql={per.get('postgresql', [])}"
        for version, per in sorted(table.items())
        if per.get("sqlite", []) != per.get("postgresql", [])
    }
    ok("sqlite/postgresql pairs match by stem", not unpaired, str(unpaired) if unpaired else "")

    print("4) version ceiling (read dynamically — never hard-coded)")
    tuned = sorted(set(ceilings.values()) - {0})
    ok(
        "both dialects agree on the ceiling",
        len(tuned) == 1,
        f"sqlite={ceilings['sqlite']:03d} postgresql={ceilings['postgresql']:03d}",
    )
    ceiling = tuned[0] if len(tuned) == 1 else max(tuned, default=0)
    print(f"     max version = {ceiling:03d} (read from {migrations})")

    print("5) each migration's _schema_version watermark matches its own prefix")
    bad_watermarks: list[str] = []
    for version, per_dialect in sorted(table.items()):
        for dialect, stems in sorted(per_dialect.items()):
            for stem in stems:
                name = file_name(version, dialect, stem)
                text = (migrations / name).read_text(encoding="utf-8")
                for value in re.findall(r"UPDATE _schema_version SET version = (\d+);", text):
                    if int(value) != version:
                        bad_watermarks.append(f"{name}: watermark {value} != prefix {version:03d}")
    ok("watermarks match their filename prefix", not bad_watermarks, "; ".join(bad_watermarks))

    print(f"6) {TEST_DB_POOL} asserts the current ceiling")
    if not test_db_pool.is_file():
        ok(f"{TEST_DB_POOL} exists", False)
    else:
        text = test_db_pool.read_text(encoding="utf-8")
        hits = re.findall(r"assert (?:v|version) == (\d+)", text)
        counts: dict[str, int] = {}
        for value in hits:
            counts[value] = counts.get(value, 0) + 1
        ok(
            f"schema-version assertions were bumped to {ceiling}",
            str(ceiling) in counts,
            f"asserted versions: {dict(sorted(counts.items(), key=lambda kv: int(kv[0])))}",
        )
        print(f"     {ceiling}-assertions in {test_db_pool.name}: {counts.get(str(ceiling), 0)}")

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
