# Duplicati 功能对齐分析与改造计划

> **状态：参考层分析 + 方案草案（2026-10-17），待 owner 裁决吸收**。结论吸收进《Web版改造方案.md》/《架构设计文档.md》前，本文不作为技术决策权威。
>
> **裁决记录（2026-10-17 owner，五轮）**：① C2 只增加邮件通知（Telegram 不做）；② **C4 做**（copy + key），并升级为**任务级多目的地**（对齐 Duplicati「+ Add another destination」）；③ D1/D2/D3/D5 均**不做**；④ D4 配置导入/导出**放多仓库落地后做**；⑤ **目的地类型选择卡**并入批次 A（RepoForm 卡片化），**源侧远端目录**（手动输入挂载路径 + rclone mount 挂 S3/B2 等的部署指引）并入批次 A4；⑥ **调度双模式**（A6，简单模式生成 cron + 高级模式直编，双向解析，后端零改动）——cron 保持唯一事实源，门槛降到 Duplicati 级；⑦ **A0 运行模式收敛**（去掉 host 模式，只留 sources；**开发期无存量数据，不做任何兼容**——配置/DB/快照均可重建；实施时**文档全量清扫含 docs/archive/ 已归档件**）+ **A7 原位恢复 Duplicati 化**（原位置默认 + overwrite 布尔勾选，S7 放松裁决见批次 A）。批次 D 全部裁决完毕。⑧ **批次重排**（owner 2026-10-17 07:31「排吧」）：原批次 A（8 项，后又增 A8 共 9 项）拆为 **A（安全收敛+引擎扩展，后端：A0→A7→A1→A2→A3）** 与 **B（前端体验对齐：A4/A5/A6，纯前端）**；原 B/C 顺延为 **C（自愈）/D（运维通知）**；D4 升为 **批次 E（多仓库落地后）**；D1/D2/D3/D5 = 不做清单。项编号 A0–A7 沿用立项时序不变。
> 依据：`D:/agent/duplicati-master`（Duplicati 3.x 源码，`Duplicati/Library` 引擎 + `Duplicati/WebserverCore` 服务端）；本项目代码现状经 codebase-memory 代码图谱核实（backend/app 全部 20 组路由 + 19 个服务 + 枚举/白名单）。
> 边界：**不参考** docs 下既有 Duplicati 三篇历史文档（按 owner 2026-10-17 指令）；引擎选型结论独立得出，如与历史文档撞车属殊途同归。

---

## 1. 总体结论与策略

**不移植 Duplicati 的 C# 引擎，移植它的「能力面」。**

Duplicati 引擎核心 = 变长分块去重（CDC + blockset 模型）+ dblock/dindex/dlist 三卷结构 + 本地 SQLite 状态库 + 卷级压缩加密。本项目以 restic 为引擎，上述能力**全部已覆盖且整体更优**（CDC 去重、zstd 压缩、AES256-CTR 仓库级加密、缓存层、快照模型）。把数万行 C# 引擎重写成 Python 只会得到一个更差的重复品。

因此对齐策略分三层：

| 层 | 结论 |
|---|---|
| **引擎层**（分块/去重/压缩/加密/状态库） | **保留 restic**，不移植（Duplicati 引擎对本项目零增益） |
| **能力面层**（操作全集/保留策略/修复自愈/通知钩子/存储广度） | **本计划主体**——把 Duplicati 有而本项目缺的操作与运维能力经 restic 补齐 |
| **项目独有且更优** | **保留**（见下表），Duplicati 对应能力不倒灌 |

本项目 11 项「优于 Duplicati」的既有实现（保留，不回退）：

1. SSE 进度流（Last-Event-ID 断点续传 + 日志文件底座，优于 Duplicati 内存轮询）；
2. 危险操作审计日志（audit_logs 只增不改，Duplicati 无此概念）;
3. Prometheus 指标 + 备份成功率报表（Duplicati 无）；
4. 数据库 dump 任务（mysqldump/pg_dump，Duplicati 无原生等价）；
5. 原位恢复三重确认门（比 Duplicati 的自由覆盖更严）；
6. forget 预览流（dry-run 先行，Duplicati 自动清理更激进）；
7. 任务并发互斥（409/记 cancelled，`asyncio.gather` 双并发测试护栏）；
8. 凭据加密存储（R-025，明文永不落库）；
9. 多任务向导 + 任务域 dry-run 预览；
10. cron 星期语义修正（APScheduler 偏移坑已修，Duplicati Timeparser 同类问题不存在）;
11. 容器路径零暴露（display_path 派生，Duplicati 直接暴露本地路径）。

---

## 2. Duplicati 功能清单与模块对照

引擎侧证据根目录：`Duplicati/Library/`；服务端：`Duplicati/WebserverCore/`、`Duplicati/Server/`。

