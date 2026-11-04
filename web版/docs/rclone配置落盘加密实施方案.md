# rclone 配置落盘加密实施方案

> 版本：v1.2（2026-11-05）
> 层级：方案层——`rclone.conf` **落盘加密与运行时口令通道**的唯一方案源，亦即 GitHub issue #2 的实现依据
> 边界：本文只覆盖 **conf 文件的加密态识别与口令通道**。rclone 段的增删改查能力边界（注册表、凭据混淆、占用扫描、配置归档）见《rclone配置生成工具设计方案》v1.21，本文不重复、不替代。
> 关联：`app/services/rclone.py`（只读探测）、`app/services/rclone_config.py`（conf 管理）、`docker/Dockerfile`、`docs/部署指南.md` §4.2、`docs/架构设计文档.md` §5.0/§5.1

> **修订记录**：
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
| **界面只读三态，启停走宿主 shell** | 面板只显示「未加密 / 已加密可解 / 已加密无法解」，不提供任何可致锁的写入口（§5.3）；应用自身无法把 conf 推进加密态 |

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

### 5.2 因此「启用开关」被消解

票面原待办含一条「开关：是否启用 conf 加密（默认关）」。经实测该开关**不需要存在**：

| 原设想 | 消解理由 |
| --- | --- |
| 需要一个 config 键声明「本部署启用了加密」 | 文件头本身就是声明，且是**更强**的声明（无法与文件真实状态不一致） |
| 需要「默认关、避免存量踩坑」 | 明文 conf 没有 header，识别逻辑天然不触发，存量行为零变化 |
| 需要界面按钮跑 `encryption set` | 加密是**一次性运维动作**，且属「改文件」而非「改配置」；本期不做（§10） |

⇒ 本期**不新增任何 config 键、不新增 settings 字段**。部署者用一条命令开启，应用自动识别。

**启用与停用一律走宿主 shell，界面只读**：

```bash
# 开启（容器内执行；--password-command 是唯一能让 set 无头完成的通道，见 §4.2）
docker compose exec -T restic-web rclone config encryption set \
  --password-command 'cat /run/secrets/rclone_pass'
# 停用
docker compose exec -T restic-web rclone config encryption remove
```

界面**不提供任何可导致锁死的入口**——没有启用按钮、没有停用按钮、不接收口令。这是「零锁死风险」的确切含义：**应用自身无法把 conf 推进加密态**，加密只可能由部署者在 shell 里显式执行。

> 边界：该表述仅指**界面侧无致锁入口**。部署侧一旦执行了 `set` 又丢失口令，仍会锁死且不可恢复（§9.1）——故 §5.3/§5.4 的识别与 §5.6 的启动自检仍然必要，它们是锁死**之后**的可观测性，不是锁死的预防。

### 5.3 界面只读三态（方案 C 的确切形态）

面板 `confStatus`（`RcloneConfigPanel.vue:226-230`）由现有二态扩为三态，**只读展示，无任何可写控件**：

| 状态 | 判定 | 界面文案（zh-CN） |
| --- | --- | --- |
| **未加密** | 文件头无加密标记 | 明文 conf（未加密） |
| **已加密可解** | 有加密标记 且 `encryption check` = 0 | 已加密 · 口令可用 |
| **已加密无法解** | 有加密标记 且 `encryption check` = 1 | ⚠️ 已加密但无法解密——所有 `rclone:` 仓库将不可用 |

第三态出现时，「已加密无法解」**同时**意味着应用当前无法读写 conf（§4.4 面板路径 685 ms 快速失败）。故该状态不只是文案：面板顶部常驻 NAlert（复用 `:626` 现有 alert 位）说明后果与恢复路径，且列表区改为空 + 该提示，不再展示半截数据。

用户真正需要回答的问题是「我的备份还能不能跑」，三态直接给出答案；至于「怎么开怎么关」，走上面的宿主 shell。

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

### 5.8 口令从哪来：三种部署形态（部署侧自管时）

