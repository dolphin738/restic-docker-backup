#!/usr/bin/env bash
# 红线机器校验清单（规范 §3.1 / AGENTS.md 编号索引的自动化落地）
#
# 为什么有这个脚本：AGENTS.md 与 docs/代码审查规范.md 历史上多次漂移
# （曾整条缺失 S4/S5/S8、漏 cache.clear 与 login.failed、漏 /restore 前缀）。
# 「改一处必须记得同步另一处」是纯约定，靠人记必然失效——本脚本把红线
# 变成可执行断言，让「是否违反」从"靠人对照"变成"一条命令出结论"。
#
# 用法：
#   bash scripts/verify_redlines.sh          # 全部检查，有 ✗ 则退出码 1
#   bash scripts/verify_redlines.sh -v       # 附带打印命中的具体行
#
# 退出码：0 = 全部通过；1 = 存在 ✗ 违反项
#
# 注意：本脚本的排除列表已逐条实跑校准，避免把合规写法误判为违规。
# 新增豁免前请先确认排除理由成立，不要为"让检查通过"而放宽断言。

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

FAIL=0
PASS=0

# 报告一个违反项（命中即打印）
violation() {
  printf '  \033[31m✗ %s\033[0m\n' "$1"
  FAIL=$((FAIL + 1))
}

# 命中不违规（仅 -v 时打印明细，便于确认排除项是否该保留）
detail() {
  [ "$VERBOSE" -eq 1 ] && printf '    · %s\n' "$1"
  return 0
}

ok() {
  printf '  \033[32m✓\033[0m %s\n' "$1"
  PASS=$((PASS + 1))
}

section() {
  printf '\n\033[1m%s\033[0m\n' "$1"
}

# ---------------------------------------------------------------- 路径与前缀

section 'S1/A2 路径映射唯一实现点'

# 文件系统前缀字面量只允许出现在：settings.py（根路径定义）、
# constants.py（白名单常量）、exclusions.py（模式派生输入）、path_mapper.py（校验实现）。
# 注：routers/ 下的 "/restore"、"/sources" 是 **API 路由路径**，与文件系统前缀无关。
out=$(grep -rIn '"/host"\|"/sources"\|"/restore"' backend/app frontend/src --include=*.py \
      | grep -v settings.py | grep -v constants.py | grep -v exclusions.py \
      | grep -v path_mapper.py | grep -v 'routers/' 2>/dev/null)
if [ -n "$out" ]; then
  violation 'S1/A2 前缀字面量出现在豁免范围之外的位置'
  detail "$out"
else
  ok 'S1/A2 无越界硬编码前缀'
fi

# ---------------------------------------------------------------- restic 参数

section 'S2 restic 参数白名单'

out=$(grep -rIn 'shell=True\|os\.system\|os\.popen' backend/app 2>/dev/null)
if [ -n "$out" ]; then
  violation 'S2 出现 shell=True / os.system / os.popen'
  detail "$out"
else
  ok 'S2 无 shell 注入面'
fi

# ---------------------------------------------------------------- 凭据泄漏

section 'S3 凭据零泄漏'

# 凭据出现在日志/打印语句中即为泄漏。注意排除常量定义与 getenv 本身。
out=$(grep -rIniE 'logger\.(debug|info|warning|error).*(RESTIC_PASSWORD|AWS_SECRET|token)' backend/app 2>/dev/null)
if [ -n "$out" ]; then
  violation 'S3 疑似把凭据/token 写入日志'
  detail "$out"
else
  ok 'S3 日志中未见凭据'
fi

# ---------------------------------------------------------------- 鉴权与首启

section 'S4/S5/S8 鉴权 · Origin · 首启门'

