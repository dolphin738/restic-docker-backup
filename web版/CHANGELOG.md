# 更新日志（Changelog）

本项目遵循语义化版本（SemVer）。所有 notable 变更记录于此。

## 架构设计文档修订史

> 《docs/架构设计文档.md》v1.0–v1.11 的全部修订记录，2026-10-30 由该文档文首**整体移入**，内容逐字保留、未做裁剪。
> 《架构设计文档.md》自 v1.11 起**只记载当前契约**，不再内联修订历史——历史属于按需查阅的参考，不该占据每次都要读的契约前排。
> 反向指针：[架构设计文档](../docs/架构设计文档.md)（当前契约）

> 版本：v1.17（2026-11-06 / v1.16 2026-11-05 / v1.15 2026-11-04 / v1.14 2026-11-02 / v1.13 2026-11-03 / v1.12 2026-11-01 / v1.11 2026-10-29 / v1.10 2026-10-27 / v1.9 2026-10-23 / v1.8 2026-10-23 / v1.7 2026-10-20 / v1.6 2026-10-17）
> **v1.17 修订（2026-11-06，§5.0 错误码表补登 5 条）**：§5.0 表体此前 14 行 + 「其余已登记码」段 11 个 = 覆盖 25 / 30，`ERROR_STATUS`（`constants.py:130-161`）实为 **30 条**，长期漏登。本次按 §5.0 `:569` 的维护约定补齐 5 行：`DUMP_FAILED`（500，R-019 dump 阶段探活/执行/产物校验）、`RCLONE_UNAVAILABLE`（503，rclone 配置管理不可用）、`COPY_SAME_REPO`（422）、`COPY_NO_SNAPSHOT`（422）、`COPY_TARGET_UNUSABLE`（422，C4 手动 copy 三类预检拒绝，须独立成码否则前端统一显示「参数校验失败」）。**零代码改动，只补登记**；连带把《代码审查规范》§3.2 的错误码计数重算（v1.18，见下段修订史）。
> **v1.16 修订（2026-11-05，§4.3 键表新增 `ui.*` 界面偏好行）**：新增 `ui.rclone_conf_backup`（`"1"`/`"0"`，缺省 1）——rclone 启用加密前是否备份明文 conf 的**持久化偏好**，原为组件内 `ref(true)`、面板重挂载即回默认。三点边界：①偏好读失败静默回落默认开、不阻塞面板；②写入失败只提示、不回滚开关；③服务端单值不随 `X-Repo-Id` 分仓，**不参与配置导出/导入**（导出只含 `behavior.` / `notify.`）；每次拨动写 `config.update` 审计。**零后端逻辑改动**（`ui.*` 非敏感键、不在 `_BOOL_KEYS` 内故放行，`ConfigRepo.mask` 回明文值；无迁移、无新端点）。**注**：本条目记录的§4.3 `ui.*` 行与前后端改动尚在工作树未提交，随其所属改动线合入时一并落盘；本条只是提前登记版本号。
> **v1.15 修订（2026-11-04，内置 TLS 退役——仅由反向代理终结 HTTPS）**：§1.2 HTTPS 段改「仅由反向代理终结」、§3 目录树 `main.py` 注释去内置 TLS、§4.3 键表两条 TLS 路径配置键行改「已废弃（2026-11-04 删除内置 TLS）：不再被读取」（全仓唯一保留该键名处）。**「三选一」中的内置选项已删除**，反向代理两案保留。
> **补录说明**：v1.15 / v1.16 两条此前**只记在《文档管理规范》v1.66 / v1.86 的修订记录里、从未写入本文件**（本段版本行从 v1.14 直接跳到 v1.17），本次按那两处记录逐字补录，内容未做二次释义。
> **v1.14 修订（2026-11-02，密钥源归属与凭据加密缺口收口）**：把「键归属知识被手抄 6 处」收成单一出口并补读取侧降级语义——①**§4.3 R-025 注 ② 键归属规则**改为「判定的唯一出口是 `crypto.owner_of()`，配套 `keys_of(repo_id, keys)`【**双参**：crypto 不读库，红线 A1，全量键由调用方从 `list_all_raw()` 取】/`must_be_owned`/`is_sensitive`，轮换/清理/回收/force 四处必须问它」，并记 `SENSITIVE_EXACT_KEYS`/`SENSITIVE_PREFIXES` 唯一定义点在 `services/crypto.py`（config_repo 只再导出）、代价段补「default 口令文件不会被孤儿回收」第二道保险；②**§4.3 新增 ④ 读取侧降级段**（`list_all()` 按密钥源分组解密、展示路径不抛 / 执行路径 `load_credentials` 抛 `CredentialDecryptError`(500)、启动期一条汇总 warning、三态判定 `degraded`+`degraded_reason`）；③**§5.0** `CREDENTIAL_DECRYPT_FAILED` 扩为兼负「密钥源失效」、`REPO_NOT_EMPTY` 记 E59 复用（**未新增错误码**）；④**§5.1** E06 增 `degraded`/`degraded_reason` 契约、E44 补 force 覆盖清理动作、E59 补三门守卫。**关键语义（易误读）**：降级与否由**调用路径**决定，不是键的属性——本文早期草案的 `is_degradable(key)` 谓词**未实现也不需要**。实现记录见《修复方案-密钥源归属与凭据加密缺口-2026-11-02.md》v1.2（0–5 批落地 + 第 6 批导出标记）。

