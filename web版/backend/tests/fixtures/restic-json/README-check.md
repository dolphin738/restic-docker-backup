# restic check `--json` fixture 依据（T04 fixture 先行，R-017）

- **restic 版本**：0.19.1（Dockerfile `ARG RESTIC_VERSION=0.19.1` 同源）
- **权威依据**：`cmd/restic/cmd_check.go` @ tag v0.19.1
  （github.com/restic/restic），关键定义：`checkSummary` / `checkError` /
  `jsonErrorPrinter`；`--read-data-subset` 取值域见 `checkFlags()`。
- **落档时间**：2026-10-14（T04 fixture 先行子任务；V4 真机实测由 T04 主任务回填架构 §12）

## 1. check `--json` 的真实消息形态（0.19.1 源码口径）

只有**两类** `message_type`，**没有任何进度/status 消息**——`jsonErrorPrinter`
的 `NewCounter` 返回 nil、`P/S/V/VV` 全为空实现，无论是否 `--read-data`：

1. `error` 行（`jsonErrorPrinter.E`，每错误一行，即时输出）：

   ```json
   {"message_type":"error","message":"<fmt 文本>"}
   ```

   注意 `message` 保留 Go 端格式化产生的换行（JSON 内为 `\n` 转义）；tree 错误
   是**两行**：头行 `"error for tree <id>:\n"` 与每条子错误行 `"  <err>\n"`
   （源码 `printer.E("error for tree %v:\n", ...)` + 逐条 `printer.E("  %v\n", ...)`）。

2. `summary` 行（**仅一条，末尾**，`ui.ToJSONString(checkSummary)`）：

   ```json
   {"message_type":"summary","num_errors":N,"broken_packs":[...],"suggest_repair_index":bool,"suggest_prune":bool}
   ```

   - **健康仓库 `broken_packs` 为 `null`**（Go nil slice → JSON `null`），不是
     `[]`——解析层必须容错两种形态（`check-clean.ndjson` 忠实保留 null）。
   - 退出码非 0 时若 `num_errors` 仍为 0，restic 的 RunE 包装会补成 1
     （`if err != nil && summary.NumErrors == 0 { summary.NumErrors = 1 }`）。

## 2. `--read-data` / `--read-data-subset` 对输出的影响

- **不改变消息形态**：只扩大错误面（读失败 → `error` 行 + `summary.broken_packs`
  填充 salvagePacks）与执行范围。**设计 §7 设想的「read_data 进度集」在 0.19.1 的
  JSON 输出中不存在**（进度只在非 JSON 的终端路径上）——T04 主任务的
  `CheckAggregate` 应由 `summary` 与 `error` 行驱动，不要按进度事件建模。
- `--read-data-subset` 取值域（`checkFlags()`；非法即 Fatal、**无 JSON 输出**——
  参数校验必须在 WhitelistArgs 层完成（S2），不能依赖 restic 的 JSON 反馈）：
  - `n/t`：正整数，`n ≤ t`，`t ≤ 256`（totalBucketsMax）
  - `x%` / `x.y%`：`0 < x ≤ 100.0`
  - 字节大小：`k/K m/M g/G t/T` 后缀，`> 0`
  - 与 `--read-data` 互斥（同时给 → Fatal）
- 退出码：0 成功 / 1 有错误（10/11/12 仓库不存在/已锁/密码错，见 cmd_check.go Long 文案）。

## 3. 消息文本仿真口径（V4 留档）

三个 fixture 的 `error.message` 文本按源码 `E` 调用点的格式串形态仿真
（`ErrPackData` 的 `error for pack <id>: <reason>`、tree 错误的头行 + 缩进子行、
index 损坏提示块），id 均为合法 64 位 hex。**V4（设计 §12.4）真机实测后若文本
与仿真有出入，由 T04 主任务校准 fixture 并回填架构文档 §12**——解析断言只应依赖
`message_type`/字段结构，不应依赖 message 文本细节。

## 4. 文件清单

| 文件 | 场景 | 内容要点 |
|---|---|---|
| `check-clean.ndjson` | 纯元数据 check（无 --read-data），健康 | 仅末尾 summary 一行；`broken_packs: null`（Go nil） |
| `check-errors-index.ndjson` | 结构/索引错误形态 | tree 错误块（2 行 error）+ index 损坏块（2 行 error）+ summary（num_errors=2、suggest_repair_index=true、broken_packs=null） |
| `check-read-data-damaged.ndjson` | 带 `--read-data` 的失败形态 | 2 条 pack 读错误（ErrPackData 形态）+ summary（num_errors=2、broken_packs=[2 个 pack id]） |

畸形行容错（设计 §7 第四类）不另设 fixture：restic 自身不输出畸形行，容错断言在
`tests/test_parser_check.py` 的护栏组中内联构造（复用 `junk_mixed.txt` 同款形态）。
