"""仓库 CRUD 与归档域（E39–E41 + 归档页仓库侧）。

候选 3 五域拆分第 2 域。各域用 ``register(router)`` 往聚合器上挂端点，而不是
各自建 APIRouter + ``include_router``：

1. **顺序即调用顺序**——``/repos/_new_id`` 必须先于 ``/repos/{repo_id}``，用 include
   时要靠一个个 include 调用来拼顺序，容易在后续重构中失稳；
2. **少一层前缀拼接**——各域 router 若也带 ``/api`` 前缀，嵌套 include 会叠加；
3. 实测（FastAPI 0.143.0）：``app.include_router`` 在路由表里留下的是**惰性**
   ``_IncludedRouter`` 节点，``app.routes`` 里看不到真实条目，但 openapi 与请求
   分发都正常——**用 app.routes 校验拆分是否生效会误判**，请改用
   ``app.openapi()["paths"]`` 或直接跑 API 用例。

**路由声明顺序是契约**：``/repos/_new_id`` 必须先于 ``/repos/{repo_id}``，
否则 ``_new_id`` 会被当 repo_id 捕获（→404）。顺序由聚合器的 register 调用次序保证。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_session
from app.auth.session import SessionContext
from app.constants import AUDIT_REPO_ARCHIVE, AUDIT_REPO_RESTORE
from app.db.repositories.repo_repo import ReposRepo
from app.errors import RepoNotFoundError, ValidationError
from app.routers.repos_shared import (
    PROTECTED,
    new_repo_id,
    resolve_repo_id,
    to_out,
    validate_repo_id,
    validate_repo_url,
)
from app.schemas.api_schemas import (
    ArchivedRepoOut,
    ArchiveListOut,
    OkResponse,
    RepoCreateRequest,
    RepoIdSuggestion,
    RepoOut,
    RepoReleaseRequest,
    RepoTombstoneOut,
    RepoUpdateRequest,
)
from app.services.env_sync import passfile_status
from app.services.repo_status_cache import invalidate_repo_status_cache
from app.services.snapshot_catalog import invalidate_snapshot_catalog

logger = logging.getLogger(__name__)

# 域内沿用原下划线别名，避免端点体里约 20 处调用点全改
_PROTECTED = PROTECTED
_to_out = to_out
_new_repo_id = new_repo_id
_resolve_repo_id = resolve_repo_id
_validate_repo_id = validate_repo_id
_validate_repo_url = validate_repo_url


def register(router: APIRouter) -> None:
    """把本域的端点挂到聚合器 router 上（调用顺序 = 路由声明顺序）。"""

    @router.get(
        "/repos",
        response_model=list[RepoOut],
        summary="R-023 仓库列表（切换器数据源）",
        dependencies=_PROTECTED,
    )
    async def list_repos(
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> list[RepoOut]:
        """返回全部仓库（default 恒首位；含类型/来源/密码文件状态）。"""
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        settings = request.app.state.settings
        return [_to_out(repo, passfile_status(settings, repo.id)) for repo in await repos_repo.list_all()]


    @router.post(
        "/repos",
        response_model=RepoOut,
        summary="R-023 新建仓库",
        dependencies=_PROTECTED,
    )
    async def create_repo(
        payload: RepoCreateRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoOut:
        """新建仓库（url/host_tag 可空；``source`` 恒 ui——env 只覆盖 default）。

        A1（Q20）：``url`` 非空时按协议前缀做严格校验（rclone 另查 remote 存在性）。
        """
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        repo_id = await _resolve_repo_id(repos_repo, payload.id)
        if payload.url:
            await _validate_repo_url(payload.url, request.app.state.rclone_probe)
        repo = await repos_repo.create(repo_id, payload.name, url=payload.url, host_tag=payload.host_tag)
        return _to_out(repo, passfile_status(request.app.state.settings, repo.id))


    @router.get(
        "/repos/_new_id",
        response_model=RepoIdSuggestion,
        summary="A8 返回服务端生成的候选仓库标识（r<YYMMDD>-<6 位随机>）",
        dependencies=_PROTECTED,
    )
    async def suggest_repo_id() -> RepoIdSuggestion:
        """供新建仓库向导预填标识（对齐 Duplicati 新建备份自动起名体验）。

        不落库；用户可改可不改。留空提交时 ``POST /api/repos`` 仍会服务端生成（见 A8）。
        声明在 ``/repos/{repo_id}`` 之前，避免被路径参数捕获。
        """
        return RepoIdSuggestion(id=_new_repo_id())


    @router.get(
        "/repos/{repo_id}",
        response_model=RepoOut,
        summary="R-023 仓库详情",
        dependencies=_PROTECTED,
    )
    async def get_repo(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoOut:
        """返回单仓库详情（不存在 404 REPO_NOT_FOUND）。"""
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        repo = await repos_repo.get(repo_id)
        if repo is None:
            raise RepoNotFoundError(repo_id)
        return _to_out(repo, passfile_status(request.app.state.settings, repo.id))


    @router.put(
        "/repos/{repo_id}",
        response_model=RepoOut,
        summary="R-023 更新仓库（url/host_tag 被 env 覆盖时 409）",
        dependencies=_PROTECTED,
    )
    async def update_repo(
        repo_id: str,
        payload: RepoUpdateRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoOut:
        """更新仓库资料；``url``/``host_tag`` 被 env 覆盖的字段拒绝界面改（409 ENV_OVERRIDDEN）。

        A1（Q20）：``url`` 非空时按协议前缀做严格校验（rclone 另查 remote 存在性）——
        校验先于落库，坏 URL 不进 repos 表。
        """
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        if payload.url:
            await _validate_repo_url(payload.url, request.app.state.rclone_probe)
        repo = await repos_repo.update(
            repo_id, name=payload.name, url=payload.url, host_tag=payload.host_tag
        )
        invalidate_repo_status_cache()
        return _to_out(repo, passfile_status(request.app.state.settings, repo.id))


    @router.delete(
        "/repos/{repo_id}",
        response_model=OkResponse,
        summary="R-023 删除仓库（默认归档；hard=1 彻底删；purge_disk=1 销毁数据；三门守卫）",
        dependencies=_PROTECTED,
    )
    async def delete_repo(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
        purge_disk: bool = False,
        hard: bool = False,
    ) -> OkResponse:
        """删除仓库，三条分支（《删除依赖与快照可用性保护方案》v1.2 裁决 D8/§5.1）：

        1. **默认 = 归档**（``deleted_at`` 置位）：行还在、**凭据与密码文件都保留**、
           磁盘数据一个字节不动 → 归档期内一键原样恢复，快照立即可读。列表不再显示，
           归档页可见。这正是「记录会过期、数据永不自动删」的落点。
        2. ``hard=1`` = 彻底删除行（+ 终态作业级联）：**不保留可恢复能力**，但磁盘数据
           仍原样留在原地（孤儿数据，供人工处置）；留不含凭据的墓碑。
        3. ``purge_disk=1`` = 销毁数据：先清空仓库目录**内容** + 删密码文件，再删行；
           与归档**互斥**（勾了就落不回归档），墓碑标 ``data_destroyed=1``。

        三门守卫（default 禁删 / 仍有 backup_tasks / 仍有活跃作业）对三条分支一视同仁。
        hard/purge 分支的执行顺序由 ``RepoLifecycleService.hard_delete`` 持有
        （候选 1：**行最后删**，崩在删行之前的任何一步都能整请求重发并收敛）。

        仓库不存在 → 404 ``REPO_NOT_FOUND``（先于校验，避免为无效 target 落审计）。
        审计：归档 ``repo.archive``、彻底删 ``repo.delete``、销毁 ``repo.purge``。
        """
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        # 存在性前置：仓库不存在直接 404，避免为「删了个不存在的仓库」落一条成功审计
        existing = await repos_repo.get(repo_id)
        if existing is None:
            raise RepoNotFoundError(repo_id)
        # 删除前置校验（default 禁删 + 无任务/无活跃作业 + 未被镜像引用）先于一切副作用——
        # purge_disk 的磁盘清理尤其不能跑到校验前面。三条分支一视同仁（归档分支也要过）。
        # hard/purge 分支会在编排器的锁内**再跑一次**（关掉校验后到执行前的 TOCTOU 窗口）。
        await repos_repo.ensure_deletable(repo_id)

        if not purge_disk and not hard:
            # ---- 分支 1：归档（可逆，零数据风险）----
            await repos_repo.archive(repo_id)
            await request.app.state.audit.record(
                AUDIT_REPO_ARCHIVE,
                target=repo_id,
                detail={"name": existing.name, "url": existing.url or ""},
                result="ok",
            )
            invalidate_repo_status_cache()
            invalidate_snapshot_catalog()
            return OkResponse()

        # hard / purge 分支：跨介质多步序列整体交给编排器（候选 1）——顺序契约、并发互斥、
        # 事务边界与审计留痕都在 RepoLifecycleService 里，路由只做入参映射。
        await request.app.state.repo_lifecycle.hard_delete(repo_id, purge_disk=purge_disk)
        invalidate_repo_status_cache()
        # P0 L4-3：仓库行没了 → 该仓库的快照计数缓存随之失效（原先只清状态缓存）
        invalidate_snapshot_catalog()
        return OkResponse()


    @router.get(
        "/archive/repos",
        response_model=ArchiveListOut,
        summary="归档页：仓库归档 + 墓碑清单（只读）",
        dependencies=_PROTECTED,
        tags=["repos"],
    )
    async def list_archive(request: Request, session: SessionContext = Depends(require_session)) -> ArchiveListOut:
        """返回「可恢复的归档仓库」与「只剩记录的墓碑」。

        刻意用 ``/archive/*`` 前缀而不是 ``/repos/archived``：``/repos/{repo_id}`` 已在
        前面声明，FastAPI 按声明顺序匹配，``archived`` 会被当成 repo_id 吃掉。
        """
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        settings = request.app.state.settings
        archived = await repos_repo.list_archived()
        tombstones = await repos_repo.list_tombstones()
        return ArchiveListOut(
            repos=[
                ArchivedRepoOut(
                    repo_id=repo.id,
                    name=repo.name,
                    type=repo.type,
                    url=repo.url,
                    archived_at=repo.deleted_at or "",
                    passfile_status=passfile_status(settings, repo.id),
                )
                for repo in archived
            ],
            tombstones=[
                RepoTombstoneOut(
                    repo_id=str(row["repo_id"]),
                    name=str(row["name"]),
                    type=str(row["type"]),
                    url_display=None if row["url_display"] is None else str(row["url_display"]),
                    archived_at=str(row["archived_at"]),
                    snapshot_count=None if row["snapshot_count"] is None else int(row["snapshot_count"]),
                    data_destroyed=bool(row["data_destroyed"]),
                    released_at=None if row["released_at"] is None else str(row["released_at"]),
                )
                for row in tombstones
            ],
        )


    @router.post(
        "/archive/repos/{repo_id}/restore",
        response_model=OkResponse,
        summary="恢复归档仓库（撤销归档；数据与凭据从未被删，故即刻可用）",
        dependencies=_PROTECTED,
        tags=["repos"],
    )
    async def restore_archived_repo(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> OkResponse:
        """把归档仓库还原为「在用」：``deleted_at`` 置空，列表与切换器立刻可见。"""
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        repo = await repos_repo.get(repo_id)
        if repo is None:
            raise RepoNotFoundError(repo_id)
        if repo.deleted_at is None:
            raise ValidationError("该仓库未归档，无需恢复", data={"repo_id": repo_id})
        await repos_repo.restore(repo_id)
        await request.app.state.audit.record(
            AUDIT_REPO_RESTORE, target=repo_id, detail={"name": repo.name}, result="ok"
        )
        invalidate_repo_status_cache()
        invalidate_snapshot_catalog()
        return OkResponse()


    @router.post(
        "/archive/repos/{repo_id}/release",
        response_model=OkResponse,
        summary="释放归档仓库 ID（需输入仓库 id 确认；旧快照自此与本应用失去关联）",
        dependencies=_PROTECTED,
        tags=["repos"],
    )
    async def release_archived_repo(
        repo_id: str,
        payload: RepoReleaseRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> OkResponse:
        """释放 ID：删 ``repos`` 行（该 id 立即可被新仓库复用）+ 留墓碑 + 清凭据键。

        **不动磁盘数据**（守 §6 红线：记录与数据分离）——数据仍可手工找回，只是本应用
        不再持有入口。确认门槛是「原样输入仓库 id」，避免误点。

        **三门守卫（2026-11-02 补齐，D5/D6）**，与 DELETE 路由同口径：

        1. ``ensure_deletable``——default 禁删（D6=(c)：**default 永不可 release**）、
           有备份任务/活跃作业即拒 409 ``REPO_NOT_EMPTY``、被镜像引用即拒 409。
           缺这道守卫时，正常 UI 操作（归档页 → 释放 ID → 输入 default）就能删掉
           default 行，并在墓碑到期后让口令文件被判孤儿回收，全局敏感键永久解不开。
        2. **必须已归档**——语义是「释放**归档**仓库的 ID」；修复前未归档的在用仓库
           也能被释放并删除行，等于绕过 DELETE 的全部校验。
        """
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        repo = await repos_repo.get(repo_id)
        if repo is None:
            raise RepoNotFoundError(repo_id)
        if (payload.confirm_id or "").strip() != repo_id:
            raise ValidationError(
                "请输入仓库 id 以确认释放", data={"repo_id": repo_id}
            )
        # 门 1：复用删除前置校验（default 禁删 + 无任务/无活跃作业 + 未被镜像引用）。
        # 放在 confirm 之后：确认文案是第一道人工闸，先让它过再查库，省一次查询。
        await repos_repo.ensure_deletable(repo_id)
        # 门 2：只释放已归档仓库（与 restore 的 :297-298 对称校验同形）
        if repo.deleted_at is None:
            raise ValidationError("该仓库未归档，无需释放", data={"repo_id": repo_id})
        # 跨介质序列交给编排器（候选 1）：顺序契约、并发互斥、事务与审计都在里面
        await request.app.state.repo_lifecycle.release(repo_id)
        invalidate_repo_status_cache()
        invalidate_snapshot_catalog()
        return OkResponse()


    @router.delete(
        "/archive/tombstones/{repo_id}",
        response_model=OkResponse,
        summary="删除归档记录（墓碑）：只删记录，绝不删数据",
        dependencies=_PROTECTED,
        tags=["repos"],
    )
    async def delete_tombstone(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> OkResponse:
        """删除墓碑行（「我连这里曾经有过什么也不想记」）。**不触碰任何数据**。"""
        del session
        repos_repo: ReposRepo = request.app.state.repos_repo
        if not await repos_repo.delete_tombstone(repo_id):
            raise RepoNotFoundError(repo_id)
        return OkResponse()


    # 注：原 ``_purge_repo_disk``（delete 的 purge_disk 分支）已迁到
    # ``services/repo_lifecycle.py::RepoLifecycleService._purge_disk``——候选 1 批次 ①。
    # 删除路径的磁盘清理不再由路由层直接调用（路由只做入参映射）。
