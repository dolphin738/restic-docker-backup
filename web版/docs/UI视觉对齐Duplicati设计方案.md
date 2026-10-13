# UI 视觉对齐 Duplicati 设计方案

> 版本：v1.0（2026-10-13）
> 性质：**纯视觉与布局改版**——不改任何功能、API、路由语义与交互逻辑；所有既有组件（ConfirmGate / JobProgress / VirtualTree / useJobStream）保留复用。
> 证据来源：Duplicati 官方社区 UI 手册（docs.duplicati.com/community-docs）+ Duplicati 源码设计令牌（github.com/duplicati/duplicati `Duplicati/Server/webroot/ngax/less` 与 `styles/{default,dark}.css`，抓取于 2026-10-13）。

---

## 1. 结论

**可行，且改造量比预想小**：本项目前端目前是 Naive UI **裸默认主题**（App.vue 无 themeOverrides、无自定义 CSS、系统字体栈），等于一张白纸——对齐 Duplicati 主要是在主题令牌层做集中注入，不需要拆旧账。

**总体策略：三层分离，避免与选项②返工**

| 层 | 内容 | 风险 | 与选项②关系 |
|---|---|---|---|
| T1 令牌层 | Naive UI themeOverrides（色板/字体/圆角/间距）+ 全局样式 | 低（集中、可回滚） | **先做**，选项②的新页面自动继承 |
| T2 布局导航层 | BasicLayout 重构（顶栏/菜单图标/响应式折叠） | 中（影响所有页面外壳） | **先做**，选项②只改内容区 |
| T3 页面结构层 | 仪表盘任务卡片、新建任务向导等 | 高 | **并入选项②实现**，本方案只给规范不实施 |

## 2. Duplicati 界面事实基线（有据可查）

### 2.1 布局与导航（来源：社区 UI 手册）

- **顶栏**：Duplicati logo（标识 Update Channel）+ **状态栏**（运行中任务进度 / 下次调度时间 / "No scheduled tasks"）+ Pause 按钮 + Throttle 按钮 + 捐赠按钮；
- **左侧主菜单**（高分辨率屏）：Home / Add backup / Restore / Settings / Show log / About / Logout，**每项带图标**；
- **响应式**：低分辨率屏（含移动端）菜单收起为**右上角 Menu 按钮**——手册原话 "The responsive design makes Duplicati easy to use on screens of all sizes"；
- **主内容区**：备份任务卡片列表，每张卡显示调度情况、后端占用空间、可用版本数，卡上可直接触发操作（Run now / Restore files / Edit / Delete / Show log）；
- **底部警告区**：警告/错误显示在屏幕底部，附日志链接 + 关闭按钮；
- 运行进度：顶部进度条含文件数、数据量、**实时上传速度**。

### 2.2 色板（来源：styles/default.css 与 dark.css 编译产物，按出现频次提取）

| 语义 | 亮色 | 暗色 |
|---|---|---|
| 主色（34 处，绝对主导） | `#277db0` | `#2a89c0` |
| 主色 hover/亮阶 | `#4ca4d7` `#65b1dd` | `#5bacdb` |
| 浅蓝填充（选中/信息底） | `#c7e5f6` `#add8e6` | 同亮色系 |
| 正文文字 | `#505050` | `#b0b0b0` / `#f0f0f0` |
| 标题文字 | `#000` | `#f0f0f0` |
| 边框/分隔线 | `#d8d8d8` `#d3d3d3` `#ddd` | `#d3d3d3` `#d8d8d8` |
| 顶栏/表头背景 | `#ededed` | `#1a1a1a` |
| 页面/卡片背景 | `#fff` | `#1a1a1a` 系 |
| 成功/激活绿 | `#568301` `#355001` | `#3f6001` `#81c601` `#609301` |
| 警示黄 | `#ff1` `#fc0` | 同 |

**特征**：Duplicati 是「一个蓝打天下」——单一品牌蓝 `#277db0` 贯穿按钮/链接/选中态/进度条；绿只用于成功语义且偏橄榄绿；几乎无渐变、无大圆角。

### 2.3 字体与圆角

