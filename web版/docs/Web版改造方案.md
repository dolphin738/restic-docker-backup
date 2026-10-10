# restic-docker-backup Web 版改造方案（容器化部署 / 脱离 bash 执行层）

> 版本：v2.3.4（2026-10-10）  
> 前提：①Web 应用本身打包为 Docker 镜像部署；②执行层不复用 shell 脚本，编排逻辑全部内化到后端；  
> ③**无存量仓库、无存量快照、无存量配置需要迁移**——所有为兼容旧脚本做的妥协全部移除，按理想形态设计。
>
> v1.0 → v2.0：备份源模型重构（全盘只读挂载，动态增删源无需改 compose）、删除全部兼容层、  
> dump 目录收进容器自有卷、恢复路径自动映射。  
> v2.0 → v2.1：**恢复交互对齐 Duplicati**——`/host` 挂载模式改为 compose 级 `ro|rw` 配置（默认 rw），  
> 原位恢复不再要求重启容器；恢复向导提供「原位置 / 其他位置」二选一 + 覆盖策略选择。  
> v2.1 → v2.2：**后端凭据（S3/B2 等）改为 Web 界面可配**（存 SQLite、API 脱敏、子进程 spawn 时注入环境），  
> compose 环境变量降级为可选覆盖；仓库密码维持密码文件方案不变（性质不同，见 §6）。
>
> v2.2 → v2.3：**鉴权设计补全并定为 P1 第 0 项**（§6.1、§8）；新增**运行用户 × 挂载模式能力四象限**  
> 与**默认排除集**（§3）；恢复向导分期——P1 仅"恢复到时间戳子目录"，覆盖比对确认流移 P2（§3.4）；  
> 策略模型字段补全、**调度统一为 cron**、P1 即按多策略建表（§4.1、§5）；任务状态机 + 取消 API、  
> SSE 协议细节、SQLite 工程约定、API 端点补全（标注阶段）（§5）；镜像依赖安装层 / HEALTHCHECK、  
> bind-mount 属主要求、pg_dump 版本兼容声明、仓库类型 P1 收敛为 s3/b2/本地（§4）；§7 限制补充；  
> §8 重写（人周估算 + DoD + 工程化约定）。
>
> v2.3 → v2.3.1：新增**仓库类型接入归期表**（§4.2）——明确 restic 全部 9 类原生后端的接入归期
> （REST Server 归 P2，Swift/Azure/GCS 明确不接入），§4 仓库类型 bullet 与 §4.1 边界清单同步引用。
>
> v2.3.1 → v2.3.2：**内置排除集改为模式感知**（§3.1/§3.2）——语义核心集（8 条目）按模式派生前缀
> （host=`/host`、sources=`/sources`）+ 各模式附加条目；sources 模式新增四层防线
> （one-file-system 模式缺省开启、首备 dry-run 命名警告、部署文档警告、核心集派生兜底）。
>
> v2.3.2 → v2.3.3：**pg_dump 大版本策略定为"始终最新稳定 GA"**（§4、§8.1）——新 client 可 dump
> 全部旧版本 server（官方支持至 9.2），无需任何版本选择；Dockerfile 锁定 Alpine 小版本；
> dump 前探测兜底"新 PG GA 与 Alpine 稳定源收录之间的窗口期"（提示 edge 源自构建或等待镜像更新）。
>
> v2.3.3 → v2.3.4：PRD 待确认问题裁决落盘——**策略模型增限速字段**（`--limit-upload/--limit-download`，
> P2 实现，Q5）；**§4.2 归期调整**：rclone P2 优先、sftp P3（Q6）；中英双语/i18n、日志保留可配置、
> 浏览器支持清单等其余裁决记录于 PRD §8（Q3/Q7/Q8）。另：通知模块新增 MeoW 渠道与渠道级脱敏开关
> （承接 PRD US-G1，随 P2 交付）。

---

## 1. 无存量数据解锁了什么

| v1.0 妥协设计（为兼容旧脚本）                                       | v2.0 新设计                                                                                                                                     |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 备份源按"宿主机原绝对路径=容器内同路径"逐个 bind-mount，**增删源要改 compose 重启** | **`/` 整盘只读挂载为 `/host`**，备份源在 UI 任意勾选容器内路径，增删零重启                                                                                              |
| 快照路径 = 宿主机路径（存量快照依赖此语义）                                 | 快照路径带 `/host` 前缀，UI/恢复层统一映射，用户仍看到 `/etc/nginx` 这样的原路径语义                                                                                      |
| 提供 backup.env 导入器                                       | 删除。配置一步到位存 SQLite，UI 表单管理                                                                                                                    |
| `RESTIC_ENV_VARS` 白名单透传模拟                               | 删除。后端凭据（S3/B2 等）**Web 界面直接配置**（存 SQLite，API 脱敏，spawn 子进程时注入环境）；compose 环境变量为可选覆盖（v2.2）                                                       |
| `RESTIC_HOST` 留空回退 hostname 的兼容逻辑                       | 删除。host 标识必填（默认取部署时填的主机名），一次性配置                                                                                                              |
| dump 落盘目录必须在备份源挂载内（/var/backups/dump）                   | dump 落容器自有卷 `/data/dump`，天然可读、天然进备份范围、与宿主机解耦                                                                                                 |
| 原位恢复 = 临时挂 `/:/host-root`                               | `/host` 挂载模式由 compose 变量 `HOST_MOUNT_MODE` 决定（`rw` 默认 / `ro` 安全档）：rw 档下原位恢复是恢复向导里的一个普通选项（Duplicati 式），**无需重启容器**；ro 档面向安全敏感部署，原位恢复选项禁用并提示改挂载 |

---

## 2. 总体架构

核心决策不变：**单容器方案**——restic 官方静态二进制（单文件约 20MB）直接打进应用镜像，容器内直接执行，  
不挂 docker.sock、无嵌套容器。Web 服务进程无需 docker 组权限，风险面收敛为"读宿主机文件 + 写仓库/恢复卷"。

