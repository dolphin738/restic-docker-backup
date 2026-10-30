"""仓库连通性测试与用量域（E29 + E41 usage）。

候选 3 五域拆分第 3 域：从 ``routers/repos.py`` 原样搬出，
端点路径 / 响应模型 / 审计动作 / 声明顺序全部不变。

用 ``register(router)`` 挂到聚合器（见 ``repos_crud`` 文件头对顺序契约的说明）。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_session
from app.auth.session import SessionContext
from app.constants import AUDIT_REPO_TEST, REPO_PROBE_TIMEOUT_SECONDS
from app.db.repositories.repo_repo import ReposRepo
from app.errors import RepoNotFoundError
from app.routers.repos_shared import PROTECTED, rclone_remote_of, validate_repo_id
from app.schemas.api_schemas import (
    RepoTestResponse,
    RepoUsageMirrorRef,
    RepoUsageOut,
)
from app.services.restic import ResticCmd, WhitelistArgs

logger = logging.getLogger(__name__)

_PROTECTED = PROTECTED
_rclone_remote_of = rclone_remote_of
_validate_repo_id = validate_repo_id


def register(router: APIRouter) -> None:
    """把本域端点挂到聚合器（调用顺序 = 路由声明顺序）。"""

    @router.post(
        "/repos/{repo_id}/test",
        response_model=RepoTestResponse,
        summary="E29 仓库连通性测试（per-repo）",
        dependencies=_PROTECTED,
    )
    async def test_repo(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoTestResponse:
        """``restic cat config`` 探测连通性：成功回 ``{ok: true, latency_ms}``。

        失败**不抛错也不静默**：回 ``{ok: false, reason}``（锁残留/凭据缺失/仓库未初始化…）。
        """
        del session
        restic: ResticCmd = request.app.state.restic
        repos_repo: ReposRepo = request.app.state.repos_repo

        repo = await repos_repo.get(repo_id)
        if repo is None:
            raise RepoNotFoundError(repo_id)
        repo_url = repo.url or ""
        if not repo_url:
            await request.app.state.audit.record(
                AUDIT_REPO_TEST, target=repo_id, detail={"ok": False}, result="denied"
            )
            return RepoTestResponse(ok=False, latency_ms=0, reason="仓库地址未配置（请先填写仓库地址）")

        started = time.monotonic()
        probe = await restic.probe(
            "cat",
            repo_url=repo_url,
            repo_id=repo_id,
            args=WhitelistArgs(json_output=False, extra_positionals=("config",)),
            timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        if probe.exit_code != 0:
            reason = probe.reason(REPO_PROBE_TIMEOUT_SECONDS)
            kind = probe.kind()
            await request.app.state.audit.record(
                AUDIT_REPO_TEST, target=repo_id, detail={"ok": False, "reason": reason}, result="denied"
            )
            return RepoTestResponse(ok=False, latency_ms=latency_ms, reason=reason, failure_kind=kind)

        await request.app.state.audit.record(
            AUDIT_REPO_TEST, target=repo_id, detail={"ok": True, "latency_ms": latency_ms}, result="ok"
        )
        return RepoTestResponse(ok=True, latency_ms=latency_ms)


    @router.get(
        "/repos/{repo_id}/usage",
        response_model=RepoUsageOut,
        summary="P1 仓库占用明细（被镜像引用 + 快照总数）",
        dependencies=_PROTECTED,
        tags=["repos"],
    )
    async def repo_usage(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoUsageOut:
        """查询某仓库被谁占用（**只读，零副作用**），供删除确认门动态展示后果。

        - ``mirror_refs``：把本仓当镜像目的地的任务清单（口径同 :meth:`ReposRepo.list_mirror_refs`）；
        - ``snapshot_total``：仓库内快照**总条数**（走 ``SnapshotCatalog`` TTL 缓存，
          含无 tag 的手工快照）；``null`` = 此刻不可知，**不可当 0 展示**。
        """
        del session
        _validate_repo_id(repo_id)
        repos_repo: ReposRepo = request.app.state.repos_repo
        existing = await repos_repo.get(repo_id)
        if existing is None:
            raise RepoNotFoundError(repo_id)
        mirror_refs = await repos_repo.list_mirror_refs(repo_id)
        catalog = getattr(request.app.state, "snapshot_catalog", None)
        total: int | None = None
        if catalog is not None and (existing.url or "").strip():
            env = await request.app.state.restic.env_for(
                repo_url=(existing.url or "").strip(), repo_id=repo_id
            )
            tally = await catalog.snapshot_tally(
                restic=request.app.state.restic,
                parser=request.app.state.parser,
                repo_id=repo_id,
                repo_url=(existing.url or "").strip(),
                env=env,
            )
            if tally is not None:
                total = tally.total
        return RepoUsageOut(
            repo_id=repo_id,
            mirror_refs=[
                RepoUsageMirrorRef(id=tid, name=name) for tid, name in mirror_refs
            ],
            snapshot_total=total,
        )
