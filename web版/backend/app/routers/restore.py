"""恢复路由：E19 ``POST /api/restore``（§5.1 L486-493 / §6.3 / R-008，D4）。

P1 唯一恢复路径 = **恢复到时间戳子目录**：

- 请求体**只有** ``snapshot_id`` 与 ``target``，不存在任何覆盖语义参数；
- 时间戳子目录由**服务端**计算：``<target>/<源目录名>-<YYYYmmdd-HHMM>``
  （例：``/restore`` + 快照含 ``/etc/nginx`` → ``/restore/etc-20261010-0138``），
  物理上不可能覆盖既有文件（PRD G3 误操作零事故）；
- ``target`` 必须在 :data:`RESTORE_ALLOWED_PREFIXES` 白名单内，否则 403 ``RESTORE_FORBIDDEN``；
- 同仓库已有 running/pending 任务 → 409 ``JOB_CONFLICT``（JobRunner 拒绝不排队）。

``--overwrite=if-changed`` 由 JobRunner 固定注入，界面无入口（P2 覆盖比对层才开放）。
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import re
import time

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import AUDIT_RESTORE_RUN, RESTORE_ALLOWED_PREFIXES, SNAPSHOT_LIST_TIMEOUT_SECONDS
from app.db.repositories.config_repo import ConfigRepo
from app.errors import PathOutOfPrefixError, RestoreForbiddenError
from app.schemas.api_schemas import RestoreRequest, RestoreResponse
from app.services.env_sync import load_credentials
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser, SnapshotRecord
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs
from app.settings import Settings
from app.utils.time import format_local, now_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["restore"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# 子目录名字典：源目录名只保留安全字符，避免把任意路径片段拼进文件系统名
_STEM_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_STEM_MAX_LENGTH = 32


@router.post(
    "/restore",
    status_code=202,
    response_model=RestoreResponse,
    summary="E19 恢复到时间戳子目录",
    dependencies=_PROTECTED,
)
async def restore_snapshot(
    payload: RestoreRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RestoreResponse:
    """提交恢复任务并返回服务端计算的时间戳子目录（202）。

    Raises:
        RestoreForbiddenError: ``target`` 不在恢复白名单（403 RESTORE_FORBIDDEN）。
        JobConflictError: 同仓库已有 running/pending 任务（409 JOB_CONFLICT）。
    """
    del session
    settings: Settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    runner: JobRunner = request.app.state.job_runner

    try:
        target_root = mapper.validate(payload.target, allowed_prefixes=RESTORE_ALLOWED_PREFIXES)
    except PathOutOfPrefixError as exc:
        allowed = ", ".join(RESTORE_ALLOWED_PREFIXES)
        raise RestoreForbiddenError(
            f"恢复目标不在允许的白名单内（{allowed}）", data={"target": payload.target}
        ) from exc

    stamp = format_local(now_utc(), settings.tz, "%Y%m%d-%H%M")
    stem = await _resolve_stem(request, payload.snapshot_id)
    restore_target = posixpath.join(target_root.rstrip("/"), f"{stem}-{stamp}")

    job = await runner.submit(
        "restore",
        trigger="manual",
        snapshot_id=payload.snapshot_id,
        params={"target": restore_target},
    )
    await request.app.state.audit.record(
        AUDIT_RESTORE_RUN,
        target=restore_target,
        detail={"snapshot_id": payload.snapshot_id, "job_id": job.id, "requested_target": payload.target},
        result="ok",
    )
    logger.info("恢复任务已提交：job=%s snapshot=%s target=%s", job.id, payload.snapshot_id, restore_target)
    return RestoreResponse(job_id=job.id, restore_target=restore_target)


# ---------------------------------------------------------------- 内部实现

async def _resolve_stem(request: Request, snapshot_id: str) -> str:
    """解析时间戳子目录的"源目录名"（best-effort，失败回退快照 id 前 8 位）。

    目录名取自快照首个备份路径的**顶层目录名**（快照存容器路径 ``/host/etc/nginx`` →
    ``/etc/nginx`` → ``etc``，与 §6.3 示例 ``/restore/etc-20261010-0138`` 一致）。
    解析失败**不阻断恢复**：仅回退为 ``<snapshot_id[:8]>`` 并记 WARNING。

    Args:
        request: 当前请求（取 restic/parser/path_mapper/config_repo）。
        snapshot_id: 快照 id（可为 restic 接受的前缀）。

    Returns:
        已清洗为安全文件名字符的目录名（长度 ≤ 32）。
    """
    fallback = snapshot_id[:8] or "restore"
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo

    try:
        repo_url = (await config_repo.get_value("repo.url")) or ""
        if not repo_url:
            return fallback
        argv = restic.build("snapshots", args=WhitelistArgs(json_output=True))
        env = restic.build_env(repo_url=repo_url, env_extras=await load_credentials(config_repo))
        started = time.monotonic()
        probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_LIST_TIMEOUT_SECONDS)
        if probe.exit_code != 0:
            logger.warning(
                "恢复子目录命名：快照查询失败（exit=%s），回退为快照 id 前缀", probe.exit_code
            )
            return fallback
        logger.debug("恢复子目录命名：快照查询耗时 %.0fms", (time.monotonic() - started) * 1000)
        records = await _parse_records(parser, probe.lines)
        display_path = _first_display_path(records, snapshot_id, mapper)
        if not display_path:
            return fallback
        return _clean_stem(display_path) or fallback
    except Exception as exc:  # noqa: BLE001 - 命名失败不应阻断恢复主流程
        logger.warning("恢复子目录命名失败，回退为快照 id 前缀：%s", exc)
        return fallback


async def _parse_records(parser: OutputParser, lines: list[str]) -> list[SnapshotRecord]:
    """在线程池里解析 ``snapshots --json`` 输出（解析是 CPU 密集的纯函数）。"""
    return await asyncio.to_thread(parser.parse_snapshot_list, "\n".join(lines))


def _first_display_path(
    records: list[SnapshotRecord], snapshot_id: str, mapper: PathMapper
) -> str | None:
    """取出匹配快照的首个备份路径（展示形态）。

    Args:
        records: 解析出的快照记录。
        snapshot_id: 请求快照 id（restic 允许前缀，故用 startswith 匹配）。
        mapper: 路径映射器。

    Returns:
        展示形态的首个备份路径；无匹配或无路径时返回 None。
    """
    for record in records:
        if not (record.snapshot_id or "").startswith(snapshot_id):
            continue
        if not record.paths:
            return None
        return mapper.to_display(str(record.paths[0]))
    return None


def _clean_stem(display_path: str) -> str:
    """把展示路径的顶层目录名清洗为安全的文件名片段。"""
    parts = [part for part in display_path.split("/") if part]
    if not parts:
        return ""
    return _STEM_SAFE.sub("_", parts[0]).strip("._-")[:_STEM_MAX_LENGTH]
