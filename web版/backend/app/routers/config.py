"""配置路由：E06 读取 / E07 写入 / E29 仓库测试连接 / E30 仓库状态（§4.3 键表）。

- 敏感项（``cred.*`` 与 ``dump.password``）只回 ``has_value`` + ``source``，不回明文；
- ``source='env'`` 的键拒绝界面写入 → 409 ``ENV_OVERRIDDEN``（由 :class:`ConfigRepo` 保证）；
- ``passfile.status`` 为**只读派生**（密码文件是否存在），不落库（§4.3）；
- E29/E30 是**探测类**端点：失败不静默——原因文案由 :func:`failure_reason` 统一生成
  （锁残留 / 凭据缺失 / 仓库未初始化 / 网络不可达，§5.1 E29「禁止静默」）。
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_CONFIG_UPDATE,
    BEHAVIOR_AUTO_INIT_KEY,
    BEHAVIOR_AUTO_UNLOCK_KEY,
    BEHAVIOR_CHECK_ENABLED_KEY,
    BEHAVIOR_CHECK_SAMPLE_RATIO_KEY,
    BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY,
    BEHAVIOR_MONTHLY_FULL_DAY_KEY,
    BEHAVIOR_MONTHLY_FULL_ENABLED_KEY,
    CHECK_SUBSET_DENOMINATOR_MAX,
    CHECK_SUBSET_DENOMINATOR_MIN,
    CRED_ENCRYPTION_KEY,
    DB_BUSY_TIMEOUT_MS_KEY,
    DB_BUSY_TIMEOUT_MS_MAX,
    DB_BUSY_TIMEOUT_MS_MIN,
    DUMP_DATABASES_KEY,
    DUMP_ENABLED_KEY,
    DUMP_ENGINES,
    DUMP_PORT_KEY,
    DUMP_RETENTION_DAYS_KEY,
    DUMP_TYPE_KEY,
    LOG_LEVEL_KEY,
    LOG_LEVELS,
    METRICS_TOKEN_KEY,
    MONTHLY_FULL_DAY_MAX,
    MONTHLY_FULL_DAY_MIN,
    RETENTION_AUDIT_ARCHIVE_DAYS_KEY,
    RETENTION_AUDIT_ARCHIVE_DIR_KEY,
    RETENTION_AUDIT_DAYS_KEY,
    RETENTION_JOBS_DAYS_KEY,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import CredentialKeyUnavailableError, ValidationError
from app.schemas.api_schemas import (
    ConfigItem,
    ConfigPatchRequest,
    ConfigResponse,
    OkResponse,
)
from app.services.crypto import EncryptionState
from app.services.env_sync import passfile_status
from app.services.log_control import apply_log_level
from app.services.metrics import hash_metrics_token
from app.services.repo_status_cache import invalidate_repo_status_cache
from app.services.scheduler import TIMEZONE_KEY
from app.services.time_sync import NTP_BACKUP_KEY, NTP_PRIMARY_KEY, validate_ntp_server
from app.utils.time import is_valid_timezone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# 键名形态白名单：小写字母开头，仅含小写字母/数字/点/下划线/连字符/星号（防污染键空间）
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9.*_-]{0,63}$")

# R-023：仓库单例键已迁 repos 表——E07 收到这些键时明确拒绝并指路（不静默忽略）
_RETIRED_REPO_PREFIXES: tuple[str, ...] = ("repo.",)

# 0/1 开关键（批次 B 批次面：behavior.* 三开关 + dump.enabled；R-025：凭据加密开关）
_BOOL_KEYS: frozenset[str] = frozenset(
    {
        BEHAVIOR_AUTO_INIT_KEY,
        BEHAVIOR_AUTO_UNLOCK_KEY,
        BEHAVIOR_CHECK_ENABLED_KEY,
        BEHAVIOR_MONTHLY_FULL_ENABLED_KEY,
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

    # R-023：仓库单例键（repo.url/repo.type/repo.host_tag）已迁 repos 表——明确
    # 拒绝并指路 /api/repos（不静默忽略：静默会让旧前端以为保存成功）
    retired = [key for key in payload.patch if key.startswith(_RETIRED_REPO_PREFIXES)]
    if retired:
        raise ValidationError(
            "仓库配置已迁至仓库管理（/api/repos），不再经配置键写入",
            data={"retired": retired},
        )

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

    # R-024：metrics.token 原地置换为 SHA-256 硬哈希（None=不变 / ""=清除 /
    # 非空=设置；设置后不可读，与 session token 同口径）。原地变换保持 set_many
    # 的原子性（env 覆盖整体拒绝、无部分写入不受影响）。
    if patch.get(METRICS_TOKEN_KEY):
        patch[METRICS_TOKEN_KEY] = hash_metrics_token(patch[METRICS_TOKEN_KEY])

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

    # 时区变更即时生效（E39）：调度触发器按 config.timezone 解释 cron，保存即重载，
    # 否则用户改完时区要等下一次任务写入或重启容器才生效。重载失败不影响已落库的
    # 写入结果（下次任务写入/重启仍会按新时区重建），故只记日志不让整笔请求失败。
    if TIMEZONE_KEY in patch:
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            try:
                await scheduler.reload()
            except Exception:  # noqa: BLE001 - 重载失败不吞掉已成功的配置写入
                logger.exception("时区变更后调度器重载失败（配置已保存，重启或下次任务写入后生效）")
        # E39 统一口径：restic 子进程 TZ 同步切到新时区（config.timezone 单一事实源），
        # 否则 restic 文本输出（snapshots 表格 / key list 的 created 等）仍按旧 TZ 渲染。
        # 空值 = 清除配置回落 settings.tz（与调度/系统时间接口同回退链）。
        restic = getattr(request.app.state, "restic", None)
        if restic is not None:
            restic.set_timezone(patch.get(TIMEZONE_KEY))

    # 系统域（compose → UI 迁移第一批）：保存即热生效——日志级别切换 root/uvicorn
    # 三件套；锁等待对当前连接重放 PRAGMA（新连接取同一份值）。失败只记日志：
    # 配置已落库，下次启动仍按新值生效（与时区刷新同纪律）。
    if LOG_LEVEL_KEY in patch:
        try:
            apply_log_level(patch.get(LOG_LEVEL_KEY))
        except Exception:  # noqa: BLE001 - 热切换失败不吞掉已成功的配置写入
            logger.exception("日志级别热切换失败（配置已保存，重启后生效）")
    if DB_BUSY_TIMEOUT_MS_KEY in patch:
        raw_busy = (patch.get(DB_BUSY_TIMEOUT_MS_KEY) or "").strip()
        if raw_busy.isdigit():
            db = getattr(request.app.state, "db", None)
            if db is not None:
                try:
                    await db.apply_busy_timeout(int(raw_busy))
                except Exception:  # noqa: BLE001 - 同上
                    logger.exception("锁等待热切换失败（配置已保存，重启后生效）")

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

    崩溃安全序（读侧恒按密文前缀 v1/v2 判定，与开关无关，故任一中断点均可读）：
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


def _parse_databases_for_validation(raw: str) -> list[str] | None:
    """校验 ``dump.databases`` 取值：非空 JSON 字符串数组（元素须为字符串）。

    非法（非 JSON / 非数组 / 含非字符串元素 / 空数组）返回 ``None``，由调用方
    转 422（整体拒绝，不产生部分写入）。
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(data, list)
        or len(data) == 0
        or not all(isinstance(item, str) for item in data)
    ):
        return None
    return data


