"""包加解密原语（单一真源）：PBKDF2-HMAC-SHA256 派生 + AES-256-GCM 认证加密。

本模块从 `services/config_transfer.py` 抽出于全站数据备份方案 v1.5 §6.1——两处包格式
（配置迁移包 / 全站备份包）用同一套参数与错误语义，任何第二份实现都算漂移：
PBKDF2 迭代数、salt/nonce 长度、算法名一旦分叉，跨版本包就会在另一条路径上解不开。

与配置迁移包的差别只有一处：本模块的 ``aad`` 参数允许把**明文头部**绑进 GCM 认证
（全站包用它绑 ``format/version/created_at``，改头即解不开）；既有调用方不传，行为不变。

S3：口令只在本模块内存里出现，不进日志/审计/异常消息；错口令与篡改统一走 GCM `InvalidTag`
（不区分「口令错」和「文件被改」——区分等于给爆破者一个判别器）。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.errors import PasswordWrongError, ValidationError

# 算法族标识（写进包的 encryption 头，解密端逐字比对）
ALGO_PBKDF2_SHA256 = "PBKDF2-HMAC-SHA256"
CIPHER_AES_256_GCM = "AES-256-GCM"

# PBKDF2 参数（OWASP 2023：PBKDF2-HMAC-SHA256 ≥ 600k 迭代）
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32

# 迭代数上限（防恶意包塞 iterations=10**9 把导入端 CPU 打死）
_MAX_ITERATIONS = 10_000_000

# 包口令下限（与仓库密码 ≥10 的口径对齐——它保护的是全部凭据）
PASSWORD_MIN_LENGTH = 10


def derive_key(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 派生 256-bit 密钥（600k 迭代）。"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def seal(
    payload: dict[str, Any],
    password: str,
    *,
    aad: bytes | None = None,
) -> tuple[dict[str, Any], str]:
    """加密载荷段，返回 ``(encryption 头, b64 密文)``。

    Args:
        payload: 待加密的 JSON 可序列化字典（凭据面只能进这里）。
        password: 用户口令明文（只在本函数内存中出现）。
        aad: 附加认证数据（不加密但参与 GCM 认证）；None = 不绑定，兼容既有配置迁移包。
    """
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = derive_key(password, salt)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sealed = AESGCM(key).encrypt(nonce, plaintext, aad)
    head = {
        "algo": ALGO_PBKDF2_SHA256,
        "iterations": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "cipher": CIPHER_AES_256_GCM,
    }
    blob = base64.b64encode(nonce + sealed).decode("ascii")
    return head, blob


def open_sealed(
    encryption: dict[str, Any],
    blob: str,
    password: str,
    *,
    aad: bytes | None = None,
) -> dict[str, Any]:
    """解密载荷段；错口令/篡改 → `PasswordWrongError`（GCM 认证失败）。

    Args:
        encryption: 包内 ``encryption`` 头（algo/iterations/salt/cipher）。
        blob: base64 密文（nonce 前缀 + sealed）。
        password: 用户口令明文。
        aad: 与加密端逐字一致的附加认证数据；对不上同样表现为认证失败。
    """
    if encryption.get("algo") != ALGO_PBKDF2_SHA256 or encryption.get("cipher") != CIPHER_AES_256_GCM:
        raise ValidationError("导出包加密算法不受支持，请使用本系统导出的文件")
    iterations = encryption.get("iterations")
    if not isinstance(iterations, int) or iterations < 1 or iterations > _MAX_ITERATIONS:
        raise ValidationError("导出包 PBKDF2 迭代参数非法")
    try:
        salt = base64.b64decode(str(encryption.get("salt", "")), validate=True)
        blob_bytes = base64.b64decode(blob, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValidationError("导出包加密段损坏") from exc
    if len(salt) != SALT_BYTES or len(blob_bytes) <= NONCE_BYTES:
        raise ValidationError("导出包加密段损坏")
    key = derive_key(password, salt)
    nonce, sealed = blob_bytes[:NONCE_BYTES], blob_bytes[NONCE_BYTES:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, aad)
    except InvalidTag as exc:
        raise PasswordWrongError("导出口令错误（或文件被篡改）") from exc
    try:
        return json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("导出包加密段内容损坏") from exc


__all__ = [
    "ALGO_PBKDF2_SHA256",
    "CIPHER_AES_256_GCM",
    "KEY_BYTES",
    "NONCE_BYTES",
    "PASSWORD_MIN_LENGTH",
    "PBKDF2_ITERATIONS",
    "SALT_BYTES",
    "derive_key",
    "open_sealed",
    "seal",
]
