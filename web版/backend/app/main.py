"""FastAPI 应用入口：中间件链、生命周期钩子、路由挂载、内置 TLS（§1.1 / §8 / §1.2 Q2 裁决）。

中间件链顺序（§8）：
    ① CSRF/Origin 校验（仅写方法，ASGI 中间件，先于路由执行）
    ② Session 鉴权（路由依赖 ``require_session``）
    ③ Bootstrap 门（路由依赖 ``require_bootstrap_done``；``must_change=1`` 时仅放行 auth/bootstrap）
    ④ 审计埋点（危险操作路由显式调用 AuditRepo，非全局）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

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
    CRED_ENCRYPTION_KEY,
    CSRF_ORIGIN,
    DB_BUSY_TIMEOUT_MS_KEY,
    ERROR_STATUS,
    FORBIDDEN,
    INTERNAL,
    LOG_LEVEL_KEY,
    NOT_FOUND,
    UNAUTHORIZED,
    VALIDATION_ERROR,
)
from app.db.database import Database, close_database, init_database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.rclone_archive_repo import RcloneArchiveRepo
from app.db.repositories.rclone_form_repo import RcloneFormRepo
from app.db.repositories.repo_repo import DEFAULT_REPO_ID, ReposRepo
from app.db.repositories.task_repo import TaskRepo
from app.errors import AppError
from app.exclusions import ExclusionRules
from app.routers import auth as auth_router
from app.routers import backup_tasks as backup_tasks_router
from app.routers import checks as checks_router
from app.routers import config as config_router
from app.routers import fs as fs_router
from app.routers import health as health_router
from app.routers import jobs as jobs_router
from app.routers import metrics as metrics_router
from app.routers import ops as ops_router
from app.routers import repos as repos_router
from app.routers import restore as restore_router
from app.routers import snapshots as snapshots_router
from app.routers import sources as sources_router
from app.routers import system as system_router
from app.schemas.api_schemas import ErrorResponse
from app.services.cache_service import CacheService
from app.services.compare import CompareService
from app.services.crypto import CredentialCrypto, EncryptionState
from app.services.env_sync import load_credentials, sync_env_overrides, sync_env_repo_overrides
from app.services.job_runner import JobRunner
from app.services.log_control import apply_log_level
from app.services.notifier import Notifier
from app.services.parser import OutputParser
from app.services.path_mapper import PathMapper
from app.services.rclone import RcloneProbe
from app.services.rclone_config import RcloneConfigService
from app.services.rclone_forms import import_legacy_sidecar
from app.services.repo_lifecycle import RepoLifecycleService
from app.services.repo_migration import migrate_legacy_repo_config
from app.services.repo_status_cache import invalidate_repo_status_cache
from app.services.restic import ResticCmd
from app.services.retention import RetentionService
from app.services.scheduler import SchedulerService
from app.services.snapshot_catalog import SnapshotCatalog
from app.services.sse import SseChannel
from app.services.task_service import TaskService
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


# ---- 日志格式（容器日志交由 Docker json-file 轮转，方案 §8.1）----
# 必须带时间戳：uvicorn 默认格式（`INFO:     消息` / 访问日志）没有时间字段，
# 容器日志里无法判断时序——排查 SSE 断流、健康检查节奏、restic 长任务耗时全靠猜。
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
# %z 输出 +0800 偏移：避免把容器 TZ（compose 默认 Asia/Shanghai）误读成 UTC
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S%z"


def _log_level_name() -> str:
    """日志级别（环境变量 ``LOG_LEVEL``，默认 INFO）。"""
    return os.environ.get("LOG_LEVEL", "INFO").upper()


def _configure_logging() -> None:
    """配置 root logger（格式化 + 级别）。

    **必须在模块导入期调用**（早于 ``create_app()``）：uvicorn 以 ``app.main:app``
    字符串导入本模块，若此刻 root 还没有 handler，``create_app()`` 与 ``main()`` 里的
    启动日志会被 logging 的 lastResort handler 按 WARNING 门槛**静默丢弃**——
    表现就是容器里只看得到 uvicorn 那几行，应用自己的启动信息一条都没有。
    """
    logging.basicConfig(
        level=getattr(logging, _log_level_name(), logging.INFO),
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
    )


def _uvicorn_log_config() -> dict[str, object]:
    """uvicorn 日志配置：沿用其 default/access 双 handler 结构，只换掉格式串补上时间戳。

    刻意**不配置 root**——root handler 由导入期的 ``_configure_logging()`` 装好，
    配合 ``disable_existing_loggers=False`` 不会被 dictConfig 摘掉，
    应用日志（``restic-web``）照常经 root 输出且只输出一次。
    """
    level = _log_level_name()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": LOG_FORMAT, "datefmt": LOG_DATEFMT},
            "access": {"format": LOG_FORMAT, "datefmt": LOG_DATEFMT},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {"level": level},
            "uvicorn.access": {"handlers": ["access"], "level": level, "propagate": False},
        },
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化 DB/迁移、装配服务、首启引导准备、过期会话清理。

    日志不在此配置——已在模块导入期完成（见 ``_configure_logging`` 说明）。
    """
    settings = get_settings()
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)

    # Windows 子进程可用性自检（2026-10-19 真机实证）：uvicorn --reload / --workers>1 会把
    # Windows 事件循环强制选成 SelectorEventLoop——它不支持 create_subprocess_exec
    # （NotImplementedError，asyncio/base_events.py:539），restic/rclone 全部子进程操作
    # 失败。启动即明示，避免用户到第一次备份/探测才发现；修法 --loop none 重启
    # （dev-scripts/dev.ps1 已内置该参数；Linux/Docker 无此问题）。
    _loop = asyncio.get_running_loop()
    if sys.platform == "win32" and not isinstance(_loop, asyncio.ProactorEventLoop):
        logger.error(
            "当前事件循环为 %s（Windows 下 uvicorn --reload 的默认选择），不支持子进程："
            "restic/rclone 操作将全部失败。请以 --loop none 重启后端"
            "（dev-scripts/dev.ps1 已内置该参数）",
            type(_loop).__name__,
        )

    db = await init_database(settings.db_path)
    audit = AuditRepo(db)
    # R-025 凭据加密（P3-A，owner 2026-10-16 裁决默认开）：密钥源自仓库密码文件
    # （E5 per-repo 布局）经 HKDF-SHA256 派生；缺省 enabled=True，随后按持久化键校准。
    # repo_id 显式传入并混入 HKDF info（enc:v2:，审计 2026-10-16 🟡-1：跨仓库密钥
    # 隔离）——当前单实例即 default；多仓库各自凭据加密时按仓库各建实例。
    credential_crypto = CredentialCrypto(settings, repo_id=DEFAULT_REPO_ID)
    encryption = EncryptionState(credential_crypto)
    config_repo = ConfigRepo(db, encryption=encryption)
    # R-023 多仓库（P3-C）：repos 表为仓库事实源（0003 扩列）；TaskRepo 经它取 host_tag
    repos_repo = ReposRepo(db)
    # 删除保护方案 D3：rclone 配置段删除自动归档（值等价于凭据 → 只落库、永不回显）
    rclone_archive_repo = RcloneArchiveRepo(db)
    sessions = SessionService(db, settings)
    admin = AdminAccountStore(db, settings)
    auth_service = AuthService(audit, sessions, admin, settings)
    bootstrap = BootstrapService(admin, audit, settings)

    # 任务链路（增量设计 §1.1：任务即策略；2026-10-15 Q1「选删」后共享字段校验
    # 由 TaskService 自持——原 PolicyService 随 policies 遗留面一并移除）
    task_repo = TaskRepo(db)
    task_service = TaskService(task_repo, settings)

    # 调度器晚于 JobRunner 构造，而自动 forget（N3 裁决 2026-10-14 开放）要在
    # backup 成功回调里调 scheduler——用 late-binding 引用解装配顺序环。
    scheduler_ref: list[SchedulerService] = []

    async def on_task_backup_success(task_id: int) -> None:
        """backup success 终态回调：清 dry_run_pending（X6）+ 自动 forget 判定（N3 开放）。"""
        await task_repo.clear_dry_run_pending(task_id)
        if scheduler_ref:
            await scheduler_ref[0].maybe_auto_forget(task_id)

    # restic 执行链路（§7.1/§7.2/§7.3）
    job_repo = JobRepo(db)
    path_mapper = PathMapper(settings)
    # 凭据来源以适配器注入（Q2 裁决 A）：ResticCmd 不 import 配置仓储/凭据服务，
    # 由装配处把 load_credentials 绑定进来；probe() 因此能一键完成「凭据 → 环境 → argv」。
    restic_cmd = ResticCmd(
        settings,
        path_mapper,
        credentials=lambda repo_id: load_credentials(config_repo, repo_id),
    )
    parser = OutputParser()
    sse_channel = SseChannel(parser, job_repo, heartbeat_seconds=settings.sse_heartbeat_seconds)
    # R-018：真实 Notifier（healthchecks/webhook/MeoW；全渠道未配置时零网络调用）。
    # repos_repo 用于把无任务关联的手动操作（init/unlock/…）在通知里显示为仓库名
    notifier = Notifier(config_repo, repos_repo)
    job_runner = JobRunner(
        db,
        restic_cmd,
        parser,
        audit,
        settings=settings,
        task_loader=task_repo.get_dict,
        # 任务终态（init/unlock 会改变仓库可连性）→ E30 状态缓存立即失效
        on_finish=invalidate_repo_status_cache,
        # X6 + N3：backup success 终态且 task_id 非空 → 清 dry_run_pending + prune 到期自动 forget
        on_task_backup_success=on_task_backup_success,
        # R-018：真实 Notifier（healthchecks/webhook/MeoW；全渠道未配置时零网络调用）
        notifier=notifier,
    )

    # 保留服务（R-020）与缓存服务（R-021）：retention 挂调度器每日维护任务；
    # cache_service 由 E25 路由经 app.state 消费
    # 删除保护方案 §5：归档生命周期（归档期 / 墓碑 / rclone 归档三个保留期）挂同一个维护任务
    retention = RetentionService(
        job_repo,
        config_repo,
        audit,
        settings,
        repos=repos_repo,
        rclone_archives=rclone_archive_repo,
    )
    cache_service = CacheService(settings)
    # 覆盖比对服务（R-015 E20 预览 + 原位恢复审计计数；stateless，只持有依赖）
    compare_service = CompareService(restic_cmd, parser, config_repo, repos_repo=repos_repo)
    # A1 rclone 只读探测器（Q17：listremotes 探测；无状态，URL 校验与端点共用）
    rclone_probe = RcloneProbe(settings)
    # rclone conf 管理服务（配置生成工具：增删改查全走官方 CLI；写操作自带 Lock）
    # 凭据形态记录落 DB 表（裁决 16 迁库，随 DB 备份/迁移走）
    rclone_form_repo = RcloneFormRepo(db)
    rclone_config_service = RcloneConfigService(settings, forms=rclone_form_repo)
    # 旧 sidecar 文件一次性导入 DB（导入后删除文件；重复启动不会重复导入）
    imported_forms = await import_legacy_sidecar(rclone_form_repo, settings.data_dir)
    if imported_forms:
        logger.info("rclone 凭据形态记录已从旧 sidecar 导入数据库：%d 段", imported_forms)
    # P1：快照目录（一次快照列表 → 按 tag 分组计数；任务卡片 snapshot_count 数据源）
    snapshot_catalog = SnapshotCatalog()
    # 候选 1：仓库生命周期编排器——delete/purge/release/rebuild 的顺序契约与并发互斥收在这里
    repo_lifecycle = RepoLifecycleService(
        db=db,
        repos=repos_repo,
        config=config_repo,
        jobs=job_repo,
        audit=audit,
        settings=settings,
    )

    # 调度器（R-013 本批落地，增量设计 §4）：per-task cron + 启动补跑 + 动态重载 + 维护任务域
    scheduler = SchedulerService(
        task_repo,
        job_runner,
        config_repo,
        settings=settings,
        jobs=job_repo,
        retention=retention,
        # dump.cleanup 每日维护兜底的审计埋点（缺省 None 时埋点跳过）
        audit=audit,
    )
    scheduler_ref.append(scheduler)

    app.state.settings = settings
    app.state.db = db
    app.state.audit = audit
    app.state.config_repo = config_repo
    app.state.repos_repo = repos_repo
    app.state.encryption = encryption
    app.state.sessions = sessions
    app.state.admin = admin
    app.state.auth_service = auth_service
    app.state.bootstrap = bootstrap
    app.state.task_repo = task_repo
    app.state.task_service = task_service
    app.state.exclusions = ExclusionRules(settings)
    app.state.job_repo = job_repo
    app.state.path_mapper = path_mapper
    app.state.restic = restic_cmd
    app.state.parser = parser
    app.state.sse_channel = sse_channel
    app.state.job_runner = job_runner
    app.state.scheduler = scheduler
    app.state.retention = retention
    app.state.cache_service = cache_service
    app.state.compare_service = compare_service
    app.state.notifier = notifier
    app.state.rclone_probe = rclone_probe
    app.state.rclone_config_service = rclone_config_service
    app.state.rclone_archive_repo = rclone_archive_repo
    app.state.snapshot_catalog = snapshot_catalog
    app.state.repo_lifecycle = repo_lifecycle

    # 首启引导准备：确保 admin_account 行存在，并缓存随机密码明文（不入 docker logs）
    await bootstrap.ensure_initial_admin()
    # 启动期惰性清理过期会话行（§8）
    purged = await sessions.purge_expired()
    if purged:
        logger.info("启动清理过期会话 %d 条", purged)
    # R-023 一次性迁移（marker 幂等）：config 的 repo.* 单例键 → repos.default、
    # cred.* → cred.default.*（E4 裁决；必须早于 env 同步——env 同步的新落点
    # 已是 repos 表/命名空间键，晚一步会把 env 值写回已废弃的旧键）
    await migrate_legacy_repo_config(config_repo, repos_repo)
    # R-025：先按持久化键校准加密开关（缺省=开），再跑 env 同步——保证 env 写入的
    # 存储形态与开关一致；随后把存量明文敏感键迁为密文（幂等，混合形态可读）
    flag = await config_repo.get_value(CRED_ENCRYPTION_KEY)
    encryption.enabled = flag != "0"
    if encryption.enabled and not credential_crypto.available():
        logger.warning(
            "凭据加密已启用但仓库密码文件缺失（%s）：新凭据写入将失败，直至密码文件就位",
            credential_crypto.passfile(),
        )
    # env→config 一次性同步：使 E07 的 ENV_OVERRIDDEN 真正触发得到（§6 env 覆盖优先）
    await sync_env_overrides(config_repo, settings, audit)
    # R-023：RESTIC_REPOSITORY / RESTIC_HOST → repos.default（env 只覆盖默认仓库）
    await sync_env_repo_overrides(repos_repo)
    # 系统域（compose → UI 迁移第一批）：import 期日志只能按 env 兜底配置，DB 此时
    # 才连上——按 config 值校正一次（env 覆盖优先的语义由 config 键本身承载：
    # env 设了 → sync_env_overrides 已写入且 source='env'，此处读到的就是 env 值）。
    # 日志行首时间戳刻意不跟随 config.timezone（容器机器时钟，见方案 §4.1 边界）。
    effective_log_level = apply_log_level(await config_repo.get_value(LOG_LEVEL_KEY))
    if effective_log_level != _log_level_name().upper():
        logger.info("日志级别按 config 生效：%s", effective_log_level)
    raw_busy = (await config_repo.get_value(DB_BUSY_TIMEOUT_MS_KEY) or "").strip()
    if raw_busy.isdigit():
        await db.apply_busy_timeout(int(raw_busy))
        logger.info("SQLite 锁等待按 config 生效：%sms", raw_busy)
    if encryption.enabled:
        migrated = await config_repo.migrate_encryption(encrypt=True)
        if migrated:
            logger.info("凭据加密已启用：%d 个存量明文键已迁移为密文", migrated)
    # 启动恢复：遗留 running/pending 改标 interrupted（P1 不做补跑与 AUTO_UNLOCK）
    await job_runner.recover_on_startup()
    # 调度器启动（§4.3）：recover 之后 start（内部 reload + 当天补跑），
    # 与 interrupted 改标共享同一启动窗口（US-026-07 AC4）
    await scheduler.start()

    try:
        yield
    finally:
        # 容器 stop 收尾（§4.3 时序）：先停调度（不再触发新任务）再收尾 restic 子进程
        await scheduler.shutdown()
        await job_runner.on_shutdown()
        await close_database()


