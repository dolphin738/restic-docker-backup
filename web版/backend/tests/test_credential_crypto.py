"""R-025 凭据加密存储（P3-A）——crypto 单测 + ConfigRepo 透明集成 + E07 开关链路。

锁死的不变量：
① 存储形态：敏感键落库为 ``enc:v2:`` 前缀密文（HKDF info 混入 repo_id）、非敏感键明文
（列不变、无 schema 迁移）；legacy ``enc:v1:`` 只读兼容，迁移重加密收敛 v2；
② 读侧透明：get / list_all / load_credentials 一律返回明文，**与开关无关**（崩溃安全）；
③ 开关崩溃安全序：启用=先落开关后迁移，关闭=先迁移后落开关，任一中断点混合形态可读；
④ 失败不静默：密码文件缺失 → 422 前置（且不产生部分写入）；密文损坏/错钥 → 500，
   且错误信息不含明文（S3）；
⑤ S3：API 响应与审计 detail 零明文泄露。
"""

from __future__ import annotations

import base64

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.db.database import Database
from app.db.repositories.config_repo import ConfigRepo
from app.errors import CredentialDecryptError, CredentialKeyUnavailableError
from app.services.crypto import (
    _HKDF_INFO_V1,
    PREFIX,
    PREFIX_V1,
    CredentialCrypto,
    EncryptionState,
    _hkdf_sha256,
)
from app.services.env_sync import load_credentials
from app.settings import Settings

SECRET = "s3cr3t-value-ÀÏ¶Ô"


def _make_crypto(tmp_path, monkeypatch, content: str = "repo-password") -> CredentialCrypto:
    """在 tmp 下按 E5 布局造密码文件并返回加解密器。"""
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    root = tmp_path / "repos"
    (root / "default").mkdir(parents=True, exist_ok=True)
    (root / "default" / "password").write_text(content, encoding="utf-8")
    return CredentialCrypto(Settings(passfile_root=str(root)))


# ---------------------------------------------------------------- crypto 单测


async def test_roundtrip_and_prefix(tmp_path, monkeypatch) -> None:
    """往返：加密产物带 enc:v2: 前缀，解密还原（含中文/长文边界）。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    for plain in (SECRET, "x", "中文凭据値", "z" * 4096):
        stored = crypto.encrypt(plain)
        assert stored.startswith(PREFIX)
        assert crypto.decrypt(stored) == plain
    # 足够长的明文不出现在密文里（单字符会随机命中 base64 字符集，不适用）
    assert SECRET not in crypto.encrypt(SECRET)


async def test_cross_instance_same_key(tmp_path, monkeypatch) -> None:
    """同一密码文件的不同实例派生同一密钥（跨实例可解密——装配重启不锁死）。"""
    crypto_a = _make_crypto(tmp_path, monkeypatch)
    crypto_b = _make_crypto(tmp_path, monkeypatch)
    stored = crypto_a.encrypt(SECRET)
    assert crypto_b.decrypt(stored) == SECRET


async def test_passfile_rotation_invalidates_key(tmp_path, monkeypatch) -> None:
    """密码文件被更换 ⇒ 旧密文解密失败（500 形态），新内容加解密正常。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    stored = crypto.encrypt(SECRET)
    (tmp_path / "repos" / "default" / "password").write_text("rotated-password", encoding="utf-8")
    with pytest.raises(CredentialDecryptError):
        crypto.decrypt(stored)
    assert crypto.decrypt(crypto.encrypt(SECRET)) == SECRET


