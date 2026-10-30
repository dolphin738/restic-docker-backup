# 更新日志（Changelog）

本项目遵循语义化版本（SemVer）。所有 notable 变更记录于此。

## 架构设计文档修订史

> 《docs/架构设计文档.md》v1.0–v1.11 的全部修订记录，2026-10-30 由该文档文首**整体移入**，内容逐字保留、未做裁剪。
> 《架构设计文档.md》自 v1.11 起**只记载当前契约**，不再内联修订历史——历史属于按需查阅的参考，不该占据每次都要读的契约前排。
> 反向指针：[架构设计文档](../docs/架构设计文档.md)（当前契约）

> 版本：v1.11（2026-10-29 / v1.10 2026-10-27 / v1.9 2026-10-23 / v1.8 2026-10-23 / v1.7 2026-10-20 / v1.6 2026-10-17）
> **v1.11 修订（2026-10-29，R-020 审计保留策略：配置键与端点补登记；无代码改动）**：
> - §5.0 配置键表补 **4 个审计保留键**（2026-10-26 已落地、本表此前漏登）：`retention.audit_days`（默认 365，`<=0` = 不启用自动清理；**先 gzip 归档再删除**，删除只经唯一受控出口 `AuditRepo.purge_older_than`，归档失败即跳过）、`retention.audit_archive_days`（归档文件自身保留期，默认 365）、`retention.audit_archive_dir`（缺省回落 `settings.audit_archive_dir` = `/data/audit/archive`，写侧过 `_is_safe_path`）、`retention.audit_archive_cold_target`（**仅占位**，应用不消费）。
> - §5.1 端点表补 **E52 `GET /api/audit/export`** / **E53 `GET /api/audit/archive`** / **E54 `POST /api/jobs/cleanup`**（同为 2026-10-26 落地、此前漏登）；E26 段加交叉引用。
> - 方案源与偏差记录：已归档的《任务历史与审计日志保留策略方案.md》（`docs/archive/`）v1.1（2026-10-29 代码复核**零推翻**：四个键、受控 DELETE 出口、三个端点均与代码一致；其设计取舍与五条裁决已吸收进《Web版PRD.md》v1.17）。
> **v1.10 修订（2026-10-27，rclone 配置管理工具：Q17 有边界放宽）**：
> - **新增 E48–E51**（§5.1）：`GET /api/rclone/configs` / `POST` / `PUT` / `DELETE /api/rclone/config/{remote_name}`——conf 段的查询、生成、编辑、删除。**Q17 档位有边界放宽**：仅**非 OAuth 类型**（crypt/webdav，凭据由用户自填）可生成与字段级编辑；OAuth 网盘段不可编辑（真实恢复须跑 `rclone authorize` 授权流），但**可删除**（清理废弃远端，确认门加重提示 + 审计留痕）；宿主机手工配置的 s3/sftp 同理（删除放开、编辑待进注册表）。
> - **写入路径 = 官方 rclone CLI**（`config create/update/delete`，`--obscure` 内置）而非自研 INI 手术：官方自带临时文件 + 双 rename 原子替换 + 备份。**由此 conf 挂载形态从单文件改为目录**（`./rclone:/etc/rclone:rw`）——真机实证单文件 bind mount 下 CLI 写入**退出码 0 但零落盘**且写操作挂住（详见《rclone配置生成工具设计方案.md》§12）；该修正同时使 OAuth token 回写真正可用（原「必须 `:rw`」的前提）。
> - 新增错误码 `RCLONE_UNAVAILABLE`（503）；审计动作 ×3 `rclone.config_append`/`_edit`/`_delete`（S6 清单 17→20，见《代码审查规范》v1.14）。§3 目录树与 §11 追溯矩阵 R-022 行同步。
> **v1.9 修订（2026-10-23，远端仓库明文暴露面收口：init 后密钥身份中性化 + 只读密钥可见性）**：
> - **init 后密钥身份自动中性化**（新服务 `services/repo_keys.py` 的 `neutralize_repo_key()`）：restic `keys/<id>` 是仓库里**唯一明文**文件，其 `username`/`hostname` 落在对象存储上对云商可见 → init 成功后自动 `key add --user svc-backup --host repo --new-password-file <**init 用的同一份**口令文件>` 再造一个中性 key，随后 `--key-hint <新 id> key remove <旧 id>` 删掉带真实身份的那个（**口令不变**，用户无感）。**幂等**（已中性化/多 key 即跳过）、**不抛**（任一步失败只记日志并返回 `False`，绝不把已成功的 init 反转成 failed）。覆盖两个 init 落点——`rebuild` 路由与作业类型 `init`（JobRunner success 终态）；未来新增 init 路径须复用同一函数（接入三要素、覆盖边界与最大坑见《云端仓库安全基线.md》§4.4）。
> - **新端点 E47 `GET /api/repos/{repo_id}/keys`**（§5.1，只读）：`restic key list --json` → `{overall, keys[], probe_error}`，`overall` 四态 `neutralized`/`exposing`/`mixed`/`unknown`。**只回展示白名单字段**（`id`/`id_full`/`user`/`host`/`created`/`created_tz`/`current`/`neutral`）；`data`（master key 密文）与 KDF 参数（`salt/N/r/p`）**绝不进 API**（少给一份仓库指纹）。**不复用 E42 的 30s TTL 缓存**——「是否已中性化」是安全判定，缓存会误导；无审计（只读探测，援 E45 先例）。
> - **白名单扩**（`services/restic.py`）：`WhitelistArgs` 增 `key_user`/`key_host`/`key_hint`；`--key-hint` 落 `_global_flags`（verb **之前**，`_assert_key_id` 校验「8~64 位小写十六进制」），`key add` 的 `--user`/`--host` 过 `_assert_safe_value`。**`--key-hint` 是必需项而非可选优化**：新旧 key 同口令时 restic 先解出哪个不确定，不加会撞 `refusing to remove key currently used`（restic 0.18.0 真机实证）。
> - 前端：仓库状态卡新增「密钥身份」小节（`pages/repo/KeyIdentity.vue`，自带 `useQuery(['repo','keys',id])`，不并入页面 30s 状态查询），与左侧连接态**左右并排**、按内容收缩 + 垂直居中、**无分隔线**，三条告警（last_error / 口令文件缺失 / 锁残留）通栏下沉到分栏下方；≤640px 回落上下堆叠。`created` 由 restic 按子进程 TZ 渲染且**不带时区标记** → 服务端补 `created_tz` 标注并**不换算**（C8 单一时间语义）。
> - §7.1 `ResticCmd.build` 的 verb 清单补齐 `copy`/`key`/`repair`/`recover`（v1.6-D / 批次 B 已落地但签名块漏列），并删去重复的旧块。
>
> **v1.8 修订（2026-10-23，UI 改造 P7 收口；端点表补齐）**：① 新增 **E36b `POST /api/repos/{repo_id}/backup-tasks/{id}/export`**（单任务配置导出，`kind=backup-task`；导入侧按 kind 分流，见 §5.1）；② 新增 **E18b `POST /api/snapshots/{id}/files/search`**（快照内文件搜索，服务端过滤 + 游标同 E18）；③ **E16 增 `?force=`**（Abort 二段：跳过宽限直接 SIGKILL，审计分流 `job.abort`，规范 §3.1 S6 15→16 项）；状态机不变。
> **v1.7 修订（2026-10-20，前端仓库页「目标位置」模块复刻 + Windows 子进程防护；API 契约不变）**：
> - **S3 URL 方言修正（事实核验，推翻此前拼法）**：restic 0.19.1 原生语法为 `s3:<endpoint>/<bucket>[/<prefix>]`——**endpoint 与 bucket 之间是斜杠**（冒号仅用于协议头或 `host:port`）；此前按 `s3://bucket/...` 组译，落库即错地址。前端字段组改为「endpoint（必填）+ bucket + prefix」，path 为空时不补尾斜杠（与解析严格互逆）。同期**退役** URL 区域段与死凭据键 **`AWS_ENDPOINT_URL`**（实证：restic 0.19.1 不读该环境变量，其 minio-go v7.1.0 依赖零引用）；区域语义由 `cred.aws_default_region` 承担。全八类型逐一比对结论：**仅 s3 一种错误**，b2/gs/azure/sftp/rest/rclone/local 均符合 restic 原生。
> - **子进程事件循环硬约束（Windows，新增 §7.10）**：uvicorn 裸 `--reload` 在 Windows 强制 `SelectorEventLoop`，其 `create_subprocess_exec` 抛 `NotImplementedError`（`asyncio/base_events.py:539`）→ restic/rclone **全部子进程操作 500**（真机实证：rclone 探测报「服务器内部错误」）。四层防护落地：`constants.SUBPROCESS_LOOP_HINT`、`rclone.list_remotes` 降级 `available=false` + `reason`、`restic.spawn` 转 `ResticFailedError`、`main.py` lifespan 启动自检 error。**启动纪律：本机必须 `--loop none`**（`dev-scripts/dev.ps1 restart backend` 已内置）；Linux/Docker 无此问题。护栏 `tests/test_subprocess_loop_guard.py`。
> - 前端同步：仓库页两态交互（列表态 ↔ 编辑态整页替换）、类型驱动字段组、测试按钮状态机（新建态「创建并测试」，后端 `testRepo` 读已落库配置故顺序恒为先存后测）、审计日志动作名/详情中文化（i18n 嵌套键 + `te()` 回落）。后端除上述防护外**零业务改动**。
> **v1.6 修订（2026-10-17）**：R-023 补遗——新增 **E44 `POST /api/repos/{repo_id}/password`**（§5.1）：首次写入仓库密码文件，生成场景一次性回显明文、已存在文件拒绝覆盖（密码同时是仓库密钥与 R-025 凭据加密密钥源）；同批登记 **`repo_id` 命名规则**（`REPO_ID_PATTERN`，不含 `.` 与 `/`，schema 侧 + 路径参数侧双校验，防路径穿越）。§3 目录树 repos.py 标注改 E39–E44；§11 追溯矩阵 R-023 行与 §13 E6 口径同步；部署指南同步「新仓库密码文件的两条获取路径」。
> **v1.6-A0 修订（2026-10-17，owner 裁决「去掉 host 模式」）**：**运行模式收敛为单一 `/sources` 形态**——`settings.restic_web_mode`/`host_mount_mode`/`host_root` 与 `RunMode`/`HostMountMode` 枚举已删除（开发期无存量数据，不做兼容）；备份源/浏览/原位恢复前缀白名单全部收敛为 `/sources`+`/restore`；`/me` 响应只剩 `user` 字段（运行模式卡收敛为单轴）；E32 排除集无 `mode` 入参/响应；compose `RESTIC_WEB_MODE`/`HOST_MOUNT_MODE` 退役。**本文 §12/§14 中 2026-10-16 及更早的「host 模式 / `mode=host` / `host_mount=rw` / `/host/*`」真机取证与缺陷记录均为 A0 前历史事实**（取证当时系统确实运行在 host 模式），结论的方法论与缺陷形状仍成立，路径示例已按新形态更新；**当前契约以代码与本文修订后正文为准**。原位恢复 overwrite 布尔化/单次确认门（A7）属后续批次，届时再修订 S7 相关表述。
> **v1.6-A1 修订（2026-10-17，Duplicati 对齐计划批次 A1）**：**仓库类型扩展为 restic 0.19.1 全部七后端**——`RepoType` 增 `sftp`/`azure`/`gcs`/`rest`/`rclone`（`derive_repo_type` 前缀映射表单一事实源；gcs 的 restic 前缀是 `gs:` 非 `gcs:`）；新增 **E45 `GET /api/rclone/remotes`**（§5.1，只读探测）；URL 严格校验（Q20：rest/rclone 前缀+remote 在册校验，s3/b2/local 不动）；`ENV_ALLOWED_PREFIXES` 增 `AZURE_/GOOGLE_/RCLONE_/SSH_`（§7.1）；镜像装 rclone + openssh-client（§2）；conf 宿主机挂载 `:rw`（Q17 硬约束）。§3 目录树与 §11 追溯矩阵 R-022 行同步。
> **v1.6-A2 修订（2026-10-17，Duplicati 对齐计划批次 A2）**：**forget 保留档位补齐**（Duplicati keep-time / retention-policy 分段的 restic 等价物）——`backup_tasks` 新增 `keep_within`/`keep_within_{hourly,daily,weekly,monthly,yearly}` 六时长档（restic duration 形态 y/m/d/h，如 `1y5m7d2h`）与 `keep_tags`（`--keep-tag` 清单，OR 语义）共七列（0004 迁移，§4.8）；E21 预览请求与 E12 执行 params 同扩七字段；**G3 兜底门扩为三门并列**（计数/时长/标签任一即放行，对齐 restic `ExpirePolicy.Empty` 含 Within/Tags）；duration 形态校验前移至拼装层 `_normalize_duration`（restic 端 Fatal 无 JSON 产出，§7.1 V4 事实 3 同型），**本项目 stricter**：重复单位 422（restic 后者覆盖属静默歧义）、全零归一未启用（与 `keep_*` 的 0 口径一致）；写侧 `TaskService.normalize` fail-fast 校验 + `keep_tags` flag 注入防护；调度器自动 forget 预检同步扩三门。前端向导保留区新增时长档/标签档输入，快照页 forget 卡片同步。

