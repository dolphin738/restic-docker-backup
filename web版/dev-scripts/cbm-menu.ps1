#Requires -Version 7.0
<#
.SYNOPSIS
    codebase-memory-mcp 交互式分级菜单脚本（Windows PowerShell 7.x / pwsh）
.DESCRIPTION
    基于《codebase-memory-mcp 使用教程与配置指南》整理，
    将文档中适用于 Windows PowerShell 的命令做成分级菜单，便于日常使用。
    运行方式：pwsh -File cbm-menu.ps1  或在 pwsh 7 中执行 .\cbm-menu.ps1
#>

# ============================================================
# 全局设置
# ============================================================
# 控制台输出统一为 UTF-8，避免 CBM 错误消息中的长破折号等特殊字符显示为 GBK 乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$script:CbmExe   = 'codebase-memory-mcp'   # CLI 可执行文件名
$script:CbmUiPort = 9749                    # 文档默认可视化端口
$script:CbmCacheDir = Join-Path $env:USERPROFILE '.cache\codebase-memory-mcp'          # 缓存根（索引库、config.json）
$script:CbmLogDir   = Join-Path $script:CbmCacheDir 'logs'                              # daemon/UI/冲突日志目录

# ============================================================
# 通用函数
# ============================================================

# 检查 codebase-memory-mcp 是否可用
function Test-CbmInstalled {
    return [bool](Get-Command $script:CbmExe -ErrorAction SilentlyContinue)
}

# 未安装时的统一提示
function Show-NotInstalledHint {
    Write-Host "`n[提示] 未找到命令 '$script:CbmExe'，请先进入 主菜单 > 安装与验证 完成安装。" -ForegroundColor Yellow
}

# 打印菜单框架（分级位置以面包屑形式展示）
function Show-Menu {
    param(
        [string]$Breadcrumb,
        [string[]]$Items
    )
    Clear-Host
    Write-Host '==================================================' -ForegroundColor Cyan
    Write-Host '  codebase-memory-mcp 交互式管理菜单' -ForegroundColor Cyan
    if ($Breadcrumb) { Write-Host "  位置：$Breadcrumb" -ForegroundColor DarkCyan }
    Write-Host '==================================================' -ForegroundColor Cyan
    for ($i = 0; $i -lt $Items.Count; $i++) {
        Write-Host ('  {0}. {1}' -f ($i + 1), $Items[$i])
    }
    Write-Host '  0. 返回上一级'
    Write-Host '==================================================' -ForegroundColor Cyan
}

# 暂停等待用户回车
function Wait-Back {
    Write-Host ''
    Read-Host '按回车键返回菜单' | Out-Null
}

# 调用 codebase-memory-mcp cli 子命令（MCP 工具封装）
# 统一走 --args-file 传参（官方 help 支持的方式）：参数写入 JSON 文件，
# 天然规避命令行引号/空格/转义问题（Windows 含空格路径不再依赖任何引号写法）
# -Payload 哈希表键为 snake_case（与 stdin JSON 同一解析层，如 repo_path / persistence）
function Invoke-CbmCli {
    param(
        [string]$SubCommand,
        [hashtable]$Payload
    )
    $exe = Get-CbmExePath
    if (-not $exe) { Show-NotInstalledHint; return }
    if (-not (Ensure-CbmDaemon)) {
        Write-Host ' [提示] daemon 未能拉起，无法执行 cli 调用。' -ForegroundColor Yellow
        return
    }

    $argList = @('cli', $SubCommand)
    $argFile = $null
    if ($Payload) {
        $json = $Payload | ConvertTo-Json -Depth 5
        # args-file 写入 Temp：成功后立即删除；失败时保留，供复制命令手动重跑（入口处另清理超 1 天的遗留）
        $argFile = Join-Path ([IO.Path]::GetTempPath()) ("cbm-menu-{0}.json" -f [guid]::NewGuid().ToString('N').Substring(0, 8))
        $json | Set-Content -LiteralPath $argFile -Encoding UTF8
        $argList += @('--args-file', $argFile)
        Write-Host ("  参数文件：{0}" -f ($json -replace "`r?`n", ' ')) -ForegroundColor DarkGray
    }
    # 回显时给含空格参数加引号，便于复制到终端手动执行（实际传参由 PowerShell 数组自动处理）
    $show = ($argList | ForEach-Object { if ($_ -match ' ') { '"{0}"' -f $_ } else { $_ } }) -join ' '
    Write-Host ("`n> {0} {1}" -f $exe, $show) -ForegroundColor DarkGray

    # stderr 与 stdout 合并后统一按行过滤 level= 噪声
    $raw = & $exe @argList 2>&1
    $exit = $LASTEXITCODE
    $lines = @($raw | ForEach-Object { "$_" })
    $text = ($lines | Where-Object { $_ -notmatch '^level=' }) -join "`n"
    if ($exit -ne 0 -or -not $text) {
        $errTail = @($lines | Where-Object { $_ -match '^error:|^codebase-memory-mcp:' } | Select-Object -Last 3)
        $errTail | ForEach-Object { Write-Host $_ -ForegroundColor Red }
        $errText = $errTail -join ' '
        if ($errText -match 'daemon could not start|could not accept this client|daemon-backed CLI execution failed|daemon session context was rejected') {
            Write-Host ' [提示] CBM daemon 无法接受客户端。常见处置：' -ForegroundColor Yellow
            Write-Host '   1. 结束残留进程后重试：Get-Process codebase-memory-mcp | Stop-Process -Force' -ForegroundColor Yellow
            Write-Host '   2. 释放内存（daemon 预算为 RAM 的 25%，空闲不足会卡 30 秒准入超时）' -ForegroundColor Yellow
            Write-Host '   3. 检查火绒 HipsDaemon 等安全软件是否拦截，详见 daemon 日志：' -ForegroundColor Yellow
            Write-Host "      $env:USERPROFILE\.cache\codebase-memory-mcp\logs\cbm-daemon.log" -ForegroundColor Yellow
        }
        Write-Host ' [提示] 手动重跑时直接复制上方以 > 开头的整行命令（参数已写入 args-file 文件并保留），保持原样即可。' -ForegroundColor Yellow
    } else {
        $text
        # 调用成功，args-file 已无用途，立即清理
        if ($argFile) { Remove-Item -LiteralPath $argFile -Force -ErrorAction SilentlyContinue }
    }
    Write-Host "`n（退出码：$exit）" -ForegroundColor DarkGray
}

# 调用 codebase-memory-mcp 原生子命令（如 --version / config set / --ui）
function Invoke-CbmArgs {
    param([string[]]$CbmArgs)
    if (-not (Test-CbmInstalled)) { Show-NotInstalledHint; return }

    Write-Host ("`n> {0} {1}" -f $script:CbmExe, ($CbmArgs -join ' ')) -ForegroundColor DarkGray
    & $script:CbmExe @CbmArgs
    Write-Host "`n（退出码：$LASTEXITCODE）" -ForegroundColor DarkGray
}

