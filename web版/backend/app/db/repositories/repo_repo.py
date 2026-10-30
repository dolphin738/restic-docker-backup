"""repos 表仓储（R-023 / P3-C；架构 §4.7 扩列后为「仓库事实表」）。

与 config 时代的职责切分（E4 裁决：``repo.*`` 全局键整体迁入本表）：
- ``url`` / ``host_tag`` 为本表列，各带来源列（``url_source`` / ``host_tag_source``，
  ``'ui'``|``'env'``——镜像 config.source 语义；compose env 只覆盖 **default** 仓库）；
- ``type`` 不落列：由 :func:`derive_repo_type` 从 url 协议前缀现算（单一事实来源，
  与 config 时代 ``repo.type`` 派生键同口径，消灭双状态源）；
- 密码文件不归本表：E5 per-repo 布局由 ``Settings.passfile_path(repo_id)`` 派生。

写保护（镜像 config 的 ENV_OVERRIDDEN 契约）：``*_source='env'`` 的字段拒绝界面
写入（409），仅启动期 env 同步（``env_sync.sync_env_repo_overrides``）可改。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.constants import ACTIVE_JOB_STATUSES, TERMINAL_JOB_STATUSES
from app.db.database import Database, Tx
from app.errors import (
    EnvOverriddenError,
    RepoInUseAsMirrorError,
    RepoNotEmptyError,
    ValidationError,
)
from app.schemas.enums import ConfigSource, RepoType
from app.utils.time import utc_iso

# 仓库 id 形态（路径参数/切换器键）：小写字母开头，小写字母/数字/下划线/连字符
REPO_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

DEFAULT_REPO_ID = "default"

# 默认仓库不可删除（「缺省 default」是切换器/补跑/env 覆盖的共同锚点）
DEFAULT_REPO_NAME = "默认仓库"

# URL 协议前缀 → 仓库类型（A1；单一事实源，新增后端只改此处）。
# 依据 restic 0.19.1 各后端 ParseConfig 的官方前缀逐一登记：
# s3:/b2:/sftp:/azure:/gs:/rest:/rclone:（gcs 的 restic 前缀是 gs:，历史命名）。
_PREFIX_TO_TYPE: tuple[tuple[str, RepoType], ...] = (
    ("s3:", RepoType.S3),
    ("b2:", RepoType.B2),
    ("sftp:", RepoType.SFTP),
    ("azure:", RepoType.AZURE),
    ("gs:", RepoType.GCS),
    ("rest:", RepoType.REST),
    ("rclone:", RepoType.RCLONE),
)


def derive_repo_type(url: str | None) -> str:
    """按仓库地址的协议前缀推导仓库类型（A1 起覆盖 restic 全部七后端）。

    Args:
        url: 仓库地址，如 ``s3:https://s3.endpoint/bucket``、``gs:bucket:/``、
            ``rclone:remote:path`` 或本地路径 ``/repo``。

    Returns:
        :class:`RepoType` 取值之一（空地址或本地路径回落 ``local``）。
    """
    lowered = (url or "").strip().lower()
    for prefix, repo_type in _PREFIX_TO_TYPE:
        if lowered.startswith(prefix):
            return str(repo_type)
    return str(RepoType.LOCAL)


@dataclass(frozen=True)
class Repo:
    """repos 表一行（0003 扩列后）。"""

    id: str
    name: str
    url: str | None
    host_tag: str | None
    url_source: str
    host_tag_source: str
    created_at: str
    # 归档时刻（ISO 8601 UTC）；None = 在用。归档只置位，不删行、不动磁盘与凭据
    # （《删除依赖与快照可用性保护方案》v1.2 §4 L4 / 迁移 0009）。
    deleted_at: str | None = None

    @property
    def type(self) -> str:
        """仓库类型（派生，不落库）。"""
        return derive_repo_type(self.url)


class ReposRepo:
    """repos 表唯一访问点。"""

    def __init__(self, db: Database) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
        """
        self._db = db

    # ---------------------------------------------------------------- 读

    async def get(self, repo_id: str) -> Repo | None:
        """按 id 读取仓库；不存在返回 None。

        **包含归档仓库**（``deleted_at`` 非空）：归档后仍要能只读浏览其快照，
        故本方法不做过滤，由调用方按 ``deleted_at`` 自行决定语义。
        """
        row = await self._db.fetch_one(
            "SELECT id, name, url, host_tag, url_source, host_tag_source, created_at, deleted_at "
            "FROM repos WHERE id = ?",
            (repo_id,),
        )
        return _to_repo(row) if row else None

    async def list_all(self) -> list[Repo]:
        """返回**在用**仓库（default 恒首位，其余按创建序）。

        归档仓库不在其中（删除保护方案 D8）：它们只出现在 :meth:`list_archived`
        与归档页；备份/任务解析、切换器、配置导出都不应再看到它们。
        """
        rows = await self._db.fetch_all(
            "SELECT id, name, url, host_tag, url_source, host_tag_source, created_at, deleted_at "
            "FROM repos WHERE deleted_at IS NULL ORDER BY (id = 'default') DESC, created_at ASC, id ASC"
        )
        return [_to_repo(row) for row in rows]

    async def list_archived(self) -> list[Repo]:
        """返回归档仓库（按归档时间倒序，最近归档在前）。"""
        rows = await self._db.fetch_all(
            "SELECT id, name, url, host_tag, url_source, host_tag_source, created_at, deleted_at "
            "FROM repos WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id ASC"
        )
        return [_to_repo(row) for row in rows]

    async def archive(self, repo_id: str) -> None:
        """归档仓库：只置 ``deleted_at``，**不动磁盘数据、不清凭据、不删密码文件**。

        这是「可一键原样恢复」的全部前提——凭据与口令还在，恢复后快照立即可读。
        """
        await self._db.write(
            "UPDATE repos SET deleted_at = ? WHERE id = ?", (utc_iso(), repo_id)
        )

    async def restore(self, repo_id: str) -> None:
        """撤销归档（``deleted_at`` 置空）。数据与凭据从未被删，故恢复即完成。"""
        await self._db.write("UPDATE repos SET deleted_at = NULL WHERE id = ?", (repo_id,))

    # ---------------------------------------------------------------- 墓碑（repo_tombstones）

    async def insert_tombstone(
        self,
        *,
        repo_id: str,
        name: str,
        repo_type: str,
        url_display: str | None,
        archived_at: str,
        snapshot_count: int | None,
        data_destroyed: bool,
        released_at: str | None = None,
        tx: Tx | None = None,
    ) -> None:
        """写墓碑（**不含密码与凭据**）：名称/类型/地址展示/归档时刻/当时快照数/是否已销毁数据。

        只记「这里曾经有过什么」，绝不存恢复能力（守 §6 双轨红线：记录与数据解耦）。
        用 ``INSERT OR REPLACE``：候选 1 的「行最后删」依赖它可重入（崩溃后重发
        不会因墓碑已存在而失败）。

        Args:
            tx: 事务句柄（候选 1）；给了就在事务内执行且不提交，否则各自提交。
        """
        sql = (
            "INSERT OR REPLACE INTO repo_tombstones "
            "(repo_id, name, type, url_display, archived_at, snapshot_count, data_destroyed, released_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            repo_id,
            name,
            repo_type,
            url_display,
            archived_at,
            snapshot_count,
            1 if data_destroyed else 0,
            released_at,
        )
        if tx is not None:
            await tx.execute(sql, params)
        else:
            await self._db.write(sql, params)

    async def list_tombstones(self) -> list[dict[str, Any]]:
        """返回全部墓碑行（按归档时间倒序）。"""
        rows = await self._db.fetch_all(
            "SELECT repo_id, name, type, url_display, archived_at, snapshot_count, "
            "data_destroyed, released_at FROM repo_tombstones "
            "ORDER BY archived_at DESC, repo_id ASC"
        )
        return [dict(row) for row in rows]

    async def delete_tombstone(self, repo_id: str) -> bool:
        """删除墓碑记录（「删除记录」动作）。返回是否确有该行。"""
        row = await self._db.fetch_one(
            "SELECT 1 FROM repo_tombstones WHERE repo_id = ?", (repo_id,)
        )
        if row is None:
            return False
        await self._db.write("DELETE FROM repo_tombstones WHERE repo_id = ?", (repo_id,))
        return True

    async def release_id(self, repo_id: str, *, snapshot_count: int | None = None) -> list[str]:
        """释放归档仓库的 ID：落墓碑（``released_at`` = 现在）后删除 ``repos`` 行。

        释放后该 id 立即可被新仓库复用（:meth:`create` 只查 ``repos``）。语义上等于
        用户声明「旧快照我不再通过本应用找回」——但**不删任何磁盘数据**，数据仍是
        手工可救的（守 §6 红线：记录/数据分离）。

        Returns:
            被级联删除的终态作业 ``log_path`` 列表（同 :meth:`hard_delete`）。
        """
        repo = await self.get(repo_id)
        if repo is None:
            return []
        now = utc_iso()
        await self.insert_tombstone(
            repo_id=repo_id,
            name=repo.name,
            repo_type=repo.type,
            url_display=repo.url,
            archived_at=repo.deleted_at or now,
            snapshot_count=snapshot_count,
            data_destroyed=False,
            released_at=now,
        )
        return await self._drop_row_legacy(repo_id)

    async def hard_delete(
        self,
        repo_id: str,
        *,
        data_destroyed: bool,
        snapshot_count: int | None = None,
    ) -> list[str]:
        """彻底删除仓库行（+ 终态作业级联），并留一条墓碑。

        与 :meth:`release_id` 的区别只在「留墓碑」：``data_destroyed=True`` 表示走了
        ``purge_disk``（目录内容 + 密码文件已销毁，**不可恢复**）。
        """
        repo = await self.get(repo_id)
        if repo is None:
            return []
        now = utc_iso()
        await self.insert_tombstone(
            repo_id=repo_id,
            name=repo.name,
            repo_type=repo.type,
            url_display=repo.url,
            archived_at=repo.deleted_at or now,
            snapshot_count=snapshot_count,
            data_destroyed=data_destroyed,
        )
        return await self._drop_row_legacy(repo_id)

    async def drop_row(self, repo_id: str, *, tx: Tx | None = None) -> None:
        """删除仓库行并级联清终态作业行（不做前置校验；**不返回**日志路径）。

        候选 1 把仓库删除改序为「行最后删」，日志路径必须由调用方**在删行之前**
        用 :meth:`JobRepo.terminal_log_paths` 取好——行一删就查不到了。

        Args:
            repo_id: 仓库 id。
            tx: 事务句柄；给了就整段落在同一事务内（墓碑 + 删 jobs + 删 repos）。
        Raises:
            sqlite3.IntegrityError: 仍有活跃任务行时外键会挡下删行（**不要加级联**——
                这是候选 1 的安全网：崩溃后可重试，行还在）。
        """
        terminal_ph = ",".join("?" * len(TERMINAL_JOB_STATUSES))
        delete_jobs = f"DELETE FROM jobs WHERE repo_id = ? AND status IN ({terminal_ph})"
        params: tuple[Any, ...] = (repo_id, *TERMINAL_JOB_STATUSES)
        delete_repos = "DELETE FROM repos WHERE id = ?"
        if tx is not None:
            await tx.execute(delete_jobs, params)
            await tx.execute(delete_repos, (repo_id,))
        else:
            await self._db.write(delete_jobs, params)
            await self._db.write(delete_repos, (repo_id,))

    async def _drop_row_legacy(self, repo_id: str) -> list[str]:
        """旧顺序：删行并**同时**返回日志路径（批 ② 迁移后删除本方法）。

        候选 1 的新顺序要求「日志路径先取、行最后删」，而本方法是先删行再让调用方
        拿路径去删文件——那样崩溃在中间就再也拿不到路径了。保留至 :meth:`release_id`
        与 :meth:`hard_delete` 改走 ``RepoLifecycleService``。
        """
        terminal_ph = ",".join("?" * len(TERMINAL_JOB_STATUSES))
        rows = await self._db.fetch_all(
            f"SELECT log_path FROM jobs WHERE repo_id = ? AND status IN ({terminal_ph})",
            (repo_id, *TERMINAL_JOB_STATUSES),
        )
        if rows:
            await self._db.write(
                f"DELETE FROM jobs WHERE repo_id = ? AND status IN ({terminal_ph})",
                (repo_id, *TERMINAL_JOB_STATUSES),
            )
        await self._db.write("DELETE FROM repos WHERE id = ?", (repo_id,))
        return [str(r["log_path"]) for r in rows]

    async def exists(self, repo_id: str) -> bool:
        """仓库是否存在（路由层 404 REPO_NOT_FOUND 判定的唯一实现点）。"""
        row = await self._db.fetch_one("SELECT 1 FROM repos WHERE id = ?", (repo_id,))
        return row is not None

    async def list_rclone_backed(self) -> list[tuple[str, str, str]]:
        """返回 ``url`` 以 ``rclone:`` 开头的仓库 ``[(id, name, url), ...]``。

        删除保护方案 P0：供「删 rclone 段前扫引用」使用。**只做 SQL 过滤**
        （``LIKE 'rclone:%'``），remote 段的精确比对由路由层完成——本层不解析
        rclone 语义，避免仓储层与命令行语义耦合。
        含归档仓库（``deleted_at`` 非空）一并返回，避免归档后引用被漏判。
        """
        rows = await self._db.fetch_all(
            "SELECT id, name, url FROM repos WHERE url LIKE 'rclone:%' "
            "ORDER BY (id = 'default') DESC, id ASC"
        )
        return [(str(row["id"]), str(row["name"]), str(row["url"] or "")) for row in rows]

    # ---------------------------------------------------------------- 写

    async def create(
        self,
        repo_id: str,
        name: str,
        *,
        url: str | None = None,
        host_tag: str | None = None,
    ) -> Repo:
        """新建仓库（url/host_tag 可空，日后再补；``source`` 恒 ``ui``——env 只覆盖 default）。

        Raises:
            ValidationError: id 形态非法或名称越界（含与既有 id 撞名）。
        """
        repo_id = (repo_id or "").strip()
        name = (name or "").strip()
        _validate_repo_id(repo_id)
        if not name or len(name) > 64:
            raise ValidationError("仓库名称须为 1~64 字符", data={"name": name})
        if await self.exists(repo_id):
            raise ValidationError("仓库 id 已存在", data={"repo_id": repo_id})
        now = utc_iso()
        await self._db.write(
            "INSERT INTO repos (id, name, url, host_tag, url_source, host_tag_source, created_at) "
            "VALUES (?, ?, ?, ?, 'ui', 'ui', ?)",
            (repo_id, name, url, host_tag, now),
        )
        created = await self.get(repo_id)
        if created is None:  # pragma: no cover - 理论不可达
            raise RuntimeError(f"仓库 {repo_id} 创建后无法读回")
        return created

    async def update(
        self,
        repo_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        host_tag: str | None = None,
    ) -> Repo:
        """更新仓库资料（name/url/host_tag 三态：None=不变 / ""=清除 / 非空=设置）。

        Raises:
            ValidationError: 仓库不存在或名称越界。
            EnvOverriddenError: url/host_tag 已被 env 覆盖且本次要改它（409）。
        """
        repo = await self.get(repo_id)
        if repo is None:
            raise ValidationError("仓库不存在", data={"repo_id": repo_id})
        assignments: list[str] = []
        params: list[Any] = []
        if name is not None:
            clean = name.strip()
            if not clean or len(clean) > 64:
                raise ValidationError("仓库名称须为 1~64 字符", data={"name": name})
            assignments.append("name = ?")
            params.append(clean)
        if url is not None:
            if repo.url_source == ConfigSource.ENV:
                raise EnvOverriddenError(data={"key": "repo.url", "repo_id": repo_id})
            assignments.append("url = ?")
            params.append(url.strip() or None)
        if host_tag is not None:
            if repo.host_tag_source == ConfigSource.ENV:
                raise EnvOverriddenError(data={"key": "repo.host_tag", "repo_id": repo_id})
            assignments.append("host_tag = ?")
            params.append(host_tag.strip() or None)
        if not assignments:
            return repo
        params.append(repo_id)
        await self._db.write(f"UPDATE repos SET {', '.join(assignments)} WHERE id = ?", tuple(params))
        updated = await self.get(repo_id)
        if updated is None:  # pragma: no cover - 理论不可达
            raise RuntimeError(f"仓库 {repo_id} 更新后无法读回")
        return updated

    async def list_mirror_refs(self, repo_id: str) -> list[tuple[int, str]]:
        """返回把本仓当**镜像目的地**的任务 ``[(id, name), ...]``（删除保护方案 P1，裁决 5）。

        ``mirror_repo_ids`` 是 **JSON 文本列**，故用 ``json_each`` 展开后按元素
        **完整相等**比对——不用 ``LIKE '%"id"%'``（会把 ``foo`` 误命中 ``foobar``，
        且手工维护的脏值/转义都会失真）。
        """
        rows = await self._db.fetch_all(
            "SELECT backup_tasks.id AS id, backup_tasks.name AS name "
        "FROM backup_tasks, json_each("
        "  CASE WHEN backup_tasks.mirror_repo_ids IS NULL OR backup_tasks.mirror_repo_ids = '' "
        "       THEN '[]' ELSE backup_tasks.mirror_repo_ids END"
        ") WHERE json_each.value = ? ORDER BY backup_tasks.id",
            (repo_id,),
        )
        return [(int(row["id"]), str(row["name"])) for row in rows]

    async def ensure_deletable(self, repo_id: str) -> None:
        """删除前置校验（**零副作用**）：default 禁删；有任务/活跃作业即拒；**被镜像引用亦拒**。

        从删除路径抽出的独立入口，供路由层的 ``purge_disk`` 流程
        **先校验后清理**——rmtree 若发生在校验之后，「拒绝删除却已毁数据」
        会比原来的死循环更糟（典型：default 禁删，但数据已被清空）。

        三门顺序**固定**（先廉后贵、先强后弱）：

        1. default 禁删（不查库即可判定）；
        2. 有备份任务或有活跃作业 → ``REPO_NOT_EMPTY``；
        3. 被其他仓库任务当**镜像目的地** → ``REPO_IN_USE_AS_MIRROR``（删除保护方案 P1，
           裁决 5：全量生效，不区分存量/新建）。

        Raises:
            ValidationError: default 仓库不可删。
            RepoNotEmptyError: 仓库下仍有备份任务或活跃作业。
            RepoInUseAsMirrorError: 仍被其他备份任务作为镜像目的地引用。
        """
        if repo_id == DEFAULT_REPO_ID:
            raise ValidationError("默认仓库不可删除", data={"repo_id": repo_id})
        active_ph = ",".join("?" * len(ACTIVE_JOB_STATUSES))
        row = await self._db.fetch_one(
            "SELECT (SELECT COUNT(*) FROM backup_tasks WHERE repo_id = ?) AS tasks, "
            f"(SELECT COUNT(*) FROM jobs WHERE repo_id = ? AND status IN ({active_ph})) AS active_jobs",
            (repo_id, repo_id, *ACTIVE_JOB_STATUSES),
        )
        tasks = int(row["tasks"]) if row else 0
        active_jobs = int(row["active_jobs"]) if row else 0
        if tasks or active_jobs:
            raise RepoNotEmptyError(repo_id, tasks=tasks, active_jobs=active_jobs)

        # 第三门：镜像引用（查询口径见 :meth:`list_mirror_refs`）
        mirror_rows = await self.list_mirror_refs(repo_id)
        if mirror_rows:
            raise RepoInUseAsMirrorError(repo_id, tasks=mirror_rows)

    # 注：原 ``delete()``（ensure_deletable + _drop_row）全仓零调用点，且与 ``hard_delete``
    # 是同一件事的第二份实现——候选 1 批次 0 已删除。删除路径统一走 ``hard_delete``
    # （留墓碑）与即将落地的 ``RepoLifecycleService``。

    # ---------------------------------------------------------------- env 同步专用（无写保护）

    async def set_field_from_env(self, repo_id: str, field: str, value: str | None) -> None:
        """env 同步写入（``source='env'``；绕过界面写保护，仅 ``env_sync`` 可调）。

        Args:
            repo_id: 仓库 id（env 只覆盖 default）。
            field: ``url`` 或 ``host_tag``。
            value: env 值；None 表示清除并回落 ``ui`` 来源。
        """
        if field not in ("url", "host_tag"):
            raise ValidationError("不支持的仓库字段", data={"field": field})
        source_column = f"{field}_source"
        if value:
            await self._db.write(
                f"UPDATE repos SET {field} = ?, {source_column} = 'env' WHERE id = ?",
                (value, repo_id),
            )
        else:
            await self._db.write(
                f"UPDATE repos SET {field} = NULL, {source_column} = 'ui' WHERE id = ?",
                (repo_id,),
            )


def _validate_repo_id(repo_id: str) -> None:
    """仓库 id 形态校验（与 config 键名同族口径，防路径/切换器键污染）。"""
    import re

    if not re.match(REPO_ID_PATTERN, repo_id):
        raise ValidationError(
            "仓库 id 须为小写字母开头，仅含小写字母/数字/下划线/连字符（≤64 字符）",
            data={"repo_id": repo_id},
        )


def _to_repo(row: Any) -> Repo:
    """将 sqlite3.Row 转换为 :class:`Repo`。"""
    keys = row.keys() if hasattr(row, "keys") else ()
    deleted_at = row["deleted_at"] if "deleted_at" in keys else None
    return Repo(
        id=str(row["id"]),
        name=str(row["name"]),
        url=None if row["url"] is None else str(row["url"]),
        host_tag=None if row["host_tag"] is None else str(row["host_tag"]),
        url_source=str(row["url_source"]),
        host_tag_source=str(row["host_tag_source"]),
        created_at=str(row["created_at"]),
        deleted_at=None if deleted_at is None else str(deleted_at),
    )
