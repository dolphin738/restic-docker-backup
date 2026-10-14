"""恢复路由：E19 ``POST /api/restore`` + E20 ``POST /api/restore/preview``（§5.1 / §6.3 / R-008、R-015，D4）。

两条恢复路径（红线 S7 v1.3 的两类合法调用点）：

1. **时间戳子目录**（P1 默认，零覆盖语义）：请求体只有 ``snapshot_id`` / ``target``
   / ``paths``；时间戳子目录由**服务端**计算：``<target>/<源目录名>-<YYYYmmdd-HHMM>``
   （例：``/restore`` + 快照含 ``/etc/nginx`` → ``/restore/etc-20261010-0138``），
   物理上不可能覆盖既有文件（PRD G3 误操作零事故）。``target`` 必须在
   :data:`RESTORE_ALLOWED_PREFIXES` 白名单内，否则 403 ``RESTORE_FORBIDDEN``；
   该路径的 ``--overwrite=if-changed`` 由 JobRunner 固定注入、界面无入口——
   请求体出现 ``overwrite_mode`` / ``keyword`` 即 **422**。
2. **原位恢复**（R-015）：请求体带 ``overwrite_mode`` / ``keyword``，目标经
   **双重锁**（rw 档 ``host_root`` 白名单 + 快照 ``paths`` 交集收紧）与三重确认门
   后端校验（``overwrite_mode == "always"``、``keyword == snapshot_id[:8]``），
   执行 ``--overwrite=always`` 并写审计 ``restore.inplace``。

两者均：同仓库已有 running/pending 任务 → 409 ``JOB_CONFLICT``（JobRunner 拒绝不排队）。
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_RESTORE_INPLACE,
    AUDIT_RESTORE_PREVIEW,
    AUDIT_RESTORE_RUN,
    INPLACE_RESTORE_ALLOWED_PREFIXES,
    INPLACE_RESTORE_TARGET_ROOT,
    RESTORE_ALLOWED_PREFIXES,
    SNAPSHOT_FILES_MAX_OFFSET,
    SNAPSHOT_LIST_TIMEOUT_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import PathOutOfPrefixError, RestoreForbiddenError, ValidationError
from app.schemas.api_schemas import (
    RestorePreviewItem,
    RestorePreviewRequest,
    RestorePreviewResponse,
    RestoreRequest,
    RestoreResponse,
)
from app.services.compare import CompareService, OverwriteItem, in_snapshot_paths
from app.services.env_sync import load_credentials
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser, SnapshotRecord
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs
from app.settings import Settings
from app.utils.cursor import decode_cursor, encode_cursor
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

    B1 文件级恢复（R-027）：``paths`` 为**展示形态**的文件/目录清单，逐条
    ``to_snapshot`` + :meth:`PathMapper.validate`（模式根白名单）归一为容器形态，
    越界 400 ``PATH_OUT_OF_PREFIX``（data 带 path）——S1 校验点①；归一后经
    ``params.include`` 入 JobRunner，由 ``ResticCmd`` 白名单拼 ``--include``
    （校验点②，纵深防御）。缺省/空 paths = 整快照恢复（兼容既有行为）。

    带 ``overwrite_mode`` / ``keyword`` 的请求转**原位恢复分支**（见
    :func:`_restore_inplace`）；不带二者的请求仍走本函数体内的时间戳分支，
    行为与 R-015 之前一致（零覆盖回归）。

    Raises:
        RestoreForbiddenError: ``target`` 不在恢复白名单（403 RESTORE_FORBIDDEN）。
        PathOutOfPrefixError: ``paths`` 中某条越出模式根白名单（400 PATH_OUT_OF_PREFIX）。
        JobConflictError: 同仓库已有 running/pending 任务（409 JOB_CONFLICT）。
    """
    del session
    settings: Settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    runner: JobRunner = request.app.state.job_runner

    # 覆盖语义请求 → 原位恢复分支（时间戳路径出现覆盖参数由该分支判 422）
    if payload.overwrite_mode is not None or payload.keyword is not None:
        return await _restore_inplace(payload, request)

    try:
        target_root = mapper.validate(payload.target, allowed_prefixes=RESTORE_ALLOWED_PREFIXES)
    except PathOutOfPrefixError as exc:
        allowed = ", ".join(RESTORE_ALLOWED_PREFIXES)
        raise RestoreForbiddenError(
            f"恢复目标不在允许的白名单内（{allowed}）", data={"target": payload.target}
        ) from exc

    include = _normalize_include_paths(payload.paths, mapper, settings)

    stamp = format_local(now_utc(), settings.tz, "%Y%m%d-%H%M")
    stem = await _resolve_stem(request, payload.snapshot_id)
    restore_target = posixpath.join(target_root.rstrip("/"), f"{stem}-{stamp}")

    params: dict[str, Any] = {"target": restore_target}
    if include:
        params["include"] = include
    job = await runner.submit(
        "restore",
        trigger="manual",
        snapshot_id=payload.snapshot_id,
        params=params,
    )
    await request.app.state.audit.record(
        AUDIT_RESTORE_RUN,
        target=restore_target,
        detail={
            "snapshot_id": payload.snapshot_id,
            "job_id": job.id,
            "requested_target": payload.target,
            # B1：只加 detail 字段，不新增动作名（S6）
            "include_count": len(include),
        },
        result="ok",
    )
    logger.info(
        "恢复任务已提交：job=%s snapshot=%s target=%s include=%d",
        job.id,
        payload.snapshot_id,
        restore_target,
        len(include),
    )
    return RestoreResponse(job_id=job.id, restore_target=restore_target)


