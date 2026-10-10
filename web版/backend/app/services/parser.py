"""OutputParser（§7.2，R-007）：restic ``--json`` 逐行输出 → 结构化事件。

设计约束（工程化约定 §8.1）：
- **零 IO、纯函数化**：不读文件、不连网络、不落库，可直接对 fixture 文本做回归测试；
- CPU 密集的解析由调用方放进线程池（``asyncio.to_thread``），不阻塞事件循环；
- :meth:`OutputParser.parse_line` 返回的 :class:`ProgressEvent` 其 ``event_id`` 由调用方
  （写入行号）回填，解析器本身不持有行号上下文。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Union

ParsedLine = Union["ProgressEvent", "SnapshotRecord", "FileRecord", "LogLine"]


@dataclass
class ProgressEvent:
    """restic ``status`` 消息（进度）与 ``error`` 消息（跳过计数）的统一载体。"""

    event_id: int = 0
    percent: float = 0.0
    bytes_done: int = 0
    total_bytes: int = 0
    files_done: int = 0
    total_files: int = 0
    skipped_delta: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotRecord:
    """``summary`` 消息或 ``snapshots --json`` 单条快照记录。"""

    snapshot_id: str | None = None
    time: str | None = None
    host: str | None = None
    tags: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    size: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileRecord:
    """``ls --json`` 单条文件记录，或 ``--verbose`` 下的 ``verbose_status`` 条目。"""

    path: str = ""
    name: str = ""
    type: str = "file"
    size: int | None = None
    mtime: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LogLine:
    """未能结构化（或非目标消息类型）的一行原始输出。"""

    text: str = ""
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class JobAggregate:
    """任务累计聚合（纯数据，由 :meth:`OutputParser.aggregate` 生成新实例）。"""

    bytes_done: int = 0
    total_bytes: int = 0
    files_done: int = 0
    total_files: int = 0
    skipped_count: int = 0
    percent: float = 0.0


class OutputParser:
    """restic ``--json`` 行解析器（零 IO）。"""

    def parse_line(self, raw: str) -> ParsedLine | None:
        """将一行输出解析为结构化对象。

        Args:
            raw: 单行文本（可含换行符，会被 strip）。

        Returns:
            :class:`ProgressEvent` / :class:`SnapshotRecord` / :class:`FileRecord` /
            :class:`LogLine` 之一；空行返回 ``None``。
        """
        text = raw.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return LogLine(text=text)
        if not isinstance(payload, dict):
            return LogLine(text=text)

        message_type = payload.get("message_type")
        if message_type == "status":
            return self._status_event(payload)
        if message_type == "error":
            return self._error_event(payload)
        if message_type == "summary":
            return self._summary_record(payload)
        if message_type == "verbose_status":
            return self._verbose_record(payload)
        if message_type == "exit_error":
            return LogLine(text=text, raw=payload)

        # 无 message_type：按形状识别 snapshots / ls 的结构化输出
        if "id" in payload and "paths" in payload:
            return self._snapshot_from_object(payload)
        if "path" in payload and "name" in payload:
            return self._file_from_object(payload)
        return LogLine(text=text, raw=payload)

    def parse_snapshot_list(self, text: str) -> list[SnapshotRecord]:
        """解析 ``restic snapshots --json`` 的整段输出。

        兼容两种输出形态：整段 pretty 打印的 JSON 数组（当前 restic 主流形态），
        以及逐行独立 JSON 对象（流式形态）。二者都不成立时返回空列表。

        Args:
            text: 子进程输出的整段文本（可含多行）。

        Returns:
            按输出顺序排列的快照记录列表。
        """
        payload = self.parse_json_blob(text)
        if isinstance(payload, list):
            return [self._snapshot_from_object(item) for item in payload if isinstance(item, dict)]
        records: list[SnapshotRecord] = []
        for line in text.splitlines():
            item = self.parse_json_blob(line)
            if isinstance(item, dict) and "id" in item:
                records.append(self._snapshot_from_object(item))
        return records

    def parse_file_list(self, text: str) -> list[FileRecord]:
        """解析 ``restic ls --json`` 的整段输出（形态兼容策略同 :meth:`parse_snapshot_list`）。

        Args:
            text: 子进程输出的整段文本（可含多行）。

        Returns:
            按输出顺序排列的文件条目列表。
        """
        payload = self.parse_json_blob(text)
        if isinstance(payload, list):
            return [self._file_from_object(item) for item in payload if isinstance(item, dict)]
        records: list[FileRecord] = []
        for line in text.splitlines():
            item = self.parse_json_blob(line)
            if isinstance(item, dict) and ("path" in item or "name" in item):
                records.append(self._file_from_object(item))
        return records

    @staticmethod
    def parse_json_blob(text: str) -> Any:
        """把一段（可能跨多行的 pretty 打印）JSON 文本解析为 Python 对象。

        Args:
            text: 待解析文本。

        Returns:
            解析结果（dict / list / 标量）；不可解析时返回 ``None``。
        """
        stripped = (text or "").strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None

    def aggregate(self, state: JobAggregate, ev: ProgressEvent) -> JobAggregate:
        """把进度事件累加进聚合状态（纯函数，返回新实例）。

        restic 的 status 消息是**快照式**而非增量式，故取 ``max`` 而非累加；
        只有 ``skipped_delta``（来自 error 消息）是增量。

        Args:
            state: 当前聚合状态。
            ev: 新解析出的进度事件。

        Returns:
            更新后的聚合状态。
        """
        if not isinstance(ev, ProgressEvent):
            return state
        return JobAggregate(
            bytes_done=max(state.bytes_done, ev.bytes_done),
            total_bytes=max(state.total_bytes, ev.total_bytes),
            files_done=max(state.files_done, ev.files_done),
            total_files=max(state.total_files, ev.total_files),
            skipped_count=state.skipped_count + max(0, ev.skipped_delta),
            percent=max(state.percent, ev.percent),
        )

    # ---------------------------------------------------------------- 内部实现

    @staticmethod
    def _status_event(payload: dict[str, Any]) -> ProgressEvent:
        """解析 ``message_type=status`` 进度消息。"""
        percent = payload.get("percent_done")
        return ProgressEvent(
            percent=float(percent) if isinstance(percent, int | float) else 0.0,
            bytes_done=_as_int(payload.get("bytes_done")),
            total_bytes=_as_int(payload.get("total_bytes")),
            files_done=_as_int(payload.get("files_done")),
            total_files=_as_int(payload.get("total_files")),
            raw=payload,
        )

    @staticmethod
    def _error_event(payload: dict[str, Any]) -> ProgressEvent:
        """解析 ``message_type=error``：归档期读失败即一次 skipped（R-012）。

        其余 error（如仓库级错误）不计入 skipped，仅以 raw 透传。
        """
        during = payload.get("during")
        return ProgressEvent(
            skipped_delta=1 if during == "archival" else 0,
            raw=payload,
        )

    @staticmethod
    def _summary_record(payload: dict[str, Any]) -> SnapshotRecord:
        """解析 ``message_type=summary``（backup 完成摘要）。"""
        return SnapshotRecord(
            snapshot_id=_as_str(payload.get("snapshot_id")),
            time=_as_str(payload.get("snapshot_time")),
            host=_as_str(payload.get("host")),
            tags=list(payload.get("tags") or []),
            paths=list(payload.get("paths") or []),
            size=_as_optional_int(payload.get("total_bytes_processed")),
            raw=payload,
        )

    @staticmethod
    def _verbose_record(payload: dict[str, Any]) -> FileRecord:
        """解析 ``message_type=verbose_status``（--verbose 下的逐文件条目）。"""
        item = _as_str(payload.get("item")) or ""
        return FileRecord(
            path=item,
            name=item.rsplit("/", 1)[-1] if item else "",
            type="file",
            size=_as_optional_int(payload.get("size")),
            mtime=_as_str(payload.get("mtime")),
            raw=payload,
        )

    @staticmethod
    def _snapshot_from_object(payload: dict[str, Any]) -> SnapshotRecord:
        """解析 ``restic snapshots --json`` 的单条对象。"""
        return SnapshotRecord(
            snapshot_id=_as_str(payload.get("id")),
            time=_as_str(payload.get("time")),
            host=_as_str(payload.get("hostname")),
            tags=list(payload.get("tags") or []),
            paths=list(payload.get("paths") or []),
            size=None,
            raw=payload,
        )

    @staticmethod
    def _file_from_object(payload: dict[str, Any]) -> FileRecord:
        """解析 ``restic ls --json`` 的单条对象。"""
        path = _as_str(payload.get("path")) or ""
        return FileRecord(
            path=path,
            name=_as_str(payload.get("name")) or (path.rsplit("/", 1)[-1] if path else ""),
            type=_as_str(payload.get("type")) or "file",
            size=_as_optional_int(payload.get("size")),
            mtime=_as_str(payload.get("mtime")),
            raw=payload,
        )


def _as_int(value: Any) -> int:
    """把可能为 None 的数值安全地转为 int。"""
    return int(value) if isinstance(value, int | float) else 0


def _as_optional_int(value: Any) -> int | None:
    """把可能为 None 的数值转为 int 或 None。"""
    return int(value) if isinstance(value, int | float) else None


def _as_str(value: Any) -> str | None:
    """把可能为 None 的值转为 str 或 None。"""
    return str(value) if value is not None else None
