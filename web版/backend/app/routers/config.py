"""配置路由：E06 读取 / E07 写入 / E29 仓库测试连接 / E30 仓库状态（§4.3 键表）。

- 敏感项（``cred.*`` 与 ``dump.password``）只回 ``has_value`` + ``source``，不回明文；
- ``source='env'`` 的键拒绝界面写入 → 409 ``ENV_OVERRIDDEN``（由 :class:`ConfigRepo` 保证）；
- ``passfile.status`` 为**只读派生**（密码文件是否存在），不落库（§4.3）；
- E29/E30 是**探测类**端点：失败不静默——原因文案由 :func:`failure_reason` 统一生成
  （锁残留 / 凭据缺失 / 仓库未初始化 / 网络不可达，§5.1 E29「禁止静默」）。
"""

from __future__ import annotations

import asyncio
import json
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
    BEHAVIOR_AUTO_INIT_KEY,
    BEHAVIOR_AUTO_UNLOCK_KEY,
    BEHAVIOR_CHECK_ENABLED_KEY,
    BEHAVIOR_CHECK_SAMPLE_RATIO_KEY,
    BEHAVIOR_MONTHLY_FULL_DAY_KEY,
    CRED_ENCRYPTION_KEY,
    DUMP_DATABASES_KEY,
    DUMP_ENABLED_KEY,
    DUMP_ENGINES,
    DUMP_PORT_KEY,
    DUMP_RETENTION_DAYS_KEY,
    DUMP_TYPE_KEY,
    MONTHLY_FULL_DAY_MAX,
    MONTHLY_FULL_DAY_MIN,
    REPO_PROBE_TIMEOUT_SECONDS,
    REPO_STATS_TIMEOUT_SECONDS,
    REPO_STATUS_CACHE_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import CredentialKeyUnavailableError, ValidationError
from app.schemas.api_schemas import (
    ConfigItem,
    ConfigPatchRequest,
    ConfigResponse,
    OkResponse,
    RepoCapacity,
    RepoStatusResponse,
    RepoTestResponse,
)
from app.services.crypto import EncryptionState
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

# 0/1 开关键（批次 B 批次面：behavior.* 三开关 + dump.enabled；R-025：凭据加密开关）
_BOOL_KEYS: frozenset[str] = frozenset(
    {
        BEHAVIOR_AUTO_INIT_KEY,
        BEHAVIOR_AUTO_UNLOCK_KEY,
        BEHAVIOR_CHECK_ENABLED_KEY,
        DUMP_ENABLED_KEY,
        CRED_ENCRYPTION_KEY,
    }
)


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
    """批量写入配置项；命中 env 覆盖键时整体拒绝（409 ENV_OVERRIDDEN，不产生部分写入）。

    R-025：patch 含 ``security.cred_encryption`` 时单独处理开关（先迁移后落键 /
    先落键后迁移的崩溃安全序见 :meth:`ConfigRepo.migrate_encryption` 与
    ``_apply_cred_encryption_flag``）；含敏感键而密码文件缺失时前置 422，
    守住「不产生部分写入」契约。
    """
    del session
    if not payload.patch:
        raise ValidationError("patch 不能为空")

    illegal = [key for key in payload.patch if not _KEY_PATTERN.match(key)]
    if illegal:
        raise ValidationError("配置键名非法", data={"illegal": illegal})

    # 键级校验钩子（批次 B）：behavior.*/dump.* 值域前置 422——坏值落库会让
    # check 域/ dump 域在运行期静默降级，必须在写入侧拒绝（与 repo.type 只读键
    # 同一机制位；值域单一真源为 config 运行期读取方：scheduler / dump / job_runner）。
    _validate_patch_values(payload.patch)

    repo = request.app.state.config_repo
    encryption: EncryptionState = request.app.state.encryption

    # R-025 开关先 pop 出来（其余键仍走 set_many，env 覆盖整体拒绝不变）；
    # 所有可能 422 的预检都在任何写入之前——守住「不产生部分写入」契约
    patch = dict(payload.patch)
    flag_in_patch = CRED_ENCRYPTION_KEY in patch
    flag_value = patch.pop(CRED_ENCRYPTION_KEY) if flag_in_patch else None
    enable = flag_in_patch and (flag_value or "1").strip() != "0"

    if enable and not encryption.crypto.available():
        raise CredentialKeyUnavailableError(str(encryption.crypto.passfile()))
    if (
        encryption.enabled
        and patch
        and any(ConfigRepo.is_sensitive(key) for key in patch)
        and not encryption.crypto.available()
    ):
        raise CredentialKeyUnavailableError(str(encryption.crypto.passfile()))

    # set_many 的 env 覆盖整体拒绝必须先于开关落键/迁移，否则拒绝时开关已改写（半写）
    await repo.set_many(patch)
    # E30 有 30s 进程内 TTL 缓存：配置刚保存（尤其 repo.url/凭据）时必须失效，
    # 否则用户保存后点「刷新」拿到的仍是旧状态，误以为"保存了也没连上"。
    invalidate_repo_status_cache()

    cred_migrated = 0
    if flag_in_patch:
        cred_migrated = await _apply_cred_encryption_flag(repo, encryption, flag_value)

    keys = sorted(payload.patch.keys())
    detail: dict[str, object] = {"keys": keys, "count": len(keys)}
    if cred_migrated:
        detail["cred_migrated"] = cred_migrated
    await request.app.state.audit.record(
        AUDIT_CONFIG_UPDATE,
        target="config",
        detail=detail,
        result="ok",
    )
    return OkResponse()


