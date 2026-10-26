# rclone 配置生成工具设计方案

> 版本：v1.15（2026-10-27）
> 层级：方案层——rclone 远端配置管理（查询/生成/编辑/删除，Crypt / WebDAV）的唯一方案源
> 关联：`app/services/rclone.py`（只读探测，Q17 档位）、架构设计文档 R-022（rclone 生命周期）、代码审查规范 §3.1（S6 危险操作清单）、部署指南（rclone.conf 挂载形态变更）

> **修订记录**：
> - v1.15（2026-10-27）：**全站「自动生成密码」统一（B9，owner 两点指令）**。①**生成器合并为唯一实现**：`utils/password.ts` 的 `generatePassword()` 成为全站唯一生成器（32 位、四类字符剔除易混 `0O1lI`、每类至少一个 + Fisher-Yates + 拒绝采样），删除 `passwordStrength.generateStrongPassword()` 与 `REPO_PASSWORD_LENGTH`（两套并存导致同系统内 24/32 位不一致）；后端同步 32 位——`constants.REPO_PASSWORD_LENGTH 24 → 32`（仓库密码）、`auth/password.py` 的 `RANDOM_PASSWORD_LENGTH 24 → 32`（首启管理密码，字符集仍为字母+数字，因为要打印到日志供抄录）。②**一次性展示统一为「仓库设置密码那种方式」**：抽出 `components/SecretReveal.vue`（alert + 等宽只读明文 + 复制 + 返回/确认）作为唯一呈现，`PasswordEntryModal` 生成态改用它；**rclone 三处密码生成与导出口令生成改为「弹窗一次性展示 → 确认后才写入」**（旧行为是直接把明文塞进掩码框，用户既看不到也复制不了——crypt 密码与导出口令丢失都不可逆）。③**按钮文案统一**：唯一键 `common.generatePassword` =「生成密码」，删除 `repo.passwordGenerate` / `settings.rclone.generatePassword` / `settings.transfer.generatePassword` 三个分散键；复制提示上提 `common.copy*`。测试：`SecretReveal.spec.ts`（新增 6 例，锁死复制/确认/返回语义）、`password.spec.ts` 扩到 10 例（32 位 / 剔除易混 / 每类至少一个 / 强度恒 4 档）、面板生成用例改为「弹窗 → 确认 → 写入」。
> - v1.14（2026-10-27）：**线上缺陷修复 + 密码框全项目统一（B8，owner 实测三项反馈）**。①**「新增 crypt 填好却提示参数校验失败」根因 = 前端双重序列化**：`api/repo.ts` 的 `createRcloneConfig` / `updateRcloneConfig` 写成 `body: JSON.stringify(payload)`，而 `http.request` 内部已统一 `JSON.stringify(body)` → 实际发出的是**字符串字面量** `"{\"type\":…}"`，后端 pydantic 期望对象 → `RequestValidationError` → 422「参数校验失败」。修法：`body` 直接传对象。**既有测试为何漏掉**：`api/__tests__/*.spec.ts` 一贯 mock 掉 `@/api/http`、断言传给 `request` 的原始对象，序列化层从未被执行 → 新增 `api/__tests__/rclone-config.spec.ts`（**不 mock http，只 stub `global.fetch`**，断言真实 body 解析后是对象）。②**密码框太短 / 各处不一致**：项目内 18 处各自写 `n-input type="password"`，`n-form-item` 里被拉满整行、`n-space` 里塌缩到几十像素（32 位密码看不到几位）→ 抽出 **`components/PasswordInput.vue`**（唯一实现：`show-password-on="click"` + 可配宽度 `width`（默认 320px）+ 可选「生成」按钮 + `$attrs` 透传 maxlength/keyup.enter），**全项目 18 处全部迁移**（登录、bootstrap 三步改密、仪表盘导出、通知邮件密码、设置页 7 处、仓库页凭据、仓库密码弹窗、rclone 3 处），并加护栏「除组件自身外 src 下不得出现裸 password 输入」；rclone 三个密码栏宽 400px（32 位密码完整可见）。③**远端名栏过长**：由撑满整行收口为 320px（`.rclone-panel__field`）。另：生成密码后 `message.success` 提示「点击眼睛图标查看明文」（掩码态下不提示会被当成没反应）。
> - v1.13（2026-10-27）：**B7 落地（owner 裁决：抽共用组件）**——新增 `components/DirPickerInline.vue`（容器视角内联双栏：`PathBreadcrumb` + `VirtualTree`(E33) + 路径值输入框 + 手输），把仓库页 local 行的内联实现**抽出复用**（repo 页删 ~66 行脚本 + 52 行样式 + 3 个 import，S-4 单一实现）。rclone crypt 表单升级为**双模式**：① 远端（NSelect 在册远端 + 远端内路径）② 本地目录（该组件，容器视角）。组件内固化三条纪律：挂载点独立预取（E33 只在根请求回 `mounts`）、点目录名不移动树、**只对外部赋值重定位**（自 emit 回流不重定位，防树跳）。测试：DirPickerInline 7 例 + 面板 +2 例（本地模式提交绝对路径 / 非绝对路径拦截）。
> - v1.12（2026-10-27）：**owner 复盘纠正「crypt 不能填本地路径」的错误结论**——查 rclone 官方文档 §Specifying the remote：`You may also specify a local filesystem path, such as /path/to/dir on Linux, C:\path\to\dir on Windows`，即 crypt **可以**包裹本地目录做纯本地加密；原实现（`^[A-Za-z0-9_-]+:.+$` 强制冒号 + 强制在册）**过严**，属缺陷。落地 **B6**：①新增 `classify_crypt_remote()` 三态分类（远端指针 / **本地绝对路径放行** / 相对路径拒绝），并修正 Windows 盘符 `C:/x`、`C:\x` 被误判成名为 `C` 的远端；②`update()` 补底层在册校验（此前只有 create 校验，编辑可存进不在册远端）；③crypt 表单改「NSelect 选在册远端 + 路径输入框」（在册校验前移），本地路径 UI 入口**押后**（B7）。单测 → **63 例**；裁决 10/11/12 入档。
> - v1.11（2026-10-27）：**B4 文档同步完成 + B5 验证收口**——架构设计文档升 **v1.10**（新增 E48–E51 四个端点条目 + E45 Q17 放宽注 + §3 目录树 `rclone.py`/`rclone_config.py` + §11 追溯矩阵 R-022 行）、部署指南 2026-10-27 校准（§1 仓库类型更正 + §4.2 目录挂载迁移指引与「界面配置管理」能力矩阵）、PRD 升 **v1.14**（需求池 R-022 行）。验证：前端 typecheck 0 错、vitest **458 passed / 47 文件**、build exit 0；后端 pytest 全量与红线待 B5 收口记录。
>   顺带修正：`RcloneConfigPanel.spec.ts` 的快照夹具缺显式类型标注（两段形状不同 → TS 推断出带 `key?: undefined` 的联合类型，与 `RcloneConfigItem[]` 不兼容，typecheck 报 TS2345）——加 `: RcloneConfigsOut` 标注。
> - v1.10（2026-10-27）：**owner 指令：Crypt `filename_encoding` 默认值 base32 → `base64`**（文件名更短、适配大小写敏感远端；base32/base32768 仍在白名单可选）。改动落点：后端 `CryptGenerator.defaults`、前端表单默认与编辑态回落、相关单测与本文 §1.1 裁决 4 / §3.1 字段表 / 示例。
> - v1.9（2026-10-27）：**B3 完成**——①compose 改**目录挂载** `./rclone:/etc/rclone:rw`（注释写入 A/B 实证与迁移指引）；②前端 `utils/password.ts`（Web Crypto 拒绝采样，32 位）+ 6 例单测；③api 层 4 方法 + 4 个类型 + i18n 双 locale 同步键；④`components/settings/RcloneConfigPanel.vue`（列表 + 表单 + 编辑态 + 删除确认门 + 生成密码 + 部署形态提示）与设置页新分区 `rclone`（插在 credential 之后）+ 6 例组件测。
> - v1.8（2026-10-26）：**B2 完成**——四端点落地（`GET /api/rclone/configs`、`POST|PUT|DELETE /api/rclone/config/{remote_name}`，`_PROTECTED` 鉴权 + Origin 校验 + 审计 ×3 + 状态码映射 422/404/409/503）；配套新增错误码 `RCLONE_UNAVAILABLE`（503）与 `errors.RcloneUnavailableError`、四个请求/响应模型、`main.py` 装配 `app.state.rclone_config_service`。端点测试 `tests/test_api/test_rclone_config_api.py` **11 例全过**（401/403/查询零凭据/审计 ×3/409/404/422/503/OAuth 段可删不可编辑）。
> - v1.7（2026-10-26）：**B1 真机 spike 完成（WSL 真机 rclone v1.60.1）**，据实测修正三处实现缺陷并固化结论，见新增 **§13 真机 spike 实证记录**（A/B 挂载形态对照、七道护栏语义、两处实现缺陷：`--config` 缺失 / 超时复用探测值）。
> - v1.6（2026-10-26）：**B1 服务层完成**——`app/services/rclone_config.py` 落地（`ConfigGenerator` 注册表 ×2 = crypt/webdav + CLI 编排 create/update/delete/dump + managed 过滤 + 实例级写锁 + 凭据脱敏 + 503/422/409/404 语义）；`app/services/rclone.py` 抽出 `exec_rclone`（stdin=DEVNULL 防交互挂起）与 `resolve_conf_path` 供探测层与配置层共用（避免第二份子进程/路径实现）。单测 `tests/test_rclone_config.py` **46 例全过**；ruff 全绿、`verify_redlines.sh` 19 项全绿、后端全量 **1113 passed**。
> - v1.5（2026-10-26）：新增 **§9.1 部署形态适配（docker / host）**——明确「**一套逻辑 + 三处形态感知**」：写入路径统一走 rclone CLI（主机档天然无 rename/EBUSY 约束，容器档靠目录挂载），差异只落在①conf 路径派生（`settings.rclone_config` 或 HOME 默认，复用探测层逻辑）②权限类错误文案③rclone 二进制可用性降级；形态判断**不参与写入分支**。compose 目录挂载变更仅影响容器档，主机档零部署动作。
> - v1.4（2026-10-26）：**B0 已完成**——规范 v1.14 落地，S6 危险操作清单按实际动作名**重计为 20 项**（17 既有枚举 + 本批 3 项）；连带修正历史计数漂移（原「16 项」与枚举不符——`config.export`/`config.import`、`copy.run`/`key.rotate` 曾并组计数，此后以枚举为准）。同批落地：`constants.py` 三个 `AUDIT_RCLONE_CONFIG_*` 常量、AGENTS.md S6 索引同步、`verify_redlines.sh` 新增两条机器断言（常量反向锚点 + rclone conf 写入 CLI 唯一实现点收敛于 `services/rclone_config.py`）。校验结果：`verify_redlines.sh` **19 项全通过**。
> - v1.3（2026-10-26）：**owner 裁决 8/9：能力边界放宽**——①删除放宽到**全部段**（CLI 路径本就全段一致；OAuth 段删除加重提示「删后需重新授权」，确认门 + 审计留痕控风险，风险模型从「不可触碰」变「知情确认」）；②编辑**随注册表扩展**（一期仅 crypt/webdav；二期 s3/b2/sftp 进 GENERATORS 即自然获得字段级编辑表单，不做自由改键口；OAuth 类型永不进注册表——无授权流支撑，编辑无意义）。managed 语义收窄为「可编辑」；查询展示所有段。
> - v1.2（2026-10-26）：**owner 裁决 7：写入路径选方案 A——rclone CLI + compose 目录挂载**。查证依据：官方文档明确 rclone 保存 conf =「临时文件 + 双 rename 原子替换」，在单文件 bind mount 上 rename 报 EBUSY——CLI 改 conf 与 OAuth token 回写在现形态下都撞墙。落地：compose 改挂 `./rclone:/etc/rclone:rw`（目录），conf 增删改全走官方 `rclone config create/update/delete --obscure`，查询走 `rclone config dump`（JSON）；自研 INI 手术器、`rclone obscure` 子进程、`.bak` 兜底全部取消，服务层瘦身为「CLI 编排 + managed 过滤 + 审计」；连带修复单文件挂载下 OAuth 回写隐患（B4 部署指南登记迁移指引）。
> - v1.1（2026-10-26）：按 owner 追加需求修订——①Crypt 新增 `filename_encoding` 参数（base32/base64/base32768 白名单）；②前端 32 位随机密码自动生成（Web Crypto 客户端生成）；③能力升级为 CRUD：查询/编辑/删除；S6 审计动作扩为 3 个（16→18）；编辑/删除仅限本工具管理的类型（Q17 边界保护）。
> - v1.0（2026-10-26）：立项。owner 三项裁决随文记录（见 §1.1）；第一阶段 Crypt + WebDAV，后端 + 设置页前端一次做完。

