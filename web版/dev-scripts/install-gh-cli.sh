#!/usr/bin/env bash
#
# 一次性向导：把 GitHub CLI (gh) 装到这台 Windows，并走完首次登录。
# 由 /wizard 生成：STAGES 标记以上的库原样保留，勿手改；只改下面的 stage。
#
# 官方来源：https://cli.github.com/ （安装包实际托管在 GitHub Releases）
# 运行（必须在 Git Bash 里，Git for Windows 自带）：
#     bash dev-scripts/install-gh-cli.sh
# 不需要你提前开管理员终端——向导自己弹 UAC 提权。
# 性质：一次性脚本，装完即可删；要不要入库由负责人决定。

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# Wizard library: delightful, consistent UX, identical across every wizard.
# ──────────────────────────────────────────────────────────────────────────

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
  BLUE=$(tput setaf 4); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); RED=$(tput setaf 1)
else
  BOLD=""; DIM=""; RESET=""; BLUE=""; GREEN=""; YELLOW=""; RED=""
fi

# Author sets this at the top of the stages section.
TOTAL_STAGES=0

_STAGE_INDEX=0
ENV_FILE="${ENV_FILE:-.env}"
WRITTEN_ENV=()    # KEYs written to ENV_FILE this run
WRITTEN_SECRET=() # secret NAMEs set this run
SKIPPED=()        # things we couldn't do (e.g. gh missing)

# _clear wipes the terminal so only the current step is on screen. No-op when
# output isn't a terminal, so piped logs stay readable.
_clear() {
  [[ -t 1 ]] || return 0
  if command -v tput >/dev/null 2>&1; then tput clear; else printf '\033[2J\033[3J\033[H'; fi
}

# banner "Title" shows the opening frame: what this wizard does.
banner() {
  _clear
  printf '\n%s%s  %s%s\n' "$BOLD" "$BLUE" "$1" "$RESET"
  printf '%s  %s stages%s\n\n' "$DIM" "$TOTAL_STAGES" "$RESET"
  printf '%s  You drive the browser; this wizard tells you exactly what to do and\n' "$DIM"
  printf '  captures the values you copy back. Stop any time with Ctrl-C and re-run\n'
  printf '  later, since it remembers values already saved.%s\n' "$RESET"
  pause "Ready to start?"
}

# stage "Name" clears the screen, then announces a stage and shows progress.
# Clearing keeps only the current step on screen.
stage() {
  _clear
  _STAGE_INDEX=$((_STAGE_INDEX + 1))
  printf '\n%s%s▸ Stage %s/%s · %s%s\n' \
    "$BOLD" "$BLUE" "$_STAGE_INDEX" "$TOTAL_STAGES" "$1" "$RESET"
}

# say "..." prints a plain instruction line.
say()  { printf '  %s\n' "$1"; }
# step "..." is a numbered-feeling action the human takes in the browser.
step() { printf '  %s•%s %s\n' "$BLUE" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }

# open_url URL opens it in the human's browser, cross-platform incl. WSL.
open_url() {
  local url="$1"
  printf '  %s↗ opening%s %s\n' "$GREEN" "$RESET" "$url"
  { if   command -v wslview     >/dev/null 2>&1; then wslview "$url"
    elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$url"
    elif command -v xdg-open    >/dev/null 2>&1; then xdg-open "$url"
    elif command -v open        >/dev/null 2>&1; then open "$url"
    else warn "couldn't open a browser; visit it manually: $url"; fi
  } >/dev/null 2>&1 || warn "couldn't open a browser, so visit it manually: $url"
}

# pause "msg" waits for the human to confirm they've done the manual part.
pause() {
  printf '  %s%s%s ' "$DIM" "${1:-Press Enter to continue}" "$RESET"
  read -r _ || true
}

# confirm "question" is a y/N gate; returns success on yes.
confirm() {
  local reply=""
  printf '  %s? %s [y/N] ' "$YELLOW" "$1"
  read -r reply || true
  [[ "$reply" =~ ^[Yy] ]]
}

