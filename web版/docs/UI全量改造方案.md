# UI 全量改造方案（对齐 Duplicati ngclient 设计语言）

> 版本：v1.5（2026-10-19 五次修订：第三轮 aria 盘点 + 移动端走查回填；v1.4 E4；v1.3 R1–R4；v1.2 G6/G7；v1.1 2026-10-19；v1.0 2026-10-17 立项）
> 状态：活跃——owner 2026-10-17 指令立项；2026-10-19 指令修订：「查看项目后端源码与 Duplicati UI 源码，按原对话要求修订方案」（后端能力面已对齐 Duplicati 后的重对比）。
> 设计基准：`Duplicati-ngclient-UIUX分析报告.md`（模版 ngclient 0.0.243，实测评分 7.9/10）+ `D:/agent/UI-UX分析-Duplicati-ngclient/ngclient-src` 源码（本轮细读 8 组组件）。
> 改造范围：**仅 `frontend/src` 的视图与交互层**。API 契约、后端语义、安全红线（S1–S9/A1–A6/C8）一律不动；红线判定以 `代码审查规范.md` §3.1 为准。

> **v1.1 修订说明**：v1.0 立项后，后端已按《Duplicati功能对齐分析与改造计划-2026-10-17》完成能力面对齐（A0 模式收敛 / A4 目的地卡 / A5 rclone / A6 调度双模式 / A7 恢复 Duplicati 化 / B1-B3 自愈 / C2 通知 / C4 多目的地+搬家 / E 配置迁移）；前端视觉层亦已由 P0–P4 批次落地（发布 1.0.1：靛蓝令牌 / 232px 侧栏 / 4 组导航 / StatusTag 全站收口 / KPI 状态带 / 向导草稿）。本修订以 **backend/app 源码实读（13 组路由）+ ngclient 源码 8 组组件细读 + frontend/src 消费现状核对** 为证据，重排第二轮范围：**视觉层维持已落地基线不回退，第二轮聚焦功能面呈现差距（G1–G5）与模版交互吸收（E1–E6）**。

---

## 0. 现状基线（2026-10-19 实读）

### 0.1 功能面对齐矩阵（后端 ✅ = API 已存在；前端 ✅ = 已有 UI 入口）

| 能力 | 后端证据 | 前端证据 | 状态 |
|---|---|---|---|
| A4 目的地类型卡（8 类型 + rclone 预设卡） | `repos.py` 12 端点 | `pages/repo/index.vue:95-103`（destType 卡 + OneDrive/GDrive/S3兼容/其它预设） | ✅✅ |
| A5 rclone 只读探测 | `listRcloneRemotes`（60s 内不重取） | `repo/index.vue:370-378`（按需拉取） | ✅✅ |
| A6 调度双模式（简单/高级/仅手动 + 双向解析） | DB 存 cron 串，零后端改动 | `ScheduleRetentionForm` + `utils/cronConverter` | ✅✅ |
| A7 原位恢复 overwrite 布尔（never/always）+ 单次确认 | S7 v1.4（`OVERWRITE_MODES` 白名单） | `pages/restore/index.vue:81`（overwriteExisting 默认不勾） | ✅✅ |
| B1 仓库修复（repair index/packs） | `JOB_TYPES` 含 repair | `repo/index.vue:465-469`（ConfirmGate 关键词 REPAIR） | ✅✅ |
| B3 找回丢失快照（recover） | `constants.py:19` + `job_runner.py:343`（写 AUDIT_RECOVER_RUN） | **零 UI 入口**（`createJob('recover')` 全库无命中） | ✅❌ → G1 |
| C4 密钥轮换 + 手动 copy（搬家） | `ops.py:576/683` | `repo/index.vue`（密钥轮换 `rotateShow:487`/`rotateRepoKey:512`，NModal 共用 `PasswordEntryModal`；手动 copy `copyShow` 区块） | ✅✅ |
| C4 任务级多目的地（镜像仓） | `backup_tasks.py:81`（镜像仓存在性 422） | `TaskWizard.vue:85/662`（镜像仓多选） | ✅✅ |
| E 配置导入/导出（口令加密凭据段） | `ops.py:358/413` | `pages/settings/index.vue:384-440`（覆盖确认门 IMPORT + 逐项报告） | ✅✅ |
| E23 通知配置 + 测试消息 | `ops.py:755-854` | `pages/notifications/index.vue` | ✅✅ |
| E25 缓存占用/清空（E26 审计查询） | `ops.py:879-976` | E25：仓库页维护区唯一入口（设置页专区已并入，复用 `getCacheUsage`/`clearCache`）；E26：审计页 | ✅✅ |

