# UI 全量改造方案（对齐 Duplicati ngclient 设计语言）

> 版本：v1.14（2026-10-22 十四次修订：**P7 D2 全局状态条挂载落地**——主页顶部按层级①挂载 `GlobalStatusBar`（进行中作业常驻条：全局进度 + Stop + 任务级限速只读展示，砍 pause/resume 与运行中限速入口），消费 `tasks` 列表首个 `running_job_id` 派生 jobId/title/limitText；为驱动 `limitText` 新增后端契约 `TaskCardOut.limit_up`/`limit_down`（纯派生 `task.limit_up_kibps`/`limit_down_kibps`，无查询无迁移）+ 前端 `TaskCard` 同名字段 + 双语 `dashboard.statusBar.limit`/`unlimited`。v1.13（2026-10-22 十三次修订：**限速按任务级裁决落地**——owner 指令「那就按任务级裁决吧」：§4.5.3 ② 状态条形态去「限速入口」（砍 pause/resume + 限速入口；限速 = 任务级配置、任务表单设置、创建时生效，状态条仅 limitText 只读展示）；§4.5.4 限速行纠正「仓库级」误标（0001 policies 与 0002 backup_tasks 两处均任务级）并重估难度（表单控件低 / 运行中入口不可行——restic CLI 无控制通道）；GlobalStatusBar 砍限速按钮与 throttle 事件、删 IconGauge 与 statusBar.throttle 双语键（spec 同步：按钮数 2→1）；TaskWizard step① 补 limit_up/limit_down 输入控件（KiB/s，0=不限，含编辑回填与提交接真值——此前恒 0）。零后端改动、零契约变更。v1.12（2026-10-22 十二次修订：批次**融合定稿**——§4.7 升级为**全项目唯一执行批次表 P1–P14**（恢复模块的搜索端点 P3、入口页+路由 P8、向导外壳 P9、步骤重组 P10、进度页三卡 P11 并入，响应式与 i18n 并入 P14）；§4.5.7（B1–B6）、§4.6.6（S1–S4）、§4.8.6（R1–R6）三处旧批次表**删除**，§4.8.6 位置改为指向 §4.7 的指针；§4.7 新增「通用护栏」小节（承接 §4.5.7 的护栏段）。v1.11 为第八轮 §4.8「恢复模块按 Duplicati 恢复复刻」方案定稿——owner 三项裁决（文件树搜索新增后端端点 / 从备份文件恢复只做到「选已有仓库 + 输密码」/ 批次 R1–R6 挂在 P6 之后），含契约变更（仅 `POST /api/snapshots/{id}/files/search` 一项）、组件与文件规划、排除项 6 类、风险 V-1..V-4；v1.10 为第七轮 §4.7——**统一批次表 P1–P9**（owner 指令重排，合并取代 §4.5.7 的 B1–B6 与 §4.6.6 的 S1–S4）；§4.6.4 强度档 owner 裁决 = **彻底**并补齐数值口径（圆角/控件密度/间距节奏/卡片阴影/标签五态）；v1.9 为第六轮 §4.6——A+ 由「仪表板单页」升级为**全站**（owner 指令「整个项目都要 A+ 视觉效果」）：关键发现 `theme/tokens.ts` 已是令牌唯一源 + `App.vue:15-26` 唯一挂载点，全站 A+ 走「L1 主题覆盖层 / L2 共用形态层 / L3 图标层」三层实现，一处改全站且**仍属单体系**（与方案 B 的界线见 §4.6.3）；含强度三档待裁决与 S1–S4 批次。v1.8 为第五轮「仪表板（主页）按 Duplicati home 复刻」方案定稿 §4.5——owner 五项裁决 + 能力可行性实证（纠正首轮「无 pause/resume/abort/限速」误判）+ 数据契约变更 + B1–B6 批次与视觉 A/A+/B 三案；v1.7 第四轮仓库页「目标位置」模块复刻；v1.6 E4 二次修订；v1.5 第三轮 aria + 移动端；v1.4 E4；v1.3 R1–R4；v1.2 G6/G7；v1.1 2026-10-19；v1.0 2026-10-17 立项）
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

