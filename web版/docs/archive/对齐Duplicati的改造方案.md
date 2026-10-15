# 对齐 Duplicati 的 Web 版改造方案

> **已归档（2026-10-15）**：A 档小项（恢复页默认最新/快照直达/i18n）、A1 向导主体（并入选项②「新建/编辑备份任务」流，US-026-01–10）、A3 立即备份入口、B1 文件级恢复（R-027）均已交付；C 档术语对齐项（R-013/R-015/R-016/R-018）与多仓库演进位（R-023）结论归入《Web版PRD.md》v1.4 与《架构设计文档.md》§13。归档前置：P2 相关项全部交付（唯一暂缓项 R-022 不属本文承诺范围，见 PRD Q14）。本文仅供追溯，不再更新。

> 依据：《Duplicati三流程分析.md》（同目录，2026-10-12）+ 本仓库代码现状核实（router/index.ts、pages/*、backend/app/routers/*、Web版PRD.md v1.1）。
> 性质：**操作流程对齐方案**——只编排界面流程与文案，不改数据模型、不放松任何红线（规范 §3.1）。
> 状态：已归档（2026-10-15，见文首标注）；本文档不含实现代码。

> **修订记录（2026-10-13，owner 裁决后）**：
> ① 待裁决项 1 **已裁决：新增 `/setup` 向导**，但**不作为独立单备份集流程实施**——A 档主体（向导/checklist/立即备份）**并入选项②「轻量多备份任务」**（同仓库 N 个任务，各绑源集合+tag+cron+keep_*，已立项随 P2 交付），作为「新建/编辑备份任务」流实现；先按全局源集合做向导再返工是最贵路径，故不先行。
> ② A 档与任务模型**无关**的小项可**立即实施**（零依赖）：恢复页默认选最新版本、快照页直达恢复入口、i18n 文案对齐。
> ③ B1 文件级恢复归期**随 P2 打包裁决**（与选项②同批）。
> ④ 多仓库演进位（R-023）设计决策已写入《架构设计文档.md》§13。
> ⑤ 实施路径：选项②属中大型需求，走标准 SOP（PM 增量 PRD → 架构师增量设计+任务分解 → 工程师实现 → QA 回归），AI 代理不自行提交。

---

## 0. 结论与边界

**结论：可行。** Duplicati 的「配置 → 运行 → 恢复」三段式与本项目 PRD 页面结构高度同构，可以以其为模板把操作流程串成一条用户旅程。差异分三类处置：

| 差异类型 | Duplicati 行为                      | 本项目处置                                                                                                             |
| ---- | --------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 安全模型 | UI 生成备份加密密码                       | **不可照搬**：仓库密码走密码文件挂载、永不进表单/API（红线 S3、US-B1 AC4）；向导对应步骤只展示「密码文件挂载状态：已配置/缺失」                                        |
| 覆盖语义 | 恢复可到原始位置/任意目标 + restore timestamp | **不可照搬**：P1 仅时间戳子目录（红线 S7）；Duplicati 的 restore timestamp 与本项目时间戳子目录**语义同源**，仅做文案对齐（「永不覆盖」）；原位恢复留待 P2（R-015 三重确认门） |
| 规模模型 | 多备份任务列表（每项独立 Start/进度/日志）         | **刻意收敛**：单仓库单备份集 → 仪表盘 + 「立即备份」 + 任务历史                                                                            |


**范围声明**：以下 A 档为纯前端编排（不动后端），B 档为小后端增量，C 档仅做术语对齐（对应 PRD P2 既有需求，不新增需求）。

---

## 1. 现状基线（2026-10-12 代码核实）

前端路由（`frontend/src/router/index.ts`）：`/login`、`/bootstrap` + 主导航 7 页：仪表盘 `/`、仓库 `/repo`、备份源 `/sources`、策略 `/policies`、任务 `/jobs`、快照 `/snapshots`、恢复 `/restore`。

各页已有能力（与 Duplicati 对应关系）：

| 页面 | 已有能力 | 对应 Duplicati 概念 |
|---|---|---|
| repo | 类型单选（local/s3/b2）+ 凭据脱敏（has_value/source，env 覆盖置灰）+ testRepo 测试连接 + E33 路径选择（双 Tab）+ 初始化仓库（ConfirmGate INIT）+ 手动备份（createJob）+ 状态卡 | Storage destination + Test destination |
| sources | 源目录树浏览 + 点目录即选 + 内置排除集只读展示 | Source data（更强：排除集强制不可删减） |
| policies | 单默认策略表单：cron 模板/keep_*/group_by/one_file_system/compression/check 等 | Schedule + Retention |
| jobs | 活跃任务卡（E13 running/pending）+ JobProgress SSE + 取消 + 日志抽屉（E27 tail 64 KiB→1 MiB）+ skipped 明细 | progress bar + backup details + Show log |
| snapshots | 分页列表 + VirtualTree 文件树抽屉 | 版本列表 + 文件浏览 |
| restore | 3 步向导（选快照+目标 → ConfirmGate RESTORE → 执行+进度），仅时间戳子目录，原位恢复置灰 | Restore 页（更严：P1 无覆盖路径） |
| dashboard | 仓库状态卡 + TierCard 运行模式 + 最近任务 | Home 概览 |