# _existing KEY: current value of KEY in ENV_FILE, if any.
_existing() {
  [[ -f "$ENV_FILE" ]] || return 1
  local line; line=$(grep -E "^${1}=" "$ENV_FILE" | tail -n1) || return 1
  printf '%s' "${line#*=}"
}

# ask KEY "Prompt" reads a value into $KEY. Offers the existing .env value as
# a default on re-runs (Enter keeps it). Visible input (non-secret).
ask() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -r input || true
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

# ask_secret KEY "Prompt" is like ask, but input is hidden.
ask_secret() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -rs input || true
  printf '\n'
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

# write_env KEY VALUE upserts KEY=VALUE into ENV_FILE (creates it; replaces
# any existing line). Idempotent.
write_env() {
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"
  tmp=$(mktemp)
  grep -vE "^${key}=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  WRITTEN_ENV+=("$key")
  printf '  %s✓ wrote%s %s → %s\n' "$GREEN" "$RESET" "$key" "$ENV_FILE"
}

# set_secret NAME VALUE sets a GitHub Actions repo secret via gh. Falls back
# to a warning (and records it) if gh is unavailable or unauthenticated.
set_secret() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if printf '%s' "$value" | gh secret set "$name" >/dev/null 2>&1; then
      WRITTEN_SECRET+=("$name")
      printf '  %s✓ set%s GitHub secret %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub secret $name (set it manually: gh secret set $name)")
  warn "skipped GitHub secret $name: gh not ready; set it later"
}

# set_var NAME VALUE sets a GitHub Actions repo variable (non-secret).
set_var() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if gh variable set "$name" --body "$value" >/dev/null 2>&1; then
      printf '  %s✓ set%s GitHub variable %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub variable $name")
  warn "skipped GitHub variable $name, gh not ready; set it later"
}

