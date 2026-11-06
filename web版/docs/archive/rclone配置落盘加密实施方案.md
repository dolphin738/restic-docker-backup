# rclone 配置落盘加密实施方案

> **已归档（2026-11-06）**：`rclone.conf` 落盘加密与运行时口令通道的**交付内容已上位化**至《架构设计文档》§5.1 E64–E68（5 端点全契约）、§5.0（`RCLONE_CONF_LOCKED` / `RCLONE_UNAVAILABLE` 503）与《部署指南》§4.2（口令落点三种部署形态、逃生通道、路径含空格处理）。实施提交链 `33a52fd` / `a280e93` / `c880f90` / `6ed6bca`（2026-11-01 ~ 11-06）；GitHub Issue #2 已按 Q5 关闭（2026-11-06）。归档前完成：①**反向回填 v1.11**（服务层第五方法 `unlock()`、S6 27 → 28 项、`<PASSFILE_ROOT>` 形态无关、E68 独立 `rclone.conf_unlock` 审计、§8/§11/架构 :921 过时自述订正）；②**引用指针迁移**（部署指南 :482/:488、架构 :564/:911、compose :48/:51 → 架构 §5.1 E64–E68 / §5.0）；③**§12 三条 ⏳ 与 §10 已知缺口终态处置**（明确不做/已接受，本环境 `docker` 不在 PATH 无法实跑真容器，如实登记）。本文不再演进，仅供追溯。
>
> 版本：**v1.11**（2026-11-06 归档前回填；v1.0 2026-11-05 初稿 → v1.10 2026-11-05 已实施并修订）
> 状态：**已实施完毕并归档**（端点契约以《架构设计文档》§5.1 E64–E68 为唯一权威规格）
> 定位：方案层——`rclone.conf` **落盘加密与运行时口令通道**的实现依据（Issue #2）
> 关联：《架构设计文档》§5.0/§5.1、《部署指南》§4.2、《代码审查规范》§3.1 S6（28 项）、`AGENTS.md` 红线 5
> 证据基线：`main @ 545b525`（归档前）；代码演进后以符号名复核。
>
> ---

> 版本：v1.11（2026-11-06，**已实施并回填**）
> 层级：方案层——`rclone.conf` **落盘加密与运行时口令通道**的实现依据（Issue #2；归档后权威规格见《架构设计文档》§5.1 E64–E68）
> 边界：本文只覆盖 **conf 文件的加密态识别与口令通道**。rclone 段的增删改查能力边界（注册表、凭据混淆、占用扫描、配置归档）见《rclone配置生成工具设计方案》v1.21，本文不重复、不替代。
> 关联：`app/services/rclone.py`（只读探测）、`app/services/rclone_config.py`（conf 管理）、`docker/Dockerfile`、`docs/部署指南.md` §4.2、`docs/架构设计文档.md` §5.0/§5.1

