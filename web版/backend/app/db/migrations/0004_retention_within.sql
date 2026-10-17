-- 0004_retention_within.sql —— A2 forget 保留档位补齐（Duplicati keep-time / retention-policy
-- 分段的 restic 等价物；《Duplicati功能对齐分析与改造计划-2026-10-17.md》批次 A2）。
-- 新增七列（全部可空语义缺省 = 未启用，与 keep_* 整数列的 0 口径一致）：
--   keep_within            restic duration（y/m/d/h，如 1y5m7d2h）——保留最新快照指定时长内的全部快照
--   keep_within_hourly     保留指定时长内的 hourly 快照（自然小时边界，与 keep_hourly 判定同法）
--   keep_within_daily/weekly/monthly/yearly  同上，按日/周/月/年边界
--   keep_tags              JSON 数组；每个元素一条 --keep-tag（多值间 OR 语义）——命中任一的快照保留
-- 校验与拼装：services/restic.py（duration 形态 + 重复单位拒绝 + 全零归一未启用）；S2 白名单透传。
-- greenfield 延续：无存量数据兼容分支（开发期无存量，owner 2026-10-17 A0 同款裁决）。
-- 幂等：ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫（database.py migrate：仅应用
--      version > current 的脚本，重跑返回 0，与 test_migrations 幂等用例同口径）。

ALTER TABLE backup_tasks ADD COLUMN keep_within TEXT NOT NULL DEFAULT '';
ALTER TABLE backup_tasks ADD COLUMN keep_within_hourly TEXT NOT NULL DEFAULT '';
ALTER TABLE backup_tasks ADD COLUMN keep_within_daily TEXT NOT NULL DEFAULT '';
ALTER TABLE backup_tasks ADD COLUMN keep_within_weekly TEXT NOT NULL DEFAULT '';
ALTER TABLE backup_tasks ADD COLUMN keep_within_monthly TEXT NOT NULL DEFAULT '';
ALTER TABLE backup_tasks ADD COLUMN keep_within_yearly TEXT NOT NULL DEFAULT '';
ALTER TABLE backup_tasks ADD COLUMN keep_tags TEXT NOT NULL DEFAULT '[]';

PRAGMA user_version = 4;
