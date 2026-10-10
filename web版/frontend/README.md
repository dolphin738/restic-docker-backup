# restic-docker-backup Web 版 · 前端

Vite 6 + Vue 3.5 + TypeScript + Naive UI 的管理控制台前端（P1 阶段一：骨架与认证链路）。

## 本地开发

后端需先启动在 `http://localhost:8000`（FastAPI，统一 `/api` 前缀）。

```bash
pnpm i
pnpm dev      # http://localhost:5173
```

其他脚本：

```bash
pnpm typecheck   # vue-tsc --noEmit
pnpm build       # 类型检查 + 生产构建
pnpm preview     # 预览构建产物
```

## 与后端对接要点

- 鉴权：cookie `rw_session`（HttpOnly / SameSite=Strict），所有请求 `credentials: 'include'`；
  写请求依赖 vite dev proxy 的同源 Origin，**不额外加自定义 header，也不使用 query token**。
- 代理：`vite.config.ts` 中 `/api` → `http://127.0.0.1:8000`，`ws: false`，SSE 不被缓冲。
- 错误：统一 `{ code, message, data }`；`src/api/http.ts` 转 `AppError`，401 → 登录页、
  `403 BOOTSTRAP_REQUIRED` → 首启引导页，其余弹全局提示。
- 分页：列表 `limit/offset`，目录树/快照文件用不透明游标，**前端不解析、不拼接游标内容**。
- 路径：界面一律展示"原路径"（如 `/etc/nginx`），容器前缀 `/host` 由后端处理，前端不存不拼。
- SSE：`GET /api/jobs/{id}/stream`，事件 `progress|log|done|error`，帧 `id` 即日志行号；
  订阅逻辑集中在 `src/composables/useJobStream.ts`：开流前先回查 E14 取 `last_event_id` 作起始游标
  （已终态则不开流，本地收尾），45s 无数据判定疑似断链 → 回查任务状态 → 按游标续传。
  注意：**断链续传不补齐日志**，日志的完整来源是 `GET /api/jobs/{id}/log`。
- 首启引导：`PUT /api/admin/password` **不下发 session**，前端必须在改密成功后用新密码重新登录。
- `GET /api/repo/status` 服务端有 30s 缓存，仪表盘按 60s 轮询 + 手动刷新，不做秒级轮询。

## 目录

```
src/
  api/          统一请求出口与端点模块（http / auth / health / jobs / repo / types）
  components/   JobProgress（SSE 进度条）、QuadrantCard（能力四象限）
  composables/  useJobStream（SSE 订阅层）
  i18n/         vue-i18n 装配与中英词条
  layouts/      BasicLayout（侧边导航 + 顶栏）
  pages/        login / bootstrap / dashboard / PlaceholderPage
  router/       路由表 + 全局前置守卫
  stores/       ui（me 缓存、主题、语言、全局 message）
```

## 约定

- 所有界面文案走 `$t()`，禁止硬编码中文（`src/i18n/locales/`）。
- store 只缓存会话态（`me`），业务数据一律走 vue-query。
- 深色主题为默认，可在顶栏切换深色 / 浅色 / 跟随系统。
- 模板纪律：不在同一元素上同时写 `v-if` 与 `v-for`；`v-else` 必须紧跟 `v-if` 兄弟节点。

## 当前未实现（下一阶段）

`/repo`、`/sources`、`/policies`、`/jobs`、`/snapshots`、`/restore` 均为占位页。