### 0.2 视觉层基线

P0–P4 视觉批次已落地（`git log`：9468e7b P1 视觉换肤 → 1a1b602 P4b，changelog 37ee151 发布 1.0.1）。**第二轮不重开视觉基线**（不回退靛蓝令牌/系统字体栈）；v1.0 中「hsl(217) 色板 / Inter Tight webfont」两条随视觉批次关闭。模版视觉范式（药丸导航态、全平面、渐变重音、等宽技术文本）已在 P1/P2 批次内对齐。

## 1. 第二轮差距清单（模版呈现 ↔ 本项目后端 ↔ 前端现状）

| # | 模版证据（ngclient 源码） | 本项目现状 | 处置 | 优先级 |
|---|---|---|---|---|
| **G1** | 数据库维护页「Recreate (delete and repair)」：`database.component.html:53-56` | 后端 B3 完整（recover 写审计、含 startup 自愈）；前端零入口 | repo 维修区新增「找回丢失快照（recover）」：说明文案（找回索引丢失的未引用数据）+ ConfirmGate 关键词 RECOVER + 提交后 toast 带 SSE 入口；维修区布局对齐模版 Maintenance 分区（说明段落 + 动作行） | 高 |
| **G2** | 任务历史可按类型过滤 | `jobs/index.vue:82` `JOB_TYPES` 数组漏 `repair`/`recover` 两项 → 两类历史不可筛 | 补齐数组 + i18n `jobs.type.repair/recover` 词条 | 高（随 G1 同批） |
| **G3** | 连接测试强门禁：`destination.component.ts:325-352` testStates 顺序验证、success 缓存跳过重测；`destination.component.html:193-202` Continue disabled | `testRepo` API 已有；repo 页保存与向导③无门禁 | = E1（v1.0 §7 已裁决纳入）：测试通过前禁用「保存/继续」，失败显示重试，**无逃生门** | 高 |
| **G4** | 导出配置口令体验：强度进度条 + 生成 + 复制（`export.component.html:85-152`） | settings 导出已有口令输入（`settings/index.vue:391`），无强度反馈 | 可选增强：口令强度条 + 生成按钮；复制门禁不引入（导出口令为一次性自设口令，模版门禁依赖其「密码必复制」策略，本项目以确认门兜底） | 低（可选） |
| **G5** | 调度摘要 `aria-live="polite"` 实时播报：`schedule.component.html:23-76` | A6 简单模式无摘要区 | 简单模式底部加实时摘要（「每周一 03:00 运行」），aria-live 播报——超越层（模版亮点直接吸收） | 中 |
| **G6** | 维护页分区范式：**先解释风险再给动作**——Maintenance 分区（说明段落 → Repair/Recreate/Delete/Compact 动作行）+ Placement 分区（`database.component.html:20-122`） | repo 页维修操作（repair/unlock/缓存清空/recover）散置各处，缺统一「解释→动作」分区 | repo 维修区重排为 Maintenance 同构分区：分区标题 + 风险说明段 + 动作行（修复 / 解锁 / 找回 / 清缓存）；G1 的 recover 入口落在此分区 | 高（随 R1） |
| **G7** | 向导骨架：横向步骤条（可点击跳步）+ 居中表单区 + 「返回← / 继续→」两端对齐页脚 + 每步独立校验跳转（`options.component.ts:62-74` submit 前按校验结果导航回未过步骤） | TaskWizard 有步骤条但页脚/跳步/校验回流形态未对齐 | 向导骨架对齐：步骤可点击（已过步骤）+ 两端页脚 + 提交时校验失败自动导航回首个未过步骤；E2/E3 落在此项 | 高（随 R3） |

## 2. 交互增强 E1–E6（v1.0 §7 保留，状态更新）

