-- 0002_multi_backup_tasks.sql —— 选项②「轻量多备份任务」（R-026；架构 §13 E6/E7）
-- 新增 repos（身份注册，R-023 预留）/ backup_tasks（任务即策略）/ jobs.task_id；
-- greenfield（owner 2026-10-13 裁决）：无存量数据，无物化/迁移分支；
-- 幂等：CREATE TABLE 全 IF NOT EXISTS；ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫
--      （database.py migrate：仅应用 version > current 的脚本，重跑返回 0，与
--       tests/test_migrations.py::test_migrate_is_idempotent_when_run_twice 同口径）。

CREATE TABLE IF NOT EXISTS repos (
    id         TEXT PRIMARY KEY,          -- P2 恒 'default'；R-023 起为仓库 id
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO repos (id, name, created_at)
VALUES ('default', '默认仓库', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

CREATE TABLE IF NOT EXISTS backup_tasks (
    id                  INTEGER PRIMARY KEY,
    repo_id             TEXT NOT NULL DEFAULT 'default' REFERENCES repos(id),  -- E7：第一天带外键
    name                TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 1,
    tag                 TEXT NOT NULL UNIQUE,     -- 自动生成只读：task-<id>（greenfield 无 legacy 继承）
    sources             TEXT NOT NULL,            -- JSON 数组，容器前缀形态（同 policies 口径）
    excludes            TEXT NOT NULL,            -- JSON 数组；内置排除集不入库（D8）
    keep_last           INTEGER NOT NULL DEFAULT 0,
    keep_hourly         INTEGER NOT NULL DEFAULT 0,
    keep_daily          INTEGER NOT NULL DEFAULT 0,
    keep_weekly         INTEGER NOT NULL DEFAULT 0,
    keep_monthly        INTEGER NOT NULL DEFAULT 0,
    keep_yearly         INTEGER NOT NULL DEFAULT 0,
    group_by            TEXT NOT NULL DEFAULT 'host,paths,tags',
    cron                TEXT,                     -- 空 → 回退全局缺省 schedule.cron_default
    one_file_system     INTEGER NOT NULL DEFAULT 0,
    compression         TEXT NOT NULL DEFAULT 'auto',
    check_sample_ratio  REAL NOT NULL DEFAULT 0.02,
    monthly_full_day    INTEGER NOT NULL DEFAULT 1,
    prune_interval_days INTEGER NOT NULL DEFAULT 7,
    limit_up_kibps      INTEGER NOT NULL DEFAULT 0,   -- KiB/s，0=不限（随 P2 生效）
    limit_down_kibps    INTEGER NOT NULL DEFAULT 0,
    dry_run_pending     INTEGER NOT NULL DEFAULT 0,   -- R-026-08：创建时服务端置 1（首跑/源变更须 dry-run 确认），E36 预览成功或首次成功备份清 0
    last_run_at         TEXT,                          -- 调度记账（先记账后执行）
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backup_tasks_repo   ON backup_tasks(repo_id);

ALTER TABLE jobs ADD COLUMN task_id INTEGER REFERENCES backup_tasks(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_task ON jobs(task_id);

PRAGMA user_version = 2;
