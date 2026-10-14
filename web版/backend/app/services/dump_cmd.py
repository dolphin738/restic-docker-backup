"""pg_dump / mysqldump 命令拼装（R-019，S2/S3；ResticCmd 模式复制）。

白名单纪律（援 ``app/services/restic.py`` 的硬约束）：
- argv 逐具名字段拼装——库名/主机/端口/用户名等全部经 :func:`assert_component`
  安全校验，**无自由字符串进 shell**（``create_subprocess_exec`` 直接 exec）；
- 密码**绝不**进 argv、日志或审计（S3）：pg 走 ``PGPASSWORD``、mysql 走
  ``MYSQL_PWD`` 环境变量通道（:meth:`DumpCmd.env_for`），pg_dump 额外带
  ``--no-password`` 杜绝交互式提示挂死后台任务；
- sqlite 不经外部命令：走 Python ``sqlite3`` 备份接口（镜像零新增依赖，PRD AC3），
  由 :class:`app.services.dump.DumpService` 直接调用。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.constants import DUMP_PORT_DEFAULT, DUMP_RETENTION_DAYS_DEFAULT
from app.errors import ValidationError

# 外部命令二进制名（镜像内 postgresql-client / mysql-client 自带；单一真源）
PG_DUMP_BINARY = "pg_dump"
MYSQLDUMP_BINARY = "mysqldump"

DumpEngine = str  # "pg" | "mysql" | "sqlite"（值域校验见 DUMP_ENGINES）


@dataclass(frozen=True)
class DumpConfig:
    """dump 配置快照（R-019）：由 config ``dump.*`` 键装配（见 dump.load_dump_config）。"""

    engine: str = "pg"
    host: str = ""
    port: int = DUMP_PORT_DEFAULT
    user: str = ""
    password: str = ""
    databases: tuple[str, ...] = ()
    retention_days: int = DUMP_RETENTION_DAYS_DEFAULT


def assert_component(value: str, field_name: str) -> str:
    """校验可作为命令行组件的字符串（防 flag 注入与换行注入，语义同 restic._assert_safe_value）。

    Args:
        value: 待校验字符串。
        field_name: 字段名（错误提示定位用）。

    Returns:
        原字符串。

    Raises:
        ValidationError: 以 ``-`` 开头（会被解析为 flag）、含 NUL 或换行。
    """
    if value.startswith("-"):
        raise ValidationError(
            f"dump 配置 {field_name} 不能以 '-' 开头（禁止注入命令行参数）", data={"field": field_name}
        )
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValidationError(f"dump 配置 {field_name} 含非法字符", data={"field": field_name})
    return value


class DumpCmd:
    """dump 命令拼装器（白名单）：pg_dump / mysqldump 的 argv 与凭据环境。"""

    def build(self, engine: str, *, cfg: DumpConfig, database: str, out_path: str) -> list[str]:
        """按引擎拼装单库 dump 的 argv（逐具名字段，S2）。

        Args:
            engine: ``pg`` / ``mysql``（sqlite 不经本方法，见模块 docstring）。
            cfg: dump 配置快照。
            database: 库名（pg/mysql）；逐库调用。
            out_path: 产物输出文件路径（写入 ``--file`` / ``--result-file``）。

        Returns:
            可直接交给 ``create_subprocess_exec`` 的 argv 列表。

        Raises:
            ValidationError: 引擎不支持或任一组件值非法。
        """
        if engine not in ("pg", "mysql"):
            raise ValidationError(f"不支持的 dump 引擎：{engine}", data={"engine": engine})
        database = assert_component(database, "databases")
        out_path = assert_component(out_path, "out_path")
        if engine == "pg":
            return self.build_pg(cfg, database=database, out_path=out_path)
        return self.build_mysql(cfg, database=database, out_path=out_path)

    def build_pg(self, cfg: DumpConfig, *, database: str, out_path: str) -> list[str]:
        """拼装单库 ``pg_dump`` argv（``--no-password``：密码只走 PGPASSWORD 通道）。"""
        argv: list[str] = [PG_DUMP_BINARY]
        if cfg.host:
            argv.extend(["--host", assert_component(cfg.host, "host")])
        if cfg.port > 0:
            argv.extend(["--port", str(int(cfg.port))])
        if cfg.user:
            argv.extend(["--username", assert_component(cfg.user, "user")])
        argv.extend(["--dbname", assert_component(database, "databases")])
        # 无 TTY 的后台任务里 pg_dump 缺密码会交互式提示并挂死——显式禁用提示，
        # 密码缺失时直接失败（原因进任务日志，禁止静默）。
        argv.append("--no-password")
        argv.extend(["--file", out_path])
        return argv

    def build_mysql(self, cfg: DumpConfig, *, database: str, out_path: str) -> list[str]:
        """拼装单库 ``mysqldump`` argv（密码只走 MYSQL_PWD 通道）。"""
        argv: list[str] = [MYSQLDUMP_BINARY]
        if cfg.host:
            argv.extend(["--host", assert_component(cfg.host, "host")])
        if cfg.port > 0:
            argv.extend(["--port", str(int(cfg.port))])
        if cfg.user:
            argv.extend(["--user", assert_component(cfg.user, "user")])
        # --databases：产物自带建库语句，恢复语义与 pg_dump --dbname 对齐
        argv.extend(["--databases", assert_component(database, "databases")])
        argv.extend(["--result-file", out_path])
        return argv

    def env_for(self, cfg: DumpConfig) -> dict[str, str]:
        """构造凭据环境变量（S3：密码仅经 env 通道，绝不进 argv）。

        Returns:
            pg → ``{"PGPASSWORD": ...}``；mysql → ``{"MYSQL_PWD": ...}``；
            sqlite / 密码为空 → 空字典。
        """
        if not cfg.password:
            return {}
        if cfg.engine == "pg":
            return {"PGPASSWORD": cfg.password}
        if cfg.engine == "mysql":
            return {"MYSQL_PWD": cfg.password}
        return {}
