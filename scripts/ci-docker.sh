#!/usr/bin/env bash
# =============================================================================
# CI 手动流水线的 docker 构建/推送脚本（由仓库根 .cnb.yml web_trigger 调用）
#
# 用法：bash scripts/ci-docker.sh all
#   all      = build + push-cnb + push-hub（顺序执行，build 失败即整体失败）
#
# 依赖的环境变量（CNB 流水线自动注入）：
#   CNB_DOCKER_REGISTRY / CNB_REPO_SLUG_LOWERCASE / CNB_COMMIT_SHORT
#   DOCKERHUB_USER / DOCKERHUB_TOKEN / DOCKERHUB_NAMESPACE（经 imports 注入；
#     未配置时 Docker Hub 推送自动跳过）
#   RESTIC_VERSION（默认 0.19.1，与 web版/docker/Dockerfile 的 ARG 同源）
#
# 标签策略（方案 §4/§8、架构 §10.2）：
#   固定 = sha 短标签 + latest；HEAD 有 git tag 时追加 <app>-restic<restic> 双版本标签
# =============================================================================
set -euo pipefail

ACTION="${1:?usage: ci-docker.sh all}"
RESTIC_VERSION="${RESTIC_VERSION:-0.19.1}"
CNB_IMG="${CNB_DOCKER_REGISTRY}/${CNB_REPO_SLUG_LOWERCASE}"
HUB_IMG="${DOCKERHUB_NAMESPACE:-}/restic-web"
CTX="$(cd "$(dirname "$0")/.." && pwd)/web版"   # 构建上下文 = web版/（AGENTS.md 约定）
TAG_FILE="$(pwd)/.ci-image-tags"

resolve_tags() {
  : > "$TAG_FILE"
  # 固定标签：sha 短标签 + latest
  printf '%s\n' "${CNB_IMG}:${CNB_COMMIT_SHORT}" >> "$TAG_FILE"
  printf '%s\n' "${CNB_IMG}:latest" >> "$TAG_FILE"
  # Docker Hub（已配置时）
  if [ -n "${DOCKERHUB_NAMESPACE:-}" ]; then
    printf '%s\n' "${HUB_IMG}:${CNB_COMMIT_SHORT}" >> "$TAG_FILE"
    printf '%s\n' "${HUB_IMG}:latest" >> "$TAG_FILE"
  fi
  # 条件标签：HEAD 恰好是某个 git tag 的目标时 → 双版本 <app版本>-restic<restic版本>
  # （发布语义：只对显式打 tag 的 commit 出发布标签，日常构建不出）
  git fetch --tags origin 2>/dev/null || true   # CI 检出可能不带 tags，补拉（失败不阻断）
  if app_version=$(git describe --tags --exact-match HEAD 2>/dev/null); then
    app_version="${app_version#v}"
    vtag="${app_version}-restic${RESTIC_VERSION}"
    printf '%s\n' "${CNB_IMG}:${vtag}" >> "$TAG_FILE"
    if [ -n "${DOCKERHUB_NAMESPACE:-}" ]; then
      printf '%s\n' "${HUB_IMG}:${vtag}" >> "$TAG_FILE"
    fi
    echo "发布双版本标签：${vtag}"
  else
    echo "当前 commit 无 git tag，跳过双版本标签"
  fi
}

do_build() {
  local args=()
  while IFS= read -r t; do
    [ -n "$t" ] && args+=("-t" "$t")
  done < "$TAG_FILE"
  docker build -f "${CTX}/docker/Dockerfile" \
    --build-arg "RESTIC_VERSION=${RESTIC_VERSION}" \
    "${args[@]}" \
    "$CTX"
}

do_push_cnb() {
  while IFS= read -r img; do
    case "$img" in "${CNB_DOCKER_REGISTRY}/"*) docker push "$img" ;; esac
  done < "$TAG_FILE"
}

do_push_hub() {
  if [ -z "${DOCKERHUB_NAMESPACE:-}" ]; then
    echo "未配置 Docker Hub 凭据（dolphin738/my:docker.yml），跳过推送"
    return 0
  fi
  echo "${DOCKERHUB_TOKEN}" | docker login -u "${DOCKERHUB_USER}" --password-stdin
  while IFS= read -r img; do
    case "$img" in "${DOCKERHUB_NAMESPACE}/"*) docker push "$img" ;; esac
  done < "$TAG_FILE"
  docker logout
}

case "$ACTION" in
  all)
    resolve_tags
    do_build
    do_push_cnb
    do_push_hub
    ;;
  build)     resolve_tags; do_build ;;
  push-cnb)  do_push_cnb ;;
  push-hub)  do_push_hub ;;
  *) echo "未知动作：$ACTION（可选 all/build/push-cnb/push-hub）" >&2; exit 2 ;;
esac
