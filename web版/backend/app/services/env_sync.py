"""环境变量 → 存储一次性同步（启动期；方案 §6「env 覆盖优先」，R-023 起分两条通道）。

两条通道（0003 多仓库后的落点）：
- **凭据通道**（``sync_env_overrides``）：``AWS_*`` / ``B2_*`` → config 键
  ``cred.default.*``（env 只覆盖 **default** 仓库的凭据；多仓库各自持有）；
  另含**系统域**（compose → UI 迁移第一批）：``LOG_LEVEL`` / ``DB_BUSY_TIMEOUT_MS``
  → ``log.level`` / ``db.busy_timeout_ms``（env 设了即锁定 UI，未设则 UI 可配）；
- **仓库通道**（``sync_env_repo_overrides``）：``RESTIC_REPOSITORY`` /
  ``RESTIC_HOST`` → ``repos.default`` 行的 url/host_tag（``*_source='env'``）。

反向操作（用户撤掉 env 覆盖）两条通道都支持：库里标记为 env 来源的键/字段
在 env 消失时回收，回落 UI 值/缺省。

派生只读的 ``passfile.status``（检测密码文件是否真实存在），**不落库**（§4.3）。

刻意**不映射** ``TZ``：时区是 UI 可配项（§4.1），而 compose 必然设置 ``TZ``；
若把 ``TZ`` 映射进 config 会让"界面改时区"永远被 env 覆盖置灰，与边界清单矛盾。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.constants import (
    AUDIT_ENV_SYNC,
    DB_BUSY_TIMEOUT_MS_KEY,
    LOG_LEVEL_KEY,
    PASSFILE_STATUS_CONFIGURED,
    PASSFILE_STATUS_MISSING,
)
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import DEFAULT_REPO_ID, ReposRepo
from app.errors import CredentialDecryptError, CredentialKeyUnavailableError
from app.schemas.enums import ConfigSource
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 环境变量名 → config 键（§4.3 键表；R-023 起凭据按仓库命名空间，env 只覆盖 default）。
# A1：新增 restic 0.19.1 后端的官方 env 名（源码核验：azure/gs/rest 各后端
# ApplyEnvironment）——与 cred.* → 子进程 env 的还原规则（load_credentials）一一对应。
# 系统域（compose → UI 迁移第一批）：LOG_LEVEL / DB_BUSY_TIMEOUT_MS 同步登记，
# env 设了即标 source='env' → E07 界面置灰 + 409 写保护，未设则 UI 可配。
ENV_KEY_MAP: dict[str, str] = {
    "LOG_LEVEL": LOG_LEVEL_KEY,
    "DB_BUSY_TIMEOUT_MS": DB_BUSY_TIMEOUT_MS_KEY,
    "AWS_ACCESS_KEY_ID": "cred.default.aws_access_key_id",
    "AWS_SECRET_ACCESS_KEY": "cred.default.aws_secret_access_key",
    "AWS_DEFAULT_REGION": "cred.default.aws_default_region",
    "AWS_ENDPOINT_URL": "cred.default.aws_endpoint_url",
    "B2_ACCOUNT_ID": "cred.default.b2_account_id",
    "B2_ACCOUNT_KEY": "cred.default.b2_account_key",
    "AZURE_ACCOUNT_NAME": "cred.default.azure_account_name",
    "AZURE_ACCOUNT_KEY": "cred.default.azure_account_key",
    "AZURE_ACCOUNT_SAS": "cred.default.azure_account_sas",
    "AZURE_ENDPOINT_SUFFIX": "cred.default.azure_endpoint_suffix",
    "GOOGLE_PROJECT_ID": "cred.default.google_project_id",
    "GOOGLE_APPLICATION_CREDENTIALS": "cred.default.google_application_credentials",
    "GOOGLE_ACCESS_TOKEN": "cred.default.google_access_token",
    "RESTIC_REST_USERNAME": "cred.default.restic_rest_username",
    "RESTIC_REST_PASSWORD": "cred.default.restic_rest_password",
}

# 仓库通道：环境变量名 → repos 表字段（仅 default 仓库）
ENV_REPO_FIELD_MAP: dict[str, str] = {
    "RESTIC_REPOSITORY": "url",
    "RESTIC_HOST": "host_tag",
}


def passfile_path(settings: Settings | None = None, repo_id: str = DEFAULT_REPO_ID) -> Path:
    """解析仓库密码文件路径（E5 per-repo 布局；委托 :meth:`Settings.passfile_path`）。

    Args:
        settings: 运行期配置；省略时取进程内单例。
        repo_id: 仓库标识（当前仅 ``default``）。
    """
    resolved = settings or get_settings()
    return resolved.passfile_path(repo_id)


def passfile_status(settings: Settings | None = None, repo_id: str = DEFAULT_REPO_ID) -> str:
    """派生密码文件挂载状态：``configured`` 或 ``missing``（只读、不落库）。

    字面量取 :data:`~app.constants.PASSFILE_STATUS_*`（单一来源，与 API 契约、
    C4 镜像阶段共用——避免三处字符串各自漂移）。
    """
    if passfile_path(settings, repo_id).is_file():
        return PASSFILE_STATUS_CONFIGURED
    return PASSFILE_STATUS_MISSING


async def load_credentials(config_repo: ConfigRepo, repo_id: str = DEFAULT_REPO_ID) -> dict[str, str]:
    """把 config 中指定仓库的 ``cred.<repo_id>.*`` 键还原为子进程环境变量。

    转换规则（A1 起按 restic 0.19.1 各后端官方 env 名逐一显式登记；未知前缀**丢弃**——
    宁可不注入也不把拼错的键变成任意环境变量，最终由 :meth:`ResticCmd.build_env`
    的 ``ENV_ALLOWED_PREFIXES`` 前缀白名单二次把关）：

    - ``cred.<repo_id>.aws_<rest>`` → ``AWS_<REST>``（故 ``aws_session_token``
      之类未在 :data:`ENV_KEY_MAP` 中的键也能正确还原）；
    - ``b2_`` → ``B2_``；``azure_`` → ``AZURE_``；``google_`` → ``GOOGLE_``；
      ``rclone_`` → ``RCLONE_``；``ssh_`` → ``SSH_``；
    - ``restic_rest_<rest>`` → ``RESTIC_REST_<REST>``（rest 后端只认
      ``RESTIC_REST_USERNAME``/``RESTIC_REST_PASSWORD``，前缀整体搬迁）。

    本函数是 ``cred.*`` → 子进程环境映射的**唯一实现点**（E05/E17/E18/E19/E29/E30
    与 JobRunner 共用）；最终注入仍由 :meth:`ResticCmd.build_env` 按前缀白名单二次校验。

    Args:
        config_repo: 配置仓储。
        repo_id: 仓库标识（凭据命名空间）。

    Returns:
        待注入子进程的环境变量字典（键均已落在各后端官方前缀内）。
    """
    rows = await config_repo.list_all()
    prefix = f"cred.{repo_id}."
    # 方案 §8.4 强制约束：失效密钥源保护的凭据**必须抛错**，绝不能像空值一样跳过——
    # 否则备份会安静地不带云凭据跑起来，最后报「认证失败」，用户去查 S3 密钥查一整天。
    # 展示路径（GET /config、两条导出）允许降级返回，执行路径（本函数，每个备份任务
    # 的必经之路）不允许。
    # getattr：本函数的契约只要求 key/value 两列（既有测试桩即如此），降级标记是新增列
    if any(getattr(row, "degraded", False) and row.key.startswith(prefix) for row in rows):
        # CredentialDecryptError 而非 CredentialKeyUnavailableError：后者是 422 校验类
        # （契约语义「用户得改输入」，E07 写路径前置用的就是它）；执行路径读不出凭据是
        # 服务端状态问题（口令文件被换/缺失），500 形态才如实。异常消息与 degraded_reason
        # 一样不含任何键名与值（S3）。
        raise CredentialDecryptError()
    extras: dict[str, str] = {}
    for row in rows:
        if not row.value or not row.key.startswith(prefix):
            continue
        rest = row.key[len(prefix) :]
        if rest.startswith("aws_"):
            extras["AWS_" + rest[len("aws_") :].upper()] = row.value
        elif rest.startswith("b2_"):
            extras["B2_" + rest[len("b2_") :].upper()] = row.value
        elif rest.startswith("azure_"):
            extras["AZURE_" + rest[len("azure_") :].upper()] = row.value
        elif rest.startswith("google_"):
            extras["GOOGLE_" + rest[len("google_") :].upper()] = row.value
        elif rest.startswith("rclone_"):
            extras["RCLONE_" + rest[len("rclone_") :].upper()] = row.value
        elif rest.startswith("ssh_"):
            extras["SSH_" + rest[len("ssh_") :].upper()] = row.value
        elif rest.startswith("restic_rest_"):
            extras["RESTIC_REST_" + rest[len("restic_rest_") :].upper()] = row.value
    return extras


async def sync_env_overrides(
    config_repo: ConfigRepo,
    settings: Settings | None = None,
    audit: AuditRepo | None = None,
) -> dict[str, int]:
    """启动期执行 env→config 一次性同步（凭据通道）。

    Args:
        config_repo: 配置仓储。
        settings: 运行期配置。
        audit: 审计仓储；传入时记录一条同步摘要。

    Returns:
        ``{"upserted": n, "pruned": m, "skipped": k}``——写入/覆盖条数、回收条数、
        因密码文件缺失被跳过的凭据键数（R-025）。
    """
    del settings  # 当前映射表只依赖 os.environ；保留形参以便调用方显式注入
    upserted = 0
    pruned = 0
    skipped = 0
    touched: list[str] = []

    for env_name, key in ENV_KEY_MAP.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            try:
                await config_repo.set(key, value, source=ConfigSource.ENV, force=True)
            except CredentialKeyUnavailableError:
                # R-025：密码文件缺失 ⇒ 凭据加密不可用。env 覆盖键降级跳过
                # （不阻断启动）；密码文件就位后下次启动自动补同步
                logger.warning("env→config 同步跳过 %s：仓库密码文件缺失，凭据加密不可用", key)
                skipped += 1
                continue
            touched.append(key)
            upserted += 1
            continue
        # 只读 source 列（不解密）：口令文件失效期间该键解不开，但「是不是 env 覆盖键」
        # 仍可判定——走 get() 会让启动期同步崩在一行读不出的密文上（降级语义被绕过）。
        if await config_repo.get_source(key) == ConfigSource.ENV:
            await config_repo.delete(key, force=True)
            pruned += 1

    if upserted:
        logger.info("env→config 同步：覆盖 %d 个键（%s）", upserted, ", ".join(touched))
    if pruned:
        logger.info("env→config 同步：回收 %d 个失效的 env 覆盖键", pruned)
    if skipped:
        logger.warning("env→config 同步：%d 个凭据键因密码文件缺失被跳过", skipped)

    if audit is not None and (upserted or pruned):
        await audit.record(
            AUDIT_ENV_SYNC,
            target="config",
            detail={"upserted": upserted, "pruned": pruned, "keys": touched},
            result="ok",
        )
    return {"upserted": upserted, "pruned": pruned, "skipped": skipped}


async def sync_env_repo_overrides(
    repos_repo: ReposRepo,
    settings: Settings | None = None,
    audit: AuditRepo | None = None,
) -> dict[str, int]:
    """启动期执行 env→repos 一次性同步（仓库通道；R-023，env 只覆盖 default 仓库）。

    Args:
        repos_repo: 仓库仓储。
        settings: 运行期配置（保留形参，与凭据通道对称）。
        audit: 审计仓储；传入时记录一条同步摘要。

    Returns:
        ``{"upserted": n, "pruned": m}``——覆盖/回收的字段数。
    """
    del settings
    upserted = 0
    pruned = 0
    touched: list[str] = []

    for env_name, field in ENV_REPO_FIELD_MAP.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            await repos_repo.set_field_from_env(DEFAULT_REPO_ID, field, value)
            touched.append(f"repo.{field}")
            upserted += 1
            continue
        repo = await repos_repo.get(DEFAULT_REPO_ID)
        if repo is not None and (
            (field == "url" and repo.url_source == ConfigSource.ENV)
            or (field == "host_tag" and repo.host_tag_source == ConfigSource.ENV)
        ):
            await repos_repo.set_field_from_env(DEFAULT_REPO_ID, field, None)
            pruned += 1

    if upserted:
        logger.info("env→repos 同步：覆盖 default 仓库 %d 个字段（%s）", upserted, ", ".join(touched))
    if pruned:
        logger.info("env→repos 同步：回收 %d 个失效的 env 覆盖字段", pruned)

    if audit is not None and (upserted or pruned):
        await audit.record(
            AUDIT_ENV_SYNC,
            target="repos",
            detail={"upserted": upserted, "pruned": pruned, "fields": touched},
            result="ok",
        )
    return {"upserted": upserted, "pruned": pruned}