- **字体：Clear Sans**（300 Light / 400 Regular / 500 Medium / 700 Bold 四字重，fonts.less 明载）；图标用 FontAwesome；日志/代码用 monospace；
- **圆角**：`2px` / `3px` / `5px` / `.1em` 为主，`50%` 仅用于圆点——整体是「近方角」的工程风，不是大圆角卡片风；
- 间距体系未在 CSS 显式令牌化，按组件实测为 8/12/16px 量级（**推断值，标注不确定**）。

### 2.4 值得借鉴的交互模式（功能不变，仅视觉/信息架构）

1. **三态文件选择器**：绿勾=选中、红叉=排除、绿方块=部分选中（恢复选文件场景的视觉语言，B1 文件级恢复可直接用）；
2. **"Pick an option" 高级选项模式**：下拉挑选 → 加入可编辑列表（比长表单清爽，策略页高级字段可用）；
3. **状态栏常驻顶栏**：运行中进度/下次调度一眼可见；
4. **About 四 tab**（General/Changelog/Libraries/System info）：版本信息页组织方式；
5. **日志双 tab**（General/Remote）+ 行展开看详情。

## 3. 本项目现状基线（代码核实，2026-10-13）

| 项 | 现状 | 文件证据 |
|---|---|---|
| 主题 | Naive UI 裸默认主题（主色为 Naive 默认青绿 `#18a058`），**无 themeOverrides** | App.vue（NConfigProvider 仅传 darkTheme/locale） |
| 字体 | 系统栈 `v-sans, system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei'` | App.vue `<style>` |
| 布局 | 左侧边栏 200px↔64px（手动 collapse，无断点自适应）+ 顶栏（标题/用户/模式/挂载标签/主题切换/语言/登出） | BasicLayout.vue |
| 导航 | **纯文字菜单，无图标** | BasicLayout.vue menuOptions |
| 暗色 | Naive darkTheme 整体切换 | App.vue + stores/ui |
| 全局样式 | 无 .css 文件，全部 scoped | Glob 验证 |
| 响应式 | 仅 sider 手动折叠，**无移动端适配** | BasicLayout.vue |

## 4. 对齐方案

### 4.1 T1 设计令牌层（Naive UI themeOverrides，集中一处）

新建 `frontend/src/theme/tokens.ts`（唯一令牌源，遵循阈值/常量单一真源纪律），明暗两套：

| 令牌 | 亮色 | 暗色 | 说明 |
|---|---|---|---|
| primary | `#277db0` | `#2a89c0` | Duplicati 品牌蓝原值 |
| primaryHover | `#4ca4d7` | `#5bacdb` | |
| info | `#277db0` | `#2a89c0` | 同主色 |
| success | `#568301` | `#81c601` | Duplicati 橄榄绿 |
| warning | `#b87a00`（hover `#d18f00`，pressed `#966500`） | `#e8a33d`（hover `#f0b45a`，pressed `#c08630`） | **已裁决（2026-10-13）**：Duplicati 仅有关亮黄 `#ff1`/`#fc0`，白底不可读；取加深琥珀，保证 AA 对比度 |
| error | `#c03636`（hover `#d04545`，pressed `#a02a2a`） | `#e06c6c`（hover `#ec8080`，pressed `#c25555`） | **已裁决（2026-10-13）**：Duplicati 无错误红令牌；取与蓝板协调的砖红，AA 对比度 |
| border | `#d8d8d8` | `#454545` | 暗色偏离 Duplicati 的 `#d3d3d3`（该值在 `#1a1a1a` 底上过硬），取 `#454545` 保可读性 |
| 卡片/弹出层底 | `#fff` | `#1a1a1a` | |
| body 文字 | `#505050` | `#b0b0b0` | |
| 标题文字 | `#000` | `#f0f0f0` | |
| borderRadius | `5px`（medium/large：卡片/弹窗/菜单） | 同 | Naive 仅有 borderRadius/borderRadiusSmall 两个键（无 borderRadiusLarge），medium 键承载 5px |
| borderRadiusSmall | `3px`（small：按钮/输入/标签） | 同 | 修订记录见 §8；Duplicati 近方角 |
| 字体 | 见 4.1.1 | | |

