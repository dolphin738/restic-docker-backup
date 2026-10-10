#!/usr/bin/env bash
# =============================================================================
#  menu.sh —— restic-docker 备份套件交互式管理菜单
#
#  集成套件全部手动操作：初始化部署 / 定时任务安装 / 仓库管理 / 恢复向导 /
#  密码与安全 / 状态体检 / 日志查看
#
#  用法： sudo bash menu.sh        （建议 root 运行）
#  说明： 所有 restic 操作经由 restic.sh 透传，自动复用 backup.env 配置与挂载；
#        危险操作（清理快照/覆盖密码）均有确认门，密码文件永不覆盖。
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
BACKUP_ENV="$SCRIPT_DIR/backup.env"
BACKUP_ENV_EXAMPLE="$SCRIPT_DIR/backup.env.example"
BACKUP_SCRIPT="$SCRIPT_DIR/restic-backup.sh"
RESTIC_CMD="$SCRIPT_DIR/restic.sh"
SYSTEMD_DIR="$SCRIPT_DIR/systemd"

BLUE='\033[1;34m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'

pause() { read -rp "按回车返回菜单..." _; }
info()  { printf "${GREEN}%s${NC}\n" "$*"; }
warn()  { printf "${YELLOW}%s${NC}\n" "$*"; }
err()   { printf "${RED}%s${NC}\n" "$*"; }

# 从 backup.env 读取 KEY=VALUE（支持多行值，如 BACKUP_SOURCES）；不存在返回空
cfg_value() {
  local key="$1"
  [[ -r "$BACKUP_ENV" ]] || return 1
  awk -v k="$key" '
    !found && $0 ~ "^"k"=" {
      found=1
      sub(/^[^=]*=/, "")
      if ($0 == "\"") { open=1; next }        # 多行值开始
      gsub(/^"|"$/, "", $0); print; exit
    }
    found && open {
      if ($0 ~ /"$/) { sub(/"$/, "", $0); print; exit }
      print
    }
  ' "$BACKUP_ENV"
}

restic_cmd() { bash "$RESTIC_CMD" "$@"; }