def _is_safe_path(value: str) -> bool:
    """目录路径 plausibility 检查：长度合理、不含空字节与控制字符（防畸形/注入）。

    仅做轻量形态校验——归档目录由 :class:`RetentionService` 经 :class:`Path` 解析后
    只用于写归档文件（落在 ``data_dir`` 派生或 DB 键指定的目录），不做严格绝对路径
    限定（host 形态下用户路径本就多样）；真正的写入安全由路径字面量唯一出处纪律保证。
    """
    if not value or len(value) > 4096:
        return False
    return "\x00" not in value and "\n" not in value and "\r" not in value


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
        elif key == BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY:
            # 每日 n/t 滚动扫描周期天数：0 = 不做每日扫描；上限 256 对齐 restic totalBucketsMax
            if not value.isdigit() or not (
                CHECK_SUBSET_DENOMINATOR_MIN <= int(value) <= CHECK_SUBSET_DENOMINATOR_MAX
            ):
                raise ValidationError(
                    f"{key} 须为 {CHECK_SUBSET_DENOMINATOR_MIN}~{CHECK_SUBSET_DENOMINATOR_MAX} 的整数"
                    "（0=不做每日扫描，其余=每 N 天把全仓扫一遍）",
                    data={"key": key, "value": raw},
                )
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
        # E39：时区手动设置——坏值落库会被 resolve_timezone 静默回退 UTC
        # （表现为「保存成功但触发时刻没变」），故写入侧前置拒绝
        elif key == TIMEZONE_KEY and not is_valid_timezone(value):
            raise ValidationError(
                f"{key} 须为合法 IANA 时区名（如 Asia/Shanghai、UTC）",
                data={"key": key, "value": raw},
            )
        # 系统域（compose → UI 迁移第一批）：日志级别白名单 + 锁等待整数区间。
        # 坏值同样「保存成功但不生效」（apply_log_level 回落 INFO、PRAGMA 拒绝执行），
        # 故与上述键同一机制位前置 422。
        elif key == LOG_LEVEL_KEY and value.upper() not in LOG_LEVELS:
            raise ValidationError(
                f"{key} 仅支持 " + "/".join(LOG_LEVELS),
                data={"key": key, "value": raw, "allowed": list(LOG_LEVELS)},
            )
        elif key == DB_BUSY_TIMEOUT_MS_KEY and (
            not value.isdigit()
            or not (DB_BUSY_TIMEOUT_MS_MIN <= int(value) <= DB_BUSY_TIMEOUT_MS_MAX)
        ):
            raise ValidationError(
                f"{key} 须为 {DB_BUSY_TIMEOUT_MS_MIN}~{DB_BUSY_TIMEOUT_MS_MAX} 的整数（毫秒）",
                data={"key": key, "value": raw},
            )
        # E39：NTP 主备服务器——坏地址落库只在探测时才炸（「保存成功但同步失败」），
        # 同理写入侧前置拒绝；空串 = 清除配置回落内置默认链（由服务侧解析）
        elif key in (NTP_PRIMARY_KEY, NTP_BACKUP_KEY) and value and validate_ntp_server(value) is None:
            raise ValidationError(
                f"{key} 须为 NTP 服务器地址（主机名或主机名:端口，如 ntp.example.com / ntp.example.com:123）",
                data={"key": key, "value": raw},
            )
        # R-020 裁决1/裁决3：保留期相关键——坏值落库会让每日维护静默不清理或误清理，
        # 故写入侧前置 422（与 DUMP_RETENTION_DAYS_KEY 同机制位）。
        elif key in (RETENTION_JOBS_DAYS_KEY, RETENTION_AUDIT_DAYS_KEY, RETENTION_AUDIT_ARCHIVE_DAYS_KEY):
            if not value.isdigit():
                raise ValidationError(
                    f"{key} 须为非负整数（0=不启用清理）", data={"key": key, "value": raw}
                )
        elif key == RETENTION_AUDIT_ARCHIVE_DIR_KEY and value and not _is_safe_path(value):
            # 归档目录：非空须为合法目录路径（空 = 回落 settings.audit_archive_dir）
            raise ValidationError(
                f"{key} 须为目录路径（不含空字节/换行等控制字符，长度 <= 4096）",
                data={"key": key, "value": raw[:200]},
            )
        # RETENTION_AUDIT_ARCHIVE_COLD_TARGET_KEY：冷存目标纯占位/提示，应用不消费，放行