> **v1.6-A3 修订（2026-10-17，Duplicati 对齐计划批次 A3）**：**快照版本标签**（Duplicati 版本标签的 restic 等价物）——`VERBS` 增 `tag` 动词；`WhitelistArgs` 增 `tag_add`/`tag_remove`（每元素一条 `--add`/`--remove`，`_assert_safe_value` 防注入）；`_verb_flags` 新增 `tag` 分支 + 位置参数挂 `extra_positionals`（快照 id）；快照 id 经 `_assert_snapshot_id`（8~64 小写十六进制）校验。新端点 **E46 `POST /api/snapshots/tag`**（§5.1）：`{snapshot_ids?, add[], remove[], tag_filter[]?, host?}` → `restic tag --add/--remove` → `{changed_count, changed[], truncated}`（restic `changed`/`summary` JSON 解析）；restic `cmd_tag.go` 源码核验的两道约束前移为服务端校验——(1) `add`/`remove` 皆空 → 422（restic 端 "nothing to do!"）；(2) 无 `snapshot_ids` 且无 `tag_filter`/`host` → 422（restic 无 id 即改**全部**匹配快照，高危）。审计动作 **`tag.update`**（constants.py `AUDIT_TAG_UPDATE`）；快照页新增「快照标签」卡片（打/去标签输入 + 可选目标快照 ID + 按当前筛选作用全部匹配快照）。
> **v1.6-A8-r2 修订（2026-10-22，owner 裁决「单字母前缀」）**：**仓库 ID 生成规则改版**——服务端自动生成由 `repo-<26 位小写 ULID>`（31 字符、不可读）改为 **`r<YYMMDD>-<6 位小写 Base32 随机>`**（14 字符，如 `r261022-k3f9x2`）。日期段取 **UTC**（C8：经 `utils/time.now_utc`，无环境依赖、跨部署可比；字典序即时间序，百年内有效）；随机段 `secrets.choice` + Crockford 字母表（剔除易混淆 i/l/o/u），32⁶≈10 亿组合/天，配 `exists` 查重 + 重试上限 10 次。单字母 `r` 前缀保留「服务端生成」辨识度，同时满足「小写字母开头」约束（纯日期开头 `261022-…` 会被 `ReposRepo.create` 拒）。**存量 `repo-<ULID>` id 全部照旧工作，零迁移**；`REPO_ID_PATTERN`、`X-Repo-Id` 头、`passfile_path` 拼装契约不变（A8 契约清单照旧）。前端预填/「换一个」/`idAutoHint` 文案同步。同日另一修订：删除仓库 `purge_disk` 由「rmtree 整目录」改为「清空目录内容、保留目录本身」+ 回收 `<passfile_root>/<repo_id>/` 空目录（见 E41 与 rebuild 段内注）。
> **v1.6-A8 修订（2026-10-17，Duplicati 对齐计划批次 A8）**：**仓库 ID 自动生成**（owner 2026-10-17 07:37 立项）——`POST /api/repos` 的 `RepoCreateRequest.id` 由**必填改为可选**，留空（null/空串，schema 前置校验把空白归一为 None）时服务端生成 **`repo-<小写 ULID>`**（26 位、全局唯一、按时间有序，天然满足 `REPO_ID_PATTERN` 与 64 长度上限；落库前 `exists` 查重 + 碰撞重试上限 10 次）。显式提供仍按既有规则校验（形态 422 + 撞名 422，非 409）。配套新增 **`GET /api/repos/_new_id`**（§5.1）返回候选标识供新建向导预填（对齐 Duplicati 自动起名；不落库，用户可改可不改；声明在 `/repos/{repo_id}` 之前避免被路径参数捕获）。契约不变：`repo_id` 语义、密码文件 `passfile_path` 拼装、`X-Repo-Id` 头全部照旧。前端新建仓库弹窗预填自动生成值 + 「换一个」按钮（可改可不改）。复用 `utils/ulid.py`（与任务 id 同一 ULID 实现）。
> **v1.6-B 修订（2026-10-17，Duplicati 对齐计划批次 B = A4/A5/A6，纯前端、后端零改动）**：三项均为**前端体验对齐 Duplicati**，后端契约与 API 不动（cron 仍存 DB 字符串、仓库类型仍由 url 派生、`PathMapper` 白名单已收敛 `/sources`）。
> - **A4 源侧远端目录**：`SourcesPicker.vue` 在目录树点选之外新增「手动输入挂载路径」入口——`manualPath` 输入校验 `startsWith('/')` 且不含 `..`、去重后复用 `emit('update:sources')`；展示形态（如 `/mnt/onedrive`）经既有 `PathMapper.to_snapshot` 归一到 `/sources/mnt/onedrive`，恒在白名单内，零后端改动。部署指南新增「2.3.1 远端源挂载指引」（NFS/SMB、rclone mount 70+ 云、其它 FUSE；compose 映射约定 `/sources/<remote-name>/`；对象存储当源性能/一致性告警；云到云走 `rclone copy`）。
> - **A5 目的地类型选择卡**：`repo/index.vue` 新建弹窗顶部新增目的地类型卡片网格（local/s3/b2/sftp/azure/gcs/rest/rclone 八类，含 scheme 前缀/URL 占位/说明）+ rclone 预设卡（onedrive/googledrive/s3compat/other，URL 模板 `rclone:<remote>:`）。卡片**只驱动 UI 提示与默认 scheme 预填**，仓库类型仍由后端 `derive_repo_type` 从 url 前缀派生；凭据留待创建后仓库配置页（`CRED_FIELDS` 已按类型条件渲染）填写。
> - **A6 调度双模式**：`ScheduleRetentionForm.vue` 调度区重做为 `none/simple/advanced` 三段 Tabs——简单模式（频率 NSelect + 时/分 NInputNumber + 星期 NCheckboxGroup + 多选用 NSelect 日期/月份）生成**可反向解析的受控 cron 子集**（hourly/daily/weekly/monthly/yearly/every_n_hours/every_n_days）；高级模式直接编辑 5 字段 `NInput`。双向解析由新文件 `utils/cronConverter.ts` 的纯函数 `simpleToCron`/`cronToSimple`/`defaultSimpleSchedule` 承担：解析不动（dom 与 dow 同时非 `*` 的 OR 语义、`*/N` 错位、范围/名称写法、字段数≠5、越界）时 `cronToSimple` 返回 `null` 自动落高级模式，不丢信息。cron **星期字段按标准 Vixie 语义（0=周日…6=周六）直写**，后端 `scheduler.py`/`policy_service._normalize_cron` 的 `(n+6)%7` 换算负责对齐 APScheduler 3.x（同 §14 X11 口径）。保留档位（keep_* + A2 keep_within* + keep_tags）逻辑沿用。
>
> **v1.6-C 修订（2026-10-17，Duplicati 对齐计划批次 C = 修复自愈）**：对齐 Duplicati Repair / RecreateDatabase / ListBrokenFiles 三操作。
> - **B1 修复仓库**：JobType 增 `repair`（E12 通用入口触发）；`ResticCmd` VERBS 增 `repair`——repair 是**两级命令**，子命令 `index`/`packs` 走 `WhitelistArgs.repair_target` **具名字段**（非自由位置参数，S2 白名单纪律），非法值 422；`JobRunner._build_args` repair 分支 `json_output=False`（repair 输出非 JSON）。审计 `repair.run`（S6 危险操作清单 9→11 项，连同 `recover.run`）。前端「仓库配置」状态卡（已连接仓库显示「修复仓库」error 按钮 + index/packs 单选，ConfirmGate 关键词 `REPAIR`）与任务历史 check 抽屉共用同一 E12 通道。
> - **B2 损坏检测报告**：`check --read-data` 输出解析 → `CheckView.broken_packs`（保序 64hex pack id 清单）+ `suggest_repair_index`（批次 B 已预实现解析与视图字段）；本次补**「一键修复索引」建议链按钮**——任务历史 check 详情抽屉在 `suggest_repair_index` 为真时展示，内联 ConfirmGate（关键词 REPAIR）→ `POST /api/jobs {"type":"repair","params":{"repair_target":"index"}}`。**文件级损坏映射为受限项**（restic 无原生命令），部署指南 §7.1 如实标注：精确定位受影响文件以受影响快照为范围做恢复演练。
> - **B3 找回未快照数据**：JobType 增 `recover`；VERBS 增 `recover`（无子命令无额外 flag，`json_output=False`），审计 `recover.run`。「重建缓存」引导（Duplicati RecreateDatabase 等价）落部署指南 §7.1：restic 无独立本地数据库、`/cache/restic` 可弃重建（E25 缓存维护），三场景对照表（清缓存 / repair / recover）+ 无界面 API 触发示例。
> **v1.6-E 修订（2026-10-18，批次 E = 配置导入/导出）**：新服务 `services/config_transfer.py` + `POST /api/config/export|import`。包格式 `restic-web-config-export` v1：明文段（仓清单/任务/`behavior.*`/`notify.*` 非敏感键）+ 加密段（`cred.*` 与敏感 `notify.*`，PBKDF2-HMAC-SHA256 600k + AES-256-GCM，错口令 GCM 拒绝）。匹配键：仓按 id、任务按 `(repo_id, name)`（任务 id 自增主键跨机不可带）；`mirror_repo_ids` 接前校验「id 在包内且 url 一致」否则置空+警告；冲突默认跳过+报告，`replace_existing` 整体替换；导入剔除 `last_run_at`/`dry_run_pending`（跨机不可带）。不导 `dump.*`/`security.*`。审计 `config.export`/`config.import`（S6 清单 12→14，detail 只含计数）。前端设置页「配置迁移」卡（导出 Blob 下载 / 导入 ConfirmGate + 报告）。
> **v1.6-D 修订（2026-10-18，Duplicati 对齐计划批次 D = 运维与通知 C1–C4）**：对齐 Duplicati RunScript / 邮件通知 / number-of-retries / copy+key+多目的地。
> - **C1 任务级前后钩子（⏸ 已暂缓下架 2026-10-18）**：整体摘除（迁移/服务/端点字段/前端/UI），设计方案与全部代码存档于 `docs/暂缓存档-任务级前后钩子C1-2026-10-18.md`，恢复实施与否另行裁决；规范 S9 条款保留为防御性规范。
> - **C2 SMTP 邮件渠道**：E23 notify 配置面扩 `mail_*` 九键（凭据 `mail_password` 入加密存储，只回 `mail_password_set` 布尔）；`notifier.py` 增 mail 渠道（STARTTLS/TLS/none）；E24 逐渠道测试结果含 mail。前端通知页 SMTP 表单（密码三态：留空不变 / 清除删除 / 新值覆盖）。
> - **C3 任务级失败重试**：`backup_tasks` 增 `retry_count`（0006 迁移，0–5 默认 2，对齐 Duplicati number-of-retries）；调度触发失败按**指数退避**自动重试，手动触发不重试；重试次数耗尽才计终态失败并触发通知。
> - **C4 copy + key + 任务级多目的地**：`VERBS` 增 `copy`/`key`（copy 拒 `--exclude`、url 走 env；key 两级命令子命令走具名字段，`passwd`/`add` 用 `--new-password-file`——密码值只落临时文件路径，绝不进 argv/日志/审计）；迁移 0007 `backup_tasks.mirror_repo_ids`（JSON 数组 ≤3）；JobRunner **镜像阶段**——主备份成功后串行 copy 到各镜像仓（`secondary_repo_env` 注入目的仓 url/口令文件，目的信息不进 argv；增量按 `--tag task-<id>`，失败自动补齐自愈），**失败隔离不翻转主终态**，自指/缺仓/缺口令全部显式记账（日志 + 审计，绝不静默）；新端点 `POST /api/repos/rotate-key`（key passwd + 重写口令文件，新密码不回显）与 `POST /api/copy`（手动搬家，超时 3600s）；审计 `copy.run` / `key.rotate`（S6 清单 11→13 项）；任务写路径镜像仓存在性/自指 422 前置拦截（运行期优雅降级双保险）。前端：向导步骤 3 镜像仓多选（未就绪仓禁选）、仓库状态卡「密钥轮换」「复制快照到…」双弹窗。
> v1.2 修订：P1 实现定案的文档同步批次——E05/E31 入参改收展示路径（兼容容器路径）、policies.sources/excludes 存储形态与三分类归一、PolicyOut 增 display_* 派生字段、E15 起始游标约定、recover_on_startup 将遗留 running+pending 一并置 interrupted（P1 裁决）、目录树补 errors.py/auth/service.py/env_sync.py、E32 归期 P2→P1、CI 依赖源固化。  
> v1.3 修订（2026-10-13）：选项②「轻量多备份任务」立项同步——新增 §13 演进位预留（E1–E7）、§11 追溯矩阵升版至 R-026、R-014 语义升级为多备份任务管理 UI。  
> **v1.4 修订（2026-10-13）：选项②已交付并正式并入**——`repos` + `backup_tasks` 表与 0002 迁移（§4.7/§4.8）、`jobs.task_id`（§4.5）、E34–E36 任务端点（§5.1）、JobRunner per-task 化（§7.1）、**SchedulerService 从零落地（§7.4，R-013 本批交付，非 P2）**、B1 文件级恢复 `--include`（§7.1/§6.3）、前端任务卡片与五步向导（§9）、追溯矩阵增 R-027（§11）、演进位 E6/E7 标注已满足（§13）。设计与实现的差异集中登记于 **§14**（本次并入以代码为准）。  
> **v1.4 追记（2026-10-13）**：§14 四项差异 owner 已全部裁决——**X2/X5/X6 按建议落实**（提交 d6bf5ca / 0e0ce6d / 7eff049，工程已实现并独立核实 193 passed），**X3 列为 P2 首个补测项**；正文 §5.1 E17、§7.1、§12 V3 已随裁决同步。  
> **v1.4 追记（2026-10-14）**：2026-10-14 真机 E2E 后 QA 独立复验提出 2 项「设计与实现/底层库语义」差异，owner 裁决**维持现状、不改代码**，已登记为 §14 的 **X10**（手动触发与调度触发的 `last_run_at` 记账时序不一致）与 **X11**（cron「日」与「星期」同时受限时 APScheduler 3.x 取 AND 而非标准 Vixie cron 的 OR）——两者在各自场景下语义均成立，强行统一代价大于收益，此处留痕备查。  
> **v1.5 修订（2026-10-15）**：**Q1「选删」——policies 遗留面全部移除**（owner 裁决，2026-10-13 原裁决「保留 legacy 只读」废止）。删除路由 E08–E11、`PolicyRepo`、`PolicyService`、E12 `policy_id` 提交入口、`PolicyOut`/`PolicyUpsertRequest`、`AUDIT_POLICY_*` 与两个错误码；共享字段校验原样并入 `TaskService`（口径逐字保留）；表与 `jobs.policy_id` 列/`JobRunner` 兼容形参保留待 0003（§4.4 注）。§13 演进位 E2/E7 口径同步。增量 PRD 与增量设计的 Q1–Q9/N1–N8 裁决已并入《Web版PRD.md》v1.4，两部过程稿转 `docs/archive/`。  
> **裁决依据**：2026-10-13 立项批次（选项②立项 + R-023 规划提前实现不提前）+ **greenfield 裁决（同日）**——本产品无存量数据，0002 迁移**不物化首任务、不建 `legacy_policy_id` 列**，原「存量迁移」需求（增量 PRD 的 R-026-07 / US-026-09）不适用。增量设计过程稿见 `docs/archive/增量设计-多备份任务-2026-10-13.md`（过程层，结论已并入本文）。  
> 上游文档：《Web版改造方案 v2.3.4》（技术决策、API 清单、分阶段计划以该文档为准）、《Web版PRD v1.4》（需求池 R-001~R-027、12 页面线框、非功能需求；US-026 与 Q15–Q24 裁决已并入）、《archive/增量PRD-多备份任务-2026-10-13.md》（R-026/B1 展开，过程稿已归档）  
> 读者：实现阶段 P2 的工程师；文档给出可直接照做的目录结构、Schema DDL、API 契约与模块签名。

