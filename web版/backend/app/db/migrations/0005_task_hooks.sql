-- 0005_task_hooks.sql —— 批次 D C1 任务级前后钩子（Duplicati RunScript 等价物）。
-- 新增两列（空串 = 未启用，与 cron 的 NULL 语义区分：空串是「明确不配」）：
--   pre_cmd    任务进入 restic 阶段**之前**执行的命令（如冻结应用、停容器、导出清单）
--   post_cmd   任务 **成功** 之后执行的命令（如解冻、推送完成标记）；失败不跑
-- 执行口径（services/hooks.py）：shlex 切分为 argv → create_subprocess_exec（**不经
--   shell**，S2 红线）；超时 HOOK_TIMEOUT_SECONDS 后 kill；stdout+stderr 合并追加进
--   任务日志（SSE 可见）；pre 失败 = 任务 failed、post 失败 = 任务 failed（C1 约定）。
-- 无 shell 的代价如实标注：管道/重定向/&& 等 shell 语法不被解释——需要复合逻辑请
--   写成脚本文件并把命令填为「脚本路径 + 参数」（部署指南 §7.2 说明）。
-- 幂等：ALTER TABLE ADD COLUMN 由迁移器 user_version 守卫（database.py migrate：仅应用
--      version > current 的脚本，重跑返回 0，与 test_migrations 幂等用例同口径）。

ALTER TABLE backup_tasks ADD COLUMN pre_cmd TEXT NOT NULL DEFAULT '';
ALTER TABLE backup_tasks ADD COLUMN post_cmd TEXT NOT NULL DEFAULT '';

PRAGMA user_version = 5;