| # | 增强项 | 状态（2026-10-19） |
|---|---|---|
| E1 | 连接测试强门禁 | 待实施（= G3，第二轮范围） |
| E2 | 向导离开保护（onBeforeRouteLeave + beforeunload） | 待实施 |
| E3 | 步骤条可点击跳步（键盘可达，不用 tabindex=-1） | 待实施 |
| E4 | 设置仓库密码复用共用弹窗（NModal → 行内面板 → 共用 `PasswordEntryModal`） | ✅ 2026-10-19（对象为 repo 页「设置仓库密码」，历经弹窗→行内→共用组件三形态，见 §4.1.2） |
| E5 | 空态 CTA 化 | 部分已有（仪表盘引导卡）；其余空态随页面扫尾统一 |
| E6 | 状态化着色 | ✅ 已达成（StatusTag 全站收口，P3a-c 批次 23 处） |

## 3. 排除项（v1.0 §8 修订后确认）

| 模版功能 | 不做理由 |
|---|---|
| delete-backup 梯度三勾选（本地库/导出抢救/远程文件，`delete-backup.component.html`） | 架构不同：本项目删除的是**任务**，restic 仓库与其数据在仓库级管理（repo 页独立生命周期），不存在「随任务删远程」语义；删除前导出抢救由 E 配置导出覆盖 |
| 备份级加密密码 + 复制门禁 | 无备份级加密概念；仓库凭据走 has_value/source（S3） |
| 30+ 后端类型选择卡 | A4 已以 8 类型卡 + rclone 预设卡落地（rclone 桥接覆盖其余后端），语义等价 |
| export 双模式（cmd 行 / 文件） | restic 引擎无对应导出形态；仅文件包（E 批次已落地） |
| 欢迎向导 / 版本标签 / 命令行执行器 / purge-files | 新功能，走 `Web版PRD.md` 需求池 |

## 4. 实施顺序（第二轮，每批独立可提交）

1. **R1 = G1 + G2 + G6**：repo 维修区重排为 Maintenance 同构分区（解释→动作行）+ recover 入口 + jobs 筛选补 repair/recover（同批提交，i18n 双语同步）
2. **R2 = G3/E1**：连接测试强门禁（repo 页保存 + 向导第③步；testStates 顺序验证 + success 缓存）
3. **R3 = G7 + E2 + E3**：向导骨架对齐（步骤条可点击 + 两端页脚 + 校验回流）+ 离开保护
4. **R4 = G5（+ 可选 G4）**：调度摘要 aria-live；E4 行内展开改版
5. **R5 = 验证**：`typecheck` → `vitest` → `build` 三步分跑；dev server 真机截图（登录/仪表盘/仓库维修区/向导③门禁态）；按 §5 口径自评

### 4.1 实施回填（2026-10-19 R1–R4 已落地，未提交）

| 批次 | 落地内容 | 关键文件 |
|---|---|---|
| **R1** | G6 维护分区（先解释风险→动作行：解锁/修复/找回/初始化/轮换/复制/清缓存）；G1 recover 入口（ConfirmGate 关键词 `RECOVER`）；G2 jobs 筛选补 `repair`/`recover` | `pages/repo/index.vue`（维护卡 + 两个 ConfirmGate + `.repo__maint-row`）、`pages/jobs/index.vue:82` |
| **R2** | E1 强门禁：向导第③步（仅新建模式，success 按仓库缓存、切仓作废、无逃生门）+ repo 页「改配置 → 结论作废 → init 挡在复验前」 | `components/TaskWizard.vue`（testState/testedRepoId/step3Blocked）、`pages/repo/index.vue`（repoTestState/verifyRequired） |
| **R3** | G7 页脚两端对齐 + 提交时回流首个未过步骤；E3 步骤条可点击跳步 + 键盘（←/→、1–5）；E2 离开保护（路由守卫 + beforeunload） | `components/TaskWizard.vue`（onStepJump/onStepsKeydown/focusFirstInvalidStep/onBeforeRouteLeave） |
| **R4** | G5 调度摘要（aria-live 播报）；G4 导出口令强度条 + 生成按钮 | `components/task/ScheduleRetentionForm.vue`、`utils/passwordStrength.ts`（+ 11 例 spec）、`pages/settings/index.vue` |