## 未发布（2026-10-28）

### 变更
- **rclone 凭据落盘形态收敛为「恒为混淆」+ 新增导出原密码**（裁决 17，owner 三问）
  - 真机取证（WSL rclone v1.60.1）：明文密码写进 conf 对 **crypt 与 webdav 一律不可用**（`failed to decrypt password: input too short…`）；把混淆值再混淆一次则**不报错**、只是换一套加密命名空间（实测目录变空）。故「目标存储形态」这个概念本身被取消；
  - 后端：删除 `store_form` / `declare_form` 请求字段与入参，落盘恒为混淆（`value_obscured` 只决定 `--obscure` / `--no-obscure`）；取消「混淆→明文」就地转换；保留并加固「明文→混淆」自愈（历史明文段不填密码即可修正，转换前 reveal 探测，解得开即 422 中止防二次混淆）；**不提供导出原密码**（owner 裁决：S3 凭据零回显不开口子）——面板只给「如何找回密码」弹窗，内容为宿主机自助命令示例（`rclone config dump` + `rclone reveal <混淆值>`），本工具不执行、不读 conf、不返回明文；
  - 前端：`RcloneConfigPanel` 删「存储形态」开关 + 「我填入的已是混淆后凭证」副复选框 + 「存储形态声明」，改为单一「密码形态」下拉 + 落盘形态只读徽标 + 「修正为混淆」+ 「如何找回密码」（命令示例弹窗）；补齐面板缺失的 `repoint*` 文案键（此前一直显示原始 key）；
  - 测试：后端相关两文件 **100 passed**（新增自愈 / 探测中止 / 误填拦截），面板 **22 passed**（含「找回密码只给命令示例」与复制断言）。
