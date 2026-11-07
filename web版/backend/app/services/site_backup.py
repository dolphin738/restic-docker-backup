"""全站备份包的导出/导入编排（方案 §3–§4.5；W3 服务层）。

职责：把 DB 快照（:class:`SiteRepo`）与文件面（仓库密码文件 / `rclone.conf` / `rclone.pass`）
拼成一份**强制加密**的包，并把包还原到目标机。入口分工与方案 §4.3 的校验顺序一一对应：

- :func:`collect_state` —— §3.3 导出：事务内取表、事务外读文件；密码文件缺失 fail-closed；
- :func:`build_site_pack` —— §3.4：外层五键 + AAD 绑头 + 载荷整体 AES-256-GCM；
- :func:`parse_site_pack` —— §4.3 第 1–5 步：**纯内存**校验与解密（不碰 DB / 文件）；
- :func:`parse_site_pack_for_app` —— 路由层入口：读一次 `user_version` 后转调上面；
- :func:`describe_target` —— §4.3 第 6 步的只读部分：目标现状摘要与障碍项（inspect 用，零写入）；
- :func:`assert_target_ready` —— §4.3 第 5–8 步：schema、目标能力与 conf 加密态一致性、
  活跃作业、非空目标需显式确认；
- :func:`apply_state` —— §4.5 第 3–6 步：回滚点 → 三文件成组原子写 → 单事务替换 →
  进程内 env 重建 → 热生效。

S3：口令只在内存里出现，不进日志 / 审计 / 异常消息。包内凭据一律**明文**（§1.6-2），
由外层 AES-256-GCM 保护；导入时按目标机写侧重新加密（`ConfigRepo.encode_for_storage`）。

A1：本模块不拼 SQL，DB 读写一律经 `SiteRepo` / `JobRepo` / `ConfigRepo`。
A5：`Database.transaction()` 内只有 SQL（加密在事务外算好，见 `_prepare_config`）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.constants import DB_BUSY_TIMEOUT_MS_KEY, LOG_LEVEL_KEY
from app.db.repositories import task_repo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.site_repo import SiteRepo
from app.db.repositories.task_repo import TaskRepo
from app.errors import (
    InternalError,
    JobConflictError,
    PasswordWrongError,
    SitePackInvalidError,
    SitePassfileIncompleteError,
    SiteSchemaUnsupportedError,
    SiteTargetNotEmptyError,
    ValidationError,
)
from app.schemas.api_schemas import PackSummary, SiteTargetSummary
from app.schemas.enums import ConfigSource
from app.services import pack_crypto
from app.services.config_transfer import PACK_FORMAT as LEGACY_PACK_FORMAT
from app.services.log_control import apply_log_level
from app.services.rclone import conf_encrypted, resolve_conf_path
from app.services.rclone_crypto import (
    clear_password_command_env,
    resolve_pass_path,
    set_password_command_env,
)
from app.services.repo_status_cache import invalidate_repo_status_cache
from app.services.scheduler import TIMEZONE_KEY
from app.services.snapshot_catalog import invalidate_snapshot_catalog
from app.settings import Settings
from app.utils.passfile import PASSFILE_NAME, write_passfile
from app.utils.time import now_utc, utc_iso

logger = logging.getLogger(__name__)

__all__ = [
    "PACK_FORMAT",
    "PACK_VERSION",
    "SiteCounts",
    "apply_state",
    "assert_target_ready",
    "build_site_pack",
    "parse_site_pack_for_app",
    "collect_state",
    "describe_target",
    "find_unfinished_restores",
    "parse_site_pack",
    "rollback_root",
    "summarize_pack",
    "target_blockers",
]

# 包结构版本（独立于旧配置迁移包的 `config_transfer._SUPPORTED_PACK_VERSIONS`，方案 §2.4）
PACK_FORMAT = "restic-web-site-backup"
PACK_VERSION = 1
_SUPPORTED_VERSIONS = frozenset({1})

# 导出侧剔除的运行期列（§3.4）：导入是既定事实，带回来的 last_run_at 会谎报上次备份时间、
# dry_run_pending 会拦下首跑。id / tag / repo_id / created_at 一律保留（§1.6-3）。
_TASK_DROP = frozenset({"last_run_at", "dry_run_pending"})

# 载荷顶层键集（§2.3 十一键；§6.4-1 验收按此逐键比对）
_PAYLOAD_KEYS = (
    "schema_version",
    "app_version",
    "created_at",
    "source",
    "admin",
    "repos",
    "tasks",
    "config",
    "passfiles",
    "rclone",
    "warnings",
)

_ROLLBACK_DIRNAME = "site-restore"
_DEPLOY_ENV_KEYS = ("TZ", "DEPLOY_MODE", "RCLONE_CONFIG")
_STATE_FILENAME = "state.json"
_RCLONE_PASS_UNREADABLE = (
    "源机 rclone conf 已加密但配置口令不可读，导出的包在新机不可用；"
    "请先恢复 /repos/rclone.pass"
)
_CONF_MISSING_WARNING = "源机未配置 rclone.conf（可能未用 rclone 后端）"



@dataclass
class _Rollback:
    """一次导入的回滚点：目录 + 每个被覆盖文件的原副本（None = 原先不存在，回滚时删掉）。"""

    dir: Path
    entries: list[tuple[Path, Path | None]]


# ---------------------------------------------------------------- 小工具


def _canonical_header(header: Mapping[str, Any]) -> bytes:
    """头部规范化 JSON（AAD 原文）：键序无关、无空格，加解密两侧的唯一算法。"""
    return json.dumps(dict(header), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _validate_password(password: str) -> None:
    if len(password) < pack_crypto.PASSWORD_MIN_LENGTH:
        raise ValidationError(
            f"导出口令至少 {pack_crypto.PASSWORD_MIN_LENGTH} 位（它保护全部凭据）",
            data={"field": "password"},
        )


def _app_version() -> str:
    """应用版本（仅供诊断，§2.4 明确不参与校验）。"""
    try:
        return importlib.metadata.version("restic-web")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _file_evidence(path: Path) -> tuple[str, str, int]:
    """读一份文件的证据三元组：base64 内容 + sha256 + mtime_ns（§3.3-2）。"""
    data = path.read_bytes()
    return (
        base64.b64encode(data).decode("ascii"),
        hashlib.sha256(data).hexdigest(),
        path.stat().st_mtime_ns,
    )


def _has_crypt_section(path: Path) -> bool:
    """conf 里是否含 `type = crypt` 段（诊断列，不解析完整 ini）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(line.strip().replace(" ", "") == "type=crypt" for line in text.splitlines())