# finish clears, then shows a closing summary of everything configured.
finish() {
  _clear
  printf '\n%s%s  ✓ Setup complete%s\n' "$BOLD" "$GREEN" "$RESET"
  (( ${#WRITTEN_ENV[@]} ))    && note "wrote ${#WRITTEN_ENV[@]} value(s) to $ENV_FILE: ${WRITTEN_ENV[*]}"
  (( ${#WRITTEN_SECRET[@]} )) && note "set ${#WRITTEN_SECRET[@]} GitHub secret(s): ${WRITTEN_SECRET[*]}"
  if (( ${#SKIPPED[@]} )); then
    printf '\n'; warn "still to do by hand:"
    for s in "${SKIPPED[@]}"; do note "  - $s"; done
  fi
  printf '\n'
}

# ──────────────────────────────────────────────────────────────────────────
# STAGES: 装 GitHub CLI（Windows 官方 MSI）并走完首次登录
# ──────────────────────────────────────────────────────────────────────────

TOTAL_STAGES=5

banner "GitHub CLI (gh) 安装向导"

# ── Stage 1：预检 ─────────────────────────────────────────────────────────
stage "预检：来源、架构、已有安装"
say "官方来源 https://cli.github.com/ ，安装包托管在 GitHub Releases。"
say "本向导用官方 Windows MSI 装 gh，装完校验版本，再带你走一遍登录。"
open_url "https://cli.github.com/"
note "途中需要你配合两次：点一次 UAC「是」，在浏览器里授权一次。"
printf '\n'

GH_VERSION_IN="${GH_VERSION:-}"

GH_ARCH=""
case "$(uname -m)" in
  x86_64|amd64)  GH_ARCH=amd64 ;;
  aarch64|arm64) GH_ARCH=arm64 ;;
  i386|i686)     GH_ARCH=386 ;;
  *) warn "识别不了架构：$(uname -m)"; ask GH_ARCH "手动填架构（amd64/arm64/386）：" ;;
esac
say "CPU 架构：$GH_ARCH"

for tool in curl sha256sum cygpath powershell.exe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    warn "缺少 $tool：请在 Git Bash 里运行本脚本（Git for Windows 自带这些工具）"
    exit 1
  fi
done

ALREADY=""
if command -v gh >/dev/null 2>&1; then
  ALREADY=$(command -v gh)
  say "已存在 gh：$ALREADY"
  "$ALREADY" --version 2>&1 | sed -n '1p' | sed 's/^/  /'
  note "继续就是覆盖安装/升级；已登录的账号和配置会保留。"
else
  note "当前没有 gh（也可能只是没进 PATH）。"
fi

confirm "开始？" || { printf '\n'; note "已取消，什么都没改。"; exit 0; }

# ── Stage 2：下载 + 校验 ──────────────────────────────────────────────────
stage "下载：解析最新版并校验 SHA-256"
GH_VERSION="$GH_VERSION_IN"
if [[ -z "$GH_VERSION" ]]; then
  note "查询 GitHub Releases 的最新 tag ……"
  GH_VERSION=$(curl -fsSL --max-time 30 https://api.github.com/repos/cli/cli/releases/latest 2>/dev/null \
    | grep -o '"tag_name": *"[^"]*"' | cut -d'"' -f4 | sed -n '1p' || true)
fi
if [[ -z "$GH_VERSION" ]]; then
  GH_VERSION="v2.103.3"
  warn "查不到最新版本，退回 $GH_VERSION（本脚本编写时核对过的版本）"
fi
GH_VER_NUM=$(printf '%s' "$GH_VERSION" | sed 's/^v//')
say "目标版本：$GH_VERSION"

MSI_NAME="gh_${GH_VER_NUM}_windows_$GH_ARCH.msi"
BASE_URL="https://github.com/cli/cli/releases/download/$GH_VERSION"
WORK_DIR=$(mktemp -d)
MSI_PATH="$WORK_DIR/$MSI_NAME"
trap 'rm -rf "$WORK_DIR"' EXIT

step "下载 $MSI_NAME"
note "$BASE_URL/$MSI_NAME"
if ! curl -fL -sS --retry 3 --retry-delay 2 --max-time 600 -o "$MSI_PATH" "$BASE_URL/$MSI_NAME"; then
  warn "下载失败：检查网络/代理，或手动下载后用 msiexec /i 安装。"
  exit 1
fi
MSI_BYTES=$(wc -c < "$MSI_PATH" | tr -d ' ')
say "已下载 $MSI_BYTES 字节"
if [[ "$MSI_BYTES" -lt 1000000 ]]; then
  warn "文件小于 1 MB，不像完整安装包，中止。"
  exit 1
fi

CHECKSUMS_URL="$BASE_URL/gh_${GH_VER_NUM}_checksums.txt"
EXPECTED_SHA=$(curl -fsSL --max-time 60 "$CHECKSUMS_URL" 2>/dev/null \
  | grep -F " $MSI_NAME" | awk '{print $1}' | sed -n '1p' || true)
if [[ -z "$EXPECTED_SHA" ]]; then
  warn "拿不到官方校验文件，无法验证 SHA-256。"
  confirm "仍然安装这个未校验的安装包？" || { printf '\n'; note "已取消。"; exit 0; }
else
  ACTUAL_SHA=$(sha256sum "$MSI_PATH" | awk '{print $1}')
  if [[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]]; then
    say "SHA-256 校验通过。"
  else
    warn "SHA-256 不匹配：安装包可能损坏或被篡改，已中止。"
    note "官方：$EXPECTED_SHA"
    note "本地：$ACTUAL_SHA"
    exit 1
  fi
fi

# ── Stage 3：安装（UAC） ──────────────────────────────────────────────────
stage "安装：UAC 提权跑 MSI"
say "接下来会弹 UAC（用户账户控制）——点「是」。"
note "msiexec 用 /passive：只显示进度条，不再问问题。"
WIN_MSI=$(cygpath -w "$MSI_PATH")
PS_HELPER="$WORK_DIR/run-msi.ps1"
cat > "$PS_HELPER" <<'PSEOF'
param([Parameter(Mandatory=$true)][string]$Msi)
try {
  $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', $Msi, '/passive', '/norestart') -Verb RunAs -PassThru -ErrorAction Stop
} catch {
  Write-Host "UAC 被取消或启动失败：$($_.Exception.Message)"
  exit 1223
}
$p.WaitForExit()
exit $p.ExitCode
PSEOF

pause "按 Enter 开始安装（随后请点 UAC 的「是」）"
set +e
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$PS_HELPER")" -Msi "$WIN_MSI"
RC=$?
set -e
case "$RC" in
  0)    say "安装器返回 0：安装成功。" ;;
  3010) say "安装器返回 3010：装好了，重启后完全生效。" ;;
  1223) warn "UAC 被取消，没装成。重跑本脚本即可。" ;;
  1603) warn "安装器返回 1603：权限不足，或机器上已有更高版本。" ;;
  *)    warn "安装器返回 $RC，安装未完成。" ;;
