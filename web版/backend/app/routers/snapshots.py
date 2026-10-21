"""快照路由：E17 快照列表 + E18 文件树（§5.1 L477-484 / R-009）。

两条约定：

1. **路径语义**：快照里存的是**容器路径**（``/sources/etc/nginx``），两条端点对外一律给
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
import logging
import posixpath

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_TAG_UPDATE,
    SNAPSHOT_FILES_DEFAULT_LIMIT,
    SNAPSHOT_FILES_MAX_LIMIT,
    SNAPSHOT_FILES_MAX_OFFSET,
    SNAPSHOT_FILES_TIMEOUT_SECONDS,
    SNAPSHOT_LIST_TIMEOUT_SECONDS,
    TAG_OPERATION_TIMEOUT_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import ReposRepo
from app.dependencies import current_repo_id
from app.errors import ResticFailedError, ValidationError
from app.schemas.api_schemas import (
    Page,
    SnapshotFileOut,
    SnapshotFileSearchRequest,
    SnapshotFileSearchResponse,
    SnapshotFilesResponse,
    SnapshotOut,
    TagChanged,
    TagSnapshotsRequest,
    TagSnapshotsResponse,
)
from app.services.env_sync import load_credentials
from app.services.parser import FileRecord, OutputParser, SnapshotRecord
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs, failure_reason
from app.utils.cursor import decode_cursor, encode_cursor

logger = logging.getLogger(__name__)

# 快照 id 格式约束（与 restic 小写十六进制形态一致；A3 端点用于打/去标签的目标校验）
_SNAPSHOT_ID_CHARS = frozenset("0123456789abcdef")


def _is_snapshot_id(value: str) -> bool:
    """快照 id 合法性（8~64 位小写十六进制）。"""
    return bool(value) and 8 <= len(value) <= 64 and set(value) <= _SNAPSHOT_ID_CHARS


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
    tag: str | None = Query(
        default=None,
        description="按快照 tags 数组**包含**过滤（如任务 tag task-1）；缺省不过滤",
    ),
    host: str | None = Query(
        default=None,
        description="按快照 host 精确过滤（R-023 多主机视图；缺省不过滤）",
    ),
    repo_id: str = Depends(current_repo_id),
    session: SessionContext = Depends(require_session),
) -> Page[SnapshotOut]:
    """``restic snapshots --json`` → 映射原路径语义 →（可选 tag 包含过滤）→ 按时间倒序 → 分页。

    ``?tag=`` 过滤（X2 owner 裁决）在 **Python 侧**对取回结果做——restic snapshot 的
    tags 是数组、任务 tag 形如 ``task-<id>``，语义为"包含该 tag 则保留"；不往 restic
    argv 传 ``--tag``（S2 白名单不扩面）。参数缺省时行为与既有契约完全一致。
    """
    del session
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库管理页填写仓库地址）")

    argv = restic.build("snapshots", args=WhitelistArgs(json_output=True))
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
    probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_LIST_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, SNAPSHOT_LIST_TIMEOUT_SECONDS)
        logger.warning("快照列表探测失败：%s", reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    records = await asyncio.to_thread(parser.parse_snapshot_list, "\n".join(probe.lines))
    items = [_to_snapshot_out(record, mapper) for record in records if record.snapshot_id]
    if tag:
        # X2：包含语义过滤（tags 数组含该 tag 即保留），分页前先过滤保证 total 正确
        items = [item for item in items if tag in item.tags]
    if host:
        # R-023 4B：多主机视图——按快照 host 精确过滤（与 tag 同机制位，分页前执行）
        items = [item for item in items if item.host == host]
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
    repo_id: str = Depends(current_repo_id),
    session: SessionContext = Depends(require_session),
) -> SnapshotFilesResponse:
    """``restic ls --json <id> <子树>`` → 直接子项过滤 → 游标分页（懒加载树一次只要一层）。"""
    del session
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库管理页填写仓库地址）")

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
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
    probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_FILES_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, SNAPSHOT_FILES_TIMEOUT_SECONDS)
        logger.warning("快照文件树探测失败（snapshot=%s path=%s）：%s", snapshot_id, container_path, reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    records = await asyncio.to_thread(parser.parse_file_list, "\n".join(probe.lines))
    entries = _direct_children(records, container_path, mapper)

    start = decode_cursor(cursor, SNAPSHOT_FILES_MAX_OFFSET)
    window = entries[start : start + limit]
    has_more = start + limit < len(entries)
    return SnapshotFilesResponse(
        snapshot_id=snapshot_id,
        path=mapper.to_display(container_path),
        items=window,
        next_cursor=encode_cursor(start + limit) if has_more else None,
        truncated=probe.truncated,
    )


@router.post(
    "/snapshots/{snapshot_id}/files/search",
    response_model=SnapshotFileSearchResponse,
    summary="P3 快照内文件搜索",
    dependencies=_PROTECTED,
)
async def search_snapshot_files(
    snapshot_id: str,
    payload: SnapshotFileSearchRequest,
    request: Request,
    repo_id: str = Depends(current_repo_id),
    session: SessionContext = Depends(require_session),
) -> SnapshotFileSearchResponse:
    """``restic ls --json <id>``（全量）→ **服务端**子串过滤 → 游标分页（R6/V-3）。

    与 E18 的关系：同一数据源、同一游标口径（``decode/encode_cursor``），差别是
    E18 只取子树的**直接子项**（懒加载树），本端点跨层级匹配全部条目。

    为什么不过滤下推进 restic：``ls`` 没有 name-filter argv，S2 白名单也不为搜索
    扩面；所以「下推」的落点是**服务端过滤**（restic 只跑一次），**前端绝不拉全量
    再过滤**——那正是本端点要消灭的用法（方案 §4.8 V-3）。

    匹配口径：对**展示路径**（``display_path``，用户看见的形态）做大小写不敏感
    子串匹配；既匹配目录也匹配文件，输出按展示路径升序（游标分页要求稳定序）。
    """
    del session
    query = payload.query.strip()
    if not query:
        raise ValidationError("搜索关键词不能为空", data={"field": "query"})

    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库管理页填写仓库地址）")

    # 不带子树 positional：搜索覆盖整棵快照树
    argv = restic.build("ls", args=WhitelistArgs(snapshot_id=snapshot_id, json_output=True))
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
    probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_FILES_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, SNAPSHOT_FILES_TIMEOUT_SECONDS)
        logger.warning("快照文件搜索探测失败（snapshot=%s）：%s", snapshot_id, reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    records = await asyncio.to_thread(parser.parse_file_list, "\n".join(probe.lines))
    matches = _search_matches(records, query, mapper)

    start = decode_cursor(payload.cursor, SNAPSHOT_FILES_MAX_OFFSET)
    window = matches[start : start + payload.limit]
    has_more = start + payload.limit < len(matches)
    return SnapshotFileSearchResponse(
        snapshot_id=snapshot_id,
        query=query,
        items=window,
        total=len(matches),
        next_cursor=encode_cursor(start + payload.limit) if has_more else None,
        truncated=probe.truncated,
    )


@router.post(
    "/snapshots/tag",
    response_model=TagSnapshotsResponse,
    summary="A3 快照打/去标签（restic tag --add/--remove）",
    dependencies=_PROTECTED,
)
async def tag_snapshots(
    payload: TagSnapshotsRequest,
    request: Request,
    repo_id: str = Depends(current_repo_id),
    session: SessionContext = Depends(require_session),
) -> TagSnapshotsResponse:
    """``restic tag --add/--remove`` → 改标签的快照新旧 id 映射（独占锁修改）。

    设计要点（restic 0.19.1 ``cmd_tag.go`` 源码核验）：
    - 至少须 ``add``/``remove`` 之一，否则 restic 端 "nothing to do!"——拼装层已前移拒 422；
    - 无 ``snapshot_ids`` 且又无 ``tag_filter``/``host`` 时，restic 会**改仓库内全部快照**
      （高危），强制拒 422；
    - ``tag`` 取独占锁，与备份并发会失败（exit 11 锁冲突），由 :func:`failure_reason`
      明示「仓库锁残留」文案，不静默。
    """
    del session
    add = [tag.strip() for tag in (payload.add or []) if tag and tag.strip()]
    remove = [tag.strip() for tag in (payload.remove or []) if tag and tag.strip()]
    if not add and not remove:
        raise ValidationError(
            "至少需要指定 add 或 remove 中的一个标签",
            data={"add": payload.add, "remove": payload.remove},
        )

    snapshot_ids = [sid.strip() for sid in (payload.snapshot_ids or []) if sid and sid.strip()]
    if not snapshot_ids and not (payload.tag_filter or payload.host):
        raise ValidationError(
            "必须指定目标快照（snapshot_ids）或过滤条件（tag_filter/host），否则将修改仓库内全部快照",
            data={"snapshot_ids": snapshot_ids, "tag_filter": payload.tag_filter, "host": payload.host},
        )
    for sid in snapshot_ids:
        if not _is_snapshot_id(sid):
            raise ValidationError("快照 id 非法（须为 8~64 位小写十六进制）", data={"snapshot_id": sid})

    restic: ResticCmd = request.app.state.restic
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    audit = request.app.state.audit
    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库管理页填写仓库地址）")

    args = WhitelistArgs(
        tags=tuple(payload.tag_filter or []),
        host=payload.host,
        tag_add=tuple(add),
        tag_remove=tuple(remove),
        extra_positionals=tuple(snapshot_ids),
    )
    argv = restic.build("tag", args=args)
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
    probe = await restic.run_json(argv, env=env, timeout_seconds=TAG_OPERATION_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, TAG_OPERATION_TIMEOUT_SECONDS)
        logger.warning("快照打/去标签失败：%s", reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    changed_count = 0
    changed: list[TagChanged] = []
    for line in probe.lines:
        parsed = _try_json(line)
        if not parsed:
            continue
        if parsed.get("message_type") == "changed":
            changed.append(
                TagChanged(
                    old_snapshot_id=str(parsed.get("old_snapshot_id", "")),
                    new_snapshot_id=str(parsed.get("new_snapshot_id", "")),
                )
            )
        elif parsed.get("message_type") == "summary":
            changed_count = int(parsed.get("changed_snapshots", 0) or 0)

    await audit.record(
        AUDIT_TAG_UPDATE,
        target="snapshot",
        detail={
            "add": add,
            "remove": remove,
            "target_count": len(snapshot_ids),
            "tag_filter": payload.tag_filter,
            "host": payload.host,
            "changed_count": changed_count,
        },
        result="ok",
    )
    return TagSnapshotsResponse(changed_count=changed_count, changed=changed, truncated=probe.truncated)


def _try_json(line: str) -> dict | None:
    """尝试把输出行解析为 JSON 对象；失败（如 restic 进度/警告文本）返回 None。"""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        import json

        return json.loads(line)
    except (ValueError, TypeError):
        return None


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


def _search_matches(
    records: list[FileRecord],
    query: str,
    mapper: PathMapper,
) -> list[SnapshotFileOut]:
    """跨层级匹配：展示路径含关键词（大小写不敏感）→ 按展示路径升序去重。

    与 :func:`_direct_children` 平级（同一 ``ls`` 数据源的两种投影），不共享排序
    口径：搜索结果按**路径**升序（层级可读），文件树按「目录优先 + 名称」升序。

    Args:
        records: ``ls`` 解析出的全部条目。
        query: 已 trim 的非空关键词。
        mapper: 路径映射器（生成 display_path）。

    Returns:
        去重后的命中条目（顺序稳定，供游标分页切片）。
    """
    needle = query.casefold()
    items: list[SnapshotFileOut] = []
    seen: set[str] = set()
    for record in records:
        item_path = record.path
        if not item_path:
            continue
        display_path = mapper.to_display(item_path)
        if needle not in display_path.casefold():
            continue
        if item_path in seen:
            continue
        seen.add(item_path)
        items.append(
            SnapshotFileOut(
                name=record.name or item_path.rsplit("/", 1)[-1],
                type=record.type or "file",
                path=item_path,
                display_path=display_path,
                size=record.size,
                mtime=record.mtime,
            )
        )
    items.sort(key=lambda item: item.display_path)
    return items


def _direct_children(
    records: list[FileRecord],
    subtree: str,
    mapper: PathMapper,
) -> list[SnapshotFileOut]:
    """过滤出子树的**直接子项**（父目录 == 子树），并按"目录优先、名称升序"排序。

    Args:
        records: ``ls`` 解析出的全部条目（可能递归到多层）。
        subtree: 容器内规范化的子树路径（如 ``/sources/etc``）。
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
