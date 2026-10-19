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
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_REPO_PASSWORD_SET,
    AUDIT_REPO_TEST,
    REPO_ID_MAX_LENGTH,
    REPO_ID_PATTERN,
    REPO_PASSWORD_LENGTH,
    REPO_PROBE_TIMEOUT_SECONDS,
    REPO_STATS_TIMEOUT_SECONDS,
    REPO_STATUS_CACHE_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import Repo, ReposRepo
from app.errors import RepoNotFoundError, ValidationError
from app.schemas.api_schemas import (
    OkResponse,
    RcloneRemotesOut,
    RepoCapacity,
    RepoCreateRequest,
    RepoIdSuggestion,
    RepoOut,
    RepoPasswordRequest,
    RepoPasswordResponse,
    RepoStatusResponse,
    RepoTestResponse,
    RepoUpdateRequest,
)
from app.services.env_sync import load_credentials, passfile_path, passfile_status
from app.services.rclone import RcloneProbe
from app.services.restic import ResticCmd, WhitelistArgs, failure_kind, failure_reason
from app.utils.ulid import new_ulid

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


# ---------------------------------------------------------------- A8 仓库标识自动生成
#
# owner 2026-10-17 07:37 立项：POST /repos 的 id 由必填改为可选，缺省服务端生成
# ``repo-<小写 ULID>``（26 位、全局唯一、按时间有序，天然满足 REPO_ID_PATTERN 与
# 64 长度上限）。ULID 小写化仍保持时间序可比较（同集合内大小写一致即可）。
# 落库前 exists 查重 + 重试（碰撞概率极低，重试只是形式化兜底）。

def _new_repo_id() -> str:
    """生成一个候选仓库标识（``repo-<小写 ULID>``）。"""
    return f"repo-{new_ulid().lower()}"


async def _resolve_repo_id(repos_repo: ReposRepo, provided: str | None) -> str:
    """A8：解析新建仓库的标识。

    - 显式提供（非空）→ 原样返回，落库时再由 ``ReposRepo.create`` 做形态 + 存在性校验；
    - 留空 → 服务端生成 ``repo-<小写 ULID>``，生成前 exists 查重，碰撞重试（上限 10 次）。
    """
    candidate = (provided or "").strip()
    if candidate:
        return candidate
    for _ in range(10):
        generated = _new_repo_id()
        if not await repos_repo.exists(generated):
            return generated
    # 理论不可达：ULID 碰撞概率极低；兜底抛错避免无限循环
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
    summary="A8 返回服务端生成的候选仓库标识（repo-<小写 ULID>）",
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
    summary="R-023 删除仓库（default 禁删；有任务/历史 409）",
    dependencies=_PROTECTED,
)
async def delete_repo(
    repo_id: str,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """删除仓库：default 不可删；仓库下仍有 backup_tasks 或 jobs 时 409（先清理再删）。"""
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    await repos_repo.delete(repo_id)
    return OkResponse()


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
    config_repo: ConfigRepo = request.app.state.config_repo
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

    argv = restic.build("cat", args=WhitelistArgs(json_output=False, extra_positionals=("config",)))
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )

    started = time.monotonic()
    probe = await restic.run_json(argv, env=env, timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS)
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


# 生成用字符集（四类；剔除易混淆字符 0/O、1/l/I，与前端 passwordStrength 同口径）
_PASSWORD_CLASSES: tuple[str, ...] = (
    "abcdefghjkmnpqrstuvwxyz",
    "ABCDEFGHJKMNPQRSTUVWXYZ",
    "23456789",
    "!@#$%^&*-_=+",
)


def _generate_repo_password(length: int = REPO_PASSWORD_LENGTH) -> str:
    """生成定长强随机仓库密码（每类字符至少一个，再整体洗牌）。

    长度取 ``REPO_PASSWORD_LENGTH``，与前端「生成强随机密码」同一口径——两处位数
    曾不一致（后端 token_urlsafe(32)≈43 位 vs 前端 20 位），用户可见。
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


def invalidate_repo_status_cache() -> None:
    """清除 E30 的进程内 TTL 缓存（全仓库；配置写入/任务终态时调用）。

    调用时机：仓库资料更新（url/凭据变了）、任务终态（init/unlock 会改变仓库可连性，
    由 JobRunner 的 ``on_finish`` 回调触发）。无锁——字典赋空是原子的。
    """
    _status_cache.clear()


async def _probe_repo_status(request: Request, repo_id: str) -> RepoStatusResponse:
    """执行一次仓库探测（E30 的未缓存实现）。"""
    restic: ResticCmd = request.app.state.restic
    parser = request.app.state.parser
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo

    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    if not repo.url:
        return RepoStatusResponse(
            configured=False, connected=False, locked=False, last_error="仓库地址未配置（请先填写仓库地址）"
        )

    env = restic.build_env(
        repo_url=repo.url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
    argv = restic.build("cat", args=WhitelistArgs(json_output=False, extra_positionals=("config",)))
    probe = await restic.run_json(argv, env=env, timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS)

    if probe.exit_code != 0:
        return RepoStatusResponse(
            connected=False,
            locked=_looks_locked(probe.lines),
            last_error=failure_reason(probe.exit_code, probe.lines, REPO_PROBE_TIMEOUT_SECONDS),
            failure_kind=failure_kind(probe.exit_code, probe.lines),
        )

    return RepoStatusResponse(connected=True, locked=False, capacity=await _read_capacity(restic, parser, env))


async def _read_capacity(restic: ResticCmd, parser: Any, env: dict[str, str]) -> RepoCapacity:
    """``restic stats --mode raw-data --json`` 读取仓库容量（失败返回空容量，不影响 connected）。"""
    argv = restic.build("stats", args=WhitelistArgs(json_output=True, stats_mode="raw-data"))
    probe = await restic.run_json(argv, env=env, timeout_seconds=REPO_STATS_TIMEOUT_SECONDS)
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