> **修订记录**：
> - v1.11（2026-11-06）：**归档前反向回填（代码已再次超出文档，与《文档管理规范》v1.65 的「不归档、需反向回填」裁决理由同构）**——本会话 4 个修复 commit 又改了 conf 加密面，方案却停在 v1.10，故按真实代码回填后冻结归档。①**服务层第五方法 `unlock(password)`**：`c880f90` 起 unlock 端点不再复用 `rclone.conf_rekey`；`6ed6bca` 把路由层 4 个口令读写/回滚 helper（`_atomic_write_pass` / `_restore_pass` / `_rollback_unlock` / `_unlock_conf`）全部删除，收敛为 `RcloneCryptoService.unlock()` 复用本层 `_write_pass` / `_restore_pass` / `_set_password_command_env` / `status()` 原语，路由只留门禁与审计。§5.10 方法表由「四方法」补为「五方法」，运行期口令通道切换与 §8 护栏同步补 unlock。②**S6 危险操作 27 → 28 项**：新增 `AUDIT_RCLONE_CONF_UNLOCK = "rclone.conf_unlock"`（`backend/app/constants.py`），《代码审查规范》v1.19 与 `AGENTS.md` 红线 5 已登记、`scripts/verify_redlines.sh` 与 `backend/tests/test_redline_assertions.py` 反向锚点已同步；故 §5.10 纪律条与 §8 护栏由「三动作 / 24 → 27」更正为「四动作 / 28 项」，《架构设计文档》E68 由「审计复用 `rclone.conf_rekey`」更正为「独立 `rclone.conf_unlock`」。③**`<PASSFILE_ROOT>` 形态无关**：`6ed6bca` 新增 `resolve_pass_path(settings)` + `RCLONE_PASS_FILENAME` 作口令路径单一真源（`main.py` 启动自检与 `RcloneCryptoService.__init__` 共用），`RCLONE_CONF_LOCKED_HINT` 去掉 docker 专属 `/repos` 字面、`main.py` 删手写的第二份 hint 副本；故 §1 结论表、§5.8 的硬编码 `/repos/rclone.pass` 改为 `<PASSFILE_ROOT>/rclone.pass`（`/repos` 仅作 docker 默认形态举例）。④**文档订正**：§8 端点契约 4 → 5 端点、§11 关系表（P8 已回填 / 5 端点 E64–E68）、《架构设计文档》E68 独立审计。⑤**§6 实现清单与 §7 批次表是历史快照**（v1.6 修订记录已如实记端点 4 → 5），按「不改历史」口径保留原计数，以本条为准。
> - v1.10（2026-11-05）：**备份开关改为持久化偏好**（owner 真机反馈「设置开了就记住开，设置关了就记住关，现在是设置关，重新打开页面就变成开」）。此前开关是组件内 `ref(true)`，面板重挂载即回默认开。现持久化到 config 键 **`ui.rclone_conf_backup`**（`"1"`/`"0"`，缺省 1）：挂载时经 **E06 `GET /api/config`** 读回，用户拨动时经 **E07 `PUT /api/config`** 写回。**零后端改动**（`ui.*` 非敏感键、未登记进 `_BOOL_KEYS` 故放行，读侧 `ConfigRepo.mask` 回明文值）——复用的是既有全局 KV，不新增迁移/端点/表。三点边界：①**偏好不是状态真源**，读取失败静默回落默认开，不阻塞加密面板；②写入失败只 `message.error` 提示，不回滚开关、不阻断随后的启用请求；③服务端单值、不随 `X-Repo-Id` 分仓（rclone conf 本身就是全局 conf），且**不参与配置导出/导入**（导出口径只含 `behavior.`/`notify.`）。键已登记进《架构设计文档》§4.3 键表（v1.16）。
> - v1.9（2026-11-05）：**真机第三轮反馈：「备份开关 + 按钮右对齐」**（owner 指令「把这个备份 conf 功能做一个开关，让使用者选择，另外把加密口令模块的按钮挪到右边」）。①**逃生备份从强制改可选**——v1.8 已把它收窄成「只有 enable 写」，但仍是无条件写。现由界面开关「启用前备份明文 conf」控制（默认开），落到 API 即 `RcloneConfEnableRequest.backup: bool = True`。**关键设计**：回滚依据与开关解耦——`enable()` 先把操作前字节读进内存 `original`，`backup=True` 才额外 `_write_escape_backup(original)`；失败分支一律 `_restore_conf_bytes(original)`。所以关掉开关**不会**让启用失败时 conf 停在半截，代价只是「口令丢了就再也打不开」这条恢复路径被用户显式放弃（审计 detail 增 `backup` 布尔留痕）。关掉时不留任何明文副本，威胁面上的明文驻留由用户自行权衡。②**动作行右对齐**——加密卡片动作行加 `.rclone-panel__row--end { justify-content: flex-end }`，状态徽标与说明留在左侧读序起点。开关只在「未加密且未锁死」时渲染（换口令/停用不写备份，加密态显示它只会误导）。**验收**：`tests/test_rclone_crypto_backup.py` 3 → **5 例**（新增 enable 关备份后零明文副本 / 关备份时失败仍逐字节回滚），变异测试（`if backup:` → `if True:` 后 2 例失败）确认能抓回退；后端 pytest **1308 passed**、ruff 0、vue-tsc 0 错、panel spec **29 passed**、全量 vitest **601 passed**（2 条既有 unhandled rejection 无关）、红线 **19/19**；真机浏览器实测加密态两按钮靠右、就地停用后变为「[开关] [生成并启用加密]」右对齐且无残留。
> - v1.8（2026-11-05）：**真机第二轮反馈两条（提交 `7f9a499` + 本轮后端收口）**。①**停用后仍多一个按钮**：v1.7 的三态 gate 只解决了「状态未就绪」，真机复现的是**状态已就绪的就地切换**——根因在 Naive UI 的 `NSpace`：它给每个插槽 item 硬编码 `key=1`（2.45.3 `space/src/Space.mjs:130`），按钮从「更换+停用」两个变成「生成并启用」一个时，Vue 的 keyed diff 把该卸载的节点留在 DOM，叠加 `:loading` 就成了那个「多出来的按钮」，刷新才消失。改为**裸 div**（`.rclone-panel__row` / 新增 `.rclone-panel__stack`），子节点用显式 key，条件分支切换不串位；控制台那条 `<Space key=1>` 重复 key 警告同步消失。②**备份文件的目的说不清、且换口令后的备份打不开**：核对后确认 `rekey()` 也调 `_backup_conf()`，而它备份的是**旧密文**——旧口令一换，备份用新旧口令都 `check` 不过（真机实测 exit=1/1），既当不了逃生通道又白留副本。改为 **`rekey()` 用内存快照回滚、不写备份**；逃生备份**只由 `enable()` 建立**（备份操作前的**明文** conf，口令彻底丢失时才用得上）。`disable()` 本来就只用内存快照。**验收**：新增 `tests/test_rclone_crypto_backup.py` 3 例（enable 写明文备份 / rekey 不写 / rekey 失败逐字节还原），经变异测试确认能抓回退；真机 disable 后就地只剩「生成并启用加密」且 spinner=0；rekey 两次后 `conf-backup.conf` mtime 不变。
> - v1.7（2026-11-05）：**真机使用后修三个缺陷（提交 `323488d`）**，其中一个是 v1.6 的安全回归。①**🔴 明文备份堆积**：`_backup_conf()` 原先把当前 conf 明文写到 **conf 同目录** `conf-backup-<时间戳>.conf` 且成功路径也不删（真机已积 7 份）——本方案 §9.1 把「强制备份」设计为逃生通道，但逃生通道不该是长期明文驻留：威胁模型是「`./rclone/` 被单独拷走」，同目录明文副本比加密 conf 更好用，**加密等于白做**。改为 **`<passfile_root>/conf-backup.conf` 单一滚动文件**（同目录 mkstemp + 0600 + `os.replace`），取走明文需同时拿到 conf 与 passfile 两卷，与既有密钥分离策略一致；conf 同目录的旧时间戳备份**不自动删**（运维可能依赖），仅 `logger.warning` 报数量。②**状态未就绪时渲染写按钮**：`v-if="!confLocked && (!confEncStatus || !confEncStatus.conf_encrypted)"` 在 `confEncStatus === null`（刷新失败）时放行了 enable 按钮，叠加 `:loading` 转圈形态，视觉上像两个按钮；改为按钮组整体 gate 在 `confEncStatus` 上，未就绪只显示可操作 alert + 重试，`refresh()` 失败时写入 `confEncError` 而非静默置 null。③**rekey 新口令段没有生成入口**：`PasswordEntryModal` 硬传 `:show-generate="false"`，改为 `confEncAction === 'rekey-new'`（旧段 false / 新段 true），生成的明文只进内存 ref 并直接用于轮换。**验收**：WSL 真实 rclone 1.75.2 连续 enable→rekey→rekey 后 passfile_root 下恰 1 份 `conf-backup.conf`（0o600）、conf 同目录零新增；回滚后 conf 逐字节还原；ruff/`pytest tests/test_rclone_config.py` 80 passed/`vue-tsc` 0 错/panel spec **25 passed**（22+3 新增，经变异测试确认能抓原缺陷）；真机浏览器三态按钮与 rekey 生成入口均按预期。
> - v1.6（2026-11-05）：**P1–P8 全部落地，方案转「已实施」**。提交链 `808a3c6`（P1 规范/常量/错误码）→ `33a52fd`（P2–P5+P7 后端）→ `a280e93`（P6 前端）→ `ab7422a`（FIX-SPACE + UI key 修复）。**实施中发现并修复一个 v1.5 未预见的 🔴 缺陷**：`RCLONE_PASSWORD_COMMAND = f"/bin/cat {pass_path}"` 在 `PASSFILE_ROOT` 含空格时被 rclone 按空白切分 exec 拆断（真机 `D:\agent\AI Coding\...` 经 WSL 挂载即含空格），导致全部 `rclone:` 远端不可用；修法是命令值恒为**无空格脚本路径**（`password_command_for()`，脚本体内 `shlex.quote` 真实路径），已用真实 rclone 1.75.2 在含空格路径下端到端验证 enable/rekey/disable 全通。**另两处实施偏差**：①端点数 4 → **5**（方案 §5.3 的第三态「输入口令解锁」需要独立端点，v1.4 已预见但 §6 只列了 4 个，实际落地补 E68）；②新增 schema 4 → **5**（拒绝复用 `OkResponse`——它在 backend 有 49 处使用、`schema.d.ts` 出现 16 次，加字段会污染全部既有端点契约）。**验收**：后端 pytest 1303 passed / 0 failed；前端 typecheck 0 错；RcloneConfigPanel.spec 22/22；真机浏览器 enable→rekey→disable 全链路通过且 conf 与备份逐字节相同。
> - v1.5（2026-11-05）：**P-1 批次落地：从代码移除 entrypoint 口令自动生成**（owner 指令「先做 P-1」）。删除 `docker/entrypoint.sh` 的 `RCLONE_PASS_FILE` 变量（6 行注释 + 1 行赋值）、`ensure_rclone_pass()` 整函数（21 行）、root 档与非 root 档两处调用（4 + 5 行），共 −36 行；`docker/docker-compose.yml` 删掉 `# RCLONE_PASS_FILE` 与「首次启动自动生成」四行说明，改为指向界面生成（§5.10），`RCLONE_PASSWORD_COMMAND` 语义保留但在 compose 中不再预置。《部署指南》§4.2 那节原写「设一个路径、容器首次启动自动生成」，现改为**如实说明本版本 conf 加密未启用、不做启动期自动生成、勿再配 `RCLONE_PASS_FILE`**，并保留「不要复用仓库口令文件」的警示。**保留不动**：`PASSFILE_ROOT` 独立密钥卷处理（`PASSFILE_ROOT: /repos` + `./repos:/repos:rw` 默认布局）与 `need_dir` 的 `_hint` 形参（仍被 `need_dir "$PASSFILE_ROOT" "<宿主机 keys 目录>"` 使用）。**验证**：`dash -n`/`sh -n` 通过；`docker/` 下 `RCLONE_PASS_FILE`/`ensure_rclone_pass` 零命中；5 场景行为测试全绿——显式设置已废弃变量时**不再生成任何文件**、独立密钥卷处理仍正常、未设 `PASSFILE_ROOT` 时行为与旧版一致。**规模修正**：实现清单 3/4 标记完成，§7 P-1 标记 ✅，§5.9 移除状态由「代码待删」改为「已删」。
> - v1.4（2026-11-05）：**界面改为完整管理（生成/更换/停用/解锁），移除 entrypoint 自动生成**（owner 三问裁决：Q1 停用也做、Q2 口令落 `./repos/rclone.pass` 且移除 entrypoint 自动生成、Q3 先更新方案）。**推翻 v1.2/v1.3 两处结论**：①**「界面只读、零致锁入口」被实测推翻**——应用可经 **argv** 显式传 `--password-command`（不依赖容器 env），在 `stdin=DEVNULL` 下完整驱动 `set`/`check`/`remove`，且 **argv 优先于 env**（argv 对+env 错→成功，argv 错+env 对→失败）；另实测**明文 conf 对口令命令失败免疫**（指向不存在的文件时 `config dump` 仍 exit 0），故首次启用时序宽松。②**entrypoint 自动生成移除**——两处都能生成会形成隐式竞争，且后台生成的 32 位随机串用户从未见过，丢文件即永久锁死；界面生成必然经 `SecretReveal` 一次性展示。**本轮改写**：§1 结论表、§5.2（界面完整管理 + 三条实测证据）、§5.3（三态 × 三按钮 + 停用次要按钮 + 组件复用）、§5.9（口令唯一落点 `/repos/rclone.pass` + 移除自动生成的对照表 + 不复用仓库口令的四行对照）、**新增 §5.10 服务层设计**（`rclone_crypto.py` 四方法 + 三条纪律：mkstemp 临时文件 / 审计三动作入 S6 24→27 / 写后必 `check` + 失败回滚 + env 重建 + `JOB_CONFLICT` 并发门禁）、§6 实现清单（去 2b/+2c–2h）、§7 批次（**新增 P-1 破坏性前置**）、§8 测试（新增口令生命周期 12 条）、§9.1（缓解措施从纪律变产品行为 + 逃生通道）、§10。**规模**：生产代码 ≈195 → **≈310 行**，测试 ≈180 → **≈330 行**，手改文件 14 → **18 个**。
> - v1.3（2026-11-05）：**口令章节的一致性返工**（owner 追问「关于密码的更新文档了吗」——核查后确认 v1.2 确实漏改多处，本节即为修正）。发现的五处失真：①**§5.8 与 §5.9 自相矛盾**——前者仍写「口令由人定、**不由应用生成**」，后者写 entrypoint 自动生成；改为 §5.8「部署侧自管三形态（非默认路径）」+ §5.9「自动生成（推荐）」，并明示两者互斥（同配时 `RCLONE_PASSWORD_COMMAND` 完全接管、env 不兜底）。②**§10 两行重复且过时**——「界面录入配置口令」与「界面录入/生成/轮换口令」内容重叠，合并为一行并补上「写进 DB 会让 `/data` 同时握有锁与钥匙」这一与暂缓存档冲突的理由。③**§9.2 引用了不存在的模板**——原文写「部署指南给出双分支脚本模板」，实际 `部署指南` 零命中；双分支脚本其实属于 §4.6，已在 §4.6 补出可复制的 6 行模板，并把 §9.2 该行改为「用自动生成时不需要任何脚本」。④**§9.1 缓解第 2 条**仍写「口令走 secrets」，与 §5.9 的默认文件路径不一致，改为「走文件、不要硬编码进 compose env」。⑤**§4.6 缺换口令操作步骤**——自动生成模式下「`cat` 同一文件两次＝换了个寂寞」，补出「写 `.new` → 临时指向双分支脚本 → 跑 `set` → 验通再改名」的完整手工序列（含「中间不要删旧文件」）。另：实现清单第 2 行与 2b 行标记为已完成并挂上提交号 `bf5d49c`，§1 结论表补「口令不用人手写」一行。
> - v1.2（2026-11-05）：**口令改为 entrypoint 自动生成 + 明确拒绝复用仓库口令**。①owner 追问「能直接用默认仓库的密码文件吗」——实测**可行**（`RCLONE_PASSWORD_COMMAND='cat <passfile>'` 能完成 `encryption set` 与读取，生成器那套 `!@#$%^&*-_=+` 字符集在 rclone 侧也无问题），但**拒绝**：`ops.py:925` 的 `key.rotate` 会用 `write_text` 直接覆盖口令文件，`repo_lifecycle.py:605/:215` 与 `retention.py:400` 也会写/删，而轮换链路**零 rclone 感知**——复用会让「轮换仓库密码」静默把加密 conf 永久锁死，且延迟到下次备份才暴露。②owner 追问「找个地方能自动生成 rclone 密码」→ 新增 §5.9：`docker/entrypoint.sh` 增加 `RCLONE_PASS_FILE`（默认空 = 功能关闭），首次启动生成 32 位 `A-Za-z0-9`、`umask 077` 写入后 `chmod 600`、**已存在绝不覆盖**、父目录自动 `mkdir -p`；root 档随后 chown 到 `PUID:PGID`，非 root 档把父目录纳入 `need_dir` 硬检查。字符集刻意取纯字母数字（该口令要经 `cat` 喂给 rclone，避免 `! $ % & *` 被 shell/编辑器二次解释）。`docker/docker-compose.yml` 加 `# RCLONE_PASS_FILE` + `# RCLONE_PASSWORD_COMMAND` 两行注释示例（默认关闭）。**§5.8 保留但收窄**为「部署侧自管口令」的三种形态。
> - v1.1（2026-11-05）：**口令通道补全 + 一处 v1.0 结论被实测推翻**。①**推翻**：v1.0 写「rclone 没有改口令，只能 `remove`→`set` 且中间态是明文」——**错**。实际「`encryption set` 对已加密 conf = 原子 rekey」：`--password-command` 被调用两次，第二次带 `RCLONE_PASSWORD_CHANGE=1`，取旧口令解密、取新口令重加密；失败时 conf md5 不变。误判根因已复盘（§4.10：脚本未区分该环境变量 + 返回值带尾随换行 + 未读 `--help`）。②新增 §4.7 调用语义（**不经 shell**：管道/`\$VAR`/`&&` 全部当字面量，按空白切分后直接 exec；口令命令失败是**毫秒级明确报错**，与「缺口令挂 60 秒」完全不同）。③新增 §4.8 口令内容规则（空串拒绝 / 单字符接受 / Unicode 可用 / 尾部 `\n` 与 `\r\n` 剥掉、**首尾空格保留**）。④新增 §4.9 双通道优先级（`PASSWORD_COMMAND` 完全接管，env **不兜底**）。⑤新增 §5.8 口令三种供给形态（明文 env / compose secrets / 宿主文件），并澄清 compose secrets 仍落盘、收益是「不进 `docker inspect` 与 compose 文件」。⑥§10 更正 `rclone` 不支持轮换的错误表述。
> - v1.0（2026-11-05）：首版。汇总 issue #2 的 Q1–Q7 + E 裁决；补入 **2026-11-05 本轮实测**（rclone v1.75.2 / restic 0.19.1，WSL2），把票面四条「待验证」全部销账；据此把原设计中的「启用开关」**消解为加密态识别**（§5.2 说明理由），并采 owner 选定的**方案 C**——界面只读三态、启停走宿主 shell、应用侧零致锁入口（§5.3）；生产代码量由「通道 + 开关 + 验证 + 回滚」收敛为「通道 + 识别 + 三处语义修正」。

