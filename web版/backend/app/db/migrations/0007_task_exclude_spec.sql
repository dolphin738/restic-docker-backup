-- 0007_task_exclude_spec.sql —— 批次② 结构化过滤条件（设计方案 §2.4）。
-- 新增一列：
--   exclude_spec   过滤条件 JSON（快捷卡 quick + 有序规则 rules，camelCase 与前端 TS 同形）；
--                  NULL = 旧任务（无结构化 spec），行为完全回落到 excludes 列
--
-- 语义：
-- - **spec 为唯一事实源**（spec 存在时）：excludes 列置空，回填/拼装在 JobRunner 与 dry-run
--   时经 services/exclude_spec.flatten_exclude_spec 展开，避免两处各存一份导致漂移；
-- - **旧任务兼容**：exclude_spec 为 NULL 时继续用 excludes 列（纯字符串规则），行为不变；
-- - **校验前置**：spec 的 glob 语法 / negate∧icase 在 TaskService.normalize 时展平校验，
--   非法即 422（不让 [abc 类 pattern 打死后续备份）。
--
-- 幂等：ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫（database.py migrate）。

ALTER TABLE backup_tasks ADD COLUMN exclude_spec TEXT;

PRAGMA user_version = 7;
