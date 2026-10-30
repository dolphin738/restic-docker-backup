"""仓库路由（R-023 / P3-C）：仓库 CRUD + E29 连通性测试 + E30 仓库状态（per-repo）。

与 config 时代的迁移（E4 裁决）：
- ``repo.url`` / ``repo.host_tag`` 不再是 config 键——仓库资料的本端点读写；
- E29/E30 从 ``routers/config.py`` 搬迁至此并按仓库作用域（旧的无参路径删除，
  前端同批切换；``repo_id`` 非 ``default`` 同样可用——R-023 的核心变化）；
- E30 状态缓存由「单仓库单值」改「按 repo_id 分桶的字典」（互不污染）。

E30 探测成本三招（沿用既有设计）：TTL 缓存（per-repo）/ 锁状态从 cat config 失败
输出识别（不额外调 list locks）/ 容量仅在 connected 时探测（失败只留空）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_RCLONE_CONFIG_APPEND,
    AUDIT_RCLONE_CONFIG_DELETE,
    AUDIT_RCLONE_CONFIG_EDIT,
    AUDIT_RCLONE_CONFIG_RESTORE,
    AUDIT_REPO_PASSWORD_SET,
    AUDIT_REPO_REPOINT,
    KEY_OPERATION_TIMEOUT_SECONDS,
    REPO_ID_PATTERN,
    REPO_PROBE_TIMEOUT_SECONDS,
    REPO_STATS_TIMEOUT_SECONDS,
)
from app.db.repositories.repo_repo import ReposRepo
from app.errors import (
    EnvOverriddenError,
    JobConflictError,
    NotFoundError,
    RcloneRemoteInUseError,
    RcloneUnavailableError,
    RepoNotFoundError,
    ValidationError,
)
from app.routers import repos_crud as crud
from app.routers import repos_ops as ops
from app.routers.repos_shared import (
    count_remote_usage,
    new_repo_id,
    rclone_remote_of,
    resolve_repo_id,
    to_out,
    validate_rclone_url_syntax,
    validate_repo_id,
    validate_repo_url,
    validate_repo_url_syntax,
)
from app.schemas.api_schemas import (
    OkResponse,
    RcloneArchiveItemOut,
    RcloneArchivesOut,
    RcloneConfigItemOut,
    RcloneConfigsOut,
    RcloneConfigWriteOut,
    RcloneConfigWriteRequest,
    RcloneRemotesOut,
    RcloneRemoteUsageOut,
    RcloneUsageRepoRef,
    RepoCapacity,
    RepoKeyOut,
    RepoKeysOut,
    RepoPasswordRequest,
    RepoPasswordResponse,
    RepoRepointOut,
    RepoRepointRequest,
    RepoStatusResponse,
)
from app.services.env_sync import passfile_path
from app.services.rclone import RcloneProbe
from app.services.rclone_config import (
    RcloneConfigConflict,
    RcloneConfigError,
    RcloneConfigNotFound,
    RcloneConfigService,
    RcloneConfigUnavailable,
)
from app.services.repo_keys import classify_keys, parse_key_list
from app.services.repo_status_cache import (
    invalidate_repo_status_cache,
    read_cached,
    write_cached,
)
from app.services.repo_status_cache import (
    status_lock as _status_lock,
)
from app.services.restic import ResticCmd, WhitelistArgs
from app.services.snapshot_catalog import invalidate_snapshot_catalog
from app.utils.passfile import generate_repo_password, write_passfile

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["repos"])

# 候选 3 五域拆分：各域经 register(router) 挂端点，调用顺序 = 路由声明顺序。
# 顺序是契约：_new_id 必须先于 {repo_id}（见 repos_crud 文件头）。
# 校验拆分是否生效请用 app.openapi()["paths"]——app.routes 里看不出惰性 include。
crud.register(router)
ops.register(router)

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# E30 进程内 TTL 缓存的状态与失效出口已搬到 services/repo_status_cache.py
# （候选 1 批次 0：编排器在 services 层，不能反向 import routers，否则成环）。
# 这里只保留 ``_status_lock`` 别名：读写入口统一走 ``read_cached`` / ``write_cached``，
# 缓存本体不再由本模块持有（测试如需重置请用 ``repo_status_cache.status_cache``）。

# 锁残留关键词（从 cat config 的失败输出里识别，零额外 restic 调用）
_LOCK_KEYWORDS: tuple[str, ...] = ("already locked", "unable to create lock", "remove stale locks")

# 仓库标识白名单（与 RepoCreateRequest.id 同源；路径参数侧再校验一次，防 `..` 穿越）
_REPO_ID_RE = re.compile(REPO_ID_PATTERN)


# 共享件（标识校验/生成、Repo→RepoOut、URL 语法校验）已搬到 routers/repos_shared.py
# （候选 3 五域拆分）。本模块保留 _ 前缀别名，避免约 20 处调用点全改。

_validate_repo_id = validate_repo_id
_new_repo_id = new_repo_id
_resolve_repo_id = resolve_repo_id
_to_out = to_out
_validate_repo_url = validate_repo_url
_validate_repo_url_syntax = validate_repo_url_syntax
_validate_rclone_url_syntax = validate_rclone_url_syntax
_rclone_remote_of = rclone_remote_of
_count_remote_usage = count_remote_usage


@router.get(
    "/rclone/remotes",
    response_model=RcloneRemotesOut,
    summary="A1 rclone 只读探测：conf 状态 + remote 清单（Q17）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def rclone_remotes(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneRemotesOut:
    """``rclone listremotes`` 只读探测（conf 存在性 + remote 名清单）。

    Q17 档位：conf 由宿主机 ``rclone config`` 生成、compose 挂载（**必须 :rw**——
    OAuth token 过期时 rclone 回写 conf，只读挂载 = 授权静默失效）；本端点
    **不生成、不编辑、不展示凭据**（conf 内 OAuth token 永不进 API）。

    用途：rclone 仓库 URL 保存前的前端提示 + A5 类型选择卡的 preset 清单来源。
    """
    del session
    probe: RcloneProbe = request.app.state.rclone_probe
    result = await probe.list_remotes()
    return RcloneRemotesOut(
        available=result.available,
        conf_present=result.conf_present,
        conf_path=result.conf_path,
        remotes=list(result.remotes),
        reason=result.reason,
    )


# ---------------------------------------------------------------- rclone 配置管理


def _rclone_config_service(request: Request) -> RcloneConfigService:
    """取配置管理服务（应用级单例，装配于 lifespan）。"""
    service: RcloneConfigService = request.app.state.rclone_config_service
    return service


def _raise_rclone_error(exc: RcloneConfigError) -> NoReturn:
    """服务层异常 → 统一错误响应（422 / 404 / 409 / 503，凭据已脱敏）。"""
    if isinstance(exc, RcloneConfigNotFound):
        raise NotFoundError(str(exc)) from exc
    if isinstance(exc, RcloneConfigConflict):
        raise JobConflictError(str(exc)) from exc
    if isinstance(exc, RcloneConfigUnavailable):
        raise RcloneUnavailableError(str(exc)) from exc
    raise ValidationError(str(exc)) from exc


@router.get(
    "/rclone/configs",
    response_model=RcloneConfigsOut,
    summary="rclone conf 段清单（凭据零回显；managed 段回显非凭据参数）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def rclone_configs(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneConfigsOut:
    """查询 rclone conf 全段（``rclone config dump`` → JSON → managed 过滤）。

    凭据（password/password2/pass/bearer_token）**永不回显**，只给 ``has_*`` 布尔与
    ``credential_obscured`` 形态布尔（方案 A）；非注册表类型（OAuth 网盘等）只露
    名字与 type，参数恒空（S3）。
    """
    del session
    service = _rclone_config_service(request)
    listing = await service.list_configs()
    # 一次仓库查询聚合出「每个 remote 被多少仓库引用」——列表页零额外探测（裁决 3）
    backed = await request.app.state.repos_repo.list_rclone_backed()
    used = _count_remote_usage(backed)
    return RcloneConfigsOut(
        conf_path=listing.conf_path,
        conf_present=listing.conf_present,
        configs=[
            RcloneConfigItemOut(
                remote=item.remote,
                type=item.type,
                managed=item.managed,
                params=dict(item.params),
                has_credentials=dict(item.has_credentials),
                credential_obscured=dict(item.credential_obscured),
                used_by_repos=used.get(item.remote, 0),
            )
            for item in listing.configs
        ],
        available=listing.available,
        reason=listing.reason,
    )


@router.get(
    "/rclone/config/{remote_name}/usage",
    response_model=RcloneRemoteUsageOut,
    summary="rclone 远端占用明细（引用仓库 + crypt 传递依赖）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def rclone_remote_usage(
    remote_name: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneRemoteUsageOut:
    """查询某 remote 被谁占用（**只读，零副作用**）。

    供 rclone 面板「查看占用」弹窗渲染；与 :func:`delete_rclone_config` 的阻断判定
    共用同一套匹配口径（remote 段完整相等），避免「提示说没人用、删时却 409」。
    """
    del session
    service = _rclone_config_service(request)
    backed = await request.app.state.repos_repo.list_rclone_backed()
    usage = await service.inspect_usage(remote_name)
    return RcloneRemoteUsageOut(
        remote=remote_name,
        repos=[
            RcloneUsageRepoRef(id=rid, name=name)
            for rid, name, url in backed
            if _rclone_remote_of(url) == remote_name
        ],
        dependent_remotes=list(usage.dependent_remotes),
        crypt=usage.crypt,
    )


@router.post(
    "/rclone/config/{remote_name}/repoint",
    response_model=RepoRepointOut,
    summary="批量改指向：把引用该 remote 的仓库切到另一个 remote（删除前的受控出口）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def repoint_rclone_remote(
    remote_name: str,
    payload: RepoRepointRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoRepointOut:
    """把 ``rclone:<old>:<path>`` 的仓库 url 批量改成 ``rclone:<new>:<path>``。

    这是「想删远端但还有仓库引用」时的正路：先改指向（快照仍可读，因为
    conf 里新旧两个段指向同一后端），再删旧段。只改 url 前缀——**不动数据、不动凭据**。
    地址被 env 覆盖的仓库（仅 default）拒绝参与（409 ENV_OVERRIDDEN，先改 env）。
    """
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    new_remote = (payload.new_remote or "").strip()
    if not new_remote or new_remote == remote_name:
        raise ValidationError(
            "目标远端名不能为空且不能与被替换者相同", data={"new_remote": new_remote}
        )
    backed = await repos_repo.list_rclone_backed()
    refs = [(rid, name, url) for rid, name, url in backed if _rclone_remote_of(url) == remote_name]
    if not refs:
        raise ValidationError(
            "没有任何仓库引用该远端，无需改指向", data={"remote": remote_name}
        )
    # 目标必须在册（否则改完仓库立刻不可达，比不改更糟）
    listing = await _rclone_config_service(request).list_configs()
    if new_remote not in {item.remote for item in listing.configs}:
        raise ValidationError(
            f"目标远端「{new_remote}」不在 rclone 配置中", data={"new_remote": new_remote}
        )
    for rid, _name, _url in refs:
        repo = await repos_repo.get(rid)
        if repo is not None and repo.url_source == "env":
            raise EnvOverriddenError("url", data={"repo_id": rid})
    updated: list[dict[str, str]] = []
    for rid, _name, url in refs:
        # rclone:<remote>:<path> → 保留 path，只换 remote 段
        tail = url.split(":", 2)[2] if url.count(":") >= 2 else ""
        new_url = f"rclone:{new_remote}:{tail}"
        await repos_repo.update(rid, url=new_url)
        updated.append({"id": rid, "url": new_url})
    await request.app.state.audit.record(
        AUDIT_REPO_REPOINT,
        target=remote_name,
        detail={"new_remote": new_remote, "repos": len(updated)},
        result="ok",
    )
    invalidate_repo_status_cache()
    invalidate_snapshot_catalog()
    return RepoRepointOut(old_remote=remote_name, new_remote=new_remote, repos=updated)


@router.get(
    "/rclone/archives",
    response_model=RcloneArchivesOut,
    summary="rclone 配置归档清单（只回键名，永不回值）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def list_rclone_archives(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneArchivesOut:
    """列出已删除、仍可一键写回的配置段（D3：删除即自动归档，保留期默认 365 天）。"""
    del session
    archive_repo = request.app.state.rclone_archive_repo
    return RcloneArchivesOut(
        items=[
            RcloneArchiveItemOut(
                remote=item.remote,
                type=item.type,
                deleted_at=item.deleted_at,
                used_by_repos=item.used_by_repos,
                keys=item.keys,
            )
            for item in await archive_repo.list_all()
        ]
    )


@router.post(
    "/rclone/archives/{remote_name}/restore",
    response_model=RcloneConfigWriteOut,
    summary="写回归档的 rclone 段（一键恢复；同名段已存在则 409）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def restore_rclone_archive(
    remote_name: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneConfigWriteOut:
    """把归档段原样写回 conf（含混淆态凭据），成功后删除归档记录。

    恢复后该 remote 的仓库立刻可再次访问（前提：底层 remote 仍在，crypt 链完整）。
    """
    del session
    archive_repo = request.app.state.rclone_archive_repo
    archive = await archive_repo.get(remote_name)
    if archive is None:
        raise NotFoundError(
            f"未找到远端「{remote_name}」的配置归档", data={"remote": remote_name}
        )
    service = _rclone_config_service(request)
    try:
        result = await service.restore_section(remote_name, archive.config)
    except RcloneConfigError as exc:
        _raise_rclone_error(exc)
    await archive_repo.delete(remote_name)
    invalidate_repo_status_cache()
    invalidate_snapshot_catalog()
    await request.app.state.audit.record(
        AUDIT_RCLONE_CONFIG_RESTORE,
        target=remote_name,
        detail={"type": result.type, "keys": len(archive.config)},
        result="ok",
    )
    return RcloneConfigWriteOut(
        remote=result.remote,
        type=result.type,
        conf_path=result.conf_path,
        action=result.action,
        remotes_after=list(result.remotes_after),
    )


@router.delete(
    "/rclone/archives/{remote_name}",
    response_model=OkResponse,
    summary="删除 rclone 配置归档记录（只删记录，不碰 conf）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def delete_rclone_archive(
    remote_name: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """清理归档记录（放弃这条后路）。conf 与其他数据一概不动。"""
    del session
    if not await request.app.state.rclone_archive_repo.delete(remote_name):
        raise NotFoundError(
            f"未找到远端「{remote_name}」的配置归档", data={"remote": remote_name}
        )
    return OkResponse()


@router.post(
    "/rclone/config/{remote_name}",
    response_model=RcloneConfigWriteOut,
    summary="新增 rclone conf 段（crypt / webdav；重名 409）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def create_rclone_config(
    remote_name: str,
    payload: RcloneConfigWriteRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneConfigWriteOut:
    """新增配置段（写 rclone ``config`` 的 create 子命令；审计 ``rclone.config_append``）。

    与 Q17「UI 不生成」的边界：仅 crypt/webdav 这类**凭据由用户自填**的类型可生成，
    OAuth 网盘类型（需浏览器授权流）不在注册表内 → 422。
    """
    del session
    service = _rclone_config_service(request)
    probe: RcloneProbe = request.app.state.rclone_probe
    remotes = await probe.list_remotes()
    try:
        result = await service.create(
            remote_name,
            payload.type,
            payload.params,
            value_obscured=payload.value_obscured,
            available_remotes=remotes.remotes if remotes.available else None,
        )
    except RcloneConfigError as exc:
        _raise_rclone_error(exc)
    await request.app.state.audit.record(
        AUDIT_RCLONE_CONFIG_APPEND,
        target=remote_name,
        # 只记名字、类型与「提交值形态」，不含凭据（S3）
        detail={"type": result.type, "value_obscured": payload.value_obscured},
    )
    return RcloneConfigWriteOut(
        remote=result.remote,
        type=result.type,
        conf_path=result.conf_path,
        action=result.action,
        remotes_after=list(result.remotes_after),
    )


@router.put(
    "/rclone/config/{remote_name}",
    response_model=RcloneConfigWriteOut,
    summary=(
        "编辑 rclone conf 段（全量更新；凭据留空 = 保持不变，历史明文段自动修正为混淆；"
        "非注册表类型 422）"
    ),
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def update_rclone_config(
    remote_name: str,
    payload: RcloneConfigWriteRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneConfigWriteOut:
    """编辑配置段（写 rclone ``config`` 的 update 子命令；审计 ``rclone.config_edit``）。

    落盘形态恒为混淆（裁决 17）；凭据留空时若记录为**明文**（早期版本/宿主机手写），
    后端按现值就地 ``--obscure`` 修正，不必重填密码。明文→混淆转换前会做一次 reveal
    一致性检查：解得开即中止（防二次混淆把仓库变成"空"）。
    """
    del session
    service = _rclone_config_service(request)
    try:
        result = await service.update(
            remote_name,
            payload.type,
            payload.params,
            value_obscured=payload.value_obscured,
        )
    except RcloneConfigError as exc:
        _raise_rclone_error(exc)
    await request.app.state.audit.record(
        AUDIT_RCLONE_CONFIG_EDIT,
        target=remote_name,
        # credentials_changed 只记布尔，不记值（S3）；形态字段只记布尔，不含凭据
        detail={
            "type": result.type,
            "credentials_changed": bool(payload.params),
            "value_obscured": payload.value_obscured,
        },
    )
    return RcloneConfigWriteOut(
        remote=result.remote,
        type=result.type,
        conf_path=result.conf_path,
        action=result.action,
        remotes_after=list(result.remotes_after),
    )


@router.delete(
    "/rclone/config/{remote_name}",
    response_model=RcloneConfigWriteOut,
    summary="删除 rclone conf 段（**全段可删**，含 OAuth 网盘段——删后需宿主机重新授权）",
    dependencies=_PROTECTED,
    tags=["rclone"],
)
async def delete_rclone_config(
    remote_name: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RcloneConfigWriteOut:
    """删除配置段（**有引用即 409**，删除保护方案 P0，裁决 1/2/3/4）。

    顺序严守「先查后删」：①引用扫描（只读、零副作用）→ ②通过才落 rclone
    命令 → ③成功后失效快照缓存。任一步拒绝时数据毫发无损（沿用
    ``delete_repo`` 既有纪律）。

    删除前顺手把**整段配置**归档（D3）：``delete()`` 内部本就要 dump 全段，
    这里再取一次做快照，成本是一次已在缓存里的 dump；归档后可一键写回。
    """
    del session
    service = _rclone_config_service(request)
    # ① 引用扫描（只读）：直接引用 = 仓库 url；传递依赖 = 压在本 remote 上的 crypt 段
    backed = await request.app.state.repos_repo.list_rclone_backed()
    refs = tuple((rid, name) for rid, name, url in backed if _rclone_remote_of(url) == remote_name)
    usage = await service.inspect_usage(remote_name)
    if refs or usage.dependent_remotes:
        raise RcloneRemoteInUseError(
            remote_name,
            repos=refs,
            dependent_remotes=usage.dependent_remotes,
            crypt=usage.crypt,
        )
    # 归档快照（**值等价于凭据**：只落 DB，绝不进响应/日志）
    archived = await service.snapshot_section(remote_name)
    try:
        result = await service.delete(remote_name)
    except RcloneConfigError as exc:
        _raise_rclone_error(exc)
    archive_repo = getattr(request.app.state, "rclone_archive_repo", None)
    if archived and archive_repo is not None:
        await archive_repo.save(
            remote_name,
            type_name=str(archived.get("type") or result.type),
            config=archived,
            used_by_repos=len(refs),
        )
    # ③ 段没了可能影响其他仓库可达性 → 一并失效快照缓存（P0 L4-3）
    invalidate_snapshot_catalog()
    invalidate_repo_status_cache()
    await request.app.state.audit.record(
        AUDIT_RCLONE_CONFIG_DELETE,
        target=remote_name,
        detail={"type": result.type, "archived_keys": len(archived or {})},
    )
    return RcloneConfigWriteOut(
        remote=result.remote,
        type=result.type,
        conf_path=result.conf_path,
        action=result.action,
        remotes_after=list(result.remotes_after),
    )


@router.post(
    "/repos/{repo_id}/password",
    response_model=RepoPasswordResponse,
    summary="R-023 设置仓库密码（仅密码文件缺失时）",
    dependencies=_PROTECTED,
)
async def set_repo_password(
    repo_id: str,
    payload: RepoPasswordRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoPasswordResponse:
    """写入仓库密码文件（``<passfile_root>/<repo_id>/password``，0600 原子写）。

    - ``password`` 为空 → 后端生成强随机密码（长度 ``REPO_PASSWORD_LENGTH``，与前端
      「生成强随机密码」同口径）并**一次性回显**（不落库、不落日志、不进审计）；
    - ``password`` 非空 → 按用户自填写入（响应不回显，用户已知）；
    - ``force`` → 文件已存在时也允许覆盖，但**必须先证明仓库当前打不开**
      （密码错误 / 未初始化 / 凭据无效……）。

    默认**只在文件不存在时允许**：该密码既是 restic 仓库密钥，也是 R-025 凭据加密的密钥源
    （HKDF info 混入 repo_id）——覆盖已存在的文件会让仓库不可读、已加密凭据全部失效。

    force 的放行条件（自救通道，2026-10-20）：密码文件存在但仓库打不开时，用户既删不掉
    default 仓库、旧密码又无从找回，界面上没有任何自救路径——故在此开一条「先探测后覆盖」
    的窄门；仓库能正常打开（密码正确）时仍拒绝，更换密码必须走 restic key passwd（轮换）。
    """
    del session
    _validate_repo_id(repo_id)
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)

    path = passfile_path(request.app.state.settings, repo_id)
    forced = False
    if path.exists():
        if not payload.force:
            raise ValidationError(
                f"仓库密码文件已存在（{path}）——密码既是仓库密钥也是凭据加密密钥源，"
                "覆盖会使仓库不可读且已加密凭据失效；如确需更换，请走「密钥轮换」，"
                "或在仓库已打不开（密码错误）时点「重设密码」覆盖"
            )
        # 覆盖前置门禁：仓库此刻能用现有密码打开 → 绝不允许覆盖（覆盖＝自毁仓库）
        status = await _probe_repo_status(request, repo_id)
        if status.connected:
            raise ValidationError(
                "仓库当前可正常打开（现有密码正确）——覆盖密码会让仓库不可读。"
                "若要更换密码，请走「密钥轮换」（restic key passwd）"
            )
        forced = True

    generated = not (payload.password or "").strip()
    plain = generate_repo_password() if generated else (payload.password or "")

    try:
        write_passfile(path, plain)
    except OSError as exc:
        raise ValidationError(
            f"密码文件写入失败（只读挂载或权限不足）：{path}；请在宿主机对应挂载目录手动放置该密码文件"
        ) from exc

    # 审计只记动作与是否生成/是否覆盖，明文绝不入审计/日志（红线 S3）
    await request.app.state.audit.record(
        AUDIT_REPO_PASSWORD_SET,
        target=repo_id,
        detail={"generated": generated, "forced": forced},
        result="ok",
    )
    invalidate_repo_status_cache()
    return RepoPasswordResponse(
        ok=True,
        generated=generated,
        password=plain if generated else None,
        path=str(path),
    )

@router.post(
    "/repos/{repo_id}/rebuild",
    response_model=RepoPasswordResponse,
    summary="重建仓库（清空快照并用新密码重新初始化；仅本地 FS 后端）",
    dependencies=_PROTECTED,
)
async def rebuild_repo(
    repo_id: str,
    payload: RepoPasswordRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoPasswordResponse:
    """忘密码 / 文件夹被旧仓库卡住时的自救通道：清空仓库全部快照、用新密码重新初始化。

    - 原为 default 专属（default 不可删、忘密码无出路）；2026-10-21 放开到**全部仓库**：
      删除仓库不触碰磁盘数据，同址新建会撞上打不开的旧仓库（报「密码错误」），
      非默认仓库此前没有任何恢复路径（「删除后换 URL 新建」同址即死循环）。
    - 仅支持本地文件系统后端（url 为本地绝对路径或 ``local:`` 前缀）；远程后端（s3/rest/sftp…）
      返回 422，提示手动清空后端存储后删除仓库、换 URL 新建（旧数据本就不可恢复）。
    - 流程：清空仓库目录内容（保留目录本身）→ 写新密码文件 → restic init——**清空必须
      先于覆盖密码文件**（清空失败 = 零损失，旧密码仍在；见函数体内注释）；
      运行中的任务会被 409 拒绝。
    - 仓库密码是凭据加密密钥源（HKDF info 混入 repo_id）：重建后该仓库的旧 enc:v2
      凭据无法用旧密钥解密，一并清除（需重新录入）；明文凭据不受影响。
    """
    del session
    _validate_repo_id(repo_id)
    # 跨介质序列（清空目录 → 写密码 → restic init → 清 enc:v2 凭据）交给编排器
    # （候选 1 批次 ③）：顺序契约（清空先于覆盖密码）与并发互斥都在那边。
    report = await request.app.state.repo_lifecycle.rebuild(
        repo_id, password=payload.password or None
    )
    return RepoPasswordResponse(
        ok=True,
        generated=report.generated,
        password=report.password,
        path=report.path,
    )


# 注：``generate_repo_password`` / ``write_passfile`` / ``_PASSWORD_CLASSES`` 已搬到
# ``utils/passfile.py``（候选 1 批次 ③）——编排器在 services 层需要它们，
# 而 services 不能反向 import routers。路由与编排器共用同一份实现。

@router.get(
    "/repos/{repo_id}/status",
    response_model=RepoStatusResponse,
    summary="E30 仓库状态（per-repo）",
    dependencies=_PROTECTED,
)
async def repo_status(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoStatusResponse:
    """仓库状态卡数据源：``{connected, locked, capacity, last_error}``（按 repo_id 缓存）。"""
    del session
    cached = read_cached(repo_id)
    if cached is not None:
        return cached

    async with _status_lock:
        cached = read_cached(repo_id)  # 双检：等锁期间可能已被别的请求刷新
        if cached is not None:
            return cached
        status = await _probe_repo_status(request, repo_id)
        write_cached(repo_id, status)
        return status


@router.get(
    "/repos/{repo_id}/keys",
    response_model=RepoKeysOut,
    summary="仓库密钥身份（只读；keys/ 是仓库唯一的明文文件）",
    dependencies=_PROTECTED,
)
async def repo_keys(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoKeysOut:
    """``restic key list --json`` 的中性化视图（**不缓存**：安全判定不吃 TTL）。

    用途：确认 init 后的身份中性化是否生效、发现历史遗留的真实身份 key、识别多 key
    接入。只返回展示白名单字段（`id`/`id_full`/`user`/`host`/`created`/`created_tz`/
    `current`/`neutral`）——``data``（master key 密文）与 ``salt/N/r/p``（KDF 参数）
    一律不进响应。

    失败不抛 5xx：地址未配置 / 未初始化 / 锁残留都回 ``overall=unknown`` +
    ``probe_error``（与 E30 只读探测的既有口径一致，UI 必须显示原因而非空白）。
    """
    del session
    _validate_repo_id(repo_id)
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    if not repo.url:
        return RepoKeysOut(repo_id=repo_id, probe_error="仓库地址未配置（请先填写仓库地址）")

    restic: ResticCmd = request.app.state.restic
    probe = await restic.probe(
        "key",
        repo_url=repo.url,
        repo_id=repo_id,
        args=WhitelistArgs(json_output=True, key_target="list"),
        timeout_seconds=KEY_OPERATION_TIMEOUT_SECONDS,
    )
    if probe.exit_code != 0:
        return RepoKeysOut(
            repo_id=repo_id,
            probe_error=probe.reason(KEY_OPERATION_TIMEOUT_SECONDS),
        )

    # restic 的 created 是按子进程 TZ 渲染的无时区字符串（build_env 注入 restic.tz
    # = config.timezone → settings.tz 回退），原样透出会与用户本地时间差出偏移且
    # 无从判断 → 把该 TZ 一并标注（不换算，C8）
    overall, items = classify_keys(
        parse_key_list(probe.lines), created_tz=request.app.state.restic.tz
    )
    return RepoKeysOut(
        repo_id=repo_id,
        overall=overall,
        keys=[RepoKeyOut.model_validate(item) for item in items],
        # 成功但读不到 key：仓库可能尚未初始化（空仓库上 key list 的行为随版本而异）
        probe_error=None if items else "未读取到任何密钥（仓库可能尚未初始化）",
    )


async def _probe_repo_status(request: Request, repo_id: str) -> RepoStatusResponse:
    """执行一次仓库探测（E30 的未缓存实现）。"""
    restic: ResticCmd = request.app.state.restic
    parser = request.app.state.parser
    repos_repo: ReposRepo = request.app.state.repos_repo

    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    if not repo.url:
        return RepoStatusResponse(
            configured=False, connected=False, locked=False, last_error="仓库地址未配置（请先填写仓库地址）"
        )

    probe = await restic.probe(
        "cat",
        repo_url=repo.url,
        repo_id=repo_id,
        args=WhitelistArgs(json_output=False, extra_positionals=("config",)),
        timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS,
    )

    if probe.exit_code != 0:
        return RepoStatusResponse(
            connected=False,
            locked=_looks_locked(probe.lines),
            last_error=probe.reason(REPO_PROBE_TIMEOUT_SECONDS),
            failure_kind=probe.kind(),
        )

    return RepoStatusResponse(
        connected=True,
        locked=False,
        capacity=await _read_capacity(restic, parser, repo_url=repo.url, repo_id=repo_id),
    )


async def _read_capacity(
    restic: ResticCmd, parser: Any, *, repo_url: str, repo_id: str
) -> RepoCapacity:
    """``restic stats --mode raw-data --json`` 读取仓库容量（失败返回空容量，不影响 connected）。"""
    probe = await restic.probe(
        "stats",
        repo_url=repo_url,
        repo_id=repo_id,
        args=WhitelistArgs(json_output=True, stats_mode="raw-data"),
        timeout_seconds=REPO_STATS_TIMEOUT_SECONDS,
    )
    if probe.exit_code != 0:
        return RepoCapacity()
    blob = parser.parse_json_blob("\n".join(probe.lines))
    if not isinstance(blob, dict):
        return RepoCapacity()
    return RepoCapacity(
        size=_as_optional_int(blob.get("total_size")),
        snaps=_as_optional_int(blob.get("snapshots_count")),
    )


def _looks_locked(lines: list[str]) -> bool:
    """从探测失败输出里识别锁残留（避免为锁状态再打一次仓库）。"""
    joined = "\n".join(lines).lower()
    return any(keyword in joined for keyword in _LOCK_KEYWORDS)


def _as_optional_int(value: Any) -> int | None:
    """把 restic JSON 字段安全地转为可选整数。"""
    return int(value) if isinstance(value, int | float) else None