**4.1.1 字体（已裁决 2026-10-13：(b) 系统栈先行）**：
- **(b) 系统字体栈近似**（PingFang SC / Segoe UI / Microsoft YaHei，零成本，视觉近似度 ~80%）——**当前采用**；
- (a) 内嵌 Clear Sans（从 Duplicati 仓库取 woff2 四字重，需先核实授权——Clear Sans 为 Intel 发布的开源字体，许可证需法务确认，**待核实**）——若授权确认，后续在 tokens.ts 换 fontFamily 一行即可；
- (c) 开源可商用替代（Noto Sans SC 等，需新增依赖约 100–300 KB）——备选。
与「新增依赖需说明理由并锁版本」纪律的兼容点：选 (a)/(c) 须走依赖评审。

### 4.2 T2 布局与导航层（BasicLayout 重构）

对齐 Duplicati 的「顶栏状态化 + 图标菜单 + 响应式折叠」：

1. **顶栏**：左侧品牌区（app 名 + 简版 logo 字标）→ 中/右**状态栏**（运行中任务实时进度摘要 / 下次调度时间 / 空闲时显示仓库连接状态，替代现有静态标题）；右侧保留主题/语言/登出，模式/挂载标签移入仪表盘或用户菜单（减少顶栏噪声）；
2. **菜单加图标**（已裁决：零依赖内联 SVG）：7 项各配 24×24 stroke 图标（stroke=currentColor）——Naive UI 无内置图标集、不新增 @vicons 依赖；文案仍走 i18n；
3. **响应式**（对齐 Duplicati 低分辩率行为）：用已有 `@vueuse/core` 的 `useMediaQuery('(max-width: 959px)')`；`<960px` 时 sider 自动隐藏，顶栏左侧出 Menu 按钮，点击滑出抽屉式菜单（n-drawer，含品牌区）；`≥960px` 常驻可折叠；
4. ~~底部全局警告区~~ **已裁决（2026-10-13）：不做**，仪表盘异常卡够用，P2 再评估；
5. 内容区间距 16px → 20px，卡片 `size="small"` 统一（与 Duplicati 紧凑工程风一致）；
6. **品牌字标**（已裁决：restic 原版）：sider 品牌区放 restic 官方 logo（`doc/logo/logo.png`，170 KB 圆形插画，字标字体 Lemon 为 OFL 许可）+ 应用名；落地为 `frontend/src/assets/restic-logo.png`；About/部署文档保留 "Powered by restic" 署名，logo 艺术许可以 restic 项目为准（待核实）。

### 4.3 T3 页面结构层（只给规范，实施并入选项②）

- **仪表盘任务卡片**：每卡 = 名称/状态标签/上次运行/下次运行/后端占用/版本数 + 行内 Start 按钮 + 进度条（复用 JobProgress）+ 日志入口——即 R-026-05，由选项②实现，本方案提供卡片视觉规范（白底/1px 边框/3px 圆角/无阴影，对齐 Duplicati 扁平风）；
- **新建/编辑任务向导**：n-steps 五步，步骤条用主色、当前步实心——即 R-026-06；
- **策略页高级字段**：评估「Pick an option」模式替代部分长表单项（P2 可选）；
- **文件选择三态**：B1 文件级恢复的 VirtualTree 选择态对齐 Duplicati（绿勾/红叉/绿方块语义）——B1 随 P2 同批；
- **登录/首启页**：令牌层自动生效即可，不单独设计。

### 4.4 响应式适配总则

- 断点：`≥960px` 桌面（sider 常驻）/ `<960px` 移动（sider 收抽屉）；
- 表格：n-data-table 加 `scroll-x`；快照/文件树虚拟滚动**保持不动**（红线兼容项）；
- 表单：n-form 默认 `label-width` 响应式（`label-placement="top"` 于窄屏）；
- 恢复向导/任务卡片：n-grid `span="24 24 12"` 式栅格降级；
- 验证方式：Chrome DevTools 设备模拟 375/768/1440 三档走查（QA 阶段执行）。

## 5. 红线与规范兼容核对

| 约束 | 影响 |
|---|---|
| 组件库不换 | 仍为 Naive UI（themeOverrides 是官方机制），不引入重叠选型 |
| i18n | 菜单图标不影响文案；所有新文案走 i18n（若 (a)/(c) 字体方案新增构建步骤，与 i18n 无关） |
| 新增依赖 | 仅字体方案 (a)/(c) 涉及，须走依赖评审 + 锁版本；(b) 零新增 |
| 虚拟滚动/SSE/ConfirmGate | 不动（红线 A4/A6、交互规范） |
| 单次合入 ≤400 行 | T1+T2 预计 ~250 行，可单次合入 |
| 构建相关 | 若最终镜像需含字体文件（方案 a/c），改 Dockerfile 时跑 `python scripts/check_dockerignore.py` |

