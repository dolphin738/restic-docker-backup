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

import asyncio
import contextlib
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_RCLONE_CONFIG_APPEND,
    AUDIT_RCLONE_CONFIG_DELETE,
    AUDIT_RCLONE_CONFIG_EDIT,
    AUDIT_RCLONE_CONFIG_RESTORE,
    AUDIT_REPO_ARCHIVE,
    AUDIT_REPO_DELETE,
    AUDIT_REPO_ID_RELEASE,
    AUDIT_REPO_PASSWORD_SET,
    AUDIT_REPO_PURGE,
    AUDIT_REPO_REBUILD,
    AUDIT_REPO_REPOINT,
    AUDIT_REPO_RESTORE,
    AUDIT_REPO_TEST,
    KEY_OPERATION_TIMEOUT_SECONDS,
    REPO_ID_MAX_LENGTH,
    REPO_ID_PATTERN,
    REPO_INIT_TIMEOUT_SECONDS,
    REPO_PASSWORD_LENGTH,
    REPO_PROBE_TIMEOUT_SECONDS,
    REPO_STATS_TIMEOUT_SECONDS,
    REPO_STATUS_CACHE_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import Repo, ReposRepo
from app.errors import (
    EnvOverriddenError,
    JobConflictError,
    NotFoundError,
    RcloneRemoteInUseError,
    RcloneUnavailableError,
    RepoNotFoundError,
    ResticFailedError,
    ValidationError,
)
from app.schemas.api_schemas import (
    ArchivedRepoOut,
    ArchiveListOut,
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
    RepoCreateRequest,
    RepoIdSuggestion,
    RepoKeyOut,
    RepoKeysOut,
    RepoOut,
    RepoPasswordRequest,
    RepoPasswordResponse,
    RepoReleaseRequest,
    RepoRepointOut,
    RepoRepointRequest,
    RepoStatusResponse,
    RepoTestResponse,
    RepoTombstoneOut,
    RepoUpdateRequest,
    RepoUsageMirrorRef,
    RepoUsageOut,
)
from app.services.env_sync import passfile_path, passfile_status
from app.services.rclone import RcloneProbe
from app.services.rclone_config import (
    RcloneConfigConflict,
    RcloneConfigError,
    RcloneConfigNotFound,
    RcloneConfigService,
    RcloneConfigUnavailable,
)
from app.services.repo_keys import classify_keys, neutralize_repo_key, parse_key_list
from app.services.restic import ResticCmd, WhitelistArgs, failure_kind, failure_reason
from app.services.snapshot_catalog import invalidate_snapshot_catalog
from app.utils.passfile import remove_passfile
from app.utils.time import now_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["repos"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# E30 进程内 TTL 缓存：按 repo_id 分桶（多仓库互不污染）
_status_cache: dict[str, tuple[float, RepoStatusResponse]] = {}
_status_lock = asyncio.Lock()

# 锁残留关键词（从 cat config 的失败输出里识别，零额外 restic 调用）
_LOCK_KEYWORDS: tuple[str, ...] = ("already locked", "unable to create lock", "remove stale locks")

# 仓库标识白名单（与 RepoCreateRequest.id 同源；路径参数侧再校验一次，防 `..` 穿越）
_REPO_ID_RE = re.compile(REPO_ID_PATTERN)


def _validate_repo_id(repo_id: str) -> str:
    """校验路径参数中的仓库标识（非法即 422，杜绝拼进密码文件路径时发生穿越）。"""
    if len(repo_id) > REPO_ID_MAX_LENGTH or _REPO_ID_RE.match(repo_id) is None:
        raise ValidationError(
            f"仓库标识非法：仅允许字母、数字、下划线与连字符（1-{REPO_ID_MAX_LENGTH} 字符，且以字母或数字开头）"
        )
    return repo_id


def _repo_local_path(url: str) -> Path | None:
    """解析 restic 仓库 URL 的本地文件系统路径；非本地后端返回 ``None``。

    接受：``/abs/path``、``local:/abs/path``、Windows ``C:\\...``；
    拒绝一切远程 scheme（``s3:`` / ``rest:`` / ``sftp:`` / ``rclone:`` … 及 ``://`` 形式）。
    重建仅支持本地文件系统后端（远端无法在此安全清空，提示手动处理）。
    """
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith("local:"):
        u = u[len("local:"):]
    # Windows 盘符（C:\ / C:/）视为本地
    if re.match(r"^[A-Za-z]:[\\/]", u):
        return Path(u)
    # 远程 scheme：含 :// 或 "word:" 形式（盘符已在上一步过滤）
    if "://" in u or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", u):
        return None
    return Path(u)


def _is_fs_root(path: Path) -> bool:
    """是否文件系统根（``/``、``C:\\``、UNC share 根）——清空类动作的硬拒绝线。

    可移植判据（``str(path) == path.anchor``）：旧的 ``path == Path(path.root)``
    在 Windows 上对 ``/`` 失效（``WindowsPath('/')`` 的 root 是 ``\\``，两者不相等），
    会把「当前盘根目录」放行给 rmtree。rebuild 与 delete 的 purge_disk 共用。
    """
    return str(path) == path.anchor


# ---------------------------------------------------------------- A8 仓库标识自动生成
#
# owner 2026-10-17 07:37 立项：POST /repos 的 id 由必填改为可选，缺省服务端生成。
# owner 2026-10-22 裁决改版（单字母前缀方案）：``repo-<26 位小写 ULID>``（31 字符、
# 不可读）→ ``r<YYMMDD>-<6 位随机>``（14 字符，如 ``r261022-k3f9x2``）：
# - 短且可读，日期段让人一眼看出创建时间，字典序即时间序（百年内）；
# - 单字母 ``r`` 前缀保留「服务端生成」的辨识度，同时满足「小写字母开头」的
#   REPO_ID_PATTERN 硬约束（纯日期开头 ``261022-…`` 会被 create 拒掉）；
# - 存量 ``repo-<ULID>`` id 全部照旧工作，本规则只影响新建，零迁移。
# 落库前 exists 查重 + 重试（碰撞概率极低，重试只是形式化兜底）。

# Crockford Base32 小写字母表（剔除易混淆的 i/l/o/u，与密码生成字符集同思路）
_ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_RANDOM_LENGTH = 6


def _new_repo_id() -> str:
    """生成一个候选仓库标识（``r<YYMMDD>-<6 位小写 Base32 随机>``，14 字符）。

    日期段取 **UTC**（C8：时间语义只走 ``utils/time``）：UTC 口径无环境依赖、
    跨部署可比；若用本地时区，「同日」边界会随部署时区漂移。随机段走
    ``secrets.choice``（CSPRNG）。
    """
    stamp = now_utc().strftime("%y%m%d")
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_RANDOM_LENGTH))
    return f"r{stamp}-{suffix}"


