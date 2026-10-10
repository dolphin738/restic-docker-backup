-- 0001_init.sql —— P1 全量建表（架构设计文档 §4）
-- 六张表：admin_account / sessions / config / policies / jobs / audit_logs
-- 幂等约定：全部使用 IF NOT EXISTS，可重复执行无副作用（迁移器每文件一个事务）。

-- 4.1 admin_account（单行表：鉴权 + 首启引导状态）
CREATE TABLE IF NOT EXISTS admin_account (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),  -- 恒单行
    password_hash            TEXT NOT NULL,    -- argon2（argon2-cffi 默认 PasswordHasher）；bcrypt 降级时以 "$2b$" 前缀区分
    must_change              INTEGER NOT NULL DEFAULT 1,          -- 1=首启未改密；Bootstrap 门（§5.1）依据
    bootstrap_shown          INTEGER NOT NULL DEFAULT 0,          -- 随机密码是否已展示（仅一次；1 后再取 → 410 BOOTSTRAP_SHOWN）
    bootstrap_password_hash  TEXT,                                 -- 首启随机密码哈希；改密完成后置 NULL
    failed_attempts          INTEGER NOT NULL DEFAULT 0,          -- 登录失败计数（存 DB 防容器重启清零，§8）
    lock_level               INTEGER NOT NULL DEFAULT 0,          -- 指数退避级数：锁定时长 = 5min × 2^lock_level，上限 1h
    locked_until             TEXT,                                 -- 解锁时刻（ISO 8601 UTC）
    updated_at               TEXT NOT NULL
);

-- 4.2 sessions（服务端会话，24h 滑动续期）
CREATE TABLE IF NOT EXISTS sessions (
    token_hash    TEXT PRIMARY KEY,   -- SHA-256(会话 token)；明文 token 只存在于 cookie，不落库不落日志
    last_seen_at  TEXT NOT NULL,      -- 每次认证请求刷新（滑动续期）
    expires_at    TEXT NOT NULL,      -- = last_seen_at + 24h；过期行由启动期惰性清理（§8）
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

-- 4.3 config（KV 配置 + 全局键表）
CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT,                            -- 敏感项（cred.*）明文落盘程度与 compose env 等价（§6.1）
    source      TEXT NOT NULL DEFAULT 'ui',      -- 'ui' | 'env'：E07 对 source='env' 的键返回 409 ENV_OVERRIDDEN
    updated_at  TEXT NOT NULL
);

-- 4.4 policies（多策略；P1 建表即含全字段，UI 分期暴露）
CREATE TABLE IF NOT EXISTS policies (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    is_default          INTEGER NOT NULL DEFAULT 0,   -- 恒一条；DELETE 默认策略 → 409 DEFAULT_POLICY_IMMUTABLE
    enabled             INTEGER NOT NULL DEFAULT 1,
    tag                 TEXT,
    sources             TEXT NOT NULL,                -- JSON 数组；以 /host 前缀存储，PathMapper 管理展示映射
    excludes            TEXT NOT NULL,                -- JSON 数组（用户追加）；内置排除集为代码常量不入库（D8）
    keep_last           INTEGER NOT NULL DEFAULT 0,
    keep_hourly         INTEGER NOT NULL DEFAULT 0,
    keep_daily          INTEGER NOT NULL DEFAULT 0,
    keep_weekly         INTEGER NOT NULL DEFAULT 0,
    keep_monthly        INTEGER NOT NULL DEFAULT 0,
    keep_yearly         INTEGER NOT NULL DEFAULT 0,
    group_by            TEXT NOT NULL DEFAULT 'host,paths,tags',
    cron                TEXT,                          -- 空则回退全局缺省 schedule.cron_default
    one_file_system     INTEGER NOT NULL DEFAULT 0,   -- sources 模式缺省 1（方案 §3.2）
    compression         TEXT NOT NULL DEFAULT 'auto', -- auto/off/max
    check_sample_ratio  REAL NOT NULL DEFAULT 0.02,
    monthly_full_day    INTEGER NOT NULL DEFAULT 1,
    prune_interval_days INTEGER NOT NULL DEFAULT 7,
    limit_up_kibps      INTEGER NOT NULL DEFAULT 0,   -- 限速 KiB/s，0=不限（Q5 裁决 P2 起生效）
    limit_down_kibps    INTEGER NOT NULL DEFAULT 0,
    last_run_at         TEXT,                          -- 调度记账：先记账后执行，防崩溃重复触发（§6.4）
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policies_default ON policies(is_default);

-- 4.5 jobs（任务元数据）
CREATE TABLE IF NOT EXISTS jobs (
    id             TEXT PRIMARY KEY,     -- ULID（python-ulid，可排序）
    type           TEXT NOT NULL,        -- backup/restore/forget/check/unlock/init/dump（E12 白名单）
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending/running/success/failed/interrupted/cancelled
    trigger        TEXT NOT NULL,        -- manual | scheduled
    policy_id      INTEGER REFERENCES policies(id),
    snapshot_id    TEXT,                 -- restore/forget 关联快照
    bytes_done     INTEGER,
    files_done     INTEGER,
    skipped_count  INTEGER NOT NULL DEFAULT 0,       -- 非 root 静默跳过计数（R-012）
    last_event_id  INTEGER NOT NULL DEFAULT 0,       -- SSE 游标（= 日志行偏移，E15 断点续传）
    log_path       TEXT NOT NULL,        -- /data/jobs/<id>.log
    summary        TEXT,
    started_at     TEXT,
    finished_at    TEXT,
    exit_code      INTEGER,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_policy ON jobs(policy_id);

-- 4.6 audit_logs（append-only：代码面仅存在 INSERT 与 SELECT）
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    action      TEXT NOT NULL,     -- login.failed / restore.run / job.skipped_conflict / cache.clear / config.update …
    target      TEXT,              -- 操作对象（快照 id / 策略 id / 路径 / job id…）
    detail      TEXT,              -- JSON
    result      TEXT NOT NULL DEFAULT 'ok',   -- ok / denied / failed
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action, ts DESC);

-- user_version 初值 1（迁移器执行完本脚本后再次写入，幂等）
PRAGMA user_version = 1;
