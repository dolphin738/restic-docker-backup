# UI 全量改造方案（对齐 Duplicati ngclient 设计语言）

> 版本：v1.38（2026-10-26 三十八修订：**P20 缺陷回归——地址栏三角恒不出现**；根因是挂载点原靠树根响应顺带填，而后端 E33 只在 `container == "/"` 的请求回 `mounts`，本页树起点是已填路径的父目录 → 一次也拿不到 → 三角 `v-if` 恒假，须手动点回 `/` 才出现。改为 `ensureMounts()` 与树位置**解耦**独立取（复用 `RestoreTargetPicker` 同款写法），三处接线 + 三角补 `Esc` 收起。详见 §4.9.10 末尾。本文件全部历史修订（v1.0–v1.38）合并记载于文末「修订记录」节，版本头不再重复内联。
> 状态：活跃——owner 2026-10-17 指令立项；2026-10-19 指令修订：「查看项目后端源码与 Duplicati UI 源码，按原对话要求修订方案」（后端能力面已对齐 Duplicati 后的重对比）。**2026-10-23 起为 UI 视觉与功能面的唯一权威**：原《UI界面设计方案》（视觉基线）已作废并入 §6，不再另存视觉文档（文档管理规范 §5 硬规则 3）。
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

> **视觉基线权威说明（2026-10-23）**：自《UI界面设计方案》作废并入起，**本文件是 UI 视觉与功能面的唯一权威**。视觉令牌 / 色彩对比度 / 字体 / 间距 / 圆角 / 阴影 / 布局 / 动效 / 可访问性 / 明确不采纳等设计基线内容，统一见 **§6 视觉设计基线**；其中圆角（12/8）、暗色三档阴影、组件级形态已随 §4.6 彻底档升级，§6 已逐条标注 supersession。

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
| E1 | 连接测试强门禁 | ✅ 已落地（R2）；2026-10-20 第四轮强化为**按钮级状态机**：新建态「创建并测试」、编辑态脏且未验证「保存并测试」（warn/error），见 §4.4 |
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

### 4.4 第四轮：仓库页「目标位置」模块复刻（2026-10-20 回填，owner 逐轮指令落地）

> 对标 ngclient `destination.component` / `destination.config.ts`（customFields + dynamicFields）：**不在列表页上平铺配置**，而是「列表态 ↔ 编辑态」整页替换，字段按存储类型动态渲染，URL 由字段组译而非裸填。本轮除 §4.4.3 外**零后端业务改动**，API 契约不变。

#### 4.4.1 交互形态定稿

| 项 | 形态 | 依据 / 取舍 |
|---|---|---|
| 两态交互 | 列表态与编辑态**整页替换**（非叠加弹窗），保存成功即回列表态 | 对齐 Duplicati `selectedDestination` 语义（选中即编辑、保存即 `null`） |
| 类型驱动字段 | 8 类型各一套字段组（local / s3 / b2 / gcs / azure / sftp / rest / rclone），凭据区随类型联动 | 修掉早期「配置项绑错对象」缺陷：字段源一律取 `repoForm.type`，**不取已有仓库的 `repo.type`** |
| URL 组译 | `composeRepoUrl()` 按类型拼；反向 `parseRepoUrl()` 解析回填，解析失败回退 `raw` 原文输入 | 用户裁决「URL 还是用现在的字段组译」——不做裸地址框 |
| 常用值下拉 | 点击输入框**即展开**全部候选（`n-auto-complete` 官方 hook `:get-show="() => true"`），仍可手填 | naive `mergedShowOptions = !!value`：聚焦空框默认不展开，需显式覆盖 |
| 测试状态机 | 新建态 = **「创建并测试」单按钮**（建完立刻测一次，二合一）；编辑态：脏且未验证 → 「保存并测试」（warn，失败转 error）；已验证或无变更 → 「保存」（primary） | 后端 `testRepo` 读**已落库**配置（§4.2 裁决 1），故顺序恒为「先存后测」，不存在「先测后存」 |
| 时序约束 | 新建后 `refetch()` → **`await nextTick()`** → `onTest()` | 必须放行 `watch(currentRepoId)` 先清完旧状态，否则它会晚于 `onTest()` 把结论清掉 |
| 仓库 ID | 列表行弱化回显 `#id`、编辑卡头「仓库 ID：#id」 | 用户裁决：改造过程中曾丢失，已恢复 |
| 类型自动选中 | 点「管理」即同步 `repoForm.type = normalizeRepoType(row.type)` | 消除 watch 异步竞态（靠 watch 会有半拍错类型） |
| 初始化按钮 | 移到仓库列表每行「管理」旁，**仅「有 URL 且未连接」时显示** | 初始化是不可逆动作，不再常驻编辑区 |

#### 4.4.2 S3 地址方言修正（**事实核验推翻原实现**）

**缺陷**：本项目此前按 `s3://bucket/...` 组译，与 restic 原生方言不符——落库即错地址。

**核验结论（restic 0.19.1 源码）**：S3 官方语法为 **`s3:<endpoint>/<bucket>[/<prefix>]`**，endpoint 与 bucket 之间是**斜杠**（冒号仅用于协议头或 `host:port`）。

**修法**：界面字段改为「服务器地址 endpoint（必填，形如 `s3.amazonaws.com` 或 `host:port`）+ Bucket 名称 + 文件夹路径」；组译 `s3:{endpoint}/{bucket}[/{prefix}]`——**path 为空时不补尾斜杠**（restic 以首个 `/` 切 endpoint，`s3:host/bucket` 即可正确切出 bucket，且与解析严格互逆，避免「假脏」反复提示未保存）。解析侧 `parseS3Url()` 兼容 `s3:host[:port]/bucket[/prefix]`、`s3://host/…`（归一）、`s3:http(s)://host:port/…` 三种存量写法。

**退役两个死字段**：
- URL 的「区域（可选）」段——restic S3 方言无该段；
- 凭据键 **`AWS_ENDPOINT_URL`**——实证 restic 0.19.1 不读该环境变量，其 minio-go v7.1.0 依赖零引用，属死凭据。区域改由 `aws_default_region` 凭据字段承担（S3 凭据集收敛为 `aws_access_key_id` / `aws_secret_access_key` / `aws_default_region` 三项）。

**全类型审计结论（8 种逐一比对 restic 原生）**：**仅 s3 一种错误**；b2（`b2:bucket:/path`）、gcs（`gs:bucket:/path`）、azure（`azure:container:/path`）、sftp（`sftp:user@host:/path`）、rest（`rest:<url>` 原样）、rclone（`rclone:<remote>`，整段即 remote:path）、local（裸路径，restic `location.Parse` 自动补 `local:` 前缀）全部符合，零改动。

#### 4.4.3 后端改动（本轮唯一例外：子进程事件循环防护）

**现象**：仓库页选 rclone 提示「探测失败：AppError 服务器内部错误」；任何 restic 操作同样 500。

**根因（Windows 实证）**：uvicorn 裸 `--reload` 走 `use_subprocess=True`，Windows 下强制 `SelectorEventLoop`，其 `create_subprocess_exec` 抛 `NotImplementedError`（`asyncio/base_events.py:539`）——**不是 rclone 未安装**（本机未装 rclone 时探测会诚实返回 `available=false`，属预期降级）。

**四层防护**（不改业务语义）：① `constants.SUBPROCESS_LOOP_HINT` 常量（错误文案直指 `--loop none`）；② `rclone.list_remotes` 捕 `NotImplementedError` → 降级 `available=false` + 可操作 `reason`（与缺二进制/超时同口径，绝不裸 500）；③ `restic.spawn` 捕同异常 → `ResticFailedError(SUBPROCESS_LOOP_HINT)`；④ `main.py` lifespan 启动自检（win32 且非 `ProactorEventLoop` → `logger.error` 明示）。

**启动纪律（已写入 AGENTS.md 与部署指南）**：本机后端启动**必须带 `--loop none`**；首选 `dev-scripts/dev.ps1 restart backend`（已内置）。新增护栏 `backend/tests/test_subprocess_loop_guard.py`（2 例）。

#### 4.4.4 审计日志中文化

- **动作名**：`audit.action.*` 35 项映射。**i18n 点号是层级分隔符**，键必须写成嵌套对象（`audit.action.login.ok`），扁平 `'login.ok'` 取不到值；未收录键用 `te()` 判存在后回落原文，保证新动作不显示成裸键名。
- **筛选下拉**：显示 `中文（原文）`；译文与原文相同时不重复括号。
- **详情**：`renderDetail()` 把 JSON 转中文键值列表——布尔 → 是/否、`latency_ms` → `N ms`、数组顿号连接、对象回落 JSON、非 JSON 的 detail 原样透传。

#### 4.4.5 顺带修复与质量门

- **KPI 卡片（dashboard）**：`n-space` 换 flex 行（`kpi__row--label/main/sub`）——naive-ui 的 vnode 克隆缺陷（v-if 模板与注释节点相邻时会复制内容）同一家族，早期「每点一次新建仓库就多一对按钮」也是它；结论：**底栏/统计行这类结构一律用 flex div，不用 `n-space`**。

**质量门**：

| 门 | 结果 |
|---|---|
| `pnpm run typecheck` | ✅ 0 error |
| `vitest`（33 例：smoke 20 + audit i18n 3 + 其余 10） | ✅ 全通过 |
| `NODE_OPTIONS="" pnpm run build` | ✅ built |
| 后端 `pytest` + `ruff` | ✅ 797 passed / 0 error |

### 4.5 第五轮：仪表板（主页）按 Duplicati `home` 复刻（2026-10-22 方案定稿，owner 逐项裁决）

> 对标 ngclient `app/home/`（`home.component.html` 535 / `.ts` 190 / `.scss` 202）+ 子件 `core/components/status-bar/`、`core/components/backup-progress/`。
> **本轮是前四轮中首个含后端契约变更的批次**（`last_duration_ms` 派生字段 + 单任务配置导出端点），红线按 §4.4.3 例外口径管理：DB 只走 `app/db/repositories/`（A1）、新增写操作埋 AUDIT_*（S6）、凭据零泄漏（S3）、时间语义只走 `utils/time.py`（C8）。

#### 4.5.1 对标基线：Duplicati 主页解剖

**四层结构**（`home.component.html`）

| 层 | 内容 | 行号 |
|---|---|---|
| ① StatusBar 全局条 | 暂停/恢复、Stop→Abort 二段、限速、远程连接态、全局进度条 | 1（子件 `status-bar.component.html`） |
| ② header 双行动卡 | Backups → `/add-backup`（Create）、Restores → `/restore`（Start）；整卡可点 + `keydown.enter/space` | 3–36 |
| ③ 「My backups」工具栏 | 视图切换按钮组（list/details）、时间类型菜单（Relative/Actual）、排序下拉（**仅 list 模式显示**） | 39–100 |
| ④ 主体 | list = `.backup` 卡片（名称+类型 chip / 指标区 / tags / 操作区）；details = `sh-table` 9 列 + 表头 `shSort` | 102–357 |

三个 `ng-template`：`#actions`（Start/Stop/Abort/Pause/Resume/编辑）、`#menu`（三点菜单，Operations/Advanced/Cleanup/Reporting 四分组 + `sh-divider`）、`#sortIcons`（funnel/asc/desc 三态）。

**状态流转**

| 状态源 | 字段 | 驱动 |
|---|---|---|
| `BackupsState` | `backups` / `backupsLoading` / `orderBy` / `timeType` / `startingBackup` | 数据源、加载占位、排序、时间口径、Start loading |
| `StatusBarState` | `runningBackupId` / `clientIsRunning` / `isResuming` / `runningTask` | 操作区在 Start ↔ Stop/Pause 间切换 |
| `ServerStateService` | `ProposedSchedule` → `scheduledTimes()` | 下次运行**优先取服务端提议**，回退 `Backup.Schedule.Time`（:92-106） |
| `SysinfoState` | 4 个 `hasV2*` 能力开关 | 三点菜单 Cleanup 分组动态显隐 |
| 本地 UI 态 | `viewMode`（**localStorage 持久化**）、`sortByColumn`、`loadingId`/`successId`、`#taskStopRequested` | 视图切换、排序、Verify 成功 2s 复位、Stop→Abort 二段 |

**关键交互链**：`startBackup` → 先 `resumeDialogCheck`；`stop()` → 置 `#taskStopRequested` → `isStopping` 生效 → 按钮变 **Abort**；`pauseResume` 运行中开 PauseDialog；`verifyFiles` → `loadingId` → `successId` 2s 后图标由 dots 变 check。

**响应式**：仅 `breakpoint(-milli)` 一档，三处生效——header 2→1 列（:26）、h2 工具栏横排→纵堆（:52）、`.backup` 横排→纵排且 `.text` 解除 `max-width:40%`（:112-126）；StatusBar 用 **desktop-actions / mobile-actions 双份 DOM** 切换。

#### 4.5.2 本项目差距（D1–D8）

| #   | 模版                      | 本项目现状（`pages/dashboard/index.vue` 454 行） | 处置                                                                                                                                                                                                                                                                                                                                                                 |
| --- | ----------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| D1  | 双行动卡                    | 无（顶部为 KPI 四格）                            | 新增行动卡行（→新建任务 / →恢复）                                                                                                                                                                                                                                                                                                                                                |
| D2  | StatusBar 全局条           | 无                                        | ✅ 已落地：`GlobalStatusBar`（全局进度 + **Stop/Abort 二段** + 限速只读；Abort 于 2026-10-23 补 `?force=true`）。**差异登记**：行卡不再嵌 `JobProgress`（同一作业开第二条 SSE 无收益），运行中进度只由本状态条承担                                                                                                                                                                                                           |
| D3  | 视图切换 + localStorage 持久化 | 仅卡片列表                                    | ✅ 已落地：SegmentedTabs 切换 list/details + localStorage 持久化；details = NDataTable 九列（含耗时/快照）。**加载态已补齐**（v1.24，P12 A7）：首载三行骨架（行高 92 对齐行卡）+ details 表格 `:loading`；分支序 error→骨架→空态→list/details，护栏 2 例（`page-render-smoke.spec.ts`）                                                                                                                                                                                                                   |
| D4  | 时间 Relative/Actual      | 仅绝对时间                                    | ✅ 已落地：relativeTime 纯函数 + 双语文案；工具栏时间口径切换同时驱动 list 卡片与 details 表格                                                                                                                                                                                                                                                                                                    |
| D5  | 排序下拉                    | 无                                        | ✅ 已落地：纯前端排序（name/last_run/next_run/status），仅 list 模式显示排序下拉                                                                                                                                                                                                                                                                                                         |
| D6  | 卡片指标（原计划 6 项 → **实际 4 项**：上次 / 下次 / 耗时 / 版本数；Source·Dest size 两项按 §4.5.4 替代口径不做 —— C8 2026-10-23 如实化）                | 仅 `last_run_at` / `next_run_at`          | ✅ 已落地：耗时 `durationText`（`last_duration_ms` → `HH:MM:SS`/`MM:SS`，null 不渲染）；size 走替代口径（4.5.4：版本数 chip 顶替，见 D7）                                                                                                                                                                                                                                                       |
| D7  | 类型 chip / 版本数 chip      | 无                                        | ✅ 已落地：版本数 chip = 该任务快照数（`snapshot_count`，null 不渲染，`task.snapshotCount` 双语）；仓库类型由既有 StatusTag/分类表达                                                                                                                                                                                                                                                                  |
| D8  | 三点菜单 7 项                | 编辑/删除                                    | ✅ 已落地：复用 P6 `TaskActionsMenu`（Operations:编辑/恢复 / Advanced:校验备份 / Danger:删除[运行中禁用]）；list 视图经 **TaskRowCard** emit（原文误记为 TaskCard——owner 2026-10-23 裁决以 §4.5.6 为准，D8 该处为错记，已更正）、details 视图经 `renderRowActions` 渲染菜单，两端均分发 edit/delete/restore/verify（verify = `createJob('check', { taskId })`，restore 跳 `/restore`）；Export 属 P2 新端点、Commandline/Database 隐藏（§4.5.4） |


#### 4.5.3 owner 裁决（2026-10-22）

| # | 裁决 |
|---|---|
| ① | **KPI 四格保留**（Duplicati 无 KPI 带，本项目保留在行动卡下方） |
| ② | 状态条按「**全局进度 + Stop/Abort 二段**」做，**砍 pause/resume + 限速入口**（2026-10-22 补充裁决：限速 = **任务级配置**，任务表单设置、创建时生效；状态条不设运行中入口，仅 limitText 只读展示） |
| ③ | Duration **补后端 `last_duration_ms` 派生字段** |
| ④ | Export **走后端端点**（单任务配置导出，复用 configTransfer 加密打包管线） |
| ⑤ | 视觉方案 A / A+ / B 三选一，**已拍板 A+**（见 4.5.8）。**分层口径**：形态 = A+（依据 v1.9 owner 指令「整个项目都要 A+ 视觉效果」）；强度 = 彻底（§4.6.4 裁决）——两者是不同维度 |

#### 4.5.4 能力可行性实证（本轮取证，含对首轮判断的纠正）

| 能力 | 首轮判断 | 实证结论 | 难度 |
|---|---|---|---|
| **限速** | ❌ 无 | ✅ **后端已实现（任务级，非仓库级）**——`restic.py:401-404` 在**备份进程启动时**拼 `--limit-upload/--limit-download`；DB 字段 `0001_init.sql:57-58`（policies 表）+ `0002:37-38`（backup_tasks 表）**均为任务级**（原「仓库级」标注有误，已纠正）。**运行中不可改**：restic CLI 无控制通道，flag 启动时读入 argv（Duplicati 实时限速靠自带 throttle 流包装层，本项目 shell out 无此层）→ 状态条运行中限速入口**不可行（降级，同 pause/resume）**；前端缺的是**任务表单输入控件**（TaskWizard 此前提交恒 0，2026-10-22 已补 limit_up/limit_down 控件） | 表单控件：低（接 UI）；运行中入口：不可行 |
| **Abort** | ❌ 无 | ✅ 可——`job_runner.py:366-401` `cancel()` 已是 SIGTERM → 宽限 `grace_seconds` → SIGKILL；Abort = `grace=0` 直杀 | 中（force 参数 + 审计埋点） |
| **Stop** | ❌ 无 | ✅ 现成 `cancelJob` 即等价 | 已有 |
| **Pause/Resume** | ❌ 无 | ❌ **真不可行**——restic 无暂停语义；`SIGSTOP` 在 Windows 无等价物，且挂起会长期持仓库锁 | 不做（唯一降级项） |
| **Verify files** | 待定 | ✅ `JobType` 含 `'check'`（`types.ts:4`），`createJob('check',{taskId})` 可用；E22 结果视图已落地（`routers/checks.py` + `api/jobs.ts:64`） | 低（只缺菜单入口 + 结果跳转） |
| **Duration** | 待定 | ✅ `jobs` 表有 `started_at`/`finished_at`（`0001_init.sql:79-80`）；TaskCard 唯一装配点 `_to_card`（`routers/backup_tasks.py:412-451`） | 低（派生字段，无迁移） |
| **per-task Size** | 待定 | ❌ 高成本——`routers/snapshots.py:12` 已有裁决「不逐快照跑 stats（N 次仓库往返不可控）」，per-task `stats --tag` 同理 | 不做，走替代（下） |
| **版本数 chip** | 待定 | ✅ 正对位「该任务快照数」——`listSnapshots(limit,offset,tag)` 已支持 tag 过滤（`api/snapshots.ts:17`） | 低（后端一次聚合，见 4.5.5） |
| **Export（单任务）** | 待定 | ⚠️ 现有 `exportConfig` 是**全局**打包（`settings/index.vue:651`，带口令），**无单任务维度** | 中（新端点） |
| **Commandline / Database** | 待定 | ❌ restic 无等价物 | 隐藏（模版中亦属高级/危险项） |

> **替代口径**：Source/Dest size 列改为「快照数 + 全局仓库容量」，缺失显示 `-`（Duplicati 缺值时同样显示 `-`）。

#### 4.5.5 数据契约变更（本轮后端改动，P1 两项 + D2 一项）

> P1：`last_duration_ms` / `snapshot_count`（派生字段，无迁移）；D2：`limit_up` / `limit_down`（任务级限速，纯抄 `task.limit_up_kibps` / `limit_down_kibps`，无查询、无迁移，仅供状态条 `limitText` 只读展示）。

