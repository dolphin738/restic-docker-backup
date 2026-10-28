# Duplicati 三条核心流程分析（面向 Web 版改造的对齐评估）

> 依据：Duplicati 官方文档（docs.duplicati.com）三个 Getting Started 页面，抓取于 2026-10-12。
> 页面：① Set up a backup in the UI ② Running a backup ③ Restoring files。
> 用途：评估其「配置 → 运行 → 恢复」三段式流程能否作为本项目 Web 版界面操作流程的对齐模板。
> 配套方案文档：《对齐Duplicati的改造方案.md》（已归档 `docs/archive/`，2026-10-15；结论见 Web版PRD.md v1.4 与架构设计文档 §13）。
>
> 📦 **本文已归档（2026-10-29）**：本文为**外部资料取证底稿**，非本项目决策源。结论已上位化至《Web版PRD.md》v1.4（§3 旅程 I/H + 需求池 R-013/015/016/018/026/027）与《架构设计文档.md》§13 演进位；配套方案亦已归档。**本文移入 `docs/archive/` 仅供追溯，不再作为设计依据。**
>
> ⚠️ **偏差注记（2026-10-29 代码复核）**：§五「三处不能照搬的安全语义」已有 **3 条被后续实现推翻/放宽**，照本文理解现状会误判：
> 1. **仓库密码「永不进表单/API」**——放宽为「**永不回显**」：现有 `POST /api/repos/{repo_id}/password`（`repos.py:1361`，表单输入或后端生成并一次性回显）与 `POST /api/repos/rotate-key`（`ops.py:731/743-772`）。红线 S3 现行定义见《代码审查规范》§3.1：凭据不得出现在 API **响应**/日志/事件/审计，只回 `has_value` + `source`。
> 2. **「P1 仅时间戳子目录，原位恢复留待 P2（R-015）」**——**R-015 已落地**：`RestoreRequest.overwrite_mode`（`api_schemas.py:814`，仅 `never`/`always`）、E20 覆盖预览（`api_schemas.py:827+`）、前端 `i18n/locales/zh-CN.ts:1134-1162`、`ConfirmGate.vue` A7 单次确认、`api/__tests__/restore.spec.ts:111` 用例。
> 3. **「本项目为单仓库单备份集」**——**多仓库已落地**：迁移 `0002_multi_backup_tasks` / `0003_multi_repos` / `0006_task_mirror_repos`，架构 §13 演进位已兑现。
> 4. 另：本项目**已无独立 Restore 页**（恢复并入快照浏览页，`frontend/src/router/index.ts` 无 `/restore`），§二阶段 3 的 UI 入口描述**仅适用于 Duplicati 原产品**，不适用于本项目现状。
> 未推翻部分：§一 ~ §四为 Duplicati 官方文档事实摘录（2026-10-12 抓取），仍有效。

---

## 一、逐页核心内容与关键概念

### 1. Set up a backup in the UI（设置备份任务）

**核心内容**：通过 UI 向导创建备份任务。入口为首页 **Add backup → Add a new backup**（已有配置可走 import/export 导入）。自 **Duplicati 2.4.0.1** 起向导分 5 步（更早版本「存储目标」在「源数据」之前）：

| 步骤 | 关键概念/术语 | 要点 |
|---|---|---|
| ① Basic configuration | name / description、encryption、passphrase | 名称仅用于列表区分；加密有轻微性能开销但推荐；内置**密码生成器 + 强度计**；官方警告：**密码丢失则无法恢复任何数据**；选择不加密则须自行确保存储安全 |
| ② Source data | source picker、filters | 选文件夹即包含全部子项；可在 UI 取消勾选做排除；高级用法用过滤器（include/exclude 规则） |
| ③ Storage destination | destination、Test destination | 最具技术性的一步，各后端配置不同（详见 destination overview 页）；⚠️ 每个备份**必须使用独立文件夹**，两个备份共用同一目标文件夹会互相破坏；填完建议点 **Test destination** 做连接测试 |
| ④ Schedule | 内置调度器 | 启用后自动运行；关闭则纯手动，可用 ServerUtil（CLI）按需触发 |
| ⑤ Retention and miscellaneous | retention、Smart backup retention、remote volume size | 保留策略自动删除旧版本；Smart backup retention 保留一份每日备份、越往前越稀疏；远程卷大小需在云存储/网络带宽间权衡 |

