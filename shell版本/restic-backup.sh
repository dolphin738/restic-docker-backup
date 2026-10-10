#!/usr/bin/env bash
# =============================================================================
#  restic-backup.sh —— 基于 Docker 版 restic 的可配置备份脚本
#
#  流程：加载配置 → 数据库 dump → 备份（单策略 / 多策略循环 / 自调度按策略到期）→
#        保留策略 → 统一物理清理 → 仓库校验 → 告警
#  依赖：bash 4.4+ / docker / curl(可选) / flock(可选) / pg_dump|mysqldump(可选)
#  用法： ./restic-backup.sh [-c /path/to/backup.env] [--dry-run] [-t|--tag <标签>]
#                          [-p|--policy <名>[,<名2>]] [--prune-only] [-h]
#         例：升级前手动备份 ./restic-backup.sh --tag pre-upgrade
#         例：只跑高频策略   ./restic-backup.sh --policy hot
#         例：只执行清理     ./restic-backup.sh --prune-only
#  定时： 见 README（systemd timer / 1Panel 计划任务 / 进程守护）
#  策略： 未设 BACKUP_POLICIES = 单策略（旧配置完全兼容）；
#         设置后多策略循环备份，各策略独立标签/备份源/排除/保留档位
#  自调度：任一策略设置 POLICY_<名>_INTERVAL_MIN 后，每次被唤醒时逐策略判定到期，
#          到期才备份（调度器只需以最高巡检频率触发，详见 backup.env 第 14 节）
#  注意： 配置文件含密钥，务必 chmod 600
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
CONFIG_FILE="${BACKUP_CONFIG:-$SCRIPT_DIR/backup.env}"
START_TS="$(date '+%F %T')"
START_EPOCH="$(date +%s)"
DOCKER_ARGS=(--rm)

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
log() {
  local level="$1"; shift
  printf '%s [%s] %s\n' "$(date '+%F %T')" "$level" "$*"
  if [[ -n "${LOG_FILE:-}" ]]; then
    printf '%s [%s] %s\n' "$(date '+%F %T')" "$level" "$*" >>"$LOG_FILE" 2>/dev/null || true
  fi
}
info() { log INFO "$@"; }
warn() { log WARN "$@"; }
die()  { log ERROR "$@"; exit 1; }

trim() { printf '%s' "$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$1")"; }

# 多行字符串 -> 全局数组（忽略空行与 # 注释行）
lines_to_array() {
  local __name="$1" __input="${2:-}"
  local -a __arr=()
  local __line
  while IFS= read -r __line; do
    __line="${__line%%#*}"
    __line="$(trim "$__line")"
    if [[ -n "$__line" ]]; then __arr+=("$__line"); fi
  done < <(printf '%s\n' "$__input" | tr -d '\r')
  if ((${#__arr[@]} == 0)); then
    eval "$__name=()"
  else
    eval "$__name=(\"\${__arr[@]}\")"
  fi
}

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
DRY_RUN_OVERRIDE=""
TAG_OVERRIDE=""
POLICY_OVERRIDE=""
PRUNE_ONLY="false"
while (($#)); do
  case "$1" in
    -c|--config)  CONFIG_FILE="$2"; shift 2 ;;
    -n|--dry-run) DRY_RUN_OVERRIDE="true"; shift ;;
    -t|--tag)     TAG_OVERRIDE="$2"; shift 2 ;;   # 临时覆盖标签（仅单策略模式），不改 backup.env
    -p|--policy)  POLICY_OVERRIDE="$2"; shift 2 ;; # 只跑指定策略（逗号分隔，多策略+自调度模式）
    --prune-only) PRUNE_ONLY="true"; shift ;;      # 只执行全部策略 forget + prune（不备份）
    -h|--help)    sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf '未知参数: %s（用 -h 查看用法）\n' "$1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# 配置加载与默认值
# ---------------------------------------------------------------------------
[[ -r "$CONFIG_FILE" ]] || die "配置文件不可读: $CONFIG_FILE（先 cp backup.env.example backup.env）"
set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a
if [[ -n "${DRY_RUN_OVERRIDE}" ]]; then DRY_RUN="true"; fi
if [[ -n "${TAG_OVERRIDE:-}" ]]; then BACKUP_TAG="$TAG_OVERRIDE"; fi