| # | 功能域 | Duplicati 模块（关键文件） | 实现方式一句话 |
|---|---|---|---|
| 1 | 核心操作 | `Library/Main/Operation/*Handler.cs`（Backup/Restore/List×9/Delete/Compact/Repair/RecreateDatabase/PurgeFiles/PurgeBrokenFiles/Test/Vacuum/SetLocks/SetVersionLabel/CreateBugReport/TestFilter/RemoteSynchronization） | `Controller.cs` 统一调度，每操作一个 Handler |
| 2 | 备份流水线 | `Operation/Backup/`（FileEnumerationProcess、FilePreFilterProcess、StreamBlockSplitter、DataBlockProcessor、MetadataGenerator、SpillCollectorProcess） | 文件枚举→glob 过滤→分块→去重→组卷→上传，Channel 流水线 |
| 3 | 卷模型 | `Main/Volumes/`（BlockVolumeWriter/IndexVolumeWriter/FilesetVolumeWriter） | dblock=数据卷、dindex=hash 索引、dlist=文件清单 |
| 4 | 上传后端管理 | `Main/Backend/BackendManager*.cs`、`IBackendManager` | Put/Get/List/Delete 分通道并发 + 待删卷记录 + 重试 |
| 5 | 断点续传/中断恢复 | `RecreateMissingIndexFiles.cs`、RepairHandler、RecreateDatabaseHandler | 缺失 dindex 补建；库损坏从远端卷重建 |
| 6 | 压缩 | `Compression/CompressionModules.cs`（zip、tar+zstd、tar+gzip） | DynamicLoader 注册 + 插件目录加载 |
| 7 | 加密 | `Encryption/EncryptionModules.cs`（AES-256-CBC、GPG 外部进程） | CryptoStreamWrapper 串在压缩后 |
| 8 | 存储后端 | `Backends/BackendModules.cs` + `Backend/` 30+ 目录（File/SFTP/S3/Azure/GCS/B2/OneDrive/Swift/Rclone/FTP/WebDAV/Storj/AliyunOSS/TencentCOS…） | 每后端一目录，OAuth 走 OAuthHelper |
| 9 | 状态库 | `Main/Database/Local/LocalDatabase.cs` + 编号 SQL schema | SQLite 存 Blockset/Block/Metadata/Remotevolume，dbpath 与远端一一对应 |
| 10 | 内置模块 | `Modules/Builtin/`（RunScript、SendMail、SendHttpMessage、SendTelegramMessage、SendJabberMessage、ConsolePasswordInput、CommonOptions） | `ReportModuleController` 在操作 start/finish/fail 时回调 |
| 11 | 调度 | `Library/RestAPI/Scheduler.cs` + `Timeparser.cs`；新架构 `WebserverCore/Services/SchedulerService/TaskQueueService/QueueRunnerService` | 后台线程重算下次时间，错过的任务立即排队；调度只发信号，worker 串行 |
| 12 | 保留与清理 | `Operation/DeleteHandler.cs`（KeepTimeRemover/KeepVersionsRemover/RetentionPolicyRemover）+ `CompactHandler.cs` | 三种保留规则：时间线 keep-time / 最近 N 版 / 分段区间（如 `7d:1d,1m:1d,forever:1m`），后接 Compact 回收 |
| 13 | Parity | `Library/Parity/Par2Parity.cs` | 调外部 par2cmdline 为上传卷生成 PAR2 恢复文件 |
| 14 | 选项系统 | `Main/Options.cs`（约 2000 行自描述参数表） | blocksize/dblock-size/keep-*/threshold/snapshot-policy/throttle/retries… |
| 15 | 快照 | `Library/Snapshots/`（WindowsSnapshot=AlphaVSS、LinuxSnapshot=lvm-scripts、MacOSSnapshot） | 卷影副本/LVM 快照保证一致性 |
| 16 | 过滤器 | `Utility/FilterExpression.cs`、FilterGroups、Sizeparser | include/exclude glob 表达式树 + 大小过滤 |
| 17 | 其他引擎能力 | `Utility/RetryHelper.cs`（重试）、带宽 throttle 流包装、`SecretProvider/`（外部密钥注入）、`UsageReporter/` | 重试/限速/密钥提供/匿名统计 |
| 18 | 服务端 API | `WebserverCore/Endpoints/V1/`（Backup CRUD、Tasks、Commandline、ProgressState、LogData、Notification、Filesystem、Auth JWT、RemoteControl） | ASP.NET minimal API；任务队列查询、远程 CLI、实时日志 |

---

## 3. 能力映射总表（Duplicati ↔ 本项目）

本项目现状锚点：`backend/app/schemas/enums.py`（JobType 七类）、`services/restic.py`（VERBS 10 个 + WhitelistArgs）、路由 20 组。

### 3.1 已等价（✅，不动）

| Duplicati 功能 | 本项目实现 | 说明 |
|---|---|---|
| Backup 增量备份/去重/压缩/加密 | restic `backup` + 仓库级 zstd/AES256 | 引擎层整体更优 |
| Restore（整版/文件级/原位） | restore + `--include` + 原位（三重确认门） | 原位比 Duplicati 更严 |
| 版本列表/文件浏览 | snapshots + `ls`（VirtualTree） | 等价 |
| 版本变更对比 ListChanges | `compare.py`（diff 服务） | 等价 |
| 删除版本 + Compact | forget + `--prune`（预览流先行） | 更严（预览门） |
| Test 抽卷校验 | check + `--read-data-subset`（E22 结果视图） | 等价 |
| Unlock | unlock 任务 | 等价 |
| 容量统计 | `stats`（E30） | 等价 |
| 过滤器 exclude | exclusions.py 内置集 + 任务级 excludes | 更严（内置集强制） |
| 带宽限速 throttle | `limit_up/down_kibps` | 等价 |
| 调度 + 串行队列 | APScheduler + JobRunner 互斥 | 更优（cron 星期修正 + 409 语义） |
| 通知（webhook 类） | notifier（healthchecks/webhook/MeoW） | 部分等价，见 3.3 |
| 运行前密码输入 | 密码文件挂载（S3 红线） | 刻意差异，保留 |

### 3.2 部分等价（⚠️，本计划补齐）

| Duplicati 功能                         | 本项目现状                                        | 差距                                                                        |
| ------------------------------------ | -------------------------------------------- | ------------------------------------------------------------------------- |
| 存储后端 30+                             | RepoType 仅 local/s3/b2                       | restic 原生还有 sftp/azure/gcs/rest；接 **rclone** 后可解锁 Duplicati 全部云后端的超集（70+） |
| 保留策略 keep-time + retention-policy 分段 | keep_last/hourly/daily/weekly/monthly/yearly | 缺时间线档 `--keep-within`（及 `--keep-within-hourly/daily/...`）与 `--keep-tags`  |
| 版本标签 SetVersionLabel                 | 无                                            | restic `tag` verb 未接                                                      |
| 通知渠道 mail/telegram                   | 仅 healthchecks/webhook/MeoW                  | 缺 SMTP 邮件与 Telegram                                                       |
| 重试 number-of-retries                 | 子进程单次执行                                      | 缺任务级失败重试 + 退避                                                             |
| RunScript 前后钩子                       | 无                                            | 缺任务级 pre/post 命令执行                                                        |

### 3.3 缺失（❌，本计划新增或裁决）

| Duplicati 功能                               | restic 对应                       | 处置建议                                                      |
| ------------------------------------------ | ------------------------------- | --------------------------------------------------------- |
| Repair（重建索引/卷一致性）                          | `repair index` / `repair packs` | **批次 B 新增**（新 verb + 确认门 + 审计）                            |
| RecreateDatabase（从远端重建库）                   | 缓存可弃重建 + `recover`（找回未快照数据）     | 批次 B 新增 `recover`；缓存重建已有 cache_service 兜底                 |
| ListBrokenFiles                            | check 报 blob 损坏，无文件级原生命令        | 批次 B：check 输出解析 → blob 级报告 + repair 建议链；文件级映射**受限**（诚实标注） |
| PurgeFiles / PurgeBrokenFiles（永久删某文件的全部历史） | restic 无原生命令（只能 forget 整版）      | **裁决（2026-10-17）：不做**（通俗说明见批次 D）                        |
| Parity（PAR2 奇偶修复卷）                         | 无等价                             | **裁决（2026-10-17）：不做**                                  |
| VSS/LVM 一致性快照                              | 无（容器内不可行）                       | 不做（host 挂载 + 应用级 quiesce 为替代说明）                           |
| 配置导入/导出                                    | —                               | **裁决（2026-10-17）：放多仓库落地后做**                                          |
| 远程管理通道 RemoteControl / 自动更新 / 使用统计         | —                               | 不做                                                        |

### 3.4 本项目独有（Duplicati 无，保留不动）

dump 数据库任务、Prometheus 指标、审计日志、多仓库（R-023）+ 凭据加密（R-025）、`copy`（restic 仓库间复制，可选增强位）。

