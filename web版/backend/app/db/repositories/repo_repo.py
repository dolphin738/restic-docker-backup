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

from app.db.database import Database
from app.errors import EnvOverriddenError, ValidationError
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
        """按 id 读取仓库；不存在返回 None。"""
        row = await self._db.fetch_one(
            "SELECT id, name, url, host_tag, url_source, host_tag_source, created_at "
            "FROM repos WHERE id = ?",
            (repo_id,),
        )
        return _to_repo(row) if row else None

    async def list_all(self) -> list[Repo]:
        """返回全部仓库（default 恒首位，其余按创建序）。"""
        rows = await self._db.fetch_all(
            "SELECT id, name, url, host_tag, url_source, host_tag_source, created_at "
            "FROM repos ORDER BY (id = 'default') DESC, created_at ASC, id ASC"
        )
        return [_to_repo(row) for row in rows]

    async def exists(self, repo_id: str) -> bool:
        """仓库是否存在（路由层 404 REPO_NOT_FOUND 判定的唯一实现点）。"""
        row = await self._db.fetch_one("SELECT 1 FROM repos WHERE id = ?", (repo_id,))
        return row is not None

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

    async def delete(self, repo_id: str) -> bool:
        """删除仓库。

        Raises:
            ValidationError: default 仓库不可删（409 语义由路由层映射）。
            EnvOverriddenError: 仓库下仍有任务或历史任务（先清理再删）。
        """
        if repo_id == DEFAULT_REPO_ID:
            raise ValidationError("默认仓库不可删除", data={"repo_id": repo_id})
        row = await self._db.fetch_one(
            "SELECT (SELECT COUNT(*) FROM backup_tasks WHERE repo_id = ?) AS tasks, "
            "(SELECT COUNT(*) FROM jobs WHERE repo_id = ?) AS jobs",
            (repo_id, repo_id),
        )
        tasks = int(row["tasks"]) if row else 0
        jobs = int(row["jobs"]) if row else 0
        if tasks or jobs:
            raise EnvOverriddenError(
                "仓库下仍有任务或历史任务，请先清理后再删除",
                data={"repo_id": repo_id, "tasks": tasks, "jobs": jobs},
            )
        await self._db.write("DELETE FROM repos WHERE id = ?", (repo_id,))
        return True

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
    return Repo(
        id=str(row["id"]),
        name=str(row["name"]),
        url=None if row["url"] is None else str(row["url"]),
        host_tag=None if row["host_tag"] is None else str(row["host_tag"]),
        url_source=str(row["url_source"]),
        host_tag_source=str(row["host_tag_source"]),
        created_at=str(row["created_at"]),
    )
