"""config 表仓储（§4.3）：KV 读写 + 敏感项脱敏 + ``source='env'`` 键写保护。

派生键（只读，服务端自动维护）：
- （R-023 起无仓库派生键）``repo.url``/``repo.type``/``repo.host_tag`` 已随 E4
  裁决整体迁入 ``repos`` 表（``repo_repo``）；config 键空间不再含仓库单例键。

R-025 凭据加密（P3-A，owner 2026-10-16 裁决默认开）：注入 :class:`EncryptionState` 后，
敏感键（``cred.*`` / ``dump.password`` / ``notify.*``）写入即加密、读取透明解密——
**读侧恒按 ``enc:v1:``/``enc:v2:`` 前缀判定，与开关无关**（崩溃安全，见 services/crypto.py）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.db.database import Database
from app.errors import CredentialDecryptError, CredentialKeyUnavailableError, EnvOverriddenError, ValidationError
from app.schemas.enums import ConfigSource
from app.services.crypto import (
    SENSITIVE_EXACT_KEYS,
    SENSITIVE_PREFIXES,
    CredentialCrypto,
    CryptoProvider,
    is_sensitive,
    owner_of,
)
from app.utils.time import utc_iso

if TYPE_CHECKING:  # 避免运行时循环导入（crypto 不依赖本模块，仅类型注解需要）
    from app.services.crypto import EncryptionState

logger = logging.getLogger(__name__)

# 敏感键清单（SENSITIVE_EXACT_KEYS / SENSITIVE_PREFIXES）唯一定义点在
# services/crypto.py（密钥源归属 keys_of 与写侧加密同一口径）；此处仅再导出以兼容
# 既有 import 点（config_transfer / notifier 等）。
__all__ = ["ConfigRepo", "ConfigRow", "SENSITIVE_EXACT_KEYS", "SENSITIVE_PREFIXES"]


@dataclass(frozen=True)
class ConfigRow:
    """config 表一行（三态：没配过 / 有值 / 失效）。

    ``degraded`` 与 ``value is None`` 的组合是**唯一**区分方式，三者互不混淆：

    - 没配过：``value is None`` 且 ``degraded is False``；
    - 有值：``value`` 非空且 ``degraded is False``；
    - 失效：``value is None`` 且 ``degraded is True``，
      原因在 ``degraded_reason``——既不是「没配过」，也不得伪装成空值（方案 §8.4）。
    """

    key: str
    value: str | None
    source: str
    updated_at: str
    degraded: bool = False
    degraded_reason: str | None = None


class ConfigRepo:
    """config 表唯一访问点。"""

    def __init__(
        self,
        db: Database,
        encryption: EncryptionState | None = None,
        provider: CryptoProvider | None = None,
    ) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
            encryption: 凭据加密运行态（R-025）；None 时行为与本特性前一致
                （明文存储；但若库里已有密文，读取仍尝试解密，无密钥组件则报错
                而非把密文当明文透出）。仍是**写侧开关**的唯一事实源（``enabled``）。
            provider: 多仓库凭据加密编排器（B1：``cred.<repo_id>.*`` 按仓库口令文件
                隔离，全局键落到 default）。与 ``encryption`` 并存——前者选「哪个密钥」、
                后者定「是否加密」。仅 ``encryption`` 而无 ``provider`` 时退化为单实例
                （default）语义，保持历史测试兼容。
        """
        self._db = db
        self._encryption = encryption
        self._provider = provider

    # ---------------------------------------------------------------- 读

    def _crypto_for(self, key: str) -> CredentialCrypto:
        """返回该配置键应使用的凭据加密实例（B1：``cred.<repo_id>.*`` → 该仓库；其余 → default）。

        无 ``provider``（仅 ``encryption``）时退化为单实例 default 语义；
        无 ``encryption`` 组件时抛 ``CredentialDecryptError``（装配缺失，绝不透出密文）。
        """
        if self._encryption is None:
            raise CredentialDecryptError()
        if self._provider is not None:
            return self._provider.for_key(key)
        return self._encryption.crypto

    def _maybe_decrypt(self, value: str | None, key: str = "") -> str | None:
        """读侧透明解密：非密文前缀（v1/v2）原样返回（与开关无关，崩溃安全）。"""
        if value is None or not CredentialCrypto.is_encrypted(value):
            return value
        # 密文存在却无加密组件（装配缺失）——_crypto_for 报错，绝不把密文当明文透给业务
        return self._crypto_for(key).decrypt(value)

    async def get(self, key: str) -> ConfigRow | None:
        """按键读取一行；不存在返回 None。"""
        row = await self._db.fetch_one("SELECT key, value, source, updated_at FROM config WHERE key = ?", (key,))
        if not row:
            return None
        return self._decrypt_row(row)

    async def get_source(self, key: str) -> str | None:
        """只读 ``source`` 列（**不解密**）；不存在返回 None。

        与 :meth:`_guard_env_source` 同一理由：只判归属/来源的调用方若走 :meth:`get`，
        会被一行解不开的密文挡住——口令文件失效期间连「这条该不该回收」都判不了，
        启动期 env 同步于是崩在读取上（而不是按降级语义继续）。
        """
        row = await self._db.fetch_one("SELECT source FROM config WHERE key = ?", (key,))
        return None if row is None else str(row["source"])

    async def get_value(self, key: str, default: str | None = None) -> str | None:
        """读取键对应的值；不存在返回 default。"""
        row = await self.get(key)
        if row is None:
            return default
        return row.value

    async def list_all(self) -> list[ConfigRow]:
        """返回全部配置项（按 key 排序；**按密钥源分组**解密，失效密钥源就地降级）。

        方案 B-full（§5.1/§8.4）：此前是「全表逐行解密，任一行失败即整表抛」——单个
        仓库口令文件坏掉会把 GET /config、两条导出、load_credentials 一起拖成 500。
        现在的算法：

        1. 一次查询取**存储原形**（不解密）；
        2. 非密文行原样返回（读侧与开关无关，崩溃安全）；
        3. 密文行经 :func:`crypto.owner_of` 归到它的密钥源：首次解密失败即把**该密钥源**
           记为失效并降级该行，同源的后续行直接降级（不重复派生密钥、不重复报错）；
        4. 其余密钥源的行照常解密返回——一个仓库坏掉不再波及别的仓库。

        降级行 = ``value=None`` + ``degraded=True`` + ``degraded_reason``：展示路径
        可原样输出（``has_value=False``），执行路径（``env_sync.load_credentials``）据此抛错，
        **绝不把「读不到」伪装成「没配过」**。
        """
        rows = await self._db.fetch_all(
            "SELECT key, value, source, updated_at FROM config ORDER BY key"
        )
        broken: dict[str, str] = {}
        out: list[ConfigRow] = []
        for base in (_to_row(row) for row in rows):
            if not CredentialCrypto.is_encrypted(base.value):
                out.append(base)
                continue
            owner = owner_of(base.key)
            reason = broken.get(owner)
            if reason is None:
                try:
                    out.append(replace(base, value=self._crypto_for(base.key).decrypt(base.value)))
                    continue
                except (CredentialDecryptError, CredentialKeyUnavailableError) as exc:
                    if self._encryption is None:
                        # 装配缺失（无加密组件）不是「密钥源失效」，无 owner 语义——照旧抛
                        raise
                    # 逐键告警会在一个坏口令文件下刷屏：汇总告警由启动期 main.py 出（一条）
                    reason = _degraded_reason(owner, exc)
                    broken[owner] = reason
            out.append(replace(base, value=None, degraded=True, degraded_reason=reason))
        return out

    async def list_all_raw(self) -> list[ConfigRow]:
        """返回全部配置项**的存储原形**（不解密；仅供加密迁移判定形态，勿用于业务）。"""
        rows = await self._db.fetch_all("SELECT key, value, source, updated_at FROM config ORDER BY key")
        return [_to_row(row) for row in rows]

    def _decrypt_row(self, row: Any) -> ConfigRow:
        """行转换 + 读侧透明解密（业务读取统一入口）。"""
        base = _to_row(row)
        return ConfigRow(
            key=base.key,
            value=self._maybe_decrypt(base.value, base.key),
            source=base.source,
            updated_at=base.updated_at,
        )

    # ---------------------------------------------------------------- 写

    async def set(
        self,
        key: str,
        value: str | None,
        *,
        source: ConfigSource = ConfigSource.UI,
        force: bool = False,
    ) -> ConfigRow:
        """写入（upsert）单个键；``source='env'`` 的键拒绝界面写入。

        Args:
            key: 配置键，如 ``dump.host``。
            value: 配置值；None 表示清空。
            source: 来源标记，界面写入恒为 ``ui``。
            force: 仅供启动期 env→config 同步使用（``env_sync``），
                跳过 ``source='env'`` 写保护；业务写入路径必须保持 False。

        Raises:
            EnvOverriddenError: 该键已由环境变量覆盖且 ``force=False``（409 ENV_OVERRIDDEN）。
        """
        await self._guard_env_source(key, force=force)
        stored = self._maybe_encrypt(key, value)
        now = utc_iso()
        await self._upsert(key, stored, str(source), now)
        return ConfigRow(key=key, value=value, source=str(source), updated_at=now)

    async def _guard_env_source(self, key: str, *, force: bool) -> None:
        """env 覆盖写保护：只读 ``source`` 列，**不解密旧值**。

        刻意不走 :meth:`get`——密码文件被更换后旧密文不可解密，若守卫先解密，
        该键将永远无法经 E07 覆写（用户连「重新录入凭据」的恢复路径都被堵死）。
        """
        if force:
            return
        row = await self._db.fetch_one("SELECT source FROM config WHERE key = ?", (key,))
        if row is not None and str(row["source"]) == ConfigSource.ENV:
            raise EnvOverriddenError(data={"key": key})

    async def _upsert(self, key: str, value: str | None, source: str, now: str | None = None) -> None:
        """裸 upsert（不做任何守卫）：供 :meth:`set` 与派生键写入复用。"""
        await self._db.write(
            "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, source = excluded.source, "
            "updated_at = excluded.updated_at",
            (key, value, source, now or utc_iso()),
        )

    def _maybe_encrypt(self, key: str, value: str | None) -> str | None:
        """写侧透明加密：开关开 + 敏感键 + 非空值 → 密文；其余原样（与读侧前缀判定对称）。"""
        if value is None or value == "" or self._encryption is None or not self._encryption.enabled:
            return value
        if not self.is_sensitive(key):
            return value
        return self._crypto_for(key).encrypt(value)

    async def migrate_encryption(self, *, encrypt: bool) -> int:
        """敏感键存量形态搬运（R-025 开关/启动期迁移；幂等）。

        Args:
            encrypt: True=明文→密文（启用）；False=密文→明文（关闭）。

        Returns:
            实际搬运的键数（已就位的不重复计）。
        """
        if self._encryption is None:
            return 0
        changed = 0
        # 密钥源失效（口令文件缺失/不可读）时跳过的键数：启动期降级，不阻断启动
        skipped = 0
        now = utc_iso()
        for row in await self.list_all_raw():
            if not row.value or not self.is_sensitive(row.key):
                continue
            cr = self._crypto_for(row.key)
            if not encrypt:
                # 关闭迁移：密文 → 明文（v1/v2 都可解——读侧按前缀选 info）
                if not cr.is_encrypted(row.value):
                    continue
                try:
                    plaintext = cr.decrypt(row.value)
                except CredentialDecryptError:
                    # 密码文件被更换 ⇒ 该键旧密文不可解密：跳过并告警（不中断整批），
                    # 保留原密文形态；恢复手段=经 E07 覆写该键（set 守卫不解密旧值）
                    logger.warning("凭据加密迁移跳过 %s：旧密文无法解密（密码文件可能已被更换）", row.key)
                    continue
                await self._upsert(row.key, plaintext, row.source, now)
                changed += 1
                continue
            # 启用迁移（encrypt=True）：三形态收敛——
            #   ① 明文 → 加密（v2）；
            #   ② legacy enc:v1:（HKDF info 未混仓库标识，审计 2026-10-16 🟡-1）
            #      → 重加密为 enc:v2:（跨仓库密钥隔离）；
            #   ③ enc:v2: 已就位 → 跳过。幂等：重跑零改动。
            if not cr.is_encrypted(row.value):
                try:
                    await self._upsert(row.key, cr.encrypt(row.value), row.source, now)
                except CredentialKeyUnavailableError:
                    # 口令文件缺失 ⇒ 该键无密钥可用：跳过并计数（不中断整批、**不得让启动
                    # 崩**——main.py 对「口令文件缺失」只告警不阻止启动，此处抛即为崩溃循环）。
                    # 明文原样保留，口令文件就位后重跑本迁移即收敛（幂等）。
                    skipped += 1
                    continue
                changed += 1
            elif cr.needs_reencrypt(row.value):
                try:
                    await self._upsert(row.key, cr.reencrypt(row.value), row.source, now)
                    changed += 1
                except CredentialDecryptError:
                    # v1 密文不可解（密码文件被更换）：跳过并告警，恢复手段同上
                    logger.warning("凭据 v1→v2 重加密跳过 %s：旧密文无法解密（密码文件可能已被更换）", row.key)
                    continue
        if skipped:
            logger.warning(
                "凭据加密迁移跳过 %d 个键：仓库密码文件缺失，迁移将在密码文件就位后重试", skipped
            )
        return changed

    async def set_many(self, patch: Mapping[str, str | None]) -> list[ConfigRow]:
        """批量写入配置项；任一键被 env 覆盖则整体拒绝（不产生部分写入）。

        Args:
            patch: 键→值映射；值为 None 表示清空该键。

        Raises:
            EnvOverriddenError: 存在被环境变量覆盖的键。
        """
        keys = list(patch.keys())
        if not keys:
            return []
        placeholders = ",".join("?" * len(keys))
        rows = await self._db.fetch_all(
            f"SELECT key, value, source, updated_at FROM config WHERE key IN ({placeholders}) "
            "AND source = 'env'",
            tuple(keys),
        )
        if rows:
            blocked = [str(row["key"]) for row in rows]
            raise EnvOverriddenError(data={"keys": blocked})
        return [await self.set(key, value) for key, value in patch.items()]

    async def rename(self, key: str, new_key: str) -> ConfigRow:
        """重命名配置键（保留 value/source/updated_at 原样；供启动期迁移专用）。

        业务写入路径**不得使用**——界面无改键名入口；本方法存在的唯一理由是
        R-023 一次性迁移（``cred.*`` → ``cred.default.*``）。
        """
        row = await self._db.fetch_one("SELECT value, source, updated_at FROM config WHERE key = ?", (key,))
        if row is None:
            raise ValidationError("配置键不存在", data={"key": key})
        await self._db.write("DELETE FROM config WHERE key = ?", (key,))
        new_value = None if row["value"] is None else str(row["value"])
        await self._upsert(new_key, new_value, str(row["source"]), str(row["updated_at"]))
        moved = await self.get(new_key)
        if moved is None:  # pragma: no cover - 理论不可达
            raise RuntimeError(f"配置键 {new_key} 重命名后无法读回")
        return moved

    async def delete(self, key: str, *, force: bool = False) -> bool:
        """删除单个键；``source='env'`` 的键拒绝删除。

        同 :meth:`_guard_env_source`：守卫只读 ``source`` 列——密码文件被更换后
        旧密文不可解密，但删除（恢复手段之一）必须仍然可行。
        """
        row = await self._db.fetch_one("SELECT source FROM config WHERE key = ?", (key,))
        if row is None:
            return False
        if not force and str(row["source"]) == ConfigSource.ENV:
            raise EnvOverriddenError(data={"key": key})
        await self._db.write("DELETE FROM config WHERE key = ?", (key,))
        return True

    # ---------------------------------------------------------------- 脱敏

    @staticmethod
    def is_sensitive(key: str) -> bool:
        """判断键是否为敏感项（API 只回 has_value；判定委托 :func:`crypto.is_sensitive`）。"""
        return is_sensitive(key)

    @staticmethod
    def degraded_sources(rows: Iterable[ConfigRow]) -> list[str]:
        """本次读取中失效的密钥源（去重排序）——启动期「一条汇总告警」的数据源。

        Args:
            rows: ``list_all()`` 的结果。

        Returns:
            失效的密钥源标识（仓库 id）。空列表 = 全部密钥源健康。
        """
        return sorted({owner_of(row.key) for row in rows if row.degraded})

    def mask(self, row: ConfigRow) -> dict[str, Any]:
        """按敏感性脱敏后输出：敏感项回 ``has_value`` + ``source``，不回明文。

        ``degraded`` / ``degraded_reason`` 对**所有**键都输出（含非敏感键）：
        失效密钥源保护的键必须能与「没配过」区分开，脱敏挡的是值、不是状态。降级行的
        ``has_value`` 恒为 False（``value`` 已被置 None）。
        """
        sensitive = self.is_sensitive(row.key)
        payload: dict[str, Any] = {
            "key": row.key,
            "has_value": row.value is not None and row.value != "",
            "source": row.source,
            "sensitive": sensitive,
            "degraded": row.degraded,
            "degraded_reason": row.degraded_reason,
        }
        if not sensitive:
            payload["value"] = row.value
        return payload


def _degraded_reason(repo_id: str, exc: Exception) -> str:
    """生成「该密钥源失效」的可读原因（S3：只含密钥源标识，不含任何键值/密文）。

    Args:
        repo_id: 失效的密钥源（仓库标识）。
        exc: 首次解密失败的异常——区分「钥匙没了」与「钥匙不对」，两者的运维动作不同。
    """
    if isinstance(exc, CredentialKeyUnavailableError):
        return f"密钥源 {repo_id} 的口令文件缺失或不可读，该键无法解密"
    return f"密钥源 {repo_id} 的口令文件与密文不匹配，该键无法解密"


def _to_row(row: Any) -> ConfigRow:
    """将 sqlite3.Row 转换为 :class:`ConfigRow`。"""
    return ConfigRow(
        key=str(row["key"]),
        value=None if row["value"] is None else str(row["value"]),
        source=str(row["source"]),
        updated_at=str(row["updated_at"]),
    )