def _normalize_env_source(
    rows: Iterable[Any], warnings: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """config 行转载荷行并把 `source` 归一为 `ui`（§1.6-1）。

    `env` 标记属于源机的环境变量，搬到新机后 `env_sync` 会在启动时把同名键删掉；
    降级行（源机解不开）导出为空值并记 warning，不阻断导出。
    """
    config: list[dict[str, Any]] = []
    normalized: list[str] = []
    for row in rows:
        if row.degraded:
            warnings.append(
                f"config 键 {row.key} 在源机无法解密（{row.degraded_reason or '密钥源失效'}），已导出为空值"
            )
        source = str(row.source)
        if source != ConfigSource.UI:
            normalized.append(row.key)
            source = str(ConfigSource.UI)
        config.append({"key": row.key, "value": row.value, "source": source})
    return config, normalized


def _collect_rclone_forms(forms: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "remote": str(form["remote"]),
            "field": str(form["field"]),
            "obscured": int(form.get("obscured") or 0),
            "value_sha256": form.get("value_sha256"),
            "updated_at": form.get("updated_at"),
        }
        for form in forms
    ]


def _missing_passfiles(settings: Settings, repos: Iterable[Mapping[str, Any]]) -> list[str]:
    """哪些在用仓库缺密码文件（§3.3-3；整段 stat 由调用方 offload）。"""
    return [
        str(repo["id"])
        for repo in repos
        if not settings.passfile_path(str(repo["id"])).is_file()
    ]


def _read_passfiles(settings: Settings, repo_ids: Iterable[str]) -> list[dict[str, Any]]:
    """批量读在用仓库的密码文件（§3.3-2；C4：整段阻塞 IO 由调用方 offload）。"""
    out: list[dict[str, Any]] = []
    for repo_id in repo_ids:
        path = settings.passfile_path(repo_id)
        content_b64, digest, mtime_ns = _file_evidence(path)
        out.append(
            {"repo_id": repo_id, "content_b64": content_b64, "sha256": digest, "mtime_ns": mtime_ns}
        )
    return out


async def _collect_rclone(
    settings: Settings, *, include: bool, warnings: list[str]
) -> dict[str, Any]:
    """采集 rclone 文件面（§3.3-5/6、§1.6-5）：只搬运字节，不做加密态转换。

    conf 不存在不算错（可能没用 rclone 后端），按 §3.3-5 记 warning；但 conf **已加密**
    而配置口令读不到时必须拒绝导出——那种包在新机解不开，导入成功之后才会静默失效。
    C4：所有文件 IO 都在 worker 线程，异常原样经 await 传回，不改变 fail-closed 语义。
    """
    conf_path = Path(resolve_conf_path(settings))
    encrypted = await asyncio.to_thread(conf_encrypted, str(conf_path))
    conf_exists = await asyncio.to_thread(conf_path.is_file)
    rclone: dict[str, Any] = {
        "included": False,
        "conf_path_hint": str(conf_path),
        "conf_sha256": None,
        "conf_b64": None,
        "conf_encrypted": encrypted,
        "has_crypt_section": False,
        "forms": [],
        "pass": {"included": False, "content_b64": None, "sha256": None, "mtime_ns": None},
    }
    if include and conf_exists:
        try:
            content_b64, digest, _ = await asyncio.to_thread(_file_evidence, conf_path)
        except OSError as exc:
            raise ValidationError(f"rclone.conf 不可读（{conf_path}），请检查挂载与权限") from exc
        rclone.update(
            included=True,
            conf_sha256=digest,
            conf_b64=content_b64,
            has_crypt_section=await asyncio.to_thread(_has_crypt_section, conf_path),
        )
    elif include:
        warnings.append(_CONF_MISSING_WARNING)
    if encrypted:
        pass_path = resolve_pass_path(settings)
        if not await asyncio.to_thread(pass_path.is_file):
            raise SitePassfileIncompleteError(message=_RCLONE_PASS_UNREADABLE)
        try:
            content_b64, digest, mtime_ns = await asyncio.to_thread(_file_evidence, pass_path)
        except OSError as exc:
            raise SitePassfileIncompleteError(message=_RCLONE_PASS_UNREADABLE) from exc
        rclone["pass"] = {
            "included": True,
            "content_b64": content_b64,
            "sha256": digest,
            "mtime_ns": mtime_ns,
        }
    return rclone


# ---------------------------------------------------------------- 导出