def create_app() -> FastAPI:
    """构造 FastAPI 应用实例（供 uvicorn / 测试使用）。"""
    app = FastAPI(
        title="restic-web",
        version="1.0.0",
        description="restic-docker-backup Web 管理端（P1 基础设施 + 鉴权闭环）",
        lifespan=lifespan,
    )

    # ① CSRF/Origin（仅写方法）——Starlette 后注册的中间件更靠外，此处为唯一前置中间件
    app.add_middleware(CSRFOriginMiddleware)

    # 免鉴权路由：E04 health（HEALTHCHECK）
    app.include_router(health_router.router)
    # auth 路由：E01/E02/E03/bootstrap/E28（②③ 由路由内部按端点声明）
    app.include_router(auth_router.router)
    # E39 系统时间/时区同步（服务器时间 + NTP 对时 + 系统时钟校准；②③ 由 _PROTECTED 声明）
    app.include_router(system_router.router)
    # 业务路由：②③ 必须显式挂载，否则首启未改密也能访问（PRD US-A1 AC2）
    protected = [Depends(require_session), Depends(require_bootstrap_done)]
    app.include_router(sources_router.router, dependencies=protected)    # E05/E31
    app.include_router(fs_router.router, dependencies=protected)         # E33 容器文件系统浏览
    app.include_router(config_router.router, dependencies=protected)     # E06/E07（E29/E30 已迁 repos）
    app.include_router(repos_router.router, dependencies=protected)      # R-023 仓库 CRUD + E29/E30 per-repo
    app.include_router(jobs_router.router, dependencies=protected)       # E12–E16/E27
    app.include_router(backup_tasks_router.router, dependencies=protected)  # E34–E36（选项② R-026）
    app.include_router(snapshots_router.router, dependencies=protected)  # E17/E18
    app.include_router(ops_router.router, dependencies=protected)        # E21 forget 预览（R-016）
    app.include_router(checks_router.router, dependencies=protected)     # E22 check 结果（R-017，批次 B）
    app.include_router(restore_router.router, dependencies=protected)    # E19
    # E37/E38（R-024）：不挂全局 protected——E37 指标端点自有鉴权（Bearer 令牌或
    # session 任一通过，owner 裁决 1C）；E38 报表端点路由内显式挂 _PROTECTED
    app.include_router(metrics_router.router)
    # 后续批次（dump/notify/cache/audit…）在此按同样方式挂载：
    #   app.include_router(x_router.router, dependencies=protected)

    _register_exception_handlers(app)
    # SPA 静态资源最后挂载：Starlette 按注册顺序匹配，业务 /api 路由始终优先于 "/" 挂载
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """挂载前端 SPA 静态产物（单容器方案 §2：8000 端口同时提供 API 与页面）。

    目录不存在（本地后端开发态）时**不挂载任何静态路由**，页面由 Vite dev server 提供；
    此时 `/` 仍返回统一 JSON 404，与 API 客户端预期一致。
    """
    settings = get_settings()
    static_dir = Path(settings.static_dir)
    index = static_dir / "index.html"
    if not index.is_file():
        logger.info("静态目录 %s 无 index.html，跳过 SPA 挂载（开发态页面由 Vite 提供）", static_dir)
        return
    app.state.spa_index = index
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="spa")
    logger.info("已挂载 SPA 静态资源：%s", static_dir)


