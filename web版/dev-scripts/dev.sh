#!/usr/bin/env bash
# ===========================================================================
# 一键开发脚本 (restic-web, web 版) - Linux 版（移植自 dev.ps1）
# ===========================================================================
# 用法（bash 中从任意目录调用，任选其一）：
#
#   # 方式一：source（推荐交互使用，函数留在当前会话）
#   source dev.sh              # 初始化环境 + 自动安装依赖（幂等，已装则跳过）
#   source dev.sh --skip-install  # 仅设置环境，跳过依赖安装（秒进环境）
#   source dev.sh --force      # 强制重装后端 + 前端依赖（--force 后跟 backend/frontend 只重装一端）
#   start_dev / stop_dev                 # 启停前后端（后台 + 日志文件）
#   start_backend / stop_backend         # 仅启动/停止后端
#   start_frontend / stop_frontend       # 仅启动/停止前端
#   update_db / reset_db                 # 显式管理开发库
#
#   # 方式二：直接调用（无需 source，适合一次性启停）
#   ./dev.sh                 # 无参数 -> 初始化 + 弹出数字菜单
#   ./dev.sh start           # 等价于「初始化 + start_dev」
#   ./dev.sh stop            # 直接 stop_dev（跳过初始化）
#   ./dev.sh --force start   # 强制重装全部依赖后启动（--force start backend 只重装后端）
#   ./dev.sh restart                  # 重启前后端（跳过依赖安装）
#   ./dev.sh restart backend          # 仅重启后端
#   ./dev.sh restart frontend         # 仅重启前端
#   ./dev.sh stop backend / frontend  # 仅停止指定服务
#
#   数据库管理（本项目为 SQLite，启动时自动按 user_version 应用 app/db/migrations/）：
#     ./dev.sh db        # 手动补跑迁移（一般无需，启动即应用）
#     ./dev.sh resetdb   # 删除开发库文件（下次启动自动重建，需确认）
#
#   依赖管理（前后端分开；前端安装固定走 npmmirror 镜像）：
#     ./dev.sh init                # 幂等同步前后端依赖（已装则跳过）
#     ./dev.sh init backend        # 只同步后端依赖
#     ./dev.sh init frontend       # 只同步前端依赖
#     ./dev.sh init frontend --force  # 只强制重装前端依赖（backend 同理）
#
#   质量工具：
#     ./dev.sh test / lint          # 后端 pytest / ruff
#     ./dev.sh typecheck / vtest    # 前端 vue-tsc / vitest
#     ./dev.sh redlines / audit     # 红线机器校验 / 依赖漏洞扫描
#     ./dev.sh check                # 环境自检（不启动任何服务）
#     ./dev.sh installenv           # 环境自举：安装 uv / Python 3.13 / Node / pnpm（幂等，可选下载源）
#   旧用法兼容：./dev.sh 1-9（1启动 2后端 3前端 4测试 5lint 6typecheck 7vtest 8红线 9扫描）
#
#   菜单编号（无参数运行时出现）：
#     [1]启动前后端  [2]启动后端  [3]启动前端
#     [4]停止前后端  [5]停止后端  [6]停止前端
#     [7]重启前后端  [8]重启后端  [9]重启前端
#     [10]重装依赖(可选目标)  [11]应用迁移  [12]清空开发库
#     [13]后端测试  [14]后端lint  [15]前端类型检查
#     [16]前端测试  [17]红线校验  [18]漏洞扫描
#     [19]安装环境（uv/Python/Node/pnpm，Linux）
#     [0]退出
# ===========================================================================
# 说明：
#   - 后端 = backend/（FastAPI + aiosqlite，uv 管理的 .venv），前端 = frontend/（Vite + Vue3，pnpm）。
#   - 开发库固定在 dev-scripts/.dev-data/restic.db（经 DATA_DIR 注入），该目录为运行时产物，
#     已被仓库根 .gitignore 收口忽略，不入 git；服务日志也写入 .dev-data/logs/。
#   - 首启（或 resetdb 后）需在 Web 界面完成 bootstrap（一次性随机密码），无脚本方式。
#   - Node 探测顺序：DEV_NODE_DIR 环境变量 -> nvm/fnm/volta -> installenv 安装位置
#     (~/.local/dev-node) -> PATH；pnpm 缺失时报错而非回退 npm。
#   - ./dev.sh installenv 可自动安装全套 Linux 工具链（uv/Python 3.13/Node/pnpm，幂等）。
#   - 服务启停：setsid 建独立进程组，停止时 kill 整组（TERM -> 等待 -> KILL），
#     PID 由服务进程自身写入 PID 文件（exec 后 PID 不变），无竞态。
# ===========================================================================

# ---------------------------------------------------------------------------
# 可配置变量（对应 dev.ps1 顶部 $BackendPort/$FrontendPort）
# ---------------------------------------------------------------------------
backend_port=8000
frontend_port=5173
node_version=v22.22.2   # installenv 安装 Node 的版本（与 Windows 侧 workbuddy 一致）