本期**不引入** `*_FILE` 基础设施、不引入 secret provider 抽象（那是已暂缓的 #3 范围）。口令的供给完全由部署侧决定，应用只认环境变量：

| 形态 | 做法 | 适用 |
| --- | --- | --- |
| **A. 明文 env（最简）** | `RCLONE_CONFIG_PASS: "<口令>"` 写在 compose 里 | 单机自用；已知 `docker inspect` 可见 |
| **B. compose secrets（推荐）** | `secrets: rclone_pass: file: ./secrets/rclone_pass` + `RCLONE_PASSWORD_COMMAND: "/bin/cat /run/secrets/rclone_pass"` | 口令不入 compose 明文、`docker inspect` 只见命令 |
| **C. 宿主文件挂载** | 挂 `./rclone/pass:/etc/rclone/pass:ro` + `RCLONE_PASSWORD_COMMAND: "/bin/cat /etc/rclone/pass"` | 已有自管凭据目录的部署 |

> **compose 模式的 secrets 仍落盘**：它是把宿主机源文件 bind mount 进容器（非 swarm 无 tmpfs），实测确认。收益不是「不落盘」，而是「**不出现在 `docker inspect` / 镜像层 / compose 文件里**」，且权限可收紧到 `0600`。这与 Q1「部分读」威胁模型一致——要防的是 conf 被单独拷走，不是防宿主。

**口令由人定，不由应用生成**：应用没有、也不该有生成该口令的逻辑（§10 已排除界面录入）。建议长度 ≥32 且用 `openssl rand -base64 32` 之类生成，但**rclone 对此不做任何校验**（§4.8：单字符也接受），故强度纪律必须写在部署指南里，代码拦不住。

### 5.9 口令从哪来：entrypoint 自动生成（可选，默认关）

部署侧**不必手写**这个口令——设置一个路径即可，首次启动自动生成：

```yaml
environment:
  RCLONE_PASS_FILE: /repos/rclone.pass
  RCLONE_PASSWORD_COMMAND: "/bin/cat /repos/rclone.pass"
```

| 行为 | 说明 |
| --- | --- |
| 文件不存在 | 生成 **32 位 `A-Za-z0-9`**，`umask 077` 写入后 `chmod 600`，日志提示立即离线备份 |
| 文件已存在 | **只打印一行、绝不覆盖**（覆盖 = 加密 conf 当场解不开） |
| 父目录不存在 | 自动 `mkdir -p`（root 档随后 chown 到 `PUID:PGID`） |
| `RCLONE_PASS_FILE` 未设 | **不做任何事**——本功能默认关，存量部署零变化 |
| 非 root 档 | 父目录纳入 `need_dir` 硬检查，不可写则拒绝启动并提示宿主机 `chown` |

**为什么字符集取纯字母数字**（与 `utils/passfile.py` 的四类混排不同）：该口令要经 `cat` 原样喂给 rclone，
纯字母数字可避免 shell 引用、编辑器补齐、复制粘贴对 `! $ % & *` 的二次解释。32 位 alnum 熵约 190 bit，足够。

**为什么单独一个文件，不复用 `<passfile_root>/default/password`**（实测可行 vs 工程代价）：

| | 复用 default 口令 | 独立 `rclone.pass` |
| --- | --- | --- |
| 部署侧新增秘密 | 0 个 | 1 个（且**自动生成，无需人手写**） |
| `key.rotate` 轮换仓库密码 | **口令文件被 `write_text` 覆盖 → conf 永久解不开，且静默** | 不受影响 |
| `rebuild` / `purge` / 归档到期 | `write_passfile` / `remove_passfile` 同样毁掉 conf | 不受影响 |
| 一个文件三用 | 是（restic 密码 + `enc:v2:` 密钥源 + conf 口令） | 否 |

