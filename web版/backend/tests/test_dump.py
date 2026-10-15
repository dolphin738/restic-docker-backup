"""T05 R-019 dump 模块测试：DumpCmd 白名单拼装、DumpService 探测/执行/清理、
JobRunner dump 阶段集成（含失败阻断与独立 dump 任务）、E07 键级校验钩子与三态密码。

红线自查锚点（§7 测试策略）：
- S2：dump argv 逐具名字段断言；非法组件（``-`` 开头/换行）422；
- S3：密码零出现在 argv / 日志 / 审计——argv 断言 + 三态密码用例 grep 输出；
- S1：cleanup 只触碰 settings.dump_dir 白名单（目录外文件保留、链接不跟随）。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import ORIGIN, finish_bootstrap
from test_state_machine import BACKUP_LINES, FakeRestic, await_job

from app.constants import (
    BEHAVIOR_CHECK_SAMPLE_RATIO_KEY,
    BEHAVIOR_MONTHLY_FULL_DAY_KEY,
    DUMP_DATABASES_KEY,
    DUMP_ENABLED_KEY,
    DUMP_PASSWORD_KEY,
    DUMP_RETENTION_DAYS_KEY,
    DUMP_TYPE_KEY,
)
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.errors import ValidationError
from app.services.dump import (
    DumpError,
    DumpService,
    PgVersionMismatchError,
    load_dump_config,
)
from app.services.dump_cmd import DumpCmd, DumpConfig
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.settings import Settings

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


async def make_dump_runner(tmp_path: Path) -> tuple[Database, JobRunner, JobRepo, ConfigRepo]:
    """构造带 config 仓储的 runner（dump 阶段集成测试基座，形态仿 make_runner）。"""
    db = Database(tmp_path / "dump.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    runner = JobRunner(
        db,
        FakeRestic(lines=BACKUP_LINES),  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
    )
    return db, runner, JobRepo(db), ConfigRepo(db)


# ---------------------------------------------------------------- DumpCmd（S2/S3）

def make_cfg(**overrides) -> DumpConfig:
    values: dict = {
        "engine": "pg",
        "host": "db.local",
        "port": 5432,
        "user": "backup",
        "password": "s3cret",
        "databases": ("appdb",),
        "retention_days": 7,
    }
    values.update(overrides)
    return DumpConfig(**values)


def test_dump_cmd_build_pg():
    """pg argv 逐具名字段：host/port/user/dbname/file；--no-password 防 TTY 挂死。"""
    argv = DumpCmd().build("pg", cfg=make_cfg(), database="appdb", out_path="/data/dump/a.sql")
    assert argv == [
        "pg_dump",
        "--host", "db.local",
        "--port", "5432",
        "--username", "backup",
        "--dbname", "appdb",
        "--no-password",
        "--file", "/data/dump/a.sql",
    ]


def test_dump_cmd_build_mysql():
    """mysql argv 逐具名字段：--databases 产物自带建库语句、--result-file 落盘。"""
    argv = DumpCmd().build("mysql", cfg=make_cfg(engine="mysql"), database="appdb", out_path="/data/dump/a.sql")
    assert argv == [
        "mysqldump",
        "--host", "db.local",
        "--port", "5432",
        "--user", "backup",
        "--databases", "appdb",
        "--result-file", "/data/dump/a.sql",
    ]


def test_dump_cmd_password_never_in_argv():
    """S3：密码绝不进 argv（只经 env_for 的 PGPASSWORD/MYSQL_PWD 通道）。"""
    cmd = DumpCmd()
    cfg = make_cfg()
    for engine in ("pg", "mysql"):
        argv = cmd.build(engine, cfg=cfg, database="appdb", out_path="/data/dump/a.sql")
        assert "s3cret" not in " ".join(argv)
    assert cmd.env_for(cfg) == {"PGPASSWORD": "s3cret"}
    assert cmd.env_for(make_cfg(engine="mysql")) == {"MYSQL_PWD": "s3cret"}
    assert cmd.env_for(make_cfg(password="")) == {}  # 空密码 → 空 env
    assert cmd.env_for(make_cfg(engine="sqlite")) == {}  # sqlite 无凭据通道


def test_dump_cmd_rejects_injection_components():
    """S2：以 ``-`` 开头或含换行的组件拒绝（防 flag 注入/换行注入）。"""
    cmd = DumpCmd()
    with pytest.raises(ValidationError):
        cmd.build("pg", cfg=make_cfg(), database="--evil", out_path="/data/dump/a.sql")
    with pytest.raises(ValidationError):
        cmd.build("pg", cfg=make_cfg(), database="db\nx", out_path="/data/dump/a.sql")
    with pytest.raises(ValidationError):
        cmd.build("pg", cfg=make_cfg(host="-h.evil"), database="appdb", out_path="/data/dump/a.sql")
    with pytest.raises(ValidationError):
        cmd.build("sqlite", cfg=make_cfg(), database="appdb", out_path="/data/dump/a.sql")


# ---------------------------------------------------------------- load_dump_config

async def test_load_dump_config_disabled_returns_none(tmp_path):
    """dump.enabled != 1（含缺省）→ None（dump 阶段跳过，既有语义零回归）。"""
    db = Database(tmp_path / "cfg.db")
    await db.connect()
    config = ConfigRepo(db)
    assert await load_dump_config(config) is None
    await config.set(DUMP_ENABLED_KEY, "0")
    assert await load_dump_config(config) is None
    await db.close()


async def test_load_dump_config_enabled_assembles_defaults(tmp_path):
    """启用后按键装配；缺省回退（engine=pg、port=5432、retention=7）；坏 port 告警回退。"""
    db = Database(tmp_path / "cfg.db")
    await db.connect()
    config = ConfigRepo(db)
    await config.set(DUMP_ENABLED_KEY, "1")
    await config.set(DUMP_DATABASES_KEY, json.dumps(["appdb", "reportdb"]))

    cfg = await load_dump_config(config)
    assert cfg is not None
    assert cfg.engine == "pg"
    assert cfg.port == 5432
    assert cfg.databases == ("appdb", "reportdb")
    assert cfg.retention_days == 7
    await db.close()


async def test_load_dump_config_invalid_engine_or_empty_databases(tmp_path):
    """启用但 engine 非法 / databases 为空或坏 JSON → 422（配置错误显式失败，禁止静默降级）。"""
    db = Database(tmp_path / "cfg.db")
    await db.connect()
    config = ConfigRepo(db)
    await config.set(DUMP_ENABLED_KEY, "1")

    await config.set(DUMP_TYPE_KEY, "oracle")
    with pytest.raises(ValidationError):
        await load_dump_config(config)

    await config.set(DUMP_TYPE_KEY, "pg")
    with pytest.raises(ValidationError):
        await load_dump_config(config)  # databases 缺失
    await config.set(DUMP_DATABASES_KEY, "not-json")
    with pytest.raises(ValidationError):
        await load_dump_config(config)
    await db.close()


# ---------------------------------------------------------------- DumpService.probe_pg

async def test_probe_pg_passes_dbname_of_first_database(tmp_path):
    """psql 版本探测必须带 --dbname=<databases[0]>：缺省时 psql 连与用户同名库，
    用户无同名库即 probe 必败（2026-10-15 真机实证），即使配置库完全正常。"""
    service = DumpService(Settings(data_dir=str(tmp_path)))
    captured: list[list[str]] = []

    async def fake_capture(argv, env_extras):  # noqa: ANN001, ANN202
        captured.append(list(argv))
        if "pg_dump" in argv[0]:
            return 0, "pg_dump (PostgreSQL) 16.4"
        return 0, "16.4"

    service._capture = fake_capture  # type: ignore[method-assign]  # noqa: SLF001
    await service.probe_pg(make_cfg())
    psql_argv = next(a for a in captured if a[0] == "psql")
    assert "--dbname" in psql_argv and "appdb" in psql_argv


async def test_probe_pg_mismatch_raises_with_guidance(tmp_path):
    """client < server → PgVersionMismatchError，文案含两版本与自建镜像提示（§7.5）。"""
    service = DumpService(Settings(data_dir=str(tmp_path)))

    async def fake_capture(argv, env_extras):  # noqa: ANN001, ANN202
        if "pg_dump" in argv[0]:
            return 0, "pg_dump (PostgreSQL) 15.3"
        return 0, "16.4"

    service._capture = fake_capture  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(PgVersionMismatchError) as excinfo:
        await service.probe_pg(make_cfg())
    assert "client 为 15" in str(excinfo.value) and "server 为 16" in str(excinfo.value)
    assert "Alpine edge" in str(excinfo.value)


async def test_probe_pg_equal_or_newer_client_passes(tmp_path):
    """client >= server → 返回双主版本。"""
    service = DumpService(Settings(data_dir=str(tmp_path)))

    async def fake_capture(argv, env_extras):  # noqa: ANN001, ANN202
        if "pg_dump" in argv[0]:
            return 0, "pg_dump (PostgreSQL) 17.0"
        return 0, "16.4"

    service._capture = fake_capture  # type: ignore[method-assign]  # noqa: SLF001
    assert await service.probe_pg(make_cfg()) == (17, 16)


async def test_probe_pg_server_unreachable_is_dump_error(tmp_path):
    """服务器连接失败 → DumpError 带退出码与输出片段（原因明示，禁止静默）。"""
    service = DumpService(Settings(data_dir=str(tmp_path)))

    async def fake_capture(argv, env_extras):  # noqa: ANN001, ANN202
        if "pg_dump" in argv[0]:
            return 0, "pg_dump (PostgreSQL) 16.4"
        return 2, "connection refused"

    service._capture = fake_capture  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(DumpError, match="连接失败"):
        await service.probe_pg(make_cfg())


# ---------------------------------------------------------------- DumpService.run

async def test_run_sqlite_backup_roundtrip(tmp_path):
    """sqlite 走 Python 备份接口：产物非空、源库可继续读写（镜像零新增依赖）。"""
    db_path = tmp_path / "app.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
    conn.close()

    settings = Settings(data_dir=str(tmp_path))
    service = DumpService(Settings(data_dir=str(tmp_path)), settings)
    out_dir = await service.run(make_cfg(engine="sqlite", databases=(str(db_path),)), tag="job-x")

    products = list(Path(out_dir).glob("app.db-job-x-*.sqlite"))
    assert len(products) == 1
    assert products[0].stat().st_size > 0
    check = sqlite3.connect(str(products[0]))
    try:
        assert check.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        check.close()


async def test_run_empty_product_is_failure(tmp_path):
    """命令成功退出但产物 0 字节 → DumpError（禁止静默备份空文件）。"""
    settings = Settings(data_dir=str(tmp_path))
    service = DumpService(settings=settings)

    async def fake_exec(argv, env_extras, timeout):  # noqa: ANN001, ANN202
        Path(argv[argv.index("--file") + 1]).write_bytes(b"")  # 空产物
        return 0, ""

    service._exec = fake_exec  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(DumpError, match="空文件"):
        await service.run(make_cfg(), out_dir=tmp_path / "dump", tag="job-x")


async def test_run_command_failure_carries_reason(tmp_path):
    """非 0 退出 → DumpError 携带退出码与输出片段。"""
    settings = Settings(data_dir=str(tmp_path))
    service = DumpService(settings=settings)

    async def fake_exec(argv, env_extras, timeout):  # noqa: ANN001, ANN202
        return 1, "pg_dump: error: connection refused"

    service._exec = fake_exec  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(DumpError, match="connection refused"):
        await service.run(make_cfg(), out_dir=tmp_path / "dump", tag="job-x")


# ---------------------------------------------------------------- DumpService.cleanup（S1）

def _seed_product(path: Path, size: int, age_days: int) -> None:
    path.write_bytes(b"x" * size)
    epoch = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (epoch, epoch))


def test_cleanup_purges_expired_within_dump_dir(tmp_path):
    """保留期判定：超期产物删除、保留期内与目录外文件保留；返回 (deleted, freed)。"""
    settings = Settings(data_dir=str(tmp_path))
    dump_dir = settings.dump_dir
    dump_dir.mkdir(parents=True, exist_ok=True)
    stale = dump_dir / "appdb-job-old-20260101T000000Z.sql"
    fresh = dump_dir / "appdb-job-new-20990101T000000Z.sql"
    outside = tmp_path / "outside.sql"
    _seed_product(stale, 100, age_days=30)
    _seed_product(fresh, 10, age_days=1)
    _seed_product(outside, 50, age_days=30)

    deleted, freed = DumpService(settings=settings).cleanup(7, now=NOW)

    assert (deleted, freed) == (1, 100)
    assert not stale.exists()
    assert fresh.exists() and outside.exists()  # S1：目录外绝不触碰


def test_cleanup_zero_days_disables(tmp_path):
    """retention_days <= 0 = 不启用（fail-safe）：文件原样保留、零删除。"""
    settings = Settings(data_dir=str(tmp_path))
    dump_dir = settings.dump_dir
    dump_dir.mkdir(parents=True, exist_ok=True)
    stale = dump_dir / "old.sql"
    _seed_product(stale, 10, age_days=999)

    assert DumpService(settings=settings).cleanup(0, now=NOW) == (0, 0)
    assert stale.exists()


def test_cleanup_symlink_never_followed(tmp_path, monkeypatch):
    """符号链接只删链接本身、绝不跟随（S1：白名单外目标不可被间接触达）。"""
    settings = Settings(data_dir=str(tmp_path))
    dump_dir = settings.dump_dir
    dump_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "victim.sql"
    target.write_bytes(b"precious")
    link = dump_dir / "evil.sql"
    link.write_bytes(b"")  # 链接本体占位

    real_is_symlink = Path.is_symlink

    def fake_is_symlink(self: Path) -> bool:
        return True if self.name == "evil.sql" else real_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    deleted, freed = DumpService(settings=settings).cleanup(7, now=NOW)

    assert deleted == 1 and freed == 0  # 链接计 0 字节
    assert not link.exists()
    assert target.read_bytes() == b"precious"


# ---------------------------------------------------------------- JobRunner dump 阶段集成

async def test_dump_disabled_backup_phase_unchanged(tmp_path):
    """dump.enabled 缺省 off：backup 行为与旧实现逐字节一致（回归保护）。"""
    db, runner, repo, config = await make_dump_runner(tmp_path)
    job = await runner.submit("backup", params={"sources": ["/host/etc"]})
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final is not None and final.status == "success"
    await db.close()


async def test_dump_phase_sqlite_success_then_backup(tmp_path):
    """dump.enabled=1（sqlite）：dump 产物落盘 → backup 阶段继续 → success。"""
    import sqlite3

    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
    conn.close()

    db, runner, repo, config = await make_dump_runner(tmp_path)
    await config.set(DUMP_ENABLED_KEY, "1")
    await config.set(DUMP_TYPE_KEY, "sqlite")
    await config.set(DUMP_DATABASES_KEY, json.dumps([str(db_path)]))

    job = await runner.submit("backup", params={"sources": ["/host/etc"]})
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final is not None and final.status == "success"
    assert not final.summary.startswith("dump 阶段失败")
    products = list(runner._settings.dump_dir.glob("app.db-job-*-*.sqlite"))  # noqa: SLF001
    assert len(products) == 1 and products[0].stat().st_size > 0
    await db.close()


async def test_dump_phase_failure_blocks_backup(tmp_path):
    """dump 失败（库文件缺失）→ failed 终态 + summary 前缀，backup 阶段被阻断（restic 未 spawn）。"""
    db, runner, repo, config = await make_dump_runner(tmp_path)
    fake = runner._restic  # FakeRestic  # noqa: SLF001
    await config.set(DUMP_ENABLED_KEY, "1")
    await config.set(DUMP_TYPE_KEY, "sqlite")
    await config.set(DUMP_DATABASES_KEY, json.dumps([str(tmp_path / "missing.db")]))

    job = await runner.submit("backup", params={"sources": ["/host/etc"]})
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final is not None and final.status == "failed"
    assert final.summary.startswith("dump 阶段失败")
    assert fake.last_process is None  # 阻断：restic 阶段未执行

    audit = AuditRepo(db)
    rows, _ = await audit.query(action="job.finished")
    assert rows[0].result == "failed"
    await db.close()


async def test_standalone_dump_job_succeeds_without_restic(tmp_path):
    """独立 type=dump 任务（E12 触发，测试用途）：dump 即终态，无 restic 阶段。"""
    import sqlite3

    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
    conn.close()

    db, runner, repo, config = await make_dump_runner(tmp_path)
    fake = runner._restic  # noqa: SLF001
    await config.set(DUMP_ENABLED_KEY, "1")
    await config.set(DUMP_TYPE_KEY, "sqlite")
    await config.set(DUMP_DATABASES_KEY, json.dumps([str(db_path)]))

    job = await runner.submit("dump", trigger="manual")
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final is not None and final.status == "success"
    assert fake.last_process is None  # dump 任务不 spawn restic
    await db.close()


async def test_dump_disabled_standalone_job_succeeds(tmp_path):
    """dump 未启用时的独立 dump 任务：阶段空转 → success（不产生 restic 调用）。"""
    db, runner, repo, _ = await make_dump_runner(tmp_path)
    fake = runner._restic  # noqa: SLF001

    job = await runner.submit("dump", trigger="manual")
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final is not None and final.status == "success"
    assert fake.last_process is None
    await db.close()


# ---------------------------------------------------------------- D2 作用域收窄（QA 复验 2026-10-15）

async def _enable_broken_dump(config: ConfigRepo, tmp_path: Path) -> None:
    """dump.enabled=1 且 DB 配置故意坏（sqlite 库文件缺失 → dump 阶段必失败）。"""
    await config.set(DUMP_ENABLED_KEY, "1")
    await config.set(DUMP_TYPE_KEY, "sqlite")
    await config.set(DUMP_DATABASES_KEY, json.dumps([str(tmp_path / "missing.db")]))


@pytest.mark.parametrize("job_type", ["check", "unlock", "restore", "forget", "init"])
async def test_dump_failure_does_not_block_non_backup_types(tmp_path, job_type):
    """D2 回归（中危，必修）：dump.enabled=1 且配置故障时，非 backup/dump 类型**零 dump 介入**。

    最坏场景（QA 取证成立）：AUTO_UNLOCK 恢复链若被 dump 失败阻断，仓库锁残留
    无法自愈。断言：任务照常进入 restic 阶段（fake spawn 发生）、终态非
    「dump 阶段失败」前缀、无 dump 产物落盘。
    """
    db, runner, repo, config = await make_dump_runner(tmp_path)
    fake = runner._restic  # noqa: SLF001
    await _enable_broken_dump(config, tmp_path)

    kwargs: dict = {"trigger": "manual"}
    if job_type == "restore":
        kwargs.update(
            snapshot_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
            params={"target": "/restore/etc-20261010-0138"},
        )
    elif job_type == "forget":
        kwargs.update(params={"tags": ["task-1"], "keep_daily": 7})
    job = await runner.submit(job_type, **kwargs)
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final is not None and final.status == "success"
    assert not (final.summary or "").startswith("dump 阶段失败")
    assert fake.last_process is not None  # restic 阶段照常执行（dump 未介入）
    assert list(runner._settings.dump_dir.glob("*")) == []  # noqa: SLF001  零 dump 产物
    await db.close()


async def test_dump_failure_still_blocks_backup_and_dump_types(tmp_path):
    """D2 守护对照：backup 与独立 dump 两类在 dump 失败时行为**不变**（阻断语义保持）。"""
    db, runner, repo, config = await make_dump_runner(tmp_path)
    await _enable_broken_dump(config, tmp_path)

    backup_job = await runner.submit("backup", params={"sources": ["/host/etc"]})
    await await_job(runner, backup_job.id)
    final_backup = await repo.get(backup_job.id)
    assert final_backup is not None and final_backup.status == "failed"
    assert (final_backup.summary or "").startswith("dump 阶段失败")

    runner._restic.last_process = None  # noqa: SLF001  重置 spawn 记录
    dump_job = await runner.submit("dump", trigger="manual")
    await await_job(runner, dump_job.id)
    final_dump = await repo.get(dump_job.id)
    assert final_dump is not None and final_dump.status == "failed"
    assert (final_dump.summary or "").startswith("dump 阶段失败")
    assert runner._restic.last_process is None  # noqa: SLF001  失败时不进 restic 阶段
    await db.close()


# ---------------------------------------------------------------- D1 probe_pg 组件校验对齐（QA 复验 2026-10-15）


@pytest.mark.parametrize("field", ["host", "user"])
@pytest.mark.parametrize("bad_value", ["--evil", "db\nx"])
async def test_probe_pg_validates_components_like_build_pg(tmp_path, field, bad_value):
    """D1（低危）：probe_pg 的 psql argv 与 build_pg 同一校验点——host/user 以 ``-``
    开头或含换行被拒（空 host/user 走「不加 flag」分支，非校验面）。"""
    service = DumpService(Settings(data_dir=str(tmp_path)))
    cfg = make_cfg(**{field: bad_value})
    with pytest.raises(ValidationError):
        await service.probe_pg(cfg)


# ---------------------------------------------------------------- E07 键级校验钩子 + 三态密码（S3）

async def test_e07_behavior_and_dump_value_guards(app_client):
    """键级钩子：behavior/dump 值域越界 422、合法值放行（config.update 审计覆盖）。"""
    ns = app_client
    await finish_bootstrap(ns)

    bad_cases = [
        ({"behavior.check_sample_ratio": "1.5"}, BEHAVIOR_CHECK_SAMPLE_RATIO_KEY),
        ({"behavior.check_sample_ratio": "abc"}, BEHAVIOR_CHECK_SAMPLE_RATIO_KEY),
        ({"behavior.monthly_full_day": "0"}, BEHAVIOR_MONTHLY_FULL_DAY_KEY),
        ({"behavior.monthly_full_day": "29"}, BEHAVIOR_MONTHLY_FULL_DAY_KEY),
        ({"behavior.auto_unlock": "yes"}, "behavior.auto_unlock"),
        ({"dump.enabled": "2"}, DUMP_ENABLED_KEY),
        ({"dump.type": "oracle"}, DUMP_TYPE_KEY),
        ({"dump.port": "0"}, "dump.port"),
        ({"dump.retention_days": "-1"}, DUMP_RETENTION_DAYS_KEY),
        ({"dump.databases": "not-json"}, DUMP_DATABASES_KEY),
        ({"dump.databases": "[]"}, DUMP_DATABASES_KEY),
    ]
    for patch, _key in bad_cases:
        resp = await ns.client.put("/api/config", json={"patch": patch}, headers=ORIGIN)
        assert resp.status_code == 422, f"{patch} 应被拒绝"

    good = {
        "behavior.check_sample_ratio": "0.02",
        "behavior.monthly_full_day": "15",
        "behavior.auto_unlock": "1",
        "behavior.auto_init": "0",
        "dump.enabled": "1",
        "dump.type": "pg",
        "dump.port": "5433",
        "dump.retention_days": "3",
        "dump.databases": '["appdb"]',
    }
    resp = await ns.client.put("/api/config", json={"patch": good}, headers=ORIGIN)
    assert resp.status_code == 200


async def test_e07_dump_password_three_state_and_masked(app_client):
    """三态密码（S3）：None=不变 / ""=清除 / 非空=设置；读取只回 has_value，明文零出现。"""
    ns = app_client
    await finish_bootstrap(ns)

    # 设置
    resp = await ns.client.put("/api/config", json={"patch": {DUMP_PASSWORD_KEY: "s3cret"}}, headers=ORIGIN)
    assert resp.status_code == 200
    items = (await ns.client.get("/api/config")).json()["items"]
    row = next(item for item in items if item["key"] == DUMP_PASSWORD_KEY)
    assert row["has_value"] is True
    assert row["value"] is None  # 敏感键不回明文（pydantic 序列化出 null 占位）
    assert "s3cret" not in json.dumps(items)  # 全响应 grep 无明文

    # None = 不变（patch 不含该键；显式 null = 清除）
    await ns.client.put("/api/config", json={"patch": {"dump.host": "db.local"}}, headers=ORIGIN)
    row = next(
        item
        for item in (await ns.client.get("/api/config")).json()["items"]
        if item["key"] == DUMP_PASSWORD_KEY
    )
    assert row["has_value"] is True  # 未被清除

    # "" = 清除
    await ns.client.put("/api/config", json={"patch": {DUMP_PASSWORD_KEY: ""}}, headers=ORIGIN)
    row = next(
        item
        for item in (await ns.client.get("/api/config")).json()["items"]
        if item["key"] == DUMP_PASSWORD_KEY
    )
    assert row["has_value"] is False
