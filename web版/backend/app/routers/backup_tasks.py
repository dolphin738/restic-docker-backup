"""备份任务路由（增量设计 §2.1，选项② R-026）：E34 列表/详情、E35 增改删、E36 dry-run、
P2 单任务配置导出。

repo 作用域（E6）：路径前缀 ``/api/repos/{repo_id}/backup-tasks``；``repo_id`` 仅接受
``default``（P2 唯一仓库），其余值 404 ``REPO_NOT_FOUND``。R-023 多仓库时零改路由
（加行 + 仓库切换器）。

鉴权：全部端点挂 ``require_session`` + ``require_bootstrap_done``；写方法经 Origin
同源校验（S4，中间件①+路由依赖②③）。DB 访问仅经 ``app.state`` 上的仓储实例（A1）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_CONFIG_EXPORT,
    AUDIT_TASK_CREATE,
    AUDIT_TASK_DELETE,
    AUDIT_TASK_UPDATE,
    DRY_RUN_MAX_ITEMS,
    DRY_RUN_TIMEOUT_SECONDS,
    PSEUDO_FS_NAMES,
)
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import Job, JobRepo
from app.db.repositories.repo_repo import ReposRepo
from app.db.repositories.task_repo import Task, TaskRepo
from app.errors import NotFoundError, PathOutOfPrefixError, RepoNotFoundError, TaskRunningError, ValidationError
from app.exclusions import ExclusionRules, normalize_user_excludes
from app.schemas.api_schemas import (
    ConfigExportRequest,
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
from app.services.config_transfer import PACK_KIND_TASK, build_export_pack
from app.services.exclude_spec import (
    assemble_excludes,
    flatten_exclude_spec,
    spec_from_wire,
)
from app.services.parser import FileRecord, OutputParser
from app.services.path_mapper import PathMapper, source_allowed_prefixes
from app.services.restic import ResticCmd, WhitelistArgs
from app.services.scheduler import SchedulerService
from app.services.snapshot_catalog import SnapshotCatalog
from app.utils.time import utc_iso

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


async def _validate_mirror_ids(request: Request, repo_id: str, mirror_ids: list[str]) -> None:
    """C4 多目的地：镜像仓须存在且不能与主仓库相同（422）。

    运行期 JobRunner 也会对缺失/自指做优雅降级（标失败或跳过），但路由层前置拒绝
    能给前端即时反馈，避免「保存成功但某个镜像仓其实根本没收到数据」的静默陷阱。
    """
    if not mirror_ids:
        return
    repos_repo: ReposRepo = request.app.state.repos_repo
    for mid in mirror_ids:
        if mid == repo_id:
            raise ValidationError("镜像仓不能与主仓库相同", data={"mirror_repo_id": mid})
        repo = await repos_repo.get(mid)
        if repo is None or not (repo.url or "").strip():
            raise ValidationError("镜像仓不存在或未配置地址", data={"mirror_repo_id": mid})


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
    ``JobRepo.latest_backup_duration_ms_by_tasks``（P1：同上一次运行的耗时）+
    ``SnapshotCatalog.counts_by_tag``（P1：一次快照列表按 tag 分组）+
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
    durations = await job_repo.latest_backup_duration_ms_by_tasks([task.id for task in tasks])
    # B4（owner 2026-10-23 裁决）：任务为空时不做快照探测（否则空列表也白跑一次 5s 级子进程）；
    # 探测超时与失败退避由 SnapshotCatalog 内部承担（5s + 指数退避），路由层不再阻塞 20s。
    snapshot_counts = await _snapshot_counts_by_tag(request, repo_id) if tasks else None

    cards = [
        await _to_card(
            task,
            active_by_task.get(task.id),
            latest_results.get(task.id),
            scheduler,
            durations.get(task.id),
            snapshot_counts,
        )
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
    await _validate_mirror_ids(request, repo_id, values.get("mirror_repo_ids") or [])
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
    await _validate_mirror_ids(request, repo_id, values.get("mirror_repo_ids") or [])
    # US-026-02 AC2：sources/excludes 与库中现值不同 → 重新出现 dry-run 门
    if (
        values["sources"] != existing.sources
        or values["excludes"] != existing.excludes
        or values.get("exclude_spec") != existing.exclude_spec
    ):
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


# ---------------------------------------------------------------- P2 单任务配置导出


@router.post(
    "/{task_id}/export",
    response_class=Response,
    summary="P2 单任务配置导出",
    dependencies=_PROTECTED,
)
async def export_task(
    repo_id: str,
    task_id: int,
    payload: ConfigExportRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> Response:
    """导出**单个**备份任务的配置包（UI 改造 §4.5.5，Duplicati 单备份 Export 对位）。

    与全局导出（``POST /api/config/export``）的差别：

    - ``kind=backup-task`` —— 导入侧据此分流，**绝不套用全量包的覆盖语义**（R-5）；
    - ``tasks`` 只有这一条；``repos`` 只有该任务所属仓 + 其引用的镜像仓；
    - ``notify`` / ``behavior`` 不带——那是**全局**设置，不该搭单任务包顺风车；
    - 凭据只带任务相关仓的 ``cred.{repo_id}.*``，且仍走口令加密段（S3：响应无明文）。

    口令校验与打包管线完全复用 :func:`build_export_pack`（不另造一套加密），
    审计埋 ``config.export``，detail 只含任务名与计数（**不含口令、不含凭据**）。
    """
    del session
    await _require_repo(request, repo_id)
    task_repo: TaskRepo = request.app.state.task_repo
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo

    task = await task_repo.get(task_id)
    if task is None or task.repo_id != repo_id:
        raise NotFoundError("任务不存在")
    detail = await task_repo.get_dict(task_id)
    if detail is None:  # pragma: no cover - 前置已确认存在，理论不可达
        raise NotFoundError("任务不存在")

    repo_ids = _export_repo_ids(task)
    repo_entries: list[dict[str, Any]] = []
    for rid in repo_ids:
        repo = await repos_repo.get(rid)
        if repo is not None:
            repo_entries.append(
                {"id": repo.id, "name": repo.name, "url": repo.url, "host_tag": repo.host_tag}
            )
    cred_prefixes = tuple(f"cred.{rid}." for rid in repo_ids)
    # 该任务相关仓的凭据行；按 cred.<repo>.* 前缀过滤是**刻意设计**（方案 §4.3 改造
    # 禁区：问的是「这个仓库的凭据有哪些」，不是「这个密钥源保护哪些键」，改 keys_of
    # 会把 dump.password 等全局键打进任务导出包 = S3 红线）。
    scoped_rows = [row for row in await config_repo.list_all() if row.key.startswith(cred_prefixes)]
    config_rows = [(row.key, row.value or "") for row in scoped_rows]
    degraded_rows = {row.key: (row.degraded_reason or "") for row in scoped_rows if row.degraded}

    pack = build_export_pack(
        repos=repo_entries,
        tasks=[detail],
        degraded_rows=degraded_rows,
        config_rows=config_rows,
        password=payload.password,
        created_at=utc_iso(),
        kind=PACK_KIND_TASK,
    )
    await request.app.state.audit.record(
        AUDIT_CONFIG_EXPORT,
        target=f"task:{task_id}",
        detail={
            "kind": PACK_KIND_TASK,
            "name": task.name,
            "repos": len(repo_entries),
            "creds": len(config_rows),
        },
        result="ok",
    )
    stamp = str(pack["created_at"])[:10]
    logger.info("备份任务配置已导出：id=%s name=%s repos=%s", task_id, task.name, len(repo_entries))
    return Response(
        content=json.dumps(pack, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="restic-web-task-{task_id}-{stamp}.json"'},
    )


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
    task_repo: TaskRepo = request.app.state.task_repo

    task = await task_repo.get(task_id)
    if task is None or task.repo_id != repo_id:
        raise NotFoundError("任务不存在")

    raw_sources = payload.sources if payload.sources is not None else task.sources
    raw_excludes = payload.excludes if payload.excludes is not None else task.excludes
    if not raw_sources:
        raise ValidationError("任务尚未配置备份源（请先保存 sources 后再预览）", data={"task_id": task_id})

    sources = tuple(
        _to_container_path(mapper, item, allowed_prefixes=source_allowed_prefixes(settings))
        for item in raw_sources
    )
    # 批次②：优先用请求体 spec（向导未保存改动），否则任务存量 spec（含大小/标记/
    # 缓存/icase），与真实备份同口径；两者皆无（旧任务或新建未存）→ 回落扁平 excludes。
    spec_raw = payload.exclude_spec or task.exclude_spec
    if spec_raw:
        flat = flatten_exclude_spec(spec_from_wire(spec_raw), mapper)
        # A 裁决（owner 2026-10-24）：内置集排**队尾**（后命中者赢 → 真强制）。
        excludes = assemble_excludes(flat.excludes, ExclusionRules(settings).built_in())
        iexcludes, larger_than = flat.iexcludes, flat.larger_than
        if_present, caches = flat.if_present, flat.caches
    else:
        user_excludes = normalize_user_excludes(
            list(raw_excludes), mapper, source_prefixes=source_allowed_prefixes(settings)
        )
        excludes = assemble_excludes(user_excludes, ExclusionRules(settings).built_in())
        iexcludes, larger_than = (), None
        if_present, caches = (), False
    args = WhitelistArgs(
        targets=sources,
        excludes=excludes,
        iexcludes=iexcludes,
        exclude_larger_than=larger_than,
        exclude_if_present=if_present,
        exclude_caches=caches,
        json_output=True,
        dry_run=True,
        verbose=True,
        one_file_system=task.one_file_system,
    )

    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    argv = restic.build("backup", args=args, env_extras=await restic.credential_extras(repo_id))

    items: list[DryRunItem] = []
    warnings: list[DryRunWarning] = []
    truncated = False

    async def _on_line(line: str) -> bool:
        """逐行解析；达到条目上限时返回真值要求机制层停止读取。"""
        nonlocal truncated
        event = await asyncio.to_thread(parser.parse_line, line)
        if not isinstance(event, FileRecord):
            return False
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
            return True
        return False

    result = await restic.consume(
        argv,
        repo_url=repo_url,
        repo_id=repo_id,
        timeout_seconds=DRY_RUN_TIMEOUT_SECONDS,
        on_line=_on_line,
        log_prefix="dryrun-task",
    )
    if result.stopped_early:
        truncated = True
    error: str | None = None
    if result.timed_out:
        truncated = True
        error = f"预览超时（>{DRY_RUN_TIMEOUT_SECONDS}s），结果不完整"
        logger.warning("任务 dry-run 预览超时：task=%s sources=%s", task_id, sources)

    if error is None and not truncated and result.exit_code != 0:
        error = f"restic 退出码 {result.exit_code}：仓库可能未初始化或凭据缺失"

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
    last_duration_ms: int | None = None,
    snapshot_counts: dict[str, int] | None = None,
) -> TaskCardOut:
    """把任务行 + 聚合数据装配为任务卡片（§1.2 派生规则，路由内聚合无新 SQL）。

    派生状态优先级：``enabled=0 → paused`` > 活跃 job（running/pending）>
    最近一次 backup 终态 > ``never_run``。

    Args:
        task: 任务行。
        active_job: 该任务的活跃 job（running/pending）；无则 None。
        last_result: 最近一次终态 backup 状态。
        scheduler: 调度器（未装配时 None）。
        last_duration_ms: 与 ``last_result`` 同一次运行的耗时（毫秒）。
        snapshot_counts: ``{tag: 快照数}``；**None 表示不可知**（卡片字段置 null，
            不可退化成 0——0 会被读成「任务还没产出快照」）。
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
        last_duration_ms=last_duration_ms,
        snapshot_count=None if snapshot_counts is None else snapshot_counts.get(task.tag, 0),
        limit_up=task.limit_up_kibps,
        limit_down=task.limit_down_kibps,
        next_run_at=next_run_at,
        running_job_id=active_job.id if active_job is not None else None,
        dry_run_pending=task.dry_run_pending,
        prune_due=prune_due,
        updated_at=task.updated_at,
    )