## 1. 结论

**做。** 威胁模型取**部分读**（只拿到 `./rclone/` 或 conf 被单独拷走，拿不到 compose），在此模型下 conf 加密有效。

三条工程结论决定了实现形态：

| 结论 | 依据 |
| --- | --- |
| **写路径零改动** | `exec_rclone`（`rclone.py:91-96`）与 `RcloneProbe._exec`（`:188-190`）都不传 `env=`，子进程全部继承父进程环境；实测加密 conf 上 `config create/update/delete/dump` 四条子命令全通且文件保持加密态 |
| **性能不构成理由** | 每次 restic 命令固定多 2–3 ms（占单命令总耗时不足 0.2%）；一条 `init + backup + snapshots` 只起 **3 个** rclone 进程，不是按请求数重复解密 |
| **不需要「启用开关」** | conf 加密态由**文件头**自证，明文 conf 无 header → 存量部署行为零变化；「默认关」天然成立，无需引入任何 config 键 |
| **界面完整管理（三态 + 按钮）** | 面板按加密态给主按钮：未加密→「生成并启用」、可解→「更换口令」、不可解→「输入口令解锁」；停用为次要按钮（§5.2/§5.3）。**v1.2 的「界面只读、零致锁入口」已被实测推翻**——应用可经 argv `--password-command` 在 `stdin=DEVNULL` 下完整驱动 `set`/`check`/`remove` |
| **口令只落 `<PASSFILE_ROOT>/rclone.pass`** | 由界面生成并**一次性展示**，让用户必然看见并备份（§5.9）；路径由 `resolve_pass_path(settings)` 单一真源派生、形态无关（docker 默认 `/repos`，host 为配置的 `<PASSFILE_ROOT>`，v1.11 回填）；**不复用仓库口令文件**——`key.rotate` 会静默锁死 conf；**不做 entrypoint 自动生成**（用户从没见过那把钥匙） |

真正的成本只有一条，且不可消除：**口令丢失 = 全部远端不可恢复**（§9.1）。

## 2. 术语与现状

「config 加密」在本项目有三义，只有第三义尚未成立：

| 对象 | 现状 | 出处 |
| --- | --- | --- |
| 应用 `config` 表敏感值 | **已加密**（`enc:v2:`，AES-256-GCM + HKDF） | `services/crypto.py:10,52-65,166-179` |
| 导出包 / 全站备份包 | **已加密**（AES-256-GCM + PBKDF2 600k） | `docs/全站数据备份与恢复设计方案.md:132,148` |
| `rclone.conf` **落盘** | **未加密**，只有 `--obscure` 混淆 | 本文 §3 |

**`obscure` ≠ 加密**：obscure 是固定密钥的可逆混淆，拿到 conf 即可 `rclone reveal` 还原（实测 `reveal` 无认证校验，见《rclone配置生成工具设计方案》§12.5）。项目当前全部依赖它（`rclone_config.py:67`「conf 里的凭据恒为 rclone 混淆产物」）。

conf 加密是**另一层**：整份 conf 用配置口令派生密钥加密，文件首行为 `# Encrypted rclone configuration File`，正文为 `RCLONE_ENCRYPT_V0:` 开头的 base64 载荷。

## 3. 裁决记录（issue #2 Q1–Q7 + E）

| # | 问题 | 裁决 |
| --- | --- | --- |
| Q1 | 威胁模型 | **部分读**——conf 单独泄露场景下加密有效；整盘读下无效（口令必须常驻进程环境），已知并接受 |
| Q2 | 口令通道 | 先实现 **Docker Secrets**，同时保留接入 KMS 的接口 → **已修订**：优先 `RCLONE_PASSWORD_COMMAND`（可直读 `/run/secrets/*`、`docker inspect` 不可见），`RCLONE_CONFIG_PASS` 作兼容回退 |
| Q3 | 存量明文 conf 是否迁移 | **不迁**——不自动转换，由部署者显式执行一次 `encryption set` |
| Q4 | 本票范围 | **仅 conf 加密**（权限与文档纪律归 #4，已关闭） |
| Q5 | 票怎么关 | 实现完成并通过验收后 close + 裁决理由 |
| Q6 | 密钥托管是否另立票 | 已开 #3 → **已删除**，评估内容落 `docs/暂缓存档-密钥托管架构-2026-11-04.md` |
| Q7 | 两个缺口合成一张票 | 已开 #4 → **已关闭**（文档失真已修，权限项经实测为伪缺口） |
| E | 做还是不做 | **做** |

**Q5 的落点变化**：原裁决为「E 等密钥托管落盘就做」，即被 #3 阻塞。#3 已删除且结论为「provider 抽象收益不足、零代码方案更优」，本票据 E「做」直接解锁，**不再等待任何前置**。

## 4. 实测记录（2026-11-05）

> 环境：WSL2（Debian 13，root，x86_64）；**rclone v1.75.2**（与镜像 `Alpine 3.24 community` 同版本）；**restic 0.19.1**；24 核。
> 容器内早期实测（`python:3.13-alpine3.24`，rclone v1.75.2-r0 / v1.75.2-DEV）已记于 issue #2 评论，此处不重复。

### 4.1 性能：解密成本可忽略

交替 A/B（plain / encrypted 轮换执行，抵消漂移），取 CPU user+sys 均值：