```
┌──────────────────── Docker 容器（应用镜像）────────────────────┐
│                                                                │
│  FastAPI 后端                                                  │
│  ├─ Web API + SSE 进度推送                                     │
│  ├─ 配置/策略模型（SQLite：配置、策略、任务历史、状态）          │
│  ├─ 调度器（APScheduler：cron + 补跑 + jitter + 重试）          │
│  ├─ Job Runner（asyncio 互斥 + subprocess 调 restic）           │
│  ├─ 输出解析（restic --json 逐行 → 结构化进度）                 │
│  ├─ 路径映射层（/host 前缀 ↔ 原路径语义）                       │
│  ├─ DB Dump（pg_dump/mysqldump 走 TCP → /data/dump）           │
│  └─ 通知（healthchecks / webhook / MeoW，渠道级脱敏）          │
│                                                                │
│  restic 0.19.x（静态二进制，版本随镜像固定）                    │
│  postgresql-client（始终最新稳定 GA 大版本）/ mysql-client        │
│  /data（SQLite + 任务日志 + dump）  /cache/restic（缓存）       │
└─────┬──────────────────┬───────────────────┬────────────────────┘
      │ /:/host (ro，可选rw)│ /restore 卷 (rw)   │ TCP
      ▼                  ▼                   ▼
 宿主机全盘（只读）    恢复目标卷          远程仓库 s3:/b2:/sftp:
                                          或本地仓库目录（bind-mount rw）
```

---

## 3. 备份源模型（v2.0 核心改进）

### 3.1 推荐方案：全盘挂载（默认 rw，可切 ro 安全档）

```yaml
volumes:
  - /:/host:rw        # 默认：读写挂载，支持 Duplicati 式原位恢复
  # 安全档：- /:/host:ro  （只读，原位恢复不可用，其余功能不变）
```

- **备份源 = 容器内 `/host` 下的任意路径**，UI 下拉/浏览选择（`/host/etc`、`/host/home/...`），  
  **增删源不需要改 compose、不需要重启容器**；
- 备份命令：`restic backup --json /host/etc /host/home --tag ...`，快照内路径形如 `/host/etc/nginx/nginx.conf`；
- 排除规则统一写容器内前缀。**防套娃的真实对象（v2.3 修正）**：dump 目录 v2.0 起在容器自有卷  
  `/data/dump`，不在备份源下，无需排除；host 模式下真正要排除的是 compose 部署目录下的 bind-mount  
  源被 `/host` 再次扫到——`/host/<部署目录>/data`（SQLite/任务日志/dump）、`/host/<部署目录>/cache`  
  （restic 缓存）、`/host/<部署目录>/restore`（恢复目标），已内置进 host 模式附加条目（见下）；
- UI 全程向用户展示**剥掉前缀**的原路径（`/etc/nginx`），映射只存在于存储层。

**内置排除集（v2.3.2 改为模式感知；P1 内置，UI 可追加、不可删减）**：

单一事实来源是**语义核心集**（与挂载模式无关的 8 个条目）：

`proc`、`sys`、`dev`、`run`、`var/run`、`var/lib/docker`、`tmp`、`lost+found`

由后端排除集生成器按 `RESTIC_WEB_MODE` 派生为容器内路径规则（一处实现，两模式不漂移）：

| 模式 | 派生前缀 | 核心集生效形态 |
|---|---|---|
| host | `/host` | `/host/proc`、`/host/sys`、`/host/dev`、`/host/run`、`/host/var/run`、`/host/var/lib/docker`、`/host/tmp`、`/host/lost+found` |
| sources | `/sources` | `/sources/proc`、`/sources/sys`、…（同构 8 条；通常无匹配、无害冗余，仅在用户把特殊内容挂进 `/sources` 时兜底） |

模式附加集（在核心集之上，每模式 1-2 条）：

| 模式 | 附加条目 | 说明 |
|---|---|---|
| host | `/host/<部署目录>/data`、`/host/<部署目录>/cache`、`/host/<部署目录>/restore` | 防套娃（见上） |
| sources | `/sources` 下仓库/缓存特征路径（本地仓库目录、`restic-cache` 类命名） | 防止仓库数据被自己备份；命中时 UI 警告 |

- 虚拟文件系统（proc/sys/dev/run）内容非持久且可能巨大，Docker 数据根目录由镜像/卷自行管理——  
  纳入备份既拖慢任务又产生无意义快照膨胀，故强制排除；
- **如实声明边界**：核心集是固定前缀路径，防不住"任意子路径挂载"（如 `/proc` 被挂成  
  `/sources/data/proc`）——因此还需 §3.2 四层防线兜底；
- "不可删减"在 sources 模式有极小概率误伤真实同名数据目录（如 `/sources/tmp`）——概率低且 tmp  
  本就是临时文件语义，可接受；内置条目在 UI 完整展示，用户知情；
- 首次备份前 UI 用 `restic backup --dry-run` 展示预计扫描范围（含内置排除生效结果），用户确认后再正式执行。

**安全权衡（如实声明）**：rw 挂载意味着容器被攻破可**写**全盘。缓解手段：非 root 运行（写操作受 UID 限制）、  
API 鉴权、参数白名单、危险操作三重确认 + 审计。安全敏感部署可将挂载切为 `ro` 档（改一行 compose），  
此时备份/浏览/恢复到其他位置全部可用，仅原位恢复禁用——**该承诺以 root 运行为前提**，  
非 root 档存在静默跳过（见 3.3 能力四象限）。另提供 3.2 最小暴露面备选。

### 3.2 备选：数据根目录模式（最小暴露面）

```yaml
volumes:
  - /srv/backup-data:/sources:ro   # 约定俗成的数据根目录
```

用户把需要备份的内容集中放到宿主机一个根目录（或在其中 bind-mount 子目录），UI 源从 `/sources` 下选。  
暴露面最小，但需要用户改变"数据放哪"的习惯。**两种模式并存，compose 一个变量切换，后端逻辑完全相同**  
（都只是"容器内哪些路径可被选为备份源"）。

**sources 模式的特殊内容防线（v2.3.2，四层）**：挂载内容完全由用户 compose 决定，若把伪文件系统 /
docker data-root 以任意子路径挂进 `/sources`，固定前缀排除集防不住，靠四层防线兜底：

