-- 0006_task_mirror_repos.sql —— 批次 D C4 任务级多目的地（Duplicati「+ Add another destination」等价物）。
-- 新增一列：
--   mirror_repo_ids  镜像仓库 id 列表（JSON 数组文本，与 keep_tags 同款存储口径）；
--                    缺省 '[]' = 不镜像（单目的地，与 0007 之前行为完全一致）
--
-- 语义（逐条对应《Duplicati功能对齐分析与改造计划-2026-10-17.md》批次 D C4）：
-- - **备份成功后镜像**：JobRunner 在 restic 阶段成功后，对每个镜像仓执行一次
--   ``restic copy``（源 = 该 job 的主仓库，目的 = 镜像仓），copy 输入/输出进同一
--   任务的日志与 SSE；
-- - **增量是 restic 端保证的**：copy 用 ``--tag task-<id>`` 过滤本任务快照，
--   restic 侧按快照的 Original 字段跳过目的仓已有的快照（cmd_copy.go
--   collectAllSnapshots），重复运行只传新快照；
-- - **镜像失败不降级主目的地**：主备份已成功，终态仍记 success，摘要标注镜像失败
--   并逐仓留审计 ``copy.run``（部分成功，禁止把「主仓成功」谎报成「整体失败」）；
-- - **与 Duplicati 的语义差异（如实标注，不假装等价）**：Duplicati 是引擎内一份去重
--   数据同时写两处；本项目是「备份成功后增量镜像」，镜像仓滞后主仓一个 copy 阶段，
--   极端情况下（copy 失败）两边会短暂不一致，靠下一次备份/手动 copy 收敛。
--
-- 幂等：ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫（database.py migrate：仅应用
--      version > current 的脚本，重跑返回 0，与 test_migrations 幂等用例同口径）。

ALTER TABLE backup_tasks ADD COLUMN mirror_repo_ids TEXT NOT NULL DEFAULT '[]';

PRAGMA user_version = 6;
