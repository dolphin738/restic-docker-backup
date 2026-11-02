"""凭据加密存储（R-025 / P3-A；架构 §4.3 演进位，owner 2026-10-16 裁决：默认开）。

设计（架构 §4.3 注既定，本模块是其唯一实现点）：

- **密钥源自仓库密码文件**：HKDF-SHA256（RFC 5869，hmac 实现，不引入 KDF 依赖）
  从 E5 per-repo 布局的密码文件内容派生 256-bit 密钥。密码文件是「加密根密钥、
  永不入库」——数据库文件单独泄露不再等于凭据泄露；代价是密码文件成为单点
  （丢失/被换 ⇒ 凭据全部解不开，与 restic 仓库本身同源）。
- **AES-256-GCM 认证加密**（pyca/cryptography）：篡改、错钥可检测，绝不静默通过。
- **存储格式** ``enc:v2:<base64(nonce‖ciphertext‖tag)>``（现行；legacy ``enc:v1:`` 只读兼容）：读侧**恒按前缀判定**，
  与运行期开关无关——开关只决定写侧是否加密。故启用/关闭/迁移中途崩溃，
  混合形态存储一律可读（崩溃安全），重跑迁移即收敛。
- **密钥缓存**按密码文件 ``(mtime_ns, size)`` 失效：运维轮换密码文件后无需重启，
  下一次加解密即用新密钥（旧密文届时将无法解密——见 :class:`CredentialDecryptError`）。

S3 合规：密钥只经本模块派生；明文不出现在日志/审计/SSE/异常消息中（错误只回
可操作原因）；测试夹具同样不得落明文快照。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.db.repositories.repo_repo import DEFAULT_REPO_ID
from app.errors import CredentialDecryptError, CredentialKeyUnavailableError
from app.settings import Settings, get_settings

# 密文格式前缀（版本化：未来换算法/参数只增前缀，旧前缀只读兼容）
# - ``enc:v1:`` legacy：HKDF info 未混入仓库标识（跨仓库密钥隔离缺口，审计
#   2026-10-16 🟡-1）——只读兼容，迁移重加密收敛到 v2；
# - ``enc:v2:`` 现行：HKDF info 混入 repo_id（同密码文件内容的不同仓库派生出
#   不同密钥，杜绝跨仓库解密）。
PREFIX_V1 = "enc:v1:"
PREFIX_V2 = "enc:v2:"
PREFIX = PREFIX_V2  # 写侧现行前缀（encrypt 输出）
_PREFIXES = (PREFIX_V1, PREFIX_V2)

_NONCE_BYTES = 12          # AES-GCM 标准 nonce 长度（96 bit）
_KEY_BYTES = 32            # AES-256
_HKDF_SALT = b"restic-web/credential-encryption/v1"
_HKDF_INFO_V1 = b"config-credential-encryption-key"

# 敏感键：API 只回 has_value + source，不回明文（§4.3 键表"敏感=是"的项）。
# **唯一定义点**（R-025）：写侧加密、API 脱敏、密钥源归属（:func:`keys_of`）共用此口径；
# db/repositories/config_repo.py 从这里导入（反向 import 会成环）。
SENSITIVE_PREFIXES: tuple[str, ...] = ("cred.",)
# R-018：通知渠道 URL 内嵌令牌（healthchecks ping UUID / webhook 地址），同凭据口径脱敏
# R-024：metrics.token 同口径（且库内只存 SHA-256 硬哈希，界面设置不可读）
SENSITIVE_EXACT_KEYS: tuple[str, ...] = (
    "dump.password",
    "notify.healthchecks_url",
    "notify.webhook.url",
    "metrics.token",
    # C2 SMTP 邮箱密码（内中继可能留空，有值即与凭据同口径：只回 has_value + 库内加密）
    "notify.mail.password",
)


def is_sensitive(key: str) -> bool:
    """判断键是否为敏感项（API 只回 has_value；加密与密钥源归属同一口径）。"""
    return key in SENSITIVE_EXACT_KEYS or key.startswith(SENSITIVE_PREFIXES)


def _hkdf_info(repo_id: str) -> bytes:
    """v2 HKDF info：仓库标识混入派生输入（跨仓库密钥隔离，审计 2026-10-16 🟡-1）。

    同一密码文件内容 + 不同 repo_id ⇒ 不同密钥——运维在两个仓库复用同一密码时，
    一库的密文无法被另一库的密钥解开。
    """
    return _HKDF_INFO_V1 + b"\x00" + repo_id.encode("utf-8")


def _hkdf_sha256(secret: bytes, *, info: bytes, length: int = _KEY_BYTES) -> bytes:
    """RFC 5869 HKDF-SHA256（hmac 实现）。

    Args:
        secret: 输入密钥材料（仓库密码文件原始字节）。
        info: 上下文与应用信息（v1=固定值；v2=混入 repo_id）。
        length: 派生长度（字节）。

    Returns:
        派生密钥。
    """
    prk = hmac.new(_HKDF_SALT, secret, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


@dataclass(frozen=True)
class _KeyCache:
    """按密码文件 (mtime_ns, size) 维度的派生密钥缓存。"""

    stat: tuple[int, int]
    key: bytes


class CredentialCrypto:
    """config 敏感值透明加解密（R-025 唯一实现点）。

    用法：由 ``main.py`` 装配进 :class:`~app.db.repositories.config_repo.ConfigRepo`
    与 :class:`EncryptionState`；业务代码不直接调用（除迁移与可用性探测）。
    """

    def __init__(self, settings: Settings | None = None, *, repo_id: str = "default") -> None:
        """构造加解密器。

        Args:
            settings: 运行期配置；省略时取进程内单例。
            repo_id: 仓库标识（当前仅 ``default``；R-023 多仓库时按仓库派生）。
        """
        self._settings = settings or get_settings()
        self._repo_id = repo_id
        # 按 HKDF info 分桶缓存（v2 现行密钥 / v1 legacy 密钥各自独立，互不污染）
        self._cache: dict[bytes, _KeyCache] = {}

    # ----------------------------------------------------------- 密钥

    def passfile(self) -> Path:
        """密码文件路径（E5 per-repo 布局，委托 :meth:`Settings.passfile_path`）。"""
        return self._settings.passfile_path(self._repo_id)

    def available(self) -> bool:
        """密钥材料是否就位（密码文件存在且非空）。"""
        path = self.passfile()
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def _derive_key(self) -> bytes:
        """派生现行 v2 密钥（HKDF info 混入 repo_id；带 (mtime_ns, size) 缓存）。

        Raises:
            CredentialKeyUnavailableError: 密码文件缺失/不可读/为空。
        """
        return self._derive_key_for_info(_hkdf_info(self._repo_id))

    def _derive_key_legacy_v1(self) -> bytes:
        """派生 legacy ``enc:v1:`` 密钥（固定 info，无仓库隔离——只读兼容路径）。

        与 v2 密钥各自缓存（info 作键），互不污染；仅 decrypt 的 v1 分支使用。
        """
        return self._derive_key_for_info(_HKDF_INFO_V1)

    def _derive_key_for_info(self, info: bytes) -> bytes:
        """按给定 HKDF info 派生密钥（共享 stat 缓存；文件被换立即失效）。

        Raises:
            CredentialKeyUnavailableError: 密码文件缺失/不可读/为空。
        """
        path = self.passfile()
        try:
            stat = path.stat()
            secret = path.read_bytes()
        except OSError as exc:
            raise CredentialKeyUnavailableError(str(path)) from exc
        if not secret:
            raise CredentialKeyUnavailableError(str(path))
        cached = self._cache.get(info)
        if cached is not None and cached.stat == (stat.st_mtime_ns, stat.st_size):
            return cached.key
        key = _hkdf_sha256(secret, info=info)
        self._cache[info] = _KeyCache(stat=(stat.st_mtime_ns, stat.st_size), key=key)
        return key

    # ----------------------------------------------------------- 编解码

    @staticmethod
    def is_encrypted(value: str | None) -> bool:
        """判断存储值是否为密文形态（``enc:v1:`` / ``enc:v2:`` 任一前缀）。

        读侧与运行期开关无关（崩溃安全）：迁移中途崩溃的 v1/v2 混合形态一律可读。
        """
        return bool(value) and value.startswith(_PREFIXES)

    @staticmethod
    def needs_reencrypt(value: str | None) -> bool:
        """是否为 legacy ``enc:v1:`` 密文（需迁移重加密收敛到 v2）。"""
        return bool(value) and value.startswith(PREFIX_V1)

    def encrypt(self, plaintext: str) -> str:
        """加密明文，返回 ``enc:v2:<base64>`` 形态密文（HKDF info 混入 repo_id）。

        Raises:
            CredentialKeyUnavailableError: 密码文件缺失/不可读/为空。
        """
        key = self._derive_key()
        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return PREFIX_V2 + base64.b64encode(nonce + sealed).decode("ascii")

    def reencrypt(self, stored: str) -> str:
        """legacy ``enc:v1:`` 密文 → 现行 ``enc:v2:``（迁移用；明文不过任何边界）。

        Raises:
            CredentialDecryptError: v1 密文无法解密（密码文件被更换）——调用方
                跳过并告警，不中断整批（与既有迁移语义一致）。
            CredentialKeyUnavailableError: 密码文件缺失/不可读/为空。
        """
        return self.encrypt(self.decrypt(stored))

    def decrypt(self, stored: str) -> str:
        """解密密文（非密文形态原样返回——调用方已按前缀过滤，双保险）。

        按前缀选择 HKDF info：``enc:v1:`` 用 legacy 固定 info（只读兼容），
        ``enc:v2:`` 用混入 repo_id 的 info。

        Raises:
            CredentialDecryptError: 密文损坏、被篡改或密钥不匹配（密码文件被更换）。
                明文/密文均不进错误信息（S3）。
        """
        if not self.is_encrypted(stored):
            return stored
        if stored.startswith(PREFIX_V1):
            prefix, key = PREFIX_V1, self._derive_key_legacy_v1()
        else:
            prefix, key = PREFIX_V2, self._derive_key()
        try:
            blob = base64.b64decode(stored[len(prefix) :], validate=True)
            nonce, sealed = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
            plaintext = AESGCM(key).decrypt(nonce, sealed, None)
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            # binascii.Error 是 ValueError 子类；InvalidTag 覆盖篡改/错钥
            raise CredentialDecryptError() from exc


class EncryptionState:
    """凭据加密运行期开关（进程内单一事实源；持久化于 config 键 ``security.cred_encryption``）。

    - ``enabled`` 只决定**写侧**是否加密；读侧恒按 ``enc:v1:``/``enc:v2:`` 前缀判定（崩溃安全）；
    - 缺省（键不存在）= ``True``——owner 2026-10-16 裁决「默认开」；
    - 单进程 asyncio 模型下内存态即运行真相；E07 写开关与启动期迁移同点维护。
    """

    def __init__(self, crypto: CredentialCrypto, *, enabled: bool = True) -> None:
        """构造运行态。

        Args:
            crypto: 加解密器（密钥材料探测入口）。
            enabled: 初始开关（随后由启动期按持久化键校准）。
        """
        self.crypto = crypto
        self.enabled = enabled


# ---------------------------------------------------------------- 多仓库凭据加密编排（B1）


def _repo_id_for_cred_key(key: str) -> str:
    """凭据键 ``cred.<repo_id>.<field>`` → 该仓库标识；其余键落到 default。

    只有**三段及以上**才把第二段当仓库标识：``cred.<repo_id>.<field>`` 是 E5 per-repo
    布局，而 ``cred.<field>``（无仓库段的 legacy 两段形态）与全局键
    （``dump.password`` / ``notify.*`` / ``metrics.token`` 等）都不被任何仓库口令文件
    隔离——它们跨仓库共享，恒由 default 实例加密（与单实例时代一致）。
    """
    if key.startswith("cred."):
        rest = key[len("cred."):]
        parts = rest.split(".", 1)
        if len(parts) == 2 and parts[0]:
            return parts[0]
    return DEFAULT_REPO_ID


def owner_of(key: str) -> str:
    """该配置键由哪个密钥源保护（``cred.<repo_id>.<field>`` → 该仓库；其余落 default）。

    键归属的**唯一判定出口**：轮换 / 清理 / 回收 / 读取四处都问这里，不得再手抄前缀。
    """
    return _repo_id_for_cred_key(key)


def keys_of(repo_id: str, keys: Iterable[str]) -> set[str]:
    """给定密钥源保护的**敏感键**集合（纯判定，不读库——全量键由调用方传入）。

    Args:
        repo_id: 密钥源（仓库标识）。
        keys: 全量配置键（调用方取 ``ConfigRepo.list_all_raw()`` 的 key）。

    Returns:
        归属该密钥源的敏感键（``is_sensitive`` 口径）。非敏感明文键（``log.level`` 等）
        不入结果——调用方的 ``is_encrypted`` 过滤不该是唯一保险。
    """
    return {key for key in keys if is_sensitive(key) and owner_of(key) == repo_id}


def must_be_owned(repo_id: str) -> bool:
    """该密钥源是否必须**永久**被视为「有主」（孤儿口令文件回收不得动它）。

    ``default`` 恒为 True：全局敏感键（``dump.password`` / ``notify.*`` / ``metrics.token``）
    的密钥源就是 default 的口令文件——判它孤儿并回收 = 全局键永久解不开。
    """
    return repo_id == DEFAULT_REPO_ID


class CryptoProvider:
    """按仓库编排 :class:`CredentialCrypto` 实例（B1：落地 enc:v2 跨仓库密钥隔离）。

    - 每个 ``repo_id`` 惰性建一个实例并复用（实例内 ``(mtime_ns, size)`` 缓存按 HKDF
      info 分桶——本就 per-repo 隔离，换实例即换密钥，无需额外并发锁）；
    - 全局键（非 ``cred.*``）恒落到 ``DEFAULT_REPO_ID`` 实例，保持跨仓库共享语义；
    - 密钥源自各仓库 E5 per-repo 口令文件，故「单仓库口令文件更换只影响该仓库凭据」。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._cache: dict[str, CredentialCrypto] = {}

    def for_repo(self, repo_id: str) -> CredentialCrypto:
        """返回（并缓存）指定仓库的凭据加密实例。"""
        cached = self._cache.get(repo_id)
        if cached is None:
            cached = CredentialCrypto(self._settings, repo_id=repo_id)
            self._cache[repo_id] = cached
        return cached

    def for_key(self, key: str) -> CredentialCrypto:
        """返回该配置键应使用的凭据加密实例（按键名判定仓库归属）。"""
        return self.for_repo(_repo_id_for_cred_key(key))
