"""P3-24: ``refresh_instance`` 的归属判据与同文件其余 6 处 ``_assert_can_manage_connector`` 统一。

**用户 2026-10-10 拍板：统一为 `_assert_can_manage_connector`（含 admin 旁路）。**
这是一个**知情下的权限放宽** —— admin 从此可以刷新**他人**实例的 OAuth token
（改前 ``inst.user_id != user.id`` ⇒ 403；改后 ⇒ 200）。

本文件给出三条判别性对照（每一条都是「真阳性 / 真阴性」成对）：

======================  ==================  ==================
调用者                   实例归属              期望
======================  ==================  ==================
admin                   他人（alice）         **允许** 200
bob（普通用户）           他人（alice）         **拒绝** 403 FORBIDDEN
alice                   自己                 **允许** 200
======================  ==================  ==================

外加**机判冻结**：``refresh_instance`` 必须复用 ``_assert_can_manage_connector``，
且不得再自带第二套内联归属模型（``inst.user_id != user.id``）。
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
MODULE = pathlib.Path("src/octop/api/routers/connectors.py")

FEISHU_CREDS = {"app_id": "cli_app_id", "app_secret": "cli_app_secret"}


@pytest.fixture
async def api(
    tmp_octop_home: pathlib.Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]]]:
    async with octop_client(tmp_octop_home) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        admin = await auth_header(client)
        alice = await create_user(client, admin, username="alice")
        bob = await create_user(client, admin, username="bob")
        yield client, srv, {"admin": admin, "alice": alice, "bob": bob}


async def _create_feishu(client: httpx.AsyncClient, auth: dict[str, str]) -> str:
    resp = await client.post(
        INSTANCES,
        headers=auth,
        json={
            "kind": "feishu-cli",
            "display_name": "alice 飞书",
            "credentials": dict(FEISHU_CREDS),
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["instance_id"])


async def test_refresh_instance_ownership_matrix(
    api: tuple[httpx.AsyncClient, Any, dict[str, dict[str, str]]],
) -> None:
    """★ 判别性三条：admin 他人=允许 / 非 admin 他人=拒绝 / owner 自己=允许。"""
    client, _srv, ctx = api
    instance_id = await _create_feishu(client, ctx["alice"])
    url = f"{INSTANCES}/{instance_id}/refresh"

    # ① 真阳性保留：非 admin 刷新**他人**实例 ⇒ 403 FORBIDDEN
    bob_resp = await client.post(url, headers=ctx["bob"])
    assert bob_resp.status_code == 403, f"非 admin 越权未被拒: {bob_resp.text}"
    assert bob_resp.json()["error"]["code"] == "FORBIDDEN", bob_resp.text
    print(f"[IV] bob(non-admin) -> other's instance refresh: {bob_resp.status_code}")

    # ② 不误伤：owner 刷新自己的实例 ⇒ 200
    alice_resp = await client.post(url, headers=ctx["alice"])
    assert alice_resp.status_code == 200, f"owner 被误伤: {alice_resp.text}"
    print(f"[IV] alice(owner) -> own instance refresh: {alice_resp.status_code}")

    # ③ ★ 语义变化：admin 刷新**他人**实例 ⇒ 允许 200（改前此处为 403）
    admin_resp = await client.post(url, headers=ctx["admin"])
    assert admin_resp.status_code == 200, (
        f"admin 旁路缺失（P3-24 未落地）: {admin_resp.status_code} {admin_resp.text}"
    )
    print(f"[IV] admin -> other's instance refresh: {admin_resp.status_code}")


# ───────────────────────── 机判冻结 ─────────────────────────


def _refresh_handler() -> ast.AsyncFunctionDef:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "refresh_instance":
            return node
    raise AssertionError("refresh_instance not found in connectors.py")


def _calls(fn: ast.AST, name: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for item in ast.walk(fn):
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == name:
            out.append(item)
    return out


def test_refresh_instance_reuses_the_shared_ownership_guard() -> None:
    """不得发明第二套语义：refresh 必须落到既有的 ``_assert_can_manage_connector``。"""
    assert _calls(_refresh_handler(), "_assert_can_manage_connector"), (
        "refresh_instance 必须复用 _assert_can_manage_connector，不能自带第二套归属模型"
    )


def test_refresh_instance_has_no_inline_owner_only_check() -> None:
    """内联 ``inst.user_id != user.id`` 必须消失 —— 它就是「无 admin 旁路」的根因。"""
    source = ast.unparse(_refresh_handler())
    assert "user_id != user.id" not in source, (
        "refresh_instance 仍有内联 owner-only 判据；P3-24 未真正统一"
    )
