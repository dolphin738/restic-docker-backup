"""FastAPI 应用入口：中间件链、生命周期钩子、路由挂载、内置 TLS（§1.1 / §8 / §1.2 Q2 裁决）。

中间件链顺序（§8）：
    ① CSRF/Origin 校验（仅写方法，ASGI 中间件，先于路由执行）
    ② Session 鉴权（路由依赖 ``require_session``）
    ③ Bootstrap 门（路由依赖 ``require_bootstrap_done``；``must_change=1`` 时仅放行 auth/bootstrap）
    ④ 审计埋点（危险操作路由显式调用 AuditRepo，非全局）
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.bootstrap import AdminAccountStore, BootstrapService
from app.auth.dependencies import (
    is_same_origin,
    is_write_method,
    require_bootstrap_done,
    require_session,
)
from app.auth.service import AuthService
from app.auth.session import SessionService
from app.constants import (
    CSRF_ORIGIN,
    ERROR_STATUS,
    FORBIDDEN,
    INTERNAL,
    NOT_FOUND,
    UNAUTHORIZED,
    VALIDATION_ERROR,
)
from app.db.database import Database, close_database, init_database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.policy_repo import PolicyRepo
from app.errors import AppError
from app.exclusions import ExclusionRules
from app.routers import auth as auth_router
from app.routers import config as config_router
from app.routers import health as health_router
from app.routers import jobs as jobs_router
from app.routers import policies as policies_router
from app.routers import restore as restore_router
from app.routers import snapshots as snapshots_router
from app.routers import sources as sources_router
from app.schemas.api_schemas import ErrorResponse
from app.services.env_sync import sync_env_overrides
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.services.path_mapper import PathMapper
from app.services.policy_service import PolicyService
from app.services.restic import ResticCmd
from app.services.sse import SseChannel
from app.settings import Settings, get_settings

logger = logging.getLogger("restic-web")

# HTTPException 状态码 → 统一错误码（仅用于兜底映射，业务错误一律走 AppError）
STATUS_FALLBACK_CODE: dict[int, str] = {
    401: UNAUTHORIZED,
    403: FORBIDDEN,
    404: NOT_FOUND,
}


class CSRFOriginMiddleware(BaseHTTPMiddleware):
    """① 写方法 Origin/Referer 同源校验（§8 第一道防线为 SameSite=Strict，此处为第二道）。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        """拦截跨站写请求；读方法与同源请求直接放行。"""
        if is_write_method(request) and not is_same_origin(request):
            payload = ErrorResponse(code=CSRF_ORIGIN, message="跨站请求被拒绝（Origin 校验失败）")
            return JSONResponse(
                status_code=ERROR_STATUS[CSRF_ORIGIN], content=payload.model_dump(exclude_none=True)
            )
        return await call_next(request)


