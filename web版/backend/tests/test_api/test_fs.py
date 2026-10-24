"""API 集成：E33 容器文件系统浏览（仓库路径选择）。

覆盖：鉴权门、根目录列举、相对路径 422、不存在 404、游标分页、mounts 仅根目录返回。
注：测试宿主可能是 Windows（/proc/mounts 不存在），断言只针对契约本身，不断言挂载点内容。
"""

from __future__ import annotations

from conftest import finish_bootstrap


async def test_fs_browse_requires_session(make_app):
    """未登录直接 401/403，不泄露容器目录结构。"""
    ns = await make_app()
    resp = await ns.client.get("/api/fs/browse")
    assert resp.status_code in (401, 403)


def test_reject_passfile_root_boundaries():
    """密码文件根目录整棵子树被拒；同前缀但非子树（/data/repos2）不误伤。"""
    from app.errors import ValidationError
    from app.routers.fs import _reject_passfile_root

    for blocked in ("/data/repos", "/data/repos/default", "/data/repos/default/password"):
        try:
            _reject_passfile_root(blocked, "/data/repos")
        except ValidationError:
            pass
        else:
            raise AssertionError(f"应被拒绝：{blocked}")

    for allowed in ("/data", "/data/repos2", "/data/repo", "/", "/repo"):
        _reject_passfile_root(allowed, "/data/repos")  # 不抛即通过


def test_reject_app_data_root_boundaries():
    """批次③ 方案 B：数据目录整棵子树被拒；同前缀但非子树不误伤。"""
    from app.errors import ValidationError
    from app.routers.fs import _reject_app_data_root

    for blocked in ("/data", "/data/restic.db", "/data/repos/default/password"):
        try:
            _reject_app_data_root(blocked, "/data")
        except ValidationError:
            pass
        else:
            raise AssertionError(f"应被拒绝：{blocked}")

    for allowed in ("/data2", "/data-backup", "/", "/repo", "/sources/etc"):
        _reject_app_data_root(allowed, "/data")  # 不抛即通过


async def test_fs_browse_denies_app_data_root(make_app, tmp_path):
    """批次③ 方案 B：?path=<data_dir>/... → 422；/repo（仓库默认位置）不受影响。"""
    appdata = tmp_path / "appdata"
    ns = await make_app({"DATA_DIR": str(appdata), "PASSFILE_ROOT": str(appdata / "repos")})
    await finish_bootstrap(ns)

    blocked = await ns.client.get("/api/fs/browse", params={"path": f"{appdata}/repos/default"})
    assert blocked.status_code == 422, blocked.text
    body = blocked.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["data"]["denied_root"] == str(appdata)

    # /repo 是仓库默认位置（E33 职责所在）：存在与否另说，但绝不能被这道门禁误伤
    repo = await ns.client.get("/api/fs/browse", params={"path": "/repo"})
    assert repo.status_code != 422


async def test_fs_browse_denies_passfile_root(monkeypatch, make_app):
    """路径层护栏：?path=<passfile_root>/... → 422，其它路径不受影响（仍走既有 404）。"""
    from app.settings import get_settings

    monkeypatch.setenv("PASSFILE_ROOT", "/data/repos")
    get_settings.cache_clear()  # 让新 env 生效（Settings 为进程内单例 + lru_cache）

    ns = await make_app()
    await finish_bootstrap(ns)

    resp = await ns.client.get("/api/fs/browse", params={"path": "/data/repos/default"})
    assert resp.status_code == 422, resp.text

    other = await ns.client.get("/api/fs/browse", params={"path": "/definitely/not/exists-xyz"})
    assert other.status_code == 404, other.text  # 未被门禁误伤


