#!/bin/sh
# 容器入口：首启属主检查（root 档自动 chown，非 root 档报错提示），再 exec 应用进程。
# 属主问题对应方案 §4「bind-mount 属主要求」与风险表 R3：属主错误会导致 SQLite 建库失败、容器起不来。
set -eu

DATA_DIR="${DATA_DIR:-/data}"
CACHE_DIR="${CACHE_DIR:-/cache/restic}"
PORT="${PORT:-8000}"
PUID="${PUID:-0}"
PGID="${PGID:-0}"
# 仓库密码文件根目录（app.settings.passfile_root，默认 /data/repos）。
# 仅在**显式设置且位于 DATA_DIR 之外**时才处理（见文件末尾 root/非 root 两支）：
# 默认值本就在 /data 内、已由下面的 chown 覆盖；不显式设置时行为与旧版完全一致
# （否则 /data/repos 尚未创建的老部署会被 need_dir 直接判死）。
PASSFILE_ROOT="${PASSFILE_ROOT:-}"
# rclone conf 加密口令文件（方案《rclone配置落盘加密实施方案》§5.8）。
# 独立于仓库密码文件——conf 是**全局**资源，而 <passfile_root>/<repo_id>/password 是
# 某仓库的生命周期对象（key.rotate / rebuild / purge 都会改写或删除它）；
# 绑在一起会在轮换仓库密码时**静默**让加密 conf 永久解不开。
# 未设置 = 不做任何事（本功能默认关闭，见方案 §5.2）。
RCLONE_PASS_FILE="${RCLONE_PASS_FILE:-}"

need_dir() {
    _d="$1"
    _hint="${2:-<宿主机 data 目录> <宿主机 cache 目录>}"
    if [ ! -d "$_d" ]; then
        echo "[entrypoint] 错误：目录 $_d 不存在。" >&2
        echo "[entrypoint] 请在 compose 中挂载该目录，或在宿主机先创建后重启容器。" >&2
        exit 1
    fi
    if [ ! -w "$_d" ]; then
        echo "[entrypoint] 错误：目录 $_d 对当前用户 uid=$(id -u) gid=$(id -g) 不可写。" >&2
        echo "[entrypoint] 非 root 档部署请在宿主机执行：" >&2
        echo "[entrypoint]   chown -R $(id -u):$(id -g) $_hint" >&2
        exit 1
    fi
}

# 密钥与数据分离（部署指南 §2.6）：PASSFILE_ROOT 显式指向 DATA_DIR 之外时，
# 它是一个**独立挂载卷**，须与 /data 一样处理属主与可写性。
#   - 未设置 / 等于默认 /data/repos（在 DATA_DIR 内）→ 不单列，由下面的 chown 覆盖；
#   - 设置且在外面 → 单独处理（root 档 chown，非 root 档 need_dir 硬检查）。
# 判定用前缀比较而非字符串相等：/data 本身或其任意子目录都算"在 DATA_DIR 内"。
SEPARATE_PASSFILE=0
if [ -n "$PASSFILE_ROOT" ]; then
    _pfr="$(echo "$PASSFILE_ROOT" | sed 's:/*$::')"
    _ddr="$(echo "$DATA_DIR" | sed 's:/*$::')"
    if [ "$_pfr" != "$_ddr" ] && [ "${_pfr#$_ddr/}" = "$_pfr" ]; then
        SEPARATE_PASSFILE=1
    fi
fi

# ---------------------------------------------------------------- rclone conf 口令
# 仅当 RCLONE_PASS_FILE 显式设置时生效：文件缺失则**首次自动生成**（32 位 A-Za-z0-9，
# 与 utils.passfile 的字符集口径一致——那里四类混排，此处取纯字母数字，因该口令要经
# `cat` 原样喂给 rclone，避免 shell/编辑器对特殊字符的二次解释）。
# 已存在则**绝不覆盖**（覆盖 = 加密 conf 当场解不开）。
ensure_rclone_pass() {
    _f="$RCLONE_PASS_FILE"
    if [ -e "$_f" ]; then
        echo "[entrypoint] rclone 口令文件已存在，保持不变：$_f"
        return 0
    fi
    mkdir -p "$(dirname "$_f")"
    _p=''
    while [ ${#_p} -lt 32 ]; do
        _p="$_p$(head -c 64 /dev/urandom | tr -dc 'A-Za-z0-9')"
    done
    ( umask 077; printf '%.32s' "$_p" > "$_f" )
    chmod 600 "$_f"
    echo "[entrypoint] 已生成 rclone 口令文件（0600，32 位）：$_f"
    echo "[entrypoint] 请立即离线备份该文件——加密 conf 的口令丢失后无法恢复。"
}

if [ "$(id -u)" = "0" ]; then
    # root 档：自动建目录并 chown 到目标 uid/gid（默认 0:0；非 root 部署可经 PUID/PGID 指定 1000）
    mkdir -p "$DATA_DIR/jobs" "$CACHE_DIR"
    chown -R "$PUID:$PGID" "$DATA_DIR" "$CACHE_DIR"
    chmod 700 "$DATA_DIR"
    echo "[entrypoint] root 档：已将 $DATA_DIR / $CACHE_DIR 属主置为 $PUID:$PGID"
    if [ "$SEPARATE_PASSFILE" = "1" ]; then
        mkdir -p "$PASSFILE_ROOT"
        chown -R "$PUID:$PGID" "$PASSFILE_ROOT"
        chmod 700 "$PASSFILE_ROOT"
        echo "[entrypoint] root 档：已将密钥卷 $PASSFILE_ROOT 属主置为 $PUID:$PGID（与 /data 分离）"
    fi
    if [ -n "$RCLONE_PASS_FILE" ]; then
        ensure_rclone_pass
        chown "$PUID:$PGID" "$RCLONE_PASS_FILE" 2>/dev/null || true
    fi
else
    # 非 root 档：无法 chown，只能明确报错提示（方案 §4）
    need_dir "$DATA_DIR"
    need_dir "$CACHE_DIR"
    if [ "$SEPARATE_PASSFILE" = "1" ]; then
        need_dir "$PASSFILE_ROOT" "<宿主机 keys 目录>"
    fi
    if [ -n "$RCLONE_PASS_FILE" ]; then
        # 非 root 档无法 chown：父目录须由宿主机预先 chown 到运行用户（与 /data 同纪律）
        need_dir "$(dirname "$RCLONE_PASS_FILE")" "<宿主机 rclone 口令目录>"
        ensure_rclone_pass
    fi
    echo "[entrypoint] 非 root 档：uid=$(id -u) gid=$(id -g)，目录可写性检查通过"
fi

# 交由 app.main 启动；HTTPS 由反向代理终结。
exec python -m app.main