def _is_spa_navigation(request: Request) -> bool:
    """判断 404 是否应回落为 SPA 页面导航（前端路由命中，而非资源缺失）。

    三条排除：① 非 GET/HEAD；② `/api` 前缀——API 客户端必须拿到统一 JSON 错误；
    ③ 末段带扩展名——确为缺失的静态资源，回落成 HTML 会掩盖真实问题。
    """
    if request.method not in ("GET", "HEAD"):
        return False
    path = request.url.path
    if path == "/api" or path.startswith("/api/"):
        return False
    return "." not in path.rsplit("/", 1)[-1]


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
    async def _handle_http_exception(request: Request, exc: Exception) -> Response:
        status_code = getattr(exc, "status_code", 500)
        spa_index = getattr(request.app.state, "spa_index", None)
        if status_code == 404 and spa_index is not None and _is_spa_navigation(request):
            # SPA 深链（/snapshots/xxx 等前端路由）回落 index.html，交由前端路由处理
            return FileResponse(spa_index, media_type="text/html", headers={"Cache-Control": "no-cache"})
        code = STATUS_FALLBACK_CODE.get(status_code, INTERNAL)
        payload = ErrorResponse(code=code, message=str(getattr(exc, "detail", "")) or "请求失败")
        return JSONResponse(status_code=status_code, content=payload.model_dump(exclude_none=True))

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> Response:
        # 兜底：即便 HTTPException 的专用处理器未生效，也保证响应体仍是统一错误结构
        if isinstance(exc, StarletteHTTPException):
            return await _handle_http_exception(request, exc)
        logger.exception("未处理异常：%s %s", request.method, request.url.path)
        payload = ErrorResponse(code=INTERNAL, message="服务器内部错误")
        return JSONResponse(status_code=ERROR_STATUS[INTERNAL], content=payload.model_dump(exclude_none=True))


# 导入期装好 root handler：create_app() 内就有 INFO 级启动日志（如 SPA 挂载结果），
# 晚一步配置会被 lastResort handler 按 WARNING 门槛丢弃。
_configure_logging()
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
        # 不传 log_config 时 uvicorn 用自己的默认格式（无时间戳），故显式给出
        "log_config": _uvicorn_log_config(),
        "log_level": _log_level_name().lower(),
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