| conf 体积 | 明文 | 加密 | 差 |
| --- | --- | --- | --- |
| 18 KB（约 40 个 remote） | 69.3 ms | 69.5 ms | **+0.2 ms** |
| 180 KB（约 400 个） | 80.2 ms | 79.2 ms | −1.0 ms（噪声） |
| 1.8 MB（约 4000 个） | 155.8 ms | 156.4 ms | **+0.6 ms** |
| 1.8 MB（wall clock） | 92.1 ms | 99.0 ms | +7.0 ms |

端到端 restic（20 MB 数据、`rclone:` 本地后端、两轮交叉）：`init` 830–3309 ms、`backup` 1351–1876 ms，明文与加密的差值在两个方向上都出现，落在噪声内。

**为什么这么小**：AES-GCM/XSalsa20 是流密码（GB/s 级），conf 通常只有几 KB；rclone 进程启动 + 解析本身就要 70–156 ms，解密不足其 1%；**解密次数与数据量无关**——实测一次 `init + backup + snapshots` 只 fork **3 个** rclone 进程（`serve restic --stdio` 常驻至命令结束，每进程只读一次 conf）。

### 4.2 加密态与口令通道的完整矩阵

| 动作 | `RCLONE_PASSWORD_COMMAND` | `RCLONE_CONFIG_PASS` | 备注 |
| --- | --- | --- | --- |
| `config encryption set`（加密） | ✅ | ❌ 提示 `Enter NEW configuration password` 后 EOF 失败 | **不对等**：env 口令只能「读」，不能「设新口令」 |
| `config encryption remove`（解密） | ✅ | ✅ | |
| `config create / update / delete / dump` | ✅ | ✅ | 加密后文件保持加密态 |
| `serve restic --stdio`（备份路径） | ✅ | ✅ | init / backup / snapshots 全通 |

⇒ **开启加密必须走 `RCLONE_PASSWORD_COMMAND`**，这是唯一能让 `set` 无头完成的通道。

### 4.3 加密态探测：退出码天然三态

`rclone config encryption check`（`stdin=DEVNULL`）实测：

| 退出码 | 含义 | 输出 |
| --- | --- | --- |
| `0` | 已加密且解得开 | （空） |
| `1` | 已加密但解不开 | `Enter configuration password:` |
| `2` | 明文 | `Error: config file is NOT encrypted` |

比读文件头强的地方：**它同时验证了口令通道是否可用**。但**不能单独用**——读不到口令的部署里它一样返回 1，无法与「conf 未加密」区分开，故需与文件头组合（§5.3）。

### 4.4 失败语义（决定告警级别）

| 场景 | 表现 | 耗时 |
| --- | --- | --- |
| **错口令** | `Couldn't decrypt configuration, most likely wrong password` | 快速失败（~800 ms） |
| **无口令 · 配置面板路径**（`stdin=DEVNULL`） | `Enter configuration password:` → `Failed to read line: EOF`，退出码 1 | **685 ms 快速失败** |
| **无口令 · 备份任务路径**（restic fork，stdin 继承） | 交互式读口令失败 → 挂到 restic 超时（曾实测 60 s `context deadline exceeded`） | **慢**，且错误信息指不到真实原因 |

⇒ 同一故障在两条路径上表现相反。**面板会快速给出准确错误，定时备份却会静默挂 60 秒**，这是本方案必须前置校验的直接理由。

### 4.5 原子性与口令更换

- **失败是原子的**：`set` / `remove` 失败后 conf 字节不变（md5 前后一致），权限恒 `600`；
- **换口令是原子 rekey，不需要明文中间态**（详见 §4.6，此项推翻了本文 v1.0 的错误结论）；
- 加密后体积：18 591 B → 25 000 B（约 +35%，base64 膨胀），另有固定 121 B 文件头。

### 4.6 换口令（rekey）：存在，且是原子的

**v1.0 曾写「rclone 没有改口令，只能 `remove` → `set`，中间态是明文」——该结论错误**，成因见 §4.10「误判复盘」。

正确机制是**两阶段单命令**：对已加密 conf 执行 `config encryption set` 时，`--password-command` 会被调用**两次**，第二次环境里带 `RCLONE_PASSWORD_CHANGE=1`：

| 调用 | `RCLONE_PASSWORD_CHANGE` | 用途 |
| --- | --- | --- |
| 第 1 次 | 未设置 | 取**旧**口令，解密现有 conf |
| 第 2 次 | `1` | 取**新**口令，重新加密并落盘 |

实测（conf 已有 `alpha`/`beta` 两段，脚本按该环境变量分别返回 `OLDPW` / `NEWPW`）：

```
换口令前  OLDPW → alpha: beta:        ✅
换口令后  OLDPW → Couldn't decrypt   ❌
换口令后  NEWPW → alpha: beta:        ✅
inode 变化（临时文件 + rename），权限仍 600
```

**中间态不会出现明文**：失败时 conf 原样保留（md5 前后一致，且仍用旧口令可解），与 `set`/`remove` 的原子性同源。

⇒ 部署侧换口令 = 把脚本改成「`CHANGE=1` 时输出新口令、否则输出旧口令」，执行一次 `encryption set`。

```sh
# /usr/local/bin/read-rclone-pass（双分支模板）
#!/bin/sh
if [ "$RCLONE_PASSWORD_CHANGE" = "1" ]; then cat /repos/rclone.pass.new
else cat /repos/rclone.pass; fi
```

**用 §5.9 的自动生成时不需要这个脚本**（`RCLONE_PASSWORD_COMMAND` 只是 `cat` 同一个文件，两次调用拿到同一把钥匙 → rekey 等于「换成自己」，等于没换）。
此时换口令的手工步骤是：把新口令写进 `rclone.pass.new` → 临时把 `RCLONE_PASSWORD_COMMAND` 指向上面那个双分支脚本 → 跑一次 `encryption set` →
确认能解开后把 `rclone.pass.new` 改名为 `rclone.pass` 并恢复原 `cat` 命令。
**中间不要删旧文件**——失败时它仍是唯一能解开 conf 的钥匙。

### 4.7 口令通道的调用语义（决定脚本怎么写）

`--password-command` 的字符串**只被按空白切分一次、直接 exec**，**不经过 shell**。实测证据：

| 传入 | 实际发生 | 结论 |
| --- | --- | --- |
| `echo A \&\& echo B` | 口令是字面量 `A && echo B`；`A` 解不开 | 无 shell 语义 |
| `cat f.txt \| tr -d X` | `cat: invalid option -- 'd'` | 管道未被解释，`\|` 及之后成了 cat 的参数 |
| `$TESTVAR` | 口令是字面量 `$TESTVAR` | 无变量展开 |
| `definitely_not_a_command` | `exec: "...": executable file not found in $PATH` | 走的是 exec 系列调用 |

⇒ **推荐形态是单个可执行文件路径**（可带参数）：

```bash
# 推荐：一个脚本，自己决定怎么取口令
RCLONE_PASSWORD_COMMAND=/usr/local/bin/read-rclone-pass
# 也可带参数（按空白切分，含空格的参数会被拆开）
RCLONE_PASSWORD_COMMAND="/bin/cat /run/secrets/rclone_pass"
```

**取不到口令时是快速失败，不是挂起**：命令非零退出 / 输出空串都会被 rclone 立即判错——

```
ERROR : --password-command stderr: cat: /run/secrets/rclone_pass: No such file or directory
CRITICAL: Failed to load config file "...": password command failed: exit status 1
Failed to read new password with --password-command: returned empty string
```

⇒ 与 §4.4 的「缺口令挂 60 秒」不同：**只要配了 `RCLONE_PASSWORD_COMMAND`，口令不可达就是毫秒级明确报错**。挂到超时只发生在**两条通道都没配**、rclone 退回交互式读 stdin 的场合。这一点决定了启动自检的实现（§5.6）：`--password-command` 配了但失败 → 直接判锁死，无需等待。

### 4.8 口令内容规则：rclone 不设任何强度要求

实测边界：

| 输入 | 结果 |
| --- | --- |
| 空串（命令输出空行） | **拒绝**：`--password-command returned empty string` |
| 单字符 `a` | **接受**并成功加密 |
| 64 位随机串 | 接受 |
| 含非 ASCII（`pässwörd中文`） | 接受，可正常解密 |
| 首尾空白 | 保留为口令的一部分，且会打一条 `Your password contains leading/trailing whitespace` 警告 |

**换行与空白的处理规则**（这条最容易踩，`echo` 和 `printf` 的行为不同）：

| 文件内容 | 实际口令 | 说明 |
| --- | --- | --- |
| `AAA\n` | `AAA` | 尾部 **1 个** `\n` 被剥掉 |
| `AAA\n\n` | `AAA` | 尾部**全部** `\n` 被剥掉 |
| `AAA<空格>\n` | `AAA ` | 空格**保留** |
| `<空格>CCC\n` | ` CCC` | 前导空格**保留** |
| `DDD\r\n` | `DDD` | CRLF 一并剥掉（Windows 编辑过的文件安全） |
| （无换行）`CCC` | `CCC` | 不出警告 |

⇒ 运维上两条纪律：①**用 `printf` 或 `echo -n` 写出无尾随换行的文件**最省心，`echo` 多出的 `\n` 虽被剥掉但会触发警告；②**永远不要在口令文件里带首尾空格**——它们会被当作口令的一部分，而 `docker` 的 `echo`/编辑器极易引入。

