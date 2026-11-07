"""配置导入/导出（批次 E / D4，owner 2026-10-18 三裁决）。

裁决要点（本模块是唯一实现点）：

1. **凭据口令加密导出**：导出口令经 PBKDF2-HMAC-SHA256（600k 迭代，OWASP 2023
   建议）派生 AES-256 密钥，AES-256-GCM 认证加密凭据段（``cred.*`` 与敏感
   ``notify.*``）。错口令 = GCM ``InvalidTag`` = 解密失败（绝不静默通过）；
   salt 每次导出随机（同口令不同包密文不同）。
2. **包内自带仓库清单，先仓后任务**：``mirror_repo_ids`` 按 id 原样接上；
   接上前校验「目标机该仓 id 存在且 url 与包内一致」，不一致置空 + 报告警告
   （防快照复制到完全不相干的仓）。
3. **冲突默认跳过 + 报告**：仓按 id、任务按 ``(repo_id, name)`` 匹配——任务 id
   是目标机自增主键，跨机不可带（tag 由 ``task-<id>`` 回写，同样不可带）。
   ``replace_existing=True`` 时以包内版本整体替换（ConfirmGate 由前端负责）。

导出范围：仓库清单 + 全部任务 + ``behavior.*`` / ``notify.*`` 配置 + 凭据
（加密段）。**不导** ``dump.*``（本机 DB dump 配置，跨机语义弱）与
``security.*``（加密开关由目标机自管）。

S3/S6 合规：明文凭据只存在于加密前后的内存瞬间，不进日志/审计/异常；
导出与导入各留一条审计（detail 只含计数与 replace_existing，绝不含凭据或包原文）。
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
PACK_FORMAT = "restic-web-config-export"
# v2（2026-11-02）：secrets 段新增并列 `degraded` 映射——导出时把「密钥源失效、
# 值读不出来」与「本来就没配」区分开（方案 §11 遗留）。接收端据此可告警而非静默丢凭据。
PACK_VERSION = 2
# 导出恒为 PACK_VERSION；导入**兼容**旧版：项目尚在开发期，无生产旧实例，
# 但开发期已存在的 v1 导出包不应作废，故接受集合而非严格相等。
_SUPPORTED_PACK_VERSIONS = frozenset({1, 2})
# 包形态（P2 单任务导出）：导入侧按 kind 分流——「全量包」是覆盖语义（可含全局
# notify/behavior 与多仓多任务），「单任务包」只按任务维度落地，绝不套用全量覆盖。
# 旧包无该字段 → 视为 full（兼容既有导出文件）。
PACK_KIND_FULL = "full"
PACK_KIND_TASK = "backup-task"
_PACK_KINDS = (PACK_KIND_FULL, PACK_KIND_TASK)

# 导出的明文配置键族（非敏感）；其余键族见模块 docstring「不导」清单
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
    """导入结果报告（前端逐项展示；reason 面向用户可读）。"""

    repos_created: list[str] = field(default_factory=list)
    repos_reused: list[str] = field(default_factory=list)
    repos_updated: list[str] = field(default_factory=list)
    repos_skipped: list[dict[str, str]] = field(default_factory=list)
    tasks_created: list[str] = field(default_factory=list)
    tasks_updated: list[str] = field(default_factory=list)
    tasks_skipped: list[dict[str, str]] = field(default_factory=list)
    mirror_warnings: list[dict[str, str]] = field(default_factory=list)
    notify_updated: int = 0
    creds_imported: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "repos": {
                "created": self.repos_created,
                "reused": self.repos_reused,
                "updated": self.repos_updated,
                "skipped": self.repos_skipped,
            },
            "tasks": {
                "created": self.tasks_created,
                "updated": self.tasks_updated,
                "skipped": self.tasks_skipped,
            },
            "mirror_warnings": self.mirror_warnings,
            "notify_updated": self.notify_updated,
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
    kind: str = PACK_KIND_FULL,
    degraded_rows: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """构建导出包（凭据段加密；明文凭据只在内存瞬时存在）。

    Args:
        repos: 仓库清单（id/name/url/host_tag）。
        tasks: 任务清单（``Task.get_dict`` 形态，含 id/时间戳——导入侧剔除）；
            单任务导出只放一条。
        config_rows: ``(key, value)`` config 明文（调用方给**已解密**明文）。
            全量导出传全量；单任务导出只传该任务相关仓的 ``cred.*`` 段。
        password: 导出口令（≥ ``PASSWORD_MIN_LENGTH``）。
        created_at: 包生成时间（ISO 字符串）。
        kind: 包形态（``PACK_KIND_FULL`` / ``PACK_KIND_TASK``），导入侧据此分流。
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
    """解析并解密导出包，返回 ``{"repos", "tasks", "notify", "creds", "degraded", "kind"}``。

    ``kind``：缺失（旧版全量包）回退 ``PACK_KIND_FULL``；非白名单值直接拒绝——
    未知形态的包不该被猜着导入。

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
    if raw_kind is None:
        kind = PACK_KIND_FULL  # 旧包无 kind 字段 → 视为全量包
    elif raw_kind in _PACK_KINDS:
        kind = str(raw_kind)
    else:
        raise ValidationError(
            f"导出包形态不支持：{raw_kind}", data={"kind": raw_kind, "allowed": list(_PACK_KINDS)}
        )
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


def notify_pairs_to_import(notify: dict[str, Any], creds: dict[str, str]) -> list[tuple[str, str]]:
    """合并明文 notify 键与加密段里的敏感 notify 键。"""
    pairs: list[tuple[str, str]] = []
    for key, value in notify.items():
        if not key.startswith("notify."):
            continue
        if ConfigRepo.is_sensitive(key):
            continue  # 敏感 notify 键以加密段为准
        pairs.append((key, str(value)))
    for key, value in creds.items():
        if key.startswith("notify."):
            pairs.append((key, str(value)))
    return pairs


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
    "notify_pairs_to_import",
    "parse_export_pack",
    "task_values_from_pack",
]