| 项 | 内容 | 红线约束 |
|---|---|---|
| `TaskCardOut.last_duration_ms` | 该任务最近一次**终态** backup job 的 `finished_at - started_at`（毫秒）；无则 `null`。装配点 `_to_card`（`routers/backup_tasks.py:437`） | A1：须经 `app/db/repositories/` 新增查询，路由不直写 SQL；零新迁移（纯派生） |
| `TaskCardOut.snapshot_count` | 按 `task.tag` 分组的快照数。实现：在任务列表接口内**取一次快照列表**（**同款 30s 独立缓存** `SNAPSHOT_LIST_CACHE_SECONDS`：与 E30 状态缓存同一失效点、但各自可调——B6 2026-10-23 裁决保持独立）按 tag 分组计数；缓存不可用则 `null` | 同上；不得退化为 N 次 `stats` 调用（违反 `snapshots.py:12` 裁决） |
| `TaskCardOut.limit_up` / `limit_down` | 任务级限速（KiB/s，0=不限）。**纯派生**：直接抄 `task.limit_up_kibps` / `limit_down_kibps`，无新查询、无迁移。用途：状态条 `limitText` 只读展示（owner 2026-10-22 任务级裁决——运行中不可改，前端不设入口） | 同上；只读展示，不新增修改端点 |
| `POST /api/repos/{repo_id}/backup-tasks/{id}/export`（E36b，repo 作用域同 E34–E36；原文缺前缀已更正） | 单任务配置导出：复用 `config_transfer` 加密打包管线，口令入参同 `exportConfig`；返回加密包 | S6 埋 `AUDIT_CONFIG_EXPORT`；S3 响应不含凭据明文；i18n 双份 |

前端同步：`api/types.ts` 的 `TaskCard` 加 `last_duration_ms` / `snapshot_count` / `limit_up` / `limit_down` 字段、`api/backupTasks.ts` 加 `exportBackupTask()`、`i18n` 双语加键（`dashboard.statusBar.limit` / `unlimited` 等）。

#### 4.5.6 组件与文件规划

| 文件                                         | 职责                                                                        |
| ------------------------------------------ | ------------------------------------------------------------------------- |
| `components/dashboard/ActionCard.vue`      | 行动卡（插图 + 标题 + 描述 + **可选**右按钮 `actionLabel`；整卡可点 + Enter/Space）。主页与仓库页均**不传**右按钮——Duplicati add-new 卡本身无按钮，避免同一动作两个入口（C5，2026-10-23） |
| `components/dashboard/GlobalStatusBar.vue` | 全局条：当前作业进度（SSE）+ Stop/Abort 二段 + 限速只读展示（任务级派生；无运行中入口，owner 2026-10-22 裁决） |
| `components/dashboard/TaskRowCard.vue`     | **list 视图任务行**（名称 + 提示 chip + 快照数 chip / 指标区：上次成功备份于·下一次排程执行·用时 + **上次结果徽章（紧跟「用时」之后 = 原位置）** / 操作区**三键同形态**：开始（播放图标 + 文案，primary）·编辑画笔·三点菜单触发器，均 small + 有边框 default 档）——P7 起为 list 视图唯一形态，旧 `TaskCard` 已删除；2026-10-24 起：指标文案改「上次成功备份于 / 下一次排程执行 / 用时」（空值一律「—」）、**徽章改用短状态词**（成功/失败/中断…）以避免与指标文案撞成两个「上次成功」、**徽章位置最终裁决 = 指标行「用时」之后**（曾按 Duplicati 形态用 margin-left:auto 顶到行尾，结果紧贴右侧三键 → owner 裁决「保留左边的，不是按钮旁边的 / 以前上次成功是在用时后面」）、编辑由菜单提到行内画笔、三点触发器由 tiny quaternary 裸「⋯」改为 small 图标按钮、菜单条目补左图标（两端：details 表格操作列同构） |
| `components/JobLogDrawer.vue`             | 作业日志抽屉（E27 尾部逐级放大；jobs 页与主页任务行**共用**，A4 裁决 Q2a） |
| `components/dashboard/TaskActionsMenu.vue` | 三点菜单（Operations / Advanced / Reporting 分组 + divider；loading/success 图标态）  |
| `components/icons/HomeIcons.ts`            | **新增 ~30 个内联 SVG 图标**（见 4.5.9 风险 R-1）                                     |
| `pages/dashboard/index.vue`                | 重写：KPI 带 → 行动卡行 → 工具栏（视图/时间/排序）→ 双视图 → 空态/加载态                             |
| `utils/__tests__/relativeTime.spec.ts`     | 相对/绝对时间格式化单测                                                              |

#### 4.5.8 视觉方案三选一（**A+ 已拍板**：形态 = A+，强度 = 彻底）

| 对比项 | A 形似 | **A+ 推荐** | B 像素级 |
|---|---|---|---|
| 布局·交互·响应式 | 一致 | 一致 | 一致 |
| 控件皮肤（卡/chip/分段） | Naive 默认 | **5 处自定义**（行动卡 hover、卡片行边框/圆角/阴影、chip 无边框实心、分段选中态、菜单分组标题） | 全部重绘 |
| 30 个图标 | 自绘简版 | 自绘贴近 | phosphor 复刻 |
| 主题变量 | 1 套 | 1 套 + ~150 行 CSS | **2 套并行** |
| 暗色模式 | 自动跟随 | 自动跟随 | **需维护两遍** |
| 观感还原 | ~60% | **85–90%** | 95%+ |
| 工期 | 1.5–2 天 | ≈2 天 | +1–1.5 天 |

**B 的代价（两套主题治理体系）**：现有体系 = Naive UI 主题（`theme/` + `n-config-provider` + `--n-*`）；B 会新增第二套 ship-ui 风格 token（`--base-1..12` 色阶 / `--shape-*` / `--box-shadow-*`）。并行后果：① 换主题色要改两处，漏改即半站变色；② 暗色要手写（Naive 内置算法不覆盖第二套），漏一处即暗底黑字；③ 下拉/弹窗仍是 Naive 皮肤而卡片是另一套 → 同屏两种灰；④ `naive-imports.spec.ts` 只守 Naive 那套，第二套无人守，日后静默走样；⑤ 已有前车之鉴（Naive 只暴露 `--n-color`、`n-grid` 数字断点永不命中），两套 = 双份踩坑成本。

**A 与 A+ 的区别**：功能、交互、响应式**完全相同**，差别只在是否对 5 处高辨识度控件写自定义 CSS（约 150 行）。

#### 4.5.9 风险

| # | 风险 | 对策 |
|---|---|---|
| R-1 | **图标是隐性大头**：项目无 `@vicons`（`package.json` 仅 naive-ui/`@vueuse`/tanstack），图标为内联 SVG，需新绘 ~30 个 | 新增 `HomeIcons.ts` 集中管理；`naive-imports.spec.ts` 显式导入纪律同步适用 |
| R-2 | 30 个任务 × 快照计数若退化为 N 次仓库调用会拖垮列表 | 强制「一次快照列表 + 按 tag 分组」，走**同款 30s 独立缓存**（非复用 E30 常量，B6） |
| R-3 | `n-grid` 响应式只能用**命名档**（`1 l:2`），数字断点永不命中（P4 实测教训） | 行动卡/卡片行一律命名档；移动端断点走查复验 |
| R-4 | Abort 直杀可能留仓库锁 | Abort 后引导 unlock；走既有 `AUDIT_*` 埋点 |
| R-5 | 单任务导出若复用全局导出的覆盖语义会造成误导入 | 导出包自带 `kind=backup-task` 标记，导入侧按 kind 分流（随 B2 定稿） |

### 4.6 第六轮：A+ 由「仪表板单页」升级为「全站」（2026-10-22，owner 指令「整个项目都要 A+ 视觉效果」）

> §4.5.8 原定 A+ 为**仪表板页内 5 处 scoped 补丁**。owner 随后要求全站对齐，范围升级。本轮给出全站落地机制与强度选项。

#### 4.6.1 关键发现：全站 A+ 有现成单一源，不是复制粘贴

| 事实 | 证据 | 意义 |
|---|---|---|
| 设计令牌已有唯一源 | `frontend/src/theme/tokens.ts`（101 行）：`lightThemeOverrides` / `darkThemeOverrides` 两份 `GlobalThemeOverrides` | 改一处即全站；**不是第二套体系** |
| 挂载点唯一 | `App.vue:15-26`（`n-config-provider` + `ui.isDark` 切换） | 无散落主题配置 |
| 目前只覆盖 `common` | 仅 `borderRadius: 8px` / `borderRadiusSmall: 6px` 等，**无组件级覆盖**（Card / Tag / Button 均未设） | 圆角、卡片面、标签形态都可走覆盖层新增 |
| 暗色是独立调校 | `darkThemeOverrides` 非反色（§2.2） | 亮/暗两份同步改是**既有结构**，非新增债 |
| 现有自定义 CSS 规模 | repo 230 / settings 133 / dashboard 94 / VirtualTree 74 / TaskWizard 58 / audit 57 / jobs 53 / TaskCard 45 行，其余 ≤41 | 逐页走查面可控 |

#### 4.6.2 三层实现（自下而上，全部收敛到既有单一源）

| 层 | 内容 | 落地位置 | 生效范围 |
|---|---|---|---|
| **L1 主题覆盖层** | 圆角档、卡片边框/面/阴影、标签无边框实心态、按钮圆角 | `theme/tokens.ts` 新增**组件级覆盖**（`Card` / `Tag` / `Button` / `DataTable`） | **一处改、全站生效**，零复制粘贴 |
| **L2 共用形态层** | Naive 无对应组件的 5 类形态（行动卡、行卡片、分段控件、菜单分组、全局状态条） | `components/dashboard/*` 提升为全站共用组件 + `theme/skin.css` | 按页引用，逐步替换 |
| **L3 图标层** | 统一风格图标集（~30 个，自绘 SVG，统一描边与尺寸） | `components/icons/` 扩展 | 全站复用 |

#### 4.6.3 与方案 B 的界线（为什么全站 A+ 仍不是「两套体系」）

| 判据 | B | 全站 A+ |
|---|---|---|
| 色板来源 | 自建 `--base-1..12` 十二级色阶 | **仍用 tokens.ts 既有语义色**，不新建色板 |
| 生效机制 | 平行 CSS 体系 | **Naive 主题覆盖（既有机制）** + 少量共用类 |
| 暗色维护 | 新增手写暗色 | 沿用既有 `darkThemeOverrides`（本来就要维护两份） |
| 护栏 | 第二套无人守 | `naive-imports` / `i18n-parity` / `a11y-baseline` 照常覆盖 |
| 组件内部结构 | 依赖并重绘 | **不改 Naive 内部结构**，只覆盖主题变量与外层形态 |

#### 4.6.4 改动强度：**owner 2026-10-22 裁决 = 彻底**

| 档 | 内容 | 观感 | 风险 | 裁决 |
|---|---|---|---|---|
| 保守 | 只动 Card / Tag / Button 组件级覆盖，`common.borderRadius` 不动 | 卡片与标签像 Duplicati，输入框/下拉仍是 Naive 8px | 最低，几乎零回退 | — |
| 中等 | `common.borderRadius` 8→10px、`borderRadiusSmall` 6→8px；Card 12–16px、Tag 药丸无边框实心、按钮 8→10px | 全站协调一致，控件密度与间距不动 | 中，需逐页回归 | — |
| **彻底** | 圆角 + **控件密度** + **间距节奏** 全部按 Duplicati 重排 | 最接近模版（95%） | 高，已验收页面大面积重排风险 | ✅ **选定** |

**彻底档数值口径**（实施时以此为唯一标准，全部落在 `tokens.ts`，不落页面 CSS）

| 维度 | 现值 | 彻底档目标 | 落地方式 |
|---|---|---|---|
| 圆角 | `borderRadius` 8 / `borderRadiusSmall` 6 | `common` **12**、`small` **8**；Card **16**、Button **10**、Tag **999（药丸）**、Input/Select **10**、Modal/Dialog **16** | `common` + 组件级覆盖 |
| 控件密度 | 跟随 naive 默认 | 按钮/输入/表格行高取 **4 的倍数**（32 / 36 / 40）；表格行高 **40** | 组件级覆盖（`Button.height` / `Input.height` / `DataTable.thHeight/tdHeight`） |
| 间距节奏 | 页面各写各的（8/12/16 混用） | 统一 **4 / 8 / 12 / 16 / 24 / 32** 六档（对应模版 `p2r()` 节奏） | 新增 spacing 常量 + `skin.css` 工具类 |
| 卡片层级 | 1px 边框、无阴影 | 边框 + **三档阴影**（rest / hover / pressed），hover 阴影内缩过渡 100ms | `Card.boxShadow` 覆盖（亮/暗各一套值） |
| 标签 | 1px 边框 + 透明底 | **无边框实心浅底**（success/warning/error/primary/info 五态）+ 药丸圆角 | `Tag` 组件级覆盖 |

> 口径纪律：以上数值**只出现在 `theme/tokens.ts`（与 `skin.css` 的 spacing 工具类）**，任何页面不得硬编码；暗色值必须同批给，禁止只调亮色。

#### 4.6.5 风险与验收

| # | 风险 | 对策 |
|---|---|---|
| S-1 | `common.borderRadius` 一改，输入框/下拉/弹窗/表格**全部一起变圆**，可能超出预期 | 先定强度档；保守档可只走组件级覆盖 |
| S-2 | 已验收页面（repo 230 行 / settings 133 行自定义 CSS）随主题变化走样 | **逐页真机截图 + 桌面零漂移像素 diff**（沿用 §4.3 教训：曾因 `overflow-x:auto` 造成桌面 +8px 漂移） |
| S-3 | 暗色下边框/阴影若只调亮色会「暗底黑边」 | 亮/暗两份**同批改**，暗色单独走查 |
| S-4 | L2 形态层若被逐页复制粘贴会退化成第二套 | 必须是**共用组件 + 单一 skin 文件**，禁止页内各写一份 |

### 4.7 统一批次表 P1–P14（2026-10-22 融合定稿：**全项目唯一执行批次表**）

> 强度档 = **彻底**（§4.6.4）；范围 = **全站 A+ + 仪表板主页复刻 + 恢复模块复刻**。
> §4.5.7（B1–B6）、§4.6.6（S1–S4）、§4.8.6（R1–R6）三处旧批次**已并入本表并删除**，执行一律以本节为准。
> 排序原则：① 后端契约先行（零前端依赖，且前端要消费新字段）；② 令牌层是全局开关，必须在逐页校准之前；③ 图标与形态层是主页复刻的前置；④ **恢复模块的结构性改造（P8–P11）不等令牌层**，排在主页复刻之后、逐页重排之前；⑤ 逐页重排按**风险从低到高**分两批；⑥ 每批独立可提交、可回滚。