: "${RESTIC_IMAGE:=restic/restic:0.19.1}"
: "${AUTO_PULL:=true}"
: "${CACHE_DIR:=/var/cache/restic}"
: "${RESTIC_CACHE_DIR:=/cache}"
: "${BACKUP_TAG:=scheduled}"
: "${EXTRA_BACKUP_ARGS:=--one-file-system}"
: "${DRY_RUN:=false}"
: "${FORGET_ENABLED:=true}"
: "${FORGET_GROUP_BY:=host,paths}"
: "${CHECK_ENABLED:=true}"
: "${CHECK_SUBSET:=10%}"
: "${CHECK_FULL_DAY_OF_MONTH:=01}"
: "${AUTO_UNLOCK:=true}"
: "${AUTO_INIT:=false}"
: "${DB_ENABLED:=false}"
: "${DB_TYPE:=none}"
: "${DB_DUMP_DIR:=/var/backups/dump}"
: "${DB_KEEP_DAYS:=3}"
: "${SELINUX_LABEL:=auto}"
: "${NOTIFY_ON_SUCCESS:=true}"
: "${TZ:=UTC}"
: "${LOG_FILE:=/var/log/restic-backup.log}"
: "${LOCK_FILE:=/var/lock/restic-backup.lock}"

if [[ -z "${RESTIC_REPOSITORY:-}" ]]; then die "RESTIC_REPOSITORY 未配置"; fi
if [[ -z "${RESTIC_ENV_VARS:-}" ]]; then RESTIC_ENV_VARS="RESTIC_REPOSITORY"; fi