> `ops.py:925` 的轮换逻辑直接改写口令文件，且**全程没有 rclone 感知**（已 grep 确认零命中）；
> `repo_lifecycle.py:605`（rebuild 写）、`:215`（purge 删）、`retention.py:400`（归档到期删）同样会动该文件。
> 把这些路径与「加密 conf 用不用同一把钥匙」耦合，等于在一条重点审查的核心路径上埋一个**延迟发作**的静默失效：
> 轮换会报 `result=ok`、凭据也正常重加密，只有下次跑 `rclone:` 备份时才炸，而错误是 `Couldn't decrypt configuration`——指不到真正原因。
> 自动生成把「新增一个秘密」的成本降到零，因此拒绝复用 default 口令的唯一理由（部署麻烦）已不存在。

**口令丢失仍不可恢复**（§4.11 不变）：自动生成不改变这一点，日志会明确提示「请立即离线备份」。
## 6. 实现清单

| # | 文件 | 改动 | 约行数 |
| --- | --- | --- | --- |
| 1 | `docker/Dockerfile` | **已完成**（`f37a905`）：`ARG RCLONE_VERSION=1.75.2-r0`（`:28`）、`rclone=${RCLONE_VERSION}`（`:53`） | 0 |
| 2 | `docker/docker-compose.yml` | `environment` 增 `RCLONE_PASS_FILE` + `RCLONE_PASSWORD_COMMAND` 注释示例（默认注释掉 = 不启用）；顺带更正 `:74` 陈旧的 `v1.60.1` 注释 | 6 |
| 2b | `docker/entrypoint.sh` | **已完成**：`RCLONE_PASS_FILE` + `ensure_rclone_pass()`（32 位 alnum、umask 077、0600、已存在不覆盖、自动建父目录）；root 档生成后 chown，非 root 档父目录 `need_dir` 硬检查 | 0（≈30 行已落） |
| 3 | `backend/app/constants.py` | 新增 `RCLONE_CONF_ENCRYPTED_MARKERS`（两个文件头标记）、`RCLONE_CONF_LOCKED`；`ERROR_STATUS` 增 503 映射 | ~10 |
| 4 | `backend/app/services/rclone.py` | 新增 `_conf_header()`（纯读，返回三态）；`RcloneRemotes` 增 `conf_encrypted: bool` + `conf_locked: bool`；`list_remotes` 锁死分支给可操作 `reason` | ~45 |
| 5 | `backend/app/services/rclone_config.py` | `RcloneConfigList` 增同名字段；`list_configs` 的 `except` 分支区分锁死 | ~18 |
| 6 | `backend/app/errors.py` | 新增 `RcloneConfLockedError`（503） | ~8 |
| 7 | `backend/app/routers/repos_rclone.py` | `_raise_rclone_error` 增分支；remotes 端点透传新字段 | ~10 |
| 8 | `backend/app/routers/repos_shared.py` | `validate_repo_url` 锁死 → 422（§5.5 双向语义） | ~12 |
| 9 | `backend/app/schemas/api_schemas.py` | `RcloneRemotesOut` / `RcloneConfigsOut` 增字段（`conf_encrypted`、`conf_locked`） | ~8 |
| 10 | `backend/app/main.py` | lifespan 启动自检（§5.6） | ~30 |
| 11 | `frontend/src/api/types.ts` | 同步两类型新字段 | ~6 |
| 12 | `frontend/src/api/openapi.json` + `schema.d.ts` | **生成物**：`scripts/gen-openapi.py` → `pnpm -C frontend gen:api` | 0（自动） |
| 13 | `frontend/src/components/settings/RcloneConfigPanel.vue` | `confStatus` computed 由二态扩三态；锁死时顶部常驻 NAlert（复用 `:626` 现有 alert 位） | ~35 |
| 14 | `frontend/src/i18n/locales/zh-CN.ts` / `en-US.ts` | 三态文案 + 锁死提示 + 恢复指引 | ~14 |
| 15 | `backend/tests/test_rclone_config.py` / `test_repo_backends.py` / `test_api/test_rclone_config_api.py` | 见 §8 | ~180 |
| 16 | `docs/部署指南.md` | 改写 `:315-317`（双端编辑纪律）、`:327-330`（加密段）、`:352`（env 表加 `RCLONE_PASSWORD_COMMAND`） | ~40 |
| 17 | `docs/架构设计文档.md` | §5.0 增错误码行；§5.1 E45/E48 补两字段 | ~10 |

