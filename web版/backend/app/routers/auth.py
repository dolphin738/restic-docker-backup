"""认证路由：E01 登录 / E02 登出 / E03 me / Bootstrap 取密码 / E28 改密（§5.1）。

鉴权约定（§5.0）：除 E04 与 E01、bootstrap 外全部要求有效 session；
写方法额外要求 Origin 同源（由全局中间件执行）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import optional_session, require_session
from app.auth.service import AuthService, client_ip
from app.auth.session import SessionContext, cookie_secure
from app.constants import SESSION_COOKIE_PATH, SESSION_COOKIE_SAMESITE
from app.errors import BootstrapRequiredError, UnauthorizedError
from app.schemas.api_schemas import (
    BootstrapResponse,
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    OkResponse,
)
from app.schemas.enums import HostMountMode, RunMode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login", response_model=OkResponse, summary="E01 登录（免鉴权）")
async def login(payload: LoginRequest, request: Request, response: Response) -> OkResponse:
    """E01 登录：校验密码并下发 ``rw_session`` cookie。

    错误：401 PASSWORD_WRONG / 429 ACCOUNT_LOCKED（带 retry_after）/ 429 RATE_LIMITED / 403 CSRF_ORIGIN。
    """
    settings = request.app.state.settings
    auth_service: AuthService = request.app.state.auth_service
    token = await auth_service.login(payload.password, client_ip(request, settings))

    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max(1, settings.session_ttl_hours) * 3600,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,  # type: ignore[arg-type]
        secure=cookie_secure(request, settings),
        path=SESSION_COOKIE_PATH,
    )
    return OkResponse()


@router.post("/auth/logout", response_model=OkResponse, summary="E02 登出")
async def logout(request: Request, response: Response, _: SessionContext = Depends(require_session)) -> OkResponse:
    """E02 登出：销毁服务端 session 并清除 cookie。"""
    settings = request.app.state.settings
    auth_service: AuthService = request.app.state.auth_service
    token = request.cookies.get(settings.session_cookie_name)
    await auth_service.logout(token, client_ip(request, settings))
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=SESSION_COOKIE_PATH,
        samesite=SESSION_COOKIE_SAMESITE,  # type: ignore[arg-type]
    )
    return OkResponse()


@router.get("/auth/me", response_model=MeResponse, summary="E03 会话信息")
async def me(request: Request, session: SessionContext = Depends(require_session)) -> MeResponse:
    """E03 会话信息：供仪表盘四象限卡（US-G2）与前端路由守卫使用。

    错误：401 UNAUTHORIZED / 403 BOOTSTRAP_REQUIRED（首启未改密）。
    """
    del session  # 依赖本身已完成鉴权，此处仅需其副作用（滑动续期）
    settings = request.app.state.settings
    account = await request.app.state.admin.get()
    must_change = True if account is None else account.must_change
    if must_change:
        raise BootstrapRequiredError()
    return MeResponse(
        authenticated=True,
        must_change_password=False,
        mode=RunMode(settings.restic_web_mode),
        host_mount=HostMountMode(settings.host_mount_mode),
        user=settings.run_user,
    )


@router.get("/auth/bootstrap", response_model=BootstrapResponse, summary="首启随机密码（一次性）")
async def bootstrap_password(request: Request) -> BootstrapResponse:
    """取回首启随机管理密码（仅一次；已展示过返回 410 BOOTSTRAP_SHOWN）。"""
    service = request.app.state.bootstrap
    plain = await service.reveal_password()
    return BootstrapResponse(password=plain)


@router.put("/admin/password", response_model=OkResponse, summary="E28 管理员改密")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    session: SessionContext | None = Depends(optional_session),
) -> OkResponse:
    """E28 改密：首启场景 ``old_password`` 为随机引导密码（无需登录）；已登录场景需有效会话。

    成功后重哈希密码、结束强制改密态、销毁其他会话并写审计。
    """
    account = await request.app.state.admin.get()
    must_change = True if account is None else account.must_change
    if session is None and not must_change:
        raise UnauthorizedError("请先登录后再修改密码")

    auth_service: AuthService = request.app.state.auth_service
    service = request.app.state.bootstrap
    current_token = session.token if session else None
    await service.change_password(
        payload.old_password,
        payload.new_password,
        destroy_other_sessions=auth_service.destroy_others,
        current_token=current_token,
    )
    if must_change:
        logger.info("首启改密完成，强制改密状态已解除")
    return OkResponse()