### 4.9 两条通道同时存在时的优先级

**`RCLONE_PASSWORD_COMMAND` 优先于 `RCLONE_CONFIG_PASS`**，实测双向验证：

| env 口令 | 命令输出 | 结果 |
| --- | --- | --- |
| 错 | 对 | ✅ 成功（命令胜出） |
| 对 | 错 | ❌ 失败（不回落 env） |

⇒ 两条同时配置时**命令完全接管**，env 不构成兜底。切换通道时必须同时移除另一条，否则排查会陷入「明明改对了 env 却还报错」。

### 4.10 误判复盘：为什么 v1.0 以为「没有改口令」

当时对已加密 conf 直接跑 `encryption set` 并**用同一个固定口令**，得到 `Couldn't decrypt configuration`，据此判定「rclone 不会 rekey」。

根因有两条：

1. **被调用两次的是同一个固定输出**——脚本没有区分 `RCLONE_PASSWORD_CHANGE`，两次都返回旧口令，于是「解密成功但新口令 = 旧口令」，事后用任何单一口令都无法验证差异；更关键的是当时的脚本返回值还带了尾随换行（§4.8），实际口令与预期不符，报错被误读成「不支持 rekey」；
2. **没有去查 CLI 自带的帮助文本**——`rclone config encryption set --help` 里明写着「called once to decrypt … and then again to read the new password」「`RCLONE_PASSWORD_CHANGE=1` will be set」。

⇒ 教训记入本方案：**判定「某能力不存在」之前必须先读该子命令的 `--help`**，再下否定结论。

### 4.11 口令丢失不可恢复

用错误口令跑 `config encryption remove` → `Couldn't decrypt configuration`，文件原样保留。**没有任何工具能在不知旧口令的前提下解除加密**。

## 5. 设计决策

### 5.1 加密态是唯一事实来源，不引入 config 键

判定顺序（`services/rclone.py`）：

1. **读文件头**（纯文件读，零子进程、零成本、永不阻塞）：命中 `# Encrypted rclone configuration File` 或 `RCLONE_ENCRYPT_V0:` → 已加密；
2. 仅在「已加密」时，再跑一次 `config encryption check`（带 `RCLONE_PROBE_TIMEOUT_SECONDS` 超时）判口令可用性；
3. 明文 conf → 直接判「未加密」，**不调 rclone**。

### 5.2 界面完整管理：三态 + 按钮（**v1.4 推翻 v1.2 的「零致锁入口」结论**）

**v1.2 曾判定**「加密是一次性运维动作，界面只读、启停走宿主 shell」。**该结论已被实测推翻**：
应用可经 **argv** 显式传 `--password-command`（不依赖容器环境变量），在 `stdin=DEVNULL` 下完整驱动
`set` / `check` / `remove` 全生命周期：

```bash
# 三条都在 stdin=DEVNULL 下成立（= app 的 exec_rclone 真实条件）
rclone --password-command 'cat /tmp/pw' config encryption set     # exit 0，conf 变加密态
rclone --password-command 'cat /tmp/pw' config encryption check   # 0=可解 / 1=不可解 / 2=明文
rclone --password-command '/tmp/dual.sh' config encryption set    # rekey：旧口令失效、新口令可解
```

**argv 优先于环境变量**（实测双向）：argv 对 + env 错 → 成功；argv 错 + env 对 → 失败。
故应用侧对自己发起的每条命令都掌握口令控制权；容器 env 只负责 `restic serve restic --stdio` 那条链路。

**明文 conf 对口令命令失败是免疫的**（实测）：明文 conf + `--password-command` 指向不存在的文件 →
`config dump` exit 0、`listremotes` 正常。rclone 只在**解密时**才调用它——这让「首次启用」的时序非常宽松：
口令文件不存在期间，明文档的一切日常操作不受影响。

⇒ 因此本期**在界面上做完整管理**，不再要求部署者进 shell。

### 5.3 界面形态：一个按钮，三种语义

面板 `confStatus`（`RcloneConfigPanel.vue:226-230`）由二态扩为三态，并按「conf 加密态」决定按钮语义：

| conf 状态 | 判定 | 主按钮 | 点击后 |
| --- | --- | --- | --- |
| **未加密** | 文件头无加密标记 | 「生成并启用加密」 | 生成 32 位口令 → 落盘 `/repos/rclone.pass` → `encryption set` → `SecretReveal` 一次性展示 → 要求确认已备份 |
| **已加密可解** | 有标记 且 `check` = 0 | 「更换加密口令」 | `PasswordEntryModal` 输入新口令 → 写临时文件 → 双分支脚本 `encryption set` → 落盘新口令 → 一次性展示 |
| **已加密无法解** | 有标记 且 `check` = 1 | 「输入口令解锁」 | 输入现有口令 → 写临时文件 → `check` 验通才落盘；不通则报错且不改任何文件 |

**停用加密**（`encryption remove`）作为次要按钮只在「已加密可解」态出现，且**必须输入当前口令**——
rclone 本身就需要它，这不是额外限制。

第三态出现时，应用当前**无法读写 conf**（§4.4 面板路径 685 ms 快速失败），故该状态不只是文案：
面板顶部常驻 NAlert（复用 `:626` 现有 alert 位）说明后果与恢复路径，列表区改为空 + 该提示，不再展示半截数据。

组件全部复用现成实现，不新写：一次性明文 = `SecretReveal.vue`；口令输入 = `PasswordEntryModal.vue`。

### 5.4 锁死时的三处语义修正

「锁死」= conf 已加密 且 口令不可达。此时现状有三处会给出误导性结果：

| 位置 | 现状 | 修正 |
| --- | --- | --- |
| `RcloneProbe.list_remotes`（`rclone.py:119-178`） | 五条失败分支一律 `available=False`，`reason` 是 rclone 原文或「超时」——**完全指不到「口令缺失」** | 识别为锁死后，`reason` 改为可操作文案 + 独立布尔字段；`available` 仍为 False（探测确实没成功） |
| `RcloneConfigService.list_configs`（`rclone_config.py:402-419`） | `except RcloneConfigUnavailable` → 整体 `available=False`，面板只剩一句 warning | 同上，文案必须说明「已加密且无法解密」并给出恢复路径（§9.1） |
| `validate_repo_url`（`repos_shared.py:244-266`） | 探测不可用 → **放行**语法正确的 URL，「由 E29 连通性测试给最终判定」 | 区分两类不可用（§5.5）；锁死 → **422 拒绝**，因为这不是临时故障，用户改 URL 也救不回来 |
| 启动期 | 无任何检查 | 新增自检（§5.6） |

### 5.5 两类「探测不可用」必须分开

| 类别 | 例子 | URL 校验 |
| --- | --- | --- |
| **基础设施不可用** | 缺 rclone 二进制 / 事件循环不支持子进程 / 临时超时 / 宿主档未装 rclone | 维持**放行**（现有语义正确：E29 会兜底） |
| **配置错误** | conf 已加密但口令不可达 | **422 拒绝**（改 URL 无用，必须先修口令通道） |

这条区分是本方案唯一触碰既有放行语义的地方，必须由测试双向锁定（§8）。

### 5.6 启动自检：**不崩，但要刺眼**

先例是 `main.py:374-378`——凭据密钥源缺失时只 `logger.warning`，注释写明「**绝不阻止启动**」，理由是「口令文件就位后无需重启即可恢复」。

conf 锁死与之不同：它让**所有 `rclone:` 仓库**同时不可用，且不会自愈。但仍**不崩**——崩掉会导致容器重启循环，用户连修复界面都进不去（而界面正是唯一能告知原因的地方）。

自检行为（`main.py` lifespan，置于 `:374` 同类逻辑附近）：

| 状态 | 行为 |
| --- | --- |
| conf 不存在 / 明文 | 静默（存量部署零噪声） |
| 已加密 且 口令可用 | `logger.info` 一行（说明加密已启用，不算异常） |
| 已加密 且 口令不可达 | `logger.error` 一行，**含恢复步骤**；同时经 API 与界面常驻 NAlert 暴露 |

日志只记状态与恢复指引，**绝不记录口令或 conf 内容**（S3）。

### 5.7 `RCLONE_PASSWORD_COMMAND` 优先于 `RCLONE_CONFIG_PASS`

| 维度 | `RCLONE_CONFIG_PASS` | `RCLONE_PASSWORD_COMMAND` |
| --- | --- | --- |
| `docker inspect` 可见性 | 明文可见 | 不可见（只见脚本路径） |
| 与 secrets 组合 | 需先读 secret 再导出 env | 直接读 `/run/secrets/*` 输出 |
| 能否开启加密（`set`） | ❌ | ✅ |

故文档与 compose 示例以 `RCLONE_PASSWORD_COMMAND` 为主，`RCLONE_CONFIG_PASS` 作为兼容回退保留（两者都已在 `ENV_ALLOWED_PREFIXES` 的 `RCLONE_` 前缀内，`restic.py:59-67`；`env_sync.py:146-147` 也已把 `cred.*.rclone_*` 映射到 `RCLONE_*`，**通道侧零代码**）。

**两条通道不能同时配**（§4.9 实测：命令完全接管、env 不兜底），故 compose 里只开一条。

