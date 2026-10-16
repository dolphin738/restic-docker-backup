"""备份任务路由（增量设计 §2.1，选项② R-026）：E34 列表/详情、E35 增改删、E36 dry-run。

repo 作用域（E6）：路径前缀 ``/api/repos/{repo_id}/backup-tasks``；``repo_id`` 仅接受
``default``（P2 唯一仓库），其余值 404 ``REPO_NOT_FOUND``。R-023 多仓库时零改路由
（加行 + 仓库切换器）。

鉴权：全部端点挂 ``require_session`` + ``require_bootstrap_done``；写方法经 Origin
同源校验（S4，中间件①+路由依赖②③）。DB 访问仅经 ``app.state`` 上的仓储实例（A1）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_TASK_CREATE,
    AUDIT_TASK_DELETE,
    AUDIT_TASK_UPDATE,
    DRY_RUN_MAX_ITEMS,
    DRY_RUN_TIMEOUT_SECONDS,
    PSEUDO_FS_NAMES,
    SOURCE_ALLOWED_PREFIXES,
)
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import Job, JobRepo
from app.db.repositories.repo_repo import ReposRepo
from app.db.repositories.task_repo import Task, TaskRepo
from app.errors import NotFoundError, PathOutOfPrefixError, RepoNotFoundError, TaskRunningError, ValidationError
from app.exclusions import ExclusionRules, normalize_user_excludes
from app.schemas.api_schemas import (
    DryRunItem,
    DryRunResponse,
    DryRunWarning,
    OkResponse,
    Page,
    TaskCardOut,
    TaskDryRunRequest,
    TaskOut,
    TaskUpsertRequest,
)
from app.services.env_sync import load_credentials
from app.services.parser import FileRecord, OutputParser
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs
from app.services.scheduler import SchedulerService
from app.utils.ulid import new_ulid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos/{repo_id}/backup-tasks", tags=["backup-tasks"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# 卡片派生状态词表（§1.2：backup_tasks 无 status 列，卡片状态为派生值）
CARD_PAUSED = "paused"
CARD_NEVER_RUN = "never_run"


async def _require_repo(request: Request, repo_id: str) -> None:
    """repo 作用域守卫（R-023）：接受**任意已存在**的仓库 id，其余 404 REPO_NOT_FOUND。

    Args:
        request: 当前请求（取 repos_repo）。
        repo_id: 路径前缀中的仓库作用域 id。

    Raises:
        RepoNotFoundError: 仓库不存在（404，data 带 repo_id）。
    """
    repos_repo: ReposRepo = request.app.state.repos_repo
    if not await repos_repo.exists(repo_id):
        raise RepoNotFoundError(repo_id)


async def _reload_scheduler(request: Request) -> None:
    """保存/删除后动态重载调度触发器（US-026-02 AC1：保存即生效，无需重启容器）。

    调度器未装配（测试替身场景）时静默跳过——此时不存在任何触发器需要重建。
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return
    await scheduler.reload()


# ---------------------------------------------------------------- E34 列表 / E34' 详情


@router.get(
    "",
    response_model=Page[TaskCardOut],
    summary="E34 任务卡片列表",
    dependencies=_PROTECTED,
)
async def list_tasks(
    repo_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(require_session),
) -> Page[TaskCardOut]:
    """任务卡片列表（US-026-06）：派生状态在路由内聚合，无新 SQL 散出。

    数据装配（§2.1）：``TaskRepo.list_all`` + ``JobRepo.list_active``（进行中）+
    ``JobRepo.latest_backup_by_tasks``（每任务最近一次 backup 终态）+
    ``SchedulerService.next_fire_time`` / ``_due_for_prune``（cron 推导与 prune 提示）。
    """
    del session
    await _require_repo(request, repo_id)
    task_repo: TaskRepo = request.app.state.task_repo
    job_repo: JobRepo = request.app.state.job_repo
    scheduler = getattr(request.app.state, "scheduler", None)

    tasks = await task_repo.list_all(repo_id)
    active_jobs = await job_repo.list_active(repo_id)
    active_by_task: dict[int, Job] = {}
    for job in active_jobs:
        if job.task_id is not None:
            active_by_task.setdefault(job.task_id, job)
    latest_results = await job_repo.latest_backup_by_tasks([task.id for task in tasks])

    cards = [
        await _to_card(task, active_by_task.get(task.id), latest_results.get(task.id), scheduler)
        for task in tasks
    ]
    window = cards[offset : offset + limit]
    return Page(items=window, total=len(cards), limit=limit, offset=offset)


