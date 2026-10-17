# AGENTS.md

> 面向 AI 编码代理（engineer / QA）的工作规范。**写代码前必读**，违反 🔴 红线的实现会被审查直接打回。  
> 详细审查标准见 `docs/代码审查规范.md`；技术决策见 `docs/架构设计文档.md` §1.3 与 `docs/Web版改造方案.md`（v2.3.4）；需求边界见 `docs/Web版PRD.md`；多仓库/多备份任务演进位（R-023 / 选项② R-026）见 `docs/架构设计文档.md` §13。
>
> 文档体系（分层/权威裁决/归档规则）见 `docs/文档管理规范.md`——**新建、移动或归档任何文档前必读**；Duplicati 流程对齐背景见 `docs/archive/对齐Duplicati的改造方案.md`（已归档 2026-10-15：结论归入 Web版PRD.md 与架构设计文档）。
>
> **维护约定**：`docs/代码审查规范.md` §3.1 是红线的**唯一权威版本**；本文件是面向写码代理的**编号索引**——每条给出编号 + 一句话 + 可自查的 grep 线索，**判定标准以规范 §3.1 对应条目为准**。规则变更时**先改规范 §3.1，再同步本文件的编号与线索**，**编号对不上即视为同步失败**。

## 项目一句话

把 restic 备份工具改造为单容器 Web 应用：FastAPI（asyncio 单进程）+ aiosqlite（WAL 单写连接）后端编排 restic 静态二进制，Vite + Vue 3.5 + Naive UI 前端，SSE 实时进度。

## 命令

```bash
# 后端（backend/，用 uv，Python ≥3.13；本机 uv 不在 PATH 时用 .venv/Scripts/python -m ...）
uv run ruff check app tests        # lint
uv run pytest                      # 全量测试
uv run pytest tests/test_parser.py # 只跑解析器 fixture 回归

# 前端（frontend/，用 pnpm——仓库前端依赖由 pnpm 管理，锁文件 pnpm-lock.yaml）
pnpm run typecheck                 # vue-tsc --noEmit
pnpm run test                      # vitest run（前端单测）
pnpm run build                     # 类型检查 + 构建

# 红线机器校验（规范 §3.1 的可执行断言：各红线 grep + 双文档编号锚点一致性）
bash scripts/verify_redlines.sh
```

> 前端 **eslint 尚未接入**（无 `lint` script、无相关依赖），**不要执行** `npm run lint` 或 `eslint .`——会失败。详见规范 §7 过渡期说明。

## 🔴 红线（写代码时不得违反）

> 括号内为规范 §3.1 的条目编号，**编号即锚点**；`自查：` 行可直接粘贴到终端执行。

1. **[S1/A2] 路径只经 `PathMapper`**（`app/services/path_mapper.py`）——用户输入路径必须过 `validate()`（规范化 + 前缀校验）；文件系统前缀白名单是 **`/sources`、`/restore` 两个**（A0 模式收敛 2026-10-17：host 模式与 `/host` 前缀已整体退役；根路径定义在 `app/settings.py` 的 `sources_root`/`restore_root`，白名单常量在 `app/constants.py`，**新增前缀只改这两处**）；容器形态路径不出 API（一律 `display_*` / `display_path` 派生字段）。

   *豁免：文件系统前缀字面量只允许出现在 `settings.py`（根路径定义）、`constants.py`（白名单常量）、`exclusions.py`（排除集派生）、`path_mapper.py`（校验实现）；路由声明里的 `"/restore"`、`"/sources"` 是 **API 路径**，与此无关。任何位置再出现 `"/host"` 前缀字面量即视为 host 模式复活 = Blocker。*

   自查：`grep -rn '"/host"\|"/sources"\|"/restore"' backend/app frontend/src --include=*.py | grep -v settings.py | grep -v constants.py | grep -v exclusions.py | grep -v path_mapper.py | grep -v routers/`

   前端附加自查（A0 补漏 2026-10-17：TS/Vue 用单引号，上面的 .py 扫描覆盖不到——`INPLACE_TARGET_ROOT = '/host'` 曾漏过一版）：`grep -rn "'/host'\|\"/host\"" frontend/src --include=*.ts --include=*.vue` —— 应零命中。
2. **[S2] restic 参数只在 `ResticCmd.build` 白名单拼装**（`app/services/restic.py`）——禁止自由字符串透传、禁止 `shell=True`。

   自查：`grep -rn "shell=True\|os.system" backend/app`
