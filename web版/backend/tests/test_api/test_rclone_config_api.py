"""API 集成：rclone 配置管理四端点（S4 鉴权 / S5 Origin / S6 审计 ×3 / S3 凭据零回显）。

CLI 用带状态的假执行器（模拟真实 conf 增删改），覆盖：
- 401（未登录）/ 403（缺 Origin 的写方法）；
- 200 查询：managed 过滤、凭据只回 ``has_*``、非注册段零参数；
- 增删改语义与审计埋点（append / edit / delete），OAuth 段可删不可编辑；
- 错误语义：409 重名、404 不在册、422 未注册类型、503 rclone 不可用。
"""

from __future__ import annotations

import json
from typing import Any

from conftest import ORIGIN, finish_bootstrap

from app.services.rclone_config import RcloneConfigService

WEBDAV_SECTION: dict[str, str] = {
    "type": "webdav",
    "url": "https://dav.example.com",
    "vendor": "other",
    "user": "u",
    "pass": "obscured-webdav-pass",
}
CRYPT_SECTION: dict[str, str] = {
    "type": "crypt",
    "remote": "mywebdav:backup/restic",
    "password": "obscured-secret-value",
    "password2": "obscured-salt",
    "filename_encryption": "standard",
    "directory_name_encryption": "true",
    "filename_encoding": "base32",
}
GDRIVE_SECTION: dict[str, str] = {"type": "drive", "token": "oauth-token", "client_id": "cid"}

CRYPT_PARAMS: dict[str, str] = {
    "remote": "mywebdav:backup/restic",
    "password": "plain-password",
    "filename_encoding": "base64",
}


class _FakeExec:
    """带 conf 状态机的 CLI 假实现（与 tests/test_rclone_config.py 同构，独立实现防耦合）。"""

    def __init__(self, state: dict[str, dict[str, Any]] | None = None) -> None:
        self.state: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (state or {}).items()}
        self.failure: tuple[int, str] | None = None
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        if self.failure is not None and ("create" in argv or "update" in argv or "delete" in argv):
            return self.failure
        if "dump" in argv:
            return 0, json.dumps(self.state)
        if "listremotes" in argv:
            return 0, "".join(f"{name}:\n" for name in sorted(self.state))
        if "create" in argv:
            i = argv.index("create")
            self.state[argv[i + 1]] = {"type": argv[i + 2], **_kv(argv[i + 3 :])}
            return 0, ""
        if "update" in argv:
            i = argv.index("update")
            self.state.setdefault(argv[i + 1], {}).update(_kv(argv[i + 2 :]))
            return 0, ""
        if "delete" in argv:
            self.state.pop(argv[argv.index("delete") + 1], None)
            return 0, ""
        return 0, ""


def _kv(args: list[str]) -> dict[str, str]:
    return dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("--"))


class _FakeProbe:
    """只读探测桩：远端清单与假 conf 状态保持一致（避免真机 rclone 干扰断言）。"""

    def __init__(self, state: dict[str, dict[str, Any]], conf_path: str) -> None:
        self._state = state
        self._conf_path = conf_path

    async def list_remotes(self):  # noqa: D102 - 桩对象，签名镜像 RcloneProbe
        from app.services.rclone import RcloneRemotes

        return RcloneRemotes(
            available=True,
            conf_present=True,
            conf_path=self._conf_path,
            remotes=tuple(sorted(self._state)),
            reason=None,
        )


def _install(ns, state: dict[str, dict[str, Any]] | None = None) -> _FakeExec:
    """把带假执行器的服务与探测桩装进 app.state（保留 settings 以复用 conf 路径）。"""
    exec_ = _FakeExec(state)
    service = RcloneConfigService(ns.app.state.settings, executor=exec_)
    ns.app.state.rclone_config_service = service
    ns.app.state.rclone_probe = _FakeProbe(exec_.state, service.conf_path)
    return exec_


# ---------------------------------------------------------------- S4/S5


async def test_rclone_config_endpoints_require_session(app_client):
    ns = app_client
    for method, url in [
        ("GET", "/api/rclone/configs"),
        ("POST", "/api/rclone/config/mycrypt"),
        ("PUT", "/api/rclone/config/mycrypt"),
        ("DELETE", "/api/rclone/config/mycrypt"),
    ]:
        resp = await ns.client.request(method, url, headers=ORIGIN, json={"type": "crypt", "params": {}})
        assert resp.status_code == 401, (method, url, resp.status_code)


async def test_rclone_config_writes_require_origin(app_client):
    """S5：写方法必须带同源 Origin。"""
    ns = app_client
    await finish_bootstrap(ns)
    _install(ns, {"mywebdav": WEBDAV_SECTION})
    for method, url in [
        ("POST", "/api/rclone/config/mycrypt"),
        ("PUT", "/api/rclone/config/mywebdav"),
        ("DELETE", "/api/rclone/config/mywebdav"),
    ]:
        resp = await ns.client.request(method, url, json={"type": "crypt", "params": {}})
        assert resp.status_code == 403, (method, url, resp.status_code)