---

## 1. 立项背景与 owner 裁决

### 1.1 裁决记录（2026-10-26）

| # | 议题 | 裁决 |
|---|---|---|
| 1 | 前端入口范围 | **一次做完**（后端 API + 设置页前端同批交付），入口放**设置页** |
| 2 | 审计清单扩容（S6 危险操作清单） | **先按规范修订**：先改《代码审查规范》§3.1，再同步 AGENTS.md 编号索引，最后实现（**B0 已落地，规范 v1.14**；按实际动作名重计为共 **20 项**——原「16 项」系并组计数漂移，本次一并修正） |
| 3 | conf 文件不存在时的行为 | **直接创建新文件**（`rclone config create` 对不存在的 conf 自动落盘创建，语义自然满足） |
| 4 | Crypt 参数扩展 | 纳入 `filename_encoding`（`base32`/`base64`/`base32768` 白名单，默认 ~~`base32`~~ → **`base64`**（owner 2026-10-27 改默认：文件名更短、适配大小写敏感远端） |
| 5 | 密码输入辅助 | 密码字段旁提供 **32 位随机密码自动生成**按钮（前端 Web Crypto 实现） |
| 6 | 管理能力 | 增加**查询已有配置、编辑、删除**功能（仅限本工具管理的 Crypt/WebDAV 段，见 §5.1） |
| 7 | conf 写入路径（v1.2） | **方案 A：rclone CLI + compose 目录挂载**。conf 增删改全走官方 `rclone config` 命令；compose 由单文件挂载改为**目录挂载** `./rclone:/etc/rclone:rw`（查证：rclone 保存 = 临时文件 + 双 rename，单文件挂载上 rename 报 EBUSY，目录挂载则正常） |
| 8 | 删除能力范围（v1.3） | **放宽到全部段**：CLI 路径对全段一致，清理废弃远端（含 OAuth 网盘）是真实需求；OAuth 段删除时加重提示（删后需重新授权），确认门 + 审计留痕控风险 |
| 9 | 编辑能力范围（v1.3） | **随注册表扩展**：一期仅 crypt/webdav；二期 s3/b2/sftp 进 GENERATORS 即自然获得字段级编辑表单（不做自由改键口）；OAuth 类型**永不进注册表**（无授权流支撑，编辑无意义） |
| 10 | crypt 底层目标口径（v1.12，owner 指出原实现过严） | **三态分类**，依据 rclone 官方文档 §Specifying the remote：① `remote:path`/`remote:bucket` → 远端名须在册；② **本地绝对路径**（`/repo/x`、`C:/x`、`C:\x`、`\\srv\share`）→ **放行**（官方支持纯本地加密）；③ 无冒号相对路径 → **拒绝**（rclone 按进程 cwd 下子目录处理，容器内不可控，属静默错误）。**Windows 盘符必须判 local**，否则被误当成名为 `C` 的远端 |
| 11 | 编辑的在册校验（v1.12） | `update()` **补**底层在册校验——此前只有 `create` 校验，编辑把 remote 改成不在册的也能存进去（保存成功、用时报错）。与 create 同口径，local 形态跳过 |
| 12 | 底层远端输入形态（v1.12/v1.13） | 两模式：①**远端** = NSelect 选在册远端 + 路径输入框（在册校验前移为「选不到就填不了」）；②**本地目录** = 容器视角目录树选择（E33 + VirtualTree + PathBreadcrumb，**从仓库页 local 行抽出的共用组件** `DirPickerInline.vue`，S-4 单一实现）。两模式互斥，提交时本地模式直接取绝对路径 |
| 13 | 密码框与字段宽度（v1.14，B8） | 抽出 `PasswordInput.vue` 作为**项目唯一密码输入实现**（宽度可配、明文切换、可选生成按钮），全项目 18 处迁移；rclone 表单远端名收口 320px、密码三栏 400px。纪律：新密码框一律用该组件，不得再写裸 `n-input type="password"`（护栏断言） |
| 14 | 密码「生成」与一次性展示统一（v1.15，B9） | 全站一套：唯一生成器 `utils/password.ts`（**32 位**）、唯一呈现 `SecretReveal.vue`（弹窗一次性明文 + 复制 + 确认）、唯一按钮文案 `common.generatePassword`（「生成密码」）。后端 `REPO_PASSWORD_LENGTH` / `RANDOM_PASSWORD_LENGTH` 同步 32 |