- **仓库页 rclone 表单增强**（问题 3 落地）
  - 远端名输入框**左侧**新增「选择远端 ▾」下拉，列出 conf 里探测到的段名，点一项回填到输入框（输入框仍可手输）；
  - **删除 rclone 预设快捷卡**（OneDrive / Google Drive / S3 / 其它四张卡）及其文案；
  - 「管理远端」按钮从远端名输入框右侧移到**编辑仓库卡右上角**；仓库 ID 从卡右上角移到**左上角标题「编辑仓库」右侧**；
  - 地址提示精简为「地址形如 rclone:远端名:路径（如 rclone:myremote:mybucket/restic）」；
  - 远端名 + 远端路径都填好后显示**实际落盘路径**：crypt 段按它的底层 `remote` 拼接（`remote = /home/222` + 路径 `restic` → `/home/222/restic`），非 crypt 段按 `remote:path` 示意；推导抽为纯函数 `utils/repoUrl.ts:resolveRcloneTarget`（7 例单测）；
  - 同一远端已有仓库时提示，同路径额外警告「会互相覆盖」；
  - 「远端路径」补必填星号 + 保存前校验（后端本就强制非空，此前只是把 422 推迟到保存时）；
  - rclone 配置面板：编辑态 password / password2 下新增风险提示「修改后旧快照将永久无法解密」。
