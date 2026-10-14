"""API 集成：E20 覆盖预览 + E19 原位恢复分支（R-015，红线 S7 v1.3）。

断言点（增量设计 §6-T08 验收要点 + §7 测试策略）：

- **双重锁矩阵**：完全越界 → 400 ``PATH_OUT_OF_PREFIX``；ro 档 / 非快照 paths →
  403 ``RESTORE_FORBIDDEN``；时间戳分支越界保持既有 403（C7 零回归）；
- **门禁 422 矩阵**：时间戳路径带 ``overwrite_mode``、原位路径 ``overwrite_mode``
  非 always、原位路径缺 ``keyword`` / ``keyword`` 错、``paths`` 与目标无交集；
- **执行层落点（S7 v1.3）**：restic 收到的 argv 中 ``--target`` 为 ``/``、
  ``--include`` 为原位目标子树（**反证**：``--target`` 绝不为 ``/host/etc``）；
- **审计**：``restore.preview``（只读留痕）与 ``restore.inplace``（危险清单）各落一条；
- **时间戳流 argv 零回归**：不带 ``overwrite_mode`` 的 E19 请求 argv 与 R-015 前一致。

restic 子进程用 ``run_json`` / ``spawn`` 替身覆盖（预录 ``snapshots --json`` /
``ls --json``，spawn 记录真实 argv 后阻断真机执行），路由、双重锁、门禁、审计、
JobRunner 参数拼装与 ``ResticCmd`` 白名单全走真实代码路径。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.constants import INPLACE_RESTORE_TARGET_ROOT, TERMINAL_JOB_STATUSES
from app.services import compare as compare_module
from app.services.restic import ProbeResult
from app.utils.time import parse_iso

SNAPSHOT_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
KEYWORD = SNAPSHOT_ID[:8]
ROOT = "/host/etc"
SNAPSHOT_MTIME = "2026-01-03T12:00:00Z"
# 磁盘侧现状：与快照不同 → 记为待覆盖（mtime 一致、size 差 1）
DISK_MTIME = "2026-02-04T08:30:00Z"


def _epoch(value: str) -> float:
    """ISO → epoch（走 utils/time，C8 时间语义唯一实现点）。"""
    moment = parse_iso(value)
    assert moment is not None
    return moment.timestamp()


class _FakeOs:
    """``os.stat`` 替身（C4 比对发生在 to_thread 里，替身只替换 stat 本身）。

    默认**所有路径都存在**且 mtime 与快照不同（→ 记为待覆盖）；``table`` 可逐路径
    覆盖，``default=None`` 表示「磁盘上不存在」。
    """

    def __init__(self) -> None:
        self.table: dict[str, tuple[float, int]] = {}
        self.default: tuple[float, int] | None = (_epoch(DISK_MTIME), 512)

    def stat(self, path: str) -> SimpleNamespace:
        """返回 stat 形状对象；未登记且无缺省即视为不存在（OSError）。"""
        mtime_size = self.table.get(path, self.default)
        if mtime_size is None:
            raise OSError(2, "No such file or directory", path)
        mtime, size = mtime_size
        return SimpleNamespace(st_mtime=mtime, st_size=size)


@pytest.fixture()
def fake_disk(monkeypatch):
    """把 compare 模块的 ``os`` 换成替身，返回可改写的磁盘现状句柄。"""
    fake = _FakeOs()
    monkeypatch.setattr(compare_module, "os", fake)
    return fake


def _snapshots_line(paths: list[str]) -> str:
    """构造 ``snapshots --json`` 输出（整段数组形态，与 restic 主流输出一致）。"""
    return json.dumps(
        [
            {
                "id": SNAPSHOT_ID,
                "short_id": KEYWORD,
                "time": "2026-01-03T12:00:00.000000000Z",
                "tree": "0" * 64,
                "paths": paths,
                "hostname": "nas",
                "tags": ["task-1"],
            }
        ]
    )


def _ls_lines(names: list[str], *, size: int, mtime: str) -> list[str]:
    """构造 ``ls --json`` NDJSON 输出（文件条目，路径为容器形态）。"""
    return [
        json.dumps(
            {
                "name": name,
                "type": "file",
                "path": f"{ROOT}/{name}",
                "size": size + index,
                "mtime": mtime,
                "struct_type": "node",
            }
        )
        for index, name in enumerate(names)
    ]


def _patch_run_json(ns, *, snapshot_paths: list[str], names: list[str], size: int = 512,
                    mtime: str = SNAPSHOT_MTIME) -> None:
    """替身 run_json：按 verb 回放 snapshots / ls 输出。"""

    async def _run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        verb = "snapshots" if "snapshots" in argv else "ls"
        lines = [_snapshots_line(snapshot_paths)] if verb == "snapshots" else _ls_lines(names, size=size, mtime=mtime)
        return ProbeResult(exit_code=0, lines=lines, truncated=False)

    ns.app.state.restic.run_json = _run_json


def _patch_submit(ns) -> list[dict]:
    """捕获 runner.submit 的 kwargs（params 透传断言）；返回调用收集器。"""
    calls: list[dict] = []
    original = ns.app.state.job_runner.submit

    async def _submit(job_type, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"job_type": job_type, **kwargs})
        return await original(job_type, **kwargs)

    ns.app.state.job_runner.submit = _submit
    return calls


def _patch_spawn(ns) -> list[list[str]]:
    """替身 ``ResticCmd.spawn``：记录**白名单拼装后的真实 argv** 并阻断真机执行。

    记录点在 spawn 边界（而非 build），即 restic 真正收到的参数；抛 OSError 让任务
    走到 failed 终态，避免测试依赖 restic 二进制是否存在。
    """
    argv_log: list[list[str]] = []

    async def _spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        raise OSError("spawn blocked in test")

    ns.app.state.restic.spawn = _spawn
    return argv_log


def _flag_value(argv: list[str], flag: str) -> str | None:
    """取 argv 中该 flag 的取值（缺失返回 None）。"""
    if flag not in argv:
        return None
    return argv[argv.index(flag) + 1]


def _flag_values(argv: list[str], flag: str) -> list[str]:
    """取 argv 中该 flag 的**全部**取值（如多条 --include）。"""
    return [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == flag]


async def _wait_terminal(ns, job_id: str) -> None:
    """等任务进入终态（spawn 被阻断 → 快速 failed），确保 argv 已被记录。"""
    for _ in range(200):
        job = await ns.app.state.job_repo.get(job_id)
        if job is not None and job.status in TERMINAL_JOB_STATUSES:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"任务 {job_id} 未在预期时间内进入终态")


async def _setup(ns, *, snapshot_paths: list[str] | None = None, names: list[str] | None = None) -> None:
    """登录 + 配置 repo.url + 装好 restic 替身。"""
    await finish_bootstrap(ns)
    await ns.app.state.config_repo.set("repo.url", "/repo")
    _patch_run_json(ns, snapshot_paths=snapshot_paths or [ROOT], names=names or ["nginx.conf"])


async def _audit(ns, action: str) -> tuple[list, int]:
    """按动作名查审计（S6：只读）。"""
    return await ns.app.state.audit.query(action=action)


# ---------------------------------------------------------------- E20 双重锁

async def test_e20_preview_returns_overwrite_items_and_audit(app_client, fake_disk):
    """预览返回覆盖清单（含 display_path）+ 总量，并写 restore.preview 留痕。"""
    ns = app_client
    await _setup(ns, names=["nginx.conf", "hosts"])

    resp = await ns.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overwrite_count"] == 2
    assert body["truncated"] is False
    assert body["total_bytes"] == 512 + 513
    item = body["items"][0]
    assert item["path"] == f"{ROOT}/nginx.conf"
    assert item["display_path"] == "/etc/nginx.conf"  # S1/A2：容器路径不出 API 展示位
    assert item["snapshot_mtime"] == SNAPSHOT_MTIME
    assert item["disk_mtime"] == DISK_MTIME
    assert item["snapshot_size"] == 512
    assert item["disk_size"] == 512  # 磁盘侧现状由 os.stat 取（不存在 → 不计）

    rows, total = await _audit(ns, "restore.preview")
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail == {"snapshot_id": SNAPSHOT_ID, "target": ROOT, "overwrite_count": 2}


async def test_e20_preview_cursor_pagination(app_client, fake_disk):
    """游标分页：limit=1 逐页取，末页 next_cursor 为 null。"""
    ns = app_client
    await _setup(ns, names=["a.conf", "b.conf", "c.conf"])

    first = await ns.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT, "limit": 1},
        headers=ORIGIN,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["overwrite_count"] == 3  # 计数是**全量**，不是本页条数
    assert len(body["items"]) == 1
    assert body["next_cursor"]

    second = await ns.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT, "limit": 2, "cursor": body["next_cursor"]},
        headers=ORIGIN,
    )
    assert second.status_code == 200, second.text
    assert [item["path"] for item in second.json()["items"]] == [f"{ROOT}/b.conf", f"{ROOT}/c.conf"]
    assert second.json()["next_cursor"] is None  # 末页


@pytest.mark.parametrize("target", ["/sources/etc", "/etc/passwd"])
async def test_e20_target_out_of_prefix_400(app_client, target):
    """完全越界（既不在 /restore 也不在 rw 档 host_root）→ 400 PATH_OUT_OF_PREFIX。"""
    ns = app_client
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": target},
        headers=ORIGIN,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "PATH_OUT_OF_PREFIX"


async def test_e20_target_not_in_snapshot_paths_403(app_client):
    """快照 paths 交集收紧：目标不在该快照备份路径内 → 403。"""
    ns = app_client
    await _setup(ns, snapshot_paths=["/host/srv"])

    resp = await ns.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT},
        headers=ORIGIN,
    )
    assert resp.status_code == 403
    assert resp.json()["data"]["snapshot_paths"] == ["/host/srv"]


async def test_e20_ro_mount_403(make_app):
    """ro 档：/host 只读挂载 → 403（data 带 host_mount_mode，前端据此置灰）。"""
    ns = await make_app({"HOST_MOUNT_MODE": "ro"})
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT},
        headers=ORIGIN,
    )
    assert resp.status_code == 403
    assert resp.json()["data"]["host_mount_mode"] == "ro"


async def test_e20_requires_session(app_client):
    """未登录 → 401（S4）。"""
    resp = await app_client.client.post(
        "/api/restore/preview",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT},
        headers=ORIGIN,
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------- E19 门禁 422 矩阵

async def test_e19_timestamp_path_with_overwrite_mode_422(app_client):
    """时间戳子目录路径带 overwrite_mode → 422（该路径零覆盖语义，界面无入口）。"""
    ns = app_client
    await _setup(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": "/restore", "overwrite_mode": "always"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert calls == []  # 未提交任何任务


async def test_e19_inplace_overwrite_mode_if_changed_422(app_client):
    """原位路径的 overwrite_mode 非 always → 422（S7 要件③：固定 always）。"""
    ns = app_client
    await _setup(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={
            "snapshot_id": SNAPSHOT_ID,
            "target": ROOT,
            "overwrite_mode": "if-changed",
            "keyword": KEYWORD,
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert calls == []


@pytest.mark.parametrize("keyword", [None, "deadbeef"])
async def test_e19_inplace_keyword_gate_422(app_client, keyword):
    """三重确认门后端校验：keyword 缺失或与快照短 id 不符 → 422。"""
    ns = app_client
    await _setup(ns)
    calls = _patch_submit(ns)

    payload = {"snapshot_id": SNAPSHOT_ID, "target": ROOT, "overwrite_mode": "always"}
    if keyword is not None:
        payload["keyword"] = keyword
    resp = await ns.client.post("/api/restore", json=payload, headers=ORIGIN)
    assert resp.status_code == 422
    assert calls == []


async def test_e19_inplace_out_of_snapshot_paths_403(app_client):
    """原位路径但不在快照 paths 内 → 403（门禁判在 422 之前）。"""
    ns = app_client
    await _setup(ns, snapshot_paths=["/host/srv"])
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT, "overwrite_mode": "always", "keyword": KEYWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 403
    assert calls == []


async def test_e19_inplace_ro_mount_403_no_spawn(make_app):
    """ro 档护栏（E19 执行侧）：/host 只读挂载 → 403，且 restic 未被 spawn。"""
    ns = await make_app({"HOST_MOUNT_MODE": "ro"})
    await _setup(ns)
    argv_log = _patch_spawn(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT, "overwrite_mode": "always", "keyword": KEYWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 403
    assert resp.json()["data"]["host_mount_mode"] == "ro"
    assert calls == []
    assert argv_log == []  # 未 spawn


# ---------------------------------------------------------------- E19 原位执行

async def test_e19_inplace_submits_always_and_audits(app_client, fake_disk):
    """原位恢复：落点 / + include 限定 + overwrite=always + restore.inplace 审计。"""
    ns = app_client
    await _setup(ns, names=["nginx.conf", "hosts"])
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT, "overwrite_mode": "always", "keyword": KEYWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["restore_target"] == ROOT  # 响应仍回用户语义的原位目录

    assert len(calls) == 1
    params = calls[0]["params"]
    assert params["target"] == INPLACE_RESTORE_TARGET_ROOT  # 执行层落点 = /
    assert params["inplace"] is True
    assert params["overwrite"] == "always"  # S7 要件③
    assert params["include"] == [ROOT]  # 强制非空：--target / 只靠 include 限定范围

    rows, total = await _audit(ns, "restore.inplace")
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["snapshot_id"] == SNAPSHOT_ID
    assert detail["target"] == ROOT  # 审计语义 = 用户语义的原位目录
    assert detail["keyword_confirmed"] is True
    assert detail["overwrite_count"] == 2  # 预览值（执行前重算，同源不可伪造）


async def test_e19_inplace_argv_target_is_root_with_include(app_client, fake_disk):
    """行为级：restic 收到的 argv --target 为 /、--include 为原位目标子树。

    restic 落点 = ``--target`` + 快照内绝对路径：``/`` + ``/host/etc/...`` →
    真正覆盖 ``/host/etc/...``；传 ``/host/etc`` 会嵌套成 ``/host/etc/host/etc/...``。
    """
    ns = app_client
    await _setup(ns)
    argv_log = _patch_spawn(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": ROOT, "overwrite_mode": "always", "keyword": KEYWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 202, resp.text
    await _wait_terminal(ns, resp.json()["job_id"])

    argv = argv_log[0]
    assert _flag_value(argv, "--target") == INPLACE_RESTORE_TARGET_ROOT
    assert _flag_values(argv, "--include") == [ROOT]
    assert "--overwrite=always" in argv
    # 反证（防回归）：--target 绝不能是用户目录——那正是"一个文件都覆盖不到"的缺陷形状
    assert _flag_value(argv, "--target") != ROOT


async def test_e19_inplace_argv_include_narrows_to_paths(app_client, fake_disk):
    """给了 paths 时 include 取**与目标相交**的子树（仍非空），落点恒为 /。"""
    ns = app_client
    await _setup(ns)
    argv_log = _patch_spawn(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={
            "snapshot_id": SNAPSHOT_ID,
            "target": ROOT,
            "overwrite_mode": "always",
            "keyword": KEYWORD,
            "paths": ["/etc/nginx"],
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 202, resp.text
    await _wait_terminal(ns, resp.json()["job_id"])

    argv = argv_log[0]
    assert _flag_value(argv, "--target") == INPLACE_RESTORE_TARGET_ROOT
    assert _flag_values(argv, "--include") == [f"{ROOT}/nginx"]


@pytest.mark.parametrize("paths", [["/srv"], ["/srv", "/home"]])
async def test_e19_inplace_paths_without_intersection_422_and_no_spawn(app_client, paths):
    """paths 与原位目标无交集 → 422，且 restic 未被 spawn（include 不允许为空）。"""
    ns = app_client
    await _setup(ns)
    argv_log = _patch_spawn(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={
            "snapshot_id": SNAPSHOT_ID,
            "target": ROOT,
            "overwrite_mode": "always",
            "keyword": KEYWORD,
            "paths": paths,
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert calls == []
    assert argv_log == []  # 未 spawn


# ---------------------------------------------------------------- 时间戳路径零覆盖回归

async def test_e19_timestamp_path_unchanged_without_overwrite_mode(app_client):
    """零覆盖回归：不带覆盖参数 → 时间戳子目录 + params 无 overwrite + restore.run 审计。"""
    ns = app_client
    await _setup(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore", json={"snapshot_id": SNAPSHOT_ID, "target": "/restore"}, headers=ORIGIN
    )
    assert resp.status_code == 202, resp.text
    target = resp.json()["restore_target"]
    assert target.startswith("/restore/etc-")  # 服务端计算的时间戳子目录
    assert "overwrite" not in calls[0]["params"]  # 由 JobRunner 固定注入，路由层零透传

    rows, total = await _audit(ns, "restore.run")
    assert total == 1
    assert json.loads(rows[0].detail or "{}")["include_count"] == 0


async def test_e19_timestamp_argv_unchanged(app_client):
    """时间戳流 argv 零回归：--target 为服务端时间戳目录，if-changed 固定注入，无 include。"""
    ns = app_client
    await _setup(ns)
    argv_log = _patch_spawn(ns)

    resp = await ns.client.post(
        "/api/restore", json={"snapshot_id": SNAPSHOT_ID, "target": "/restore"}, headers=ORIGIN
    )
    assert resp.status_code == 202, resp.text
    restore_target = resp.json()["restore_target"]
    await _wait_terminal(ns, resp.json()["job_id"])

    argv = argv_log[0]
    assert _flag_value(argv, "--target") == restore_target  # 与响应逐字节一致
    assert _flag_value(argv, "--target") != INPLACE_RESTORE_TARGET_ROOT  # 未被原位落点污染
    assert "--overwrite=if-changed" in argv  # JobRunner 固定注入
    assert "--include" not in argv


async def test_e19_inplace_prefix_without_overwrite_mode_still_403(app_client):
    """回归护栏：target=/host 但不带覆盖参数 → 仍走时间戳分支 → 403（行为不变）。"""
    ns = app_client
    await _setup(ns)

    resp = await ns.client.post(
        "/api/restore", json={"snapshot_id": SNAPSHOT_ID, "target": "/host"}, headers=ORIGIN
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "RESTORE_FORBIDDEN"
