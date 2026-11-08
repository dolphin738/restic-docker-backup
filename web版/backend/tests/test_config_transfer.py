"""单任务配置包（``kind=backup-task``）打包/解析管线 + ``POST /api/config/import` 收窄。

阶段 ②（owner 2026-11-08，D5/D6）后本模块的职责边界：

1. 全量「配置迁移」已由《全站数据备份与恢复设计方案》取代——全量导出端点
   ``POST /api/config/export` 已删除，全量导入支路已删除（含 ``notify_pairs_to_import`）；
2. ``POST /api/config/import` **收窄保留**：只接受 ``kind=backup-task``，``kind=full`` 与
   无 kind 的旧全量包一律 422 并指路新功能（Q3 / §9.3-G8）；
3. 单任务包的既有语义不变：凭据口令加密、mirror 引用接前校验、同仓同名任务跳过。

保留的三裁决口径（owner 2026-10-18，单任务包仍适用）：

1. 凭据口令加密（PBKDF2 600k + AES-256-GCM）：错口令 = GCM InvalidTag = 明确报错；
2. 包内带仓清单，mirror 引用接前校验「id 在包内且 url 一致」，否则置空 + 警告；
3. 落位按匹配、不覆盖（单任务包没有覆盖语义）。
"""

from __future__ import annotations

import base64
import json

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.errors import PasswordWrongError, ValidationError
from app.services.config_transfer import (
    PACK_FORMAT,
    PACK_KIND_TASK,
    build_export_pack,
    creds_to_import,
    mirror_ids_for_import,
    parse_export_pack,
    task_values_from_pack,
)

PW = "export-pass-123"


# ---------------------------------------------------------------- 纯函数


def _mini_pack(password: str = PW) -> dict:
    """最小**单任务包**（两仓 + 一任务 + 凭据）。"""
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
                # host_tag 是 repos 表字段，被 TaskRepo.get_dict 契约带进包——
                # 落库前必须剔除（真机 E2E 2026-10-18：漏剔则 create 路径 500）
                "host_tag": "nas",
            }
        ],
        config_rows=[("cred.default.access_key", "AKID")],
        password=password,
        created_at="2026-10-18T00:00:00Z",
    )


def test_roundtrip_and_secret_isolation():
    pack = _mini_pack()
    raw = json.dumps(pack)
    # 明文段绝不出现凭据（S3：加密段之外零明文）
    assert "AKID" not in raw
    assert pack["format"] == PACK_FORMAT
    assert pack["kind"] == PACK_KIND_TASK  # 单任务包是唯一合法形态
    parsed = parse_export_pack(pack, PW)
    assert parsed["kind"] == PACK_KIND_TASK
    assert parsed["creds"]["cred.default.access_key"] == "AKID"
    assert parsed["notify"] == {}


def test_full_kind_rejected_with_pointer():
    """kind=full 的全量包：端点已收窄 → 422 且指路「全站备份 / 恢复」（D5/D6）。"""
    pack = _mini_pack()
    pack["kind"] = "full"
    with pytest.raises(ValidationError) as excinfo:
        parse_export_pack(pack, PW)
    message = str(excinfo.value)
    assert "全量表" in message or "全量配置迁移包" in message
    assert "全站备份" in message


def test_missing_kind_legacy_full_pack_rejected():
    """无 kind 的旧全量包同样拒绝——不猜形态（阶段 ② 起不再回退 full）。"""
    pack = _mini_pack()
    del pack["kind"]
    with pytest.raises(ValidationError) as excinfo:
        parse_export_pack(pack, PW)
    assert "全站备份" in str(excinfo.value)


def test_degraded_rows_marked_in_v2_pack():
    """v2：密钥源失效的键必须带 degraded 标记——否则接收端分不清「没配」与「读不出来」。"""
    pack = build_export_pack(
        repos=[{"id": "default", "name": "主仓", "url": "local:/main", "host_tag": "nas"}],
        tasks=[],
        config_rows=[("cred.default.access_key", "")],
        password=PW,
        created_at="2026-11-02T00:00:00+00:00",
        degraded_rows={"cred.default.access_key": "密钥源 default 的口令文件缺失或不可读，该键无法解密"},
    )
    assert pack["version"] == 2
    # 降级信息进加密段：明文段不得出现键名与原因（键名本身即暴露「配过哪些凭据」）
    raw = json.dumps(pack)
    assert "access_key" not in raw
    assert "口令文件缺失" not in raw
    parsed = parse_export_pack(pack, PW)
    assert parsed["degraded"]["cred.default.access_key"].startswith("密钥源 default")
    # 值仍是空串——降级行本来就读不出来
    assert parsed["creds"]["cred.default.access_key"] == ""


def test_pack_without_degraded_has_empty_mapping():
    """无降级行时不写该字段；解析侧回空 dict（旧行为不变）。"""
    pack = _mini_pack()
    assert parse_export_pack(pack, PW)["degraded"] == {}


