"""API 集成：全站数据备份 / 恢复三端点（W4 路由层验收门）。

对应《全站数据备份与恢复设计方案》§10.2 W4 的验证门，逐条断言：

- **S4/S5 鉴权**：三端点未登录一律 401（``_PROTECTED`` 全挂）；
- **inspect 零业务写入**：调前调后 DB 行数、密码文件字节、conf 字节逐项不变；
- **409 两门**：有活跃作业 → ``JOB_CONFLICT``；目标非空且未带 ``acknowledge`` →
  ``SITE_TARGET_NOT_EMPTY``（data 明示确认契约）；
- **旧包 422 提示**：``restic-web-config-export`` 被明确指路到新功能（D6 不兼容）；
- **S3**：响应体不含仓库密码明文，审计 detail 不含口令；
- **S6**：导出/导入各留一条 ``site.*`` 审计，``audit_logs`` 只增不改；
- **导入即接管**：成功后 ``sessions`` 清空（下一个请求 401）。
"""

from __future__ import annotations

import json
import sys

import pytest
from conftest import NEW_PASSWORD, ORIGIN, finish_bootstrap

PASSWORD = "Str0ngSitePass1"
PLAIN_CONF = b"[mycrypt]\ntype = crypt\nremote = /home/222\n"
REPO_SECRET = "repo-password"


@pytest.fixture()
def rclone_conf(tmp_path, monkeypatch):
    """把 ``RCLONE_CONFIG`` 钉到 tmp 下的 conf。

    不加这条隔离，``resolve_conf_path`` 会回落到 **本机真实** ``$HOME/.config/rclone/
    rclone.conf``——导入测试会当场覆盖开发者自己的网盘授权。
    """
    conf = tmp_path / "rclone" / "rclone.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_bytes(PLAIN_CONF)
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    return conf


@pytest.fixture()
async def site_app(make_app, rclone_conf):
    """起一个 restic 可用的应用实例（``site-backup`` 前置检查要求 restic 可执行）。"""
    return await make_app({"RESTIC_BINARY": sys.executable})


async def _export(ns, *, password: str = PASSWORD, include_rclone_conf: bool = True):
    return await ns.client.post(
        "/api/site-backup/export",
        json={"password": password, "include_rclone_conf": include_rclone_conf},
        headers=ORIGIN,
    )


async def _inspect(ns, pack, *, password: str = PASSWORD):
    return await ns.client.post(
        "/api/site-backup/inspect",
        json={"pack": pack, "password": password},
        headers=ORIGIN,
    )


async def _import(ns, pack, *, acknowledge: str | None = None, password: str = PASSWORD):
    body: dict[str, object] = {"pack": pack, "password": password, "mode": "replace"}
    if acknowledge is not None:
        body["acknowledge"] = acknowledge
    return await ns.client.post("/api/site-backup/import", json=body, headers=ORIGIN)


async def _row_counts(ns) -> dict[str, int]:
    """五张纳入表 + sessions/jobs 的行数（零写入断言的观测面）。"""
    out: dict[str, int] = {}
    for name in ("repos", "backup_tasks", "config", "sessions", "jobs", "admin_account"):
        row = await ns.app.state.db.fetch_one(f"SELECT COUNT(*) FROM {name}")
        out[name] = int(row[0])
    return out


# ---------------------------------------------------------------- S4/S5 鉴权


async def test_all_endpoints_require_session(app_client):
    """三端点未登录一律 401（S4：新端点必须挂 require_session）。"""
    ns = app_client
    pack = {"format": "restic-web-site-backup", "version": 1}
    assert (await ns.client.post(
        "/api/site-backup/export", json={"password": PASSWORD}, headers=ORIGIN
    )).status_code == 401
    assert (await _inspect(ns, pack)).status_code == 401
    assert (await _import(ns, pack)).status_code == 401


async def test_write_endpoints_reject_cross_site_origin(site_app):
    """写方法过 Origin 同源校验（CSRF 中间件层，非路由内实现）。"""
    ns = site_app
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/site-backup/export",
        json={"password": PASSWORD},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "CSRF_ORIGIN"


# ---------------------------------------------------------------- 导出