### 2. Running a backup（运行备份）

**核心内容**：如何在自动计划之外运行备份。

- 官方建议保留自动调度（避免备份过期遗忘）；
- 手动运行：UI 中点该备份的 **Start** 按钮；
- 无 UI 场景用 **ServerUtil** 从命令行触发；
- 运行中顶部区域变为**进度条**；**首次运行最慢**（处理所有文件与文件夹），后续只处理新增/变更数据（增量 + 去重）；
- 完成后视图切换并显示该次备份的**详情**。

关键概念：schedule / manual run（Start）/ progress bar / first full run vs. incremental / ServerUtil / backup details。

**本页未覆盖**：日志查看入口、通知与邮件上报、备份结果校验（verify/test）——日志入口（备份的 **Reporting → Show log**，General 看操作结果、Remote 看后端事件）与邮件上报（**Settings → Default options**，如 `send-mail-level=all`）经文档问答机制从其他页面确认；警告/错误还会显示在主屏底部并链接日志；TrayIcon 可弹桌面通知。

### 3. Restoring files（恢复文件）

**核心内容**：所有恢复从 **Restore 页**开始，提供三条途径：

| 途径 | 适用场景 | 关键点 |
|---|---|---|
| 从现有配置恢复 | 备份配置还在本机 | 最快；直接查**本地数据库（local database）**；本地库缺失时需先从远端存储构建部分库，较慢 |
| 从配置文件恢复 | 持有导出的配置文件 | 提供配置文件避免手填目标信息；配置文件若加密需输入其密码；⚠️ **配置文件密码不一定等于备份数据密码**；需多次恢复时更推荐先导入配置 + 重建本地数据库 |
| 从备份文件直接恢复 | 在非原机器上恢复 | 只需 **destination details + passphrase**；先 **Test connection**；未加密则密码留空；⚠️ 无密码无法恢复加密备份；因需下载处理更多数据，**不推荐日常使用** |

**选择文件**：连接后自动查找所有已完成备份，**默认选中最新版本**，下拉切换版本（version），点选文件/文件夹 → Continue。

**恢复选项**：

- 位置：默认恢复到**原始位置**，也可 **restore to another destination**（先落到别处检查再替换）；
- **覆盖行为**：目标文件夹非空时可在文件名后附加 **restore timestamp** 保留多版本（便于同目录对比多个版本）；
- **权限默认不恢复**（原机器的用户/组在目标机可能不存在，恢复权限可能导致文件无法访问）；
- Submit 后显示**恢复摘要页**。

---

## 二、端到端完整工作流（对应 UI 入口）

**阶段 1 · 配置（首页 → Add backup → Add a new backup，5 步向导）**
1. 基础配置：填名称/描述 → 选加密方法 → 用内置生成器生成密码并妥善保存 → Continue；
2. 源数据：source picker 选文件夹（含子项），必要时用过滤器精细排除 → Continue；
3. 存储目标：按后端类型填连接信息 → 点 Test destination 验证 → Continue；
4. 计划：启用调度器（或关闭改手动）→ Continue；
5. 保留与杂项：设 Smart backup retention、定 remote volume size → Submit。

**阶段 2 · 执行（备份列表页）**
1. 配置完建议立刻点 Start 做首次手动运行（不必等计划）；
2. 观察顶部进度条——首次最慢属预期；后续调度器自动跑，也可手动 Start 或 ServerUtil 触发；
3. 完成后看备份详情；日志：备份的 Reporting → Show log（General/Remote）；
4. 通知：主屏底部警告/错误链接、TrayIcon 桌面通知、Settings → Default options 邮件上报。

