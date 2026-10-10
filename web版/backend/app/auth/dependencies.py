"""鉴权依赖与同源校验（§8 中间件顺序 ①②③）。

- ① CSRF/Origin：仅写方法（POST/PUT/PATCH/DELETE），由 ``main.py`` 的中间件调用 :func:`is_same_origin`；
- ② Session 鉴权：:func:`require_session`（路由依赖）；
- ③ Bootstrap 门：:func:`require_bootstrap_done`（``must_change=1`` 时仅放行 auth/bootstrap 相关路由）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.auth.bootstrap import BOOTSTRAP_STEPS, AdminAccountStore
from app.auth.session import SessionContext, SessionService
from app.constants import WRITE_METHODS
from app.errors import BootstrapRequiredError, CsrfOriginError, UnauthorizedError
from app.settings import Settings


def is_same_origin(request: Request) -> bool:
    """校验请求 Origin/Referer 与本站 host 同源（§8 第二道 CSRF 防线）。

    ``Origin`` 缺失时回退 ``Referer``；两者皆缺失视为不同源（本产品只有浏览器客户端，
    浏览器对写方法必带 Origin）。

    Args:
        request: 当前请求。

    Returns:
        同源返回 True，否则 False。
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return False
    source = _origin_parts(origin)
    if source is None:
        return False

    settings: Settings = request.app.state.settings
    forwarded_host = ""
    if settings.trust_proxy:
        forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host_header = forwarded_host or request.headers.get("host") or ""
    if not host_header:
        return False
    scheme = request.url.scheme
    if settings.trust_proxy:
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        if forwarded_proto:
            scheme = forwarded_proto
    target = _origin_parts(f"{scheme}://{host_header}")
    if target is None:
        return False
    return source[1:] == target[1:]


def is_write_method(request: Request) -> bool:
    """判断是否为需要 Origin 校验的写方法。"""
    return request.method.upper() in WRITE_METHODS


async def require_csrf(request: Request) -> None:
    """写方法同源校验依赖（供需要显式声明的路由使用；全局由中间件统一执行）。

    Raises:
        CsrfOriginError: 403 CSRF_ORIGIN。
    """
    if is_write_method(request) and not is_same_origin(request):
        raise CsrfOriginError()


async def require_session(request: Request) -> SessionContext:
    """校验 cookie 会话（②），并借此完成滑动续期。

    Returns:
        有效会话上下文。

    Raises:
        UnauthorizedError: 401 UNAUTHORIZED（未登录或会话过期）。
    """
    context = await optional_session(request)
    if context is None:
        raise UnauthorizedError()
    return context


async def optional_session(request: Request) -> SessionContext | None:
    """尝试解析会话；无有效会话时返回 None（不抛错）。"""
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    sessions: SessionService = request.app.state.sessions
    return await sessions.get_valid(token)


async def require_bootstrap_done(request: Request) -> None:
    """首启改密门（③）：``must_change=1`` 时除 auth/bootstrap 相关路由外一律 403。

    Raises:
        BootstrapRequiredError: 403 BOOTSTRAP_REQUIRED（data 带引导步骤）。
    """
    admin: AdminAccountStore = request.app.state.admin
    account = await admin.get()
    if account is None or account.must_change:
        raise BootstrapRequiredError(list(BOOTSTRAP_STEPS))


def _origin_parts(value: str) -> tuple[str, str, int] | None:
    """解析 URL/Origin 为 ``(scheme, host, port)``；解析失败返回 None。"""
    text = value if "://" in value else f"http://{value}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if not parts.hostname:
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname.lower(), port
