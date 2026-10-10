# restic-docker 备份套件

基于官方 Docker 镜像 `restic/restic` 的可配置 VPS 备份方案：
**配置文件驱动 → 数据库 dump → 去重加密备份 → 保留策略 → 仓库校验 → 告警通知**。

## 文件清单

| 文件 | 用途 |
|---|---|
| `menu.sh` | **交互式管理菜单（推荐入口）**：集成初始化/定时/仓库/恢复/密码/体检全部手动操作 |
| `backup.env.example` | 全部配置项（仓库/密码/备份源/排除/保留策略/数据库/挂载/告警） |
| `restic-backup.sh` | 主备份脚本（备份 + forget/prune + check + 通知） |
| `restic.sh` | restic 命令行透传（snapshots / restore / check / init / unlock …） |
| `supervisor-timer.sh` | 可选：用 1Panel「进程守护(Supervisor)」模拟定时器（含当天补跑） |
| `systemd/restic-backup.{service,timer}` | 每天 03:30 定时备份，错过自动补跑 |

## 部署步骤（Debian/Ubuntu/RHEL 通用）

> **推荐**：装好 Docker 后直接 `sudo bash menu.sh`，按 首页[1] 安装与初始化 → 子菜单 1→2→3→4→5 顺序走完即可完成部署；
> 菜单同样集成定时任务安装、仓库管理、恢复向导等全部日常操作。下面的手工命令与菜单一一对应。

```bash
# 1. 安装
sudo mkdir -p /opt/restic-docker-backup && cd /opt/restic-docker-backup
#    把 restic-backup.sh / restic.sh / backup.env.example / systemd/ 拷到此处
sudo install -m 755 restic-backup.sh restic.sh /usr/local/bin/   # 可选：加入 PATH

# 2. 配置
sudo cp backup.env.example backup.env
sudo chmod 600 backup.env                    # 含密钥，必须收紧
sudo vim backup.env

# 3. 仓库密码（随机生成，务必离线另存一份！）
sudo sh -c 'umask 077; head -c 32 /dev/urandom | base64 > /etc/restic/pass'

# 4. 初始化仓库（首个备份前执行一次）
sudo ./restic.sh init

# 5. 演练验证（不写数据）
sudo ./restic-backup.sh --dry-run

# 6. 首次真实备份
sudo ./restic-backup.sh

# 7. 安装定时任务
sudo cp systemd/restic-backup.service systemd/restic-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now restic-backup.timer
```

### crontab 备选（无 systemd 时）

```cron
30 3 * * * /opt/restic-docker-backup/restic-backup.sh >> /var/log/restic-backup.log 2>&1
```

### 自调度（差异化频率）

需要不同目录不同备份频率时（如 1panel 快照每小时、系统配置每天），**无需多份配置文件、三种调度方式通用**：

**1. 配置**（backup.env 第 13/14 节）：

```bash
BACKUP_POLICIES="daily hot"           # 多策略
POLICY_daily_TAG="scheduled,例行备份"
POLICY_daily_SOURCES="/etc"
                                      # daily 未设 INTERVAL_MIN = 默认 1440（每天一次）
POLICY_hot_TAG="hourly,高频"
POLICY_hot_SOURCES="/home/dolphin738/1panel/backup/system_snapshot"
POLICY_hot_INTERVAL_MIN="60"          # 该策略每小时备份一次
PRUNE_INTERVAL_MIN="1440"             # forget+prune 清理间隔（分钟），独立判到期
```

**2. 调度器只需以最高巡检频率触发一个入口**（任选其一，零策略感知）：

| 调度方式 | 形态 |
|---|---|
| systemd timer | 一个单元，`OnCalendar=hourly` + `Persistent=true` |
| 1Panel 计划任务 | 一条每小时任务跑 `./restic-backup.sh` |
| 进程守护/Supervisor | supervisor-timer.sh 检测到 INTERVAL_MIN 自动切换间隔巡检模式（`CHECK_CYCLE_MIN` 默认 60 分钟） |

