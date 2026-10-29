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
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_CACHE_CLEAR,
    AUDIT_CONFIG_EXPORT,
    AUDIT_CONFIG_IMPORT,
    AUDIT_COPY_RUN,
    AUDIT_FORGET_PREVIEW,
    AUDIT_KEY_ROTATE,
    AUDIT_NOTIFY_TEST,
    AUDIT_NOTIFY_UPDATE,
    AUDIT_PRUNE_PREVIEW,
    COPY_LOG_KEEP,
    FORGET_PREVIEW_TIMEOUT_SECONDS,
    HISTORY_QUERY_MAX_LIMIT,
    KEY_OPERATION_TIMEOUT_SECONDS,
    MANUAL_COPY_TIMEOUT_SECONDS,
    PASSFILE_STATUS_CONFIGURED,
    PRUNE_PREVIEW_TIMEOUT_SECONDS,
    SNAPSHOT_LIST_TIMEOUT_SECONDS,
)
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.repo_repo import DEFAULT_REPO_ID, ReposRepo
from app.db.repositories.task_repo import TaskRepo
from app.dependencies import current_repo_id
from app.errors import (
    CopyNoSnapshotError,
    CopySameRepoError,
    CopyTargetUnusableError,
    CredentialDecryptError,
    EnvOverriddenError,
    JobConflictError,
    ReadonlyKeyError,
    ResticFailedError,
    ValidationError,
)
from app.routers.repos import invalidate_repo_status_cache
from app.schemas.api_schemas import (
    AuditArchiveItem,
    AuditOut,
    AuditPageResponse,
    CacheUsageResponse,
    ConfigExportRequest,
    ConfigImportReport,
    ConfigImportRequest,
    ForgetPreviewRequest,
    ForgetPreviewResponse,
    ForgetPreviewSnapshot,
    KeyRotateResponse,
    ManualCopyRequest,
    ManualCopyResponse,
    NotifyConfigPatch,
    NotifyConfigResponse,
    NotifyTestChannel,
    NotifyTestResponse,
    OkResponse,
    PrunePreviewResponse,
    RepoKeyRotateRequest,
)
from app.schemas.enums import AuditResult
from app.services.config_transfer import (
    PACK_KIND_TASK,
    ImportReport,
    build_export_pack,
    creds_to_import,
    mirror_ids_for_import,
    notify_pairs_to_import,
    parse_export_pack,
    task_values_from_pack,
)
from app.services.env_sync import load_credentials, passfile_path, passfile_status
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
    KEY_MEOW_HTML_HEIGHT,
    KEY_MEOW_IMG_URL,
    KEY_MEOW_MSG_TYPE,
    KEY_MEOW_NICKNAME,
    KEY_MEOW_PLAN,
    KEY_MEOW_REDACT,
    KEY_MEOW_URL,
    KEY_ON_SUCCESS,
    KEY_SKIPPED_THRESHOLD,
    KEY_WEBHOOK_REDACT,
    KEY_WEBHOOK_TEMPLATE,
    KEY_WEBHOOK_URL,
    Notifier,
    _parse_bool,
    _parse_html_height,
    _parse_mail_port,
    _parse_mail_security,
    _parse_meow_msg_type,
    _parse_meow_plan,
    _parse_threshold,
)
from app.services.parser import OutputParser
from app.services.path_mapper import PathMapper
from app.services.restic import (
    ResticCmd,
    WhitelistArgs,
    _normalize_duration,
    failure_reason,
    secondary_repo_env,
)
from app.utils.time import now_utc, parse_utc_bound, utc_iso

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


# ---------------------------------------------------------------- R-016 独立回收预览（restic prune --dry-run）

_SIZE_FACTORS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
    "PIB": 1024**5,
    "EIB": 1024**6,
}


def _human_to_bytes(value: float, unit: str) -> int:
    """把 restic 的 humanized 体积（如 ``568.123 MiB``）转字节。"""
    return int(value * _SIZE_FACTORS.get(unit.upper(), 1))