@router.get(
    "/{task_id}",
    response_model=TaskOut,
    summary="E34' 任务详情/编辑预填",
    dependencies=_PROTECTED,
)
async def get_task(
    repo_id: str,
    task_id: int,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> TaskOut:
    """返回任务全字段 + display 派生（tag 只读露出）；不存在返回 404 NOT_FOUND。"""
    del session
    await _require_repo(request, repo_id)
    task = await request.app.state.task_repo.get(task_id)
    if task is None or task.repo_id != repo_id:
        raise NotFoundError("任务不存在")
    return TaskOut(**request.app.state.task_service.to_api(task))


# ---------------------------------------------------------------- E35 创建 / E35' 更新 / E35'' 删除


@router.post(
    "",
    status_code=201,
    response_model=TaskOut,
    summary="E35 新建备份任务",
    dependencies=_PROTECTED,
)
async def create_task(
    repo_id: str,
    payload: TaskUpsertRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> TaskOut:
    """创建任务（US-026-01）：normalize（共享字段委托策略域）→ create（dry_run_pending=1、
    tag 回写 ``task-<id>``）→ 审计 ``task.create`` → 调度器 reload → 201 TaskOut。"""
    del session
    await _require_repo(request, repo_id)
    service = request.app.state.task_service
    task_repo: TaskRepo = request.app.state.task_repo

    values = service.normalize(payload.model_dump())
    values["repo_id"] = repo_id  # 任务归属路径作用域仓库（E7）
    task = await task_repo.create(values)
    await request.app.state.audit.record(
        AUDIT_TASK_CREATE, target=str(task.id), detail={"name": task.name, "tag": task.tag}, result="ok"
    )
    await _reload_scheduler(request)
    logger.info("备份任务已创建：id=%s name=%s tag=%s", task.id, task.name, task.tag)
    return TaskOut(**service.to_api(task))


@router.put(
    "/{task_id}",
    response_model=TaskOut,
    summary="E35' 更新备份任务",
    dependencies=_PROTECTED,
)
async def update_task(
    repo_id: str,
    task_id: int,
    payload: TaskUpsertRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> TaskOut:
    """更新任务（US-026-02，全量语义）：存在性校验 → normalize → 源集合变更则
    ``dry_run_pending=1``（AC2）→ tag 永不被写 → 调度器 reload → 审计 → 200 TaskOut。"""
    del session
    await _require_repo(request, repo_id)
    task_repo: TaskRepo = request.app.state.task_repo
    service = request.app.state.task_service

    existing = await task_repo.get(task_id)
    if existing is None or existing.repo_id != repo_id:
        raise NotFoundError("任务不存在")

    values = service.normalize(payload.model_dump())
    # US-026-02 AC2：sources/excludes 与库中现值不同 → 重新出现 dry-run 门
    if values["sources"] != existing.sources or values["excludes"] != existing.excludes:
        values["dry_run_pending"] = 1
    updated = await task_repo.update(task_id, values)
    if updated is None:  # pragma: no cover - 前置已确认存在，理论不可达
        raise NotFoundError("任务不存在")

    await request.app.state.audit.record(
        AUDIT_TASK_UPDATE,
        target=str(task_id),
        detail={"name": updated.name, "fields": sorted(values.keys())},
        result="ok",
    )
    await _reload_scheduler(request)
    return TaskOut(**service.to_api(updated))


@router.delete(
    "/{task_id}",
    response_model=OkResponse,
    summary="E35'' 删除备份任务",
    dependencies=_PROTECTED,
)
async def delete_task(
    repo_id: str,
    task_id: int,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """删除任务（US-026-03）：活跃 job 检查（409 TASK_RUNNING）→ 删除（jobs.task_id
    经外键置空、快照不动）→ 调度器移除触发器 → 审计 ``task.delete`` → 200 OkResponse。"""
    del session
    await _require_repo(request, repo_id)
    task_repo: TaskRepo = request.app.state.task_repo
    job_repo: JobRepo = request.app.state.job_repo

    existing = await task_repo.get(task_id)
    if existing is None or existing.repo_id != repo_id:
        raise NotFoundError("任务不存在")

    for job in await job_repo.list_active(repo_id):
        if job.task_id == task_id:
            raise TaskRunningError(job.id, existing.name)

    await task_repo.delete(task_id)
    await request.app.state.audit.record(
        AUDIT_TASK_DELETE,
        target=str(task_id),
        detail={"name": existing.name, "tag": existing.tag},
        result="ok",
    )
    await _reload_scheduler(request)
    logger.info("备份任务已删除：id=%s name=%s tag=%s", task_id, existing.name, existing.tag)
    return OkResponse()


# ---------------------------------------------------------------- E36 dry-run 预览


@router.post(
    "/{task_id}/dry-run",
    response_model=DryRunResponse,
    summary="E36 任务域扫描范围预览",
    dependencies=_PROTECTED,
)
async def dry_run_task(
    repo_id: str,
    task_id: int,
    payload: TaskDryRunRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> DryRunResponse:
    """``restic backup --dry-run --json --verbose`` 任务域预览（R-026-08，E31 同链路）。

    请求体字段均可选，缺省取任务存量 sources/excludes（支撑「源集合变更后重新预览」）；
    **预览成功（无 error 且未截断）即清 ``dry_run_pending=0``**——用户已见完整扫描范围，
    是 US-026-01 AC1 第⑤步「预览→确认」的服务端凭证。截断视为未看全，保守不清标记。
    """
    del session
    await _require_repo(request, repo_id)
    settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    config_repo: ConfigRepo = request.app.state.config_repo
    task_repo: TaskRepo = request.app.state.task_repo

    task = await task_repo.get(task_id)
    if task is None or task.repo_id != repo_id:
        raise NotFoundError("任务不存在")

    raw_sources = payload.sources if payload.sources is not None else task.sources
    raw_excludes = payload.excludes if payload.excludes is not None else task.excludes
    if not raw_sources:
        raise ValidationError("任务尚未配置备份源（请先保存 sources 后再预览）", data={"task_id": task_id})

    sources = tuple(
        _to_container_path(mapper, item, allowed_prefixes=SOURCE_ALLOWED_PREFIXES)
        for item in raw_sources
    )
    user_excludes = normalize_user_excludes(
        list(raw_excludes), mapper, source_prefixes=(settings.mode_root,)
    )
    excludes = tuple(_dedupe(ExclusionRules(settings).built_in() + user_excludes))
    args = WhitelistArgs(
        targets=sources,
        excludes=excludes,
        json_output=True,
        dry_run=True,
        verbose=True,
        one_file_system=task.one_file_system,
    )

    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    env_extras = await load_credentials(config_repo, repo_id)
    argv = restic.build("backup", args=args, env_extras=env_extras)
    env = restic.build_env(repo_url=repo_url, repo_id=repo_id, env_extras=env_extras)

    log_path = settings.jobs_dir / f"dryrun-task-{new_ulid()}.log"
    proc = await restic.spawn(argv, log_path, env=env)

    items: list[DryRunItem] = []
    warnings: list[DryRunWarning] = []
    truncated = False
    error: str | None = None
    exit_code = -1
    try:
        async with asyncio.timeout(DRY_RUN_TIMEOUT_SECONDS):
            async for line in proc.lines():
                event = await asyncio.to_thread(parser.parse_line, line)
                if isinstance(event, FileRecord):
                    file_path = event.path
                    items.append(
                        DryRunItem(
                            path=file_path,
                            display_path=mapper.to_display(file_path),
                            size=event.size,
                        )
                    )
                    warning = _pseudo_warning(file_path)
                    if warning is not None:
                        warnings.append(warning)
                    if len(items) >= DRY_RUN_MAX_ITEMS:
                        truncated = True
                        break
        if truncated:
            proc.kill()
        exit_code = await proc.wait()
    except TimeoutError:
        truncated = True
        error = f"预览超时（>{DRY_RUN_TIMEOUT_SECONDS}s），结果不完整"
        logger.warning("任务 dry-run 预览超时：task=%s sources=%s", task_id, sources)
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
    finally:
        log_path.unlink(missing_ok=True)

    if error is None and not truncated and exit_code != 0:
        error = f"restic 退出码 {exit_code}：仓库可能未初始化或凭据缺失"

    for source in raw_sources:
        warning = _pseudo_warning(source)
        if warning is not None:
            warnings.append(warning)

    if error is None and not truncated:
        # 用户已见完整扫描范围 → 清除首跑门标记（R-026-08）
        await task_repo.clear_dry_run_pending(task_id)

    return DryRunResponse(items=items, warnings=_dedupe_warnings(warnings), truncated=truncated, error=error)


# ---------------------------------------------------------------- 内部实现


async def _to_card(
    task: Task,
    active_job: Job | None,
    last_result: str | None,
    scheduler: SchedulerService | None,
) -> TaskCardOut:
    """把任务行 + 聚合数据装配为任务卡片（§1.2 派生规则，路由内聚合无新 SQL）。

    派生状态优先级：``enabled=0 → paused`` > 活跃 job（running/pending）>
    最近一次 backup 终态 > ``never_run``。
    """
    if not task.enabled:
        status = CARD_PAUSED
    elif active_job is not None:
        status = active_job.status
    elif last_result is not None:
        status = last_result
    else:
        status = CARD_NEVER_RUN

    next_run_at = scheduler.next_fire_time(task) if scheduler is not None else None
    # N3 裁决：prune 到期只判定 + 卡片提示，调度器不自动提交 forget。
    # 设计 §2.1 数据装配明示由路由直接调用 ``_due_for_prune`` 判定（本处为唯一调用点）。
    prune_due = await scheduler._due_for_prune(task) if scheduler is not None else False

    return TaskCardOut(
        id=task.id,
        name=task.name,
        enabled=task.enabled,
        tag=task.tag,
        cron=task.cron,
        status=status,
        last_run_at=task.last_run_at,
        last_result=last_result,
        next_run_at=next_run_at,
        running_job_id=active_job.id if active_job is not None else None,
        dry_run_pending=task.dry_run_pending,
        prune_due=prune_due,
        updated_at=task.updated_at,
    )


def _to_container_path(
    mapper: PathMapper,
    raw: str,
    *,
    allowed_prefixes: tuple[str, ...],
) -> str:
    """把 E36 的路径入参还原为容器内路径（与 E31 ``_to_container_path`` 同口径）。

    新契约收**展示路径**（``/etc/nginx``）→ ``to_snapshot`` 还原；兼容容器路径形态：
    入参已带当前模式根前缀（``/host`` / ``/sources``）时原样通过校验（任务存量值即此形态）。

    Args:
        mapper: 路径映射器。
        raw: 客户端入参（展示路径或容器路径）。
        allowed_prefixes: 允许的容器前缀白名单。

    Returns:
        规范化且通过白名单校验的容器内路径。

    Raises:
        PathOutOfPrefixError: 两种形态都越出白名单（400）。
    """
    try:
        return mapper.validate(raw, allowed_prefixes=allowed_prefixes)
    except PathOutOfPrefixError:
        # 展示路径 → 还原为容器路径后再次校验（normpath 折叠 ../ 防穿越）
        return mapper.validate(mapper.to_snapshot(raw), allowed_prefixes=allowed_prefixes)


def _pseudo_warning(path: str) -> DryRunWarning | None:
    """命中伪文件系统目录名时生成黄色警告（不拦截，方案 §3.2 第三层）。"""
    parts = [part for part in path.split("/") if part]
    for part in parts:
        if part in PSEUDO_FS_NAMES:
            return DryRunWarning(type="pseudo_fs_name", path=path)
    return None


def _dedupe_warnings(warnings: list[DryRunWarning]) -> list[DryRunWarning]:
    """按路径去重警告（扫描过程同一目录可能被多次命中）。"""
    seen: set[str] = set()
    result: list[DryRunWarning] = []
    for warning in warnings:
        if warning.path in seen:
            continue
        seen.add(warning.path)
        result.append(warning)
    return result


def _dedupe(values: list[str]) -> list[str]:
    """按字符串去重并保持顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