**3. 行为**：每次被唤醒时逐策略判定到期（距上次运行 ≥ INTERVAL_MIN 才备份，状态存 `STATE_DIR/last-<策略名>`），未到期秒级跳过；`Persistent=true` 或状态过期时开机自动补跑。

**4. 手动操作**：

```bash
./restic-backup.sh --policy hot          # 强制只跑某策略（无视到期）
./restic-backup.sh --prune-only          # 只执行全部策略 forget + prune（不备份）
```

**注意**：自调度模式下 `BACKUP_TAG`/`--tag`/`FORGET_TAG` 不生效（各策略按自身标签独立执行）；prune 与备份频率解耦、独立判到期，避免高频策略拖累全仓库重扫。

## 1Panel 接入（计划任务 或 进程守护，二选一；与 systemd timer 不要同时启用）

**方式 A —— 计划任务（简单，无补跑）**：计划任务 → 新建 Shell 脚本任务，每天 03:30，内容：
`/opt/restic-docker-backup/restic-backup.sh`，执行超时时间调大（如 12h）。

**方式 B —— 进程守护/Supervisor（等价 systemd timer，含补跑）**：进程守护 → 添加守护进程：

| 配置项 | 填写 |
|---|---|
| 名称 | restic-timer |
| 启动命令 | `/opt/restic-docker-backup/supervisor-timer.sh` |
| 运行用户 | root |
| 自动启动 | 是 |
| 环境变量（可选） | 需自定义触发时间时填 `RUN_HOUR="3",RUN_MIN="30"`（**Supervisor 语法：逗号分隔+引号包值，多个变量不能用空格分隔，否则报 CANT_REREAD**）；留空 = 默认 03:30。`TIMER_LOG_ENABLED="true"` 开启调度日志落盘（默认关，路径 `TIMER_LOG_FILE`，默认 `/var/log/restic-timer.log`） |

`supervisor-timer.sh` 是常驻循环：每天到点触发备份；**进程/服务器重启后若发现当天已过点且没备份过，立即补跑**（等价 systemd `Persistent=true`）。备份失败不重试，等下个周期；Supervisor 只负责把循环进程保活。

### 日志去向一览

| 部署方式 | supervisor-timer 调度日志 | restic-backup 备份日志 |
|---|---|---|
| 1Panel 进程守护 | stdout 由 Supervisor 接管（1Panel 界面「日志」可直接看）；可在 backup.env 设 `TIMER_LOG_ENABLED="true"` 额外落盘到 `TIMER_LOG_FILE`（默认 `/var/log/restic-timer.log`，**默认关闭**） | `/var/log/restic-backup.log` |
| systemd timer | `journalctl -u restic-backup.service` | `/var/log/restic-backup.log` |
| 1Panel 计划任务 | —（不经过 supervisor-timer） | `/var/log/restic-backup.log` + 1Panel 任务执行日志 |

说明：调度日志默认只输出 stdout；supervisor-timer.sh 的所有可配项（触发时间/重试/日志开关等）均遵循「1Panel 环境变量 > backup.env」的优先级；备份详细日志由 backup.env 的 `LOG_FILE` 控制。

## 验证命令

```bash
systemctl list-timers restic-backup.timer      # 下次触发时间
journalctl -u restic-backup.service -n 100     # 最近一次运行日志
./restic.sh snapshots                          # 快照列表
./restic.sh stats latest --mode restore-size   # 最新快照还原体积
```

## 恢复 Runbook（务必先演练过一次）

```bash
# 1. 查看快照
./restic.sh snapshots

# 2. 整机恢复到临时目录检查
sudo mkdir -p /restore && ./restic.sh restore latest --target /restore

# 3. 只恢复某个路径（原位覆盖恢复：--target / 时脚本自动把宿主机根挂到容器 /host-root，
#    并按快照内原路径写入宿主机——先停相关服务，避免恢复时文件被读写！）
sudo ./restic.sh restore latest --target / --include /var/www

# 4. 恢复指定时间点
./restic.sh snapshots --host $(hostname)
sudo ./restic.sh restore <snapshot_id> --target / --include /etc

# 注意：--target / 为原位覆盖模式（脚本自动映射宿主机根到容器 /host-root 并改写目标），
# 相对路径会被 realpath 规范化为绝对路径；主备份脚本 restic-backup.sh 的备份源仍按只读挂载。

# 5. 数据库恢复（对应 dump_databases 产物）
#    PG:  pg_restore -Fc -d appdb /restore/var/backups/dump/pg-appdb-XXXX.dump
#    MySQL: mysql appdb < /restore/var/backups/dump/mysql-appdb-XXXX.sql
```