### 5.8 口令从哪来（一）：部署侧自管的三种形态

> **默认路径是界面生成**（§5.9）——用户点一次按钮，应用把口令写到 `<PASSFILE_ROOT>/rclone.pass`（docker 默认 `/repos`）并一次性展示。
> 本节三种形态用于**不经过界面**的部署：「已有自管凭据目录」「必须走 secrets/KMS」「需要与人共享口令」。
> 二者互斥：同时配 `RCLONE_CONFIG_PASS` 与 `RCLONE_PASSWORD_COMMAND` 时，命令完全接管（§4.9），env 不兜底。

本期**不引入** `*_FILE` 基础设施、不引入 secret provider 抽象（那是已暂缓的 #3 范围）。口令的供给完全由部署侧决定，应用只认环境变量：

| 形态 | 做法 | 适用 |
| --- | --- | --- |
| **A. 明文 env（最简）** | `RCLONE_CONFIG_PASS: "<口令>"` 写在 compose 里 | 单机自用；已知 `docker inspect` 可见 |
| **B. compose secrets（推荐）** | `secrets: rclone_pass: file: ./secrets/rclone_pass` + `RCLONE_PASSWORD_COMMAND: "/bin/cat /run/secrets/rclone_pass"` | 口令不入 compose 明文、`docker inspect` 只见命令 |
| **C. 宿主文件挂载** | 挂 `./rclone/pass:/etc/rclone/pass:ro` + `RCLONE_PASSWORD_COMMAND: "/bin/cat /etc/rclone/pass"` | 已有自管凭据目录的部署 |

> **compose 模式的 secrets 仍落盘**：它是把宿主机源文件 bind mount 进容器（非 swarm 无 tmpfs），实测确认。收益不是「不落盘」，而是「**不出现在 `docker inspect` / 镜像层 / compose 文件里**」，且权限可收紧到 `0600`。这与 Q1「部分读」威胁模型一致——要防的是 conf 被单独拷走，不是防宿主。

**走本节形态时，界面的启用按钮不可用**：界面生成的口令会写到 `<PASSFILE_ROOT>/rclone.pass`，而本节的形态让 rclone 从别处取口令——
两边不一致会让「界面显示的加密态」与「实际生效的口令」脱节。故 §5.9 规定：`RCLONE_PASSWORD_COMMAND` 已由部署侧配置时，
界面只读展示加密态，不显示启用/更换按钮（判定方式：进程 env 里的 `RCLONE_PASSWORD_COMMAND` 是否指向 `<passfile_root>/rclone.pass`）。

### 5.9 口令文件位置与来源（**v1.4 移除 entrypoint 自动生成**）

**唯一落点：`<PASSFILE_ROOT>/rclone.pass`**（默认即 `/repos/rclone.pass`，与仓库密码同卷但不同文件）。

| 生成方 | 何时 | 状态 |
| --- | --- | --- |
| **界面「生成并启用加密」** | 用户点击 | **唯一入口**（v1.4 起） |
| ~~entrypoint 启动期自动生成~~ | — | **v1.4 移除**：两处都能生成会让「谁先生效、谁覆盖谁」变成隐式竞争；且自动生成的口令用户从没见过，丢文件即永久锁死 |

> v1.2/v1.3 曾设计 `RCLONE_PASS_FILE` + `ensure_rclone_pass()` 在 entrypoint 里自动生成。
> **v1.4 已移除该设计，P-1 批次已从 `docker/entrypoint.sh` 与 `docker-compose.yml` 删除代码**（`RCLONE_PASS_FILE` / `ensure_rclone_pass` 在 `docker/` 下零命中）。
> 移除理由：界面生成路径能给出一次性明文展示（用户必然看见并备份），而后台生成的 32 位随机串用户从未见过——
> 「文件丢了就永久失守」这件事在自动生成下比在界面生成下严重得多；两处都能生成还会形成「谁先生效、谁覆盖谁」的隐式竞争。

**为什么是 `/repos` 而不是 `/data`**：`/data` 已有 `restic.db`（锁）。口令放进去等于一个目录同时握有 conf 密文 + 锁 + 口令，
与《暂缓存档-密钥托管架构》的结论直接冲突（见 §9.1）。`/repos` 是已存在的独立卷（部署指南 §2.6），复用即可。

**为什么不用 `<passfile_root>/<repo_id>/password`**（复用仓库口令）：

| | 复用 default 口令 | 独立 `repos/rclone.pass` |
| --- | --- | --- |
| 界面首次启用额外要求 | 无 | 无（自动生成） |
| `key.rotate` 轮换仓库密码 | **口令文件被 `write_text` 覆盖 → conf 永久解不开，且静默** | 不受影响 |
| `rebuild` / `purge` / 归档到期 | `write_passfile` / `remove_passfile` 同样毁掉 conf | 不受影响 |
| 一个文件三用 | 是（restic 密码 + `enc:v2:` 密钥源 + conf 口令） | 否 |

> `ops.py:925` 直接改写口令文件，**全程无 rclone 感知**（grep 零命中）；`repo_lifecycle.py:605`（rebuild 写）、
> `:215`（purge 删）、`retention.py:400`（归档到期删）同样会动它。复用等于让「轮换仓库密码」**静默**锁死 conf，
> 且延迟到下次 `rclone:` 备份才暴露、错误只显示「口令错」。

### 5.10 服务层设计：口令不进 DB、不进 argv、不进日志

新增 `backend/app/services/rclone_crypto.py`（约 130 行）：

| 方法 | 行为 |
| --- | --- |
| `status()` | 读文件头（纯文件读）+ 有口令时跑 `check` → 三态；**明文路径不调 rclone** |
| `enable(new_password: str \| None)` | 仅「未加密」可用；`None` = 后端生成 32 位；备份明文 conf → `set` → `check` → 落盘口令 → 失败回滚 |
| `rekey(old_password: str, new_password: str)` | 仅「已加密可解」可用；双分支脚本（`RCLONE_PASSWORD_CHANGE=1` 时输出新口令）→ `set` → `check` → 落盘新口令 |
| `disable(current_password: str)` | 仅「已加密可解」可用；`remove` → 校验首行变明文 → 删口令文件 |
| `unlock(password: str)` | **v1.11 回填的第五方法**：锁死态（已加密但口令不可达）输入现有口令，原子写回 `<PASSFILE_ROOT>/rclone.pass` → 重建运行期口令命令 → `status()` 验通；不通则回滚口令文件与环境变量（复用本层 `_write_pass` / `_restore_pass` / `_set_password_command_env` / `_restore_env` 原语）；路由层不再自带口令读写 helper |

**三条纪律**（都是既有先例的复用）：

1. **临时口令文件用 `tempfile.mkstemp`**（`ops.py:865` 已有先例），`chmod 600`，`try/finally` 删除；
   口令**只经该文件传给 rclone**，不进 argv、不进日志、不进审计（S3）；
2. **审计只记动作**：新增 `rclone.conf_encrypt` / `rclone.conf_rekey` / `rclone.conf_decrypt` / `rclone.conf_unlock`（v1.11 回填：unlock 与 rekey 语义不同，独立留痕，S6「审计只增不改」下误标不可修正），
   detail 只含 `generated`（是否后端生成）与 `result`，**绝不含口令**；四者入 S6 危险操作清单（v1.17 24 → 27，v1.19 再 27 → **28 项**）；
3. **写后必须重跑 `check`**：拿到 0 才算成功；非 0 立即用备份还原 conf 并抛错，**绝不留下「已加密但解不开」**。

**运行期口令通道切换**：每次 `enable`/`rekey`/`disable`/`unlock` 成功后重建 `os.environ["RCLONE_PASSWORD_COMMAND"]`，
使后续 `restic serve restic --stdio` 子进程拿到新口令（`RcloneProbe` / `RcloneConfigService` 不传 `env=`，继承即生效）。

**并发门禁**：换口令期间不得有 running job——conf 已用新口令重加密而旧 env 的 serve 进程仍活着，会让该次备份失败。
复用 `JOB_CONFLICT`（409），与既有同仓库互斥同口径。

## 6. 实现清单