async def collect_state(app: Any, *, include_rclone: bool = True) -> dict[str, Any]:
    """取导出用的全量状态（§3.3）：事务内读表，事务外读文件。

    Args:
        app: FastAPI 实例（用 `app.state.settings/db/config_repo`）。
        include_rclone: 是否把 `rclone.conf` 与凭据表单打进包（D1，默认含）。

    Returns:
        待加密的载荷字典（§2.3 十一键），`config` 里是**明文值**（§1.6-2）；
        `source.normalized_env_keys` 承载 §1.6-1 的归一化键清单（不新增顶层键）。

    Raises:
        SitePassfileIncompleteError: 任一在用仓库缺密码文件，或 conf 已加密但口令不可读。
        ValidationError: conf 存在但不可读。
    """
    settings: Settings = app.state.settings
    logger.info("全站备份导出：开始采集状态（include_rclone=%s）", include_rclone)

    # 1) DB 侧一致快照（五张表 + user_version，纯 SQL；事务由 SiteRepo 自己开）
    dump = await SiteRepo(app.state.db).dump_state()

    # 2) config 必须是明文：dump_state 给的是存储形态（敏感键仍是 enc:v2: 密文），
    #    跨机搬运的密文在目标机解不开——密钥源是对方的密码文件（§1.6-2）。
    warnings: list[str] = []
    config, normalized = _normalize_env_source(await app.state.config_repo.list_all(), warnings)
    if normalized:
        warnings.append(
            "以下配置键在源机由环境变量提供，包内已归一为界面值："
            + "、".join(normalized)
            + "（新机没有同名环境变量，保留 env 标记会在启动同步时被删除）"
        )

    # 3) 密码文件 fail-closed：缺一个就拒绝（能导不能读的包比不给包更糟，§1.6-4）
    repos = list(dump["repos"])
    active_repos = [repo for repo in repos if repo.get("deleted_at") is None]
    missing = await asyncio.to_thread(_missing_passfiles, settings, active_repos)
    if missing:
        raise SitePassfileIncompleteError(repo_ids=tuple(missing))
    passfiles = await asyncio.to_thread(
        _read_passfiles, settings, tuple(str(repo["id"]) for repo in active_repos)
    )

    # 4) 任务：剔除运行期列（id / tag / repo_id / created_at 保留，§1.6-3）
    tasks = [{key: value for key, value in row.items() if key not in _TASK_DROP} for row in dump["tasks"]]

    # 5) rclone 文件面（conf 不在包里也照搬 forms，见 §3.3-5）
    rclone = await _collect_rclone(settings, include=include_rclone, warnings=warnings)
    rclone["forms"] = _collect_rclone_forms(dump["forms"])

    state: dict[str, Any] = {
        "schema_version": dump["schema_version"],
        "app_version": await asyncio.to_thread(_app_version),
        "created_at": utc_iso(),
        "source": {
            "deploy_mode": settings.deploy_mode_effective,
            "deploy_env": {key: os.environ.get(key) for key in _DEPLOY_ENV_KEYS},
            # §1.6-1 / §4.2：被归一化的键随 source 子键走，不新增第 12 个顶层载荷键。
            "normalized_env_keys": normalized,
        },
        "admin": dict(dump["admin"]) if dump["admin"] else None,
        "repos": repos,
        "tasks": tasks,
        "config": config,
        "passfiles": passfiles,
        "rclone": rclone,
        "warnings": warnings,
    }
    logger.info(
        "全站备份导出：采集完成 repos=%s tasks=%s config=%s passfiles=%s rclone_included=%s",
        len(state["repos"]),
        len(state["tasks"]),
        len(state["config"]),
        len(state["passfiles"]),
        rclone["included"],
    )
    return state


def build_site_pack(state: Mapping[str, Any], password: str) -> dict[str, Any]:
    """把载荷封成完整备份包（§3.4）：外层五键 + AAD 绑头 + 载荷整体加密。

    `aad` 把明文头部（format / version / created_at）绑进 GCM 认证：改头即解不开。

    Args:
        state: :func:`collect_state` 的返回值。
        password: 用户口令（只在内存中出现，S3）。

    Returns:
        可直接 `json.dumps` 成下载文件的包字典。

    Raises:
        ValidationError: 口令不足 :data:`pack_crypto.PASSWORD_MIN_LENGTH` 位。

    W4 约定：本函数是同步函数，路由层由协程调用时不得在协程内直调 seal（PBKDF2 600k），
    须经 `asyncio.to_thread` offload，遵守 C4。
    """
    _validate_password(password)
    header = {"format": PACK_FORMAT, "version": PACK_VERSION, "created_at": utc_iso()}
    # 载荷的 created_at 与头部对齐：导入端的 acknowledge 对的是包外层 created_at。
    payload = {**state, "created_at": header["created_at"]}
    aad = _canonical_header(header)
    enc_head, blob = pack_crypto.seal(payload=payload, password=password, aad=aad)
    return {**header, "encryption": {**enc_head, "aad": aad.decode("utf-8")}, "payload": blob}


# ---------------------------------------------------------------- 导入：解析与校验


