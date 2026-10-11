"""备份源路由：E05 目录浏览 + E31 首备 dry-run 预览 + E32 内置排除集只读暴露。

- E05/E31（PRD 页 5 / US-B2 / R-006 确认门）：所有路径先经 :class:`PathMapper` 白名单校验
  （方案 §6 注入防护），越界返回 400 ``PATH_OUT_OF_PREFIX``。
  **入参语义修订（团队裁决，文档 §5.1 E05 待同步）**：``path`` / ``sources`` 收**展示路径**
  （``/etc/nginx``），服务端经 :meth:`PathMapper.to_snapshot` 还原为容器路径后再校验——
  响应侧只回 ``display_path``，若入参也收容器路径，前端唯一能拿到容器路径的方式就是自己拼
  ``/host``，与"前端不存不拼 /host"硬约束冲突。**兼容旧形态**：已带当前模式根前缀
  （``/host`` / ``/sources``）的入参原样接受，文档示例字面不失效；
- E32（PRD 备份源页"内置排除集（强制生效）"只读展示）：结果直接取 :class:`ExclusionRules`，
  **前端不得自行复制一份规则**（D8 单一事实来源），故本端点与主理人裁决一致提前到 P1；
  ``items``/``core`` 为容器前缀形态（wire 兼容），``display_items``/``display_core`` 为
  剥前缀后的展示形态（前端优先用后者）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    BROWSE_ALLOWED_PREFIXES,
    DRY_RUN_MAX_ITEMS,
    DRY_RUN_TIMEOUT_SECONDS,
    PSEUDO_FS_NAMES,
    SOURCE_ALLOWED_PREFIXES,
    SOURCE_BROWSE_DEFAULT_LIMIT,
    SOURCE_BROWSE_MAX_LIMIT,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import PathOutOfPrefixError
from app.exclusions import ExclusionRules, normalize_user_excludes
from app.schemas.api_schemas import (
    DryRunItem,
    DryRunRequest,
    DryRunResponse,
    DryRunWarning,
    ExclusionsResponse,
    SourceBrowseResponse,
    SourceItem,
)
from app.schemas.enums import RunMode
from app.services.dir_scan import cursor_index, encode_cursor, scan_dir_async
from app.services.env_sync import load_credentials
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
    path: str | None = Query(
        default=None,
        description="展示路径（如 /etc/nginx）；缺省为模式根；兼容旧示例的 /host 容器路径形态",
    ),
    cursor: str | None = Query(default=None, description="上一页末条目的不透明游标"),
    limit: int = Query(default=SOURCE_BROWSE_DEFAULT_LIMIT, ge=1, le=SOURCE_BROWSE_MAX_LIMIT),
    session: SessionContext = Depends(require_session),
) -> SourceBrowseResponse:
    """浏览可备份目录：返回条目名/类型与**剥掉前缀**的展示路径。

    入参 ``path`` 收**展示路径**（服务端 ``to_snapshot`` 还原，见模块 docstring 语义修订说明）；
    缺省为当前模式根。

    错误：400 PATH_OUT_OF_PREFIX（越界）/ 404（不存在）/ 403（无读权限）。
    """
    del session
    settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    container_path = (
        _to_container_path(mapper, path, allowed_prefixes=BROWSE_ALLOWED_PREFIXES)
        if path
        else mapper.validate(settings.mode_root, allowed_prefixes=BROWSE_ALLOWED_PREFIXES)
    )

    entries = await scan_dir_async(container_path)
    start_index = cursor_index(entries, cursor)

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
    next_cursor = encode_cursor(window[-1][0]) if (has_more and window) else None
    return SourceBrowseResponse(path=mapper.to_display(container_path), items=items, next_cursor=next_cursor)


@router.get(
    "/exclusions",
    response_model=ExclusionsResponse,
    summary="E32 内置排除集（只读）",
    dependencies=_PROTECTED,
)
async def list_exclusions(
    request: Request,
    mode: RunMode | None = Query(default=None, description="host|sources；缺省取当前运行模式"),
    session: SessionContext = Depends(require_session),
) -> ExclusionsResponse:
    """返回指定模式下的内置排除集（强制生效，UI 可追加不可删减）。

    数据来源是 :class:`ExclusionRules` 本身，避免前端复制一份规则造成 D8 双写漂移。
    """
    del session
    settings = request.app.state.settings
    rules: ExclusionRules = request.app.state.exclusions or ExclusionRules(settings)
    effective = RunMode(mode.value if mode is not None else settings.restic_web_mode)
    items = rules.built_in(effective.value)
    core = rules.core(effective.value)
    mapper: PathMapper = request.app.state.path_mapper
    return ExclusionsResponse(
        mode=effective,
        items=items,
        core=core,
        display_items=[mapper.to_display(item) for item in items],
        display_core=[mapper.to_display(item) for item in core],
        total=len(items),
    )


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

    入参 ``sources`` 收**展示路径**（服务端 ``to_snapshot`` 还原，见模块 docstring 语义修订说明）；
    ``excludes`` 同为展示形态/纯 glob 模式，经 :func:`normalize_user_excludes` 三分类归一为
    容器形态后再与内置集叠加（否则预览里用户排除不生效）。
    命中 proc/sys/dev/run/docker/lost+found 类目录名时置黄色 ``warnings``（**不拦截**，
    方案 §3.2 四层防线第三层）；预览有超时与条目上限，超出置 ``truncated``。
    """
    del session
    settings = request.app.state.settings
    mapper: PathMapper = request.app.state.path_mapper
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    config_repo: ConfigRepo = request.app.state.config_repo

    sources = tuple(
        _to_container_path(mapper, item, allowed_prefixes=SOURCE_ALLOWED_PREFIXES)
        for item in payload.sources
    )
    user_excludes = normalize_user_excludes(
        list(payload.excludes), mapper, source_prefixes=(settings.mode_root,)
    )
    excludes = tuple(_dedupe(ExclusionRules(settings).built_in() + user_excludes))
    args = WhitelistArgs(
        targets=sources,
        excludes=excludes,
        json_output=True,
        dry_run=True,
        verbose=True,
        one_file_system=settings.restic_web_mode == "sources",
    )

    repo_url = (await config_repo.get_value("repo.url")) or ""
    env_extras = await load_credentials(config_repo)
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