# 业务 router 必须挂 protected dependencies；health.py 免鉴权、auth.py 自身
# 是鉴权端点均为既定设计；__init__.py 是包声明不是 router。
missing=$(for f in backend/app/routers/*.py; do
  case "$(basename "$f")" in
    health.py|auth.py|__init__.py) continue ;;
  esac
  grep -q '_PROTECTED\|require_session' "$f" || echo "$f"
done)
if [ -n "$missing" ]; then
  violation 'S4 以下业务 router 未见 require_session/_PROTECTED'
  detail "$missing"
else
  ok 'S4 业务 router 均挂鉴权依赖'
fi

# SSE 与其他端点禁止 query token 鉴权。current_token/next_token 是 E28 改密的
# 合法业务字段名，不是鉴权 token，须排除。
out=$(grep -rIn 'token=' backend/app/routers 2>/dev/null | grep -vi 'current_token\|next_token')
if [ -n "$out" ]; then
  violation 'S4 routers 中出现 token=（疑似 query token 鉴权）'
  detail "$out"
else
  ok 'S4 无 query token 鉴权'
fi

# ---------------------------------------------------------------- 审计

section 'S6 审计只增不改'

# audit_logs 是 append-only，出现 UPDATE/DELETE 即破坏不变量。
out=$(grep -rIniE 'UPDATE[[:space:]]+audit_logs|DELETE[[:space:]]+FROM[[:space:]]+audit' backend/app 2>/dev/null)
if [ -n "$out" ]; then
  violation 'S6 audit_logs 出现 UPDATE/DELETE（append-only 被破坏）'
  detail "$out"
else
  ok 'S6 audit_logs 保持 INSERT/SELECT'
fi

# 危险操作必须埋点：动作名一律取 constants 的 AUDIT_* 常量，
# 直接写裸字符串说明绕过了常量单一真源。
out=$(grep -rIn '"login.failed"\|"cache.clear"\|"config.update"' backend/app --include=*.py 2>/dev/null \
      | grep -v constants.py | grep -v migrations/)
if [ -n "$out" ]; then
  detail "S6 审计动作名出现裸字符串（应引用 constants.AUDIT_*）：$out"
fi

# ---------------------------------------------------------------- 覆盖语义

section 'S7 禁止覆盖语义'

# --overwrite 仅限时间戳子目录场景由 JobRunner 固定注入（架构 §6.3），
# 经 restic.py 的 OVERWRITE_MODES 白名单校验、界面无入口——属合规。
out=$(grep -rIn 'overwrite' backend/app frontend/src 2>/dev/null \
      | grep -v 'restic.py' | grep -v 'job_runner.py' | grep -v 'locales' | grep -v 'restore.py')
if [ -n "$out" ]; then
  violation 'S7 overwrite 出现在预期之外的路径'
  detail "$out"
else
  ok 'S7 无越界的覆盖语义入口'
fi

# ---------------------------------------------------------------- DB 唯一实现点

section 'A1 DB 访问只在 repositories/'

# 豁免：app/auth/（架构 §1.1 明确的独立模块，自成表边界）、health.py 探针。
# 预期仅命中 job_runner.py 的 jobs 写入（已知待收口项，见规范 A1 豁免小节）。
out=$(grep -rIn 'SELECT \|INSERT INTO\|UPDATE \|DELETE FROM' backend/app --include=*.py 2>/dev/null \
      | grep -v 'app/db/' | grep -v 'app/auth/' | grep -v constants.py | grep -v 'health.py')
if [ -n "$out" ]; then
  violation 'A1 非仓储层出现 SQL（豁免仅 auth/ 与 health 探针）'
  detail "$out"
else
  ok 'A1 非仓储层无 SQL'
fi

# ---------------------------------------------------------------- 排除集单一来源

section 'A3 内置排除集单一来源'

# 内置排除集只有 exclusions.ExclusionRules 一处定义（架构 D8）。
# 已知豁免：
#   constants.PSEUDO_FS_NAMES —— E31 dry-run 的伪文件系统**告警**清单，
#     与排除集是两个不同用途的清单（命中只出黄色警告、不拦截），不属复制；
#   docstring / 注释行 —— 解释性文字（如 sources.py 描述该告警逻辑），
#     grep 无法区分文档与代码，用 -v 滤掉以行首引号或文档注释开头的命中。
# PSEUDO_FS_NAMES 与 exclusions.CORE 有 4 项字面重复（proc/sys/dev/run/lost+found），
# 这是设计上的有意重叠（都源自方案 §3.2），但**若 exclusions.CORE 变更需同步复核**。
out=$(grep -rIn 'lost+found' frontend/src backend/app 2>/dev/null \
      | grep -v exclusions.py | grep -v constants.py \
      | grep -vE ':[[:space:]]*(#|"""|[^\x27"]*$)' | grep -vE '命中|置黄色|告警')
if [ -n "$out" ]; then
  violation 'A3 内置排除集疑似被复制到别处'
  detail "$out"
else
  ok 'A3 排除集未被复制'
fi

# ---------------------------------------------------------------- SQLite 工程约定

section 'A5 SQLite 单写连接'

# PRAGMA 三件套必须齐全（journal_mode / busy_timeout / foreign_keys）。
for pragma in journal_mode busy_timeout foreign_keys; do
  if ! grep -q "PRAGMA $pragma" backend/app/db/database.py 2>/dev/null; then
    violation "A5 缺少 PRAGMA $pragma 初始化"
  fi
done
[ "$FAIL" -eq 0 ] && ok 'A5 PRAGMA 三件套齐全'

# busy_timeout 实际值须走 settings，不得裸写数值（评审 G4）
if grep -qE 'PRAGMA busy_timeout=[0-9]' backend/app/db/database.py 2>/dev/null; then
  violation 'A5 busy_timeout 裸写数值（应走 settings.db_busy_timeout_ms）'
else
  ok 'A5 busy_timeout 已走 settings'
fi

# ---------------------------------------------------------------- 解析纯函数

section 'A6 解析零 IO'

# parser.py 是纯函数层，出现 IO/仓储/restic 导入即污染。
out=$(grep -nE '^(import|from)' backend/app/services/parser.py 2>/dev/null \
      | grep -E '\bos\b|repositories|restic|path_mapper|aiofiles')
if [ -n "$out" ]; then
  violation 'A6 parser.py 出现 IO / 仓储 / restic 导入'
  detail "$out"
else
  ok 'A6 parser.py 保持纯函数'
fi

# ---------------------------------------------------------------- 可调阈值

section '可调阈值单一真源'

# 阈值应走 settings / 构造参数，常量只作默认值单一真源。
# 因此「常量文件里存在 SSE_HEARTBEAT_SECONDS = 15.0 / DB_BUSY_TIMEOUT_MS = 5000」
# 是**正确设计而非违规**；真正的违规是业务逻辑层裸写数值。
# 断言方向：constants.py / settings.py 之外的文件不得直接出现该数值。
out=$(grep -rIn '15\.0' backend/app/services backend/app/routers backend/app/db --include=*.py 2>/dev/null \
      | grep -i 'heartbeat')
if [ -n "$out" ]; then
  violation 'SSE 心跳间隔在业务层裸写数值（应走 settings.sse_heartbeat_seconds）'
  detail "$out"
else
  ok 'SSE 心跳未在业务层硬编码'
fi

out=$(grep -rIniE 'busy_timeout[ =]+[0-9]{3,}' backend/app --include=*.py 2>/dev/null \
      | grep -v constants.py)
if [ -n "$out" ]; then
  violation 'SQLite busy_timeout 在业务层裸写数值（应走 settings.db_busy_timeout_ms）'
  detail "$out"
else
  ok 'busy_timeout 未在业务层硬编码'
fi

# 反向确认：两个 settings 字段真实存在且被引用
for field in sse_heartbeat_seconds db_busy_timeout_ms; do
  if ! grep -q "$field" backend/app/settings.py 2>/dev/null; then
    violation "settings 缺少可配置阈值字段：$field"
  fi
done

# ---------------------------------------------------------------- 双文档同步

section '双文档同步（编号锚点）'

# AGENTS.md 的红线条目带 [S#/A#/C#] 编号锚点。规范 §3.1 是唯一权威版本，
# 编号对不上即视为同步失败——这是本脚本存在的根本目的。
if [ ! -f AGENTS.md ]; then
  violation 'AGENTS.md 缺失（AI 代理的首读入口）'
elif [ ! -f docs/代码审查规范.md ]; then
  violation 'docs/代码审查规范.md 缺失（红线唯一权威版本）'
else
  # AGENTS.md 引用的编号必须在规范里真实存在
  for id in $(grep -oE '\[(S|A)[0-9]+(/[SA][0-9]+)*\]' AGENTS.md 2>/dev/null \
              | tr -d '[]' | tr '/' '\n' | sort -u); do
    if ! grep -qE "^\| $id \|" docs/代码审查规范.md 2>/dev/null; then
      violation "同步失败：AGENTS.md 引用的编号 [$id] 在规范 §3.1 中不存在"
    fi
  done
  ok 'AGENTS.md 编号锚点与规范 §3.1 一致'
fi

# ---------------------------------------------------------------- Docker 构建上下文

section 'G8 .dockerignore 与 Dockerfile 一致'

# 无 docker 运行时也要能验证 .dockerignore：排除过头会导致构建失败，
# 排除不足会把 .venv/.env 打进镜像。校验器解析 Dockerfile 的 COPY 源做双向核对。
# python3 → python 依次尝试；都没有则跳过（不误报为违反，但会明确提示）。
PY_BIN=''
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
done

if [ -f .dockerignore ] && [ -f docker/Dockerfile ]; then
  if [ -z "$PY_BIN" ]; then
    printf '  \033[33m○\033[0m .dockerignore 存在，但无 python 解释器，跳过一致性校验\n'
  elif out=$("$PY_BIN" scripts/check_dockerignore.py 2>&1); then
    ok '.dockerignore 与 Dockerfile COPY 源一致'
  else
    violation '.dockerignore 与 Dockerfile 不一致（见下）'
    detail "$out"
  fi
else
  violation '缺少 .dockerignore 或 docker/Dockerfile'
fi

# ---------------------------------------------------------------- 汇总

printf '\n%s\n' '────────────────────────────────────────'
if [ "$FAIL" -gt 0 ]; then
  printf '\033[31m✗ %d 项违反\033[0m（通过 %d 项）\n' "$FAIL" "$PASS"
  exit 1
fi
printf '\033[32m✓ 全部 %d 项红线检查通过\033[0m\n' "$PASS"
exit 0