confirm() {  # confirm "提示" -> 0=同意
  local ans
  read -rp "$1 [y/N]: " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

# 交互输入；输入 q/Q 返回 1（调用方据此取消当前操作，回到上级菜单）
ask() {  # ask <变量名> <提示语>
  read -rp "$2" "$1"
  [[ "${!1,,}" == "q" ]] && return 1
  return 0
}

need_config() {
  if [[ ! -r "$BACKUP_ENV" ]]; then
    err "未找到 $BACKUP_ENV"
    if [[ -r "$BACKUP_ENV_EXAMPLE" ]] && confirm "是否现在从 backup.env.example 复制一份？"; then
      cp "$BACKUP_ENV_EXAMPLE" "$BACKUP_ENV"
      chmod 600 "$BACKUP_ENV"
      warn "已创建，请稍后用 首页[1] 安装与初始化 → [2] 编辑备份配置（仓库地址/备份源/密码路径为必改项）"
    else
      return 1
    fi
  fi
  return 0
}

# ---------------------------------------------------------------------------
# 动作实现
# ---------------------------------------------------------------------------
act_gen_password() {
  local pass_file
  pass_file="$(cfg_value RESTIC_PASSWORD_FILE_HOST || echo /etc/restic/pass)"
  if [[ -s "$pass_file" ]]; then
    warn "密码文件已存在: $pass_file —— 绝不覆盖（覆盖 = 旧仓库数据永久无法解密）"
    warn "如确需更换密码，请先确认旧仓库已废弃，再手工操作"
    return 0
  fi
  info "将生成随机密码文件: $pass_file（umask 077，权限 600）"
  if ! confirm "确认生成？"; then return 0; fi
  sh -c "umask 077; mkdir -p '$(dirname "$pass_file")'; head -c 32 /dev/urandom | base64 > '$pass_file'"
  chmod 600 "$pass_file"
  info "已生成。【重要】立即把以下密码内容另存到密码管理器/离线一份（丢失 = 数据全丢）："
  cat "$pass_file"
  warn "密码与安全菜单（首页[5] → 1）可随时重新查看；请勿把该文件提交到任何仓库"
}

act_edit_config() {
  need_config || { pause; return; }
  warn "必改项：RESTIC_REPOSITORY / RESTIC_PASSWORD_FILE_HOST / BACKUP_SOURCES"
  ${EDITOR:-vi} "$BACKUP_ENV" </dev/tty >/dev/tty
  info "配置已保存。可执行 首页[6] 系统工具 → [1] 做状态体检"
}

act_init_repo() {
  need_config || { pause; return; }
  warn "仅在首次部署时需要；对已初始化的仓库执行会报错（无破坏性）"
  if confirm "执行 restic init？"; then
    restic_cmd init
  fi
}

act_dry_run() {
  need_config || { pause; return; }
  info "演练模式：不实际写入、不清理、不校验"
  bash "$BACKUP_SCRIPT" --dry-run
}

act_full_backup() {
  need_config || { pause; return; }
  if confirm "执行一次完整备份（含数据库 dump、保留策略、校验）？"; then
    bash "$BACKUP_SCRIPT"
  fi
}

# 定时方式四路检测（被 act_summary 的状态体检调用）
check_schedule() {
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-enabled restic-backup.timer >/dev/null 2>&1; then
      info "定时①systemd timer : 已启用（$(systemctl is-active restic-backup.timer)）"
    else
      warn "定时①systemd timer : 未启用"
    fi
  else
    warn "定时①systemd       : 不存在"
  fi
  if crontab -l 2>/dev/null | grep -q restic-backup; then
    info "定时②crontab       : 存在 restic-backup 条目"
  else
    warn "定时②crontab       : 无 restic-backup 条目"
  fi
  if pgrep -f "supervisor-timer.sh" >/dev/null 2>&1; then
    info "定时③supervisor    : 正在运行（1Panel 进程守护方式）"
  else
    warn "定时③supervisor    : 未检测到运行中的 supervisor-timer.sh"
  fi
  if [[ -d /opt/1panel ]]; then
    info "定时④1Panel        : 已安装（计划任务/进程守护请在面板界面查看与配置）"
  else
    warn "定时④1Panel        : 未检测到"
  fi
  warn "提醒：定时方式只能启用一种，并存会造成日志混乱（flock 会兜底防止并发备份）"
}

act_schedule_guide() {
  cat <<'EOF'
────────── 1Panel 接入指引 ──────────
三种定时方式只能启用一种（systemd timer / 1Panel 计划任务 / 1Panel 进程守护）。

【方式 A：1Panel 计划任务】（简单，无补跑机制）
  面板路径：计划任务 → 创建任务
    任务类型     : Shell 脚本
    周期         : 每天 凌晨 03:30
    执行超时时间 : 调大（建议 12h，大仓库备份会被默认超时掐断）
    脚本内容     : /opt/restic-docker-backup/restic-backup.sh

【方式 B：1Panel 进程守护(Supervisor)】（等价 systemd timer，含当天补跑）
  面板路径：进程守护 → 添加守护进程
    名称         : restic-timer
    启动命令     : <套件目录>/supervisor-timer.sh
    运行用户     : root
    自动启动     : 是
    环境变量(可选): RUN_HOUR="3",RUN_MIN="30"   # 注意 Supervisor 语法：逗号分隔+引号包值，
                    #                          多个变量不能用空格分隔（会报 CANT_REREAD）；
                    #                          留空 = 默认 03:30
                    TIMER_LOG_ENABLED="true"   # 开启调度日志落盘（默认关）
                    TIMER_LOG_FILE="/var/log/restic-timer.log"
  行为：每天到点触发；重启后当天已过点且未备份则立即补跑（≈ Persistent=true）；
        备份失败按 RETRY_MAX 次数自动重试。

【差异化频率（自调度）】任一策略在 backup.env 设置 POLICY_<名>_INTERVAL_MIN（分钟）后：
  - 方式 B 的进程守护自动切换为间隔巡检模式（CHECK_CYCLE_MIN 默认 60 分钟）；
  - 方式 A 加一条高频计划任务即可：./restic-backup.sh --policy <策略名>；
  - systemd timer 单元触发时间改为 hourly 即可承载全部策略；
  - 清理独立判到期（PRUNE_INTERVAL_MIN，默认每天一次），与备份频率解耦。

详见 README.md「1Panel 接入」与「自调度（差异化频率）」一节。
EOF
}

act_install_timer() {
  need_config || return 1
  if ! command -v systemctl >/dev/null 2>&1; then
    err "未检测到 systemd，请改用 1Panel 计划任务 / crontab / 进程守护（见 README）"
    return 1
  fi
  for f in restic-backup.service restic-backup.timer; do
    [[ -r "$SYSTEMD_DIR/$f" ]] || { err "缺少 $SYSTEMD_DIR/$f"; return 1; }
  done
  # 调度参数来自 backup.env（与 supervisor-timer 版共用同一套配置）
  local schedule rdelay rinterval tstart
  schedule="$(cfg_value SCHEDULE)";        schedule="${schedule:-*-*-* 03:30:00}"
  rdelay="$(cfg_value RANDOM_DELAY_SEC)";  rdelay="${rdelay:-900}"
  rinterval="$(cfg_value RETRY_INTERVAL)"; rinterval="${rinterval:-300}"
  tstart="$(cfg_value TIMEOUT_START_SEC)"; tstart="${tstart:-6h}"
  warn "将按实际路径 $(dirname "$BACKUP_SCRIPT") 安装，调度参数："
  echo "  触发时间(OnCalendar) : $schedule"
  echo "  随机延迟上限         : ${rdelay}s"
  echo "  失败重试间隔         : ${rinterval}s"
  echo "  单次备份超时         : $tstart"
  if ! confirm "确认安装并启用？"; then return 0; fi
  for f in restic-backup.service restic-backup.timer; do
    sed -e "s|/opt/restic-docker-backup|$SCRIPT_DIR|g" \
        -e "s|\*-\*-\* 03:30:00|$schedule|" \
        -e "s|RandomizedDelaySec=900|RandomizedDelaySec=$rdelay|" \
        -e "s|RestartSec=5min|RestartSec=${rinterval}s|" \
        -e "s|TimeoutStartSec=6h|TimeoutStartSec=$tstart|" \
        "$SYSTEMD_DIR/$f" > "/etc/systemd/system/$f"
  done
  systemctl daemon-reload
  systemctl enable --now restic-backup.timer
  info "已启用。下次触发时间："
  systemctl list-timers restic-backup.timer --no-pager | head -3
  warn "注意：1Panel 计划任务 / 进程守护与此定时器只能启用其一"
}

act_uninstall_timer() {
  command -v systemctl >/dev/null 2>&1 || { err "未检测到 systemd"; return 1; }
  if ! confirm "确认停用并移除 restic-backup.timer？"; then return 0; fi
  systemctl disable --now restic-backup.timer 2>/dev/null || true
  rm -f /etc/systemd/system/restic-backup.service /etc/systemd/system/restic-backup.timer
  systemctl daemon-reload
  info "已移除（备份脚本与仓库数据不受影响）"
}

act_snapshots()  { need_config && restic_cmd snapshots; }
act_stats()      { need_config && restic_cmd stats latest --mode restore-size; }

act_check() {
  need_config || { pause; return; }
  local m s def_subset
  def_subset="$(cfg_value CHECK_SUBSET 2>/dev/null)"
  def_subset="${def_subset:-10%}"
  while true; do
    echo "────────── 仓库校验 ──────────"
    echo "  1) 抽样校验（快，日常使用）"
    echo "  2) 全量校验 --read-data（慢，彻底，建议每月一次）"
    echo "  0) 返回上级菜单"
    echo -n "请选择: "
    read -r m
    case "$m" in
      1)
        ask s "抽样比例 [$def_subset]（可选 10% / 1/3 / 100M，q = 返回）: " || continue
        restic_cmd check --read-data-subset "${s:-$def_subset}"
        pause
        ;;
      2)
        warn "全量校验耗时较长、IO 高，确认现在执行？"
        if confirm "执行 --read-data？"; then restic_cmd check --read-data; fi
        pause
        ;;
      0) return 0 ;;
      *) warn "无效选择: $m" ;;
    esac
  done
}

