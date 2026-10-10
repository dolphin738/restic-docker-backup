#!/usr/bin/env bash
# =============================================================================
#  supervisor-timer.sh —— 用 1Panel「进程守护(Supervisor)」等价替代
#                         restic-backup.service + restic-backup.timer
#
#  与 systemd 两个文件的功能对应：
#    .timer OnCalendar=03:30        → 循环睡到触发点执行（RUN_HOUR/RUN_MIN 可配）
#    .timer Persistent=true         → 启动补跑：今天已过点且当天未成功备份 → 立即跑
#    .timer RandomizedDelaySec=900  → 触发时随机延迟 0~900s（RANDOM_DELAY_SEC 可关）
#    .service ExecStart             → 调用同目录 restic-backup.sh
#    .service Restart=on-failure ×2 → 失败自动重试（RETRY_MAX/RETRY_INTERVAL 可配）
#    .service Nice=10 + ionice idle → 以低 CPU/IO 优先级运行备份
#    .service 进程保活              → 交给 Supervisor：循环被杀自动拉起
#
#  调度模式（自动判定）：
#    daily    —— 默认：每日到点 + 启动补跑（RUN_HOUR/RUN_MIN 可配）
#    interval —— backup.env 中任一策略设置 POLICY_<名>_INTERVAL_MIN 时自动切换：
#                每 CHECK_CYCLE_MIN 分钟（默认 60）巡检一次，调用 restic-backup.sh，
#                由脚本内部按各策略间隔判定到期（状态存 STATE_DIR）
#
#  配套防重入由 restic-backup.sh 内的 flock 保证；请勿与 systemd timer /
#  1Panel 计划任务同时启用。
#
#  配置：读取同目录 backup.env（可选，不存在也能跑），调用方环境变量优先。
#  可配项：RUN_HOUR/RUN_MIN（触发时间）、RETRY_MAX/RETRY_INTERVAL（失败重试）、
#          RANDOM_DELAY_SEC（随机延迟）、STATE_DIR（成功标记目录）、
#          TIMER_LOG_ENABLED/TIMER_LOG_FILE（调度日志落盘开关与路径，默认关）
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
BACKUP_SCRIPT="$SCRIPT_DIR/restic-backup.sh"

# ---------------------------------------------------------------------------
# 配置加载：backup.env（可选）提供默认值，调用方环境变量优先（1Panel 进程守护
# 的环境变量里设置的同名变量会覆盖配置文件）
# ---------------------------------------------------------------------------
CONFIGURABLE=(RUN_HOUR RUN_MIN RETRY_MAX RETRY_INTERVAL RANDOM_DELAY_SEC \
              STATE_DIR TIMER_LOG_ENABLED TIMER_LOG_FILE)
declare -A CALLER_ENV=()
for v in "${CONFIGURABLE[@]}"; do
  if printenv "$v" >/dev/null 2>&1; then CALLER_ENV[$v]="$(printenv "$v")"; fi
done

CONFIG_FILE="${BACKUP_CONFIG:-$SCRIPT_DIR/backup.env}"
if [[ -r "$CONFIG_FILE" ]]; then
  # 仅读取值，不 set -a：配置不导出给子进程（restic-backup.sh 自己会加载同一份配置）
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

: "${RUN_HOUR:=3}"
: "${RUN_MIN:=30}"
: "${RETRY_MAX:=2}"          # 失败后重试次数（不含首次），0 = 不重试
: "${RETRY_INTERVAL:=300}"   # 重试间隔（秒），等价 .service 的 RestartSec
: "${RANDOM_DELAY_SEC:=900}" # 到点后随机延迟上限（秒），0 = 关闭，等价 RandomizedDelaySec
: "${STATE_DIR:=/var/lib/restic-backup}"           # 成功标记目录
: "${TIMER_LOG_ENABLED:=false}"                    # 调度日志落盘开关（true/false，默认关）
: "${TIMER_LOG_FILE:=/var/log/restic-timer.log}"   # 调度日志文件路径

# 环境变量优先：恢复调用方显式设置的值（覆盖配置文件）
for v in "${!CALLER_ENV[@]}"; do
  printf -v "$v" '%s' "${CALLER_ENV[$v]}"
  export "$v"
done
unset CALLER_ENV

STATE_FILE="$STATE_DIR/last-success"

[[ -x "$BACKUP_SCRIPT" ]] || { echo "找不到可执行脚本: $BACKUP_SCRIPT" >&2; exit 1; }
mkdir -p "$STATE_DIR" || { echo "无法创建状态目录: $STATE_DIR" >&2; exit 1; }

