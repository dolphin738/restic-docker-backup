"""API 集成：E05/E31 展示路径契约（语义修订）+ E31 白名单收紧。

Windows 环境无 /host 挂载：文件系统扫描以 monkeypatch ``_scan_dir`` 替身覆盖
（架构 §10.1 认可的"restic 子进程 mock 为预录脚本输出"同款手段），路由/映射/白名单
契约全部走真实代码路径。
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import ORIGIN, finish_bootstrap


def _fake_scan_dir(entries: list[tuple[str, str]]):
    def _scan(path: str) -> list[tuple[str, str]]:  # 路由经 asyncio.to_thread 调用，须为同步函数
        return entries

    return _scan


class _FakeDryRunProcess:
    """ResticProcess 替身：按预录 JSON 行产出 dry-run 输出并 tee 到日志文件。"""

    def __init__(self, lines: list[str], log_path: Path, exit_code: int = 0) -> None:
        self._lines = lines
        self._log_path = log_path
        self._exit_code = exit_code

    async def lines(self):  # noqa: ANN201
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("ab") as sink:
            for line in self._lines:
                sink.write((line + "\n").encode("utf-8"))
                sink.flush()
                yield line

    async def wait(self) -> int:
        return self._exit_code

    def terminate(self) -> None:  # pragma: no cover - 本测试不触达
        pass

    def kill(self) -> None:  # pragma: no cover
        pass


def _patch_restic_spawn(ns, lines: list[str], exit_code: int = 0) -> list[list[str]]:
    """替身 spawn：记录 argv 供白名单断言。返回 argv 收集器。"""
    argv_log: list[list[str]] = []

    async def _spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return _FakeDryRunProcess(lines, Path(log_path), exit_code)

    ns.app.state.restic.spawn = _spawn  # 实例级替身（app.state 可变）
    return argv_log


# ---------------------------------------------------------------- E05 展示路径

async def test_e05_display_path_200(app_client, monkeypatch):
    """新契约：E05 收展示路径（/etc）→ 200，path 与 items[].display_path 均为展示形态。"""
    import app.routers.sources as sources_module

    await finish_bootstrap(app_client)
    monkeypatch.setattr(
        sources_module,
        "_scan_dir",
        _fake_scan_dir([("nginx", "dir"), ("hosts", "file")]),
    )

    resp = await app_client.client.get("/api/sources", params={"path": "/etc"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "/etc"
    assert [(i["name"], i["type"]) for i in body["items"]] == [("nginx", "dir"), ("hosts", "file")]
    assert [i["display_path"] for i in body["items"]] == ["/etc/nginx", "/etc/hosts"]


async def test_e05_container_path_still_compat(app_client, monkeypatch):
    """兼容旧文档示例的容器路径形态（/host/etc）→ 同样 200。"""
    import app.routers.sources as sources_module

    await finish_bootstrap(app_client)
    monkeypatch.setattr(sources_module, "_scan_dir", _fake_scan_dir([("nginx", "dir")]))

    resp = await app_client.client.get("/api/sources", params={"path": "/host/etc"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "/etc"
    assert body["items"][0]["display_path"] == "/etc/nginx"


async def test_e05_default_path_is_mode_root(app_client, monkeypatch):
    await finish_bootstrap(app_client)
    import app.routers.sources as sources_module

    monkeypatch.setattr(sources_module, "_scan_dir", _fake_scan_dir([]))
    resp = await app_client.client.get("/api/sources")
    assert resp.status_code == 200
    assert resp.json()["path"] == "/"


async def test_e05_traversal_rejected(app_client):
    """NUL 注入 → 400 PATH_OUT_OF_PREFIX（防穿越语义不变）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/sources", params={"path": "/etc\x00x"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "PATH_OUT_OF_PREFIX"


# ---------------------------------------------------------------- E31 dry-run（展示路径 + 白名单收紧）

DRY_RUN_LINES = [
    '{"message_type":"status","percent_done":0.0,"total_files":2,"files_done":0,"total_bytes":512,"bytes_done":0}',
    '{"message_type":"verbose_status","action":"modified","item":"/host/etc/nginx/nginx.conf","duration":0,"data_size":512,"size":512,"metadata_time":0,"metadata_size":100}',
    '{"message_type":"verbose_status","action":"modified","item":"/host/etc/proc/kcore","duration":0,"data_size":0,"size":0,"metadata_time":0,"metadata_size":0}',
    '{"message_type":"summary","total_files_processed":2,"total_bytes_processed":512}',
]


async def test_e31_dry_run_display_path(app_client):
    """新契约：E31 收展示路径（/etc）→ 200；items[].display_path 为展示形态；
    命中伪文件系统目录名出黄色警告；restic 参数全部落在 /host 白名单前缀内。"""
    ns = app_client
    await finish_bootstrap(ns)

    argv_log = _patch_restic_spawn(ns, DRY_RUN_LINES, exit_code=0)
    resp = await ns.client.post("/api/sources/dry-run", json={"sources": ["/etc"]}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] is None
    assert body["truncated"] is False

    assert [(i["path"], i["display_path"], i["size"]) for i in body["items"]] == [
        ("/host/etc/nginx/nginx.conf", "/etc/nginx/nginx.conf", 512),
        ("/host/etc/proc/kcore", "/etc/proc/kcore", 0),
    ]
    assert any(w["type"] == "pseudo_fs_name" and w["path"] == "/host/etc/proc/kcore" for w in body["warnings"])

    # 白名单收紧：展示路径 /etc 被还原为容器路径后，targets 恰为 /host/etc（落在白名单内）
    assert argv_log, "spawn 未被调用"
    assert "/host/etc" in argv_log[0]
    assert all(t.startswith("/host/") or t.startswith("/sources/") for t in argv_log[0][-1:])


async def test_e31_container_path_compat(app_client):
    """旧容器路径形态（/host/etc）→ dry-run 同样受理。"""
    ns = app_client
    await finish_bootstrap(ns)
    _patch_restic_spawn(ns, DRY_RUN_LINES, exit_code=0)

    resp = await ns.client.post("/api/sources/dry-run", json={"sources": ["/host/etc"]}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"][0]["display_path"] == "/etc/nginx/nginx.conf"


async def test_e31_empty_source_rejected(app_client):
    """空串备份源 → 400（to_snapshot 规范化拒绝空路径）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.post("/api/sources/dry-run", json={"sources": [""]}, headers=ORIGIN)
    assert resp.status_code == 400
    assert resp.json()["code"] == "PATH_OUT_OF_PREFIX"


async def test_e31_restic_failure_reports_error(app_client):
    """restic 非 0 退出 → 200 但 error 字段明示（不静默、不 5xx）。"""
    ns = app_client
    await finish_bootstrap(ns)
    _patch_restic_spawn(ns, ["boom"], exit_code=1)

    resp = await ns.client.post("/api/sources/dry-run", json={"sources": ["/etc"]}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] is not None
    assert "1" in body["error"]  # 退出码透传
    assert body["items"] == []


def _json_line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