async def test_tampered_ciphertext_rejected(tmp_path, monkeypatch) -> None:
    """GCM 认证：篡改密文任一位 ⇒ 解密失败（绝不静默通过）。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    stored = crypto.encrypt(SECRET)
    blob = bytearray(base64.b64decode(stored[len(PREFIX) :]))
    blob[-1] ^= 0x01
    tampered = PREFIX + base64.b64encode(bytes(blob)).decode("ascii")
    with pytest.raises(CredentialDecryptError):
        crypto.decrypt(tampered)


async def test_malformed_ciphertext_rejected(tmp_path, monkeypatch) -> None:
    """结构破坏（非 base64 / 过短）同样走 500 形态，不崩未捕获异常。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    for bad in (PREFIX + "!!!not-base64!!!", PREFIX + base64.b64encode(b"short").decode("ascii")):
        with pytest.raises(CredentialDecryptError):
            crypto.decrypt(bad)


async def test_missing_passfile_unavailable(tmp_path, monkeypatch) -> None:
    """密码文件缺失：available()=False，加解密均报 422 形态错误（不静默）。"""
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    crypto = CredentialCrypto(Settings(passfile_root=str(tmp_path / "nope")))
    assert crypto.available() is False
    with pytest.raises(CredentialKeyUnavailableError):
        crypto.encrypt(SECRET)


async def test_empty_passfile_unavailable(tmp_path, monkeypatch) -> None:
    """空密码文件视为不可用（几乎必然是误配置，不拿空材料派生密钥）。"""
    crypto = _make_crypto(tmp_path, monkeypatch, content="")
    assert crypto.available() is False
    with pytest.raises(CredentialKeyUnavailableError):
        crypto.encrypt(SECRET)


async def test_plaintext_passthrough(tmp_path, monkeypatch) -> None:
    """非密文形态原样返回（读侧双保险）；is_encrypted 前缀判定。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    assert crypto.decrypt("plain-value") == "plain-value"
    assert crypto.decrypt(None) is None  # type: ignore[arg-type]
    assert CredentialCrypto.is_encrypted("plain") is False
    assert CredentialCrypto.is_encrypted(None) is False
    assert CredentialCrypto.is_encrypted(crypto.encrypt("x")) is True


async def test_decrypt_error_hides_secrets(tmp_path, monkeypatch) -> None:
    """S3：解密失败的错误信息不含明文/密文。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    stored = crypto.encrypt(SECRET)
    (tmp_path / "repos" / "default" / "password").write_text("rotated", encoding="utf-8")
    with pytest.raises(CredentialDecryptError) as exc_info:
        crypto.decrypt(stored)
    assert SECRET not in str(exc_info.value)
    assert stored not in str(exc_info.value)