def _assert_payload(state: Any) -> None:
    """载荷结构校验（§4.3 第 4 步）：键集、类型、必备子结构，一处不对就 422。

    宁可拒绝也不猜：缺 `passfiles` 会让目标机带着旧口令跑，缺 `admin` 会把人锁在门外。
    """
    if not isinstance(state, Mapping):
        raise SitePackInvalidError("备份包载荷不是对象")
    missing = [key for key in _PAYLOAD_KEYS if key not in state]
    if missing:
        raise SitePackInvalidError("备份包缺少字段：" + "、".join(missing))
    unknown = [key for key in state if key not in _PAYLOAD_KEYS]
    if unknown:
        raise SitePackInvalidError("备份包含有未知字段：" + "、".join(unknown))
    if not isinstance(state["schema_version"], int):
        raise SitePackInvalidError("备份包 schema_version 不是整数")
    for key in ("repos", "tasks", "config", "passfiles", "warnings"):
        if not isinstance(state[key], list):
            raise SitePackInvalidError(f"备份包 {key} 不是数组")
    if not isinstance(state["rclone"], Mapping):
        raise SitePackInvalidError("备份包 rclone 不是对象")
    forms = state["rclone"].get("forms")
    if not isinstance(forms, list):
        raise SitePackInvalidError("备份包 rclone.forms 不是数组")
    for row in forms:
        if not isinstance(row, Mapping) or "remote" not in row or "field" not in row:
            raise SitePackInvalidError("备份包 rclone.forms 条目缺少 remote / field")
    if not isinstance(state["source"], Mapping):
        raise SitePackInvalidError("备份包 source 不是对象")
    # 旧包可不带该子键；带则必须全为字符串，否则会污染导入报告的回显字段。
    if "normalized_env_keys" in state["source"]:
        normalized = state["source"]["normalized_env_keys"]
        if not isinstance(normalized, list) or any(not isinstance(key, str) for key in normalized):
            raise SitePackInvalidError("备份包 source.normalized_env_keys 必须是字符串数组")
    if state["admin"] is not None and not isinstance(state["admin"], Mapping):
        raise SitePackInvalidError("备份包 admin 不是对象")
    for row in state["passfiles"]:
        if not isinstance(row, Mapping) or "repo_id" not in row or "content_b64" not in row:
            raise SitePackInvalidError("备份包 passfiles 条目缺少 repo_id / content_b64")
    for row in state["repos"]:
        if not isinstance(row, Mapping) or "id" not in row:
            raise SitePackInvalidError("备份包 repos 条目缺少 id")
    for row in state["tasks"]:
        if not isinstance(row, Mapping) or "id" not in row:
            raise SitePackInvalidError("备份包 tasks 条目缺少 id（任务 id 是快照 tag 的来源）")
    for row in state["config"]:
        if not isinstance(row, Mapping) or "key" not in row:
            raise SitePackInvalidError("备份包 config 条目缺少 key")


async def parse_site_pack(pack: Any, password: str, *, target_schema: int) -> dict[str, Any]:
    """校验并解密备份包（§4.3 第 1–5 步；**纯内存**，不碰 DB / 文件）。

    Args:
        pack: 请求体里的包字典（`json.loads` 之后）。
        password: 用户口令。
        target_schema: 目标机 `PRAGMA user_version`（由调用方在事务外读一次）。

    Returns:
        解密后的载荷字典。

    Raises:
        SitePackInvalidError: 结构 / 版本 / 载荷形态不符（422）。
        PasswordWrongError: AAD 对不上或 GCM 认证失败（401；错口令与篡改合并处理）。
        SiteSchemaUnsupportedError: 包 schema 高于本机（422）。
    """
    # 1) format 分流：旧配置迁移包单独给可操作提示（D6 不兼容）
    if not isinstance(pack, Mapping):
        raise SitePackInvalidError("不是有效的全站备份文件")
    pack_format = pack.get("format")
    if pack_format != PACK_FORMAT:
        if pack_format == LEGACY_PACK_FORMAT:
            raise SitePackInvalidError(
                "这是旧版配置迁移文件，已停止支持；请在原机用本功能重新导出全站备份包"
            )
        raise SitePackInvalidError("不是有效的全站备份文件")

    # 2) 包结构版本
    version = pack.get("version")
    if version not in _SUPPORTED_VERSIONS:
        raise SitePackInvalidError(f"包版本 {version} 不受支持，请升级应用 / 重新导出")

    # 3) AAD 重算 + GCM 解密：错口令与篡改落到同一句提示（不给爆破者判别器）
    header = {key: pack.get(key) for key in ("format", "version", "created_at")}
    encryption = pack.get("encryption")
    if not isinstance(encryption, Mapping):
        raise SitePackInvalidError("不是有效的全站备份文件")
    aad = _canonical_header(header)
    stored_aad = encryption.get("aad")
    if not isinstance(stored_aad, str) or stored_aad.encode("utf-8") != aad:
        raise PasswordWrongError("口令错误或文件已损坏")
    blob = pack.get("payload")
    if not isinstance(blob, str):
        raise SitePackInvalidError("不是有效的全站备份文件")
    try:
        opened = await asyncio.to_thread(
            pack_crypto.open_sealed, dict(encryption), blob, password, aad=aad
        )
    except PasswordWrongError:
        raise
    except ValidationError as exc:
        raise SitePackInvalidError(exc.message) from exc

    # 4) 载荷结构
    _assert_payload(opened)

    # 5) schema：本机比包旧时缺列，先拒绝比半途回滚省事
    pack_schema = int(opened["schema_version"])
    if pack_schema > int(target_schema):
        raise SiteSchemaUnsupportedError(pack_schema=pack_schema, target_schema=int(target_schema))
    if pack_schema < int(target_schema):
        # §2.4：旧 schema 包的任务行可能带目标机没有的列，SiteRepo._task_values 会静默丢弃；
        # 把丢弃结果记进载荷 warnings，导入报告原样转出（不新增顶层键）。
        dropped = {
            key
            for row in opened["tasks"]
            for key in row
            if key not in task_repo._COLUMNS
        }
        opened["warnings"].append(
            f"备份包 schema_version {pack_schema} 低于本机 {int(target_schema)}："
            f"多余列丢弃 {sorted(dropped)}，缺失列取默认（见 task_repo._SEED_DEFAULTS）"
        )
    return dict(opened)


