"""API 集成：**host 部署形态**下的恢复链路（owner 2026-10-25 裁决 Q1=A / Q2 同批）。

存在理由（取证背景）：
- 恢复白名单历史上是固定常量（``/restore`` / ``/sources``），而 host 模式快照里存的是
  **主机绝对路径**（如 ``/srv/data``）——固定 ``/sources`` 会让合法原位恢复直接 400；
- 前端又把「展示形态 → 容器形态」写死为 ``/sources + display``，host 模式下
  ``to_display`` 恒等（display 已含前缀）→ 拼出 ``/sources/sources/srv/data`` → 403
  「原位恢复目标不在该快照的备份路径范围内」（用户实拍缺陷）。

裁决后的契约（本文件锁死）：
1. ``restore_allowed_prefixes``：docker → ``/restore``；host → ``/``（deny 兜底）；
2. ``inplace_restore_allowed_prefixes``：与备份源同源 → docker ``/sources`` / host ``/``；
3. ``GET /api/system/runtime`` 同时下发 ``restore_root`` 与 ``inplace_root``（宿主形态均 ``/``）；
4. host 模式下 display == container（恒等），E20/E19 均按主机真实路径工作；
5. deny 命中（仓库/数据目录、``/proc`` 等）→ 400（不被降级成 403）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.constants import TERMINAL_JOB_STATUSES
from app.services import compare as compare_module
from app.services.restic import ProbeResult

SNAPSHOT_ID = "b" * 64
SHORT_ID = SNAPSHOT_ID[:8]
HOST_ROOT = "/srv/data"  # host 模式下的真实主机路径（快照存储形态 == 展示形态）
SNAPSHOT_MTIME = "2026-01-03T12:00:00Z"
DISK_MTIME = "2026-02-04T08:30:00Z"


def _epoch(value: str) -> float:
    from app.utils.time import parse_iso

    moment = parse_iso(value)
    assert moment is not None
    return moment.timestamp()


class _FakeOs:
    """``os.stat`` 替身：默认磁盘上存在且 mtime 与快照不同（→ 记为待覆盖）。"""

    def stat(self, path: str) -> SimpleNamespace:  # noqa: ANN001
        del path
        return SimpleNamespace(st_mtime=_epoch(DISK_MTIME), st_size=512)


@pytest.fixture()
def fake_disk(monkeypatch):
    """把 compare 模块的 ``os`` 换成替身（与 docker 模式用例同款手法）。"""
    fake = _FakeOs()
    monkeypatch.setattr(compare_module, "os", fake)
    return fake


def _patch_run_json(ns, *, snapshot_paths: list[str], names: list[str]) -> None:
    """替身 run_json：``snapshots`` 回录 paths，``ls`` 回录容器形态文件条目。"""

    async def _run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        verb = "snapshots" if "snapshots" in argv else "ls"
        if verb == "snapshots":
            lines = [
                json.dumps(
                    [
                        {
                            "id": SNAPSHOT_ID,
                            "short_id": SHORT_ID,
                            "time": "2026-01-03T12:00:00.000000000Z",
                            "tree": "0" * 64,
                            "paths": snapshot_paths,
                            "hostname": "host-01",
                            "tags": ["task-1"],
                        }
                    ]
                )
            ]
        else:
            lines = [
                json.dumps(
                    {
                        "name": name,
                        "type": "file",
                        "path": f"{HOST_ROOT}/{name}",
                        "size": 512 + index,
                        "mtime": SNAPSHOT_MTIME,
                        "struct_type": "node",
                    }
                )
                for index, name in enumerate(names)
            ]
        return ProbeResult(exit_code=0, lines=lines, truncated=False)

    ns.app.state.restic.run_json = _run_json


def _patch_spawn(ns) -> None:
    """阻断真实 restic 子进程（任务走 failed 终态，不影响 202 响应断言）。"""

    async def _spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        del argv, log_path, env
        raise OSError("spawn blocked in test")

    ns.app.state.restic.spawn = _spawn


async def _setup(ns, *, snapshot_paths: list[str] | None = None, names: list[str] | None = None) -> None:
    """登录 + 仓库 url + restic 替身（host 形态快照路径为真实主机绝对路径）。"""
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    _patch_run_json(ns, snapshot_paths=snapshot_paths or [HOST_ROOT], names=names or ["a.log"])
    _patch_spawn(ns)


async def _host_app(make_app):
    """host 形态应用（DEPLOY_MODE=host → browse_root=/，与 WSL/主机自动检测一致）。"""
    return await make_app({"DEPLOY_MODE": "host"})


async def _wait_terminal(ns, job_id: str) -> None:
    for _ in range(200):
        job = await ns.app.state.job_repo.get(job_id)
        if job is not None and job.status in TERMINAL_JOB_STATUSES:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"任务 {job_id} 未在预期时间内进入终态")


# ---------------------------------------------------------------- runtime 契约


async def test_runtime_host_restore_roots_are_slash(make_app):
    """host 形态：restore_root / inplace_root 均随部署形态派生为 ``/``。"""
    ns = await _host_app(make_app)
    await finish_bootstrap(ns)

    resp = await ns.client.get("/api/system/runtime")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deploy_mode"] == "host"
    assert body["browse_root"] == "/"
    assert body["restore_root"] == "/"
    assert body["inplace_root"] == "/"
    assert body["deny_prefixes"], "host 模式必须下发 deny 前缀"


async def test_runtime_docker_roots_keep_container_paths(app_client):
    """docker 形态（测试套件缺省钉死）：restore_root=/restore、inplace_root=/sources。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/system/runtime")
    body = resp.json()
    assert body["restore_root"] == "/restore"
    assert body["inplace_root"] == "/sources"
    assert body["deny_prefixes"] == []