export TZ RESTIC_REPOSITORY RESTIC_CACHE_DIR RESTIC_PASSWORD_FILE
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 通知（healthchecks.io 风格 ping + 自定义命令 %MSG%）
# ---------------------------------------------------------------------------
hc_ping() {
  local path="$1" body="${2:-}"
  if [[ -z "${HEALTHCHECK_URL:-}" ]]; then return 0; fi
  if [[ -n "$body" ]]; then
    curl -fsS -m 20 --retry 2 --retry-delay 5 -o /dev/null --data-raw "$body" "${HEALTHCHECK_URL}${path}" >/dev/null 2>&1 || true
  else
    curl -fsS -m 20 --retry 2 --retry-delay 5 -o /dev/null "${HEALTHCHECK_URL}${path}" >/dev/null 2>&1 || true
  fi
}
notify() {
  local msg="$1" ok="${2:-true}"
  if [[ -z "${NOTIFY_CMD:-}" ]]; then return 0; fi
  if [[ "$ok" != "true" || "${NOTIFY_ON_SUCCESS}" == "true" ]]; then
    # shellcheck disable=SC2295
    eval "${NOTIFY_CMD//%MSG%/$msg}" >/dev/null 2>&1 || warn "通知命令执行失败"
  fi
}
on_error() {
  local exit_code=$?
  local detail="备份失败（第 ${1:-?} 行，退出码 ${exit_code}）: ${BASH_COMMAND:-}"
  log ERROR "$detail"
  hc_ping "/fail" "$detail"
  notify "$detail" false
  if [[ "${AUTO_UNLOCK}" == "true" && ${#DOCKER_ARGS[@]} -gt 3 ]]; then
    docker run "${DOCKER_ARGS[@]}" "${RESTIC_IMAGE}" unlock >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}
trap 'on_error $LINENO' ERR

# ---------------------------------------------------------------------------
# 并发锁
# ---------------------------------------------------------------------------
if command -v flock >/dev/null 2>&1; then
  mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "已有备份进程在运行（锁文件: $LOCK_FILE）"
fi

# ---------------------------------------------------------------------------
# Docker 环境检查
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then die "未找到 docker 命令"; fi
if ! docker info >/dev/null 2>&1; then die "docker daemon 不可访问（当前用户是否在 docker 组？）"; fi

if [[ "${AUTO_PULL}" == "true" ]]; then
  if ! docker image inspect "$RESTIC_IMAGE" >/dev/null 2>&1; then
    info "拉取镜像: $RESTIC_IMAGE"
    docker pull "$RESTIC_IMAGE" >/dev/null
  fi
fi

# ---------------------------------------------------------------------------
# 仓库密码（文件方式优先）
# ---------------------------------------------------------------------------
if [[ -n "${RESTIC_PASSWORD_FILE_HOST:-}" && -n "${RESTIC_PASSWORD_FILE:-}" ]]; then
  if [[ ! -r "$RESTIC_PASSWORD_FILE_HOST" ]]; then die "密码文件不可读: $RESTIC_PASSWORD_FILE_HOST"; fi
  DOCKER_ARGS+=( -v "$RESTIC_PASSWORD_FILE_HOST:$RESTIC_PASSWORD_FILE:ro" )
  export RESTIC_PASSWORD_FILE
elif [[ -n "${RESTIC_PASSWORD:-}" ]]; then
  warn "正在使用明文 RESTIC_PASSWORD，建议改用密码文件"
  export RESTIC_PASSWORD
  RESTIC_ENV_VARS="${RESTIC_ENV_VARS} RESTIC_PASSWORD"
else
  die "未配置仓库密码（RESTIC_PASSWORD_FILE_HOST 或 RESTIC_PASSWORD）"
fi

# 后端凭据透传（B2/S3/rclone 等；未设置的自动跳过）
for name in $RESTIC_ENV_VARS; do
  if printenv "$name" >/dev/null 2>&1; then
    DOCKER_ARGS+=( -e "$name" )
  else
    warn "环境变量未设置，跳过透传: $name"
  fi
done

# 缓存卷：务必持久化，否则每次备份重建缓存、耗时明显变长
mkdir -p "$CACHE_DIR"
DOCKER_ARGS+=( -v "$CACHE_DIR:$RESTIC_CACHE_DIR" -e RESTIC_CACHE_DIR -e TZ )
if [[ -n "${DOCKER_USER:-}" ]]; then DOCKER_ARGS+=( --user "$DOCKER_USER" ); fi

# SELinux（RHEL/CentOS/Rocky 不加 :z 会 Permission denied）
mount_opts_ro() {
  local s=":ro"
  case "${SELINUX_LABEL}" in
    z|Z)  s=":ro,${SELINUX_LABEL}" ;;
    none) s=":ro" ;;
    auto) if [[ -d /sys/fs/selinux ]] && command -v getenforce >/dev/null 2>&1; then s=":ro,z"; fi ;;
  esac
  printf '%s' "$s"
}
mount_opts_rw() {
  local s=""
  case "${SELINUX_LABEL}" in
    z|Z)  s=",${SELINUX_LABEL}" ;;
    none) s="" ;;
    auto) if [[ -d /sys/fs/selinux ]] && command -v getenforce >/dev/null 2>&1; then s=",z"; fi ;;
  esac
  printf '%s' "$s"
}