# ---------------------------------------------------------------------------
# 颜色与非交互降级（对应 Write-Host -ForegroundColor）
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_CYAN=$'\033[36m'   C_GREEN=$'\033[32m'  C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'    C_GRAY=$'\033[90m'   C_WHITE=$'\033[37m'
    C_RESET=$'\033[0m'
else
    C_CYAN="" C_GREEN="" C_YELLOW="" C_RED="" C_GRAY="" C_WHITE="" C_RESET=""
fi
msg() { local c=$1; shift; printf '%s%s%s\n' "${!c}" "$*" "${C_RESET}"; }
# 用法: msg cyan "文本" / msg red "文本" ...（颜色名小写映射 C_<name>）

# ---------------------------------------------------------------------------
# 项目根目录推导（对应 $PSScriptRoot 向上找 backend/pyproject.toml）
# ---------------------------------------------------------------------------
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)
root=$script_dir
while [[ $root != / && ! -f $root/backend/pyproject.toml ]]; do
    root=$(dirname -- "$root")
done

backend=$root/backend
frontend=$root/frontend
scripts_dir=$root/scripts
# 双环境共存：backend/.venv 可能是 Windows 布局（Scripts\python.exe，dev.ps1 创建）。
# Linux 侧直接 uv sync 会把它重建为 bin/ 布局，毁掉 Windows 侧环境——
# 检测到 Windows venv 时改用独立 .venv-linux（经 UV_PROJECT_ENVIRONMENT 隔离），两边互不覆盖
if [[ -f $backend/.venv/Scripts/python.exe ]]; then
    venv=$backend/.venv-linux
else
    venv=$backend/.venv
fi
venv_python=$venv/bin/python          # Linux venv 布局：bin/python（Windows 是 Scripts\python.exe）
if [[ $venv == "$backend/.venv-linux" ]]; then
    export UV_PROJECT_ENVIRONMENT=$venv   # 让 uv sync / uv pip install 落到隔离 venv 而非默认 .venv
fi
pyproject=$backend/pyproject.toml
dev_data_dir=$script_dir/.dev-data    # 开发库固定目录（DATA_DIR），随脚本放 dev-scripts 下，不入 git
log_dir=$dev_data_dir/logs
# 运行时产物收口：settings.py 里密码文件根（/data/repos）、restic 缓存（/cache/restic）默认落在系统根，
# 开发模式统一指到 .dev-data 下（与 DATA_DIR 同一处收口，均被仓库根 .gitignore 的 .dev-data/ 规则忽略）
passfile_root=$dev_data_dir/repos             # PASSFILE_ROOT：密码文件 = <根>/<repo_id>/password
cache_root=$dev_data_dir/cache                # CACHE_DIR：restic 缓存
rclone_dir=$dev_data_dir/rclone
rclone_conf=$rclone_dir/rclone.conf           # RCLONE_CONFIG：显式指定，不随 HOME 漂移
backend_pid_file=$script_dir/.dev-backend.linux.pid    # 加 .linux 后缀：与 Windows 版 dev.ps1 的 pid 文件隔离，避免跨环境 pid 误杀
frontend_pid_file=$script_dir/.dev-frontend.linux.pid
backend_log=$log_dir/backend.log
frontend_log=$log_dir/frontend.log

script_name=${BASH_SOURCE[0]##*/}

# ---------------------------------------------------------------------------
# 工具探测（对应 Find-Python / Find-Uv / Find-Node / Find-Pnpm）
# ---------------------------------------------------------------------------
find_python() {
    # 要求 >=3.13；Linux 无 py 启动器，探测 python3.13/python3/python
    local c p v
    for c in python3.13 python3 python; do
        p=$(command -v "$c" 2>/dev/null) || continue
        v=$("$p" -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>/dev/null) || continue
        # 版本比较：sort -V 后 3.13 排在 v 前 = v >= 3.13
        if [[ $(printf '%s\n3.13\n' "$v" | sort -V | head -n1) == "3.13" ]]; then
            printf '%s\n' "$p"; return 0
        fi
    done
    return 1
}

find_uv() {
    if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
    # Linux 上 uv 官方安装脚本默认 ~/.local/bin，cargo 用户在 ~/.cargo/bin
    local c
    for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        [[ -x $c ]] && { printf '%s\n' "$c"; return 0; }
    done
    return 1
}

find_node() {
    # 1) 用户显式指定（对应 Windows 版的 workbuddy 固定目录）
    if [[ -n ${DEV_NODE_DIR:-} && -x $DEV_NODE_DIR/bin/node ]]; then
        printf '%s\n' "$DEV_NODE_DIR"; return 0
    fi
    # 2) nvm（取已安装的最高版本）
    local d
    if compgen -G "$HOME/.nvm/versions/node/*/bin/node" >/dev/null; then
        d=$(printf '%s\n' "$HOME"/.nvm/versions/node/*/bin | sort -V | tail -n1)
        printf '%s\n' "${d%/bin}"; return 0
    fi
    # 3) fnm / volta 默认别名 / installenv 安装位置
    for d in "$HOME/.fnm/aliases/default" "$HOME/.volta" "$HOME/.local/dev-node"; do
        [[ -x $d/bin/node ]] && { printf '%s\n' "$d"; return 0; }
    done
    # 4) PATH
    local n
    if n=$(command -v node 2>/dev/null); then
        dirname -- "$n"; return 0
    fi
    return 1
}

find_pnpm() {
    # 与探测到的 node 同目录优先（对应 workbuddy 全局 pnpm），缺失时报错而非回退 npm
    local d
    if [[ -n $node_path ]]; then
        [[ -x $node_path/pnpm ]] && { printf '%s\n' "$node_path/pnpm"; return 0; }
    fi
    if d=$(command -v pnpm 2>/dev/null) && [[ $d != /mnt/* ]]; then printf '%s\n' "$d"; return 0; fi
    # corepack shim 与 pnpm 官方独立安装位置；command -v 命中的 /mnt/* 是 WSL
    # interop 继承的 Windows 侧工具（Linux 进程不可用），必须排除
    for d in \
        "${node_path:+$node_path/node_modules/corepack/shims/pnpm}" \
        "$HOME/.local/bin/pnpm" \
        "$HOME/.local/share/pnpm/pnpm"; do
        [[ -n $d && -x $d ]] && { printf '%s\n' "$d"; return 0; }
    done
    return 1
}

node_path=$(find_node 2>/dev/null || true)
if [[ -z $node_path ]]; then
    msg yellow " 未检测到 Node。前端将无法启动。"
    if grep -qi microsoft /proc/version 2>/dev/null; then
        msg yellow "   （WSL 内不会使用 Windows 侧的 Node——路径体系不兼容；可运行 ./dev.sh installenv 安装）"
    fi
fi
pnpm_path=$(find_pnpm 2>/dev/null || true)
if [[ -z $pnpm_path ]]; then
    msg yellow " 未检测到 pnpm。前端将无法正确安装依赖（请勿回退 npm）。"
fi
uv_exe=$(find_uv 2>/dev/null || true)
# Linux 上红线校验直接用当前 bash，无需（也不存在）Git Bash 定位

if [[ ! -d $backend ]]; then
    msg red " 未找到 backend/ 目录。"
    # source 模式温和返回，直接执行退出
    if [[ ${BASH_SOURCE[0]} != "$0" ]]; then return 0; else exit 1; fi
fi

# ---------------------------------------------------------------------------
# PATH 辅助与环境设置（对应 Set-LightEnv / add_path）
# ---------------------------------------------------------------------------
add_path() {
    local d=$1
    [[ -d $d ]] || return 0
    case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
}

set_light_env() {
    [[ -x $venv_python ]] && add_path "$(dirname -- "$venv_python")"
    [[ -n $node_path ]] && { [[ -d $node_path/bin ]] && add_path "$node_path/bin" || add_path "$node_path"; }
    [[ -n $pnpm_path ]] && add_path "$(dirname -- "$pnpm_path")"
    export PATH
    unset NODE_OPTIONS
}

# 返回当前 pnpm 命令（验证可执行；不可用输出空，调用方报错而非回退 npm）
get_pnpm_cmd() {
    if [[ -n $pnpm_path && -x $pnpm_path ]] && "$pnpm_path" --version >/dev/null 2>&1; then
        printf '%s\n' "$pnpm_path"; return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# uv / venv 准备（幂等；对应 Ensure-Uv / Ensure-Venv）
# ---------------------------------------------------------------------------
ensure_uv() {
    if [[ -n $uv_exe && -x $uv_exe ]] || command -v uv >/dev/null 2>&1; then return 0; fi
    msg cyan "-> 未检测到 uv，尝试安装到当前 Python..."
    local py
    if [[ -x $venv_python ]]; then py=$venv_python; else py=$(find_python 2>/dev/null || true); fi
    if [[ -z $py ]]; then
        msg red "  无可用 Python，无法安装 uv。"
        # WSL 场景引导：Windows 侧环境对 Linux 进程不可用（路径体系不兼容），需装 Linux 版依赖
        if grep -qi microsoft /proc/version 2>/dev/null; then
            msg yellow "  当前在 WSL 内：Windows 侧的 uv/Python/Node 无法被 Linux 进程使用。"
            msg yellow "  可运行 ./dev.sh installenv 自动安装 Linux 工具链；或手动装 uv 后重跑："
            msg yellow "    curl -LsSf https://astral.sh/uv/install.sh | sh"
            msg yellow "  （仅做 Windows 本机开发时，直接用 dev.ps1 即可。）"
        fi
        return 1
    fi
    "$py" -m pip install -q uv
    local cand
    cand=$(dirname -- "$py")/uv
    if [[ -x $cand ]]; then uv_exe=$cand; return 0; fi
    command -v uv >/dev/null 2>&1 && return 0
    msg red "  uv 安装失败。"
    return 1
}

uv_cmd() { command -v uv >/dev/null 2>&1 && { command -v uv; return; }; printf '%s\n' "$uv_exe"; }

ensure_venv() {
    [[ -x $venv_python ]] && return 0
    msg cyan "-> 后端 venv 不存在（$venv），尝试用 uv 构建（uv sync --extra dev）..."
    ensure_uv || return 1
    export UV_DEFAULT_INDEX="https://mirrors.cloud.tencent.com/pypi/simple"
    local uv; uv=$(uv_cmd)
    (cd "$backend" && "$uv" sync --extra dev) || { msg red "  uv sync 失败。"; return 1; }
    [[ -x $venv_python ]]
}

# ---------------------------------------------------------------------------
# 环境自举（installenv）：安装 Linux 工具链 uv / Python 3.13 / Node / pnpm（幂等）
# 说明：Windows 侧环境对 Linux 进程不可用（路径体系不兼容），WSL/纯 Linux 需自备
#       Linux 版工具链；本函数提供一键自举。项目本身要求 Python >= 3.13，
#       apt 源普遍无此版本，故 Python 统一由 uv 托管安装（uv python install）。
# 下载源：交互选择或 DEV_DL_MODE 环境变量指定（auto=官方优先镜像降级 /
#         mirror=国内镜像优先 / official=仅官方源），套用到所有下载步骤。
# ---------------------------------------------------------------------------

# 下载源模式（默认 auto）；镜像：npmmirror（Node/Python）+ 腾讯 pypi（uv/pnpm 包）
dl_mode=auto

choose_dl_mode() {
    # 脚本化场景：DEV_DL_MODE 环境变量直接指定，不再询问
    if [[ -n ${DEV_DL_MODE:-} ]]; then
        case ${DEV_DL_MODE,,} in
            auto | mirror | official) dl_mode=${DEV_DL_MODE,,}; return 0 ;;
        esac
    fi
    if [[ ! -t 0 ]]; then dl_mode=auto; return 0; fi   # 非交互降级
    msg white "  下载源选择：[1] 国内镜像优先（推荐国内网络）  [2] 仅官方源  [3] 自动（官方优先，失败降级镜像）"
    local c
    if read -r -p "请选择 [3]: " c; then
        case $c in 1) dl_mode=mirror ;; 2) dl_mode=official ;; *) dl_mode=auto ;; esac
    else
        dl_mode=auto
    fi
    msg gray "  -> 下载源模式：$dl_mode"
}

# apt 基础包安装：root 直接装，非 root 借 sudo；非 apt 系发行版给出手动提示
apt_install() {
    if ! command -v apt-get >/dev/null 2>&1; then
        msg yellow "  非 apt 系发行版：请手动安装后重跑：$*"
        return 1
    fi
    local s=()
    (( EUID != 0 )) && s=(sudo)
    "${s[@]}" apt-get update -qq
    DEBIAN_FRONTEND=noninteractive "${s[@]}" apt-get install -y -qq "$@"
}

install_uv() {
    if command -v uv >/dev/null 2>&1 || [[ -x $HOME/.local/bin/uv ]]; then
        msg gray "  uv 已安装，跳过"
        return 0
    fi
    msg cyan "-> 安装 uv（模式：$dl_mode）..."
    # uv 官方脚本：脚本走 astral.sh、二进制走 GitHub Releases；pip 降级走腾讯镜像
    try_uv_official() {
        command -v curl >/dev/null 2>&1 \
            && curl -fsSL --max-time 90 https://astral.sh/uv/install.sh 2>/dev/null | sh \
            && [[ -x $HOME/.local/bin/uv ]]
    }
    try_uv_pip() {
        command -v python3 >/dev/null 2>&1 || return 1
        python3 -m pip install -q -i https://mirrors.cloud.tencent.com/pypi/simple uv
        command -v uv >/dev/null 2>&1 || [[ -x $HOME/.local/bin/uv ]]
    }
    case $dl_mode in
        mirror)
            # 镜像优先：有 python3 直接 pip 镜像；否则退官方脚本（uv 无官方镜像站）
            if try_uv_pip; then return 0; fi
            msg yellow "  pip 镜像不可用（缺 python3 或失败），退官方脚本..."
            try_uv_official ;;
        official)
            try_uv_official ;;
        *)
            if try_uv_official; then return 0; fi
            msg yellow "  官方脚本失败，降级 pip 腾讯镜像..."
            try_uv_pip ;;
    esac || { msg red "  uv 安装失败。"; return 1; }
}

install_python() {
    local uv; uv=$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")
    if "$uv" python find 3.13 >/dev/null 2>&1; then
        msg gray "  Python 3.13（uv 托管）已安装，跳过"
        return 0
    fi
    msg cyan "-> 由 uv 安装 Python 3.13（约 30MB，模式：$dl_mode）..."
    # mirror 模式：uv 下载源走 npmmirror 的 python-build-standalone 镜像
    if [[ $dl_mode == mirror ]]; then
        export UV_PYTHON_INSTALL_MIRROR="https://registry.npmmirror.com/-/binary/python-build-standalone"
    fi
    "$uv" python install 3.13
}

install_node() {
    if command -v node >/dev/null 2>&1; then
        msg gray "  PATH 中已有 node ($(node -v))，跳过安装"
        return 0
    fi
    local dest=$HOME/.local/dev-node
    if [[ -x $dest/bin/node ]]; then
        msg gray "  Node 已安装（$dest），跳过"
        return 0
    fi
    local arch
    case $(uname -m) in
        x86_64)  arch=x64 ;;
        aarch64) arch=arm64 ;;
        *) msg red "  不支持的架构：$(uname -m)（仅支持 x64/arm64）。"; return 1 ;;
    esac
    local pkg="node-$node_version-linux-$arch"
    msg cyan "-> 安装 Node $node_version 到 $dest（模式：$dl_mode）..."
    mkdir -p "$dest" /tmp/devsh-node || return 1
    # 按模式排源：official 仅官方 / mirror 仅 npmmirror / auto 官方优先镜像降级
    local urls=()
    case $dl_mode in
        official) urls=("https://nodejs.org/dist/$node_version/$pkg.tar.xz") ;;
        mirror)   urls=("https://registry.npmmirror.com/-/binary/node/$node_version/$pkg.tar.xz") ;;
        *)        urls=("https://nodejs.org/dist/$node_version/$pkg.tar.xz" \
                        "https://registry.npmmirror.com/-/binary/node/$node_version/$pkg.tar.xz") ;;
    esac
    local ok=0 u
    for u in "${urls[@]}"; do
        if curl -fSL --retry 2 --connect-timeout 15 -o "/tmp/devsh-node/$pkg.tar.xz" "$u"; then ok=1; break; fi
    done
    (( ok )) || { msg red "  Node 下载失败（官方源与镜像均不可达）。"; rm -rf /tmp/devsh-node; return 1; }
    tar -xJf "/tmp/devsh-node/$pkg.tar.xz" -C /tmp/devsh-node || { rm -rf /tmp/devsh-node; return 1; }
    rm -rf "$dest"
    mv "/tmp/devsh-node/$pkg" "$dest"
    rm -rf /tmp/devsh-node
    msg gray "  已安装到 $dest（dev.sh 自动探测该位置）"
}

install_pnpm() {
    # 已装判定排除 WSL interop 继承的 Windows 侧路径（/mnt/*）：那是 Windows 工具链，
    # Linux 进程不可用，不能当作"已安装"跳过
    local found; found=$(command -v pnpm 2>/dev/null || true)
    if { [[ -n $found && $found != /mnt/* ]] || [[ -x $HOME/.local/bin/pnpm ]]; }; then
        msg gray "  pnpm 已安装，跳过"
        return 0
    fi
    local node_dir; node_dir=$(find_node 2>/dev/null) || { msg red "  未找到 node，无法安装 pnpm（先完成 Node 安装）。"; return 1; }
    local corepack_exe
    if [[ -x $node_dir/bin/corepack ]]; then corepack_exe=$node_dir/bin/corepack
    else corepack_exe=$(command -v corepack 2>/dev/null) || { msg red "  未找到 corepack。"; return 1; }; fi
    msg cyan "-> 经 corepack 启用 pnpm（shim 装到 ~/.local/bin，模式：$dl_mode）..."
    # official 用 npm 官方 registry；mirror/auto 用 npmmirror
    local reg=https://registry.npmmirror.com
    [[ $dl_mode == official ]] && reg=https://registry.npmjs.org
    mkdir -p "$HOME/.local/bin"
    # corepack 本身是 node 脚本：临时把 node bin 加进 PATH（installenv 路径不走 set_light_env）
    PATH="$node_dir/bin:$PATH" COREPACK_ENABLE_DOWNLOAD_PROMPT=0 COREPACK_NPM_REGISTRY="$reg" \
        "$corepack_exe" enable --install-directory "$HOME/.local/bin" pnpm
}

install_env() {
    msg cyan "======== 安装 Linux 开发环境（幂等，已装项自动跳过） ========"
    choose_dl_mode
    if ! command -v curl >/dev/null 2>&1 || ! command -v xz >/dev/null 2>&1; then
        msg cyan "-> 安装基础工具（curl xz-utils ca-certificates）..."
        apt_install curl xz-utils ca-certificates || msg yellow "  基础工具安装失败，后续步骤可能不可用。"
    fi
    install_uv     || return 1
    install_python || return 1
    install_node   || return 1
    install_pnpm   || return 1
    # 刷新顶层探测结果，当前会话立即可用
    uv_exe=$(find_uv 2>/dev/null || true)
    node_path=$(find_node 2>/dev/null || true)
    pnpm_path=$(find_pnpm 2>/dev/null || true)
    msg green "-> Linux 环境就绪。重新运行 ./dev.sh 即可正常初始化。"
}

# ---------------------------------------------------------------------------
# 依赖安装（幂等；scope 拆分前后端；对应 Init-Dev）
# ---------------------------------------------------------------------------
# 用法: init_dev [scope] [reinstall]
#   scope:     all|''=前后端 / backend / frontend（限定只处理一端）
#   reinstall: force=强制重装（后端 --reinstall；前端忽略已有 node_modules）
init_dev() {
    local scope=${1:-all} reinstall=${2:-}
    local do_backend=0 do_frontend=0
    case $scope in
        backend)  do_backend=1 ;;
        frontend) do_frontend=1 ;;
        *)        do_backend=1; do_frontend=1 ;;
    esac

    if (( do_backend )); then
        if ! ensure_venv; then return 1; fi

        msg cyan "-> 同步后端依赖 (uv sync --extra dev)"
        export UV_DEFAULT_INDEX="https://mirrors.cloud.tencent.com/pypi/simple"
        local uv; uv=$(uv_cmd)
        local sync_args=(sync --extra dev)
        [[ $reinstall == force ]] && sync_args+=(--reinstall)
        (cd "$backend" && "$uv" "${sync_args[@]}") \
            || msg red "  uv sync 失败。"
        msg green "-> 后端依赖同步完成"
    fi

    if (( do_frontend )); then
        # 前端依赖（必须用 pnpm，避免 npm 覆写 pnpm 布局与 .npmrc 专属配置）
        local pkg=$frontend/package.json
        if [[ -f $pkg ]]; then
            # WSL 双环境提示：node_modules 是平台相关布局（esbuild 等 native 二进制按平台安装），
            # Windows 侧装的在 Linux 下跑不了 vite；重装会覆盖为 Linux 布局，Windows 侧需重装恢复
            if grep -qi microsoft /proc/version 2>/dev/null \
                && [[ -d $frontend/node_modules ]] \
                && compgen -G "$frontend/node_modules/@esbuild/win32-x64*" >/dev/null; then
                msg yellow "  检测到 Windows 布局的 node_modules（WSL 内前端不可用）。"
                msg yellow "  WSL 内跑前端需 ./dev.sh init frontend --force 重装（覆盖为 Linux 布局，Windows 侧需再重装恢复）；仅跑后端不受影响。"
            fi
            if [[ $reinstall != force && -d $frontend/node_modules ]]; then
                msg gray "-> 前端 node_modules 已存在，跳过安装"
            else
                # 强制重装：先清掉旧 node_modules——半成品残留（如上次安装中断留下的
                # @esbuild/linux-x64 空壳）会让 pnpm 重建时报 EEXIST 直接失败
                if [[ $reinstall == force && -d $frontend/node_modules ]]; then
                    msg cyan "-> 清理旧 node_modules（强制重装）"
                    rm -rf "$frontend/node_modules"
                fi
                local pm
                if ! pm=$(get_pnpm_cmd); then
                    msg red "  未找到 pnpm。前端安装需要 pnpm（勿用 npm，会破坏布局与 .npmrc 配置）。"
                    return 1
                fi
                msg cyan "-> 安装前端依赖 (frontend) via pnpm（registry=npmmirror）"
                # 固定国内镜像：npm_config_registry 用前缀赋值只作用于本命令，不污染会话
                (cd "$frontend" && npm_config_registry=https://registry.npmmirror.com "$pm" install) \
                    || msg red "  pnpm install 失败。"
            fi
        else
            msg gray "-> frontend/ 尚未初始化（无 package.json），跳过前端依赖"
        fi
    fi
}

# ===========================================================================
# 启动 / 停止单个服务（独立进程组 + PID 文件 + 日志文件）
# ===========================================================================

# 用法: start_service <pid_file> <log_file> <label> <工作目录> <命令...>
# setsid 建独立进程组（pgid=pid）；目标进程自身把 $$ 写入 pid_file（exec 不变 pid，无竞态）
start_service() {
    local pid_file=$1 log_file=$2 label=$3 workdir=$4
    shift 4
    mkdir -p "$(dirname -- "$log_file")"
    if [[ -f $log_file ]]; then mv -f "$log_file" "$log_file.old" 2>/dev/null; fi
    setsid bash -c '
        printf "%s\n" "$$" > "$1"; shift
        cd "$1" || exit 1; shift
        export NODE_OPTIONS=
        exec "$@"
    ' _ "$pid_file" "$workdir" "$@" > "$log_file" 2>&1 &
    local pid=$!
    sleep 0.6
    if ! kill -0 "$pid" 2>/dev/null; then
        msg red "  $label 启动失败，日志尾部："
        tail -n 5 "$log_file" | sed 's/^/    /'
        return 1
    fi
    msg green "-> $label 已启动（PID=$pid，日志 $log_file）"
    return 0
}

# 用法: stop_service <pid_file> <label> —— kill 整个进程组（TERM -> 等待 -> KILL）
stop_service() {
    local pid_file=$1 label=$2
    if [[ ! -f $pid_file ]]; then
        msg yellow "  $label 未在运行。"; return 0
    fi
    local pid
    pid=$(head -n1 "$pid_file" | tr -d '[:space:]')
    if [[ ! $pid =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        msg yellow "  $label 未在运行（已清理残留 pid 文件）。"; return 0
    fi
    msg cyan "-> 停止 $label ..."
    # setsid 保证进程组 id = pid，整组一次杀干净（uvicorn reload 子进程 / vite 子进程）
    kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    local i
    for i in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL -- -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    fi
    rm -f "$pid_file"
    msg green "-> $label 已停止。"
}

# ===========================================================================
# 启动函数
# ===========================================================================

start_backend() {
    if ! ensure_venv; then return 1; fi
    # --loop none（2026-10-19）：uvicorn >=0.54 的 --reload 在 Windows 上会强制 SelectorEventLoop
    # 导致 create_subprocess_exec 全部失败；Linux 下系统默认循环无此问题，参数保留以两端行为一致。
    # 预建收口目录（幂等）
    mkdir -p "$dev_data_dir" "$passfile_root" "$cache_root" "$rclone_dir"
    if start_service "$backend_pid_file" "$backend_log" "后端" "$backend" \
        env DATA_DIR="$dev_data_dir" PASSFILE_ROOT="$passfile_root" CACHE_DIR="$cache_root" RCLONE_CONFIG="$rclone_conf" \
        "$venv_python" -m uvicorn app.main:app --reload --loop none --port "$backend_port"; then
        msg yellow "  后端 API     : http://127.0.0.1:$backend_port  文档 /docs"
        msg yellow "  首启提示     : 需在 Web 界面完成 bootstrap（一次性随机密码）"
        msg yellow "  实时日志     : tail -f $backend_log"
    fi
}

start_frontend() {
    local pm
    if ! pm=$(get_pnpm_cmd); then
        msg red "  未找到 pnpm，前端无法启动（勿回退 npm）。"; return 1
    fi
    if start_service "$frontend_pid_file" "$frontend_log" "前端" "$frontend" \
        env PATH="$(dirname -- "$pm"):${node_path:+$node_path:}$PATH" "$pm" dev; then
        msg yellow "  前端页面     : http://localhost:$frontend_port"
        msg yellow "  实时日志     : tail -f $frontend_log"
    fi
}

start_dev() {
    msg cyan "-> 启动开发服务（前后端后台运行，日志见 $log_dir）"
    start_backend
    start_frontend
    echo
    msg cyan "========================================"
    msg green "  开发服务已启动 (restic-web)"
    msg cyan "========================================"
    msg yellow "  后端 API     : http://127.0.0.1:$backend_port  文档 /docs"
    msg yellow "  前端页面     : http://localhost:$frontend_port"
    msg gray "  API 代理     : 前端 /api 已通过 Vite 代理转发到 http://127.0.0.1:$backend_port"
    msg gray "  开发库       : $dev_data_dir/restic.db（启动自动应用迁移）"
    msg gray "  后端日志     : $backend_log"
    msg gray "  前端日志     : $frontend_log"
    msg yellow "  停止命令     : stop_dev（或 ./$script_name stop）"
    msg yellow "  单服务重启   : ./$script_name restart backend / restart frontend"
    msg cyan "========================================"
}

# ===========================================================================
# 停止函数
# ===========================================================================

stop_backend()  { stop_service "$backend_pid_file"  "后端"; }
stop_frontend() { stop_service "$frontend_pid_file" "前端"; }
stop_dev() {
    stop_backend
    stop_frontend
}

# ===========================================================================
# 数据库管理函数
# ===========================================================================

update_db() {
    if ! ensure_venv; then return 1; fi
    msg cyan "-> 手动应用开发库迁移（init_database -> migrate，按 user_version 增量）"
    # 迁移逻辑复用应用自身：启动时会自动执行，这里仅提供显式补跑入口
    local rc=0
    env DATA_DIR="$dev_data_dir" PASSFILE_ROOT="$passfile_root" CACHE_DIR="$cache_root" RCLONE_CONFIG="$rclone_conf" \
        "$venv_python" - "$backend" <<'PYEOF' || rc=$?
import asyncio
import sys
sys.path.insert(0, sys.argv[1])
from app.settings import get_settings
from app.db.database import init_database

async def main():
    s = get_settings()
    await init_database(s.db_path)
    print("OK migrations applied ->", s.db_path)

asyncio.run(main())
PYEOF
    if (( rc != 0 )); then
        msg red "  迁移执行失败。"; return 1
    fi
    msg green "-> 开发库表结构已更新到最新迁移。"
}

reset_db() {
    local db_file=$dev_data_dir/restic.db
    if [[ ! -f $db_file ]]; then
        msg yellow "  开发库不存在：$db_file（无需清空）。"; return 0
    fi
    msg red "!! 即将删除开发库 '$db_file'（含 -wal/-shm），此操作不可恢复！"
    local ans
    if ! read -r -p "确认清空？输入 y 继续 (y/N): " ans; then
        msg gray "-> 非交互环境，已取消清空。"; return 0
    fi
    if [[ ${ans,,} != y ]]; then
        msg yellow "-> 已取消清空。"; return 0
    fi
    local f
    for f in "$db_file" "$db_file-wal" "$db_file-shm"; do
        if [[ -f $f ]]; then
            rm -f "$f"
            msg gray "  -> 已删除 $f"
        fi
    done
    msg green "-> 开发库已清空。下次启动后端时自动重建全部表并重新 bootstrap。"
}

# ===========================================================================
# 质量工具（本仓库特有，沿用既有实测实现）
# ===========================================================================

run_backend_test() {
    if ! ensure_venv; then return 1; fi
    msg cyan "-> 后端全量测试（pytest）..."
    (cd "$backend" && "$venv_python" -m pytest -q)
}

run_backend_lint() {
    if ! ensure_venv; then return 1; fi
    msg cyan "-> 后端 lint（ruff）..."
    (cd "$backend" && "$venv_python" -m ruff check app tests)
}

run_frontend_typecheck() {
    local pm
    if ! pm=$(get_pnpm_cmd); then msg red "  未找到 pnpm。"; return 1; fi
    msg cyan "-> 前端类型检查（vue-tsc）..."
    (cd "$frontend" && "$pm" run typecheck)
}

run_frontend_test() {
    local pm
    if ! pm=$(get_pnpm_cmd); then msg red "  未找到 pnpm。"; return 1; fi
    msg cyan "-> 前端测试（vitest run）..."
    (cd "$frontend" && "$pm" run test)
}

run_redlines() {
    # Linux 原生 bash 直接执行（对应 Windows 版的 Git Bash 定位与 MSYS 路径转换）
    local sh=$scripts_dir/verify_redlines.sh
    if [[ ! -f $sh ]]; then
        msg red "  未找到红线校验脚本: $sh"; return 1
    fi
    msg cyan "-> 红线机器校验（verify_redlines.sh）..."
    bash "$sh"
}

run_audit() {
    if ! ensure_venv; then return 1; fi
    if ! ensure_uv; then return 1; fi
    local uv; uv=$(uv_cmd)
    # pip-audit 是临时工具，不在 uv.lock 内；uv sync 会清掉它，故每次先确保存在
    if ! "$venv_python" -c "import pip_audit" >/dev/null 2>&1; then
        msg cyan "-> pip-audit 未安装，临时安装（不入锁）..."
        export UV_DEFAULT_INDEX="https://mirrors.cloud.tencent.com/pypi/simple"
        "$uv" pip install --python "$venv_python" pip-audit -q || return 1
    fi
    msg cyan "-> 依赖漏洞扫描（pip-audit + OSV；pypi.org 直连不可达，必须用 OSV）..."
    local tmp_req
    tmp_req=$(mktemp /tmp/restic-web-audit-req.XXXXXX.txt)
    export UV_DEFAULT_INDEX="https://mirrors.cloud.tencent.com/pypi/simple"
    "$uv" export --no-dev --no-hashes --format requirements-txt --output-file "$tmp_req" --project "$backend"
    "$venv_python" -m pip_audit -r "$tmp_req" -s osv --progress-spinner off --timeout 60
    local rc=$?
    rm -f "$tmp_req"
    if [[ $rc -eq 0 ]]; then msg green "-> 无已知漏洞"; else
        msg red "-> 存在漏洞，见上表明细（HIGH/CRITICAL = Blocker）"
    fi
    return $rc
}

# ===========================================================================
# 环境信息与自检
# ===========================================================================

show_env_info() {
    echo
    msg cyan "========================================"
    msg green "  开发环境已初始化 (restic-web)"
    msg cyan "========================================"
    if [[ -x $venv_python ]]; then
        echo "  Python     : $("$venv_python" -V 2>&1)"
    else
        echo "  Python     : venv 缺失（init 时由 uv 构建）"
    fi
    echo "  venv       : $venv"
    echo "  uv         : ${uv_exe:-未找到（仅 venv 缺失时需要）}"
    if [[ -n $node_path ]]; then
        echo "  Node       : $(node -v 2>/dev/null)  @ $node_path"
    else
        echo "  Node       : 不可用"
    fi
    echo "  pnpm       : $("$pnpm_path" --version 2>/dev/null || echo '不可用（前端需 pnpm，勿用 npm）')"
    echo "  BackendPort: $backend_port"
    echo "  FrontendPort: $frontend_port"
    echo "  开发库     : $dev_data_dir/restic.db"
    echo "  目录       : $root"
    echo "  前端目录   : $frontend"
    msg cyan "========================================"
    echo
    msg yellow "常用命令："
    echo "  source ./$script_name                 # 初始化环境并安装依赖"
    echo "  source ./$script_name --skip-install  # 仅设置环境，跳过依赖安装"
    echo "  source ./$script_name --force         # 强制重装依赖（--force backend/frontend 只重装一端）"
    echo "  ./$script_name init backend           # 只同步/重装指定端依赖（init frontend 同理）"
    echo "  ./$script_name start                  # 直接一键启动"
    echo "  ./$script_name stop                   # 直接停止"
    echo "  ./$script_name restart                # 重启（跳过依赖安装）"
    echo "  ./$script_name restart backend/frontend  # 仅重启指定服务"
    echo "  ./$script_name stop backend/frontend     # 仅停止指定服务"
    echo "  ./$script_name db                     # 手动补跑开发库迁移"
    echo "  ./$script_name resetdb                # 清空开发库（危险，启动自动重建）"
    echo "  ./$script_name test/lint              # 后端 pytest / ruff"
    echo "  ./$script_name typecheck/vtest        # 前端 vue-tsc / vitest"
    echo "  ./$script_name redlines/audit         # 红线校验 / 漏洞扫描"
    echo "  ./$script_name check                  # 环境自检"
    echo
}

invoke_check() {
    msg cyan "=== 环境自检 ==="
    echo "  项目根        : $root"
    if [[ -x $venv_python ]]; then echo "  backend/.venv : 有 ($venv_python)"; else echo "  backend/.venv : 无"; fi
    echo "  uv            : ${uv_exe:-未找到（仅 venv 缺失时需要）}"
    if [[ -d $frontend/node_modules ]]; then echo "  node_modules  : 有"; else echo "  node_modules  : 无（首次跑前端需 pnpm install）"; fi
    echo "  node          : ${node_path:-未找到}"
    echo "  pnpm          : ${pnpm_path:-未找到}"
    if [[ -f $scripts_dir/verify_redlines.sh ]]; then echo "  verify_redlines: 有"; else echo "  verify_redlines: 缺失"; fi
    if [[ -f $dev_data_dir/restic.db ]]; then
        echo "  开发库        : $dev_data_dir (已存在)"
    else
        echo "  开发库        : $dev_data_dir (未创建，首启自动生成)"
    fi
    echo "  密码文件根    : $passfile_root（PASSFILE_ROOT）"
    echo "  restic 缓存   : $cache_root（CACHE_DIR）"
    echo "  rclone 配置   : $rclone_conf（RCLONE_CONFIG）"
    if [[ -f $backend_pid_file ]]; then
        echo "  后端 PID 文件 : $(head -n1 "$backend_pid_file")（日志 $backend_log）"
    else
        echo "  后端 PID 文件 : 无（未运行）"
    fi
    if [[ -f $frontend_pid_file ]]; then
        echo "  前端 PID 文件 : $(head -n1 "$frontend_pid_file")（日志 $frontend_log）"
    else
        echo "  前端 PID 文件 : 无（未运行）"
    fi
    if [[ -x $venv_python ]]; then
        echo "  关键依赖      : $("$venv_python" -c "import fastapi, starlette, uvicorn; print('fastapi', fastapi.__version__, '| starlette', starlette.__version__, '| uvicorn', uvicorn.__version__)" 2>/dev/null || echo '导入失败')"
    fi
}

# ===========================================================================
# 交互式菜单
# ===========================================================================

show_dev_menu() {
    # 非交互终端（输入被重定向）直接退出，对应 [Console]::IsInputRedirected
    if [[ ! -t 0 ]]; then return 0; fi
    local choice
    while true; do
        echo
        msg cyan "========================================"
        msg green "  开发菜单 (restic-web)"
        msg cyan "========================================"
        msg white "  [1] 启动前后端          [4] 停止前后端          [7] 重启前后端"
        msg white "  [2] 仅启动后端          [5] 仅停止后端          [8] 仅重启后端"
        msg white "  [3] 仅启动前端          [6] 仅停止前端          [9] 仅重启前端"
        msg yellow "  [10] 重装依赖(可选目标)  [11] 应用迁移           [12] 清空开发库"
        msg yellow "  [13] 后端测试           [14] 后端 lint          [15] 前端类型检查"
        msg yellow "  [16] 前端测试           [17] 红线校验           [18] 漏洞扫描"
        msg yellow "  [19] 安装环境（uv/Python/Node/pnpm）"
        msg gray "  [0] 退出菜单"
        msg cyan "========================================"
        if ! read -r -p "请输入编号: " choice; then
            msg gray "-> 当前主机非交互，已退出菜单。"; return 0
        fi
        choice=${choice,,}
        case $choice in
            1)  start_dev ;;
            2)  start_backend ;;
            3)  start_frontend ;;
            4)  stop_dev ;;
            5)  stop_backend ;;
            6)  stop_frontend ;;
            7)  stop_dev; start_dev ;;
            8)  stop_backend; start_backend ;;
            9)  stop_frontend; start_frontend ;;
            10)
                # 重装目标子选择（交互样式与下载源选择一致，默认全部；read 失败时也按全部处理）
                msg white "  重装目标选择：[1] 全部（后端 + 前端）  [2] 仅后端  [3] 仅前端"
                local c10
                if read -r -p "请选择 [1]: " c10; then
                    case $c10 in
                        2) init_dev backend force ;;
                        3) init_dev frontend force ;;
                        *) init_dev all force ;;
                    esac
                else
                    init_dev all force
                fi ;;
            11) update_db ;;
            12) reset_db ;;
            13) run_backend_test ;;
            14) run_backend_lint ;;
            15) run_frontend_typecheck ;;
            16) run_frontend_test ;;
            17) run_redlines ;;
            18) run_audit ;;
            19) install_env ;;
            0)  msg gray "-> 已退出菜单。"; return 0 ;;
            "") return 0 ;;
            *)  msg red "  无效输入：$choice（请输入 0-19）" ;;
        esac
    done
}

# ===========================================================================
# 顶层控制流（对照 PowerShell 版：stop 最快 / restart 轻环境 / 其余完整初始化）
# ===========================================================================
# 参数解析（对应 param：-SkipInstall/-Force/-Check + 动作/服务位置参数）
skip_install=0
force=0
check_mode=0
action=""
service=""

while (( $# )); do
    case $1 in
        -s|--skip-install) skip_install=1 ;;
        -f|--force)        force=1 ;;
        -c|--check)        check_mode=1 ;;
        -h|--help)         sed -n '2,45p' "${BASH_SOURCE[0]}"; [[ ${BASH_SOURCE[0]} != "$0" ]] && return 0 || exit 0 ;;
        --) shift; break ;;
        *) if [[ -z $action ]]; then action=$1; elif [[ -z $service ]]; then service=$1; fi ;;
    esac
    shift
done

# 旧数字用法兼容（1-9），语义词直接透传（对应 -Run）
if [[ -n $action && $action =~ ^[1-9]$ && -z $service ]]; then
    case $action in
        1) action=start ;;
        2) action=start; service=backend ;;
        3) action=start; service=frontend ;;
        4) action=test ;;
        5) action=lint ;;
        6) action=typecheck ;;
        7) action=vtest ;;
        8) action=redlines ;;
        9) action=audit ;;
    esac
fi

main() {
    local act=$1 svc=$2
    # init_dev 参数：位置参数 backend/frontend 限定目标；--force 强制重装（未指定目标时全套）
    local scope=all reinstall=''
    [[ $svc == backend || $svc == frontend ]] && scope=$svc
    (( force )) && reinstall=force

    if (( check_mode )) || [[ $act == check ]]; then
        set_light_env
    elif [[ $act == stop || $act == installenv ]]; then
        # 仅停止 / 环境自举：不做项目依赖安装（自举本身不依赖项目环境）
        :
    elif [[ $act == restart ]]; then
        set_light_env
        (( force )) && init_dev "$scope" "$reinstall"
    else
        set_light_env
        if (( skip_install )); then
            msg gray "-> 已跳过依赖安装（--skip-install）"
        else
            init_dev "$scope" "$reinstall"
        fi
        # 环境信息（仅非 source 时打印）
        if [[ ${BASH_SOURCE[0]} == "$0" ]]; then show_env_info; fi
    fi

    # 动作分发（source 时空 action 仅定义函数不触发动作）
    if (( check_mode )); then
        invoke_check
    elif [[ -z $act && ${BASH_SOURCE[0]} != "$0" ]]; then
        :
    elif [[ -z $act ]]; then
        show_dev_menu
    else
        case $act in
            start)
                if [[ $svc == backend ]]; then start_backend
                elif [[ $svc == frontend ]]; then start_frontend
                else start_dev; fi ;;
            stop)
                if [[ $svc == backend ]]; then stop_backend
                elif [[ $svc == frontend ]]; then stop_frontend
                else stop_dev; fi ;;
            restart)
                if [[ $svc == backend ]]; then stop_backend; start_backend
                elif [[ $svc == frontend ]]; then stop_frontend; start_frontend
                else stop_dev; start_dev; fi ;;
            init)      init_dev "$scope" "$reinstall" ;;
            db)        update_db ;;
            resetdb)   reset_db ;;
            test)      run_backend_test ;;
            lint)      run_backend_lint ;;
            typecheck) run_frontend_typecheck ;;
            vtest)     run_frontend_test ;;
            redlines)  run_redlines ;;
            audit)     run_audit ;;
            check)     invoke_check ;;
            installenv) install_env ;;
            *) : ;;
        esac
    fi
}

main "$action" "$service"