3. **[S3] 凭据零泄漏**——API 只回 `has_value` + `source`；**session 明文 token 只存在于 cookie，DB 只存 SHA-256**；凭据/session token 不得出现在日志、SSE 事件、审计 detail、异常消息、测试快照中。

   自查：`grep -rn "RESTIC_PASSWORD\|AWS_SECRET\|B2_" backend/app | grep -i "log\|print\|message"`
4. **[S4/S5/S8] 鉴权与首启门（三条合一）**——① 新增端点必须挂 `require_session`（免鉴权仅限 `routers/health.py` 与 auth/bootstrap）；② 写方法（POST/PUT/DELETE）必须过 Origin 同源校验，SSE 与普通 API 同一 cookie 鉴权、**禁止 query token 鉴权**；③ bootstrap 未完成时的全站压制逻辑不得被新路由或中间件顺序破坏，**随机密码明文只能一次性返回，不得进日志**。

   自查：`grep -rn "token=\|token=" backend/app/routers | grep -vi "current_token\|next_token"` ｜ 新增 router 后确认其 `dependencies` 含 `require_session`
5. **[S6] 审计只增不改**——`audit_logs` 代码面只允许 INSERT 与 SELECT（出现 UPDATE/DELETE = Blocker）；**危险操作共 14 项必须埋点**：`forget --prune` / `unlock` / **`cache.clear`** / `config.update` / **`login.failed`** / `restore.run` / **`restore.inplace`（原位恢复）** / **`retention.cleanup`** / **`dump.cleanup`** / **`repair.run`** / **`recover.run`** / **`copy.run` + `key.rotate`（C4 多目的地）** / **`config.export` + `config.import`（批次 E 配置迁移）**（动作名一律取 `app/constants.py` 的 `AUDIT_*` 常量；`hook.run` 随 C1 暂缓摘除，恢复时补回）。*`restore.preview` / `forget.preview` 是只读留痕，**不属**危险清单。*

   自查：`grep -rn "UPDATE audit_logs\|DELETE FROM audit" backend/app`
6. **[S7] 覆盖语义仅限「原位恢复（in-place）」路径**（v1.4 修订，owner 2026-10-17 签署；v1.3 2026-10-15；A0 修订 2026-10-17）——引入覆盖语义须**四要件齐备**：① 目标经**双重锁**（`/sources` 前缀白名单 + 快照 `paths` 交集收紧，实现于 restore 路由；A0 前为「rw 档 `host_root` 白名单」，`host_mount_mode` 随 host 模式删除）；② 先经 E20 预览 + **单次确认门**（ConfirmGate 无关键词；v1.4 修订：原「三重确认门 keyword = 快照短 id」放松，对齐 Duplicati 无关键词确认）；③ `--overwrite` 取值 **`never`/`always`**（v1.4 修订：原「固定 `always`」改为 Duplicati 布尔勾选——默认不勾 = `never` 跳过已存在文件，勾选 = `always`；`if-changed` 仅是时间戳路径的服务端固定注入值，原位分支不接受；`OVERWRITE_MODES` 白名单校验）；④ 执行写审计 `restore.inplace`。**时间戳子目录路径（E19 默认）继续零覆盖语义**：`--overwrite=if-changed` 仅由 `JobRunner` 固定注入、界面无入口，E19 请求体在该路径出现 `overwrite_mode` 即 **422**。除上述两类调用点外，任何覆盖语义参数**不得接任何路由**。

   自查（区分两类合法调用点，避免误伤时间戳路径既有注入点）：

   `grep -rn "overwrite" backend/app frontend/src --include=*.py --include=*.ts --include=*.vue | grep -v "restic.py\|job_runner.py\|routers/restore.py\|services/compare.py\|constants.py\|locales\|pages/restore/index.vue\|overwrite_mode\|overwrite_count\|overwrite="` —— 应零命中。**末三项是收窄豁免**：请求/响应模型与前端请求出口必然出现 `overwrite_mode` / `overwrite_count` 这两个契约字段名、docstring 必然引用 `--overwrite=if-changed` 写法（均非覆盖能力入口），故只豁免这三个字面——**其他形态的 overwrite 命中仍属违规**（不整文件排除，避免放过真正的越界）；

   辅助断言：`OVERWRITE_MODES` 是模式枚举唯一出处（`restic.py`）；`job_runner.py` 的 overwrite 透传点 ≤2（时间戳固定注入 + 原位 params 透传）。