def _dir_writable(path: Path, *, create: bool) -> bool:
    """试写判定目录可写：`os.access` 对容器里的 root 恒为真，只有真建文件才作数。

    `create=False` 时目录必须已存在——`rclone.conf` 的父目录属于宿主机挂载点，
    inspect 不该替人把它建出来（挂错路径要能被看见，而不是被悄悄补上）。
    """
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            return False
        with tempfile.NamedTemporaryFile(dir=path, prefix=".site-probe-", delete=True):
            return True
    except OSError:
        return False


async def describe_target(app: Any) -> SiteTargetSummary:
    """探测目标机现状（§4.3 第 6 步的只读部分；**零业务写入**，供 inspect 预览）。"""
    settings: Settings = app.state.settings
    db = app.state.db
    counts = await SiteRepo(db).counts()
    active = await JobRepo(db).list_active()
    tasks = TaskRepo(db)
    active_jobs: list[str] = []
    for job in active:
        # §1.5：inspect 报告要「任务名 + 类型 + 开始时间」；手动 / legacy 作业无 task_id，
        # 任务名回退作业 id 短前缀，避免展示 undefined。started_at 缺省回落 created_at。
        task = await tasks.get(job.task_id) if job.task_id is not None else None
        name = task.name if task is not None else f"作业 {job.id[:8]}"
        started = job.started_at or job.created_at
        active_jobs.append(f"{name}（{job.type}，始于 {started}）")
    conf_path = Path(resolve_conf_path(settings))
    return SiteTargetSummary(
        repos=counts["repos"],
        repos_configured=counts["repos_configured"],
        tasks=counts["tasks"],
        config_keys=counts["config"],
        jobs=counts["jobs"],
        active_jobs=active_jobs,
        restic_available=(await asyncio.to_thread(shutil.which, settings.restic_binary)) is not None,
        passfile_writable=await asyncio.to_thread(
            _dir_writable, Path(settings.passfile_root), create=True
        ),
        rclone_conf_writable=await asyncio.to_thread(_dir_writable, conf_path.parent, create=False),
        rclone_conf_encrypted=await asyncio.to_thread(conf_encrypted, str(conf_path)),
        rollback_keep_count=settings.site_restore_keep_count,
    )


def target_blockers(summary: SiteTargetSummary, rclone: Mapping[str, Any]) -> list[str]:
    """把目标摘要与包内 rclone 段比成阻碍项（纯函数）：inspect 展示、import 拒绝共用同一判定。"""
    blockers: list[str] = []
    if not summary.passfile_writable:
        blockers.append("仓库密码文件目录不可写（挂载只读或无权限）")
    if not summary.restic_available:
        blockers.append("restic 二进制不可执行，导入后无法执行任何备份")
    if rclone.get("included"):
        if not summary.rclone_conf_writable:
            blockers.append("rclone.conf 所在目录不存在或不可写")
        pack_encrypted = bool(rclone.get("conf_encrypted"))
        if pack_encrypted != summary.rclone_conf_encrypted:
            blockers.append(
                f"包内 rclone conf 为{'加密' if pack_encrypted else '明文'}态，"
                f"目标机为{'加密' if summary.rclone_conf_encrypted else '明文'}态，"
                "导入会使全部网盘远端不可用；请先在目标机调整 conf 加密态或更换包"
            )
    return blockers


async def assert_target_ready(
    app: Any,
    state: Mapping[str, Any],
    *,
    acknowledge: str | None = None,
    expected_created_at: str | None = None,
) -> SiteTargetSummary:
    """§4.3 第 5–8 步：schema 兼容、目标能力、无活跃作业、非空目标需显式确认。

    Args:
        app: FastAPI 实例。
        state: 已解密的载荷。
        acknowledge: 二次确认串，必须逐字等于**包外层 `created_at`**（§4.2）；空库时可不传。
        expected_created_at: 包外层 `created_at`（由路由层从 `payload.pack` 传入）。
            载荷内 `created_at` 由封包器对齐，但它是加密内容、可被重封，故**不作**
            确认基准；未传时回落载荷内值，仅供不持包对象的直接调用（如单测）。

    Returns:
        目标机摘要（调用方填报告或日志）。

    Raises:
        SiteSchemaUnsupportedError: 包 schema 高于本机。
        ValidationError: 目标能力不足或 conf 加密态不一致（`data.blockers` 与 inspect 同源）。
        JobConflictError: 有活跃作业（D3）。
        SiteTargetNotEmptyError: 目标已有数据且未确认整机替换。
    """
    db = app.state.db
    target_schema = await db.user_version()
    pack_schema = int(state["schema_version"])
    if pack_schema > target_schema:
        raise SiteSchemaUnsupportedError(pack_schema=pack_schema, target_schema=target_schema)

    summary = await describe_target(app)
    blockers = target_blockers(summary, state.get("rclone") or {})
    if blockers:
        raise ValidationError("目标机不满足导入前置条件：" + "；".join(blockers), data={"blockers": blockers})

    if summary.active_jobs:
        raise JobConflictError(
            "有任务正在运行或等待中：" + "、".join(summary.active_jobs[:10]) + "，请等它结束或先取消"
        )

    occupied = _occupied(summary)
    confirm = expected_created_at if expected_created_at is not None else str(state.get("created_at") or "")
    if occupied and acknowledge != confirm:
        raise SiteTargetNotEmptyError(
            blockers=tuple(occupied), target=summary.model_dump()
        )
    return summary