| 批次      | 内容                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | 依赖          | 验收门                                                                                                                                                 | 风险 / 对策                                                                                                          |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **P1**  | 后端：`TaskCardOut.last_duration_ms`（最近终态 backup job 的 `finished_at - started_at`）、`snapshot_count`（一次快照列表按 tag 分组计数，走**同款 30s 独立缓存** `SNAPSHOT_LIST_CACHE_SECONDS`；B6 裁决保持独立、失败按 5s 超时 + 指数退避）                                                                                                                                                                                                                                                                                                  | —           | `pytest` 全绿 + `ruff`；新增单测覆盖「无历史 job → null」「快照缓存不可用 → null」                                                                                         | A1 红线：须经 `app/db/repositories/` 新查询，路由不直写 SQL；禁止退化成 N 次 `stats`（`snapshots.py:12` 既有裁决）                          |
| **P2**  | 后端：`POST /api/repos/{repo_id}/backup-tasks/{id}/export`（E36b）单任务配置导出（复用 config_transfer 加密管线）+ 审计埋点 + 导出包带 `kind=backup-task` 标记                                                                                                                                                                                                                                                                                                                                                              | —           | `pytest` + 审计留痕断言                                                                                                                                   | S6 埋 `AUDIT_CONFIG_EXPORT`；S3 响应不含凭据明文                                                                           |
| **P3**  | 后端：`POST /api/snapshots/{id}/files/search` 快照内文件搜索（`{query, limit, cursor}` → `{items, next_cursor}`，游标与 E18 同口径）                                                                                                                                                                                                                                                                                                                                                                             | —           | `pytest` + 分页上限单测                                                                                                                                   | 上限沿用 `SNAPSHOT_FILES_MAX_LIMIT`（`constants.py:213`）；过滤下推，**禁止前端全量拉取再过滤**                                         |
| **P4**  | L3 图标集：`components/icons/` 扩展 ~30 个统一风格 SVG（统一描边 1.5 / 尺寸 20），含主页与恢复向导所需全部图标                                                                                                                                                                                                                                                                                                                                                                                                                  | —           | `typecheck` + `naive-imports` 护栏 + 目视                                                                                                               | 无图标库，全自绘；集中在单一文件，禁止散落                                                                                            |
| **P5**  | L1 令牌层**彻底档**：`tokens.ts` 圆角（common 12 / small 8 / Card 16 / Button 10 / Tag 999 / Input 10 / Modal 16）+ 控件密度（行高 32/36/40）+ **间距节奏 4/8/12/16/24/32** + 卡片三档阴影 + 标签无边框实心五态；**亮/暗同批改**                                                                                                                                                                                                                                                                                                          | P4          | 全站基线截图存档 → 改后**桌面零漂移像素 diff** + 亮/暗双模式截图                                                                                                            | 风险最高的一批：改前必须先存档全站基线；暗色值必须同批给（否则暗底黑边）；间距常量只落 `tokens.ts` + `skin.css`，页面零硬编码                                      |
| **P6**  | L2 共用形态层：行动卡 / 行卡片 / 分段控件 / 菜单分组 / 全局状态条 → 提升为**全站共用组件** + `theme/skin.css`                                                                                                                                                                                                                                                                                                                                                                                                                   | P5          | `typecheck` + 新 spec（分段选中态、菜单分组渲染）                                                                                                                  | S-4：必须是共用组件 + 单一 skin 文件，**禁止逐页各写一份**（否则退化成第二套）                                                                  |
| **P7**  | **仪表板主页复刻**（§4.5）：KPI 带保留 → 行动卡行 → 工具栏（视图切换 localStorage / 时间 Relative-Actual / 排序）→ list-details 双视图 → 空态/加载态 → 状态条（全局进度 + Stop/Abort 二段；限速仅只读展示）→ 三点菜单（Restore / Export / Delete / Verify / Log）                                                                                                                                                                                                                                                                                            | P1 P2 P4 P6 | `typecheck` + 新 spec（视图持久化、时间切换、排序三态、Stop→Abort 二段）——**2026-10-23 已补齐**：前三项在 `page-render-smoke.spec.ts`「P7 验收门」组，Abort 在 `GlobalStatusBar.spec.ts` | 限速 = 任务级表单配置（TaskWizard 控件，创建时生效；运行中入口已裁决砍掉）；Abort = `cancel()` grace=0；Verify = `createJob('check')`            |
| **P8**  | ✅ **已落地**（v1.19）：**恢复入口页 + 路由重排**（§4.8.5）：`/restore` 改造成入口页（快照行动卡 + 两张备选卡）；新增 `/restore/:id/{select-files,options,progress}`（**`:id` = 快照 id**，见 §4.8.5 裁决）                                                                                                                                                                                                                                                                                                                                   | —           | `typecheck` + **快照页 `?snapshot=` 直达入口实测**（V-1）                                                                                                      | 路由重排不得破坏 `snapshots/index.vue:118-123` 的预选直达                                                                     |
| **P9**  | ✅ **已落地**（v1.20）：**恢复向导外壳**（§4.8.5）：可跳步步骤条（键盘可达）+ `inactive` 态 + **满高**切换 + 离开确认。**全宽未落地**（内容区本就全宽，无切换对象，待 P14）                                                                                                                                                                                                                                                                                                                                                                               | P8          | `typecheck` + `flow-shell.spec.ts` 7 例（跳步 / inactive 双向不可达 / URL 同步 / 离开拦截）                                                                         | V-2：跳步前校验，未过步骤置 `inactive`（沿用模版语义）                                                                               |
| **P10** | ⏳ **部分落地**（v1.21）：**恢复步骤重组**——select-files：版本下拉补 size（R5）+ **搜索框**（结果计数 / Clear / spinner；**B3 契约** debounce 300ms 且关键词 ≥2 字符才发请求，P3 端点**已核验落地**）+ 文件树；options：**两分区**（恢复到哪 / 已存在文件）+ 页脚两端对齐。**未落地**：Metadata / 高级两分区（后端无字段支撑，需先加契约）；~~组件拆分（原计划随 P11）~~ **已于 v1.23 补做**                                                                                                                                                                                                                       | P3 P9       | `typecheck` + `flow-shell.spec.ts` 11 例（含 B3 三例：未到门槛不发 / debounce 未到不发 / 命中计数与勾选）                                                                   | 搜索框依赖 P3 端点——**已确认落地**（`POST /snapshots/{id}/files/search`，30s 缓存），故该项未顺延                                        |
| **P11** | ✅ **已落地**（v1.22）：**恢复进度页三卡 + 完成态圆环**（§4.8.5 R8/R9）——`n-grid 1 m:2` 三卡（进度卡含 `RestoreProgressRing` / **结果摘要卡** / 日志卡复用 `getJobLog`）+ SSE 终态驱动圆环与摘要。**「通知卡」裁决为结果摘要卡**：无通知记录数据源（`api/notify.ts` 仅配置），复用 `result`/`jobs.summary` 不新造端点（V-4 实质遵守）。**附带修复 A7 潜伏缺陷**：path-less 快照 `targetMode` 卡死 inplace（`watch(inPlaceDisabled)` 被真空初态吞掉）→ 改 `watch(selectedSnapshot)` 回落。**进度页组件已随 v1.23 拆出**（`progress.vue`）                                                                                      | P9          | `typecheck` + `flow-shell.spec.ts` 16 例（三卡 / 圆环三态 / 越界归一化 / **path-less 回落回归**）                                                                     | V-4：复用既有 job 日志 API，**不新造数据源**；无后端支撑不做占位卡                                                                        |
| **P12** | ✅ **已落地**（v1.24）：**逐页重排第一批（低风险）**——① 五页间距节奏对齐 4/8/12/16/24/32（原 10/6 为 P5 前遗留值，共 16 处：垂直分段 10→12 / 控件横隙 10→8 / 开关-标签对 6→8），无节奏外残留；② **A7 主页任务列表加载态**（owner 裁决从 P7 并入）：分支序 error→骨架→空态→list/details，骨架行高 92 对齐行卡、仅首载显示（不含 15s 轮询）、`role=status`+`aria-busy`+复用 `common.loading` 零新键；修复「首载闪空态引导卡」误导；护栏 +2 例；③ **reports 加载态**（表格 `:loading`，原首载空白）；④ **notifications 加载防护**（渠道列表数据到位才渲染 + 保存按钮首载禁用——全量字段 patch 防默认值回写）；⑤ **scroll-x 复核无溢出**（四表固定列和均 < 阈值）；⑥ 像素 diff 变化全部落在间距区（0.9–3.5%），A7 探针全过 | P5 P6       | 三步门分跑（typecheck / vitest 289 / build）+ 逐页像素 diff + A7 真机探针                                                                                          | 表格页 scroll-x 已复核（见左）；加载态类改动均走「挂起 query」单测 + 延迟响应真机双验证                                                            |
| **P13** | ✅ **已落地**（v1.25）：**逐页重排第二批（高风险）**——① repo（自定义 CSS 230 行）：模板 7 处 + CSS 11 处节奏对齐（vertical 10→12 / 横隙 6,10→8 / 紧凑对 2→4；`.repo__req` 2px 星标微调为字形级例外保留）；② settings（128 行 CSS）：卡片级 10→12 ×3、开关行→提示对 2→4 ×4（对齐全站 4px 惯例）、横隙与时区子块 6/10→8、sticky 页脚 padding 12px 0 4px；③ TaskWizard + 步骤组件：wizard__body 14→16、字段对 2→4 ×11、行距 6/10→8 ×9；④ bootstrap 20→24；login 零改动（本就在节奏内）                                                                                                                             | P12         | 三步门分跑 + 逐页像素 diff + **向导 5 步与 ConfirmGate 真机断言**（`.e2e/p13-wizard-probe.cjs`：五步全流程 + 删除三门全流程，零 pageerror）                                           | 自定义 CSS 最厚的两页逐块校准完成；门禁未因重排失效（探针实证）                                                                               |
| **P14** | ✅ **已落地**（v1.26）：**全站回归**——零代码改动纯验证批。① **四象限截图**：8 页 × 亮/暗 × 中/英 = 32 组合零 pageerror，**四象限全站零未翻译裸键**（内置扫描器：`段.键` 形态 ≥2 段视为缺键）；② **桌面零漂移**：HEAD 连跑两次 8/8 页**逐像素一致**（bbox=None，截图管线确定性证明）；③ **移动端 4 视口**（1440/960/768/390）× 8 页 = 32 组合**零横向溢出**，390 向导步骤条 `scrollWidth 503 > clientWidth 308`（5 步全可达，与 §4.3 记录同值）；④ **恢复模块响应式**：`/restore` 4 视口 ok + flow select-files 768/390 零溢出零报错；⑤ **E2E 冒烟**：A7 加载态探针 + P13 向导五步/ConfirmGate 三门探针复跑 PASS                                                  | P13         | 全绿；移动端 4 项断言（参照 §4.3 口径）                                                                                                                            | 沿用 §4.3 教训（`overflow-x:auto` 折叠 margin 致桌面 +8px 漂移）——本批以「连跑两次逐像素一致」作零漂移实证；三步门终跑被 owner 取消（P13 已全绿且本批零代码改动，可随时补跑） |
| **P15** | ✅ **已落地**（v1.27）：**全站图标统一**（owner 2026-10-23 四项裁决：①重绘 ②合并 ③全上 ④留库）——① **三文件合并**：HomeIcons + MenuIcons + BackendIcons → 单一 `components/icons/icons.ts`（57 图标 + `BACKEND_ICONS` 映射 + `backendIcon`，旧三文件删除、9 处消费方 import 改向，`IconPlusCircle` 跨文件重复去重）；② **24 网格重绘为 P4 标准**：原 MenuIcons 16 + BackendIcons 8 按 20×20 / 描边 1.5 重绘（几何 ×5/6 精确变换、arc 标志位保护；旧 24 网格画法淘汰，渲染描边 1.25px→1.5px）；③ **全站图标化**：9 个零图标页面补 35 处标准图标（jobs/snapshots/audit/reports/notifications/settings/taskWizard/login/bootstrap；规则 = small 及以上按钮用 `#icon` 插槽、tiny 紧凑按钮保持纯文字）；④ **留库不删**：全量导出保留（含 IconPlay/IconSearch/IconFilter 等暂无消费方的留库资产），`icons.spec.ts` 登记表 59 项 + 全量 20 网格护栏；⑤ **预览页**：`frontend/icon-preview-p15.html`（57 图标分组 + P15 徽标 + 尺寸档 + 暗底带，可重生成）。验证：三步门分跑（typecheck / **291 passed** / build）+ 像素 diff（全站 0.09–1.1% 且同尺寸——图标化不改变布局高度）+ 探针复跑（A7 / P13 向导与三门）+ 移动端 8 页 × 4 视口零溢出 | P14        | 三步门 + 像素 diff + 探针 + 预览页目视 | 重绘用坐标精确变换（非手绘），arc 标志位单独保护防弧向损坏；多行内联脚本在 Git Bash 有引号/路径坑，一律写 .cjs |
| **P16** | ✅ **已落地**（v1.34）：**恢复向导第二步「恢复选项」按 Duplicati options 复刻**（§4.9，owner 2026-10-25 五裁决）——① 外壳去 `n-card` 改「stepper + 居中列」（options 500px / select-files·progress 1000px 双档，≤960 断点 padding 0 32，`box-sizing: border-box` 保证断点内列宽仍 500）；② 三卡片：恢复到哪（raised 单选卡：原位置默认 / 选择位置 + 原位目标下拉内嵌）/ 已存在文件（A7 overwrite 布尔 + **E20 预览并入**，D4=A）/ 页脚两端（返回 / 提交 raised primary）；③ `RestoreOptionCard.vue` 折叠卡（复刻 ToggleCard 动画与 aria）+ `RestoreTargetPicker.vue` 路径选择器（**D2 树双模式**：`runtime.restoreRoot` 单一事实源 + VirtualTree + mount chips + 浏览树/手输二模）；④ 模式切换 effect + 跨 OS 确认 + customTarget 越界前端双校验；⑤ icons.ts 增 `IconArrowRight`（登记 60→61）。**D1 Metadata / D3 高级选项取消**（无后端支撑 / 红线 S2，留档 §4.9.6） | P15        | **全绿**：typecheck 干净 / 全量 **41 文件 403 passed**（新增 3 spec 19 例）/ build ✓ 2m08s；真机探针 `.e2e/p16-options-mobile.cjs` **pass**（1440/960/768/390 四视口零横向溢出、零 pageerror；1440 列宽 500px；两张折叠卡 + 两行单选卡 + 双按钮页脚齐备） | 复用 VirtualTree / runtime store / api/fs / icons.ts / ConfirmGate（S-4 禁止重复造轮子）；**零后端改动**（target 白名单已允许 /restore 任意子路径，服务端恒建时间戳子目录守 G3） |
| **P17** | ✅ **已落地**（v1.35）：**host 形态恢复白名单派生 + 原位换算修复**（§4.9.8，owner 2026-10-25 裁决 **Q1=A + Q2 同批**）——① 后端三条白名单统一派生：新增 `restore_allowed_prefixes`（docker `/restore` / host `/`）与 `inplace_restore_allowed_prefixes`（与备份源同源），**删除** `RESTORE_ALLOWED_PREFIXES`/`INPLACE_RESTORE_ALLOWED_PREFIXES` 常量；② runtime 增 **`inplace_root`**（换算前缀），`restore_root` 改生效值；③ 前端 `inplaceTargetFull` 按 `inplaceRoot` 换算（host 恒等 + 幂等保护）——修掉 host 模式 `/sources/sources/...` **双前缀 403** 实拍缺陷；④ 选择器 host 模式 **deny 拦截** + 树根随 `restoreRoot` 天然双模式；⑤ deny 命中原样 400（不降级 403）；⑥ 红线 S1/S7 与 AGENTS 同步「三条派生」 | P16        | **全绿**：后端 **1064 passed**（+8 host 用例）+ ruff；前端 typecheck + 全量 **41 文件 / 406 passed** + build ✓ 1m45s；`test_restore_host_mode.py` 含**双前缀反证 403** | 行为变更如实记录：host 模式恢复可写主机任意非保护目录（仍受时间戳子目录 + 快照 paths 交集双约束）；docker 形态**零变化** |

**批次关系说明**

- **P1 / P2 / P3 与前端零耦合**，可与 P4–P6 并行推进（后端与前端不互相阻塞）。
- **P5 是分水岭**：在此之前页面外观不变；P5 一落地全站同时变化，P12/P13 是"变化后的逐页校准"，期间存在中间态（可接受，每批可提交）。
- **P7 排在 P6 之后**：主页复刻要用 P6 的行动卡/行卡片/分段/菜单分组，否则会写出页内私有版本（违反 S-4）。
- **P8–P11（恢复模块）是结构性改造，不等 P5 令牌层**，可与 P5/P6 并行；但其视觉打磨须在 P5/P6 之后复跑一遍（彻底档改圆角与间距）。
- **P14 不是可选项**：彻底档动了间距节奏，移动端必然受影响，4 视口走查是硬性验收。

**通用护栏（每批适用，沿 v1.x）**

- 既有 spec 原样通过；`i18n-parity` / `naive-imports` / `page-render-smoke` / `a11y-baseline` 四道静态护栏不破；
- 文案全走 i18n 双语；危险操作埋点由后端既有 `AUDIT_*` 承担，**前端零新增端点**（唯一例外 P2 的单任务导出）；
- `ConfirmGate` / `StatusTag` / `PathBreadcrumb` / `PasswordEntryModal` 只复用、不换接口。

### 4.8 第七轮：恢复模块按 Duplicati 恢复复刻（2026-10-22 方案定稿，owner 三项裁决）

> 对标 ngclient `app/restore/`（入口页 + from-config）与 `app/restore-flow/`（向导：select-files / options / restore-destination / restore-encryption / restore-progress + `restore-flow.state.ts`）。MCP 图谱索引 `duplicati-ngclient`（2670 节点 / 7897 边；`restore-flow` 作用域 100 节点 / 215 边，热点 `#submitRestore` / `SelectFilesComponent.next` / `#repairVersion` / `#navigateToNext` / `#setTaskResult`）。
> 视觉沿用 §4.6.4 已裁决的**全站 A+ 彻底档**；本模块批次挂在 §4.7 的 **P6 之后**（依赖 P4 令牌层 + P5 形态层），但结构性改造（R-1/R-2）**不等令牌层**，可先行。

#### 4.8.1 对标基线

| 层 | 形态 | 证据 |
|---|---|---|
| 入口页 | 标题「Where do you want to restore from?」+ 备份列表行动卡（48px 插图＋名称＋`SourceSizeString`/`BackupListCount`）+ `sh-divider`「Or without a local configuration」+ 两张卡（直接恢复 / 从配置恢复）；`@defer` 加载占位 | `restore.component.html:1-76` |
| 向导外壳 | `app-status-bar` + `sh-stepper`（`routerLinkActive` 高亮，可点击跳步）+ `router-outlet`；常规 2 步 / 从文件 4 步（后两步初始 `inactive` = `pointer-events:none + opacity .5`） | `restore-flow.component.html:1-34`、`.scss:44-47` |
| 布局信号 | `isFullWidthPage`（progress/select-files → 内容宽 500→**1000**）、`isSelectFilesPage`（`.fill-height` flex + `overflow:hidden` 撑满文件树） | `restore-flow.component.ts:35-45`、`.scss:5-24` |
| 状态中枢 | 三张表单共享 + `getBackup()` 错误分诊（加密→跳 encryption、文件夹缺失/空→跳 destination、`DBPathExists=false`→提示修库）+ filesets 重试 2 次/间隔 1s + `submit()` 组装（paths 按 `\0` 切、目录补 `*`；`overwrite`；`skip_metadata=!includeMetadata`）+ `exit()` 离开确认 | `restore-flow.state.ts:113-361` |
| 步骤 | select-files（版本下拉含 size/partial/label + 搜索 + 文件树）、options（四个 `toggle-card` 分区）、destination（目标 URL + `app-test-url` + 覆盖 URL 对话框）、encryption（密码框 `autocomplete="disabled"`）、progress（**三卡**：进度/通知/日志 + 完成态 SVG 圆环） | 各步骤 `.html` |
| 响应式 | 仅 `-milli` 一档（`section` 加 `padding: 0 32`），与主页同 | `restore-flow.component.scss:36` |

#### 4.8.2 本项目差距（R1–R10，对照 `pages/restore/index.vue` 682 行单页三步）

| # | 差距 | 可行性 |
|---|---|---|
| R1 | 无恢复**入口页**（导航直接进向导） | ✅ 低 |
| R2 | 无「从备份文件恢复」 | ⚠️ 中（见裁决） |
| R3 | 无「从配置恢复」 | ✅ 低（`importConfig` 已有） |
| R4 | `n-steps` 只读、无跳步 / 无全宽满高 / 无离开确认 | ✅ 低 |
| R5 | 版本下拉缺 size/partial/label；快照语义等价 | ✅ 低 |
| R6 | 文件树**无搜索**（`listSnapshotFiles` 只有游标） | ⚠️ 中（见裁决） |
| R7 | 目标/覆盖/文件混在步骤 1，非四分区 | ✅ 低（重组） |
| R8 | 进度页只有 `JobProgress`，无三卡 | ✅ 低（通知/日志 API 已有） |
| R9 | 无完成态圆环 | ✅ 低 |
| R10 | 无离开确认 | ✅ 低 |

#### 4.8.3 owner 三项裁决（2026-10-22）

| # | 裁决 |
|---|---|
| ① | **R6 文件树搜索 → 新增后端搜索端点**（不做前端伪过滤） |
| ② | **R2 只做到「选已有仓库 + 输密码」**——Destination = 选已有仓库（复用仓库列表）；Encryption = 仓库密码（复用 `PasswordEntryModal` / passfile 引导）。**不做**临时备份、`copytotemp`、目标 URL 直填 |
| ③ | 批次位置**按建议**：R1–R6 挂在 §4.7 的 P6 之后，写入本节 |

#### 4.8.4 契约变更（后端，仅一项）

| 项 | 内容 | 红线 |
|---|---|---|
| `POST /api/snapshots/{snapshot_id}/files/search` | 快照内文件名/路径搜索；请求 `{ query, limit, cursor }`，响应 `{ items, next_cursor }`——游标语义与 E18 `listSnapshotFiles` **同口径**；复用既有文件树服务，不新增数据源 | A1（经 `app/db/repositories/` 或既有 service）；S1（路径只出 `display_path`）；分页上限沿用 `SNAPSHOT_FILES_MAX_LIMIT`（`constants.py:213`） |

其余全部**零后端改动**（入口页/外壳/分区/进度页均为纯前端重组）。

#### 4.8.5 组件与文件规划

| 文件 | 职责 |
|---|---|
| `pages/restore/index.vue` | ✅ **已改造为入口页**（P8，R1）：标题「从哪里恢复？」+ 当前激活仓库**快照**行动卡 + 分隔线「或者不使用本地配置」+ 两张备选卡（从配置恢复 → 深链 `/settings#sec-transfer`；从其他仓库恢复 → `/repo`）。**偏离原计划**：原「备份/仓库列表」改为「快照列表」——后端不支持按仓库过滤，见下行 `:id` 裁决 |
| `pages/restore/flow/index.vue`（新） | ✅ **P8 迁入 + P9 改造完成**：P8 `git mv` 零逻辑改动迁入；P9 改造为**向导外壳**——可跳步步骤条（`RestoreStepper`，inactive 双向不可达）+ 步骤↔路由同步 + 满高 + 离开确认（R4/R10）。**全宽未落地**：内容区本就无 `max-width`，无切换对象（待 P14） |
| `pages/restore/flow/select-files.vue`（新） | ✅ **已拆出**（v1.23）：快照下拉（P10 补 size，R5）+ **搜索框**（R6，B3 契约与 debounce 定时器随组件内聚）+ 文件树多选；快照查询/默认选中/勾选集合留父级（确认门与提交共用），组件经 props 读、emit 回写 |
| `pages/restore/flow/options.vue`（新） | ✅ **已拆出**（v1.23）→ ✅ **P16 已按 Duplicati options 重写**（v1.34，§4.9）：三折叠卡（恢复到哪 / 已存在文件 + E20 预览并入）+ 页脚两端；**Metadata / 高级两分区 owner 2026-10-25 裁决取消**（D1 无 restic 参数支撑、D3 触红线 S2，留档 §4.9.6）；预览 fetch 状态留父级（确认门插槽共用），列定义随组件内聚 |
| `pages/restore/flow/progress.vue`（新） | ✅ **已拆出**（v1.23）：三卡（进度 / 结果摘要 / 日志）+ 完成态圆环（`RestoreProgressRing` 共用组件）；日志拉取与 summary 计数解析随组件内聚，SSE 终态事件上抛父级落 result |
| `router/index.ts:77-80` | ✅ **已重排**（P8）：`/restore` → 入口页；`/restore/:id/{select-files,options,progress}` → 向导（`flow/index.vue`，三子路由暂同指向，P9 起按路由分步）。**`:id` 语义裁决 = 快照 id（非仓库 id）**：E17 `listSnapshots` 走 `current_repo_id` **激活仓库模型、不支持按仓库过滤**，仓库 id 无法用于过滤快照 |

> ⚠️ 兼容约束：`snapshots/index.vue:118-123` 的「恢复此快照」走 `{ name: 'restore', query: { snapshot } }`，路由重排后**必须保持该入口可用**（预选语义不变）。
> ✅ 已守护（P8）：入口页把 `?snapshot=` **原样转发**到 `/restore/:id/select-files`（`router.replace`），向导预选源扩为 `?snapshot=` 与路由 `:id` 二选一，语义不变；由 `pages/restore/__tests__/entry.spec.ts` 4 例锁死（含真实路由表 name→path 解析）。

> **批次**：本模块批次已并入 §4.7 统一批次表——**P3（搜索端点）/ P8（入口页+路由）/ P9（向导外壳）/ P10（步骤重组）/ P11（进度页三卡）**，响应式与 i18n 并入 **P14**。依赖判断：P8–P11 是结构性改造，不等 P5 令牌层；视觉打磨在 P5/P6 后复跑。

#### 4.8.7 排除项（restic 语义无对应，不做）

