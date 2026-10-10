"""枚举定义：与 DB 列取值、API 契约一一对应（架构文档 §4/§5）。

全部继承 ``StrEnum``，序列化后即为其字符串值，便于直接落库与返回。
"""

from __future__ import annotations

from enum import StrEnum


class RunMode(StrEnum):
    """备份源挂载模式（compose ``RESTIC_WEB_MODE``，方案 §3.1/§3.2）。"""

    HOST = "host"
    SOURCES = "sources"


class HostMountMode(StrEnum):
    """/host 挂载读写模式（compose ``HOST_MOUNT_MODE``，方案 §3.3 能力四象限）。"""

    RO = "ro"
    RW = "rw"


class JobType(StrEnum):
    """任务类型白名单（E12，方案 §5）。"""

    BACKUP = "backup"
    RESTORE = "restore"
    FORGET = "forget"
    CHECK = "check"
    UNLOCK = "unlock"
    INIT = "init"
    DUMP = "dump"


class JobStatus(StrEnum):
    """任务状态机（方案 §5）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class JobTrigger(StrEnum):
    """任务触发来源。"""

    MANUAL = "manual"
    SCHEDULED = "scheduled"


class ConfigSource(StrEnum):
    """配置项来源（§4.3）：'ui' 界面可写，'env' 环境变量覆盖（409 ENV_OVERRIDDEN）。"""

    UI = "ui"
    ENV = "env"


class AuditResult(StrEnum):
    """审计结果（§4.6）。"""

    OK = "ok"
    DENIED = "denied"
    FAILED = "failed"


class RepoType(StrEnum):
    """仓库类型（P1 收敛：s3 / b2 / 本地路径，方案 §4.2）。"""

    LOCAL = "local"
    S3 = "s3"
    B2 = "b2"