async def _apply_cred_encryption_flag(
    repo: ConfigRepo,
    encryption: EncryptionState,
    flag_value: str | None,
) -> int:
    """应用凭据加密开关（R-025）：返回存量搬运键数（审计用，不含任何明文）。

    崩溃安全序（读侧恒按 ``enc:v1:`` 前缀判定，与开关无关，故任一中断点均可读）：
    - **启用**：密码文件缺失 → 422 前置拒绝；否则先落开关再明文→密文搬运
     （中断残留明文，重跑即收敛）；
    - **关闭**：先密文→明文搬运再落开关（中断时开关仍开、新写仍加密，重跑即收敛）；
    - None（清除）= 缺省 = 开。
    """
    enable = (flag_value or "1").strip() != "0"
    if enable:
        if not encryption.crypto.available():
            raise CredentialKeyUnavailableError(str(encryption.crypto.passfile()))
        if not encryption.enabled:
            await repo.set(CRED_ENCRYPTION_KEY, "1")
            encryption.enabled = True
        # 已开启也跑一次幂等迁移：愈合上次中断残留的明文（X6 类中断恢复思路）
        return await repo.migrate_encryption(encrypt=True)
    if encryption.enabled:
        migrated = await repo.migrate_encryption(encrypt=False)
        await repo.set(CRED_ENCRYPTION_KEY, "0")
        encryption.enabled = False
        return migrated
    await repo.set(CRED_ENCRYPTION_KEY, "0")
    return 0


def invalidate_repo_status_cache() -> None:
    """清除 E30 的进程内 TTL 缓存。

    调用时机：E07 配置写入（repo.url / 凭据变了）、任务终态（init/unlock 会改变
    仓库可连性，由 JobRunner 的 ``on_finish`` 回调触发）。本函数无锁——赋值为
    ``None`` 是原子的，最多让一次并发请求多做一次探测，无正确性问题。
    """
    global _status_cache
    _status_cache = None


def _validate_patch_values(patch: dict[str, str | None]) -> None:
    """E07 键级校验钩子（批次 B）：behavior.* / dump.* 的值域前置校验。

    值为 ``None``（清除语义）一律放行；``dump.password`` 为三态写口径
    （None=不变 / ""=清除 / 非空=设置，读侧经 SENSITIVE_EXACT_KEYS 只回 has_value），
    明文不校验格式、原样存储（S3：仅经环境通道消费，argv/日志/审计零出现）。

    Raises:
        ValidationError: 任一受控键的值越出值域（422，整体拒绝不产生部分写入）。
    """
    for key, raw in patch.items():
        if raw is None:
            continue
        value = raw.strip()
        if key in _BOOL_KEYS:
            if value not in ("0", "1"):
                raise ValidationError(f"{key} 仅接受 0/1", data={"key": key, "value": raw})
        elif key == BEHAVIOR_CHECK_SAMPLE_RATIO_KEY:
            try:
                ratio = float(value)
            except ValueError as exc:
                raise ValidationError(
                    f"{key} 须为 [0,1] 内的数字", data={"key": key, "value": raw}
                ) from exc
            if not 0.0 <= ratio <= 1.0:
                raise ValidationError(f"{key} 须在 [0,1] 内（0=不抽样）", data={"key": key, "value": raw})
        elif key == BEHAVIOR_MONTHLY_FULL_DAY_KEY:
            if not value.isdigit() or not (MONTHLY_FULL_DAY_MIN <= int(value) <= MONTHLY_FULL_DAY_MAX):
                raise ValidationError(
                    f"{key} 须为 {MONTHLY_FULL_DAY_MIN}~{MONTHLY_FULL_DAY_MAX} 的整数（规避月末不存在日）",
                    data={"key": key, "value": raw},
                )
        elif key == DUMP_TYPE_KEY:
            if value not in DUMP_ENGINES:
                raise ValidationError(
                    f"{key} 仅支持 " + "/".join(DUMP_ENGINES),
                    data={"key": key, "value": raw, "allowed": list(DUMP_ENGINES)},
                )
        elif key == DUMP_PORT_KEY:
            if not value.isdigit() or not (1 <= int(value) <= 65535):
                raise ValidationError(f"{key} 须为 1~65535 的整数", data={"key": key, "value": raw})
        elif key == DUMP_RETENTION_DAYS_KEY:
            if not value.isdigit():
                raise ValidationError(f"{key} 须为非负整数（0=不启用清理）", data={"key": key, "value": raw})
        elif key == DUMP_DATABASES_KEY:
            databases = _parse_databases_for_validation(value)
            if databases is None:
                raise ValidationError(
                    f"{key} 须为非空 JSON 字符串数组", data={"key": key, "value": raw[:200]}
                )


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
            configured=False, connected=False, locked=False, last_error="仓库地址未配置（请先填写 repo.url）"
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


def _parse_databases_for_validation(raw: str) -> list[str] | None:
    """校验用：解析 ``dump.databases`` JSON 字符串数组；非法/为空返回 None。"""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    items = [str(item).strip() for item in payload if isinstance(item, str) and str(item).strip()]
    return items or None


def _as_optional_int(value: Any) -> int | None:
    """把 restic JSON 字段安全地转为可选整数。"""
    return int(value) if isinstance(value, int | float) else None