# 确保常驻热 daemon 在运行（官方 hint 建议：daemon start 常驻，免去每次 cli 调用临时拉起 daemon 的启动开销）
# daemon 内存预算按物理内存 25% 硬编码，空闲内存不足时进程在但准入仍会 30 秒超时
function Ensure-CbmDaemon {
    if (Get-Process codebase-memory-mcp -ErrorAction SilentlyContinue) { return $true }
    $exe = Get-CbmExePath
    if (-not $exe) { return $false }
    Write-Host ">> $exe daemon start（后台拉起常驻热 daemon）" -ForegroundColor DarkGray
    Start-Process -FilePath $exe -ArgumentList 'daemon', 'start' -WindowStyle Hidden | Out-Null
    foreach ($i in 1..60) {
        Start-Sleep -Milliseconds 500
        if (Get-Process codebase-memory-mcp -ErrorAction SilentlyContinue) { return $true }
    }
    return $false
}

# 交互选择项目：优先从已索引列表选择，daemon 不可用时手动输入（精简自 cbm.ps1）
# 0.10.4 的 search_graph / trace_path / get_architecture / query_graph / detect_changes 均以 project 名为必填参数
function Select-CbmProject {
    $names = @()
    $exe = Get-CbmExePath
    if ($exe -and (Ensure-CbmDaemon)) {
        # 与 Invoke-CbmCli 同款：stderr 合并后过滤 level= 噪声，避免文件重定向（list_projects 无参数，无引号问题域）
        $raw = '{}' | & $exe cli --json list_projects 2>&1
        $exit = $LASTEXITCODE
        $jsonText = (@($raw | ForEach-Object { "$_" }) | Where-Object { $_ -notmatch '^level=' }) -join "`n"
        if ($exit -eq 0 -and $jsonText) {
            $r = $jsonText.Trim() | ConvertFrom-Json -Depth 30
            $items = $r.result.structuredContent.projects
            if (-not $items -and $r.projects) { $items = $r.projects }
            if ($items) {
                $names = @($items | ForEach-Object {
                    if ($_.PSObject.Properties['name']) { $_.name } else { "$_" }
                })
            }
        }
    }
    if ($names.Count) {
        Write-Host "`n已索引项目：" -ForegroundColor DarkGray
        for ($i = 0; $i -lt $names.Count; $i++) {
            Write-Host ("  [{0}] {1}" -f ($i + 1), $names[$i])
        }
        $sel = Read-Host '选择编号（直接回车 = 手动输入项目名）'
        if ($sel -match '^\d+$' -and [int]$sel -ge 1 -and [int]$sel -le $names.Count) {
            return $names[[int]$sel - 1]
        }
        if ($sel) { return $sel.Trim() }
    }
    return (Read-Host '请输入项目名')
}

# 请求输入项目路径，返回规范化的绝对路径；无效时返回 $null
function Get-RepoPath {
    param([string]$Hint = '请输入项目根目录的绝对路径（例如 D:\work\my-repo，支持拖入文件夹）')
    $raw = Read-Host $Hint
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }

    # 去除拖拽路径可能带上的引号
    $raw = $raw.Trim().Trim('"')

    # 展开 ~ 与环境变量
    $raw = [Environment]::ExpandEnvironmentVariables($raw)
    if ($raw.StartsWith('~')) {
        $rest = $raw.Substring(1).TrimStart('\', '/')
        $raw = if ($rest) { Join-Path $HOME $rest } else { $HOME }
    }

    if (-not (Test-Path -LiteralPath $raw -PathType Container)) {
        Write-Host "[错误] 路径不存在或不是目录：$raw" -ForegroundColor Red
        return $null
    }
    return (Resolve-Path -LiteralPath $raw).Path
}

# 请求输入非空字符串
function Read-NonEmpty {
    param([string]$Prompt)
    while ($true) {
        $v = Read-Host $Prompt
        if (-not [string]::IsNullOrWhiteSpace($v)) { return $v.Trim() }
        Write-Host '[提示] 输入不能为空。' -ForegroundColor Yellow
    }
}

# 请求输入整数（带范围校验，直接回车使用默认值）
function Read-Int {
    param([string]$Prompt, [int]$Default, [int]$Min, [int]$Max)
    while ($true) {
        $raw = (Read-Host "$Prompt（直接回车 = $Default）").Trim()
        if ([string]::IsNullOrWhiteSpace($raw)) { return $Default }
        $n = 0
        if ([int]::TryParse($raw, [ref]$n) -and $n -ge $Min -and $n -le $Max) { return $n }
        Write-Host "[提示] 请输入 $Min - $Max 之间的整数。" -ForegroundColor Yellow
    }
}