> **v1.13 修订（2026-11-03，R-025 凭据加密改按仓库密钥编排；本批纯文档，代码已于 0b741a4 落地）**：把「`enc:v2` 声称的跨仓库密钥隔离实际未生效」这一既有缺口在契约层收口——①**§4.3 的 R-025 注扩写为三段**：**密钥编排**（`CredentialCrypto` 是单仓库作用域，`repo_id` 参与 HKDF info；`CryptoProvider` 按 `repo_id` 惰性建实例并缓存，装配点 `main.py`，`app.state.crypto_provider` 供 `key.rotate` 使用）、**键归属规则**（`cred.<repo_id>.<field>` 三段及以上→该仓；两段 legacy `cred.<field>` 与全局键 `dump.password`/`notify.*`/`metrics.token`→`DEFAULT_REPO_ID`；`ConfigRepo._crypto_for(key)` 读/写/迁移三路径共用；仓库 id 正则不含点号故切分无歧义）、**严格语义**（无口令文件即拒写 `cred.<repo_id>.*`，422 `VALIDATION_ERROR`／内部类 `CredentialKeyUnavailableError`／`data.path` 给路径，**不降级回 default 密钥**；故「先设密码、再录凭据」为硬性顺序）；并把「密码文件成为单点」的代价**收窄为按仓库**（仅该仓凭据失效，全局键仍绑 default）；②**§5.1 E44** 密钥源句改 per-repo（覆盖只影响该仓库）；③**§5.1 rebuild** 纠正顺序契约——原文写的「写新密码文件 → 清空目录内容 → `restic init`」正是 2026-10-22 🔴 修复掉的那个错误顺序，实为「清空内容 → **清该仓旧 `enc:v2` 凭据** → 写新密码 → `restic init` → 中性化」，并补两条「为什么必须先」的因果（先清空后覆盖密码：否则清空失败时旧密码已覆盖且无回滚，盘上快照永久不可读；先清密文后 init：init 取凭据需全表解密，新密码已写而旧密文不可解会 500），影响面收窄为「只清该仓库自己的 `cred.<repo_id>.*`，其他仓库存活」；④**§11 R-025 行**补 `enc:v2` 与 `CryptoProvider`、严格语义、三条护栏测试名。**顺带更正两处既有漂移**：`enc:v1:` → `enc:v2:`（写侧形态，v1 仅只读兼容）、「密码文件单点」→ 按仓库。**零代码改动**（对应实现见提交 `0b741a4`）。
> **v1.12 修订（2026-11-01，删除依赖与快照可用性保护方案契约上位化；零代码改动）**：把《删除依赖与快照可用性保护方案》v1.4 的**契约面补登进本文**——此前该方案已实施但契约从未进架构文档，构成其唯一归档障碍。①**§5.0 错误码表**补 2 行：`RCLONE_REMOTE_IN_USE`（409，E51/E55；独立错误类 `RcloneRemoteInUseError`，不复用 `REPO_NOT_EMPTY` 以免混码无法分流）、`REPO_IN_USE_AS_MIRROR`（409，E41；`json_each` 精确匹配 `mirror_repo_ids` 防子串误判）；②**§5.1 新增 E55–E63 九端点**（路径一律以代码实际声明为准，方案稿 §7.2 的 `/release-id`、`?include_archived=1` 为设计期写法、落地已收敛）：E55 `GET /rclone/config/{r}/usage`、E56 `POST /rclone/config/{r}/repoint`（`repo.repoint` 审计，**不入 S6 清单**）、E57 `GET /archive/repos`、E58 `POST /archive/repos/{id}/restore`、E59 `POST /archive/repos/{id}/release`（`repo.id_release`，S6 第 22 项）、E60 `DELETE /archive/tombstones/{id}`、E61 `GET /rclone/archives`、E62 `POST /rclone/archives/{r}/restore`（`rclone.config_restore`，S6 第 23 项）、E63 `DELETE /rclone/archives/{r}`；并就地登记 **E51/E41 的语义扩展**（先查后删 + 409、默认归档 + 三门口径）与「force 强删永久不做」裁定；③**§4.3 config 键表**补 4 键：`retention.repo_archive_days`(30) / `repo_tombstone_days`(365) / `rclone_archive_days`(365) / `repo_passfile_days`(30)，并注**入口在归档页「保留策略」分区、不外露设置页**（对齐 `retention.audit_days`）。**效果**：该方案自此满足文档管理规范 §3「结论已被上位文档吸收」的归档门槛（正文仍留 `docs/` 作唯一方案源与取证底稿）。ror`，不复用 `REPO_NOT_EMPTY` 以免混码无法分流）、`REPO_IN_USE_AS_MIRROR`（409，E41；`json_each` 精确匹配 `mirror_repo_ids` 防子串误判）；②**§5.1 新增 E55–E63 九端点**（路径一律以代码实际声明为准，方案稿 §7.2 的 `/release-id`、`?include_archived=1` 为设计期写法、落地已收敛）：E55 `GET /rclone/config/{r}/usage`、E56 `POST /rclone/config/{r}/repoint`（`repo.repoint` 审计，**不入 S6 清单**）、E57 `GET /archive/repos`、E58 `POST /archive/repos/{id}/restore`、E59 `POST /archive/repos/{id}/release`（`repo.id_release`，S6 第 22 项）、E60 `DELETE /archive/tombstones/{id}`、E61 `GET /rclone/archives`、E62 `POST /rclone/archives/{r}/restore`（`rclone.config_restore`，S6 第 23 项）、E63 `DELETE /rclone/archives/{r}`；并就地登记 **E51/E41 的语义扩展**（先查后删 + 409、默认归档 + 三门口径）与「force 强删永久不做」裁定；③**§4.3 config 键表**补 4 键：`retention.repo_archive_days`(30) / `repo_tombstone_days`(365) / `rclone_archive_days`(365) / `repo_passfile_days`(30)，并注**入口在归档页「保留策略」分区、不外露设置页**（对齐 `retention.audit_days`）。**效果**：该方案自此满足文档管理规范 §3「结论已被上位文档吸收」的归档门槛（正文仍留 `docs/` 作唯一方案源与取证底稿）。
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

## 代码审查规范修订史

> 由《代码审查规范.md》文首**整体移入**（候选 7：权威文档正文不夹带修订史），内容逐字保留、未做裁剪。
> 反向指针：[代码审查规范.md](docs/代码审查规范.md)（当前契约）

> 版本：v1.18（2026-11-06 / v1.17 2026-11-05 / v1.16 2026-11-04 / v1.15 2026-10-28；规则正文以 §3.1 为准）
> **v1.18 修订（2026-11-06，只改计数、不增规则）**：§3.2「错误码登记（反向检查）」重算——`ERROR_STATUS` 现 **30 条**（`constants.py:130-161`），架构 §5.0 覆盖 **25 / 30**（表体 14 行 + 「其余已登记码」段 11 个），**缺 5 条**：`DUMP_FAILED` / `RCLONE_UNAVAILABLE` / `COPY_SAME_REPO` / `COPY_NO_SNAPSHOT` / `COPY_TARGET_UNUSABLE`（已随架构文档 v1.17 补齐）。v1.17 及以前本条写的「20 条 / 架构 §5.0 仅列 14 条 / 缺 `RESTIC_FAILED`、`POLICY_WRITE_CLOSED`、`READONLY_KEY`、`PASSWORD_WRONG`、`CSRF_ORIGIN`、`BOOTSTRAP_REQUIRED`」是估算且已过时：`POLICY_WRITE_CLOSED` 随 policies 遗留面删除（`ERROR_STATUS` 不再含 POLICY 码），`RESTIC_FAILED` 等 6 个已于 2026-10-11 补入 §5.0「其余已登记码」段。
> **v1.17 修订（2026-11-05）**：S6 危险操作清单在 v1.16 基础上 +3（24 → 27），登记 `rclone.conf_encrypt` / `rclone.conf_rekey` / `rclone.conf_decrypt`（conf 加密启用/换口令/停用）——三者直接决定所有 `rclone:` 远端是否可达，停用后 conf 退回明文；审计 detail 只记动作与 result，不含口令（S3）。
> **v1.16 修订（2026-11-04）**：S6 危险操作清单 23 项 → **24 项**：**`repo.repoint`（批量改指向——把引用某 rclone 远端的仓库 url 整体切到另一个远端，只换 remote 段保留 path、不动数据不动凭据；删旧远端前的受控出口，与 `rclone.config_delete` 阻断判定共用同一匹配口径；detail 只含 `new_remote` 与改动仓库数，不含 url 原文）**。动因：代码自 P0 起已埋点 `repo.repoint`，清单漏登。（v1.16 的修订实质由提交 `ea09f0a` 带入，当时把版本行直接写成了 v1.17 的口径，本条按该提交内容补录。）
> **v1.1 修订（2026-10-11）**：对齐仓库实况，修正 6 处失实声明——①「CI 已配置」改为按 §7 过渡期说明的实况表述；②过渡期命令改为 pnpm 实情并移除尚不存在的 eslint/vitest；③上游文件名与真实路径对齐；④owner 统一为 `restic-web-dev`；⑤A1/A2 补豁免机制（原红线与已合入的 `auth/` 模块、`exclusions.py` 冲突，reviewer 无法执行）；⑥修正 §1 与 AGENTS.md 的映射描述（此前漏称 S4/S5/S8 已进镜像）。
> **v1.2 修订（2026-10-13）**：补入 AI 生成代码质量三项检查（均为 🟡 Suggestion 级，见 §3.2）——①**死代码/死导出**；②**过度抽象反向刹车**；③**AI 幻觉防御**（引用 API 真实存在于依赖锁定版本，并入 §4.3 步骤 3）。§6 速查表联动。
> **v1.3 修订（2026-10-15，owner 签署生效）**：**S7 由「P1 禁止任何覆盖语义」修订为「覆盖语义仅限原位恢复路径且须四要件齐备」**（R-015 归期裁决的落地件，草案见《archive/增量设计-P2收尾-2026-10-14.md》§8）——该修订是批次 D（R-015）开工的**唯一前置闸门**，签署前原位恢复不得接任何路由；连带核对（§8.3）：S6 危险操作清单 7 项 → 9 项（`restore.inplace` 实化原「原位恢复」占位；补登记批次 A/B 已落地但漏登记的 `retention.cleanup`、`dump.cleanup`），`restore.preview` 为只读留痕、**不入**危险清单（援 `forget.preview` 先例）。
> **v1.4 修订（2026-10-17，owner 签署生效）**：S7 要件②③ 随 **A7「原位恢复 Duplicati 化」**放宽——② **三重确认门**（keyword = 快照短 id）→ **单次确认门**（ConfirmGate 无关键词输入，对齐 Duplicati 的无关键词确认）；③ `--overwrite` **固定 `always`** → **`never` / `always` 布尔勾选**（默认不勾 = `never` 跳过已存在文件，勾选 = `always` 覆盖；对齐 Duplicati `--overwrite` 布尔默认 false）。要件①双重锁与④审计**不变**；时间戳子目录路径零覆盖语义**不变**。**语义差异如实标注**：Duplicati `--overwrite=false` 实为「冲突时改名另存」（`RestoreHandler.cs:2112`，`name.yyyy-MM-dd` 后缀），restic 无等价能力，取最接近的 `never`（跳过）。源码证据（D:\agent\duplicati-master）：`Options.cs:529` overwrite 布尔默认 false；`RestoreHandler.cs:1958/2112` rename = !options.Overwrite；`FileRestoreDestinationProvider.cs:138-142` outside-target 护栏默认 false（原位置 = 空 target，不走该前缀检查）。
> **v1.5 修订（2026-10-17，owner 签署生效）**：S6 危险操作清单 11 项 → **13 项**（批次 D C1/C4 新增）：**`hook.run`（任务级前后钩子，执行任意用户命令）**、**`copy.run`（跨仓库复制快照）**、**`key.rotate`（轮换仓库密码）**。同时新增 **S9 用户命令执行边界**（动因：C1 引入用户命令执行面——不经 shell、shlex 切分为 argv、强制超时、审计 detail 绝不含命令行原文，见 §3.1 S9 行）。
> **v1.6 修订（2026-10-18，C1 暂缓下架）**：C1 任务级前后钩子整体下架存档（`docs/暂缓存档-任务级前后钩子C1-2026-10-18.md`），S6 清单 13 项 → **12 项**（`hook.run` 随之摘除，恢复 C1 时补回）；**S9 条款保留**（防御性规范：未来任何用户自带命令执行面必须满足四要件）。
> **v1.7 修订（2026-10-18，批次 E）**：S6 危险操作清单 12 项 → **14 项**：**`config.export`（配置导出——凭据口令加密后文件出系统边界，detail 只含计数不回显内容）**、**`config.import`（配置导入——改写仓库/任务/通知配置，detail 只含计数与 overwrite）**。
> **v1.8 修订（2026-10-18，owner 确认）**：**§4.3 审查执行步骤新增步骤 3「需求符合度核查」（文档 → 代码正向覆盖）**——PR 触及的功能域须对照架构 §5.1 端点表 / §11 追溯矩阵 / PRD 需求编号逐条确认文档声称的行为在代码中真实存在；**文档超前**（登记已交付但实现缺席）与**实现偏离**（代码与文档语义不符）审查人均不自行修正，一律提交 owner 裁决（改文档降级 / 改代码 / 维持现状+差异登记），裁决前不合并。原步骤 3–5 顺延为 4–6（v1.2 修订行所称「§4.3 步骤 3」按当时编号理解，现为其步骤 4）。§6 速查表加「全局」块。
> **v1.9 修订（2026-10-21，R-023 仓库删除治理）**：S6 危险操作清单 14 项 → **15 项**：**`repo.delete`（删除仓库——级联清除终态作业历史行 + 日志文件 + `cred.{repo_id}.*` 凭据键，不可逆；detail 只含仓库名与计数，不回显凭据（S3））**。动因：E41 v2 把「终态作业历史随仓库级联删除」纳入删除语义后，该端点从「改一行配置」升级为「批量不可逆删除」，须与 `retention.cleanup`（同类删除动作）同等留痕。仍不入清单：`repo.create` / `repo.update`（纯配置写入，无删除副作用）。
> **v1.10 修订（2026-10-23，P7 D2 Abort 二段）**：S6 危险操作清单 15 项 → **16 项**：**`job.abort`（Abort 强杀——跳过 SIGTERM 宽限直接 SIGKILL，与普通 `job.cancel` 分动作留痕；detail 记 `force` 与 `grace_seconds`，不含命令行原文）**。动因：P7 主页复刻按 Duplicati 补 Stop/Abort 二段（`POST /api/jobs/{id}/cancel?force=true`，§4.5.4 Abort 行「force 参数 + 审计埋点」），强杀会跳过 restic 的优雅收尾，须与 cancel 可区分。**
> **v1.11 修订（2026-10-24，源数据过滤与树双模式·批次①）**：S1 白名单语义由「固定两颗前缀 `/sources`/`/restore`」改为「**备份源白名单由 `browse_root` 单一派生**（docker→`/sources`、host→`/`）+ host 模式 **deny 前缀**（伪目录 + 仓库存储 + 应用数据目录，`validate` 对源根命中时强制拒绝）」；新增 `DEPLOY_MODE=docker|host|auto` 自动检测与 `GET /api/system/runtime` 下发。`/host` **挂载前缀**仍零容忍；**host 作为部署形态回归**（与 `/host` 前缀无关）。同步 AGENTS.md S1 索引（编号锚点不变）。
> **v1.12 修订（2026-10-26，R-020 审计保留策略落地）**：S6 的 `audit_logs` 不变量由「只允许 INSERT 与 SELECT」修订为「INSERT / SELECT + **唯一受控 DELETE 出口** `AuditRepo.purge_older_than`」——按 `retention.audit_days`（默认 365，`<=0` 永不清理）分批删除超期审计行，**先 gzip 按年归档（`audit-<YYYY>.jsonl.gz`）成功后才允许 purge**（归档失败即跳过删除，fail-safe），DELETE 分批走 `Database.write` 串行（A5），清理动作自身记 `audit.cleanup`、归档动作记 `audit.archive`（动作名入 `constants.AUDIT_*`）。任何第二处对 `audit_logs` 的 UPDATE/DELETE 仍 = Blocker；危险操作 16 项清单**不扩项**（`audit.cleanup` 的删除语义由受控出口条款覆盖并强制埋点）。裁决2 敏感动作豁免**不做**（无 `PROTECTED_AUDIT_ACTIONS`）。
> **v1.15 修订（2026-10-28，删除保护方案 P2 落地）**：S6 危险操作清单 **20 项 → 23 项**——**`repo.purge`（销毁仓库数据——清空仓库目录内容 + 删密码文件 + 删行，落 `data_destroyed=1` 的墓碑，不可恢复）**、**`repo.id_release`（释放归档仓库 ID——删 repos 行使该 id 可被复用，旧快照自此与应用失去关联；不删任何数据）**、**`rclone.config_restore`（把归档的 conf 段写回——直接决定 restic `rclone:` 后端可达性，与 `config_edit` 同性质；detail 只含 remote/type/键数）**。**不同步入清单**（埋点但不危险，理由随动作注明）：`repo.archive`（置 `deleted_at`，数据/凭据/密码文件全留，可一键恢复）、`repo.restore`（恢复性动作）、`repo.purge_remote_ack`（仅记录「用户已知悉远程数据需自行清理」）。同时 `DELETE /api/repos/{id}` 语义变更：**默认归档**，`hard=1` 彻底删，`purge_disk=1` 销毁数据（与归档互斥）。头部版本号 v1.14 → v1.15。
> **v1.14 修订（2026-10-26，rclone 配置管理 B0）**：S6 危险操作清单扩项 **+3**——**`rclone.config_append`（新增 rclone conf 段）/ `rclone.config_edit`（编辑段）/ `rclone.config_delete`（删除段，含 OAuth 网盘段——删后需在宿主机重新授权才能再用）**。动因：rclone conf 是容器 rw 挂载的真实文件，段的增删改直接决定仓库后端（restic `rclone:` 后端）可达性；删除动作对 OAuth 段不可逆（token 一并清除），须与 `repo.delete` 同等留痕。detail 一律只含 remote 名与 type（编辑加「是否改凭据」布尔），**不含任何凭据值**（S3 延续）。**同版修正历史计数漂移**：原「16 项」与实际枚举（17 个动作名）不符——`config.export`/`config.import`、`copy.run`/`key.rotate` 历史上曾并组计数；本次按动作名**重计为 20 项**（17 + 3），并声明「此后以枚举为准，不再沿用并组计数」。头部版本号 v1.13 → v1.14。
> **v1.13 修订（2026-10-26，重复实现条款补齐 + 修订史编号重排）**：§3.2 新增「**重复实现（平行轮子）**」条——A1–A6 域内出现第二份活实现 = 🔴、域外 = 🟡，新增工具函数/组件前必须 grep 既有实现，且只有第二次使用时才允许抽取（与「过度抽象」互为刹车）。动因：规范此前只有「死代码」条（🟡）与 5 条架构单一实现点，**缺少通用的「复用既有实现」条款**——后端时间/分页/skipped 统计等工具级双写无条款可依。§2 作者自查与 §6 速查表联动补项；§7 补待接入的机器兜底（vulture / knip / jscpd，**未落地不作为当前门禁**）。本条为 🟡 级，AGENTS.md 仅索引 §3.1 红线，故不同步。**同版重排修订史**：原存在两个 v1.11（2026-10-24 源数据过滤 / 2026-10-26 R-020）且顺序倒置，已按时间序重编为 v1.11（10-24）→ v1.12（10-26 R-020）→ v1.13（本条），头部版本号同步 v1.6 → v1.13。

## Web版改造方案修订史

> 由《Web版改造方案.md》文首**整体移入**（候选 7：权威文档正文不夹带修订史），内容逐字保留、未做裁剪。
> 反向指针：[Web版改造方案.md](docs/Web版改造方案.md)（当前契约）

> 版本：v2.3.4（2026-10-10）  
> v1.0 → v2.0：备份源模型重构（全盘只读挂载，动态增删源无需改 compose）、删除全部兼容层、  
> dump 目录收进容器自有卷、恢复路径自动映射。  
> v2.0 → v2.1：**恢复交互对齐 Duplicati**——`/host` 挂载模式改为 compose 级 `ro|rw` 配置（默认 rw），  
> 原位恢复不再要求重启容器；恢复向导提供「原位置 / 其他位置」二选一 + 覆盖策略选择。  
> v2.1 → v2.2：**后端凭据（S3/B2 等）改为 Web 界面可配**（存 SQLite、API 脱敏、子进程 spawn 时注入环境），  
> compose 环境变量降级为可选覆盖；仓库密码维持密码文件方案不变（性质不同，见 §6）。
> v2.2 → v2.3：**鉴权设计补全并定为 P1 第 0 项**（§6.1、§8）；新增**运行用户 × 挂载模式能力四象限**  
> 与**默认排除集**（§3）；恢复向导分期——P1 仅"恢复到时间戳子目录"，覆盖比对确认流移 P2（§3.4）；  
> 策略模型字段补全、**调度统一为 cron**、P1 即按多策略建表（§4.1、§5）；任务状态机 + 取消 API、  
> SSE 协议细节、SQLite 工程约定、API 端点补全（标注阶段）（§5）；镜像依赖安装层 / HEALTHCHECK、  
> bind-mount 属主要求、pg_dump 版本兼容声明、仓库类型 P1 收敛为 s3/b2/本地（§4）；§7 限制补充；  
> §8 重写（人周估算 + DoD + 工程化约定）。
> v2.3 → v2.3.1：新增**仓库类型接入归期表**（§4.2）——明确 restic 全部 9 类原生后端的接入归期
> （REST Server 归 P2，Swift/Azure/GCS 明确不接入），§4 仓库类型 bullet 与 §4.1 边界清单同步引用。
> v2.3.1 → v2.3.2：**内置排除集改为模式感知**（§3.1/§3.2）——语义核心集（8 条目）按模式派生前缀
> （host=`/host`、sources=`/sources`）+ 各模式附加条目；sources 模式新增四层防线
> （one-file-system 模式缺省开启、首备 dry-run 命名警告、部署文档警告、核心集派生兜底）。
> v2.3.2 → v2.3.3：**pg_dump 大版本策略定为"始终最新稳定 GA"**（§4、§8.1）——新 client 可 dump
> 全部旧版本 server（官方支持至 9.2），无需任何版本选择；Dockerfile 锁定 Alpine 小版本；
> dump 前探测兜底"新 PG GA 与 Alpine 稳定源收录之间的窗口期"（提示 edge 源自构建或等待镜像更新）。
> v2.3.3 → v2.3.4：PRD 待确认问题裁决落盘——**策略模型增限速字段**（`--limit-upload/--limit-download`，
> P2 实现，Q5）；**§4.2 归期调整**：rclone P2 优先、sftp P3（Q6）；中英双语/i18n、日志保留可配置、
> 浏览器支持清单等其余裁决记录于 PRD §8（Q3/Q7/Q8）。另：通知模块新增 MeoW 渠道与渠道级脱敏开关
> （承接 PRD US-G1，随 P2 交付）。

## Web版PRD修订史

> 由《Web版PRD.md》文首**整体移入**（候选 7：权威文档正文不夹带修订史），内容逐字保留、未做裁剪。
> 反向指针：[Web版PRD.md](docs/Web版PRD.md)（当前契约）

> 版本：v1.17（基于《Web版改造方案》v2.3.4；v1.17 = 2026-10-29 R-020 审计保留策略方案结论吸收（方案原件入 `docs/archive/`）；v1.16 = 2026-10-28 恢复流程并入快照页；v1.15 = 2026-10-29 rclone 表单交互与落盘形态收敛；v1.14 = 2026-10-27 rclone 远端配置管理（Crypt/WebDAV 生成 + 全段查询/编辑/删除）；v1.13 = 2026-10-25 快照浏览页收尾 + US-026-08 分组视图撤回；v1.12 = 2026-10-23 仓库密钥身份中性化 + 只读可见性；v1.11 = 2026-10-20 仓库页「目标位置」模块复刻与 S3 方言校准）
> **v1.15 修订（2026-10-29，rclone 表单交互与落盘形态收敛）**：①**设置页 rclone 面板**：删除「目标存储形态」开关与「我填入的已是混淆后凭证」副复选框，改为单一「密码形态」（明文 / 已混淆串）——真机实测明文密码写进 conf 对 crypt/webdav 一律不可用，落盘形态因此恒为混淆；历史明文段可「修正为混淆」（不填密码即可）；**不提供导出原密码**（S3「凭据零回显」不开口子），只给「如何找回密码」弹窗——内容为宿主机命令示例（`rclone config dump` + `rclone reveal`），可一键复制。②**仓库页 rclone 字段**：远端名 = 输入框 + 左侧「选择远端 ▾」下拉（选项 = 探测到的段名，选中回填，仍可手输）；删除 rclone 预设快捷卡；远端路径必填；填好后显示实际落盘路径（crypt 段按其底层 `remote` 拼接）；同远端占用/同路径冲突提示；编辑卡头 = 标题 + 仓库 ID（左）+ 管理远端（右）。详见《rclone配置生成工具设计方案》v1.20。
> **v1.14 修订（2026-10-27，rclone 远端配置管理）**：设置页新增「rclone 远端」分区（E48–E51）——
> 可在界面**查询 / 生成 / 编辑 / 删除** rclone.conf 段。Q17「UI 不生成、不编辑、不展示凭据」对
> **非 OAuth 类型**（Crypt / WebDAV）做**有边界放宽**：凭据由用户自填、服务端混淆后落盘，
> 响应与审计一律零凭据；OAuth 网盘段（drive/onedrive）**不可编辑**（须宿主机 `rclone authorize`）、
> **可删除**（加重确认提示）；s3/sftp 等手工配置段可删除、编辑待注册表扩展。Crypt 增
> `filename_encoding`（默认 base64）+ 前端 32 位随机密码生成。写入走官方 rclone CLI，
> conf 挂载形态由单文件改为**目录**（H-2/部署指南 §4.2 迁移指引）。详见
> `docs/rclone配置生成工具设计方案.md` 与《架构设计文档.md》v1.10。
> **修订记录**：
> - v1.17（2026-10-29）：**R-020 审计保留策略：方案结论吸收**（配合《任务历史与审计日志保留策略方案.md》归档，该件入 `docs/archive/`，本条为其独有内容的蒸馏）——①**方案选型落 B**：「gzip 归档 + 365 天滚动清理」。否决 A（永久保留：库无界增长）、C（按条数环形截断：突发 `login.failed` 会冲掉正常审计，**「最近 N 条」的边界无法向审计方解释**、可辩护性差）、D（审计外置独立库：破坏「单一 SQLite 文件」部署假设，收益不抵成本）。B 的核心代价是归档文件自身也要有边界 → 归档按**年份切分**并另设 `retention.audit_archive_days`。②**合规口径**：有明确保留期 + 清理自身留痕（`audit.cleanup`）比「永久但无管理」更易通过审计问询；**365 天是下限**（客户要求 ≥1/≥3 年时由管理员调大），界面须写清「改小会缩短取证窗口」；归档文件落 `/data` 卷，**`/data` 若被排除出宿主机备份则归档同样会丢 → 必须外送冷存**（应用不自动同步，见《部署指南.md》）。③**五条裁决与偏差**（原文 §5）：审计默认 **365 天**且界面可调；**敏感动作豁免不做**（安全事件不额外延长窗口，需更长窗口由运维调大 `audit_days` 或走冷存）；归档由明文 JSONL 改为 **gzip 压缩**；审计「立即清理」**不进设置页**（配置项迁审计页第二分区）；任务历史「立即清理」走独立 `cleanup_jobs_only()` 路径，**不触发审计归档/清理**（防误删合规证据）。④**验收口径 5 条**：`audit_days<=0` 零删除（fail-safe）／归档失败零删除／清理幂等／清理后时间窗查询与归档行数一致／敏感动作豁免项不适用（裁决2 已砍）。契约见《架构设计文档.md》v1.11（四键 + E52–E54）与《代码审查规范.md》§3.1 S6（`AuditRepo.purge_older_than` 为唯一受控 DELETE 出口）。
> - v1.16（2026-10-28）：**恢复流程并入快照浏览页（owner 决策：删「恢复向导」独立页面，三步流程全部复用）**——① **删除** `/restore` 恢复入口页与 `/restore/:id/{select-files,options,progress}` 三个向导路由（`pages/restore/` 整目录移除），三步流程组件（选文件 / 恢复选项 / 执行与结果）**原样复用**，容器改为快照页右侧宽抽屉（桌面 1080px / 移动端全屏）；② **入口归一到页面 8 两处**：列表行内新增「恢复」按钮、文件树抽屉「恢复此快照」，二者打开同一面板并**预选该行快照**；③ 原入口页两张备选卡（从配置恢复 / 从其他仓库恢复）收敛为快照页卡片头部两个轻量入口；④ 深链改 `?restore=<id>`（快照页读 query 自动开面板、关闭即清 query）；旧 `/restore*` 地址**不保留兼容**（owner 2026-10-28 核实：全仓无可点击的旧地址引用，文档里只有代码路径与 `/api/restore/*`；唯一使用方 e2e 探针已改新入口）；⑤ 关闭面板改走抽屉级确认，脏判定收紧为「已选文件 / 已进步骤 2 / 作业未终态」（原「有快照即脏」会让每次关闭都弹确认）；⑥ 导航「数据」组撤「恢复向导」项（主菜单 8 → 7 项），仪表盘三处恢复入口改跳快照页。
> - v1.15（2026-10-26）：**两处 owner 实拍微调**——① **恢复向导步骤 1「选文件」**：文件树勾选态此前**无任何样式**（`vtree__row--checked` / `--selected` 两个类名只挂不写规则，点下去零反馈，用户误判「不支持多选」）→ 补齐高亮规则（令牌化、亮暗自适应、多选行加左侧竖线）；下方由「已选 N 个」升级为**已选清单**（逐条列出已选路径、每条可单独取消、一键清空、容器内滚动），清单与树/搜索命中项共用同一 `selectedFiles` 集合，多选语义不变。② **任务向导第④步「调度」**：三段 card tab（不调度 / 简单模式 / 高级模式）改为**下拉菜单**（步骤 4 内容长，tab 条持续下推保留策略区）。③ **恢复树视口改为内容驱动**：原固定 520px（P9 满高）在快照根条目少时留下大片死空白、把已选清单与页脚顶出首屏；改为「行数 × 行高，256–672px 夹住，超出内部滚动」。详见《UI全量改造方案.md》v1.39（P21）。
> - v1.14（2026-10-26）：**任务历史与审计日志保留策略落地（R-020 扩展，owner 五项裁决）**——① 审计保留策略：新增 `retention.audit_days`（默认 365，`<=0` = 永不清理），每日维护先**gzip 压缩归档**为 `audit-<YYYY>.jsonl.gz`（按年切分、追加不覆盖）再受控 purge（归档失败即跳过删除，fail-safe；DELETE 唯一出口 `AuditRepo.purge_older_than`，S6 不破）；② 归档配置：`retention.audit_archive_days`（默认 365，归档文件自身保留期）+ `retention.audit_archive_dir`（可配置，回落 `/data/audit/archive`）+ `retention.audit_archive_cold_target`（**仅占位提示**，应用不自动同步，外送交给既有 restic/rclone/挂卷，见部署指南）；③ 敏感动作豁免：**裁决2 跳过不做**；④ **审计页改为两分区 Tab**（2026-10-26 迁移：保留策略从设置页并入本页）——第一分区「审计日志」= 原查询/导出；第二分区「保留策略」= audit/archive 天数 + 归档目录 + 冷存占位 + 归档文件列表只读展示，**无审计清理按钮**（裁决4）；⑤ 任务历史页新增「立即清理」（裁决5：`POST /api/jobs/cleanup` 仅清 jobs 终态行与孤儿/过期日志，**不触碰审计**，ConfirmGate + 清理报告）；⑥ 审计页新增「导出当前筛选」（流式 NDJSON）。详见已归档的《任务历史与审计日志保留策略方案.md》（`docs/archive/`）§5/§6（其设计取舍与裁决结论已由 v1.17 条吸收进本 PRD）。
> - v1.13（2026-10-25）：**快照浏览页收尾 + 分组视图撤回（2026-10-25 真机走查后 owner 指令）**——① **US-026-08「默认分组」撤回**：分组视图（未筛选时按任务渲染分组卡）先落地、后整体删除，需求池该条目由「✅ 已交付」回落为「⏸️ 部分交付（tag 筛选已交付 / 分组视图已撤回）」，§5 页面 8 表述同步（撤回理由：排版不佳 + 分组计数与叠加 `hostFilter` 后的表格总数不同源）。② **快照页缺陷修复与口径补齐**（前端，后端契约零改动）：切筛选回第 1 页（原沿用旧 offset → 空列表误导）；tag 下拉改累积归并（原从已筛选结果归并 → 选项自噬、切不回去）；A3 打/去标签**回显新旧 id 映射**（US-E2 AC5，改标签生成新快照对象，旧 id 立即失效）+ **目标范围前置拦截**（ID 与筛选都空时不发请求，说明「会作用于仓库内全部快照，已阻止」）；E18 输出上限截断明示（原静默）；`size` 列改走 `utils/format` 单一口径（不裸显字节数）；文件树行末补展示 E18 已返回的 size/mtime（原整段丢弃）；错误态取 `AppError.message`。③ **forget 提交后跳任务页 `?job=<id>`**（复用 A6 既有模式）：forget 是异步作业，留在快照页列表不会自变，用户据此误判「清理没生效」。④ **生产缺陷修复**：快照页 `NInput` 从未导入 → 5 个输入框（forget 时长档/标签档 + 标签卡 3 个）在浏览器里 unresolved 整片不渲染；根因是 `naive-imports` 护栏按非贪婪 `<template…</template>` 取块、遇嵌套 `<template #header-extra>` 在首个 `</template>` 截断 → 护栏已改为全文扫 `<n-*` 且只扫 `.vue`，复扫全仓零漏导入。⑤ **A3 语义澄清**：改标签后快照 id 变化是 restic 固有行为（`restic tag` 写新快照对象），非缺陷；「不填 ID 无法打标签」是**有意的高危拦截**（restic 无 id 即改全部匹配快照），非功能缺失。⑥ forget `group_by` **不给前端可编辑入口**（后端只防注入不校验维度合法性 → 非法值迟到 500），改为只读公示。护栏新增 `pages/snapshots/__tests__/snapshots-page.spec.ts`（9 例）。详见《UI全量改造方案.md》v1.30。
> - v1.12（2026-10-23）：**仓库密钥身份对外暴露面收口并入**——US-B1 新增 **AC8**：① init 成功后（含首启初始化、远端建库、`AUTO_INIT` 自举、仓库页「重建」四条路径）`keys/<id>` 里的明文身份自动中性化为 `svc-backup@repo`，**仓库口令不变、用户无感**，失败只记日志不中断初始化；② 仓库状态卡新增「密钥身份」只读小节（`GET /api/repos/{repo_id}/keys`），显示每个 key 的短 id/身份/创建时间/是否当前/是否中性 + 整体结论（已中性化 / 暴露真实身份 / 混合 / 无法读取），让管理员无需进容器跑 CLI 就能确认「云端看得到的是中性值」；③ 该小节**只读**（不含加/删 key），也**不显示** master key 密文与 KDF 参数。威胁模型、加固分层与「哪些暴露面本产品防不住」见《云端仓库安全基线.md》，实现契约见《架构设计文档.md》v1.9 与 E47。
> - v1.11（2026-10-20）：**仓库页「目标位置」模块复刻并入**（对齐 Duplicati `destination.component`，纯前端形态 + S3 事实校准，API 契约不变）——US-B1 新增 **AC6**：列表态 ↔ 编辑态整页替换、字段组按类型动态渲染、地址由字段组译（解析失败才回退原文输入）、常用值下拉聚焦即展开且仍可手填；新增 **AC7**：S3 地址按 restic 原生方言 `s3:<服务器地址>/<桶名>[/<文件夹>]` 组译（服务器地址与桶名之间为**斜杠**），**退役** URL 区域段与死凭据 `AWS_ENDPOINT_URL`（restic 0.19.1 不读、minio-go v7.1.0 零引用），区域语义归 `aws_default_region`；八类型逐一比对结论 = 仅 s3 一种原拼法错误。同期落地：仓库 ID 回显、管理即选中当前类型、初始化按钮就近且仅未初始化显示、审计日志动作名与详情中文化、新建态「创建并测试」单按钮。详见《UI全量改造方案.md》§4.4 与《架构设计文档.md》v1.7。
> - v1.7（2026-10-17）：**A1 交付并入**——仓库类型扩展为 restic 0.19.1 全部七后端（sftp/azure/gcs/rest/rclone + 既有 s3/b2/本地）：URL 严格校验（Q20）、rclone 只读托管（conf 宿主机挂载 `:rw`，凭据零落库）、E45 `GET /api/rclone/remotes`、镜像装 rclone/openssh-client、凭据 env 表扩展（AZURE_/GOOGLE_/RESTIC_REST_）；US-B1 AC1/AC5 与 R-022/Q14 行更新为已交付。前端类型选择卡 preset 快捷卡随批次 A5 落地。详见《架构设计文档.md》v1.6-A1 与已归档的《Duplicati功能对齐分析与改造计划-2026-10-17.md》（`docs/archive/`，仅供追溯）批次 A1。
> - v1.8（2026-10-17）：**A2 交付并入**——forget 保留档位补齐（Duplicati keep-time / retention-policy 分段的 restic 等价物）：任务保留策略新增时间线档 `keep_within`/`keep_within_{hourly,daily,weekly,monthly,yearly}`（restic duration，如 `1y5m7d2h`）与标签档 `keep_tags`（`--keep-tag`，OR 语义），与既有计数档并列；E21 预览/E12 执行/自动 forget 三处的「至少一个保留规则」兜底门同步扩为三门；duration 形态校验前移（保存即 422）。US-026-01④/US-026-07 保留档位表述同步。详见《架构设计文档.md》v1.6-A2 与已归档的《Duplicati功能对齐分析与改造计划-2026-10-17.md》（`docs/archive/`，仅供追溯）批次 A2。
> - v1.9（2026-10-17）：**A3 交付并入**——**快照版本标签**（Duplicati 版本标签的 restic 等价物）：`VERBS` 增 `tag` 动词；新端点 **E46 `POST /api/snapshots/tag`** 提供 `add[]`/`remove[]` 维度（与 forget/snapshots 的 `--tag` **筛选**维度 `tags` 语义隔离，不复用），目标快照可指定 `snapshot_ids[]` 或按当前 `tag_filter`/`host` 作用全部匹配快照；restic `cmd_tag.go` 源码核验的两道高危约束前移为服务端 422——(1) `add`/`remove` 皆空 → 422（restic "nothing to do!"）；(2) 无 `snapshot_ids` 且无 `tag_filter`/`host` → 422（restic 无 id 即改**全部**匹配快照）。`tag` 动词取独占锁（与备份并发 exit 11 锁冲突由 `failure_reason` 明示「仓库锁残留」）；改标签生成新快照对象（原→新 id）经 `changed[]` 映射回传。审计动作 `tag.update`；快照页新增「快照标签」卡片（打/去标签输入 + 可选目标快照 ID + 应用结果回显 + 成功后自动刷新列表）。新增 US-E2。详见《架构设计文档.md》v1.6-A3 与已归档的《Duplicati功能对齐分析与改造计划-2026-10-17.md》（`docs/archive/`，仅供追溯）批次 A3。
> - v1.11（2026-10-22）：**仓库 ID 生成规则改版 + 删除仓库磁盘清理语义修订**（owner 裁决）——① 服务端自动生成 id 由 `repo-<小写 ULID>`（31 字符）改为 **`r<YYMMDD>-<6 位小写 Base32 随机>`**（14 字符，如 `r261022-k3f9x2`；日期段 UTC 口径、字典序即时间序；存量 id 照旧、零迁移）；② 删除仓库勾选「同时删除磁盘数据」时，由「rmtree 整目录」改为「**清空目录内容、保留目录本身**」（同址新建免手动建文件夹），并回收 `<passfile_root>/<repo_id>/` 空目录（治孤儿空文件夹堆积；rmdir 仅空目录成功，用户手动放入的文件不误删）；rebuild 端点同步。详见《架构设计文档.md》v1.6-A8-r2 与 E41/rebuild 段。
> - v1.10（2026-10-17）：**A8 交付并入**——**仓库 ID 自动生成**（owner 2026-10-17 07:37 立项）：新建仓库 `POST /api/repos` 的 `id` 由必填改为**可选**，留空时服务端生成 **`repo-<小写 ULID>`**（26 位、全局唯一、按时间有序，天然满足 `REPO_ID_PATTERN` 与 64 长度上限；落库前 `exists` 查重 + 碰撞重试）；显式指定仍允许（脚本/重命名场景，撞名 422）。契约不变：`repo_id` 语义、密码文件 `passfile_path` 拼装、`X-Repo-Id` 头全部照旧。配套新增 `GET /api/repos/_new_id` 返回候选标识，前端新建仓库弹窗预填自动生成值 + 「换一个」按钮（可改可不改，对齐 Duplicati 新建备份自动起名体验）。无新增用户故事（属工程便利性，归入 R-023 新建仓库体验改进）。详见《架构设计文档.md》v1.6-A8 与已归档的《Duplicati功能对齐分析与改造计划-2026-10-17.md》（`docs/archive/`，仅供追溯）批次 A8。
> - v1.6（2026-10-16）：P3 批次结论并入——R-023 多仓库/多主机视图**已交付**（P3-C：仓库管理页 + 顶栏切换器 + 快照 `?host=` 筛选；后端 `repos` 表承载 url/host_tag + `X-Repo-Id` 头路由 + 凭据 `cred.{repo_id}.*` 命名空间，config `repo.*` 键退休）；R-024（P3-B）/ R-025（P3-A）同日交付（见 §4 需求池）。技术细节以《架构设计文档.md》§4.7/§5.1/§13 为准。
> - v1.5（2026-10-16）：Q14 补记——R-022 归期 owner 再确认**维持暂缓**；立项前置讨论结论（sources 模式不支持原位恢复 / rclone 按需子进程生命周期与 token 回写隐患 / Q18 迁移成本评估）蒸馏留档于 `docs/archive/讨论记录-R-022立项前置-2026-10-16.md`（2026-10-18 归档，见文档管理规范 §6 登记表），再立项时吸收进增量 PRD。
> - v1.3（2026-10-15）：P2 收尾批次结论并入——《增量PRD-P2收尾-2026-10-14.md》（已归档 `docs/archive/`）的用户故事 US-P2-01–15 与验收标准、Q1–Q6 及 G3 DoD 口径裁决吸收为正文（§3 旅程 H / §4 需求池状态 / §5 页面 7·9·11·12 / §8 Q9–Q14 / §9 阶段 P2 DoD）；红线 S7 修订经 owner 2026-10-15 签署（四要件 + 执行层落点纪律），原位恢复正式开放；**R-022 暂缓退出 P2 收尾批次**（owner 2026-10-14「先憋实施，需要讨论」，再立项时重开其 Q17/Q18/Q20）。实现与 QA 证据链见《archive/增量设计-P2收尾-2026-10-14.md》§13/§14 落账与《架构设计文档.md》§14b。
> - v1.4（2026-10-15）：轻量多备份任务批次结论并入——《增量PRD-多备份任务-2026-10-13.md》与《增量设计-多备份任务-2026-10-13.md》（均已归档 `docs/archive/`，合并前经代码图谱逐项核实）的用户故事 US-026-01–10 与验收标准吸收为正文（§3 旅程 I）；**Q1 裁决链完整记载**：2026-10-13「policies 退 legacy 保留只读」→ **2026-10-15 owner 改裁「选删」**——policies 代码面（路由 E08–E11 / PolicyRepo / PolicyService / E12 policy_id 入口 / 模型 / 审计常量 / 错误码）全部移除，共享字段校验原样并入 TaskService，DDL 表与 `jobs.policy_id` 列留待后续迁移（§4 需求池 / §8 Q15）；九问与架构师 N1–N8 裁决落为 §8 Q15–Q24；页面 5/6 退役与删除状态定稿（§5）。
> - v1.2（2026-10-13）：选项②「轻量多备份任务」立项——新增 R-026；R-014 语义升级；P2 构成与 DoD 同步调整（见 §4/§9）。演进位决策（含 R-023 多仓库预留）见《架构设计文档.md》§13；流程对齐背景见《对齐Duplicati的改造方案.md》。

## UI全量改造方案修订史

> 由《UI全量改造方案.md》文首**整体移入**（候选 7：权威文档正文不夹带修订史），内容逐字保留、未做裁剪。
> 反向指针：[UI全量改造方案.md](docs/UI全量改造方案.md)（当前契约）

> 版本：v1.40（2026-10-29 四十修订：**地址栏与树根分离**——owner 实拍「选中目录后快捷路径栏没有跟着变成选中的路径」。原口径地址栏 = 树根（浏览层），点目录名只改选中 → 勾选型多选场景（备份源 `SourcesPicker`、仓库/rclone 本地目录 `DirPickerInline`）下「选了看不出选了谁」。新口径：地址栏 = 关注路径（选中项/导航目标），树根 = 浏览路径；点目录名 → 地址栏跟随、**树不下钻**；地址栏跳转 → 树跟随且地址栏回浏览根。`RestoreTargetPicker` 单选下钻语义不变。详见 §4.9.9 末尾。）（2026-10-28 三十九修订：**恢复流程并入快照浏览页**——原 `/restore` 入口页与 `/restore/:id/*` 三条向导路由删除（owner 裁决：只删页面、三步流程全部复用），`pages/restore/` 目录迁至 `pages/snapshots/restore/`（`flow/index.vue` → `panel.vue`），容器改快照页右侧宽抽屉；入口 = 列表行内「恢复」/ 文件树「恢复此快照」/ 深链 `?restore=<id>`；导航「数据」组撤「恢复向导」（8→7 项）。§4.8.5/§4.8.6 的历史路径记录保留但已失效，以本节为准。）（2026-10-26 三十八修订：**P20 缺陷回归——地址栏三角恒不出现**；根因是挂载点原靠树根响应顺带填，而后端 E33 只在 `container == "/"` 的请求回 `mounts`，本页树起点是已填路径的父目录 → 一次也拿不到 → 三角 `v-if` 恒假，须手动点回 `/` 才出现。改为 `ensureMounts()` 与树位置**解耦**独立取（复用 `RestoreTargetPicker` 同款写法），三处接线 + 三角补 `Esc` 收起。详见 §4.9.10 末尾。本文件全部历史修订（v1.0–v1.38）合并记载于文末「修订记录」节，版本头不再重复内联。
> **v1.1 修订说明**：v1.0 立项后，后端已按《Duplicati功能对齐分析与改造计划-2026-10-17》（2026-10-29 归档至 `docs/archive/`，仅供追溯）完成能力面对齐（A0 模式收敛 / A4 目的地卡 / A5 rclone / A6 调度双模式 / A7 恢复 Duplicati 化 / B1-B3 自愈 / C2 通知 / C4 多目的地+搬家 / E 配置迁移）；前端视觉层亦已由 P0–P4 批次落地（发布 1.0.1：靛蓝令牌 / 232px 侧栏 / 4 组导航 / StatusTag 全站收口 / KPI 状态带 / 向导草稿）。本修订以 **backend/app 源码实读（13 组路由）+ ngclient 源码 8 组组件细读 + frontend/src 消费现状核对** 为证据，重排第二轮范围：**视觉层维持已落地基线不回退，第二轮聚焦功能面呈现差距（G1–G5）与模版交互吸收（E1–E6）**。

## 删除依赖与快照可用性保护方案修订史

> 由《删除依赖与快照可用性保护方案.md》文首**整体移入**（候选 7：权威文档正文不夹带修订史），内容逐字保留、未做裁剪。
> 反向指针：[删除依赖与快照可用性保护方案.md](docs/删除依赖与快照可用性保护方案.md)（当前契约）

> 版本：v1.3（2026-10-27 初稿；2026-10-27 补 `purge_disk` 与归档互斥口径；2026-10-28 全量裁决定案；2026-10-28 **P0/P1/P2 全部落地**）

## 未发布（2026-11-02）

### 修复
- **仓库页子组件样式全面失效（scoped 越界）**：仓库页的 `repo__*` class 定义在父页 `<style scoped>`，但 `RepoListCard` / `RepoMaintenanceCard` / `RepoEditForm`（及向导复用的 `NewRepoStep`）内部元素不带父页 scoped 的 data-v 属性——`.repo__note { display: block }` 等规则对子组件**全部失效**，维护卡「清空本地缓存」行的多行信息堆叠被挤成单行（2026-11-02 owner 截图指认）。修复：整块去 scope（全部 class 带 `repo__` 前缀，grep 核实无页外使用，无泄漏风险）。真机核验 note 元素恢复块级堆叠。
- **仓库删除/释放后 404「仓库 'xxx' 不存在」刷屏**：删除门 `onDeleted` 契约是「删的是当前仓 → 切回 default」，仓库页却误接成 `onSetCurrent(被删 id)`，把已删/已归档 id 持久化进 localStorage，此后每个带 `X-Repo-Id` 的请求都 404。修复：①接线改回真正切 default；②http 层兜底——`REPO_NOT_FOUND` 且失效的是**实际生效的仓库路由头**时自动切回 default（调用方显式 `X-Repo-Id` 覆盖不触发，快照页看归档仓不受影响）。护栏 `http.spec.ts` +2 例（当前仓 404 回退 / 显式覆盖不回退）。
- **密钥源归属与凭据加密缺口（0–6 批全落地，方案源《修复方案-密钥源归属与凭据加密缺口-2026-11-02.md》v1.2，**已归档至 `docs/archive/`** 2026-11-02，仅供追溯）**
  - **缺口 1 · 轮换漏全局键**：`key.rotate` 的清理范围原本手抄 `cred.<repo>.` 前缀，漏掉归属该仓库的全局敏感键（`dump.password` / `notify.*` / `metrics.token`）；改由 `crypto.keys_of(repo_id, keys)` 给出（**双参：crypto 不读库，红线 A1，全量键由调用方传入**），键归属判定自此只有 `crypto.owner_of()` 一个出口，`_repo_id_for_cred_key` 降为其实现体。
  - **缺口 2 · force 覆盖留残密文**：`POST /api/repos/{repo_id}/password` 的 `force` 覆盖路径**压根不调用任何清理函数**，用新口令覆盖后旧 `enc:v2` 密文永不可解，进而让 `ConfigRepo.list_all()` 全表逐行解密失败——单仓库故障被放大成全站故障。现在 `write_passfile` 成功之后（写失败零副作用）按 `keys_of` 清理该密钥源旧密文，**仅清密文，明文与 env 键保留**；`_clear_credentials(D)` 与 `retention` 兜底分支(E) 同步改为问 `keys_of`。
  - **缺口 3/4 · 回收误伤 default**：`retention` 的 `known_ids` **无条件并入 `DEFAULT_REPO_ID`**（配合 `crypto.must_be_owned`）——default 口令文件即使在 `repos` 行缺失时也不会被判孤儿回收，堵死「行没了 → 文件被回收 → 全局键永久解不开」。
  - **缺口 6 · release 端点缺守卫**：`POST /api/archive/repos/{repo_id}/release` 补齐三门——`ensure_deletable`（default 禁删 + 有任务/活跃作业 409 + 被镜像引用 409）、**必须已归档**否则 422、外键冲突 `IntegrityError` 译成 409 不落 500。修复前正常 UI 操作即可删掉 default 行。

- **系统设置页保存按钮图标空白**：`SectionSystem` / `SectionMetrics` / `SectionTimezone` 三个分区的保存按钮模板使用了 `<IconCheckCircle />` 但**未 import**（Timezone 只导入了 IconClock）——Vue 将未导入组件静默回落为原生空元素，`n-icon` 的 16px 槽位照常保留，表现为「保存」文案左侧一块固定空白。补齐三处 import（Timezone 并为 `{ IconCheckCircle, IconClock }`）。该坑与第 5 批记录的「无自动导入插件、组件必须显式 import，typecheck 不报错」同型，图标版。真机三分区逐处核验图标渲染。
### 变更（行为变化，需留意）
- **向导「新建仓库」步的一次性密码改为可复制弹窗（2026-11-02）**：建仓生成态原用 `message.warning` 常驻 toast 呈现 32 位密码——不可选中复制，用户只能手抄；现复用全站唯一实现（`PasswordEntryModal` 生成态 = `SecretReveal`：只读明文 + 复制按钮 + 确认关闭），与仓库页「设置密码 / 重建结果」同款交互。确认只关弹窗，不影响建仓「四项齐备」放行判定；明文仍只在内存（S3）。i18n 键 `wizard.newRepoPassword`（含明文插值）替换为 `newRepoPasswordTitle/Hint/Confirm` 三键（中英同批）。护栏 `newRepoPassword.spec.ts`；真机探针 PASS。
- **读取侧降级语义（方案 B-full）**：`ConfigRow` / `ConfigItem` 增 `degraded: bool` + `degraded_reason: str|null`，三态判定——没配过（`has_value=false` 且 `degraded=false`）/ 有值（`has_value=true`）/ **失效**（`has_value=false` 且 `degraded=true`）。`list_all()` 改为**按密钥源分组解密**：某密钥源首次失败即降级该源全部键，其余密钥源照常读出。
- **两条路径语义刻意分叉**：**展示路径**（E06 `GET /api/config`、两条导出）降级**不抛**；**执行路径**（`env_sync.load_credentials`，每个备份任务的必经之路）遇降级行**抛 `CredentialDecryptError`（500）**，**绝不降级为空值**静默跑——否则备份会带着缺失的云凭据跑到「认证失败」，把排查引向错误方向。该错误码自本版起也承担「密钥源失效」语义。
- **启动期**把失效密钥源汇总成**一条** `logger.warning`（不是逐键刷屏）。前端 `api/types.ts` / `openapi.json` / `schema.d.ts` 同步。

### 文档
- **架构设计文档**：§4.3 R-025 注——② 键归属规则改为「唯一出口 `crypto.owner_of()` + `keys_of`/`must_be_owned`/`is_sensitive` 三判定，轮换/清理/回收三处必须问它」并注明 `SENSITIVE_EXACT_KEYS`/`SENSITIVE_PREFIXES` 唯一定义点在 `services/crypto.py`（config_repo 只再导出）；代价段补第二道保险；新增 **④ 读取侧降级**段。§5.0：`CREDENTIAL_DECRYPT_FAILED` 补「也承担执行路径的密钥源失效」、`REPO_NOT_EMPTY` 补「E59 release 复用该码」（**未新增错误码**）。§5.1：E06 补 `degraded`/`degraded_reason` 契约、E44 补 force 覆盖清理动作、E59 补三门守卫。
- **云端仓库安全基线（§2.3，修订五）**：补「口令文件失效的影响面」——已按密钥源隔离（配置页显示「失效」而非「没配过」，涉及该源的备份任务直接失败），但 default 口令文件仍是全局键单点且不会被孤儿回收。
- **部署指南**：`RESTIC_PASSWORD_FILE` 逃生舱补「显式设全局口令文件 ⇒ 隔离收益归零」；R-025 段补读取侧降级语义。
- **配置迁移导入导出说明**：§2 包格式 v1 → **v2**（v1 仍可导入），§3 补「v2 新增加密段并列 `degraded` 映射（键 → 失效原因）」。
- **文档管理规范 v1.62**：登记表该方案行改**归档行**（v1.2 六批全落地、§11 两条遗留由第 6 批关闭、结论见架构 v1.14 + 本文）。归档经 owner 确认后 `git mv` 入 `docs/archive/`。
- **架构设计文档升 v1.14**（文首版本行 `v1.13` → `v1.14` + `文档管理规范.md` §6 登记表同步）。按本文第 8 行约定，架构文档自 v1.11 起不再内联修订历史，故此处**不**向上方「架构设计文档修订史」段追加 v1.14 条目——本次改动摘要见上三条与登记表。
- **方案文档已归档**：`git mv` 入 `docs/archive/`（owner 确认）。归档前修掉三处指向本文的失效表述（架构 §5.1 E06 的「两条导出不含标记…见方案 §11」、E36b 的 `version=1`）并补记本文第 6 批，见《文档管理规范》v1.62。
- **`scripts/check_dockerignore.py` 修 GBK 控制台崩溃**：脚本打印 `✓`/`✗`（U+2713/U+2717），Windows 控制台默认 cp936 下抛 `UnicodeEncodeError` 直接崩溃，致 `bash scripts/verify_redlines.sh` 的 **G8 恒为假失败**（19 项里 1 项误报）。最小修法：`main()` 开头对 `sys.stdout`/`sys.stderr` 调 `reconfigure(encoding="utf-8", errors="replace")`（`hasattr` 保护非 TTY/重定向场景），**不改脚本语义与输出形态**。修后不设任何环境变量即 19/19。
- **方案文档本身 v1.0**：状态行改已实施 + 实施结论；`keys_of` 签名更正为双参；记 §9.3 与红测试②的断言偏差；§8.2–§8.4 标 ✅；新增 §11 已知遗留（**本次不修**：两条导出取 `row.value or ""`，降级行导出空串且包里无标记，待 owner 决定是否加 `degraded` 标记）。

### 第 5 批 · 前端渲染「失效」（2026-11-02，补齐文档已宣称的界面形态）
- **背景**：前 4 批只做到 API 契约（`ConfigItem.degraded`），前端**一处都没渲染**——仓库配置页的凭据徽标、设置页的 dump 口令与 metrics 令牌仍只看 `has_value`，密钥源失效时显示「未配置」。而架构 §4.3 注④、§5.1 E06、部署指南、云端基线四份文档都已写「配置页显示『失效』标记（不是『没配过』）」——**文档超前于代码**。本批让代码追上文档。
- **改动**：`RepoEditForm.vue`（凭据徽标）、`SectionDump.vue`（dump 口令）、`SectionMetrics.vue`（metrics 令牌）三处按 `degraded → has_value → 未配置` 优先级渲染；失效态用 `StatusTag kind="warning"` + `n-tooltip` 显示 `degraded_reason`（沿用既有「env 覆盖」提示的同款交互，原因不塞进徽标文字）。新增 i18n 键 `repo.credDegraded`/`repo.credDegradedReason`、`settings.dump.passwordDegraded(Reason)`、`settings.metrics.tokenDegraded(Reason)`，中英双语同批。
- **两个非显然点**：① `RepoEditForm` 的 `itemOf` prop 是结构化子类型，就地加宽即可，**父页不用改**；② 项目无 naive-ui 自动导入插件，`<n-tooltip>` 必须显式 import `NTooltip`——漏掉时 typecheck 照样过，但运行时是未知自定义元素、hover 静默无反应。
- **护栏**：新增 `frontend/src/pages/settings/__tests__/degraded-badge.spec.ts`（5 例）——锁死「degraded 时显示失效而非未配置」「未失效时三态正确」「degraded 优先于 has_value」与中英键齐备。

### 第 6 批 · 导出包降级标记（2026-11-02，关闭方案 §11 遗留）
- **背景**：第 3 批只把「失效」做进了 `GET /api/config`，两条导出（全量 `POST /api/config/export`、单任务 `E36b`）仍取 `row.value or ""`——降级行导出空串且**包里无标记**，接收端分不清「本来没配」与「密钥源失效」，等于把「不知道」当「没有」传下去。
- **改动**：`services/config_transfer.py` —— `PACK_VERSION` 1 → **2**，新增 `_SUPPORTED_PACK_VERSIONS = {1, 2}`（导出恒为最新版，导入兼容旧包，不作废开发期 v1 导出），`build_export_pack` 增 `degraded_rows` 参数，`parse_export_pack` 回 `degraded` 映射；`routers/ops.py`（全量导出）与 `routers/backup_tasks.py`（单任务导出，范围仍限该任务的 `cred.<repo>.*`，**刻意不动**）两处传 `{row.key: row.degraded_reason}`。
- **为什么进加密段**：`degraded` 的键名就是凭据键名，放明文段等于把「本实例配过哪些凭据」泄露给任何拿到导出文件的人（S3）；原因串只含密钥源标识与异常类别，不含任何键值/密文。
- **护栏**：`tests/test_config_transfer.py` 增 4 例（v2 降级键带标记且明文段不出现键名与原因 / 无降级行不写字段 / v1 旧包仍可导入 / version=99 被拒）。

### 第 7 批 · rclone 表单规整 + 全站「生成密码」统一 + 单一实现收口（2026-11-03，纯前端）

- **rclone 配置表单排布规整（owner 截图指认 + 方案 B 演示稿对比拍板）**：说明/警告从输入框右侧同行改为下方独立整行（四输入行）；警告缩为单句 + `passwordFormHint` 重排 + `passwordObscureNote` 方位词修正（中英同批）；控件宽度五档收敛 **320/400 两档**；「密码形态」块重排并新增「维护与找回」分组框（i18n 键 `maintGroupLabel`）。裁决逻辑零改动（裁决 17、凭据零回显、SecretReveal 不动）。
- **全站「生成密码」按钮统一（owner 裁决：取配置迁移导出口令按钮样式为唯一形态）**：收进 `PasswordInput`（输入框右侧、secondary small、盾牌图标，S-4 单一实现）；`PasswordEntryModal` 底部生成按钮挪进自填态输入框（`showGenerate=false` 态仍隐藏）；`SectionTransfer` 手写按钮改走 `generate-label` 通道。全站 6 处可见生成按钮单一形态。**顺带修复**：导入覆盖 ConfirmGate 曾误嵌在按钮 `<template #icon>` 内，解缠至导入区末尾。
- **单一实现收口（owner 追问「还有没有别的手写」后核查）**：① `formatBytes` 三份手写副本删除（`repo/index.vue` / `RepoMaintenanceCard.vue` 逐字节相同、`audit/index.vue` 口径已漂移缺 GiB/TiB 档），统一走 `utils/format.ts`；② `utils/passwordStrength.ts` 新增 `countCharClasses()`，类别定义唯一出处，bootstrap `strengthOk` 改为消费它；③ 新增 `utils/clipboard.ts` `copyWithNotify()`，收口 ConfirmGate / SecretReveal / RcloneConfigPanel 三处复制样板（bootstrap 静默策略刻意不迁）。全站核查（裸 password 扫描 / 唯一生成器消费方 / i18n 键反查 / @click 兜底）确认无其他「有组件却手写」遗留。
- **护栏**：`PasswordInput.spec`（含裸 password 护栏）+ `RcloneConfigPanel.spec` 22 例在新布局下全绿；零后端改动。

### 校验结果
- 后端 WSL 全量 `pytest tests -q`：**1296 passed / 0 failed**（第 6 批 4 条护栏后为 1296；第 5 批时为 1292）。
- 前端全量 `vitest run`：**575 passed / 61 files**（第 7 批后；2 条 `Errors` 为 `wizardStepSequence.spec.ts` 的 Vue setup 警告，HEAD 基线复现同样 2 条，与本批无关）。
- 前端 `vue-tsc --noEmit`：0 error。
- `ruff check app tests` 干净。
- `bash scripts/verify_redlines.sh`：**19/19 全部通过**（Windows Git-Bash 不设任何环境变量、WSL 两侧均实测；G8 需 python 解释器，无 python 时该项跳过并计 18/18）——AGENTS.md 与规范 §3.1 编号锚点一致，未新增/删除红线条款。

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
