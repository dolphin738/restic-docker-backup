#!/usr/bin/env bash
# =============================================================================
#  restic.sh —— Docker 版 restic 命令行透传（复用 backup.env 的仓库/挂载配置）
#
#  用法： ./restic.sh <restic 子命令与参数...>
#  示例：
#    ./restic.sh snapshots
#    ./restic.sh list snapshots
#    ./restic.sh restore latest --target /restore
#    ./restic.sh restore <snapshot_id> --target /restore --include /var/www
#    ./restic.sh check --read-data
#    ./restic.sh unlock
#    ./restic.sh init                # 首次初始化仓库
#    ./restic.sh stats --mode raw-size
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
CONFIG_FILE="${BACKUP_CONFIG:-$SCRIPT_DIR/backup.env}"

[[ -r "$CONFIG_FILE" ]] || { echo "配置文件不可读: $CONFIG_FILE" >&2; exit 1; }
(($#)) || { echo "用法: $0 <restic 参数...>（如 snapshots / restore latest --target /restore）" >&2; exit 2; }

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

: "${RESTIC_IMAGE:=restic/restic:0.19.1}"
: "${CACHE_DIR:=/var/cache/restic}"
: "${RESTIC_CACHE_DIR:=/cache}"
: "${SELINUX_LABEL:=auto}"
: "${TZ:=UTC}"
: "${RESTIC_ENV_VARS:=RESTIC_REPOSITORY}"
if [[ -z "${RESTIC_REPOSITORY:-}" ]]; then echo "RESTIC_REPOSITORY 未配置" >&2; exit 1; fi

DOCKER_ARGS=(--rm)
if [[ -t 0 ]]; then DOCKER_ARGS+=( -it ); fi   # 交互终端才分配 tty，cron 下不报警
export TZ RESTIC_REPOSITORY RESTIC_CACHE_DIR RESTIC_PASSWORD_FILE

# 密码
if [[ -n "${RESTIC_PASSWORD_FILE_HOST:-}" && -n "${RESTIC_PASSWORD_FILE:-}" ]]; then
  if [[ ! -r "$RESTIC_PASSWORD_FILE_HOST" ]]; then echo "密码文件不可读: $RESTIC_PASSWORD_FILE_HOST" >&2; exit 1; fi
  DOCKER_ARGS+=( -v "$RESTIC_PASSWORD_FILE_HOST:$RESTIC_PASSWORD_FILE:ro" )
elif [[ -n "${RESTIC_PASSWORD:-}" ]]; then
  export RESTIC_PASSWORD
  RESTIC_ENV_VARS="${RESTIC_ENV_VARS} RESTIC_PASSWORD"
else
  echo "未配置仓库密码（RESTIC_PASSWORD_FILE_HOST 或 RESTIC_PASSWORD）" >&2; exit 1
fi

# 凭据透传
for name in $RESTIC_ENV_VARS; do
  if printenv "$name" >/dev/null 2>&1; then DOCKER_ARGS+=( -e "$name" ); fi
done

# 缓存
mkdir -p "$CACHE_DIR"
DOCKER_ARGS+=( -v "$CACHE_DIR:$RESTIC_CACHE_DIR" -e RESTIC_CACHE_DIR -e TZ )

# SELinux 标签
# 源挂载为「可写」（区别于主备份脚本的只读）：restic.sh 承担恢复/管理职责，
# restore --target / 原位还原需要写权限；误写风险由用户显式执行的 restic 命令控制
mount_opts_rw() {
  local s=""
  case "${SELINUX_LABEL}" in
    z|Z)  s=",${SELINUX_LABEL}" ;;
    none) s="" ;;
    auto) if [[ -d /sys/fs/selinux ]] && command -v getenforce >/dev/null 2>&1; then s=",z"; fi ;;
  esac
  printf '%s' "$s"
}

# 备份源挂载（restore --target / 或 --include 时需要目标路径存在）
lines_to_array() {
  local __name="$1" __input="${2:-}"
  local -a __arr=()
  local __line
  while IFS= read -r __line; do
    __line="${__line%%#*}"
    __line="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$__line")"
    if [[ -n "$__line" ]]; then __arr+=("$__line"); fi
  done < <(printf '%s\n' "$__input" | tr -d '\r')
  if ((${#__arr[@]} == 0)); then eval "$__name=()"; else eval "$__name=(\"\${__arr[@]}\")"; fi
}
lines_to_array SOURCES "${BACKUP_SOURCES:-}"
for src in "${SOURCES[@]}"; do
  if [[ "$src" == *:* ]]; then
    echo "警告: 不支持映射语法（路径含冒号），已跳过: $src" >&2
  elif [[ -e "$src" ]]; then
    DOCKER_ARGS+=( -v "$src:$src$(mount_opts_rw)" )
  fi
done

# 额外挂载（rclone/SSH）
if [[ -n "${EXTRA_MOUNTS:-}" ]]; then
  lines_to_array EXTRA_MOUNTS_ARR "$EXTRA_MOUNTS"
  for m in "${EXTRA_MOUNTS_ARR[@]}"; do
    IFS=':' read -r mhost mcont mopts <<<"$m"
    if [[ -z "${mcont:-}" ]]; then mcont="$mhost"; fi
    if [[ -z "${mopts:-}" ]]; then mopts="ro"; fi
    if [[ -e "$mhost" ]]; then DOCKER_ARGS+=( -v "$mhost:$mcont:$mopts" ); fi
  done
fi

# 本地路径仓库：自动挂载进容器（可写，init/备份/校验都会写仓库）
if [[ "$RESTIC_REPOSITORY" != *:* && "$RESTIC_REPOSITORY" == /* ]]; then
  mkdir -p "$RESTIC_REPOSITORY" 2>/dev/null || true
  DOCKER_ARGS+=( -v "$RESTIC_REPOSITORY:$RESTIC_REPOSITORY$(mount_opts_rw)" )
fi

# 恢复目标目录挂载（-t/--target 指定的宿主机目录）
# 三种取值：
#   /            → 原位恢复：宿主机 / 挂到容器 /host-root（可写），目标改写为 /host-root，
#                  快照内绝对路径 /home/... → /host-root/home/... = 宿主机 /home/...
#   /绝对/子目录  → 挂到同名容器路径（普通恢复）
#   相对路径      → 直接报错退出（Docker 不接受相对挂载点）
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--target" && -n "${args[i+1]:-}" ]]; then
    tdir="${args[i+1]}"
    # 规范化为绝对路径（/root/. → /root，. → 当前目录）；规范化失败则拦截
    tdir="$(realpath -m "$tdir" 2>/dev/null || true)"
    if [[ -z "$tdir" || "$tdir" != /* ]]; then
      echo "错误: 恢复目标无法解析为绝对路径，请输入如 /restore 或 /" >&2
      exit 1
    fi
    if [[ "$tdir" == "/" ]]; then
      DOCKER_ARGS+=( -v "/:/host-root$(mount_opts_rw)" )
      args[i+1]="/host-root"
      echo "提示: 原位恢复模式——快照将按原路径覆盖写入宿主机，请确认相关服务已停止" >&2
    else
      mkdir -p "$tdir"
      DOCKER_ARGS+=( -v "$tdir:$tdir$(mount_opts_rw)" )
    fi
  fi
done

exec docker run "${DOCKER_ARGS[@]}" "$RESTIC_IMAGE" "${args[@]}"
