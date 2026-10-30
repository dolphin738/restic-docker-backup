"""仓库生命周期编排器（候选 1）——跨介质多步序列的唯一实现点。

背景：`routers/repos.py` 的 delete / release / rebuild 是「跨磁盘 + DB + 子进程 +
缓存」的裸序列，没有事务边界也没有可重试性——行一删，后续步骤的重试入口就 404 了
（见 `docs/决策-候选1仓库生命周期事务-2026-10-31.md`）。

**风险模型不是「靠事务回滚」，而是「顺序 + 幂等」**：磁盘清空、删日志、删密码文件
都不可逆，补偿补不回来。真正让系统收敛的是**把删行挪到最后**——崩在删行之前的任何
一步，行都还在，用户重发即可收敛（`remove_passfile` / `unlink` / `config_repo.delete`
三者都已天然幂等）。

事务只用来消掉「墓碑写了但行没删」这一个窗口：3 条 SQL，毫秒级。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

from app.constants import (
    AUDIT_REPO_LIFECYCLE_BEGIN,
    AUDIT_REPO_LIFECYCLE_FAILED,
)
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.repo_repo import Repo, ReposRepo
from app.errors import JobConflictError, RepoNotFoundError, ValidationError
from app.services.repo_status_cache import invalidate_repo_status_cache
from app.services.snapshot_catalog import invalidate_snapshot_catalog
from app.settings import Settings
from app.utils.passfile import remove_passfile
from app.utils.time import utc_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteReport:
    """删除类动作的产物计数（与原审计 detail 同形，前端无感）。"""

    removed_logs: int = 0
    cleared_credentials: int = 0
    purged_disk: bool = False


def _is_fs_root(path: Path) -> bool:
    """是否文件系统根——清空类动作的硬拒绝线（本模块与 rebuild 共用）。"""
    return path.anchor == str(path) or str(path) in {"/", "\\\\"}


async def _clear_dir_contents(local_path: Path) -> None:
    """清空目录**内容**（保留目录本身），失败抛 ``ValidationError``（422，可操作）。"""

    def _clear() -> None:
        import shutil

        for child in local_path.iterdir():
            # 符号链接一律 unlink：rmtree 拒绝符号链接，且绝不跟随链接删目标
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    try:
        await asyncio.to_thread(_clear)
    except OSError as exc:
        raise ValidationError(
            f"仓库数据目录清理失败（权限不足或文件被占用）：{local_path}；"
            "请在宿主机对应挂载目录手动清理后重试"
        ) from exc


def _unlink_logs_sync(paths: list[str]) -> None:
    """同步删除孤儿任务日志文件（缺失/失败静默跳过）。"""
    for raw in paths:
        with contextlib.suppress(OSError):
            Path(raw).unlink()


class RepoLifecycleService:
    """仓库生命周期编排器：把跨介质多步序列收成一个深模块。

    路由只做入参映射与响应映射；本类持有**顺序契约**与**并发互斥**。
    
    并发：按 repo_id 惰性建 ``asyncio.Lock``。锁内**重跑**前置校验（关掉
    「校验通过 → 别的请求插进来改状态」的 TOCTOU 窗口）。
    
    注意：本锁只保护生命周期侧。backup 提交与 delete 之间仍有缝——
    由 ``jobs.repo_id`` 外键兜住（活跃任务存在时删行会 IntegrityError），
    真正关闭需与 JobRunner 共用锁域，属 R-023 范围。
    """

    def __init__(
        self,
        *,
        db,
        repos: ReposRepo,
        config: ConfigRepo,
        jobs: JobRepo,
        audit,
        settings: Settings,
        status_cache=None,
        catalog=None,
    ) -> None:
        """依赖注入（与 JobRunner 同风格）。

        Args:
            db: :class:`Database`（事务入口）。
            repos: 仓库仓储。
            config: 配置仓储（凭据键清理）。
            jobs: 任务仓储（活跃任务复查 + 日志路径）。
            audit: 审计仓储。
            settings: 运行期配置（密码文件路径派生）。
            status_cache: 状态缓存失效回调；None 时用模块级实现。
            catalog: 快照目录缓存失效回调；None 时用模块级实现。
        """
        self._db = db
        self._repos = repos
        self._config = config
        self._jobs = jobs
        self._audit = audit
        self._settings = settings
        self._invalidate_status = status_cache or invalidate_repo_status_cache
        self._invalidate_catalog = catalog or invalidate_snapshot_catalog
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, repo_id: str) -> asyncio.Lock:
        """按仓库维度惰性建锁（不可重入：锁内不得再调本类的其它加锁方法）。"""
        lock = self._locks.get(repo_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[repo_id] = lock
        return lock

    # ---------------------------------------------------------------- 内部步骤

    async def _require_repo(self, repo_id: str) -> Repo:
        """锁内重取仓库行；不存在即 404。"""
        repo = await self._repos.get(repo_id)
        if repo is None:
            raise RepoNotFoundError(repo_id)
        return repo

    async def _purge_disk(self, repo: Repo) -> bool:
        """清空仓库目录内容 + 删密码文件（不可逆）。返回是否真的清了目录。

        失败一律 422 **零损失**：此时行与数据都还在，可安全重发。
        """
        from app.routers.repos import _repo_local_path  # 路径解析唯一实现点

        local_path = _repo_local_path(repo.url or "")
        if local_path is None:
            raise ValidationError(
                "该仓库未配置地址或后端不是本地文件系统（远程后端无法自动清空）。"
                "请手动清空后端存储后再删除，或换 URL 新建。",
                data={"url": repo.url or ""},
            )
        if _is_fs_root(local_path):
            raise ValidationError(f"仓库路径解析为文件系统根目录，拒绝清空：{local_path}")
        removed = False
        if local_path.exists():
            if not local_path.is_dir():
                raise ValidationError(f"仓库路径不是目录，拒绝清空：{local_path}")
            await _clear_dir_contents(local_path)
            removed = True
        # 密码文件随数据一并清除（全局共用密码文件时 remove_passfile 会自行拒绝）
        remove_passfile(self._settings, repo.id)
        return removed

    async def _clear_credentials(self, repo_id: str) -> int:
        """清 ``cred.{repo_id}.*`` 凭据键，返回清除条数（幂等：键已无则跳过）。"""
        cleared = 0
        prefix = f"cred.{repo_id}."
        for row in await self._config.list_all_raw():
            # 镜像 rebuild 先例：跳过 env 来源与空值——env 键归 env 同步管
            if not (row.key.startswith(prefix) and row.source != "env" and row.value):
                continue
            await self._config.delete(row.key)
            cleared += 1
        return cleared

    async def _begin(self, op: str, repo: Repo, **extra: object) -> None:
        """写意图留痕（在任何副作用之前）。"""
        detail = {"op": op, "name": repo.name, **extra}
        await self._audit.record(AUDIT_REPO_LIFECYCLE_BEGIN, target=repo.id, detail=detail, result="ok")

    async def _failed(self, op: str, repo_id: str, exc: BaseException) -> None:
        """写失败留痕；审计本身失败只记日志（S6 是留痕手段，不该反过来放大故障）。"""
        try:
            await self._audit.record(
                AUDIT_REPO_LIFECYCLE_FAILED,
                target=repo_id,
                detail={"op": op, "error": type(exc).__name__},
                result="failed",
            )
        except Exception:  # noqa: BLE001 - 审计写失败不该改变原异常的语义
            logger.error("生命周期失败审计写入失败（op=%s, repo=%s）", op, repo_id, exc_info=True)

    async def _finish(self, action: str, repo: Repo, report: DeleteReport) -> None:
        """收尾审计 + 缓存失效。审计失败**吞掉**：变更已生效，抛出会让用户以为失败了。"""
        try:
            await self._audit.record(
                action,
                target=repo.id,
                detail={
                    "name": repo.name,
                    "removed_logs": report.removed_logs,
                    "cleared_credentials": report.cleared_credentials,
                    "purged_disk": report.purged_disk,
                },
                result="ok",
            )
        except Exception:  # noqa: BLE001 - 数据已删，抛出只会让用户重试得 404
            logger.error("生命周期收尾审计写入失败（action=%s, repo=%s）", action, repo.id, exc_info=True)
        self._invalidate_status()
        self._invalidate_catalog()

    # ---------------------------------------------------------------- 公开接口

    async def hard_delete(self, repo_id: str, *, purge_disk: bool = False) -> DeleteReport:
        """彻底删除仓库：清凭据 → 清磁盘 → 删行（最后）→ 清日志文件 → 收尾审计。

        顺序是**契约**（不是实现细节）：
        1. 锁内重取行 + 重跑三门守卫 + 复查活跃作业；
        2. begin 审计（意图留痕，先于一切副作用）；
        3. 取日志路径（**必须在删行之前**——行一删就查不到了）；
        4. purge_disk 分支：清目录内容 + 删密码文件（不可逆，失败即 422 零损失）；
        5. 清凭据键（幂等）；
        6. 事务内：墓碑 + 删终态 jobs + 删 repos（3 条 SQL）；
        7. 删日志文件（崩在这之前留下的孤儿由 retention 按 mtime 回收）；
        8. 收尾审计 + 缓存失效。

        崩在 1~6 之间 → 行仍在 → 整请求重发即收敛；崩在 6 之后 → 只缺收尾审计。

        Args:
            repo_id: 仓库 id。
            purge_disk: 是否销毁磁盘数据（不可逆）。

        Returns:
            :class:`DeleteReport`。
        Raises:
            RepoNotFoundError: 仓库不存在（先于一切副作用）。
            ValidationError / RepoNotEmptyError / RepoInUseAsMirrorError: 守卫拒绝。
            JobConflictError: 仍有活跃作业。
        """
        async with self._lock(repo_id):
            op = "purge" if purge_disk else "delete"
            try:
                repo = await self._require_repo(repo_id)
                await self._repos.ensure_deletable(repo_id)
                if await self._jobs.list_active(repo_id):
                    raise JobConflictError(data={"repo_id": repo_id})
                await self._begin(op, repo, purge_disk=purge_disk)

                log_paths = await self._jobs.terminal_log_paths(repo_id)
                purged = await self._purge_disk(repo) if purge_disk else False
                cleared = await self._clear_credentials(repo_id)

                now = utc_iso()
                async with self._db.transaction() as tx:
                    await self._repos.insert_tombstone(
                        tx=tx,
                        repo_id=repo.id,
                        name=repo.name,
                        repo_type=repo.type,
                        url_display=repo.url,
                        archived_at=repo.deleted_at or now,
                        snapshot_count=None,
                        data_destroyed=purged,
                    )
                    await self._repos.drop_row(repo.id, tx=tx)

                if log_paths:
                    await asyncio.to_thread(_unlink_logs_sync, log_paths)
                report = DeleteReport(
                    removed_logs=len(log_paths),
                    cleared_credentials=cleared,
                    purged_disk=purged,
                )
                from app.constants import AUDIT_REPO_DELETE, AUDIT_REPO_PURGE

                await self._finish(AUDIT_REPO_PURGE if purged else AUDIT_REPO_DELETE, repo, report)
                return report
            except Exception as exc:
                await self._failed(op, repo_id, exc)
                raise