后端路由已齐：`auth/config/fs/health/jobs/policies/restore/snapshots/sources`。**后端能力对三段式流程已基本完备**，差距集中在「流程编排」而非「能力缺失」。

---

## 2. 流程映射总表

### 阶段 1：配置（Duplicati 5 步向导 ↔ Web 版）

| Duplicati 步骤 | Web 版现状 | 差距 | 动作 |
|---|---|---|---|
| ① Basic（名称+加密密码） | 分散：repo 页（类型/凭据/密码状态）+ 仓库无「名称」概念（单仓库） | 无向导串联；密码状态提示已有 | A1 向导第 1 步：任务名称（仅展示用）+ 运行模式说明 + 密码文件挂载状态展示 |
| ② Source data | sources 页完整 | 未进向导 | A1 向导第 2 步（复用 SourcesPicker） |
| ③ Storage destination | repo 页完整（含 Test destination） | 未进向导 | A1 向导第 3 步（复用 RepoForm + testRepo） |
| ④ Schedule | policies 页 cron 字段 | 未进向导 | A1 向导第 4 步（复用调度表单） |
| ⑤ Retention | policies 页 keep_* 字段 | 未进向导 | A1 向导第 5 步（保留档位）+ **首备 dry-run 预览 → 确认 → 立即首跑**（同时满足 US-B2 门禁与 Duplicati「配置完即跑」体验） |

### 阶段 2：运行（Duplicati 运行页 ↔ Web 版）

| Duplicati 概念 | Web 版现状 | 差距 | 动作 |
|---|---|---|---|
| Start 按钮 | repo 页有 createJob 触发 | 入口不在任务/仪表盘视角 | A3：仪表盘与任务页加「立即备份」（409 冲突提示已有） |
| 进度条（顶部） | JobProgress SSE（Last-Event-ID 断点续传，优于 Duplicati） | 无 | 不动 |
| 首次最慢/增量 | 解析层已处理 | 无 | A4：文案提示「首次备份最慢属预期」 |
| Show log（General/Remote） | jobs 日志抽屉（tail 逐级放大） | 无分栏概念，能力等价 | 不动 |
| 通知 | R-018（P2 未实现） | 缺 | C 档：P2 通知上线后对齐（healthchecks/webhook/MeoW，不做邮件） |
| 定时调度 | 调度器属 P2（R-013），policies cron 字段已可存 | 调度未生效 | C 档：P2 调度器上线后仪表盘显示「下次运行时间」 |

### 阶段 3：恢复（Duplicati Restore 页 ↔ Web 版）

| Duplicati 概念 | Web 版现状 | 差距 | 动作 |
|---|---|---|---|
| 三入口（现有配置/配置文件/直接恢复） | 单一 /restore 页 | 多入口在单仓库模式下无意义 | 不新增入口；「换机器恢复」等价于 repo 页重新指向已有仓库（已有 init/unlock） |
| 版本选择（默认最新） | restore 页快照下拉 | 默认未选中最新 | A2：下拉默认选第一项（最新） |
| 文件/文件夹级选择 | **整快照恢复**（restore 仅 snapshot_id+target） | 缺文件级勾选 | B1：restore API 增可选 paths（restic `--include`），前端 VirtualTree 多选 |
| restore to another destination / restore timestamp | 时间戳子目录（强制，永不覆盖） | 语义一致、表述不同 | A4：i18n 文案对齐「恢复到自动新建的时间戳子目录，永不覆盖」 |
| 权限默认不恢复 | 非 root skipped 计数/明细展示 | 等价（restic skipped 机制） | 不动 |
| 恢复摘要页 | JobProgress + 结果路径展示 | 无 | 已等价 |

---

## 3. 界面调整清单

### A 档（纯前端，不动后端，建议随下一轮 UI 迭代）

