# ============================================================
# restic-web 开发环境启动脚本（dev.ps1）
#
# 需要 PowerShell 7+（pwsh）。用法：
#   .\dev.ps1              # 交互菜单
#   .\dev.ps1 -Check       # 仅自检环境（不启动任何服务）
#
# 菜单：
#   1. 启动开发环境（后端 :8000 + 前端 :5173，各开一个窗口）
#   2. 仅启动后端（uvicorn --reload）
#   3. 仅启动前端（vite dev server）
#   4. 后端全量测试（pytest）
#   5. 后端 lint（ruff）
#   6. 前端 typecheck（vue-tsc）
#   7. 前端测试（vitest run）
#   8. 红线机器校验（scripts/verify_redlines.sh，需 Git Bash）
#   9. 依赖漏洞扫描（pip-audit + OSV）
#   0. 退出
#
# 说明：本仓库无容器也能开发；生产部署走 docker/（build web版/ 为上下文）。
# ============================================================

param(
    [switch]$Check,   # 自检模式：只打印环境探测结果，不执行任何服务
    [string]$Run      # 非交互模式：直接执行菜单动作（1-9 或 all），不进菜单
)

$ErrorActionPreference = "Stop"

# ---- 路径锚点：脚本位于 web版\dev-scripts\，项目根是其上一级 ----
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # web版\dev-scripts
$Root      = Split-Path -Parent $ScriptDir                     # web版/
$Backend   = Join-Path $Root "backend"
$Frontend  = Join-Path $Root "frontend"
$Scripts   = Join-Path $Root "scripts"

# ---- Python：优先项目 venv（不依赖 uv 在 PATH）----
$VenvPython = Join-Path $Backend ".venv\Scripts\python.exe"
$HasVenv    = Test-Path $VenvPython

# ---- uv：不在 PATH 时探测常见位置（仅 venv 缺失时才需要）----
$UvExe = $null
foreach ($cand in @(
    "$env:USERPROFILE\.local\bin\uv.exe",
    "$env:LOCALAPPDATA\Programs\uv\uv.exe"
)) {
    if (Test-Path $cand) { $UvExe = $cand; break }
}

# ---- bash：红线校验用。优先显式 Git Bash 路径；PATH 里命中的 system32\bash.exe
#      是 WSL 存根（跑 .sh 会走 Linux 路径语义），必须排除。----
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