| 模版能力 | 不做理由 |
|---|---|
| extendedDataType：o365 / gsuite / diskimage 远程目标 | restic 无该概念；恢复目标只有原目录与 `/restore` 两种白名单 |
| Hyper-V 注册（`register-restored-items`、虚拟根 `\\duplicati\hyperv\`） | 无对应 |
| Check permissions 按钮 | 无对应 |
| 临时备份 `copytotemp` | 裁决②已排除 |
| 目标 URL 直填 + `app-test-url`（`containsBackup` 探测） | 裁决②简化为「选已有仓库」 |
| `passphrase` 逐备份加密 | 本项目是仓库级密码（`PasswordEntryModal`） |

#### 4.8.8 风险

| # | 风险 | 对策 |
|---|---|---|
| V-1 | 路由重排破坏快照页直达入口 | R-1 验收必须实测 `?snapshot=` 预选 |
| V-2 | 步骤条可跳步会跳过必填（如未选快照） | 跳步前校验，未过步骤 `inactive`（沿用模版 `.inactive` 语义） |
| V-3 | 搜索端点若全量扫快照会很慢 | 游标分页 + `SNAPSHOT_FILES_MAX_LIMIT` 上限；后端过滤下推，禁止前端全量拉取再过滤 |
| V-4 | 三卡里的通知/日志若新造拉取逻辑会重复 | 复用既有 notifications 与 job 日志 API，不新增数据源 |

### 4.9 第八轮：恢复向导第二步「恢复选项」按 Duplicati options 复刻（2026-10-25 方案定稿 + 当日落地，owner 五项裁决）

> **状态：✅ 已落地（v1.34）**——P16 五子批全部完成，三步门 + 真机探针全绿（详见 §4.9.5 与 §4.7 P16 行）。

> 对标 ngclient `app/restore-flow/options/`（`options.component.ts/html/scss` + `restore-option-defaults.ts`）与 `core/components/toggle-card/`、`core/components/file-tree/`。MCP 图谱索引 `D-agent-duplicati-ngclient-src`（2026-10-25 新建：2670 节点 / 8842 边；restore-flow 作用域 100 节点 / 216 边，热点 `OptionsComponent.submit` / `RestoreFlowState.#submitRestore` / `#selectDefaultRestoreModeEffect`）。视觉沿用 §4.6.4 全站 A+ 彻底档；复用纪律遵循 S-4 与 v1.31 FilterBar 收敛教训——**能复用必复用，禁止重复造轮子**。

#### 4.9.1 owner 五项裁决（2026-10-25）

| # | 裁决 |
|---|---|
| D1 | Metadata 分区（Restore file metadata / Restore read/write permissions 两 toggle）**不做**——restic restore 无 `--skip-metadata` / `--restore-permissions` 参数，渲染即占位死控件（v1.21 同结论，P7 D8 同类教训） |
| D2 | Pick location 路径选择器**走《源数据过滤与树双模式设计方案》§3 树双模式逻辑**——部署形态自动检测决定浏览根（单一事实源 `runtime.restoreRoot`，后端运行时下发），复用 `VirtualTree` + 挂载点快跳 chips；交互为 Duplicati FileTree 的「浏览树 / 手动输入」双模式 |
| D3 | Advanced options（自由 restic 参数列表）**不做**——红线 S2「restic 参数只在 `ResticCmd.build` 白名单拼装」，自由透传即 Blocker；将来若要开放只能走白名单增量（另立需求） |
| D4 | E20 覆盖预览区**并入「已存在文件」卡片**（A）——预览本就是「已存在文件」的论据，不删不搬家 |
| D5 | 向导外壳**全向导去 `n-card`**，改 Duplicati「stepper + 居中列」：options 列宽 **500px** 居中；select-files / progress 满宽 **1000px**（对齐模版 `isFullWidthPage` 双档） |

#### 4.9.2 对标基线（Duplicati options 实测解剖）

- **结构**：`form` → `h3.title-30` 标题 → 5 张 `app-toggle-card`（卡片间距 24px；header padding 16；内容折叠 `max-height` 0↔1000px、250ms linear；caret 旋转 180°、200ms）→ 页脚两端对齐（左 Go back / 右 Submit raised primary + loading）。
- **卡片①「Where do you want to restore files to?」**（禁折叠）：`sh-radio raised primary` 单选卡 Original location（默认）/ Pick location；`app-file-tree` 始终渲染、非 Pick location 时 `disabled`；「Browse path / Manually type path」二分段 + 目录树（层缩进 0.25rem）+ Path 输入 + 错误 `sh-alert`。
- **卡片②「How do you want to handle existing files?」**（禁折叠）：单选 Overwrite / Save different versions with timestamp in file name（默认 saveTimestamp）。
- **卡片③「Metadata settings」**（可折叠、默认收起）→ **D1 取消**；**卡片④ Hyper-V（条件）/ 卡片⑤ Advanced options（条件，隐藏 8 个内置项）**→ **D3 取消 + §4.8.7 已排除**。
- **提交前门**：跨 OS 原位置恢复 ConfirmDialog（所选文件分隔符与系统 `DirectorySeparator` 不符时）；自定义远端路径为空时阻塞对话框（o365/gsuite 分支，不纳入）。
- **状态流**：`optionsForm`（5 字段信号）→ `toSignal(valueChanges)` → computed 派生 UI 显隐 → effect 模式纠正（`#selectDefaultRestoreModeEffect`：数据类型变化即切模式并重置 handleExisting）→ `submit()` 前置门 → `RestoreFlowState.submit`（POST restore）→ navigate progress；默认值经 backup/server settings 解析（restore-permissions / skip-metadata，每 backupId 一次）。

#### 4.9.3 本项目落地形态与复用面（不重复造轮子）

| 需件 | 复用 / 新建 | 说明 |
|---|---|---|
| 折叠卡容器 | **新建** `components/restore/RestoreOptionCard.vue` | 复刻 ToggleCard：v-model 展开态 + `disableToggle` 强制展开 + caret 旋转 + max-height 过渡 + `aria-expanded`；图标取 icons.ts 现有 `IconChevronDown`（旋转 180°，零新增图标） |
| raised 单选卡 | `options.vue` scoped `.restore__opt-card` | 单页消费，**不升共用组件**（S-4 判据：非多页复用的形态不进 `components/`）；整行可点 + 选中主色描边/浅底 + 键盘可达 |
| 路径选择器（树 + 手输双模式） | **新建** `components/restore/RestoreTargetPicker.vue` | 复用：`VirtualTree.vue`（`select-dir` + highlight）、`stores/runtime.ts`（`restoreRoot` / `denyPrefixes` / `deployMode` 单一事实源）、`api/fs.ts` `browseFs`（E33；`/restore` 不在 `_MOUNT_PREFIX_DENY`）、mount chips 范式（`SourcesPicker.vue:294-314`）、icons.ts 图标 |
| 500px 居中列 + 满宽切换 | `flow/index.vue` 外壳改造 | 去 `n-card`；`.restore-flow__section` 双档 `max-width`（500/1000），≤960 断点 `padding: 0 32`（对齐 §4.3 移动端惯例，不自定 -milli 档） |
| 页脚两端 | `flow/options.vue` | 复用 TaskWizard `wizard__footer` 范式（`n-space justify="space-between"`）；返回用 `IconArrowLeft`，提交用**新增** `IconArrowRight`（20×20 / 描边 1.5，IconArrowLeft 镜像；icons.spec 登记 60→61） |
| 确认门 | `ConfirmGate`（既有） | 接口不变；D4 预览并入卡片②后，门内预览插槽保持既有口径 |
| 跨 OS 确认 | `window.confirm` 轻量确认 | 复刻 Duplicati ConfirmDialog 文案结构；**不进** ConfirmGate（无关键词语义，避免与恢复确认门混淆）；沿用 R10 离开确认的 `window.confirm` 模式（不新造弹窗组件，vi.stubGlobal 可测） |

#### 4.9.4 第二步结构定稿（三卡片 + 页脚）

**卡片①「恢复到哪」**（禁折叠）：

- 单选卡 A「原位置」（默认；`inPlaceDisabled` 时置灰 + 原因 alert，既有 A7 语义）→ 选中时内嵌原位目标下拉（既有，限该快照 paths，后端双重锁兜底）；
- 单选卡 B「选择位置」→ 选中时展开 `RestoreTargetPicker`：浏览树 / 手动输入二模，树根 = `runtime.restoreRoot`（容器形态）；目标为 `/restore` 子树内路径，**服务端仍建 `<stem>-<stamp>` 时间戳子目录**（守 PRD G3），UI 明示该语义；
- **模式切换 effect**（watch `restoreFrom` 数据本身，避 P11 真空初态坑）：切 B → `customTarget` 重置为 `restoreRoot` 且预览失效；切 A → 清空 `customTarget`。

**卡片②「已存在文件」**（禁折叠）：

- 原位置：A7 overwrite 布尔勾选（默认不勾 = never）+ **E20 预览表并入**（统计行 / 策略行 / 截断提示 / 虚拟滚动表 / 加载更多，既有口径整体搬迁）；
- 选择位置：零覆盖说明文案（服务端时间戳子目录，不涉及覆盖选项）。

**卡片③ Metadata / 高级**：D1/D3 取消，不渲染（留档防回潮，见 §4.9.6）。

**页脚**：左「返回」/ 右「提交」（raised primary + loading → 开 ConfirmGate；原位流需先有预览结果才可开，既有 `canOpenGate` 不变）。

#### 4.9.5 实施批次（P16 五子批，每批独立可提交可回滚）

| 子批 | 内容 | 依赖 | 验收门 |
|---|---|---|---|
| P16-0 | icons.ts 增 `IconArrowRight` + 登记表 60→61；新建 `RestoreOptionCard.vue`（含 spec：折叠 / 强制展开 / aria-expanded） | — | typecheck + 新 spec |
| P16-1 | `flow/index.vue` 外壳去 n-card + 500/1000 双档列宽 + ≤960 断点；`options.vue` 三卡片布局重写 + 页脚两端 | P16-0 | typecheck + 既有 spec 回归 |
| P16-2 | `RestoreTargetPicker.vue`（双模式 + 越界校验 + mount chips）+ `restoreFrom` / `customTarget` 状态重构（替 `targetMode` 的 timestamp 分支）+ 模式切换 effect + 跨 OS 确认 | P16-1 | 新 spec（双模式切换 / 越界 / 禁用态 / effect 重置回路） |
| P16-3 | i18n zh-CN / en-US 同步新增（i18n-parity 护栏）；更新 `flow-shell.spec.ts` ④ 按钮文本断言与 `restore-fallback.spec.ts` ①② 文本断言 | P16-2 | i18n-parity + 全量 |
| P16-4 | 三步门分跑（typecheck / vitest 全量 / build）+ 真机走查（390 视口 options 页零溢出）+ 像素 diff（外壳去卡片位移核对） | P16-3 | **✅ 全绿**：typecheck 干净；全量 **41 文件 / 403 passed**；build ✓ 2m08s（2888 modules）；探针 `.e2e/p16-options-mobile.cjs` pass（4 视口零溢出 + 零 pageerror + 1440 列宽 500px + 双按钮页脚；390 截图存档 `.e2e/p16-options/`） |

**落地取证（2026-10-25）**：