def _occupied(summary: SiteTargetSummary) -> list[str]:
    """目标「非空」的判定口径：`repos.default` 由迁移预置，不能算数据（§4.4）。"""
    occupied: list[str] = []
    if summary.repos_configured:
        occupied.append(f"{summary.repos_configured} 个已配置仓库")
    if summary.tasks:
        occupied.append(f"{summary.tasks} 个备份任务")
    if summary.config_keys:
        occupied.append(f"{summary.config_keys} 项配置")
    return occupied


# ---------------------------------------------------------------- 导入：文件面


def rollback_root(settings: Settings) -> Path:
    """回滚点根目录（`<data_dir>/site-restore`）的**唯一路径派生点**（S1）。

    服务层写盘、裁剪、启动期遗留检测，以及 `main.py` 的告警文案都走这里——
    字面量只出现在本函数，别处再拼一份就是第二真源。
    """
    return Path(settings.data_dir) / _ROLLBACK_DIRNAME


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """同目录 mkstemp → 写 → chmod → `os.replace`：跨文件系统不会出现半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))


async def _snapshot_files(settings: Settings, state: Mapping[str, Any]) -> _Rollback:
    """第 3 步：把即将被覆盖的文件抄进 `<data>/site-restore/<ts>/`（D4）。

    只抄「这次真要改的那几个」——旧仓库的密码文件不会被删除，抄了也用不上。
    """
    # 时间戳只到微秒，连续导入可能撞名：加随机后缀，排序仍按时间戳前缀
    stamp = now_utc().strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:6]
    root = rollback_root(settings) / stamp
    entries: list[tuple[Path, Path | None]] = []
    rclone = state.get("rclone") or {}

    def _copy_overwritten_files() -> None:
        root.mkdir(parents=True, exist_ok=True)
        targets: list[tuple[str, Path]] = [
            (f"passfiles/{row['repo_id']}/{PASSFILE_NAME}", settings.passfile_path(str(row["repo_id"])))
            for row in state.get("passfiles") or ()
        ]
        if rclone.get("included"):
            targets.append(("rclone.conf", Path(resolve_conf_path(settings))))
        if (rclone.get("pass") or {}).get("included"):
            targets.append(("rclone.pass", resolve_pass_path(settings)))
        for rel, dest in targets:
            backup: Path | None = None
            if dest.is_file():
                backup = root / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup)
            entries.append((dest, backup))
        _write_json(
            root / _STATE_FILENAME,
            {"phase": "prepared", "started_at": utc_iso(), "files": [str(dest) for dest, _ in entries]},
        )

    await asyncio.to_thread(_copy_overwritten_files)
    return _Rollback(dir=root, entries=entries)


async def _restore_files(rollback: _Rollback) -> None:
    """从回滚点还原：原先有就写回，原先没有就删掉——恢复成「导入从未发生」。"""

    def _restore_overwritten_files() -> None:
        for dest, backup in rollback.entries:
            if backup is None:
                dest.unlink(missing_ok=True)
                continue
            _atomic_write(dest, backup.read_bytes())

    try:
        await asyncio.to_thread(_restore_overwritten_files)
    except OSError:
        logger.exception("全站导入：从回滚点 %s 还原文件失败，请人工介入", rollback.dir)


def _write_passfiles(settings: Settings, rows: Iterable[Mapping[str, Any]]) -> None:
    """第 4 步之一：按目标机自己的 `passfile_path` 原子写回（§1.6-6）。"""
    for row in rows:
        secret = base64.b64decode(row["content_b64"]).decode("utf-8")
        write_passfile(settings.passfile_path(str(row["repo_id"])), secret)


def _write_rclone_conf(settings: Settings, rclone: Mapping[str, Any]) -> None:
    """第 4 步之二：conf 按 `conf_b64` **原样字节**写回，不做加密态转换（§4.5）。"""
    if not rclone.get("included"):
        return
    _atomic_write(Path(resolve_conf_path(settings)), base64.b64decode(rclone["conf_b64"]))


def _write_rclone_pass(settings: Settings, rclone: Mapping[str, Any]) -> None:
    """第 4 步之三：`rclone.pass` 原样字节 + 0600 + 无尾随换行（与 `_write_pass` 对齐）。"""
    pass_section = rclone.get("pass") or {}
    if not pass_section.get("included"):
        return
    _atomic_write(resolve_pass_path(settings), base64.b64decode(pass_section["content_b64"]))


async def _mark_committed(rollback: _Rollback) -> None:
    """第 6 步：把 `state.json` 改成 committed——崩溃窗口到此闭合（§4.5）。"""
    state_path = rollback.dir / _STATE_FILENAME
    if not await asyncio.to_thread(state_path.is_file):
        return
    try:
        current = json.loads(await asyncio.to_thread(state_path.read_text, encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("全站导入：回滚点状态文件不可读，跳过提交标记 %s", state_path)
        return
    await asyncio.to_thread(
        _write_json, state_path, {**current, "phase": "committed", "committed_at": utc_iso()}
    )


# ---------------------------------------------------------------- W4 路由层复用件


@dataclass(frozen=True)
class SiteCounts:
    """一次导出 / 导入的**各段计数**（S6 审计 detail 与报告 `applied` 的唯一真源）。

    W4 审查前这几组数字在 export 审计、import 审计、`SiteImportReport.applied` 三处
    各自手拼；任何一个字段漏拼都会让「审计说了什么」与「前端看到什么」悄悄分叉。
    审计 detail 一律走 :meth:`audit_detail`——只含计数与模式，绝不含凭据 / 包原文。
    """

    repos: int = 0
    tasks: int = 0
    config_keys: int = 0
    passfiles: int = 0
    rclone_conf_included: bool = False
    rclone_pass_included: bool = False

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> SiteCounts:
        """从解密载荷（或 `collect_state` 的返回值）按段计数。"""
        rclone = state.get("rclone") or {}
        return cls(
            repos=len(state.get("repos") or ()),
            tasks=len(state.get("tasks") or ()),
            config_keys=len(state.get("config") or ()),
            passfiles=len(state.get("passfiles") or ()),
            rclone_conf_included=bool(rclone.get("included")),
            rclone_pass_included=bool((rclone.get("pass") or {}).get("included")),
        )

    def audit_detail(self, *, mode: str | None = None, rollback_dir: str | None = None) -> dict[str, Any]:
        """`site.export` / `site.import` 的审计 detail（S3：只记计数、模式与回滚点目录名）。"""
        detail: dict[str, Any] = {
            "repos": self.repos,
            "tasks": self.tasks,
            "config_keys": self.config_keys,
            "passfiles": self.passfiles,
            "rclone_conf": self.rclone_conf_included,
            "rclone_pass": self.rclone_pass_included,
        }
        if mode is not None:
            detail["mode"] = mode
        if rollback_dir is not None:
            detail["rollback_dir"] = rollback_dir
        return detail


async def parse_site_pack_for_app(app: Any, pack: Any, password: str) -> dict[str, Any]:
    """路由层入口：读一次目标机 `user_version` → 解密校验包（§4.3 第 1–5 步）。

    A1：`user_version()` 是 `Database` 的方法而非拼 SQL，但仍属 DB 访问——收在**服务层**，
    路由层不再持有 `db` 句柄；`inspect` 与 `import` 共用这一处，消除两行重复取数。
    """
    target_schema = await app.state.db.user_version()
    return await parse_site_pack(pack, password, target_schema=target_schema)


def summarize_pack(pack: Mapping[str, Any], state: Mapping[str, Any]) -> PackSummary:
    """把包外层头 + 解密载荷汇总成 `PackSummary` 字段字典（只计数与布尔，不含凭据）。

    各段计数复用 :class:`SiteCounts`（与 export / import 审计 detail 同源）；
    字段集的真源是 `schemas.api_schemas.PackSummary`，由单测锁死，新增字段漏改会红。
    """
    counts = SiteCounts.from_state(state)
    rclone = state.get("rclone") or {}
    return PackSummary(
        format=str(pack.get("format") or ""),
        version=int(pack.get("version") or 0),
        created_at=str(pack.get("created_at") or ""),
        schema_version=int(state.get("schema_version") or 0),
        app_version=str(state.get("app_version") or "unknown"),
        deploy_mode=(state.get("source") or {}).get("deploy_mode"),
        repos=counts.repos,
        tasks=counts.tasks,
        config_keys=counts.config_keys,
        passfiles=counts.passfiles,
        rclone_conf_included=counts.rclone_conf_included,
        rclone_conf_encrypted=bool(rclone.get("conf_encrypted")),
        rclone_forms=len(rclone.get("forms") or ()),
        rclone_pass_included=counts.rclone_pass_included,
        # 包内 warnings 是源机导出的现场记录（conf 缺失 / env 归一 / schema 降级），
        # 预览时就该让用户看见，而不是等到导入报告里才第一次出现。
        warnings=[str(item) for item in state.get("warnings") or ()],
    )


async def find_unfinished_restores(settings: Settings) -> list[str]:
    """列出崩溃窗口遗留的回滚点目录（`state.json` 仍为 `prepared` 的那些）。

    只有「文件已写、DB 尚未提交」这一小段崩溃窗口会留下这种目录（方案 §4.5）；
    命中即说明盘上是**混合态**（新密码文件 + 旧 DB），启动期必须点名告警。
    不自动修复——继续导入还是回滚文件由人工决定，自动动手可能二次破坏。

    Returns:
        未完成回滚点的目录名列表（升序）；全无遗留时为空列表。
    """
    root = rollback_root(settings)

    def _scan() -> list[str]:
        if not root.is_dir():
            return []
        pending: list[str] = []
        for entry in sorted(root.iterdir()):
            state_path = entry / _STATE_FILENAME
            if not entry.is_dir() or not state_path.is_file():
                continue
            try:
                phase = json.loads(state_path.read_text(encoding="utf-8")).get("phase")
            except (OSError, ValueError):
                # 状态文件坏了同样属于「不知道提交没提交」，按未完成处理（保守）
                pending.append(entry.name)
                continue
            if phase != "committed":
                pending.append(entry.name)
        return pending

    return await asyncio.to_thread(_scan)


async def _prune_rollback_dirs(root: Path, keep: int) -> None:
    """D4：只保留最近 `keep` 份回滚点，更早的整目录删掉（`keep <= 0` = 不保留）。"""
    if not await asyncio.to_thread(root.is_dir):
        return
    entries = await asyncio.to_thread(lambda: list(root.iterdir()))
    dirs = await asyncio.to_thread(lambda: sorted(entry for entry in entries if entry.is_dir()))
    stale = dirs if keep <= 0 else dirs[:-keep] if len(dirs) > keep else []
    for entry in stale:
        try:
            await asyncio.to_thread(shutil.rmtree, entry)
        except OSError:
            logger.warning("全站导入：清理旧回滚点失败 %s（不影响本次导入）", entry)


async def _reload_rclone_password_env(settings: Settings, rclone: Mapping[str, Any]) -> None:
    """C3：进程内重建 `RCLONE_PASSWORD_COMMAND`（§4.6，v1.5 必做项）。

    子进程每次都重读 `rclone.conf`，但口令**命令**是我们注入的 env——包内不带口令时
    必须主动清掉，否则新机沿用旧脚本，指向一个已经不存在的口令文件。
    """
    if (rclone.get("pass") or {}).get("included"):
        await asyncio.to_thread(set_password_command_env, resolve_pass_path(settings))
    else:
        await asyncio.to_thread(clear_password_command_env)


async def _hot_reload(app: Any) -> None:
    """第 6 步热生效（§4.6 九行表）：逐个钩子独立 try，一个失败不拖垮其余。"""
    config_repo = app.state.config_repo
    try:
        await app.state.scheduler.reload()
    except Exception:
        logger.exception("全站导入：调度器重载失败")
    try:
        apply_log_level(await config_repo.get_value(LOG_LEVEL_KEY))
    except Exception:
        logger.exception("全站导入：日志级别热生效失败")
    try:
        raw_busy = await config_repo.get_value(DB_BUSY_TIMEOUT_MS_KEY)
        if raw_busy and str(raw_busy).isdigit():
            await app.state.db.apply_busy_timeout(int(raw_busy))
    except Exception:
        logger.exception("全站导入：busy_timeout 热生效失败")
    try:
        app.state.restic.set_timezone(await config_repo.get_value(TIMEZONE_KEY))
    except Exception:
        logger.exception("全站导入：restic 时区热生效失败")
    try:
        invalidate_repo_status_cache()
        invalidate_snapshot_catalog()
    except Exception:
        logger.exception("全站导入：缓存失效失败")


# ---------------------------------------------------------------- 导入：落地


def _prepare_config_rows(config_repo: Any, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """事务外预加密整批 config（C4：encode_for_storage 读密码文件，不阻塞事件循环）。"""
    return [
        {
            "key": str(row["key"]),
            "value": config_repo.encode_for_storage(str(row["key"]), row.get("value")),
            "source": str(ConfigSource.UI),
        }
        for row in rows
        if str(row.get("key") or "")
    ]


async def apply_state(app: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    """把载荷落到目标机（§4.5 第 3–6 步）：回滚点 → 三文件成组写 → 单事务替换 → 热生效。

    顺序是**先文件、后 DB**：文件内容单一、体积极小，失败回滚代价最低；DB 有事务天然原子。
    加密必须在事务外做（`ConfigRepo._maybe_encrypt` 读密码文件 = 文件 IO，A5），
    且必须排在新密码文件**写完之后**——写侧密钥源就是那个文件。

    Args:
        app: FastAPI 实例。
        state: :func:`parse_site_pack` 的返回值。

    Returns:
        导入报告字典（W4 映射成 `SiteImportReport`）。

    Raises:
        InternalError: 文件写入 OSError 已转换为 `data.rolled_back=True` 并从回滚点恢复；
            DB 事务异常按 §6.2 owner 裁决原样 raise（文件亦从回滚点恢复）。
    """
    settings: Settings = app.state.settings
    db = app.state.db
    rclone = dict(state.get("rclone") or {})
    rollback = await _snapshot_files(settings, state)

    try:
        await asyncio.to_thread(_write_passfiles, settings, state.get("passfiles") or ())
        await asyncio.to_thread(_write_rclone_conf, settings, rclone)
        await asyncio.to_thread(_write_rclone_pass, settings, rclone)
    except OSError as exc:
        await _restore_files(rollback)
        logger.exception("全站导入：文件写入失败，已从回滚点恢复 %s", rollback.dir)
        raise InternalError("导入失败，已回滚到导入前状态", data={"rolled_back": True}) from exc

    config_repo = app.state.config_repo
    prepared = {
        **state,
        "config": await asyncio.to_thread(
            _prepare_config_rows, config_repo, state.get("config") or ()
        ),
    }
    try:
        async with db.transaction() as tx:
            applied = await SiteRepo(db).replace_all(tx, prepared)
    except BaseException:
        await _restore_files(rollback)
        logger.exception("全站导入：事务失败，DB 已回滚并从回滚点恢复文件 %s", rollback.dir)
        raise

    # §4.5 第 6 步：DB 一提交就立刻闭合崩溃窗口，再放热生效钩子。
    # 若把 env 重建排在前面，它抛 OSError 时 DB 已提交却仍留 phase=prepared，
    # 启动期会误报「新密码文件 + 旧 DB」混合态，诱导人工把已提交的文件回滚掉。
    await _mark_committed(rollback)
    await _prune_rollback_dirs(rollback_root(settings), settings.site_restore_keep_count)
    await _reload_rclone_password_env(settings, rclone)
    await _hot_reload(app)

    counts = SiteCounts.from_state(state)
    report = {
        "applied": {
            **applied,
            "passfiles": counts.passfiles,
            "rclone_conf": int(counts.rclone_conf_included),
            "rclone_pass": int(counts.rclone_pass_included),
        },
        # §1.6-1：导出侧已把 config[].source 全归一为 ui，归一清单改从 source 子键取。
        "normalized_env_keys": list((state.get("source") or {}).get("normalized_env_keys") or ()),
        # §2.4：schema 降级的「多余列丢弃」warning 已由 parse_site_pack 写进载荷 warnings。
        "warnings": list(state.get("warnings") or ()),
        "rollback_dir": rollback.dir.name,
        "logged_out": True,
    }
    logger.info("全站导入完成：%s（回滚点 %s）", report["applied"], report["rollback_dir"])
    return report