---

## 4. 改造计划（批次 A–D）

> 排序原则（2026-10-17 07:31 重排）：**批次 A 先行**——A0/A7 是唯一动安全模型（S1/S7 红线）的施工，单独成批独立评审，A0 白名单收敛后 A1 的新类型才不用写 host 分支；**批次 B 纯前端**，依赖 A1 类型清单落地，但可与批次 C/D 并行推进；自愈（C）、运维通知（D）殿后，E 等多仓库。与进行中的 P3（R-023/024/025）并行，改 `repos/enums/settings` 的批次需与 P3 提交顺序协调（P3-A 未提交，P3-B/C 进行中）。

### 批次 A：安全收敛与引擎扩展（后端为主，P3 收尾后启动）

> 项编号 A0–A7 沿用立项时序；批次归属以本章为准——A0/A7/A1/A2/A3/A8 属批次 A，A4/A5/A6 属批次 B。

| 项   | 内容                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | 触及文件                                                                                                                                                                                |                 |           |       |     |                                                                                                                                    |                                                                                                                                                                                                         |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | --------- | ----- | --- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A0 | **运行模式收敛**（owner 2026-10-17 裁决：去掉 host 模式，只留 sources，对齐 Duplicati「只认进程可见目录」心智）：① `settings.restic_web_mode` 字段直接删除、`RunMode`/`HostMountMode` 枚举删除（**开发期无存量数据，不做任何兼容**——配置/DB/快照均可重建，owner 2026-10-17 07:18 指令）；② 路径白名单收敛为 `/sources`+`/restore`（S1 唯一出处 `settings.py`，`host_root` 删除）；③ `auth.py` /me、E05 源白名单、`job_runner`/`task_service` mode 分支清理、exclusions 内置集归一、one_file_system 默认统一；④ TierCard 四象限收敛；⑤ compose `RESTIC_WEB_MODE`/`HOST_MOUNT_MODE` 删除（nonRoot + `/sources` 固定）；⑥ TierCard/E2E 四象限用例收敛 + 规范 §3.1 S1 线索同步；⑦ **文档全量清扫（owner 2026-10-17 07:18 指令）**：grep `host 模式 / RESTIC_WEB_MODE / HOST_MOUNT_MODE / host_root / /host / 四象限 / RunMode` 更新**所有相关文档——含 docs/archive/ 已归档件**（活跃文档改正文；归档件按文档管理规范 §3 处理——文首加「内容已过时」注记而非重写正文；AGENTS.md/规范/PRD/架构文档/部署指南/compose 注释全查） | `settings.py`、`enums.py`、`path_mapper.py`、`exclusions.py`、`routers/auth.py`、`routers/sources.py`、`routers/restore.py`、`job_runner.py`、`task_service.py`、compose、TierCard、E2E、规范 §3.1、docs 全域（含 archive） |
| A1（✅ 已交付 2026-10-17） | RepoType 增 `sftp`/`azure`/`gcs`/`rest`/`rclone`；rclone 作为「万能后端」（一个类型解锁 Duplicati 全部云后端超集）。实现细则见下方「A1 细则」 | `enums.py`、`repos.py`（表单/校验/testRepo）、`restic.py`（`ENV_ALLOWED_PREFIXES` 增 `RCLONE_/AZURE_/GOOGLE_/REST_/SSH_`）、`crypto.py`（新凭据字段入加密）、Dockerfile（装 rclone + openssh-client）、compose |
| A2（✅ 已交付 2026-10-17） | forget 保留档位补齐：`keep_within`、`keep_within_hourly/daily/weekly/monthly/yearly`、`keep_tags`（Duplicati keep-time / retention-policy 分段的 restic 等价物） | `restic.py` WhitelistArgs、`retention.py`、任务表单 + forget 预览联动、迁移新增列 |
| A3（✅ 已交付 2026-10-17） | **快照版本标签**（Duplicati 版本标签的 restic 等价物）：`VERBS` 增 `tag`；`WhitelistArgs` 增 `tag_add`/`tag_remove`（每元素一条 `--add`/`--remove`，`_assert_safe_value` 防注入；与 forget/snapshots 的 `--tag` **筛选**维度 `tags` 语义隔离，不复用）；快照 id 经 `_assert_snapshot_id`（8~64 小写十六进制）；新端点 **E46 `POST /api/snapshots/tag`**（同步执行，援 forget 预览先例）；restic `cmd_tag.go` 源码核验的两道约束前移为服务端 422——(1) `add`/`remove` 皆空 → 422；(2) 无 `snapshot_ids` 且无 `tag_filter`/`host` → 422（restic 无 id 即改**全部**匹配快照，高危）；审计动作 `tag.update`；快照页新增「快照标签」卡片 | `restic.py`（VERBS/WhitelistArgs.tag_add/tag_remove/_verb_flags tag 分支/_assert_snapshot_id）、`routers/snapshots.py`（E46 + 解析 changed/summary）、`schemas/api_schemas.py`（TagSnapshots* 三 schema）、`constants.py`（AUDIT_TAG_UPDATE/TAG_OPERATION_TIMEOUT_SECONDS）、前端 snapshots/index.vue + api/snapshots.ts + types.ts + i18n 双语、测试 test_restic_tag.py + test_api/test_snapshots_tag.py |
| A8（✅ 已交付 2026-10-17） | **仓库 ID 自动生成**（owner 2026-10-17 07:37 立项）：POST /repos 的 `repo_id` 必填 → 可选，缺省时服务端生成 **`repo-<小写 ULID>`**（26 位全局唯一、按时间有序，天然满足 `REPO_ID_PATTERN` 与 64 长度上限；落库前 exists 查重，碰撞重试）；显式指定仍允许（脚本/重命名场景）。契约不变：repo_id 语义、passfile 路径拼装、`X-Repo-Id` 头全部照旧。前端：向导新建仓库预填自动生成值 + 「换一个」按钮，可改可不改（对齐 Duplicati 新建备份自动起名体验） | `repos.py`（POST schema 改可选 + `_resolve_repo_id`/`_new_repo_id` 生成器 + `GET /repos/_new_id` 候选端点 + 单测）、`schemas/api_schemas.py`（RepoCreateRequest.id 可选 + 空白归一 None 的前置校验 + RepoIdSuggestion）、`utils/ulid.py`（复用）、前端 repo/index.vue + api/repo.ts + types.ts + i18n 双语 |



