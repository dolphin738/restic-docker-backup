"""备份源路由：E05 目录浏览 + E31 首备 dry-run 预览（PRD 页 5 / US-B2 / R-006 确认门）。

所有路径先经 :class:`PathMapper` 白名单校验（方案 §6 注入防护），越界返回 400 ``PATH_OUT_OF_PREFIX``。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    BROWSE_ALLOWED_PREFIXES,
    DRY_RUN_MAX_ITEMS,
    DRY_RUN_TIMEOUT_SECONDS,
    PSEUDO_FS_NAMES,
    SOURCE_BROWSE_DEFAULT_LIMIT,
    SOURCE_BROWSE_MAX_LIMIT,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import ForbiddenError, NotFoundError
from app.exclusions import ExclusionRules
from app.schemas.api_schemas import (
    DryRunItem,
    DryRunRequest,
    DryRunResponse,
    DryRunWarning,
    SourceBrowseResponse,
    SourceItem,
)
from app.services.parser import FileRecord, OutputParser
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs
from app.utils.ulid import new_ulid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sources"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]


@router.get(
    "/sources",
    response_model=SourceBrowseResponse,
    summary="E05 备份源目录浏览",
    dependencies=_PROTECTED,
)
async def browse_sources(
    request: Request,
    path: str | None = Query(default=None, description="容器内路径；缺省为当前模式根前缀"),
    cursor: str | None = Query(default=None, description="上一页末条目的不透明游标"),
    limit: int = Query(default=SOURCE_BROWSE_DEFAULT_LIMIT, ge=1, le=SOURCE_BROWSE_MAX_LIMIT),
    session: SessionContext = Depends(require_session),
) -> SourceBrowseResponse:
    """浏览可备份目录：返回条目名/类型与**剥掉前缀**的展示路径。

    错误：400 PATH_OUT_OF_PREFIX（越界）/ 404（不存在）/ 403（无读权限）。
    """
    del session
    settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    container_path = mapper.validate(path or settings.mode_root, allowed_prefixes=BROWSE_ALLOWED_PREFIXES)

    entries = await asyncio.to_thread(_scan_dir, container_path)
    start_index = _cursor_index(entries, cursor)

    window = entries[start_index : start_index + limit]
    items = [
        SourceItem(
            name=name,
            type=kind,
            display_path=mapper.to_display(f"{container_path.rstrip('/')}/{name}"),
        )
        for name, kind in window
    ]
    has_more = start_index + limit < len(entries)
    next_cursor = _encode_cursor(window[-1][0]) if (has_more and window) else None
    return SourceBrowseResponse(path=mapper.to_display(container_path), items=items, next_cursor=next_cursor)


@router.post(
    "/sources/dry-run",
    response_model=DryRunResponse,
    summary="E31 首备扫描范围预览",
    dependencies=_PROTECTED,
)
async def dry_run(
    payload: DryRunRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> DryRunResponse:
    """``restic backup --dry-run --json --verbose`` 流式预览扫描范围。

    命中 proc/sys/dev/run/docker/lost+found 类目录名时置黄色 ``warnings``（**不拦截**，
    方案 §3.2 四层防线第三层）；预览有超时与条目上限，超出置 ``truncated``。
    """
    del session
    settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    config_repo: ConfigRepo = request.app.state.config_repo

    sources = tuple(mapper.validate(item) for item in payload.sources)
    excludes = tuple(_dedupe(ExclusionRules(settings).built_in() + list(payload.excludes)))
    args = WhitelistArgs(
        targets=sources,
        excludes=excludes,
        json_output=True,
        dry_run=True,
        verbose=True,
        one_file_system=settings.restic_web_mode == "sources",
    )

    repo_url = (await config_repo.get_value("repo.url")) or ""
    env_extras = await _load_credentials(config_repo)
    argv = restic.build("backup", args=args, env_extras=env_extras)
    env = restic.build_env(repo_url=repo_url, env_extras=env_extras)

    log_path = settings.jobs_dir / f"dryrun-{new_ulid()}.log"
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
        logger.warning("备份源预览超时：%s", sources)
        proc.kill()
        with _suppress():
            await proc.wait()
    finally:
        log_path.unlink(missing_ok=True)

    if error is None and not truncated and exit_code != 0:
        error = f"restic 退出码 {exit_code}：仓库可能未初始化或凭据缺失"

    for source in payload.sources:
        warning = _pseudo_warning(source)
        if warning is not None:
            warnings.append(warning)

    return DryRunResponse(items=items, warnings=_dedupe_warnings(warnings), truncated=truncated, error=error)


# ---------------------------------------------------------------- 内部实现

def _scan_dir(path: str) -> list[tuple[str, str]]:
    """同步扫描单层目录，返回按名称排序的 ``(name, type)`` 列表。

    Args:
        path: 已通过白名单校验的容器路径。

    Returns:
        排序后的条目列表，type 取 ``dir`` / ``file`` / ``other``。

    Raises:
        NotFoundError: 路径不存在。
        ValidationError: 路径不是目录。
        ForbiddenError: 无读取权限（非 root 档常见，R-012）。
    """
    target = Path(path)
    if not target.exists():
        raise NotFoundError(f"路径不存在：{path}")
    if not target.is_dir():
        from app.errors import ValidationError

        raise ValidationError(f"路径不是目录：{path}")
    try:
        names = sorted(os.listdir(target))
    except PermissionError as exc:
        raise ForbiddenError(f"无权限读取目录：{path}（非 root 档常见，如需全量备份请切 root 档）") from exc

    entries: list[tuple[str, str]] = []
    for name in names:
        child = target / name
        if child.is_symlink():
            kind = "other"
        elif child.is_dir():
            kind = "dir"
        elif child.is_file():
            kind = "file"
        else:
            kind = "other"
        entries.append((name, kind))
    return entries


def _cursor_index(entries: list[tuple[str, str]], cursor: str | None) -> int:
    """把游标换算为起始下标；游标不可解析时从头开始。"""
    if not cursor:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode("utf-8")
    except Exception:  # noqa: BLE001 - 游标损坏直接从头返回，不报错
        return 0
    for index, (name, _) in enumerate(entries):
        if name == decoded:
            return index + 1
    return 0


def _encode_cursor(name: str) -> str:
    """把末条目名编码为不透明游标。"""
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("utf-8")


def _pseudo_warning(path: str) -> DryRunWarning | None:
    """命中伪文件系统目录名时生成黄色警告（不拦截）。"""
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


async def _load_credentials(config_repo: ConfigRepo) -> dict[str, str]:
    """把 config 中 ``cred.*`` 键还原为子进程环境变量。"""
    rows = await config_repo.list_all()
    extras: dict[str, str] = {}
    for row in rows:
        if not row.value:
            continue
        if row.key.startswith("cred.aws_"):
            extras["AWS_" + row.key[len("cred.aws_"):].upper()] = row.value
        elif row.key.startswith("cred.b2_"):
            extras["B2_" + row.key[len("cred.b2_"):].upper()] = row.value
    return extras


class _suppress:
    """极简上下文管理器：吞掉块内异常（用于收尾阶段的 best-effort 调用）。"""

    def __enter__(self) -> "_suppress":
        """进入上下文。"""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        """退出上下文并吞掉异常。"""
        return True