7. **[A1] DB 访问只在 `app/db/repositories/`**——路由层与服务层不得直接 import aiosqlite 或拼 SQL。**豁免仅限已合入的 `app/auth/`（独立 auth 模块，自成表边界）与 `routers/health.py` 探针；新增代码不得援引此豁免**。

   自查：`bash scripts/verify_redlines.sh`（A1 段应零命中）｜ 或 `grep -rn "SELECT \|INSERT INTO\|UPDATE \|DELETE FROM" backend/app --include=*.py | grep -v "app/db/" | grep -v "app/auth/" | grep -v constants.py | grep -v "health.py"`
8. **[A3] 内置排除集唯一来源是 `app/exclusions.py` 的 `ExclusionRules`**——前端/后端其他位置复制该清单 = 违规（单一事实来源 D8）；UI 只读展示走 E32 端点。

   自查：`grep -rn "lost+found" frontend/src backend/app | grep -v exclusions.py | grep -v constants.py`

   *已知噪声：`routers/sources.py` 的 E31 docstring 会命中（解释伪文件系统告警，非复制排除集）。真正要抓的是把 `lost+found` 写进 `excludes` 默认值或模板里的地方。*
9. **[A5] SQLite 单写连接**——全部写经 `Database.write` 串行；禁止第二个写连接/连接池；WAL + `busy_timeout` + **`foreign_keys=ON`** 初始化不得移除。

   *`busy_timeout` 实际值走 `settings.db_busy_timeout_ms`（环境变量 `DB_BUSY_TIMEOUT_MS`，默认 5000，取自 `constants.DB_BUSY_TIMEOUT_MS`）——**不得在 `database.py` 里裸写数值**。*

   自查：`grep -n "busy_timeout\|journal_mode\|foreign_keys" backend/app/db/database.py`（应恰好 3 处 PRAGMA）
10. **[A4/A6] SSE 底座是日志文件、解析层零 IO**——`SseChannel`（`app/services/sse.py`）不得改为纯内存队列（否则丢失断点续传与重启回放）；心跳 15s 与 `X-Accel-Buffering: no` 不得移除；`OutputParser`（`app/services/parser.py`）保持纯函数，**不得 import `os` / `repositories` / `restic`**，破坏 fixture 可回归能力 = 违规。

    自查：`grep -n "^import\|^from" backend/app/services/parser.py`
11. **[C8] 时间语义只走 `app/utils/time.py`**——`zoneinfo` 只允许在 `utils/time.py` 直连（`resolve_timezone` 的唯一实现点）；业务代码与测试的时区处理一律走 `resolve_timezone()` / `now_utc()` / `parse_iso()`，直连 `ZoneInfo` = Blocker：tzdata 异常的容器里 `ZoneInfo("UTC")` 的 utcoffset 非零，同刻换算结果随环境漂移（CI 复盘 cnb-opa-1k4qp8tdh、cnb-dgc-1k4qqhg77 两次同签名事故）。

    自查：`grep -rIln "from zoneinfo import\|^import zoneinfo" backend/app backend/tests --include=*.py | grep -v utils/time.py`（应零命中）
12. **[S9] 用户命令执行边界**（v1.5 新增，批次 D C1；**C1 已暂缓下架（2026-10-18），条款保留作为防御性规范——未来任何用户自带命令执行面必须满足**）——四要件：① **不经 shell**（`shlex.split` 切 argv + `create_subprocess_exec`，禁止 `shell=True`/`os.system`/`os.popen`）；② **强制超时**（超时 kill）；③ 输出入任务日志、**审计 detail 绝不含命令行原文**（命令行可能夹带凭据，S3 延续）；④ **写侧校验前置**（长度/引号闭合/控制字符在保存时 422）。设计存档：`docs/暂缓存档-任务级前后钩子C1-2026-10-18.md`。

    自查：`grep -rn "create_subprocess_exec" backend/app --include=*.py`（应只落在 `services/dump.py`——新增子进程调用点必须说明为何不经 shell）｜ `grep -rn "shell=True\|os.system\|os.popen" backend/app`（应零命中）

## 可调阈值（不得裸写数值）

阈值一律走 `settings`（环境变量为大写形式）或构造参数，**常量只作默认值单一真源**。当前可调项：

