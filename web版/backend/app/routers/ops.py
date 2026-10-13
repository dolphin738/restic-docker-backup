"""运维路由（架构 §2 P2 定档）：E21 forget 预览（R-016 预览-确认流的前半）。

三条约定（承接快照路由 + G3 安全裁决）：

1. **只读**：本端点恒以 ``forget --dry-run`` 执行（:class:`WhitelistArgs.dry_run`
   由本路由硬编码 ``True``，调用方无法覆盖），**绝不**落任何裁剪。实际执行必须经
   E12 ``{"type":"forget"}``（三重确认门在 UI，执行入审计 job.submit/finished）。
2. **G3 服务端兜底**：无任何 ``keep_*`` 规则的 forget 会删除分组内**全部**快照——
   preview 与 E12 执行两侧同拒 422（见 :func:`require_keep_rule`）。
3. **探测失败不静默**：restic 非 0 退出/超时 → 500 ``RESTIC_FAILED``，
   ``data.reason`` 由 :func:`app.services.restic.failure_reason` 生成可操作文案。

预览与执行的一致性（R-016 验收口径）：响应 ``params`` 回显请求参数，前端确认门
提交 E12 时原样透传——执行侧 :meth:`JobRunner._build_args` forget 分支消费同一批
具名字段，两边走的是同一份 restic 语义。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import AUDIT_FORGET_PREVIEW, FORGET_PREVIEW_TIMEOUT_SECONDS
from app.db.repositories.config_repo import ConfigRepo
from app.errors import ResticFailedError, ValidationError
from app.schemas.api_schemas import ForgetPreviewRequest, ForgetPreviewResponse, ForgetPreviewSnapshot
from app.services.env_sync import load_credentials
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs, failure_reason

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ops"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# keep_* → WhitelistArgs 具名字段（执行/预览共用一批语义，杜绝两边漂移）
_KEEP_FIELDS: tuple[str, ...] = (
    "keep_last",
    "keep_hourly",
    "keep_daily",
    "keep_weekly",
    "keep_monthly",
    "keep_yearly",
)


def require_keep_rule(*keep_values: int | None) -> None:
    """G3 兜底：至少一个保留规则显式给出，否则 422。

    Args:
        keep_values: 全部 keep_* 的有效值（None 表示未指定）。

    Raises:
        ValidationError: 全部未指定。
    """
    if all(value is None for value in keep_values):
        raise ValidationError(
            "forget 必须至少指定一个保留规则（keep_last/keep_hourly/…），"
            "否则会删除分组内全部快照",
            data={"keep_fields": list(_KEEP_FIELDS)},
        )


def build_forget_args(
    *,
    tag: str | None,
    group_by: str,
    keep: dict[str, int | None],
    dry_run: bool,
    prune: bool,
) -> WhitelistArgs:
    """从请求参数构造 forget 的白名单参数（E21 预览与 E12 执行共用一份映射）。

    Args:
        tag: 限定 tag（None = 不加 --tag）。
        group_by: 分组维度。
        keep: keep_* 具名字段 → 值映射。
        dry_run: 预览恒 True；执行恒 False。
        prune: 是否随 forget --prune 回收数据。
    """
    return WhitelistArgs(
        tags=(tag,) if tag else (),
        group_by=group_by,
        keep_last=keep.get("keep_last"),
        keep_hourly=keep.get("keep_hourly"),
        keep_daily=keep.get("keep_daily"),
        keep_weekly=keep.get("keep_weekly"),
        keep_monthly=keep.get("keep_monthly"),
        keep_yearly=keep.get("keep_yearly"),
        dry_run=dry_run,
        prune=prune,
        json_output=True,
    )


@router.post(
    "/forget/preview",
    response_model=ForgetPreviewResponse,
    summary="E21 forget 预览（R-016）",
    dependencies=_PROTECTED,
)
async def preview_forget(
    payload: ForgetPreviewRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> ForgetPreviewResponse:
    """``forget --dry-run --json`` → 将被裁剪的快照列表（只读，不落任何裁剪）。

    ``tag`` 传给 restic ``--tag``（与 E17 的 Python 侧过滤不同：forget 的范围限定
    本来就是 restic 语义，tag 值经 :func:`_assert_safe_value` 白名单校验）。
    """
    del session
    keep = {field: getattr(payload, field) for field in _KEEP_FIELDS}
    require_keep_rule(*keep.values())

    restic: ResticCmd = request.app.state.restic
    parser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo
    audit = request.app.state.audit

    repo_url = (await config_repo.get_value("repo.url")) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库配置页填写 repo.url）")

    args = build_forget_args(
        tag=payload.tag,
        group_by=payload.group_by,
        keep=keep,
        dry_run=True,  # 预览恒只读（模块 docstring 约定①），调用方无法经本端点传 False
        prune=False,  # dry-run 与 --prune 并存无意义，prune 只属于确认后的执行
    )
    argv = restic.build("forget", args=args)
    env = restic.build_env(repo_url=repo_url, env_extras=await load_credentials(config_repo))
    probe = await restic.run_json(argv, env=env, timeout_seconds=FORGET_PREVIEW_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, FORGET_PREVIEW_TIMEOUT_SECONDS)
        logger.warning("forget 预览探测失败：%s", reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    groups = await asyncio.to_thread(parser.parse_forget_groups, "\n".join(probe.lines))
    removed = [record for group in groups for record in group.remove]
    kept_count = sum(len(group.keep) for group in groups)
    items = [
        ForgetPreviewSnapshot(
            id=record.snapshot_id or "",
            time=record.time,
            host=record.host,
            tags=list(record.tags),
            paths=[mapper.to_display(item) for item in record.paths],
        )
        for record in removed
        if record.snapshot_id
    ]
    # 参数回显：前端确认门提交 E12 时原样透传（预览与实际结果一致）。
    # tag 的执行侧消费键是 params["tags"]（列表，JobRunner._build_args forget 分支），
    # 回显必须与消费端同键名，否则确认提交后范围限定会静默丢失。
    echo_params: dict[str, object] = {"group_by": payload.group_by, "prune": payload.prune}
    if payload.tag:
        echo_params["tags"] = [payload.tag]
    echo_params.update({field: value for field, value in keep.items() if value is not None})

    await audit.record(
        AUDIT_FORGET_PREVIEW,
        target="forget",
        detail={"tag": payload.tag, "remove_count": len(items), "prune": payload.prune},
        result="ok",
    )
    return ForgetPreviewResponse(
        remove=items,
        remove_count=len(items),
        keep_count=kept_count,
        group_count=len(groups),
        params=echo_params,  # type: ignore[arg-type]
        truncated=probe.truncated,
    )