### 1.2 背景：对 Q17 档位的主动放宽

现状（`app/services/rclone.py` 模块 docstring，Q17 档位）：conf 由宿主机 `rclone config` 生成后挂载进容器，UI **不生成、不编辑、不展示凭据**——理由是网盘 OAuth token 属敏感回写面。

本工具对 Q17 档位做**有边界的放宽**：

- **放宽的部分**：对 Crypt / WebDAV 这类**凭据由用户自填、无 OAuth 流**的类型，允许在 UI 生成配置段并增删改 conf；
- **不变的部分**：OAuth 网盘类型（drive / onedrive 等）**不进**本工具——其配置段对编辑/删除 API **不可见**（§5.1 边界）；conf 凭据值**永不回显**（API 响应与日志零凭据，S3 口径不变）。

此放宽须在《架构设计文档》R-022 段与部署指南 compose 注释处同步登记（实施批次 B4）。

### 1.3 关键工程事实（现状核实 + v1.2 查证）

| # | 事实 | 对设计的影响 |
|---|---|---|
| 1 | **rclone 保存 conf = 临时文件 + 双 rename 原子替换**（官方文档：先写临时文件 → 旧文件 rename 为备份 → 新文件 rename 到正式名 → 删备份） | 单文件 bind mount 上 rename 报 EBUSY → **CLI 改 conf 与 OAuth token 回写在单文件挂载下都不可行** → compose 必须改目录挂载（裁决 7） |
| 2 | conf 路径来源 | `settings.rclone_config`（`RCLONE_CONFIG` env）；目录挂载后路径不变（`/etc/rclone/rclone.conf`），`RCLONE_CONFIG` 无需改动 |
| 3 | 应用是 asyncio 单进程 | 模块级 `asyncio.Lock` 串行化全部写操作（增/改/删共用一把锁，防与 OAuth 回写/宿主机编辑交叠） |
| 4 | rclone 二进制容器内必有 | `docker/Dockerfile:50` apk 安装；CLI 编排可依赖；不可用时明确报错（503） |
| 5 | 现有探测能力 | `RcloneProbe.listremotes()` + `GET /api/rclone/remotes` 复用做写后验证 |
| 6 | `rclone config create/update` 支持 `key=value` 参数形式 + `--obscure` 标志（v1.56+，2021-10 起；Alpine stable rclone 远新于该版本） | 密码混淆内置，**无需自调 `rclone obscure`**；参数直接传值 |
| 7 | `rclone config dump` 输出全量 conf 的 JSON（键值保持混淆形态） | 查询走 JSON 解析（比自研正则解析稳） |

---

## 2. 整体结构

```
backend/app/
├─ services/rclone_config.py       # 新增：CLI 编排（create/update/delete/dump）+ managed 过滤 + 审计细节
├─ routers/repos.py                # 追加 4 个端点（rclone 相关端点保持同处，已有 /rclone/remotes）
├─ constants.py                    # 新增 AUDIT_RCLONE_CONFIG_APPEND / _EDIT / _DELETE 常量
└─ schemas/api_schemas.py          # 新增请求/响应模型

frontend/src/
├─ pages/settings/index.vue        # SECTIONS 新增 'rclone' 分区（单面板 v-if）
├─ components/settings/RcloneConfigPanel.vue  # 新增分区面板组件（列表 + 表单）
├─ utils/password.ts               # 32 位随机密码生成（Web Crypto）
└─ locales/*.ts                    # 中英双 locale 同步键（i18n-parity 护栏）
```

**核心设计：注册表模式 + CLI 编排（可扩展性的关键）**