def test_encryption_state_default_enabled(tmp_path, monkeypatch) -> None:
    """owner 2026-10-16 裁决：默认开（缺省键 = enabled）。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    assert EncryptionState(crypto).enabled is True


# ---------------------------------------------------------------- ConfigRepo 集成


async def _repo(tmp_path, *, enabled: bool = True):
    root = tmp_path / "repos"
    (root / "default").mkdir(parents=True, exist_ok=True)
    (root / "default" / "password").write_text("repo-password", encoding="utf-8")
    db = Database(tmp_path / "cred.db")
    await db.connect()
    crypto = CredentialCrypto(Settings(passfile_root=str(root)))
    state = EncryptionState(crypto, enabled=enabled)
    return db, ConfigRepo(db, encryption=state), state


async def test_sensitive_write_encrypted_others_plain(tmp_path) -> None:
    """敏感键落库密文、非敏感键明文；读取两侧都透明还原明文。"""
    db, repo, _state = await _repo(tmp_path)
    await repo.set("cred.aws_secret_access_key", SECRET)
    await repo.set("repo.url", "/repo")

    raw = {row.key: row.value for row in await repo.list_all_raw()}
    assert raw["cred.aws_secret_access_key"].startswith(PREFIX)
    assert raw["repo.url"] == "/repo"

    assert await repo.get_value("cred.aws_secret_access_key") == SECRET
    assert await repo.get_value("repo.url") == "/repo"
    await db.close()


async def test_notify_mail_password_encrypted(tmp_path) -> None:
    """C2：``notify.mail.password`` 入敏感键清单 → 落库密文、读侧透明还原明文。"""
    db, repo, _state = await _repo(tmp_path)
    await repo.set("notify.mail.password", "app-password")
    await repo.set("notify.mail.host", "smtp.example.com")  # 非敏感键：明文

    raw = {row.key: row.value for row in await repo.list_all_raw()}
    assert raw["notify.mail.password"].startswith(PREFIX)
    assert raw["notify.mail.host"] == "smtp.example.com"

    assert await repo.get_value("notify.mail.password") == "app-password"
    await db.close()


async def test_migration_enable_disable_idempotent(tmp_path) -> None:
    """存量搬运幂等：明文→密文→再迁 0 条；密文→明文可逆。"""
    db, repo, state = await _repo(tmp_path, enabled=False)
    await repo.set("cred.b2_account_key", "b2-key")
    await repo.set("dump.password", "pg-pass")
    await repo.set("notify.webhook.url", "https://hook.example/token")

    raw = {row.key: row.value for row in await repo.list_all_raw()}
    assert all(not value.startswith(PREFIX) for value in raw.values())

    assert await repo.migrate_encryption(encrypt=True) == 3
    raw = {row.key: row.value for row in await repo.list_all_raw()}
    assert all(value.startswith(PREFIX) for value in raw.values())
    # 读侧与开关无关：此时 enabled=False，仍能透透明读明文
    assert await repo.get_value("cred.b2_account_key") == "b2-key"
    # 幂等：已就位不重复搬运
    assert await repo.migrate_encryption(encrypt=True) == 0

    assert await repo.migrate_encryption(encrypt=False) == 3
    raw = {row.key: row.value for row in await repo.list_all_raw()}
    assert all(not value.startswith(PREFIX) for value in raw.values())
    assert await repo.get_value("dump.password") == "pg-pass"
    await db.close()


async def test_mixed_storage_readable_when_enabled(tmp_path) -> None:
    """崩溃安全：开关开着但库里有残留明文（迁移中断形态）——读取不炸、不自行改写。"""
    db, repo, state = await _repo(tmp_path, enabled=False)
    await repo.set("cred.aws_access_key_id", "AKIA-LEFT")
    state.enabled = True  # 模拟：启用后迁移尚未跑完

    assert await repo.get_value("cred.aws_access_key_id") == "AKIA-LEFT"
    raw = {row.key: row.value for row in await repo.list_all_raw()}
    assert not raw["cred.aws_access_key_id"].startswith(PREFIX)  # 读不改写存储
    await db.close()


async def test_mask_has_value_on_ciphertext(tmp_path) -> None:
    """脱敏口径对密文不变：has_value=True、不回 value 键。"""
    db, repo, _state = await _repo(tmp_path)
    await repo.set("cred.aws_secret_access_key", SECRET)
    row = await repo.get("cred.aws_secret_access_key")
    assert row is not None
    masked = repo.mask(row)
    assert masked["has_value"] is True
    assert masked["sensitive"] is True
    assert "value" not in masked
    await db.close()


async def test_ciphertext_without_component_rejected(tmp_path) -> None:
    """装配缺失（无 EncryptionState）而库里有密文 ⇒ 报错，绝不把密文当明文透出。"""
    db, repo, _state = await _repo(tmp_path)
    await repo.set("cred.aws_secret_access_key", SECRET)
    raw = {row.key: row.value for row in await repo.list_all_raw()}
    await db.close()

    db2 = Database(tmp_path / "cred2.db")
    await db2.connect()
    await db2.write(
        "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, 'ui', 'now')",
        ("cred.aws_secret_access_key", raw["cred.aws_secret_access_key"]),
    )
    bare_repo = ConfigRepo(db2)  # 无加密组件
    with pytest.raises(CredentialDecryptError):
        await bare_repo.get_value("cred.aws_secret_access_key")
    await db2.close()


async def test_load_credentials_decrypts(tmp_path) -> None:
    """cred.* → 子进程 env 的唯一出口：密文入库、明文出站（AWS_/B2_ 还原规则不变）。"""
    db, repo, _state = await _repo(tmp_path)
    await repo.set("cred.default.aws_secret_access_key", SECRET)
    await repo.set("cred.default.b2_account_key", "b2-key")
    env = await load_credentials(repo)
    assert env == {"AWS_SECRET_ACCESS_KEY": SECRET, "B2_ACCOUNT_KEY": "b2-key"}
    await db.close()


async def test_recovery_after_passfile_rotation(tmp_path) -> None:
    """密码文件被更换后：旧密文读不出（500 形态）但**恢复路径不堵死**——
    set/delete 的 env 守卫只读 source 列不解密旧值 ⇒ 该键可覆写/可删；
    关闭迁移遇解不开的键跳过并告警，不中断整批。"""
    db, repo, _state = await _repo(tmp_path)
    await repo.set("cred.aws_secret_access_key", SECRET)
    passfile = tmp_path / "repos" / "default" / "password"

    passfile.write_text("rotated-password", encoding="utf-8")
    with pytest.raises(CredentialDecryptError):
        await repo.get_value("cred.aws_secret_access_key")

    # 恢复手段一：E07 覆写（新值按新密钥加密，立即可读）
    await repo.set("cred.aws_secret_access_key", "new-secret")
    assert await repo.get_value("cred.aws_secret_access_key") == "new-secret"

    # 再造一个解不开的键，验证关闭迁移跳过而非中断
    await repo.set("cred.b2_account_key", "b2-key")
    passfile.write_text("third-password", encoding="utf-8")
    assert await repo.migrate_encryption(encrypt=False) == 0

    # 恢复手段二：删除（守卫同样不解密旧值）
    assert await repo.delete("cred.b2_account_key") is True
    assert await repo.get_value("cred.b2_account_key") is None
    await db.close()


# ---------------------------------------------------------------- E07 开关链路（API）


async def test_e07_cred_write_encrypted_at_rest(app_client) -> None:
    """E07 写凭据 ⇒ 落库密文；E06 只回 has_value；响应体与审计零明文（S3）。"""
    ns = app_client
    await finish_bootstrap(ns)
    client = ns.client

    resp = await client.put(
        "/api/config",
        json={"patch": {"cred.aws_secret_access_key": SECRET}},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert SECRET not in resp.text

    raw = await ns.app.state.db.fetch_one(
        "SELECT value FROM config WHERE key = 'cred.aws_secret_access_key'"
    )
    assert raw["value"].startswith(PREFIX)

    resp = await client.get("/api/config")
    assert resp.status_code == 200
    item = next(i for i in resp.json()["items"] if i["key"] == "cred.aws_secret_access_key")
    assert item["has_value"] is True and item["sensitive"] is True
    assert item["value"] is None  # 敏感项不回明文（契约字段恒序列化，值为 null）
    assert SECRET not in resp.text

    # 审计 detail 只记键名（S3：无明文）
    audit_row = await ns.app.state.db.fetch_one(
        "SELECT detail FROM audit_logs WHERE action = 'config.update' ORDER BY id DESC"
    )
    assert SECRET not in audit_row["detail"]

    # 透明读回
    assert await ns.app.state.config_repo.get_value("cred.aws_secret_access_key") == SECRET


async def test_toggle_off_then_on_roundtrip(app_client) -> None:
    """开关往返：关=存量密文转明文+新写明文；开=存量明文转密文+新写密文。"""
    ns = app_client
    await finish_bootstrap(ns)
    client = ns.client
    db = ns.app.state.db

    await client.put("/api/config", json={"patch": {"cred.b2_account_key": "b2-key"}}, headers=ORIGIN)

    # 关
    resp = await client.put(
        "/api/config", json={"patch": {"security.cred_encryption": "0"}}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    raw = await db.fetch_one("SELECT value FROM config WHERE key = 'cred.b2_account_key'")
    assert raw["value"] == "b2-key"
    resp = await client.get("/api/config")
    flag = next(i for i in resp.json()["items"] if i["key"] == "security.cred_encryption")
    assert flag["value"] == "0"

    # 关闭态新写仍明文
    await client.put("/api/config", json={"patch": {"cred.b2_account_id": "acct"}}, headers=ORIGIN)
    raw = await db.fetch_one("SELECT value FROM config WHERE key = 'cred.b2_account_id'")
    assert raw["value"] == "acct"

    # 开
    resp = await client.put(
        "/api/config", json={"patch": {"security.cred_encryption": "1"}}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    for key in ("cred.b2_account_key", "cred.b2_account_id"):
        raw = await db.fetch_one("SELECT value FROM config WHERE key = ?", (key,))
        assert raw["value"].startswith(PREFIX), key
    flag = next(
        i for i in (await client.get("/api/config")).json()["items"]
        if i["key"] == "security.cred_encryption"
    )
    assert flag["value"] == "1"


async def test_flag_value_domain_guard(app_client) -> None:
    """开关只收 0/1（_BOOL_KEYS 值域前置 422）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.put(
        "/api/config", json={"patch": {"security.cred_encryption": "2"}}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_enable_without_passfile_422_no_partial_write(make_app, monkeypatch, tmp_path) -> None:
    """密码文件缺失：启用/写敏感键均 422 前置，且**不产生部分写入**（repo.url 不落库）。"""
    empty_root = tmp_path / "no-repos"
    empty_root.mkdir()
    ns = await make_app({"PASSFILE_ROOT": str(empty_root)})
    await finish_bootstrap(ns)
    client = ns.client

    # 默认开 + 无密码文件：启动不崩（warning 路径），写敏感键 422
    resp = await client.put(
        "/api/config", json={"patch": {"cred.aws_access_key_id": "AKIA"}}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

    # 启用开关顺带写非敏感键：整体 422，repo.url 不得落库（无部分写入契约）
    resp = await client.put(
        "/api/config",
        json={"patch": {"security.cred_encryption": "1", "repo.url": "/repo"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert await ns.app.state.config_repo.get_value("repo.url") is None


async def test_startup_migrates_legacy_plaintext(make_app) -> None:
    """默认开：二次启动同一 DB 时，存量明文敏感键自动迁为密文（升级路径零操作）。"""
    ns = await make_app()
    await finish_bootstrap(ns)

    # 人为造「升级前」形态：关开关写明文，再恢复开（不触发迁移）
    state = ns.app.state.encryption
    state.enabled = False
    await ns.app.state.config_repo.set("cred.aws_secret_access_key", "legacy-plain")
    state.enabled = True
    raw = await ns.app.state.db.fetch_one(
        "SELECT value FROM config WHERE key = 'cred.aws_secret_access_key'"
    )
    assert not raw["value"].startswith(PREFIX)

    # 二次启动（同一 DATA_DIR）：lifespan 重跑，启动期自动迁移
    ns2 = await make_app()
    raw = await ns2.app.state.db.fetch_one(
        "SELECT value FROM config WHERE key = 'cred.aws_secret_access_key'"
    )
    assert raw["value"].startswith(PREFIX)
    assert await ns2.app.state.config_repo.get_value("cred.aws_secret_access_key") == "legacy-plain"


# ---------------------------------------------------------------- v2 隔离与 v1 兼容（审计 2026-10-16 🟡-1）


def _make_v1_ciphertext(crypto: CredentialCrypto, plaintext: str) -> str:
    """按 legacy v1 口径（固定 info、无仓库隔离）构造密文（模拟 R-025 首版产物）。"""
    secret = crypto.passfile().read_bytes()
    key = _hkdf_sha256(secret, info=_HKDF_INFO_V1)
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return PREFIX_V1 + base64.b64encode(nonce + sealed).decode("ascii")


def test_v2_cross_repo_isolation(tmp_path, monkeypatch) -> None:
    """同密码文件内容 + 不同 repo_id ⇒ 不同密钥：跨仓库密文互不可解（enc:v2: 隔离）。"""
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    root = tmp_path / "repos"
    # 两个仓库的密码文件内容完全相同——隔离性正是要防「运维复用密码」的形态
    for repo_id in ("repo-a", "repo-b"):
        (root / repo_id).mkdir(parents=True, exist_ok=True)
        (root / repo_id / "password").write_text("same-password", encoding="utf-8")
    settings = Settings(passfile_root=str(root))

    crypto_a = CredentialCrypto(settings, repo_id="repo-a")
    crypto_b = CredentialCrypto(settings, repo_id="repo-b")

    stored = crypto_a.encrypt("cross-repo-secret")
    assert stored.startswith("enc:v2:")
    with pytest.raises(CredentialDecryptError):
        crypto_b.decrypt(stored)  # repo-b 的密钥解不开 repo-a 的密文
    assert crypto_a.decrypt(stored) == "cross-repo-secret"  # 本仓库自解正常


def test_v1_legacy_decrypt_and_reencrypt(tmp_path, monkeypatch) -> None:
    """v1 密文只读兼容：legacy info 解密成功；reencrypt 收敛 v2 且明文不变。"""
    crypto = _make_crypto(tmp_path, monkeypatch)
    legacy = _make_v1_ciphertext(crypto, SECRET)
    assert legacy.startswith(PREFIX_V1)
    assert crypto.is_encrypted(legacy)
    assert crypto.needs_reencrypt(legacy)

    assert crypto.decrypt(legacy) == SECRET  # v1 只读兼容路径

    upgraded = crypto.reencrypt(legacy)
    assert upgraded.startswith("enc:v2:")
    assert crypto.decrypt(upgraded) == SECRET
    assert not crypto.needs_reencrypt(upgraded)


async def test_migrate_reencrypts_v1_to_v2(tmp_path, monkeypatch) -> None:
    """启用迁移三形态收敛：明文→v2、legacy v1→v2、v2 保持；读侧明文恒正确。"""
    db = Database(tmp_path / "reencrypt.db")
    await db.connect()
    crypto = _make_crypto(tmp_path, monkeypatch)
    repo = ConfigRepo(db, encryption=EncryptionState(crypto, enabled=True))

    plaintext_key = "cred.default.aws_secret_access_key"
    await repo._upsert(plaintext_key, "plain-value", "user", "2026-01-01T00:00:00Z")
    v1_value = _make_v1_ciphertext(crypto, "legacy-value")
    await repo._upsert("cred.default.b2_account_key", v1_value, "user", "2026-01-01T00:00:00Z")
    v2_value = crypto.encrypt("already-v2")
    await repo._upsert("cred.default.aws_access_key_id", v2_value, "user", "2026-01-01T00:00:00Z")

    changed = await repo.migrate_encryption(encrypt=True)
    assert changed == 2  # 明文与 v1 各 +1；v2 已就位不计

    # 落库形态（raw）：明文键与 v1 键都已收敛为 enc:v2: 密文
    raw = {r.key: r.value for r in await repo.list_all_raw()}
    assert raw[plaintext_key].startswith("enc:v2:")
    assert crypto.decrypt(raw[plaintext_key]) == "plain-value"
    assert raw["cred.default.b2_account_key"].startswith("enc:v2:")
    assert crypto.decrypt(raw["cred.default.b2_account_key"]) == "legacy-value"
    assert raw["cred.default.aws_access_key_id"].startswith("enc:v2:")
    assert crypto.decrypt(raw["cred.default.aws_access_key_id"]) == "already-v2"

    # 读侧透明：get 返回明文，与开关无关
    row_v2 = await repo.get(plaintext_key)
    assert row_v2 is not None and row_v2.value == "plain-value"
    row_up = await repo.get("cred.default.b2_account_key")
    assert row_up is not None and row_up.value == "legacy-value"

    # 幂等：重跑零改动
    assert await repo.migrate_encryption(encrypt=True) == 0
    await db.close()