@router.post(
    "/restore/preview",
    response_model=RestorePreviewResponse,
    summary="E20 原位恢复覆盖预览",
    dependencies=_PROTECTED,
)
async def preview_inplace_restore(
    payload: RestorePreviewRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RestorePreviewResponse:
    """比对快照内容与目标磁盘现状，返回「将被覆盖文件清单」（只读，游标分页）。

    双重目标锁（R-015 Q1 裁决）：① 目标落在 :data:`INPLACE_RESTORE_ALLOWED_PREFIXES`
    （rw 档 ``host_root``）；② 目标必须是该快照 ``paths`` 命中的目录或其子目录；
    ro 档 → 403 ``RESTORE_FORBIDDEN``、完全越界 → 400 ``PATH_OUT_OF_PREFIX``
    （前端据此置灰「原位置」）。

    预览比对的磁盘落点是**真实原位文件**（``/host/etc/nginx/...``）——与执行层
    ``--target /`` + 快照内绝对路径的落点一致，故不随执行层参数变化。

    预览是敏感留痕动作：写审计 ``restore.preview``（只读，**不属** S6 危险操作清单，
    援 ``forget.preview`` 先例）。清单只承诺**预览时点**的磁盘现状（Q4 TOCTOU 口径）。

    Raises:
        PathOutOfPrefixError: 目标完全越界（400）。
        RestoreForbiddenError: ro 档 / 非快照 paths（403）。
        ResticFailedError: 快照元数据或文件清单探测失败（500，原因明示）。
    """
    del session
    mapper: PathMapper = request.app.state.path_mapper
    compare: CompareService = request.app.state.compare_service

    container_target, roots = await _enforce_inplace_lock(request, payload.snapshot_id, payload.target)
    scan = compare.diff(payload.snapshot_id, container_target, roots=roots)
    items: list[OverwriteItem] = [item async for item in scan]

    start = decode_cursor(payload.cursor, SNAPSHOT_FILES_MAX_OFFSET)
    window = items[start : start + payload.limit]
    has_more = start + payload.limit < len(items)

    await request.app.state.audit.record(
        AUDIT_RESTORE_PREVIEW,
        target=container_target,
        detail={
            "snapshot_id": payload.snapshot_id,
            "target": container_target,
            "overwrite_count": len(items),
        },
        result="ok",
    )
    logger.info(
        "覆盖预览完成：snapshot=%s target=%s overwrite_count=%d truncated=%s",
        payload.snapshot_id,
        container_target,
        len(items),
        scan.truncated,
    )
    return RestorePreviewResponse(
        overwrite_count=len(items),
        items=[_to_preview_item(item, mapper) for item in window],
        total_bytes=sum(int(item.snapshot_size or 0) for item in items),
        next_cursor=encode_cursor(start + payload.limit) if has_more else None,
        truncated=scan.truncated,
    )


# ---------------------------------------------------------------- 内部实现

async def _restore_inplace(payload: RestoreRequest, request: Request) -> RestoreResponse:
    """原位恢复分支（R-015 US-P2-14）：双重锁 + 三重确认门后端校验 + 审计。

    执行层落点（S7 v1.3「执行层落点纪律」）：restic 的落点是 ``--target`` +
    快照内绝对路径的**拼接**，快照存的是容器绝对路径（``/host/etc/...``），故
    ``params.target`` 固定 :data:`INPLACE_RESTORE_TARGET_ROOT`（``/``）——传
    ``<目标目录>`` 会嵌套成 ``/host/etc/host/etc/...``，一个目标文件都不会被覆盖。
    用户可控范围改由**强制非空**的 ``--include`` 限定（``container_target`` 或其
    与 ``paths`` 的交集）。响应与审计的 ``target`` 仍回用户语义的原位目录。

    Raises:
        PathOutOfPrefixError: 目标完全越界（400 PATH_OUT_OF_PREFIX）。
        RestoreForbiddenError: ro 档 / 非快照 paths（403 RESTORE_FORBIDDEN）。
        ValidationError: 时间戳子目录路径带覆盖参数；或原位路径的 ``overwrite_mode``
            非 ``always`` / ``keyword`` 不等于快照短 id / ``paths`` 与目标无交集（422）。
    """
    settings: Settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    runner: JobRunner = request.app.state.job_runner
    compare: CompareService = request.app.state.compare_service

    container_target, roots = await _enforce_inplace_lock(request, payload.snapshot_id, payload.target)

    # 三重确认门的后端校验（防前端自报绕过）：模式固定 always + keyword = 快照短 id
    if payload.overwrite_mode != "always":
        raise ValidationError(
            "原位恢复的 overwrite_mode 必须为 always（界面固定传入，无模式选择项）",
            data={"overwrite_mode": payload.overwrite_mode},
        )
    if payload.keyword != payload.snapshot_id[:8]:
        raise ValidationError(
            "原位恢复确认关键字不正确（须为快照短 id 前 8 位）",
            data={"keyword": payload.keyword, "expected": payload.snapshot_id[:8]},
        )

    include = _resolve_inplace_include(payload.paths, mapper, settings, container_target)
    overwrite_count = await _current_overwrite_count(
        compare, payload.snapshot_id, container_target, roots
    )
    params: dict[str, Any] = {
        # 落点固定 "/"：restic 落点 = --target + 快照内绝对路径（S7 v1.3）
        "target": INPLACE_RESTORE_TARGET_ROOT,
        "inplace": True,
        "overwrite": "always",
        # 强制非空：--target / 只靠 include 限定写入范围（纵深防御）
        "include": include,
    }
    job = await runner.submit(
        "restore",
        trigger="manual",
        snapshot_id=payload.snapshot_id,
        params=params,
    )
    # S6 只增不改：detail 记执行时的预览快照值，执行后的实际计数不回填 detail
    await request.app.state.audit.record(
        AUDIT_RESTORE_INPLACE,
        target=container_target,
        detail={
            "snapshot_id": payload.snapshot_id,
            "target": container_target,
            "overwrite_count": overwrite_count,
            "keyword_confirmed": True,
        },
        result="ok",
    )
    logger.info(
        "原位恢复任务已提交：job=%s snapshot=%s target=%s overwrite_count=%s",
        job.id,
        payload.snapshot_id,
        container_target,
        overwrite_count,
    )
    return RestoreResponse(job_id=job.id, restore_target=container_target)


async def _enforce_inplace_lock(
    request: Request, snapshot_id: str, target: str
) -> tuple[str, list[str]]:
    """原位恢复的**双重目标锁**（R-015 Q1 裁决）：返回（容器形态目标, 快照备份根）。

    ① 前缀白名单：:data:`INPLACE_RESTORE_ALLOWED_PREFIXES`；
    ② rw 档判定：``settings.host_mount_mode == "rw"``（ro 档 403，前端置灰）；
    ③ 快照 paths 交集收紧：目标须为该快照 ``paths`` 命中的目录或其子目录。

    Args:
        request: 当前请求（取 settings/path_mapper/compare_service）。
        snapshot_id: 快照 id。
        target: 请求体里的目标路径（容器形态）。

    Returns:
        ``(规范化后的容器形态目标, 该快照的备份根清单)``。

    Raises:
        ValidationError: 目标落在时间戳子目录白名单（``/restore``）却带覆盖语义（422）。
        PathOutOfPrefixError: 目标完全越界（既不在 ``/restore`` 也不在 rw 档
            ``host_root``）——400 ``PATH_OUT_OF_PREFIX``（PRD US-P2-13 AC2 口径）。
        RestoreForbiddenError: ro 档 / 目标不在该快照的备份路径内（403）。
        ResticFailedError: 快照元数据探测失败（500，原因明示）。
    """
    settings: Settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    compare: CompareService = request.app.state.compare_service

    try:
        container_target = mapper.validate(target, allowed_prefixes=INPLACE_RESTORE_ALLOWED_PREFIXES)
    except PathOutOfPrefixError as exc:
        if _within(mapper, target, RESTORE_ALLOWED_PREFIXES):
            raise ValidationError(
                "时间戳子目录路径不接受 overwrite_mode/keyword"
                "（该路径的 --overwrite=if-changed 由服务端固定注入，界面无入口）",
                data={"target": target},
            ) from exc
        # 完全越界（既不在 /restore 也不在 rw 档 host_root）→ 400（PRD US-P2-13 AC2 口径）
        allowed = ", ".join(INPLACE_RESTORE_ALLOWED_PREFIXES)
        raise PathOutOfPrefixError(
            f"原位恢复目标不在允许的白名单内（{allowed}）", data={"target": target}
        ) from exc

    if settings.host_mount_mode != "rw":
        raise RestoreForbiddenError(
            "当前 /host 为只读挂载（ro 档），原位恢复不可用（请将挂载切换为 rw）",
            data={"target": target, "host_mount_mode": settings.host_mount_mode},
        )

    roots = await compare.snapshot_roots(snapshot_id)
    if not in_snapshot_paths(roots, container_target):
        raise RestoreForbiddenError(
            "原位恢复目标不在该快照的备份路径范围内",
            data={"target": target, "snapshot_paths": roots},
        )
    return container_target, roots


def _resolve_inplace_include(
    paths: list[str], mapper: PathMapper, settings: Settings, container_target: str
) -> list[str]:
    """原位恢复的 ``--include``：**恒非空**（``--target /`` 只靠 include 限定范围）。

    Args:
        paths: 请求体 ``paths``（展示形态）；空 = 整棵目标子树。
        mapper: 路径映射器。
        settings: 运行期配置（取当前模式根）。
        container_target: 双重锁通过的容器形态原位目标。

    Returns:
        容器形态的 include 清单（至少一条）。

    Raises:
        PathOutOfPrefixError: ``paths`` 中某条越出模式根（400，data 带 path）。
        ValidationError: ``paths`` 与 ``container_target`` 无交集（422）。
    """
    if not paths:
        return [container_target]
    include = _normalize_include_paths(paths, mapper, settings)
    kept = [item for item in include if _path_intersects(item, container_target)]
    if not kept:
        raise ValidationError(
            "原位恢复的恢复路径与目标目录无交集（恢复范围须落在目标子树内）",
            data={"paths": include, "target": container_target},
        )
    return kept


def _path_intersects(left: str, right: str) -> bool:
    """判断两条路径是否**互为**祖先（交集非空：restic --include 支持目录含子树）。"""
    return in_snapshot_paths([left], right) or in_snapshot_paths([right], left)


def _within(mapper: PathMapper, path: str, prefixes: tuple[str, ...]) -> bool:
    """判断路径是否落在给定前缀白名单内（越界/非法路径均返回 False）。"""
    try:
        mapper.validate(path, allowed_prefixes=prefixes)
    except PathOutOfPrefixError:
        return False
    return True


async def _current_overwrite_count(
    compare: CompareService, snapshot_id: str, target: str, roots: list[str]
) -> int | None:
    """执行前重算一次覆盖计数（审计 detail 的「预览值」；失败不阻断执行）。

    计数取自 CompareService（与 E20 同源、不可由前端伪造——E19 请求体不接受前端
    传入的计数）。

    成本提示（勿当免费调用）：本调用**阻塞**提交请求，量级与一次 E20 预览相当
    （一次 ``ls --json`` + 全量 ``os.stat`` 比对）；大快照下会拉长 E19 响应耗时。
    此处保持服务端重算是**安全优先**的取舍：换成前端上报即等于开放伪造入口。
    比对失败时返回 ``None``（审计 detail 留空），**不阻断**已过三重确认门的恢复执行。
    """
    try:
        scan = compare.diff(snapshot_id, target, roots=roots)
        return len([item async for item in scan])
    except Exception as exc:  # noqa: BLE001 - 计数失败不得阻断恢复主流程
        logger.warning("原位恢复覆盖计数失败，审计 detail 置 null：%s", exc)
        return None


def _to_preview_item(item: OverwriteItem, mapper: PathMapper) -> RestorePreviewItem:
    """覆盖项 → 响应条目（``display_path`` 为原路径语义，S1/A2）。"""
    return RestorePreviewItem(
        path=item.path,
        display_path=mapper.to_display(item.path),
        snapshot_mtime=item.snapshot_mtime,
        disk_mtime=item.disk_mtime,
        snapshot_size=item.snapshot_size,
        disk_size=item.disk_size,
    )


def _normalize_include_paths(
    paths: list[str], mapper: PathMapper, settings: Settings
) -> list[str]:
    """B1 恢复路径归一（S1 校验点①）：展示形态 → 容器形态 + 模式根白名单校验。

    Args:
        paths: 请求体 ``paths``（展示形态，如 ``/etc``、``/home/u/x``）。
        mapper: 路径映射器。
        settings: 运行期配置（取当前模式根）。

    Returns:
        去重保序的容器形态路径清单（供 ``params.include`` 入 JobRunner）。

    Raises:
        PathOutOfPrefixError: 某条路径越出当前模式根（400，data 带 path）。
    """
    include: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        item = str(raw).strip()
        if not item:
            continue
        try:
            container = mapper.validate(
                mapper.to_snapshot(item), allowed_prefixes=(settings.mode_root,)
            )
        except PathOutOfPrefixError as exc:
            raise PathOutOfPrefixError(
                f"恢复路径 {item!r} 不在当前模式根范围内", data={"path": item}
            ) from exc
        if container not in seen:
            seen.add(container)
            include.append(container)
    return include

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
