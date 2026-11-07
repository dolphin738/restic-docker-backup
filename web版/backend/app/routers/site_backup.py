"""全站数据备份 / 恢复路由（方案 v1.5 §3.2 / §4.2 / §5.2；W4 路由层）。

三端点，全部 ``_PROTECTED``（``require_session`` + ``require_bootstrap_done``）：

- ``POST /api/site-backup/export``  —— 导出加密备份包（附件下载，强制口令）；
- ``POST /api/site-backup/inspect`` —— 只读预检（**零业务写入**，回包摘要 + 目标现状 + 阻碍项）；
- ``POST /api/site-backup/import``  —— 整机替换导入（replace 语义 + 二次确认串）。

设计约束（逐条对应方案章节，实现时不得绕过）：

- **S3 凭据零泄漏**：口令只在请求体内，不写日志 / 审计 / 异常消息；审计 detail 只含计数；
- **S6 审计只增不改**：导出埋 ``site.export``、导入埋 ``site.import``，detail 走
  :meth:`SiteCounts.audit_detail`（只记计数、模式与回滚点目录名）；
- **A1 / A5**：路由不持有 DB 句柄——``user_version`` 由服务层
  :func:`parse_site_pack_for_app` 读，判定与落地全部经 ``services/site_backup.py``
  与 ``SiteRepo``；事务边界在服务层；
- **C4**：PBKDF2 600k 的封包 / 解包一律 ``asyncio.to_thread`` offload（服务层的
  ``build_site_pack`` 是同步函数、``parse_site_pack`` 内部已 offload）；
- **首启门**：未完成 bootstrap 不得导出或导入（S4/S5——否则可绕过 ``must_change``
  直接接管实例）。

CSRF：本模块三个端点全是 POST，由 ``main.CSRFOriginMiddleware`` 统一做同源校验；
因此**不提供 GET 触发**（导出产物是整站万能钥匙，必须 POST + Origin + 审计）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import require_bootstrap_done, require_session
from app.constants import AUDIT_SITE_EXPORT, AUDIT_SITE_IMPORT
from app.schemas.api_schemas import (
    SiteExportRequest,
    SiteImportReport,
    SiteImportRequest,
    SiteInspectReport,
    SiteInspectRequest,
)
from app.services import site_backup

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["site-backup"])

# 与其余业务路由同值（S4：新端点必须挂 require_session；首启门另行挂 require_bootstrap_done）
# 三个端点全部通过 dependencies 挂载鉴权，故函数签名里**不再重复声明** session 依赖。
_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# 下载文件名的时间戳取包自身的 created_at（ISO 前 10 位即日期），与包内容一致
_FILENAME_STAMP_LENGTH = 10


@router.post(
    "/site-backup/export",
    response_class=Response,
    summary="E69 全站数据备份包导出（强制加密）",
    dependencies=_PROTECTED,
)
async def export_site_backup(payload: SiteExportRequest, request: Request) -> Response:
    """导出全站备份包（配置 + 仓库密码文件 + 可选 rclone.conf / rclone.pass）。

    一致性靠服务层「事务内取表 + 事务外读文件」（方案 §3.1），**不停任务**：``jobs`` 不进包。
    任一在用仓库缺密码文件、或 conf 已加密而口令不可读 → 422 fail-closed（方案 §3.3-3/6）：
    导出侧是唯一能发现「包在新机不可用」的地方。

    S3：口令只进 :func:`build_site_pack` 的形参，不进日志 / 审计 / 异常；
    审计 ``site.export`` 的 detail 只记各段计数。
    """
    state = await site_backup.collect_state(request.app, include_rclone=payload.include_rclone_conf)
    # C4：PBKDF2 600k 是 CPU 密集，绝不在事件循环线程里跑
    pack = await asyncio.to_thread(site_backup.build_site_pack, state, payload.password)
    counts = site_backup.SiteCounts.from_state(state)
    await request.app.state.audit.record(
        AUDIT_SITE_EXPORT,
        target="site",
        detail=counts.audit_detail(),
        result="ok",
    )
    stamp = str(pack["created_at"])[:_FILENAME_STAMP_LENGTH]
    logger.info(
        "全站备份包已导出：repos=%s tasks=%s config=%s passfiles=%s rclone_conf=%s",
        counts.repos,
        counts.tasks,
        counts.config_keys,
        counts.passfiles,
        counts.rclone_conf_included,
    )
    return Response(
        content=json.dumps(pack, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="restic-web-site-{stamp}.json"'},
    )


@router.post(
    "/site-backup/inspect",
    response_model=SiteInspectReport,
    summary="E70 全站备份包导入预检（零业务写入）",
    dependencies=_PROTECTED,
)
async def inspect_site_backup(payload: SiteInspectRequest, request: Request) -> SiteInspectReport:
    """解密并预检备份包，回包摘要 + 目标现状 + 阻碍项（**不写任何业务数据 / 文件**）。

    与 :func:`import_site_backup` 共用同一套判定（``parse_site_pack_for_app`` →
    ``describe_target`` → ``target_blockers``），因此「预览说能导、导却被拒」不会发生。

    阻碍项只报三条**能力类**问题（密码目录不可写 / restic 不可用 / conf 加密态不一致）；
    活跃作业与「目标非空」不列为阻碍——它们分别要用户去等或去确认，属可化解的前置条件。
    """
    state = await site_backup.parse_site_pack_for_app(request.app, payload.pack, payload.password)
    summary = await site_backup.describe_target(request.app)
    blockers = site_backup.target_blockers(summary, state.get("rclone") or {})
    return SiteInspectReport(
        pack=site_backup.summarize_pack(payload.pack, state),
        target=summary,
        blockers=blockers,
    )


@router.post(
    "/site-backup/import",
    response_model=SiteImportReport,
    summary="E71 全站备份包导入（整机替换 + 文件回滚点）",
    dependencies=_PROTECTED,
)
async def import_site_backup(payload: SiteImportRequest, request: Request) -> SiteImportReport:
    """把备份包整机替换到本实例（唯一语义 ``replace``，方案 §4.4）。

    校验顺序严格照方案 §4.3 第 1–8 步：解析 / 版本 / AAD 解密 / 载荷结构 / schema 均在
    :func:`parse_site_pack` 内（失败即**零写入**）；随后 :func:`assert_target_ready` 做
    目标能力、conf 加密态一致性、**无活跃作业（409，D3）**、非空目标需 ``acknowledge``
    逐字等于包外层 ``created_at``（409，§4.7）。

    落地由 :func:`apply_state` 编排：文件回滚点 → 三文件成组原子写 → 单事务替换 →
    进程内重建 ``RCLONE_PASSWORD_COMMAND`` → 热生效。失败时文件从回滚点还原、DB 事务回滚。

    成功即 ``sessions`` 清空：本请求是当前会话的最后一次有效请求，前端拿到响应体后
    必须跳登录页（``logged_out=true``）。
    """
    state = await site_backup.parse_site_pack_for_app(request.app, payload.pack, payload.password)
    await site_backup.assert_target_ready(
        request.app,
        state,
        acknowledge=payload.acknowledge or None,
        # §4.2 / §4.7：acknowledge 逐字比对**包外层** created_at（AAD 绑定的明文头），
        # 不是解密载荷里的 created_at——后者可被重封，前者才是用户预览时看到的那个值。
        expected_created_at=str(payload.pack.get("created_at") or ""),
    )
    report = await site_backup.apply_state(request.app, state)
    counts = site_backup.SiteCounts.from_state(state)
    await request.app.state.audit.record(
        AUDIT_SITE_IMPORT,
        target="site",
        detail=counts.audit_detail(
            mode=payload.mode, rollback_dir=report["rollback_dir"]
        ),
        result="ok",
    )
    return SiteImportReport(**report)


__all__ = ["router"]