| # | 文件 | 改动 | 约行数 |
| --- | --- | --- | --- |
| **已完成** | | | |
| 1 | `docker/Dockerfile` | 钉 `ARG RCLONE_VERSION=1.75.2-r0`（`f37a905`） | 0 |
| 2 | `docker/docker-compose.yml` / `docker/entrypoint.sh` | `PASSFILE_ROOT: /repos` + `./repos:/repos:rw` 改默认、entrypoint 独立密钥卷处理（`bf5d49c`） | 0 |
| **v1.4 移除（P-1 ✅ 已落地）** | | | |
| 3 | `docker/entrypoint.sh` | 删 `RCLONE_PASS_FILE` + `ensure_rclone_pass()` + 两处调用（§5.9 理由） | −36 |
| 4 | `docker/docker-compose.yml` | 删 `RCLONE_PASS_FILE` 注释行与「首次启动自动生成」说明；改为指向界面生成（§5.10） | −2 |
| **新增：口令生命周期** | | | |
| 5 | `backend/app/services/rclone_crypto.py` | `status` / `enable` / `rekey` / `disable`（§5.10）+ mkstemp 临时文件 + 备份回滚 + `check` 验通 + env 重建 | ~130 |
| 6 | `backend/app/routers/repos_rclone.py` | 4 端点：`GET /api/rclone/conf-encryption` + `POST .../enable` `.../rekey` `.../disable` | ~45 |
| 7 | `backend/app/schemas/api_schemas.py` | 3 个请求模型 + 1 个状态模型（含 `conf_encrypted` / `conf_locked` / `password_once`） | ~25 |
| 8 | `backend/app/constants.py` | `RCLONE_CONF_ENCRYPTED_MARKERS`、`RCLONE_CONF_LOCKED`、3 个审计动作常量；`ERROR_STATUS` 增 503 | ~14 |
| 9 | `backend/app/errors.py` | `RcloneConfLockedError`（503） | ~8 |
| **识别与降级** | | | |
| 10 | `backend/app/services/rclone.py` | `_conf_header()` 三态；`RcloneRemotes` 增 `conf_encrypted` + `conf_locked`；锁死分支可操作 `reason` | ~45 |
| 11 | `backend/app/services/rclone_config.py` | `RcloneConfigList` 增同名字段；`list_configs` 区分锁死 | ~18 |
| 12 | `backend/app/routers/repos_shared.py` | `validate_repo_url` 锁死 → 422（§5.5） | ~12 |
| 13 | `backend/app/main.py` | lifespan 启动自检（§5.6） | ~30 |
| **前端** | | | |
| 14 | `frontend/src/components/settings/RcloneConfigPanel.vue` | 三态徽标 + 三按钮 + 停用次要按钮 + NAlert；接 `SecretReveal` / `PasswordEntryModal` | ~110 |
| 15 | `frontend/src/api/repo.ts` + `types.ts` | 4 端点封装 + 类型 | ~36 |
| 16 | `frontend/src/i18n/locales/zh-CN.ts` / `en-US.ts` | 三态 + 三按钮 + 停用确认 + 恢复指引 | ~24 |
| 17 | `frontend/src/api/openapi.json` + `schema.d.ts` | 生成物：`scripts/gen-openapi.py` → `pnpm -C frontend gen:api` | 0（自动） |
| **测试与文档** | | | |
| 18 | `backend/tests/test_rclone_crypto.py`（新）+ 三个既有测试文件 | 见 §8（含 12 条口令生命周期） | ~330 |
| 19 | `docs/部署指南.md` | 改写加密段、env 表、§2.6 交叉引用 | ~50 |
| 20 | `docs/架构设计文档.md` | §5.0 增错误码行；§5.1 增 4 端点 | ~20 |
| 21 | `docs/代码审查规范.md` §3.1 + `AGENTS.md` | S6 危险清单 24 → 27（**先改规范再改代码**） | ~10 |

**生产代码 ≈ 310 行**（不含生成物）；**测试 ≈ 330 行；文档 ≈ 80 行**；手改文件 **18 个**；净删除 ≈ 32 行（entrypoint/compose）。

## 7. 实施批次

| 批次 | 内容 | 前置 | 验收 |
| --- | --- | --- | --- |
| P0 ✅ | Dockerfile 钉 rclone 版本（独立于本票） | — | `apk policy rclone` = `1.75.2-r0` |
| **P-1 ✅ 已完成** | **移除 entrypoint 自动生成**（§5.9）：删 `RCLONE_PASS_FILE` + `ensure_rclone_pass()` + 两处调用，compose 去注释行，部署指南 §4.2 改写 | — | ✅ `dash -n`/`sh -n` 通过；`docker/` 下 `RCLONE_PASS_FILE` 零命中；5 场景行为测试全绿（含「显式设废弃变量也不再生成文件」） |
| P1 ✅ | 常量（3 审计动作）+ 错误码 + 架构 §5.0 + 规范 §3.1 S6（24→27） | P-1 | ✅ `verify_redlines.sh` 19/19 |
| P2 ✅ | `rclone_crypto.py` 服务层四方法 + 备份回滚 + env 重建 | P1 | ✅ 真实 rclone 1.75.2 端到端 |
| P3 ✅ | 识别层三态 + 两个 dataclass 字段 + 锁死分支（§5.4） | P1 | ✅ 单测三态 × 锁死文案 |
| P4 ✅ | 启动自检（§5.6）+ **重启后 env 重建** | P3 | ✅ 日志实证「已重建 RCLONE_PASSWORD_COMMAND」 |
| P5 ✅ | **5** 端点（含 E68 unlock）+ schemas + openapi 生成 | P2 | ✅ 73 paths / 112 schemas |
| P6 ✅ | 前端口令区块（三态 + 按钮 + SecretReveal/PasswordEntryModal/ConfirmGate）+ i18n | P5 | ✅ typecheck 0 错，panel spec 22/22 |
| P7 ✅ | `validate_repo_url` 收窄（§5.5） | P3 | ✅ 双向测试 |
| P8 ✅ | 文档收口：部署指南 §4.2 + 架构 §5.0/§5.1（E64–E68）+ 本文回填 | 全部 | ✅ 真机闭环 |
| **FIX-SPACE ✅** | **实施中发现**：口令命令在含空格路径下失效 → 改无空格脚本路径 | 全部 | ✅ 真实 rclone 端到端 |

**P-1 是破坏性变更**：已按 v1.3 部署并让 entrypoint 生成过口令的环境，其 `/repos/rclone.pass` 仍在——
升级后界面会识别为「口令文件已存在」，可直接用「更换加密口令」接管，不会失联。

P2 是技术核心；P3/P4/P7 一条线，P5/P6 一条线，两条可并行。

## 8. 测试与护栏

**识别层**：

- **文件头三态**：已加密（两种标记各一例）/ 明文 / 文件不存在 → `conf_encrypted` 与 `conf_locked` 取值正确，且**明文路径不调用任何子进程**（executor 计数器断言）；
- **锁死不静默降级**：锁死时 `reason` 必须含「已加密」「口令」两个语义关键词，不得等于 rclone 原文或「超时」；
- **两类不可用分离**（§5.5 双向）：锁死 → `validate_repo_url` 抛 422；缺二进制 / 事件循环不支持 → 继续放行；
- **启动自检三分支**：明文静默 / 已加密+口令可用 → 恰一条 info / 已加密+不可达 → 恰一条 error 且**不含口令与 conf 内容**（S3 断言）；

**口令生命周期（v1.4 新增，约 12 条）**：

- `enable` 成功路径：conf 变加密态、口令文件落盘 0600、`check` 返回 0、一次命令只生成一个备份；
- `enable` **幂等/门禁**：conf 已加密时调用 `enable` → 422，**且不改动任何文件**（md5 前后一致）；
- `enable` **回滚**：`set` 成功但 `check` 非 0（构造口令写坏）→ conf 从备份还原 + 抛错 + 口令文件不落盘；
- `rekey` 成功：旧口令解不开、新口令可解（用真实 rclone 断言，不 mock）；
- `rekey` **旧口令错** → 422 且 conf md5 不变、旧口令仍可解；
- `rekey` **有 running job** → 409 `JOB_CONFLICT`，零副作用；
- `disable` 成功：conf 首行变明文、口令文件被删；
- `disable` **口令错** → 422，conf 仍加密且口令文件保留；
- **三处零泄漏**（S3）：口令不出现在 argv（用 executor 拦截 argv 断言）、日志、审计 detail、API 响应（除 `enable`/`rekey` 的**一次性** `password` 字段）；
- **env 重建**：`enable` 后 `os.environ["RCLONE_PASSWORD_COMMAND"]` 指向新口令文件（读 env 断言，无副作用）；
- **临时文件清理**：三个方法成功与失败路径下 `mkstemp` 产生的文件都被删除（遍历 tmpdir 断言）；
- **端点契约**：**5 端点**（v1.11 回填：E64 status / E65 enable / E66 rekey / E67 disable / E68 unlock）× 鉴权 401 / Origin 403 / 锁死 503 / 门禁 422 / 并发 409；unlock 不受 `conf_locked` 门禁限制。

**既有护栏**：`verify_redlines.sh` 全绿（含 S6 **四动作 / 28 项**的反向锚点，v1.19 补 `rclone.conf_unlock`）；`check_dockerignore.py`（compose 改动后）。

**真机验收**（容器内）：明文 → 界面「生成并启用加密」→ 一次性明文可复制 → 重启容器后仍可解 →
「更换加密口令」→ 旧口令失效 → 「停用加密」→ conf 首行变明文 → 全流程 restic `rclone:` 备份各跑一次。

## 9. 风险与取舍

### 9.1 口令丢失 = 全部远端不可恢复（唯一实质代价）

不可逆、无后门（§4.11 实测）。**v1.4 起界面握有致锁能力，故缓解措施从前置纪律变成产品行为**：