## 常见后端配置速查

| 后端 | `RESTIC_REPOSITORY` | 需透传的环境变量（加进 `RESTIC_ENV_VARS`） |
|---|---|---|
| AWS S3 | `s3:https://s3.<region>.amazonaws.com/<bucket>/<path>` | `AWS_ACCESS_KEY_ID` `AWS_SECRET_ACCESS_KEY` `AWS_DEFAULT_REGION` |
| Cloudflare R2 | `s3:https://<accountid>.r2.cloudflarestorage.com/<bucket>` | 同上 |
| Backblaze B2 | `b2:<bucket>:<path>` | `B2_ACCOUNT_ID` `B2_ACCOUNT_KEY` |
| MinIO 自建 | `s3:http://10.0.0.5:9000/<bucket>` | 同 S3 |
| SFTP | `sftp:user@host:/srv/repo` | 无（挂载 `~/.ssh`，见 `EXTRA_MOUNTS`） |
| rclone | `rclone:<remote>:<path>` | 无（宿主机装 rclone，挂载 `~/.config/rclone`） |

## 踩坑点（脚本已处理，改动时注意）

1. **SELinux**（RHEL/CentOS/Rocky）：容器挂载必须带 `:z/Z` 标签，否则 `Permission denied`。默认 `SELINUX_LABEL=auto` 自动检测。
2. **缓存卷**：`CACHE_DIR` 必须持久化，否则每次备份重建索引缓存，大仓库会慢数倍。
3. **`--one-file-system`**：默认开启，防止把 `/proc`、Docker 卷等挂载点扫进去；备份源本身在独立挂载点不受影响。
4. **镜像版本固定**：不要改成 `latest`，升级时改 `RESTIC_IMAGE` 版本号并先在测试机验证仓库兼容性。
5. **密码丢失 = 数据全丢**：`/etc/restic/pass` 的内容请存入密码管理器 + 异地一份。
6. **数据库**：容器版 restic 内没有 `pg_dump`，脚本在宿主机侧先 dump 再备份 `DB_DUMP_DIR`；PG 在容器里时用 `PG_DUMP_METHOD=docker`。
7. **恢复文件属主**：容器默认 root 运行，恢复出的文件属主为 root，需要时 `chown -R` 或设置 `DOCKER_USER`。
8. **存量随机主机名快照**：主机标识修复前的快照 Host 列是容器随机 ID，按主机分组时自成分组，**不会被保留策略自动清理**。全部为测试数据 → 清空仓库重建（`rm -rf <仓库目录>/*` 后菜单 1-3 重新 init）；需保留 → 逐个处理：`./restic.sh snapshots --host <旧主机名>` 找出快照 ID，`./restic.sh forget <ID> --prune` 删除。

## 手动备份与标签用法

```bash
# 升级/大变更前手动备份：--tag 临时覆盖标签（支持中文、逗号分隔多标签），不改 backup.env
sudo ./restic-backup.sh --tag "pre-upgrade,升级前"

# 想让保留策略只清理例行快照（pre-upgrade 永不过期）：
# 在 backup.env 里设 FORGET_TAG="scheduled"，清理旧手动快照时手动执行：
sudo ./restic.sh forget --tag pre-upgrade --keep-last 5 --prune
```

## 风险提示

- 首次接入生产前：先用 `--dry-run` 演练，再手动跑一次全量并做一次完整恢复验证。
- `forget --prune` 会删除过期快照对应的数据块，保留策略配置不当会永久丢历史版本，修改 `KEEP_*` 后先用 `./restic.sh forget --dry-run <同样参数>` 预览。
- 变更窗口建议放在业务低峰；prune/check 会产生 IO 与流量。