1. **`--one-file-system` 模式缺省开启**：sources 模式下策略缺省 `true`（bind-mount 进来的伪文件系统 /
   docker 目录都是独立挂载点，天然被其排除）；host 模式缺省不变（用户可选）；
2. **核心集派生兜底**（§3.1）：常规命名（`/sources/proc` 等）直接排除；
3. **首备 dry-run 命名警告**：扫描范围中出现 proc/sys/dev/run/docker/lost+found 目录名时 UI 黄色警告
   （不强制拦截，用户可能真有同名数据目录）；
4. **部署文档警告**：明确"勿将伪文件系统 / docker data-root 挂入 `/sources`"，把错误挡在挂载层。

### 3.3 运行用户 × 挂载模式：能力四象限（v2.3 新增）

`/host` 挂载模式（rw/ro）与运行用户（root / 非 root 1000:1000）正交组合，能力边界如下：

| 组合          | 备份      | 快照浏览     | 原位恢复    | 说明                                                                |
| ----------- | ------- | -------- | ------- | ----------------------------------------------------------------- |
| root + rw   | ✅ 全量    | ✅        | ✅       | 默认推荐组合，能力完整                                                       |
| root + ro   | ✅ 全量    | ✅        | ❌（置灰）   | 安全档：**"ro 档备份/浏览/恢复全部可用"的承诺仅对 root 运行成立**                         |
| 非 root + rw | ⚠️ 部分跳过 | ⚠️ 部分不可读 | ⚠️ 部分失败 | 非 root 档读不到宿主机 root 属主 600/640 文件（/etc/shadow、/etc/ssl/private 等） |
| 非 root + ro | ⚠️ 部分跳过 | ⚠️ 部分不可读 | ❌       | 能力最受限，仅适合"数据全部归属普通用户"的专用场景                                        |

**非 root 档的关键行为（如实声明）**：restic backup 对单个文件读失败会**报错但继续**——  
备份侧表现为**静默跳过**（任务整体仍 success），用户不易察觉。缓解措施：

- 任务日志记录 skipped 文件计数与明细；
- skipped 计数超过阈值（可配，默认 100，0=任何 skipped 即告警；告警等级不区分，Q4 裁决）时任务标记告警并触发通知；
- 首次备份完成后 UI 明确展示 skipped 文件数，并提示"非 root 档存在不可读文件，如需全量备份请切 root 档"。

### 3.4 恢复向导（对齐 Duplicati 交互）

恢复向导为单页表单，两组单选 + 元数据开关：

**① 恢复到哪里？**

| 选项          | 行为                                                                                                 | 可用性                            |
| ----------- | -------------------------------------------------------------------------------------------------- | ------------------------------ |
| **原位置**     | `restic restore <id> --target /host`，快照路径 `/host/etc/nginx` 原样写回 = 宿主机 `/etc/nginx`                | `/host` 为 rw 档时可用；ro 档下选项置灰并提示 |
| **手动选择的位置** | 目标限定在可写挂载点内（`/restore/...`，rw 档下也可选 `/host` 下任意子目录）；路径映射层自动剥 `/host` 前缀，还原结构为 `/restore/etc/nginx` | 恒可用                            |

**② 如何处理已存在的文件？**

restic 自带语义只有覆盖（0.17+ 提供 `--overwrite=if-changed|always`），**没有** Duplicati  
"保留旧版本 + 文件名加时间戳"的内置选项。Web 层补齐：

- **P1 默认（唯一恢复路径）**：恢复到**目标下自动新建的时间戳子目录**（`/restore/etc-20261010-0138/...`），  
  从根上避免覆盖，等价实现"保留不同版本"；
- **"直接覆盖 + 覆盖清单确认流"整体移至 P2**：`restic restore --dry-run` 的原生输出是 verbose 日志  
  而非结构化的"将被覆盖文件清单"（0.19.1 上 `--dry-run --json` 是否输出结构化文件清单**待实测验证**）；  
  结构化覆盖清单需**自研比对逻辑**：快照 `ls --json` × 目标磁盘现状逐文件比对（按 mtime/size 判断  
  是否会被覆盖）。该比对层完成前，P1 不提供任何"直接覆盖"选项，从产品层面杜绝误覆盖。

**③ 元数据**：restic restore 默认还原权限/属主/时间戳。注意：原位恢复要完整还原 root 属主的文件  
（如 /etc 下），容器内执行用户需为 root——compose 提供 `user` 两档（默认 root 以保证恢复保真；  
安全敏感场景切 `1000:1000`，代价是 root 属主文件的恢复会部分失败并在任务日志中列明）。

**原位恢复确认门（rw 档，P2）**：原位恢复 = 向 `/host` 直接写回，必然走"直接覆盖"路径，  
因此依赖 P2 的覆盖比对清单——显示将被覆盖的文件清单（自研比对，§3.4 ②）→ 输入确认文本 → 二次确认 +  
"停止相关服务"提示。不因挂载已可写而省略。**P1 阶段原位恢复入口不提供**。

---

## 4. 镜像与部署

```dockerfile
FROM python:3.13-alpine3.24    # 锁定 Alpine 小版本：postgresql-client 随 stable 源收录的最新 GA 大版本
COPY --from=restic/restic:0.19.1 /usr/bin/restic /usr/local/bin/restic
RUN apk add --no-cache postgresql-client mysql-client tzdata
WORKDIR /app
COPY app/ /app/
RUN pip install --no-cache-dir .        # 或 uv sync --frozen（按锁文件确定性安装依赖）
RUN adduser -D -u 1000 backup
USER backup
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8000/api/health || exit 1
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
services:
  restic-web:
    image: restic-web:0.1.0
    user: "0:0"                    # 默认 root：保证原位恢复完整还原属主/权限；安全敏感场景改 1000:1000
    environment:
      TZ: Asia/Shanghai
      RESTIC_WEB_MODE: host        # host=全盘挂载 / sources=数据根目录
      # 后端凭据（AWS_*/B2_*）默认在 Web 界面配置，无需写在这里；
      # 如需 GitOps/secret 注入式部署，可在此设置环境变量覆盖 UI 值（可选）：
      # AWS_ACCESS_KEY_ID: ...
      # AWS_SECRET_ACCESS_KEY: ...
      # AWS_DEFAULT_REGION: ...
    volumes:
      - /:/host:rw                 # 默认 rw；安全档改 :ro（原位恢复禁用，其余功能不变）
      - /restore:/restore          # 恢复目标
      - /mnt/backup/restic-repo:/repo   # 仅本地路径仓库需要；对象存储后端可省
      - ./data:/data               # SQLite + 任务日志 + dump（含状态持久化）
      - ./cache:/cache/restic      # restic 缓存，务必持久化
      - ./secrets/restic-pass:/run/secrets/restic-pass:ro   # 仓库密码
    restart: unless-stopped
```