act_unlock() {
  warn "unlock 会移除仓库锁文件；确认没有其他备份进程正在运行再继续"
  if confirm "执行 restic unlock？"; then restic_cmd unlock; fi
}

act_forget() {
  need_config || { pause; return; }
  err "forget --prune 会物理删除过期快照的数据块，删除后不可恢复！"
  # 默认保留份数：优先 KEEP_LAST，为 0 时取 KEEP_DAILY，再兜底 5
  local def_keep tag keep args
  def_keep="$(cfg_value KEEP_LAST 2>/dev/null)"
  if [[ -z "$def_keep" || "$def_keep" == "0" ]]; then def_keep="$(cfg_value KEEP_DAILY 2>/dev/null)"; fi
  def_keep="${def_keep:-5}"
  ask tag "仅针对标签（留空 = 全部快照，q = 取消）: " || { warn "已取消"; return 0; }
  ask keep "保留最近 N 份 [$def_keep]（q = 取消）: " || { warn "已取消"; return 0; }
  keep="${keep:-$def_keep}"
  args=(forget --keep-last "$keep" --group-by "$(cfg_value FORGET_GROUP_BY 2>/dev/null || echo host,paths)")
  [[ -n "$tag" ]] && args+=(--tag "$tag")
  info "先预览（--dry-run），将删除的快照会列出来："
  restic_cmd "${args[@]}" --dry-run || { pause; return; }
  if confirm "确认按上述结果实际执行 --prune？"; then
    restic_cmd "${args[@]}" --prune
  fi
}