async def test_export_returns_encrypted_attachment_and_audit(site_app):
    """导出：附件响应 + 包可解 + 明文零泄漏（S3）+ 审计只记计数（S6）。"""
    ns = site_app
    await finish_bootstrap(ns)

    resp = await _export(ns)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=\"restic-web-site-")

    pack = resp.json()
    assert pack["format"] == "restic-web-site-backup"
    # 明文头只有四个字段：包体其余部分必须完全不可读（S3 / §2.1）
    assert set(pack) == {"format", "version", "created_at", "encryption", "payload"}
    assert REPO_SECRET not in resp.text  # 仓库密码明文零泄漏
    assert PASSWORD not in resp.text

    rows, total = await ns.app.state.audit.query(action="site.export")
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["repos"] == 1 and detail["passfiles"] == 1
    assert detail["rclone_conf"] is True
    assert PASSWORD not in json.dumps(detail, ensure_ascii=False)
    assert REPO_SECRET not in json.dumps(detail, ensure_ascii=False)


async def test_export_refuses_when_passfile_missing(site_app):
    """在用仓库缺密码文件 → 422 fail-closed（能导不能读的包比不给包更糟）。"""
    ns = site_app
    await finish_bootstrap(ns)
    ns.app.state.settings.passfile_path("default").unlink()

    resp = await _export(ns)
    assert resp.status_code == 422
    assert resp.json()["code"] == "SITE_PASSFILE_INCOMPLETE"


async def test_export_password_too_short_422(site_app):
    """口令 <10 位由 schema 直接 422（不进打包管线，不烧 600k 迭代）。"""
    ns = site_app
    await finish_bootstrap(ns)
    resp = await _export(ns, password="short")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- inspect


async def test_inspect_is_read_only(site_app, rclone_conf):
    """inspect 零业务写入：DB 行数、密码文件、conf 字节调前调后逐项不变。"""
    ns = site_app
    await finish_bootstrap(ns)
    pack = (await _export(ns)).json()

    before_counts = await _row_counts(ns)
    before_pass = ns.app.state.settings.passfile_path("default").read_bytes()
    before_conf = rclone_conf.read_bytes()

    resp = await _inspect(ns, pack)
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["pack"]["format"] == "restic-web-site-backup"
    assert report["pack"]["passfiles"] == 1
    assert report["target"]["rollback_keep_count"] == 3
    assert report["target"]["restic_available"] is True
    assert report["target"]["active_jobs"] == []
    assert report["blockers"] == []

    assert await _row_counts(ns) == before_counts
    assert ns.app.state.settings.passfile_path("default").read_bytes() == before_pass
    assert rclone_conf.read_bytes() == before_conf
    # 只读端点不写审计（不是危险动作，且 S6 清单只登记 export / import）
    _rows, total = await ns.app.state.audit.query(action_prefix="site.")
    assert total == 1


async def test_inspect_rejects_legacy_config_pack(site_app):
    """旧配置迁移包 → 422 且文案指路新功能（D6：不兼容、不迁移）。"""
    ns = site_app
    await finish_bootstrap(ns)
    resp = await _inspect(ns, {"format": "restic-web-config-export", "version": 2})
    assert resp.status_code == 422
    assert resp.json()["code"] == "SITE_PACK_INVALID"
    assert "旧版配置迁移文件" in resp.json()["message"]


async def test_inspect_wrong_password_401(site_app):
    """口令错与篡改落到同一句 401（不给爆破者判别器）。"""
    ns = site_app
    await finish_bootstrap(ns)
    pack = (await _export(ns)).json()
    resp = await _inspect(ns, pack, password="WrongSitePass9")
    assert resp.status_code == 401
    assert resp.json()["code"] == "PASSWORD_WRONG"


async def test_inspect_reports_conf_encryption_mismatch(site_app, rclone_conf):
    """目标机 conf 改成加密态 → blockers 明示（预览即拦，不等到导入才 422）。"""
    ns = site_app
    await finish_bootstrap(ns)
    pack = (await _export(ns)).json()
    rclone_conf.write_bytes(b"# Encrypted rclone configuration File\n\nRCLONE_ENCRYPT_V0:\nAAAA\n")

    report = (await _inspect(ns, pack)).json()
    assert any("rclone conf 为明文态" in item for item in report["blockers"])


# ---------------------------------------------------------------- 导入


async def test_import_blocked_by_active_job(site_app):
    """有 running 作业 → 409 JOB_CONFLICT（D3 强制，不提供强杀后导入）。"""
    ns = site_app
    await finish_bootstrap(ns)
    pack = (await _export(ns)).json()
    await ns.app.state.db.write(
        "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, repo_id) "
        "VALUES ('job-live', 'backup', 'running', 'manual', '/data/jobs/j.log', "
        "'2026-01-01T00:00:00Z', 'default')"
    )

    resp = await _import(ns, pack, acknowledge=pack["created_at"])
    assert resp.status_code == 409
    assert resp.json()["code"] == "JOB_CONFLICT"
    # 一行都没动：repos 仍只有 default，任务表仍为空
    assert (await _row_counts(ns))["repos"] == 1