- restic 版本随镜像 tag 固定，升级 = 改 Dockerfile 中 COPY --from 的标签重新构建镜像（**有意设计**：  
  ①版本确定性——应用代码与 restic 二进制原子发布，`--json` 解析契约与二进制同步演进；  
  ②仓库格式安全——防止随手升级二进制把仓库格式顶上去导致无法回退）。
   ==实际成本极低：改一个 tag 字符串 + CI 自动构建==；镜像 tag 双版本标注（如 `restic-web:1.2.0-restic0.19.1`）；   
   restic 发布频率低，属低频主动升级动作，旧版本可一直稳定使用。不建议"宿主机挂载二进制替换"或  
   `COPY --from=latest`——前者破坏镜像自包含、后者构建不可复现。

- dump 客户端只装 client，通过 `PGHOST/PGPORT/PGUSER/PGPASSWORD` TCP 连接；
- **pg_dump 版本策略（v2.3.3）**：pg_dump 要求 **client 主版本 ≥ server**，新 client 可 dump 任意
  旧版本 server（官方支持至 9.2）——故镜像内**始终安装最新稳定 GA 大版本的 postgresql-client**，
  对全部旧版本 server 天然可用；唯一缺口是"新 PG 大版本 GA 后、Alpine 稳定源收录前"的窗口期，
  Job Runner dump 前探测服务器主版本兜底：不匹配时任务失败并 UI 提示"镜像内 client 为 N、
  server 为 M：请以 Alpine edge 源自构建镜像，或等待镜像更新收录"；
  mysql-client（MariaDB 系）无此严格校验；
- 镜像层 `USER backup` 仅在 compose **未指定** `user` 时生效（作为兜底安全默认）；本项目 compose  
  显式指定 `user: 0:0`（默认 root 档），**实际运行用户以 compose user 为准**；
- **bind-mount 属主要求（非 root 档，v2.3）**：`/data`、`/cache`、`./secrets` 均为 bind-mount，  
  宿主机目录属主若为 root，非 root（1000:1000）容器内将无法写入——SQLite 建库失败，  
  **容器直接起不来**。部署文档写明 `chown -R 1000:1000 ./data ./cache ./secrets`，或由 entrypoint  
  首启处理（root 档下自动 chown，非 root 档只能明确报错提示）；
- **仓库类型范围（v2.3）**：镜像内无 openssh-client / rclone，sftp 的 known_hosts 托管亦未设计——  
  **P1 仓库类型仅支持 s3 / b2 / 本地路径**；rclone 移 P2（Q6 裁决优先）、sftp 移 P3，列为**待设计项**  
  （需补镜像依赖、known_hosts 管理与 UI 联动表单）；restic 全部 9 类原生后端的接入归期全表见 **§4.2**；
- 运行用户两档可选：root（默认，原位恢复完整还原属主/权限）/ 1000:1000（安全档，root 属主文件恢复会部分失败）；
- **单实例假设**：锁与调度都在进程内，部署文档明确禁止水平扩容多副本。


### 4.1 部署项 vs 界面项边界清单

原则：**凡是不依赖"容器挂载了什么/怎么启动"的设置，一律进 Web 界面**。按此原则逐项划定边界：

| 设置项                                                                  | 归属                                   | 说明                                                                                        |                          |
| -------------------------------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------------------------------- | ------------------------ |
| 仓库地址（RESTIC_REPOSITORY）                                              | **UI**                               | 后端类型联动表单（P1：s3/b2/本地路径；REST Server P2；rclone P2（优先）、sftp P3（待设计）；Swift/Azure/GCS 不接入——归期全表见 §4.2）                                              |                          |
| 后端凭据（AWS\_*/B2\_*）                                                   | **UI**（env 可覆盖）                      | v2.2 已定                                                                                   |                          |
| host 标识（RESTIC_HOST）                                                 | **UI**                               | 一次性配置                                                                                     |                          |
| 数据库 dump 全套（DB 类型、PG/MYSQL 主机/端口/账号/**密码**/库名、保留天数）                  | **UI**                               | PGPASSWORD/MYSQL 密码属可轮换凭证，随凭据分级入 SQLite                                                   |                          |
| 通知（healthchecks URL、webhook 地址与模板、MeoW 昵称、各渠道脱敏开关、成功是否通知） | **UI**                               | 另配"发送测试消息"按钮                                                                              |                          |
| 调度参数（cron 表达式、jitter、重试次数/间隔、巡检周期、prune 独立间隔）                        | **UI**                               | APScheduler 动态重载，改完即生效无需重启容器；**调度统一为 cron 触发器**（策略级 cron + 全局缺省，v2.3 消除 interval/cron 双轨） |                          |
| 行为开关（AUTO_INIT、AUTO_UNLOCK、CHECK 启用/抽样比例/月度全量日、压缩开关、one-file-system） | **UI**                               | 后者两项为策略级字段                                                                                |                          |
| 时区（TZ）                                                               | **UI**                               | 后端用 zoneinfo 格式化时间戳 + spawn restic 时注入 TZ env；容器系统时区仅作回退                                  |                          |
| 缓存维护                                                                 | **UI 动作**                            | 缓存位置由挂载定死（compose），但"查看占用/一键清空"是界面操作（清缓存不丢数据，仅变慢）                                         |                          |
| 备份源 / 排除规则 / 保留档位（KEEP\_* / group-by）                                | **UI**                               | 即策略模型                                                                                     |                          |
| 管理员账号与密码                                                             | **UI**                               | 首启随机密码仅 Web 页面一次性展示 + 强制修改（§6.1，不入 docker logs）                                           |                          |
| 挂载清单（`/host` ro                                                      | rw、/restore、本地仓库 /repo、/data、/cache） | **compose**                                                                               | bind-mount 运行时不可变，容器固有约束 |
| 备份源模式（RESTIC_WEB_MODE=host/sources）                                  | **compose**                          | 随挂载模型定                                                                                    |                          |
| 端口映射、反向代理/HTTPS                                                      | **compose / 反代**（首选）+ **UI**（挂证书式内置 TLS）   | HTTPS 首选反代（部署文档含 Caddy 自动 HTTPS 示例）；镜像内置"挂证书"式 TLS 选项（UI 配证书/私钥路径，无自动签发，Q2 裁决）                                                    |                          |
| 运行用户（user: 0:0 或 1000:1000）                                          | **compose**                          | 安全档选择                                                                                     |                          |
| 仓库密码文件                                                               | **compose 挂载**                       | §6 已论证：不入库、不出 API                                                                         |                          |
| restic 版本                                                            | **Dockerfile**                       | §4 已论证：随镜像构建                                                                              |                          |
| 镜像版本 / AUTO_PULL                                                     | **compose**                          | 部署层                                                                                       |                          |

