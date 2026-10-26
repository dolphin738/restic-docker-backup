# 真机验收探针（`web版/e2e/`）

用真实浏览器（Edge/Chrome）跑用户旅程、**量计算样式**、查横向溢出与对比度的验收工具。
补充 vitest 抓不到的那一类缺陷：jsdom 不算样式、`vue-tsc` 不看 CSS。

> **这不是 CI 门禁。** 探针需要 `localhost:5173` 的前端 dev server + 本机浏览器，
> CI 环境跑不了。它的价值是**验收门取证**与**可复现的缺陷证据**，不替代 vitest。
> 真正的自动化门禁是 `pnpm test`（45 文件 / 446 例）+ `scripts/verify_redlines.sh`（17 项）。

## 目录约定

| 路径 | 是否进库 | 说明 |
|---|---|---|
| `web版/e2e/*.cjs`、`harness.cjs`、本 README | ✅ 进库 | 探针源码 |
| `web版/.e2e/`（`.gitignore:104`） | ❌ 不进库 | 截图/JSON/log 产物 + 一次性归因脚本 |

产物目录由 `harness.outDir(name)` 自动创建，**永远落在 `web版/.e2e/<name>/`**，
不会污染本目录。

> **唯一真源就是本目录。** `.e2e/` 下曾与这六个探针同名的旧副本已于 2026-10-26 删除
> （它们是收编前的版本：硬编码本机路径、`channel: 'msedge'`、不含 `harness.cjs`）。
> 别再回去跑那些路径的残留副本——改旧的那份等于白改，且断言已过期。
> `.e2e/` 里其余脚本是一次性归因脚本，按下面的「收录 / 退役规则」留在本机。

## 前置条件

1. 前端 dev server 已起：`cd web版/frontend && pnpm dev`（默认 `:5173`，六个探针统一用这个）
2. 浏览器可用：默认依次尝试 `msedge` → `chrome` → playwright 自带 chromium；
   要指定就 `E2E_CHANNEL=chrome`
3. `playwright-core`：`harness.cjs` 按下列顺序解析，第一个**能用**的生效
   - `PLAYWRIGHT_CORE` 环境变量（目录或包入口）
   - `web版/frontend/node_modules/playwright-core`（若加为 devDependency）
   - 本机已知安装：DevEco Studio 自带 node 内那份（Windows 开发机实测可用）
   - `require('playwright-core')` 兜底

> 探针**不再写死**本机绝对路径与 `channel: 'msedge'`——旧 103 个脚本里 81 个这么写，
> 换机器必挂（`p13` 曾在 P19 改版后失效一个多月无人发现，正因它不在版本库、不在清单里）。

## 运行

```bash
# 在 web版/e2e/ 下
node p13-wizard-probe.cjs                  # 默认 http://localhost:5173
node p17-host-restore.cjs http://localhost:5173   # 也可显式传 base
```

退出码 `0` = PASS，`1` = FAIL / FATAL。逐条断言打在 stdout，截图落在 `web版/.e2e/<name>/`。

## 探针清单

| 探针 | 验收门 | 查什么（关键断言） | 产物 |
|---|---|---|---|
| `p13-wizard-probe.cjs` | 任务向导 5 步 + 仓库删除确认门 | 五步全走（手动加备份源 → 测试连接过 G3/E1 强门 → dry-run 让「确认并立即首跑」由禁用转可用）；ConfirmGate 三门（弹出 / 错词禁用 / 对词放行 / 终态按钮）；零 pageerror | `.e2e/p13-probe/` |
| `p14-mobile.cjs` | 移动端走查（§4.3） | 4 视口逐页 `scrollWidth ≤ innerWidth+1`；390px 向导步骤条横向可达；表格页 `.n-data-table` 存在；零 pageerror | `.e2e/p14-mobile/` |
| `p16-options-mobile.cjs` | 恢复第二步 options 页 | 4 视口零溢出；三卡片 + 两端页脚 + raised 单选卡在 DOM；桌面列宽 ≤500；切「选择位置」后树与二模开关仍零溢出 | `.e2e/p16-options/` |
| `p17-host-restore.cjs` | host 部署形态恢复链路 | **双前缀反证**：E20 预览 `target` 必须是展示形态（`/srv/data`），不得是 `/sources/srv/data`；树根 = `restore_root`；`/repo` deny 拦截 + 内联错误；1440/390 零溢出 | `.e2e/p17-host/` |
| `p20-stepper-contrast.cjs` | 向导步骤条对比度（WCAG 2.1） | 亮暗双主题逐元素量 `color` / `opacity` / `background-color`，按 alpha 合成后算对比度（阈值 ≥4.5）；四处向导 | `.e2e/p20-contrast/` |
| `p21-restore-pick.cjs` | 恢复第一步勾选反馈 | **取计算样式**（不查类名）：未选行 `rgba(0,0,0,0)` vs 选中行 12%/16% 底色 + `inset 2px` 左竖线；多选 2 行 + 已选清单 2 条；逐条 ×／清空后归零；亮暗双跑；零 pageerror | `.e2e/p21-restore-pick/` |

## 收录 / 退役规则

**进**：绑定某个**验收门**、以后还会再跑、且断言能给出可复核数值（计算样式/像素/对比度/溢出量）的探针。

**不进**：
- 一次性归因脚本（P5 视觉发散定位、某次 dblclick 复现等）——留在 `.e2e/` 本机即可；
- 纯截图脚本（`*-shot.cjs`）——截图不是断言；
- 硬编码绝对路径或真实口令的脚本——先过 `harness` 与去敏，再谈收录。

**退役**：功能下线或页面重构导致探针语义失效时，要么跟着改（像 `p13` 这样），
要么**删掉并在提交信息里写明原因**——留在库里跑不通的探针比没有更坏。

## 已知限制

- 探针不启动 dev server，也不清理端口；六个探针统一打 `:5173`（`BASE` 可用 argv[2] 覆盖）。
- 断言是「当前实现的快照」，UI 有意改版时需同步改断言（改完请把新口径写进提交信息）。
- 探针里的 API stub 是**回放**结构，后端契约变更后需同步字段。
