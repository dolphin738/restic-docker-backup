# ===========================================================================
# 一键开发脚本 (restic-web, web 版) - 参照 investment_return_tracker 风格
# ===========================================================================
# 用法（PowerShell 中从任意目录调用，任选其一）：
#
#   # 方式一：dot-source（推荐交互使用，函数留在当前会话）
#   . .\dev.ps1              # 初始化环境 + 自动安装依赖（幂等，已装则跳过）
#   . .\dev.ps1 -SkipInstall # 仅设置环境，跳过依赖安装（秒进环境）
#   . .\dev.ps1 -Force       # 强制重装后端 + 前端依赖
#   Start-Dev / Stop-Dev                 # 启停前后端（独立窗口）
#   Start-Backend / Stop-Backend         # 仅启动/停止后端
#   Start-Frontend / Stop-Frontend       # 仅启动/停止前端
#   Update-Db / Reset-Db                 # 显式管理开发库
#
#   # 方式二：直接调用（无需先 dot-source，适合一次性启停）
#   .\dev.ps1                 # 无参数 -> 初始化 + 弹出数字菜单
#   .\dev.ps1 start           # 等价于「初始化 + Start-Dev」
#   .\dev.ps1 stop            # 直接 Stop-Dev（跳过初始化）
#   .\dev.ps1 start -Force    # 强制重装依赖后启动
#   .\dev.ps1 restart                  # 重启前后端（跳过依赖安装）
#   .\dev.ps1 restart backend          # 仅重启后端
#   .\dev.ps1 restart frontend         # 仅重启前端
#   .\dev.ps1 stop backend / frontend  # 仅停止指定服务
#
#   数据库管理（本项目为 SQLite，启动时自动按 user_version 应用 app/db/migrations/）：
#     .\dev.ps1 db        # 手动补跑迁移（一般无需，启动即应用）
#     .\dev.ps1 resetdb   # 删除开发库文件（下次启动自动重建，需确认）
#
#   质量工具：
#     .\dev.ps1 test / lint          # 后端 pytest / ruff
#     .\dev.ps1 typecheck / vtest    # 前端 vue-tsc / vitest
#     .\dev.ps1 redlines / audit     # 红线机器校验 / 依赖漏洞扫描
#     .\dev.ps1 check                # 环境自检（不启动任何服务）
#   旧用法兼容：-Run 1-9（1启动 2后端 3前端 4测试 5lint 6typecheck 7vtest 8红线 9扫描）
#
#   菜单编号（无参数运行时出现）：
#     [1]启动前后端  [2]启动后端  [3]启动前端
#     [4]停止前后端  [5]停止后端  [6]停止前端
#     [7]重启前后端  [8]重启后端  [9]重启前端
#     [10]重装依赖  [11]应用迁移  [12]清空开发库
#     [13]后端测试  [14]后端lint  [15]前端类型检查
#     [16]前端测试  [17]红线校验  [18]漏洞扫描
#     [0]退出
# ===========================================================================
# 说明：
#   - 后端 = backend/（FastAPI + aiosqlite，uv 管理的 .venv），前端 = frontend/（Vite + Vue3，pnpm）。
#   - 开发库固定在 backend\.dev-data\restic.db（经 DATA_DIR 注入），避免跟随盘符漂移；
#     如需沿用旧开发库，把旧 restic.db 移到该目录或修改下方 $devDataDir。
#   - 首启（或 resetdb 后）需在 Web 界面完成 bootstrap（一次性随机密码），无脚本方式。
#   - Node 探测顺序：workbuddy -> PATH；pnpm 缺失时报错而非回退 npm。
# ===========================================================================

#requires -Version 5.1
param(
    [switch]$SkipInstall,
    [switch]$Force,
    [switch]$Check,   # 自检模式：只打印环境探测结果，不启动任何服务（兼容旧用法）
    [string]$Action = "",
    [string]$Service = "",
    [string]$Message = "",
    [string]$Run = ""  # 兼容旧用法：等价于 -Action（支持 1-9 数字映射）
)

$ErrorActionPreference = "Stop"

# 动态获取脚本名（用于提示信息）
$scriptName = Split-Path $PSCommandPath -Leaf

# ---------------------------------------------------------------------------
# 可配置变量
# ---------------------------------------------------------------------------
$BackendPort  = 8000
$FrontendPort = 5173

