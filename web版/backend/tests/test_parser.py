"""OutputParser fixture 回归（§10.1 最高优先级层）：对固定 restic --json 输出做全量断言。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.parser import (
    FileRecord,
    JobAggregate,
    LogLine,
    OutputParser,
    ProgressEvent,
    SnapshotRecord,
)

FIXTURES = Path(__file__).parent / "fixtures" / "restic-json"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- backup status / error / summary

def test_status_progress_lines():
    parser = OutputParser()
    lines = read_fixture("backup_status.ndjson").splitlines()

    ev0 = parser.parse_line(lines[0])
    assert isinstance(ev0, ProgressEvent)
    assert ev0.percent == 0.0
    assert ev0.bytes_done == 0
    assert ev0.total_bytes == 2048
    assert ev0.files_done == 0
    assert ev0.total_files == 12
    assert ev0.skipped_delta == 0

    ev1 = parser.parse_line(lines[1])
    assert isinstance(ev1, ProgressEvent)
    assert ev1.percent == 0.5
    assert ev1.bytes_done == 1024
    assert ev1.files_done == 6


def test_error_archival_counts_as_skipped():
    """error 消息 during=archival → 一次 skipped 增量（R-012 非 root 静默跳过）。"""
    parser = OutputParser()
    line = read_fixture("backup_status.ndjson").splitlines()[2]

    ev = parser.parse_line(line)
    assert isinstance(ev, ProgressEvent)
    assert ev.skipped_delta == 1
    assert ev.bytes_done == 0  # error 事件不携带进度


def test_error_non_archival_is_not_skipped():
    """非 archival 阶段的 error 不计入 skipped（仓库级错误走 raw 透传）。"""
    parser = OutputParser()
    ev = parser.parse_line('{"message_type":"error","during":"scan","item":"/x","error":"boom"}')
    assert isinstance(ev, ProgressEvent)
    assert ev.skipped_delta == 0
    assert ev.raw["error"] == "boom"


def test_summary_yields_snapshot_record():
    parser = OutputParser()
    line = read_fixture("backup_status.ndjson").splitlines()[3]

    record = parser.parse_line(line)
    assert isinstance(record, SnapshotRecord)
    assert record.snapshot_id == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
    assert record.time == "2026-10-10T01:38:00.123456+08:00"
    assert record.size == 2048  # total_bytes_processed


def test_restore_summary_carries_files_skipped():
    """restore 的 --json summary 提取 files_skipped（X12：此前被整体丢弃）。

    真机取证形态（架构文档 §12 V7）：``--overwrite=always`` 下仍写不进去的文件
    计入 ``files_skipped``——曾因被丢弃导致 ``skipped_count`` 恒 0、任务静默报
    success。此处断言解析层把该字段提取到 ``SnapshotRecord.files_skipped``。
    """
    parser = OutputParser()
    line = '{"message_type":"summary","total_files":2,"files_restored":3,"files_skipped":3,"bytes_skipped":20}'

    record = parser.parse_line(line)
    assert isinstance(record, SnapshotRecord)
    assert record.files_skipped == 3
    assert record.snapshot_id is None  # restore summary 无快照 id


def test_backup_summary_has_no_files_skipped():
    """backup summary 无 files_skipped 字段 → None（X12 零副作用回归）。

    backup 的 summary 不带 files_skipped；fold 分支只在非 None 时触发，
    故 backup 行为逐字节不变（既不入聚合、也不影响 snapshot_id 捕获）。
    """
    parser = OutputParser()
    line = read_fixture("backup_status.ndjson").splitlines()[3]

    record = parser.parse_line(line)
    assert isinstance(record, SnapshotRecord)
    assert record.snapshot_id  # backup 仍照旧捕获快照 id
    assert record.files_skipped is None


def test_restore_summary_skipped_folds_into_aggregate():
    """restore summary 的 files_skipped 折入 skipped_count（X12：复刻 JobRunner 路径）。

    JobRunner 对 restore summary 行以 ``ProgressEvent(skipped_delta=files_skipped)``
    走 ``aggregate``——此处断言该折入真实生效（此前零覆盖：字段被丢弃）。
    """
    parser = OutputParser()
    line = '{"message_type":"summary","total_files":2,"files_restored":3,"files_skipped":3,"bytes_skipped":20}'

    state = JobAggregate()
    record = parser.parse_line(line)
    assert isinstance(record, SnapshotRecord)
    if record.files_skipped is not None:
        state = parser.aggregate(state, ProgressEvent(skipped_delta=record.files_skipped))
    assert state.skipped_count == 3


def test_verbose_status_yields_file_record():
    parser = OutputParser()
    lines = read_fixture("backup_verbose.ndjson").splitlines()

    rec0 = parser.parse_line(lines[0])
    assert isinstance(rec0, FileRecord)
    assert rec0.path == "/sources/etc/nginx/nginx.conf"
    assert rec0.name == "nginx.conf"
    assert rec0.size == 512
    assert rec0.type == "file"

    rec1 = parser.parse_line(lines[1])
    assert isinstance(rec1, FileRecord)
    assert rec1.name == "hosts"


def test_verbose_status_marks_directories_by_trailing_slash():
    """restic 用尾斜杠标记目录条目——这是 verbose_status 里唯一的目录信号。

    真机实测（/home/123/123，一份 restic 仓库）：277 个条目里 264 个带尾斜杠，
    其中 253 个是**空目录**。若一律当文件，空目录没有子路径可反推层级，
    UI 只能把它们显示成文件，13 个文件会显示成 266 个。
    """
    parser = OutputParser()
    lines = read_fixture("backup_verbose_dirs.ndjson").splitlines()

    dir_rec = parser.parse_line(lines[0])
    assert isinstance(dir_rec, FileRecord)
    assert dir_rec.type == "dir"
    assert dir_rec.path == "/sources/etc/nginx"  # 尾斜杠已剥掉，与 file 记录同形
    assert dir_rec.name == "nginx"

    file_rec = parser.parse_line(lines[1])
    assert isinstance(file_rec, FileRecord)
    assert file_rec.type == "file"
    assert file_rec.path == "/sources/etc/nginx/nginx.conf"

    empty_dir = parser.parse_line(lines[2])
    assert isinstance(empty_dir, FileRecord)
    assert empty_dir.type == "dir"
    assert empty_dir.name == "empty-dir"


# ---------------------------------------------------------------- 容错：非 JSON / 空行 / 垃圾输入

def test_non_json_line_becomes_log_line():
    parser = OutputParser()
    line = read_fixture("junk_mixed.txt").splitlines()[0]
    ev = parser.parse_line(line)
    assert isinstance(ev, LogLine)
    assert ev.text == "vault is already unlocked"


def test_empty_line_returns_none():
    parser = OutputParser()
    assert parser.parse_line("") is None
    assert parser.parse_line("   \n") is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"message_type":"status"',  # 被截断的 JSON
        "[]",  # 合法 JSON 但非 dict
        "42",
        '"just a string"',
        "null",
        "{}",  # dict 但无 message_type 也无形状特征
    ],
)
def test_garbage_inputs_do_not_crash(raw):
    parser = OutputParser()
    parsed = parser.parse_line(raw)
    assert isinstance(parsed, LogLine)


def test_unknown_message_type_passthrough():
    parser = OutputParser()
    parsed = parser.parse_line('{"message_type":"unknown_kind","x":1}')
    assert isinstance(parsed, LogLine)
    assert parsed.raw == {"message_type": "unknown_kind", "x": 1}


# ---------------------------------------------------------------- snapshots 两种输出形态

def test_parse_snapshot_list_pretty_array():
    parser = OutputParser()
    records = parser.parse_snapshot_list(read_fixture("snapshots_pretty.json"))

    assert len(records) == 2
    assert records[0].snapshot_id == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
    assert records[0].host == "nas"
    assert records[0].tags == ["auto"]
    assert records[0].paths == ["/sources/etc/nginx"]
    assert records[1].snapshot_id == "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    assert records[1].paths == ["/sources/var/lib", "/sources/db"]


def test_parse_snapshot_list_line_stream_matches_pretty():
    """逐行对象流与 pretty 数组两种形态解析结果一致。"""
    parser = OutputParser()
    from_lines = parser.parse_snapshot_list(read_fixture("snapshots_lines.ndjson"))
    from_pretty = parser.parse_snapshot_list(read_fixture("snapshots_pretty.json"))

    assert [(r.snapshot_id, r.time, r.host, r.tags, r.paths) for r in from_lines] == [
        (r.snapshot_id, r.time, r.host, r.tags, r.paths) for r in from_pretty
    ]


def test_parse_snapshot_list_garbage_returns_empty():
    parser = OutputParser()
    assert parser.parse_snapshot_list("") == []
    assert parser.parse_snapshot_list("not json at all") == []


# ---------------------------------------------------------------- ls --json

def test_parse_file_list_line_stream():
    parser = OutputParser()
    records = parser.parse_file_list(read_fixture("ls_files.ndjson"))

    assert len(records) == 4
    assert records[0].path == "/sources/etc"
    assert records[0].type == "dir"
    assert records[2].name == "nginx.conf"
    assert records[2].size == 512
    assert records[2].mtime == "2026-01-03T12:00:00+08:00"


def test_parse_file_list_pretty_array():
    parser = OutputParser()
    records = parser.parse_file_list(read_fixture("ls_files_array.json"))

    assert [r.path for r in records] == ["/sources/etc/nginx", "/sources/etc/nginx/nginx.conf"]


def test_parse_file_list_garbage_returns_empty():
    parser = OutputParser()
    assert parser.parse_file_list("") == []
    assert parser.parse_file_list("boom") == []


# ---------------------------------------------------------------- aggregate（status 快照式取 max，skipped 增量累加）

def test_aggregate_max_and_skipped_accumulation():
    parser = OutputParser()
    lines = read_fixture("backup_status.ndjson").splitlines()

    state = JobAggregate()
    for raw in lines:
        ev = parser.parse_line(raw)
        if isinstance(ev, ProgressEvent):
            state = parser.aggregate(state, ev)

    assert state.bytes_done == 1024  # status 快照式 → max 而非累加
    assert state.total_bytes == 2048
    assert state.files_done == 6
    assert state.total_files == 12
    assert state.percent == 0.5
    assert state.skipped_count == 1  # error(archival) 增量累加


def test_aggregate_ignores_non_progress_events():
    parser = OutputParser()
    state = JobAggregate(bytes_done=100)
    record = parser.parse_line(read_fixture("snapshots_lines.ndjson").splitlines()[0])
    assert parser.aggregate(state, record) == state  # SnapshotRecord 不参与聚合


def test_parse_line_with_event_id_note():
    """解析器本身不持有行号：ProgressEvent.event_id 由调用方回填。"""
    parser = OutputParser()
    ev = parser.parse_line(read_fixture("backup_status.ndjson").splitlines()[1])
    assert isinstance(ev, ProgressEvent)
    assert ev.event_id == 0
    ev.event_id = 2
    assert ev.event_id == 2


# ---------------------------------------------------------------- forget --json（R-016 E21）

def _forget_group(keep_id: str, remove_id: str) -> dict:
    def _snapshot(snapshot_id: str) -> dict:
        return {
            "id": snapshot_id,
            "time": "2026-10-01T03:00:00+08:00",
            "hostname": "nas",
            "tags": ["task-1"],
            "paths": ["/sources/etc"],
        }

    return {"keep": [_snapshot(keep_id)], "remove": [_snapshot(remove_id)], "reasons": []}


KEEP_ID = "a" * 64
REMOVE_ID = "b" * 64


def test_parse_forget_groups_ndjson_stream():
    """restic forget 的真实形态：逐组独立 JSON 对象（NDJSON）。"""
    import json

    parser = OutputParser()
    text = "\n".join(json.dumps(_forget_group(KEEP_ID, REMOVE_ID)) for _ in range(2))
    groups = parser.parse_forget_groups(text)

    assert len(groups) == 2
    assert groups[0].keep[0].snapshot_id == KEEP_ID
    assert groups[0].remove[0].snapshot_id == REMOVE_ID
    assert groups[0].keep[0].host == "nas"


def test_parse_forget_groups_pretty_array():
    """整段 JSON 数组形态（兼容策略同 parse_snapshot_list）解析结果一致。"""
    import json

    parser = OutputParser()
    text = json.dumps([_forget_group(KEEP_ID, REMOVE_ID)])
    groups = parser.parse_forget_groups(text)

    assert len(groups) == 1
    assert groups[0].keep[0].snapshot_id == KEEP_ID
    assert groups[0].remove[0].snapshot_id == REMOVE_ID


def test_parse_forget_groups_garbage_returns_empty():
    parser = OutputParser()
    assert parser.parse_forget_groups("") == []
    assert parser.parse_forget_groups("not json at all") == []
    # 快照对象（无 keep/remove 键）不构成分组，不误收
    assert parser.parse_forget_groups('{"id": "a1b2", "paths": ["/sources/etc"]}') == []