# ---------------------------------------------------------------- E20 预览（host）


async def test_e20_preview_host_identity_display(make_app, fake_disk):
    """host 模式：display == container（恒等），预览按主机真实路径工作。"""
    ns = await _host_app(make_app)
    await _setup(ns, names=["a.log", "b.log"])

    resp = await ns.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": HOST_ROOT},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overwrite_count"] == 2
    item = body["items"][0]
    assert item["path"] == f"{HOST_ROOT}/a.log"
    assert item["display_path"] == f"{HOST_ROOT}/a.log"  # host 无前缀可剥 → 恒等


# ---------------------------------------------------------------- E19 原位（host）


async def test_e19_inplace_host_accepts_host_path(make_app, fake_disk):
    """host 原位：目标 = 快照内的主机路径，不叠 ``/sources`` 前缀。"""
    ns = await _host_app(make_app)
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": HOST_ROOT, "overwrite_mode": "always"},
        headers=ORIGIN,
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["restore_target"] == HOST_ROOT

    rows, total = await ns.app.state.audit.query(action="restore.inplace")
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["target"] == HOST_ROOT
    assert detail["overwrite_mode"] == "always"


async def test_e19_inplace_host_double_prefix_now_403(make_app, fake_disk):
    """反证：前端旧实现的双前缀 ``/sources/sources/...`` 在 host 模式必须被拒（403）。

    该形态是用户实拍缺陷的请求体；修复后前端不再产生它，但后端仍须拒绝——
    防止未来任何调用方把容器前缀叠加到主机路径上。
    """
    ns = await _host_app(make_app)
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={
            "snapshot_id": SNAPSHOT_ID,
            "target": "/sources/sources/srv/data",
            "overwrite_mode": "never",
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 403, resp.text
    assert "不在该快照的备份路径范围内" in resp.json()["message"]


async def test_e19_inplace_host_out_of_snapshot_403(make_app, fake_disk):
    """host 原位：同一白名单（/）内但不在快照 paths 内 → 403（交集收紧仍生效）。"""
    ns = await _host_app(make_app)
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": "/opt/other", "overwrite_mode": "never"},
        headers=ORIGIN,
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------- E19 时间戳（host）


async def test_e19_timestamp_host_accepts_host_target(make_app, fake_disk):
    """host 时间戳流：目标可为主机目录，服务端在其下建时间戳子目录（零覆盖不变）。"""
    ns = await _host_app(make_app)
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": "/srv"},
        headers=ORIGIN,
    )
    assert resp.status_code == 202, resp.text
    restore_target = resp.json()["restore_target"]
    # 时间戳子目录名 = 快照路径的**顶层目录名**（/srv/data → srv，E19 既有规则，本批未改）
    assert restore_target.startswith("/srv/srv-"), restore_target
    await _wait_terminal(ns, resp.json()["job_id"])


async def test_e19_host_denied_region_400(make_app, fake_disk):
    """host deny：``/proc`` 等受保护区域 → 400（不得降级成 403「不在白名单」）。"""
    ns = await _host_app(make_app)
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": "/proc/self"},
        headers=ORIGIN,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "PATH_OUT_OF_PREFIX"
    assert "受保护区域" in body["message"]
