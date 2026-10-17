-- 0006_task_retry.sql —— 批次 D C3 任务级失败重试（Duplicati number-of-retries 等价物）。
-- 新增一列：
--   retry_count  调度触发失败后的**最大重试次数**（0 = 不重试）；缺省 2（RETRY_COUNT_DEFAULT）
--
-- 语义与边界（逐条对应《Duplicati功能对齐分析与改造计划-2026-10-17.md》批次 D C3）：
-- - **仅调度触发重试**：手动触发（manual）恒不重试——用户点了「立即运行」就是立刻要看结果，
--   重试会把它变成不可预期的长任务；
-- - **仅 restic 阶段重试**：dump 阶段失败直接终态（dump 失败多为配置/版本问题，重试无益），
--   只有 restic 子进程的失败（锁冲突 11 / 网络抖动 / 远端瞬时错误）才值得重试；
-- - 退避：``base * 2 ** (attempt - 1)`` 且钳 RETRY_MAX_DELAY_SECONDS（常量，非列）；
-- - 重试链路仍在同一 job 内：job id / 日志文件 / 仓库互斥锁均不变（E1 并发互斥不回归）。
--
-- 幂等：ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫（database.py migrate：仅应用
--      version > current 的脚本，重跑返回 0，与 test_migrations 幂等用例同口径）。

ALTER TABLE backup_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 2;

PRAGMA user_version = 6;
