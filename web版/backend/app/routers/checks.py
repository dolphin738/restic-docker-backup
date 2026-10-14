"""check 结果路由（R-017 批次 B）：E22 ``GET /api/checks/{job_id}``。

设计要点（增量设计 §2.4）：
- 校验 job 存在且 ``type == "check"``（否则 404 / 422）；
- 响应 = ``JobOut`` 全字段 + ``check_view``——数据源为 parser 对 ``check --json``
  输出的结构化提取（**summary + error 行驱动，无进度事件**，§7.1 V4 定稿）；
- **不建新表**（Q9 基线）：错误列表与 summary 指标从任务日志（restic stdout）
  现场解析；``read_data`` 取 JobRunner 内存保留的提交参数（重启后回落 False）；
- check 无进度事件（V4）：前端不建进度条，以运行状态 + 终态结果视图呈现（T06）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import LOG_TAIL_MAX_BYTES
from app.errors import NotFoundError, ValidationError
from app.schemas.api_schemas import CheckDetailResponse, CheckErrorOut, CheckView
from app.services.parser import OutputParser

router = APIRouter(prefix="/api", tags=["checks"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]


@router.get(
    "/checks/{job_id}",
    response_model=CheckDetailResponse,
    summary="E22 check 任务结果视图（R-017）",
    dependencies=_PROTECTED,
)
async def get_check_detail(
    job_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> CheckDetailResponse:
    """返回 check 任务的结构化结果：错误原文列表 + summary 指标 + 下一步动作数据源。

    非 check 类型任务 422（语义与 E13 type 白名单同口径）；任务日志缺失（如
    调度冲突的 cancelled 任务）回零值视图，不报错。
    """
    del session
    job_repo = request.app.state.job_repo
    job = await job_repo.get(job_id)
    if job is None:
        raise NotFoundError("任务不存在")
    if job.type != "check":
        raise ValidationError(
            "该任务不是 check 类型", data={"job_id": job_id, "type": job.type}
        )

    text = await _read_log_tail(Path(job.log_path))
    parser: OutputParser = request.app.state.parser
    aggregate = await asyncio.to_thread(parser.parse_check, text)
    read_data = bool(request.app.state.job_runner.check_params(job_id).get("read_data", False))

    return CheckDetailResponse(
        **job.__dict__,  # type: ignore[arg-type]
        check_view=CheckView(
            errors=[CheckErrorOut(message=message) for message in aggregate.errors],
            error_count=len(aggregate.errors),
            num_errors=aggregate.num_errors,
            broken_packs=list(aggregate.broken_packs),
            suggest_repair_index=aggregate.suggest_repair_index,
            suggest_prune=aggregate.suggest_prune,
            read_data=read_data,
        ),
    )


async def _read_log_tail(path: Path, max_bytes: int = LOG_TAIL_MAX_BYTES) -> str:
    """读取任务日志尾部（上限与 E27 同源 ``LOG_TAIL_MAX_BYTES``），缺失返回空串。"""
    if not path.is_file():
        return ""

    def _read() -> str:
        size = path.stat().st_size
        start = max(0, size - max_bytes)
        with path.open("rb") as handle:
            handle.seek(start)
            return handle.read(max_bytes).decode("utf-8", errors="replace")

    return await asyncio.to_thread(_read)
