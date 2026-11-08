"""单任务配置包（``kind=backup-task``）的打包与落地管线。

**范围（阶段 ② 收窄，2026-11-08）**：本模块只服务 **E36b 单任务配置导出**
（``routers/backup_tasks.py::export_task``）与其对应导入
（``routers/ops.py::import_config``）。原「全量配置迁移」已由
《全站数据备份与恢复设计方案》取代（owner 裁决 D5/D6），**全量配置导出端点与全量导入
支路均已删除**；导入端点保留但**只接受 ``kind=backup-task``**，``kind=full`` / 无 kind 的
旧包一律 422 并指路新功能。

保留的三条语义（owner 2026-10-18 裁决，单任务包仍适用）：

1. **凭据口令加密导出**：导出口令经 PBKDF2-HMAC-SHA256（600k 迭代，OWASP 2023
   建议）派生 AES-256 密钥，AES-256-GCM 认证加密凭据段（``cred.*``）。错口令 =
   GCM ``InvalidTag`` = 解密失败（绝不静默通过）；salt 每次导出随机。
2. **包内自带仓库清单，先仓后任务**：``mirror_repo_ids`` 按 id 原样接上；
   接上前校验「目标机该仓 id 存在且 url 与包内一致」，不一致置空 + 报告警告
   （防快照复制到完全不相干的仓）。
3. **落位按匹配、不覆盖**：仓按 id、任务按 ``(repo_id, name)`` 匹配——任务 id
   是目标机自增主键，跨机不可带（tag 由 ``task-<id>`` 回写，同样不可带）。
   单任务包**没有覆盖语义**，同仓同名任务一律跳过并在报告里说明（R-5）。

S3/S6 合规：明文凭据只存在于加密前后的内存瞬间，不进日志/审计/异常；
导出与导入各留一条审计（detail 只含计数，绝不含凭据或包原文）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.db.repositories.config_repo import ConfigRepo
from app.errors import ValidationError
from app.services.pack_crypto import PASSWORD_MIN_LENGTH
from app.services.pack_crypto import open_sealed as _open_secrets
from app.services.pack_crypto import seal as _seal_secrets

# 包格式标识与版本（未来结构变更只增 version；旧版是否兼容届时裁决）
# 注意：本 format 属**单任务包**；全站包是另一个 format（`restic-web-site-backup`，
# 见 services/site_backup.py），两者互不入对方的解析器。
PACK_FORMAT = "restic-web-config-export"
# v2（2026-11-02）：secrets 段新增并列 `degraded` 映射——导出时把「密钥源失效、
# 值读不出来」与「本来就没配」区分开（方案 §11 遗留）。接收端据此可告警而非静默丢凭据。
PACK_VERSION = 2
# 导出恒为 PACK_VERSION；导入**兼容**旧版：项目尚在开发期，无生产旧实例，
# 但开发期已存在的 v1 导出包不应作废，故接受集合而非严格相等。
_SUPPORTED_PACK_VERSIONS = frozenset({1, 2})
# 包形态（P2 单任务导出）。阶段 ② 收窄后 **只有单任务包**是合法内容：
# ``PACK_KIND_FULL`` 保留为常量，仅用于「识别旧全量包并明确拒绝」——
# 它不是一条可走的导入路径（``parse_export_pack`` 对 full/无 kind 一律 422）。
PACK_KIND_FULL = "full"
PACK_KIND_TASK = "backup-task"
_PACK_KINDS = (PACK_KIND_FULL, PACK_KIND_TASK)

# 单任务包只带任务相关仓的凭据；``cred.*`` 是唯一进加密段的明文键族
# （全局 ``notify.*`` / ``behavior.*`` 不搭单任务包顺风车，见 E36b docstring）。
_EXPORT_CONFIG_PREFIXES = ("behavior.", "notify.")


@dataclass(frozen=True)
class RepoEntry:
    """包内仓库条目（不含任何凭据——凭据在加密段）。"""

    id: str
    name: str
    url: str | None
    host_tag: str | None


@dataclass
class ImportReport:
    """导入结果报告（调用方逐项展示；reason 面向用户可读）。

    单任务包没有覆盖语义，故只有 created / reused / skipped 三类结果，没有
    ``*_updated`` 与 ``notify_updated``——全局 ``notify.*`` 从不被单任务包写入。
    """

    repos_created: list[str] = field(default_factory=list)
    repos_reused: list[str] = field(default_factory=list)
    repos_skipped: list[dict[str, str]] = field(default_factory=list)
    tasks_created: list[str] = field(default_factory=list)
    tasks_skipped: list[dict[str, str]] = field(default_factory=list)
    mirror_warnings: list[dict[str, str]] = field(default_factory=list)
    creds_imported: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "repos": {
                "created": self.repos_created,
                "reused": self.repos_reused,
                "skipped": self.repos_skipped,
            },
            "tasks": {
                "created": self.tasks_created,
                "skipped": self.tasks_skipped,
            },
            "mirror_warnings": self.mirror_warnings,
            "creds_imported": self.creds_imported,
        }


# 加解密原语（PBKDF2 + AES-GCM）在 app/services/pack_crypto.py —— 全站备份包共用同一份，
# 本模块以 _seal_secrets / _open_secrets 别名引用，签名与行为保持不变。


# ---------------------------------------------------------------- 导出


def _validate_export_password(password: str) -> None:
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise ValidationError(
            f"导出口令至少 {PASSWORD_MIN_LENGTH} 位（它保护全部凭据）",
            data={"field": "password"},
        )


def build_export_pack(
    *,
    repos: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    config_rows: list[tuple[str, str]],
    password: str,
    created_at: str,
    kind: str = PACK_KIND_TASK,
    degraded_rows: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """构建导出包（凭据段加密；明文凭据只在内存瞬时存在）。

    Args:
        repos: 仓库清单（id/name/url/host_tag）。
        tasks: 任务清单（``Task.get_dict`` 形态，含 id/时间戳——导入侧剔除）；
            单任务导出只放一条。
        config_rows: ``(key, value)`` config 明文（调用方给**已解密**明文）。
            调用方只传该任务相关仓的 ``cred.*`` 段（全局键不进单任务包）。
        password: 导出口令（≥ ``PASSWORD_MIN_LENGTH``）。
        created_at: 包生成时间（ISO 字符串）。
        kind: 包形态；恒为 ``PACK_KIND_TASK``（全量包已随阶段 ② 退役）。
        degraded_rows: ``{key: reason}``——**密钥源失效、值读不出来**的键（v2）。
            它与「本来就没配」在 v1 包里不可区分（两者都是空串），接收端会静默
            丢掉凭据；v2 把它写进**加密段**（原因里只有密钥源标识，不含任何值），
            接收端据此可告警。缺省空 = 无降级行。
    """
    _validate_export_password(password)
    creds: dict[str, str] = {}
    notify_plain: dict[str, str] = {}
    for key, value in config_rows:
        if key.startswith("cred."):
            creds[key] = value
        elif key.startswith(_EXPORT_CONFIG_PREFIXES):
            if ConfigRepo.is_sensitive(key):
                creds[key] = value
            else:
                notify_plain[key] = value
    secrets_payload: dict[str, Any] = {"creds": creds}
    if degraded_rows:
        # 原因串由 ConfigRepo._degraded_reason 生成：只含密钥源标识与异常类别，
        # 不含键值/密文（S3）。放进加密段而非明文段：键名本身即暴露「配过哪些凭据」。
        secrets_payload["degraded"] = {str(k): str(v) for k, v in degraded_rows.items()}
    enc_head, blob = _seal_secrets(secrets_payload, password)
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "kind": kind,
        "created_at": created_at,
        "encryption": enc_head,
        "data": {
            "repos": repos,
            "tasks": tasks,
            "notify": notify_plain,
            "secrets": blob,
        },
    }


def parse_export_pack(pack: dict[str, Any], password: str) -> dict[str, Any]:
    """解析并解密**单任务包**，返回 ``{"repos", "tasks", "notify", "creds", "degraded", "kind"}``。

    ``kind`` 必须是 ``PACK_KIND_TASK``：全量配置迁移已由全站备份取代（owner 裁决
    D5/D6），``kind=full`` 与无 kind 的旧全量包在此**明确拒绝**并指路新功能——
    本端点只落地单任务维度，不猜全量包的意图（Q3 / §9.3-G8）。

    Raises:
        ValidationError: 格式/版本/kind/结构非法。
        PasswordWrongError: 口令错误或包被篡改。
    """
    _validate_export_password(password)
    if not isinstance(pack, dict) or pack.get("format") != PACK_FORMAT:
        raise ValidationError("不是本系统的配置导出文件")
    if pack.get("version") not in _SUPPORTED_PACK_VERSIONS:
        raise ValidationError(f"导出包版本不支持（{pack.get('version')}）")
    raw_kind = pack.get("kind")
    if raw_kind is not None and raw_kind not in _PACK_KINDS:
        raise ValidationError(
            f"导出包形态不支持：{raw_kind}", data={"kind": raw_kind, "allowed": list(_PACK_KINDS)}
        )
    if raw_kind != PACK_KIND_TASK:
        # 全量包（kind=full 或无 kind 的老包）：端点已收窄，不再落地全量语义。
        raise ValidationError(
            "这是全量配置迁移包，该端点已停止支持；请在原机用「设置 → 全站备份 / 恢复」"
            "重新导出全站备份包",
            data={"kind": raw_kind or PACK_KIND_FULL, "allowed": [PACK_KIND_TASK]},
        )
    kind = PACK_KIND_TASK
    data = pack.get("data")
    if not isinstance(data, dict):
        raise ValidationError("导出包缺少数据段")
    repos, tasks, notify = data.get("repos"), data.get("tasks"), data.get("notify")
    if not isinstance(repos, list) or not isinstance(tasks, list) or not isinstance(notify, dict):
        raise ValidationError("导出包数据段结构非法")
    secrets = _open_secrets(pack.get("encryption") or {}, str(data.get("secrets") or ""), password)
    creds = secrets.get("creds")
    if not isinstance(creds, dict):
        raise ValidationError("导出包凭据段结构非法")
    raw_degraded = secrets.get("degraded")
    if raw_degraded is None:
        degraded: dict[str, str] = {}  # v1 包无该字段 → 无降级信息（旧行为）
    elif isinstance(raw_degraded, dict):
        degraded = {str(k): str(v) for k, v in raw_degraded.items()}
    else:
        raise ValidationError("导出包降级段结构非法")
    return {
        "repos": repos,
        "tasks": tasks,
        "notify": notify,
        "creds": creds,
        "degraded": degraded,
        "kind": kind,
    }


# ---------------------------------------------------------------- 导入辅助


def mirror_ids_for_import(
    mirror_ids: list[str], pack_urls: dict[str, str | None], repo_final_urls: dict[str, str | None]
) -> tuple[list[str], list[dict[str, str]]]:
    """导入任务的镜像引用校验（裁决 2 细则）。

    规则：镜像 id 必须在**包内仓库清单**中（id 语义来自导出方），且目标机该仓
    最终 url 与包内一致——不一致（含仓缺失）一律置空 + 警告，绝不静默指向
    一个不相干的仓。

    Returns:
        ``(保留的镜像 id 列表, 警告列表)``。
    """
    kept: list[str] = []
    warnings: list[dict[str, str]] = []
    for mid in mirror_ids:
        if mid not in pack_urls:
            warnings.append({"mirror_id": mid, "reason": "包内不存在该仓库"})
        elif (repo_final_urls.get(mid) or None) != (pack_urls.get(mid) or None):
            warnings.append({"mirror_id": mid, "reason": "目标机同 id 仓库 url 与包内不一致"})
        else:
            kept.append(mid)
    # 保序去重（与任务写路径 _normalize_mirror_ids 同口径）
    seen: set[str] = set()
    kept = [mid for mid in kept if not (mid in seen or seen.add(mid))]
    return kept, warnings


def task_values_from_pack(task: dict[str, Any], repo_id: str, mirror_ids: list[str]) -> dict[str, Any]:
    """把包内任务 dict 转成落库 values（剔除跨机不可带列；归一 JSON 列）。

    剔除：``id``/``tag``（目标机自增+回写）、``created_at``/``updated_at``、
    ``last_run_at``（目标机无对应执行历史，带回会谎报「上次备份时间」）、
    ``dry_run_pending``（恒 0——导入不是待确认的新建草稿）、``host_tag``
    （repos 表字段，被导出契约带进包；``backup_tasks`` 无此列，落库前必须剔除，
    否则 ``TaskRepo.create`` 的 ``_assert_columns`` 抛键校验错 = 导入 500——
    真机 E2E 2026-10-18 实证：update 路径不校验故覆盖导入能过，create 路径
    （异地导入/新任务名）必炸）。``repo_id`` 用导入侧解析值显式设置（以仓匹配
    结果为准，不信任包内归属）。
    """
    drop = {
        "id", "tag", "repo_id", "created_at", "updated_at", "last_run_at",
        "dry_run_pending", "host_tag",
    }
    values = {k: v for k, v in task.items() if k not in drop}
    values["repo_id"] = repo_id
    values["mirror_repo_ids"] = list(mirror_ids)
    return values


def creds_to_import(creds: dict[str, str], repo_ids: list[str]) -> list[tuple[str, str]]:
    """按导入后的仓库 id 过滤凭据键（``cred.{repo_id}.*``；其他仓的凭据不带）。"""
    prefixes = tuple(f"cred.{rid}." for rid in repo_ids)
    return [(k, v) for k, v in creds.items() if k.startswith(prefixes)]


__all__ = [
    "PACK_FORMAT",
    "PACK_KIND_FULL",
    "PACK_KIND_TASK",
    "PACK_VERSION",
    "PASSWORD_MIN_LENGTH",
    "ImportReport",
    "RepoEntry",
    "build_export_pack",
    "creds_to_import",
    "mirror_ids_for_import",
    "parse_export_pack",
    "task_values_from_pack",
]
