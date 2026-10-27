"""rclone 凭据形态记录**存储抽象**（裁决 15 方案 A / 裁决 16 迁库）。

形态记录 = 「某个 conf 段的某个凭据，落盘形态是不是混淆产物」+「conf 现值哈希」。
**不含凭据明文**（不破 S3 零回显）。

为什么必须**记录**而不是事后判定：rclone 的 ``reveal`` 无认证校验（v1.60.1 真机实证：
任意足够长的 base64 可解码串都能「解出」一串垃圾且 exit 0），事后反推不可靠——形态必须在
**写入时**记下（那次我们显式传了 ``--obscure``/``--no-obscure``，真值即在）。

两种实现：

- :class:`RcloneFormRepo`（生产，DB 表 ``rclone_credential_forms``）——随 DB 备份/迁移走；
- :class:`LegacySidecarStore`（旧 sidecar 文件 ``<data_dir>/rclone_meta.json``）——仅用于
  **一次性导入源**与单元测试；升级后由 :func:`import_legacy_sidecar` 导入 DB 并删除文件。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

# 形态记录条目：字段 → (是否混淆形态, conf 现值 sha256)
FormEntry = tuple[bool, str]
# 全量形态快照：remote → field → 条目
FormSnapshot = dict[str, dict[str, FormEntry]]

# 旧 sidecar 文件名（迁移源）
LEGACY_META_FILENAME = "rclone_meta.json"


class FormStore(Protocol):
    """形态记录存储协议（DB 仓储与旧文件实现同形）。"""

    async def load(self) -> FormSnapshot:
        """读全量形态快照。"""
        ...

    async def replace_remote(self, remote: str, entries: Mapping[str, FormEntry]) -> None:
        """整段替换某 remote 的形态记录（空 = 清空该段）。"""
        ...

    async def delete_remote(self, remote: str) -> None:
        """删除某 remote 的全部记录（段被删除时清账）。"""
        ...

    async def delete_orphans(self, existing_remotes: Iterable[str]) -> int:
        """清理「conf 中已不存在」的孤儿记录，返回删除行数。"""
        ...


class LegacySidecarStore:
    """旧 sidecar 文件实现（JSON 文件 + 原子替换）。

    ⚠️ 仅用于**一次性导入源**与单元测试；生产走 :class:`RcloneFormRepo`（DB）。

    文件内格式（可读）：``{"remotes": {"<段名>": {"<字段>": {"obscured": true, "sha256": "…"}}}}``；
    对外接口用**快照**格式（tuple），两者严格分离——本实现自己写自己读必须自洽。
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _read_raw(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        remotes = data.get("remotes")
        return remotes if isinstance(remotes, dict) else {}

    async def load(self) -> FormSnapshot:
        out: FormSnapshot = {}
        for remote, fields in self._read_raw().items():
            if not isinstance(fields, dict):
                continue
            entries: dict[str, FormEntry] = {}
            for field, entry in fields.items():
                if not isinstance(entry, dict):
                    continue
                digest = entry.get("sha256")
                if not isinstance(digest, str):
                    continue
                obscured = entry.get("obscured")
                entries[str(field)] = (bool(obscured) if isinstance(obscured, bool) else False, digest)
            out[str(remote)] = entries
        return out

    async def replace_remote(self, remote: str, entries: Mapping[str, FormEntry]) -> None:
        remotes = self._read_raw()
        fields = remotes.get(remote) or {}
        for field, (obscured, digest) in entries.items():
            fields[field] = {"obscured": bool(obscured), "sha256": digest}
        remotes[remote] = fields
        self._write(remotes)

    async def delete_remote(self, remote: str) -> None:
        remotes = self._read_raw()
        if remotes.pop(remote, None) is None:
            return
        self._write(remotes)

    async def delete_orphans(self, existing_remotes: Iterable[str]) -> int:
        remotes = self._read_raw()
        keep = set(existing_remotes)
        orphans = [remote for remote in remotes if remote not in keep]
        for remote in orphans:
            remotes.pop(remote, None)
        if orphans:
            self._write(remotes)
        return len(orphans)

    def _write(self, remotes: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "remotes": remotes}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)


async def import_legacy_sidecar(store: FormStore, data_dir: Path | str) -> int:
    """把旧 sidecar 文件一次性导入目标存储并删除文件（升级迁移）。

    Args:
        store: 目标存储（生产 = DB 仓储）。
        data_dir: 应用数据目录（旧文件固定在 ``<data_dir>/rclone_meta.json``）。

    Returns:
        导入的段数（无旧文件 → 0）。
    """
    legacy = LegacySidecarStore(Path(data_dir) / LEGACY_META_FILENAME)
    if not legacy.path.is_file():
        return 0
    snapshot = await legacy.load()
    for remote, entries in snapshot.items():
        await store.replace_remote(remote, entries)
    legacy.path.unlink(missing_ok=True)
    return len(snapshot)