- **恢复流程并入快照浏览页**（owner 决策：删「恢复向导」独立页面，三步流程全部复用）
  - 删除 `/restore` 恢复入口页与 `/restore/:id/{select-files,options,progress}` 三个向导路由，`frontend/src/pages/restore/` 整目录移除；
  - 三步流程组件（选文件 / 恢复选项 / 执行与结果）原样复用，迁入 `pages/snapshots/restore/`，容器改为快照页右侧宽抽屉（桌面 1080px / 移动端全屏）；
  - 入口归一：快照列表行内新增「恢复」按钮 + 文件树抽屉「恢复此快照」，均预选该行快照；原入口页「从配置恢复 / 从其他仓库恢复」两张卡收敛为快照页卡片头部两个轻量入口；
  - 深链改 `?restore=<id>`（快照页读 query 自动开抽屉、关闭即清 query）；旧 `/restore*` 地址**不做兼容**——核实全仓无一处在用（文档里只有代码路径与 `/api/restore/*`），唯一使用方 e2e 探针已改新路径；
  - 关闭面板改抽屉级确认，脏判定收紧（已选文件 / 已进步骤 2 / 作业未终态），面板一打开即关不再弹确认；
  - 导航「数据」组撤「恢复向导」项（主菜单 8 → 7 项）；仪表盘三处恢复入口改跳快照页；
  - 测试：新增 `snapshots/__tests__/restore-drawer.spec.ts`（7 例：入口 / 预选 / 深链 / 关闭清 query / 三条旧地址转址），`flow-shell.spec.ts` 重写为 `restore/__tests__/panel.spec.ts`（面板步骤推进 + 关闭脏判定），`entry.spec.ts` 随入口页删除；
  - e2e 探针 p14/p16/p17/p21 入口路径同步改为 `/snapshots?restore=<id>`（p16 增加一次「下一步」点击，因为面板不再支持直达步骤 2）。

