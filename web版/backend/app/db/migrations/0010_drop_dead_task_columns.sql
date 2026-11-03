-- 0010_drop_dead_task_columns.sql —— 删除任务表上的两个零消费列（2026-11-03 裁决 B）
--
-- 背景：`backup_tasks.monthly_full_day` 从建表起就是**死列**——调度器只读全局配置键
-- `behavior.monthly_full_day`（`scheduler._parse_monthly_full_day` ← `config.get_value`），
-- 任务级同名列写库但运行时零消费；policies 表（0001 遗留面，已随选项②退役）同列同理。
-- 留着它会让「向导里看不到、但 API 里能设」的字段长期误导使用者（本次据实核实后裁决删除）。
--
-- 为什么不删 `prune_interval_days`：**它真的生效**——自动 forget 的间隔
-- （`scheduler._due_for_prune`，0 = 关闭自动清理），只是 UI 无入口。保留。
--
-- 幂等：SQLite 的 `ALTER TABLE ... DROP COLUMN` **不支持 IF EXISTS**（列不存在即报错）。
-- 本迁移由迁移器 user_version 守卫（database.py 仅应用 version > current 的脚本），
-- 同一库上不会重跑；全新库执行到本步时两列必然存在（0001/0002 已建）。故此处无需额外守卫。
--
-- 兼容性：SQLite >= 3.35（DROP COLUMN 引入版本）。本仓 python:3.13-alpine 与
-- Windows 开发机的 sqlite3 均 >= 3.50（实测 3.50.4）。

ALTER TABLE backup_tasks DROP COLUMN monthly_full_day;
ALTER TABLE policies DROP COLUMN monthly_full_day;

PRAGMA user_version = 10;
