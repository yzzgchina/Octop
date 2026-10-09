# Makefile for Octop
# Usage:
#   make help               - Show this help (default goal)
#
# Ship bar / release:
#   make all                - format-all + lint + lint-frontend + typecheck + test + test-frontend (ship bar)
#   make build              - Build frontend + Python wheel
#   make publish            - Build + upload to PyPI
#   make build-frontend     - npm ci + npm run build -> src/octop/dashboard
#   make build-wheel        - Build Python wheel + sdist (frontend must already be built)
#   make publish-test       - Build + upload to TestPyPI (make publish PYPI_REPO=testpypi)
#
# Gate targets:
#   make precommit          - format-all + lint + lint-frontend + typecheck + test-affected + test-frontend (git hook gate)
#   make test-affected      - pytest --testmon; prints cold-start vs baseline, cold start runs the full backend suite once
#   make audit              - read-only tree-discipline audit (scripts/audit/current_tree.py); standalone, NOT in all / check-all / CI
#   make check-all          - lint-all + typecheck-all + test + test-frontend
#
# Quality (backend):
#   make lint               - ruff check + ruff format --check (src, tests)
#   make format             - ruff check --fix + ruff format (src, tests)
#   make typecheck          - mypy src/octop (strict via [tool.mypy] in pyproject.toml)
#   make test               - pytest -n PYTEST_JOBS -m "not live"
#   make test-fast          - pytest -n PYTEST_JOBS -m "not live and not slow"
#   make test-live          - pytest -m live
#
# Quality (frontend):
#   make lint-frontend      - npm run lint (ESLint) + npm run format:check (Prettier check)
#   make format-frontend    - npm run format (Prettier write)
#   make typecheck-frontend - npx tsc -b
#   make test-frontend      - npx vitest run + scripts/vitest-fail-classify.py (exit 1/2/3 = failure)
#
# Quality (full stack):
#   make lint-all           - lint + lint-frontend
#   make format-all         - format + format-frontend (first leg of make all)
#   make typecheck-all      - typecheck + typecheck-frontend
#
# Development:
#   make dev                - dashboard dev server + octop run together
#   make dev-frontend       - Vite dev server only
#   make dev-backend        - octop run only
#
# Online-deps (.venv-online = local Octop source + harness-* from PyPI):
#   make install-online     - Create .venv-online (octop editable + harness-* from PyPI)
#   make test-online        - pytest -m "not live" against .venv-online
#   make run-online         - octop run from .venv-online
#
# Utilities:
#   make install-hooks      - Point git at .githooks (pre-commit runs make precommit + dashboard build)
#   make install            - Install Python dev dependencies (uv sync / pip install -e ".[dev]")
#   make install-dev        - Same rule as install
#   make install-tools      - Install build + twine for publishing
#   make clean              - Remove build artifacts and caches
#   make clean-online       - Remove the .venv-online environment
#   make version            - Print the project version from pyproject.toml
#
# Prerequisites:
#   - Node.js + npm   (frontend)
#   - Python 3.12+    (backend)
#   - uv              (recommended) or pip
#   - twine           (publish: pip install twine / make install-tools)

SHELL := /bin/bash
.DEFAULT_GOAL := help

REPO_ROOT     := $(shell pwd)
DASHBOARD_DIR := $(REPO_ROOT)/dashboard

# ★ 显式串行（批次十四 `T-B-GATE` ⑧）：`all` 的依赖里既有后端 `test`（pytest 全量）
# 也有前端 `test-frontend`（vitest）。make **默认**就是串行，但一旦有人 `make -j`，
# 两者会**并发** ⇒ 前端 collect/tests 超时更紧，而 `AuthGuard` 的 flake 正是**负载敏感**的
# （实测：并发负载下会红）⇒ 这里显式声明**不可并行**，行为与默认**等价**。
.NOTPARALLEL:
DASHBOARD_DEST := $(REPO_ROOT)/src/octop/dashboard
DIST_DIR      := $(REPO_ROOT)/dist

# PyPI repository (override: make publish PYPI_REPO=testpypi)
PYPI_REPO  ?= pypi
PYPI_TOKEN ?=