# ---------------------------------------------------------------------------
# 项目根目录推导
# ---------------------------------------------------------------------------
$root = $PSScriptRoot
while ($root -and -not (Test-Path (Join-Path $root "backend\pyproject.toml"))) {
    $root = Split-Path -Parent $root
}
if (-not $root) {
    Write-Host " 无法定位项目根目录（未找到 backend/pyproject.toml）。" -ForegroundColor Red
    return
}

$backend         = Join-Path $root "backend"
$frontend        = Join-Path $root "frontend"
$scriptsDir      = Join-Path $root "scripts"
$venv            = Join-Path $backend ".venv"
$venvPython      = Join-Path $venv "Scripts\python.exe"
$pyproject       = Join-Path $backend "pyproject.toml"
$devDataDir      = Join-Path $backend ".dev-data"     # 开发库固定目录（DATA_DIR）
$backendPidFile  = Join-Path $PSScriptRoot ".dev-backend.pid"
$frontendPidFile = Join-Path $PSScriptRoot ".dev-frontend.pid"

if (-not (Test-Path $backend)) {
    Write-Host " 未找到 backend/ 目录。" -ForegroundColor Red; return
}
if (-not (Test-Path $pyproject)) {
    Write-Host " 未找到 backend/pyproject.toml。" -ForegroundColor Red; return
}

# ---------------------------------------------------------------------------
# 定位 Python（venv 缺失时兜底用，要求 >=3.13）
# ---------------------------------------------------------------------------
function Find-Python {
    foreach ($cmd in @("py", "python3", "python")) {
        try {
            $p = (Get-Command $cmd -ErrorAction Stop).Source
            $ver = & $p -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($ver -and ([version]$ver -ge [version]"3.13")) { return $p }
        } catch { }
    }
    return $null
}

# ---------------------------------------------------------------------------
# 定位 uv（不在 PATH 时探测常见安装位置）
# ---------------------------------------------------------------------------
function Find-Uv {
    if ($u = Get-Command uv -ErrorAction SilentlyContinue) { return $u.Source }
    foreach ($c in @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    )) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

# ---------------------------------------------------------------------------
# 定位 Node（多候选：workbuddy -> PATH）
# ---------------------------------------------------------------------------
function Find-Node {
    $candidates = @(
        "C:\Users\dolphin738\.workbuddy\binaries\node\versions\22.22.2"
    )
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c "node.exe")) { return $c }
    }
    try {
        $n = (Get-Command node -ErrorAction Stop).Source
        if ($n) { return Split-Path -Parent $n }
    } catch { }
    return $null
}