**新增护栏**：`src/utils/__tests__/passwordStrength.spec.ts`（11 例：分档边界 + 生成口令必落强档 + 不含易混淆字符）、`src/__tests__/i18n-message-syntax.spec.ts`（2 例：文案禁止未转义 `@`）。

### 4.3 第三轮：aria 盘点 + 移动端走查（2026-10-19，冲 9.5 的两项底座工程）

**aria 盘点（可访问性 4.5 → 补课）**：静态扫描 24 个 .vue，真缺口两类——图标按钮 1 处（移动端汉堡）+ n-switch 无可访问名 10 处（naive 不关联相邻文案）。全部补齐（`:aria-label` 与相邻文案同键）+ `a11y-baseline.spec.ts` 静态护栏（缺失即 CI 拦截 + 空跑防护）。接受项：纯装饰 n-icon 不逐个加 aria-hidden。

**移动端走查（响应式 5 → 补课）**：4 视口（1440/960/768/390）× 11 页真机截图。实锤 4 项全部修复，改法遵守「只增断点、不改桌面基础规则」：

| # | 问题（390px 实拍） | 修复 | 桌面影响 |
|---|---|---|---|
| 1 | 五张数据表列挤压竖排/行动作裁切 | 补 `:scroll-x`（760/720/860/560/900，均 ≤ 桌面内容宽 1140） | 无（宽于阈值时 scroll-x 不生效） |
| 2 | audit 时间范围选择器 360px 固定宽溢出 | `max-width: 100%` | 无（360px 恒等） |
| 3 | forget 控件行 `:wrap="false"` 裁切 | 允许换行 | 无（放得下则不换行） |
| 4 | 向导 5 步在 390px 裁切 | 步骤条容器横向滚动（**仅 @media ≤960**） | 无（见下） |

**⚠️ 实测教训（像素 diff 抓出的回归）**：`overflow-x: auto` 不能无条件加——滚动容器会「收住」子元素 margin（不再与父级折叠），桌面端 steps 导航 22→30px、下方整体下移 8px；已回退为断点内启用。

**桌面零漂移验收**：修复页 1440 全页截图走查前后像素 diff——repo/jobs/audit/tasks_new **0.000%**，snapshots 0.102%（toast 动画瞬时态，结构无差异）。390 断言：步骤条 scrollWidth 503 > clientWidth 308（5 步全部可达）。

**走查后评分影响**：响应式 8.5 → **9**（全量实测背书）；可访问性 8 → **8.5**（覆盖已护栏化，读屏实测仍未做）。

### 4.1.2 E4 落地回填（2026-10-19，同日追加）

**对象纠正（取证结论）**：v1.0 把 E4 写成「更改密码弹窗 → 行内」时未指明页面。实读发现——设置页管理员改密（E28）**从来就是行内卡**（`git log -S "n-modal"` 在 settings/index.vue 零命中）；全项目唯一的密码弹窗是 repo 页「设置仓库密码」（passfile 缺失时的一次性引导）。故 E4 的真实对象是后者。

**改造（v1.4 落地；v1.6 已二次修订改回 NModal 弹窗，见下方「E4 二次修订」）**：原 `NModal` → 状态卡内**行内展开面板**（`v-if="passwordShow"`，按钮再点一次 = 收起；`role="region"` + aria-label）。对齐 Duplicati 向导 Step1 的行内形态——密码缺失本就发生在读状态卡的语境里，弹窗打断「看到问题 → 当场处理」动线。两段式流程（生成→复制 / 自填）、`setRepoPassword` API、一次性明文「面板存活期可见、收起即丢弃」的语义全部保留。

**真机断言**（`.e2e/e2e-e4b.cjs`，临时仓库完整生命周期 + 自清理）：新建临时仓库（不 init）→ passfile 必然缺失 → 面板可见 ✅ / **`.n-modal-mask` 数量 = 0** ✅ / 生成按钮可见 ✅ / 取消后面板收起 ✅ / 三步 DELETE 门删除临时仓库 ✅（页面 0 pageerror）。