async def test_import_requires_acknowledge_on_non_empty_target(site_app):
    """目标非空且未带 acknowledge → 409，data 明示「必须等于包外层 created_at」。"""
    ns = site_app
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    pack = (await _export(ns)).json()

    resp = await _import(ns, pack)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "SITE_TARGET_NOT_EMPTY"
    assert body["data"]["acknowledge_required"] is True
    assert "created_at" in body["data"]["acknowledge"]




async def test_target_not_empty_returns_target_summary(site_app):
    """§4.7：409 必须一并返回目标现状摘要（repos/tasks/config/jobs 计数）。"""
    ns = site_app
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    pack = (await _export(ns)).json()

    body = (await _import(ns, pack)).json()
    assert body["code"] == "SITE_TARGET_NOT_EMPTY"
    target = body["data"]["target"]
    assert target["repos"] == 1 and target["repos_configured"] == 1
    assert target["tasks"] == 0 and "jobs" in target
    assert "config_keys" in target and "active_jobs" in target
    # 摘要只含计数/布尔/活跃作业清单，不含任何凭据
    assert PASSWORD not in json.dumps(target, ensure_ascii=False)


async def test_acknowledge_must_match_outer_created_at(site_app):
    """§4.2/§4.7：确认基准是**包外层** created_at，不是解密载荷内的那个。"""
    ns = site_app
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    pack = (await _export(ns)).json()
    inner_created_at = "2026-02-02T00:00:00Z"  # 载荷内（合法包两者恒等，此处造一个不同的）

    # 用外层 created_at 确认 → 放行；用载荷内的 created_at 确认 → 409
    ok = await _import(ns, pack, acknowledge=pack["created_at"])
    assert ok.status_code == 200, ok.text

    # 导入成功后 sessions 已清空（§4.6），重新登录再复验错误确认串
    assert (await ns.client.post("/api/auth/login", json={"password": NEW_PASSWORD}, headers=ORIGIN)).status_code == 200
    await ns.app.state.repos_repo.update("default", url="/repo")
    bad = await _import(ns, pack, acknowledge=inner_created_at)
    assert bad.status_code == 409
    assert bad.json()["code"] == "SITE_TARGET_NOT_EMPTY"




async def test_acknowledge_uses_outer_even_when_payload_created_at_differs(site_app):
    """§4.2：重封一份「载荷 created_at ≠ 包外层 created_at」的合法包，确认基准仍取外层。

    合法导出包两者恒等（build_site_pack 对齐），只有手工重封才能制造差异；
    AAD 只绑 format/version/created_at 三个头字段，故重封后 GCM 仍校验通过。
    若实现错用载荷内的 created_at 当基准，用外层值确认就会被 409 拒掉。
    """
    from app.services import pack_crypto
    from app.services import site_backup as sb

    ns = site_app
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    real = (await _export(ns)).json()
    state = await sb.parse_site_pack_for_app(ns.app, real, PASSWORD)
    state["created_at"] = "2026-02-02T00:00:00Z"  # 故意与外层头不同
    header = {key: real[key] for key in ("format", "version", "created_at")}
    aad = sb._canonical_header(header)
    enc_head, blob = pack_crypto.seal(payload=state, password=PASSWORD, aad=aad)
    forged = {**header, "encryption": {**enc_head, "aad": aad.decode("utf-8")}, "payload": blob}

    good = await _import(ns, forged, acknowledge=real["created_at"])  # 外层值
    assert good.status_code == 200, good.text
    assert (await ns.client.post("/api/auth/login", json={"password": NEW_PASSWORD}, headers=ORIGIN)).status_code == 200
    await ns.app.state.repos_repo.update("default", url="/repo")
    wrong = await _import(ns, forged, acknowledge=state["created_at"])  # 载荷内值
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "SITE_TARGET_NOT_EMPTY"

async def test_active_job_report_has_name_type_and_start(site_app):
    """§1.5：inspect 的活跃作业清单必须含任务名 + 类型 + 开始时间。"""
    ns = site_app
    await finish_bootstrap(ns)
    task = await ns.app.state.task_repo.create({"repo_id": "default", "name": "在跑的任务"})
    await ns.app.state.db.write(
        "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, started_at, repo_id, task_id) "
        "VALUES ('job-live', 'backup', 'running', 'manual', '/data/jobs/j.log', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', 'default', ?)",
        (task.id,),
    )
    pack = (await _export(ns)).json()
    report = (await _inspect(ns, pack)).json()
    line = report["target"]["active_jobs"][0]
    assert "在跑的任务" in line
    assert "backup" in line
    assert "2026-01-01T00:01:00Z" in line


