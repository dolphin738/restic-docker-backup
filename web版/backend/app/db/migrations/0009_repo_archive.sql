-- 0009_repo_archive.sql —— 删除保护：仓库归档 + 两张墓碑表
-- （《删除依赖与快照可用性保护方案》v1.2 §4 L4 / §5.1，裁决 D8/D10/D13）
--
-- 语义总纲：**记录会过期，数据永不自动删**。
--   - 归档只置 `repos.deleted_at`，不删行、不删密码文件、不清 cred.<id>.* 凭据；
--     列表默认过滤归档行，归档页可只读浏览并「一键原样恢复」。
--   - 到期（retention.repo_archive_days，默认 30 天）清**密码文件 + 凭据**，元数据转入
--     `repo_tombstones`（墓碑期，retention.repo_tombstone_days 默认 365 天）。
--   - 快照数据自始至终不在数据库里；清数据只能由用户显式动作触发（purge / 手工），
--     任何「归档」「墓碑过期」动作都不碰数据。
--
-- 各列口径：
--   repos.deleted_at        ISO 8601 UTC；NULL = 未归档（所有既有查询默认按 NULL 过滤）
--   repo_tombstones.snapshot_count  归档时快照数（**总条数**，含无 tag 手工快照；NULL = 当时不可知）
--   repo_tombstones.data_destroyed  1 = 走了 purge_disk（目录内容 + 密码文件已销毁，不可恢复）
--   repo_tombstones.released_at     手动释放 ID 的时刻（释放后新仓库可复用该 id）
--   rclone_config_archives.config_json  rclone conf 段**完整 key-value** JSON。
--     值为 rclone 混淆态而非明文，但**等价于凭据**：API 永不回值（只回 has_config +
--     键名列表）；日志不写；审计只记 remote/type/键数；本表**排除在配置导出之外**（S3）。
--   rclone_config_archives.used_by_repos  删除时刻被多少个仓库 url 引用（0 = 删时无引用）
--
-- 幂等：ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫（database.py migrate 仅应用
--      version > current 的脚本，重跑返回 0）；建表/索引一律 IF NOT EXISTS。

ALTER TABLE repos ADD COLUMN deleted_at TEXT;

CREATE INDEX IF NOT EXISTS idx_repos_deleted_at
    ON repos (deleted_at);

CREATE TABLE IF NOT EXISTS repo_tombstones (
    repo_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    type           TEXT NOT NULL,
    url_display    TEXT,
    archived_at    TEXT NOT NULL,
    snapshot_count INTEGER,
    data_destroyed INTEGER NOT NULL DEFAULT 0,
    released_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_repo_tombstones_archived_at
    ON repo_tombstones (archived_at);

CREATE TABLE IF NOT EXISTS rclone_config_archives (
    remote        TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    config_json   TEXT NOT NULL,
    deleted_at    TEXT NOT NULL,
    used_by_repos INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_rclone_config_archives_deleted_at
    ON rclone_config_archives (deleted_at);

PRAGMA user_version = 9;
