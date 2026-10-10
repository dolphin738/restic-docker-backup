"""环境变量 → config 表一次性同步（批次 2 待办，方案 §6「env 覆盖优先」）。

动机：``config.source='env'`` 此前没有任何写入方，导致 E07 的 409 ``ENV_OVERRIDDEN``
永远触发不到（界面 env 置灰也就无从谈起）。本模块在**启动期**做一次同步：

- env 中出现的键 → upsert 为 ``source='env'``（覆盖 UI 值，env 优先）；
- env 中已消失、但库里仍是 ``source='env'`` 的键 → 删除，回落到 UI 值/缺省
  （支持"用户撤掉 env 覆盖"这一反向操作）；
- 派生只读的 ``passfile.status``（检测密码文件是否真实存在），**不落库**（§4.3 注明"只读派生"）。

刻意**不映射** ``TZ``：时区是 UI 可配项（§4.1），而 compose 必然设置 ``TZ``；
若把 ``TZ`` 映射进 config 会让"界面改时区"永远被 env 覆盖置灰，与边界清单矛盾。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.constants import AUDIT_ENV_SYNC
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.schemas.enums import ConfigSource
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 环境变量名 → config 键（§4.3 键表）
ENV_KEY_MAP: dict[str, str] = {
    "RESTIC_REPOSITORY": "repo.url",
    "RESTIC_HOST": "repo.host_tag",
    "AWS_ACCESS_KEY_ID": "cred.aws_access_key_id",
    "AWS_SECRET_ACCESS_KEY": "cred.aws_secret_access_key",
    "AWS_DEFAULT_REGION": "cred.aws_default_region",
    "AWS_ENDPOINT_URL": "cred.aws_endpoint_url",
    "B2_ACCOUNT_ID": "cred.b2_account_id",
    "B2_ACCOUNT_KEY": "cred.b2_account_key",
}


def passfile_path(settings: Settings | None = None) -> Path:
    """解析仓库密码文件路径（优先 ``RESTIC_PASSWORD_FILE``，回退配置值）。"""
    resolved = settings or get_settings()
    override = os.environ.get("RESTIC_PASSWORD_FILE", "").strip()
    return Path(override or resolved.passfile)


def passfile_status(settings: Settings | None = None) -> str:
    """派生密码文件挂载状态：``configured`` 或 ``missing``（只读、不落库）。"""
    return "configured" if passfile_path(settings).is_file() else "missing"


async def sync_env_overrides(
    config_repo: ConfigRepo,
    settings: Settings | None = None,
    audit: AuditRepo | None = None,
) -> dict[str, int]:
    """启动期执行 env→config 一次性同步。

    Args:
        config_repo: 配置仓储。
        settings: 运行期配置。
        audit: 审计仓储；传入时记录一条同步摘要。

    Returns:
        ``{"upserted": n, "pruned": m}``——写入/覆盖条数与回收条数。
    """
    del settings  # 当前映射表只依赖 os.environ；保留形参以便调用方显式注入
    upserted = 0
    pruned = 0
    touched: list[str] = []

    for env_name, key in ENV_KEY_MAP.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            await config_repo.set(key, value, source=ConfigSource.ENV, force=True)
            touched.append(key)
            upserted += 1
            continue
        existing = await config_repo.get(key)
        if existing is not None and existing.source == ConfigSource.ENV:
            await config_repo.delete(key, force=True)
            pruned += 1

    if upserted:
        logger.info("env→config 同步：覆盖 %d 个键（%s）", upserted, ", ".join(touched))
    if pruned:
        logger.info("env→config 同步：回收 %d 个失效的 env 覆盖键", pruned)

    if audit is not None and (upserted or pruned):
        await audit.record(
            AUDIT_ENV_SYNC,
            target="config",
            detail={"upserted": upserted, "pruned": pruned, "keys": touched},
            result="ok",
        )
    return {"upserted": upserted, "pruned": pruned}