| A7（✅ 已交付 2026-10-17） | **原位恢复 Duplicati 化**（owner 2026-10-17 裁决「Duplicati 怎么实现本项目就怎么实现」；源码证据：`Options.cs:529` `--overwrite` 布尔默认 false、`RestoreHandler.cs:1784` 原位置分支、唯一护栏 `allow-restore-outside-target-directory` 默认 false）：① 恢复页目标 = **原位置（默认）**（restic 等价 `--target /`，机制复用现有 `INPLACE_RESTORE_TARGET_ROOT`，执行层零改动）或自定义目录（时间戳语义）；② `overwrite` 从「固定 always」改为 **Duplicati 布尔勾选**（默认不勾 → `--overwrite=never` 已存在文件跳过；勾选 → `=always`），时间戳路径的 `if-changed` 注入不动；③ 确认门从「三重确认（关键词=快照短 id）」放松为**单次 ConfirmGate 确认**（对齐 Duplicati 无关键词确认）；④ 保留的底线护栏 = 目标前缀白名单（等价 Duplicati 的 outside-target-directory=false）+ `restore.inplace` 审计；⑤ **规范 §3.1 S7 修订先行**（三重确认门/固定 always 条目改写 + AGENTS.md 编号同步），否则 verify_redlines 假绿（开发期无存量数据，无历史 `/host/*` 快照兼容负担） | `routers/restore.py`、`restic.py`（WhitelistArgs.overwrite 值域扩 never）、规范 §3.1 + AGENTS.md、前端 restore 页、E2E |

**A1 细则：rclone 实现方案（2026-10-17 接续《讨论记录-R-022立项前置-2026-10-16》§3/§5 定稿，owner 批示写入）**

1. **运行方式（采纳 R-022 §3 生命周期结论）**：按需子进程，非常驻服务——restic 原生 `rclone:` backend 按仓库命令自动拉起 `rclone serve restic --stdio`，命令结束即退出；不进 asyncio 事件循环，同仓库任务互斥天然覆盖。`restic.py` 执行层**零改动**，只把 `rclone:<remote>:<path>` 当一种 repo.url 传入；`ENV_ALLOWED_PREFIXES` 增 `RCLONE_`。
2. **配置面（沿用 Q17 档位：只读托管）**：`rclone.conf` 由用户宿主机 `rclone config` 生成 → compose 挂载进容器（**必须 `:rw` 挂载**——网盘 OAuth token 过期时 rclone 回写 conf，只读挂载 = 授权静默失效，R-022 登记的运维约束升级为硬约束）；UI 只回状态：`rclone listremotes` 只读探测（conf 存在性 + remote 名清单），**不生成、不编辑、不展示凭据**（conf 内 OAuth token 永不进 API，S3 延续）。
3. **Q18 作废**：R-023 已落地 repos 表（0004 迁移），rclone 仓库直接进 repos 表——只存 `url` + conf 状态，凭据零落库（优于原「暂放 config 全局键」结论）；`crypto.py` 无新凭据字段。
4. **Q20 采纳**：`rest:`/`rclone:` 严格 URL 校验（前缀合法 + remote 必须在 listremotes 内 + 路径语法，422）；s3/b2/local 校验不动。
5. **R-022 §5「边界说明」修正**：其「远程仓库快照容器内无挂载点 → 原位恢复无落点」结论基于 P1 时代 /host 白名单设计，**已失效**——本项目 restic 客户端就在容器内，rclone 仓库快照 paths = `/sources/...`，A7 Duplicati 化后原位恢复（`--target /`）对 rclone 仓库照常可用。
6. **与 A5 联动**：向导 OneDrive/Google Drive preset 快捷卡 = 引导宿主机 `rclone config` 对应 provider + 约定 remote 命名，卡内表单只填 `rclone:<remote>:<path>`。
7. **文档动作**：《讨论记录-R-022立项前置-2026-10-16》打吸收标注（再立项已触发，R-022 范围并入 A1/A5；批次 A1 落地后按其约定归档）。

验收（批次 A）：`ruff` + `pytest`（新 verb/新 repo 类型测试先行）+ `verify_redlines` + 真机对 rclone/local 仓库 E2E 备份→恢复→forget 全链。

### 批次 B：前端 Duplicati 体验对齐（纯前端，可与批次 C/D 并行）

| 项 | 内容 | 触及文件 |
| --- | --- | --- |
| A4 | ✅ 已交付 2026-10-17 —— **源侧远端目录**（owner 2026-10-17 裁决）：① 源选择器加「手动输入挂载路径」入口（过 PathMapper，仍在 `/sources` 白名单内）；② 部署指南补「远端源挂载指引」——**rclone mount 通吃 S3/B2/Azure/GCS/OneDrive 等 70+ 云**（NFS/SMB 只是最常见情形），推荐宿主机挂载→compose 映射进 `/sources`（容器免 FUSE 特权），附对象存储当源的性能（海量小文件慢、请求费）与一致性（VFS 缓存）告警 | 前端 sources/任务向导、`docs/部署指南.md` |
| A5 | ✅ 已交付 2026-10-17 —— **目的地类型选择卡**（对齐 Duplicati Add backup 向导）：RepoForm 重构为「卡片网格选择 + 按类型条件渲染表单」；OneDrive/Google Drive 等 Duplicati 云端以 **rclone preset 快捷卡**呈现（rclone 后端实际可用），不做假卡——视觉等价 Duplicati、实际全部可用 | 前端 RepoForm/任务向导（后端零新增，依赖 A1） |
| A6 | ✅ 已交付 2026-10-17 —— **调度双模式**（门槛降到 Duplicati 级，owner 2026-10-17 裁决）：ScheduleRetentionForm 重构为「**简单模式**（默认）+ **高级模式**」——① 简单模式三控件生成 cron：频率下拉（每小时/每天/每周/每月/每年/每 N 小时/每 N 天）+ 时间点选择器 + 日子多选（每周→星期、每月→几号），全程不露 cron 字样；② 高级模式直编 5 字段 cron（5 个独立输入框 + 校验）；③ **双向解析**：生成器只产出受控子集（永远可反向解析），编辑回显时 cron→简单模式，解析不了的复杂表达式自动落高级模式，不丢信息。**后端零改动**：DB 存的还是 cron 字符串，调度语义/APScheduler/cron 星期修正护栏全复用；表达力仍是 Duplicati 7 种间隔的超集 | 前端 ScheduleRetentionForm（生成器 + 解析器 + vitest）、任务向导/策略页 |

验收（批次 B）：`pnpm run typecheck` → `vitest run` → `build` 三步分跑 + 真机 UI 旅程（向导建任务 → 类型选择卡 → 简单模式调度 → 源侧手动输入挂载路径）。

### 批次 C：修复自愈（对齐 Repair/Recreate/ListBroken）