async def _resolve_repo_id(repos_repo: ReposRepo, provided: str | None) -> str:
    """A8：解析新建仓库的标识。

    - 显式提供（非空）→ 原样返回，落库时再由 ``ReposRepo.create`` 做形态 + 存在性校验；
    - 留空 → 服务端生成 ``r<YYMMDD>-<6 位随机>``，生成前 exists 查重，碰撞重试（上限 10 次）。
    """
    candidate = (provided or "").strip()
    if candidate:
        return candidate
    for _ in range(10):
        generated = _new_repo_id()
        if not await repos_repo.exists(generated):
            return generated
    # 理论不可达：26 位随机空间下单日碰撞概率极低；兜底抛错避免无限循环
    raise ValidationError("无法生成唯一仓库标识，请稍后重试或显式指定 id", data={})


def _to_out(repo: Repo, passfile: str) -> RepoOut:
    """Repo → RepoOut（type 派生 + passfile_status 只读派生）。"""
    return RepoOut(
        id=repo.id,
        name=repo.name,
        type=repo.type,
        url=repo.url,
        host_tag=repo.host_tag,
        url_source=repo.url_source,
        host_tag_source=repo.host_tag_source,
        passfile_status=passfile,
        created_at=repo.created_at,
    )


# ---------------------------------------------------------------- A1 URL 严格校验（Q20）
#
# 依据 restic 0.19.1 各后端 ParseConfig 的官方语法逐一登记（源码核验，非猜测）：
# - s3/b2/local **保持原状不校验**（owner 裁定 Q20：既有形态零回归）；
# - sftp/azure/gs/rest/rclone 新增类型做语法校验；rclone 另加 remote 存在性校验
#   （``rclone listremotes`` 只读探测，remote 不在册 → 422，防"保存即失败"）。
# 校验只认**写入路径**（POST/PUT），env 同步（运维显式设置）不走此门。

# sftp 双形态（restic sftp.ParseConfig）：URL 形态支持端口/IPv6，scp 形态不支持端口。
# restic sftp 后端是 location.NoPassword + 外部 ssh 子进程（免密登录假设）——URL 里的
# 密码会被 scp 解析当作用户名的一部分静默误解，故显式拒绝并指向私钥挂载部署方式。
# user 段均不允许 ":"（密码内嵌检测 + 双形态一致）；scp 形态允许 user@domain@host
# （restic SplitN(host,"@",3) 支持的域限定用户形态）。
_SFTP_URL_FORM = re.compile(
    r"^sftp://(?P<user>[^\s@/:]+)@(?P<host>\[[^\s@/]+\]|[^\s@/:]+)(?::(?P<port>\d+))?/(?P<path>\S+)$"
)
_SFTP_SCP_FORM = re.compile(r"^sftp:(?P<user>[^\s:]+)@(?P<host>[^\s@/:]+):(?P<path>[^\s:]+)$")
# Azure 容器名规则（官方：3-63 位小写字母/数字/连字符，首尾字母数字）
_AZURE_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
# GCS 存储桶名（官方：3-63 位小写字母/数字/点/下划线/连字符；此处取中等强度子集）
_GCS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")


def _validate_repo_url_syntax(url: str) -> None:
    """按协议前缀做 URL 语法校验（纯语法；rclone 的 remote 存在性校验在调用方）。

    Raises:
        ValidationError: 语法非法（422，data 带 url 与可操作提示）。
    """
    stripped = url.strip()
    lowered = stripped.lower()
    if lowered.startswith("sftp:"):
        _validate_sftp_url(stripped)
    elif lowered.startswith("azure:"):
        _validate_azure_url(stripped)
    elif lowered.startswith("gs:"):
        _validate_gs_url(stripped)
    elif lowered.startswith("rest:"):
        _validate_rest_url(stripped)
    # s3:/b2:/本地路径：Q20 裁定不校验（零回归）；rclone: 语法见 _validate_rclone_url_syntax