**E2E 取证沉淀**：ConfirmGate 是自定义内容 n-modal（**无 `.n-modal` 类**，祖先选择用 `.n-modal-body-wrapper`）；三步门按钮类型依次 primary（下一步）→ warning（下一步）→ error（最终确认），自动化必须按步走完。

**E4 二次修订（2026-10-19 第六轮，commit eb9cf69）**：又将行内展开面板**改回 NModal 弹窗**，并抽取共用组件 `frontend/src/components/PasswordEntryModal.vue`——「设置仓库密码（E44）」与「密钥轮换（C4）」共用同一弹窗外壳，仅输入语义各异：E44 生成走后端（已落盘后一次性回显明文，`generated=true`）、自填直接提交；C4 生成走前端本地强随机（`generateStrongPassword`，因轮换新密码后端不回显 S3），复制后点「确认轮换」才执行 `restic key passwd`。同时修复弹窗瘦长条（原 `repo__modal` 类无 CSS 定义，n-modal 被撑满全屏宽；组件内 `width: min(520px, calc(100vw - 32px))` 收口）。`repo/index.vue` 删除行内面板/双栏确认/死 CSS，行号迁移：设置密码 `passwordShow:238`/`openPassword:246`/`generatePassword:256`/`setRepoPassword:272`/modal `1414-1437`；密钥轮换 `rotateShow:487`/`onRotate:498`/`rotateRepoKey:512`/modal `1439`。

### 4.1.1 R5 验证结果（2026-10-19 实测）

| 门 | 结果 |
|---|---|
| `pnpm run typecheck` | ✅ 0 error |
| `vitest run`（12 文件） | ✅ **111 passed** |
| `NODE_OPTIONS="" pnpm run build` | ✅ built |
| 真机（vite :5173 + 后端 :8000，`.e2e/e2e-r1r4*.cjs`） | ✅ 四页截图 + 结构化断言：jobs 类型下拉实测 9 项含「修复/找回」；强度条「太短→强」+ 生成 20 位；repo 测试连接后状态由「尚未验证」转「连接失败」且错误详情可见；recover 按钮在未连接时禁用（符合门禁） |
| 控制台（`.e2e/e2e-console-check.cjs`，/repo /settings /jobs /notifications） | ✅ **0 error**（同时修掉一处既有缺陷，见 §4.2.3） |

> 真机口径提醒：vite 只监听 `localhost`（`127.0.0.1:5173` 连不上），E2E 用 `http://localhost:5173`。截图存于 `.e2e/r1r4-*.png`（gitignore，不入库）。

### 4.2 两处裁决修订（实施中取证推翻方案原文）

1. **E1 在 repo 页的形态改为「保存后强制复验」，不是「保存前必须先测」。**
   证据：后端 `testRepo`（`routers/repos.py:394` POST `/repos/{id}/test`）读的是**已落库**配置，而凭据必须先保存才能被测到——若把「保存」挡在测试之后，用户永远无法录入第一组凭据（死锁）。等价强门禁：URL/凭据保存成功 → 连接结论置 `idle` → 显示复验提示 → **init（不可逆）禁用至复验通过**。向导侧保持原文的无逃生门禁。
2. **额外修掉一处真机才暴露的既有缺陷（i18n `@` 语法冲突）**：`repo.urlPlaceholder` 等 4 个键（`sftp:user@host:/path`、`sender@example.com`）里的 `@` 被 vue-i18n 当作 linked message 语法起点，**每次渲染都抛 `Message compilation error: Invalid linked format`**；页面照常显示（回退原文），故 typecheck/vitest/build 三道门全放行。定案：改为字面量插值 `{'@'}`（外层字符串同时改双引号，避免单引号提前闭合），并加 `i18n-message-syntax.spec.ts` 静态护栏。复验：四个页面控制台 0 error。

