"""restic check ``--json`` 解析契约测试骨架（T04 fixture 先行，R-017）。

红线约定「改 parser 前必须先补真实形态 fixture」：本文件是 T04 主任务
（check 解析 + ``CheckAggregate``）的**预期断言落点**。check 事件解析尚未实现
（``OutputParser.parse_line`` 目前仅有 backup 语义分支，check 行会被误入 backup
的 error/summary 分支），故文件分两组：

- **契约组**：``pytest.mark.xfail(strict=False, reason="check 解析未实现...")``
  标注——T04 主任务实现后应转绿，届时并入/对齐 ``test_parser.py`` 正式回归并
  移除标注；若 T04 采用不同 API 形态（如统一经 ``CheckAggregate`` 暴露字段），
  同步调整本组断言即可（本骨架的职责是把 fixture 输入与预期语义钉住）；
- **容错护栏组**：不断言具体类型，只断言逐行可解析、不抛异常——当前即应通过，
  T04 主任务**不得破坏**本组（设计 §7 畸形行容错维度；A6：解析层纯函数零 IO）。

fixture 依据：restic 0.19.1 ``cmd/restic/cmd_check.go``——check --json 仅输出
``error`` 行与末尾一条 ``summary``，**无任何进度消息**；健康仓库 ``broken_packs``
为 Go nil 序列化出的 ``null``。细节见 ``tests/fixtures/restic-json/README-check.md``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.parser import OutputParser

FIXTURES = Path(__file__).parent / "fixtures" / "restic-json"

CHECK_FIXTURES = ("check-clean.ndjson", "check-errors-index.ndjson", "check-read-data-damaged.ndjson")

PACK_ID_A = "8c0a3b6f1e2d4c5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a"
PACK_ID_B = "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 契约组（T04 主任务实现后转绿）


@pytest.mark.xfail(reason="check 解析未实现（T04 主任务 R-017）——fixture 已就绪", strict=False)
def test_check_clean_summary_contract():
    """健康 check（纯元数据、无 --read-data）：唯一 summary 行 → check 专用摘要记录。

    预期契约：num_errors==0、broken_packs 为空列表（fixture 中是 Go nil 的 null，
    解析层必须容错）、两个 suggest hint 均 False；且**不是** backup 的
    SnapshotRecord（否则 check 结果会被当成 backup 快照消费——这正是现状缺陷）。
    """
    parser = OutputParser()
    lines = read_fixture("check-clean.ndjson").splitlines()

    records = [parser.parse_line(line) for line in lines]
    assert len(records) == 1  # 无进度、无 error：只有 summary 一行（restic 0.19.1 真实形态）

    record = records[0]
    assert record.num_errors == 0
    assert record.broken_packs == []  # null 必须归一为空列表
    assert record.suggest_repair_index is False
    assert record.suggest_prune is False


@pytest.mark.xfail(reason="check 解析未实现（T04 主任务 R-017）——fixture 已就绪", strict=False)
def test_check_error_lines_preserve_message_verbatim():
    """error 行 → check 专用错误记录，message 文本**逐字保留**（含尾部/内嵌换行）。

    restic 把人读的细节全部放 message（tree 错误是头行+缩进子行两条独立 JSON 行、
    index 损坏提示块带前后空行换行）——解析层做二次排版加工会丢细节，UI（T06
    check 视图）要靠原文呈现与折叠。
    """
    parser = OutputParser()
    lines = read_fixture("check-errors-index.ndjson").splitlines()

    records = [parser.parse_line(line) for line in lines]
    assert len(records) == 5  # 4 条 error + 1 条 summary（源码 E 调用点逐行对应）

    tree_header, tree_child, index_err, damaged_hint, summary = records
    assert tree_header.message == "error for tree 5a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2:\n"
    assert tree_child.message == "  loading blob from cache failed: file does not exist\n"
    assert index_err.message.endswith("header is invalid\n")
    assert damaged_hint.message.startswith("\nThe repository index is damaged")

    assert summary.num_errors == 2  # 1 条 treeErr + 1 条 index err（源码计数口径）
    assert summary.broken_packs == []
    assert summary.suggest_repair_index is True
    assert summary.suggest_prune is False


@pytest.mark.xfail(reason="check 解析未实现（T04 主任务 R-017）——fixture 已就绪", strict=False)
def test_check_read_data_damaged_contract():
    """--read-data 失败形态：pack 读错误逐行成记录；summary.broken_packs 填充 salvagePacks。

    ``broken_packs`` 是 E22/E13 check 视图「下一步动作」（repair packs <id>…）的
    数据源，必须保序且与 error 行中的 pack id 对应。
    """
    parser = OutputParser()
    lines = read_fixture("check-read-data-damaged.ndjson").splitlines()

    records = [parser.parse_line(line) for line in lines]
    assert len(records) == 3  # 2 条 pack 错误 + 1 条 summary

    summary = records[-1]
    assert summary.num_errors == 2
    assert summary.broken_packs == [PACK_ID_A, PACK_ID_B]  # 保序
    assert summary.suggest_repair_index is False
    assert summary.suggest_prune is False

    first_error = records[0]
    assert first_error.message.startswith(f"error for pack {PACK_ID_A}: ")
    assert "unexpected EOF" in first_error.message


# ---------------------------------------------------------------- 容错护栏组（当前即应通过；T04 不得破坏）


def test_check_fixture_lines_never_raise():
    """三个 check fixture 的每一行经 parse_line 均不抛异常（现状：误入 backup 分支）。

    这是 check 流接入 SSE/日志回放（A4 底座）的安全前提：解析层对**任何** restic
    输出行都不能抛异常，check 行在 T04 落地前会被误分类，但绝不许崩。
    """
    parser = OutputParser()
    for name in CHECK_FIXTURES:
        for line in read_fixture(name).splitlines():
            parser.parse_line(line)  # 不抛即通过；类型断言归契约组（xfail）


@pytest.mark.parametrize(
    "raw",
    [
        "vault is already unlocked",          # 纯文本行
        '{"message_type":"status"',           # 截断 JSON
        "[]",                                 # 非对象 JSON
        "42",
        '"just a string"',
        "{}",
        '{"message_type":"unknown_kind"}',
        "",                                   # 空行 → None
        "   \n",
    ],
)
def test_check_stream_malformed_lines_tolerated(raw):
    """畸形行容错（设计 §7 第四类）：check 流中混入垃圾行时逐行容错、不抛异常。

    restic 自身不输出畸形行（故不设 fixture），此组复用 junk_mixed.txt 同款形态
    内联构造；与 test_parser.py 的既有容错断言同口径。
    """
    parser = OutputParser()
    parsed = parser.parse_line(raw)
    if not raw.strip():
        assert parsed is None
    else:
        assert parsed is not None  # 非 JSON/未知形态 → LogLine 透传，不抛


def test_check_fixtures_are_valid_ndjson_with_known_message_types():
    """fixture 卫生护栏：每个 check fixture 均为合法 NDJSON，summary 恰一条且在末尾。

    本组锁的是**资产本身**（与 parser 实现解耦）：message_type 词表
    {error, summary}、字段形态（num_errors 整数、broken_packs 列表或 null、
    suggest_* 布尔）与 README-check.md 记载的 restic 0.19.1 形态一致。
    """
    for name in CHECK_FIXTURES:
        lines = read_fixture(name).splitlines()
        assert lines, f"{name} 不应为空"
        payloads = [json.loads(line) for line in lines]  # 任一行非法 JSON 即失败

        summaries = [p for p in payloads if p["message_type"] == "summary"]
        errors = [p for p in payloads if p["message_type"] == "error"]
        assert len(summaries) == 1, f"{name} 应恰有一条 summary（restic 0.19.1 形态）"
        assert payloads[-1] is summaries[0], f"{name} 的 summary 应在末尾"

        assert all(set(p) >= {"message_type"} for p in payloads)
        for e in errors:
            assert isinstance(e["message"], str) and e["message"], f"{name} 的 error 行应有非空 message"
        s = summaries[0]
        assert isinstance(s["num_errors"], int) and s["num_errors"] >= 0
        assert s["broken_packs"] is None or isinstance(s["broken_packs"], list)
        assert isinstance(s["suggest_repair_index"], bool)
        assert isinstance(s["suggest_prune"], bool)

    # 健康形态与损坏形态的关键差异必须钉住（供 T04 契约组与 T06 视图对照）
    clean = json.loads(read_fixture("check-clean.ndjson"))  # 单行文件
    assert clean["num_errors"] == 0 and clean["broken_packs"] is None
    damaged = json.loads(read_fixture("check-read-data-damaged.ndjson").splitlines()[-1])  # 末行 summary
    assert damaged["num_errors"] == 2 and damaged["broken_packs"] == [PACK_ID_A, PACK_ID_B]
