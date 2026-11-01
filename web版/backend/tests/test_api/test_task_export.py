"""API 集成：P2 单任务配置导出 ``POST /api/repos/{repo_id}/backup-tasks/{task_id}/export``。

断言点：
- **任务域**：包只含这一条任务 + 所属仓（+ 镜像仓），``kind=backup-task``，
  不带全局 ``notify`` / ``behavior``（那是全量包的语义）；
- **S3**：凭据以加密段携带，响应体**不含明文**；
- **口令**：过短 → 422（复用 ``build_export_pack`` 的统一校验，不另造一套）；
- **作用域**：任务不存在 → 404；
- **S6**：埋 ``config.export`` 审计，detail 含任务名与计数、**不含口令/凭据**；
- **kind 白名单**：未知形态的包解析直接拒绝（导入侧不猜）。
"""

from __future__ import annotations

import json

from conftest import ORIGIN, finish_bootstrap

from app.errors import ValidationError
from app.services.config_transfer import (
    PACK_FORMAT,
    PACK_KIND_FULL,
    PACK_KIND_TASK,
    PACK_VERSION,
    parse_export_pack,
)

PASSWORD = "export-pass-2026"
SECRET = "SUPER-SECRET-PLAINTEXT-9"


async def _create_task(ns, **overrides) -> dict:
    """经 API 创建任务并返回响应体。"""
    payload = {"name": "每日全量", "sources": ["/etc"]}
    payload.update(overrides)
    resp = await ns.client.post("/api/repos/default/backup-tasks", json=payload, headers=ORIGIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _export(ns, task_id: int, password: str = PASSWORD, repo_id: str = "default"):
    """调用单任务导出端点。"""
    return await ns.client.post(
        f"/api/repos/{repo_id}/backup-tasks/{task_id}/export",
        json={"password": password},
        headers=ORIGIN,
    )


async def test_export_returns_task_scoped_pack(app_client):
    """导出包：kind=backup-task、只含该任务与所属仓、可凭口令回解。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns, cron="0 3 * * *")
    await ns.app.state.repos_repo.update("default", url="/repo")

    resp = await _export(ns, task["id"])
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert f"restic-web-task-{task['id']}-" in resp.headers["content-disposition"]
    assert "attachment" in resp.headers["content-disposition"]

    pack = resp.json()
    assert pack["format"] == PACK_FORMAT
    assert pack["version"] == PACK_VERSION
    assert pack["kind"] == PACK_KIND_TASK

    parsed = parse_export_pack(pack, PASSWORD)
    assert parsed["kind"] == PACK_KIND_TASK
    assert [item["name"] for item in parsed["tasks"]] == ["每日全量"]  # 只有这一条
    assert [repo["id"] for repo in parsed["repos"]] == ["default"]
    assert parsed["notify"] == {}  # 全局 notify 不搭单任务包顺风车


async def test_export_carries_repo_credentials_encrypted(app_client):
    """凭据随包走（加密段），但响应体绝不含明文（S3）。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns)
    await ns.app.state.config_repo.set("cred.default.repo_password", SECRET)

    resp = await _export(ns, task["id"])
    assert resp.status_code == 200
    assert SECRET not in resp.text  # 明文零泄漏

    parsed = parse_export_pack(resp.json(), PASSWORD)
    assert parsed["creds"]["cred.default.repo_password"] == SECRET  # 加密段可回解