| # | 模版 | 本项目现状（`pages/dashboard/index.vue` 454 行） | 处置 |
|---|---|---|---|
| D1 | 双行动卡 | 无（顶部为 KPI 四格） | 新增行动卡行（→新建任务 / →恢复） |
| D2 | StatusBar 全局条 | 无 | 新建降级版（见 4.5.3 ②） |
| D3 | 视图切换 + localStorage 持久化 | 仅卡片列表 | 按钮组 + localStorage + **新增表格视图** |
| D4 | 时间 Relative/Actual | 仅绝对时间 | 新增相对时间格式化 + 切换菜单 |
| D5 | 排序下拉 | 无 | 纯前端排序（后端 `listBackupTasks(limit,offset)` 无 order 参数） |
| D6 | 卡片 6 项指标 | 仅 `last_run_at` / `next_run_at` | duration 补后端（③）；size 走替代口径（4.5.4） |
| D7 | 类型 chip / 版本数 chip | 无 | 仓库类型 chip；版本数 = 该任务快照数（4.5.4） |
| D8 | 三点菜单 7 项 | 编辑/删除 | 见 4.5.4 能力表（Verify 可直接接、Export 新端点、Commandline/Database 隐藏） |

#### 4.5.3 owner 裁决（2026-10-22）

| # | 裁决 |
|---|---|
| ① | **KPI 四格保留**（Duplicati 无 KPI 带，本项目保留在行动卡下方） |
| ② | 状态条按「**全局进度 + Stop/Abort 二段**」做，**砍 pause/resume + 限速入口**（2026-10-22 补充裁决：限速 = **任务级配置**，任务表单设置、创建时生效；状态条不设运行中入口，仅 limitText 只读展示） |
| ③ | Duration **补后端 `last_duration_ms` 派生字段** |
| ④ | Export **走后端端点**（单任务配置导出，复用 configTransfer 加密打包管线） |
| ⑤ | 视觉方案 A / A+ / B 三选一，**推荐 A+**（见 4.5.8，待 owner 最终拍板） |

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
| `TaskCardOut.snapshot_count` | 按 `task.tag` 分组的快照数。实现：在任务列表接口内**取一次快照列表**（复用 E30 30s 服务端缓存）按 tag 分组计数；缓存不可用则 `null` | 同上；不得退化为 N 次 `stats` 调用（违反 `snapshots.py:12` 裁决） |
| `TaskCardOut.limit_up` / `limit_down` | 任务级限速（KiB/s，0=不限）。**纯派生**：直接抄 `task.limit_up_kibps` / `limit_down_kibps`，无新查询、无迁移。用途：状态条 `limitText` 只读展示（owner 2026-10-22 任务级裁决——运行中不可改，前端不设入口） | 同上；只读展示，不新增修改端点 |
| `POST /api/backup-tasks/{id}/export` | 单任务配置导出：复用 `config_transfer` 加密打包管线，口令入参同 `exportConfig`；返回加密包 | S6 埋 `AUDIT_CONFIG_EXPORT`；S3 响应不含凭据明文；i18n 双份 |

前端同步：`api/types.ts` 的 `TaskCard` 加 `last_duration_ms` / `snapshot_count` / `limit_up` / `limit_down` 字段、`api/backupTasks.ts` 加 `exportBackupTask()`、`i18n` 双语加键（`dashboard.statusBar.limit` / `unlimited` 等）。

#### 4.5.6 组件与文件规划

| 文件                                         | 职责                                                                       |
| ------------------------------------------ | ------------------------------------------------------------------------ |
| `components/dashboard/ActionCard.vue`      | 行动卡（插图 + 标题 + 描述 + 右按钮；整卡可点 + Enter/Space；`router` 跳转）                   |
| `components/dashboard/GlobalStatusBar.vue` | 全局条：当前作业进度（SSE）+ Stop/Abort 二段 + 限速只读展示（任务级派生；无运行中入口，owner 2026-10-22 裁决） |
| `components/dashboard/TaskRowCard.vue`     | list 视图卡片行（名称 + 类型 chip + 快照数 chip / 指标区 / 操作区）                          |
| `components/dashboard/TaskActionsMenu.vue` | 三点菜单（Operations / Advanced / Reporting 分组 + divider；loading/success 图标态） |
| `components/icons/HomeIcons.ts`            | **新增 ~30 个内联 SVG 图标**（见 4.5.9 风险 R-1）                                    |
| `pages/dashboard/index.vue`                | 重写：KPI 带 → 行动卡行 → 工具栏（视图/时间/排序）→ 双视图 → 空态/加载态                            |
| `utils/__tests__/relativeTime.spec.ts`     | 相对/绝对时间格式化单测                                                             |

