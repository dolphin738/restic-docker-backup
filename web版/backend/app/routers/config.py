"""配置路由：E06 读取 / E07 写入 / E29 仓库测试连接 / E30 仓库状态（§4.3 键表）。

- 敏感项（``cred.*`` 与 ``dump.password``）只回 ``has_value`` + ``source``，不回明文；
- ``source='env'`` 的键拒绝界面写入 → 409 ``ENV_OVERRIDDEN``（由 :class:`ConfigRepo` 保证）；
- ``passfile.status`` 为**只读派生**（密码文件是否存在），不落库（§4.3）；
- E29/E30 是**探测类**端点：失败不静默——原因文案由 :func:`failure_reason` 统一生成
  （锁残留 / 凭据缺失 / 仓库未初始化 / 网络不可达，§5.1 E29「禁止静默」）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_CONFIG_UPDATE,
    AUDIT_REPO_TEST,
    REPO_PROBE_TIMEOUT_SECONDS,
    REPO_STATS_TIMEOUT_SECONDS,
    REPO_STATUS_CACHE_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import ValidationError
from app.schemas.api_schemas import (
    ConfigItem,
    ConfigPatchRequest,
    ConfigResponse,
    OkResponse,
    RepoCapacity,
    RepoStatusResponse,
    RepoTestResponse,
)
from app.services.env_sync import load_credentials, passfile_status
from app.services.restic import ResticCmd, WhitelistArgs, failure_reason

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# 键名形态白名单：小写字母开头，仅含小写字母/数字/点/下划线/连字符/星号（防污染键空间）
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9.*_-]{0,63}$")

# E30 进程内 TTL 缓存：仪表盘轮询不打爆仓库（单进程单仓库，无需跨实例一致性）
_status_cache: tuple[float, RepoStatusResponse] | None = None
_status_lock = asyncio.Lock()

# 锁残留关键词（从 cat config 的失败输出里识别，零额外 restic 调用）
_LOCK_KEYWORDS: tuple[str, ...] = ("already locked", "unable to create lock", "remove stale locks")


@router.get(
    "/config",
    response_model=ConfigResponse,
    summary="E06 读取配置",
    dependencies=_PROTECTED,
)
async def get_config(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> ConfigResponse:
    """返回全部配置项（敏感项脱敏）与仓库密码文件挂载状态。"""
    del session
    settings = request.app.state.settings
    repo = request.app.state.config_repo
    rows = await repo.list_all()
    items = [ConfigItem(**repo.mask(row)) for row in rows]
    return ConfigResponse(items=items, passfile_status=passfile_status(settings))


@router.put(
    "/config",
    response_model=OkResponse,
    summary="E07 写入配置",
    dependencies=_PROTECTED,
)
async def put_config(
    payload: ConfigPatchRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """批量写入配置项；命中 env 覆盖键时整体拒绝（409 ENV_OVERRIDDEN，不产生部分写入）。"""
    del session
    if not payload.patch:
        raise ValidationError("patch 不能为空")

    illegal = [key for key in payload.patch if not _KEY_PATTERN.match(key)]
    if illegal:
        raise ValidationError("配置键名非法", data={"illegal": illegal})

    repo = request.app.state.config_repo
    await repo.set_many(payload.patch)

    keys = sorted(payload.patch.keys())
    await request.app.state.audit.record(
        AUDIT_CONFIG_UPDATE,
        target="config",
        detail={"keys": keys, "count": len(keys)},
        result="ok",
    )
    return OkResponse()


@router.post(
    "/repo/test",
    response_model=RepoTestResponse,
    summary="E29 仓库连通性测试",
    dependencies=_PROTECTED,
)
async def test_repo(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoTestResponse:
    """``restic cat config`` 探测连通性：成功回 ``{ok: true, latency_ms}``。

    失败**不抛错也不静默**：回 ``{ok: false, reason}``（锁残留/凭据缺失/仓库未初始化…）。
    """
    del session
    restic: ResticCmd = request.app.state.restic
    config_repo: ConfigRepo = request.app.state.config_repo

    repo_url = (await config_repo.get_value("repo.url")) or ""
    if not repo_url:
        await request.app.state.audit.record(
            AUDIT_REPO_TEST, target="repo.url", detail={"ok": False}, result="denied"
        )
        return RepoTestResponse(ok=False, latency_ms=0, reason="仓库地址未配置（请先填写 repo.url）")

    argv = restic.build("cat", args=WhitelistArgs(json_output=False, extra_positionals=("config",)))
    env = restic.build_env(repo_url=repo_url, env_extras=await load_credentials(config_repo))

    started = time.monotonic()
    probe = await restic.run_json(argv, env=env, timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS)
    latency_ms = int((time.monotonic() - started) * 1000)

    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, REPO_PROBE_TIMEOUT_SECONDS)
        logger.warning("仓库连通性测试失败：%s", reason)
        await request.app.state.audit.record(
            AUDIT_REPO_TEST, target=repo_url, detail={"ok": False, "reason": reason}, result="denied"
        )
        return RepoTestResponse(ok=False, latency_ms=latency_ms, reason=reason)

    await request.app.state.audit.record(
        AUDIT_REPO_TEST, target=repo_url, detail={"ok": True, "latency_ms": latency_ms}, result="ok"
    )
    return RepoTestResponse(ok=True, latency_ms=latency_ms)


@router.get(
    "/repo/status",
    response_model=RepoStatusResponse,
    summary="E30 仓库状态",
    dependencies=_PROTECTED,
)
async def repo_status(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> RepoStatusResponse:
    """仓库状态卡数据源：``{connected, locked, capacity, last_error}``。

    成本可控三招（文档未定义，自定）：
    1. 结果走 ``REPO_STATUS_CACHE_SECONDS`` 秒进程内 TTL 缓存，仪表盘轮询不重复打仓库；
    2. ``locked`` **不额外调用** ``list locks``，而是从 ``cat config`` 的失败输出关键词识别；
    3. 容量探测（``stats --mode raw-data``）**仅在 connected 为真时**执行，失败只留空容量、
       不影响 connected 判定。
    """
    global _status_cache  # noqa: PLW0603 - 单进程 TTL 缓存，见模块 docstring

    cached = _status_cache
    if cached is not None and time.monotonic() - cached[0] < REPO_STATUS_CACHE_SECONDS:
        return cached[1]

    async with _status_lock:
        cached = _status_cache  # 双检：等锁期间可能已被别的请求刷新
        if cached is not None and time.monotonic() - cached[0] < REPO_STATUS_CACHE_SECONDS:
            return cached[1]
        status = await _probe_repo_status(request)
        _status_cache = (time.monotonic(), status)
        return status


# ---------------------------------------------------------------- 内部实现

async def _probe_repo_status(request: Request) -> RepoStatusResponse:
    """执行一次仓库探测（E30 的未缓存实现）。"""
    restic: ResticCmd = request.app.state.restic
    parser = request.app.state.parser
    config_repo: ConfigRepo = request.app.state.config_repo

    repo_url = (await config_repo.get_value("repo.url")) or ""
    if not repo_url:
        return RepoStatusResponse(
            connected=False, locked=False, last_error="仓库地址未配置（请先填写 repo.url）"
        )

    env = restic.build_env(repo_url=repo_url, env_extras=await load_credentials(config_repo))
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
        logger.warning(
            "仓库容量探测失败（不影响连通性判定）：%s",
            failure_reason(probe.exit_code, probe.lines, REPO_STATS_TIMEOUT_SECONDS),
        )
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
    return int(value) if isinstance(value, (int, float)) else None