act_restore() {
  need_config || { pause; return; }
  info "快照列表："
  restic_cmd snapshots || { pause; return; }
  local snap target inc def_target
  def_target="$(cfg_value RESTORE_TARGET 2>/dev/null)"
  def_target="${def_target:-/restore}"
  ask snap "要恢复的快照 ID [latest]（q = 取消）: " || { warn "已取消"; return 0; }
  snap="${snap:-latest}"
  ask inc "只恢复某路径？(--include，留空 = 全部，q = 取消): " || { warn "已取消"; return 0; }
  ask target "恢复目标目录 [$def_target]（q = 取消；填 / = 原位覆盖恢复）: " || { warn "已取消"; return 0; }
  target="${target:-$def_target}"
  if [[ "$target" == "/" ]]; then
    err "原位恢复将按快照内路径覆盖写入宿主机原位置！"
    warn "执行前请停止相关服务（网站/数据库容器），避免恢复过程中文件被读写"
  fi
  mkdir -p "$target" 2>/dev/null || true
  warn "将恢复快照 [$snap] 到 $target${inc:+（仅 $inc）}"
  if confirm "确认恢复？"; then
    if [[ -n "$inc" ]]; then
      restic_cmd restore "$snap" --target "$target" --include "$inc"
    else
      restic_cmd restore "$snap" --target "$target"
    fi
  fi
}

act_show_password() {
  need_config || { pause; return; }
  local pass_file
  pass_file="$(cfg_value RESTIC_PASSWORD_FILE_HOST || echo /etc/restic/pass)"
  if [[ ! -r "$pass_file" ]]; then err "密码文件不存在或不可读: $pass_file"; return 1; fi
  warn "即将显示仓库密码明文——请确认周围无人、无录屏"
  if confirm "继续显示？"; then
    cat "$pass_file"
    warn "【务必】确认该密码已在密码管理器/离线介质中另存一份"
  fi
}