#### 4.5.8 视觉方案三选一（待 owner 拍板，推荐 A+）

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
| R-2 | 30 个任务 × 快照计数若退化为 N 次仓库调用会拖垮列表 | 强制「一次快照列表 + 按 tag 分组」，复用 E30 30s 缓存 |
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

| 批次 | 内容 | 依赖 | 验收门 | 风险 / 对策 |
|---|---|---|---|---|
| **P1** | 后端：`TaskCardOut.last_duration_ms`（最近终态 backup job 的 `finished_at - started_at`）、`snapshot_count`（一次快照列表按 tag 分组计数，复用 E30 30s 缓存） | — | `pytest` 全绿 + `ruff`；新增单测覆盖「无历史 job → null」「快照缓存不可用 → null」 | A1 红线：须经 `app/db/repositories/` 新查询，路由不直写 SQL；禁止退化成 N 次 `stats`（`snapshots.py:12` 既有裁决） |
| **P2** | 后端：`POST /api/backup-tasks/{id}/export` 单任务配置导出（复用 config_transfer 加密管线）+ 审计埋点 + 导出包带 `kind=backup-task` 标记 | — | `pytest` + 审计留痕断言 | S6 埋 `AUDIT_CONFIG_EXPORT`；S3 响应不含凭据明文 |
| **P3** | 后端：`POST /api/snapshots/{id}/files/search` 快照内文件搜索（`{query, limit, cursor}` → `{items, next_cursor}`，游标与 E18 同口径） | — | `pytest` + 分页上限单测 | 上限沿用 `SNAPSHOT_FILES_MAX_LIMIT`（`constants.py:213`）；过滤下推，**禁止前端全量拉取再过滤** |
| **P4** | L3 图标集：`components/icons/` 扩展 ~30 个统一风格 SVG（统一描边 1.5 / 尺寸 20），含主页与恢复向导所需全部图标 | — | `typecheck` + `naive-imports` 护栏 + 目视 | 无图标库，全自绘；集中在单一文件，禁止散落 |
| **P5** | L1 令牌层**彻底档**：`tokens.ts` 圆角（common 12 / small 8 / Card 16 / Button 10 / Tag 999 / Input 10 / Modal 16）+ 控件密度（行高 32/36/40）+ **间距节奏 4/8/12/16/24/32** + 卡片三档阴影 + 标签无边框实心五态；**亮/暗同批改** | P4 | 全站基线截图存档 → 改后**桌面零漂移像素 diff** + 亮/暗双模式截图 | 风险最高的一批：改前必须先存档全站基线；暗色值必须同批给（否则暗底黑边）；间距常量只落 `tokens.ts` + `skin.css`，页面零硬编码 |
| **P6** | L2 共用形态层：行动卡 / 行卡片 / 分段控件 / 菜单分组 / 全局状态条 → 提升为**全站共用组件** + `theme/skin.css` | P5 | `typecheck` + 新 spec（分段选中态、菜单分组渲染） | S-4：必须是共用组件 + 单一 skin 文件，**禁止逐页各写一份**（否则退化成第二套） |
| **P7** | **仪表板主页复刻**（§4.5）：KPI 带保留 → 行动卡行 → 工具栏（视图切换 localStorage / 时间 Relative-Actual / 排序）→ list-details 双视图 → 空态/加载态 → 状态条（全局进度 + Stop/Abort 二段；限速仅只读展示）→ 三点菜单（Restore / Export / Delete / Verify / Log） | P1 P2 P4 P6 | `typecheck` + 新 spec（视图持久化、时间切换、排序三态、Stop→Abort 二段） | 限速 = 任务级表单配置（TaskWizard 控件，创建时生效；运行中入口已裁决砍掉）；Abort = `cancel()` grace=0；Verify = `createJob('check')` |
| **P8** | **恢复入口页 + 路由重排**（§4.8.5）：`/restore` 改造成入口页；新增 `/restore/:id/{select-files,options,progress}` | — | `typecheck` + **快照页 `?snapshot=` 直达入口实测**（V-1） | 路由重排不得破坏 `snapshots/index.vue:118-123` 的预选直达 |
| **P9** | **恢复向导外壳**（§4.8.5）：可跳步步骤条（键盘可达）+ `inactive` 态 + 全宽/满高切换 + 离开确认 | P8 | 新 spec（跳步/确认/全宽类） | V-2：跳步前校验，未过步骤置 `inactive`（沿用模版语义） |
| **P10** | **恢复步骤重组**：select-files（版本下拉补 size/label + **搜索框**：结果计数 / Clear / spinner）+ options 四分区（恢复到哪 / 已存在文件 / Metadata / 高级）+ 页脚两端对齐 | P3 P9 | 新 spec | 搜索框依赖 P3 端点；无端点时该项顺延 |
| **P11** | **恢复进度页三卡 + 完成态圆环**（进度 / 通知 / 日志）+ SSE 终态断言 | P9 | 新 spec | V-4：复用既有通知与 job 日志 API，**不新造数据源** |
| **P12** | 逐页重排**第一批（低风险）**：snapshots → jobs → audit → reports → notifications | P5 P6 | `typecheck` → `vitest` → `NODE_OPTIONS="" pnpm run build` 三步分跑 + 逐页像素 diff | 表格页需复核 `:scroll-x` 阈值（间距变大后可能溢出） |
| **P13** | 逐页重排**第二批（高风险）**：repo（自定义 CSS 230 行）→ settings（133 行）→ TaskWizard（58 行）→ login / bootstrap | P12 | 同上 + 向导 5 步与 ConfirmGate 真机断言 | 自定义 CSS 最厚的两页，逐块校准；沿用 §4.2 教训（门禁不得因重排失效） |
| **P14** | 全站回归：三步门 + **全站逐页桌面零漂移像素 diff** + 移动端 4 视口（1440/960/768/390）走查 + 恢复模块响应式（`-milli` 等价）与 i18n 双语 + 真机 E2E 冒烟 | P13 | 全绿；移动端 4 项断言（参照 §4.3 口径） | 沿用 §4.3 教训：`overflow-x:auto` 会使 margin 不折叠致桌面 +8px 漂移，必须 diff 验收 |

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
| `pages/restore/index.vue` | **改造为入口页**（R1）：备份/仓库列表行动卡 + 直接恢复 + 从配置恢复 |
| `pages/restore/flow/index.vue`（新） | 向导外壳：可跳步步骤条 + 全宽/满高 + 离开确认（R4/R10） |
| `pages/restore/flow/select-files.vue`（新） | 版本下拉（size/label）+ 搜索框 + 文件树（R5/R6） |
| `pages/restore/flow/options.vue`（新） | 四分区（恢复到哪 / 已存在文件 / Metadata / 高级）（R7） |
| `pages/restore/flow/progress.vue`（新） | 三卡 + 完成态圆环（R8/R9） |
| `router/index.ts:77-80` | 路由重排：`/restore` → 入口；`/restore/:id/select-files|options|progress` → 向导 |

> ⚠️ 兼容约束：`snapshots/index.vue:118-123` 的「恢复此快照」走 `{ name: 'restore', query: { snapshot } }`，路由重排后**必须保持该入口可用**（预选语义不变）。

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

- v1.14（2026-10-22 十四次修订）：**P7 D2 全局状态条挂载落地**（见上版本头说明）；同步 `docs` §4.5.5 契约表（增补 `limit_up`/`limit_down` 派生字段并纠正「仅此两项」表述）、前端同步清单、P7 行口径。
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
