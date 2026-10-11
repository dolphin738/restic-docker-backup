# restic-docker-backup

以 **restic** 为核心的 Docker 备份方案，单容器、无 docker.sock、仓库密码与业务配置分离。
本仓库包含两套并存的实现形态：

| 形态 | 目录 | 状态 | 说明 |
| --- | --- | --- | --- |
| Shell 版 | `shell版本/` | ✅ 已交付 | `backup.env` + cron 驱动的纯脚本方案（详见其自带 README） |
| Web 版 | `web版/` | ✅ P1 已交付（v1.0.0） | FastAPI + Vue 3.5 管理端，浏览器完成仓库/备份源/备份/恢复全流程 |

> Web 版 P1（核心闭环）已完成：鉴权与首启改密、仓库配置、备份源选择、手动备份（SSE 实时进度）、
> 快照浏览、**恢复到时间戳子目录**。P2（调度器、多策略、原位恢复覆盖比对、通知、dump、审计等）见文末路线图。

---

## 1. 快速开始（Web 版，5 步）

前置：宿主机已装 Docker 与 Compose v2。

```bash
# ① 取部署文件（compose 模板即 web版/docker/docker-compose.yml）
mkdir -p restic-web && cd restic-web
mkdir -p data cache secrets restore

# ② 生成仓库密码文件（加密根密钥，永不入库、永不出 API；丢失即无法解密备份）
openssl rand -base64 48 > secrets/restic-pass
chmod 600 secrets/restic-pass

# ③ 启动（默认 host 模式 + rw 挂载，端口 8000）
docker run -d --name restic-web \
  -p 8000:8000 \
  -e TZ=Asia/Shanghai \
  -e RESTIC_WEB_MODE=host \
  -e HOST_MOUNT_MODE=rw \
  -v /:/host:rw \
  -v "$PWD/restore:/restore" \
  -v "$PWD/data:/data" \
  -v "$PWD/cache:/cache/restic" \
  -v "$PWD/secrets/restic-pass:/run/secrets/restic-pass:ro" \
  --restart unless-stopped \
  dolphin738/restic-web:1.0.0-restic0.19.1
```

更推荐直接用仓库内模板（含注释与两种挂载模式切换说明）：
`web版/docker/docker-compose.yml` → 复制后 `docker compose up -d`。

> 部署模板**不含 `build:` 段**（只用已发布镜像）。需要本地构建时用叠加文件：
> `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`
> （详见 `web版/docs/部署指南.md` §2.5）。

```bash
# ④ 打开 http://<宿主机IP>:8000 —— 首启引导页会一次性展示随机管理密码
#    务必立即保存，并用它作为「旧密码」设置新密码（展示过即失效，端点返回 410）

# ⑤ 在界面完成：仓库配置（s3 / b2 / 本地路径）→ 备份源勾选 → 首备 dry-run 预览 → 备份
```

> **本地路径仓库**：额外挂一个卷到 `/repo`（如 `-v /mnt/backup/restic-repo:/repo`），
> 对象存储（S3/B2）后端无需该卷。

---

## 2. 挂载模式（二选一）

| `RESTIC_WEB_MODE` | 挂载 | 备份源 | 适用场景 |
| --- | --- | --- | --- |
| `host`（默认） | `/:/host:${HOST_MOUNT_MODE}` | UI 在 `/host` 下任意勾选，增删零重启 | 通用；`rw` 档还支持向 `/host` 下目录恢复 |
| `sources` | `<数据根目录>:/sources:ro` | 仅该目录内 | 最小暴露面，安全敏感场景 |

`HOST_MOUNT_MODE`：
- `rw`（默认）—— 支持"恢复到 `/host` 下子目录"；
- `ro` —— 仅备份与浏览，恢复只能落到 `/restore`（界面会给出置灰原因）。

> `docker-compose.yml` 中卷串里的 `${HOST_MOUNT_MODE}` 由**宿主机 compose 环境**插值（不是容器 env），
> 切 `ro` 时需同时在 `docker/.env` 写 `HOST_MOUNT_MODE=ro`（详见 `web版/docs/部署指南.md`）。

---

## 3. 卷与路径

| 容器路径 | 用途 | 必须持久化 |
| --- | --- | --- |
| `/data` | SQLite（`db/jobs`）、任务日志、dump | ✅ 是（丢失=任务历史丢失） |
| `/cache/restic` | restic 缓存 | ✅ 是（丢失=每次全量重扫，很慢） |
| `/run/secrets/restic-pass` | 仓库密码文件（只读挂载） | ✅ 是（丢失=备份不可解密） |
| `/restore` | P1 恢复落地目录（自动建时间戳子目录） | 按需 |
| `/repo` | 仅"本地路径仓库"需要 | 按需 |

