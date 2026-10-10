"""策略路由：E08 GET / E09 POST / E10 PUT / E11 DELETE（§5.1 L432-446）。

P1 口径：**仅默认策略可写**（多策略写开放于 P2）。精确行为：
- E09 POST：已有默认策略 → 409 ``POLICY_WRITE_CLOSED``（P1 不支持创建第二条）；
- E10 PUT：目标非默认策略 → 409 ``POLICY_WRITE_CLOSED``；策略不存在 → 404；
- E11 DELETE：默认策略 → 409 ``DEFAULT_POLICY_IMMUTABLE``；非默认策略 → 409 ``POLICY_WRITE_CLOSED``。

``is_default`` 由服务端控制：不接受客户端指定（E10 显式剔除），避免"第二条默认策略"。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import AUDIT_POLICY_CREATE, AUDIT_POLICY_DELETE, AUDIT_POLICY_UPDATE
from app.db.repositories.policy_repo import PolicyRepo
from app.errors import DefaultPolicyImmutableError, NotFoundError, PolicyWriteClosedError
from app.schemas.api_schemas import Page, PolicyOut, PolicyUpsertRequest
from app.services.policy_service import PolicyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["policies"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

_P1_WRITE_CLOSED_MSG = "P1 仅支持单条默认策略，多策略写开放于 P2"


@router.get(
    "/policies",
    response_model=Page[PolicyOut],
    summary="E08 策略列表",
    dependencies=_PROTECTED,
)
async def list_policies(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(require_session),
) -> Page[PolicyOut]:
    """返回全部策略（P1 实际只有一条默认策略）。"""
    del session
    repo: PolicyRepo = request.app.state.policy_repo
    service: PolicyService = request.app.state.policy_service
    policies = await repo.list_all()
    items = [PolicyOut(**service.to_api(policy)) for policy in policies]
    window = items[offset : offset + limit]
    return Page(items=window, total=len(items), limit=limit, offset=offset)


@router.post(
    "/policies",
    status_code=201,
    response_model=PolicyOut,
    summary="E09 新建策略",
    dependencies=_PROTECTED,
)
async def create_policy(
    payload: PolicyUpsertRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> PolicyOut:
    """P1 仅允许在尚无默认策略时创建默认策略；已有则 409 POLICY_WRITE_CLOSED。"""
    del session
    repo: PolicyRepo = request.app.state.policy_repo
    service: PolicyService = request.app.state.policy_service

    existing_default = await repo.get_default()
    if existing_default is not None:
        raise PolicyWriteClosedError(_P1_WRITE_CLOSED_MSG, data={"default_policy_id": existing_default.id})

    values = service.normalize(payload.model_dump())
    values["is_default"] = 1
    policy = await repo.create(values)
    await request.app.state.audit.record(
        AUDIT_POLICY_CREATE, target=str(policy.id), detail={"name": policy.name}, result="ok"
    )
    return PolicyOut(**service.to_api(policy))


@router.put(
    "/policies/{policy_id}",
    response_model=PolicyOut,
    summary="E10 更新策略",
    dependencies=_PROTECTED,
)
async def update_policy(
    policy_id: int,
    payload: PolicyUpsertRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> PolicyOut:
    """仅默认策略可写；非默认策略返回 409 POLICY_WRITE_CLOSED，不存在返回 404。"""
    del session
    repo: PolicyRepo = request.app.state.policy_repo
    service: PolicyService = request.app.state.policy_service

    target = await repo.get(policy_id)
    if target is None:
        raise NotFoundError("策略不存在")
    if not target.is_default:
        raise PolicyWriteClosedError(_P1_WRITE_CLOSED_MSG, data={"policy_id": policy_id})

    values = service.normalize(payload.model_dump())
    values.pop("is_default", None)  # is_default 由服务端控制，不接受客户端修改
    updated = await repo.update(policy_id, values)
    if updated is None:  # pragma: no cover - 理论不可达（前置已确认存在）
        raise NotFoundError("策略不存在")
    await request.app.state.audit.record(
        AUDIT_POLICY_UPDATE, target=str(policy_id), detail={"fields": sorted(values.keys())}, result="ok"
    )
    return PolicyOut(**service.to_api(updated))


@router.delete(
    "/policies/{policy_id}",
    response_model=Page[PolicyOut],
    summary="E11 删除策略",
    dependencies=_PROTECTED,
)
async def delete_policy(
    policy_id: int,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> Page[PolicyOut]:
    """默认策略不可删除（409 DEFAULT_POLICY_IMMUTABLE）；非默认策略 P1 不开放删除（409）。

    成功路径在 P1 不可达（两条删除分支均抛 409），返回删除后的列表仅为保持端点契约完整。
    """
    del session
    repo: PolicyRepo = request.app.state.policy_repo

    target = await repo.get(policy_id)
    if target is None:
        raise NotFoundError("策略不存在")

    # 删除是敏感动作：即便被拒（409）也要留痕（审计 append-only，§4.6）
    reason = "default_policy_immutable" if target.is_default else "policy_write_closed"
    await request.app.state.audit.record(
        AUDIT_POLICY_DELETE,
        target=str(policy_id),
        detail={"name": target.name, "reason": reason},
        result="denied",
    )
    if target.is_default:
        raise DefaultPolicyImmutableError(data={"policy_id": policy_id})
    raise PolicyWriteClosedError(_P1_WRITE_CLOSED_MSG, data={"policy_id": policy_id})