结论：compose 里剩下的**全部是"容器怎么跑"的部署事实**（挂载、端口、用户、镜像），没有任何一项  
"业务配置"——用户日常调整（仓库、凭据、策略、调度、通知、dump）100% 在 Web 界面完成，  
改配置不再需要碰 compose 或重启容器。

### 4.2 仓库类型接入归期表（v2.3.1）

restic 0.19.1 原生支持 9 类存储后端，接入归期如下（**有意不全接入**——镜像依赖、凭据模型与
UI 联动表单按后端类型逐个交付，避免 P1 范围失控）：

| restic 原生后端 | 归期 | 说明 |
|---|---|---|
| 本地目录 | **P1** | bind-mount `/repo`，零外部依赖 |
| Amazon S3 及 S3 兼容（MinIO / Wasabi / 阿里云 OSS / 腾讯 COS 等） | **P1** | 走统一 S3 协议 + AWS\_\* 凭据，一套表单覆盖大量云服务 |
| Backblaze B2 | **P1** | B2\_\* 凭据，v2.2 起凭据模型已定 |
| REST Server（restic 官方自托管服务端） | **P2** | 协议即 HTTP，复用既有凭据模型，无新镜像依赖；与本项目自托管用户画像契合，v2.3.1 补归期 |
| SFTP（SSH） | **P3（待设计）** | 优先序低于 rclone（Q6 裁决）；需补 openssh-client、known_hosts 托管方案与 UI 联动表单 |
| rclone（网关 40+ 云服务） | **P2（优先）** | Q6 裁决优先 rclone；需补 rclone 二进制及其 config 管理 |
| OpenStack Swift | **不接入** | 小众云；如有需求可经 rclone 后端间接使用（随 rclone 归期） |
| Microsoft Azure Blob | **不接入** | 同上 |
| Google Cloud Storage | **不接入** | 同上 |

"不接入"是**明确的产品决策**而非遗漏：UI 仓库类型下拉只出现归期为 P1/P2/P3 的类型，
文档如实声明，避免用户误以为支持全部 restic 后端。

---


## 5. 后端模块设计（无兼容包袱版）

| 模块         | 职责                                               | 实现要点                                                                                                                        |
| ---------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| 配置模型       | 仓库地址、host 标识、密码文件路径、**后端凭据（S3/B2 等）**、通知、调度参数、时区 | SQLite + UI 表单；密码文件字段只回"已配置/缺失"；凭据字段 API 脱敏（只回 has_value），支持"来源"标识（UI 设置 / 环境变量覆盖）；调度参数改动由 APScheduler 动态重载、即时生效            |
| 策略模型       | 完整字段见下表                                          | **调度统一为 cron**（策略级 cron 触发器 + 全局缺省，v2.3 消除 interval_min/cron 双轨）；**P1 数据模型即按多策略表设计**（表结构支持任意条策略），仅 UI 暴露一条默认策略，P2 开放多策略无需改表 |
| Job Runner | 执行 restic 命令                                     | subprocess + `--json` 逐行解析；前置 `restic cat config` 探测 + 可选 AUTO_INIT；按仓库维度 asyncio.Lock 互斥；失败自动 unlock（可配）                   |
| 调度器        | cron 触发 + 到期判定                                   | APScheduler：cron 触发器 + jitter + 重试；启动补跑（扫 last_run，当天已过点未成功立即跑）；prune 独立判到期                                                 |
| 路径映射       | `/host` 前缀 ↔ 原路径                                 | 备份源选择、快照浏览、恢复目标三处统一映射；UI 永远展示原路径                                                                                            |
| Dump 模块    | pg_dump/mysqldump                                | 走 TCP 落 `/data/dump`；`/data/dump` 作为默认备份源之一；空文件校验 + 按保留天数清理                                                                 |
| 保留策略       | forget + prune                                   | 每策略 `forget --tag --group-by --keep-* --json`；**UI 强制 dry-run 预览 → 确认 → --prune**                                           |
| 校验         | 抽样 + 月度全量                                        | 备份成功后按 subset 抽样；每月第 N 天全量 --read-data（APScheduler 任务）                                                                      |
| 通知         | healthchecks / webhook / **MeoW**                | /start、/fail、成功摘要 ping；飞书/钉钉 webhook 模板；**MeoW 推送渠道**（POST JSON 至 `api.chuckfang.com/{昵称}`，成功以响应 JSON `status` 判定、HTTP 恒 200，429 时记日志不重试推送）；**渠道级脱敏开关**——勾选后敏感变量（路径/错误详情/仓库/主机名/数据库名）渲染时**直接剔除、不显示占位符**，仅保留非敏感摘要（任务号/类型/状态/时长/字节数）并附任务详情深链；第三方渠道默认勾选、自建 webhook 默认不勾；UI 配置 + 测试按钮                                                    |
| 维护动作       | 缓存管理                                             | UI 查看缓存占用 / 一键清空（清空不丢数据，仅下次备份变慢）                                                                                            |
| 任务历史       | 审计与排障                                            | 每任务日志文件 /data/jobs/<id>.log + DB 元数据（起止/退出码/摘要/字节数）；容器重启将 running 标记 interrupted 并按策略补跑                                     |

