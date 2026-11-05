"""rclone 远端与 conf 段管理域（E45 + E48–E51）。

候选 3 五域拆分第 4 域（最大的一域）：从 ``routers/repos.py`` 原样搬出，
端点路径 / 响应模型 / 审计动作 / 声明顺序全部不变。

域内既有端点：remotes 探测、configs 列表、remote usage、repoint（批量改指向）、
config archives 三段（list/restore/delete）、config 三段（create/update/delete）；
另含 conf 加密三态 5 端点（status/enable/rekey/disable/unlock）。

用 ``register(router)`` 挂到聚合器（见 ``repos_crud`` 文件头对顺序契约的说明）。
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import NoReturn

from fastapi import APIRouter, Depends, Request
from pydantic import SecretStr

from app.auth.dependencies import require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_RCLONE_CONF_DECRYPT,
    AUDIT_RCLONE_CONF_ENCRYPT,
    AUDIT_RCLONE_CONF_REKEY,
    AUDIT_RCLONE_CONFIG_APPEND,
    AUDIT_RCLONE_CONFIG_DELETE,
    AUDIT_RCLONE_CONFIG_EDIT,
    AUDIT_RCLONE_CONFIG_RESTORE,
    AUDIT_REPO_REPOINT,
)
from app.db.repositories.repo_repo import ReposRepo
from app.errors import (
    EnvOverriddenError,
    JobConflictError,
    NotFoundError,
    RcloneConfLockedError,
    RcloneRemoteInUseError,
    RcloneUnavailableError,
    ValidationError,
)
from app.routers.repos_shared import (
    PROTECTED,
    count_remote_usage,
    rclone_remote_of,
    validate_rclone_url_syntax,
)
from app.schemas.api_schemas import (
    OkResponse,
    RcloneArchiveItemOut,
    RcloneArchivesOut,
    RcloneConfEnableRequest,
    RcloneConfEncryptionOut,
    RcloneConfigItemOut,
    RcloneConfigsOut,
    RcloneConfigWriteOut,
    RcloneConfigWriteRequest,
    RcloneConfPasswordOnceOut,
    RcloneConfPasswordRequest,
    RcloneConfRekeyRequest,
    RcloneRemotesOut,
    RcloneRemoteUsageOut,
    RcloneUsageRepoRef,
    RepoRepointOut,
    RepoRepointRequest,
)
from app.services.rclone import RCLONE_CONF_LOCKED_HINT, RcloneProbe
from app.services.rclone_config import (
    RcloneConfigConflict,
    RcloneConfigError,
    RcloneConfigNotFound,
    RcloneConfigService,
    RcloneConfigUnavailable,
)
from app.services.rclone_crypto import RcloneCryptoService, password_command_for
from app.services.repo_status_cache import invalidate_repo_status_cache
from app.services.snapshot_catalog import invalidate_snapshot_catalog

logger = logging.getLogger(__name__)

_PROTECTED = PROTECTED
_rclone_remote_of = rclone_remote_of
_count_remote_usage = count_remote_usage
_validate_rclone_url_syntax = validate_rclone_url_syntax

_RCLONE_PASSWORD_COMMAND = "RCLONE_PASSWORD_COMMAND"


def _crypto_service(request: Request) -> RcloneCryptoService:
    """取 conf 加密服务（应用级单例，装配于 lifespan）。"""
    service: RcloneCryptoService = request.app.state.rclone_crypto
    return service


async def _conf_write_guard(request: Request, *, unlock: bool = False) -> RcloneCryptoService:
    """写前门禁：活跃 job 409；锁死态除 unlock 外 503（提示不含口令）。"""
    active = await request.app.state.job_repo.list_active()
    if active:
        raise JobConflictError(data={"running_job_id": active[0].id})
    service = _crypto_service(request)
    if not unlock and (await service.status()).conf_locked:
        raise RcloneConfLockedError(RCLONE_CONF_LOCKED_HINT)
    return service


def _secret_text(value: SecretStr | None) -> str | None:
    """只在调用服务层时解开一次；绝不进日志 / 审计 / 异常消息。"""
    return value.get_secret_value() if value is not None else None


def _atomic_write_pass(path: Path, password: str) -> None:
    """同目录 mkstemp + os.replace 原子写 0600；不追加尾随换行。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="rclone-unlock-", dir=path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(password.encode())
            tmp.chmod(0o600)
            os.replace(tmp, path)
        finally:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)
    except OSError:
        raise RcloneUnavailableError("配置口令文件不可写") from None


def _restore_pass(path: Path, previous: bytes | None) -> None:
    """验通失败时恢复原口令文件；原本不存在则删除刚写的文件。"""
    with suppress(OSError):
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)
            path.chmod(0o600)


def _rollback_unlock(previous_env: str | None, path: Path, previous: bytes | None) -> None:
    """回滚运行期口令命令与口令文件；异常路径也必须零残留（口令本身不含在入参里）。"""
    if previous_env is None:
        os.environ.pop(_RCLONE_PASSWORD_COMMAND, None)
    else:
        os.environ[_RCLONE_PASSWORD_COMMAND] = previous_env
    _restore_pass(path, previous)