| 缓解 | 落点 |
| --- | --- |
| **启用前备份明文 conf（可选，默认开）** | **只有** `enable()` 且在 `backup=True` 时复制到 **`<passfile_root>/conf-backup.conf`**（单一滚动文件，0600，**不在 conf 同目录**——否则明文副本会让加密失效，见 v1.7）。用户可在界面关掉，代价是放弃唯一恢复路径；**回滚不依赖它**（`enable()` 用内存里的操作前字节），故关掉不会让失败启用留下半截 conf（v1.9）。`rekey()` 不写备份：备份的是旧密文，旧口令一换就解不开，用内存快照回滚即可（v1.8）。**开关状态持久化**（v1.10）：存 config 键 `ui.rclone_conf_backup`（`"1"`/`"0"`，缺省 1），挂载读回、拨动写回，换浏览器与重开页面都记住 |
| **口令一次性展示 + 强制确认** | `SecretReveal.vue`，用户点「我已备份」才算完成（沿用 E44 / B9 的既有心智） |
| **每次操作后 `check` 验通** | 非 0 立即用备份还原 conf 并抛错，**绝不留下「已加密但解不开」** |
| **锁死时面板仍可打开** | §5.4 的三处语义修正 + §5.6 启动自检 + §5.3 第三态的「输入口令解锁」按钮 |
| **口令不进 `/data`** | 落 `/repos`（独立卷），避免「拷走 `/data` = 拿到锁与钥匙」 |

⇒ 界面致锁风险**无法降到零**，只能靠上述五条把「误操作 → 不可恢复」压缩成「误操作 → 一次点击内可回滚 + 有明文备份」。
这是与 v1.2「零致锁入口」的实质差别，必须写进验收。

**逃生通道（写进界面文案）**：`<passfile_root>/conf-backup.conf` 里的明文 conf 可直接覆盖回 `/etc/rclone/rclone.conf`，
覆盖后加密态消失、口令文件可删——这是口令彻底丢失时**唯一**的恢复路径。

> **开关的语义边界（v1.9）**：这个开关关的是「留不留明文副本」，不关「失败能不能回滚」
> ——回滚用内存字节，与开关无关。所以它是**一个纯粹的风险权衡**：留着，多一份明文驻留（位置在口令卷、
> 与 conf 分卷，需同时拿到两卷才能利用）；不留，加密面最干净，但口令丢失即永久不可解。默认留。
>
> **偏好的持久化（v1.10）**：开关值存 config 键 `ui.rclone_conf_backup`（服务端单值，缺省 `"1"`）。
> 它记的是**用户的风险取舍**，不是 conf 的加密态——加密态仍由文件头自证（§5.2），二者互不替代：
> 偏好读失败只让开关回落默认开，绝不改变面板显示的三态。

> **只在「启用」这一步存在（v1.8）**：换口令不会刷新它，因为那时 conf 已是密文。
> 也就是说：**启用加密前 conf 必须是可用的明文**，那一刻的备份才是有用的逃生通道；
> 事后换了口令，这份备份仍是「启用当时的明文」，照样能覆盖回去——这正是它该有的语义。

### 9.2 已接受的风险

| 风险 | 处置 |
| --- | --- |
| 整盘读下加密无效（口令必须常驻进程环境） | **已知并接受**（Q1 裁决，部分读模型）；文档明写边界，不得宣称「防整盘泄露」 |
| 备份任务路径在口令缺失时挂到超时（§4.4） | 由启动自检在**故障发生前**暴露；任务失败原因仍可能指向超时，故自检日志是主要防线 |
| 宿主机侧编辑流程改变（加密后宿主 `rclone config` 也需先给口令） | 部署指南 `:315-317` 改写为该事实 |
| 换口令需脚本区分 `RCLONE_PASSWORD_CHANGE` | 若脚本两种调用都返回同一口令，rekey 会「成功但密码未变」（§4.6）。**用 §5.9 的自动生成时不需要任何脚本**——口令文件内容就是新口令，`encryption set` 会拿它当新旧同一把钥匙，换口令须手工换文件内容（见 §4.6） |
| 存量明文 conf 不自动迁移 | Q3 裁决；界面识别为「未加密」并静默，不打扰 |

## 10. 本期不做

| 项 | 理由 |
| --- | --- |
| ~~界面「启用/停用加密」按钮~~ | **v1.4 已做**（§5.2/§5.3）——v1.2 的「零致锁入口」结论被实测推翻（argv `--password-command` 可用） |
| ~~界面录入 / 生成 / 轮换口令~~ | **v1.4 已做**（生成、轮换、停用、解锁四项都在界面上）。仍然不做的是**把口令存进 DB**——与「业务配置走 DB」边界冲突，且会让 `/data` 同时握有锁与钥匙 |
| 存量 conf 自动迁移 | Q3 裁决：不自动加密已有明文 conf，由用户点按钮显式启用 |
| 接 KMS | 本期口令只落文件；KMS 由 `RCLONE_PASSWORD_COMMAND` 指向任意脚本天然支持（§5.8 形态 B/C），不改应用代码 |
| 口令强度校验 | rclone 不校验（§4.8 单字符也接受）；界面生成恒 32 位，自填不拦（用户可能沿用外部口令）。**v1.11 终态处置：已知并接受、明确不做**——产品裁决保留「自填不拦」，理由：① 自填用于沿用外部既有口令，强加复杂度策略会把合法存量口令挡在门外；② 默认路径（界面生成）恒为 32 位随机口令，弱口令只可能来自用户主动自填；③ rclone 自身不校验，应用层补校验是重复且无实益的拦截。不再作为未决缺口，随本文归档冻结 |

> **已移除（v1.4 设计 / P-1 代码落地）**：entrypoint 启动期自动生成口令（`RCLONE_PASS_FILE` / `ensure_rclone_pass()`，见 §5.9）。
> 两处都能生成会形成隐式竞争，且后台生成的口令用户从未见过——丢文件即永久锁死。

## 11. 与既有文档的关系

| 文档 | 关系 |
| --- | --- |
| 《rclone配置生成工具设计方案》v1.21 | **不替代**：那边管「段的增删改查与凭据混淆」，本文管「整份文件的落盘加密与口令通道」。交叉点只有一处——加密后写 conf 仍走同一 CLI，故那边无需改动 |
| 《暂缓存档-密钥托管架构-2026-11-04.md》 | 前置评估已收敛为「暂不实施」；本文不依赖它，`RCLONE_PASSWORD_COMMAND` 使口令来源与 provider 抽象解耦 |
| 《部署指南》§4.2 | **P-1 已改写**为「本版本 conf 加密未启用、不做启动期自动生成、勿配 `RCLONE_PASS_FILE`」；界面操作说明 **P8 已回填**（v1.11 订正：§7 P8 状态为 ✅，原「待 P8 回填」为过时自述） |
| 《架构设计文档》§5.0 / §5.1 | 错误码（`RCLONE_CONF_LOCKED` 503 / `RCLONE_UNAVAILABLE` 503）与 **5 端点 E64–E68** 的新契约字段（v1.11 订正：原「两个端点」为过时自述；E68 审计已改为独立 `rclone.conf_unlock`） |
| Git Issue #2 | 本文为实现依据；落地完成并通过 §8 验收后按其 Q5 关闭——**v1.11：2026-11-06 已关闭**（`gh issue close 2`，端点契约已上位化至《架构设计文档》§5.1 E64–E68 / §5.0） |

## 12. 待验证（归档前终态处置，2026-11-06）

> **v1.11 收口**：本节原为「实现前须补测」清单。前 4 项已 ✅ 销账；后 3 项 ⏳ 在归档前逐条裁决，**全部转为「明确不做 / 已接受」终态**，不再留未决项（归档文档不再演进）。裁决依据：① WSL 官方 rclone 1.75.2 的真实端到端验证（§4.3 三态退出码、`--password-command` 三路径、argv 优先级、含空格路径）已覆盖应用真正依赖的行为面；② Alpine 打包版 `1.75.2-r0` 与官方 1.75.2 同源，差异属**部署形态**而非应用契约；③ 本会话环境 `docker` 不在 PATH、WSL 亦不可用，**无法实跑真容器**——如实登记「本环境无法验证」，不谎称已验证；④ 若未来某部署形态确需显式 argv，属**新需求**，另起 issue/方案，不在已归档方案里悬置。

| 项 | 状态 |
| --- | --- |
| 非交互调用下 env 是否足够、是否强制 TTY | ✅ 已销（§4.2） |
| 加密后 `serve restic --stdio` 是否受影响 | ✅ 已销（§4.2） |
| conf 首次创建默认权限 | ✅ 已销（恒 600，与 umask 无关；已写入部署指南 `:322-325`） |
| 加密态如何探测 | ✅ 已销（§4.3 / §5.1） |
| **容器内（Alpine rclone 1.75.2-r0）复跑 §4.3 三态退出码** | ⛔ **明确不做（已接受）**——WSL 官方 1.75.2 已验三态退出码；Alpine 打包版同源，且本会话 `docker` 不在 PATH 无法起真容器。真容器复跑属部署验收（由部署方按《部署指南》§4.2 自行确认），不构成本方案未决项 |
| **argv `--password-command` 三条路径在 Alpine 版复验**（§5.2 依据） | ⛔ **明确不做（已接受）**——同上；WSL 官方 1.75.2 已验证 `set`/`check`/rekey 三态与 argv 优先级，应用侧契约以 §5.10「不传 `env=`、子进程继承」为准，与 rclone 发行版无关 |
| **probe 与 ConfigService 是否需要显式传 argv** | ⛔ **裁定维持现状（不改为显式传 argv）**——§5.10 的「重建 `os.environ` 让子进程继承」路线已由 WSL 真实 rclone 端到端验证（`RcloneProbe` / `RcloneConfigService` 不传 `env=` 即生效）；显式传 argv 会与「口令不进 argv」（S3）冲突，故**明确不做**。若未来容器继承不可靠，另起新方案评估，不在本文悬置 |
