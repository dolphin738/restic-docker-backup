"""枚举定义：与 DB 列取值、API 契约一一对应（架构文档 §4/§5）。

全部继承 ``StrEnum``，序列化后即为其字符串值，便于直接落库与返回。
"""

from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    """任务类型白名单（E12，方案 §5）。"""

    BACKUP = "backup"
    RESTORE = "restore"
    FORGET = "forget"
    CHECK = "check"
    UNLOCK = "unlock"
    INIT = "init"
    DUMP = "dump"
    REPAIR = "repair"  # B1 自愈：restic repair index / repair packs（索引/卷一致性重建）
    RECOVER = "recover"  # B3 自愈：restic recover（找回未快照数据，RecreateDatabase 等价）
    PRUNE = "prune"  # 独立回收未引用数据块（restic prune；补漏 forget 未勾选 --prune 的遗留空间）


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
    """仓库类型（A1 起与 restic 后端 URL 协议前缀一一对应；type 派生不落库）。

    前缀 → 类型映射的单一事实源在 ``repo_repo._PREFIX_TO_TYPE``（新增后端只改一处）。
    注意 **gcs 的 URL 前缀是 ``gs:``**（restic 历史命名，早于 GCS 简称普及）——
    类型名与前缀不同形，映射表里显式登记，勿按名前缀推断。
    """

    LOCAL = "local"
    S3 = "s3"
    B2 = "b2"
    SFTP = "sftp"
    AZURE = "azure"
    GCS = "gcs"
    REST = "rest"
    RCLONE = "rclone"