esac
if [[ "$RC" -ne 0 && "$RC" -ne 3010 ]]; then
  warn "手动安装：msiexec /i <msi> /passive"
  note "$WIN_MSI"
  exit 1
fi

# ── Stage 4：验证 ─────────────────────────────────────────────────────────
stage "验证：gh --version"
GH_EXE=""
if command -v gh >/dev/null 2>&1; then
  GH_EXE=$(command -v gh)
else
  for cand in "/c/Program Files/GitHub CLI/gh.exe" "/c/Program Files (x86)/GitHub CLI/gh.exe"; do
    if [[ -z "$GH_EXE" && -x "$cand" ]]; then GH_EXE="$cand"; fi
  done
fi
if [[ -z "$GH_EXE" ]]; then
  warn '找不到 gh.exe，默认应在 C:\Program Files\GitHub CLI\gh.exe'
  exit 1
fi
say "位置：$GH_EXE"
CURRENT_VER=$("$GH_EXE" --version 2>&1 | sed -n '1p' || true)
say "版本：$CURRENT_VER"
case "$CURRENT_VER" in
  *"$GH_VER_NUM"*) say "版本与目标一致。" ;;
  *) warn "版本里没看到 $GH_VER_NUM：可能装的是别的版本，或 PATH 上还有旧 gh。" ;;
esac
note "当前 Git Bash 的 PATH 是启动时的快照，所以本窗口里 gh 还不在 PATH 上。"
note '新开一个终端后，PATH 才会包含 Program Files\GitHub CLI。'

# ── Stage 5：登录 ─────────────────────────────────────────────────────────
stage "登录：gh auth login（浏览器一次性码）"
say "下一屏 gh 会打印一次性码（形如 ABCD-1234），并让你按 Enter 打开浏览器。"
step "复制那个一次性码"
step "浏览器里粘到 github.com/login/device，点 Authorize"
note "若问 Authenticate Git with your GitHub credentials? —— 直接回车（Yes）。"
printf '\n'
DO_LOGIN=1
confirm "现在开始登录？" || DO_LOGIN=0
GH_ACCOUNT=""
if [[ "$DO_LOGIN" -eq 1 ]]; then
  if "$GH_EXE" auth login --hostname github.com --git-protocol https --web; then
    GH_ACCOUNT=$("$GH_EXE" api user --jq .login 2>/dev/null || true)
    if [[ -n "$GH_ACCOUNT" ]]; then say "已登录账号：$GH_ACCOUNT"; fi
    printf '\n'
    if confirm "顺手把 gh 设为 git 的 GitHub 凭据助手（gh auth setup-git）？"; then
      if "$GH_EXE" auth setup-git --hostname github.com; then
        say "已写入 git 凭据配置。"
      else
        warn "setup-git 失败；不影响 gh 本身，可稍后手动跑。"
      fi
    else
      note "跳过；之后可用 gh auth setup-git 补上。"
    fi
  else
    warn "登录没走完；稍后单独跑 gh auth login --hostname github.com --web"
  fi
fi

finish

note "gh 位置：$GH_EXE"
if [[ -n "$GH_ACCOUNT" ]]; then note "登录账号：$GH_ACCOUNT"; fi
note "登录凭据由 gh 自己存进 Windows 凭据管理器，本向导不落任何明文。"
note "卸载：设置 → 应用 → GitHub CLI，或 msiexec /x <安装用的 msi>"
note "文档：https://cli.github.com/manual/ ｜ https://docs.github.com/zh/github-cli"