async def test_env_rebuild_failure_does_not_leave_prepared_state(site_app, monkeypatch):
    """§4.5 第 6 步：DB 提交后先闭合崩溃窗口，env 重建失败不留 phase=prepared。"""
    from app.services import site_backup as sb

    ns = site_app
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    pack = (await _export(ns)).json()

    async def _boom(settings, rclone):
        raise OSError("cannot write password script")

    monkeypatch.setattr(sb, "_reload_rclone_password_env", _boom)
    with pytest.raises(OSError):
        await _import(ns, pack, acknowledge=pack["created_at"])
    pending = await sb.find_unfinished_restores(ns.app.state.settings)
    assert pending == [], "DB 已提交却仍留 prepared，启动期会误报混合态"


async def test_import_replaces_site_and_audits(site_app, rclone_conf):
    """整机替换成功路径：五表重建 + 文件回写 + 会话清空 + 审计只增不改。"""
    ns = site_app
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    await ns.client.post(
        "/api/repos/default/backup-tasks",
        json={"name": "搬家的任务", "sources": ["/etc"], "cron": "0 3 * * *"},
        headers=ORIGIN,
    )
    pack = (await _export(ns)).json()

    audit_before = await ns.app.state.db.fetch_all("SELECT ts, action, detail FROM audit_logs ORDER BY id")
    resp = await _import(ns, pack, acknowledge=pack["created_at"])
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["applied"]["repos"] == 1
    assert report["applied"]["tasks"] == 1
    assert report["applied"]["passfiles"] == 1
    assert report["rollback_dir"]
    assert report["logged_out"] is True

    # 任务 id / tag 原样搬回（task-<id> 是 restic 快照 tag 的契约）
    row = await ns.app.state.db.fetch_one("SELECT id, tag FROM backup_tasks")
    assert row["tag"] == f"task-{row['id']}"

    # 审计：旧行逐字不变（append-only），新增恰好一条 site.import
    audit_after = await ns.app.state.db.fetch_all("SELECT ts, action, detail FROM audit_logs ORDER BY id")
    assert [tuple(r) for r in audit_after[: len(audit_before)]] == [tuple(r) for r in audit_before]
    rows, total = await ns.app.state.audit.query(action="site.import")
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["mode"] == "replace"
    assert detail["rollback_dir"] == report["rollback_dir"]
    assert PASSWORD not in json.dumps(detail, ensure_ascii=False)

    # 导入即接管：sessions 已清空，下一个请求 401
    assert (await ns.client.get("/api/repos")).status_code == 401


async def test_import_mode_is_mandatory(site_app):
    """``mode`` 必填且只接受 replace（防误调：没有隐藏的默认覆盖语义）。"""
    ns = site_app
    await finish_bootstrap(ns)
    pack = (await _export(ns)).json()
    resp = await ns.client.post(
        "/api/site-backup/import",
        json={"pack": pack, "password": PASSWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- 崩溃窗口


async def test_lifespan_warns_on_unfinished_restore(make_app, rclone_conf, tmp_path, caplog):
    """启动期检测 ``state.json`` 仍为 prepared 的回滚点并点名告警（§4.5 兜底）。"""
    pending = tmp_path / "data" / "site-restore" / "2026-01-01T00-000000Z-abc123"
    pending.mkdir(parents=True)
    (pending / "state.json").write_text(json.dumps({"phase": "prepared"}), encoding="utf-8")

    with caplog.at_level("ERROR", logger="restic-web"):
        await make_app({"RESTIC_BINARY": sys.executable})
    messages = [record.getMessage() for record in caplog.records]
    assert any("未完成的全站导入" in message for message in messages)
    # 告警必须点名回滚点目录（人工据此决定继续导入还是回滚文件）
    assert any("2026-01-01T00-000000Z-abc123" in message for message in messages)


async def test_no_warning_when_restore_committed(make_app, rclone_conf, tmp_path, caplog):
    """已提交的回滚点不告警（只有 prepared 才是混合态）。"""
    done = tmp_path / "data" / "site-restore" / "2026-01-02T00-000000Z-def456"
    done.mkdir(parents=True)
    (done / "state.json").write_text(json.dumps({"phase": "committed"}), encoding="utf-8")

    with caplog.at_level("ERROR", logger="restic-web"):
        await make_app({"RESTIC_BINARY": sys.executable})
    assert not any("未完成的全站导入" in record.getMessage() for record in caplog.records)