function Write-Info($msg)  { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host $msg -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Err2($msg)  { Write-Host $msg -ForegroundColor Red }

# ------------------------------------------------------------
# 环境准备：venv 缺失时用 uv sync 构建；前端依赖缺失时 pnpm install
# ------------------------------------------------------------
function Ensure-Backend {
    if ($HasVenv) { return $true }
    Write-Warn2 "[backend] .venv 不存在，尝试用 uv 构建（走腾讯云镜像）..."
    if (-not $UvExe) {
        Write-Err2 "[backend] 未找到 uv.exe，且 .venv 不存在，无法继续。请先安装 uv 或手动创建 venv。"
        return $false
    }
    $env:UV_DEFAULT_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"
    & $UvExe sync --extra dev --project $Backend
    return ($LASTEXITCODE -eq 0)
}

function Ensure-Frontend {
    if (Test-Path (Join-Path $Frontend "node_modules")) { return $true }
    Write-Warn2 "[frontend] node_modules 不存在，执行 pnpm install ..."
    Push-Location $Frontend
    try { pnpm install; return ($LASTEXITCODE -eq 0) } finally { Pop-Location }
}

# ------------------------------------------------------------
# 各菜单动作
# ------------------------------------------------------------
function Start-Backend {
    if (-not (Ensure-Backend)) { return }
    Write-Info "启动后端 uvicorn :8000（--reload，新窗口）..."
    Start-Process -WorkingDirectory $Backend -FilePath $VenvPython `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"
    Write-Ok  "后端已在新窗口启动：http://127.0.0.1:8000  API 文档 /docs"
    Write-Warn2 "首启需在 Web 界面完成 bootstrap（改默认口令），凭据走界面配置。"
}

function Start-Frontend {
    if (-not (Ensure-Frontend)) { return }
    Write-Info "启动前端 vite :5173（新窗口）..."
    Start-Process -WorkingDirectory $Frontend -FilePath "pnpm" `
        -ArgumentList "dev"
    Write-Ok "前端已在新窗口启动：http://localhost:5173（/api 代理到 127.0.0.1:8000）"
}

function Start-All {
    Start-Backend
    Start-Frontend
    Write-Ok "`n开发环境已拉起：前端 http://localhost:5173 → 后端 http://127.0.0.1:8000"
}

function Run-BackendTest {
    if (-not (Ensure-Backend)) { return }
    Write-Info "后端全量测试（pytest）..."
    Push-Location $Backend
    try { & $VenvPython -m pytest -q } finally { Pop-Location }
}

function Run-BackendLint {
    if (-not (Ensure-Backend)) { return }
    Write-Info "后端 lint（ruff）..."
    Push-Location $Backend
    try { & $VenvPython -m ruff check app tests } finally { Pop-Location }
}

function Run-FrontendTypecheck {
    if (-not (Ensure-Frontend)) { return }
    Write-Info "前端类型检查（vue-tsc）..."
    Push-Location $Frontend
    try { pnpm run typecheck } finally { Pop-Location }
}

function Run-FrontendTest {
    if (-not (Ensure-Frontend)) { return }
    Write-Info "前端测试（vitest run）..."
    Push-Location $Frontend
    try { pnpm run test } finally { Pop-Location }
}

function Run-Redlines {
    if (-not $BashExe) {
        Write-Err2 "未找到可用的 Git Bash（system32\bash.exe 是 WSL 存根，不能用于 .sh）。"
        return
    }
    Write-Info "红线机器校验（verify_redlines.sh）..."
    # 关键坑（实测）：本机便携 Git 的 bash 对 Windows PATH 的 MSYS 转换是坏的
    # （/c 挂载缺失）——无论怎么往 $env:PATH 前插 <Git>\usr\bin，bash 内都找不到
    # grep/dirname。有效解法：在 bash 内部用 POSIX 形式 export PATH，脚本路径
    # 也转成 POSIX 形式传入。对正常安装的 Git Bash 同样适用。
    $BashScript  = Join-Path $Scripts "verify_redlines.sh"
    $posixScript = $BashScript.Replace('\', '/').Replace(':', '')
    $posixScript = '/' + $posixScript.TrimStart('/')   # D:\x\y -> /d/x/y
    $cmd = 'export PATH="/usr/bin:/bin:$PATH"; bash "' + $posixScript + '"'
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    & $BashExe -c $cmd
}

function Run-Audit {
    if (-not (Ensure-Backend)) { return }
    # pip-audit 是临时工具，不在 uv.lock 内；uv sync 会清掉它，故每次先确保存在
    & $VenvPython -c "import pip_audit" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "pip-audit 未安装，临时安装（不入锁）..."
        $env:UV_DEFAULT_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"
        if ($UvExe) {
            & $UvExe pip install --python $VenvPython pip-audit -q
        } else {
            & $VenvPython -m pip install -q pip-audit -i https://mirrors.cloud.tencent.com/pypi/simple
        }
    }
    Write-Info "依赖漏洞扫描（pip-audit + OSV；pypi.org 直连在本环境不可达，必须用 OSV）..."
    $tmpReq = Join-Path $env:TEMP "restic-web-audit-req.txt"
    $env:UV_DEFAULT_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"
    if ($UvExe) {
        & $UvExe export --no-dev --no-hashes --format requirements-txt --output-file $tmpReq --project $Backend
    } else {
        Write-Err2 "需要 uv 来从 uv.lock 导出依赖清单。"
        return
    }
    & $VenvPython -m pip_audit -r $tmpReq -s osv --progress-spinner off --timeout 60
    if ($LASTEXITCODE -eq 0) { Write-Ok "无已知漏洞" } else { Write-Err2 "存在漏洞，见上表明细（HIGH/CRITICAL = Blocker）" }
}

# ------------------------------------------------------------
# 自检模式：-Check
# ------------------------------------------------------------
if ($Check) {
    Write-Info "=== 环境自检 ==="
    Write-Host ("  项目根        : " + $Root)
    Write-Host ("  backend/.venv : " + $(if ($HasVenv) { "有 ($VenvPython)" } else { "无" }))
    Write-Host ("  uv.exe        : " + $(if ($UvExe) { $UvExe } else { "未找到（仅 venv 缺失时需要）" }))
    Write-Host ("  node_modules  : " + $(if (Test-Path (Join-Path $Frontend "node_modules")) { "有" } else { "无（首次跑前端需 pnpm install）" }))
    Write-Host ("  bash          : " + $(if ($BashExe) { $BashExe } else { "未找到（菜单 8 不可用）" }))
    Write-Host ("  verify_redlines: " + $(if (Test-Path (Join-Path $Scripts "verify_redlines.sh")) { "有" } else { "缺失" }))
    if ($HasVenv) {
        $v = & $VenvPython -c "import fastapi, starlette, uvicorn; print('fastapi', fastapi.__version__, '| starlette', starlette.__version__, '| uvicorn', uvicorn.__version__)"
        Write-Host ("  关键依赖      : " + $v)
    }
    return
}

# ------------------------------------------------------------
# 动作分发（菜单与 -Run 共用）
# ------------------------------------------------------------
function Invoke-Choice([string]$choice) {
    switch ($choice) {
        "1"    { Start-All }
        "2"    { Start-Backend }
        "3"    { Start-Frontend }
        "4"    { Run-BackendTest }
        "5"    { Run-BackendLint }
        "6"    { Run-FrontendTypecheck }
        "7"    { Run-FrontendTest }
        "8"    { Run-Redlines }
        "9"    { Run-Audit }
        "0"    { return }
        "all"  { Start-All }
        "lint" { Run-BackendLint }
        "test" { Run-BackendTest }
        default { Write-Warn2 "无效选择：$choice" }
    }
}

# -Run 非交互模式
if ($Run) {
    Invoke-Choice $Run
    return
}

# ------------------------------------------------------------
# 交互菜单
# ------------------------------------------------------------
while ($true) {
    Write-Host ""
    Write-Host "======== restic-web 开发工具 ========" -ForegroundColor Cyan
    Write-Host " 1. 启动开发环境（后端 + 前端）"
    Write-Host " 2. 仅启动后端  (:8000, --reload)"
    Write-Host " 3. 仅启动前端  (:5173, vite)"
    Write-Host " 4. 后端全量测试 (pytest)"
    Write-Host " 5. 后端 lint    (ruff)"
    Write-Host " 6. 前端 typecheck (vue-tsc)"
    Write-Host " 7. 前端测试     (vitest)"
    Write-Host " 8. 红线机器校验 (verify_redlines.sh)"
    Write-Host " 9. 依赖漏洞扫描 (pip-audit + OSV)"
    Write-Host " 0. 退出"
    $choice = Read-Host "请选择"
    Invoke-Choice $choice
}