- **A1 首次配置向导 `/setup`**（核心项，待裁决）：
  - 新页 `pages/setup/index.vue`，n-steps 5 步，顺序严格对齐 Duplicati：①基础（名称/运行模式/密码文件状态）→ ②备份源 → ③存储目标（含测试连接）→ ④调度 → ⑤保留 + dry-run 确认 + 立即首跑；
  - 表单抽共享子组件 `components/wizard/`：RepoForm / SourcesPicker / ScheduleRetentionForm（自 repo/sources/policies 页抽出复用，原页面改为引用）；
  - 向导完成判定读现有 GET（config/sources/policies）完成度，无需新端点；
  - 入口：仪表盘在「仓库未配置或无备份源」时显示 checklist 卡 + 「开始配置」按钮（对齐 Duplicati 的 Add backup 入口）；
  - 全部文案走 i18n（模板禁硬编码中文）。
- **A2 恢复流程小对齐**：restore 页快照下拉默认选最新；snapshots 页文件树抽屉加「恢复此快照」按钮，携带 snapshot_id 跳转 `/restore?snapshot=<id>`（PRD §5 页面 8 已有此设计，落到实现）。
- **A3 立即备份入口**：仪表盘/任务页加「立即备份」按钮（复用 createJob），对齐 Duplicati Start 语义，含 409 冲突提示。
- **A4 文案对齐**：i18n 增补「首次备份最慢属预期」「永不覆盖·时间戳子目录」等表述。

### B 档（小后端增量）

- **B1 文件级恢复**（对齐 Duplicati 选文件/文件夹，归期待裁决）：
  - 后端：`routers/restore.py` 请求体增 `paths: list[str] | None`；`ResticCmd.build` 白名单增 `--include` 参数拼装（S2）；每个 path 过 `PathMapper.validate()`（S1）；`restore.run` 审计已覆盖（S6，不新增动作名）；
  - 前端：restore 步骤 1 增可选文件树多选（复用 VirtualTree + listSnapshotFiles）；ConfirmGate 预览增所选文件清单；
  - 测试：restore 路由 paths 用例 + 参数白名单用例 + 前端 spec。

### C 档（对应 PRD P2，仅术语对齐，不新增需求）

调度器下次运行时间卡（R-013）、通知模块（R-018，healthchecks/webhook/MeoW）、原位恢复（R-015，保留三重确认门）、forget 预览流（R-016，比 Duplicati 自动清理更严，保留）。

---

## 4. 关键实现步骤（A 档顺序）

1. 抽共享子组件（RepoForm/SourcesPicker/ScheduleRetentionForm），原页面改为引用，跑 `pnpm run typecheck && pnpm run test` 确认零回归；
2. 新增 `/setup` 路由 + 向导页（n-steps + 各步表单）+ i18n 键；
3. 仪表盘 checklist 卡（读 config/sources/policies 完成度）；
4. restore 页默认最新 + snapshots 页「恢复此快照」跳转；
5. 立即备份按钮（仪表盘/任务页）；
6. 回归三件套：`pnpm run typecheck && pnpm run test && pnpm run build`；`uv run pytest`；`bash scripts/verify_redlines.sh`（本档不动 Dockerfile/compose，无需 check_dockerignore）。

B1 追加：先补 fixture/测试再改 `restic.py`（改解析/参数逻辑必须测试先行）。

---

## 5. 红线影响核对（规范 §3.1）

| 红线 | 影响 |
|---|---|
| S1 路径 | A 档无新路径输入；B1 的 paths 全部过 PathMapper，白名单不变 |
| S2 参数 | B1 需在 ResticCmd.build 白名单内新增 `--include` 拼装，禁自由字符串 |
| S3 凭据 | 无新增凭据面；向导第 1 步仅展示密码文件挂载状态 |
| S4 鉴权 | 新页 `/setup` 在登录守卫内，不新增免鉴权端点 |
| S6 审计 | restore.run 已埋点；向导内 testRepo/config.update 走既有埋点 |
| S7 覆盖 | 全案不引入任何覆盖语义；原位恢复仍置灰至 P2 |
| A1/A3/A4/A5 | 不涉（无 DB 直连、无排除集复制、SSE 底座与解析层不动） |

---

## 6. 待裁决项（2026-10-13 已全部裁决）

1. ~~是否新增 `/setup` 向导页~~ **已裁决：立项为选项②的「新建/编辑备份任务」流**（见文首修订记录 ①），不作为独立单备份集流程；
2. ~~B1 文件级恢复归期~~ **已裁决：随 P2 与选项②同批实施**；
3. ~~Duplicati「配置文件导入/导出」~~ **已裁决：不做**（单仓库模式下换机器恢复由 repo 页重新指向已有仓库承担）。

## 7. 不做清单（与 Duplicati 的刻意差异）

- UI 生成/保管备份加密密码（密码文件挂载语义，S3）；
- 恢复到原始位置/任意目标 + 覆盖选项（S7，P2 原位恢复走 R-015 更严确认流）；
- 多备份任务列表（单仓库单备份集）;
- 邮件通知渠道（R-018 已裁决 healthchecks/webhook/MeoW）。
