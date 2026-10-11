# 更新日志

本项目遵循 [Conventional Commits](https://www.conventionalcommits.org/) 提交规范，
版本采用语义化版本（SemVer）。仓库同时包含 `shell版本/` 与 `web版/` 两套实现，
下方版本记录以 **Web 版**为主。

## [1.0.0] — 2026-10-12（Web 版首个正式发布）

首个可部署版本，交付 P1「核心闭环」：鉴权 + 仓库配置 + 备份源 + 手动备份 + 快照浏览 + 恢复到时间戳子目录。

### 新增

- 仓库配置页状态卡新增**「初始化仓库」**动作：E30 显示未连接时出现（三步确认门，入审计）。
  此前 API 白名单虽有 `init` 任务类型，但 UI 无任何入口——本地路径仓库（`/repo`）不做 init
  就永远「未连接」，P1 本地仓库流程实际不可用（`AUTO_INIT` 仍归 P2 行为开关）。

- **路径选择改为「浏览 + 手动输入」双入口**（E33）：
  - 仓库地址在本地路径类型下新增「浏览路径 / 手动输入路径」双 Tab；浏览态提供容器根目录树、
    **挂载点快跳标签**（`/proc/mounts` 过滤伪文件系统得出）、面包屑、**点目录名即选为仓库地址**；
  - 备份源页树浏览支持**点目录名加入右侧已选**（三角形仍只负责展开/收起，两种手势不再打架），
    并在已选列表上方提供**手动输入路径**输入框（越界在 E31 dry-run 时被 `PATH_OUT_OF_PREFIX` 拦截）；
  - 后端新增 `GET /api/fs/browse`：容器文件系统单层只读列举，条目带 `is_mountpoint`，
    根目录请求额外回 `mounts` 快跳列表。与 E05 的差异：**无 `/host`|`/sources` 白名单**（仓库可落在
    `/repo` 或任意自挂点）、**回容器绝对路径而非 display_path**（`repo.url` 本就容器形态）。
    单层扫描与游标分页抽到 `services/dir_scan.py`，与 E05 共用（D8）。

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
- `scripts/`（宿主机侧校验脚本：`check_dockerignore.py` / `scan_image.sh` / `verify_redlines.sh`）
  排除出 Docker 构建上下文——Dockerfile 从不 COPY 它们，原先每次构建白白传输 27.7KB。
- `scripts/check_dockerignore.py` 新增 `--context`：无 docker 也能出"构建上下文体积账"，
  按顶层目录聚合、列出 `frontend/` 明细、标出"进了上下文却从不被 COPY"的目录，
  并单列"跨阶段 COPY"（如 `/fe/dist`）以区分"上下文来源"与"镜像内容"。
- 仪表盘「能力四象限」卡改为「运行模式」卡：只展示当前部署形态对应的模式
  （完整 / 全览 / 受限 / 沙盒，命名按"视野 × 恢复"能力形态），不再四格全铺；
  附"模式由部署形态决定，调整 compose 后重启容器生效"提示。
- `services/dir_scan.py`（新）：目录单层扫描 + 游标分页从 `routers/sources.py` 抽出为公共服务，
  E05 与 E33 共用同一实现；E05 侧测试替身从 `_scan_dir` 改为 `scan_dir_async`（异步组合优于同步注入）。

### 修复

- CI 首跑两处失败：`.gitignore` 的 `restore/` 误伤 `frontend/src/pages/restore/`（TS2307）；
  docker 段内联 shell 参数被吞（收敛为 `scripts/ci-docker.sh`）。
- 发布标签语义：`git describe --exact-match` + 构建前 `git fetch --tags`，
  保证仅对已打 tag 的 commit 产出双版本标签 `<应用版本>-restic<restic版本>`。
- **镜像不含前端产物、后端无静态路由，浏览器打开首页返回 `{"code":"NOT_FOUND"}`**（P1 页面完全不可用）：
  - Dockerfile 新增 `frontend-build` 阶段（`node:22` + pnpm `--frozen-lockfile` + `pnpm run build`），
    产物 COPY 进运行时的 `/app/webroot`（构建期依赖不进最终镜像）；`.dockerignore` 放开 `frontend/` 源码；
  - 后端新增 `settings.static_dir`，`create_app()` 在业务路由之后挂载 `StaticFiles(html=True)`，
    404 处理器仅对"GET/HEAD + 非 `/api` 前缀 + 末段无扩展名"回落 `index.html`（SPA 深链）；
  - 新增 `tests/test_api/test_spa.py` 8 例锁定边界（首页可达、深链回落、资源 404、
    `/api` 不被遮蔽、无 webroot 时仍是 JSON 404）。
- **容器日志没有时间戳，且应用启动日志被静默丢弃**：
  - uvicorn 默认 `log_config` 格式串是 `%(levelprefix)s %(message)s`，访问日志同样无时间字段，
    容器日志里无法判断时序（排查 SSE 断流、健康检查节奏、restic 长任务耗时全靠猜）；
    改为显式传入 `log_config`，default/access 两个 formatter 都带 `%(asctime)s` 与
    `datefmt=%Y-%m-%d %H:%M:%S%z`（带 `%z` 偏移，避免把容器 `TZ` 误读成 UTC），
    并保持 `disable_existing_loggers=False` 以免摘掉 root handler；
  - `logging.basicConfig` 原先在 lifespan 里调用，而 uvicorn 以 `app.main:app` 字符串**导入**本模块，
    导入期 root 尚无 handler → `create_app()` 与 `main()` 的 INFO 级启动日志被 lastResort
    按 WARNING 门槛丢弃（表现：容器里只看得到几行 uvicorn 日志）。改为模块导入期完成配置；
  - 新增 `tests/test_logging.py` 6 例锁定格式串、"导入期 root 已有 handler"与 `LOG_LEVEL` 生效。
- **保存仓库配置后，仓库状态卡仍显示「未连接」**：E30 有 30s 进程内 TTL 缓存，但 E07 配置写入
  不失效它、前端保存后也不重取 → 用户改完 `repo.url` 看到的还是旧状态（30s 内连手动「刷新」
  都是旧值）。修复：E07 写入即失效缓存；任务终态（init/unlock 改变仓库可连性）经 JobRunner
  新增的 `on_finish` 回调失效；前端保存成功后立即重取状态卡。
- 探测链路兜底：restic 二进制缺失/不可执行时 `run_json` 抛 `OSError` → E29/E30 直接 5xx；
  改为返回退出码 -1 的结构化失败（`failure_reason` 生成文案），维持"探测失败不 5xx"契约。

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