async def _unlock_conf(service: RcloneCryptoService, password: str) -> None:
    """写口令文件 → 重建运行期口令命令 → status() 验通；不通则回滚并 422。"""
    current = await service.status()
    pass_path = Path(current.pass_path)
    try:
        previous = pass_path.read_bytes() if pass_path.is_file() else None
    except OSError:
        raise RcloneUnavailableError("配置口令文件不可读") from None
    previous_env = os.environ.get(_RCLONE_PASSWORD_COMMAND)
    _atomic_write_pass(pass_path, password)
    try:
        os.environ[_RCLONE_PASSWORD_COMMAND] = password_command_for(pass_path)
    except OSError:
        _rollback_unlock(previous_env, pass_path, previous)
        raise RcloneUnavailableError("配置口令脚本不可写") from None
    try:
        unlocked = not (await service.status()).conf_locked
    except Exception:
        _rollback_unlock(previous_env, pass_path, previous)
        raise
    if not unlocked:
        _rollback_unlock(previous_env, pass_path, previous)
        raise ValidationError("口令无法解锁 rclone.conf，请确认口令后重试") from None


def register(router: APIRouter) -> None:
    """把本域端点挂到聚合器（调用顺序 = 路由声明顺序）。"""
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
            conf_encrypted=result.conf_encrypted,
            conf_locked=result.conf_locked,
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
            conf_encrypted=listing.conf_encrypted,
            conf_locked=listing.conf_locked,
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

    # ---------------------------------------------------------------- rclone conf 加密三态

    @router.get(
        "/rclone/conf-encryption",
        response_model=RcloneConfEncryptionOut,
        summary="rclone conf 加密三态（口令明文永不回显）",
        dependencies=_PROTECTED,
        tags=["rclone"],
    )
    async def rclone_conf_encryption(
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RcloneConfEncryptionOut:
        """读文件头 + 加密时 check，返回三态与口令文件存在性。"""
        del session
        status = await _crypto_service(request).status()
        return RcloneConfEncryptionOut(
            conf_encrypted=status.conf_encrypted,
            conf_locked=status.conf_locked,
            password_present=status.password_present,
            conf_path=status.conf_path,
            pass_path=status.pass_path,
        )

    @router.post(
        "/rclone/conf-encryption/enable",
        response_model=RcloneConfPasswordOnceOut,
        summary="启用 rclone conf 加密（省略口令则生成 32 位并一次性回显）",
        dependencies=_PROTECTED,
        tags=["rclone"],
    )
    async def enable_rclone_conf_encryption(
        payload: RcloneConfEnableRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RcloneConfPasswordOnceOut:
        """服务层 ValidationError / RcloneUnavailableError 直接透传；口令不进审计。"""
        del session
        service = await _conf_write_guard(request)
        generated = payload.password is None
        password = await service.enable(_secret_text(payload.password), backup=payload.backup)
        await request.app.state.audit.record(
            AUDIT_RCLONE_CONF_ENCRYPT,
            detail={"generated": generated, "backup": payload.backup, "result": "ok"},
            result="ok",
        )
        return RcloneConfPasswordOnceOut(password_once=password if generated else None)

    @router.post(
        "/rclone/conf-encryption/rekey",
        response_model=RcloneConfPasswordOnceOut,
        summary="更换 rclone conf 加密口令（一次性回显新口令）",
        dependencies=_PROTECTED,
        tags=["rclone"],
    )
    async def rekey_rclone_conf_encryption(
        payload: RcloneConfRekeyRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RcloneConfPasswordOnceOut:
        """新口令一次性回显；审计 detail 只记 generated / result。"""
        del session
        service = await _conf_write_guard(request)
        password = await service.rekey(
            _secret_text(payload.old_password), _secret_text(payload.new_password)
        )
        await request.app.state.audit.record(
            AUDIT_RCLONE_CONF_REKEY, detail={"generated": False, "result": "ok"}, result="ok"
        )
        return RcloneConfPasswordOnceOut(password_once=password)

    @router.post(
        "/rclone/conf-encryption/disable",
        response_model=OkResponse,
        summary="停用 rclone conf 加密（需当前口令）",
        dependencies=_PROTECTED,
        tags=["rclone"],
    )
    async def disable_rclone_conf_encryption(
        payload: RcloneConfPasswordRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> OkResponse:
        """停用后退回明文 conf；缺 current_password 一律 422。"""
        del session
        service = await _conf_write_guard(request)
        password = _secret_text(payload.current_password)
        if password is None:
            raise ValidationError("当前口令不能为空")
        await service.disable(password)
        await request.app.state.audit.record(
            AUDIT_RCLONE_CONF_DECRYPT, detail={"generated": False, "result": "ok"}, result="ok"
        )
        return OkResponse()

    @router.post(
        "/rclone/conf-encryption/unlock",
        response_model=OkResponse,
        summary="输入口令解锁 rclone conf（锁死态也可用）",
        dependencies=_PROTECTED,
        tags=["rclone"],
    )
    async def unlock_rclone_conf_encryption(
        payload: RcloneConfPasswordRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> OkResponse:
        """口令原子写入唯一落点，status() 验通后重建运行期口令命令；复用换口令审计动作。"""
        del session
        service = await _conf_write_guard(request, unlock=True)
        password = _secret_text(payload.password)
        if password is None:
            raise ValidationError("口令不能为空")
        await _unlock_conf(service, password)
        await request.app.state.audit.record(
            AUDIT_RCLONE_CONF_REKEY, detail={"generated": False, "result": "ok"}, result="ok"
        )
        return OkResponse()
