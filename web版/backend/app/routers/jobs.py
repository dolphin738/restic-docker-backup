"""任务路由：E12 触发 / E13 列表（P1 最小版）/ E14 详情 / E15 SSE 流 / E16 取消 / E27 日志尾部。

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
    ACTIVE_JOB_STATUSES,
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
from app.schemas.enums import JobStatus

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

    ``params.excludes`` 为用户追加的排除规则（展示形态或纯 glob 模式），在**提交前**
    经 :func:`normalize_user_excludes` 三分类归一为容器形态——放在路由层而非 JobRunner，
    越界锚定路径才能以 422 ``VALIDATION_ERROR`` 同步回给前端，而不是把任务打进 failed。
    """
    del session
    runner = request.app.state.job_runner
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
        trigger="manual",
        snapshot_id=payload.snapshot_id,
        params=params,
    )
    return JobCreateResponse(id=job.id, status=JobStatus(job.status), type=payload.type)


@router.get("/jobs", response_model=Page[JobOut], summary="E13 任务列表（P1 最小版）", dependencies=_PROTECTED)
async def list_jobs(
    request: Request,
    status: str | None = Query(default=None, description="仅支持 running/pending（逗号分隔）"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(require_session),
) -> Page[JobOut]:
    """P1 最小版：只提供"进行中"列表（running/pending），P2 再开放全量分页/筛选/retention。"""
    del session
    statuses = _parse_active_statuses(status)
    repo = request.app.state.job_repo
    jobs, total = await repo.list_page(statuses=statuses, limit=limit, offset=offset)
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


def _parse_active_statuses(status: str | None) -> tuple[str, ...]:
    """解析并校验 E13 的 status 过滤参数（P1 仅允许 running/pending）。"""
    if status is None or not status.strip():
        return ACTIVE_JOB_STATUSES
    requested = tuple(item.strip() for item in status.split(",") if item.strip())
    illegal = [item for item in requested if item not in ACTIVE_JOB_STATUSES]
    if illegal:
        raise ValidationError(
            "P1 最小版仅支持 status=running/pending 的任务列表",
            data={"illegal": illegal, "allowed": list(ACTIVE_JOB_STATUSES)},
        )
    return requested
