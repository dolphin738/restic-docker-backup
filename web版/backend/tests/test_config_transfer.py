"""批次 E 配置导入/导出（D4）——加密往返 / 归一辅助 / API 全链路。

三裁决口径（owner 2026-10-18）：

1. 凭据口令加密（PBKDF2 600k + AES-256-GCM）：错口令 = GCM InvalidTag = 明确报错；
2. 包内带仓清单，mirror 引用接前校验「id 在包内且 url 一致」，否则置空 + 警告；
3. 冲突默认跳过 + 报告；``replace_existing=True`` 整体替换（幂等重导 = 全跳过）。
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.errors import PasswordWrongError, ValidationError
from app.services.config_transfer import (
    PACK_FORMAT,
    build_export_pack,
    creds_to_import,
    mirror_ids_for_import,
    notify_pairs_to_import,
    parse_export_pack,
    task_values_from_pack,
)

PW = "export-pass-123"


# ---------------------------------------------------------------- 纯函数


def _mini_pack(password: str = PW) -> dict[str, Any]:
    """最小导出包（两仓 + 一任务 + 凭据 + notify 明文/敏感各一）。"""
    return build_export_pack(
        repos=[
            {"id": "default", "name": "主仓", "url": "local:/main", "host_tag": "nas"},
            {"id": "mir", "name": "镜像", "url": "local:/mir", "host_tag": "nas"},
        ],
        tasks=[
            {
                "id": 7,
                "tag": "task-7",
                "repo_id": "default",
                "name": "每日全量",
                "sources": ["/sources/etc"],
                "excludes": [],
                "mirror_repo_ids": ["mir"],
                "retry_count": 2,
                "last_run_at": "2026-01-01T00:00:00Z",
                "dry_run_pending": 0,
            }
        ],
        config_rows=[
            ("behavior.concurrency", "2"),
            ("notify.mail.from", "a@b.c"),
            ("cred.default.access_key", "AKID"),
            ("notify.webhook.url", "https://hook.example/tok"),
        ],
        password=password,
        created_at="2026-10-18T00:00:00Z",
    )


def test_roundtrip_and_secret_isolation():
    pack = _mini_pack()
    raw = json.dumps(pack)
    # 明文段绝不出现凭据/敏感值（S3：加密段之外零明文）
    assert "AKID" not in raw
    assert "https://hook.example/tok" not in raw
    assert pack["format"] == PACK_FORMAT
    parsed = parse_export_pack(pack, PW)
    assert parsed["creds"]["cred.default.access_key"] == "AKID"
    assert parsed["creds"]["notify.webhook.url"] == "https://hook.example/tok"
    assert parsed["notify"]["behavior.concurrency"] == "2"
    assert parsed["notify"]["notify.mail.from"] == "a@b.c"
    assert "notify.webhook.url" not in parsed["notify"]  # 敏感键以加密段为准


def test_wrong_password_rejected():
    pack = _mini_pack()
    with pytest.raises(PasswordWrongError):
        parse_export_pack(pack, "wrong-pass-999")


def test_password_too_short_rejected():
    with pytest.raises(ValidationError):
        _mini_pack("short")


def test_tampered_pack_rejected():
    pack = _mini_pack()
    blob = pack["data"]["secrets"]
    decoded = bytearray(base64.b64decode(blob))
    decoded[-1] ^= 0xFF
    pack["data"]["secrets"] = base64.b64encode(bytes(decoded)).decode("ascii")
    with pytest.raises(PasswordWrongError):
        parse_export_pack(pack, PW)


def test_mirror_ids_rules():
    pack_urls = {"default": "local:/main", "mir": "local:/mir"}
    final_same = {"default": "local:/main", "mir": "local:/mir"}
    final_diff = {"default": "local:/main", "mir": "local:/changed"}
    kept, warns = mirror_ids_for_import(["mir", "mir"], pack_urls, final_same)
    assert kept == ["mir"] and warns == []
    kept, warns = mirror_ids_for_import(["mir"], pack_urls, final_diff)
    assert kept == [] and warns[0]["mirror_id"] == "mir" and "url" in warns[0]["reason"]
    kept, warns = mirror_ids_for_import(["ghost"], pack_urls, final_same)
    assert kept == [] and "不存在" in warns[0]["reason"]


def test_task_values_drops_cross_machine_columns():
    values = task_values_from_pack(_mini_pack()["data"]["tasks"][0], "default", ["mir"])
    for dropped in ("id", "tag", "created_at", "updated_at", "last_run_at", "dry_run_pending"):
        assert dropped not in values
    assert values["repo_id"] == "default"
    assert values["mirror_repo_ids"] == ["mir"]


def test_creds_filtered_by_imported_repos():
    pairs = creds_to_import(
        {"cred.default.k": "v", "cred.ghost.k": "v2"}, ["default"]
    )
    assert pairs == [("cred.default.k", "v")]


def test_notify_pairs_merge_sensitive_from_creds():
    pairs = dict(
        notify_pairs_to_import(
            {"notify.mail.from": "a@b.c", "notify.webhook.url": "SHOULD-NOT"},
            {"notify.webhook.url": "https://real"},
        )
    )
    assert pairs["notify.mail.from"] == "a@b.c"
    assert pairs["notify.webhook.url"] == "https://real"  # 加密段优先


# ---------------------------------------------------------------- API 全链路


async def test_export_rejects_short_password(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post("/api/config/export", json={"password": "short"}, headers=ORIGIN)
    assert resp.status_code == 422


async def test_export_download_and_roundtrip_import(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="local:/main")
    await ns.app.state.repos_repo.create("mir", "镜像", url="local:/mir")
    await ns.app.state.task_repo.create(
        {"repo_id": "default", "name": "每日全量", "sources": ["/sources/etc"], "excludes": []}
    )
    await ns.app.state.config_repo.set("cred.default.access_key", "AKID")

    resp = await ns.client.post("/api/config/export", json={"password": PW}, headers=ORIGIN)
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    pack = resp.json()
    assert pack["format"] == PACK_FORMAT
    assert "AKID" not in json.dumps(pack)  # 明文零泄漏
    rows, _total = await ns.app.state.audit.query(action="config.export", limit=5)
    assert any(r.result == "ok" for r in rows)

    # 导入：同机重导 → 幂等全跳过
    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW, "replace_existing": False}, headers=ORIGIN
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["repos"]["reused"] == ["default", "mir"]
    assert report["tasks"]["skipped"][0]["name"] == "每日全量"
    assert report["creds_imported"] >= 1
    rows, _total = await ns.app.state.audit.query(action="config.import", limit=5)
    assert any(r.result == "ok" for r in rows)


async def test_import_creates_repos_tasks_and_credentials(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    pack = _mini_pack()
    # bootstrap 预建的 default 仓 url 为空 → 对齐包内 url 走「复用」分支
    await ns.app.state.repos_repo.update("default", url="local:/main")
    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW, "replace_existing": False}, headers=ORIGIN
    )
    assert resp.status_code == 200
    report = resp.json()
    # bootstrap 已预建 default（复用），mir 为新建
    assert report["repos"]["created"] == ["mir"]
    assert report["repos"]["reused"] == ["default"]
    assert report["tasks"]["created"] == ["每日全量"]
    assert report["mirror_warnings"] == []  # url 一致 → mirror 原样接上
    assert report["creds_imported"] >= 1

    repo = await ns.app.state.repos_repo.get("mir")
    assert repo is not None and repo.url == "local:/mir"
    # 凭据落库且为加密形态（读侧透明解密）
    value = await ns.app.state.config_repo.get_value("cred.default.access_key")
    assert value == "AKID"
    raw = await ns.app.state.config_repo.list_all_raw()
    stored = next(str(r.value) for r in raw if r.key == "cred.default.access_key")
    assert "AKID" not in stored  # 库内非明文
    tasks = await ns.app.state.task_repo.list_all("default")
    assert any(t.name == "每日全量" and t.mirror_repo_ids == ["mir"] for t in tasks)


async def test_import_url_mismatch_empties_mirror_without_overwrite(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    pack = _mini_pack()
    # 目标机先存在同 id 但 url 不同的仓
    await ns.app.state.repos_repo.update("default", url="local:/other")
    await ns.app.state.repos_repo.create("mir", "镜像", url="local:/different")
    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW, "replace_existing": False}, headers=ORIGIN
    )
    assert resp.status_code == 200
    report = resp.json()
    # 两仓 url 都与包内不一致 → 跳过；任务的 mirror 引用被置空 + 警告
    assert len(report["repos"]["skipped"]) == 2
    assert report["tasks"]["created"] == ["每日全量"]
    assert report["mirror_warnings"] != []
    tasks = await ns.app.state.task_repo.list_all("default")
    assert all(t.mirror_repo_ids == [] for t in tasks if t.name == "每日全量")


async def test_import_overwrite_replaces_task_and_repo(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    pack = _mini_pack()
    await ns.client.post("/api/config/import", json={"pack": pack, "password": PW, "replace_existing": False})
    # 目标机改 url + 改任务名后，覆盖导入应替换为包内版本
    await ns.app.state.repos_repo.update("default", url="local:/changed")
    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW, "replace_existing": True}, headers=ORIGIN
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["repos"]["updated"] == ["default"]
    repo = await ns.app.state.repos_repo.get("default")
    assert repo is not None and repo.url == "local:/main"


async def test_import_wrong_password_4xx(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/config/import",
        json={"pack": _mini_pack(), "password": "wrong-pass-999"},
        headers=ORIGIN,
    )
    assert resp.status_code == 401  # PasswordWrongError（导出口令错，密码类统一 401）