def _configure_logging() -> None:
    """配置进程日志级别（容器日志交由 Docker json-file 轮转，方案 §8.1）。"""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化 DB/迁移、装配服务、首启引导准备、过期会话清理。"""
    _configure_logging()
    settings = get_settings()
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)

    db = await init_database(settings.db_path)
    audit = AuditRepo(db)
    config_repo = ConfigRepo(db)
    sessions = SessionService(db, settings)
    admin = AdminAccountStore(db, settings)
    auth_service = AuthService(audit, sessions, admin, settings)
    bootstrap = BootstrapService(admin, audit, settings)

    # 策略链路（§4.4）：
    # policy_repo.get_dict 的签名 (int) -> Awaitable[dict | None] 与 JobRunner 的
    # PolicyLoader 契约一致，故直接作为回调注入，无需包一层 lambda。
    policy_repo = PolicyRepo(db)
    policy_service = PolicyService(policy_repo, settings)
    await policy_repo.ensure_default(one_file_system=policy_service.default_one_file_system())

    # restic 执行链路（§7.1/§7.2/§7.3）
    job_repo = JobRepo(db)
    path_mapper = PathMapper(settings)
    restic_cmd = ResticCmd(settings, path_mapper)
    parser = OutputParser()
    sse_channel = SseChannel(parser, job_repo)
    job_runner = JobRunner(
        db,
        restic_cmd,
        parser,
        audit,
        settings=settings,
        policy_loader=policy_repo.get_dict,
    )

    app.state.settings = settings
    app.state.db = db
    app.state.audit = audit
    app.state.config_repo = config_repo
    app.state.sessions = sessions
    app.state.admin = admin
    app.state.auth_service = auth_service
    app.state.bootstrap = bootstrap
    app.state.policy_repo = policy_repo
    app.state.policy_service = policy_service
    app.state.exclusions = ExclusionRules(settings)
    app.state.job_repo = job_repo
    app.state.path_mapper = path_mapper
    app.state.restic = restic_cmd
    app.state.parser = parser
    app.state.sse_channel = sse_channel
    app.state.job_runner = job_runner

    # 首启引导准备：确保 admin_account 行存在，并缓存随机密码明文（不入 docker logs）
    await bootstrap.ensure_initial_admin()
    # 启动期惰性清理过期会话行（§8）
    purged = await sessions.purge_expired()
    if purged:
        logger.info("启动清理过期会话 %d 条", purged)
    # env→config 一次性同步：使 E07 的 ENV_OVERRIDDEN 真正触发得到（§6 env 覆盖优先）
    await sync_env_overrides(config_repo, settings, audit)
    # 存量回填：repo.url 已有但 repo.type 缺失的老库，按协议推导补写（前端不再自行推导）
    if await config_repo.backfill_repo_type():
        logger.info("已按 repo.url 协议回填 repo.type")
    # 启动恢复：遗留 running/pending 改标 interrupted（P1 不做补跑与 AUTO_UNLOCK）
    await job_runner.recover_on_startup()

    try:
        yield
    finally:
        # 容器 stop 收尾：向 restic 子进程转发 SIGTERM（§6.5）
        await job_runner.on_shutdown()
        await close_database()


def create_app() -> FastAPI:
    """构造 FastAPI 应用实例（供 uvicorn / 测试使用）。"""
    app = FastAPI(
        title="restic-web",
        version="0.1.0",
        description="restic-docker-backup Web 管理端（P1 基础设施 + 鉴权闭环）",
        lifespan=lifespan,
    )

    # ① CSRF/Origin（仅写方法）——Starlette 后注册的中间件更靠外，此处为唯一前置中间件
    app.add_middleware(CSRFOriginMiddleware)

    # 免鉴权路由：E04 health（HEALTHCHECK）
    app.include_router(health_router.router)
    # auth 路由：E01/E02/E03/bootstrap/E28（②③ 由路由内部按端点声明）
    app.include_router(auth_router.router)
    # 业务路由：②③ 必须显式挂载，否则首启未改密也能访问（PRD US-A1 AC2）
    protected = [Depends(require_session), Depends(require_bootstrap_done)]
    app.include_router(sources_router.router, dependencies=protected)    # E05/E31
    app.include_router(config_router.router, dependencies=protected)     # E06/E07 + E29/E30
    app.include_router(jobs_router.router, dependencies=protected)       # E12–E16/E27
    app.include_router(policies_router.router, dependencies=protected)   # E08–E11
    app.include_router(snapshots_router.router, dependencies=protected)  # E17/E18
    app.include_router(restore_router.router, dependencies=protected)    # E19
    # 后续批次（dump/notify/cache/audit…）在此按同样方式挂载：
    #   app.include_router(x_router.router, dependencies=protected)

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """注册统一错误响应处理器（§5.0：{code, message, data}）。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
        error = exc if isinstance(exc, AppError) else AppError()
        payload = ErrorResponse(code=error.code, message=error.message, data=error.data)
        return JSONResponse(status_code=error.status, content=payload.model_dump(exclude_none=True))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        detail = jsonable_encoder(getattr(exc, "errors", lambda: [])())
        payload = ErrorResponse(code=VALIDATION_ERROR, message="参数校验失败", data={"errors": detail})
        return JSONResponse(
            status_code=ERROR_STATUS[VALIDATION_ERROR], content=payload.model_dump(exclude_none=True)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        status_code = getattr(exc, "status_code", 500)
        code = STATUS_FALLBACK_CODE.get(status_code, INTERNAL)
        payload = ErrorResponse(code=code, message=str(getattr(exc, "detail", "")) or "请求失败")
        return JSONResponse(status_code=status_code, content=payload.model_dump(exclude_none=True))

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 兜底：即便 HTTPException 的专用处理器未生效，也保证响应体仍是统一错误结构
        if isinstance(exc, StarletteHTTPException):
            return await _handle_http_exception(request, exc)
        logger.exception("未处理异常：%s %s", request.method, request.url.path)
        payload = ErrorResponse(code=INTERNAL, message="服务器内部错误")
        return JSONResponse(status_code=ERROR_STATUS[INTERNAL], content=payload.model_dump(exclude_none=True))


app = create_app()


def _read_tls_options() -> tuple[str | None, str | None]:
    """启动时从 config 表读取内置 TLS 配置（§4.3 键表：server.tls_cert / server.tls_key）。

    Returns:
        ``(cert_path, key_path)``；两者齐备时以 uvicorn ssl 参数启动，否则纯 HTTP 供反代终结。
    """
    import asyncio

    async def _read() -> tuple[str | None, str | None]:
        settings = get_settings()
        db = Database(settings.db_path)
        await db.connect()
        try:
            repo = ConfigRepo(db)
            return await repo.get_value("server.tls_cert"), await repo.get_value("server.tls_key")
        finally:
            await db.close()

    try:
        return asyncio.run(_read())
    except Exception as exc:  # noqa: BLE001 - 配置不可读时按纯 HTTP 启动，避免容器起不来
        logger.warning("读取 TLS 配置失败，将以纯 HTTP 启动：%s", exc)
        return None, None


def main() -> None:
    """进程入口（entrypoint.sh 调用）：按 config 决定是否启用内置 TLS。"""
    import uvicorn

    settings: Settings = get_settings()
    cert_path, key_path = _read_tls_options()
    uvicorn_kwargs: dict[str, object] = {
        "host": settings.host,
        "port": settings.port,
        "proxy_headers": settings.trust_proxy,
        "forwarded_allow_ips": "*" if settings.trust_proxy else "127.0.0.1",
    }
    if cert_path and key_path:
        logger.info("启用内置 TLS：cert=%s key=%s", cert_path, key_path)
        uvicorn_kwargs["ssl_certfile"] = cert_path
        uvicorn_kwargs["ssl_keyfile"] = key_path
    else:
        logger.info("未配置 server.tls_cert/tls_key，以纯 HTTP 启动（HTTPS 由反向代理终结）")
    uvicorn.run("app.main:app", **uvicorn_kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
