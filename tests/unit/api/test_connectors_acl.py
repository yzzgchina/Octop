"""P1-8: connectors router 的**权限位**与**资源归属**判别性对照。

本文件守两条线，每条都同时给出「越权被拒 / 合法通过 / 机判冻结」：

**(A) 资源归属 —— `cli_config_key` 是宿主机目录键**

``cli_config_key`` 指向宿主机目录 ``~/.octop/connector-cli/<kind>/<key>/``
（``ConnectorService.feishu_cli_config_dir``），并且这个目录**与 connector 实例共享**：
``resolve_cli_config_key`` 先取 ``cli_config_key``、再退化到 ``instance_id``，而
``cleanup_keys_for_creds`` 对 ``feishu-cli`` 同时列出这两者（注释逐字：
「``instance_id``: legacy dirs only (pre-isolation)」）。因此 ``instance_id`` 就是一把
**承载归属的键** —— 而 ``GET /api/connector-instances`` 会把 ``shared=1`` 实例的
``instance_id`` 交给**非 owner**（同文件已有的对照用例断言 ``can_manage is False``）。

改动前：任何已登录用户都能把 ``cli_config_key`` 设成他人的 ``instance_id``，从而让自己
的连接器 CLI 读写/清理 ``~/.octop/connector-cli/<kind>/<他人的键>/``。
改动后：该键已命名一个 CLI-kind 的行 ⇒ 必须过 ``_assert_can_manage_connector``（403）。

**(B) 权限位 —— `connectors` 模块键**

前端已按 ``connectors`` 键 gated 整个模块面（``dashboard/src/routes/index.tsx:61``
把 ``/connectors`` 映射到 ``connectors``；``sidebarNav.tsx:185`` 用它隐藏导航项），
而 ``connectors`` 属于 ``BASELINE_PERMISSIONS``（新用户默认持有）。
但后端此前**一个权限位都没有**（只有 ``connector_install_cli`` 有）⇒
「前端隐藏 ≠ 后端拒绝」：被撤销该键的用户仍可直连 HTTP 建实例、落第三方凭据、
发起 OAuth、改写 custom-MCP 文档。

**刻意不门禁**（逐条理由见 ``test_connector_read_surface_stays_open_without_permission``）：
``GET /connector-instances``（Chat 输入框 ``useChatComposerResources.ts:201`` 与
项目面板 ``ConnectorsPanel.tsx:63`` 都要用，不能被 ``connectors`` 键挡住）、
``GET /connectors/catalog`` 等无资源元数据读。
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, create_user

INSTANCES = "/api/connector-instances"
CUSTOM_MCP = "/api/connectors/custom-mcp"

FEISHU_CREDS = {"app_id": "cli_app_id", "app_secret": "cli_app_secret"}


@pytest.fixture
async def api(
    tmp_octop_home: pathlib.Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]]]:
    async with octop_client(tmp_octop_home) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        admin = await auth_header(client)
        # alice / bob 拿 BASELINE_PERMISSIONS（含 ``connectors``）；
        # carol 显式不带任何模块键。
        alice = await create_user(client, admin, username="alice")
        bob = await create_user(client, admin, username="bob")
        carol = await create_user(client, admin, username="carol", permissions=[])
        yield client, srv, {"admin": admin, "alice": alice, "bob": bob, "carol": carol}


async def _create_feishu(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    *,
    display_name: str = "飞书 CLI",
    shared: bool = False,
    cli_config_key: str | None = None,
) -> httpx.Response:
    creds: dict[str, Any] = dict(FEISHU_CREDS)
    if cli_config_key is not None:
        creds["cli_config_key"] = cli_config_key
    return await client.post(
        INSTANCES,
        headers=auth,
        json={
            "kind": "feishu-cli",
            "display_name": display_name,
            "credentials": creds,
            "shared": shared,
        },
    )


# ───────────────────────── (A) 资源归属 ─────────────────────────


async def test_foreign_cli_config_key_is_refused_everywhere_it_enters(
    api: tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]],
) -> None:
    """非 owner 拿别人的 ``instance_id`` 当 CLI 目录键 ⇒ 四个入口全部 403。"""
    client, _srv, ctx = api

    created = await _create_feishu(client, ctx["alice"], display_name="alice 飞书", shared=True)
    assert created.status_code == 201, created.text
    alice_instance_id = created.json()["instance_id"]

    # 前提证据：非 owner 确实【看得到】这把键（所以它是可发现的资源标识）
    listed = await client.get(INSTANCES, headers=ctx["bob"])
    assert listed.status_code == 200, listed.text
    row = next(r for r in listed.json() if r["instance_id"] == alice_instance_id)
    assert row["can_manage"] is False, "非 owner 看不到 can_manage=True，否则前提不成立"
    assert row["shared"] is True

    # ① POST /connector-instances —— 建实例时绑键
    denied_create = await _create_feishu(
        client, ctx["bob"], display_name="bob 抢绑", cli_config_key=alice_instance_id
    )
    assert denied_create.status_code == 403, denied_create.text
    assert denied_create.json()["error"]["code"] == "FORBIDDEN"

    # ② POST /connectors/feishu-cli/user-auth/start —— 匿名设备登录写同一目录
    denied_start = await client.post(
        "/api/connectors/feishu-cli/user-auth/start",
        headers=ctx["bob"],
        json={
            "app_id": "x",
            "app_secret": "y",
            "cli_config_key": alice_instance_id,
        },
    )
    assert denied_start.status_code == 403, denied_start.text
    assert denied_start.json()["error"]["code"] == "FORBIDDEN"

    # ③ POST /connectors/feishu-cli/user-auth/complete —— 同上
    denied_complete = await client.post(
        "/api/connectors/feishu-cli/user-auth/complete",
        headers=ctx["bob"],
        json={
            "app_id": "x",
            "app_secret": "y",
            "device_code": "dc",
            "cli_config_key": alice_instance_id,
        },
    )
    assert denied_complete.status_code == 403, denied_complete.text
    assert denied_complete.json()["error"]["code"] == "FORBIDDEN"

    # ④ PATCH /connector-instances/{own} —— 事后把凭据里的键改成别人的
    own = await _create_feishu(client, ctx["bob"], display_name="bob 自己的")
    assert own.status_code == 201, own.text
    denied_patch = await client.patch(
        f"{INSTANCES}/{own.json()['instance_id']}",
        headers=ctx["bob"],
        json={"credentials": {"cli_config_key": alice_instance_id}},
    )
    assert denied_patch.status_code == 403, denied_patch.text
    assert denied_patch.json()["error"]["code"] == "FORBIDDEN"


async def test_owner_and_admin_still_bind_their_own_cli_config_key(
    api: tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]],
) -> None:
    """合法路径不得被误伤：owner 本人 + admin 旁路 + 全新随机键 都必须成功。"""
    client, _srv, ctx = api

    created = await _create_feishu(client, ctx["alice"], display_name="alice 飞书", shared=True)
    assert created.status_code == 201, created.text
    alice_instance_id = created.json()["instance_id"]

    # ① owner 用自己的 instance_id 作键（幂等，指向自己的目录）
    own = await _create_feishu(
        client, ctx["alice"], display_name="alice 复用", cli_config_key=alice_instance_id
    )
    assert own.status_code == 201, own.text

    # ② 正常路径：不传键 ⇒ 服务端生成全新 ULID
    fresh = await _create_feishu(client, ctx["bob"], display_name="bob 全新键")
    assert fresh.status_code == 201, fresh.text

    # ③ admin 旁路
    as_admin = await _create_feishu(
        client, ctx["admin"], display_name="admin 旁路", cli_config_key=alice_instance_id
    )
    assert as_admin.status_code == 201, as_admin.text


# ───────────────────────── (B) 权限位 ─────────────────────────


async def test_connector_management_requires_connectors_permission(
    api: tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]],
) -> None:
    """无 ``connectors`` 键的用户（非 admin）⇒ 管理面全部 403。"""
    client, _srv, ctx = api
    carol = ctx["carol"]

    created = await _create_feishu(client, ctx["alice"], display_name="alice 飞书")
    assert created.status_code == 201, created.text
    instance_id = created.json()["instance_id"]

    denied: list[tuple[str, httpx.Response]] = [
        ("create", await _create_feishu(client, carol, display_name="carol 建")),
        (
            "patch",
            await client.patch(
                f"{INSTANCES}/{instance_id}", headers=carol, json={"display_name": "x"}
            ),
        ),
        ("delete", await client.delete(f"{INSTANCES}/{instance_id}", headers=carol)),
        ("test", await client.post(f"{INSTANCES}/{instance_id}/test", headers=carol)),
        ("refresh", await client.post(f"{INSTANCES}/{instance_id}/refresh", headers=carol)),
        (
            "test-credentials",
            await client.post(
                "/api/connectors/test-credentials",
                headers=carol,
                json={"kind": "qq-mail", "credentials": {"email": "a@b.c", "password": "p"}},
            ),
        ),
        (
            "put-custom-mcp",
            await client.put(CUSTOM_MCP, headers=carol, json={"servers": {}}),
        ),
        (
            "patch-custom-mcp-server",
            await client.patch(f"{CUSTOM_MCP}/servers/x", headers=carol, json={"enabled": True}),
        ),
        (
            "oauth-start",
            await client.post(
                "/api/connectors/oauth/start",
                headers=carol,
                json={"target": {"type": "catalog", "kind": "notion"}},
            ),
        ),
        (
            "oauth-pending",
            await client.get("/api/connectors/oauth/pending/nope", headers=carol),
        ),
        (
            "exchange-code",
            await client.post(
                "/api/connectors/auth/wechat-reading/exchange-code",
                headers=carol,
                json={"code": "c"},
            ),
        ),
    ]
    for label, resp in denied:
        assert resp.status_code == 403, f"{label}: {resp.status_code} {resp.text}"
        assert resp.json()["error"]["code"] == "FORBIDDEN", f"{label}: {resp.text}"


async def test_connector_management_allows_baseline_user_and_admin(
    api: tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]],
) -> None:
    """合法通过：baseline 用户（持有 ``connectors``）与 admin 都不被误伤。"""
    client, _srv, ctx = api

    for who in ("alice", "admin"):
        resp = await _create_feishu(client, ctx[who], display_name=f"{who} 飞书")
        assert resp.status_code == 201, f"{who}: {resp.text}"
        instance_id = resp.json()["instance_id"]
        deleted = await client.delete(f"{INSTANCES}/{instance_id}", headers=ctx[who])
        assert deleted.status_code == 204, f"{who}: {deleted.text}"


async def test_connector_read_surface_stays_open_without_permission(
    api: tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]],
) -> None:
    """刻意**不**门禁的读面 —— 冻结理由，防止后续把门禁铺成装饰性权限。

    ``GET /connector-instances`` 被 Chat 输入框与项目面板复用，不属于
    ``/connectors`` 管理页；它只对 ``shared=1`` 行可见且从不回传凭据。
    ``GET /connectors/catalog`` 是无资源元数据（kind / 文档链接 / OAuth 就绪位）。
    """
    client, _srv, ctx = api
    carol = ctx["carol"]

    assert (await client.get(INSTANCES, headers=carol)).status_code == 200
    assert (await client.get("/api/connectors/catalog", headers=carol)).status_code == 200
    assert (await client.get("/api/connectors/auth/notion/info", headers=carol)).status_code == 200


# ───────────────────────── 机判冻结 ─────────────────────────

MODULE = pathlib.Path("src/octop/api/routers/connectors.py")

#: 管理面：必须带 `require_permission("connectors")`。
GATED = frozenset(
    {
        "put_custom_mcp",
        "patch_custom_mcp_server",
        "test_custom_mcp",
        "create_instance",
        "patch_instance",
        "delete_instance",
        "test_instance",
        "test_credentials",
        "feishu_cli_user_auth_start",
        "feishu_cli_user_auth_complete",
        "feishu_cli_user_auth_start_instance",
        "feishu_cli_user_auth_complete_instance",
        "refresh_instance",
        "auth_exchange_code",
        "oauth_start_unified",
        "oauth_start_legacy",
        "oauth_pending",
    }
)

#: 刻意留开 —— 复用面 / 无资源元数据 / 公开 OAuth 回调 / 已单独门禁。
UNGATED_BY_DESIGN = frozenset(
    {
        "list_instances",  # Chat 输入框 + 项目面板复用
        "get_catalog",  # 无资源元数据
        "get_instance",
        "get_custom_mcp",
        "auth_info",
        "auth_authorize_url",
        "connector_cli_status",
        "detect_weknora_on_octop_host",
        "oauth_callback",  # 提供商浏览器重定向，无 JWT
        "connector_install_cli",  # 早就有 require_permission("connectors")
    }
)

#: 必须做 `cli_config_key` 归属校验的入口。
CLI_KEY_GUARDED = frozenset(
    {
        "create_instance",
        "patch_instance",
        "feishu_cli_user_auth_start",
        "feishu_cli_user_auth_complete",
    }
)


def _handlers() -> dict[str, ast.AST]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                attr = getattr(target, "attr", None)
                if attr in {"get", "post", "put", "patch", "delete"}:
                    out[node.name] = node
                    break
    return out


def _calls(fn: ast.AST, name: str) -> list[ast.Call]:
    """`fn` 内（含签名默认值）对该名字的调用 —— 结构化，不依赖 `ast.unparse` 的引号风格。"""
    out: list[ast.Call] = []
    for item in ast.walk(fn):
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == name:
            out.append(item)
    return out


def _has_connectors_gate(fn: ast.AST) -> bool:
    for call in _calls(fn, "require_permission"):
        if (
            call.args
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == "connectors"
        ):
            return True
    return False


def test_gate_set_matches_frozen_lists() -> None:
    names = set(_handlers())
    assert GATED.issubset(names), f"gated handlers missing from module: {sorted(GATED - names)}"
    assert UNGATED_BY_DESIGN.issubset(names), (
        f"exempt handlers missing from module: {sorted(UNGATED_BY_DESIGN - names)}"
    )
    assert not (GATED & UNGATED_BY_DESIGN)


def test_every_management_handler_carries_the_connectors_permission_key() -> None:
    handlers = _handlers()
    missing = [name for name in sorted(GATED) if not _has_connectors_gate(handlers[name])]
    assert not missing, f"connectors module key missing on: {missing}"


def test_exempt_handlers_are_not_accidentally_gated() -> None:
    """反装饰性权限：留开的面不得被顺手挂上 `connectors` 键。"""
    handlers = _handlers()
    accidentally = [
        name
        for name in sorted(UNGATED_BY_DESIGN - {"connector_install_cli"})
        if _has_connectors_gate(handlers[name])
    ]
    assert not accidentally, f"decorative gate added to exempt surfaces: {accidentally}"


def test_cli_config_key_ownership_guard_is_wired_into_every_entry() -> None:
    handlers = _handlers()
    missing = [
        name
        for name in sorted(CLI_KEY_GUARDED)
        if not _calls(handlers[name], "_assert_cli_config_key_owned")
    ]
    assert not missing, f"cli_config_key ownership guard missing on: {missing}"


def test_ownership_guard_reuses_the_existing_predicate() -> None:
    """不得发明新语义：归属判定必须落到既有的 `_assert_can_manage_connector`。"""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    guard = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_cli_config_key_owned"
    )
    assert _calls(guard, "_assert_can_manage_connector"), (
        "_assert_cli_config_key_owned 必须复用 _assert_can_manage_connector，不能自带第二套归属模型"
    )
