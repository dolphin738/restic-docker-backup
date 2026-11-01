"""归档生命周期（删除保护方案 §5 三档）——每日维护里的归档清理回归。

口径（三档一一对应，**都不碰备份数据**）：
1. `retention.repo_archive_days`（缺省 30）到期 → 清凭据 + 密码文件，仓库行转墓碑；
2. `retention.repo_tombstone_days`（缺省 365）到期 → 删墓碑记录；
3. `retention.rclone_archive_days`（缺省 365）到期 → 删 rclone 配置归档。

反向断言（同等重要）：
- 归档期内的仓库**不得**被提前清理（30 天窗口就是「手滑后悔窗口」）；
- `<=0` = 不启用自动清理（永久保留）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import finish_bootstrap

from app.db.repositories.rclone_archive_repo import RcloneArchiveRepo

_SECRET = "s3cr3t-ak-value"

def _write_passfile(ns, repo_id: str, password: str = "test-password") -> None:
    """B1 严格语义：cred.<repo>.* 的密钥源自该仓库口令文件，测试须先造出来。"""
    path = ns.app.state.settings.passfile_path(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password, encoding="utf-8")



def _iso(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _archive_repo(ns, repo_id: str, *, days_ago: int = 0) -> None:
    """造一个已归档仓库（deleted_at 直接改写成指定天数前）。"""
    await ns.app.state.repos_repo.create(repo_id, f"仓库{repo_id}", url=f"/data/{repo_id}")
    _write_passfile(ns, repo_id, "archive-password")
    await ns.app.state.config_repo.set(f"cred.{repo_id}.ak", _SECRET)
    await ns.app.state.repos_repo.archive(repo_id)
    await ns.app.state.db.write(
        "UPDATE repos SET deleted_at = ? WHERE id = ?", (_iso(days_ago), repo_id)
    )


async def test_archive_expiry_clears_credentials_and_passfile_but_not_data(app_client, tmp_path):
    """归档期到期：清凭据 + 删密码文件 + 行转墓碑；**磁盘数据目录一动不动**。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = tmp_path / "repo-old"
    data_dir.mkdir()
    (data_dir / "config").write_text("{}", encoding="utf-8")
    await _archive_repo(ns, "old", days_ago=40)
    # 覆盖 url 指向真实目录，验证数据不被删
    await ns.app.state.db.write("UPDATE repos SET url = ? WHERE id = 'old'", (str(data_dir),))
    # 口令文件由 _archive_repo 建好（凭据已用它加密）；此处不得覆写，
    # 否则密钥一变 cred.old.ak 就解不开（B1 严格语义）
    passfile = ns.app.state.settings.passfile_path("old")
    assert passfile.exists()

    report = await ns.app.state.retention.cleanup_archives()
    assert report.repos_expired == ["old"]

    assert await ns.app.state.repos_repo.get("old") is None
    cred = await ns.app.state.config_repo.get("cred.old.ak")
    assert cred is None, "归档期到期必须清凭据（这是「重加即失忆」的根因）"
    assert not passfile.exists(), "归档期到期必须删密码文件"
    assert data_dir.exists() and (data_dir / "config").exists(), "数据永不自动删"
    tombs = {row["repo_id"]: row for row in await ns.app.state.repos_repo.list_tombstones()}
    assert "old" in tombs and tombs["old"]["data_destroyed"] == 0


async def test_archive_within_window_untouched(app_client):
    """归档期内的仓库不得被清理（30 天后悔窗口）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _archive_repo(ns, "fresh", days_ago=3)

    report = await ns.app.state.retention.cleanup_archives()
    assert report.repos_expired == []
    repo = await ns.app.state.repos_repo.get("fresh")
    assert repo is not None and repo.deleted_at is not None
    assert await ns.app.state.config_repo.get("cred.fresh.ak") is not None


async def test_archive_retention_zero_disables_cleanup(app_client):
    """`retention.repo_archive_days = 0` = 永久保留（不启用自动清理）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.config_repo.set("retention.repo_archive_days", "0")
    await _archive_repo(ns, "forever", days_ago=1000)

    report = await ns.app.state.retention.cleanup_archives()
    assert report.repos_expired == []
    assert await ns.app.state.repos_repo.get("forever") is not None


async def test_tombstone_and_rclone_archive_expiry(app_client):
    """墓碑与 rclone 配置归档按各自保留期清理。"""
    ns = app_client
    await finish_bootstrap(ns)
    # 旧墓碑
    await ns.app.state.repos_repo.insert_tombstone(
        repo_id="t-old",
        name="旧墓碑",
        repo_type="local",
        url_display="/data/t-old",
        archived_at=_iso(400),
        snapshot_count=3,
        data_destroyed=False,
    )
    # 新墓碑（不该被删）
    await ns.app.state.repos_repo.insert_tombstone(
        repo_id="t-new",
        name="新墓碑",
        repo_type="local",
        url_display="/data/t-new",
        archived_at=_iso(10),
        snapshot_count=1,
        data_destroyed=False,
    )
    archive_repo = RcloneArchiveRepo(ns.app.state.db)
    await archive_repo.save("oldremote", type_name="webdav", config={"type": "webdav"}, used_by_repos=0)
    await ns.app.state.db.write(
        "UPDATE rclone_config_archives SET deleted_at = ? WHERE remote = 'oldremote'", (_iso(400),)
    )
    await archive_repo.save("newremote", type_name="webdav", config={"type": "webdav"}, used_by_repos=0)

    report = await ns.app.state.retention.cleanup_archives()
    assert report.tombstones_deleted == ["t-old"]
    assert report.rclone_archives_expired == ["oldremote"]
    remaining = {row["repo_id"] for row in await ns.app.state.repos_repo.list_tombstones()}
    assert remaining == {"t-new"}
    assert [item.remote for item in await archive_repo.list_all()] == ["newremote"]