async def test_export_excludes_other_repo_credentials(app_client):
    """其它仓库的凭据不进单任务包（任务域收敛）。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns)
    await ns.app.state.config_repo.set("cred.default.repo_password", SECRET)
    # B1 严格语义：非 default 仓库要有自己的口令文件才存得下凭据
    await ns.app.state.repos_repo.create("other", "其他仓库")
    other_passfile = ns.app.state.settings.passfile_path("other")
    other_passfile.parent.mkdir(parents=True, exist_ok=True)
    other_passfile.write_text("other-password", encoding="utf-8")
    await ns.app.state.config_repo.set("cred.other.repo_password", "OTHER-SECRET-7")

    parsed = parse_export_pack((await _export(ns, task["id"])).json(), PASSWORD)
    assert "cred.default.repo_password" in parsed["creds"]
    assert "cred.other.repo_password" not in parsed["creds"]


async def test_export_password_too_short_422(app_client):
    """口令 <10 位 → 422 VALIDATION_ERROR（不进打包管线）。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns)

    resp = await _export(ns, task["id"], password="short-1")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_export_missing_task_404(app_client):
    """任务不存在 → 404 NOT_FOUND（不泄漏其它仓库的任务）。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await _export(ns, 999)
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_export_requires_session(app_client):
    """未登录 → 401（S4：新端点挂 require_session）。"""
    ns = app_client
    resp = await _export(ns, 1)
    assert resp.status_code == 401


async def test_export_records_audit_without_password(app_client):
    """审计埋 config.export：detail 含任务名与计数，绝不含口令/凭据（S6 + S3）。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns)
    await ns.app.state.config_repo.set("cred.default.repo_password", SECRET)

    assert (await _export(ns, task["id"])).status_code == 200
    rows, total = await ns.app.state.audit.query(action="config.export")
    assert total == 1
    assert rows[0].target == f"task:{task['id']}"
    detail = json.loads(rows[0].detail or "{}")
    assert detail["kind"] == PACK_KIND_TASK
    assert detail["name"] == "每日全量"
    assert detail["creds"] == 1
    assert PASSWORD not in json.dumps(detail, ensure_ascii=False)
    assert SECRET not in json.dumps(detail, ensure_ascii=False)


async def test_parse_rejects_unknown_pack_kind():
    """未知 kind 的包解析直接拒绝——导入侧不猜形态（R-5 的入口护栏）。"""
    pack = {"format": PACK_FORMAT, "version": PACK_VERSION, "kind": "bogus"}
    try:
        parse_export_pack(pack, PASSWORD)
    except ValidationError as exc:
        assert "形态不支持" in str(exc)
    else:  # pragma: no cover - 走到这里即护栏失效
        raise AssertionError("未知 kind 应被拒绝")


async def test_parse_defaults_missing_kind_to_full(app_client):
    """旧版无 kind 字段的包 → 视为全量包（兼容既有导出文件）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    await _create_task(ns)

    resp = await ns.client.post("/api/config/export", json={"password": PASSWORD}, headers=ORIGIN)
    assert resp.status_code == 200
    pack = resp.json()
    assert pack["kind"] == PACK_KIND_FULL
    assert parse_export_pack(pack, PASSWORD)["kind"] == PACK_KIND_FULL


async def test_import_task_pack_skips_global_notify_and_never_overwrites(app_client):
    """单任务包导入按 kind 分流（方案 §4.5.9 R-5）：不碰全局 notify，且不覆盖已有任务。

    三条断言对应 R-5 的三个「绝不」：
    1. 即使 ``replace_existing=true`` 也不覆盖同仓同名任务（单任务包没有全量覆盖语义）；
    2. 全局 ``notify.*`` 一个字节都不动（包内无全局设置，空段不得被读成「清空」意图）；
    3. 审计 detail 记 ``kind=backup-task``，便于事后区分两种导入。
    """
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns, cron="0 3 * * *")
    await ns.app.state.repos_repo.update("default", url="/repo")
    await ns.app.state.config_repo.set("notify.on_success", "false")

    pack = (await _export(ns, task["id"])).json()
    resp = await ns.client.post(
        "/api/config/import",
        json={"pack": pack, "password": PASSWORD, "replace_existing": True},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()

    # ① 已有同名任务：不覆盖（replace_existing=true 也不生效）
    assert report["tasks"]["created"] == []
    assert report["tasks"]["updated"] == []
    assert report["tasks"]["skipped"][0]["reason"].startswith("单任务包不覆盖")
    unchanged = await ns.app.state.task_repo.get(task["id"])
    assert unchanged is not None and unchanged.cron == "0 3 * * *"

    # ② 全局通知未被触碰
    assert report["notify_updated"] == 0
    assert await ns.app.state.config_repo.get_value("notify.on_success") == "false"

    # ③ 审计记形态
    rows, _total = await ns.app.state.audit.query(action="config.import")
    detail = json.loads(rows[0].detail or "{}")
    assert detail["kind"] == PACK_KIND_TASK