## 6. 实施计划（建议顺序）

| 步骤 | 内容 | 依赖 | 预计 |
|---|---|---|---|
| S1 | `theme/tokens.ts` + App.vue 接入 themeOverrides + 全局字体/间距微调 | 无 | 半天 |
| S2 | BasicLayout 重构（顶栏状态化/菜单图标/响应式抽屉） | S1 | 1 天 |
| S3 | 全页面视觉走查 + 细节修正（按钮尺寸/表单密度/标签色） | S2 | 1 天 |
| S4 | 响应式三档验证 + typecheck/test/build | S3 | 半天 |
| S5 | 选项②实现时套用 §4.3 规范 | 选项② SOP | 随 P2 |

S1–S4 为独立可交付单元（约 3 人日），与选项②无代码冲突（只碰 theme/ + App.vue/BasicLayout.vue；选项②碰 dashboard/pages/restore + 后端）。

## 7. 裁决记录（2026-10-13 全部关闭）

1. **字体方案** → (b) 系统栈先行；(a) Clear Sans 内嵌待授权核实后在 tokens.ts 单行切换；
2. **warning/error 色值** → warning `#b87a00`/`#e8a33d`、error `#c03636`/`#e06c6c`（AA 对比度，见 §4.1 表）；
3. **底部全局警告区** → 不做，仪表盘异常卡够用，P2 再评估；
4. **菜单图标** → 零依赖内联 SVG（Naive 无内置图标集，不新增 @vicons）；
5. **品牌字标** → restic 原版 logo（`doc/logo/logo.png`），sider 品牌区 + 署名 "Powered by restic"。

## 8. 实施与复验记录（2026-10-13）

**T1/T2 + A 档小项已交付并通过 QA 独立复验（PASS，零 Blocker）**：

| 项 | 状态 |
|---|---|
| T1 令牌层（theme/tokens.ts + App.vue 接线） | 已落地；QA 运行时挂载实证生效（`--n-color: #277db0`/`#2a89c0`） |
| T2 布局层（BasicLayout：logo 品牌区/菜单图标/959px 抽屉/顶栏状态标签/间距 20px） | 已落地；QA matchMedia mock 三态验证 |
| A 档 3 小项（恢复页默认最新/快照直达/i18n） | 已落地；QA 5/5 行为矩阵（含 API 乱序对抗） |
| 圆角修订（QA 建议项） | 已按 §4.1 口径落地：`borderRadius:5px` / `borderRadiusSmall:3px`（Naive 无 borderRadiusLarge 键，medium 键承载 5px；tokens.ts 双主题同步，typecheck 通过） |
| Nit 3（状态标签 tooltip 透出 last_error） | 已修复（BasicLayout.vue，未连接时 hover 显示后端原文，typecheck 通过） |

**遗留项（不阻塞，记录在案）**：

| # | 内容 | 处置 |
|---|---|---|
| L1 | `?snapshot=` 在 /restore 页内二次导航不重生效（组件不重建） | 已知限制；当前唯一入口总是全新挂载，实际不可达，不修 |
| L2 | 用户手动清空快照选择后再 refetch 会重新套用默认最新 | 已知限制；非典型流，行为可辩护，不修 |
| L3 | ~~P2 候选：E30 仓库状态增 `configured` 字段，区分「未配置」（warning）与「连接失败」（error）分色~~ **已落地（2026-10-14）**：E30 响应增 `configured`，仪表盘状态卡按未配置=warning / 失败=error 分色 | QA 意见，已交付 |
| L4 | Clear Sans 授权核实（字体方案 a） | 待核实，单行令牌切换 |
| L5 | T3 页面结构层（任务卡片/向导等） | 并入选项②（R-026-05/06）实施，本文 §4.3 为规范 |

**未完成部分与选项②的边界**：T3 不单独排期；视觉令牌（T1）与布局（T2）已就位，选项②新页面自动继承。
