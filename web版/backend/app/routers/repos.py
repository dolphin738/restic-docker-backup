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

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_REPO_PASSWORD_SET,
    AUDIT_REPO_TEST,
    REPO_ID_MAX_LENGTH,
    REPO_ID_PATTERN,
    REPO_PROBE_TIMEOUT_SECONDS,
    REPO_STATS_TIMEOUT_SECONDS,
    REPO_STATUS_CACHE_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import Repo, ReposRepo
from app.errors import RepoNotFoundError, ValidationError
from app.schemas.api_schemas import (
    OkResponse,
    RepoCapacity,
    RepoCreateRequest,
    RepoOut,
    RepoPasswordRequest,
    RepoPasswordResponse,
    RepoStatusResponse,
    RepoTestResponse,
    RepoUpdateRequest,
)
from app.services.env_sync import load_credentials, passfile_path, passfile_status
from app.services.restic import ResticCmd, WhitelistArgs, failure_reason

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
    """新建仓库（url/host_tag 可空；``source`` 恒 ui——env 只覆盖 default）。"""
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.create(payload.id, payload.name, url=payload.url, host_tag=payload.host_tag)
    return _to_out(repo, passfile_status(request.app.state.settings, repo.id))


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
    """更新仓库资料；``url``/``host_tag`` 被 env 覆盖的字段拒绝界面改（409 ENV_OVERRIDDEN）。"""
    del session
    repos_repo: ReposRepo = request.app.state.repos_repo
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
        await request.app.state.audit.record(
            AUDIT_REPO_TEST, target=repo_id, detail={"ok": False, "reason": reason}, result="denied"
        )
        return RepoTestResponse(ok=False, latency_ms=latency_ms, reason=reason)

    await request.app.state.audit.record(
        AUDIT_REPO_TEST, target=repo_id, detail={"ok": True, "latency_ms": latency_ms}, result="ok"
    )
    return RepoTestResponse(ok=True, latency_ms=latency_ms)


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
    """首次写入仓库密码文件（``<passfile_root>/<repo_id>/password``，0600 原子写）。

    - ``password`` 为空 → 后端生成强随机密码并**一次性回显**（不落库、不落日志、不进审计）；
    - ``password`` 非空 → 按用户自填写入（响应不回显，用户已知）。

    **只在文件不存在时允许**：该密码既是 restic 仓库密钥，也是 R-025 凭据加密的密钥源
    （HKDF info 混入 repo_id）——覆盖已存在的文件会让仓库不可读、已加密凭据全部失效。
    """
    del session
    _validate_repo_id(repo_id)
    repos_repo: ReposRepo = request.app.state.repos_repo
    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)

    path = passfile_path(request.app.state.settings, repo_id)
    if path.exists():
        raise ValidationError(
            f"仓库密码文件已存在（{path}）——密码既是仓库密钥也是凭据加密密钥源，"
            "覆盖会使仓库不可读且已加密凭据失效；如确需更换，请在宿主机挂载点手动操作"
        )

    generated = not (payload.password or "").strip()
    plain = secrets.token_urlsafe(32) if generated else (payload.password or "")

    try:
        _write_passfile(path, plain)
    except OSError as exc:
        raise ValidationError(
            f"密码文件写入失败（只读挂载或权限不足）：{path}；请在宿主机对应挂载目录手动放置该密码文件"
        ) from exc

    # 审计只记动作与是否生成，明文绝不入审计/日志（红线 S3）
    await request.app.state.audit.record(
        AUDIT_REPO_PASSWORD_SET, target=repo_id, detail={"generated": generated}, result="ok"
    )
    invalidate_repo_status_cache()
    return RepoPasswordResponse(
        ok=True,
        generated=generated,
        password=plain if generated else None,
        path=str(path),
    )


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