| 证据 | 结论 |
|---|---|
| `RestoreOptionCard` / `RestoreTargetPicker` / `options-step` 三个新 spec（19 例） | 折叠动画与 aria、二模切换、越界三段（根外/非绝对/`..`）不 emit、模式↔控件挂钩、页脚 loading/禁用、卡片标题齐备全过 |
| `flow-shell.spec.ts` ⑨⑩（新增 2 例，该文件 17 例） | 跨 OS 确认（`\` 分隔符 + 原位置）confirm 取消留步 1、确认放行进 2；选择位置 effect 复位 customTarget → 提交链 target = `/restore` |
| `restore-fallback.spec.ts` ①② | path-less 回落「选择位置」、有 paths 保持原位置的 A7 语义零回退（文本断言原样命中） |
| 探针首轮三处真缺陷 | ① `RestoreOptionCard` 漏 import `NCard`、`RestoreTargetPicker` 漏 import `NInput` → 未解析组件按原生元素渲染、**named 槽（#header）整片丢失 + 输入框不出**（Vue warn 抓到；vue-tsc 查不出，`naive-imports` 全量跑本可拦——当时只跑定向 spec 未覆盖，教训已记）；② ≤960 断点 `content-box` 下 section 实测 564px（比 Duplicati 宽 64px）→ 补 `box-sizing: border-box` 后 500px 恒定；③ 探针自身 `**/api/**` 通配误伤 Vite `/src/api/*.ts` 模块（MIME 严格校验白屏）→ 改 `/api/` 前缀谓词 |

#### 4.9.6 排除项（含本轮取消留档，防回潮）

| 项 | 理由 |
|---|---|
| Metadata 两 toggle（D1） | restic restore 无对应参数；占位死控件违反诚实原则（v1.21 同结论） |
| Advanced options 自由参数（D3） | 红线 S2；若要开放只能走 `ResticCmd` 白名单增量（另立需求） |
| Hyper-V 卡 / o365 / gsuite / diskimage / Check permissions | §4.8.7 已排除 |
| Create folder 弹窗（Duplicati `enableCreateFolder`） | 后端无 mkdir 端点；不造假入口 |
| 直接落所选目录（无时间戳子目录） | 违反 PRD G3 误操作零事故；服务端恒建时间戳子目录 |
| 自定义 `-milli` 断点 | 项目移动端断点统一 960（§4.3 惯例） |

#### 4.9.7 风险

| # | 风险 | 对策 |
|---|---|---|
| V-5 | 外壳去 n-card 牵动步骤 1/3 布局（fill-height / 满高依赖卡片容器） | 满高信号保留（步骤 1 `min-height: 60vh`）；像素 diff 核对三步位移 |
| V-6 | `customTarget` 引入后越界路径要到后端才 403 | 前端双校验（`restoreRoot` 前缀 + 绝对路径 / 无 `..` + **deny 受保护区域**），与后端 `restore_allowed_prefixes`（派生，见 §4.9.8）同口径；提交前拦一道 |
| V-7 | 模式切换 effect 重蹈 P11 真空初态（watch 派生布尔被吞） | watch `restoreFrom` 数据本身；spec 走完整切换回路（点 B → 重置 → 点 A → 清空） |
| V-8 | E33 浏览 `/restore` 在 host 模式下该目录不存在 | `restoreRoot` 由后端运行时下发；不存在时 treeLoader 404 → 内联错误态，手输模式始终可用 |

#### 4.9.8 P17：host 形态恢复白名单派生 + 原位换算修复（2026-10-25 owner Q1=A / Q2 同批）

**取证（两个用户实拍问题的同一根因）**：源数据早已按 §3 做部署形态派生，**恢复链路没做**。

| # | 现象 | 根因链（逐跳可复核） |
|---|---|---|
| Q2 | 「已存在文件预览清单」恒报 **原位恢复目标不在该快照的备份路径范围内** | ① 环境判定 host（`browse_root='/'`）→ E17 `paths` 经 `to_display` **恒等**（`path_mapper.py:68-71`），展示形态即容器形态（如 `/sources/123`）；② 前端 `inplaceTargetFull` 无条件叠 `/sources` → **`/sources/sources/123`**；③ 后端第一道 `/sources` 白名单通过（双前缀仍在 `/sources` 下），第二道 `in_snapshot_paths(['/sources/123'], '/sources/sources/123')` 不命中 → 403（`restore.py` `_enforce_inplace_lock`）；④ 提交（E19 原位）走同一把锁 → 同样 403，host 模式原位恢复**完全不可用**；⑤ 测试为何全绿：`backend/tests/conftest.py` 钉死 `DEPLOY_MODE=docker`，host+原位零覆盖 |
| Q1 | 「选择位置」目录树不是双模式 | 恢复白名单是**固定常量**（`/restore` / `/sources`），与部署形态无关 → 树根恒 `settings.restore_root`，host 想看真实文件系统做不到；强做即假开关（§3.2） |

**裁决（owner 2026-10-25）**：Q1 = **A**（恢复白名单也按部署形态派生，host → `/` + deny 兜底）；Q2 与 Q1 **同批修**（同一处路径派生逻辑）。

**改动清单（零前端猜测前缀、零新增端点）**：

| 层 | 文件 | 变更 |
|---|---|---|
| 后端 | `app/services/path_mapper.py` | 新增 `restore_allowed_prefixes`（docker `/restore` / host `/`）与 `inplace_restore_allowed_prefixes`（与备份源同源）；deny 命中文案改为「受保护区域…不可作为备份源或恢复目标」 |
| 后端 | `app/constants.py` | **删除** `RESTORE_ALLOWED_PREFIXES` / `INPLACE_RESTORE_ALLOWED_PREFIXES` 常量（留派生说明，防复活） |
| 后端 | `app/routers/restore.py` | 三处校验改走函数；**deny 命中原样 400**（不降级成 403「不在白名单」，否则「`/` 在白名单里」自相矛盾）；docstring 同步 |
| 后端 | `app/routers/system.py` + `schemas/api_schemas.py` | runtime 下发 `restore_root`（生效值）与**新增 `inplace_root`**（原位换算前缀） |
| 前端 | `stores/runtime.ts` + `api/types.ts` | `inplaceRoot` 入 store；类型补 `inplace_root` |
| 前端 | `pages/restore/flow/index.vue` | `inplaceTargetFull` 按 `inplaceRoot` 换算（host 恒等 + 已带前缀幂等保护，对齐 `to_snapshot`） |
| 前端 | `components/restore/RestoreTargetPicker.vue` | host 模式 **deny 拦截**（手输内联报错、树点被保护目录不选中不下钻）；树根随 `restoreRoot` 天然双模式（host → `/`）；`isUnderRoot()` 修 `root='/'` 时 `'//'` 前缀判定 bug（后端 `_is_under` 同款坑——不修则 host 下所有路径被判「根外」） |
| 前端 | `i18n` 双语 | **文案去目录名硬编码**：`originalNote` 去掉「`/sources` 下」、`pickLocationNote` 改 `{root}` 插值（options 收 `restoreRoot` prop）——否则 host 模式提示与事实不符（实拍截图发现） |
| 文档 | `代码审查规范 §3.1 S1/S7`、`AGENTS.md 红线 1`、`架构设计文档 §6.3`、`PRD US-P2-13/Q9` | 白名单表述统一为「三条派生」 |

**护栏**：`backend/tests/test_api/test_restore_host_mode.py` 8 例（runtime 双形态根 / E20 恒等 display / E19 原位接受主机路径 / **双前缀反证 403** / 非同快照 403 / 时间戳流主机目标 202 / deny 400 / docker 根不变）；前端 +`RestoreTargetPicker` deny 2 例（手输 / 树点）+ `flow-shell` ⑪（host 换算恒等，反证旧实现 `/sources/srv/data`）。

**验证**：后端 **1064 passed**（+8）+ ruff 干净；前端 typecheck 干净 + 全量 **41 文件 / 406 passed** + build ✓ 1m45s；**真机探针 `.e2e/p17-host-restore.cjs` pass**——走真实旅程 select-files→下一步→options（host stub）：预览请求 target 为 `/srv/data`（**无双前缀**，Q2 反证成立）、选择位置树根 chip `/`（Q1 双模式生效）、点 `/repo` 被 deny 拦截且已选仍为 `/`、1440/390 零横向溢出零 pageerror（截图 `.e2e/p17-host/`）。

**行为变更（如实记录，需知会）**：① host 模式恢复目标/原位目标白名单均为 `/`（deny 兜底），即**恢复可写主机任意非保护目录**——仍保留「时间戳子目录零覆盖」与「快照 paths 交集收紧」两道约束；② docker 形态行为**零变化**（三条白名单取值与改前逐一相同）；③ host 模式下 `overwrite_mode` 指向非快照路径的响应码由 422 变 **403**（两白名单同为 `/`，422 分支不可达）。

### 4.9.9 P19：快捷路径全项目统一为 D 形态地址栏（2026-10-26 owner 裁决）

> **状态：✅ 已落地（v1.36）**——owner 裁决「按 D 实施，实施时复用项目已有、不重复造轮子，并把项目里其他有快捷路径的地方都统一成 D」。

**取证（为什么必须换掉 chips）**：演示页 `frontend/restore-picker-preview-p18.html` 用真实 WSL 环境 13 个挂载点实测——**A 形态（平铺 chips）快跳区 190px / 占 8 行**，长路径（`/usr/lib/linux-tools/6.18.54.1-microsoft-standard-WSL2`）顶满整行；**D 形态恒定 25px / 1 行**，B 63px、E/G 57px。三处消费方（恢复目标 / 备份源 / 仓库浏览弹窗）此前各写一份 chips，`mountChips` 取数口径也各不相同。

**D 形态定义（本项目的唯一口径，勿各写一份）**：

| # | 语义 | 交互 |
|---|---|---|
| ① | 面包屑分段 | 点段名 = 跳到该层（末段除外） |
| ② | 根段旁的三角 | 展开挂载点菜单（**文字与三角是两段独立热区**：点根段只跳转、点三角只开菜单，与 `VirtualTree` 同款手势纪律） |
| ③ | 整条地址 = 一个编辑热区 | hover 出**覆盖完整地址范围**的虚线框（用 `outline` 绘制，不占布局、无抖动）；点空白或末段 = 就地变地址栏 |
| ④ | 地址栏 | Enter 提交 / Esc 取消 / 失焦「合法即提交、非法即取消并上报 invalid」——失焦**不保留编辑态**（焦点劫持会让用户 Tab 不走，属反模式） |

**改动清单（零后端改动、零新增端点）**：

| 层 | 文件 | 变更 |
|---|---|---|
| 组件 | `components/PathBreadcrumb.vue` | **原地升级**为 D 形态地址栏（S-4：不新增第二个面包屑组件）。新增可选 props `root`（根段/树根，默认 `/`）/ `mounts`（三角菜单项，空则不渲染三角）/ `validate`（业务校验注入）/ `editable`（false 退化纯面包屑）；新增 emit `invalid`（失焦非法时上报，组件不劫持焦点、不弹消息）；老调用方只给 `path` + `@select` 行为不变 |
| 恢复 | `components/restore/RestoreTargetPicker.vue` | 移除 `SegmentedTabs`「浏览树 / 手动输入」二分段与 mount chips → 地址栏同时承担手输（**导航即目标**：地址栏提交既跟浏览层也 emit 目标，等价 Duplicati「Manually type path」语义）；越界/deny 改为 `validate` 注入，脏值仍不 emit |
| 备份源 | `components/task/SourcesPicker.vue` | mount chips → 地址栏；跳转**只改浏览层**（备份源成员资格仍由点目录名切换，语义不变） |
| 仓库 | `pages/repo/index.vue`（浏览弹窗） | 挂载点 `n-tag` 群 → 地址栏（复用既有 `jumpTo`）；删 `NTag` 死 import、删 `.repo__mounts` 死 CSS |
| i18n | zh-CN / en-US | 新增 `pathBar.*` 6 键（组件级文案归组件）；清死键 `repo.mountRoot` / `repo.browseHint`（标签群与提示已删，后者本就是死键）与 `picker.{browse,manual,modeAria,pathLabel,manualHint,errEmpty,errAbsolute,errTraversal}`（二分段与手输块已删，基础形态校验改由地址栏 `pathBar.*` 承担） |

**护栏**：新增 `components/__tests__/PathBreadcrumb.spec.ts`（**14 例**：分段文案与跳转 / 两段热区（点根段只跳转、点三角只开菜单）/ 挂载菜单点项跳转并收起 / mounts 空则不渲染三角 / 空白点击进编辑 / Enter 归一化提交 / Enter 非法保留编辑态 / validate 业务口径注入 / Esc 取消 / 失焦合法提交 / 失焦非法上报 invalid 且不劫持焦点 / editable=false 退化 / root 非 `/` 的根段显示 / root 外路径完整可读）。`RestoreTargetPicker.spec.ts` 8 例改为走地址栏（挂载点收在菜单里 + 点末段空白进编辑 + 越界三段不 emit + host deny 两例不变）；`sourcesPickerJump.spec.ts` 断言对象由 chip 换为 `.pcrumb__menu-item`（chips 常驻与真跳转两条语义保留）。

**过程实证（如实记录）**：首跑抓一处 `//mnt` 双斜杠——`root='/'` 时面包屑累加自 `base` 起拼成 `//mnt`，与后端 `PathMapper._is_under`（`//` 前缀判定）是**同款坑**，已在 `crumbs` 里按「根为 `/` 则累加自空串起」修正并写进注释。

**明确不做（防回潮）**：地址栏不做「新建目录」（后端无 mkdir 端点）、不做路径自动补全与历史记录（无数据源，做了即假开关）、不把挂载点菜单改成 teleport 下拉（就地展开即可查可测，且暗色继承不依赖 portal）。

### 4.9.10 P20：步骤条暗色对比度修复 + 全站向导步骤条统一（2026-10-26）

**触发**：owner 实拍「暗黑主题下恢复向导文案看不清」+ 要求把全站向导的步骤序号数字与文案统一为恢复向导样式并抽成可复用件；同期「仓库配置 → 新建仓库」的文件路径按仪表盘「编辑任务」第②步改造。

**根因（实测取证，非推断）**：新建探针 `.e2e/p20-stepper-contrast.cjs` 直接量 `getComputedStyle` + 按 WCAG 2.1 算对比度（文字 alpha 合成到最近不透明祖先，元素 opacity 一并计入），暗色下：

| 位置 | 修复前 | 修复后 |
|---|---|---|
| 步骤标题（选择文件 / 恢复选项 / 执行与结果） | **1.09 – 1.16** | **10.92 – 15.99** |
| 步骤序号徽标 | **1.32 – 1.41** | **6.33 – 13.18** |
| 亮色主题（对照组） | 3.33 – 21（inactive 步 3.33/3.98 不达标） | 5.68 – 17.85 |

真因是**裸 `<button>` 的 UA 默认 `color: buttontext` 恒为黑**——暗色主题不改 UA 默认值，黑字压 #14161C 只剩 1.16:1。组件只写了 `font: inherit`（**不含 color**），且不能靠 `color: inherit` 兜底：`index.html` 为防首屏白闪把 `#app` 文本色钉成浅色，亮色主题下会反噬（浅字压白底）。另：`--n-action-color` 只在 naive 组件根上存在，普通 div 取不到（P6 既有结论），徽标底色实际落在灰色半透明兜底上。

**改动**：

1. **令牌层**（`theme/skin.css`）新增自持变量，亮/暗各一套：`--rw-text-1/2/3`（与 `tokens.ts` common.textColor1/2/3 同源）、`--rw-primary` / `--rw-success` / `--rw-on-accent`。**`--rw-on-accent` 亮暗相反**（亮 `#ffffff` / 暗 `#14161C`）——暗色主色 #7C93F5、成功色 #81C601 都是高亮色，压白字只有 2.86:1 / 2.08:1，必须压深色。
2. **组件层**：`restore/RestoreStepper.vue` **提升为全站共用 `components/WizardStepper.vue`**（S-4：不复制第二份），旧文件删除。四处向导统一消费——恢复向导 / 任务向导（5 步，`n-steps` → 本组件）/ 首启引导（3 步，description 由新字段承载）/ 三步确认门（无关键词时 2 步，序号按数组连续重排）。**弱化不可跳步的手段由 `opacity:.5` 改为降级文本色 `--rw-text-2`**（opacity 在暗色下同样掉出 AA，且对比度不可控）。
3. **仓库新建的文件路径**（`pages/repo/index.vue`）：原「输入框 + 浏览…弹窗」改为**内联**「目录树选择 + 路径展示」——左栏地址栏（`PathBreadcrumb`）+ 目录树（`VirtualTree`）+ 提示，右栏「已选路径」输入框 + 说明，比例 55/45 与 ≤960 回落单栏均照抄 `SourcesPicker`；**弹窗与 `浏览…` 按钮退役**（S-4：同一能力不留两份实现）。手输保留：新仓库目录往往**尚不存在**，纯树选无法表达「我要建在 /srv/repo-new」。点目录名 = 选为目标且**不改浏览层**（导航只在地址栏/三角）。

**过程实证（两处，均属「不跑真机测不出来」）**：

- **片段根吃掉事件透传**：模板里 `<nav>` 之前写了行 HTML 注释 → 模板变片段根，Vue 对片段根**既不透传事件监听**也把 `$el` 退化成占位节点。后果是任务向导的 `@keydown`（←/→ 与数字键 1–5 跳步）**静默失效**；VTU 里征兆是 `w.element.tagName` 由 `NAV` 变 `DIV`。已把注释移到 `<template>` 之外并在文件头写死禁令。
- **naive `.n-form-item-blank` 是 `display:flex; flex-direction:row`**（实测）：两个裸子节点被平分成左右各 50%，曾把目录树挤成半宽。已显式包一层 `.repo__path-row` 声明比例与窄屏回落。

**护栏**：新增 `components/__tests__/WizardStepper.spec.ts` **8 例**（序号按数组连续编号 / description 有则渲染 / 根元素是 `nav` 且 `aria-label`·`tabindex` 透传 / `@keydown` 透传 / 三态 / inactive 双向不可达 / emit 原 key / enabled 缺省全可跳）；`theme/__tests__/tokens.spec.ts` 新增 **P20 令牌节 4 例**（文本三档与 primary/success 跨文件同源、`--rw-on-accent` 亮暗相反、步骤条禁裸 `--n-*` 且**禁 opacity 弱化**——只扫 `<style>` 块，脚本区注释里的字样会误命中）。真机探针 `.e2e/p20-stepper-contrast.cjs`（四处向导 × 亮暗，AA 4.5 判定）与 `.e2e/p20-repo-path-picker.cjs`（1440/390）全绿。

**验证**：typecheck 干净 + 全量 **43 文件 / 432 passed** + build ✓ + 真机双探针 pass、零 pageerror 零横向溢出。**零后端改动**。

**后续缺陷回归（2026-10-26 owner 实拍「左边地址栏的三角没有，点击 / 才会出现」）**：

- **根因（代码取证）**：挂载点原先靠**树根响应顺带填**（`fsLoader` 里 `if (page.path === "/")`）。但后端 E33 只在 `container == "/"` 的请求里回 `mounts`（`app/routers/fs.py:91`），而本页树起点是**已填路径的父目录**（编辑既有仓库时几乎必然不是 `/`）→ 一次也拿不到 → 三角因 `v-if="mounts.length > 0"` 恒不渲染；只有用户手动点回 `/` 触发一次根请求才突然出现。
- **修法（复用既有解，S-4）**：抽 `ensureMounts()` **与树位置解耦**，独立发一次 `browseFs("/", null, 1)` 只取 `mounts`（与 `RestoreTargetPicker` 同款），并在 `openCreate` / `loadRepoIntoForm`（编辑回填）/ `onTypeChange`（切到 local）三处接线，内部幂等（已有则跳过）、失败静默降级（快跳只是便利）。
- **顺带补的键盘可达性**：三角 `@keydown.esc` 收起菜单。原先只靠「点击组件外」关闭，键盘用户（Tab 到三角 + Enter 展开）**没有关闭路径**。
- **护栏**：`PathBreadcrumb.spec.ts` 增 `④b` 一例（三角聚焦后 Esc 收起、不 emit）；探针 `.e2e/p20-repo-path-picker.cjs` 增断言「**未点击前** `hasCaret` 为真 + 展开后菜单项 ≥2」，真机 1440/390 复跑 pass（挂载点 `/home` `/srv` `/mnt` 全部在菜单内，点 `srv` 仍只写输入框、地址栏保持 `/`）。

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

## 6. 视觉设计基线（合并自《UI界面设计方案》v1.1，2026-10-23 并入）

> 本节将已作废的《UI界面设计方案》（v1.1，原「视觉基线」）的**独有且仍有效**的设计内容并入本文件。自 2026-10-23 起，**本文件是 UI 视觉与功能面的唯一权威**（文档管理规范 §5 硬规则 3：同一主题只保留一个正式文档）。
>
> 并入时按「代码现状 + §4.6 彻底档」做了 supersession 标注：颜色值 / 间距节奏 / 等宽字体 / 导航四分组 / 顶栏重组 / StatusTag 等**已落地为代码且仍有效**；圆角（8/6→12/8）、暗色阴影（无→三档）、`TaskCard`（已删除，由 `TaskRowCard` 取代）三项**被彻底档推翻**，仅作历史记录；命令面板（原 P5）为未启动的可选 backlog。
>
> 事实基线：本节所有 `文件:行号` 引用均于 2026-10-23 实读 `frontend/src/` 复核（BasicLayout 232px / 四分组、tokens 色值与 `fontFamilyMono`、StatusTag / ConfirmGate / JobProgress / VirtualTree / PathBreadcrumb 均存在；TaskCard 已删除；圆角 / 阴影被 §4.6.4 覆盖）。

### 6.1 设计原则（原 §1，仍是本项目设计哲学基线）

四条原则每条对应代码里已形成的行为惯性，新设计是强化而非推翻：

1. **状态先行** —— 备份工具核心焦虑是「备份现在好吗」；仓库连接状态、运行中任务数常驻顶栏（15s 轮询）；仪表盘首屏即仓库状态。现升级为全站统一状态带（§4.5.2 D2 `GlobalStatusBar`）。
2. **危险操作设门，日常操作零打扰** —— 原位恢复 / `forget --prune` 等走 `ConfirmGate`；浏览 / 筛选 / 切仓库从不弹窗。三级手势：读操作随手 / 写操作设门 / 不可逆操作设双门（红线 S6/S7）。
3. **技术用户的密度偏好** —— 快照 id / 容器路径 / cron 以等宽字体呈现才扫读得快。硬规则：数据区等宽、说明区可读。
4. **深色优先、双语并行** —— 主题三态（dark/light/auto）与中英切换为顶栏一级入口；暗色 token 为独立调校非反色。

### 6.2 色彩与对比度（原 §2；色值已落地为 tokens.ts 现值，本节为取证依据）

主色靛蓝 `#3D5BD9`（亮）/ `#7C93F5`（暗），取代旧 Duplicati 蓝。全部色值收敛于 `theme/tokens.ts` 单一来源（亮 / 暗两份 `common` 段），落地只替换该文件。

**WCAG 2.1 AA 对比度实测（按相对亮度公式核算，取证级）**

| 前景 / 背景 | 实测比值 | AA 判定 |
|---|---|---|
| `#3D5BD9` / `#FFFFFF` | 5.68:1 | 通过 |
| `#0F172A` / `#FFFFFF` | 17.9:1 | 通过 |
| `#475569` / `#FFFFFF` | 7.58:1 | 通过 |
| `#94A3B8` / `#FFFFFF` | 2.56:1 | **不通过** → 辅助文字改用 `#64748B`（4.76:1） |
| `#15803D` / `#FFFFFF` | 5.02:1 | 通过 |
| `#B45309` / `#FFFFFF` | 5.02:1 | 通过（现值 `#b87a00` 仅 3.3:1，属现存缺陷） |
| `#B91C1C` / `#FFFFFF` | 6.47:1 | 通过 |
| `#7C93F5` / `#14161C` | 6.33:1 | 通过 |
| `#C3C9D9` / `#1C1F27` | 9.9:1 | 通过 |
| `#7D8598` / `#14161C` | 4.90:1 | 通过（勉强，仅作辅助） |

> 约束：tokens.ts 提交时须附本表；`#94A3B8` 作图标 / 占位符也需 ≥3:1，统一降级 `#64748B`。亮 / 暗当前色值（primary / success / warning / error / text1-3 / body / card / border）见 `tokens.ts:111-194`，与本节 §2.1/§2.2 一致。

### 6.3 字体 / 间距 / 圆角 / 阴影（原 §3；部分被 §4.6.4 彻底档覆盖）

- **字体**：界面正文维持系统栈（`App.vue` 规则，不引入 webfont）；数据 / 路径 / id / cron 等宽 —— `tokens.ts` 导出 `fontFamilyMono`（原 4 处硬编码 `ScheduleRetentionForm` / `audit` / `jobs` / `repo` 已统一收口，§3.1）。✅ 已落地。
- **间距节奏六档**：4 / 8 / 12 / 16 / 24 / 32（`tokens.ts` 导出 `spacing` + `skin.css` `--rw-space-*`）。✅ 与 §3.2 一致，彻底档沿用。
- **圆角**：⚠️ **被覆盖**：原 §3.3 现值 8/6 已改为彻底档 `common.borderRadius 12` / `borderRadiusSmall 8`、Card 16、Button/Input 10、Tag 999、Modal 16（§4.6.4 + `tokens.ts:147-148,150`）。
- **阴影**：⚠️ **被推翻**：原 §3.3「暗色不用阴影，靠 `cardColor` / `borderColor` 两档亮度差分层次」已被彻底档**三档阴影**（rest / hover / pressed）取代（§4.6.4 + `skin.css`）。亮色仍保留阴影分层（`skin.css` 引用本节 §3.3 卡片值 `0 1px 3px rgba(15,23,42,.08)`）。

### 6.4 布局与顶栏（原 §4；导航四分组 + 232px + 顶栏 IA，已落地）

- 侧栏 9 项平铺 → 4 组（总览 / 备份任务 / 数据 / 管理），宽度 200 → **232px**（展开）/ 64px（折叠）。`BasicLayout.vue:283` `:width="232"`、`:collapsed-width="64"`；`menuOptions` 按 `collapsed` 派生两套（折叠扁平一维 / 展开四组，`BasicLayout.vue:126-128`）。
- 顶栏两区：左·上下文区（页面标题 + 二级页 `PathBreadcrumb`），右·全局区（仓库切换 ▾ → 连接状态 tag → 运行中 tag → 主题 → 语言 → 用户菜单，登出收进用户下拉）。主题三态用 `NDropdown`（原 PV-3 定稿），非单按钮循环。
- 响应式断点不变：桌面 ≥960px 侧栏常驻可折叠；<960px 转左抽屉 240px（JS `useMediaQuery` 控制，全仓无 `@media`）。
- PV-1（折叠态分组散掉）：P0 实证选定候选 1（折叠派生扁平数组、展开用分组），零手势改动、零回归面，已在 P2 落地。

### 6.5 页面级方案（原 §5；12 页线框要点，多数已落地，作历史基线）

路由 13 条 / 12 页（`router/index.ts:17-103`）。要点：

- 仪表盘：KPI 四格状态带（仓库状态 / 快照数 / 运行模式 / 最近备份）+ 行动卡行（→新建 / →恢复）+ list/details 双视图（§4.5 主页复刻已落地，D1–D8）。
- jobs：状态筛选 chips + 表格 + 行内 SSE 进度 + 日志抽屉（E27 已落地）。
- snapshots：主从布局 + `VirtualTree`（手势分工不变，spec 7 例护栏）。
- restore：时间戳 + 原位双模式（S7 四要件）；覆盖 = 布尔勾选；`ConfirmGate` 无关键词单次确认（§4.8 恢复模块复刻已落地）。
- taskWizard：NSteps + 草稿持久化（localStorage，仅新建模式）；repo / settings / audit / notifications / login / bootstrap 见各轮回填。

### 6.6 核心组件规范（原 §6；标注被取代项）

- **StatusTag**（新增，`components/StatusTag.vue`）：圆点 6px + 文案，五语义态（success / error / warning / default / info），info 供 audit 只读动作；props 覆盖 jobs 的 `cancelled` 与 audit 的「无 type」。全站收口 P3a-c 批次 23 处（后续批次如恢复向导续增至 36 处）。✅ 已落地，spec 护栏。
- **ConfirmGate**：交互语义不变（S7）；视觉升级：危险动词红色主按钮 + 目标等宽展示 + 输入式确认仅用于不可逆操作。
- **JobProgress**：条高 6px 全圆角；百分比等宽（tabular-nums）；完成转绿、失败转红停留。✅。
- **空态 / 加载态**：空态 = 线稿插画 + 主行动按钮；加载 = NSkeleton 骨架 / 表格 3 行占位（§4.7 P12 A7 加载态已落地）。
- **CodeText**：等宽 + 浅底块 + 可点复制；展示 `display_path`（S1）。
- ⚠️ **TaskCard 已删除**：原 §6 的 TaskCard 规范（260px 骨架、虚拟滚动常量 `CARD_HEIGHT=260` / `ITEM_HEIGHT=272`）已被 P7 的 `TaskRowCard` 取代（list 视图唯一形态，旧 TaskCard 删除）；相关骨架常量在 `TaskRowCard` 中维护（§4.5.6 / §4.7 P7）。

### 6.7 动效与反馈（原 §7，独特，保留）

| 项 | 规则 |
|---|---|
| 时长 | 150ms（hover / 焦点）· 250ms（弹层 / 抽屉）· 400ms（页面切换淡入 8px 上移） |
| 缓动 | `cubic-bezier(0.4, 0, 0.2, 1)` |
| 写操作反馈 | 完成后 message 轻提示（3s / 最多 3 条） |
| 轮询刷新 | 不做视觉闪烁，只变数字 |
| SSE 进度 | 平滑插值，避免与 15s 轮询叠加跳变 |
| KPI 数字 | 进入视口 count-up（`composables/useCountUp.ts`，§11 AB-2，`prefers-reduced-motion` 直切） |
| 降级 | `prefers-reduced-motion` 时全部动效直切（含 count-up、脉冲） |

### 6.8 可访问性（原 §8，WCAG 2.1 AA）

| 维度 | 要求 |
|---|---|
| 对比度 | 正文 ≥4.5:1、大字 / 图形 ≥3:1（取值见 §6.2 实测表） |
| 键盘导航 | 所有操作可达；菜单 / 表格行 `:focus-visible` 2px 主色 outline + 2px offset |
| 触控目标 | ≥44×44px（顶栏小按钮移动态升至 40px + 8px 热区） |
| 屏幕阅读器 | 状态 tag 附 aria-label；进度条 role=progressbar；ConfirmGate 焦点圈闭；主题控件 aria-label + 当前值可读 |
| 文本缩放 | 200% 缩放下布局不破（等宽路径行 ellipsis 兜底） |
| 动效偏好 | 尊重 `prefers-reduced-motion` |

> 现状：全站 aria 盘点 + `a11y-baseline.spec.ts` 静态护栏已落地（§4.3），读屏实测仍未做（保守计分 8）。

### 6.9 明确不采纳（原 §12，设计侧拒绝项；与 §3 功能排除项互补）

| 不采纳项 | 理由 |
|---|---|
| Dock 纯图标导航（72px 无文字） | 低频自托管工具，图标记忆最弱、最需文字标签；移动端无 hover |
| Bento Grid 首屏 | 近 30 天备份量 sparkline / 存储趋势净增**无数据源**（后端只出 capacity / last_error），补齐需扩后端契约，与「不动 API」前提冲突 |
| 玻璃拟态（backdrop-filter） | Naive 无玻璃 token，只能覆写内部类，破坏令牌单一来源；需低端 GPU 降级分支 |
| 渐变主色 + 发光状态点 | primaryColor 只接受单色；项目定位「安静可靠」，发光与日常心境不符 |
| 断点体系重写（Dock → 底部 Tab Bar） | 推翻已 QA 复验的 <960px 抽屉行为；现状断点为 JS useMediaQuery |
| 全量重写 BasicLayout | 违反增量改动 + 单批 ≤400 行；布局层无 spec 覆盖 |

> 保留态度：若后端提供快照时间序列，Bento 与容量环形进度可重新评估（先有数据再谈布局）。

### 6.10 历史批次映射（原 §9 / §10 / §11 → 本文件 §4.7）

| 原《界面设计方案》批次 | 现归属 |
|---|---|
| P0 前置验证（NMenu 折叠 / 主题控件形态） | 已落地：§6.4 / §4.4 对应结论（候选 1 / NDropdown） |
| P1 视觉换肤（纯令牌） | 已落地：等效本文件早期视觉批次（tokens 色值 / 圆角 / 字体），§4.6.4 在其后升级为彻底档 |
| P2 布局与顶栏重组 | 已落地：§6.4（232px 四分组 + 顶栏两区） |
| P3a-c StatusTag 收口 | 已落地：§6.6 StatusTag P3a-c 收口 23 处（后续增至 36 处） |
| P4 页面级（KPI 带 / count-up / 草稿持久化） | 已落地：§4.5 D2/D6、§6.7 count-up、taskWizard 草稿 |
| P5 ⌘K 命令面板（AB-3，**需先立项**） | **未启动**：仅读动作（页面跳转 / 任务定位 / 仓库切换），不放入写操作；开工闸门 = 先按文档管理规范 §5 在 PRD 增补需求条目。列为可选 backlog，不阻塞 §4.7 任何批次 |

> 注：原《界面设计方案》与本文档早期版本都用「P1–P5」编号但含义不同——前者是视觉批次、后者（§4.7）是 P1–P14 全项目批次。自本合并起，**唯一执行批次表为 §4.7 P1–P14**，原视觉 P0–P4 已并入历史落地批次，P5 命令面板为 backlog。

### 6.11 风险与不做（原 §13，标注被推翻项）

- 轮询节奏（15s）/ ConfirmGate 交互语义 / VirtualTree 手势 / UI 库 · CSS 框架 · webfont 不引入 / 960 断点不改 / 后端 · API 契约零改动 —— 均维持（§4 各轮已落地或重申）。
- ⚠️ 原「暗色不用阴影」已推翻（§6.3）；原「圆角 8/6」已改为 12/8（§4.6.4）。
- 后端延展（Bento 重启）前置条件不变：须先开后端批次（stats 时间序列端点），不得以「UI 顺手补接口」夹带。

---

## 修订记录

- v1.38（2026-10-26 三十八修订）：**P20 缺陷回归——地址栏三角恒不出现**——owner 实拍「左边地址栏的三角没有，点击 / 才会出现」。**根因（代码取证）**：挂载点原靠树根响应顺带填（`fsLoader` 里判 `page.path === "/"`），而后端 E33 只在 `container == "/"` 的请求回 `mounts`（`app/routers/fs.py:91`）；本页树起点是**已填路径的父目录**（编辑既有仓库时几乎必然不是 `/`）→ 一次也拿不到 → 三角 `v-if="mounts.length > 0"` 恒假，只有手动点回 `/` 才出现。**改动**：① 抽 `ensureMounts()` **与树位置解耦**独立取挂载点（`browseFs("/", null, 1)`，复用 `RestoreTargetPicker` 同款解，S-4），内部幂等 + 失败静默降级，在 `openCreate` / `loadRepoIntoForm` / `onTypeChange` 三处接线；② 三角补 `@keydown.esc` 收起（原先仅「点组件外」可关，键盘用户无关闭路径）。**护栏**：`PathBreadcrumb.spec.ts` 增 `④b`（Esc 收起 + 不 emit）；探针新增「未点击前 `hasCaret` 为真 + 菜单项 ≥2」断言，1440/390 复跑 pass。**验证**：typecheck 干净 + 定向 spec 15/15 + 探针 pass。**零后端改动**。

- v1.37（2026-10-26 三十七修订）：**P20 步骤条暗色对比度修复 + 全站向导步骤条统一（§4.9.10）**——owner 实拍「暗色主题下恢复向导文案看不清」并指令「全站向导步骤序号与文案统一为恢复向导样式、抽成可复用件」。**取证**：新建 `.e2e/p20-stepper-contrast.cjs` 实量 WCAG 对比度，暗色标题 **1.09–1.16**、徽标 **1.32–1.41**（AA 需 ≥4.5）；根因是裸 `<button>` 的 UA 默认 `color: buttontext` **恒黑**，而组件只写了 `font: inherit`（不含 color），且不能靠 `color: inherit` 兜底（index.html 为防白闪把 `#app` 文本色钉成浅色，亮色会反噬）。**改动**：① skin.css 新增自持 `--rw-text-1/2/3`（与 tokens.ts textColor1/2/3 同源）、`--rw-primary`/`--rw-success`/`--rw-on-accent`（**亮暗相反**：暗色主色/成功色是高亮色，压白字仅 2.86/2.08）；② `restore/RestoreStepper.vue` 提升为共用 `components/WizardStepper.vue`，恢复向导 / 任务向导（5 步，弃 `n-steps`）/ 首启引导（3 步 + description）/ 三步确认门（无关键词 2 步，序号按数组连续）四处统一，不可跳步由 `opacity:.5` 改为降级文本色；③ 仓库新建文件路径改内联「目录树选择 + 路径展示」（左 55% 地址栏+树+提示 / 右 45% 已选路径输入框，≤960 回落到单栏，照抄 `SourcesPicker`），**弹窗与「浏览…」按钮退役**，手输保留（新目录可能尚不存在）。**过程实证**：模板里 `<nav>` 前的 HTML 注释导致**片段根**、吃掉 `@keydown` 透传（任务向导数字键跳步静默失效，VTU 征兆 `NAV`→`DIV`）；naive `.n-form-item-blank` 是 row-flex，裸子节点被平分成左右各 50%（树曾挤成半宽）。**护栏**：`WizardStepper.spec.ts` 8 例 + `tokens.spec.ts` P20 节 4 例（禁裸 `--n-*` 且禁 opacity，只扫 `<style>` 块）。**验证**：typecheck 干净 + 全量 **43 文件 / 432 passed** + build ✓ + 真机探针 `p20-stepper-contrast`（四处向导 × 亮暗全部 ≥5.68）与 `p20-repo-path-picker`（1440/390）pass。**零后端改动**。

- v1.36（2026-10-26 三十六修订）：**P19 快捷路径全项目统一为 D 形态地址栏（§4.9.9）**——owner 裁决「按 D 实施；复用项目已有、不重复造轮子；其他有快捷路径的地方都统一成 D」。**取证**：演示页 `restore-picker-preview-p18.html`（真实 WSL 13 挂载点）实测 A 形态（平铺 chips）快跳区 **190px / 8 行**、长路径顶满行，D 形态**恒定 25px / 1 行**。**改动**：① 原地升级 `components/PathBreadcrumb.vue`（S-4：不新增第二个面包屑组件）——面包屑分段跳转 + 根段旁三角挂载菜单（**两段独立热区**：点根段只跳转 / 点三角只开菜单）+ 整条地址为编辑热区（hover `outline` 虚线框覆盖完整地址、不占布局无抖动；点空白或末段变地址栏；Enter 提交 / Esc 取消 / 失焦合法即提交、非法即取消并 emit `invalid`，**不保留编辑态**＝不劫持焦点），新增可选 props `root`/`mounts`/`validate`/`editable`，老调用方（path + @select）零改动；② 三处消费方统一——`RestoreTargetPicker`（删 `SegmentedTabs` 二分段与 chips，地址栏兼手输且**导航即目标**）、`SourcesPicker`（chips → 地址栏，跳转只改浏览层、成员资格仍由点目录名切换）、`pages/repo/index.vue` 浏览弹窗（`n-tag` 群 → 地址栏，清 `NTag` 死 import 与 `.repo__mounts` 死 CSS）；③ i18n 双语新增 `pathBar.*` 6 键、清死键 `repo.mountRoot`/`repo.browseHint` 与 `picker.{browse,manual,modeAria,pathLabel,manualHint,errEmpty,errAbsolute,errTraversal}`。**护栏**：新增 `PathBreadcrumb.spec.ts` **14 例**（两段热区 / 挂载菜单 / 编辑三键 / 失焦两种分支 / editable 退化 / root 与越界显示），`RestoreTargetPicker.spec.ts` 8 例改走地址栏，`sourcesPickerJump.spec.ts` 断言对象由 chip 换为 `.pcrumb__menu-item`（chips 常驻与真跳转语义保留）。**过程实证**：首跑抓到 `root='/'` 拼出 `//mnt` 双斜杠（与后端 `PathMapper._is_under` 的 `//` 判定同款坑），已按「根为 `/` 则累加自空串起」修正。**明确不做**：地址栏不新建目录（后端无 mkdir）、不做补全/历史（无数据源＝假开关）、菜单不用 teleport 下拉（就地展开可查可测、暗色不依赖 portal）。**验证**：typecheck 干净 + 全量 **42 文件 / 420 passed**（+1 文件 +14 例）+ build ✓；**零后端改动**。

- v1.35（2026-10-25 三十五修订）：**P17 host 形态恢复白名单派生 + 原位换算修复（§4.9.8）**——owner 就两个实拍问题裁决 **Q1=A（恢复白名单也按部署形态派生）+ Q2 同批**。**根因（同一处）**：源数据早按 §3 派生，恢复链路仍是固定常量 → ① host 模式 `to_display` 恒等，前端 `inplaceTargetFull` 无条件叠 `/sources` 产生 `/sources/sources/...` **双前缀**，E20 预览与 E19 提交双双 403「原位恢复目标不在该快照的备份路径范围内」（且 host 模式原位**整体不可用**）；② 「选择位置」树根恒 `/restore`，做不出设计口径的部署双模式（强做即假开关）。**改动**：后端新增 `restore_allowed_prefixes`（docker `/restore` / host `/`）与 `inplace_restore_allowed_prefixes`（与备份源同源）、删两个白名单常量、deny 命中原样 400（不降级 403）、runtime 增 `inplace_root` 且 `restore_root` 改生效值；前端 store 接 `inplaceRoot`、`inplaceTargetFull` 改为形态感知换算（host 恒等 + 幂等保护）、选择器加 **host deny 拦截**（手输内联报错 / 树点不选中不下钻）与 `isUnderRoot()`（修 `root='/'` 时 `'//'` 前缀判定 bug——后端 `_is_under` 同款坑）；红线 S1/S7、AGENTS 红线 1、架构 §6.3、PRD US-P2-13/Q9 同步「三条派生」。**验证**：后端 1064 passed（+8 `test_restore_host_mode.py`，含**双前缀反证 403**）+ ruff 干净；前端 typecheck + 全量 **41 文件 / 406 passed** + build ✓ 1m45s；**真机探针 `.e2e/p17-host-restore.cjs` pass**（host stub：预览 target = `/srv/data` 无双前缀 / 树根 `/` / deny 拦截 / 4 视口零溢出零报错）。另修两处 host 文案失真（`originalNote` 去 `/sources`、`pickLocationNote` 改 `{root}` 插值）。**行为变更**：host 模式恢复可写主机任意非保护目录（仍受时间戳子目录零覆盖 + 快照 paths 交集双约束）；docker 形态取舍与改前逐一相同（零变化）；host 下 `overwrite_mode` 指向非快照路径由 422 变 403（两白名单同为 `/`）。

- v1.34（2026-10-25 三十四修订）：**P16 恢复向导第二步按 Duplicati options 复刻落地（§4.9 第八轮）**——owner 五裁决：**D1 Metadata 分区取消**（restic restore 无 `--skip-metadata`/`--restore-permissions`，占位死控件违背诚实原则）、**D2 走《源数据过滤与树双模式设计方案》§3 树双模式**（`runtime.restoreRoot` 单一事实源 + VirtualTree + mount chips + 浏览树/手输二模）、**D3 高级选项取消**（红线 S2）、**D4=A E20 预览并入「已存在文件」卡**、**D5=A 全向导去 `n-card`**。① **外壳**：Duplicati「stepper + 居中列」——options 500px / select-files·progress 1000px 双档（`isWidePage`），≤960 断点 padding 0 32 + `border-box`（探针抓到 content-box 下 564px 偏差，已修）；② **三卡片**：`RestoreOptionCard.vue`（复刻 ToggleCard：header 折叠、caret 200ms 旋转、max-height 0↔1000px/250ms、`disableToggle` 退化非按钮的纯标题行、aria-expanded）+ raised 单选卡（`button[role=radio]`，repo A4 类型卡范式：整行可点 + 主色描边 + 单选圆点 + 键盘 Enter/Space）+ 页脚两端（返回 `IconArrowLeft` / 提交 `IconArrowRight` raised primary + loading）；③ **`RestoreTargetPicker.vue`**：「浏览路径 / 手动输入」二模（复用 `SegmentedTabs`）+ mount chips（E33 仅根请求回 mounts → 一次性取 `/` 过滤 restoreRoot 子树）+ 越界三段内联校验（根外/非绝对/`..`，与后端 `RESTORE_ALLOWED_PREFIXES` 同口径，脏值不 emit）；④ **状态重构**：`targetMode('timestamp'|'inplace')` → `restoreFrom('original'|'pickLocation') + customTarget`（容器形态，默认 runtime.restoreRoot；服务端仍在所选目录下建 `<stem>-<stamp>` 时间戳子目录，守 PRD G3 零覆盖）+ 模式切换 effect（watch `restoreFrom` 数据本身，避 P11 真空初态坑；切 pickLocation 复位 customTarget）+ 跨 OS 确认（原位置流 + 快照 paths 含 `\` → `window.confirm`，沿用 R10 模式）；⑤ i18n 双语新增 `restore.options.{title,original,originalNote,pickLocation,pickLocationNote,back,submit,crossOsConfirm}` + `picker.*` 9 键，清 6 个死键（`stepConfirm`/`targetTitle`/`targetTimestamp(Note)`/`targetInPlace(Note)`）；⑥ 图标库 `IconArrowRight` 登记 60→61。**验证**：typecheck 干净 + 全量 **41 文件 / 403 passed**（新增 `RestoreOptionCard` 6 例、`RestoreTargetPicker` 6 例、`options-step` 7 例、`flow-shell` ⑨⑩ 2 例）+ build ✓ 2m08s + 真机探针 `.e2e/p16-options-mobile.cjs` **pass**（1440/960/768/390 零横向溢出零 pageerror、1440 列宽 500、双按钮页脚；截图 `.e2e/p16-options/`）。**过程实证三缺陷**（如实记录）：漏 import `NCard`/`NInput` 致组件按原生元素渲染、named 槽与输入框整片丢失（vue-tsc 查不出；`naive-imports` 全量跑本可拦，本次只跑定向 spec 未覆盖——已记教训）、`content-box` 断点列宽偏差（564→500）、探针 `**/api/**` 通配误伤 Vite 模块。**零后端改动**。

- v1.30（2026-10-25 三十次修订）：**快照浏览页收尾 + 护栏盲区修复**（owner「按顺序修」指令，UI 设计一律以本文档为准）。① **五处功能缺陷修复**（`pages/snapshots/index.vue`，此前零前端 spec）：切筛选**回到第 1 页**（深页切筛选沿用旧 offset → 空列表误导）；历史 tag 改**累积归并**（原从已筛选结果归并 → 选中某 tag 后其它历史 tag 从下拉消失，切不回去）；A3 打/去标签**回显新旧 id 映射**（PRD US-E2 AC5，改标签生成新快照对象，旧 id 立即失效）；E18 `truncated` **明示提示**（大目录截断不再静默）；`size` 列改走 `utils/format`（`formatBytesOr`，与恢复向导版本下拉同口径，不裸显字节数）+ 错误态取 `AppError.message`（去掉 `Error: ` 前缀）。② **E18 已返回的 size/mtime 不再丢弃**：`VirtualTree` 增可选 `meta` 字段（行末等宽小字、opacity 降档、亮暗自适应），仅快照页传入，E05/E33 两处消费方零影响。③ **真实生产缺陷（本轮最重）**：快照页 **`NInput` 从未导入** → 5 个输入框（forget 时长档/标签档 + 打标签三组）在浏览器里 unresolved 整片不渲染；`vue-tsc` 与 `build` 都不检查未注册组件名，P14 四象限走查也未捕获（unresolved 只是 Vue warn，非 pageerror）。④ **护栏盲区修复**：`naive-imports.spec.ts` 原按非贪婪 `<template…</template>` 取块，遇到嵌套 `<template #header-extra>` / `#icon` / `#preview` 的页面在**第一个** `</template>` 处截断，其后 `n-*` 全部逃脱检查（这正是 ③ 能潜伏至今的原因）；改为**全文扫描 `<n-*`** 且只扫 `.vue`（`.ts`/spec 里的 `<n-xxx>` 全是字符串字面量或注释，实测 4 处假阳性）。复扫全仓 `.vue` **零漏导入**。⑤ **护栏补齐**：新增 `pages/snapshots/__tests__/snapshots-page.spec.ts`（6 例，覆盖上述五项 + 文件树 meta）。⑥ **明确不做（如实记录）**：快照抽屉**不加搜索框**——P3 端点虽已就绪，但 `VirtualTree` 无「定位到路径」能力，命中项点击无落点即死键（V-4 / D8 同类教训）；需先给 VirtualTree 加 `expandTo(path)` 能力再做。⑦ **两项裁决：一项落地、一项撤回（如实记录）**：① **US-026-08 默认分组视图「先落地、后整体删除」**——落地口径为「未筛选时按任务渲染分组卡（`n-grid` + 复用 P6 形态层 `ActionCard`，数据取 `TaskCard.snapshot_count` 的 per-tag 计数，零后端改动；历史遗留 tag 落「未归类」组且计数显示「未知」不造假 0；点组 = 设 `tagFilter`）」；真机走查 owner 反馈排版不佳 → **按指令整体删除**（分组带 / `ActionCard` 与 `IconSnapshots` 引用 / 双语 `snapshot.groups.*` / 相关 6 例 spec 一并清除）。删除同时消掉一处口径隐患（取证）：分组计数是「该 tag 在**当前仓库**的全部快照」（`_snapshot_counts_by_tag(request, repo_id)`，`backup_tasks.py:610-634`），而表格在其上还叠加 `hostFilter` → 先按主机筛选再点分组会给出「卡上 4 个、表里 3 条」的落差；保留就必须在卡上标注口径，删掉更干净。**PRD §4 需求池 US-026-08「✅ 已交付」需回落为「未交付」**（文档侧同步待办）。② **forget `group_by` 不给可编辑入口**（保留）——`restic.py:521-522` 只过 `_assert_safe_value`（防注入）**不校验维度合法性**，非法值会迟到 500；且任务表单无该控件（`ScheduleRetentionForm.vue:12` 仅注释列为「高级字段」）、恒默认 `host,paths,tags` 自洽 → 改为**只读公示**「分组维度：host, paths, tags（restic 默认，不可编辑）」。③ **forget 提交后跳任务页 `?job=<id>`**（复用 A6 既有模式，`jobs/index.vue:333-349` 按 query 自动开日志抽屉）——forget 是**异步作业**，留在快照页列表不会自己变，用户据此判定「清理没生效」；跳转后成功/失败原因立刻可见。④ **A3 打/去标签加前端前置拦截**：ID 与 tag/host 筛选都空时**不发请求**，直接给出「会作用于仓库内全部快照，已阻止」——与后端 422 同口径，但前端先说人话（原行为是发请求后被拒，用户只看到一条错误）。新增双语 `snapshot.forget.groupBy` / `snapshot.tag.needTarget`；护栏改为覆盖 ③④（快照页 spec 共 8 例）。验证：typecheck + 前端全量 + build 全绿。
- v1.31（2026-10-25 三十一次修订）：**筛选组件收敛为共用 FilterBar（纠正 S-4 重复造轮子）**。快照浏览页筛选区此前**重写了一份任务历史页的筛选条**（draft 模式 + 应用/重置 + IconFilter），违反本文档 S-4「必须共用组件、禁止页内各写一份」。收敛动作：① 抽出共用 `components/FilterBar.vue`——筛选卡外壳 + 控件默认插槽 + 应用/重置按钮（loading 透传、IconFilter、`common.applyFilters`/`common.reset` 双语键），只 emit 不查询；② **两处接入**——任务历史「全部历史」与快照浏览页均替换原手写版；③ **清理死代码**——jobs 页 `IconFilter`/`IconX` 死 import、`jobs.apply`/`jobs.reset` 死键、快照页 `snapshot.apply`/`snapshot.reset` 死键（统一走 `common`）；④ **纪律固化进组件注释**：flex 容器里筛选控件必须用**固定 width**（jobs 历来如此；快照页曾因 `max-width` 被按钮挤成 0 宽，两个页面各踩一次）；⑤ 护栏 `components/__tests__/FilterBar.spec.ts`（4 例：插槽渲染 / 改下拉只 emit 不查询 / loading 透传 / 图标与文案）。验证：typecheck + 前端全量（39 文件）+ build 全绿。
- v1.32（2026-10-25 三十二修订）：**R-016 独立回收（restic prune）落地——补漏 forget 未勾选 --prune 的遗留空间**。背景：用户场景「漏勾 forget 的回收开关、快照其实已 forget 完」→ `restic forget --prune` 仅在确有快照被删除时才触发 prune，UI 无独立回收入口即无法释放空间。设计：复用 R-016（E21）forget 预览→确认门→提交异步作业→跳任务页跟踪的成熟范式，prune 独立成一条作业链。后端（restic.py `VERBS` 增 `prune` + `_verb_flags` 增 `if verb=="prune"` 仅透传 `--dry-run`；`enums.JobType` 增 `PRUNE`；`constants.JOB_TYPES` 增 `"prune"`、`AUDIT_PRUNE_PREVIEW`/`AUDIT_PRUNE_RUN`、大仓库专属 `PRUNE_PREVIEW_TIMEOUT_SECONDS=120`；`job_runner._build_args` 增 `prune` 分支返回空 `WhitelistArgs`；`notifier.type_label` 增 `"prune":"回收空间"`；`api_schemas.PrunePreviewResponse` 数据类；`routers/ops.py` 增 `POST /api/prune/preview`，读-only 跑 `restic prune --dry-run`，正则容错解析「to delete: N packs」/「total prune: …/X」回填 `would_remove_packs`/`freed_bytes`/`freed_text`/`clean`/`lines`，审计 `AUDIT_PRUNE_PREVIEW`。实际执行侧复用既有 `createJob('prune', {})`→`job_runner`→restic prune，审计 `AUDIT_PRUNE_RUN` 在作业落库处统一记（S6 危险操作清单 16→17）。注意安全白名单链：`prune` 在 `VERBS`/`JobType`/`JOB_TYPES`/`_build_args` 四处必须同步，否则请求被 S2 拒。前端：`api/types.ts` 的 `JobType` 联合增 `'prune'` + `PrunePreviewResponse` 接口；新增 `api/prune.ts`（`previewPrune()`）；快照页新增「回收空间」卡片（warning 说明 + 预览按钮 IconSearch），`ConfirmGate`（keyword=`PRUNE`）预览插槽展示 clean 绿色提示 / 待删 packs+可释放量告警 + `<n-collapse>` 折叠原始 restic 输出（`.snapshots__prune-raw` 等宽滚动 pre）；forget 卡片在原回收开关下补 `snapshot.forget.pruneHint` 锁/耗时提示（开关保留为日常主路径、独立 prune 为兜底）；i18n zh-CN/en-US 各增 `snapshot.prune.*` + `forget.pruneHint`；护栏 `snapshots-page.spec.ts` 增 2 例（preview→确认提交 prune 作业并跳 `?job=job-1` / clean=true 显绿）。验证：typecheck + 前端全量 + build 全绿（prune.spec 随全量跑）。
- v1.33（2026-10-25 三十三修订）：**forget 卡片预览按钮下移到独立行，与 prune 卡片同构**（用户指令「预览将被清理快照按钮也挪下来，跟回收空间一样」）。原 forget 卡片把 `预览将被清理的快照` 按钮塞在「标签/保留档/回收开关」横向表单 `n-space` 末尾，随控件增多被挤成一行里的末位；改为把按钮抽离到独立 `<n-space align="center">` 行（在表单行之下、needKeep/groupBy 提示之上），并在按钮右侧补 `snapshot.forget.previewHint`（restic forget --dry-run 只读预览说明），结构对齐 prune 卡片的「按钮 + previewHint」行。forget 测试按按钮文案 `预览将被清理的快照` 定位，下移后文案不变仍命中；prune 测试原 `findComponent(ConfirmGate)` 会命中首个（forget）门导致 `createJob('prune')` 断言落空，改为 `findAllComponents(ConfirmGate).find(title 含『回收』)` 精准命中 prune 门。验证：快照页 spec 全绿（prune 两例 + forget 去向例均过）。
- v1.29（2026-10-24 二十九次修订）：**任务行形态对齐 + 向导第①步语义澄清 + 第②步左树右栏**——① **`TaskRowCard` 对齐 Duplicati 备份行形态**（owner 裁决「版式 + 文案，不动数据」）：指标文案改「上次成功备份于 / 下一次排程执行 / 用时」（空值一律「—」，与 details 表 `tableDuration` 同形态）、「立即运行」改「▶ 开始」、编辑由三点菜单**提到行内画笔**、三点触发器由 tiny quaternary 裸「⋯」改 small 图标按钮（`IconMore` 新增，图标库登记 59→60）、菜单条目补左图标；**状态徽章位置经 owner 三次裁决最终定为「用时」之后**（曾按参考图下移到指标行行尾 → 紧贴右侧三键被否 → 抬高到名称行 → 仍非原位置 → 回到指标行末），徽章文案改**短状态词**（成功/失败/中断…）并删掉与徽章同色同文案的「上次结果」指标（否则一行两个「上次成功」）。② **第①步「启用调度」→「启用任务」**：它实为 `tasks.enabled` 任务总闸（关掉只停自动、手动「开始」不受约束，卡片变「已暂停」，补跑/ prune 判定同停），原文案与语义不符；开关下加副作用 hint，**第④步顶部加条件提示**（`enabled=false` 时提示调度暂不生效）——总闸在第①步、被它控制的东西在第④步，中间隔两步，不提示用户会白配 cron。③ **第②步 `SourcesPicker` 改左树右栏**（对齐 Duplicati `source-data.component.html` 的 `.left`/`.right`）：左 = 目录树，右 = 「路径」/「排除」两张卡；已选路径与排除规则由 tag 群改为**只读输入行 + 铅笔编辑 + 垃圾桶删除**（编辑态给保存/取消，提交走全量列表替换保序），路径校验与「直接添加」同口径（绝对路径 + 无 `..`）。**明确不做**（有意为之，勿回退）：不做「新增远程目录」对话框（备份源恒为容器内 `/sources` 挂载点，远端一律宿主机挂载后映射）、不做「显示隐藏文件」开关（E05 `/api/sources` 无 hidden 参数，给了也是假开关）、不做 Duplicati 14 项过滤条件下拉（restic 只有 glob exclude，映射结论见 `.e2e/probe-restic-exclude3b.sh` / `3c.sh`，待 owner 定方案另起组件）。④ 护栏：新增 `wizardEnabledHint.spec.ts`（5 例，该组件此前无单测）与 `sourcesPicker.spec.ts`（7 例）。
- v1.28（2026-10-23 二十八次修订）：**合并《UI界面设计方案》视觉基线（v1.1）并入本文件**——新增 §6 视觉设计基线（设计原则 / WCAG 对比度实测 / 字体间距 / 布局顶栏 / 页面线框 / 组件规范 / 动效 / 可访问性 / 明确不采纳 / 历史批次映射 / 风险），逐条标注被 §4.6 彻底档覆盖的项（圆角 12/8、暗色三档阴影、`TaskCard`→`TaskRowCard`）；原《UI界面设计方案》作废归档（`docs/archive/`，superseded by 本文件）。代码注释中指向旧文档的 9 处引用改指本文件 §6（App.vue / useCountUp.ts / skin.css / tokens.ts / StatusTag.vue 等）。文档管理规范 §6 登记表同步更新。
- v1.27（2026-10-23 二十七次修订）：**P15 全站图标统一落地**（owner 四项裁决：①重绘 ②合并 ③全上 ④留库）——① **三文件合并**：`HomeIcons.ts`（33）+ `MenuIcons.ts`（16）+ `BackendIcons.ts`（8+映射）→ 单一 `components/icons/icons.ts`（57 图标 + `BACKEND_ICONS` + `backendIcon`）；旧三文件删除、9 处消费方 import 改向；`IconPlusCircle` 跨文件重复去重（保留 HomeIcons 版）。② **24 网格重绘为 P4 标准**：24 个旧画法（导航 16 + 后端 8）按 20×20 网格 / 描边 1.5 重绘——几何 ×5/6 精确变换 + arc 标志位（large/sweep）单独保护（防弧向损坏），token 空格分隔防数字粘连；旧 24 网格渲染描边 1.25px → 标准 1.5px。③ **全站图标化**：9 个零图标页面补 35 处标准图标（jobs 4 / snapshots 3 / audit 2 / reports 5 / notifications 3 / settings 9 / TaskWizard 6 / SourcesPicker 2 / login 1 / bootstrap 4），统一 `<n-icon :size="16">` + `#icon` 插槽消费；规则 = small 及以上按钮加图标、tiny 紧凑按钮（表格行内）保持纯文字。④ **留库不删**：全量导出保留作图标库（含 IconPlay/IconSearch/IconFilter 等暂无消费方资产），`icons.spec.ts` 登记 59 项 + **全量** 20 网格/1.5 描边护栏（原只守 HomeIcons）；⑤ **预览页**：`frontend/icon-preview-p15.html`（57 图标 × 6 分组 + P15 徽标 + 尺寸档 16/20/24/32 + 暗底 currentColor 带；脚本 `.e2e/p15-preview.py` 可重生成）。验证：三步门分跑（typecheck / 全量 **291 passed** / build）+ 像素 diff（全站 0.09–1.1%、全部同尺寸——图标化不改变布局高度）+ 探针复跑（A7 / P13 向导五步与 ConfirmGate 三门）+ 移动端 8 页 × 4 视口零溢出（390 步骤条 503>308 不变）。
- v1.26（2026-10-23 二十六次修订）：**P14 全站回归落地（零代码改动纯验证批）**——① **四象限截图**：全站 8 页 × 亮/暗 × 中/英 = 32 组合零 pageerror，且**四象限全站零未翻译裸键**（内置扫描器按 `段.键` 形态检测 vue-i18n 缺键回显）；② **桌面零漂移**：HEAD 连跑两次全站 8 页截图**逐像素一致**（bbox=None），证明截图管线确定性且当前 HEAD 无漂移（§4.3 的 `overflow-x:auto` 折叠 margin 类教训未复现）；③ **移动端 4 视口**（1440/960/768/390）× 8 页 = 32 组合**零横向溢出**（`documentElement.scrollWidth ≤ innerWidth + 1`），390 向导步骤条 `scrollWidth 503 > clientWidth 308` → 5 步全可达，与 §4.3 历史记录同值（复验通过）；④ **恢复模块响应式**（`-milli` 等价）：`/restore` 入口 4 视口 ok + flow `select-files` 768/390 零溢出零报错；⑤ **E2E 冒烟**：A7 加载态探针（骨架 3 行/空态不闪/details 遮罩）与 P13 向导五步 + ConfirmGate 三门探针复跑 PASS，零 pageerror。产物脚本：`.e2e/p14-pixdiff.cjs`（四象限 + 裸键扫描）、`p14-mobile.cjs`（4 视口溢出矩阵 + 步骤条断言）、`p14-restore-flow.cjs`。**三步门终跑被 owner 取消**——P13 时三步门已全绿（typecheck / 289 passed / build）且本批零代码改动（工作树干净），可随时补跑。
- v1.25（2026-10-23 二十五次修订）：**P13 逐页重排第二批（高风险）落地**——① repo（自定义 CSS 230 行）：模板 7 处 + CSS 11 处节奏对齐（vertical 10→12、横隙 6/10→8、紧凑对 2→4；`.repo__req` 的 2px 星标微调为字形级例外保留）；② settings（128 行 CSS）：卡片级 10→12 ×3、开关行→提示对 2→4 ×4（对齐全站「控件→自身提示」4px 惯例）、横隙与时区子块 6/10→8、sticky 页脚 padding 10px 0 2px→12px 0 4px；③ TaskWizard + 步骤组件（ScheduleRetentionForm / SourcesPicker）：wizard__body 14→16、字段对 2→4 ×11、行距 6/10→8 ×9；④ bootstrap margin-top 20→24；login 零改动（本就落在节奏内）。验证：三步门分跑（typecheck / 全量 vitest **289 passed** / build）+ 逐页像素 diff（repo 8.9% 为 padding 级联位移、其余 0–3.6%、login 0%，并排图逐页核对结构零缺失）+ **真机探针**（`.e2e/p13-wizard-probe.cjs`）：向导五步全流程（填名 → 手动加源 → 测试连接过 G3/E1 强门禁 → 调度 → 保留；dry-run 前「确认并立即首跑」禁用、stub 预演通过后放行）+ repo 删除 ConfirmGate 三门全流程（弹出 → 关键词错词禁用/对词放行 → 终态按钮），零 pageerror。
- v1.24（2026-10-23 二十四次修订）：**P12 逐页重排第一批（低风险）落地 + A7 主页加载态**——① **五页校准**（P5 令牌层变化后的逐页校准）：snapshots / jobs / audit / reports / notifications 五页间距节奏对齐 4/8/12/16/24/32（原 `10`/`6` 为 P5 前遗留值，共 16 处：垂直分段 10→12、控件横隙 10→8、开关-标签对 6→8），全页无节奏外数值残留；② **A7 加载态落地**（owner 2026-10-23 裁决从 P7 并入）：dashboard 任务卡分支序补「error → 骨架 → 空态 → list/details」——首载（`isLoading`，不含 15s 轮询的 `isFetching`）显示三行骨架（行高 92 与 `TaskRowCard` 对齐，加载完成不跳高；`role=status` + `aria-busy` + 复用 `common.loading` 可访问名，零新 i18n 键），details 表格补 `:loading`；修复原缺陷形态「首载 `sortedTasks` 为空亦真 → 闪空态引导卡误导用户去新建」；护栏 `page-render-smoke.spec.ts` 增 2 例（挂起 query → 断言骨架 3 行且空态不出现 + 数据到达后正确让位）；③ **reports 加载态**：表格去 `v-if="report"` 改 `:loading`（原首载「窗口选择器之下大片空白」）；④ **notifications 加载防护**：配置未到位时渠道列表整体不渲染（原首载闪「四渠道全部未配置」+ noChannel 警告），且 `saveChannel`/`saveRules` 按钮首载禁用——两者 patch 均为**全量字段**语义，加载未完成就保存等于把表单默认值写回后端（真实竞态，顺带关闭）；⑤ **scroll-x 阈值复核**（P12 风险项）：四表固定列宽和均 < 阈值（snapshots 630<860 / jobs 780<900 / audit 440+expand 列<720 / reports 无固定列全弹性、520 为窄屏下限），弹性列可收缩，无溢出；⑥ 验证：三步门分跑（typecheck / 全量 vitest **289 passed** / build）+ 逐页像素 diff（PIL 阈值 8/255：四页变化 0.9–3.5% 且 bbox 全部落在间距调整区，dashboard/reports 0%——加载态仅加载中可见属预期）+ A7 真机探针（延迟 3s 响应：骨架出现 / 空态不闪 / aria 正确 / 数据到达让位 / 零 pageerror）。
- v1.23（2026-10-23 二十三次修订）：**恢复向导三步骤组件拆出（P10/P11 遗留项补做）**——① `select-files.vue` / `options.vue` / `progress.vue` 三个步骤组件从 `flow/index.vue` 拆出，父级**收壳为纯编排器**。② **拆分口径**：跨步骤共享的状态与动作留父级（快照查询与默认选中、勾选集合、目标模式与预览 fetch、result 与作业终态、确认门与提交、步骤↔路由同步、离开确认）；**步骤私有不共享的逻辑随组件内聚**——B3 搜索与 debounce 定时器 → select-files，预览表格列定义 → options，日志拉取（getJobLog）与 jobs.summary 计数解析 → progress。③ **可变态走显式 props/emits**（对齐 RestoreStepper 风格，不用 defineModel）：snapshotId / targetMode / inplaceTarget / overwriteExisting 均由父级持有、子组件 emit 回写；勾选收敛为**路径级 `toggle-file` 事件**（父级不 import 子组件类型）。④ **样式随模板迁移**（scoped 不跨组件）：`.restore__hit*` 移入 select-files，`.restore__gate-file` 等确认门样式留父级。⑤ **预览 fetch 状态留父级的原因**：确认门预览插槽与 `canOpenGate` 共用同一份 previewStats/previewItems，拆进子组件会逼出第二份状态或 slot 回传，故只把「动作」（reload / load-more）emit 上去。⑥ 验证：typecheck + 全量测试 + build 全绿，恢复三 spec 21 例覆盖全旅程——**纯结构重构，零行为变化**（组件表「待拆」三项至此清零）。
- v1.22（2026-10-23 二十二次修订）：**P11 恢复进度页三卡 + 完成态圆环（含一处 A7 潜伏缺陷修复）**——① **三卡落地（R8）**：步骤 3 由「单卡结果区」改为 `n-grid`（`1 m:2`）三卡——进度卡（恢复目标 / 文件数 / 运行说明 + `JobProgress` + 完成态 `RestoreProgressRing` 圆环）、结果摘要卡、日志卡（复用既有 `getJobLog`，与 `JobLogDrawer` 同一口径）。② **「通知卡」裁决为结果摘要卡（如实记录，不造假）**：Duplicati 三卡是「进度 / 通知 / 日志」，但本项目**无通知记录数据源**（`api/notify.ts` 仅配置读写、通知页也只消费配置）→ 第二卡以**结果摘要**落地（复用既有 `result` / `jobs.summary`，含原位流覆盖/失败计数回报），V-4「不新造数据源」的实质仍被遵守（不为此新增端点）。③ **完成态圆环（R9）**：新建 `components/restore/RestoreProgressRing.vue`——SVG 圆环 + 状态语义色（success/error/info）+ 中心文案调用方注入（组件不依赖 i18n，便于单测）；percent 越界归一化，不产生负偏移反向弧。④ **顺手修复 A7 潜伏缺陷**（由 P11 护栏用例④走完整流程暴露）：path-less 快照下 `targetMode` 卡死 `'inplace'`——原 `watch(inPlaceDisabled)` 只认变化，而 `inPlaceDisabled` 在快照列表加载前已恒为 true（无选中快照 → paths 为空）、path-less 快照加载后依然是 true → 「入口置灰则回落时间戳流」的逻辑被**真空初态整个吞掉**，用户卡在「inplace 置灰却仍选中」的非法态（`goConfirm` 因 needTarget 早退，步骤 1/2 的点击全部空转，confirm 亦空转）。改为 `watch(selectedSnapshot)`：切到（含默认选中）无 paths 的快照时才回落；有 paths 时不打扰 A7 的 inplace 默认。该缺陷自 A7（原位置为默认）起存在，P8 原样搬迁带入 flow 页。双语新增 `restore.progress.*`；护栏 `flow-shell.spec.ts` 增 2 例（共 16 例；`entry.spec.ts` 4 例不变）。
- v1.21（2026-10-23 二十一次修订）：**P10 恢复步骤重组（搜索框 + 版本下拉 size + options 分区 + 页脚）**——① **搜索框落地（R6）**：前端封装 `searchSnapshotFiles`（P3 端点**已核验落地**：`POST /api/snapshots/{id}/files/search`，服务端子串匹配 + 游标分页 + 30s 缓存）；UI 含**结果计数**（取后端 `total` 命中总数，非本页条数）、Clear、spinner；**B3 前端契约**：debounce 300ms **且**关键词 ≥2 字符才发请求，前端防逐键触发（后端虽有缓存仍需此项）。② **版本下拉补 size（R5）**：顺带把 `formatBytes` 收口为 `utils/format.ts`（含空值版 `formatBytesOr`，命名对齐 `formatDateTimeOr`），仪表盘与恢复页共用，**消除第三份重复**。③ **步骤重组**：目标模式 / 目标目录 / 覆盖策略由步骤 1 移至步骤 2，步骤 1 只管「选快照 + 选文件」，对齐 Duplicati 的 select-files / options 划分。④ **options 分区**：落地「恢复到哪」「已存在文件」两分区 + **页脚两端对齐**（左上一步 / 右下一步）。⑤ **Metadata 与「高级」两分区未落地（如实记录，不造假）**：后端 `RestoreRequest` 仅 `snapshot_id`/`target`/`paths`/`overwrite_mode` 四个字段、`restic.py` 无 metadata/skip_metadata 参数 → **无后端支撑**，做出来就是占位死控件（P7 D8 死键同类教训），需先加后端契约再补。⑥ 组件暂未拆分为 `select-files.vue` / `options.vue`（内容重组先行；拆分随 P11 一并做，避免状态提升改两遍）。双语新增 `restore.search.*` / `restore.options.*`；护栏 `flow-shell.spec.ts` 增 3 例（共 11 例）。
- v1.20（2026-10-23 二十次修订）：**P9 恢复向导外壳落地（§4.8.5 R4/R10）**——① `pages/restore/flow/index.vue` 由 P8 迁入的向导**改造为外壳**：只读 `n-steps` 换成自建 `RestoreStepper`（每步是原生 `button`，故 Tab/Enter/Space 天然可达；未过步骤置 `inactive` = `pointer-events:none` + `opacity .5` + `tabindex=-1` + `aria-disabled`，**鼠标与键盘双向不可达**，沿用模版 `.scss:44-47` 语义，V-2）。② **步骤 ↔ 路由双向同步**：1=`select-files` / 2=`options` / 3=`progress`，跳步与内部推进都写回 URL（`router.replace`），浏览器前进/后退同样生效——P8 的「三子路由同指向且 step 不变」至此消除。③ **跳步校验**：第 2 步需已选快照、第 3 步需已提交作业，否则 `inactive`。④ **满高**：仅步骤 1（文件树 `:height` 300→520 + 主体 `min-height: 60vh`）。⑤ **离开确认**（R10）：沿用 `TaskWizard` 既有模式 `onBeforeRouteLeave` + `window.confirm` + `beforeunload`，不新造弹窗；脏判定 = 未走完向导或作业未到终态。⑥ **全宽未落地（如实记录，不造假）**：`BasicLayout` 内容区无 `max-width` 约束、内容本就全宽，Duplicati 的 `isFullWidthPage`（500→1000）在本项目**无对应切换对象**，待 P14 若引入内容最大宽度再接。双语新增 `restore.flow.*`；清死键 `restore.stepPick`/`stepRun`（`n-steps` 移除后无引用；`stepConfirm` 仍被引用故保留）。护栏 `pages/restore/__tests__/flow-shell.spec.ts` 7 例。
- v1.19（2026-10-23 十九次修订）：**P8 恢复入口页 + 路由重排落地（§4.8.5 R1）**——① `/restore` 改造为**入口页**：标题「从哪里恢复？」+ 当前激活仓库快照行动卡（点击 → `/restore/:id/select-files`）+ 分隔线「或者不使用本地配置」+ 两张备选卡（从配置恢复 → 深链 `/settings#sec-transfer` 复用既有 importConfig；从其他仓库恢复 → `/repo` 切换激活仓库）。② **路由重排**：新增 `/restore/:id/{select-files,options,progress}` 三条路由（name = restore-select-files / restore-options / restore-progress），向导整体由 `pages/restore/index.vue` **迁入** `pages/restore/flow/index.vue`（git mv，**零逻辑改动**，为 P9 外壳与 P10 步骤预留挂载点；三个子路由暂同指向该向导）。③ **`:id` 语义裁决 = 快照 id（非仓库 id）**：后端 E17 `listSnapshots` 走 `current_repo_id` **激活仓库模型、不支持按仓库过滤**，仓库 id 无法用于过滤快照，故入口页直接列当前激活仓库的快照。④ **V-1 兼容**：快照页 `?snapshot=` 直达由入口页**原样转发**到向导首步（`router.replace`，预选语义不变），向导预选源扩为 `?snapshot=` 与路由 `:id` **二选一**（`firstString` 取首个非空串）。新增双语 `restore.entry.*`；护栏 `pages/restore/__tests__/entry.spec.ts` 4 例（真实路由表 name→path 解析 / `?snapshot=` 转发 / 点行动卡进向导 / 空态提示）。另：§4.8.5 文件规划表标记已落地。
- v1.18（2026-10-23 十八次修订）：**A 组裁决全量落地（owner 逐条裁决 A1–A11）**——① **A1 以 §4.5.6 为准**：list 视图改 `TaskRowCard` 行卡（旧 `TaskCard` 删除），D8 行「list 视图经 TaskCard」为错记，已更正；② **A5 Abort 二段落地**：E16 增 `?force=true`（grace=0 直杀）+ 审计 `job.abort`（规范 §3.1 S6 15→16）；③ **A3/A4 菜单补齐 Export（口令弹窗）/ Log（共用 `JobLogDrawer`）**；④ **A6 校验后跳 E22**（`?job=<id>` 自动开抽屉）；⑤ **A9 导入侧按 kind 分流**；⑥ **A2 P7 验收门 4 条 spec 补齐**；⑦ A7 加载态并入 P12、A8 像素 diff 补做存档、A10 端点表登记、A11 A+ 拍板措辞分层。
- v1.17（2026-10-22 十七次修订）：**P7 D8 收口（details 视图接入菜单 + restore/verify 接线）**——补做首批遗漏：① dashboard 此前未监听 `@restore`/`@verify`，TaskCard 菜单「恢复/校验」为**死键**（`createJob('check')` 全库无调用点可证）；现补监听，restore 跳 `/restore`、verify = `createJob('check', { taskId })`（复用 onStart 的 JOB_CONFLICT 口径）。② details 视图操作列由按钮组占位换成 `TaskActionsMenu`（与 TaskCard 同三组口径；列宽 200→100，使列宽总和 = `scroll-x` 920）。新增双语 `task.verifySubmitted`；补 `page-render-smoke` 两条防死键护栏（菜单 select → 断言 `createJob('check', { taskId })`）。另：§4.5.2 D8 行补 details 视图与两端接线口径。
- v1.16（2026-10-22 十六次修订）：**P7 D6/D7/D8 任务卡落地**——TaskCard 加耗时指标 `durationText`（`last_duration_ms` → `HH:MM:SS`/`MM:SS`，null 不渲染）+ 版本数 chip（`snapshot_count`，null 不渲染，`task.snapshotCount` 双语）+ 三点菜单复用 P6 `TaskActionsMenu`（Operations:编辑/恢复 / Advanced:校验备份 / Danger:删除[运行中禁用]，emit 分发 edit/delete/restore/verify 至主页）；新增双语 `task.duration`/`restore`/`verify`/`snapshotCount`；零后端改动（契约 `last_duration_ms`/`snapshot_count` 此前已在 P1 补）。另：§4.5.2 D6/D7/D8 行标记已落地。
- v1.15（2026-10-22 十五次修订）：**P7 D3/D4/D5 工具栏 + 双视图落地**——主页工具栏 SegmentedTabs 视图切换 list/details（localStorage 持久化）+ 时间口径 Relative/Actual 切换 + 排序下拉（仅 list，纯前端 name/last_run/next_run/status）；details 用 NDataTable 九列（含耗时/快照数）；relativeTime 纯函数 + 双语文案；TaskCard 加 timeMode 受时间口径驱动。另：§4.5.2 D3/D4/D5 行标记已落地。
- v1.14（2026-10-22 十四次修订）：**P7 D2 全局状态条挂载落地**——主页顶部按层级①挂载 `GlobalStatusBar`（进行中作业常驻条：全局进度 + Stop + 任务级限速只读展示，砍 pause/resume 与运行中限速入口），消费 `tasks` 列表首个 `running_job_id` 派生 jobId/title/limitText；为驱动 `limitText` 新增后端契约 `TaskCardOut.limit_up`/`limit_down`（纯派生 `task.limit_up_kibps`/`limit_down_kibps`，无查询无迁移）+ 前端 `TaskCard` 同名字段 + 双语 `dashboard.statusBar.limit`/`unlimited`。另：同步 `docs` §4.5.5 契约表（增补 `limit_up`/`limit_down` 派生字段并纠正「仅此两项」表述）、前端同步清单、P7 行口径。
- v1.13（2026-10-22 十三次修订）：**限速按任务级裁决落地**（owner 指令「那就按任务级裁决吧」）。§4.5.3 ② 状态条形态修订为「全局进度 + Stop/Abort 二段」，砍 pause/resume + 限速入口；限速定为**任务级配置**（任务表单设置、创建时生效——备份进程启动时读入 restic argv，运行中不可改），状态条仅保留 limitText 只读展示。§4.5.4 限速行实证纠正：①「仓库级」标注错误——`0001_init.sql:57-58` 在 policies 表、`0002:37-38` 在 backup_tasks 表，均为任务级；②运行中限速入口不可行（restic CLI 无控制通道，Duplicati 实时限速靠自带 throttle 流包装层，本项目 shell out 无此层）；③难度重估：任务表单控件低（接 UI）/ 运行中入口降级（同 pause/resume）。代码：GlobalStatusBar 砍限速按钮 + throttle 事件 + NTooltip/IconGauge 引用 + `statusBar.throttle` 双语键（spec 同步：动作按钮数 2→1）；TaskWizard step① 补 limit_up/limit_down 输入控件（KiB/s，0=不限，含编辑回填与提交接真值——此前硬编码 0）。零后端改动、零契约变更。
- v1.12（2026-10-22 十二次修订）：owner 指令「4.8.6 批次融入 4.7 统一批次，融合后 4.6.6 / 4.5.7 / 4.8.6 都可以删除」。**§4.7 成为全项目唯一执行批次表**，编号重排为 **P1–P14**：P1 `last_duration_ms`+`snapshot_count`／P2 单任务导出／**P3 快照文件搜索端点**／P4 图标集／**P5 令牌层彻底档（分水岭）**／P6 共用形态层／P7 仪表板主页复刻／**P8 恢复入口页+路由重排**／**P9 恢复向导外壳**／**P10 恢复步骤重组（含搜索框）**／**P11 恢复进度页三卡**／P12 逐页重排低风险／P13 逐页重排高风险／P14 全站回归（含恢复模块响应式与 i18n）。删除 §4.5.7、§4.6.6、§4.8.6 三节（§4.8.6 位置改为指向 §4.7 的指针）；§4.5.7 的护栏段升格为 §4.7 的「通用护栏（每批适用）」。批次关系更新：P8–P11 属结构性改造，**不等 P5 令牌层**，可与 P5/P6 并行，视觉打磨在其后复跑。
- v1.11（2026-10-22 十一次修订）：新增 §4.8 第七轮（排序按模块，文档内第七轮批次另计）——恢复模块按 Duplicati 恢复复刻方案定稿。MCP 图谱索引 `duplicati-ngclient`（2670 节点 / 7897 边，restore-flow 作用域 100/215，热点 `#submitRestore` 等）。对标：入口页（备份卡 + 直接恢复 + 从配置）、向导外壳（可跳步 stepper + 全宽/满高）、状态中枢（三表单 + 错误分诊 + 重试）、5 个步骤形态、单档 `-milli` 响应式。差距 R1–R10。**owner 三项裁决**：① 文件树搜索 → 新增后端端点 `POST /api/snapshots/{id}/files/search`（游标同 E18 口径）；② 从备份文件恢复只做到「选已有仓库 + 输密码」，排除 copytotemp / URL 直填 / app-test-url；③ 批次 R1–R6 挂在 §4.7 的 P6 之后，R-1/R-2/R-3 结构性改造不等令牌层可先行。后端契约变更**仅此一项**，其余零后端改动。排除项 6 类（o365/gsuite/diskimage、Hyper-V、Check permissions、copytotemp、URL 直填、逐备份 passphrase）。兼容硬约束：快照页 `?snapshot=` 直达入口不得破坏。
- v1.10（2026-10-22 十次修订）：新增 §4.7 **统一批次表 P1–P9**（owner 指令「把 §4.5.7 与 §4.6.6 批次重排成新的批次表」）——合并后端 B1/B2、图标 L3、令牌层 L1、形态层 L2、主页复刻、两批逐页重排、全站回归；排序原则：后端契约先行 → 令牌层（全局开关）在逐页校准之前 → 图标与形态层是主页复刻前置 → 逐页按风险低到高分两批 → 每批独立可提交。§4.5.7 / §4.6.6 保留原表作历史记录并加指针。**§4.6.4 强度档由「待裁决」定为 owner 裁决 = 彻底**，并补齐彻底档数值口径（圆角 common 12 / small 8 / Card 16 / Button 10 / Tag 999 / Input 10 / Modal 16；控件行高 32/36/40；间距 4/8/12/16/24/32 六档；卡片三档阴影；标签无边框实心五态），数值只落 `tokens.ts` 与 `skin.css`，页面零硬编码、暗色同批。
- v1.9（2026-10-22 九次修订）：新增 §4.6 第六轮——owner 指令「整个项目都要 A+ 视觉效果」，A+ 范围由仪表板单页升级为**全站**。取证结论：`frontend/src/theme/tokens.ts`（101 行，亮/暗两份 `GlobalThemeOverrides`）已是令牌唯一源、`App.vue:15-26` 唯一挂载，且**目前只覆盖 `common`、无组件级覆盖**（Card/Tag/Button 均可新增）→ 全站 A+ 走三层：L1 主题覆盖层（一处改全站，零复制粘贴）、L2 共用形态层（5 类 Naive 无对应组件的形态）、L3 图标层；§4.6.3 明确其与方案 B 的界线（不新建色板、不重绘组件内部、暗色沿用既有双份结构，故**仍是单体系**）；§4.6.4 强度三档（保守/中等/彻底，推荐中等）待 owner 裁决；§4.6.5 风险 S-1..S-4（含沿用 §4.3「桌面零漂移像素 diff」教训）；§4.6.6 批次 S1–S4，后端 B1/B2 不受影响可先行。
- v1.8（2026-10-22 八次修订）：新增 §4.5 第五轮「仪表板（主页）按 Duplicati home 复刻」**方案定稿**（未动代码）——① 对标解剖：四层结构（StatusBar / 双行动卡 / 工具栏 / list-details 双视图）+ 三个 ng-template + 五类状态源 + 单档 `breakpoint(-milli)` 三处响应式；② 差距 D1–D8；③ owner 五项裁决（KPI 保留 / 状态条＝进度+Stop-Abort+限速且砍 pause-resume / 补 `last_duration_ms` / Export 走后端端点 / 视觉三案待拍）；④ **能力可行性实证表并纠正首轮误判**——限速后端已实现（`restic.py:401-404` + DB 字段）仅缺 UI、Abort 可（`cancel()` 已 SIGTERM→grace→SIGKILL）、Verify 即 `check`（全链路已就绪）、Duration 可派生（`jobs.started_at/finished_at`）、**仅 pause/resume 真不可行**；per-task Size 不做（`snapshots.py:12` 既有裁决），以「快照数」顶替 Versions chip；⑤ 数据契约变更仅两项（`last_duration_ms` / `snapshot_count` 派生 + `POST /api/backup-tasks/{id}/export`）；⑥ B1–B6 批次与验收门；⑦ 视觉 A/A+/B 对比与「两套主题治理体系」代价说明，推荐 A+。
- v1.7（2026-10-20 七次修订）：第四轮仓库页「目标位置」模块复刻回填（新增 §4.4）——① 两态整页替换 + 类型驱动字段 + URL 字段组译 + 常用值下拉聚焦即展开 + 测试按钮状态机（新建态「创建并测试」二合一；时序上须 `await nextTick()` 放行 `watch(currentRepoId)` 再测）；② **S3 地址方言修正**（restic 原生 `s3:<endpoint>/<bucket>[/<prefix>]` 为斜杠，原 `s3://bucket/...` 落库即错），退役 URL 区域段与死凭据 `AWS_ENDPOINT_URL`（restic 0.19.1 不读、minio-go 零引用），全 8 类型审计结论 = 仅 s3 一种错误；③ 后端唯一例外——Windows `--reload` 强制 SelectorEventLoop 致子进程 NotImplementedError 的四层防护 + 启动纪律 `--loop none`；④ 审计日志中文化（嵌套 i18n 键 + `te()` 回落 + 详情键值中文渲染）；⑤ 仓库 ID 回显恢复、管理即选中类型、初始化按钮就近且仅未初始化显示。
- v1.6（2026-10-19 六次修订）：E4「设置仓库密码」由状态卡内行内展开面板**改回 NModal 弹窗**，并抽取 `PasswordEntryModal.vue` 共用组件（E44 设置密码与 C4 密钥轮换复用，生成语义各异：E44 后端生成回显 / C4 前端本地强随机）；弹窗 `width` 收口修复瘦长条（原 `repo__modal` 类无 CSS 定义）；E25 缓存维护设置页专区**整体并入仓库页维护区**成为唯一入口（占用详情改纯文本注释口径，删除设置页 `sec-cache` 卡片与锚点项）。后端与红线区零改动（commit eb9cf69）。
- v1.5（2026-10-19 五次修订）：第三轮底座工程回填（§4.3）——aria 盘点（缺口 11 处补齐 + a11y-baseline 静态护栏）与移动端 4 视口走查（4 项实锤修复，含「overflow-x:auto 使 margin 不折叠 → 桌面 +8px」的像素级回归教训）；桌面零漂移以像素 diff 验收（0.000%）；评分更新：响应式 9 / 可访问性 8.5。
- v1.4（2026-10-19 四次修订）：E4 落地（§4.1.2）——对象由「更改密码弹窗」纠正为 repo 页「设置仓库密码」NModal（设置页 E28 本就是行内卡，`git log -S` 取证）；改为状态卡内行内展开面板（API 不变，一次性明文语义保留）；真机以「临时仓库完整生命周期 + 自清理」断言（modal 遮罩 = 0、面板开合、DELETE 三步门）。E1–E6 至此全部落地。
- v1.3（2026-10-19 三次修订）：R1–R4 实施回填（§4.1 逐批落地清单 + 新增 passwordStrength spec 护栏）；§4.2 记录两处**由实施取证推翻方案原文**的裁决——① E1 在 repo 页改为「保存后强制复验」（`testRepo` 读已存配置，先测后存会与凭据录入死锁），② 撤销「E25 前端零入口」误判（`api/settings.ts` + 设置页专区已完整覆盖，repo 维护区为复用式第二入口）。E4（更改密码行内展开）仍未实施，转入待办。
- v1.2（2026-10-19 二次修订）：owner 裁决「实施能让每个功能各层级交互布局都跟 Duplicati 类似吗」讨论落档——新增 G6（repo 维修区 Maintenance 同构分区重排）与 G7（向导骨架对齐：步骤可点击 + 两端页脚 + 校验回流），并入 R1/R3 批次；相似度口径分层（L1 框架 100% 已达成 / L2 共有功能骨架 ~90% 可达 / L3 独有功能按语言延展）记入本记录，复刻上限以「布局范式级相似」为准。
- v1.1（2026-10-19）：按 owner 指令基于后端对齐后的源码实读 + ngclient 8 组组件细读修订。新增 §0 现状基线（功能面对齐矩阵 11 行 + 视觉层 P0–P4 已落地不回退）；新增 §1 差距清单 G1–G5（recover 零 UI 入口 / jobs 筛选漏项 / 连接测试强门禁 / 导出口令强度 / aria-live 摘要）；E1–E6 状态更新（E6 已达成）；排除项补 delete 梯度与 export cmd 双模式裁决；实施顺序重排为 R1–R5；自评口径新增「功能面覆盖度」维度。v1.0 的「hsl(217) 色板 / Inter Tight」条目随视觉批次落地关闭。
- v1.0（2026-10-17）：立项。范围边界（壳/核）、四层换肤结构、§7 交互增强 E1–E6（owner 裁决纳入）、实施顺序与自评口径成文。