def test_v1_pack_still_importable():
    """A1 决策：升 v2 但导入端接受 {1,2}，开发期旧包不作废。"""
    pack = _mini_pack()
    pack["version"] = 1
    parsed = parse_export_pack(pack, PW)
    assert parsed["creds"]["cred.default.access_key"] == "AKID"
    assert parsed["degraded"] == {}


def test_unsupported_pack_version_rejected():
    pack = _mini_pack()
    pack["version"] = 99
    with pytest.raises(ValidationError):
        parse_export_pack(pack, PW)


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
    for dropped in (
        "id", "tag", "created_at", "updated_at", "last_run_at",
        "dry_run_pending", "host_tag",
    ):
        assert dropped not in values
    assert values["repo_id"] == "default"
    assert values["mirror_repo_ids"] == ["mir"]


def test_creds_filtered_by_imported_repos():
    pairs = creds_to_import(
        {"cred.default.k": "v", "cred.ghost.k": "v2"}, ["default"]
    )
    assert pairs == [("cred.default.k", "v")]


# ---------------------------------------------------------------- API 全链路


async def test_import_rejects_full_pack_422(app_client):
    """全量包走 ``/api/config/import`` → 422 且指路新功能（D5/D6 的端点边界）。"""
    ns = app_client
    await finish_bootstrap(ns)
    pack = _mini_pack()
    pack["kind"] = "full"
    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW}, headers=ORIGIN
    )
    assert resp.status_code == 422, resp.text
    assert "全站备份" in resp.json()["message"]


async def test_import_task_pack_creates_repos_tasks_and_credentials(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    pack = _mini_pack()
    # bootstrap 预建的 default 仓 url 为空 → 对齐包内 url 走「复用」分支
    await ns.app.state.repos_repo.update("default", url="local:/main")
    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    # bootstrap 已预建 default（复用），mir 为新建
    assert report["repos"]["created"] == ["mir"]
    assert report["repos"]["reused"] == ["default"]
    assert report["tasks"]["created"] == ["每日全量"]
    assert report["mirror_warnings"] == []  # url 一致 → mirror 原样接上
    assert report["creds_imported"] >= 1
    assert "updated" not in report["repos"] and "updated" not in report["tasks"]
    assert "notify_updated" not in report  # 单任务包不写全局 notify

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

    rows, _total = await ns.app.state.audit.query(action="config.import", limit=5)
    assert any(r.result == "ok" for r in rows)


async def test_import_url_mismatch_empties_mirror_without_overwrite(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    pack = _mini_pack()
    # 目标机先存在同 id 但 url 不同的仓
    await ns.app.state.repos_repo.update("default", url="local:/other")
    await ns.app.state.repos_repo.create("mir", "镜像", url="local:/different")
    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW}, headers=ORIGIN
    )
    assert resp.status_code == 200
    report = resp.json()
    # 两仓 url 都与包内不一致 → 跳过；任务的 mirror 引用被置空 + 警告
    assert len(report["repos"]["skipped"]) == 2
    assert report["tasks"]["created"] == ["每日全量"]
    assert report["mirror_warnings"] != []
    tasks = await ns.app.state.task_repo.list_all("default")
    assert all(t.mirror_repo_ids == [] for t in tasks if t.name == "每日全量")


async def test_import_never_overwrites_existing_task(app_client):
    """单任务包**没有覆盖语义**：目标机同名任务改动后重导 → 一律跳过、库内不变。"""
    ns = app_client
    await finish_bootstrap(ns)
    pack = _mini_pack()
    await ns.client.post("/api/config/import", json={"pack": pack, "password": PW}, headers=ORIGIN)
    tasks = await ns.app.state.task_repo.list_all("default")
    mine = next(t for t in tasks if t.name == "每日全量")
    await ns.app.state.task_repo.update(mine.id, {"cron": "0 5 * * *"})

    resp = await ns.client.post(
        "/api/config/import", json={"pack": pack, "password": PW}, headers=ORIGIN
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["tasks"]["created"] == []
    assert report["tasks"]["skipped"][0]["reason"].startswith("单任务包不覆盖")
    unchanged = await ns.app.state.task_repo.get(mine.id)
    assert unchanged is not None and unchanged.cron == "0 5 * * *"


async def test_import_rejects_replace_existing_field(app_client):
    """旧客户端多发的 replace_existing 不再被静默忽略（extra=forbid → 422）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/config/import",
        json={"pack": _mini_pack(), "password": PW, "replace_existing": True},
        headers=ORIGIN,
    )
    assert resp.status_code == 422


async def test_import_wrong_password_4xx(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/config/import",
        json={"pack": _mini_pack(), "password": "wrong-pass-999"},
        headers=ORIGIN,
    )
    assert resp.status_code == 401  # PasswordWrongError（导出口令错，密码类统一 401）
