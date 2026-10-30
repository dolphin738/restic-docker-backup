"""仓库密码文件的删除与孤儿回收（E5 per-repo 布局的**唯一删除出口**）。

存在理由：密码文件的删除原先散在两处（`routers/repos.py` 的 purge_disk、
`services/retention.py` 的归档到期），两处都直接 `Path.unlink()` 且都不认识
``RESTIC_PASSWORD_FILE`` 全局覆盖——一旦运维设了该 env，`passfile_path()` 返回的
是**所有仓库共用**的那一个文件，删它等于一次性毁掉全部仓库的可读性。此处收口：

- :func:`remove_passfile`：删 ``<passfile_root>/<repo_id>/password`` + 空目录回收；
- :func:`reap_orphan_passfiles`：回收「无主且超龄」的密码文件，兜住三类残留——
  ``?hard=1`` 删除、归档页「释放 ID」、以及上一轮清理 unlink 失败留下的孤儿。

纪律（红线落点）：
- S1：只触碰 ``settings.passfile_root``（应用管控目录），不递归子目录、只认
  `password` 这一个文件名，父目录仅在其为**空**时 `rmdir`（用户手放的文件不误删）；
- S3：日志/返回值只含 repo_id，绝不打印口令内容；
- 幂等：文件不存在即静默成功（返回 False），重跑零副作用。
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from app.constants import REPO_PASSWORD_LENGTH

if TYPE_CHECKING:
    from app.settings import Settings

logger = logging.getLogger(__name__)

# 密码文件固定文件名（E5 per-repo 布局：<passfile_root>/<repo_id>/password）。
PASSFILE_NAME = "password"

# 生成用字符集（四类；剔除易混淆字符 0/O、1/l/I，与前端 passwordStrength 同口径）
_PASSWORD_CLASSES: tuple[str, ...] = (
    "abcdefghjkmnpqrstuvwxyz",
    "ABCDEFGHJKMNPQRSTUVWXYZ",
    "23456789",
    "!@#$%^&*-_=+",
)


def generate_repo_password(length: int = REPO_PASSWORD_LENGTH) -> str:
    """生成定长强随机仓库密码（每类字符至少一个，再整体洗牌）。

    长度取 ``REPO_PASSWORD_LENGTH``（32），与前端 ``utils/password.ts`` 的
    ``generatePassword()`` 同一口径。历史上前后端位数曾不一致（后端
    ``token_urlsafe(32)``≈43 位 vs 前端 20 位），2026-10-27 起全站统一 32 位。

    自 ``routers/repos.py`` 搬来（候选 1 批次 ③）：编排器在 services 层需要它，
    而 services 不能反向 import routers。
    """
    rng = secrets.SystemRandom()
    chars = [rng.choice(cls) for cls in _PASSWORD_CLASSES]
    all_chars = "".join(_PASSWORD_CLASSES)
    while len(chars) < max(length, len(_PASSWORD_CLASSES)):
        chars.append(rng.choice(all_chars))
    rng.shuffle(chars)
    return "".join(chars)


def write_passfile(path: Path, secret: str) -> None:
    """原子写密码文件（同目录临时文件 + rename；权限 0600）。

    不追加尾随换行：restic 读取时会 TrimSpace，原样落盘可保证「写入即所用」。

    自 ``routers/repos.py`` 搬来（候选 1 批次 ③），理由同上。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(secret, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def remove_passfile(settings: Settings, repo_id: str) -> bool:
    """删除某仓库的密码文件（+ 空目录回收），返回是否真的删掉了。

    ``RESTIC_PASSWORD_FILE`` 全局覆盖时**拒绝执行**并返回 False：此刻删文件会
    毁掉所有仓库共用的钥匙，只能整体跳过（记 warning 便于运维察觉）。

    Args:
        settings: 运行期配置（路径派生来源）。
        repo_id: 仓库标识。

    Returns:
        是否实际删除了文件（文件本就不存在 → False，且不算错误）。
    """
    if settings.passfile_override is not None:
        # 日志只说「全局共用」（不落那个 env 变量名与任何值）：此刻删文件等于毁掉全部仓库
        logger.warning("密码文件为全局共用模式，跳过仓库 %s 的密码文件删除", repo_id)
        return False
    try:
        target = settings.passfile_path(repo_id)
    except Exception:  # noqa: BLE001 - 路径派生失败不该拖垮调用方（删除/每日维护）
        return False
    removed = False
    try:
        target.unlink()
        removed = True
    except FileNotFoundError:
        pass  # 本就未配置密码文件（空目录回收仍继续）
    except OSError:
        logger.warning("仓库 %s 密码文件删除失败（不影响本次删除本身）", repo_id)
    # 仅空目录 rmdir 成功；非空自然失败（目录里若有用户手放的文件，不属应用管控范围）
    with contextlib.suppress(OSError):
        target.parent.rmdir()
    return removed


def list_passfile_owners(root: str | Path) -> list[str]:
    """列出 ``<root>/<repo_id>/password`` 中确实存在密码文件的 repo_id（升序遍历）。

    只认一层子目录与固定文件名：其它形态（用户自建目录/别的文件）一律忽略。
    """
    base = Path(root)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    owners: list[str] = []
    for entry in entries:
        try:
            if entry.is_dir() and (entry / PASSFILE_NAME).is_file():
                owners.append(entry.name)
        except OSError:
            continue
    return owners


def reap_orphan_passfiles(settings: Settings, known_ids: set[str], *, cutoff_ts: float) -> list[str]:
    """回收「无主且超龄」的密码文件，返回被回收的 repo_id 列表（同步，调用方自行 to_thread）。

    判定：``<passfile_root>/<repo_id>/password`` 存在，且 repo_id **不在**
    ``known_ids``（在用仓库 + 归档仓库 + 墓碑），且文件 mtime **早于**
    ``cutoff_ts``（冷却期：避开「行已建、密码文件刚写」的中间态）。

    ``RESTIC_PASSWORD_FILE`` 全局覆盖时整段跳过（同 :func:`remove_passfile`）。
    """
    if settings.passfile_override is not None:
        logger.warning("密码文件为全局共用模式，跳过孤儿密码文件回收")
        return []
    root = Path(settings.passfile_root)
    reaped: list[str] = []
    for repo_id in list_passfile_owners(root):
        if repo_id in known_ids:
            continue
        try:
            mtime = (root / repo_id / PASSFILE_NAME).stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff_ts:
            # 冷却期内不碰：可能是刚创建、行还没落库的仓库
            continue
        if remove_passfile(settings, repo_id):
            reaped.append(repo_id)
    return reaped
