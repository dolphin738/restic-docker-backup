# 构建上下文为 web版/ 目录：
#   docker build -f docker/Dockerfile -t restic-web:0.1.0-restic0.19.1 .
# 说明：本镜像为单容器方案（方案 §2），restic 静态二进制随镜像固定版本（§4 D1）。

ARG RESTIC_VERSION=0.19.1

FROM restic/restic:${RESTIC_VERSION} AS restic-src

FROM python:3.13-alpine3.24

# restic 版本与（可选）校验位数：RESTIC_SHA256 非空时执行 sha256sum -c 校验
ARG RESTIC_VERSION=0.19.1
ARG RESTIC_SHA256=""

LABEL org.opencontainers.image.title="restic-web" \
      org.opencontainers.image.description="restic-docker-backup Web 管理端（FastAPI + restic 静态二进制）" \
      org.opencontainers.image.version="restic-${RESTIC_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC \
    DATA_DIR=/data \
    CACHE_DIR=/cache/restic \
    PORT=8000

# restic 官方静态二进制（版本由 ARG 固定，随镜像原子发布）
COPY --from=restic-src /usr/bin/restic /usr/local/bin/restic

# postgresql-client：始终跟随 Alpine stable 源收录的最新稳定 GA 大版本（方案 v2.3.3）
# mysql-client（MariaDB 系）、tzdata（zoneinfo 依赖）、wget（HEALTHCHECK）
RUN apk add --no-cache tzdata wget postgresql-client mysql-client \
    && apk add --no-cache --virtual .build-deps build-base libffi-dev \
    && if [ -n "${RESTIC_SHA256}" ]; then \
         echo "${RESTIC_SHA256}  /usr/local/bin/restic" | sha256sum -c - ; \
       else \
         restic version ; \
       fi \
    && true

WORKDIR /app

# 依赖与源码：pyproject.toml 为依赖唯一事实来源
COPY backend/pyproject.toml /app/pyproject.toml
COPY backend/app /app/app
RUN pip install --no-cache-dir . \
    && apk del .build-deps

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /data /cache/restic /restore \
    && adduser -D -u 1000 backup

# 兜底安全默认：仅当 compose 未显式指定 user 时生效（方案 §4：compose 默认显式指定 0:0）
USER backup

EXPOSE 8000

# HEALTHCHECK 打到 /api/health（E04，免鉴权）。
# 已知边界：内置"挂证书"式 TLS 启用后本探测需改为 https（busybox wget 无 TLS 支持），
# 该场景下建议改由反代侧探测或部署文档另行说明。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
