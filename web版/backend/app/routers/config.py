"""配置路由：E06 读取 / E07 写入（§4.3 键表）。

- 敏感项（``cred.*`` 与 ``dump.password``）只回 ``has_value`` + ``source``，不回明文；
- ``source='env'`` 的键拒绝界面写入 → 409 ``ENV_OVERRIDDEN``（由 :class:`ConfigRepo` 保证）；
- ``passfile.status`` 为**只读派生**（密码文件是否存在），不落库（§4.3）。
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import AUDIT_CONFIG_UPDATE
from app.errors import ValidationError
from app.schemas.api_schemas import ConfigItem, ConfigPatchRequest, ConfigResponse, OkResponse
from app.services.env_sync import passfile_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# 键名形态白名单：小写字母开头，仅含小写字母/数字/点/下划线/连字符/星号（防污染键空间）
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9.*_-]{0,63}$")


@router.get(
    "/config",
    response_model=ConfigResponse,
    summary="E06 读取配置",
    dependencies=_PROTECTED,
)
async def get_config(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> ConfigResponse:
    """返回全部配置项（敏感项脱敏）与仓库密码文件挂载状态。"""
    del session
    settings = request.app.state.settings
    repo = request.app.state.config_repo
    rows = await repo.list_all()
    items = [ConfigItem(**repo.mask(row)) for row in rows]
    return ConfigResponse(items=items, passfile_status=passfile_status(settings))


@router.put(
    "/config",
    response_model=OkResponse,
    summary="E07 写入配置",
    dependencies=_PROTECTED,
)
async def put_config(
    payload: ConfigPatchRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """批量写入配置项；命中 env 覆盖键时整体拒绝（409 ENV_OVERRIDDEN，不产生部分写入）。"""
    del session
    if not payload.patch:
        raise ValidationError("patch 不能为空")

    illegal = [key for key in payload.patch if not _KEY_PATTERN.match(key)]
    if illegal:
        raise ValidationError("配置键名非法", data={"illegal": illegal})

    repo = request.app.state.config_repo
    await repo.set_many(payload.patch)

    keys = sorted(payload.patch.keys())
    await request.app.state.audit.record(
        AUDIT_CONFIG_UPDATE,
        target="config",
        detail={"keys": keys, "count": len(keys)},
        result="ok",
    )
    return OkResponse()