**策略模型完整字段表（v2.3）**（P1 建表即含全部字段，UI 分期暴露）：

| 字段                                                                              | 类型    | 说明                         |
| ------------------------------------------------------------------------------- | ----- | -------------------------- |
| tag                                                                             | text  | restic --tag               |
| sources                                                                         | json  | 备份源路径列表（`/host` 前缀，映射层管理）  |
| excludes                                                                        | json  | 追加排除规则（内置排除集按模式派生、不可删减，§3.1）     |
| keep_last / keep_hourly / keep_daily / keep_weekly / keep_monthly / keep_yearly | int   | 保留档位                       |
| group_by                                                                        | text  | forget 分组（host,paths,tags） |
| cron                                                                            | text  | 调度 cron 表达式（策略级），空则回退全局缺省  |
| one_file_system                                                                 | bool  | `--one-file-system`（sources 模式缺省 true，host 模式缺省 false，§3.2） |
| compression                                                                     | text  | 压缩开关（auto/off/max）         |
| check_sample_ratio                                                              | float | 备份后抽样校验比例                  |
| monthly_full_day                                                                | int   | 月度全量校验日（--read-data）       |
| prune_interval_days                                                             | int   | prune 独立间隔（与备份调度分离）        |
| limit_up / limit_down                                                           | int   | 备份传输限速 KiB/s（restic `--limit-upload/--limit-download`，0=不限；Q5 裁决 P2 加入，进程级 nice/ionice 作 IO 优先级补充） |

**任务状态机（v2.3）**：

状态枚举：`pending / running / success / failed / interrupted / cancelled`。

| 转换                                         | 触发                                                                 |
| ------------------------------------------ | ------------------------------------------------------------------ |
| pending → running                          | Job Runner 取队头（按仓库维度互斥）                                            |
| running → success / failed                 | restic 退出码 0 / 非 0                                                 |
| running → cancelled                        | `POST /api/jobs/{id}/cancel`：SIGTERM → 宽限期 → SIGKILL，终态记 cancelled |
| running → interrupted                      | 容器重启：启动扫描将遗留 running 改标 interrupted，按调度策略决定是否补跑                    |
| failed / interrupted / cancelled → pending | 调度器补跑或用户手动重试                                                       |

- **手动触发与调度触发互斥**：同一仓库已有任务 running/pending 时，手动触发**拒绝**（409 +  
  明确提示"已有任务进行中"），不做排队——排队会让用户误以为任务已开始执行；
- **容器 stop 收尾**：容器收到 SIGTERM 时 Job Runner 先向 restic 子进程转发 SIGTERM 并等待宽限期；  
  超时后 restic 被 SIGKILL，**仓库锁残留**——下个任务前置探测（`restic cat config`）失败且锁存在时，  
  按 AUTO_UNLOCK 语义处理（可配，默认关：UI 提示手动解锁；开启则自动 `restic unlock`）。

**进度反馈（SSE 协议细节，v2.3）**：`backup/restore/check --json` 逐行 status 事件  
（percent_done/bytes_done/total_files）→ SSE 推送；页面刷新凭 job_id 重订阅，先回放日志尾部再续流。

- 每个事件携带递增 `id`，浏览器 `EventSource` 自动回传 `Last-Event-ID`，服务端据此从任务日志偏移处  
  **断点续传**（日志先落 `/data/jobs/<id>.log`，SSE 只是回放 + 追加）；
- 心跳：空闲时每 **15s** 发送 comment 行（`: keep-alive`），防代理/浏览器断连；
- 反向代理需**禁用缓冲**：nginx 加 `X-Accel-Buffering: no`（或等效响应头 + 服务端主动 flush）；
- `--json` 逐行解析属 CPU 密集处理，放入**线程池**（`asyncio.to_thread` / run_in_executor）执行，  
  避免阻塞事件循环。

**SQLite 工程约定（v2.3 新增）**：

- `journal_mode=WAL` + `busy_timeout=5000`；
- **单写连接**：aiosqlite 单例连接，全部写操作经同一连接**串行化**（利用 SQLite 单写多读特性，避免 SQLITE_BUSY）；
- schema 版本管理：`PRAGMA user_version` 记录版本号，启动时执行**幂等迁移器**（逐版本升级、可重复执行无副作用）。

**API 概览（v2.3 补全，每条标注所属阶段）**：

```
POST /api/auth/login / POST /api/auth/logout   P1   登录/登出（cookie session，§6.1）
GET  /api/auth/me                              P1   会话信息
GET  /api/health                               P1   健康检查（容器 HEALTHCHECK 用）
GET  /api/sources                              P1   可选备份源（按模式浏览 /host 或 /sources）
POST /api/sources/dry-run                      P1   首备扫描范围预览（backup --dry-run + 伪文件系统目录名警告）
POST /api/repo/test                            P1   仓库连通性测试（复用 restic cat config 探测）
GET  /api/repo/status                          P1   仓库状态（连接/锁状态/容量；仪表盘数据源）
GET/PUT /api/config                            P1   配置读写（凭据/密码文件脱敏）
GET/POST/PUT/DELETE /api/policies              P1   策略 CRUD（P1 单默认策略；P2 开放多策略）
POST /api/jobs                                 P1   触发任务（类型白名单：backup/forget/check/restore/unlock/init；dump 随 P2 Dump 模块生效）
GET  /api/jobs                                 P1   任务列表（最小：status=running/pending 进行中列表；P2 补分页/筛选）
GET  /api/jobs/{id}                            P1   任务状态与元数据
GET  /api/jobs/{id}/stream                     P1   SSE 进度流（Last-Event-ID 断点续传）
POST /api/jobs/{id}/cancel                     P1   取消运行中/排队任务（v2.3 新增）
GET  /api/snapshots                            P1   snapshots --json（路径已映射为原路径语义，分页）
GET  /api/snapshots/{id}/files                 P1   ls --json 文件树（分页 + 懒加载子目录）
POST /api/restore                              P1   执行恢复（P1 仅时间戳子目录默认路径）
POST /api/restore/preview                      P2   覆盖比对预览（自研比对层，§3.4）
POST /api/forget/preview                       P2   forget dry-run 预览
GET  /api/checks/{id}                          P2   check 任务结果
GET/PUT /api/notify                            P2   通知配置；POST /api/notify/test 发送测试消息
GET  /api/exclusions                           P2   内置排除集按模式派生结果（只读展示数据源）
GET  /api/cache + POST /api/cache/clear        P2   缓存占用查看 / 一键清空
GET  /api/audit                                P2   审计日志查询（append-only，§6.1）
PUT  /api/admin/password                       P1   管理员改密
```