```python
# 每个类型 = 一个生成器（字段 schema + 校验），新增类型只注册、不改编排层
GENERATORS: dict[str, ConfigGenerator] = {
    "crypt": CryptGenerator(),
    "webdav": WebDAVGenerator(),
}
# ConfigGenerator 统一接口：
#   name: str                              # 类型标识（= 请求体 type）
#   build_args(params, obscure: bool) -> list[str]
#       # 校验 + 规范化，产出 rclone config create/update 的 key=value 参数序列
#       # obscure=False 表示用户提交的已是混淆值（命令整体不加 --obscure）
#   credential_keys: frozenset[str]        # 凭据键（查询时以 has_value 替代，S3）
#   display_keys: tuple[str, ...]          # 非凭据键（查询回显顺序）

# 写入统一走 CLI（锁内）：
#   新增：rclone config create <name> <type> key=value... [--obscure]
#   编辑：rclone config update <name> key=value... [--obscure]
#   删除：rclone config delete <name>
# 查询统一走：rclone config dump（JSON）→ managed 过滤 → 凭据替换 has_*
```

## 3. 各类型配置的生成逻辑

### 3.1 Crypt（rclone crypt 后端，加密叠加层）

| 字段 | 必填 | 校验/处理 |
|---|---|---|
| `remote_name` | ✅ | 生成的配置段名（remote 名），限 `[A-Za-z0-9_-]+`；新增时与既有段重名 → 409（dump 预检 + rclone 自身也报错） |
| `remote` | ✅ | 底层目标，**三态**（v1.12，官方文档 §Specifying the remote）：① `远端名:路径`（如 `mywebdav:backup/restic`）→ 远端名须在现有 remotes 清单中；② 本地绝对路径 `/repo/encrypted`、`C:/x`、`C:\x`、`\\srv\share` → 放行（本地路径不做存在性校验：rclone 会自动创建）；③ 无冒号相对路径 → 422 拒绝（rclone 视为进程 cwd 下子目录，容器内不可控）。**UI 两模式**（裁决 12）：远端 = 下拉+路径；本地 = 目录树选择（`DirPickerInline`，容器视角） |
| `password` | ✅ | 默认明文提交，`--obscure` 由 rclone 混淆；`already_obscured: true` 时提交混淆值且命令不加 `--obscure` |
| `password2` | 可选 | 文件名加密 salt，规则同上 |
| `filename_encryption` | 默认 `standard` | 白名单 `standard / obfuscate / off` |
| `directory_name_encryption` | 默认 `true` | 布尔 |
| `filename_encoding`（v1.1；默认 v1.10 改 `base64`） | 默认 **`base64`** | 白名单 `base32 / base64 / base32768`：base64（默认）文件名更短、适合大小写敏感远端；base32 通用性最好；base32768 适合按 UTF-16/码点计长的远端（OneDrive/Dropbox） |

### 3.2 WebDAV

| 字段 | 必填 | 校验/处理 |
|---|---|---|
| `remote_name` | ✅ | 同上 |
| `url` | ✅ | http(s) 格式校验 |
| `vendor` | 默认 `other` | 白名单 `other / owncloud / nextcloud / sharepoint` |
| `user` | ✅ | 原文写入 |
| `pass` | ✅ | 混淆规则同 Crypt `password` |
| `bearer_token` | 可选 | 混淆规则同上 |

### 3.3 通用防护

- **参数注入防护**：所有 `key=value` 值拒绝 `\r`、`\n` 与控制字符；`remote_name` 正则白名单（同段名规则）；
- **凭据零回显**（S3）：请求中的明文只经内存流转进入 argv，响应、日志、审计 detail、异常消息一律不含；rclone 的 stderr 可能回显参数时须截断过滤；
- **CLI 失败即拒绝**：rclone 二进制缺失/超时/非零退出 → 可操作 reason 报错（503/422），绝不静默。

生成效果示例（Crypt，含 `filename_encoding`；实际落盘由 rclone 写入，形态一致）：

```ini
[mycrypt]
type = crypt
remote = gdrive:backup/restic
password = XXXX-obscured-XXXX
password2 = YYYY-obscured-YYYY
filename_encryption = standard
directory_name_encryption = true
filename_encoding = base64
```

## 4. 32 位随机密码生成（裁决 5，前端实现）

- **生成位置：客户端**（`frontend/src/utils/password.ts`，Web Crypto `crypto.getRandomValues`）——密码不额外经服务端流转（提交路径与手填一致，不多暴露面），零后端改动；
- **规格**：长度 **32**，字符集 `A-Za-z0-9` + 安全符号（排除引号/反斜杠/空白/INI 敏感字符）；每字符取 6 bit 熵（掩码采样避免 modulo 偏置）；
- **交互**：`password` / `password2` / `pass` 输入框旁「骰子」按钮，点击生成并填充；编辑模式下同样可用；
- 附「显示明文」切换（n-input type=password 切换），生成后默认明文展示一次方便抄录。

## 5. conf 管理能力（裁决 6）：查询 / 编辑 / 删除

### 5.1 能力边界（v1.3：裁决 8/9）

- **managed 判定**：段的 `type` ∈ 当前注册表（一期 `{crypt, webdav}`，二期扩 s3/b2/sftp）→ **可编辑**（字段级表单）；`managed` 语义仅限编辑；
- **删除 = 全部段可删**（裁决 8）：OAuth 网盘、宿主机手工配置的 s3/sftp 等均可删；确认门按段类型分级（OAuth 段加重提示「删除后需在宿主机重新授权才能再用」）；
- **编辑 = 仅 managed**：非注册类型（含 OAuth、未注册的 s3/sftp）编辑 → 422；OAuth 类型永不进注册表（§8）；
- **查询 = 全部段**：所有段展示名字与 type；仅 managed 段回显非凭据参数（凭据以 `has_*` 布尔替代）；
- 凭据值**任何操作下都永不回显**（S3 口径不变）。

### 5.2 查询：`GET /api/rclone/configs`

实现：`rclone config dump`（JSON，全量含混淆值）→ 遍历 remotes → 取 `type` → managed 段解析 `display_keys` 非凭据参数 + 凭据键以 `has_<key>` 布尔回显；非 managed 段只留 `{remote, type, managed: false}`。

**响应**（零凭据）：

```jsonc
{
  "conf_path": "/etc/rclone/rclone.conf",
  "conf_present": true,
  "configs": [
    { "remote": "mycrypt", "type": "crypt", "managed": true,
      "params": { "remote": "gdrive:backup/restic",
                  "filename_encryption": "standard",
                  "directory_name_encryption": "true",
                  "filename_encoding": "base64" },
      "has_password": true, "has_password2": false },
    { "remote": "gdrive", "type": "drive", "managed": false, "params": {} }
  ]
}
```

### 5.3 编辑：`PUT /api/rclone/config/{remote_name}`

- 请求体与创建相同；**type 必须与现有段一致**（dump 预检，不一致 → 422）；**语义为全量更新**；
- **密码留空 = 保持不变**（该键不出现在 `rclone config update` 参数中，conf 既有值不动）；
- **底层在册校验与 create 同口径**（v1.12，裁决 11）：此前 `update()` 漏了这步，编辑把 crypt 的 remote 改成不在册远端也能保存成功、用时报错；现在同样过 `_check_underlying`（local 形态按裁决 10 跳过）；
- 实现：Lock 内 dump 预检（在册 + managed + type 一致 + **底层在册**）→ `rclone config update <name> key=value... [--obscure]` → 写后 dump 复核；
- 审计：`rclone.config_edit`（detail：remote 名、type、是否改了凭据布尔，不含值）。