# Prefer uv when available; fall back to plain commands (CI / pip install -e ".[dev]")
UV   := $(shell command -v uv 2>/dev/null)
RUN  := $(if $(UV),uv run,)
PYTHON := $(if $(UV),uv run python,python3)
PIP  := $(if $(UV),uv pip,python3 -m pip)
# Parallel workers for make test / make test-fast (override: make test PYTEST_JOBS=4)
PYTEST_JOBS ?= auto

# ─── Help ────────────────────────────────────────────────────────────────────

.PHONY: help
help:
	@echo "Octop Build System"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Build targets:"
	@echo "  build            Build frontend + Python wheel (full package)"
	@echo "  build-frontend   Build React dashboard only → $(DASHBOARD_DEST)"
	@echo "  build-wheel      Build Python wheel only (assumes frontend built)"
	@echo ""
	@echo "Publish targets:"
	@echo "  publish          Build + upload to PyPI (PYPI_TOKEN or prompt)"
	@echo "  publish-test     Build + upload to TestPyPI"
	@echo ""
	@echo "Development targets:"
	@echo "  dev              Start frontend + backend dev servers"
	@echo "  dev-frontend     Start Vite dev server only"
	@echo "  dev-backend      Start octop run only"
	@echo ""
	@echo "Online-deps targets (local Octop source + PyPI harness components):"
	@echo "  install-online   Create .venv-online: Octop editable + harness-* from PyPI"
	@echo "  test-online      pytest against .venv-online (not live)"
	@echo "  run-online       Start octop run from .venv-online"
	@echo ""
	@echo "Quality targets (ship bar):"
	@echo "  all              format-all + lint + typecheck + test (backend lint/typecheck/test)"
	@echo "  precommit        fast change-aware gate for the git pre-commit hook (testmon-scoped tests)"
	@echo "  lint             Ruff check + format check (src, tests)"
	@echo "  format           Ruff auto-fix + format (src, tests)"
	@echo "  typecheck        mypy --strict src/octop"
	@echo "  test             pytest -m \"not live\""
	@echo "  test-live        pytest -m live"
	@echo ""
	@echo "Quality targets (frontend):"
	@echo "  lint-frontend    ESLint + Prettier check"
	@echo "  format-frontend  Prettier write"
	@echo "  typecheck-frontend  tsc -b (project references)"
	@echo ""
	@echo "Quality targets (full stack):"
	@echo "  lint-all         lint + lint-frontend"
	@echo "  format-all       format + format-frontend (also first step of make all)"
	@echo "  typecheck-all    typecheck + typecheck-frontend"
	@echo "  check-all        lint-all + typecheck-all + test"
	@echo ""
	@echo "Utility targets:"
	@echo "  install-hooks    Point git to .githooks (pre-commit: make precommit + dashboard build)"
	@echo "  install          Install Python dev dependencies (alias: install-dev)"
	@echo "  install-dev      uv sync / pip install -e \".[dev]\""
	@echo "  install-tools    Install build + twine for publishing"
	@echo "  clean            Remove build artifacts and caches"
	@echo "  clean-online     Remove the .venv-online environment"
	@echo "  version          Show current project version"

# ─── Build ───────────────────────────────────────────────────────────────────

.PHONY: build
build: build-frontend build-wheel

.PHONY: build-frontend
build-frontend:
	@echo "[build-frontend] Installing npm dependencies..."
	cd $(DASHBOARD_DIR) && npm ci
	@echo "[build-frontend] Building dashboard (output: $(DASHBOARD_DEST))..."
	cd $(DASHBOARD_DIR) && NODE_ENV=production NODE_OPTIONS="--max-old-space-size=2048" npm run build
	@echo "[build-frontend] Done."

