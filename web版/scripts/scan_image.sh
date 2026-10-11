#!/usr/bin/env bash
# 镜像漏洞扫描（G8：trivy 扫描，高危 CVE = Blocker）
#
# 为什么需要：规范 §3.1 只约束"源码写得好不好"，而镜像里装的
# python:3.13-alpine3.24 / postgresql-client / mysql-client 的
# 系统层漏洞不在源码审查范围内，必须靠镜像扫描兜住。
#
# 前置：需本机具备 docker（构建镜像）与 trivy（扫描镜像）。
#   docker build -t restic-web:scan web版/
#   trivy image restic-web:scan
#
# 用法：
#   bash scripts/scan_image.sh                 # 构建 + 扫描本地镜像
#   bash scripts/scan_image.sh <image:tag>     # 扫描已有镜像（跳过构建）
#   bash scripts/scan_image.sh --install-hint  # 只打印安装命令
#
# 退出码：0 = 无高危；1 = 存在 HIGH/CRITICAL（Blocker）；2 = 工具缺失

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

# 判定门槛：与规范一致——HIGH/CRITICAL 阻塞合并。
# MEDIUM 不阻塞但会在输出中提示，由审查人判断是否可接受（记 issue）。
SEVERITY="HIGH,CRITICAL"
IMAGE_TAG="restic-web:scan"

if [ "${1:-}" = "--install-hint" ]; then
  cat <<'EOF'
安装指引：
  trivy 二进制（官方安装脚本会自动选平台包）：
    curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
  Windows 可用 winget / scoop / 直接下 release 包。
  校验：trivy --version

  离线镜像库方案（CI 内网环境）：
    trivy image --download-db-only     # 预下载漏洞库
    trivy image --skip-db-update <image>
EOF
  exit 0
fi

# ---------------------------------------------------------------- 工具检查

missing=0
command -v trivy >/dev/null 2>&1 || {
  printf '✗ 未找到 trivy。安装：bash scripts/scan_image.sh --install-hint\n'
  missing=1
}

if [ "${1:-}" != "--no-build" ] && [ "$#" -eq 0 ] && ! command -v docker >/dev/null 2>&1; then
  printf '✗ 未找到 docker，无法构建待扫描镜像。\n'
  printf '  若要扫描已有镜像：bash scripts/scan_image.sh <image:tag>\n'
  missing=1
fi

if [ "$missing" -eq 1 ]; then
  printf '\n\033[31m工具缺失，未执行扫描\033[0m\n'
  exit 2
fi

# ---------------------------------------------------------------- 构建

if [ "$#" -gt 0 ] && [ "${1:-}" != "--no-build" ]; then
  IMAGE_TAG="$1"
  printf '扫描已有镜像：%s\n\n' "$IMAGE_TAG"
else
  printf '构建镜像：%s（上下文 web版/）\n' "$IMAGE_TAG"
  printf '提示：.dockerignore 已排除 .venv/node_modules/.env，镜像不会夹带这些内容。\n\n'
  if ! docker build -t "$IMAGE_TAG" . ; then
    printf '\n\033[31m✗ 镜像构建失败\033[0m\n'
    exit 1
  fi
  printf '\n构建完成，开始扫描\n\n'
fi

# ---------------------------------------------------------------- 扫描

# --ignore-unfixed 只看"有修复版本的漏洞"：未修复的 alpine/apk 长期滞后噪声大，
# 一并报出会淹没真实问题。修复可用性由审查人另行判断。
REPORT=$(trivy image \
  --severity "$SEVERITY" \
  --ignore-unfixed \
  --format json \
  "$IMAGE_TAG" 2>/dev/null)

if [ -z "$REPORT" ]; then
  printf '\033[33m⚠ trivy 无输出（可能漏洞库未更新，可加 --download-db-only 预下载）\033[0m\n'
  exit 2
fi

# 统计各级漏洞数（jq 不可用时退回文本表格）
if command -v jq >/dev/null 2>&1; then
  HIGH=$(printf '%s' "$REPORT" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length')
  CRIT=$(printf '%s' "$REPORT" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length')
else
  HIGH=$(printf '%s' "$REPORT" | grep -o '"Severity":"HIGH"' | wc -l | tr -d ' ')
  CRIT=$(printf '%s' "$REPORT" | grep -o '"Severity":"CRITICAL"' | wc -l | tr -d ' ')
fi

printf '\n%s\n' '────────��───────────────────────────────'
printf 'HIGH=%s  CRITICAL=%s（忽略无修复版本）\n' "$HIGH" "$CRIT"

if [ "$HIGH" -gt 0 ] || [ "$CRIT" -gt 0 ]; then
  printf '\n\033[31m✗ 存在高危漏洞 = Blocker（规范 §6 Docker 速查表）\033[0m\n'
  printf '明细：trivy image --severity %s %s\n' "$SEVERITY" "$IMAGE_TAG"
  printf '处置：① 评估是否已有 alpine 侧缓解；② 更新基础镜像小版本；③ 无法修复须在 PR 记 issue 说明风险接受理由。\n'
  exit 1
fi

printf '\n\033[32m✓ 无高危漏洞\033[0m\n'
exit 0
