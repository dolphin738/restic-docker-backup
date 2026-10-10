"""健康检查路由：E04（免鉴权，供容器 HEALTHCHECK 使用，§1.2）。

不校验仓库连通性，避免远程探测拖垮健康检查（§5.1 E04）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.schemas.api_schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="E04 健康检查（免鉴权）")
async def health(request: Request) -> HealthResponse:
    """返回进程与 SQLite 可用性；仓库连通性不在本端点检测范围内。"""
    db_ok = True
    try:
        await request.app.state.db.fetch_one("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - 健康检查不得因异常中断探测
        db_ok = False
        logger.warning("健康检查：数据库不可用（%s）", exc)
    return HealthResponse(status="ok" if db_ok else "degraded", db="ok" if db_ok else "error")