| 阈值         | settings 字段                             | 环境变量                    | 默认值  | 出处                                |
| ---------- | --------------------------------------- | ----------------------- | ---- | --------------------------------- |
| SSE 空闲心跳   | `sse_heartbeat_seconds`                 | `SSE_HEARTBEAT_SECONDS` | 15.0 | `constants.SSE_HEARTBEAT_SECONDS` |
| SQLite 锁等待 | `db_busy_timeout_ms`                    | `DB_BUSY_TIMEOUT_MS`    | 5000 | `constants.DB_BUSY_TIMEOUT_MS`    |
| 进度落库节流     | 构造参数 `JobRunner(db_flush_interval=...)` | —                       | 1.0  | `job_runner.py`                   |

参照 `JobRunner.db_flush_interval` 的可注入设计——**新增阈值按此模式，不得裸写在业务逻辑里**。改阈值需同步 `docker-compose.yml` 与架构文档 §4.3 config 键表。

## 编码约定

- **改解析逻辑必须先补 fixture 再改代码**（用固定版本 restic 的真实输出，放在 `tests/fixtures/`）；
- 任务状态机改动必须同步 `tests/test_state_machine.py` 转换表；迁移只新增 `000N_*.sql`，不改已发布迁移，且过幂等性测试（`tests/test_migrations.py`）；
- 错误响应走 `app/errors.py` 唯一映射点，路由里不得裸 `HTTPException` 返回未登记 code；**新增 `ERROR_STATUS` 条目须同 PR 更新架构文档 §5.0 错误码表**；
- CPU 密集与阻塞 IO 放 `to_thread`，不阻塞事件循环；同仓库任务互斥：手动冲突 409、调度冲突记 cancelled + audit；**`JobRunner.submit` 的并发互斥必须有 `asyncio.gather` 双并发测试**（断言恰好 1 成功；「预置 running 再触发」不是并发测试）——竞态人工 review 发现不了；
- 前端请求走 `src/api/` 唯一出口；SSE 一律用 `useJobStream`（组件不直接持有 EventSource，SSE 数据不进响应式树）；危险操作复用 `ConfirmGate.vue`；路径展示复用 `PathBreadcrumb.vue`；文案全部走 i18n（`<script setup>` 中 `const { t } = useI18n()`，**模板文案禁硬编码中文，代码注释里可以**）；大列表用虚拟滚动（`n-data-table virtual-scroll` / `useVirtualList`）；
- 改 `useJobStream.ts` 必须有对应测试（vitest 已接入，`pnpm run test`；现有 12 条用例见 `src/composables/__tests__/useJobStream.spec.ts`）；改 `parser.py` 必须跑 fixture 回归；
- 新增依赖需说明理由并锁版本（后端 `uv.lock`、前端 `pnpm-lock.yaml`），禁止引入与既有选型重叠的库；
- **构建相关**：Docker 构建上下文为 `web版/`，`.dockerignore` 位于上下文根；改 `Dockerfile`/`compose` 时执行 `python scripts/check_dockerignore.py` 确认 COPY 源未被排除（排除过头会构建失败）、`.venv`/`.env` 仍在排除列表（防止打进镜像）。

## Git 与提交

- Conventional Commits（`feat:/fix:/refactor:/test:/docs:/chore:`），**按特性拆分提交，不混无关改动**——拆分在**实现侧**完成，owner 负责审查与合并，不做事后拆分；
- 本仓库实际分支模型（2026-10-12 裁决，**dev 分支已删除**）：主干开发——`main` 唯一长期分支，日常改动走 `feat/<topic>` 短分支 → MR → `main`（小改动可直接在 `main` 上提交）；单次合入 ≤400 行 diff（豁免仅 `tests/**` 与 `tests/fixtures/**` 路径）；
- AI 代理实现**不自行提交**——实现完成后报告改动清单，由负责人（`restic-web-dev <dev@local>`，与 git 身份一致）审查后提交并决定是否 push；
- 本地 commit 不自动 push；main 分支仅经审查合并。

## 工作流

三段式：**engineer 实现 → QA 独立验证（核对 git diff 与测试真实性 + 需求符合度——文档声称项逐条对照代码现实，trust but verify）→ owner 审查合并**。**文档超前 / 实现偏离一律提交 owner 裁决，不自行修正，裁决前不合并**（规范 §4.3 步骤 3，v1.6）。审查按 `docs/代码审查规范.md` 执行：🔴 Blocker（安全/红线/数据丢失）必须修复，🟡 Suggestion 应修复或记 issue，💭 Nit 不阻塞；**🔴 须在同一会话内闭环，跨会话遗留的未处置 🔴 视为阻塞项**。commit 描述须含：改了什么、为什么、如何验证、影响面。