**大规模列表（v2.3）**：snapshots 与文件树在长历史/大目录下可达数万条目——API 侧分页  
（limit/offset 或游标）或 `--json` **流式转发**，前端用**虚拟滚动**渲染，避免一次性 DOM 爆炸。

---

## 6. 安全设计

| 项             | 方案                                                                                                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 仓库密码          | 只支持密码文件（secret 只读挂载），永不入库、永不出 API——它是**不可轮换的加密根密钥**，泄露=全部备份明文                                                                                                                  |
| 后端凭据（S3/B2 等） | **Web 界面可配**（v2.2）：可轮换、可加最小权限 scope，性质不同于仓库密码；存 SQLite（明文落盘程度与 compose env 等价），API 脱敏；Job Runner spawn restic 子进程时注入环境；compose env 设置时**优先于 UI 值**并置灰对应输入框（对齐原脚本"调用方环境变量优先"哲学） |
| 权限面           | 无 docker.sock、无 docker 组；`/host` 默认 rw（原位恢复需要），提供 ro 安全档；运行用户默认 root（恢复保真），可切非 root 档                                                                                          |
| 鉴权            | **详见 §6.1**（cookie session、首启 Web 一次性展示随机密码、防爆破锁定）；HTTPS 双轨：首选反代（Caddy 自动 HTTPS 示例见部署文档）+ 内置"挂证书"式 TLS 选项（Q2 裁决）                                                                                                                  |
| 注入防护          | 表单字段 → 固定参数白名单拼装，无自由命令透传；路径输入规范化并校验在允许前缀内                                                                                                                                      |
| 危险操作          | forget --prune / unlock / 原位恢复：预览 + 确认文本 + 二次确认；全部入审计日志                                                                                                                        |

### 6.1 鉴权设计（v2.3 补全，P1 第 0 项）

**会话机制**：

- cookie session（`HttpOnly` + `SameSite=Strict`）+ **Origin 校验**做 CSRF 防护（写请求校验 Origin/Referer 同源）；
- 密码哈希：**argon2id**（环境不支持则 bcrypt），不存明文、不落日志；
- 会话有效期 24h，活跃**滑动续期**；登出即销毁服务端 session。

**首启引导**：首次启动生成随机管理密码，**仅在首启 Web 页面一次性展示 + 强制修改后才能进入其他页面**  
（v2.3 修正：不再打印进 docker logs——日志可能被收集系统或同宿主机其他用户读取）。

**暴力破解防护**：同一账号登录失败 5 次锁定 5 分钟（**指数退避**：每次锁定时长翻倍，上限 1 小时）；  
登录接口独立限流。

**SSE 端点鉴权**：`EventSource` 无法自定义 header，**不使用 query token**（会进反代访问日志）——  
SSE 鉴权与普通 API 走同一 cookie session 体系。

**凭据存储增量风险（v2.3）**：凭据明文存 SQLite（泄露面与 compose env 等价），部署要求 `/data` 目录  
`chmod 700` 且**排除出宿主机上其他备份工具的备份范围**（否则凭据随宿主机备份外流）；预留演进位：  
**可选加密存储（密钥源自密码文件派生）**，P1 不做。

**审计日志**：**append-only**——只提供追加与查询路径，不提供任何更新/删除 API；  
文件只增不删，保留仅按整段归档滚动（不覆盖已写入内容）。

---

## 7. 限制与权衡（如实声明）

1. **全盘挂载的暴露面**：默认 rw 档下容器被攻破可写宿主机全部文件（ro 安全档或 3.2 数据根目录模式可收敛，代价分别是失去原位恢复便利 / 改变数据存放习惯）；
2. **原位恢复的安全依赖 UI 确认门而非系统隔离**：默认 rw 档下，防止误覆盖全靠 dry-run 预览 + 确认文本 + 二次确认；需要系统级硬隔离的部署应切 ro 档；
3. dump 仅 TCP 模式，数据库须允许来自容器网络的连接；
4. 调度托管在容器内，容器停止即调度停止（restart: unless-stopped 缓解）；
5. restic --json 字段随版本演进，解析层做版本适配（镜像已固定版本，风险低）；
6. **非 root 档备份能力边界（v2.3）**：非 root 档读不到宿主机 root 属主 600/640 文件，备份侧**静默跳过**  
   （§3.3 四象限）；"ro 档备份/浏览/恢复全部可用"的承诺仅对 root 运行成立；
7. **备份期间宿主机 IO 显著升高（v2.3）**：全量备份会占满可用 IO（读全盘 + 写缓存/仓库），对同宿主机  
   其他负载有可感知影响；缓解：策略限速字段 `--limit-upload/--limit-download`（Q5 已裁决 restic 0.19.1  
   支持，P2 接入，见 §5 字段表）+ 进程级 nice/ionice 作 IO 优先级补充；P2 实现时对限速实际行为做  
   确认性实测并记录（非存在性验证）。

---

## 8. 分阶段实施（v2.3 重写）

**P1 —— 核心闭环（最小可部署镜像）**（估算：6–8 人周）

