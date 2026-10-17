"""运维路由（架构 §2 P2 定档）：E21 forget 预览 / E23·E24 通知（R-016/R-018）。

四条约定（承接快照路由 + G3 安全裁决）：

1. **E21 只读**：恒以 ``forget --dry-run`` 执行（:class:`WhitelistArgs.dry_run`
   由本路由硬编码 ``True``，调用方无法覆盖），**绝不**落任何裁剪。实际执行必须经
   E12 ``{"type":"forget"}``（三重确认门在 UI，执行入审计 job.submit/finished）。
2. **G3 服务端兜底**：无任何保留规则（``keep_*`` 计数档 / ``keep_within*`` 时长档 /
   ``keep_tags`` 标签档，A2 起三门并列）的 forget 会删除分组内**全部**快照——
   preview 与 E12 执行两侧同拒 422（见 :func:`require_keep_rule`）。
3. **探测/推送失败不静默**：restic 非 0 退出/超时 → 500 ``RESTIC_FAILED``；
   E24 测试消息逐渠道回结果，失败必须带原因。
4. **E23 脱敏**：healthchecks/webhook URL 内嵌令牌，只回 ``*_set`` 布尔不回明文
   （与 E06 cred.* 同口径）；写入三态语义（None=不变 / ""=清除 / 非空=设置）。

预览与执行的一致性（R-016 验收口径）：响应 ``params`` 回显请求参数，前端确认门
提交 E12 时原样透传——执行侧 :meth:`JobRunner._build_args` forget 分支消费同一批
具名字段，两边走的是同一份 restic 语义。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_CACHE_CLEAR,
    AUDIT_FORGET_PREVIEW,
    AUDIT_NOTIFY_TEST,
    AUDIT_NOTIFY_UPDATE,
    FORGET_PREVIEW_TIMEOUT_SECONDS,
    HISTORY_QUERY_MAX_LIMIT,
)
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import ReposRepo
from app.dependencies import current_repo_id
from app.errors import JobConflictError, ResticFailedError, ValidationError
from app.schemas.api_schemas import (
    AuditOut,
    AuditPageResponse,
    CacheUsageResponse,
    ForgetPreviewRequest,
    ForgetPreviewResponse,
    ForgetPreviewSnapshot,
    NotifyConfigPatch,
    NotifyConfigResponse,
    NotifyTestChannel,
    NotifyTestResponse,
    OkResponse,
)
from app.schemas.enums import AuditResult
from app.services.env_sync import load_credentials
from app.services.notifier import (
    DEFAULT_MAIL_REDACT,
    DEFAULT_MEOW_REDACT,
    DEFAULT_WEBHOOK_REDACT,
    KEY_HEALTHCHECKS_URL,
    KEY_MAIL_FROM,
    KEY_MAIL_HOST,
    KEY_MAIL_PASSWORD,
    KEY_MAIL_PORT,
    KEY_MAIL_REDACT,
    KEY_MAIL_SECURITY,
    KEY_MAIL_TO,
    KEY_MAIL_USERNAME,
    KEY_MEOW_NICKNAME,
    KEY_MEOW_REDACT,
    KEY_ON_SUCCESS,
    KEY_SKIPPED_THRESHOLD,
    KEY_WEBHOOK_REDACT,
    KEY_WEBHOOK_TEMPLATE,
    KEY_WEBHOOK_URL,
    Notifier,
    _parse_bool,
    _parse_mail_port,
    _parse_mail_security,
    _parse_threshold,
)
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs, _normalize_duration, failure_reason
from app.utils.time import parse_utc_bound

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
# A2 时间线档（restic duration）与 --keep-tag 清单：同属 G3「至少一个规则」门与
# build_forget_args 映射面（restic ExpirePolicy.Empty 亦含 Within/Tags 维度）
_KEEP_WITHIN_FIELDS: tuple[str, ...] = (
    "keep_within",
    "keep_within_hourly",
    "keep_within_daily",
    "keep_within_weekly",
    "keep_within_monthly",
    "keep_within_yearly",
)
_KEEP_TAG_FIELD = "keep_tags"


def _rule_active(value: object) -> bool:
    """保留规则是否生效：None/空串/空列表/0 = 未启用（A2 起时长档与 tag 档入列）。

    计数档 int 的 0 口径由调用侧（job_runner ``_as_keep_rule``）预先归一为 None，
    此处仍防御性处理（E21 请求 schema 已 ``ge=1``，0 到不了本函数）。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | tuple):
        return bool(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value > 0
    return True


def require_keep_rule(*keep_values: object) -> None:
    """G3 兜底：至少一个保留规则显式给出，否则 422。

    Args:
        keep_values: 全部保留规则的有效值（None/空 = 未指定；计数档 int、
            时长档 str、tag 档 list 混合传入）。

    Raises:
        ValidationError: 全部未指定。
    """
    if all(not _rule_active(value) for value in keep_values):
        raise ValidationError(
            "forget 必须至少指定一个保留规则（keep_last/keep_hourly/…、keep_within/keep_tags），"
            "否则会删除分组内全部快照",
            data={"keep_fields": [*_KEEP_FIELDS, *_KEEP_WITHIN_FIELDS, _KEEP_TAG_FIELD]},
        )


def build_forget_args(
    *,
    tag: str | None,
    group_by: str,
    keep: dict[str, object],
    dry_run: bool,
    prune: bool,
) -> WhitelistArgs:
    """从请求参数构造 forget 的白名单参数（E21 预览与 E12 执行共用一份映射）。

    Args:
        tag: 限定 tag（None = 不加 --tag）。
        group_by: 分组维度。
        keep: 保留规则具名字段 → 值映射（计数档 int|None / 时长档 str|None /
            tag 档 list；缺失键按未启用处理）。
        dry_run: 预览恒 True；执行恒 False。
        prune: 是否随 forget --prune 回收数据。
    """
    return WhitelistArgs(
        tags=(tag,) if tag else (),
        group_by=group_by,
        keep_last=keep.get("keep_last"),  # type: ignore[arg-type]
        keep_hourly=keep.get("keep_hourly"),  # type: ignore[arg-type]
        keep_daily=keep.get("keep_daily"),  # type: ignore[arg-type]
        keep_weekly=keep.get("keep_weekly"),  # type: ignore[arg-type]
        keep_monthly=keep.get("keep_monthly"),  # type: ignore[arg-type]
        keep_yearly=keep.get("keep_yearly"),  # type: ignore[arg-type]
        keep_within=keep.get("keep_within"),  # type: ignore[arg-type]
        keep_within_hourly=keep.get("keep_within_hourly"),  # type: ignore[arg-type]
        keep_within_daily=keep.get("keep_within_daily"),  # type: ignore[arg-type]
        keep_within_weekly=keep.get("keep_within_weekly"),  # type: ignore[arg-type]
        keep_within_monthly=keep.get("keep_within_monthly"),  # type: ignore[arg-type]
        keep_within_yearly=keep.get("keep_within_yearly"),  # type: ignore[arg-type]
        keep_tags=tuple(keep.get("keep_tags") or ()),  # type: ignore[arg-type]
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
    repo_id: str = Depends(current_repo_id),
    session: SessionContext = Depends(require_session),
) -> ForgetPreviewResponse:
    """``forget --dry-run --json`` → 将被裁剪的快照列表（只读，不落任何裁剪）。

    ``tag`` 传给 restic ``--tag``（与 E17 的 Python 侧过滤不同：forget 的范围限定
    本来就是 restic 语义，tag 值经 :func:`_assert_safe_value` 白名单校验）。
    """
    del session
    keep = {field: getattr(payload, field) for field in _KEEP_FIELDS}
    # A2 时长档：拼装层归一（形态非法 422；全零/空 = 未启用 → None）后再过 G3 门，
    # 避免「0d」这类空策略穿过门后由 restic 端 Fatal（静默防线前移）
    within = {
        field: _normalize_duration(getattr(payload, field), field) for field in _KEEP_WITHIN_FIELDS
    }
    keep_tags = tuple(str(item).strip() for item in payload.keep_tags if str(item).strip())
    require_keep_rule(*keep.values(), *within.values(), keep_tags)
    keep_all: dict[str, object] = {**keep, **within, _KEEP_TAG_FIELD: keep_tags}

    restic: ResticCmd = request.app.state.restic
    parser = request.app.state.parser
    mapper: PathMapper = request.app.state.path_mapper
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    audit = request.app.state.audit

    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库管理页填写仓库地址）")

    args = build_forget_args(
        tag=payload.tag,
        group_by=payload.group_by,
        keep=keep_all,
        dry_run=True,  # 预览恒只读（模块 docstring 约定①），调用方无法经本端点传 False
        prune=False,  # dry-run 与 --prune 并存无意义，prune 只属于确认后的执行
    )
    argv = restic.build("forget", args=args)
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
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
    # A2：keep_tags 的回显键是 params["keep_tags"]（与 filter 的 "tags" 区分，
    # JobRunner forget 分支分别消费）；时长档原样回传归一后的串。
    echo_params: dict[str, object] = {"group_by": payload.group_by, "prune": payload.prune}
    if payload.tag:
        echo_params["tags"] = [payload.tag]
    for field, value in keep_all.items():
        if _rule_active(value):
            echo_params[field] = list(value) if isinstance(value, tuple) else value

    await audit.record(
        AUDIT_FORGET_PREVIEW,
        target="forget",
        detail={
            "tag": payload.tag,
            "remove_count": len(items),
            "prune": payload.prune,
            "keep_fields": sorted(field for field, value in keep_all.items() if _rule_active(value)),
        },
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


# ---------------------------------------------------------------- E23/E24 通知（R-018）

def _bool_value(value: bool) -> str:
    """布尔 → config 存储形态（"1"/"0"）。"""
    return "1" if value else "0"


@router.get(
    "/notify",
    response_model=NotifyConfigResponse,
    summary="E23 通知配置读取",
    dependencies=_PROTECTED,
)
async def get_notify(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> NotifyConfigResponse:
    """通知配置：敏感 URL 只回 ``*_set`` 布尔（不回明文），其余回现值（未配置回缺省）。"""
    del session
    config: ConfigRepo = request.app.state.config_repo
    return NotifyConfigResponse(
        healthchecks_url_set=bool(((await config.get_value(KEY_HEALTHCHECKS_URL)) or "").strip()),
        webhook_url_set=bool(((await config.get_value(KEY_WEBHOOK_URL)) or "").strip()),
        webhook_template=(await config.get_value(KEY_WEBHOOK_TEMPLATE)) or "",
        meow_nickname=(await config.get_value(KEY_MEOW_NICKNAME)) or "",
        on_success=_parse_bool(await config.get_value(KEY_ON_SUCCESS), default=False),
        skipped_threshold=_parse_threshold(await config.get_value(KEY_SKIPPED_THRESHOLD)),
        webhook_redact=_parse_bool(await config.get_value(KEY_WEBHOOK_REDACT), default=DEFAULT_WEBHOOK_REDACT),
        meow_redact=_parse_bool(await config.get_value(KEY_MEOW_REDACT), default=DEFAULT_MEOW_REDACT),
        # C2 SMTP（密码只回 mail_password_set；端口/加密方式回退解析后的合法值）
        mail_host=((await config.get_value(KEY_MAIL_HOST)) or "").strip(),
        mail_port=_parse_mail_port(await config.get_value(KEY_MAIL_PORT)),
        mail_username=((await config.get_value(KEY_MAIL_USERNAME)) or "").strip(),
        mail_password_set=bool((await config.get_value(KEY_MAIL_PASSWORD)) or ""),
        mail_from=((await config.get_value(KEY_MAIL_FROM)) or "").strip(),
        mail_to=((await config.get_value(KEY_MAIL_TO)) or "").strip(),
        mail_security=_parse_mail_security(await config.get_value(KEY_MAIL_SECURITY)),
        mail_redact=_parse_bool(await config.get_value(KEY_MAIL_REDACT), default=DEFAULT_MAIL_REDACT),
    )


@router.put(
    "/notify",
    response_model=OkResponse,
    summary="E23 通知配置写入",
    dependencies=_PROTECTED,
)
async def put_notify(
    payload: NotifyConfigPatch,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """批量写入通知配置（三态语义见 :class:`NotifyConfigPatch`）；入审计 ``notify.update``。"""
    del session
    config: ConfigRepo = request.app.state.config_repo
    patch: dict[str, str] = {}
    if payload.healthchecks_url is not None:
        patch[KEY_HEALTHCHECKS_URL] = payload.healthchecks_url.strip()
    if payload.webhook_url is not None:
        patch[KEY_WEBHOOK_URL] = payload.webhook_url.strip()
    if payload.webhook_template is not None:
        patch[KEY_WEBHOOK_TEMPLATE] = payload.webhook_template
    if payload.meow_nickname is not None:
        patch[KEY_MEOW_NICKNAME] = payload.meow_nickname.strip()
    if payload.on_success is not None:
        patch[KEY_ON_SUCCESS] = _bool_value(payload.on_success)
    if payload.skipped_threshold is not None:
        patch[KEY_SKIPPED_THRESHOLD] = str(int(payload.skipped_threshold))
    if payload.webhook_redact is not None:
        patch[KEY_WEBHOOK_REDACT] = _bool_value(payload.webhook_redact)
    if payload.meow_redact is not None:
        patch[KEY_MEOW_REDACT] = _bool_value(payload.meow_redact)
    # C2 SMTP（password 三态：None=不变 / ""=清除 / 非空=设置；敏感键不回读）
    if payload.mail_host is not None:
        patch[KEY_MAIL_HOST] = payload.mail_host.strip()
    if payload.mail_port is not None:
        patch[KEY_MAIL_PORT] = str(int(payload.mail_port))
    if payload.mail_username is not None:
        patch[KEY_MAIL_USERNAME] = payload.mail_username.strip()
    if payload.mail_password is not None:
        patch[KEY_MAIL_PASSWORD] = payload.mail_password
    if payload.mail_from is not None:
        patch[KEY_MAIL_FROM] = payload.mail_from.strip()
    if payload.mail_to is not None:
        patch[KEY_MAIL_TO] = payload.mail_to.strip()
    if payload.mail_security is not None:
        patch[KEY_MAIL_SECURITY] = payload.mail_security
    if payload.mail_redact is not None:
        patch[KEY_MAIL_REDACT] = _bool_value(payload.mail_redact)
    if not patch:
        raise ValidationError("patch 不能为空（至少一个字段待更新）")

    await config.set_many(patch)
    keys = sorted(patch.keys())
    await request.app.state.audit.record(
        AUDIT_NOTIFY_UPDATE, target="notify", detail={"keys": keys, "count": len(keys)}, result="ok"
    )
    return OkResponse()


@router.post(
    "/notify/test",
    response_model=NotifyTestResponse,
    summary="E24 测试消息",
    dependencies=_PROTECTED,
)
async def test_notify(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> NotifyTestResponse:
    """向全部已配置渠道发测试消息，逐渠道回结果（失败必须带原因，禁止静默）。"""
    del session
    notifier: Notifier = request.app.state.notifier
    ok, latency_ms, results, reason = await notifier.send_test()
    await request.app.state.audit.record(
        AUDIT_NOTIFY_TEST,
        target="notify",
        detail={"ok": ok, "channels": [item.channel for item in results]},
        result="ok" if ok else "failed",
    )
    return NotifyTestResponse(
        ok=ok,
        latency_ms=latency_ms,
        channels=[NotifyTestChannel(channel=item.channel, ok=item.ok, reason=item.reason) for item in results],
        reason=reason,
    )


# ---------------------------------------------------------------- E26 审计日志查询（R-020，批次 A）


@router.get(
    "/audit",
    response_model=AuditPageResponse,
    summary="E26 审计日志查询（只读，append-only）",
    dependencies=_PROTECTED,
)
async def query_audit(
    request: Request,
    action: str | None = Query(default=None, description="动作名前缀匹配（如 restore.；通配符自动转义）"),
    result: str | None = Query(default=None, description="结果过滤：ok/denied/failed"),
    since: str | None = Query(default=None, description="ts 下界（ISO 8601，含时区归一 UTC）"),
    until: str | None = Query(default=None, description="ts 上界（ISO 8601，含时区归一 UTC）"),
    limit: int = Query(default=50, ge=1, le=HISTORY_QUERY_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(require_session),
) -> AuditPageResponse:
    """审计日志分页查询（S6：只读端点，无 PUT/DELETE；查询本身不留痕）。

    ``action`` 为**前缀**匹配：用户输入中的 ``%``/``_``/``\\`` 先转义再拼 LIKE
    （防通配注入越权），右锚定命中 ``idx_audit_action`` 索引；``since``/``until``
    经 :func:`parse_utc_bound` 归一 UTC（C8）；``result`` 走 AuditResult 白名单。
    响应附 ``actions``（去重动作名枚举，repo 层 DISTINCT 查询，前端筛选下拉数据源）。
    """
    del session
    resolved_result: str | None = None
    if result:
        try:
            resolved_result = AuditResult(result).value
        except ValueError as exc:
            allowed = [item.value for item in AuditResult]
            raise ValidationError(
                "result 仅支持 " + "/".join(allowed),
                data={"result": result, "allowed": allowed},
            ) from exc
    since_bound = parse_utc_bound(since)
    if since and since_bound is None:
        raise ValidationError("since 须为 ISO 8601 时间", data={"field": "since", "value": since})
    until_bound = parse_utc_bound(until)
    if until and until_bound is None:
        raise ValidationError("until 须为 ISO 8601 时间", data={"field": "until", "value": until})

    repo: AuditRepo = request.app.state.audit
    rows, total = await repo.query(
        action_prefix=action.strip() if action else None,
        result=resolved_result,
        since=since_bound,
        until=until_bound,
        limit=limit,
        offset=offset,
    )
    return AuditPageResponse(
        items=[
            AuditOut(
                id=row.id,
                ts=row.ts,
                action=row.action,
                target=row.target,
                detail=row.detail,
                result=row.result,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        actions=await repo.distinct_actions(),
    )


# ---------------------------------------------------------------- E25 缓存维护（R-021，批次 A）


@router.get(
    "/cache",
    response_model=CacheUsageResponse,
    summary="E25 缓存占用查询",
    dependencies=_PROTECTED,
)
async def cache_usage(request: Request) -> CacheUsageResponse:
    """restic 缓存目录占用（du 在 to_thread，超时回 truncated 部分值，30s 结果缓存）。"""
    usage = await request.app.state.cache_service.usage()
    return CacheUsageResponse(
        path=usage.path,
        size_bytes=usage.size_bytes,
        items_count=usage.items_count,
        measured_at=usage.measured_at,
        truncated=usage.truncated,
    )


@router.post(
    "/cache/clear",
    response_model=OkResponse,
    summary="E25 清空 restic 缓存",
    dependencies=_PROTECTED,
)
async def cache_clear(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> OkResponse:
    """清空 restic 缓存目录内容（危险操作，S6 埋点 ``cache.clear``）。

    护栏（Q15 基线）：存在 running/pending 任务时 409 ``JOB_CONFLICT``（data 带
    ``running_job_id``）——restic 进程正持有缓存文件，此时清空会破坏进行中的任务。
    清空仅触碰 ``settings.cache_dir`` 白名单内的一级子项（S1）。
    """
    del session
    active = await request.app.state.job_repo.list_active()
    if active:
        raise JobConflictError(data={"running_job_id": active[0].id})
    cache_dir = str(request.app.state.settings.cache_dir)
    await request.app.state.cache_service.clear()
    await request.app.state.audit.record(
        AUDIT_CACHE_CLEAR,
        target=cache_dir,
        detail={"path": cache_dir},
        result="ok",
    )
    return OkResponse()
