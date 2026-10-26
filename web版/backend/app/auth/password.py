"""密码哈希封装（§8）：argon2 单一后端。

- argon2：argon2-cffi 默认 PasswordHasher（默认 type 即 id 变体）；
- 明文密码不落库、不落日志；校验失败统一返回 False（不抛异常，避免细节泄漏）；
- v1 起 argon2-only：历史 ``$2b$`` 前缀的 bcrypt 哈希不再支持，
  verify_password 对其按不匹配处理（无法校验，需重置密码）。
"""

from __future__ import annotations

import logging
import re
import secrets
import string
from typing import Final

logger = logging.getLogger(__name__)

# 强度校验（文档未定义具体阈值，按最简合理实现）：长度 ≥ 10 且至少含两类字符
MIN_PASSWORD_LENGTH: Final[int] = 10
REQUIRED_CHAR_CLASSES: Final[int] = 2

# 首启随机管理密码长度：全站「自动生成密码」统一 32 位（owner 2026-10-27 指令）。
# 与仓库密码（constants.REPO_PASSWORD_LENGTH）同长度；字符集刻意保持「字母 + 数字」
# 不含符号——该密码要打印到启动日志 / 首启页由人抄录，符号易被 shell 与输入法吞掉。
RANDOM_PASSWORD_LENGTH: Final[int] = 32
_RANDOM_ALPHABET: Final[str] = string.ascii_letters + string.digits

_LOWER = re.compile(r"[a-z]")
_UPPER = re.compile(r"[A-Z]")
_DIGIT = re.compile(r"[0-9]")
_SYMBOL = re.compile(r"[^A-Za-z0-9]")


def _load_argon2():
    """加载 argon2 哈希后端（编译期选择，运行期常量）。

    Returns:
        argon2 PasswordHasher 实例；不可用时为 None（理论上不会发生，
        argon2-cffi 为本项目声明依赖）。
    """
    try:
        from argon2 import PasswordHasher

        return PasswordHasher()
    except ImportError:  # pragma: no cover - 依赖已声明，仅防御性兜底
        logger.critical("argon2-cffi 未安装，密码哈希后端不可用")
        return None


_ARGON2_HASHER = _load_argon2()


def hash_password(plain: str) -> str:
    """对明文密码做哈希。

    Args:
        plain: 明文密码。

    Returns:
        argon2 哈希字符串。

    Raises:
        RuntimeError: argon2 后端不可用。
    """
    if _ARGON2_HASHER is None:
        raise RuntimeError("无可用的密码哈希后端：argon2-cffi 未安装")
    return str(_ARGON2_HASHER.hash(plain))  # type: ignore[attr-defined]


def verify_password(plain: str, hashed: str | None) -> bool:
    """校验明文密码与哈希是否匹配。

    Args:
        plain: 明文密码。
        hashed: 库中哈希；为空视为不匹配。历史 bcrypt（``$2b$`` 前缀）哈希
            按不匹配处理（argon2-only 后不再支持，需重置密码）。

    Returns:
        匹配返回 True；任何异常（哈希损坏、后端异常、旧 bcrypt 哈希）均返回 False。
    """
    if not hashed:
        return False
    if _ARGON2_HASHER is None:
        return False
    try:
        return bool(_ARGON2_HASHER.verify(hashed, plain))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 校验失败一律按不匹配处理，不泄漏细节
        return False


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
    """生成首启随机管理密码（32 位，大小写字母 + 数字）。

    Args:
        length: 密码长度，默认 32（与全站生成密码统一）。

    Returns:
        随机密码明文（调用方负责一次性展示，不落库、不落日志）。
    """
    return "".join(secrets.choice(_RANDOM_ALPHABET) for _ in range(length))
