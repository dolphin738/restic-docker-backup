-- 0003_multi_repos.sql —— R-023 多仓库/多主机视图（P3-C；架构 §13 E4/E6/E7 兑现）
-- repos 由「身份注册表」扩为「仓库事实表」：url/host_tag 及各自来源列（'ui'|'env'，
-- 镜像 config.source 语义——compose env 只覆盖 default 仓库）；jobs 增 repo_id 归属列
-- （历史行恒 'default'；E1 仓库维度互斥的数据基础）。
-- greenfield 延续（owner 2026-10-13 裁决）：不做存量物化分支——config repo.* 全局键的
-- 搬迁由启动期一次性迁移负责（services/repo_migration.py，marker 键幂等）。
-- 幂等：ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫（database.py migrate：仅应用
--      version > current 的脚本，重跑返回 0，与 test_migrations 幂等用例同口径）。

ALTER TABLE repos ADD COLUMN url TEXT;
ALTER TABLE repos ADD COLUMN host_tag TEXT;
ALTER TABLE repos ADD COLUMN url_source TEXT NOT NULL DEFAULT 'ui';
ALTER TABLE repos ADD COLUMN host_tag_source TEXT NOT NULL DEFAULT 'ui';

ALTER TABLE jobs ADD COLUMN repo_id TEXT NOT NULL DEFAULT 'default' REFERENCES repos(id);
CREATE INDEX IF NOT EXISTS idx_jobs_repo ON jobs(repo_id);

PRAGMA user_version = 3;