# ============================================================
# 一级菜单 1：安装与验证
# ============================================================
function Show-InstallMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 安装与验证' -Items @(
            'Windows 安装（官方 install.ps1，建议管理员运行）'
            '设置当前会话执行策略为 Bypass'
            'npm 全局安装'
            'pip 安装'
            '检查版本（--version）'
            '查看已索引项目（list_projects）'
        )
        switch (Read-Host '请选择') {
            '1' {
                Install-CbmWindows
                Wait-Back
            }
            '2' {
                Write-Host "`n> Set-ExecutionPolicy -Scope Process Bypass" -ForegroundColor DarkGray
                Set-ExecutionPolicy -Scope Process Bypass -Force
                Write-Host "当前会话执行策略：$(Get-ExecutionPolicy -Scope Process)"
                Wait-Back
            }
            '3' {
                Write-Host "`n> npm install -g codebase-memory-mcp" -ForegroundColor DarkGray
                npm install -g codebase-memory-mcp
                Write-Host "`n（退出码：$LASTEXITCODE）" -ForegroundColor DarkGray
                Wait-Back
            }
            '4' {
                Write-Host "`n> pip install codebase-memory-mcp" -ForegroundColor DarkGray
                pip install codebase-memory-mcp
                Write-Host "`n（退出码：$LASTEXITCODE）" -ForegroundColor DarkGray
                Wait-Back
            }
            '5' {
                Invoke-CbmArgs -CbmArgs @('--version')
                Wait-Back
            }
            '6' {
                Invoke-CbmCli -SubCommand 'list_projects'
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# Windows 官方安装脚本（文档 2.2 节）
function Install-CbmWindows {
    # 管理员权限检测
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Host '[提示] 当前不是管理员会话，安装可能因权限不足失败。建议以管理员身份重新运行本脚本。' -ForegroundColor Yellow
        $go = Read-Host '仍要继续吗？(y/N)'
        if ($go -notin @('y', 'Y')) { return }
    }

    # 下载安装脚本到临时目录，避免污染当前目录
    $installer = Join-Path ([System.IO.Path]::GetTempPath()) 'cbm-install.ps1'
    Write-Host "`n正在下载安装脚本到 $installer ..."
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.ps1' -OutFile $installer
    if (-not (Test-Path -LiteralPath $installer)) {
        Write-Host '[错误] 下载失败，请检查网络后重试。' -ForegroundColor Red
        return
    }

    Write-Host '开始安装（如提示执行策略限制，请先执行本菜单第 2 项）...'
    & $installer
    Write-Host "`n安装完成后建议重新打开终端，让 PATH 生效。"
}

# ============================================================
# 一级菜单 2：AI 工具配置
# ============================================================
function Show-AiToolMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > AI 工具配置' -Items @(
            'Claude Code 全局配置（所有项目共享记忆）'
            'Claude Code 仅当前项目配置'
            '查看 Claude Code 已配置的 MCP（claude mcp list）'
            '显示 .mcp.json 项目配置模板（通用）'
            '显示 Claude Desktop 配置说明'
        )
        switch (Read-Host '请选择') {
            '1' { Invoke-ClaudeAdd -ScopeUser; Wait-Back }
            '2' { Invoke-ClaudeAdd;            Wait-Back }
            '3' {
                if (Get-Command claude -ErrorAction SilentlyContinue) {
                    Write-Host "`n> claude mcp list" -ForegroundColor DarkGray
                    claude mcp list
                    Write-Host "`n（退出码：$LASTEXITCODE）" -ForegroundColor DarkGray
                } else {
                    Write-Host '[提示] 未找到 claude 命令，请确认已安装 Claude Code。' -ForegroundColor Yellow
                }
                Wait-Back
            }
            '4' {
                Write-Host "`n在项目根目录创建 .mcp.json，内容如下（二进制不在 PATH 时 command 需写绝对路径）：`n"
                Write-Host @'
{
  "mcpServers": {
    "codebase-memory": {
      "command": "codebase-memory-mcp"
    }
  }
}
'@
                Wait-Back
            }
            '5' {
                $cfgPath = Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
                Write-Host "`nWindows 配置文件路径：$cfgPath"
                Write-Host '配置内容模板：'
                Write-Host @'
{
  "mcpServers": {
    "codebase-memory": {
      "command": "codebase-memory-mcp",
      "args": []
    }
  }
}
'@
                Write-Host "`n配置完成后需重启 Claude Desktop 生效。"
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# 执行 claude mcp add 配置命令
function Invoke-ClaudeAdd {
    param([switch]$ScopeUser)
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host '[提示] 未找到 claude 命令，请确认已安装 Claude Code。' -ForegroundColor Yellow
        return
    }
    if ($ScopeUser) {
        Write-Host "`n> claude mcp add --scope user codebase-memory -- codebase-memory-mcp" -ForegroundColor DarkGray
        claude mcp add --scope user codebase-memory -- codebase-memory-mcp
    } else {
        Write-Host "`n> claude mcp add codebase-memory -- codebase-memory-mcp" -ForegroundColor DarkGray
        claude mcp add codebase-memory -- codebase-memory-mcp
    }
    Write-Host "`n（退出码：$LASTEXITCODE）" -ForegroundColor DarkGray
    Write-Host '[提示] 可通过本菜单第 3 项验证配置结果。'
}

# ============================================================
# 一级菜单 3：项目索引
# ============================================================
function Show-IndexMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 项目索引' -Items @(
            '索引 / 重建指定项目（index_repository，首次必做）'
            '查看已索引项目（list_projects）'
            '索引状态（index_status）'
            '索引覆盖检查（check_index_coverage）'
            '删除项目索引（delete_project）'
        )
        switch (Read-Host '请选择') {
            '1' {
                $repo = Get-RepoPath
                if ($repo) {
                    # 索引模式（官方 --help：全模式均做 LSP 调用解析，差异在文件范围与相似性/语义边）
                    $payload = @{ repo_path = $repo }
                    $sel = Read-Host '索引模式（1=full 全量 2=moderate 均衡 3=fast 快速 4=跨项目，回车=工具默认）'
                    if     ($sel -eq '1') { $payload.mode = 'full' }
                    elseif ($sel -eq '2') { $payload.mode = 'moderate' }
                    elseif ($sel -eq '3') { $payload.mode = 'fast' }
                    elseif ($sel -eq '4') {
                        $payload.mode = 'cross-repo-intelligence'
                        $tp = Read-Host '跨项目搜索目标（回车=全部已索引项目）'
                        $payload.target_projects = if ($tp) { $tp } else { '["*"]' }
                    }
                    Invoke-CbmCli -SubCommand 'index_repository' -Payload $payload
                }
                Wait-Back
            }
            '2' {
                Invoke-CbmCli -SubCommand 'list_projects'
                Wait-Back
            }
            '3' {
                $proj = Select-CbmProject
                if ($proj) {
                    $payload = @{ project = $proj }
                    if ((Read-Host '附 git 上下文详情? y/N') -match '^(y|true)$') { $payload.verbose = $true }
                    Invoke-CbmCli -SubCommand 'index_status' -Payload $payload
                }
                Wait-Back
            }
            '4' {
                $proj = Select-CbmProject
                if ($proj) {
                    $scopes = Read-Host '检查范围前缀（逗号分隔，.=根；直接回车 = .）'
                    # scopes 为前缀数组：空输入按整库根处理
                    $scopeArr = @($scopes -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
                    if (-not $scopeArr.Count) { $scopeArr = @('.') }
                    Invoke-CbmCli -SubCommand 'check_index_coverage' -Payload @{ project = $proj; scopes = $scopeArr }
                }
                Wait-Back
            }
            '5' {
                $proj = Select-CbmProject
                if ($proj) {
                    if ((Read-Host "确认删除项目 '$proj' 的索引? (y/N)") -match '^(y|yes)$') {
                        # 只删缓存中的知识图，不影响源码
                        Invoke-CbmCli -SubCommand 'delete_project' -Payload @{ project = $proj }
                    } else {
                        Write-Host ' 已取消。'
                    }
                }
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# ============================================================
# 一级菜单 4：图谱查询
# ============================================================
function Show-QueryMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 图谱查询' -Items @(
            '搜索函数或类（search_graph）'
            '查看函数调用链（trace_path）'
            '项目架构概览（get_architecture）'
            '执行 Cypher 图查询（query_graph）'
            '变更影响分析（detect_changes，0.10.4 替代旧版死代码检测）'
            '图 Schema（get_graph_schema）'
            '代码搜索（search_code）'
            '查看代码片段（get_code_snippet）'
        )
        switch (Read-Host '请选择') {
            '1' {
                $proj = Select-CbmProject
                if ($proj) {
                    $pattern = Read-NonEmpty '请输入符号名匹配模式（正则，如 .*Handler.*）'
                    $label = $null
                    $sel = Read-Host '请选择节点类型（1=Function 2=Class 3=自定义，回车=不限）'
                    if     ($sel -eq '1') { $label = 'Function' }
                    elseif ($sel -eq '2') { $label = 'Class' }
                    elseif ($sel -eq '3') { $label = Read-NonEmpty '请输入自定义节点类型' }
                    $payload = @{ project = $proj; name_pattern = $pattern }
                    if ($label) { $payload.label = $label }
                    Invoke-CbmCli -SubCommand 'search_graph' -Payload $payload
                }
                Wait-Back
            }
            '2' {
                $proj = Select-CbmProject
                if ($proj) {
                    $fn = Read-NonEmpty '请输入函数名（如 createUser）'
                    # 0.10.4 仅支持 inbound/outbound，不再支持 both
                    $sel = Read-Host '请选择方向（1=outbound 它调用了谁，回车=inbound 谁调用了它）'
                    if ($sel -eq '1') { $direction = 'outbound' } else { $direction = 'inbound' }
                    Invoke-CbmCli -SubCommand 'trace_path' -Payload @{ function_name = $fn; project = $proj; direction = $direction }
                }
                Wait-Back
            }
            '3' {
                $proj = Select-CbmProject
                if ($proj) {
                    Invoke-CbmCli -SubCommand 'get_architecture' -Payload @{ project = $proj }
                }
                Wait-Back
            }
            '4' {
                $proj = Select-CbmProject
                if ($proj) {
                    $cypher = Read-NonEmpty '请输入 Cypher 查询（如 MATCH (f:Function) RETURN f.name LIMIT 5）'
                    Invoke-CbmCli -SubCommand 'query_graph' -Payload @{ project = $proj; query = $cypher }
                }
                Wait-Back
            }
            '5' {
                # 0.10.4 已移除 dead_code_detection 工具，改用 detect_changes
                $proj = Select-CbmProject
                if ($proj) {
                    $since = Read-Host '对比基准 git ref（如 HEAD~5、v0.5.0；直接回车 = 工作区未提交变更）'
                    $payload = @{ project = $proj }
                    if ($since) { $payload.since = $since }
                    Invoke-CbmCli -SubCommand 'detect_changes' -Payload $payload
                }
                Wait-Back
            }
            '6' {
                $proj = Select-CbmProject
                if ($proj) {
                    Invoke-CbmCli -SubCommand 'get_graph_schema' -Payload @{ project = $proj }
                }
                Wait-Back
            }
            '7' {
                $pattern = Read-NonEmpty '请输入搜索内容（正则或关键词）'
                if ($pattern) {
                    $proj = Select-CbmProject
                    if ($proj) {
                        $payload = @{ pattern = $pattern; project = $proj }
                        $fp = Read-Host '文件 glob（如 *.go；直接回车 = 全部）'
                        if ($fp) { $payload.file_pattern = $fp }
                        $mode = Read-Host '输出模式 compact(签名)/full(含源码)/files(仅文件)，回车 = compact'
                        if ($mode) { $payload.mode = $mode }
                        Invoke-CbmCli -SubCommand 'search_code' -Payload $payload
                    }
                }
                Wait-Back
            }
            '8' {
                $proj = Select-CbmProject
                if ($proj) {
                    Write-Host ' qualified_name 来自 search_graph 结果（也支持短函数名）。' -ForegroundColor DarkGray
                    $qn = Read-NonEmpty '请输入 qualified_name'
                    Invoke-CbmCli -SubCommand 'get_code_snippet' -Payload @{ project = $proj; qualified_name = $qn }
                }
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# ============================================================
# 一级菜单 5：配置管理
# ============================================================
function Show-ConfigMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 配置管理' -Items @(
            '开启自动索引（auto_index = true，推荐）'
            '关闭自动索引（auto_index = false，超大仓库适用）'
            '设置自动索引文件数上限（auto_index_limit）'
            '查看全部配置（config list）'
            '自定义配置查看/设置/重置（config get / set / reset）'
        )
        switch (Read-Host '请选择') {
            '1' {
                Invoke-CbmArgs -CbmArgs @('config', 'set', 'auto_index', 'true')
                Wait-Back
            }
            '2' {
                Invoke-CbmArgs -CbmArgs @('config', 'set', 'auto_index', 'false')
                Wait-Back
            }
            '3' {
                $limit = Read-Int -Prompt '请输入文件数上限' -Default 50000 -Min 1 -Max 10000000
                Invoke-CbmArgs -CbmArgs @('config', 'set', 'auto_index_limit', "$limit")
                Wait-Back
            }
            '4' {
                Invoke-CbmArgs -CbmArgs @('config', 'list')
                Wait-Back
            }
            '5' {
                $sub = Read-Host '子命令（get=查看 set=设置 reset=重置，回车=list）'
                if ($sub -notin @('get', 'set', 'reset')) { $sub = 'list' }
                $key = Read-Host '配置键'
                $cfgArgs = @('config', $sub)
                if ($key) { $cfgArgs += $key }
                if ($sub -eq 'set' -and $key) {
                    $val = Read-Host '配置值（如 true / 50000）'
                    if (-not $val) { $val = Read-NonEmpty '配置值不能为空，请重新输入' }
                    $cfgArgs += $val
                }
                Invoke-CbmArgs -CbmArgs $cfgArgs
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# ============================================================
# 一级菜单 6：可视化 UI
# ============================================================
function Show-UiMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 可视化 UI' -Items @(
            "启动 3D 知识图谱 UI（默认端口 $($script:CbmUiPort)，后台常驻 + 自动开浏览器）"
            '启动 3D 知识图谱 UI（自定义端口）'
            '结束 UI（停止常驻 daemon）'
        )
        switch (Read-Host '请选择') {
            '1' {
                Start-CbmUi -Port $script:CbmUiPort
                Wait-Back
            }
            '2' {
                $port = Read-Int -Prompt '请输入端口号' -Default $script:CbmUiPort -Min 1 -Max 65535
                Start-CbmUi -Port $port
                Wait-Back
            }
            '3' {
                Stop-CbmUi
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# 探测 CBM 可执行文件：PATH 优先，其次已知安装位置（与 cbm.ps1 的探测逻辑一致）
function Get-CbmExePath {
    $fromPath = Get-Command $script:CbmExe -ErrorAction SilentlyContinue
    if ($fromPath) { return $fromPath.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\codebase-memory-mcp\codebase-memory-mcp.exe",
        "$env:USERPROFILE\.cache\codebase-memory-mcp\bin\codebase-memory-mcp.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

# 后台启动可视化 UI：必要时拉起常驻 daemon，轮询端口就绪后自动打开浏览器
# （参考 cbm.ps1 的 Cbm-Ui：daemon start 仅接受 --open/--port，--ui 不是 daemon 子命令参数，
#   ui_enabled 为持久化配置，对应全局 --ui=true）
function Start-CbmUi {
    param([int]$Port)
    $exe = Get-CbmExePath
    if (-not $exe) { Show-NotInstalledHint; return }
    $url = "http://localhost:$Port"

    # 端口已监听 = daemon 已在运行，直接打开浏览器
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "`n daemon 已在运行（端口 $Port），直接打开浏览器。"
        Start-Process $url
        return
    }

    # 内存预检：daemon 内存预算按物理内存 25% 硬编码，空闲不足会卡满 30 秒准入超时
    $os = Get-CimInstance Win32_OperatingSystem
    $freeMB   = [math]::Round($os.FreePhysicalMemory / 1KB)
    $budgetMB = [math]::Round($os.TotalVisibleMemorySize / 1KB * 0.25)
    Write-Host "`n 内存预检：空闲 ${freeMB}MB / daemon 预算约 ${budgetMB}MB（RAM 的 25%）"
    if ($freeMB -lt $budgetMB) {
        Write-Host ' [!!] 空闲内存低于 daemon 预算，启动会卡 30 秒准入超时。' -ForegroundColor Yellow
        Write-Host '      建议先释放内存（wsl --shutdown、关闭浏览器/大应用）再试。' -ForegroundColor Yellow
        $ok = Read-Host ' 仍要继续启动? (y/N)'
        if ($ok -notmatch '^(y|yes)$') { Write-Host ' 已取消。'; return }
    }

    # ui_enabled=true 对应全局 --ui=true（持久化配置，幂等设置）
    Write-Host "`n> $exe config set ui_enabled true" -ForegroundColor DarkGray
    & $exe config set ui_enabled true | Out-Null

    # 后台常驻启动 daemon（隐藏窗口，不阻塞菜单）
    Write-Host ">> $exe daemon start --port=$Port（后台常驻，最长等待 30 秒就绪）" -ForegroundColor DarkGray
    Start-Process -FilePath $exe -ArgumentList 'daemon', 'start', "--port=$Port" -WindowStyle Hidden | Out-Null

    # 轮询 UI 端口就绪（30 秒与 daemon 准入超时对齐）
    $ready = $false
    foreach ($i in 1..60) {
        Start-Sleep -Milliseconds 500
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { $ready = $true; break }
    }
    if ($ready) {
        Write-Host " [OK] UI 就绪: $url" -ForegroundColor Green
        Start-Process $url
    } else {
        Write-Host ' [!!] 30 秒内 UI 端口未就绪（daemon 启动失败或准入超时）。' -ForegroundColor Red
        Write-Host '      常见原因：内存不足、安全软件拦截 daemon，可运行 cbm.ps1 doctor 排查。' -ForegroundColor Yellow
    }
}

# 结束 UI：停止常驻 daemon
function Stop-CbmUi {
    $exe = Get-CbmExePath
    if (-not $exe) { Show-NotInstalledHint; return }
    Write-Host "`n> $exe daemon stop" -ForegroundColor DarkGray
    (& $exe daemon stop 2>&1 | Out-String).Trim() | Write-Host
}

# ============================================================
# 一级菜单 7：快照与 Git 备份
# ============================================================
# 刷新已索引项目的索引并重建团队快照（移植自 cbm.ps1 Cbm-RefreshIndex）
# 显式传回 list_projects 记录的根路径与项目名（name），防止路径推导名漂移导致重建为新项目
function Refresh-CbmSnapshot {
    $exe = Get-CbmExePath
    if (-not $exe) { Show-NotInstalledHint; return }
    if (-not (Ensure-CbmDaemon)) { Write-Host ' [提示] daemon 未能拉起。' -ForegroundColor Yellow; return }

    $raw = '{}' | & $exe cli --json list_projects 2>&1
    $exit = $LASTEXITCODE
    $jsonText = (@($raw | ForEach-Object { "$_" }) | Where-Object { $_ -notmatch '^level=' }) -join "`n"
    $items = @()
    if ($exit -eq 0 -and $jsonText) {
        $r = $jsonText.Trim() | ConvertFrom-Json -Depth 30
        $items = @($r.result.structuredContent.projects)
        if (-not $items -and $r.projects) { $items = @($r.projects) }
    }
    $items = @($items | Where-Object { $_ })
    if (-not $items.Count) { Write-Host ' 无已索引项目，先在「项目索引」中索引仓库。' -ForegroundColor Yellow; return }

    Write-Host "`n已索引项目："
    for ($i = 0; $i -lt $items.Count; $i++) {
        Write-Host ("  [{0}] {1}  （{2}）" -f ($i + 1), $items[$i].name, $items[$i].root_path)
    }
    $sel = Read-Host '选择编号或项目名（直接回车 = 全部项目）'
    if ($sel -match '^\d+$' -and [int]$sel -ge 1 -and [int]$sel -le $items.Count) {
        $targets = @($items[[int]$sel - 1])
    } elseif ($sel) {
        $targets = @($items | Where-Object { $_.name -eq $sel.Trim() })
        if (-not $targets.Count) { Write-Host " 未找到项目: $sel" -ForegroundColor Red; return }
    } else {
        $targets = $items
    }

    $mode = $null
    $m = Read-Host '索引模式（1=full 2=moderate 3=fast，回车=工具默认）'
    if     ($m -eq '1') { $mode = 'full' }
    elseif ($m -eq '2') { $mode = 'moderate' }
    elseif ($m -eq '3') { $mode = 'fast' }

    foreach ($it in $targets) {
        Write-Host "`n===== 刷新 $($it.name) （$($it.root_path)）=====" -ForegroundColor Cyan
        $p = @{ repo_path = "$($it.root_path)"; name = "$($it.name)"; persistence = $true }
        if ($mode) { $p.mode = $mode }
        Invoke-CbmCli -SubCommand 'index_repository' -Payload $p
    }
    Write-Host "`n[完成] 已刷新 $($targets.Count) 个项目。" -ForegroundColor Green
}

# 环境自检：可执行文件、缓存、config、残留进程/安全软件、daemon 连接、冲突日志（移植自 cbm.ps1 Cbm-Doctor）
# -Deep 追加 daemon 生命周期观察（逐秒采样，判定 spawn 被拦还是启动后自退）
function Invoke-CbmDoctor {
    param([switch]$Deep)
    $issues = [System.Collections.Generic.List[string]]::new()

    # 1) 可执行文件
    $exe = Get-CbmExePath
    if ($exe) {
        $v = (& $exe --version 2>$null | Out-String).Trim()
        Write-Host " [OK] 可执行文件: $exe"
        Write-Host "      版本: $v"
    } else {
        $issues.Add('未找到 codebase-memory-mcp.exe（PATH 与默认安装位置均无）')
        Write-Host ' [!!] 可执行文件未找到' -ForegroundColor Red
    }

    # 2) 缓存与项目索引
    if (Test-Path $script:CbmCacheDir) {
        $dbs = Get-ChildItem $script:CbmCacheDir -Filter *.db -File -ErrorAction SilentlyContinue
        $sizeMB = [math]::Round((($dbs | Measure-Object Length -Sum).Sum) / 1MB, 1)
        Write-Host " [OK] 缓存目录: $script:CbmCacheDir（索引库 $(@($dbs).Count) 个，共 $sizeMB MB）"
        $dbs | ForEach-Object { Write-Host "      - $($_.Name)  $([math]::Round($_.Length/1MB,1)) MB" }
        $stale = Get-ChildItem $script:CbmCacheDir -Filter '*.stage.*' -File -ErrorAction SilentlyContinue
        if ($stale) { Write-Host " [提示] 存在 $(@($stale).Count) 个中断索引的 .stage 临时文件（可安全清理）" -ForegroundColor Yellow }
    } else {
        $issues.Add("缓存目录不存在: $script:CbmCacheDir")
        Write-Host ' [!!] 缓存目录不存在' -ForegroundColor Red
    }

    # 3) config
    $cfg = Join-Path $script:CbmCacheDir 'config.json'
    if (Test-Path $cfg) {
        Write-Host " [OK] config.json: $((Get-Content $cfg -Raw).Trim() -replace '\s+', ' ')"
    }

    # 3.5) 残留进程与安全软件（daemon 准入被拒的常见原因）
    $procs = Get-Process codebase-memory-mcp -ErrorAction SilentlyContinue
    if ($procs) {
        Write-Host " [提示] 检测到 $(@($procs).Count) 个常驻 CBM 进程（可能占用会话准入）" -ForegroundColor Yellow
        $procs | ForEach-Object { Write-Host "      - pid $($_.Id)  启动于 $($_.StartTime)" }
        Write-Host "      遇 'could not accept this client / session rejected' 时先全部结束再重试" -ForegroundColor DarkGray
    } else {
        Write-Host ' [OK] 无残留 CBM 进程'
    }
    $hips = Get-Service HipsDaemon -ErrorAction SilentlyContinue
    if ($hips -and $hips.Status -eq 'Running') {
        Write-Host ' [提示] 火绒 HipsDaemon 服务运行中（行为防护可能拦截 daemon 准入/IPC）' -ForegroundColor Yellow
    }

    # 4) daemon 连接（真实短调用，计时）
    if ($exe) {
        Write-Host ' [..] 探测 daemon 连接（cli list_projects，最多 35 秒）...' -ForegroundColor DarkGray
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $null = '{}' | & $exe cli --json list_projects 2>&1
        $sw.Stop()
        if ($LASTEXITCODE -eq 0) {
            Write-Host " [OK] daemon 正常，耗时 $([int]$sw.Elapsed.TotalSeconds) 秒"
        } else {
            $issues.Add("daemon 无法启动（$([int]$sw.Elapsed.TotalSeconds)s 超时）")
            Write-Host ' [!!] daemon 无法启动' -ForegroundColor Red
        }
    }

    # 5) Deep：daemon 启动即死观察
    if ($Deep -and $exe) {
        Write-Host "`n===== Deep 诊断：daemon 进程生命周期观察 =====" -ForegroundColor Cyan
        $outF = [IO.Path]::GetTempFileName(); $errF = [IO.Path]::GetTempFileName()
        $proc = Start-Process -FilePath $exe -ArgumentList 'cli', 'list_projects' -PassThru -NoNewWindow -RedirectStandardOutput $outF -RedirectStandardError $errF
        $trace = @()
        $deadline = (Get-Date).AddSeconds(40)
        while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
            $others = Get-Process codebase-memory-mcp -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $proc.Id }
            if ($others) { $trace += "t+$([int]((Get-Date) - $proc.StartTime).TotalSeconds)s PID: $($others.Id -join ',')" }
            Start-Sleep -Milliseconds 800
        }
        if (-not $proc.HasExited) { $proc.Kill($true) }
        $proc.Dispose()
        if ($trace) {
            Write-Host ' daemon 子进程出现过：'
            $trace | Select-Object -First 8 | ForEach-Object { Write-Host "   $_" }
            Write-Host ' 判定：进程被 spawn 后很快消失 = 启动即死（典型：安全软件击杀）。' -ForegroundColor Yellow
        } else {
            Write-Host ' 40 秒内 daemon 子进程从未出现 = spawn 层被拦。' -ForegroundColor Yellow
        }
        Remove-Item $outF, $errF -Force -ErrorAction SilentlyContinue
    }

    # 6) 日志摘要
    if (Test-Path $script:CbmLogDir) {
        $conf = Join-Path $script:CbmLogDir 'daemon-conflicts.ndjson'
        if (Test-Path $conf) {
            Write-Host ' [提示] 存在 daemon 冲突记录（版本/build/缓存根不一致）：' -ForegroundColor Yellow
            Get-Content $conf -Tail 3 | ForEach-Object { Write-Host "   $_" }
        }
    }

    # 汇总
    Write-Host "`n===== 自检结论 =====" -ForegroundColor Cyan
    if ($issues.Count -eq 0) {
        Write-Host ' 全部通过，CBM 可用。' -ForegroundColor Green
    } else {
        $issues | ForEach-Object { Write-Host " 问题: $_" -ForegroundColor Red }
        Write-Host ' 处置建议：' -ForegroundColor Yellow
        Write-Host '   1. 检查安全软件行为防护（火绒 HipsDaemon、360 等），将 codebase-memory-mcp.exe 加入信任/放行；' -ForegroundColor Yellow
        Write-Host '   2. 释放内存（daemon 预算为 RAM 的 25%，空闲不足会卡 30 秒准入超时）；' -ForegroundColor Yellow
        Write-Host '   3. 用「维护与诊断 > 查看日志」深入定位。' -ForegroundColor Yellow
    }
}

# 查看 daemon / conflicts / ui 日志（移植自 cbm.ps1 Cbm-Logs）
function Show-CbmLogs {
    Write-Host "`n日志目录：$script:CbmLogDir"
    if (-not (Test-Path $script:CbmLogDir)) { Write-Host ' 日志目录不存在（daemon 从未启动过）。'; return }
    Write-Host ' 日志文件：' -ForegroundColor DarkGray
    Get-ChildItem $script:CbmLogDir -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name, Length, LastWriteTime |
        Format-Table -AutoSize | Out-String | Write-Host
    $file = Read-Host '查看哪个? daemon / conflicts / ui（直接回车 = daemon）'
    if (-not $file) { $file = 'daemon' }
    $name = switch ($file) {
        'daemon'    { 'cbm-daemon.log' }
        'conflicts' { 'daemon-conflicts.ndjson' }
        'ui'        { 'ui.log' }
        default     { $file }
    }
    $path = Join-Path $script:CbmLogDir $name
    if (-not (Test-Path $path)) { Write-Host " 文件不存在: $name（通常 = 无此事件发生）"; return }
    Write-Host "--- $name 尾部 30 行 ---" -ForegroundColor DarkGray
    Get-Content $path -Tail 30
}

# 查看单个工具的官方参数说明（cli <tool> --help，本地解析不走 daemon）
function Show-CbmToolHelp {
    $exe = Get-CbmExePath
    if (-not $exe) { Show-NotInstalledHint; return }
    Write-Host ' 全部工具: index_repository search_graph query_graph trace_path get_code_snippet' -ForegroundColor DarkGray
    Write-Host '           get_graph_schema get_architecture search_code list_projects delete_project' -ForegroundColor DarkGray
    Write-Host '           index_status check_index_coverage detect_changes manage_adr ingest_traces' -ForegroundColor DarkGray
    $tool = Read-Host '工具名（直接回车退出）'
    if (-not $tool) { return }
    Write-Host "`n> $exe cli $tool --help" -ForegroundColor DarkGray
    & $exe cli $tool --help 2>&1
}

# ============================================================
# 一级菜单 9：维护与诊断
# ============================================================
function Show-MaintMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 维护与诊断' -Items @(
            '环境自检（可执行文件 / 缓存 / config / daemon 连接）'
            '环境自检 - Deep（观察 daemon 是否启动即死）'
            '查看日志（daemon / conflicts / ui）'
            '工具参数帮助（cli <tool> --help）'
            'ADR 管理（manage_adr）'
            '摄取追踪（ingest_traces）'
        )
        switch (Read-Host '请选择') {
            '1' { Invoke-CbmDoctor; Wait-Back }
            '2' { Invoke-CbmDoctor -Deep; Wait-Back }
            '3' { Show-CbmLogs; Wait-Back }
            '4' { Show-CbmToolHelp; Wait-Back }
            '5' {
                $proj = Select-CbmProject
                if ($proj) {
                    $payload = @{ project = $proj }
                    $mode = Read-Host 'mode（直接回车 = 列出现有 ADR；update = 替换文档）'
                    if ($mode) {
                        $payload.mode = $mode
                        if ($mode -eq 'update') {
                            Write-Host ' 输入完整 ADR 文档（支持多行，结束行单独输入 EOF）：' -ForegroundColor DarkGray
                            $lines = @()
                            while ($true) { $line = Read-Host; if ($line -eq 'EOF') { break }; $lines += $line }
                            $payload.content = ($lines -join "`n")
                        }
                    }
                    Invoke-CbmCli -SubCommand 'manage_adr' -Payload $payload
                }
                Wait-Back
            }
            '6' {
                $proj = Select-CbmProject
                if ($proj) {
                    Write-Host ' 粘贴 traces JSON（数组，或含 traces 键的对象；结束行单独输入 EOF）：' -ForegroundColor DarkGray
                    $lines = @()
                    while ($true) { $line = Read-Host; if ($line -eq 'EOF') { break }; $lines += $line }
                    $rawInput = $lines -join "`n"
                    if (-not $rawInput.Trim()) {
                        Write-Host ' 已取消。'
                    } else {
                        try {
                            # traces 为必填数组参数，仅能经 JSON 输入（用户输入边界校验）
                            $parsed = $rawInput | ConvertFrom-Json -Depth 20
                            $traces = if ($parsed.PSObject.Properties['traces']) { $parsed.traces } else { $parsed }
                            Invoke-CbmCli -SubCommand 'ingest_traces' -Payload @{ project = $proj; traces = $traces }
                        } catch {
                            Write-Host " JSON 解析失败：$($_.Exception.Message)" -ForegroundColor Red
                        }
                    }
                }
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

function Show-SnapshotMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 快照与 Git 备份' -Items @(
            '生成快照（index_repository + persistence，生成 .codebase-memory/graph.db.zst）'
            '将快照提交到 Git（git add + commit）'
            '完整流程：索引并生成快照 -> 提交 Git'
            '刷新已索引项目的快照（从列表选择，自动复用根路径与项目名）'
        )
        switch (Read-Host '请选择') {
            '1' {
                Export-CbmSnapshot
                Wait-Back
            }
            '2' {
                Commit-CbmSnapshot
                Wait-Back
            }
            '3' {
                $repo = Get-RepoPath '完整流程：请输入项目根目录绝对路径'
                if ($repo) {
                    # 第 1 步：索引并生成快照（0.10.4 中 persistence=true 一步完成，等价旧版索引+export）
                    Invoke-CbmCli -SubCommand 'index_repository' -Payload @{ repo_path = $repo; persistence = $true }
                    $snap = Join-Path $repo '.codebase-memory\graph.db.zst'
                    if (Test-Path -LiteralPath $snap) {
                        Write-Host "[完成] 快照已生成：$snap" -ForegroundColor Green
                        # 第 2 步：确认后提交 Git
                        Commit-CbmSnapshot -RepoPath $repo -AskConfirm
                    } else {
                        Write-Host '[提示] 未生成快照文件，跳过 Git 提交。' -ForegroundColor Yellow
                    }
                }
                Wait-Back
            }
            '4' {
                Refresh-CbmSnapshot
                Wait-Back
            }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# 生成团队共享快照：0.10.4 已无 export 子命令，改为 index_repository 传 persistence=true
# （在项目下生成 .codebase-memory/graph.db.zst，做法与 cbm.ps1 的 Cbm-RefreshIndex 一致）
function Export-CbmSnapshot {
    param([string]$RepoPath)
    $repo = if ($RepoPath) { $RepoPath } else { Get-RepoPath '请输入项目根目录绝对路径（快照将生成在该目录下）' }
    if (-not $repo) { return }

    Invoke-CbmCli -SubCommand 'index_repository' -Payload @{ repo_path = $repo; persistence = $true }

    $snap = Join-Path $repo '.codebase-memory\graph.db.zst'
    if (Test-Path -LiteralPath $snap) {
        Write-Host "[完成] 快照已生成：$snap" -ForegroundColor Green
    } else {
        Write-Host '[提示] 未在项目目录找到快照文件，请查看上方命令输出。' -ForegroundColor Yellow
    }
}

# 将快照提交到 Git 仓库
function Commit-CbmSnapshot {
    param([string]$RepoPath, [switch]$AskConfirm)
    $repo = if ($RepoPath) { $RepoPath } else { Get-RepoPath '请输入项目根目录绝对路径' }
    if (-not $repo) { return }

    $snap = Join-Path $repo '.codebase-memory\graph.db.zst'
    if (-not (Test-Path -LiteralPath $snap)) {
        Write-Host '[提示] 未找到快照文件，请先执行导出（本菜单第 1 项）。' -ForegroundColor Yellow
        return
    }
    if ($AskConfirm) {
        $go = Read-Host '是否将快照提交到 Git？(y/N)'
        if ($go -notin @('y', 'Y')) { return }
    }

    Push-Location -LiteralPath $repo
    try {
        Write-Host "`n> git add .codebase-memory/" -ForegroundColor DarkGray
        git add .codebase-memory/
        $msg = Read-Host '请输入提交信息（直接回车 = chore: update codebase graph snapshot）'
        if ([string]::IsNullOrWhiteSpace($msg)) { $msg = 'chore: update codebase graph snapshot' }
        Write-Host "> git commit -m `"$msg`"" -ForegroundColor DarkGray
        git commit -m $msg
        Write-Host "`n（退出码：$LASTEXITCODE）" -ForegroundColor DarkGray
    }
    finally {
        Pop-Location
    }
}

# ============================================================
# 一级菜单 8：帮助与环境变量
# ============================================================
function Show-HelpMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单 > 帮助与环境变量' -Items @(
            '环境变量说明与当前取值'
            '设置 / 清除 CBM 环境变量（4 项，支持用户级持久化）'
            '常见问题速查'
            '高频踩坑提示'
            '推荐向 AI 提问的方式'
        )
        switch (Read-Host '请选择') {
            '1' { Show-EnvInfo;      Wait-Back }
            '2' { Set-CbmEnvVars;    Wait-Back }
            '3' { Show-Faq;          Wait-Back }
            '4' { Show-Pitfalls;     Wait-Back }
            '5' { Show-PromptTips;   Wait-Back }
            '0' { return }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# 环境变量说明（文档 6.5 节）
function Show-EnvInfo {
    Write-Host "`n环境变量说明（Windows 路径）：`n"
    Write-Host '  CBM_CACHE_DIR    默认: %USERPROFILE%\.cache\codebase-memory-mcp   说明: 索引数据库存储目录'
    Write-Host '  CBM_LOG_LEVEL    默认: info                                       说明: 日志级别 debug/info/warn/error/none'
    Write-Host '  CBM_WORKERS      默认: 自动检测                                   说明: 并行索引线程数（1-256）'
    Write-Host '  CBM_DIAGNOSTICS  默认: false                                      说明: 设为 1 启用诊断日志'
    Write-Host "`n当前会话取值："
    foreach ($name in @('CBM_CACHE_DIR', 'CBM_LOG_LEVEL', 'CBM_WORKERS', 'CBM_DIAGNOSTICS')) {
        $val = [Environment]::GetEnvironmentVariable($name)
        if (-not $val) { $val = '（未设置，使用默认值）' }
        Write-Host "  $name = $val"
    }
    Write-Host "`nWindows 设置示例：`$env:CBM_CACHE_DIR = 'D:\cbm-data'"
}

# 设置 / 清除 CBM 环境变量（会话级立即生效，或用户级持久化到注册表）
function Set-CbmEnvVars {
    # 4 个变量的元数据：名称、说明、取值校验器（$null 表示不限）
    $defs = @(
        @{ Name = 'CBM_CACHE_DIR';   Desc = '索引数据库存储目录'; Validate = $null },
        @{ Name = 'CBM_LOG_LEVEL';   Desc = '日志级别'; Validate = { param($v) $v -in @('debug', 'info', 'warn', 'error', 'none') } },
        @{ Name = 'CBM_WORKERS';     Desc = '并行索引线程数'; Validate = { param($v) ($v -match '^\d+$') -and [int]$v -ge 1 -and [int]$v -le 256 } },
        @{ Name = 'CBM_DIAGNOSTICS'; Desc = '诊断日志开关'; Validate = { param($v) $v -in @('0', '1', 'true', 'false') } }
    )

    while ($true) {
        Write-Host "`n可设置项："
        for ($i = 0; $i -lt $defs.Count; $i++) {
            $cur = [Environment]::GetEnvironmentVariable($defs[$i].Name)
            $usr = [Environment]::GetEnvironmentVariable($defs[$i].Name, 'User')
            $curTxt = if ($cur) { $cur } else { '（未设置，用默认值）' }
            $usrTxt = if ($usr) { "  [用户级 = $usr]" } else { '' }
            Write-Host ("  [{0}] {1,-14} {2}   当前: {3}{4}" -f ($i + 1), $defs[$i].Name, $defs[$i].Desc, $curTxt, $usrTxt)
        }
        Write-Host '  [C] 清除变量（恢复默认）    [0] 返回'
        $sel = Read-Host '请选择'
        if ($sel -eq '0') { return }
        if ($sel -eq 'C' -or $sel -eq 'c') {
            $idx = Read-Host '要清除的变量编号'
            if ($idx -notmatch '^\d+$' -or [int]$idx -lt 1 -or [int]$idx -gt $defs.Count) {
                Write-Host ' 编号无效。' -ForegroundColor Yellow
                continue
            }
            $name = $defs[[int]$idx - 1].Name
            $scope = Read-Host "清除 $name 的作用域（1=仅会话 2=仅用户级持久化，回车=两者都清）"
            if ($scope -eq '1') {
                Remove-Item "Env:$name" -ErrorAction SilentlyContinue
                Write-Host "已清除会话级 $name。"
            } elseif ($scope -eq '2') {
                [Environment]::SetEnvironmentVariable($name, $null, 'User')
                Write-Host "已清除用户级 $name（本会话的值保留）。"
            } else {
                Remove-Item "Env:$name" -ErrorAction SilentlyContinue
                [Environment]::SetEnvironmentVariable($name, $null, 'User')
                Write-Host "已清除 $name（会话级与用户级），恢复工具默认值。"
            }
            continue
        }
        if ($sel -notmatch '^\d+$' -or [int]$sel -lt 1 -or [int]$sel -gt $defs.Count) {
            Write-Host ' 无效选择。' -ForegroundColor Yellow
            Start-Sleep -Seconds 1
            continue
        }
        $d = $defs[[int]$sel - 1]
        $v = Read-Host "请输入 $($d.Name) 的值（$($d.Desc)）"
        if (-not $v) { Write-Host ' 已取消。'; continue }
        if ($d.Validate -and (-not (& $d.Validate $v))) {
            Write-Host " 值不合法：$v（输入 0 返回查看说明）" -ForegroundColor Red
            continue
        }
        if ($d.Name -eq 'CBM_CACHE_DIR' -and -not (Test-Path -LiteralPath $v)) {
            Write-Host " [提示] 目录不存在，CBM 首次使用时会自动创建：$v" -ForegroundColor Yellow
        }
        $scope = Read-Host '作用域（1=仅当前会话 2=用户级持久化，回车=1）'
        if ($scope -eq '2') {
            [Environment]::SetEnvironmentVariable($d.Name, $v, 'User')
            Set-Item -Path "Env:$($d.Name)" -Value $v
            Write-Host "已设置 $($d.Name) = $v（用户级持久化：本会话立即生效，新开终端也生效）" -ForegroundColor Green
        } else {
            Set-Item -Path "Env:$($d.Name)" -Value $v
            Write-Host "已设置 $($d.Name) = $v（仅当前脚本会话内生效）"
        }
    }
}

# 常见问题速查（文档第十一节）
function Show-Faq {
    Write-Host "`n常见问题速查：`n"
    Write-Host '  索引文件存在哪？          %USERPROFILE%\.cache\codebase-memory-mcp\（SQLite 数据库）'
    Write-Host '  重装系统会丢索引吗？      会丢，需重新索引或用快照恢复'
    Write-Host '  代码改了会自动更新吗？    开启 auto_index 后会自动增量更新'
    Write-Host '  开启前代码已有改动？      开启后监听器启动时会自动检测并补齐'
    Write-Host '  项目里没有 graph.db.zst？ 经 index_repository 传 persistence=true 生成（0.10.4 已无 export 子命令）'
    Write-Host '  怎么验证安装成功？        --version 检查版本，或在 AI 工具中输入 /mcp'
}

# 高频踩坑（文档第十二节）
function Show-Pitfalls {
    Write-Host "`n高频踩坑提示：`n"
    Write-Host '  1. 大仓库会静默截断（不报错）：超大仓库建议关闭自动索引，按模块分批索引。'
    Write-Host '  2. AI 不会主动优先用图谱：会话开头要明确要求优先调用 codebase-memory-mcp 做结构查询，不要批量读源码。'
    Write-Host '  3. 反射、动态代理、eval 等运行时调用解析不出来，调用链缺失属于工具固有边界。'
    Write-Host '  4. Windows 下索引整体失败（提示 repo_path exists and contains source files）：'
    Write-Host '     很可能是根目录有 nul、con、prn、aux 等保留设备名文件，删掉杂散文件重跑即可。'
    Write-Host '  5. 别把测试仓库放 C:\tmp：DACL 不达标会被 cache-private 校验拒掉，制造"新项目一律失败"的假象。'
}

# 推荐提问方式（文档 5.6 节）
function Show-PromptTips {
    Write-Host "`n索引完成后，推荐向 AI 这样提问：`n"
    Write-Host '  - 请分析这个项目的整体架构，重点说明入口、核心模块和数据流。'
    Write-Host '  - 请查一下 UserService.createUser 被哪些地方调用，调用链是什么？'
    Write-Host '  - 我准备修改 OrderController.submitOrder，请分析可能影响到哪些模块。'
    Write-Host '  - 请找出项目中可能没有调用者的函数，并按风险排序。'
    Write-Host '  - 请结合代码图谱解释这个接口从路由到数据库写入的完整链路。'
    Write-Host "`n提示：会话开头建议明确声明「优先调用 codebase-memory-mcp 做结构查询，不要批量读源码」。"
}

# ============================================================
# 主菜单
# ============================================================
function Show-MainMenu {
    while ($true) {
        Show-Menu -Breadcrumb '主菜单' -Items @(
            '安装与验证'
            'AI 工具配置'
            '项目索引'
            '图谱查询'
            '配置管理（自动索引等）'
            '可视化 UI'
            '快照与 Git 备份'
            '帮助与环境变量'
            '维护与诊断（自检 / 日志 / ADR）'
        )
        switch (Read-Host '请选择') {
            '1' { Show-InstallMenu }
            '2' { Show-AiToolMenu }
            '3' { Show-IndexMenu }
            '4' { Show-QueryMenu }
            '5' { Show-ConfigMenu }
            '6' { Show-UiMenu }
            '7' { Show-SnapshotMenu }
            '8' { Show-HelpMenu }
            '9' { Show-MaintMenu }
            '0' {
                Write-Host "`n已退出，再见。"
                return
            }
            default {
                Write-Host '[提示] 无效选择。' -ForegroundColor Yellow
                Start-Sleep -Seconds 1
            }
        }
    }
}

# ============================================================
# 脚本入口
# ============================================================
# 清理超过 1 天的历史 args-file 临时文件（失败的调用会保留文件供手动重跑，成功即删）
Get-ChildItem ([IO.Path]::GetTempPath()) -Filter 'cbm-menu-*.json' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-24) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Show-MainMenu