## v1.0.4（2026-10-22）

### 新增
- **R-023 多仓库支持**
  - 后端：多仓库核心模型，维护校验作业覆盖全部已配置仓库。
  - 前端：多仓库管理、仓库删除与重建页面（R-016 / R-023）。
- **R-018 通知服务**
  - 后端：通知服务（渠道类型 + 已保存渠道列表）。
  - 前端：通知设置页（渠道类型下拉 + 已保存渠道列表，A 方案）。
- **E39 系统设置**：时区手动设置 + NTP 时间同步 + 主备服务器配置。
- **维护校验策略升级**：每日校验改为 n/t 确定性分桶 + 月度全量独立开关（设置页周期天数 + 月度全量开关）。
- **默认仓库重建自救通道**：忘密码时清空快照 + 新密码重 init。

### 变更
- 仓库 ID 生成规则改版为 `r<YYMMDD>-<6 位 Base32 随机>`。
- 仓库删除 / 重建语义修订：改为清空目录内容、保留目录本身，并回收空密码目录。
- 仓库删除保护改用语义正确的 `RepoNotEmptyError`（E41）。
- 仓库页作业进度卡行级化 + 维护作业浮卡终态清理。
- 开发模式收口：`PASSFILE_ROOT` / `CACHE_DIR` / `RCLONE_CONFIG` + WSL `dev.sh`。

### 修复
- **🔴 重建仓库调序**：先清空数据目录再覆盖密码文件，避免清空失败（EBUSY / 权限）时旧密码已被覆盖、快照永久不可读；新增回归测试 `test_rebuild_clear_failure_preserves_old_passfile`。
- 通知失败提示去掉「已脱敏」字样，仅保留前往日志指引。
- 删除仓库勾选文案同步「保留目录本身」语义。
- 顶栏默认仓库框重叠 + 抽样比例改百分比输入 + vite 轮询。

### 文档
- 代码审查规范 v1.9：S6 危险操作纳入 `repo.delete`（R-023 仓库删除治理）。
- 架构 / PRD 同步仓库 ID 改版、E40 / A8-r2、E41 / rebuild 磁盘清理语义。
- 配置迁移导入导出说明入库 + 加密算法表述统一。

### 校验结果
- 后端 `pytest` **925 passed** / `ruff` clean。
- 前端（WSL）：`vitest` + `vue-tsc` + `pnpm build` 全绿。
