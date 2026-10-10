"""pytest 共享夹具：隔离运行环境 + 带 lifespan 的 FastAPI 应用/httpx 客户端。"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import httpx
import pytest

# 测试内所有写方法请求携带的同源 Origin（与 httpx base_url 的 host 匹配）
ORIGIN = {"Origin": "http://testserver"}
NEW_PASSWORD = "Str0ngPass!23"


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    """为每个测试准备隔离的 DATA_DIR/密码文件，并清空可能污染的仓库凭据环境变量。"""
    data_dir = tmp_path / "data"
    passfile = tmp_path / "restic-pass"
    passfile.write_text("repo-password", encoding="utf-8")

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PASSFILE", str(passfile))
    # 登录锁定测试需要连续多次请求，把进程内限流阈值放大避免 429 RATE_LIMITED 干扰
    monkeypatch.setenv("LOGIN_RATE_LIMIT", "100")
    monkeypatch.setenv("RESTIC_BINARY", "restic-not-invoked-in-api-tests")

    for name in (
        "RESTIC_REPOSITORY",
        "RESTIC_HOST",
        "RESTIC_PASSWORD_FILE",
        "RESTIC_WEB_DEPLOY_DIR",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "AWS_ENDPOINT_URL",
        "B2_ACCOUNT_ID",
        "B2_ACCOUNT_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    from app.settings import get_settings

    get_settings.cache_clear()

    import app.routers.config as config_module

    monkeypatch.setattr(config_module, "_status_cache", None)
    return SimpleNamespace(data_dir=data_dir, tmp_path=tmp_path, passfile=passfile)


@pytest.fixture()
async def make_app(api_env, monkeypatch):
    """应用工厂：允许测试在 lifespan 启动前注入额外环境变量（如 RESTIC_REPOSITORY）。

    返回 ``async make_app(env_extra=None) -> SimpleNamespace(app, client)``。
    """
    stack = contextlib.AsyncExitStack()
    clients: list[httpx.AsyncClient] = []

    async def _make(env_extra: dict[str, str] | None = None) -> SimpleNamespace:
        for key, value in (env_extra or {}).items():
            monkeypatch.setenv(key, value)

        from app.main import create_app
        from app.settings import get_settings

        get_settings.cache_clear()
        application = create_app()
        await stack.enter_async_context(application.router.lifespan_context(application))
        transport = httpx.ASGITransport(app=application, client=("10.0.0.1", 12345))
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        clients.append(client)
        return SimpleNamespace(app=application, client=client)

    yield _make
    for client in clients:
        await client.aclose()
    await stack.aclose()


@pytest.fixture()
async def app_client(make_app):
    """默认单应用实例（无额外 env 注入）。"""
    return await make_app()


async def finish_bootstrap(ns) -> str:
    """走完首启引导：取一次性密码 → 改密 → 登录，返回一次性密码。"""
    client = ns.client
    resp = await client.get("/api/auth/bootstrap")
    assert resp.status_code == 200, resp.text
    password = resp.json()["password"]
    resp = await client.put(
        "/api/admin/password",
        json={"old_password": password, "new_password": NEW_PASSWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post("/api/auth/login", json={"password": NEW_PASSWORD}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    return password