**生产代码 ≈ 195 行**（不含生成物与测试）；**测试 ≈ 180 行；文档 ≈ 50 行**；触及 **14 个手改文件**。

## 7. 实施批次

| 批次 | 内容 | 前置 | 验收 |
| --- | --- | --- | --- |
| P0 ✅ | Dockerfile 钉 rclone 版本（独立于本票，已完成） | — | `apk policy rclone` = `1.75.2-r0` |
| P1 | 常量 + 错误码 + 架构 §5.0（**规范先行**：新增 `ERROR_STATUS` 条目必须先登记文档） | — | `verify_redlines.sh` 全绿 |
| P2 | 服务层：`_conf_header` 三态 + 两个 dataclass 字段 + `list_remotes` / `list_configs` 锁死分支 | P1 | 单测：三态 × 锁死文案 |
| P3 | 启动自检 | P2 | 三条日志路径各有断言 |
| P4 | 契约层：schemas + router + openapi 生成 + 前端三态 + i18n | P2 | `pnpm run typecheck` 0 错误 |
| P5 | `validate_repo_url` 收窄（§5.5） | P2 | 双向测试：锁死 422 / 缺二进制放行 |
| P6 | 文档收口：部署指南 + 架构 §5.1 + 本文回填落地差异 | 全部 | 真机闭环 |

P2 是本方案的技术核心，P3/P4/P5 三者互不依赖，可并行。

## 8. 测试与护栏

- **文件头三态**：已加密（两种标记各一例）/ 明文 / 文件不存在 → `conf_encrypted` 与 `conf_locked` 取值正确，且**明文路径不调用任何子进程**（用 executor 计数器断言）；
- **锁死不静默降级**：`list_remotes` 在锁死时 `reason` 必须含「已加密」「口令」两个语义关键词，不得等于 rclone 原文或「超时」——这条直接对应票面「失败语义」待办；
- **两类不可用分离**（§5.5 双向）：锁死 → `validate_repo_url` 抛 422；缺二进制 / 事件循环不支持 → 继续放行；
- **启动自检三分支**：明文静默 / 已加密+通道可用 → 恰一条 info / 已加密+通道不可达 → 恰一条 error 且**不含口令与 conf 内容**（S3 断言）；
- **端点契约**：`GET /api/rclone/remotes` 与 `GET /api/rclone/configs` 回新字段；锁死时两接口仍 **200**（可渲染）而非 503——这是「面板不消失」的机器断言；
- **错误码登记**：`RCLONE_CONF_LOCKED` 在 `ERROR_STATUS`、架构 §5.0、`errors.py` 三处一致；
- **红线**：`bash scripts/verify_redlines.sh` 全绿；`python scripts/check_dockerignore.py`（compose 改动后）；
- **真机验收**（容器内，非 WSL 宿主机）：`encryption set --password-command` → 界面「查看列表 / 新增段 / 编辑段」三项全通 → `encryption remove` 复原；再删掉口令环境变量重启，确认面板仍可打开且提示指向恢复路径。

## 9. 风险与取舍

### 9.1 口令丢失 = 全部远端不可恢复（唯一实质代价）

不可逆、无后门（§4.11 实测）。**缓解只能前置**：

1. 开启前把明文 conf 复制到 `data/` 之外并明确告知这是唯一逃生通道；
2. 口令走 secrets 而非硬编码 env（`docker inspect` 不可见）；
3. 界面对锁死状态给常驻告警而不是一次性 toast。

### 9.2 已接受的风险