### 5.4 删除：`DELETE /api/rclone/config/{remote_name}`

- **全部段可删**（裁决 8）；不在册 → 404；
- 实现：Lock 内 dump 预检（确认段存在，取其 type 供审计与提示分级）→ `rclone config delete <name>` → `listremotes` 验证（rclone 自带备份+原子替换，无需自研兜底）；
- 审计：`rclone.config_delete`（detail：remote 名、type——type 供审计追溯被删段性质）；
- 前端确认门：n-popconfirm 二次确认（段名出现在确认文案中）；**OAuth 段加重提示**（段名 + 「该网盘配置删除后需在宿主机重新授权才能再用」）。

### 5.5 写路径的并发与一致性（v1.2：全交官方实现）

- 全部写操作在 `asyncio.Lock` 内「dump 预检 → CLI 写 → listremotes 验证」，缩小与宿主机编辑/OAuth 回写的交叠窗口；
- rclone 自身的保存机制（临时文件 + 双 rename + 权限镜像 + 备份清理）由目录挂载形态保证可用（§1.3-1）；
- 真机验证项（B1 spike）：容器内 `rclone config create → update → delete` 全链路 round-trip，确认目录挂载下 rename 正常、`--obscure`/`key=value` 语法在 Alpine rclone 版本上可用。

## 6. API 契约汇总

| 方法/路径 | 用途 | 审计动作 |
|---|---|---|
| `GET /api/rclone/configs` | 查询 conf 段清单（含非凭据参数） | 只读，不埋点 |
| `POST /api/rclone/config` | 新增配置段 | `rclone.config_append` |
| `PUT /api/rclone/config/{remote_name}` | 编辑配置段 | `rclone.config_edit` |
| `DELETE /api/rclone/config/{remote_name}` | 删除配置段 | `rclone.config_delete` |

**创建请求体**（PUT 同形，密码可留空）：

```jsonc
{
  "type": "crypt",            // "crypt" | "webdav"（白名单，未注册类型 422）
  "params": {
    "remote": "gdrive:backup/restic",
    "password": "…",
    "already_obscured": false
  }
}
```

**创建响应**（零凭据）：

```jsonc
{
  "remote": "mycrypt",
  "conf_path": "/etc/rclone/rclone.conf",
  "remotes_after": ["gdrive", "mycrypt"]
}
```

**错误语义**：

| 状态码 | 场景 |
|---|---|
| 422 | 字段缺失/白名单外/注入字符/remote 格式非法/底层 remote 不在册/type 不一致/目标非 managed/rclone 校验拒绝 |
| 404 | 删除/编辑目标段不在 conf 中 |
| 409 | 新增时 `remote_name` 与既有段重名 |
| 503 | rclone 二进制不可用（CLI 执行失败），带可操作 reason |

**鉴权与红线**：全部挂 `require_session` + Origin 同源校验（S4，含 DELETE/PUT）；conf 路径取自 settings 而非用户输入，不涉 PathMapper（S1 不适用）。

## 7. 设置页前端方案（裁决 1：一次做完）

- **入口**：设置页新增分区 `rclone`（标题「rclone 远端配置」），插入 `SECTIONS` 的 `credential` 之后；沿用「顶部分段 Tab + 单面板 v-if + `#sec-rclone` 深链」既有形态；
- **面板结构**（`RcloneConfigPanel.vue`），上下两区：
  1. **已有配置列表**：`GET /api/rclone/configs` 渲染（managed 段展示非凭据参数 + `has_*` 徽标；非 managed 段灰显参数区，只露名字与 type）；每行操作：**编辑**（仅 managed 段显示）/ **删除**（全部段显示，n-popconfirm 确认；OAuth 段文案加重「删除后需重新授权」）；
  2. **生成/编辑表单**：类型 Select（Crypt/WebDAV）→ 按 v-if 切换字段组（与后端 §3 一致）；密码输入框旁「生成」按钮（§4）+ 明文切换；编辑模式复用同一表单（进入编辑 = 预填非凭据字段、密码框空 + placeholder「留空保持不变」、`remote_name` 只读）；`already_obscured` 复选框（勾选后密码框语义变为「已混淆值」，提交命令整体不加 `--obscure`）；提交后刷新列表并 `message.success`；409/422/503 错误 reason 直出；
     - **Crypt 底层目标（v1.12/1.13，裁决 12）**：先选**底层类型**（NRadioGroup 两模式）——
       **① 远端**：「**NSelect 选在册远端**（候选 = 列表快照里的全部段，排除正在编辑的段自身，避免自引用成环）+ 路径输入框（留空 = 远端根）」，提交时拼回 `name:path`；未选远端时前端拦截不发请求；
       **② 本地目录**：容器视角目录树选择（`DirPickerInline.vue`：`PathBreadcrumb` + `VirtualTree`(E33) + 路径值输入框），提交时整串作为 `remote`（绝对路径）；
       编辑态自动判形态回填——`/x`、`C:/x`、`C:\x`、`\\srv\share` → 本地模式整串进路径位；其余按**首个冒号**拆两段；无法识别的历史值原样落路径位（不静默丢值）；
       **共用组件（S-4）**：`DirPickerInline` 由仓库页 local 行抽出，两处共用一份实现（内含三条纪律：挂载点独立预取、点目录名不移动树、只对外部赋值重定位）；
  3. 底部说明：OAuth 网盘类型不在本工具范围（宿主机生成），指向部署指南；crypt 底层候选**包含**网盘段（crypt 可叠加在任何 remote 上，含 OAuth 网盘）；
- **字段宽度收口（v1.14）**：远端名 320px（原撑满整行，观感「太长」）；密码三栏 400px（原在 `n-space` 内塌缩，32 位随机密码只看得见前几位）；其余 Select 200px。密码框统一走 **`components/PasswordInput.vue`**（唯一实现，见 §7.1）；
- **i18n**：`settings.rclone.*` 键中英双 locale 同步（i18n-parity.spec.ts 护栏）；
- **测试**：面板组件测（列表渲染/类型切换字段组/编辑预填/密码留空语义/提交调用/错误展示/删除确认门）+ 设置页分区切换含 `rclone` + 随机密码生成器单测（长度/字符集/熵采样无偏）。

### 7.1 密码输入统一组件（v1.14，B8）

`components/PasswordInput.vue` 是**项目唯一密码输入实现**（S-4 单一实现 / 审查规范重复实现条款）。