# 额外挂载：rclone 配置 / SSH 私钥 / known_hosts 等
# 每行格式：宿主机路径[:容器路径[:选项]]，缺省容器路径=宿主机路径、选项=ro
if [[ -n "${EXTRA_MOUNTS:-}" ]]; then
  lines_to_array EXTRA_MOUNTS_ARR "$EXTRA_MOUNTS"
  if ((${#EXTRA_MOUNTS_ARR[@]} > 0)); then
    for m in "${EXTRA_MOUNTS_ARR[@]}"; do
      IFS=':' read -r mhost mcont mopts <<<"$m"
      if [[ -z "${mcont:-}" ]]; then mcont="$mhost"; fi
      if [[ -z "${mopts:-}" ]]; then mopts="ro"; fi
      if [[ -e "$mhost" ]]; then
        DOCKER_ARGS+=( -v "$mhost:$mcont:$mopts" )
      else
        warn "额外挂载路径不存在，已跳过: $mhost"
      fi
    done
  fi
fi

run_restic() { docker run "${DOCKER_ARGS[@]}" "$RESTIC_IMAGE" "$@"; }

# ---------------------------------------------------------------------------
# 本地路径仓库：自动挂载进容器（可写）
# 判断依据：RESTIC_REPOSITORY 无后端前缀冒号（s3:/b2:/sftp:/rclone:）且为绝对路径。
# 不挂载的后果：init 会把仓库建在临时容器层里（--rm 后丢失），表现为
#   "init 成功但备份报仓库不可用或密码错误"——本缺陷的典型特征。
# ---------------------------------------------------------------------------
if [[ "$RESTIC_REPOSITORY" != *:* && "$RESTIC_REPOSITORY" == /* ]]; then
  if mkdir -p "$RESTIC_REPOSITORY" 2>/dev/null; then
    DOCKER_ARGS+=( -v "$RESTIC_REPOSITORY:$RESTIC_REPOSITORY$(mount_opts_rw)" )
    info "本地仓库已挂载（可写）: $RESTIC_REPOSITORY"
  else
    die "本地仓库目录无法创建: $RESTIC_REPOSITORY（检查权限，或改用对象存储后端）"
  fi
fi

# ---------------------------------------------------------------------------
# 数据库 dump（容器镜像内没有 pg_dump/mysqldump，须在宿主机侧先落盘）
# ---------------------------------------------------------------------------
dump_databases() {
  if [[ "${DB_ENABLED}" != "true" ]]; then return 0; fi
  mkdir -p "$DB_DUMP_DIR"
  local ts; ts="$(date '+%F-%H%M%S')"
  local out

  case "${DB_TYPE}" in
    postgres)
      out="$DB_DUMP_DIR/pg-${PG_DATABASE:-appdb}-$ts.dump"
      info "导出 PostgreSQL: ${PG_DATABASE:-appdb} -> $out"
      if [[ "${PG_DUMP_METHOD:-host}" == "docker" ]]; then
        if [[ -z "${PG_CONTAINER:-}" ]]; then die "PG_DUMP_METHOD=docker 时必须配置 PG_CONTAINER"; fi
        docker exec -e PGPASSWORD="${PGPASSWORD:-}" "$PG_CONTAINER" \
          pg_dump -Fc -U "${PG_USER:-postgres}" -d "${PG_DATABASE:-postgres}" >"$out"
      else
        PGPASSWORD="${PGPASSWORD:-}" pg_dump -Fc \
          -h "${PG_HOST:-127.0.0.1}" -p "${PG_PORT:-5432}" \
          -U "${PG_USER:-postgres}" -d "${PG_DATABASE:-postgres}" >"$out"
      fi
      if [[ ! -s "$out" ]]; then die "pg_dump 输出为空: $out"; fi
      ;;
    mysql)
      out="$DB_DUMP_DIR/mysql-${MYSQL_DATABASE:-appdb}-$ts.sql"
      info "导出 MySQL: ${MYSQL_DATABASE:-appdb} -> $out"
      if [[ "${MYSQL_DUMP_METHOD:-host}" == "docker" ]]; then
        if [[ -z "${MYSQL_CONTAINER:-}" ]]; then die "MYSQL_DUMP_METHOD=docker 时必须配置 MYSQL_CONTAINER"; fi
        if [[ -n "${MYSQL_ROOT_PASSWORD:-}" ]]; then
          # 面板场景（1Panel 等）：MySQL 容器自带 root 密码环境变量，直接复用，免去凭据文件
          docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$MYSQL_CONTAINER" \
            mysqldump --single-transaction --quick --default-character-set=utf8mb4 \
            -uroot "${MYSQL_DATABASE:-}" >"$out"
        else
          docker exec "$MYSQL_CONTAINER" \
            mysqldump --single-transaction --quick --default-character-set=utf8mb4 \
            --defaults-extra-file="${MYSQL_DEFAULTS_FILE:-/dev/null}" "${MYSQL_DATABASE:-}" >"$out"
        fi
      else
        mysqldump --single-transaction --quick --default-character-set=utf8mb4 \
          --defaults-extra-file="${MYSQL_DEFAULTS_FILE:-/dev/null}" "${MYSQL_DATABASE:-}" >"$out"
      fi
      if [[ ! -s "$out" ]]; then die "mysqldump 输出为空: $out"; fi
      ;;
    none|"") return 0 ;;
    *) die "不支持的 DB_TYPE: $DB_TYPE" ;;
  esac

  # 清理过期本地 dump（仅限本目录内的 .dump/.sql，避免误删其他文件）
  if [[ "${DRY_RUN}" != "true" ]]; then
    find "$DB_DUMP_DIR" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.sql' \) \
      -mtime "+${DB_KEEP_DAYS}" -delete 2>/dev/null || true
  fi
}

# ---------------------------------------------------------------------------
# 备份策略解析
#   单策略（旧配置兼容）：未设置 BACKUP_POLICIES 时，全局 BACKUP_SOURCES /
#     BACKUP_TAG / KEEP_* 构成一个隐式策略，行为与旧版完全一致
#   多策略：BACKUP_POLICIES="p1 p2"，每个策略以 POLICY_<名>_<字段> 定义：
#     POLICY_<名>_TAG               备份标签（默认回退 BACKUP_TAG）
#     POLICY_<名>_SOURCES           备份源（多行，必填）
#     POLICY_<名>_EXCLUDE_PATTERNS  策略级排除（可选，追加到全局 EXCLUDE_PATTERNS 之后）
#     POLICY_<名>_KEEP_LAST/DAILY/WEEKLY/MONTHLY/YEARLY（默认回退全局同名项）
#   约定：策略名仅允许字母/数字/下划线；多策略模式下 BACKUP_TAG、--tag、
#         FORGET_TAG 不生效（各策略按自身标签独立执行备份与保留策略）
# ---------------------------------------------------------------------------
declare -A P_TAG P_SOURCES P_EXCLUDES P_KEEP_LAST P_KEEP_DAILY P_KEEP_WEEKLY P_KEEP_MONTHLY P_KEEP_YEARLY
POLICY_LIST=()
MULTI="false"

policy_val() {  # policy_val <策略名> <字段后缀> [默认值]
  local v="POLICY_${1}_${2}" val=""
  val="$(printenv "$v" 2>/dev/null || true)"
  printf '%s' "${val:-${3:-}}"
}

if [[ -n "${BACKUP_POLICIES:-}" ]]; then
  MULTI="true"
  read -r -a POLICY_LIST <<<"$BACKUP_POLICIES"
  local_p=""
  for p in "${POLICY_LIST[@]}"; do
    if [[ ! "$p" =~ ^[A-Za-z0-9_]+$ ]]; then die "策略名不合法: $p（仅允许字母/数字/下划线）"; fi
    P_TAG[$p]="$(policy_val "$p" TAG "${BACKUP_TAG:-scheduled}")"
    P_SOURCES[$p]="$(policy_val "$p" SOURCES "")"
    if [[ -z "${P_SOURCES[$p]}" ]]; then die "策略 $p 未定义 POLICY_${p}_SOURCES"; fi
    P_EXCLUDES[$p]="$(policy_val "$p" EXCLUDE_PATTERNS "")"
    P_KEEP_LAST[$p]="$(policy_val "$p" KEEP_LAST "${KEEP_LAST:-0}")"
    P_KEEP_DAILY[$p]="$(policy_val "$p" KEEP_DAILY "${KEEP_DAILY:-0}")"
    P_KEEP_WEEKLY[$p]="$(policy_val "$p" KEEP_WEEKLY "${KEEP_WEEKLY:-0}")"
    P_KEEP_MONTHLY[$p]="$(policy_val "$p" KEEP_MONTHLY "${KEEP_MONTHLY:-0}")"
    P_KEEP_YEARLY[$p]="$(policy_val "$p" KEEP_YEARLY "${KEEP_YEARLY:-0}")"
  done
  if [[ -n "${FORGET_TAG:-}" ]]; then warn "多策略模式下 FORGET_TAG 不生效（各策略按自身标签执行保留策略）"; fi
  if [[ -n "${TAG_OVERRIDE:-}" ]]; then warn "多策略模式下 --tag 不生效（如需临时备份请用单策略配置或直接调 restic.sh）"; fi
else
  MULTI="false"
  POLICY_LIST=("single")
  P_TAG[single]="$BACKUP_TAG"
  P_SOURCES[single]="${BACKUP_SOURCES:-}"
  P_EXCLUDES[single]="${EXCLUDE_PATTERNS:-}"
  P_KEEP_LAST[single]="${KEEP_LAST:-0}"
  P_KEEP_DAILY[single]="${KEEP_DAILY:-0}"
  P_KEEP_WEEKLY[single]="${KEEP_WEEKLY:-0}"
  P_KEEP_MONTHLY[single]="${KEEP_MONTHLY:-0}"
  P_KEEP_YEARLY[single]="${KEEP_YEARLY:-0}"
fi

# ---------------------------------------------------------------------------
# 自调度模式（多策略 + 任一策略设置 POLICY_<名>_INTERVAL_MIN 时启用）
#   语义：每次被调度器唤醒时，逐策略检查是否到期（距上次运行 ≥ 间隔），
#         到期才备份；prune 同样按 PRUNE_INTERVAL_MIN 独立判到期。
#   效果：调度器（systemd timer / 1Panel 计划任务）只需以最高巡检频率触发，
#         各策略的真实备份频率由配置表达——一个单元即可承载全部策略。
# ---------------------------------------------------------------------------
INTERVAL_MODE="false"
PRUNE_INTERVAL_MIN="${PRUNE_INTERVAL_MIN:-1440}"   # forget+prune 间隔（分钟），默认每天
STATE_DIR="${STATE_DIR:-/var/lib/restic-backup}"   # 各策略 last-run 状态目录
if [[ "$MULTI" == "true" ]]; then
  for p in "${POLICY_LIST[@]}"; do
    if printenv "POLICY_${p}_INTERVAL_MIN" >/dev/null 2>&1; then
      INTERVAL_MODE="true"
      break
    fi
  done
  if [[ "$INTERVAL_MODE" == "true" ]]; then
    if mkdir -p "$STATE_DIR" 2>/dev/null; then
      info "自调度模式：各策略按 POLICY_*_INTERVAL_MIN 判定到期（状态目录: $STATE_DIR）"
    else
      warn "无法创建状态目录 $STATE_DIR，自调度退化为全部策略执行"
      INTERVAL_MODE="false"
    fi
  fi
fi

state_file() { printf '%s/last-%s' "$STATE_DIR" "$1"; }
last_run()  { cat "$(state_file "$1")" 2>/dev/null || echo 0; }
mark_run()  { date +%s > "$(state_file "$1")"; }
is_due() {  # is_due <名字> <间隔分钟> → 0=到期
  local interval_min="$2"
  [[ "$interval_min" =~ ^[0-9]+$ ]] || return 0
  (( $(date +%s) - $(last_run "$1") >= interval_min * 60 ))
}

# --policy 过滤列表（多策略 + 自调度模式生效；显式指定的策略强制执行、无视到期）
WANT_LIST=()
if [[ -n "${POLICY_OVERRIDE:-}" ]]; then
  if [[ "$MULTI" != "true" ]]; then
    warn "单策略模式下 --policy 不生效，已忽略"
  else
    IFS=',' read -r -a WANT_LIST <<<"$POLICY_OVERRIDE"
    for w in "${WANT_LIST[@]}"; do
      found_w="false"
      for p in "${POLICY_LIST[@]}"; do
        if [[ "$p" == "$w" ]]; then found_w="true"; break; fi
      done
      if [[ "$found_w" != "true" ]]; then
        die "--policy 指定了未定义的策略: $w（已定义: ${POLICY_LIST[*]}）"
      fi
    done
  fi
fi

ALL_SOURCES=()

# 单个策略的备份执行（挂载该策略的备份源 → restic backup）
run_policy_backup() {
  local p="$1"
  local -a psrcs pargs vsrcs=() pex gex extra_args
  local src ex
  lines_to_array psrcs "${P_SOURCES[$p]}"
  if ((${#psrcs[@]} == 0)); then
    warn "策略 [$p] 未定义备份源，跳过"
    return 0
  fi
  pargs=("${DOCKER_ARGS[@]}")
  for src in "${psrcs[@]}"; do
    if [[ "$src" == *:* ]]; then
      warn "策略 [$p] 不支持映射语法（路径含冒号），已跳过: $src"
    elif [[ ! -e "$src" ]]; then
      warn "策略 [$p] 备份源不存在，已跳过: $src"
    else
      pargs+=( -v "$src:$src$(mount_opts_ro)" )
      vsrcs+=( "$src" )
    fi
  done
  if ((${#vsrcs[@]} == 0)); then
    warn "策略 [$p] 没有有效备份源，跳过"
    return 0
  fi
  local bargs=( backup --tag "${P_TAG[$p]}" --host "${RESTIC_HOST}" )
  lines_to_array pex "${P_EXCLUDES[$p]}"
  for ex in "${pex[@]}"; do bargs+=( --exclude "$ex" ); done
  lines_to_array gex "${EXCLUDE_PATTERNS:-}"
  for ex in "${gex[@]}"; do bargs+=( --exclude "$ex" ); done
  read -r -a extra_args <<<"${EXTRA_BACKUP_ARGS}"
  if ((${#extra_args[@]} > 0)); then bargs+=( "${extra_args[@]}" ); fi
  if [[ "${DRY_RUN}" == "true" ]]; then
    bargs+=( --dry-run )
    info "策略 [$p] DRY-RUN 演练模式：不实际写入"
  fi
  info "策略 [$p] 备份源: ${vsrcs[*]}"
  docker run "${pargs[@]}" "$RESTIC_IMAGE" "${bargs[@]}" "${vsrcs[@]}"
  ALL_SOURCES+=( "${vsrcs[@]}" )
  info "策略 [$p] 备份完成"
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
info "========== 备份开始 (仓库: ${RESTIC_REPOSITORY}) =========="
hc_ping "/start" ""

# 仓库可达性 / 自动初始化
if ! run_restic cat config >/dev/null 2>&1; then
  if [[ "${AUTO_INIT}" == "true" ]]; then
    info "仓库不可用，尝试 restic init"
    if ! run_restic init; then die "restic init 失败（仓库已存在则检查密码）"; fi
  else
    die "仓库不可用或密码错误（如需自动初始化请设 AUTO_INIT=true，或手动执行: ./restic.sh init）"
  fi
fi

dump_databases

# ---- 备份与保留（固定主机标识：容器随机主机名会让按主机分组的保留策略失效）----
: "${RESTIC_HOST:=$(hostname)}"
export RESTIC_HOST

# 全部策略的 forget（不含 prune；由调用方决定何时统一物理清理）
run_forget_all() {
  local p ftag
  local -a forget_args
  for p in "${POLICY_LIST[@]}"; do
    if [[ "$MULTI" == "false" && -n "${FORGET_TAG:-}" ]]; then
      ftag="$FORGET_TAG"   # 单策略模式兼容：FORGET_TAG 限定策略作用范围
    else
      ftag="${P_TAG[$p]}"
    fi
    forget_args=( forget --tag "$ftag" --group-by "$FORGET_GROUP_BY" )
    if [[ "${P_KEEP_LAST[$p]:-0}"    -gt 0 ]]; then forget_args+=( --keep-last    "${P_KEEP_LAST[$p]}" ); fi
    if [[ "${P_KEEP_DAILY[$p]:-0}"   -gt 0 ]]; then forget_args+=( --keep-daily   "${P_KEEP_DAILY[$p]}" ); fi
    if [[ "${P_KEEP_WEEKLY[$p]:-0}"  -gt 0 ]]; then forget_args+=( --keep-weekly  "${P_KEEP_WEEKLY[$p]}" ); fi
    if [[ "${P_KEEP_MONTHLY[$p]:-0}" -gt 0 ]]; then forget_args+=( --keep-monthly "${P_KEEP_MONTHLY[$p]}" ); fi
    if [[ "${P_KEEP_YEARLY[$p]:-0}"  -gt 0 ]]; then forget_args+=( --keep-yearly  "${P_KEEP_YEARLY[$p]}" ); fi
    info "策略 [$p] 执行保留策略: ${forget_args[*]}"
    run_restic "${forget_args[@]}"
  done
}

RAN_BACKUP="false"

if [[ "${PRUNE_ONLY}" == "true" ]]; then
  info "prune-only 模式：全部策略 forget + prune（不执行备份与校验）"
  run_forget_all
  info "统一执行仓库物理清理 restic prune"
  run_restic prune
elif [[ "$MULTI" == "true" && "$INTERVAL_MODE" == "true" ]]; then
  # 自调度模式：逐策略判定到期，到期才备份（--policy 显式指定的策略强制执行）
  for p in "${POLICY_LIST[@]}"; do
    if ((${#WANT_LIST[@]} > 0)); then
      want_p="false"
      for w in "${WANT_LIST[@]}"; do
        if [[ "$w" == "$p" ]]; then want_p="true"; break; fi
      done
      if [[ "$want_p" != "true" ]]; then continue; fi
      run_policy_backup "$p"
      RAN_BACKUP="true"
      if [[ "${DRY_RUN}" != "true" ]]; then mark_run "$p"; fi
    else
      imin="$(printenv "POLICY_${p}_INTERVAL_MIN" 2>/dev/null || echo "$PRUNE_INTERVAL_MIN")"
      if is_due "$p" "$imin"; then
        run_policy_backup "$p"
        RAN_BACKUP="true"
        if [[ "${DRY_RUN}" != "true" ]]; then mark_run "$p"; fi
      else
        info "策略 [$p] 未到期（间隔 ${imin} 分钟），跳过"
      fi
    fi
  done
  # prune 独立判到期（与备份频率解耦），只做 forget + prune，不触发校验
  if [[ "${FORGET_ENABLED}" == "true" ]] && is_due "__prune__" "$PRUNE_INTERVAL_MIN" && [[ "${DRY_RUN}" != "true" ]]; then
    run_forget_all
    info "统一执行仓库物理清理 restic prune"
    run_restic prune
    mark_run "__prune__"
  fi
else
  # 传统模式（单策略 / 多策略无 INTERVAL_MIN）：全部策略备份 + forget + prune
  for p in "${POLICY_LIST[@]}"; do
    if ((${#WANT_LIST[@]} > 0)); then
      want_p="false"
      for w in "${WANT_LIST[@]}"; do
        if [[ "$w" == "$p" ]]; then want_p="true"; break; fi
      done
      if [[ "$want_p" != "true" ]]; then continue; fi
    fi
    run_policy_backup "$p"
    RAN_BACKUP="true"
  done
  if ((${#ALL_SOURCES[@]} == 0)); then die "没有有效的备份源"; fi
  if [[ "${FORGET_ENABLED}" == "true" && "${DRY_RUN}" != "true" ]]; then
    run_forget_all
    info "统一执行仓库物理清理 restic prune"
    run_restic prune
  fi
fi

# ---- 仓库校验（随实际发生的备份执行；prune-only / 全跳过时不校验）----
if [[ "${CHECK_ENABLED}" == "true" && "${DRY_RUN}" != "true" && "${PRUNE_ONLY}" != "true" && "$RAN_BACKUP" == "true" ]]; then
  if [[ "${CHECK_FULL_DAY_OF_MONTH}" != "0" && "$(date +%d)" == "${CHECK_FULL_DAY_OF_MONTH}" ]]; then
    info "执行全量校验 restic check --read-data（较慢）"
    run_restic check --read-data
  else
    info "执行抽样校验 restic check --read-data-subset=${CHECK_SUBSET}"
    run_restic check --read-data-subset "$CHECK_SUBSET"
  fi
fi

# ---- 结果汇总 ----
DURATION="$(( $(date +%s) - START_EPOCH ))"
if [[ "${PRUNE_ONLY}" == "true" ]]; then
  SUMMARY="restic 清理完成 | 主机: ${RESTIC_HOST} | 仓库: ${RESTIC_REPOSITORY} | 耗时: ${DURATION}s"
elif [[ "$RAN_BACKUP" != "true" ]]; then
  SUMMARY="restic 无到期策略，本轮跳过 | 主机: ${RESTIC_HOST} | 耗时: ${DURATION}s"
else
  if [[ "$MULTI" == "true" ]]; then
    DETAIL="策略: ${POLICY_LIST[*]}"
  else
    DETAIL="源: ${ALL_SOURCES[*]}"
  fi
  SUMMARY="restic 备份成功 | 主机: ${RESTIC_HOST} | 仓库: ${RESTIC_REPOSITORY} | 开始: ${START_TS} | 耗时: ${DURATION}s | ${DETAIL}"
fi
info "========== ${SUMMARY%% | *}，耗时 ${DURATION}s =========="
hc_ping "" "$SUMMARY"
notify "$SUMMARY" true
exit 0