def _to_container_path(
    mapper: PathMapper,
    raw: str,
    *,
    allowed_prefixes: tuple[str, ...],
) -> str:
    """把 E05/E31 的路径入参还原为容器内路径（语义修订的单一实现点）。

    新契约收**展示路径**（``/etc/nginx``）→ ``to_snapshot`` 还原；兼容文档旧示例的
    容器路径形态：入参已带当前模式根前缀（``/host`` / ``/sources``）时原样通过校验。

    Args:
        mapper: 路径映射器。
        raw: 客户端入参（展示路径或旧形态容器路径）。
        allowed_prefixes: 允许的容器前缀白名单。

    Returns:
        规范化且通过白名单校验的容器内路径。

    Raises:
        PathOutOfPrefixError: 两种形态都越出白名单（400）。
    """
    try:
        # 旧形态：已是容器路径（带模式根前缀）——文档示例字面兼容
        return mapper.validate(raw, allowed_prefixes=allowed_prefixes)
    except PathOutOfPrefixError:
        # 新契约：展示路径 → 还原为容器路径后再次校验（normpath 折叠 ../ 防穿越）
        return mapper.validate(mapper.to_snapshot(raw), allowed_prefixes=allowed_prefixes)


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


class _suppress:
    """极简上下文管理器：吞掉块内异常（用于收尾阶段的 best-effort 调用）。"""

    def __enter__(self) -> _suppress:
        """进入上下文。"""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        """退出上下文并吞掉异常。"""
        return True