| 项 | 说明 |
|---|---|
| 为什么要有它 | `n-input type="password"` 的宽度行为完全取决于容器：`n-form-item` 内默认 `width:100%`（宽面板里被拉成一整行），`n-space` 内无 `flex-basis`（塌缩到几十像素，32 位密码看不全）。宽度在组件内一次收口 |
| 宽度语义 | `width` 属性（CSS 值）：**表单字段型**用默认 `320px`，**容器撑满型**传 `100%`，**定宽场景**传具体值（如导出口令 260px）；外层恒 `max-width:100%` 防窄屏溢出 |
| 行为 | `show-password-on="click"`（点眼睛图标切明文，不用 hover 预览——会频繁误触发）；可选 `generate-label` → 渲染「生成」按钮并 emit `generate`（生成实现归调用方） |
| 属性透传 | `inheritAttrs: false` + `v-bind="$attrs"` 落到内部 NInput——`maxlength`、`@keyup.enter`、`placeholder` 等不必逐个声明 |
| 迁移范围 | 18 处：登录页、bootstrap 三步改密、仪表盘导出、通知邮件密码、设置页 7 处（dump / metrics / 改密 ×3 / 导出 / 导入口令）、仓库页凭据行、仓库密码弹窗、rclone 面板 3 处 |
| 护栏 | `components/__tests__/PasswordInput.spec.ts` 断言「除组件自身外，`src/**/*.vue` 不得出现 `type="password"` 或 `type: 'password'`」——防止将来又散回裸实现 |

### 7.2 密码生成与「一次性展示」统一（v1.15，B9）

全站「自动生成密码」收敛为**一套生成器 + 一套呈现 + 一个文案**（S-4 单一实现）：

| 关注点 | 统一口径 |
|---|---|
| **生成器** | `utils/password.ts` 的 `generatePassword()` 是**唯一实现**：长度 32、四类字符（小写 / 大写 / 数字 / 符号，均剔除易混 `0O1lI`）、每类至少取一个再 Fisher-Yates 洗牌、拒绝采样无模偏置。`utils/passwordStrength.ts` 的 `generateStrongPassword()` / `REPO_PASSWORD_LENGTH` **已删除**（曾并存 24 位版，导致同一系统里密码位数不一致） |
| **长度** | 全站 32 位。后端两处同口径：`constants.REPO_PASSWORD_LENGTH = 32`（仓库密码）、`auth/password.py` 的 `RANDOM_PASSWORD_LENGTH = 32`（首启管理密码，字符集仍为字母+数字——要打印到日志供人抄录，符号易被 shell/输入法吞掉） |
| **按钮文案** | 唯一键 `common.generatePassword` =「生成密码」/「Generate password」（不带位数）。删除 `repo.passwordGenerate` / `settings.rclone.generatePassword` / `settings.transfer.generatePassword` 三个分散键 |
| **一次性展示** | `components/SecretReveal.vue` 是**唯一呈现**：success 提示 + 等宽只读明文 + 复制 + 返回/确认。`PasswordEntryModal` 的生成态、rclone 三处密码生成、导出口令生成全部复用它 |
| **防误丢** | 生成 → **弹窗展示（可复制）** → 用户点「我已备份」才写入表单/提交。旧行为（rclone / 导出口令直接把明文塞进掩码框）用户既看不到也复制不了，凭据丢失即不可逆 |

接入点（5 类场景 / 4 个复用处）：

| 场景 | 生成实现 | 呈现 |
|---|---|---|
| 仓库设置密码 E44 | 后端 `REPO_PASSWORD_LENGTH` 生成，响应一次性回显 | `PasswordEntryModal` → `SecretReveal` |
| 密钥轮换 C4 | 前端 `generatePassword()`（确认后才提交） | 同上 |
| 重建默认仓库结果 | 后端返回的一次性新密码 | 同上（仅展示态） |
| rclone crypt/webdav 密码 ×3 | 前端 `generatePassword()` | 面板内 `NModal` + `SecretReveal` |
| 配置迁移导出口令 | 前端 `generatePassword()` | 同上（确认后填入输入框） |

## 8. 可扩展类型路线（注册表即插即用；编辑能力随注册表扩展，裁决 9）

| 阶段 | 类型 | 说明 |
|---|---|---|
| 二期 | `s3` / `b2` | access_key/secret_key 混淆写入，字段多但同模式；**进注册表后即获得字段级编辑表单** |
| 二期 | `sftp` | host/user/key_file（key 走挂载路径引用而非内联）；同上 |
| 三期 | `alias` / `local` | 无凭据类型，最简单 |
| **永不** | `drive` / `onedrive` 等网盘 | 需 OAuth 浏览器授权流，UI 无法替代授权（编辑无意义）；**永不进注册表**（编辑口不开）；删除仍可用（裁决 8，全段可删） |

> 设计纪律：编辑只走「注册表字段级表单」，**不开自由改键口**——自由编辑 = 绕过校验 + 注入面扩大，明确拒绝。

## 9. 部署形态变更（裁决 7 连带）

```yaml
# compose（变更前 → 变更后）
- ./rclone/rclone.conf:/etc/rclone/rclone.conf:rw   # 单文件挂载（旧）
+ ./rclone:/etc/rclone:rw                           # 目录挂载（新）
```

- conf 文件放宿主机 `./rclone/` 目录内（首次部署：`mkdir -p ./rclone && touch ./rclone/rclone.conf`，或直接留空由 `rclone config create` 落盘创建——满足裁决 3）；
- `RCLONE_CONFIG=/etc/rclone/rclone.conf` **不变**；老用户迁移 = 把现有 `rclone.conf` 移入 `./rclone/` 目录（部署指南 B4 登记迁移指引）；
- **连带收益**：修复单文件挂载下 OAuth token 回写 EBUSY 的潜在既有问题（R-022「必须 ：rw」的前提在目录挂载下才真正成立）；
- 部署指南补充：容器内 UI 写 conf 与宿主机 `rclone config` 修改同一文件，双端都写后建议刷新 remote 清单核对。

### 9.1 部署形态适配：docker 档 vs host 档（v1.5）

**结论：不加第二套实现，统一「一套写入逻辑 + 三处形态感知」。** 形态判断（`settings.deploy_mode_effective`，`docker|host`）**只用于文案与降级，绝不参与写入分支**。

| 关注点 | 容器档（docker） | 主机档（host） | 处理 |
|---|---|---|---|
| conf 路径 | `/etc/rclone/rclone.conf`（`RCLONE_CONFIG` + 目录挂载） | 宿主机真实路径（未设 `RCLONE_CONFIG` → `$HOME/.config/rclone/rclone.conf`） | **同一解析函数**：`settings.rclone_config or HOME 默认`（复用 `RcloneProbe._conf_present` 现有逻辑，抽公共 helper 供查询/写入共用） |
| rename 原子替换 | 依赖**目录挂载**（裁决 7 动因：单文件挂载 EBUSY） | 天然可用（无 bind mount 层） | 无分支，统一走 rclone CLI |
| 权限 | nonRoot 档（1000:1000）需宿主目录 uid 匹配，EACCES 常见 | 按运行用户，偶需调文件属主 | **仅错误文案分支**：容器 →「检查 compose 目录挂载 `:rw` 与目录属主」；主机 →「检查 conf 文件属主/权限」 |
| rclone 二进制 | 镜像内置（`docker/Dockerfile:50`） | **需宿主机自装**（本机未装 = `available=False` 属预期） | 复用 `RcloneProbe.available` 语义：不可用 → **503 + 可操作 reason**，前端禁用写入按钮（不裸 500） |
| 并发一致性 | 经挂载层（宿主 `rclone config` 与容器内 UI 写同一 inode） | 直接同一文件 | 同一 `asyncio.Lock` + 同一 CLI，无差别 |
| 前端提示 | 面板底部：容器档提示「conf 由 compose 目录挂载提供，宿主机亦可编辑」 | 面板底部：主机档提示「直接读写宿主机 conf，注意文件权限」 | 文案取自 `GET /api/system/runtime` 的 `deploy_mode` |