3. **删除「E25 缓存前端零入口」的前提**——该前提是我上一轮 grep 漏配造成的误判。
   证据：`api/settings.ts:12/19` 已有 `getCacheUsage`/`clearCache`（`/api/cache`、`/api/cache/clear`），当时 `pages/settings/index.vue` 有完整缓存专区（占用/truncated/409 冲突提示/ConfirmGate）。故当时 repo 维护区的「清缓存」是复用同一对函数的第二入口，409 分支沿用 `settings.cache.conflict` 文案，不新造一套。（据此删除了误建指向 `/api/ops/cache` 的模块——该路径在后端不存在。）**2026-10-19 第六轮修订**：设置页缓存维护专区已整体并入仓库页维护区（删除 `settings/index.vue` 的 `sec-cache` 卡片、锚点目录 `cache` 项、失效 import），仓库页成为 E25 唯一前端入口（占用详情改纯文本注释口径 `cachePathLine`/`cacheMeasuredLine`/`cacheTruncated`，复用 `api/settings` 同一对函数）。

**护栏**（沿 v1.0）：不改 API/composable；既有 spec 原样通过；ConfirmGate/StatusTag/PathBreadcrumb 只复用不换接口；文案全走 i18n 双语；危险操作埋点由后端既有 AUDIT_* 承担（前端零新增端点）。

## 5. 验收与自评口径（按报告 10 维度 + 功能面新增）

| 维度 | 口径 |
|---|---|
| 布局与导航 / 配色 / 字体排版 / 控件统一 / 组件复用 | ✅ P0–P4 已落地（1.0.1），第二轮不回退 |
| 状态设计/防错 | G3 强门禁 + E2 离开保护 + ConfirmGate 体系；模版 database 页「先解释风险再给动作」的分区文案范式随 G1 吸收 |
| 本地化完整性 | 100% i18n 双语（G1/G2 新词条照此纪律） |
| 响应式 / 可访问性 | 响应式随 P2 已落地；A11y 增量 = G5 aria-live 摘要 + 图标按钮 aria-label 补漏 |
| **功能面覆盖度（v1.1 新增维度）** | 后端 JOB_TYPES 全量 9 类 + ops 全部危险操作均有 UI 入口且可筛（G1/G2 修完即满）；模版交互范式吸收率 = E1–E5 全落地 |

≥9 分判据：G1/G2/G3 + E2/E3 落地（功能面零断链）+ 真机截图无形态级偏差；G4/G5 为加分项。

### 5.1 实测自评（2026-10-19 R1–R4 + R5 验证后，对照报告 §10.1 同一维度表）

| 维度 | 模版终版 | 本项目自评 | 依据（全部为实测证据，非印象分） |
|---|---|---|---|
| 布局与导航 | 9 | **9** | P0–P4 三段式框架 + 本轮仓库维护分区/向导两端页脚；真机截图无形态偏差 |
| 配色系统 | 9.5 | **9.5** | 靛蓝令牌基线 + StatusTag 全站收口；本轮新增 UI 全部引用既有语义色，零新硬编码 |
| 字体排版 | 9 | **9** | 单字型四级层级 + 等宽技术文本（pack id / 仓库 id / cron） |
| 控件与交互模式 | 9 | **9** | 步骤条（可点击+键盘）/ 两端页脚 / ConfirmGate / Toast 全站统一 |
| 组件复用度 | 9.5 | **9.5** | ConfirmGate 6 处复用（UNLOCK/INIT/REPAIR/RECOVER/CLEAR/DELETE）、JobProgress 4 处、StatusTag 全站、`PasswordEntryModal` 2 处（E44 设置密码 / C4 密钥轮换） |
| 状态设计/防错 | 8.5 | **9** ↑ | E1 强门禁（向导无逃生门 + repo 改配置强制复验）+ E2 离开保护（模版没有的能力）+ G5 摘要回读；真机实测门禁生效（未连接时 recover 禁用） |
| 本地化完整性 | 6 | **9.5** ↑ | i18n parity 护栏（双语键集合全等）+ 本轮修掉 `@` 编译错误 + 新增 message-syntax 静态护栏；未做翻译质量人工审校，故不满分 |
| 响应式适配 | 5 | **8.5** ↑ | P2 断点体系 + 本轮维护行 640px 纵向堆叠；**全站移动端走查未做**，保守计分 |
| 可访问性 | 4.5 | **8** ↑ | 本轮：aria-live 摘要 / nav 键盘导航 / 焦点环 / ConfirmGate 语义；**全站 aria 审计未做**（模版 0 aria，本项目已非零但未盘点），保守计分 |
| 设计系统/工程成熟度 | 9.5 | **9.5** | 三道 CI 门 + 12 spec 文件 111 例 + 真机 E2E 脚本化（`.e2e/`）+ 方案/文档/登记表同步 |
| **综合** | 7.9 | **≈9.0** | 感知层与模版持平；模版三大底座硬伤（本地化/响应式/A11y）中本地化已反超，响应式与 A11y 做了增量但未做全站审计——这也是后续冲 9.5 的明确路径 |