.PHONY: build-wheel
build-wheel:
	@echo "[build-wheel] Generating README.pypi.md (CHANGELOG + README)..."
	@{ cat $(REPO_ROOT)/CHANGELOG.md; echo; cat $(REPO_ROOT)/README.md; } > $(REPO_ROOT)/README.pypi.md
	@cp $(REPO_ROOT)/pyproject.toml $(REPO_ROOT)/pyproject.toml.bak
	@$(PYTHON) -c "from pathlib import Path; p=Path('$(REPO_ROOT)/pyproject.toml'); p.write_text(p.read_text().replace('readme = \"README.md\"', 'readme = \"README.pypi.md\"', 1))"
	@echo "[build-wheel] Cleaning previous build artifacts..."
	rm -rf $(DIST_DIR)/*
	rm -rf $(REPO_ROOT)/build
	@echo "[build-wheel] Building wheel + sdist..."
	@set -e; \
	status=0; \
	if [ -n "$(UV)" ]; then \
		uv build --out-dir $(DIST_DIR) . || status=$$?; \
	else \
		$(PIP) install --quiet build; \
		$(PYTHON) -m build --no-isolation --outdir $(DIST_DIR) . || status=$$?; \
	fi; \
	mv $(REPO_ROOT)/pyproject.toml.bak $(REPO_ROOT)/pyproject.toml; \
	rm -f $(REPO_ROOT)/README.pypi.md; \
	exit $$status
	@echo "[build-wheel] Done. Artifacts in: $(DIST_DIR)/"
	@ls -lh $(DIST_DIR)/

# ─── Publish ─────────────────────────────────────────────────────────────────

.PHONY: publish
publish: build
	@echo "[publish] Uploading to $(PYPI_REPO)..."
	@if ! command -v twine > /dev/null 2>&1; then \
		echo "[publish] twine not found. Installing..."; \
		$(PIP) install --quiet twine; \
	fi
	@_token="$(PYPI_TOKEN)"; \
	if [ -z "$$_token" ]; then \
		echo ""; \
		printf "  PYPI_TOKEN is not set. Enter your PyPI API token (pypi-xxx): "; \
		read -r _token; \
		if [ -z "$$_token" ]; then \
			echo "[publish] No token provided. Aborting."; \
			exit 1; \
		fi; \
	fi; \
	$(if $(UV),uv run,) twine upload \
		$(if $(filter testpypi,$(PYPI_REPO)),--repository testpypi,) \
		--username __token__ --password "$$_token" \
		$(DIST_DIR)/*
	@echo "[publish] Upload complete."

.PHONY: publish-test
publish-test:
	$(MAKE) publish PYPI_REPO=testpypi

# ─── Development ─────────────────────────────────────────────────────────────

.PHONY: dev
dev:
	@echo "[dev] Starting frontend and backend dev servers (Ctrl-C to stop both)..."
	@trap 'kill 0' SIGINT; \
	(cd $(DASHBOARD_DIR) && npm run dev) & \
	($(RUN) octop run) & \
	wait

.PHONY: dev-frontend
dev-frontend:
	cd $(DASHBOARD_DIR) && npm run dev

.PHONY: dev-backend
dev-backend:
	$(RUN) octop run

# ─── Online-deps dev (local Octop source + PyPI harness components) ───────────
# Keeps a dedicated .venv-online alongside .venv. Octop itself is installed
# editable (-e) so source edits are live; the harness-* siblings are pulled
# from PyPI (--no-sources ignores the editable local paths in uv.lock).
# Re-run install-online only when a dependency version changes.
VENV_ONLINE := $(REPO_ROOT)/.venv-online
PY_ONLINE   := $(VENV_ONLINE)/bin

.PHONY: install-online
install-online:
	@echo "[install-online] Creating $(VENV_ONLINE) (local Octop + PyPI harness deps)..."
	uv venv $(VENV_ONLINE) --python 3.12
	uv pip install --no-sources --python $(PY_ONLINE)/python -e ".[dev]"
	@echo "[install-online] Done. Octop=editable(local), harness-*=PyPI."

.PHONY: test-online
test-online:
	@echo "[test-online] pytest against $(VENV_ONLINE) (not live)..."
	$(PY_ONLINE)/pytest -m "not live"

.PHONY: run-online
run-online:
	$(PY_ONLINE)/octop run

# ─── Quality (backend) ───────────────────────────────────────────────────────

.PHONY: all
all: format-all lint lint-frontend typecheck test test-frontend

.PHONY: lint
lint:
	@echo "[lint] Ruff check..."
	$(RUN) ruff check src tests
	@echo "[lint] Ruff format check..."
	$(RUN) ruff format --check src tests

.PHONY: format
format:
	@echo "[format] Ruff fix + format..."
	$(RUN) ruff check --fix src tests
	$(RUN) ruff format src tests

.PHONY: typecheck
typecheck:
	@echo "[typecheck] mypy..."
	$(RUN) mypy src/octop

.PHONY: test
test:
	@echo "[test] pytest (not live, -n $(PYTEST_JOBS))..."
	$(RUN) pytest -n $(PYTEST_JOBS) -m "not live"

.PHONY: test-frontend
# ★ 前端测试段（批次十四 `T-B-GATE` · `P1 = B`）：把 vitest 接进 `make all`。
# ★ 与既有 `build-frontend`（构建）**是两件事**，两条都保留。
# ★ 退出码语义由 `dashboard/scripts/vitest-fail-classify.py`（`D-8`）给出：★★ **四级**
#   有套件加载失败（`FAIL … [ … ]`）⇒ **1** · 仅用例失败（`FAIL … > …`）⇒ **2** · 全绿 ⇒ **0**
#   · ★ `counts` 报有失败而分类为空 ⇒ **1**（守卫 · `AC-9`）· ★★ 无输入 / 无法解析（`P2'` 不成立）⇒ **3**。
# ★ `2` 与 `3` 也是失败 ⇒ **不得** `|| true`。
# ★ `P1-19`：第三段解释器**不得**写裸 `python3` —— 本仓 CI 只声明 `setup-uv`（⇒ `uv run python`），
#   `python3` 是 Windows 镜像的**镜像内别名**、不是本仓工具链的保证 ⇒ 统一用 `$(PYTHON)`。
test-frontend:
	@echo "[test-frontend] vitest + fail-classifier (1 = suite load, 2 = case fail, 3 = no input)..."
	@set -o pipefail; cd $(DASHBOARD_DIR) && npx vitest run 2>&1 | tee /tmp/octop-vitest.log | $(PYTHON) scripts/vitest-fail-classify.py

.PHONY: test-fast
test-fast:
	@echo "[test-fast] pytest (not live, not slow, -n $(PYTEST_JOBS))..."
	$(RUN) pytest -n $(PYTEST_JOBS) -m "not live and not slow"

.PHONY: test-live
test-live:
	@echo "[test] pytest (live)..."
	$(RUN) pytest -m live

# Fast, change-aware gate for the local pre-commit hook. Runs ruff/mypy (cheap
# and kept full for accuracy) plus only the tests affected by changed files via
# pytest-testmon. The full suite still runs in CI (`make all`); this is local
# feedback only, so a missed cross-module impact is caught there.
# ★ `P1-9`：`test-frontend` 也在本目标里 —— 前端**没有**等价的 testmon 选择器，
#   所以 dashboard 套件每次提交都**全量**跑一遍（本机实测 ≈ 27s，225 文件 / 1428 用例）。
#   这是**有意的阻断**：前端红 ⇒ 钩子非 0 ⇒ 提交被挡（不是提示）。
.PHONY: precommit
precommit: format-all lint lint-frontend typecheck test-affected test-frontend

# NOTE: do NOT pass `-m` here — pytest-testmon deactivates its affected-test
# selection whenever a marker expression is present, which would fall back to
# the full suite. Live tests under tests/live/ auto-skip when credentials are
# absent, so running them unscoped is cheap and safe; the CI `make all` gate
# keeps `-m "not live"` for the authoritative full run.
#
# NOTE: testmon detects changes via `git ls-files -m` (worktree vs index), which
# is empty once changes are staged — exactly the pre-commit state — so it would
# silently run zero tests. The patch in conftest.py / tests/support/
# testmon_staged_changes.py redirects detection to `git diff HEAD` (staged +
# unstaged) so the gate actually fires on a commit's changes.
#
# ★ `P3-22`：**冷启动**（无 `.testmondata`，或该库里还没有任何 `test_execution`
#   记录 ⇒ 典型于「新克隆 / 刚删掉库」）时，testmon 在 `pytest_configure` 里
#   走 `first_build`：没有任何基线指纹 ⇒ **它必须跑满全量**才能建立基线
#   （本机实测 ≈1007 s / 5845 用例）。`P1-9` 两次实测的症状正与此吻合 ——
#   其证据明确记载首跑「钩子此前无 .testmondata」⇒ `5795 passed / 1007.19 s`。
#
#   本目标因此把两种状态**显式**打印出来，避免把「正在建基线的全量」误读成
#   「变更范围没生效」（本卡要消除的正是这个歧义）。★ 不删库、不跳过检查、
#   不削弱断言：冷启动的全量跑是 testmon 建立基线的**必要**一次；建好后每提交
#   只跑受影响用例。★ 稳态下命令仍是 `pytest --testmon`，与改前**逐字一致**。
.PHONY: test-affected
test-affected:
	@echo "[test] pytest (testmon: only tests affected by changes)..."
	@$(PYTHON) -c "import os, sqlite3; \
p = os.environ.get('TESTMON_DATAFILE', '.testmondata'); \
n = 0; \
exec('try:\n    n = sqlite3.connect(p).execute(\"select count(*) from test_execution\").fetchone()[0]\nexcept Exception:\n    n = 0') if os.path.exists(p) else None; \
print('[test] ★ testmon 无基线（冷启动）⇒ 本次跑满后端全量以建立基线（≈16 min，仅此一次）') if n == 0 else print('[test] testmon 基线：%d 条用例记录 ⇒ 只跑受影响用例' % n)"
	$(RUN) pytest --testmon

# ─── Quality (frontend) ──────────────────────────────────────────────────────

.PHONY: lint-frontend
lint-frontend:
	@echo "[lint-frontend] ESLint..."
	cd $(DASHBOARD_DIR) && npm run lint
	@echo "[lint-frontend] Prettier check..."
	cd $(DASHBOARD_DIR) && npm run format:check

.PHONY: format-frontend
format-frontend:
	@echo "[format-frontend] Prettier..."
	cd $(DASHBOARD_DIR) && npm run format

.PHONY: typecheck-frontend
typecheck-frontend:
	@echo "[typecheck-frontend] tsc..."
	cd $(DASHBOARD_DIR) && npx tsc -b

# ─── Quality (full stack) ────────────────────────────────────────────────────

.PHONY: lint-all
lint-all: lint lint-frontend

.PHONY: format-all
format-all: format format-frontend

.PHONY: typecheck-all
typecheck-all: typecheck typecheck-frontend

.PHONY: check-all
check-all: lint-all typecheck-all test test-frontend

# ─── Utilities ───────────────────────────────────────────────────────────────

.PHONY: install-hooks
install-hooks:
	@echo "[install-hooks] Setting core.hooksPath=.githooks"
	git config core.hooksPath .githooks
	@chmod +x "$(REPO_ROOT)/.githooks/"* 2>/dev/null || true
	@echo "[install-hooks] Done. Pre-commit will run: make precommit (format-all + lint + typecheck + testmon-scoped tests), dashboard build"
	@echo "[install-hooks] Bypass: SKIP_PRECOMMIT=1 git commit …   or   git commit --no-verify"

.PHONY: install install-dev
install install-dev:
	@echo "[install] Installing Python dev dependencies..."
ifdef UV
	uv sync
else
	$(PIP) install -e ".[dev]"
endif

.PHONY: install-tools
install-tools:
	$(PIP) install --quiet build twine
	@echo "[install-tools] build and twine installed."

.PHONY: clean
clean:
	@echo "[clean] Removing build artifacts..."
	rm -rf $(DIST_DIR)/*
	rm -rf $(DASHBOARD_DIR)/dist
	rm -rf $(REPO_ROOT)/build
	rm -rf $(REPO_ROOT)/*.egg-info
	rm -rf $(REPO_ROOT)/src/*.egg-info
	rm -rf $(REPO_ROOT)/.mypy_cache $(REPO_ROOT)/.ruff_cache $(REPO_ROOT)/.pytest_cache
	rm -rf $(REPO_ROOT)/htmlcov
	find $(REPO_ROOT)/src -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "[clean] Done."

.PHONY: clean-online
clean-online:
	@echo "[clean-online] Removing $(VENV_ONLINE)..."
	rm -rf $(VENV_ONLINE)
	@echo "[clean-online] Done."

.PHONY: version
version:
	@$(PYTHON) -c "import pathlib, re; t = pathlib.Path('pyproject.toml').read_text(); m = re.search(r'^version\\s*=\\s*\"([^\"]+)\"', t, re.M); print(m.group(1) if m else 'unknown')"

# ─── Audit（★ 独立目标 · 不并进 all · 只读）─────────────────────────────────

.PHONY: audit
audit:
	@echo "[audit] 当前树纪律审计（只读 · 独立目标）..."
	$(PYTHON) scripts/audit/current_tree.py
