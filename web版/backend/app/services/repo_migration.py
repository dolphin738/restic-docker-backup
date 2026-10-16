"""R-023 启动期一次性迁移（P3-C）：config 单例仓库键 → repos 表 + 凭据命名空间化。

背景（架构 §13 E4 裁决）：``repo.url`` / ``repo.type`` / ``repo.host_tag`` 三个
config 全局键整体迁入 ``repos`` 表；``cred.*`` 凭据按仓库命名空间化为
``cred.<repo_id>.*``（多仓库各自持有后端凭证——E4「禁止把仓库级状态塞全局 KV」
的兑现面）。``repo.type`` 不迁：它是由 url 派生的只读键，双状态源本身就是
config 时代的遗留问题，随迁移一并消灭（现算，见 ``repo_repo.derive_repo_type``）。

幂等：marker 键 ``security.repos_migrated`` 落库即视为完成；重复启动零改动。
崩溃安全：任一步中断后重跑——已迁走的键不存在（跳过），未迁的继续迁；
``repos.default`` 行 0002 已建（恒存在），不存在「半行」形态。
"""

from __future__ import annotations

import logging

from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import DEFAULT_REPO_ID, ReposRepo

logger = logging.getLogger(__name__)

MIGRATION_MARKER_KEY = "security.repos_migrated"

# 迁离 config 的仓库单例键（存在即迁，迁后即删）
_LEGACY_REPO_KEYS = ("repo.url", "repo.type", "repo.host_tag")


async def migrate_legacy_repo_config(config_repo: ConfigRepo, repos_repo: ReposRepo) -> dict[str, int]:
    """执行一次性迁移（marker 幂等）。

    Args:
        config_repo: 配置仓储（旧键读取 + cred 重写 + marker）。
        repos_repo: 仓库仓储（default 行写入）。

    Returns:
        ``{"repo_keys": n, "cred_keys": m}``——搬迁的仓库键数与凭据键数。
    """
    if (await config_repo.get_value(MIGRATION_MARKER_KEY)) == "1":
        return {"repo_keys": 0, "cred_keys": 0}

    moved_repo_keys = 0
    # ① 仓库单例键 → repos.default（含 source 语义：env 覆盖的键迁过去仍是 env 来源）
    for key, field in (("repo.url", "url"), ("repo.host_tag", "host_tag")):
        row = await config_repo.get(key)
        if row is None or not row.value:
            continue
        if row.source == "env":
            await repos_repo.set_field_from_env(DEFAULT_REPO_ID, field, row.value)
        else:
            await repos_repo.update(DEFAULT_REPO_ID, **{field: row.value})
        moved_repo_keys += 1

    # ② cred.* → cred.default.*（保留 value/source/updated_at；旧键删除）
    moved_cred_keys = 0
    prefix = f"cred.{DEFAULT_REPO_ID}."
    for row in await config_repo.list_all_raw():
        if not row.key.startswith("cred.") or row.key.startswith(prefix):
            continue
        new_key = prefix + row.key[len("cred.") :]
        if await config_repo.get(new_key) is not None:
            logger.warning("凭据迁移目标键已存在，跳过 %s → %s（请人工核对）", row.key, new_key)
            continue
        await config_repo.rename(row.key, new_key)
        moved_cred_keys += 1

    # ③ 清理旧键 + 落 marker
    for key in _LEGACY_REPO_KEYS:
        if await config_repo.get(key) is not None:
            await config_repo.delete(key, force=True)
    await config_repo.set(MIGRATION_MARKER_KEY, "1")

    if moved_repo_keys or moved_cred_keys:
        logger.info(
            "R-023 一次性迁移完成：仓库键 %d 个、凭据键 %d 个已迁入 repos 表/命名空间",
            moved_repo_keys,
            moved_cred_keys,
        )
    return {"repo_keys": moved_repo_keys, "cred_keys": moved_cred_keys}