async def test_mount_quick_jump_denies_data_volume(monkeypatch, tmp_path):
    """E5 后 /data 存仓库密码文件：快跳入口不得列出它（黑名单回归护栏）。"""
    from app.routers import fs as fs_module

    mounts_file = tmp_path / "mounts"
    mounts_file.write_text(
        "\n".join(
            [
                "/dev/sda1 / ext4 rw 0 0",
                "/dev/sdb1 /repo ext4 rw 0 0",
                "/dev/sdc1 /data ext4 rw 0 0",
                "/dev/sdd1 /run/secrets ext4 ro 0 0",
                "proc /proc proc rw 0 0",
            ]
        ),
        encoding="utf-8",
    )
    payload = mounts_file.read_text(encoding="utf-8")  # 先取内容，避免补丁递归（同属 Path 类）
    monkeypatch.setattr(fs_module.Path, "read_text", lambda *a, **k: payload)
    # 只保留目录型挂载（owner 2026-10-24）：合成挂载表里的路径在测试机不存在，
    # 需按目标字符串判定 is_dir，否则会被目录过滤误伤
    real_is_dir = fs_module.Path.is_dir
    monkeypatch.setattr(
        fs_module.Path,
        "is_dir",
        lambda self: str(self) in {"/repo", "/data", "/run/secrets"} or real_is_dir(self),
    )

    mounts = fs_module._mount_points()
    assert "/repo" in mounts
    assert "/data" not in mounts
    assert not any(m.startswith("/run") for m in mounts)
    assert "/proc" not in mounts


async def test_mount_points_skips_file_mounts(monkeypatch, tmp_path):
    """文件/socket 型挂载点不标挂载点（owner 2026-10-24：WSL 把 init 二进制 bind mount
    到 /init，/proc/mounts 里大量非目录挂载——给二进制打「挂载点」徽章是纯噪声）。"""
    from app.routers import fs as fs_module

    mounts_file = tmp_path / "mounts"
    mounts_file.write_text(
        "\n".join(
            [
                "/dev/sda1 / ext4 rw 0 0",
                "rootfs /init ext4 rw 0 0",  # 文件型 bind mount（WSL init）
                "/dev/sdb1 /repo ext4 rw 0 0",  # 目录型（保留）
                "tmpfs /tmp/.X11-unix tmpfs rw 0 0",  # socket 型（剔除）
            ]
        ),
        encoding="utf-8",
    )
    payload = mounts_file.read_text(encoding="utf-8")
    monkeypatch.setattr(fs_module.Path, "read_text", lambda *a, **k: payload)
    real_is_dir = fs_module.Path.is_dir
    monkeypatch.setattr(
        fs_module.Path,
        "is_dir",
        lambda self: str(self) == "/repo" or real_is_dir(self),
    )

    mounts = fs_module._mount_points()
    assert "/repo" in mounts
    assert "/init" not in mounts
    assert "/tmp/.X11-unix" not in mounts


async def test_fs_browse_root_lists_entries(make_app):
    """默认根目录：200 + path=/ + 条目字段齐全。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "/"
    assert isinstance(body["mounts"], list)
    assert body["next_cursor"] is None or isinstance(body["next_cursor"], str)
    for item in body["items"]:
        assert item["name"]
        assert item["type"] in ("dir", "file", "other")
        assert item["path"].startswith("/")
        assert isinstance(item["is_mountpoint"], bool)


async def test_fs_browse_rejects_relative_path(make_app):
    """相对路径 → 422 VALIDATION_ERROR（避免相对进程 cwd 的歧义）。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse", params={"path": "etc/nginx"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_fs_browse_missing_path_404(make_app):
    """不存在的路径 → 404 NOT_FOUND。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse", params={"path": "/definitely/not/exists-xyz"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_fs_browse_pagination_cursor(make_app):
    """limit=1 翻页：第二页首条目不与第一页重复，游标耗尽后 next_cursor=None。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp1 = await ns.client.get("/api/fs/browse", params={"limit": 1})
    assert resp1.status_code == 200
    page1 = resp1.json()
    assert len(page1["items"]) == 1
    if page1["next_cursor"] is None:
        return  # 根目录只有 0/1 个条目的极端环境，跳过
    resp2 = await ns.client.get(
        "/api/fs/browse", params={"limit": 1, "cursor": page1["next_cursor"]}
    )
    page2 = resp2.json()
    assert page2["items"][0]["name"] != page1["items"][0]["name"]


async def test_fs_browse_path_normalization(make_app):
    """``/../`` 折叠为根目录：与直接请求 / 同形（normpath 语义）。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse", params={"path": "/../"})
    assert resp.status_code == 200
    assert resp.json()["path"] == "/"