# ---------------------------------------------------------------------------
# 定位 pnpm（仅存在于 workbuddy node 全局，缺失时报错而非回退 npm）
# ---------------------------------------------------------------------------
function Find-Pnpm {
    # 1) 与探测到的 node 同级的 pnpm（workbuddy node 全局安装的 pnpm.cmd）
    if ($nodePath -and (Test-Path (Join-Path $nodePath "pnpm.cmd"))) {
        return (Join-Path $nodePath "pnpm.cmd")
    }
    # 2) workbuddy node 固定版本目录下的全局 pnpm
    $wb = "C:\Users\dolphin738\.workbuddy\binaries\node\versions\22.22.2"
    if (Test-Path (Join-Path $wb "pnpm.cmd")) { return (Join-Path $wb "pnpm.cmd") }
    # 3) PATH 里已有的 pnpm
    try {
        $g = Get-Command pnpm -ErrorAction Stop
        if ($g) { return $g.Source }
    } catch { }
    # 4) corepack shim
    foreach ($c in @(
        "C:\Users\dolphin738\.workbuddy\binaries\node\versions\22.22.2\node_modules\corepack\shims\pnpm.cmd",
        "$env:APPDATA\npm\pnpm.cmd"
    )) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

# ---------------------------------------------------------------------------
# 定位 Git Bash（红线校验用；排除 system32 的 WSL 存根）
# ---------------------------------------------------------------------------
$BashExe = $null
foreach ($cand in @(
    "$env:USERPROFILE\.workbuddy\binaries\PortableGit\versions\1.2.0\bin\bash.exe",
    "C:\Program Files\Git\bin\bash.exe",
    "C:\Program Files (x86)\Git\bin\bash.exe"
)) {
    if (Test-Path $cand) { $BashExe = $cand; break }
}
if (-not $BashExe) {
    $resolved = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($resolved -and $resolved.Source -notlike "$env:SystemRoot\system32*") {
        $BashExe = $resolved.Source
    }
}

$nodePath = Find-Node
if (-not $nodePath) {
    Write-Host " 未检测到 Node。前端将无法启动。" -ForegroundColor Yellow
}

$pnpmPath = Find-Pnpm
if (-not $pnpmPath) {
    Write-Host " 未检测到 pnpm。前端将无法正确安装依赖（请勿回退 npm）。" -ForegroundColor Yellow
}

$UvExe = Find-Uv

# 子窗口宿主：本机 PowerShell 7 优先，退回 Windows PowerShell
$psExe = "powershell"
try {
    $pwsh = Get-Command pwsh -ErrorAction Stop
    if ($pwsh) { $psExe = $pwsh.Source }
} catch { }

# ---------------------------------------------------------------------------
# uv / venv 准备（幂等）
# ---------------------------------------------------------------------------
function Ensure-Uv {
    if ($UvExe -and (Test-Path $UvExe)) { return $true }
    if (Get-Command uv -ErrorAction SilentlyContinue) { return $true }
    Write-Host "-> 未检测到 uv，尝试安装到当前 Python..." -ForegroundColor Cyan
    $py = if (Test-Path $venvPython) { $venvPython } else { Find-Python }
    if (-not $py) { Write-Host "  无可用 Python，无法安装 uv。" -ForegroundColor Red; return $false }
    & $py -m pip install -q uv
    $cand = Join-Path (Split-Path -Parent $py) "uv.exe"
    if (Test-Path $cand) { $script:UvExe = $cand; return $true }
    if (Get-Command uv -ErrorAction SilentlyContinue) { return $true }
    Write-Host "  uv 安装失败。" -ForegroundColor Red
    return $false
}

function Ensure-Venv {
    if (Test-Path $venvPython) { return $true }
    Write-Host "-> backend/.venv 不存在，尝试用 uv 构建（uv sync --extra dev）..." -ForegroundColor Cyan
    if (-not (Ensure-Uv)) { return $false }
    $uv = if (Get-Command uv -ErrorAction SilentlyContinue) { "uv" } else { $UvExe }
    $env:UV_DEFAULT_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"
    & $uv sync --extra dev --project $backend
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  uv sync 失败（退出码 $LASTEXITCODE）。" -ForegroundColor Red
        return $false
    }
    return (Test-Path $venvPython)
}

# ---------------------------------------------------------------------------
# 依赖安装（幂等；-Force 重装）
# ---------------------------------------------------------------------------
function Init-Dev {
    [CmdletBinding()]
    param([switch]$Force)

    if (-not (Ensure-Venv)) { return }

    Write-Host "-> 同步后端依赖 (uv sync --extra dev)" -ForegroundColor Cyan
    $uv = if (Get-Command uv -ErrorAction SilentlyContinue) { "uv" } else { $UvExe }
    $env:UV_DEFAULT_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"
    $syncArgs = @('sync', '--extra', 'dev')
    if ($Force) { $syncArgs += '--reinstall' }
    Push-Location $backend
    try {
        & $uv @syncArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  uv sync 失败（退出码 $LASTEXITCODE）。" -ForegroundColor Red
        }
    } finally { Pop-Location }
    Write-Host "-> 后端依赖同步完成" -ForegroundColor Green

    # 前端依赖（必须用 pnpm，避免 npm 覆写 pnpm 布局与 .npmrc 专属配置）
    $pkg = Join-Path $frontend "package.json"
    if (Test-Path $pkg) {
        $nodeModules = Join-Path $frontend "node_modules"
        if ((-not $Force) -and (Test-Path $nodeModules)) {
            Write-Host "-> 前端 node_modules 已存在，跳过安装" -ForegroundColor DarkGray
        } else {
            $pm = Get-PnpmCommand
            if (-not $pm) {
                Write-Host "  未找到 pnpm。前端安装需要 pnpm（勿用 npm，会破坏布局与 .npmrc 配置）。" -ForegroundColor Red
                return
            }
            Write-Host "-> 安装前端依赖 (frontend) via pnpm" -ForegroundColor Cyan
            Push-Location $frontend
            try {
                & $pm install
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  pnpm install 失败（退出码 $LASTEXITCODE）。" -ForegroundColor Red
                }
            } finally { Pop-Location }
        }
    } else {
        Write-Host "-> frontend/ 尚未初始化（无 package.json），跳过前端依赖" -ForegroundColor DarkGray
    }
}

# ===========================================================================
# 启动 / 停止单个服务（独立窗口 + PID 文件）
# ===========================================================================

