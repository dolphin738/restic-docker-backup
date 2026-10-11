# AGENTS.md

> 面向 AI 编码代理（engineer / QA）的工作规范。**写代码前必读**，违反 🔴 红线的实现会被审查直接打回。  
> 详细审查标准见 `docs/代码审查规范.md`；技术决策见 `docs/架构设计文档.md` §1.3 与 `docs/Web版改造方案.md`（v2.3.4）；需求边界见 `docs/Web版PRD.md`。
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
pnpm run build                     # 类型检查 + 构建
```

> 前端 **eslint / vitest 尚未接入**（无 `lint` script、无相关依赖、无任何 `*.spec.ts`），**不要执行** `npm run lint`、`npx vitest run`、`eslint .`——它们会失败。详见规范 §7 过渡期说明。

## 🔴 红线（写代码时不得违反）

> 括号内为规范 §3.1 的条目编号，**编号即锚点**；`自查：` 行可直接粘贴到终端执行。

1. **[S1/A2] 路径只经 `PathMapper`**（`app/services/path_mapper.py`）——用户输入路径必须过 `validate()`（规范化 + 前缀校验）；文件系统前缀白名单是 **`/host`、`/sources`、`/restore` 三个**（唯一出处是 `app/settings.py` 的 `host_root`/`sources_root`/`restore_root`，**新增前缀只改那里**）；容器形态路径不出 API（一律 `display_*` / `display_path` 派生字段）。
   *豁免：文件系统前缀字面量只允许出现在 `settings.py`（根路径定义）、`constants.py`（白名单常量）、`exclusions.py`（模式派生输入）、`path_mapper.py`（校验实现）；路由声明里的 `"/restore"`、`"/sources"` 是 **API 路径**，与此无关。*
   自查：`grep -rn '"/host"\|"/sources"\|"/restore"' backend/app frontend/src --include=*.py | grep -v settings.py | grep -v constants.py | grep -v exclusions.py | grep -v path_mapper.py | grep -v routers/`

2. **[S2] restic 参数只在 `ResticCmd.build` 白名单拼装**（`app/services/restic.py`）——禁止自由字符串透传、禁止 `shell=True`。
   自查：`grep -rn "shell=True\|os.system" backend/app`

3. **[S3] 凭据零泄漏**——API 只回 `has_value` + `source`；**session 明文 token 只存在于 cookie，DB 只存 SHA-256**；凭据/session token 不得出现在日志、SSE 事件、审计 detail、异常消息、测试快照中。
   自查：`grep -rn "RESTIC_PASSWORD\|AWS_SECRET\|B2_" backend/app | grep -i "log\|print\|message"`

4. **[S4/S5/S8] 鉴权与首启门（三条合一）**——① 新增端点必须挂 `require_session`（免鉴权仅限 `routers/health.py` 与 auth/bootstrap）；② 写方法（POST/PUT/DELETE）必须过 Origin 同源校验，SSE 与普通 API 同一 cookie 鉴权、**禁止 query token 鉴权**；③ bootstrap 未完成时的全站压制逻辑不得被新路由或中间件顺序破坏，**随机密码明文只能一次性返回，不得进日志**。
   自查：`grep -rn "token=\|token=" backend/app/routers | grep -vi "current_token\|next_token"` ｜ 新增 router 后确认其 `dependencies` 含 `require_session`

5. **[S6] 审计只增不改**——`audit_logs` 代码面只允许 INSERT 与 SELECT（出现 UPDATE/DELETE = Blocker）；**危险操作共 7 项必须埋点**：`forget --prune` / `unlock` / 原位恢复 / **`cache.clear`** / `config.update` / **`login.failed`** / `restore.run`（动作名一律取 `app/constants.py` 的 `AUDIT_*` 常量）。
   自查：`grep -rn "UPDATE audit_logs\|DELETE FROM audit" backend/app`

6. **[S7] P1 禁止任何覆盖语义**——恢复只有「时间戳子目录」一条路径；`--overwrite` 仅限时间戳子目录场景按架构 §6.3 使用；原位恢复在 CompareService（P2）落地前**不得接任何路由**。
   *`--overwrite` 仅限时间戳子目录场景按架构 §6.3 由 `JobRunner` 固定注入（默认 `if-changed`，经 `OVERWRITE_MODES` 白名单校验，界面无入口）；P1 无任何用户可触达的覆盖路径。*
   自查：`grep -rn "overwrite" backend/app frontend/src | grep -vi "restic.py\|job_runner.py\|locales\|restore.py:12"`

7. **[A1] DB 访问只在 `app/db/repositories/`**——路由层与服务层不得直接 import aiosqlite 或拼 SQL。**豁免仅限已合入的 `app/auth/`（独立 auth 模块，自成表边界）与 `routers/health.py` 探针；新增代码不得援引此豁免**（`job_runner.py` 的 jobs 写入应收敛到 `job_repo.py`，属待办）。
   自查：`grep -rn "SELECT \|INSERT INTO\|UPDATE \|DELETE FROM" backend/app --include=*.py | grep -v "app/db/" | grep -v "app/auth/" | grep -v constants.py` —— 当前仅应命中 `health.py` 探针与 `job_runner.py:473`（jobs 写入待收口至 `job_repo.py`），出现其他文件即违规。

8. **[A3] 内置排除集唯一来源是 `app/exclusions.py` 的 `ExclusionRules`**——前端/后端其他位置复制该清单 = 违规（单一事实来源 D8）；UI 只读展示走 E32 端点。
   自查：`grep -rn "lost+found" frontend/src backend/app | grep -v exclusions.py | grep -v constants.py`
   *已知噪声：`routers/sources.py` 的 E31 docstring 会命中（解释伪文件系统告警，非复制排除集）。真正要抓的是把 `lost+found` 写进 `excludes` 默认值或模板里的地方。*

9. **[A5] SQLite 单写连接**——全部写经 `Database.write` 串行；禁止第二个写连接/连接池；WAL + `busy_timeout=5000` + **`foreign_keys=ON`** 初始化不得移除。
   自查：`grep -n "busy_timeout\|journal_mode\|foreign_keys" backend/app/db/database.py`（应恰好 3 处）

10. **[A4/A6] SSE 底座是日志文件、解析层零 IO**——`SseChannel`（`app/services/sse.py`）不得改为纯内存队列（否则丢失断点续传与重启回放）；心跳 15s 与 `X-Accel-Buffering: no` 不得移除；`OutputParser`（`app/services/parser.py`）保持纯函数，**不得 import `os` / `repositories` / `restic`**，破坏 fixture 可回归能力 = 违规。
    自查：`grep -n "^import\|^from" backend/app/services/parser.py`

## 编码约定

- **改解析逻辑必须先补 fixture 再改代码**（用固定版本 restic 的真实输出，放在 `tests/fixtures/`）；
- 任务状态机改动必须同步 `tests/test_state_machine.py` 转换表；迁移只新增 `000N_*.sql`，不改已发布迁移，且过幂等性测试（`tests/test_migrations.py`）；
- 错误响应走 `app/errors.py` 唯一映射点，路由里不得裸 `HTTPException` 返回未登记 code；**新增 `ERROR_STATUS` 条目须同 PR 更新架构文档 §5.0 错误码表**；
- CPU 密集与阻塞 IO 放 `to_thread`，不阻塞事件循环；同仓库任务互斥：手动冲突 409、调度冲突记 cancelled + audit；**`JobRunner.submit` 的并发互斥必须有 `asyncio.gather` 双并发测试**（人工 review 发现不了竞态）；
- 前端请求走 `src/api/` 唯一出口；SSE 一律用 `useJobStream`（组件不直接持有 EventSource，SSE 数据不进响应式树）；危险操作复用 `ConfirmGate.vue`；路径展示复用 `PathBreadcrumb.vue`；文案全部走 i18n（`<script setup>` 中 `const { t } = useI18n()`，**模板文案禁硬编码中文，代码注释里可以**）；大列表用虚拟滚动（`n-data-table virtual-scroll` / `useVirtualList`）；
- 改 `useJobStream.ts` 必须有 mock EventSource 测试；改 `parser.py` 必须跑 fixture 回归；
- 新增依赖需说明理由并锁版本（后端 `uv.lock`、前端 `pnpm-lock.yaml`），禁止引入与既有选型重叠的库。

## Git 与提交

- Conventional Commits（`feat:/fix:/refactor:/test:/docs:/chore:`），**按特性拆分提交，不混无关改动**——拆分在**实现侧**完成，owner 负责审查与合并，不做事后拆分；
- 本仓库实际分支模型：`main ← dev ← feat/<topic>`（`feat/*` 与 PR 为目标形态，当前 P1 走 `dev → main`）；单次合入 ≤400 行 diff（豁免仅 `tests/**` 与 `tests/fixtures/**` 路径）；
- AI 代理实现**不自行提交**——实现完成后报告改动清单，由负责人（`restic-web-dev <dev@local>`，与 git 身份一致）审查后提交并决定是否 push；
- 本地 commit 不自动 push；main 分支仅经审查合并。

## 工作流

三段式：**engineer 实现 → QA 独立验证（核对 git diff 与测试真实性，trust but verify）→ owner 审查合并**。审查按 `docs/代码审查规范.md` 执行：🔴 Blocker（安全/红线/数据丢失）必须修复，🟡 Suggestion 应修复或记 issue，💭 Nit 不阻塞；**🔴 须在同一会话内闭环，跨会话遗留的未处置 🔴 视为阻塞项**。commit 描述须含：改了什么、为什么、如何验证、影响面。
