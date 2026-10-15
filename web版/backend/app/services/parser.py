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

# check 流的行类型（R-017）：error 行与 summary 行，与 backup 语义分流（§7.1 V4）
ParsedLine = Union[
    "ProgressEvent", "SnapshotRecord", "FileRecord", "LogLine", "CheckErrorLine", "CheckSummary"
]


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
    # X12：restic **restore** 的 ``--json`` summary 用 ``files_skipped`` 表达因权限
    # 等原因未被恢复的文件数（``--overwrite=always`` 下仍写不进去即计入）。此前该
    # 字段被整体丢弃，导致 ``skipped_count`` 对恢复场景恒为 0、任务静默报 success
    # （V7/X12 静默部分失败）。此处提取出来，由 JobRunner 折入
    # ``JobAggregate.skipped_count``。backup / check 的 summary 无此字段 → ``None``。
    files_skipped: int | None = None
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


@dataclass
class CheckErrorLine:
    """restic check 的 ``error`` 行（R-017，restic 0.19.1 形态）。

    ``message`` **逐字保留**（含 Go 端格式化产生的换行与缩进）：tree 错误是
    「头行 + 缩进子行」两条独立 JSON 行，index 损坏提示块自带前后空行——解析层
    做二次排版加工会丢细节，check 结果视图（E22/T06）要靠原文呈现与折叠。
    """

    message: str = ""
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class CheckSummary:
    """restic check 的 ``summary`` 行（唯一、位于输出末尾；check 无任何进度消息）。

    ``broken_packs`` 健康仓库为 Go nil 序列化出的 ``null``（不是 ``[]``）——
    解析层统一归一为空列表，两种形态都容错（§7.1 V4 事实 2）。
    """

    num_errors: int = 0
    broken_packs: list[str] = field(default_factory=list)
    suggest_repair_index: bool = False
    suggest_prune: bool = False
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class CheckAggregate:
    """check 任务累计聚合（§7.1 V4 定稿：由 summary + error 行驱动，无进度集）。

    ``errors`` 为 error 行 ``message`` 的原文列表（逐字、保序）；summary 指标以
    末尾唯一 summary 行为准——输出被截断导致 summary 缺失时指标归零（错误行
    原文仍在，UI 以 ``errors`` 为准呈现）。
    """

    errors: list[str] = field(default_factory=list)
    num_errors: int = 0
    broken_packs: list[str] = field(default_factory=list)
    suggest_repair_index: bool = False
    suggest_prune: bool = False


