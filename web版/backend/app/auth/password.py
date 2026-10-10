"""密码哈希封装（§8）：argon2id 优先，不可用时降级 bcrypt(cost=12)。

- argon2id：argon2-cffi 默认 PasswordHasher（默认 type 即 id 变体）；
- 降级：bcrypt cost=12，哈希串以 ``$2b$`` 前缀区分（§4.1 注释）；
- 明文密码不落库、不落日志；校验失败统一返回 False（不抛异常，避免细节泄漏）。
"""

from __future__ import annotations

import logging
import re
import string
from typing import Final

logger = logging.getLogger(__name__)

BCRYPT_PREFIX: Final[str] = "$2b$"
BCRYPT_ROUNDS: Final[int] = 12

# 强度校验（文档未定义具体阈值，按最简合理实现）：长度 ≥ 10 且至少含两类字符
MIN_PASSWORD_LENGTH: Final[int] = 10
REQUIRED_CHAR_CLASSES: Final[int] = 2

RANDOM_PASSWORD_LENGTH: Final[int] = 24
_RANDOM_ALPHABET: Final[str] = string.ascii_letters + string.digits

_LOWER = re.compile(r"[a-z]")
_UPPER = re.compile(r"[A-Z]")
_DIGIT = re.compile(r"[0-9]")
_SYMBOL = re.compile(r"[^A-Za-z0-9]")


def _load_backends() -> tuple[object | None, object | None]:
    """按可用性加载哈希后端（编译期选择，运行期常量）。

    Returns:
        ``(argon2_hasher, bcrypt_module)``，不可用时对应项为 None。
    """
    hasher: object | None = None
    bcrypt_mod: object | None = None
    try:
        from argon2 import PasswordHasher

        hasher = PasswordHasher()
    except ImportError:
        hasher = None
    try:
        import bcrypt as bcrypt_mod_import

        bcrypt_mod = bcrypt_mod_import
    except ImportError:
        bcrypt_mod = None
    return hasher, bcrypt_mod


_ARGON2_HASHER, _BCRYPT = _load_backends()


def backend_name() -> str:
    """返回当前生效的哈希后端名（``argon2id`` 或 ``bcrypt``）。"""
    return "argon2id" if _ARGON2_HASHER is not None else "bcrypt"


def is_bcrypt_hash(hashed: str) -> bool:
    """判断哈希串是否为 bcrypt 降级产物（``$2b$`` 前缀）。"""
    return hashed.startswith(BCRYPT_PREFIX)


def hash_password(plain: str) -> str:
    """对明文密码做哈希。

    Args:
        plain: 明文密码。

    Returns:
        哈希字符串（argon2id 串或 ``$2b$`` 前缀的 bcrypt 串）。

    Raises:
        RuntimeError: 无任何可用哈希后端。
    """
    if _ARGON2_HASHER is not None:
        return str(_ARGON2_HASHER.hash(plain))  # type: ignore[attr-defined]
    if _BCRYPT is not None:
        # bcrypt 仅取前 72 字节；超长密码先截断以保证行为确定
        raw = plain.encode("utf-8")[:72]
        return _BCRYPT.hashpw(raw, _BCRYPT.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")  # type: ignore[attr-defined]
    raise RuntimeError("无可用的密码哈希后端：请安装 argon2-cffi 或 bcrypt")


def verify_password(plain: str, hashed: str | None) -> bool:
    """校验明文密码与哈希是否匹配。

    Args:
        plain: 明文密码。
        hashed: 库中哈希；为空视为不匹配。

    Returns:
        匹配返回 True；任何异常（哈希损坏、后端异常）均返回 False。
    """
    if not hashed:
        return False
    try:
        if is_bcrypt_hash(hashed):
            if _BCRYPT is None:
                return False
            raw = plain.encode("utf-8")[:72]
            return bool(_BCRYPT.checkpw(raw, hashed.encode("utf-8")))  # type: ignore[attr-defined]
        if _ARGON2_HASHER is None:
            return False
        return bool(_ARGON2_HASHER.verify(hashed, plain))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 校验失败一律按不匹配处理，不泄漏细节
        return False


def needs_rehash(hashed: str) -> bool:
    """判断哈希是否需要重算（如从 bcrypt 降级态回到 argon2id）。

    Args:
        hashed: 库中哈希。

    Returns:
        需要重算返回 True。
    """
    if _ARGON2_HASHER is None:
        return not is_bcrypt_hash(hashed)
    return is_bcrypt_hash(hashed)


def validate_password_strength(plain: str) -> None:
    """校验新密码强度（E28 强度校验失败 422）。

    Args:
        plain: 待校验明文。

    Raises:
        ValidationError: 长度不足或字符类别不足。
    """
    from app.errors import ValidationError  # 延迟导入：errors 不依赖本模块，避免导入环

    if len(plain) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"密码长度至少 {MIN_PASSWORD_LENGTH} 位")
    classes = sum(
        1
        for pattern in (_LOWER, _UPPER, _DIGIT, _SYMBOL)
        if pattern.search(plain)
    )
    if classes < REQUIRED_CHAR_CLASSES:
        raise ValidationError("密码需至少包含小写字母、大写字母、数字、符号中的两类")


def generate_random_password(length: int = RANDOM_PASSWORD_LENGTH) -> str:
    """生成首启随机管理密码（24 位，大小写字母 + 数字）。

    Args:
        length: 密码长度，默认 24。

    Returns:
        随机密码明文（调用方负责一次性展示，不落库、不落日志）。
    """
    import secrets

    return "".join(secrets.choice(_RANDOM_ALPHABET) for _ in range(length))