def _parse_prune_preview(lines: list[str]) -> dict[str, object]:
    """解析 ``restic prune --dry-run`` 输出，提取待删块数与可释放字节。

    restic 的 dry-run 措辞随版本浮动（真机采集：本仓 0.18.0），单位口径用多模式
    正则容错匹配：
    - 待删块数：``to delete: N packs``（0.15-0.16）/ ``to delete: N blobs``
      （0.17+，本仓口径）/ ``will delete|remove N packs``；
    - 释放量：``total prune: N blobs / X`` / ``this removes N blobs / X`` /
      ``will free X after this``，体积单位兼容裸 ``B``（如 ``0 B``）。
    都不命中 → 视为无需回收（clean）。
    """
    text = "\n".join(lines)
    packs = None
    for pat in (
        r"to delete:\s*(\d+)\s*(?:packs|blobs)",
        r"will (?:delete|remove)\s*(\d+)\s*(?:packs|blobs)",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            packs = int(m.group(1))
            break

    freed_bytes: int | None = None
    freed_text: str | None = None
    for pat in (
        r"total prune:\s*.*?/\s*([\d.]+)\s*((?:[KMGTPE]i?)?B)",
        r"this removes\s*\d+\s*blobs\s*/\s*([\d.]+)\s*((?:[KMGTPE]i?)?B)",
        r"will free\s*([\d.]+)\s*((?:[KMGTPE]i?)?B)\s*after this",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            freed_text = f"{m.group(1)} {m.group(2)}"
            freed_bytes = _human_to_bytes(float(m.group(1)), m.group(2))
            break

    clean = (packs or 0) == 0 and freed_bytes in (None, 0)
    return {
        "would_remove_packs": packs or 0,
        "freed_bytes": freed_bytes,
        "freed_text": freed_text,
        "clean": clean,
    }


@router.post(
    "/prune/preview",
    response_model=PrunePreviewResponse,
    summary="R-016 独立回收预览（restic prune --dry-run）",
    dependencies=_PROTECTED,
)
async def preview_prune(
    request: Request,
    repo_id: str = Depends(current_repo_id),
    session: SessionContext = Depends(require_session),
) -> PrunePreviewResponse:
    """``prune --dry-run`` 只读探测：回答「仓库里有没有需要 prune 的数据」。

    与 E21 forget 预览同先例——只读、不落任何删除；实际回收必须经 E12
    ``{"type":"prune"}``（确认门在 UI，执行入审计 ``prune.run``）。预览端点恒以
    ``dry_run=True``（调用方无法覆盖），restic 0.15+ 支持 ``--dry-run``（本仓 0.18.0）。
    """
    del session
    restic: ResticCmd = request.app.state.restic
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    audit = request.app.state.audit

    repo = await repos_repo.get(repo_id)
    repo_url = (repo.url if repo is not None else None) or ""
    if not repo_url:
        raise ResticFailedError("仓库地址未配置（请先在仓库管理页填写仓库地址）")

    args = WhitelistArgs(dry_run=True)  # 仅只读探测，prune 本期不带其它可调参数
    argv = restic.build("prune", args=args)
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
    probe = await restic.run_json(argv, env=env, timeout_seconds=PRUNE_PREVIEW_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, PRUNE_PREVIEW_TIMEOUT_SECONDS)
        logger.warning("prune 预览探测失败：%s", reason)
        raise ResticFailedError(reason, exit_code=probe.exit_code)

    parsed = _parse_prune_preview(probe.lines)
    await audit.record(
        AUDIT_PRUNE_PREVIEW,
        target="prune",
        detail={"would_remove_packs": parsed["would_remove_packs"], "clean": parsed["clean"]},
        result="ok",
    )
    return PrunePreviewResponse(
        would_remove_packs=parsed["would_remove_packs"],  # type: ignore[arg-type]
        freed_bytes=parsed["freed_bytes"],  # type: ignore[arg-type]
        freed_text=parsed["freed_text"],  # type: ignore[arg-type]
        clean=parsed["clean"],  # type: ignore[arg-type]
        lines=probe.lines,
    )


# ---------------------------------------------------------------- 批次 E 配置导入/导出（D4）


async def _set_config_pairs(
    config_repo: ConfigRepo, pairs: list[tuple[str, str]]
) -> tuple[int, list[dict[str, str]]]:
    """逐键写入配置（env 覆盖/只读键捕获为警告，不中断整批）。"""
    written = 0
    warnings: list[dict[str, str]] = []
    for key, value in pairs:
        try:
            await config_repo.set(key, value)
            written += 1
        except (EnvOverriddenError, ReadonlyKeyError) as exc:
            warnings.append({"key": key, "reason": str(exc)[:200]})
    return written, warnings


@router.post("/config/export")
async def export_config(
    payload: ConfigExportRequest,
    request: Request,
    _session: SessionContext = Depends(require_session),
    _boot: None = Depends(require_bootstrap_done),
) -> Response:
    """批次 E：配置导出（仓清单 + 任务 + behavior/notify 配置 + 加密凭据段）。

    响应为可下载 JSON 文件；凭据经导出口令 PBKDF2-HMAC-SHA256 600k 迭代 + AES-256-GCM 加密（S3：明文
    凭据不出内存，不进日志/审计）。导出留审计 ``config.export``（只含计数）。
    """
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    task_repo: TaskRepo = request.app.state.task_repo
    audit = request.app.state.audit

    repos = await repos_repo.list_all()
    repo_entries = [
        {"id": repo.id, "name": repo.name, "url": repo.url, "host_tag": repo.host_tag}
        for repo in repos
    ]
    tasks: list[dict[str, Any]] = []
    for repo in repos:
        for task in await task_repo.list_all(repo.id):
            detail = await task_repo.get_dict(task.id)
            if detail is not None:
                tasks.append(detail)
    config_rows = [(row.key, row.value or "") for row in await config_repo.list_all()]
    pack = build_export_pack(
        repos=repo_entries,
        tasks=tasks,
        config_rows=config_rows,
        password=payload.password,
        created_at=utc_iso(),
    )
    await audit.record(
        AUDIT_CONFIG_EXPORT,
        target="config",
        detail={
            "repos": len(repo_entries),
            "tasks": len(tasks),
            "config_keys": len(config_rows),
            "creds": sum(1 for key, _ in config_rows if key.startswith("cred.")),
        },
        result="ok",
    )
    stamp = str(pack["created_at"])[:10]
    return Response(
        content=json.dumps(pack, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="restic-web-config-{stamp}.json"'},
    )


@router.post("/config/import")
async def import_config(
    payload: ConfigImportRequest,
    request: Request,
    _session: SessionContext = Depends(require_session),
    _boot: None = Depends(require_bootstrap_done),
) -> ConfigImportReport:
    """批次 E：配置导入（先仓后任务；冲突默认跳过，``replace_existing`` 整体替换）。

    **按包形态分流（方案 §4.5.9 R-5）**：``kind=backup-task``（单任务导出包）只按任务维度
    落地——**不写全局 notify/behavior**（那是全量包的语义），且**不套用全量包的覆盖语义**
    （即使带 ``replace_existing=true`` 也不覆盖同仓同名任务，一律跳过并在报告里说明）。
    ``kind=full`` / 旧包（无 kind 字段）走原全量语义。

    - 仓按 id 匹配：缺失新建 / 同 id 同 url 复用 / 同 id 异 url 跳过（覆盖则更新）；
    - 任务按 ``(repo_id, name)`` 匹配：缺失新建 / 已存在跳过（全量包覆盖则更新）；
      ``mirror_repo_ids`` 接上前校验「id 在包内且 url 一致」，否则置空 + 警告；
    - 凭据（``cred.*`` 与敏感 ``notify.*``）从加密段解出后写入（ConfigRepo 透明加密）；
    - 导入留审计 ``config.import``（detail 只含 kind/计数与 replace_existing，绝不含凭据/包原文）。
    """
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    task_repo: TaskRepo = request.app.state.task_repo
    audit = request.app.state.audit

    parsed = parse_export_pack(payload.pack, payload.password)
    kind = str(parsed["kind"])
    is_task_pack = kind == PACK_KIND_TASK
    report = ImportReport()
    pack_urls = {str(r.get("id") or ""): r.get("url") for r in parsed["repos"]}
    repo_final_urls: dict[str, str | None] = {}
    imported_repo_ids: list[str] = []

    # 1. 仓库（先仓后任务：mirror 引用与任务归属都依赖仓落位）
    for entry in parsed["repos"]:
        rid = str(entry.get("id") or "").strip()
        if not rid:
            continue
        existing = await repos_repo.get(rid)
        pack_url = entry.get("url") or None
        if existing is None:
            await repos_repo.create(
                rid, str(entry.get("name") or rid), url=pack_url, host_tag=entry.get("host_tag")
            )
            report.repos_created.append(rid)
            repo_final_urls[rid] = pack_url
        elif payload.replace_existing:
            await repos_repo.update(
                rid, name=str(entry.get("name") or existing.name), url=pack_url,
                host_tag=entry.get("host_tag"),
            )
            report.repos_updated.append(rid)
            repo_final_urls[rid] = pack_url
        elif (existing.url or None) == pack_url:
            report.repos_reused.append(rid)
            repo_final_urls[rid] = existing.url
        else:
            report.repos_skipped.append({"id": rid, "reason": "同 id 仓库已存在且 url 不同"})
            repo_final_urls[rid] = existing.url
        imported_repo_ids.append(rid)

    # 2. 凭据（cred.*；失败键捕获为警告——env 覆盖/只读键不中断整批）
    cred_pairs = creds_to_import(parsed["creds"], imported_repo_ids)
    creds_written, creds_warnings = await _set_config_pairs(config_repo, cred_pairs)
    report.creds_imported = creds_written
    for warning in creds_warnings:
        report.mirror_warnings.append({"mirror_id": "(config)", "reason": f"{warning['key']}：{warning['reason']}"})

    # 3. 任务（按 (repo_id, name) 匹配；mirror 引用按裁决 2 细则校验）
    existing_names: dict[str, dict[str, Any]] = {}
    for rid in imported_repo_ids:
        for task in await task_repo.list_all(rid):
            existing_names[f"{rid}|{task.name}"] = {"id": task.id, "name": task.name}
    for task in parsed["tasks"]:
        rid = str(task.get("repo_id") or "default")
        name = str(task.get("name") or "").strip()
        if not name:
            continue
        if rid not in repo_final_urls:
            report.tasks_skipped.append({"name": name, "reason": "所属仓库不可用（未导入或被跳过）"})
            continue
        key = f"{rid}|{name}"
        mirror_ids, warnings = mirror_ids_for_import(
            [str(m) for m in (task.get("mirror_repo_ids") or [])], pack_urls, repo_final_urls
        )
        for warning in warnings:
            report.mirror_warnings.append(
                {"mirror_id": warning["mirror_id"], "reason": f"任务「{name}」{warning['reason']}"}
            )
        values = task_values_from_pack(task, rid, mirror_ids)
        current = existing_names.get(key)
        if current is None:
            await task_repo.create(values)
            report.tasks_created.append(name)
        elif payload.replace_existing and not is_task_pack:
            await task_repo.update(int(current["id"]), values)
            report.tasks_updated.append(name)
        elif is_task_pack:
            report.tasks_skipped.append(
                {"name": name, "reason": "单任务包不覆盖已有任务（如需替换请在任务页手工编辑）"}
            )
        else:
            report.tasks_skipped.append({"name": name, "reason": "同仓同名任务已存在（如需替换请勾选覆盖导入）"})

    # 4. 通知配置（明文键 + 加密段敏感键合并写入）
    #    单任务包不带全局设置：整段跳过——否则空 notify 段会被读成「把全局通知清空」的意图
    #    （方案 §4.5.9 R-5：单任务包绝不套用全量包语义）。
    if is_task_pack:
        report.notify_updated = 0
    else:
        notify_pairs = notify_pairs_to_import(parsed["notify"], parsed["creds"])
        notify_written, notify_warnings = await _set_config_pairs(config_repo, notify_pairs)
        report.notify_updated = notify_written
        for warning in notify_warnings:
            report.mirror_warnings.append({"mirror_id": "(config)", "reason": f"{warning['key']}：{warning['reason']}"})

    await audit.record(
        AUDIT_CONFIG_IMPORT,
        target="config",
        detail={
            "kind": kind,
            "replace_existing": payload.replace_existing,
            "repos_created": len(report.repos_created),
            "repos_updated": len(report.repos_updated),
            "repos_skipped": len(report.repos_skipped),
            "tasks_created": len(report.tasks_created),
            "tasks_updated": len(report.tasks_updated),
            "tasks_skipped": len(report.tasks_skipped),
            "creds": report.creds_imported,
            "notify": report.notify_updated,
            "mirror_warnings": len(report.mirror_warnings),
        },
        result="ok",
    )
    return ConfigImportReport(**report.to_dict())


# ---------------------------------------------------------------- C4 密钥轮换 + 手动 copy（搬家）

async def _run_restic_copy(
    restic: ResticCmd,
    *,
    source_url: str,
    source_repo_id: str,
    target_url: str,
    target_repo_id: str,
    target_passfile: Path,
    tags: tuple[str, ...],
    config_repo: ConfigRepo,
    timeout_seconds: float,
    log_dir: Path,
) -> tuple[int, str, str | None]:
    """执行一次 restic copy（源→目标）：``(退出码, 最后一条输出, 留存日志文件名)``。

    copy 无 JSON 输出，逐行落临时文件 + 取末行作失败线索；超时按中断处理（restic
    copy 增量可续传，下次运行补齐，无需手工补）。目的仓的 url 与口令文件**绝不进
    argv**——与源仓同口径走 env（见 :func:`secondary_repo_env`）。

    输出**不再用完即删**：copy 是唯一没有 job 日志的长操作，事后判断「复制了几条 /
    卡在哪」只能靠原文，故整体移入 ``log_dir``（只回文件名，容器路径不出 API）。
    留存失败绝不影响 copy 结论——只降级为 ``log_name=None``。
    """
    env_extras = await load_credentials(config_repo, source_repo_id)
    env_extras.update(secondary_repo_env(target_url, target_passfile))
    args = WhitelistArgs(json_output=False, tags=tags)
    argv = restic.build("copy", args=args, env_extras=env_extras)
    env = restic.build_env(repo_url=source_url, repo_id=source_repo_id, env_extras=env_extras)
    fd, log_path = tempfile.mkstemp(prefix="restic-copy-", suffix=".log")
    os.close(fd)
    tmp = Path(log_path)
    exit_code = -1
    last_line = ""
    try:
        proc = await restic.spawn(argv, tmp, env=env)
        async for line in proc.lines():
            last_line = line
        try:
            exit_code = await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            exit_code = -1
            last_line = last_line or "copy 超时"
    finally:
        log_name = _retain_copy_log(
            tmp, log_dir, source_repo_id=source_repo_id, target_repo_id=target_repo_id
        )
    return exit_code, last_line, log_name


def _retain_copy_log(
    tmp: Path, log_dir: Path, *, source_repo_id: str, target_repo_id: str
) -> str | None:
    """把临时输出移入留存目录并返回文件名；任何失败都降级为 ``None``（不影响结论）。"""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # UTC 文件名：与容器时区无关，跨时区排障可直接排序比对
        name = f"copy-{source_repo_id}-to-{target_repo_id}-{now_utc():%Y%m%dT%H%M%SZ}.log"
        shutil.move(str(tmp), log_dir / name)
    except OSError:
        logger.warning("copy 输出留存失败（不影响 copy 结论）：%s", tmp, exc_info=True)
        with contextlib.suppress(OSError):
            tmp.unlink()
        return None
    _prune_copy_logs(log_dir)
    return name


def _prune_copy_logs(log_dir: Path) -> None:
    """只保留最近 :data:`COPY_LOG_KEEP` 个留存文件（按 mtime 倒序；清理失败静默）。"""
    try:
        files = sorted(log_dir.glob("copy-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in files[COPY_LOG_KEEP:]:
        with contextlib.suppress(OSError):
            stale.unlink()


async def _copy_snapshot_ids(
    restic: ResticCmd,
    parser: OutputParser,
    *,
    repo_url: str,
    repo_id: str,
    config_repo: ConfigRepo,
    tags: tuple[str, ...] = (),
) -> tuple[frozenset[str], str | None]:
    """读一个仓库的快照 id 集合（copy 前置预检与后置复核共用）。

    ``restic copy`` 的退出码只表示「进程没报错」：源/目标同仓、源无快照、快照已全部
    存在、``--tag`` 无匹配四种情况都会返回 0 且**零输出**（restic 0.19.1 真机实测）。
    故成功语义必须由快照集合核算补齐——本函数是那一层的唯一读取点。

    ``tags`` 非空时按「包含任一标签」过滤：与本层 argv 拼装同口径（每个标签一条
    ``--tag``，restic 端多次出现取并集），保证预检「有没有可复制的快照」与 copy
    实际过滤条件一致。

    Args:
        restic: restic 命令构造器（S2 白名单唯一拼装点）。
        parser: 输出解析器（快照列表解析的唯一实现）。
        repo_url: 仓库地址。
        repo_id: 仓库 id（口令文件按此派生）。
        config_repo: 凭据来源（与 copy 链路同一份注入）。
        tags: 快照标签过滤（OR 语义）；空 = 不过滤。

    Returns:
        ``(ids, None)`` = 读取成功；``(空集, 原因)`` = 仓库不可读（地址/口令/未初始化）。
        调用方必须区分「仓库里确实没有快照」与「读不出来」——前者 422 提示，后者
        属部署/凭据问题，不能混报成「没有可复制的快照」。
    """
    argv = restic.build("snapshots", args=WhitelistArgs(json_output=True))
    env = restic.build_env(
        repo_url=repo_url, repo_id=repo_id, env_extras=await load_credentials(config_repo, repo_id)
    )
    probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_LIST_TIMEOUT_SECONDS)
    if probe.exit_code != 0:
        reason = failure_reason(probe.exit_code, probe.lines, SNAPSHOT_LIST_TIMEOUT_SECONDS)
        return frozenset(), reason
    records = await asyncio.to_thread(parser.parse_snapshot_list, "\n".join(probe.lines))
    wanted = [record for record in records if record.snapshot_id]
    if tags:
        wanted = [record for record in wanted if any(tag in record.tags for tag in tags)]
    return frozenset(record.snapshot_id for record in wanted), None


@router.post(
    "/repos/rotate-key",
    response_model=KeyRotateResponse,
    summary="C4 仓库密钥轮换（restic key passwd）",
    dependencies=_PROTECTED,
)
async def rotate_repo_key(
    payload: RepoKeyRotateRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> KeyRotateResponse:
    """在旧密码仍有效时把仓库密码改为新密码（restic key passwd）。

    流程：新密码写入临时文件 → restic key passwd --new-password-file <tmp>（旧密码
    由当前口令文件经 env 提供）→ 成功后重写口令文件为新密码 → 审计 key.rotate。
    新密码只经本次请求体，不落库、不回显（S3）。
    """
    del session
    restic: ResticCmd = request.app.state.restic
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    audit = request.app.state.audit
    settings = request.app.state.settings

    repo = await repos_repo.get(payload.repo_id)
    if repo is None or not (repo.url or "").strip():
        raise ValidationError("目标仓库不存在或未配置地址", data={"repo_id": payload.repo_id})
    if passfile_status(settings, payload.repo_id) != PASSFILE_STATUS_CONFIGURED:
        raise ValidationError("目标仓库未设置密码（请先设置仓库密码）", data={"repo_id": payload.repo_id})

    # 新密码只落临时文件，绝不进 argv / 日志 / 审计（S3）
    fd, pw_path = tempfile.mkstemp(prefix="restic-keypass-", suffix=".tmp")
    os.write(fd, payload.new_password.encode("utf-8"))
    os.close(fd)
    _, log_path = tempfile.mkstemp(prefix="restic-keyrun-", suffix=".log")
    os.close(_)
    try:
        env = restic.build_env(
            repo_url=repo.url,
            repo_id=payload.repo_id,
            env_extras=await load_credentials(config_repo, payload.repo_id),
        )
        args = WhitelistArgs(json_output=False, key_target="passwd", new_password_file=pw_path)
        argv = restic.build("key", args=args, env_extras={})
        proc = await restic.spawn(argv, log_path, env=env)
        last_line = ""
        async for line in proc.lines():
            last_line = line
        try:
            exit_code = await asyncio.wait_for(proc.wait(), timeout=KEY_OPERATION_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            exit_code = -1
    finally:
        for path in (pw_path, log_path):
            with contextlib.suppress(OSError):
                os.unlink(path)

    if exit_code != 0:
        reason = failure_reason(exit_code, [last_line]) if exit_code != -1 else "密钥轮换超时"
        logger.warning("仓库 %s 密钥轮换失败：%s", payload.repo_id, reason)
        raise ResticFailedError(f"密钥轮换失败：{reason}", exit_code=exit_code)

    # R-025 联动（审计 2026-10-18 S-1）：轮换 **default** 仓库 = 凭据加密根（口令文件）
    # 内容变更——存量 enc:v2 凭据用旧密码派生的密钥加密，口令文件一换即全部解不开。
    # 趁口令文件仍是旧内容（restic passwd 已成功、新内容尚未写入），先用旧密钥把
    # 该仓库的敏感键解出（明文只持于内存，S3）；重写口令文件后再经 set() 写侧用
    # 新密钥重加密。仅 default 走此分支：CredentialCrypto 单实例的口令文件恒为
    # default 布局，其它仓库的 rotate 不影响凭据密钥；per-repo crypto 落地时
    # 本分支改为按仓库实例。
    reencrypt_pairs: list[tuple[str, str]] = []
    if payload.repo_id == DEFAULT_REPO_ID:
        crypto = request.app.state.encryption.crypto
        cred_prefix = f"cred.{payload.repo_id}."
        for row in await config_repo.list_all_raw():
            if not (row.key.startswith(cred_prefix) and row.source != "env" and row.value):
                continue
            if not crypto.is_encrypted(row.value):
                continue  # 明文形态（历史上关闭加密的残留），恒可读、无需重加密
            try:
                reencrypt_pairs.append((row.key, crypto.decrypt(row.value)))  # 旧密钥
            except CredentialDecryptError:
                # legacy 密文解不开（口令文件曾被更换）：跳过+告警，恢复路径=经 E07
                # 覆写该键（沿 migrate_encryption 既有语义）
                logger.warning("key.rotate 跳过凭据重加密 %s：旧密文无法解密", row.key)

    # restic 已用新密码重写仓库密钥 → 同步口令文件，后续访问用新密码
    passfile_path(settings, payload.repo_id).write_text(payload.new_password, encoding="utf-8")
    # 口令文件内容已变 → 凭据加密密钥随之切换（crypto 缓存按 stat 自动失效）：
    # 上方快照的明文经 set() 写侧透明加密（新密钥）写回；set 对 source='env' 有
    # 守卫，但 env 行已在收集阶段过滤，不会触达。
    for key, plain in reencrypt_pairs:
        await config_repo.set(key, plain)
    await audit.record(
        AUDIT_KEY_ROTATE,
        target=payload.repo_id,
        detail={
            "repo_id": payload.repo_id,
            "result": "ok",
            "creds_reencrypted": len(reencrypt_pairs),
        },
        result="ok",
    )
    return KeyRotateResponse(ok=True, repo_id=payload.repo_id)


@router.post(
    "/copy",
    response_model=ManualCopyResponse,
    summary="C4 手动 copy（搬家场景）",
    dependencies=_PROTECTED,
)
async def manual_copy(
    payload: ManualCopyRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> ManualCopyResponse:
    """把源仓库快照原样复制到目标仓库（增量）。搬家 / 建异地副本场景。

    源=源仓库(env)，目的=目标仓库(secondary_repo_env)；两仓地址与密码须已配置，
    否则 422。copy 无 JSON 输出，逐行消费；中断安全（restic copy 增量可续传）。

    **成功语义（本次加固）**：``restic copy`` 的退出码只代表「进程没报错」——源/目标
    同仓、源无快照、快照已全部存在、``--tag`` 无匹配这四种情况都返回 0 且**零输出**
    （restic 0.19.1 真机实测；后者是「提示复制完成但目标仓看不到」的直接成因）。故：

    ① 复制前读两侧快照 id 集合，把「注定零复制」的场景前移成 422（同仓 / 源空 /
       目标不可读），不再让用户等一次静默成功；
    ② 复制后复核目标仓，返回 ``copied``（新增条数）与 ``target_total``——``copied=0``
       时界面必须说「未新增」而不是「复制完成」；
    ③ 结束失效 E30 状态缓存与快照目录缓存，否则仓库页数字在 TTL 内仍是旧值。
    """
    del session
    restic: ResticCmd = request.app.state.restic
    parser: OutputParser = request.app.state.parser
    config_repo: ConfigRepo = request.app.state.config_repo
    repos_repo: ReposRepo = request.app.state.repos_repo
    audit = request.app.state.audit

    if payload.source_repo_id == payload.target_repo_id:
        raise ValidationError("源仓库与目标仓库不能相同", data={"repo_ids": [payload.source_repo_id]})

    src = await repos_repo.get(payload.source_repo_id)
    dst = await repos_repo.get(payload.target_repo_id)
    for rid, repo in ((payload.source_repo_id, src), (payload.target_repo_id, dst)):
        if repo is None or not (repo.url or "").strip():
            raise ValidationError("仓库不存在或未配置地址", data={"repo_id": rid})

    tags = tuple(str(t).strip() for t in payload.tags if str(t).strip())

    # ---- 前置预检（D1）：把三类「零复制也报成功」的场景在 copy 之前拦掉 ----------
    src_ids, src_err = await _copy_snapshot_ids(
        restic,
        parser,
        repo_url=src.url,
        repo_id=payload.source_repo_id,
        config_repo=config_repo,
        tags=tags,
    )
    if src_err is not None:
        # 读不出来 ≠ 没有快照：这是部署/凭据问题，按 500 报，不与 422 混报
        raise ResticFailedError(f"源仓库快照列表读取失败：{src_err}")
    if not src_ids:
        raise CopyNoSnapshotError(data={"source_repo_id": payload.source_repo_id, "tags": list(tags)})
    dst_ids, dst_err = await _copy_snapshot_ids(
        restic,
        parser,
        repo_url=dst.url,
        repo_id=payload.target_repo_id,
        config_repo=config_repo,
    )
    if dst_err is not None:
        raise CopyTargetUnusableError(target_repo_id=payload.target_repo_id, reason=dst_err)
    # 快照 ID 有交集 ⇒ 两个记录指向同一仓库：跨仓 copy 必然重新加密生成新 ID
    # （restic 0.19.1 实测 3c3714c7 → 7418c432），ID 完全相同只可能是同一存储。
    overlap = src_ids & dst_ids
    if overlap:
        raise CopySameRepoError(
            data={
                "source_repo_id": payload.source_repo_id,
                "target_repo_id": payload.target_repo_id,
                "overlap": len(overlap),
            },
        )

    exit_code, last_line, log_name = await _run_restic_copy(
        restic,
        source_url=src.url,
        source_repo_id=payload.source_repo_id,
        target_url=dst.url,
        target_repo_id=payload.target_repo_id,
        target_passfile=passfile_path(request.app.state.settings, payload.target_repo_id),
        tags=tags,
        config_repo=config_repo,
        timeout_seconds=MANUAL_COPY_TIMEOUT_SECONDS,
        log_dir=request.app.state.settings.copy_log_dir,
    )

    ok = exit_code == 0
    # ---- 后置复核（D2）：exit 0 ≠ 复制了东西 -----------------------------------
    copied: int | None = None
    target_total: int | None = None
    if ok:
        after_ids, after_err = await _copy_snapshot_ids(
            restic,
            parser,
            repo_url=dst.url,
            repo_id=payload.target_repo_id,
            config_repo=config_repo,
        )
        if after_err is None:
            target_total = len(after_ids)
            copied = len(after_ids - dst_ids)
        # 复核失败只降级字段（copied=None），不改判 ok——命令本身确实成功了

    # 目标仓内容已变（哪怕 exit 0 未新增，也可能补齐了索引）；两个缓存一律失效，
    # 否则仓库页「快照数 / 容量」在 30s TTL 内仍是旧值，用户会以为没复制。
    invalidate_repo_status_cache()

    await audit.record(
        AUDIT_COPY_RUN,
        target=payload.source_repo_id,
        detail={
            "type": "manual",
            "source_repo": payload.source_repo_id,
            "target_repo": payload.target_repo_id,
            "status": "ok" if ok else "failed",
            "exit_code": exit_code,
            "copied": copied,
            "target_total": target_total,
            # 只记文件名（容器形态路径不出 API/审计展示面）；定位靠 data_dir/logs/copy
            "log_name": log_name,
        },
        result="ok" if ok else "failed",
    )
    if not ok:
        message = failure_reason(exit_code, [last_line])
    elif copied == 0:
        message = "未新增快照（目标仓库已包含全部待复制快照）"
    else:
        message = ""
    return ManualCopyResponse(
        ok=ok,
        source_repo_id=payload.source_repo_id,
        target_repo_id=payload.target_repo_id,
        exit_code=exit_code,
        message=message,
        copied=copied,
        target_total=target_total,
        log_name=log_name,
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
        meow_msg_type=_parse_meow_msg_type(await config.get_value(KEY_MEOW_MSG_TYPE)),
        meow_html_height=_parse_html_height(await config.get_value(KEY_MEOW_HTML_HEIGHT)),
        meow_url=((await config.get_value(KEY_MEOW_URL)) or "").strip(),
        meow_img_url=((await config.get_value(KEY_MEOW_IMG_URL)) or "").strip(),
        meow_plan=_parse_meow_plan(await config.get_value(KEY_MEOW_PLAN)),
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
    if payload.meow_msg_type is not None:
        patch[KEY_MEOW_MSG_TYPE] = payload.meow_msg_type
    if payload.meow_html_height is not None:
        patch[KEY_MEOW_HTML_HEIGHT] = str(int(payload.meow_html_height))
    # 可选增强：空串 = 清除（E23 三态写入口径：'' 即清除该键）
    if payload.meow_url is not None:
        patch[KEY_MEOW_URL] = payload.meow_url.strip()
    if payload.meow_img_url is not None:
        patch[KEY_MEOW_IMG_URL] = payload.meow_img_url.strip()
    if payload.meow_plan is not None:
        patch[KEY_MEOW_PLAN] = payload.meow_plan.strip().lower()
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
    action: str | None = Query(
        default=None,
        description="动作名前缀匹配（如 restore.）；逗号分隔多个前缀取并集（如 repo.purge,repo.id_release）",
    ),
    result: str | None = Query(default=None, description="结果过滤：ok/denied/failed"),
    since: str | None = Query(default=None, description="ts 下界（ISO 8601，含时区归一 UTC）"),
    until: str | None = Query(default=None, description="ts 上界（ISO 8601，含时区归一 UTC）"),
    limit: int = Query(default=50, ge=1, le=HISTORY_QUERY_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(require_session),
) -> AuditPageResponse:
    """审计日志分页查询（S6：只读端点，无 PUT/DELETE；查询本身不留痕）。

    ``action`` 为**前缀**匹配，可传**逗号分隔的多个前缀**（取并集，供归档页「操作日志」
    按动作清单查询——几个归档动作名不共享单一前缀）；用户输入中的 ``%``/``_``/``\\``
    先转义再拼 LIKE（防通配注入越权），右锚定命中 ``idx_audit_action`` 索引；
    ``since``/``until`` 经 :func:`parse_utc_bound` 归一 UTC（C8）；``result``
    走 AuditResult 白名单。响应附 ``actions``（去重动作名枚举，repo 层 DISTINCT
    查询，前端筛选下拉数据源）。

    注：导出端点 :func:`export_audit` 仍只接受单个前缀（流式遍历口径未改）。
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
        action_prefix=_split_action_prefixes(action),
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


def _split_action_prefixes(action: str | None) -> list[str] | None:
    """把逗号分隔的动作前缀串切成列表（空串项丢弃）；空 → ``None``（不过滤）。

    多前缀供**归档页「操作日志」**按动作清单查询：释放 ID（``repo.id_release``）、
    销毁数据（``repo.purge``）、恢复写回（``rclone.config_restore``）这几个动作名
    不共享任何单一前缀，逐个查要发 N 个请求且翻页失效，故在服务端取并集。
    单值仍按原口径透传（向后兼容，既有筛选下拉与手输不受影响）。
    """
    if not action:
        return None
    items = [item.strip() for item in action.split(",") if item.strip()]
    return items or None


def _resolve_audit_result(result: str | None) -> str | None:
    """把查询参数 ``result`` 归一为 AuditResult 白名单值；非法值 422。"""
    if not result:
        return None
    try:
        return AuditResult(result).value
    except ValueError as exc:
        allowed = [item.value for item in AuditResult]
        raise ValidationError(
            "result 仅支持 " + "/".join(allowed),
            data={"result": result, "allowed": allowed},
        ) from exc


@router.get(
    "/audit/export",
    summary="E26 审计日志导出（流式 NDJSON，复用当前筛选；S6 只读、不留痕）",
    dependencies=_PROTECTED,
)
async def export_audit(
    request: Request,
    action: str | None = Query(default=None, description="动作名前缀匹配（如 restore.；通配符自动转义）"),
    result: str | None = Query(default=None, description="结果过滤：ok/denied/failed"),
    since: str | None = Query(default=None, description="ts 下界（ISO 8601，含时区归一 UTC）"),
    until: str | None = Query(default=None, description="ts 上界（ISO 8601，含时区归一 UTC）"),
    session: SessionContext = Depends(require_session),
) -> StreamingResponse:
    """流式导出审计日志为 NDJSON（每行一条 JSON，UTF-8，不转义非 ASCII）。

    复用 :meth:`query_audit` 同口径筛选（action 前缀 / result 白名单 / since·until 归一）；
    供前端「导出当前筛选」下载。导出是只读动作，不留审计痕（与查询同口径 S6）。
    """
    del session
    resolved_result = _resolve_audit_result(result)
    since_bound = parse_utc_bound(since)
    if since and since_bound is None:
        raise ValidationError("since 须为 ISO 8601 时间", data={"field": "since", "value": since})
    until_bound = parse_utc_bound(until)
    if until and until_bound is None:
        raise ValidationError("until 须为 ISO 8601 时间", data={"field": "until", "value": until})

    repo: AuditRepo = request.app.state.audit

    async def _stream() -> AsyncIterator[bytes]:
        async for row in repo.iter_rows(
            action_prefix=action.strip() if action else None,
            result=resolved_result,
            since=since_bound,
            until=until_bound,
            batch=2000,
        ):
            obj = {
                "id": row.id,
                "ts": row.ts,
                "action": row.action,
                "target": row.target,
                "detail": row.detail,
                "result": row.result,
                "created_at": row.created_at,
            }
            yield (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="audit-export.ndjson"'},
    )


@router.get(
    "/audit/archive",
    response_model=list[AuditArchiveItem],
    summary="R-020 审计归档列表（只读，裁决3）",
    dependencies=_PROTECTED,
)
async def list_audit_archives(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> list[AuditArchiveItem]:
    """列出 ``audit_archive_dir`` 下的压缩归档文件（``*.jsonl.gz``），供前端只读展示。

    归档文件由每日维护（``retention.cleanup`` 串联的 ``audit.archive``）生成；本端点
    不消费 ``retention.audit_archive_cold_target``（外送冷存由部署侧既有 restic/rclone 完成）。
    """
    del session
    retention = request.app.state.retention
    items = await retention.list_archives()
    return [AuditArchiveItem(**item) for item in items]


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