@dataclass
class ForgetGroup:
    """``forget --json`` 单个分组（R-016 E21 预览解析产物）。

    restic forget 按 ``--group-by`` 维度分组评估保留策略，每组各带 keep / remove
    两个快照数组（元素为完整快照对象，形状同 ``snapshots --json``）。
    """

    keep: list[SnapshotRecord] = field(default_factory=list)
    remove: list[SnapshotRecord] = field(default_factory=list)


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
            # backup 的 error 行带 during/item 字段（R-012 skipped 口径）；check 的
            # error 行只有 message（restic 0.19.1 jsonErrorPrinter 形态）——按字段
            # 形状分流，避免 check 行被误入 backup 的 skipped 计数（现状缺陷）。
            if "message" in payload:
                return self._check_error_line(payload)
            return self._error_event(payload)
        if message_type == "summary":
            # backup summary 带 snapshot_id/total_bytes_processed；check summary 的
            # 特征字段是 num_errors——按字段形状分流（§7.1 V4）。
            if "num_errors" in payload:
                return self._check_summary_record(payload)
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

    def parse_forget_groups(self, text: str) -> list[ForgetGroup]:
        """解析 ``restic forget --json`` 的整段输出（R-016 E21 预览）。

        restic forget 的 JSON 输出为**逐组独立对象**（每组 ``{"keep": [...],
        "remove": [...], "reasons": [...]}``），形态兼容策略同 :meth:`parse_snapshot_list`：
        整段数组与逐行 NDJSON 都能解析；二者都不成立时返回空列表。

        Args:
            text: 子进程输出的整段文本（可含多行）。

        Returns:
            按输出顺序排列的分组列表（keep/remove 内为完整快照记录）。
        """
        groups: list[ForgetGroup] = []

        def _absorb(payload: Any) -> None:
            if isinstance(payload, dict) and ("keep" in payload or "remove" in payload):
                groups.append(
                    ForgetGroup(
                        keep=self._snapshots_from(payload.get("keep")),
                        remove=self._snapshots_from(payload.get("remove")),
                    )
                )

        blob = self.parse_json_blob(text)
        if isinstance(blob, list):
            for item in blob:
                _absorb(item)
            if groups:
                return groups
        for line in text.splitlines():
            _absorb(self.parse_json_blob(line))
        return groups

    def _snapshots_from(self, payload: Any) -> list[SnapshotRecord]:
        """把分组内 keep/remove 数组解析为快照记录（形状同 snapshots --json 单条）。"""
        if not isinstance(payload, list):
            return []
        return [self._snapshot_from_object(item) for item in payload if isinstance(item, dict)]

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

    def parse_check(self, text: str) -> CheckAggregate:
        """把 ``restic check --json`` 的整段输出解析为聚合结果（R-017，§7.1 V4）。

        check 输出仅两类消息（error 行逐条 + 末尾唯一 summary），**没有任何进度
        消息**——聚合由 summary 与 error 行驱动，不按进度事件建模。

        Args:
            text: 子进程输出的整段文本（可含多行；空文本返回零值聚合）。

        Returns:
            :class:`CheckAggregate`：errors 为 error 行 ``message`` 原文列表
            （逐字、保序）；summary 指标取末尾唯一 summary 行，缺失（截断）时归零；
            ``broken_packs`` 容错 ``null`` 与数组两形态，均归一为列表。
        """
        errors: list[str] = []
        num_errors = 0
        broken_packs: list[str] = []
        suggest_index = False
        suggest_prune = False
        for line in (text or "").splitlines():
            record = self.parse_line(line)
            if isinstance(record, CheckErrorLine):
                errors.append(record.message)
            elif isinstance(record, CheckSummary):
                num_errors = record.num_errors
                broken_packs = list(record.broken_packs)
                suggest_index = record.suggest_repair_index
                suggest_prune = record.suggest_prune
        return CheckAggregate(
            errors=errors,
            num_errors=num_errors,
            broken_packs=broken_packs,
            suggest_repair_index=suggest_index,
            suggest_prune=suggest_prune,
        )

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
    def _check_error_line(payload: dict[str, Any]) -> CheckErrorLine:
        """解析 check 的 ``message_type=error`` 行（message 逐字保留，不做 strip）。"""
        return CheckErrorLine(message=_as_str(payload.get("message")) or "", raw=payload)

    @staticmethod
    def _check_summary_record(payload: dict[str, Any]) -> CheckSummary:
        """解析 check 的 ``summary`` 行（broken_packs 容错 null 与 [] 两形态）。"""
        broken = payload.get("broken_packs")
        return CheckSummary(
            num_errors=_as_int(payload.get("num_errors")),
            broken_packs=[str(item) for item in broken] if isinstance(broken, list) else [],
            suggest_repair_index=bool(payload.get("suggest_repair_index")),
            suggest_prune=bool(payload.get("suggest_prune")),
            raw=payload,
        )

    @staticmethod
    def _summary_record(payload: dict[str, Any]) -> SnapshotRecord:
        """解析 ``message_type=summary``（backup / restore 完成摘要）。

        X12：restore 的 summary 用 ``files_skipped`` 记录因权限等原因未被恢复的
        文件数——此前被整体丢弃，导致 ``skipped_count`` 对恢复场景恒为 0（任务照报
        success，静默部分失败，见架构文档 §12 V7 / 缺陷 X12）。此处提取该字段，
        由 JobRunner 折入 ``JobAggregate.skipped_count``。backup summary 无
        ``files_skipped`` → ``None``（零副作用）。
        """
        return SnapshotRecord(
            snapshot_id=_as_str(payload.get("snapshot_id")),
            time=_as_str(payload.get("snapshot_time")),
            host=_as_str(payload.get("host")),
            tags=list(payload.get("tags") or []),
            paths=list(payload.get("paths") or []),
            size=_as_optional_int(payload.get("total_bytes_processed")),
            files_skipped=_as_optional_int(payload.get("files_skipped")),
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
