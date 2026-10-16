"""凭据加密存储（R-025 / P3-A；架构 §4.3 演进位，owner 2026-10-16 裁决：默认开）。

设计（架构 §4.3 注既定，本模块是其唯一实现点）：

- **密钥源自仓库密码文件**：HKDF-SHA256（RFC 5869，hmac 实现，不引入 KDF 依赖）
  从 E5 per-repo 布局的密码文件内容派生 256-bit 密钥。密码文件是「加密根密钥、
  永不入库」——数据库文件单独泄露不再等于凭据泄露；代价是密码文件成为单点
  （丢失/被换 ⇒ 凭据全部解不开，与 restic 仓库本身同源）。
- **AES-256-GCM 认证加密**（pyca/cryptography）：篡改、错钥可检测，绝不静默通过。
- **存储格式** ``enc:v1:<base64(nonce‖ciphertext‖tag)>``：读侧**恒按前缀判定**，
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
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.errors import CredentialDecryptError, CredentialKeyUnavailableError
from app.settings import Settings, get_settings

# 密文格式前缀（版本化：未来换算法/参数只增前缀，旧前缀只读兼容）
PREFIX = "enc:v1:"

_NONCE_BYTES = 12          # AES-GCM 标准 nonce 长度（96 bit）
_KEY_BYTES = 32            # AES-256
_HKDF_SALT = b"restic-web/credential-encryption/v1"
_HKDF_INFO = b"config-credential-encryption-key"


def _hkdf_sha256(secret: bytes, *, length: int = _KEY_BYTES) -> bytes:
    """RFC 5869 HKDF-SHA256（hmac 实现）。

    Args:
        secret: 输入密钥材料（仓库密码文件原始字节）。
        length: 派生长度（字节）。

    Returns:
        派生密钥。
    """
    prk = hmac.new(_HKDF_SALT, secret, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + _HKDF_INFO + bytes([counter]), hashlib.sha256).digest()
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
        self._cache: _KeyCache | None = None

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
        """派生密钥（带 (mtime_ns, size) 缓存；文件被换立即失效）。

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
        cached = self._cache
        if cached is not None and cached.stat == (stat.st_mtime_ns, stat.st_size):
            return cached.key
        key = _hkdf_sha256(secret)
        self._cache = _KeyCache(stat=(stat.st_mtime_ns, stat.st_size), key=key)
        return key

    # ----------------------------------------------------------- 编解码

    @staticmethod
    def is_encrypted(value: str | None) -> bool:
        """判断存储值是否为密文形态（``enc:v1:`` 前缀）。"""
        return bool(value) and value.startswith(PREFIX)

    def encrypt(self, plaintext: str) -> str:
        """加密明文，返回 ``enc:v1:<base64>`` 形态密文。

        Raises:
            CredentialKeyUnavailableError: 密码文件缺失/不可读/为空。
        """
        key = self._derive_key()
        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, stored: str) -> str:
        """解密密文（非密文形态原样返回——调用方已按前缀过滤，双保险）。

        Raises:
            CredentialDecryptError: 密文损坏、被篡改或密钥不匹配（密码文件被更换）。
                明文/密文均不进错误信息（S3）。
        """
        if not self.is_encrypted(stored):
            return stored
        try:
            blob = base64.b64decode(stored[len(PREFIX) :], validate=True)
            nonce, sealed = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
            plaintext = AESGCM(self._derive_key()).decrypt(nonce, sealed, None)
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            # binascii.Error 是 ValueError 子类；InvalidTag 覆盖篡改/错钥
            raise CredentialDecryptError() from exc


class EncryptionState:
    """凭据加密运行期开关（进程内单一事实源；持久化于 config 键 ``security.cred_encryption``）。

    - ``enabled`` 只决定**写侧**是否加密；读侧恒按 ``enc:v1:`` 前缀判定（崩溃安全）；
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