| 项   | 内容                                                                                       | 触及文件                                               |
| --- | ---------------------------------------------------------------------------------------- | -------------------------------------------------- |
| B1  | ✅ 已交付 2026-10-17 —— `repair index` / `repair packs`：JobType 增 `repair`，操作卡/审计/ConfirmGate（关键词 REPAIR，不可逆提示）；前端入口在「仓库配置」状态卡（修复仓库按钮 + index/packs 单选）与任务历史 check 抽屉 | `enums.py`、`constants.py`、`job_runner.py`、`restic.py`、审计常量、前端 repo 页 |
| B2  | ✅ 已交付 2026-10-17 —— 损坏检测报告：check `--read-data` 输出解析 → blob 级损坏清单（E22 视图，批次 B 已预实现 broken_packs/suggest_repair_index）+「一键修复索引」建议链按钮（任务历史 check 抽屉，内联 ConfirmGate）；**文件级映射为受限项，已在部署指南 §7.1 如实标注** | `parser.py`（fixture 先行）、`checks.py`、前端 check 详情    |
| B3  | ✅ 已交付 2026-10-17 —— `recover`（找回未快照数据）：JobType 增 `recover`（E12 触发，`recover.run` 审计）+「重建缓存」引导（RecreateDatabase 等价说明落部署指南 §7.1：缓存可弃重建 / repair / recover 三场景对照表 + API 触发示例） | `restic.py`、`enums.py`、`constants.py`、`job_runner.py`、部署指南 |

### 批次 D：运维与通知（对齐 Modules/Builtin）

| 项      | 内容                                                                                            | 触及文件                      |
| ------ | --------------------------------------------------------------------------------------------- | ------------------------- |
| C1     | ✅ 已交付 2026-10-18 —— 任务级 `pre_cmd`/`post_cmd`（0005 迁移；`services/hooks.py`：shlex 切分不经 shell + 强制超时 + 输出进任务日志 + 失败计任务失败 + 审计 hook.run，post 仅主备份成功后跑）；前端向导步骤 1 双输入 | 0005 迁移、`job_runner.py`、`services/hooks.py`、任务向导 |
| C2     | ✅ 已交付 2026-10-18 —— SMTP 邮件渠道（mail_* 九键入加密存储，只回 mail_password_set；STARTTLS/TLS/none；E24 逐渠道测试含 mail）；前端通知页 SMTP 表单（密码三态） | `notifier.py`、notify 配置面、通知页 |
| C3     | ✅ 已交付 2026-10-18 —— 任务级重试（0006 迁移 retry_count 0–5 默认 2；调度触发失败指数退避自动重试，手动触发不重试；次数耗尽才计终态+通知）；前端向导步骤 1 计数输入 | `scheduler.py`、0006 迁移、任务向导 |
| C4（**已裁决：做**） | ✅ 已交付 2026-10-18 —— copy + key + 任务级多目的地：VERBS 增 copy/key（url/密码走 env 与临时文件，绝不进 argv）；0007 迁移 mirror_repo_ids（≤3）；JobRunner 镜像阶段（主备份成功后串行增量 copy，失败隔离不翻转主终态，跳过全显式记账）；`POST /api/repos/rotate-key` + `POST /api/copy`；审计 copy.run/key.rotate；任务写路径自指/缺仓 422 前置拦截；前端向导镜像多选 + 仓库页轮换/搬家双弹窗 | 0007 迁移、`job_runner.py`、`restic.py`、ops 端点、审计、前端任务向导 + 仓库页 |

**C4 通俗说明**：
- **copy（备份搬家 / 异地副本）**：把仓库 A 里已有的备份快照**原样复制**到仓库 B。用途：① 换存储时不用从头全量备份（如本地盘搬去 S3）；② 双仓库异地容灾（本地一份、云端一份）。首次复制仍需走网络传全量数据，之后只传增量。
- **key（改仓库密码）**：在**旧密码还有效时**，给仓库添加新密码、验证成功后再撤销旧密码。用途：密码疑似泄露或定期轮换时，**不用删仓库重新备份**。Duplicati 没有等价能力（它只能整库重建），与本项目凭据加密（R-025）天然衔接。
- **任务级多目的地（对齐 Duplicati「+ Add another destination」）**：任务模型加 `mirror_repo_ids`（0..N 个镜像仓库）；backup 成功后 JobRunner 自动对每个镜像仓执行 copy（**增量**，只传新快照），copy 阶段进同一任务的 SSE 进度/日志，失败计「部分成功」并审计；向导 Storage 步骤 = 主目的地卡 + 「+ Add another destination」按钮追加镜像仓；镜像仓未 init 时给引导。**语义差异如实标注**：Duplicati 是引擎内一份去重数据写两处，本项目是「备份成功后增量镜像」，副目的地滞后主目的地一个 copy 阶段，功能体验等价。ops 侧保留手动 copy 入口（搬家场景）。

### 批次 E：配置导入/导出（多仓库落地后）+ 裁决不做清单

（原「批次 D 裁决项」，owner 2026-10-17 全部裁决完毕；D4 升格为批次 E，其余为不做清单）

**批次 E：D4 配置导入/导出**——通俗说：把「任务配置、策略、通知设置」打包成一个文件导出，在另一台机器上导入，**不用逐项手填**（备份数据本身不导出，只导配置）。**裁决：放多仓库落地后做**（导出格式要带多仓库凭据引用，现在做会返工）。

**不做清单**：

- D1 **PurgeFiles/PurgeBrokenFiles（从全部历史版本里永久删除某个文件）**——通俗说：普通删除备份版本是「删掉某一天的备份」，而 purge 是「这个文件在我所有备份历史里彻底消失，哪怕恢复到任何旧版本也找不回它」。典型用途：误把敏感文件（如密码文件、私钥）备份进了仓库，要彻底清除。**现状**：restic 没有原生命令，只能「删掉整个备份版本」变通（含目标文件的版本 forget），粒度粗且会连带丢掉其他文件的历史。**裁决：不做**（敏感文件场景可用「删含该文件的版本 + 之后靠 excludes 拦截」变通）；
- D2 **Parity PAR2（奇偶校验修复文件）**——通俗说：给上传到云端的每个备份数据包再附带若干「数学纠错包」，云端某几个数据包损坏时，可以用纠错包**原地算出丢失的数据**，不用重新备份。**裁决：不做**（restic 有校验无修复；check + repair + 重新备份已覆盖绝大多数场景，镜像增重不值）；
- D3 **VSS/LVM 快照（一致性定格）**——通俗说：备份时给磁盘「拍一张瞬时快照」（Windows 用卷影副本 VSS、Linux 用 LVM），备份的是定格那一刻的文件，避免备份过程中文件正被写到一半、备出来的数据损坏。**现状**：容器里拿不到 VSS/LVM，技术上不可行；**裁决：不做**，一致性边界写入部署文档（数据库类数据建议走本项目独有的 dump 任务保证一致性）；
- D5 **远程管理通道 / 自动更新 / 使用统计**——通俗说：① 远程管理通道 = 从 Duplicati 官网或另一台服务器远程操控这台备份机的管理界面（适合托管在客户处的机器）；② 自动更新 = 程序自己下载新版本升级；③ 使用统计 = 匿名上报使用情况给 Duplicati 官方。**裁决：不做**（自托管单容器场景无此需求，自动更新反而破坏镜像版本纪律）。