部署影响：**compose 目录挂载变更仅影响容器档**；主机档零部署动作（部署指南迁移指引须写明）。B1 spike 在容器档执行；主机档以「`rclone` 缺失降级 + 路径回落 HOME 默认」两条用例覆盖（单测即可，无需真机）。

## 10. 实施批次

| 批次 | 内容 | 前置 |
|---|---|---|
| B0 ✅ | **规范修订（已完成 2026-10-26）**：《代码审查规范》v1.14 —— S6 按实际动作名重计 **20 项**（`rclone.config_append` / `rclone.config_edit` / `rclone.config_delete` 入库 + 修正历史并组计数漂移）+ AGENTS.md 编号索引同步 + `constants.py` 三个 `AUDIT_RCLONE_CONFIG_*` 常量 + `verify_redlines.sh` 两条新断言（常量反向锚点、写入 CLI 唯一实现点）；`verify_redlines.sh` 19 项全绿 | 裁决 2 |
| B1 ✅ | **服务层 + 真机 spike 完成（2026-10-26）**：`rclone_config.py`（CLI 编排 + GENERATORS ×2 + managed 过滤 + conf 路径统一解析 helper + 实例写锁 + 写后 dump 复核）；真机 spike（WSL rclone v1.60.1）确认 `--obscure`/`key=value`/`filename_encoding` 语法与 A/B 挂载形态差异（见 §12），并修出 `--config` 缺失与超时过短两处缺陷；单测 46 例、全量 1117 passed | B0 |
| B2 ✅ | 端点 ×4 + 审计 ×3 + schemas ×4 + `main.py` 装配 + 端点测 11 例（鉴权 401 / Origin 403 / 查询零凭据 / 审计 ×3 / 409 重名 / 404 不在册 / 422 未注册类型·OAuth 段编辑 / 503 不可用） | B1 |
| B3 ✅ | **已完成 2026-10-27**：compose 目录挂载（含注释重写）+ 设置页 `rclone` 分区 + `RcloneConfigPanel.vue`（列表 + 表单 + 编辑态 + 删除确认门 + 生成密码）+ `utils/password.ts`（32 位 Web Crypto）+ i18n 双 locale；组件测 6 例 + 密码工具测 6 例；typecheck / build 通过 | B1 |
| B4 ✅ | **已完成 2026-10-27**：架构设计文档 **v1.10**（E48–E51 四端点条目 + E45 Q17 放宽注 + 写后复核护栏 + §3 目录树 + §11 R-022 行）、部署指南（2026-10-27 校准：§1 仓库类型更正、§4.2 目录挂载迁移指引 + 界面配置管理能力矩阵）、PRD **v1.14**（需求池 R-022 行） | 全部 |
| B5 ✅ | **已完成 2026-10-27**：后端 pytest 全量 **1128 passed** · ruff 全绿 · `verify_redlines.sh` **19 项全绿**；前端 typecheck **0 错误** · vitest **458 passed / 47 文件** · build **exit 0** | 全部 |
| B6 ✅ | **已完成 2026-10-27（owner 复盘后的口径修正）**：①crypt 底层目标三态分类 `classify_crypt_remote`（远端 / **本地绝对路径放行** / 相对路径拒绝；修 Windows 盘符被误判为远端名 `C`）；②`update()` 补底层在册校验（此前只有 create 校验）；③crypt 表单改「NSelect 选在册远端 + 路径输入框」，在册校验前移；④单测 46 → **63 例**（含 10 例三态分类参数化）；⑤方案写入裁决 10/11/12 | 裁决 10–12 |
| B7 ✅ | **已完成 2026-10-27（owner 裁决：抽共用组件）**：抽出 `components/DirPickerInline.vue`（容器视角：`PathBreadcrumb` + `VirtualTree`(E33) + 路径值输入框），**仓库页 local 行改为调用它**（删本地实现 ~66 行脚本 + 52 行样式 + 3 个 import），rclone crypt 面板接入为「本地目录」模式；组件测 7 例（挂载点预取 / 树起点=父目录 / 点选与文件忽略 / 手输 / 外部赋值重定位 vs 自 emit 不重定位 / disabled）；面板测 +2 例（本地模式提交绝对路径 / 非绝对路径拦截） | 裁决 12 |
| B8 ✅ | **已完成 2026-10-27（owner 实测三项反馈）**：①修复「新增 crypt 提示参数校验失败」——`api/repo.ts` 双重 `JSON.stringify` 使请求体变字符串字面量，后端 pydantic 422；改为 `body` 直接传对象。②抽出 `components/PasswordInput.vue` 并迁移全项目 18 处密码框（宽度可配 / 明文切换 / 可选生成 / `$attrs` 透传），护栏断言无裸 password 输入。③rclone 表单远端名收口 320px、密码三栏 400px；生成密码后给明文提示。护栏新增 `api/__tests__/rclone-config.spec.ts`（fetch 级序列化断言）与 `components/__tests__/PasswordInput.spec.ts` | 实测反馈 |
| B9 ✅ | **已完成 2026-10-27（owner：统一生成方式 + 文案 + 32 位）**：①合并生成器（删 `generateStrongPassword`/`REPO_PASSWORD_LENGTH`，前后端统一 32 位）；②抽 `SecretReveal.vue` 并接入 5 类场景（仓库设置密码 / 轮换 / 重建结果 / rclone ×3 / 导出口令），rclone 与导出口令从「静默塞掩码框」改为「弹窗一次性展示 → 确认写入」；③按钮文案统一 `common.generatePassword`=「生成密码」 | 实测反馈 |

## 11. 测试与护栏清单

- 生成器单测：两类型 `build_args` 渲染（含 `filename_encoding`）、白名单校验、缺字段 422、`already_obscured` 分支（不加 `--obscure`）、**crypt 底层目标三态分类**（`classify_crypt_remote` 参数化 10 例：远端指针 / 本地绝对路径 Linux·盘符·UNC / 相对路径拒绝）；
- 编排层单测：dump JSON 解析与 managed 过滤、凭据键替换 `has_*`、非 managed 段零参数回显、CLI 非零退出/超时/缺二进制降级（**含 host 档「未装 rclone」路径**）、Lock 串行断言、**create 与 update 双路径的底层在册校验**（local 形态跳过）、编辑改不在册远端时零 CLI 调用；
- 端点测试：鉴权 + Origin + 审计埋点 ×3 断言 + 能力边界双向断言（OAuth 段编辑 422 / 删除可用且审计含 type）+ 响应/异常/日志零凭据泄漏；
- 前端：面板组件测（列表 / 编辑预填拆两段 / 未选底层远端时拦截不发请求 / `remote` 拼成 `name:path` / **本地模式提交绝对路径 + 非绝对路径拦截**）+ `DirPickerInline` 组件测 7 例（挂载点独立预取 / 树起点=父目录 / 点目录名选中且不移动树 / 文件不选中 / 手输 / 外部赋值重定位 vs 自 emit 不重定位 / disabled）+ 随机密码生成器单测 + i18n parity；
- **请求体序列化护栏（v1.14，B8）**：`api/__tests__/rclone-config.spec.ts` **不 mock `@/api/http`**（mock 掉就拿不到序列化层——这正是本次 422 缺陷逃逸的原因），改 stub `global.fetch`，断言真实 `init.body` 解析后是**对象**而非字符串；
- **密码输入单一实现护栏（v1.14，B8）**：`components/__tests__/PasswordInput.spec.ts` 全量扫描 `src/**/*.vue`，除组件自身外零 `type="password"`；
- 红线：`bash scripts/verify_redlines.sh` 全绿（含 S6 新增断言）；
- 真机验收（B1 spike + B5）：容器内 create→update→delete round-trip + 设置页全旅程。