1. **鉴权与首启改密流程（第 0 项，先于一切功能）**：cookie session 登录 + 首启随机管理密码  
   一次性展示 + 强制修改（§6.1）。明确动机：**无鉴权的 P1 镜像 = rw 全盘挂载的 Web 服务裸奔**——  
   任何能访问端口的人都可配置仓库、触发恢复、向宿主机写文件；鉴权不就位，其他功能一律不上；
2. 镜像骨架 + compose 模板（host/sources 双模式，HOST_MOUNT_MODE ro|rw）+ 依赖安装层 + HEALTHCHECK；
3. 配置模型（仓库地址 + 后端凭据 UI 可配，仓库类型 s3/b2/本地）+ 密码文件接入；
4. Job Runner（backup/restore/unlock/init + **cancel**）+ 互斥锁 + 任务状态机 + 任务库 + `/host` 路径映射；
5. `backup --json` 解析（线程池）+ SSE 进度（Last-Event-ID 断点续传 + 心跳 + 反代禁缓冲说明）；
6. SQLite 工程约定落地（WAL + 单写连接 + user_version 幂等迁移器）；
7. 页面：登录/首启引导、**仪表盘**（仓库状态卡/最近任务/四象限能力卡；"下次调度时间"卡随 P2  
   调度器生效）、仓库配置、备份源选择（含内置排除集展示 + 首备 dry-run 确认）、  
   手动备份（实时进度）、快照浏览（snapshots/ls，分页 + 虚拟滚动）、  
   **恢复向导（P1 仅"恢复到时间戳子目录"默认路径）**。

**P1 DoD（验收场景）**：全新宿主机 **30 分钟内**完成部署（含 bind-mount 属主处理）+ 配置仓库 +  
完成首次备份 + 从快照恢复一份文件到时间戳子目录；**全程每一步都有鉴权保护**；首启改密流程可完整走通。

**P2 —— 运维完善**（估算：5–7 人周）

1. 调度器（cron 触发 + 补跑 + jitter + 重试）；
2. 策略管理器（多策略 UI 开放 / 自调度 / prune 独立到期，改表零成本——P1 已按多策略建表）；
3. **覆盖比对确认流**：自研"快照 ls × 目标磁盘现状"比对层 + `restore/preview` + 原位恢复确认门（§3.4）；
4. forget 预览-确认流、check、任务历史与审计；
5. 通知模块（UI 配置 + 测试按钮）、DB dump 配置 UI（含 pg 版本探测失败提示）、缓存维护动作、  
   行为开关（AUTO_INIT/AUTO_UNLOCK/CHECK）；
6. **rclone 仓库支持（P2 优先，Q6 裁决）**：rclone 二进制、config 管理、UI 联动表单；sftp 移 P3（known_hosts 托管待设计）；
7. **策略限速字段（Q5 裁决）**：`--limit-upload/--limit-download`（KiB/s，0=不限）进策略表与策略页，进程级 nice/ionice 作 IO 优先级补充（支持项随 P2 实现实测）。

**P2 DoD**：策略按 cron 自动触发且漏跑补跑正确；多策略互不干扰；forget 预览与实际裁剪结果一致；  
覆盖恢复前列表与实际覆盖文件一致；容器重启后任务状态与仓库锁恢复正确。

**P3 —— 增强（按需）**（估算：2–4 人周）

1. 多仓库/多主机视图；
2. Prometheus 指标、成功率报表；
3. 凭据可选加密存储（密钥源自密码文件派生，§6.1 演进位）。

### 8.1 工程化约定（v2.3 新增）

- **前端选型**：SPA（**Vite + Vue 3.5 + Naive UI**，2026-10-10 已裁决）；SSE 用原生 `EventSource`，  
  快照列表/文件树用虚拟滚动；
- **测试策略**：`--json` 解析层 **fixture 回归测试优先**——用固定版本 restic 的真实输出做 fixture，  
  解析器改动必须先补 fixture 再改代码；任务状态机转换与 SQLite 迁移器幂等性次之；
- **CI/CD 与镜像发布**：构建流水线 = lint + fixture 测试 + 镜像构建；发布 tag 双版本标注  
  （`restic-web:<应用版本>-restic<restic版本>`，如 `1.2.0-restic0.19.1`）；main 分支保护，发布由 tag 触发；  
  新 PG 大版本 GA 后例行跟进升级 postgresql-client（Alpine 稳定源收录通常滞后数周）；
- **日志轮转与任务历史 retention**：`/data/jobs` 任务日志**无限增长**问题——单任务日志按大小上限截断  
  （保留尾部），DB 任务元数据按保留天数（可配，默认 90 天）定期清理（清理动作入调度器）；  
  容器应用日志交由 Docker json-file 轮转（部署文档写明 `max-size`/`max-file` 建议）。

---

## 9. TL;DR

- 无存量包袱后，**v2.x 删掉了全部兼容层**（env 导入、白名单模拟、host 回退、双轨配置）。
- 备份源模型：**`/` 挂载为 `/host`**，UI 任意勾选源、增删零重启；快照路径带前缀、UI/恢复层自动映射；  
  安全敏感场景可切 ro 档或数据根目录模式。
- 恢复交互对齐 Duplicati：**「原位置 / 手动选择位置」二选一**（v2.1）；`/host` 默认 rw 挂载，  
  原位恢复不需要重启容器；**P1 恢复仅"时间戳子目录"默认路径，覆盖比对确认流在 P2**  
  （v2.3：dry-run 原生输出非结构化清单，需自研比对）。
- 鉴权与能力边界（v2.3）：**鉴权是 P1 第 0 项**——无鉴权 + rw 全盘挂载 = Web 服务裸奔；  
  非 root 档存在**静默跳过**（能力四象限，§3.3）；调度统一为 cron；P1 仓库类型仅 s3/b2/本地。
- dump 收进容器自有卷 `/data/dump`，与宿主机彻底解耦。
- 单容器 + restic 静态二进制 + 无 docker.sock 的架构不变；P1 核心闭环比 v1.0 更薄（少兼容层约 20% 工作量）。
- 凭据分级（v2.2）：**S3/B2 等后端凭据 Web 界面可配**（可轮换凭证，compose env 可选覆盖、优先于 UI）；  
  **仓库密码维持密码文件**（不可轮换的加密根密钥，永不入库）。
