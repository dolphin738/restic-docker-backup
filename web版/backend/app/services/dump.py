"""DumpService（R-019，§7.5 签名实化）：pg 版本探测、逐库 dump、产物清理。

语义要点：
- **pg 主版本比对**（§7.5）：客户端 ``pg_dump`` 主版本 < 服务器主版本 → 抛
  :class:`PgVersionMismatchError`（任务 failed + UI 提示自建镜像口径）；
  mysql-client（MariaDB 系）无主版本强校验，仅记录；
- **产物空文件校验**：dump 成功退出但产物为 0 字节视同失败（禁止静默）；
- **清理 fail-safe**：``dump.retention_days <= 0`` 视为不启用；只触碰
  ``settings.dump_dir`` 白名单内的一级文件（S1），符号链接只删链接本身、绝不跟随；
- 探测/执行失败一律抛 :class:`DumpError`（结构化，原因明示）——由 JobRunner 的
  dump 阶段收敛为任务 failed，探测失败**不产生 5xx 通道**（无独立 dump 端点）。

红线落点：S1（dump_dir 路径字面量唯一出处 settings.py）、S2（argv 逐具名字段，
见 dump_cmd）、S3（密码仅 PGPASSWORD/MYSQL_PWD 环境通道）、C8（时间只走 utils/time）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

from app.constants import (
    DUMP_DATABASES_KEY,
    DUMP_ENABLED_KEY,
    DUMP_ENGINES,
    DUMP_HOST_KEY,
    DUMP_PASSWORD_KEY,
    DUMP_PORT_DEFAULT,
    DUMP_PORT_KEY,
    DUMP_PROBE_TIMEOUT_SECONDS,
    DUMP_RETENTION_DAYS_DEFAULT,
    DUMP_RETENTION_DAYS_KEY,
    DUMP_RUN_TIMEOUT_SECONDS,
    DUMP_TYPE_KEY,
    DUMP_USER_KEY,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import AppError, ValidationError
from app.services.dump_cmd import PG_DUMP_BINARY, DumpCmd, DumpConfig, assert_component
from app.settings import Settings, get_settings
from app.utils.time import now_utc

logger = logging.getLogger(__name__)

# sqlite 产物后缀（备份接口产出的是 SQLite 库文件而非 SQL 文本，命名如实）
SQLITE_DUMP_SUFFIX = ".sqlite"

# psql 服务端版本探测（SHOW 语句不经 A1 敏感词：DB 访问红线约束的是本仓库数据库）
_PSQL_SERVER_VERSION_ARGS = ("--no-psqlrc", "--tuples-only", "--no-align", "--command", "SHOW server_version;")


class DumpError(AppError):
    """dump 阶段失败（探测/执行/产物校验；原因明示，禁止静默）。"""

    code = "DUMP_FAILED"

    def __init__(self, reason: str) -> None:
        """构造 dump 失败错误。

        Args:
            reason: 可直接展示的中文原因。
        """
        super().__init__(f"dump 失败：{reason}", data={"reason": reason})


class PgVersionMismatchError(DumpError):
    """pg_dump 客户端主版本低于服务器主版本（§7.5：任务 failed + UI 提示自建镜像）。"""

    def __init__(self, client_major: int, server_major: int) -> None:
        """构造版本不匹配错误。

        Args:
            client_major: 镜像内 pg_dump 主版本。
            server_major: 服务器主版本。
        """
        super().__init__(
            f"镜像内 client 为 {client_major}、server 为 {server_major}："
            "请以 Alpine edge 源自构建镜像，或等待镜像更新收录"
        )
        self.data = {"client_major": client_major, "server_major": server_major}


async def load_dump_config(config: ConfigRepo) -> DumpConfig | None:
    """从 config ``dump.*`` 键装配配置快照。

    Returns:
        :class:`DumpConfig`；``dump.enabled != 1`` 时返回 ``None``（dump 阶段跳过，
        保持既有语义）。``dump.enabled=1`` 但引擎/库名清单非法时抛 422
        （配置错误必须在任务启动时显式失败，禁止静默降级）。

    Raises:
        ValidationError: 引擎不在白名单、或 ``dump.databases`` 为空/非法 JSON。
    """
    if ((await config.get_value(DUMP_ENABLED_KEY)) or "").strip() != "1":
        return None
    engine = ((await config.get_value(DUMP_TYPE_KEY)) or "pg").strip() or "pg"
    if engine not in DUMP_ENGINES:
        raise ValidationError(
            f"dump.type 非法：{engine}", data={"field": "dump.type", "allowed": list(DUMP_ENGINES)}
        )
    databases = _parse_databases(await config.get_value(DUMP_DATABASES_KEY))
    if not databases:
        raise ValidationError("dump.databases 未配置（至少一个库名/库文件路径）", data={"field": "dump.databases"})
    return DumpConfig(
        engine=engine,
        host=((await config.get_value(DUMP_HOST_KEY)) or "").strip(),
        port=_parse_port(await config.get_value(DUMP_PORT_KEY)),
        user=((await config.get_value(DUMP_USER_KEY)) or "").strip(),
        # S3：明文只在本进程内存中流转（argv 绝不带，日志/审计不落）
        password=(await config.get_value(DUMP_PASSWORD_KEY)) or "",
        databases=databases,
        retention_days=_parse_retention_days(await config.get_value(DUMP_RETENTION_DAYS_KEY)),
    )


def _parse_databases(raw: str | None) -> tuple[str, ...]:
    """解析 ``dump.databases``（JSON 数组；坏值告警回退空 → 调用方拒绝启用）。"""
    if not raw or not raw.strip():
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("dump.databases 配置非法（非 JSON 数组）：%r", raw[:100])
        return ()
    if not isinstance(payload, list):
        logger.warning("dump.databases 配置非法（应为 JSON 数组）")
        return ()
    return tuple(str(item).strip() for item in payload if str(item).strip())


def _parse_port(raw: str | None) -> int:
    """解析 ``dump.port``（非法值回退缺省 5432 并告警）。"""
    if raw is None or not raw.strip():
        return DUMP_PORT_DEFAULT
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("dump.port 配置非法：%r，回退缺省 %s", raw, DUMP_PORT_DEFAULT)
        return DUMP_PORT_DEFAULT


def _parse_retention_days(raw: str | None) -> int:
    """解析 ``dump.retention_days``（非法值回退缺省；<=0 原样返回 = 不启用清理）。"""
    if raw is None or not raw.strip():
        return DUMP_RETENTION_DAYS_DEFAULT
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("dump.retention_days 配置非法：%r，回退缺省 %s", raw, DUMP_RETENTION_DAYS_DEFAULT)
        return DUMP_RETENTION_DAYS_DEFAULT


class DumpService:
    """数据库 dump 服务（R-019）：探测 → 逐库导出 → 产物清理。"""

    def __init__(
        self,
        config: ConfigRepo | None = None,
        settings: Settings | None = None,
        cmd: DumpCmd | None = None,
    ) -> None:
        """构造 dump 服务。

        Args:
            config: 配置仓储（``dump.*`` 键装配）；仅 ``run`` 便捷路径需要。
            settings: 运行期配置（``dump_dir`` 白名单来源）；缺省取进程单例。
            cmd: 命令拼装器；缺省新建（测试可注入替身）。
        """
        self._config = config
        self._settings = settings or get_settings()
        self._cmd = cmd or DumpCmd()

    # ---------------------------------------------------------------- 版本探测（§7.5）

    async def probe_pg(self, cfg: DumpConfig) -> tuple[int, int]:
        """比对 pg 客户端与服务器主版本（§7.5）。

        Returns:
            ``(client_major, server_major)``。

        Raises:
            ValidationError: host/user 组件非法（与 build_pg 同一校验点，D1 对齐；
                **先于任何子进程调用**执行——配置错误 fail-fast，不产生外部命令）。
            DumpError: 服务器连接失败或版本输出不可解析。
            PgVersionMismatchError: 客户端主版本 < 服务器主版本。
        """
        # host/user 与 build_pg（dump_cmd.assert_component）同一校验点（D1 对齐）：
        # 探测端点的 argv 同样不允许组件以 '-' 开头或含换行（S2 注入面一致）。
        host = assert_component(cfg.host, "host") if cfg.host else ""
        user = assert_component(cfg.user, "user") if cfg.user else ""

        client_exit, client_text = await self._capture([PG_DUMP_BINARY, "--version"], {})
        if client_exit != 0:
            raise DumpError(f"pg_dump 不可用（退出码 {client_exit}）：{client_text.strip()[:200]}")
        client_major = _major_version(client_text)
        if client_major is None:
            raise DumpError(f"pg_dump 版本输出不可解析：{client_text.strip()[:100]}")

        server_argv = ["psql"]
        if host:
            server_argv.extend(["--host", host])
        if cfg.port > 0:
            server_argv.extend(["--port", str(cfg.port)])
        if user:
            server_argv.extend(["--username", user])
        server_argv.extend(_PSQL_SERVER_VERSION_ARGS)
        server_exit, server_text = await self._capture(server_argv, self._cmd.env_for(cfg))
        if server_exit != 0:
            raise DumpError(f"PostgreSQL 服务器连接失败（退出码 {server_exit}）：{server_text.strip()[:200]}")
        server_major = _major_version(server_text)
        if server_major is None:
            raise DumpError(f"服务器版本输出不可解析：{server_text.strip()[:100]}")

        if client_major < server_major:
            raise PgVersionMismatchError(client_major, server_major)
        logger.info("pg 版本探测通过：client=%s server=%s", client_major, server_major)
        return client_major, server_major

    # ---------------------------------------------------------------- 执行（§7.5）

    async def run(self, cfg: DumpConfig, out_dir: Path | None = None, *, tag: str = "manual") -> Path:
        """逐库执行 dump 并做产物空文件校验。

        Args:
            cfg: dump 配置快照（含库名清单）。
            out_dir: 产物目录；缺省 ``settings.dump_dir``（S1 白名单）。
            tag: 产物文件名标签（任务 id / ``task-<id>``）。

        Returns:
            产物目录（逐库产物为 ``<db>-<tag>-<ts>.sql`` / sqlite 为 ``.sqlite``）。

        Raises:
            DumpError: 任一库执行失败或产物为空文件。
        """
        target_dir = Path(out_dir) if out_dir is not None else self._settings.dump_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")

        for database in cfg.databases:
            if cfg.engine == "sqlite":
                out_path = await self._dump_sqlite(database, target_dir, tag, stamp)
            else:
                out_path = await self._dump_via_cmd(cfg, database, target_dir, tag, stamp)
            if not out_path.is_file() or out_path.stat().st_size == 0:
                # 空产物视同失败：restic 备份一份空文件毫无价值且掩盖故障（禁止静默）
                raise DumpError(f"产物为空文件：{out_path.name}")
            logger.info("dump 完成：%s（%s 字节）", out_path.name, out_path.stat().st_size)
        return target_dir

    async def _dump_via_cmd(
        self, cfg: DumpConfig, database: str, target_dir: Path, tag: str, stamp: str
    ) -> Path:
        """经 pg_dump/mysqldump 导出单库（create_subprocess_exec，超时 kill）。"""
        out_path = target_dir / f"{database}-{tag}-{stamp}.sql"
        argv = self._cmd.build(cfg.engine, cfg=cfg, database=database, out_path=str(out_path))
        exit_code, output = await self._exec(argv, self._cmd.env_for(cfg), DUMP_RUN_TIMEOUT_SECONDS)
        if exit_code != 0:
            raise DumpError(
                f"{cfg.engine} 库 {database} 导出失败（退出码 {exit_code}）：{output.strip()[:200]}"
            )
        return out_path

    async def _dump_sqlite(self, database: str, target_dir: Path, tag: str, stamp: str) -> Path:
        """经 Python sqlite3 备份接口导出（镜像零新增依赖，PRD AC3）。"""
        source = Path(database)
        if not source.is_file():
            raise DumpError(f"sqlite 库文件不存在：{database}")
        out_path = target_dir / f"{source.name}-{tag}-{stamp}{SQLITE_DUMP_SUFFIX}"
        await asyncio.to_thread(_sqlite_backup_sync, source, out_path)
        return out_path

    # ---------------------------------------------------------------- 清理（Q10 基线）

    def cleanup(self, keep_days: int, *, now: datetime | None = None) -> tuple[int, int]:
        """清理 ``settings.dump_dir`` 内超期产物（同步方法；调用方放线程池）。

        Args:
            keep_days: 保留天数；``<= 0`` 视为不启用（fail-safe，与 retention 同口径）。
            now: 当前时刻；缺省取真实 UTC 时间（C8）。

        Returns:
            ``(deleted_count, freed_bytes)``。
        """
        if keep_days <= 0:
            return 0, 0
        cutoff = (now or now_utc()) - timedelta(days=keep_days)
        return _purge_dump_files_sync(self._settings.dump_dir, cutoff.timestamp())

    # ---------------------------------------------------------------- 子进程封装

    async def _capture(self, argv: list[str], env_extras: dict[str, str]) -> tuple[int, str]:
        """运行一次短探测命令，返回 ``(exit_code, 合并输出)``（超时 kill）。"""
        return await self._exec(argv, env_extras, DUMP_PROBE_TIMEOUT_SECONDS)

    async def _exec(self, argv: list[str], env_extras: dict[str, str], timeout: float) -> tuple[int, str]:
        """执行外部命令（不经 shell），超时 kill；返回 ``(exit_code, 合并输出)``。"""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, **env_extras},
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as exc:
            proc.kill()
            with suppress(Exception):
                await proc.wait()
            raise DumpError(f"命令超时（>{timeout:g}s）：{Path(argv[0]).name}") from exc
        text = (out or b"").decode("utf-8", errors="replace")
        return proc.returncode if proc.returncode is not None else -1, text


def _sqlite_backup_sync(source: Path, target: Path) -> None:
    """sqlite3 备份接口（同步）：源库 → 产物文件（线程池内执行）。"""
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _purge_dump_files_sync(dump_dir: Path, cutoff_epoch: float) -> tuple[int, int]:
    """同步删除 ``dump_dir`` 一级目录内 mtime 早于阈值的产物文件（S1：不递归、不越界）。

    符号链接只删链接本身、绝不跟随（防止白名单外目标被间接触达）。

    Returns:
        ``(deleted_count, freed_bytes)``。
    """
    deleted = 0
    freed = 0
    if not dump_dir.is_dir():
        return deleted, freed
    for entry in dump_dir.iterdir():
        try:
            if entry.is_symlink():
                entry.unlink()
                deleted += 1
                continue
            stat = entry.stat()
            if not entry.is_file() or stat.st_mtime >= cutoff_epoch:
                continue
            entry.unlink()
        except OSError:
            continue  # 单文件失败不中断整轮清理（逐项隔离）
        deleted += 1
        freed += stat.st_size
    return deleted, freed


def _major_version(text: str) -> int | None:
    """从 ``pg_dump (PostgreSQL) 16.4`` / ``16.4 (Ubuntu ...)`` 形态文本提取主版本。"""
    for token in text.replace("(", " ").replace(")", " ").split():
        if token[:1].isdigit():
            head = token.split(".", 1)[0]
            if head.isdigit():
                return int(head)
    return None