---

## 5. 规范影响（建议「规范先行」而非绕开）

owner 已声明本次改造不受现有规范限制，但本仓库红线是机器校验的（`verify_redlines.sh`），**绕开只会让 CI 假绿**。建议每个批次同步更新规范 §3.1 + AGENTS.md 编号索引：

- S2：VERBS 白名单扩展（`tag/repair/recover/copy`）与 WhitelistArgs 新具名字段；
- S6：审计动作新增（`repair.run`、`tag.update`、`hook.run`、`key.rotate`）——走 `AUDIT_*` 常量；
- S1/S3：新 env 前缀（`RCLONE_/AZURE_/GOOGLE_/REST_/SSH_`）与新凭据字段入加密面；
- 迁移纪律不变：只新增 `000N_*.sql`，幂等测试。

## 6. 里程碑建议

1. P3（R-023/024/025）提交收尾 → **批次 A**（✅ A0 已交付 2026-10-17 → ✅ A7 已交付 2026-10-17（规范 S7 v1.4 先行签署）→ ✅ A1 已交付 2026-10-17 → ✅ A2 已交付 2026-10-17 → ✅ A3 已交付 2026-10-17 → ✅ A8 已交付 2026-10-17；后端 + 规范 §3.1 修订 + 镜像）→ **批次 B**（✅ A4 已交付 2026-10-17 → ✅ A5 已交付 2026-10-17 → ✅ A6 已交付 2026-10-17；纯前端，可与 C/D 并行）→ **批次 C**（✅ B1 已交付 2026-10-17 → ✅ B2 已交付 2026-10-17 → ✅ B3 已交付 2026-10-17；repair/recover JobType + 审计 + ConfirmGate + 一键修复建议链 + 部署指南 §7.1 RecreateDatabase 等价说明）→ **批次 D**（✅ C1 已交付 2026-10-18 → ✅ C2 已交付 2026-10-18 → ✅ C3 已交付 2026-10-18 → ✅ C4 已交付 2026-10-18；钩子/邮件/重试/多目的地+copy+key，部署指南 §7.2）→ **批次 E**（多仓库落地后的配置导入/导出）；
2. 每批次独立 PR、独立回归（pytest + 前端三件套 + verify_redlines + 真机 E2E），按特性拆分提交；
3. 本文结论若被 owner 采纳，蒸馏进《Web版改造方案.md》新章节后归档本文。

