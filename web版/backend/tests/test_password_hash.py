"""密码哈希（argon2-only）护栏（审查💭①：verify 对历史 bcrypt 哈希按不匹配处理）。

argon2-only 语义：``$2b$`` 前缀的 bcrypt 哈希不再可校验（verify_password 一律 False，
用户需重置密码）；正常 argon2 哈希 roundtrip 匹配；明文不落任何输出。
"""

from app.auth.password import hash_password, validate_password_strength, verify_password

_BCRYPT_HASH = "$2b$12$KIXQxJ9t1sKrGqZovkP3XuJhGvV5oXb1cO4Wl2YpBmSdN0eQ1fF2G"


class TestVerifyPassword:
    def test_argon2_roundtrip(self) -> None:
        hashed = hash_password("correct-horse-1")
        assert hashed.startswith("$argon2")
        assert verify_password("correct-horse-1", hashed) is True
        assert verify_password("wrong-horse-2", hashed) is False

    def test_bcrypt_hash_rejected_as_mismatch(self) -> None:
        # argon2-only：$2b$ 前缀不可校验，按不匹配处理（返回 False 而非抛异常）
        assert verify_password("correct-horse-1", _BCRYPT_HASH) is False

    def test_empty_or_none_hash_is_mismatch(self) -> None:
        assert verify_password("whatever-1", "") is False
        assert verify_password("whatever-1", None) is False

    def test_corrupted_hash_is_mismatch(self) -> None:
        assert verify_password("whatever-1", "$argon2id$v=19$m=65536,t=3,p=4$bogus") is False


class TestValidatePasswordStrength:
    def test_too_short_rejected(self) -> None:
        import pytest

        from app.errors import ValidationError

        with pytest.raises(ValidationError):
            validate_password_strength("Ab1efghij")  # 9 位

    def test_single_class_rejected(self) -> None:
        import pytest

        from app.errors import ValidationError

        with pytest.raises(ValidationError):
            validate_password_strength("abcdefghij")  # 10 位但单类

    def test_ok_passes(self) -> None:
        validate_password_strength("Ab1efghijk")  # 10 位两类
