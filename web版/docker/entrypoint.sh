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
else
    # 非 root 档：无法 chown，只能明确报错提示（方案 §4）
    need_dir "$DATA_DIR"
    need_dir "$CACHE_DIR"
    if [ "$SEPARATE_PASSFILE" = "1" ]; then
        need_dir "$PASSFILE_ROOT" "<宿主机 keys 目录>"
    fi
    echo "[entrypoint] 非 root 档：uid=$(id -u) gid=$(id -g)，目录可写性检查通过"
fi

# 交由 app.main 启动；HTTPS 由反向代理终结。
exec python -m app.main