def _validate_sftp_url(url: str) -> None:
    """sftp 双形态校验；拒密码内嵌（restic 无密码认证，走了也是静默误解）。"""
    if ":" in url[len("sftp:") :].split("@", 1)[0]:
        raise ValidationError(
            "sftp 地址不支持密码认证：restic 的 sftp 后端经系统 ssh 免密登录，"
            "URL 内密码会被当作用户名一部分静默误解。请挂载 SSH 私钥（见部署指南）",
            data={"url": url},
        )
    if _SFTP_URL_FORM.match(url) or _SFTP_SCP_FORM.match(url):
        return
    raise ValidationError(
        "sftp 地址应为 sftp:user@host:目录 或 sftp://user@host[:端口]/目录"
        "（目录必填；scp 形态不支持端口，需要端口请用 sftp:// 形态）",
        data={"url": url},
    )


def _validate_azure_url(url: str) -> None:
    """azure:container:/prefix 校验（restic azure.ParseConfig：冒号必填）。"""
    rest = url[len("azure:") :]
    container, sep, _prefix = rest.partition(":")
    if not sep or not _AZURE_CONTAINER_RE.match(container):
        raise ValidationError(
            "azure 地址应为 azure:容器名:/前缀（前缀可为空；容器名 3-63 位小写字母/数字/连字符）",
            data={"url": url},
        )


def _validate_gs_url(url: str) -> None:
    """gs:bucket:/prefix 校验（restic gs.ParseConfig：冒号必填；前缀是 gs: 不是 gcs:）。"""
    rest = url[len("gs:") :]
    bucket, sep, _prefix = rest.partition(":")
    if not sep or not _GCS_BUCKET_RE.match(bucket):
        raise ValidationError(
            "gcs 地址应为 gs:桶名:/前缀（restic 前缀是 gs:；前缀可为空，冒号必填）",
            data={"url": url},
        )


def _validate_rest_url(url: str) -> None:
    """rest:http(s)://... 校验；拒 user:pass@ 内嵌（env 凭据在 URL 无用户信息时才生效）。"""
    rest = url[len("rest:") :]
    if rest.startswith("http+unix://"):
        return  # 本地 socket 形态（rest:http+unix:///tmp/rest.socket:/repo/），路径由 restic 解析
    try:
        parts = urlsplit(rest)
    except ValueError as exc:
        raise ValidationError(f"rest 地址解析失败：{exc}", data={"url": url}) from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError(
            "rest 地址应为 rest:http://主机[:端口][/路径] 或 rest:https://...",
            data={"url": url},
        )
    if parts.username or parts.password:
        # restic rest 后端 ApplyEnvironment：URL 带用户信息时 RESTIC_REST_* 环境变量**不生效**；
        # 且密码会以明文落库（url 列不加密）→ 拒绝并指向加密凭据字段
        raise ValidationError(
            "rest 地址不支持内嵌用户密码：URL 带用户信息时 RESTIC_REST_USERNAME/PASSWORD "
            "环境变量将失效，且密码会明文存入 url 列。请只填主机地址，"
            "用户名/密码走下方凭据字段（加密存储）",
            data={"url": url},
        )


def _validate_rclone_url_syntax(url: str) -> str:
    """rclone:remote:path 语法校验；返回 remote 名（存在性校验由调用方异步执行）。"""
    spec = url[len("rclone:") :]
    remote, sep, path = spec.partition(":")
    if not sep or not remote or not path or any(ch.isspace() for ch in spec):
        raise ValidationError(
            "rclone 地址应为 rclone:远端名:路径（远端名须与宿主机 rclone config 中配置的一致；"
            "路径为桶内路径，如 rclone:myremote:mybucket/restic）",
            data={"url": url},
        )
    return remote


def _rclone_remote_of(url: str) -> str | None:
    """从 ``rclone:<remote>:<path>`` 宽松取出 remote 名；非 rclone 后端/语法不符返回 ``None``。

    **与校验版的区别**：本函数用于扫描**既有**数据（可能含历史脏值），故不抛异常；
    remote 段按**完整相等**比对（配合此处带尾冒号切分），杜绝 ``foo`` 误命中 ``foobar``。
    """
    text = (url or "").strip()
    if not text.lower().startswith("rclone:"):
        return None
    head, sep, _ = text[len("rclone:") :].partition(":")
    return head if sep and head else None


def _count_remote_usage(backed: Sequence[tuple[str, str, str]]) -> dict[str, int]:
    """按 remote 名聚合「直接引用它的仓库数」（含归档仓库，避免漏判）。"""
    counts: dict[str, int] = {}
    for _rid, _name, url in backed:
        remote = _rclone_remote_of(url)
        if remote:
            counts[remote] = counts.get(remote, 0) + 1
    return counts


async def _validate_repo_url(url: str, probe: RcloneProbe) -> None:
    """仓库 URL 完整校验（语法 + rclone remote 存在性）。空地址跳过（允许日后再补）。

    Raises:
        ValidationError: 语法非法或 rclone remote 不在册（422）。
    """
    stripped = (url or "").strip()
    if not stripped:
        return
    if stripped.lower().startswith("rclone:"):
        remote = _validate_rclone_url_syntax(stripped)
        remotes = await probe.list_remotes()
        if not remotes.available:
            # 探测不可用（缺二进制/超时/conf 读取失败）→ 放行语法正确的 URL，
            # 由 E29 连通性测试给出最终判定（原因明示，不静默）
            return
        if remote not in remotes.remotes:
            raise ValidationError(
                f"rclone 远端「{remote}」不在 rclone 配置中（可用远端："
                f"{', '.join(remotes.remotes) or '无'}）——请先在宿主机 rclone config 添加",
                data={"remote": remote, "available_remotes": list(remotes.remotes)},
            )
        return
    _validate_repo_url_syntax(stripped)