| 风险 | 处置 |
| --- | --- |
| 整盘读下加密无效（口令必须常驻进程环境） | **已知并接受**（Q1 裁决，部分读模型）；文档明写边界，不得宣称「防整盘泄露」 |
| 备份任务路径在口令缺失时挂到超时（§4.4） | 由启动自检在**故障发生前**暴露；任务失败原因仍可能指向超时，故自检日志是主要防线 |
| 宿主机侧编辑流程改变（加密后宿主 `rclone config` 也需先给口令） | 部署指南 `:315-317` 改写为该事实 |
| 换口令需脚本区分 `RCLONE_PASSWORD_CHANGE` | 若脚本两种调用都返回同一口令，rekey 会「成功但密码未变」（§4.6）；部署指南给出双分支脚本模板 |
| 存量明文 conf 不自动迁移 | Q3 裁决；界面识别为「未加密」并静默，不打扰 |

## 10. 本期不做

| 项 | 理由 |
| --- | --- |
| **界面「启用/停用加密」按钮** | **本期取方案 C：界面只读三态，启停走宿主 shell**（§5.3）。理由：①按钮属「改文件」而非「改配置」；②要做必须配齐「启用前 `check` 验证 + 失败自动 `remove` 回滚 + 强制明文备份 + 面板防锁死」四件套（约 +150~250 行），是独立一块；③界面自持致锁能力时，一次误点即可造成不可恢复状态。用户真正需要知道的是「我的备份还能不能跑」，三态已直接回答 |
| 界面录入配置口令 | 口令属部署侧凭据，与「业务配置走 DB」边界冲突（`settings.py:4`）；且写入 DB 会与 `enc:v2:` 密钥源纠缠 |
| 界面录入 / 生成 / 轮换口令 | UI 既不做录入也不做生成——口令由部署侧供给（§5.8）。**注意：这不是「rclone 不支持轮换」**——rclone **支持**原子 rekey（§4.6 实测），只是本期不把它做进界面；部署侧换口令走 `encryption set` + 双分支脚本即可 |
| 存量 conf 自动迁移 | Q3 裁决 |
| 接 KMS | Q2 的「保留接口」由 `RCLONE_PASSWORD_COMMAND` 天然满足——口令来源是任意脚本，接哪家 KMS 都不改应用代码 |

## 11. 与既有文档的关系

| 文档 | 关系 |
| --- | --- |
| 《rclone配置生成工具设计方案》v1.21 | **不替代**：那边管「段的增删改查与凭据混淆」，本文管「整份文件的落盘加密与口令通道」。交叉点只有一处——加密后写 conf 仍走同一 CLI，故那边无需改动 |
| 《暂缓存档-密钥托管架构-2026-11-04.md》 | 前置评估已收敛为「暂不实施」；本文不依赖它，`RCLONE_PASSWORD_COMMAND` 使口令来源与 provider 抽象解耦 |
| 《部署指南》§4.2 | 运维落地入口：口令配置、开启命令、逃生命中备份 |
| 《架构设计文档》§5.0 / §5.1 | 错误码与两个端点的新契约字段 |
| Git Issue #2 | 本文为实现依据；落地完成并通过 §8 验收后按其 Q5 关闭 |

## 12. 待验证（实现前须补测）

| 项 | 状态 |
| --- | --- |
| 非交互调用下 env 是否足够、是否强制 TTY | ✅ 已销（§4.2） |
| 加密后 `serve restic --stdio` 是否受影响 | ✅ 已销（§4.2） |
| conf 首次创建默认权限 | ✅ 已销（恒 600，与 umask 无关；已写入部署指南 `:322-325`） |
| 加密态如何探测 | ✅ 已销（§4.3 / §5.1） |
| **容器内（Alpine rclone 1.75.2-r0）复跑 §4.3 三态退出码** | ⏳ 未做——WSL 实测用的是官方 1.75.2；Alpine 打包版理论上同源，但需在真容器内确认一次 |