# ---------------------------------------------------------------- 查询


async def test_list_configs_managed_and_zero_credentials(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    _install(ns, {"mycrypt": CRYPT_SECTION, "gdrive": GDRIVE_SECTION})

    resp = await ns.client.get("/api/rclone/configs")
    assert resp.status_code == 200
    body = resp.json()
    # conf 路径由 settings 派生（测试环境未必是容器路径），只断言落点形态
    assert body["conf_path"].endswith("rclone.conf")
    by_name = {c["remote"]: c for c in body["configs"]}

    assert by_name["mycrypt"]["managed"] is True
    assert by_name["mycrypt"]["params"]["filename_encoding"] == "base32"
    assert by_name["mycrypt"]["has_credentials"] == {"password": True, "password2": True}
    # S3：凭据值永不出现在响应里
    assert "obscured-secret-value" not in resp.text

    assert by_name["gdrive"]["managed"] is False
    assert by_name["gdrive"]["params"] == {} and by_name["gdrive"]["has_credentials"] == {}
    assert "oauth-token" not in resp.text


# ---------------------------------------------------------------- 增删改 + 审计


async def test_create_config_success_and_audit(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    exec_ = _install(ns, {"mywebdav": WEBDAV_SECTION})

    resp = await ns.client.post(
        "/api/rclone/config/mycrypt",
        json={"type": "crypt", "params": CRYPT_PARAMS},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "create" and body["type"] == "crypt"
    assert "mycrypt" in body["remotes_after"]
    assert "plain-password" not in resp.text  # S3：响应零凭据

    rows, total = await ns.app.state.audit.query(action="rclone.config_append")
    assert total == 1
    assert rows[0].target == "mycrypt"
    assert json.loads(rows[0].detail)["type"] == "crypt"
    assert "plain-password" not in (rows[0].detail or "")  # S3：审计 detail 零凭据

    created = exec_.state["mycrypt"]
    assert created["filename_encoding"] == "base64"


async def test_create_duplicate_remote_409(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    _install(ns, {"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})

    resp = await ns.client.post(
        "/api/rclone/config/mycrypt", json={"type": "crypt", "params": CRYPT_PARAMS}, headers=ORIGIN
    )
    assert resp.status_code == 409, resp.text
    _, total = await ns.app.state.audit.query(action="rclone.config_append")
    assert total == 0  # 失败不埋点


async def test_create_oauth_type_422(app_client):
    """OAuth 网盘类型不在注册表 → 422（绝不生成需浏览器授权的配置）。"""
    ns = app_client
    await finish_bootstrap(ns)
    _install(ns, {})
    resp = await ns.client.post(
        "/api/rclone/config/gd", json={"type": "drive", "params": {"token": "x"}}, headers=ORIGIN
    )
    assert resp.status_code == 422


async def test_update_config_success_and_audit(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    exec_ = _install(ns, {"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})

    resp = await ns.client.put(
        "/api/rclone/config/mycrypt",
        json={"type": "crypt", "params": {"remote": "mywebdav:bucket", "filename_encoding": "base32768"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["action"] == "update"
    assert exec_.state["mycrypt"]["filename_encoding"] == "base32768"

    rows, total = await ns.app.state.audit.query(action="rclone.config_edit")
    assert total == 1 and rows[0].target == "mycrypt"


async def test_update_oauth_section_rejected(app_client):
    """裁决 9：OAuth 段不可编辑（无授权流支撑）。"""
    ns = app_client
    await finish_bootstrap(ns)
    _install(ns, {"gdrive": GDRIVE_SECTION})
    resp = await ns.client.put(
        "/api/rclone/config/gdrive",
        json={"type": "crypt", "params": CRYPT_PARAMS},
        headers=ORIGIN,
    )
    assert resp.status_code == 422


async def test_delete_oauth_section_allowed_and_audited(app_client):
    """裁决 8：OAuth 段可删（确认门在前端），审计留痕含 type。"""
    ns = app_client
    await finish_bootstrap(ns)
    exec_ = _install(ns, {"gdrive": GDRIVE_SECTION})

    resp = await ns.client.delete("/api/rclone/config/gdrive", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "drive"
    assert "gdrive" not in exec_.state

    rows, total = await ns.app.state.audit.query(action="rclone.config_delete")
    assert total == 1 and rows[0].target == "gdrive"
    assert json.loads(rows[0].detail)["type"] == "drive"


async def test_delete_missing_remote_404(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    _install(ns, {})
    resp = await ns.client.delete("/api/rclone/config/ghost", headers=ORIGIN)
    assert resp.status_code == 404


async def test_cli_unavailable_maps_to_503(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    exec_ = _install(ns, {"mywebdav": WEBDAV_SECTION})
    exec_.failure = (1, "open /etc/rclone: permission denied")

    resp = await ns.client.post(
        "/api/rclone/config/mycrypt", json={"type": "crypt", "params": CRYPT_PARAMS}, headers=ORIGIN
    )
    assert resp.status_code == 503, resp.text
    assert "目录挂载" in resp.text or "permission" in resp.text