function Start-Service {
    param([string]$Name, [string]$Script, [string]$PidFile, [string]$Label)
    $tmpDir = if ($env:TEMP) { $env:TEMP } else { $env:TMPDIR }
    $scriptFile = Join-Path $tmpDir "dev-$Name-$(Get-Random).ps1"
    [System.IO.File]::WriteAllText($scriptFile, $Script, (New-Object System.Text.UTF8Encoding $true))

    $proc = Start-Process $psExe -ArgumentList "-NoExit", "-File", $scriptFile -PassThru
    if ($proc) {
        $proc.Id | Out-File -FilePath $PidFile -Encoding utf8
        Write-Host "-> $Label 已启动（PID=$($proc.Id)）" -ForegroundColor Green
    } else {
        Write-Host "  $Label 启动失败。" -ForegroundColor Red
    }
    return $proc
}

function Stop-Service {
    param([string]$PidFile, [string]$Label)
    if (-not (Test-Path $PidFile)) {
        Write-Host "  $Label 未在运行。" -ForegroundColor Yellow; return
    }
    Write-Host "-> 停止 $Label ..." -ForegroundColor Cyan
    Get-Content $PidFile | ForEach-Object {
        $pidv = $_.Trim()
        if ($pidv -match '^\d+$') {
            & taskkill /PID $pidv /T /F 2>$null
            Write-Host "  -> 已请求关闭进程树 PID=$pidv" -ForegroundColor Gray
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "-> $Label 已停止。" -ForegroundColor Green
}

# ===========================================================================
# 启动函数
# ===========================================================================

function Start-Backend {
    if (-not (Ensure-Venv)) { return }
    $backendCmd = @"
`$ErrorActionPreference = 'Stop'
`$env:NODE_OPTIONS = `$null
`$env:DATA_DIR = "$devDataDir"
Set-Location "$backend"
New-Item -ItemType Directory -Force -Path "$devDataDir" | Out-Null
Write-Host "-> 启动后端：uvicorn app.main:app --reload --port $BackendPort（DATA_DIR=$devDataDir）" -ForegroundColor Cyan
& "$venvPython" -m uvicorn app.main:app --reload --port $BackendPort
"@
    $proc = Start-Service -Name "backend" -Script $backendCmd -PidFile $backendPidFile -Label "后端"
    if ($proc) {
        Write-Host "  后端 API     : http://127.0.0.1:$BackendPort  文档 /docs" -ForegroundColor Yellow
        Write-Host "  首启提示     : 需在 Web 界面完成 bootstrap（一次性随机密码）" -ForegroundColor Yellow
    }
    return $proc
}

function Start-Frontend {
    $pm = Get-PnpmCommand
    if (-not $pm) {
        Write-Host "  未找到 pnpm，前端无法启动（勿回退 npm）。" -ForegroundColor Red; return
    }
    $childPath = Split-Path -Parent $pm
    $frontendCmd = @"
`$ErrorActionPreference = 'Stop'
`$env:NODE_OPTIONS = `$null
`$env:PATH = "$childPath;$nodePath;`$env:PATH"
Set-Location "$frontend"
Write-Host "-> 启动前端(frontend)：pnpm dev (port $FrontendPort)" -ForegroundColor Cyan
& "$pm" dev
"@
    $proc = Start-Service -Name "frontend" -Script $frontendCmd -PidFile $frontendPidFile -Label "前端"
    if ($proc) { Write-Host "  前端页面     : http://localhost:$FrontendPort" -ForegroundColor Yellow }
    return $proc
}

function Start-Dev {
    Write-Host "-> 启动开发服务（前后端并发，各开独立窗口）" -ForegroundColor Cyan
    $b = Start-Backend
    $f = Start-Frontend
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  开发服务已启动 (restic-web)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  后端 API     : http://127.0.0.1:$BackendPort  文档 /docs" -ForegroundColor Yellow
    Write-Host "  前端页面     : http://localhost:$FrontendPort" -ForegroundColor Yellow
    Write-Host "  API 代理     : 前端 /api 已通过 Vite 代理转发到 http://127.0.0.1:$BackendPort" -ForegroundColor DarkGray
    Write-Host "  开发库       : $(Join-Path $devDataDir 'restic.db')（启动自动应用迁移）" -ForegroundColor DarkGray
    Write-Host "  后端 PID     : $(if ($b) { $b.Id } else { 'N/A' })"
    Write-Host "  前端 PID     : $(if ($f) { $f.Id } else { 'N/A' })"
    Write-Host "  停止命令     : Stop-Dev（或 .\$scriptName stop）" -ForegroundColor Yellow
    Write-Host "  单服务重启   : .\$scriptName restart backend / restart frontend" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
}

# ===========================================================================
# 停止函数
# ===========================================================================

function Stop-Backend  { Stop-Service -PidFile $backendPidFile  -Label "后端" }
function Stop-Frontend { Stop-Service -PidFile $frontendPidFile -Label "前端" }
function Stop-Dev {
    Stop-Backend
    Stop-Frontend
}

# ===========================================================================
# 数据库管理函数
# ===========================================================================

function Update-Db {
    [CmdletBinding()]
    param()
    if (-not (Ensure-Venv)) { return }
    Write-Host "-> 手动应用开发库迁移（init_database -> migrate，按 user_version 增量）" -ForegroundColor Cyan

    # 迁移逻辑复用应用自身：启动时会自动执行，这里仅提供显式补跑入口
    $migratePy = @"
import asyncio
import sys
sys.path.insert(0, r"$backend")
from app.settings import get_settings
from app.db.database import init_database

async def main():
    s = get_settings()
    await init_database(s.db_path)
    print("OK migrations applied ->", s.db_path)

asyncio.run(main())
"@
    $tmpPy = Join-Path $env:TEMP ("dev-migrate-$(Get-Random).py")
    [System.IO.File]::WriteAllText($tmpPy, $migratePy, (New-Object System.Text.UTF8Encoding $true))
    $oldDataDir = $env:DATA_DIR
    $env:DATA_DIR = $devDataDir
    try {
        & $venvPython $tmpPy
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  迁移执行失败（退出码 $LASTEXITCODE）。" -ForegroundColor Red; return
        }
    } finally {
        if ($null -ne $oldDataDir) { $env:DATA_DIR = $oldDataDir }
        else { Remove-Item Env:\DATA_DIR -ErrorAction SilentlyContinue }
        Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
    }
    Write-Host "-> 开发库表结构已更新到最新迁移。" -ForegroundColor Green
}

function Reset-Db {
    [CmdletBinding()]
    param()
    $dbFile = Join-Path $devDataDir "restic.db"
    if (-not (Test-Path $dbFile)) {
        Write-Host "  开发库不存在：$dbFile（无需清空）。" -ForegroundColor Yellow; return
    }

    Write-Host "!! 即将删除开发库 '$dbFile'（含 -wal/-shm），此操作不可恢复！" -ForegroundColor Red
    try {
        $ans = (Read-Host "确认清空？输入 y 继续 (y/N)").Trim().ToLower()
    } catch {
        Write-Host "-> 非交互环境，已取消清空。" -ForegroundColor DarkGray; return
    }
    if ($ans -ne 'y') {
        Write-Host "-> 已取消清空。" -ForegroundColor Yellow; return
    }

    foreach ($f in @($dbFile, "$dbFile-wal", "$dbFile-shm")) {
        if (Test-Path $f) {
            Remove-Item $f -Force
            Write-Host "  -> 已删除 $f" -ForegroundColor Gray
        }
    }
    Write-Host "-> 开发库已清空。下次启动后端时自动重建全部表并重新 bootstrap。" -ForegroundColor Green
}

# ===========================================================================
# 质量工具（本仓库特有，沿用既有实测实现）
# ===========================================================================

function Run-BackendTest {
    if (-not (Ensure-Venv)) { return }
    Write-Host "-> 后端全量测试（pytest）..." -ForegroundColor Cyan
    Push-Location $backend
    try { & $venvPython -m pytest -q } finally { Pop-Location }
}

function Run-BackendLint {
    if (-not (Ensure-Venv)) { return }
    Write-Host "-> 后端 lint（ruff）..." -ForegroundColor Cyan
    Push-Location $backend
    try { & $venvPython -m ruff check app tests } finally { Pop-Location }
}

function Run-FrontendTypecheck {
    $pm = Get-PnpmCommand
    if (-not $pm) { Write-Host "  未找到 pnpm。" -ForegroundColor Red; return }
    Write-Host "-> 前端类型检查（vue-tsc）..." -ForegroundColor Cyan
    Push-Location $frontend
    try { & $pm run typecheck } finally { Pop-Location }
}

function Run-FrontendTest {
    $pm = Get-PnpmCommand
    if (-not $pm) { Write-Host "  未找到 pnpm。" -ForegroundColor Red; return }
    Write-Host "-> 前端测试（vitest run）..." -ForegroundColor Cyan
    Push-Location $frontend
    try { & $pm run test } finally { Pop-Location }
}

function Run-Redlines {
    if (-not $BashExe) {
        Write-Host "  未找到可用的 Git Bash（system32\bash.exe 是 WSL 存根，不能用于 .sh）。" -ForegroundColor Red
        return
    }
    Write-Host "-> 红线机器校验（verify_redlines.sh）..." -ForegroundColor Cyan
    # 关键坑（实测）：本机便携 Git 的 bash 对 Windows PATH 的 MSYS 转换是坏的
    # （/c 挂载缺失）——在 bash 内部用 POSIX 形式 export PATH，脚本路径也转 POSIX。
    $bashScript  = Join-Path $scriptsDir "verify_redlines.sh"
    $posixScript = $bashScript.Replace('\', '/').Replace(':', '')
    $posixScript = '/' + $posixScript.TrimStart('/')   # D:\x\y -> /d/x/y
    $cmd = 'export PATH="/usr/bin:/bin:$PATH"; bash "' + $posixScript + '"'
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    & $BashExe -c $cmd
}

function Run-Audit {
    if (-not (Ensure-Venv)) { return }
    if (-not (Ensure-Uv)) { return }
    # pip-audit 是临时工具，不在 uv.lock 内；uv sync 会清掉它，故每次先确保存在
    & $venvPython -c "import pip_audit" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "-> pip-audit 未安装，临时安装（不入锁）..." -ForegroundColor Cyan
        $env:UV_DEFAULT_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"
        $uv = if (Get-Command uv -ErrorAction SilentlyContinue) { "uv" } else { $UvExe }
        & $uv pip install --python $venvPython pip-audit -q
    }
    Write-Host "-> 依赖漏洞扫描（pip-audit + OSV；pypi.org 直连不可达，必须用 OSV）..." -ForegroundColor Cyan
    $tmpReq = Join-Path $env:TEMP "restic-web-audit-req.txt"
    $env:UV_DEFAULT_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"
    $uv2 = if (Get-Command uv -ErrorAction SilentlyContinue) { "uv" } else { $UvExe }
    & $uv2 export --no-dev --no-hashes --format requirements-txt --output-file $tmpReq --project $backend
    & $venvPython -m pip_audit -r $tmpReq -s osv --progress-spinner off --timeout 60
    if ($LASTEXITCODE -eq 0) { Write-Host "-> 无已知漏洞" -ForegroundColor Green }
    else { Write-Host "-> 存在漏洞，见上表明细（HIGH/CRITICAL = Blocker）" -ForegroundColor Red }
}

# ===========================================================================
# 环境初始化与工具
# ===========================================================================

function Set-LightEnv {
    if (Test-Path $venvPython) {
        $scripts = Split-Path -Parent $venvPython
        if ($env:PATH -notmatch [regex]::Escape($scripts)) { $env:PATH = "$scripts;$env:PATH" }
    }
    if ($nodePath -and (Test-Path "$nodePath\node.exe")) { $env:PATH = "$nodePath;$env:PATH" }
    if ($pnpmPath) {
        $pnpmBin = Split-Path -Parent $pnpmPath
        if ($env:PATH -notmatch [regex]::Escape($pnpmBin)) { $env:PATH = "$pnpmBin;$env:PATH" }
    }
    $env:NODE_OPTIONS = $null
}

# 返回当前 pnpm 命令（优先解析出的真实路径），调用不了则报错而非回退 npm
function Get-PnpmCommand {
    if (-not $pnpmPath) { return $null }
    try { & $pnpmPath --version 2>$null | Out-Null; return $pnpmPath } catch {
        # pnpm.cmd 无法直接调用时改走同名可执行
        $alt = $pnpmPath -replace '\.cmd$', ''
        if (Test-Path $alt) { return $alt }
        return $null
    }
}

function Show-EnvInfo {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  开发环境已初始化 (restic-web)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Python     : $(if (Test-Path $venvPython) { & $venvPython -V 2>&1 } else { 'venv 缺失（init 时由 uv 构建）' })"
    Write-Host "  venv       : $venv"
    Write-Host "  uv         : $(if ($UvExe) { $UvExe } else { '未找到（仅 venv 缺失时需要）' })"
    Write-Host "  Node       : $(if ($nodePath) { "$(node -v 2>$null)  @ $nodePath" } else { '不可用' })"
    Write-Host "  pnpm       : $(if ($pnpmPath) { & $pnpmPath --version 2>$null } else { '不可用（前端需 pnpm，勿用 npm）' })"
    Write-Host "  bash       : $(if ($BashExe) { $BashExe } else { '未找到（红线校验不可用）' })"
    Write-Host "  BackendPort: $BackendPort"
    Write-Host "  FrontendPort: $FrontendPort"
    Write-Host "  开发库     : $(Join-Path $devDataDir 'restic.db')"
    Write-Host "  目录       : $root"
    Write-Host "  前端目录   : $frontend"
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "常用命令：" -ForegroundColor Yellow
    Write-Host "  . .\$scriptName              # 初始化环境并安装依赖"
    Write-Host "  . .\$scriptName -SkipInstall # 仅设置环境，跳过依赖安装"
    Write-Host "  . .\$scriptName -Force       # 强制重装依赖"
    Write-Host "  .\$scriptName start          # 直接一键启动"
    Write-Host "  .\$scriptName stop           # 直接停止"
    Write-Host "  .\$scriptName restart        # 重启（跳过依赖安装）"
    Write-Host "  .\$scriptName restart backend/frontend  # 仅重启指定服务"
    Write-Host "  .\$scriptName stop backend/frontend     # 仅停止指定服务"
    Write-Host "  .\$scriptName db             # 手动补跑开发库迁移"
    Write-Host "  .\$scriptName resetdb        # 清空开发库（危险，启动自动重建）"
    Write-Host "  .\$scriptName test/lint      # 后端 pytest / ruff"
    Write-Host "  .\$scriptName typecheck/vtest  # 前端 vue-tsc / vitest"
    Write-Host "  .\$scriptName redlines/audit   # 红线校验 / 漏洞扫描"
    Write-Host "  .\$scriptName check          # 环境自检"
    Write-Host ""
}

# ---------------------------------------------------------------------------
# 自检：只打印探测结果，不执行任何服务
# ---------------------------------------------------------------------------
function Invoke-Check {
    Write-Host "=== 环境自检 ===" -ForegroundColor Cyan
    Write-Host ("  项目根        : " + $root)
    Write-Host ("  backend/.venv : " + $(if (Test-Path $venvPython) { "有 ($venvPython)" } else { "无" }))
    Write-Host ("  uv            : " + $(if ($UvExe) { $UvExe } else { "未找到（仅 venv 缺失时需要）" }))
    Write-Host ("  node_modules  : " + $(if (Test-Path (Join-Path $frontend "node_modules")) { "有" } else { "无（首次跑前端需 pnpm install）" }))
    Write-Host ("  node          : " + $(if ($nodePath) { $nodePath } else { "未找到" }))
    Write-Host ("  pnpm          : " + $(if ($pnpmPath) { $pnpmPath } else { "未找到" }))
    Write-Host ("  bash          : " + $(if ($BashExe) { $BashExe } else { "未找到（红线校验不可用）" }))
    Write-Host ("  verify_redlines: " + $(if (Test-Path (Join-Path $scriptsDir "verify_redlines.sh")) { "有" } else { "缺失" }))
    Write-Host ("  开发库        : $devDataDir " + $(if (Test-Path (Join-Path $devDataDir "restic.db")) { "(已存在)" } else { "(未创建，首启自动生成)" }))
    Write-Host ("  后端 PID 文件 : " + $(if (Test-Path $backendPidFile) { (Get-Content $backendPidFile -Raw).Trim() } else { "无（未运行）" }))
    Write-Host ("  前端 PID 文件 : " + $(if (Test-Path $frontendPidFile) { (Get-Content $frontendPidFile -Raw).Trim() } else { "无（未运行）" }))
    if (Test-Path $venvPython) {
        $v = & $venvPython -c "import fastapi, starlette, uvicorn; print('fastapi', fastapi.__version__, '| starlette', starlette.__version__, '| uvicorn', uvicorn.__version__)"
        Write-Host ("  关键依赖      : " + $v)
    }
}

# ===========================================================================
# 交互式菜单
# ===========================================================================

function Show-DevMenu {
    try { if ([Console]::IsInputRedirected) { return } } catch { return }

    while ($true) {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "  开发菜单 (restic-web)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "  [1] 启动前后端          [4] 停止前后端          [7] 重启前后端" -ForegroundColor White
        Write-Host "  [2] 仅启动后端          [5] 仅停止后端          [8] 仅重启后端" -ForegroundColor White
        Write-Host "  [3] 仅启动前端          [6] 仅停止前端          [9] 仅重启前端" -ForegroundColor White
        Write-Host "  [10] 重装依赖           [11] 应用迁移           [12] 清空开发库" -ForegroundColor Yellow
        Write-Host "  [13] 后端测试           [14] 后端 lint          [15] 前端类型检查" -ForegroundColor Yellow
        Write-Host "  [16] 前端测试           [17] 红线校验           [18] 漏洞扫描" -ForegroundColor Yellow
        Write-Host "  [0] 退出菜单" -ForegroundColor DarkGray
        Write-Host "========================================" -ForegroundColor Cyan
        try {
            $choice = (Read-Host "请输入编号").Trim().ToLower()
        } catch {
            Write-Host "-> 当前主机非交互，已退出菜单。" -ForegroundColor DarkGray; return
        }
        switch ($choice) {
            "1"  { Start-Dev }
            "2"  { Start-Backend }
            "3"  { Start-Frontend }
            "4"  { Stop-Dev }
            "5"  { Stop-Backend }
            "6"  { Stop-Frontend }
            "7"  { Stop-Dev; Start-Dev }
            "8"  { Stop-Backend; Start-Backend }
            "9"  { Stop-Frontend; Start-Frontend }
            "10" { Init-Dev -Force:$true }
            "11" { Update-Db }
            "12" { Reset-Db }
            "13" { Run-BackendTest }
            "14" { Run-BackendLint }
            "15" { Run-FrontendTypecheck }
            "16" { Run-FrontendTest }
            "17" { Run-Redlines }
            "18" { Run-Audit }
            "0"  { Write-Host "-> 已退出菜单。" -ForegroundColor DarkGray; return }
            ""   { return }
            default { Write-Host "  无效输入：$choice（请输入 0-18）" -ForegroundColor Red }
        }
    }
}

# ===========================================================================
# 顶层控制流（对照 investment 版：stop 最快 / restart 轻环境 / 其余完整初始化）
# ===========================================================================

$isDotSourced = ($MyInvocation.InvocationName -eq '.')

# -Run 旧数字用法兼容（1-9），语义词直接透传
if ($Run -and -not $Action) {
    switch ($Run) {
        "1" { $Action = "start" }
        "2" { $Action = "start"; $Service = "backend" }
        "3" { $Action = "start"; $Service = "frontend" }
        "4" { $Action = "test" }
        "5" { $Action = "lint" }
        "6" { $Action = "typecheck" }
        "7" { $Action = "vtest" }
        "8" { $Action = "redlines" }
        "9" { $Action = "audit" }
        default { $Action = $Run }
    }
}

if ($Check -or $Action -eq "check") {
    Set-LightEnv
} elseif ($Action -eq "stop") {
    # 仅停止，不做任何环境设置（最快）
} elseif ($Action -eq "restart") {
    Set-LightEnv
    if ($Force) { Init-Dev -Force:$true }
} else {
    Set-LightEnv
    if ($SkipInstall) {
        Write-Host "-> 已跳过依赖安装（-SkipInstall）" -ForegroundColor DarkGray
    } else {
        Init-Dev -Force:$Force
    }
    # 环境信息（仅非 dot-source 时打印）
    if (-not $isDotSourced) { Show-EnvInfo }
}

# ===========================================================================
# 动作分发（dot-source 时空 Action 仅定义函数不触发动作）
# ===========================================================================

if ($Check) {
    Invoke-Check
} elseif ($Action -eq "" -and -not $isDotSourced) {
    Show-DevMenu
} else {
    switch ($Action) {
        "start" {
            if ($Service -eq "backend")      { Start-Backend }
            elseif ($Service -eq "frontend") { Start-Frontend }
            else                             { Start-Dev }
        }
        "stop" {
            if ($Service -eq "backend")      { Stop-Backend }
            elseif ($Service -eq "frontend") { Stop-Frontend }
            else                             { Stop-Dev }
        }
        "restart" {
            if ($Service -eq "backend")      { Stop-Backend;  Start-Backend }
            elseif ($Service -eq "frontend") { Stop-Frontend; Start-Frontend }
            else                             { Stop-Dev; Start-Dev }
        }
        "init"      { Init-Dev -Force:$Force }
        "db"        { Update-Db }
        "resetdb"   { Reset-Db }
        "test"      { Run-BackendTest }
        "lint"      { Run-BackendLint }
        "typecheck" { Run-FrontendTypecheck }
        "vtest"     { Run-FrontendTest }
        "redlines"  { Run-Redlines }
        "audit"     { Run-Audit }
        "check"     { Invoke-Check }
        default { }
    }
}