# 调度日志默认仅 stdout（由 Supervisor/journald 接管）；
# TIMER_LOG_ENABLED=true 时额外落盘到 TIMER_LOG_FILE（双写），目录不可写自动降级为仅 stdout
TIMER_LOG_ENABLED="${TIMER_LOG_ENABLED,,}"
if [[ "$TIMER_LOG_ENABLED" == "true" ]] && ! mkdir -p "$(dirname "$TIMER_LOG_FILE")" 2>/dev/null; then
  echo "警告: 无法创建调度日志目录 $(dirname "$TIMER_LOG_FILE")，调度日志仅 stdout" >&2
  TIMER_LOG_ENABLED="false"
fi

log() {
  printf '%s [supervisor-timer] %s\n' "$(date '+%F %T')" "$*"
  if [[ "$TIMER_LOG_ENABLED" == "true" ]]; then
    printf '%s [supervisor-timer] %s\n' "$(date '+%F %T')" "$*" >>"$TIMER_LOG_FILE" 2>/dev/null || true
  fi
}

# 低 CPU/IO 优先级前缀（等价 .service 的 Nice=10 + IOSchedulingClass=idle）
PREFIX=()
command -v nice   >/dev/null 2>&1 && PREFIX+=(nice -n 10)
command -v ionice >/dev/null 2>&1 && PREFIX+=(ionice -c3)

run_backup() {
  local attempt=0 rc=0
  while :; do
    log "触发备份（第 $((attempt + 1)) 次尝试）"
    "${PREFIX[@]}" "$BACKUP_SCRIPT"
    rc=$?
    if (( rc == 0 )); then
      date +%F >"$STATE_FILE"
      log "备份成功，已记录 $(cat "$STATE_FILE")"
      return 0
    fi
    attempt=$((attempt + 1))
    if (( attempt > RETRY_MAX )); then
      log "备份失败（退出码 $rc），已达重试上限，等待下个周期"
      return "$rc"
    fi
    log "备份失败（退出码 $rc），${RETRY_INTERVAL}s 后重试"
    sleep "$RETRY_INTERVAL"
  done
}

next_target_epoch() {
  local h m today_target
  h="$1"; m="$2"
  today_target=$(date -d "today ${h}:${m}" +%s)
  if (( $(date +%s) >= today_target )); then
    date -d "tomorrow ${h}:${m}" +%s
  else
    printf '%s' "$today_target"
  fi
}

# ---- 调度模式判定 ----
# interval 模式：backup.env 中任一策略设置了 POLICY_*_INTERVAL_MIN 时启用，
#   巡检循环只负责定期调用 restic-backup.sh（脚本内部按策略判定到期并维护状态）；
# daily 模式（默认）：维持原有"每日到点 + 启动补跑"逻辑。
RUN_MODE="daily"
CHECK_CYCLE_MIN="${CHECK_CYCLE_MIN:-60}"   # 间隔模式的巡检周期（分钟）
for v in $(compgen -v 2>/dev/null | grep '^POLICY_[A-Za-z0-9_]*_INTERVAL_MIN$' || true); do
  if [[ -n "${!v:-}" ]]; then RUN_MODE="interval"; break; fi
done

if [[ "$RUN_MODE" == "interval" ]]; then
  # ---- 间隔巡检模式：启动立即巡检一次，此后每 CHECK_CYCLE_MIN 分钟一次 ----
  # 到期判定/补跑/状态维护全部由 restic-backup.sh 自调度完成
  log "间隔巡检模式：每 ${CHECK_CYCLE_MIN} 分钟巡检一次（各策略频率由 POLICY_*_INTERVAL_MIN 决定）"
  run_backup
  while true; do
    log "休眠 $((CHECK_CYCLE_MIN * 60))s"
    sleep "$((CHECK_CYCLE_MIN * 60))"
    run_backup
  done
fi

# ---- 每日到点模式（≈ systemd OnCalendar + Persistent）----
# 启动即补跑：今天已过触发点且当天未成功备份
last="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
today_target="$(date -d "today ${RUN_HOUR}:${RUN_MIN}" +%s)"
now="$(date +%s)"
if (( now >= today_target && last < today_target )); then
  log "检测到今天尚未备份且已过触发点，启动补跑"
  run_backup
fi

# ---- 常驻循环：睡到下一个触发点 ----
while true; do
  now="$(date +%s)"
  target="$(next_target_epoch "$RUN_HOUR" "$RUN_MIN")"
  wait=$(( target - now + 10 ))
  log "下次触发: $(date -d "@$target" '+%F %T')，睡眠 ${wait}s"
  sleep "$wait"
  if (( RANDOM_DELAY_SEC > 0 )); then
    delay=$(( RANDOM % RANDOM_DELAY_SEC ))
    log "随机延迟 ${delay}s（≈ RandomizedDelaySec）"
    sleep "$delay"
  fi
  run_backup
done
