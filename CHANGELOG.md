# 更新日志

本项目遵循 [Conventional Commits](https://www.conventionalcommits.org/) 提交规范，
版本采用语义化版本（SemVer）。仓库同时包含 `shell版本/` 与 `web版/` 两套实现，
下方版本记录以 **Web 版**为主。

## [1.0.0] — 2026-10-12（Web 版首个正式发布）

首个可部署版本，交付 P1「核心闭环」：鉴权 + 仓库配置 + 备份源 + 手动备份 + 快照浏览 + 恢复到时间戳子目录。

### 新增

- **鉴权与首启改密**（P1 第 0 项）：cookie session 登录（E01/E02/E03）、首启随机密码一次性展示
  （`GET /api/auth/bootstrap`，展示后 410）、强制改密状态机、管理员改密（E28）、登录失败递增锁定。
- **备份闭环**：目录浏览（E05）、配置读写（E06/E07）、策略读写（E08–E11，P1 仅默认策略可写）、
  触发任务（E12）、任务列表/详情（E13/E14）、SSE 进度流（E15，`Last-Event-ID` 断点续传）、
  取消任务（E16）、日志尾部（E27）。
- **快照与恢复**：快照列表（E17）、文件树（E18）、恢复到时间戳子目录（E19，**P1 唯一恢复路径**）。
- **仓库与排除集**：连通性测试（E29）、仓库状态（E30）、首备扫描范围预览（E31）、内置排除集只读（E32）。
- **前端**：Vite + Vue 3.5 + Naive UI，8 个核心页面（登录/首启/仪表盘/仓库/备份源/手动备份/快照浏览/恢复向导）。
- **镜像**：单容器 `python:3.13-alpine` + restic 静态二进制（`0.19.1`），含 HEALTHCHECK 与非 root 兜底。
- **CI**：CNB 手动触发流水线（`web_trigger`），后端 ruff + pytest、前端 vue-tsc + vitest + build、
  镜像构建并推送 CNB 制品库与 Docker Hub。
- **文档**：新增根目录 `README.md`、`web版/docs/部署指南.md`、`web版/docs/依赖升级指南.md`。

### 变更

- 应用版本口径统一为 `1.0.0`（原 `0.1.0` 为开发期占位），与 git tag 一致。
- `docker-compose.yml` 镜像名补全命名空间：`dolphin738/restic-web:1.0.0-restic0.19.1`。
- 部署模板与本地构建拆成两份：`docker-compose.yml` 移除 `build:` 段（只用已发布镜像），
  新增叠加文件 `docker-compose.build.yml`（`build:` 段 + 本地专用镜像名 `restic-web:1.0.0-local`）。
  修掉"本地构建产物顶替同名发布镜像、外观无从分辨"的隐患。
- 依赖安装改为 `uv sync --frozen`（`uv.lock` 锁定），CI 与 Dockerfile 同步，构建可重复。
- 分支模型收敛为**主干开发**（`main` 唯一长期分支，dev 分支已删除）。

### 修复

- CI 首跑两处失败：`.gitignore` 的 `restore/` 误伤 `frontend/src/pages/restore/`（TS2307）；
  docker 段内联 shell 参数被吞（收敛为 `scripts/ci-docker.sh`）。
- 发布标签语义：`git describe --exact-match` + 构建前 `git fetch --tags`，
  保证仅对已打 tag 的 commit 产出双版本标签 `<应用版本>-restic<restic版本>`。

### 性能

- 镜像瘦身 `-25MB`：uv 二进制与其缓存在同一 RUN 层内"用完即删"（`pip uninstall uv`）。

### 许可证

- 项目改用 **GNU AGPL-3.0-or-later**（根 `LICENSE`，SPDX 标识进入 `pyproject.toml` /
  `package.json` / 镜像 OCI 标签）。

### 已知边界（P1）

- 无调度器（手动触发）；单默认策略；限速字段只读。
- 无覆盖恢复入口：恢复仅落地到 `/restore/<源目录名>-<时间戳>/`。
- 无通知、dump、缓存维护、审计日志页（均归 P2）。
- 前端 `naive-ui` 产物 chunk 约 472KB（P2 分包优化）。

> 预发布标记 `v1.0.0-p1`（2026-10-11，commit `a5d2d8c`）为 P1 代码冻结里程碑，保留于仓库历史。

## 基线

- `design-baseline-v2.3.4`（2026-10-10）：P1 设计冻结（方案 v2.3.4 + PRD + 架构设计文档）。
- `shell版本/`：独立交付的脚本方案，变更不纳入上方版本线（详见该目录自带 README）。
