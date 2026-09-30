"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

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