act_summary() {
  echo "────────── 配置摘要 ──────────"
  if [[ -r "$BACKUP_ENV" ]]; then
    echo "仓库地址     : $(cfg_value RESTIC_REPOSITORY)"
    echo "镜像版本     : $(cfg_value RESTIC_IMAGE)"
    if [[ -n "$(cfg_value BACKUP_POLICIES)" ]]; then
      echo "备份模式     : 多策略（$(cfg_value BACKUP_POLICIES)）"
      local p kd kw km ky
      for p in $(cfg_value BACKUP_POLICIES); do
        kd="$(cfg_value "POLICY_${p}_KEEP_DAILY")"; kw="$(cfg_value "POLICY_${p}_KEEP_WEEKLY")"
        km="$(cfg_value "POLICY_${p}_KEEP_MONTHLY")"; ky="$(cfg_value "POLICY_${p}_KEEP_YEARLY")"
        echo "  [$p] 标签   : $(cfg_value "POLICY_${p}_TAG")"
        echo "  [$p] 源     : $(cfg_value "POLICY_${p}_SOURCES" | tr '\n' ' ')"
        echo "  [$p] 保留   : 日${kd:-0}/周${kw:-0}/月${km:-0}/年${ky:-0}"
        iv="$(cfg_value "POLICY_${p}_INTERVAL_MIN")"
        if [[ -n "$iv" ]]; then
          sd="$(cfg_value STATE_DIR 2>/dev/null)"; sd="${sd:-/var/lib/restic-backup}"
          lr="$(cat "$sd/last-$p" 2>/dev/null || echo 0)"
          if [[ "$lr" == "0" ]]; then lrs="从未运行"; else lrs="$(date -d "@$lr" '+%F %T' 2>/dev/null || echo "?")"; fi
          echo "  [$p] 频率   : 每 ${iv} 分钟（上次运行: $lrs）"
        fi
      done
    else
      echo "备份源       : $(cfg_value BACKUP_SOURCES | tr '\n' ' ')"
      echo "备份标签     : $(cfg_value BACKUP_TAG)"
      echo "保留策略     : 日$(cfg_value KEEP_DAILY)/周$(cfg_value KEEP_WEEKLY)/月$(cfg_value KEEP_MONTHLY)/年$(cfg_value KEEP_YEARLY)，FORGET_TAG=$(cfg_value FORGET_TAG || echo 未设置)"
    fi
    echo "数据库备份   : $(cfg_value DB_ENABLED) ($(cfg_value DB_TYPE))"
    echo "备份日志     : $(cfg_value LOG_FILE)"
  else
    warn "backup.env 不存在（可从 backup.env.example 复制，或走 首页[1] 安装与初始化流程）"
  fi
  echo "────────── 状态体检 ──────────"
  local pass_file
  pass_file="$(cfg_value RESTIC_PASSWORD_FILE_HOST 2>/dev/null || echo /etc/restic/pass)"
  if [[ -s "$pass_file" ]]; then info "密码文件     : OK ($pass_file)"; else err "密码文件     : 缺失 ($pass_file)"; fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    info "docker       : OK"
    docker image inspect "$(cfg_value RESTIC_IMAGE 2>/dev/null || echo restic/restic:0.19.1)" >/dev/null 2>&1 \
      && info "restic 镜像  : OK（本地已存在）" || warn "restic 镜像  : 本地不存在（首次备份会自动拉取）"
  else
    err "docker       : 不可用"
  fi
  local log_file
  log_file="$(cfg_value LOG_FILE 2>/dev/null || echo /var/log/restic-backup.log)"
  if [[ -f "$log_file" ]]; then
    echo "最近备份日志 : $(stat -c '%y' "$log_file" 2>/dev/null | cut -d. -f1)（$log_file）"
  else
    warn "备份日志     : 尚未生成（还没跑过备份？）"
  fi
  echo "────────── 定时方式（只能启用一种）──────────"
  check_schedule
}

act_tail_log() {
  local log_file
  log_file="$(cfg_value LOG_FILE 2>/dev/null || echo /var/log/restic-backup.log)"
  if [[ ! -f "$log_file" ]]; then err "日志文件不存在: $log_file"; return 1; fi
  local n
  ask n "显示最近多少行 [50]（q = 取消）: " || { warn "已取消"; return 0; }
  tail -n "${n:-50}" "$log_file"
}

# ---------------------------------------------------------------------------
# 菜单（两级：首页 → 分组子菜单，子菜单 0 均返回首页）
# ---------------------------------------------------------------------------
menu_header() { echo -e "${BLUE}══════════ $1 ══════════${NC}"; }

