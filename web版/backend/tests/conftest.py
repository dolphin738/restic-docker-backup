"""pytest 共享夹具：隔离运行环境 + 带 lifespan 的 FastAPI 应用/httpx 客户端。"""

from __future__ import annotations

import contextlib
import weakref
from types import SimpleNamespace

import httpx
import pytest

# 测试内所有写方法请求携带的同源 Origin（与 httpx base_url 的 host 匹配）
ORIGIN = {"Origin": "http://testserver"}
NEW_PASSWORD = "Str0ngPass!23"


@pytest.fixture(scope="session", autouse=True)
def db_registry():
    """会话级：登记所有 ``Database`` 实例（弱引用，不阻止回收）。

    为 :func:`close_leaked_databases` 提供实例清单——该夹具需要知道「本用例新建了
    哪些连接」才能兜底关闭。以弱引用持有，避免测试夹具自身延长对象生命周期。
    """
    from app.db.database import Database

    registry: weakref.WeakSet = weakref.WeakSet()
    original_init = Database.__init__

    def patched_init(self, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)
        registry.add(self)

    Database.__init__ = patched_init
    try:
        yield registry
    finally:
        Database.__init__ = original_init


@pytest.fixture(autouse=True)
async def close_leaked_databases(db_registry):
    """用例结束后兜底关闭**本用例新建**且未 close 的 Database 连接。

    根因（实测）：aiosqlite 的连接 worker 线程是**非 daemon 线程**
    （``aiosqlite/core.py`` 的 ``Thread(target=_connection_worker_thread, ...)``
    未设 daemon），只要有一个连接没关，解释器退出时就无限期等待该线程——
    表现为「pytest 跑完却不退出」，CI 无超时即永久卡死。用例一旦抛异常，
    正文里的 ``await db.close()`` 就被跳过，故在此兜底。

    ``Database.close`` 本身幂等，重复调用无副作用；本夹具只关闭本用例新增的
    实例（按对象 id 比对），不动会话级/上游夹具持有的连接。
    """
    before = {id(db) for db in db_registry}
    yield
    for db in list(db_registry):
        if id(db) in before:
            continue
        # 兜底路径不得掩盖用例本身的失败（吞掉异常是刻意的，非遗漏）
        with contextlib.suppress(Exception):
            await db.close()


@pytest.fixture(autouse=True)
def reset_meow_limiter():
    """每用例清零 MeoW 限流计数（模块级单例，跨用例共享）。

    限流器按 API §5.8 守「3 秒 1 条 / 分钟 15 / 小时 60 / 天 1000」；不清零会让
    连续用例互相串味（第二次推送被判超配额或被迫真的睡 3 秒）。
    """
    from app.services import notifier as notifier_module

    notifier_module.reset_meow_rate_limiter()
    yield
    notifier_module.reset_meow_rate_limiter()


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    """为每个测试准备隔离的 DATA_DIR/密码文件，并清空可能污染的仓库凭据环境变量。"""
    data_dir = tmp_path / "data"
    # E5 per-repo 布局：<PASSFILE_ROOT>/<repo_id>/password，当前仅 default 一行
    passfile_root = tmp_path / "repos"
    passfile = passfile_root / "default" / "password"
    passfile.parent.mkdir(parents=True, exist_ok=True)
    passfile.write_text("repo-password", encoding="utf-8")

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PASSFILE_ROOT", str(passfile_root))
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

    import app.routers.repos as repos_module

    monkeypatch.setattr(repos_module, "_status_cache", {})
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