@router.get(
    "/repos",
    response_model=list[RepoOut],
    summary="R-023 仓库列表（切换器数据源）",
    dependencies=_PROTECTED,
)
async def list_repos(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> list[RepoOut]:
    """返回全部仓库（default 恒首位；含类型/来源/密码文件状态）。"""
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    settings = request.app.state.settings
    return [_to_out(repo, passfile_status(settings, repo.id)) for repo in await repos_repo.list_all()]


@router.post(
    "/repos",
    response_model=RepoOut,
    summary="R-023 新建仓库",
    dependencies=_PROTECTED,
)
async def create_repo(
    payload: RepoCreateRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoOut:
    """新建仓库（url/host_tag 可空；``source`` 恒 ui——env 只覆盖 default）。

    A1（Q20）：``url`` 非空时按协议前缀做严格校验（rclone 另查 remote 存在性）。
    """
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo_id = await _resolve_repo_id(repos_repo, payload.id)
    if payload.url:
        await _validate_repo_url(payload.url, request.app.state.rclone_probe)
    repo = await repos_repo.create(repo_id, payload.name, url=payload.url, host_tag=payload.host_tag)
    return _to_out(repo, passfile_status(request.app.state.settings, repo.id))


@router.get(
    "/repos/_new_id",
    response_model=RepoIdSuggestion,
    summary="A8 返回服务端生成的候选仓库标识（r<YYMMDD>-<6 位随机>）",
    dependencies=_PROTECTED,
)
async def suggest_repo_id() -> RepoIdSuggestion:
    """供新建仓库向导预填标识（对齐 Duplicati 新建备份自动起名体验）。

    不落库；用户可改可不改。留空提交时 ``POST /api/repos`` 仍会服务端生成（见 A8）。
    声明在 ``/repos/{repo_id}`` 之前，避免被路径参数捕获。
    """
    return RepoIdSuggestion(id=_new_repo_id())


@router.get(
    "/repos/{repo_id}",
    response_model=RepoOut,
    summary="R-023 仓库详情",
    dependencies=_PROTECTED,
)
async def get_repo(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoOut:
    """返回单仓库详情（不存在 404 REPO_NOT_FOUND）。"""
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    return _to_out(repo, passfile_status(request.app.state.settings, repo.id))


@router.put(
    "/repos/{repo_id}",
    response_model=RepoOut,
    summary="R-023 更新仓库（url/host_tag 被 env 覆盖时 409）",
    dependencies=_PROTECTED,
)
async def update_repo(
    repo_id: str,
    payload: RepoUpdateRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoOut:
    """更新仓库资料；``url``/``host_tag`` 被 env 覆盖的字段拒绝界面改（409 ENV_OVERRIDDEN）。

    A1（Q20）：``url`` 非空时按协议前缀做严格校验（rclone 另查 remote 存在性）——
    校验先于落库，坏 URL 不进 repos 表。
    """
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    if payload.url:
        await _validate_repo_url(payload.url, request.app.state.rclone_probe)
    repo = await repos_repo.update(
        repo_id, name=payload.name, url=payload.url, host_tag=payload.host_tag
    )
    invalidate_repo_status_cache()
    return _to_out(repo, passfile_status(request.app.state.settings, repo.id))


@router.delete(
    "/repos/{repo_id}",
    response_model=OkResponse,
    summary="R-023 删除仓库（默认归档；hard=1 彻底删；purge_disk=1 销毁数据；三门守卫）",
    dependencies=_PROTECTED,
)
async def delete_repo(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
    purge_disk: bool = False,
    hard: bool = False,
) -> OkResponse:
    """删除仓库，三条分支（《删除依赖与快照可用性保护方案》v1.2 裁决 D8/§5.1）：

    1. **默认 = 归档**（``deleted_at`` 置位）：行还在、**凭据与密码文件都保留**、
       磁盘数据一个字节不动 → 归档期内一键原样恢复，快照立即可读。列表不再显示，
       归档页可见。这正是「记录会过期、数据永不自动删」的落点。
    2. ``hard=1`` = 彻底删除行（+ 终态作业级联）：**不保留可恢复能力**，但磁盘数据
       仍原样留在原地（孤儿数据，供人工处置）；留不含凭据的墓碑。
    3. ``purge_disk=1`` = 销毁数据：先清空仓库目录**内容** + 删密码文件，再删行；
       与归档**互斥**（勾了就落不回归档），墓碑标 ``data_destroyed=1``。

    三门守卫（default 禁删 / 仍有 backup_tasks / 仍有活跃作业）对三条分支一视同仁，
    且**先于一切副作用**执行——校验拒绝时数据必须原封不动。

    仓库不存在 → 404 ``REPO_NOT_FOUND``（先于校验，避免为无效 target 落审计）。
    审计：归档 ``repo.archive``、彻底删 ``repo.delete``、销毁 ``repo.purge``。
    """
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    config_repo: ConfigRepo = request.app.state.config_repo
    # 存在性前置：仓库不存在直接 404，避免为「删了个不存在的仓库」落一条成功审计
    existing = await repos_repo.get(repo_id)
    if existing is None:
        raise RepoNotFoundError(repo_id)
    # 删除前置校验（default 禁删 + 无任务/无活跃作业 + 未被镜像引用）先于一切副作用——
    # purge_disk 的磁盘清理尤其不能跑到校验前面
    await repos_repo.ensure_deletable(repo_id)

    if not purge_disk and not hard:
        # ---- 分支 1：归档（可逆，零数据风险）----
        await repos_repo.archive(repo_id)
        await request.app.state.audit.record(
            AUDIT_REPO_ARCHIVE,
            target=repo_id,
            detail={"name": existing.name, "url": existing.url or ""},
            result="ok",
        )
        invalidate_repo_status_cache()
        invalidate_snapshot_catalog()
        return OkResponse()

    purged = await _purge_repo_disk(request, repo_id, existing.url or "") if purge_disk else False
    removed_logs = await repos_repo.hard_delete(repo_id, data_destroyed=purged)
    # 孤儿日志清理（口径同 retention：行没了，日志没有保留价值；缺失/失败静默跳过）
    if removed_logs:
        await asyncio.to_thread(_unlink_logs_sync, removed_logs)
    # 凭据键清理（镜像 rebuild 先例：跳过 env 来源与空值——env 键归 env 同步管）
    cleared = 0
    for row in await config_repo.list_all_raw():
        if not (row.key.startswith(f"cred.{repo_id}.") and row.source != "env" and row.value):
            continue
        await config_repo.delete(row.key)
        cleared += 1
    if cleared:
        logger.info("仓库 %s 已删除：级联清理凭据键 %d 个", repo_id, cleared)
    # 审计：只记计数与仓库名，不回显凭据（S3）。purge 入 S6 危险清单（不可逆销毁）。
    await request.app.state.audit.record(
        AUDIT_REPO_PURGE if purged else AUDIT_REPO_DELETE,
        target=repo_id,
        detail={
            "name": existing.name,
            "removed_logs": len(removed_logs),
            "cleared_credentials": cleared,
            "purged_disk": purged,
        },
        result="ok",
    )
    invalidate_repo_status_cache()
    # P0 L4-3：仓库行没了 → 该仓库的快照计数缓存随之失效（原先只清状态缓存）
    invalidate_snapshot_catalog()
    return OkResponse()


@router.get(
    "/archive/repos",
    response_model=ArchiveListOut,
    summary="归档页：仓库归档 + 墓碑清单（只读）",
    dependencies=_PROTECTED,
    tags=["repos"],
)
async def list_archive(request: Request, session: SessionContext = Depends(require_session)) -> ArchiveListOut:
    """返回「可恢复的归档仓库」与「只剩记录的墓碑」。

    刻意用 ``/archive/*`` 前缀而不是 ``/repos/archived``：``/repos/{repo_id}`` 已在
    前面声明，FastAPI 按声明顺序匹配，``archived`` 会被当成 repo_id 吃掉。
    """
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    settings = request.app.state.settings
    archived = await repos_repo.list_archived()
    tombstones = await repos_repo.list_tombstones()
    return ArchiveListOut(
        repos=[
            ArchivedRepoOut(
                repo_id=repo.id,
                name=repo.name,
                type=repo.type,
                url=repo.url,
                archived_at=repo.deleted_at or "",
                passfile_status=passfile_status(settings, repo.id),
            )
            for repo in archived
        ],
        tombstones=[
            RepoTombstoneOut(
                repo_id=str(row["repo_id"]),
                name=str(row["name"]),
                type=str(row["type"]),
                url_display=None if row["url_display"] is None else str(row["url_display"]),
                archived_at=str(row["archived_at"]),
                snapshot_count=None if row["snapshot_count"] is None else int(row["snapshot_count"]),
                data_destroyed=bool(row["data_destroyed"]),
                released_at=None if row["released_at"] is None else str(row["released_at"]),
            )
            for row in tombstones
        ],
    )


@router.post(
    "/archive/repos/{repo_id}/restore",
    response_model=OkResponse,
    summary="恢复归档仓库（撤销归档；数据与凭据从未被删，故即刻可用）",
    dependencies=_PROTECTED,
    tags=["repos"],
)
async def restore_archived_repo(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """把归档仓库还原为「在用」：``deleted_at`` 置空，列表与切换器立刻可见。"""
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    if repo.deleted_at is None:
        raise ValidationError("该仓库未归档，无需恢复", data={"repo_id": repo_id})
    await repos_repo.restore(repo_id)
    await request.app.state.audit.record(
        AUDIT_REPO_RESTORE, target=repo_id, detail={"name": repo.name}, result="ok"
    )
    invalidate_repo_status_cache()
    invalidate_snapshot_catalog()
    return OkResponse()


@router.post(
    "/archive/repos/{repo_id}/release",
    response_model=OkResponse,
    summary="释放归档仓库 ID（需输入仓库 id 确认；旧快照自此与本应用失去关联）",
    dependencies=_PROTECTED,
    tags=["repos"],
)
async def release_archived_repo(
    repo_id: str,
    payload: RepoReleaseRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """释放 ID：删 ``repos`` 行（该 id 立即可被新仓库复用）+ 留墓碑 + 清凭据键。

    **不动磁盘数据**（守 §6 红线：记录与数据分离）——数据仍可手工找回，只是本应用
    不再持有入口。确认门槛是「原样输入仓库 id」，避免误点。
    """
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    config_repo: ConfigRepo = request.app.state.config_repo
    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    if (payload.confirm_id or "").strip() != repo_id:
        raise ValidationError(
            "请输入仓库 id 以确认释放", data={"repo_id": repo_id}
        )
    removed_logs = await repos_repo.release_id(repo_id)
    if removed_logs:
        await asyncio.to_thread(_unlink_logs_sync, removed_logs)
    cleared = 0
    for row in await config_repo.list_all_raw():
        if not (row.key.startswith(f"cred.{repo_id}.") and row.source != "env" and row.value):
            continue
        await config_repo.delete(row.key)
        cleared += 1
    await request.app.state.audit.record(
        AUDIT_REPO_ID_RELEASE,
        target=repo_id,
        detail={"name": repo.name, "removed_logs": len(removed_logs), "cleared_credentials": cleared},
        result="ok",
    )
    invalidate_repo_status_cache()
    invalidate_snapshot_catalog()
    return OkResponse()


@router.delete(
    "/archive/tombstones/{repo_id}",
    response_model=OkResponse,
    summary="删除归档记录（墓碑）：只删记录，绝不删数据",
    dependencies=_PROTECTED,
    tags=["repos"],
)
async def delete_tombstone(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """删除墓碑行（「我连这里曾经有过什么也不想记」）。**不触碰任何数据**。"""
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    if not await repos_repo.delete_tombstone(repo_id):
        raise RepoNotFoundError(repo_id)
    return OkResponse()


async def _purge_repo_disk(request: Request, repo_id: str, url: str) -> bool:
    """清空仓库磁盘数据（delete 的 ``purge_disk``）：清空仓库目录内容 + 删密码文件。

    返回是否实际清空了数据目录（目录本不存在时为 False；密码文件仍会尝试清理）。
    调用方保证：删除前置校验已通过、DB 行尚未删——故这里的 422 一律「零损失」
    （校验/清理失败时仓库行与数据都还在，可安全重试）。

    - 远程后端/未配置地址/文件系统根/非目录 → 422（可操作提示，不静默）；
    - 清理失败（权限不足/文件被占用）→ 422 可操作提示，不 500。

    **保留目录本身、只清内容**（2026-10-22 owner 反馈）：整体删掉
    ``/home/123/qqq`` 后，同址新建还得先手动建文件夹；restic init 虽会
    MkdirAll，但挂载点/运维预置目录不该被连根拔掉。
    """
    local_path = _repo_local_path(url)
    if local_path is None:
        raise ValidationError(
            "该仓库未配置地址或后端不是本地文件系统（远程后端无法自动清空）。"
            "请手动清空后端存储后再删除，或换 URL 新建。",
            data={"url": url},
        )
    if _is_fs_root(local_path):
        raise ValidationError(f"仓库路径解析为文件系统根目录，拒绝清空：{local_path}")
    removed = False
    if local_path.exists():
        if not local_path.is_dir():
            raise ValidationError(f"仓库路径不是目录，拒绝清空：{local_path}")
        await _clear_dir_contents(local_path)
        removed = True
    # 密码文件随数据一并清除（purge 语义 = 该地址可干净重用）：删除动作收口在
    # `utils/passfile.remove_passfile`（唯一出口）——`RESTIC_PASSWORD_FILE` 全局共用
    # 密码文件时会拒绝删除，否则删一个仓库的密码文件等于毁掉全部仓库的可读性。
    remove_passfile(request.app.state.settings, repo_id)
    return removed


async def _clear_dir_contents(local_path: Path) -> None:
    """清空目录**内容**（子文件/子目录全部删除，目录本身保留）——purge/rebuild 共用。

    保留目录本身：同址新建/重建不必先手动建文件夹；restic init 本就 MkdirAll，
    但挂载点与运维预置目录不该被整体删掉（2026-10-22 owner 反馈）。

    调用方须先过 ``_is_fs_root`` 硬拒绝（清空根目录内容 = 灾难）。
    失败（权限不足/文件被占用/挂载点 EBUSY）→ ``ValidationError``（422 可操作
    提示，不 500）——两条调用路径（delete purge / rebuild）都要求「失败即停」。
    """

    def _clear() -> None:
        for child in local_path.iterdir():
            # 符号链接一律 unlink：rmtree 拒绝符号链接，且绝不跟随链接删目标
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    try:
        await asyncio.to_thread(_clear)
    except OSError as exc:
        raise ValidationError(
            f"仓库数据目录清理失败（权限不足或文件被占用）：{local_path}；"
            "请在宿主机对应挂载目录手动清理后重试"
        ) from exc


def _unlink_logs_sync(paths: list[str]) -> None:
    """同步删除孤儿任务日志文件（随仓库删除的终态作业；缺失/失败静默跳过）。"""
    for raw in paths:
        with contextlib.suppress(OSError):
            Path(raw).unlink()


@router.post(
    "/repos/{repo_id}/test",
    response_model=RepoTestResponse,
    summary="E29 仓库连通性测试（per-repo）",
    dependencies=_PROTECTED,
)
async def test_repo(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoTestResponse:
    """``restic cat config`` 探测连通性：成功回 ``{ok: true, latency_ms}``。

    失败**不抛错也不静默**：回 ``{ok: false, reason}``（锁残留/凭据缺失/仓库未初始化…）。
    """
    del session
    restic: ResticCmd = request.app.state.restic
    repos_repo: ReposRepo = request.app.state.repos_repo

    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    repo_url = repo.url or ""
    if not repo_url:
        await request.app.state.audit.record(
            AUDIT_REPO_TEST, target=repo_id, detail={"ok": False}, result="denied"
        )
        return RepoTestResponse(ok=False, latency_ms=0, reason="仓库地址未配置（请先填写仓库地址）")

    started = time.monotonic()
    probe = await restic.probe(
        "cat",
        repo_url=repo_url,
        repo_id=repo_id,
        args=WhitelistArgs(json_output=False, extra_positionals=("config",)),
        timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, REPO_PROBE_TIMEOUT_SECONDS)
        kind = failure_kind(probe.exit_code, probe.lines)
        await request.app.state.audit.record(
            AUDIT_REPO_TEST, target=repo_id, detail={"ok": False, "reason": reason}, result="denied"
        )
        return RepoTestResponse(ok=False, latency_ms=latency_ms, reason=reason, failure_kind=kind)

    await request.app.state.audit.record(
        AUDIT_REPO_TEST, target=repo_id, detail={"ok": True, "latency_ms": latency_ms}, result="ok"
    )
    return RepoTestResponse(ok=True, latency_ms=latency_ms)


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


@router.get(
    "/repos/{repo_id}/usage",
    response_model=RepoUsageOut,
    summary="P1 仓库占用明细（被镜像引用 + 快照总数）",
    dependencies=_PROTECTED,
    tags=["repos"],
)
async def repo_usage(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoUsageOut:
    """查询某仓库被谁占用（**只读，零副作用**），供删除确认门动态展示后果。

    - ``mirror_refs``：把本仓当镜像目的地的任务清单（口径同 :meth:`ReposRepo.list_mirror_refs`）；
    - ``snapshot_total``：仓库内快照**总条数**（走 ``SnapshotCatalog`` TTL 缓存，
      含无 tag 的手工快照）；``null`` = 此刻不可知，**不可当 0 展示**。
    """
    del session
    _validate_repo_id(repo_id)
    repos_repo: ReposRepo = request.app.state.repos_repo
    existing = await repos_repo.get(repo_id)
    if existing is None:
        raise RepoNotFoundError(repo_id)
    mirror_refs = await repos_repo.list_mirror_refs(repo_id)
    catalog = getattr(request.app.state, "snapshot_catalog", None)
    total: int | None = None
    if catalog is not None and (existing.url or "").strip():
        env = await request.app.state.restic.env_for(
            repo_url=(existing.url or "").strip(), repo_id=repo_id
        )
        tally = await catalog.snapshot_tally(
            restic=request.app.state.restic,
            parser=request.app.state.parser,
            repo_id=repo_id,
            repo_url=(existing.url or "").strip(),
            env=env,
        )
        if tally is not None:
            total = tally.total
    return RepoUsageOut(
        repo_id=repo_id,
        mirror_refs=[
            RepoUsageMirrorRef(id=tid, name=name) for tid, name in mirror_refs
        ],
        snapshot_total=total,
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
    plain = _generate_repo_password() if generated else (payload.password or "")

    try:
        _write_passfile(path, plain)
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
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)

    # 远程后端不支持自动清空
    local_path = _repo_local_path(repo.url or "")
    if local_path is None:
        raise ValidationError(
            "该仓库后端不是本地文件系统（远程后端无法自动清空）。请手动清空后端存储后删除仓库、换 URL 新建。",
            data={"url": repo.url},
        )

    # 运行中的任务冲突
    active = await request.app.state.job_repo.list_active(repo_id)
    if active:
        raise JobConflictError(data={"running_job_id": active[0].id})

    restic: ResticCmd = request.app.state.restic
    config_repo: ConfigRepo = request.app.state.config_repo
    audit = request.app.state.audit
    settings = request.app.state.settings

    # 新密码：自填或生成
    generated = not (payload.password or "").strip()
    plain = _generate_repo_password() if generated else (payload.password or "")

    # 1) 清空仓库数据目录内容（已确认是本地文件系统目录；保留目录本身，
    #    同址重建不必先手动建文件夹——restic init 会自行 MkdirAll）。
    #    **清空必须先于写新密码文件**（审查 🔴 修复，2026-10-22）：若先覆盖密码文件
    #    再清空，清空失败（EBUSY/权限 422）时旧密码已被覆盖且无回滚——盘上快照
    #    永久不可读，「自救」变「自毁」。调序后清空失败 = 零损失（什么都没动）；
    #    清空成功后密码文件/init 失败 = 旧数据已按确认销毁，状态自洽（空库待 re-init）。
    if _is_fs_root(local_path):
        raise ValidationError(f"仓库路径解析为文件系统根目录，拒绝清空：{local_path}")
    if local_path.exists():
        if not local_path.is_dir():
            raise ValidationError(f"仓库路径不是目录，拒绝清空：{local_path}")
        await _clear_dir_contents(local_path)

    # 2) 写新密码文件（数据已清空；此时失败 = 空库无密钥，按提示手动放置后重跑 rebuild 即可）
    path = passfile_path(settings, repo_id)
    try:
        _write_passfile(path, plain)
    except OSError as exc:
        raise ValidationError(
            f"密码文件写入失败（只读挂载或权限不足）：{path}；请在宿主机对应挂载目录手动放置该密码文件"
        ) from exc

    # 3) restic init（用新密码文件）
    fd, log_path = tempfile.mkstemp(prefix="restic-init-", suffix=".log")
    os.close(fd)
    exit_code = -1
    last_line = ""
    try:
        env = await restic.env_for(repo_url=repo.url, repo_id=repo_id)
        argv = restic.build("init", args=WhitelistArgs(json_output=False), env_extras={})
        proc = await restic.spawn(argv, log_path, env=env)
        async for line in proc.lines():
            last_line = line
        try:
            exit_code = await asyncio.wait_for(proc.wait(), timeout=REPO_INIT_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            exit_code = -1
    finally:
        with contextlib.suppress(OSError):
            os.unlink(log_path)
    if exit_code != 0:
        reason = failure_reason(exit_code, [last_line]) if exit_code != -1 else "初始化超时"
        logger.warning("仓库 %s 重建初始化失败：%s", repo_id, reason)
        raise ResticFailedError(f"重建初始化失败：{reason}", exit_code=exit_code)

    # 3.1) key 中性化：``keys/`` 是仓库里唯一的明文文件，含 username/hostname/created
    #      （KDF 参数无法加密——口令派生的硬约束；但身份标注可换）。init 后立刻把唯一
    #      key 换成中性标识并移除旧 key，避免云端/存储侧推断真实机器与账号。
    #      失败只记日志、不阻塞重建（仓库已可用，旧 key 仍有效）。
    await neutralize_repo_key(
        restic,
        repo_id=repo_id,
        env=env,
        password_file=path,
    )

    # 4) default 凭据根：清除旧 enc:v2 凭据（旧密钥已丢失，无法解密）
    crypto = request.app.state.encryption.crypto
    cleared = 0
    for row in await config_repo.list_all_raw():
        if not (row.key.startswith(f"cred.{repo_id}.") and row.source != "env" and row.value):
            continue
        if not crypto.is_encrypted(row.value):
            continue
        await config_repo.delete(row.key)
        cleared += 1

    await audit.record(
        AUDIT_REPO_REBUILD,
        target=repo_id,
        detail={"generated": generated, "cleared_credentials": cleared},
        result="ok",
    )
    invalidate_repo_status_cache()
    return RepoPasswordResponse(
        ok=True,
        generated=generated,
        password=plain if generated else None,
        path=str(path),
    )


# 生成用字符集（四类；剔除易混淆字符 0/O、1/l/I，与前端 passwordStrength 同口径）
_PASSWORD_CLASSES: tuple[str, ...] = (
    "abcdefghjkmnpqrstuvwxyz",
    "ABCDEFGHJKMNPQRSTUVWXYZ",
    "23456789",
    "!@#$%^&*-_=+",
)


def _generate_repo_password(length: int = REPO_PASSWORD_LENGTH) -> str:
    """生成定长强随机仓库密码（每类字符至少一个，再整体洗牌）。

    长度取 ``REPO_PASSWORD_LENGTH``（32），与前端 ``utils/password.ts`` 的
    ``generatePassword()`` 同一口径。历史上前后端位数曾不一致（后端
    ``token_urlsafe(32)``≈43 位 vs 前端 20 位），2026-10-27 起全站统一 32 位。
    """
    rng = secrets.SystemRandom()
    chars = [rng.choice(cls) for cls in _PASSWORD_CLASSES]
    all_chars = "".join(_PASSWORD_CLASSES)
    while len(chars) < max(length, len(_PASSWORD_CLASSES)):
        chars.append(rng.choice(all_chars))
    rng.shuffle(chars)
    return "".join(chars)


def _write_passfile(path: Path, secret: str) -> None:
    """原子写密码文件（同目录临时文件 + rename；权限 0600）。

    不追加尾随换行：restic 读取时会 TrimSpace，原样落盘可保证「写入即所用」。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(secret, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
    cached = _status_cache.get(repo_id)
    if cached is not None and time.monotonic() - cached[0] < REPO_STATUS_CACHE_SECONDS:
        return cached[1]

    async with _status_lock:
        cached = _status_cache.get(repo_id)  # 双检：等锁期间可能已被别的请求刷新
        if cached is not None and time.monotonic() - cached[0] < REPO_STATUS_CACHE_SECONDS:
            return cached[1]
        status = await _probe_repo_status(request, repo_id)
        _status_cache[repo_id] = (time.monotonic(), status)
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
            probe_error=failure_reason(probe.exit_code, probe.lines, KEY_OPERATION_TIMEOUT_SECONDS),
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


def invalidate_repo_status_cache() -> None:
    """清除 E30 的进程内 TTL 缓存（全仓库；配置写入/任务终态时调用）。

    调用时机：仓库资料更新（url/凭据变了）、任务终态（init/unlock 会改变仓库可连性，
    由 JobRunner 的 ``on_finish`` 回调触发）。无锁——字典赋空是原子的。

    P1：连带清快照目录缓存——backup 终态会新增快照，任务卡片的 ``snapshot_count``
    不该继续吃旧快照列表（TTL 之外的即时失效）。
    """
    _status_cache.clear()
    invalidate_snapshot_catalog()


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
            last_error=failure_reason(probe.exit_code, probe.lines, REPO_PROBE_TIMEOUT_SECONDS),
            failure_kind=failure_kind(probe.exit_code, probe.lines),
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