## 12. 真机 spike 实证记录（2026-10-26，WSL 真机 rclone v1.60.1）

> 环境：WSL2（Debian 13，root），`apt install rclone` = v1.60.1；无 docker，故容器档
> 以 **bind mount 模拟**单文件/目录两种挂载形态做 A/B 对照。

### 12.1 A/B 挂载形态对照（**裁决 7 的实证依据**）

| 形态 | create 退出码 | 落盘结果 | 结论 |
|---|---|---|---|
| **单文件** bind mount（`mount --bind src.conf dst.conf`） | **0**（无报错） | 挂载点与宿主机源文件**均未被修改**；且写操作会**挂住不返回**（实测 8s 未结束） | **不可用**：静默失败 + 卡死，UI 若只看退出码会误报成功 |
| **目录** bind mount（`mount --bind ./rclone /etc/rclone`） | 0 | 宿主机侧文件真实更新；create crypt → update → delete 全链路生效，dump JSON 正确，权限沿用原文件（644） | **可用**（裁决 7 正确） |

### 12.2 CLI 语义实测（决定「哪些校验必须在编排层」）

| 场景 | rclone 真实行为 | 本工具对策（已实现） |
|---|---|---|
| 重名 create | **exit 0，静默覆盖同名段** | 409 只能靠 dump 预检（编排层已实现） |
| delete 不存在的段 | **exit 0，静默成功** | 404 只能靠 dump 预检（已实现） |
| 未知选项键（如 `url2`） | **exit 0，静默落盘** | 注册表白名单是唯一防线（已实现，422） |
| 缺参数 create | exit 0，写入残缺段（**不交互追问**） | 生成器必填校验（已实现，422）；仍传 `stdin=DEVNULL` 防挂起 |
| 未知 backend 类型 | exit 1（couldn't find backend） | 注册表前置拦截（422） |
| `--obscure` + `key=value` | 支持；`filename_encoding` 的 base32/base64/base32768 均被接受 | 与实现一致 |
| conf 不存在时 dump | stdout `{}` + **stderr NOTICE**，exit 0 | `_parse_dump` 截取首尾花括号后解析（**缺陷修复**） |

### 12.3 spike 抓到的两处实现缺陷（已修）

1. **未向 rclone 传 `--config`**：编排层解析了 conf 路径却不传参 → rclone 走 `$HOME`
   默认 conf，出现「读写的 conf ≠ UI 展示的路径」（主机档与容器档 HOME 不同，容器还分
   root/nonRoot 档）。修复：所有命令统一带 `rclone --config <conf_path>`。
2. **写操作复用探测的 5s 超时**：实测 rclone **单次启动即 ~2.3s**（简单 dump 也要
   2.3–2.6s），5s 在慢环境会误判超时。修复：新增
   `constants.RCLONE_CONFIG_TIMEOUT_SECONDS = 15.0`（与仓库探测同档），超时文案同时
   指向单文件挂载这一常见诱因。

### 12.4 单文件挂载的兜底行为（最终形态）

单文件挂载下写 conf 会**挂住**，最终由超时兜底成 **503 + 可操作文案**（「若 conf 位于
单文件 bind mount，rclone 保存会卡住：请将 compose 改为目录挂载 …」）；另加**写后 dump
复核**（段缺失 / 落值不符 → 503），双保险确保「退出码 0 却没写进去」不会误报成功。

## 13. 已登记风险与取舍

| 风险/取舍 | 说明 | 处置 |
|---|---|---|
| `key=value` 参数经 argv 传递，进程列表理论可见明文 | 容器单用户运行、存活毫秒级（与原 `rclone obscure` 同级风险） | 登记为已接受风险；支持 `already_obscured` 旁路 |
| rclone CLI 版本语法差异 | `key=value` 与 `--obscure` 需 rclone ≥1.56；Alpine stable rclone 远新 | **B1 spike 待真机确认**（本机无 docker/rclone）；登记依赖升级指南核查点 |
| rclone 校验可能拒绝合法键 | create/update 对未知选项键报错（如旧版无 `filename_encoding`） | 同上，待真机确认 Alpine 版本含该键；报错 reason 直出可诊断 |
| 真机已验证但版本/形态有限 | spike 在 WSL rclone **v1.60.1**、目录 bind mount 下全绿；容器（Alpine 版 rclone）未跑 | B2 后建议在真容器内复跑一次 §12 的 A/B 与 round-trip；Alpine rclone 版本差异登记入依赖升级指南 |
| 单文件挂载下写 conf 会挂住 | 实测 8s+ 不返回，最终靠 15s 超时兜底 | 已实现 503 + 挂载形态文案；**部署侧必须改目录挂载**（§9），否则功能不可用而非报错 |
| 与宿主机编辑/OAuth 回写的并发窗口 | 双端都可能写 conf | Lock 内「dump 预检 → CLI 写 → 验证」缩小窗口；目录挂载下 rclone 原子替换 + 权限镜像兜底 |
| 管理能力边界（v1.3） | 编辑随注册表扩展（一期 crypt/webdav）、删除全段放开 | 编辑口只走字段级表单；删除确认门分级 + 审计留痕；测试覆盖「OAuth 段编辑 422 / 删除可用」双向断言 |
| Q17 档位放宽的文档一致性 | 三处文档（架构/部署指南/PRD）需同批登记 | B4 批次强制收口 |
| **本地路径不做存在性校验**（v1.12，裁决 10） | 官方文档：`The path you specify does not have to exist, rclone will create it when needed`。若填了**宿主机路径**（容器内不存在），rclone **不报错**、会在容器可写层建同名目录，数据落容器层 → 容器重建即丢 | 后端无法静态判定（本地路径合法且可自动创建）；**UI 侧缓解（v1.13 已落地）**：目录树（E33 只列真实存在项）+ 挂载点快跳 + 明确提示「容器内视角 / 宿主机目录须先挂载」；手输仍保留（目录可先不存在），故仍属**知情风险** |
| 本地路径形态不校验可写性 | 路径存在但只读/无权限 → 备份运行时才报错 | 属运行期错误，`failure_reason` 直出；不前置探测（探测成本高且与运行期权限可能不同） |