menu_install() {
  while true; do
    clear
    menu_header "安装与初始化"
    echo "  1) 生成仓库密码文件（已存在则跳过，绝不覆盖）"
    echo "  2) 编辑备份配置 backup.env"
    echo "  3) 初始化备份仓库（restic init，仅首次）"
    echo "  4) 演练备份（--dry-run，不写入）"
    echo "  5) 执行一次完整备份"
    echo "  0) 返回首页菜单"
    echo -n "请选择: "
    local c; read -r c
    case "$c" in
      1) act_gen_password; pause ;;
      2) act_edit_config; pause ;;
      3) act_init_repo; pause ;;
      4) act_dry_run; pause ;;
      5) act_full_backup; pause ;;
      0) return 0 ;;
      *) warn "无效选择: $c" ;;
    esac
  done
}

menu_schedule() {
  while true; do
    clear
    menu_header "定时任务（状态体检见 首页[6] → [1]）"
    echo "  1) 安装/启用 systemd timer（触发时间见 backup.env 第 12 节 SCHEDULE）"
    echo "  2) 停用/卸载 systemd timer"
    echo "  3) 显示 1Panel 接入指引（计划任务 / 进程守护）"
    echo "  0) 返回首页菜单"
    echo -n "请选择: "
    local c; read -r c
    case "$c" in
      1) act_install_timer; pause ;;
      2) act_uninstall_timer; pause ;;
      3) act_schedule_guide; pause ;;
      0) return 0 ;;
      *) warn "无效选择: $c" ;;
    esac
  done
}

menu_repo() {
  while true; do
    clear
    menu_header "仓库管理"
    echo "  1) 查看快照列表"
    echo "  2) 查看仓库体积统计"
    echo "  3) 仓库校验（抽样 / 全量）"
    echo "  4) 解锁仓库（unlock）"
    echo "  5) 手动清理旧快照（forget --prune，先预览后执行）"
    echo "  0) 返回首页菜单"
    echo -n "请选择: "
    local c; read -r c
    case "$c" in
      1) act_snapshots; pause ;;
      2) act_stats; pause ;;
      3) act_check ;;   # 自带循环与返回菜单，无需外层 pause
      4) act_unlock; pause ;;
      5) act_forget; pause ;;
      0) return 0 ;;
      *) warn "无效选择: $c" ;;
    esac
  done
}

menu_restore() {
  while true; do
    clear
    menu_header "恢复"
    echo "  1) 恢复向导（列表 → 选快照 → 选目标）"
    echo "  0) 返回首页菜单"
    echo -n "请选择: "
    local c; read -r c
    case "$c" in
      1) act_restore; pause ;;
      0) return 0 ;;
      *) warn "无效选择: $c" ;;
    esac
  done
}

menu_secret() {
  while true; do
    clear
    menu_header "密码与安全"
    echo "  1) 显示仓库密码（用于离线备份）"
    echo "  0) 返回首页菜单"
    echo -n "请选择: "
    local c; read -r c
    case "$c" in
      1) act_show_password; pause ;;
      0) return 0 ;;
      *) warn "无效选择: $c" ;;
    esac
  done
}

menu_system() {
  while true; do
    clear
    menu_header "系统工具"
    echo "  1) 配置摘要与状态体检"
    echo "  2) 查看备份日志（tail）"
    echo "  0) 返回首页菜单"
    echo -n "请选择: "
    local c; read -r c
    case "$c" in
      1) act_summary; pause ;;
      2) act_tail_log; pause ;;
      0) return 0 ;;
      *) warn "无效选择: $c" ;;
    esac
  done
}

show_main() {
  clear
  menu_header "restic-docker 备份套件管理（首页）"
  echo "  1) 安装与初始化"
  echo "  2) 定时任务"
  echo "  3) 仓库管理"
  echo "  4) 恢复"
  echo "  5) 密码与安全"
  echo "  6) 系统工具"
  echo "  0) 退出"
  echo -n "请选择: "
}

main() {
  if [[ $EUID -ne 0 ]]; then
    warn "当前非 root 用户：查看类操作可用，安装/备份/密码类操作可能因权限失败"
  fi
  while true; do
    show_main
    local choice
    read -r choice
    case "$choice" in
      1) menu_install ;;
      2) menu_schedule ;;
      3) menu_repo ;;
      4) menu_restore ;;
      5) menu_secret ;;
      6) menu_system ;;
      0) info "再见。备份使命必达。"; exit 0 ;;
      *) warn "无效选择: $choice" ;;
    esac
  done
}

trap 'echo; exit 0' INT
main
