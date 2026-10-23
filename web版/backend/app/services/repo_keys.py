"""仓库密钥中性化（init 后抹去 ``keys/`` 文件的明文身份元数据）。

restic 的 ``keys/<id>`` 是**明文 JSON**（唯一不加密的仓库文件），含：

- ``username`` / ``hostname`` / ``created`` —— 明文身份标注；
- ``kdf`` / ``N`` / ``r`` / ``p`` / ``salt`` —— 明文 KDF 参数（口令派生链路的硬约束，
  **无法加密**：没有它们就无法从口令启动派生）；
- ``data`` —— 密文（master key）。

云端或存储侧只能从该文件推断「哪台机器 / 哪个账号」。本模块在 init 成功后追加
「``key add`` 中性标识 → ``key remove`` 旧 key」两步，把唯一密钥的身份标注换成中性值。

实现纪律：

- 全部命令经 :meth:`ResticCmd.build` 白名单拼装（S2），不经 shell；
- 新 key 的 ``--new-password-file`` 指向**现有密码文件** → 与旧 key 同口令，中性化对
  用户透明（原密码照常开库）；
- ``remove`` 必须带 ``--key-hint <新 key>``：restic ``deleteKey`` 拒绝删「当前 key」，
  而新旧 key 同口令时 restic 先解哪个不确定，需强制当前 key 指向新 key；
- 任何一步失败只记日志并返回 ``False``，**不抛异常**——中性化是加固步骤，不能让已成功
  的 init 反转成失败。失败时仓库仍至少有可用 key（旧 key 未删）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.constants import KEY_OPERATION_TIMEOUT_SECONDS
from app.services.restic import ProbeResult, ResticCmd, WhitelistArgs

logger = logging.getLogger(__name__)

# 中性身份标注：不暴露真实机器名 / 账号（写入 key 文件的 username / hostname）
NEUTRAL_KEY_USER = "svc-backup"
NEUTRAL_KEY_HOST = "repo"

# 展示用短 id 长度：与 ``restic key list`` 表格列一致（8 位是 restic 的缩略口径）
KEY_ID_SHORT_LEN = 8

# 仓库密钥身份的整体判定（只读密钥可见性端点用；也是 UI 四态的唯一依据）
KEY_OVERALL_NEUTRALIZED = "neutralized"  # 全部 key 均为中性身份
KEY_OVERALL_EXPOSING = "exposing"  # 存在非中性 key（暴露真实机器/账号）
KEY_OVERALL_MIXED = "mixed"  # 中性 + 非中性并存（旧 key 未删 / 其它工具接入）
KEY_OVERALL_UNKNOWN = "unknown"  # key list 失败或读不到任何 key


def is_neutral_key(item: dict[str, object]) -> bool:
    """判断一条 ``key list --json`` 项的身份标注是否为中性值。"""
    return (
        str(item.get("userName") or "") == NEUTRAL_KEY_USER
        and str(item.get("hostName") or "") == NEUTRAL_KEY_HOST
    )


def classify_keys(
    keys: list[dict[str, object]], *, created_tz: str = "UTC"
) -> tuple[str, list[dict[str, object]]]:
    """把 ``key list --json`` 原始项归一化为展示模型，并给出整体状态。

    只取展示必需的字段（``id`` / ``userName`` / ``hostName`` / ``created`` /
    ``current``）——``data``（master key 密文）与 KDF 参数一律不进入返回值，
    避免把不必要的仓库指纹经 API 外泄（见《云端仓库安全基线》§5.3）。

    ``created`` 是 restic 按子进程 ``TZ`` 渲染的**无时区字符串**（本项目
    ``build_env`` 注入 ``settings.tz``，默认 UTC）。原样透出会与用户本地时间差出
    时区偏移且无从判断，故把该 TZ 一并带上（``created_tz``），由前端原样展示——
    不做时间换算，避免在展示层引入第二套时间语义（红线 C8）。

    Args:
        keys: :func:`parse_key_list` 的产物（非法项已被过滤）。
        created_tz: restic 子进程时区（即 ``settings.tz``）；仅作标注，不参与换算。

    Returns:
        ``(overall, items)``。``items`` 按输入顺序；缺 ``id`` 的项被丢弃（无法定位也
        无法展示）。``overall`` 取 :data:`KEY_OVERALL_*` 之一；``items`` 为空时恒为
        ``unknown``——调用方负责补 ``probe_error`` 说明原因。
    """
    items: list[dict[str, object]] = []
    for item in keys:
        key_id = str(item.get("id") or "")
        if not key_id:
            continue
        items.append(
            {
                "id": key_id[:KEY_ID_SHORT_LEN],
                "id_full": key_id,
                "user": str(item.get("userName") or ""),
                "host": str(item.get("hostName") or ""),
                "created": str(item.get("created") or ""),
                "created_tz": created_tz,
                "current": bool(item.get("current")),
                "neutral": is_neutral_key(item),
            }
        )
    if not items:
        return KEY_OVERALL_UNKNOWN, items
    neutral_count = sum(1 for item in items if item["neutral"])
    if neutral_count == len(items):
        return KEY_OVERALL_NEUTRALIZED, items
    if neutral_count == 0:
        return KEY_OVERALL_EXPOSING, items
    return KEY_OVERALL_MIXED, items


def parse_key_list(lines: list[str]) -> list[dict[str, object]]:
    """解析 ``restic key list --json`` 输出（单行 JSON 数组）。

    restic 0.19.1 ``cmd_key.go`` 的 keyInfo 字段：
    ``current`` / ``id`` / ``userName`` / ``hostName`` / ``created``。旧版 ``id`` 可能只有
    8 位短前缀，``key remove`` 接受唯一前缀，故不校验长度。

    非法/空输出返回空列表（由调用方按「无法定位」处理，绝不抛异常）。
    """
    text = "\n".join(lines).strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


async def neutralize_repo_key(
    restic: ResticCmd,
    *,
    repo_id: str,
    env: dict[str, str],
    password_file: str | Path,
) -> bool:
    """把仓库**唯一** key 的明文身份元数据中性化（幂等）。

    Args:
        restic: 命令器（build 白名单 + 受控执行）。
        repo_id: 仓库标识（仅用于日志；``env`` 已含 ``RESTIC_REPOSITORY``）。
        env: 子进程环境（:meth:`ResticCmd.build_env` 产出，含密码文件与凭据）。
        password_file: 现有密码文件路径 —— ``key add --new-password-file`` 复用它，
            保证新 key 与旧 key 同口令。

    Returns:
        ``True`` = 实际完成「加中性 key + 删旧 key」；``False`` = 跳过（已中性化 /
        多 key / 失败，均已记日志）。调用方无需分支处理，失败不阻塞 init 成功。
    """
    password_file = str(password_file)

    # 1) 读取现有 key：仅处理「唯一 key」的 init 场景，多 key 仓库不擅自删。
    keys = await _list_keys(restic, env=env)
    if len(keys) != 1:
        logger.info("仓库 %s 有 %d 个 key，跳过中性化（仅处理唯一 key）", repo_id, len(keys))
        return False

    old = keys[0]
    old_id = str(old.get("id") or "")
    old_user = str(old.get("userName") or "")
    old_host = str(old.get("hostName") or "")
    if old_user == NEUTRAL_KEY_USER and old_host == NEUTRAL_KEY_HOST:
        logger.info("仓库 %s 的密钥身份已是中性值，跳过", repo_id)
        return False
    if not old_id:
        logger.warning("仓库 %s 的 key 列表缺少 id，跳过中性化", repo_id)
        return False

    # 2) 添加中性 key（同口令：复用现有密码文件）
    add_argv = restic.build(
        "key",
        args=WhitelistArgs(
            json_output=False,
            key_target="add",
            key_user=NEUTRAL_KEY_USER,
            key_host=NEUTRAL_KEY_HOST,
            new_password_file=password_file,
        ),
    )
    added = await restic.run_json(add_argv, env=env, timeout_seconds=KEY_OPERATION_TIMEOUT_SECONDS)
    if added.exit_code != 0:
        logger.warning(
            "仓库 %s 添加中性 key 失败（exit=%s）：%s",
            repo_id,
            added.exit_code,
            _tail(added),
        )
        return False

    # 3) 定位新 key（按中性 userName/hostName 匹配）
    fresh = await _list_keys(restic, env=env)
    new_key = next(
        (
            item
            for item in fresh
            if str(item.get("userName") or "") == NEUTRAL_KEY_USER
            and str(item.get("hostName") or "") == NEUTRAL_KEY_HOST
            and str(item.get("id") or "")
        ),
        None,
    )
    if new_key is None:
        logger.warning(
            "仓库 %s 已添加中性 key，但在 key list 中未定位到（旧 key 保留，功能不受影响）",
            repo_id,
        )
        return False
    new_id = str(new_key["id"])

    # 4) 删旧 key：--key-hint 强制以新 key 为「当前 key」，否则 restic 拒绝删当前 key。
    remove_argv = restic.build(
        "key",
        args=WhitelistArgs(
            json_output=False,
            key_target="remove",
            key_id=old_id,
            key_hint=new_id,
        ),
    )
    removed = await restic.run_json(
        remove_argv, env=env, timeout_seconds=KEY_OPERATION_TIMEOUT_SECONDS
    )
    if removed.exit_code != 0:
        logger.warning(
            "仓库 %s 删除旧 key 失败（exit=%s，旧 key 保留、中性 key 已生效）：%s",
            repo_id,
            removed.exit_code,
            _tail(removed),
        )
        return False

    logger.info(
        "仓库 %s 的密钥身份已中性化（user=%s host=%s）", repo_id, NEUTRAL_KEY_USER, NEUTRAL_KEY_HOST
    )
    return True


async def _list_keys(restic: ResticCmd, *, env: dict[str, str]) -> list[dict[str, object]]:
    """执行 ``restic key list --json`` 并解析；失败返回空列表（原因记日志）。"""
    # json_output 显式 True：``--json`` 是解析前提（缺省会退化成人类可读表格，
    # parse_key_list 只能静默返回空表——中性化会被误判为「仓库没有 key」而跳过）。
    argv = restic.build("key", args=WhitelistArgs(json_output=True, key_target="list"))
    result = await restic.run_json(argv, env=env, timeout_seconds=KEY_OPERATION_TIMEOUT_SECONDS)
    if result.exit_code != 0:
        logger.warning("restic key list 失败（exit=%s）：%s", result.exit_code, _tail(result))
        return []
    return parse_key_list(result.lines)


def _tail(result: ProbeResult, count: int = 3) -> str:
    """取输出末尾若干行用于日志（不落凭据，S3：restic 输出不含口令）。"""
    return "\n".join(result.lines[-count:])