**判据核对**：≥9 判据 = G1/G2/G3 + E2/E3 落地 ✅ + 真机截图无形态级偏差 ✅（另有 G4/G5 两个加分项落地）。**达标。**

---

## 修订记录

- v1.6（2026-10-19 六次修订）：E4「设置仓库密码」由状态卡内行内展开面板**改回 NModal 弹窗**，并抽取 `PasswordEntryModal.vue` 共用组件（E44 设置密码与 C4 密钥轮换复用，生成语义各异：E44 后端生成回显 / C4 前端本地强随机）；弹窗 `width` 收口修复瘦长条（原 `repo__modal` 类无 CSS 定义）；E25 缓存维护设置页专区**整体并入仓库页维护区**成为唯一入口（占用详情改纯文本注释口径，删除设置页 `sec-cache` 卡片与锚点项）。后端与红线区零改动（commit eb9cf69）。
- v1.5（2026-10-19 五次修订）：第三轮底座工程回填（§4.3）——aria 盘点（缺口 11 处补齐 + a11y-baseline 静态护栏）与移动端 4 视口走查（4 项实锤修复，含「overflow-x:auto 使 margin 不折叠 → 桌面 +8px」的像素级回归教训）；桌面零漂移以像素 diff 验收（0.000%）；评分更新：响应式 9 / 可访问性 8.5。
- v1.4（2026-10-19 四次修订）：E4 落地（§4.1.2）——对象由「更改密码弹窗」纠正为 repo 页「设置仓库密码」NModal（设置页 E28 本就是行内卡，`git log -S` 取证）；改为状态卡内行内展开面板（API 不变，一次性明文语义保留）；真机以「临时仓库完整生命周期 + 自清理」断言（modal 遮罩 = 0、面板开合、DELETE 三步门）。E1–E6 至此全部落地。
- v1.3（2026-10-19 三次修订）：R1–R4 实施回填（§4.1 逐批落地清单 + 新增 passwordStrength spec 护栏）；§4.2 记录两处**由实施取证推翻方案原文**的裁决——① E1 在 repo 页改为「保存后强制复验」（`testRepo` 读已存配置，先测后存会与凭据录入死锁），② 撤销「E25 前端零入口」误判（`api/settings.ts` + 设置页专区已完整覆盖，repo 维护区为复用式第二入口）。E4（更改密码行内展开）仍未实施，转入待办。
- v1.2（2026-10-19 二次修订）：owner 裁决「实施能让每个功能各层级交互布局都跟 Duplicati 类似吗」讨论落档——新增 G6（repo 维修区 Maintenance 同构分区重排）与 G7（向导骨架对齐：步骤可点击 + 两端页脚 + 校验回流），并入 R1/R3 批次；相似度口径分层（L1 框架 100% 已达成 / L2 共有功能骨架 ~90% 可达 / L3 独有功能按语言延展）记入本记录，复刻上限以「布局范式级相似」为准。
- v1.1（2026-10-19）：按 owner 指令基于后端对齐后的源码实读 + ngclient 8 组组件细读修订。新增 §0 现状基线（功能面对齐矩阵 11 行 + 视觉层 P0–P4 已落地不回退）；新增 §1 差距清单 G1–G5（recover 零 UI 入口 / jobs 筛选漏项 / 连接测试强门禁 / 导出口令强度 / aria-live 摘要）；E1–E6 状态更新（E6 已达成）；排除项补 delete 梯度与 export cmd 双模式裁决；实施顺序重排为 R1–R5；自评口径新增「功能面覆盖度」维度。v1.0 的「hsl(217) 色板 / Inter Tight」条目随视觉批次落地关闭。
- v1.0（2026-10-17）：立项。范围边界（壳/核）、四层换肤结构、§7 交互增强 E1–E6（owner 裁决纳入）、实施顺序与自评口径成文。
