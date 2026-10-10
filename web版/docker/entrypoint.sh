#!/bin/sh
# 容器入口：首启属主检查（root 档自动 chown，非 root 档报错提示），再 exec 应用进程。
# 属主问题对应方案 §4「bind-mount 属主要求」与风险表 R3：属主错误会导致 SQLite 建库失败、容器起不来。
set -eu

DATA_DIR="${DATA_DIR:-/data}"
CACHE_DIR="${CACHE_DIR:-/cache/restic}"
PORT="${PORT:-8000}"
PUID="${PUID:-0}"
PGID="${PGID:-0}"

need_dir() {
    _d="$1"
    if [ ! -d "$_d" ]; then
        echo "[entrypoint] 错误：目录 $_d 不存在。" >&2
        echo "[entrypoint] 请在 compose 中挂载该目录，或在宿主机先创建后重启容器。" >&2
        exit 1
    fi
    if [ ! -w "$_d" ]; then
        echo "[entrypoint] 错误：目录 $_d 对当前用户 uid=$(id -u) gid=$(id -g) 不可写。" >&2
        echo "[entrypoint] 非 root 档部署请在宿主机执行：" >&2
        echo "[entrypoint]   chown -R $(id -u):$(id -g) <宿主机 data 目录> <宿主机 cache 目录>" >&2
        exit 1
    fi
}

if [ "$(id -u)" = "0" ]; then
    # root 档：自动建目录并 chown 到目标 uid/gid（默认 0:0；非 root 部署可经 PUID/PGID 指定 1000）
    mkdir -p "$DATA_DIR/jobs" "$CACHE_DIR"
    chown -R "$PUID:$PGID" "$DATA_DIR" "$CACHE_DIR"
    chmod 700 "$DATA_DIR"
    echo "[entrypoint] root 档：已将 $DATA_DIR / $CACHE_DIR 属主置为 $PUID:$PGID"
else
    # 非 root 档：无法 chown，只能明确报错提示（方案 §4）
    need_dir "$DATA_DIR"
    need_dir "$CACHE_DIR"
    echo "[entrypoint] 非 root 档：uid=$(id -u) gid=$(id -g)，目录可写性检查通过"
fi

# 交由 app.main 启动：内置"挂证书"式 TLS（config server.tls_cert/tls_key 齐备时）
# 需由应用侧读取配置后以 uvicorn ssl_certfile/ssl_keyfile 启动（Q2 裁决，架构文档 §1.2/§4.3）。
exec python -m app.main
