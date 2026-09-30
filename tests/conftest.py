"""Shared pytest fixtures."""

from __future__ import annotations

import logging
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules whose tests boot OctopServer, real harness, browser tooling, or bwrap.
# Tagged ``slow`` so ``make test-fast`` can skip them during local iteration.
_SLOW_TEST_MODULES = frozenset(
    {
        "tests/unit/browser/test_browser_setup.py",
        "tests/unit/infra/utils/test_bwrap.py",
        "tests/unit/agents/test_skills_hub_raw.py",
        "tests/unit/agents/test_skillhub_market.py",
        "tests/unit/gateway/test_attachment_hints.py",
        "tests/unit/agents/test_agent_manager.py",
        "tests/unit/agents/test_agent_registry.py",
        "tests/unit/api/test_jwt_auth_middleware.py",
        "tests/unit/api/test_openapi_meta.py",
        "tests/unit/api/test_exception_handlers.py",
    },
)


def _module_path(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        module = _module_path(item.nodeid)
        if module in _SLOW_TEST_MODULES or module.startswith("tests/integration/"):
            item.add_marker(pytest.mark.slow)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


# Creating an agent now starts a multi-hour asyncio.sleep (proactive care
# defaults to on). pytest-asyncio waits for leftover tasks before fixture
# teardown, so any test that boots OctopServer without going through
# ``octop_client`` would hang the suite. Scheduler unit tests opt out.
_PROACTIVE_SCHEDULER_TESTS = "tests/unit/proactive/test_scheduler.py"


@pytest.fixture(autouse=True)
def _suspend_proactive_care_loops(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if _module_path(request.node.nodeid) == _PROACTIVE_SCHEDULER_TESTS:
        return
    from octop.infra.proactive.scheduler import ProactiveCareScheduler

    monkeypatch.setattr(ProactiveCareScheduler, "ensure_scheduled", lambda self, _id: None)

    async def _start_all(self: Any) -> None:
        return None

    monkeypatch.setattr(ProactiveCareScheduler, "start_all", _start_all)
    monkeypatch.setattr(ProactiveCareScheduler, "_schedule", lambda self, _id: None)


@pytest.fixture(autouse=True)
def _isolated_user_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Per-test HOME outside the test's ``tmp_path``.

    Keeping HOME off ``tmp_path`` avoids polluting directory listings and
    colliding with tests that create ``tmp_path / \"home\"`` themselves.
    Required for xdist so workers never share ``~/.octop`` / CLI state.
    """
    home = tmp_path_factory.mktemp("user-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in (
        "OCTOP_CAPTCHA_PROVIDER",
        "OCTOP_CAPTCHA_SITE_KEY",
        "OCTOP_CAPTCHA_SECRET",
        "OCTOP_CAPTCHA_V3_MIN_SCORE",
    ):
        monkeypatch.delenv(key, raising=False)
    return home


@pytest.fixture
def tmp_octop_home(_isolated_user_home: Path) -> Iterator[Path]:
    """Redirect ``~/.octop`` under the autouse isolated HOME."""
    octop = _isolated_user_home / ".octop"
    octop.mkdir(exist_ok=True)
    yield octop


@pytest.fixture(autouse=True)
def _isolated_plugin_registry() -> Iterator[None]:
    """Reset the **process-wide** plugin registry around every test.

    ``octop_harness.plugins.PluginRegistry`` is a singleton: whatever a test loads into
    it stays visible to every later test in the same worker, and that visibility reaches
    the product path, not just the tests.

    **Why this net has to exist** (mechanism, measured — A-BLOCK 定性, pre-existing):

    * ``infra/agents/plugins/plugin_tool_defaults.py:98-102`` skips a plugin only when
      ``global_plugins.get(pid) is False``. A **missing key is not ``False``**, and
      ``agent_plugin_enabled`` defaults to ``True`` — so a plugin that is merely
      *registered* while absent from ``global_plugins`` is attached to **every** agent
      built afterwards.
    * Two measured polluters, each leaving its plugin behind:
      - ``tests/integration/test_plugin_tool_disable.py::test_disable_plugin_tool_via_admin_plugins_api``
        → residual ``echo-tool``;
      - ``tests/unit/test_bundled_plugins_layout.py::test_market_install_copies_from_catalog``
        → residual ``air-quality`` (an extra ``get_air_quality`` tool).
    * Victims: ``tests/unit/agents/test_agent_manager.py::test_build_harness_config_includes_search_knowledge_without_cron``
      and ``::test_build_harness_config_includes_cronjob_tools_when_cron_manager_set``.
      Minimal trios (polluter + both victims, ``-p no:randomly``) fail ``2 failed, 1 passed``
      before this fixture and pass afterwards.
    * Why it stayed hidden: that layout file's **last** test
      (``test_offline_bundled_plugins_load``) ends in ``finally: PluginRegistry.reset()``,
      so any run that also executes it happens to clean up; ``-n`` parallelism hides the
      leak behind process isolation; and ``PluginManager.load_installed()`` begins with
      ``PluginRegistry().clear()`` (``plugins/manager.py:397``) — the only incidental
      cleaner, and only on that one code path.

    **Scope of ``reset()``** — it drops the singleton (``cls._instance = None``), so the
    next ``PluginRegistry()`` starts empty. It does **not** mutate an instance someone
    already holds, and it does not touch plugin config (``config.json → plugins``) or
    on-disk plugin directories. That is exactly the scope needed: polluters register into
    the singleton, victims read it through a *fresh* ``PluginRegistry()`` lookup.

    **No assertion semantics change.** The files that already reset locally
    (``tests/unit/test_plugin_seed.py``, ``tests/unit/test_plugins.py``,
    ``tests/unit/test_plugin_manager.py``) keep their own autouse fixtures — they remain
    self-contained when selected alone — and this global net is strictly additive.
    """
    from octop_harness.plugins import PluginRegistry

    PluginRegistry.reset()
    yield
    PluginRegistry.reset()


def _clear_qcc_metadata_cache() -> None:
    """Empty the QCC metadata TTL cache through **its own** reset inlet.

    ``infra/connectors/qcc.py:51 clear_metadata_cache()`` is the object's own way to
    drop ``_metadata_ok_until`` (``:36``, written at ``:69``). Calling the module's
    function — rather than snapshotting and writing back the old dict — is deliberate:
    see the fixture docstring for why snapshot/restore was rejected.
    """
    from octop.infra.connectors import qcc

    qcc.clear_metadata_cache()


def _resync_package_attributes() -> int:
    """Realign ``octop``/``octop.*`` parent-package attributes with ``sys.modules``.

    The import system binds a submodule onto its parent package, and deleting the
    ``sys.modules`` entry does **not** roll that binding back. Restoring the invariant
    therefore takes both directions, and either one alone leaves a split:

    * **present** — a name in ``sys.modules`` whose parent's attribute points elsewhere:
      point the attribute at the ``sys.modules`` object, which is what
      ``importlib.import_module`` returns.
      ``{sys.modules["pkg.child"], pkg.child}`` agree afterwards.
    * **absent** — a ``sys.modules`` entry that was deleted while the parent attribute
      still holds the old module: **drop the dangling attribute**. Without this the
      ``from pkg import child`` fallback finds the stale attribute and never re-imports,
      while ``importlib.import_module`` does re-import — producing a second live object
      from the same source file (measured: at the victim's setup the entry was
      ``None`` while the attribute still held the previous test's module object).

    Returns the number of attributes actually realigned or dropped (``0`` when nothing
    was split).

    Scope: the ``octop`` package tree only. A bare ``startswith("octop")`` would also
    match sibling distributions such as ``octop_harness``, which this net does not own.
    Nothing is imported, reloaded or read from disk: a name absent from ``sys.modules``
    is never resurrected, only unbound; and only attributes that hold a *module object
    that is not in* ``sys.modules`` are dropped (a live alias to a registered module is
    left alone).
    """
    changed = 0
    packages: list[Any] = []
    # One **keys-only** snapshot serves both rules: ``list(sys.modules)`` copies names
    # (~0.01 ms at 4.6k modules) whereas ``list(sys.modules.items())`` builds 4.6k tuples
    # (~0.45 ms) — measured, and this runs twice per test, so the cheap form is what keeps
    # the net affordable. Modules are then fetched per octop-tree name (≈250 of them).
    for name in list(sys.modules):
        if name != "octop" and not name.startswith("octop."):
            continue
        module = sys.modules.get(name)
        if module is None:
            continue
        parent_name, _, child_name = name.rpartition(".")
        if parent_name:
            parent = sys.modules.get(parent_name)
            if parent is not None and getattr(parent, child_name, None) is not module:
                setattr(parent, child_name, module)
                changed += 1
        if hasattr(module, "__path__"):  # packages own submodules
            packages.append(module)

    for parent in packages:
        for child_name, child in list(vars(parent).items()):
            if not isinstance(child, types.ModuleType):
                continue
            if sys.modules.get(child.__name__) is child:
                continue  # a registered module, also reachable under this name: keep
            try:
                delattr(parent, child_name)
            except AttributeError:  # pragma: no cover - already gone
                continue
            changed += 1
    return changed


def _reset_module_state() -> None:
    """Run both resets. **Never raises** — but never swallows silently either.

    ``S7``: this net exists to stop other tests' leftovers from turning a green case
    red, so a failure *inside the net* must not do that itself. It is therefore wrapped
    whole and reported through ``logger.warning(..., exc_info=True)`` — a bare
    ``except: pass`` would hide a broken net, which is the one failure mode nobody would
    notice until the victims came back.
    """
    try:
        _clear_qcc_metadata_cache()
        changed = _resync_package_attributes()
        if changed:
            logger.debug("module-state net realigned %d package attribute(s)", changed)
    except Exception:  # noqa: BLE001 - a broken net must not fail the test it protects
        logger.warning("module-state isolation net failed; state left as-is", exc_info=True)


@pytest.fixture(autouse=True)
def _isolated_module_state() -> Iterator[None]:
    """Reset **process-wide module state** around every test (before *and* after).

    Two leftovers survive a completed test in the same worker and reach later tests —
    both measured, both pre-existing, neither covered by the plugin-registry net above
    (``_isolated_plugin_registry``) nor by ``_isolated_user_home``:

    * **V1 — a module-level TTL cache.** ``infra/connectors/qcc.py:36``
      ``_metadata_ok_until: dict[str, float]`` is mutable module state, written at
      ``:69``. ``tests/unit/connectors/test_qcc_grant.py::test_resource_metadata_is_cached``
      leaves ``risk -> now + 600`` in it: that file's ``mcp_http`` fixture (``:49-98``)
      clears the cache in **setup only** (``:95``) and has **no teardown**. The victim
      ``tests/unit/connectors/test_qcc_oauth_grant.py::test_metadata_mismatch_blocks_bearer_transport``
      (``:340``) neither uses that fixture nor clears anything, so the cache hits, the
      metadata check is short-circuited (``qcc.py:58``) and the expected ``ValueError``
      never fires. Minimal pair before this fixture: ``1 failed, 1 passed``.
    * **V2 — one module, two live objects in one process.** The import system binds a
      submodule onto its parent package; ``tests/unit/test_cli_main.py::test_root_help_does_not_import_subcommand_modules``
      (``:167-186``) deletes the ``sys.modules['octop.cli.commands.*']`` entries **without**
      clearing the parent's attributes, so ``octop.cli.commands.memory`` is left *absent*
      from ``sys.modules`` while the package attribute still holds the previous test's
      module object. The victim
      ``tests/unit/agents/test_memory_slim.py::test_empty_discovery_exits_without_a_prompt``
      (``:337``) does ``from octop.cli.commands import memory`` — with the attribute
      present the ``from X import Y`` fallback never re-imports, so its ``monkeypatch``
      lands on that stale object — while ``src/octop/cli/main.py:65-73 _LazyCLI.get_command``
      resolves through ``importlib.import_module``, which **does** re-import the file and
      executes the new object, real code and all. Minimal triple before this fixture:
      ``1 failed, 2 passed``.

    **Why the net has to be global and autouse.** The two polluters live in different
    places relative to their victims — one in a sibling file of the same directory, one
    in a different test root entirely — so a directory-level ``conftest.py`` would catch
    at most the first. A fixture with the same name in ``tests/unit/conftest.py`` would
    shadow this one and silently produce a false green.

    **Why the reset calls the object's own inlet instead of restoring an old value.**
    ``clear_metadata_cache()`` expresses exactly "this cache is empty"; snapshot/restore
    would fabricate states production never has (the same reasoning as
    ``captcha/config.py:13 _INSTALLED``, where ``None`` means "recompute from env" and
    writing back an old value would be a lie). The resync restores one invariant —
    "parent-package attribute <-> ``sys.modules`` entry" — in both directions: where the
    entry exists the attribute is re-pointed at it, and where the entry is **gone** the
    dangling attribute is unbound (never a module resurrected from nowhere). It imports
    nothing, reloads nothing, touches no disk, and is a no-op when the two already agree.

    **Scope of each step.** ``clear_metadata_cache()`` empties that one TTL dict; it does
    not touch HTTP clients, credentials, or cached responses on disk. The resync covers
    ``octop``/``octop.*`` only, keeps live aliases to registered modules, and never
    re-creates a module that ``sys.modules`` no longer has.

    **No assertion semantics change.** The two files that leak are deliberately left
    untouched — they are the evidence this net exists — and per-file isolation fixtures
    (e.g. ``tests/unit/auth/test_captcha_env.py:10``) keep working; this net is strictly
    additive, and the helpers above never raise into the test (see ``_reset_module_state``).
    """
    _reset_module_state()
    yield
    _reset_module_state()