def _export_repo_ids(task: Task) -> list[str]:
    """单任务导出包要带的仓库 id：所属仓 + 引用的镜像仓（保序去重）。

    镜像仓必须同包带出——导入侧 ``mirror_ids_for_import`` 的校验基准是「包内仓库
    清单」，缺了会一律置空 + 警告，任务落地后静默丢掉多目的地语义。
    """
    ordered = [task.repo_id, *task.mirror_repo_ids]
    seen: set[str] = set()
    return [rid for rid in ordered if rid and not (rid in seen or seen.add(rid))]


async def _snapshot_counts_by_tag(request: Request, repo_id: str) -> dict[str, int] | None:
    """取一次快照列表并按 tag 分组计数（P1：卡片 ``snapshot_count`` 数据源）。

    未配置仓库地址 / ``snapshot_catalog`` 未装配 / 探测失败 → ``None``（卡片字段降级
    null）。环境经 ``ResticCmd.env_for()`` 组装后**整体传入**服务，本路由不接触明文（S3）。
    """
    catalog: SnapshotCatalog | None = getattr(request.app.state, "snapshot_catalog", None)
    if catalog is None:
        return None
    repos_repo: ReposRepo = request.app.state.repos_repo
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser

    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    if not repo_url.strip():
        return None
    env = await restic.env_for(repo_url=repo_url, repo_id=repo_id)
    return await catalog.counts_by_tag(
        restic=restic, parser=parser, repo_id=repo_id, repo_url=repo_url, env=env
    )


def _to_container_path(
    mapper: PathMapper,
    raw: str,
    *,
    allowed_prefixes: tuple[str, ...],
) -> str:
    """把 E36 的路径入参还原为容器内路径（与 E31 ``_to_container_path`` 同口径）。

    新契约收**展示路径**（``/etc/nginx``）→ ``to_snapshot`` 还原；兼容容器路径形态：
    入参已带当前模式根前缀（A0 收敛后即 ``/sources``）时原样通过校验（任务存量值即此形态）。

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


