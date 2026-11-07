"""全站备份包服务层（services/site_backup.py）单测 —— W3 验收门。

锁死六条不变量（方案 §10.2 W3 验证门）：
1. AAD 绑头：改外层头部即解不开（与错口令同一句提示，不给爆破者判别器）；
2. 口令错 → 401 PASSWORD_WRONG；
3. 包内 conf 加密态与目标机不一致 → 422 blockers；
4. conf-backup.conf 不入包（那是 rclone 自己的备份副本）；
5. rclone.pass 往返逐字节一致；
6. 连导 4 次只留 3 个回滚点（site_restore_keep_count）。

另含 §1.6 三条 fail-closed：仓库密码文件缺失拒绝导出、conf 已加密但口令不可读拒绝导出、
env 来源的 config 归一为 ui。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.constants import SITE_RESTORE_KEEP_COUNT
from app.db.database import Database
from app.db.repositories.config_repo import ConfigRepo
from app.errors import (
    InternalError,
    JobConflictError,
    PasswordWrongError,
    SitePackInvalidError,
    SitePassfileIncompleteError,
    SiteSchemaUnsupportedError,
    SiteTargetNotEmptyError,
    ValidationError,
)
from app.services import site_backup
from app.settings import Settings

PASSWORD = "Str0ngSitePass1"
ENCRYPTED_CONF = b"# Encrypted rclone configuration File\n\nRCLONE_ENCRYPT_V0:\nAAAA\n"
PLAIN_CONF = b"[mycrypt]\ntype = crypt\nremote = /home/222\n"
RCLONE_PASS = b"rclone-config-passphrase"


class _StubScheduler:
    def __init__(self) -> None:
        self.reloads = 0

    async def reload(self) -> None:
        self.reloads += 1


class _StubRestic:
    def __init__(self) -> None:
        self.tz: list[str | None] = []

    def set_timezone(self, tz_name: str | None) -> None:
        self.tz.append(tz_name)


def _settings(tmp_path: Path, *, rclone_config: str | None) -> Settings:
    """隔离的 Settings：data / passfile / conf 全在 tmp_path 下。

    restic_binary 指向当前解释器：shutil.which 必须命中，否则 §4.3 第 6 步 3 恒失败。
    """
    return Settings(
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        passfile_root=str(tmp_path / "repos"),
        rclone_config=rclone_config,
        restic_binary=sys.executable,
        site_restore_keep_count=SITE_RESTORE_KEEP_COUNT,
    )


async def _seed_target(db: Database, *, with_url: bool = True) -> None:
    """目标机最小既有数据：default 仓库（可选 url）+ 一个任务 + 一个会话 + 一条配置。"""
    if with_url:
        await db.write("UPDATE repos SET url = ? WHERE id = 'default'", ("/data/repos/default",))
    await db.write(
        "INSERT INTO backup_tasks (id, repo_id, name, tag, sources, excludes, created_at, updated_at) "
        "VALUES (1, 'default', '旧任务', 'task-1', '[]', '[]', ?, ?)",
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    await db.write(
        "INSERT INTO sessions (token_hash, last_seen_at, expires_at, created_at) VALUES (?, ?, ?, ?)",
        ("old-session", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    await db.write(
        "INSERT INTO config (key, value, source, updated_at) VALUES ('timezone', 'UTC', 'ui', ?)",
        ("2026-01-01T00:00:00Z",),
    )
    await db.write(
        "INSERT INTO admin_account (id, password_hash, must_change, updated_at) VALUES (1, ?, 0, ?)",
        ("$argon2id$target-old-hash", "2026-01-01T00:00:00Z"),
    )


async def _make_app(
    tmp_path: Path,
    *,
    conf: bytes | None = PLAIN_CONF,
    passfile: bytes | None = b"repo-password",
    rclone_pass: bytes | None = None,
    seed: bool = True,
):
    """建一个够用的 app.state：真 Database + 真 ConfigRepo + 两个热生效 stub。"""
    conf_path = tmp_path / "rclone" / "rclone.conf"
    if conf is not None:
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        conf_path.write_bytes(conf)
    settings = _settings(tmp_path, rclone_config=str(conf_path))
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.passfile_root).mkdir(parents=True, exist_ok=True)
    if passfile is not None:
        (Path(settings.passfile_root) / "default").mkdir(parents=True, exist_ok=True)
        (Path(settings.passfile_root) / "default" / "password").write_bytes(passfile)
    if rclone_pass is not None:
        (Path(settings.passfile_root) / "rclone.pass").write_bytes(rclone_pass)
    db = Database(settings.db_path)
    await db.connect()
    if seed:
        await _seed_target(db, with_url=passfile is not None)
    return SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            db=db,
            config_repo=ConfigRepo(db),
            scheduler=_StubScheduler(),
            restic=_StubRestic(),
        )
    )


async def _schema(app) -> int:
    return await app.state.db.user_version()


def _export_pack() -> dict:
    """一份结构合法的载荷（不经过 collect_state，省掉 DB 依赖）。"""
    return {
        "schema_version": 10,
        "app_version": "1.0.0",
        "created_at": "2026-02-02T00:00:00Z",
        "source": {"deploy_mode": "docker", "deploy_env": {"TZ": "UTC"}},
        "admin": {"password_hash": "$argon2id$v=19$m=65536,t=3,p=4$packed", "must_change": 0},
        "repos": [
            {
                "id": "default",
                "name": "搬来的默认仓库",
                "url": "/data/repos/default",
                "host_tag": "host-a",
                "url_source": "env",
                "host_tag_source": "env",
                "created_at": "2026-02-02T00:00:00Z",
                "deleted_at": None,
            }
        ],
        "tasks": [
            {
                "id": 7,
                "repo_id": "default",
                "name": "搬来的任务",
                "enabled": 1,
                "tag": "pending",
                "sources": '["/sources/etc"]',
                "excludes": "[]",
                "cron": "0 3 * * *",
                "created_at": "2026-02-02T00:00:00Z",
                "updated_at": "2026-02-02T00:00:00Z",
            }
        ],
        "config": [
            {"key": "timezone", "value": "Asia/Shanghai", "source": "env"},
            {"key": "log.level", "value": "debug", "source": "ui"},
        ],
        "passfiles": [
            {
                "repo_id": "default",
                "content_b64": base64.b64encode(b"pack-repo-password").decode("ascii"),
                "sha256": hashlib.sha256(b"pack-repo-password").hexdigest(),
                "mtime_ns": 1,
            }
        ],
        "rclone": {
            "included": True,
            "conf_path_hint": "/etc/rclone/rclone.conf",
            "conf_sha256": hashlib.sha256(PLAIN_CONF).hexdigest(),
            "conf_b64": base64.b64encode(PLAIN_CONF).decode("ascii"),
            "conf_encrypted": False,
            "has_crypt_section": True,
            "forms": [
                {
                    "remote": "backup",
                    "field": "pass",
                    "obscured": 1,
                    "value_sha256": "digest",
                    "updated_at": "2026-02-02T01:02:03Z",
                }
            ],
            "pass": {"included": False, "content_b64": None, "sha256": None, "mtime_ns": None},
        },
        "warnings": [],
    }


@pytest.fixture()
def env_calls(monkeypatch) -> list[tuple[str, object]]:
    """拦下 C3 的进程 env 写入（真实现会往临时目录写脚本，测试不必真落盘）。"""
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(site_backup, "set_password_command_env", lambda path: calls.append(("set", path)))
    monkeypatch.setattr(site_backup, "clear_password_command_env", lambda: calls.append(("clear", None)))
    return calls


# ---------------------------------------------------------------- 包格式


async def test_pack_roundtrip_preserves_payload():
    """build_site_pack → parse_site_pack 往返后载荷逐键一致，created_at 与头部对齐。"""
    state = _export_pack()
    pack = site_backup.build_site_pack(state, PASSWORD)
    assert pack["format"] == site_backup.PACK_FORMAT
    assert pack["version"] == site_backup.PACK_VERSION
    assert set(pack["encryption"]) == {"algo", "iterations", "salt", "cipher", "aad"}

    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert parsed["created_at"] == pack["created_at"]
    assert parsed["passfiles"] == state["passfiles"]
    assert parsed["tasks"] == state["tasks"]


async def test_aad_binds_header():
    """改外层 created_at：AAD 对不上 → 与错口令同一句提示（不给爆破者判别器）。"""
    pack = site_backup.build_site_pack(_export_pack(), PASSWORD)
    pack["created_at"] = "2026-03-03T00:00:00Z"
    with pytest.raises(PasswordWrongError):
        await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)


async def test_wrong_password_rejected():
    pack = site_backup.build_site_pack(_export_pack(), PASSWORD)
    with pytest.raises(PasswordWrongError):
        await site_backup.parse_site_pack(pack, "WrongPassword9", target_schema=10)


def test_short_password_rejected():
    with pytest.raises(ValidationError):
        site_backup.build_site_pack(_export_pack(), "short")


async def test_legacy_config_pack_gets_dedicated_message():
    """旧配置迁移包（format 不同）必须给「已停止支持」而不是笼统的「不是有效包」。"""
    with pytest.raises(SitePackInvalidError) as excinfo:
        await site_backup.parse_site_pack(
            {"format": "restic-web-config-export", "version": 2}, PASSWORD, target_schema=10
        )
    assert "旧版配置迁移文件" in excinfo.value.message


async def test_version_unsupported():
    pack = site_backup.build_site_pack(_export_pack(), PASSWORD)
    pack["version"] = 99
    with pytest.raises(SitePackInvalidError):
        await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)


async def test_payload_missing_key_rejected():
    state = _export_pack()
    del state["warnings"]
    pack = site_backup.build_site_pack(state, PASSWORD)
    with pytest.raises(SitePackInvalidError) as excinfo:
        await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert "warnings" in excinfo.value.message


async def test_schema_newer_than_target_rejected():
    state = _export_pack()
    state["schema_version"] = 99
    pack = site_backup.build_site_pack(state, PASSWORD)
    with pytest.raises(SiteSchemaUnsupportedError):
        await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)


# ---------------------------------------------------------------- 导出：fail-closed


async def test_export_refuses_when_passfile_missing(tmp_path):
    """仓库密码文件缺失 → 拒绝导出（能导不能读的包比不给包更糟，§1.6-4）。"""
    app = await _make_app(tmp_path, passfile=None)
    with pytest.raises(SitePassfileIncompleteError) as excinfo:
        await site_backup.collect_state(app)
    assert excinfo.value.data["repo_ids"] == ["default"]


async def test_export_refuses_when_rclone_pass_unreadable(tmp_path):
    """conf 已加密但 rclone.pass 缺失 → 拒绝导出（那种包在新机解不开）。"""
    app = await _make_app(tmp_path, conf=ENCRYPTED_CONF, rclone_pass=None)
    with pytest.raises(SitePassfileIncompleteError) as excinfo:
        await site_backup.collect_state(app)
    assert "配置口令" in excinfo.value.message


async def test_export_collects_rclone_pass_bytes(tmp_path):
    app = await _make_app(tmp_path, conf=ENCRYPTED_CONF, rclone_pass=RCLONE_PASS)
    state = await site_backup.collect_state(app)
    rclone = state["rclone"]
    assert rclone["included"] is True
    assert rclone["conf_encrypted"] is True
    assert base64.b64decode(rclone["pass"]["content_b64"]) == RCLONE_PASS


async def test_conf_backup_copy_never_enters_pack(tmp_path):
    """rclone 自己的 conf 备份副本不进包：它不属于配置本体。"""
    app = await _make_app(tmp_path)
    backup = tmp_path / "rclone" / "conf-backup.conf"
    backup.write_bytes(b"MARKER-CONF-BACKUP-CONTENT")
    state = await site_backup.collect_state(app)
    snapshot = json.dumps(state, ensure_ascii=False)
    assert "MARKER-CONF-BACKUP-CONTENT" not in snapshot
    assert set(state["rclone"]) == {
        "included",
        "conf_path_hint",
        "conf_sha256",
        "conf_b64",
        "conf_encrypted",
        "has_crypt_section",
        "forms",
        "pass",
    }


async def test_conf_missing_is_not_an_error(tmp_path):
    """源机没配 rclone.conf 不算错误：included=false，不抛（§3.3-5）。"""
    app = await _make_app(tmp_path, conf=None)
    state = await site_backup.collect_state(app)
    assert state["rclone"]["included"] is False
    assert state["rclone"]["conf_b64"] is None
    assert site_backup._CONF_MISSING_WARNING in state["warnings"]


async def test_env_config_source_normalized_to_ui(tmp_path):
    """env 来源的 config 归一为 ui，并留一条 warning（§1.6-1）。"""
    app = await _make_app(tmp_path)
    await app.state.db.write("UPDATE config SET source = 'env' WHERE key = 'timezone'")
    state = await site_backup.collect_state(app)
    row = next(item for item in state["config"] if item["key"] == "timezone")
    assert row["source"] == "ui"
    assert any("归一" in warning for warning in state["warnings"])


async def test_runtime_columns_stripped_from_tasks(tmp_path):
    """运行期列不进包，id 与 tag 保留（§1.6-3）。"""
    app = await _make_app(tmp_path)
    await app.state.db.write(
        "UPDATE backup_tasks SET last_run_at = ?, dry_run_pending = 1 WHERE id = 1",
        ("2026-05-05T00:00:00Z",),
    )
    state = await site_backup.collect_state(app)
    task = state["tasks"][0]
    assert "last_run_at" not in task
    assert "dry_run_pending" not in task
    assert task["id"] == 1
    assert task["tag"] == "task-1"


# ---------------------------------------------------------------- 目标机预检


async def test_conf_encryption_mismatch_is_a_blocker(tmp_path):
    """包内明文 conf vs 目标机加密 conf → 422 blockers（导入后网盘全不可用）。"""
    app = await _make_app(tmp_path, conf=ENCRYPTED_CONF, rclone_pass=RCLONE_PASS)
    state = _export_pack()
    with pytest.raises(ValidationError) as excinfo:
        await site_backup.assert_target_ready(app, state, acknowledge=state["created_at"])
    blockers = excinfo.value.data["blockers"]
    assert any("rclone conf 为明文态" in item for item in blockers)


async def test_restic_missing_is_a_blocker(tmp_path):
    app = await _make_app(tmp_path)
    app.state.settings.restic_binary = "definitely-not-a-restic-binary"
    state = _export_pack()
    with pytest.raises(ValidationError) as excinfo:
        await site_backup.assert_target_ready(app, state, acknowledge=state["created_at"])
    assert any("restic" in item for item in excinfo.value.data["blockers"])


async def test_active_job_blocks_import(tmp_path):
    app = await _make_app(tmp_path)
    await app.state.db.write(
        "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, repo_id) "
        "VALUES ('job-running', 'backup', 'running', 'manual', '/data/jobs/j.log', ?, 'default')",
        ("2026-01-01T00:00:00Z",),
    )
    state = _export_pack()
    with pytest.raises(JobConflictError) as excinfo:
        await site_backup.assert_target_ready(app, state, acknowledge=state["created_at"])
    assert "backup" in excinfo.value.message


async def test_non_empty_target_needs_acknowledge(tmp_path):
    app = await _make_app(tmp_path)
    state = _export_pack()
    with pytest.raises(SiteTargetNotEmptyError):
        await site_backup.assert_target_ready(app, state)
    summary = await site_backup.assert_target_ready(app, state, acknowledge=state["created_at"])
    assert summary.repos_configured == 1
    assert summary.tasks == 1


async def test_describe_target_is_read_only(tmp_path):
    """inspect 只读：连跑两次结果一致，且不动 DB 行数。"""
    app = await _make_app(tmp_path)
    first = await site_backup.describe_target(app)
    second = await site_backup.describe_target(app)
    assert first == second


async def test_schema_newer_than_target_rejected_pre_import(tmp_path):
    app = await _make_app(tmp_path)
    state = _export_pack()
    state["schema_version"] = 999
    with pytest.raises(SiteSchemaUnsupportedError):
        await site_backup.assert_target_ready(app, state, acknowledge=state["created_at"])


# ---------------------------------------------------------------- 导入：落地


@pytest.fixture()
def hot_reload_stubs(monkeypatch) -> list[str]:
    """把日志级别热生效替换成记账（apply_log_level 改的是进程级 root logger）。"""
    applied: list[str | None] = []
    monkeypatch.setattr(site_backup, "apply_log_level", lambda name: applied.append(name) or "INFO")
    return applied


async def _import(app, state=None, **overrides):
    payload = dict(state or _export_pack())
    payload.update(overrides)
    return await site_backup.apply_state(app, payload)


async def test_import_writes_files_and_rows(tmp_path, env_calls, hot_reload_stubs):
    """一次成功导入：三文件按字节落盘、五表重建、会话清空、任务 id/tag 保留。"""
    app = await _make_app(tmp_path)
    settings = app.state.settings
    rclone_pass_path = Path(settings.passfile_root) / "rclone.pass"
    rclone_pass_path.write_bytes(b"old-rclone-pass")

    state = _export_pack()
    state["rclone"]["pass"] = {
        "included": True,
        "content_b64": base64.b64encode(RCLONE_PASS).decode("ascii"),
        "sha256": hashlib.sha256(RCLONE_PASS).hexdigest(),
        "mtime_ns": 1,
    }
    assert rclone_pass_path.read_bytes() == b"old-rclone-pass"

    report = await _import(app, state)

    # 文件面：密码文件 / conf / rclone.pass 全部按包内字节写回
    assert settings.passfile_path("default").read_text(encoding="utf-8") == "pack-repo-password"
    assert Path(settings.rclone_config).read_bytes() == PLAIN_CONF
    assert rclone_pass_path.read_bytes() == RCLONE_PASS

    # DB 面：五表重建，sessions 清空
    assert report["applied"] == {
        "repos": 1,
        "tasks": 1,
        "config": 2,
        "forms": 1,
        "passfiles": 1,
        "rclone_conf": 1,
        "rclone_pass": 1,
    }
    assert report["logged_out"] is True
    # 手造包不是真实导出，没有 source.normalized_env_keys；真实链路断言见下方专测
    assert report["normalized_env_keys"] == []
    row = await app.state.db.fetch_one("SELECT tag FROM backup_tasks WHERE id = 7")
    assert row[0] == "task-7"
    sessions = await app.state.db.fetch_one("SELECT COUNT(*) FROM sessions")
    assert sessions[0] == 0

    # 热生效：C3 env 重建一次、调度器重载一次、日志级别按包内值
    assert env_calls == [("set", rclone_pass_path)]
    assert app.state.scheduler.reloads == 1
    assert hot_reload_stubs == ["debug"]
    assert app.state.restic.tz == ["Asia/Shanghai"]


async def test_import_clears_password_env_when_pass_not_included(tmp_path, env_calls, hot_reload_stubs):
    """包内不带 rclone.pass 时必须清掉 RCLONE_PASSWORD_COMMAND（否则指向不存在的文件）。"""
    app = await _make_app(tmp_path)
    await _import(app)
    assert env_calls == [("clear", None)]


async def test_import_keeps_three_rollback_dirs(tmp_path, env_calls, hot_reload_stubs):
    """连导 4 次只留 3 个回滚点（settings.site_restore_keep_count）。"""
    app = await _make_app(tmp_path)
    for _ in range(4):
        await _import(app)
    root = Path(app.state.settings.data_dir) / "site-restore"
    dirs = sorted(entry for entry in root.iterdir() if entry.is_dir())
    assert len(dirs) == SITE_RESTORE_KEEP_COUNT == 3


async def test_rollback_dir_records_phase(tmp_path, env_calls, hot_reload_stubs):
    """提交后 state.json 记为 committed，且抄下了被覆盖的旧文件。"""
    app = await _make_app(tmp_path)
    report = await _import(app)
    rollback = Path(app.state.settings.data_dir) / "site-restore" / report["rollback_dir"]
    meta = json.loads((rollback / "state.json").read_text(encoding="utf-8"))
    assert meta["phase"] == "committed"
    assert (rollback / "passfiles" / "default" / "password").read_text(encoding="utf-8") == "repo-password"


async def test_import_restores_files_when_transaction_fails(tmp_path, env_calls, hot_reload_stubs, monkeypatch):
    """DB 事务失败 → 文件从回滚点恢复、异常原样抛出（不留混合态）。"""
    app = await _make_app(tmp_path)
    settings = app.state.settings

    async def _boom(self, tx, state):
        raise RuntimeError("boom")

    monkeypatch.setattr(site_backup.SiteRepo, "replace_all", _boom)
    with pytest.raises(RuntimeError):
        await _import(app)

    assert settings.passfile_path("default").read_text(encoding="utf-8") == "repo-password"
    assert Path(settings.rclone_config).read_bytes() == PLAIN_CONF
    row = await app.state.db.fetch_one("SELECT tag FROM backup_tasks WHERE id = 1")
    assert row[0] == "task-1"
    assert app.state.scheduler.reloads == 0


async def test_import_file_write_failure_raises_internal_error(tmp_path, env_calls, hot_reload_stubs, monkeypatch):
    """文件写失败 → InternalError（rolled_back=True），DB 一行未动。"""
    app = await _make_app(tmp_path)

    def _boom(settings, rows):
        raise OSError("disk full")

    monkeypatch.setattr(site_backup, "_write_passfiles", _boom)
    with pytest.raises(InternalError) as excinfo:
        await _import(app)
    assert excinfo.value.data == {"rolled_back": True}
    row = await app.state.db.fetch_one("SELECT COUNT(*) FROM backup_tasks")
    assert row[0] == 1


async def test_import_reencrypts_sensitive_config(tmp_path, env_calls, hot_reload_stubs, monkeypatch):
    """导入时 config 值按目标机写侧重新加密（密钥源是刚写好的密码文件）。"""
    app = await _make_app(tmp_path)
    seen: list[tuple[str, str | None]] = []
    original = app.state.config_repo.encode_for_storage
    monkeypatch.setattr(
        app.state.config_repo,
        "encode_for_storage",
        lambda key, value: (seen.append((key, value)), original(key, value))[1],
    )
    await _import(app)
    assert {key for key, _ in seen} == {"timezone", "log.level"}
    row = await app.state.db.fetch_one("SELECT source FROM config WHERE key = 'timezone'")
    assert row[0] == "ui"


# ---------------------------------------------------------------- W1–W3 审查裁决回归


async def test_open_sealed_runs_in_worker_thread(tmp_path, monkeypatch):
    """C4：PBKDF2 600k 的 open_sealed 不得在事件循环线程执行。"""
    pack = site_backup.build_site_pack(_export_pack(), PASSWORD)
    loop_thread = threading.get_ident()
    seen: list[int] = []
    original = site_backup.pack_crypto.open_sealed

    def _record(*args, **kwargs):
        seen.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(site_backup.pack_crypto, "open_sealed", _record)
    await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert seen and all(tid != loop_thread for tid in seen)
    assert asyncio.get_running_loop().is_running()


async def test_encode_for_storage_runs_in_worker_thread(tmp_path, env_calls, hot_reload_stubs, monkeypatch):
    """C4：读密码文件 IO 的 encode_for_storage 不得在事件循环线程执行。"""
    app = await _make_app(tmp_path)
    loop_thread = threading.get_ident()
    seen: list[int] = []
    original = app.state.config_repo.encode_for_storage

    def _record(key, value):
        seen.append(threading.get_ident())
        return original(key, value)

    monkeypatch.setattr(app.state.config_repo, "encode_for_storage", _record)
    await _import(app)
    assert len(seen) == 2
    assert all(tid != loop_thread for tid in seen)


async def test_payload_rejects_bad_forms():
    """§2.3：rclone.forms 必须是数组且条目必备 remote / field。"""
    state = _export_pack()
    state["rclone"]["forms"] = [{"remote": "backup"}]
    pack = site_backup.build_site_pack(state, PASSWORD)
    with pytest.raises(SitePackInvalidError) as excinfo:
        await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert "rclone.forms" in excinfo.value.message


async def test_older_schema_warning_enters_import_report(tmp_path, env_calls, hot_reload_stubs):
    """§2.4：schema_version < 本机时，多余列丢弃的 warning 进导入报告。"""
    app = await _make_app(tmp_path)
    state = _export_pack()
    state["schema_version"] = 9
    pack = site_backup.build_site_pack(state, PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert any("多余列" in item for item in parsed["warnings"])
    report = await site_backup.apply_state(app, parsed)
    assert any("多余列" in item for item in report["warnings"])


async def test_old_pack_without_normalized_env_keys_still_parses():
    """§2.4 兼容性：旧包 source 无 normalized_env_keys 子键，仍可解析。"""
    state = _export_pack()
    assert "normalized_env_keys" not in state["source"]
    pack = site_backup.build_site_pack(state, PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert "normalized_env_keys" not in parsed["source"]
    assert set(parsed) == set(site_backup._PAYLOAD_KEYS)


@pytest.mark.parametrize("bad", ["env", {"timezone": 1}, ["timezone", 7], None, 7])
async def test_source_normalized_env_keys_bad_type_rejected(bad):
    """§4.3 第 4 步：子键存在时必须是 list 且元素均为 str，否则 422。"""
    state = _export_pack()
    state["source"]["normalized_env_keys"] = bad
    pack = site_backup.build_site_pack(state, PASSWORD)
    with pytest.raises(SitePackInvalidError) as excinfo:
        await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert "normalized_env_keys" in excinfo.value.message


async def test_source_normalized_env_keys_valid_list_accepted():
    """合法字符串数组放行，且解析后逐字保留（导入报告回显来源）。"""
    state = _export_pack()
    state["source"]["normalized_env_keys"] = ["timezone", "log.level"]
    pack = site_backup.build_site_pack(state, PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert parsed["source"]["normalized_env_keys"] == ["timezone", "log.level"]


async def test_no_downgrade_warning_when_schema_equal():
    """§2.4：schema 相等不产生降级 warning。"""
    pack = site_backup.build_site_pack(_export_pack(), PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert parsed["warnings"] == []


async def test_real_export_chain_reports_normalized_env_keys(tmp_path, env_calls, hot_reload_stubs):
    """§1.6-1 / §4.2：真实 collect_state → 封包 → 解包 → 导入链路给出归一化键。"""
    source = await _make_app(tmp_path, conf=PLAIN_CONF)
    await source.state.db.write("UPDATE config SET source = 'env' WHERE key = 'timezone'")
    state = await site_backup.collect_state(source)
    assert state["source"]["normalized_env_keys"] == ["timezone"]
    assert set(state) == set(site_backup._PAYLOAD_KEYS)  # 无第 12 个顶层键
    pack = site_backup.build_site_pack(state, PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    report = await site_backup.apply_state(source, parsed)
    assert report["normalized_env_keys"] == ["timezone"]


async def test_conf_missing_warning_survives_real_export_chain(tmp_path):
    """§3.3-5：conf 缺失的 warning 随真实导出链进包、进导入报告。"""
    app = await _make_app(tmp_path, conf=None)
    state = await site_backup.collect_state(app)
    assert site_backup._CONF_MISSING_WARNING in state["warnings"]
    pack = site_backup.build_site_pack(state, PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    assert site_backup._CONF_MISSING_WARNING in parsed["warnings"]


async def test_error_messages_and_acknowledge_contract(tmp_path):
    """§4.7 / §4.2：逐条对齐文案，data 明示 acknowledge 契约。"""
    from app.errors import SitePassfileIncompleteError as _Pass
    from app.errors import SiteSchemaUnsupportedError as _Schema

    assert "请先升级" in _Schema(pack_schema=12, target_schema=10).message
    assert "导出已中止" in _Pass(repo_ids=("default",)).message
    assert "/repos/rclone.pass" in site_backup._RCLONE_PASS_UNREADABLE

    app = await _make_app(tmp_path)
    with pytest.raises(SiteTargetNotEmptyError) as excinfo:
        await site_backup.assert_target_ready(app, _export_pack())
    assert "确认整机替换" in excinfo.value.message
    assert excinfo.value.data["acknowledge_required"] is True
    assert "created_at" in excinfo.value.data["acknowledge"]


# ---------------------------------------------------------------- W4 审查回归：单一真源


async def test_summarize_pack_matches_schema_fields():
    """W4 判定项回归：`summarize_pack` 返回 `PackSummary` 实例，字段集由模型锁死。"""
    from app.schemas.api_schemas import PackSummary

    state = _export_pack()
    pack = site_backup.build_site_pack(state, PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    summary = site_backup.summarize_pack(pack, parsed)
    assert isinstance(summary, PackSummary)
    assert set(summary.model_dump()) == set(PackSummary.model_fields)


async def test_describe_target_returns_schema_model(tmp_path):
    """W4 判定项回归：目标摘要只有一个类型真源（`SiteTargetSummary`）。"""
    from app.schemas.api_schemas import SiteTargetSummary

    app = await _make_app(tmp_path)
    target = await site_backup.describe_target(app)
    assert isinstance(target, SiteTargetSummary)
    assert set(target.model_dump()) == set(SiteTargetSummary.model_fields)
    await app.state.db.close()


def test_site_counts_audit_detail_has_no_credentials():
    """W4 判定项回归：export/import 审计 detail 同源，只含计数/模式/回滚目录名。"""
    state = _export_pack()
    counts = site_backup.SiteCounts.from_state(state)
    export_detail = counts.audit_detail()
    import_detail = counts.audit_detail(mode="replace", rollback_dir="2026-01-01T00Z-abc")
    assert set(export_detail) == {
        "repos", "tasks", "config_keys", "passfiles", "rclone_conf", "rclone_pass",
    }
    assert set(import_detail) - set(export_detail) == {"mode", "rollback_dir"}
    assert "pack-repo-password" not in json.dumps(import_detail, ensure_ascii=False)
    assert PASSWORD not in json.dumps(import_detail, ensure_ascii=False)

async def test_pack_version_and_schema_hints():
    """§4.7：包版本提示给可操作动作；schema 提示指向升级。"""
    with pytest.raises(SitePackInvalidError) as excinfo:
        await site_backup.parse_site_pack(
            {"format": site_backup.PACK_FORMAT, "version": 99}, PASSWORD, target_schema=10
        )
    assert "重新导出" in excinfo.value.message
    assert "请升级应用" in excinfo.value.message


async def test_forms_updated_at_roundtrip(tmp_path, env_calls, hot_reload_stubs):
    """§2.3：forms[].updated_at 端到端保留，而不是被导入时间覆盖。"""
    app = await _make_app(tmp_path)
    state = _export_pack()
    await _import(app, state)
    row = await app.state.db.fetch_one("SELECT updated_at FROM rclone_credential_forms")
    assert row[0] == "2026-02-02T01:02:03Z"


async def test_forms_updated_at_falls_back_to_now(tmp_path, env_calls, hot_reload_stubs):
    """§2.3：旧包 forms 缺 updated_at 时回落导入时刻（C8 时间语义）。"""
    app = await _make_app(tmp_path)
    state = _export_pack()
    del state["rclone"]["forms"][0]["updated_at"]
    await _import(app, state)
    row = await app.state.db.fetch_one("SELECT updated_at FROM rclone_credential_forms")
    assert row[0] and row[0] != "2026-02-02T01:02:03Z"


async def test_real_export_preserves_forms_updated_at(tmp_path, env_calls, hot_reload_stubs):
    """§2.3：真实导出侧的 dump_state SELECT 带出 updated_at，往返逐字一致。"""
    app = await _make_app(tmp_path)
    await app.state.db.write(
        "INSERT INTO rclone_credential_forms (remote, field, obscured, value_sha256, updated_at) "
        "VALUES ('gdrive', 'pass', 1, 'sha', '2026-01-02T03:04:05Z')"
    )
    state = await site_backup.collect_state(app)
    form = state["rclone"]["forms"][0]
    assert form["updated_at"] == "2026-01-02T03:04:05Z"
    pack = site_backup.build_site_pack(state, PASSWORD)
    parsed = await site_backup.parse_site_pack(pack, PASSWORD, target_schema=10)
    await site_backup.apply_state(app, parsed)
    row = await app.state.db.fetch_one("SELECT updated_at FROM rclone_credential_forms")
    assert row[0] == "2026-01-02T03:04:05Z"