**阶段 3 · 恢复（导航 Restore 页）**
1. 选入口：本机有配置 → 列表 Restore；有配置文件 → Restore from configuration；换机器 → Direct restore from backup files；
2. 选版本（默认最新）+ 文件/文件夹 → Continue；
3. 恢复选项：定目标位置；非空目标保留多版本用 restore timestamp；权限默认不恢复；
4. Submit → 恢复摘要页。

---

## 三、初学者易踩坑与关键前提条件

| # | 坑点/前提 | 文档依据 |
|---|---|---|
| 1 | **密码丢失 = 数据永久不可恢复**，没有后门 | 明确警告框 |
| 2 | **每个备份任务必须用独立的目标文件夹**，共用会互相破坏 | 明确警告框 |
| 3 | 不加密可以，但存储安全完全自负 | 明确 |
| 4 | 导出配置文件的密码 ≠ 备份数据密码 | 明确 |
| 5 | 直接恢复（direct restore）数据开销大，不适合常规使用 | 明确 |
| 6 | 权限默认不恢复——防止原机 UID/GID 在目标机不存在导致文件不可访问 | 明确 |
| 7 | 恢复到非空文件夹想保留多版本，用 restore timestamp | 明确 |
| 8 | 首次备份最慢（全量处理），不要误判为卡死 | 明确 |
| 9 | 低于 2.4.0.1 的版本向导步骤顺序不同 | 明确（hint 框） |
| 10 | 关掉调度器后无任何自动机制，需手动 Start 或 ServerUtil | 明确 |
| 11 | ⚠️ 源路径与目标存储不能重叠——**这三页均未提及**，属一般最佳实践，标注不确定 |
| 12 | ⚠️ 路径写法差异（盘符/UNC/大小写/尾部斜杠）——**三页均未提及**，标注不确定 |

## 四、文档未明确或存在歧义之处

1. 「运行备份」页完全没有日志/通知/校验内容；日志与通知细节来自文档其他页面（经问答机制确认）；
2. Smart backup retention 只有一句话描述，无具体保留颗数/时长规则；
3. remote volume size 只给定性建议并转向论坛帖；
4. 加密方法具体有哪些选项未列出；
5. Schedule 步骤未展开调度粒度配置细节；
6. 保留策略删除的粒度（版本 vs 底层去重块）未说明；
7. "restore to another destination" 与 restore timestamp 组合时的相对路径处理描述较简；
8. 直接恢复时如何识别"存在哪些备份"未展开；
9. 版本顺序差异只提示 2.4.0.1 一个分界点；
10. 源/目标重叠、路径写法两题在三页中完全缺失。

## 五、与本项目 Web 版的相关性速览

**结论：可行，且高度同构。** Duplicati 的「5 步配置向导 → Start/调度运行 + 进度/日志/通知 → 三入口恢复（版本选择 + 时间戳语义 + 默认不覆盖）」三段式，与本项目 PRD 的「仓库配置 → 备份源 → 策略调度 → 手动/定时备份（SSE 进度）→ 快照浏览 → 时间戳子目录恢复」骨架一一对应。三处必须保留本项目更严的安全语义、不能照搬 Duplicati：

1. **加密密码**：Duplicati 在 UI 生成备份加密密码；本项目仓库密码走密码文件挂载、永不进表单/API（红线 S3、US-B1 AC4）——向导中对应步骤只展示「密码文件挂载状态」；
2. **覆盖语义**：Duplicati 允许恢复到原始位置/任意目标；本项目 P1 仅时间戳子目录（红线 S7），Duplicati 的 restore timestamp 与本项目时间戳子目录语义同源，可在 UI 文案上对齐「永不覆盖」表述；原位恢复留待 P2 覆盖比对确认流（R-015）；
3. **多备份 vs 单仓库**：Duplicati 支持多备份任务列表；本项目为单仓库单备份集，Duplicati 的「备份列表 + 每项 Start」收敛为「仪表盘/仓库页的立即备份 + 任务历史」。

详见《对齐Duplicati的改造方案.md》（已归档 `docs/archive/`）。
