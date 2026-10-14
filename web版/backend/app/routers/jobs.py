"""任务路由：E12 触发 / E13 列表（P2 全量版）/ E14 详情 / E15 SSE 流 / E16 取消 / E27 日志尾部。

鉴权：全部经 ``require_session`` + ``require_bootstrap_done``；SSE 走同一 cookie session
（§8：不使用 query token，避免进反代访问日志）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    HISTORY_QUERY_MAX_LIMIT,
    JOB_STATUSES,
    JOB_TYPES,
    LOG_TAIL_DEFAULT_BYTES,
    LOG_TAIL_MAX_BYTES,
)
from app.errors import NotFoundError, ValidationError
from app.exclusions import normalize_user_excludes
from app.schemas.api_schemas import (
    JobCreateRequest,
    JobCreateResponse,
    JobLogResponse,
    JobOut,
    OkResponse,
    Page,
)
from app.schemas.enums import JobStatus, JobType
from app.utils.time import parse_utc_bound, utc_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["jobs"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]


def _to_out(job: object) -> JobOut:
    """把仓储层 Job 转换为响应模型。"""
    return JobOut(**job.__dict__)  # type: ignore[arg-type]


@router.post(
    "/jobs",
    status_code=202,
    response_model=JobCreateResponse,
    summary="E12 触发任务",
    dependencies=_PROTECTED,
)
async def create_job(
    payload: JobCreateRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> JobCreateResponse:
    """触发一个任务（manual）；同仓库已有 running/pending 时返回 409 JOB_CONFLICT。

    ``task_id`` 与 ``policy_id`` 互斥（同供 422 ``VALIDATION_ERROR``）：任务域 job
    落 ``task_id``（profile 经 ``task_loader`` 加载），legacy 策略 job 落 ``policy_id``。
    ``params.excludes`` 为用户追加的排除规则（展示形态或纯 glob 模式），在**提交前**
    经 :func:`normalize_user_excludes` 三分类归一为容器形态——放在路由层而非 JobRunner，
    越界锚定路径才能以 422 ``VALIDATION_ERROR`` 同步回给前端，而不是把任务打进 failed。

    手动触发同样**记账**（``task_id`` 非空且 ``type=backup`` 时写任务的 ``last_run_at``）
    ——与调度触发路径同口径，否则只跑过手动备份的任务卡片会显示「上次运行：无」；
    非 backup 类型不写，保住「上次备份时间」语义。
    """
    del session
    runner = request.app.state.job_runner
    task_repo = request.app.state.task_repo
    policy_repo = request.app.state.policy_repo
    if payload.task_id is not None and payload.policy_id is not None:
        raise ValidationError(
            "task_id 与 policy_id 互斥，只能指定其中一个",
            data={"task_id": payload.task_id, "policy_id": payload.policy_id},
        )
    # 存在性前置校验（Q1）：非法 task_id 会在 JobRunner 落库时撞 jobs.task_id 外键
    # 抛 sqlite3.IntegrityError → 未捕获即 500。此处提前 422 VALIDATION_ERROR
    # （与其它参数类错误同口径），data 带 task_id 便于前端定位是哪条关联写错。
    if payload.task_id is not None and await task_repo.get(payload.task_id) is None:
        raise ValidationError("备份任务不存在", data={"task_id": payload.task_id})
    # 同上：legacy policy_id 路径撞 jobs.policy_id 外键（0001_init.sql:71）同样 500，
    # 一并前置为 422——两条路径语义同为「引用了不存在的实体」，错误语义必须一致。
    if payload.policy_id is not None and await policy_repo.get(payload.policy_id) is None:
        raise ValidationError("备份策略不存在", data={"policy_id": payload.policy_id})
    params = dict(payload.params)
    if params.get("excludes"):
        # 锚定路径白名单收紧为当前模式根（越界 → 422），见 exclusions.normalize_user_excludes
        params["excludes"] = normalize_user_excludes(
            list(params["excludes"]),
            request.app.state.path_mapper,
            source_prefixes=(request.app.state.settings.mode_root,),
        )
    job = await runner.submit(
        str(payload.type),
        policy_id=payload.policy_id,
        task_id=payload.task_id,
        trigger="manual",
        snapshot_id=payload.snapshot_id,
        params=params,
    )
    # 手动「立即运行」同样记账（与调度触发路径同口径：写入 last_run_at）——否则
    # 只跑过手动备份的任务卡片会显示「上次运行：无」，与「上次运行：[成功]」并存。
    # 仅 backup 类型记账：restore/check/forget/unlock/init/dump 写入会把
    # 「上次备份时间」污染成「上次任何作业时间」，语义必须守住。
    if payload.task_id is not None and payload.type == JobType.BACKUP:
        await task_repo.set_last_run_at(payload.task_id, utc_iso())
    return JobCreateResponse(id=job.id, status=JobStatus(job.status), type=payload.type)


@router.get("/jobs", response_model=Page[JobOut], summary="E13 任务列表（P2 全量版）", dependencies=_PROTECTED)
async def list_jobs(
    request: Request,
    job_type: str | None = Query(default=None, alias="type", description="任务类型筛选（JOB_TYPES 白名单单值）"),
    status: str | None = Query(default=None, description="状态筛选（逗号分隔，全状态白名单；缺省不过滤）"),
    task_id: int | None = Query(default=None, description="按备份任务筛选（N7）"),
    started_from: str | None = Query(default=None, description="started_at 下界（ISO 8601，含时区归一 UTC）"),
    started_to: str | None = Query(default=None, description="started_at 上界（ISO 8601，含时区归一 UTC）"),
    limit: int = Query(default=50, ge=1, le=HISTORY_QUERY_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(require_session),
) -> Page[JobOut]:
    """E13 完整版（US-P2-01，Q13 基线）：全状态筛选 + type/task_id/时间范围维度。

    ``status`` 缺省＝不过滤（P1 语义「仅活跃」由前端显式传 ``running,pending`` 保持，
    ``listActiveJobs`` 已显式传参，无隐式依赖）；时间参数经 :func:`parse_utc_bound`
    归一为 UTC 后进 SQL 比较（C8：无时区后缀按 UTC 解释）。
    """
    del session
    statuses = _parse_statuses(status)
    resolved_type = _parse_job_type(job_type)
    from_bound = parse_utc_bound(started_from)
    if started_from and from_bound is None:
        raise ValidationError("started_from 须为 ISO 8601 时间", data={"field": "started_from", "value": started_from})
    to_bound = parse_utc_bound(started_to)
    if started_to and to_bound is None:
        raise ValidationError("started_to 须为 ISO 8601 时间", data={"field": "started_to", "value": started_to})
    repo = request.app.state.job_repo
    jobs, total = await repo.list_page(
        statuses=statuses,
        job_type=resolved_type,
        task_id=task_id,
        started_from=from_bound,
        started_to=to_bound,
        limit=limit,
        offset=offset,
    )
    return Page(items=[_to_out(job) for job in jobs], total=total, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobOut, summary="E14 任务详情", dependencies=_PROTECTED)
async def get_job(
    job_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> JobOut:
    """返回任务元数据；不存在返回 404。"""
    del session
    job = await request.app.state.job_repo.get(job_id)
    if job is None:
        raise NotFoundError("任务不存在")
    return _to_out(job)


@router.get("/jobs/{job_id}/stream", summary="E15 SSE 进度流", dependencies=_PROTECTED)
async def stream_job(
    job_id: str,
    request: Request,
    last_event_id: int = Query(default=0, ge=0, description="重连游标（浏览器通常经 Last-Event-ID 头携带）"),
    session: SessionContext = Depends(require_session),
) -> StreamingResponse:
    """SSE 流：日志回放（按 last_event_id 行偏移）+ 追加续流 + 15s 心跳 + 终态收流。"""
    del session
    repo = request.app.state.job_repo
    job = await repo.get(job_id)
    if job is None:
        raise NotFoundError("任务不存在")

    header_cursor = request.headers.get("last-event-id")
    cursor = last_event_id
    if header_cursor and header_cursor.isdigit():
        cursor = int(header_cursor)

    channel = request.app.state.sse_channel
    # X-Accel-Buffering: no —— 反向代理禁用缓冲（方案 §5 SSE 细节）
    # 心跳由 SseChannel 自行以 ": keep-alive" 注释行发送，不引入额外 SSE 依赖
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        channel.stream(job, cursor),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post(
    "/jobs/{job_id}/cancel",
    status_code=202,
    response_model=OkResponse,
    summary="E16 取消任务",
    dependencies=_PROTECTED,
)
async def cancel_job(
    job_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """SIGTERM → 30s 宽限 → SIGKILL，终态 cancelled；非 running/pending 返回 409 NOT_CANCELLABLE。"""
    del session
    runner = request.app.state.job_runner
    await runner.cancel(job_id)
    return OkResponse()


@router.get("/jobs/{job_id}/log", response_model=JobLogResponse, summary="E27 任务日志尾部", dependencies=_PROTECTED)
async def job_log(
    job_id: str,
    request: Request,
    tail_bytes: int = Query(default=LOG_TAIL_DEFAULT_BYTES, ge=1, le=LOG_TAIL_MAX_BYTES),
    session: SessionContext = Depends(require_session),
) -> JobLogResponse:
    """返回日志文件尾部内容（日志详情抽屉与 skipped 明细数据源，R-012）。"""
    del session
    job = await request.app.state.job_repo.get(job_id)
    if job is None:
        raise NotFoundError("任务不存在")

    path = Path(job.log_path)
    if not path.exists():
        return JobLogResponse(id=job_id, log_path=job.log_path, tail_bytes=0, content="", truncated=False)

    size = path.stat().st_size
    start = max(0, size - tail_bytes)
    content = await _read_tail(path, start, tail_bytes)
    return JobLogResponse(
        id=job_id,
        log_path=job.log_path,
        tail_bytes=min(tail_bytes, size),
        content=content,
        truncated=size > tail_bytes,
    )


async def _read_tail(path: Path, start: int, length: int) -> str:
    """从指定偏移读取指定字节数并解码为文本。"""
    import asyncio

    def _read() -> str:
        with path.open("rb") as handle:
            handle.seek(start)
            return handle.read(length).decode("utf-8", errors="replace")

    return await asyncio.to_thread(_read)


def _parse_statuses(status: str | None) -> tuple[str, ...] | None:
    """解析并校验 E13 的 status 过滤参数（P2 全状态；缺省＝不过滤，返回 None）。

    P1 语义「仅活跃」不再由缺省承载——前端 ``listActiveJobs`` 显式传
    ``running,pending``（api/jobs.ts），行为无隐式依赖。
    """
    if status is None or not status.strip():
        return None
    requested = tuple(item.strip() for item in status.split(",") if item.strip())
    illegal = [item for item in requested if item not in JOB_STATUSES]
    if illegal:
        raise ValidationError(
            "status 仅支持 " + "/".join(JOB_STATUSES),
            data={"illegal": illegal, "allowed": list(JOB_STATUSES)},
        )
    return requested


def _parse_job_type(job_type: str | None) -> str | None:
    """解析并校验 E13 的 type 过滤参数（JOB_TYPES 白名单单值；缺省＝不过滤）。"""
    if job_type is None or not job_type.strip():
        return None
    candidate = job_type.strip()
    if candidate not in JOB_TYPES:
        raise ValidationError(
            f"不支持的任务类型：{candidate}", data={"type": candidate, "allowed": list(JOB_TYPES)}
        )
    return candidate
