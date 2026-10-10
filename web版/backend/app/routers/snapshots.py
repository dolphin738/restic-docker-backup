"""快照路由：E17 快照列表 + E18 文件树（§5.1 L477-484 / R-009）。

两条约定：

1. **路径语义**：快照里存的是**容器路径**（``/host/etc/nginx``），两条端点对外一律给
   ``display_path``（原路径语义 ``/etc/nginx``），转换只走 :class:`PathMapper`（§7.3 唯一实现点）；
   E18 的 ``?path=`` 入参按展示形态接收，服务端用 ``to_snapshot`` 还原为容器内绝对路径。
2. **探测失败不静默**：restic 非 0 退出或超时 → 500 ``RESTIC_FAILED``，
   ``data.reason`` 给出可操作原因（锁残留/凭据缺失/仓库未初始化…），由
   :func:`app.services.restic.failure_reason` 统一生成。

成本口径（自定，文档未定义）：E17 **不逐快照**跑 ``stats``（N 次仓库往返不可控），
故 ``size`` 恒为 ``null``；E18 单次 ``ls`` 全量收流后做子树过滤与游标分页，
行数/时间双上限由 :meth:`ResticCmd.run_json` 兜底。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import posixpath

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    SNAPSHOT_FILES_DEFAULT_LIMIT,
    SNAPSHOT_FILES_MAX_LIMIT,
    SNAPSHOT_FILES_MAX_OFFSET,
    SNAPSHOT_FILES_TIMEOUT_SECONDS,
    SNAPSHOT_LIST_TIMEOUT_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import ResticFailedError
from app.schemas.api_schemas import Page, SnapshotFileOut, SnapshotFilesResponse, SnapshotOut
from app.services.env_sync import load_credentials
from app.services.parser import FileRecord, OutputParser, SnapshotRecord
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs, failure_reason

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["snapshots"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]


@router.get(
    "/snapshots",
    response_model=Page[SnapshotOut],
    summary="E17 快照列表",
    dependencies=_PROTECTED,
)
async def list_snapshots(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(require_session),
) -> Page[SnapshotOut]:
    """``restic snapshots --json`` → 映射原路径语义 → 按时间倒序 → 分页。"""
    del session
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo

    repo_url = (await config_repo.get_value("repo.url")) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库配置页填写 repo.url）")

    argv = restic.build("snapshots", args=WhitelistArgs(json_output=True))
    env = restic.build_env(repo_url=repo_url, env_extras=await load_credentials(config_repo))
    probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_LIST_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, SNAPSHOT_LIST_TIMEOUT_SECONDS)
        logger.warning("快照列表探测失败：%s", reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    records = await asyncio.to_thread(parser.parse_snapshot_list, "\n".join(probe.lines))
    items = [_to_snapshot_out(record, mapper) for record in records if record.snapshot_id]
    items.sort(key=lambda item: item.time or "", reverse=True)  # 最新在前，restic 默认升序
    return Page(items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset)


@router.get(
    "/snapshots/{snapshot_id}/files",
    response_model=SnapshotFilesResponse,
    summary="E18 快照文件树",
    dependencies=_PROTECTED,
)
async def list_snapshot_files(
    snapshot_id: str,
    request: Request,
    path: str = Query(default="/", description="展示形态的目录路径（如 /etc）；缺省为模式根"),
    cursor: str | None = Query(default=None, description="上一页末条目的不透明游标"),
    limit: int = Query(default=SNAPSHOT_FILES_DEFAULT_LIMIT, ge=1, le=SNAPSHOT_FILES_MAX_LIMIT),
    session: SessionContext = Depends(require_session),
) -> SnapshotFilesResponse:
    """``restic ls --json <id> <子树>`` → 直接子项过滤 → 游标分页（懒加载树一次只要一层）。"""
    del session
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo

    repo_url = (await config_repo.get_value("repo.url")) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库配置页填写 repo.url）")

    container_path = mapper.to_snapshot(path or "/")
    argv = restic.build(
        "ls",
        args=WhitelistArgs(
            snapshot_id=snapshot_id,
            # 子树走 extra_positionals：它是**快照内**路径，不能过 PathMapper 的容器前缀校验
            extra_positionals=(container_path,),
            json_output=True,
        ),
    )
    env = restic.build_env(repo_url=repo_url, env_extras=await load_credentials(config_repo))
    probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_FILES_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, SNAPSHOT_FILES_TIMEOUT_SECONDS)
        logger.warning("快照文件树探测失败（snapshot=%s path=%s）：%s", snapshot_id, container_path, reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    records = await asyncio.to_thread(parser.parse_file_list, "\n".join(probe.lines))
    entries = _direct_children(records, container_path, mapper)

    start = _cursor_offset(cursor)
    window = entries[start : start + limit]
    has_more = start + limit < len(entries)
    return SnapshotFilesResponse(
        snapshot_id=snapshot_id,
        path=mapper.to_display(container_path),
        items=window,
        next_cursor=_encode_cursor(start + limit) if has_more else None,
        truncated=probe.truncated,
    )


# ---------------------------------------------------------------- 内部实现

def _to_snapshot_out(record: SnapshotRecord, mapper: PathMapper) -> SnapshotOut:
    """快照记录 → 响应模型（paths 映射为原路径语义；size 见模块 docstring 成本口径）。"""
    return SnapshotOut(
        id=record.snapshot_id or "",
        time=record.time,
        host=record.host,
        tags=list(record.tags),
        paths=[mapper.to_display(item) for item in record.paths],
        size=record.size,
    )


def _direct_children(
    records: list[FileRecord],
    subtree: str,
    mapper: PathMapper,
) -> list[SnapshotFileOut]:
    """过滤出子树的**直接子项**（父目录 == 子树），并按"目录优先、名称升序"排序。

    Args:
        records: ``ls`` 解析出的全部条目（可能递归到多层）。
        subtree: 容器内规范化的子树路径（如 ``/host/etc``）。
        mapper: 路径映射器（生成 display_path）。

    Returns:
        去重后的直接子项列表。
    """
    parent_expect = subtree.rstrip("/") or "/"
    items: list[SnapshotFileOut] = []
    seen: set[str] = set()
    for record in records:
        item_path = record.path
        if not item_path or item_path == parent_expect:
            continue  # 跳过空路径与子树自身（ls 会把子树根节点一并列出）
        if (posixpath.dirname(item_path) or "/") != parent_expect:
            continue
        if item_path in seen:
            continue
        seen.add(item_path)
        items.append(
            SnapshotFileOut(
                name=record.name or item_path.rsplit("/", 1)[-1],
                type=record.type or "file",
                path=item_path,
                display_path=mapper.to_display(item_path),
                size=record.size,
                mtime=record.mtime,
            )
        )
    items.sort(key=lambda item: (0 if item.type == "dir" else 1, item.name))
    return items


def _cursor_offset(cursor: str | None) -> int:
    """把不透明游标换算为起始下标；不可解析或越界时回落 0 / 上限值。"""
    if not cursor:
        return 0
    try:
        decoded = int(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
    except Exception:  # noqa: BLE001 - 游标损坏直接从头返回，不报错
        return 0
    return max(0, min(decoded, SNAPSHOT_FILES_MAX_OFFSET))


def _encode_cursor(offset: int) -> str:
    """把下页起始下标编码为不透明游标。"""
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("utf-8")