> **交付记录（2026-10-17）**：
> - **A0**：host 模式整体退役（后端 14 文件 / 前端 12 文件 / compose / 红线探针扩前端 / 测试全域 / 文档全域含 archive 注记）；补漏记录：`INPLACE_TARGET_ROOT='/host'`（api/restore.ts）曾因机器探针只扫 .py 而漏出，前端原位恢复会全量 400——探针已扩到 TS/Vue。
> - **A7**：原位恢复 Duplicati 化。S7 v1.4 签署（三重确认门→单次确认、固定 always→never/always 布尔）；后端契约 `overwrite_mode: never|always`、keyword 字段删除（extra=forbid 反证）；前端「原位置」默认 + 覆盖勾选（默认不勾）+ ConfirmGate 无关键词可选模式；语义差异如实标注（Duplicati overwrite=false = 冲突改名另存，restic 取最近似 never=跳过）。真机 E2E 待跑（本机无 docker）。
> - **A1**（2026-10-17）：存储后端扩展至 restic 0.19.1 全部七后端（local/s3/b2 既有 + sftp/azure/gcs/rest/rclone 新增）。后端：`RepoType` 八类型（`gs:` 前缀陷阱 docstring 标注）；`derive_repo_type` 前缀映射表 `_PREFIX_TO_TYPE` 单一事实源（type 恒派生不落库）；`_validate_repo_url` 严格校验（sftp 双形态 + 密码检测 422、azure/gs 语法、rest 拒 URL userinfo、rclone remote 须在 `listremotes` 清单内）；新服务 `services/rclone.py`（`rclone listremotes` 只读探测，缺二进制/超时/非零退出全降级 + 可操作 reason）；新端点 `GET /api/rclone/remotes`（E45）；`env_sync.ENV_KEY_MAP` +9 项（AZURE_×4/GOOGLE_×3/RESTIC_REST_×2）+ `load_credentials` 5 显式分支；`restic.ENV_ALLOWED_PREFIXES` 扩至七前缀（`REST_` 不新增——`RESTIC_REST_` 已被 `RESTIC_` 覆盖）；`crypto.py` 零改动（rclone 凭据零落库，azure/gcs/rest 走既有加密凭据通道）。镜像：Dockerfile 装 rclone + openssh-client；compose `rclone.conf` 挂载 **`:rw`**（OAuth token 回写硬约束，bind 目录顶替陷阱注释 + `touch` 指引）+ ssh 私钥/known_hosts 挂载示例。前端**最小配套**（计划 A1 行仅列后端文件，但缺此前端则新类型 UI 显示「本地」且无凭据入口，故纳入 A1 并在汇报中标注此偏差；卡片化重排随 A5）：typeLabel 泛化 + CRED_FIELDS 三类型 + rclone conf 状态/探测失败提示 + `listRcloneRemotes()`。测试：`tests/test_repo_backends.py` 58 用例首跑全过（derive 矩阵/URL 参数化/凭据映射/env 注入越界拦截/探测六场景/API 四用例含端点契约冻结）；ruff + pytest 630 passed + 前端 typecheck/vitest 54/build 全绿。真机 E2E 待跑（本机无 docker）。
> - **A2**（2026-10-17）：forget 保留档位补齐（Duplicati keep-time / retention-policy 分段的 restic 等价物）。后端：WhitelistArgs 增六时长档（`keep_within`/`keep_within_{hourly,daily,weekly,monthly,yearly}`，restic duration 形态）+ `keep_tags`（`--keep-tag` 清单，OR 语义）；拼装层 `_normalize_duration` 校验前移（形态 422、**重复单位 422**——restic 后者覆盖属静默歧义、全零归一未启用——与 `keep_*` 的 0 口径一致）；0004 迁移 `backup_tasks` +7 列（user_version 4）；TaskService.normalize 写侧 fail-fast（时长形态 + keep_tags 去重/flag 注入防护）；**G3 兜底门扩为三门并列**（计数/时长/标签任一即放行，对齐 restic `ExpirePolicy.Empty` 含 Within/Tags）——ops.require_keep_rule、JobRunner forget 分支、调度器自动 forget 预检三处同口径；E21 预览请求/E12 params 回显同扩七字段（`keep_tags` 回显键与 filter 的 `tags` 区分）；**`retention.py` 未动**（该文件是 R-020 jobs/日志保留，与快照保留无关——计划行文件清单据此修正）。前端：向导保留区新增时长档/标签档输入（ScheduleRetentionForm + TaskWizard 往返），快照页 forget 卡片同步两输入 + 任务回填；i18n 双语。测试：`tests/test_retention_within.py` 45 例（duration 参数化/argv 拼装/归一/仓储往返/G3 三门/profile 回退/ops 门与映射）+ test_migrations 0004 两例；ruff + pytest **632 passed** + 前端三件套全绿。真机 E2E 待跑（本机无 docker）。
> - **A3**（2026-10-17）：快照版本标签（Duplicati 版本标签的 restic 等价物）。后端：`VERBS` 增 `"tag"`（此前 10 个 +1）；`WhitelistArgs` 增 `tag_add`/`tag_remove`（每元素一条 `--add`/`--remove`，各经 `_assert_safe_value` 禁 `-` 开头/NUL/换行）——**与既有 `tags`（forget/snapshots 的 `--tag` 筛选维度）语义隔离、不复用**；`_positionals` 把 `tag` 纳入可挂位置参数（快照 id 列表）；`_verb_flags` 新增 `tag` 分支（空 add/remove → `ValidationError`）；新增 `_assert_snapshot_id`（8~64 小写十六进制）；新常量 `AUDIT_TAG_UPDATE` + `TAG_OPERATION_TIMEOUT_SECONDS=60`。新端点 **E46 `POST /api/snapshots/tag`**（`dependencies=_PROTECTED`，同步 `run_json`，援 forget 预览 E21 先例）：请求 `{snapshot_ids?, add[], remove[], tag_filter[]?, host?}` → 服务端 422 三道护栏① add/remove 皆空 ② 无 snapshot_ids 且无 tag_filter/host（避免改全仓库）③ 任一 snapshot_id 非法；`WhitelistArgs(tags=tag_filter, host=host, tag_add=add, tag_remove=remove, extra_positionals=snapshot_ids)`；逐行解析 restic JSON `changed{old_snapshot_id,new_snapshot_id}` + `summary{changed_snapshots}` → `{changed_count, changed[], truncated}`；`audit.record(AUDIT_TAG_UPDATE, ...)`。`tag` 动词取 restic 独占锁（与备份并发 exit 11 锁冲突由 `failure_reason` 明示「仓库锁残留」）。前端：snapshots/index.vue 在 forget 卡片后新增「快照标签」卡片（添加/移除两 n-input + 目标快照 ID n-input + 应用按钮 + 结果 alert，成功后 `refetch` 快照列表）；api/snapshots.ts 增 `tagSnapshots(payload)`；types.ts 增 `TagChanged`/`TagSnapshotsResponse`/`TagSnapshotsRequest`；i18n 双语 `snapshot.tag.*`。测试：`tests/test_restic_tag.py` 6 例（VERBS 含 tag / argv 拼装 / 过滤维度 / 空 add-remove 拒 / 领先 `-` 拒 / 仅 add 无位置参数）+ `tests/test_api/test_snapshots_tag.py` 7 例（成功映射 / 空 add-remove 422 / 无目标 422 / 非法 id 422 / tag_filter·host 维度 / restic 失败 500（锁冲突文案）/ 未登录 401）；**restic `cmd_tag.go` 源码级事实核验**：`--set`/`--add`/`--remove`（TagLists）+ `--tag`/`--host`/`--path`/`--group-by`；约束① 无 set/add/remove → "nothing to do!"；约束② 无 snapshotID 则改**全部**匹配快照（高危）；JSON `changed`/`summary`；改标签生成新快照对象（原→新 id）。**本批次只实现 `--add`/`--remove`，不引 `--set`**（保持 Duplicati 标签等价物简洁）。回归：ruff + pytest **690 passed**（677+13）+ verify_redlines 18 ✓ + dockerignore ✓ + 前端 typecheck ✓ + vitest 54 ✓ + build ✓。真机 E2E 待跑（本机无 docker）。
> - **A8**（2026-10-17）：仓库 ID 自动生成（owner 2026-10-17 07:37 立项）。后端：`RepoCreateRequest.id` 由**必填改为可选**（`str | None`，`Field(default=None)`）；为兼容旧客户端，`field_validator("id", mode="before")` 把空串/纯空白归一为 `None`（否则会被 `pattern` 拒为 422）；`repos.py` 新增 `_new_repo_id()`（`f"repo-{new_ulid().lower()}"`，复用 `utils/ulid.py`）与 `_resolve_repo_id(repos_repo, provided)`——显式提供原样返回，留空则生成 + `exists` 查重 + 碰撞重试（上限 10 次，理论不可达）。新端点 **`GET /api/repos/_new_id`**（声明在 `/repos/{repo_id}` 之前避免被路径参数捕获）返回 `RepoIdSuggestion{id}` 候选标识供前端预填。契约不变：`repo_id` 语义、`passfile_path` 拼装、`X-Repo-Id` 头全部照旧；撞名仍是 422（非 409，修正了架构/E40 旧文「409」的误记）。前端：repo/index.vue 新建弹窗在 `openCreate` 时调 `suggestRepoId()` 预填 + 「换一个」按钮（`regenerateRepoId`）可改可不改；`onCreate` 发送 `id: createForm.id.trim() || undefined`（空则省略→服务端生成）；i18n 双语（`idAutoHint`/`idAutoNote`/`regenerate`）；types.ts `RepoCreateRequest.id` 改可选 + 新增 `RepoIdSuggestion`。测试：`tests/test_api/test_repo_a8.py` 11 例（生成器格式/唯一性/`_resolve_repo_id` 显式直通与留空生成/POST 缺省生成/空串生成/显式使用/撞名 422/非法 pattern 422/`GET _new_id` 候选/不被路径参数捕获）。回归：ruff + pytest **701 passed**（690+11）+ verify_redlines 18 ✓ + 前端 typecheck ✓ + vitest 54 ✓ + build ✓。真机 E2E 待跑（本机无 docker）。
> - **A4 / A5 / A6**（2026-10-17，批次 B 纯前端三件，后端零改动）：对齐 Duplicati Add backup 向导体验。
>   - **A4 源侧远端目录**：`components/task/SourcesPicker.vue` 在目录树点选之外新增「手动输入挂载路径」入口（n-input + 加入按钮）；前端基础防护——非空、须绝对路径、含 `..` 穿越段拒收——落库时后端 `PathMapper.to_snapshot` 还原为 `/sources/<path>`（恒在 `/sources` 白名单内，与 A0 收敛后唯一形态一致）；与树点选同形态入库、去重。i18n 双语 `wizard.manualPath*`/`pathInvalid`。文档：`docs/部署指南.md` §2.3 新增「2.3.1 远端源挂载指引（A4）」专节——三种挂载路线（NFS/SMB、rclone mount 70+ 云、其它 FUSE）、compose 映射约定（`/sources/<remote-name>/` 子目录）、UI 填法、对象存储当源的性能（海量小文件慢/请求费）与一致性（VFS 缓存）告警、云到云复制走 `rclone copy` 不绕本机。
>   - **A5 目的地类型选择卡**：`pages/repo/index.vue` 新建仓库弹窗重构为「目的地类型卡片网格（8 类：本地/S3/B2/SFTP/Azure/GCS/REST/rclone）+ 按类型条件渲染 URL 表单」；rclone 下含 **OneDrive/Google Drive/S3 兼容/其它** 四张预设快捷卡（点击预填 `rclone:<remote>:<path>` 地址模板，远端名须与宿主机 conf 约定一致，视觉等价 Duplicati、实际全部可用、不做假卡）；点选驱动 URL 占位/格式提示与按类型说明；**类型仍由后端从 url 派生（A1 `derive_repo_type`），零后端改动**；凭据留待创建后在「当前仓库配置」页填写（该页 `CRED_FIELDS` 已按类型条件渲染）。i18n 双语 `repo.dest.*`（label/hint + 8 类型 placeholder/note + rclone 预设）。
>   - **A6 调度双模式**：门槛降到 Duplicati 级。`components/task/ScheduleRetentionForm.vue` 重构为「不调度 / 简单模式（默认）/ 高级模式」三段切换——简单模式三控件生成 cron：频率下拉（每小时/每天/每周/每月/每年/每 N 小时/每 N 天）+ 时间点（时/分两数字输入）+ 日子多选（每周→星期、每月→几号、每年→月+几号），全程不露 cron 字样；高级模式直编标准 crontab 5 字段；**双向解析**——新增纯函数 `utils/cronConverter.ts`：`simpleToCron` 只产出受控子集（恒可反向解析），`cronToSimple` 解析不了的复杂表达式（如 dom 与 dow 同时非通配的 OR 语义、`*/N` 错位、范围/名称写法）返回 `null` 自动落高级模式，不丢信息。**后端零改动**：DB 仍存 cron 字符串，调度语义/APScheduler/`policy_service._normalize_cron` 星期修正护栏全复用；星期按标准 crontab（0=周日…6=周六），与后端换算对齐。新增 `utils/__tests__/cronConverter.spec.ts` 37 例（生成/解析/往返/边界/落高级）。i18n 双语 `wizard.schedule*`/`freq.*`/`dow.*`；清理了旧 `cronTemplates` 死键。
>   - 验收（批次 B）：前端 `typecheck` 0 错误 → `vitest` **91 passed**（含 cronConverter 37 + i18n-parity + naive-imports + VirtualTree 7）→ `build` 通过。**后端未改动故 pytest/红线/dockerignore 不变**（701 passed / 18 / 通过）。真机 UI 旅程待跑（本机无 docker）。
> - **批次 D / C1（2026-10-18）**：任务级前后钩子（Duplicati RunScript 等价物）。0005 迁移 `backup_tasks.pre_cmd/post_cmd`（空串 = 未配置）；新服务 `services/hooks.py`——命令经 `shlex.split` 切分 argv **不经 shell**（S2/S9 用户命令执行边界），`to_thread` + 强制超时，输出进任务日志 `[hook]` 行；pre 在 restic 阶段前执行，post 仅主备份成功后执行；钩子失败计任务失败；审计 `hook.run`（detail 不含命令行原文——只记 phase/exit_code/耗时）。规范 S9「用户命令执行边界」随本项先行签署。
> - **批次 D / C2（2026-10-18）**：SMTP 邮件通知渠道（owner 裁决：只加邮件，Telegram 不做）。E23 配置面扩 `mail_*` 九键（host/port/username/password/from/to/security/redact + `mail_password_set`），凭据入加密存储只回布尔；`notifier.py` 增 mail 渠道（STARTTLS/TLS/none 三档）；E24 逐渠道测试结果含 mail。前端通知页 SMTP 表单，密码三态（留空不变 / 清除删除 / 新值覆盖）与 healthchecks URL 同口径。部署指南 §7.2 收录出站端口与授权码说明。
> - **批次 D / C3（2026-10-18）**：任务级失败重试（对齐 Duplicati number-of-retries）。0006 迁移 `backup_tasks.retry_count`（0–5，默认 2）；调度触发失败按指数退避自动重试，**手动触发不重试**；重试次数耗尽才计终态失败并触发通知（避免重试中误告警）。
> - **批次 D / C4（2026-10-18）**：copy + key + 任务级多目的地（owner 裁决升级为任务级多目的地，对齐 Duplicati「+ Add another destination」）。后端：`VERBS` 增 `copy`/`key`（copy 拒 `--exclude`、**目的仓 url/口令文件走 `secondary_repo_env` 注入 env 绝不进 argv**；key 两级命令，子命令 `add/list/remove/passwd` 走具名字段，`passwd`/`add` 用 `--new-password-file`——新密码值只落临时文件路径）；0007 迁移 `backup_tasks.mirror_repo_ids`（JSON 数组，`_normalize_mirror_ids` 保序去重，**超限 >3 硬拒 422**——静默截掉镜像会谎报副本成功）；JobRunner **镜像阶段**——主备份成功后串行 copy 到各镜像仓（copy 无 JSON 输出，逐行 tee 消费；增量按 `--tag task-<id>`，restic 侧跳过目的仓已有快照，失败下次自动补齐自愈），**失败隔离：镜像失败不翻转主目的地终态**（主仓数据已落好，标 failed 属谎报——Q4 静默防线反面），自指/缺仓/缺口令全部显式记账（日志 `[copy]` 行 + 审计，绝不静默跳过）；新端点 `POST /api/repos/rotate-key`（restic key passwd + 成功后重写口令文件，新密码不回显不落审计明文）与 `POST /api/copy`（手动搬家，超时 3600s，增量可续传）；审计 `copy.run`/`key.rotate`（S6 清单 11→13 项）；任务写路径 `_validate_mirror_ids`（存在性/自指/无地址 422 前置拦截，与运行期优雅降级双保险）。**测试期抓出实现级 bug**：自指镜像原直接 return 绕过 `_finish_mirror`，静默无审计——与「绝不静默跳过」纪律矛盾，已修复（skipped 也走记账）。前端：向导步骤 1 钩子双输入 + 重试计数、步骤 3 镜像仓多选（候选排除当前仓，未配地址/密码禁选）；仓库状态卡「密钥轮换」弹窗（新密码两次输入 ≥10 位）与「复制快照到…」弹窗（目标仓选择 + 结果回显含续传提示）；api/types.ts + repo.ts 全量契约字段；i18n 双语。回归：ruff + pytest **811 passed**（C4 新增 27 例）+ verify_redlines 18 ✓ + 前端 typecheck 0 错误 + vitest **91 passed**（i18n-parity/naive-imports 护栏过）+ build ✓。文档：架构 v1.6-D 修订注记 + 部署指南 §7.2（首 copy 全量/失败隔离/续传/出站端口）。真机 E2E 待跑（本机无 docker）。