运行身份：镜像默认以 `1000:1000` 启动，`docker-compose.yml` 显式指定 `0:0`（root 档，保留属主/权限）。
**改非 root 档前**须在宿主机执行 `chown -R 1000:1000 ./data ./cache`，否则容器拒绝启动（entrypoint 会明确报错）。

---

## 4. 端口、健康检查与反向代理

- 监听 `8000`（`PORT` 可覆盖）；`GET /api/health` **免鉴权**，供容器 HEALTHCHECK 使用。
- 前端 SPA 产物已**内置在镜像里**（`/app/webroot`），8000 端口同时提供页面与 API，
  不需要额外的静态服务器或第二个容器（单容器方案）。
- 内置 TLS：在界面/配置中填 `server.tls_cert` 与 `server.tls_key` 后由应用直接以 HTTPS 启动（Q2 裁决）；
  未配置时为纯 HTTP，HTTPS 建议在反向代理终结。
- **SSE 实时进度必须禁用反代缓冲**：后端已下发 `X-Accel-Buffering: no`，Nginx 侧还需
  `proxy_buffering off; proxy_read_timeout 3600s;`，且空闲心跳默认 15s
  （`SSE_HEARTBEAT_SECONDS`），反代 idle timeout 小于该值时需下调心跳间隔。

---

## 5. 版本与镜像标签

三条独立版本线（升级方式见 `web版/docs/依赖升级指南.md`）：

| 版本线 | 事实来源 | 当前值 |
| --- | --- | --- |
| 应用 | `web版/backend/pyproject.toml` / `frontend/package.json` | `1.0.0` |
| restic | `web版/docker/Dockerfile` 的 `ARG RESTIC_VERSION` | `0.19.1` |
| 基础镜像 | Dockerfile `FROM python:3.13-alpine3.24` | alpine3.24 |

镜像发布到两处，标签由 CI（`scripts/ci-docker.sh`）产出：

- CNB 制品库：`docker.cnb.cool/dolphin738/restic-docker-backup`
- Docker Hub：`dolphin738/restic-web`

标签规则：**`latest` + commit 短 sha（每次构建）**，若该 commit 恰好打了 git tag 则**追加双版本标签**
`<应用版本>-restic<restic版本>`（如 `1.0.0-restic0.19.1`）。

---

## 6. 文档索引

| 文档 | 内容 |
| --- | --- |
| `web版/docs/部署指南.md` | 部署全流程、挂载模式、反代、TLS、升级、故障排查 |
| `web版/docs/Web版PRD.md` | 需求与验收标准（P0/P1/P2 需求优先级） |
| `web版/docs/Web版改造方案.md` | 设计方案 v2.3.x（路径映射、能力四象限、状态机、SSE） |
| `web版/docs/架构设计文档.md` | 实现层架构（v1.2，端点/服务/数据模型） |
| `web版/docs/依赖升级指南.md` | 三条版本线的升级工作流与发布 SOP |
| `web版/AGENTS.md` | AI 协作约定与开发红线 |
| `web版/docs/代码审查规范.md` | 代码审查清单 |
| `CHANGELOG.md` | 版本变更记录 |

## 7. 本地开发

```bash
# 后端（FastAPI，:8000）
cd web版/backend && uv run uvicorn app.main:app --reload

# 前端（Vite + Vue 3.5，:5173，/api 代理到 127.0.0.1:8000）
cd web版/frontend && pnpm dev

# Windows 一键脚本（菜单式：启停/测试/lint/红线校验）
web版/dev-scripts/dev.ps1
```

质量门禁（与 CI 一致）：后端 `ruff check app tests` + `pytest`（117 passed）；
前端 `vue-tsc` + `vitest` + `vite build`。CI 为**手动触发**（CNB 分支页"构建并推送镜像"按钮），无自动构建。

## 8. 路线图

**P2（运维完善）**：调度器（cron + 漏跑补跑 + jitter + 重试）、多策略 UI（P1 已按多策略建表，无需改表）、
覆盖比对确认流与原位恢复、`forget` 预览-确认流、check 校验、通知（healthchecks/webhook/MeoW + 渠道级脱敏）、
DB dump 配置 UI、缓存维护、任务历史与审计、rclone 仓库支持（sftp 移 P3）。

**P3（增强，按需）**：多仓库/多主机视图、Prometheus 指标与成功率报表、凭据可选加密存储。

## 9. 许可证

本项目采用 **GNU Affero General Public License v3.0（AGPL-3.0-or-later）**，完整条款见 [`LICENSE`](./LICENSE)。

- AGPL 的**网络交互条款**：若你修改本项目并通过网络向他人提供服务，须向使用者提供修改后的完整源码。
  本仓库源码公开于 <https://cnb.cool/dolphin738/restic-docker-backup>，网络用户可直接获取。
- 随镜像分发的 **restic** 为独立程序，遵循其自有许可证（BSD-2-Clause），不受本项目许可证覆盖。
- 仓库密码文件（`secrets/restic-pass`）与业务配置**永不入库**，请勿提交真实凭据。
